"""Explicit PBC-rate cash restatement; immutable plan, resume and rollback.

Run as a module. Preparation and verification use canonical NAV calculations;
only the restricted repository entrypoint writes business data.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import os
from pathlib import Path

from src.maintenance.nav_history_repair.common import (
    BASE_FIELDS, MAINTENANCE_FIELDS, FieldState, FreshNavRow,
    assert_maintenance_history_evidence, maintenance_target_states,
    read_fresh_nav_rows, recompute_derived_row, state_subset,
)
from src.maintenance.nav_history_repair.context import create_nav_repair_context
from src.maintenance.nav_history_repair.patch import _append_journal
from src.process_lock import account_lock_key, process_lock

FIELDS = (*BASE_FIELDS, *MAINTENANCE_FIELDS)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, default=str).encode()).hexdigest()


def states(row):
    return {k: v.envelope() for k, v in state_subset(row, FIELDS).items()}


def series(rows):
    return [{"record_id": r.record_id, "date": r.nav.date.isoformat(),
             "fields": states(r)} for r in rows]


def snapshots(context, start):
    storage = context.storage
    records = storage.client.list_records(
        "holdings_snapshot",
        filter_str=(f'CurrentValue.[account] = "{context.account}" && '
                    f'CurrentValue.[as_of] >= "{start}"'),
        field_names=storage.snapshots.PROJECTION_FIELDS,
    )
    return [{"record_id": r["record_id"],
             **storage._from_feishu_fields(r["fields"], "holdings_snapshot")}
            for r in records]


def snapshot_hash(rows):
    return digest(sorted(({k: v for k, v in r.items() if k != "validation_error"}
                          for r in rows), key=lambda r: r["record_id"]))


def cash_target(row, amount, rate):
    amount, rate = Decimal(str(amount)), Decimal(str(rate))
    if not amount.is_finite() or not rate.is_finite() or amount < 0 or rate <= 0:
        raise ValueError("invalid MMF amount or FX rate")
    delta = (amount * rate).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) - amount
    updates = {k: float(Decimal(str(getattr(row.nav, k))) + delta)
               for k in ("cash_value", "total_value")}
    new_states = dict(row.field_states)
    new_states.update({k: FieldState.valued(v) for k, v in updates.items()})
    return FreshNavRow(row.nav.model_copy(update=updates), new_states), str(delta)


def prepare(context, source, fx):
    account = context.account
    bundle = source["accounts"][account]
    source_dates = sorted(n["date"] for n in bundle["navs"])
    if not source_dates or len(set(source_dates)) != len(source_dates):
        raise ValueError("invalid source dates")
    start = source_dates[0]
    live_snapshots = snapshots(context, start)
    if snapshot_hash(live_snapshots) != snapshot_hash(bundle["snapshots"]):
        raise ValueError("snapshot evidence changed since audit")
    fresh = read_fresh_nav_rows(context)
    target_dates = [r.nav.date for r in fresh if r.nav.date.isoformat() >= start]
    assert_maintenance_history_evidence(fresh, account=account, target_dates=target_dates)
    if [d.isoformat() for d in target_dates] != source_dates:
        raise ValueError("NAV tail changed; refresh audit before planning")
    working = [r.nav for r in fresh]
    targets = []
    for row in fresh:
        day = row.nav.date.isoformat()
        if day < start:
            continue
        original = next(n for n in bundle["navs"] if n["date"] == day)
        if row.record_id != original["record_id"] or any(
            getattr(row.nav, k) != original[k] for k in BASE_FIELDS
        ):
            raise ValueError(f"base facts changed since audit: {day}")
        if (row.nav.details or {}).get("base_fact_restatement"):
            raise ValueError(f"already restated: {day}")
        mmf = [s for s in live_snapshots if s["as_of"] == day
               and s["asset_id"] == "CNY-MMF" and s["broker"] == "富途"]
        if len(mmf) != 1 or mmf[0]["currency"] != "CNY" or any(
            Decimal(str(mmf[0][k])) != Decimal(str(mmf[0]["quantity"]))
            for k in ("market_value_cny",)
        ) or mmf[0]["price"] != 1 or mmf[0]["cny_price"] != 1:
            raise ValueError(f"unexpected MMF shape: {day}")
        rate = fx["rates"][day]
        adjusted, delta = cash_target(row, mmf[0]["quantity"], rate)
        candidate, dataset = recompute_derived_row(
            context=context, observed=adjusted, working_navs=working,
            run_id=f"cash-restatement:{account}:{day}",
        )
        candidate.details["base_fact_restatement"] = {
            "source_currency": "HKD", "rate": rate, "rate_source": "pbc_central_parity",
            "rate_source_sha256": fx["sha256"], "delta": delta,
            "original_cash_value": row.nav.cash_value,
            "original_total_value": row.nav.total_value,
            "mmf_snapshot_record_id": mmf[0]["record_id"],
            "original_snapshot_preserved": True,
            "rate_policy": "operator_approved_pbc_rate_not_original_valuation_fx",
            "write_reason": "nav_history_base_fact_restatement",
        }
        after = states(adjusted)
        after.update({k: v.envelope() for k, v in
                      maintenance_target_states(adjusted, candidate).items()})
        targets.append({"record_id": row.record_id, "date": day,
                        "before": states(row), "after": after,
                        "cash_flow_fingerprint": dataset.financial_fingerprint})
        working = [candidate if n.record_id == row.record_id else n for n in working]
    return {"account": account, "start": start, "before_series": series(fresh),
            "snapshot_sha256": snapshot_hash(live_snapshots), "targets": targets}


def verify_series(context, plan, *, allow_mixed):
    live = series(read_fresh_nav_rows(context))
    targets = {r["record_id"]: r for r in plan["targets"]}
    if [(r["record_id"], r["date"]) for r in live] != [
        (r["record_id"], r["date"]) for r in plan["before_series"]
    ]:
        raise ValueError("NAV series membership changed")
    for current, original in zip(live, plan["before_series"]):
        target = targets.get(current["record_id"])
        allowed = [original["fields"]]
        if target:
            allowed = [target["before"], target["after"]] if allow_mixed else [target["after"]]
        if current["fields"] not in allowed:
            raise ValueError(f"NAV CAS conflict: {current['date']}")
    return {r["record_id"]: r["fields"] for r in live}


def verify_calculation(context, plan):
    fresh = read_fresh_nav_rows(context)
    working = [r.nav for r in fresh]
    by_id = {r.record_id: r for r in fresh}
    for target in plan["targets"]:
        observed = by_id[target["record_id"]]
        candidate, dataset = recompute_derived_row(
            context=context, observed=observed, working_navs=working,
            run_id=f"cash-restatement:{context.account}:{target['date']}",
        )
        verify_dataset(dataset, target)
        for field in MAINTENANCE_FIELDS:
            if field != "details" and getattr(candidate, field) != getattr(observed.nav, field):
                raise ValueError(f"canonical verification failed: {target['date']} {field}")


def verify_dataset(dataset, target):
    evidence = target["after"]["details"]["value"]["maintenance_provenance"]["cash_flow_dataset"]
    # effect_store_revision is a fresh scan UUID, not a stable ledger revision.
    if not dataset.complete:
        raise ValueError("cash-flow dataset is blocked")
    for field in ("financial_fingerprint", "fx_confirmation_fingerprint"):
        if getattr(dataset, field) != evidence[field]:
            raise ValueError(f"cash-flow evidence changed: {field}")


def execute(context, plan, journal, *, rollback=False):
    current = verify_series(context, plan, allow_mixed=True)
    for row in reversed(plan["targets"]) if rollback else plan["targets"]:
        before, after = (row["after"], row["before"]) if rollback else (row["before"], row["after"])
        if current[row["record_id"]] == after:
            continue
        _append_journal(journal, {"event": "write_intent", "account": context.account,
                                 "date": row["date"], "rollback": rollback})
        context.storage.nav_history.patch_nav_cash_restatement(
            account=context.account, record_id=row["record_id"], expected=before, target=after,
        )
        current = verify_series(context, plan, allow_mixed=True)
        if current[row["record_id"]] != after:
            raise ValueError("write readback mismatch")
        _append_journal(journal, {"event": "readback_verified", "account": context.account,
                                 "date": row["date"], "rollback": rollback})
    if rollback:
        if series(read_fresh_nav_rows(context)) != plan["before_series"]:
            raise ValueError("rollback verification failed")
    else:
        verify_series(context, plan, allow_mixed=False)
        verify_calculation(context, plan)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply", "rollback"))
    parser.add_argument("--source")
    parser.add_argument("--fx")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    path = Path(args.plan)
    if args.mode == "prepare":
        source = json.loads(Path(args.source).read_text())
        fx = json.loads(Path(args.fx).read_text())
        if fx["units"] != "CNY per 1 HKD" or fx["coverage_dates"] != len(fx["rates"]):
            raise ValueError("invalid FX manifest")
        accounts = sorted(source["accounts"])
    else:
        envelope = json.loads(path.read_text())
        if not args.expected_digest or digest(envelope["plan"]) != args.expected_digest:
            raise ValueError("expected plan digest mismatch")
        plan = envelope["plan"]
        accounts = [p["account"] for p in plan]
    contexts = [create_nav_repair_context(account=a) for a in accounts]
    with ExitStack() as stack:
        for c in contexts:
            stack.enter_context(process_lock(account_lock_key(c.account)))
        if args.mode == "prepare":
            plan = [prepare(c, source, fx) for c in contexts]
            # Re-read after all calculations before sealing the backup.
            for c, p in zip(contexts, plan):
                if series(read_fresh_nav_rows(c)) != p["before_series"]:
                    raise ValueError("NAV changed during planning")
            envelope = {"digest": digest(plan), "plan": plan}
            with path.open("x") as handle:
                json.dump(envelope, handle, ensure_ascii=False, allow_nan=False, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            print(json.dumps({"status": "prepared", "digest": envelope["digest"],
                              "rows": sum(len(p["targets"]) for p in plan)}))
        else:
            journal = path.with_suffix(".journal.jsonl")
            for c, p in zip(contexts, plan):
                verify_series(c, p, allow_mixed=True)
                if args.mode == "apply":
                    if snapshot_hash(snapshots(c, p["start"])) != p["snapshot_sha256"]:
                        raise ValueError("historical snapshots changed")
                    for row in p["targets"]:
                        ds = c.portfolio.build_cash_flow_dataset(
                            account=c.account, nav_date=date.fromisoformat(row["date"]),
                            run_id="cash-restatement-check",
                        )
                        verify_dataset(ds, row)
            _append_journal(journal, {"event": "start", "mode": args.mode,
                                     "plan_digest": args.expected_digest})
            for c, p in zip(contexts, plan):
                execute(c, p, journal, rollback=args.mode == "rollback")
            _append_journal(journal, {"event": "completed", "mode": args.mode})
            print(json.dumps({"status": "completed", "mode": args.mode,
                              "plan_digest": args.expected_digest}))


if __name__ == "__main__":
    main()
