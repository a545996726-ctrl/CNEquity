#!/usr/bin/env python3
"""Sync the per-dataset column tables in ``docs/datasets/schema.md`` with the code.

The page is not generated. Its prose carries rationale that no schema can hold
— why ``trading_status`` splits ST from suspension, what the volume unit is —
and regenerating it would throw that away. What it cannot maintain by hand is
the mechanical half: 11 of 42 datasets had no section at all, and a column
added in ``domain/schemas.py`` reached the docs only if someone remembered.

So this syncs rather than generates. Column names, order and types come from
``DATASET_SCHEMAS``; the ``说明`` cell is hand-written and is carried across by
column name. A column that leaves the schema loses its row (and its
description) — that is the point.

    python scripts/sync_schema_docs.py            # rewrite the page
    python scripts/sync_schema_docs.py --check    # fail if it would change (CI)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_PATH = REPO_ROOT / "docs" / "datasets" / "schema.md"

# The page's own vocabulary, not Polars'. Keep the spellings already in use so
# a sync does not rewrite every row the first time it runs.
_TYPE_NAMES = {
    "String": "string",
    "Date": "date",
    "Boolean": "bool",
    "Float64": "float64",
    "Int8": "int8",
    "Int32": "int32",
    "Int64": "int64",
    "Datetime(time_unit='us', time_zone='UTC')": "timestamp",
    "Datetime(time_unit='us', time_zone=None)": "timestamp（naive）",
}

_TABLE_HEADER = "| 列 | 类型 | 说明 |"
_TABLE_DIVIDER = "|--------|------|-------|"
# Datasets that are deliberately documented somewhere else.
_SECTION_ANCHOR = "### Compact 去重"


def _type_name(dtype: object) -> str:
    text = str(dtype)
    return _TYPE_NAMES.get(text, text.lower())


def _schemas() -> dict[str, dict[str, object]]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from cnequity.domain.schemas import DATASET_SCHEMAS

    return DATASET_SCHEMAS


def _heading_datasets(heading: str, datasets: list[str]) -> list[str]:
    """Return every dataset a ``####`` heading documents.

    Headings are not always one dataset (``minute_bars / minute_bars_5m``) and
    not always bare (``stock_news（按需缓存）``).
    """
    return [
        name for name in datasets if re.search(rf"(?<![\w_]){re.escape(name)}(?![\w_])", heading)
    ]


def _split_table(lines: list[str], start: int) -> tuple[int, int, dict[str, str]]:
    """Locate the first table after *start*; return its bounds and descriptions."""
    index = start
    while index < len(lines) and not lines[index].startswith("| 列 "):
        if lines[index].startswith("#### "):
            return -1, -1, {}
        index += 1
    if index >= len(lines):
        return -1, -1, {}
    table_start = index
    index += 2  # header + divider
    descriptions: dict[str, str] = {}
    while index < len(lines) and lines[index].startswith("|"):
        cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        if cells:
            description = cells[2] if len(cells) > 2 else ""
            # One row often stands for several columns that share a note --
            # "open / high / low / close" or "source / data_version /
            # fetched_at". Expanding those to one row per column must carry the
            # note to each of them, or the sync quietly deletes the sentence
            # that explained the group.
            for name in (part.strip() for part in cells[0].split("/")):
                if name and (name not in descriptions or not descriptions[name]):
                    descriptions[name] = description
        index += 1
    return table_start, index, descriptions


def _render_table(schema: dict[str, object], descriptions: dict[str, str]) -> list[str]:
    rows = [_TABLE_HEADER, _TABLE_DIVIDER]
    for column, dtype in schema.items():
        rows.append(f"| {column} | {_type_name(dtype)} | {descriptions.get(column, '')} |")
    return rows


def sync(text: str) -> tuple[str, list[str]]:
    schemas = _schemas()
    datasets = list(schemas)
    lines = text.split("\n")
    notes: list[str] = []
    documented: set[str] = set()

    index = 0
    while index < len(lines):
        if not lines[index].startswith("#### "):
            index += 1
            continue
        heading = lines[index][5:].strip()
        names = _heading_datasets(heading, datasets)
        if not names:
            index += 1
            continue
        documented.update(names)
        table_start, table_end, descriptions = _split_table(lines, index + 1)
        if table_start < 0:
            notes.append(f"{heading}: no column table found, left alone")
            index += 1
            continue
        rendered = _render_table(schemas[names[0]], descriptions)
        if lines[table_start:table_end] != rendered:
            notes.append(f"{heading}: column table synced")
        lines[table_start:table_end] = rendered
        index = table_start + len(rendered)

    missing = [name for name in datasets if name not in documented]
    if missing:
        anchor = next(
            (i for i, line in enumerate(lines) if line.startswith(_SECTION_ANCHOR)),
            len(lines),
        )
        block: list[str] = []
        for name in missing:
            block.append(f"#### {name}")
            block.append("")
            block.extend(_render_table(schemas[name], {}))
            block.append("")
            notes.append(f"{name}: section added")
        lines[anchor:anchor] = block

    return "\n".join(lines), notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the page is out of sync instead of rewriting it",
    )
    args = parser.parse_args()

    original = DOC_PATH.read_text(encoding="utf-8")
    updated, notes = sync(original)

    if updated == original:
        print("schema docs are in sync")
        return 0

    if args.check:
        print("schema docs are OUT OF SYNC with domain/schemas.py:")
        for note in notes:
            print(f"  - {note}")
        print("\nRun: python scripts/sync_schema_docs.py")
        return 1

    DOC_PATH.write_text(updated, encoding="utf-8")
    for note in notes:
        print(f"  - {note}")
    print(f"wrote {DOC_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
