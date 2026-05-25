"""Pure NAV performance calculations."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Iterable, List, Tuple


def sort_navs(navs: Iterable[Any]) -> List[Any]:
    return sorted(list(navs or []), key=lambda nav: nav.date or date.min)


def calc_risk_metrics(navs: Iterable[Any]) -> Tuple[float, float]:
    navs_sorted = sort_navs(navs)
    if len(navs_sorted) < 2:
        return 0, 0

    valid_navs = [nav for nav in navs_sorted if nav.nav and nav.nav > 0]
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


def calc_month_return(month: str, *, navs: Iterable[Any]) -> Dict[str, Any]:
    navs_sorted = sort_navs(navs)
    month_navs = [nav for nav in navs_sorted if nav.date.strftime("%Y-%m") == month]
    if len(month_navs) < 1:
        return {"success": False, "message": f"{month} 数据不足"}

    end_nav = max(month_navs, key=lambda nav: nav.date)
    year, mon = int(month[:4]), int(month[5:7])
    prev_month = f"{year - 1}-12" if mon == 1 else f"{year}-{mon - 1:02d}"
    prev_month_navs = [nav for nav in navs_sorted if nav.date.strftime("%Y-%m") == prev_month]
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


def calc_year_return(year: str, *, navs: Iterable[Any]) -> Dict[str, Any]:
    navs_sorted = sort_navs(navs)
    year_navs = [nav for nav in navs_sorted if nav.date.strftime("%Y") == year]
    if len(year_navs) < 1:
        return {"success": False, "message": f"{year} 数据不足"}

    end_nav = max(year_navs, key=lambda nav: nav.date)
    prev_year = str(int(year) - 1)
    prev_year_navs = [nav for nav in navs_sorted if nav.date.strftime("%Y") == prev_year]
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


def calc_since_inception_return(*, navs: Iterable[Any], start_year: int) -> Dict[str, Any]:
    navs_sorted = sort_navs(navs)
    base_date = date(start_year - 1, 12, 31)

    base_candidates = [nav for nav in navs_sorted if nav.date <= base_date]
    base_nav = max(base_candidates, key=lambda nav: nav.date) if base_candidates else None
    latest = navs_sorted[-1] if navs_sorted else None

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


@dataclass(frozen=True)
class NavPerformanceCalculator:
    start_year: int

    def month_return(self, month: str, *, navs: Iterable[Any]) -> Dict[str, Any]:
        return calc_month_return(month, navs=navs)

    def year_return(self, year: str, *, navs: Iterable[Any]) -> Dict[str, Any]:
        return calc_year_return(year, navs=navs)

    def since_inception_return(self, *, navs: Iterable[Any]) -> Dict[str, Any]:
        return calc_since_inception_return(navs=navs, start_year=self.start_year)

    def risk_metrics(self, navs: Iterable[Any]) -> Tuple[float, float]:
        return calc_risk_metrics(navs)
