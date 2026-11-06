from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
from types import SimpleNamespace

import pytest

from src.app.holdings_reconciliation_service import HoldingsReconciliationService
from src.app.holdings_workflow_service import HoldingsWorkflowService
from src.app.operation_state_store import OperationStateStore
from src.domain.holding_mutations import HoldingRepairPatch
from src.domain.holdings import RawHoldingRecord


class _Storage:
    def __init__(self, *, currency="", patch_mode="success"):
        self.fields = {
            "asset_id": "AAPL",
            "asset_name": "Apple",
            "asset_type": "us_stock",
            "account": "lx",
            "broker": "IBKR",
            "quantity": 1,
            "currency": currency,
            "asset_class": "美国资产",
        }
        self.patch_mode = patch_mode
        self.patch_calls = []
        self.read_calls = []

    def get_raw_holdings(self, *, account=None, record_id=None):
        self.read_calls.append({"account": account, "record_id": record_id})
        if account is not None and account != self.fields.get("account"):
            return []
        if record_id is not None and record_id != "rec-1":
            return []
        return [RawHoldingRecord("rec-1", dict(self.fields))]

    def patch_holding_record(self, patch):
        assert isinstance(patch, HoldingRepairPatch)
        record_id = patch.record_id
        fields = dict(patch.values)
        self.patch_calls.append((record_id, dict(fields)))
        if self.patch_mode == "timeout_after_apply":
            self.fields.update(fields)
            raise TimeoutError("timeout after server accepted patch")
        if self.patch_mode == "no_apply_error":
            raise TimeoutError("transport outcome unavailable")
        if self.patch_mode == "mismatch":
            self.fields.update(fields)
            target_field = next(iter(fields))
            self.fields[target_field] = "EUR"
            return RawHoldingRecord("rec-1", dict(self.fields))
        self.fields.update(fields)
        return RawHoldingRecord("rec-1", dict(self.fields))


def _workflow(tmp_path, storage, lock_events=None):
    @contextmanager
    def lock(key):
        if lock_events is not None:
            lock_events.append(("enter", key))
        try:
            yield
        finally:
            if lock_events is not None:
                lock_events.append(("exit", key))

    reconciliation = HoldingsReconciliationService(storage=storage)
    return HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(tmp_path / "operations.sqlite3"),
        reconciliation=reconciliation,
        lock_factory=lock,
    )


def _operator():
    return {
        "username": "tester",
        "hostname": "host",
        "command_mode": "test",
        "trusted_identity": False,
    }


def test_notify_materializes_one_case_without_remote_holding_write(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)

    result = service.notify(record_id="rec-1")

    assert storage.patch_calls == []
    assert result["workflow"]["created_case_keys"]
    cases = service.list_cases()["cases"]
    assert [(item["field"], item["state"]) for item in cases] == [
        ("currency", "pending_apply")
    ]
    # Currency policy v2 deliberately changes case identity.
    assert cases[0]["case_key"] == (
        "40d05d72ae2bb4dede1b7fca6b9455411686ece4925bf528cc833d0f72e51967"
    )
    assert cases[0]["case_precondition_digest"] == (
        "holdings-precondition.v2:"
        "a20516349cabdadc50e6d067535d5278f8da62fccaf13a317aba925ea8297c68"
    )
    receipt = service.store.get_operation_receipt(
        f"holdings:case:discovered:{cases[0]['case_key']}"
    )
    assert "--record-id rec-1 --apply --confirm" in receipt["payload"]["action"]["command"]


