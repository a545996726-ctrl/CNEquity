"""Preview and apply current-user scheduling, with persistent fail-closed state."""

from __future__ import annotations

import copy
import os
import re
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from cnequity.config import load_config
from cnequity.config.upgrade import _table_span, tomllib
from cnequity.file_lock import LockUnavailable, exclusive_lock
from cnequity.orchestrator.scheduler_lock import lock_directory
from cnequity.serve.ops.catalog import OpsError, config_file
from cnequity.serve.ops.records import atomic_write, read_record
from cnequity.serve.ops.scheduler_backend import SchedulerBackend
from cnequity.storage.atomic import replace_with_retry


def state_dir(config) -> Path:
    return Path(config.meta_root) / "state" / "web_scheduler"


def settings(config) -> dict:
    path = state_dir(config) / "settings.json"
    if not path.exists():
        return {"daily": False, "stale": False}
    value = read_record(path)
    if not isinstance(value, dict) or any(
        key in value and not isinstance(value[key], bool)
        for key in ("daily", "stale", "backup", "events")
    ):
        raise OpsError("定时任务设置无效，请核对 settings.json。")
    if value.get("config_path") != str(config_file(config)):
        raise OpsError("这个数据湖的 Web 调度绑定了另一份配置，请使用原配置管理。")
    interval = value.get("events_interval_minutes", 60)
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 1440:
        raise OpsError("事件流运行间隔无效，请重新设置。")
    if value.get("backup") and (
        not isinstance(value.get("backup_run_at"), str)
        or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value["backup_run_at"])
        or not isinstance(value.get("backup_root"), str)
        or not Path(value["backup_root"]).is_absolute()
        or not isinstance(value.get("backup_datasets"), list)
        or not value["backup_datasets"]
        or not all(isinstance(item, str) for item in value["backup_datasets"])
    ):
        raise OpsError("自动备份设置无效，请重新设置。")
    if value.get("events") and not isinstance(value.get("events_group"), str):
        raise OpsError("定时事件组无效，请重新设置。")
    packs = value.get("daily_packs", [])
    if not isinstance(packs, list) or not all(isinstance(item, str) for item in packs):
        raise OpsError("研究包设置无效，请重新设置。")
    if packs:
        from cnequity.research.packs import normalize_packs

        try:
            normalize_packs(packs)
        except ValueError as exc:
            raise OpsError(str(exc)) from exc
    return value


def _settings_bytes(config) -> bytes:
    path = state_dir(config) / "settings.json"
    return path.read_bytes() if path.exists() else b""


def edit_times(text: str, daily: str, stale: str) -> str:
    """Preserve comments/other values and verify the complete TOML semantic diff."""
    for value in (daily, stale):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise OpsError("运行时间必须为北京时间 HH:MM。")
    original = tomllib.loads(text)
    expected = copy.deepcopy(original)
    for name, value in (("daily", daily), ("stale", stale)):
        expected.setdefault("job", {}).setdefault(name, {})["run_at"] = value
        lines = text.splitlines(keepends=True)
        span = _table_span(lines, f"job.{name}")
        if span is None:
            text = text.rstrip() + f'\n\n[job.{name}]\nrun_at = "{value}"\n'
            continue
        start, end = span
        for index in range(start + 1, end):
            match = re.match(
                r"""^(\s*(?:run_at|"run_at"|'run_at')\s*=\s*)(["'])([^"']*)\2(\s*(?:#.*)?)(\r?\n)?$""",
                lines[index],
            )
            if match:
                lines[index] = f'{match[1]}"{value}"{match[4]}{match[5] or chr(10)}'
                break
        else:
            lines.insert(start + 1, f'run_at = "{value}"\n')
        text = "".join(lines)
    try:
        parsed = tomllib.loads(text)
    except ValueError as exc:
        raise OpsError(
            "无法自动修改这份配置的时间，请将 run_at 放在 [job.daily] 和 [job.stale] 表内。"
        ) from exc
    if parsed != expected:
        raise OpsError("配置结构不支持自动修改时间，请使用标准 TOML 表。")
    return text


