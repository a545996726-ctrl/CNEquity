"""Futures and option datasets from the exchanges' own daily files (ADR-0013).

Several exchanges feed one dataset, and they are independent failure domains.
A session is therefore written with whichever exchanges published it; each one
that did not becomes an audit finding, and the three-session reconciliation
tail comes back for it on the next runs. Only a session none of them answered
fails the step — that is an outage, not a straggler.

Backfill walks one session at a time (``walk_day_backfill``): every file is
one day, and a range fetch would meet the run-day date guard in
``fetch_incremental_daily``. A session counts as done only when every exchange
that was trading that day has rows for it, so a rerun fills the exchange that
failed instead of skipping the whole date.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import polars as pl

from cnequity.adapters.futures_exchange import (
    SUPPORTED_EXCHANGES,
    cffex,
    czce,
    dce,
    gfex,
    members,
    shfe,
)
from cnequity.adapters.futures_exchange.common import (
    SOURCE,
    ExchangeDay,
    FuturesDayUnavailable,
    FuturesSourceBlocked,
)
from cnequity.adapters.sina import dce_futures
from cnequity.config import Config
from cnequity.domain.datasets import is_dataset_enabled
from cnequity.orchestrator.registry import register_step
from cnequity.steps.common import walk_day_backfill
from cnequity.steps.http_common import run_incremental_fetched, write_fetched

logger = logging.getLogger(__name__)

Kind = str  # "futures" | "options"


@dataclass(frozen=True)
class ExchangeReader:
    """How to read one exchange, and from which session each kind exists."""

    exchange: str
    first_futures_session: date
    first_options_session: date | None
    fetch_day: Callable[..., ExchangeDay]
    fetch_reference: Callable[..., pl.DataFrame] | None = None

    def first_session(self, kind: Kind) -> date | None:
        return self.first_futures_session if kind == "futures" else self.first_options_session


READERS: dict[str, ExchangeReader] = {
    "SHF": ExchangeReader(
        exchange="SHF",
        first_futures_session=shfe.FIRST_SESSION,
        first_options_session=shfe.FIRST_OPTION_SESSION,
        fetch_day=shfe.fetch_shfe_day,
        fetch_reference=shfe.fetch_shfe_reference,
    ),
    "CZC": ExchangeReader(
        exchange="CZC",
        first_futures_session=czce.FIRST_SESSION,
        first_options_session=czce.FIRST_OPTION_SESSION,
        fetch_day=czce.fetch_czce_day,
        fetch_reference=czce.fetch_czce_reference,
    ),
    "GFE": ExchangeReader(
        exchange="GFE",
        first_futures_session=gfex.FIRST_SESSION,
        first_options_session=gfex.FIRST_OPTION_SESSION,
        fetch_day=gfex.fetch_gfex_day,
        fetch_reference=gfex.fetch_gfex_reference,
    ),
    # DCE's own endpoints answer with an access challenge, so the default route
    # is Sina: futures only, contracts listed from mid-2018 (ADR-0013).
    "DCE": ExchangeReader(
        exchange="DCE",
        first_futures_session=dce_futures.FIRST_SESSION,
        first_options_session=None,
        fetch_day=dce_futures.fetch_dce_day,
    ),
    "CFE": ExchangeReader(
        exchange="CFE",
        first_futures_session=cffex.FIRST_SESSION,
        first_options_session=cffex.FIRST_OPTION_SESSION,
        fetch_day=cffex.fetch_cffex_day,
        fetch_reference=cffex.fetch_cffex_params,
    ),
}

#: `[futures] dce_route = "official"`: DCE's own file, not yet seen answering.
DCE_OFFICIAL = ExchangeReader(
    exchange="DCE",
    first_futures_session=date(2000, 1, 4),
    first_options_session=date(2017, 3, 31),
    fetch_day=dce.fetch_dce_official_day,
)

_BARS_DATASET = {"futures": "futures_bars", "options": "option_bars"}


def reader(config: Config, exchange: str) -> ExchangeReader:
    """The reader for *exchange* under this config's routes."""
    if exchange == "DCE" and getattr(config, "futures_dce_route", "sina") == "official":
        return DCE_OFFICIAL
    return READERS[exchange]