def test_spy_repair_closes_only_name_while_legacy_timestamps_migrate(tmp_path):
    record_id = "recvfmaw53roJx"

    class SpyStorage(_Storage):
        def __init__(self):
            super().__init__(currency="USD")
            self.fields.update(
                {
                    "asset_id": "SPY",
                    "asset_name": "SPY",
                    "asset_type": "us_fund",
                    "account": "sy",
                    "broker": "富途",
                    "asset_class": "美国资产",
                    "created_at": "2026-03-30T00:00:00Z",
                    "updated_at": "2026-07-31T00:00:00Z",
                }
            )

        def get_raw_holdings(self, *, account=None, record_id=None):
            self.read_calls.append({"account": account, "record_id": record_id})
            if account is not None and account != self.fields["account"]:
                return []
            if record_id is not None and record_id != record_id_value:
                return []
            return [RawHoldingRecord(record_id_value, dict(self.fields))]

    record_id_value = record_id
    storage = SpyStorage()

    def observe(_account):
        return SimpleNamespace(
            source="futu",
            source_snapshot_id="snapshot-sy",
            observed_at_utc="2026-08-01T01:00:00+00:00",
            profile_fingerprint="profile-sy",
            account_fingerprint="account-sy",
            positions=(
                SimpleNamespace(
                    asset_id="SPY.US",
                    raw_code="US.SPY",
                    asset_name="标普500ETF-SPDR",
                    security_type="ETF",
                    market="US",
                    currency="USD",
                    currency_explicit=True,
                ),
            ),
        )

    reconciliation = HoldingsReconciliationService(
        storage=storage,
        futu_observer=observe,
    )
    service = HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(tmp_path / "operations.sqlite3"),
        reconciliation=reconciliation,
        lock_factory=lambda _key: nullcontext(),
    )
    evaluation = reconciliation.evaluate(record_id=record_id)
    current_cases = service._cases_for_record(
        evaluation.report.records[0], evaluation
    )
    assert {item["field"] for item in current_cases} == {
        "asset_type",
        "asset_name",
        "created_at",
        "updated_at",
    }
    legacy_cases = []
    for item in current_cases:
        legacy = dict(item)
        legacy["case_precondition_digest"] = item[
            "legacy_case_precondition_digest"
        ]
        legacy_cases.append(legacy)
    service.store.materialize_holding_cases(
        cases=legacy_cases,
        discovery_receipts=[
            service._discovery_receipt(item) for item in legacy_cases
        ],
        trigger={"mode": "seed_legacy_fixture"},
    )

    by_field = {item["field"]: item for item in legacy_cases}
    service.store.resolve_holding_cases_external(
        record_id=record_id,
        active_case_keys=[
            item["case_key"]
            for item in legacy_cases
            if item["field"] != "asset_type"
        ],
        record_digest=evaluation.report.records[0].record_digest,
        current_identity=service._raw_identity(storage.fields),
        trigger={"mode": "seed_already_repaired_type"},
    )
    timestamp_keys = {
        by_field[field]["case_key"] for field in ("created_at", "updated_at")
    }

    def receipt_rows():
        with service.store._connect() as conn:
            return [
                (
                    row["receipt_key"],
                    row["receipt_type"],
                    json.loads(row["payload_json"]),
                )
                for row in conn.execute(
                    "SELECT receipt_key, receipt_type, payload_json "
                    "FROM operation_receipt_outbox ORDER BY receipt_key"
                ).fetchall()
            ]

    before_receipts = receipt_rows()
    storage.fields.update(
        {
            "asset_type": "exchange_fund",
            "asset_name": "标普500ETF-SPDR",
        }
    )

    repaired = service.notify(record_id=record_id)

    assert storage.patch_calls == []
    assert repaired["workflow"]["closed_case_keys"] == [
        by_field["asset_name"]["case_key"]
    ]
    assert repaired["workflow"]["superseded_case_keys"] == []
    after_receipts = receipt_rows()
    new_receipts = [row for row in after_receipts if row not in before_receipts]
    assert len(new_receipts) == 1
    assert new_receipts[0][1] == "holding_case_closed"
    assert new_receipts[0][2]["field"] == "asset_name"
    for case_key in timestamp_keys:
        durable = service.store.get_holding_case(case_key)
        assert durable["state"] == "pending_manual_edit"
        assert durable["case_precondition_digest"].startswith(
            "holdings-precondition.v2:"
        )
        assert [
            event["event_type"]
            for event in service.store.list_holding_case_events(case_key)
        ].count("precondition_contract_migrated") == 1

    event_counts = {
        item["case_key"]: len(
            service.store.list_holding_case_events(item["case_key"])
        )
        for item in service.store.list_holding_cases()
    }
    repeated = service.notify(record_id=record_id)

    assert all(not values for values in repeated["workflow"].values())
    assert receipt_rows() == after_receipts
    assert event_counts == {
        item["case_key"]: len(
            service.store.list_holding_case_events(item["case_key"])
        )
        for item in service.store.list_holding_cases()
    }


