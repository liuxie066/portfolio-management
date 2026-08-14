from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.app.cash_flow_effect_service import CashFlowEffectService
from src.app.cash_flow_summary_service import CashFlowSummaryService
from src.domain.cash_flow_contracts import (
    CASH_FLOW_DATASET_CONTRACT_VERSION,
    CashFlowContractError,
    CashFlowDatasetRefusal,
    CompletedCashFlowFacts,
    RawCashFlowRecord,
    expected_cash_flow_dedup_key_from_values,
)
from src.models import CashFlow, NAVHistory


def _completed_flow(
    flow_date,
    amount,
    *,
    currency="CNY",
    rate=1,
    record_id="cf_1",
):
    return CompletedCashFlowFacts.build(
        flow_date=flow_date,
        account="a",
        broker="某券商",
        amount=amount,
        currency=currency,
        exchange_rate=rate,
        cny_amount=amount * rate,
        source="test",
        record_id=record_id,
    ).to_cash_flow()


def _raw(flow: CashFlow) -> RawCashFlowRecord:
    return RawCashFlowRecord.from_cash_flow(flow)


def _storage(rows):
    return SimpleNamespace(get_raw_cash_flows=Mock(return_value=list(rows)))


def test_cash_flow_summary_service_ignores_stale_aggregate_cache_and_reflects_edits():
    rows = [
        _raw(_completed_flow(date(2024, 12, 31), 10000, record_id="cf_2024")),
        _raw(_completed_flow(date(2025, 3, 1), 20000, record_id="cf_0301")),
        _raw(_completed_flow(date(2025, 3, 14), 5000, record_id="cf_0314")),
        _raw(_completed_flow(date(2025, 3, 15), 123, record_id="cf_future")),
    ]
    storage = _storage(rows)
    storage.get_cash_flow_aggs = Mock(return_value={
        "daily": {"2025-03-14": -999999},
        "monthly": {"2025-03": -999999},
        "yearly": {"2025": -999999},
    })
    service = CashFlowSummaryService(storage=storage)
    last_nav = NAVHistory(
        date=date(2025, 3, 13),
        account="a",
        total_value=1000.0,
        nav=1.0,
        shares=1000.0,
    )

    first = service.summarize(
        "a",
        date(2025, 3, 14),
        2024,
        last_nav=last_nav,
    )
    storage.get_raw_cash_flows.return_value = [
        rows[0],
        rows[1],
        _raw(_completed_flow(date(2025, 3, 14), 7000, record_id="cf_0314")),
        _raw(_completed_flow(date(2025, 3, 10), 1000, record_id="cf_added")),
    ]
    second = service.summarize(
        "a",
        date(2025, 3, 14),
        2024,
        last_nav=last_nav,
    )
    storage.get_raw_cash_flows.return_value = [
        rows[0],
        _raw(_completed_flow(date(2025, 3, 14), 7000, record_id="cf_0314")),
    ]
    third = service.summarize(
        "a",
        date(2025, 3, 14),
        2024,
        last_nav=last_nav,
    )

    assert first == {
        "daily": 5000.0,
        "monthly": 25000.0,
        "yearly": {"2024": 10000.0, "2025": 25000.0},
        "cumulative": 35000.0,
        "gap": 5000.0,
    }
    assert second["daily"] == 7000.0
    assert second["monthly"] == 28000.0
    assert second["cumulative"] == 38000.0
    assert third["monthly"] == 7000.0
    assert third["cumulative"] == 17000.0
    assert storage.get_raw_cash_flows.call_count == 3
    storage.get_cash_flow_aggs.assert_not_called()


def test_cash_flow_summary_service_period_and_point_queries_use_fresh_rows():
    storage = _storage([
        _raw(_completed_flow(date(2025, 3, 1), 100, record_id="cf_1")),
        _raw(_completed_flow(date(2025, 3, 14), 50, record_id="cf_2")),
    ])
    service = CashFlowSummaryService(storage=storage)

    assert service.daily("a", date(2025, 3, 14)) == 50.0
    assert service.monthly("a", 2025, 3) == 150.0
    assert service.yearly("a", "2025") == 150.0
    assert service.period("a", date(2025, 3, 2), date(2025, 3, 14)) == 50.0
    assert storage.get_raw_cash_flows.call_count == 4


def test_cash_flow_summary_service_sums_cash_flow_objects():
    flows = [
        _completed_flow(date(2025, 3, 1), 100, record_id="cf_1"),
        _completed_flow(date(2025, 3, 2), 50, record_id="cf_2"),
        _completed_flow(date(2025, 3, 3), -20, record_id="cf_3"),
    ]

    assert CashFlowSummaryService.sum_cash_flows(flows) == 130.0


