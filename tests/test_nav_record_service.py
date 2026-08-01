from dataclasses import replace
from datetime import date, datetime
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from skill_api import PortfolioSkill
from src import config
from src.app.cash_flow_summary_service import CashFlowSummaryService
from src.app.compensation_service import PartialWriteError
from src.app.nav_finality import NavWriteContext
from src.app.nav_record_service import NavRecordService
from src.app.operation_state_store import OperationStateStore
from src.app.valuation_service import ValuationService
from src.domain.cash_flow_contracts import (
    CompletedCashFlowFacts,
    RawCashFlowRecord,
    cash_flow_generated_fingerprint,
)
from src.domain.nav_calculator import ClosedNavTarget
from src.domain.snapshot_contracts import (
    NormalizedValuationSnapshot,
    attached_normalized_valuation,
)
from src.models import AssetClass, AssetType, Holding, NAVHistory, PortfolioValuation
from src.maintenance.nav_history_repair import backfill
from src.maintenance.nav_history_repair.common import (
    BASE_FIELDS,
    MAINTENANCE_FIELDS,
    FieldState,
    FreshNavRow,
    recompute_derived_row,
)
from src.maintenance.nav_history_repair.context import NavRepairContext
from src.portfolio import PortfolioManager


def _valuation(warnings=None, holdings_provenance=None):
    row_inputs = (
        (
            Holding(
                asset_id="CNY-CASH",
                asset_name="Cash",
                asset_type=AssetType.CASH,
                account="a",
                broker="TEST",
                quantity=200,
                currency="CNY",
                asset_class=AssetClass.CASH,
            ),
            "cash",
            "1",
        ),
        (
            Holding(
                asset_id="000001",
                asset_name="Equity",
                asset_type=AssetType.A_STOCK,
                account="a",
                broker="TEST",
                quantity=90,
                currency="CNY",
                asset_class=AssetClass.CN_ASSET,
            ),
            "equity",
            "10",
        ),
        (
            Holding(
                asset_id="FUND-1",
                asset_name="Fund",
                asset_type=AssetType.FUND,
                account="a",
                broker="TEST",
                quantity=10,
                currency="CNY",
                asset_class=AssetClass.CN_ASSET,
            ),
            "fund",
            "10",
        ),
    )
    holdings = [holding for holding, _normalized_type, _price in row_inputs]
    price_snapshot = {
        holding.asset_id: {
            "price": price,
            "cny_price": price,
            "currency": "CNY",
            "source": "test_fixture",
        }
        for holding, _normalized_type, price in row_inputs
    }
    normalized = ValuationService(
        manager=None,
        storage=Mock(),
        price_fetcher=None,
    ).calculate_normalized_valuation(
        account="a",
        holdings=holdings,
        price_snapshot=price_snapshot,
        total_shares=1000,
        holdings_provenance=holdings_provenance,
        price_warnings=list(warnings or []),
    )
    return normalized.to_portfolio_valuation()


def _storage():
    storage = Mock()
    storage.get_nav_index.return_value = {"_nav_objects": []}
    storage.get_raw_cash_flows.return_value = []

    def write_nav(nav, **_kwargs):
        nav.record_id = nav.record_id or "nav-1"
        return nav

    storage.write_nav_record.side_effect = write_nav
    storage.write_nav_records.side_effect = lambda navs, **_kwargs: [write_nav(nav) for nav in navs]
    return storage


def _manager(storage):
    manager = PortfolioManager(storage=storage, price_fetcher=Mock())
    manager.snapshot_service = Mock()
    manager.snapshot_service.build_holdings_snapshots.return_value = []
    manager._record_compensation = Mock()
    manager._print_nav_summary = Mock()
    return manager


def _dataset(storage, nav_date, run_id="run-nav-test", account="a"):
    return CashFlowSummaryService(storage=storage).build_dataset(
        account=account,
        nav_date=nav_date,
        run_id=run_id,
        start_year=config.get_start_year(),
    )


class _DatasetStub:
    def details(self):
        return {"financial_fingerprint": "stub-dataset"}


