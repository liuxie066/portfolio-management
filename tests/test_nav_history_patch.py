from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from src.app.nav_finality import evaluate_nav_finality
from src.maintenance.nav_history_repair import backfill, patch
from src.maintenance.nav_history_repair.common import (
    BASE_FIELDS,
    MAINTENANCE_FIELDS,
    FieldState,
    maintenance_details,
    nav_with_states,
)
from src.maintenance.nav_history_repair.context import NavRepairContext
from src.models import NAVHistory


def _nav(record_id: str, nav_date: date, *, nav: float = 1.0) -> NAVHistory:
    return NAVHistory(
        record_id=record_id,
        date=nav_date,
        account="lx",
        total_value=100.0,
        cash_value=20.0,
        stock_value=80.0,
        fund_value=0.0,
        cn_stock_value=80.0,
        us_stock_value=0.0,
        hk_stock_value=0.0,
        stock_weight=0.8,
        cash_weight=0.2,
        shares=100.0,
        nav=nav,
        cash_flow=0.0,
        share_change=0.0,
        pnl=0.0,
        details={"evidence_version": "legacy"},
    )


class FakeStorage:
    def __init__(self, navs, *, missing_fields=None):
        self.navs = list(navs)
        self.nav_history = self
        self.live_patch_attempts = 0
        self.full_write_attempts = 0
        self.fail_on_live_attempt = None
        missing_fields = missing_fields or {}
        self.states = {}
        for nav in self.navs:
            missing = set(missing_fields.get(nav.record_id, ()))
            field_states = {}
            for field in (*BASE_FIELDS, *MAINTENANCE_FIELDS):
                value = getattr(nav, field)
                if field in missing:
                    field_states[field] = FieldState.missing()
                elif value is None:
                    field_states[field] = FieldState.null()
                else:
                    field_states[field] = FieldState.valued(value)
            self.states[nav.record_id] = field_states
        self.patch_calls = []

    def read_nav_maintenance_rows(self, account):
        assert account == "lx"
        return [
            {
                "nav": nav,
                "record_id": nav.record_id,
                "date": nav.date,
                "field_states": {
                    field: state.envelope()
                    for field, state in self.states[nav.record_id].items()
                },
            }
            for nav in self.navs
        ]

    def patch_nav_maintenance_fields(self, record_id, field_states, dry_run=False):
        assert dry_run is False
        assert not (set(field_states) & set(BASE_FIELDS))
        self.live_patch_attempts += 1
        if self.live_patch_attempts == self.fail_on_live_attempt:
            raise RuntimeError(f"write failed at {record_id}")
        converted = {
            field: FieldState.from_envelope(envelope)
            for field, envelope in field_states.items()
        }
        self.patch_calls.append((record_id, converted))
        self.states[record_id].update(converted)
        for idx, nav in enumerate(self.navs):
            if nav.record_id == record_id:
                self.navs[idx] = nav_with_states(nav, converted)
                return {"record_id": record_id}
        raise AssertionError(f"unknown record: {record_id}")

    def write_nav_record(self, *_args, **_kwargs):
        self.full_write_attempts += 1
        raise AssertionError("maintenance must not call full write_nav_record")

    def write_nav_records(self, *_args, **_kwargs):
        self.full_write_attempts += 1
        raise AssertionError("maintenance must not call full write_nav_records")


