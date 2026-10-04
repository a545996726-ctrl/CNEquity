"""Whitelisted dashboard operations.

Each operation is one Click command plus the flags the page is allowed to set.
``--config`` is never taken from the request. The alignment test walks this
table against Click, so a renamed flag fails there instead of 404ing a button.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from cnequity.domain.market_time import shanghai_today

# Targets ``cne derive`` accepts that the page may run. The command's other
# names rewrite data only with ``--apply`` or have no schedule at all; those
# stay on the CLI. The test compares this tuple with ``derive``'s branches.
DERIVE_NAMES = (
    "adj_factors",
    "industry_index",
    "trading_status",
    "futures_continuous",
    "option_greeks",
)

# Click choices the page deliberately does not offer.
EXCLUDED_CHOICES: dict[tuple[str, str], frozenset[str]] = {
    ("init.start", "profile"): frozenset({"demo", "sample"}),
}

WRITE_LOCKS = frozenset({"ingest-daily", "ingest-events", "lake-write", "init"})


class OpsError(ValueError):
    """The page can show this and must not start a process."""


class UnknownOp(OpsError):
    """No such operation id."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # bool, date, choice, multi, symbols, text, run_id
    label: str
    flag: str | None = None
    help: str = ""
    required: bool = False
    choices: tuple[str, ...] = ()
    choice_source: str | None = None
    position: int = 0
    empty_reason: str = ""
    repeat: bool = False


@dataclass(frozen=True)
class OpSpec:
    id: str
    title: str
    summary: str
    group: str
    command: tuple[str, ...]
    params: tuple[Param, ...] = ()
    fixed: tuple[str, ...] = ()
    lock_class: str = "read"  # or "run" — decided from the target run
    confirm: str = "standard"  # none, standard, heavy
    result: str = "run_json"  # run_json, report, gate
    available_in: str = "normal"  # normal, setup, both
    needs: str | None = None
    needs_reason: str = ""
    heavy_text: str = ""


STANDARD_ACK = {"id": "confirm", "text": "我确认现在启动这一次操作。"}


