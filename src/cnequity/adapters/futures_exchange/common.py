"""Shared plumbing for the futures exchanges' own daily files.

Every exchange here publishes a file per session that lists every live
contract, traded or not, with its settlement price. What differs is how each
one says "there is no file for that day" — measured on a closed session
(2026-09-25): SHFE answers 404 with an HTML page, CZCE 404 with a
「当日无数据」 page, CFFEX a 302, GFEX 200 with a single all-zero 「总计」 row.
Adapters turn all of those into :class:`FuturesDayUnavailable`, so "absent"
means the same thing whichever exchange said it.

DCE answers every request with a JavaScript challenge (HTTP 412 carrying a
``$_ts`` bootstrap). That is an access control, and this project does not work
around it: :class:`FuturesSourceBlocked` exists so a challenge is never read
as "no data".
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date

import httpx
import polars as pl

from cnequity.domain.rate_limit import source_request

logger = logging.getLogger(__name__)

#: Registry and rate-limit label for every official futures exchange file.
SOURCE = "futures_exchange"

_TIMEOUT_SECONDS = 60.0
_NETWORK_ATTEMPTS = 2
_RETRY_PAUSE_SECONDS = 2.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}
_CACHE_ENTRIES = 16
_cache: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
_cache_lock = threading.Lock()


class FuturesDayUnavailable(RuntimeError):
    """The exchange has no file for this session (closed, or not yet published)."""


class FuturesSourceBlocked(RuntimeError):
    """The exchange answered with an access challenge instead of data."""


class FuturesPayloadError(RuntimeError):
    """The exchange answered, but not with a file this adapter understands."""


@dataclass(frozen=True)
class ExchangeDay:
    """One exchange's contracts for one session, split by instrument kind."""

    exchange: str
    trade_date: date
    futures: pl.DataFrame = field(default_factory=pl.DataFrame)
    options: pl.DataFrame = field(default_factory=pl.DataFrame)


def looks_like_challenge(status_code: int, body: bytes) -> bool:
    """A WAF bootstrap page, as opposed to an ordinary error page."""
    head = body[:4096]
    return status_code == 412 or b"$_ts" in head


def fetch_bytes(
    url: str,
    *,
    config=None,
    method: str = "GET",
    data: dict | None = None,
    json_body: dict | None = None,
    follow_redirects: bool = False,
) -> bytes:
    """One paced request, answered from a small per-process cache when repeated.

    `futures_bars` and `option_bars` read the same CFFEX file; the cache keeps
    that to one download per session instead of two. Only a 200 response is
    cached, so a day that is not yet published is asked again next time.
    """
    body_key = "&".join(
        f"{k}={v}" for k, v in sorted({**(data or {}), **(json_body or {})}.items())
    )
    key = (method, url, body_key)
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    resp = None
    for attempt in range(_NETWORK_ATTEMPTS):
        try:
            with source_request(config, SOURCE):
                with httpx.Client(
                    timeout=_TIMEOUT_SECONDS,
                    headers=_HEADERS,
                    follow_redirects=follow_redirects,
                ) as client:
                    resp = client.request(method, url, data=data, json=json_body)
            break
        except (httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            # A dropped connection (GFEX reset one on 2026-08-31 mid-sweep) is
            # the network, not the file; one paced retry recovers it. Timeouts
            # are not retried: a server that took 60 s will take 60 s again.
            if attempt + 1 == _NETWORK_ATTEMPTS:
                raise FuturesPayloadError(f"{url}: {type(exc).__name__}: {exc}") from exc
            logger.info("%s: %s, retrying once", url, type(exc).__name__)
            time.sleep(_RETRY_PAUSE_SECONDS)
        except httpx.HTTPError as exc:
            raise FuturesPayloadError(f"{url}: {type(exc).__name__}: {exc}") from exc
    if resp is None:  # unreachable: every attempt either breaks or raises
        raise FuturesPayloadError(f"{url}: no response")
    body = resp.content
    if looks_like_challenge(resp.status_code, body):
        raise FuturesSourceBlocked(f"{url}: access challenge (HTTP {resp.status_code})")
    if resp.status_code in (301, 302, 303, 307, 308, 404):
        raise FuturesDayUnavailable(f"{url}: HTTP {resp.status_code}")
    if resp.status_code != 200:
        raise FuturesPayloadError(f"{url}: HTTP {resp.status_code}")
    with _cache_lock:
        _cache[key] = body
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_ENTRIES:
            _cache.popitem(last=False)
    return body


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def decode_text(body: bytes) -> str:
    """The exchanges moved from GBK to UTF-8 at different times; try both."""
    if body.startswith(b"\xef\xbb\xbf"):
        return body[3:].decode("utf-8")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("gb18030")


def parse_number(value) -> float | None:
    """A published figure; blanks, dashes and thousands separators tolerated."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).replace(",", "").strip()
    if not text or text in {"-", "--", "nan", "None", "null"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_price(value) -> float | None:
    """A price field: zero is how the files print "no price", never a price."""
    number = parse_number(value)
    return number if number is not None and number > 0 else None


def parse_option_settle(value) -> float | None:
    """An option's settlement, where zero *is* a price.

    On its last session an out-of-the-money option settles at 0 — every
    exchange prints it that way (CFFEX IO2609 puts, CZCE AP2610 calls and GFEX
    on 2026-09 expiry days). Read as "no price", that zero also hid the put
    side of CFFEX's parity forward on expiry day. A row that is zero for want
    of anything to report — no settle, no trade, no position — is still a
    placeholder and dropped by :func:`is_placeholder`.
    """
    number = parse_number(value)
    return number if number is not None and number >= 0 else None


def drop_placeholders(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not is_placeholder(row)]


def is_placeholder(row: dict) -> bool:
    """A listed-but-not-yet-opened month: no settlement, no position, no trade.

    SHFE's early files carry far months this way (2002-01-07: CU0210–CU0212 at
    settlement 0 with zero open interest). Nothing is known about such a row,
    so it is not a live contract yet; a zero settlement that *does* carry
    positions is kept, and the schema rejects it as the anomaly it is.
    """
    return not row.get("settle") and not row.get("volume") and not row.get("open_interest")
