"""Shared NAV payload formatting helpers."""
from __future__ import annotations

from typing import Any, Dict


NAV_DETAIL_EXPORT_PREFIXES = ("nav_change_", "appreciation_", "cash_flow_")
NAV_DETAIL_EXPORT_KEYS = (
    "cumulative_appreciation",
    "cumulative_nav_change",
    "year_cash_flow",
    "initial_value",
    "cagr",
    "cagr_pct",
)


def _iso_date(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def format_nav_payload(nav: Any) -> Dict[str, Any]:
    """Format a NAVHistory-like object for read/report payloads."""
    payload = {
        "date": _iso_date(getattr(nav, "date", None)),
        "nav": getattr(nav, "nav", None),
        "shares": getattr(nav, "shares", None),
        "total_value": getattr(nav, "total_value", None),
        "stock_value": getattr(nav, "stock_value", None),
        "cash_value": getattr(nav, "cash_value", None),
        "stock_weight": getattr(nav, "stock_weight", None),
        "cash_weight": getattr(nav, "cash_weight", None),
        "cash_flow": getattr(nav, "cash_flow", None),
        "share_change": getattr(nav, "share_change", None),
        "pnl": getattr(nav, "pnl", None),
        "mtd_nav_change": getattr(nav, "mtd_nav_change", None),
        "ytd_nav_change": getattr(nav, "ytd_nav_change", None),
        "mtd_pnl": getattr(nav, "mtd_pnl", None),
        "ytd_pnl": getattr(nav, "ytd_pnl", None),
    }

    details = getattr(nav, "details", None)
    if details:
        payload["details"] = details
        for key, value in details.items():
            if key.startswith(NAV_DETAIL_EXPORT_PREFIXES) and key not in payload:
                payload[key] = value
        for key in NAV_DETAIL_EXPORT_KEYS:
            if key in details:
                payload[key] = details[key]

    return payload


def format_nav_history_item(nav: Any) -> Dict[str, Any]:
    return {
        "date": _iso_date(getattr(nav, "date", None)),
        "nav": getattr(nav, "nav", None),
        "share_change": getattr(nav, "share_change", None),
    }
