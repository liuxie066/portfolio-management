from __future__ import annotations

from fastapi.testclient import TestClient

from src.service.http import create_app


class FakePortfolioService:
    def __init__(self):
        self.calls = []

    def health(self):
        self.calls.append(("health", {}))
        return {"success": True, "status": "ok"}

    def list_accounts(self, **kwargs):
        self.calls.append(("list_accounts", kwargs))
        return {"success": True, "accounts": ["alice"]}

    def list_nav_accounts(self, **kwargs):
        self.calls.append(("list_nav_accounts", kwargs))
        return {"success": True, "accounts": ["alice"]}

    def multi_account_overview(self, **kwargs):
        self.calls.append(("overview", kwargs))
        return {"success": True, "accounts": kwargs["accounts"]}

    def get_holdings(self, **kwargs):
        self.calls.append(("holdings", kwargs))
        return {"success": True, "account": kwargs["account"]}

    def get_cash(self, **kwargs):
        self.calls.append(("cash", kwargs))
        return {"success": True, "account": kwargs["account"]}

    def get_nav(self, **kwargs):
        self.calls.append(("nav", kwargs))
        return {"success": True, "days": kwargs["days"]}

    def get_capital_facts(self, **kwargs):
        self.calls.append(("capital_facts", kwargs))
        return {"success": True, "status": "ok", **kwargs}

    def record_nav(self, **kwargs):
        self.calls.append(("record_nav", kwargs))
        return {"success": True, "dry_run": kwargs["dry_run"], "account": kwargs["account"]}

    def get_distribution(self, **kwargs):
        self.calls.append(("distribution", kwargs))
        return {"success": True, "account": kwargs["account"]}

    def full_report(self, **kwargs):
        self.calls.append(("full_report", kwargs))
        return {"success": True, "account": kwargs["account"]}

    def generate_report(self, **kwargs):
        self.calls.append(("generate_report", kwargs))
        return {"success": True, "report_type": kwargs["report_type"]}

    def daily_report_bundle(self, **kwargs):
        self.calls.append(("daily_report_bundle", kwargs))
        return {"success": True, "account": kwargs["account"], "dry_run": kwargs["dry_run"]}

    def audit_nav_history_duplicates(self, **kwargs):
        self.calls.append(("audit_nav_history_duplicates", kwargs))
        return {"success": True, "duplicate_group_count": 0}

    def daily_nav_job(self, **kwargs):
        self.calls.append(("daily_nav_job", kwargs))
        return {"success": True, "status": "completed", "dry_run": kwargs["dry_run"]}


