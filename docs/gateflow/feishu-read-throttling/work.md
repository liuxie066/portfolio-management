# Feishu read throttling recovery

## Scope and plan — 2026-11-25

User approved implementation, tests, one commit, push and merge to main via PR.
No release, production upgrade or real sync is part of this change.
Base: d45b4e6294f5d3801f432c88503d7fd10dee5192. Worktree: earnest-tesla.

Confirmed incident: HTTP 200/code 1254290 during lx position diff. The client
only retried HTTP 429. Triggering upstream quota remains unknown; later sy
succeeded. Existing request spacing is per client, not a global quota guarantee.

One implementation slice:
1. Retry GET business throttles (HTTP 200/code 1254290) at most three times.
   Preserve HTTP 429 behavior and typed errors; honor server wait hints and
   deadline budgets. Do not add business-error retries to writes/messages.
2. Emit sanitized per-attempt diagnostics: time, endpoint category, hashed
   app/base/table identity, process/client, status/code/log id and backoff.
3. Reuse the freshly read cash base between preparation and CashService; keep
   storage's fresh digest check, post-write proof and final reconciliation.
4. Document operations and regressions; run mandatory project checks and
   independent code review, then deliver one commit through a PR.

Plan check: sufficient for confirmed goal; no schema/public HTTP change, new
cache, lock or dependency. Separate plan review is not required for this bounded
change to existing request and cash-base paths. Do not delete safety reads.
Unknown quota attribution belongs to subsequent production observation by the
owner after a separately authorized release/upgrade; centralized pacing remains
evidence-dependent.

## Validation / review / delivery

Implementation complete. GET business throttles recover with bounded retries;
structured request diagnostics preserve privacy; aggregate cash reuses its fresh
base and saves one account-wide read while all mutation proofs remain.

Baseline reproduction: previous FeishuClient fails after one attempt on HTTP
200/code 1254290. Tests now cover recovery, exhaustion, pagination, wait hints,
deadlines, non-retryable writes/errors and log privacy; real-repository cash tests
cover create/update/no-op, concurrent changes, wrong readback and dry-run.

Python 3.12.13 full suite: 2185 passed in 24.64s. Compileall, full project Ruff,
OM OpenAPI drift check and diff whitespace passed. Logs retained for this session
at /tmp/pm-throttle-full-pytest.log and /tmp/pm-throttle-initial-tests.log.
README decision: no change to capability, installation or public interface;
runbook owns operational behavior. LESSONS links the regression and incident.

Complete review: local, gitignored artifact
docs/reviews/code-review-20261125-091150.md. Two independent
specialist reviews and controller combined review found no material issue.
All authorized implementation steps are complete; shared limiter and further
safety-read consolidation remain evidence-dependent decisions, as agreed in
scope. No live sync or production operation was performed.

Next: one commit, push, PR identity/CI check, authorized merge, local main sync.
Delivery receipts will be retained in the current conversation; no extra commit
is needed solely to rewrite this pre-delivery record.
