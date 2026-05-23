"""Structured pricing results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PriceQuote:
    """Successful quote plus diagnostics that callers can persist or render."""

    code: str
    payload: Dict[str, Any]
    source: Optional[str] = None
    source_chain: list[str] = field(default_factory=list)
    cache_status: str = "realtime"
    stale: bool = False

    @classmethod
    def from_payload(
        cls,
        payload: Dict[str, Any],
        *,
        code: Optional[str] = None,
        cache_status: str = "realtime",
        stale: Optional[bool] = None,
        source_chain: Optional[list[str]] = None,
    ) -> "PriceQuote":
        data = dict(payload or {})
        quote_code = code or data.get("code") or data.get("asset_id") or ""
        source = data.get("source") or data.get("data_source")
        chain = list(source_chain or data.get("source_chain") or ([source] if source else []))
        is_stale = bool(data.get("is_stale")) if stale is None else bool(stale)

        data.setdefault("code", quote_code)
        data.setdefault("cache_status", cache_status)
        if chain:
            data.setdefault("source_chain", chain)
        if is_stale:
            data.setdefault("is_stale", True)

        return cls(
            code=str(quote_code),
            payload=data,
            source=source,
            source_chain=chain,
            cache_status=cache_status,
            stale=is_stale,
        )

    def to_payload(self) -> Dict[str, Any]:
        data = dict(self.payload)
        data.setdefault("code", self.code)
        data.setdefault("cache_status", self.cache_status)
        if self.source_chain:
            data.setdefault("source_chain", list(self.source_chain))
        if self.stale:
            data.setdefault("is_stale", True)
        return data


@dataclass
class PriceFailure:
    """Structured failure for a quote request."""

    code: str
    error_type: str
    message: str
    source_chain: list[str] = field(default_factory=list)
    fallback_quote: Optional[PriceQuote] = None

    def to_payload(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "code": self.code,
            "success": False,
            "error_type": self.error_type,
            "error": self.message,
            "source_chain": list(self.source_chain),
        }
        if self.fallback_quote is not None:
            data["fallback_quote"] = self.fallback_quote.to_payload()
        return data


@dataclass
class BatchPriceResult:
    """Batch quote result with successful quotes and structured failures."""

    quotes: Dict[str, PriceQuote] = field(default_factory=dict)
    failures: Dict[str, PriceFailure] = field(default_factory=dict)
    diagnostics: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def payloads(self) -> Dict[str, Dict[str, Any]]:
        return {code: quote.to_payload() for code, quote in self.quotes.items()}
