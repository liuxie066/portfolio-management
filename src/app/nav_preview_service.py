"""Synthetic NAV preview for read-only report queries."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

from src.domain.nav_calculator import NavCalculator
from src.models import NAVHistory


class NavPreviewService:
    """Build a non-persisted NAV point from live valuation and prior history."""

    def __init__(self, *, account: str, portfolio: Any, start_year: int):
        self.account = account
        self.portfolio = portfolio
        self.start_year = start_year

    def build(
        self,
        *,
        today: date,
        history_navs: list,
        valuation: Any,
        live_total: float,
        live_cash: float,
        live_stock: float,
    ) -> Optional[NAVHistory]:
        history_navs = sorted([nav for nav in list(history_navs or []) if nav.date < today], key=lambda nav: nav.date)
        if not history_navs:
            return None

        last_nav = self.portfolio._find_latest_nav_before(history_navs, today) or history_navs[-1]
        if not last_nav.shares or last_nav.shares <= 0:
            return None

        current_year = str(today.year)
        build_lookup = getattr(self.portfolio, "_build_nav_lookup", None)
        nav_index = build_lookup(history_navs) if callable(build_lookup) else None
        prev_year_end_nav = self.portfolio._find_year_end_nav(history_navs, str(today.year - 1))
        prev_month_end_nav = self.portfolio._find_prev_month_end_nav(history_navs, today.year, today.month)
        find_mtd_base = getattr(self.portfolio, "_find_mtd_return_base_nav", None)
        find_ytd_base = getattr(self.portfolio, "_find_ytd_return_base_nav", None)
        mtd_return_base_nav = (
            find_mtd_base(history_navs, today, nav_index=nav_index)
            if callable(find_mtd_base)
            else prev_month_end_nav
        )
        ytd_return_base_nav = (
            find_ytd_base(history_navs, today, nav_index=nav_index)
            if callable(find_ytd_base)
            else prev_year_end_nav
        )

        yearly_data = {}
        for yr in range(self.start_year, today.year + 1):
            yr_str = str(yr)
            yearly_data[yr_str] = {
                "prev_end": self.portfolio._find_year_end_nav(history_navs, str(yr - 1)),
                "end": self.portfolio._find_year_end_nav(history_navs, yr_str),
            }

        cash_flow_summary = self._summarize_cash_flows(today=today, last_nav=last_nav)
        daily_cash_flow = cash_flow_summary["daily"]
        monthly_cash_flow = cash_flow_summary["monthly"]
        yearly_cash_flow = cash_flow_summary["yearly"].get(current_year, 0.0)
        for yr_str, yd in yearly_data.items():
            yd["cash_flow"] = cash_flow_summary["yearly"].get(yr_str, 0.0)

        stock_ratio = live_stock / live_total if live_total > 0 else 0
        cash_ratio = live_cash / live_total if live_total > 0 else 0

        calc = self._calc_nav_metrics(
            today=today,
            total_value=live_total,
            yesterday_nav=last_nav,
            prev_year_end_nav=prev_year_end_nav,
            prev_month_end_nav=prev_month_end_nav,
            mtd_return_base_nav=mtd_return_base_nav,
            ytd_return_base_nav=ytd_return_base_nav,
            last_nav=last_nav,
            yearly_data=yearly_data,
            daily_cash_flow=daily_cash_flow,
            monthly_cash_flow=monthly_cash_flow,
            yearly_cash_flow=yearly_cash_flow,
            cumulative_cash_flow=cash_flow_summary["cumulative"],
            start_year=self.start_year,
            gap_cash_flow=cash_flow_summary["gap"],
            all_navs=history_navs,
        )

        nav_record = self._build_nav_record(
            date=today,
            account=self.account,
            valuation=valuation,
            stock_value=live_stock,
            cash_value=live_cash,
            total_value=live_total,
            stock_ratio=stock_ratio,
            cash_ratio=cash_ratio,
            daily_cash_flow=daily_cash_flow,
            monthly_cash_flow=monthly_cash_flow,
            yearly_cash_flow=yearly_cash_flow,
            yearly_data=yearly_data,
            cumulative_cash_flow=cash_flow_summary["cumulative"],
            start_year=self.start_year,
            **calc,
        )
        details = dict(nav_record.details or {})
        details["is_synthetic"] = True
        nav_record.details = details
        return nav_record

    def _summarize_cash_flows(self, *, today: date, last_nav: NAVHistory) -> dict:
        summarize = getattr(self.portfolio, "_summarize_cash_flows", None)
        if callable(summarize):
            try:
                return summarize(account=self.account, today=today, start_year=self.start_year, last_nav=last_nav)
            except Exception:
                pass

        current_year = today.strftime("%Y")
        daily = self.portfolio._get_daily_cash_flow(self.account, today)
        monthly = self.portfolio._get_monthly_cash_flow(self.account, today.year, today.month)
        yearly = {current_year: self.portfolio._get_yearly_cash_flow(self.account, current_year)}
        gap_start = last_nav.date + timedelta(days=1)
        gap = self.portfolio._get_period_cash_flow(self.account, gap_start, today)
        cumulative_func = getattr(self.portfolio, "_get_cumulative_cash_flow_from_year", None)
        if callable(cumulative_func):
            cumulative = cumulative_func(self.account, str(self.start_year), today)
        else:
            cumulative = self.portfolio._get_period_cash_flow(self.account, date(self.start_year, 1, 1), today)
        return {
            "daily": daily,
            "monthly": monthly,
            "yearly": yearly,
            "cumulative": cumulative,
            "gap": gap,
        }

    def _calc_nav_metrics(self, **kwargs) -> dict:
        calc = getattr(self.portfolio, "_calc_nav_metrics", None)
        if callable(calc):
            return calc(account=self.account, **kwargs)

        all_navs = kwargs.pop("all_navs", None)
        initial_value = None
        get_initial_value = getattr(self.portfolio, "_get_initial_value", None)
        if callable(get_initial_value):
            initial_value = get_initial_value(self.account, all_navs=all_navs)
        return NavCalculator.calc_nav_metrics(initial_value=initial_value, **kwargs)

    def _build_nav_record(self, *, date: date, **kwargs) -> NAVHistory:
        build = getattr(self.portfolio, "_build_nav_record", None)
        if callable(build):
            return build(today=date, **kwargs)
        return NavCalculator.build_nav_record(today=date, **kwargs)
