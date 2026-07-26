from datetime import date

import pytest

from src.app.cash_flow_effect_store import CashFlowEffectStore, sha256_json


def _source(record_id="rec_1", *, amount="100.00", flow_date="2026-07-26"):
    return {
        "record_id": record_id,
        "account": "lx",
        "broker": "某券商",
        "currency": "CNY",
        "signed_amount": amount,
        "flow_date": flow_date,
    }


def test_normal_open_never_creates_missing_database(tmp_path):
    path = tmp_path / "effects.sqlite3"

    with pytest.raises(RuntimeError, match="not initialized"):
        CashFlowEffectStore(path)

    assert not path.exists()


def test_init_binds_immutable_cutover_and_validates_integrity(tmp_path):
    path = tmp_path / "effects.sqlite3"
    store = CashFlowEffectStore.initialize(
        db_path=path,
        cutover_date="2026-07-01",
    )

    assert store.cutover_date == date(2026, 7, 1)
    assert store.assert_cutover("2026-07-01") == date(2026, 7, 1)
    assert store.integrity_check()["ok"] is True
    assert path.stat().st_mode & 0o777 == 0o600

    with pytest.raises(RuntimeError, match="immutable"):
        CashFlowEffectStore.initialize(
            db_path=path,
            cutover_date="2026-07-02",
        )
    with pytest.raises(RuntimeError, match="config is missing"):
        store.assert_cutover(None)


def test_effect_versions_are_idempotent_and_supersede_unresolved(tmp_path):
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    first_source = _source()
    first_hash = sha256_json(first_source)
    first = store.create_version(
        source=first_source,
        source_hash=first_hash,
        state="pending",
        mode="apply",
    )

    repeated = store.create_version(
        source=first_source,
        source_hash=first_hash,
        state="pending",
        mode="apply",
    )
    assert repeated["effect_id"] == first["effect_id"]

    second_source = _source(amount="120.00")
    second = store.create_version(
        source=second_source,
        source_hash=sha256_json(second_source),
        state="pending",
        mode="apply",
    )

    assert second["version"] == 2
    assert store.get_effect(first["effect_id"])["state"] == "superseded"
    assert store.get_latest_for_record("rec_1")["effect_id"] == second["effect_id"]


def test_nav_blockers_respect_scheduled_flow_date(tmp_path):
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    source = _source(flow_date="2026-08-01")
    effect = store.create_version(
        source=source,
        source_hash=sha256_json(source),
        state="scheduled",
        mode="apply",
    )

    assert store.list_blockers(account="lx", nav_date="2026-07-31") == []
    assert [item["effect_id"] for item in store.list_blockers(
        account="lx",
        nav_date="2026-08-01",
    )] == [effect["effect_id"]]

    store.update_effect(
        effect["effect_id"],
        state="applied",
        event_type="applied",
        expected_states={"scheduled"},
    )
    assert store.list_blockers(account="lx", nav_date="2026-08-01") == []


def test_scan_run_only_completes_once(tmp_path):
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    run_id = store.begin_scan(scope="all")
    result = store.finish_scan(
        run_id,
        status="completed",
        source_record_count=2,
        source_digest="digest",
        added_count=1,
    )

    assert result["status"] == "completed"
    assert store.latest_successful_scan()["scan_run_id"] == run_id
    with pytest.raises(RuntimeError, match="not active"):
        store.finish_scan(run_id, status="failed", error="late failure")


def test_holding_fingerprint_separates_observed_and_confirmed_state(tmp_path):
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    observed = store.observe_fingerprint(
        holding_identity="CNY-CASH|lx|某券商",
        holding_record_id="hold_1",
        amount="10.00",
        observation_hash="h1",
    )
    assert observed["last_confirmed_hash"] is None
    assert observed["last_observed_hash"] == "h1"

    confirmed = store.confirm_fingerprint(
        holding_identity="CNY-CASH|lx|某券商",
        holding_record_id="hold_1",
        amount="10.00",
        confirmation_hash="h1",
        effect_id="effect_1",
    )
    assert confirmed["last_confirmed_hash"] == "h1"
    assert confirmed["confirmed_by_effect_id"] == "effect_1"


def test_fx_confirmation_and_receipt_outbox_are_durable(tmp_path):
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    confirmation_id = store.record_fx_confirmation(
        record_id="rec_fx",
        source_hash="source-hash",
        exchange_rate="7.2000",
        exchange_rate_date="2026-07-01",
        exchange_rate_source="provider:example",
        exchange_rate_evidence_type="provider",
        cny_amount="72.00",
        confirmation={"method": "local_cli"},
    )
    assert confirmation_id.startswith("cfx_")
    assert store.latest_fx_confirmation("rec_fx")["confirmation"]["method"] == "local_cli"

    assert store.enqueue_receipt(
        receipt_key="scan:digest",
        receipt_type="discovery",
        scan_run_id="scan_1",
        payload={"changed": 1},
    ) is True
    assert store.enqueue_receipt(
        receipt_key="scan:digest",
        receipt_type="discovery",
        scan_run_id="scan_1",
        payload={"changed": 1},
    ) is False
    assert len(store.list_pending_receipts()) == 1

    store.mark_receipt("scan:digest", success=False, error="network")
    assert store.list_pending_receipts()[0]["attempt_count"] == 1
    store.mark_receipt("scan:digest", success=True, message_id="msg_1")
    assert store.list_pending_receipts() == []
