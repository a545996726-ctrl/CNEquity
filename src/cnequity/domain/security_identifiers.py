"""Dated security-code continuity backed by issuer announcements.

Security codes are labels, not issuer identities.  Some vendors restate an
issuer's entire price history under its current code while others retain the
code that was live on each date.  Joining those feeds without a dated mapping
duplicates the issuer and can also turn a code change into a fictional sale.

The registry below contains only changes whose effective date and conversion
ratio are established by an archived primary-source announcement.  Adding an
entry therefore requires the same evidence fields; callers must not infer a
change from similar prices or names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import polars as pl


@dataclass(frozen=True)
class SecurityIdentifierChange:
    predecessor: str
    successor: str
    effective_date: date
    share_ratio: float
    announcement_id: str
    announcement_url: str
    source_published_at: datetime
    document_sha256: str


IDENTIFIER_CHANGES: tuple[SecurityIdentifierChange, ...] = (
    SecurityIdentifierChange(
        predecessor="601313.SH",
        successor="601360.SH",
        effective_date=date(2018, 2, 28),
        share_ratio=1.0,
        announcement_id="1204419027",
        announcement_url="https://static.cninfo.com.cn/finalpage/2018-02-13/1204419027.PDF",
        source_published_at=datetime.fromisoformat("2018-02-12T16:00:00+00:00"),
        document_sha256="6850d636e45d61cfd4e97c6d46e2379b8af119816a42331156c78c7bb1029b6b",
    ),
    SecurityIdentifierChange(
        predecessor="000022.SZ",
        successor="001872.SZ",
        effective_date=date(2018, 12, 26),
        share_ratio=1.0,
        announcement_id="1205690369",
        announcement_url="https://static.cninfo.com.cn/finalpage/2018-12-26/1205690369.PDF",
        source_published_at=datetime.fromisoformat("2018-12-25T16:00:00+00:00"),
        document_sha256="bf406b1759dd7fda45cc46927e36316840b0b1152bb40677a7b52e11568d4350",
    ),
    SecurityIdentifierChange(
        predecessor="000043.SZ",
        successor="001914.SZ",
        effective_date=date(2019, 12, 16),
        share_ratio=1.0,
        announcement_id="1207164397",
        announcement_url="https://static.cninfo.com.cn/finalpage/2019-12-16/1207164397.PDF",
        source_published_at=datetime.fromisoformat("2019-12-15T16:00:00+00:00"),
        document_sha256="46160431c51df23c30b3be802eb140492255128fb166d103ed735a62f303ab05",
    ),
)

# The current vendor label 302132.SZ is also attached to 2016–2024 rows.
# CNINFO queries under its issuer org id return contemporaneous notices whose
# issuer PDF explicitly says 300114.SZ.  The two daily-bar series overlap on
# 2,177 sessions with identical OHLC.  This is a historical source restatement,
# not evidence of the later conversion date or share ratio.  Limit the alias
# to the researched period; never manufacture a holding transition from it.
HISTORICAL_VENDOR_RESTATEMENTS: tuple[tuple[str, str, date], ...] = (
    ("302132.SZ", "300114.SZ", date(2024, 12, 31)),
)


def identifier_changes() -> tuple[SecurityIdentifierChange, ...]:
    """Return the immutable, evidence-backed identifier-change registry."""

    return IDENTIFIER_CHANGES


def identifier_aliases(symbol: str) -> frozenset[str]:
    """Return every code in the same registered identifier chain."""

    aliases = {symbol}
    changed = True
    while changed:
        changed = False
        for event in IDENTIFIER_CHANGES:
            pair = {event.predecessor, event.successor}
            if aliases & pair and not pair <= aliases:
                aliases.update(pair)
                changed = True
    return frozenset(aliases)


def canonical_symbol(symbol: str, on_date: date) -> str:
    """Return the exchange code that was effective on *on_date*."""

    current = symbol
    for event in sorted(IDENTIFIER_CHANGES, key=lambda item: item.effective_date):
        if current in (event.predecessor, event.successor):
            current = event.successor if on_date >= event.effective_date else event.predecessor
    return current


def canonicalize_dated_identifiers(
    frame: pl.DataFrame,
    *,
    date_col: str,
    key_columns: list[str] | tuple[str, ...] | None = None,
) -> pl.DataFrame:
    """Put dated rows on their historically effective code and remove twins.

    When both a native historical row and a vendor-restated successor row are
    present for the same business key, the native row wins.  When only the
    restated row exists it is relabelled, preserving coverage without creating
    a second issuer.  The original source/provenance columns remain untouched.
    """

    if frame.is_empty() or "symbol" not in frame.columns or date_col not in frame.columns:
        return frame
    if frame.schema[date_col] != pl.Date:
        raise ValueError(f"{date_col} must be a Date column for identifier continuity")

    out = frame.with_columns(pl.col("symbol").alias("__identifier_original"))
    for event in sorted(IDENTIFIER_CHANGES, key=lambda item: item.effective_date):
        in_chain = pl.col("symbol").is_in([event.predecessor, event.successor])
        out = out.with_columns(
            pl.when(in_chain & (pl.col(date_col) < event.effective_date))
            .then(pl.lit(event.predecessor))
            .when(in_chain & (pl.col(date_col) >= event.effective_date))
            .then(pl.lit(event.successor))
            .otherwise(pl.col("symbol"))
            .alias("symbol")
        )
    for vendor_label, historical_code, last_evidenced_date in HISTORICAL_VENDOR_RESTATEMENTS:
        out = out.with_columns(
            pl.when((pl.col("symbol") == vendor_label) & (pl.col(date_col) <= last_evidenced_date))
            .then(pl.lit(historical_code))
            .otherwise(pl.col("symbol"))
            .alias("symbol")
        )

    keys = list(key_columns or ["symbol", date_col])
    missing = set(keys) - set(out.columns)
    if missing:
        raise ValueError(f"identifier continuity keys are missing: {sorted(missing)}")
    # Native historical identity sorts after a restated alias and therefore
    # survives ``unique(keep='last')``.  Stable input order resolves exact
    # duplicate rows from the same identity in the usual canonical layer.
    out = (
        out.with_columns(
            (pl.col("__identifier_original") == pl.col("symbol"))
            .cast(pl.Int8)
            .alias("__identifier_native")
        )
        .sort([*keys, "__identifier_native"])
        .unique(keys, keep="last", maintain_order=True)
        .drop("__identifier_original", "__identifier_native")
    )
    return out


def transitions_on(on_date: date) -> tuple[SecurityIdentifierChange, ...]:
    """Return identifier changes effective on one date."""

    return tuple(event for event in IDENTIFIER_CHANGES if event.effective_date == on_date)
