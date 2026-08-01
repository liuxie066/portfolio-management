# Gateflow S9 Fix — DeepReview Findings

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S9
- Base: `833fcb0`
- Source review: `docs/reviews/code-review-20260802-071913.md`
- Recorded at: 2026-08-02T07:25:09+08:00
- Status: all accepted findings fixed; pending aggregate re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s9-fix.md`

## Finding Decisions

Both high-severity findings and the medium-severity finding were accepted. No
finding was deferred or rejected.

### DR-S9-01 — Aggregate compatibility promotion — fixed

- `NavRecordService.record_nav(persist=True)` no longer constructs a
  normalized valuation from an unattached `PortfolioValuation`, regardless of
  whether that compatibility payload has holding rows.
- Every persistence attempt, including dry-run persistence, requires a
  compatibility projection derived from an attached
  `NormalizedValuationSnapshot`. Aggregate-only compatibility remains usable
  for nonofficial reporting/calculation but cannot cross the write boundary.
- `from_compatibility_projection()` is now unconditionally reporting-only;
  callers can no longer set `official_eligible=True`.
- The generic normalized builder is also reporting-only. Official eligibility
  is an in-memory capability issued only by the private ValuationService
  factory or the type-checked `from_closed_input(ClosedNavTarget)` factory;
  immutable runtime-context replacement explicitly preserves that capability.
- Consumption is source-scoped as well: normal `record_nav` accepts only the
  ValuationService capability, while `record_closed_nav` verifies the CLOSED
  capability. A manual CLOSED target cannot be routed through the normal entry
  point to recover the removed aggregate bypass.
- NAV regressions now use replayable cash/equity/fund rows rather than promoting
  self-declared aggregate fixtures. A new regression proves an unattached
  aggregate payload fails before NAV or snapshot transport.

### DR-S9-02 — Lossy shared-digest validation — fixed

- When a compatibility projection already carries an attached normalized
  object, any explicit `normalized_valuation` must have the same complete
  normalized digest before compatibility validation or calculation.
- The complete comparison covers source/source provenance, row source,
  component provenance, excluded-zero evidence, and every other normalized
  field that the compatibility model cannot represent.
- V2 persisted-row serialization and digest now have one domain owner in
  `snapshot_contracts.py`; target digest, evidence row digest, SnapshotService,
  and compensation evidence no longer depend on a reverse domain-to-app import
  or parallel row formulas.
- The existing compatibility digest check remains as the second guard and
  still detects mutation of totals, holdings, prices, provenance, warnings, or
  other projected fields.
- A regression constructs a substituted object whose compatibility projection
  matches but whose source and complete digest differ; the write now fails
  before either repository is called.

### DR-S9-03 — Zero quantity writer bypass — fixed

- `HoldingSnapshot` now rejects any quantity that is zero after the canonical
  eight-decimal normalization. The invariant applies to normal persistence,
  direct repository callers, and compensation deserialization.
- Negative nonzero quantities remain valid for short positions; positive and
  negative values that round to zero both fail before transport.
- Regressions cover `0`, `-0`, `0.000000004`, and `-0.000000004`, while the
  minimum nonzero `0.00000001` remains accepted and replayable.

## Bounded Test Scope Corrections

- `tests/test_portfolio.py` was outside the original S9 allowlist. Three legacy
  NAV tests used unattached aggregate `PortfolioValuation` objects for formal
  persistence and therefore correctly failed after DR-S9-01 was closed.
- Only those fixtures were converted to replayable normalized cash/equity rows;
  their NAV/cash-flow assertions and production code paths were not changed.
- The earlier bounded `tests/test_compensation_service.py` fixture correction
  remains unchanged.

## Validation

- Exact S9 suite: `176 passed`.
- Focused fix regression: `54 passed`.
- Updated legacy NAV fixture scope: `15 passed`.
- Full repository suite: `1324 passed`.
- Scoped Ruff for every changed S9 source/test module: passed.
- Python compileall and `git diff --check`: passed.
- No live Feishu/Futu read or write, live schema mutation, business-data
  repair, merge, release, or deployment occurred.

## Residual Boundary

- S10 still owns exact-set write authority, durable prepare, fresh full-slice
  readback, completed evidence, and compensation authority reuse.
- Feishu Number JSON transport precision remains an external canary boundary;
  normalized calculations are exact Decimal values before that transport.
- Same-host source contracts do not provide remote atomicity against external
  Feishu editors.

## Next Gate

Run a fresh DeepReview over the complete S9 diff from `833fcb0`, including this
fix artifact and the original finding artifact. A no-findings result is
required before the scoped S9 commit.
