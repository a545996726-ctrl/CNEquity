"""Browser approvals and deletion safety on isolated, tiny local lakes."""

import time
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from cnequity.cli.main import cli
from cnequity.config import Config
from cnequity.serve.app import create_app
from cnequity.storage.lifecycle import LifecycleStore, digest
from cnequity.storage.lifecycle.artifacts import ArtifactStore
from cnequity.storage.lifecycle.experiments import ExperimentRetirement
from cnequity.storage.revisions import RevisionStore


@pytest.fixture
def web(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "lake")
    writer = RevisionStore(cfg.meta_root, cfg.curated_root)
    source = cfg.curated_root / "daily_bars/part.parquet"
    source.parent.mkdir(parents=True)
    for i in range(7):
        pl.DataFrame({"close": [float(i)]}).write_parquet(source)
        writer.commit(
            "daily_bars",
            run_id=str(i),
            changed_files=[source],
            schema_version=1,
            contract_fingerprint="test",
        )
    store = LifecycleStore(cfg.meta_root)
    store.import_manifest(
        {
            "schema_version": 1,
            "meta_root": str(store.meta),
            "holds": {},
            "reference_roots": [],
            "reference_fingerprint": digest([]),
        }
    )

    class Earlier(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(timezone.utc) - timedelta(days=8)

    with monkeypatch.context() as patch:
        patch.setattr("cnequity.storage.lifecycle.datetime", Earlier)
        store.mark(store.plan()["plan_id"])
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    return client, cfg, store, writer


def headers(client):
    return {
        "Origin": "http://127.0.0.1",
        "X-CNE-Storage-CSRF": client.app.state.storage_maintenance.csrf,
    }


def wait(client, response):
    assert response.status_code == 202, response.text
    job = response.json()
    deadline = time.monotonic() + 10
    while job["status"] in {"checking", "executing"}:
        assert time.monotonic() < deadline, job
        time.sleep(0.01)
        job = client.get(f"/api/storage/jobs/{job['job_id']}").json()
    return job


def review(client, kind="revisions", phase="purge", **kwargs):
    return wait(
        client,
        client.post(
            "/api/storage/reviews",
            headers=headers(client),
            json={"kind": kind, "phase": phase, **kwargs},
        ),
    )


def confirm(client, job, **changes):
    body = {
        "review_id": job["job_id"],
        "confirmation_token": job["confirmation_token"],
        "confirmed": True,
        "maintenance_confirmed": True,
    }
    body.update(changes)
    return client.post("/api/storage/confirm", headers=headers(client), json=body)


def test_reading_and_review_never_delete_then_explicit_confirmation_does(web):
    client, cfg, store, writer = web
    current = writer.current_root("daily_bars")
    contents = (current / "part.parquet").read_bytes()
    original = store.registry()
    for _ in range(2):
        summary = client.get("/api/storage").json()
        assert sum(o["status"] == "due" for o in summary["objects"]) == 2
        assert sum(o["status"] == "protected" for o in summary["objects"]) == 6
    assert store.registry() == original
    job = review(client)
    assert len(job["objects"]) == 2
    assert len(store.inspect()["objects"]) == 8
    assert confirm(client, job, confirmed=False).status_code == 409
    assert confirm(client, job, maintenance_confirmed=False).status_code == 409
    assert confirm(client, job, confirmation_token="wrong").status_code == 409
    assert confirm(client, job, confirmed="true").status_code == 422
    result = wait(client, confirm(client, job))
    assert result["status"] == "complete", result
    assert len(store.inspect()["objects"]) == 6
    assert (current / "part.parquet").read_bytes() == contents
    assert writer.current_root("daily_bars") == current
    assert confirm(client, job).status_code == 409
    assert not client.app.state.storage_maintenance.gate.closed


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"Origin": "http://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"X-CNE-Storage-CSRF": "wrong"},
    ],
)
def test_origin_and_csrf_required(web, bad):
    client, _, store, _ = web
    supplied = {} if not bad else headers(client) | bad
    response = client.post("/api/storage/reviews", headers=supplied, json={"kind": "revisions"})
    assert response.status_code == 403
    assert not client.app.state.storage_maintenance.jobs
    assert len(store.inspect()["objects"]) == 8


