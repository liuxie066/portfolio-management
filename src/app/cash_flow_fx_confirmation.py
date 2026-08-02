"""Single authority for matching local FX evidence to a cash-flow row."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from src.domain.cash_flow_contracts import normalize_cash_flow_rate_source


ALLOWED_FX_EVIDENCE_TYPES = frozenset({"provider", "manual_supplement"})


def evaluate_cash_flow_fx_confirmation(
    row: Dict[str, Any],
    confirmation: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return a structured exact-match decision for one foreign cash-flow row."""

    record_id = str(row.get("record_id") or "").strip()
    if not confirmation:
        return {
            "valid": False,
            "reason_code": "fx_confirmation_missing",
            "record_id": record_id,
            "confirmation_id": None,
        }
    confirmation_id = str(confirmation.get("confirmation_id") or "").strip() or None
    comparisons = {
        "record_id": (
            str(confirmation.get("record_id") or "").strip(),
            record_id,
        ),
        "generated_fingerprint": (
            str(
                confirmation.get("generated_fingerprint")
                or confirmation.get("source_hash")
                or ""
            ),
            str(
                row.get("generated_fingerprint")
                or row.get("source_hash")
                or ""
            ),
        ),
        "exchange_rate_date": (
            str(confirmation.get("exchange_rate_date") or ""),
            str(row.get("flow_date") or ""),
        ),
    }
    for field, (actual, expected) in comparisons.items():
        if not expected or actual != expected:
            return {
                "valid": False,
                "reason_code": "fx_confirmation_stale",
                "mismatch_field": field,
                "record_id": record_id,
                "confirmation_id": confirmation_id,
            }
    try:
        confirmation_rate = Decimal(str(confirmation.get("exchange_rate")))
        row_rate = Decimal(str(row.get("exchange_rate")))
        confirmation_cny = Decimal(str(confirmation.get("cny_amount")))
        row_cny = Decimal(str(row.get("cny_amount")))
        numeric_matches = (
            confirmation_rate.is_finite()
            and row_rate.is_finite()
            and confirmation_rate > 0
            and row_rate > 0
            and confirmation_cny.is_finite()
            and row_cny.is_finite()
            and confirmation_rate == row_rate
            and confirmation_cny == row_cny
        )
    except (InvalidOperation, TypeError, ValueError):
        numeric_matches = False
    if not numeric_matches:
        return {
            "valid": False,
            "reason_code": "fx_confirmation_stale",
            "mismatch_field": "exchange_rate_or_cny_amount",
            "record_id": record_id,
            "confirmation_id": confirmation_id,
        }
    evidence_type = str(
        confirmation.get("exchange_rate_evidence_type") or ""
    ).strip()
    try:
        normalize_cash_flow_rate_source(
            confirmation.get("exchange_rate_source")
        )
    except ValueError:
        source_is_traceable = False
    else:
        source_is_traceable = True
    if (
        not source_is_traceable
        or evidence_type not in ALLOWED_FX_EVIDENCE_TYPES
    ):
        return {
            "valid": False,
            "reason_code": "fx_confirmation_stale",
            "mismatch_field": "evidence_authority",
            "record_id": record_id,
            "confirmation_id": confirmation_id,
        }
    return {
        "valid": True,
        "reason_code": "fx_confirmation_valid",
        "record_id": record_id,
        "confirmation_id": confirmation_id,
    }


def frozen_fx_confirmation_identity(
    confirmation: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return only stable evidence facts allowed in semantic receipt identity."""

    if not confirmation:
        return {"state": "no_confirmation"}
    return {
        "state": "confirmation",
        "confirmation_id": confirmation.get("confirmation_id"),
        "record_id": confirmation.get("record_id"),
        "generated_fingerprint": (
            confirmation.get("generated_fingerprint")
            or confirmation.get("source_hash")
        ),
        "exchange_rate": confirmation.get("exchange_rate"),
        "cny_amount": confirmation.get("cny_amount"),
        "exchange_rate_date": confirmation.get("exchange_rate_date"),
        "exchange_rate_source": confirmation.get("exchange_rate_source"),
        "exchange_rate_evidence_type": confirmation.get(
            "exchange_rate_evidence_type"
        ),
    }


__all__ = [
    "ALLOWED_FX_EVIDENCE_TYPES",
    "evaluate_cash_flow_fx_confirmation",
    "frozen_fx_confirmation_identity",
]
