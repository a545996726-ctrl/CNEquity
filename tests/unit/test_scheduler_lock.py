"""The mkdir lock shared by the shell pipelines and the operations page."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cnequity.config import Config
from cnequity.config.bootstrap import path_for_toml
from cnequity.orchestrator.scheduler_lock import (
    SchedulerLockError,
    lock_directory,
    scheduler_lock,
    scheduler_lock_holder,
)

ROOT = Path(__file__).resolve().parents[2]


def test_lock_directory_follows_the_data_root_unless_overridden(tmp_path, monkeypatch):
    config = Config(data_root=tmp_path / "lake")
    monkeypatch.delenv("CNE_SCHEDULER_LOCK_DIR", raising=False)
    monkeypatch.delenv("CNE_LOCK_DIR", raising=False)
    assert lock_directory(config) == (tmp_path / "lake" / "locks").resolve()
    monkeypatch.setenv("CNE_SCHEDULER_LOCK_DIR", str(tmp_path / "custom"))
    assert lock_directory(config) == (tmp_path / "custom").resolve()


def test_a_dead_owner_is_reclaimed_and_a_live_one_blocks(tmp_path):
    root = tmp_path / "locks"
    stale = root / "daily.lock"
    stale.mkdir(parents=True)
    (stale / "pid").write_text(f"{2**31 - 1}\n", encoding="utf-8")
    with scheduler_lock(root, "daily"):
        assert scheduler_lock_holder(root, "daily") == os.getpid()
    assert scheduler_lock_holder(root, "daily") is None


def test_another_process_blocks_until_it_exits(tmp_path):
    root = tmp_path / "locks"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from cnequity.orchestrator.scheduler_lock import scheduler_lock\n"
            "with scheduler_lock(Path(sys.argv[1]), 'events'):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(30)\n",
            str(root),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdout.readline().strip() == "held"
    with pytest.raises(SchedulerLockError) as caught:
        with scheduler_lock(root, "events"):
            pass
    assert caught.value.code == 1
    child.kill()
    child.wait(timeout=5)
    with scheduler_lock(root, "events"):
        assert scheduler_lock_holder(root, "events") == os.getpid()


@pytest.mark.skipif(sys.platform == "win32", reason="Unix shell scripts")
def test_the_shell_default_is_the_configured_data_root(tmp_path):
    data = tmp_path / "lake"
    path = tmp_path / "cnequity.toml"
    path.write_text(f'[data]\nroot = "{path_for_toml(data)}"\n', encoding="utf-8")
    script = tmp_path / "lock.sh"
    script.write_text(
        f"""#!/bin/bash
set -u
unset CNE_SCHEDULER_LOCK_DIR
unset CNE_LOCK_DIR
export CNE_CONFIG="{path}"
export CNE_PYTHON="{sys.executable}"
. "{ROOT / "scripts" / "scheduler" / "scheduler_lock.sh"}"
scheduler_lock_acquire "{tmp_path}" daily
printf '%s\\n' "$SCHEDULER_LOCK_DIR"
scheduler_lock_release
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    env = os.environ.copy()
    env.pop("CNE_SCHEDULER_LOCK_DIR", None)
    env.pop("CNE_LOCK_DIR", None)
    done = subprocess.run([str(script)], capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str((data / "locks" / "daily.lock").resolve())
    assert not (data / "locks" / "daily.lock").exists()
