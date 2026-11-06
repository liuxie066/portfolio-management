"""Fresh table observations and opt-in durable non-Futu holdings notifications."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import partial
import hashlib
import math
import sys
from typing import Any

from src import config
from src.domain.holding_changes import POLICY_VERSION, observe
from src.process_lock import holdings_observation_lock_key, process_lock

from ._json import canonical_json
from .holding_change_receipt import receipts
from .holdings_validation import duplicate_holding_groups
from .holdings_workflow_service import HoldingsWorkflowService
from .operation_state_store import OperationStateStore
from .operation_receipt_outbox_service import OperationReceiptOutboxService
from src.time_utils import bj_now_naive


@dataclass(frozen=True)
class MonitorSettings:
    accounts: tuple[str, ...]
    enabled: bool = False
    coalesce_seconds: float = 2
    reconcile_interval_seconds: float = 60
    stale_seconds: float = 120

    @classmethod
    def from_config(cls):
        raw = config.get("holdings.change_monitor", {}) or {}
        accounts = raw.get("accounts", [])
        if not isinstance(accounts, list) or any(not isinstance(item, str) or not item.strip() for item in accounts):
            raise ValueError("holdings.change_monitor.accounts must be an explicit list of accounts")
        enabled = raw.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("holdings.change_monitor.enabled must be boolean")
        values = {}
        for key, default in (("coalesce_seconds", 2), ("reconcile_interval_seconds", 60), ("stale_seconds", 120)):
            value = raw.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"holdings.change_monitor.{key} must be positive and finite")
            values[key] = float(value)
        return cls(tuple(sorted(set(item.strip() for item in accounts))), enabled, **values)

    def scope_settings(self):
        return {"accounts": list(self.accounts), "policy_version": POLICY_VERSION}


class HoldingChangeMonitorService:
    def __init__(self, *, storage: Any = None, store: Any = None, settings: MonitorSettings | None = None,
                 target: tuple[str, str] | None = None, db_path=None, dispatcher: Any = None):
        self.storage = storage
        self._store = store
        self.settings = settings or MonitorSettings.from_config()
        self.target = target or config.get_feishu_table_ref("holdings")
        if not self.settings.accounts and self.settings.enabled:
            raise ValueError("enabled holdings change monitor requires explicit accounts")
        self.scope_key = hashlib.sha256(canonical_json(list(self.target)).encode()).hexdigest()
        self.db_path = store.db_path if store is not None else db_path
        self.dispatcher = dispatcher

    @property
    def store(self):
        if self._store is None:
            self._store = OperationStateStore(self.db_path)
        return self._store

    def _state(self):
        return OperationStateStore.read_holding_monitor(self.scope_key, self.db_path)

    def _read(self):
        if self.storage is None:
            raise ValueError("monitor observation requires holdings storage")
        return list(self.storage.get_raw_holdings(require_complete=True))

    def status(self):
        state = self._state()
        base = {"scope_key": self.scope_key, "target": {"file_token": self.target[0], "table_id": self.target[1]},
                "enabled": self.settings.enabled, "accounts": list(self.settings.accounts),
                "policy_version": POLICY_VERSION, "source": "local", "read_only": True}
        if not state:
            return {**base, "success": True, "status": "uninitialized", "ready": False}
        mismatch = state["settings"] != self.settings.scope_settings()
        rows = state["rows"]
        last = state["background_success_at"]
        now = self._store.now_factory() if self._store else bj_now_naive()
        stale = not last or (now - datetime.fromisoformat(last)).total_seconds() > self.settings.stale_seconds
        gaps = sum(item["state"] == "gap" for item in rows.values())
        status = "scope_changed" if mismatch else "disabled" if not self.settings.enabled else "stale" if stale else "degraded" if gaps or state["background_error"] else "ready"
        return {**base, "success": True, "status": status, "ready": bool(state["ready"] and not mismatch),
                "epoch": state["epoch"], "requested": state["requested"], "completed": state["completed"],
                "observation_seq": state["observation_seq"], "next_due": state["next_due"],
                "receipts": {**state.get("receipts", {}), "queued": sum(state.get("receipts", {}).get(key, 0) for key in ("pending", "claimed", "sending"))},
                "active_count": sum(item["state"] == "active" for item in rows.values()), "gap_count": gaps,
                "missing_candidate_count": sum(item["state"] == "missing_candidate" for item in rows.values()),
                "problems": [{"record_id": rid, "reason": row["reason"]} for rid, row in rows.items() if row.get("reason")],
                **{key: state[key] for key in ("manual_attempt_at", "manual_success_at", "manual_error", "background_attempt_at", "background_success_at", "background_error")}}

    def initialize(self, *, apply: bool = False, confirm: bool = False, rebuild: bool = False):
        if apply and not confirm:
            raise ValueError("monitor init requires --apply --confirm")
        if not self.settings.accounts:
            raise ValueError("configure explicit holdings.change_monitor.accounts before initialization")
        # Previews also serialize source reads, but never initialize/migrate the DB.
        from src.process_lock import process_lock as lock
        path = OperationStateStore.resolve_db_path_read_only(self.db_path)
        with lock(holdings_observation_lock_key(*self.target), data_dir=path.parent):
            old = self._state()
            if old and not rebuild:
                raise ValueError("monitor baseline already exists; use explicit --rebuild")
            records = self._read()
            now = self.store.now_factory().isoformat() if apply else self._preview_now()
            plan = observe(records, {}, accounts=frozenset(self.settings.accounts), now=now,
                           duplicate_groups=duplicate_holding_groups(records), initialize=True)
            result = {"success": not plan["problems"], "status": "blocked" if plan["problems"] else "initialized" if apply else "preview",
                      "dry_run": not apply, "row_count": len(plan["rows"]), "problems": plan["problems"],
                      "rebuild": rebuild, "historical_receipts": "preserved; existing dispatch authorization unchanged"}
            if apply and not plan["problems"]:
                state = self.store.initialize_holding_monitor(scope_key=self.scope_key, file_token=self.target[0],
                    table_id=self.target[1], settings=self.settings.scope_settings(), rows=plan["rows"], rebuild=rebuild)
                result["epoch"] = state["epoch"]
            return result

    @staticmethod
    def _preview_now():
        from src.time_utils import bj_now_naive
        return bj_now_naive().isoformat()

    def scan(self, *, apply: bool = False, confirm: bool = False, mode: str = "manual", due_only: bool = False):
        if mode not in {"manual", "background"} or (apply and not confirm):
            raise ValueError("monitor scan mutation requires confirmation and a valid mode")
        if mode == "background" and not self.settings.enabled:
            return {"success": True, "status": "disabled"}
        path = OperationStateStore.resolve_db_path_read_only(self.db_path)
        keys = []
        with process_lock(holdings_observation_lock_key(*self.target), data_dir=path.parent):
            state = self._state()
            if not state or not state["ready"]:
                return {"success": False, "status": "uninitialized"}
            if state["settings"] != self.settings.scope_settings():
                return {"success": False, "status": "scope_changed"}
            now = self.store.now_factory() if apply else datetime.fromisoformat(self._preview_now())
            if due_only and state["next_due"] and state["next_due"] > now.isoformat():
                return {"success": True, "status": "idle"}
            try:
                records = self._read()
                plan = observe(records, state["rows"], accounts=frozenset(self.settings.accounts),
                               now=now.isoformat(), duplicate_groups=duplicate_holding_groups(records))
                result = {"success": True, "status": "observed" if apply else "preview", "dry_run": not apply,
                          "changes": plan["changes"], "problems": plan["problems"]}
                if apply:
                    duplicate_plan = HoldingsWorkflowService(storage=self.storage, store=self.store).plan_duplicate_observation(
                        records, trigger={"mode": f"holdings_changes_{mode}", "observed_at": now.isoformat()},
                        covered_record_ids=plan["covered_record_ids"], proven_deleted_ids=plan["proven_deleted_ids"])
                    settled = self.store.settle_holding_monitor(scope_key=self.scope_key, settings=self.settings.scope_settings(),
                        epoch=state["epoch"], mode=mode, generation=state["requested"], plan=plan, duplicate_plan=duplicate_plan,
                        receipt_builder=partial(receipts, self.scope_key), interval=self.settings.reconcile_interval_seconds)
                    result.update(settled)
                    keys = settled["receipt_keys"]
            except Exception as exc:
                if apply:
                    self.store.fail_holding_monitor(self.scope_key, mode=mode, error=str(exc))
                return {"success": False, "status": "failed", "error": str(exc), "dry_run": not apply}
        # Message delivery is outside both SQLite transaction and observation lock.
        if keys:
            dispatcher = self.dispatcher or OperationReceiptOutboxService(store=self.store)
            result["delivery"] = [dispatcher.dispatch_pending(receipt_key=key) for key in keys]
        return result

    def run_worker_loop(self, *, stop_event, poll_seconds: float = 1):
        if not self.settings.enabled:
            return
        valid = self.store.configure_holding_monitor(self.scope_key, settings=self.settings.scope_settings(), enabled=True)
        if not valid:
            print("holdings change monitor requires a matching initialized baseline", file=sys.stderr)
        else:
            self.store.request_holding_monitor(self.scope_key)
        try:
            while not stop_event.is_set():
                try:
                    if not valid:
                        valid = self.store.configure_holding_monitor(self.scope_key, settings=self.settings.scope_settings(), enabled=True)
                        if valid:
                            self.store.request_holding_monitor(self.scope_key)
                    if valid:
                        outcome = self.scan(apply=True, confirm=True, mode="background", due_only=True)
                        if outcome.get("status") in {"scope_changed", "uninitialized"}:
                            valid = False
                except Exception as exc:
                    print(f"holdings change monitor worker: {exc}", file=sys.stderr)
                stop_event.wait(poll_seconds)
        finally:
            self.store.configure_holding_monitor(self.scope_key, settings=self.settings.scope_settings(), enabled=False)
