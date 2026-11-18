from datetime import date
import sqlite3

import pytest

from src.app.cash_flow_effect_store import CashFlowEffectStore, HASH_CONTRACT_VERSION, sha256_json
from src.domain.cash_flow_contracts import RawCashFlowRecord
from src.maintenance.cash_flow_audit_timestamp import CashFlowAuditTimestampRecovery
from tests.test_cash_flow_effect_service import FakeStorage, _cash, _flow, _refresh_flow_contract, _service


class Storage(FakeStorage):
    def get_raw_cash_flows(self, *, account, require_complete=False):
        assert require_complete is True
        return [RawCashFlowRecord.from_cash_flow(flow) for flow in self.flows if flow.account == account]


def completed(tmp_path, monkeypatch, state="applied", count=1):
    flow_date = date(2026, 6, 1) if state == "record_only" else date(2026, 7, 26)
    flows = [_flow(record_id=f"cf_{i}", flow_date=flow_date) for i in range(count)]
    storage = Storage(flows=flows, holdings=[_cash(quantity=100)])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    service.scan()
    parents = []
    for flow in flows:
        effect = service.store.get_latest_for_record(flow.record_id)
        preview = service.preview(effect["effect_id"], external_action="apply_delta")
        outcome = service.confirm(effect["effect_id"], preview_hash=preview["preview_hash"],
                                  external_action="apply_delta", confirm=True)
        assert outcome["success"]
        parent = service.store.get_latest_for_record(flow.record_id)
        assert parent["state"] == state
        parents.append(parent)
    storage.replacements.clear()
    return service, storage, parents


def legacy_pending(service, storage):
    result = []
    for flow in storage.flows:
        flow.updated_at = 1794980000000
        source = service.source_from_cash_flow(flow)
        parent = service.store.get_latest_for_record(flow.record_id)
        result.append(service.store.create_version(
            source=source, source_hash=sha256_json({"contract": HASH_CONTRACT_VERSION, "source": source}),
            state="pending", mode=parent["mode"],
        ))
    return result


def workflow_snapshot(store):
    with store._connect() as conn:
        return {name: [tuple(row) for row in conn.execute(f"SELECT * FROM {name}")]
                for name in ("cash_flow_effects", "cash_flow_effect_events", "cash_flow_scan_runs",
                             "cash_holding_fingerprints", "cash_flow_effect_receipts")}


@pytest.mark.parametrize("state", ["applied", "record_only"])
def test_timestamp_versions_inherit_proven_completion_without_writes(tmp_path, monkeypatch, state):
    service, storage, parents = completed(tmp_path, monkeypatch, state)
    parent = parents[0]
    before = workflow_snapshot(service.store)
    monkeypatch.setattr(service.store, "confirm_fingerprint", lambda **kw: pytest.fail("baseline changed"))
    for timestamp in (1794980000000, 1794980001000):
        storage.flows[0].updated_at = timestamp
        assert service.scan(enqueue_receipts=True)["changed"] == 1
        child = service.store.get_latest_for_record("cf_0")
        assert child["state"] == state
        assert child["confirmation"]["completion_effect_id"] == parent["effect_id"]
        assert child["confirmation"]["confirmed_at"] == parent["confirmation"]["confirmed_at"]
        assert child["source_hash"] != parent["source_hash"]
        assert child["targets"] == parent["targets"]
        assert service.scan()["changed"] == 0
        service.store = CashFlowEffectStore(service.store.db_path)
    assert not storage.replacements
    after = workflow_snapshot(service.store)
    assert after["cash_flow_effect_receipts"] == before["cash_flow_effect_receipts"]
    assert service.store.get_effect(parent["effect_id"]) == parent
    assert service.nav_gate(account="lx", nav_date=date(2026, 11, 18))["blocker_count"] == 0


