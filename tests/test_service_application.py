from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from src.models import AssetType
from src.service import PortfolioService


def test_portfolio_service_generate_report_uses_direct_app_service():
    storage = SimpleNamespace(get_nav_history=Mock(return_value=[]))
    portfolio = SimpleNamespace(reporting_service=object())
    snapshot = _snapshot(total_value=150, cash_ratio=0.2, stock_ratio=0.7, fund_ratio=0.1)
    read_service = SimpleNamespace(
        build_snapshot=Mock(return_value=snapshot),
        get_distribution=Mock(return_value={"success": True, "by_type": [{"type": "stock", "value": 100.0, "ratio": 2 / 3}]}),
    )

    service = PortfolioService(
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
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=11)
    read_service.get_distribution.assert_called_once_with(holdings_data=snapshot["holdings_data"])


def test_portfolio_service_list_accounts_uses_direct_account_service():
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

    storage = SimpleNamespace(
        client=FakeClient(),
        get_holdings=Mock(side_effect=get_holdings),
        _from_feishu_fields=lambda fields, _table: dict(fields),
    )
    service = PortfolioService(storage=storage, default_account="default")

    result = service.list_accounts()

    assert result["success"] is True
    assert result["default_account"] == "default"
    assert result["accounts"] == ["alice", "bob", "carol", "dave", "default"]
    assert result["sources"]["holdings"] == ["alice", "bob"]
    assert result["sources"]["cash_flow"] == ["carol"]


def test_portfolio_service_multi_account_overview_uses_direct_account_service():
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
    assert storage.get_nav_history.call_args_list[0].args == ("alice",)
    assert storage.get_nav_history.call_args_list[1].args == ("bob",)


def test_portfolio_service_multi_account_overview_exposes_error_when_all_accounts_fail():
    service = PortfolioService(
        storage=SimpleNamespace(),
        portfolio=SimpleNamespace(reporting_service=object()),
        read_service_factory=lambda **_kwargs: SimpleNamespace(
            build_snapshot=Mock(side_effect=ValueError("missing holdings table"))
        ),
        default_account="default",
    )

    result = service.multi_account_overview(accounts="default")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"] == "default: missing holdings table"
    assert result["errors"] == [{"account": "default", "error": "missing holdings table"}]


def test_portfolio_service_get_nav_uses_direct_storage_path():
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

    service = PortfolioService(storage=storage)

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
    storage.get_nav_history.assert_called_once_with("alice", days=7)


