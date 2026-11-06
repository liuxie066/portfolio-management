from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from src.app.holding_change_monitor_service import HoldingChangeMonitorService, MonitorSettings
from src.app.operation_state_store import OperationStateStore
from src.domain.holdings import RawHoldingRecord
from src.domain.holding_changes import observe
from src.app.holdings_validation import duplicate_holding_groups


def raw(rid="r1", **changes):
    fields = dict(asset_id="EXAMPLE", account="example", broker="某券商", asset_type="us_stock",
                  currency="USD", quantity=100, asset_name="Example")
    fields.update(changes)
    return RawHoldingRecord(rid, fields)


class Clock:
    value = datetime(2026, 11, 6, 10)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class Storage:
    def __init__(self, rows):
        self.rows = rows
        self.reads = 0

    def get_raw_holdings(self, *, require_complete):
        assert require_complete is True
        self.reads += 1
        return deepcopy(self.rows)


@pytest.fixture
def monitor(tmp_path):
    clock = Clock()
    store = OperationStateStore(tmp_path / "operations.sqlite3", now_factory=clock)
    storage = Storage([raw()])
    sender = Mock()
    service = HoldingChangeMonitorService(storage=storage, store=store,
        settings=MonitorSettings(("example",)), target=("base", "holdings"), dispatcher=sender)
    service.clock = clock
    return service


def apply(service):
    return service.scan(apply=True, confirm=True)


def test_initialize_silent_and_preview_does_not_advance(monitor):
    assert monitor.initialize()["status"] == "preview"
    assert monitor._state() is None
    assert monitor.initialize(apply=True, confirm=True)["status"] == "initialized"
    assert monitor.dispatcher.mock_calls == []
    old = monitor._state()
    monitor.storage.rows = [raw(quantity=120)]
    assert monitor.scan()["changes"][0]["after"]["quantity"] == "120"
    assert monitor._state() == old
    with pytest.raises(ValueError, match="already exists"):
        monitor.initialize(apply=True, confirm=True)


def test_quantity_round_trip_is_not_content_deduplicated(monitor):
    monitor.initialize(apply=True, confirm=True)
    keys = []
    for quantity in (120, 100, 120):
        monitor.clock.advance(1)
        monitor.storage.rows = [raw(quantity=quantity)]
        result = apply(monitor)
        assert result["success"], result
        assert len(result["changes"]) == 1
        keys.extend(result["receipt_keys"])
    assert len(set(keys)) == 3
    assert monitor._state()["completed"] == 0
    assert monitor._state()["background_success_at"] is None


@pytest.mark.parametrize("field", ["quantity", "avg_cost"])
@pytest.mark.parametrize("outside_scope", [
    {"broker": "富途"}, {"asset_type": "cash"}, {"account": "another-account"},
])
def test_excluded_large_values_do_not_block_other_changes(monitor, field, outside_scope):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw(quantity=101), raw("excluded", asset_id="OTHER",
        **outside_scope, **{field: "1e30"})]
    result = apply(monitor)
    assert result["success"], result
    assert [item["record_id"] for item in result["changes"]] == ["r1"]
    assert monitor._state()["rows"]["r1"]["snapshot"]["quantity"] == "101"


@pytest.mark.parametrize("before, after, expected", [
    ("100", "1e30", "1000000000000000000000000000000"),
    ("0.1234567890123456789012345678901234", "0.1234567890123456789012345678901235",
     "0.1234567890123456789012345678901235"),
])
def test_finite_numeric_changes_preserve_exact_value(monitor, before, after, expected):
    monitor.storage.rows = [raw(quantity=before)]
    assert monitor.initialize(apply=True, confirm=True)["success"]
    monitor.storage.rows = [raw(quantity=after, avg_cost="1e30")]
    result = apply(monitor)
    assert result["success"], result
    assert len(result["changes"]) == 1
    assert result["changes"][0]["kind"] == "quantity_changed"
    assert result["changes"][0]["after"]["quantity"] == expected
    assert monitor._state()["rows"]["r1"]["snapshot"]["quantity"] == expected


def test_metadata_cost_and_number_format_changes_are_silent(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw(quantity="100.00", avg_cost=12, asset_name="New name", industry="AI")]
    result = apply(monitor)
    assert result["success"], result
    assert result["changes"] == []
    assert result["receipt_keys"] == []


def test_gap_preserves_last_valid_then_recovers(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw(quantity="NaN")]
    assert apply(monitor)["changes"] == []
    assert monitor._state()["rows"]["r1"]["snapshot"]["quantity"] == "100"
    monitor.storage.rows = [raw(quantity=130)]
    change = apply(monitor)["changes"][0]
    assert change["before"]["quantity"] == "100"
    assert change["after_gap"] is True


