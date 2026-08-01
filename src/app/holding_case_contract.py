"""Pure identity contracts for durable holdings reconciliation cases."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


PRECONDITION_CONTRACT_VERSION = "holdings-precondition.v2"
LEGACY_PRECONDITION_CONTRACT_VERSION = "holdings-precondition.v1"
PRECONDITION_EXACT = "exact"
PRECONDITION_LEGACY_MIGRATABLE = "legacy_migratable"
PRECONDITION_REJECT = "reject"

_V2_PREFIX = f"{PRECONDITION_CONTRACT_VERSION}:"
_LEGACY_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ASSET_TYPE_DEPENDENT_FIELDS = frozenset({"currency", "asset_class"})
_LEGACY_MIGRATABLE_STATES = frozenset(
    {
        "pending_apply",
        "pending_confirmation",
        "pending_manual_edit",
        "resolved_keep",
        "resolved_accept",
        "resolved_external",
        "superseded",
    }
)
_SEMANTIC_FIELDS = (
    "case_key",
    "record_id",
    "identity",
    "field",
    "kind",
    "current",
    "proposed",
    "authority_id",
    "policy_version",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_case_precondition(
    *,
    record_id: str,
    identity: Mapping[str, Any],
    field: str,
    current: Any,
    canonical_record: Mapping[str, Any],
) -> dict[str, str]:
    """Build the field-specific v2 digest and its current-record v1 comparator."""

    normalized_identity = dict(identity)
    legacy_payload = {
        "record_id": record_id,
        "identity": normalized_identity,
        "field": field,
        "current": current,
        "authority_inputs": {
            key: canonical_record.get(key)
            for key in ("asset_id", "asset_type", "account", "broker")
        },
    }
    authority_inputs = {}
    if field in _ASSET_TYPE_DEPENDENT_FIELDS:
        authority_inputs["asset_type"] = canonical_record.get("asset_type")
    payload = {
        "record_id": record_id,
        "identity": normalized_identity,
        "field": field,
        "current": current,
        "authority_inputs": authority_inputs,
    }
    return {
        "case_precondition_digest": f"{_V2_PREFIX}{_digest(payload)}",
        "legacy_case_precondition_digest": _digest(legacy_payload),
    }


def confirmation_scope(case: Mapping[str, Any]) -> str:
    return _digest(
        {
            "case_key": case["case_key"],
            "case_precondition_digest": case["case_precondition_digest"],
            "authority_id": case.get("authority_id"),
            "policy_version": case["policy_version"],
        }
    )


def precondition_contract(digest: Any) -> str | None:
    normalized = str(digest or "").strip()
    if normalized.startswith(_V2_PREFIX) and _LEGACY_DIGEST_RE.fullmatch(
        normalized[len(_V2_PREFIX) :]
    ):
        return PRECONDITION_CONTRACT_VERSION
    if _LEGACY_DIGEST_RE.fullmatch(normalized):
        return LEGACY_PRECONDITION_CONTRACT_VERSION
    return None


def classify_precondition_transition(
    stored: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> str:
    """Classify one same-key transition without mutating durable state."""

    stored_digest = str(stored.get("case_precondition_digest") or "").strip()
    candidate_digest = str(candidate.get("case_precondition_digest") or "").strip()
    if stored_digest == candidate_digest:
        return PRECONDITION_EXACT
    if (
        precondition_contract(stored_digest)
        != LEGACY_PRECONDITION_CONTRACT_VERSION
        or precondition_contract(candidate_digest) != PRECONDITION_CONTRACT_VERSION
        or str(stored.get("state") or "") not in _LEGACY_MIGRATABLE_STATES
    ):
        return PRECONDITION_REJECT
    if any(
        _canonical_json(stored.get(field)) != _canonical_json(candidate.get(field))
        for field in _SEMANTIC_FIELDS
    ):
        return PRECONDITION_REJECT

    field = str(candidate.get("field") or "")
    if field in _ASSET_TYPE_DEPENDENT_FIELDS and stored_digest != str(
        candidate.get("legacy_case_precondition_digest") or ""
    ):
        return PRECONDITION_REJECT

    if stored.get("state") == "resolved_keep":
        resolution = dict(stored.get("resolution") or {})
        if resolution.get("confirmation_scope") != confirmation_scope(stored):
            return PRECONDITION_REJECT
    return PRECONDITION_LEGACY_MIGRATABLE


__all__ = [
    "LEGACY_PRECONDITION_CONTRACT_VERSION",
    "PRECONDITION_CONTRACT_VERSION",
    "PRECONDITION_EXACT",
    "PRECONDITION_LEGACY_MIGRATABLE",
    "PRECONDITION_REJECT",
    "build_case_precondition",
    "classify_precondition_transition",
    "confirmation_scope",
    "precondition_contract",
]
