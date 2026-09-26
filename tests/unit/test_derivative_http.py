"""Refusal circuits and request-count contracts, without outbound requests."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import polars as pl
import pytest

from cnequity.adapters.futures_exchange import common, shfe
from cnequity.adapters.sina.dce_futures import SinaDceHistory
from cnequity.config import Config


@pytest.fixture(autouse=True)
def clean():
    common.clear_cache()
    yield
    common.clear_cache()


@pytest.mark.parametrize("status", [403, 412, 429, 456, 503, 567])
def test_refusal_stops_other_dates_and_is_shared_across_configs(tmp_path, monkeypatch, status):
    monkeypatch.setenv("CNE_RATE_LIMIT_ROOT", str(tmp_path / "egress-budget"))
    cfg = Config(data_root=tmp_path / "one")
    other = Config(data_root=tmp_path / "two")
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(status, content=b"blocked", headers={"Retry-After": "600"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for config, day in [(cfg, "1"), (cfg, "2"), (other, "3")]:
            with pytest.raises(common.FuturesSourceBlocked):
                common.fetch_bytes(
                    f"https://hq.sinajs.cn/{day}", config=config, source="sina", client=client
                )
    assert len(calls) == 1
    assert not list((cfg.meta_root / "derivatives" / "http_cache").glob("*.json"))


def test_cache_survives_process_cache_clear_and_simultaneous_misses_are_merged(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(200, content=b'{"data":[1]}')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:

        def fetch():
            return common.fetch_bytes("https://example.test/daily", config=cfg, client=client)

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(lambda _: fetch(), range(2))) == [b'{"data":[1]}'] * 2
        common.clear_cache()
        assert fetch() == b'{"data":[1]}'
        assert len(calls) == 1
        cfg._derivatives_refresh = True
        fetch()
        assert len(calls) == 2


def test_prior_receipts_survive_transport_change_but_czce_options_reparse(tmp_path):
    from cnequity.storage.derivative_evidence import (
        PRE_2004_ARCHIVE_PARSER,
        PRE_ARCHIVE_DECOUPLING_PARSER,
        PRE_CZCE_EXPIRY_REPAIR_PARSER,
        receipt_path,
        record_session,
        session_matches,
    )

    cfg = Config(data_root=tmp_path / "lake")
    day = date(2026, 1, 13)
    frame = pl.DataFrame({"symbol": ["FG2602C1220.CZC"], "trade_date": [day]})
    for dataset, exchange, expected in [
        ("option_bars", "CZC", False),
        ("futures_bars", "CZC", True),
        ("option_bars", "CFE", True),
    ]:
        record_session(cfg, dataset, day, exchange, frame)
        path = receipt_path(cfg, dataset, day, exchange)
        receipt = json.loads(path.read_text())
        receipt["parser"] = PRE_CZCE_EXPIRY_REPAIR_PARSER
        path.write_text(json.dumps(receipt))
        assert session_matches(cfg, dataset, day, exchange, frame) is expected
        receipt["parser"] = PRE_2004_ARCHIVE_PARSER
        path.write_text(json.dumps(receipt))
        assert session_matches(cfg, dataset, day, exchange, frame)
        receipt["parser"] = PRE_ARCHIVE_DECOUPLING_PARSER
        path.write_text(json.dumps(receipt))
        assert session_matches(cfg, dataset, day, exchange, frame)


def test_empty_live_contract_cache_expires_and_does_not_hide_a_new_listing(tmp_path, monkeypatch):
    cfg = Config(data_root=tmp_path / "lake")
    calls = []
    now = common.time.time()
    monkeypatch.setattr(common.time, "time", lambda: now)

    def handler(req):
        calls.append(req.url)
        body = b"x([])" if len(calls) == 1 else b'x([{"d":"2026-09-24","v":1,"p":1,"s":3000}])'
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        history = SinaDceHistory()
        assert history.contract("M2711", config=cfg, client=client) == {}
        assert history.contract("M2711", config=cfg, client=client) == {}
        now += 3601
        assert date(2026, 9, 24) in history.contract("M2711", config=cfg, client=client)
    assert len(calls) == 2


def test_futures_only_does_not_request_or_depend_on_options(monkeypatch):
    urls = []
    monkeypatch.setattr(shfe, "parse_futures", lambda *_: pl.DataFrame({"symbol": ["CU2611.SHF"]}))

    def fetch(url, **kwargs):
        urls.append(url)
        if "kx" in url:
            return b"future"
        raise common.FuturesDayUnavailable("option file unavailable")

    monkeypatch.setattr(shfe, "fetch_bytes", fetch)
    # Match the actual futures URL without assuming its current filename.
    monkeypatch.setattr(shfe, "FUTURES_URL", "https://example.test/kx/{ymd}")
    result = shfe.fetch_shfe_day(date(2026, 9, 24), kind="futures")
    assert result.futures.height == 1 and result.options.is_empty()
    assert len(urls) == 1
