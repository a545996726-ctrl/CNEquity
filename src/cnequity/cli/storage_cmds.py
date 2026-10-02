"""Inspect revision protection and begin a non-destructive observation period."""

from __future__ import annotations

import json
from pathlib import Path

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import _cfg, config_option
from cnequity.file_lock import LockUnavailable
from cnequity.storage.lifecycle import LifecycleError, LifecycleStore
from cnequity.storage.revisions import RevisionConsistencyError


class _LifecycleGroup(click.Group):
    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except (
            LifecycleError,
            RevisionConsistencyError,
            LockUnavailable,
            OSError,
            ValueError,
        ) as exc:
            raise click.ClickException(str(exc)) from exc


def _store(config_path: str) -> LifecycleStore:
    return LifecycleStore(_cfg(config_path).meta_root)


def _emit(value: dict) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


@cli.group(cls=_LifecycleGroup)
def storage():
    """版本保留登记与观察期；物理删除须在 serve 网页确认。"""


@storage.command("inspect")
@config_option
@click.option("--keep", type=click.IntRange(min=1), default=5, show_default=True)
def inspect(config_path: str, keep: int):
    """只读列出版本、保留原因和已登记试验。"""
    _emit(_store(config_path).inspect(keep=keep))


@storage.command("explain")
@config_option
@click.argument("object_id")
def explain(config_path: str, object_id: str):
    """显示一个版本的保留依据。"""
    report = _store(config_path).inspect()
    for obj in report["objects"]:
        if obj["object_id"] == object_id:
            _emit(obj)
            return
    reasons = report["resource_holds"].get(object_id)
    if reasons:
        _emit({"object_id": object_id, "holds": reasons})
        return
    raise click.ClickException(f"Unknown retained object: {object_id}")


@storage.command("import")
@config_option
@click.option(
    "--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def import_references(config_path: str, manifest: Path):
    """导入已审核引用清单，只增加保护，不自动解除旧 hold。"""
    value = _store(config_path).import_manifest(json.loads(manifest.read_text(encoding="utf-8")))
    _emit(
        {
            "lake_id": value["lake_id"],
            "held_objects": len(value["holds"]),
            "registered_experiments": len(value["experiments"]),
        }
    )


@storage.command("hold")
@config_option
@click.argument("object_id")
@click.option("--reason", required=True)
def hold(config_path: str, object_id: str, reason: str):
    """保留版本、试验、工件或已存在的清理资源，并取消待删除标记。"""
    _store(config_path).hold(object_id, reason)
    _emit({"object_id": object_id, "held": True})


@storage.command("plan")
@config_option
@click.option("--keep", type=click.IntRange(min=1), default=5, show_default=True)
@click.option("--phase", type=click.Choice(["mark", "purge"]), default="mark", show_default=True)
def plan(config_path: str, keep: int, phase: str):
    """校验引用并保存不可变版本清理计划，不删除数据。"""
    result = _store(config_path).plan(keep=keep, phase=phase)
    summary = {
        k: result[k]
        for k in (
            "plan_id",
            "logical_bytes_selected",
            "candidate_ids",
            "grace_days",
            "purge_available",
            "phase",
        )
    }
    if phase == "purge":
        summary.update({k: result[k] for k in ("purge_ids", "logical_bytes_to_purge")})
    _emit(summary)


@storage.command("apply")
@config_option
@click.argument("plan_id")
@click.option(
    "--phase",
    type=click.Choice(["mark", "purge"]),
    default="mark",
    show_default=True,
    help="mark 原地标记；purge 已禁用，请转到 serve 存储运维页。",
)
@click.option(
    "--maintenance-window",
    is_flag=True,
    help="兼容选项；不能绕过网页删除确认。",
)
def apply(config_path: str, plan_id: str, phase: str, maintenance_window: bool):
    """复查计划后标记；CLI 不允许执行物理删除。"""
    if phase == "purge":
        raise click.ClickException(
            "物理删除必须在 cne serve 的存储运维网页中检查并确认；CLI 只允许预览和标记。"
        )
    else:
        _emit(_store(config_path).mark(plan_id))


@storage.command("experiment-create")
@config_option
@click.option("--parent", type=click.Path(path_type=Path), required=True)
@click.option("--case-id", required=True)
def experiment_create(config_path: str, parent: Path, case_id: str):
    """创建有独立实例身份的空试验目录，并登记为 active。"""
    from cnequity.storage.lifecycle_artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).create_experiment(parent, case_id=case_id))


@storage.command("archive")
@config_option
@click.argument("object_id")
@click.option("--destination", type=click.Path(path_type=Path), required=True)
def archive(config_path: str, object_id: str, destination: Path):
    """复制登记试验并逐文件校验；封存副本，保留原目录及其保护。"""
    from cnequity.storage.lifecycle_artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).archive(object_id, destination))


@storage.command("artifact-verify")
@config_option
@click.argument("object_id")
def artifact_verify(config_path: str, object_id: str):
    """按封存清单校验归档工件的完整内容。"""
    from cnequity.storage.lifecycle_artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).verify(object_id))


@storage.command("resolve")
@config_option
@click.argument("old_path", type=click.Path(path_type=Path))
@click.option("--artifact-id", help="存在多个封存版本时明确选择一个工件。")
def resolve(config_path: str, old_path: Path, artifact_id: str | None):
    """校验并解析旧路径的归档位置；不改写报告，不创建符号链接。"""
    from cnequity.storage.lifecycle_artifacts import ArtifactStore

    path = ArtifactStore(_store(config_path)).resolve(old_path, artifact_id=artifact_id)
    _emit({"old_path": str(old_path), "resolved_path": str(path)})


@storage.command("experiment-plan")
@config_option
@click.option("--phase", type=click.Choice(["mark", "purge"]), default="mark", show_default=True)
def experiment_plan(config_path: str, phase: str):
    """为无引用、非 active 且已有完整归档的试验原目录生成退出计划。"""
    from cnequity.storage.lifecycle_experiments import ExperimentRetirement

    _emit(ExperimentRetirement(_store(config_path)).plan(phase=phase))


@storage.command("experiment-apply")
@config_option
@click.argument("plan_id")
@click.option("--maintenance-window", is_flag=True, help="兼容选项；不能绕过网页删除确认。")
def experiment_apply(config_path: str, plan_id: str, maintenance_window: bool):
    """原地标记冗余原目录；CLI 不允许物理删除，须到 serve 网页确认。"""
    from cnequity.storage.lifecycle_experiments import ExperimentRetirement

    cfg = _cfg(config_path)
    from cnequity.storage.lifecycle import _read

    if len(plan_id) != 64 or any(c not in "0123456789abcdef" for c in plan_id):
        raise click.ClickException("Invalid experiment plan ID")
    plan = _read(LifecycleStore(cfg.meta_root).root / "experiment-plans" / f"{plan_id}.json")
    if plan.get("phase") == "purge":
        raise click.ClickException("物理删除必须在 cne serve 的存储运维网页中检查并确认。")
    _emit(
        ExperimentRetirement(LifecycleStore(cfg.meta_root)).apply(
            plan_id, maintenance=maintenance_window, manifest=cfg.manifest_path
        )
    )


@storage.command("experiment-seal")
@config_option
@click.argument("object_id")
@click.option("--artifact-id", required=True)
def experiment_seal(config_path: str, object_id: str, artifact_id: str):
    """显式结束试验工作目录，并将封存状态绑定到已验证的归档。"""
    from cnequity.storage.lifecycle_artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).seal_experiment(object_id, artifact_id))
