"""Read-only backup inventory and bounded previews for portable lake snapshots."""

from __future__ import annotations

import hashlib
from pathlib import Path

from cnequity.domain.datasets import DATASETS
from cnequity.serve.ops.catalog import OpsError
from cnequity.storage.snapshots import SnapshotStore, _reject_symlink_path


def store_for(config, root: str | None = None) -> SnapshotStore:
    if root is None:
        from cnequity.serve.ops.scheduler import settings

        root = settings(config).get("backup_root")
    return SnapshotStore(config, Path(root) if root else None)


def snapshot_names(config) -> list[str]:
    return [item["name"] for item in inventory(config)["snapshots"] if not item.get("error")]


def create_root(config, root: str | None = None) -> Path:
    path = store_for(config, root).root
    _reject_symlink_path(path, label="snapshot root")
    resolved = path.resolve()
    lake = Path(config.data_root).resolve()
    if resolved == lake or lake in resolved.parents:
        allowed = [Path(config.meta_root).resolve() / "snapshots", lake / "backups"]
        if not any(resolved == base or base in resolved.parents for base in allowed):
            raise OpsError(
                "湖内备份只允许放在 meta/snapshots 或 backups，避免混入业务数据；也可选择湖外目录。"
            )
    if path.exists() and not path.is_dir():
        raise OpsError("备份目录不是目录。")
    return path


def stored_datasets(config) -> list[str]:
    """Use publication pointers, not a scan of parquet contents."""
    from cnequity.storage.revisions import resolve_committed_root

    result = []
    for name, spec in DATASETS.items():
        base = config.derived_root if spec.layer == "derived" else config.curated_root
        try:
            path = resolve_committed_root(base / name, dataset=name, meta_root=config.meta_root)
        except (OSError, ValueError, RuntimeError):
            continue
        if path.is_dir():
            result.append(name)
    return sorted(result)


def inventory(config, root: str | None = None) -> dict:
    if config is None:
        return {"root": None, "snapshots": [], "datasets": []}
    store = store_for(config, root)
    _reject_symlink_path(store.root, label="snapshot root")
    if store.root.exists() and not store.root.is_dir():
        raise OpsError("备份目录不是目录。")
    rows = []
    if store.root.is_dir():
        for path in sorted(store.root.iterdir(), key=lambda p: p.name, reverse=True):
            if path.name.startswith("."):
                continue
            try:
                _, manifest = store._manifest(path.name)
                files = manifest.get("files")
                datasets = manifest.get("datasets")
                if not isinstance(datasets, list) or not all(
                    isinstance(item, str) for item in datasets
                ):
                    raise ValueError("invalid snapshot datasets")
                if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
                    raise ValueError("invalid snapshot file inventory")
                rows.append(
                    {
                        "name": path.name,
                        "created_at": manifest.get("created_at"),
                        "datasets": datasets,
                        "files": len(files),
                        "bytes": sum(int(item.get("size_bytes", 0)) for item in files),
                        "verified": False,
                    }
                )
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                rows.append({"name": path.name, "error": str(exc)})
    return {"root": str(store.root), "snapshots": rows, "datasets": stored_datasets(config)}


def restore_target(config, text: str, snapshot_root: str | None = None) -> Path:
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise OpsError("恢复目标必须是绝对路径。")
    _reject_symlink_path(path, label="restore target")
    resolved = path.resolve()
    for forbidden in (
        Path(config.data_root).resolve(),
        store_for(config, snapshot_root).root.resolve(),
    ):
        if resolved == forbidden or forbidden in resolved.parents or resolved in forbidden.parents:
            raise OpsError("恢复目标必须在当前数据湖和备份目录之外。")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise OpsError("恢复目标必须是新建的或空目录。")
    return path


def describe(config, op: str, params: dict) -> tuple[str, str | None]:
    store = store_for(config, params.get("snapshot_root"))
    if op == "snapshot.create":
        if store.path(params["name"]).exists():
            raise OpsError("这个备份名已存在，请填写新的名称。")
        return (
            f"数据备份：{', '.join(params['datasets'])}\n目标：{store.path(params['name'])}\n"
            "复制所选已发布数据、状态、契约与血缘；不请求数据源。"
            "这不是系统镜像，不包含原配置、凭据或完整运行数据库。",
            None,
        )
    path, manifest = store._manifest(params["name"])
    if (
        not isinstance(manifest.get("datasets"), list)
        or not all(isinstance(item, str) for item in manifest["datasets"])
        or not isinstance(manifest.get("files"), list)
    ):
        raise OpsError("备份清单格式无效。")
    digest = hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()
    if op == "snapshot.restore":
        restore_target(config, params["target"], params.get("snapshot_root"))
    text = f"备份：{params['name']}\n数据集：{', '.join(manifest.get('datasets', []))}\n文件：{len(manifest.get('files', []))}"
    if op == "snapshot.restore":
        text += f"\n恢复目标：{params['target']}\n执行时完整校验摘要，校验失败不恢复；完成后当前服务仍使用原数据湖。"
    else:
        text += "\n重新计算全部文件摘要，校验结果保存在任务输出中。"
    return text, digest


def check_manifest(config, params: dict, expected: str | None) -> None:
    if expected is None:
        return
    try:
        path = store_for(config, params.get("snapshot_root")).path(params["name"]) / "manifest.json"
        matched = hashlib.sha256(path.read_bytes()).hexdigest() == expected
    except (OSError, ValueError) as exc:
        raise OpsError("备份清单无法读取，请重新预览。") from exc
    if not matched:
        raise OpsError("备份清单在预览后发生变化，请重新预览。")
