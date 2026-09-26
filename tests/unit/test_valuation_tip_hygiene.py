"""Baostock history must not invent a sparse valuation tip past EastMoney."""

from datetime import date

import polars as pl

from cnequity.config import Config
from cnequity.domain.schemas import DAILY_BARS_SCHEMA, VALUATION_METRICS_SCHEMA
from cnequity.quality.cross_checks import (
    last_complete_em_valuation_tip,
    last_dense_valuation_date,
    valuation_day_coverage_ratio,
)
from cnequity.steps.finalize import _reconcile_watermarks, _watermark_date_for
from cnequity.steps.fundamentals import _valuation_history_end
from cnequity.storage.state import StateStore


def _write_day(
    root,
    dataset,
    d: date,
    symbols: list[str],
    *,
    source: str,
    schema: dict,
    partition_value: str | None = None,
    file_name: str = "part-merged.parquet",
):
    part = root / dataset / f"trade_date={partition_value or d.isoformat()}"
    part.mkdir(parents=True, exist_ok=True)
    n = len(symbols)
    cols = {
        "symbol": symbols,
        "trade_date": [d] * n,
        "source": [source] * n,
        "data_version": ["v1"] * n,
        "fetched_at": ["2026-07-01T00:00:00+00:00"] * n,
    }
    if dataset == "daily_bars":
        cols.update(
            {
                "open": [1.0] * n,
                "high": [1.0] * n,
                "low": [1.0] * n,
                "close": [1.0] * n,
                "volume": [1] * n,
                "amount": [1.0] * n,
            }
        )
    else:
        cols.update(
            {
                "pe_ttm": [10.0] * n,
                "pb": [1.0] * n,
                "ps_ttm": [2.0] * n,
                "total_mv": [1e9] * n,
                "float_mv": [1e9] * n,
            }
        )
    keep = [c for c in schema if c in cols]
    (
        pl.DataFrame({c: cols[c] for c in keep})
        .with_columns(pl.col("fetched_at").str.to_datetime(time_unit="us", time_zone="UTC"))
        .write_parquet(part / file_name)
    )


def _lake(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    cfg.curated_root.mkdir(parents=True, exist_ok=True)
    cfg.meta_root.mkdir(parents=True, exist_ok=True)
    return cfg


def test_coverage_ratio_and_dense_tip(tmp_path):
    cfg = _lake(tmp_path)
    dense = date(2026, 7, 16)
    sparse = date(2026, 7, 22)
    bars = [f"{i:06d}.SH" for i in range(600000, 600010)]
    _write_day(cfg.curated_root, "daily_bars", dense, bars, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(cfg.curated_root, "daily_bars", sparse, bars, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        dense,
        bars,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
    )
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        sparse,
        bars[:2],
        source="baostock",
        schema=VALUATION_METRICS_SCHEMA,
    )

    assert valuation_day_coverage_ratio(cfg, dense) == 1.0
    assert valuation_day_coverage_ratio(cfg, sparse) == 0.2
    assert last_dense_valuation_date(cfg) == dense
    assert last_complete_em_valuation_tip(cfg) == dense


def test_dense_tip_includes_root_legacy_files_in_mixed_layout(tmp_path):
    cfg = _lake(tmp_path)
    old = date(2026, 7, 16)
    latest = date(2026, 7, 22)
    bars = [f"{i:06d}.SH" for i in range(600000, 600005)]
    _write_day(cfg.curated_root, "daily_bars", old, bars, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        old,
        bars,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
    )
    _write_day(
        cfg.curated_root,
        "daily_bars",
        latest,
        bars,
        source="tdx",
        schema=DAILY_BARS_SCHEMA,
    )
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        latest,
        bars,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
    )
    latest_dir = cfg.curated_root / "valuation_metrics" / f"trade_date={latest.isoformat()}"
    legacy_path = cfg.curated_root / "valuation_metrics" / "part-legacy.parquet"
    pl.read_parquet(latest_dir / "part-merged.parquet").write_parquet(legacy_path)
    (latest_dir / "part-merged.parquet").unlink()
    latest_dir.rmdir()

    assert last_dense_valuation_date(cfg) == latest


def test_dense_tip_reads_real_dates_from_coarse_partitions(tmp_path):
    cfg = _lake(tmp_path)
    dense = date(2026, 7, 16)
    sparse = date(2026, 7, 22)
    bars = [f"{i:06d}.SH" for i in range(600000, 600005)]
    for d in (dense, sparse):
        _write_day(cfg.curated_root, "daily_bars", d, bars, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        dense,
        bars,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
        partition_value="2026-07",
        file_name="dense.parquet",
    )
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        sparse,
        bars[:1],
        source="baostock",
        schema=VALUATION_METRICS_SCHEMA,
        partition_value="2026-07",
        file_name="sparse.parquet",
    )

    assert last_dense_valuation_date(cfg) == dense


