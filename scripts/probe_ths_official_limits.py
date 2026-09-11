#!/usr/bin/env python
"""Find the 同花顺 official API's real pacing budget. Measures, writes nothing.

Phase 3 of ``docs/development/ths-official-integration.md`` sends roughly 11,200
requests (5,600 symbols x balance sheet + cash flow). The service publishes no
threshold — only "no cumulative cap, arrange requests reasonably, back off if
throttled" — and the deepest run so far was 55 requests at 0.4s spacing, which
never throttled. That says nothing about what 11,200 will do.

So this ramps politely and stops at the first sign of throttling, which is what
the upstream guidance asks for. It never hammers: the ramp halts on the first
step that throttles, total requests are capped, and concurrency stops at 8.

**It probes the real Phase 3 endpoint**, not a cheap one. A limit measured on
``/calendar/trading-days`` (no parameters, almost certainly edge-cached) would
not transfer to a per-symbol financial statement query.

    HITHINK_FINANCE_API_KEY=... .venv/bin/python scripts/probe_ths_official_limits.py

Not part of the package. Delete it once the pacing is recorded in the plan.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import polars as pl

from cnequity.adapters.ths_official.client import (
    BASE_URL,
    CODE_OK,
    CODE_RATE_LIMITED,
)
from cnequity.config import load_config
from cnequity.query.parquet_scan import scan_parquet_root

ENDPOINT = "/api/a-share/financials/cash-flow-statements"
PHASE3_REQUESTS = 11_200


@dataclass
class StepResult:
    label: str
    workers: int
    interval: float
    sent: int = 0
    ok: int = 0
    throttled: int = 0
    other: int = 0
    latencies: list[float] = field(default_factory=list)
    wall_seconds: float = 0.0

    @property
    def achieved_rps(self) -> float:
        return self.sent / self.wall_seconds if self.wall_seconds else 0.0

    def row(self) -> str:
        lat = sorted(self.latencies)

        def pct(p: float) -> float:
            return lat[min(int(len(lat) * p), len(lat) - 1)] if lat else float("nan")

        return (
            f"{self.label:<22} sent={self.sent:>4} ok={self.ok:>4} "
            f"throttled={self.throttled:>3} other={self.other:>3} "
            f"rps={self.achieved_rps:>5.2f} "
            f"p50={pct(0.50):>5.2f}s p95={pct(0.95):>5.2f}s"
        )


def load_symbols(count: int) -> list[str]:
    """Live stocks, widely spread so no server-side per-symbol cache flatters us."""
    config = load_config("configs/cnequity.toml")
    frame = (
        scan_parquet_root(config.curated_root / "instruments", dataset="instruments")
        .filter((pl.col("asset_type") == "stock") & pl.col("delist_date").is_null())
        .select("symbol")
        .collect()
        .get_column("symbol")
        .to_list()
    )
    if not frame:
        raise SystemExit("no live instruments in the lake")
    stride = max(1, len(frame) // count)
    return frame[::stride][:count]


def one_request(client: httpx.Client, symbol: str) -> tuple[int | None, float]:
    started = time.monotonic()
    try:
        response = client.get(
            ENDPOINT,
            params={"thscode": symbol, "period": "quarterly", "limit": 20},
        )
    except httpx.HTTPError:
        return None, time.monotonic() - started
    elapsed = time.monotonic() - started
    if response.status_code != 200:
        return -response.status_code, elapsed
    try:
        return response.json().get("code"), elapsed
    except ValueError:
        return None, elapsed


def run_step(
    client: httpx.Client,
    symbols: list[str],
    *,
    label: str,
    workers: int,
    interval: float,
    count: int,
) -> StepResult:
    result = StepResult(label=label, workers=workers, interval=interval)
    started = time.monotonic()

    def task(index: int) -> tuple[int | None, float]:
        if interval and workers == 1 and index:
            time.sleep(interval)
        return one_request(client, symbols[index % len(symbols)])

    if workers == 1:
        outcomes = [task(i) for i in range(count)]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = list(pool.map(task, range(count)))

    result.wall_seconds = time.monotonic() - started
    for code, elapsed in outcomes:
        result.sent += 1
        result.latencies.append(elapsed)
        if code == CODE_OK:
            result.ok += 1
        elif code == CODE_RATE_LIMITED:
            result.throttled += 1
        else:
            result.other += 1
    return result


def measure_recovery(client: httpx.Client, symbol: str, *, budget: int = 6) -> float | None:
    """After a throttle, how long until the service answers normally again?

    Phase 3 needs this to size its backoff: a limiter that clears in a second
    wants a different retry curve from one that parks the caller for a minute.
    """
    waited = 0.0
    for attempt in range(budget):
        gap = 2.0**attempt
        time.sleep(gap)
        waited += gap
        code, _ = one_request(client, symbol)
        if code == CODE_OK:
            return waited
    return None


def run_soak(key: str, symbols: list[str], *, seconds: int, workers: int) -> StepResult:
    """Hold a steady rate long enough for a sliding-window limiter to show itself.

    The ramp above only proves a burst is tolerated. A limiter measured per
    minute or per hour stays invisible for the 320 requests the ramp sends, and
    Phase 3 sends 11,200 — so the question that actually matters is whether the
    rate survives being sustained, and where in the run it first bites.
    """
    result = StepResult(label=f"soak {workers}w/{seconds}s", workers=workers, interval=0.0)
    deadline = time.monotonic() + seconds
    started = time.monotonic()
    first_throttle_at: float | None = None
    index = 0
    lock = __import__("threading").Lock()

    def worker(slot: int) -> None:
        nonlocal index, first_throttle_at
        with httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30.0, connect=15.0),
            headers={"X-api-key": key},
        ) as client:
            while time.monotonic() < deadline:
                with lock:
                    symbol = symbols[index % len(symbols)]
                    index += 1
                code, elapsed = one_request(client, symbol)
                with lock:
                    result.sent += 1
                    result.latencies.append(elapsed)
                    if code == CODE_OK:
                        result.ok += 1
                    elif code == CODE_RATE_LIMITED:
                        result.throttled += 1
                        if first_throttle_at is None:
                            first_throttle_at = time.monotonic() - started
                    else:
                        result.other += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, range(workers)))
    result.wall_seconds = time.monotonic() - started
    print(result.row())
    if first_throttle_at is not None:
        print(
            f"  first throttle after {first_throttle_at:.0f}s "
            f"(~{int(first_throttle_at * result.achieved_rps)} requests)"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-step", type=int, default=40, help="requests per step")
    parser.add_argument("--max-requests", type=int, default=600, help="hard cap for the whole run")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--soak",
        type=int,
        default=0,
        metavar="SECONDS",
        help="after the ramp, hold a steady rate for this long to catch a sliding window",
    )
    parser.add_argument("--soak-workers", type=int, default=4)
    args = parser.parse_args()

    key = os.environ.get("HITHINK_FINANCE_API_KEY", "").strip()
    if not key:
        print("HITHINK_FINANCE_API_KEY is not set")
        return 2

    symbols = load_symbols(200)
    print(f"probing {ENDPOINT} with {len(symbols)} distinct symbols\n")

    # Sequential pacing first, then concurrency. Stop at the first throttle:
    # the point is to find the edge, not to sit on it.
    plan = [
        ("serial 1.0s (1/s)", 1, 1.0),
        ("serial 0.5s (2/s)", 1, 0.5),
        ("serial 0.25s (4/s)", 1, 0.25),
        ("serial 0.1s (10/s)", 1, 0.1),
        ("serial no delay", 1, 0.0),
        ("2 workers", 2, 0.0),
        ("4 workers", 4, 0.0),
        ("8 workers", 8, 0.0),
    ]

    results: list[StepResult] = []
    spent = 0
    throttled_at: StepResult | None = None
    with httpx.Client(
        base_url=BASE_URL,
        timeout=httpx.Timeout(30.0, connect=15.0),
        headers={"X-api-key": key},
    ) as client:
        for label, workers, interval in plan:
            if spent + args.per_step > args.max_requests:
                print(f"stopping: request cap {args.max_requests} reached")
                break
            step = run_step(
                client,
                symbols,
                label=label,
                workers=workers,
                interval=interval,
                count=args.per_step,
            )
            spent += step.sent
            results.append(step)
            print(step.row())
            if step.throttled:
                throttled_at = step
                print(f"\n  throttled at '{label}' — stopping the ramp and backing off")
                break

        recovery = None
        if throttled_at is not None:
            recovery = measure_recovery(client, symbols[0])
            print(
                f"  recovery: {recovery:.0f}s"
                if recovery is not None
                else "  recovery: still throttled after ~63s"
            )

    if args.soak and throttled_at is None:
        print(f"\n--- soak: {args.soak_workers} workers for {args.soak}s ---")
        soak = run_soak(key, symbols, seconds=args.soak, workers=args.soak_workers)
        results.append(soak)
        if soak.throttled:
            throttled_at = soak

    print("\n--- verdict ---")
    clean = [r for r in results if not r.throttled and not r.other]
    if throttled_at is None:
        best = max(clean, key=lambda r: r.achieved_rps) if clean else None
        if best is None:
            print("no clean step; inspect the errors above")
            return 1
        print(f"no throttling up to {best.achieved_rps:.2f} req/s ({best.label}).")
        # Two thirds of the highest rate that was never refused: the ceiling is
        # unknown, so leave room rather than run at the edge of what was tried.
        safe = best.achieved_rps * 0.66
    else:
        safe_steps = [r for r in clean if r.achieved_rps < throttled_at.achieved_rps]
        ceiling = max((r.achieved_rps for r in safe_steps), default=0.5)
        print(
            f"throttled at {throttled_at.achieved_rps:.2f} req/s ({throttled_at.label}); "
            f"highest clean rate {ceiling:.2f} req/s"
        )
        safe = ceiling * 0.66

    hours = PHASE3_REQUESTS / safe / 3600 if safe else float("inf")
    print(f"recommended sustained rate: {safe:.2f} req/s")
    print(f"Phase 3 ({PHASE3_REQUESTS:,} requests) would take about {hours:.1f}h at that rate")
    if hours > 6:
        print("  -> long enough that the backfill needs checkpointing and resume")

    if args.json_out:
        args.json_out.write_text(
            json.dumps(
                {
                    "endpoint": ENDPOINT,
                    "measured_at": date.today().isoformat(),
                    "steps": [
                        {
                            "label": r.label,
                            "workers": r.workers,
                            "interval": r.interval,
                            "sent": r.sent,
                            "ok": r.ok,
                            "throttled": r.throttled,
                            "other": r.other,
                            "rps": round(r.achieved_rps, 3),
                            "p50_seconds": round(statistics.median(r.latencies), 3)
                            if r.latencies
                            else None,
                        }
                        for r in results
                    ],
                    "recommended_rps": round(safe, 3),
                    "phase3_hours": round(hours, 2),
                },
                indent=2,
            )
        )
        print(f"\njson -> {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
