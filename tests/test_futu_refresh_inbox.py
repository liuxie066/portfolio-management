from __future__ import annotations

import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Event, Thread
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src import config
from src.app.futu_refresh_worker import WORKER_LOCK_KEY, FutuRefreshWorker
from src.app.operation_state import OperationStateStore
from src.process_lock import process_lock
from src.service.application import PortfolioService
from src.service.http import create_app

PATH = "/api/v1/futu/holdings/durable-refresh-requests"
IDENTITY = {"account": "alice", "request_id": "stock-refresh:1"}


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    profile = {"host": "127.0.0.1", "port": 11111, "acc_id": 101,
               "trd_env": "REAL", "trd_market": "US"}
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"futu": {"profiles": {
        "alice": profile, "bob": {**profile, "acc_id": 202},
        "broken": {"acc_id": 303},
    }}}))
    monkeypatch.setenv("PORTFOLIO_CONFIG_FILE", str(path))
    monkeypatch.setenv("PM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "_cached_config", None)
    monkeypatch.setattr("src.app.futu_refresh_worker.POLL_SECONDS", 0.01)
    # Any accidental provider/storage construction must fail before external I/O.
    monkeypatch.setattr("src.feishu_storage.FeishuStorage", Mock(side_effect=AssertionError("real storage")))
    monkeypatch.setattr("src.app.FutuBalanceSyncService", Mock(side_effect=AssertionError("real provider")))


@pytest.fixture
def store(tmp_path):
    return OperationStateStore(tmp_path / "operations.sqlite3")


def service_for(store, refresh=None):
    service = PortfolioService(operation_state_store=store)
    service.refresh_futu_holdings = refresh or Mock(return_value={"success": True})
    return service


def client_for(service):
    return TestClient(create_app(service), client=("127.0.0.1", 50000))


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "worker did not make progress"
        Event().wait(0.01)


@contextmanager
def running_worker(store, refresh):
    worker = FutuRefreshWorker(store, refresh)
    thread = Thread(target=worker.run)
    thread.start()
    try:
        yield worker, thread
    finally:
        worker.stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive()


def state(store, **identity):
    return store.get_futu_refresh(**(identity or IDENTITY))["state"]


def test_concurrent_http_admission_commits_once_and_response_loss_is_absorbed(store):
    service = service_for(store)

    def post(_):
        return client_for(service).post(PATH, json=IDENTITY)

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(post, range(16)))
    assert {response.status_code for response in responses} == {202}
    assert all(response.json() == {"success": True, "status": "accepted", **IDENTITY}
               for response in responses)
    assert all(response.headers["x-pm-api-version"] == "portfolio.api.v1" for response in responses)
    assert state(OperationStateStore(store.db_path)) == "pending"
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM futu_refresh_inbox").fetchone()[0] == 1
    service.refresh_futu_holdings.assert_not_called()

    with running_worker(store, service.refresh_futu_holdings):
        wait_for(lambda: state(store) == "completed")
        # Pretend the original 202 was lost, retry even after completion.
        assert client_for(service).post(PATH, json=IDENTITY).status_code == 202
        assert state(store) == "completed"
    service.refresh_futu_holdings.assert_called_once_with(**IDENTITY)


@pytest.mark.parametrize("account", ["unknown", "broken", "ALICE", "", " alice "])
def test_wrong_account_never_admitted_or_synced(store, account):
    service = service_for(store)
    response = client_for(service).post(PATH, json={**IDENTITY, "account": account})
    assert response.status_code in (400, 422)
    assert response.json()["success"] is False
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM futu_refresh_inbox").fetchone()[0] == 0
    service.refresh_futu_holdings.assert_not_called()


def test_explicit_account_required_and_extra_fields_rejected(store):
    client = client_for(service_for(store))
    for payload in ({"request_id": "id"}, {**IDENTITY, "delta": 100},
                    {**IDENTITY, "request_id": "bad/id"}):
        assert client.post(PATH, json=payload).status_code == 422


def test_same_request_id_is_isolated_by_account(store):
    service = service_for(store)
    for account in ("alice", "bob"):
        assert client_for(service).post(PATH, json={**IDENTITY, "account": account}).status_code == 202
    with running_worker(store, service.refresh_futu_holdings):
        wait_for(lambda: all(state(store, **{**IDENTITY, "account": account}) == "completed"
                             for account in ("alice", "bob")))
    assert {call.kwargs["account"] for call in service.refresh_futu_holdings.call_args_list} == {"alice", "bob"}
    assert service.refresh_futu_holdings.call_count == 2