def test_cash_flow_summary_service_rejects_foreign_flow_without_cny_amount():
    flow = CashFlow(
        record_id="cf_usd",
        flow_date=date(2025, 3, 1),
        account="a",
        broker="某券商",
        amount=10,
        currency="USD",
        cny_amount=None,
        exchange_rate=None,
        flow_type="DEPOSIT",
        dedup_key=expected_cash_flow_dedup_key_from_values(
            flow_date=date(2025, 3, 1),
            account="a",
            broker="某券商",
            amount=10,
            currency="USD",
            flow_type="DEPOSIT",
        ),
        source="test",
    )

    with pytest.raises(CashFlowContractError) as exc_info:
        CashFlowSummaryService.sum_cash_flows([flow])

    assert {issue.reason_code for issue in exc_info.value.issues} >= {
        "EXCHANGE_RATE_MISSING",
        "CNY_AMOUNT_MISSING",
    }


def test_cash_flow_dataset_missing_date_blocks_without_partial_authority():
    missing_date = RawCashFlowRecord(
        record_id="cf_missing_date",
        raw_fields={
            "flow_date": None,
            "account": "a",
            "broker": "某券商",
            "amount": 100,
            "currency": "CNY",
            "cny_amount": 100,
            "exchange_rate": 1,
            "flow_type": "DEPOSIT",
            "dedup_key": "untrusted-without-date",
            "source": "test",
        },
    )
    service = CashFlowSummaryService(storage=_storage([
        _raw(_completed_flow(date(2025, 3, 1), 50, record_id="cf_valid")),
        missing_date,
    ]))

    dataset = service.build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-missing-date",
        start_year=2025,
    )

    assert dataset.complete is False
    assert "FLOW_DATE_MISSING" in {
        blocker.reason_code for blocker in dataset.blockers
    }
    assert dataset.cumulative == Decimal("50.00")
    with pytest.raises(CashFlowDatasetRefusal, match="has blockers") as exc_info:
        dataset.assert_official_scope(
            account="a",
            nav_date=date(2025, 3, 14),
            run_id="run-missing-date",
            start_year=2025,
        )
    assert exc_info.value.reason_code == "CASH_FLOW_DATASET_BLOCKED"
    assert exc_info.value.blockers == dataset.blockers
    assert exc_info.value.blockers[0].reason_code == "FLOW_DATE_MISSING"


def test_cash_flow_dataset_future_rows_are_audit_only_and_do_not_affect_totals():
    service = CashFlowSummaryService(storage=_storage([
        _raw(_completed_flow(date(2025, 3, 14), 50, record_id="cf_current")),
        _raw(_completed_flow(date(2025, 3, 15), 500, record_id="cf_future")),
    ]))

    dataset = service.build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-future",
        start_year=2025,
    )

    assert dataset.complete is True
    assert dataset.cumulative == Decimal("50.00")
    assert dataset.audit_only_record_ids == ("cf_future",)
    assert "2025-03-15" not in dataset.daily


def test_cash_flow_dataset_fingerprint_distinguishes_missing_and_null():
    common = {
        "flow_date": date(2025, 3, 14),
        "account": "a",
        "broker": "某券商",
        "amount": 50,
        "currency": "CNY",
        "flow_type": "DEPOSIT",
        "cny_amount": 50,
        "dedup_key": expected_cash_flow_dedup_key_from_values(
            flow_date=date(2025, 3, 14),
            account="a",
            broker="某券商",
            amount=50,
            currency="CNY",
            flow_type="DEPOSIT",
        ),
        "exchange_rate": 1,
        "source": "test",
    }
    missing = CashFlowSummaryService(storage=_storage([
        RawCashFlowRecord(record_id="cf_1", raw_fields=common),
    ])).build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-missing",
        start_year=2025,
    )
    null = CashFlowSummaryService(storage=_storage([
        RawCashFlowRecord(record_id="cf_1", raw_fields={**common, "remark": None}),
    ])).build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-null",
        start_year=2025,
    )

    assert missing.contract_version == CASH_FLOW_DATASET_CONTRACT_VERSION
    assert missing.financial_fingerprint == null.financial_fingerprint
    assert missing.full_fingerprint != null.full_fingerprint


def test_cash_flow_dataset_rejects_completed_rows_detached_from_raw_source():
    dataset = CashFlowSummaryService(storage=_storage([
        _raw(_completed_flow(date(2025, 3, 14), 50, record_id="cf_1")),
    ])).build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-integrity",
        start_year=2025,
    )
    detached = replace(
        dataset,
        completed_rows=(),
        daily={},
        monthly={},
        yearly={},
        cumulative=Decimal("0"),
    )

    with pytest.raises(CashFlowDatasetRefusal, match="completed_rows") as exc_info:
        detached.assert_official_scope(
            account="a",
            nav_date=date(2025, 3, 14),
            run_id="run-integrity",
            start_year=2025,
        )
    assert exc_info.value.reason_code == "CASH_FLOW_DATASET_SCOPE_MISMATCH"
    assert tuple(exc_info.value.details["mismatches"]) == ("completed_rows",)