def _args(**overrides):
    values = {
        "account": "lx",
        "patch_file": None,
        "mode": "strong-consistency-gap",
        "dry_run": False,
        "apply": False,
        "resume_journal": None,
        "rollback_journal": None,
        "backup_file": None,
        "no_validate": False,
        "validate_level": "basic",
        "validate_scope": "changed",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_context(monkeypatch, tmp_path, storage):
    context = NavRepairContext(
        account="lx",
        storage=storage,
        portfolio=SimpleNamespace(),
    )
    monkeypatch.setattr(patch, "create_nav_repair_context", lambda account=None: context)
    monkeypatch.setattr(patch.config, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    return context


def _install_recompute(monkeypatch, targets):
    def fake_recompute(*, observed, **_kwargs):
        payload = observed.nav.model_dump()
        payload.update(targets.get(observed.record_id, {}))
        return NAVHistory(**payload), SimpleNamespace()

    monkeypatch.setattr(patch, "recompute_derived_row", fake_recompute)


def _write_patch(tmp_path, rows):
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return patch_file


def _backfill_args(input_path, **overrides):
    values = {
        "account": "lx",
        "input": str(input_path),
        "d_from": None,
        "d_to": None,
        "mode": "replace",
        "allow_partial": False,
        "apply": False,
        "dry_run": True,
        "limit": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_patch_preflight_rejects_missing_or_duplicate_live_target(monkeypatch, tmp_path):
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-02"}])

    missing = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, missing)
    with pytest.raises(SystemExit, match="historical_evidence_required.*exactly one record"):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))
    assert missing.live_patch_attempts == 0

    duplicate = FakeStorage([
        _nav("rec-2a", date(2026, 1, 2)),
        _nav("rec-2b", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, duplicate)
    with pytest.raises(SystemExit, match="historical_evidence_required.*exactly one record"):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))
    assert duplicate.live_patch_attempts == 0


def test_patch_input_rejects_nonfinite_evidence_and_accepts_share_change_alias(
    monkeypatch,
    tmp_path,
):
    invalid_file = _write_patch(
        tmp_path,
        [{"date": "2026-01-01", "nav": "NaN"}],
    )
    with pytest.raises(ValueError, match="nav.*finite"):
        patch.load_patch_rows(str(invalid_file), "strong-consistency-gap")

    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-1": {"share_change": 5.0}})
    alias_file = _write_patch(
        tmp_path,
        [{"date": "2026-01-01", "share_change": 5.0}],
    )

    result = patch.run(_args(patch_file=str(alias_file), dry_run=True))

    assert result["success"] is True


@pytest.mark.parametrize(
    "navs,input_rows,mode,reason",
    [
        (
            [_nav("rec-1", date(2026, 1, 1))],
            [{"date": "2026-01-02"}],
            "upsert",
            "missing_target",
        ),
        (
            [
                _nav("rec-2a", date(2026, 1, 2)),
                _nav("rec-2b", date(2026, 1, 2)),
            ],
            [{"date": "2026-01-02"}],
            "replace",
            "duplicate_target",
        ),
        (
            [_nav("rec-1", date(2026, 1, 1))],
            [{"date": "2026-01-01", "total_value": 999.0}],
            "replace",
            "base_evidence_drift",
        ),
    ],
)
def test_backfill_historical_evidence_preflight_performs_zero_writes(
    monkeypatch,
    tmp_path,
    navs,
    input_rows,
    mode,
    reason,
):
    storage = FakeStorage(navs)
    context = NavRepairContext(
        account="lx",
        storage=storage,
        portfolio=SimpleNamespace(),
    )
    monkeypatch.setattr(
        backfill,
        "create_nav_repair_context",
        lambda account=None: context,
    )
    input_path = tmp_path / "backfill.json"
    input_path.write_text(json.dumps({"rows": input_rows}), encoding="utf-8")

    result = backfill.run(_backfill_args(input_path, mode=mode))

    assert result["success"] is False
    assert result["status"] == "historical_evidence_required"
    assert result["reasons"][0]["reason"] == reason
    assert result["write"] == {"would_write": 0, "written": 0}
    assert storage.live_patch_attempts == 0
    assert storage.full_write_attempts == 0


def test_backfill_rejects_ambiguous_predecessor_chain_before_recompute(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-prev-a", date(2026, 1, 1)),
        _nav("rec-prev-b", date(2026, 1, 1)),
        _nav("rec-target", date(2026, 1, 2)),
    ])
    context = NavRepairContext(
        account="lx",
        storage=storage,
        portfolio=SimpleNamespace(),
    )
    monkeypatch.setattr(
        backfill,
        "create_nav_repair_context",
        lambda account=None: context,
    )
    input_path = tmp_path / "backfill.json"
    input_path.write_text(
        json.dumps({"rows": [{"date": "2026-01-02"}]}),
        encoding="utf-8",
    )

    result = backfill.run(_backfill_args(input_path))

    assert result["success"] is False
    assert result["status"] == "historical_evidence_required"
    assert result["reasons"][0]["reason"] == (
        "maintenance_history_evidence_invalid"
    )
    assert "duplicate NAV dependency dates" in result["reasons"][0]["error"]
    assert result["write"] == {"would_write": 0, "written": 0}
    assert storage.live_patch_attempts == 0
    assert storage.full_write_attempts == 0


