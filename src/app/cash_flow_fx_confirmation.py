"""Single authority for matching local FX evidence to a cash-flow row."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional


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
        "source_hash": (
            str(confirmation.get("source_hash") or ""),
            str(row.get("source_hash") or ""),
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
        numeric_matches = (
            Decimal(str(confirmation.get("exchange_rate")))
            == Decimal(str(row.get("exchange_rate")))
            and Decimal(str(confirmation.get("cny_amount")))
            == Decimal(str(row.get("cny_amount")))
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
    source = str(confirmation.get("exchange_rate_source") or "").strip()
    evidence_type = str(
        confirmation.get("exchange_rate_evidence_type") or ""
    ).strip()
    if not source or evidence_type not in ALLOWED_FX_EVIDENCE_TYPES:
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
        "source_hash": confirmation.get("source_hash"),
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
