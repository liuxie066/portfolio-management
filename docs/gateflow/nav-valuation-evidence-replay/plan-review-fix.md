# Gateflow Plan Review Fix — NAV Valuation Evidence Replay

- Gate: `plan review -> fix -> re-review`
- Work unit: `nav-valuation-evidence-replay`
- Review artifact: `docs/reviews/plan-review-20260814-173648.md`
- Artifact path: `docs/gateflow/nav-valuation-evidence-replay/plan-review-fix.md`
- Current gate: `accepted plan commit`
- Next entry point: create the accepted plan commit

## Finding decisions and fixes

### PR-PLAN-01 — accepted — 已修复

The plan now restricts evidence capture to
`CASH_FLOW_DATASET_BLOCKED` and `CASH_FLOW_EFFECT_GATE_INCOMPLETE`, requires a
strict dataset account/date/run binding plus financial fingerprint/effect
revision before save, and explicitly forbids artifact creation for
scope/integrity refusals. The S1 tests must prove non-capture.

### PR-PLAN-02 — accepted — 已修复

`PortfolioReadService` is now an allowed S1 owner. The implementation must
extract its existing snapshot projection and use it for both normal valuation
and replay, with equivalence coverage for report-relevant fields.

## Re-review

The corrected plan is code-generation-ready. The save capability cannot be
issued for an untrusted dataset, and the report snapshot retains one projection
owner. Slice order, public input restrictions, persistence ownership, historical
fact-date rules, validation, and residual-risk assignments remain coherent.

- Re-review conclusion: `pass`
- Accepted findings: 2
- Unresolved accepted findings: 0
- Blocking open questions: none

## Residual risks

- Historical provider availability: classified as an operator-time fail-closed
  dependency and documented in S2.
- Runtime evidence backup: assigned to operations.
- Cross-host writer coordination: owned by the existing NAV persistence boundary.

No residual risk is unclassified.
