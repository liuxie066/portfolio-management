# Gateflow Final Closeout — nav-finality-write-authority

- Gate: `final closeout`
- Work unit: `nav-finality-write-authority`
- Branch: `fix/nav-finality-write-authority`
- Base: `main@81e82e2`
- Draft PR: `https://github.com/liuxie066/portfolio-management/pull/31`
- Accepted plan commit: `b5e3989`
- Accepted S1 commit: `14cb8c3`
- Accepted S2 commit: `93d737e`
- Accepted aggregate DeepReview commit: `0db273b`
- Accepted PR review commit: `18c563e`
- Artifact path: `docs/gateflow/nav-finality-write-authority/final-closeout.md`
- Status: `final closeout pass`

## What changed

- Added the versioned `details.finality` provenance contract and trusted-writer/status validation for authoritative NAV mutations.
- Made the canonical daily job skip an existing row only when its finality is eligible for the target date; legacy, manual, malformed, or mismatched rows fail closed as `existing_nav_not_final`.
- Propagated classified write context through daily job, manual record, daily report, initialization, close, and maintenance/backfill paths.
- Changed public NAV overwrite defaults to opt-in and documented the required CLI/publisher flags.
- Preserved `details` through local NAV index construction, incremental cache writes, and restart reconstruction; account-scoped duplicate audit now refreshes the index from the remote rows it already fetched.
- Made finality-bearing single/batch create/update fail closed when Feishu returns `FieldNameNotFound`, preventing silent no-details retry and local-only finality.
- Retained optional-details compatibility fallback only for legacy low-level payloads without `details.finality`.

## Verification

- Focused Feishu repository/fallback suite: `89 passed`.
- Complete relevant NAV/service/CLI/publisher suite: `245 passed`.
- Full repository suite: `684 passed in 3.84s`.
- `python3.12 -m compileall -q src skill_api.py scripts`: pass.
- `git diff --check`: pass.
- Aggregate DeepReview re-review: pass; DR-AGG-01 and DR-AGG-02 both `已修复`; no new material findings.
- PR DeepReview: pass; no accepted findings requiring another fix/re-review loop.
- GitHub PR review evidence at reviewed head: no submitted reviews, no review threads, and no hosted workflow/status results returned.

## Documentation

- Updated `README.md`, `docs/runbook.md`, `docs/service.md`, and `scripts/README_daily_report.md` for safe overwrite defaults, daily-job finality semantics, and publisher write opt-in.
- Preserved all Gateflow plan, implementation, fix, aggregate review, PR review, and final closeout artifacts in the branch.

## Finding status

- DR-AGG-01 — local NAV index dropped finality after restart: `已修复`.
- DR-AGG-02 — `FieldNameNotFound` silently dropped authoritative finality: `已修复`.
- PR DeepReview: no material findings.

## Remaining risks and owners

- Cross-host or external Feishu writer uniqueness: `assigned to a later infrastructure work unit`; current process lock is same-host only.
- Legacy remote rows without trusted finality: `assigned to an operator-owned repair/backfill flow after deployment`; the daily job intentionally fails closed until repaired.
- Live Feishu mutation/canary: `excluded by user instruction`; no production mutation was performed.
- Yahoo/Finnhub/Futu provider behavior: `assigned to a separate provider work unit`; unchanged here.
- Hosted CI: no workflow or commit status was returned for the reviewed head; deterministic local validation is the evidence for this gate.

## PR and issue status

- Draft PR #31 is open and remains draft.
- No merge, mark-ready, reviewer request, approval, auto-merge, deployment, release, or production Feishu mutation was performed.
- Issue link: not applicable; this work unit was not tied to a numbered GitHub issue.
- Issue closeout comment: not applicable.

## Next entry point

The work unit is complete at `final closeout pass`. The next user-controlled action is to inspect Draft PR #31 and, when desired, explicitly decide whether to mark it ready, request reviewers, or merge it. None of those actions are part of this closeout.
