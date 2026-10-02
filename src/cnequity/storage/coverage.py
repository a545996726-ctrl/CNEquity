"""Coverage evidence derived from the exact candidate or committed bytes."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from cnequity.domain.datasets import DATASETS
from cnequity.query.parquet_scan import dataset_has_parquet, scan_parquet_root


def resolved_outstanding_keys(
    dataset: str, root: Path, owed: list[dict], *, committed: bool = True
) -> set[tuple[str, str]]:
    """Return only owed symbol/date identities actually present in this view."""
    if not owed or not dataset_has_parquet(root, committed=committed):
        return set()
    column = DATASETS[dataset].partition_col
    if not column or any(not row.get("symbol") or not row.get("trade_date") for row in owed):
        raise ValueError(f"invalid outstanding scope for {dataset}")
    symbols = sorted({row["symbol"] for row in owed})
    days = sorted({row["trade_date"] for row in owed})
    present = set(
        scan_parquet_root(root, partition_col=column, symbols=symbols, committed=committed)
        .select("symbol", pl.col(column).cast(pl.String).alias("_date"))
        .filter(pl.col("_date").is_between(pl.lit(days[0]), pl.lit(days[-1])))
        .unique()
        .collect()
        .iter_rows()
    )
    return present & {(row["symbol"], row["trade_date"]) for row in owed}


def candidate_gaps(
    payload: dict, run_id: str, resolved: set[tuple[str, str]], *, full_staging: bool
) -> dict:
    """Describe post-publication gaps without mutating the current index."""
    days = set(payload.get("staged_request_days", {}).get(run_id, [])) if full_staging else set()
    units = set(payload.get("staged_units", {}).get(run_id, [])) if full_staging else set()
    return {
        "missing_ranges": [r for r in payload.get("missing_ranges", []) if r["start"] not in days],
        "missing_units": {
            key: value
            for key, value in payload.get("missing_units", {}).items()
            if key not in units
        },
        "outstanding_keys": [
            r
            for r in payload.get("outstanding_keys", [])
            if (r["symbol"], r["trade_date"]) not in resolved
        ],
    }
