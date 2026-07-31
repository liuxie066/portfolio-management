"""Observe Futu CASH and synchronize MMF plus stock/ETF holdings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import uuid
from math import isfinite
from typing import Any, Dict, Optional, Protocol, Sequence

from src import config
from src.models import (
    AssetClass,
    AssetType,
    MMF_ASSET_ID,
    Holding,
)
from src.process_lock import account_lock_key, process_lock

from .cash_service import CashService
from .futu_sync_evidence import FutuSyncEvidenceStore
from .futu_sync_reconciler import FutuSyncReconciler


@dataclass(frozen=True)
class FutuBalanceSnapshot:
    """Authoritative observe-only per-currency cash plus the MMF balance."""

    cash_by_currency: Dict[str, Optional[float]] | None = None
    mmf: Optional[float] = None
    source: str = "futu"
    account_id: Optional[int] = None
    profile_fingerprint: Optional[str] = None
    cash_source_fields: Dict[str, str] | None = None
    cash_present_by_currency: Dict[str, bool] | None = None
    mmf_source_field: Optional[str] = None
    mmf_present: bool = False
    source_snapshot_id: Optional[str] = None
    observed_at_utc: Optional[str] = None
    account_fingerprint: Optional[str] = None
    trd_env: Optional[str] = None
    trd_market: Optional[str] = None
    refresh_cache: bool = True
    account_verified: bool = False
    pagination_complete: bool = False


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
    currency_explicit: bool = True


@dataclass(frozen=True)
class FutuPortfolioSnapshot:
    """Complete positions, MMF, and observe-only cash snapshot."""

    cash_by_currency: Dict[str, Optional[float]] | None = None
    mmf: Optional[float] = None
    positions: tuple[FutuPositionSnapshot, ...] = ()
    source: str = "futu"
    account_id: Optional[int] = None
    profile_fingerprint: Optional[str] = None
    cash_source_fields: Dict[str, str] | None = None
    cash_present_by_currency: Dict[str, bool] | None = None
    mmf_source_field: Optional[str] = None
    mmf_present: bool = False
    source_snapshot_id: Optional[str] = None
    observed_at_utc: Optional[str] = None
    account_fingerprint: Optional[str] = None
    trd_env: Optional[str] = None
    trd_market: Optional[str] = None
    refresh_cache: bool = True
    account_verified: bool = False
    pagination_complete: bool = False


class FutuBalanceProvider(Protocol):
    def fetch_balances(self) -> FutuBalanceSnapshot:
        """Return absolute Futu cash observations and the MMF balance."""


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
    projected_fields: Dict[str, Any]


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

    CASH_COLUMNS = {
        "CNY": "cn_cash",
        "USD": "us_cash",
        "HKD": "hk_cash",
    }
    MMF_COLUMNS = ("fund_assets",)

    def __init__(
        self,
        *,
        host: Optional[str] = None,
        port: Optional[int] = None,
        trd_env: Optional[str] = None,
        acc_id: Optional[int] = None,
        trd_market: Optional[str] = None,
        account_fingerprint: Optional[str] = None,
        verify_account: bool = False,
    ):
        self.host = host or config.get("futu.opend.host", "127.0.0.1")
        self.port = int(port if port is not None else (config.get_int("futu.opend.port", 11111) or 11111))
        self.trd_env = trd_env or config.get("futu.trd_env", "REAL")
        self.acc_id = int(acc_id) if acc_id is not None else config.get_int("futu.acc_id")
        self.trd_market = trd_market or config.get("futu.trd_market", "HK")
        self.verify_account = verify_account
        self.account_fingerprint = account_fingerprint or (
            f"sha256:{hashlib.sha256(str(self.acc_id).encode()).hexdigest()}"
            if self.acc_id is not None
            else None
        )

    @classmethod
    def from_account(cls, account: str) -> "FutuOpenApiBalanceProvider":
        settings = config.get_futu_profile(account)
        return cls(
            host=settings["host"],
            port=settings["port"],
            acc_id=settings["acc_id"],
            trd_env=settings["trd_env"],
            trd_market=settings["trd_market"],
            verify_account=True,
        )

    @property
    def profile_fingerprint(self) -> str:
        payload = {
            "host": self.host,
            "port": self.port,
            "acc_id": self.acc_id,
            "trd_env": self.trd_env,
            "trd_market": self.trd_market,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"

    def discover_accounts(self) -> dict[str, Any]:
        """Return only the OpenD authority fields needed for explicit mapping."""
        futu_sdk = self._import_sdk()
        ctx = self._open_trade_context(futu_sdk)
        try:
            query = getattr(ctx, "get_acc_list", None)
            if not callable(query):
                raise RuntimeError("Futu account list query is unavailable")
            ret, data = query()
            self._ensure_ok(futu_sdk, ret, data, "get_acc_list")
            rows = _rows(data)
        finally:
            self._close(ctx)

        if not rows:
            raise RuntimeError("Futu account list is empty")

        accounts: list[dict[str, Any]] = []
        seen_acc_ids: set[int] = set()
        for row in rows:
            acc_id = _optional_int(row.get("acc_id"))
            trd_env = str(row.get("trd_env") or "").strip().upper()
            if acc_id is None or acc_id <= 0 or not trd_env:
                raise RuntimeError("Futu account list contains incomplete authority fields")
            if acc_id in seen_acc_ids:
                raise RuntimeError("Futu account list contains duplicate account IDs")
            seen_acc_ids.add(acc_id)
            account = {
                "acc_id": acc_id,
                "account_fingerprint": (
                    f"sha256:{hashlib.sha256(str(acc_id).encode()).hexdigest()}"
                ),
                "trd_env": trd_env,
                "trd_market": str(self.trd_market).upper(),
            }
            accounts.append(account)

        return {
            "success": True,
            "read_only": True,
            "contains_sensitive_identifiers": True,
            "do_not_log_or_commit": True,
            "observed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "trd_market": str(self.trd_market).upper(),
            "accounts": sorted(accounts, key=lambda item: int(item["acc_id"])),
        }

    def fetch_balances(self) -> FutuBalanceSnapshot:
        futu_sdk = self._import_sdk()
        ctx = self._open_trade_context(futu_sdk)
        try:
            self._verify_account_authority(futu_sdk, ctx)
            row = self._fetch_accinfo_row(futu_sdk, ctx)
        finally:
            self._close(ctx)

        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return FutuBalanceSnapshot(
            cash_by_currency=self._cash_balances_from_row(row),
            mmf=self._mmf_from_row(row),
            source="futu-openapi",
            account_id=self.acc_id,
            profile_fingerprint=self.profile_fingerprint,
            cash_source_fields=dict(self.CASH_COLUMNS),
            cash_present_by_currency=self._cash_presence_from_row(row),
            mmf_source_field="fund_assets" if "fund_assets" in row else None,
            mmf_present="fund_assets" in row and row.get("fund_assets") not in (None, "", "N/A"),
            source_snapshot_id=f"futu-{uuid.uuid4().hex}",
            observed_at_utc=observed_at,
            account_fingerprint=self.account_fingerprint,
            trd_env=self.trd_env,
            trd_market=self.trd_market,
            refresh_cache=True,
            account_verified=bool(self.verify_account),
            pagination_complete=True,
        )

    def fetch_portfolio(self) -> FutuPortfolioSnapshot:
        futu_sdk = self._import_sdk()
        trade_ctx = self._open_trade_context(futu_sdk)
        quote_ctx = None
        try:
            self._verify_account_authority(futu_sdk, trade_ctx)
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

        observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return FutuPortfolioSnapshot(
            cash_by_currency=self._cash_balances_from_row(account_row),
            mmf=self._mmf_from_row(account_row),
            positions=positions,
            source="futu-openapi",
            account_id=self.acc_id,
            profile_fingerprint=self.profile_fingerprint,
            cash_source_fields=dict(self.CASH_COLUMNS),
            cash_present_by_currency=self._cash_presence_from_row(account_row),
            mmf_source_field="fund_assets" if "fund_assets" in account_row else None,
            mmf_present="fund_assets" in account_row and account_row.get("fund_assets") not in (None, "", "N/A"),
            source_snapshot_id=f"futu-{uuid.uuid4().hex}",
            observed_at_utc=observed_at,
            account_fingerprint=self.account_fingerprint,
            trd_env=self.trd_env,
            trd_market=self.trd_market,
            refresh_cache=True,
            account_verified=bool(self.verify_account),
            pagination_complete=True,
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

    def _fetch_cash_balances(self, futu_sdk: Any, ctx: Any) -> Dict[str, Optional[float]]:
        return self._cash_balances_from_row(self._fetch_accinfo_row(futu_sdk, ctx))

    def _fetch_mmf(self, futu_sdk: Any, ctx: Any) -> Optional[float]:
        return self._mmf_from_row(self._fetch_accinfo_row(futu_sdk, ctx))

    def _cash_balances_from_row(self, row: dict[str, Any]) -> Dict[str, Optional[float]]:
        return {
            currency: self._money_field(row, column, quantize=False)
            for currency, column in self.CASH_COLUMNS.items()
        }

    def _cash_presence_from_row(self, row: dict[str, Any]) -> Dict[str, bool]:
        return {
            currency: (
                column in row and row.get(column) not in (None, "", "N/A")
            )
            for currency, column in self.CASH_COLUMNS.items()
        }

    def _mmf_from_row(self, row: dict[str, Any]) -> Optional[float]:
        return self._money_field(row, "fund_assets", quantize=True)

    @staticmethod
    def _money_field(row: dict[str, Any], field: str, *, quantize: bool) -> Optional[float]:
        if field not in row or row.get(field) in (None, "", "N/A"):
            return None
        try:
            value = Decimal(str(row[field]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise RuntimeError(f"Futu {field} is invalid") from exc
        if not value.is_finite():
            raise RuntimeError(f"Futu {field} is invalid")
        if quantize:
            value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(value)

    def _verify_account_authority(self, futu_sdk: Any, ctx: Any) -> None:
        if not self.verify_account:
            return
        if self.acc_id is None:
            raise RuntimeError("explicit Futu acc_id is required")
        query = getattr(ctx, "get_acc_list", None)
        if not callable(query):
            raise RuntimeError("Futu account list query is unavailable")
        ret, data = query()
        self._ensure_ok(futu_sdk, ret, data, "get_acc_list")
        matches = [row for row in _rows(data) if _optional_int(row.get("acc_id")) == self.acc_id]
        if len(matches) != 1:
            raise RuntimeError("configured Futu account is not uniquely present")
        actual_env = str(matches[0].get("trd_env") or "").upper()
        if actual_env and actual_env != str(self.trd_env).upper():
            raise RuntimeError("configured Futu account environment mismatch")

    def _fetch_accinfo_row(self, futu_sdk: Any, ctx: Any) -> dict[str, Any]:
        if self.acc_id is None:
            raise ValueError(
                "Futu acc_id is required for authoritative cash observations"
            )
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
        raw_currency = str(row.get("currency") or "").strip().upper()
        currency_explicit = bool(raw_currency and raw_currency != "N/A")
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
            currency_explicit=currency_explicit,
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
        market = str(self.trd_market or "").strip().upper()
        currency_by_market = {"HK": "HKD", "US": "USD"}
        currency = currency_by_market.get(market)
        if currency is None:
            raise ValueError(
                f"unsupported Futu trade market for account info query: {self.trd_market}"
            )
        kwargs["currency"] = self._enum_value(futu_sdk, "Currency", currency)
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
    STOCK_SYNC_ASSET_TYPES = {
        AssetType.A_STOCK,
        AssetType.HK_STOCK,
        AssetType.US_STOCK,
        AssetType.EXCHANGE_FUND,
    }
    LEGACY_ETF_ASSET_TYPES = {
        AssetType.CN_FUND,
        AssetType.HK_FUND,
        AssetType.US_FUND,
    }

    def __init__(
        self,
        storage: Any,
        provider: Optional[Any] = None,
        evidence_store: Optional[FutuSyncEvidenceStore] = None,
        reconciler: Optional[FutuSyncReconciler] = None,
    ):
        self.storage = storage
        self.provider = provider
        self.cash_service = CashService(storage)
        self.evidence_store = evidence_store or FutuSyncEvidenceStore()
        self.reconciler = reconciler or FutuSyncReconciler(storage)

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
        sync_run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        resolved_run_id = sync_run_id or f"futu-sync-{uuid.uuid4().hex}"
        source_requested = cash_balance is None and mmf_balance is None
        try:
            snapshot = (
                FutuBalanceSnapshot(
                    cash_by_currency={
                        "CNY": cash_balance,
                        "USD": None,
                        "HKD": None,
                    },
                    mmf=mmf_balance,
                    source="manual-observation",
                )
                if not source_requested
                else self._fetch_balances(account)
            )
        except Exception as exc:
            failure = self._failure(account, broker, dry_run, str(exc))
            if source_requested and not dry_run and self.provider is None:
                return self._persist_failed_attempt(
                    failure,
                    account=account,
                    sync_run_id=resolved_run_id,
                    reason_code="FUTU_SOURCE_QUERY_FAILED",
                    phase="source_query",
                )
            return failure
        try:
            self._validate_authoritative_balances(snapshot)
        except Exception as exc:
            failure = self._failure(account, broker, dry_run, str(exc))
            if snapshot.source == "futu-openapi" and not dry_run:
                return self._persist_failed_attempt(
                    failure,
                    account=account,
                    sync_run_id=resolved_run_id,
                    reason_code="FUTU_SOURCE_VALIDATION_FAILED",
                    phase="source_validation",
                    snapshot=snapshot,
                )
            return failure

        with process_lock(account_lock_key(account)):
            result = self._sync_cash_snapshot(
                snapshot,
                account=account,
                broker=broker,
                dry_run=dry_run,
            )
        result["sync_run_id"] = resolved_run_id
        self._attach_reconciliation(
            snapshot,
            result,
            account=account,
            broker=broker,
            balances_only=True,
        )
        return self._persist_receipt_if_required(snapshot, result)

    def observe_portfolio(self, *, account: str) -> FutuPortfolioSnapshot:
        """Return one fresh, validated portfolio observation without any write.

        Holdings validation consumes this contract. It deliberately bypasses
        sync diffing, process locks, evidence persistence, and receipt delivery.
        """

        snapshot = self._fetch_portfolio(account)
        self._validate_authoritative_balances(snapshot)
        return snapshot

    def sync_portfolio(
        self,
        *,
        account: str,
        broker: str = "富途",
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
        sync_run_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if not dry_run and not confirm:
            return self._failure(account, broker, dry_run, "Futu holdings write requires confirm=True")
        if allow_empty_stock_snapshot and not confirm:
            return self._failure(account, broker, dry_run, "allow-empty-stock-snapshot requires confirm=True")

        resolved_run_id = sync_run_id or f"futu-sync-{uuid.uuid4().hex}"
        snapshot = None
        try:
            snapshot = self._fetch_portfolio(account)
            self._validate_authoritative_balances(snapshot)
        except Exception as exc:
            failure = self._failure(account, broker, dry_run, str(exc))
            if not dry_run and (
                self.provider is None
                or getattr(snapshot, "source", None) == "futu-openapi"
            ):
                return self._persist_failed_attempt(
                    failure,
                    account=account,
                    sync_run_id=resolved_run_id,
                    reason_code=(
                        "FUTU_SOURCE_QUERY_FAILED"
                        if snapshot is None
                        else "FUTU_SOURCE_VALIDATION_FAILED"
                    ),
                    phase="source_query" if snapshot is None else "source_validation",
                    snapshot=snapshot,
                )
            return failure

        with process_lock(account_lock_key(account)):
            try:
                items, replacements = self._build_position_diff(
                    snapshot.positions,
                    account=account,
                    broker=broker,
                    allow_empty_stock_snapshot=allow_empty_stock_snapshot,
                )
            except Exception as exc:
                failure = self._failure(account, broker, dry_run, str(exc))
                if snapshot.source == "futu-openapi" and not dry_run:
                    return self._persist_failed_attempt(
                        failure,
                        account=account,
                        sync_run_id=resolved_run_id,
                        reason_code="FUTU_POSITION_DIFF_INVALID",
                        phase="position_diff",
                        snapshot=snapshot,
                        stages={
                            "positions": {
                                "status": "failed",
                                "partial_write_possible": False,
                            },
                            "securities_cash": {
                                "status": "not_run",
                                "partial_write_possible": False,
                            },
                            "fund_mmf": {
                                "status": "not_run",
                                "partial_write_possible": False,
                            },
                        },
                    )
                return failure

            summary = {
                "created": sum(item.action == "create" for item in items),
                "updated": sum(item.action == "update" for item in items),
                "zeroed": sum(item.action == "zero" for item in items),
                "unchanged": sum(item.action == "unchanged" for item in items),
                "quantity_changed": sum(item.quantity_changed for item in items),
                "cost_changed": sum(item.cost_changed for item in items),
            }
            write_stage = "positions"
            stages = {
                "positions": {"status": "started", "partial_write_possible": False},
                "securities_cash": {"status": "pending", "partial_write_possible": False},
                "fund_mmf": {"status": "pending", "partial_write_possible": False},
            }
            try:
                if not dry_run and replacements:
                    self.storage.upsert_holdings_bulk(replacements, mode="replace")
                stages["positions"]["status"] = "succeeded"

                write_stage = "cash_mmf"
                cash_result = self._sync_cash_snapshot(
                    FutuBalanceSnapshot(
                        cash_by_currency=snapshot.cash_by_currency,
                        mmf=snapshot.mmf,
                        source=snapshot.source,
                        account_id=snapshot.account_id,
                        profile_fingerprint=snapshot.profile_fingerprint,
                        cash_source_fields=snapshot.cash_source_fields,
                        cash_present_by_currency=(
                            snapshot.cash_present_by_currency
                        ),
                        mmf_source_field=snapshot.mmf_source_field,
                        mmf_present=snapshot.mmf_present,
                        source_snapshot_id=snapshot.source_snapshot_id,
                        observed_at_utc=snapshot.observed_at_utc,
                        account_fingerprint=snapshot.account_fingerprint,
                        trd_env=snapshot.trd_env,
                        trd_market=snapshot.trd_market,
                        refresh_cache=snapshot.refresh_cache,
                        account_verified=snapshot.account_verified,
                        pagination_complete=snapshot.pagination_complete,
                    ),
                    account=account,
                    broker=broker,
                    dry_run=dry_run,
                )
            except Exception as exc:
                stages["positions"]["status"] = (
                    "failed" if write_stage == "positions" else stages["positions"]["status"]
                )
                if write_stage != "positions":
                    stages["securities_cash"]["status"] = "failed"
                failure = self._failure(account, broker, dry_run, str(exc))
                failure.update({
                    "write_stage": write_stage,
                    "partial_write_possible": not dry_run,
                    "sync_run_id": resolved_run_id,
                    "source_snapshot_id": snapshot.source_snapshot_id,
                    "source_metadata": self._public_source_metadata(snapshot),
                    "stages": stages,
                    "positions": [item.__dict__ for item in items],
                    "summary": summary,
                })
                self._attach_reconciliation(snapshot, failure, account=account, broker=broker)
                return self._persist_receipt_if_required(snapshot, failure)

            stages["securities_cash"] = dict(cash_result["stages"]["securities_cash"])
            stages["fund_mmf"] = dict(cash_result["stages"]["fund_mmf"])
            result = {
                "success": bool(cash_result.get("success")),
                "status": "dry_run" if dry_run else "written",
                "account": account,
                "broker": broker,
                "dry_run": dry_run,
                "source": snapshot.source,
                "source_snapshot_id": snapshot.source_snapshot_id,
                "sync_run_id": resolved_run_id,
                "source_metadata": self._public_source_metadata(snapshot),
                "stages": stages,
                "partial_write_possible": any(
                    stage["status"] == "failed" and not dry_run
                    for stage in stages.values()
                ),
                "cash_mmf": cash_result,
                "positions": [item.__dict__ for item in items],
                "summary": summary,
            }
            if not result["success"]:
                result["write_stage"] = "cash_mmf"
            self._attach_reconciliation(snapshot, result, account=account, broker=broker)
            return self._persist_receipt_if_required(snapshot, result)

    def _sync_cash_snapshot(
        self,
        snapshot: FutuBalanceSnapshot,
        *,
        account: str,
        broker: str,
        dry_run: bool,
    ) -> dict[str, Any]:
        items = []
        stages = {
            "securities_cash": {
                "status": "succeeded",
                "mode": "observe_only",
                "partial_write_possible": False,
            },
            "fund_mmf": {
                "status": "started",
                "partial_write_possible": False,
            },
        }
        try:
            items.extend(self._sync_asset(
                account=account,
                broker=broker,
                asset_id=MMF_ASSET_ID,
                asset_name="货币基金",
                asset_type=AssetType.MMF,
                target=snapshot.mmf,
                dry_run=dry_run,
            ))
            stages["fund_mmf"]["status"] = "succeeded"
        except Exception as exc:
            stages["fund_mmf"] = {
                "status": "failed",
                "partial_write_possible": not dry_run,
                "reason_code": "FUND_MMF_WRITE_FAILED",
            }
            return {
                "success": False,
                "account": account,
                "broker": broker,
                "dry_run": dry_run,
                "source": snapshot.source,
                "source_snapshot_id": snapshot.source_snapshot_id,
                "source_metadata": self._public_source_metadata(snapshot),
                "stages": stages,
                "partial_write_possible": not dry_run,
                "items": [item.__dict__ for item in items],
                "error": str(exc),
            }
        return {
            "success": True,
            "account": account,
            "broker": broker,
            "dry_run": dry_run,
            "source": snapshot.source,
            "source_snapshot_id": snapshot.source_snapshot_id,
            "source_metadata": self._public_source_metadata(snapshot),
            "stages": stages,
            "cash_mode": "observe_only",
            "cash_observations": dict(snapshot.cash_by_currency or {}),
            "account_id": snapshot.account_id,
            "profile_fingerprint": snapshot.profile_fingerprint,
            "items": [item.__dict__ for item in items],
            "updated": sum(1 for item in items if item.updated),
            "created": sum(1 for item in items if item.created),
        }

    def _persist_receipt_if_required(self, snapshot: Any, result: dict[str, Any]) -> dict[str, Any]:
        if snapshot.source != "futu-openapi" or result.get("dry_run"):
            return result
        receipt = {
            "schema_version": "pm.futu_sync_receipt.v1",
            "sync_run_id": result["sync_run_id"],
            "account": result["account"],
            "source_snapshot_id": snapshot.source_snapshot_id,
            "source_metadata": self._public_source_metadata(snapshot),
            "stages": result.get("stages", {}),
            "success": bool(result.get("success")),
            "partial_write_possible": bool(result.get("partial_write_possible")),
            "reconciliation": result.get("reconciliation"),
        }
        try:
            refs = self.evidence_store.save(result["account"], result["sync_run_id"], receipt)
        except Exception:
            result["success"] = False
            result["quality_status"] = "untrusted"
            result["receipt_persisted"] = False
            result["receipt_reason_code"] = "SYNC_RECEIPT_PERSIST_FAILED"
            return result
        result["receipt_persisted"] = True
        result["receipt_refs"] = refs
        return result

    def _persist_failed_attempt(
        self,
        result: dict[str, Any],
        *,
        account: str,
        sync_run_id: str,
        reason_code: str,
        phase: str,
        snapshot: Any | None = None,
        stages: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        attempted_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if snapshot is not None:
            try:
                source_metadata = self._public_source_metadata(snapshot)
            except Exception:
                source_metadata = {
                    "provider": "futu-openapi",
                    "observed_at_utc": attempted_at,
                    "evidence_complete": False,
                }
        else:
            source_metadata = {
                "provider": "futu-openapi",
                "observed_at_utc": attempted_at,
                "evidence_complete": False,
            }
        receipt = {
            "schema_version": "pm.futu_sync_receipt.v1",
            "sync_run_id": sync_run_id,
            "account": account,
            "source_snapshot_id": getattr(snapshot, "source_snapshot_id", None),
            "source_metadata": source_metadata,
            "stages": stages or {
                "source": {
                    "status": "failed",
                    "partial_write_possible": False,
                },
            },
            "success": False,
            "partial_write_possible": False,
            "reconciliation": None,
            "failure": {
                "reason_code": reason_code,
                "phase": phase,
            },
        }
        result.update(
            {
                "sync_run_id": sync_run_id,
                "source_snapshot_id": receipt["source_snapshot_id"],
                "source_metadata": source_metadata,
                "stages": receipt["stages"],
                "partial_write_possible": False,
                "failure_reason_code": reason_code,
            }
        )
        try:
            refs = self.evidence_store.save(account, sync_run_id, receipt)
        except Exception:
            result["quality_status"] = "untrusted"
            result["receipt_persisted"] = False
            result["receipt_reason_code"] = "SYNC_RECEIPT_PERSIST_FAILED"
            return result
        result["receipt_persisted"] = True
        result["receipt_refs"] = refs
        return result

    def _attach_reconciliation(
        self,
        snapshot: Any,
        result: dict[str, Any],
        *,
        account: str,
        broker: str,
        balances_only: bool = False,
    ) -> None:
        if snapshot.source != "futu-openapi" or result.get("dry_run"):
            return
        try:
            reconcile = (
                self.reconciler.reconcile_balances(snapshot, account=account, broker=broker)
                if balances_only
                else self.reconciler.reconcile(snapshot, account=account, broker=broker)
            )
        except Exception:
            reconcile = {
                "status": "unavailable",
                "reason_code": "REPOSITORY_READBACK_FAILED",
                "datasets": {},
            }
        result["reconciliation"] = reconcile
        if reconcile["status"] != "trusted":
            result["quality_status"] = reconcile["status"]
            datasets = dict(reconcile.get("datasets") or {})
            non_cash_untrusted = any(
                verdict.get("status") != "trusted"
                for dataset_id, verdict in datasets.items()
                if dataset_id != "pm.securities_cash"
            )
            if non_cash_untrusted or not datasets:
                result["success"] = False

    @staticmethod
    def _validate_authoritative_balances(snapshot: Any) -> None:
        if snapshot.source != "futu-openapi":
            return
        expected_fields = dict(FutuOpenApiBalanceProvider.CASH_COLUMNS)
        balances = dict(snapshot.cash_by_currency or {})
        source_fields = dict(snapshot.cash_source_fields or {})
        presence = dict(snapshot.cash_present_by_currency or {})
        if source_fields != expected_fields:
            raise RuntimeError(
                "authoritative Futu per-currency cash field evidence is invalid"
            )
        missing = [
            currency
            for currency in expected_fields
            if not presence.get(currency) or balances.get(currency) is None
        ]
        if missing:
            raise RuntimeError(
                "authoritative Futu per-currency cash fields are missing: "
                + ", ".join(missing)
            )
        if not snapshot.mmf_present or snapshot.mmf_source_field != "fund_assets" or snapshot.mmf is None:
            raise RuntimeError("authoritative Futu fund_assets field is missing")
        if str(snapshot.trd_env).upper() != "REAL":
            raise RuntimeError("Futu trading environment must be REAL")
        if snapshot.account_id is None:
            raise RuntimeError("Futu account ID evidence is missing")
        if not snapshot.profile_fingerprint:
            raise RuntimeError("Futu profile fingerprint is missing")
        if not snapshot.account_fingerprint:
            raise RuntimeError("Futu account fingerprint is missing")
        expected_account_fingerprint = (
            "sha256:"
            + hashlib.sha256(str(snapshot.account_id).encode()).hexdigest()
        )
        if snapshot.account_fingerprint != expected_account_fingerprint:
            raise RuntimeError("Futu account fingerprint mismatch")
        if not snapshot.source_snapshot_id:
            raise RuntimeError("Futu source snapshot identity is missing")
        if not snapshot.observed_at_utc:
            raise RuntimeError("Futu snapshot observation time is missing")
        try:
            observed_at = datetime.fromisoformat(
                str(snapshot.observed_at_utc).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RuntimeError("Futu snapshot observation time is invalid") from exc
        if observed_at.tzinfo is None:
            raise RuntimeError("Futu snapshot observation time is invalid")
        if not snapshot.trd_market:
            raise RuntimeError("Futu trading market is missing")
        if snapshot.refresh_cache is not True:
            raise RuntimeError("Futu snapshot must use refresh_cache=True")
        if snapshot.account_verified is not True:
            raise RuntimeError("Futu account authority was not verified")
        if snapshot.pagination_complete is not True:
            raise RuntimeError("Futu snapshot pagination is incomplete")

    @staticmethod
    def _public_source_metadata(snapshot: Any) -> dict[str, Any]:
        position_snapshot_included = hasattr(snapshot, "positions")
        return {
            "provider": snapshot.source,
            "source_snapshot_id": snapshot.source_snapshot_id,
            "observed_at_utc": snapshot.observed_at_utc,
            "account_fingerprint": snapshot.account_fingerprint,
            "profile_fingerprint": snapshot.profile_fingerprint,
            "trd_env": snapshot.trd_env,
            "trd_market": snapshot.trd_market,
            "cash": {
                "mode": "per_currency",
                "present": all(
                    (snapshot.cash_present_by_currency or {}).get(currency)
                    for currency in FutuOpenApiBalanceProvider.CASH_COLUMNS
                ),
                "source_fields": dict(snapshot.cash_source_fields or {}),
                "present_by_currency": dict(
                    snapshot.cash_present_by_currency or {}
                ),
            },
            "fund_mmf": {
                "present": snapshot.mmf_present,
                "source_field": snapshot.mmf_source_field,
            },
            "refresh_cache": snapshot.refresh_cache,
            "account_verified": snapshot.account_verified,
            "pagination_complete": snapshot.pagination_complete,
            "position_snapshot_included": position_snapshot_included,
            "position_count": (
                len(snapshot.positions) if position_snapshot_included else None
            ),
            "payload_sha256": FutuBalanceSyncService._snapshot_payload_sha256(snapshot),
        }

    @staticmethod
    def _snapshot_payload_sha256(snapshot: Any) -> str:
        positions = [
            {
                "asset_id": str(item.asset_id),
                "security_type": str(item.security_type),
                "quantity": str(item.quantity),
                "average_cost": None if item.average_cost is None else str(item.average_cost),
                "currency": str(item.currency),
                "market": str(item.market),
                "position_side": str(item.position_side),
            }
            for item in getattr(snapshot, "positions", ())
        ]
        positions.sort(
            key=lambda item: (
                item["market"],
                item["asset_id"],
                item["security_type"],
                item["position_side"],
            )
        )
        payload = {
            "account_fingerprint": snapshot.account_fingerprint,
            "profile_fingerprint": snapshot.profile_fingerprint,
            "trd_env": snapshot.trd_env,
            "trd_market": snapshot.trd_market,
            "cash_by_currency": {
                currency: None if value is None else str(value)
                for currency, value in sorted(
                    (snapshot.cash_by_currency or {}).items()
                )
            },
            "fund_mmf": None if snapshot.mmf is None else str(snapshot.mmf),
            "position_snapshot_included": hasattr(snapshot, "positions"),
            "positions": positions,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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
                raise ValueError(
                    f"unknown position side blocks sync: {position.raw_code or position.asset_id}={position.position_side}"
                )
            if not position.asset_id:
                raise ValueError(f"empty normalized Futu code: {position.raw_code}")
            if position.asset_id in eligible:
                raise ValueError(f"duplicate normalized Futu position: {position.asset_id}")
            if position.average_cost is None or not isfinite(position.average_cost) or position.average_cost < 0:
                raise ValueError(
                    f"valid Futu average_cost required for non-zero position: {position.raw_code or position.asset_id}"
                )
            eligible[position.asset_id] = position

        matchable_types = self.STOCK_SYNC_ASSET_TYPES | self.LEGACY_ETF_ASSET_TYPES
        existing: dict[str, Holding] = {}
        for holding in self.storage.get_holdings(account=account, include_empty=True):
            if (holding.broker or "") != broker or holding.asset_type not in matchable_types:
                continue
            if holding.asset_id in existing:
                raise ValueError(f"duplicate existing Futu holding: {holding.asset_id}")
            existing[holding.asset_id] = holding

        canonical_nonzero = [
            holding.asset_id
            for holding in existing.values()
            if holding.asset_type in self.STOCK_SYNC_ASSET_TYPES and holding.quantity != 0
        ]
        if not eligible and canonical_nonzero and not allow_empty_stock_snapshot:
            raise ValueError(
                "empty eligible Futu stock snapshot would zero existing positions; "
                "re-run with allow_empty_stock_snapshot=True and confirm=True after manual verification"
            )

        items: list[FutuHoldingSyncItem] = []
        replacements: list[Holding] = []

        def append_item(asset_id: str, current: Optional[Holding], target: Optional[FutuPositionSnapshot]) -> None:
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
                security_type = "ETF" if current.asset_type == AssetType.EXCHANGE_FUND else "STOCK"

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

        matched: set[str] = set()
        for asset_id in sorted(eligible):
            current = existing.get(asset_id)
            if current is not None:
                matched.add(asset_id)
            append_item(asset_id, current, eligible[asset_id])

        for asset_id in sorted(set(existing) - matched):
            current = existing[asset_id]
            if current.asset_type in self.STOCK_SYNC_ASSET_TYPES:
                append_item(asset_id, current, None)

        return items, replacements

    def _fetch_balances(self, account: str) -> FutuBalanceSnapshot:
        provider = self.provider or FutuOpenApiBalanceProvider.from_account(account)
        return provider.fetch_balances()

    def _fetch_portfolio(self, account: str) -> FutuPortfolioSnapshot:
        provider = self.provider or FutuOpenApiBalanceProvider.from_account(account)
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
            projected_fields=dict(synced.get("projected_fields") or {}),
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


def _optional_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