def test_nav_cash_flow_gate_requires_local_fx_confirmation(tmp_path):
    facts = CompletedCashFlowFacts.build(
        flow_date=date(2026, 7, 1),
        account="a",
        broker="某券商",
        amount=10,
        currency="USD",
        exchange_rate="7.2",
        cny_amount="72.0",
        source="test",
        record_id="cf_usd",
    )
    storage = SimpleNamespace(
        get_raw_cash_flows=Mock(return_value=[
            RawCashFlowRecord.from_cash_flow(facts.to_cash_flow())
        ])
    )
    operation_store = OperationStateStore(tmp_path / "operations.sqlite3")
    service = CashFlowSummaryService(storage=storage)

    blocked = service.build_dataset(
        account="a",
        nav_date=date(2026, 7, 1),
        run_id="run-fx",
        start_year=2026,
        operation_state_store=operation_store,
    )
    assert blocked.complete is False
    assert {item.reason_code for item in blocked.blockers} == {
        "fx_confirmation_missing"
    }

    operation_store.record_fx_confirmation(
        confirmation_id="fx_1",
        record_id="cf_usd",
        source_hash=cash_flow_generated_fingerprint(facts),
        exchange_rate="7.20",
        exchange_rate_date="2026-07-01",
        exchange_rate_source="provider:example",
        exchange_rate_evidence_type="provider",
        cny_amount="72.00",
        confirmation={"operator": "tester"},
    )

    confirmed = service.build_dataset(
        account="a",
        nav_date=date(2026, 7, 1),
        run_id="run-fx-confirmed",
        start_year=2026,
        operation_state_store=operation_store,
    )
    assert confirmed.complete is True
    assert confirmed.fx_confirmation_fingerprint != blocked.fx_confirmation_fingerprint


def test_nav_record_service_records_nav_through_manager_helpers():
    storage = _storage()
    manager = _manager(storage)
    manager._find_latest_nav_before = Mock(return_value=None)
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 3, 19),
        persist=True,
        dry_run=True,
        run_id="run-nav-test",
        cash_flow_dataset=_dataset(storage, date(2026, 3, 19)),
    )

    assert result.date == date(2026, 3, 19)
    assert result.account == "a"
    assert result.total_value == 1200.0
    manager._find_latest_nav_before.assert_called_once()
    manager.snapshot_service.persist_holdings_snapshot.assert_called_once()
    storage.write_nav_record.assert_called_once_with(result, overwrite_existing=False, dry_run=True)
    storage.write_nav_records.assert_not_called()
    manager._print_nav_summary.assert_not_called()


def test_nav_record_service_persists_run_id_in_details():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 3, 19),
        persist=True,
        dry_run=True,
        run_id="run-nav-1",
        cash_flow_dataset=_dataset(storage, date(2026, 3, 19), "run-nav-1"),
    )

    assert result.details["run_id"] == "run-nav-1"
    assert result.details["cash_flow_dataset"]["run_id"] == "run-nav-1"
    assert result.details["cash_flow_dataset"]["window"] == {
        "start": f"{config.get_start_year()}-01-01",
        "end": "2026-03-19",
        "start_inclusive": True,
        "end_inclusive": True,
    }
    assert result.details["finality"] == {
        "version": 1,
        "status": "manual",
        "nav_date": "2026-03-19",
        "valuation_as_of": None,
        "writer": "nav-record",
        "write_reason": "direct_nav_record",
        "run_id": "run-nav-1",
    }
    storage.write_nav_record.assert_called_once_with(
        result,
        overwrite_existing=False,
        dry_run=True,
    )