def test_http_service_routes_delegate_to_portfolio_service():
    service = FakePortfolioService()
    client = TestClient(create_app(service=service))

    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/accounts", params={"include_default": False}).json()["accounts"] == ["alice"]
    assert client.get("/accounts/nav", params={"include_default": True}).json()["accounts"] == ["alice"]
    assert client.get("/accounts/overview", params={"accounts": "alice,bob", "price_timeout": 7}).json()["accounts"] == "alice,bob"
    assert client.get("/holdings", params={"account": "alice/bob", "include_cash": False, "group_by_market": True, "include_price": True}).json()["account"] == "alice/bob"
    assert client.get("/cash", params={"account": "alice/bob"}).json()["account"] == "alice/bob"
    assert client.get("/nav", params={"account": "alice/bob", "days": 14}).json()["days"] == 14
    assert client.get("/analysis/capital-facts", params={"account": "alice/bob", "period": "mtd", "as_of_month": "2026-06"}).json()["status"] == "ok"
    assert client.post("/nav/record", json={"account": "alice/bob", "price_timeout": 8, "dry_run": False, "confirm": True, "overwrite_existing": False, "run_id": "run-nav-1"}).json()["dry_run"] is False
    assert client.get("/nav/duplicates", params={"account": "alice/bob"}).json()["duplicate_group_count"] == 0
    assert client.get("/distribution", params={"account": "alice/bob"}).json()["account"] == "alice/bob"
    assert client.get("/report/full", params={"account": "alice/bob", "price_timeout": 9}).json()["account"] == "alice/bob"
    assert client.post("/report/daily-bundle", json={"account": "alice/bob", "price_timeout": 10, "dry_run": False, "confirm": True, "use_bulk_persist": True, "sync_futu_cash_mmf": True, "sync_futu_dry_run": False, "run_id": "run-report-1"}).json()["dry_run"] is False
    assert client.post("/daily-nav-job", json={"accounts": ["alice", "bob"], "nav_date": "2026-05-22", "price_timeout": 12, "dry_run": True, "overwrite_existing": False}).json()["status"] == "completed"
    assert client.get("/report/monthly", params={"account": "alice/bob", "price_timeout": 11}).json()["report_type"] == "monthly"

    assert service.calls == [
        ("health", {}),
        ("list_accounts", {"include_default": False}),
        ("list_nav_accounts", {"include_default": True}),
        ("overview", {"accounts": "alice,bob", "price_timeout": 7, "include_details": False}),
        ("holdings", {"account": "alice/bob", "include_cash": False, "group_by_market": True, "include_price": True}),
        ("cash", {"account": "alice/bob"}),
        ("nav", {"account": "alice/bob", "days": 14}),
        ("capital_facts", {"account": "alice/bob", "period": "mtd", "as_of_month": "2026-06"}),
        ("record_nav", {"account": "alice/bob", "price_timeout": 8, "dry_run": False, "confirm": True, "overwrite_existing": False, "use_bulk_persist": False, "run_id": "run-nav-1"}),
        ("audit_nav_history_duplicates", {"account": "alice/bob"}),
        ("distribution", {"account": "alice/bob", "accounts": None, "by_asset": False, "include_value": True}),
        ("full_report", {"account": "alice/bob", "price_timeout": 9}),
        ("daily_report_bundle", {"account": "alice/bob", "price_timeout": 10, "dry_run": False, "confirm": True, "overwrite_existing": True, "use_bulk_persist": True, "sync_futu_cash_mmf": True, "sync_futu_dry_run": False, "run_id": "run-report-1"}),
        ("daily_nav_job", {"accounts": ["alice", "bob"], "nav_date": "2026-05-22", "price_timeout": 12, "dry_run": True, "confirm": False, "overwrite_existing": False, "use_bulk_persist": False, "sync_futu_cash_mmf": False, "force_non_business_day": False}),
        ("generate_report", {"account": "alice/bob", "report_type": "monthly", "price_timeout": 11}),
    ]


def test_http_capital_facts_validates_period_and_month():
    client = TestClient(create_app(service=FakePortfolioService()))

    assert client.get(
        "/analysis/capital-facts",
        params={"account": "alice", "period": "weekly", "as_of_month": "2026-06"},
    ).status_code == 422
    assert client.get(
        "/analysis/capital-facts",
        params={"account": "alice", "period": "mtd", "as_of_month": "2026-6"},
    ).status_code == 422
    assert client.get(
        "/analysis/capital-facts",
        params={"account": "alice", "period": "mtd"},
    ).status_code == 422

def test_http_service_rejects_unknown_report_type():
    response = TestClient(create_app(service=FakePortfolioService())).get(
        "/report/weekly", params={"account": "alice"}
    )

    assert response.status_code == 400
    assert "unsupported report_type=weekly" in response.json()["detail"]


def test_http_futu_holdings_sync_routes_delegate_to_service():
    class FutuService(FakePortfolioService):
        def sync_futu_holdings(self, **kwargs):
            self.calls.append(("sync_futu_holdings", kwargs))
            return {"success": True, **kwargs}

    service = FutuService()
    client = TestClient(create_app(service=service))

    query = client.post("/futu/holdings/sync", json={
        "account": "lx",
        "dry_run": False,
        "confirm": True,
        "allow_empty_stock_snapshot": True,
    }).json()
    assert query["account"] == "lx"
    assert service.calls == [
        ("sync_futu_holdings", {
            "account": "lx",
            "dry_run": False,
            "confirm": True,
            "allow_empty_stock_snapshot": True,
        }),
    ]
