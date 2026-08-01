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


def _public_holdings_preflight(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    return {
        key: value
        for key, value in payload.items()
        if key != "validated_snapshot"
    }


class AccountNavRecorderService:
    """Sync account cash inputs, build one valuation snapshot, and record NAV."""

    def __init__(
        self,
        *,
        account: str,
        storage: Any,
        portfolio: Any,
        read_service: Any,
        holdings_preflight: Any = None,
    ):
        self.account = account
        self.storage = storage
        self.portfolio = portfolio
        self.read_service = read_service
        self.holdings_preflight = holdings_preflight

    def record(
        self,
        *,
        nav_date: Optional[Any] = None,
        price_timeout: int = 30,
        snapshot: Optional[Dict[str, Any]] = None,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
        nav_write_context: Optional[NavWriteContext] = None,
        run_quote_pool: Any = None,
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

        if (
            self.holdings_preflight is not None
            and not dry_run
            and sync_futu_cash_mmf
            and sync_futu_dry_run is True
        ):
            return {
                "success": False,
                "status": "holdings_preflight_failed",
                "error": "formal NAV cannot consume a Futu dry-run projection",
                "account": self.account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }

        holdings_preflight_result = None
        try:
            futu_sync_result = None
            project_futu_dry_run = False
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
                project_futu_dry_run = bool(resolved_sync_futu_dry_run)

            if self.holdings_preflight is not None:
                if snapshot is not None:
                    raise ValueError(
                        "official holdings preflight does not accept a caller snapshot"
                    )
                holdings_preflight_result = self.holdings_preflight.prepare_account(
                    account=self.account,
                    dry_run=dry_run,
                    confirm=confirm,
                    trigger={
                        "mode": "daily_nav_preflight",
                        "account": self.account,
                        "nav_date": today.isoformat(),
                        "run_id": resolved_run_id,
                    },
                    futu_sync_result=futu_sync_result,
                    project_futu_dry_run=project_futu_dry_run,
                )
                if not holdings_preflight_result.get("success"):
                    public_preflight = _public_holdings_preflight(
                        holdings_preflight_result
                    ) or {}
                    failure = dict(public_preflight)
                    failure.update(
                        {
                            "account": self.account,
                            "date": today.isoformat(),
                            "run_id": resolved_run_id,
                            "dry_run": dry_run,
                            "confirm": confirm,
                            "futu_sync_result": futu_sync_result,
                            "holdings_preflight": public_preflight,
                        }
                    )
                    return failure

            if snapshot is None:
                t_snapshot = _now_ms()
                snapshot_kwargs = {"price_timeout_seconds": price_timeout}
                if run_quote_pool is not None:
                    snapshot_kwargs["run_quote_pool"] = run_quote_pool
                if holdings_preflight_result is not None:
                    validated = holdings_preflight_result["validated_snapshot"]
                    snapshot_kwargs.update(
                        {
                            "holdings": validated.to_valuation_holdings(),
                            "holdings_provenance": validated.provenance(),
                            "holdings_warnings": list(validated.warnings),
                        }
                    )
                snapshot = self.read_service.build_snapshot(**snapshot_kwargs)
                snapshot_ms = _now_ms() - t_snapshot
            else:
                snapshot_ms = 0
            snapshot["run_id"] = resolved_run_id
            cash_flow_dataset = self.portfolio.build_cash_flow_dataset(
                account=self.account,
                nav_date=today,
                run_id=resolved_run_id,
            )
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
                cash_flow_dataset=cash_flow_dataset,
                normalized_valuation=snapshot.get("normalized_valuation"),
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
            holdings_snapshot = dict(snapshot.get("holdings_snapshot") or {})
            if holdings_snapshot:
                nav_result["holdings_snapshot"] = holdings_snapshot
                nav_result["holdings_digest"] = holdings_snapshot.get(
                    "normalized_holdings_digest"
                )
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
                "holdings_preflight": (
                    _public_holdings_preflight(holdings_preflight_result)
                ),
                "holdings_snapshot": holdings_snapshot,
                "cash_flow_dataset": cash_flow_dataset.details(),
            }
        except Exception as e:
            failure = {
                "success": False,
                "error": str(e),
                "account": self.account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }
            public_preflight = _public_holdings_preflight(
                holdings_preflight_result
            )
            if public_preflight is not None:
                failure["holdings_preflight"] = public_preflight
            return failure

    def record_closed(
        self,
        *,
        nav_date: Optional[Any] = None,
        total_value: Any = None,
        cash_value: Any = None,
        stock_value: Any = None,
        overwrite_existing: bool = False,
        dry_run: bool = True,
        confirm: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record the compatibility CLOSED target through the official dataset path."""

        from src.run_id import new_run_id

        today = _coerce_date(nav_date) if nav_date is not None else bj_today()
        resolved_run_id = run_id or new_run_id("close-nav", self.account)
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
            missing_components = [
                field
                for field, value in (
                    ("total_value", total_value),
                    ("cash_value", cash_value),
                    ("stock_value", stock_value),
                )
                if value is None
            ]
            if missing_components:
                raise ValueError(
                    "CLOSED NAV requires explicit observed components: "
                    + ", ".join(missing_components)
                )

            cash_flow_dataset = self.portfolio.build_cash_flow_dataset(
                account=self.account,
                nav_date=today,
                run_id=resolved_run_id,
            )
            nav_record = self.portfolio.record_closed_nav(
                account=self.account,
                nav_date=today,
                total_value=total_value,
                cash_value=cash_value,
                stock_value=stock_value,
                cash_flow_dataset=cash_flow_dataset,
                run_id=resolved_run_id,
                overwrite_existing=overwrite_existing,
                dry_run=dry_run,
                nav_write_context=NavWriteContext(
                    status="closed",
                    writer="close-nav",
                    write_reason="account_closed",
                    nav_date=today,
                    run_id=resolved_run_id,
                ),
            )
            return {
                "success": True,
                "dry_run": dry_run,
                "account": self.account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "nav": nav_record.nav,
                "shares": nav_record.shares,
                "total_value": nav_record.total_value,
                "cash_flow_dataset": cash_flow_dataset.details(),
                "message": (
                    f"已演练 {today} 清仓净值点（CLOSED）"
                    if dry_run
                    else f"已记录 {today} 清仓净值点（CLOSED）：shares=0, nav=1.0"
                ),
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "account": self.account,
                "date": today.isoformat(),
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
            }
