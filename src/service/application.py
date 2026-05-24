"""Service facade for portfolio-management use cases.

This layer is intentionally thin during the migration from Skill-first to
service-first architecture. It gives HTTP, CLI, MCP, and future workers one
application boundary while `skill_api.py` remains a caller-facing adapter.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.app.nav_payload import format_nav_payload


def _snapshot_failure(nav_record: Any) -> Optional[Dict[str, Any]]:
    details = getattr(nav_record, "details", None) or {}
    snapshot_error = details.get("snapshot_error")
    if not snapshot_error:
        return None
    return {
        "snapshot_status": details.get("snapshot_status") or "failed",
        "snapshot_persisted": bool(details.get("snapshot_persisted")),
        "snapshot_error": snapshot_error,
    }


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _set_run_id(payload: Any, run_id: str) -> Any:
    if isinstance(payload, dict):
        payload.setdefault("run_id", run_id)
    return payload


class PortfolioService:
    """Application service boundary used by HTTP and other adapters."""

    def __init__(
        self,
        backend: Optional[Any] = None,
        *,
        storage: Optional[Any] = None,
        portfolio: Optional[Any] = None,
        price_fetcher: Optional[Any] = None,
        storage_factory: Optional[Any] = None,
        portfolio_factory: Optional[Any] = None,
        read_service_factory: Optional[Any] = None,
        default_account: Optional[str] = None,
    ):
        self._backend = backend
        self._storage = storage
        self._portfolio = portfolio
        self._price_fetcher = price_fetcher
        self._storage_factory = storage_factory
        self._portfolio_factory = portfolio_factory
        self._read_service_factory = read_service_factory
        self._default_account = default_account

    @property
    def backend(self) -> Any:
        if self._backend is None:
            import skill_api

            self._backend = skill_api
        return self._backend

    @property
    def storage(self) -> Any:
        if self._storage is None:
            if self._storage_factory is not None:
                try:
                    self._storage = self._storage_factory(healthcheck=False)
                except TypeError:
                    self._storage = self._storage_factory()
            else:
                from src.storage import create_storage

                self._storage = create_storage(healthcheck=False)
        return self._storage

    @property
    def portfolio(self) -> Any:
        if self._portfolio is None:
            if self._portfolio_factory is not None:
                self._portfolio = self._portfolio_factory(self.storage)
            else:
                from src.portfolio import PortfolioManager

                if self._price_fetcher is None:
                    self._portfolio = PortfolioManager(self.storage)
                else:
                    self._portfolio = PortfolioManager(self.storage, price_fetcher=self._price_fetcher)
        return self._portfolio

    def _resolve_account(self, account: Optional[str]) -> str:
        if account:
            return account
        if self._default_account:
            return self._default_account
        from src import config

        return config.get_account()

    def _read_service(self, account: str) -> Any:
        portfolio = self.portfolio
        if self._read_service_factory is not None:
            return self._read_service_factory(
                account=account,
                storage=self.storage,
                portfolio=portfolio,
                reporting_service=portfolio.reporting_service,
            )

        from src.app import PortfolioReadService

        return PortfolioReadService(
            account=account,
            storage=self.storage,
            portfolio=portfolio,
            reporting_service=portfolio.reporting_service,
        )

    def health(self) -> Dict[str, Any]:
        return {
            "success": True,
            "status": "ok",
            "service": "portfolio-management",
        }

    def list_accounts(self, *, include_default: bool = True) -> Dict[str, Any]:
        from src.app import AccountService

        return AccountService(
            storage=self.storage,
            default_account=self._resolve_account(None),
        ).list_accounts(include_default=include_default)

    def multi_account_overview(
        self,
        *,
        accounts: Any = None,
        price_timeout: int = 30,
        include_details: bool = False,
    ) -> Dict[str, Any]:
        from src.app import AccountService

        return AccountService(
            storage=self.storage,
            default_account=self._resolve_account(None),
            full_report_func=self.full_report,
        ).multi_account_overview(
            accounts=accounts,
            price_timeout=price_timeout,
            include_details=include_details,
        )

    def get_holdings(
        self,
        *,
        account: Optional[str] = None,
        include_cash: bool = True,
        group_by_market: bool = False,
        include_price: bool = False,
    ) -> Dict[str, Any]:
        try:
            return self._read_service(self._resolve_account(account)).get_holdings(
                include_cash=include_cash,
                group_by_market=group_by_market,
                include_price=include_price,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_cash(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        from src.app import CashService

        return CashService(self.storage).get_cash(self._resolve_account(account))

    def get_nav(self, *, account: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
        from src.app import NavReadService

        return NavReadService(storage=self.storage).get_nav(
            account=self._resolve_account(account),
            days=days,
        )

    def record_nav(
        self,
        *,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = True,
        use_bulk_persist: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from src.time_utils import bj_today
        from src.run_id import new_run_id

        resolved_account = self._resolve_account(account)
        today = bj_today()
        resolved_run_id = run_id or new_run_id("nav", resolved_account)

        if (not dry_run) and (not confirm):
            return {
                "success": False,
                "error": "Refuse to write nav_history without confirm=True (safety guard).",
                "account": resolved_account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }

        try:
            snapshot = self._read_service(resolved_account).build_snapshot(
                price_timeout_seconds=price_timeout,
            )
            snapshot["run_id"] = resolved_run_id
            valuation = snapshot["valuation"]
            nav_record = self.portfolio.record_nav(
                resolved_account,
                valuation=valuation,
                nav_date=today,
                persist=True,
                overwrite_existing=overwrite_existing,
                dry_run=dry_run,
                use_bulk_persist=use_bulk_persist,
                run_id=resolved_run_id,
            )

            nav_payload = format_nav_payload(nav_record)
            result = {
                "success": True,
                **nav_payload,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "message": (
                    f"已演练 {today} 净值写入: {nav_record.nav:.4f}"
                    if dry_run
                    else f"已记录 {today} 净值: {nav_record.nav:.4f}"
                ),
                "snapshot_time": snapshot.get("snapshot_time"),
                "dry_run": dry_run,
            }
            warnings = getattr(valuation, "warnings", None)
            if warnings:
                result["warnings"] = warnings

            failure = _snapshot_failure(nav_record)
            if failure:
                result.update(failure)
                result["success"] = False
                result["status"] = "failed" if dry_run else "partial"
                result["error"] = failure["snapshot_error"]
                result["message"] = (
                    f"净值已演练，但 holdings_snapshot 写入校验失败: {failure['snapshot_error']}"
                    if dry_run
                    else f"净值已写入，但 holdings_snapshot 写入失败: {failure['snapshot_error']}"
                )
            return result
        except Exception as e:
            return {"success": False, "error": str(e), "run_id": resolved_run_id}

    def get_distribution(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        try:
            return self._read_service(self._resolve_account(account)).get_distribution()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def full_report(self, *, account: Optional[str] = None, price_timeout: int = 30) -> Dict[str, Any]:
        from src.app import FullReportService

        resolved_account = self._resolve_account(account)
        read_service = self._read_service(resolved_account)
        return FullReportService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=read_service,
        ).full_report(price_timeout=price_timeout)

    def generate_report(
        self,
        *,
        account: Optional[str] = None,
        report_type: str = "daily",
        price_timeout: int = 30,
    ) -> Dict[str, Any]:
        from src.app import FullReportService, ReportGenerationService

        resolved_account = self._resolve_account(account)
        read_service = self._read_service(resolved_account)
        full_report_service = FullReportService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=read_service,
        )
        return ReportGenerationService(
            build_snapshot_func=read_service.build_snapshot,
            full_report_func=full_report_service.full_report,
        ).generate_report(
            report_type=report_type,
            price_timeout=price_timeout,
        )

    def daily_report_bundle(
        self,
        *,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = True,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: bool = True,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from src.app import FutuBalanceSyncService, FullReportService, NavReadService, ReportGenerationService
        from src.run_id import new_run_id
        from src.time_utils import bj_today

        resolved_account = self._resolve_account(account)
        today = bj_today()
        resolved_run_id = run_id or new_run_id("daily-report", resolved_account)

        if (not dry_run) and (not confirm):
            return {
                "success": False,
                "error": "Refuse to write nav_history without confirm=True (safety guard).",
                "account": resolved_account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }

        try:
            futu_sync_result = None
            if sync_futu_cash_mmf:
                futu_sync_result = FutuBalanceSyncService(self.storage).sync_cash_and_mmf(
                    account=resolved_account,
                    dry_run=sync_futu_dry_run,
                )
                if not futu_sync_result.get("success"):
                    return _set_run_id(futu_sync_result, resolved_run_id)

            read_service = self._read_service(resolved_account)

            t_snapshot = _now_ms()
            snapshot = read_service.build_snapshot(price_timeout_seconds=price_timeout)
            snapshot["run_id"] = resolved_run_id
            snapshot_ms = _now_ms() - t_snapshot

            t_navs = _now_ms()
            navs_all = self.storage.get_nav_history(resolved_account, days=9999)
            navs_ms = _now_ms() - t_navs

            t_record_nav = _now_ms()
            nav_record = self.portfolio.record_nav(
                resolved_account,
                valuation=snapshot["valuation"],
                nav_date=today,
                persist=True,
                overwrite_existing=overwrite_existing,
                dry_run=dry_run,
                use_bulk_persist=use_bulk_persist,
                run_id=resolved_run_id,
            )
            nav_payload = format_nav_payload(nav_record)
            nav_result = {
                "success": True,
                **nav_payload,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "message": (
                    f"已演练 {today} 净值写入: {nav_record.nav:.4f}"
                    if dry_run
                    else f"已记录 {today} 净值: {nav_record.nav:.4f}"
                ),
                "snapshot_time": snapshot.get("snapshot_time"),
                "dry_run": dry_run,
            }
            warnings = getattr(snapshot["valuation"], "warnings", None)
            if warnings:
                nav_result["warnings"] = warnings

            failure = _snapshot_failure(nav_record)
            if failure:
                nav_result.update(failure)
                nav_result["success"] = False
                nav_result["status"] = "failed" if dry_run else "partial"
                nav_result["error"] = failure["snapshot_error"]
                nav_result["message"] = (
                    f"净值已演练，但 holdings_snapshot 写入校验失败: {failure['snapshot_error']}"
                    if dry_run
                    else f"净值已写入，但 holdings_snapshot 写入失败: {failure['snapshot_error']}"
                )
            record_nav_ms = _now_ms() - t_record_nav
            if not nav_result.get("success"):
                return nav_result

            distribution = read_service.get_distribution(holdings_data=snapshot)
            if not distribution.get("success"):
                return _set_run_id(distribution, resolved_run_id)

            full_report_service = FullReportService(
                account=resolved_account,
                storage=self.storage,
                portfolio=self.portfolio,
                read_service=read_service,
            )
            t_report = _now_ms()
            report = ReportGenerationService(
                build_snapshot_func=read_service.build_snapshot,
                full_report_func=full_report_service.full_report,
            ).generate_report(
                report_type="daily",
                price_timeout=price_timeout,
                snapshot=snapshot,
                navs=navs_all,
                nav_override=nav_record,
            )
            report_ms = _now_ms() - t_report
            if not report.get("success"):
                return _set_run_id(report, resolved_run_id)
            report["run_id"] = resolved_run_id

            t_get_nav = _now_ms()
            nav_snapshot = NavReadService(storage=self.storage).get_nav(account=resolved_account, days=2)
            get_nav_ms = _now_ms() - t_get_nav
            if not nav_snapshot.get("success"):
                return _set_run_id(nav_snapshot, resolved_run_id)

            return {
                "success": True,
                "account": resolved_account,
                "run_id": resolved_run_id,
                "snapshot": snapshot,
                "nav_result": nav_result,
                "distribution": distribution,
                "report": report,
                "nav_snapshot": nav_snapshot,
                "stage_timings": {
                    "snapshot_ms": snapshot_ms,
                    "navs_all_ms": navs_ms,
                    "record_nav_ms": record_nav_ms,
                    "generate_report_ms": report_ms,
                    "get_nav_ms": get_nav_ms,
                },
                "futu_sync_result": futu_sync_result,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "account": resolved_account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }
