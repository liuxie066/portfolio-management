# OM-facing PM API contract

PM owns the canonical `/api/v1` OpenAPI snapshot in `contracts/om-api/`.
OM vendors that snapshot and verifies every client method against it.

- `/health` and `/quality/status` remain unversioned operational endpoints.
- Existing business paths remain deprecated aliases for at least two PM release cycles.
- Breaking changes require `/api/v2`; V1 is never reinterpreted in place.
- `pm-api-vN.N.N` tags are immutable contract releases, separate from PM application releases.
- Contract sync only creates a draft OM PR and never auto-merges, releases, or deploys.

`/analysis/cash-facts` is intentionally absent from V1. Until its financial
semantics are implemented in a separate PM work unit, OM must report
`portfolio_cash_facts_not_onboarded` without calling a placeholder endpoint.
