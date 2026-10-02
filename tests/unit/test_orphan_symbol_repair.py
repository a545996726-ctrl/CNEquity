from datetime import date, datetime, timezone

import polars as pl

from cnequity.config import Config
from cnequity.query import load
from cnequity.storage.orphan_symbol_repair import repair_orphan_symbols
from cnequity.storage.revisions import RevisionStore

FETCHED = datetime(2026, 9, 30, tzinfo=timezone.utc)
DAYS = [date(2024, 1, 2), date(2024, 1, 3)]


def _commit(cfg: Config, dataset: str, root) -> None:
    RevisionStore(cfg.meta_root, cfg.curated_root, cfg.derived_root).commit(
        dataset,
        run_id="seed",
        changed_files=sorted(root.rglob("*.parquet")),
        schema_version=1,
        contract_fingerprint="contract",
    )


def _lake(tmp_path) -> Config:
    cfg = Config(data_root=tmp_path)
    for day in DAYS:
        bars = cfg.curated_root / "daily_bars" / f"trade_date={day}"
        bars.mkdir(parents=True)
        pl.DataFrame(
            {
                "symbol": ["600519.SH"],
                "trade_date": [day],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "volume": [100.0],
                "amount": [1000.0],
            }
        ).write_parquet(bars / "part-0.parquet")
        factors = cfg.derived_root / "adj_factors" / f"trade_date={day}"
        factors.mkdir(parents=True)
        # 519019.SH has factors on the first day only; that partition keeps
        # 600519.SH. The second day's partition holds a priced symbol too.
        symbols = ["600519.SH", "519019.SH"] if day == DAYS[0] else ["600519.SH"]
        pl.DataFrame(
            {
                "symbol": symbols,
                "trade_date": [day] * len(symbols),
                "adjust_type": ["hfq"] * len(symbols),
                "factor": [1.0] * len(symbols),
                "source": ["sina"] * len(symbols),
                "data_version": ["v1"] * len(symbols),
                "fetched_at": [FETCHED] * len(symbols),
            }
        ).write_parquet(factors / "part-0.parquet")
    for year, symbol in ((2023, "519019.SH"), (2024, "600519.SH")):
        part = cfg.curated_root / "corporate_actions" / f"ex_date={year}"
        part.mkdir(parents=True)
        pl.DataFrame(
            {
                "symbol": [symbol],
                "ex_date": [date(year, 6, 1)],
                "action_type": ["cash_dividend"],
                "cash_dividend": [0.1],
                "bonus_ratio": [0.0],
                "transfer_ratio": [0.0],
                "allotment_ratio": [None],
                "allotment_price": [None],
                "source": ["tdx_protocol"],
                "data_version": ["v1"],
                "fetched_at": [FETCHED],
            },
            schema_overrides={"allotment_ratio": pl.Float64, "allotment_price": pl.Float64},
        ).write_parquet(part / "part-0.parquet")
    _commit(cfg, "daily_bars", cfg.curated_root / "daily_bars")
    _commit(cfg, "adj_factors", cfg.derived_root / "adj_factors")
    _commit(cfg, "corporate_actions", cfg.curated_root / "corporate_actions")
    return cfg


def test_plan_counts_rows_without_touching_the_lake(tmp_path):
    cfg = _lake(tmp_path)
    plan = repair_orphan_symbols(cfg)
    assert plan["applied"] is False
    assert plan["datasets"]["adj_factors"]["rows"] == 1
    assert plan["datasets"]["corporate_actions"]["rows"] == 1
    assert "519019.SH" in load("adj_factors", config=cfg).get_column("symbol").to_list()


def test_apply_removes_only_securities_without_bars(tmp_path):
    cfg = _lake(tmp_path)
    report = repair_orphan_symbols(cfg, apply=True)
    assert report["datasets"]["adj_factors"]["partitions_rewritten"] == 1
    # The 2023 action partition held only the orphan and is gone.
    assert report["datasets"]["corporate_actions"]["partitions_emptied"] == 1
    assert set(load("adj_factors", config=cfg).get_column("symbol")) == {"600519.SH"}
    assert load("adj_factors", config=cfg).height == 2
    assert set(load("corporate_actions", config=cfg).get_column("symbol")) == {"600519.SH"}
    # The previous generation is still readable by revision.
    old = load("adj_factors", config=cfg, revision=1)
    assert "519019.SH" in old.get_column("symbol").to_list()
    assert repair_orphan_symbols(cfg)["datasets"]["adj_factors"]["orphan_symbols"] == 0
