#!/usr/bin/env python3
"""Recompute historical NAV derived fields without rewriting base facts."""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src import config
from src.domain.nav_calculator import NavCalculator
from src.maintenance.nav_history_repair import patch as patch_workflow
from src.maintenance.nav_history_repair.common import (
    BASE_FIELDS,
    FreshNavRow,
    MAINTENANCE_FIELDS,
    assert_maintenance_history_evidence,
    changed_states,
    maintenance_dependency_evidence,
    maintenance_target_states,
    read_fresh_nav_rows,
    recompute_derived_row,
    rows_by_date,
    state_subset,
)
from src.maintenance.nav_history_repair.context import create_nav_repair_context
from src.models import NAVHistory
from src.process_lock import process_lock


@dataclass(frozen=True)
class BaseNavPoint:
    """Requested date plus optional expected immutable evidence from input."""

    d: date
    total_value: Optional[float] = None
    cash_value: Optional[float] = None
    stock_value: Optional[float] = None
    fund_value: Optional[float] = None
    cn_stock_value: Optional[float] = None
    us_stock_value: Optional[float] = None
    hk_stock_value: Optional[float] = None
    record_id: Optional[str] = None
    expected_account: Optional[str] = None
    provided_fields: frozenset[str] = frozenset()


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000).date()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


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


def _load_input_rows(path: str) -> List[Dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("rebuilt", "rows", "navs", "items"):
        rows = data.get(key)
        if isinstance(rows, list):
            return rows
    raise ValueError(
        "input json must be a list or contain one of keys: rebuilt/rows/navs/items"
    )


def _rows_to_points(rows: List[Dict[str, Any]]) -> List[BaseNavPoint]:
    points: list[BaseNavPoint] = []
    for row_index, raw in enumerate(rows):
        if not isinstance(raw, dict) or raw.get("date") in (None, ""):
            raise ValueError(f"input row {row_index} requires date")
        provided = frozenset(str(key) for key in raw)
        kwargs = {
            field: _optional_finite_float(raw.get(field), field=field)
            for field in BASE_FIELDS
            if field in raw
        }
        points.append(
            BaseNavPoint(
                d=_to_date(raw["date"]),
                **kwargs,
                record_id=(
                    str(raw.get("record_id") or "").strip() or None
                    if "record_id" in raw
                    else None
                ),
                expected_account=(
                    str(raw.get("account") or "").strip() or None
                    if "account" in raw
                    else None
                ),
                provided_fields=provided,
            )
        )
    return sorted(points, key=lambda point: point.d)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recompute/backfill nav_history derived fields"
    )
    parser.add_argument("--account", default="lx")
    parser.add_argument("--input", help="Input JSON with dates/expected base evidence")
    parser.add_argument("--from", dest="d_from", help="YYYY-MM-DD")
    parser.add_argument("--to", dest="d_to", help="YYYY-MM-DD")
    parser.add_argument("--mode", choices=["replace", "upsert"], default="replace")
    parser.add_argument("--allow-partial", action="store_true")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        raise ValueError("--apply and --dry-run are mutually exclusive")
    return args


def main(argv=None) -> Dict[str, Any]:
    return run(parse_args(argv))


def _emit(payload: Dict[str, Any]) -> Dict[str, Any]:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return payload


def _blocked(
    *,
    account: str,
    reasons: list[dict[str, Any]],
    mode: str,
) -> Dict[str, Any]:
    return _emit({
        "success": False,
        "status": "historical_evidence_required",
        "account": account,
        "mode": mode,
        "reasons": reasons,
        "write": {"would_write": 0, "written": 0},
    })


def _assert_expected_base(point: BaseNavPoint, observed: FreshNavRow) -> list[str]:
    errors: list[str] = []
    if "record_id" in point.provided_fields and point.record_id != observed.record_id:
        errors.append(
            f"record_id expected={point.record_id!r} actual={observed.record_id!r}"
        )
    if "account" in point.provided_fields and point.expected_account != observed.nav.account:
        errors.append(
            f"account expected={point.expected_account!r} actual={observed.nav.account!r}"
        )
    for field in BASE_FIELDS:
        if field not in point.provided_fields:
            continue
        expected = getattr(point, field)
        actual_state = observed.field_states.get(field)
        if expected is None:
            if actual_state is None or actual_state.state != "null":
                errors.append(
                    f"{field} expected=null actual={getattr(actual_state, 'state', 'missing')}"
                )
        elif actual_state is None or actual_state.state != "value":
            errors.append(
                f"{field} expected={expected} actual_state={getattr(actual_state, 'state', 'missing')}"
            )
        elif not NavCalculator.money_equal(expected, actual_state.value):
            errors.append(
                f"{field} expected={expected} actual={actual_state.value}"
            )
    return errors


