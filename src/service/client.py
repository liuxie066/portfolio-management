"""Small HTTP client for the local portfolio service."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from src import config


class PortfolioServiceError(RuntimeError):
    """Base error raised by the local portfolio service client."""


class PortfolioServiceUnavailable(PortfolioServiceError):
    """Raised when the local service cannot be reached."""


class PortfolioServiceResponseError(PortfolioServiceError):
    """Raised when the local service responds with an invalid/error payload."""


def _query_value(value: Any) -> Any:
    if isinstance(value, set):
        return ",".join(str(item) for item in sorted(value, key=str))
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return value


def _body_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _error_from_collection(value: Any) -> Optional[str]:
    if not isinstance(value, list):
        return None
    for item in value:
        if not isinstance(item, dict):
            continue
        message = item.get("error") or item.get("message") or item.get("detail")
        if not message:
            continue
        account = item.get("account")
        if account:
            return f"{account}: {message}"
        return str(message)
    return None


def _failure_message(payload: Dict[str, Any]) -> str:
    for key in ("error", "message", "detail"):
        value = payload.get(key)
        if value:
            return str(value)
    for key in ("errors", "items"):
        message = _error_from_collection(payload.get(key))
        if message:
            return message
    status = payload.get("status")
    if status:
        return f"status={status}"
    return "unknown service error"


class PortfolioServiceClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 0.5):
        self.base_url = (base_url or config.get_service_url()).rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_params = {
            key: value
            for key, value in (params or {}).items()
            if value is not None
        }
        query = f"?{urlencode(clean_params)}" if clean_params else ""
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}{query}",
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise PortfolioServiceResponseError(f"service returned HTTP {e.code}: {detail}") from e
        except (OSError, URLError) as e:
            raise PortfolioServiceUnavailable(str(e)) from e

        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as e:
            raise PortfolioServiceResponseError(f"service returned non-JSON response: {payload[:120]}") from e

        if not isinstance(decoded, dict):
            raise PortfolioServiceResponseError("service returned non-object JSON")
        if decoded.get("success") is False:
            message = _failure_message(decoded)
            raise PortfolioServiceResponseError(f"service returned success=false: {message}")
        return decoded

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._request("GET", path, params=params)

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", path, body=body)

    def health(self) -> Dict[str, Any]:
        return self._get("/health")

    def is_available(self) -> bool:
        try:
            result = self.health()
        except PortfolioServiceUnavailable:
            return False
        return result.get("success") is True and result.get("service") == "portfolio-management"

    def list_accounts(self, *, include_default: bool = True) -> Dict[str, Any]:
        return self._get("/accounts", {"include_default": include_default})

    def list_nav_accounts(self, *, include_default: bool = False) -> Dict[str, Any]:
        return self._get("/accounts/nav", {"include_default": include_default})

    def multi_account_overview(
        self,
        *,
        accounts: Any = None,
        price_timeout: int = 30,
        include_details: bool = False,
    ) -> Dict[str, Any]:
        return self._get(
            "/accounts/overview",
            {
                "accounts": _query_value(accounts),
                "price_timeout": price_timeout,
                "include_details": include_details,
            },
        )

    def get_holdings(
        self,
        *,
        account: str,
        include_cash: bool = True,
        group_by_market: bool = False,
        include_price: bool = False,
    ) -> Dict[str, Any]:
        return self._get(
            "/holdings",
            {
                "account": account,
                "include_cash": include_cash,
                "group_by_market": group_by_market,
                "include_price": include_price,
            },
        )

    def get_cash(self, *, account: str) -> Dict[str, Any]:
        return self._get("/cash", {"account": account})

    def sync_futu_holdings(
        self,
        *,
        account: Optional[str] = None,
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
    ) -> Dict[str, Any]:
        return self._post("/futu/holdings/sync", {
            "account": account,
            "dry_run": dry_run,
            "confirm": confirm,
            "allow_empty_stock_snapshot": allow_empty_stock_snapshot,
        })

    def get_nav(self, *, account: str, days: int = 30) -> Dict[str, Any]:
        return self._get("/nav", {"account": account, "days": days})

    def get_capital_facts(self, *, account: str, period: str, as_of_month: str) -> Dict[str, Any]:
        return self._get(
            "/analysis/capital-facts",
            {"account": account, "period": period, "as_of_month": as_of_month},
        )

    def record_nav(
        self,
        *,
        account: str,
        nav_date: Optional[Any] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = True,
        use_bulk_persist: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            "account": account,
            "price_timeout": price_timeout,
            "dry_run": dry_run,
            "confirm": confirm,
            "overwrite_existing": overwrite_existing,
            "use_bulk_persist": use_bulk_persist,
        }
        if nav_date is not None:
            body["nav_date"] = _body_value(nav_date)
        if run_id is not None:
            body["run_id"] = run_id
        return self._post("/nav/record", body)

    def audit_nav_history_duplicates(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        return self._get("/nav/duplicates", {"account": account})

    def get_distribution(
        self,
        *,
        account: Optional[str] = None,
        accounts: Any = None,
        by_asset: bool = False,
        include_value: bool = True,
        group_cash: bool = False,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if account is not None:
            params["account"] = account
        if accounts is not None:
            params["accounts"] = _query_value(accounts)
        params["by_asset"] = by_asset
        params["include_value"] = include_value
        if group_cash:
            params["group_cash"] = True
        return self._get("/distribution", params)

    def full_report(self, *, account: str, price_timeout: int = 30) -> Dict[str, Any]:
        return self._get("/report/full", {"account": account, "price_timeout": price_timeout})

    def generate_report(self, *, account: str, report_type: str = "daily", price_timeout: int = 30) -> Dict[str, Any]:
        return self._get(
            f"/report/{quote(report_type, safe='')}",
            {"account": account, "price_timeout": price_timeout},
        )

    def daily_report_bundle(
        self,
        *,
        account: Optional[str],
        nav_date: Optional[Any] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = True,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            "account": account,
            "price_timeout": price_timeout,
            "dry_run": dry_run,
            "confirm": confirm,
            "overwrite_existing": overwrite_existing,
            "use_bulk_persist": use_bulk_persist,
            "sync_futu_cash_mmf": sync_futu_cash_mmf,
        }
        if sync_futu_dry_run is not None:
            body["sync_futu_dry_run"] = sync_futu_dry_run
        if nav_date is not None:
            body["nav_date"] = _body_value(nav_date)
        if run_id is not None:
            body["run_id"] = run_id
        return self._post("/report/daily-bundle", body)

    def daily_nav_job(
        self,
        *,
        account: Optional[str] = None,
        accounts: Any = None,
        nav_date: Optional[Any] = None,
        run_date: Optional[Any] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        force_non_business_day: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            "price_timeout": price_timeout,
            "dry_run": dry_run,
            "confirm": confirm,
            "overwrite_existing": overwrite_existing,
            "use_bulk_persist": use_bulk_persist,
            "sync_futu_cash_mmf": sync_futu_cash_mmf,
            "force_non_business_day": force_non_business_day,
        }
        if sync_futu_dry_run is not None:
            body["sync_futu_dry_run"] = sync_futu_dry_run
        optional = {
            "account": account,
            "accounts": accounts,
            "nav_date": _body_value(nav_date),
            "run_date": _body_value(run_date),
            "run_id": run_id,
        }
        body.update({key: value for key, value in optional.items() if value is not None})
        return self._post("/daily-nav-job", body)
