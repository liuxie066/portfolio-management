"""Business-day calendar for daily NAV jobs."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Optional, Set

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
    """NAV calendar: any open CN, HK, or US market makes a business day."""

    MARKETS = ("CN", "HK", "US")

    def __init__(
        self,
        *,
        holidays: Optional[Iterable[Any]] = None,
        market_days: Optional[Callable[[date], dict[str, Optional[bool]]]] = None,
    ):
        self.holidays = _parse_date_set(holidays)
        self._market_days = market_days

    @classmethod
    def from_config(cls) -> "BusinessCalendarService":
        return cls(market_days=cls._futu_market_days)

    @classmethod
    def _futu_market_days(cls, day: date) -> dict[str, Optional[bool]]:
        try:
            import futu as sdk
        except ImportError:
            import moomoo as sdk

        ctx = sdk.OpenQuoteContext(
            host=config.get("futu.opend.host", "127.0.0.1"),
            port=int(config.get("futu.opend.port", 11111)),
        )
        try:
            result = {}
            for market in cls.MARKETS:
                try:
                    ret, rows = ctx.request_trading_days(
                        market=getattr(sdk.TradeDateMarket, market),
                        start=day.isoformat(),
                        end=day.isoformat(),
                    )
                    result[market] = (
                        bool(rows)
                        if ret == sdk.RET_OK
                        and isinstance(rows, list)
                        and all(
                            isinstance(row, dict)
                            and row.get("time") == day.isoformat()
                            and row.get("trade_date_type") in {"WHOLE", "MORNING", "AFTERNOON"}
                            for row in rows
                        )
                        else None
                    )
                except Exception:
                    result[market] = None
            return result
        finally:
            ctx.close()

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
        return self.explain(value)["business_day"]

    def explain(self, value: Any) -> dict:
        d = _parse_date(value)
        if d.weekday() >= 5:
            return {"business_day": False, "reason": "weekend", "date": d.isoformat()}
        if self._market_days is not None:
            markets = self._market_days(d)
            if any(markets.get(market) is True for market in self.MARKETS):
                return {
                    "business_day": True,
                    "reason": "market_open",
                    "date": d.isoformat(),
                    "markets": markets,
                }
            if any(markets.get(market) is not False for market in self.MARKETS):
                raise RuntimeError(f"trading calendar unavailable for {d.isoformat()}")
            return {
                "business_day": False,
                "reason": "all_markets_closed",
                "date": d.isoformat(),
                "markets": markets,
            }
        if d in self.holidays:
            return {"business_day": False, "reason": "holiday", "date": d.isoformat()}
        return {"business_day": True, "reason": "business_day", "date": d.isoformat()}