def test_patch_without_fresh_ledger_dataset_blocks(monkeypatch, tmp_path):
    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])
    monkeypatch.setattr(
        patch,
        "recompute_derived_row",
        lambda **_kwargs: (_ for _ in ()).throw(
            ValueError("cash-flow dataset unavailable")
        ),
    )

    with pytest.raises(SystemExit, match="historical_evidence_required.*dataset unavailable"):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))
    assert storage.live_patch_attempts == 0
    assert storage.full_write_attempts == 0


def test_maintenance_details_preserves_original_dataset_receipt():
    finality = {
        "version": 1,
        "status": "final",
        "nav_date": "2026-01-01",
        "valuation_as_of": "2026-01-01T16:00:00+08:00",
        "writer": "daily-nav-job",
        "write_reason": "daily_nav",
        "run_id": "valuation-run",
    }
    original = {
        "evidence_version": "legacy",
        "cash_flow_dataset": {"fetched_at": "original"},
        "finality": finality,
        "run_id": "valuation-run",
    }
    calculated = {
        "cash_flow_dataset": {
            "contract_version": "cash-flow-dataset.v1",
            "financial_fingerprint": "financial-1",
            "full_fingerprint": "full-1",
            "fetched_at": "new-run",
            "run_id": "repair-run",
        },
        "cash_flow_basis": {"version": 1},
        "finality": {
            "version": 1,
            "status": "maintenance",
            "nav_date": "2026-01-01",
            "valuation_as_of": None,
            "writer": "nav-repair",
            "write_reason": "nav_history_derived_repair",
            "run_id": "repair-run",
        },
        "run_id": "repair-run",
    }

    merged = maintenance_details(original, calculated)

    assert merged["cash_flow_dataset"] == {"fetched_at": "original"}
    assert merged["cash_flow_basis"] == {"version": 1}
    assert merged["finality"] == finality
    assert merged["run_id"] == "valuation-run"
    assert merged["maintenance_provenance"] == {
        "version": 1,
        "status": "maintenance",
        "nav_date": "2026-01-01",
        "valuation_as_of": None,
        "writer": "nav-repair",
        "write_reason": "nav_history_derived_repair",
        "run_id": "repair-run",
        "cash_flow_dataset": {
            "contract_version": "cash-flow-dataset.v1",
            "financial_fingerprint": "financial-1",
            "full_fingerprint": "full-1",
            "run_id": "repair-run",
        },
    }
    assert evaluate_nav_finality(
        merged,
        target_date=date(2026, 1, 1),
    ).eligible


def test_patch_preflight_rejects_incomplete_target_base_evidence(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage(
        [_nav("rec-1", date(2026, 1, 1))],
        missing_fields={"rec-1": {"fund_value"}},
    )
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-1": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])

    with pytest.raises(
        SystemExit,
        match="historical_evidence_required.*fund_value is missing",
    ):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))

    assert storage.live_patch_attempts == 0
    assert storage.full_write_attempts == 0


def test_patch_preflight_rejects_duplicate_predecessor_date(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-prev-a", date(2026, 1, 1)),
        _nav("rec-prev-b", date(2026, 1, 1)),
        _nav("rec-target", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-target": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-02"}])

    with pytest.raises(
        SystemExit,
        match="historical_evidence_required.*duplicate NAV dependency dates",
    ):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))

    assert storage.live_patch_attempts == 0
    assert storage.full_write_attempts == 0


