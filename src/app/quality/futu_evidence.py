from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from src import config

_BEIJING = ZoneInfo("Asia/Shanghai")
_GRACE = timedelta(minutes=15)
_SCHEDULES = (
    ("morning", time(8, 10), frozenset({0, 1, 2, 3, 4, 5})),
    ("evening", time(17, 10), frozenset({0, 1, 2, 3, 4})),
)
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_CASH_SOURCE_FIELDS = {
    "CNY": "cn_cash",
    "USD": "us_cash",
    "HKD": "hk_cash",
}


@dataclass(frozen=True)
class ReceiptFreshness:
    status: str
    reason_code: str
    observed_at_utc: datetime | None
    required_trigger_at_utc: datetime
    expected_by_utc: datetime
    age_seconds: float | None
    grace_seconds: float = _GRACE.total_seconds()

    @property
    def current(self) -> bool:
        return self.status == "fresh"

    def as_payload(self, *, fallback_observed_at_utc: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "observed_at_utc": (
                _iso(self.observed_at_utc)
                if self.observed_at_utc is not None
                else fallback_observed_at_utc
            ),
            "expected_by_utc": _iso(self.expected_by_utc),
            "grace_seconds": self.grace_seconds,
        }
        if self.age_seconds is not None:
            payload["age_seconds"] = self.age_seconds
        return payload


def resolve_account_mappings(accounts: Iterable[str]) -> dict[str, dict[str, Any]]:
    normalized = list(dict.fromkeys(str(item).strip().lower() for item in accounts))
    resolved: dict[str, dict[str, Any]] = {}
    by_fingerprint: dict[str, list[str]] = {}
    for account in normalized:
        try:
            settings = config.get_futu_account_settings(account)
        except ValueError:
            resolved[account] = {
                "valid": False,
                "reason_code": "ACCOUNT_MAPPING_INVALID",
                "settings": None,
            }
            continue
        resolved[account] = {
            "valid": True,
            "reason_code": "ACCOUNT_MAPPING_VALID",
            "settings": settings,
        }
        by_fingerprint.setdefault(settings["account_fingerprint"], []).append(account)

    for duplicate_accounts in by_fingerprint.values():
        if len(duplicate_accounts) < 2:
            continue
        for account in duplicate_accounts:
            resolved[account] = {
                "valid": False,
                "reason_code": "ACCOUNT_MAPPING_DUPLICATE",
                "settings": None,
            }
    return resolved


def evaluate_receipt_freshness(
    receipt: Mapping[str, Any] | None,
    *,
    now: datetime,
) -> ReceiptFreshness:
    normalized_now = _as_utc(now)
    required_trigger, expected_by = _latest_required_window(normalized_now)
    observed_at = _receipt_observed_at(receipt)
    if observed_at is None:
        return ReceiptFreshness(
            status="unknown",
            reason_code="SYNC_RECEIPT_MISSING_OR_INVALID_TIME",
            observed_at_utc=None,
            required_trigger_at_utc=required_trigger,
            expected_by_utc=expected_by,
            age_seconds=None,
        )
    age_seconds = max(0.0, (normalized_now - observed_at).total_seconds())
    if observed_at > normalized_now + timedelta(minutes=5):
        return ReceiptFreshness(
            status="unknown",
            reason_code="SYNC_RECEIPT_CLOCK_SKEW",
            observed_at_utc=observed_at,
            required_trigger_at_utc=required_trigger,
            expected_by_utc=expected_by,
            age_seconds=0.0,
        )
    if observed_at < required_trigger:
        return ReceiptFreshness(
            status="stale",
            reason_code="SYNC_RECEIPT_STALE",
            observed_at_utc=observed_at,
            required_trigger_at_utc=required_trigger,
            expected_by_utc=expected_by,
            age_seconds=age_seconds,
        )
    return ReceiptFreshness(
        status="fresh",
        reason_code="SYNC_RECEIPT_CURRENT",
        observed_at_utc=observed_at,
        required_trigger_at_utc=required_trigger,
        expected_by_utc=expected_by,
        age_seconds=age_seconds,
    )


def source_receipt_complete(
    receipt: Mapping[str, Any] | None,
    *,
    settings: Mapping[str, Any] | None,
) -> bool:
    if not receipt or not settings:
        return False
    metadata = receipt.get("source_metadata")
    if not isinstance(metadata, Mapping):
        return False
    snapshot_id = receipt.get("source_snapshot_id")
    payload_sha256 = str(metadata.get("payload_sha256") or "")
    return bool(
        receipt.get("account") == settings.get("account")
        and snapshot_id
        and metadata.get("source_snapshot_id") == snapshot_id
        and metadata.get("provider") == "futu-openapi"
        and _parse_utc(metadata.get("observed_at_utc")) is not None
        and metadata.get("account_fingerprint") == settings.get("account_fingerprint")
        and metadata.get("profile_fingerprint")
        == settings.get("profile_fingerprint")
        and str(metadata.get("trd_env") or "").upper() == str(settings.get("trd_env") or "").upper()
        and str(metadata.get("trd_market") or "").upper()
        == str(settings.get("trd_market") or "").upper()
        and (metadata.get("cash") or {}).get("mode") == "per_currency"
        and (metadata.get("cash") or {}).get("present") is True
        and (metadata.get("cash") or {}).get("source_fields")
        == _CASH_SOURCE_FIELDS
        and all(
            ((metadata.get("cash") or {}).get("present_by_currency") or {}).get(
                currency
            )
            is True
            for currency in _CASH_SOURCE_FIELDS
        )
        and (metadata.get("fund_mmf") or {}).get("source_field") == "fund_assets"
        and metadata.get("refresh_cache") is True
        and metadata.get("account_verified") is True
        and metadata.get("pagination_complete") is True
        and metadata.get("position_snapshot_included") is True
        and isinstance(metadata.get("position_count"), int)
        and int(metadata["position_count"]) >= 0
        and _SHA256_RE.fullmatch(payload_sha256)
    )


def _latest_required_window(now: datetime) -> tuple[datetime, datetime]:
    local_now = now.astimezone(_BEIJING)
    candidates: list[tuple[datetime, datetime]] = []
    for days_ago in range(9):
        day = local_now.date() - timedelta(days=days_ago)
        for _name, trigger_time, weekdays in _SCHEDULES:
            if day.weekday() not in weekdays:
                continue
            trigger_local = datetime.combine(day, trigger_time, tzinfo=_BEIJING)
            deadline_local = trigger_local + _GRACE
            if deadline_local <= local_now:
                candidates.append(
                    (trigger_local.astimezone(UTC), deadline_local.astimezone(UTC))
                )
    if not candidates:
        raise RuntimeError("no PM synchronization window found")
    return max(candidates, key=lambda item: item[1])


def _receipt_observed_at(receipt: Mapping[str, Any] | None) -> datetime | None:
    metadata = (receipt or {}).get("source_metadata")
    if not isinstance(metadata, Mapping):
        return None
    return _parse_utc(metadata.get("observed_at_utc"))


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("quality clock must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
