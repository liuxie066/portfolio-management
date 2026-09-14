from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.feishu.repositories.nav_history_repository import NavHistoryRepository
from src.maintenance.nav_history_repair import cash_restatement as repair
from src.maintenance.nav_history_repair.common import FieldState, FreshNavRow
from src.models import NAVHistory


def test_restatement_rejects_changed_fx_confirmation():
    evidence = {"financial_fingerprint": "same-ledger",
                "fx_confirmation_fingerprint": "original-fx", "effect_store_revision": "r1"}
    target = {"after": {"details": {"value": {
        "maintenance_provenance": {"cash_flow_dataset": evidence},
    }}}}
    dataset = SimpleNamespace(**evidence, complete=True)
    repair.verify_dataset(dataset, target)
    dataset.effect_store_revision = "new-scan-id"
    repair.verify_dataset(dataset, target)
    dataset.fx_confirmation_fingerprint = "changed-fx"
    with pytest.raises(ValueError, match="fx_confirmation_fingerprint"):
        repair.verify_dataset(dataset, target)
    dataset.complete = False
    with pytest.raises(ValueError, match="blocked"):
        repair.verify_dataset(dataset, target)


def test_cash_restatement_cas_resume_and_rollback(tmp_path, monkeypatch):
    nav = NAVHistory(record_id="r1", account="lx", date=date(2026, 9, 11),
                     cash_value=100, stock_value=50, total_value=150, shares=150, nav=1)
    before_row = FreshNavRow(nav, {k: FieldState.valued(getattr(nav, k)) for k in repair.FIELDS})
    adjusted, delta = repair.cash_target(before_row, 100, ".86384")
    assert delta == "-13.62"
    assert adjusted.nav.cash_value == 86.38
    assert adjusted.nav.total_value == 136.38
    for bad in ("NaN", "Infinity", "0", "-1"):
        with pytest.raises(ValueError):
            repair.cash_target(before_row, 100, bad)

    live = {"row": before_row}
    context = SimpleNamespace(account="lx")
    monkeypatch.setattr(repair, "read_fresh_nav_rows", lambda _: [live["row"]])
    monkeypatch.setattr(repair, "verify_calculation", lambda *_: None)
    monkeypatch.setattr("src.feishu.repositories.nav_history_repository.process_lock", lambda _: nullcontext())
    repo = NavHistoryRepository(SimpleNamespace())
    repo.read_nav_maintenance_rows = lambda _: [{
        "record_id": "r1", "field_states": repair.states(live["row"]),
    }]

    def write(record_id, values, **kwargs):
        assert record_id == "r1"
        current = live["row"]
        states = dict(current.field_states)
        states.update({k: FieldState.valued(v) for k, v in values.items()})
        live["row"] = FreshNavRow(current.nav.model_copy(update=values), states)

    repo._patch_nav_fields = Mock(side_effect=write)
    context.storage = SimpleNamespace(nav_history=repo)
    target = {"record_id": "r1", "date": "2026-09-11",
              "before": repair.states(before_row), "after": repair.states(adjusted)}
    plan = {"targets": [target], "before_series": repair.series([before_row])}

    invalid = dict(target["after"])
    invalid["stock_value"] = {"state": "value", "value": 51}
    with pytest.raises(ValueError, match="non-cash"):
        repo.patch_nav_cash_restatement(account="lx", record_id="r1",
                                       expected=target["before"], target=invalid)
    invalid = dict(target["after"])
    invalid["total_value"] = {"state": "value", "value": 150}
    with pytest.raises(ValueError, match="deltas"):
        repo.patch_nav_cash_restatement(account="lx", record_id="r1",
                                       expected=target["before"], target=invalid)
    repo._patch_nav_fields.assert_not_called()

    # Simulate transport failure AFTER the remote write. Resume must not double apply.
    def uncertain_write(*args, **kwargs):
        write(*args, **kwargs)
        raise RuntimeError("connection lost after write")

    repo._patch_nav_fields.side_effect = uncertain_write
    journal = tmp_path / "journal.jsonl"
    with pytest.raises(RuntimeError, match="connection lost"):
        repair.execute(context, plan, journal)
    assert repair.states(live["row"]) == target["after"]
    repo._patch_nav_fields.side_effect = write
    repair.execute(context, plan, journal)
    assert repo._patch_nav_fields.call_count == 1
    repair.execute(context, plan, journal, rollback=True)
    assert repair.states(live["row"]) == target["before"]
    assert repo._patch_nav_fields.call_count == 2

    # An unrelated concurrent edit must not be overwritten by apply or rollback.
    live["row"] = FreshNavRow(nav.model_copy(update={"stock_value": 51}), {
        **before_row.field_states, "stock_value": FieldState.valued(51),
    })
    with pytest.raises(ValueError, match="CAS conflict"):
        repair.execute(context, plan, journal)
    assert repo._patch_nav_fields.call_count == 2