def test_cash_flow_dataset_repeated_source_record_id_blocks_aggregation():
    row = _raw(_completed_flow(date(2025, 3, 14), 50, record_id="cf_1"))
    dataset = CashFlowSummaryService(storage=_storage([row, row])).build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-duplicate-id",
        start_year=2025,
    )

    assert dataset.complete is False
    assert dataset.cumulative == Decimal("0")
    assert "RECORD_ID_DUPLICATE" in {
        blocker.reason_code for blocker in dataset.blockers
    }


def test_cash_flow_dataset_deep_freezes_nested_raw_field_values():
    nested = {"labels": ["observed"]}
    record = RawCashFlowRecord(
        record_id="cf_nested",
        raw_fields={"extra": nested},
    )
    before = record.canonical_fields()

    nested["labels"].append("mutated-outside")
    projected = record.canonical_fields()
    projected["extra"]["labels"].append("mutated-projection")

    assert before == {"extra": {"labels": ["observed"]}}
    assert record.canonical_fields() == before
    assert tuple(record.raw_fields["extra"]["labels"]) == ("observed",)


def test_cash_flow_dataset_blocks_effect_revision_for_another_source():
    class StaleEffectGate:
        def nav_gate(self, *, account, nav_date, cash_flow_dataset):
            return {
                "success": True,
                "account": account,
                "nav_date": nav_date.isoformat(),
                "effect_store_revision": "scan-other-source",
                "cash_flow_financial_fingerprint": "other-source",
            }

    dataset = CashFlowSummaryService(storage=_storage([
        _raw(_completed_flow(date(2025, 3, 14), 50, record_id="cf_1")),
    ])).build_dataset(
        account="a",
        nav_date=date(2025, 3, 14),
        run_id="run-stale-gate",
        start_year=2025,
        cash_flow_effect_service=StaleEffectGate(),
    )

    assert dataset.complete is False
    assert "EFFECT_SOURCE_FINGERPRINT_MISMATCH" in {
        blocker.reason_code for blocker in dataset.blockers
    }


def test_effect_nav_gate_projects_account_specific_correction_operations():
    service = object.__new__(CashFlowEffectService)
    previous = {
        "source": {
            "account": "lx",
            "broker": "平安证券",
            "currency": "CNY",
            "signed_amount": "20.00",
            "flow_date": "2026-08-13",
        }
    }
    service.store = SimpleNamespace(
        get_previous_applied=lambda _effect_id: previous
    )
    service.scan = lambda **_kwargs: {
        "scan_run": {"scan_run_id": "scan-safe-blocker"}
    }
    service._blockers_for_account = lambda **_kwargs: [{
        "effect_id": "cfe_pending",
        "effect_kind": "cash_flow",
        "state": "pending",
        "record_id": "cf_1",
        "flow_date": "2026-08-13",
        "account": "sy",
        "broker": "华泰证券",
        "currency": "CNY",
        "signed_amount": "20.00",
        "source": {
            "account": "sy",
            "broker": "华泰证券",
            "currency": "CNY",
            "signed_amount": "20.00",
            "flow_date": "2026-08-13",
        },
        "last_error": None,
    }]
    lx_dataset = SimpleNamespace(
        account="lx",
        nav_date=date(2026, 8, 13),
        financial_fingerprint="cash-fingerprint",
    )
    sy_dataset = SimpleNamespace(
        account="sy",
        nav_date=date(2026, 8, 13),
        financial_fingerprint="cash-fingerprint",
    )

    lx_gate = service.nav_gate(
        account="lx",
        nav_date=date(2026, 8, 13),
        cash_flow_dataset=lx_dataset,
    )
    sy_gate = service.nav_gate(
        account="sy",
        nav_date=date(2026, 8, 13),
        cash_flow_dataset=sy_dataset,
    )

    assert lx_gate["blockers"][0]["broker"] == "平安证券"
    assert lx_gate["blockers"][0]["signed_amount"] == "-20.00"
    assert lx_gate["blockers"][0]["operations"] == [{
        "account": "lx",
        "broker": "平安证券",
        "currency": "CNY",
        "signed_amount": "-20.00",
        "flow_date": "2026-08-13",
    }]
    assert sy_gate["blockers"][0]["broker"] == "华泰证券"
    assert sy_gate["blockers"][0]["signed_amount"] == "20.00"
    assert sy_gate["blockers"][0]["operations"][0]["account"] == "sy"
