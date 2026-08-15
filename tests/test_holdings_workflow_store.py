from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3

import pytest

from src.app.holding_case_contract import (
    build_case_precondition,
    confirmation_scope,
)
from src.app.operation_state_store import OperationStateStore


def _case(
    key: str,
    *,
    field: str = "currency",
    kind: str = "missing_completable",
    current=None,
    proposed="USD",
    evidence_id: str | None = "evidence-1",
):
    return {
        "case_key": key,
        "record_id": "rec-1",
        "account": "lx",
        "field": field,
        "kind": kind,
        "blocks_official_nav": True,
        "policy_version": "holdings-validation.v1+holdings-currency.v1",
        "authority_id": "asset_type:us_stock",
        "current": current,
        "proposed": proposed,
        "record_digest": f"record-{key}",
        "case_precondition_digest": f"precondition-{key}",
        "latest_evidence_instance_id": evidence_id,
        "evidence": {"source_snapshot_id": evidence_id} if evidence_id else {},
        "state": "pending_apply" if kind == "missing_completable" else "pending_confirmation",
    }


def _receipt(case):
    return {
        "case_key": case["case_key"],
        "receipt_key": f"holdings:case:discovered:{case['case_key']}",
        "receipt_type": "holding_case_discovered",
        "payload": {"case_key": case["case_key"], "state": case["state"]},
    }


def _contract_case_pair(
    *,
    field="created_at",
    current="2026/03/30",
    state="pending_manual_edit",
    before_asset_type="us_fund",
    after_asset_type="exchange_fund",
):
    identity = {"asset_id": "SPY", "account": "sy", "broker": "IBKR"}
    before_record = {**identity, "asset_type": before_asset_type}
    after_record = {**identity, "asset_type": after_asset_type}
    before = build_case_precondition(
        record_id="rec-1",
        identity=identity,
        field=field,
        current=current,
        canonical_record=before_record,
    )
    after = build_case_precondition(
        record_id="rec-1",
        identity=identity,
        field=field,
        current=current,
        canonical_record=after_record,
    )
    kind = "missing_completable" if state == "pending_apply" else "invalid"
    base = {
        **_case(
            "case-contract",
            field=field,
            kind=kind,
            current=current,
            proposed="USD" if state == "pending_apply" else None,
            evidence_id=None,
        ),
        "identity": identity,
        "authority_id": None,
        "policy_version": "holdings-validation.v1",
        "state": state,
    }
    legacy = {
        **base,
        "record_digest": "record-before",
        "case_precondition_digest": before["legacy_case_precondition_digest"],
        "legacy_case_precondition_digest": before[
            "legacy_case_precondition_digest"
        ],
    }
    candidate = {
        **base,
        "record_digest": "record-after",
        **after,
    }
    return legacy, candidate


def test_holdings_feature_schema_is_additive_and_rejects_newer_version(tmp_path):
    path = tmp_path / "operations.sqlite3"
    store = OperationStateStore(path)
    store.record_fx_confirmation(
        confirmation_id="fx-1",
        record_id="cash-1",
        source_hash="source",
        exchange_rate="7.2",
        exchange_rate_date="2026-07-31",
        exchange_rate_source="provider:test",
        exchange_rate_evidence_type="provider",
        cny_amount="72",
        confirmation={"operator": "tester"},
    )
    store.enqueue_nav_receipt(receipt_key="nav:run-1", payload={"run_id": "run-1"})

    restarted = OperationStateStore(path)
    assert restarted.latest_fx_confirmation("cash-1")["confirmation_id"] == "fx-1"
    assert restarted.get_nav_receipt("nav:run-1")["status"] == "pending"
    with sqlite3.connect(path) as conn:
        meta = dict(conn.execute("SELECT key, value FROM operation_meta"))
        conn.execute(
            "UPDATE operation_meta SET value = '2' WHERE key = 'holdings_workflow_schema_version'"
        )
        schema_before = list(
            conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            )
        )
    assert meta["schema_version"] == "2"
    assert meta["holdings_workflow_schema_version"] == "1"
    with pytest.raises(RuntimeError, match="newer holdings workflow"):
        OperationStateStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT value FROM operation_meta WHERE key = 'holdings_workflow_schema_version'"
        ).fetchone()[0] == "2"
        assert list(
            conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            )
        ) == schema_before


