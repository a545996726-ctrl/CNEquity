"""Read the delisted catalogue with `delisted status`.

Rebuilding the catalogue is a one-off project in `scripts/delisted_ops.py`.
Reading it stays here; history fetching uses `backfill daily_bars`.
"""

from __future__ import annotations

import json

import click

from cnequity.cli._root import cli, moved_hints
from cnequity.cli._shared import (
    _cfg,
    config_option,
    parse_date_option,
)

# Exit 0 even when sources are down. A red source is this command's *output*,
# not its failure.


@cli.group(
    "delisted",
    cls=moved_hints({"backfill": "cne backfill daily_bars --profile delisted --start <date>"}),
)
def delisted_grp():
    """读取退市名录；历史行情通过 backfill daily_bars 补数。

    \b
    重建名录本身 —— 代码空间扫描、终态对账、instruments 修复和覆盖门禁 ——
    是一次性的工程而不是日常运维，放在 `scripts/delisted_ops.py` 里。
    """


@delisted_grp.command("status")
@config_option
@click.option("--since", default="2016-01-01", show_default=True, help="湖窗口起点。")
@click.option("--sample", default=15, show_default=True, help="打印多少行明细。")
def delisted_status(config_path: str, since: str, sample: int):
    """汇总名录：有多少、从什么时候起、还有多少待探测。"""
    from collections import Counter

    from cnequity.steps.delisted import (
        LIVE_RECENCY_DAYS,
        classify_catalog,
        delisted_symbols_in_window,
        pending_codes,
    )

    cfg = _cfg(config_path)
    start = parse_date_option(since, "--since")
    catalog, live_missing = classify_catalog(cfg)
    in_window = {s: d for s, d in catalog.items() if d >= start}
    by_year = Counter(d.year for d in in_window.values())
    by_board = Counter(s.split(".")[1] for s in in_window)
    recent = sorted(in_window.items(), key=lambda kv: kv[1], reverse=True)[:sample]
    click.echo(
        json.dumps(
            {
                "delisted": len(catalog),
                "in_window": len(in_window),
                # Still quoting near the latest session: either a code the
                # instrument list is missing, or a delisting inside the recency
                # window that will reclassify on the next sweep.
                "live_or_recent": len(live_missing),
                "live_or_recent_by_exchange": dict(
                    sorted(Counter(s.split(".")[1] for s in live_missing).items())
                ),
                "live_recency_days": LIVE_RECENCY_DAYS,
                "window_start": since,
                "pending_probe": len(pending_codes(cfg)),
                "not_yet_ingested": len(delisted_symbols_in_window(cfg, start)),
                "by_year": dict(sorted(by_year.items())),
                "by_exchange": dict(sorted(by_board.items())),
                "most_recent": [{"symbol": s, "last_traded": d.isoformat()} for s, d in recent],
            },
            indent=2,
        )
    )
