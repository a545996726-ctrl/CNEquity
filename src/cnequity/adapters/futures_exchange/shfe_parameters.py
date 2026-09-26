"""Read SHFE monthly settlement-parameter XLS as dated source observations.

The workbook's column dates are *settlement* dates. Its notes can describe a
change on the following trading day, and the workbook explicitly excludes
holiday adjustments announced separately. These rows must not be treated as
complete daily, customer-level, or effective-session trading parameters.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from hashlib import sha256

import xlrd

from cnequity.adapters.futures_exchange.common import FuturesPayloadError

_CONTRACT = re.compile(r"[a-z]{1,3}\d{4}")
_HEADING_DATE = re.compile(r"^(\d{1,2})月(\d{1,2})日")
_MARGIN_UNITS = ("一般%", "套保%")
_FEE_UNITS = {
    ("一般‰", "套保‰"): "permille",
    ("一般（元/手）", "套保（元/手）"): "cny_per_contract",
}
_FIELDS = (
    "margin_general_pct",
    "margin_hedge_pct",
    "fee_general_value",
    "fee_hedge_value",
)


def _settlement_date(value: object, report_month: date) -> date:
    match = _HEADING_DATE.match(str(value).strip())
    if match is None:
        raise FuturesPayloadError(f"SHFE parameter date changed: {value!r}")
    month, day = map(int, match.groups())
    previous = 12 if report_month.month == 1 else report_month.month - 1
    if month not in (report_month.month, previous):
        raise FuturesPayloadError(f"SHFE parameter date outside report month: {value!r}")
    year = report_month.year - (month == 12 and report_month.month == 1)
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise FuturesPayloadError(f"SHFE invalid parameter date: {value!r}") from exc


def _number(value: object, *, row: int, column: int | str) -> float:
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise FuturesPayloadError(f"SHFE parameter cell {row}:{column} is not numeric") from exc
    if not 0 <= number < 1000:
        raise FuturesPayloadError(f"SHFE parameter cell {row}:{column} is out of range")
    return number


def parse_monthly_settlement_parameters(
    body: bytes,
    *,
    report_month: date,
    published_on: date,
    source_url: str,
) -> list[dict]:
    """Parse one official XLS without inferring trading-day applicability.

    Values retain the workbook's percent and fee units. ``settlement_date``
    and ``published_on`` are separate so a future PIT importer cannot silently
    use an unpublished adjustment or mistake next-session remarks for same-day
    trading costs. Blank four-cell groups mean that contract has no observation
    for that heading; partial groups or changed units fail closed.
    """
    if report_month.day != 1 or not source_url.startswith("https://www.shfe.com.cn/"):
        raise ValueError("report_month must be month-first and source_url must be official SHFE")
    try:
        workbook = xlrd.open_workbook(file_contents=body)
    except xlrd.XLRDError as exc:
        raise FuturesPayloadError("SHFE settlement-parameter workbook is not readable XLS") from exc
    if workbook.nsheets != 1:
        raise FuturesPayloadError("SHFE settlement-parameter workbook changed sheet count")
    sheet = workbook.sheet_by_index(0)
    raw_sha256 = sha256(body).hexdigest()
    rows: list[dict] = []
    seen: set[tuple[str, date]] = set()
    headings: list[tuple[int, date, str]] = []
    in_block = False
    for index in range(sheet.nrows):
        values = sheet.row_values(index)
        first = str(values[0]).strip() if values else ""
        if "调整日期" in first and "合约" in first:
            if index + 2 >= sheet.nrows:
                raise FuturesPayloadError("SHFE parameter header is truncated")
            units = sheet.row_values(index + 2)
            headings = []
            for column in range(1, min(sheet.ncols, 17), 4):
                label = values[column] if column < len(values) else ""
                group = units[column : column + 4]
                if not str(label).strip():
                    if any(str(item).strip() for item in group):
                        raise FuturesPayloadError("SHFE parameter date is missing above values")
                    continue
                labels = tuple(str(item).strip() for item in group)
                fee_unit = _FEE_UNITS.get(labels[2:])
                if labels[:2] != _MARGIN_UNITS or fee_unit is None:
                    raise FuturesPayloadError("SHFE parameter units changed")
                headings.append((column, _settlement_date(label, report_month), fee_unit))
            if not headings:
                raise FuturesPayloadError("SHFE parameter block has no dated columns")
            in_block = True
            continue
        if first == "备注":
            in_block = False
            continue
        if not in_block:
            continue
        if not _CONTRACT.fullmatch(first):
            if first and any(str(values[column]).strip() for column, _, _ in headings):
                raise FuturesPayloadError(f"SHFE unknown parameter contract row {index + 1}")
            continue
        for column, settled_on, fee_unit in headings:
            group = values[column : column + 4]
            present = [str(item).strip() != "" for item in group]
            if not any(present):
                continue
            if len(group) != 4 or not all(present):
                raise FuturesPayloadError(f"SHFE parameter row {index + 1} has partial values")
            key = (first, settled_on)
            if key in seen:
                raise FuturesPayloadError(f"SHFE duplicate parameter observation: {key}")
            seen.add(key)
            rows.append(
                {
                    "contract_code": first.upper(),
                    "settlement_date": settled_on,
                    "report_month": report_month,
                    "published_on": published_on,
                    "source_url": source_url,
                    "raw_sha256": raw_sha256,
                    "source_row": index + 1,
                    "fee_unit": fee_unit,
                    **{
                        field: _number(value, row=index + 1, column=column + offset)
                        for offset, (field, value) in enumerate(zip(_FIELDS, group, strict=True))
                    },
                }
            )
    if not rows:
        raise FuturesPayloadError("SHFE settlement-parameter workbook has no contract rows")
    return rows


def parse_daily_settlement_parameters(
    body: bytes, *, report_date: date, source_url: str
) -> list[dict]:
    """Read SHFE's post-settlement JSON without assigning intraday validity.

    The source timestamp is retained as published text. It is not proof that
    the numbers were known before that day's trades, nor is this exchange-level
    schedule a customer's broker fee or margin schedule.
    """
    expected_url = (
        f"https://www.shfe.com.cn/data/tradedata/future/dailydata/js{report_date:%Y%m%d}.dat"
    )
    if source_url != expected_url:
        raise ValueError("source_url must match the official SHFE report date")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise FuturesPayloadError("SHFE daily settlement parameters are not JSON") from exc
    if not isinstance(payload, dict) or payload.get("o_code") != "0000":
        raise FuturesPayloadError("SHFE daily settlement-parameter response was not successful")
    if payload.get("report_date") != f"{report_date:%Y%m%d}":
        raise FuturesPayloadError("SHFE daily settlement-parameter report date changed")
    updated = payload.get("update_date")
    try:
        datetime.strptime(updated, "%Y%m%d %H:%M:%S")
    except (TypeError, ValueError) as exc:
        raise FuturesPayloadError("SHFE daily settlement-parameter timestamp changed") from exc
    source_rows = payload.get("o_cursor")
    if not isinstance(source_rows, list) or not source_rows:
        raise FuturesPayloadError("SHFE daily settlement-parameter rows are empty")
    digest = sha256(body).hexdigest()
    seen: set[str] = set()
    rows = []
    for index, source in enumerate(source_rows, start=1):
        if not isinstance(source, dict):
            raise FuturesPayloadError(f"SHFE parameter row {index} is not an object")
        code = source.get("INSTRUMENTID")
        if not isinstance(code, str) or not _CONTRACT.fullmatch(code):
            raise FuturesPayloadError(f"SHFE parameter row {index} has invalid contract")
        if code in seen:
            raise FuturesPayloadError(f"SHFE parameter row {index} repeats {code}")
        seen.add(code)
        product = source.get("PRODUCTID")
        if not isinstance(product, str) or not product.endswith("_f"):
            raise FuturesPayloadError(f"SHFE parameter row {index} has invalid product")
        fields = {}
        for key, name in (
            ("SPECLONGMARGINRATIO", "margin_general_long"),
            ("SPECSHORTMARGINRATIO", "margin_general_short"),
            ("HEDGLONGMARGINRATIO", "margin_hedge_long"),
            ("HEDGSHORTMARGINRATIO", "margin_hedge_short"),
            ("TRADEFEERATIO", "fee_general_permille"),
            ("TTRADEFEERATIO", "fee_hedge_permille"),
            ("TRADEFEEUNIT", "fee_general_cny_per_contract"),
            ("TTRADEFEEUNIT", "fee_hedge_cny_per_contract"),
        ):
            if key not in source:
                raise FuturesPayloadError(f"SHFE parameter row {index} lacks {key}")
            fields[name] = _number(source[key], row=index, column=key)
        if any(fields[key] > 1 for key in fields if key.startswith("margin_")):
            raise FuturesPayloadError(f"SHFE parameter row {index} has invalid margin ratio")
        has_ratio = fields["fee_general_permille"] > 0 or fields["fee_hedge_permille"] > 0
        has_unit = (
            fields["fee_general_cny_per_contract"] > 0 or fields["fee_hedge_cny_per_contract"] > 0
        )
        if has_ratio == has_unit:
            raise FuturesPayloadError(f"SHFE parameter row {index} has ambiguous fee units")
        rows.append(
            {
                "contract_code": code.upper(),
                "product_id": product,
                "settlement_date": report_date,
                "source_updated_at": updated,
                "source_url": source_url,
                "raw_sha256": digest,
                "source_row": index,
                "fee_unit": "permille" if has_ratio else "cny_per_contract",
                **fields,
            }
        )
    return rows