def test_history_end_caps_at_complete_em_tip(tmp_path):
    cfg = _lake(tmp_path)
    tip = date(2026, 7, 16)
    bars = [f"{i:06d}.SH" for i in range(600000, 600005)]
    _write_day(cfg.curated_root, "daily_bars", tip, bars, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        tip,
        bars,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
    )

    assert _valuation_history_end(cfg, date(2026, 7, 24)) == tip


def test_watermark_date_ignores_sparse_tip(tmp_path):
    cfg = _lake(tmp_path)
    dense = date(2026, 7, 16)
    sparse = date(2026, 7, 22)
    bars = [f"{i:06d}.SH" for i in range(600000, 600010)]
    for d in (dense, sparse):
        _write_day(cfg.curated_root, "daily_bars", d, bars, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        dense,
        bars,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
    )
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        sparse,
        bars[:2],
        source="baostock",
        schema=VALUATION_METRICS_SCHEMA,
    )

    assert _watermark_date_for(cfg, "valuation_metrics", "trade_date") == dense

    state = StateStore(cfg.meta_root)
    state.set_date("valuation_metrics", sparse)
    findings = _reconcile_watermarks(cfg)
    assert state.get_date("valuation_metrics") == dense
    assert any(f["check"] == "valuation_watermark_coverage_gate" for f in findings)


def test_baostock_single_flight_refuses_overlap(tmp_path):
    from cnequity.orchestrator.run_lock import run_lock
    from cnequity.steps.fundamentals import _backfill_valuation_metrics

    cfg = _lake(tmp_path)
    with run_lock(cfg.meta_root, "baostock"):
        out = _backfill_valuation_metrics(cfg, date(2026, 7, 24), "run-1")
    assert out["status"] == "warning"
    assert out["rows_written"] == 0
    assert "baostock lock" in out["note"]
    assert out["context_updates"]["audit_findings"][0]["check"] == "baostock_single_flight"


def _em_tip_lake(tmp_path, tip: date, symbols: list[str]):
    cfg = _lake(tmp_path)
    _write_day(cfg.curated_root, "daily_bars", tip, symbols, source="tdx", schema=DAILY_BARS_SCHEMA)
    _write_day(
        cfg.curated_root,
        "valuation_metrics",
        tip,
        symbols,
        source="eastmoney",
        schema=VALUATION_METRICS_SCHEMA,
    )
    return cfg


def test_an_outage_fill_reaches_only_the_days_eastmoney_missed(tmp_path):
    """push2 failed every host from 2026-09-22; the last EM day was 09-21."""
    from cnequity.steps.fundamentals import _em_outage_window

    symbols = [f"{i:06d}.SH" for i in range(600000, 600005)]
    cfg = _em_tip_lake(tmp_path, date(2026, 9, 21), symbols)

    # Starts after the last EastMoney day, never on the run day it still owns.
    assert _em_outage_window(cfg, date(2026, 9, 26), date(2026, 9, 1), date(2026, 9, 30)) == (
        date(2026, 9, 22),
        date(2026, 9, 25),
    )


def test_an_outage_fill_needs_an_end_and_an_eastmoney_anchor(tmp_path):
    import pytest

    from cnequity.steps.fundamentals import _em_outage_window

    with pytest.raises(RuntimeError, match="--end"):
        _em_outage_window(_lake(tmp_path), date(2026, 9, 26), date(2026, 9, 22), None)
    with pytest.raises(RuntimeError, match="anchor"):
        _em_outage_window(_lake(tmp_path), date(2026, 9, 26), date(2026, 9, 22), date(2026, 9, 24))


def test_an_incomplete_outage_fill_publishes_nothing(tmp_path, monkeypatch):
    import pytest

    from cnequity.steps import fundamentals

    symbols = [f"{i:06d}.SH" for i in range(600000, 600003)]
    cfg = _em_tip_lake(tmp_path, date(2026, 9, 21), symbols)
    cfg._backfill_start = date(2026, 9, 22)
    cfg._backfill_end = date(2026, 9, 24)
    cfg._valuation_fill_em_outage = True
    monkeypatch.setattr(fundamentals, "load_symbols", lambda _cfg: symbols)
    monkeypatch.setattr(
        "cnequity.storage.valuation_orphans.purge_valuation_orphan_symbols", lambda _cfg: {}
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.valuation.fetch_valuation_history",
        lambda batch, start, end, config=None: (pl.DataFrame(), [batch[0]]),
    )

    with pytest.raises(RuntimeError, match="nothing will be published"):
        fundamentals._backfill_valuation_metrics_locked(cfg, date(2026, 9, 26), "run-outage")
