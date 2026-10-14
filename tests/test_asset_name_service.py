import time
import pytest
from unittest.mock import Mock

from src.app.asset_name_service import AssetNameService
from src.portfolio import PortfolioManager


def test_asset_name_service_returns_fetched_name():
    manager = Mock()
    manager.price_fetcher.fetch.return_value = {"name": "平安银行"}
    service = AssetNameService(manager=manager)

    assert service.get_asset_name("000001", "平安") == "平安银行"


def test_asset_name_service_falls_back_to_user_name_on_error():
    manager = Mock()
    manager.price_fetcher.fetch.side_effect = RuntimeError("boom")
    service = AssetNameService(manager=manager)

    assert service.get_asset_name("000001", "平安") == "平安"


def test_asset_name_service_falls_back_to_asset_id_without_user_name():
    manager = Mock()
    manager.price_fetcher.fetch.return_value = {}
    service = AssetNameService(manager=manager)

    assert service.get_asset_name("000001") == "000001"


def test_asset_name_service_timeout_uses_fallback_name():
    manager = Mock()

    def slow_fetch(_asset_id):
        time.sleep(0.05)
        return {"name": "late"}

    manager.price_fetcher.fetch.side_effect = slow_fetch
    service = AssetNameService(manager=manager)

    assert service.get_asset_name("000001", "平安", timeout=0.001) == "平安"


def test_portfolio_get_asset_name_delegates_to_service():
    storage = Mock()
    manager = PortfolioManager(storage=storage, price_fetcher=Mock())
    manager.asset_name_service = Mock()
    manager.asset_name_service.get_asset_name.return_value = "平安银行"

    assert manager._get_asset_name("000001", "平安", timeout=1.0) == "平安银行"
    manager.asset_name_service.get_asset_name.assert_called_once_with(
        asset_id="000001",
        user_provided_name="平安",
        timeout=1.0,
    )


def test_fund_name_lookup_reuses_exact_tencent_parser_and_closes_session(monkeypatch):
    from types import SimpleNamespace

    calls = []
    response = SimpleNamespace(text='v_jj011609="1~测试基金~011609~0~0~1.234~0~0~2026-10-14";', encoding=None)
    def get(self, url, **kwargs):
        calls.append((url, kwargs))
        return response
    import requests
    monkeypatch.setattr(requests.Session, "get", get)
    closed = []
    monkeypatch.setattr(requests.Session, "close", lambda self: closed.append(True))
    result = AssetNameService.lookup_fund_name("011609")
    assert result["name"] == "测试基金"
    assert result["code"] == "011609"
    assert result["source"] == "tencent_jj"
    assert result["source_as_of"]
    assert calls[0][0] == "https://qt.gtimg.cn/q=jj011609"
    assert 0 < calls[0][1]["timeout"] <= 5
    assert closed == [True]


@pytest.mark.parametrize("updates", [
    {"code": "011610"}, {"source": "eastmoney"}, {"name": "011609"},
    {"name": " "}, {"is_stale": True}, {"is_from_cache": True},
])
def test_fund_name_lookup_rejects_unproven_payload(monkeypatch, updates):
    from src.price_fetcher import PriceFetcher
    monkeypatch.setattr(PriceFetcher, "fetch", lambda *args, **kwargs: {
        "code": "011609", "source": "tencent_jj", "name": "测试基金", **updates,
    })
    assert AssetNameService.lookup_fund_name("011609") is None


def test_fund_name_lookup_rejects_foreign_tencent_response(monkeypatch):
    import requests
    from types import SimpleNamespace
    monkeypatch.setattr(requests.Session, "get", lambda *args, **kwargs: SimpleNamespace(
        text='v_jj011610="1~其他基金~011610~0~0~1.234~0~0~2026-10-14";', encoding=None,
    ))
    assert AssetNameService.lookup_fund_name("011609") is None
