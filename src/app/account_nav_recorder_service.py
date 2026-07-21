"""Single-account NAV recording service."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from src.app.nav_finality import NavWriteContext
from src.app.nav_payload import format_nav_payload
from src.time_utils import bj_today


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _snapshot_failure(nav_record: Any) -> Optional[Dict[str, Any]]:
    details = getattr(nav_record, "details", None) or {}
    failed = details.get("snapshot_persisted") is False or details.get("snapshot_status") == "failed"
    if not failed:
        return None
    snapshot_error = details.get("snapshot_error") or "holdings_snapshot recovery required"
    return {
        "snapshot_status": details.get("snapshot_status") or "failed",
        "snapshot_persisted": False,
        "snapshot_error": snapshot_error,
        "error": snapshot_error,
        "task_id": details.get("snapshot_task_id"),
        "retry_command": details.get("snapshot_retry_command"),
    }


def _set_run_id(payload: Any, run_id: str) -> Any:
    if isinstance(payload, dict):
        payload.setdefault("run_id", run_id)
    return payload


class AccountNavRecorderService:
    """Sync account cash inputs, build one valuation snapshot, and record NAV."""

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

    def record(
        self,
        *,
        nav_date: Optional[Any] = None,
        price_timeout: int = 30,
        snapshot: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = True,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
        nav_write_context: Optional[NavWriteContext] = None,
    ) -> Dict[str, Any]:
        from src.app import FutuBalanceSyncService
        from src.run_id import new_run_id

        today = _coerce_date(nav_date) if nav_date is not None else bj_today()
        resolved_run_id = run_id or new_run_id("daily-report", self.account)

        if (not dry_run) and (not confirm):
            return {
                "success": False,
                "error": "Refuse to write nav_history without confirm=True (safety guard).",
                "account": self.account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }

        try:
            futu_sync_result = None
            if sync_futu_cash_mmf:
                resolved_sync_futu_dry_run = (
                    True
                    if dry_run
                    else (False if sync_futu_dry_run is None else sync_futu_dry_run)
                )
                futu_sync_result = FutuBalanceSyncService(self.storage).sync_cash_and_mmf(
                    account=self.account,
                    dry_run=resolved_sync_futu_dry_run,
                )
                if not futu_sync_result.get("success"):
                    return _set_run_id(futu_sync_result, resolved_run_id)

            if snapshot is None:
                t_snapshot = _now_ms()
                snapshot = self.read_service.build_snapshot(price_timeout_seconds=price_timeout)
                snapshot_ms = _now_ms() - t_snapshot
            else:
                snapshot_ms = 0
            snapshot["run_id"] = resolved_run_id
            resolved_context = nav_write_context or NavWriteContext(
                status="manual",
                writer="nav-record",
                write_reason="manual_nav_record",
                nav_date=today,
                run_id=resolved_run_id,
            )
            resolved_context = resolved_context.with_runtime(
                valuation_as_of=snapshot.get("snapshot_time"),
                run_id=resolved_run_id,
            )

            t_record_nav = _now_ms()
            nav_record = self.portfolio.record_nav(
                self.account,
                valuation=snapshot["valuation"],
                nav_date=today,
                persist=True,
                overwrite_existing=overwrite_existing,
                dry_run=dry_run,
                use_bulk_persist=use_bulk_persist,
                run_id=resolved_run_id,
                nav_write_context=resolved_context,
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

            return {
                "success": bool(nav_result.get("success")),
                "status": nav_result.get("status") or ("recorded" if not dry_run else "dry_run"),
                "account": self.account,
                "run_id": resolved_run_id,
                "date": today.isoformat(),
                "snapshot": snapshot,
                "nav_record": nav_record,
                "nav_result": nav_result,
                "stage_timings": {
                    "snapshot_ms": snapshot_ms,
                    "record_nav_ms": record_nav_ms,
                },
                "futu_sync_result": futu_sync_result,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "account": self.account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }
