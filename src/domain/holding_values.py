"""Shared raw holding value rules; missing and invalid quantities never become zero."""
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


def holding_text(value: Any) -> Optional[str]:
    if value is None or not isinstance(value, (str, int, float, Decimal)):
        return None
    normalized = str(value).strip()
    return normalized or None


def holding_decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def canonical_holding_number(value: Decimal) -> str:
    """Format finite values exactly, independently of Decimal context precision."""
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def holding_broker_scope(value: Optional[str]) -> str:
    if not isinstance(value, str):
        return "unknown"
    compact = value.strip().replace("（", "(").replace("）", ")").replace(" ", "").replace("　", "").lower()
    if (not compact or compact in {"n/a", "na", "none", "null"}
        or compact.startswith(("unknown", "未知", "其他", "其它", "other", "manual", "手动", "未指定", "待确认"))
        or not any(char.isalnum() for char in compact)):
        return "unknown"
    if compact.startswith(("moomoo", "富途", "futu")):
        return "futu"
    return "non_futu"
