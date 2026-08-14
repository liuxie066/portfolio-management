"""FastAPI HTTP service for portfolio-management."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .application import PortfolioService
from .bind import allow_remote_from_env, is_loopback_client
from src import config


REPORT_TYPES = {"daily", "monthly", "yearly"}
PM_API_VERSION = "portfolio.api.v1"
LOGGER = logging.getLogger(__name__)
LEGACY_SUCCESSORS = {
    "/accounts": "/api/v1/accounts",
    "/accounts/overview": "/api/v1/accounts/overview",
    "/holdings": "/api/v1/holdings",
    "/cash": "/api/v1/cash",
    "/nav": "/api/v1/nav",
    "/analysis/capital-facts": "/api/v1/analysis/capital-facts",
    "/analysis/valuation-evidence": "/api/v1/analysis/valuation-evidence",
    "/distribution": "/api/v1/distribution",
    "/report/full": "/api/v1/report/full",
    "/futu/holdings/sync": "/api/v1/futu/holdings/sync",
}


class NavRecordRequest(BaseModel):
    account: Optional[str] = None
    nav_date: Optional[str] = None
    price_timeout: int = 30
    dry_run: bool = True
    confirm: bool = False
    overwrite_existing: bool = False
    use_bulk_persist: bool = False
    run_id: Optional[str] = None


class DailyReportBundleRequest(BaseModel):
    account: Optional[str] = None
    nav_date: Optional[str] = None
    price_timeout: int = 30
    dry_run: bool = True
    confirm: bool = False
    overwrite_existing: bool = False
    use_bulk_persist: bool = False
    sync_futu_cash_mmf: bool = False
    sync_futu_dry_run: Optional[bool] = None
    run_id: Optional[str] = None


class FutuHoldingsSyncRequest(BaseModel):
    account: Optional[str] = None
    dry_run: bool = True
    confirm: bool = False
    allow_empty_stock_snapshot: bool = False


class DailyNavJobRequest(BaseModel):
    account: Optional[str] = None
    accounts: Optional[Any] = None
    nav_date: Optional[str] = None
    run_date: Optional[str] = None
    price_timeout: int = 30
    dry_run: bool = True
    confirm: bool = False
    overwrite_existing: bool = False
    use_bulk_persist: bool = False
    sync_futu_cash_mmf: bool = False
    sync_futu_dry_run: Optional[bool] = None
    force_non_business_day: bool = False
    run_id: Optional[str] = None
    valuation_ref: Optional[str] = None


class ValuationEvidenceRequest(BaseModel):
    accounts: list[str] = Field(min_length=1, max_length=20)
    supplemental_codes: list[str] = Field(default_factory=list, max_length=500)
    price_timeout: int = Field(default=30, ge=1, le=300)


class FreshnessEvidence(BaseModel):
    status: Literal["fresh", "stale", "unknown", "unavailable"]
    trust_status: Literal["trusted", "partial", "untrusted", "unavailable"]
    observed_at_utc: Optional[str] = None
    dataset_ids: list[str]
    reason_codes: list[str] = Field(default_factory=list)


class PublicResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: Literal[True]
    freshness: FreshnessEvidence
    retrieved_at_utc: str


class AccountsResponse(PublicResponse):
    accounts: list[str]
    count: int


class AccountsOverviewResponse(PublicResponse):
    status: str
    accounts: list[str]
    account_count: int
    successful_count: int
    failed_count: int
    summary: dict[str, Any]
    items: list[dict[str, Any]]


class HoldingsResponse(PublicResponse):
    count: int
    holdings: Optional[list[dict[str, Any]]] = None
    by_market: Optional[dict[str, list[dict[str, Any]]]] = None

    @model_validator(mode="after")
    def validate_holding_shape(self):
        if self.holdings is None and self.by_market is None:
            raise ValueError("holdings or by_market is required")
        return self


class CashResponse(PublicResponse):
    by_currency: dict[str, Any]
    items: list[dict[str, Any]]
    count: int


class NavResponse(PublicResponse):
    latest: dict[str, Any]
    history: list[dict[str, Any]]


class DistributionResponse(PublicResponse):
    total_value: Optional[float] = None
    total_quantity: Optional[float] = None
    accounts: Optional[list[str]] = None
    by_type: Optional[list[dict[str, Any]]] = None
    by_asset: Optional[list[dict[str, Any]]] = None

    @model_validator(mode="after")
    def validate_distribution_shape(self):
        if self.by_type is None and self.by_asset is None:
            raise ValueError("by_type or by_asset is required")
        return self


class FullReportResponse(PublicResponse):
    generated_at: str
    overview: dict[str, Any]
    nav: Optional[dict[str, Any]] = None
    returns: dict[str, Any]
    top_holdings: list[dict[str, Any]]
    distribution: dict[str, Any]


class FutuHoldingsSyncResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    success: Literal[True]
    account: str
    broker: str
    dry_run: bool
    source: str
    source_snapshot_id: str
    sync_run_id: str
    stages: dict[str, Any]
    positions: list[dict[str, Any]]


class CapitalFactsResponse(PublicResponse):
    schema_version: str
    status: str
    account: str
    period: dict[str, Any]
    amounts: Optional[dict[str, Any]] = None


class ValuationEvidenceResponse(PublicResponse):
    schema_version: str
    status: str
    scope: dict[str, Any]
    snapshot: dict[str, Any]
    holdings: list[dict[str, Any]]
    quotes: list[dict[str, Any]]
    account_status: list[dict[str, Any]]
    warnings: list[str]


class PublicErrorResponse(BaseModel):
    success: Literal[False] = False
    error_code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


_VERSION_HEADER = {
    "X-PM-API-Version": {
        "description": "Stable PM business API version.",
        "schema": {"type": "string", "const": PM_API_VERSION},
    }
}
V1_RESPONSES = {
    200: {"headers": _VERSION_HEADER},
    400: {"model": PublicErrorResponse},
    422: {"model": PublicErrorResponse},
    503: {"model": PublicErrorResponse},
}


def _service(request: Request) -> PortfolioService:
    return request.app.state.portfolio_service


def _payload_dict(payload: BaseModel) -> dict:
    return payload.model_dump(exclude_none=True)


def _quality_headers(request_id: str, *, etag: str | None = None) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store",
        "X-Request-Id": request_id,
        "X-Quality-Schema-Version": "investment.quality_status.v1",
    }
    if etag:
        headers["ETag"] = etag
    return headers


def _quality_error(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
        headers=_quality_headers(request_id),
    )


def _is_v1(request: Request) -> bool:
    return request.url.path.startswith("/api/v1/")


def _public_result(
    request: Request,
    result: Any,
    *,
    input_error_status: int | None = None,
    dataset_ids: tuple[str, ...] = (),
    accounts: tuple[str, ...] = (),
) -> Any:
    if not _is_v1(request) or not isinstance(result, dict):
        return result
    if result.get("success") is not False:
        enriched = dict(result)
        if dataset_ids:
            enriched["freshness"] = _view_freshness(
                request,
                dataset_ids=dataset_ids,
                accounts=accounts,
            )
            enriched["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return enriched
    code = str(result.get("error_code") or "PM_SERVICE_UNAVAILABLE").strip().upper()
    status_code = input_error_status if code == "INPUT_ERROR" and input_error_status else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error_code": code,
            "message": str(result.get("error") or result.get("message") or "portfolio-management request failed"),
            "request_id": f"req-{uuid4().hex}",
            "details": {},
        },
    )


def _view_freshness(
    request: Request,
    *,
    dataset_ids: tuple[str, ...],
    accounts: tuple[str, ...],
) -> dict[str, Any]:
    requested = set(dataset_ids)
    account_filter = {item.strip().lower() for item in accounts if item.strip()}
    try:
        quality = _service(request).quality_status()
    except Exception:
        quality = None
    datasets = quality.get("datasets") if isinstance(quality, dict) else None
    if not isinstance(datasets, list):
        return _unavailable_freshness(dataset_ids, "QUALITY_ARTIFACT_UNAVAILABLE")

    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for item in datasets:
        if not isinstance(item, dict) or item.get("dataset_id") not in requested:
            continue
        account = str((item.get("scope") or {}).get("account") or "").strip().lower()
        if account_filter and account not in account_filter:
            continue
        matched[(str(item["dataset_id"]), account)] = item

    expected = {
        (dataset_id, account)
        for dataset_id in dataset_ids
        for account in (account_filter or {""})
    }
    if account_filter:
        missing = expected - set(matched)
    else:
        found_ids = {dataset_id for dataset_id, _ in matched}
        missing = {(dataset_id, "") for dataset_id in requested - found_ids}
    if missing:
        return _unavailable_freshness(dataset_ids, "DATASET_EVIDENCE_MISSING")

    items = list(matched.values())
    trust_values = {str(item.get("status") or "unavailable") for item in items}
    freshness_values = {
        str((item.get("freshness") or {}).get("status") or "unknown")
        for item in items
    }
    if "unavailable" in trust_values:
        trust_status = "unavailable"
    elif "untrusted" in trust_values:
        trust_status = "untrusted"
    elif "partial" in trust_values:
        trust_status = "partial"
    else:
        trust_status = "trusted"

    if trust_status == "unavailable":
        freshness_status = "unavailable"
    elif "stale" in freshness_values:
        freshness_status = "stale"
    elif freshness_values == {"fresh"}:
        freshness_status = "fresh"
    else:
        freshness_status = "unknown"

    observed = [
        str((item.get("freshness") or {}).get("observed_at_utc") or item.get("as_of_utc") or "")
        for item in items
    ]
    observed = sorted(item for item in observed if item)
    reasons = sorted({
        str(reason)
        for item in items
        for reason in (item.get("reason_codes") or [])
        if reason
    })
    return {
        "status": freshness_status,
        "trust_status": trust_status,
        "observed_at_utc": observed[0] if observed else None,
        "dataset_ids": list(dataset_ids),
        "reason_codes": reasons,
    }


def _unavailable_freshness(dataset_ids: tuple[str, ...], reason_code: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "trust_status": "unavailable",
        "observed_at_utc": None,
        "dataset_ids": list(dataset_ids),
        "reason_codes": [reason_code],
    }


def create_app(service: Optional[PortfolioService] = None, allow_remote: Optional[bool] = None) -> FastAPI:
    app = FastAPI(
        title="Portfolio Management Service",
        version="0.1.1",
        description="Service-first API for portfolio accounts, holdings, NAV, and reports.",
    )
    app.state.portfolio_service = service or PortfolioService()
    app.state.allow_remote = allow_remote_from_env() if allow_remote is None else bool(allow_remote)

    @app.middleware("http")
    async def enforce_loopback_client(request: Request, call_next):
        client_host = request.client.host if request.client is not None else ""
        if not app.state.allow_remote and not is_loopback_client(client_host):
            return JSONResponse(
                status_code=403,
                content={"detail": "portfolio service accepts loopback clients only"},
            )
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/v1/"):
            response.headers["X-PM-API-Version"] = PM_API_VERSION
        elif path in LEGACY_SUCCESSORS:
            successor = LEGACY_SUCCESSORS[path]
            response.headers["Deprecation"] = "true"
            response.headers["Link"] = f'<{successor}>; rel="successor-version"'
            LOGGER.info(
                "pm_api_legacy_request method=%s path=%s successor=%s",
                request.method,
                path,
                successor,
            )
        return response

    @app.exception_handler(RequestValidationError)
    async def versioned_validation_error(request: Request, exc: RequestValidationError):
        if not _is_v1(request):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error_code": "INPUT_VALIDATION_ERROR",
                "message": "request validation failed",
                "request_id": f"req-{uuid4().hex}",
                "details": {},
            },
        )

    @app.get("/health", tags=["system"])
    def health(request: Request):
        return _service(request).health()

    @app.get("/quality/status", tags=["quality"])
    def quality_status(request: Request):
        request_id = f"req-{uuid4().hex}"
        expected = str(config.get("quality.read_token") or "")
        authorization = request.headers.get("authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return _quality_error(
                401,
                "QUALITY_AUTH_FAILED",
                "quality endpoint authentication failed",
                request_id,
            )
        try:
            payload = _service(request).quality_status()
        except Exception:
            return _quality_error(
                503,
                "QUALITY_STATUS_UNAVAILABLE",
                "quality status is unavailable",
                request_id,
            )
        if payload is None:
            return _quality_error(
                503,
                "QUALITY_STATUS_UNAVAILABLE",
                "quality status is unavailable",
                request_id,
            )
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        etag = f'"sha256:{hashlib.sha256(canonical).hexdigest()}"'
        headers = _quality_headers(request_id, etag=etag)
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(content=payload, headers=headers)

    @app.get("/accounts", tags=["accounts"], include_in_schema=False)
    @app.get("/api/v1/accounts", tags=["accounts"], response_model=AccountsResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def list_accounts(
        request: Request,
        include_default: bool = Query(True, description="Include configured default account even if empty."),
    ):
        return _public_result(
            request,
            _service(request).list_accounts(include_default=include_default),
            dataset_ids=("pm.account_mapping",),
        )

    @app.get("/accounts/nav", tags=["accounts"])
    def list_nav_accounts(
        request: Request,
        include_default: bool = Query(False, description="Include configured default account even if empty."),
    ):
        return _service(request).list_nav_accounts(include_default=include_default)

    @app.get("/accounts/overview", tags=["accounts"], include_in_schema=False)
    @app.get("/api/v1/accounts/overview", tags=["accounts"], response_model=AccountsOverviewResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def multi_account_overview(
        request: Request,
        accounts: Optional[str] = Query(None, description="Comma-separated accounts. Empty means auto-discover."),
        price_timeout: int = Query(30, ge=1, le=300),
        include_details: bool = Query(False),
    ):
        selected_accounts = tuple(item.strip() for item in (accounts or "").split(",") if item.strip())
        return _public_result(request, _service(request).multi_account_overview(
            accounts=accounts,
            price_timeout=price_timeout,
            include_details=include_details,
        ), dataset_ids=(
            "pm.holdings_quantity",
            "pm.cash_aggregate",
            "pm.fund_mmf",
            "pm.prices",
            "pm.fx",
        ), accounts=selected_accounts)

    @app.get("/holdings", tags=["holdings"], include_in_schema=False)
    @app.get("/api/v1/holdings", tags=["holdings"], response_model=HoldingsResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def get_holdings_query(
        request: Request,
        account: str = Query(...),
        include_cash: bool = Query(True),
        group_by_market: bool = Query(False),
        include_price: bool = Query(False),
    ):
        required = ["pm.holdings_quantity", "pm.cost_basis"]
        if include_cash:
            required.extend(["pm.cash_aggregate", "pm.fund_mmf"])
        return _public_result(request, _service(request).get_holdings(
            account=account,
            include_cash=include_cash,
            group_by_market=group_by_market,
            include_price=include_price,
        ), dataset_ids=tuple(required), accounts=(account,))


    @app.get("/cash", tags=["cash"], include_in_schema=False)
    @app.get("/api/v1/cash", tags=["cash"], response_model=CashResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def get_cash_query(request: Request, account: str = Query(...)):
        return _public_result(
            request,
            _service(request).get_cash(account=account),
            dataset_ids=("pm.cash_aggregate", "pm.fund_mmf"),
            accounts=(account,),
        )


    @app.post("/futu/holdings/sync", tags=["holdings"], include_in_schema=False)
    @app.post("/api/v1/futu/holdings/sync", tags=["holdings"], response_model=FutuHoldingsSyncResponse, responses=V1_RESPONSES)
    def sync_futu_holdings_query(request: Request, payload: FutuHoldingsSyncRequest):
        return _public_result(request, _service(request).sync_futu_holdings(**_payload_dict(payload)))


    @app.get("/nav", tags=["nav"], include_in_schema=False)
    @app.get("/api/v1/nav", tags=["nav"], response_model=NavResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def get_nav_query(
        request: Request,
        account: str = Query(...),
        days: int = Query(30, ge=1, le=10000),
    ):
        return _public_result(
            request,
            _service(request).get_nav(account=account, days=days),
            dataset_ids=("pm.nav", "pm.nav_history"),
            accounts=(account,),
        )

    @app.get("/analysis/capital-facts", tags=["analysis"], include_in_schema=False)
    @app.get(
        "/api/v1/analysis/capital-facts",
        tags=["analysis"],
        response_model=CapitalFactsResponse,
        responses=V1_RESPONSES,
    )
    def get_capital_facts_query(
        request: Request,
        account: str = Query(...),
        period: Literal["mtd", "ytd"] = Query(...),
        as_of_month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    ):
        return _public_result(request, _service(request).get_capital_facts(
            account=account,
            period=period,
            as_of_month=as_of_month,
        ), dataset_ids=("pm.nav_history",), accounts=(account,))

    @app.post("/analysis/valuation-evidence", tags=["analysis"], include_in_schema=False)
    @app.post(
        "/api/v1/analysis/valuation-evidence",
        tags=["analysis"],
        response_model=ValuationEvidenceResponse,
        responses=V1_RESPONSES,
    )
    def get_valuation_evidence_query(
        request: Request,
        payload: ValuationEvidenceRequest,
    ):
        result = _service(request).get_valuation_evidence(**_payload_dict(payload))
        if not _is_v1(request) and result.get("success") is False and result.get("error_code") == "INPUT_ERROR":
            return JSONResponse(status_code=400, content=result)
        return _public_result(
            request,
            result,
            input_error_status=400,
            dataset_ids=("pm.holdings_quantity", "pm.prices", "pm.fx"),
            accounts=tuple(payload.accounts),
        )


    @app.post("/nav/record", tags=["nav"])
    def record_nav_query(request: Request, payload: NavRecordRequest):
        kwargs = _payload_dict(payload)
        return _service(request).record_nav(**kwargs)


    @app.get("/nav/duplicates", tags=["nav"])
    def audit_nav_history_duplicates(request: Request, account: Optional[str] = Query(None)):
        return _service(request).audit_nav_history_duplicates(account=account)

    @app.get("/distribution", tags=["positions"], include_in_schema=False)
    @app.get("/api/v1/distribution", tags=["positions"], response_model=DistributionResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def get_distribution_query(
        request: Request,
        account: Optional[str] = Query(None),
        accounts: Optional[str] = Query(None, description="Comma-separated accounts; overrides account."),
        by_asset: bool = Query(False, description="Group distribution by asset code."),
        include_value: bool = Query(True, description="Include market value and ratio fields."),
        group_cash: bool = Query(False, description="Collapse cash and MMF into one row."),
    ):
        kwargs = {
            "account": account,
            "accounts": accounts,
            "by_asset": by_asset,
            "include_value": include_value,
        }
        if group_cash:
            kwargs["group_cash"] = True
        selected_accounts = tuple(item.strip() for item in (accounts or account or "").split(",") if item.strip())
        return _public_result(
            request,
            _service(request).get_distribution(**kwargs),
            dataset_ids=(
                "pm.holdings_quantity",
                "pm.cash_aggregate",
                "pm.fund_mmf",
                "pm.prices",
                "pm.fx",
            ),
            accounts=selected_accounts,
        )


    @app.get("/report/full", tags=["reports"], include_in_schema=False)
    @app.get("/api/v1/report/full", tags=["reports"], response_model=FullReportResponse, response_model_exclude_none=True, responses=V1_RESPONSES)
    def full_report_query(
        request: Request,
        account: str = Query(...),
        price_timeout: int = Query(30, ge=1, le=300),
    ):
        return _public_result(
            request,
            _service(request).full_report(account=account, price_timeout=price_timeout),
            dataset_ids=(
                "pm.holdings_quantity",
                "pm.cash_aggregate",
                "pm.fund_mmf",
                "pm.prices",
                "pm.fx",
                "pm.nav",
                "pm.nav_history",
            ),
            accounts=(account,),
        )


    @app.post("/report/daily-bundle", tags=["reports"])
    def daily_report_bundle_query(request: Request, payload: DailyReportBundleRequest):
        kwargs = _payload_dict(payload)
        return _service(request).daily_report_bundle(**kwargs)


    @app.post("/daily-nav-job", tags=["nav"])
    def daily_nav_job_query(request: Request, payload: DailyNavJobRequest):
        kwargs = _payload_dict(payload)
        return _service(request).daily_nav_job(**kwargs)


    @app.get("/report/{report_type}", tags=["reports"])
    def generate_report_query(
        request: Request,
        report_type: str,
        account: str = Query(...),
        price_timeout: int = Query(30, ge=1, le=300),
    ):
        if report_type not in REPORT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported report_type={report_type}; expected one of {sorted(REPORT_TYPES)}",
            )
        return _service(request).generate_report(
            account=account,
            report_type=report_type,
            price_timeout=price_timeout,
        )


    return app


app = create_app()