def test_patch_preflight_rejects_incomplete_predecessor_evidence(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage(
        [
            _nav("rec-prev", date(2026, 1, 1)),
            _nav("rec-target", date(2026, 1, 2)),
        ],
        missing_fields={"rec-prev": {"shares"}},
    )
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-target": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-02"}])

    with pytest.raises(
        SystemExit,
        match="historical_evidence_required.*shares is missing",
    ):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))

    assert storage.live_patch_attempts == 0
    assert storage.full_write_attempts == 0


def test_changed_scope_blocks_when_canonical_successor_would_change(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(
        monkeypatch,
        {"rec-1": {"nav": 1.1}, "rec-2": {"share_change": 10.0}},
    )
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])

    result = patch.run(_args(patch_file=str(patch_file), dry_run=True))

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["validation_dates"] == ["2026-01-01", "2026-01-02"]
    assert result["violations"] == [{
        "date": "2026-01-02",
        "record_id": "rec-2",
        "error": "canonical_derived_mismatch",
        "fields": {
            "share_change": {
                "actual": {"state": "value", "value": 0.0},
                "expected": {"state": "value", "value": 10.0},
            }
        },
    }]
    assert storage.live_patch_attempts == 0


def test_changed_scope_apply_aborts_before_journal_when_successor_is_invalid(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(
        monkeypatch,
        {"rec-1": {"nav": 1.1}, "rec-2": {"share_change": 10.0}},
    )
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])

    with pytest.raises(SystemExit, match="validation errors"):
        patch.run(_args(patch_file=str(patch_file), apply=True))

    assert storage.live_patch_attempts == 0
    assert not (tmp_path / "data" / "nav_repair").exists()