def enabled_exchanges(config: Config) -> list[str]:
    """Configured exchanges, in publication order; empty config means all."""
    wanted = {
        "SHF" if e == "INE" else e
        for e in (getattr(config, "futures_exchanges", None) or SUPPORTED_EXCHANGES)
    }
    if getattr(config, "futures_dce_route", "sina") == "off":
        wanted.discard("DCE")
    return [e for e in SUPPORTED_EXCHANGES if e in wanted and e in READERS]


def expected_exchanges(config: Config, kind: Kind, day: date) -> list[str]:
    """Exchanges that should have a *kind* file for *day*."""
    out = []
    for exchange in enabled_exchanges(config):
        first = reader(config, exchange).first_session(kind)
        if first is not None and day >= first:
            out.append(exchange)
    return out


def earliest_session(config: Config, kind: Kind) -> date | None:
    firsts = [
        reader(config, e).first_session(kind)
        for e in enabled_exchanges(config)
        if reader(config, e).first_session(kind) is not None
    ]
    return min(firsts) if firsts else None


def _missing_finding(dataset: str, day: date, exchange: str, error: str, severity: str) -> dict:
    return {
        "dataset": dataset,
        "severity": severity,
        "check": "futures_exchange_missing",
        "message": (
            f"{dataset}: {exchange} did not publish {day.isoformat()} ({error}); the other "
            "exchanges were written and the reconciliation tail retries this one"
        ),
        "exchange": exchange,
        "trade_date": day.isoformat(),
    }


def _quarantine(
    frame: pl.DataFrame, dataset: str, exchange: str, day: date, findings: list[dict]
) -> pl.DataFrame:
    """Drop rows that cannot be true, keep the rest of the exchange's session.

    One impossible row in a file of hundreds is a fact about that row, not
    about the session; refusing the whole write would trade one bad contract
    for every other exchange's data that day.
    """
    from cnequity.domain.schemas import derivative_bar_violations

    predicate = derivative_bar_violations(dataset)
    bad = frame.filter(predicate)
    if bad.is_empty():
        return frame
    findings.append(
        {
            "dataset": dataset,
            "severity": "warning",
            "check": "futures_row_rejected",
            "message": (
                f"{dataset}: {exchange} {day.isoformat()} had {bad.height} row(s) that break "
                "the bar invariants; they were left out and the rest of the session written"
            ),
            "exchange": exchange,
            "trade_date": day.isoformat(),
            "sample": bad.head(5)
            .select(
                [
                    c
                    for c in ("symbol", "open", "high", "low", "close", "settle")
                    if c in bad.columns
                ]
            )
            .to_dicts(),
        }
    )
    return frame.filter(~predicate)


def fetch_session(
    config: Config,
    day: date,
    kind: Kind,
    *,
    findings: list[dict],
    raise_when_empty: bool = True,
) -> pl.DataFrame:
    """Every expected exchange's *kind* rows for *day*, tolerating stragglers."""
    dataset = _BARS_DATASET[kind]
    exchanges = expected_exchanges(config, kind, day)
    frames: list[pl.DataFrame] = []
    failures: list[tuple[str, str]] = []
    for exchange in exchanges:
        try:
            result = reader(config, exchange).fetch_day(day, config=config)
        except FuturesSourceBlocked as exc:
            failures.append((exchange, f"blocked: {exc}"))
            continue
        except FuturesDayUnavailable as exc:
            failures.append((exchange, f"not published: {exc}"))
            continue
        except Exception as exc:  # noqa: BLE001 — one exchange must not sink the rest
            failures.append((exchange, f"{type(exc).__name__}: {exc}"))
            continue
        frame = result.futures if kind == "futures" else result.options
        if frame.is_empty():
            failures.append((exchange, "file listed no contracts of this kind"))
            continue
        frames.append(_quarantine(frame, dataset, exchange, day, findings))
    for exchange, error in failures:
        logger.warning("%s: %s %s — %s", dataset, exchange, day.isoformat(), error)
        findings.append(
            _missing_finding(dataset, day, exchange, error, "warning" if frames else "error")
        )
    if not frames:
        if exchanges and raise_when_empty:
            raise RuntimeError(
                f"{dataset}: no exchange published {day.isoformat()} "
                f"({'; '.join(f'{e}: {m}' for e, m in failures)})"
            )
        return pl.DataFrame()
    # A reader that is not the exchange itself (Sina for DCE) stamps its own
    # label; the exchanges' files are all `futures_exchange`.
    frames = [
        f if "source" in f.columns else f.with_columns(pl.lit(SOURCE).alias("source"))
        for f in frames
    ]
    return pl.concat(frames, how="diagonal_relaxed")


