#!/usr/bin/env python3
"""Patch Feishu nav_history safely (merge + validate + dry-run).

This module contains the implementation behind
``scripts/nav_history_repair.py patch``.

Design goals
- Never overwrite non-target fields with model defaults (e.g., cash_value/stock_value becoming 0).
- Two-phase workflow: dry-run diff -> apply.
- Optional mathematical validations; abort apply if any invariant fails.

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
- We intentionally patch only a whitelist of fields.
- We always read existing record first and merge patch fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src import config
from src.maintenance.nav_history_repair.context import NavRepairContext, create_nav_repair_context
from src.models import NAVHistory
from src.process_lock import account_lock_key, process_lock


MONEY_EPS = 0.06  # tolerate rounding/quantization noise
NAV_EPS = 1e-6
WEIGHT_EPS = 1e-4


def _iso_to_date(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _money_equal(a: Optional[float], b: Optional[float], eps: float = MONEY_EPS) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= eps


def _nav_equal(a: Optional[float], b: Optional[float], eps: float = 2e-6) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= eps


def _weight_equal(a: Optional[float], b: Optional[float], eps: float = WEIGHT_EPS) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= eps


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
            out.append(
                PatchRow(
                    d=d,
                    cash_flow=float(r["gap_cash_flow"]) if r.get("gap_cash_flow") is not None else None,
                    share_change=float(r["gap_share_change"]) if r.get("gap_share_change") is not None else None,
                    shares=float(r["shares"]) if r.get("shares") is not None else None,
                    nav=float(r["nav"]) if r.get("nav") is not None else None,
                    pnl=float(r["pnl"]) if r.get("pnl") is not None else None,
                    mtd_nav_change=float(r["mtd_nav_change"]) if r.get("mtd_nav_change") is not None else None,
                    ytd_nav_change=float(r["ytd_nav_change"]) if r.get("ytd_nav_change") is not None else None,
                    mtd_pnl=float(r["mtd_pnl"]) if r.get("mtd_pnl") is not None else None,
                    ytd_pnl=float(r["ytd_pnl"]) if r.get("ytd_pnl") is not None else None,
                )
            )
        else:
            raise ValueError(f"unsupported mode: {mode}")

    dates = [p.d for p in out]
    duplicates = sorted({d for d in dates if dates.count(d) > 1})
    if duplicates:
        raise ValueError(f"patch-file contains duplicate dates: {[d.isoformat() for d in duplicates]}")
    return sorted(out, key=lambda row: row.d)


def merge_existing(existing: NAVHistory, patch: PatchRow, patch_none: bool = False) -> NAVHistory:
    """Return a new NAVHistory object, preserving all non-target fields."""

    def pick(old: Any, new: Any) -> Any:
        if new is None and not patch_none:
            return old
        return new

    return NAVHistory(
        record_id=existing.record_id,
        date=existing.date,
        account=existing.account,
        # keep breakdown + weights + total_value as-is
        total_value=existing.total_value,
        cash_value=existing.cash_value,
        stock_value=existing.stock_value,
        fund_value=existing.fund_value,
        cn_stock_value=existing.cn_stock_value,
        us_stock_value=existing.us_stock_value,
        hk_stock_value=existing.hk_stock_value,
        stock_weight=existing.stock_weight,
        cash_weight=existing.cash_weight,
        # patch target fields
        shares=pick(existing.shares, patch.shares),
        nav=pick(existing.nav, patch.nav),
        cash_flow=pick(existing.cash_flow, patch.cash_flow),
        share_change=pick(existing.share_change, patch.share_change),
        pnl=pick(existing.pnl, patch.pnl),
        mtd_nav_change=pick(existing.mtd_nav_change, patch.mtd_nav_change),
        ytd_nav_change=pick(existing.ytd_nav_change, patch.ytd_nav_change),
        mtd_pnl=pick(existing.mtd_pnl, patch.mtd_pnl),
        ytd_pnl=pick(existing.ytd_pnl, patch.ytd_pnl),
        # preserve details unless we explicitly patch it elsewhere
        details=existing.details,
    )


def validate_math(
    *,
    context: NavRepairContext,
    navs_sorted: List[NAVHistory],
    idx: int,
    candidate: NAVHistory,
    mode: str,
    validate_level: str = "basic",  # basic|full
) -> List[str]:
    """Return list of violations for one candidate record.

    validate_level:
      - basic: invariants that should always hold for patched fields (safe, low false positives)
      - full: include breakdown weights + mtd/ytd derivations (stricter; may flag legacy-history inconsistencies)
    """
    errs: List[str] = []

    # Invariant A/B: breakdown consistency
    # We only enforce when breakdown appears to be populated (non-trivial values or weights present),
    # because legacy history may not store these fields.
    breakdown_present = (
        (candidate.cash_value is not None and abs(candidate.cash_value) > MONEY_EPS)
        or (candidate.stock_value is not None and abs(candidate.stock_value) > MONEY_EPS)
        or (candidate.fund_value is not None and abs(candidate.fund_value) > MONEY_EPS)
        or (candidate.stock_weight is not None)
        or (candidate.cash_weight is not None)
    )

    # total_value == stock_value + cash_value is a basic accounting identity (when breakdown exists)
    if breakdown_present:
        expected_total = (candidate.stock_value or 0.0) + (candidate.cash_value or 0.0)
        if candidate.total_value is not None and not _money_equal(candidate.total_value, expected_total):
            errs.append(f"total_value != stock_value + cash_value ({candidate.total_value} != {expected_total})")

    # weights checks are stricter; keep them in full mode
    if validate_level == "full" and breakdown_present:
        if candidate.total_value and candidate.total_value > 0 and candidate.stock_weight is not None and candidate.cash_weight is not None:
            exp_stock_w = (candidate.stock_value or 0.0) / candidate.total_value
            exp_cash_w = (candidate.cash_value or 0.0) / candidate.total_value
            if not _weight_equal(candidate.stock_weight, exp_stock_w):
                errs.append(f"stock_weight mismatch ({candidate.stock_weight} != {exp_stock_w})")
            if not _weight_equal(candidate.cash_weight, exp_cash_w):
                errs.append(f"cash_weight mismatch ({candidate.cash_weight} != {exp_cash_w})")
            if not _weight_equal(candidate.stock_weight + candidate.cash_weight, 1.0):
                errs.append(f"weights sum != 1 ({candidate.stock_weight + candidate.cash_weight})")

    # Invariant C: nav = total_value / shares
    if candidate.shares is not None and candidate.nav is not None and candidate.shares > 0 and candidate.total_value is not None:
        exp_nav = candidate.total_value / candidate.shares
        if not _nav_equal(candidate.nav, exp_nav):
            errs.append(f"nav != total_value/shares ({candidate.nav} != {exp_nav})")

    # Invariant D: recurrence + share_change relation (strong-consistency-gap)
    if mode == "strong-consistency-gap":
        if idx > 0:
            prev = navs_sorted[idx - 1]
            if prev.nav is not None and prev.nav > 0 and prev.shares is not None and candidate.shares is not None:
                if candidate.cash_flow is not None and candidate.share_change is not None:
                    # Share change relation is sensitive to rounding of prev.nav.
                    # Validate via cash terms: share_change * prev_nav ~= cash_flow.
                    exp_cash = candidate.share_change * prev.nav
                    # allow small drift due to rounding/quantization
                    if not _money_equal(candidate.cash_flow, exp_cash, eps=10.0):
                        errs.append(f"cash_flow != share_change*prev_nav ({candidate.cash_flow} != {exp_cash})")

                    exp_shares = prev.shares + candidate.share_change
                    # shares quantized to 0.01; tolerate several rounding units
                    if not _money_equal(candidate.shares, exp_shares, eps=0.30):
                        errs.append(f"shares != prev_shares + share_change ({candidate.shares} != {exp_shares})")

                # pnl constraint only for consecutive day
                if (candidate.date - prev.date).days == 1:
                    if candidate.pnl is None:
                        errs.append("pnl should not be None for consecutive day")
                    else:
                        exp_pnl = candidate.total_value - prev.total_value - (candidate.cash_flow or 0.0)
                        if not _money_equal(candidate.pnl, exp_pnl):
                            errs.append(f"pnl mismatch ({candidate.pnl} != {exp_pnl})")
                else:
                    # for non-consecutive, pnl should be None (project convention)
                    if candidate.pnl is not None:
                        errs.append("pnl should be None when not consecutive day")

        # MTD/YTD are "full" checks; legacy stored values may follow older conventions.
        if validate_level == "full":
            all_navs = navs_sorted
            p = context.portfolio
            nav_index = p._build_nav_lookup(all_navs)

            pm_base = p._find_prev_month_end_nav(all_navs, candidate.date.year, candidate.date.month, nav_index=nav_index)
            py_base = p._find_year_end_nav(all_navs, str(candidate.date.year - 1), nav_index=nav_index)
            mtd_return_base = p._find_mtd_return_base_nav(all_navs, candidate.date, nav_index=nav_index)
            ytd_return_base = p._find_ytd_return_base_nav(all_navs, candidate.date, nav_index=nav_index)

            if candidate.nav is not None:
                exp_mtd = p._calc_mtd_nav_change(candidate.nav, mtd_return_base) if mtd_return_base else None
                exp_ytd = p._calc_ytd_nav_change(candidate.nav, ytd_return_base) if ytd_return_base else None
                exp_mtd_r = round(exp_mtd, 6) if exp_mtd is not None else None
                exp_ytd_r = round(exp_ytd, 6) if exp_ytd is not None else None
                if candidate.mtd_nav_change is not None:
                    if exp_mtd_r is None:
                        errs.append("mtd_nav_change patched but month base missing")
                    elif not _nav_equal(candidate.mtd_nav_change, exp_mtd_r):
                        errs.append(f"mtd_nav_change mismatch ({candidate.mtd_nav_change} != {exp_mtd_r})")
                if candidate.ytd_nav_change is not None:
                    if exp_ytd_r is None:
                        errs.append("ytd_nav_change patched but year base missing")
                    elif not _nav_equal(candidate.ytd_nav_change, exp_ytd_r):
                        errs.append(f"ytd_nav_change mismatch ({candidate.ytd_nav_change} != {exp_ytd_r})")

            monthly_cf = p._get_monthly_cash_flow(candidate.account, candidate.date.year, candidate.date.month) if pm_base else None
            yearly_cf = p._get_yearly_cash_flow(candidate.account, str(candidate.date.year)) if py_base else None

            if candidate.mtd_pnl is not None:
                if not (pm_base and monthly_cf is not None):
                    errs.append("mtd_pnl patched but month base/cash_flow missing")
                else:
                    exp_mtd_pnl = p._calc_mtd_pnl(candidate.total_value, pm_base, monthly_cf)
                    exp_mtd_pnl_r = round(exp_mtd_pnl, 2) if exp_mtd_pnl is not None else None
                    if exp_mtd_pnl_r is not None and not _money_equal(candidate.mtd_pnl, exp_mtd_pnl_r):
                        errs.append(f"mtd_pnl mismatch ({candidate.mtd_pnl} != {exp_mtd_pnl_r})")

            if candidate.ytd_pnl is not None:
                if not (py_base and yearly_cf is not None):
                    errs.append("ytd_pnl patched but year base/cash_flow missing")
                else:
                    exp_ytd_pnl = p._calc_ytd_pnl(candidate.total_value, py_base, yearly_cf)
                    exp_ytd_pnl_r = round(exp_ytd_pnl, 2) if exp_ytd_pnl is not None else None
                    if exp_ytd_pnl_r is not None and not _money_equal(candidate.ytd_pnl, exp_ytd_pnl_r):
                        errs.append(f"ytd_pnl mismatch ({candidate.ytd_pnl} != {exp_ytd_pnl_r})")

    return errs



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
NON_TARGET_FIELDS = [
    "cash_value",
    "stock_value",
    "fund_value",
    "cn_stock_value",
    "us_stock_value",
    "hk_stock_value",
    "total_value",
]


def _target_fields(nav: NAVHistory) -> Dict[str, Any]:
    return {field: getattr(nav, field) for field in PATCH_FIELDS}


def _with_target_fields(nav: NAVHistory, fields: Dict[str, Any]) -> NAVHistory:
    payload = nav.model_dump()
    payload.update({field: fields.get(field) for field in PATCH_FIELDS})
    return NAVHistory(**payload)


def _same_target_fields(nav: NAVHistory, fields: Dict[str, Any]) -> bool:
    return _target_fields(nav) == {field: fields.get(field) for field in PATCH_FIELDS}


def _resolve_patch_targets(
    *,
    navs: List[NAVHistory],
    patches: List[PatchRow],
    account: str,
    mode: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[NAVHistory], str]:
    by_date: Dict[date, List[NAVHistory]] = {}
    for nav in navs:
        by_date.setdefault(nav.date, []).append(nav)

    errors = []
    rows = []
    merged = []
    diffs = []
    for patch in patches:
        matches = by_date.get(patch.d) or []
        if len(matches) != 1:
            errors.append({"date": patch.d.isoformat(), "match_count": len(matches)})
            continue
        existing = matches[0]
        if not existing.record_id:
            errors.append({"date": patch.d.isoformat(), "match_count": 1, "error": "missing record_id"})
            continue
        candidate = merge_existing(existing, patch)
        for field in NON_TARGET_FIELDS:
            if getattr(existing, field) != getattr(candidate, field):
                raise SystemExit(f"safety abort: non-target field changed: {patch.d} {field}")
        original_fields = _target_fields(existing)
        target_fields = _target_fields(candidate)
        changes = {
            field: {"old": original_fields[field], "new": target_fields[field]}
            for field in PATCH_FIELDS
            if original_fields[field] != target_fields[field]
        }
        rows.append({
            "date": patch.d.isoformat(),
            "record_id": existing.record_id,
            "original_fields": original_fields,
            "target_fields": target_fields,
            "status": "pending",
        })
        merged.append(candidate)
        diffs.append({"date": patch.d.isoformat(), "record_id": existing.record_id, "changes": changes})

    if errors:
        raise SystemExit(f"patch preflight failed: every target date must resolve to exactly one record: {errors}")

    digest_payload = {
        "account": account,
        "mode": mode,
        "rows": [
            {
                "date": row["date"],
                "record_id": row["record_id"],
                "target_fields": row["target_fields"],
            }
            for row in rows
        ],
    }
    digest_raw = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    plan_digest = hashlib.sha256(digest_raw.encode("utf-8")).hexdigest()
    return rows, diffs, merged, plan_digest


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
    ordered_dates = [nav.date for nav in series]
    positions = {nav_date: idx for idx, nav_date in enumerate(ordered_dates)}
    for changed_date in changed_dates:
        idx = positions.get(changed_date)
        if idx is not None and idx + 1 < len(ordered_dates):
            selected.add(ordered_dates[idx + 1])
    return selected


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
    rows = {
        str(row["record_id"]): {**row, "status": "pending", "error": None}
        for row in plan["rows"]
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
        "resume_command": resume_command,
        "rollback_command": rollback_command,
    }
    result.update(extra)
    return result


def _apply_failure_status(plan: Dict[str, Any]) -> str:
    return "partial" if any(row["status"] == "applied" for row in plan["rows"]) else "failed"


def _current_rows(context: NavRepairContext, plan: Dict[str, Any]) -> Dict[str, NAVHistory]:
    navs = context.storage.get_nav_history(context.account, days=9999)
    by_date: Dict[date, List[NAVHistory]] = {}
    for nav in navs:
        by_date.setdefault(nav.date, []).append(nav)
    current = {}
    errors = []
    for row in plan["rows"]:
        row_date = _iso_to_date(row["date"])
        matches = by_date.get(row_date) or []
        if len(matches) != 1 or str(getattr(matches[0], "record_id", "")) != str(row["record_id"]):
            errors.append({
                "date": row["date"],
                "expected_record_id": row["record_id"],
                "match_count": len(matches),
                "actual_record_ids": [getattr(match, "record_id", None) for match in matches],
            })
            continue
        current[str(row["record_id"])] = matches[0]
    if errors:
        raise SystemExit(f"journal preflight failed: {errors}")
    return current


def _apply_journal(*, context: NavRepairContext, journal_path: Path, resume: bool) -> Dict[str, Any]:
    with process_lock(account_lock_key(context.account)):
        with process_lock(f"nav-repair:{journal_path.resolve()}"):
            return _apply_journal_locked(context=context, journal_path=journal_path, resume=resume)


def _apply_journal_locked(*, context: NavRepairContext, journal_path: Path, resume: bool) -> Dict[str, Any]:
    plan = _read_journal(journal_path)
    if plan["state"] in {"ROLLING_BACK", "ROLLBACK_PARTIAL", "ROLLED_BACK"}:
        raise SystemExit(f"cannot apply journal in state {plan['state']}")
    current = _current_rows(context, plan)

    conflict = None
    for row in plan["rows"]:
        nav = current[str(row["record_id"])]
        if row["status"] == "applied":
            if not _same_target_fields(nav, row["target_fields"]):
                conflict = (row, "applied row no longer matches target fields")
                break
        elif _same_target_fields(nav, row["target_fields"]):
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "applied",
                "recovered": True,
            })
        elif not _same_target_fields(nav, row["original_fields"]):
            conflict = (row, "current row matches neither original nor target fields")
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
        nav = current[str(row["record_id"])]
        try:
            context.storage.write_nav_record(
                _with_target_fields(nav, row["target_fields"]),
                overwrite_existing=True,
                dry_run=False,
            )
        except Exception as exc:
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "failed",
                "error": str(exc),
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
        nav = current[str(row["record_id"])]
        if _same_target_fields(nav, row["original_fields"]):
            _append_journal(journal_path, {
                "event": "ROW",
                "record_id": row["record_id"],
                "date": row["date"],
                "status": "rolled_back",
                "recovered": True,
            })
            continue
        if not _same_target_fields(nav, row["target_fields"]):
            error = "current row matches neither target nor original fields"
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
            context.storage.write_nav_record(
                _with_target_fields(nav, row["original_fields"]),
                overwrite_existing=True,
                dry_run=False,
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
    ap.add_argument("--validate-level", choices=["basic", "full"], default="basic")
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
    context = create_nav_repair_context(account=getattr(args, "account", None))
    patches = load_patch_rows(patch_file, args.mode)
    navs = sorted(context.storage.get_nav_history(context.account, days=9999), key=lambda nav: nav.date)
    rows, diffs, merged, plan_digest = _resolve_patch_targets(
        navs=navs,
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

    patched_by_date = {candidate.date: candidate for candidate in merged}
    series = [patched_by_date.get(nav.date, nav) for nav in navs]
    violations: List[Dict[str, Any]] = []
    validation_dates = set()
    if not args.no_validate:
        validation_dates = _validation_dates(series=series, rows=rows, validate_scope=args.validate_scope)
        for idx, nav in enumerate(series):
            if nav.date not in validation_dates:
                continue
            errors = validate_math(
                context=context,
                navs_sorted=series,
                idx=idx,
                candidate=nav,
                mode=args.mode,
                validate_level=args.validate_level,
            )
            if errors:
                violations.append({"date": nav.date.isoformat(), "record_id": nav.record_id, "errors": errors})

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
            "rows": [
                {
                    "date": nav.date.isoformat(),
                    "record_id": nav.record_id,
                    "fields": nav.model_dump(mode="json"),
                }
                for nav in navs
                if nav.date in {_iso_to_date(row["date"]) for row in rows}
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
            "rows": rows,
        })

    return _print_result(_apply_journal(context=context, journal_path=journal_path, resume=False))


if __name__ == "__main__":
    main()
