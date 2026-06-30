"""Multi-account daily NAV job orchestration."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Dict, Optional

from src.app.account_service import AccountService, normalize_accounts
from src.app.business_calendar_service import BusinessCalendarService


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


class DailyNavJobService:
    """Run daily NAV recording for one or many accounts through one path."""

    def __init__(
        self,
        *,
        storage: Any,
        portfolio: Any,
        default_account: Optional[str] = None,
        read_service_factory: Optional[Callable[..., Any]] = None,
        calendar: Optional[BusinessCalendarService] = None,
        account_runner_factory: Optional[Callable[[str], Any]] = None,
    ):
        self.storage = storage
        self.portfolio = portfolio
        self.default_account = default_account
        self.read_service_factory = read_service_factory
        self.calendar = calendar or BusinessCalendarService.from_config()
        self.account_runner_factory = account_runner_factory

    def _read_service(self, account: str) -> Any:
        if self.read_service_factory is not None:
            return self.read_service_factory(
                account=account,
                storage=self.storage,
                portfolio=self.portfolio,
                reporting_service=self.portfolio.reporting_service,
            )

        from src.app import PortfolioReadService

        return PortfolioReadService(
            account=account,
            storage=self.storage,
            portfolio=self.portfolio,
            reporting_service=self.portfolio.reporting_service,
        )

    def _account_runner(self, account: str) -> Any:
        if self.account_runner_factory is not None:
            return self.account_runner_factory(account)

        from src.app import DailyAccountNavService

        return DailyAccountNavService(
            account=account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(account),
        )

    def _resolve_accounts(self, accounts: Any = None, account: Optional[str] = None) -> Dict[str, Any]:
        raw_accounts = accounts if accounts is not None else account
        target_accounts = normalize_accounts(raw_accounts)
        if target_accounts is not None:
            return {"success": True, "accounts": target_accounts, "source": "input"}

        discovery = AccountService(
            storage=self.storage,
            default_account=self.default_account,
        ).list_nav_accounts(include_default=False)
        if not discovery.get("success"):
            return discovery
        return {
            "success": True,
            "accounts": discovery.get("accounts") or [],
            "source": "holdings",
            "discovery": discovery,
        }

    def _audit_duplicates(self, account: str) -> Optional[Dict[str, Any]]:
        audit = getattr(self.storage, "audit_nav_history_duplicates", None)
        if not callable(audit):
            return None
        result = audit(account=account)
        if result.get("duplicate_group_count", 0):
            return result
        return None

    def _existing_nav_item(self, account: str, nav_date: date) -> Optional[Dict[str, Any]]:
        get_nav_on_date = getattr(self.storage, "get_nav_on_date", None)
        if not callable(get_nav_on_date):
            return None
        existing = get_nav_on_date(account, nav_date)
        if not existing:
            return None
        return {
            "status": "skipped_existing_nav",
            "success": True,
            "account": account,
            "date": nav_date.isoformat(),
            "record_id": getattr(existing, "record_id", None),
            "nav": getattr(existing, "nav", None),
            "total_value": getattr(existing, "total_value", None),
        }

    def _cash_flow_blocker(self, account: str, *, dry_run: bool) -> Optional[Dict[str, Any]]:
        reconcile = getattr(self.storage, "reconcile_cash_flows", None)
        if not callable(reconcile):
            return None
        result = reconcile(account=account, dry_run=dry_run)
        if result.get("success") is False:
            return {
                "status": "cash_flow_check_failed",
                "success": False,
                "account": account,
                "error": result.get("error") or "cash-flow reconcile failed",
                "cash_flow_reconcile": result,
            }
        if int(result.get("error_count") or 0) > 0:
            return {
                "status": "cash_flow_error",
                "success": False,
                "account": account,
                "error": "cash_flow has invalid manual rows",
                "cash_flow_reconcile": result,
            }
        if dry_run and int(result.get("change_count") or 0) > 0:
            return {
                "status": "cash_flow_pending",
                "success": False,
                "account": account,
                "error": "cash_flow has generated fields pending; run pm cash-flow reconcile --apply --confirm",
                "cash_flow_reconcile": result,
            }
        return None

    @staticmethod
    def _summarize(items: list[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for item in items:
            status = str(item.get("status") or ("ok" if item.get("success") else "failed"))
            summary[status] = summary.get(status, 0) + 1
        return summary

    def run(
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
        from src.run_id import new_run_id

        resolved_nav_date = (
            _coerce_date(nav_date)
            if nav_date is not None and str(nav_date) != "auto"
            else self.calendar.default_nav_date(run_date=run_date)
        )
        resolved_run_id = run_id or new_run_id("daily-nav-job", account or "multi")
        calendar_info = self.calendar.explain(resolved_nav_date)

        if (not dry_run) and (not confirm):
            return {
                "success": False,
                "status": "failed",
                "error": "daily-nav-job write requires confirm=True",
                "date": resolved_nav_date.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }

        if (not calendar_info.get("business_day")) and not force_non_business_day:
            return {
                "success": True,
                "status": "skipped_non_business_day",
                "date": resolved_nav_date.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "calendar": calendar_info,
                "items": [],
                "summary": {"skipped_non_business_day": 1},
            }

        account_result = self._resolve_accounts(accounts=accounts, account=account)
        if not account_result.get("success"):
            return {
                "success": False,
                "status": "failed",
                "date": resolved_nav_date.isoformat(),
                "run_id": resolved_run_id,
                "error": account_result.get("error") or "failed to resolve accounts",
                "account_result": account_result,
            }

        target_accounts = account_result.get("accounts") or []
        if not target_accounts:
            return {
                "success": True,
                "status": "skipped_no_accounts",
                "date": resolved_nav_date.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
                "calendar": calendar_info,
                "account_source": account_result.get("source"),
                "accounts": [],
                "items": [],
                "summary": {"skipped_no_accounts": 1},
            }

        items: list[Dict[str, Any]] = []
        resolved_sync_futu_dry_run = (
            True
            if dry_run
            else (False if sync_futu_dry_run is None else sync_futu_dry_run)
        )
        for target_account in target_accounts:
            duplicate_audit = self._audit_duplicates(target_account)
            if duplicate_audit:
                items.append({
                    "status": "nav_history_duplicate",
                    "success": False,
                    "account": target_account,
                    "date": resolved_nav_date.isoformat(),
                    "error": "nav_history has duplicate account/date records; repair before NAV write",
                    "duplicate_audit": duplicate_audit,
                })
                continue

            cash_flow_blocker = self._cash_flow_blocker(target_account, dry_run=dry_run)
            if cash_flow_blocker:
                cash_flow_blocker.setdefault("date", resolved_nav_date.isoformat())
                items.append(cash_flow_blocker)
                continue

            if not overwrite_existing:
                existing_item = self._existing_nav_item(target_account, resolved_nav_date)
                if existing_item:
                    items.append(existing_item)
                    continue

            item_run_id = f"{resolved_run_id}:{target_account}"
            result = self._account_runner(target_account).run(
                nav_date=resolved_nav_date,
                price_timeout=price_timeout,
                dry_run=dry_run,
                confirm=confirm,
                overwrite_existing=overwrite_existing,
                use_bulk_persist=use_bulk_persist,
                sync_futu_cash_mmf=sync_futu_cash_mmf,
                sync_futu_dry_run=resolved_sync_futu_dry_run,
                run_id=item_run_id,
            )
            result.setdefault("account", target_account)
            result.setdefault("date", resolved_nav_date.isoformat())
            result["status"] = "dry_run" if dry_run and result.get("success") else ("written" if result.get("success") else "failed")
            items.append(result)

        summary = self._summarize(items)
        blocking_statuses = {
            "failed",
            "cash_flow_check_failed",
            "cash_flow_error",
            "cash_flow_pending",
            "nav_history_duplicate",
        }
        has_blocker = any(str(item.get("status")) in blocking_statuses or item.get("success") is False for item in items)
        status = "completed" if not has_blocker else ("failed" if len(items) == 1 else "partial")

        return {
            "success": not has_blocker,
            "status": status,
            "date": resolved_nav_date.isoformat(),
            "run_id": resolved_run_id,
            "dry_run": dry_run,
            "confirm": confirm,
            "calendar": calendar_info,
            "account_source": account_result.get("source"),
            "accounts": target_accounts,
            "items": items,
            "summary": summary,
        }
