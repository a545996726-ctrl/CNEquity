"""Settle factor/action contradictions with baostock, and switch sources on proof.

``adj_factors`` comes from Sina. Where its hfq factor and the recorded
corporate actions disagree — an action the factor never steps on, or a step
with no action — neither lake series can say which is wrong. Baostock
publishes its own back-adjusted factor per ex-date, an independent vendor's
view, so it can vote on each contradiction:

* action recorded, Sina still, baostock steps     -> Sina missed a step
* action recorded, Sina still, baostock still     -> the action is unconfirmed
* Sina steps, no action, baostock steps           -> the lake misses the action
* Sina steps, no action, baostock still           -> Sina's step is spurious

A security switches its whole factor series to baostock only when Sina is
shown wrong at least once and baostock also steps on every material event
where Sina and the recorded actions agree, so the switch cannot trade one
error for another. Nothing is spliced: a series comes from one vendor.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl

from cnequity.config import Config
from cnequity.domain.canonical import dedupe_lazy_by_primary_key
from cnequity.query.parquet_scan import scan_parquet_root
from cnequity.storage.atomic import write_json_atomic
from cnequity.storage.state import StateStore

logger = logging.getLogger(__name__)

# StateStore field on ``adj_factors`` naming securities whose series comes from
# baostock, with the evidence that justified each switch.
SOURCE_OVERRIDES_FIELD = "source_overrides"
# Sessions on each side of a date whose median level is compared.
_LEVEL_WINDOW = 5
# Sina steps below 3x this are too small to be events baostock must confirm.
_MIN_STEP = 1.5e-3
# Baostock's factor table carries six decimals, so a real step far below
# ``_MIN_STEP`` is still a step: a 0.03% dividend matched Sina to 1e-6.
_BAO_MIN_STEP = 1e-5
# Split-share reform: consideration shares Sina steps on and Baostock's factor
# table leaves out, so neither vendor is proven wrong on these dates.
_SHARE_REFORM = (date(2005, 4, 29), date(2007, 12, 31))
# A price gap says something about one ex-date only when the previous session
# is this close; across a long suspension it mixes months of moves.
_MAX_PRICE_GAP_DAYS = 10
# Symbols per baostock sweep: one staged evidence batch.
_BATCH = 100
_SAMPLE = 20
# Cached baostock batches older than this are fetched again.
_CACHE_MAX_AGE = timedelta(days=7)
# Batches from another fetch method are ignored rather than mixed in.
_CACHE_METHOD = "adjust-factor-events"
_FETCH_START = date(1990, 1, 1)


def source_overrides(config: Config) -> dict[str, dict]:
    """Securities switched to baostock factors, keyed by symbol."""
    payload = StateStore(config.meta_root).get_payload("adj_factors")
    overrides = payload.get(SOURCE_OVERRIDES_FIELD) or {}
    return overrides if isinstance(overrides, dict) else {}


def _baostock_stock(symbol: str) -> bool:
    code, _, exchange = symbol.partition(".")
    if exchange == "SH":
        return code.startswith(("60", "688"))
    if exchange == "SZ":
        return code.startswith(("00", "30"))
    return False


def _level_steps(column: str) -> pl.Expr:
    """Relative level change at each date: median after vs median before."""
    before = pl.col(column).shift(1).rolling_median(_LEVEL_WINDOW, min_samples=1)
    after = pl.col(column).reverse().rolling_median(_LEVEL_WINDOW, min_samples=1).reverse()
    return (after / before - 1).over("symbol")


def _lake_factors(config: Config, symbols: list[str]) -> pl.DataFrame:
    return (
        dedupe_lazy_by_primary_key(
            scan_parquet_root(config.derived_root / "adj_factors", partition_col="trade_date"),
            "adj_factors",
        )
        .filter((pl.col("adjust_type") == "hfq") & pl.col("symbol").is_in(symbols))
        .select("symbol", "trade_date", pl.col("factor").alias("sina"))
        .collect()
        .sort("symbol", "trade_date")
    )


def _cache_dir(config: Config) -> Path:
    return config.meta_root / "adj_factor_arbitration_cache"


def _cached_batches(config: Config) -> tuple[list[pl.DataFrame], set[str]]:
    """Batches fetched within ``_CACHE_MAX_AGE``, and the symbols they answered."""
    folder = _cache_dir(config)
    if not folder.is_dir():
        return [], set()
    oldest = datetime.now(timezone.utc) - _CACHE_MAX_AGE
    frames: list[pl.DataFrame] = []
    answered: set[str] = set()
    for sidecar in sorted(folder.glob("*.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(meta["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        rows = sidecar.with_suffix(".parquet")
        if meta.get("method") != _CACHE_METHOD or fetched < oldest or not rows.is_file():
            continue
        frames.append(pl.read_parquet(rows))
        answered.update(meta.get("answered") or [])
    return frames, answered


def _cache_batch(config: Config, frame: pl.DataFrame, answered: list[str]) -> None:
    folder = _cache_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    fetched = datetime.now(timezone.utc)
    stem = f"{_CACHE_METHOD}-{fetched.strftime('%Y%m%dT%H%M%S%fZ')}"
    tmp = folder / f"{stem}.parquet.tmp"
    frame.write_parquet(tmp)
    tmp.replace(folder / f"{stem}.parquet")
    # The sidecar lands last, so a batch counts only once its rows are on disk.
    write_json_atomic(
        folder / f"{stem}.json",
        {"method": _CACHE_METHOD, "fetched_at": fetched.isoformat(), "answered": answered},
    )


def _baostock_factors(
    config: Config, names: list[str], end: date, fetch
) -> tuple[pl.DataFrame, list[str]]:
    """Baostock factor rows for *names*: one per ex-date, or a daily series.

    Each batch is cached as it lands, so an interrupted sweep resumes from the
    symbols still unanswered, and ``--apply`` after a preview reuses the
    preview's evidence. Failed symbols are not cached and are asked again.
    The window always starts before any listing, so each symbol's first row is
    the factor in force at listing rather than a mid-history step.
    """
    frames: list[pl.DataFrame] = []
    failed: list[str] = []
    cached, answered = _cached_batches(config)
    wanted = set(names)
    frames.extend(frame.filter(pl.col("symbol").is_in(wanted)) for frame in cached)
    todo = [name for name in names if name not in answered]
    if len(todo) < len(names):
        logger.info(
            "factor arbitration: %d of %d symbol(s) reused from cache",
            len(names) - len(todo),
            len(names),
        )
    for offset in range(0, len(todo), _BATCH):
        chunk = todo[offset : offset + _BATCH]
        frame, chunk_failed = fetch(chunk, _FETCH_START, end, config=config)
        chunk_failed = list(chunk_failed)
        unanswered = set(chunk_failed)
        _cache_batch(config, frame, [name for name in chunk if name not in unanswered])
        frames.append(frame)
        failed.extend(chunk_failed)
    empty = pl.DataFrame(schema={"symbol": pl.Utf8, "trade_date": pl.Date, "factor": pl.Float64})
    return (pl.concat(frames) if frames else empty), failed


def _price_gaps(config: Config, symbols: list[str]) -> pl.DataFrame:
    """Raw close gap into each session: the factor step the price itself implies."""
    frame = (
        dedupe_lazy_by_primary_key(
            scan_parquet_root(
                config.curated_root / "daily_bars", partition_col="trade_date", traded_only=True
            ),
            "daily_bars",
        )
        .filter(pl.col("symbol").is_in(symbols))
        .select("symbol", "trade_date", "close")
        .collect()
        .sort("symbol", "trade_date")
    )
    return frame.select(
        "symbol",
        "trade_date",
        pl.col("trade_date").shift(1).over("symbol").alias("prev_date"),
        (pl.col("close").shift(1).over("symbol") / pl.col("close") - 1).alias("price_gap"),
    )


def arbitrate_factor_sources(
    config: Config,
    *,
    symbols: list[str] | None = None,
    fetch=None,
) -> dict:
    """Verdicts per contradiction and the securities that should switch source."""
    from cnequity.quality.cross_checks import factor_action_contradictions

    if fetch is None:
        from cnequity.adapters.baostock.adj_factors import (
            fetch_adjust_factor_events_baostock_many as fetch,
        )

    contradictions, _ = factor_action_contradictions(config)
    contradictions = contradictions.filter(
        pl.col("symbol").map_elements(_baostock_stock, return_dtype=pl.Boolean)
    )
    if symbols:
        contradictions = contradictions.filter(pl.col("symbol").is_in(symbols))
    report: dict = {"contradictions": contradictions.height, "symbols": 0, "switch": []}
    if contradictions.is_empty():
        return report
    names = sorted(contradictions.get_column("symbol").unique().to_list())
    lake = _lake_factors(config, names)
    baostock, failed = _baostock_factors(config, names, lake.get_column("trade_date").max(), fetch)
    report.update(symbols=len(names), baostock_unserved=len(failed))

    # Baostock's factor holds from each row's date until the next, so carry it
    # onto every lake session. Sessions before a symbol's first row, and
    # symbols it never answered, get no baostock opinion.
    joined = (
        lake.join_asof(
            baostock.select("symbol", "trade_date", pl.col("factor").alias("bao")).sort(
                "symbol", "trade_date"
            ),
            on="trade_date",
            by="symbol",
            strategy="backward",
            check_sortedness=False,
        )
        .filter(pl.col("bao").is_not_null())
        .sort("symbol", "trade_date")
        .with_columns(
            _level_steps("sina").alias("sina_step"),
            _level_steps("bao").alias("bao_step"),
            (pl.col("sina") / pl.col("sina").shift(1).over("symbol") - 1)
            .abs()
            .alias("_sina_daily"),
        )
    )
    bao_steps = pl.col("bao_step").abs() > _BAO_MIN_STEP
    judged = (
        contradictions.join(
            joined.select("symbol", "trade_date", "sina_step", "bao_step"),
            left_on=["symbol", "ex_date"],
            right_on=["symbol", "trade_date"],
            how="inner",
        )
        .filter(
            # A symbol's first baostock session has no level before it to compare.
            pl.col("bao_step").is_not_null()
        )
        .with_columns(
            pl.when(pl.col("kind") == "action_without_step")
            .then(
                pl.when(bao_steps)
                .then(pl.lit("sina_missed_step"))
                .otherwise(pl.lit("action_unconfirmed"))
            )
            .when(~bao_steps)
            .then(pl.lit("sina_spurious_step"))
            .when((pl.col("bao_step") > 0) == (pl.col("sina_step") > 0))
            .then(pl.lit("action_missing"))
            .otherwise(pl.lit("disputed"))
            .alias("verdict")
        )
        .join(
            _price_gaps(config, names),
            left_on=["symbol", "ex_date"],
            right_on=["symbol", "trade_date"],
            how="left",
        )
        .with_columns(
            pl.col("verdict").is_in(["sina_missed_step", "sina_spurious_step"]).alias("sina_wrong"),
            # Baostock's view is proof only where the raw price backs it: a
            # session close enough to be one event, outside the share reform,
            # vendors materially apart, and a gap nearer Baostock's step.
            (
                ((pl.col("ex_date") - pl.col("prev_date")).dt.total_days() <= _MAX_PRICE_GAP_DAYS)
                & ~pl.col("ex_date").is_between(*_SHARE_REFORM)
                # Vendors a hair apart differ on timing, not on the event.
                & ((pl.col("sina_step") - pl.col("bao_step")).abs() > 3 * _MIN_STEP)
                & (
                    (pl.col("price_gap") - pl.col("bao_step")).abs()
                    < (pl.col("price_gap") - pl.col("sina_step")).abs()
                )
            )
            .fill_null(False)
            .alias("price_confirms"),
        )
    )
    # Material events both lake series already agree on: a vendor that is to
    # replace Sina must see them too.
    contra_keys = contradictions.select("symbol", pl.col("ex_date").alias("trade_date"))
    agreed = (
        joined.filter(pl.col("_sina_daily") > 1e-6)
        .join(contra_keys, on=["symbol", "trade_date"], how="anti")
        .filter((pl.col("sina_step").abs() > 3 * _MIN_STEP) & pl.col("bao_step").is_not_null())
        .with_columns((~(pl.col("bao_step").abs() > _BAO_MIN_STEP)).alias("bao_missed"))
    )
    per_symbol = (
        judged.group_by("symbol")
        .agg(
            pl.col("sina_wrong").sum().alias("sina_wrong"),
            (pl.col("sina_wrong") & pl.col("price_confirms")).sum().alias("sina_proven"),
            (pl.col("verdict") == "disputed").sum().alias("disputed"),
            pl.len().alias("contradictions"),
        )
        .join(
            agreed.group_by("symbol").agg(
                pl.col("bao_missed").sum().alias("bao_missed_agreed"),
                pl.len().alias("agreed_events"),
            ),
            on="symbol",
            how="left",
        )
        .with_columns(
            pl.col("bao_missed_agreed").fill_null(0), pl.col("agreed_events").fill_null(0)
        )
    )
    # Every Sina error must be price-proven: a switch replaces the whole series,
    # so an unproven row would trade Sina's view for Baostock's unchecked.
    switch = per_symbol.filter(
        (pl.col("sina_wrong") > 0)
        & (pl.col("sina_proven") == pl.col("sina_wrong"))
        & (pl.col("disputed") == 0)
        & (pl.col("bao_missed_agreed") == 0)
    ).sort("symbol")
    unproven = per_symbol.filter(pl.col("sina_proven") < pl.col("sina_wrong"))
    verdicts = dict(judged.group_by("verdict").len().iter_rows())
    report.update(
        judged=judged.height,
        verdicts=verdicts,
        switch=switch.get_column("symbol").to_list(),
        sina_wrong_unproven=unproven.height,
        switch_detail={
            row["symbol"]: {
                "sina_wrong": int(row["sina_wrong"]),
                "contradictions": int(row["contradictions"]),
                "agreed_events_checked": int(row["agreed_events"]),
            }
            for row in switch.iter_rows(named=True)
        },
        action_missing_sample=[
            {"symbol": row["symbol"], "ex_date": row["ex_date"].isoformat()}
            for row in judged.filter(pl.col("verdict") == "action_missing")
            .head(_SAMPLE)
            .iter_rows(named=True)
        ],
    )
    folder = config.meta_root / "quality" / "evidence"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = folder / f"adj_factor_source_arbitration-{stamp}.json"
    write_json_atomic(
        path,
        {
            **report,
            "verdict_rows": [
                {**row, "ex_date": row["ex_date"].isoformat()}
                for row in judged.select(
                    "symbol",
                    "ex_date",
                    "kind",
                    "verdict",
                    "sina_step",
                    "bao_step",
                    "price_gap",
                    pl.col("prev_date").cast(pl.Utf8),
                    "price_confirms",
                ).iter_rows(named=True)
            ],
        },
        indent=2,
    )
    report["evidence"] = str(path)
    return report


def record_source_overrides(config: Config, report: dict) -> list[str]:
    """Persist the switches a report justified; returns the switched symbols."""
    switched = list(report.get("switch") or [])
    if not switched:
        return []
    decided = datetime.now(timezone.utc).isoformat()
    with StateStore(config.meta_root).transaction("adj_factors") as state:
        overrides = dict(state.get(SOURCE_OVERRIDES_FIELD) or {})
        for symbol in switched:
            overrides[symbol] = {
                "source": "baostock",
                "decided_at": decided,
                "evidence": report.get("evidence"),
                **report["switch_detail"].get(symbol, {}),
            }
        state[SOURCE_OVERRIDES_FIELD] = json.loads(json.dumps(overrides, default=str))
    return switched
