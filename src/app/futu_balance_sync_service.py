"""Synchronize Futu cash, MMF, stock/ETF quantities, and average costs."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from math import isfinite
from typing import Any, Dict, Optional, Protocol, Sequence

from src import config
from src.models import (
    AssetClass,
    AssetType,
    CASH_ASSET_ID,
    MMF_ASSET_ID,
    Holding,
)
from .cash_service import CashService


@dataclass(frozen=True)
class FutuBalanceSnapshot:
    """Absolute cash-like balances fetched from Futu."""

    cash: Optional[float] = None
    mmf: Optional[float] = None
    currency: str = "CNY"
    source: str = "futu"


@dataclass(frozen=True)
class FutuPositionSnapshot:
    """One raw Futu position with its quote security classification."""

    asset_id: str
    asset_name: str
    security_type: str
    quantity: float
    average_cost: Optional[float]
    currency: str
    market: str
    position_side: str = "LONG"
    raw_code: str = ""


@dataclass(frozen=True)
class FutuPortfolioSnapshot:
    """Complete cash-like and position snapshot for one Futu account."""

    cash: Optional[float] = None
    mmf: Optional[float] = None
    positions: tuple[FutuPositionSnapshot, ...] = ()
    currency: str = "CNY"
    source: str = "futu"


class FutuBalanceProvider(Protocol):
    def fetch_balances(self) -> FutuBalanceSnapshot:
        """Return absolute Futu cash/MMF balances."""


class FutuPortfolioProvider(Protocol):
    def fetch_portfolio(self) -> FutuPortfolioSnapshot:
        """Return a complete Futu portfolio snapshot."""


@dataclass(frozen=True)
class FutuBalanceSyncItem:
    asset_id: str
    asset_name: str
    current: float
    target: float
    delta: float
    created: bool
    updated: bool
    fields_changed: bool
    field_updates: Dict[str, Any]


@dataclass(frozen=True)
class FutuHoldingSyncItem:
    asset_id: str
    asset_name: str
    security_type: str
    action: str
    current_quantity: float
    target_quantity: float
    quantity_changed: bool
    current_avg_cost: Optional[float]
    target_avg_cost: Optional[float]
    cost_changed: bool
    cost_source: str
    currency: str


class FutuOpenApiBalanceProvider:
    """Minimal Futu OpenAPI adapter.

    The ``futu`` SDK and Futu OpenD are optional runtime dependencies. Tests
    should inject a provider instead of constructing this adapter.
    """

    CASH_COLUMNS = ("cash", "available_funds", "withdraw_cash", "power")
    MMF_COLUMNS = ("fund_assets",)

    def __init__(
        self,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        trd_env: Optional[str] = None,
        acc_id: Optional[int] = None,
        trd_market: Optional[str] = None,
        cash_currency: Optional[str] = None,
    ):
        self.host = host or config.get("futu.opend.host", "127.0.0.1")
        self.port = int(port if port is not None else (config.get_int("futu.opend.port", 11111) or 11111))
        self.trd_env = trd_env or config.get("futu.trd_env", "REAL")
        self.acc_id = int(acc_id) if acc_id is not None else config.get_int("futu.acc_id")
        self.trd_market = trd_market or config.get("futu.trd_market", "HK")
        self.cash_currency = cash_currency or config.get("futu.cash_currency", "CNH")

    def fetch_balances(self) -> FutuBalanceSnapshot:
        futu_sdk = self._import_sdk()
        ctx = self._open_trade_context(futu_sdk)
        try:
            row = self._fetch_accinfo_row(futu_sdk, ctx)
        finally:
            self._close(ctx)

        return FutuBalanceSnapshot(
            cash=self._cash_from_row(row),
            mmf=self._mmf_from_row(row),
            currency="CNY",
            source="futu-openapi",
        )

    def fetch_portfolio(self) -> FutuPortfolioSnapshot:
        futu_sdk = self._import_sdk()
        trade_ctx = self._open_trade_context(futu_sdk)
        quote_ctx = None
        try:
            account_row = self._fetch_accinfo_row(futu_sdk, trade_ctx)
            position_rows = self._fetch_position_rows(futu_sdk, trade_ctx)
            if position_rows:
                quote_ctx = futu_sdk.OpenQuoteContext(host=self.host, port=self.port)
                security_types = self._fetch_security_types(futu_sdk, quote_ctx, position_rows)
            else:
                security_types = {}
            positions = tuple(
                self._position_snapshot(row, security_types.get(str(row.get("code") or "")))
                for row in position_rows
            )
        finally:
            self._close(quote_ctx)
            self._close(trade_ctx)

        return FutuPortfolioSnapshot(
            cash=self._cash_from_row(account_row),
            mmf=self._mmf_from_row(account_row),
            positions=positions,
            currency="CNY",
            source="futu-openapi",
        )

    @staticmethod
    def _import_sdk() -> Any:
        try:
            import futu as futu_sdk
        except ImportError as exc:
            try:
                import moomoo as futu_sdk
            except ImportError:
                raise RuntimeError("未安装 futu/moomoo SDK；请安装 Futu OpenAPI SDK 并启动 OpenD，或注入自定义 provider") from exc
        return futu_sdk

    def _fetch_cash(self, futu_sdk: Any, ctx: Any) -> Optional[float]:
        return self._cash_from_row(self._fetch_accinfo_row(futu_sdk, ctx))

    def _fetch_mmf(self, futu_sdk: Any, ctx: Any) -> Optional[float]:
        return self._mmf_from_row(self._fetch_accinfo_row(futu_sdk, ctx))

    def _cash_from_row(self, row: dict[str, Any]) -> Optional[float]:
        for column in self.CASH_COLUMNS:
            value = row.get(column)
            if value is not None:
                return float(value)
        return None

    def _mmf_from_row(self, row: dict[str, Any]) -> Optional[float]:
        for column in self.MMF_COLUMNS:
            value = row.get(column)
            if value is not None:
                return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        return None

    def _fetch_accinfo_row(self, futu_sdk: Any, ctx: Any) -> dict[str, Any]:
        kwargs = self._accinfo_kwargs(futu_sdk)
        try:
            ret, data = ctx.accinfo_query(**kwargs)
        except TypeError:
            kwargs.pop("currency", None)
            ret, data = ctx.accinfo_query(**kwargs)
        self._ensure_ok(futu_sdk, ret, data, "accinfo_query")
        return _first_row(data)

    def _fetch_position_rows(self, futu_sdk: Any, ctx: Any) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "trd_env": self._enum_value(futu_sdk, "TrdEnv", self.trd_env),
            "refresh_cache": True,
        }
        if self.acc_id is not None:
            kwargs["acc_id"] = self.acc_id
        currency_none = self._enum_value(futu_sdk, "Currency", "NONE")
        if currency_none is not None:
            kwargs["currency"] = currency_none
        try:
            ret, data = ctx.position_list_query(**kwargs)
        except TypeError:
            kwargs.pop("currency", None)
            ret, data = ctx.position_list_query(**kwargs)
        self._ensure_ok(futu_sdk, ret, data, "position_list_query")
        return _rows(data)

    def _fetch_security_types(
        self,
        futu_sdk: Any,
        quote_ctx: Any,
        position_rows: Sequence[dict[str, Any]],
    ) -> dict[str, str]:
        codes_by_market: dict[str, list[str]] = {}
        for row in position_rows:
            raw_code = str(row.get("code") or "").strip()
            if not raw_code:
                continue
            market = _market_from_code(raw_code, row.get("position_market"))
            codes_by_market.setdefault(market, []).append(raw_code)

        result: dict[str, str] = {}
        for market, codes in codes_by_market.items():
            market_enum = self._enum_value(futu_sdk, "Market", market)
            security_none = self._enum_value(futu_sdk, "SecurityType", "NONE")
            ret, data = quote_ctx.get_stock_basicinfo(
                market=market_enum,
                stock_type=security_none,
                code_list=sorted(set(codes)),
            )
            self._ensure_ok(futu_sdk, ret, data, f"get_stock_basicinfo[{market}]")
            for row in _rows(data):
                code = str(row.get("code") or "").strip()
                if code:
                    result[code] = str(row.get("stock_type") or "N/A").upper()

        missing_nonzero = [
            str(row.get("code") or "")
            for row in position_rows
            if _to_float(row.get("qty"), default=0.0) != 0
            and str(row.get("code") or "") not in result
        ]
        if missing_nonzero:
            raise RuntimeError(f"Futu security classification missing for: {', '.join(sorted(missing_nonzero))}")
        return result

    def _position_snapshot(self, row: dict[str, Any], security_type: Optional[str]) -> FutuPositionSnapshot:
        raw_code = str(row.get("code") or "").strip()
        market = _market_from_code(raw_code, row.get("position_market"))
        return FutuPositionSnapshot(
            asset_id=_normalize_futu_code(raw_code),
            asset_name=str(row.get("stock_name") or _normalize_futu_code(raw_code)),
            security_type=str(security_type or "N/A").upper(),
            quantity=_to_float(row.get("qty"), default=0.0),
            average_cost=_optional_float(row.get("average_cost")),
            currency=_normalize_currency(row.get("currency"), market),
            market=market,
            position_side=str(row.get("position_side") or "N/A").upper(),
            raw_code=raw_code,
        )

    def _open_trade_context(self, futu_sdk: Any) -> Any:
        kwargs = {"host": self.host, "port": self.port}
        trd_market = self._enum_value(futu_sdk, "TrdMarket", self.trd_market)
        if trd_market is not None:
            kwargs["filter_trdmarket"] = trd_market
        return futu_sdk.OpenSecTradeContext(**kwargs)

    def _accinfo_kwargs(self, futu_sdk: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        kwargs["trd_env"] = self._enum_value(futu_sdk, "TrdEnv", self.trd_env)
        kwargs["currency"] = self._enum_value(futu_sdk, "Currency", self.cash_currency)
        if self.acc_id is not None:
            kwargs["acc_id"] = self.acc_id
        return kwargs

    @staticmethod
    def _enum_value(futu_sdk: Any, enum_name: str, value: str) -> Any:
        enum_type = getattr(futu_sdk, enum_name, None)
        return getattr(enum_type, value, value) if enum_type is not None else value

    @staticmethod
    def _ensure_ok(futu_sdk: Any, ret: Any, data: Any, op: str) -> None:
        ok = getattr(futu_sdk, "RET_OK", 0)
        if ret != ok:
            raise RuntimeError(f"Futu {op} failed: {data}")

    @staticmethod
    def _close(ctx: Any) -> None:
        close = getattr(ctx, "close", None)
        if callable(close):
            close()


class FutuBalanceSyncService:
    MONEY_QUANT = Decimal("0.01")
    ELIGIBLE_SECURITY_TYPES = {"STOCK", "ETF"}
    ELIGIBLE_EXISTING_TYPES = {
        AssetType.A_STOCK,
        AssetType.HK_STOCK,
        AssetType.US_STOCK,
        AssetType.EXCHANGE_FUND,
        AssetType.CN_FUND,
        AssetType.HK_FUND,
        AssetType.US_FUND,
    }

    def __init__(self, storage: Any, provider: Optional[Any] = None):
        self.storage = storage
        self.provider = provider
        self.cash_service = CashService(storage)

    @classmethod
    def quantize_money(cls, value: Any) -> float:
        return float(Decimal(str(value or 0)).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP))

    def sync_cash_and_mmf(
        self,
        *,
        account: str,
        broker: str = "富途",
        dry_run: bool = False,
        cash_balance: Optional[float] = None,
        mmf_balance: Optional[float] = None,
    ) -> dict[str, Any]:
        snapshot = (
            FutuBalanceSnapshot(cash=cash_balance, mmf=mmf_balance)
            if cash_balance is not None or mmf_balance is not None
            else self._fetch_balances()
        )

        return self._sync_cash_snapshot(
            snapshot,
            account=account,
            broker=broker,
            dry_run=dry_run,
        )

    def sync_portfolio(
        self,
        *,
        account: str,
        broker: str = "富途",
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
    ) -> dict[str, Any]:
        if not dry_run and not confirm:
            return self._failure(account, broker, dry_run, "Futu holdings write requires confirm=True")
        if allow_empty_stock_snapshot and not confirm:
            return self._failure(account, broker, dry_run, "allow-empty-stock-snapshot requires confirm=True")

        try:
            snapshot = self._fetch_portfolio()
            items, replacements = self._build_position_diff(
                snapshot.positions,
                account=account,
                broker=broker,
                allow_empty_stock_snapshot=allow_empty_stock_snapshot,
            )
        except Exception as exc:
            return self._failure(account, broker, dry_run, str(exc))

        summary = {
            "created": sum(item.action == "create" for item in items),
            "updated": sum(item.action == "update" for item in items),
            "zeroed": sum(item.action == "zero" for item in items),
            "unchanged": sum(item.action == "unchanged" for item in items),
            "quantity_changed": sum(item.quantity_changed for item in items),
            "cost_changed": sum(item.cost_changed for item in items),
        }
        write_stage = "positions"
        positions_written = False
        try:
            if not dry_run and replacements:
                self.storage.upsert_holdings_bulk(replacements, mode="replace")
                positions_written = True

            write_stage = "cash_mmf"
            cash_result = self._sync_cash_snapshot(
                FutuBalanceSnapshot(
                    cash=snapshot.cash,
                    mmf=snapshot.mmf,
                    currency=snapshot.currency,
                    source=snapshot.source,
                ),
                account=account,
                broker=broker,
                dry_run=dry_run,
            )
        except Exception as exc:
            failure = self._failure(account, broker, dry_run, str(exc))
            failure.update({
                "write_stage": write_stage,
                "partial_write_possible": not dry_run,
                "positions": [item.__dict__ for item in items],
                "summary": summary,
            })
            return failure

        return {
            "success": bool(cash_result.get("success")),
            "status": "dry_run" if dry_run else "written",
            "account": account,
            "broker": broker,
            "dry_run": dry_run,
            "source": snapshot.source,
            "cash_mmf": cash_result,
            "positions": [item.__dict__ for item in items],
            "summary": summary,
        }

    def _sync_cash_snapshot(
        self,
        snapshot: FutuBalanceSnapshot,
        *,
        account: str,
        broker: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        items = []
        items.extend(self._sync_asset(
            account=account,
            broker=broker,
            asset_id=CASH_ASSET_ID,
            asset_name="人民币现金",
            asset_type=AssetType.CASH,
            target=snapshot.cash,
            dry_run=dry_run,
        ))
        items.extend(self._sync_asset(
            account=account,
            broker=broker,
            asset_id=MMF_ASSET_ID,
            asset_name="货币基金",
            asset_type=AssetType.MMF,
            target=snapshot.mmf,
            dry_run=dry_run,
        ))
        return {
            "success": True,
            "account": account,
            "broker": broker,
            "dry_run": dry_run,
            "source": snapshot.source,
            "items": [item.__dict__ for item in items],
            "updated": sum(1 for item in items if item.updated),
            "created": sum(1 for item in items if item.created),
        }

    def _build_position_diff(
        self,
        positions: Sequence[FutuPositionSnapshot],
        *,
        account: str,
        broker: str,
        allow_empty_stock_snapshot: bool,
    ) -> tuple[list[FutuHoldingSyncItem], list[Holding]]:
        eligible: dict[str, FutuPositionSnapshot] = {}
        for position in positions:
            if position.security_type not in self.ELIGIBLE_SECURITY_TYPES:
                continue
            if position.position_side == "SHORT" or position.quantity < 0:
                raise ValueError(f"short stock/ETF position blocks sync: {position.raw_code or position.asset_id}")
            if position.quantity == 0:
                continue
            if position.position_side != "LONG":
                raise ValueError(f"unknown position side blocks sync: {position.raw_code or position.asset_id}={position.position_side}")
            if not position.asset_id:
                raise ValueError(f"empty normalized Futu code: {position.raw_code}")
            if position.asset_id in eligible:
                raise ValueError(f"duplicate normalized Futu position: {position.asset_id}")
            if position.average_cost is None or not isfinite(position.average_cost) or position.average_cost < 0:
                raise ValueError(f"valid Futu average_cost required for non-zero position: {position.raw_code or position.asset_id}")
            eligible[position.asset_id] = position

        existing_rows = [
            holding for holding in self.storage.get_holdings(account=account, include_empty=True)
            if (holding.broker or "") == broker and holding.asset_type in self.ELIGIBLE_EXISTING_TYPES
        ]
        existing = {holding.asset_id: holding for holding in existing_rows}
        existing_nonzero = [holding.asset_id for holding in existing_rows if holding.quantity != 0]
        if not eligible and existing_nonzero and not allow_empty_stock_snapshot:
            raise ValueError(
                "empty eligible Futu stock snapshot would zero existing positions; "
                "re-run with allow_empty_stock_snapshot=True and confirm=True after manual verification"
            )

        items: list[FutuHoldingSyncItem] = []
        replacements: list[Holding] = []
        for asset_id in sorted(set(existing) | set(eligible)):
            current = existing.get(asset_id)
            target = eligible.get(asset_id)
            current_quantity = float(current.quantity if current else 0)
            target_quantity = float(target.quantity if target else 0)
            current_cost = current.avg_cost if current else None
            target_cost = self.quantize_money(target.average_cost) if target else None
            quantity_changed = _decimal(current_quantity) != _decimal(target_quantity)
            cost_changed = current_cost != target_cost

            if current is None:
                action = "create"
            elif target is None and (quantity_changed or cost_changed):
                action = "zero"
            elif quantity_changed or cost_changed:
                action = "update"
            else:
                action = "unchanged"

            if target is not None:
                target_type, target_currency, target_asset_class = _target_descriptor(target)
                asset_type = current.asset_type if current else target_type
                currency = current.currency if current else target_currency
                asset_class = current.asset_class if current else target_asset_class
                asset_name = current.asset_name if current else target.asset_name
                security_type = target.security_type
            else:
                asset_type = current.asset_type
                currency = current.currency
                asset_class = current.asset_class
                asset_name = current.asset_name
                security_type = "STOCK" if current.asset_type != AssetType.EXCHANGE_FUND else "ETF"

            items.append(FutuHoldingSyncItem(
                asset_id=asset_id,
                asset_name=asset_name,
                security_type=security_type,
                action=action,
                current_quantity=current_quantity,
                target_quantity=target_quantity,
                quantity_changed=quantity_changed,
                current_avg_cost=current_cost,
                target_avg_cost=target_cost,
                cost_changed=cost_changed,
                cost_source="average_cost",
                currency=currency,
            ))
            if action != "unchanged":
                replacements.append(Holding(
                    record_id=current.record_id if current else None,
                    asset_id=asset_id,
                    asset_name=asset_name,
                    asset_type=asset_type,
                    account=account,
                    broker=broker,
                    quantity=target_quantity,
                    avg_cost=target_cost,
                    currency=currency,
                    asset_class=current.asset_class if current else asset_class,
                    industry=current.industry if current else None,
                    tag=list(current.tag or []) if current else [],
                    created_at=current.created_at if current else None,
                    updated_at=current.updated_at if current else None,
                ))

        return items, replacements

    def _fetch_balances(self) -> FutuBalanceSnapshot:
        provider = self.provider or FutuOpenApiBalanceProvider()
        return provider.fetch_balances()

    def _fetch_portfolio(self) -> FutuPortfolioSnapshot:
        provider = self.provider or FutuOpenApiBalanceProvider()
        fetch = getattr(provider, "fetch_portfolio", None)
        if not callable(fetch):
            raise RuntimeError("Futu portfolio provider does not implement fetch_portfolio()")
        return fetch()

    def _sync_asset(
        self,
        *,
        account: str,
        broker: str,
        asset_id: str,
        asset_name: str,
        asset_type: AssetType,
        target: Optional[float],
        dry_run: bool,
    ) -> list[FutuBalanceSyncItem]:
        if target is None:
            return []

        synced = self.cash_service.sync_cash_like_balance(
            account=account,
            asset_id=asset_id,
            asset_name=asset_name,
            asset_type=asset_type,
            target=target,
            broker=broker,
            dry_run=dry_run,
        )

        return [FutuBalanceSyncItem(
            asset_id=synced["asset_id"],
            asset_name=synced["asset_name"],
            current=synced["current"],
            target=synced["target"],
            delta=synced["delta"],
            created=synced["created"],
            updated=synced["updated"],
            fields_changed=bool(synced.get("fields_changed")),
            field_updates=dict(synced.get("field_updates") or {}),
        )]

    @staticmethod
    def _failure(account: str, broker: str, dry_run: bool, error: str) -> dict[str, Any]:
        return {
            "success": False,
            "status": "failed",
            "account": account,
            "broker": broker,
            "dry_run": dry_run,
            "error": error,
        }


def _target_descriptor(position: FutuPositionSnapshot) -> tuple[AssetType, str, AssetClass]:
    market = position.market.upper()
    currency = _normalize_currency(position.currency, market)
    if position.security_type == "ETF":
        asset_type = AssetType.EXCHANGE_FUND
    elif market == "HK":
        asset_type = AssetType.HK_STOCK
    elif market == "US":
        asset_type = AssetType.US_STOCK
    elif market in {"SH", "SZ", "CN"}:
        asset_type = AssetType.A_STOCK
    else:
        raise ValueError(f"unsupported Futu market for stock sync: {market}")

    if currency == "USD":
        asset_class = AssetClass.US_ASSET
    elif currency == "HKD":
        asset_class = AssetClass.HK_ASSET
    else:
        asset_class = AssetClass.CN_ASSET
    return asset_type, currency, asset_class


def _normalize_futu_code(code: str) -> str:
    value = str(code or "").strip()
    if "." not in value:
        return value
    market, symbol = value.split(".", 1)
    if market.upper() in {"HK", "US", "SH", "SZ", "CN", "SG", "JP", "AU", "CA", "MY"}:
        return symbol
    return value


def _market_from_code(code: str, fallback: Any = None) -> str:
    value = str(code or "").strip()
    if "." in value:
        return value.split(".", 1)[0].upper()
    fallback_value = str(fallback or "").strip().upper()
    return fallback_value if fallback_value and fallback_value != "N/A" else "US"


def _normalize_currency(value: Any, market: str) -> str:
    currency = str(value or "").strip().upper()
    if currency == "CNH":
        return "CNY"
    if currency and currency != "N/A":
        return currency
    if market == "HK":
        return "HKD"
    if market == "US":
        return "USD"
    return "CNY"


def _optional_float(value: Any) -> Optional[float]:
    if value in (None, "", "N/A"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _to_float(value: Any, *, default: float) -> float:
    result = _optional_float(value)
    return default if result is None else result


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("NaN")


def _rows(data: Any) -> list[dict[str, Any]]:
    if hasattr(data, "to_dict"):
        return data.to_dict("records")
    if isinstance(data, list):
        return [dict(row) for row in data]
    if isinstance(data, dict):
        return [data]
    return []


def _first_row(data: Any) -> dict[str, Any]:
    rows = _rows(data)
    return rows[0] if rows else {}