def test_patch_apply_cas_rejects_base_field_drift_before_write(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-1": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])
    original_read = storage.read_nav_maintenance_rows
    read_count = 0

    def read_with_base_drift(account):
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            changed = FieldState.valued(101.0)
            storage.states["rec-1"]["total_value"] = changed
            storage.navs[0] = nav_with_states(
                storage.navs[0],
                {"total_value": changed},
            )
        return original_read(account)

    storage.read_nav_maintenance_rows = read_with_base_drift

    result = patch.run(_args(patch_file=str(patch_file), apply=True))

    assert result["status"] == "failed"
    assert result["failed"][0]["error"] == (
        "immutable base fields changed after planning"
    )
    assert storage.live_patch_attempts == 0


def test_patch_apply_cas_rejects_unchanged_derived_field_drift_before_write(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-1": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])
    original_read = storage.read_nav_maintenance_rows
    read_count = 0

    def read_with_derived_drift(account):
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            changed = FieldState.valued(999.0)
            storage.states["rec-1"]["mtd_pnl"] = changed
            storage.navs[0] = nav_with_states(
                storage.navs[0],
                {"mtd_pnl": changed},
            )
        return original_read(account)

    storage.read_nav_maintenance_rows = read_with_derived_drift

    result = patch.run(_args(patch_file=str(patch_file), apply=True))

    assert result["status"] == "failed"
    assert result["failed"][0]["error"] == (
        "current row matches neither complete original nor desired "
        "maintenance state"
    )
    assert storage.live_patch_attempts == 0


def test_patch_apply_cas_rejects_dependency_drift_before_write(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-prev", date(2026, 1, 1)),
        _nav("rec-target", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-target": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-02"}])
    original_read = storage.read_nav_maintenance_rows
    read_count = 0

    def read_with_dependency_drift(account):
        nonlocal read_count
        read_count += 1
        if read_count == 2:
            changed = FieldState.valued(101.0)
            storage.states["rec-prev"]["shares"] = changed
            storage.navs[0] = nav_with_states(
                storage.navs[0],
                {"shares": changed},
            )
        return original_read(account)

    storage.read_nav_maintenance_rows = read_with_dependency_drift

    result = patch.run(_args(patch_file=str(patch_file), apply=True))

    assert result["status"] == "failed"
    assert "NAV dependency evidence changed" in result["failed"][0]["error"]
    assert result["partial_write_possible"] is False
    assert storage.live_patch_attempts == 0


def test_patch_readback_rejects_unchanged_derived_field_drift(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-1": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])
    original_read = storage.read_nav_maintenance_rows
    read_count = 0

    def read_with_readback_drift(account):
        nonlocal read_count
        read_count += 1
        if read_count == 3:
            changed = FieldState.valued(999.0)
            storage.states["rec-1"]["mtd_pnl"] = changed
            storage.navs[0] = nav_with_states(
                storage.navs[0],
                {"mtd_pnl": changed},
            )
        return original_read(account)

    storage.read_nav_maintenance_rows = read_with_readback_drift

    result = patch.run(_args(patch_file=str(patch_file), apply=True))

    assert result["status"] == "partial"
    assert result["failed"][0]["error"] == (
        "fresh readback does not match complete desired maintenance state"
    )
    assert result["partial_write_possible"] is True
    assert storage.live_patch_attempts == 1


def test_patch_partial_apply_resumes_and_rolls_back_with_restricted_fields(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    targets = {"rec-1": {"nav": 1.1}, "rec-2": {"nav": 1.2}}
    _install_recompute(monkeypatch, targets)
    patch_file = _write_patch(tmp_path, [
        {"date": "2026-01-01"},
        {"date": "2026-01-02"},
    ])
    storage.fail_on_live_attempt = 2

    partial = patch.run(_args(patch_file=str(patch_file), apply=True))

    assert partial["status"] == "partial"
    assert [row["record_id"] for row in partial["applied"]] == ["rec-1"]
    assert [row["record_id"] for row in partial["failed"]] == ["rec-2"]
    assert [nav.nav for nav in storage.navs] == [1.1, 1.0]
    assert storage.full_write_attempts == 0
    journal_path = partial["journal_path"]
    with open(journal_path, "ab") as handle:
        handle.write(b'{"event":')

    storage.fail_on_live_attempt = None
    completed = patch.run(
        _args(patch_file=str(patch_file), resume_journal=journal_path)
    )

    assert completed["status"] == "completed"
    assert completed["failed"] == []
    assert [nav.nav for nav in storage.navs] == [1.1, 1.2]
    rolled_back = patch.run(_args(rollback_journal=journal_path, patch_file=None))
    assert rolled_back["status"] == "rolled_back"
    assert [nav.nav for nav in storage.navs] == [1.0, 1.0]
    assert storage.full_write_attempts == 0


def test_backfill_partial_apply_resumes_through_same_patch_plan_digest(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    context = _install_context(monkeypatch, tmp_path, storage)
    monkeypatch.setattr(
        backfill,
        "create_nav_repair_context",
        lambda account=None: context,
    )
    targets = {"rec-1": {"nav": 1.1}, "rec-2": {"nav": 1.2}}

    def fake_recompute(*, observed, **_kwargs):
        payload = observed.nav.model_dump()
        payload.update(targets.get(observed.record_id, {}))
        return NAVHistory(**payload), SimpleNamespace()

    monkeypatch.setattr(patch, "recompute_derived_row", fake_recompute)
    monkeypatch.setattr(backfill, "recompute_derived_row", fake_recompute)
    input_path = tmp_path / "backfill.json"
    input_path.write_text(
        json.dumps({
            "rows": [
                {"date": "2026-01-01"},
                {"date": "2026-01-02"},
            ]
        }),
        encoding="utf-8",
    )
    storage.fail_on_live_attempt = 2

    partial = backfill.run(
        _backfill_args(input_path, apply=True, dry_run=False)
    )

    assert partial["status"] == "partial"
    assert partial["write"]["status"] == "partial"
    journal_path = partial["write"]["journal_path"]
    planned = json.loads(open(journal_path, encoding="utf-8").readline())
    assert planned["plan_digest"] == partial["plan_digest"]
    storage.fail_on_live_attempt = None

    completed = patch.run(
        _args(
            patch_file=planned["patch_file"],
            resume_journal=journal_path,
        )
    )

    assert completed["status"] == "completed"
    assert completed["plan_digest"] == partial["plan_digest"]
    assert [nav.nav for nav in storage.navs] == [1.1, 1.2]


def test_patch_journal_preserves_missing_and_legacy_evidence(monkeypatch, tmp_path):
    original = _nav("rec-1", date(2026, 1, 1))
    original.pnl = None
    storage = FakeStorage([original], missing_fields={"rec-1": {"pnl"}})
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {
        "rec-1": {
            "pnl": 5.0,
            "details": {
                "evidence_version": "legacy",
                "cash_flow_basis": {"version": 1},
            },
        }
    })
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])

    completed = patch.run(_args(patch_file=str(patch_file), apply=True))
    planned = json.loads(open(completed["journal_path"], encoding="utf-8").readline())
    row = planned["rows"][0]
    assert row["original_fields"]["pnl"] == {"state": "missing"}
    assert row["target_fields"]["pnl"] == {"state": "value", "value": 5.0}
    assert storage.navs[0].details["evidence_version"] == "legacy"
    assert not (set(storage.patch_calls[0][1]) & set(BASE_FIELDS))

    patch.run(_args(rollback_journal=completed["journal_path"], patch_file=None))
    assert storage.states["rec-1"]["pnl"] == FieldState.missing()
    assert storage.navs[0].details == {"evidence_version": "legacy"}


def test_patch_apply_and_rollback_take_account_lock_before_journal_lock(
    monkeypatch,
    tmp_path,
):
    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(monkeypatch, {"rec-1": {"nav": 1.1}})
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01"}])
    locks = []

    @contextmanager
    def fake_lock(key):
        locks.append(("enter", key))
        try:
            yield
        finally:
            locks.append(("exit", key))

    monkeypatch.setattr(patch, "process_lock", fake_lock)

    completed = patch.run(_args(patch_file=str(patch_file), apply=True))
    patch.run(_args(rollback_journal=completed["journal_path"], patch_file=None))

    account_enters = [
        idx for idx, event in enumerate(locks)
        if event == ("enter", "account-write:lx")
    ]
    assert len(account_enters) == 2
    for account_idx in account_enters:
        later_journal = next(
            idx for idx, event in enumerate(locks[account_idx + 1 :], account_idx + 1)
            if event[0] == "enter" and str(event[1]).startswith("nav-repair:")
        )
        assert account_idx < later_journal


def test_patch_resume_rejects_changed_canonical_plan_digest(monkeypatch, tmp_path):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    targets = {"rec-1": {"nav": 1.1}, "rec-2": {"nav": 1.2}}
    _install_recompute(monkeypatch, targets)
    patch_file = _write_patch(tmp_path, [
        {"date": "2026-01-01"},
        {"date": "2026-01-02"},
    ])
    storage.fail_on_live_attempt = 2
    partial = patch.run(_args(patch_file=str(patch_file), apply=True))
    writes_before = storage.live_patch_attempts
    targets["rec-2"] = {"nav": 1.3}

    with pytest.raises(SystemExit, match="digest mismatch"):
        patch.run(
            _args(
                patch_file=str(patch_file),
                resume_journal=partial["journal_path"],
            )
        )
    assert storage.live_patch_attempts == writes_before


def test_patch_rollback_reports_partial_failure(monkeypatch, tmp_path):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    _install_recompute(
        monkeypatch,
        {"rec-1": {"nav": 1.1}, "rec-2": {"nav": 1.2}},
    )
    patch_file = _write_patch(tmp_path, [
        {"date": "2026-01-01"},
        {"date": "2026-01-02"},
    ])
    completed = patch.run(_args(patch_file=str(patch_file), apply=True))
    storage.fail_on_live_attempt = storage.live_patch_attempts + 2

    result = patch.run(
        _args(rollback_journal=completed["journal_path"], patch_file=None)
    )

    assert result["status"] == "rollback_partial"
    assert [row["record_id"] for row in result["rolled_back"]] == ["rec-2"]
    assert [row["record_id"] for row in result["rollback_failed"]] == ["rec-1"]
    assert [nav.nav for nav in storage.navs] == [1.1, 1.0]
