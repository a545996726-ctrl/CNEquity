"""What is already going on, and whether this operation may start.

The answers are advisory. The job process takes the slot and, where the
operation writes, the scheduler lock; the engine takes its own locks again.
A check that passed here can still be rejected a moment later, and that
rejection is the one that counts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cnequity.domain.market_time import shanghai_now, shanghai_today
from cnequity.orchestrator.run_lock import (
    DAILY_INGESTION_LOCK,
    EVENTS_INGESTION_LOCK,
    INIT_JOB_LOCK,
    is_run_locked,
)
from cnequity.orchestrator.run_window import (
    due_session,
    is_done,
    mark_done,
    parse_run_at,
    pending_session,
)
from cnequity.orchestrator.scheduler_lock import lock_directory, scheduler_lock_holder
from cnequity.serve.ops.catalog import OpSpec
from cnequity.serve.ops.records import live_holder, slot_path

ENGINE_TEXT = {
    DAILY_INGESTION_LOCK: "有日更正在运行（各调度组共用一把采集锁）。",
    EVENTS_INGESTION_LOCK: "有事件流正在运行。",
    INIT_JOB_LOCK: "有初始化正在运行。",
}
SCHEDULER_TEXT = {
    "daily": "定时日更或收尾补抓正在运行。",
    "events": "定时事件流正在运行。",
}


def engine_names(lock_class: str) -> tuple[str, ...]:
    if lock_class == "ingest-daily":
        return (DAILY_INGESTION_LOCK, INIT_JOB_LOCK)
    if lock_class == "ingest-events":
        return (EVENTS_INGESTION_LOCK, INIT_JOB_LOCK)
    if lock_class in {"lake-write", "init"}:
        return (DAILY_INGESTION_LOCK, EVENTS_INGESTION_LOCK, INIT_JOB_LOCK)
    return ()


def scheduler_names(lock_class: str) -> tuple[str, ...]:
    if lock_class == "ingest-daily":
        return ("daily",)
    if lock_class == "ingest-events":
        return ("events",)
    if lock_class in {"lake-write", "init"}:
        return ("daily", "events")
    return ()


def engine_blockers(config, lock_class: str) -> list[str]:
    if config is None:
        return []
    return [
        ENGINE_TEXT[name]
        for name in engine_names(lock_class)
        if is_run_locked(config.meta_root, name)
    ]


def scheduler_blockers(config, lock_class: str) -> list[str]:
    if config is None:
        return []
    root = lock_directory(config)
    return [
        SCHEDULER_TEXT[name]
        for name in scheduler_names(lock_class)
        if scheduler_lock_holder(root, name)
    ]


def blockers(config, spec: OpSpec, *, storage_busy: bool, lock_class: str) -> list[str]:
    found: list[str] = []
    from cnequity.file_lock import is_locked

    if is_locked(slot_path(config)):
        holder = live_holder(config)
        if holder:
            title = holder.get("title") or holder.get("op")
            found.append(f"面板正在执行「{title}」。")
        else:
            found.append("面板正在执行另一个任务。")
    found.extend(scheduler_blockers(config, lock_class))
    found.extend(engine_blockers(config, lock_class))
    if storage_busy and lock_class != "read":
        found.append("存储维护正在执行，请等它结束后再启动。")
    return found


def hints(config, spec: OpSpec, params: dict) -> list[str]:
    if config is None:
        return []
    notes: list[str] = []
    if spec.id in {"daily.full", "daily.group", "daily.stale"}:
        target = params.get("trade_date")
        today = shanghai_today().isoformat()
        if target in (None, today) and shanghai_now().time() < parse_run_at(config.daily_run_at):
            if spec.id == "daily.full":
                notes.append(
                    f"还没到今天的日更时间 {config.daily_run_at}（北京时间），"
                    "当天数据可能还没发布。这次不会记成定时任务已跑，到点后定时日更仍会运行。"
                )
            else:
                notes.append(
                    f"还没到今天的日更时间 {config.daily_run_at}（北京时间），当天数据可能还没发布。"
                )
    if getattr(config, "eastmoney_push2_paused", False):
        notes.append("push2 已暂停（配置 push2_paused），相关数据集会降级或缺失。")
    try:
        from cnequity.adapters.eastmoney.host_guard import status

        ledger = status(config)
    except (OSError, ValueError):
        notes.append("没能读来源熔断状态。")
    else:
        for name, section in ledger.items():
            if isinstance(section, dict) and section.get("breaker"):
                notes.append(f"{name} 熔断今天已经触发，相关请求会失败到明天。")
    if spec.id not in {"init.start", "init.resume", "diag.doctor"}:
        pending = incomplete_init(config)
        if pending and not pending["running"]:
            notes.append(
                f"有一次初始化没跑完（{pending['run_id']}）。"
                "从面板继续初始化会续跑它；这次操作仍可进行。"
            )
    if lock_class_waits_for_compact(spec) and is_run_locked(config.meta_root, "compact"):
        notes.append("发布锁正被占用，发布阶段会排队等待。")
    return notes


def lock_class_waits_for_compact(spec: OpSpec) -> bool:
    return spec.lock_class in {"ingest-daily", "ingest-events", "lake-write", "init", "run"}


def incomplete_init(config) -> dict | None:
    if config is None or not Path(config.manifest_path).exists():
        return None
    from cnequity.orchestrator.manifest import Manifest

    # Same coverage question as ``cne status``. A step this init never recorded,
    # but a later run has since completed, is not a reason to offer resume.
    # ``cne init`` itself keeps the strict ledger and can still be resumed.
    row = Manifest(config.manifest_path).latest_incomplete_init_run(discharged_by_later_runs=True)
    if row is None:
        return None
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "running": is_run_locked(config.meta_root, INIT_JOB_LOCK),
    }


def lake_empty(config) -> bool:
    if config is None:
        return True
    curated = Path(config.curated_root)
    if not curated.exists():
        return True
    return next(curated.rglob("*.parquet"), None) is None


def schedule_view(config) -> dict:
    if config is None:
        return {}
    from cnequity.domain.datasets import _is_exchange_session

    now = datetime.now(timezone.utc)
    local = shanghai_now(now)
    today = shanghai_today(now)
    run_at = parse_run_at(config.daily_run_at)
    due = due_session(now, run_at)
    pending = pending_session(config, "daily", now)
    return {
        "daily_run_at": config.daily_run_at,
        "stale_run_at": config.stale_run_at,
        "daily_due": due.isoformat() if due else None,
        "daily_pending": pending.isoformat() if pending else None,
        "daily_done": bool(due) and is_done(config.meta_root, "daily", due, config),
        "today": today.isoformat(),
        "today_is_session": _is_exchange_session(today),
        "before_daily_run_at": local.time() < run_at,
    }


def occupancy(config, *, storage_busy: bool) -> dict:
    holder = live_holder(config)
    engines = []
    schedulers = []
    if config is not None:
        engines = [
            name
            for name in (DAILY_INGESTION_LOCK, EVENTS_INGESTION_LOCK, INIT_JOB_LOCK)
            if is_run_locked(config.meta_root, name)
        ]
        root = lock_directory(config)
        schedulers = [name for name in ("daily", "events") if scheduler_lock_holder(root, name)]
    notes: list[str] = []
    if config is not None and getattr(config, "eastmoney_push2_paused", False):
        notes.append("push2 已暂停。")
    return {
        "slot": (
            {
                "job_id": holder.get("job_id"),
                "op": holder.get("op"),
                "title": holder.get("title"),
                "state": holder.get("state"),
                "command": holder.get("command"),
            }
            if holder
            else None
        ),
        "engine_locks": engines,
        "scheduler_locks": schedulers,
        "storage_busy": storage_busy,
        "incomplete_init": incomplete_init(config),
        "lake_empty": lake_empty(config),
        "schedule": schedule_view(config),
        "hints": notes,
    }


def mark_due_daily_session(
    config, op_id: str, params: dict, *, now: datetime | None = None
) -> bool:
    """Write the scheduler's done marker when a full daily run *is* today's session.

    A single group, a backfill replay and a stale-only pass do not. Neither
    does a full run started before ``run_at``: the timed job should still run
    once the session is actually due. ``pending_session`` is None in that case,
    so this writes nothing.
    """
    if op_id != "daily.full" or params.get("backfill"):
        return False
    moment = now or datetime.now(timezone.utc)
    pending = pending_session(config, "daily", moment)
    if pending is None:
        return False
    target = params.get("trade_date")
    if target is None:
        if shanghai_today(moment) != pending:
            return False
    elif target != pending.isoformat():
        return False
    mark_done(config.meta_root, "daily", pending)
    return True
