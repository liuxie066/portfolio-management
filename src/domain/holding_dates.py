"""Canonical date transport for Feishu holdings metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


HOLDING_DATE_FORMAT = "%Y/%m/%d"
HOLDING_PREDECESSOR_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def format_holding_date(value: datetime) -> str:
    """Render one holdings timestamp at the canonical day precision."""

    return value.strftime(HOLDING_DATE_FORMAT)


def parse_holding_date(value: Any, *, field_name: str) -> Optional[datetime]:
    """Parse canonical holdings dates plus the immediately preceding format."""

    if value in (None, ""):
        return None
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
