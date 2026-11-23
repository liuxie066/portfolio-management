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
        market_value = float(holding["market_value"]) if holding.get("market_value") is not None else None
        quantity = float(holding.get("quantity") or 0.0)

        if normalized_type == "cash" or is_cash_like(raw_type, code):
            cash_bucket["quantity"] += quantity
            cash_bucket["market_value"] = (
                cash_bucket["market_value"] + market_value
                if cash_bucket["market_value"] is not None and market_value is not None else None
            )
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
            row["market_value"] = (
                row["market_value"] + market_value
                if row["market_value"] is not None and market_value is not None else None
            )
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
        market_value = row["market_value"]
        row["weight"] = market_value / total_value if market_value is not None and total_value > 0 else None

    merged_rows.sort(key=lambda row: float(row.get("market_value") or 0.0), reverse=True)
    return merged_rows[:top_n]


def report_holdings_fields(holdings_data: dict, position_data: dict) -> dict:
    """Project the valuation overview and top holdings used by reports."""
    total_value = holdings_data.get("total_value", 0)
    options = [
        row for row in holdings_data.get("holdings", [])
        if row.get("normalized_type") == "option"
    ]
    option_values = {}
    if options:
        long_value = sum(row.get("market_value") or 0.0 for row in options if row["quantity"] > 0)
        short_value = sum(row.get("market_value") or 0.0 for row in options if row["quantity"] < 0)
        option_values = {
            "option_long_value": long_value,
            "option_short_value": short_value,
            "option_value": long_value + short_value,
        }
    return {
        "overview": {
            "total_value": total_value,
            "cash_ratio": position_data.get("cash_ratio", 0),
            "stock_ratio": position_data.get("stock_ratio", 0),
            **({"pending_asset_value": holdings_data["pending_asset_value"]}
               if holdings_data.get("pending_asset_value") else {}),
            "fund_ratio": position_data.get("fund_ratio", 0),
            **option_values,
        },
        "top_holdings": merge_top_holdings(holdings_data.get("holdings", []), total_value or 0),
    }
