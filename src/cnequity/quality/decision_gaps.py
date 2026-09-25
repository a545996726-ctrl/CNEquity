"""Frozen, source-neutral inventory of historical cash-payment gaps.

This module records what the lake cannot yet prove. A missing payment date is
*unreviewed*, not evidence that the issuer never published one.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path

import polars as pl

from cnequity.query.reader import load
from cnequity.storage.revisions import committed_revision

RESEARCH_START = date(2016, 1, 1)
RESEARCH_END = date(2024, 12, 31)


def check_research_window(start: date, end: date) -> None:
    """Reject a protected range before config, revision, or lake access."""
    if start < RESEARCH_START or end > RESEARCH_END or end < start:
        raise ValueError("decision gap inventory is limited to 2016-01-01..2024-12-31")


def source_route(symbol: str) -> str:
    """Route by code convention only; this does not certify issuer identity."""
    code, _, exchange = symbol.partition(".")
    if len(code) != 6 or not code.isdigit():
        return "identity_unresolved"
    if code.startswith("519"):
        return "identity_unresolved"  # fund-like code, historical identity unproved
    if exchange == "BJ" and code.startswith(("92", "43", "83", "87")):
        return "bse_equity_candidate"
    if exchange == "SH" and code.startswith(("501", "502", "51", "52", "53", "56", "58")):
        return "fund_candidate"
    if exchange == "SZ" and code.startswith(("15", "16")):
        return "fund_candidate"
    if exchange == "SH" and code.startswith(("60", "68")):
        return "sh_sz_equity_candidate"
    if exchange == "SZ" and code.startswith(("00", "30")):
        return "sh_sz_equity_candidate"
    return "identity_unresolved"


def _canonical(payload: object) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()


def _code_sha256() -> str:
    package_root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(package_root.rglob("*.py")):
        digest.update(path.relative_to(package_root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def inventory(
    *,
    data_root: Path,
    start: date,
    end: date,
    revision: tuple[int, str] | None = None,
) -> dict:
    """Read only the explicit historical range of one pinned corporate-action revision."""
    check_research_window(start, end)
    root = Path(data_root)
    pinned = revision or committed_revision(
        root / "curated" / "corporate_actions",
        dataset="corporate_actions",
        meta_root=root / "meta",
    )
    if pinned is None:
        raise ValueError("corporate_actions has no committed revision to pin")
    frame = load("corporate_actions", data_root=root, start=start, end=end, revision=pinned[1])
    missing = frame.filter(
        (pl.col("action_type") == "cash_dividend")
        & (pl.col("cash_dividend") > 0)
        & pl.col("payment_date").is_null()
    )
    events = [
        {
            "symbol": row["symbol"],
            "ex_date": row["ex_date"].isoformat(),
            "action_type": row["action_type"],
            "cash_dividend": row["cash_dividend"],
            "source": row["source"],
            "data_version": row["data_version"],
            "source_route": source_route(row["symbol"]),
            "evidence_status": "unreviewed",
            "source_document_id": None,
            "source_sha256": None,
            "source_published_at": None,
            "effective_at": None,
            "observed_at": None,
            "supersedes_document_id": None,
            "payment_date": None,
        }
        for row in missing.iter_rows(named=True)
    ]
    events.sort(key=lambda row: (row["symbol"], row["ex_date"], row["action_type"]))
    counts: dict[str, int] = {}
    for row in events:
        route = row["source_route"]
        counts[route] = counts.get(route, 0) + 1
    return {
        "schema": "cnequity.decision_payment_gaps.v1",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "dataset": "corporate_actions",
        "revision": pinned[0],
        "revision_id": pinned[1],
        "code_sha256": _code_sha256(),
        "count": len(events),
        "source_route_counts": counts,
        "events": events,
    }


def save_immutable(payload: dict, output_dir: Path, *, prefix: str = "payment-gaps") -> Path:
    """Content-addressed, create-only write; an existing manifest is never replaced."""
    encoded = _canonical(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"{prefix}-{digest}.json"
    fd, tmp_name = tempfile.mkstemp(dir=output_dir, prefix=f".{prefix}-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            if target.read_bytes() != encoded:
                raise ValueError(
                    f"existing gap inventory has different content: {target}"
                ) from None
    finally:
        tmp.unlink(missing_ok=True)
    return target
