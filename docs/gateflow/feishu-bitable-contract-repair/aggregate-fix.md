# Gateflow Aggregate Fix — DeepReview Findings

- Gate: aggregate fix
- Work unit: feishu-bitable-contract-repair
- Base: `59625b0d6c666da338c0a520e85a221932846949`
- Initial aggregate review: `docs/reviews/code-review-20260802-094826.md`
- Recorded at: 2026-08-02T09:57:34+08:00
- Status: accepted findings fixed; pending aggregate re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/aggregate-fix.md`

## Finding Decisions

### DR-AGG-01 — accepted — fixed

The holdings validator had a handwritten required-field set that omitted
`asset_name`, even though the canonical holdings create contract and strict
reader require it. Validation requirements and relevant-field ordering now
project from the typed registry. A missing name is a blocking raw-fact gap and
cannot be converted to a typed `Holding`; provider evidence can propose a value
but cannot silently supply the missing raw fact. A present manual name remains
authoritative and a nonblocking provider-name conflict retains that current
value.

The reconciliation test whose subject is Futu evidence failure now supplies a
valid name so it continues to isolate its original failure mode.

Final status: `已修复`.

### DR-AGG-02 — accepted — fixed

The cash-flow create contract now requires the complete persisted projection
of `CompletedCashFlowFacts`: `flow_date`, `account`, `broker`, `amount`,
`currency`, `flow_type`, `cny_amount`, `dedup_key`, `exchange_rate`, and
`source`. The client therefore rejects incomplete core rows before transport.

`CashFlowType` is now the domain semantic owner of `DEPOSIT` and `WITHDRAW`;
the registry derives its SingleSelect options from that projection. The domain
also owns the manual-required and complete-financial field tuples; event
identity and the registry create contract project those tuples instead of
copying them. A generic `TableContract` invariant also requires every declared
business-key field to be create-required for every writable table, preventing
recurrence outside cash flow.

Final status: `已修复`.

### DR-AGG-03 — accepted — fixed

The snapshot domain now owns one canonical business-key field tuple in the
declared order `(as_of, account, asset_id, broker)` and one canonical dedup-key
helper. Registry identity, domain key construction, digest sorting, scope
assertions, valuation-row construction, and repository stable ordering project
from those sources.

Full-row payloads now originate from a strict `HoldingSnapshot` dump and only
canonicalize numeric values. `HoldingSnapshot` rejects unknown fields, while
the repository fails fast unless model fields and registry fields match
exactly, including order. Regression coverage proves identity order, dedup
semantics, model/registry/payload coverage, and unknown-field rejection.

Final status: `已修复`.

## Validation

- Focused aggregate regression suite: `158 passed` before the legacy fixture
  correction.
- Full repository suite after all corrections: `1375 passed`.
- Scoped Ruff, Python compileall, schema generator `--check`, migration
  expectations, and `git diff --check`: passed.
- Generated `docs/schema.md` agrees with the canonical registry.
- No live Feishu/Futu request, schema mutation, business-data mutation, merge,
  release, deployment, or service change occurred.

## Aggregate Re-review Scope

Re-review the complete branch from
`59625b0d6c666da338c0a520e85a221932846949`, including every accepted S1-S12
change and these aggregate corrections. Re-prove registry/domain ownership,
raw-field validation, client preflight rejection, snapshot exact-set identity
and full-row coverage, generated projections, and all previously recorded
finding closures.
