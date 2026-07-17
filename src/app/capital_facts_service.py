"""Read-only capital facts for MTD/YTD asset bridges."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

from src.app.cash_flow_summary_service import CashFlowSummaryService
from src.time_utils import bj_today


_SCHEMA_VERSION = "portfolio.capital_facts.v1"
_MONEY = Decimal("0.01")
_RECONCILIATION_TOLERANCE = Decimal("0.05")


class CapitalFactsService:
    def __init__(
        self,
        *,
        storage: Any,
        cash_flow_summary_service: CashFlowSummaryService | None = None,
        today_fn: Callable[[], date] = bj_today,
    ):
        self.storage = storage
        self.cash_flows = cash_flow_summary_service or CashFlowSummaryService(storage)
        self.today_fn = today_fn

    def get(self, *, account: str, period: str, as_of_month: str) -> dict[str, Any]:
        account_value = str(account or "").strip()
        if not account_value:
            raise ValueError("account is required")
        period_value = str(period or "").strip().lower()
        if period_value not in {"mtd", "ytd"}:
            raise ValueError("period must be mtd or ytd")

        target_start = _parse_month(as_of_month)
        today = self.today_fn()
        if target_start > date(today.year, today.month, 1):
            return self._unavailable(
                account=account_value,
                period=period_value,
                as_of_month=as_of_month,
                reason="requested_month_in_future",
            )

        target_end = _month_end(target_start)
        effective_target_end = min(target_end, today)
        navs = sorted(
            (
                nav
                for nav in self.storage.get_nav_history(account_value, days=10000)
                if getattr(nav, "date", None) is not None
            ),
            key=lambda nav: nav.date,
        )
        ending_nav = _last_nav_between(navs, target_start, effective_target_end)
        if ending_nav is None:
            return self._unavailable(
                account=account_value,
                period=period_value,
                as_of_month=as_of_month,
                reason="target_month_nav_missing",
            )

        if period_value == "mtd":
            anchor_end = target_start - timedelta(days=1)
            anchor_start = date(anchor_end.year, anchor_end.month, 1)
            anchor_reason = "previous_month_anchor_missing"
            calendar_start = target_start
            stored_pnl = getattr(ending_nav, "mtd_pnl", None)
        else:
            anchor_start = date(target_start.year - 1, 1, 1)
            anchor_end = date(target_start.year - 1, 12, 31)
            anchor_reason = "previous_year_anchor_missing"
            calendar_start = date(target_start.year, 1, 1)
            stored_pnl = getattr(ending_nav, "ytd_pnl", None)

        anchor_nav = _last_nav_between(navs, anchor_start, anchor_end)
        if anchor_nav is None:
            return self._unavailable(
                account=account_value,
                period=period_value,
                as_of_month=as_of_month,
                reason=anchor_reason,
                calendar_start=calendar_start,
                end_date=ending_nav.date,
            )

        opening_assets = _money(getattr(anchor_nav, "total_value", None))
        ending_assets = _money(getattr(ending_nav, "total_value", None))
        external_cash_flow = _money(
            self.cash_flows.period(account_value, calendar_start, ending_nav.date)
        )
        calculated_pnl = _money(ending_assets - opening_assets - external_cash_flow)

        return {
            "schema_version": _SCHEMA_VERSION,
            "success": True,
            "status": "ok",
            "account": account_value,
            "period": {
                "kind": period_value,
                "requested_as_of_month": target_start.strftime("%Y-%m"),
                "calendar_start": calendar_start.isoformat(),
                "anchor_date": anchor_nav.date.isoformat(),
                "end_date": ending_nav.date.isoformat(),
                "basis": "latest_persisted_nav_in_requested_month",
                "timezone": "Asia/Shanghai",
            },
            "amounts": {
                "currency": "CNY",
                "opening_assets": float(opening_assets),
                "external_cash_flow": float(external_cash_flow),
                "period_pnl": float(calculated_pnl),
                "ending_assets": float(ending_assets),
            },
            "reconciliation": _pnl_reconciliation(stored_pnl, calculated_pnl),
            "source": {
                "nav": "nav_history",
                "external_cash_flow": "cash_flow",
            },
        }

    @staticmethod
    def _unavailable(
        *,
        account: str,
        period: str,
        as_of_month: str,
        reason: str,
        calendar_start: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        scope: dict[str, Any] = {
            "kind": period,
            "requested_as_of_month": str(as_of_month),
            "timezone": "Asia/Shanghai",
        }
        if calendar_start is not None:
            scope["calendar_start"] = calendar_start.isoformat()
        if end_date is not None:
            scope["end_date"] = end_date.isoformat()
        return {
            "schema_version": _SCHEMA_VERSION,
            "success": True,
            "status": "unavailable",
            "reason": reason,
            "account": account,
            "period": scope,
        }


def _parse_month(value: str) -> date:
    text = str(value or "").strip()
    try:
        year_text, month_text = text.split("-", 1)
        if len(year_text) != 4 or len(month_text) != 2:
            raise ValueError
        parsed = date(int(year_text), int(month_text), 1)
    except Exception as exc:
        raise ValueError("as_of_month must use YYYY-MM format") from exc
    if parsed.strftime("%Y-%m") != text:
        raise ValueError("as_of_month must use YYYY-MM format")
    return parsed


def _month_end(month_start: date) -> date:
    return date(month_start.year, month_start.month, calendar.monthrange(month_start.year, month_start.month)[1])


def _last_nav_between(navs: list[Any], start: date, end: date) -> Any | None:
    matches = [nav for nav in navs if start <= nav.date <= end]
    return matches[-1] if matches else None


def _money(value: Any) -> Decimal:
    if value is None:
        raise ValueError("capital fact amount is missing")
    return Decimal(str(value)).quantize(_MONEY, rounding=ROUND_HALF_UP)


def _pnl_reconciliation(stored_value: Any, calculated: Decimal) -> dict[str, Any]:
    if stored_value is None:
        return {
            "stored_period_pnl": None,
            "calculated_period_pnl": float(calculated),
            "difference": None,
            "tolerance": float(_RECONCILIATION_TOLERANCE),
            "status": "not_observed",
        }
    stored = _money(stored_value)
    difference = _money(stored - calculated)
    return {
        "stored_period_pnl": float(stored),
        "calculated_period_pnl": float(calculated),
        "difference": float(difference),
        "tolerance": float(_RECONCILIATION_TOLERANCE),
        "status": "ok" if abs(difference) <= _RECONCILIATION_TOLERANCE else "mismatch",
    }
