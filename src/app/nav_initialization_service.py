"""Initial NAV history service."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.app.nav_finality import NavWriteContext
from src.asset_utils import parse_date
from src.time_utils import bj_today


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


class NavInitializationService:
    """Initialize the first NAV fact for an account with existing holdings."""

    def __init__(self, *, account: str, storage: Any, portfolio: Any, read_service: Any):
        self.account = account
        self.storage = storage
        self.portfolio = portfolio
        self.read_service = read_service

    def init_nav_history(
        self,
        *,
        date_str: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        use_bulk_persist: bool = False,
    ) -> Dict[str, Any]:
        """Create the first `nav_history` row for an empty account."""
        try:
            nav_date = parse_date(date_str) if date_str else bj_today()

            if (not dry_run) and (not confirm):
                return {
                    "success": False,
                    "error": "Refuse to initialize nav_history without confirm=True (safety guard).",
                    "account": self.account,
                    "date": nav_date.isoformat(),
                    "dry_run": dry_run,
                    "confirm": confirm,
                }

            existing_navs = self.storage.get_nav_history(self.account, days=9999)
            if existing_navs:
                latest = max(existing_navs, key=lambda n: n.date)
                earliest = min(existing_navs, key=lambda n: n.date)
                return {
                    "success": False,
                    "error": "nav_history already exists; initialization is only for empty accounts.",
                    "account": self.account,
                    "existing_count": len(existing_navs),
                    "earliest_date": earliest.date.isoformat(),
                    "latest_date": latest.date.isoformat(),
                    "dry_run": dry_run,
                }

            snapshot = self.read_service.build_snapshot(price_timeout_seconds=price_timeout)
            valuation = snapshot["valuation"]
            if valuation.total_value_cny <= 0:
                return {
                    "success": False,
                    "error": "Cannot initialize nav_history with non-positive total_value.",
                    "account": self.account,
                    "date": nav_date.isoformat(),
                    "total_value": valuation.total_value_cny,
                    "warnings": valuation.warnings,
                }

            nav_record = self.portfolio.record_nav(
                self.account,
                valuation=valuation,
                nav_date=nav_date,
                persist=True,
                overwrite_existing=False,
                dry_run=dry_run,
                use_bulk_persist=use_bulk_persist,
                nav_write_context=NavWriteContext(
                    status="initial",
                    writer="init-nav",
                    write_reason="nav_history_initialization",
                    nav_date=nav_date,
                    valuation_as_of=snapshot.get("snapshot_time"),
                ),
            )

            result = {
                "success": True,
                "account": self.account,
                "date": nav_date.isoformat(),
                "dry_run": dry_run,
                "nav": nav_record.nav,
                "shares": nav_record.shares,
                "total_value": nav_record.total_value,
                "cash_value": nav_record.cash_value,
                "stock_value": nav_record.stock_value,
                "fund_value": nav_record.fund_value,
                "snapshot_time": snapshot.get("snapshot_time"),
                "message": (
                    f"已演练初始化 {self.account} 的 nav_history: {nav_record.nav:.4f}"
                    if dry_run
                    else f"已初始化 {self.account} 的 nav_history: {nav_record.nav:.4f}"
                ),
            }
            if valuation.warnings:
                result["warnings"] = valuation.warnings
            failure = _snapshot_failure(nav_record)
            if failure:
                result.update(failure)
                result["success"] = False
                result["status"] = "failed" if dry_run else "partial"
                result["error"] = failure["snapshot_error"]
                result["message"] = (
                    f"初始化已演练，但 holdings_snapshot 写入校验失败: {failure['snapshot_error']}"
                    if dry_run
                    else f"nav_history 已初始化，但 holdings_snapshot 写入失败: {failure['snapshot_error']}"
                )
            return result
        except Exception as e:
            return {"success": False, "error": str(e), "account": self.account}