def test_nav_repair_uses_explicit_history_snapshot_without_cache_mutation():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    previous = NAVHistory(
        record_id="nav-prev",
        date=date(2026, 3, 18),
        account="a",
        total_value=1200.0,
        cash_value=200.0,
        stock_value=1000.0,
        fund_value=100.0,
        cn_stock_value=1000.0,
        us_stock_value=0.0,
        hk_stock_value=0.0,
        stock_weight=0.833333,
        cash_weight=0.166667,
        shares=1000.0,
        nav=1.2,
        cash_flow=0.0,
        share_change=0.0,
    )
    run_id = "nav-repair:a:2026-03-19"

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 3, 19),
        persist=False,
        dry_run=True,
        run_id=run_id,
        cash_flow_dataset=_dataset(
            storage,
            date(2026, 3, 19),
            run_id,
        ),
        nav_history_snapshot=(previous,),
        nav_write_context=NavWriteContext(
            status="maintenance",
            writer="nav-repair",
            write_reason="nav_history_derived_repair",
            nav_date=date(2026, 3, 19),
            run_id=run_id,
        ),
    )

    assert result.nav == 1.2
    storage.preload_nav_index.assert_not_called()
    storage.get_nav_index.assert_not_called()


def test_explicit_history_snapshot_is_restricted_to_nav_repair():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    with pytest.raises(ValueError, match="restricted to.*nav-repair"):
        service.record_nav(
            account="a",
            valuation=_valuation(),
            nav_date=date(2026, 3, 19),
            persist=False,
            nav_history_snapshot=(),
        )


def test_nav_record_service_persists_explicit_daily_job_finality():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 3, 19),
        persist=True,
        dry_run=True,
        nav_write_context=NavWriteContext(
            status="final",
            writer="daily-nav-job",
            write_reason="canonical_daily_nav_job",
            nav_date=date(2026, 3, 19),
            valuation_as_of="2026-03-19T18:00:00",
            run_id="daily-1:a",
        ),
        cash_flow_dataset=_dataset(storage, date(2026, 3, 19), "daily-1:a"),
    )

    assert result.details["finality"]["status"] == "final"
    assert result.details["finality"]["writer"] == "daily-nav-job"
    assert result.details["finality"]["valuation_as_of"] == "2026-03-19T18:00:00"
    assert result.details["run_id"] == "daily-1:a"


def test_nav_record_service_persists_holdings_input_provenance():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    valuation = _valuation(holdings_provenance={
        "account": "a",
        "raw_record_digest": "raw-1",
        "normalized_holdings_digest": "normalized-1",
        "source_fetch_time": "2026-03-19T10:00:00+00:00",
    })

    result = service.record_nav(
        account="a",
        valuation=valuation,
        nav_date=date(2026, 3, 19),
        persist=True,
        dry_run=True,
        run_id="run-holdings",
        cash_flow_dataset=_dataset(storage, date(2026, 3, 19), "run-holdings"),
    )

    assert result.details["holdings_snapshot"] == valuation.holdings_provenance


def test_nav_record_service_requires_complete_matching_dataset():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    with pytest.raises(ValueError, match="requires CashFlowDatasetSnapshot"):
        service.record_nav(
            account="a",
            valuation=_valuation(),
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=True,
            run_id="run-required",
        )

    base = _dataset(storage, date(2026, 3, 19), "run-match")
    mismatches = [
        (replace(base, run_id="run-other"), "run_id"),
        (replace(base, account="other"), "account"),
        (
            replace(
                base,
                nav_date=date(2026, 3, 18),
                window_end=date(2026, 3, 18),
            ),
            "nav_date",
        ),
        (replace(base, effect_store_revision="tampered"), "effect_store_revision"),
    ]
    for dataset, field in mismatches:
        with pytest.raises(ValueError, match=field):
            service.record_nav(
                account="a",
                valuation=_valuation(),
                nav_date=date(2026, 3, 19),
                persist=True,
                dry_run=True,
                run_id="run-match",
                cash_flow_dataset=dataset,
            )

    storage.write_nav_record.assert_not_called()


def test_nav_record_service_rejects_valuation_account_mismatch():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    valuation = _valuation().model_copy(update={"account": "other"})

    with pytest.raises(ValueError, match="valuation account mismatch"):
        service.record_nav(
            account="a",
            valuation=valuation,
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=True,
            run_id="run-account-mismatch",
            cash_flow_dataset=_dataset(
                storage,
                date(2026, 3, 19),
                "run-account-mismatch",
            ),
        )

    storage.write_nav_record.assert_not_called()