def replace_config(path: Path, text: str) -> None:
    # Keep the existing file's permissions, including private credentials.
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".cne-schedule-")
    tmp = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        tmp.chmod(path.stat().st_mode)
        replace_with_retry(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class ScheduleService:
    def __init__(self, config):
        path = config_file(config)
        if path is None:
            raise OpsError("先生成配置，再设置定时任务。")
        self.config = config
        self.path = path
        saved = settings(config)
        self.backend = SchedulerBackend(
            path,
            Path(config.meta_root),
            working_directory=saved.get("working_directory"),
            scheduler_lock_dir=str(lock_directory(config)),
        )
        self.previews: dict[str, dict] = {}

    def _current(self):
        config = load_config(self.path)
        if Path(config.meta_root) != Path(self.config.meta_root):
            raise OpsError("配置的数据目录已经变化，请重新启动 serve 后设置定时任务。")
        return config

    def home(self) -> dict:
        from cnequity.serve.ops.backups import stored_datasets

        # Show what the timer will actually load, including external edits.
        config = self._current()
        state = settings(config)
        native = self.backend.observe()
        heartbeat = state_dir(config) / "last_tick.json"
        try:
            last_tick = read_record(heartbeat) if heartbeat.exists() else None
        except (OSError, ValueError):
            last_tick = None
        active = bool(native["active"] and native["matches"])
        enabled = active and any(state.get(key) for key in ("daily", "stale", "backup", "events"))
        timer_health = "paused"
        if enabled:
            stamp = (last_tick or {}).get("at") or state.get("updated_at")
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
            except (TypeError, ValueError):
                age = float("inf")
            timer_health = "stale" if age > 20 * 60 else (last_tick or {}).get("status", "not_seen")
        return {
            "backend": self.backend.kind,
            "native": native,
            "daily": bool(state.get("daily")),
            "stale": bool(state.get("stale")),
            "daily_run_at": config.daily_run_at,
            "stale_run_at": config.stale_run_at,
            "backup": bool(state.get("backup")),
            "backup_run_at": state.get("backup_run_at", "23:00"),
            "backup_datasets": state.get("backup_datasets", []),
            "backup_choices": stored_datasets(config),
            "backup_root": state.get("backup_root") or str(Path(config.meta_root) / "snapshots"),
            "events": bool(state.get("events")),
            "events_group": state.get("events_group"),
            "events_interval_minutes": state.get("events_interval_minutes", 60),
            "events_groups": sorted(config.events_groups),
            "daily_packs": list(state.get("daily_packs") or []),
            "enabled": enabled,
            "timer_health": timer_health,
            "last_tick": last_tick,
            "warning": state.get("warning"),
            "note": (
                "每分钟检查一次。日更与补抓每个交易日各尝试一次；补抓只处理快照，并等待日更已执行。"
                "备份每个自然日到点后尝试一次，事件流按独立频率运行，含周末和节假日。任务占用时等待，下次检查再判断。"
                "关闭网页或 serve 后继续生效。Windows 和 macOS 需要当前用户已登录；Linux 需要 cron 服务运行。"
                "休眠期间不执行，恢复后再判断；日更与补抓最迟到下个交易日 09:15，备份只补当天，事件流不累积补跑。"
                "这里只管理 Web 创建的任务，已有的脚本和其他系统任务请先核对，避免重复调度。"
            ),
        }

    def preview(
        self,
        *,
        daily: bool,
        stale: bool,
        daily_run_at: str,
        stale_run_at: str,
        backup: bool = False,
        backup_run_at: str = "23:00",
        backup_datasets: list[str] | None = None,
        backup_root: str | None = None,
        events: bool = False,
        events_group: str | None = None,
        events_interval_minutes: int = 60,
        daily_packs: list[str] | None = None,
    ) -> dict:
        if stale and not daily:
            raise OpsError("启用收尾补抓需要同时启用日更。")
        native = self.backend.observe()
        from cnequity.serve.ops.catalog import normalize, spec_for

        config = self._current()
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", backup_run_at):
            raise OpsError("备份时间必须为北京时间 HH:MM。")
        if (
            isinstance(events_interval_minutes, bool)
            or not isinstance(events_interval_minutes, int)
            or not 1 <= events_interval_minutes <= 1440
        ):
            raise OpsError("事件流间隔必须为 1–1440 分钟。")
        if events and events_group not in config.events_groups:
            raise OpsError("请选择有效的事件组。")
        datasets = sorted(set(backup_datasets or []))
        backup_root = backup_root or str(Path(config.meta_root) / "snapshots")
        if backup:
            normalize(
                spec_for("snapshot.create"),
                {"name": "timer-preview", "datasets": datasets, "snapshot_root": backup_root},
                config,
            )
        elif backup_root:
            # Validate a saved directory even if automatic backup is disabled.
            from cnequity.storage.snapshots import _reject_symlink_path

            root = Path(backup_root).expanduser()
            if not root.is_absolute():
                raise OpsError("备份目录必须是绝对路径。")
            _reject_symlink_path(root, label="snapshot root")
            if root.exists() and not root.is_dir():
                raise OpsError("备份目录不是目录。")
        packs = list(daily_packs or [])
        if packs:
            from cnequity.research.packs import normalize_packs

            try:
                packs = list(normalize_packs(packs))
            except ValueError as exc:
                raise OpsError(str(exc)) from exc
        options = {
            "backup": backup,
            "backup_run_at": backup_run_at,
            "backup_datasets": datasets,
            "backup_root": str(Path(backup_root).expanduser())
            if backup_root
            else str(Path(config.meta_root) / "snapshots"),
            "events": events,
            "events_group": events_group,
            "events_interval_minutes": events_interval_minutes,
            "daily_packs": packs,
        }
        enabled = daily or stale or backup or events
        if enabled and native["error"]:
            raise OpsError(native["error"])
        if enabled and native["installed"] and not native.get("owned", native["matches"]):
            raise OpsError("同名系统任务已被修改，请先在系统调度器中核对。")
        raw = self.path.read_bytes()
        text = edit_times(raw.decode("utf-8"), daily_run_at, stale_run_at)
        artifact = self.backend.render() if enabled else ""
        # Binding is checked even on a paused schedule.
        settings(self._current())
        token = secrets.token_urlsafe(32)
        self.previews = {
            key: value
            for key, value in self.previews.items()
            if value["expires"] > time.monotonic()
        }
        if len(self.previews) >= 16:
            self.previews.pop(next(iter(self.previews)))
        self.previews[token] = {
            "raw": raw,
            "text": text,
            "artifact": artifact,
            "native": native["fingerprint"],
            "state": _settings_bytes(self.config),
            "daily": daily,
            "stale": stale,
            "options": options,
            "enabled": enabled,
            "expires": time.monotonic() + 600,
        }
        return {
            "token": token,
            "backend": self.backend.kind,
            "action": "启用 / 更新" if enabled else "暂停",
            "daily": daily,
            "stale": stale,
            "daily_run_at": daily_run_at,
            "stale_run_at": stale_run_at,
            **options,
            "artifact": artifact,
            "confirmation": (
                "系统将按所选范围自动取数或复制备份（备份持续占用磁盘，不自动删除）；关闭 Web 服务后仍会运行。"
                + (
                    " 日更只跑研究包 "
                    + "、".join(options["daily_packs"])
                    + " 的调度组，不含事件流。快照漏一天无法按日期补回。"
                    if options.get("daily_packs")
                    else ""
                )
            )
            if enabled
            else "停止后续定时触发，已在运行的任务继续执行。",
        }

    def apply(self, token: str, *, acknowledged: bool, requested_by: str) -> dict:
        if not acknowledged:
            raise OpsError("请先确认定时任务的执行范围。")
        try:
            with exclusive_lock(state_dir(self.config) / "control.lock", blocking=False):
                plan = self.previews.pop(token, None)
                if plan is None or plan["expires"] <= time.monotonic():
                    raise OpsError("定时设置预览已失效，请重新预览。")
                if (
                    self.path.read_bytes() != plan["raw"]
                    or self.backend.observe()["fingerprint"] != plan["native"]
                    or _settings_bytes(self.config) != plan["state"]
                ):
                    raise OpsError("配置或系统任务已经变化，请重新预览。")
                state = {
                    "schema_version": 1,
                    "config_path": str(self.path),
                    "daily": False,
                    "stale": False,
                    "requested_by": requested_by,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "working_directory": self.backend.working_directory,
                    **plan["options"],
                    "backup": False,
                    "events": False,
                }
                destination = state_dir(self.config) / "settings.json"
                # Fail closed before unregistering/reloading a timer. A failed
                # OS command must never leave background ingestion enabled.
                atomic_write(destination, state)
                if plan["text"].encode() != plan["raw"]:
                    backup = self.path.with_name(self.path.name + ".schedule.bak")
                    try:
                        descriptor = os.open(
                            backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, self.path.stat().st_mode
                        )
                    except FileExistsError:
                        pass
                    else:
                        with os.fdopen(descriptor, "wb") as handle:
                            handle.write(plan["raw"])
                    replace_config(self.path, plan["text"])
                try:
                    self.backend.apply(plan["enabled"], plan["artifact"])
                    native = self.backend.observe()
                    if plan["enabled"] and not (native["active"] and native["matches"]):
                        raise OpsError("系统任务注册后没有确认到活动状态。")
                except OpsError as exc:
                    if plan["enabled"]:
                        raise OpsError(f"定时执行保持暂停；时间设置已保存。{exc}") from exc
                    # The worker checks this state even if the OS task cannot
                    # be removed (permission change, offline scheduler, drift).
                    state["warning"] = f"自动取数已暂停，但系统触发未移除：{exc}"
                state.update(daily=plan["daily"], stale=plan["stale"], **plan["options"])
                atomic_write(destination, state)
        except LockUnavailable as exc:
            raise OpsError("另一个页面正在修改定时设置，请稍后重试。") from exc
        return self.home()
