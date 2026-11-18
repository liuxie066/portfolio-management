"""Daily report payload assembly from an already-recorded NAV fact."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src import config
from src.app.account_nav_recorder_service import _now_ms, _set_run_id
from src.app.nav_payload import format_nav_history_item, format_nav_payload
from src.app.report_generation_service import build_daily_report
from src.domain.nav.performance import NavPerformanceCalculator, sort_navs
from src.domain.report.holdings_projection import report_holdings_fields


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
        if getattr(nav_record, "date", None) is None:
            return _set_run_id({"success": False, "error": "nav_record.date is required"}, resolved_run_id)
        navs_for_report = self._replace_nav_for_date(navs_all, nav_record)
        report = self._report_from_nav_record(
            snapshot=snapshot,
            navs=navs_for_report,
            nav_record=nav_record,
        )
        report_ms = _now_ms() - t_report
        if not report.get("success"):
            return _set_run_id(report, resolved_run_id)
        report["run_id"] = resolved_run_id

        t_get_nav = _now_ms()
        nav_snapshot = self._build_nav_snapshot(navs_for_report)
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

    def _report_from_nav_record(
        self,
        *,
        snapshot: Dict[str, Any],
        navs: list,
        nav_record: Any,
    ) -> Dict[str, Any]:
        holdings_data = snapshot.get("holdings_data") or {}
        nav = format_nav_payload(nav_record)
        nav_date = nav_record.date
        performance = NavPerformanceCalculator(start_year=config.get_start_year())
        current_year = str(nav_date.year)
        current_month = nav_date.strftime("%Y-%m")
        since_inception = performance.since_inception_return(navs=navs)

        position_data = snapshot.get("position_data") or {}
        report = build_daily_report(
            snapshot_time=snapshot.get("snapshot_time"),
            nav=nav,
            **report_holdings_fields(holdings_data, position_data),
            warnings=list(getattr(snapshot.get("valuation"), "warnings", None) or []),
            since_inception=since_inception,
        )
        report["returns"] = {
            "monthly": performance.month_return(current_month, navs=navs),
            "yearly": performance.year_return(current_year, navs=navs),
            "since_inception": since_inception,
        }
        return report

    @staticmethod
    def _build_nav_snapshot(navs: list) -> Dict[str, Any]:
        recent_navs = navs[-2:]
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
