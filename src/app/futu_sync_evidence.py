from __future__ import annotations

from datetime import date, datetime, time
import json
import os
from pathlib import Path
from typing import Any

from src import config
from src.domain.snapshot_contracts import digest_payload
from src.time_utils import TZ_SHANGHAI, bj_today


FUTU_ASSET_DATASETS = ("pm.pending_asset", "pm.total_assets")


def futu_holdings_digest(holdings) -> str:
    """Bind the priced facts in one account to its verified Futu readback."""
    rows = [
        [holding.asset_id, holding.asset_type.value, holding.quantity, holding.currency,
         holding.contract_multiplier, holding.asset_class.value if holding.asset_class else None]
        for holding in holdings
        if holding.broker == "富途" and holding.quantity != 0
    ]
    return digest_payload(sorted(rows, key=lambda row: row[0]))


class FutuSyncEvidenceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (config.get_data_dir() / "futu_sync_receipts")

    def save(self, account: str, sync_run_id: str, receipt: dict[str, Any]) -> dict[str, str]:
        account_dir = self.root / account
        history_path = account_dir / "history" / f"{sync_run_id}.json"
        latest_path = account_dir / "latest.json"
        self._atomic_write(history_path, receipt)
        self._atomic_write(latest_path, receipt)
        return {
            "history_ref": f"futu-sync-receipt:{account}:{sync_run_id}",
            "latest_ref": f"futu-sync-receipt:{account}:latest",
        }

    def latest(self, account: str) -> dict[str, Any] | None:
        path = self.root / account / "latest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def manages_cash(self, account: str) -> bool:
        profiles = config.get("futu.profiles", {})
        return account in (profiles or {}) or self.cash_write(account) is not None

    def save_cash_write(self, account: str, payload: dict[str, Any]) -> None:
        self._atomic_write(self.root / account / "cash_write.json", payload)

    def cash_write(self, account: str) -> dict[str, Any] | None:
        path = self.root / account / "cash_write.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def require_nav_capture(self, account: str, nav_date: date, holdings) -> dict[str, Any]:
        from .business_calendar_service import BusinessCalendarService

        latest = self.latest(account)
        if not latest or not latest.get("success"):
            raise ValueError("formal NAV requires a successful Futu portfolio sync")
        verdicts = (latest.get("reconciliation") or {}).get("datasets") or {}
        if any((verdicts.get(key) or {}).get("status") != "trusted" for key in FUTU_ASSET_DATASETS):
            raise ValueError("formal NAV requires verified Futu pending and total assets")
        cash_write = latest.get("cash_write") or {}
        observed_at = datetime.fromisoformat(cash_write.get("observed_at_utc") or "")
        if observed_at.tzinfo is None:
            raise ValueError("Futu sync observation timestamp is not timezone-aware")
        local = observed_at.astimezone(TZ_SHANGHAI)
        cash_started = datetime.fromisoformat(cash_write.get("cash_read_started_at") or "")
        if cash_started.tzinfo is None:
            raise ValueError("Futu cash read timestamp is not timezone-aware")
        local_start = cash_started.astimezone(TZ_SHANGHAI)
        current_date = bj_today()
        if (
            local.date() != current_date
            or local_start.date() != current_date
            # 09:00 Beijing is before the next regular HK/CN/US open.
            or not time(8, 10) <= local_start.time() <= local.time() < time(9)
            or BusinessCalendarService.from_config().default_nav_date(run_date=current_date) != nav_date
        ):
            raise ValueError("Futu capture is outside the early NAV window; use archived valuation evidence")
        if (
            cash_write.get("status") != "verified"
            or cash_write.get("snapshot_id") != latest.get("source_snapshot_id")
            or latest.get("holdings_digest") != futu_holdings_digest(holdings)
        ):
            raise ValueError("current Futu holdings do not match verified NAV capture")
        return cash_write

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