def test_deletion_needs_second_complete_read(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = []
    assert apply(monitor)["changes"] == []
    monitor.clock.advance(1)
    assert apply(monitor)["changes"] == []
    monitor.clock.advance(1)
    result = apply(monitor)
    assert result["success"], result
    assert [change["kind"] for change in result["changes"]] == ["record_deleted"]
    assert apply(monitor)["changes"] == []


def test_replacement_waits_then_one_record_replaced_change(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw("r2", quantity=120)]
    assert apply(monitor)["changes"] == []
    monitor.clock.advance(2)
    result = apply(monitor)
    assert result["success"], result
    assert [change["kind"] for change in result["changes"]] == ["record_replaced"]


def test_duplicate_creates_case_and_preserves_quantity_baseline(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw(), raw("r2", quantity=120)]
    result = apply(monitor)
    assert result["success"], result
    assert result["changes"] == []
    cases = monitor.store.list_holding_cases(record_id="r1", limit=None)
    assert len(cases) == 1
    assert cases[0]["kind"] == "duplicate_identity"
    assert apply(monitor)["receipt_keys"] == []
    monitor.storage.rows = [raw(quantity=130)]
    assert apply(monitor)["changes"] == []
    monitor.clock.advance(2)
    result = apply(monitor)
    assert result["success"], result
    assert result["changes"][0]["after_gap"] is True
    assert monitor.store.get_holding_case(cases[0]["case_key"])["state"] == "resolved_external"


def test_cash_mmf_futu_excluded_and_fractional_funds_supported(monitor):
    monitor.storage.rows = [raw("cash", asset_type="cash", asset_id="USD-CASH"),
                            raw("futu", broker="富途"), raw("mmf", asset_id="CNY-MMF", asset_type="mmf"),
                            raw("fund", asset_type="otc_fund", quantity="1.25")]
    monitor.initialize(apply=True, confirm=True)
    assert set(monitor._state()["rows"]) == {"fund"}
    monitor.storage.rows[-1] = raw("fund", asset_type="otc_fund", quantity="1.5")
    assert apply(monitor)["changes"][0]["after"]["quantity"] == "1.5"


def test_identity_change_not_two_trades(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw(asset_id="OTHER", quantity=120)]
    result = apply(monitor)
    assert [item["kind"] for item in result["changes"]] == ["identity_changed"]


def test_failed_read_does_not_delete_or_advance(monitor):
    monitor.initialize(apply=True, confirm=True)
    state = monitor._state()
    monitor.storage.get_raw_holdings = Mock(side_effect=RuntimeError("incomplete page"))
    assert apply(monitor)["status"] == "failed"
    after = monitor._state()
    assert after["rows"] == state["rows"]
    assert after["observation_seq"] == state["observation_seq"]


def test_outbox_error_rolls_back_baseline_and_case(monitor, monkeypatch):
    monitor.initialize(apply=True, confirm=True)
    monitor.storage.rows = [raw(quantity=120)]
    before = monitor._state()
    monkeypatch.setattr("src.app.operation_state._holding_monitor_mixin.insert_operation_receipt_tx",
                        Mock(side_effect=RuntimeError("disk error")))
    assert apply(monitor)["status"] == "failed"
    after = monitor._state()
    assert after["rows"] == before["rows"]
    assert after["observation_seq"] == 0


def test_scope_change_requires_explicit_rebuild(monitor):
    monitor.initialize(apply=True, confirm=True)
    monitor.settings = MonitorSettings(("other",))
    assert apply(monitor)["status"] == "scope_changed"
    assert monitor.status()["status"] == "scope_changed"
    assert monitor.initialize(apply=True, confirm=True, rebuild=True)["status"] == "initialized"


def test_uninitialized_status_does_not_create_database(tmp_path):
    path = tmp_path / "absent.sqlite3"
    service = HoldingChangeMonitorService(settings=MonitorSettings(("example",)), target=("a", "b"), db_path=path)
    assert service.status()["status"] == "uninitialized"
    assert not path.exists()


def test_domain_duplicate_uses_all_rows_even_invalid_type():
    rows = [raw(), raw("r2", asset_type="cash", quantity=None)]
    result = observe(rows, {}, accounts=frozenset({"example"}), now="2026-11-06T10:00:00",
                     duplicate_groups=duplicate_holding_groups(rows), initialize=True)
    assert result["problems"][0]["reason"] == "duplicate_identity"
