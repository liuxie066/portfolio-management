from datetime import date
from decimal import Decimal
import hashlib
from unittest.mock import Mock

import pytest

from src.domain.cash_flow_contracts import (
    CashFlowContractError,
    CashFlowManualDatasetAudit,
    CompletedCashFlowFacts,
    ManualCashFlowFacts,
    RawCashFlowRecord,
    cash_flow_generated_fingerprint,
    expected_cash_flow_dedup_key_from_values,
    normalize_cash_flow_rate_source,
)
from src.feishu_storage import FeishuStorage


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


def test_manual_dataset_duplicate_authority_uses_expected_not_observed_key():
    records = [
        RawCashFlowRecord(
            record_id=record_id,
            raw_fields=_manual_fields(dedup_key=observed_key),
        )
        for record_id, observed_key in (
            ("cf_1", "tampered-a"),
            ("cf_2", "tampered-b"),
        )
    ]

    audit = CashFlowManualDatasetAudit.build(records)

    assert len(audit.duplicate_groups) == 1
    group = audit.duplicate_groups[0]
    assert group.record_ids == ("cf_1", "cf_2")
    assert group.expected_dedup_key not in {"tampered-a", "tampered-b"}
    assert set(audit.duplicate_by_record_id) == {"cf_1", "cf_2"}


def test_generated_fingerprint_is_canonical_and_covers_generated_fields():
    base = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100",
        currency="USD",
        exchange_rate="7.20",
        cny_amount="720.00",
        source="manual",
    )
    equivalent = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100.00",
        currency="USD",
        exchange_rate="7.2",
        cny_amount="720",
        source="manual",
    )
    changed_source = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100",
        currency="USD",
        exchange_rate="7.2",
        cny_amount="720",
        source="imported",
    )

    assert cash_flow_generated_fingerprint(base) == (
        cash_flow_generated_fingerprint(equivalent)
    )
    assert cash_flow_generated_fingerprint(base) != (
        cash_flow_generated_fingerprint(changed_source)
    )


@pytest.mark.parametrize(
    "source",
    ["", " ", "manual", "unknown", "N/A", "na", "none", "null", "-", "tbd"],
)
def test_rate_source_authority_rejects_placeholder_values(source):
    with pytest.raises(ValueError, match="traceable text"):
        normalize_cash_flow_rate_source(source)

    assert normalize_cash_flow_rate_source(" bank:receipt-1 ") == (
        "bank:receipt-1"
    )


def test_exact_reconcile_uses_fresh_full_account_scan_for_duplicate_gate():
    client = Mock()
    client.list_records.return_value = [
        {
            "record_id": record_id,
            "fields": _manual_fields(dedup_key=observed_key),
        }
        for record_id, observed_key in (
            ("cf_1", "tampered-a"),
            ("cf_2", "tampered-b"),
        )
    ]
    storage = FeishuStorage(client=client)

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=True,
    )

    assert result["source_scanned"] == 2
    assert result["scanned"] == 1
    assert result["change_count"] == 0
    assert result["rows"][0]["reason_code"] == (
        "cash_flow_expected_dedup_duplicate"
    )
    assert result["rows"][0]["duplicate_group"]["record_ids"] == [
        "cf_1",
        "cf_2",
    ]
    assert "RecordId()" not in str(client.list_records.call_args.kwargs["filter_str"])
    client.batch_update_records.assert_not_called()


def test_singleton_completed_row_is_verified_from_fresh_scan():
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100",
        currency="CNY",
        source="manual",
        record_id="cf_1",
    )
    client = Mock()
    client.list_records.return_value = [{
        "record_id": "cf_1",
        "fields": facts.to_fields(),
    }]
    storage = FeishuStorage(client=client)

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=True,
    )

    assert result["error_count"] == 0
    assert result["completed_count"] == 1
    assert result["readback_verified"] is True
    assert result["rows"][0]["completion_state"] == "completed"
    assert result["rows"][0]["generated_fingerprint"]


def test_manual_fx_date_mismatch_has_zero_feishu_update():
    client = Mock()
    client.list_records.return_value = [{
        "record_id": "cf_usd",
        "fields": _manual_fields(currency="USD"),
    }]
    storage = FeishuStorage(client=client)

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_usd",
        dry_run=False,
        manual_exchange_rate="7.2",
        rate_date=date(2026, 7, 25),
        rate_source="bank:receipt-1",
    )

    assert result["updated_count"] == 0
    assert result["error_count"] == 1
    assert "must equal cash_flow flow_date" in result["rows"][0]["error"]
    client.batch_update_records.assert_not_called()


def test_manual_fx_placeholder_source_fails_before_feishu_scan_or_update():
    client = Mock()
    storage = FeishuStorage(client=client)

    with pytest.raises(ValueError, match="traceable text"):
        storage.reconcile_cash_flows(
            record_id="cf_usd",
            dry_run=False,
            manual_exchange_rate="7.2",
            rate_date=date(2026, 7, 26),
            rate_source="unknown",
        )

    client.list_records.assert_not_called()
    client.batch_update_records.assert_not_called()


