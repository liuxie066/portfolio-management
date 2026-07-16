from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import patch

from src.models import AssetType, PriceCache
from src.pricing import BatchPricePlanner, PriceFailure, PriceQuote, PriceRequest, PriceService
from src.pricing.types import ProviderResult
from src.time_utils import bj_now_naive


class Provider:
    name = "test-provider"

    def supports(self, request):
        return True

    def fetch_one(self, request):
        return ProviderResult(
            payload={"code": request.normalized_code or request.code, "price": 1.23, "currency": "CNY", "cny_price": 1.23},
            provider=self.name,
            latency_ms=1,
        )


def test_price_service_returns_first_successful_provider_result():
    service = PriceService([Provider()])

    result = service.fetch_realtime(PriceRequest(code="000001", normalized_code="SZ000001"))

    assert result["code"] == "SZ000001"
    assert result["provider"] == "test-provider"
    assert result["source_chain"] == ["test-provider"]
    assert service.last_diagnostics[0]["ok"] is True


@dataclass
class StubStorage:
    cached: object = None
    saved: object = None

    def get_price(self, asset_id, *, allow_expired=False, max_stale_after_expiry_sec=0):
        return self.cached

    def save_price(self, price):
        self.saved = price


class FetcherContext:
    def __init__(self, storage, realtime_payload=None):
        self.storage = storage
        self.use_cache = True
        self.realtime_payload = realtime_payload
        self.realtime_calls = 0

    def _fetch_realtime(self, code, asset_name="", asset_type=None):
        self.realtime_calls += 1
        return self.realtime_payload


def _cache(expires_delta, *, source="tencent"):
    return PriceCache(
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        price=10.5,
        currency="CNY",
        cny_price=10.5,
        data_source=source,
        expires_at=bj_now_naive() + expires_delta,
    )


def test_fetch_quote_returns_valid_cache_without_realtime_call():
    storage = StubStorage(cached=_cache(timedelta(hours=1)))
    fetcher = FetcherContext(storage, realtime_payload={"code": "000001", "price": 99})
    service = PriceService([], fetcher_context=fetcher)

    quote = service.fetch_quote("000001")

    assert isinstance(quote, PriceQuote)
    assert quote.to_payload()["price"] == 10.5
    assert quote.to_payload()["cache_status"] == "hit"
    assert quote.to_payload()["source"] == "tencent"
    assert fetcher.realtime_calls == 0
    assert service.last_diagnostics == []


def test_fetch_quote_returns_fixed_cash_without_fetcher_context():
    service = PriceService([])

    quote = service.fetch_quote("CNY-CASH")

    assert isinstance(quote, PriceQuote)
    payload = quote.to_payload()
    assert payload["price"] == 1.0
    assert payload["source"] == "fixed"
    assert payload["source_chain"] == ["cash"]


def test_fetch_quote_returns_fixed_usd_crypto_value():
    fetcher = FetcherContext(StubStorage())
    fetcher._fetch_exchange_rates = lambda: {"USDCNY": 7.2}
    service = PriceService([], fetcher_context=fetcher)

    quote = service.fetch_quote("BINANCE-TRADING-CRYPTO-USD")

    assert isinstance(quote, PriceQuote)
    payload = quote.to_payload()
    assert payload["price"] == 1.0
    assert payload["currency"] == "USD"
    assert payload["cny_price"] == 7.2
    assert payload["market_type"] == "crypto"
    assert payload["source_chain"] == ["crypto_value"]
    assert fetcher.realtime_calls == 0


def test_fetch_quote_falls_back_to_stale_cache_when_realtime_fails():
    storage = StubStorage(cached=_cache(timedelta(hours=-1)))
    fetcher = FetcherContext(storage, realtime_payload=None)
    service = PriceService([], fetcher_context=fetcher)

    quote = service.fetch_quote(
        "000001",
        accept_stale_when_closed=True,
        max_stale_after_expiry_sec=7200,
    )

    assert isinstance(quote, PriceQuote)
    payload = quote.to_payload()
    assert payload["source"] == "cache_fallback"
    assert payload["cache_status"] == "stale_fallback"
    assert payload["is_stale"] is True
    assert fetcher.realtime_calls == 1


