"""Business-day calendar for daily NAV jobs."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Set

from src import config
from src.time_utils import bj_today


def _parse_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _parse_date_set(value: Any) -> Set[date]:
    if value in (None, ""):
        return set()
    if isinstance(value, str):
        raw_items: Iterable[Any] = [item.strip() for item in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]

    out: Set[date] = set()
    for item in raw_items:
        if item in (None, ""):
            continue
        out.add(_parse_date(item))
    return out


class BusinessCalendarService:
    """Weekend + configured holiday calendar.

    The product deliberately uses one simple business calendar for daily NAV
    jobs.  Market-specific holiday precision can be added later without changing
    the NAV calculation boundary.
    """

    def __init__(self, *, holidays: Optional[Iterable[Any]] = None):
        self.holidays = _parse_date_set(holidays)

    @classmethod
    def from_config(cls) -> "BusinessCalendarService":
        return cls(holidays=config.get("calendar.holidays", []))

    def previous_business_day(self, *, before: Optional[Any] = None) -> date:
        base_date = _parse_date(before) if before is not None else bj_today()
        candidate = base_date - timedelta(days=1)
        for _ in range(366):
            if self.is_business_day(candidate):
                return candidate
            candidate -= timedelta(days=1)
        raise ValueError("no business day found within one year before run date")

    def default_nav_date(self, *, run_date: Optional[Any] = None) -> date:
        base_date = _parse_date(run_date) if run_date is not None else bj_today()
        return self.previous_business_day(before=base_date)

    def is_business_day(self, value: Any) -> bool:
        d = _parse_date(value)
        if d.weekday() >= 5:
            return False
        if d in self.holidays:
            return False
        return True

    def explain(self, value: Any) -> dict:
        d = _parse_date(value)
        if d.weekday() >= 5:
            return {"business_day": False, "reason": "weekend", "date": d.isoformat()}
        if d in self.holidays:
            return {"business_day": False, "reason": "holiday", "date": d.isoformat()}
        return {"business_day": True, "reason": "business_day", "date": d.isoformat()}
