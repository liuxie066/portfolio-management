"""Single-account daily NAV bundle use case."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.app.account_nav_recorder_service import AccountNavRecorderService, _coerce_date, _set_run_id
from src.app.daily_report_payload_service import DailyReportPayloadService
from src.app.nav_finality import NavWriteContext
from src.time_utils import bj_today


class DailyAccountNavService:
    """Compatibility orchestrator for one account's daily NAV bundle."""

    def __init__(
        self,
        *,
        account: str,
        storage: Any,
        portfolio: Any,
        read_service: Any,
    ):
        self.account = account
        self.storage = storage
        self.portfolio = portfolio
        self.read_service = read_service

    def run(
        self,
        *,
        nav_date: Optional[Any] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
        nav_write_context: Optional[NavWriteContext] = None,
    ) -> Dict[str, Any]:
        resolved_date = _coerce_date(nav_date) if nav_date is not None else bj_today()
        resolved_context = nav_write_context or NavWriteContext(
            status="manual",
            writer="daily-report",
            write_reason="daily_report_bundle",
            nav_date=resolved_date,
            run_id=run_id,
        )
        record_result = AccountNavRecorderService(
            account=self.account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self.read_service,
        ).record(
            nav_date=nav_date,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            sync_futu_cash_mmf=sync_futu_cash_mmf,
            sync_futu_dry_run=sync_futu_dry_run,
            run_id=run_id,
            nav_write_context=resolved_context,
        )
        if not record_result.get("success"):
            nav_result = record_result.get("nav_result")
            if isinstance(nav_result, dict) and not nav_result.get("success"):
                return nav_result
            return record_result

        try:
            payload_result = DailyReportPayloadService(
                account=self.account,
                storage=self.storage,
                portfolio=self.portfolio,
                read_service=self.read_service,
            ).build(
                snapshot=record_result["snapshot"],
                nav_record=record_result["nav_record"],
                nav_result=record_result["nav_result"],
                price_timeout=price_timeout,
                run_id=record_result["run_id"],
            )
        except Exception as e:
            return {
                "success": False,
                "status": "failed" if dry_run else "partial",
                "error": str(e),
                "account": self.account,
                "date": record_result["date"],
                "run_id": record_result["run_id"],
                "dry_run": dry_run,
                "confirm": confirm,
                "nav_persisted": not dry_run,
                "nav_result": record_result.get("nav_result"),
            }
        if not payload_result.get("success"):
            payload_result.setdefault("status", "failed" if dry_run else "partial")
            payload_result.setdefault("nav_persisted", not dry_run)
            payload_result.setdefault("nav_result", record_result.get("nav_result"))
            payload_result.setdefault("account", self.account)
            payload_result.setdefault("date", record_result["date"])
            payload_result.setdefault("dry_run", dry_run)
            payload_result.setdefault("confirm", confirm)
            return _set_run_id(payload_result, record_result["run_id"])

        stage_timings = {
            **(record_result.get("stage_timings") or {}),
            **(payload_result.get("stage_timings") or {}),
        }
        return {
            "success": True,
            "account": self.account,
            "run_id": record_result["run_id"],
            "date": record_result["date"],
            "snapshot": record_result["snapshot"],
            "nav_result": record_result["nav_result"],
            "distribution": payload_result["distribution"],
            "report": payload_result["report"],
            "nav_snapshot": payload_result["nav_snapshot"],
            "stage_timings": stage_timings,
            "futu_sync_result": record_result.get("futu_sync_result"),
        }