def _ops() -> tuple[OpSpec, ...]:
    trade_date = Param("trade_date", "date", "交易日", flag="--trade-date", help="默认今天。")
    backfill = Param(
        "backfill", "bool", "按补跑语义", flag="--backfill", help="补跑某一天，不走增量窗口。"
    )
    return (
        OpSpec(
            id="snapshot.create",
            title="创建数据备份",
            group="备份与恢复",
            summary="把明确选择的数据集复制成可校验的湖快照，不请求数据源。",
            command=("snapshot", "create"),
            lock_class="lake-write",
            result="report",
            params=(
                Param("name", "snapshot_name", "新备份名称", required=True),
                Param(
                    "snapshot_root",
                    "directory",
                    "备份存放目录（可选，绝对路径）",
                    flag="--snapshot-root",
                ),
                Param(
                    "datasets",
                    "multi",
                    "备份数据集",
                    flag="--dataset",
                    required=True,
                    choice_source="stored_datasets",
                    repeat=True,
                    empty_reason="还没有已发布的数据。",
                ),
            ),
        ),
        OpSpec(
            id="snapshot.verify",
            title="校验数据备份",
            group="备份与恢复",
            summary="重新计算备份文件摘要，检查缺失、损坏与清单一致性。",
            command=("snapshot", "verify"),
            result="gate",
            params=(
                Param("name", "snapshot_name", "备份名称", required=True),
                Param(
                    "snapshot_root",
                    "directory",
                    "备份存放目录（可选，绝对路径）",
                    flag="--snapshot-root",
                ),
            ),
        ),
        OpSpec(
            id="snapshot.restore",
            title="恢复数据备份",
            group="备份与恢复",
            summary="校验后恢复到新的或空目录，完成后可检查恢复结果。",
            command=("snapshot", "restore"),
            lock_class="lake-write",
            result="report",
            confirm="heavy",
            heavy_text="我确认恢复目标在当前数据湖之外，并了解仅恢复所选备份的内容。",
            params=(
                Param("name", "snapshot_name", "备份名称", required=True),
                Param(
                    "snapshot_root",
                    "directory",
                    "备份存放目录（可选，绝对路径）",
                    flag="--snapshot-root",
                ),
                Param("target", "path", "恢复目录（绝对路径）", required=True, position=1),
            ),
        ),
        OpSpec(
            id="daily.full",
            title="跑一天的更新",
            summary="按配置顺序跑全部日更调度组，再跑事件流。某组失败也继续。",
            group="日更",
            command=("run", "daily"),
            params=(
                trade_date,
                backfill,
                Param("no_events", "bool", "不跑事件流", flag="--no-events"),
                Param(
                    "pack",
                    "multi",
                    "研究包",
                    flag="--pack",
                    repeat=True,
                    choices=("market", "fundamentals", "universe"),
                    help="不选则跑全部调度组和事件流。选择后只跑对应调度组。",
                ),
            ),
            lock_class="ingest-daily",
            result="run_json",
        ),
        OpSpec(
            id="daily.group",
            title="跑一个调度组",
            summary="只跑选定的日更调度组。不写“今天已跑过”的标记，定时全流程仍会执行。",
            group="日更",
            command=("run", "daily"),
            params=(
                Param(
                    "group",
                    "choice",
                    "调度组",
                    flag="--group",
                    required=True,
                    choice_source="schedule_groups",
                    empty_reason="这份配置没有日更调度组。",
                ),
                trade_date,
                backfill,
            ),
            lock_class="ingest-daily",
            result="run_json",
        ),
        OpSpec(
            id="daily.stale",
            title="补抓落后的数据集",
            summary="只重抓仍然落后于最后交易日的数据集。可限定调度组，或只重抓快照。",
            group="补抓与恢复",
            command=("run", "daily"),
            fixed=("--stale-only",),
            params=(
                Param("snapshots_only", "bool", "只重抓快照", flag="--snapshots-only"),
                Param(
                    "groups",
                    "multi",
                    "限定调度组",
                    flag="--groups",
                    choice_source="schedule_groups",
                    help="不选就是全部已启用的组。",
                ),
            ),
            lock_class="ingest-daily",
            result="run_json",
        ),
        OpSpec(
            id="events.run",
            title="跑事件流",
            summary="公告、监管事件和资讯。周末和节假日也会跑。",
            group="日更",
            command=("run", "events"),
            params=(
                Param(
                    "group",
                    "choice",
                    "事件组",
                    flag="--group",
                    choice_source="events_groups",
                    help="不选就按配置顺序跑全部。",
                ),
                Param(
                    "trade_date",
                    "date",
                    "自然日",
                    flag="--trade-date",
                    help="默认今天，含周末与节假日。",
                ),
            ),
            lock_class="ingest-events",
            needs="events_groups",
            needs_reason="这份配置没有事件流调度组。",
            result="run_json",
        ),
        OpSpec(
            id="run.retry",
            title="重试一次 run",
            summary="重试指定 run 里失败的批次。初始化 run 会按初始化续跑。",
            group="补抓与恢复",
            command=("run", "retry"),
            params=(Param("run_id", "run_id", "run", flag="--run-id", required=True),),
            lock_class="run",
            result="run_json",
        ),
        OpSpec(
            id="run.retry_failed_groups",
            title="重试失败的日更组",
            summary="每个日更调度组最近一次失败的 run 各重试一次。",
            group="补抓与恢复",
            command=("run", "retry"),
            fixed=("--failed-groups",),
            lock_class="ingest-daily",
            result="run_json",
        ),
        OpSpec(
            id="run.compact",
            title="发布 staging",
            summary="把这次 run 留在 staging 里、已经结束的数据发布到湖里。",
            group="补抓与恢复",
            command=("run", "compact"),
            params=(Param("run_id", "run_id", "run", flag="--run-id", required=True),),
            lock_class="lake-write",
            result="run_json",
        ),
        OpSpec(
            id="diag.doctor",
            title="环境体检",
            summary="离线检查配置、依赖和会悄悄坏掉的地方。不访问网络。",
            group="初始化",
            command=("doctor",),
            fixed=("--json",),
            lock_class="read",
            confirm="none",
            result="gate",
            available_in="both",
        ),
        OpSpec(
            id="init.start",
            title="初始化数据湖",
            summary="全市场标的。quick 是近 3 年，full 从各数据集的历史起点起，日线从 2016-01-01 起。",
            group="初始化",
            command=("init",),
            params=(
                Param(
                    "profile",
                    "choice",
                    "深度",
                    flag="--profile",
                    required=True,
                    choices=("quick", "full"),
                    help="quick 为近 3 年；full 更深。",
                ),
                Param("since", "date", "历史起点", flag="--since", help="填写后覆盖上面的深度。"),
                Param(
                    "pack",
                    "multi",
                    "研究包",
                    flag="--pack",
                    repeat=True,
                    choices=("market", "fundamentals", "universe"),
                    help="不改变初始化下载的内容。market 行情，fundamentals 基本面，universe 历史 ST。",
                ),
                Param("keep_going", "bool", "某阶段失败后继续", flag="--keep-going"),
            ),
            lock_class="init",
            confirm="heavy",
            heavy_text="我知道初始化会按所选深度请求数据源，可能持续数小时。",
            result="run_json",
        ),
        OpSpec(
            id="init.resume",
            title="继续初始化",
            summary="续跑最近一次没跑完的初始化，已成功的批次会保留。",
            group="初始化",
            command=("init",),
            params=(
                Param("run_id", "run_id", "init run", flag="--run-id", required=True),
                Param("keep_going", "bool", "某阶段失败后继续", flag="--keep-going"),
            ),
            lock_class="init",
            result="run_json",
        ),
        OpSpec(
            id="backfill.run",
            title="回填一个数据集",
            summary="先给出来源、范围和抓取方式，确认后才取数。",
            group="定向补数",
            command=("backfill",),
            params=(
                Param(
                    "dataset",
                    "choice",
                    "数据集",
                    required=True,
                    choice_source="datasets",
                    position=0,
                    empty_reason="没有可从面板回填的数据集。",
                ),
                Param("start", "date", "起点", flag="--start"),
                Param("end", "date", "终点", flag="--end", help="默认今天。"),
                Param(
                    "symbols",
                    "symbols",
                    "标的",
                    flag="--symbols",
                    help="逗号分隔，如 600519.SH,000001.SZ。不填就是该数据集的默认范围。",
                ),
            ),
            lock_class="lake-write",
            confirm="standard",
            heavy_text="我确认按预览范围回填；不限标的或超过一年会大量请求数据源。",
            result="run_json",
        ),
        OpSpec(
            id="derive.run",
            title="重算派生数据",
            summary="从已发布的数据重算。全量会重写分区。",
            group="定向补数",
            command=("derive",),
            params=(
                Param(
                    "name",
                    "choice",
                    "派生",
                    required=True,
                    choices=DERIVE_NAMES,
                    position=0,
                ),
                Param("start", "date", "起点", flag="--start"),
                Param("end", "date", "终点", flag="--end"),
                Param("full", "bool", "全量重写", flag="--full"),
            ),
            lock_class="lake-write",
            heavy_text="我确认全量重算并重写分区。",
            result="run_json",
        ),
        OpSpec(
            id="check.status",
            title="新鲜度",
            summary="按数据集查看覆盖和新鲜度，只读。",
            group="巡检",
            command=("status",),
            fixed=("--datasets",),
            params=(
                Param(
                    "groups",
                    "multi",
                    "限定调度组",
                    flag="--groups",
                    choice_source="schedule_groups",
                ),
            ),
            lock_class="read",
            confirm="none",
            result="report",
        ),
        OpSpec(
            id="check.verify",
            title="校验覆盖",
            summary="只读校验湖的覆盖。退出码 1 表示有发现，不是命令出错。",
            group="巡检",
            command=("verify",),
            lock_class="read",
            confirm="none",
            result="gate",
        ),
        OpSpec(
            id="check.audit",
            title="全湖审计",
            summary="跑一次全湖审计并写下快照。",
            group="巡检",
            command=("audit",),
            fixed=("--full",),
            lock_class="report",
            confirm="none",
            result="gate",
        ),
        OpSpec(
            id="check.sources_probe",
            title="探测数据源",
            summary="复用 12 小时内的采集证据，只补探没碰过的来源。会请求第三方主机。",
            group="巡检",
            command=("sources", "probe"),
            fixed=("--stale-only",),
            params=(
                Param(
                    "vantage",
                    "text",
                    "出口标签",
                    flag="--vantage",
                    help="如 local、cn。不填用 local。",
                ),
            ),
            lock_class="report",
            result="report",
        ),
        OpSpec(
            id="maint.stats_rebuild",
            title="重建度量表",
            summary="重算行数、体积和来源分布。不取数。",
            group="巡检",
            command=("stats", "rebuild"),
            lock_class="report",
            confirm="none",
            result="report",
        ),
        OpSpec(
            id="config.upgrade_preview",
            title="预览配置升级",
            summary="列出当前版本要补进配置的 step 和调度组，不写文件。",
            group="巡检",
            command=("config",),
            fixed=("upgrade", "--dry-run"),
            lock_class="read",
            confirm="none",
            result="report",
        ),
    )


