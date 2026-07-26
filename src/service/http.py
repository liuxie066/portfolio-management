"""FastAPI HTTP service for portfolio-management."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .application import PortfolioService
from .bind import allow_remote_from_env, is_loopback_client
from src import config


REPORT_TYPES = {"daily", "monthly", "yearly"}


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


class ValuationEvidenceRequest(BaseModel):
    accounts: list[str] = Field(min_length=1, max_length=20)
    supplemental_codes: list[str] = Field(default_factory=list, max_length=500)
    price_timeout: int = Field(default=30, ge=1, le=300)


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
        return await call_next(request)

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

    @app.get("/accounts", tags=["accounts"])
    def list_accounts(
        request: Request,
        include_default: bool = Query(True, description="Include configured default account even if empty."),
    ):
        return _service(request).list_accounts(include_default=include_default)

    @app.get("/accounts/nav", tags=["accounts"])
    def list_nav_accounts(
        request: Request,
        include_default: bool = Query(False, description="Include configured default account even if empty."),
    ):
        return _service(request).list_nav_accounts(include_default=include_default)

    @app.get("/accounts/overview", tags=["accounts"])
    def multi_account_overview(
        request: Request,
        accounts: Optional[str] = Query(None, description="Comma-separated accounts. Empty means auto-discover."),
        price_timeout: int = Query(30, ge=1, le=300),
        include_details: bool = Query(False),
    ):
        return _service(request).multi_account_overview(
            accounts=accounts,
            price_timeout=price_timeout,
            include_details=include_details,
        )

    @app.get("/holdings", tags=["holdings"])
    def get_holdings_query(
        request: Request,
        account: str = Query(...),
        include_cash: bool = Query(True),
        group_by_market: bool = Query(False),
        include_price: bool = Query(False),
    ):
        return _service(request).get_holdings(
            account=account,
            include_cash=include_cash,
            group_by_market=group_by_market,
            include_price=include_price,
        )


    @app.get("/cash", tags=["cash"])
    def get_cash_query(request: Request, account: str = Query(...)):
        return _service(request).get_cash(account=account)


    @app.post("/futu/holdings/sync", tags=["holdings"])
    def sync_futu_holdings_query(request: Request, payload: FutuHoldingsSyncRequest):
        return _service(request).sync_futu_holdings(**_payload_dict(payload))


    @app.get("/nav", tags=["nav"])
    def get_nav_query(
        request: Request,
        account: str = Query(...),
        days: int = Query(30, ge=1, le=10000),
    ):
        return _service(request).get_nav(account=account, days=days)

    @app.get("/analysis/capital-facts", tags=["analysis"])
    def get_capital_facts_query(
        request: Request,
        account: str = Query(...),
        period: Literal["mtd", "ytd"] = Query(...),
        as_of_month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    ):
        return _service(request).get_capital_facts(
            account=account,
            period=period,
            as_of_month=as_of_month,
        )

    @app.post("/analysis/valuation-evidence", tags=["analysis"])
    def get_valuation_evidence_query(
        request: Request,
        payload: ValuationEvidenceRequest,
    ):
        result = _service(request).get_valuation_evidence(**_payload_dict(payload))
        if result.get("success") is False and result.get("error_code") == "INPUT_ERROR":
            return JSONResponse(status_code=400, content=result)
        return result


    @app.post("/nav/record", tags=["nav"])
    def record_nav_query(request: Request, payload: NavRecordRequest):
        kwargs = _payload_dict(payload)
        return _service(request).record_nav(**kwargs)


    @app.get("/nav/duplicates", tags=["nav"])
    def audit_nav_history_duplicates(request: Request, account: Optional[str] = Query(None)):
        return _service(request).audit_nav_history_duplicates(account=account)

    @app.get("/distribution", tags=["positions"])
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
        return _service(request).get_distribution(**kwargs)


    @app.get("/report/full", tags=["reports"])
    def full_report_query(
        request: Request,
        account: str = Query(...),
        price_timeout: int = Query(30, ge=1, le=300),
    ):
        return _service(request).full_report(account=account, price_timeout=price_timeout)


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
