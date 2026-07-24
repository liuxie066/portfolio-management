"""Portfolio read-model service for holdings, snapshot, and distributions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from src.asset_utils import normalize_code
from src.reporting_utils import normalize_asset_type, normalization_warning
from src.time_utils import bj_now_naive


class PortfolioReadService:
    def __init__(self, *, account: str, storage: Any, portfolio: Any, reporting_service: Any):
        self.account = account
        self.storage = storage
        self.portfolio = portfolio
        self.reporting_service = reporting_service

    @staticmethod
    def _quote_lookup(price_evidence: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        lookup: Dict[str, Dict[str, Any]] = {}
        for raw_code, payload in (price_evidence or {}).items():
            if not isinstance(payload, dict):
                continue
            for key in (
                str(raw_code or "").strip(),
                str(raw_code or "").strip().upper(),
                normalize_code(str(raw_code or "").strip()),
                str(payload.get("code") or "").strip(),
                str(payload.get("code") or "").strip().upper(),
                normalize_code(str(payload.get("code") or "").strip()),
            ):
                if key:
                    lookup.setdefault(key, payload)
        return lookup

    @staticmethod
    def _evidence_quote(code: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        currency = str(payload.get("currency") or "CNY").strip().upper()
        exchange_rate = payload.get("exchange_rate")
        if currency == "CNY":
            exchange_rate = 1.0
        observed_at = payload.get("fetched_at")
        if observed_at:
            try:
                parsed = datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                observed_at = parsed.astimezone(timezone.utc).isoformat()
            except ValueError:
                observed_at = str(observed_at)
        return {
            "code": code,
            "status": "observed",
            "currency": currency,
            "price_native": payload.get("price"),
            "price_cny": payload.get("cny_price") if payload.get("cny_price") is not None else (
                payload.get("price") if currency == "CNY" else None
            ),
            "exchange_rate_to_cny": exchange_rate,
            "source": payload.get("source") or payload.get("data_source"),
            "source_chain": list(payload.get("source_chain") or []),
            "observed_at": observed_at,
            "cache_status": payload.get("cache_status"),
            "is_from_cache": bool(payload.get("is_from_cache")),
            "is_from_run_pool": bool(payload.get("is_from_run_pool")),
            "is_stale": bool(payload.get("is_stale")) or payload.get("source") == "cache_fallback",
        }

    def build_valuation_evidence(
        self,
        *,
        supplemental_codes: Optional[list[str]] = None,
        price_timeout_seconds: int = 30,
        run_quote_pool: Any = None,
    ) -> Dict[str, Any]:
        supplemental = list(
            dict.fromkeys(
                str(code or "").strip()
                for code in (supplemental_codes or [])
                if str(code or "").strip()
            )
        )
        valuation = self.portfolio.calculate_valuation(
            self.account,
            price_timeout_seconds=price_timeout_seconds,
            run_quote_pool=run_quote_pool,
            supplemental_codes=supplemental,
        )
        raw_evidence = dict(getattr(valuation, "price_evidence", None) or {})
        quote_lookup = self._quote_lookup(raw_evidence)
        quotes_by_code: Dict[str, Dict[str, Any]] = {}
        holdings: list[Dict[str, Any]] = []
        partial = False

        for holding in valuation.holdings or []:
            code = str(holding.asset_id or "").strip()
            raw_quote = (
                quote_lookup.get(code)
                or quote_lookup.get(code.upper())
                or quote_lookup.get(normalize_code(code))
            )
            raw_type = holding.asset_type.value if holding.asset_type else None
            currency = str(holding.currency or "").strip().upper()
            if not isinstance(raw_quote, dict) and raw_type in {"cash", "mmf"} and currency == "CNY":
                raw_quote = {
                    "code": code,
                    "price": 1.0,
                    "cny_price": 1.0,
                    "exchange_rate": 1.0,
                    "currency": "CNY",
                    "source": "fixed_identity",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
            if isinstance(raw_quote, dict):
                quotes_by_code.setdefault(code, self._evidence_quote(code, raw_quote))
            if holding.quantity and holding.market_value_cny is None:
                partial = True
            holdings.append(
                {
                    "account": getattr(holding, "account", None) or self.account,
                    "broker": holding.broker,
                    "code": code,
                    "name": holding.asset_name,
                    "asset_type": raw_type,
                    "normalized_type": normalize_asset_type(holding.asset_type, code),
                    "quantity": holding.quantity,
                    "currency": currency,
                    "price_native": holding.current_price,
                    "price_cny": holding.cny_price,
                    "market_value_cny": holding.market_value_cny,
                    "quote_code": code,
                }
            )

        missing_supplemental: list[str] = []
        for code in supplemental:
            raw_quote = (
                quote_lookup.get(code)
                or quote_lookup.get(code.upper())
                or quote_lookup.get(normalize_code(code))
            )
            if not isinstance(raw_quote, dict):
                missing_supplemental.append(code)
                partial = True
                continue
            quotes_by_code.setdefault(code, self._evidence_quote(code, raw_quote))

        quotes = list(quotes_by_code.values())
        if any(item.get("is_stale") for item in quotes):
            partial = True
        valuation_messages = list(valuation.warnings or [])
        diagnostics = [
            str(item)
            for item in valuation_messages
            if str(item).strip().startswith("[价格汇总]")
        ]
        warnings = [
            str(item)
            for item in valuation_messages
            if not str(item).strip().startswith("[价格汇总]")
        ]
        warnings.extend(f"{code}: supplemental quote missing" for code in missing_supplemental)
        return {
            "account": self.account,
            "status": "partial" if partial else "complete",
            "holdings": holdings,
            "quotes": quotes,
            "warnings": list(dict.fromkeys(str(item) for item in warnings if str(item).strip())),
            "diagnostics": diagnostics,
        }

    def build_snapshot(
        self,
        *,
        price_timeout_seconds: Optional[int] = None,
        run_quote_pool: Any = None,
    ) -> Dict[str, Any]:
        if price_timeout_seconds is None:
            if run_quote_pool is None:
                valuation = self.portfolio.calculate_valuation(self.account)
            else:
                valuation = self.portfolio.calculate_valuation(
                    self.account,
                    run_quote_pool=run_quote_pool,
                )
        else:
            valuation_kwargs = {"price_timeout_seconds": price_timeout_seconds}
            if run_quote_pool is not None:
                valuation_kwargs["run_quote_pool"] = run_quote_pool
            valuation = self.portfolio.calculate_valuation(self.account, **valuation_kwargs)
        holdings = valuation.holdings or []
        holdings_list = []
        for h in holdings:
            holdings_list.append({
                "code": h.asset_id,
                "name": h.asset_name,
                "quantity": h.quantity,
                "type": h.asset_type.value if h.asset_type else None,
                "normalized_type": normalize_asset_type(h.asset_type, h.asset_id),
                "account": getattr(h, "account", None),
                "broker": h.broker,
                "currency": h.currency,
                "price": h.current_price,
                "cny_price": h.cny_price,
                "market_value": h.market_value_cny,
                "weight": h.weight,
            })
        holdings_list.sort(key=lambda x: x.get("market_value") or 0, reverse=True)

        return {
            "snapshot_time": bj_now_naive().isoformat(),
            "valuation": valuation,
            "holdings_data": {
                "success": True,
                "holdings": holdings_list,
                "count": len(holdings_list),
                "total_value": valuation.total_value_cny,
                "cash_value": valuation.cash_value_cny,
                "stock_value": valuation.stock_value_cny + valuation.fund_value_cny,
                "cash_ratio": valuation.cash_ratio,
                "warnings": valuation.warnings,
            },
            "position_data": {
                "cash_ratio": valuation.cash_ratio,
                "stock_ratio": valuation.stock_ratio,
                "fund_ratio": valuation.fund_ratio,
            },
        }

    def get_holdings(
        self,
        *,
        include_cash: bool = True,
        group_by_market: bool = False,
        include_price: bool = False,
        group_by_broker: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if group_by_broker is not None:
            group_by_market = group_by_broker

        if include_price:
            snapshot = self.build_snapshot()
            holdings_data = snapshot.get("holdings_data") or {}
            result_holdings = [
                dict(h)
                for h in (holdings_data.get("holdings") or [])
                if include_cash or h.get("normalized_type") != "cash"
            ]

            result = {
                "success": True,
                "count": len(result_holdings),
                "total_value": holdings_data.get("total_value", 0),
                "cash_value": holdings_data.get("cash_value", 0),
                "stock_value": holdings_data.get("stock_value", 0),
                "cash_ratio": holdings_data.get("cash_ratio", 0),
            }
            warnings = holdings_data.get("warnings") or []
            if warnings:
                result["warnings"] = warnings

            return self._format_holdings_result(
                result=result,
                holdings=result_holdings,
                group_by_market=group_by_market,
                include_price=True,
            )

        holdings = self.storage.get_holdings(account=self.account)
        result_holdings = []
        normalization_warnings = []

        for h in holdings:
            normalized_type = normalize_asset_type(h.asset_type, h.asset_id)
            warn = normalization_warning(h.asset_type, h.asset_id)
            if warn and warn not in normalization_warnings:
                normalization_warnings.append(warn)

            if include_cash or normalized_type != "cash":
                result_holdings.append({
                    "code": h.asset_id,
                    "name": h.asset_name,
                    "quantity": h.quantity,
                    "type": h.asset_type.value if h.asset_type else None,
                    "normalized_type": normalized_type,
                    "broker": h.broker,
                    "currency": h.currency,
                })

        result = {"success": True, "count": len(result_holdings)}
        if normalization_warnings:
            result["warnings"] = [f"分类兜底: {w}" for w in normalization_warnings]

        return self._format_holdings_result(
            result=result,
            holdings=result_holdings,
            group_by_market=group_by_market,
            include_price=False,
        )

    def get_position(self, holdings_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snapshot = self._snapshot_from_holdings_data(holdings_data) if holdings_data is not None else self.build_snapshot()
        return self.reporting_service.build_position(snapshot)

    def get_distribution(self, holdings_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        snapshot = self._snapshot_from_holdings_data(holdings_data) if holdings_data is not None else self.build_snapshot()
        return self.reporting_service.build_distribution(snapshot)

    def get_asset_distribution(
        self,
        holdings_data: Optional[Dict[str, Any]] = None,
        *,
        include_value: bool = True,
        group_cash: bool = False,
    ) -> Dict[str, Any]:
        snapshot = self._snapshot_from_holdings_data(holdings_data) if holdings_data is not None else self.build_snapshot()
        return self.reporting_service.build_asset_distribution(
            snapshot,
            include_value=include_value,
            group_cash=group_cash,
        )

    @staticmethod
    def merge_holdings_data(holdings_data_list: list) -> Dict[str, Any]:
        """Merge holdings_data snapshots from multiple accounts.

        Preserves per-row ``account`` so downstream builders can still break down
        by account.  Values and totals are summed; metadata like ``cash_value``
        is omitted because it is not needed for distribution reports.
        """
        merged_holdings: list = []
        total_value = 0.0
        for holdings_data in holdings_data_list:
            if not isinstance(holdings_data, dict):
                continue
            for holding in (holdings_data.get("holdings") or []):
                merged_holdings.append(dict(holding))
            total_value += float(holdings_data.get("total_value") or 0)

        return {
            "success": True,
            "holdings": merged_holdings,
            "total_value": total_value,
        }

    @staticmethod
    def _format_holdings_result(
        *,
        result: Dict[str, Any],
        holdings: list,
        group_by_market: bool,
        include_price: bool,
    ) -> Dict[str, Any]:
        if not group_by_market:
            result["holdings"] = holdings
            return result

        by_market = {}
        for holding in holdings:
            broker = holding.get("broker") or "未指定券商"
            by_market.setdefault(broker, []).append(holding)

        if include_price:
            market_values = {
                market: sum((item.get("market_value") or 0) for item in items)
                for market, items in by_market.items()
            }
            sorted_markets = sorted(by_market.keys(), key=lambda m: market_values[m], reverse=True)
            result["by_market"] = {m: by_market[m] for m in sorted_markets}
            result["market_values"] = {m: market_values[m] for m in sorted_markets}
        else:
            result["by_market"] = by_market

        result["market_count"] = len(by_market)
        return result

    @staticmethod
    def _snapshot_from_holdings_data(holdings_data: Dict[str, Any]) -> Dict[str, Any]:
        if holdings_data is None:
            return {}
        if "holdings_data" in holdings_data or "valuation" in holdings_data:
            return holdings_data
        return {"holdings_data": holdings_data}