def test_readback_inherits_and_later_correction_and_deletion_apply_once(tmp_path, monkeypatch):
    service, storage, parents = completed(tmp_path, monkeypatch)
    flow = storage.flows[0]
    flow.updated_at = 1794980000000
    assert service._current_cash_flow_source(parents[0])[3] is None
    assert not storage.replacements
    flow.amount = 30
    _refresh_flow_contract(flow)
    service.scan()
    correction = service.store.get_latest_for_record(flow.record_id)
    assert correction["state"] == "pending"
    preview = service.preview(correction["effect_id"], external_action="apply_delta")
    assert preview["targets"][0]["quantity"] == 130
    service.confirm(correction["effect_id"], preview_hash=preview["preview_hash"],
                    external_action="apply_delta", confirm=True)
    storage.flows.clear()
    service.scan()
    deletion = service.store.get_latest_for_record(flow.record_id)
    preview = service.preview(deletion["effect_id"], external_action="apply_delta")
    assert preview["targets"][0]["quantity"] == 100
    service.confirm(deletion["effect_id"], preview_hash=preview["preview_hash"],
                    external_action="apply_delta", confirm=True)
    assert [item.quantity for item in storage.replacements] == [130, 100]


@pytest.mark.parametrize("field,value", [
    ("remark", "new note"), ("source", "bank"), ("amount", 30),
    ("account", "sy"), ("broker", "other"), ("currency", "USD"),
    ("flow_date", date(2026, 7, 27)), ("exchange_rate", 2),
    ("dedup_key", "tampered"), ("cny_amount", 99), ("flow_type", "OUT"),
])
def test_other_source_changes_never_inherit(tmp_path, monkeypatch, field, value):
    service, storage, parents = completed(tmp_path, monkeypatch)
    flow = storage.flows[0]
    flow.updated_at = 1794980000000
    setattr(flow, field, value)
    if field in {"amount", "account", "broker", "currency", "flow_date"}:
        _refresh_flow_contract(flow)
    service.scan()
    assert service.store.get_latest_for_record(flow.record_id)["state"] not in {"applied", "record_only"}
    assert service.store.get_effect(parents[0]["effect_id"]) == parents[0]
    assert not storage.replacements


@pytest.mark.parametrize("broken", ["confirmation", "preview", "compensation", "event", "mode", "unknown"])
def test_completion_requires_exact_evidence(tmp_path, monkeypatch, broken):
    service, storage, parents = completed(tmp_path, monkeypatch)
    parent = parents[0]
    if broken == "confirmation":
        service.store.update_effect(parent["effect_id"], fields={"confirmation_json": None}, event_type="test")
    elif broken == "preview":
        service.store.update_effect(parent["effect_id"], fields={"preview_hash": "wrong"}, event_type="test")
    elif broken == "compensation":
        service.store.update_effect(parent["effect_id"], fields={"compensation_task_id": "task"}, event_type="test")
    elif broken == "event":
        with service.store._connect() as conn:
            conn.execute("DELETE FROM cash_flow_effect_events WHERE effect_id=? AND event_type='applied'", (parent["effect_id"],))
    storage.flows[0].updated_at = 1794980000000
    source = service.source_from_cash_flow(storage.flows[0])
    if broken == "unknown":
        source["new_field"] = "unrecognized"
    current_parent = service.store.get_effect(parent["effect_id"])
    assert service.audit_timestamp_proof(current_parent, source, "record_only" if broken == "mode" else "apply") is None


@pytest.mark.parametrize("state", ["record_only", "applied"])
def test_explicit_recovery_is_readonly_until_confirmed_and_replayable(tmp_path, monkeypatch, state):
    service, storage, parents = completed(tmp_path, monkeypatch, state)
    legacy_pending(service, storage)
    recovery = CashFlowAuditTimestampRecovery(storage=storage, db_path=service.store.db_path)
    before = workflow_snapshot(service.store)
    plan = recovery.plan(account="lx")
    assert len(plan["candidates"]) == 1
    assert workflow_snapshot(service.store) == before
    with pytest.raises(ValueError, match="confirm"):
        recovery.apply(account="lx", plan=plan, confirm=False, backup=tmp_path / "before.sqlite3")
    assert workflow_snapshot(service.store) == before
    result = recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "before.sqlite3")
    assert result["recovered"] == 1
    after = workflow_snapshot(service.store)
    assert after["cash_holding_fingerprints"] == before["cash_holding_fingerprints"]
    assert after["cash_flow_effect_receipts"] == before["cash_flow_effect_receipts"]
    assert not storage.replacements
    assert recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "before.sqlite3")["already_completed"]
    assert workflow_snapshot(service.store) == after
    assert service.store.get_latest_for_record("cf_0")["state"] == state
    assert service.store.get_effect(parents[0]["effect_id"]) == parents[0]