def _replace_working(
    working: list[NAVHistory],
    candidate: NAVHistory,
) -> list[NAVHistory]:
    return [
        candidate if nav.record_id == candidate.record_id else nav
        for nav in working
    ]


def run(args: argparse.Namespace) -> Dict[str, Any]:
    if args.apply and args.dry_run:
        raise ValueError("--apply and --dry-run are mutually exclusive")
    if getattr(args, "allow_partial", False):
        raise ValueError(
            "--allow-partial is not supported for CAS-protected derived maintenance"
        )

    context = create_nav_repair_context(account=args.account)
    fresh_rows = read_fresh_nav_rows(context)

    if args.input:
        points = _rows_to_points(_load_input_rows(args.input))
    else:
        if not args.d_from or not args.d_to:
            raise ValueError("either --input or (--from and --to) is required")
        d_from = _to_date(args.d_from)
        d_to = _to_date(args.d_to)
        points = [
            BaseNavPoint(d=row.nav.date)
            for row in fresh_rows
            if d_from <= row.nav.date <= d_to
        ]

    if args.limit and args.limit > 0:
        points = points[: args.limit]
    if not points:
        return _emit({
            "success": True,
            "status": "no_targets",
            "account": context.account,
            "count": 0,
            "write": {"would_write": 0, "written": 0},
        })

    request_dates = [point.d for point in points]
    duplicate_requests = sorted({
        item for item in request_dates if request_dates.count(item) > 1
    })
    if duplicate_requests:
        return _blocked(
            account=context.account,
            mode=args.mode,
            reasons=[{
                "reason": "duplicate_requested_date",
                "dates": [item.isoformat() for item in duplicate_requests],
            }],
        )

    grouped = rows_by_date(fresh_rows)
    preflight_errors: list[dict[str, Any]] = []
    resolved: list[tuple[BaseNavPoint, FreshNavRow]] = []
    for point in points:
        matches = grouped.get(point.d) or []
        if len(matches) != 1:
            preflight_errors.append({
                "date": point.d.isoformat(),
                "reason": "missing_target" if not matches else "duplicate_target",
                "match_count": len(matches),
                "record_ids": [match.record_id for match in matches],
                "mode": args.mode,
            })
            continue
        evidence_errors = _assert_expected_base(point, matches[0])
        if evidence_errors:
            preflight_errors.append({
                "date": point.d.isoformat(),
                "reason": "base_evidence_drift",
                "errors": evidence_errors,
            })
            continue
        resolved.append((point, matches[0]))
    if preflight_errors:
        return _blocked(
            account=context.account,
            mode=args.mode,
            reasons=preflight_errors,
        )
    try:
        assert_maintenance_history_evidence(
            fresh_rows,
            account=context.account,
            target_dates=(point.d for point, _row in resolved),
        )
    except ValueError as exc:
        return _blocked(
            account=context.account,
            mode=args.mode,
            reasons=[{
                "reason": "maintenance_history_evidence_invalid",
                "error": str(exc),
            }],
        )

    working = [row.nav for row in fresh_rows]
    plan_rows: list[dict[str, Any]] = []
    diffs: list[dict[str, Any]] = []
    candidates: list[NAVHistory] = []
    digest_targets: list[dict[str, Any]] = []
    try:
        for _point, observed in resolved:
            run_id = (
                f"nav-repair:{context.account}:{observed.nav.date.isoformat()}"
            )
            candidate, _dataset = recompute_derived_row(
                context=context,
                observed=observed,
                working_navs=working,
                run_id=run_id,
            )
            original, target = changed_states(observed, candidate)
            desired = maintenance_target_states(observed, candidate)
            complete_original = state_subset(observed, MAINTENANCE_FIELDS)
            base_fields = {
                field: state.envelope()
                for field, state in state_subset(observed, BASE_FIELDS).items()
            }
            original_envelopes = {
                field: state.envelope() for field, state in original.items()
            }
            target_envelopes = {
                field: state.envelope() for field, state in target.items()
            }
            plan_rows.append({
                "date": observed.nav.date.isoformat(),
                "record_id": observed.record_id,
                "base_fields": base_fields,
                "original_fields": original_envelopes,
                "target_fields": target_envelopes,
                "original_maintenance_fields": {
                    field: state.envelope()
                    for field, state in complete_original.items()
                },
                "desired_maintenance_fields": {
                    field: state.envelope()
                    for field, state in desired.items()
                },
                "status": "pending",
            })
            diffs.append({
                "date": observed.nav.date.isoformat(),
                "record_id": observed.record_id,
                "changes": {
                    field: {
                        "old": original_envelopes[field],
                        "new": target_envelopes[field],
                    }
                    for field in target_envelopes
                },
            })
            candidates.append(candidate)
            digest_targets.append({
                "date": observed.nav.date.isoformat(),
                "record_id": observed.record_id,
                "base_fields": base_fields,
                "target_fields": {
                    field: state.envelope()
                    for field, state in desired.items()
                },
            })
            working = _replace_working(working, candidate)
    except Exception as exc:
        return _blocked(
            account=context.account,
            mode=args.mode,
            reasons=[{
                "reason": "canonical_recompute_blocked",
                "error": str(exc),
            }],
        )

    dependency_rows = maintenance_dependency_evidence(
        fresh_rows,
        target_dates=(point.d for point, _row in resolved),
    )
    plan_digest = patch_workflow.canonical_plan_digest(
        account=context.account,
        mode="strong-consistency-gap",
        rows=digest_targets,
        dependency_rows=dependency_rows,
    )
    changed_count = sum(bool(row["target_fields"]) for row in plan_rows)
    validation_dates = patch_workflow._validation_dates(
        series=working,
        rows=plan_rows,
        validate_scope="changed",
    )
    violations = patch_workflow._canonical_validation_violations(
        context=context,
        fresh_rows=fresh_rows,
        series=working,
        rows=plan_rows,
        validation_dates=validation_dates,
    )
    payload: Dict[str, Any] = {
        "success": not violations,
        "status": (
            "failed"
            if violations
            else ("dry_run" if not args.apply else "planned")
        ),
        "account": context.account,
        "count": len(candidates),
        "changed": changed_count,
        "date_from": candidates[0].date.isoformat(),
        "date_to": candidates[-1].date.isoformat(),
        "mode": args.mode,
        "plan_digest": plan_digest,
        "validation_dates": sorted(item.isoformat() for item in validation_dates),
        "violations": violations,
        "diffs": diffs,
        "sample": [
            {
                "date": nav.date.isoformat(),
                "nav": nav.nav,
                "shares": nav.shares,
                "cash_flow": nav.cash_flow,
                "gap_cash_flow": (
                    ((nav.details or {}).get("cash_flow_basis") or {}).get(
                        "gap_cash_flow"
                    )
                ),
                "pnl": nav.pnl,
            }
            for nav in candidates[:5]
        ],
    }
    if violations:
        payload["write"] = {"would_patch": 0, "written": 0}
        return _emit(payload)
    if not args.apply:
        payload["write"] = {
            "would_patch": changed_count,
            "full_row_writes": 0,
            "note": "run with --apply to persist derived-only patches",
        }
        return _emit(payload)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_dir = Path("audit")
    audit_dir.mkdir(exist_ok=True)
    request_path = audit_dir / (
        f"nav_history_backfill_request_{context.account}_{stamp}.json"
    )
    request_path.write_text(
        json.dumps(
            {"rows": [{"date": point.d.isoformat()} for point in points]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    journal_path = (
        config.get_data_dir()
        / "nav_repair"
        / f"{plan_digest[:16]}-{stamp}.jsonl"
    )
    with process_lock(f"nav-repair:{journal_path.resolve()}"):
        if journal_path.exists():
            raise SystemExit(f"journal already exists: {journal_path}")
        patch_workflow._append_journal(journal_path, {
            "event": "STATE",
            "state": "PLANNED",
            "account": context.account,
            "mode": "strong-consistency-gap",
            "patch_file": str(request_path.resolve()),
            "plan_digest": plan_digest,
            "created_at": datetime.now().isoformat(),
            "dependency_rows": dependency_rows,
            "rows": plan_rows,
        })
    result = patch_workflow._apply_journal(
        context=context,
        journal_path=journal_path,
        resume=False,
    )
    payload["success"] = result.get("success", False)
    payload["status"] = result.get("status")
    payload["write"] = result
    return _emit(payload)


if __name__ == "__main__":
    main()
