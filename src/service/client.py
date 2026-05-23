"""Small HTTP client for the local portfolio service."""
from __future__ import annotations

import json
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
            message = decoded.get("error") or decoded.get("message") or decoded.get("detail") or "unknown service error"
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

    def get_nav(self, *, account: str, days: int = 30) -> Dict[str, Any]:
        return self._get("/nav", {"account": account, "days": days})

    def record_nav(
        self,
        *,
        account: str,
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
        if run_id is not None:
            body["run_id"] = run_id
        return self._post("/nav/record", body)

    def get_distribution(self, *, account: str) -> Dict[str, Any]:
        return self._get("/distribution", {"account": account})

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
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: bool = True,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        body = {
            "account": account,
            "price_timeout": price_timeout,
            "dry_run": dry_run,
            "confirm": confirm,
            "use_bulk_persist": use_bulk_persist,
            "sync_futu_cash_mmf": sync_futu_cash_mmf,
            "sync_futu_dry_run": sync_futu_dry_run,
        }
        if run_id is not None:
            body["run_id"] = run_id
        return self._post("/report/daily-bundle", body)
