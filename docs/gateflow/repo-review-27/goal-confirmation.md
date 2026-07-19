# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `repo-review-27 correctness hardening`
- Branch: `gateflow/fix-repo-review-27`
- Base: `origin/main@d04c62496bb9682a5a3ac00b38efb34ad6f3e9b7` (`v0.1.17`)
- Source review: `docs/reviews/repo-review-20260719-142425.md`
- Artifact path: `docs/gateflow/repo-review-27/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `确认目标`
- Confirmed at: `2026-07-19 16:54:35 +0800`

## Why this work unit exists

The repository review confirmed 27 material correctness, stability, and security defects. The highest-risk paths can duplicate financial side effects, permit overselling or invalid signed writes, silently report partial commits as success, zero valid Futu fund positions, fabricate valuations, lose compensation tasks, or expose unauthenticated write endpoints when the service is started outside its wrapper.

The branch was moved from the reviewed `v0.1.16` snapshot to current `origin/main` (`v0.1.17`). The four intervening commits add the read-only capital-facts endpoint and related tests. They do not remove the root branches described by the 27 findings. The current service still has a 0.5-second write fallback and a module-level unauthenticated `app`; the other finding-owned production files are unchanged by `v0.1.17`.

## Target outcome

Fix all 27 findings in the source review on the `v0.1.17` base, preserving the intended portfolio-management behavior while making financial writes, synchronization, valuation, repair, storage, installer, and service boundaries fail safely and report truthful state.

## Success signals

1. Every source-review finding is mapped to an implementation slice and ends aggregate re-review as `已修复`.
2. Idempotent replay changes transaction/cash-flow records, holdings, and cash at most once, including concurrent same-host callers.
3. Invalid financial inputs and oversells are rejected before any ledger or balance side effect.
4. Partial multi-step writes return an explicit recoverable state; compensation persistence cannot lose concurrent tasks and has an executable recovery path.
5. Futu synchronization never zeros an asset merely because the upstream security type was filtered inconsistently.
6. NAV partial states, dry-run semantics, and repair validation/apply behavior preserve auditability and support deterministic recovery.
7. Pricing/valuation rejects invalid or unconvertible quotes, uses correct market time, preserves quantity precision, and performs no network calls in cache-only mode.
8. Feishu batch/delete/HTTP/schema boundaries validate responses, propagate failures, use bounded I/O, and keep local caches consistent.
9. Service write requests are not automatically replayed after an ambiguous timeout, and direct ASGI startup retains the loopback safety boundary.
10. Re-installation preserves the three `OM_FEISHU_BOT_*` values unless an explicit replacement is available.
11. Focused regression tests plus the full test suite and compile checks pass; review artifacts and final docs describe changed contracts and residual risks.
12. Gateflow reaches `final closeout pass` with protected commits, aggregate DeepReview, Draft PR, PR review, and final validation evidence.

## Scope boundary

### In scope

- The 27 findings in `docs/reviews/repo-review-20260719-142425.md`.
- Minimal production changes in the existing financial write, Futu, NAV, pricing, Feishu, installer, and service boundaries.
- Tests that reproduce each defect and prove the corrected state transitions.
- Necessary schema/runbook/migration documentation and a safe operator-facing recovery or audit command where a finding requires one.
- Backward-incompatible failure behavior that is necessary for correctness, such as rejecting invalid values, refusing ambiguous write fallback, or surfacing partial state.

### Out of scope

- Executing repairs against production Feishu/Futu data, changing live credentials, deploying a release, or merging the eventual PR.
- Building a general distributed transaction platform, multi-region idempotency service, or new authentication product.
- Refactoring unrelated modules, renaming unrelated public fields, or addressing lower-impact observations not included in the 27 findings.
- Automatically deleting or rewriting historical duplicate/corrupt records. The code may provide detection/migration tooling, but production execution requires a separate explicit approval boundary.

## Direct code evidence

- `src/app/trade_service.py` continues side effects after repositories return existing records and swallows secondary buy/sell failures.
- `src/app/futu_balance_sync_service.py` uses inconsistent eligible upstream and existing asset-type sets.
- `src/app/valuation_service.py` falls back from missing `cny_price` to raw price and values missing CNY securities at one yuan.
- `src/feishu_client.py` has unbounded core HTTP calls, swallows DELETE failure, and accepts malformed batch cardinality.
- `scripts/pm.py` still falls back from a 0.5-second service timeout to direct execution for write commands.
- `src/service/http.py` still exports a module-level unauthenticated `app`; bind validation remains only in wrapper scripts.
- `src/app/compensation_service.py` uses concurrent read-copy-replace JSONL writes.
- Baseline verification on `v0.1.17`: `546 passed`.

## Minimality / over-design exclusions

- Reuse current services, repositories, models, CLI patterns, stdlib locking/SQLite primitives, and existing configuration surfaces before adding abstractions.
- Prefer one authoritative validation or state-transition boundary over guards in every caller.
- Do not add dependencies unless an existing/stdlib mechanism cannot satisfy a confirmed contract.
- Do not create speculative extension points, generalized workflow engines, or a new storage layer unrelated to the findings.

## Blocking open questions

- None for planning. The plan must explicitly document any unavoidable single-host assumption and any historical-data audit that remains operator-triggered.

## Validation completed at this gate

- Protected-branch preflight passed by creating `gateflow/fix-repo-review-27` from current `origin/main`.
- Existing review artifact preserved.
- Base delta inspected: `v0.1.17` adds capital-facts behavior and does not invalidate the finding roots.
- Full baseline test suite: `546 passed in 14.26s`.

## Docs decision

Documentation updates are required where behavior changes: write/idempotency contracts, service timeout and remote-binding safety, installer env preservation, NAV repair/recovery, and any Feishu schema or operator action.

## Residual risks

- Production data may already contain duplicates, partial writes, stale caches, or rounded snapshots. Classified as `requiring separate explicit production-data decision`; no live mutation is authorized in this work unit.
- Feishu may not provide a native unique constraint. The approved plan must define a same-host atomicity contract and clearly classify any cross-host residual risk.
- External protocol behavior is not fully reproducible locally. Classified as `covered by focused fault-injection tests plus later canary/deployment work unit`.