OPS: tuple[OpSpec, ...] = _ops()
OPS_BY_ID: dict[str, OpSpec] = {spec.id: spec for spec in OPS}

GROUP_ORDER = ("日更", "补抓与恢复", "初始化", "定向补数", "备份与恢复", "巡检")


def spec_for(op_id: str) -> OpSpec:
    try:
        return OPS_BY_ID[op_id]
    except KeyError as exc:
        raise UnknownOp(f"未知操作 {op_id}") from exc


def flags_of(spec: OpSpec) -> list[str]:
    """Option strings this operation passes, for the Click alignment test."""
    flags = [param.flag for param in spec.params if param.flag]
    flags.extend(token for token in spec.fixed if token.startswith("--"))
    return flags


def backfill_datasets(config) -> list[str]:
    """Datasets the page may backfill: registered, enabled, not derived."""
    import cnequity.steps  # noqa: F401 — registration is the import
    from cnequity.domain.datasets import DATASETS, is_dataset_enabled
    from cnequity.orchestrator.registry import STEP_REGISTRY

    names = [
        name
        for name, dataset in DATASETS.items()
        if dataset.layer != "derived" and name in STEP_REGISTRY and is_dataset_enabled(name, config)
    ]
    return sorted(names)


def choices_for(source: str, config) -> list[str]:
    if config is None:
        return []
    if source == "schedule_groups":
        return sorted(getattr(config, "schedule_groups", {}) or {})
    if source == "events_groups":
        return sorted(getattr(config, "events_groups", {}) or {})
    if source == "datasets":
        return backfill_datasets(config)
    if source == "derive":
        return list(DERIVE_NAMES)
    if source in {"stored_datasets", "snapshots"}:
        from cnequity.serve.ops.backups import snapshot_names, stored_datasets

        return stored_datasets(config) if source == "stored_datasets" else snapshot_names(config)
    return []


