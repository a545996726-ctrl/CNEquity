"""15m / 30m / 60m bars stored on request, resampled from the lake's 1m and 5m.

Nothing schedules this. The coarser bars are a pure function of minute data the
lake already holds, so by default they are computed at read time
(``cnequity.query.resample_minute_history``); ``cne derive minute_bars_15m``
and its siblings are for users who want them as ordinary datasets — readable by
SQL, the HTTP API and MCP without a Python step.

Each symbol-day is built from 1m when the lake has 1m for it and from 5m
otherwise, and ``resampled_from`` records which. 1m keeps trade-only OHLC; the
vendor's 5m already folds untraded carry quotes into its OHLC, but reaches back
about two years where 1m stops after roughly 95 trading days.

A symbol-day whose bars leave an interval partly filled is skipped and counted
instead of failing the session. Halted names are the ordinary case: the source
returns 239 untraded 1m bars for them, one short of a session.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

import polars as pl

from cnequity.domain.datasets import RESAMPLED_MINUTE_DATASETS
from cnequity.query.resample import incomplete_symbol_days, resample_minute_history

if TYPE_CHECKING:
    from pathlib import Path

    from cnequity.config import Config

logger = logging.getLogger(__name__)

FREQUENCY_BY_DATASET = RESAMPLED_MINUTE_DATASETS
INPUTS = ("minute_bars", "minute_bars_5m")


def _partition_day(directory: Path) -> date | None:
    try:
        return date.fromisoformat(directory.name.split("=", 1)[1])
    except (IndexError, ValueError):
        return None


def _newest_mtime(directory: Path) -> float | None:
    times = [p.stat().st_mtime for p in directory.rglob("*.parquet")] if directory.is_dir() else []
    return max(times) if times else None


def _input_days(config: Config) -> dict[date, float]:
    """Every session either input holds, with its newest input file time."""
    from cnequity.storage.read_context import read_root

    days: dict[date, float] = {}
    for dataset in INPUTS:
        root = read_root(config, dataset)
        for directory in root.glob("trade_date=*") if root.is_dir() else []:
            day, mtime = _partition_day(directory), _newest_mtime(directory)
            if day is not None and mtime is not None:
                days[day] = max(days.get(day, 0.0), mtime)
    return days


def stale_sessions(config: Config, dataset: str) -> list[date]:
    """Sessions with no output yet, or whose minute input changed after it.

    A later 1m backfill is the case that matters: a session first built from
    5m must be rebuilt once 1m arrives for it.
    """
    from cnequity.storage.read_context import read_root

    output = read_root(config, dataset)
    return sorted(
        day
        for day, source in _input_days(config).items()
        if (_newest_mtime(output / f"trade_date={day.isoformat()}") or 0.0) < source
    )


def _session(config: Config, dataset: str, day: date) -> pl.DataFrame:
    from cnequity.domain.canonical import dedupe_by_primary_key
    from cnequity.domain.schemas import DATASET_SCHEMAS
    from cnequity.query.parquet_scan import scan_parquet_files
    from cnequity.storage.read_context import read_root

    files = sorted(
        (read_root(config, dataset) / f"trade_date={day.isoformat()}").rglob("*.parquet")
    )
    if not files:
        return pl.DataFrame(schema=DATASET_SCHEMAS[dataset])
    return dedupe_by_primary_key(scan_parquet_files(files).collect(), dataset)


def resample_session(
    minute_1m: pl.DataFrame, minute_5m: pl.DataFrame, frequency: str
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Resample one session; return the bars and the symbol-days set aside.

    A symbol-day with 1m is judged on its 1m alone, as in
    :func:`resample_minute_history`, so an incomplete 1m day is skipped rather
    than quietly rebuilt from 5m.
    """
    keys = ["symbol", "trade_date"]
    skipped = pl.concat(
        [
            incomplete_symbol_days(minute_1m, frequency).with_columns(pl.lit("1m").alias("input")),
            incomplete_symbol_days(
                minute_5m.join(minute_1m.select(keys).unique(), on=keys, how="anti"), frequency
            ).with_columns(pl.lit("5m").alias("input")),
        ]
    )
    bars = resample_minute_history(
        minute_1m.join(skipped, on=keys, how="anti"),
        minute_5m.join(skipped.select(keys), on=keys, how="anti").join(
            minute_1m.select(keys).unique(), on=keys, how="anti"
        ),
        frequency,
    )
    return bars, skipped


def derive_minute_resample(
    config: Config,
    dataset: str,
    *,
    start: date | None = None,
    end: date | None = None,
    full: bool = False,
) -> dict:
    """Compute and write one of the ``minute_bars_{15,30,60}m`` datasets.

    By default only :func:`stale_sessions` are rebuilt; ``start``/``end``
    pick a window instead, and ``full`` rebuilds every input session. No
    watermark is kept: nothing schedules these datasets, so one would only
    ever read as stale.
    """
    from cnequity.domain.schemas import validate_dataframe, with_provenance
    from cnequity.file_lock import lake_mutation_lock
    from cnequity.storage.parquet import CuratedWriter

    frequency = FREQUENCY_BY_DATASET[dataset]
    if start is None and end is None and not full:
        sessions = stale_sessions(config, dataset)
        if not sessions:
            return {"rows": 0, "note": f"{dataset} 已是最新：分钟线输入没有更新"}
    else:
        sessions = sorted(
            day
            for day in _input_days(config)
            if (start is None or day >= start) and (end is None or day <= end)
        )
        if not sessions:
            return {"rows": 0, "note": f"{dataset}：所选窗口内没有 1m 或 5m 数据"}
    writer = CuratedWriter(config.derived_root)
    rows = 0
    built = {"1m": 0, "5m": 0}
    skipped = {"1m": 0, "5m": 0}
    written: list[date] = []
    with lake_mutation_lock(config.meta_root, blocking=True):
        for day in sessions:
            bars, rejected = resample_session(
                _session(config, "minute_bars", day),
                _session(config, "minute_bars_5m", day),
                frequency,
            )
            for source, count in rejected.group_by("input").len().iter_rows():
                skipped[source] += count
            if bars.is_empty():
                continue
            for source, count in (
                bars.select("symbol", "resampled_from").unique().group_by("resampled_from").len()
            ).iter_rows():
                built[source] += count
            frame = validate_dataframe(
                with_provenance(bars, source="derived", data_version="v1"), dataset
            )
            writer.write_partition(
                dataset, "trade_date", day.isoformat(), frame, "part-000.parquet"
            )
            rows += frame.height
            written.append(day)
    return {
        "rows": rows,
        "sessions": len(written),
        "first": str(written[0]) if written else None,
        "last": str(written[-1]) if written else None,
        "symbol_days_from_1m": built["1m"],
        "symbol_days_from_5m": built["5m"],
        "skipped_incomplete_1m": skipped["1m"],
        "skipped_incomplete_5m": skipped["5m"],
    }
