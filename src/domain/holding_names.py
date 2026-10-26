"""Pure identity checks for public Hong Kong holding-name evidence."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Mapping

_HK_CODE = re.compile(r"(?:HK\.?([0-9]{1,5})|([0-9]{1,5})(?:\.HK)?)", re.ASCII)


def normalize_hk_stock_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _HK_CODE.fullmatch(value.strip().upper())
    if not match:
        return None
    digits = match.group(1) or match.group(2)
    return digits.zfill(5) if int(digits) else None


def verified_hk_name(evidence: Any, asset_id: Any) -> Mapping[str, Any] | None:
    code = normalize_hk_stock_code(asset_id)
    if not code or not isinstance(evidence, Mapping):
        return None
    name = evidence.get("name")
    if (
        evidence.get("code") != code
        or evidence.get("market") != "HK"
        or evidence.get("source") != "tencent_hk"
        or not isinstance(name, str) or not name.strip()
        or normalize_hk_stock_code(name) is not None
        or not isinstance(evidence.get("source_as_of"), str)
        or not evidence["source_as_of"].strip()
    ):
        return None
    try:
        observed_at = datetime.fromisoformat(evidence["source_as_of"].replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed_at.tzinfo is None or evidence.get("is_stale") or evidence.get("is_from_cache"):
        return None
    return {**evidence, "name": name.strip()}
