from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from src.models import AssetType
from src.service import PortfolioService


def test_portfolio_service_generate_report_uses_direct_app_service_not_backend():
    backend = SimpleNamespace(
        full_report=Mock(side_effect=AssertionError("backend should not be called")),
        generate_report=Mock(side_effect=AssertionError("backend should not be called")),
    )
    storage = SimpleNamespace(get_nav_history=Mock(return_value=[]))
    portfolio = SimpleNamespace(reporting_service=object())
    snapshot = _snapshot(total_value=150, cash_ratio=0.2, stock_ratio=0.7, fund_ratio=0.1)
    read_service = SimpleNamespace(
        build_snapshot=Mock(return_value=snapshot),
        get_distribution=Mock(return_value={"success": True, "by_type": [{"type": "stock", "value": 100.0, "ratio": 2 / 3}]}),
    )

    service = PortfolioService(
        backend=backend,
        storage=storage,
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    assert service.health()["status"] == "ok"
    result = service.generate_report(account="alice", report_type="monthly", price_timeout=11)

    assert result["success"] is True
    assert result["report_type"] == "月报"
    assert result["snapshot_time"] == "2026-05-23T12:00:00"
    assert result["monthly_return"]["success"] is False
    backend.full_report.assert_not_called()
    backend.generate_report.assert_not_called()
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=11)
    read_service.get_distribution.assert_called_once_with(holdings_data=snapshot["holdings_data"])


def test_portfolio_service_list_accounts_uses_direct_account_service_not_backend():
    class FakeClient:
        def list_records(self, table, **_kwargs):
            records = {
                "transactions": [{"fields": {"account": "bob"}}],
                "cash_flow": [{"fields": {"account": [{"text": "carol"}]}}],
                "nav_history": [{"fields": {"account": {"value": "dave"}}}],
            }
            return records.get(table, [])

    def get_holdings(*, account=None, include_empty=False):
        assert account is None
        assert include_empty is True
        return [
            SimpleNamespace(account="alice"),
            SimpleNamespace(account="bob"),
            SimpleNamespace(account=""),
        ]

    backend = SimpleNamespace(list_accounts=Mock(side_effect=AssertionError("backend should not be called")))
    storage = SimpleNamespace(
        client=FakeClient(),
        get_holdings=Mock(side_effect=get_holdings),
        _from_feishu_fields=lambda fields, _table: dict(fields),
    )
    service = PortfolioService(backend=backend, storage=storage, default_account="default")

    result = service.list_accounts()

    assert result["success"] is True
    assert result["default_account"] == "default"
    assert result["accounts"] == ["alice", "bob", "carol", "dave", "default"]
    assert result["sources"]["holdings"] == ["alice", "bob"]
    assert result["sources"]["cash_flow"] == ["carol"]
    backend.list_accounts.assert_not_called()


def test_portfolio_service_multi_account_overview_uses_direct_account_service_not_backend_overview():
    backend = SimpleNamespace(
        multi_account_overview=Mock(side_effect=AssertionError("backend should not be called")),
        full_report=Mock(side_effect=AssertionError("backend should not be called")),
    )
    snapshots = {
        "alice": _snapshot(total_value=100, cash_ratio=0.2, stock_ratio=0.7, fund_ratio=0.1),
        "bob": _snapshot(total_value=200, cash_ratio=0.5, stock_ratio=0.25, fund_ratio=0.25),
    }
    storage = SimpleNamespace(get_nav_history=Mock(return_value=[]))
    portfolio = SimpleNamespace(reporting_service=object())

    def read_service_factory(**kwargs):
        account = kwargs["account"]
        return SimpleNamespace(
            build_snapshot=Mock(return_value=snapshots[account]),
            get_distribution=Mock(return_value={"success": True, "by_type": []}),
        )

    service = PortfolioService(
        backend=backend,
        storage=storage,
        portfolio=portfolio,
        read_service_factory=read_service_factory,
        default_account="default",
    )

    result = service.multi_account_overview(
        accounts="alice,bob",
        price_timeout=5,
        include_details=True,
    )

    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["default_account"] == "default"
    assert result["accounts"] == ["alice", "bob"]
    assert result["successful_count"] == 2
    assert result["summary"]["total_value"] == 300
    assert result["summary"]["cash_value"] == 120
    assert result["summary"]["stock_value"] == 120
    assert result["summary"]["fund_value"] == 60
    assert "report" in result["items"][0]
    backend.multi_account_overview.assert_not_called()
    backend.full_report.assert_not_called()
    assert storage.get_nav_history.call_args_list[0].args == ("alice",)
    assert storage.get_nav_history.call_args_list[1].args == ("bob",)