@pytest.mark.parametrize("claimed", [False, True])
def test_lifespan_restart_recovers_without_post(store, claimed):
    store.accept_futu_refresh(**IDENTITY)
    if claimed:
        assert store.claim_futu_refresh() is not None
    restarted = OperationStateStore(store.db_path)
    service = service_for(restarted)
    with client_for(service) as client:
        assert client.get("/health").status_code == 200
        wait_for(lambda: state(restarted) == "completed")
    service.refresh_futu_holdings.assert_called_once_with(**IDENTITY)


@pytest.mark.parametrize("failure", [{"success": False}, RuntimeError("source unavailable"), {"success": 1}])
def test_false_or_exception_result_stays_pending_with_bounded_backoff(store, failure):
    clock = [datetime(2026, 10, 27, 9)]
    store.now_factory = lambda: clock[0]
    store.accept_futu_refresh(**IDENTITY)
    refresh = Mock(side_effect=[failure, {"success": True}])
    with running_worker(store, refresh):
        wait_for(lambda: store.get_futu_refresh(**IDENTITY)["attempt_count"] == 1
                 and state(store) == "pending")
        row = store.get_futu_refresh(**IDENTITY)
        assert row["last_error"]
        assert datetime.fromisoformat(row["next_attempt_at"]) == clock[0] + timedelta(minutes=1)
        clock[0] += timedelta(minutes=1)
        wait_for(lambda: state(store) == "completed")
    assert refresh.call_count == 2
    # Saturate backoff; it never exceeds the existing one-hour retry ceiling.
    with store._connect() as conn:
        conn.execute("UPDATE futu_refresh_inbox SET state='running', attempt_count=100")
    store.settle_futu_refresh(**IDENTITY, result={"success": False})
    assert datetime.fromisoformat(store.get_futu_refresh(**IDENTITY)["next_attempt_at"]) == clock[0] + timedelta(hours=1)


@pytest.mark.parametrize("stop_during_outage", [False, True])
def test_same_worker_survives_post_sync_settlement_and_reset_db_outage(store, monkeypatch, stop_during_outage):
    store.accept_futu_refresh(**IDENTITY)
    settlement_failed = Event()
    recovered = Event()
    settle = store.settle_futu_refresh
    reset = store.recover_futu_refreshes

    def fail_settlement(**kwargs):
        if not recovered.is_set():
            settlement_failed.set()
            raise sqlite3.OperationalError("database unavailable after sync")
        settle(**kwargs)

    def fail_reset():
        if settlement_failed.is_set() and not recovered.is_set():
            raise sqlite3.OperationalError("reset unavailable too")
        reset()

    monkeypatch.setattr(store, "settle_futu_refresh", fail_settlement)
    monkeypatch.setattr(store, "recover_futu_refreshes", fail_reset)
    refresh = Mock(return_value={"success": True})
    with running_worker(store, refresh) as (worker, thread):
        assert settlement_failed.wait(5)
        assert state(store) == "running"
        assert refresh.call_count == 1
        other = {**IDENTITY, "account": "bob"}
        store.accept_futu_refresh(**other)
        if stop_during_outage:
            worker.stop.set()
            thread.join(timeout=1)
            assert not thread.is_alive()
            assert state(store) == "running"
        else:
            recovered.set()
            wait_for(lambda: state(store) == "completed" and state(store, **other) == "completed")
            assert thread.is_alive()
            assert refresh.call_count == 2


def test_lifespan_stop_holds_ownership_until_sync_finishes_and_no_overlap(store):
    store.accept_futu_refresh(**IDENTITY)
    entered, release, closed = Event(), Event(), Event()
    refresh = Mock()

    def sync(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"success": True}

    refresh.side_effect = sync
    service = service_for(store, refresh)

    def app_lifecycle():
        with client_for(service):
            assert entered.wait(5)
        closed.set()

    lifecycle = Thread(target=app_lifecycle)
    lifecycle.start()
    try:
        assert entered.wait(5)
        competing = Mock(return_value={"success": True})
        with running_worker(OperationStateStore(store.db_path), competing):
            # HTTP shutdown has started, but the sync and lock must still be alive.
            assert not closed.wait(0.1)
            with pytest.raises(BlockingIOError), process_lock(
                WORKER_LOCK_KEY, data_dir=store.db_path.parent, blocking=False,
            ):
                pytest.fail("ownership escaped during sync")
            assert state(store) == "running"
            competing.assert_not_called()
            release.set()
            assert closed.wait(5)
            assert state(store) == "completed"
            competing.assert_not_called()
    finally:
        release.set()
        lifecycle.join(5)
    assert not lifecycle.is_alive()
    refresh.assert_called_once_with(**IDENTITY)
    with process_lock(WORKER_LOCK_KEY, data_dir=store.db_path.parent, blocking=False):
        pass