def test_nav_record_service_rejects_context_date_mismatch():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    with pytest.raises(ValueError, match="does not match record date"):
        service.record_nav(
            account="a",
            valuation=_valuation(),
            nav_date=date(2026, 3, 19),
            persist=False,
            dry_run=True,
            nav_write_context=NavWriteContext(
                status="maintenance",
                writer="nav-repair",
                write_reason="nav_history_backfill",
                nav_date=date(2026, 3, 18),
            ),
        )


def test_nav_write_context_rejects_invalid_writer_status_combination():
    with pytest.raises(ValueError, match="invalid for writer close-nav"):
        NavWriteContext(
            status="final",
            writer="close-nav",
            write_reason="invalid",
            nav_date=date(2026, 3, 19),
        )


def test_nav_write_context_rejects_runtime_fact_conflicts():
    context = NavWriteContext(
        status="final",
        writer="daily-nav-job",
        write_reason="canonical_daily_nav_job",
        nav_date=date(2026, 3, 19),
        valuation_as_of="2026-03-19T18:00:00",
        run_id="run-a",
    )

    with pytest.raises(ValueError, match="run_id conflicts"):
        context.with_runtime(run_id="run-b")
    with pytest.raises(ValueError, match="valuation_as_of conflicts"):
        context.with_runtime(valuation_as_of="2026-03-19T18:01:00")


def test_nav_record_service_normalizes_datetime_nav_date():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=datetime(2026, 3, 19, 8, 30),
        persist=False,
        dry_run=True,
    )

    assert result.date == date(2026, 3, 19)
    assert result.details["finality"]["nav_date"] == "2026-03-19"


def test_nav_record_service_falls_back_to_current_period_start_for_nav_change():
    storage = _storage()
    base = NAVHistory(
        date=date(2026, 5, 1),
        account="a",
        total_value=1000.0,
        cash_value=100.0,
        stock_value=900.0,
        stock_weight=0.9,
        cash_weight=0.1,
        shares=1000.0,
        nav=1.0,
    )
    storage.get_nav_index.return_value = {"_nav_objects": [base]}
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 5, 28),
        persist=True,
        dry_run=True,
        run_id="run-period",
        cash_flow_dataset=_dataset(storage, date(2026, 5, 28), "run-period"),
    )

    assert result.mtd_nav_change == 0.2
    assert result.ytd_nav_change == 0.2
    assert result.mtd_pnl is None
    assert result.ytd_pnl is None


def test_nav_record_service_uses_bulk_persist_when_requested():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 3, 19),
        persist=True,
        dry_run=False,
        overwrite_existing=True,
        use_bulk_persist=True,
        run_id="run-bulk",
        cash_flow_dataset=_dataset(storage, date(2026, 3, 19), "run-bulk"),
    )

    storage.write_nav_records.assert_called_once_with([result], mode="replace", allow_partial=False, dry_run=False)
    storage.write_nav_record.assert_not_called()
    manager._print_nav_summary.assert_called_once()


def test_nav_record_service_rejects_real_write_on_unreliable_valuation():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)

    with pytest.raises(ValueError, match="NAV 写入拒绝"):
        service.record_nav(
            account="a",
            valuation=_valuation(warnings=[
                "美元现金(USD-CASH): 无法获取汇率",
                "[价格汇总] realtime=0, cache=0, stale_fallback=0, missing=1",
            ]),
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=False,
            run_id="run-unreliable",
            cash_flow_dataset=_dataset(storage, date(2026, 3, 19), "run-unreliable"),
        )

    storage.write_nav_record.assert_not_called()
    storage.write_nav_records.assert_not_called()
    manager.snapshot_service.persist_holdings_snapshot.assert_not_called()


def test_nav_record_service_rejects_mutated_compatibility_projection():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    valuation = _valuation()
    normalized = attached_normalized_valuation(valuation)
    assert normalized is not None
    valuation.total_value_cny = 1201.0

    with pytest.raises(ValueError, match="compatibility projection"):
        service.record_nav(
            account="a",
            valuation=valuation,
            normalized_valuation=normalized,
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=True,
            run_id="run-mutated",
            cash_flow_dataset=_dataset(
                storage,
                date(2026, 3, 19),
                "run-mutated",
            ),
        )

    storage.write_nav_record.assert_not_called()
    manager.snapshot_service.persist_holdings_snapshot.assert_not_called()


