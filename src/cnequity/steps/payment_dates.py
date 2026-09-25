"""Scoped, source-backed cash payment date enrichment through normal publication."""

from __future__ import annotations

import json
from datetime import date

import polars as pl

from cnequity.adapters.baostock.corporate_actions import fetch_corporate_actions_baostock
from cnequity.domain.symbols import is_etf_symbol, parse_symbol
from cnequity.query.reader import load
from cnequity.steps.http_common import verify_raw_archive, write_fetched


def match_payment_dates(existing: pl.DataFrame, fetched: pl.DataFrame):
    """Match event keys and amounts within TDX Float32 wire precision.

    Preserve the exact stored amount; never replace it with the peer rounding.
    """
    lookup = {(r["symbol"], r["ex_date"], r["action_type"]): r for r in fetched.to_dicts()}
    accepted, unresolved = [], []
    for old in existing.to_dicts():
        key = (old["symbol"], old["ex_date"], old["action_type"])
        new = lookup.get(key)
        reason = None
        if new is None:
            reason = "source_event_missing"
        elif new.get("payment_date") is None:
            reason = "source_payment_date_missing"
        elif new["payment_date"] < old["ex_date"]:
            reason = "payment_before_ex_date"
        elif any(
            abs(float(old.get(c) or 0) - float(new.get(c) or 0))
            > max(1e-8, abs(float(old.get(c) or 0)) * 1e-7)
            for c in ("cash_dividend", "bonus_ratio", "transfer_ratio")
        ):
            reason = "economic_amount_conflict"
        elif old.get("payment_date") not in (None, new["payment_date"]):
            reason = "existing_payment_date_conflict"
        if reason:
            unresolved.append({"symbol": key[0], "ex_date": str(key[1]), "reason": reason})
        elif old.get("payment_date") is None:
            # Keep exact old economics (not rounded replacements), stamp the
            # corroborating source and retain all optional corporate fields.
            accepted.append(
                {
                    **old,
                    "payment_date": new["payment_date"],
                    "payment_source": new["payment_source"],
                    "source": "baostock",
                }
            )
    return pl.DataFrame(accepted, schema_overrides={"payment_date": pl.Date}), unresolved


