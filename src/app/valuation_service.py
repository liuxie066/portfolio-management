"""Portfolio valuation application service."""
from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from src.asset_utils import normalize_code
from src.models import AssetClass, AssetType, PortfolioValuation
from src.pricing.payload import positive_finite_decimal
from src.reporting_utils import normalize_holding_type


class ValuationService:
    def __init__(self, manager: Any, storage: Any, price_fetcher=None):
        self.manager = manager
        self.storage = storage
        self.price_fetcher = price_fetcher

    @staticmethod
    def _price_field(price_payload: Any, key: str, default: Any = None) -> Any:
        if isinstance(price_payload, dict):
            return price_payload.get(key, default)
        return getattr(price_payload, key, default)

    def calculate_valuation(
        self,
        account: str,
        fetch_prices: bool = True,
        price_timeout_seconds: int = 25,
        allow_stale_price_fallback: bool = True,
        price_market_closed_ttl_multiplier: float = 1.0,
        run_quote_pool: Any = None,
        supplemental_codes: list[str] | None = None,
        deadline: float | None = None,
        holdings: Sequence[Any] | None = None,
        price_snapshot: Mapping[str, Any] | None = None,
        price_warnings: Sequence[str] | None = None,
        total_shares: Any = None,
    ) -> PortfolioValuation:
        account_holdings = (
            list(holdings)
            if holdings is not None
            else list(self.storage.get_holdings(account=account))
        )
        supplemental = list(
            dict.fromkeys(
                str(code or "").strip()
                for code in (supplemental_codes or [])
                if str(code or "").strip()
            )
        )
        if not account_holdings and not supplemental:
            return PortfolioValuation(
                account=account,
                total_value_cny=0,
                warnings=[
                    str(value)
                    for value in (price_warnings or [])
                    if str(value).strip()
                ],
            )

        prices: dict[str, Any] = dict(price_snapshot or {})
        price_errors: list[str] = [
            str(value)
            for value in (price_warnings or [])
            if str(value).strip()
        ]
        normalization_warnings: list[str] = []
        if price_snapshot is None and self.price_fetcher and fetch_prices:
            prices, fetch_warnings = self.fetch_price_snapshot(
                holdings=account_holdings,
                supplemental_codes=supplemental,
                price_timeout_seconds=price_timeout_seconds,
                allow_stale_price_fallback=allow_stale_price_fallback,
                price_market_closed_ttl_multiplier=price_market_closed_ttl_multiplier,
                run_quote_pool=run_quote_pool,
                deadline=deadline,
            )
            price_errors.extend(fetch_warnings)
        elif price_snapshot is None:
            for holding in account_holdings:
                price = self.storage.get_price(holding.asset_id)
                if price:
                    prices[holding.asset_id] = price

        price_lookup = dict(prices)
        for code, payload in list(prices.items()):
            if code:
                upper = str(code).strip().upper()
                price_lookup.setdefault(upper, payload)
                price_lookup.setdefault(normalize_code(upper), payload)

        total_value_cny = Decimal("0")
        cash_value_cny = Decimal("0")
        stock_value_cny = Decimal("0")
        fund_value_cny = Decimal("0")
        cn_asset_value = Decimal("0")
        us_asset_value = Decimal("0")
        hk_asset_value = Decimal("0")
        price_meta = {
            "from_cache": 0,
            "from_realtime": 0,
            "stale_fallback": 0,
            "missing": 0,
            "run_reused": 0,
        }

        for holding in account_holdings:
            holding_code = str(holding.asset_id).strip()
            price = (
                price_lookup.get(holding.asset_id)
                or price_lookup.get(holding_code.upper())
                or price_lookup.get(normalize_code(holding_code))
                or {}
            )
            normalized_type = normalize_holding_type(holding)
            raw_type = holding.asset_type.value if holding.asset_type else None
            currency = str(holding.currency or self._price_field(price, "currency", "CNY")).upper()

            if normalized_type == "cash" and raw_type not in ("cash", "mmf") and holding_code.upper().endswith("-CASH"):
                warning = (
                    f"分类兜底: {holding.asset_id}: 原始 asset_type={raw_type or 'None'}，"
                    "按代码后缀归一为 cash"
                )
                if warning not in normalization_warnings:
                    normalization_warnings.append(warning)

            quantity_dec = self.manager._to_decimal(holding.quantity)
            valid_price = False
            price_dec = None
            cny_price_dec = None
            if price:
                try:
                    price_dec = positive_finite_decimal(self._price_field(price, "price"), "price")
                    cny_raw = self._price_field(price, "cny_price")
                    if cny_raw is None and currency == "CNY":
                        cny_raw = price_dec
                    cny_price_dec = positive_finite_decimal(cny_raw, "cny_price")
                    valid_price = True
                except (TypeError, ValueError):
                    valid_price = False

            if valid_price:
                if isinstance(price, dict) and price.get("is_from_cache"):
                    price_meta["from_cache"] += 1
                elif isinstance(price, dict):
                    price_meta["from_realtime"] += 1
                else:
                    price_meta["from_cache"] += 1
                if isinstance(price, dict) and (
                    price.get("source") == "cache_fallback" or price.get("is_stale")
                ):
                    price_meta["stale_fallback"] += 1
                if isinstance(price, dict) and price.get("is_from_run_pool"):
                    price_meta["run_reused"] += 1

                holding.current_price = float(price_dec)
                holding.cny_price = float(cny_price_dec)
                market_value_dec = self.manager._quantize_money(quantity_dec * cny_price_dec)
                holding.market_value_cny = float(market_value_dec)
            else:
                price_meta["missing"] += 1
                can_use_unit_price = currency == "CNY" and raw_type in (
                    AssetType.CASH.value,
                    AssetType.MMF.value,
                )
                if can_use_unit_price:
                    holding.current_price = 1.0
                    holding.cny_price = 1.0
                    market_value_dec = self.manager._quantize_money(quantity_dec)
                    holding.market_value_cny = float(market_value_dec)
                else:
                    holding.current_price = None
                    holding.cny_price = None
                    market_value_dec = Decimal("0")
                    holding.market_value_cny = None
                    if normalized_type == "cash" and currency != "CNY":
                        price_errors.append(f"{holding.asset_name}({holding.asset_id}): 无法获取汇率")
                    elif holding.quantity != 0:
                        price_errors.append(f"{holding.asset_name}({holding.asset_id}): 价格缺失，无法可靠估值")

            total_value_cny += market_value_dec
            if normalized_type == "cash":
                cash_value_cny += market_value_dec
            elif normalized_type == "fund":
                fund_value_cny += market_value_dec
            else:
                stock_value_cny += market_value_dec

            if holding.asset_class == AssetClass.CN_ASSET:
                cn_asset_value += market_value_dec
            elif holding.asset_class == AssetClass.US_ASSET:
                us_asset_value += market_value_dec
            elif holding.asset_class == AssetClass.HK_ASSET:
                hk_asset_value += market_value_dec

        for holding in account_holdings:
            if total_value_cny > 0 and holding.market_value_cny is not None:
                weight_dec = self.manager._to_decimal(holding.market_value_cny) / total_value_cny
                holding.weight = float(self.manager._quantize_weight(weight_dec))

        resolved_total_shares = (
            self.storage.get_total_shares(account)
            if total_shares is None
            else total_shares
        )
        total_shares_dec = self.manager._to_decimal(resolved_total_shares)
        nav = float(self.manager._quantize_nav(total_value_cny / total_shares_dec)) if total_shares_dec > 0 else None

        warnings = [*normalization_warnings, *price_errors]
        tencent_meta = getattr(self.price_fetcher, "_last_tencent_batch_meta", None) if self.price_fetcher else None
        extra = ""
        if isinstance(tencent_meta, dict) and tencent_meta.get("requests") is not None:
            extra = (
                f"; tencent_batch=reqs={tencent_meta.get('requests')}, "
                f"elapsed_ms={tencent_meta.get('elapsed_ms')}, "
                f"returned={tencent_meta.get('returned_codes')}/{tencent_meta.get('requested_codes')}"
            )
        warnings.append(
            f"[价格汇总] realtime={price_meta['from_realtime']}, cache={price_meta['from_cache']}, "
            f"stale_fallback={price_meta['stale_fallback']}, missing={price_meta['missing']}, "
            f"run_reused={price_meta['run_reused']}" + extra
        )

        return PortfolioValuation(
            account=account,
            total_value_cny=float(self.manager._quantize_money(total_value_cny)),
            cash_value_cny=float(self.manager._quantize_money(cash_value_cny)),
            stock_value_cny=float(self.manager._quantize_money(stock_value_cny)),
            fund_value_cny=float(self.manager._quantize_money(fund_value_cny)),
            cn_asset_value=float(self.manager._quantize_money(cn_asset_value)),
            us_asset_value=float(self.manager._quantize_money(us_asset_value)),
            hk_asset_value=float(self.manager._quantize_money(hk_asset_value)),
            shares=resolved_total_shares,
            nav=nav,
            holdings=account_holdings,
            price_evidence={
                str(code): dict(payload)
                for code, payload in prices.items()
                if isinstance(payload, dict)
            },
            warnings=warnings,
        )

    def fetch_price_snapshot(
        self,
        *,
        holdings: Sequence[Any],
        supplemental_codes: Sequence[str] | None = None,
        price_timeout_seconds: int = 25,
        allow_stale_price_fallback: bool = True,
        price_market_closed_ttl_multiplier: float = 1.0,
        run_quote_pool: Any = None,
        deadline: float | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Fetch one deadline-bound quote snapshot for one or more accounts."""
        supplemental = list(
            dict.fromkeys(
                str(code or "").strip()
                for code in (supplemental_codes or [])
                if str(code or "").strip()
            )
        )
        snapshot_holdings = list(holdings)
        if not snapshot_holdings and not supplemental:
            return {}, []
        if not self.price_fetcher:
            return {}, ["价格获取不可用：未配置行情服务"]

        name_map = {holding.asset_id: holding.asset_name for holding in snapshot_holdings}
        name_map.update(
            {
                str(holding.asset_id).strip().upper(): holding.asset_name
                for holding in snapshot_holdings
                if holding.asset_id
            }
        )
        asset_type_map = {
            holding.asset_id: holding.asset_type
            for holding in snapshot_holdings
        }
        asset_type_map.update(
            {
                str(holding.asset_id).strip().upper(): holding.asset_type
                for holding in snapshot_holdings
                if holding.asset_id
            }
        )
        for code in supplemental:
            name_map.setdefault(code, code)

        try:
            from src.market_time import MarketTimeUtil

            any_open = (
                MarketTimeUtil.is_cn_market_open()
                or MarketTimeUtil.is_hk_market_open()
                or MarketTimeUtil.is_us_market_open()
            )
            accept_stale_when_closed = allow_stale_price_fallback and not any_open
            market_closed_ttl_multiplier = (
                price_market_closed_ttl_multiplier if not any_open else 1.0
            )
        except Exception:
            accept_stale_when_closed = False
            market_closed_ttl_multiplier = 1.0

        effective_deadline = (
            deadline
            if deadline is not None
            else time.monotonic() + max(0.0, float(price_timeout_seconds))
        )
        fetch_kwargs = {
            "name_map": name_map,
            "asset_type_map": asset_type_map,
            "market_closed_ttl_multiplier": market_closed_ttl_multiplier,
            "accept_stale_when_closed": accept_stale_when_closed,
            "use_concurrent": True,
            "skip_us": False,
            "deadline": effective_deadline,
        }
        codes = list(
            dict.fromkeys(
                [holding.asset_id for holding in snapshot_holdings] + supplemental
            )
        )
        try:
            if run_quote_pool is None:
                prices = self.price_fetcher.fetch_batch(codes, **fetch_kwargs)
            else:
                prices = run_quote_pool.fetch_batch(
                    codes,
                    fetch_batch=self.price_fetcher.fetch_batch,
                    **fetch_kwargs,
                )
        except TimeoutError:
            return {}, [f"价格获取超时（{price_timeout_seconds}秒）"]
        except Exception as exc:
            return {}, [f"价格获取异常: {type(exc).__name__}"]

        warnings: list[str] = []
        if time.monotonic() >= effective_deadline:
            warnings.append(f"价格获取达到全局 deadline（{price_timeout_seconds}秒）")
        return dict(prices or {}), warnings
