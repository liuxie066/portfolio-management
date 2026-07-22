"""Tests for PriceCachePolicy semantic stale acceptance (market-session based)."""
from datetime import timedelta

from src.market_time import MarketTimeUtil
from src.models import AssetType, PriceCache
from src.pricing.cache import PriceCachePolicy, STALE_RETRIEVAL_WINDOW_SEC
from src.time_utils import bj_now_naive


class StubStorage:
    def __init__(self, cached):
        self.cached = cached
        self.calls = []

    def get_price(self, asset_id, *, allow_expired=False, max_stale_after_expiry_sec=0):
        self.calls.append({"allow_expired": allow_expired, "max_stale_after_expiry_sec": max_stale_after_expiry_sec})
        return self.cached


def _expired_cache(asset_id="FUTU", hours=48):
    return PriceCache(
        asset_id=asset_id,
        asset_name=asset_id,
        asset_type=AssetType.US_STOCK,
        price=100.0,
        currency="USD",
        cny_price=710.0,
        data_source="sina_us",
        expires_at=bj_now_naive() - timedelta(hours=hours),
    )


def test_semantic_stale_accepted_when_market_has_not_traded(monkeypatch):
    monkeypatch.setattr(MarketTimeUtil, "has_market_session_between", lambda *a, **kw: False)
    storage = StubStorage(_expired_cache())
    policy = PriceCachePolicy(storage, enabled=True)

    quote = policy.get("FUTU", accept_stale=True)

    assert quote is not None
    assert quote.stale is True
    payload = quote.to_payload()
    assert payload["source"] == "cache_fallback"
    assert payload["cache_status"] == "stale_fallback"
    assert payload["is_stale"] is True
    # retrieval uses the scan window, not the zero default
    assert storage.calls[0]["max_stale_after_expiry_sec"] == STALE_RETRIEVAL_WINDOW_SEC


def test_semantic_stale_rejected_when_market_has_traded(monkeypatch):
    monkeypatch.setattr(MarketTimeUtil, "has_market_session_between", lambda *a, **kw: True)
    storage = StubStorage(_expired_cache())
    policy = PriceCachePolicy(storage, enabled=True)

    assert policy.get("FUTU", accept_stale=True) is None


def test_explicit_window_contract_bypasses_market_check(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("market check must not run for explicit-window contract")

    monkeypatch.setattr(MarketTimeUtil, "has_market_session_between", forbidden)
    storage = StubStorage(_expired_cache(hours=1))
    policy = PriceCachePolicy(storage, enabled=True)

    quote = policy.get("FUTU", accept_stale=True, max_stale_after_expiry_sec=7200)

    assert quote is not None
    assert quote.stale is True
    assert storage.calls[0]["max_stale_after_expiry_sec"] == 7200


def test_expired_cache_rejected_without_accept_stale():
    storage = StubStorage(_expired_cache())
    policy = PriceCachePolicy(storage, enabled=True)

    assert policy.get("FUTU") is None


def test_semantic_stale_uses_real_market_detection_for_us_code():
    # expires 8h ago: well within today, no US session between (US opens 21:30 BJ)
    # — acceptance depends on real wall-clock market sessions, no monkeypatching.
    from src.time_utils import bj_now_naive as _now

    now = _now()
    storage = StubStorage(_expired_cache(hours=1))
    policy = PriceCachePolicy(storage, enabled=True)
    quote = policy.get("FUTU", accept_stale=True)

    from src.market_time import MarketTimeUtil as _MTU

    traded = _MTU.has_market_session_between("us", now - timedelta(hours=1), now)
    if traded:
        assert quote is None
    else:
        assert quote is not None and quote.stale is True


def test_aware_expires_at_does_not_mislabel_fresh_quote():
    from datetime import timezone

    # fresh (expires in 1h, tz-aware UTC) must come back as a normal cache hit,
    # not mislabeled stale_fallback
    aware_future = bj_now_naive().replace(tzinfo=timezone.utc) + timedelta(hours=1)
    cached = PriceCache(
        asset_id="FUTU",
        asset_name="FUTU",
        asset_type=AssetType.US_STOCK,
        price=100.0,
        currency="USD",
        cny_price=710.0,
        data_source="sina_us",
        expires_at=aware_future,
    )
    storage = StubStorage(cached)
    policy = PriceCachePolicy(storage, enabled=True)

    quote = policy.get("FUTU", accept_stale=True)
    assert quote is not None
    assert quote.stale is False
    assert quote.cache_status == "hit"