def _completed_sessions(config: Config, kind: Kind, days: list[date]) -> set[date]:
    """Sessions where every exchange expected that day already has rows."""
    root = config.curated_root / _BARS_DATASET[kind]
    files = list(root.glob("**/*.parquet")) if root.exists() else []
    if not files:
        return set()
    from cnequity.query.parquet_scan import scan_parquet_files

    publisher = {member: key for key in READERS for member in members(key)}
    present: dict[date, set[str]] = {}
    pairs = scan_parquet_files(files).select("trade_date", "exchange").unique().collect()
    for trade_date, exchange in pairs.iter_rows():
        present.setdefault(trade_date, set()).add(publisher.get(exchange, exchange))
    return {d for d in days if set(expected_exchanges(config, kind, d)) <= present.get(d, set())}


def _disabled(dataset: str) -> dict:
    return {
        "rows_read": 0,
        "rows_written": 0,
        "note": f"{dataset} capture disabled ([futures].enabled = false)",
    }


def _with_findings(result: dict, findings: list[dict]) -> dict:
    if not findings:
        return result
    updates = dict(result.get("context_updates") or {})
    updates["audit_findings"] = list(updates.get("audit_findings") or []) + findings
    result["context_updates"] = updates
    if result.get("status") in (None, "success"):
        result["status"] = "degraded"
    return result


def _run_bars(config: Config, trade_date: date, run_id: str, kind: Kind) -> dict:
    dataset = _BARS_DATASET[kind]
    if not is_dataset_enabled(dataset, config):
        return _disabled(dataset)
    findings: list[dict] = []
    if getattr(config, "_backfill", False):
        floor = earliest_session(config, kind)
        if floor is None:
            return {"rows_read": 0, "rows_written": 0, "note": "no enabled exchange lists these"}
        start = getattr(config, "_backfill_start", None)
        if start is not None and start < floor:
            config._backfill_start = floor
        result = walk_day_backfill(
            config,
            trade_date,
            run_id,
            dataset,
            lambda d: fetch_session(config, d, kind, findings=findings, raise_when_empty=False),
            source=SOURCE,
            floor=floor,
            existing_dates_fn=lambda days: _completed_sessions(config, kind, days),
        )
        return _with_findings(result, findings)
    result = run_incremental_fetched(
        config,
        trade_date,
        run_id,
        dataset,
        lambda d: fetch_session(config, d, kind, findings=findings),
        source=SOURCE,
    )
    return _with_findings(result, findings)


@register_step("futures_bars", group="derivatives")
def step_futures_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Per-contract futures daily bars from the exchanges' session files."""
    return _run_bars(config, trade_date, run_id, "futures")


@register_step("option_bars", group="derivatives")
def step_option_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Per-contract option daily bars from the exchanges' session files."""
    return _run_bars(config, trade_date, run_id, "options")


def _scan_bars(config: Config, dataset: str) -> pl.LazyFrame | None:
    root = config.curated_root / dataset
    files = list(root.glob("**/*.parquet")) if root.exists() else []
    if not files:
        return None
    from cnequity.query.parquet_scan import scan_parquet_files

    return scan_parquet_files(files)


def _reference(config: Config, bars: pl.LazyFrame, findings: list[dict], dataset: str):
    """Each exchange's reference file for the latest session it has bars for."""
    publisher = {member: key for key in READERS for member in members(key)}
    latest = (
        bars.with_columns(pl.col("exchange").replace_strict(publisher, default=pl.col("exchange")))
        .group_by("exchange")
        .agg(pl.col("trade_date").max())
        .collect()
    )
    frames: list[pl.DataFrame] = []
    for exchange, day in latest.iter_rows():
        known = READERS.get(exchange)
        if known is None or reader(config, exchange).fetch_reference is None:
            continue
        try:
            frame = reader(config, exchange).fetch_reference(day, config=config)
        except Exception as exc:  # noqa: BLE001 — observed dates still stand
            findings.append(
                {
                    "dataset": dataset,
                    "severity": "warning",
                    "check": "futures_reference_unavailable",
                    "message": (
                        f"{dataset}: {exchange} reference file for {day.isoformat()} "
                        f"unavailable ({exc}); live contracts keep a null last trading day"
                    ),
                }
            )
            continue
        if not frame.is_empty():
            frames.append(frame)
    return pl.concat(frames, how="diagonal_relaxed") if frames else None


