# Final closeout — cash scan boundaries

Gate: final closeout pass. Time 2026-10-14T12:52:25.159581+08:00. Artifact: docs/gateflow/cash-scan-boundaries/final-closeout.md.

Draft PR: https://github.com/liuxie066/portfolio-management/pull/75. OPEN, Draft, base main a59f8e3. Reviewed source is c494dd3; accepted aggregate 04c80a5, accepted PR review b48af02. Final review push verified against both PR head and remote branch. Subsequent closing artifact commit contains documentation only and is pushed/read back during closeout.

Changes: failed cash scans attempt durable error receipt delivery while retaining original failure; existing receipt timer retries cash-effect outbox independently. Runtime receipts show occurrence time, exact-scope current state and proved recovery. PerDB same-host lock includes send/mark and survives divergent data-dir configs. Cash reads/init skip definite noncash rows with missing names; unknown or corrupt cash still blocks without advancing fingerprints, manufacturing zero/name or modifying full caches/NAV validation.

Verification: 1673 complete tests passed in 10.99s; required Ruff, compileall and diff checks passed. Real repository/scanner and init-before-create tests, failed scan/send/timer retries, scope/current/legacy state and spawned-process serialization covered. Quality Contract CI 38513290493 (04c80a5) and 38513482958 (b48af02) succeeded; this CI covers contract validation, while full suite evidence is local. Source and test inputs unchanged after S1. No need to rerun behavior tests for review-only documents.

Reviews: planreview accepted one scope omission, fixed and re-reviewed; code review accepted three findings (DB lock root, legacy same-scan recovery proof, reserved cash aliases), all 已修复; aggregate and PR deepreview found no new blocking findings. No deferred findings/unclassified risks. Each review has its tracked durable artifact in docs/reviews.

Docs: cash-flow-effects-runbook and deploy-linux describe validation/delivery boundaries; README positioning unchanged.

Residual risks: remote send succeeded followed by crash before durable marking can duplicate a retry; multi-host coordination is outside same-host lock. Assigned to later work unit, owner PM outbox maintainer. Live Feishu/service acceptance assigned to operator during a later separately authorized upgrade; no production mutation, release or deployment performed here.

Issue linking/closeout comment: not applicable, no issue supplied. Worktree remains on authorized feature branch; no worktree created, unrelated changes absent. local_main_sync=not_applicable (no merge/release). VERSION remains 0.2.5. Next entry: none within work unit; future merge/release/upgrade are separate authorization boundaries.

Delivery timing: request start unavailable, so total wall time unknown; first durable plan record 12:33:13 Beijing and closure record above. Main test execution 10.99s; review/integration and CI waits account for other elapsed work.
