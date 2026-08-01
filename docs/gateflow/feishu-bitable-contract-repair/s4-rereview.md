# Gateflow S4 Re-review — Cash-flow Row Contracts

- Gate: re-review
- Work unit: feishu-bitable-contract-repair
- Slice: S4
- Base: `8ef9c8c`
- Status: accepted
- Final review artifact: `docs/reviews/code-review-20260802-031618.md`
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s4-rereview.md`

## Review Chain

- `docs/reviews/code-review-20260802-025828.md`: three findings, accepted and fixed.
- `docs/reviews/code-review-20260802-031618.md`: no actionable findings.

## Acceptance Decision

S4 is accepted. Cash-flow rows now have one lossless raw source boundary and one
manual/completed promotion authority. No model default can turn a missing field
into a writeable or aggregatable financial fact.

Direct creation consumes only completed facts. Replay is bound to the observed
remote canonical dedup rather than record existence, and cache publication uses
the same Decimal semantics as a fresh aggregate rebuild. Missing/invalid rows
block before publication and cannot contribute only to a partial total.

List/exact projections and effect revisions preserve the fields needed for
validation and change detection, including observed dedup, source, remark, and
updated-at. Historical dedup text remains compatible without weakening Decimal
business calculations.

## Verification

- 45 focused fault-injection tests passed.
- 175 final S4 scoped and compatibility tests passed.
- 1204 full repository tests passed.
- Compile, scoped lint, and diff hygiene passed.
- No live schema or business-data mutation was performed.

## Next Gate

Commit the accepted S4 scope, then start S5 from the accepted commit.
