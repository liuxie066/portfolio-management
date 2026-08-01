from datetime import date
from decimal import Decimal
import hashlib

import pytest

from src.domain.cash_flow_contracts import (
    CashFlowContractError,
    CompletedCashFlowFacts,
    ManualCashFlowFacts,
    RawCashFlowRecord,
    expected_cash_flow_dedup_key_from_values,
)


def _manual_fields(**overrides):
    fields = {
        "flow_date": date(2026, 7, 26),
        "account": "lx",
        "broker": "某券商",
        "amount": "100.00",
        "currency": "CNY",
        "remark": "bank receipt",
    }
    fields.update(overrides)
    return fields


def _completed_fields(**overrides):
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100.00",
        currency="CNY",
        source="manual",
        remark="bank receipt",
        record_id="cf_1",
    )
    fields = facts.to_fields()
    fields.update(overrides)
    return fields


def _reason_codes(fields):
    _, issues = CompletedCashFlowFacts.validate(
        RawCashFlowRecord(record_id="cf_1", raw_fields=fields)
    )
    return {issue.reason_code for issue in issues}


def test_raw_cash_flow_record_is_immutable_and_copy_preserves_missing():
    source = {"amount": 100, "currency": None}
    record = RawCashFlowRecord(record_id=" cf_1 ", raw_fields=source)
    source["amount"] = 200

    assert record.record_id == "cf_1"
    assert record.canonical_fields() == {"amount": 100, "currency": None}
    with pytest.raises(TypeError):
        record.raw_fields["amount"] = 300


def test_manual_cash_flow_facts_use_decimal_and_canonical_values():
    facts = ManualCashFlowFacts.require(RawCashFlowRecord(
        record_id="cf_1",
        raw_fields=_manual_fields(
            account=" lx ",
            broker=" 某券商 ",
            amount="100.005",
            currency="cny",
        ),
    ))

    assert facts.flow_date == date(2026, 7, 26)
    assert facts.account == "lx"
    assert facts.broker == "某券商"
    assert facts.amount == Decimal("100.01")
    assert facts.currency == "CNY"


@pytest.mark.parametrize(
    ("field", "value", "reason_code"),
    [
        ("flow_date", None, "FLOW_DATE_MISSING"),
        ("flow_date", "not-a-date", "FLOW_DATE_INVALID"),
        ("account", "  ", "ACCOUNT_MISSING"),
        ("broker", None, "BROKER_MISSING"),
        ("currency", None, "CURRENCY_MISSING"),
        ("currency", "EUR", "CURRENCY_UNSUPPORTED"),
        ("amount", None, "AMOUNT_MISSING"),
        ("amount", 0, "AMOUNT_ZERO"),
        ("amount", "0.001", "AMOUNT_ZERO"),
        ("amount", "NaN", "AMOUNT_INVALID"),
        ("amount", "Infinity", "AMOUNT_INVALID"),
    ],
)
def test_manual_cash_flow_validation_blocks_invalid_authoritative_fields(
    field,
    value,
    reason_code,
):
    _, issues = ManualCashFlowFacts.validate(RawCashFlowRecord(
        record_id="cf_bad",
        raw_fields=_manual_fields(**{field: value}),
    ))

    assert reason_code in {issue.reason_code for issue in issues}


def test_completed_cash_flow_builds_canonical_cny_fact():
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount=100,
        currency="CNY",
        source="manual",
    )

    assert facts.flow_type == "DEPOSIT"
    assert facts.exchange_rate == Decimal("1")
    assert facts.cny_amount == Decimal("100.00")
    assert facts.dedup_key == expected_cash_flow_dedup_key_from_values(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount=100,
        currency="CNY",
        flow_type="DEPOSIT",
    )


def test_canonical_dedup_key_preserves_existing_float_fingerprint_shape():
    expected_raw = "lx|某券商|2026-07-26|DEPOSIT|100.0|CNY"
    expected = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()[:16]

    assert expected_cash_flow_dedup_key_from_values(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100.00",
        currency="CNY",
        flow_type="DEPOSIT",
    ) == expected


def test_canonical_dedup_key_preserves_existing_scientific_float_fingerprint():
    expected_raw = "lx|某券商|2026-07-26|DEPOSIT|1e+20|CNY"
    expected = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()[:16]

    assert expected_cash_flow_dedup_key_from_values(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100000000000000000000.00",
        currency="CNY",
        flow_type="DEPOSIT",
    ) == expected


def test_completed_cash_flow_builds_foreign_fact_only_with_rate_and_cny_amount():
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount=-10,
        currency="USD",
        exchange_rate="7.2",
        cny_amount="-72",
        source="manual",
    )

    assert facts.flow_type == "WITHDRAW"
    assert facts.exchange_rate == Decimal("7.2")
    assert facts.cny_amount == Decimal("-72.00")

    with pytest.raises(CashFlowContractError) as exc_info:
        CompletedCashFlowFacts.build(
            flow_date=date(2026, 7, 26),
            account="lx",
            broker="某券商",
            amount=10,
            currency="USD",
            source="manual",
        )
    assert {issue.field for issue in exc_info.value.issues} >= {
        "exchange_rate",
        "cny_amount",
    }


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        ({"flow_type": "WITHDRAW"}, "FLOW_TYPE_SIGN_MISMATCH"),
        ({"exchange_rate": 0}, "EXCHANGE_RATE_NOT_POSITIVE"),
        ({"exchange_rate": 2}, "CNY_RATE_NOT_ONE"),
        ({"cny_amount": 99}, "CNY_AMOUNT_MISMATCH"),
        ({"dedup_key": "observed-wrong-key"}, "DEDUP_KEY_MISMATCH"),
        ({"source": "  "}, "SOURCE_MISSING"),
    ],
)
def test_completed_validation_uses_one_cross_field_contract(overrides, reason_code):
    assert reason_code in _reason_codes(_completed_fields(**overrides))


def test_completed_validation_requires_persisted_system_fields():
    fields = _manual_fields()
    reason_codes = _reason_codes(fields)

    assert reason_codes >= {
        "FLOW_TYPE_MISSING",
        "EXCHANGE_RATE_MISSING",
        "CNY_AMOUNT_MISSING",
        "DEDUP_KEY_MISSING",
        "SOURCE_MISSING",
    }


def test_completed_to_cash_flow_preserves_observed_fields_and_runtime_replay_marker():
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount=100,
        currency="CNY",
        source="manual",
        remark="memo",
        record_id="cf_1",
        updated_at="2026-07-26 10:00:00",
    ).with_record_id("cf_1", replayed=True)

    flow = facts.to_cash_flow()

    assert flow.dedup_key == facts.dedup_key
    assert flow.source == "manual"
    assert flow.remark == "memo"
    assert flow.updated_at == "2026-07-26 10:00:00"
    assert flow.was_replayed is True
    assert "was_replayed" not in flow.model_dump()