def test_portfolio_service_get_nav_uses_direct_storage_path_not_backend():
    backend = SimpleNamespace(get_nav=Mock(side_effect=AssertionError("backend should not be called")))
    navs = [
        SimpleNamespace(
            date=date(2026, 5, 22),
            nav=1.1,
            shares=1000.0,
            total_value=1100.0,
            stock_value=800.0,
            cash_value=300.0,
            stock_weight=0.72,
            cash_weight=0.28,
            cash_flow=0.0,
            share_change=0.0,
            mtd_nav_change=0.02,
            ytd_nav_change=0.1,
            mtd_pnl=20.0,
            ytd_pnl=100.0,
            details=None,
        ),
        SimpleNamespace(
            date=date(2026, 5, 23),
            nav=1.2,
            shares=1000.0,
            total_value=1200.0,
            stock_value=900.0,
            cash_value=300.0,
            stock_weight=0.75,
            cash_weight=0.25,
            cash_flow=10.0,
            share_change=8.0,
            mtd_nav_change=0.03,
            ytd_nav_change=0.2,
            mtd_pnl=30.0,
            ytd_pnl=200.0,
            details={
                "nav_change_2026": 0.2,
                "appreciation_2026": 200.0,
                "cash_flow_2026": 10.0,
                "cumulative_nav_change": 0.2,
                "initial_value": 1000.0,
            },
        ),
    ]
    storage = SimpleNamespace(get_nav_history=Mock(return_value=navs))

    service = PortfolioService(backend=backend, storage=storage)

    result = service.get_nav(account="alice", days=7)

    assert result["success"] is True
    assert result["latest"]["date"] == "2026-05-23"
    assert result["latest"]["nav"] == 1.2
    assert result["latest"]["nav_change_2026"] == 0.2
    assert result["latest"]["cumulative_nav_change"] == 0.2
    assert result["latest"]["initial_value"] == 1000.0
    assert result["history"] == [
        {"date": "2026-05-22", "nav": 1.1, "share_change": 0.0},
        {"date": "2026-05-23", "nav": 1.2, "share_change": 8.0},
    ]
    backend.get_nav.assert_not_called()
    storage.get_nav_history.assert_called_once_with("alice", days=7)


def test_portfolio_service_get_holdings_uses_direct_read_service_not_backend():
    backend = SimpleNamespace(get_holdings=Mock(side_effect=AssertionError("backend should not be called")))
    portfolio = SimpleNamespace(reporting_service=object())
    read_service = SimpleNamespace(
        get_holdings=Mock(return_value={"success": True, "holdings": [{"code": "AAPL"}]})
    )

    service = PortfolioService(
        backend=backend,
        storage=object(),
        portfolio=portfolio,
        read_service_factory=lambda **kwargs: read_service,
    )

    result = service.get_holdings(
        account="alice",
        include_cash=False,
        group_by_market=True,
        include_price=True,
    )

    assert result == {"success": True, "holdings": [{"code": "AAPL"}]}
    backend.get_holdings.assert_not_called()
    read_service.get_holdings.assert_called_once_with(
        include_cash=False,
        group_by_market=True,
        include_price=True,
    )


def test_portfolio_service_get_cash_uses_direct_cash_service_not_backend():
    backend = SimpleNamespace(get_cash=Mock(side_effect=AssertionError("backend should not be called")))
    storage = SimpleNamespace(get_holdings=Mock(return_value=[
        SimpleNamespace(
            asset_id="CNY-CASH",
            asset_name="人民币现金",
            asset_type=AssetType.CASH,
            quantity=100.0,
            currency="CNY",
        )
    ]))

    service = PortfolioService(backend=backend, storage=storage)

    result = service.get_cash(account="alice")

    assert result == {
        "success": True,
        "by_currency": {"CNY": 100.0},
        "items": [{"code": "CNY-CASH", "name": "人民币现金", "amount": 100.0, "currency": "CNY", "type": "cash"}],
        "count": 1,
    }
    backend.get_cash.assert_not_called()
    storage.get_holdings.assert_called_once_with(account="alice")