def _choice_list(param: Param, config) -> list[str]:
    if param.choice_source:
        return choices_for(param.choice_source, config)
    return list(param.choices)


def unavailable_reason(spec: OpSpec, config, *, setup: bool) -> str | None:
    if setup and spec.available_in == "normal":
        return "先完成首次配置。"
    if not setup and spec.available_in == "setup":
        return "只在首次配置时使用。"
    if config is None:
        return None if spec.available_in != "normal" else "先完成首次配置。"
    if spec.needs and not choices_for(spec.needs, config):
        return spec.needs_reason or "配置里没有这一项。"
    for param in spec.params:
        if param.required and param.choice_source and not _choice_list(param, config):
            return param.empty_reason or f"没有可选的{param.label}。"
    return None


def command_template(spec: OpSpec) -> str:
    """The command a person can read. No ``--config`` path from this machine."""
    tokens = ["cne", *spec.command, *spec.fixed]
    positionals = sorted(
        (param for param in spec.params if param.flag is None), key=lambda param: param.position
    )
    for param in (*positionals, *(param for param in spec.params if param.flag)):
        if param.flag is None:
            piece = param.name.upper()
        elif param.kind == "bool":
            piece = param.flag
        else:
            piece = f"{param.flag} {param.name.upper()}"
        tokens.append(piece if param.required else f"[{piece}]")
    return " ".join(tokens)


