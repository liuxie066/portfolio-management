"""Read-only portfolio report query service."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from src import config
from src.app.nav_payload import format_nav_payload
from src.app.nav_preview_service import NavPreviewService
from src.domain.nav.performance import (
    calc_month_return,
    calc_risk_metrics,
    calc_since_inception_return,
    calc_year_return,
    sort_navs,
)
from src.domain.report.holdings_projection import merge_top_holdings
from src.time_utils import bj_now_naive, bj_today


class ReportQueryService:
    """Build read-only full-report projections from one valuation snapshot."""

    def __init__(self, *, account: str, storage: Any, portfolio: Any, read_service: Optional[Any] = None):
        self.account = account
        self.storage = storage
        self.portfolio = portfolio
        self.read_service = read_service

    def full_report(
        self,
        *,
        price_timeout: int = 30,
        snapshot: Optional[Dict[str, Any]] = None,
        navs: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Generate a read-only full report.

        Full-report queries may preview today's NAV from the live valuation when
        today's NAV fact has not been recorded yet. The preview is not used by
        daily NAV write/report workflows.
        """
        try:
            snapshot = snapshot or self._build_snapshot(price_timeout)
            valuation = snapshot["valuation"]
            holdings_data = snapshot["holdings_data"]
            position_data = snapshot["position_data"]

            all_navs = sort_navs(navs if navs is not None else self.storage.get_nav_history(self.account, days=9999))

            today = bj_today()
            live_total = valuation.total_value_cny
            live_cash = valuation.cash_value_cny
            live_stock = valuation.stock_value_cny + valuation.fund_value_cny

            working_navs = [nav for nav in all_navs if nav.date < today]
            today_nav = self._latest_nav_on(all_navs, today)
            if today_nav is not None:
                working_navs.append(today_nav)
            elif working_navs and live_total > 0:
                synthetic_nav = NavPreviewService(
                    account=self.account,
                    portfolio=self.portfolio,
                    start_year=config.get_start_year(),
                ).build(
                    today=today,
                    history_navs=working_navs,
                    valuation=valuation,
                    live_total=live_total,
                    live_cash=live_cash,
                    live_stock=live_stock,
                )
                if synthetic_nav is not None:
                    working_navs.append(synthetic_nav)

            nav_latest = format_nav_payload(working_navs[-1]) if working_navs else None

            hist_volatility, hist_max_dd = calc_risk_metrics(all_navs)

            distribution_data = self._get_distribution(holdings_data)
            distribution_result = distribution_data.get("by_type", []) if distribution_data.get("success") else []

            current_year = str(today.year)
            current_month = today.strftime("%Y-%m")

            return {
                "success": True,
                "generated_at": bj_now_naive().isoformat(),
                "overview": {
                    "total_value": holdings_data.get("total_value", 0),
                    "cash_ratio": position_data.get("cash_ratio", 0),
                    "stock_ratio": position_data.get("stock_ratio", 0),
                    "fund_ratio": position_data.get("fund_ratio", 0),
                },
                "nav": nav_latest,
                "returns": {
                    "monthly": calc_month_return(current_month, navs=working_navs),
                    "yearly": calc_year_return(current_year, navs=working_navs),
                    "since_inception": calc_since_inception_return(
                        navs=working_navs,
                        start_year=config.get_start_year(),
                    ),
                    "historical_volatility": hist_volatility,
                    "max_drawdown": hist_max_dd,
                },
                "top_holdings": merge_top_holdings(
                    holdings=holdings_data.get("holdings", []),
                    total_value=holdings_data.get("total_value", 0) or 0,
                    top_n=10,
                ),
                "distribution": distribution_result,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _build_snapshot(self, price_timeout: int) -> Dict[str, Any]:
        if self.read_service is None:
            raise RuntimeError("read_service is required when snapshot is not provided")
        try:
            return self.read_service.build_snapshot(price_timeout_seconds=price_timeout)
        except TypeError:
            return self.read_service.build_snapshot()

    def _get_distribution(self, holdings_data: Dict[str, Any]) -> Dict[str, Any]:
        if self.read_service is None:
            return {"success": False, "error": "read_service is required to build distribution"}
        return self.read_service.get_distribution(holdings_data=holdings_data)

    @staticmethod
    def _latest_nav_on(navs: list, target_date: date):
        matches = [nav for nav in navs if nav.date == target_date]
        return matches[-1] if matches else None