def test_portfolio_service_record_nav_uses_direct_portfolio_path_not_backend():
    backend = SimpleNamespace(record_nav=Mock(side_effect=AssertionError("backend should not be called")))
    valuation = SimpleNamespace(warnings=["price warning"])
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-23T12:00:00"}
    nav_record = SimpleNamespace(nav=1.2345, total_value=1234.5, shares=1000.0, details={})
    portfolio = SimpleNamespace(
        reporting_service=object(),
        record_nav=Mock(return_value=nav_record),
    )
    read_service = SimpleNamespace(build_snapshot=Mock(return_value=snapshot))

    service = PortfolioService(
        backend=backend,
        storage=object(),
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.record_nav(
        account="alice",
        price_timeout=8,
        dry_run=False,
        confirm=True,
        overwrite_existing=False,
        use_bulk_persist=True,
        run_id="run-nav-1",
    )

    assert result["success"] is True
    assert result["run_id"] == "run-nav-1"
    assert snapshot["run_id"] == "run-nav-1"
    assert result["nav"] == 1.2345
    assert result["snapshot_time"] == "2026-05-23T12:00:00"
    assert result["warnings"] == ["price warning"]
    backend.record_nav.assert_not_called()
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=8)
    portfolio.record_nav.assert_called_once()
    assert portfolio.record_nav.call_args.args[0] == "alice"
    assert portfolio.record_nav.call_args.kwargs["valuation"] is valuation
    assert portfolio.record_nav.call_args.kwargs["persist"] is True
    assert portfolio.record_nav.call_args.kwargs["dry_run"] is False
    assert portfolio.record_nav.call_args.kwargs["overwrite_existing"] is False
    assert portfolio.record_nav.call_args.kwargs["use_bulk_persist"] is True
    assert portfolio.record_nav.call_args.kwargs["run_id"] == "run-nav-1"


def test_portfolio_service_daily_report_bundle_reuses_one_snapshot():
    backend = SimpleNamespace(daily_report_bundle=Mock(side_effect=AssertionError("backend should not be called")))
    valuation = SimpleNamespace(
        total_value_cny=150.0,
        cash_value_cny=30.0,
        stock_value_cny=105.0,
        fund_value_cny=15.0,
        cn_asset_value=0.0,
        us_asset_value=105.0,
        hk_asset_value=0.0,
        warnings=[],
    )
    snapshot = _snapshot(total_value=150, cash_ratio=0.2, stock_ratio=0.7, fund_ratio=0.1)
    snapshot["valuation"] = valuation
    nav_record = SimpleNamespace(nav=1.5, total_value=150.0, shares=100.0, details={})
    latest_nav = SimpleNamespace(
        date=date(2026, 5, 23),
        nav=1.5,
        shares=100.0,
        total_value=150.0,
        stock_value=120.0,
        cash_value=30.0,
        stock_weight=0.8,
        cash_weight=0.2,
        cash_flow=0.0,
        share_change=0.0,
        mtd_nav_change=None,
        ytd_nav_change=None,
        mtd_pnl=None,
        ytd_pnl=None,
        details=None,
    )
    storage = SimpleNamespace(get_nav_history=Mock(side_effect=[[], [latest_nav]]))
    portfolio = SimpleNamespace(
        reporting_service=object(),
        record_nav=Mock(return_value=nav_record),
    )
    read_service = SimpleNamespace(
        build_snapshot=Mock(return_value=snapshot),
        get_distribution=Mock(return_value={"success": True, "by_type": []}),
    )

    service = PortfolioService(
        backend=backend,
        storage=storage,
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.daily_report_bundle(
        account="alice",
        price_timeout=8,
        dry_run=False,
        confirm=True,
        use_bulk_persist=True,
        run_id="run-report-1",
    )

    assert result["success"] is True
    assert result["account"] == "alice"
    assert result["run_id"] == "run-report-1"
    assert result["snapshot"]["run_id"] == "run-report-1"
    assert result["nav_result"]["run_id"] == "run-report-1"
    assert result["report"]["run_id"] == "run-report-1"
    assert result["snapshot"] is snapshot
    assert result["nav_result"]["nav"] == 1.5
    assert result["report"]["report_type"] == "日报"
    assert result["nav_snapshot"]["latest"]["nav"] == 1.5
    backend.daily_report_bundle.assert_not_called()
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=8)
    portfolio.record_nav.assert_called_once()
    assert portfolio.record_nav.call_args.args[0] == "alice"
    assert portfolio.record_nav.call_args.kwargs["valuation"] is valuation
    assert portfolio.record_nav.call_args.kwargs["dry_run"] is False
    assert portfolio.record_nav.call_args.kwargs["use_bulk_persist"] is True
    assert portfolio.record_nav.call_args.kwargs["run_id"] == "run-report-1"


