from __future__ import annotations

from fastapi.testclient import TestClient

from src.service.http import app as module_app, create_app


def _client(app, host="127.0.0.1"):
    return TestClient(app, client=(host, 50000))


class FakePortfolioService:
    def __init__(self):
        self.calls = []

    def health(self):
        self.calls.append(("health", {}))
        return {"success": True, "status": "ok"}

    def quality_status(self):
        self.calls.append(("quality_status", {}))
        dataset_ids = (
            "pm.account_mapping",
            "pm.holdings_quantity",
            "pm.cost_basis",
            "pm.securities_cash",
            "pm.fund_mmf",
            "pm.prices",
            "pm.fx",
            "pm.nav",
            "pm.nav_history",
        )
        return {
            "schema_version": "investment.quality_status.v1",
            "producer": {
                "service": "portfolio-management",
                "producer_version": "0.1.0",
                "policy_version": "quality-policy-v1",
                "instance_id": "pm-test",
            },
            "observed_at_utc": "2026-07-26T01:00:00Z",
            "runtime": {
                "status": "healthy",
                "as_of_utc": "2026-07-26T01:00:00Z",
                "checks": [],
            },
            "datasets": [
                {
                    "dataset_id": dataset_id,
                    "scope": {"account": "alice"},
                    "status": "trusted",
                    "as_of_utc": "2026-07-26T01:00:00Z",
                    "freshness": {
                        "status": "fresh",
                        "observed_at_utc": "2026-07-26T01:00:00Z",
                    },
                    "reason_codes": [],
                }
                for dataset_id in dataset_ids
            ],
            "incidents": [],
        }

    def list_accounts(self, **kwargs):
        self.calls.append(("list_accounts", kwargs))
        return {"success": True, "accounts": ["alice"], "count": 1}

    def list_nav_accounts(self, **kwargs):
        self.calls.append(("list_nav_accounts", kwargs))
        return {"success": True, "accounts": ["alice"]}

    def multi_account_overview(self, **kwargs):
        self.calls.append(("overview", kwargs))
        accounts = [item for item in (kwargs["accounts"] or "").split(",") if item]
        return {
            "success": True,
            "status": "ok",
            "accounts": accounts,
            "account_count": len(accounts),
            "successful_count": len(accounts),
            "failed_count": 0,
            "summary": {},
            "items": [],
        }

    def get_holdings(self, **kwargs):
        self.calls.append(("holdings", kwargs))
        return {"success": True, "account": kwargs["account"], "holdings": [], "count": 0}

    def get_cash(self, **kwargs):
        self.calls.append(("cash", kwargs))
        return {
            "success": True,
            "account": kwargs["account"],
            "by_currency": {},
            "items": [],
            "count": 0,
        }

    def get_nav(self, **kwargs):
        self.calls.append(("nav", kwargs))
        return {"success": True, "days": kwargs["days"], "latest": {}, "history": []}

    def get_capital_facts(self, **kwargs):
        self.calls.append(("capital_facts", kwargs))
        return {
            "schema_version": "portfolio.capital_facts.v1",
            "success": True,
            "status": "ok",
            "account": kwargs["account"],
            "period": {
                "kind": kwargs["period"],
                "requested_as_of_month": kwargs["as_of_month"],
            },
            "amounts": {"currency": "CNY"},
        }

    def get_valuation_evidence(self, **kwargs):
        self.calls.append(("valuation_evidence", kwargs))
        return {
            "schema_version": "portfolio.valuation_evidence.v1",
            "success": True,
            "status": "complete",
            "scope": kwargs,
            "snapshot": {"observed_at": "2026-07-26T00:00:00Z"},
            "holdings": [],
            "quotes": [],
            "account_status": [],
            "warnings": [],
        }

    def record_nav(self, **kwargs):
        self.calls.append(("record_nav", kwargs))
        return {"success": True, "dry_run": kwargs["dry_run"], "account": kwargs["account"]}

    def get_distribution(self, **kwargs):
        self.calls.append(("distribution", kwargs))
        return {"success": True, "account": kwargs["account"], "total_value": 0, "by_type": []}

    def full_report(self, **kwargs):
        self.calls.append(("full_report", kwargs))
        return {
            "success": True,
            "account": kwargs["account"],
            "generated_at": "2026-07-26T01:00:00",
            "overview": {},
            "nav": None,
            "returns": {},
            "top_holdings": [],
            "distribution": {},
        }

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


