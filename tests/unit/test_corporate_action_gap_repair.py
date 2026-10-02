import json
from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.query import load
from cnequity.storage.corporate_action_gap_repair import repair_corporate_action_gaps
from cnequity.storage.revisions import RevisionStore

FETCHED = datetime(2026, 9, 29, tzinfo=timezone.utc)
SESSIONS = [
    date(2024, 1, 2) + timedelta(days=i)
    for i in range(30)
    if (date(2024, 1, 2) + timedelta(days=i)).weekday() < 5
]
CLOSE = 10.0
# symbol -> (true ex-date, factor step it causes)
STEPS = {
    "600001.SH": (date(2024, 1, 15), CLOSE / (CLOSE - 0.1) - 1),  # recorded on Sat 01-13
    "600002.SH": (date(2024, 1, 17), CLOSE / (CLOSE - 0.2) - 1),  # not recorded at all
    "600003.SH": (date(2024, 1, 19), 0.1),  # nearby row whose terms do not fit
}


def _action(symbol, ex_date, action_type, *, cash=0.0, bonus=0.0, source="tdx_protocol"):
    return {
        "symbol": symbol,
        "ex_date": ex_date,
        "action_type": action_type,
        "cash_dividend": cash,
        "bonus_ratio": bonus,
        "transfer_ratio": 0.0,
        "allotment_ratio": None,
        "allotment_price": None,
        "source": source,
        "data_version": "v1",
        "fetched_at": FETCHED,
        "payment_date": None,
        "payment_source": None,
        "split_factor": 1.0,
    }


_SCHEMA = {
    "allotment_ratio": pl.Float64,
    "allotment_price": pl.Float64,
    "payment_date": pl.Date,
    "payment_source": pl.Utf8,
}


@pytest.fixture
def lake(tmp_path) -> Config:
    cfg = Config(data_root=tmp_path)
    for day in SESSIONS:
        bars = cfg.curated_root / "daily_bars" / f"trade_date={day}"
        bars.mkdir(parents=True)
        factors = cfg.derived_root / "adj_factors" / f"trade_date={day}"
        factors.mkdir(parents=True)
        pl.DataFrame(
            [
                {
                    "symbol": s,
                    "trade_date": day,
                    "open": CLOSE,
                    "high": CLOSE,
                    "low": CLOSE,
                    "close": CLOSE,
                    "volume": 100.0,
                    "amount": CLOSE * 100,
                }
                for s in STEPS
            ]
        ).write_parquet(bars / "part-0.parquet")
        pl.DataFrame(
            [
                {
                    "symbol": s,
                    "trade_date": day,
                    "adjust_type": "hfq",
                    "factor": 1.0 + (step if day >= ex else 0.0),
                    "source": "sina",
                    "data_version": "v1",
                    "fetched_at": FETCHED,
                }
                for s, (ex, step) in STEPS.items()
            ]
        ).write_parquet(factors / "part-0.parquet")
    part = cfg.curated_root / "corporate_actions" / "ex_date=2024"
    part.mkdir(parents=True)
    pl.DataFrame(
        [
            _action("600001.SH", date(2024, 1, 13), "cash_dividend", cash=0.1),
            _action("600003.SH", date(2024, 1, 18), "bonus", bonus=0.5),
            _action("600003.SH", date(2024, 6, 3), "cash_dividend", cash=0.3),
        ],
        schema_overrides=_SCHEMA,
    ).write_parquet(part / "part-merged.parquet")
    RevisionStore(cfg.meta_root, cfg.curated_root).commit(
        "corporate_actions",
        run_id="seed",
        changed_files=sorted((cfg.curated_root / "corporate_actions").rglob("*.parquet")),
        schema_version=1,
        contract_fingerprint="contract",
    )
    evidence = cfg.meta_root / "quality" / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "adj_factor_source_arbitration-20260929T000000Z.json").write_text(
        json.dumps(
            {
                "verdict_rows": [
                    {
                        "symbol": s,
                        "ex_date": ex.isoformat(),
                        "kind": "step_without_action",
                        "verdict": "action_missing",
                        "sina_step": step,
                        "bao_step": step,
                    }
                    for s, (ex, step) in STEPS.items()
                ]
            }
        )
    )
    return cfg


def _baostock(asked):
    def fetch(symbols, start, end):
        asked.append((tuple(symbols), start.year))
        rows = [
            _action("600002.SH", date(2024, 1, 17), "cash_dividend", cash=0.2),
            # Another event that year is not a gap and is not added.
            _action("600002.SH", date(2024, 7, 1), "cash_dividend", cash=0.4),
        ]
        frame = pl.DataFrame(rows, schema_overrides=_SCHEMA).drop(
            "source", "data_version", "fetched_at"
        )
        return frame.filter(pl.col("symbol").is_in(symbols)), []

    return fetch


def test_plan_is_offline_and_counts_both_paths(lake):
    asked: list = []
    plan = repair_corporate_action_gaps(lake, fetch=_baostock(asked))
    assert (plan["missing"], plan["moves"], plan["unpaired"]) == (3, 1, 2)
    assert plan["applied"] is False and asked == []


def test_apply_moves_misdated_rows_and_adds_only_fitting_gaps(lake):
    asked: list = []
    report = repair_corporate_action_gaps(lake, apply=True, fetch=_baostock(asked))
    assert report["applied"] is True
    assert report["added_rows"] == 1
    # One request per symbol-year, only for gaps nothing nearby explains.
    assert asked == [(("600002.SH", "600003.SH"), 2024)]

    rows = load("corporate_actions", config=lake).sort("symbol", "ex_date")
    keys = rows.select("symbol", "ex_date", "action_type", "source").rows()
    assert keys == [
        ("600001.SH", date(2024, 1, 15), "cash_dividend", "tdx_protocol"),
        ("600002.SH", date(2024, 1, 17), "cash_dividend", "baostock"),
        # A share-count row that cannot explain a 10% step stays where it is.
        ("600003.SH", date(2024, 1, 18), "bonus", "tdx_protocol"),
        ("600003.SH", date(2024, 6, 3), "cash_dividend", "tdx_protocol"),
    ]
    # The previous generation stays readable by revision.
    old = load("corporate_actions", config=lake, revision=1)
    assert date(2024, 1, 13) in old.get_column("ex_date").to_list()
