"""Completeness and consistency for the futures and option datasets (ADR-0013).

The equity cross-section proof compares a tip against active instruments. That
cannot work here: contracts are not instruments, and a futures universe changes
every month as contracts list and expire. What makes a proof possible is how
the exchanges publish. Every file lists every live contract, traded or not, so
**a contract in the previous session's file that has not reached its last
trading day must be in this one.** That is an expectation derived from the
data alone (ADR-0008 prefers those to peer comparisons), and it is judged per
exchange, because each exchange publishes independently.

The audit side asks the same question over history. Between an exchange's
first and last observed session, every exchange session must have its rows. A
dataset-level dense watermark cannot see a hole in one of several exchanges,
and would pin the whole family at the first historical gap if it could.
"""

from __future__ import annotations

import re
from datetime import date

import polars as pl

from cnequity.config import Config
from cnequity.domain.datasets import is_dataset_enabled

__all__ = [
    "DERIVATIVE_BAR_CONTRACTS",
    "derivative_findings",
    "derivative_tip_scope",
    "exchange_session_gaps",
]

HEADING = "取数截面（期货/期权，按交易所）"

#: Exchanges whose default route is a vendor series that omits no-trade
#: sessions (DCE via Sina); the tip proof cannot hold them to every contract.
VENDOR_ROUTED = frozenset({"DCE"})

#: Bars dataset → the contract table naming each contract's last trading day.
DERIVATIVE_BAR_CONTRACTS = {
    "futures_bars": ("futures_contracts", "last_trade_date"),
    "option_bars": ("option_contracts", "expiry_date"),
}


def _scan(config: Config, dataset: str) -> pl.LazyFrame | None:
    root = config.curated_root / dataset
    files = list(root.glob("**/*.parquet")) if root.exists() else []
    if not files:
        return None
    from cnequity.query.parquet_scan import scan_parquet_files

    return scan_parquet_files(files)


def _last_trade_dates(config: Config, bars_dataset: str) -> dict[str, date]:
    table, column = DERIVATIVE_BAR_CONTRACTS[bars_dataset]
    contracts = _scan(config, table)
    if contracts is None:
        return {}
    frame = (
        contracts.select("symbol", column)
        .filter(pl.col(column).is_not_null())
        .unique(subset=["symbol"], keep="last")
        .collect()
    )
    return dict(frame.iter_rows())


_SERIES = re.compile(r"^[A-Z]+(\d{2})(\d{2})")


def _in_delivery(symbol: str, day: date) -> bool:
    """Whether *symbol*'s delivery (or expiry) month has begun by *day*.

    Used only where no reference file named the last trading day (DCE via
    Sina). A contract inside its delivery month may stop trading on any day of
    it, so the proof does not owe it; one before its delivery month is owed.
    """
    match = _SERIES.match(symbol)
    if match is None:
        return False
    return (2000 + int(match.group(1)), int(match.group(2))) <= (day.year, day.month)


