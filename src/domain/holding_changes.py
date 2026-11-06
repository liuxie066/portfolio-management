"""Pure projection and comparison of successive raw holdings observations."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from src.domain.holdings import RawHoldingRecord
from src.models import AssetType
from src.domain.holding_currencies import is_known_holding_currency

POLICY_VERSION = "non-futu-holdings-observation.v1"
SUPPORTED_TYPES = frozenset({
    "a_stock", "hk_stock", "us_stock", "exchange_fund", "otc_fund",
    "us_fund", "cn_fund", "hk_fund", "fund",
})
IDENTITY_FIELDS = ("asset_id", "account", "broker")
MEASURE_FIELDS = ("asset_type", "currency")


def identity(snapshot: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(snapshot.get(key) or "" for key in IDENTITY_FIELDS)


def project_record(record: RawHoldingRecord, accounts: frozenset[str]) -> dict[str, Any]:
    # Import the existing pure rules; no reconciliation service/provider is used.
    from src.domain.holding_values import (
        canonical_holding_number as _canonical_number,
        holding_decimal as _decimal, holding_text as _text, holding_broker_scope,
    )

    raw = record.raw_fields
    snapshot = {key: _text(raw.get(key)) for key in (*IDENTITY_FIELDS, *MEASURE_FIELDS)}
    snapshot["asset_type"] = (snapshot["asset_type"] or "").lower()
    snapshot["currency"] = (snapshot["currency"] or "").upper()
    snapshot["asset_name"] = _text(raw.get("asset_name"))
    amount = _decimal(raw.get("quantity"))
    snapshot["quantity"] = _canonical_number(amount) if amount is not None else None
    cost = _decimal(raw.get("avg_cost"))
    snapshot["avg_cost"] = _canonical_number(cost) if cost is not None else None
    account, kind = snapshot["account"], snapshot["asset_type"]
    scope = holding_broker_scope(snapshot["broker"])
    recognized_types = {item.value for item in AssetType}
    excluded = bool(
        (account and account not in accounts)
        or scope == "futu"
        or (kind in recognized_types and kind not in SUPPORTED_TYPES)
    )
    problem = None
    if not excluded:
        if any(not snapshot.get(key) for key in (*IDENTITY_FIELDS, *MEASURE_FIELDS)) or scope != "non_futu":
            problem = "incomplete_identity"
        elif kind not in SUPPORTED_TYPES:
            problem = "invalid_asset_type"
        elif not is_known_holding_currency(snapshot["currency"]):
            problem = "invalid_currency"
        elif amount is None or amount < 0:
            problem = "invalid_quantity"
    return {"snapshot": snapshot, "eligible": not excluded and not problem,
            "excluded": excluded, "reason": problem}


def _positive(snapshot: dict[str, Any] | None) -> bool:
    try:
        return bool(snapshot and Decimal(str(snapshot.get("quantity"))) > 0)
    except (InvalidOperation, TypeError, ValueError):
        return False


def observe(
    records: Iterable[RawHoldingRecord],
    previous: dict[str, dict[str, Any]],
    *, accounts: frozenset[str], now: str,
    duplicate_groups: dict[tuple[str, str, str], tuple[RawHoldingRecord, ...]],
    initialize: bool = False,
) -> dict[str, Any]:
    """Return a settlement plan; missing rows need two complete reads >=2s apart."""
    from datetime import datetime

    records = list(records)
    raw = {item.record_id: item for item in records}
    if len(raw) != len(records) or any(not key for key in raw):
        raise ValueError("holdings observation has duplicate or missing record_id")
    projected = {key: project_record(row, accounts) for key, row in raw.items()}
    duplicate_ids = set()
    for group in duplicate_groups.values():
        ids = {row.record_id for row in group}
        if any(projected[key]["eligible"] or key in previous for key in ids):
            duplicate_ids.update(ids)
    states, changes, errors = {}, [], []
    covered, deleted, protected = set(), set(), set()

    def change(kind, rid, before, after, old, **extra):
        changes.append({"kind": kind, "record_id": rid, "before": before,
                        "after": after, "previous_observed_at": old.get("observed_at"),
                        "observed_at": now, "after_gap": old.get("state") == "gap", **extra})

    # Absence is checked against ALL raw rows, never an eligible-only projection.
    for rid, old in previous.items():
        if rid in raw:
            continue
        row = {**old}
        if old["state"] in {"deleted", "out_of_scope"}:
            states[rid] = row
            continue
        first = old.get("missing_since")
        confirmed = first and (datetime.fromisoformat(now) - datetime.fromisoformat(first)).total_seconds() >= 2
        if confirmed:
            row.update(state="deleted", missing_since=None, reason=None)
            deleted.add(rid)
            covered.add(rid)
        else:
            row.update(state="missing_candidate", missing_since=first or now, reason="record_absent")
            protected.add(identity(old.get("snapshot") or old.get("current") or {}))
        states[rid] = row

    # Hold a replacement's first notification until the old physical row is confirmed absent.
    missing_by_identity = defaultdict(list)
    for rid in previous:
        if rid not in raw and previous[rid]["state"] not in {"deleted", "out_of_scope"}:
            missing_by_identity[identity(previous[rid].get("snapshot") or previous[rid].get("current") or {})].append(rid)
    replaced = set()
    for rid, item in projected.items():
        old = previous.get(rid, {})
        before = old.get("snapshot")
        after = item["snapshot"]
        group_identity = identity(after)
        if item["excluded"]:
            if before and all(after.get(key) for key in IDENTITY_FIELDS):
                covered.add(rid)
            if before and old.get("state") not in {"deleted", "out_of_scope"}:
                change("out_of_scope", rid, before, after, old)
                states[rid] = {"state": "out_of_scope", "snapshot": after, "observed_at": now,
                               "reason": None, "missing_since": None}
            elif old:
                states[rid] = old
            continue
        problem = "duplicate_identity" if rid in duplicate_ids else item["reason"]
        if group_identity in protected:
            problem = problem or "missing_group_member"
        if problem:
            if problem == "duplicate_identity" and group_identity not in protected:
                covered.add(rid)
            errors.append({"record_id": rid, "reason": problem})
            states[rid] = {"state": "gap", "snapshot": before, "observed_at": old.get("observed_at"),
                           "reason": problem, "missing_since": None, "current": after}
            continue
        covered.add(rid)
        replacement_ids = missing_by_identity.get(group_identity, []) if not before else []
        if len(replacement_ids) == 1 and replacement_ids[0] in deleted:
            predecessor = replacement_ids[0]
            change("record_replaced", rid, previous[predecessor].get("snapshot"), after,
                   previous[predecessor], previous_record_id=predecessor)
            replaced.add(predecessor)
        elif not initialize:
            if not before or old.get("state") == "deleted":
                if _positive(after):
                    change("first_observed", rid, None, after, old)
            elif old.get("state") == "out_of_scope":
                change("back_in_scope", rid, before, after, old)
            elif identity(before) != identity(after):
                change("identity_changed", rid, before, after, old)
            elif any(before.get(key) != after.get(key) for key in MEASURE_FIELDS):
                change("measure_changed", rid, before, after, old)
            elif before.get("quantity") != after.get("quantity"):
                kind = "quantity_zero" if not _positive(after) else "quantity_restored" if not _positive(before) else "quantity_changed"
                change(kind, rid, before, after, old)
        states[rid] = {"state": "active", "snapshot": after, "observed_at": now,
                       "reason": None, "missing_since": None}
    for rid in deleted - replaced:
        if previous[rid].get("snapshot"):
            change("record_deleted", rid, previous[rid]["snapshot"], None, previous[rid])
    # Do not let a first absence settle the duplicate case of any related member.
    for rid in list(covered):
        row = states.get(rid, {})
        snap = row.get("snapshot") or row.get("current") or {}
        if identity(snap) in protected:
            covered.discard(rid)
    return {"rows": states, "changes": changes, "problems": errors,
            "covered_record_ids": sorted(covered), "proven_deleted_ids": sorted(deleted),
            "has_missing_candidates": any(row["state"] == "missing_candidate" for row in states.values())}