def test_module_app_rejects_remote_clients_by_default():
    response = _client(module_app, "203.0.113.5").get(
        "/health",
        headers={"host": "127.0.0.1", "x-forwarded-for": "127.0.0.1"},
    )

    assert response.status_code == 403
    assert "loopback clients only" in response.json()["detail"]


def test_http_service_allows_only_actual_loopback_clients():
    service = FakePortfolioService()
    app = create_app(service=service)

    assert _client(app, "127.0.0.1").get("/health").status_code == 200
    assert _client(app, "::1").get("/health").status_code == 200

    response = _client(app, "203.0.113.5").get(
        "/health",
        headers={"host": "localhost", "x-forwarded-for": "127.0.0.1"},
    )
    assert response.status_code == 403
    assert service.calls == [("health", {}), ("health", {})]


def test_http_service_explicit_remote_override_and_env(monkeypatch):
    service = FakePortfolioService()
    assert _client(create_app(service=service, allow_remote=True), "203.0.113.5").get("/health").status_code == 200

    monkeypatch.setenv("PORTFOLIO_SERVICE_ALLOW_REMOTE", "true")
    assert _client(create_app(service=service), "203.0.113.5").get("/health").status_code == 200


def test_http_service_routes_delegate_to_portfolio_service():
    service = FakePortfolioService()
    client = _client(create_app(service=service))

    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/accounts", params={"include_default": False}).json()["accounts"] == ["alice"]
    assert client.get("/accounts/nav", params={"include_default": True}).json()["accounts"] == ["alice"]
    assert client.get("/accounts/overview", params={"accounts": "alice,bob", "price_timeout": 7}).json()["accounts"] == ["alice", "bob"]
    assert client.get("/holdings", params={"account": "alice/bob", "include_cash": False, "group_by_market": True, "include_price": True}).json()["account"] == "alice/bob"
    assert client.get("/cash", params={"account": "alice/bob"}).json()["account"] == "alice/bob"
    assert client.get("/nav", params={"account": "alice/bob", "days": 14}).json()["days"] == 14
    assert client.get("/analysis/capital-facts", params={"account": "alice/bob", "period": "mtd", "as_of_month": "2026-06"}).json()["status"] == "ok"
    evidence = client.post(
        "/analysis/valuation-evidence",
        json={"accounts": ["alice"], "supplemental_codes": ["NVDA"], "price_timeout": 13},
    ).json()
    assert evidence["status"] == "complete"
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
        (
            "valuation_evidence",
            {"accounts": ["alice"], "supplemental_codes": ["NVDA"], "price_timeout": 13},
        ),
        ("record_nav", {"account": "alice/bob", "price_timeout": 8, "dry_run": False, "confirm": True, "overwrite_existing": False, "use_bulk_persist": False, "run_id": "run-nav-1"}),
        ("audit_nav_history_duplicates", {"account": "alice/bob"}),
        ("distribution", {"account": "alice/bob", "accounts": None, "by_asset": False, "include_value": True}),
        ("full_report", {"account": "alice/bob", "price_timeout": 9}),
        ("daily_report_bundle", {"account": "alice/bob", "price_timeout": 10, "dry_run": False, "confirm": True, "overwrite_existing": False, "use_bulk_persist": True, "sync_futu_cash_mmf": True, "sync_futu_dry_run": False, "run_id": "run-report-1"}),
        ("daily_nav_job", {"accounts": ["alice", "bob"], "nav_date": "2026-05-22", "price_timeout": 12, "dry_run": True, "confirm": False, "overwrite_existing": False, "use_bulk_persist": False, "sync_futu_cash_mmf": False, "force_non_business_day": False}),
        ("generate_report", {"account": "alice/bob", "report_type": "monthly", "price_timeout": 11}),
    ]