def describe(config, *, setup: bool) -> list[dict[str, Any]]:
    """Cards and form fields. Choices come from the loaded config."""
    cards = []
    for spec in OPS:
        reason = unavailable_reason(spec, config, setup=setup)
        cards.append(
            {
                "id": spec.id,
                "title": spec.title,
                "summary": spec.summary,
                "group": spec.group,
                "command": command_template(spec),
                "confirm": spec.confirm,
                "result": spec.result,
                "available": reason is None,
                "unavailable_reason": reason,
                "params": [
                    {
                        "name": param.name,
                        "kind": param.kind,
                        "label": param.label,
                        "help": param.help,
                        "required": param.required,
                        "choices": _choice_list(param, config) or None,
                    }
                    for param in spec.params
                ],
            }
        )
    return cards


def _parse_date(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OpsError(f"{label}须是 YYYY-MM-DD。")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OpsError(f"{label}须是 YYYY-MM-DD。") from exc
    if parsed > shanghai_today():
        raise OpsError(f"{label}不能晚于今天（北京时间）。")
    return parsed.isoformat()


def _parse_symbols(value: object) -> list[str]:
    from cnequity.domain.symbols import parse_symbol

    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        raise OpsError("标的须是逗号分隔的代码。")
    if len(parts) > 200:
        raise OpsError("一次最多 200 个标的。")
    parsed = []
    for part in parts:
        try:
            parsed.append(parse_symbol(part).symbol)
        except ValueError as exc:
            raise OpsError(f"无法识别的标的 {part}") from exc
    return parsed


def _parse_run_id(value: object, config) -> str:
    if not isinstance(value, str) or not value or len(value) > 80:
        raise OpsError("run id 无效。")
    if any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-"
        for char in value
    ):
        raise OpsError("run id 无效。")
    if config is None or not Path(config.manifest_path).exists():
        raise OpsError("还没有运行记录。")
    from cnequity.orchestrator.manifest import Manifest
    from cnequity.orchestrator.run_lock import is_run_locked

    record = Manifest(config.manifest_path).get_run(value)
    if record is None:
        raise OpsError(f"没有 run {value}。")
    if record["status"] == "running" and is_run_locked(config.meta_root, value):
        raise OpsError(f"run {value} 还在运行。")
    return value


def _parse_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OpsError(f"{label}无效。")
    if not value:
        return ""
    if len(value) > 32 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        for char in value
    ):
        raise OpsError(f"{label}只能是字母、数字、下划线和短横线。")
    return value


