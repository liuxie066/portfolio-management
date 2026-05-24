"""NAV read-model service."""
from __future__ import annotations

from typing import Any, Dict

from src.app.nav_payload import format_nav_history_item, format_nav_payload


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
        return format_nav_payload(nav)

    @staticmethod
    def _format_history_item(nav: Any) -> Dict[str, Any]:
        return format_nav_history_item(nav)