def test_nav_record_service_rejects_unattached_aggregate_projection():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    valuation = PortfolioValuation(
        account="a",
        total_value_cny=100,
        cash_value_cny=100,
        shares=100,
        nav=1,
    )

    with pytest.raises(ValueError, match="ValuationService-owned"):
        service.record_nav(
            account="a",
            valuation=valuation,
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=True,
            run_id="run-aggregate",
            cash_flow_dataset=_dataset(
                storage,
                date(2026, 3, 19),
                "run-aggregate",
            ),
        )

    storage.write_nav_record.assert_not_called()
    storage.write_nav_records.assert_not_called()
    manager.snapshot_service.persist_holdings_snapshot.assert_not_called()


def test_normal_nav_entrypoint_rejects_closed_input_authority():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    normalized = NormalizedValuationSnapshot.from_closed_input(
        ClosedNavTarget.build(
            total_value=100,
            cash_value=100,
            non_cash_value=0,
        ),
        account="a",
    )

    with pytest.raises(ValueError, match="source mismatch"):
        service.record_nav(
            account="a",
            valuation=normalized.to_portfolio_valuation(),
            normalized_valuation=normalized,
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=True,
            run_id="run-closed-wrong-entry",
            cash_flow_dataset=_dataset(
                storage,
                date(2026, 3, 19),
                "run-closed-wrong-entry",
            ),
        )

    storage.write_nav_record.assert_not_called()
    manager.snapshot_service.persist_holdings_snapshot.assert_not_called()


def test_nav_record_service_rejects_substituted_normalized_digest():
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    valuation = _valuation()
    attached = attached_normalized_valuation(valuation)
    assert attached is not None
    substituted = replace(attached, source="substituted_source")
    substituted.assert_compatible(valuation)
    assert substituted.digest != attached.digest

    with pytest.raises(ValueError, match="normalized valuation digest"):
        service.record_nav(
            account="a",
            valuation=valuation,
            normalized_valuation=substituted,
            nav_date=date(2026, 3, 19),
            persist=True,
            dry_run=True,
            run_id="run-substituted",
            cash_flow_dataset=_dataset(
                storage,
                date(2026, 3, 19),
                "run-substituted",
            ),
        )

    storage.write_nav_record.assert_not_called()
    storage.write_nav_records.assert_not_called()
    manager.snapshot_service.persist_holdings_snapshot.assert_not_called()


def test_nav_record_service_logs_snapshot_failure_after_nav_write(caplog):
    storage = _storage()
    manager = _manager(storage)
    manager.snapshot_service.persist_holdings_snapshot.side_effect = RuntimeError("snapshot boom")
    service = NavRecordService(manager=manager, storage=storage)

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=date(2026, 3, 19),
        persist=True,
        run_id="run-snapshot-fail",
        cash_flow_dataset=_dataset(storage, date(2026, 3, 19), "run-snapshot-fail"),
    )

    assert result.date == date(2026, 3, 19)
    storage.write_nav_record.assert_called_once()
    assert result.details["snapshot_persisted"] is False
    assert result.details["snapshot_status"] == "failed"
    assert result.details["snapshot_error"] == "snapshot boom"
    manager._record_compensation.assert_called_once()
    call = manager._record_compensation.call_args.kwargs
    assert call["operation_type"] == "NAV_HOLDINGS_SNAPSHOT_FAILED"
    assert call["task_id"] == result.details["snapshot_task_id"]
    assert call["payload"]["targets"][0]["type"] == "HOLDINGS_SNAPSHOT_TARGET_SET"
    storage.nav_history.patch_nav_details.assert_called_once_with("nav-1", result.details, dry_run=False)
    assert "holdings_snapshot write failed for 2026-03-19 (a): snapshot boom" in caplog.text


