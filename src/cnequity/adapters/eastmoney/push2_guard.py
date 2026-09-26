"""Keep this host's IP off EastMoney push2's ban list.

push2 / push2his / push2delay ban an egress IP that requests too hard, and the
ban escalates: from 2026-08 the main push2 pool refused this host while the
backup hosts still served it, the backups were hit harder to compensate, and
on 2026-09-22 they refused it too (the first day every EastMoney group ran
automatically). A failover to another host is therefore the wrong reaction to
a refusal — it spends the next host's goodwill on the same request pattern.

Three local rules, enforced before a push2 request leaves the process:

* ``push2_paused`` refuses everything (a banned IP resting);
* the breaker: the first refusal (HTTP 403/429/5xx, a dropped or reset
  connection, a timeout) closes push2 for the rest of the local day, across
  every process, with no backup-host attempt;
* the daily budget: a hard cap on push2 requests per local day.

A refused request raises a :class:`Push2BlockedError`, an ``httpx.ConnectError``,
so every caller already treats it as a dead route: fail fast, no retry, its
own fallbacks run. State lives in ``meta/state/push2_guard.json``; delete it to
clear a tripped breaker by hand.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from cnequity.file_lock import exclusive_lock

if TYPE_CHECKING:
    from cnequity.config import Config

logger = logging.getLogger(__name__)

_PUSH2_HOST_MARKERS = (
    "push2.eastmoney.com",
    "push2delay.eastmoney.com",
    "push2his.eastmoney.com",
    "push2ex.eastmoney.com",
)
# Statuses that mean "this IP is being refused", not "this request was bad".
_TRIP_STATUSES = frozenset({403, 429})


class Push2BlockedError(httpx.ConnectError):
    """A push2 request refused locally, before anything was sent."""


class Push2PausedError(Push2BlockedError):
    """Refused by ``[sources.eastmoney].push2_paused``."""


class Push2BreakerOpenError(Push2BlockedError):
    """Refused because push2 already refused this IP today."""


class Push2BudgetExhaustedError(Push2BlockedError):
    """Refused because today's push2 request budget is spent."""


def is_push2_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == marker or host.endswith("." + marker) for marker in _PUSH2_HOST_MARKERS)


def _today() -> date:
    """The local calendar day; the breaker and the budget reset at midnight."""
    return datetime.now().astimezone().date()


def _state_path(config: Config) -> Path:
    return Path(config.meta_root) / "state" / "push2_guard.json"


@contextmanager
def _locked(config: Config):
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix(".lock"), timeout=60.0):
        yield path


def _load(path: Path, today: date) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict) or state.get("day") != today.isoformat():
        return {"day": today.isoformat(), "requests": 0, "breaker": None}
    state.setdefault("requests", 0)
    state.setdefault("breaker", None)
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _host(url: str) -> str:
    return urlparse(url).netloc


def admit(config: Config | None, url: str) -> None:
    """Refuse *url* locally, or count it against today's budget and let it go.

    Non-push2 URLs and bare (config-less) clients pass untouched.
    """
    if config is None or not is_push2_url(url):
        return
    if getattr(config, "eastmoney_push2_paused", False):
        raise Push2PausedError(
            f"push2 paused by [sources.eastmoney].push2_paused; not sent: {_host(url)}"
        )
    breaker = bool(getattr(config, "eastmoney_push2_breaker", True))
    budget = int(getattr(config, "eastmoney_push2_daily_budget", 0) or 0)
    if not breaker and budget <= 0:
        return
    today = _today()
    with _locked(config) as path:
        state = _load(path, today)
        tripped = state.get("breaker")
        if breaker and tripped:
            raise Push2BreakerOpenError(
                f"push2 breaker open since {tripped.get('at')} "
                f"({tripped.get('reason')} on {tripped.get('host')}); "
                f"not sent until tomorrow: {_host(url)}"
            )
        if budget > 0 and int(state["requests"]) >= budget:
            raise Push2BudgetExhaustedError(
                f"push2 daily budget spent ({state['requests']}/{budget}); not sent: {_host(url)}"
            )
        state["requests"] = int(state["requests"]) + 1
        _save(path, state)


def refusal_reason(
    *, status_code: int | None = None, exc: BaseException | None = None
) -> str | None:
    """Why a push2 outcome counts as a refusal of this IP, or None."""
    if exc is not None:
        if isinstance(exc, Push2BlockedError) or isinstance(exc, httpx.ProxyError):
            # Our own refusal, or a dead local proxy: neither is push2 talking.
            return None
        if isinstance(exc, httpx.TransportError):
            return type(exc).__name__
        return None
    if status_code is not None and (status_code in _TRIP_STATUSES or status_code >= 500):
        return f"HTTP {status_code}"
    return None


def trip(config: Config | None, url: str, reason: str) -> None:
    """Close push2 for the rest of the local day after a refusal."""
    if config is None or not is_push2_url(url):
        return
    if not getattr(config, "eastmoney_push2_breaker", True):
        return
    today = _today()
    with _locked(config) as path:
        state = _load(path, today)
        if state.get("breaker"):
            return
        state["breaker"] = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": reason,
            "host": _host(url),
        }
        _save(path, state)
    logger.warning(
        "push2 breaker tripped: %s on %s; no push2 request (any host) until tomorrow",
        reason,
        _host(url),
    )


def breaker_enabled(config: Config | None) -> bool:
    return config is not None and bool(getattr(config, "eastmoney_push2_breaker", True))


def status(config: Config) -> dict[str, Any]:
    """Today's guard state, for reporting."""
    with _locked(config) as path:
        state = _load(path, _today())
    state["budget"] = int(getattr(config, "eastmoney_push2_daily_budget", 0) or 0)
    state["paused"] = bool(getattr(config, "eastmoney_push2_paused", False))
    return state
