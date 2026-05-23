"""NAV read-model service."""
from __future__ import annotations

from typing import Any, Dict


class NavReadService:
    def __init__(self, *, storage: Any):
        self.storage = storage

    def get_nav(self, *, account: str, days: int = 30) -> Dict[str, Any]:
        try:
            navs = self.storage.get_nav_history(account, days=days)
            if not navs:
                return {"success": False, "message": "无净值记录"}

            latest = navs[-1]
            return {
                "success": True,
                "latest": self._format_latest(latest),
                "history": [self._format_history_item(nav) for nav in navs],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    def _format_latest(nav: Any) -> Dict[str, Any]:
        latest = {
            "date": nav.date.isoformat(),
            "nav": nav.nav,
            "shares": nav.shares,
            "total_value": nav.total_value,
            "stock_value": nav.stock_value,
            "cash_value": nav.cash_value,
            "stock_weight": nav.stock_weight,
            "cash_weight": nav.cash_weight,
            "cash_flow": nav.cash_flow,
            "share_change": nav.share_change,
            "mtd_nav_change": nav.mtd_nav_change,
            "ytd_nav_change": nav.ytd_nav_change,
            "mtd_pnl": nav.mtd_pnl,
            "ytd_pnl": nav.ytd_pnl,
        }

        details = getattr(nav, "details", None)
        if details:
            latest["details"] = details
            for key, value in details.items():
                if key.startswith(("nav_change_", "appreciation_", "cash_flow_")) and key not in latest:
                    latest[key] = value
            for key in ("cumulative_appreciation", "cumulative_nav_change", "year_cash_flow", "initial_value"):
                if key in details:
                    latest[key] = details[key]
        return latest

    @staticmethod
    def _format_history_item(nav: Any) -> Dict[str, Any]:
        return {
            "date": nav.date.isoformat(),
            "nav": nav.nav,
            "share_change": nav.share_change,
        }
