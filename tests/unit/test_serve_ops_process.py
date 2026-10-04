"""Cancellation must verify an identity before sending a termination signal."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from cnequity import windows_process
from cnequity.serve.ops import job, runner
from cnequity.serve.ops.catalog import OpsError
from cnequity.serve.ops.records import atomic_write, cancel_path


@pytest.mark.parametrize("created,matched", [("old", False), (None, False), ("current", True)])
def test_windows_taskkill_only_runs_while_verified_handle_is_held(
    tmp_path, monkeypatch, created, matched
):
    record = tmp_path / "job.json"
    atomic_write(record, {"state": "running", "pid": 123, "process_created_at": created})
    monkeypatch.setattr(runner, "IS_WINDOWS", True)
    held = []

    @contextmanager
    def verify(pid, expected):
        assert pid == 123
        assert expected == created
        held.append(True)
        try:
            yield expected == "current"
        finally:
            held.pop()

    calls = []

    def taskkill(argv, **kwargs):
        assert held == [True], "identity must stay pinned during taskkill"
        assert argv == ["taskkill", "/T", "/F", "/PID", "123"]
        calls.append(argv)

    monkeypatch.setattr(windows_process, "verified_process", verify)
    monkeypatch.setattr(runner.subprocess, "run", taskkill)
    runner._escalate("job", record)
    assert bool(calls) is matched
    assert held == []


def test_windows_refuses_cancel_without_identity_and_writes_no_marker(config, monkeypatch):
    service = runner.OpsService(config, storage_busy=lambda: False)
    job_id = "ab" * 16
    monkeypatch.setattr(service, "get_job", lambda _: {"state": "running", "pid": 123})
    monkeypatch.setattr(runner, "IS_WINDOWS", True)
    monkeypatch.setattr(runner, "same_job", lambda pid, job_id, created: False)
    with pytest.raises(OpsError, match="未发送终止信号"):
        service.cancel(job_id, requested_by="test")
    assert not cancel_path(config, job_id).exists()


def test_posix_cancel_refuses_a_process_with_another_command_line(tmp_path, monkeypatch):
    record = tmp_path / "job.json"
    atomic_write(record, {"state": "running", "pid": 123})
    monkeypatch.setattr(runner, "IS_WINDOWS", False)
    monkeypatch.setattr(runner, "same_job", lambda pid, job_id: False)
    signals = []
    monkeypatch.setattr(runner, "_signal", lambda pid, sig: signals.append(sig))
    runner._escalate("old-job", record)
    assert signals == []


def test_posix_identity_check_handles_exit_during_command_lookup(monkeypatch):
    from cnequity.orchestrator import scheduler_lock

    monkeypatch.setattr(job, "IS_WINDOWS", False)
    monkeypatch.setattr(scheduler_lock, "pid_alive", lambda pid: True)

    def exited(pid):
        raise FileNotFoundError("process exited")

    monkeypatch.setattr(job, "command_line_of", exited)
    assert job.same_job(123, "job") is False


def test_windows_same_job_needs_a_matching_creation_time(monkeypatch):
    from cnequity.orchestrator import scheduler_lock

    monkeypatch.setattr(job, "IS_WINDOWS", True)
    monkeypatch.setattr(scheduler_lock, "pid_alive", lambda pid: True)

    @contextmanager
    def verify(pid, expected):
        yield expected == "current"

    monkeypatch.setattr(windows_process, "verified_process", verify)
    assert job.same_job(123, "job", "current")
    assert not job.same_job(123, "job", "old")
    assert not job.same_job(123, "job")