def test_contending_worker_can_stop_without_acquiring_lock(store):
    with process_lock(WORKER_LOCK_KEY, data_dir=store.db_path.parent), running_worker(
        store, Mock(),
    ) as (worker, thread):
        worker.stop.set()
        thread.join(timeout=1)
        assert not thread.is_alive()


@pytest.mark.parametrize("stop_during_outage", [False, True])
def test_lock_permission_outage_retries_same_worker_or_stops(store, caplog, stop_during_outage):
    lock_dir = store.db_path.parent / "locks"
    lock_dir.mkdir()
    lock_dir.chmod(0o000)
    service = service_for(store)
    try:
        with running_worker(store, service.refresh_futu_holdings) as (worker, thread):
            wait_for(lambda: any(record.getMessage() == "pm_futu_refresh_ownership_failed"
                                 for record in caplog.records))
            response = client_for(service).post(PATH, json=IDENTITY)
            assert response.status_code == 202
            assert state(store) == "pending"
            service.refresh_futu_holdings.assert_not_called()
            assert thread.is_alive()
            assert any(isinstance(record.exc_info[1], PermissionError)
                       for record in caplog.records if record.exc_info)
            if stop_during_outage:
                worker.stop.set()
                thread.join(timeout=1)
                assert not thread.is_alive()
                assert state(store) == "pending"
            else:
                lock_dir.chmod(0o700)
                wait_for(lambda: state(store) == "completed")
                assert thread.is_alive()
                service.refresh_futu_holdings.assert_called_once_with(**IDENTITY)
    finally:
        lock_dir.chmod(0o700)


def test_db_locked_admission_is_503_not_accepted(store):
    service = service_for(store)
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        response = client_for(service).post(PATH, json=IDENTITY)
    assert response.status_code == 503
    assert response.headers["x-pm-api-version"] == "portfolio.api.v1"
    assert response.json()["error_code"] == "PM_SERVICE_UNAVAILABLE"
    assert store.get_futu_refresh(**IDENTITY) is None
    assert client_for(service).post(PATH, json=IDENTITY).status_code == 202


def quantity_result():
    return {
        "success": True, "status": "written", "dry_run": False,
        "source": "futu-openapi", "account": "alice",
        "receipt_persisted": True, "sync_run_id": "new-sync",
        "source_snapshot_id": "new-snapshot",
        "source_metadata": {"observed_at_utc": "2026-10-30T01:00:00Z"},
        "reconciliation": {"status": "trusted"},
        "positions": [{"asset_id": "EXAMPLE", "security_type": "STOCK",
                       "current_quantity": 50, "target_quantity": 51,
                       "quantity_changed": True}],
    }


def receipts(store):
    with store._connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM operation_receipt_outbox")]


def test_new_worker_change_is_atomically_queued_once_and_completed_duplicates_never_replay(store):
    result = quantity_result()
    refresh = Mock(return_value=result)
    store.accept_futu_refresh(**IDENTITY)
    with running_worker(store, refresh):
        wait_for(lambda: state(store) == "completed")
        store.accept_futu_refresh(**IDENTITY)
    assert refresh.call_count == 1
    rows = receipts(store)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["changes"] == [{"asset_id": "EXAMPLE", "before": 50, "after": 51}]
    assert payload["sync_run_id"] == "new-sync"
    # Retry after a lost commit acknowledgement is absorbing, not an enqueue replay.
    store.settle_futu_refresh(**IDENTITY, result=result, receipt_payload=payload)
    assert len(receipts(store)) == 1
    restarted = OperationStateStore(store.db_path)
    with running_worker(restarted, refresh):
        restarted.accept_futu_refresh(**IDENTITY)
    assert refresh.call_count == 1 and len(receipts(store)) == 1