def repair_payment_dates(config, trade_date: date, run_id: str, context: dict):
    symbols = context.get("_retry_symbols") or getattr(config, "_backfill_symbols", None)
    start, end = getattr(config, "_backfill_start", None), getattr(config, "_backfill_end", None)
    if not symbols or start is None or end is None or start > end:
        raise ValueError("payment repair requires explicit symbols and valid start/end")
    existing = load("corporate_actions", data_root=config.data_root, start=start, end=end)
    existing = existing.filter(
        pl.col("symbol").is_in(symbols)
        & (pl.col("action_type") == "cash_dividend")
        & (pl.col("cash_dividend") > 0)
    )
    if "payment_date" in existing.columns:
        existing = existing.filter(pl.col("payment_date").is_null())
    if existing.is_empty():
        return {"rows_read": 0, "rows_written": 0}
    unsupported = {
        symbol
        for symbol in existing["symbol"].unique().to_list()
        if is_etf_symbol(parse_symbol(symbol).code, parse_symbol(symbol).exchange)
    }
    diagnostics = [
        {
            "symbol": row["symbol"],
            "ex_date": str(row["ex_date"]),
            "reason": "fund_payment_source_required",
        }
        for row in existing.filter(pl.col("symbol").is_in(unsupported)).to_dicts()
    ]
    pending = existing.filter(~pl.col("symbol").is_in(unsupported))

    def remove_keys(frame: pl.DataFrame, keys: list[tuple[str, date]]) -> pl.DataFrame:
        if not keys:
            return frame
        return frame.filter(
            ~pl.struct("symbol", "ex_date").is_in(
                [{"symbol": symbol, "ex_date": day} for symbol, day in keys]
            )
        )

    # Exact primary-source notices are both more authoritative and cheaper
    # than retrying a vendor that already omitted or conflicted on the event.
    # Reviewed exceptional/correction chains run first; the generic CNINFO
    # parser then accepts only PDFs whose code, ex-date, pretax amount and
    # payment date all agree with the stored event.
    from cnequity.adapters.cninfo.payment_notices import repair_cninfo_payment_notices
    from cnequity.adapters.eastmoney.payment_notices import repair_reviewed_notices

    reviewed_keys = repair_reviewed_notices(config, run_id, pending)
    pending = remove_keys(pending, reviewed_keys)
    metrics = {
        "network_requests": 0,
        "replayed_requests": 0,
        "cninfo_network_responses": 0,
    }
    cninfo_keys, cninfo_diagnostics = repair_cninfo_payment_notices(
        config, run_id, pending, metrics=metrics
    )
    diagnostics.extend(cninfo_diagnostics)
    pending = remove_keys(pending, cninfo_keys)

    windows = {
        r["symbol"]: (r["start"], r["end"])
        for r in pending.group_by("symbol")
        .agg(pl.col("ex_date").min().alias("start"), pl.col("ex_date").max().alias("end"))
        .to_dicts()
    }
    scope = f"payment-dates:{start}:{end}"
    years = {
        symbol: set(pending.filter(pl.col("symbol") == symbol)["ex_date"].dt.year().to_list())
        for symbol in windows
    }
    cninfo_only = bool(getattr(config, "_corporate_actions_cninfo_notice_only", False))
    if windows and not cninfo_only and not config.sources.get("baostock", False):
        raise ValueError("unresolved payment events require the baostock source")
    if windows and not cninfo_only:
        fetched, failed = fetch_corporate_actions_baostock(
            sorted(windows),
            start,
            end,
            config=config,
            run_id=run_id,
            symbol_windows=windows,
            request_scope=scope,
            repair_years=years,
            diagnostics=diagnostics,
            metrics=metrics,
        )
        enriched, unresolved = match_payment_dates(pending, fetched)
    else:
        fetched, failed = pending.head(0), []
        enriched = pending.head(0)
        unresolved = (
            [
                {
                    "symbol": row["symbol"],
                    "ex_date": str(row["ex_date"]),
                    "reason": "cninfo_notice_unresolved",
                }
                for row in pending.to_dicts()
            ]
            if cninfo_only
            else []
        )
    issues = {(r["symbol"], r["ex_date"]): r for r in diagnostics}
    unresolved = [issues.get((r["symbol"], r["ex_date"]), r) for r in unresolved]
    report = config.meta_root / "payment_date_repairs" / f"{run_id}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "symbols": sorted(windows),
                "start": str(start),
                "end": str(end),
                "requested_events": existing.height,
                "matched_events": enriched.height + len(reviewed_keys) + len(cninfo_keys),
                "failed_symbols": failed,
                "event_conflicts": diagnostics,
                "acquisition": metrics,
                "unresolved": [*unresolved, *[r for r in diagnostics if r.get("ex_date")]],
                "reviewed_notice_events": len(reviewed_keys),
                "cninfo_notice_events": len(cninfo_keys),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    evidence = (
        verify_raw_archive(
            config, "corporate_actions", run_id, source="baostock", request_scope=scope
        )
        if config.should_archive_raw("corporate_actions") and not enriched.is_empty()
        else None
    )
    if failed:
        # Avoid falsely acknowledging complete publication when a source query failed.
        raise RuntimeError(f"payment date fetch failed for {len(failed)} symbols; see {report}")
    result = {
        "rows_read": fetched.height + len(reviewed_keys) + len(cninfo_keys),
        "rows_written": len(reviewed_keys) + len(cninfo_keys),
    }
    if not enriched.is_empty():
        staged = write_fetched(
            config,
            run_id,
            "corporate_actions",
            enriched,
            source="baostock",
            batch_id=context.get("_batch_id") or "payment-dates",
            raw_archive_evidence=evidence,
        )
        result["rows_read"] += int(staged.get("rows_read", 0))
        result["rows_written"] += int(staged.get("rows_written", 0))
    result["payment_date_report"] = str(report)
    return result
