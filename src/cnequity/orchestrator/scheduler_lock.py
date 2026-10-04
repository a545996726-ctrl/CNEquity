"""The mkdir lock the shell pipelines use, as a Python context manager.

``scripts/scheduler/scheduler_lock.sh`` cannot call ``flock`` — macOS does not
ship it — so a scheduled run is a directory with a pid file. The dashboard's
daily and events jobs take the same directory. Two different lock files would
let a panel run and a launchd run both believe they own the session.

``CNE_SCHEDULER_LOCK_DIR`` and the older ``CNE_LOCK_DIR`` still win. Otherwise
the directory is ``{data.root}/locks``, which is where a ``cne serve`` process
can see it. The shell used to fall back to ``$REPO_ROOT/data/cnequity/locks``
and missed every lake whose root was not that default.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cnequity.config import Config

IS_WINDOWS = sys.platform == "win32"


class SchedulerLockError(RuntimeError):
    """``code`` matches the shell helper: 1 is busy, 2 is an I/O failure."""

    def __init__(self, message: str, *, code: int):
        super().__init__(message)
        self.code = code


def lock_directory(config: Config) -> Path:
    """Where both the shell and the panel look for ``daily.lock`` / ``events.lock``."""
    override = os.environ.get("CNE_SCHEDULER_LOCK_DIR") or os.environ.get("CNE_LOCK_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path(config.data_root).resolve() / "locks"


def _proc_state(stat_text: str) -> str | None:
    """The state field from ``/proc/<pid>/stat``. ``comm`` may contain spaces."""
    end = stat_text.rfind(")")
    if end < 0:
        return None
    fields = stat_text[end + 1 :].split()
    return fields[0] if fields else None


def _zombie(pid: int) -> bool:
    """An exited process whose parent has not waited is not doing work.

    ``os.kill(pid, 0)`` still succeeds for that pid, which made a cancelled
    panel job look running until the parent reaped it.
    """
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return _proc_state(text) == "Z"


def pid_alive(pid: int) -> bool:
    """Whether *pid* is a live process.

    ``os.kill(pid, 0)`` on Windows is not a probe: it terminates the process.
    A handle query is the equivalent of the shell's ``kill -0``.
    """
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if IS_WINDOWS:
        from cnequity.windows_process import pid_alive as windows_pid_alive

        return windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return not _zombie(pid)


def _read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.isdigit():
        return int(text)
    return None


def scheduler_lock_holder(root: Path, name: str) -> int | None:
    """Pid holding ``name``, or None. Does not create or reclaim the directory."""
    owner = _read_pid(Path(root) / f"{name}.lock" / "pid")
    if owner and pid_alive(owner):
        return owner
    return None


@contextmanager
def scheduler_lock(root: Path, name: str) -> Iterator[None]:
    """Hold the shell's ``name`` lock, reclaiming a pid that has gone away.

    The reclaim order is the shell's: rename the pid file aside, remove that
    marker, then ``rmdir`` only an empty directory. Two processes that read the
    same dead marker cannot both delete the directory the other just created.
    """
    root = Path(root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SchedulerLockError(f"无法创建调度锁目录 {root}（{exc}）", code=2) from exc
    lock_dir = root / f"{name}.lock"
    pid_file = lock_dir / "pid"
    held = False
    try:
        for _attempt in range(2):
            try:
                lock_dir.mkdir()
            except FileExistsError:
                owner = _read_pid(pid_file)
                if owner and pid_alive(owner):
                    raise SchedulerLockError(
                        f"调度锁 {name} 正被进程 {owner} 持有。", code=1
                    ) from None
                if owner:
                    reclaimed = lock_dir / f".reclaimed.{os.getpid()}"
                    try:
                        os.replace(pid_file, reclaimed)
                    except OSError as exc:
                        raise SchedulerLockError(f"调度锁 {name} 正被占用。", code=1) from exc
                    try:
                        reclaimed.unlink()
                        lock_dir.rmdir()
                    except OSError as exc:
                        raise SchedulerLockError(f"无法回收调度锁 {name}（{exc}）", code=2) from exc
                    continue
                raise SchedulerLockError(
                    f"调度锁 {name} 还没有 pid 文件，不能判断它是否已死。", code=1
                ) from None
            else:
                try:
                    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
                except OSError as exc:
                    try:
                        lock_dir.rmdir()
                    except OSError:
                        pass
                    raise SchedulerLockError(f"无法写入调度锁 {name}（{exc}）", code=2) from exc
                held = True
                break
        else:
            raise SchedulerLockError(f"调度锁 {name} 正被占用。", code=1)
        yield
    finally:
        if held:
            owner = _read_pid(pid_file)
            if owner == os.getpid():
                try:
                    pid_file.unlink()
                except OSError:
                    pass
                try:
                    lock_dir.rmdir()
                except OSError:
                    pass


def main(argv: list[str] | None = None) -> int:
    """Print the lock directory for one config. The shell helper calls this."""
    parser = argparse.ArgumentParser(description="Resolve the scheduler lock directory.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    from cnequity.config import load_config

    print(lock_directory(load_config(args.config)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
