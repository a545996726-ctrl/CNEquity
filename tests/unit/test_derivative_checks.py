"""Cross-section proof and per-exchange gap audit for futures/option bars."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.domain.schemas import validate_dataframe, with_provenance
from cnequity.quality.derivative_checks import (
    derivative_findings,
    derivative_tip_scope,
    exchange_session_gaps,
)
from cnequity.storage.parquet import StagingWriter, compact_dataset


@pytest.fixture
def cfg(tmp_path) -> Config:
    config = Config(data_root=tmp_path / "data")
    config.futures_enabled = True
    config.futures_exchanges = ["CFE"]
    return config


def _bar(symbol: str, day: date) -> dict:
    return {
        "symbol": symbol,
        "exchange": "CFE",
        "exchange_code": symbol.split(".")[0],
        "product": "IF",
        "trade_date": day,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "settle": 4000.0,
        "pre_settle": 4000.0,
        "volume": 0,
        "amount": 0.0,
        "open_interest": 10,
        "oi_change": 0,
        "source": "futures_exchange",
    }


def _write(cfg: Config, dataset: str, rows: list[dict], granularity="month") -> None:
    frame = with_provenance(
        pl.DataFrame(rows, infer_schema_length=None), source="futures_exchange", data_version="v1"
    )
    frame = validate_dataframe(frame, dataset)
    StagingWriter(cfg.staging_root).write_batch(dataset, "r1", "b0", frame)
    partition = None if dataset.endswith("_contracts") else "trade_date"
    compact_dataset(
        cfg.staging_root,
        cfg.curated_root,
        dataset,
        "r1",
        partition_col=partition,
        granularity=granularity if partition else None,
    )


def test_a_live_contract_missing_from_the_tip_is_incomplete(cfg):
    _write(
        cfg,
        "futures_bars",
        [
            _bar("IF2610.CFE", date(2026, 9, 23)),
            _bar("IF2611.CFE", date(2026, 9, 23)),
            _bar("IF2610.CFE", date(2026, 9, 24)),
        ],
    )
    report = derivative_tip_scope(cfg, "futures_bars")
    assert report["state"] == "incomplete"
    assert report["missing"] == ["IF2611.CFE"]
    assert report["exchanges"] == {"CFE": [1, 2]}


def test_a_contract_past_its_last_trading_day_is_not_owed(cfg):
    _write(
        cfg,
        "futures_bars",
        [
            _bar("IF2609.CFE", date(2026, 9, 23)),
            _bar("IF2610.CFE", date(2026, 9, 23)),
            _bar("IF2610.CFE", date(2026, 9, 24)),
        ],
    )
    contract = {
        "symbol": "IF2609.CFE",
        "exchange": "CFE",
        "exchange_code": "IF2609",
        "product": "IF",
        "product_name": "沪深300股指期货",
        "delivery_month": date(2026, 9, 1),
        "list_date": date(2026, 1, 19),
        "last_trade_date": date(2026, 9, 23),
        "multiplier": 300.0,
        "tick_size": 0.2,
        "quote_unit": "点",
        "dates_basis": "exchange",
        "first_seen_date": date(2026, 1, 19),
        "last_seen_date": date(2026, 9, 23),
        "source": "futures_exchange",
    }
    _write(cfg, "futures_contracts", [contract])
    report = derivative_tip_scope(cfg, "futures_bars")
    assert report["state"] == "complete"
    assert report["expected"] == 1


def test_a_single_session_cannot_be_proved(cfg):
    _write(cfg, "futures_bars", [_bar("IF2610.CFE", date(2026, 9, 24))])
    assert derivative_tip_scope(cfg, "futures_bars")["state"] == "unverified"


def test_disabled_family_is_not_judged(tmp_path):
    assert derivative_tip_scope(Config(data_root=tmp_path / "d"), "futures_bars") is None


def test_an_exchange_hole_inside_its_span_is_reported(cfg):
    _write(
        cfg,
        "futures_bars",
        [
            _bar("IF2610.CFE", date(2026, 9, 21)),
            _bar("IF2610.CFE", date(2026, 9, 23)),
            _bar("IF2610.CFE", date(2026, 9, 24)),
        ],
    )
    assert exchange_session_gaps(cfg, "futures_bars") == {"CFE": [date(2026, 9, 22)]}
    checks = [f["check"] for f in derivative_findings(cfg)]
    assert checks == ["futures_exchange_session_gap"]


def test_a_session_the_exchange_never_published_is_not_a_gap(cfg):
    cfg.futures_exchanges = ["SHF"]
    rows = []
    for day in (date(2007, 6, 1), date(2007, 6, 5)):
        bar = _bar("CU0708.SHF", day)
        bar.update({"exchange": "SHF", "exchange_code": "CU0708", "product": "CU"})
        rows.append(bar)
    _write(cfg, "futures_bars", rows)
    # SHFE's archive has no 2007-06-04 file (404 while both neighbours exist).
    assert exchange_session_gaps(cfg, "futures_bars") == {}


def test_prices_off_the_tick_grid_and_a_wrong_lot_are_reported(cfg):
    traded = []
    for day, price in ((date(2026, 9, 23), 4000.1), (date(2026, 9, 24), 4000.3)):
        row = _bar("IF2610.CFE", day)
        # IF ticks by 0.2, so 4000.1 and 4000.3 are off the grid; 300 × 5 lots
        # at ~4000 is ~6,000,000, so 2,000,000 implies a lot of ~100, not 300.
        row.update(
            open=price,
            high=price,
            low=price,
            close=price,
            settle=price,
            volume=5,
            amount=2_000_000.0,
        )
        traded.append(row)
    _write(cfg, "futures_bars", traded)
    checks = {f["check"] for f in derivative_findings(cfg)}
    assert {"futures_tick_grid", "futures_multiplier_mismatch"} <= checks


def test_without_an_end_date_a_contract_in_delivery_is_not_owed(cfg):
    _write(
        cfg,
        "futures_bars",
        [
            # IF2609 is in its delivery month on the tip and has no known end:
            # it may have stopped trading, so it is not owed.
            _bar("IF2609.CFE", date(2026, 9, 23)),
            _bar("IF2610.CFE", date(2026, 9, 23)),
            _bar("IF2610.CFE", date(2026, 9, 24)),
        ],
    )
    report = derivative_tip_scope(cfg, "futures_bars")
    assert report["state"] == "complete"
    assert report["expected"] == 1
