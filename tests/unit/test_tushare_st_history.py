"""Offline contract tests for the optional Tushare BJ ST adapter."""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from cnequity.adapters.tushare.st_history import _post_json, fetch_st_history
from cnequity.config import Config
from cnequity.domain.http_policy import SourceCoolingDown


def test_business_rate_limit_stops_remaining_symbols(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"code": -2001, "msg": "请求过于频繁"})

    cfg = Config(data_root=tmp_path, source_intervals={"tushare": 0})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        rows, failed = fetch_st_history(
            ["920001.BJ", "920002.BJ"],
            date(2017, 1, 1),
            date(2017, 1, 5),
            token="test-token",
            client=client,
            config=cfg,
            trading_dates={
                "920001.BJ": [date(2017, 1, 4)],
                "920002.BJ": [date(2017, 1, 4)],
            },
        )
    assert rows.is_empty()
    assert failed == ["920001.BJ", "920002.BJ"]
    assert len(calls) == 1


def test_http_429_does_not_sleep_and_retry(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"})

    cfg = Config(data_root=tmp_path, source_intervals={"tushare": 0}, max_retries=4)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SourceCoolingDown):
            _post_json(
                client, {"token": "test-token"}, config=cfg, sleep=lambda _: pytest.fail("sleep")
            )
    assert len(calls) == 1


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, rows_by_code, rows_by_date=None):
        self.rows_by_code = rows_by_code
        self.rows_by_date = rows_by_date or {}
        self.calls = []

    def post(self, url, *, json):
        self.calls.append(json)
        if json["api_name"] == "bak_basic":
            rows = self.rows_by_date.get(json["params"]["trade_date"], [])
            fields = ["ts_code", "name", "trade_date"]
        else:
            code = json["params"]["ts_code"]
            rows = self.rows_by_code.get(code, [])
            fields = ["ts_code", "name", "trade_date", "type", "type_name"]
        return _Response(
            {
                "code": 0,
                "data": {
                    "fields": fields,
                    "items": rows,
                },
            }
        )


class _FlakyClient(_Client):
    def __init__(self, rows_by_code, rows_by_date=None, transient_failures=0):
        super().__init__(rows_by_code, rows_by_date)
        self.transient_failures = transient_failures

    def post(self, url, *, json):
        if self.transient_failures:
            self.transient_failures -= 1
            raise httpx.ReadTimeout("temporary Tushare timeout")
        return super().post(url, json=json)


def _row(code: str, trade_date: str, kind: str = "ST"):
    return [code, "*ST测试", trade_date, kind, "风险警示板"]


def test_emits_explicit_normal_rows_and_maps_legacy_bj_code():
    client = _Client({"920001.BJ": [_row("920001.BJ", "20170104")]})
    df, failed = fetch_st_history(
        ["873001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={
            "873001.BJ": [date(2017, 1, 3), date(2017, 1, 4)],
        },
    )

    assert failed == []
    assert {call["params"]["ts_code"] for call in client.calls} == {
        "873001.BJ",
        "920001.BJ",
    }
    assert df.sort("trade_date")["status"].to_list() == ["normal", "normal"]
    assert df.sort("trade_date")["risk_warning"].to_list() == [False, True]
    assert df["symbol"].unique().to_list() == ["873001.BJ"]


def test_pre_floor_bars_remain_unresolved_without_network_call():
    client = _Client({})
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2015, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={"920001.BJ": [date(2015, 12, 30)]},
    )

    assert df.is_empty()
    assert failed == ["920001.BJ"]
    assert client.calls == []


def test_bak_basic_name_covers_2016_and_stock_st_covers_2017():
    client = _Client(
        {"920001.BJ": [_row("920001.BJ", "20170104")]},
        {"20161230": [["920001.BJ", "*ST测试", "20161230"]]},
    )
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2016, 12, 30),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={
            "920001.BJ": [date(2016, 12, 30), date(2017, 1, 4)],
        },
    )

    assert failed == []
    assert df.sort("trade_date")["status"].to_list() == ["normal", "normal"]
    assert df.sort("trade_date")["risk_warning"].to_list() == [True, True]
    assert [call["api_name"] for call in client.calls] == ["bak_basic", "stock_st", "stock_st"]


def test_unknown_st_type_fails_closed():
    client = _Client({"920001.BJ": [_row("920001.BJ", "20170104", "RISK")]})
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={"920001.BJ": [date(2017, 1, 4)]},
    )

    assert df.is_empty()
    assert failed == ["920001.BJ"]


def test_missing_stock_st_identity_fails_closed():
    client = _Client({"920001.BJ": [[None, "*ST测试", "20170104", "ST", "风险警示板"]]})
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={"920001.BJ": [date(2017, 1, 4)]},
    )

    assert df.is_empty()
    assert failed == ["920001.BJ"]


def test_invalid_stock_st_date_fails_closed():
    client = _Client({"920001.BJ": [_row("920001.BJ", "not-a-date")]})
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        trading_dates={"920001.BJ": [date(2017, 1, 4)]},
    )

    assert df.is_empty()
    assert failed == ["920001.BJ"]


def test_retries_transient_timeout_before_emitting_evidence(tmp_path):
    from cnequity.domain.rate_limit import _read_json

    client = _FlakyClient(
        {"920001.BJ": [_row("920001.BJ", "20170104")]},
        transient_failures=1,
    )
    cfg = Config(data_root=tmp_path, max_retries=2, retry_backoff_seconds=0)
    df, failed = fetch_st_history(
        ["920001.BJ"],
        date(2017, 1, 1),
        date(2017, 1, 5),
        token="secret",
        client=client,
        config=cfg,
        sleep=lambda _: None,
        trading_dates={"920001.BJ": [date(2017, 1, 4)]},
    )

    assert failed == []
    assert client.transient_failures == 0
    assert df["status"].to_list() == ["normal"]
    assert df["risk_warning"].to_list() == [True]
    assert _read_json(cfg.rate_limit_root / "events-tushare.json")["retry"] == 1