def normalize(spec: OpSpec, raw: dict[str, Any] | None, config) -> dict[str, Any]:
    """Validate *raw* and return JSON-ready values for every parameter."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise OpsError("参数格式无效。")
    known = {param.name for param in spec.params}
    extra = sorted(set(raw) - known)
    if extra:
        raise OpsError("不能指定：" + "、".join(extra))
    values: dict[str, Any] = {}
    for param in spec.params:
        if param.name not in raw or raw[param.name] is None or raw[param.name] == "":
            if param.required:
                raise OpsError(f"请填写{param.label}。")
            if param.kind == "bool":
                values[param.name] = False
            elif param.kind == "multi":
                values[param.name] = []
            else:
                values[param.name] = None
            continue
        value = raw[param.name]
        if param.kind == "bool":
            if not isinstance(value, bool):
                raise OpsError(f"{param.label}须是布尔值。")
            values[param.name] = value
        elif param.kind == "date":
            values[param.name] = _parse_date(value, param.label)
        elif param.kind == "choice":
            if not isinstance(value, str):
                raise OpsError(f"{param.label}无效。")
            allowed = _choice_list(param, config)
            if value not in allowed:
                raise OpsError(f"{param.label}不在可选范围内。")
            values[param.name] = value
        elif param.kind == "multi":
            if isinstance(value, str):
                items = [part.strip() for part in value.split(",") if part.strip()]
            elif isinstance(value, list) and all(isinstance(part, str) for part in value):
                items = [part.strip() for part in value if part.strip()]
            else:
                raise OpsError(f"{param.label}无效。")
            allowed = set(_choice_list(param, config))
            unknown = [item for item in items if item not in allowed]
            if unknown:
                raise OpsError(f"{param.label}不在可选范围内：{'、'.join(unknown)}")
            values[param.name] = items
        elif param.kind == "symbols":
            values[param.name] = _parse_symbols(value)
        elif param.kind == "text":
            values[param.name] = _parse_text(value, param.label) or None
        elif param.kind == "run_id":
            values[param.name] = _parse_run_id(value, config)
        elif param.kind == "snapshot_name":
            from cnequity.storage.snapshots import SnapshotStore

            if not isinstance(value, str):
                raise OpsError("备份名称无效。")
            try:
                values[param.name] = SnapshotStore._validate_name(value)
            except ValueError as exc:
                raise OpsError("备份名称须为 1–80 个字母、数字、点、下划线或短横线。") from exc
        elif param.kind == "path":
            if (
                not isinstance(value, str)
                or len(value) > 4096
                or any(ord(char) < 32 for char in value)
            ):
                raise OpsError("目录路径无效。")
            from cnequity.serve.ops.backups import restore_target

            values[param.name] = str(restore_target(config, value, raw.get("snapshot_root")))
        elif param.kind == "directory":
            from cnequity.storage.snapshots import _reject_symlink_path

            if (
                not isinstance(value, str)
                or not value
                or len(value) > 4096
                or any(ord(char) < 32 for char in value)
            ):
                raise OpsError("备份目录路径无效。")
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise OpsError("备份目录必须是绝对路径。")
            _reject_symlink_path(path, label="snapshot root")
            if path.exists() and not path.is_dir():
                raise OpsError("备份存放目录不是目录。")
            values[param.name] = str(path)
        else:
            raise OpsError(f"未知参数类型 {param.kind}")
    _check_cross(spec, values, config)
    return values


def _check_cross(spec: OpSpec, values: dict[str, Any], config) -> None:
    start, end = values.get("start"), values.get("end")
    if start and end and start > end:
        raise OpsError("起点不能晚于终点。")
    if spec.id == "snapshot.create" and not values.get("datasets"):
        raise OpsError("至少选择一个备份数据集。")
    if spec.id == "snapshot.create":
        from cnequity.serve.ops.backups import create_root

        create_root(config, values.get("snapshot_root"))
    if spec.id == "init.resume":
        if config is None:
            raise OpsError("尚未配置。")
        from cnequity.orchestrator.manifest import Manifest

        latest = None
        if Path(config.manifest_path).exists():
            latest = Manifest(config.manifest_path).latest_incomplete_init_run()
        if latest is None or latest["run_id"] != values["run_id"]:
            raise OpsError("只能续跑最近一次没完成的初始化。")
    if spec.id == "init.start" and values.get("profile") not in {"quick", "full"}:
        raise OpsError("初始化深度只能是 quick 或 full。")


def confirm_level(spec: OpSpec, params: dict[str, Any]) -> str:
    if spec.id == "backfill.run":
        symbols = params.get("symbols") or []
        start, end = params.get("start"), params.get("end")
        if not symbols or not start:
            return "heavy"
        finish = date.fromisoformat(end) if end else shanghai_today()
        if (finish - date.fromisoformat(start)).days > 366:
            return "heavy"
        return "standard"
    if spec.id == "derive.run" and params.get("full"):
        return "heavy"
    return spec.confirm


def acknowledgements(spec: OpSpec, level: str) -> list[dict[str, str]]:
    if level == "none":
        return []
    items = [dict(STANDARD_ACK)]
    if level == "heavy" and spec.heavy_text:
        items.append({"id": "heavy", "text": spec.heavy_text})
    return items


def lock_class_for(spec: OpSpec, params: dict[str, Any], config) -> str:
    if spec.lock_class != "run":
        return spec.lock_class
    from cnequity.orchestrator.engine import job_family
    from cnequity.orchestrator.manifest import Manifest

    record = Manifest(config.manifest_path).get_run(params["run_id"])
    family = job_family(str(record["job_name"]))
    if family == "daily":
        return "ingest-daily"
    if family == "events":
        return "ingest-events"
    if family == "init":
        return "init"
    return "lake-write"


def target_run_id(spec: OpSpec, params: dict[str, Any]) -> str | None:
    if spec.id in {"run.retry", "init.resume", "run.compact"}:
        return params.get("run_id")
    return None


def build_argv(spec: OpSpec, params: dict[str, Any], config_path: Path | None) -> list[str]:
    argv = [*spec.command, *spec.fixed]
    for param in sorted(
        (item for item in spec.params if item.flag is None), key=lambda item: item.position
    ):
        value = params.get(param.name)
        if value is not None:
            argv.append(str(value))
    for param in spec.params:
        if param.flag is None:
            continue
        value = params.get(param.name)
        if param.kind == "bool":
            if value:
                argv.append(param.flag)
            continue
        if value is None or value == [] or value == "":
            continue
        if param.kind in {"multi", "symbols"}:
            if param.repeat:
                for item in value:
                    argv.extend([param.flag, item])
                continue
            argv.extend([param.flag, ",".join(value)])
            continue
        argv.extend([param.flag, str(value)])
    if config_path is not None:
        argv.extend(["--config", str(config_path)])
    return argv


def command_line(argv: list[str]) -> str:
    return "cne " + shlex.join(argv)


def config_file(config) -> Path | None:
    path = getattr(config, "config_path", None) if config is not None else None
    if path is None:
        return None
    resolved = Path(path).expanduser().resolve()
    return resolved if resolved.is_file() else None


def pass_config(spec: OpSpec, config) -> Path | None:
    """Absolute ``--config``, or None when doctor is running before a file exists."""
    path = config_file(config)
    if path is None and spec.id == "diag.doctor":
        return None
    return path


def prepared(spec: OpSpec, raw: dict[str, Any] | None, config) -> dict[str, Any]:
    """Normalized parameters, argv and the confirmation this request needs."""
    reason = unavailable_reason(spec, config, setup=config is None)
    if reason and spec.id != "diag.doctor":
        raise OpsError(reason)
    params = normalize(spec, raw, config)
    if spec.id.startswith("snapshot."):
        from cnequity.serve.ops.backups import store_for

        params["snapshot_root"] = str(store_for(config, params.get("snapshot_root")).root)
    path = pass_config(spec, config)
    if path is None and spec.id != "diag.doctor":
        raise OpsError("这份服务没有可写入的配置文件路径，不能从面板启动。")
    level = confirm_level(spec, params)
    argv = build_argv(spec, params, path)
    return {
        "params": params,
        "argv": argv,
        "command": command_line(argv),
        "lock_class": (
            spec.lock_class if spec.lock_class != "run" else lock_class_for(spec, params, config)
        ),
        "confirm": level,
        "acknowledgements": acknowledgements(spec, level),
        "target_run_id": target_run_id(spec, params),
        "config_path": str(path) if path is not None else None,
        "result": spec.result,
        "title": spec.title,
    }
