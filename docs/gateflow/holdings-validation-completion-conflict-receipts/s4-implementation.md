# Gateflow S4 Implementation Artifact — Official NAV Holdings Gate

- Work unit: `holdings-validation-completion-conflict-receipts`
- Gate: `implementation`
- Slice: `S4`
- Base: `5a9f1f0`
- Status: `complete; DeepReview passed`

## Scope

- Discover NAV accounts from complete raw holdings so invalid new rows cannot
  disappear behind typed conversion.
- Run one global orphan scan after existing-final/cash-flow/duplicate checks,
  then validate each ready account after optional Futu CASH/MMF sync.
- Materialize formal cases and receipts before valuation; keep dry-run free of
  workflow writes and validate only authoritative completed Futu projections.
- Freeze raw/normalized holdings digests and pass private typed copies through
  the read service into valuation without rereading holdings.
- Persist and render holdings provenance in NAV details, job results, and
  receipts.

## Safety invariants

- Currency is required and resolved by instrument/type evidence; it is never
  defaulted to CNY by this gate.
- Populated conflicts remain cases requiring an exact human decision. A
  matching `keep-current` is scoped to its immutable confirmation facts.
- Formal NAV never consumes a Futu dry-run projection.
- Source, projection, workflow-state, or case-materialization failure blocks.
- A repaired case is closed only by a fresh proof; evidence outage cannot
  falsely close a prior provider-backed case.
- Existing eligible final NAV remains idempotently skipped before holdings
  added later are considered.

## Result

- S4-related focused regression suite: `198 passed`.
- Ruff, Python compilation, and `git diff --check` passed.
- Initial DeepReview findings are documented in
  `docs/reviews/code-review-20260731-225000.md`; both were fixed and re-review
  at `docs/reviews/code-review-20260731-225629.md` passed.
- No live Feishu/Futu request, holdings mutation, message delivery,
  subscription, service activation, push/PR, release, or deployment occurred.
