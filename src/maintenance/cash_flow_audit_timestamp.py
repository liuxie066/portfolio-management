"""Explicit, exact-plan recovery of timestamp-only pending cash-flow versions.

Planning never scans or mutates workflow state. Application only carries
completion evidence forward in SQLite; it does not execute financial effects.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from src import config
from src.app.cash_flow_effect_service import CashFlowEffectService
from src.app.cash_flow_effect_store import HASH_CONTRACT_VERSION, CashFlowEffectStore, sha256_json
from src.domain.cash_flow_contracts import CompletedCashFlowFacts
from src.process_lock import account_lock_key, process_lock


class CashFlowAuditTimestampRecovery:
    def __init__(self, *, storage: Any, db_path: str | Path | None = None):
        self.storage = storage
        self.db_path = CashFlowEffectStore.resolve_db_path(db_path)

    def _sources(self, account: str) -> dict[str, dict]:
        sources = {}
        for raw in self.storage.get_raw_cash_flows(account=account, require_complete=True):
            facts = CompletedCashFlowFacts.require(raw)
            source = CashFlowEffectService.source_from_cash_flow(facts.to_cash_flow())
            if source["account"] != account or source["record_id"] in sources:
                raise ValueError("recovery source account/record identity is ambiguous")
            sources[source["record_id"]] = source
        return sources

    def _plan(self, account: str, sources: dict, store: CashFlowEffectStore) -> dict:
        cutover = store.assert_cutover(config.get("cash_flow.effects.cutover_date"))
        identity = store.integrity_check()
        service = CashFlowEffectService(storage=self.storage, store=store)
        candidates, skipped = [], []
        for effect in store.list_effects(account=account, states=["pending"]):
            parent = store.get_previous_version(effect)
            source = sources.get(effect["record_id"])
            reason = None
            if any(effect.get(key) is not None for key in (
                "confirmation", "preview_hash", "before", "targets", "target_source",
                "compensation_task_id", "last_error",
                "warnings",
            )):
                reason = "existing_workflow_evidence"
            elif not source or source != effect["source"]:
                reason = "source_changed_or_missing"
            elif (effect["hash_contract_version"] != HASH_CONTRACT_VERSION
                  or effect["source_hash"] != sha256_json({"contract": HASH_CONTRACT_VERSION, "source": source})
                  or any(effect.get(key) != source.get(key) for key in (
                      "record_id", "account", "broker", "currency", "flow_date", "signed_amount",
                  ))):
                reason = "stored_source_identity_invalid"
            elif not parent or parent["version"] + 1 != effect["version"]:
                reason = "immediate_predecessor_missing"
            mode = "record_only" if source and date.fromisoformat(source["flow_date"]) < cutover else "apply"
            if not reason and mode != effect["mode"]:
                reason = "processing_mode_changed"
            proof = service.audit_timestamp_proof(parent, source, mode) if not reason else None
            if not proof:
                skipped.append({"effect_id": effect["effect_id"],
                                "reason": reason or "completion_not_proven"})
                continue
            candidates.append({
                "effect_id": effect["effect_id"], "version": effect["version"],
                "source_hash": effect["source_hash"],
                "effect_digest": sha256_json(effect), "parent_digest": sha256_json(parent),
                "proof": proof,
            })
        payload = {
            "schema": "pm.cash-flow.audit-timestamp-plan.v1",
            "account": account, "database_id": identity["database_id"],
            "cutover_date": cutover.isoformat(), "source_digest": sha256_json(sources),
            "candidates": candidates, "skipped": skipped,
        }
        return {**payload, "plan_digest": sha256_json(payload)}

    def plan(self, *, account: str) -> dict:
        if not isinstance(account, str) or not account.strip() or account != account.strip():
            raise ValueError("an explicit nonblank account is required")
        store = CashFlowEffectStore(self.db_path, read_only=True)
        return self._plan(account, self._sources(account), store)

    @staticmethod
    def _completed(plan: dict, sources: dict, store: CashFlowEffectStore) -> bool:
        """Only an entire, still-current successful plan may replay as a no-op."""
        if not plan["candidates"] or sha256_json(sources) != plan["source_digest"]:
            return False
        service = CashFlowEffectService(storage=None, store=store)
        for entry in plan["candidates"]:
            effect = store.get_effect(entry["effect_id"])
            if not effect or store.get_latest_for_record(effect["record_id"]) != effect:
                return False
            parent = store.get_previous_version(effect)
            if not parent or sha256_json(parent) != entry["parent_digest"]:
                return False
            proof = service.audit_timestamp_proof(parent, effect["source"], effect["mode"])
            if proof != entry["proof"]:
                return False
            expected_confirmation = {
                **parent["confirmation"], "kind": "audit_timestamp_inherited",
                "parent_effect_id": parent["effect_id"],
                "completion_effect_id": entry["proof"]["completion_effect_id"],
            }
            if (effect["state"] != proof["state"]
                    or effect["version"] != entry["version"]
                    or effect["source_hash"] != entry["source_hash"]
                    or effect.get("confirmation") != expected_confirmation
                    or effect["source"] != sources.get(effect["record_id"])
                    or effect.get("last_error") is not None
                    or service.audit_timestamp_proof(
                        effect, parent["source"], effect["mode"],
                    ) is None):
                return False
            if not any(event["event_type"] == "audit_timestamp_inherited"
                       and event["payload"].get("plan_digest") == plan["plan_digest"]
                       and event["payload"].get("source_hash") == effect["source_hash"]
                       and all(event["payload"].get(key) == value
                               for key, value in proof.items())
                       for event in store.list_events(effect["effect_id"])):
                return False
        return True

    def apply(self, *, account: str, plan: dict, confirm: bool, backup: str | Path) -> dict:
        if not confirm:
            raise ValueError("audit timestamp recovery requires confirm=True")
        payload = {key: value for key, value in plan.items() if key != "plan_digest"}
        if (plan.get("schema") != "pm.cash-flow.audit-timestamp-plan.v1"
                or plan.get("account") != account or not account or account != account.strip()
                or plan.get("plan_digest") != sha256_json(payload)):
            raise ValueError("invalid audit timestamp recovery plan")
        with process_lock("cash-flow-effects:scanner"), process_lock(account_lock_key(account)):
            store = CashFlowEffectStore(self.db_path)
            sources = self._sources(account)
            current = self._plan(account, sources, store)
            if any(current[key] != plan.get(key) for key in (
                "database_id", "cutover_date", "source_digest",
            )):
                raise ValueError("recovery database, cutover or source changed; regenerate plan")
            if self._completed(plan, sources, store):
                return {"success": True, "already_completed": True,
                        "plan_digest": plan["plan_digest"], "recovered": 0}
            if current != plan:
                raise ValueError("recovery plan is stale; no states changed")
            if not current["candidates"]:
                raise ValueError("recovery plan has no eligible candidates")
            saved_backup = store.backup(backup)
            with store.transaction():
                if self._plan(account, sources, store) != plan:
                    raise ValueError("recovery state changed after backup; no states changed")
                for entry in plan["candidates"]:
                    effect = store.get_effect(entry["effect_id"])
                    parent = store.get_previous_version(effect)
                    store.inherit_audit_completion(
                        parent=parent, source=effect["source"], proof=entry["proof"],
                        current=effect, plan_digest=plan["plan_digest"],
                    )
            return {"success": True, "already_completed": False,
                    "plan_digest": plan["plan_digest"], "recovered": len(plan["candidates"]),
                    "backup": saved_backup, "financial_writes": 0}
