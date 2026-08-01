# Gateflow S2 Scope Correction — Compatibility Test Surfaces

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S2
- Base: 4af82f6
- Status: accepted test-only scope correction
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/s2-scope-correction.md

## Correction

S2 changed the public holdings mutation contract from an optional broker and
optimistic/cache-based completion to complete identity, explicit ownership, and
fresh readback proof. The following existing regression tests are outside the
original scoped-test list but directly instantiate affected public interfaces or
their storage/cache test doubles. They therefore require compatibility-only
updates in the same slice:

- `tests/test_audit_fixes.py`: supply the now-required broker and a fresh-read
  storage double for the cash deduction path.
- `tests/test_feishu_efficiency.py`: make the injected holdings-index cache
  implement the existing `load_all()` protocol used when an account slice is
  replaced.
- `tests/test_futu_balance_sync_service.py`: accept and apply the canonical
  `HoldingTarget` emitted by the active MMF reconciliation path.
- `tests/test_futu_sync_evidence.py`: accept canonical targets and assert that a
  failed fresh proof becomes an untrusted mismatch rather than an unavailable
  read.
- `tests/test_holdings_preload_minimal.py`: update cache-key expectations and
  fresh-read call counts, and add cache migration/completeness regressions.

## Boundary

These changes do not alter production behavior outside the S2 allowed source
files. They preserve the existing scenarios while making the test doubles obey
the same complete-identity and fresh-proof interfaces as production storage.

## Re-review Contract Correction

The first S2 re-review found that the S1 registry and S2 domain contract still
disagreed on the holdings create key and null-clearability, and that the public
`init_db(initial_cash=...)` compatibility entrypoint had no way to supply the
newly required broker. Closing those findings requires these additional files:

- `src/feishu/contracts/registry.py`: make the registry authoritative for the
  complete holdings create row and null-clearability.
- `docs/schema.md`: regenerate the registry projection.
- `tests/test_feishu_contracts.py` and `tests/test_feishu_client.py`: prove the
  same single/batch transport contract and reject nonclearable null.
- `skill_api.py`: require an explicit broker only when initial cash would be
  written, then use an exact fresh read and the same broker for creation.
- `tests/test_skill_api_boundaries.py`: prove missing broker has zero holding
  writes and an explicit broker is carried end to end.

This correction is limited to the S2 complete-identity and unique-source
contract. It does not authorize a live write, schema change, release, deploy,
or broader compatibility redesign.

## Raw Repair And Completion-Proof Correction

The fourth S2 re-review found two remaining callers that bypassed the accepted
mutation semantics. Closing them requires the following directly affected
workflow and regression surfaces:

- `src/app/holdings_workflow_service.py`: construct the immutable raw repair
  contract from the exact row confirmed by the operator instead of passing a
  naked record ID and dictionary.
- `tests/test_holdings_workflow_service.py`: make the workflow double accept
  and inspect that contract.
- `tests/test_holding_event_inbox_service.py`: keep the event-worker no-write
  assertion compatible with the same public boundary.

The cash-flow and compensation production/test files were already within S2's
allowed scope. This correction adds no new field authority: raw repair remains
restricted to the registry-backed reconciliation allowlist, while normal
typed mutations continue to use `HoldingTarget`.
