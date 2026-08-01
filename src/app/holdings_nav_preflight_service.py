"""Fresh holdings integrity gate and frozen snapshot for official daily NAV."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Callable, Iterable, Mapping, Optional

from src.domain.holdings import RawHoldingRecord
from src.models import Holding
from src.process_lock import account_lock_key, process_lock

from .holdings_reconciliation_service import (
    HoldingsReconciliationEvaluation,
    HoldingsReconciliationService,
)
from .holdings_validation import canonical_record_payload
from .holdings_workflow_service import HoldingsWorkflowService


_MAX_ACTION_ITEMS_PER_SCOPE = 5


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid normalized holding number: {value}") from exc
    if not number.is_finite():
        raise ValueError(f"invalid normalized holding number: {value}")
    if number == 0:
        return "0"
    if number == number.to_integral():
        return str(number.quantize(Decimal("1")))
    return format(number.normalize(), "f")


def _pending_case_keys(plan: Mapping[str, Any]) -> list[str]:
    confirmed = {
        str(case_key)
        for case_key in list(plan.get("confirmed_case_keys") or [])
    }
    return [
        str(case_key)
        for case_key in list(plan.get("case_keys") or [])
        if str(case_key) not in confirmed
    ]


def _action_contract(plan: Mapping[str, Any]) -> dict[str, Any]:
    pending_case_keys = _pending_case_keys(plan)
    pending = set(pending_case_keys)
    action_items: list[dict[str, str]] = []
    for receipt in list(plan.get("discovery_receipts") or []):
        payload = dict(receipt.get("payload") or {})
        if str(payload.get("case_key") or "") not in pending:
            continue
        action = dict(payload.get("action") or {})
        command = str(action.get("command") or "").strip()
        if not command:
            continue
        action_items.append(
            {
                "case_key": str(payload.get("case_key") or ""),
                "record_id": str(payload.get("record_id") or ""),
                "field": str(payload.get("field") or ""),
                "state": str(payload.get("state") or ""),
                "command": command,
            }
        )
    total = len(action_items)
    return {
        "pending_case_keys": pending_case_keys,
        "action_items": action_items[:_MAX_ACTION_ITEMS_PER_SCOPE],
        "action_item_count": total,
        "action_item_omitted_count": max(
            total - _MAX_ACTION_ITEMS_PER_SCOPE,
            0,
        ),
    }


@dataclass(frozen=True)
class FrozenHoldingRow:
    record_id: Optional[str]
    asset_id: str
    asset_name: str
    asset_type: str
    account: str
    broker: str
    quantity: float
    avg_cost: Optional[float]
    currency: str
    asset_class: Optional[str]
    industry: Optional[str]
    tag: tuple[str, ...]
    created_at: Optional[str]
    updated_at: Optional[str]

    @classmethod
    def from_holding(cls, holding: Holding) -> "FrozenHoldingRow":
        payload = holding.model_dump(mode="json")
        return cls(
            record_id=payload.get("record_id"),
            asset_id=str(payload["asset_id"]),
            asset_name=str(payload.get("asset_name") or ""),
            asset_type=str(payload["asset_type"]),
            account=str(payload["account"]),
            broker=str(payload.get("broker") or ""),
            quantity=float(payload["quantity"]),
            avg_cost=(
                float(payload["avg_cost"])
                if payload.get("avg_cost") is not None
                else None
            ),
            currency=str(payload["currency"]).upper(),
            asset_class=(
                str(payload["asset_class"])
                if payload.get("asset_class") is not None
                else None
            ),
            industry=(
                str(payload["industry"])
                if payload.get("industry") is not None
                else None
            ),
            tag=tuple(str(item) for item in (payload.get("tag") or [])),
            created_at=(
                str(payload["created_at"])
                if payload.get("created_at") is not None
                else None
            ),
            updated_at=(
                str(payload["updated_at"])
                if payload.get("updated_at") is not None
                else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "account": self.account,
            "broker": self.broker,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "currency": self.currency,
            "asset_class": self.asset_class,
            "industry": self.industry,
            "tag": list(self.tag),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_holding(self) -> Holding:
        return Holding(**self.as_dict())


@dataclass(frozen=True)
class ValidatedHoldingsSnapshot:
    account: str
    rows: tuple[FrozenHoldingRow, ...]
    raw_record_digest: str
    normalized_holdings_digest: str
    source_fetch_time: str
    policy_version: str
    currency_policy_version: str
    asset_class_policy_version: str
    source_mode: str
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_evaluation(
        cls,
        *,
        account: str,
        records: Iterable[RawHoldingRecord],
        evaluation: HoldingsReconciliationEvaluation,
        source_fetch_time: datetime,
        source_mode: str,
        warnings: Iterable[str] = (),
        confirmed_conflict_fields: Optional[Mapping[str, Iterable[str]]] = None,
    ) -> "ValidatedHoldingsSnapshot":
        validations = list(evaluation.report.records)
        confirmed_by_record = {
            str(record_id): {str(field) for field in fields}
            for record_id, fields in dict(confirmed_conflict_fields or {}).items()
        }
        typed = []
        invalid_ids = []
        for item in validations:
            try:
                typed.append(
                    item.to_holding(
                        confirmed_conflict_fields=confirmed_by_record.get(
                            item.raw.record_id,
                            (),
                        )
                    )
                )
            except ValueError:
                invalid_ids.append(item.raw.record_id)
        if invalid_ids:
            raise ValueError(
                "holdings snapshot contains blocking records: "
                + ", ".join(sorted(invalid_ids))
            )
        rows = tuple(
            sorted(
                (
                    FrozenHoldingRow.from_holding(holding)
                    for holding in typed
                ),
                key=lambda row: (
                    row.asset_id,
                    row.account,
                    row.broker,
                    row.record_id or "",
                ),
            )
        )
        raw_facts = sorted(
            (
                {
                    "record_id": record.record_id,
                    "fields": canonical_record_payload(record.raw_fields),
                }
                for record in records
            ),
            key=lambda item: item["record_id"],
        )
        normalized_facts = []
        for row in rows:
            normalized = row.as_dict()
            normalized["quantity"] = _canonical_number(row.quantity)
            normalized["avg_cost"] = _canonical_number(row.avg_cost)
            normalized_facts.append(normalized)
        return cls(
            account=account,
            rows=rows,
            raw_record_digest=_digest(raw_facts),
            normalized_holdings_digest=_digest(normalized_facts),
            source_fetch_time=source_fetch_time.astimezone(UTC).isoformat(),
            policy_version=evaluation.report.policy_version,
            currency_policy_version=evaluation.report.currency_policy_version,
            asset_class_policy_version=(
                evaluation.report.asset_class_policy_version
            ),
            source_mode=source_mode,
            warnings=tuple(str(item) for item in warnings if str(item).strip()),
        )

    def to_valuation_holdings(self) -> list[Holding]:
        """Return private copies because valuation mutates runtime fields."""

        return [row.to_holding() for row in self.rows]

    def provenance(self) -> dict[str, Any]:
        return {
            "account": self.account,
            "record_count": len(self.rows),
            "raw_record_digest": self.raw_record_digest,
            "normalized_holdings_digest": self.normalized_holdings_digest,
            "source_fetch_time": self.source_fetch_time,
            "policy_version": self.policy_version,
            "currency_policy_version": self.currency_policy_version,
            "asset_class_policy_version": self.asset_class_policy_version,
            "source_mode": self.source_mode,
            "warnings": list(self.warnings),
        }


class HoldingsNavPreflightService:
    """Fail-closed holdings gate used only by the canonical daily NAV job."""

    def __init__(
        self,
        *,
        storage: Any,
        reconciliation: Optional[HoldingsReconciliationService] = None,
        workflow: Optional[HoldingsWorkflowService] = None,
        lock_factory: Callable[..., Any] = process_lock,
        now_factory: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.storage = storage
        self.reconciliation = reconciliation or HoldingsReconciliationService(
            storage=storage
        )
        self.workflow = workflow or HoldingsWorkflowService(
            storage=storage,
            reconciliation=self.reconciliation,
        )
        self.lock_factory = lock_factory
        self.now_factory = now_factory or (lambda: datetime.now(UTC))

    def scan_global_orphans(
        self,
        *,
        dry_run: bool,
        confirm: bool,
        trigger: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not dry_run and not confirm:
            raise ValueError("formal holdings preflight requires confirmation")
        records = list(self.storage.get_raw_holdings())
        plan = self.workflow.plan_global_orphans(records, trigger=dict(trigger))
        action_contract = _action_contract(plan)
        if not plan["case_keys"]:
            workflow_result = None
            if not dry_run:
                workflow_result = self.workflow.prove_global_orphans_absent(
                    trigger=dict(trigger),
                    enqueue_receipts=False,
                )
            return {
                "success": True,
                "status": "valid",
                "scope": "global",
                "orphan_count": 0,
                "case_keys": [],
                "pending_case_keys": [],
                "blocking_case_keys": [],
                "workflow": workflow_result,
                **action_contract,
            }
        workflow_result = None
        if not dry_run:
            workflow_result = self.workflow.materialize_plan(
                plan,
                enqueue_receipts=False,
            )
        orphan_records = list(plan["cases"][0]["current"]["orphan_records"])
        return {
            "success": False,
            "status": "holdings_confirmation_required",
            "scope": "global",
            "global_blocker": True,
            "error": "holdings contains records without an account",
            "orphan_count": len(orphan_records),
            "orphan_record_ids": [item["record_id"] for item in orphan_records],
            "case_keys": list(plan["case_keys"]),
            "blocking_case_keys": list(plan["blocking_case_keys"]),
            "would_materialize": dry_run,
            "workflow": workflow_result,
            **action_contract,
        }

    def prepare_account(
        self,
        *,
        account: str,
        dry_run: bool,
        confirm: bool,
        trigger: Mapping[str, Any],
        futu_sync_result: Optional[Mapping[str, Any]] = None,
        project_futu_dry_run: bool = False,
    ) -> dict[str, Any]:
        if not dry_run and not confirm:
            raise ValueError("formal holdings preflight requires confirmation")
        if not dry_run and project_futu_dry_run:
            raise ValueError(
                "formal NAV cannot consume a Futu dry-run projection"
            )
        with self.lock_factory(account_lock_key(account)):
            records = list(self.storage.get_raw_holdings(account=account))
            source_mode = "feishu"
            if project_futu_dry_run:
                records = self._project_futu_plan(
                    records,
                    account=account,
                    result=futu_sync_result,
                )
                source_mode = "projected_futu_dry_run"
            evaluation = self.reconciliation.evaluate_records(
                records,
                account=account,
            )
            plan = self.workflow.plan_evaluation(
                evaluation,
                trigger=dict(trigger),
            )
            plan = self.workflow.apply_outage_manual_confirmations(
                plan,
                evaluation,
            )
            action_contract = _action_contract(plan)
            workflow_result = None
            if not dry_run:
                try:
                    workflow_result = self.workflow.materialize_preflight_plan(
                        plan,
                        evaluation,
                        enqueue_receipts=False,
                    )
                except Exception as exc:
                    return {
                        "success": False,
                        "status": "holdings_preflight_failed",
                        "account": account,
                        "error": (
                            "holdings workflow materialization failed: "
                            f"{str(exc) or exc.__class__.__name__}"
                        ),
                        "raw_record_digest": self._raw_digest(records),
                        "normalized_holdings_digest": None,
                        "case_keys": list(plan["case_keys"]),
                        "blocking_case_keys": list(plan["blocking_case_keys"]),
                        "would_materialize": False,
                        "workflow": None,
                        "validation": self.reconciliation.reconcile_payload(
                            evaluation
                        ),
                        "source_mode": source_mode,
                        **action_contract,
                    }

            validation_payload = self.reconciliation.reconcile_payload(evaluation)
            raw_digest = self._raw_digest(records)
            if plan["blocking_case_keys"]:
                return {
                    "success": False,
                    "status": (
                        "holdings_evidence_unavailable"
                        if evaluation.report.evidence_errors
                        else "holdings_confirmation_required"
                    ),
                    "account": account,
                    "error": (
                        "holdings evidence is unavailable"
                        if evaluation.report.evidence_errors
                        else "holdings requires completion or manual confirmation"
                    ),
                    "raw_record_digest": raw_digest,
                    "normalized_holdings_digest": None,
                    "case_keys": list(plan["case_keys"]),
                    "blocking_case_keys": list(plan["blocking_case_keys"]),
                    "would_materialize": dry_run and bool(plan["case_keys"]),
                    "workflow": workflow_result,
                    "validation": validation_payload,
                    "source_mode": source_mode,
                    **action_contract,
                }

            confirmed_fields: dict[str, set[str]] = {}
            for record_id, fields in dict(
                plan.get("confirmed_fields") or {}
            ).items():
                confirmed_fields.setdefault(str(record_id), set()).update(
                    str(field) for field in fields
                )
            confirmed_keys = set(plan.get("confirmed_case_keys") or [])
            warnings = self._case_warnings(plan["cases"])
            warnings.extend(
                f"holdings evidence unavailable: account={target_account}: {error}"
                for target_account, error in sorted(
                    evaluation.report.evidence_errors.items()
                )
            )
            for case in plan["cases"]:
                if case["case_key"] in confirmed_keys:
                    confirmed_fields.setdefault(case["record_id"], set()).add(
                        case["field"]
                    )
                    warnings.append(
                        "holdings confirmed keep-current: "
                        f"record={case['record_id']} field={case['field']}"
                    )
            planned_case_keys = {case["case_key"] for case in plan["cases"]}
            for case_key in sorted(confirmed_keys - planned_case_keys):
                warnings.append(
                    "holdings confirmed keep-current during evidence outage: "
                    f"case={case_key}"
                )
            source_fetch_time = self._source_fetch_time(records)
            snapshot = ValidatedHoldingsSnapshot.from_evaluation(
                account=account,
                records=records,
                evaluation=evaluation,
                source_fetch_time=source_fetch_time,
                source_mode=source_mode,
                warnings=warnings,
                confirmed_conflict_fields=confirmed_fields,
            )
            return {
                "success": True,
                "status": "valid_with_warnings" if warnings else "valid",
                "account": account,
                "validated_snapshot": snapshot,
                "holdings_snapshot": snapshot.provenance(),
                "warnings": warnings,
                "case_keys": list(plan["case_keys"]),
                "blocking_case_keys": list(plan["blocking_case_keys"]),
                "would_materialize": dry_run and bool(plan["case_keys"]),
                "workflow": workflow_result,
                "validation": validation_payload,
                **action_contract,
            }

    @staticmethod
    def _raw_digest(records: Iterable[RawHoldingRecord]) -> str:
        return _digest(
            sorted(
                (
                    {
                        "record_id": record.record_id,
                        "fields": canonical_record_payload(record.raw_fields),
                    }
                    for record in records
                ),
                key=lambda item: item["record_id"],
            )
        )

    def _source_fetch_time(self, records: Iterable[RawHoldingRecord]) -> datetime:
        times = [
            record.fetched_at
            for record in records
            if isinstance(record.fetched_at, datetime)
        ]
        resolved = max(times) if times else self.now_factory()
        if resolved.tzinfo is None:
            resolved = resolved.replace(tzinfo=UTC)
        return resolved

    @staticmethod
    def _case_warnings(cases: Iterable[Mapping[str, Any]]) -> list[str]:
        return [
            (
                "holdings attention: "
                f"record={case.get('record_id')} field={case.get('field')} "
                f"kind={case.get('kind')}"
            )
            for case in cases
            if not case.get("blocks_official_nav")
        ]

    @staticmethod
    def _project_futu_plan(
        records: list[RawHoldingRecord],
        *,
        account: str,
        result: Optional[Mapping[str, Any]],
    ) -> list[RawHoldingRecord]:
        payload = dict(result or {})
        if not payload.get("success") or payload.get("dry_run") is not True:
            raise ValueError("Futu dry-run projection requires a completed dry-run result")
        stages = dict(payload.get("stages") or {})
        if (stages.get("fund_mmf") or {}).get("status") != "succeeded":
            raise ValueError("Futu dry-run projection is incomplete")
        if payload.get("partial_write_possible"):
            raise ValueError("Futu dry-run projection reports a partial outcome")
        metadata = dict(payload.get("source_metadata") or {})
        if (
            payload.get("source") != "futu-openapi"
            or not str(payload.get("source_snapshot_id") or "").strip()
            or metadata.get("source_snapshot_id") != payload.get("source_snapshot_id")
            or not str(metadata.get("profile_fingerprint") or "").strip()
            or not str(metadata.get("account_fingerprint") or "").strip()
            or str(metadata.get("trd_env") or "").upper() != "REAL"
            or metadata.get("account_verified") is not True
            or metadata.get("pagination_complete") is not True
            or metadata.get("refresh_cache") is not True
            or (metadata.get("fund_mmf") or {}).get("present") is not True
            or (metadata.get("fund_mmf") or {}).get("source_field")
            != "fund_assets"
        ):
            raise ValueError("Futu dry-run projection lacks authoritative provenance")
        try:
            plan_observed_at = datetime.fromisoformat(
                str(metadata.get("observed_at_utc") or "").replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError(
                "Futu dry-run projection observation time is invalid"
            ) from exc
        if plan_observed_at.tzinfo is None:
            raise ValueError("Futu dry-run projection observation time is invalid")

        projected = list(records)
        for index, item_value in enumerate(list(payload.get("items") or [])):
            item = dict(item_value or {})
            fields = dict(item.get("projected_fields") or {})
            required = {
                "asset_id",
                "asset_name",
                "asset_type",
                "account",
                "broker",
                "quantity",
                "currency",
            }
            if not required.issubset(fields):
                raise ValueError("Futu dry-run projection lacks complete holding fields")
            if str(fields.get("account") or "").strip() != account:
                raise ValueError("Futu dry-run projection crossed the account boundary")
            if str(fields.get("asset_id") or "") != str(item.get("asset_id") or ""):
                raise ValueError("Futu dry-run projection identity is inconsistent")
            if _canonical_number(fields.get("quantity")) != _canonical_number(
                item.get("target")
            ):
                raise ValueError("Futu dry-run projection target is inconsistent")

            supplied_record_id = str(fields.pop("record_id", "") or "").strip()
            matches = [
                position
                for position, record in enumerate(projected)
                if (
                    supplied_record_id
                    and record.record_id == supplied_record_id
                )
                or (
                    not supplied_record_id
                    and str(record.raw_fields.get("asset_id") or "")
                    == str(fields.get("asset_id") or "")
                    and str(record.raw_fields.get("account") or "") == account
                    and str(record.raw_fields.get("broker") or "")
                    == str(fields.get("broker") or "")
                )
            ]
            if item.get("created"):
                if matches:
                    raise ValueError("Futu dry-run create projection already exists")
                record_id = (
                    supplied_record_id
                    or f"projected:{account}:{fields['broker']}:{fields['asset_id']}"
                )
                projected.append(
                    RawHoldingRecord(
                        record_id=record_id,
                        raw_fields=fields,
                        source="futu_dry_run_projection",
                        fetched_at=plan_observed_at,
                    )
                )
                continue
            if len(matches) != 1:
                raise ValueError("Futu dry-run update projection is not uniquely anchored")
            current = projected[matches[0]]
            current_fetched_at = current.fetched_at
            projected_fetched_at = (
                max(current_fetched_at, plan_observed_at)
                if isinstance(current_fetched_at, datetime)
                and current_fetched_at.tzinfo is not None
                else plan_observed_at
            )
            projected[matches[0]] = RawHoldingRecord(
                record_id=current.record_id,
                raw_fields=fields,
                source="futu_dry_run_projection",
                fetched_at=projected_fetched_at,
            )
        return projected
