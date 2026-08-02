"""Transport-neutral holdings source contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite
import re
from typing import Any, Mapping, Optional

from src.models import AssetClass, AssetType


_PROVIDER_NUMBER_PATTERN = re.compile(
    r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)


class ProviderValueState(str, Enum):
    """State of one provider value before business defaults can be applied."""

    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True)
class ProviderNumericFact:
    """Finite provider number with missing and invalid kept distinct."""

    state: ProviderValueState
    value: Optional[float]
    raw: Any = None

    @classmethod
    def parse(cls, raw: Any) -> "ProviderNumericFact":
        if raw is None:
            return cls(ProviderValueState.MISSING, None, raw)
        if isinstance(raw, bool):
            return cls(ProviderValueState.INVALID, None, raw)
        if isinstance(raw, str):
            normalized = raw.strip()
            if not normalized or normalized.upper() == "N/A":
                return cls(ProviderValueState.MISSING, None, raw)
            if not _PROVIDER_NUMBER_PATTERN.fullmatch(normalized):
                return cls(ProviderValueState.INVALID, None, raw)
        try:
            parsed = Decimal(str(raw).strip())
        except (InvalidOperation, AttributeError, TypeError, ValueError):
            return cls(ProviderValueState.INVALID, None, raw)
        if not parsed.is_finite():
            return cls(ProviderValueState.INVALID, None, raw)
        try:
            value = float(parsed)
        except (OverflowError, ValueError):
            return cls(ProviderValueState.INVALID, None, raw)
        if not isfinite(value):
            return cls(ProviderValueState.INVALID, None, raw)
        return cls(ProviderValueState.VALID, value, raw)

    @property
    def is_valid(self) -> bool:
        return self.state == ProviderValueState.VALID


def asset_class_for_economic_exposure(
    asset_type: Optional[AssetType | str],
) -> Optional[AssetClass]:
    """Classify only when instrument type proves underlying exposure."""

    if asset_type is None:
        return None
    try:
        resolved = (
            asset_type
            if isinstance(asset_type, AssetType)
            else AssetType(str(asset_type).strip().lower())
        )
    except ValueError:
        return None
    if resolved == AssetType.A_STOCK:
        return AssetClass.CN_ASSET
    if resolved in {AssetType.CASH, AssetType.MMF}:
        return AssetClass.CASH
    return None


@dataclass(frozen=True)
class RawHoldingRecord:
    """One untyped source row before any model or cache defaults."""

    record_id: str
    raw_fields: Mapping[str, Any]
    source: str = "feishu"
    fetched_at: Optional[datetime] = None

    def canonical_fields(self) -> dict[str, Any]:
        return {str(key): value for key, value in self.raw_fields.items()}
