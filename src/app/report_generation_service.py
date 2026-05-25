"""Report payload assembly application service."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src.app.nav_payload import format_nav_payload


class ReportGenerationService:
    """Build daily/monthly/yearly report payloads from a full report."""

    def __init__(
        self,
        *,
        build_snapshot_func: Callable[..., Dict[str, Any]],
        full_report_func: Callable[..., Dict[str, Any]],
    ):
        self.build_snapshot_func = build_snapshot_func
        self.full_report_func = full_report_func

    def generate_report(
        self,
        *,
        report_type: str = "daily",
        price_timeout: int = 30,
        snapshot: Optional[Dict[str, Any]] = None,
        navs: Optional[list] = None,
        nav_override: Optional[Any] = None,
    ) -> Dict[str, Any]:
        snapshot = snapshot or self._build_snapshot(price_timeout)
        full = self.full_report_func(price_timeout=price_timeout, snapshot=snapshot, navs=navs)
        if not full.get("success"):
            return full

        nav = self._normalize_nav_override(nav_override) or full.get("nav") or {}
        nav_details = nav.get("details") or {}
        returns = full.get("returns") or {}
        since_inception = returns.get("since_inception") or {}
        cagr_value = nav_details.get("cagr")
        cagr_pct_value = nav_details.get("cagr_pct")
        if cagr_value is None and since_inception.get("success"):
            cagr_pct_value = since_inception.get("cagr_pct")
            cagr_value = (cagr_pct_value / 100) if cagr_pct_value is not None else since_inception.get("cagr")

        report_warnings = list(full.get("warnings") or [])

        if report_type == "daily":
            return {
                "success": True,
                "snapshot_time": snapshot.get("snapshot_time"),
                "report_type": "日报",
                "date": nav.get("date"),
                "overview": full["overview"],
                "nav": nav.get("nav"),
                "total_value": nav.get("total_value"),
                "cash_flow": nav.get("cash_flow"),
                "pnl": nav.get("pnl"),
                "mtd_nav_change": nav.get("mtd_nav_change"),
                "ytd_nav_change": nav.get("ytd_nav_change"),
                "mtd_pnl": nav.get("mtd_pnl"),
                "ytd_pnl": nav.get("ytd_pnl"),
                "top_holdings": full.get("top_holdings"),
                "cagr": cagr_value,
                "cagr_pct": cagr_pct_value,
                "warnings": report_warnings,
            }

        if report_type == "monthly":
            return {
                "success": True,
                "snapshot_time": snapshot.get("snapshot_time"),
                "report_type": "月报",
                "date": nav.get("date"),
                "overview": full["overview"],
                "nav": nav.get("nav"),
                "total_value": nav.get("total_value"),
                "monthly_return": returns.get("monthly"),
                "mtd_nav_change": nav.get("mtd_nav_change"),
                "mtd_pnl": nav.get("mtd_pnl"),
                "top_holdings": full.get("top_holdings"),
                "distribution": full.get("distribution"),
                "cagr": cagr_value,
                "cagr_pct": cagr_pct_value,
            }

        if report_type == "yearly":
            yearly_breakdown = {}
            for key, value in nav_details.items():
                if key.startswith(("nav_change_", "appreciation_", "cash_flow_")):
                    yearly_breakdown[key] = value

            return {
                "success": True,
                "snapshot_time": snapshot.get("snapshot_time"),
                "report_type": "年报",
                "date": nav.get("date"),
                "overview": full["overview"],
                "nav": nav.get("nav"),
                "total_value": nav.get("total_value"),
                "yearly_return": returns.get("yearly"),
                "ytd_nav_change": nav.get("ytd_nav_change"),
                "ytd_pnl": nav.get("ytd_pnl"),
                "since_inception": returns.get("since_inception"),
                "risk": {
                    "volatility": returns.get("historical_volatility"),
                    "max_drawdown": returns.get("max_drawdown"),
                },
                "yearly_breakdown": yearly_breakdown,
                "cumulative_nav_change": nav_details.get("cumulative_nav_change"),
                "cumulative_appreciation": nav_details.get("cumulative_appreciation"),
                "top_holdings": full.get("top_holdings"),
                "distribution": full.get("distribution"),
            }

        return {"success": False, "error": f"不支持的报告类型: {report_type}，可选: daily/monthly/yearly"}

    @staticmethod
    def _normalize_nav_override(nav_override: Optional[Any]) -> Optional[Dict[str, Any]]:
        if not nav_override:
            return None
        if isinstance(nav_override, dict):
            latest = nav_override.get("latest")
            if isinstance(latest, dict):
                return dict(latest)
            return dict(nav_override)
        return format_nav_payload(nav_override)

    def _build_snapshot(self, price_timeout: int) -> Dict[str, Any]:
        try:
            return self.build_snapshot_func(price_timeout_seconds=price_timeout)
        except TypeError:
            return self.build_snapshot_func()
