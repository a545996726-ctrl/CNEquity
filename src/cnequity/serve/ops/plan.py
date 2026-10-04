"""Read-only ``cne backfill --plan`` for the preview step.

Run out of process so a plan cannot change the serve process's config or
logging. Sixty seconds is a bound, not a promise that every plan is fast.
"""

from __future__ import annotations

import subprocess
import sys

from cnequity.serve.ops.catalog import OpsError
from cnequity.serve.ops.environment import command_environment

PLAN_TIMEOUT_SECONDS = 60


def describe_backfill(argv: list[str]) -> str:
    """Return the plan text, or raise ``OpsError`` when the plan itself fails."""
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "cnequity", *argv, "--plan"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=command_environment(),
            timeout=PLAN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpsError("回填预览超过 60 秒，没有启动。") from exc
    except OSError as exc:
        raise OpsError(f"无法启动回填预览（{exc}）。") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise OpsError(detail or "回填预览失败，没有启动。")
    return completed.stdout.strip()
