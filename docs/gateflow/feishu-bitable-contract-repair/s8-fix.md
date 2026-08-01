# Gateflow S8 Fix — DeepReview Findings

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S8
- Base: `6cbe411`
- Source review: `docs/reviews/code-review-20260802-061448.md`
- Recorded at: 2026-08-02T06:37:33+08:00
- Status: all accepted findings fixed; pending aggregate re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s8-fix.md`

## Finding Decisions

All three high-severity findings were accepted. No finding was deferred or
rejected.

### DR-S8-01 — Finality downgrade — fixed

- `maintenance_details()` now preserves the observed valuation finality and
  top-level valuation `run_id`; a derived-only maintenance run cannot replace a
  valid final/manual/initial/closed classification with `maintenance`.
- Maintenance writer, reason, run id, valuation timestamp, and stable cash-flow
  dataset fingerprints are recorded under a separate
  `maintenance_provenance` object. Volatile `fetched_at` is deliberately not
  copied into the deterministic repair target.
- A legacy row without finality receives only the non-final maintenance
  classification. S8 does not infer authority to promote it to
  `nav-repair/final`.
- Finality vocabulary and payload validation now have one pure domain owner in
  `src/domain/nav_finality_contract.py`. Both `evaluate_nav_finality()` and the
  final NAV invariant consume that validator.
- Regression proves that an existing daily finality and valuation run id remain
  unchanged and `evaluate_nav_finality()` remains eligible after details
  maintenance.

### DR-S8-02 — Incomplete or ambiguous history dependencies — fixed

- One state-aware maintenance preflight now validates the complete fresh
  account history before recomputation or journal creation.
- Every target requires all immutable base decomposition fields as finite
  `Value` states. The valuation projection itself repeats this requirement and
  never defaults fund or regional values to zero.
- Duplicate account/date rows are rejected. Every non-target row that can act
  as predecessor, period base, or inception base must have finite total,
  shares, and positive NAV evidence.
- Dependency identity and state are included in the canonical plan digest and
  journal. Apply/readback re-read the complete account history and abort before
  further writes if dependency evidence changes.
- Canonical recomputation consumes the already fresh working history as an
  explicit immutable snapshot. It no longer injects hypothetical candidates
  into the shared NAV cache, so dry-run planning cannot mutate later read
  authority.
- Patch and backfill regressions cover missing fund/region evidence, duplicate
  predecessors, missing predecessor shares, and dependency drift with zero
  writes.

### DR-S8-03 — Partial CAS/readback comparison — fixed

- Every journal row stores both complete original and complete desired
  maintenance states in addition to the changed-field write subset.
- Apply CAS requires the complete live state to equal complete original or
  complete desired state. Fresh readback requires the complete desired state.
  Rollback still writes only the changed fields and requires complete original
  readback.
- Journal parsing validates the relationship between changed subsets and
  complete states and recomputes the canonical plan digest before use.
- A write-attempt followed by readback failure is reported as `partial` with
  `partial_write_possible=true`; a pre-write conflict remains `failed` with
  zero writes.
- Regressions cover drift in an originally unchanged derived field both before
  write and during readback, partial apply/resume, and restricted rollback.

## Validation

- Exact S8 suite: `89 passed`.
- Full repository suite: `1297 passed`.
- Scoped Ruff: passed for every changed S8 source/test module other than
  `skill_api.py`; that file has 13 pre-existing whole-file E402/F541 findings,
  while the two changed defaults/documentation lines introduce none.
- Python compile and `git diff --check`: passed.
- No live Feishu/Futu read or write, live schema mutation, repair execution,
  merge, release, or deployment occurred.

## Residual Boundary

- S8 has no authority to promote a legacy row to final.
- Missing and null remain distinct in the journal, but Feishu exposes only a
  clear operation for restoration; fresh readback remains authoritative.
- Same-host locks plus full fresh CAS/readback detect observed external drift
  but cannot create a cross-host atomic transaction after the last readback.

## Next Gate

Run a fresh DeepReview over the complete S8 diff from `6cbe411`, including this
fix artifact and the original finding artifact. A no-findings result is
required before the scoped S8 commit.
