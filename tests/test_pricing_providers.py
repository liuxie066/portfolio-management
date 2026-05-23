from unittest.mock import patch

from src.price_fetcher import PriceFetcher
from src.pricing.providers.etf import ETFProvider
from src.pricing.providers.cn import CNStockProvider
from src.pricing.providers.fund import FundProvider
from src.pricing.providers.hk import HKStockProvider
from src.pricing.providers.tencent_batch import fetch_tencent_quotes_batch
from src.pricing.providers.us import USStockProvider
from src.pricing.providers.us_batch import fetch_us_batch


def test_fetch_realtime_routes_cn_stock_through_provider(monkeypatch):
    fetcher = PriceFetcher()

    def fake_fetch(self, code):
        return {"code": code, "price": 10.0, "currency": "CNY", "cny_price": 10.0, "source": "fake-cn"}

    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fake_fetch)

    result = fetcher._fetch_realtime("000001", "平安银行")

    assert result["source"] == "fake-cn"
    assert result["provider"] == "cn-stock"
    assert result["source_chain"] == ["cn-stock"]


def test_fetch_realtime_routes_fund_before_cn_stock(monkeypatch):
    fetcher = PriceFetcher()

    def fake_fund(self, code):
        return {"code": code, "price": 1.0, "currency": "CNY", "cny_price": 1.0, "source": "fake-fund"}

    def fail_cn(self, code):
        raise AssertionError("fund-like code should not reach CN stock provider")

    monkeypatch.setattr(FundProvider, "fetch_fund", fake_fund)
    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fail_cn)

    result = fetcher._fetch_realtime("004001", "基金")

    assert result["source"] == "fake-fund"
    assert result["provider"] == "fund"


def test_fetch_realtime_routes_otc_fund_asset_type_to_fund(monkeypatch):
    fetcher = PriceFetcher()

    def fake_fund(self, code):
        return {"code": code, "price": 1.0, "currency": "CNY", "cny_price": 1.0, "source": "fake-fund"}

    def fail_cn(self, code):
        raise AssertionError("otc_fund should not reach CN stock provider")

    monkeypatch.setattr(FundProvider, "fetch_fund", fake_fund)
    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fail_cn)

    result = fetcher._fetch_realtime("000001", "", "otc_fund")

    assert result["source"] == "fake-fund"
    assert result["provider"] == "fund"


def test_fetch_realtime_routes_exchange_fund_asset_type_to_etf(monkeypatch):
    fetcher = PriceFetcher()

    def fake_etf(self, code):
        return {"code": code, "price": 4.0, "currency": "CNY", "cny_price": 4.0, "source": "fake-etf"}

    def fail_fund(self, code):
        raise AssertionError("exchange_fund should not reach fund NAV provider")

    monkeypatch.setattr(ETFProvider, "fetch_etf", fake_etf)
    monkeypatch.setattr(FundProvider, "fetch_fund", fail_fund)

    result = fetcher._fetch_realtime("510300", "", "exchange_fund")

    assert result["source"] == "fake-etf"
    assert result["provider"] == "etf"


def test_fetch_realtime_routes_hk_stock_through_provider(monkeypatch):
    fetcher = PriceFetcher()

    def fake_hk(self, code):
        return {"code": code, "price": 400.0, "currency": "HKD", "cny_price": 360.0, "source": "fake-hk"}

    monkeypatch.setattr(HKStockProvider, "fetch_hk_stock", fake_hk)

    result = fetcher._fetch_realtime("00700", "腾讯控股")

    assert result["source"] == "fake-hk"
    assert result["provider"] == "hk-stock"


def test_fetch_realtime_defaults_to_us_provider(monkeypatch):
    fetcher = PriceFetcher()

    def fake_us(self, code):
        return {"code": code, "price": 100.0, "currency": "USD", "cny_price": 720.0, "source": "fake-us"}

    monkeypatch.setattr(USStockProvider, "fetch_us_stock", fake_us)

    result = fetcher._fetch_realtime("AAPL", "Apple")

    assert result["source"] == "fake-us"
    assert result["provider"] == "us-stock"


def test_tencent_batch_provider_fetches_cn_quote():
    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda: {"HKDCNY": 0.9}

    parts = [""] * 46
    parts[1] = "沪深300ETF"
    parts[3] = "4.20"
    parts[4] = "4.00"
    parts[5] = "4.10"
    parts[30] = "2026-05-23 15:00:00"
    parts[31] = "0.20"
    parts[32] = "5.00"
    parts[33] = "4.30"
    parts[34] = "4.00"
    parts[36] = "100"

    def fake_batch(session, query_codes, timeout=8, chunk_size=50):
        assert query_codes == ["sh510300"]
        return {"sh510300": parts}, {"requested": 1, "returned": 1}

    with patch("src.pricing.providers.tencent_batch.tencent_fetch_batch", fake_batch):
        results, leftover = fetch_tencent_quotes_batch(fetcher, ["510300"], name_map={"510300": "沪深300ETF"})

    assert leftover == []
    assert results["510300"]["source"] == "tencent_batch"
    assert results["510300"]["price"] == 4.2
    assert fetcher._last_tencent_batch_meta == {"requested": 1, "returned": 1}


def test_us_batch_provider_falls_back_to_stale_cache():
    fetcher = PriceFetcher()
    fetcher.session.get = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))

    result = fetch_us_batch(
        fetcher,
        ["AAPL"],
        name_map={},
        expired_cache={"AAPL": {"code": "AAPL", "source": "old_cache", "is_from_cache": True}},
        _nested=True,
    )

    assert result["AAPL"]["source"] == "cache_fallback"
    assert result["AAPL"]["is_from_cache"] is True