def test_event_inbox_deduplicates_claims_retries_and_persists_outcome(tmp_path):
    clock = [datetime(2026, 7, 31, 20, 0)]
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: clock[0],
    )
    kwargs = {
        "event_id": "event-1",
        "event_type": "drive.file.bitable_record_changed_v1",
        "file_token": "file",
        "table_id": "table",
        "revision": "7",
        "action_list": [{"action": "edited", "record_id": "rec-1"}],
        "payload_digest": "digest-1",
    }
    assert store.accept_holding_event(**kwargs) is True
    assert store.accept_holding_event(**kwargs) is False
    with pytest.raises(ValueError, match="collision"):
        store.accept_holding_event(**{**kwargs, "payload_digest": "different"})

    claimed = store.claim_holding_events(claim_id="worker-1")
    assert [row["event_id"] for row in claimed] == ["event-1"]
    assert store.claim_holding_events(claim_id="worker-2") == []
    store.mark_holding_event_failed(
        event_id="event-1",
        claim_id="worker-1",
        error="temporary",
    )
    assert store.claim_holding_events() == []
    clock[0] += timedelta(minutes=1)
    retried = store.claim_holding_events(claim_id="worker-3")
    store.mark_holding_event_processed(
        event_id="event-1",
        claim_id="worker-3",
        outcome={"case_keys": ["case-1"]},
    )
    row = OperationStateStore(
        store.db_path,
        now_factory=lambda: clock[0],
    ).get_holding_event("event-1")
    assert retried[0]["attempt_count"] == 1
    assert row["state"] == "processed"
    assert row["outcome"] == {"case_keys": ["case-1"]}