def test_fetch_quote_saves_realtime_payload_and_returns_market_type():
    storage = StubStorage()
    fetcher = FetcherContext(
        storage,
        realtime_payload={
            "code": "AAPL",
            "name": "Apple",
            "price": 190.123,
            "currency": "USD",
            "cny_price": 1350.456,
            "exchange_rate": 7.1023,
            "source": "yahoo_chart",
        },
    )
    service = PriceService([], fetcher_context=fetcher)

    quote = service.fetch_quote("AAPL", asset_type_map={"AAPL": AssetType.US_STOCK})

    assert isinstance(quote, PriceQuote)
    payload = quote.to_payload()
    assert payload["price"] == 190.12
    assert payload["market_type"] == "us"
    assert payload["cache_status"] == "realtime"
    assert storage.saved.asset_id == "AAPL"
    assert storage.saved.asset_type == AssetType.US_STOCK
    assert storage.saved.data_source == "yahoo_chart"


def test_fetch_quote_returns_structured_failure_without_cache_or_realtime():
    storage = StubStorage()
    fetcher = FetcherContext(storage, realtime_payload=None)
    service = PriceService([], fetcher_context=fetcher)

    failure = service.fetch_quote("MISSING")

    assert isinstance(failure, PriceFailure)
    assert failure.error_type == "quote_unavailable"
    assert failure.to_payload()["success"] is False


def test_fetch_batch_wraps_optimized_payloads():
    storage = StubStorage(cached=_cache(timedelta(hours=1)))
    fetcher = FetcherContext(storage, realtime_payload=None)
    service = PriceService([], fetcher_context=fetcher)

    result = service.fetch_batch(["000001"], use_cache_only=True)

    assert result.ok
    assert result.quotes["000001"].to_payload()["price"] == 10.5
    assert result.quotes["000001"].to_payload()["cache_status"] == "hit"


def test_fetch_batch_returns_fixed_usd_crypto_value():
    fetcher = FetcherContext(StubStorage())
    fetcher._fetch_exchange_rates = lambda: {"USDCNY": 7.2}
    service = PriceService([], fetcher_context=fetcher)

    result = service.fetch_batch(["BINANCE-WALLET-CRYPTO-USD"])

    assert result.ok
    payload = result.quotes["BINANCE-WALLET-CRYPTO-USD"].to_payload()
    assert payload["price"] == 1.0
    assert payload["cny_price"] == 7.2
    assert payload["market_type"] == "crypto"
    assert fetcher.realtime_calls == 0


def test_fetch_batch_does_not_fail_when_payload_key_is_normalized():
    fetcher = FetcherContext(StubStorage(), realtime_payload=None)
    service = PriceService([], fetcher_context=fetcher)
    payload = {"code": "BABA", "price": 80.0, "currency": "USD", "cny_price": 580.0}

    with patch("src.pricing.service.BatchPricePlanner.fetch_batch", return_value={"BABA": payload}):
        result = service.fetch_batch(["baba"])

    assert result.ok
    assert "BABA" in result.quotes
    assert result.failures == {}


def test_batch_planner_fetch_non_us_uses_provider_batch():
    fetcher = FetcherContext(StubStorage(), realtime_payload=None)
    payload = {"code": "000001", "price": 10.5, "currency": "CNY", "cny_price": 10.5, "source": "tencent_batch"}

    with patch("src.pricing.batch.fetch_tencent_quotes_batch", return_value=({"000001": payload}, [])) as mocked:
        result = BatchPricePlanner(fetcher).fetch_non_us(["000001"], {"000001": "平安银行"}, _nested=True)

    assert result == {"000001": payload}
    mocked.assert_called_once()
