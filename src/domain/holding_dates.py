"""Canonical date transport for Feishu holdings metadata."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional


HOLDING_DATE_FORMAT = "%Y/%m/%d"
HOLDING_PREDECESSOR_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_holding_date(value: datetime) -> str:
    """Render one holdings timestamp at the canonical day precision."""

    return value.strftime(HOLDING_DATE_FORMAT)


def parse_holding_date(value: Any, *, field_name: str) -> Optional[datetime]:
    """Parse text dates and Feishu millisecond timestamps in Beijing time."""

    if value in (None, ""):
        return None
    if type(value) in (int, float):
        try:
            return datetime.fromtimestamp(
                value / 1000, tz=timezone(timedelta(hours=8))
            ).replace(tzinfo=None)
        except (ValueError, OverflowError, OSError):
            raise ValueError(f"invalid {field_name}: {value}") from None
    text = str(value).strip()
    for date_format in (
        HOLDING_DATE_FORMAT,
        HOLDING_PREDECESSOR_DATETIME_FORMAT,
    ):
        try:
            parsed = datetime.strptime(text, date_format)
        except (TypeError, ValueError):
            continue
        if parsed.strftime(date_format) == text:
            return parsed
    raise ValueError(f"invalid {field_name}: {value}")
