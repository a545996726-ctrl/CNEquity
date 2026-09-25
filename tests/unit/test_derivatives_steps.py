"""Futures/option steps: opt-in, partial exchanges, resumable backfill, contracts."""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from cnequity.adapters.futures_exchange.cffex import parse_daily_csv
from cnequity.adapters.futures_exchange.common import ExchangeDay, FuturesDayUnavailable
from cnequity.config import Config
from cnequity.derive.derivative_contracts import build_futures_contracts, build_option_contracts
from cnequity.domain.datasets import is_dataset_enabled
from cnequity.steps import derivatives
from cnequity.storage.parquet import compact_dataset

FIXTURE = Path(__file__).parents[1] / "fixtures" / "futures" / "cffex_20260924.csv"


def _day(d: date) -> ExchangeDay:
    """The fixture file, re-dated: every session lists the same contracts."""
    parsed = parse_daily_csv(FIXTURE.read_bytes(), date(2026, 9, 24))
    redate = pl.lit(d).alias("trade_date")
    return ExchangeDay(
        "CFE", d, parsed.futures.with_columns(redate), parsed.options.with_columns(redate)
    )


@pytest.fixture
def cfg(tmp_path) -> Config:
    config = Config(data_root=tmp_path / "data")
    config.futures_enabled = True
    config.futures_exchanges = ["CFE"]
    return config


@pytest.fixture
def reader(monkeypatch):
    calls: list[date] = []
    missing: set[date] = set()

    def fetch_day(d, *, config=None):
        calls.append(d)
        if d in missing:
            raise FuturesDayUnavailable("not published")
        return _day(d)

    patched = dataclasses.replace(
        derivatives.READERS["CFE"], fetch_day=fetch_day, fetch_reference=None
    )
    monkeypatch.setitem(derivatives.READERS, "CFE", patched)
    return calls, missing


def _staged(cfg: Config, dataset: str) -> pl.DataFrame:
    files = sorted((cfg.staging_root / dataset).rglob("*.parquet"))
    return pl.concat([pl.read_parquet(p) for p in files], how="diagonal_relaxed")


def test_the_family_is_off_until_configured(tmp_path):
    config = Config(data_root=tmp_path / "data")
    assert not is_dataset_enabled("futures_bars", config)
    result = derivatives.step_futures_bars(config, date(2026, 9, 24), "run", {})
    assert result["rows_written"] == 0
    assert "disabled" in result["note"]
    config.futures_enabled = True
    config.futures_options = False
    assert is_dataset_enabled("futures_bars", config)
    assert not is_dataset_enabled("option_bars", config)


