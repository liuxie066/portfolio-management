from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.app.capital_facts_service import CapitalFactsService


def _nav(day: str, total: float, *, mtd_pnl=None, ytd_pnl=None):
    return SimpleNamespace(
        date=date.fromisoformat(day),
        total_value=total,
        mtd_pnl=mtd_pnl,
        ytd_pnl=ytd_pnl,
    )


def _service(navs, *, cash_flow=0.0, today=date(2026, 7, 17)):
    storage = SimpleNamespace(get_nav_history=Mock(return_value=navs))
    cash_flows = SimpleNamespace(period=Mock(return_value=cash_flow))
    return CapitalFactsService(
        storage=storage,
        cash_flow_summary_service=cash_flows,
        today_fn=lambda: today,
    ), storage, cash_flows


def test_mtd_capital_facts_use_strict_previous_month_anchor_and_partial_month_cutoff():
    service, storage, cash_flows = _service(
        [
            _nav("2026-05-29", 900.0),
            _nav("2026-06-30", 1000.0),
            _nav("2026-07-16", 1050.0, mtd_pnl=40.0),
            _nav("2026-07-18", 9999.0, mtd_pnl=9999.0),
        ],
        cash_flow=10.0,
    )

    result = service.get(account="lx", period="mtd", as_of_month="2026-07")

    assert result["status"] == "ok"
    assert result["period"] == {
        "kind": "mtd",
        "requested_as_of_month": "2026-07",
        "calendar_start": "2026-07-01",
        "anchor_date": "2026-06-30",
        "end_date": "2026-07-16",
        "basis": "latest_persisted_nav_in_requested_month",
        "timezone": "Asia/Shanghai",
    }
    assert result["amounts"] == {
        "currency": "CNY",
        "opening_assets": 1000.0,
        "external_cash_flow": 10.0,
        "period_pnl": 40.0,
        "ending_assets": 1050.0,
    }
    assert result["reconciliation"]["status"] == "ok"
    storage.get_nav_history.assert_called_once_with("lx", days=10000)
    cash_flows.period.assert_called_once_with("lx", date(2026, 7, 1), date(2026, 7, 16))


def test_ytd_capital_facts_use_previous_year_anchor_and_requested_month_end():
    service, _, cash_flows = _service(
        [
            _nav("2025-12-31", 1000.0),
            _nav("2026-05-29", 1080.0),
            _nav("2026-06-30", 1120.0, ytd_pnl=100.0),
            _nav("2026-07-16", 1200.0, ytd_pnl=170.0),
        ],
        cash_flow=20.0,
    )

    result = service.get(account="lx", period="ytd", as_of_month="2026-06")

    assert result["period"]["anchor_date"] == "2025-12-31"
    assert result["period"]["end_date"] == "2026-06-30"
    assert result["amounts"]["period_pnl"] == 100.0
    cash_flows.period.assert_called_once_with("lx", date(2026, 1, 1), date(2026, 6, 30))


def test_january_mtd_and_ytd_use_previous_december_and_previous_year():
    navs = [
        _nav("2025-11-28", 900.0),
        _nav("2025-12-31", 1000.0),
        _nav("2026-01-30", 1100.0, mtd_pnl=100.0, ytd_pnl=100.0),
    ]
    service, _, _ = _service(navs, today=date(2026, 2, 1))

    mtd = service.get(account="lx", period="mtd", as_of_month="2026-01")
    ytd = service.get(account="lx", period="ytd", as_of_month="2026-01")

    assert mtd["period"]["anchor_date"] == "2025-12-31"
    assert ytd["period"]["anchor_date"] == "2025-12-31"


@pytest.mark.parametrize(
    ("navs", "period", "reason"),
    [
        ([_nav("2026-05-30", 1000.0)], "mtd", "target_month_nav_missing"),
        ([_nav("2026-06-30", 1000.0)], "mtd", "previous_month_anchor_missing"),
        ([_nav("2026-06-30", 1000.0)], "ytd", "previous_year_anchor_missing"),
    ],
)
def test_capital_facts_report_strict_missing_data_reasons(navs, period, reason):
    service, _, cash_flows = _service(navs)

    result = service.get(account="lx", period=period, as_of_month="2026-06")

    assert result["success"] is True
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    cash_flows.period.assert_not_called()


def test_capital_facts_reject_future_month_without_querying_storage():
    service, storage, _ = _service([])

    result = service.get(account="lx", period="mtd", as_of_month="2026-08")

    assert result["reason"] == "requested_month_in_future"
    storage.get_nav_history.assert_not_called()


def test_capital_facts_preserve_missing_stored_pnl_as_not_observed():
    service, _, _ = _service(
        [_nav("2026-05-29", 1000.0), _nav("2026-06-30", 1020.0)],
        cash_flow=10.0,
    )

    result = service.get(account="lx", period="mtd", as_of_month="2026-06")

    assert result["amounts"]["period_pnl"] == 10.0
    assert result["reconciliation"] == {
        "stored_period_pnl": None,
        "calculated_period_pnl": 10.0,
        "difference": None,
        "tolerance": 0.05,
        "status": "not_observed",
    }


def test_capital_facts_flag_stored_pnl_mismatch():
    service, _, _ = _service(
        [_nav("2026-05-29", 1000.0), _nav("2026-06-30", 1020.0, mtd_pnl=12.0)],
        cash_flow=10.0,
    )

    result = service.get(account="lx", period="mtd", as_of_month="2026-06")

    assert result["reconciliation"]["difference"] == 2.0
    assert result["reconciliation"]["status"] == "mismatch"


@pytest.mark.parametrize(
    ("period", "month", "message"),
    [
        ("weekly", "2026-06", "period must be mtd or ytd"),
        ("mtd", "2026-6", "YYYY-MM"),
        ("mtd", "2026-13", "YYYY-MM"),
    ],
)
def test_capital_facts_validate_input(period, month, message):
    service, _, _ = _service([])

    with pytest.raises(ValueError, match=message):
        service.get(account="lx", period=period, as_of_month=month)
