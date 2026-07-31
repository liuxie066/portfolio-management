"""Durable cases and explicitly confirmed holdings repair workflows."""

from __future__ import annotations

from contextlib import ExitStack
import getpass
import hashlib
import json
import socket
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from src.process_lock import account_lock_key, holding_record_lock_key, process_lock

from .holdings_reconciliation_service import (
    HoldingsReconciliationEvaluation,
    HoldingsReconciliationService,
)
from .holdings_validation import (
    CURRENCY_POLICY_VERSION,
    RecordValidation,
    VALIDATION_POLICY_VERSION,
    canonical_record_payload,
    record_digest,
)
from .operation_state_store import OperationStateStore


CASE_CONTRACT_VERSION = "holdings-case.v1"
MANUAL_APPLY_POLICY_VERSION = "holdings-manual-apply.v1"
_ACTIONABLE_OUTCOMES = {
    "missing_completable",
    "conflict",
    "missing_manual",
    "invalid",
}


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


def operator_context(*, command_mode: str) -> Dict[str, Any]:
    return {
        "username": getpass.getuser(),
        "hostname": socket.gethostname(),
        "command_mode": str(command_mode),
        "trusted_identity": False,
    }


class HoldingsWorkflowService:
    def __init__(
        self,
        *,
        storage: Any,
        store: Optional[OperationStateStore] = None,
        reconciliation: Optional[HoldingsReconciliationService] = None,
        lock_factory: Any = process_lock,
    ) -> None:
        self.storage = storage
        self._store = store
        self.reconciliation = reconciliation or HoldingsReconciliationService(
            storage=storage
        )
        self.lock_factory = lock_factory

    @property
    def store(self) -> OperationStateStore:
        """Initialize durable state only when a mutating/read-state path needs it."""

        if self._store is None:
            self._store = OperationStateStore()
        return self._store

    def notify(
        self,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
        trigger: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        evaluation = self.reconciliation.evaluate(
            account=account,
            record_id=record_id,
        )
        workflow = self._materialize_evaluation(
            evaluation,
            trigger=trigger or {"mode": "manual_notify"},
            prove_external=True,
        )
        payload = self.reconciliation.reconcile_payload(evaluation)
        payload.update({"read_only": False, "workflow": workflow})
        return payload

    def plan_event_notification(
        self,
        *,
        record_id: str,
        trigger: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Fresh-read one record and build a transaction-ready event outcome."""

        resolved_record_id = str(record_id or "").strip()
        if not resolved_record_id:
            raise ValueError("event notification requires one record id")
        evaluation = self.reconciliation.evaluate(record_id=resolved_record_id)
        if evaluation.report.evidence_errors:
            errors = "; ".join(
                f"{account}: {error}"
                for account, error in sorted(
                    evaluation.report.evidence_errors.items()
                )
            )
            raise RuntimeError(
                f"holding event provider evidence unavailable: {errors}"
            )
        records = list(evaluation.report.records)
        if not records:
            return {
                "record_id": resolved_record_id,
                "cases": [],
                "discovery_receipts": [],
                "active_case_keys": [],
                "record_digest": "",
                "current_identity": {},
                "prove_external": False,
                "trigger": dict(trigger),
                "validation": self.reconciliation.reconcile_payload(evaluation),
                "record_status": "stale_record_missing",
            }
        validation = self._single_validation(evaluation, resolved_record_id)
        cases = self._cases_for_record(validation, evaluation)
        account = str(validation.raw.raw_fields.get("account") or "").strip()
        evidence_complete = not evaluation.report.evidence_errors or (
            account not in evaluation.report.evidence_errors
        )
        return {
            "record_id": resolved_record_id,
            "cases": cases,
            "discovery_receipts": [
                self._discovery_receipt(item, trigger=trigger) for item in cases
            ],
            "active_case_keys": [item["case_key"] for item in cases],
            "record_digest": validation.record_digest,
            "current_identity": self._raw_identity(validation.raw.raw_fields),
            "prove_external": evidence_complete,
            "trigger": dict(trigger),
            "validation": self.reconciliation.reconcile_payload(evaluation),
            "record_status": "validated",
        }

    def plan_evaluation(
        self,
        evaluation: HoldingsReconciliationEvaluation,
        *,
        trigger: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build one transaction-ready workflow plan without mutating state."""

        cases = [
            case
            for validation in evaluation.report.records
            for case in self._cases_for_record(validation, evaluation)
        ]
        stored = self._stored_cases_read_only(
            [case["case_key"] for case in cases]
        )
        confirmed_case_keys = []
        for case in cases:
            durable = stored.get(case["case_key"])
            if not durable or durable.get("state") != "resolved_keep":
                continue
            resolution = dict(durable.get("resolution") or {})
            if resolution.get("confirmation_scope") == self._confirmation_scope(case):
                confirmed_case_keys.append(case["case_key"])
        return {
            "cases": cases,
            "discovery_receipts": [
                self._discovery_receipt(case, trigger=trigger) for case in cases
            ],
            "case_keys": [case["case_key"] for case in cases],
            "blocking_case_keys": [
                case["case_key"]
                for case in cases
                if case.get("blocks_official_nav")
                and case["case_key"] not in confirmed_case_keys
            ],
            "confirmed_case_keys": confirmed_case_keys,
            "trigger": dict(trigger),
        }

    def materialize_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically materialize every case and receipt in a prepared plan."""

        return self.store.materialize_holding_cases(
            cases=list(plan.get("cases") or []),
            discovery_receipts=list(plan.get("discovery_receipts") or []),
            trigger=dict(plan.get("trigger") or {}),
        )

    def materialize_preflight_plan(
        self,
        plan: Dict[str, Any],
        evaluation: HoldingsReconciliationEvaluation,
    ) -> Dict[str, Any]:
        """Materialize current cases and prove repaired account cases closed."""

        combined = self.materialize_plan(plan)
        active_by_record: Dict[str, list[str]] = {}
        for case in list(plan.get("cases") or []):
            active_by_record.setdefault(str(case["record_id"]), []).append(
                str(case["case_key"])
            )
        for validation in evaluation.report.records:
            raw_account = str(
                validation.raw.raw_fields.get("account") or ""
            ).strip()
            if raw_account in evaluation.report.evidence_errors:
                continue
            closed = self.store.resolve_holding_cases_external(
                record_id=validation.raw.record_id,
                active_case_keys=active_by_record.get(
                    validation.raw.record_id,
                    [],
                ),
                record_digest=validation.record_digest,
                current_identity=self._raw_identity(validation.raw.raw_fields),
                trigger=dict(plan.get("trigger") or {}),
            )
            for key, values in closed.items():
                combined.setdefault(key, []).extend(values)
        return combined

    def prove_global_orphans_absent(
        self,
        *,
        trigger: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Close prior synthetic global orphan cases after a fresh empty scan."""

        return self.store.resolve_holding_cases_external(
            record_id="__global_orphan_holdings__",
            active_case_keys=[],
            record_digest=_digest([]),
            current_identity={"asset_id": None, "account": None, "broker": None},
            trigger=dict(trigger),
        )

    def apply_outage_manual_confirmations(
        self,
        plan: Dict[str, Any],
        evaluation: HoldingsReconciliationEvaluation,
    ) -> Dict[str, Any]:
        """Give exact durable manual decisions precedence during Futu outage."""

        outage_accounts = set(evaluation.report.evidence_errors)
        if not outage_accounts:
            return plan
        records = {
            validation.raw.record_id: validation.raw
            for validation in evaluation.report.records
        }
        durable_cases: list[Dict[str, Any]] = []
        for account in sorted(outage_accounts):
            if self._store is not None:
                durable_cases.extend(
                    self._store.list_holding_cases(
                        account=account,
                        state="resolved_keep",
                    )
                )
            else:
                durable_cases.extend(
                    OperationStateStore.list_holding_cases_read_only(
                        account=account,
                        state="resolved_keep",
                    )
                )

        confirmed_fields: Dict[str, set[str]] = {}
        confirmed_case_keys: list[str] = []
        for case in durable_cases:
            raw = records.get(str(case.get("record_id") or ""))
            if raw is None or not str(case.get("authority_id") or "").startswith(
                "futu:"
            ):
                continue
            field = str(case.get("field") or "")
            expected_policy = VALIDATION_POLICY_VERSION
            if field == "currency":
                expected_policy += f"+{CURRENCY_POLICY_VERSION}"
            if case.get("policy_version") != expected_policy:
                continue
            identity = self._raw_identity(raw.raw_fields)
            if identity != dict(case.get("identity") or {}):
                continue
            canonical = canonical_record_payload(raw.raw_fields)
            precondition = _digest(
                {
                    "record_id": raw.record_id,
                    "identity": identity,
                    "field": field,
                    "current": canonical.get(field),
                    "authority_inputs": {
                        key: canonical.get(key)
                        for key in ("asset_id", "asset_type", "account", "broker")
                    },
                }
            )
            resolution = dict(case.get("resolution") or {})
            if (
                precondition != case.get("case_precondition_digest")
                or resolution.get("confirmation_scope")
                != self._confirmation_scope(case)
            ):
                continue
            confirmed_fields.setdefault(raw.record_id, set()).add(field)
            confirmed_case_keys.append(str(case["case_key"]))

        if not confirmed_fields:
            return plan
        cases = [
            case
            for case in list(plan.get("cases") or [])
            if str(case.get("field") or "")
            not in confirmed_fields.get(str(case.get("record_id") or ""), set())
        ]
        filtered = dict(plan)
        filtered.update(
            {
                "cases": cases,
                "discovery_receipts": [
                    self._discovery_receipt(case, trigger=plan.get("trigger"))
                    for case in cases
                ],
                "case_keys": [case["case_key"] for case in cases],
                "blocking_case_keys": [
                    case["case_key"]
                    for case in cases
                    if case.get("blocks_official_nav")
                ],
                "confirmed_case_keys": list(
                    dict.fromkeys(
                        [
                            *(plan.get("confirmed_case_keys") or []),
                            *confirmed_case_keys,
                        ]
                    )
                ),
                "confirmed_fields": {
                    record_id: sorted(fields)
                    for record_id, fields in confirmed_fields.items()
                },
            }
        )
        return filtered

    def _stored_cases_read_only(
        self,
        case_keys: list[str],
    ) -> Dict[str, Dict[str, Any]]:
        if self._store is not None:
            return {
                case_key: stored
                for case_key in case_keys
                if (stored := self._store.get_holding_case(case_key)) is not None
            }
        return OperationStateStore.get_holding_cases_read_only(case_keys)

    def plan_global_orphans(
        self,
        records: Iterable[Any],
        *,
        trigger: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Collapse all missing-account rows into one global NAV blocker."""

        orphan_facts = sorted(
            (
                {
                    "record_id": str(record.record_id),
                    "record_digest": record_digest(record.raw_fields),
                }
                for record in records
                if not str(record.raw_fields.get("account") or "").strip()
            ),
            key=lambda item: item["record_id"],
        )
        if not orphan_facts:
            return {
                "cases": [],
                "discovery_receipts": [],
                "case_keys": [],
                "blocking_case_keys": [],
                "trigger": dict(trigger),
            }

        aggregate_digest = _digest(orphan_facts)
        identity = {"asset_id": None, "account": None, "broker": None}
        current = {"orphan_records": orphan_facts}
        precondition = _digest(
            {
                "scope": "global",
                "field": "account",
                "current": current,
            }
        )
        case_key = _digest(
            {
                "contract_version": CASE_CONTRACT_VERSION,
                "scope": "global",
                "field": "account",
                "kind": "orphan_global",
                "current": current,
                "policy_version": VALIDATION_POLICY_VERSION,
            }
        )
        case = {
            "case_key": case_key,
            "record_id": "__global_orphan_holdings__",
            "account": None,
            "field": "__global__:account",
            "kind": "orphan_global",
            "blocks_official_nav": True,
            "policy_version": VALIDATION_POLICY_VERSION,
            "authority": None,
            "authority_id": None,
            "current": current,
            "proposed": None,
            "record_digest": aggregate_digest,
            "case_precondition_digest": precondition,
            "latest_evidence_instance_id": None,
            "evidence": {},
            "state": "pending_manual_edit",
            "identity": identity,
            "reason_code": "ACCOUNT_ORPHAN_GLOBAL_BLOCKER",
        }
        return {
            "cases": [case],
            "discovery_receipts": [
                self._discovery_receipt(case, trigger=trigger)
            ],
            "case_keys": [case_key],
            "blocking_case_keys": [case_key],
            "trigger": dict(trigger),
        }

    def list_cases(
        self,
        *,
        account: Optional[str] = None,
        state: Optional[str] = None,
    ) -> Dict[str, Any]:
        cases = self.store.list_holding_cases(account=account, state=state)
        return {"success": True, "count": len(cases), "cases": cases}

    def show_case(self, case_key: str) -> Dict[str, Any]:
        case = self.store.get_holding_case(case_key)
        if not case:
            raise KeyError(f"holding case not found: {case_key}")
        return {
            "success": True,
            "case": case,
            "events": self.store.list_holding_case_events(case_key),
        }

    def apply_missing(
        self,
        *,
        record_id: str,
        confirmed_operator: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved_record_id = str(record_id or "").strip()
        if not resolved_record_id:
            raise ValueError("single-record apply requires --record-id")
        account = self._initial_account(resolved_record_id)
        with self._locked(account, resolved_record_id):
            evaluation = self.reconciliation.evaluate(record_id=resolved_record_id)
            validation = self._single_validation(evaluation, resolved_record_id)
            locked_account = str(validation.raw.raw_fields.get("account") or "").strip()
            if not locked_account or locked_account != account:
                raise ValueError("holding account changed before confirmed apply")
            observed_cases = self._cases_for_record(validation, evaluation)
            case_by_field = {item["field"]: item for item in observed_cases}
            targets = [
                outcome
                for outcome in validation.outcomes
                if outcome.status == "missing_completable"
            ]
            if not targets:
                workflow = self._materialize_evaluation(
                    evaluation,
                    trigger={
                        "mode": "manual_apply_no_eligible",
                        "operator": confirmed_operator,
                    },
                    prove_external=False,
                )
                return {
                    "success": True,
                    "status": "no_eligible_missing_fields",
                    "record_id": resolved_record_id,
                    "workflow": workflow,
                }
            return self._execute_patch(
                validation=validation,
                cases=[case_by_field[item.field] for item in targets],
                field_targets={item.field: item.proposed for item in targets},
                allowed_states=("pending_apply", "failed_retryable"),
                decision="accept-proposed",
                reason="explicit missing-field completion",
                confirmed_operator=confirmed_operator,
                observed_cases=observed_cases,
                discovery_receipts=[
                    self._discovery_receipt(item) for item in observed_cases
                ],
            )

    def resolve(
        self,
        *,
        case_key: str,
        decision: str,
        reason: str,
        confirmed_operator: Dict[str, Any],
    ) -> Dict[str, Any]:
        if decision not in {"accept-proposed", "keep-current"}:
            raise ValueError(f"unsupported holding decision: {decision}")
        resolved_reason = str(reason or "").strip()
        if not resolved_reason:
            raise ValueError("holding conflict decision requires a nonblank reason")
        stored = self._require_case(case_key)
        if stored["kind"] != "conflict":
            raise ValueError("only conflict cases can be resolved by decision")
        account = str(stored.get("account") or "").strip()
        if not account:
            raise ValueError("holding conflict case lacks an account")
        with self._locked(account, stored["record_id"]):
            evaluation = self.reconciliation.evaluate(record_id=stored["record_id"])
            validation = self._single_validation(evaluation, stored["record_id"])
            observed_cases = self._cases_for_record(validation, evaluation)
            current_cases = {item["case_key"]: item for item in observed_cases}
            current = current_cases.get(case_key)
            if current is None:
                raise ValueError("holding confirmation scope changed; reconcile again")
            self._require_same_scope(stored, current)
            confirmation_scope = self._confirmation_scope(current)
            if stored["state"] == "resolved_keep" and decision == "keep-current":
                return {
                    "success": True,
                    "status": "already_resolved",
                    "case": stored,
                }
            resolution = {
                "decision": decision,
                "reason": resolved_reason,
                "operator_context": dict(confirmed_operator),
                "confirmation_scope": confirmation_scope,
            }
            if decision == "keep-current":
                self.store.materialize_holding_cases(
                    cases=observed_cases,
                    discovery_receipts=[
                        self._discovery_receipt(item) for item in observed_cases
                    ],
                    trigger={
                        "mode": "manual_resolve_keep",
                        "operator": confirmed_operator,
                    },
                )
                self.store.resolve_holding_cases_external(
                    record_id=validation.raw.record_id,
                    active_case_keys=[item["case_key"] for item in observed_cases],
                    record_digest=validation.record_digest,
                    current_identity=self._raw_identity(validation.raw.raw_fields),
                    trigger={
                        "mode": "manual_resolve_keep",
                        "operator": confirmed_operator,
                    },
                )
                receipt = self._terminal_receipt(
                    current,
                    state="resolved_keep",
                    resolution=resolution,
                    resolution_digest=confirmation_scope,
                )
                self.store.finalize_holding_cases(
                    outcomes=[
                        {
                            "case_key": case_key,
                            "state": "resolved_keep",
                            "event_type": "resolved_keep",
                            "resolution": resolution,
                        }
                    ],
                    receipts=[receipt],
                )
                return {
                    "success": True,
                    "status": "resolved_keep",
                    "case_key": case_key,
                    "wrote_holdings": False,
                }
            return self._execute_patch(
                validation=validation,
                cases=[current],
                field_targets={current["field"]: current["proposed"]},
                allowed_states=("pending_confirmation", "failed_retryable"),
                decision=decision,
                reason=resolved_reason,
                confirmed_operator=confirmed_operator,
                observed_cases=observed_cases,
                discovery_receipts=[
                    self._discovery_receipt(item) for item in observed_cases
                ],
            )

    def recover(
        self,
        *,
        case_key: str,
        confirmed_operator: Dict[str, Any],
    ) -> Dict[str, Any]:
        case = self._require_case(case_key)
        if case["state"] in {
            "resolved_accept",
            "resolved_keep",
            "resolved_external",
            "superseded",
        }:
            return {"success": True, "status": "already_terminal", "case": case}
        recoverable_states = {
            "applying",
            "failed_retryable",
            "apply_outcome_unknown",
        }
        if case["state"] not in recoverable_states:
            raise ValueError(
                f"holding case has no recoverable apply attempt: {case_key}: "
                f"{case['state']}"
            )
        if not str(case.get("apply_attempt_id") or "").strip() or case.get("target") is None:
            raise ValueError(
                f"holding case lacks durable apply facts: {case_key}"
            )
        account = str(case.get("account") or "").strip()
        if not account:
            raise ValueError("holding recovery case lacks an account")
        with self._locked(account, case["record_id"]):
            locked_case = self._require_case(case_key)
            if (
                locked_case["state"] not in recoverable_states
                or locked_case.get("apply_attempt_id") != case.get("apply_attempt_id")
            ):
                raise ValueError("holding recovery state changed while acquiring locks")
            case = locked_case
            try:
                records = self.storage.get_raw_holdings(record_id=case["record_id"])
                if len(records) != 1:
                    raise RuntimeError("holding recovery read did not return one record")
                raw = records[0]
                observed = raw.raw_fields.get(case["field"])
                readback_digest = record_digest(raw.raw_fields)
                observed_identity = self._raw_identity(raw.raw_fields)
                identity_matches = observed_identity == dict(case.get("identity") or {})
                read_error = None
            except Exception as exc:
                observed = None
                readback_digest = None
                observed_identity = None
                identity_matches = False
                read_error = str(exc) or exc.__class__.__name__

            remote_started = bool(case.get("remote_attempt_started_at"))
            if read_error:
                state = "apply_outcome_unknown" if remote_started else "failed_retryable"
            elif not identity_matches:
                state = "superseded"
            elif self._same_value(observed, case.get("target")):
                state = "resolved_accept"
            elif self._same_value(observed, case.get("before")):
                state = "apply_outcome_unknown" if remote_started else "failed_retryable"
            else:
                state = "superseded"
            resolution = {
                "decision": "recover",
                "already_applied": state == "resolved_accept",
                "observed": observed,
                "readback_digest": readback_digest,
                "read_error": read_error,
                "observed_identity": observed_identity,
                "expected_identity": case.get("identity"),
                "operator_context": dict(confirmed_operator),
                "apply_attempt_id": case.get("apply_attempt_id"),
            }
            receipts = []
            if state in {"resolved_accept", "superseded"}:
                resolution_digest = self._apply_resolution_digest(case, resolution)
                receipts.append(
                    self._terminal_receipt(
                        case,
                        state=state,
                        resolution=resolution,
                        resolution_digest=resolution_digest,
                    )
                )
            elif state == "apply_outcome_unknown":
                attention = self._attention_receipt(case, resolution)
                if self.store.get_operation_receipt(attention["receipt_key"]) is None:
                    receipts.append(attention)
            self.store.finalize_holding_cases(
                outcomes=[
                    {
                        "case_key": case_key,
                        "state": state,
                        "event_type": "recovered",
                        "resolution": resolution,
                        "last_error": read_error,
                        "apply_attempt_id": case.get("apply_attempt_id"),
                    }
                ],
                receipts=receipts,
            )
            return {
                "success": state in {"resolved_accept", "superseded", "failed_retryable"},
                "status": state,
                "case_key": case_key,
                "observed": observed,
            }

    def _execute_patch(
        self,
        *,
        validation: RecordValidation,
        cases: list[Dict[str, Any]],
        field_targets: Dict[str, Any],
        allowed_states: Iterable[str],
        decision: str,
        reason: str,
        confirmed_operator: Dict[str, Any],
        observed_cases: Optional[list[Dict[str, Any]]] = None,
        discovery_receipts: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        attempt_id = f"holdapply_{uuid4().hex}"
        prepared = []
        for case in cases:
            stored = self.store.get_holding_case(case["case_key"])
            if stored is not None:
                self._require_same_scope(stored, case)
            elif observed_cases is None:
                raise KeyError(f"holding case not found: {case['case_key']}")
            current_raw = validation.raw.raw_fields.get(case["field"])
            prepared.append(
                {
                    "case_key": case["case_key"],
                    "case_precondition_digest": case["case_precondition_digest"],
                    "allowed_states": tuple(allowed_states),
                    "target": field_targets[case["field"]],
                    "before": current_raw,
                    "decision": decision,
                    "reason": reason,
                    "confirmation_scope": self._confirmation_scope(case),
                }
            )
        workflow = None
        if observed_cases is not None:
            workflow = self.store.materialize_and_prepare_holding_apply(
                observed_cases=observed_cases,
                discovery_receipts=list(discovery_receipts or ()),
                apply_cases=prepared,
                apply_attempt_id=attempt_id,
                operator_context=confirmed_operator,
                trigger={"mode": "manual_apply", "operator": confirmed_operator},
            )
        else:
            self.store.prepare_holding_apply(
                cases=prepared,
                apply_attempt_id=attempt_id,
                operator_context=confirmed_operator,
            )
        case_keys = [item["case_key"] for item in cases]
        self.store.mark_holding_remote_attempt(
            case_keys=case_keys,
            apply_attempt_id=attempt_id,
        )

        patch_error: Optional[str] = None
        readback = None
        try:
            readback = self.storage.patch_holding_record(
                record_id=validation.raw.record_id,
                fields=field_targets,
            )
        except Exception as exc:
            patch_error = str(exc) or exc.__class__.__name__
            try:
                records = self.storage.get_raw_holdings(
                    record_id=validation.raw.record_id
                )
                readback = records[0] if len(records) == 1 else None
            except Exception:
                readback = None

        readback_digest = (
            record_digest(readback.raw_fields) if readback is not None else None
        )
        outcomes = []
        receipts = []
        states = []
        for case in cases:
            observed = (
                readback.raw_fields.get(case["field"])
                if readback is not None
                else None
            )
            target = field_targets[case["field"]]
            before = validation.raw.raw_fields.get(case["field"])
            if readback is None:
                state = "apply_outcome_unknown"
            elif self._same_value(observed, target):
                state = "resolved_accept"
            elif self._same_value(observed, before):
                state = "apply_outcome_unknown"
            else:
                state = "superseded"
            resolution = {
                "decision": decision,
                "reason": reason,
                "operator_context": dict(confirmed_operator),
                "apply_attempt_id": attempt_id,
                "before": before,
                "target": target,
                "readback": observed,
                "readback_digest": readback_digest,
                "patch_error": patch_error,
            }
            outcomes.append(
                {
                    "case_key": case["case_key"],
                    "state": state,
                    "event_type": "apply_readback_classified",
                    "resolution": resolution,
                    "last_error": patch_error if state != "resolved_accept" else None,
                    "apply_attempt_id": attempt_id,
                }
            )
            durable_case = {**case, "apply_attempt_id": attempt_id}
            if state == "apply_outcome_unknown":
                receipts.append(self._attention_receipt(durable_case, resolution))
            else:
                receipts.append(
                    self._terminal_receipt(
                        durable_case,
                        state=state,
                        resolution=resolution,
                        resolution_digest=self._apply_resolution_digest(
                            durable_case,
                            resolution,
                        ),
                    )
                )
            states.append(state)
        self.store.finalize_holding_cases(outcomes=outcomes, receipts=receipts)
        success = all(state == "resolved_accept" for state in states)
        return {
            "success": success,
            "status": "resolved_accept" if success else "apply_attention_required",
            "record_id": validation.raw.record_id,
            "apply_attempt_id": attempt_id,
            "patched_fields": dict(field_targets),
            "case_states": dict(zip(case_keys, states)),
            "patch_error": patch_error,
            "workflow": workflow,
        }
    def _materialize_evaluation(
        self,
        evaluation: HoldingsReconciliationEvaluation,
        *,
        trigger: Dict[str, Any],
        prove_external: bool,
    ) -> Dict[str, Any]:
        combined = {
            "created_case_keys": [],
            "refreshed_case_keys": [],
            "reopened_case_keys": [],
            "superseded_case_keys": [],
            "closed_case_keys": [],
            "enqueued_receipt_keys": [],
        }
        for validation in evaluation.report.records:
            cases = self._cases_for_record(validation, evaluation)
            receipts = [self._discovery_receipt(item) for item in cases]
            stored = self.store.materialize_holding_cases(
                cases=cases,
                discovery_receipts=receipts,
                trigger=trigger,
            )
            for key, values in stored.items():
                combined.setdefault(key, []).extend(values)
            account = str(validation.raw.raw_fields.get("account") or "").strip()
            evidence_complete = not evaluation.report.evidence_errors or (
                account not in evaluation.report.evidence_errors
            )
            if prove_external and evidence_complete:
                closed = self.store.resolve_holding_cases_external(
                    record_id=validation.raw.record_id,
                    active_case_keys=[item["case_key"] for item in cases],
                    record_digest=validation.record_digest,
                    current_identity=self._raw_identity(validation.raw.raw_fields),
                    trigger=trigger,
                )
                for key, values in closed.items():
                    combined.setdefault(key, []).extend(values)
        return combined

    def _cases_for_record(
        self,
        validation: RecordValidation,
        evaluation: HoldingsReconciliationEvaluation,
    ) -> list[Dict[str, Any]]:
        result = []
        for outcome in validation.outcomes:
            if outcome.status not in _ACTIONABLE_OUTCOMES:
                continue
            result.append(
                self._build_case(
                    validation,
                    evaluation,
                    field=outcome.field,
                    kind=outcome.status,
                    current=outcome.current,
                    proposed=outcome.proposed,
                    authority=outcome.authority,
                    authority_id=outcome.authority_id,
                    blocks=outcome.blocks_official_nav,
                    evidence=dict(outcome.evidence or {}),
                    reason_code=outcome.reason_code,
                )
            )
        for issue in validation.issues:
            result.append(
                self._build_case(
                    validation,
                    evaluation,
                    field=f"__record__:{issue.kind}",
                    kind=issue.kind,
                    current=dict(issue.details),
                    proposed=None,
                    authority=None,
                    authority_id=None,
                    blocks=issue.blocks_official_nav,
                    evidence={},
                    reason_code=issue.reason_code,
                )
            )
        return result

    def _build_case(
        self,
        validation: RecordValidation,
        evaluation: HoldingsReconciliationEvaluation,
        *,
        field: str,
        kind: str,
        current: Any,
        proposed: Any,
        authority: Optional[str],
        authority_id: Optional[str],
        blocks: bool,
        evidence: Dict[str, Any],
        reason_code: str,
    ) -> Dict[str, Any]:
        raw = validation.raw.raw_fields
        identity = {
            "asset_id": str(raw.get("asset_id") or "").strip() or None,
            "account": str(raw.get("account") or "").strip() or None,
            "broker": str(raw.get("broker") or "").strip() or None,
        }
        policy_version = evaluation.report.policy_version
        if field == "currency":
            policy_version += f"+{evaluation.report.currency_policy_version}"
        precondition_payload = {
            "record_id": validation.raw.record_id,
            "identity": identity,
            "field": field,
            "current": current,
            "authority_inputs": {
                key: canonical_record_payload(raw).get(key)
                for key in ("asset_id", "asset_type", "account", "broker")
            },
        }
        case_precondition_digest = _digest(precondition_payload)
        case_key = _digest(
            {
                "contract_version": CASE_CONTRACT_VERSION,
                "record_id": validation.raw.record_id,
                "identity": identity,
                "field": field,
                "kind": kind,
                "current": current,
                "proposed": proposed,
                "authority_id": authority_id,
                "policy_version": policy_version,
            }
        )
        evidence_instance_id = (
            _digest(
                {
                    "source": evidence.get("source"),
                    "source_snapshot_id": evidence.get("source_snapshot_id"),
                    "source_as_of": evidence.get("source_as_of"),
                    "evidence": evidence,
                }
            )
            if evidence
            else None
        )
        state = (
            "pending_apply"
            if kind == "missing_completable"
            else "pending_confirmation"
            if kind == "conflict"
            else "pending_manual_edit"
        )
        return {
            "case_key": case_key,
            "record_id": validation.raw.record_id,
            "account": identity["account"],
            "field": field,
            "kind": kind,
            "blocks_official_nav": blocks,
            "policy_version": policy_version,
            "authority": authority,
            "authority_id": authority_id,
            "current": current,
            "proposed": proposed,
            "record_digest": validation.record_digest,
            "case_precondition_digest": case_precondition_digest,
            "latest_evidence_instance_id": evidence_instance_id,
            "evidence": evidence,
            "state": state,
            "identity": identity,
            "reason_code": reason_code,
        }

    def _discovery_receipt(
        self,
        case: Dict[str, Any],
        *,
        trigger: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if case["kind"] == "orphan_global":
            action = {
                "description": "请在 holdings 表补齐缺失账户后重新证明",
                "command": "pm holdings reconcile --notify --confirm",
            }
        elif case["state"] == "pending_apply":
            action = {
                "description": "确认后只补全该记录仍为空且证据一致的字段",
                "command": (
                    f"pm holdings reconcile --record-id {case['record_id']} "
                    "--apply --confirm"
                ),
            }
        elif case["state"] == "pending_confirmation":
            action = {
                "description": "冲突需要人工选择并填写理由",
                "command": (
                    f"pm holdings resolve --case-key {case['case_key']} "
                    "--decision accept-proposed|keep-current --reason REASON --confirm"
                ),
                "allowed_decisions": ["accept-proposed", "keep-current"],
            }
        else:
            action = {
                "description": "请在 holdings 表人工修复后重新证明",
                "command": (
                    f"pm holdings reconcile --record-id {case['record_id']} "
                    "--notify --confirm"
                ),
            }
        payload = {
            "case_key": case["case_key"],
            "record_id": case["record_id"],
            "account": case["account"],
            "identity": case["identity"],
            "field": case["field"],
            "kind": case["kind"],
            "state": case["state"],
            "current": case["current"],
            "proposed": case["proposed"],
            "authority": case.get("authority"),
            "authority_id": case.get("authority_id"),
            "evidence_as_of": (case.get("evidence") or {}).get("source_as_of"),
            "evidence": case.get("evidence") or {},
            "blocks_official_nav": case["blocks_official_nav"],
            "reason_code": case["reason_code"],
            "action": action,
        }
        if trigger:
            payload["trigger"] = dict(trigger)
        return {
            "case_key": case["case_key"],
            "receipt_key": f"holdings:case:discovered:{case['case_key']}",
            "receipt_type": "holding_case_discovered",
            "payload": payload,
        }

    def _terminal_receipt(
        self,
        case: Dict[str, Any],
        *,
        state: str,
        resolution: Dict[str, Any],
        resolution_digest: str,
    ) -> Dict[str, Any]:
        return {
            "case_key": case["case_key"],
            "receipt_key": (
                f"holdings:case:closed:{case['case_key']}:{state}:"
                f"{resolution_digest}"
            ),
            "receipt_type": "holding_case_closed",
            "payload": {
                "case_key": case["case_key"],
                "record_id": case["record_id"],
                "account": case.get("account"),
                "field": case["field"],
                "terminal_state": state,
                **resolution,
            },
        }

    def _attention_receipt(
        self,
        case: Dict[str, Any],
        resolution: Dict[str, Any],
    ) -> Dict[str, Any]:
        attempt_id = str(
            resolution.get("apply_attempt_id")
            or case.get("apply_attempt_id")
            or "unknown"
        )
        return {
            "case_key": case["case_key"],
            "receipt_key": (
                f"holdings:case:attention:{case['case_key']}:{attempt_id}:"
                "apply_outcome_unknown"
            ),
            "receipt_type": "holding_case_attention_required",
            "payload": {
                "case_key": case["case_key"],
                "record_id": case["record_id"],
                "account": case.get("account"),
                "field": case["field"],
                "state": "apply_outcome_unknown",
                **resolution,
                "action": {
                    "description": "禁止自动重试；请先执行显式恢复判断",
                    "command": (
                        f"pm holdings recover --case-key {case['case_key']} --confirm"
                    ),
                },
            },
        }

    @staticmethod
    def _confirmation_scope(case: Dict[str, Any]) -> str:
        return _digest(
            {
                "case_key": case["case_key"],
                "case_precondition_digest": case["case_precondition_digest"],
                "authority_id": case.get("authority_id"),
                "policy_version": case["policy_version"],
            }
        )

    @staticmethod
    def _apply_resolution_digest(
        case: Dict[str, Any], resolution: Dict[str, Any]
    ) -> str:
        return _digest(
            {
                "manual_apply_policy": MANUAL_APPLY_POLICY_VERSION,
                "apply_attempt_id": resolution.get("apply_attempt_id")
                or case.get("apply_attempt_id"),
                "target": resolution.get("target") or case.get("target"),
                "operator_context": resolution.get("operator_context"),
                "readback_digest": resolution.get("readback_digest"),
            }
        )

    def _initial_account(self, record_id: str) -> str:
        rows = self.storage.get_raw_holdings(record_id=record_id)
        if len(rows) != 1:
            raise ValueError(f"holding record lookup is not exact: {record_id}")
        account = str(rows[0].raw_fields.get("account") or "").strip()
        if not account:
            raise ValueError("holding record has no account; apply is not allowed")
        return account

    def _locked(self, account: str, record_id: str) -> ExitStack:
        stack = ExitStack()
        try:
            stack.enter_context(self.lock_factory(account_lock_key(account)))
            stack.enter_context(self.lock_factory(holding_record_lock_key(record_id)))
        except Exception:
            stack.close()
            raise
        return stack

    @staticmethod
    def _single_validation(
        evaluation: HoldingsReconciliationEvaluation,
        record_id: str,
    ) -> RecordValidation:
        records = list(evaluation.report.records)
        if len(records) != 1 or records[0].raw.record_id != record_id:
            raise ValueError(f"holding reconciliation scope is not exact: {record_id}")
        return records[0]

    def _require_case(self, case_key: str) -> Dict[str, Any]:
        case = self.store.get_holding_case(str(case_key or "").strip())
        if not case:
            raise KeyError(f"holding case not found: {case_key}")
        return case

    @staticmethod
    def _require_same_scope(stored: Dict[str, Any], current: Dict[str, Any]) -> None:
        if (
            stored["case_key"] != current["case_key"]
            or stored["case_precondition_digest"]
            != current["case_precondition_digest"]
            or stored.get("authority_id") != current.get("authority_id")
            or stored["policy_version"] != current["policy_version"]
        ):
            raise ValueError("holding confirmation scope changed; reconcile again")

    @staticmethod
    def _same_value(left: Any, right: Any) -> bool:
        return _canonical_json(left) == _canonical_json(right)

    @staticmethod
    def _raw_identity(raw_fields: Dict[str, Any] | Any) -> Dict[str, Any]:
        return {
            "asset_id": str(raw_fields.get("asset_id") or "").strip() or None,
            "account": str(raw_fields.get("account") or "").strip() or None,
            "broker": str(raw_fields.get("broker") or "").strip() or None,
        }


__all__ = ["HoldingsWorkflowService", "operator_context"]
