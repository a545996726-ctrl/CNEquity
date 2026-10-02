import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cnequity.storage.lifecycle import LifecycleError, LifecycleStore, reference_fingerprint
from cnequity.storage.lifecycle_artifacts import ArtifactStore
from cnequity.storage.lifecycle_experiments import ExperimentRetirement


@pytest.fixture
def legacy(tmp_path):
    source = tmp_path / "cne-legacy"
    source.mkdir()
    (source / "input").write_bytes(b"important evidence")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    roots = [{"path": str(evidence)}]
    store = LifecycleStore(tmp_path / "lake/meta")
    info = source.stat()
    store.import_manifest(
        {
            "schema_version": 1,
            "meta_root": str(store.meta),
            "holds": {},
            "reference_roots": roots,
            "reference_fingerprint": reference_fingerprint(roots),
            "experiments": [
                {
                    "object_id": "experiment/test",
                    "path": str(source),
                    "directory_identity": [info.st_dev, info.st_ino],
                    "status": "needs_classification",
                    "references": [],
                }
            ],
        }
    )
    artifact = ArtifactStore(store).archive("experiment/test", tmp_path / "archives")
    return store, ExperimentRetirement(store), source, artifact, evidence


def mature(store):
    registry = store.registry()
    now = datetime.now(timezone.utc)
    for p in registry["experiment_pending"].values():
        p["marked_at"] = (now - timedelta(days=8)).isoformat()
        p["not_before"] = (now - timedelta(days=1)).isoformat()
    store._save(registry)


def test_mark_then_maintenance_purge_retains_resolvable_archive(legacy):
    store, cleaner, source, artifact, _ = legacy
    result = cleaner.apply(cleaner.plan()["plan_id"])
    assert result["marked"] == 1 and result["logical_bytes_deleted"] == 0
    assert source.exists()
    assert not cleaner.plan(phase="purge")["objects"]
    mature(store)
    plan = cleaner.plan(phase="purge")
    with pytest.raises(LifecycleError, match="maintenance"):
        cleaner.apply(plan["plan_id"])
    result = cleaner.apply(plan["plan_id"], maintenance=True)
    assert result["status"] == "complete" and not source.exists()
    assert cleaner.apply(plan["plan_id"], maintenance=True) == result
    assert ArtifactStore(store).resolve(source / "input").read_bytes() == b"important evidence"
    assert Path(artifact["path"]).exists()


@pytest.mark.parametrize("change", ["hold", "references", "source", "archive"])
def test_changes_block_saved_plan_without_deleting(legacy, change):
    store, cleaner, source, artifact, evidence = legacy
    cleaner.apply(cleaner.plan()["plan_id"])
    mature(store)
    plan = cleaner.plan(phase="purge")
    if change == "hold":
        store.hold("experiment/test", "new consumer")
        assert not store.registry()["experiment_pending"]
    elif change == "references":
        (evidence / "new.json").write_text("{}")
    elif change == "source":
        (source / "input").write_bytes(b"new bytes")
    else:
        (Path(artifact["path"]) / "data/input").write_bytes(b"corrupt archive")
    with pytest.raises(LifecycleError):
        cleaner.apply(plan["plan_id"], maintenance=True)
    assert source.exists()


def test_active_and_referenced_experiments_never_selected(legacy):
    store, cleaner, _, _, _ = legacy
    registry = store.registry()
    registry["experiments"][0]["references"] = [{"source": "legacy-report"}]
    store._save(registry)
    assert not cleaner.plan()["objects"]
    registry["experiments"][0].update(references=[], status="active")
    store._save(registry)
    assert not cleaner.plan()["objects"]


def test_partial_delete_retry_is_confined_and_path_reuse_blocks(legacy, monkeypatch):
    import cnequity.storage.lifecycle_experiments as module

    store, cleaner, source, _, _ = legacy
    cleaner.apply(cleaner.plan()["plan_id"])
    mature(store)
    plan = cleaner.plan(phase="purge")
    original = module.shutil.rmtree

    def fail(path):
        (path / "input").unlink()
        raise OSError("interrupted")

    monkeypatch.setattr(module.shutil, "rmtree", fail)
    with pytest.raises(OSError, match="interrupted"):
        cleaner.apply(plan["plan_id"], maintenance=True)
    event = store.root / "experiment-purges" / f"{plan['plan_id']}.json"
    assert json.loads(event.read_text())["status"] == "error"
    with pytest.raises(LifecycleError, match="unfinished"):
        cleaner.plan()
    source.mkdir()
    (source / "new-input").write_bytes(b"new experiment")
    with pytest.raises(LifecycleError, match="reused"):
        cleaner.apply(plan["plan_id"], maintenance=True)
    (source / "new-input").unlink()
    source.rmdir()
    monkeypatch.setattr(module.shutil, "rmtree", original)
    assert cleaner.apply(plan["plan_id"], maintenance=True)["status"] == "complete"


def test_archive_corruption_blocks_marking(legacy):
    _, cleaner, source, artifact, _ = legacy
    (Path(artifact["path"]) / "data/input").unlink()
    with pytest.raises(LifecycleError):
        cleaner.plan()
    assert source.exists()


def test_legacy_experiment_running_manifest_blocks_purge(legacy, tmp_path):
    import sqlite3

    store, cleaner, source, _, _ = legacy
    (source / "meta").mkdir()
    with sqlite3.connect(source / "meta/manifest.db") as db:
        db.execute("CREATE TABLE ingestion_runs (run_id TEXT, status TEXT)")
        db.execute("INSERT INTO ingestion_runs VALUES ('trial-worker', 'running')")
    ArtifactStore(store).archive("experiment/test", tmp_path / "archives")
    cleaner.apply(cleaner.plan()["plan_id"])
    mature(store)
    plan = cleaner.plan(phase="purge")
    with pytest.raises(LifecycleError, match="Ingestion is running"):
        cleaner.apply(plan["plan_id"], maintenance=True)
    assert source.exists()
