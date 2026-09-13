from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from cnequity.config import Config
from cnequity.domain.rate_limit import RateLimiter, SourceConcurrencyLimiter


def _source_family(source: str) -> str:
    """Map endpoint-specific pacing names to one vendor concurrency budget."""
    text = str(source).strip().lower()
    if text.startswith("ths"):
        return "ths"
    if text.startswith("tdx"):
        return "tdx_protocol"
    if text.startswith("cninfo"):
        return "cninfo"
    if text.startswith("eastmoney") or text in {"em", "datacenter"}:
        return "eastmoney"
    if text.startswith("sina"):
        return "sina"
    return text


@dataclass
class SourceRateLimiters:
    config: Config
    _limiters: dict[str, RateLimiter] = field(default_factory=dict, init=False, repr=False)
    _concurrency: dict[str, SourceConcurrencyLimiter] = field(
        default_factory=dict, init=False, repr=False
    )
    _request_local: threading.local = field(default_factory=threading.local, init=False, repr=False)

    def __post_init__(self) -> None:
        state_dir = self.config.meta_root / "rate_limits"
        if self.config.tdx_enabled:
            interval = self.config.tdx_min_interval_ms / 1000.0
            self._limiters["tdx_protocol"] = RateLimiter(
                "tdx_protocol",
                interval,
                state_dir,
                lock_timeout=self.config.tdx_lock_timeout_sec,
            )
        for source, interval in self.config.source_intervals.items():
            self._limiters[source] = RateLimiter(source, interval, state_dir)

        # Every source with an interval gets a default cap as well.  The
        # default follows the legacy scheduler budget, while an explicit
        # source_concurrency/http_workers/source_workers value narrows it.
        # Endpoint aliases (ths_pages/ths_bonus, for example) share one
        # vendor-wide ledger so independent DAG steps cannot exceed the cap in
        # aggregate.
        sources = set(self._limiters) | set(self.config.source_concurrency)
        sources |= set(self.config.http_workers) | set(self.config.source_workers)
        sources.add("tdx_protocol")
        for source in sources:
            family = _source_family(source)
            if family in self._concurrency:
                continue
            limit = self._configured_limit(family, sources)
            self._concurrency[family] = SourceConcurrencyLimiter(
                family,
                max(1, int(limit)),
                state_dir,
                lock_timeout=getattr(self.config, "tdx_lock_timeout_sec", 15.0),
            )

    def _configured_limit(self, family: str, sources: set[str] | None = None) -> int:
        """Resolve one deterministic vendor-wide cap from all aliases.

        ``ths``, ``ths_pages`` and ``ths_bonus`` are separate pacing lanes but
        one upstream service.  Taking the narrowest explicitly configured
        value guarantees that whichever alias initializes the family first
        cannot accidentally discard a stricter sibling setting.
        """
        names = {family}
        names.update(source for source in (sources or set()) if _source_family(source) == family)
        values: list[int] = []
        for mapping in (
            self.config.source_concurrency,
            self.config.http_workers,
            self.config.source_workers,
        ):
            values.extend(int(mapping[name]) for name in names if name in mapping)
        return max(1, min(values) if values else int(self.config.workers))

    def wait(self, source: str) -> None:
        limiter = self._limiters.get(source)
        if limiter is not None:
            limiter.wait()

    def defer(self, source: str, seconds: float) -> None:
        """Apply a vendor-wide cooling-off deadline to every pacing alias."""
        family = _source_family(source)
        matching = [
            limiter for name, limiter in self._limiters.items() if _source_family(name) == family
        ]
        for limiter in matching:
            limiter.defer(seconds)

    def _get_concurrency(self, source: str) -> SourceConcurrencyLimiter:
        family = _source_family(source)
        limiter = self._concurrency.get(family)
        if limiter is None:
            # A source can be used by a lazy adapter without a min-interval
            # entry.  Materialize its cap on demand so it still participates
            # in the global in-flight contract.
            state_dir = self.config.meta_root / "rate_limits"
            limit = self._configured_limit(family, {source})
            limiter = SourceConcurrencyLimiter(
                family,
                limit,
                state_dir,
                lock_timeout=getattr(self.config, "tdx_lock_timeout_sec", 15.0),
            )
            self._concurrency[family] = limiter
        return limiter

    @contextmanager
    def slot(
        self,
        source: str,
        *,
        metrics: dict | None = None,
        timeout: float | None = None,
    ) -> Iterator[None]:
        with self._get_concurrency(source).slot(metrics=metrics, timeout=timeout):
            yield

    @contextmanager
    def request(
        self,
        source: str,
        *,
        metrics: dict | None = None,
        timeout: float | None = None,
    ) -> Iterator[None]:
        """Pace, then hold the vendor slot across one request."""
        family = _source_family(source)
        active = getattr(self._request_local, "active", None)
        if active is None:
            active = set()
            self._request_local.active = active
        # ``SourceConcurrencyLimiter.slot`` is itself re-entrant, but skip the
        # second pacing reservation as well: a nested helper is part of the
        # same caller operation, not a new request start.
        if family in active:
            with self.slot(source, metrics=metrics, timeout=timeout):
                yield
            return
        self.wait(source)
        active.add(family)
        try:
            with self.slot(source, metrics=metrics, timeout=timeout):
                yield
        finally:
            active.discard(family)
