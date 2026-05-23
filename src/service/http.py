"""FastAPI HTTP service for portfolio-management."""
from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from .application import PortfolioService


REPORT_TYPES = {"daily", "monthly", "yearly"}


class NavRecordRequest(BaseModel):
    account: Optional[str] = None
    price_timeout: int = 30
    dry_run: bool = True
    confirm: bool = False
    overwrite_existing: bool = True
    use_bulk_persist: bool = False
    run_id: Optional[str] = None


class DailyReportBundleRequest(BaseModel):
    account: Optional[str] = None
    price_timeout: int = 30
    dry_run: bool = True
    confirm: bool = False
    use_bulk_persist: bool = False
    sync_futu_cash_mmf: bool = False
    sync_futu_dry_run: bool = True
    run_id: Optional[str] = None


def _service(request: Request) -> PortfolioService:
    return request.app.state.portfolio_service


def create_app(service: Optional[PortfolioService] = None) -> FastAPI:
    app = FastAPI(
        title="Portfolio Management Service",
        version="0.1.0",
        description="Service-first API for portfolio accounts, holdings, NAV, and reports.",
    )
    app.state.portfolio_service = service or PortfolioService()

    @app.get("/health", tags=["system"])
    def health(request: Request):
        return _service(request).health()

    @app.get("/accounts", tags=["accounts"])
    def list_accounts(
        request: Request,
        include_default: bool = Query(True, description="Include configured default account even if empty."),
    ):
        return _service(request).list_accounts(include_default=include_default)

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

    @app.get("/accounts/{account}/holdings", tags=["holdings"])
    def get_holdings(
        request: Request,
        account: str,
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

    @app.get("/accounts/{account}/cash", tags=["cash"])
    def get_cash(request: Request, account: str):
        return _service(request).get_cash(account=account)

    @app.get("/nav", tags=["nav"])
    def get_nav_query(
        request: Request,
        account: str = Query(...),
        days: int = Query(30, ge=1, le=10000),
    ):
        return _service(request).get_nav(account=account, days=days)

    @app.get("/accounts/{account}/nav", tags=["nav"])
    def get_nav(
        request: Request,
        account: str,
        days: int = Query(30, ge=1, le=10000),
    ):
        return _service(request).get_nav(account=account, days=days)

    @app.post("/nav/record", tags=["nav"])
    def record_nav_query(request: Request, payload: NavRecordRequest):
        kwargs = dict(
            account=payload.account,
            price_timeout=payload.price_timeout,
            dry_run=payload.dry_run,
            confirm=payload.confirm,
            overwrite_existing=payload.overwrite_existing,
            use_bulk_persist=payload.use_bulk_persist,
        )
        if payload.run_id is not None:
            kwargs["run_id"] = payload.run_id
        return _service(request).record_nav(**kwargs)

    @app.post("/accounts/{account}/nav/record", tags=["nav"])
    def record_nav(request: Request, account: str, payload: NavRecordRequest):
        kwargs = dict(
            account=account,
            price_timeout=payload.price_timeout,
            dry_run=payload.dry_run,
            confirm=payload.confirm,
            overwrite_existing=payload.overwrite_existing,
            use_bulk_persist=payload.use_bulk_persist,
        )
        if payload.run_id is not None:
            kwargs["run_id"] = payload.run_id
        return _service(request).record_nav(**kwargs)

    @app.get("/distribution", tags=["positions"])
    def get_distribution_query(request: Request, account: str = Query(...)):
        return _service(request).get_distribution(account=account)

    @app.get("/accounts/{account}/distribution", tags=["positions"])
    def get_distribution(request: Request, account: str):
        return _service(request).get_distribution(account=account)

    @app.get("/report/full", tags=["reports"])
    def full_report_query(
        request: Request,
        account: str = Query(...),
        price_timeout: int = Query(30, ge=1, le=300),
    ):
        return _service(request).full_report(account=account, price_timeout=price_timeout)

    @app.get("/accounts/{account}/report/full", tags=["reports"])
    def full_report(
        request: Request,
        account: str,
        price_timeout: int = Query(30, ge=1, le=300),
    ):
        return _service(request).full_report(account=account, price_timeout=price_timeout)

    @app.post("/report/daily-bundle", tags=["reports"])
    def daily_report_bundle_query(request: Request, payload: DailyReportBundleRequest):
        kwargs = dict(
            account=payload.account,
            price_timeout=payload.price_timeout,
            dry_run=payload.dry_run,
            confirm=payload.confirm,
            use_bulk_persist=payload.use_bulk_persist,
            sync_futu_cash_mmf=payload.sync_futu_cash_mmf,
            sync_futu_dry_run=payload.sync_futu_dry_run,
        )
        if payload.run_id is not None:
            kwargs["run_id"] = payload.run_id
        return _service(request).daily_report_bundle(**kwargs)

    @app.post("/accounts/{account}/report/daily-bundle", tags=["reports"])
    def daily_report_bundle(request: Request, account: str, payload: DailyReportBundleRequest):
        kwargs = dict(
            account=account,
            price_timeout=payload.price_timeout,
            dry_run=payload.dry_run,
            confirm=payload.confirm,
            use_bulk_persist=payload.use_bulk_persist,
            sync_futu_cash_mmf=payload.sync_futu_cash_mmf,
            sync_futu_dry_run=payload.sync_futu_dry_run,
        )
        if payload.run_id is not None:
            kwargs["run_id"] = payload.run_id
        return _service(request).daily_report_bundle(**kwargs)

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

    @app.get("/accounts/{account}/report/{report_type}", tags=["reports"])
    def generate_report(
        request: Request,
        account: str,
        report_type: str,
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
