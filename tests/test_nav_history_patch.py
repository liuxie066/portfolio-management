from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from src.maintenance.nav_history_repair import patch
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
        shares=100.0,
        nav=nav,
        cash_flow=0.0,
        share_change=0.0,
        pnl=0.0,
    )


class FakeStorage:
    def __init__(self, navs):
        self.navs = list(navs)
        self.live_write_attempts = 0
        self.fail_on_live_attempt = None

    def get_nav_history(self, account, days=9999):
        assert account == "lx"
        return list(self.navs)

    def write_nav_record(self, nav, overwrite_existing=True, dry_run=False):
        assert overwrite_existing is True
        if dry_run:
            return {"record_id": nav.record_id}
        self.live_write_attempts += 1
        if self.live_write_attempts == self.fail_on_live_attempt:
            raise RuntimeError(f"write failed at {nav.record_id}")
        for idx, existing in enumerate(self.navs):
            if existing.record_id == nav.record_id:
                self.navs[idx] = nav
                return None
        raise AssertionError(f"unknown record: {nav.record_id}")


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
        "no_validate": True,
        "validate_level": "basic",
        "validate_scope": "changed",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _install_context(monkeypatch, tmp_path, storage):
    context = NavRepairContext(account="lx", storage=storage, portfolio=SimpleNamespace())
    monkeypatch.setattr(patch, "create_nav_repair_context", lambda account=None: context)
    monkeypatch.setattr(patch.config, "get_data_dir", lambda: tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    return context


def _write_patch(tmp_path, rows):
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return patch_file


def test_patch_preflight_rejects_missing_or_duplicate_live_target(monkeypatch, tmp_path):
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-02", "nav": 1.1}])

    missing = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, missing)
    with pytest.raises(SystemExit, match="exactly one record"):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))
    assert missing.live_write_attempts == 0

    duplicate = FakeStorage([
        _nav("rec-2a", date(2026, 1, 2)),
        _nav("rec-2b", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, duplicate)
    with pytest.raises(SystemExit, match="exactly one record"):
        patch.run(_args(patch_file=str(patch_file), dry_run=True))
    assert duplicate.live_write_attempts == 0


def test_patch_changed_scope_validates_first_successor(monkeypatch, tmp_path):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
        _nav("rec-3", date(2026, 1, 3)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01", "nav": 1.1}])
    validated = []
    monkeypatch.setattr(
        patch,
        "validate_math",
        lambda **kwargs: validated.append(kwargs["candidate"].date) or [],
    )

    result = patch.run(
        _args(
            patch_file=str(patch_file),
            dry_run=True,
            no_validate=False,
            validate_scope="changed",
        )
    )

    assert result["success"] is True
    assert validated == [date(2026, 1, 1), date(2026, 1, 2)]
    assert result["validation_dates"] == ["2026-01-01", "2026-01-02"]


def test_patch_partial_apply_resumes_and_rolls_back(monkeypatch, tmp_path):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    patch_file = _write_patch(tmp_path, [
        {"date": "2026-01-01", "nav": 1.1},
        {"date": "2026-01-02", "nav": 1.2},
    ])
    storage.fail_on_live_attempt = 2

    partial = patch.run(_args(patch_file=str(patch_file), apply=True))

    assert partial["status"] == "partial"
    assert [row["record_id"] for row in partial["applied"]] == ["rec-1"]
    assert [row["record_id"] for row in partial["failed"]] == ["rec-2"]
    assert storage.navs[0].nav == 1.1
    assert storage.navs[1].nav == 1.0
    journal_path = partial["journal_path"]
    assert journal_path.startswith(str(tmp_path / "data" / "nav_repair"))
    assert "--resume-journal" in partial["resume_command"]
    assert "--rollback-journal" in partial["rollback_command"]
    with open(journal_path, "ab") as handle:
        handle.write(b'{"event":')

    storage.fail_on_live_attempt = None
    completed = patch.run(
        _args(
            patch_file=str(patch_file),
            resume_journal=journal_path,
        )
    )

    assert completed["status"] == "completed"
    assert completed["failed"] == []
    assert completed["pending"] == []
    assert [nav.nav for nav in storage.navs] == [1.1, 1.2]

    rolled_back = patch.run(_args(rollback_journal=journal_path, patch_file=None))

    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["success"] is True
    assert [nav.nav for nav in storage.navs] == [1.0, 1.0]
    journal_states = [json.loads(line).get("state") for line in open(journal_path, encoding="utf-8")]
    assert journal_states == ["PLANNED", "APPLYING", None, None, "PARTIAL", "APPLYING", None, "COMPLETED", "ROLLING_BACK", None, None, "ROLLED_BACK"]


def test_patch_apply_and_rollback_take_account_lock_before_journal_lock(monkeypatch, tmp_path):
    storage = FakeStorage([_nav("rec-1", date(2026, 1, 1))])
    _install_context(monkeypatch, tmp_path, storage)
    patch_file = _write_patch(tmp_path, [{"date": "2026-01-01", "nav": 1.1}])
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

    account_enters = [idx for idx, event in enumerate(locks) if event == ("enter", "account-write:lx")]
    journal_enters = [
        idx
        for idx, event in enumerate(locks)
        if event[0] == "enter" and str(event[1]).startswith("nav-repair:")
    ]
    assert len(account_enters) == 2
    assert account_enters[0] < journal_enters[1]
    assert account_enters[1] < journal_enters[2]


def test_patch_resume_rejects_changed_plan_digest(monkeypatch, tmp_path):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    patch_file = _write_patch(tmp_path, [
        {"date": "2026-01-01", "nav": 1.1},
        {"date": "2026-01-02", "nav": 1.2},
    ])
    storage.fail_on_live_attempt = 2
    partial = patch.run(_args(patch_file=str(patch_file), apply=True))
    writes_before = storage.live_write_attempts
    _write_patch(tmp_path, [
        {"date": "2026-01-01", "nav": 1.1},
        {"date": "2026-01-02", "nav": 1.3},
    ])

    with pytest.raises(SystemExit, match="digest mismatch"):
        patch.run(
            _args(
                patch_file=str(patch_file),
                resume_journal=partial["journal_path"],
            )
        )

    assert storage.live_write_attempts == writes_before


def test_patch_rollback_reports_partial_failure(monkeypatch, tmp_path):
    storage = FakeStorage([
        _nav("rec-1", date(2026, 1, 1)),
        _nav("rec-2", date(2026, 1, 2)),
    ])
    _install_context(monkeypatch, tmp_path, storage)
    patch_file = _write_patch(tmp_path, [
        {"date": "2026-01-01", "nav": 1.1},
        {"date": "2026-01-02", "nav": 1.2},
    ])
    completed = patch.run(_args(patch_file=str(patch_file), apply=True))
    storage.fail_on_live_attempt = storage.live_write_attempts + 2

    result = patch.run(_args(rollback_journal=completed["journal_path"], patch_file=None))

    assert result["status"] == "rollback_partial"
    assert result["success"] is False
    assert [row["record_id"] for row in result["rolled_back"]] == ["rec-2"]
    assert [row["record_id"] for row in result["rollback_failed"]] == ["rec-1"]
    assert [nav.nav for nav in storage.navs] == [1.1, 1.0]
