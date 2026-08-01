from __future__ import annotations

import pytest

from src.app.holding_case_contract import (
    PRECONDITION_EXACT,
    PRECONDITION_LEGACY_MIGRATABLE,
    PRECONDITION_REJECT,
    build_case_precondition,
    classify_precondition_transition,
    confirmation_scope,
)


IDENTITY = {"asset_id": "SPY", "account": "sy", "broker": "IBKR"}


def _record(asset_type: str) -> dict[str, object]:
    return {
        **IDENTITY,
        "asset_name": "标普500ETF-SPDR",
        "asset_type": asset_type,
        "created_at": "2026/03/30",
        "currency": "USD",
        "asset_class": "美国资产",
    }


def _case(
    *,
    field: str,
    current: object,
    precondition: str,
    legacy_precondition: str,
    state: str = "pending_manual_edit",
) -> dict[str, object]:
    return {
        "case_key": f"case-{field}",
        "record_id": "rec-spy",
        "identity": dict(IDENTITY),
        "field": field,
        "kind": "invalid" if field == "created_at" else "conflict",
        "current": current,
        "proposed": None if field == "created_at" else "CNY",
        "authority_id": None if field == "created_at" else "asset_type:us_stock",
        "policy_version": "holdings-validation.v1",
        "case_precondition_digest": precondition,
        "legacy_case_precondition_digest": legacy_precondition,
        "state": state,
    }


@pytest.mark.parametrize(
    "field,current",
    [
        ("asset_name", "标普500ETF-SPDR"),
        ("asset_type", "us_fund"),
        ("created_at", "2026/03/30"),
        ("updated_at", "2026/03/30"),
        ("tag", "[]"),
        ("industry", "金融"),
        ("quantity", "1"),
        ("__record__:duplicate_identity", {"record_ids": ["one", "two"]}),
    ],
)
def test_unrelated_asset_type_change_keeps_non_dependent_v2_precondition_stable(
    field, current
):
    before = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field=field,
        current=current,
        canonical_record=_record("us_fund"),
    )
    after = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field=field,
        current=current,
        canonical_record=_record("exchange_fund"),
    )

    assert before["case_precondition_digest"].startswith(
        "holdings-precondition.v2:"
    )
    assert before["case_precondition_digest"] == after["case_precondition_digest"]
    assert (
        before["legacy_case_precondition_digest"]
        != after["legacy_case_precondition_digest"]
    )


@pytest.mark.parametrize("field,current", [("currency", "USD"), ("asset_class", "美国资产")])
def test_asset_type_change_invalidates_dependent_precondition(field, current):
    before = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field=field,
        current=current,
        canonical_record=_record("us_fund"),
    )
    after = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field=field,
        current=current,
        canonical_record=_record("exchange_fund"),
    )

    assert before["case_precondition_digest"] != after["case_precondition_digest"]
    assert (
        before["legacy_case_precondition_digest"]
        != after["legacy_case_precondition_digest"]
    )


def test_legacy_timestamp_transition_allows_only_known_one_way_contract_change():
    before = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field="created_at",
        current="2026/03/30",
        canonical_record=_record("us_fund"),
    )
    after = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field="created_at",
        current="2026/03/30",
        canonical_record=_record("exchange_fund"),
    )
    stored = _case(
        field="created_at",
        current="2026/03/30",
        precondition=before["legacy_case_precondition_digest"],
        legacy_precondition=before["legacy_case_precondition_digest"],
    )
    candidate = _case(
        field="created_at",
        current="2026/03/30",
        precondition=after["case_precondition_digest"],
        legacy_precondition=after["legacy_case_precondition_digest"],
    )

    assert (
        classify_precondition_transition(stored, candidate)
        == PRECONDITION_LEGACY_MIGRATABLE
    )
    assert (
        classify_precondition_transition(
            {**stored, "case_precondition_digest": "unknown:v3"}, candidate
        )
        == PRECONDITION_REJECT
    )
    assert (
        classify_precondition_transition(
            {**stored, "case_precondition_digest": candidate["case_precondition_digest"]},
            candidate,
        )
        == PRECONDITION_EXACT
    )
    assert (
        classify_precondition_transition(
            {
                **stored,
                "case_precondition_digest": "holdings-precondition.v2:"
                + "0" * 64,
            },
            candidate,
        )
        == PRECONDITION_REJECT
    )


def test_legacy_currency_transition_requires_current_legacy_digest_match():
    before = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field="currency",
        current="USD",
        canonical_record=_record("us_fund"),
    )
    after = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field="currency",
        current="USD",
        canonical_record=_record("exchange_fund"),
    )
    stored = _case(
        field="currency",
        current="USD",
        precondition=before["legacy_case_precondition_digest"],
        legacy_precondition=before["legacy_case_precondition_digest"],
        state="pending_confirmation",
    )
    candidate = _case(
        field="currency",
        current="USD",
        precondition=after["case_precondition_digest"],
        legacy_precondition=after["legacy_case_precondition_digest"],
        state="pending_confirmation",
    )

    assert classify_precondition_transition(stored, candidate) == PRECONDITION_REJECT


@pytest.mark.parametrize(
    "inflight_state",
    ["applying", "failed_retryable", "apply_outcome_unknown"],
)
def test_resolved_keep_requires_exact_legacy_scope_and_rejects_inflight_state(
    inflight_state,
):
    digests = build_case_precondition(
        record_id="rec-spy",
        identity=IDENTITY,
        field="created_at",
        current="2026/03/30",
        canonical_record=_record("us_fund"),
    )
    stored = _case(
        field="created_at",
        current="2026/03/30",
        precondition=digests["legacy_case_precondition_digest"],
        legacy_precondition=digests["legacy_case_precondition_digest"],
        state="resolved_keep",
    )
    stored["resolution"] = {"confirmation_scope": confirmation_scope(stored)}
    candidate = _case(
        field="created_at",
        current="2026/03/30",
        precondition=digests["case_precondition_digest"],
        legacy_precondition=digests["legacy_case_precondition_digest"],
    )

    assert (
        classify_precondition_transition(stored, candidate)
        == PRECONDITION_LEGACY_MIGRATABLE
    )
    assert (
        classify_precondition_transition(
            {**stored, "resolution": {"confirmation_scope": "stale"}}, candidate
        )
        == PRECONDITION_REJECT
    )
    assert (
        classify_precondition_transition(
            {**stored, "state": inflight_state}, candidate
        )
        == PRECONDITION_REJECT
    )
