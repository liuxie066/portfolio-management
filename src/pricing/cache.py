"""Price cache policy for the pricing service."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from src.asset_utils import detect_asset_type, detect_market_type
from src.market_time import MarketTimeUtil
from src.models import AssetType, PriceCache
from src.time_utils import bj_now_naive

from .classifier import is_etf
from .payload import normalize_price_payload
from .result import PriceQuote


def market_type_from_asset_type(code: str, asset_type: Any, default_market_type: Optional[str]) -> Optional[str]:
    """Map persisted asset_type to pricing market_type, keeping legacy fund rows compatible."""
    if asset_type is None:
        return default_market_type

    atv = asset_type.value if hasattr(asset_type, "value") else str(asset_type)
    if atv in (AssetType.A_STOCK.value, AssetType.CN_FUND.value):
        return "cn"
    if atv == AssetType.EXCHANGE_FUND.value:
        return default_market_type if default_market_type in ("hk", "us") else "cn"
    if atv in (AssetType.HK_STOCK.value, AssetType.HK_FUND.value):
        return "hk"
    if atv in (AssetType.US_STOCK.value, AssetType.US_FUND.value):
        return "us"
    if atv == AssetType.OTC_FUND.value:
        return "fund"
    if atv == AssetType.FUND.value:
        return "cn" if is_etf(code) else "fund"
    return default_market_type


def _parse_expires_at(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    # Normalize to naive Beijing wall time: local_cache stores naive strings and
    # callers compare against bj_now_naive(); mixing aware/naive raises TypeError.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(MarketTimeUtil.TZ_SHANGHAI).replace(tzinfo=None)
    return parsed


# Scan window used only to *retrieve* an expired entry for the semantic stale
# check; acceptance itself is decided by has_market_session_between.
STALE_RETRIEVAL_WINDOW_SEC = 40 * 86400


def price_cache_to_payload(cached: PriceCache, *, is_stale: bool | None = None) -> dict:
    """Convert persisted price cache records into normalized quote payloads."""
    payload = {
        "code": cached.asset_id,
        "name": cached.asset_name,
        "price": cached.price,
        "currency": cached.currency,
        "cny_price": cached.cny_price,
        "change": cached.change,
        "change_pct": cached.change_pct,
        "exchange_rate": cached.exchange_rate,
        "source": cached.data_source or "cache",
        "expires_at": cached.expires_at,
        "is_from_cache": True,
    }
    if is_stale is not None:
        payload["is_stale"] = bool(is_stale)
    return normalize_price_payload(payload)


class PriceCachePolicy:
    """Own cache lookup, stale fallback, and cache writes for quote payloads."""

    def __init__(self, storage: Any = None, *, enabled: bool = False):
        self.storage = storage
        self.enabled = bool(enabled and storage is not None)

    def get(
        self,
        code: str,
        *,
        accept_stale: bool = False,
        max_stale_after_expiry_sec: int = 0,
    ) -> Optional[PriceQuote]:
        """Return a cached quote, optionally accepting an expired one.

        Two stale-acceptance contracts:
        - explicit window (max_stale_after_expiry_sec > 0): accept within the
          window after expiry (legacy callers/tests);
        - semantic (accept_stale=True, window=0): accept only when the quote's
          market has NOT traded since expiry — a closed market cannot move the
          price, so the stale quote is still the correct one. If the market
          has traded since, reject (fail closed).
        """
        if not self.enabled:
            return None

        semantic_stale = accept_stale and max_stale_after_expiry_sec <= 0
        storage_window = STALE_RETRIEVAL_WINDOW_SEC if semantic_stale else max_stale_after_expiry_sec
        cached = self.storage.get_price(
            code,
            allow_expired=accept_stale,
            max_stale_after_expiry_sec=storage_window,
        )
        if not cached:
            return None

        is_expired = False
        expire_dt = None
        if getattr(cached, "expires_at", None):
            try:
                expire_dt = _parse_expires_at(cached.expires_at)
                is_expired = bool(expire_dt and expire_dt <= bj_now_naive())
            except Exception:
                is_expired = True

        payload = price_cache_to_payload(cached, is_stale=is_expired)

        if is_expired:
            if not accept_stale:
                return None
            if semantic_stale and self._market_has_traded_since(code, expire_dt):
                return None
            payload["source"] = "cache_fallback"
            return PriceQuote.from_payload(payload, code=code, cache_status="stale_fallback", stale=True)

        return PriceQuote.from_payload(payload, code=code, cache_status="hit")

    @staticmethod
    def _market_has_traded_since(code: str, expire_dt: Optional[datetime]) -> bool:
        """Whether the quote's market traded after expire_dt (fail closed on doubt)."""
        if expire_dt is None:
            return True
        try:
            market_type = detect_market_type(code)
            return MarketTimeUtil.has_market_session_between(market_type, expire_dt, bj_now_naive())
        except Exception:
            return True

    def save(
        self,
        code: str,
        quote: PriceQuote,
        *,
        asset_type: Any = None,
        market_closed_ttl_multiplier: float = 1.0,
    ) -> Optional[str]:
        if not self.enabled:
            return None

        payload = quote.to_payload()
        market_type = detect_market_type(code)
        market_type = market_type_from_asset_type(code, asset_type, market_type)

        ttl = int(MarketTimeUtil.get_cache_ttl(market_type) * market_closed_ttl_multiplier)
        expires_at = bj_now_naive() + timedelta(seconds=ttl)

        cache_asset_type = AssetType.OTHER
        detected_asset_type = None
        try:
            detected_asset_type = detect_asset_type(code)[0]
        except Exception:
            detected_asset_type = None

        if detected_asset_type in (AssetType.EXCHANGE_FUND, AssetType.OTC_FUND):
            cache_asset_type = detected_asset_type
        elif market_type == "cn":
            cache_asset_type = AssetType.A_STOCK
        elif market_type == "hk":
            cache_asset_type = AssetType.HK_STOCK
        elif market_type == "us":
            cache_asset_type = AssetType.US_STOCK
        elif market_type == "fund":
            cache_asset_type = AssetType.OTC_FUND

        price_cache = PriceCache(
            asset_id=code,
            asset_name=payload.get("name"),
            asset_type=cache_asset_type,
            price=payload.get("price", 0),
            currency=payload.get("currency", "CNY"),
            cny_price=payload.get("cny_price", payload.get("price", 0)),
            change=payload.get("change"),
            change_pct=payload.get("change_pct"),
            exchange_rate=payload.get("exchange_rate"),
            data_source=payload.get("source"),
            expires_at=expires_at,
        )
        self.storage.save_price(price_cache)
        return market_type
