"""Verified independent experiment archives and explicit legacy-path resolution.

Archiving never moves or deletes a source, releases a hold, or promises replay.
Only a sealed copy is registered; interrupted copies remain visibly incomplete.
"""

from __future__ import annotations

import os
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cnequity.file_lock import lake_mutation_lock
from cnequity.storage.atomic import write_json_atomic
from cnequity.storage.file_copy import copy2_isolated
from cnequity.storage.lifecycle import (
    LifecycleError,
    LifecycleStore,
    _read,
    _tree_signature,
    digest,
)
from cnequity.storage.revisions import _reject_symlink_path, sha256_file


def _absolute(path: Path | str) -> Path:
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise LifecycleError("Parent traversal is not a managed path")
    _reject_symlink_path(path)
    return path


def _entries(root: Path, *, hashes: bool = True) -> dict:
    _absolute(root)
    if not root.is_dir():
        raise LifecycleError(f"Archive tree unavailable: {root}")
    directories, files = [], {}

    def onerror(error: OSError) -> None:
        raise error

    for directory, dirs, names in os.walk(root, followlinks=False, onerror=onerror):
        parent = Path(directory)
        for name in sorted(dirs + names):
            path = parent / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
            elif stat.S_ISREG(info.st_mode):
                files[relative] = {"size": info.st_size}
                if hashes:
                    files[relative]["sha256"] = sha256_file(path)
            else:
                raise LifecycleError(f"Archive contains a link or special file: {path}")
    return {"directories": sorted(directories), "files": dict(sorted(files.items()))}


