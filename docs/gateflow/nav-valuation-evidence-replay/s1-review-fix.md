# Gateflow S1 Fix Artifact — DeepReview

- Gate: `fix`
- Work unit: `nav-valuation-evidence-replay`
- Slice: `S1`
- Review artifact: `docs/reviews/code-review-20260814-174907.md`
- Status: `fix and re-review complete`

## Finding decisions

### DR-S1-01 — accepted — 已修复

`NavValuationEvidenceStore` now requires the top-level Holdings digest to equal
the normalized valuation's `holdings_provenance.normalized_holdings_digest`
both when preparing and when loading an artifact. A mismatch regression test
passes.

### DR-S1-02 — accepted — 已修复

`DailyNavJobService` now treats any supplied `valuation_ref` as replay input,
requires exactly one normalized explicit account and date, and loads the
account/date-bound capability before duplicate checks or Holdings preflight.
The same store instance is passed to the default account recorder. Regression
tests cover invalid references and comma-separated accounts.

## Re-review

- Confirmed both accepted findings are fixed at the shared trust boundaries.
- Confirmed invalid replay evidence cannot reach duplicate, global, or account
  Holdings preflight.
- Focused suite: 114 passed.
- Compile check and `git diff --check`: passed.
- Final finding status: no unresolved high or critical finding in S1.
