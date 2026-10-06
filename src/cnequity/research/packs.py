"""Closed sets of datasets for a research question.

``cne init`` always builds the same market spine. A pack does not change that
download. It decides which daily groups are worth their source budget afterwards,
and what is still missing before the question can be answered.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cnequity.storage.atomic import write_json_atomic

PACK_ORDER = ("market", "fundamentals", "universe")

SNAPSHOT_NOTE = (
    "交易状态这类快照漏掉当天，下一次按日期的日更补不回这一天；收尾补抓会在当天再试一次。"
    "公告和资讯不在研究包日更里，需要时另跑 `cne run events`。"
)


@dataclass(frozen=True)
class ResearchPack:
    id: str
    title: str
    summary: str
    datasets: tuple[str, ...]
    groups: tuple[str, ...]
    next_command: str
    extra: str = ""


PACKS: dict[str, ResearchPack] = {
    "market": ResearchPack(
        id="market",
        title="行情",
        summary="全市场日线、复权、日历、公司行为和当前交易状态。初始化固定下载这一套。",
        datasets=(
            "instruments",
            "trading_calendar",
            "corporate_actions",
            "daily_bars",
            "index_bars",
            "adj_factors",
            "trading_status",
        ),
        groups=("core",),
        next_command="cne init",
    ),
    "fundamentals": ResearchPack(
        id="fundamentals",
        title="基本面",
        summary="财报、股本和披露日程。不在初始化里下载。",
        datasets=(
            "financial_statement_items",
            "share_structure",
            "earnings_disclosure_schedule",
        ),
        groups=("fundamentals",),
        next_command="cne run daily --pack fundamentals",
        extra=(
            "这个日更组还会带上指数成分、行业成分和股东人数。"
            "估值历史用 `cne backfill valuation_metrics`，不必为此打开整个资金组。"
        ),
    ),
    "universe": ResearchPack(
        id="universe",
        title="股票池",
        summary="历史 ST 证据。当前交易状态和窗口内退市日线已经在行情包里。",
        datasets=(),
        groups=(),
        next_command="cne backfill trading_status",
        extra="这个包不增加日更请求。历史 ST 是单独一次补数。",
    ),
}


def normalize_packs(names: Sequence[str] | None) -> tuple[str, ...]:
    """Return known packs in display order. An empty selection is the market pack."""
    if not names:
        return ("market",)
    unknown = [name for name in names if name not in PACKS]
    if unknown:
        raise ValueError("未知研究包：" + "、".join(unknown))
    selected = set(names)
    return tuple(name for name in PACK_ORDER if name in selected)


def next_command_for_dataset(dataset: str) -> str | None:
    """The command that first builds *dataset*, when a research pack owns it.

    Derived factors stay with ``cne derive``: init publishes them at the end,
    and a lake that already has bars should not be sent back through init.
    """
    from cnequity.domain.datasets import DATASETS

    if dataset in DATASETS and DATASETS[dataset].layer == "derived":
        return None
    for pack in PACKS.values():
        if dataset in pack.datasets:
            return pack.next_command
    return None


def groups_for(names: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    for pack_id in normalize_packs(names):
        for group in PACKS[pack_id].groups:
            if group not in ordered:
                ordered.append(group)
    return ordered


def packs_path(config) -> Path:
    return Path(config.meta_root) / "state" / "research_packs.json"


def load_packs(config) -> tuple[str, ...] | None:
    path = packs_path(config)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        names = payload.get("packs") if isinstance(payload, dict) else None
        if not isinstance(names, list):
            return None
        return normalize_packs([str(name) for name in names])
    except (OSError, ValueError, TypeError):
        return None


def remember_packs(config, names: Sequence[str] | None, *, override: bool) -> tuple[str, ...]:
    """Persist the selection. A later init without ``--pack`` keeps the previous one."""
    existing = load_packs(config)
    chosen = normalize_packs(names) if override or not existing else existing
    path = packs_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {"packs": list(chosen)})
    return chosen


def snapshot_steps(config, group_names: Sequence[str]) -> list[str]:
    from cnequity.domain.datasets import DATASETS, history_mode_for

    found: list[str] = []
    groups = getattr(config, "schedule_groups", {}) or {}
    for name in group_names:
        group = groups.get(name)
        if group is None:
            continue
        for step in group.steps:
            spec = DATASETS.get(step)
            if spec is not None and history_mode_for(spec) != "by_date" and step not in found:
                found.append(step)
    return found


def _catalog(config) -> dict[str, dict]:
    from cnequity.query.reader import list_datasets

    frame = list_datasets(config=config)
    return {row["dataset"]: row for row in frame.iter_rows(named=True)}


def _window(row: dict | None) -> str | None:
    if not row or not row.get("has_data"):
        return None
    start, end = row.get("coverage_start"), row.get("coverage_end")
    if start and end:
        return f"{start} .. {end}"
    return None


def assess(config, names: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """One verdict per pack: window, what is missing, and the next command."""
    chosen = normalize_packs(names if names is not None else load_packs(config))
    try:
        catalog = _catalog(config)
    except (OSError, ValueError):
        catalog = {}
    from cnequity.research.preview import preview_symbols

    preview = preview_symbols(config)
    reports: list[dict[str, Any]] = []
    for pack_id in chosen:
        pack = PACKS[pack_id]
        if pack_id == "universe":
            reports.append(_assess_universe(config, pack, catalog))
            continue
        missing = [name for name in pack.datasets if not (catalog.get(name) or {}).get("has_data")]
        bars = catalog.get("daily_bars")
        window = _window(bars) if pack_id == "market" else None
        if pack_id != "market":
            present = [name for name in pack.datasets if name not in missing]
            if present:
                window = _window(catalog.get(present[0]))
        if pack_id == "market" and "daily_bars" in missing and preview:
            status = "partial"
            detail = (
                f"已有 {len(preview)} 只股票的未复权日线可按代码查询"
                '（`load("daily_bars", symbols=[...])`）。'
                "复权因子要等初始化收尾发布。这还不是全市场。"
            )
            nxt = 'load("daily_bars", symbols=["600519.SH"])'
        elif not missing:
            status = "ready"
            detail = pack.summary
            nxt = ""
        elif len(missing) == len(pack.datasets):
            status = "missing"
            detail = "还没有这些数据：" + "、".join(missing)
            nxt = pack.next_command
        else:
            status = "partial"
            detail = "还缺：" + "、".join(missing)
            nxt = pack.next_command
        if window and pack_id == "market" and status == "ready":
            detail = f"日线窗口 {window}。{detail}"
        elif window and status != "partial":
            detail = f"已有窗口 {window}。{detail}"
        if pack.extra:
            detail = f"{detail} {pack.extra}"
        reports.append(
            {
                "id": pack.id,
                "title": pack.title,
                "status": status,
                "window": window,
                "detail": detail.strip(),
                "next_command": nxt,
            }
        )
    return reports


def _assess_universe(config, pack: ResearchPack, catalog: dict[str, dict]) -> dict[str, Any]:
    if not (catalog.get("instruments") or {}).get("has_data"):
        return {
            "id": pack.id,
            "title": pack.title,
            "status": "missing",
            "window": None,
            "detail": f"{pack.summary} 先要有证券主数据。",
            "next_command": "cne init",
        }
    try:
        from cnequity.quality.st_coverage import st_evidence_coverage_report

        evidence = st_evidence_coverage_report(config)
    except (OSError, ValueError, TypeError) as exc:
        return {
            "id": pack.id,
            "title": pack.title,
            "status": "unknown",
            "window": None,
            "detail": f"历史 ST 证据读不出来（{type(exc).__name__}）。{pack.extra}",
            "next_command": pack.next_command,
        }
    verified = bool(evidence.get("verified"))
    start, end = evidence.get("coverage_start"), evidence.get("coverage_end")
    window = f"{start} .. {end}" if start and end else None
    if verified:
        detail = "历史 ST 证据覆盖当前股票池。"
        if window:
            detail = f"证据窗口 {window}。{detail}"
        return {
            "id": pack.id,
            "title": pack.title,
            "status": "ready",
            "window": window,
            "detail": f"{detail} {pack.extra}".strip(),
            "next_command": "",
        }
    reason = evidence.get("reason") or "没有覆盖整个股票池的历史 ST 收据"
    return {
        "id": pack.id,
        "title": pack.title,
        "status": "missing",
        "window": window,
        "detail": f"历史 ST 尚未覆盖：{reason}。当前交易状态不能代替它。{pack.extra}",
        "next_command": pack.next_command,
    }


def format_readiness(config, names: Sequence[str] | None = None) -> str:
    lines = ["研究包："]
    for row in assess(config, names):
        lines.append(f"- {row['title']}：{row['detail']}")
        if row["next_command"]:
            lines.append(f"  下一步：{row['next_command']}")
    groups = groups_for(names if names is not None else (load_packs(config) or ("market",)))
    if groups:
        snapshots = snapshot_steps(config, groups)
        scheduled = "、".join(groups)
        lines.append(f"定时日更若只跑这些研究包，调度组是 {scheduled}，不含事件流。")
        if snapshots:
            lines.append("其中漏一天就补不回的数据集：" + "、".join(snapshots) + "。")
    lines.append(SNAPSHOT_NOTE)
    return "\n".join(lines)


def readiness_payload(config) -> dict[str, Any]:
    if config is None:
        return {"packs": [], "groups": [], "snapshots": [], "note": "先完成首次配置。"}
    chosen = load_packs(config) or ("market",)
    groups = groups_for(chosen)
    return {
        "packs": assess(config, chosen),
        "saved": list(chosen),
        "groups": groups,
        "snapshots": snapshot_steps(config, groups),
        "note": SNAPSHOT_NOTE,
    }


_STATUS_CODE = {"ready": 0, "partial": 1, "missing": 1, "unknown": 2}


def readiness_exit_code(rows: Sequence[dict[str, Any]]) -> int:
    codes = [_STATUS_CODE.get(str(row.get("status")), 2) for row in rows]
    return 1 if 1 in codes else max(codes, default=0)
