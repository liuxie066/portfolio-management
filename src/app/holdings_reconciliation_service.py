"""Read-only holdings reconciliation orchestration for S1."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .holdings_validation import (
    FutuAccountEvidence,
    FutuPositionEvidence,
    HoldingsEvidenceBundle,
    HoldingsValidator,
)


class HoldingsReconciliationService:
    """Fresh-read and classify holdings without mutating business/workflow state."""

    def __init__(
        self,
        *,
        storage: Any,
        validator: Optional[HoldingsValidator] = None,
        futu_observer: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.storage = storage
        self.validator = validator or HoldingsValidator()
        self.futu_observer = futu_observer or self._observe_futu

    def reconcile(
        self,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if account and record_id:
            raise ValueError("account and record_id are mutually exclusive")
        records = self.storage.get_raw_holdings(account=account, record_id=record_id)
        futu_accounts = sorted(
            {
                str((record.raw_fields.get("account") or "")).strip()
                for record in records
                if self._is_futu(record.raw_fields.get("broker"))
                and str((record.raw_fields.get("account") or "")).strip()
            }
        )
        evidence_by_account: dict[str, FutuAccountEvidence] = {}
        evidence_errors: dict[str, str] = {}
        for target_account in futu_accounts:
            try:
                evidence_by_account[target_account] = self._evidence_from_snapshot(
                    target_account,
                    self.futu_observer(target_account),
                )
            except Exception as exc:
                evidence_errors[target_account] = str(exc)

        report = self.validator.validate(
            records,
            evidence=HoldingsEvidenceBundle(
                futu_by_account=evidence_by_account,
                source_errors=evidence_errors,
            ),
        )
        payload = report.as_dict()
        payload.update(
            {
                "scope": {
                    "account": account,
                    "record_id": record_id,
                },
                "source": "feishu",
                "futu_observation_count": len(evidence_by_account),
            }
        )
        return payload

    def _observe_futu(self, account: str) -> Any:
        from .futu_balance_sync_service import FutuBalanceSyncService

        return FutuBalanceSyncService(self.storage).observe_portfolio(account=account)

    @staticmethod
    def _is_futu(value: Any) -> bool:
        return str(value or "").strip().lower() in {"futu", "moomoo", "富途"}

    @staticmethod
    def _evidence_from_snapshot(account: str, snapshot: Any) -> FutuAccountEvidence:
        source_snapshot_id = str(getattr(snapshot, "source_snapshot_id", "") or "").strip()
        source_as_of = str(getattr(snapshot, "observed_at_utc", "") or "").strip()
        profile_fingerprint = str(
            getattr(snapshot, "profile_fingerprint", "") or ""
        ).strip()
        account_fingerprint = str(
            getattr(snapshot, "account_fingerprint", "") or ""
        ).strip()
        if not all(
            (
                source_snapshot_id,
                source_as_of,
                profile_fingerprint,
                account_fingerprint,
            )
        ):
            raise RuntimeError(
                "Futu observation lacks snapshot, time, profile, or account identity"
            )
        positions = tuple(
            FutuPositionEvidence(
                asset_id=str(getattr(position, "asset_id", "") or ""),
                raw_code=str(getattr(position, "raw_code", "") or ""),
                asset_name=str(getattr(position, "asset_name", "") or ""),
                security_type=str(getattr(position, "security_type", "") or "").upper(),
                market=str(getattr(position, "market", "") or "").upper(),
                currency=(
                    str(getattr(position, "currency", "") or "").upper() or None
                ),
                currency_explicit=bool(getattr(position, "currency_explicit", False)),
            )
            for position in tuple(getattr(snapshot, "positions", ()) or ())
        )
        for position in positions:
            if not position.asset_id or not position.raw_code:
                raise RuntimeError("Futu position evidence lacks an exact identity")
            if position.currency_explicit:
                currency = str(position.currency or "")
                if (
                    not currency.isascii()
                    or not currency.isalpha()
                    or not 3 <= len(currency) <= 5
                ):
                    raise RuntimeError("Futu position explicit currency is invalid")
        return FutuAccountEvidence(
            account=account,
            source=str(getattr(snapshot, "source", "futu") or "futu"),
            source_snapshot_id=source_snapshot_id,
            source_as_of=source_as_of,
            positions=positions,
            profile_fingerprint=profile_fingerprint,
            account_fingerprint=account_fingerprint,
        )
