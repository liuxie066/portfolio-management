"""Account discovery and multi-account read orchestration."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from src.time_utils import bj_now_naive


def iter_account_values(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        account = value.strip()
        if account:
            yield account
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from iter_account_values(item)
        return
    if isinstance(value, dict):
        for key in ("text", "name", "value"):
            if key in value:
                yield from iter_account_values(value.get(key))
        return

    account = str(value).strip()
    if account:
        yield account


def normalize_accounts(accounts: Any) -> Optional[List[str]]:
    if accounts is None:
        return None
    if isinstance(accounts, str):
        raw_items = accounts.split(",")
    elif isinstance(accounts, (list, tuple, set)):
        raw_items = list(accounts)
    else:
        raw_items = [accounts]

    normalized: List[str] = []
    seen = set()
    for item in raw_items:
        for account in iter_account_values(item):
            if account not in seen:
                seen.add(account)
                normalized.append(account)
    return normalized


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_money(value: float) -> float:
    return round(float(value or 0.0), 2)


def report_value_breakdown(report: Dict[str, Any]) -> Dict[str, float]:
    overview = report.get("overview") or {}
    total_value = _as_float(overview.get("total_value"), 0.0)

    cash_ratio = _as_float(overview.get("cash_ratio"), 0.0)
    stock_ratio = _as_float(overview.get("stock_ratio"), 0.0)
    fund_ratio = _as_float(overview.get("fund_ratio"), 0.0)

    nav = report.get("nav") or {}
    if cash_ratio == 0 and stock_ratio == 0 and fund_ratio == 0 and total_value:
        cash_value = _as_float(nav.get("cash_value"), 0.0)
        stock_value = _as_float(nav.get("stock_value"), 0.0)
        fund_value = _as_float(nav.get("fund_value"), 0.0)
    else:
        cash_value = total_value * cash_ratio
        stock_value = total_value * stock_ratio
        fund_value = total_value * fund_ratio

    return {
        "total_value": _round_money(total_value),
        "cash_value": _round_money(cash_value),
        "stock_value": _round_money(stock_value),
        "fund_value": _round_money(fund_value),
        "non_cash_value": _round_money(stock_value + fund_value),
    }


class AccountService:
    def __init__(
        self,
        *,
        storage: Any,
        default_account: Optional[str] = None,
        full_report_func: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        self.storage = storage
        self.default_account = default_account
        self.full_report_func = full_report_func

    def list_accounts(self, *, include_default: bool = True) -> Dict[str, Any]:
        accounts = set()
        sources: Dict[str, List[str]] = {}
        warnings = []

        def remember(source: str, account_values: Iterable[str]) -> None:
            source_accounts = set()
            for account in account_values:
                if not account:
                    continue
                accounts.add(account)
                source_accounts.add(account)
            sources[source] = sorted(source_accounts)

        try:
            get_holdings_fn = getattr(self.storage, "get_holdings")
            try:
                holdings = get_holdings_fn(account=None, include_empty=True)
            except TypeError:
                holdings = get_holdings_fn(account=None)
            remember("holdings", (getattr(holding, "account", None) for holding in holdings or []))
        except Exception as e:
            warnings.append({"source": "holdings", "error": str(e)})

        client = getattr(self.storage, "client", None)
        list_records = getattr(client, "list_records", None)
        if callable(list_records):
            for table in ("transactions", "cash_flow", "nav_history"):
                try:
                    records = list_records(table, field_names=["account"])
                    values = []
                    for record in records or []:
                        raw_fields = record.get("fields") or {}
                        fields = raw_fields
                        convert_fields = getattr(self.storage, "_from_feishu_fields", None)
                        if callable(convert_fields):
                            try:
                                fields = convert_fields(raw_fields, table)
                            except Exception:
                                fields = raw_fields
                        values.extend(iter_account_values(fields.get("account")))
                    remember(table, values)
                except Exception as e:
                    warnings.append({"source": table, "error": str(e)})

        if include_default and self.default_account:
            accounts.add(self.default_account)

        result = {
            "success": True,
            "default_account": self.default_account,
            "accounts": sorted(accounts),
            "count": len(accounts),
            "sources": sources,
        }
        if warnings:
            result["warnings"] = warnings
        return result

    def multi_account_overview(
        self,
        *,
        accounts: Any = None,
        price_timeout: int = 30,
        include_details: bool = False,
    ) -> Dict[str, Any]:
        if self.full_report_func is None:
            return {"success": False, "error": "full_report_func is required"}

        try:
            target_accounts = normalize_accounts(accounts)
            discovery = None
            if target_accounts is None:
                discovery = self.list_accounts(include_default=True)
                if not discovery.get("success"):
                    return discovery
                target_accounts = discovery.get("accounts") or []

            items = []
            errors = []
            summary_values = {
                "total_value": 0.0,
                "cash_value": 0.0,
                "stock_value": 0.0,
                "fund_value": 0.0,
                "non_cash_value": 0.0,
            }

            for account in target_accounts:
                report = self.full_report_func(account=account, price_timeout=price_timeout)
                if not report.get("success"):
                    error = {
                        "account": account,
                        "error": report.get("error") or report.get("message") or "unknown error",
                    }
                    errors.append(error)
                    items.append({"account": account, "success": False, **error})
                    continue

                values = report_value_breakdown(report)
                for key in summary_values:
                    summary_values[key] += values[key]

                item = {
                    "account": account,
                    "success": True,
                    **values,
                    "overview": report.get("overview") or {},
                    "nav": report.get("nav"),
                    "returns": report.get("returns") or {},
                }
                if include_details:
                    item["report"] = report
                items.append(item)

            successful_count = sum(1 for item in items if item.get("success"))
            failed_count = len(errors)
            total_value = summary_values["total_value"]
            summary = {key: _round_money(value) for key, value in summary_values.items()}
            summary.update({
                "cash_ratio": summary["cash_value"] / total_value if total_value > 0 else 0,
                "stock_ratio": summary["stock_value"] / total_value if total_value > 0 else 0,
                "fund_ratio": summary["fund_value"] / total_value if total_value > 0 else 0,
            })

            if not target_accounts:
                status = "empty"
                success = True
            elif successful_count == 0:
                status = "failed"
                success = False
            elif failed_count:
                status = "partial"
                success = True
            else:
                status = "ok"
                success = True

            result = {
                "success": success,
                "status": status,
                "generated_at": bj_now_naive().isoformat(),
                "default_account": self.default_account,
                "accounts": target_accounts,
                "account_count": len(target_accounts),
                "successful_count": successful_count,
                "failed_count": failed_count,
                "summary": summary,
                "items": items,
            }
            if discovery is not None:
                result["discovery"] = discovery
            if errors:
                result["errors"] = errors
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}