class ArtifactStore:
    def __init__(self, lifecycle: LifecycleStore):
        self.lifecycle = lifecycle

    def _experiment(self, object_id: str) -> dict:
        registry = self.lifecycle.registry(required=True)
        matches = [e for e in registry["experiments"] if e["object_id"] == object_id]
        if len(matches) != 1:
            raise LifecycleError(f"Unknown or ambiguous experiment: {object_id}")
        return matches[0]

    def create_experiment(self, parent: Path, *, case_id: str) -> dict:
        """Create an empty managed workspace with its own instance identity."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", case_id):
            raise LifecycleError("Invalid case ID")
        parent = _absolute(parent)
        token = uuid.uuid4().hex
        root = parent / case_id / token
        with lake_mutation_lock(self.lifecycle.meta, timeout=30):
            registry = self.lifecycle.registry(required=True)
            root.mkdir(parents=True, exist_ok=False)
            identity = {
                "schema_version": 1,
                "object_id": f"experiment/{token}",
                "instance_id": uuid.uuid4().hex,
                "case_id": case_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source_lake_id": registry["lake_id"],
            }
            write_json_atomic(root / ".cne-experiment.json", identity, indent=2)
            info = root.stat()
            obj = {
                **identity,
                "path": str(root),
                "directory_identity": [info.st_dev, info.st_ino],
                "status": "active",
                "references": [],
            }
            registry["experiments"].append(obj)
            self.lifecycle._save(registry)
        return obj

    def archive(self, object_id: str, destination: Path) -> dict:
        """Seal a verified snapshot; a live source remains a separate location.

        Cooperating lake writers are excluded while hashing/copying. External
        writers are detected by source signatures before and after verification.
        No original location becomes deletion-eligible as a result of this call.
        """
        experiment = self._experiment(object_id)
        source, destination = _absolute(experiment["path"]), _absolute(destination)
        if (
            source.resolve() == destination.resolve()
            or source.resolve() in destination.resolve().parents
        ):
            raise LifecycleError("Archive destination must be outside the source")
        info = source.stat()
        if [info.st_dev, info.st_ino] != experiment["directory_identity"]:
            raise LifecycleError("Experiment path was replaced")
        # Use the source lake's writer lock when it exists, without creating a
        # metadata tree in small candidate-only experiments.
        from contextlib import nullcontext

        source_lock = (
            lake_mutation_lock(source / "meta", timeout=30)
            if (source / "meta").is_dir()
            else nullcontext()
        )
        with source_lock:
            signature = _tree_signature(source)
            entries = _entries(source)
            if _tree_signature(source) != signature:
                raise LifecycleError("Experiment changed during hashing")
            manifest = {
                "schema_version": 1,
                "source_object_id": object_id,
                "source_path": str(source),
                "source_identity": experiment["directory_identity"],
                "source_signature": signature,
                "entries": entries,
                "logical_bytes": sum(f["size"] for f in entries["files"].values()),
                "retention_level": "record",
                "replay_ready": False,
            }
            artifact_id = digest(manifest)
            target = destination / artifact_id
            _absolute(target)
            if target.exists():
                if _read(target / "manifest.json") != manifest:
                    raise LifecycleError("Existing artifact manifest differs")
                self._verify_tree(target, manifest)
            else:
                destination.mkdir(parents=True, exist_ok=True)
                partial = destination / f".incomplete-{uuid.uuid4().hex}"
                (partial / "data").mkdir(parents=True)
                for directory in entries["directories"]:
                    (partial / "data" / directory).mkdir(parents=True, exist_ok=True)
                for relative in entries["files"]:
                    copy2_isolated(source / relative, partial / "data" / relative)
                self._verify_tree(partial, manifest)
                if _tree_signature(source) != signature:
                    raise LifecycleError("Experiment changed while archiving; copy is incomplete")
                write_json_atomic(partial / "manifest.json", manifest, indent=2)
                partial.rename(target)
            if _tree_signature(source) != signature:
                raise LifecycleError("Experiment changed before archive registration")
        record = {
            "object_id": f"artifact/{artifact_id}",
            "path": str(target),
            "manifest_digest": artifact_id,
            "source_object_id": object_id,
            "source_path": str(source),
            "source_signature": signature,
            "logical_bytes": manifest["logical_bytes"],
            "files": len(entries["files"]),
            "status": "sealed",
        }
        # Large copies do not hold the production lake's publication lock.
        with lake_mutation_lock(self.lifecycle.meta, timeout=30):
            if self._experiment(object_id) != experiment:
                raise LifecycleError("Experiment registration changed during archiving")
            registry = self.lifecycle.registry(required=True)
            artifacts = registry.setdefault("artifacts", {})
            previous = artifacts.get(record["object_id"])
            if previous is not None and previous != record:
                raise LifecycleError("Artifact already registered at another location")
            artifacts[record["object_id"]] = record
            self.lifecycle._save(registry)
        return record

    def seal_experiment(self, object_id: str, artifact_id: str) -> dict:
        """Declare a workspace finished, bound to an independently sealed copy.

        This is an explicit operator transition. A later source write makes
        retirement ineligible until a new archive is verified and sealed.
        """
        from contextlib import nullcontext

        with lake_mutation_lock(self.lifecycle.meta, timeout=30):
            obj = self._experiment(object_id)
            source = _absolute(obj["path"])
            _, manifest = self.manifest(artifact_id)
            if manifest["source_object_id"] != object_id:
                raise LifecycleError("Archive belongs to another experiment")
            source_lock = (
                lake_mutation_lock(source / "meta", blocking=False)
                if (source / "meta").is_dir()
                else nullcontext()
            )
            with source_lock:
                self.verify(artifact_id)
                info = source.stat()
                if [info.st_dev, info.st_ino] != obj["directory_identity"] or _tree_signature(
                    source
                ) != manifest["source_signature"]:
                    raise LifecycleError("Experiment changed since archival")
                registry = self.lifecycle.registry(required=True)
                obj = next(e for e in registry["experiments"] if e["object_id"] == object_id)
                obj.update(
                    status="sealed",
                    sealed_artifact_id=artifact_id,
                    sealed_at=datetime.now(timezone.utc).isoformat(),
                )
                registry.get("experiment_pending", {}).pop(object_id, None)
                self.lifecycle._save(registry)
                return obj

    @staticmethod
    def _verify_tree(root: Path, manifest: dict) -> None:
        if _entries(root / "data") != manifest["entries"]:
            raise LifecycleError(f"Artifact content differs from manifest: {root}")

    def manifest(self, object_id: str) -> tuple[Path, dict]:
        registry = self.lifecycle.registry(required=True)
        record = registry.get("artifacts", {}).get(object_id)
        if record is None or record.get("status") != "sealed":
            raise LifecycleError(f"Unknown sealed artifact: {object_id}")
        root = _absolute(record["path"])
        manifest = _read(root / "manifest.json")
        if (
            digest(manifest) != record["manifest_digest"]
            or object_id != f"artifact/{digest(manifest)}"
            or manifest.get("source_object_id") != record["source_object_id"]
            or manifest.get("source_path") != record["source_path"]
        ):
            raise LifecycleError("Artifact manifest identity changed")
        return root, manifest

    def verify(self, object_id: str) -> dict:
        root, manifest = self.manifest(object_id)
        self._verify_tree(root, manifest)
        return {
            "object_id": object_id,
            "verified": True,
            "logical_bytes": manifest["logical_bytes"],
        }

    def resolve(self, path: Path, *, artifact_id: str | None = None) -> Path:
        """Resolve an old absolute path, rejecting ambiguous archive versions.

        Files are hash-checked; resolving a directory verifies its whole archive.
        Consumers must call this explicitly; old reports are never rewritten.
        """
        path = _absolute(path)
        registry = self.lifecycle.registry(required=True)
        matches = []
        for oid, record in registry.get("artifacts", {}).items():
            old = Path(record["source_path"])
            if path == old or old in path.parents:
                if artifact_id is None or oid == artifact_id:
                    matches.append(oid)
        if len(matches) != 1:
            raise LifecycleError("Legacy path has no unique archive; specify its artifact ID")
        root, manifest = self.manifest(matches[0])
        relative = path.relative_to(manifest["source_path"]).as_posix()
        target = _absolute(root / "data" / relative)
        expected = manifest["entries"]["files"].get(relative)
        if expected is not None:
            if (
                not target.is_file()
                or target.stat().st_size != expected["size"]
                or sha256_file(target) != expected["sha256"]
            ):
                raise LifecycleError("Resolved artifact file is missing or corrupt")
        elif relative == "." or relative in manifest["entries"]["directories"]:
            self._verify_tree(root, manifest)
        else:
            raise LifecycleError("Legacy path is not present in the archive")
        return target