def test_case_event_and_receipt_materialization_is_atomic(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    first = _case("case-1")
    second = _case("case-2", field="asset_type", proposed="us_stock")

    with pytest.raises(ValueError, match="lacks discovery receipt"):
        store.materialize_holding_cases(
            cases=[first, second],
            discovery_receipts=[_receipt(first)],
        )

    assert store.get_holding_case("case-1") is None
    assert store.get_operation_receipt("holdings:case:discovered:case-1") is None


def test_materialize_and_prepare_rolls_back_case_receipt_and_events_together(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    candidate = _case("case-atomic")
    invalid_apply = {
        "case_key": candidate["case_key"],
        "case_precondition_digest": candidate["case_precondition_digest"],
        "allowed_states": ("pending_confirmation",),
        "target": "USD",
        "before": "",
        "decision": "accept-proposed",
        "reason": "test",
        "confirmation_scope": "scope",
    }

    with pytest.raises(ValueError, match="not applicable"):
        store.materialize_and_prepare_holding_apply(
            observed_cases=[candidate],
            discovery_receipts=[_receipt(candidate)],
            apply_cases=[invalid_apply],
            apply_attempt_id="attempt-1",
            operator_context={"trusted_identity": False},
        )

    assert store.get_holding_case(candidate["case_key"]) is None
    assert store.get_operation_receipt(_receipt(candidate)["receipt_key"]) is None


def test_stable_case_refresh_does_not_resend_and_semantic_change_supersedes(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    first = _case("case-1")
    created = store.materialize_holding_cases(
        cases=[first], discovery_receipts=[_receipt(first)]
    )
    refreshed_case = {
        **first,
        "latest_evidence_instance_id": "evidence-2",
        "evidence": {"source_snapshot_id": "evidence-2"},
        "record_digest": "new-whole-record-digest",
    }
    refreshed = store.materialize_holding_cases(
        cases=[refreshed_case], discovery_receipts=[_receipt(refreshed_case)]
    )
    changed = _case("case-2", current="CNY", proposed="USD")
    superseded = store.materialize_holding_cases(
        cases=[changed], discovery_receipts=[_receipt(changed)]
    )

    assert created["enqueued_receipt_keys"] == ["holdings:case:discovered:case-1"]
    assert refreshed["refreshed_case_keys"] == ["case-1"]
    assert refreshed["enqueued_receipt_keys"] == []
    assert store.get_operation_receipt("holdings:case:discovered:case-1")["status"] == "pending"
    assert superseded["superseded_case_keys"] == ["case-1"]
    assert store.get_holding_case("case-1")["state"] == "superseded"
    assert store.get_holding_case("case-2")["state"] == "pending_apply"


def test_legacy_timestamp_precondition_migrates_in_place_without_receipt(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    legacy, candidate = _contract_case_pair()
    store.materialize_holding_cases(
        cases=[legacy], discovery_receipts=[_receipt(legacy)]
    )

    migrated = store.materialize_holding_cases(
        cases=[candidate], discovery_receipts=[_receipt(candidate)]
    )
    repeated = store.materialize_holding_cases(
        cases=[candidate], discovery_receipts=[_receipt(candidate)]
    )

    durable = store.get_holding_case(candidate["case_key"])
    events = store.list_holding_case_events(candidate["case_key"])
    assert durable["state"] == "pending_manual_edit"
    assert durable["case_precondition_digest"] == candidate[
        "case_precondition_digest"
    ]
    assert migrated["enqueued_receipt_keys"] == []
    assert repeated["enqueued_receipt_keys"] == []
    assert [event["event_type"] for event in events].count(
        "precondition_contract_migrated"
    ) == 1


def test_legacy_resolved_keep_migrates_scope_without_state_or_receipt_change(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    legacy, candidate = _contract_case_pair(state="pending_confirmation")
    store.materialize_holding_cases(
        cases=[legacy], discovery_receipts=[_receipt(legacy)]
    )
    store.finalize_holding_cases(
        outcomes=[
            {
                "case_key": legacy["case_key"],
                "state": "resolved_keep",
                "event_type": "resolved_keep",
                "resolution": {
                    "decision": "keep-current",
                    "reason": "verified",
                    "confirmation_scope": confirmation_scope(legacy),
                },
            }
        ],
        receipts=[],
    )

    result = store.materialize_holding_cases(
        cases=[candidate], discovery_receipts=[_receipt(candidate)]
    )

    durable = store.get_holding_case(candidate["case_key"])
    assert durable["state"] == "resolved_keep"
    assert durable["resolution"]["reason"] == "verified"
    assert durable["resolution"]["confirmation_scope"] == confirmation_scope(
        candidate
    )
    assert result["enqueued_receipt_keys"] == []


def test_legacy_inflight_precondition_migration_rolls_back(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    legacy, candidate = _contract_case_pair(state="pending_confirmation")
    store.materialize_holding_cases(
        cases=[legacy], discovery_receipts=[_receipt(legacy)]
    )
    store.prepare_holding_apply(
        cases=[
            {
                "case_key": legacy["case_key"],
                "case_precondition_digest": legacy["case_precondition_digest"],
                "allowed_states": ("pending_confirmation",),
                "target": None,
                "before": legacy["current"],
                "decision": "accept-proposed",
                "reason": "test",
                "confirmation_scope": confirmation_scope(legacy),
            }
        ],
        apply_attempt_id="attempt-legacy",
        operator_context={"trusted_identity": False},
    )
    events_before = store.list_holding_case_events(legacy["case_key"])

    with pytest.raises(ValueError, match="different semantics"):
        store.materialize_holding_cases(
            cases=[candidate], discovery_receipts=[_receipt(candidate)]
        )

    durable = store.get_holding_case(legacy["case_key"])
    assert durable["state"] == "applying"
    assert durable["case_precondition_digest"] == legacy[
        "case_precondition_digest"
    ]
    assert store.list_holding_case_events(legacy["case_key"]) == events_before


def test_combined_materialize_and_prepare_migrates_before_applying_atomically(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    legacy, candidate = _contract_case_pair(
        field="currency",
        current=None,
        state="pending_apply",
        before_asset_type="us_stock",
        after_asset_type="us_stock",
    )
    store.materialize_holding_cases(
        cases=[legacy], discovery_receipts=[_receipt(legacy)]
    )

    result = store.materialize_and_prepare_holding_apply(
        observed_cases=[candidate],
        discovery_receipts=[_receipt(candidate)],
        apply_cases=[
            {
                "case_key": candidate["case_key"],
                "case_precondition_digest": candidate[
                    "case_precondition_digest"
                ],
                "allowed_states": ("pending_apply",),
                "target": "USD",
                "before": None,
                "decision": "accept-proposed",
                "reason": "test",
                "confirmation_scope": confirmation_scope(candidate),
            }
        ],
        apply_attempt_id="attempt-v2",
        operator_context={"trusted_identity": False},
    )

    durable = store.get_holding_case(candidate["case_key"])
    assert durable["state"] == "applying"
    assert durable["case_precondition_digest"] == candidate[
        "case_precondition_digest"
    ]
    assert result["enqueued_receipt_keys"] == []
    assert [
        event["event_type"]
        for event in store.list_holding_case_events(candidate["case_key"])
    ][-3:] == [
        "precondition_contract_migrated",
        "record_refreshed",
        "apply_prepared",
    ]


def test_receipt_suppression_preserves_32_case_lifecycle_events(tmp_path):
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    cases = []
    receipts = []
    for index in range(32):
        account = "lx" if index < 13 else "sy"
        candidate = {
            **_case(f"case-{index}"),
            "record_id": f"rec-{index}",
            "account": account,
            "record_digest": f"record-{index}",
        }
        cases.append(candidate)
        receipts.append(_receipt(candidate))

    created = store.materialize_holding_cases(
        cases=cases,
        discovery_receipts=receipts,
        trigger={"mode": "daily_nav_preflight"},
        enqueue_receipts=False,
    )
    closed_case_keys = []
    for index, candidate in enumerate(cases):
        closed = store.resolve_holding_cases_external(
            record_id=candidate["record_id"],
            active_case_keys=[],
            record_digest=f"repaired-{index}",
            current_identity={},
            trigger={"mode": "daily_nav_preflight"},
            enqueue_receipts=False,
        )
        closed_case_keys.extend(closed["closed_case_keys"])
        assert closed["enqueued_receipt_keys"] == []

    assert len(created["created_case_keys"]) == 32
    assert created["enqueued_receipt_keys"] == []
    assert len(closed_case_keys) == 32
    for case_key in closed_case_keys:
        assert store.get_holding_case(case_key)["state"] == "resolved_external"
        assert [
            event["event_type"]
            for event in store.list_holding_case_events(case_key)
        ] == ["discovered", "resolved_external"]
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operation_receipt_outbox"
        ).fetchone()[0] == 0


def test_typed_receipt_claim_and_sending_expiry_have_distinct_safety(tmp_path):
    clock = [datetime(2026, 7, 31, 20, 0)]
    store = OperationStateStore(
        tmp_path / "operations.sqlite3",
        now_factory=lambda: clock[0],
    )
    store.enqueue_operation_receipt(
        receipt_key="receipt-claimed",
        receipt_type="holding_case_discovered",
        payload={"case_key": "case-1"},
    )
    store.claim_due_operation_receipts(receipt_key="receipt-claimed", claim_id="one")
    clock[0] += timedelta(minutes=6)
    reclaimed = store.claim_due_operation_receipts(
        receipt_key="receipt-claimed", claim_id="two"
    )
    assert reclaimed[0]["claim_id"] == "two"

    store.enqueue_operation_receipt(
        receipt_key="receipt-sending",
        receipt_type="holding_case_discovered",
        payload={"case_key": "case-2"},
    )
    store.claim_due_operation_receipts(receipt_key="receipt-sending", claim_id="three")
    store.start_operation_receipt_send(
        receipt_key="receipt-sending", claim_id="three"
    )
    clock[0] += timedelta(minutes=6)
    assert store.claim_due_operation_receipts(receipt_key="receipt-sending") == []
    assert store.get_operation_receipt("receipt-sending")["status"] == "unknown"
    store.resolve_operation_receipt(
        receipt_key="receipt-sending",
        decision="retry",
        operator_context={"trusted_identity": False},
    )
    assert store.get_operation_receipt("receipt-sending")["status"] == "failed"
