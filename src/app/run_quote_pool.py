"""Run-scoped successful quote sharing for sequential account valuation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping

from src.asset_utils import detect_market_type
from src.pricing.cache import market_type_from_asset_type
from src.pricing.classifier import canonicalize_pricing_code
from src.pricing.payload import positive_finite_decimal


QuoteIdentity = tuple[str, str]


class RunQuotePool:
    """Share valid quotes within one caller-owned daily NAV run."""

    max_attempts = 2

    def __init__(self) -> None:
        self._quotes: dict[QuoteIdentity, dict[str, Any]] = {}
        self._attempts: dict[QuoteIdentity, int] = {}
        self._requested: set[QuoteIdentity] = set()
        self._fetcher_resolved: set[QuoteIdentity] = set()
        self._reuse_hits = 0

    @staticmethod
    def _mapped_value(
        mapping: Mapping[str, Any] | None,
        raw_code: str,
        canonical_code: str,
        default: Any = None,
    ) -> Any:
        if not mapping:
            return default
        for key in (raw_code, raw_code.upper(), canonical_code):
            if key in mapping:
                return mapping[key]
        return default

    @classmethod
    def _identity(
        cls,
        raw_code: str,
        asset_type_map: Mapping[str, Any] | None,
    ) -> QuoteIdentity | None:
        canonical = canonicalize_pricing_code(raw_code)
        if not canonical:
            return None
        asset_type = cls._mapped_value(asset_type_map, raw_code, canonical)
        try:
            detected_market = detect_market_type(raw_code)
        except Exception:
            detected_market = None
        market = market_type_from_asset_type(canonical, asset_type, detected_market)
        return canonical, str(market or "unknown")

    @staticmethod
    def _valid_payload(payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, Mapping):
            return None
        copied = deepcopy(dict(payload))
        try:
            price = positive_finite_decimal(copied.get("price"), "price")
            currency = str(copied.get("currency") or "CNY").strip().upper()
            cny_price = copied.get("cny_price")
            if cny_price is None and currency == "CNY":
                cny_price = price
            positive_finite_decimal(cny_price, "cny_price")
        except (TypeError, ValueError):
            return None
        copied.pop("is_from_run_pool", None)
        return copied

    @staticmethod
    def _can_reuse(payload: Mapping[str, Any], fetch_kwargs: Mapping[str, Any]) -> bool:
        if fetch_kwargs.get("force_refresh"):
            return False
        is_stale = payload.get("source") == "cache_fallback" or bool(payload.get("is_stale"))
        if is_stale and not fetch_kwargs.get("accept_stale_when_closed"):
            return False
        return True

    @staticmethod
    def _partition_delegations(identities: list[QuoteIdentity]) -> list[list[QuoteIdentity]]:
        """Keep canonical collisions across markets out of one downstream batch."""
        groups: list[list[QuoteIdentity]] = []
        canonical_by_group: list[set[str]] = []
        for identity in identities:
            canonical = identity[0]
            for index, seen_canonical in enumerate(canonical_by_group):
                if canonical not in seen_canonical:
                    groups[index].append(identity)
                    seen_canonical.add(canonical)
                    break
            else:
                groups.append([identity])
                canonical_by_group.append({canonical})
        return groups

    @staticmethod
    def _result_payload(
        fetched: Mapping[str, Any],
        *,
        representative: str,
        canonical: str,
    ) -> Any:
        for key in (representative, representative.upper(), canonical):
            if key in fetched:
                return fetched[key]
        for result_code, payload in fetched.items():
            if canonicalize_pricing_code(str(result_code)) == canonical:
                return payload
        return None

    def fetch_batch(
        self,
        codes: list[str],
        *,
        fetch_batch: Callable[..., Mapping[str, Any]],
        name_map: Mapping[str, str] | None = None,
        asset_type_map: Mapping[str, Any] | None = None,
        **fetch_kwargs: Any,
    ) -> dict[str, dict[str, Any]]:
        requests: list[tuple[str, QuoteIdentity]] = []
        representative_by_identity: dict[QuoteIdentity, str] = {}
        for code in codes or []:
            raw_code = str(code or "").strip()
            identity = self._identity(raw_code, asset_type_map)
            if identity is None:
                continue
            requests.append((raw_code, identity))
            self._requested.add(identity)
            representative_by_identity.setdefault(identity, raw_code)

        for identity in representative_by_identity:
            stored = self._quotes.get(identity)
            if stored is not None and not self._can_reuse(stored, fetch_kwargs):
                del self._quotes[identity]

        cached_before = {identity for _raw, identity in requests if identity in self._quotes}
        self._reuse_hits += len(cached_before)

        misses = [
            identity
            for identity in representative_by_identity
            if identity not in self._quotes and self._attempts.get(identity, 0) < self.max_attempts
        ]
        for group in self._partition_delegations(misses):
            representatives = [representative_by_identity[identity] for identity in group]
            scoped_names: dict[str, str] = {}
            scoped_asset_types: dict[str, Any] = {}
            for identity, representative in zip(group, representatives):
                canonical = identity[0]
                name = self._mapped_value(name_map, representative, canonical)
                if name is not None:
                    scoped_names[representative] = name
                asset_type = self._mapped_value(asset_type_map, representative, canonical)
                if asset_type is not None:
                    scoped_asset_types[representative] = asset_type
                self._attempts[identity] = self._attempts.get(identity, 0) + 1

            fetched = fetch_batch(
                representatives,
                name_map=scoped_names,
                asset_type_map=scoped_asset_types,
                **fetch_kwargs,
            )
            if not isinstance(fetched, Mapping):
                fetched = {}

            for identity, representative in zip(group, representatives):
                payload = self._result_payload(
                    fetched,
                    representative=representative,
                    canonical=identity[0],
                )
                valid = self._valid_payload(payload)
                if valid is None:
                    continue
                self._quotes[identity] = valid
                self._fetcher_resolved.add(identity)

        result: dict[str, dict[str, Any]] = {}
        for raw_code, identity in requests:
            stored = self._quotes.get(identity)
            if stored is None:
                continue
            payload = deepcopy(stored)
            payload["code"] = raw_code
            if identity in cached_before:
                payload["is_from_run_pool"] = True
            result[raw_code] = payload
        return result

    def summary(self) -> dict[str, int]:
        """Return deterministic facts observable at this pool boundary."""
        return {
            "unique_requested": len(self._requested),
            "fetch_attempted": len(self._attempts),
            "fetcher_resolved": len(self._fetcher_resolved),
            "run_reused": self._reuse_hits,
            "retried": sum(1 for attempts in self._attempts.values() if attempts > 1),
            "failed_unique": sum(1 for identity in self._requested if identity not in self._quotes),
        }
