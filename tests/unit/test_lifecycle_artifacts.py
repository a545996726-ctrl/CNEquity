"""Archival tests use isolated directories; no real lake or provider is accessed."""

import json
from pathlib import Path

import pytest

from cnequity.storage.lifecycle import LifecycleError, LifecycleStore, reference_fingerprint
from cnequity.storage.lifecycle_artifacts import ArtifactStore
from cnequity.storage.revisions import RevisionConsistencyError


@pytest.fixture
def artifacts(tmp_path):
    store = LifecycleStore(tmp_path / "lake/meta")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    roots = [{"path": str(evidence)}]
    store.import_manifest(
        {
            "schema_version": 1,
            "meta_root": str(store.meta),
            "holds": {},
            "reference_roots": roots,
            "reference_fingerprint": reference_fingerprint(roots),
        }
    )
    return ArtifactStore(store)


def setup_source(artifacts, tmp_path):
    obj = artifacts.create_experiment(tmp_path / "experiments", case_id="repair")
    source = Path(obj["path"])
    (source / "empty").mkdir()
    (source / "input").mkdir()
    (source / "input/a.parquet").write_bytes(b"original content")
    return obj, source


def test_independent_archive_and_resolve_survive_missing_source(artifacts, tmp_path):
    obj, source = setup_source(artifacts, tmp_path)
    result = artifacts.archive(obj["object_id"], tmp_path / "archives")
    target = artifacts.resolve(source / "input/a.parquet")
    assert target.read_bytes() == b"original content"
    assert target.stat().st_ino != (source / "input/a.parquet").stat().st_ino
    (source / "input/a.parquet").write_bytes(b"new content")
    assert target.read_bytes() == b"original content"
    source.rename(source.with_name("moved"))
    assert artifacts.resolve(source / "input/a.parquet") == target
    assert artifacts.verify(result["object_id"])["verified"]
    registry = artifacts.lifecycle.registry()
    assert registry["experiments"][0]["status"] == "active"
    assert not registry["pending"]
    manifest = json.loads((Path(result["path"]) / "manifest.json").read_text())
    assert not manifest["replay_ready"]


def test_archive_is_idempotent_and_versions_require_explicit_selection(artifacts, tmp_path):
    obj, source = setup_source(artifacts, tmp_path)
    first = artifacts.archive(obj["object_id"], tmp_path / "archives")
    assert artifacts.archive(obj["object_id"], tmp_path / "archives") == first
    (source / "input/a.parquet").write_bytes(b"changed")
    second = artifacts.archive(obj["object_id"], tmp_path / "archives")
    assert first["object_id"] != second["object_id"]
    with pytest.raises(LifecycleError, match="unique"):
        artifacts.resolve(source / "input/a.parquet")
    assert (
        artifacts.resolve(source / "input/a.parquet", artifact_id=first["object_id"]).read_bytes()
        == b"original content"
    )


@pytest.mark.parametrize("change", ["bytes", "extra", "missing", "manifest", "link"])
def test_archive_corruption_rejected(artifacts, tmp_path, change):
    obj, source = setup_source(artifacts, tmp_path)
    result = artifacts.archive(obj["object_id"], tmp_path / "archives")
    root = Path(result["path"])
    if change == "bytes":
        (root / "data/input/a.parquet").write_bytes(b"corrupted")
    elif change == "extra":
        (root / "data/extra").mkdir()
    elif change == "missing":
        (root / "data/empty").rmdir()
    elif change == "manifest":
        path = root / "manifest.json"
        content = json.loads(path.read_text())
        content["source_path"] = str(tmp_path)
        path.write_text(json.dumps(content))
    else:
        (root / "data/input/a.parquet").unlink()
        (root / "data/input/a.parquet").symlink_to(source / "input/a.parquet")
    with pytest.raises((LifecycleError, RevisionConsistencyError)):
        artifacts.verify(result["object_id"])


def test_interruption_or_concurrent_source_write_never_registers(artifacts, tmp_path, monkeypatch):
    import cnequity.storage.lifecycle_artifacts as module

    obj, source = setup_source(artifacts, tmp_path)
    real_copy = module.copy2_isolated

    def changing_copy(src, dst):
        real_copy(src, dst)
        (source / "new").write_bytes(b"concurrent writer")

    monkeypatch.setattr(module, "copy2_isolated", changing_copy)
    with pytest.raises(LifecycleError, match="changed"):
        artifacts.archive(obj["object_id"], tmp_path / "archives")
    assert not artifacts.lifecycle.registry().get("artifacts")
    assert list((tmp_path / "archives").glob(".incomplete-*"))
    assert (source / "input/a.parquet").read_bytes() == b"original content"


def test_replaced_source_and_unsafe_destinations_rejected(artifacts, tmp_path):
    obj, source = setup_source(artifacts, tmp_path)
    with pytest.raises(LifecycleError, match="outside"):
        artifacts.archive(obj["object_id"], source / "archive")
    with pytest.raises(LifecycleError, match="traversal"):
        artifacts.archive(obj["object_id"], tmp_path / "a/../archives")
    source.rename(source.with_name("previous"))
    source.mkdir()
    with pytest.raises(LifecycleError, match="replaced"):
        artifacts.archive(obj["object_id"], tmp_path / "archives")


def test_source_links_rejected(artifacts, tmp_path):
    obj, source = setup_source(artifacts, tmp_path)
    (source / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(LifecycleError, match="link"):
        artifacts.archive(obj["object_id"], tmp_path / "archives")


def test_managed_workspace_requires_explicit_seal_and_new_writes_block_retirement(
    artifacts, tmp_path
):
    from cnequity.storage.lifecycle_experiments import ExperimentRetirement

    obj, source = setup_source(artifacts, tmp_path)
    record = artifacts.archive(obj["object_id"], tmp_path / "archives")
    cleaner = ExperimentRetirement(artifacts.lifecycle)
    assert not cleaner.plan()["objects"]
    artifacts.seal_experiment(obj["object_id"], record["object_id"])
    assert len(cleaner.plan()["objects"]) == 1
    (source / "input/a.parquet").write_bytes(b"new work")
    assert not cleaner.plan()["objects"]
    with pytest.raises(LifecycleError, match="changed"):
        artifacts.seal_experiment(obj["object_id"], record["object_id"])
