"""Keep this host's IP off EastMoney's ban lists: push2 and datacenter.

push2 / push2his / push2delay ban an egress IP that requests too hard, and the
ban escalates: from 2026-08 the main push2 pool refused this host while the
backup hosts still served it, the backups were hit harder to compensate, and
on 2026-09-22 they refused it too (the first day every EastMoney group ran
automatically). A failover to another host is therefore the wrong reaction to
a refusal — it spends the next host's goodwill on the same request pattern.

datacenter-web serves ~20 datasets (and, since 2026-09-26, valuation), so a
ban there would cost far more. It has never refused this IP — no "busy"
message and no 403/429 in the logs through 2026-09-26, at 0.5 s / 4 in flight
— but it does time out on individual slow reports (RPT_SHAREBONUS_DET,
2026-09-17), which is not a refusal and must not close the whole host.

Rules, enforced before a request leaves the process (every process shares one
ledger, ``meta/state/eastmoney_guard.json``, reset at local midnight):

==============  ==========================  ===================================
rule            push2                       datacenter
==============  ==========================  ===================================
pause           ``push2_paused``            —
breaker trips   first refusal               3 refusals in a row (a success
                                            resets the count); timeouts are
                                            not refusals here
refusal         403/429/5xx, any transport  403/429/5xx, connection refused /
                error but a local proxy     reset / dropped, "busy" retries
                                            exhausted
daily budget    ``push2_daily_budget``      ``datacenter_daily_budget``
                                            (0 = count only)
==============  ==========================  ===================================

A tripped breaker closes that host family until midnight, across processes.
A refused request raises a :class:`EastMoneyHostBlockedError`, an
``httpx.ConnectError``, so every caller already treats it as a dead route:
fail fast, no retry, its own fallbacks run. Delete the ledger to clear a
breaker by hand.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from cnequity.file_lock import exclusive_lock

if TYPE_CHECKING:
    from cnequity.config import Config

logger = logging.getLogger(__name__)

# Statuses that mean "this IP is being refused", not "this request was bad".
_TRIP_STATUSES = frozenset({403, 429})


class EastMoneyHostBlockedError(httpx.ConnectError):
    """An EastMoney request refused locally, before anything was sent."""


class Push2BlockedError(EastMoneyHostBlockedError):
    """A push2 request refused locally."""


class Push2PausedError(Push2BlockedError):
    """Refused by ``[sources.eastmoney].push2_paused``."""


class Push2BreakerOpenError(Push2BlockedError):
    """Refused because push2 already refused this IP today."""


class Push2BudgetExhaustedError(Push2BlockedError):
    """Refused because today's push2 request budget is spent."""


class DatacenterBlockedError(EastMoneyHostBlockedError):
    """A datacenter request refused locally."""


class DatacenterBreakerOpenError(DatacenterBlockedError):
    """Refused because datacenter refused this IP repeatedly today."""


class DatacenterBudgetExhaustedError(DatacenterBlockedError):
    """Refused because today's datacenter request budget is spent."""


