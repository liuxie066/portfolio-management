"""Snapshot models for auditability.

This module intentionally keeps the snapshot schema minimal and stable.
Snapshots are written at NAV record time to make each NAV point reproducible.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import _quantize_decimal, MONEY_QUANT

QUANTITY_QUANT = Decimal("0.00000001")


class HoldingSnapshot(BaseModel):
    """A per-day holding snapshot row (one asset per row).

    Business key (recommended): (as_of, account, asset_id, broker)
    """

    record_id: Optional[str] = None

    as_of: str = Field(..., description="Business date (Asia/Shanghai) as YYYY-MM-DD")
    account: str
    asset_id: str
    broker: str

    quantity: float
    currency: str

    # Pricing used for valuation
    price: float
    cny_price: float
    market_value_cny: float

    # Optional metadata
    dedup_key: str
    asset_name: Optional[str] = None
    avg_cost: Optional[float] = None
    source: Optional[str] = None
    remark: Optional[str] = None

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra='forbid')

    @field_validator(
        'as_of', 'account', 'asset_id', 'broker', 'currency', 'dedup_key',
        mode='before',
    )
    @classmethod
    def _require_nonblank_text(cls, v, info):
        value = str(v or '').strip()
        if not value:
            raise ValueError(f'{info.field_name} must be nonblank')
        return value

    @field_validator('quantity', mode='before')
    @classmethod
    def _quantize_quantity(cls, v):
        value = Decimal(str(v))
        if not value.is_finite():
            raise ValueError('quantity must be a finite number')
        normalized = value.quantize(QUANTITY_QUANT, rounding=ROUND_HALF_UP)
        if normalized == 0:
            raise ValueError('quantity must be nonzero after normalization')
        return float(normalized)

    @field_validator('price', 'cny_price', mode='before')
    @classmethod
    def _preserve_unit_price_precision(cls, v, info):
        if v is None:
            raise ValueError(f'{info.field_name} is required')
        value = Decimal(str(v))
        if not value.is_finite():
            raise ValueError(f'{info.field_name} must be a finite number')
        if value <= 0:
            raise ValueError(f'{info.field_name} must be positive')
        return float(value)

    @field_validator('market_value_cny', mode='before')
    @classmethod
    def _quantize_market_value(cls, v):
        if v is None:
            raise ValueError('market_value_cny is required')
        value = Decimal(str(v))
        if not value.is_finite():
            raise ValueError('market_value_cny must be a finite number')
        return _quantize_decimal(value, MONEY_QUANT)

    @field_validator('avg_cost', mode='before')
    @classmethod
    def _quantize_avg_cost(cls, v):
        if v is None:
            return None
        value = Decimal(str(v))
        if not value.is_finite():
            raise ValueError('avg_cost must be a finite number')
        return _quantize_decimal(value, MONEY_QUANT)

    @model_validator(mode='after')
    def _assert_replayable_market_value(self):
        expected = (
            Decimal(str(self.quantity)) * Decimal(str(self.cny_price))
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        actual = Decimal(str(self.market_value_cny)).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        if actual != expected:
            raise ValueError(
                'market_value_cny replay mismatch: '
                f'{self.quantity} * {self.cny_price} -> {expected}, got {actual}'
            )
        return self