def test_nav_record_service_raises_partial_when_recovery_evidence_cannot_persist():
    storage = _storage()
    manager = _manager(storage)
    manager.snapshot_service.persist_holdings_snapshot.side_effect = RuntimeError("snapshot boom")
    manager._record_compensation.side_effect = OSError("disk full")
    service = NavRecordService(manager=manager, storage=storage)

    with pytest.raises(PartialWriteError) as captured:
        service.record_nav(
            account="a",
            valuation=_valuation(),
            nav_date=date(2026, 3, 19),
            persist=True,
            run_id="run-compensation-fail",
            cash_flow_dataset=_dataset(
                storage,
                date(2026, 3, 19),
                "run-compensation-fail",
            ),
        )

    assert captured.value.operation == "NAV_RECORD"
    assert captured.value.compensation_persisted is False
    assert storage.write_nav_record.called


def test_portfolio_skill_record_nav_surfaces_snapshot_partial_failure():
    nav_record = NAVHistory(
        date=date(2026, 3, 19),
        account="a",
        total_value=1200.0,
        nav=1.2,
        shares=1000.0,
        details={
            "snapshot_persisted": False,
            "snapshot_status": "failed",
            "snapshot_error": "snapshot boom",
        },
    )
    skill = PortfolioSkill.__new__(PortfolioSkill)
    skill.account = "a"
    skill.portfolio = Mock()
    skill.portfolio.record_nav.return_value = nav_record
    skill.portfolio.build_cash_flow_dataset.return_value = _DatasetStub()

    result = skill.record_nav(
        snapshot={"valuation": _valuation(), "snapshot_time": "2026-03-19T12:00:00"},
        dry_run=False,
        confirm=True,
    )

    assert result["success"] is False
    assert result["status"] == "partial"
    assert result["snapshot_persisted"] is False
    assert result["snapshot_error"] == "snapshot boom"
    assert result["nav"] == 1.2
    skill.portfolio.build_cash_flow_dataset.assert_called_once()
    assert (
        skill.portfolio.record_nav.call_args.kwargs["cash_flow_dataset"]
        is skill.portfolio.build_cash_flow_dataset.return_value
    )


def test_portfolio_skill_record_nav_passes_price_timeout_to_snapshot_builder():
    nav_record = NAVHistory(
        date=date(2026, 3, 19),
        account="a",
        total_value=1200.0,
        nav=1.2,
        shares=1000.0,
        details={},
    )
    calls = []
    skill = PortfolioSkill.__new__(PortfolioSkill)
    skill.account = "a"
    skill.portfolio = Mock()
    skill.portfolio.record_nav.return_value = nav_record
    skill.portfolio.build_cash_flow_dataset.return_value = _DatasetStub()
    skill.build_snapshot = Mock(
        side_effect=lambda price_timeout_seconds=None: calls.append(price_timeout_seconds)
        or {"valuation": _valuation(), "snapshot_time": "2026-03-19T12:00:00"}
    )

    result = skill.record_nav(price_timeout=17)

    assert result["success"] is True
    assert calls == [17]
    skill.portfolio.build_cash_flow_dataset.assert_called_once()


def test_portfolio_manager_record_nav_delegates_to_service():
    storage = _storage()
    manager = _manager(storage)
    manager.nav_record_service = Mock()
    expected = NAVHistory(date=date(2026, 3, 19), account="a", total_value=1.0)
    manager.nav_record_service.record_nav.return_value = expected

    result = manager.record_nav("a", valuation=_valuation(), nav_date=date(2026, 3, 19), persist=False, run_id="run-nav-1")

    assert result is expected
    manager.nav_record_service.record_nav.assert_called_once()
    assert manager.nav_record_service.record_nav.call_args.kwargs["account"] == "a"
    assert manager.nav_record_service.record_nav.call_args.kwargs["persist"] is False
    assert manager.nav_record_service.record_nav.call_args.kwargs["run_id"] == "run-nav-1"


