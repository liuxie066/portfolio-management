# Gateflow Implementation Artifact — Holdings Case Precondition Identity

## Gate

- Gate: implementation slice and code review
- Work unit: `holdings-case-precondition-identity`
- Slice: `S1-S4` (one cohesive contract, persistence, integration, and regression slice)
- Artifact path: `docs/gateflow/holdings-case-precondition-identity/implementation-s1-s4.md`
- Review artifact: `docs/reviews/code-review-20260801-104142.md`
- Completion status: implementation and code review passed; waiting at the separately authorized accepted-slice commit boundary

## Objective and Outcome

Replace the old whole-record holdings case precondition with a field-specific,
versioned precondition while preserving `holdings-case.v1` semantic keys. Known
legacy rows can now migrate in place during their next materialization without
blocking a repaired field's closure or producing migration receipts.

The concrete `sy / SPY` regression now succeeds: changing `asset_type` from
`us_fund` to `exchange_fund` and correcting the display name closes only the
obsolete name case. Unchanged invalid timestamp cases keep their keys and open
states, receive one audit-only migration, and do not produce discovery,
supersession, or closure receipts.

## Changed Files

- `src/app/holding_case_contract.py`
  - owns the v2 digest builder, legacy comparator, confirmation scope, contract
    recognition, state allowlist, and one-way compatibility predicate.
- `src/app/holdings_workflow_service.py`
  - builds field-specific preconditions; uses the shared scope predicate in
    planning, direct resolve/apply, and evidence-outage confirmation handling;
    leaves the v1 case-key payload unchanged.
- `src/app/operation_state_store.py`
  - migrates eligible rows atomically in both normal materialization and
    combined materialize-and-prepare; rewrites only a validated keep scope;
    writes `precondition_contract_migrated` audit events and no migration receipt.
- `tests/test_holding_case_contract.py`
  - covers dependency groups, version recognition, transition direction,
    relevant mismatch, semantic mismatch, keep scope, and in-flight rejection.
- `tests/test_holdings_workflow_store.py`
  - covers pending/keep migrations, rejection rollback, idempotency, receipt
    silence, and atomic migration-to-applying.
- `tests/test_holdings_workflow_service.py`
  - covers the case-key golden, exact SPY repair, direct apply/resolve migration,
    no holdings write, exact receipt delta, and repeat zero-delta behavior.
- `tests/test_holding_event_inbox_service.py`
  - covers one event transaction that silently migrates an unchanged legacy
    case while closing exactly the repaired case.
- `tests/test_holdings_nav_preflight_service.py`
  - covers read-only dry-run compatibility, formal in-place keep migration,
    receipt silence, provider-outage compatibility, and dependency drift rejection.

## Contract and State Decisions

- `CASE_CONTRACT_VERSION` and its hash payload remain byte-identical. The
  captured missing-currency golden remains
  `bb54e18113db476fe42491aa1a489a3cc2a85590c0214a4a3b978330f6f0b64f`.
- New preconditions use
  `holdings-precondition.v2:<sha256-of-canonical-payload>`.
- Every field includes record id, stable holding identity, field, and current
  field value. Only `currency` and `asset_class` additionally include raw
  `asset_type`.
- An unprefixed 64-character lowercase SHA-256 is the only recognized legacy
  shape. Migration also requires unchanged semantic facts and an eligible state.
- Relevant-field migration requires the stored digest to equal the legacy
  comparator built from the fresh current record.
- A `resolved_keep` migrates only when its old confirmation scope exactly
  matches the stored legacy row; the scope is then atomically rewritten to v2.
- `applying`, `failed_retryable`, and `apply_outcome_unknown` reject migration.
- Unknown prefixes and all v2-to-different-v2 transitions remain hard collisions.

## Side Effects and Invariants

- Migration does not enqueue, reset, or modify an operation receipt.
- Migration, evidence refresh/reopen, apply preparation, event completion, and
  existing receipt writes share the surrounding SQLite transaction.
- Notify and event listener paths never patch holdings.
- Dry-run NAV preflight recognizes a valid legacy keep without mutating it.
- Formal preflight migrates the row and scope before returning success.
- A compatible keep during provider outage is read-only and remains legacy
  until an evidence-complete materialization.

## Validation

- Focused holdings suites: 126 passed.
- Full repository baseline: 1038 passed.
- Python compile check for `src` and `tests`: passed.
- `git diff --check`: passed.
- DeepReview: no material findings.

## Documentation Decision

No public schema, CLI, HTTP, or holdings documentation change is needed. The v2
token is a private workflow contract that is visible only as an opaque value in
diagnostic case JSON. The rollout compatibility boundary stays in Gateflow
artifacts and must be repeated in release notes if a later release is authorized.

## Residual Risks and Uncovered Areas

- **Assigned to later authorized rollout:** migrated v2 rows cannot be consumed
  by the preceding binary. A remote rollout must use the suspended canary,
  paired operation-database backup, outbox hold, and forward-only gate from the
  accepted plan.
- **Accepted within current slice:** for non-dependent fields, an old broad
  digest cannot reconstruct the historical raw `asset_type`. Exact semantic
  identity, known digest shape, eligible state, and one-way migration bound this
  compatibility risk.
- **Outside this work unit:** live Feishu/production canary, timestamp or tag
  cleanup, listener redesign, release, and remote upgrade were not performed.

## Next Entry Point

`accepted slice commit`, after explicit commit authorization. Push, draft PR,
release, and remote upgrade remain separate later boundaries.
