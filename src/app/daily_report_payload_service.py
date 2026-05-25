"""Daily report payload assembly from an already-recorded NAV fact."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src import config
from src.app.account_nav_recorder_service import _now_ms, _set_run_id
from src.app.nav_payload import format_nav_history_item, format_nav_payload
from src.domain.nav.performance import NavPerformanceCalculator, sort_navs
from src.domain.report.holdings_projection import merge_top_holdings


class DailyReportPayloadService:
    """Build distribution, report, and recent-NAV payload without writing NAV."""

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

    def build(
        self,
        *,
        snapshot: Dict[str, Any],
        nav_record: Any,
        nav_result: Dict[str, Any],
        price_timeout: int = 30,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_run_id = run_id or nav_result.get("run_id") or snapshot.get("run_id")

        t_navs = _now_ms()
        navs_all = self.storage.get_nav_history(self.account, days=9999)
        navs_ms = _now_ms() - t_navs

        distribution = self.read_service.get_distribution(holdings_data=snapshot)
        if not distribution.get("success"):
            return _set_run_id(distribution, resolved_run_id)

        t_report = _now_ms()
        report = self._build_daily_report(
            snapshot=snapshot,
            navs=navs_all,
            nav_record=nav_record,
        )
        report_ms = _now_ms() - t_report
        if not report.get("success"):
            return _set_run_id(report, resolved_run_id)
        report["run_id"] = resolved_run_id

        t_get_nav = _now_ms()
        nav_snapshot = self._build_nav_snapshot(navs_all, nav_record)
        get_nav_ms = _now_ms() - t_get_nav

        return {
            "success": True,
            "account": self.account,
            "run_id": resolved_run_id,
            "date": nav_result.get("date"),
            "distribution": distribution,
            "report": report,
            "nav_snapshot": nav_snapshot,
            "stage_timings": {
                "navs_all_ms": navs_ms,
                "generate_report_ms": report_ms,
                "get_nav_ms": get_nav_ms,
            },
        }

    def _build_daily_report(
        self,
        *,
        snapshot: Dict[str, Any],
        navs: list,
        nav_record: Any,
    ) -> Dict[str, Any]:
        holdings_data = snapshot.get("holdings_data") or {}
        nav = format_nav_payload(nav_record)
        nav_details = nav.get("details") or {}
        nav_date = getattr(nav_record, "date", None)
        if nav_date is None:
            return {"success": False, "error": "nav_record.date is required"}

        navs_for_returns = self._replace_nav_for_date(navs, nav_record)
        performance = NavPerformanceCalculator(start_year=config.get_start_year())
        current_year = str(nav_date.year)
        current_month = nav_date.strftime("%Y-%m")
        since_inception = performance.since_inception_return(navs=navs_for_returns)

        cagr_value = nav_details.get("cagr")
        cagr_pct_value = nav_details.get("cagr_pct")
        if cagr_value is None and since_inception.get("success"):
            cagr_pct_value = since_inception.get("cagr_pct")
            cagr_value = (cagr_pct_value / 100) if cagr_pct_value is not None else since_inception.get("cagr")

        valuation = snapshot.get("valuation")
        warnings = list(getattr(valuation, "warnings", None) or [])

        return {
            "success": True,
            "snapshot_time": snapshot.get("snapshot_time"),
            "report_type": "日报",
            "date": nav.get("date"),
            "overview": {
                "total_value": holdings_data.get("total_value", 0),
                "cash_ratio": (snapshot.get("position_data") or {}).get("cash_ratio", 0),
                "stock_ratio": (snapshot.get("position_data") or {}).get("stock_ratio", 0),
                "fund_ratio": (snapshot.get("position_data") or {}).get("fund_ratio", 0),
            },
            "nav": nav.get("nav"),
            "total_value": nav.get("total_value"),
            "cash_flow": nav.get("cash_flow"),
            "pnl": nav.get("pnl"),
            "mtd_nav_change": nav.get("mtd_nav_change"),
            "ytd_nav_change": nav.get("ytd_nav_change"),
            "mtd_pnl": nav.get("mtd_pnl"),
            "ytd_pnl": nav.get("ytd_pnl"),
            "top_holdings": merge_top_holdings(
                holdings=holdings_data.get("holdings", []),
                total_value=holdings_data.get("total_value", 0) or 0,
                top_n=10,
            ),
            "cagr": cagr_value,
            "cagr_pct": cagr_pct_value,
            "warnings": warnings,
            "returns": {
                "monthly": performance.month_return(current_month, navs=navs_for_returns),
                "yearly": performance.year_return(current_year, navs=navs_for_returns),
                "since_inception": since_inception,
            },
        }

    @classmethod
    def _build_nav_snapshot(cls, navs: list, nav_record: Any) -> Dict[str, Any]:
        navs_for_snapshot = cls._replace_nav_for_date(navs, nav_record)
        recent_navs = sort_navs(navs_for_snapshot)[-2:]
        if not recent_navs:
            return {"success": False, "message": "无净值记录"}

        latest = recent_navs[-1]
        return {
            "success": True,
            "latest": format_nav_payload(latest),
            "history": [format_nav_history_item(nav) for nav in recent_navs],
        }

    @staticmethod
    def _replace_nav_for_date(navs: list, nav_record: Any) -> list:
        target_date = getattr(nav_record, "date", None)
        replaced = False
        merged = []
        for nav in sort_navs(navs):
            if getattr(nav, "date", None) == target_date:
                if not replaced:
                    merged.append(nav_record)
                    replaced = True
                continue
            merged.append(nav)
        if not replaced:
            merged.append(nav_record)
        return sort_navs(merged)