def derivative_tip_scope(config: Config, dataset: str) -> dict | None:
    """Cross-section proof for the tip session of a futures/option bars dataset.

    Same shape as the equity proof in ``cne status`` (complete / incomplete /
    unverified), so both print through one loop.
    """
    if dataset not in DERIVATIVE_BAR_CONTRACTS or not is_dataset_enabled(dataset, config):
        return None
    bars = _scan(config, dataset)
    if bars is None:
        return None
    from cnequity.adapters.futures_exchange import members
    from cnequity.adapters.futures_exchange.registry import expected_exchanges

    kind = "futures" if dataset == "futures_bars" else "options"
    try:
        tip = bars.select(pl.col("trade_date").max()).collect().item()
        if tip is None:
            return None
        previous = (
            bars.filter(pl.col("trade_date") < tip)
            .select(pl.col("trade_date").max())
            .collect()
            .item()
        )
        if previous is None:
            return {
                "dataset": dataset,
                "heading": HEADING,
                "state": "unverified",
                "date": tip,
                "message": f"{dataset} 只有一个交易日，没有前一交易日可以推出应有合约",
            }
        pairs = (
            bars.filter(pl.col("trade_date").is_in([previous, tip]))
            .select("trade_date", "exchange", "symbol")
            .unique()
            .collect()
        )
        ends = _last_trade_dates(config, dataset)
        before = pairs.filter(pl.col("trade_date") == previous)
        observed = set(pairs.filter(pl.col("trade_date") == tip)["symbol"].to_list())
        expected: set[str] = set()
        per_exchange: dict[str, list[int]] = {}
        unverifiable: list[str] = []
        for exchange in expected_exchanges(config, kind, tip):
            if exchange in VENDOR_ROUTED and getattr(config, "futures_dce_route", "sina") == "sina":
                # Sina drops sessions without a trade, so a contract missing
                # from the tip may simply not have traded: not provable.
                unverifiable.append(exchange)
                continue
            rows = before.filter(pl.col("exchange").is_in(list(members(exchange))))
            live = {
                symbol
                for symbol in rows["symbol"].to_list()
                if (ends[symbol] >= tip if symbol in ends else not _in_delivery(symbol, tip))
            }
            expected |= live
            per_exchange[exchange] = [len(live & observed), len(live)]
    except (OSError, ValueError, pl.exceptions.PolarsError) as exc:
        return {
            "dataset": dataset,
            "heading": HEADING,
            "state": "unverified",
            "date": tip if "tip" in locals() else None,
            "message": f"读取衍生品截面证据失败：{exc}",
        }
    if not expected:
        return {
            "dataset": dataset,
            "heading": HEADING,
            "state": "unverified",
            "date": tip,
            "message": f"{previous} 没有任何可证明的交易所合约，无法推出 {tip} 应有的合约",
        }
    missing = sorted(expected - observed)
    covered = len(expected) - len(missing)
    return {
        "dataset": dataset,
        "heading": HEADING,
        "state": "complete" if not missing else "incomplete",
        "date": tip,
        "covered": covered,
        "expected": len(expected),
        "ratio": covered / len(expected),
        "missing": missing,
        "owed": 0,
        "noun": "个在市合约",
        "unverifiable_exchanges": unverifiable,
        "exchanges": per_exchange,
    }


def exchange_session_gaps(config: Config, dataset: str) -> dict[str, list[date]]:
    """Sessions with no rows for an exchange, inside that exchange's own span.

    Sessions the exchange never published (``UNPUBLISHED_FUTURES_SESSIONS``)
    are not gaps: nothing can fill them.
    """
    bars = _scan(config, dataset)
    if bars is None:
        return {}
    from cnequity.domain.derivatives import UNPUBLISHED_FUTURES_SESSIONS
    from cnequity.query.calendar import list_trading_dates

    present = bars.select("exchange", "trade_date").unique().collect()
    gaps: dict[str, list[date]] = {}
    for exchange, group in present.group_by("exchange"):
        name = str(exchange[0] if isinstance(exchange, tuple) else exchange)
        days = set(group["trade_date"].to_list())
        excused = UNPUBLISHED_FUTURES_SESSIONS.get(name, frozenset())
        if dataset != "futures_bars":
            excused = frozenset()
        sessions = list_trading_dates(config, min(days), max(days))
        missing = [d for d in sessions if d not in days and d not in excused]
        if missing:
            gaps[name] = missing
    return gaps


def _unknown_product_findings(config: Config) -> list[dict]:
    findings: list[dict] = []
    for table in ("futures_contracts", "option_contracts"):
        contracts = _scan(config, table)
        if contracts is None:
            continue
        unknown = (
            contracts.filter(pl.col("multiplier").is_null())
            .select("exchange", "product")
            .unique()
            .collect()
        )
        if unknown.is_empty():
            continue
        names = sorted(f"{e}:{p}" for e, p in unknown.iter_rows())
        findings.append(
            {
                "dataset": table,
                "severity": "warning",
                "check": "futures_unknown_product",
                "message": (
                    f"{table}: {len(names)} 个品种没有合约规格（乘数/最小变动价位为空），"
                    f"需要补进 domain/futures_products.py：{', '.join(names[:10])}"
                ),
                "products": names,
            }
        )
    return findings


#: Sessions of recent history the spec checks read; the table describes the
#: current contract rules, so older sessions would test old ones.
SPEC_CHECK_SESSIONS = 20


def _recent(bars: pl.LazyFrame, sessions: int) -> pl.DataFrame:
    days = (
        bars.select(pl.col("trade_date").unique().sort(descending=True).head(sessions))
        .collect()["trade_date"]
        .to_list()
    )
    return bars.filter(pl.col("trade_date").is_in(days) & (pl.col("volume") > 0)).collect()