def test_quality_status_is_independently_authenticated_no_store_and_etag(monkeypatch):
    monkeypatch.setenv("PM_QUALITY_READ_TOKEN", "pm-read-secret")
    service = FakePortfolioService()
    client = _client(create_app(service=service))

    unauthorized = client.get("/quality/status")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "QUALITY_AUTH_FAILED"
    assert unauthorized.headers["cache-control"] == "no-store"
    assert service.calls == []

    response = client.get(
        "/quality/status",
        headers={"Authorization": "Bearer pm-read-secret"},
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == "investment.quality_status.v1"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-quality-schema-version"] == "investment.quality_status.v1"
    assert response.headers["etag"].startswith('"sha256:')

    unchanged = client.get(
        "/quality/status",
        headers={
            "Authorization": "Bearer pm-read-secret",
            "If-None-Match": response.headers["etag"],
        },
    )
    assert unchanged.status_code == 304
    assert service.calls == [("quality_status", {}), ("quality_status", {})]


def test_quality_status_missing_artifact_is_safe_503(monkeypatch):
    monkeypatch.setenv("PM_QUALITY_READ_TOKEN", "pm-read-secret")
    service = FakePortfolioService()
    service.quality_status = lambda: None

    response = _client(create_app(service=service)).get(
        "/quality/status",
        headers={"Authorization": "Bearer pm-read-secret"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUALITY_STATUS_UNAVAILABLE"
    assert "path" not in response.text.lower()


def test_http_capital_facts_validates_period_and_month():
    client = _client(create_app(service=FakePortfolioService()))

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
    response = _client(create_app(service=FakePortfolioService())).get(
        "/report/weekly", params={"account": "alice"}
    )

    assert response.status_code == 400
    assert "unsupported report_type=weekly" in response.json()["detail"]


def test_http_futu_holdings_sync_routes_delegate_to_service():
    class FutuService(FakePortfolioService):
        def sync_futu_holdings(self, **kwargs):
            self.calls.append(("sync_futu_holdings", kwargs))
            return {
                "success": True,
                "status": "written",
                "broker": "富途",
                "source": "futu-openapi",
                "source_snapshot_id": "snapshot-1",
                "sync_run_id": "sync-1",
                "stages": {},
                "positions": [],
                **kwargs,
            }

    service = FutuService()
    client = _client(create_app(service=service))

    query = client.post("/futu/holdings/sync", json={
        "account": "lx",
        "dry_run": False,
        "confirm": True,
        "allow_empty_stock_snapshot": True,
    }).json()
    assert query["account"] == "lx"
    versioned = client.post("/api/v1/futu/holdings/sync", json={
        "account": "lx",
        "dry_run": False,
        "confirm": True,
        "allow_empty_stock_snapshot": True,
    })
    assert versioned.json()["account"] == "lx"
    assert versioned.headers["x-pm-api-version"] == "portfolio.api.v1"
    assert service.calls == [
        ("sync_futu_holdings", {
            "account": "lx",
            "dry_run": False,
            "confirm": True,
            "allow_empty_stock_snapshot": True,
        }),
        ("sync_futu_holdings", {
            "account": "lx",
            "dry_run": False,
            "confirm": True,
            "allow_empty_stock_snapshot": True,
        }),
    ]


def test_om_facing_v1_routes_match_legacy_and_expose_version_headers():
    service = FakePortfolioService()
    client = _client(create_app(service=service))
    cases = [
        ("get", "/accounts", {"include_default": False}, None),
        ("get", "/accounts/overview", {"accounts": "alice", "price_timeout": 7}, None),
        ("get", "/holdings", {"account": "alice"}, None),
        ("get", "/cash", {"account": "alice"}, None),
        ("get", "/nav", {"account": "alice"}, None),
        (
            "get",
            "/analysis/capital-facts",
            {"account": "alice", "period": "mtd", "as_of_month": "2026-07"},
            None,
        ),
        ("get", "/distribution", {"account": "alice"}, None),
        ("get", "/report/full", {"account": "alice"}, None),
        (
            "post",
            "/analysis/valuation-evidence",
            None,
            {"accounts": ["alice"], "supplemental_codes": [], "price_timeout": 5},
        ),
    ]

    for method, legacy_path, params, payload in cases:
        legacy = client.request(method, legacy_path, params=params, json=payload)
        versioned = client.request(method, f"/api/v1{legacy_path}", params=params, json=payload)
        assert versioned.status_code == legacy.status_code == 200
        versioned_payload = versioned.json()
        assert versioned_payload.pop("freshness")["status"] == "fresh"
        assert versioned_payload.pop("retrieved_at_utc").endswith("Z")
        legacy_payload = {key: value for key, value in legacy.json().items() if value is not None}
        assert versioned_payload == legacy_payload
        assert versioned.headers["x-pm-api-version"] == "portfolio.api.v1"
        assert legacy.headers["deprecation"] == "true"
        assert legacy.headers["link"] == f'</api/v1{legacy_path}>; rel="successor-version"'


def test_v1_errors_are_stable_and_legacy_payload_is_unchanged():
    service = FakePortfolioService()
    service.get_holdings = lambda **_: {"success": False, "error": "broker unavailable"}
    client = _client(create_app(service=service))

    legacy = client.get("/holdings", params={"account": "alice"})
    assert legacy.status_code == 200
    assert legacy.json() == {"success": False, "error": "broker unavailable"}

    versioned = client.get("/api/v1/holdings", params={"account": "alice"})
    assert versioned.status_code == 503
    assert versioned.json()["error_code"] == "PM_SERVICE_UNAVAILABLE"
    assert versioned.json()["message"] == "broker unavailable"
    assert versioned.json()["request_id"].startswith("req-")
    assert versioned.headers["x-pm-api-version"] == "portfolio.api.v1"

    invalid = client.get("/api/v1/holdings")
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "INPUT_VALIDATION_ERROR"


def test_v1_freshness_is_unavailable_when_owner_evidence_is_missing():
    service = FakePortfolioService()
    service.quality_status = lambda: {
        "schema_version": "investment.quality_status.v1",
        "datasets": [],
    }

    payload = _client(create_app(service=service)).get(
        "/api/v1/cash",
        params={"account": "alice"},
    ).json()

    assert payload["freshness"] == {
        "status": "unavailable",
        "trust_status": "unavailable",
        "dataset_ids": ["pm.securities_cash", "pm.fund_mmf"],
        "reason_codes": ["DATASET_EVIDENCE_MISSING"],
    }


def test_v1_freshness_propagates_stale_partial_owner_evidence():
    service = FakePortfolioService()
    artifact = service.quality_status()
    for dataset in artifact["datasets"]:
        if dataset["dataset_id"] == "pm.fund_mmf":
            dataset["status"] = "partial"
            dataset["freshness"]["status"] = "stale"
            dataset["reason_codes"] = ["SOURCE_STALE"]
    service.quality_status = lambda: artifact

    payload = _client(create_app(service=service)).get(
        "/api/v1/cash",
        params={"account": "alice"},
    ).json()

    assert payload["freshness"]["status"] == "stale"
    assert payload["freshness"]["trust_status"] == "partial"
    assert payload["freshness"]["reason_codes"] == ["SOURCE_STALE"]


def test_openapi_contract_contains_only_real_om_v1_capabilities():
    schema = create_app(service=FakePortfolioService()).openapi()
    required = {
        "/api/v1/accounts",
        "/api/v1/accounts/overview",
        "/api/v1/holdings",
        "/api/v1/cash",
        "/api/v1/nav",
        "/api/v1/analysis/capital-facts",
        "/api/v1/analysis/valuation-evidence",
        "/api/v1/distribution",
        "/api/v1/report/full",
        "/api/v1/futu/holdings/sync",
    }
    assert required <= set(schema["paths"])
    assert "/api/v1/analysis/cash-facts" not in schema["paths"]
    for path in required:
        operation = next(iter(schema["paths"][path].values()))
        header = operation["responses"]["200"]["headers"]["X-PM-API-Version"]
        assert header["schema"]["const"] == "portfolio.api.v1"
    accounts_schema = schema["components"]["schemas"]["AccountsResponse"]
    assert {"success", "accounts", "count", "freshness", "retrieved_at_utc"} <= set(accounts_schema["required"])
    holdings_schema = schema["components"]["schemas"]["HoldingsResponse"]
    assert {"success", "count", "freshness", "retrieved_at_utc"} <= set(holdings_schema["required"])