def test_stale_plan_and_partial_transaction_do_not_recover_anything(tmp_path, monkeypatch):
    service, storage, _ = completed(tmp_path, monkeypatch, count=2)
    effects = legacy_pending(service, storage)
    recovery = CashFlowAuditTimestampRecovery(storage=storage, db_path=service.store.db_path)
    plan = recovery.plan(account="lx")
    assert len(plan["candidates"]) == 2
    storage.flows[0].updated_at += 1
    with pytest.raises(ValueError, match="source changed"):
        recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "before.sqlite3")
    assert not (tmp_path / "before.sqlite3").exists()
    storage.flows[0].updated_at -= 1
    before = workflow_snapshot(service.store)
    original = CashFlowEffectStore.inherit_audit_completion
    calls = []
    def fail_second(self, **kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise RuntimeError("interrupted")
        return original(self, **kwargs)
    monkeypatch.setattr(CashFlowEffectStore, "inherit_audit_completion", fail_second)
    with pytest.raises(RuntimeError, match="interrupted"):
        recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "before.sqlite3")
    assert workflow_snapshot(service.store) == before
    assert all(service.store.get_effect(effect["effect_id"])["state"] == "pending" for effect in effects)
    assert not storage.replacements


def test_readonly_store_cannot_write(tmp_path, monkeypatch):
    service, _, _ = completed(tmp_path, monkeypatch)
    store = CashFlowEffectStore(service.store.db_path, read_only=True)
    with pytest.raises(RuntimeError, match="read-only"):
        with store.transaction():
            pass
    with pytest.raises(sqlite3.OperationalError):
        store.append_event("missing", "forbidden")


def test_scan_does_not_recover_existing_pending_version(tmp_path, monkeypatch):
    service, storage, _ = completed(tmp_path, monkeypatch)
    pending = legacy_pending(service, storage)[0]
    assert service.scan()["changed"] == 0
    assert service.store.get_effect(pending["effect_id"]) == pending
    assert service.nav_gate(account="lx", nav_date=date(2026, 11, 18))["blocker_count"] == 1
    assert not storage.replacements


def test_stale_local_plan_is_rejected_without_backup(tmp_path, monkeypatch):
    service, storage, _ = completed(tmp_path, monkeypatch)
    pending = legacy_pending(service, storage)[0]
    recovery = CashFlowAuditTimestampRecovery(storage=storage, db_path=service.store.db_path)
    plan = recovery.plan(account="lx")
    service.preview(pending["effect_id"], external_action="apply_delta")
    before = workflow_snapshot(service.store)
    with pytest.raises(ValueError, match="stale"):
        recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "before.sqlite3")
    assert workflow_snapshot(service.store) == before
    assert not (tmp_path / "before.sqlite3").exists()


@pytest.mark.parametrize("overrides", [
    {"no_service": False}, {"apply": True},
    {"apply": True, "confirm": True}, {"apply": True, "backup": "backup.sqlite3"},
    {"confirm": True}, {"backup": "backup.sqlite3"},
])
def test_cli_rejects_unsafe_arguments_before_storage(monkeypatch, overrides):
    from types import SimpleNamespace
    from scripts.pm import cmd_cash_flow_effects_audit_timestamp
    from src.feishu_storage import FeishuStorage

    monkeypatch.setattr(FeishuStorage, "__init__", lambda *_a, **_kw: pytest.fail("storage constructed"))
    args = dict(no_service=True, apply=False, confirm=False, backup=None)
    args.update(overrides)
    with pytest.raises(SystemExit):
        cmd_cash_flow_effects_audit_timestamp(SimpleNamespace(**args))