def test_daily_run_writes_the_reconciliation_tail(cfg, reader):
    calls, _missing = reader
    result = derivatives.step_futures_bars(cfg, date(2026, 9, 24), "run", {})
    staged = _staged(cfg, "futures_bars")
    # Three sessions: the lookback includes the run day.
    assert sorted(set(calls)) == [date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
    assert result["rows_written"] == staged.height == 7 * 3
    assert set(staged["source"]) == {"futures_exchange"}


def test_a_session_no_exchange_published_is_a_failed_day_not_a_crash(cfg, reader):
    _calls, missing = reader
    missing.add(date(2026, 9, 24))
    result = derivatives.step_futures_bars(cfg, date(2026, 9, 24), "run", {})
    staged = _staged(cfg, "futures_bars")
    assert date(2026, 9, 24) not in set(staged["trade_date"])
    checks = {f["check"] for f in result["context_updates"]["audit_findings"]}
    assert "futures_exchange_missing" in checks


def test_one_exchange_missing_leaves_the_others_written(cfg, monkeypatch, reader):
    """A second exchange that did not publish must not hold the first one back."""

    def never(d, *, config=None):
        raise FuturesDayUnavailable("not yet")

    monkeypatch.setitem(
        derivatives.READERS,
        "XXX",
        dataclasses.replace(derivatives.READERS["CFE"], exchange="XXX", fetch_day=never),
    )
    monkeypatch.setattr(derivatives, "SUPPORTED_EXCHANGES", ("CFE", "XXX"))
    cfg.futures_exchanges = []
    findings: list[dict] = []
    frame = derivatives.fetch_session(cfg, date(2026, 9, 24), "futures", findings=findings)
    assert frame.height == 7
    assert [f["exchange"] for f in findings] == ["XXX"]
    assert findings[0]["severity"] == "warning"


def test_backfill_walks_sessions_and_resumes_by_exchange(cfg, reader):
    calls, _missing = reader
    cfg._backfill = True
    cfg._backfill_start = date(2026, 9, 21)
    cfg._backfill_end = date(2026, 9, 24)
    result = derivatives.step_option_bars(cfg, date(2026, 9, 24), "bf", {})
    assert result["rows_written"] == 5 * 4
    compact_dataset(
        cfg.staging_root,
        cfg.curated_root,
        "option_bars",
        "bf",
        partition_col="trade_date",
        granularity="day",
    )
    calls.clear()
    again = derivatives.step_option_bars(cfg, date(2026, 9, 24), "bf2", {})
    assert calls == []
    assert again["rows_written"] == 0


def test_backfill_never_asks_before_the_exchange_existed(cfg, reader):
    calls, _missing = reader
    cfg._backfill = True
    cfg._backfill_start = date(2019, 12, 19)
    cfg._backfill_end = date(2019, 12, 24)
    derivatives.step_option_bars(cfg, date(2019, 12, 24), "bf", {})
    assert min(calls) == date(2019, 12, 23)


def _bars(rows: list[tuple[str, str, date]]) -> pl.LazyFrame:
    return pl.DataFrame(
        {
            "symbol": [r[0] for r in rows],
            "exchange": ["CFE"] * len(rows),
            "exchange_code": [r[1] for r in rows],
            "product": ["IF"] * len(rows),
            "trade_date": [r[2] for r in rows],
        }
    ).lazy()


def test_contract_dates_come_from_observation_and_say_so():
    bars = _bars(
        [
            # Already live on the first observed day: listing date unknown.
            ("IF2501.CFE", "IF2501", date(2025, 1, 2)),
            ("IF2501.CFE", "IF2501", date(2025, 1, 17)),
            # Listed and expired inside observed history.
            ("IF2503.CFE", "IF2503", date(2025, 1, 3)),
            ("IF2503.CFE", "IF2503", date(2025, 3, 21)),
            # Still live on the last observed day.
            ("IF2506.CFE", "IF2506", date(2025, 1, 20)),
            ("IF2506.CFE", "IF2506", date(2025, 3, 24)),
        ]
    )
    table = {r["symbol"]: r for r in build_futures_contracts(bars).to_dicts()}
    assert table["IF2501.CFE"]["dates_basis"] == "observed_truncated"
    assert table["IF2503.CFE"]["dates_basis"] == "observed"
    assert table["IF2503.CFE"]["list_date"] == date(2025, 1, 3)
    assert table["IF2503.CFE"]["last_trade_date"] == date(2025, 3, 21)
    assert table["IF2506.CFE"]["dates_basis"] == "open"
    assert table["IF2506.CFE"]["last_trade_date"] is None
    assert table["IF2506.CFE"]["multiplier"] == 300.0
    assert table["IF2506.CFE"]["delivery_month"] == date(2025, 6, 1)


def test_a_reference_file_names_a_live_contract_end():
    bars = _bars(
        [
            ("IF2506.CFE", "IF2506", date(2025, 1, 20)),
            ("IF2506.CFE", "IF2506", date(2025, 3, 24)),
        ]
    )
    reference = pl.DataFrame(
        {
            "symbol": ["IF2506.CFE"],
            "list_date": [date(2024, 10, 21)],
            "last_trade_date": [date(2025, 6, 20)],
        }
    )
    row = build_futures_contracts(bars, reference).row(0, named=True)
    assert row["dates_basis"] == "exchange"
    assert row["list_date"] == date(2024, 10, 21)
    assert row["last_trade_date"] == date(2025, 6, 20)


def test_option_contracts_carry_series_and_style():
    parsed = parse_daily_csv(FIXTURE.read_bytes(), date(2026, 9, 24))
    table = build_option_contracts(parsed.options.lazy())
    row = table.filter(pl.col("symbol") == "MO2610C6200.CFE").row(0, named=True)
    assert row["underlying_symbol"] == "000852.SH"
    assert row["underlying_kind"] == "index"
    assert row["exercise_style"] == "european"
    assert row["expiry_month"] == date(2026, 10, 1)
    assert row["multiplier"] == 100.0


def test_an_impossible_row_is_quarantined_not_the_session(cfg, monkeypatch):
    def fetch_day(d, *, config=None):
        day = _day(d)
        broken = day.futures.with_columns(
            pl.when(pl.col("symbol") == "IF2610.CFE")
            .then(-1)
            .otherwise(pl.col("open_interest"))
            .alias("open_interest")
        )
        return ExchangeDay("CFE", d, broken, day.options)

    monkeypatch.setitem(
        derivatives.READERS,
        "CFE",
        dataclasses.replace(derivatives.READERS["CFE"], fetch_day=fetch_day),
    )
    findings: list[dict] = []
    frame = derivatives.fetch_session(cfg, date(2026, 9, 24), "futures", findings=findings)
    assert frame.height == 6
    assert "IF2610.CFE" not in frame["symbol"].to_list()
    assert [f["check"] for f in findings] == ["futures_row_rejected"]


def test_real_file_shapes_pass_the_invariants():
    """Shapes the history sweep found: a close with no open/high/low on a
    traded row, an expiring option with no settlement, delta at -1.000001."""
    from cnequity.domain.schemas import derivative_bar_violations

    rows = pl.DataFrame(
        {
            "open": [None, None],
            "high": [None, None],
            "low": [None, None],
            "close": [92.45, 1.0],
            "settle": [92.45, None],
            "pre_settle": [90.0, 2.0],
            "volume": [1, 438],
            "amount": [None, 0.0],
            "open_interest": [13, 0],
            "strike": [435.0, 12200.0],
            "option_type": ["C", "C"],
            "delta": [-1.000001, 0.05],
            "exercise_volume": [0, 0],
            "implied_vol": [None, 0.3],
            "series_implied_vol": [None, None],
        }
    )
    assert rows.filter(derivative_bar_violations("option_bars")).is_empty()


def test_a_zero_settlement_is_a_price_only_for_options():
    from cnequity.domain.schemas import derivative_bar_violations

    none = pl.Series([None], dtype=pl.Float64)
    base = {
        "open": none,
        "high": none,
        "low": none,
        "close": none,
        "settle": [0.0],
        "pre_settle": [3.4],
        "volume": [0],
        "amount": [0.0],
        "open_interest": [5],
    }
    option = pl.DataFrame(
        {
            **base,
            "strike": [4600.0],
            "option_type": ["P"],
            "delta": none,
            "exercise_volume": [0],
            "implied_vol": none,
            "series_implied_vol": none,
        }
    )
    assert option.filter(derivative_bar_violations("option_bars")).is_empty()
    assert (
        option.with_columns(pl.lit(-0.2).alias("settle"))
        .filter(derivative_bar_violations("option_bars"))
        .height
        == 1
    )
    assert pl.DataFrame(base).filter(derivative_bar_violations("futures_bars")).height == 1
