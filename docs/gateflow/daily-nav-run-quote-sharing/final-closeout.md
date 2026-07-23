# Gateflow Final Closeout — daily-nav-run-quote-sharing

- Gate: `final closeout`
- Work unit: `daily-nav-run-quote-sharing`
- Branch: `fix/daily-nav-run-quote-sharing`
- Base: `main@a21bb1d`
- Draft PR: `https://github.com/liuxie066/portfolio-management/pull/32`
- Accepted plan commit: `421f6b3`
- Accepted S1 commit: `4619207`
- Accepted aggregate DeepReview commit: `d0db590`
- Accepted PR review commit: `9705ea3`
- Artifact path: `docs/gateflow/daily-nav-run-quote-sharing/final-closeout.md`
- Status: `final closeout pass`

## What changed

- Added one caller-owned `RunQuotePool` for each canonical daily NAV job run.
- Shared only validated successful quotes using `(canonical_code, market_type)` identities and deep-copy return semantics.
- Reused FUTU/PDD/TCOM-style overlap for later accounts while delegating only current misses such as SPY/BABA.
- Kept failed identities out of the quote map and allowed at most one later retry, for two delegated attempts per identity per run.
- Partitioned same-canonical/different-market requests before the existing canonical-only batch planner.
- Preserved current-account stale policy, force-refresh behavior, provider exceptions, per-account deadline, blockers, and Futu sync ordering.
- Added boundary-accurate run metrics and independent `run_reused` account/receipt visibility.
- Kept all direct/single-account callers unchanged when no pool is supplied.

## Why the previous global cache did not solve the incident

- Each account valuation created a new local `prices` mapping.
- The batch pricing path could read `PriceCachePolicy`, but successful batch payloads were not guaranteed to be written through to that persistent cache.
- Therefore `lx` success did not establish a durable fact for `sy`; `sy` re-entered the provider chain for the overlapping tickers.
- This work unit intentionally fixes the narrower same-run ownership gap without changing persistent-cache semantics.

## Verification

- Focused post-fix suite: `64 passed in 0.30s`.
- Full repository suite: `729 passed in 5.91s`.
- `python3.12 -m compileall -q src skill_api.py scripts/pm.py scripts/publish_daily_report.py`: pass.
- `git diff --check`: pass.
- Plan review: pass after PR-01/PR-02 fixes.
- Slice DeepReview: pass after DR-S1-01/DR-S1-02 fixes.
- Aggregate DeepReview: pass; no new material findings.
- PR DeepReview: pass; remote reviewed head matched local aggregate head.
- Final GitHub state after PR-review push: head `9705ea3`, Draft=true, merge state clean, no submitted reviews or hosted checks.

## Finding status

- PR-01 — downstream canonical-only planner could collapse cross-market identities: `已修复`.
- PR-02 — proposed network metric claimed evidence unavailable at the pool boundary: `已修复`.
- DR-S1-01 — stale run quote could bypass a later account's current market policy: `已修复`.
- DR-S1-02 — pool swallowed fetcher exceptions and removed existing diagnostics: `已修复`.
- Aggregate and PR reviews: no material findings.

## Remaining risks and owners

- Persistent batch-cache write-through: `assigned to a separate pricing-cache work unit`; unchanged here.
- Provider-specific failures/timeouts and real network request counts: `provider/diagnostics-owned`; unchanged here.
- Raw last-call Tencent batch metadata can be stale for an all-hit account: `low-risk diagnostics follow-up`; it does not affect valuation or consolidated receipt.
- `sy` `CNY-CASH` with `currency=HKD`: `separate data-quality issue`; intentionally not changed.
- Missing production `sy` NAV for 2026-07-22: `operator-owned backfill decision`; not performed.
- Live production canary/release/deployment: `not authorized`; not performed.

## PR and issue status

- Draft PR #32 is open and remains draft.
- No merge, mark-ready, reviewer request, approval, auto-merge, release, deployment, or production data mutation was performed.
- Issue link: not applicable; this work unit was not tied to a numbered issue.

## Next entry point

The code work unit is complete at `final closeout pass`. The next user-controlled action is to inspect Draft PR #32 and decide whether to mark it ready, request reviewers, merge it, or schedule a release/deployment. Production `sy` backfill remains a separate explicit decision.