def test_dns_rebinding_and_remote_auth(web):
    client, cfg, _, _ = web
    assert client.get("/api/storage", headers={"Host": "evil.example"}).status_code == 403
    remote = TestClient(create_app(cfg, token="secret"), base_url="http://remote.example")
    assert remote.get("/api/storage").status_code == 401
    assert remote.get("/api/storage", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_stale_review_fails_without_deletion(web):
    client, _, store, _ = web
    job = review(client)
    store.hold(job["objects"][0]["object_id"], "new dependency")
    result = wait(client, confirm(client, job))
    assert result["status"] == "error"
    assert len(store.inspect()["objects"]) == 8
    assert not client.app.state.storage_maintenance.gate.closed


def test_expired_confirmation_and_restart_are_rejected(web):
    client, cfg, store, _ = web
    job = review(client)
    client.app.state.storage_maintenance.jobs[job["job_id"]]["_expires"] = 0
    assert confirm(client, job).status_code == 409
    restarted = TestClient(create_app(cfg), base_url="http://127.0.0.1")
    assert confirm(restarted, job).status_code == 409
    assert len(store.inspect()["objects"]) == 8


def test_execution_drains_reads_and_blocks_new_ones_but_keeps_shell_available(web):
    client, _, _, _ = web
    assert client.get("/api/storage").status_code == 200
    job = review(client)
    gate = client.app.state.storage_maintenance.gate
    assert gate.enter()
    response = confirm(client, job)
    try:
        assert response.status_code == 202
        assert gate.closed
        cached = client.get("/api/storage").json()
        assert cached["inventory_paused"] is True
        assert cached["active_jobs"][0]["status"] == "executing"
        assert client.get("/api/datasets").status_code == 503
        assert client.get("/").status_code == 200
        assert client.get(f"/api/storage/jobs/{response.json()['job_id']}").status_code == 200
        assert (
            client.post(
                "/api/storage/reviews", headers=headers(client), json={"kind": "revisions"}
            ).status_code
            == 409
        )
    finally:
        gate.leave()
    assert wait(client, response)["status"] == "complete"


def test_mark_requires_confirmation_and_cannot_delete_before_grace(web):
    client, _, store, _ = web
    value = store.registry()
    value["pending"] = {}
    store._save(value)
    job = review(client, phase="mark")
    assert not store.registry()["pending"]
    assert wait(client, confirm(client, job, maintenance_confirmed=False))["status"] == "complete"
    assert len(store.registry()["pending"]) == 2
    empty = review(client)
    assert empty["objects"] == []
    assert confirm(client, empty).status_code == 409
    assert len(store.inspect()["objects"]) == 8


def test_experiment_web_delete_keeps_archive_and_cli_is_blocked(web, tmp_path, monkeypatch):
    client, cfg, store, _ = web
    source = tmp_path / "cne-old"
    source.mkdir()
    (source / "evidence").write_bytes(b"test evidence")
    value = store.registry()
    info = source.stat()
    value["experiments"] = [
        {
            "object_id": "experiment/test",
            "path": str(source),
            "directory_identity": [info.st_dev, info.st_ino],
            "status": "needs_classification",
            "references": [],
        }
    ]
    store._save(value)
    artifacts = ArtifactStore(store)
    artifacts.archive("experiment/test", tmp_path / "archives")
    cleaner = ExperimentRetirement(store)
    cleaner.apply(cleaner.plan()["plan_id"])
    value = store.registry()
    for p in value["experiment_pending"].values():
        p["marked_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        p["not_before"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    store._save(value)
    job = review(client, "experiments")
    assert len(job["objects"]) == 1
    monkeypatch.setattr("cnequity.cli.storage_cmds._cfg", lambda _: cfg)
    result = CliRunner().invoke(
        cli, ["storage", "experiment-apply", job["plan_id"], "--maintenance-window"]
    )
    assert result.exit_code == 1 and "网页" in result.output
    assert source.exists()
    result = wait(client, confirm(client, job))
    assert result["status"] == "complete", result
    assert not source.exists()
    assert artifacts.resolve(source / "evidence").read_bytes() == b"test evidence"


def test_failed_review_does_not_leave_service_busy(web, monkeypatch):
    client, _, _, _ = web
    service = client.app.state.storage_maintenance

    def fail(**kwargs):
        raise OSError("unreadable evidence")

    monkeypatch.setattr(service.store, "plan", fail)
    assert review(client)["status"] == "error"
    assert not service.busy and not service.gate.closed


def test_clean_without_dry_run_never_requests_deletion(web, monkeypatch):
    client, cfg, _, _ = web
    import cnequity.cli.maintain_cmds as commands

    monkeypatch.setattr(commands, "_cfg", lambda _: cfg)
    for name in [
        "clean_staging",
        "clean_source_snapshots",
        "clean_run_logs",
        "prune_revision_generations",
    ]:
        original = getattr(commands, name)

        def checked(*args, _original=original, **kwargs):
            assert kwargs["dry_run"] is True
            return _original(*args, **kwargs)

        monkeypatch.setattr(commands, name, checked)
    result = CliRunner().invoke(cli, ["run", "clean", "--force"])
    assert result.exit_code == 0, result.output


def test_dashboard_cannot_be_framed_for_clickjacking(web):
    client, _, _, _ = web
    response = client.get("/")
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_partial_failure_needs_another_web_review_and_confirmation(web, monkeypatch):
    import cnequity.storage.lifecycle.purge as purge

    client, _, store, _ = web
    job = review(client)

    def fail(path):
        next(path.rglob("*.parquet")).unlink()
        raise OSError("injected partial deletion")

    with monkeypatch.context() as patch:
        patch.setattr(purge.shutil, "rmtree", fail)
        assert wait(client, confirm(client, job))["status"] == "error"
    summary = client.get("/api/storage").json()
    assert summary["unfinished"] == [{"kind": "revisions", "plan_id": job["plan_id"]}]
    assert review(client)["status"] == "error"
    retry = review(client, resume_plan_id=job["plan_id"])
    assert retry["status"] == "ready" and retry["resuming"]
    assert retry["objects"] == job["objects"]
    assert confirm(client, retry, confirmed=False).status_code == 409
    assert wait(client, confirm(client, retry))["status"] == "complete"
    assert len(store.inspect()["objects"]) == 6


def test_background_scan_holds_the_same_maintenance_gate(web, monkeypatch):
    from threading import Event
    from types import SimpleNamespace

    client, _, _, _ = web
    view = client.app.state.view
    gate = client.app.state.storage_maintenance.gate
    entered, release, finished = Event(), Event(), Event()

    def scan(_):
        entered.set()
        assert release.wait(5)
        finished.set()
        return None

    monkeypatch.setattr(
        "cnequity.serve.lake.stats_freshness", lambda _: SimpleNamespace(stale=True)
    )
    monkeypatch.setattr("cnequity.serve.lake.refresh_stats_if_stale", scan)
    assert view.refresh_stats_in_background()
    try:
        assert entered.wait(5)
        assert gate.readers == 1
        gate.close()
        assert not view.refresh_stats_in_background()
    finally:
        release.set()
        assert finished.wait(5)
        gate.drain(timeout=5)
        gate.reopen()
    assert gate.readers == 0
