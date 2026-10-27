# OM-facing PM API contract

PM owns the canonical `/api/v1` OpenAPI snapshot in `contracts/om-api/`.
OM vendors that snapshot and verifies every client method against it.

- `/health` and `/quality/status` remain unversioned operational endpoints.
- Existing business paths remain deprecated aliases for at least two PM release cycles.
- Breaking changes require `/api/v2`; V1 is never reinterpreted in place.
- Successful V1 read responses include PM-owned `freshness` evidence and a
  separate `retrieved_at_utc`; missing owner evidence is reported as
  `unavailable`, never inferred as live by OM.
- `pm-api-vN.N.N` tags are immutable contract releases, separate from PM application releases.
- `pm-api-v1.0.0` remains an immutable historical published contract. The current
  snapshot adds durable refresh admission and is `release_state=unreleased`,
  `contract_release=null`; its manifest pins the checked-in SHA-256 without
  circularly pinning its own source commit. OM records the exact accepted PM
  source commit and matching hash for this unreleased contract. Do not claim the
  new bytes were published by the historical tag.
- PM CI only validates an explicitly tagged contract release. It has no
  cross-repository credential, schedule, push, or PR authority.
- An operator vendors the validated snapshot into OM with
  `scripts/om_api_contract_release.py vendor`, then pushes a normal OM branch
  and creates the PR manually. Contract distribution never auto-merges,
  releases, or deploys.

`/analysis/cash-facts` is intentionally absent from V1. Until its financial
semantics are implemented in a separate PM work unit, OM must report
`portfolio_cash_facts_not_onboarded` without calling a placeholder endpoint.

`POST /api/v1/futu/holdings/refresh-requests` is a loopback-only, accepted-only
hint. A 202 means a non-durable background task was registered; only PM's sync
evidence can prove the later full Futu reconciliation completed.


`POST /api/v1/futu/holdings/durable-refresh-requests` uses the same strict
`{account, request_id}` payload and accepted identity response. It validates an
explicit configured Futu profile without provider I/O, commits SQLite admission
before 202, and absorbs duplicates even after completion. Profile/input errors
are 400/422; failed storage admission is 503, never 202. The API version header
remains `portfolio.api.v1` on these responses.

The HTTP lifespan worker recovers pending/running requests without another POST,
serializes under a DB-root process lock, and calls the existing fresh-state full
sync without user receipts. Only `success=True` completes a request; other
results retry with bounded backoff. DB settlement/reset outages recover in the
same live worker. Graceful shutdown holds ownership until the sync actually ends.
Acceptance is not proof of completed sync or exactly-once Feishu effects; an
interrupted settlement can cause fresh full sync to repeat.

Receiver-first rollout is required and deployment is separately authorized.
OM must leave a 404/timeout/response-loss handoff pending and retry the same
identity on the durable endpoint; never fall back to legacy acceptance. Existing
historical attempted-only OM rows remain uncertain and are not auto-replayed.
The contract release validator rejects this unreleased manifest; normal export
and SHA checks validate source review without creating a tag or Release.
