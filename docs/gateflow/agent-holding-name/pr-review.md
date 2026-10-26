# PR 77 independent deepreview

## Scope
- Mode: PR; https://github.com/liuxie066/portfolio-management/pull/77 (draft at review).
- Title: Add controlled Agent preview and confirmation for missing holding names. Author: liuxie066.
- Exact base: `931fc086a23ec10a06edb2f7a81db80101cef3f0`.
- Exact head: `47f48d5dd2b0e7896bc588ce188cfba81a63d61c`.
- Included: complete source/test/docs change reviewed in aggregate, actual HTTP/auth → durable claim → workflow → Feishu facade/repository and receipt/state paths; rechecked final committed correction and public PR description/diff/CI.
- Prior independent artifacts: code-review-20261026-122039.md and code-review-20261026-122404.md; public aggregate-review.md preserves finding and repair history.
- Remote PR patch downloaded read-only. After normalizing only hunk-header function context, it exactly equals local base..head patch; normalized SHA256 `cfb2dbb2aa2400bd2ba5192dedbd987f0dd52b6942311c9b16eba87cf70cad43`.
- No new subreviewers or test reruns in this PR gate. No source changes, commits, public comments, production configuration/credentials or business operations. Excludes live provider/production acceptance.

## Findings
No unresolved substantive finding. **Pass-with-risks** for the exact reviewed head.

Previously accepted AGG-01 is fixed: Agent explicitly sets refresh_account_cache=False through workflow and actual facade; repository performs protected exact patch/readback then invalidates local cache, without loading other account records. Legacy default remains True. The real-client regression logs forbidden list attempts before raising, so workflow exception recovery cannot mask a broad read. Existing updated_at transport metadata is disclosed separately from the sole asset_name business-field write.

## Boundary and failure review
- Default disabled and malformed configuration fail closed. Exact principal/record/account/broker grants are verified before provider work; request body/query cannot supply arbitrary actions, fields, commands or URLs. Cached policy reload limitation is explicit.
- Nonce binds current principal/grant and source snapshot; expiration, wrong principal/record, changed policy/evidence/source and cross-identity races reject. Atomic claim and record locks prevent duplicate confirmation; terminal replay/status do not reissue writes. Executing/unknown states stay conservative rather than blind retry.
- Full fresh same-record observed cases preserve currency/other blockers. Receipt suppression reaches discovery, absent-case closure and terminal paths; synthetic self-event regression checks existing cases do not enqueue new receipts. Preview only persists a local operation snapshot, not cases or remote data.
- HTTP 200 after claim may contain durable rejected state; not represented as write success. Unclaimed expired request remains 409. Sanitized errors/results avoid raw financial/provider details.
- Agent namespace is separate from immutable OM export; minimal prefix exclusion and regression preserve published contract ownership. No credential provisioning, Agent grants, currency patch, production enablement or new network exposure in this diff.
- Public diff/body contain synthetic cases, not the reported private record/case/account identifiers or private holding data. Targeted identifier/secret-marker scan found no match; full reviewed content contains no actual token/secret. PR body accurately discloses native full-suite failures and separate enablement boundary.

## CI and validation evidence
Independently read GitHub run `40269534026`: completed/success on exact head `47f48d5dd2b0e7896bc588ce188cfba81a63d61c`; quality-contract check completed 2026-10-26T04:32:14Z.
https://github.com/liuxie066/portfolio-management/actions/runs/40269534026

Workflow inspected: validates pinned OpenAPI plus four contract test files. This is contract CI, **not** remote execution of the entire suite.

Reused final local evidence: 174 focused passed; 40 independent QA passed; native full 1765 passed and 2 reproduced unchanged baseline date-dependent NAV failures; supplemental full 1767 passed with clock fixed only for those two fixtures. Final assertion hardening: 18 targeted tests passed. Compile/Ruff/diff checks passed. Original failures remain in validation ledger. No claim native full suite green, no skipped failures or live validation claimed.

## Open Questions
None blocking this default-disabled code change. Controller must bind final checkpoint and CI after adding review documents; this report is not a GitHub approval action or authorization to activate access.

## Residual Risk
- Storage: inherited Feishu read/PATCH is not atomic server CAS; existing locks and rechecks reduce but cannot eliminate remote concurrent edits.
- Activation/security: controller/operator must separately obtain approval for concrete principal/token provisioning, exact grants and supported configuration reload. YAML edit alone does not instantly revoke cached policy.
- Operations: retained previews and executing/unknown recovery require explicit maintenance decisions; no automatic replay/cleanup added.
- Acceptance: live provider health and real production operation untested. Native date-fixture failures remain transparent baseline debt.

## Decision
PASS-WITH-RISKS; AGG-01 accepted/已修复, no new blocking issue. Public delivery remains controller-owned. Any product-code or base change requires affected review/validation; review-only checkpoint still requires its final-head CI.
