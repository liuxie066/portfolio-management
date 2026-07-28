from datetime import datetime, timedelta
import sqlite3

import pytest

from src.app.nav_receipt_outbox_service import NavReceiptOutboxService
from src.app.operation_state_store import OperationStateStore
from src.app.cash_flow_effect_store import CashFlowEffectStore


def test_operation_store_upgrades_v1_outbox_for_claims(tmp_path):
    path = tmp_path / "operations.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE operation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO operation_meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE nav_receipt_outbox (
                receipt_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                message_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    OperationStateStore(path)

    with sqlite3.connect(path) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(nav_receipt_outbox)")
        }
        version = conn.execute(
            "SELECT value FROM operation_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert {"claim_id", "claimed_at"} <= columns
    assert version == "2"


def test_fx_confirmation_is_local_and_durable(tmp_path):
    path = tmp_path / "operations.sqlite3"
    store = OperationStateStore(path)

    store.record_fx_confirmation(
        confirmation_id="fx_1",
        record_id="rec_1",
        source_hash="hash_1",
        exchange_rate="7.2",
        exchange_rate_date="2026-07-01",
        exchange_rate_source="provider:example",
        exchange_rate_evidence_type="provider",
        cny_amount="72.0",
        confirmation={"operator": "tester"},
    )

    confirmation = OperationStateStore(path).latest_fx_confirmation("rec_1")
    assert confirmation["confirmation_id"] == "fx_1"
    assert confirmation["exchange_rate_source"] == "provider:example"
    assert confirmation["confirmation"] == {"operator": "tester"}


def test_nav_receipt_failure_survives_restart_and_retries_once_due(tmp_path):
    clock = [datetime(2026, 7, 28, 8, 11)]
    path = tmp_path / "operations.sqlite3"
    attempts = []

    class Sender:
        def send(self, payload):
            attempts.append(dict(payload))
            if len(attempts) == 1:
                return {"success": False, "status": "failed", "error": "offline"}
            return {"success": True, "status": "sent", "message_id": "msg_1"}

    store = OperationStateStore(path, now_factory=lambda: clock[0])
    service = NavReceiptOutboxService(store=store, sender=Sender())
    payload = {
        "success": False,
        "status": "failed",
        "run_id": "run_1",
        "dry_run": False,
        "items": [],
        "error": "cash flow failed",
    }
    result = service.enqueue_and_dispatch(payload)

    assert result["status"] == "queued"
    row = store.get_nav_receipt("nav:run_1")
    assert row["status"] == "failed"
    assert row["attempt_count"] == 1

    restarted = OperationStateStore(path, now_factory=lambda: clock[0])
    assert restarted.list_due_nav_receipts() == []
    clock[0] += timedelta(minutes=1)
    retried = NavReceiptOutboxService(
        store=restarted,
        sender=service.sender,
    ).dispatch_pending()

    assert retried["sent"] == 1
    assert restarted.get_nav_receipt("nav:run_1")["status"] == "sent"
    assert len(attempts) == 2

    deduplicated = NavReceiptOutboxService(
        store=restarted,
        sender=service.sender,
    ).enqueue_and_dispatch(payload)
    assert deduplicated["deduplicated"] is True
    assert len(attempts) == 2

    with pytest.raises(ValueError, match="key collision"):
        restarted.enqueue_nav_receipt(
            receipt_key="nav:run_1",
            payload={"run_id": "run_1", "success": True},
        )


def test_nav_receipt_claim_is_atomic_across_dispatchers(tmp_path):
    path = tmp_path / "operations.sqlite3"
    first = OperationStateStore(path)
    second = OperationStateStore(path)
    first.enqueue_nav_receipt(
        receipt_key="nav:run_atomic",
        payload={"run_id": "run_atomic", "dry_run": False},
    )

    claimed = first.claim_due_nav_receipts(claim_id="worker_1")
    competing = second.claim_due_nav_receipts(claim_id="worker_2")

    assert [row["receipt_key"] for row in claimed] == ["nav:run_atomic"]
    assert competing == []
    first.mark_nav_receipt(
        "nav:run_atomic",
        claim_id="worker_1",
        success=True,
        message_id="msg_atomic",
    )
    assert second.get_nav_receipt("nav:run_atomic")["status"] == "sent"


def test_legacy_fx_confirmation_import_is_explicit_and_idempotent(tmp_path):
    legacy = CashFlowEffectStore.initialize(
        cutover_date="2026-07-01",
        db_path=tmp_path / "effects.sqlite3",
    )
    legacy.record_fx_confirmation(
        record_id="rec_legacy",
        source_hash="hash_legacy",
        exchange_rate="7.1",
        exchange_rate_date="2026-07-01",
        exchange_rate_source="provider:legacy",
        exchange_rate_evidence_type="provider",
        cny_amount="71",
        confirmation={"operator": "legacy"},
    )
    store = OperationStateStore(tmp_path / "operations.sqlite3")

    first = store.import_legacy_fx_confirmations(legacy.db_path)
    second = store.import_legacy_fx_confirmations(legacy.db_path)

    assert first == {"scanned": 1, "imported": 1}
    assert second == {"scanned": 1, "imported": 0}
    assert (
        store.latest_fx_confirmation("rec_legacy")["exchange_rate_source"]
        == "provider:legacy"
    )


def test_default_operation_store_auto_imports_default_legacy_fx_confirmations(
    tmp_path,
    monkeypatch,
):
    from src import config

    legacy_path = tmp_path / "cash_flow_effects.sqlite3"
    legacy = CashFlowEffectStore.initialize(
        cutover_date="2026-07-01",
        db_path=legacy_path,
    )
    legacy.record_fx_confirmation(
        record_id="rec_upgrade",
        source_hash="hash_upgrade",
        exchange_rate="7.1",
        exchange_rate_date="2026-07-01",
        exchange_rate_source="provider:legacy",
        exchange_rate_evidence_type="provider",
        cny_amount="71",
        confirmation={"operator": "legacy"},
    )
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        CashFlowEffectStore,
        "resolve_db_path",
        staticmethod(lambda db_path=None: legacy_path),
    )

    store = OperationStateStore()
    store.import_default_legacy_fx_confirmations()

    assert store.latest_fx_confirmation("rec_upgrade")["source_hash"] == "hash_upgrade"