def test_portfolio_skill_close_nav_persists_closed_finality():
    skill = PortfolioSkill.__new__(PortfolioSkill)
    skill.account = "a"
    skill.storage = _storage()
    skill.portfolio = _manager(skill.storage)
    skill.portfolio.nav_record_service.cash_flow_dataset_dependencies = lambda: {
        "cash_flow_effect_service": None,
        "operation_state_store": None,
    }

    result = skill.close_nav(
        date_str="2026-03-19",
        total_value=100.0,
        cash_value=100.0,
        stock_value=0.0,
        dry_run=False,
        confirm=True,
    )

    assert result["success"] is True
    written = skill.storage.write_nav_record.call_args_list[-1].args[0]
    assert written.details["status"] == "CLOSED"
    assert written.details["finality"]["status"] == "closed"
    assert written.details["finality"]["writer"] == "close-nav"
    evidence = written.details["snapshot_evidence"]
    assert evidence["version"] == "v2"
    assert evidence["status"] == "planned"
    assert evidence["row_count"] == 0
    assert [item["name"] for item in evidence["components"]] == [
        "manual_cash_value",
        "manual_non_cash_value",
    ]
    assert evidence["target_digest"]
    assert written.details["cash_flow_dataset"]["financial_fingerprint"]
    assert skill.storage.get_raw_cash_flows.call_count == 1
    skill.storage.reconcile_cash_flows.assert_not_called()
    assert "write_nav_record" not in inspect.getsource(PortfolioSkill.close_nav)


def test_nav_record_persists_daily_column_and_weekend_gap_basis():
    storage = _storage()
    friday = NAVHistory(
        record_id="nav-friday",
        date=date(2026, 3, 13),
        account="a",
        total_value=1000.0,
        cash_value=100.0,
        stock_value=900.0,
        stock_weight=0.9,
        cash_weight=0.1,
        shares=1000.0,
        nav=1.0,
        cash_flow=0.0,
        share_change=0.0,
        details={"evidence_version": "legacy"},
    )
    storage.get_nav_index.return_value = {"_nav_objects": [friday]}
    weekend_flow = CompletedCashFlowFacts.build(
        flow_date=date(2026, 3, 14),
        account="a",
        broker="bank",
        amount="100",
        currency="CNY",
        exchange_rate="1",
        cny_amount="100",
        source="test",
        record_id="cf-weekend",
    )
    storage.get_raw_cash_flows.return_value = [
        RawCashFlowRecord.from_cash_flow(weekend_flow.to_cash_flow())
    ]
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    monday = date(2026, 3, 16)
    dataset = _dataset(storage, monday, "run-weekend")

    result = service.record_nav(
        account="a",
        valuation=_valuation(),
        nav_date=monday,
        persist=True,
        dry_run=True,
        run_id="run-weekend",
        cash_flow_dataset=dataset,
    )

    assert result.cash_flow == 0.0
    assert result.share_change == 100.0
    assert result.details["cash_flow_basis"] == {
        "version": 1,
        "cash_flow_column_semantics": "daily",
        "daily_cash_flow": 0.0,
        "gap_cash_flow": 100.0,
        "previous_nav_date": "2026-03-13",
        "gap_window": {
            "start": "2026-03-13",
            "end": "2026-03-16",
            "start_inclusive": False,
            "end_inclusive": True,
        },
        "dataset_contract_version": dataset.contract_version,
        "dataset_financial_fingerprint": dataset.financial_fingerprint,
        "dataset_full_fingerprint": dataset.full_fingerprint,
    }


@pytest.mark.parametrize(
    "values,error",
    [
        ({"total_value": "NaN", "cash_value": 1, "stock_value": 0}, "finite"),
        ({"total_value": 100, "cash_value": 60, "stock_value": 30}, "decomposition"),
    ],
)
def test_closed_nav_blocks_invalid_target_before_repository(values, error):
    storage = _storage()
    manager = _manager(storage)
    service = NavRecordService(manager=manager, storage=storage)
    dataset = _dataset(storage, date(2026, 3, 19), "close-invalid")

    with pytest.raises(ValueError, match=error):
        service.record_closed_nav(
            account="a",
            nav_date=date(2026, 3, 19),
            cash_flow_dataset=dataset,
            run_id="close-invalid",
            **values,
        )

    storage.write_nav_record.assert_not_called()