def test_completed_preexisting_result_is_never_backfilled(store):
    result = quantity_result()
    store.accept_futu_refresh(**IDENTITY)
    store.claim_futu_refresh()
    # Simulate the previous version: successful stored result, no receipt.
    store.settle_futu_refresh(**IDENTITY, result=result)
    refresh = Mock()
    with running_worker(OperationStateStore(store.db_path), refresh):
        store.accept_futu_refresh(**IDENTITY)
    refresh.assert_not_called()
    assert receipts(store) == []


def test_settlement_and_enqueue_rollback_together(store, monkeypatch):
    from src.app.futu_quantity_receipt import quantity_change_payload
    from src.app.operation_state import _futu_refresh_mixin as module

    result = quantity_result()
    payload = quantity_change_payload(result)
    store.accept_futu_refresh(**IDENTITY)
    store.claim_futu_refresh()
    insert = module.insert_operation_receipt_tx

    def fail_after_insert(*args, **kwargs):
        insert(*args, **kwargs)
        raise sqlite3.OperationalError("fake commit outage")

    monkeypatch.setattr(module, "insert_operation_receipt_tx", fail_after_insert)
    with pytest.raises(sqlite3.OperationalError):
        store.settle_futu_refresh(**IDENTITY, result=result, receipt_payload=payload)
    assert state(store) == "running" and receipts(store) == []
    monkeypatch.setattr(module, "insert_operation_receipt_tx", insert)
    store.settle_futu_refresh(**IDENTITY, result=result, receipt_payload=payload)
    assert state(store) == "completed" and len(receipts(store)) == 1


def test_live_settlement_retry_preserves_original_change_and_never_resyncs(store, monkeypatch, caplog):
    store.accept_futu_refresh(**IDENTITY)
    failed, recovered = Event(), Event()
    settle = store.settle_futu_refresh

    def temporary_outage(**kwargs):
        if not recovered.is_set():
            failed.set()
            raise sqlite3.OperationalError("fake settlement outage")
        settle(**kwargs)

    monkeypatch.setattr(store, "settle_futu_refresh", temporary_outage)
    refresh = Mock(return_value=quantity_result())
    with running_worker(store, refresh):
        assert failed.wait(5)
        assert state(store) == "running" and refresh.call_count == 1
        recovered.set()
        wait_for(lambda: state(store) == "completed")
    assert refresh.call_count == 1
    assert json.loads(receipts(store)[0]["payload_json"])["changes"][0]["before"] == 50
    assert "pm_futu_refresh_settlement_failed" in caplog.text


def test_receipt_identity_is_account_scoped_and_next_unchanged_request_is_silent(store):
    def refresh(account, request_id):
        result = quantity_result()
        result["account"] = account
        if request_id == "next":
            result["positions"][0].update(current_quantity=51, quantity_changed=False)
        return result

    identities = [IDENTITY, {**IDENTITY, "account": "bob"}, {**IDENTITY, "request_id": "next"}]
    for identity in identities:
        store.accept_futu_refresh(**identity)
    with running_worker(store, refresh):
        wait_for(lambda: all(state(store, **identity) == "completed" for identity in identities))
    rows = receipts(store)
    assert len(rows) == 2 and len({row["receipt_key"] for row in rows}) == 2
    assert {json.loads(row["payload_json"])["account"] for row in rows} == {"alice", "bob"}


def test_remote_admission_rejected_before_store(store):
    service = service_for(store)
    client = TestClient(create_app(service), client=("203.0.113.5", 50000))
    assert client.post(PATH, json=IDENTITY).status_code == 403
    assert store.get_futu_refresh(**IDENTITY) is None


def test_lifespan_reuses_confirmed_full_sync_owner_without_receipt(store, monkeypatch):
    storage = object()
    sync = Mock(return_value={"success": True, "status": "written"})
    factory = Mock(return_value=Mock(sync_portfolio=sync))
    monkeypatch.setattr("src.app.FutuBalanceSyncService", factory)
    receipt = Mock()
    service = PortfolioService(storage=storage, operation_state_store=store,
                               futu_receipt_service=receipt)
    with client_for(service) as client:
        assert client.post(PATH, json=IDENTITY).status_code == 202
        wait_for(lambda: state(store) == "completed")
    factory.assert_called_once_with(storage)
    sync.assert_called_once_with(account="alice", dry_run=False, confirm=True,
                                 allow_empty_stock_snapshot=False)
    receipt.send.assert_not_called()