def _run_contracts(config: Config, run_id: str, dataset: str) -> dict:
    from cnequity.derive.derivative_contracts import (
        build_futures_contracts,
        build_option_contracts,
    )

    if not is_dataset_enabled(dataset, config):
        return _disabled(dataset)
    kind = "option" if dataset == "option_contracts" else "future"
    bars = _scan_bars(config, "option_bars" if kind == "option" else "futures_bars")
    if bars is None:
        return {"rows_read": 0, "rows_written": 0, "note": "no bars observed yet"}
    findings: list[dict] = []
    reference = _reference(config, bars, findings, dataset)
    if reference is not None:
        reference = reference.filter(pl.col("kind") == kind)
    build = build_option_contracts if kind == "option" else build_futures_contracts
    frame = build(bars, reference)
    if frame.is_empty():
        return _with_findings({"rows_read": 0, "rows_written": 0}, findings)
    result = write_fetched(config, run_id, dataset, frame, source=SOURCE)
    return _with_findings(result, findings)


@register_step("futures_contracts", depends_on=["futures_bars"], group="derivatives")
def step_futures_contracts(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Futures contract table, rebuilt from observed bars and reference files."""
    return _run_contracts(config, run_id, "futures_contracts")


@register_step("option_contracts", depends_on=["option_bars"], group="derivatives")
def step_option_contracts(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """Option contract table, rebuilt from observed bars and reference files."""
    return _run_contracts(config, run_id, "option_contracts")


def minute_scope(config: Config) -> list[str]:
    """Contracts to capture 1m bars for: named ones, then each product's two
    most-held contracts on the latest session the lake has."""
    chosen: list[str] = list(getattr(config, "futures_minute_contracts", []) or [])
    products = list(getattr(config, "futures_minute_products", []) or [])
    if products:
        bars = _scan_bars(config, "futures_bars")
        if bars is not None:
            latest = bars.select(pl.col("trade_date").max()).collect().item()
            day = bars.filter(pl.col("trade_date") == latest).collect()
            for product_symbol in products:
                product, _, exchange = product_symbol.partition(".")
                held = (
                    day.filter((pl.col("product") == product) & (pl.col("exchange") == exchange))
                    .sort("open_interest", descending=True)
                    .head(2)["symbol"]
                    .to_list()
                )
                chosen.extend(held)
    unique = list(dict.fromkeys(chosen))
    return unique[: int(getattr(config, "futures_minute_max_contracts", 100))]


@register_step("futures_minute_bars", group="derivatives")
def step_futures_minute_bars(config: Config, trade_date: date, run_id: str, context: dict) -> dict:
    """The latest window of 1m bars for the configured futures contracts."""
    from datetime import timedelta

    from cnequity.adapters.sina.futures_minute import fetch_minute_bars
    from cnequity.steps.common import list_trading_dates

    dataset = "futures_minute_bars"
    if not is_dataset_enabled(dataset, config):
        return {
            "rows_read": 0,
            "rows_written": 0,
            "note": f"{dataset} capture disabled ([futures].minute_enabled = false)",
        }
    symbols = minute_scope(config)
    if not symbols:
        return {"rows_read": 0, "rows_written": 0, "note": "no contracts in the minute scope"}
    sessions = list_trading_dates(
        config, trade_date - timedelta(days=21), trade_date + timedelta(days=21)
    )
    frame, failures = fetch_minute_bars(symbols, sessions=sessions, config=config)
    findings: list[dict] = []
    if failures:
        findings.append(
            {
                "dataset": dataset,
                "severity": "warning" if not frame.is_empty() else "error",
                "check": "futures_minute_missing",
                "message": (
                    f"{dataset}: {len(failures)} of {len(symbols)} contract(s) returned no bars "
                    f"(e.g. {', '.join(sorted(failures)[:5])}); Sina keeps about two sessions, "
                    "so a contract missed for longer loses those bars for good"
                ),
                "contracts": failures,
            }
        )
    if frame.is_empty():
        if failures and len(failures) == len(symbols):
            raise RuntimeError(f"{dataset}: no contract returned bars ({failures})")
        return _with_findings({"rows_read": 0, "rows_written": 0}, findings)
    frame = _quarantine(frame, dataset, "SINA", trade_date, findings)
    result = write_fetched(config, run_id, dataset, frame, source="sina")
    result["contracts"] = len(symbols)
    return _with_findings(result, findings)