def spec_findings(config: Config, dataset: str = "futures_bars") -> list[dict]:
    """Traded prices off the tick grid, and lot sizes the turnover contradicts.

    Both specs live in a hand-kept table (``domain/futures_products.py``), so
    both are checked against the data. A price not on the product's tick grid
    means the tick is wrong (or the parse is). ``amount / (volume × settle)``
    is the lot size to within the gap between settlement and the day's average
    price, so a ratio far from the table's multiplier means the multiplier is
    wrong. Futures only for the second: an option's settlement can sit far
    from where it traded.
    """
    from cnequity.domain.derivatives import parse_future_code, parse_option_code
    from cnequity.domain.futures_products import product_spec

    bars = _scan(config, dataset)
    if bars is None:
        return []
    recent = _recent(bars, SPEC_CHECK_SESSIONS)
    if recent.is_empty():
        return []
    kind = "future" if dataset == "futures_bars" else "option"
    parse = parse_future_code if kind == "future" else parse_option_code
    findings: list[dict] = []
    for (exchange, product), group in recent.group_by(["exchange", "product"]):
        sample = group.row(0, named=True)
        contract = parse(sample["exchange_code"], exchange, sample["trade_date"])
        series = contract if kind == "future" else contract.series
        spec = product_spec(exchange, product, kind, delivery=series.delivery_month)
        if spec is None:
            continue
        prices = pl.concat([group[c] for c in ("open", "high", "low", "close")]).drop_nulls()
        off = ((prices / spec.tick_size) - (prices / spec.tick_size).round(0)).abs() > 1e-6
        share = float(off.mean()) if prices.len() else 0.0
        if share > 0.01:
            findings.append(
                {
                    "dataset": dataset,
                    "severity": "warning",
                    "check": "futures_tick_grid",
                    "message": (
                        f"{dataset}: {exchange}:{product} 最近 {SPEC_CHECK_SESSIONS} 个交易日"
                        f"有 {share:.1%} 的成交价不在最小变动价位 {spec.tick_size} 的整数倍上；"
                        "检查 domain/futures_products.py 的 tick"
                    ),
                    "exchange": exchange,
                    "product": product,
                }
            )
        if kind != "future":
            continue
        priced = group.filter(pl.col("amount").is_not_null() & (pl.col("amount") > 0))
        if priced.is_empty():
            continue
        implied = float((priced["amount"] / (priced["volume"] * priced["settle"])).median())
        if abs(implied / spec.multiplier - 1) > 0.15:
            findings.append(
                {
                    "dataset": dataset,
                    "severity": "warning",
                    "check": "futures_multiplier_mismatch",
                    "message": (
                        f"{dataset}: {exchange}:{product} 成交额反推的合约乘数约 {implied:.2f}，"
                        f"规格表写的是 {spec.multiplier:g}；检查 domain/futures_products.py"
                    ),
                    "exchange": exchange,
                    "product": product,
                    "implied_multiplier": implied,
                }
            )
    return findings


def derivative_findings(config: Config) -> list[dict]:
    """Audit findings for the enabled futures/option datasets."""
    findings: list[dict] = []
    for dataset in DERIVATIVE_BAR_CONTRACTS:
        if not is_dataset_enabled(dataset, config):
            continue
        for exchange, missing in sorted(exchange_session_gaps(config, dataset).items()):
            findings.append(
                {
                    "dataset": dataset,
                    "severity": "warning",
                    "check": "futures_exchange_session_gap",
                    "message": (
                        f"{dataset}: {exchange} 在自身区间内缺 {len(missing)} 个交易日"
                        f"（例：{', '.join(d.isoformat() for d in missing[:5])}）；"
                        f"`cne backfill {dataset} --start {missing[0].isoformat()} "
                        f"--end {missing[-1].isoformat()}` 只重取缺口所在的交易日"
                    ),
                    "exchange": exchange,
                    "days_missing": len(missing),
                    "sample_dates": [d.isoformat() for d in missing[:8]],
                }
            )
    for dataset in DERIVATIVE_BAR_CONTRACTS:
        if is_dataset_enabled(dataset, config):
            findings.extend(spec_findings(config, dataset))
    if is_dataset_enabled("futures_contracts", config) or is_dataset_enabled(
        "option_contracts", config
    ):
        findings.extend(_unknown_product_findings(config))
    return findings
