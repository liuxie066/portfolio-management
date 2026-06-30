"""Portfolio reporting read service."""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.reporting_utils import normalize_asset_type


class ReportingService:
    """Build lightweight portfolio distribution reports.

    ``manager`` is used as the runtime facade so changes to
    ``manager.price_fetcher`` and ``manager.calculate_valuation`` are respected.
    """

    def __init__(self, manager: Any, storage: Any):
        self.manager = manager
        self.storage = storage

    def get_asset_distribution(self, account: str) -> Dict[str, float]:
        valuation = self.manager.calculate_valuation(account)
        if valuation.total_value_cny == 0:
            return {}

        return {
            "现金": valuation.cash_value_cny / valuation.total_value_cny,
            "股票": valuation.stock_value_cny / valuation.total_value_cny,
            "基金": valuation.fund_value_cny / valuation.total_value_cny,
            "中国资产": valuation.cn_asset_value / valuation.total_value_cny,
            "美国资产": valuation.us_asset_value / valuation.total_value_cny,
            "港股资产": valuation.hk_asset_value / valuation.total_value_cny,
        }

    def get_industry_distribution(self, account: str) -> Dict[str, float]:
        valuation = self.manager.calculate_valuation(account)
        holdings = valuation.holdings or []

        industry_values = {}
        total_value = 0.0
        for holding in holdings:
            market_value = holding.market_value_cny or 0
            industry = holding.industry.value if holding.industry else "其他"
            industry_values[industry] = industry_values.get(industry, 0) + market_value
            total_value += market_value

        if total_value == 0:
            return {}

        return {industry: value / total_value for industry, value in industry_values.items()}

    def build_position(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        valuation = snapshot.get("valuation")
        holdings_data = snapshot.get("holdings_data") or {}
        position_data = snapshot.get("position_data") or {}

        if valuation is not None:
            total_value = valuation.total_value_cny
            stock_value = valuation.stock_value_cny
            fund_value = valuation.fund_value_cny
            cash_value = valuation.cash_value_cny
            return {
                "success": True,
                "total_value": total_value,
                "stock_value": stock_value,
                "fund_value": fund_value,
                "cash_value": cash_value,
                "stock_ratio": valuation.stock_ratio,
                "fund_ratio": valuation.fund_ratio,
                "cash_ratio": valuation.cash_ratio,
            }

        total_value = holdings_data.get("total_value", 0) or 0
        stock_value = holdings_data.get("stock_value", 0) or 0
        cash_value = holdings_data.get("cash_value", 0) or 0
        holdings = holdings_data.get("holdings") or []
        fund_value = sum((h.get("market_value") or 0) for h in holdings if h.get("normalized_type") == "fund")
        stock_value = max(0, stock_value - fund_value)

        return {
            "success": True,
            "total_value": total_value,
            "stock_value": stock_value,
            "fund_value": fund_value,
            "cash_value": cash_value,
            "stock_ratio": position_data.get("stock_ratio", stock_value / total_value if total_value > 0 else 0),
            "fund_ratio": position_data.get("fund_ratio", fund_value / total_value if total_value > 0 else 0),
            "cash_ratio": position_data.get("cash_ratio", cash_value / total_value if total_value > 0 else 0),
        }

    def build_distribution(self, snapshot: Dict[str, Any]) -> Dict[str, Any]:
        valuation = snapshot.get("valuation")
        holdings_data = snapshot.get("holdings_data") or {}
        holdings = holdings_data.get("holdings") or []

        type_dist: Dict[str, float] = {}
        market_dist: Dict[str, float] = {}
        currency_dist: Dict[str, float] = {}

        for holding in holdings:
            normalized_type = holding.get("normalized_type") or normalize_asset_type(holding.get("type"), holding.get("code", ""))
            market_value = holding.get("market_value") or 0
            type_dist[normalized_type] = type_dist.get(normalized_type, 0) + market_value

            broker = holding.get("broker") or "未指定券商"
            market_dist[broker] = market_dist.get(broker, 0) + market_value

            currency = holding.get("currency") or "CNY"
            currency_dist[currency] = currency_dist.get(currency, 0) + market_value

        total = (
            valuation.total_value_cny
            if valuation is not None
            else holdings_data.get("total_value", 0)
        ) or 0

        def sort_by_value(items_dict):
            return sorted(items_dict.items(), key=lambda x: x[1], reverse=True)

        by_market = [{"broker": k, "value": v, "ratio": v / total if total > 0 else 0} for k, v in sort_by_value(market_dist)]

        return {
            "success": True,
            "total_value": total,
            "by_type": [{"type": k, "value": v, "ratio": v / total if total > 0 else 0} for k, v in sort_by_value(type_dist)],
            "by_market": by_market,
            "by_broker": by_market,
            "by_currency": [{"currency": k, "value": v, "ratio": v / total if total > 0 else 0} for k, v in sort_by_value(currency_dist)],
        }

    def build_asset_distribution(
        self,
        snapshot: Dict[str, Any],
        *,
        include_value: bool = True,
        group_cash: bool = False,
    ) -> Dict[str, Any]:
        """Build asset-level distribution, optionally including market value.

        Returns a merged view across accounts/brokers for the same asset code.
        Each row contains quantity, per-account breakdown, and (when requested)
        value/ratio fields.

        When ``group_cash`` is True, all cash-like positions (normalized type
        ``cash``) are collapsed into a single "CASH+MMF" row.
        """
        valuation = snapshot.get("valuation")
        holdings_data = snapshot.get("holdings_data") or {}
        holdings = holdings_data.get("holdings") or []

        total = (
            valuation.total_value_cny
            if valuation is not None
            else holdings_data.get("total_value", 0)
        ) or 0

        asset_map: Dict[str, Dict[str, Any]] = {}
        total_quantity = 0.0
        cash_entry: Optional[Dict[str, Any]] = None

        for holding in holdings:
            code = holding.get("code") or ""
            if not code:
                continue

            normalized_type = holding.get("normalized_type") or normalize_asset_type(
                holding.get("type"), code
            )
            quantity = float(holding.get("quantity") or 0)
            market_value = float(holding.get("market_value") or 0)
            account = holding.get("account") or "default"
            broker = holding.get("broker") or "未指定券商"
            currency = holding.get("currency") or "CNY"

            is_cash = normalized_type == "cash"
            if group_cash and is_cash:
                if cash_entry is None:
                    cash_entry = {
                        "code": "CASH+MMF",
                        "name": "现金及货基",
                        "type": holding.get("type"),
                        "normalized_type": "cash",
                        "currency": currency,
                        "quantity": 0.0,
                        "value": 0.0,
                        "accounts": {},
                        "brokers": set(),
                        "breakdown": [],
                    }
                entry = cash_entry
            else:
                if code not in asset_map:
                    asset_map[code] = {
                        "code": code,
                        "name": holding.get("name") or code,
                        "type": holding.get("type"),
                        "normalized_type": normalized_type,
                        "currency": currency,
                        "quantity": 0.0,
                        "value": 0.0,
                        "accounts": {},
                        "brokers": set(),
                        "breakdown": [],
                    }
                entry = asset_map[code]

            entry["quantity"] += quantity
            entry["value"] += market_value
            entry["accounts"][account] = entry["accounts"].get(account, 0.0) + quantity
            entry["brokers"].add(broker)
            breakdown_item = {
                "account": account,
                "broker": broker,
                "quantity": quantity,
            }
            if include_value:
                breakdown_item["value"] = market_value
            entry["breakdown"].append(breakdown_item)

            total_quantity += quantity

        if group_cash and cash_entry is not None:
            asset_map["CASH+MMF"] = cash_entry

        def sort_key(item):
            return item[1]["value"] if include_value else item[1]["quantity"]

        sorted_assets = sorted(asset_map.items(), key=sort_key, reverse=True)

        by_asset = []
        for code, entry in sorted_assets:
            row = {
                "code": entry["code"],
                "name": entry["name"],
                "normalized_type": entry["normalized_type"],
                "currency": entry["currency"],
                "quantity": entry["quantity"],
                "accounts": entry["accounts"],
                "brokers": sorted(entry["brokers"]),
                "breakdown": entry["breakdown"],
            }
            if include_value:
                row["value"] = entry["value"]
                row["ratio"] = entry["value"] / total if total > 0 else 0.0
            else:
                row["quantity_ratio"] = entry["quantity"] / total_quantity if total_quantity > 0 else 0.0
            by_asset.append(row)

        result: Dict[str, Any] = {"success": True, "by_asset": by_asset}
        if include_value:
            result["total_value"] = total
        else:
            result["total_quantity"] = total_quantity
        return result
