"""Holdings projections for report payloads."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.reporting_utils import is_cash_like


def merge_top_holdings(holdings: Iterable[Dict[str, Any]], total_value: float, top_n: int = 10) -> List[Dict[str, Any]]:
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
