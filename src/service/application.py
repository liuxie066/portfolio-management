"""Service facade for portfolio-management use cases.

This layer gives HTTP, CLI, and future workers one application boundary.
`skill_api.py` remains a caller-facing adapter and is not a service dependency.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class PortfolioService:
    """Application service boundary used by HTTP and other adapters."""

    def __init__(
        self,
        *,
        storage: Optional[Any] = None,
        portfolio: Optional[Any] = None,
        price_fetcher: Optional[Any] = None,
        storage_factory: Optional[Any] = None,
        portfolio_factory: Optional[Any] = None,
        read_service_factory: Optional[Any] = None,
        futu_receipt_service: Optional[Any] = None,
        nav_receipt_service: Optional[Any] = None,
        default_account: Optional[str] = None,
    ):
        self._storage = storage
        self._portfolio = portfolio
        self._price_fetcher = price_fetcher
        self._storage_factory = storage_factory
        self._portfolio_factory = portfolio_factory
        self._read_service_factory = read_service_factory
        self._futu_receipt_service = futu_receipt_service
        self._nav_receipt_service = nav_receipt_service
        self._default_account = default_account

    @property
    def storage(self) -> Any:
        if self._storage is None:
            if self._storage_factory is not None:
                try:
                    self._storage = self._storage_factory(healthcheck=False)
                except TypeError:
                    self._storage = self._storage_factory()
            else:
                from src.feishu_storage import FeishuStorage

                self._storage = FeishuStorage()
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

    def list_nav_accounts(self, *, include_default: bool = False) -> Dict[str, Any]:
        from src.app import AccountService

        return AccountService(
            storage=self.storage,
            default_account=self._resolve_account(None),
        ).list_nav_accounts(include_default=include_default)

    def audit_nav_history_duplicates(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        audit = getattr(self.storage, "audit_nav_history_duplicates", None)
        if not callable(audit):
            return {"success": False, "error": "storage does not support nav_history duplicate audit"}
        return audit(account=account)

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

    def sync_futu_holdings(
        self,
        *,
        account: Optional[str] = None,
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
    ) -> Dict[str, Any]:
        from src.app import FutuBalanceSyncService
        from src.app.futu_sync_receipt_service import FutuSyncReceiptService

        resolved_account = self._resolve_account(account)
        try:
            result = FutuBalanceSyncService(self.storage).sync_portfolio(
                account=resolved_account,
                dry_run=dry_run,
                confirm=confirm,
                allow_empty_stock_snapshot=allow_empty_stock_snapshot,
            )
        except Exception as exc:
            result = {
                "success": False,
                "status": "failed",
                "account": resolved_account,
                "broker": "富途",
                "dry_run": dry_run,
                "error": str(exc),
            }

        receipt_service = self._futu_receipt_service or FutuSyncReceiptService()
        result = dict(result)
        result["receipt"] = receipt_service.send(result)
        return result

    def get_nav(self, *, account: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
        from src.app.nav_payload import format_nav_history_item, format_nav_payload

        try:
            navs = self.storage.get_nav_history(self._resolve_account(account), days=days)
            if not navs:
                return {"success": False, "message": "无净值记录"}
            return {
                "success": True,
                "latest": format_nav_payload(navs[-1]),
                "history": [format_nav_history_item(nav) for nav in navs],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_capital_facts(
        self,
        *,
        account: Optional[str] = None,
        period: str,
        as_of_month: str,
    ) -> Dict[str, Any]:
        from src.app import CapitalFactsService

        try:
            return CapitalFactsService(storage=self.storage).get(
                account=self._resolve_account(account),
                period=period,
                as_of_month=as_of_month,
            )
        except Exception as exc:
            return {"success": False, "status": "failed", "error": str(exc)}

    def record_nav(
        self,
        *,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        run_id: Optional[str] = None,
        nav_date: Optional[Any] = None,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from src.app import AccountNavRecorderService
        from src.run_id import new_run_id

        resolved_account = self._resolve_account(account)
        resolved_run_id = run_id or new_run_id("nav", resolved_account)
        result = AccountNavRecorderService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(resolved_account),
        ).record(
            nav_date=nav_date,
            price_timeout=price_timeout,
            snapshot=snapshot,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            run_id=resolved_run_id,
        )
        nav_result = result.get("nav_result")
        if isinstance(nav_result, dict):
            return nav_result
        return result

    def init_nav_history(
        self,
        *,
        account: Optional[str] = None,
        date_str: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        use_bulk_persist: bool = False,
    ) -> Dict[str, Any]:
        from src.app import NavInitializationService

        resolved_account = self._resolve_account(account)
        return NavInitializationService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(resolved_account),
        ).init_nav_history(
            date_str=date_str,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            use_bulk_persist=use_bulk_persist,
        )

    def get_distribution(
        self,
        *,
        account: Optional[str] = None,
        accounts: Any = None,
        by_asset: bool = False,
        include_value: bool = True,
        group_cash: bool = False,
    ) -> Dict[str, Any]:
        try:
            from src.app.account_service import normalize_accounts

            by_asset = bool(by_asset or group_cash)
            target_accounts = normalize_accounts(accounts)
            if target_accounts is None:
                if account is not None:
                    target_accounts = [account]
                else:
                    target_accounts = [self._resolve_account(None)]

            if len(target_accounts) == 1 and not by_asset:
                return self._read_service(target_accounts[0]).get_distribution()

            snapshots = []
            errors = []
            for acc in target_accounts:
                try:
                    snapshot = self._read_service(acc).build_snapshot()
                    snapshots.append(snapshot)
                except Exception as e:
                    errors.append({"account": acc, "error": str(e)})

            if not snapshots:
                return {"success": False, "error": errors[0]["error"] if errors else "no holdings data"}

            from src.app.portfolio_read_service import PortfolioReadService

            merged_holdings_data = PortfolioReadService.merge_holdings_data(
                [(s.get("holdings_data") or {}) for s in snapshots]
            )
            read_service = self._read_service(target_accounts[0])
            if by_asset:
                result = read_service.get_asset_distribution(
                    merged_holdings_data,
                    include_value=include_value,
                    group_cash=group_cash,
                )
            else:
                result = read_service.get_distribution(merged_holdings_data)

            result["accounts"] = target_accounts
            if errors:
                result["errors"] = errors
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def full_report(self, *, account: Optional[str] = None, price_timeout: int = 30) -> Dict[str, Any]:
        from src.app import ReportQueryService

        resolved_account = self._resolve_account(account)
        read_service = self._read_service(resolved_account)
        return ReportQueryService(
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
        from src.app import ReportGenerationService, ReportQueryService

        resolved_account = self._resolve_account(account)
        read_service = self._read_service(resolved_account)
        report_query_service = ReportQueryService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=read_service,
        )
        return ReportGenerationService(
            build_snapshot_func=read_service.build_snapshot,
            full_report_func=report_query_service.full_report,
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
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
        nav_date: Optional[Any] = None,
    ) -> Dict[str, Any]:
        resolved_account = self._resolve_account(account)
        from src.app import DailyAccountNavService

        return DailyAccountNavService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(resolved_account),
        ).run(
            nav_date=nav_date,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            sync_futu_cash_mmf=sync_futu_cash_mmf,
            sync_futu_dry_run=sync_futu_dry_run,
            run_id=run_id,
        )

    def daily_nav_job(
        self,
        *,
        nav_date: Optional[Any] = None,
        run_date: Optional[Any] = None,
        accounts: Any = None,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        force_non_business_day: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from src.app import DailyNavJobService
        from src.app.nav_history_receipt_service import NavHistoryReceiptService

        result = DailyNavJobService(
            storage=self.storage,
            portfolio=self.portfolio,
            default_account=self._resolve_account(None),
            read_service_factory=self._read_service_factory,
        ).run(
            nav_date=nav_date,
            run_date=run_date,
            accounts=accounts,
            account=account,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            sync_futu_cash_mmf=sync_futu_cash_mmf,
            sync_futu_dry_run=sync_futu_dry_run,
            force_non_business_day=force_non_business_day,
            run_id=run_id,
        )
        result = dict(result)
        result.setdefault("dry_run", dry_run)
        result.setdefault("confirm", confirm)
        receipt_service = self._nav_receipt_service or NavHistoryReceiptService()
        result["receipt"] = receipt_service.send(result)
        return result
