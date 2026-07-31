from __future__ import annotations

import pytest

from src.app.cash_flow_fx_confirmation import (
    evaluate_cash_flow_fx_confirmation,
    frozen_fx_confirmation_identity,
)


ROW = {
    "record_id": "rec-fx-1",
    "source_hash": "hash-1",
    "flow_date": "2026-07-31",
    "exchange_rate": "7.200",
    "cny_amount": "720.00",
}


def _confirmation(**overrides):
    value = {
        "confirmation_id": "fx-1",
        "source_hash": "hash-1",
        "exchange_rate": "7.2",
        "cny_amount": "720",
        "exchange_rate_date": "2026-07-31",
        "exchange_rate_source": "provider:example",
        "exchange_rate_evidence_type": "provider",
    }
    value.update(overrides)
    return value


def test_fx_confirmation_requires_local_evidence():
    result = evaluate_cash_flow_fx_confirmation(ROW, None)

    assert result == {
        "valid": False,
        "reason_code": "fx_confirmation_missing",
        "record_id": "rec-fx-1",
        "confirmation_id": None,
    }


@pytest.mark.parametrize(
    ("overrides", "mismatch_field"),
    [
        ({"source_hash": "hash-old"}, "source_hash"),
        ({"exchange_rate_date": "2026-07-30"}, "exchange_rate_date"),
        ({"exchange_rate": "7.19"}, "exchange_rate_or_cny_amount"),
        ({"cny_amount": "719"}, "exchange_rate_or_cny_amount"),
        ({"exchange_rate_source": ""}, "evidence_authority"),
        ({"exchange_rate_evidence_type": "guess"}, "evidence_authority"),
    ],
)
def test_fx_confirmation_rejects_stale_or_unauthorized_evidence(
    overrides,
    mismatch_field,
):
    result = evaluate_cash_flow_fx_confirmation(ROW, _confirmation(**overrides))

    assert result["valid"] is False
    assert result["reason_code"] == "fx_confirmation_stale"
    assert result["mismatch_field"] == mismatch_field
    assert result["confirmation_id"] == "fx-1"


def test_fx_confirmation_uses_decimal_equivalence_and_allowed_manual_evidence():
    result = evaluate_cash_flow_fx_confirmation(
        ROW,
        _confirmation(exchange_rate_evidence_type="manual_supplement"),
    )

    assert result == {
        "valid": True,
        "reason_code": "fx_confirmation_valid",
        "record_id": "rec-fx-1",
        "confirmation_id": "fx-1",
    }


def test_frozen_identity_excludes_operator_and_timestamps():
    confirmation = _confirmation(
        confirmed_at="2026-07-31T23:30:00",
        confirmation={"operator": "tester"},
    )

    identity = frozen_fx_confirmation_identity(confirmation)

    assert identity["confirmation_id"] == "fx-1"
    assert "confirmed_at" not in identity
    assert "confirmation" not in identity

