#!/usr/bin/env python3
"""Patch Feishu nav_history through canonical derived-only maintenance.

This module contains the implementation behind
``scripts/nav_history_repair.py patch``.

Design goals
- Never overwrite non-target fields with model defaults (e.g., cash_value/stock_value becoming 0).
- Two-phase workflow: dry-run diff -> apply.
- Canonical NAV invariants are mandatory; abort apply if any invariant fails.

Typical usage
  ./.venv/bin/python scripts/nav_history_repair.py patch \
    --account lx \
    --patch-file audit/rebuild_strong_consistency_lx.json \
    --mode strong-consistency-gap \
    --dry-run

  ./.venv/bin/python scripts/nav_history_repair.py patch \
    --account lx \
    --patch-file audit/rebuild_strong_consistency_lx.json \
    --mode strong-consistency-gap \
    --apply

Patch file format
- Accepts JSON with either:
  - {"rebuilt": [ {"date": "YYYY-MM-DD", ... } ]}
  - {"rows": [ ... ]}
- For strong-consistency-gap mode we look for keys:
  gap_cash_flow, gap_share_change, shares, nav, pnl,
  mtd_nav_change, ytd_nav_change, mtd_pnl, ytd_pnl

Notes
- We patch only the maintenance whitelist through FieldState envelopes.
- Fresh base facts remain immutable CAS evidence and are never written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from src import config
from src.domain.nav_calculator import NavCalculator
from src.maintenance.nav_history_repair.common import (
    BASE_FIELDS,
    FieldState,
    FreshNavRow,
    MAINTENANCE_FIELDS,
    assert_maintenance_history_evidence,
    changed_states,
    maintenance_dependency_evidence,
    maintenance_target_states,
    read_fresh_nav_rows,
    recompute_derived_row,
    restricted_patch,
    rows_by_date,
    state_subset,
)
from src.maintenance.nav_history_repair.context import NavRepairContext, create_nav_repair_context
from src.models import NAVHistory
from src.process_lock import account_lock_key, process_lock


def _iso_to_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _money_equal(a: Optional[float], b: Optional[float]) -> bool:
    return NavCalculator.money_equal(a, b)


def _nav_equal(a: Optional[float], b: Optional[float]) -> bool:
    return NavCalculator.nav_equal(a, b)


def _optional_finite_float(value: Any, *, field: str) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid expected {field}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"invalid expected {field}: value must be finite")
    return parsed


@dataclass
class PatchRow:
    d: date
    # desired replacements (None means "do not patch")
    cash_flow: Optional[float] = None
    share_change: Optional[float] = None
    shares: Optional[float] = None
    nav: Optional[float] = None
    pnl: Optional[float] = None
    mtd_nav_change: Optional[float] = None
    ytd_nav_change: Optional[float] = None
    mtd_pnl: Optional[float] = None
    ytd_pnl: Optional[float] = None
    gap_cash_flow: Optional[float] = None
    provided_fields: frozenset[str] = frozenset()


def load_patch_rows(patch_file: str, mode: str) -> List[PatchRow]:
    data = json.loads(Path(patch_file).read_text(encoding="utf-8"))
    rows = data.get("rebuilt") or data.get("rows")
    if not isinstance(rows, list):
        raise ValueError("patch-file must contain a list under 'rebuilt' or 'rows'")
    if not rows:
        raise ValueError("patch-file contains no rows")

    out: List[PatchRow] = []
    for r in rows:
        d = _iso_to_date(r["date"]) if isinstance(r.get("date"), str) else _iso_to_date(str(r.get("date")))

        if mode == "strong-consistency-gap":
            provided = frozenset(str(key) for key in r)
            out.append(
                PatchRow(
                    d=d,
                    cash_flow=_optional_finite_float(
                        r.get("cash_flow"),
                        field="cash_flow",
                    ) if r.get("cash_flow") is not None else (
                        _optional_finite_float(
                            r.get("daily_cash_flow"),
                            field="daily_cash_flow",
                        ) if r.get("daily_cash_flow") is not None else None
                    ),
                    share_change=_optional_finite_float(
                        (
                            r.get("gap_share_change")
                            if r.get("gap_share_change") is not None
                            else r.get("share_change")
                        ),
                        field="share_change",
                    ),
                    shares=_optional_finite_float(r.get("shares"), field="shares"),
                    nav=_optional_finite_float(r.get("nav"), field="nav"),
                    pnl=_optional_finite_float(r.get("pnl"), field="pnl"),
                    mtd_nav_change=_optional_finite_float(
                        r.get("mtd_nav_change"),
                        field="mtd_nav_change",
                    ),
                    ytd_nav_change=_optional_finite_float(
                        r.get("ytd_nav_change"),
                        field="ytd_nav_change",
                    ),
                    mtd_pnl=_optional_finite_float(r.get("mtd_pnl"), field="mtd_pnl"),
                    ytd_pnl=_optional_finite_float(r.get("ytd_pnl"), field="ytd_pnl"),
                    gap_cash_flow=_optional_finite_float(
                        r.get("gap_cash_flow"),
                        field="gap_cash_flow",
                    ),
                    provided_fields=provided,
                )
            )
        else:
            raise ValueError(f"unsupported mode: {mode}")

    dates = [p.d for p in out]
    duplicates = sorted({d for d in dates if dates.count(d) > 1})
    if duplicates:
        raise ValueError(f"patch-file contains duplicate dates: {[d.isoformat() for d in duplicates]}")
    return sorted(out, key=lambda row: row.d)


PATCH_FIELDS = [
    "cash_flow",
    "share_change",
    "shares",
    "nav",
    "pnl",
    "mtd_nav_change",
    "ytd_nav_change",
    "mtd_pnl",
    "ytd_pnl",
]
VALIDATION_FIELDS = tuple(
    field for field in MAINTENANCE_FIELDS if field != "details"
)


def canonical_plan_digest(
    *,
    account: str,
    mode: str,
    rows: List[Dict[str, Any]],
    dependency_rows: List[Dict[str, Any]],
) -> str:
    """Hash the complete immutable and desired facts for one repair plan."""

    payload = {
        "account": account,
        "mode": mode,
        "rows": rows,
        "dependency_rows": dependency_rows,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _states_from_envelopes(fields: Mapping[str, Mapping[str, Any]]) -> Dict[str, FieldState]:
    return {
        field: FieldState.from_envelope(envelope)
        for field, envelope in fields.items()
    }


def _same_row_states(row: FreshNavRow, fields: Dict[str, Any]) -> bool:
    expected = _states_from_envelopes(fields)
    return state_subset(row, expected) == expected


def _base_state_envelopes(row: FreshNavRow) -> Dict[str, Dict[str, Any]]:
    return {
        field: state.envelope()
        for field, state in state_subset(row, BASE_FIELDS).items()
    }


def _requested_value(patch: PatchRow, field: str) -> tuple[bool, Any]:
    aliases = {
        "cash_flow": ("cash_flow", "daily_cash_flow"),
        "share_change": ("share_change", "gap_share_change"),
    }
    keys = aliases.get(field, (field,))
    if not any(key in patch.provided_fields for key in keys):
        return False, None
    return True, getattr(patch, field)


def _assert_requested_derived_evidence(patch: PatchRow, candidate: NAVHistory) -> None:
    errors = []
    for field in PATCH_FIELDS:
        provided, expected = _requested_value(patch, field)
        if not provided:
            continue
        actual = getattr(candidate, field)
        equal = (
            _nav_equal(actual, expected)
            if field in {"nav", "mtd_nav_change", "ytd_nav_change"}
            else _money_equal(actual, expected)
        )
        if not equal:
            errors.append(f"{field}: expected={expected}, canonical={actual}")
    if "gap_cash_flow" in patch.provided_fields:
        basis = (candidate.details or {}).get("cash_flow_basis") or {}
        actual_gap = basis.get("gap_cash_flow")
        if not _money_equal(actual_gap, patch.gap_cash_flow):
            errors.append(
                f"gap_cash_flow: expected={patch.gap_cash_flow}, canonical={actual_gap}"
            )
    if errors:
        raise SystemExit(
            "historical_evidence_required: requested derived evidence does not "
            "match the canonical ledger calculation: " + " | ".join(errors)
        )


def _resolve_patch_targets(
    *,
    context: NavRepairContext,
    fresh_rows: List[FreshNavRow],
    patches: List[PatchRow],
    account: str,
    mode: str,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[NAVHistory],
    List[Dict[str, Any]],
    str,
]:
    by_date = rows_by_date(fresh_rows)
    working_navs = [row.nav for row in fresh_rows]

    errors = []
    for patch in patches:
        matches = by_date.get(patch.d) or []
        if len(matches) != 1:
            errors.append({"date": patch.d.isoformat(), "match_count": len(matches)})
        elif not matches[0].record_id:
            errors.append({
                "date": patch.d.isoformat(),
                "match_count": 1,
                "error": "missing record_id",
            })
    if errors:
        raise SystemExit(
            "historical_evidence_required: every target date must resolve to "
            f"exactly one record: {errors}"
        )
    try:
        assert_maintenance_history_evidence(
            fresh_rows,
            account=account,
            target_dates=(patch.d for patch in patches),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    rows = []
    diffs = []
    digest_targets = []
    for patch in patches:
        observed = by_date[patch.d][0]
        existing = observed.nav
        run_id = f"nav-repair:{account}:{patch.d.isoformat()}"
        try:
            candidate, _dataset = recompute_derived_row(
                context=context,
                observed=observed,
                working_navs=working_navs,
                run_id=run_id,
            )
        except Exception as exc:
            raise SystemExit(
                f"historical_evidence_required: {patch.d.isoformat()}: {exc}"
            ) from exc
        _assert_requested_derived_evidence(patch, candidate)
        for field in BASE_FIELDS:
            if getattr(existing, field) != getattr(candidate, field):
                raise SystemExit(f"safety abort: non-target field changed: {patch.d} {field}")
        original_states, target_states = changed_states(observed, candidate)
        desired_states = maintenance_target_states(observed, candidate)
        complete_original_states = state_subset(observed, MAINTENANCE_FIELDS)
        base_fields = _base_state_envelopes(observed)
        original_fields = {
            field: state.envelope() for field, state in original_states.items()
        }
        target_fields = {
            field: state.envelope() for field, state in target_states.items()
        }
        changes = {
            field: {"old": original_fields[field], "new": target_fields[field]}
            for field in target_fields
        }
        rows.append({
            "date": patch.d.isoformat(),
            "record_id": observed.record_id,
            "base_fields": base_fields,
            "original_fields": original_fields,
            "target_fields": target_fields,
            "original_maintenance_fields": {
                field: state.envelope()
                for field, state in complete_original_states.items()
            },
            "desired_maintenance_fields": {
                field: state.envelope()
                for field, state in desired_states.items()
            },
            "status": "pending",
        })
        diffs.append({"date": patch.d.isoformat(), "record_id": observed.record_id, "changes": changes})
        digest_targets.append({
            "date": patch.d.isoformat(),
            "record_id": observed.record_id,
            "base_fields": base_fields,
            "target_fields": {
                field: state.envelope()
                for field, state in desired_states.items()
            },
        })
        working_navs = [
            candidate if nav.record_id == candidate.record_id else nav
            for nav in working_navs
        ]

    dependency_rows = maintenance_dependency_evidence(
        fresh_rows,
        target_dates=(patch.d for patch in patches),
    )

    plan_digest = canonical_plan_digest(
        account=account,
        mode=mode,
        rows=digest_targets,
        dependency_rows=dependency_rows,
    )
    return rows, diffs, working_navs, dependency_rows, plan_digest


def _validation_dates(
    *,
    series: List[NAVHistory],
    rows: List[Dict[str, Any]],
    validate_scope: str,
) -> set[date]:
    patched_dates = {_iso_to_date(row["date"]) for row in rows}
    changed_dates = {
        _iso_to_date(row["date"])
        for row in rows
        if row["original_fields"] != row["target_fields"]
    }
    if validate_scope == "all":
        return {nav.date for nav in series}
    if validate_scope == "patched":
        return patched_dates

    selected = set(changed_dates)
    ordered_dates = sorted({nav.date for nav in series})
    positions = {nav_date: idx for idx, nav_date in enumerate(ordered_dates)}
    for changed_date in changed_dates:
        idx = positions.get(changed_date)
        if idx is not None and idx + 1 < len(ordered_dates):
            selected.add(ordered_dates[idx + 1])
    return selected


def _canonical_validation_violations(
    *,
    context: NavRepairContext,
    fresh_rows: List[FreshNavRow],
    series: List[NAVHistory],
    rows: List[Dict[str, Any]],
    validation_dates: set[date],
) -> List[Dict[str, Any]]:
    """Validate non-target rows against the canonical post-patch chain."""

    target_dates = {_iso_to_date(row["date"]) for row in rows}
    by_date = rows_by_date(fresh_rows)
    violations: List[Dict[str, Any]] = []
    for validation_date in sorted(validation_dates - target_dates):
        matches = by_date.get(validation_date) or []
        if len(matches) != 1:
            violations.append({
                "date": validation_date.isoformat(),
                "error": "validation_target_not_unique",
                "match_count": len(matches),
                "record_ids": [match.record_id for match in matches],
            })
            continue
        observed = matches[0]
        try:
            candidate, _dataset = recompute_derived_row(
                context=context,
                observed=observed,
                working_navs=series,
                run_id=(
                    f"nav-repair-validation:{context.account}:"
                    f"{validation_date.isoformat()}"
                ),
            )
        except Exception as exc:
            violations.append({
                "date": validation_date.isoformat(),
                "record_id": observed.record_id,
                "error": "canonical_validation_unavailable",
                "detail": str(exc),
            })
            continue

        expected = maintenance_target_states(observed, candidate)
        actual = state_subset(observed, VALIDATION_FIELDS)
        mismatches = {
            field: {
                "actual": actual[field].envelope(),
                "expected": expected[field].envelope(),
            }
            for field in VALIDATION_FIELDS
            if actual[field] != expected[field]
        }
        if mismatches:
            violations.append({
                "date": validation_date.isoformat(),
                "record_id": observed.record_id,
                "error": "canonical_derived_mismatch",
                "fields": mismatches,
            })
    return violations


def _append_journal(path: Path, event: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open("r+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.seek(0)
                    data = handle.read()
                    handle.truncate(data.rfind(b"\n") + 1)
                    handle.flush()
                    os.fsync(handle.fileno())
    line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _read_journal(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"journal not found: {path}")
    events = []
    data = path.read_bytes()
    raw_lines = data.split(b"\n")
    for line_number, raw_line in enumerate(raw_lines, start=1):
        if not raw_line.strip():
            continue
        try:
            line = raw_line.decode("utf-8")
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if line_number == len(raw_lines) and not data.endswith(b"\n"):
                break
            raise SystemExit(f"invalid journal JSON at line {line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise SystemExit(f"invalid journal event at line {line_number}")
        events.append(event)
    if not events or events[0].get("state") != "PLANNED" or not events[0].get("rows"):
        raise SystemExit(f"invalid nav repair journal: {path}")

    plan = dict(events[0])
    raw_rows = plan["rows"]
    if not isinstance(plan.get("dependency_rows"), list):
        raise SystemExit(
            "invalid nav repair journal: dependency evidence is required"
        )
    record_ids = [str(row.get("record_id") or "") for row in raw_rows]
    dates = [str(row.get("date") or "") for row in raw_rows]
    if (
        any(not record_id for record_id in record_ids)
        or len(set(record_ids)) != len(record_ids)
        or any(not item for item in dates)
        or len(set(dates)) != len(dates)
    ):
        raise SystemExit(f"invalid nav repair journal row identity: {path}")
    for row in raw_rows:
        if not isinstance(row.get("base_fields"), dict):
            raise SystemExit(
                "invalid nav repair journal: immutable base field evidence is required"
            )
        if set(row["base_fields"]) != set(BASE_FIELDS):
            raise SystemExit(
                "invalid nav repair journal: base field evidence is incomplete"
            )
        if not isinstance(row.get("original_fields"), dict) or not isinstance(
            row.get("target_fields"),
            dict,
        ):
            raise SystemExit(f"invalid nav repair journal field states: {path}")
        original_complete = row.get("original_maintenance_fields")
        desired_complete = row.get("desired_maintenance_fields")
        if (
            not isinstance(original_complete, dict)
            or set(original_complete) != set(MAINTENANCE_FIELDS)
            or not isinstance(desired_complete, dict)
            or set(desired_complete) != set(MAINTENANCE_FIELDS)
        ):
            raise SystemExit(
                "invalid nav repair journal: complete maintenance states are required"
            )
        changed_fields = set(row["original_fields"])
        if changed_fields != set(row["target_fields"]) or not changed_fields.issubset(
            MAINTENANCE_FIELDS
        ):
            raise SystemExit(
                "invalid nav repair journal: changed maintenance fields are inconsistent"
            )
        for state_group in (
            row["base_fields"],
            row["original_fields"],
            row["target_fields"],
            original_complete,
            desired_complete,
        ):
            try:
                _states_from_envelopes(state_group)
            except (AttributeError, TypeError, ValueError) as exc:
                raise SystemExit(
                    f"invalid nav repair journal field envelope: {exc}"
                ) from exc
        if any(
            row["original_fields"][field] != original_complete[field]
            or row["target_fields"][field] != desired_complete[field]
            or original_complete[field] == desired_complete[field]
            for field in changed_fields
        ) or any(
            original_complete[field] != desired_complete[field]
            for field in set(MAINTENANCE_FIELDS) - changed_fields
        ):
            raise SystemExit(
                "invalid nav repair journal: changed and complete states disagree"
            )
    for dependency in plan["dependency_rows"]:
        if (
            not isinstance(dependency, dict)
            or not str(dependency.get("record_id") or "").strip()
            or not str(dependency.get("date") or "").strip()
            or set(dependency.get("fields") or {}) != {"total_value", "shares", "nav"}
        ):
            raise SystemExit(
                "invalid nav repair journal: dependency row evidence is incomplete"
            )
        try:
            _states_from_envelopes(dependency["fields"])
        except (AttributeError, TypeError, ValueError) as exc:
            raise SystemExit(
                f"invalid nav repair journal dependency envelope: {exc}"
            ) from exc
    dependency_ids = [
        str(item["record_id"])
        for item in plan["dependency_rows"]
    ]
    dependency_dates = [
        str(item["date"])
        for item in plan["dependency_rows"]
    ]
    if (
        len(set(dependency_ids)) != len(dependency_ids)
        or len(set(dependency_dates)) != len(dependency_dates)
        or any(item.get("account") != plan.get("account") for item in plan["dependency_rows"])
    ):
        raise SystemExit(
            "invalid nav repair journal: dependency identity is inconsistent"
        )
    digest_rows = [
        {
            "date": row["date"],
            "record_id": row["record_id"],
            "base_fields": row["base_fields"],
            "target_fields": row["desired_maintenance_fields"],
        }
        for row in raw_rows
    ]
    expected_digest = canonical_plan_digest(
        account=str(plan.get("account") or ""),
        mode=str(plan.get("mode") or ""),
        rows=digest_rows,
        dependency_rows=plan["dependency_rows"],
    )
    if plan.get("plan_digest") != expected_digest:
        raise SystemExit("invalid nav repair journal: plan digest mismatch")
    rows = {
        str(row["record_id"]): {
            **row,
            "status": "pending",
            "error": None,
            "write_attempted": False,
        }
        for row in raw_rows
    }
    state = "PLANNED"
    for event in events[1:]:
        if event.get("event") == "STATE":
            state = str(event.get("state") or state)
        elif event.get("event") == "ROW":
            row = rows.get(str(event.get("record_id")))
            if row is None:
                raise SystemExit(f"journal row event references unknown record_id: {event.get('record_id')}")
            row["status"] = str(event.get("status") or row["status"])
            row["error"] = event.get("error")
            if "write_attempted" in event:
                row["write_attempted"] = bool(event["write_attempted"])
    plan["state"] = state
    plan["rows"] = list(rows.values())
    return plan


def _commands(plan: Dict[str, Any], journal_path: Path) -> Tuple[str, str]:
    parts = [
        "python",
        "scripts/nav_history_repair.py",
        "patch",
        "--account",
        str(plan["account"]),
        "--patch-file",
        str(plan["patch_file"]),
        "--mode",
        str(plan["mode"]),
        "--resume-journal",
        str(journal_path),
    ]
    resume = " ".join(shlex.quote(part) for part in parts)
    rollback = " ".join(
        shlex.quote(part)
        for part in [
            "python",
            "scripts/nav_history_repair.py",
            "patch",
            "--rollback-journal",
            str(journal_path),
        ]
    )
    return resume, rollback


def _result(plan: Dict[str, Any], journal_path: Path, status: str, **extra: Any) -> Dict[str, Any]:
    rows = plan["rows"]
    resume_command, rollback_command = _commands(plan, journal_path)
    result = {
        "success": status in {"completed", "rolled_back"},
        "status": status,
        "plan_digest": plan["plan_digest"],
        "journal_path": str(journal_path),
        "applied": [row for row in rows if row["status"] == "applied"],
        "failed": [row for row in rows if row["status"] == "failed"],
        "pending": [row for row in rows if row["status"] == "pending"],
        "rolled_back": [row for row in rows if row["status"] == "rolled_back"],
        "rollback_failed": [row for row in rows if row["status"] == "rollback_failed"],
        "partial_write_possible": any(
            row.get("write_attempted") and row["status"] == "failed"
            for row in rows
        ),
        "resume_command": resume_command,
        "rollback_command": rollback_command,
    }
    result.update(extra)
    return result


def _apply_failure_status(plan: Dict[str, Any]) -> str:
    return (
        "partial"
        if any(
            row["status"] == "applied" or row.get("write_attempted")
            for row in plan["rows"]
        )
        else "failed"
    )


def _current_rows(context: NavRepairContext, plan: Dict[str, Any]) -> Dict[str, FreshNavRow]:
    fresh_rows = read_fresh_nav_rows(context)
    target_dates = [_iso_to_date(row["date"]) for row in plan["rows"]]
    try:
        assert_maintenance_history_evidence(
            fresh_rows,
            account=context.account,
            target_dates=target_dates,
        )
    except ValueError as exc:
        raise RuntimeError(f"journal dependency preflight failed: {exc}") from exc
    actual_dependencies = maintenance_dependency_evidence(
        fresh_rows,
        target_dates=target_dates,
    )
    if actual_dependencies != plan["dependency_rows"]:
        raise RuntimeError(
            "journal dependency preflight failed: NAV dependency evidence changed"
        )
    by_date = rows_by_date(fresh_rows)
    current = {}
    errors = []
    for row in plan["rows"]:
        row_date = _iso_to_date(row["date"])
        matches = by_date.get(row_date) or []
        if len(matches) != 1 or matches[0].record_id != str(row["record_id"]):
            errors.append({
                "date": row["date"],
                "expected_record_id": row["record_id"],
                "match_count": len(matches),
                "actual_record_ids": [match.record_id for match in matches],
            })
            continue
        current[str(row["record_id"])] = matches[0]
    if errors:
        raise RuntimeError(f"journal preflight failed: {errors}")
    return current


def _apply_journal(*, context: NavRepairContext, journal_path: Path, resume: bool) -> Dict[str, Any]:
    with process_lock(account_lock_key(context.account)):
        with process_lock(f"nav-repair:{journal_path.resolve()}"):
            return _apply_journal_locked(context=context, journal_path=journal_path, resume=resume)


def _apply_journal_locked(*, context: NavRepairContext, journal_path: Path, resume: bool) -> Dict[str, Any]:
    plan = _read_journal(journal_path)
    if plan["state"] in {"ROLLING_BACK", "ROLLBACK_PARTIAL", "ROLLED_BACK"}:
        raise SystemExit(f"cannot apply journal in state {plan['state']}")
    try:
        current = _current_rows(context, plan)
    except Exception as exc:
        row = next(
            (item for item in plan["rows"] if item["status"] != "applied"),
            plan["rows"][0],
        )
        error = str(exc)
        _append_journal(journal_path, {
            "event": "ROW",
            "record_id": row["record_id"],
            "date": row["date"],
            "status": "failed",
            "error": error,
        })
        _append_journal(
            journal_path,
            {"event": "STATE", "state": "PARTIAL", "error": error},
        )
        failed_plan = _read_journal(journal_path)
        return _result(
            failed_plan,
            journal_path,
            _apply_failure_status(failed_plan),
        )

    conflict = None
    for row in plan["rows"]:
        live_row = current[str(row["record_id"])]
        if not _same_row_states(live_row, row["base_fields"]):
            conflict = (row, "immutable base fields changed after planning")
            break
        if row["status"] == "applied":
            if not _same_row_states(
                live_row,
                row["desired_maintenance_fields"],
            ):
                conflict = (
                    row,
                    "applied row no longer matches complete desired maintenance state",
                )
                break
        elif _same_row_states(live_row, row["desired_maintenance_fields"]):
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "applied",
                "recovered": True,
            })
        elif not _same_row_states(
            live_row,
            row["original_maintenance_fields"],
        ):
            conflict = (
                row,
                "current row matches neither complete original nor desired maintenance state",
            )
            break
    if conflict:
        row, error = conflict
        _append_journal(journal_path, {
            "event": "ROW",
            "record_id": row["record_id"],
            "date": row["date"],
            "status": "failed",
            "error": error,
        })
        _append_journal(journal_path, {"event": "STATE", "state": "PARTIAL", "error": error})
        failed_plan = _read_journal(journal_path)
        return _result(failed_plan, journal_path, _apply_failure_status(failed_plan))

    _append_journal(journal_path, {"event": "STATE", "state": "APPLYING", "resume": resume})
    plan = _read_journal(journal_path)
    for row in plan["rows"]:
        if row["status"] == "applied":
            continue
        write_attempted = False
        try:
            live_row = current[str(row["record_id"])]
            if not _same_row_states(live_row, row["base_fields"]):
                raise RuntimeError("immutable base fields changed before apply")
            if _same_row_states(live_row, row["desired_maintenance_fields"]):
                _append_journal(journal_path, {
                    "event": "ROW",
                    "record_id": row["record_id"],
                    "date": row["date"],
                    "status": "applied",
                    "recovered": True,
                })
                continue
            if not _same_row_states(
                live_row,
                row["original_maintenance_fields"],
            ):
                raise RuntimeError(
                    "current row matches neither complete original nor desired "
                    "maintenance state before apply"
                )
            write_attempted = True
            restricted_patch(
                context,
                record_id=str(row["record_id"]),
                states=_states_from_envelopes(row["target_fields"]),
                dry_run=False,
            )
            current = _current_rows(context, plan)
            live_row = current[str(row["record_id"])]
            if not _same_row_states(live_row, row["base_fields"]):
                raise RuntimeError("immutable base fields changed during apply")
            if not _same_row_states(
                live_row,
                row["desired_maintenance_fields"],
            ):
                raise RuntimeError(
                    "fresh readback does not match complete desired maintenance state"
                )
        except Exception as exc:
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "failed",
                "error": str(exc),
                "write_attempted": write_attempted,
            })
            _append_journal(journal_path, {"event": "STATE", "state": "PARTIAL", "error": str(exc)})
            failed_plan = _read_journal(journal_path)
            return _result(failed_plan, journal_path, _apply_failure_status(failed_plan))
        _append_journal(journal_path, {
            "event": "ROW",
            "record_id": row["record_id"],
            "date": row["date"],
            "status": "applied",
        })

    _append_journal(journal_path, {"event": "STATE", "state": "COMPLETED"})
    return _result(_read_journal(journal_path), journal_path, "completed")


def _rollback_journal(*, context: NavRepairContext, journal_path: Path) -> Dict[str, Any]:
    with process_lock(account_lock_key(context.account)):
        with process_lock(f"nav-repair:{journal_path.resolve()}"):
            return _rollback_journal_locked(context=context, journal_path=journal_path)


def _rollback_journal_locked(*, context: NavRepairContext, journal_path: Path) -> Dict[str, Any]:
    plan = _read_journal(journal_path)
    if plan["state"] == "ROLLED_BACK":
        return _result(plan, journal_path, "rolled_back")
    current = _current_rows(context, plan)
    _append_journal(journal_path, {"event": "STATE", "state": "ROLLING_BACK"})

    for row in reversed(plan["rows"]):
        if row["status"] == "rolled_back":
            continue
        live_row = current[str(row["record_id"])]
        if not _same_row_states(live_row, row["base_fields"]):
            error = "immutable base fields changed after planning"
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "rollback_failed",
                "error": error,
            })
            _append_journal(
                journal_path,
                {"event": "STATE", "state": "ROLLBACK_PARTIAL", "error": error},
            )
            return _result(
                _read_journal(journal_path),
                journal_path,
                "rollback_partial",
            )
        if _same_row_states(live_row, row["original_maintenance_fields"]):
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "rolled_back",
                "recovered": True,
            })
            continue
        if not _same_row_states(live_row, row["desired_maintenance_fields"]):
            error = (
                "current row matches neither complete desired nor original "
                "maintenance state"
            )
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "rollback_failed",
                "error": error,
            })
            _append_journal(journal_path, {"event": "STATE", "state": "ROLLBACK_PARTIAL", "error": error})
            return _result(_read_journal(journal_path), journal_path, "rollback_partial")
        try:
            restricted_patch(
                context,
                record_id=str(row["record_id"]),
                states=_states_from_envelopes(row["original_fields"]),
                dry_run=False,
            )
            current = _current_rows(context, plan)
            live_row = current[str(row["record_id"])]
            if not _same_row_states(live_row, row["base_fields"]):
                raise RuntimeError("immutable base fields changed during rollback")
            if not _same_row_states(
                live_row,
                row["original_maintenance_fields"],
            ):
                raise RuntimeError(
                    "fresh rollback readback does not match complete original "
                    "maintenance state"
                )
        except Exception as exc:
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "rollback_failed",
                "error": str(exc),
            })
            _append_journal(journal_path, {"event": "STATE", "state": "ROLLBACK_PARTIAL", "error": str(exc)})
            return _result(_read_journal(journal_path), journal_path, "rollback_partial")
        _append_journal(journal_path, {
            "event": "ROW",
            "record_id": row["record_id"],
            "date": row["date"],
            "status": "rolled_back",
        })

    _append_journal(journal_path, {"event": "STATE", "state": "ROLLED_BACK"})
    return _result(_read_journal(journal_path), journal_path, "rolled_back")


def _print_result(result: Dict[str, Any]) -> Dict[str, Any]:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default=None)
    ap.add_argument("--patch-file", default=None)
    ap.add_argument("--mode", choices=["strong-consistency-gap"], default="strong-consistency-gap")
    action = ap.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--resume-journal")
    action.add_argument("--rollback-journal")
    ap.add_argument("--backup-file", default=None, help="where to write backup JSON before apply")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument(
        "--validate-level",
        choices=["basic", "full"],
        default="basic",
        help="compatibility option; both levels enforce canonical invariants",
    )
    ap.add_argument("--validate-scope", choices=["changed", "patched", "all"], default="changed")
    args = ap.parse_args(argv)
    return run(args)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    resume_journal = getattr(args, "resume_journal", None)
    rollback_journal = getattr(args, "rollback_journal", None)
    dry_run = bool(getattr(args, "dry_run", False))
    apply = bool(getattr(args, "apply", False))
    selected = sum(bool(value) for value in [dry_run, apply, resume_journal, rollback_journal])
    if selected != 1:
        raise SystemExit("choose exactly one of --dry-run / --apply / --resume-journal / --rollback-journal")

    if rollback_journal:
        journal_path = Path(rollback_journal)
        plan = _read_journal(journal_path)
        requested_account = getattr(args, "account", None)
        if requested_account and requested_account != plan["account"]:
            raise SystemExit(f"journal account mismatch: {requested_account} != {plan['account']}")
        context = create_nav_repair_context(account=plan["account"])
        return _print_result(_rollback_journal(context=context, journal_path=journal_path))

    patch_file = getattr(args, "patch_file", None)
    if not patch_file:
        raise SystemExit("--patch-file is required unless --rollback-journal is used")
    if getattr(args, "no_validate", False):
        raise SystemExit(
            "--no-validate is no longer supported: canonical NAV invariants are mandatory"
        )
    context = create_nav_repair_context(account=getattr(args, "account", None))
    patches = load_patch_rows(patch_file, args.mode)
    fresh_rows = read_fresh_nav_rows(context)
    rows, diffs, series, dependency_rows, plan_digest = _resolve_patch_targets(
        context=context,
        fresh_rows=fresh_rows,
        patches=patches,
        account=context.account,
        mode=args.mode,
    )

    if resume_journal:
        journal_path = Path(resume_journal)
        plan = _read_journal(journal_path)
        if plan["plan_digest"] != plan_digest:
            raise SystemExit(f"resume plan digest mismatch: {plan_digest} != {plan['plan_digest']}")
        if plan["account"] != context.account or plan["mode"] != args.mode:
            raise SystemExit("resume plan account/mode mismatch")
        return _print_result(_apply_journal(context=context, journal_path=journal_path, resume=True))

    validation_dates = _validation_dates(
        series=series,
        rows=rows,
        validate_scope=args.validate_scope,
    )
    violations = _canonical_validation_violations(
        context=context,
        fresh_rows=fresh_rows,
        series=series,
        rows=rows,
        validation_dates=validation_dates,
    )

    out_dir = Path("audit")
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    diff_path = out_dir / f"nav_history_repair_patch_diff_{context.account}_{stamp}.json"
    diff_path.write_text(
        json.dumps({
            "account": context.account,
            "mode": args.mode,
            "plan_digest": plan_digest,
            "diffs": diffs,
            "validation_dates": sorted(d.isoformat() for d in validation_dates),
            "violations": violations,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("wrote", diff_path)

    if violations and apply:
        raise SystemExit("abort apply due to validation errors")
    if dry_run:
        return _print_result({
            "success": not violations,
            "status": "dry_run" if not violations else "failed",
            "plan_digest": plan_digest,
            "changed": sum(1 for diff in diffs if diff["changes"]),
            "target_count": len(rows),
            "validation_dates": sorted(d.isoformat() for d in validation_dates),
            "violations": violations,
            "diff_path": str(diff_path),
        })

    backup_file = getattr(args, "backup_file", None) or str(
        out_dir / f"nav_history_repair_patch_backup_{context.account}_{stamp}.json"
    )
    Path(backup_file).write_text(
        json.dumps({
            "account": context.account,
            "plan_digest": plan_digest,
            "dependency_rows": dependency_rows,
            "rows": [
                {
                    "date": row["date"],
                    "record_id": row["record_id"],
                    "base_fields": row["base_fields"],
                    "original_fields": row["original_fields"],
                    "original_maintenance_fields": row[
                        "original_maintenance_fields"
                    ],
                }
                for row in rows
            ],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("backup wrote", backup_file)

    journal_dir = config.get_data_dir() / "nav_repair"
    journal_path = journal_dir / f"{plan_digest[:16]}-{stamp}.jsonl"
    with process_lock(f"nav-repair:{journal_path.resolve()}"):
        if journal_path.exists():
            raise SystemExit(f"journal already exists: {journal_path}")
        _append_journal(journal_path, {
            "event": "STATE",
            "state": "PLANNED",
            "account": context.account,
            "mode": args.mode,
            "patch_file": str(Path(patch_file).resolve()),
            "plan_digest": plan_digest,
            "created_at": datetime.now().isoformat(),
            "dependency_rows": dependency_rows,
            "rows": rows,
        })

    return _print_result(_apply_journal(context=context, journal_path=journal_path, resume=False))


if __name__ == "__main__":
    main()
