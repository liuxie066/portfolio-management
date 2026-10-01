"""Service facade for portfolio-management use cases.

This layer gives HTTP, CLI, and future workers one application boundary.
`skill_api.py` remains a caller-facing adapter and is not a service dependency.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.pricing.payload import quantize_money


LOGGER = logging.getLogger(__name__)


def _positive_amount(value: Any) -> bool:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return amount.is_finite() and amount > 0


def _finite_amount(value: Any) -> bool:
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, TypeError, ValueError):
        return False


def _utc_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _pricing_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
            for pattern in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(raw, pattern)
                    break
                except ValueError:
                    continue
            if parsed is None:
                return None
    return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


class PortfolioService:
    """Application service boundary used by HTTP and other adapters."""

    def __init__(
        self,
        *,
        storage: Optional[Any] = None,
        portfolio: Optional[Any] = None,
        price_fetcher: Optional[Any] = None,
        storage_factory: Optional[Any] = None,
        portfolio_factory: Optional[Any] = None,
        read_service_factory: Optional[Any] = None,
        futu_receipt_service: Optional[Any] = None,
        nav_receipt_service: Optional[Any] = None,
        operation_state_store: Optional[Any] = None,
        quality_service: Optional[Any] = None,
        default_account: Optional[str] = None,
    ):
        self._storage = storage
        self._portfolio = portfolio
        self._price_fetcher = price_fetcher
        self._storage_factory = storage_factory
        self._portfolio_factory = portfolio_factory
        self._read_service_factory = read_service_factory
        self._futu_receipt_service = futu_receipt_service
        self._nav_receipt_service = nav_receipt_service
        self._operation_state_store = operation_state_store
        self._quality_service = quality_service
        self._default_account = default_account

    @property
    def storage(self) -> Any:
        if self._storage is None:
            if self._storage_factory is not None:
                try:
                    self._storage = self._storage_factory(healthcheck=False)
                except TypeError:
                    self._storage = self._storage_factory()
            else:
                from src.feishu_storage import FeishuStorage

                self._storage = FeishuStorage()
        return self._storage

    @property
    def portfolio(self) -> Any:
        if self._portfolio is None:
            if self._portfolio_factory is not None:
                self._portfolio = self._portfolio_factory(self.storage)
            else:
                from src.portfolio import PortfolioManager

                if self._price_fetcher is None:
                    self._portfolio = PortfolioManager(self.storage)
                else:
                    self._portfolio = PortfolioManager(self.storage, price_fetcher=self._price_fetcher)
        return self._portfolio

    def _resolve_account(self, account: Optional[str]) -> str:
        if account:
            return account
        if self._default_account:
            return self._default_account
        from src import config

        return config.get_account()

    def _read_service(self, account: str) -> Any:
        portfolio = self.portfolio
        if self._read_service_factory is not None:
            return self._read_service_factory(
                account=account,
                storage=self.storage,
                portfolio=portfolio,
                reporting_service=portfolio.reporting_service,
            )

        from src.app import PortfolioReadService

        return PortfolioReadService(
            account=account,
            storage=self.storage,
            portfolio=portfolio,
            reporting_service=portfolio.reporting_service,
        )

    def health(self) -> Dict[str, Any]:
        return {
            "success": True,
            "status": "ok",
            "service": "portfolio-management",
        }

    def quality_status(self) -> Optional[Dict[str, Any]]:
        if self._quality_service is not None:
            return self._quality_service.read_published()
        from src.app.quality.artifact import QualityArtifactStore

        return QualityArtifactStore().read()

    def refresh_quality_status(self, *, accounts: Any = None) -> Dict[str, Any]:
        from src import config
        from src.app.quality.service import PMQualityService

        normalized = list(accounts or config.get_quality_accounts())
        service = self._quality_service or PMQualityService(self.storage)
        return service.refresh(accounts=normalized)

    def list_accounts(self, *, include_default: bool = True) -> Dict[str, Any]:
        from src.app import AccountService

        return AccountService(
            storage=self.storage,
            default_account=self._resolve_account(None),
        ).list_accounts(include_default=include_default)

    def list_nav_accounts(self, *, include_default: bool = False) -> Dict[str, Any]:
        from src.app import AccountService

        return AccountService(
            storage=self.storage,
            default_account=self._resolve_account(None),
        ).list_nav_accounts(include_default=include_default)

    def audit_nav_history_duplicates(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        audit = getattr(self.storage, "audit_nav_history_duplicates", None)
        if not callable(audit):
            return {"success": False, "error": "storage does not support nav_history duplicate audit"}
        return audit(account=account)

    def multi_account_overview(
        self,
        *,
        accounts: Any = None,
        price_timeout: int = 30,
        include_details: bool = False,
    ) -> Dict[str, Any]:
        from src.app import AccountService

        return AccountService(
            storage=self.storage,
            default_account=self._resolve_account(None),
            full_report_func=self.full_report,
        ).multi_account_overview(
            accounts=accounts,
            price_timeout=price_timeout,
            include_details=include_details,
        )

    def get_holdings(
        self,
        *,
        account: Optional[str] = None,
        include_cash: bool = True,
        group_by_market: bool = False,
        include_price: bool = False,
    ) -> Dict[str, Any]:
        try:
            return self._read_service(self._resolve_account(account)).get_holdings(
                include_cash=include_cash,
                group_by_market=group_by_market,
                include_price=include_price,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_valuation_evidence(
        self,
        *,
        accounts: Any,
        supplemental_codes: Any = None,
        price_timeout: int = 30,
        holdings_scope: str = "all",
    ) -> Dict[str, Any]:
        request_deadline = time.monotonic() + max(0.0, float(price_timeout))
        normalized_accounts = list(
            dict.fromkeys(
                str(item or "").strip().lower()
                for item in (accounts or [])
                if str(item or "").strip()
            )
        )
        normalized_codes = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in (supplemental_codes or [])
                if str(item or "").strip()
            )
        )
        if not normalized_accounts:
            return {
                "success": False,
                "error_code": "INPUT_ERROR",
                "error": "accounts must contain at least one account",
            }
        if len(normalized_accounts) > 20:
            return {
                "success": False,
                "error_code": "INPUT_ERROR",
                "error": "accounts must contain at most 20 accounts",
            }
        if len(normalized_codes) > 500:
            return {
                "success": False,
                "error_code": "INPUT_ERROR",
                "error": "supplemental_codes must contain at most 500 codes",
            }
        if holdings_scope not in {"all", "non_futu"}:
            return {"success": False, "error_code": "INPUT_ERROR", "error": "holdings_scope must be all or non_futu"}
        if holdings_scope == "non_futu" and normalized_codes:
            return {"success": False, "error_code": "INPUT_ERROR", "error": "non_futu scope does not accept supplemental_codes"}

        account_result = self.list_accounts(include_default=True)
        available_accounts = {
            str(item or "").strip().lower()
            for item in (account_result.get("accounts") or [])
            if str(item or "").strip()
        }
        unknown = [account for account in normalized_accounts if account not in available_accounts]
        if unknown:
            return {
                "success": False,
                "error_code": "INPUT_ERROR",
                "error": f"unknown accounts: {', '.join(unknown)}",
            }

        from src.app.run_quote_pool import RunQuotePool

        pool = RunQuotePool()
        account_items: list[Dict[str, Any]] = []
        holdings: list[Dict[str, Any]] = []
        quotes_by_identity: Dict[tuple[str, str], Dict[str, Any]] = {}
        warnings: list[str] = []
        holdings_by_account: Dict[str, list[Any]] = {}
        source_read_times: Dict[str, datetime] = {}
        filter_counts: Dict[str, Dict[str, int]] = {}
        broker_inventory: Dict[str, Dict[str, Any]] = {}
        source_rows: Dict[str, list[Dict[str, Any]]] = {}
        included_source: Dict[str, Dict[tuple[str, str], Dict[str, Any]]] = {}
        filter_warnings: Dict[str, list[str]] = {}
        pending_accounts: list[str] = []
        for account in normalized_accounts:
            if time.monotonic() >= request_deadline:
                account_items.append(
                    {
                        "account": account,
                        "status": "deadline_exceeded",
                        "holdings": [],
                        "quotes": [],
                        "warnings": [
                            f"{account}: 全局 deadline 前未读取账户持仓"
                        ],
                    }
                )
                continue
            try:
                if holdings_scope == "non_futu":
                    from src.app.holdings_validation import holding_broker_scope

                    raw_records = list(self.storage.get_raw_holdings(account=account))
                    read_at = raw_records[0].fetched_at if raw_records else datetime.now(timezone.utc)
                    if _utc_time(read_at) is None or any(
                        record.fetched_at != read_at or record.source != "feishu"
                        for record in raw_records
                    ):
                        raise ValueError("holdings source read provenance is incomplete")
                    source_read_times[account] = read_at
                    counts = {
                        "source_rows": len(raw_records), "included": 0,
                        "zero_quantity": 0, "excluded_futu": 0,
                        "excluded_unknown_broker": 0, "unsupported": 0,
                    }
                    broker_rows: Dict[tuple[str, str], int] = {}
                    selected = []
                    source_rows[account] = []
                    for record in raw_records:
                        fields = getattr(record, "raw_fields", None)
                        if (
                            not str(getattr(record, "record_id", "") or "").strip()
                            or not isinstance(fields, dict)
                            or str(fields.get("account") or "").strip() != account
                        ):
                            raise ValueError("holdings source returned an incomplete or cross-account row")
                        broker = fields.get("broker") if isinstance(fields.get("broker"), str) else ""
                        classification = holding_broker_scope(broker)
                        broker_rows[(broker, classification)] = broker_rows.get((broker, classification), 0) + 1
                        source_rows[account].append({
                            "record_id": record.record_id,
                            "broker": broker,
                            "classification": classification,
                            "source_read_at_utc": read_at.isoformat(),
                            "record_updated_at": str(fields.get("updated_at")) if fields.get("updated_at") else "unknown",
                        })
                        if classification == "futu":
                            counts["excluded_futu"] += 1
                        elif classification == "unknown":
                            counts["excluded_unknown_broker"] += 1
                        else:
                            selected.append(record)
                    account_holdings = list(self.storage._convert_raw_holdings(selected))
                    if len(account_holdings) != len(selected):
                        raise ValueError("holdings conversion lost source rows")
                    included_source[account] = {}
                    for record, holding in zip(selected, account_holdings):
                        if (
                            holding.record_id != record.record_id
                            or holding.account != account
                            or holding.broker != str(record.raw_fields.get("broker") or "").strip()
                            or holding.asset_id != str(record.raw_fields.get("asset_id") or "").strip()
                        ):
                            raise ValueError("holdings conversion changed source identity")
                        included_source[account][(holding.broker, holding.asset_id)] = {
                            "source_read_at_utc": read_at.isoformat(),
                            "record_updated_at": str(record.raw_fields.get("updated_at")) if record.raw_fields.get("updated_at") else "unknown",
                            "record_id": record.record_id,
                        }
                    nonzero_holdings = []
                    for holding in account_holdings:
                        if holding.quantity == 0:
                            counts["zero_quantity"] += 1
                        else:
                            nonzero_holdings.append(holding)
                    account_holdings = nonzero_holdings
                    counts["included"] = len(account_holdings)
                    filter_counts[account] = counts
                    broker_inventory[account] = {
                        "source": "feishu",
                        "source_rows": len(raw_records),
                        "read_at_utc": read_at.isoformat(),
                        "brokers": [
                            {"broker": broker, "classification": classification, "row_count": count}
                            for (broker, classification), count in sorted(broker_rows.items())
                        ],
                    }
                    filter_warnings[account] = []
                    if counts["excluded_unknown_broker"]:
                        filter_warnings[account].append(
                            f"{account}: unknown broker on {counts['excluded_unknown_broker']} Holdings row(s)"
                        )
                else:
                    account_holdings = list(self.storage.get_holdings(account=account))
            except Exception as exc:
                account_items.append(
                    {
                        "account": account,
                        "status": "unavailable",
                        "holdings": [],
                        "quotes": [],
                        "warnings": [f"{account}: {exc}"],
                    }
                )
                continue
            holdings_by_account[account] = account_holdings
            pending_accounts.append(account)

        shared_prices: Dict[str, Any] = {}
        shared_price_warnings: list[str] = []
        if pending_accounts and time.monotonic() < request_deadline and (
            holdings_scope == "all" or normalized_codes or any(holdings_by_account.values())
        ):
            all_holdings = [
                holding
                for account in pending_accounts
                for holding in holdings_by_account[account]
            ]
            price_holdings = all_holdings
            if holdings_scope == "non_futu":
                price_holdings = []
                for holding in all_holdings:
                    asset_type = getattr(holding.asset_type, "value", holding.asset_type)
                    if asset_type in {"cash", "mmf"} and str(holding.currency).upper() == "CNY":
                        shared_prices[holding.asset_id] = {
                            "code": holding.asset_id, "price": 1.0, "cny_price": 1.0,
                            "exchange_rate": 1.0, "currency": "CNY",
                            "source": "fixed_identity",
                        }
                    else:
                        price_holdings.append(holding)
            if price_holdings or holdings_scope == "all":
                fetched_prices, shared_price_warnings = self.portfolio.fetch_price_snapshot(
                    holdings=price_holdings,
                    supplemental_codes=normalized_codes,
                    price_timeout_seconds=price_timeout,
                    run_quote_pool=pool,
                    deadline=request_deadline,
                )
                shared_prices.update(fetched_prices)
        elif pending_accounts and time.monotonic() >= request_deadline:
            shared_price_warnings = [
                f"价格获取达到全局 deadline（{price_timeout}秒）"
            ]

        if holdings_scope == "non_futu":
            foreign_currencies = {
                str(holding.currency or "").upper()
                for account in pending_accounts
                for holding in holdings_by_account[account]
                if str(holding.currency or "").upper() != "CNY"
            }
            rates: Dict[str, float] = {}
            fx_evidence: Dict[str, Any] = {}
            if foreign_currencies:
                try:
                    fx_service = self.portfolio.price_fetcher.fx_service
                    rates, fx_evidence = fx_service.fetch_exchange_rates_with_evidence(
                        deadline=request_deadline
                    )
                except Exception as exc:
                    shared_price_warnings.append(f"scoped FX evidence unavailable: {exc}")
            for payload in shared_prices.values():
                if not isinstance(payload, dict):
                    continue
                currency = str(payload.get("currency") or "CNY").upper()
                if currency == "CNY":
                    continue
                rate_key = f"{currency}CNY"
                moment = _utc_time(fx_evidence.get("observed_at_utc"))
                source = (fx_evidence.get("sources") or {}).get(rate_key)
                fx_valid = (
                    _positive_amount(rates.get(rate_key))
                    and isinstance(source, str) and bool(source.strip())
                    and fx_evidence.get("is_stale") is False
                    and moment is not None
                    and 0 <= (datetime.now(timezone.utc) - moment).total_seconds() < 86400
                )
                payload["fx_evidence"] = {
                    "source": source or "unknown",
                    "observed_at_utc": fx_evidence.get("observed_at_utc"),
                    "cache_status": fx_evidence.get("cache_status") or "unavailable",
                    "is_stale": bool(fx_evidence.get("is_stale", True)),
                }
                if fx_valid and _positive_amount(payload.get("price")):
                    payload["exchange_rate"] = rates[rate_key]
                    payload["cny_price"] = quantize_money(
                        Decimal(str(payload["price"])) * Decimal(str(rates[rate_key]))
                    )
                else:
                    payload["exchange_rate"] = None
                    payload["cny_price"] = None

        for account in pending_accounts:
            try:
                item = self._read_service(account).build_valuation_evidence(
                    supplemental_codes=normalized_codes,
                    price_timeout_seconds=price_timeout,
                    deadline=request_deadline,
                    holdings=holdings_by_account[account],
                    price_snapshot=shared_prices,
                    price_warnings=shared_price_warnings,
                )
            except Exception as exc:
                item = {
                    "account": account,
                    "status": "unavailable",
                    "holdings": [],
                    "quotes": [],
                    "warnings": [f"{account}: {exc}"],
                }
            if holdings_scope == "non_futu":
                item["holding_counts"] = filter_counts[account]
                item["warnings"] = list(item.get("warnings") or []) + filter_warnings[account]
                if item.get("account") != account:
                    item = {
                        "account": account, "status": "unavailable", "holdings": [],
                        "quotes": [], "holding_counts": filter_counts[account],
                        "warnings": [f"{account}: valuation returned a different account"],
                    }
                for quote in item.get("quotes") or []:
                    code = str(quote.get("code") or "").strip()
                    payload = shared_prices.get(code) or shared_prices.get(code.upper())
                    if not isinstance(payload, dict):
                        payload = {}
                    quote["fetched_at_utc"] = (
                        moment.isoformat() if (moment := _pricing_time(payload.get("fetched_at"))) else None
                    )
                    quote["cache_expires_at_utc"] = (
                        moment.isoformat() if (moment := _pricing_time(payload.get("expires_at"))) else None
                    )
                    quote["fx_evidence"] = payload.get("fx_evidence")
                    source = str(payload.get("source") or "")
                    market_raw = (
                        payload.get("time") if source in {"tencent", "tencent_batch"}
                        else payload.get("nav_date") if source in {"tencent_jj", "eastmoney"}
                        else None
                    )
                    quote["market_as_of"] = (
                        (moment.isoformat() if (moment := _pricing_time(market_raw)) else "invalid")
                        if market_raw not in (None, "") else "unknown"
                    )
                expected = {
                    (account, holding.broker, holding.asset_id)
                    for holding in holdings_by_account[account]
                }
                actual = set()
                for row in item.get("holdings") or []:
                    identity = (row.get("account"), row.get("broker"), row.get("code"))
                    actual.add(identity)
                    row.update(included_source[account].get((row.get("broker"), row.get("code")), {}))
                if actual != expected or len(item.get("holdings") or []) != len(expected):
                    item["warnings"].append(f"{account}: valuation holdings differ from raw source slice")
                    if item.get("status") == "complete":
                        item["status"] = "partial"
                filter_counts[account]["unsupported"] = sum(
                    1 for row in item.get("holdings") or []
                    if not _finite_amount(row.get("market_value_cny"))
                ) + len(expected - actual)
                if filter_warnings[account] and item.get("status") == "complete":
                    item["status"] = "partial"
            account_items.append(item)

        account_order = {
            account: index
            for index, account in enumerate(normalized_accounts)
        }
        account_items.sort(
            key=lambda item: account_order.get(str(item.get("account") or ""), len(account_order))
        )
        for item in account_items:
            holdings.extend(
                dict(row)
                for row in (item.get("holdings") or [])
                if isinstance(row, dict)
            )
            for quote in item.get("quotes") or []:
                if not isinstance(quote, dict):
                    continue
                identity = (
                    str(quote.get("code") or "").strip().upper(),
                    str(quote.get("currency") or "").strip().upper(),
                )
                if identity[0]:
                    quotes_by_identity.setdefault(identity, dict(quote))
            warnings.extend(str(value) for value in (item.get("warnings") or []) if str(value).strip())

        statuses = {str(item.get("status") or "") for item in account_items}
        status = (
            "unavailable"
            if statuses == {"unavailable"}
            else ("complete" if statuses <= {"complete"} else "partial")
        )
        observed_at = datetime.now(timezone.utc).isoformat()
        result = {
            "schema_version": "portfolio.valuation_evidence.v1",
            "success": True,
            "status": status,
            "scope": {
                "accounts": normalized_accounts,
                "supplemental_codes": normalized_codes,
                "reporting_currency": "CNY",
                **({
                    "holdings_scope": "non_futu",
                    "holding_counts": filter_counts,
                    "broker_inventory": broker_inventory,
                } if holdings_scope == "non_futu" else {}),
            },
            "snapshot": {
                "snapshot_id": f"valuation-{uuid4().hex}",
                "observed_at": observed_at,
                "quote_pool": pool.summary(),
                "deadline_seconds": price_timeout,
                "deadline_exceeded": time.monotonic() >= request_deadline,
            },
            "holdings": holdings,
            "quotes": list(quotes_by_identity.values()),
            "account_status": [
                {
                    "account": item.get("account"),
                    "status": item.get("status"),
                    "warnings": list(item.get("warnings") or []),
                    "diagnostics": list(item.get("diagnostics") or []),
                    **({"holding_counts": item.get("holding_counts")} if holdings_scope == "non_futu" else {}),
                    **({"source_rows": source_rows.get(str(item.get("account")), [])} if holdings_scope == "non_futu" else {}),
                }
                for item in account_items
            ],
            "warnings": list(dict.fromkeys(warnings)),
        }
        if holdings_scope == "non_futu":
            freshness = self._non_futu_valuation_freshness(result, source_read_times)
            result["quality_issues"] = freshness.pop("row_issues")
            result["freshness"] = freshness
            result["retrieved_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if result["status"] == "complete" and freshness["trust_status"] != "trusted":
                result["status"] = "partial"
            if result["status"] == "complete" and freshness["status"] != "fresh":
                result["status"] = "partial"
        return result

    def _non_futu_valuation_freshness(
        self, result: Dict[str, Any], source_read_times: Dict[str, datetime]
    ) -> Dict[str, Any]:
        accounts = result["scope"]["accounts"]
        holdings = result["holdings"]
        quotes = {(str(row.get("code") or "").upper(), str(row.get("currency") or "").upper()): row for row in result["quotes"]}
        required = ["pm.holdings_feishu"]
        if any(str(row.get("asset_type") or "").lower() not in {"cash", "mmf"} for row in holdings):
            required.append("pm.prices")
        if any(str(row.get("currency") or "").upper() != "CNY" for row in holdings):
            required.append("pm.fx")
        reasons: set[str] = set()
        observed: list[datetime] = []
        row_issues: list[Dict[str, str]] = []
        now = datetime.now(timezone.utc)
        for account in accounts:
            moment = source_read_times.get(account)
            if moment is None or not 0 <= (now - moment).total_seconds() <= 300:
                reasons.add("HOLDINGS_FRESH_READ_UNAVAILABLE")
            else:
                observed.append(moment)
        for row in holdings:
            if row.get("quantity") in (None, 0, "0"):
                continue
            row_reasons: set[str] = set()
            try:
                market_value = Decimal(str(row.get("market_value_cny")))
            except (InvalidOperation, TypeError, ValueError):
                market_value = Decimal("NaN")
            if not market_value.is_finite():
                row_reasons.add("INCLUDED_VALUE_MISSING")
            currency = str(row.get("currency") or "").upper()
            asset_type = str(row.get("asset_type") or "").lower()
            quote = quotes.get((str(row.get("code") or "").upper(), currency))
            fixed_cny = currency == "CNY" and asset_type in {"cash", "mmf"}
            if fixed_cny:
                try:
                    fixed_valid = bool(quote) and Decimal(str(quote.get("price_native"))) == 1 and Decimal(str(quote.get("price_cny"))) == 1
                except (InvalidOperation, TypeError, ValueError):
                    fixed_valid = False
                if not fixed_valid:
                    row_reasons.add("INCLUDED_FIXED_IDENTITY_INVALID")
            else:
                if (
                    not quote
                    or not _positive_amount(quote.get("price_native"))
                    or not _positive_amount(quote.get("price_cny"))
                    or str(quote.get("source") or "").strip().lower()
                    in {"", "unknown", "unavailable", "cache_fallback"}
                    or quote.get("is_stale")
                ):
                    row_reasons.add("INCLUDED_QUOTE_INVALID")
                if quote:
                    if quote.get("is_from_cache"):
                        expiry = _utc_time(quote.get("cache_expires_at_utc"))
                        if expiry is None or expiry <= now:
                            row_reasons.add("INCLUDED_QUOTE_CACHE_INVALID")
                    else:
                        fetched = _utc_time(quote.get("fetched_at_utc"))
                        if fetched is None or not 0 <= (now - fetched).total_seconds() <= 300:
                            row_reasons.add("INCLUDED_QUOTE_TIME_INVALID")
                        else:
                            observed.append(fetched)
                    market_as_of = quote.get("market_as_of")
                    if market_as_of not in (None, "unknown"):
                        market_time = _utc_time(market_as_of)
                        if market_time is None or not 0 <= (now - market_time).total_seconds() <= 7 * 86400:
                            row_reasons.add("INCLUDED_MARKET_TIME_INVALID")
                    if currency != "CNY":
                        fx = quote.get("fx_evidence") or {}
                        fx_time = _utc_time(fx.get("observed_at_utc"))
                        if (
                            not _positive_amount(quote.get("exchange_rate_to_cny"))
                            or not str(fx.get("source") or "").strip()
                            or fx.get("source") == "unknown"
                            or fx.get("is_stale") is not False
                            or fx.get("cache_status") == "stale_fallback"
                            or fx_time is None
                            or not 0 <= (now - fx_time).total_seconds() < 86400
                        ):
                            row_reasons.add("INCLUDED_FX_MISSING")
                        else:
                            observed.append(fx_time)
            for reason in sorted(row_reasons):
                row_issues.append({
                    "account": str(row.get("account") or ""),
                    "broker": str(row.get("broker") or ""),
                    "code": str(row.get("code") or ""),
                    "reason_code": reason,
                })
            reasons.update(row_reasons)
        if any(item.get("status") != "complete" for item in result["account_status"]):
            reasons.add("SCOPED_ACCOUNT_PARTIAL")
        if result["snapshot"]["deadline_exceeded"]:
            reasons.add("SCOPED_DEADLINE_EXCEEDED")
        return {
            "status": "fresh" if not reasons else "unknown",
            "trust_status": "trusted" if not reasons else "partial",
            "observed_at_utc": min(observed).isoformat().replace("+00:00", "Z") if observed else None,
            "dataset_ids": required,
            "reason_codes": sorted(reasons),
            "row_issues": row_issues,
        }

    def get_cash(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        from src.app import CashService

        return CashService(self.storage).get_cash(self._resolve_account(account))

    def _run_futu_holdings_sync(
        self,
        *,
        account: Optional[str] = None,
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
    ) -> Dict[str, Any]:
        from src.app import FutuBalanceSyncService
        from src.process_lock import futu_full_sync_lock_key, process_lock

        resolved_account = self._resolve_account(account)
        try:
            with process_lock(futu_full_sync_lock_key(resolved_account)):
                result = FutuBalanceSyncService(self.storage).sync_portfolio(
                    account=resolved_account,
                    dry_run=dry_run,
                    confirm=confirm,
                    allow_empty_stock_snapshot=allow_empty_stock_snapshot,
                )
        except Exception as exc:
            return {
                "success": False,
                "status": "failed",
                "account": resolved_account,
                "broker": "富途",
                "dry_run": dry_run,
                "error": str(exc),
            }
        return dict(result)

    def sync_futu_holdings(
        self,
        *,
        account: Optional[str] = None,
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
    ) -> Dict[str, Any]:
        from src.app.futu_sync_receipt_service import FutuSyncReceiptService

        result = self._run_futu_holdings_sync(
            account=account,
            dry_run=dry_run,
            confirm=confirm,
            allow_empty_stock_snapshot=allow_empty_stock_snapshot,
        )
        receipt_service = self._futu_receipt_service or FutuSyncReceiptService()
        result["receipt"] = receipt_service.send(result)
        return result

    def refresh_futu_holdings(self, *, account: str, request_id: str) -> Dict[str, Any]:
        """Run the non-durable OM refresh hint without a user receipt."""
        try:
            result = self._run_futu_holdings_sync(
                account=account,
                dry_run=False,
                confirm=True,
                allow_empty_stock_snapshot=False,
            )
        except Exception:
            LOGGER.exception(
                "pm_futu_refresh_failed account=%s request_id=%s",
                account,
                request_id,
            )
            return {"success": False, "status": "failed", "account": account}
        LOGGER.info(
            "pm_futu_refresh_completed account=%s request_id=%s success=%s status=%s",
            account,
            request_id,
            bool(result.get("success")),
            str(result.get("status") or "unknown"),
        )
        return result

    def get_nav(self, *, account: Optional[str] = None, days: int = 30) -> Dict[str, Any]:
        from src.app.nav_payload import format_nav_history_item, format_nav_payload

        try:
            navs = self.storage.get_nav_history(
                self._resolve_account(account), days=days, fresh=True
            )
            if not navs:
                return {"success": False, "message": "无净值记录"}
            return {
                "success": True,
                "latest": format_nav_payload(navs[-1]),
                "history": [format_nav_history_item(nav) for nav in navs],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_capital_facts(
        self,
        *,
        account: Optional[str] = None,
        period: str,
        as_of_month: str,
    ) -> Dict[str, Any]:
        from src.app import CapitalFactsService

        try:
            return CapitalFactsService(storage=self.storage).get(
                account=self._resolve_account(account),
                period=period,
                as_of_month=as_of_month,
            )
        except Exception as exc:
            return {"success": False, "status": "failed", "error": str(exc)}

    def record_nav(
        self,
        *,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        run_id: Optional[str] = None,
        nav_date: Optional[Any] = None,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        from src.app import AccountNavRecorderService
        from src.run_id import new_run_id

        resolved_account = self._resolve_account(account)
        resolved_run_id = run_id or new_run_id("nav", resolved_account)
        result = AccountNavRecorderService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(resolved_account),
        ).record(
            nav_date=nav_date,
            price_timeout=price_timeout,
            snapshot=snapshot,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            run_id=resolved_run_id,
        )
        nav_result = result.get("nav_result")
        if isinstance(nav_result, dict):
            return nav_result
        return result

    def init_nav_history(
        self,
        *,
        account: Optional[str] = None,
        date_str: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        use_bulk_persist: bool = False,
    ) -> Dict[str, Any]:
        from src.app import NavInitializationService

        resolved_account = self._resolve_account(account)
        return NavInitializationService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(resolved_account),
        ).init_nav_history(
            date_str=date_str,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            use_bulk_persist=use_bulk_persist,
        )

    def get_distribution(
        self,
        *,
        account: Optional[str] = None,
        accounts: Any = None,
        by_asset: bool = False,
        include_value: bool = True,
        group_cash: bool = False,
    ) -> Dict[str, Any]:
        try:
            from src.app.account_service import normalize_accounts

            by_asset = bool(by_asset or group_cash)
            target_accounts = normalize_accounts(accounts)
            if target_accounts is None:
                if account is not None:
                    target_accounts = [account]
                else:
                    target_accounts = [self._resolve_account(None)]

            if len(target_accounts) == 1 and not by_asset:
                return self._read_service(target_accounts[0]).get_distribution()

            snapshots = []
            errors = []
            for acc in target_accounts:
                try:
                    snapshot = self._read_service(acc).build_snapshot()
                    snapshots.append(snapshot)
                except Exception as e:
                    errors.append({"account": acc, "error": str(e)})

            if not snapshots:
                return {"success": False, "error": errors[0]["error"] if errors else "no holdings data"}

            from src.app.portfolio_read_service import PortfolioReadService

            merged_holdings_data = PortfolioReadService.merge_holdings_data(
                [(s.get("holdings_data") or {}) for s in snapshots]
            )
            read_service = self._read_service(target_accounts[0])
            if by_asset:
                result = read_service.get_asset_distribution(
                    merged_holdings_data,
                    include_value=include_value,
                    group_cash=group_cash,
                )
            else:
                result = read_service.get_distribution(merged_holdings_data)

            result["accounts"] = target_accounts
            if errors:
                result["errors"] = errors
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def full_report(self, *, account: Optional[str] = None, price_timeout: int = 30) -> Dict[str, Any]:
        from src.app import ReportQueryService

        resolved_account = self._resolve_account(account)
        read_service = self._read_service(resolved_account)
        return ReportQueryService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=read_service,
        ).full_report(price_timeout=price_timeout)

    def generate_report(
        self,
        *,
        account: Optional[str] = None,
        report_type: str = "daily",
        price_timeout: int = 30,
    ) -> Dict[str, Any]:
        from src.app import ReportGenerationService, ReportQueryService

        resolved_account = self._resolve_account(account)
        read_service = self._read_service(resolved_account)
        report_query_service = ReportQueryService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=read_service,
        )
        return ReportGenerationService(
            build_snapshot_func=read_service.build_snapshot,
            full_report_func=report_query_service.full_report,
        ).generate_report(
            report_type=report_type,
            price_timeout=price_timeout,
        )

    def daily_report_bundle(
        self,
        *,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
        nav_date: Optional[Any] = None,
    ) -> Dict[str, Any]:
        resolved_account = self._resolve_account(account)
        from src.app import DailyAccountNavService

        return DailyAccountNavService(
            account=resolved_account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(resolved_account),
        ).run(
            nav_date=nav_date,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            sync_futu_cash_mmf=sync_futu_cash_mmf,
            sync_futu_dry_run=sync_futu_dry_run,
            run_id=run_id,
        )

    def prepare_historical_nav_valuation_evidence(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        from src.app.nav_valuation_evidence_service import (
            HistoricalNavValuationEvidenceService,
        )

        try:
            return HistoricalNavValuationEvidenceService(
                storage=self.storage,
                portfolio=self.portfolio,
            ).prepare(**kwargs)
        except Exception as exc:
            return {
                "success": False,
                "status": "failed",
                "stage": "historical_valuation_evidence",
                "error": str(exc) or exc.__class__.__name__,
            }

    def daily_nav_job(
        self,
        *,
        nav_date: Optional[Any] = None,
        run_date: Optional[Any] = None,
        accounts: Any = None,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        force_non_business_day: bool = False,
        run_id: Optional[str] = None,
        valuation_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        from src.app.business_calendar_service import BusinessCalendarService
        from src.app import DailyNavJobService
        from src.app.nav_history_receipt_service import NavHistoryReceiptService
        from src.app.nav_receipt_outbox_service import (
            NavReceiptOutboxService,
            ReceiptDispatchStateUnknown,
        )
        from src.run_id import new_run_id

        resolved_run_id = run_id or new_run_id("daily-nav-job", account or "multi")
        try:
            result = DailyNavJobService(
                storage=self.storage,
                portfolio=self.portfolio,
                default_account=self._resolve_account(None),
                read_service_factory=self._read_service_factory,
            ).run(
                nav_date=nav_date,
                run_date=run_date,
                accounts=accounts,
                account=account,
                price_timeout=price_timeout,
                dry_run=dry_run,
                confirm=confirm,
                overwrite_existing=overwrite_existing,
                use_bulk_persist=use_bulk_persist,
                sync_futu_cash_mmf=sync_futu_cash_mmf,
                sync_futu_dry_run=sync_futu_dry_run,
                force_non_business_day=force_non_business_day,
                run_id=resolved_run_id,
                valuation_ref=valuation_ref,
            )
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            try:
                resolved_date = (
                    str(nav_date)[:10]
                    if nav_date is not None and str(nav_date) != "auto"
                    else BusinessCalendarService.from_config()
                    .default_nav_date(run_date=run_date)
                    .isoformat()
                )
            except Exception:
                resolved_date = str(nav_date or "unknown")
            result = {
                "success": False,
                "status": "failed",
                "date": resolved_date,
                "run_id": resolved_run_id,
                "dry_run": dry_run,
                "confirm": confirm,
                "items": [],
                "summary": {"failed": 1},
                "error": error,
                "failure": {
                    "stage": "daily_nav_job",
                    "exception_type": exc.__class__.__name__,
                    "message": error,
                },
            }
        result = dict(result)
        result.setdefault("dry_run", dry_run)
        result.setdefault("confirm", confirm)
        result.setdefault("run_id", resolved_run_id)
        receipt_sender = self._nav_receipt_service or NavHistoryReceiptService()
        if bool(result.get("dry_run", True)):
            result["receipt"] = receipt_sender.send(result)
            return result
        try:
            result["receipt"] = NavReceiptOutboxService(
                store=self._operation_state_store,
                sender=receipt_sender,
            ).enqueue_and_dispatch(result)
        except ReceiptDispatchStateUnknown as exc:
            result["receipt"] = {
                **exc.delivery,
                "success": False,
                "status": "unknown",
                "receipt_key": exc.receipt_key,
                "outbox_error": str(exc),
                "immediate_retry_suppressed": True,
            }
        except Exception as exc:
            fallback = dict(receipt_sender.send(result))
            fallback["outbox_error"] = str(exc) or exc.__class__.__name__
            result["receipt"] = fallback
        return result
