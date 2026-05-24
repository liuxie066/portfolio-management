from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.app.full_report_service import FullReportService
from src.domain.nav_calculator import NavCalculator
from src.models import NAVHistory


def _snapshot(total_value=1000.0, cash_value=100.0, stock_value=900.0):
    return {
        "snapshot_time": "2026-03-20T15:00:00",
        "valuation": SimpleNamespace(
            total_value_cny=total_value,
            cash_value_cny=cash_value,
            stock_value_cny=stock_value,
            fund_value_cny=0.0,
            cn_asset_value=stock_value,
            us_asset_value=0.0,
            hk_asset_value=0.0,
        ),
        "holdings_data": {
            "success": True,
            "holdings": [],
            "total_value": total_value,
            "cash_value": cash_value,
            "stock_value": stock_value,
        },
        "position_data": {
            "cash_ratio": cash_value / total_value,
            "stock_ratio": stock_value / total_value,
            "fund_ratio": 0.0,
        },
    }


def _read_service():
    return SimpleNamespace(get_distribution=lambda holdings_data: {"success": True, "by_type": []})


def test_full_report_prefers_recorded_today_nav_over_synthetic():
    today = date(2026, 3, 20)
    previous = NAVHistory(
        date=date(2026, 3, 19),
        account="a",
        total_value=900.0,
        cash_value=100.0,
        stock_value=800.0,
        shares=90.0,
        nav=10.0,
    )
    recorded_today = NAVHistory(
        date=today,
        account="a",
        total_value=1000.0,
        cash_value=100.0,
        stock_value=900.0,
        shares=100.0,
        nav=10.0,
        cash_flow=100.0,
        share_change=10.0,
        stock_weight=0.9,
        cash_weight=0.1,
        details={"source": "recorded"},
    )
    forbidden = Mock(side_effect=AssertionError("synthetic calculation should not run"))
    portfolio = SimpleNamespace(
        _find_latest_nav_before=forbidden,
        _find_year_end_nav=forbidden,
        _find_prev_month_end_nav=forbidden,
        _summarize_cash_flows=forbidden,
        _calc_nav_metrics=forbidden,
        _build_nav_record=forbidden,
    )
    service = FullReportService(account="a", storage=SimpleNamespace(), portfolio=portfolio, read_service=_read_service())

    with (
        patch("src.app.full_report_service.bj_today", return_value=today),
        patch("src.app.full_report_service.bj_now_naive", return_value=datetime(2026, 3, 20, 15, 0, 0)),
    ):
        report = service.full_report(snapshot=_snapshot(), navs=[previous, recorded_today])

    assert report["success"] is True
    assert report["nav"]["date"] == "2026-03-20"
    assert report["nav"]["shares"] == 100.0
    assert report["nav"]["nav"] == 10.0
    assert report["nav"]["details"] == {"source": "recorded"}


def test_full_report_synthetic_nav_reuses_core_nav_calculation():
    today = date(2026, 3, 20)
    previous = NAVHistory(
        date=date(2026, 3, 19),
        account="a",
        total_value=900.0,
        cash_value=100.0,
        stock_value=800.0,
        shares=90.0,
        nav=10.0,
    )

    def calc_metrics(**kwargs):
        kwargs.pop("account", None)
        kwargs.pop("all_navs", None)
        return NavCalculator.calc_nav_metrics(initial_value=900.0, **kwargs)

    calc_mock = Mock(side_effect=calc_metrics)
    build_mock = Mock(side_effect=lambda **kwargs: NavCalculator.build_nav_record(**kwargs))
    portfolio = SimpleNamespace(
        _find_latest_nav_before=lambda navs, before_date: previous,
        _find_year_end_nav=lambda navs, year: previous,
        _find_prev_month_end_nav=lambda navs, year, month: previous,
        _summarize_cash_flows=Mock(return_value={
            "daily": 100.0,
            "monthly": 100.0,
            "yearly": {"2026": 100.0},
            "cumulative": 100.0,
            "gap": 100.0,
        }),
        _calc_nav_metrics=calc_mock,
        _build_nav_record=build_mock,
    )
    service = FullReportService(account="a", storage=SimpleNamespace(), portfolio=portfolio, read_service=_read_service())

    with (
        patch("src.app.full_report_service.bj_today", return_value=today),
        patch("src.app.full_report_service.bj_now_naive", return_value=datetime(2026, 3, 20, 15, 0, 0)),
        patch("src.app.full_report_service.config.get_start_year", return_value=2026),
    ):
        report = service.full_report(snapshot=_snapshot(total_value=1100.0, cash_value=200.0, stock_value=900.0), navs=[previous])

    assert report["success"] is True
    assert report["nav"]["shares"] == 100.0
    assert report["nav"]["nav"] == 11.0
    assert report["nav"]["share_change"] == 10.0
    assert report["nav"]["pnl"] == 100.0
    assert report["nav"]["details"]["is_synthetic"] is True
    calc_mock.assert_called_once()
    build_mock.assert_called_once()