def test_portfolio_service_get_distribution_uses_direct_read_service_not_backend():
    backend = SimpleNamespace(get_distribution=Mock(side_effect=AssertionError("backend should not be called")))
    portfolio = SimpleNamespace(reporting_service=object())
    read_service = SimpleNamespace(get_distribution=Mock(return_value={"success": True, "total_value": 10}))

    service = PortfolioService(
        backend=backend,
        storage=object(),
        portfolio=portfolio,
        read_service_factory=lambda **kwargs: read_service,
    )

    result = service.get_distribution(account="alice")

    assert result == {"success": True, "total_value": 10}
    backend.get_distribution.assert_not_called()
    read_service.get_distribution.assert_called_once_with()


def test_portfolio_service_full_report_uses_direct_app_service_not_backend():
    backend = SimpleNamespace(full_report=Mock(side_effect=AssertionError("backend should not be called")))
    storage = SimpleNamespace(get_nav_history=Mock(return_value=[]))
    portfolio = SimpleNamespace(reporting_service=object())
    snapshot = _snapshot(total_value=150, cash_ratio=0.2, stock_ratio=0.7, fund_ratio=0.1)
    snapshot["holdings_data"]["holdings"] = [
        {
            "code": "AAPL",
            "name": "Apple",
            "quantity": 1,
            "type": "us_stock",
            "normalized_type": "stock",
            "broker": "futu",
            "currency": "USD",
            "market_value": 100.0,
        },
        {
            "code": "CNY-CASH",
            "name": "现金",
            "quantity": 50,
            "type": "cash",
            "normalized_type": "cash",
            "broker": "futu",
            "currency": "CNY",
            "market_value": 50.0,
        },
    ]
    read_service = SimpleNamespace(
        build_snapshot=Mock(return_value=snapshot),
        get_distribution=Mock(return_value={"success": True, "by_type": [{"type": "stock", "value": 100.0, "ratio": 2 / 3}]}),
    )

    service = PortfolioService(
        backend=backend,
        storage=storage,
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.full_report(account="alice", price_timeout=12)

    assert result["success"] is True
    assert result["overview"] == {"total_value": 150, "cash_ratio": 0.2, "stock_ratio": 0.7, "fund_ratio": 0.1}
    assert result["distribution"] == [{"type": "stock", "value": 100.0, "ratio": 2 / 3}]
    assert [row["code"] for row in result["top_holdings"]] == ["AAPL", "CASH+MMF"]
    backend.full_report.assert_not_called()
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=12)
    read_service.get_distribution.assert_called_once_with(holdings_data=snapshot["holdings_data"])
    storage.get_nav_history.assert_called_once_with("alice", days=9999)


def _snapshot(*, total_value: float, cash_ratio: float, stock_ratio: float, fund_ratio: float):
    cash_value = total_value * cash_ratio
    stock_value = total_value * stock_ratio
    fund_value = total_value * fund_ratio
    return {
        "snapshot_time": "2026-05-23T12:00:00",
        "valuation": SimpleNamespace(
            total_value_cny=total_value,
            cash_value_cny=cash_value,
            stock_value_cny=stock_value,
            fund_value_cny=fund_value,
            cn_asset_value=0.0,
            us_asset_value=stock_value,
            hk_asset_value=0.0,
        ),
        "holdings_data": {
            "success": True,
            "holdings": [],
            "count": 0,
            "total_value": total_value,
            "cash_value": cash_value,
            "stock_value": stock_value + fund_value,
            "cash_ratio": cash_ratio,
            "warnings": [],
        },
        "position_data": {
            "cash_ratio": cash_ratio,
            "stock_ratio": stock_ratio,
            "fund_ratio": fund_ratio,
        },
    }
