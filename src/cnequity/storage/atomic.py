"""Atomic writes for curated/derived data and quality metadata."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import polars as pl

# Windows refuses ``os.replace`` while another handle (DuckDB, Explorer preview,
# AV) holds the destination. A short backoff covers the common "query just
# closed" race; persistent locks surface as FileInUseError with a hint.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SEC = 0.05
IS_WINDOWS = sys.platform == "win32"


class FileInUseError(PermissionError):
    """Windows kept denying a replace because another program holds the path."""


class RollbackIncompleteError(RuntimeError):
    """A swap failed and putting the original back failed too; data sits in a backup."""


def replace_with_retry(tmp: Path | str, path: Path | str) -> None:
    """``os.replace`` that rides out transient Windows sharing denials."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            if attempt + 1 < _REPLACE_ATTEMPTS:
                time.sleep(_REPLACE_BACKOFF_SEC * (2**attempt))
                continue
            if not IS_WINDOWS:
                raise
            raise FileInUseError(
                exc.errno,
                f"{path} 正被其他程序占用，重试 {_REPLACE_ATTEMPTS} 次后仍无法替换。"
                "常见原因：Jupyter/DuckDB/polars 正在读取这份数据、资源管理器打开了该目录，"
                "或杀毒/同步软件正在扫描。关闭占用的程序后重跑即可",
                str(tmp),
                None,
                str(path),
            ) from exc


def swap_with_backup(staged: Path, target: Path, backup: Path) -> None:
    """Replace *target* with *staged*, keeping the old tree at *backup*.

    The caller removes *backup* after success. If promoting *staged* fails and
    the original cannot be moved back either, the original stays at *backup*
    and the error names it, so no data is silently lost.
    """
    replace_with_retry(target, backup)
    try:
        replace_with_retry(staged, target)
    except BaseException:
        try:
            replace_with_retry(backup, target)
        except OSError as restore_exc:
            raise RollbackIncompleteError(
                f"替换 {target} 失败，且未能还原原数据；原数据完整保存在 {backup}。"
                f"关闭占用的程序后，把它改名回 {target.name} 即可恢复"
            ) from restore_exc
        raise


def write_parquet_atomic(path: Path, df: pl.DataFrame, **kwargs) -> Path:
    """Write *df* to *path* via a unique same-directory temp file.

    A unique temporary name matters when two independent workers refresh the
    same cache or derived artifact: a fixed ``target.tmp`` lets them overwrite
    each other's in-progress footer before either ``os.replace`` runs.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.write_parquet(tmp, **kwargs)
        # Windows rejects fsync on a read-only file descriptor with EBADF.
        # Reopen read/write so the durability barrier works on every platform.
        with tmp.open("r+b") as handle:
            os.fsync(handle.fileno())
        replace_with_retry(tmp, path)
        return path
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def write_json_atomic(path: Path, payload: Any, **kwargs: Any) -> Path:
    """Serialize JSON to a unique temp file, then atomically replace *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, **kwargs)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp, path)
        return path
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
