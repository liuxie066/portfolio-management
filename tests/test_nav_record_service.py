from datetime import date
from unittest.mock import Mock

import pytest

from skill_api import PortfolioSkill
from src.app.nav_record_service import NavRecordService
from src.models import NAVHistory, PortfolioValuation
from src.portfolio import PortfolioManager


def _valuation(warnings=None):
    return PortfolioValuation(
        account="a",
        total_value_cny=1200.0,
        cash_value_cny=200.0,
        stock_value_cny=900.0,
        fund_value_cny=100.0,
        cn_asset_value=1000.0,
        shares=1000.0,
        nav=1.2,
        holdings=[],
        warnings=list(warnings or []),
    )


def _storage():
    storage = Mock()
    storage.get_nav_index.return_value = {"_nav_objects": []}
    storage.get_cash_flow_aggs.return_value = {"daily": {}, "monthly": {}, "yearly": {}}
    return storage


def _manager(storage):
    manager = PortfolioManager(storage=storage, price_fetcher=Mock())
    manager.snapshot_service = Mock()
    manager._record_compensation = Mock()
    manager._print_nav_summary = Mock()
    return manager


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
    )

    assert result.date == date(2026, 3, 19)
    assert result.account == "a"
    assert result.total_value == 1200.0
    manager._find_latest_nav_before.assert_called_once()
    manager.snapshot_service.persist_holdings_snapshot.assert_called_once()
    storage.write_nav_record.assert_called_once_with(result, overwrite_existing=True, dry_run=True)
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
    )

    assert result.details["run_id"] == "run-nav-1"
    storage.write_nav_record.assert_called_once_with(result, overwrite_existing=True, dry_run=True)


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
    )

    assert result.date == date(2026, 3, 19)
    storage.write_nav_record.assert_called_once()
    assert result.details["snapshot_persisted"] is False
    assert result.details["snapshot_status"] == "failed"
    assert result.details["snapshot_error"] == "snapshot boom"
    manager._record_compensation.assert_called_once()
    assert manager._record_compensation.call_args.kwargs["operation_type"] == "NAV_HOLDINGS_SNAPSHOT_FAILED"
    assert "holdings_snapshot write failed for 2026-03-19 (a): snapshot boom" in caplog.text


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
    skill.build_snapshot = Mock(
        side_effect=lambda price_timeout_seconds=None: calls.append(price_timeout_seconds)
        or {"valuation": _valuation(), "snapshot_time": "2026-03-19T12:00:00"}
    )

    result = skill.record_nav(price_timeout=17)

    assert result["success"] is True
    assert calls == [17]


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