def test_cli_plan_and_apply_use_saved_private_plan(tmp_path, monkeypatch, capsys):
    import json
    from types import SimpleNamespace
    from scripts.pm import cmd_cash_flow_effects_audit_timestamp
    from src import config, feishu_storage

    service, storage, _ = completed(tmp_path, monkeypatch)
    legacy_pending(service, storage)
    original_get = config.get
    monkeypatch.setattr(config, "get", lambda key, default=None: str(service.store.db_path)
                        if key == "cash_flow.effects.db_path" else original_get(key, default))
    monkeypatch.setattr(feishu_storage, "FeishuStorage", lambda: storage)
    path = tmp_path / "plan.json"
    args = SimpleNamespace(no_service=True, apply=False, confirm=False, backup=None,
                           account="lx", plan_file=str(path), json=True)
    before = workflow_snapshot(service.store)
    result = cmd_cash_flow_effects_audit_timestamp(args)
    assert result["eligible"] == 1
    assert path.stat().st_mode & 0o777 == 0o600
    plan = json.loads(path.read_text())
    assert plan["plan_digest"] == result["plan_digest"]
    assert workflow_snapshot(service.store) == before
    assert "cf_0" not in capsys.readouterr().out
    with pytest.raises(FileExistsError):
        cmd_cash_flow_effects_audit_timestamp(args)
    assert json.loads(path.read_text()) == plan
    args.apply, args.confirm, args.backup = True, True, str(tmp_path / "backup.sqlite3")
    assert cmd_cash_flow_effects_audit_timestamp(args)["recovered"] == 1
    assert cmd_cash_flow_effects_audit_timestamp(args)["already_completed"]
    assert not storage.replacements


@pytest.mark.parametrize("fallback", [False, True])
def test_complete_raw_read_preserves_flag_and_rejects_partial_failure(fallback):
    from unittest.mock import Mock
    from src.feishu_storage import FeishuStorage

    client = Mock()
    storage = FeishuStorage(client=client)
    client.list_records.side_effect = ([Exception("FieldNameNotFound")] if fallback else []) + [[]]
    assert storage.get_raw_cash_flows(account="lx", require_complete=True) == []
    assert all(call.kwargs["require_complete"] is True for call in client.list_records.call_args_list)
    if fallback:
        assert "updated_at" not in client.list_records.call_args.kwargs["field_names"]
    client.list_records.side_effect = ([Exception("FieldNameNotFound")] if fallback else []) + [RuntimeError("incomplete page")]
    with pytest.raises(RuntimeError, match="incomplete page"):
        storage.get_raw_cash_flows(account="lx", require_complete=True)


@pytest.mark.parametrize("fields", [
    {"targets_json": [{"quantity": 999}]}, {"before_json": []},
    {"preview_hash": "altered"}, {"target_source": "altered"},
    {"compensation_task_id": "unexpected"},
])
def test_recovery_replay_rejects_changed_inheritance_evidence(tmp_path, monkeypatch, fields):
    service, storage, _ = completed(tmp_path, monkeypatch)
    pending = legacy_pending(service, storage)[0]
    recovery = CashFlowAuditTimestampRecovery(storage=storage, db_path=service.store.db_path)
    plan = recovery.plan(account="lx")
    recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "before.sqlite3")
    service.store.update_effect(pending["effect_id"], fields=fields, event_type="test_changed")
    before = workflow_snapshot(service.store)
    with pytest.raises(ValueError, match="stale"):
        recovery.apply(account="lx", plan=plan, confirm=True, backup=tmp_path / "unused.sqlite3")
    assert workflow_snapshot(service.store) == before
    assert not (tmp_path / "unused.sqlite3").exists()


def test_backup_is_private_before_any_sqlite_write_and_after_interruption(tmp_path, monkeypatch):
    import os
    service, _, _ = completed(tmp_path, monkeypatch)
    target = tmp_path / "private.sqlite3"
    original_connect = sqlite3.connect
    observed = []

    def connect(path, *args, **kwargs):
        if str(path) == str(target):
            observed.append(target.stat().st_mode & 0o777)
            raise RuntimeError("interrupted before writing")
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    previous_umask = os.umask(0o022)
    try:
        with pytest.raises(RuntimeError, match="interrupted"):
            service.store.backup(target)
    finally:
        os.umask(previous_umask)
    assert observed == [0o600]
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="already exists"):
        service.store.backup(target)
    dangling = tmp_path / "dangling.sqlite3"
    missing = tmp_path / "missing.sqlite3"
    dangling.symlink_to(missing)
    with pytest.raises(RuntimeError, match="already exists"):
        service.store.backup(dangling)
    assert not missing.exists()
