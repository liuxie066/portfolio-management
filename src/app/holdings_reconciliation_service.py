"""Read-only holdings reconciliation orchestration for S1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .asset_name_service import AssetNameService

from .holdings_validation import (
    FutuAccountEvidence,
    FutuPositionEvidence,
    HoldingsEvidenceBundle,
    HoldingsValidationReport,
    HoldingsValidator,
    holding_broker_scope,
)


@dataclass(frozen=True)
class HoldingsReconciliationEvaluation:
    report: HoldingsValidationReport
    account: Optional[str]
    record_id: Optional[str]
    evidence_by_account: dict[str, FutuAccountEvidence]


class HoldingsReconciliationService:
    """Fresh-read and classify holdings without mutating business/workflow state."""

    def __init__(
        self,
        *,
        storage: Any,
        validator: Optional[HoldingsValidator] = None,
        futu_observer: Optional[Callable[[str], Any]] = None,
        fund_name_lookup: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.storage = storage
        self.validator = validator or HoldingsValidator()
        self.futu_observer = futu_observer or self._observe_futu
        self.fund_name_lookup = fund_name_lookup or AssetNameService.lookup_fund_name

    def reconcile(
        self,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> dict[str, Any]:
        evaluation = self.evaluate(account=account, record_id=record_id)
        return self.reconcile_payload(evaluation)

    @staticmethod
    def reconcile_payload(
        evaluation: HoldingsReconciliationEvaluation,
    ) -> dict[str, Any]:
        payload = evaluation.report.as_dict()
        payload.update(
            {
                "scope": {
                    "account": evaluation.account,
                    "record_id": evaluation.record_id,
                },
                "source": "feishu",
                "futu_observation_count": len(evaluation.evidence_by_account),
            }
        )
        return payload

    def evaluate(
        self,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
        include_fund_name: bool = False,
    ) -> HoldingsReconciliationEvaluation:
        if account and record_id:
            raise ValueError("account and record_id are mutually exclusive")
        records = self.storage.get_raw_holdings(account=account, record_id=record_id)
        return self.evaluate_records(
            records,
            account=account,
            record_id=record_id,
            include_fund_name=include_fund_name,
        )

    def evaluate_records(
        self,
        records: Any,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
        include_fund_name: bool = False,
    ) -> HoldingsReconciliationEvaluation:
        """Validate an already frozen complete source slice."""

        records = list(records)
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

        fund_names = {}
        if include_fund_name and record_id and len(records) == 1:
            raw = records[0]
            if raw.record_id == record_id and self.is_fund_name_candidate(raw.raw_fields):
                code = raw.raw_fields["asset_id"].strip()
                try:
                    name = self.fund_name_lookup(code)
                except Exception:
                    name = None
                if (
                    isinstance(name, dict)
                    and name.get("code") == code
                    and name.get("source") == "tencent_jj"
                    and isinstance(name.get("name"), str)
                    and name["name"].strip()
                    and name["name"].strip() != code
                    and name.get("source_as_of")
                ):
                    fund_names[code] = {**name, "name": name["name"].strip()}
        report = self.validator.validate(
            records,
            evidence=HoldingsEvidenceBundle(
                futu_by_account=evidence_by_account,
                source_errors=evidence_errors,
                fund_names=fund_names,
            ),
        )
        return HoldingsReconciliationEvaluation(
            report=report,
            account=account,
            record_id=record_id,
            evidence_by_account=evidence_by_account,
        )

    @staticmethod
    def is_fund_name_candidate(fields: Any) -> bool:
        code = fields.get("asset_id")
        name = fields.get("asset_name")
        return (
            isinstance(code, str) and code.strip().isascii()
            and code.strip().isdigit() and len(code.strip()) == 6
            and (name is None or isinstance(name, str) and not name.strip())
            and isinstance(fields.get("asset_type"), str)
            and fields["asset_type"] in {"cn_fund", "otc_fund"}
            and isinstance(fields.get("account"), str) and bool(fields["account"].strip())
            and holding_broker_scope(fields.get("broker")) == "non_futu"
        )

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
