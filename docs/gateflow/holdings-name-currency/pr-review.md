# PR 76 independent deepreview

## Findings

未发现实质性问题。Decision: pass-with-risks. No new accepted finding or source fix is required.
Prior short-HK-alias finding and QA-01 invalid-currency keep/NAV waiver are accepted/已修复.

## Scope

- Mode: PR review; repository liuxie066/portfolio-management; PR 76.
- Title: Fix HK holding name completion and confirmed currency correction.
- Author: liuxie066; head fix/holdings-hk-name-currency; base main.
- URL: https://github.com/liuxie066/portfolio-management/pull/76
- Exact reviewed head: 9ea2df1450a36fca3744b89be67d8a5520dd3fd0.
- Exact base: b8e75eaef53294cb3d7fcc3cd46fe9eb229d3ca7.
- Also reviewed the controller's uncommitted README capability paragraph at lines 168–175: correctly describes explicit HK typing, exact identity evidence, preserved names, lookup-free ordinary reads and confirmation-only currency correction.
- Also reviewed the delivery continuation appended to status.md: it explicitly supersedes historical local-only scope, records actual first-head CI and leaves final checks/merge pending without claiming completion.
- Complete PR scope: six production modules, four test modules, runbook and Gateflow artifacts. Reused unchanged aggregate review evidence at docs/reviews/code-review-20261026-110532.md and local independent QA evidence. No nested reviewers. No production access, source changes, test reruns or external writes by this reviewer.

## Execution and adversarial review

Read the PR title/body and current metadata, complete production diff and related test diff. Traced process_event_notification -> _complete_event_name -> evaluate/include_hk_stock_name -> exact source/evidence validation -> _execute_patch and protected repository write/readback. Candidate typing, broker/account dimensions, bounded HK grammar, exact outer/inner response identity and duplicate rejection remain explicit. Shared domain checks avoid HTTP dependencies in validation. Existing name preservation, repeated/self events, stale identities, human edits and unknown outcomes are covered by real state assertions. The write target remains name alone. Lookup failures remain missing/manual rather than claimed success.

Traced _currency -> blocking conflict -> discovery receipt -> resolve and apply_missing. Invalid proposed codes fail membership validation; the one reviewed typo map has versioned evidence, no invented source observation time, no market/account guessing and no automatic write. Conflicting authority leaves no candidate. Existing valid currency behavior remains. Fresh case/scope checks protect confirmation; invalid keep-current rejection precedes idempotent resolved_keep return. Durable keep matching and to_holding also reject invalid raw denomination, protecting NAV rather than just UI wording. Existing record locks, digest preconditions, durable attempt-before-I/O and explicit unknown outcome behavior remain reused. No schema, new state machine or unrelated account-query authority was introduced.

README/runbook align with implemented limits. Changed tests use synthetic data and mocked services and exercise failures, concurrency edits, scope change and invalid waiver paths. Updated case hash is explained by currency policy v2, without weakening precondition assertions. No new overcoupling or semantic ownership drift was established.

## Validation and CI

Reused unchanged source/test evidence: 253 focused passes; 66 independent QA passes; compile/Ruff/diff passed. Native actual-date full suite is 1746 passed and 2 failed, not all green. Both unchanged NAV freshness fixtures contain September dates outside the current 30-day window and fail identically on the original baseline. Supplemental full suite with an external clock fixture scoped only to those two tests is 1748 passed. This conditional result does not erase native failures.

Read-only GitHub metadata reports Quality Contract SUCCESS on reviewed PR head, run 40263345471, job 121380239253, completed 2026-10-26T03:18:02Z. This workflow runs contract checks only, not the full suite. 

Independent merge assessment: these two proven baseline date failures do not establish a regression in this change. No fixture-only expansion is necessary while required CI succeeds; maintainers retain the existing separate follow-up. Do not call native full tests green. After documentation acceptance commit, controller must bind CI and merge to that final exact head and refresh base/required review state; this artifact itself is not GitHub approval or permission to bypass branch rules.

## Open Questions

None blocking the reviewed code. Final exact-head CI and merge state remain controller-owned pending steps.

## Residual Risk

- Assigned to maintainers' later test work unit: native date-dependent NAV fixtures.
- Assigned to later storage work unit: inherited non-atomic Feishu read/PATCH interval.
- Requires separately authorized acceptance: live Tencent response/availability and production rollout/real holdings verification. No production changes included.
- Assigned to maintainers: reviewed currency snapshot and typo-rule updates; no runtime catalog/fuzzy correction.
- Documented limitation: requests timeout is I/O timeout, not hard wall-clock cancellation.

## Completion

PR code review gate passes with classified risks and no fix loop. Controller owns sanitized review checkpoint, final push/checks and authorized merge. No release, tag, deployment, notification or production mutation is authorized by this review.