def test_portfolio_service_get_holdings_uses_direct_read_service():
    portfolio = SimpleNamespace(reporting_service=object())
    read_service = SimpleNamespace(
        get_holdings=Mock(return_value={"success": True, "holdings": [{"code": "AAPL"}]})
    )

    service = PortfolioService(
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
    read_service.get_holdings.assert_called_once_with(
        include_cash=False,
        group_by_market=True,
        include_price=True,
    )


def test_portfolio_service_get_cash_uses_direct_cash_service():
    storage = SimpleNamespace(get_holdings=Mock(return_value=[
        SimpleNamespace(
            asset_id="CNY-CASH",
            asset_name="人民币现金",
            asset_type=AssetType.CASH,
            quantity=100.0,
            currency="CNY",
        )
    ]))

    service = PortfolioService(storage=storage)

    result = service.get_cash(account="alice")

    assert result == {
        "success": True,
        "by_currency": {"CNY": 100.0},
        "items": [{"code": "CNY-CASH", "name": "人民币现金", "amount": 100.0, "currency": "CNY", "type": "cash"}],
        "count": 1,
    }
    storage.get_holdings.assert_called_once_with(account="alice")


def test_portfolio_service_record_nav_uses_direct_portfolio_path():
    valuation = SimpleNamespace(warnings=["price warning"])
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-23T12:00:00"}
    nav_record = SimpleNamespace(nav=1.2345, total_value=1234.5, shares=1000.0, details={})
    portfolio = SimpleNamespace(
        reporting_service=object(),
        record_nav=Mock(return_value=nav_record),
    )
    read_service = SimpleNamespace(build_snapshot=Mock(return_value=snapshot))

    service = PortfolioService(
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
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=8)
    portfolio.record_nav.assert_called_once()
    assert portfolio.record_nav.call_args.args[0] == "alice"
    assert portfolio.record_nav.call_args.kwargs["valuation"] is valuation
    assert portfolio.record_nav.call_args.kwargs["persist"] is True
    assert portfolio.record_nav.call_args.kwargs["dry_run"] is False
    assert portfolio.record_nav.call_args.kwargs["overwrite_existing"] is False
    assert portfolio.record_nav.call_args.kwargs["use_bulk_persist"] is True
    assert portfolio.record_nav.call_args.kwargs["run_id"] == "run-nav-1"


def test_portfolio_service_init_nav_history_uses_direct_app_service():
    valuation = SimpleNamespace(total_value_cny=1000.0, warnings=[])
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-22T12:00:00"}
    nav_record = SimpleNamespace(
        nav=1.0,
        shares=1000.0,
        total_value=1000.0,
        cash_value=200.0,
        stock_value=800.0,
        fund_value=0.0,
        details={},
    )
    storage = SimpleNamespace(get_nav_history=Mock(return_value=[]))
    portfolio = SimpleNamespace(
        reporting_service=object(),
        record_nav=Mock(return_value=nav_record),
    )
    read_service = SimpleNamespace(build_snapshot=Mock(return_value=snapshot))

    service = PortfolioService(
        storage=storage,
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.init_nav_history(
        account="alice",
        date_str="2026-05-22",
        price_timeout=8,
        dry_run=False,
        confirm=True,
        use_bulk_persist=True,
    )

    assert result["success"] is True
    assert result["account"] == "alice"
    assert result["date"] == "2026-05-22"
    storage.get_nav_history.assert_called_once_with("alice", days=9999)
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=8)
    portfolio.record_nav.assert_called_once()
    assert portfolio.record_nav.call_args.args[0] == "alice"
    assert portfolio.record_nav.call_args.kwargs["valuation"] is valuation
    assert portfolio.record_nav.call_args.kwargs["dry_run"] is False
    assert portfolio.record_nav.call_args.kwargs["overwrite_existing"] is False
    assert portfolio.record_nav.call_args.kwargs["use_bulk_persist"] is True


def test_portfolio_service_daily_report_bundle_reuses_one_snapshot():
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
    nav_record = SimpleNamespace(
        date=date(2026, 5, 23),
        nav=1.5,
        total_value=150.0,
        shares=100.0,
        stock_value=120.0,
        cash_value=30.0,
        stock_weight=0.8,
        cash_weight=0.2,
        cash_flow=0.0,
        share_change=0.0,
        pnl=3.0,
        mtd_nav_change=0.05,
        ytd_nav_change=0.12,
        mtd_pnl=5.0,
        ytd_pnl=12.0,
        details={"cagr_pct": 8.8},
    )
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
        get_distribution=Mock(return_value={"success": True, "total_value": 150.0, "by_type": []}),
    )

    service = PortfolioService(
        storage=storage,
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.daily_report_bundle(
        account="alice",
        price_timeout=8,
        dry_run=False,
        confirm=True,
        overwrite_existing=False,
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
    assert result["distribution"]["total_value"] == 150.0
    assert result["report"]["report_type"] == "日报"
    assert result["report"]["date"] == "2026-05-23"
    assert result["report"]["nav"] == 1.5
    assert result["report"]["total_value"] == 150.0
    assert result["report"]["pnl"] == 3.0
    assert result["report"]["mtd_nav_change"] == 0.05
    assert result["report"]["cagr_pct"] == 8.8
    assert result["nav_snapshot"]["latest"]["nav"] == 1.5
    read_service.build_snapshot.assert_called_once_with(price_timeout_seconds=8)
    portfolio.record_nav.assert_called_once()
    assert portfolio.record_nav.call_args.args[0] == "alice"
    assert portfolio.record_nav.call_args.kwargs["valuation"] is valuation
    assert portfolio.record_nav.call_args.kwargs["dry_run"] is False
    assert portfolio.record_nav.call_args.kwargs["overwrite_existing"] is False
    assert portfolio.record_nav.call_args.kwargs["use_bulk_persist"] is True
    assert portfolio.record_nav.call_args.kwargs["run_id"] == "run-report-1"
    read_service.get_distribution.assert_any_call(holdings_data=snapshot)


def test_portfolio_service_daily_report_bundle_returns_failure_payload_on_snapshot_error():
    read_service = SimpleNamespace(
        build_snapshot=Mock(side_effect=ValueError("missing holdings table"))
    )

    service = PortfolioService(
        storage=SimpleNamespace(),
        portfolio=SimpleNamespace(record_nav=Mock(), reporting_service=object()),
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.daily_report_bundle(
        account="alice",
        dry_run=True,
        confirm=False,
        run_id="run-report-failure",
    )

    assert result["success"] is False
    assert result["error"] == "missing holdings table"
    assert result["account"] == "alice"
    assert result["run_id"] == "run-report-failure"
    assert result["dry_run"] is True
    assert result["confirm"] is False


def test_portfolio_service_get_distribution_uses_direct_read_service():
    portfolio = SimpleNamespace(reporting_service=object())
    read_service = SimpleNamespace(get_distribution=Mock(return_value={"success": True, "total_value": 10}))

    service = PortfolioService(
        storage=object(),
        portfolio=portfolio,
        read_service_factory=lambda **kwargs: read_service,
    )

    result = service.get_distribution(account="alice")

    assert result == {"success": True, "total_value": 10}
    read_service.get_distribution.assert_called_once_with()


def test_portfolio_service_get_distribution_merges_accounts_by_asset():
    portfolio = SimpleNamespace(reporting_service=object())
    read_services = []

    def read_service_factory(**kwargs):
        account = kwargs["account"]
        service = SimpleNamespace(
            build_snapshot=Mock(return_value={
                "holdings_data": {
                    "success": True,
                    "holdings": [
                        {"code": "AAPL", "name": "Apple", "normalized_type": "stock", "account": account, "broker": "futu", "currency": "USD", "quantity": 5, "market_value": 100.0},
                    ],
                    "total_value": 100.0,
                },
            }),
            get_asset_distribution=Mock(return_value={"success": True, "by_asset": []}),
        )
        read_services.append(service)
        return service

    service = PortfolioService(
        storage=object(),
        portfolio=portfolio,
        read_service_factory=read_service_factory,
    )

    result = service.get_distribution(accounts="alice,bob", by_asset=True, include_value=False)

    assert result["success"] is True
    assert result["accounts"] == ["alice", "bob"]
    # The service resolves the first account again for the final builder call.
    final_read_service = read_services[-1]
    final_read_service.get_asset_distribution.assert_called_once()
    passed_holdings_data = final_read_service.get_asset_distribution.call_args[0][0]
    assert passed_holdings_data["total_value"] == 200.0
    assert len(passed_holdings_data["holdings"]) == 2
    assert final_read_service.get_asset_distribution.call_args[1]["include_value"] is False


def test_portfolio_service_group_cash_implies_asset_distribution():
    read_service = SimpleNamespace(
        build_snapshot=Mock(return_value={
            "holdings_data": {
                "success": True,
                "holdings": [],
                "total_value": 0.0,
            },
        }),
        get_distribution=Mock(return_value={"success": True}),
        get_asset_distribution=Mock(return_value={"success": True, "by_asset": []}),
    )
    service = PortfolioService(
        storage=object(),
        portfolio=SimpleNamespace(reporting_service=object()),
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.get_distribution(account="alice", group_cash=True)

    assert result["success"] is True
    assert result["accounts"] == ["alice"]
    read_service.get_distribution.assert_not_called()
    read_service.get_asset_distribution.assert_called_once()
    assert read_service.get_asset_distribution.call_args[1] == {
        "include_value": True,
        "group_cash": True,
    }


def test_portfolio_service_full_report_uses_direct_app_service():
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
        storage=storage,
        portfolio=portfolio,
        read_service_factory=lambda **_kwargs: read_service,
    )

    result = service.full_report(account="alice", price_timeout=12)

    assert result["success"] is True
    assert result["overview"] == {"total_value": 150, "cash_ratio": 0.2, "stock_ratio": 0.7, "fund_ratio": 0.1}
    assert result["distribution"] == [{"type": "stock", "value": 100.0, "ratio": 2 / 3}]
    assert [row["code"] for row in result["top_holdings"]] == ["AAPL", "CASH+MMF"]
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


def test_portfolio_service_sync_futu_holdings_uses_resolved_account(monkeypatch):
    calls = []

    class FakeSyncService:
        def __init__(self, storage):
            calls.append(("init", storage))

        def sync_portfolio(self, **kwargs):
            calls.append(("sync", kwargs))
            return {"success": True, **kwargs}

    class FakeReceiptService:
        def send(self, result):
            calls.append(("receipt", result))
            return {"success": True, "status": "sent"}

    import src.app as app_module

    storage = object()
    monkeypatch.setattr(app_module, "FutuBalanceSyncService", FakeSyncService)
    result = PortfolioService(
        storage=storage,
        default_account="lx",
        futu_receipt_service=FakeReceiptService(),
    ).sync_futu_holdings(
        dry_run=False,
        confirm=True,
        allow_empty_stock_snapshot=True,
    )

    assert result["success"] is True
    assert result["receipt"] == {"success": True, "status": "sent"}
    assert calls[0] == ("init", storage)
    assert calls[1] == ("sync", {
        "account": "lx",
        "dry_run": False,
        "confirm": True,
        "allow_empty_stock_snapshot": True,
    })
    assert calls[2][0] == "receipt"
    assert calls[2][1]["account"] == "lx"


def test_portfolio_service_receipt_failure_does_not_change_sync_success(monkeypatch):
    class FakeSyncService:
        def __init__(self, storage):
            self.storage = storage

        def sync_portfolio(self, **kwargs):
            return {"success": True, **kwargs}

    class FailedReceiptService:
        def send(self, result):
            return {"success": False, "status": "failed", "error": "send failed"}

    import src.app as app_module

    monkeypatch.setattr(app_module, "FutuBalanceSyncService", FakeSyncService)
    result = PortfolioService(
        storage=object(),
        default_account="lx",
        futu_receipt_service=FailedReceiptService(),
    ).sync_futu_holdings(dry_run=False, confirm=True)

    assert result["success"] is True
    assert result["receipt"]["success"] is False


def test_portfolio_service_daily_nav_job_sends_one_receipt(monkeypatch):
    calls = []

    class FakeDailyNavJobService:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def run(self, **kwargs):
            calls.append(("run", kwargs))
            return {
                "success": True,
                "status": "completed",
                "date": "2026-07-17",
                "items": [],
            }

    class FakeReceiptService:
        def send(self, result):
            calls.append(("receipt", dict(result)))
            return {"success": True, "status": "sent"}

    import src.app as app_module

    monkeypatch.setattr(app_module, "DailyNavJobService", FakeDailyNavJobService)
    result = PortfolioService(
        storage=object(),
        portfolio=SimpleNamespace(reporting_service=object()),
        default_account="lx",
        nav_receipt_service=FakeReceiptService(),
    ).daily_nav_job(
        accounts="lx,hb,sy",
        dry_run=False,
        confirm=True,
        run_id="run-nav-receipt",
    )

    assert result["success"] is True
    assert result["receipt"] == {"success": True, "status": "sent"}
    assert calls[1][0] == "run"
    assert calls[1][1]["accounts"] == "lx,hb,sy"
    assert calls[2][0] == "receipt"
    assert calls[2][1]["dry_run"] is False
    assert calls[2][1]["confirm"] is True


def test_portfolio_service_nav_receipt_failure_does_not_change_job_success(monkeypatch):
    class FakeDailyNavJobService:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return {"success": True, "status": "completed", "items": []}

    class FailedReceiptService:
        def send(self, _result):
            return {"success": False, "status": "failed", "error": "send failed"}

    import src.app as app_module

    monkeypatch.setattr(app_module, "DailyNavJobService", FakeDailyNavJobService)
    result = PortfolioService(
        storage=object(),
        portfolio=SimpleNamespace(reporting_service=object()),
        default_account="lx",
        nav_receipt_service=FailedReceiptService(),
    ).daily_nav_job(dry_run=False, confirm=True)

    assert result["success"] is True
    assert result["receipt"]["success"] is False
