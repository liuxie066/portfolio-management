# OM-facing PM API contract

PM owns the canonical `/api/v1` OpenAPI snapshot in `contracts/om-api/`.
OM vendors that snapshot and verifies every client method against it.

- `/health` and `/quality/status` remain unversioned operational endpoints.
- Existing business paths remain deprecated aliases for at least two PM release cycles.
- Breaking changes require `/api/v2`; V1 is never reinterpreted in place.
- Successful V1 business responses include PM-owned `freshness` evidence and a
  separate `retrieved_at_utc`; missing owner evidence is reported as
  `unavailable`, never inferred as live by OM.
- `pm-api-vN.N.N` tags are immutable contract releases, separate from PM application releases.
- The current `pm-api-v1.0.0` name is planned but unpublished. Until an
  independently authorized annotated tag exists, manifests pin the reviewed
  source commit and SHA-256 and explicitly say `unpublished`.
- PM CI only validates an explicitly tagged contract release. It has no
  cross-repository credential, schedule, push, or PR authority.
- An operator vendors the validated snapshot into OM with
  `scripts/om_api_contract_release.py vendor`, then pushes a normal OM branch
  and creates the PR manually. Contract distribution never auto-merges,
  releases, or deploys.

`/analysis/cash-facts` is intentionally absent from V1. Until its financial
semantics are implemented in a separate PM work unit, OM must report
`portfolio_cash_facts_not_onboarded` without calling a placeholder endpoint.
