"""EastMoneyClient proxy wiring."""

from unittest.mock import patch

import httpx
import pytest

from cnequity.adapters.eastmoney.em_auth import EastMoneyClient
from cnequity.config import Config, load_config
from cnequity.config.bootstrap import path_for_toml


def test_eastmoney_client_passes_config_proxy(tmp_path):
    cfg = Config(data_root=tmp_path / "data", eastmoney_proxy="http://127.0.0.1:7890")
    with patch("cnequity.adapters.eastmoney.em_auth.httpx.Client") as mock_client:
        mock_client.return_value = mock_client
        client = EastMoneyClient(config=cfg)
        client.close()
    kwargs = mock_client.call_args.kwargs
    # httpx>=0.28 uses ``proxy``; older pins used ``proxies``.
    assert kwargs.get("proxy") == "http://127.0.0.1:7890" or kwargs.get("proxies") == (
        "http://127.0.0.1:7890"
    )


def test_eastmoney_client_no_proxy_by_default(tmp_path):
    cfg = Config(data_root=tmp_path / "data")
    with patch("cnequity.adapters.eastmoney.em_auth.httpx.Client") as mock_client:
        mock_client.return_value = mock_client
        client = EastMoneyClient(config=cfg)
        client.close()
    kwargs = mock_client.call_args.kwargs
    assert kwargs.get("proxy") is None
    assert kwargs.get("proxies") is None


def test_eastmoney_direct_fallback_config_is_explicit(tmp_path):
    path = tmp_path / "cnequity.toml"
    path.write_text(
        f'''[data]
root = "{path_for_toml(tmp_path / "data")}"
[sources.eastmoney]
enabled = true
proxy = "http://127.0.0.1:7890"
direct_fallback = true
''',
        encoding="utf-8",
    )

    assert load_config(path).eastmoney_direct_fallback is True


def test_push2his_proxy_protocol_failure_retries_direct_once(tmp_path, monkeypatch):
    cfg = Config(
        data_root=tmp_path / "data",
        eastmoney_proxy="http://127.0.0.1:7890",
        eastmoney_direct_fallback=True,
        source_intervals={"eastmoney": 0.0},
    )
    client = EastMoneyClient(config=cfg)
    direct_calls: list[str] = []

    def proxy_get(url, **kwargs):
        raise httpx.RemoteProtocolError("server disconnected")

    class _Direct:
        def get(self, url, **kwargs):
            direct_calls.append(url)
            return httpx.Response(200, request=httpx.Request("GET", url), text="ok")

        def close(self):
            pass

    monkeypatch.setattr(client._client, "get", proxy_get)
    client._direct_client = _Direct()

    response = client.get("https://push2his.eastmoney.com/api/qt/stock/kline/get")
    client.close()

    assert response.status_code == 200
    assert len(direct_calls) == 1
    assert client.last_route_outcome["proxy_failed"] is True
    assert client.last_route_outcome["direct_succeeded"] is True
    assert client.last_route_outcome["direct_failed"] is False


def test_push2his_records_when_proxy_and_direct_both_fail(tmp_path, monkeypatch):
    cfg = Config(
        data_root=tmp_path / "data",
        eastmoney_proxy="http://127.0.0.1:7890",
        eastmoney_direct_fallback=True,
        source_intervals={"eastmoney": 0.0},
    )
    client = EastMoneyClient(config=cfg)

    class _Direct:
        def get(self, url, **kwargs):
            raise httpx.RemoteProtocolError("direct drop")

        def close(self):
            pass

    monkeypatch.setattr(
        client._client,
        "get",
        lambda url, **kwargs: (_ for _ in ()).throw(httpx.RemoteProtocolError("proxy drop")),
    )
    client._direct_client = _Direct()

    with pytest.raises(httpx.RemoteProtocolError, match="direct drop"):
        client.get("https://push2his.eastmoney.com/api/qt/stock/kline/get")
    assert client.last_route_outcome["proxy_failed"] is True
    assert client.last_route_outcome["direct_failed"] is True
    client.close()


def test_direct_fallback_does_not_bypass_proxy_for_other_hosts(tmp_path, monkeypatch):
    cfg = Config(
        data_root=tmp_path / "data",
        eastmoney_proxy="http://127.0.0.1:7890",
        eastmoney_direct_fallback=True,
        source_intervals={"eastmoney": 0.0},
    )
    client = EastMoneyClient(config=cfg)
    monkeypatch.setattr(
        client._client,
        "get",
        lambda url, **kwargs: (_ for _ in ()).throw(httpx.RemoteProtocolError("drop")),
    )

    with pytest.raises(httpx.RemoteProtocolError):
        client.get("https://push2.eastmoney.com/api/qt/clist/get")
    assert client._direct_client is None
    client.close()


def test_direct_fallback_is_opt_in(tmp_path, monkeypatch):
    cfg = Config(
        data_root=tmp_path / "data",
        eastmoney_proxy="http://127.0.0.1:7890",
        source_intervals={"eastmoney": 0.0},
    )
    client = EastMoneyClient(config=cfg)
    monkeypatch.setattr(
        client._client,
        "get",
        lambda url, **kwargs: (_ for _ in ()).throw(httpx.RemoteProtocolError("drop")),
    )

    with pytest.raises(httpx.RemoteProtocolError):
        client.get("https://push2his.eastmoney.com/api/qt/stock/kline/get")
    assert client._direct_client is None
    client.close()
