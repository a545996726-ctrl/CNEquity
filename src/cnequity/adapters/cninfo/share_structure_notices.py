"""Conservative issuer-PDF proof for dated share-capital changes.

This recognises an actual before/change/after table and the registration date.
It deliberately proves only fields printed in the original notice; a vendor's
``free_float_shares`` estimate is not implied by the unrestricted-share row.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_NUMBER = r"\d{1,3}(?:,\d{3})+"
_DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def _after_count(section: str, label: str) -> int | None:
    match = re.search(rf"{label}\s+({_NUMBER})\s+(?:{_NUMBER}|-)\s+({_NUMBER})", section)
    return int(match[2].replace(",", "")) if match else None


def review_share_change_notice(
    text: str,
    *,
    symbol: str,
    change_date: date,
    source_document_id: str,
    source_sha256: str,
    source_published_at: datetime,
    vendor_row: dict,
) -> dict | None:
    """Prove three share counts from one original notice or return no claim."""
    if (
        source_published_at.tzinfo is None
        or not source_document_id
        or len(source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_sha256)
    ):
        raise ValueError("issuer proof requires timezone-aware publication and PDF SHA-256")
    code = symbol.split(".", 1)[0]
    if not re.search(r"(?:股票|证券)代码\s*[：:]\s*" + re.escape(code) + r"\b", text):
        return None
    if "股本变动公告" not in text:
        return None
    # The date must describe *registration of the new shares*, not a later
    # expected unlock or an earlier board vote in the same lengthy PDF.
    registered = re.search(
        r"新增股份已于\s*(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
        r".{0,70}?办理了登记托\s*管手续",
        text,
        flags=re.DOTALL,
    )
    if registered is None:
        return None
    parsed = _DATE.search(registered[1])
    if parsed is None or date(*(int(value) for value in parsed.groups())) != change_date:
        return None
    table = re.search(
        r"本次发行前后公司股本结构变动情况如下表所示[：:]?(.{0,900})",
        text,
        flags=re.DOTALL,
    )
    if table is None:
        return None
    section = table[1]
    restricted = _after_count(section, "有限售条件的流通股合计")
    float_shares = _after_count(section, "无限售条件流通股合计")
    total = _after_count(section, "股份总额")
    if None in (restricted, float_shares, total) or restricted + float_shares != total:
        return None
    fields = {
        "total_shares": total,
        "float_shares": float_shares,
        "restricted_shares": restricted,
    }
    if vendor_row.get("symbol") != symbol or vendor_row.get("change_date") != change_date:
        return None
    if any(vendor_row.get(name) != amount for name, amount in fields.items()):
        return None
    return {
        "symbol": symbol,
        "change_date": change_date.isoformat(),
        "source_document_id": source_document_id,
        "source_sha256": source_sha256,
        "source_published_at": source_published_at.isoformat(),
        "verified_fields": fields,
        "unverified_fields": ["free_float_shares"],
        "evidence_status": "verified_repaired",
    }