def test_missing_apply_is_exact_and_locks_account_before_record(tmp_path):
    storage = _Storage()
    locks = []
    service = _workflow(tmp_path, storage, lock_events=locks)

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is True
    assert storage.fields["currency"] == "USD"
    assert storage.patch_calls == [("rec-1", {"currency": "USD"})]
    assert locks[:2] == [
        ("enter", "account-write:lx"),
        ("enter", "holding-record-write:rec-1"),
    ]
    case = next(iter(service.store.list_holding_cases()))
    assert case["state"] == "resolved_accept"
    assert case["remote_attempt_started_at"] is not None

    second = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert second["status"] == "no_eligible_missing_fields"
    assert len(storage.patch_calls) == 1


def test_direct_apply_migrates_eligible_legacy_case_before_remote_write(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    evaluation = service.reconciliation.evaluate(record_id="rec-1")
    current = service._cases_for_record(
        evaluation.report.records[0], evaluation
    )[0]
    legacy = dict(current)
    legacy["case_precondition_digest"] = current[
        "legacy_case_precondition_digest"
    ]
    service.store.materialize_holding_cases(
        cases=[legacy],
        discovery_receipts=[service._discovery_receipt(legacy)],
    )

    result = service.apply_missing(
        record_id="rec-1", confirmed_operator=_operator()
    )

    assert result["success"] is True
    assert storage.patch_calls == [("rec-1", {"currency": "USD"})]
    durable = service.store.get_holding_case(current["case_key"])
    assert durable["state"] == "resolved_accept"
    assert durable["case_precondition_digest"] == current[
        "case_precondition_digest"
    ]
    assert [
        event["event_type"]
        for event in service.store.list_holding_case_events(current["case_key"])
    ].count("precondition_contract_migrated") == 1


def test_timeout_with_actual_success_is_resolved_by_fresh_readback(tmp_path):
    storage = _Storage(patch_mode="timeout_after_apply")
    service = _workflow(tmp_path, storage)

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is True
    assert result["patch_error"] == "timeout after server accepted patch"
    assert next(iter(service.store.list_holding_cases()))["state"] == "resolved_accept"


def test_attempt_with_exact_before_value_becomes_unknown_and_never_retries(tmp_path):
    storage = _Storage(patch_mode="no_apply_error")
    service = _workflow(tmp_path, storage)

    first = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    case_key = next(iter(first["case_states"]))
    assert first["case_states"][case_key] == "apply_outcome_unknown"
    assert len(storage.patch_calls) == 1

    with pytest.raises(ValueError, match="not applicable"):
        service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert len(storage.patch_calls) == 1

    storage.fields["currency"] = "USD"
    recovered = service.recover(case_key=case_key, confirmed_operator=_operator())
    assert recovered["status"] == "resolved_accept"
    assert service.store.get_holding_case(case_key)["state"] == "resolved_accept"


def test_mismatching_readback_supersedes_case(tmp_path):
    storage = _Storage(patch_mode="mismatch")
    service = _workflow(tmp_path, storage)

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is False
    assert next(iter(result["case_states"].values())) == "superseded"


def test_conflict_keep_current_requires_reason_and_exact_scope(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    notified = service.notify(record_id="rec-1")
    case_key = notified["workflow"]["created_case_keys"][0]

    with pytest.raises(ValueError, match="nonblank reason"):
        service.resolve(
            case_key=case_key,
            decision="keep-current",
            reason=" ",
            confirmed_operator=_operator(),
        )
    storage.fields["currency"] = "EUR"
    with pytest.raises(ValueError, match="scope changed"):
        service.resolve(
            case_key=case_key,
            decision="keep-current",
            reason="manual exception",
            confirmed_operator=_operator(),
        )
    assert storage.patch_calls == []


def test_conflict_keep_current_closes_without_feishu_write(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]

    result = service.resolve(
        case_key=case_key,
        decision="keep-current",
        reason="broker statement confirmed CNY",
        confirmed_operator=_operator(),
    )

    assert result["status"] == "resolved_keep"
    assert result["wrote_holdings"] is False
    assert storage.patch_calls == []
    assert service.store.get_holding_case(case_key)["state"] == "resolved_keep"

    storage.fields["currency"] = "EUR"
    changed = service.notify(record_id="rec-1")
    assert changed["workflow"]["superseded_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "superseded"
    new_cases = [
        item
        for item in service.store.list_holding_cases()
        if item["case_key"] != case_key
    ]
    assert len(new_cases) == 1
    assert new_cases[0]["state"] == "pending_confirmation"


def test_direct_keep_resolution_migrates_eligible_legacy_scope(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    evaluation = service.reconciliation.evaluate(record_id="rec-1")
    current = service._cases_for_record(
        evaluation.report.records[0], evaluation
    )[0]
    legacy = dict(current)
    legacy["case_precondition_digest"] = current[
        "legacy_case_precondition_digest"
    ]
    service.store.materialize_holding_cases(
        cases=[legacy],
        discovery_receipts=[service._discovery_receipt(legacy)],
    )

    result = service.resolve(
        case_key=legacy["case_key"],
        decision="keep-current",
        reason="statement verified",
        confirmed_operator=_operator(),
    )

    assert result["status"] == "resolved_keep"
    assert storage.patch_calls == []
    durable = service.store.get_holding_case(legacy["case_key"])
    assert durable["state"] == "resolved_keep"
    assert durable["case_precondition_digest"] == current[
        "case_precondition_digest"
    ]
    assert durable["resolution"]["confirmation_scope"] == service._confirmation_scope(
        current
    )


def test_repeat_keep_current_rechecks_fresh_scope_before_deduplication(tmp_path):
    storage = _Storage(currency="CNY")
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    service.resolve(
        case_key=case_key,
        decision="keep-current",
        reason="statement evidence",
        confirmed_operator=_operator(),
    )
    storage.fields["currency"] = "EUR"

    with pytest.raises(ValueError, match="scope changed"):
        service.resolve(
            case_key=case_key,
            decision="keep-current",
            reason="repeat",
            confirmed_operator=_operator(),
        )


def test_manual_repair_is_proved_by_fresh_notify_and_resolved_external(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    storage.fields["currency"] = "USD"

    result = service.notify(record_id="rec-1")

    assert result["workflow"]["closed_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "resolved_external"
    assert storage.patch_calls == []

    storage.fields["currency"] = ""
    reopened = service.notify(record_id="rec-1")
    assert reopened["workflow"]["reopened_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "pending_apply"
    with service.store._connect() as conn:
        discovery_count = conn.execute(
            """
            SELECT COUNT(*) FROM operation_receipt_outbox
            WHERE receipt_key = ?
            """,
            (f"holdings:case:discovered:{case_key}",),
        ).fetchone()[0]
    assert discovery_count == 1

    storage.fields["currency"] = "USD"
    closed_again = service.notify(
        record_id="rec-1",
        trigger={"mode": "event_validate_notify", "event_id": "event-2"},
    )
    assert closed_again["workflow"]["closed_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "resolved_external"


def test_manual_repair_with_identity_change_is_superseded_not_resolved_external(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    storage.fields["currency"] = "USD"
    storage.fields["asset_id"] = "MSFT"

    result = service.notify(record_id="rec-1")

    assert result["workflow"]["closed_case_keys"] == []
    assert result["workflow"]["superseded_case_keys"] == [case_key]
    assert service.store.get_holding_case(case_key)["state"] == "superseded"


def test_apply_fresh_scan_closes_other_manually_repaired_case(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    currency_case = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]
    storage.fields["currency"] = "USD"
    storage.fields["asset_class"] = ""

    result = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())

    assert result["success"] is True
    assert result["status"] == "no_eligible_missing_fields"
    assert result["workflow"]["closed_case_keys"] == [currency_case]
    assert service.store.get_holding_case(currency_case)["state"] == "resolved_external"
    assert storage.patch_calls == []


def test_asset_class_case_uses_independent_policy_version(tmp_path):
    storage = _Storage(currency="CNY")
    storage.fields.update(
        {
            "asset_id": "CNY-CASH",
            "asset_name": "Cash",
            "asset_type": "cash",
            "asset_class": "美国资产",
        }
    )
    service = _workflow(tmp_path, storage)

    result = service.notify(record_id="rec-1")

    assert result["workflow"]["created_case_keys"]
    case = service.list_cases()["cases"][0]
    assert case["field"] == "asset_class"
    assert case["policy_version"] == (
        "holdings-validation.v1+holdings-asset-class.v2"
    )


def test_recovery_that_still_reads_before_remains_unknown_without_resend(tmp_path):
    storage = _Storage(patch_mode="no_apply_error")
    service = _workflow(tmp_path, storage)
    first = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    case_key = next(iter(first["case_states"]))

    recovered = service.recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "apply_outcome_unknown"
    with service.store._connect() as conn:
        attention = conn.execute(
            "SELECT * FROM operation_receipt_outbox WHERE receipt_type = 'holding_case_attention_required'"
        ).fetchall()
    assert len(attention) == 1


def test_recover_rejects_case_that_never_started_apply(tmp_path):
    storage = _Storage()
    service = _workflow(tmp_path, storage)
    case_key = service.notify(record_id="rec-1")["workflow"]["created_case_keys"][0]

    with pytest.raises(ValueError, match="no recoverable apply attempt"):
        service.recover(case_key=case_key, confirmed_operator=_operator())

    assert service.store.get_holding_case(case_key)["state"] == "pending_apply"


@pytest.mark.parametrize(
    ("field", "value"),
    (("account", "sy"), ("asset_id", "MSFT"), ("broker", "OTHER")),
)
def test_recovery_supersedes_when_frozen_identity_drifted(tmp_path, field, value):
    storage = _Storage(patch_mode="no_apply_error")
    service = _workflow(tmp_path, storage)
    first = service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    case_key = next(iter(first["case_states"]))
    storage.fields["currency"] = "USD"
    storage.fields[field] = value

    recovered = service.recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "superseded"
    assert service.store.get_holding_case(case_key)["state"] == "superseded"


class _DuplicateStorage:
    def __init__(self, rows):
        self.rows = list(rows)
        self.reads = []

    def get_raw_holdings(self, *, account=None, record_id=None, require_complete=False):
        self.reads.append((account, record_id, require_complete))
        return [
            row for row in self.rows
            if (account is None or row.raw_fields.get("account") == account)
            and (record_id is None or row.record_id == record_id)
        ]


def _duplicate_row(record_id, **fields):
    return RawHoldingRecord(record_id, {
        "asset_id": "AAPL", "asset_name": "Apple", "account": "lx", "broker": "IBKR",
        "asset_type": "us_stock", "quantity": 1, "currency": "USD", "asset_class": "美国资产",
        **fields,
    })


def _duplicate_service(tmp_path, rows, *, lock_factory=None):
    storage = _DuplicateStorage(rows)
    def no_provider(*args, **kwargs):
        raise AssertionError("duplicate observation must not query a provider")
    reconciliation = HoldingsReconciliationService(
        storage=storage, futu_observer=no_provider,
        fund_name_lookup=no_provider, hk_stock_name_lookup=no_provider,
    )
    service = HoldingsWorkflowService(
        storage=storage, store=OperationStateStore(tmp_path / "duplicates.sqlite3"),
        reconciliation=reconciliation,
        lock_factory=lock_factory or (lambda key: nullcontext()),
    )
    return service, storage


def test_duplicate_plan_is_pure_and_matches_existing_validator_identity(tmp_path):
    rows = [_duplicate_row("two", asset_type="cash", quantity="bad"), _duplicate_row("one")]
    service, storage = _duplicate_service(tmp_path, rows)
    monitor = service.plan_duplicate_observation(
        rows, trigger={"mode": "monitor"}, covered_record_ids=["one"],
    )
    assert storage.reads == []
    assert service.store.list_holding_cases() == []
    assert len(monitor["cases"]) == 1
    case = monitor["cases"][0]
    assert case["current"]["record_ids"] == ["one", "two"]
    assert case["state"] == "pending_manual_edit"
    assert case["field"] == "__record__:duplicate_identity"
    evaluation = service.reconciliation.evaluate_records(rows)
    ordinary = next(
        item for item in service.plan_evaluation(evaluation, trigger={})["cases"]
        if item["record_id"] == "one" and item["kind"] == "duplicate_identity"
    )
    assert case["case_key"] == ordinary["case_key"]
    assert case["case_precondition_digest"] == ordinary["case_precondition_digest"]
    assert "--record-id" not in monitor["discovery_receipts"][0]["payload"]["action"]["command"]


def test_single_record_event_and_notify_cannot_close_duplicate_case(tmp_path):
    service, storage = _duplicate_service(tmp_path, [_duplicate_row("one"), _duplicate_row("two")])
    first = service.notify(account="lx")
    keys = first["workflow"]["created_case_keys"]
    assert len(keys) == 2
    one = next(case for case in service.store.list_holding_cases() if case["record_id"] == "one")
    assert service.notify(record_id="one")["workflow"]["closed_case_keys"] == []
    plan = service.process_event_notification(record_id="one", trigger={"event_id": "event-one"})
    with service.store._connect() as conn:
        result = service.store._resolve_absent_holding_cases_tx(
            conn, record_id="one", active_case_keys=plan["active_case_keys"],
            record_digest="single-read", current_identity=one["identity"],
            trigger={"mode": "event"}, now=service.store.now_factory().isoformat(),
        )
    assert result["closed_case_keys"] == []
    assert all(service.store.get_holding_case(key)["state"] == "pending_manual_edit" for key in keys)
    assert storage.reads[0] == ("lx", None, True)


def test_complete_notify_reconciles_duplicate_members_and_reopens_without_resend(tmp_path):
    service, storage = _duplicate_service(tmp_path, [_duplicate_row("one"), _duplicate_row("two")])
    first = service.notify()
    keys = set(first["workflow"]["created_case_keys"])
    storage.rows[0] = _duplicate_row("one", quantity=2, asset_name="Edited name")
    repeat = service.notify(account="lx")["workflow"]
    assert repeat["created_case_keys"] == []
    assert repeat["enqueued_receipt_keys"] == []
    assert set(repeat["refreshed_case_keys"]) == keys
    storage.rows[1] = _duplicate_row("two", asset_id="MSFT", asset_name="Microsoft")
    repaired = service.notify()["workflow"]
    assert len(repaired["closed_case_keys"]) == 1
    assert len(repaired["superseded_case_keys"]) == 1
    storage.rows[1] = _duplicate_row("two")
    reopened = service.notify()["workflow"]
    assert set(reopened["reopened_case_keys"]) == keys
    assert reopened["enqueued_receipt_keys"] == []


def test_duplicate_missing_requires_deletion_proof_and_valid_remaining_fields(tmp_path):
    service, storage = _duplicate_service(tmp_path, [_duplicate_row("one"), _duplicate_row("two")])
    keys = service.notify()["workflow"]["created_case_keys"]
    storage.rows = [_duplicate_row("one", quantity="invalid")]
    first_missing = service.plan_duplicate_observation(
        storage.rows, trigger={}, covered_record_ids=[],
    )
    assert service._reconcile_duplicate_plan(first_missing)["closed_case_keys"] == []
    deletion = service.plan_duplicate_observation(
        storage.rows, trigger={}, covered_record_ids=["one"], proven_deleted_ids=["two"],
    )
    archived = service._reconcile_duplicate_plan(deletion)
    assert len(archived["closed_case_keys"]) == 1
    deleted = service.store.get_holding_case(archived["closed_case_keys"][0])
    assert deleted["record_id"] == "two"
    assert deleted["resolution"]["decision"] == "record_deleted"
    storage.rows = [_duplicate_row("one")]
    repaired = service._reconcile_duplicate_plan(service.plan_duplicate_observation(
        storage.rows, trigger={}, covered_record_ids=["one"], proven_deleted_ids=["two"],
    ))
    assert set(archived["closed_case_keys"] + repaired["closed_case_keys"]) == set(keys)


@pytest.mark.parametrize("identity_edit", [{"asset_id": None}, {"broker": "unknown"}])
def test_incomplete_identity_does_not_close_or_supersede_old_duplicate_group(tmp_path, identity_edit):
    service, storage = _duplicate_service(tmp_path, [_duplicate_row("one"), _duplicate_row("two")])
    keys = service.notify()["workflow"]["created_case_keys"]
    storage.rows[1] = _duplicate_row("two", **identity_edit)
    result = service.notify()["workflow"]
    assert not set(keys).intersection(result["closed_case_keys"] + result["superseded_case_keys"])
    assert all(service.store.get_holding_case(key)["state"] == "pending_manual_edit" for key in keys)


def test_duplicate_transaction_keeps_unrelated_field_cases_and_rolls_back(tmp_path):
    rows = [_duplicate_row("one"), _duplicate_row("two")]
    service, storage = _duplicate_service(tmp_path, rows)
    field_case = service.notify(record_id="one")["workflow"]
    assert field_case["created_case_keys"] == []
    plan = service.plan_duplicate_observation(rows, trigger={}, covered_record_ids=["one", "two"])
    with pytest.raises(RuntimeError, match="rollback"):
        with service.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            service.store._reconcile_duplicate_cases_tx(
                conn, plan=plan, now=service.store.now_factory().isoformat(),
            )
            raise RuntimeError("rollback")
    assert service.store.list_holding_cases() == []
    storage.rows = [_duplicate_row("one", currency=""), _duplicate_row("two")]
    service.notify(record_id="one")
    currency_case = next(case for case in service.store.list_holding_cases() if case["field"] == "currency")
    service._reconcile_duplicate_plan(plan)
    assert service.store.get_holding_case(currency_case["case_key"])["state"] == "pending_apply"


def test_full_notify_acquires_observation_before_read_then_account_lock(tmp_path):
    order = []
    @contextmanager
    def lock(key):
        order.append(("enter", key))
        yield
        order.append(("exit", key))
    service, storage = _duplicate_service(tmp_path, [_duplicate_row("one"), _duplicate_row("two")], lock_factory=lock)
    reader = storage.get_raw_holdings
    def read(**kwargs):
        order.append(("read", kwargs))
        return reader(**kwargs)
    storage.get_raw_holdings = read
    service.notify(account="lx")
    assert order[0][0] == "enter" and order[0][1].startswith("holdings-observation:")
    assert order[1] == ("read", {"account": "lx", "require_complete": True})
    assert order[2] == ("enter", "account-write:lx")
    assert order[-1][0] == "exit" and order[-1][1] == order[0][1]


def test_remote_success_then_local_finalize_failure_recovers_without_second_patch(tmp_path):
    class FailFinalizeStore(OperationStateStore):
        def finalize_holding_cases(self, **kwargs):
            raise RuntimeError("local finalize unavailable")

    storage = _Storage()
    path = tmp_path / "operations.sqlite3"
    failing_store = FailFinalizeStore(path)
    service = HoldingsWorkflowService(
        storage=storage,
        store=failing_store,
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    )
    with pytest.raises(RuntimeError, match="local finalize"):
        service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert storage.fields["currency"] == "USD"
    assert len(storage.patch_calls) == 1
    case_key = OperationStateStore(path).list_holding_cases()[0]["case_key"]

    recovered = HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(path),
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    ).recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "resolved_accept"
    assert len(storage.patch_calls) == 1


def test_local_failure_before_remote_attempt_recovers_as_retryable(tmp_path):
    class FailRemoteMarkerStore(OperationStateStore):
        def mark_holding_remote_attempt(self, **kwargs):
            raise RuntimeError("local marker unavailable")

    storage = _Storage()
    path = tmp_path / "operations.sqlite3"
    service = HoldingsWorkflowService(
        storage=storage,
        store=FailRemoteMarkerStore(path),
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    )
    with pytest.raises(RuntimeError, match="local marker"):
        service.apply_missing(record_id="rec-1", confirmed_operator=_operator())
    assert storage.patch_calls == []
    case_key = OperationStateStore(path).list_holding_cases()[0]["case_key"]

    recovered = HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(path),
        reconciliation=HoldingsReconciliationService(storage=storage),
        lock_factory=lambda _key: _null_lock(),
    ).recover(case_key=case_key, confirmed_operator=_operator())

    assert recovered["status"] == "failed_retryable"
    assert storage.patch_calls == []


@contextmanager
def _null_lock():
    yield
