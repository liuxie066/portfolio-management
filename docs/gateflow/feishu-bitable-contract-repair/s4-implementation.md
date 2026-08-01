# Gateflow S4 Implementation — Cash-flow Row Contracts

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S4
- Base: `8ef9c8c`
- Recorded at: 2026-08-02T02:57:36+08:00
- Status: accepted after DeepReview and re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s4-implementation.md`

## Scope

Production changes:

- `src/domain/cash_flow_contracts.py`
- `src/models.py`
- `src/feishu/repositories/cash_flow_repository.py`
- `src/feishu/_cash_flow_mixin.py`
- `src/app/cash_flow_summary_service.py`
- `src/app/cash_flow_effect_service.py`

Regression changes:

- `tests/test_cash_flow_contracts.py`
- `tests/test_cash_flow_summary_service.py`
- `tests/test_cash_flow_effect_service.py`
- `tests/test_feishu_storage.py`
- `tests/test_nav_cashflow_perf_minimal.py`
- `tests/test_portfolio.py`

The final two files are the test-only compatibility surfaces recorded in
`docs/gateflow/feishu-bitable-contract-repair/s4-scope-correction.md`.

## Implemented Contract

- Added immutable `RawCashFlowRecord`, `ManualCashFlowFacts`,
  `CompletedCashFlowFacts`, and field-level `CashFlowValidationIssue`.
- Manual facts require a valid date, nonblank account and broker, a supported
  currency, and a finite nonzero cent-precision `Decimal` amount.
- Completed facts additionally prove sign-consistent flow type, positive finite
  exchange rate, exact cent-rounded CNY formula, canonical persisted dedup key,
  and nonblank source. CNY requires rate 1; foreign rows have no amount fallback.
- The `CashFlow` Pydantic object is now an explicitly partial transport model.
  Exact/list reads retain missing currency, flow type, source, and dedup state
  instead of inventing `CNY`, `DEPOSIT`, zero, or blank identity defaults.
- Cash-flow projections are derived from the S1 registry and include every
  raw/digest/readback field. The only live-schema fallback removes the optional
  `updated_at` projection.
- Direct create accepts only revalidated `CompletedCashFlowFacts`. Replay exact
  reads validate the persisted row before returning it, and cache mutation uses
  only validated CNY amount.
- Loaded aggregate caches receive a validated incremental append. An unloaded
  disk cache is invalidated instead of receiving an unknowably partial append.
  Reconcile updates and confirmed deletes invalidate memory and disk scope; a
  delete resolves the old account from an exact pre-delete read.
- Aggregate preload validates every raw row before publishing any cache. A
  missing/invalid date, incomplete system fields, formula mismatch, or invalid
  identity leaves the previous cache untouched and contributes to no total.
- Summary and cash-holding-effect services reuse the completed contract.
  Effect fingerprints now include CNY amount, rate, dedup, source, remark, and
  updated-at so metadata or observed-key changes remain visible.
- Replay additionally proves that the exact remote row still owns the requested
  observed dedup key. Stale local mappings are evicted and re-queried rather
  than suppressing a different transaction.
- Loaded-cache increments use the same cent-quantized Decimal addition as fresh
  rebuilds. The canonical dedup formatter preserves the complete historical
  float-text representation, including scientific notation, while deriving it
  from validated Decimal facts.

## Validation

- Focused review fault suite: `45 passed`.
- Final S4 scoped and compatibility suite: `175 passed`.
- Full repository suite: `1204 passed`.
- `python3.12 -m compileall` for source and touched tests: passed.
- Ruff for touched production files and clean/new test surfaces: passed. The
  legacy monolithic storage test retains unrelated pre-existing lint findings.
- `git diff --check`: passed.
- No live Feishu or Futu request was made.

## Expected Assertions Closed

- Missing currency/flow type/dedup/source remain missing on exact reads and
  cannot become completed facts.
- Blank identity, zero, NaN, Inf, invalid date, sign mismatch, invalid rate,
  CNY formula mismatch, and tampered dedup fail closed.
- Incomplete foreign direct add performs zero lookup, write, or cache mutation.
- Missing-date aggregate refresh raises a blocker and preserves the old cache.
- Remark-only, source-only, and observed dedup changes alter the effect-source
  revision; a tampered dedup becomes blocked.
- Existing persisted dedup keys for cent-normalized float-shaped amounts remain
  stable.

## Documentation Decision

No generated schema metadata changed in S4. The durable implementation and
scope-correction artifacts are sufficient; `docs/schema.md` remains untouched.

## Residual Risks

- Duplicate expected-dedup groups, fresh reconcile/readback sequencing, and
  local FX-confirmation binding are covered by approved S5.
- Official NAV still has a stale-cache/double-scan boundary; it is covered by
  approved S6 and is not weakened by S4.
- Existing live business-row conformance is unknown because this slice made no
  business-data read. It remains assigned to the approved, separately
  authorized pre-production read-only conformance audit.
- External edits racing a direct create remain the already-classified
  cross-system concurrency risk; stronger coordination belongs to the later
  concurrency work unit recorded in the accepted plan.

No residual risk is unclassified.

## Review Closure

- Initial DeepReview: `docs/reviews/code-review-20260802-025828.md`.
- Fix decisions: `docs/gateflow/feishu-bitable-contract-repair/s4-fix.md`.
- Accepted re-review: `docs/reviews/code-review-20260802-031618.md`.
- Gate decision: `docs/gateflow/feishu-bitable-contract-repair/s4-rereview.md`.

## Next Gate

Commit accepted S4, then start S5.
