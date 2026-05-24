"""Full portfolio report application service."""
from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any, Dict, Optional

from src import config
from src.domain.nav_calculator import NavCalculator
from src.models import NAVHistory
from src.app.nav_payload import format_nav_payload
from src.reporting_utils import is_cash_like
from src.time_utils import bj_now_naive, bj_today


class FullReportService:
    """Build the read-only full report from one valuation snapshot."""

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
        """Generate the read-only full report.

        A synthetic "today" NAV is composed from the live valuation and the
        latest recorded shares so return metrics remain useful before a daily
        NAV record is written.
        """
        try:
            snapshot = snapshot or self._build_snapshot(price_timeout)
            valuation = snapshot["valuation"]
            holdings_data = snapshot["holdings_data"]
            position_data = snapshot["position_data"]

            all_navs = self._sort_navs(navs if navs is not None else self.storage.get_nav_history(self.account, days=9999))

            today = bj_today()
            live_total = valuation.total_value_cny
            live_cash = valuation.cash_value_cny
            live_stock = valuation.stock_value_cny + valuation.fund_value_cny

            working_navs = [nav for nav in all_navs if nav.date < today]
            today_nav = self._latest_nav_on(all_navs, today)
            if today_nav is not None:
                working_navs.append(today_nav)
            elif working_navs and live_total > 0:
                synthetic_nav = self._build_synthetic_nav(
                    today=today,
                    all_navs=working_navs,
                    valuation=valuation,
                    live_total=live_total,
                    live_cash=live_cash,
                    live_stock=live_stock,
                )
                if synthetic_nav is not None:
                    working_navs.append(synthetic_nav)

            nav_latest = self._format_latest_nav(working_navs[-1]) if working_navs else None

            hist_volatility, hist_max_dd = self.calc_risk_metrics(all_navs)

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
                    "monthly": self._calc_month_return(current_month, navs=working_navs),
                    "yearly": self._calc_year_return(current_year, navs=working_navs),
                    "since_inception": self._calc_since_inception_return(navs=working_navs),
                    "historical_volatility": hist_volatility,
                    "max_drawdown": hist_max_dd,
                },
                "top_holdings": self.merge_daily_top_holdings(
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
    def _sort_navs(navs: list) -> list:
        return sorted(list(navs or []), key=lambda nav: nav.date or date.min)

    @staticmethod
    def _latest_nav_on(navs: list, target_date: date):
        matches = [nav for nav in navs if nav.date == target_date]
        return matches[-1] if matches else None

    def _build_synthetic_nav(
        self,
        *,
        today: date,
        all_navs: list,
        valuation: Any,
        live_total: float,
        live_cash: float,
        live_stock: float,
    ) -> Optional[NAVHistory]:
        history_navs = [nav for nav in self._sort_navs(all_navs) if nav.date < today]
        if not history_navs:
            return None

        last_nav = self.portfolio._find_latest_nav_before(history_navs, today) or history_navs[-1]
        if not last_nav.shares or last_nav.shares <= 0:
            return None

        current_year = str(today.year)
        start_year = config.get_start_year()
        yesterday_nav = last_nav
        prev_year_end_nav = self.portfolio._find_year_end_nav(history_navs, str(today.year - 1))
        prev_month_end_nav = self.portfolio._find_prev_month_end_nav(history_navs, today.year, today.month)

        yearly_data = {}
        for yr in range(start_year, today.year + 1):
            yr_str = str(yr)
            yearly_data[yr_str] = {
                "prev_end": self.portfolio._find_year_end_nav(history_navs, str(yr - 1)),
                "end": self.portfolio._find_year_end_nav(history_navs, yr_str),
            }

        cash_flow_summary = self._summarize_cash_flows_for_synthetic(
            today=today,
            start_year=start_year,
            last_nav=last_nav,
        )
        daily_cash_flow = cash_flow_summary["daily"]
        monthly_cash_flow = cash_flow_summary["monthly"]
        yearly_cash_flow = cash_flow_summary["yearly"].get(current_year, 0.0)
        for yr_str, yd in yearly_data.items():
            yd["cash_flow"] = cash_flow_summary["yearly"].get(yr_str, 0.0)
        cumulative_cash_flow = cash_flow_summary["cumulative"]
        gap_cash_flow = cash_flow_summary["gap"]

        stock_ratio = live_stock / live_total if live_total > 0 else 0
        cash_ratio = live_cash / live_total if live_total > 0 else 0

        calc = self._calc_nav_metrics_for_synthetic(
            today=today,
            total_value=live_total,
            yesterday_nav=yesterday_nav,
            prev_year_end_nav=prev_year_end_nav,
            prev_month_end_nav=prev_month_end_nav,
            last_nav=last_nav,
            yearly_data=yearly_data,
            daily_cash_flow=daily_cash_flow,
            monthly_cash_flow=monthly_cash_flow,
            yearly_cash_flow=yearly_cash_flow,
            cumulative_cash_flow=cumulative_cash_flow,
            start_year=start_year,
            gap_cash_flow=gap_cash_flow,
            all_navs=history_navs,
        )

        nav_record = self._build_nav_record_for_synthetic(
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
            cumulative_cash_flow=cumulative_cash_flow,
            start_year=start_year,
            **calc,
        )
        details = dict(nav_record.details or {})
        details["is_synthetic"] = True
        nav_record.details = details
        return nav_record

    def _summarize_cash_flows_for_synthetic(self, *, today: date, start_year: int, last_nav: NAVHistory) -> dict:
        summarize = getattr(self.portfolio, "_summarize_cash_flows", None)
        if callable(summarize):
            try:
                return summarize(account=self.account, today=today, start_year=start_year, last_nav=last_nav)
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
            cumulative = cumulative_func(self.account, str(start_year), today)
        else:
            cumulative = self.portfolio._get_period_cash_flow(self.account, date(start_year, 1, 1), today)
        return {
            "daily": daily,
            "monthly": monthly,
            "yearly": yearly,
            "cumulative": cumulative,
            "gap": gap,
        }

    def _calc_nav_metrics_for_synthetic(self, **kwargs) -> dict:
        calc = getattr(self.portfolio, "_calc_nav_metrics", None)
        if callable(calc):
            return calc(account=self.account, **kwargs)

        all_navs = kwargs.pop("all_navs", None)
        initial_value = None
        get_initial_value = getattr(self.portfolio, "_get_initial_value", None)
        if callable(get_initial_value):
            initial_value = get_initial_value(self.account, all_navs=all_navs)
        return NavCalculator.calc_nav_metrics(initial_value=initial_value, **kwargs)

    def _build_nav_record_for_synthetic(self, *, date: date, **kwargs) -> NAVHistory:
        build = getattr(self.portfolio, "_build_nav_record", None)
        if callable(build):
            return build(today=date, **kwargs)
        return NavCalculator.build_nav_record(today=date, **kwargs)

    @staticmethod
    def _format_latest_nav(nav: Any) -> Dict[str, Any]:
        return format_nav_payload(nav)

    @staticmethod
    def calc_risk_metrics(navs: list) -> tuple:
        if len(navs) < 2:
            return 0, 0

        valid_navs = [nav for nav in navs if nav.nav and nav.nav > 0]
        if len(valid_navs) < 2:
            return 0, 0

        returns = []
        for idx in range(1, len(valid_navs)):
            returns.append((valid_navs[idx].nav - valid_navs[idx - 1].nav) / valid_navs[idx - 1].nav)

        volatility = statistics.stdev(returns) * (252 ** 0.5) * 100 if len(returns) > 1 else 0

        max_dd = 0
        peak = valid_navs[0].nav
        for nav in valid_navs[1:]:
            if nav.nav > peak:
                peak = nav.nav
            drawdown = (peak - nav.nav) / peak
            if drawdown > max_dd:
                max_dd = drawdown

        return volatility, max_dd * 100

    def _calc_month_return(self, month: str, *, navs: list) -> Dict[str, Any]:
        month_navs = [nav for nav in navs if nav.date.strftime("%Y-%m") == month]
        if len(month_navs) < 1:
            return {"success": False, "message": f"{month} 数据不足"}

        end_nav = max(month_navs, key=lambda nav: nav.date)
        year, mon = int(month[:4]), int(month[5:7])
        prev_month = f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"
        prev_month_navs = [nav for nav in navs if nav.date.strftime("%Y-%m") == prev_month]
        if prev_month_navs:
            start_nav = max(prev_month_navs, key=lambda nav: nav.date)
            start_nav_label = "上月末"
        else:
            start_nav = min(month_navs, key=lambda nav: nav.date)
            start_nav_label = "月初"

        ret = (end_nav.nav - start_nav.nav) / start_nav.nav * 100 if start_nav.nav > 0 else 0
        return {
            "success": True,
            "period": month,
            "return_pct": ret,
            "start_nav": start_nav.nav,
            "end_nav": end_nav.nav,
            "start_date": start_nav.date.isoformat(),
            "end_date": end_nav.date.isoformat(),
            "base": start_nav_label,
        }

    def _calc_year_return(self, year: str, *, navs: list) -> Dict[str, Any]:
        year_navs = [nav for nav in navs if nav.date.strftime("%Y") == year]
        if len(year_navs) < 1:
            return {"success": False, "message": f"{year} 数据不足"}

        end_nav = max(year_navs, key=lambda nav: nav.date)
        prev_year = str(int(year) - 1)
        prev_year_navs = [nav for nav in navs if nav.date.strftime("%Y") == prev_year]
        if prev_year_navs:
            start_nav = max(prev_year_navs, key=lambda nav: nav.date)
            start_nav_label = "上年末"
        else:
            start_nav = min(year_navs, key=lambda nav: nav.date)
            start_nav_label = "年初"

        ret = (end_nav.nav - start_nav.nav) / start_nav.nav * 100 if start_nav.nav > 0 else 0
        return {
            "success": True,
            "period": year,
            "return_pct": ret,
            "start_nav": start_nav.nav,
            "end_nav": end_nav.nav,
            "start_date": start_nav.date.isoformat(),
            "end_date": end_nav.date.isoformat(),
            "base": start_nav_label,
        }

    def _calc_since_inception_return(self, *, navs: list) -> Dict[str, Any]:
        start_year = config.get_start_year()
        base_date = date(start_year - 1, 12, 31)

        base_candidates = [nav for nav in navs if nav.date <= base_date]
        base_nav = max(base_candidates, key=lambda nav: nav.date) if base_candidates else None
        latest = navs[-1] if navs else None

        if not base_nav or not latest:
            return {"success": False, "message": "数据不足"}

        actual_start_nav = base_nav.nav
        actual_latest_nav = latest.nav
        if not actual_start_nav or actual_start_nav <= 0:
            return {"success": False, "message": "基准净值无效"}

        normalized_nav = actual_latest_nav / actual_start_nav
        total_ret = (normalized_nav - 1.0) * 100
        days = (latest.date - base_date).days
        years = days / 365.25
        cagr = ((normalized_nav) ** (1 / years) - 1) * 100 if years > 0 else 0

        return {
            "success": True,
            "period": f"{start_year}至今",
            "return_pct": total_ret,
            "total_return_pct": total_ret,
            "cagr": cagr,
            "cagr_pct": cagr,
            "days": days,
            "start_nav": 1.0,
            "start_date": base_date.isoformat(),
            "latest_nav": round(normalized_nav, 4),
            "actual_start_nav": actual_start_nav,
            "actual_latest_nav": actual_latest_nav,
            "base": f"{start_year - 1}年末",
        }

    @staticmethod
    def merge_daily_top_holdings(holdings: list, total_value: float, top_n: int = 10) -> list:
        if not holdings:
            return []

        merged_by_code: Dict[str, Dict[str, Any]] = {}
        cash_bucket: Dict[str, Any] = {
            "code": "CASH+MMF",
            "name": "现金及货基",
            "quantity": 0.0,
            "type": "cash",
            "normalized_type": "cash",
            "broker": "多券商汇总",
            "currency": "MIXED",
            "price": None,
            "cny_price": None,
            "market_value": 0.0,
            "weight": 0.0,
            "_parts": set(),
        }

        for holding in holdings:
            code = str(holding.get("code") or "").strip()
            if not code:
                continue

            normalized_type = holding.get("normalized_type")
            raw_type = holding.get("type")
            market_value = float(holding.get("market_value") or 0.0)
            quantity = float(holding.get("quantity") or 0.0)

            if normalized_type == "cash" or is_cash_like(raw_type, code):
                cash_bucket["quantity"] += quantity
                cash_bucket["market_value"] += market_value
                cash_bucket["_parts"].add(code)
                continue

            key = code.upper()
            if key not in merged_by_code:
                merged_by_code[key] = {
                    "code": code,
                    "name": holding.get("name"),
                    "quantity": quantity,
                    "type": raw_type,
                    "normalized_type": normalized_type,
                    "broker": "多券商汇总",
                    "currency": holding.get("currency") or "MIXED",
                    "price": None,
                    "cny_price": None,
                    "market_value": market_value,
                    "weight": 0.0,
                    "_parts": {code},
                }
            else:
                row = merged_by_code[key]
                row["quantity"] += quantity
                row["market_value"] += market_value
                row["_parts"].add(code)
                if row.get("currency") != (holding.get("currency") or "MIXED"):
                    row["currency"] = "MIXED"

        merged_rows = list(merged_by_code.values())
        if cash_bucket["_parts"]:
            cash_bucket["code"] = "CASH+MMF"
            cash_bucket["name"] = "现金及货基(合并)"
            merged_rows.append(cash_bucket)

        for row in merged_rows:
            row.pop("_parts", None)
            market_value = float(row.get("market_value") or 0.0)
            row["weight"] = (market_value / total_value) if total_value > 0 else 0.0

        merged_rows.sort(key=lambda row: float(row.get("market_value") or 0.0), reverse=True)
        return merged_rows[:top_n]