def test_portfolio_skill_close_nav_does_not_default_missing_stock_to_zero():
    skill = PortfolioSkill.__new__(PortfolioSkill)
    skill.account = "a"
    skill.storage = _storage()
    skill.portfolio = _manager(skill.storage)

    result = skill.close_nav(
        date_str="2026-03-19",
        total_value=100.0,
        cash_value=100.0,
        dry_run=True,
    )

    assert result["success"] is False
    assert "stock_value" in result["error"]
    skill.storage.write_nav_record.assert_not_called()


def test_nav_history_backfill_recomputes_without_full_row_write(monkeypatch):
    storage = SimpleNamespace()
    context = SimpleNamespace(account="a", storage=storage, portfolio=SimpleNamespace())
    nav = NAVHistory(
        record_id="nav-1",
        date=date(2026, 3, 19),
        account="a",
        total_value=1200.0,
        cash_value=200.0,
        stock_value=1000.0,
        fund_value=0.0,
        cn_stock_value=1000.0,
        us_stock_value=0.0,
        hk_stock_value=0.0,
        stock_weight=0.833333,
        cash_weight=0.166667,
        shares=1000.0,
        nav=1.2,
        cash_flow=0.0,
        share_change=0.0,
        details={"evidence_version": "legacy"},
    )
    states = {
        field: (
            FieldState.null()
            if getattr(nav, field) is None
            else FieldState.valued(getattr(nav, field))
        )
        for field in (*BASE_FIELDS, *MAINTENANCE_FIELDS)
    }
    fresh = FreshNavRow(nav=nav, field_states=states)
    calls = []
    monkeypatch.setattr(backfill, "create_nav_repair_context", lambda account=None: context)
    monkeypatch.setattr(backfill, "read_fresh_nav_rows", lambda _context: [fresh])
    monkeypatch.setattr(
        backfill,
        "recompute_derived_row",
        lambda **kwargs: calls.append(kwargs) or (nav.model_copy(deep=True), SimpleNamespace()),
    )

    result = backfill.run(
        SimpleNamespace(
            account="a",
            apply=False,
            dry_run=True,
            input=None,
            d_from="2026-03-19",
            d_to="2026-03-19",
            limit=None,
            mode="replace",
            allow_partial=False,
        )
    )

    assert result["success"] is True
    assert result["write"]["full_row_writes"] == 0
    assert len(calls) == 1
    assert calls[0]["run_id"] == "nav-repair:a:2026-03-19"


def test_maintenance_recompute_consumes_snapshot_without_publishing_cache():
    storage = _storage()
    manager = _manager(storage)
    nav = NAVHistory(
        record_id="nav-1",
        date=date(2026, 3, 19),
        account="a",
        total_value=1200.0,
        cash_value=200.0,
        stock_value=1000.0,
        fund_value=100.0,
        cn_stock_value=1000.0,
        us_stock_value=0.0,
        hk_stock_value=0.0,
        stock_weight=0.833333,
        cash_weight=0.166667,
        shares=1200.0,
        nav=1.0,
        cash_flow=0.0,
        share_change=0.0,
        details={"evidence_version": "legacy"},
    )
    states = {
        field: (
            FieldState.null()
            if getattr(nav, field) is None
            else FieldState.valued(getattr(nav, field))
        )
        for field in (*BASE_FIELDS, *MAINTENANCE_FIELDS)
    }
    observed = FreshNavRow(nav=nav, field_states=states)
    context = NavRepairContext(
        account="a",
        storage=storage,
        portfolio=manager,
    )

    candidate, dataset = recompute_derived_row(
        context=context,
        observed=observed,
        working_navs=[nav],
        run_id="nav-repair:a:2026-03-19",
    )

    assert candidate.nav == 1.0
    assert candidate.details["finality"]["status"] == "maintenance"
    assert dataset.run_id == "nav-repair:a:2026-03-19"
    storage.preload_nav_index.assert_not_called()
    storage.get_nav_index.assert_not_called()
    storage.write_nav_record.assert_not_called()