def test_legacy_fx_resolver_fails_closed_for_foreign_currency():
    storage = FeishuStorage(client=Mock())

    assert storage._resolve_cash_flow_exchange_rate(
        currency="CNY",
        amount=10,
        cny_amount=10,
        rate_cache={},
    ) == 1.0
    with pytest.raises(ValueError, match="cannot prove dated evidence"):
        storage._resolve_cash_flow_exchange_rate(
            currency="USD",
            amount=10,
            cny_amount=72,
            rate_cache={"USDCNY": 7.2},
        )


def test_apply_fails_closed_when_fresh_readback_does_not_converge():
    client = Mock()
    client.list_records.return_value = [{
        "record_id": "cf_1",
        "fields": _manual_fields(),
    }]
    client.batch_update_records.return_value = [{"record_id": "cf_1"}]
    storage = FeishuStorage(client=client)

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=False,
    )

    assert result["success"] is False
    assert result["reason_code"] == "cash_flow_readback_not_verified"
    assert result["readback_verified"] is False
    assert result["partial_write_possible"] is True
    assert result["rows"][0]["status"] == "pending"


def test_post_write_duplicate_fails_completed_readback_for_target():
    client = Mock()
    first = {"record_id": "cf_1", "fields": _manual_fields()}
    concurrent_duplicate = {
        "record_id": "cf_2",
        "fields": _manual_fields(dedup_key="tampered-observed-key"),
    }
    client.list_records.side_effect = [
        [first],
        [first, concurrent_duplicate],
    ]
    client.batch_update_records.return_value = [{"record_id": "cf_1"}]
    storage = FeishuStorage(client=client)

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=False,
    )

    assert result["success"] is False
    assert result["partial_write_possible"] is True
    assert result["rows"][0]["reason_code"] == (
        "cash_flow_expected_dedup_duplicate"
    )
    assert result["rows"][0]["duplicate_group"]["record_ids"] == [
        "cf_1",
        "cf_2",
    ]


def test_batch_exception_invalidates_cache_and_reports_partial_write_possible():
    client = Mock()
    client.list_records.return_value = [{
        "record_id": "cf_1",
        "fields": _manual_fields(),
    }]
    client.batch_update_records.side_effect = TimeoutError("timeout after send")
    storage = FeishuStorage(client=client)
    storage._cash_flow_agg_loaded_accounts.add("lx")
    storage._cash_flow_agg_mem_cache["lx"] = {"cumulative": 100.0}
    storage._local_cash_flow_agg_cache.set_account(
        "lx",
        {"cumulative": 100.0},
        _flush=True,
    )

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=False,
    )

    assert result["success"] is False
    assert result["reason_code"] == "cash_flow_batch_update_failed"
    assert result["partial_write_possible"] is True
    assert "lx" not in storage._cash_flow_agg_loaded_accounts
    assert "lx" not in storage._cash_flow_agg_mem_cache
    assert storage._local_cash_flow_agg_cache.get_account("lx") == {}


def test_readback_exception_preserves_known_update_impact():
    client = Mock()
    client.list_records.side_effect = [
        [{"record_id": "cf_1", "fields": _manual_fields()}],
        TimeoutError("fresh readback unavailable"),
    ]
    client.batch_update_records.return_value = [{"record_id": "cf_1"}]
    storage = FeishuStorage(client=client)

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=False,
    )

    assert result["success"] is False
    assert result["reason_code"] == "cash_flow_readback_failed"
    assert result["updated_count"] == 1
    assert result["partial_write_possible"] is True
    assert result["readback_verified"] is False


def test_flow_type_conflict_is_proposed_until_fresh_readback_confirms():
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 26),
        account="lx",
        broker="某券商",
        amount="100",
        currency="CNY",
        source="manual",
        record_id="cf_1",
    )
    wrong_fields = {**facts.to_fields(), "flow_type": "WITHDRAW"}
    client = Mock()
    client.list_records.side_effect = [
        [{"record_id": "cf_1", "fields": wrong_fields}],
        [{"record_id": "cf_1", "fields": wrong_fields}],
        [{"record_id": "cf_1", "fields": facts.to_fields()}],
    ]
    client.batch_update_records.return_value = [{"record_id": "cf_1"}]
    storage = FeishuStorage(client=client)

    preview = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=True,
    )

    assert preview["rows"][0]["status"] == "pending"
    assert preview["rows"][0]["completion_state"] == "proposed"
    assert preview["rows"][0]["generated_fingerprint"] is None

    result = storage.reconcile_cash_flows(
        account="lx",
        record_id="cf_1",
        dry_run=False,
    )

    assert result["change_count"] == 1
    assert result["updated_count"] == 1
    assert result["readback_verified"] is True
    assert result["rows"][0]["completion_state"] == "completed"
    assert result["rows"][0]["applied_updates"] == {
        "flow_type": "DEPOSIT"
    }
