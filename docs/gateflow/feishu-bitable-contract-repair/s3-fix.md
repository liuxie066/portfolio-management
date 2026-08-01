# Gateflow S3 Fix — DeepReview Findings

## Gate Metadata

- Gate: fix
- Slice: S3
- Work unit: feishu-bitable-contract-repair
- Review artifacts:
  - `docs/reviews/code-review-20260802-021205.md`
  - `docs/reviews/code-review-20260802-021823.md`
- Recorded at: 2026-08-02T02:14:47+08:00
- Status: all findings fixed; final re-review accepted
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s3-fix.md`

## Finding Decisions

### DR-S3-01 — accepted — fixed

- Provider currency validity now reuses the project `Currency` enum after
  normalization; the Futu adapter no longer defines a wider three-letter
  shape-only validity rule.
- Explicit `CNH` normalizes to canonical `CNY`; unknown placeholders such as
  `NAN`, `BAD`, malformed text, and unsupported codes fail complete-slice
  validation before a fresh holdings read or mutation.

### DR-S3-02 — accepted — fixed

- Existing holding currency validation now runs before the source-target
  branch. It protects matched updates, explicit closes, and holdings absent
  from the authoritative source slice.
- An invalid historical currency therefore cannot be carried into an automatic
  zero target; the row remains untouched for the controlled repair workflow.

### DR-S3-03 — accepted — fixed

- Eligible provider rows now reuse `_target_descriptor()` during complete
  source validation.
- Unsupported stock markets are classified as source validation failures before
  any holdings diff/fresh read and no longer appear as position-diff failures.

### DR-S3-RR-01 — accepted — fixed

- Provider numeric parsing now feeds trimmed source text directly to `Decimal`.
  It no longer deletes commas or repairs malformed machine data.
- Malformed quantity and average-cost strings stay invalid and block the whole
  source slice before any holdings read or write.

### DR-S3-RR-02 — accepted — fixed

- Currency presence no longer uses truthiness. Only `None`, blank text, and the
  declared `N/A` sentinel are missing; numeric zero and boolean false remain
  explicit invalid facts and fail source validation.

### DR-S3-RR-03 — accepted — fixed

- `FutuPositionSnapshot.position_side` now defaults to `N/A`, so a custom
  provider cannot acquire implicit LONG authority.
- `_market_from_code()` no longer guesses US when both code prefix and provider
  market are absent. Quote classification skips that unrouteable row and lets
  complete-slice source validation report the missing fact before diffing.

## Regression Evidence

- S3 scoped suite after initial fixes: `123 passed`.
- S3 plus adjacent holdings-reconciliation suite after re-review fixes:
  `139 passed`.
- Python compileall and `git diff --check`: passed.
- Fault injection covers shape-valid unknown currencies, absent invalid
  existing rows, and unsupported stock markets with zero write authority.
- No live Futu or Feishu request was made.

## Next Gate

Accepted by `docs/reviews/code-review-20260802-022647.md`; commit S3.