@dataclass(frozen=True)
class _Profile:
    name: str
    hosts: tuple[str, ...]
    breaker_attr: str
    budget_attr: str
    strikes_attr: str | None
    timeouts_are_refusals: bool
    breaker_error: type[EastMoneyHostBlockedError]
    budget_error: type[EastMoneyHostBlockedError]

    def matches(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def strikes_needed(self, config: Config) -> int:
        if self.strikes_attr is None:
            return 1
        return max(1, int(getattr(config, self.strikes_attr, 3) or 3))


PUSH2 = _Profile(
    name="push2",
    hosts=(
        "push2.eastmoney.com",
        "push2delay.eastmoney.com",
        "push2his.eastmoney.com",
        "push2ex.eastmoney.com",
    ),
    breaker_attr="eastmoney_push2_breaker",
    budget_attr="eastmoney_push2_daily_budget",
    strikes_attr=None,
    timeouts_are_refusals=True,
    breaker_error=Push2BreakerOpenError,
    budget_error=Push2BudgetExhaustedError,
)
DATACENTER = _Profile(
    name="datacenter",
    hosts=("datacenter-web.eastmoney.com", "datacenter.eastmoney.com"),
    breaker_attr="eastmoney_datacenter_breaker",
    budget_attr="eastmoney_datacenter_daily_budget",
    strikes_attr="eastmoney_datacenter_breaker_strikes",
    timeouts_are_refusals=False,
    breaker_error=DatacenterBreakerOpenError,
    budget_error=DatacenterBudgetExhaustedError,
)
_PROFILES = (PUSH2, DATACENTER)


def profile_for(url: str) -> _Profile | None:
    return next((p for p in _PROFILES if p.matches(url)), None)


def is_push2_url(url: str) -> bool:
    return PUSH2.matches(url)


def is_datacenter_url(url: str) -> bool:
    return DATACENTER.matches(url)


def _today() -> date:
    """The local calendar day; breakers and budgets reset at midnight."""
    return datetime.now().astimezone().date()


def _state_path(config: Config) -> Path:
    return Path(config.meta_root) / "state" / "eastmoney_guard.json"


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
        state = {"day": today.isoformat()}
    for profile in _PROFILES:
        section = state.setdefault(profile.name, {})
        section.setdefault("requests", 0)
        section.setdefault("breaker", None)
        section.setdefault("strikes", 0)
    return state


def _save(path: Path, state: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _host(url: str) -> str:
    return urlparse(url).netloc


def admit(config: Config | None, url: str) -> None:
    """Refuse *url* locally, or count it against today's ledger and let it go.

    URLs outside the guarded hosts and bare (config-less) clients pass untouched.
    """
    if config is None:
        return
    profile = profile_for(url)
    if profile is None:
        return
    if profile is PUSH2 and getattr(config, "eastmoney_push2_paused", False):
        raise Push2PausedError(
            f"push2 paused by [sources.eastmoney].push2_paused; not sent: {_host(url)}"
        )
    breaker = bool(getattr(config, profile.breaker_attr, True))
    budget = int(getattr(config, profile.budget_attr, 0) or 0)
    with _locked(config) as path:
        state = _load(path, _today())
        section = state[profile.name]
        tripped = section.get("breaker")
        if breaker and tripped:
            raise profile.breaker_error(
                f"{profile.name} breaker open since {tripped.get('at')} "
                f"({tripped.get('reason')} on {tripped.get('host')}); "
                f"not sent until tomorrow: {_host(url)}"
            )
        if budget > 0 and int(section["requests"]) >= budget:
            raise profile.budget_error(
                f"{profile.name} daily budget spent ({section['requests']}/{budget}); "
                f"not sent: {_host(url)}"
            )
        section["requests"] = int(section["requests"]) + 1
        _save(path, state)


def refusal_reason(
    *,
    status_code: int | None = None,
    exc: BaseException | None = None,
    url: str | None = None,
) -> str | None:
    """Why an outcome counts as the host refusing this IP, or None."""
    profile = profile_for(url) if url else PUSH2
    if exc is not None:
        if isinstance(exc, EastMoneyHostBlockedError | httpx.ProxyError):
            # Our own refusal, or a dead local proxy: neither is EastMoney talking.
            return None
        if isinstance(exc, httpx.TimeoutException) and not (
            profile is None or profile.timeouts_are_refusals
        ):
            return None
        if isinstance(exc, httpx.TransportError):
            return type(exc).__name__
        return None
    if status_code is not None and (status_code in _TRIP_STATUSES or status_code >= 500):
        return f"HTTP {status_code}"
    return None


def note_outcome(
    config: Config | None,
    url: str,
    *,
    status_code: int | None = None,
    exc: BaseException | None = None,
) -> None:
    """Record one request's outcome: a refusal strikes, a success clears strikes."""
    if config is None:
        return
    profile = profile_for(url)
    if profile is None:
        return
    reason = refusal_reason(status_code=status_code, exc=exc, url=url)
    if reason is None:
        if exc is None and profile.strikes_attr is not None:
            _clear_strikes(config, profile)
        return
    strike(config, url, reason)


def _clear_strikes(config: Config, profile: _Profile) -> None:
    with _locked(config) as path:
        state = _load(path, _today())
        if state[profile.name]["strikes"]:
            state[profile.name]["strikes"] = 0
            _save(path, state)


def strike(config: Config | None, url: str, reason: str) -> None:
    """Count one refusal; trip the host's breaker once enough have landed in a row."""
    if config is None:
        return
    profile = profile_for(url)
    if profile is None or not getattr(config, profile.breaker_attr, True):
        return
    needed = profile.strikes_needed(config)
    with _locked(config) as path:
        state = _load(path, _today())
        section = state[profile.name]
        if section.get("breaker"):
            return
        section["strikes"] = int(section["strikes"]) + 1
        tripped = section["strikes"] >= needed
        if tripped:
            section["breaker"] = {
                "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "reason": reason,
                "host": _host(url),
                "strikes": section["strikes"],
            }
        _save(path, state)
    if tripped:
        logger.warning(
            "%s breaker tripped: %s on %s; no %s request until tomorrow",
            profile.name,
            reason,
            _host(url),
            profile.name,
        )
    else:
        logger.warning(
            "%s refusal %d/%d: %s on %s",
            profile.name,
            section["strikes"],
            needed,
            reason,
            _host(url),
        )


def trip(config: Config | None, url: str, reason: str) -> None:
    """Close *url*'s host family for the rest of the local day, now."""
    if config is None:
        return
    profile = profile_for(url)
    if profile is None or not getattr(config, profile.breaker_attr, True):
        return
    with _locked(config) as path:
        state = _load(path, _today())
        section = state[profile.name]
        if section.get("breaker"):
            return
        section["breaker"] = {
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "reason": reason,
            "host": _host(url),
        }
        _save(path, state)
    logger.warning(
        "%s breaker tripped: %s on %s; no %s request until tomorrow",
        profile.name,
        reason,
        _host(url),
        profile.name,
    )


def breaker_enabled(config: Config | None) -> bool:
    """Whether the push2 breaker is on (the clist failover policy reads it)."""
    return config is not None and bool(getattr(config, PUSH2.breaker_attr, True))


def status(config: Config) -> dict[str, Any]:
    """Today's ledger, for reporting."""
    with _locked(config) as path:
        state = _load(path, _today())
    for profile in _PROFILES:
        state[profile.name]["budget"] = int(getattr(config, profile.budget_attr, 0) or 0)
    state["push2"]["paused"] = bool(getattr(config, "eastmoney_push2_paused", False))
    return state
