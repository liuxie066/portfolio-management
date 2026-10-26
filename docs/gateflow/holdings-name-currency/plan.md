# Holdings HK name and currency — local Gateflow plan

## Goal confirmation and boundaries

User requested Gateflow repair of the two diagnosed issues; delegated scope explicitly authorizes an isolated branch, local design/code/tests/reports and independent QA. This is the binding equivalent goal confirmation. Main is clean at b8e75ea (v0.2.6); isolated branch fix/holdings-hk-name-currency exists in /tmp/pm-holdings-hk-name-currency. No other active PM owner was visible. No production reads/writes, real external requests, credentials, messages, sync/reconcile commands, public push/PR/issues/comments, release/deploy, worktree deletion or AGENTS edits. Prior broader dispatch including public delivery was denied and did not execute; this local scope must not route around that denial. Stop at ready-to-open-draft-PR; PR gates remain explicitly unexecuted, not passed.

Goals: (1) eligible explicitly typed non-Futu HK stocks with an empty name can be completed from exact public market identity evidence; (2) invalid currency strings cannot pass holdings validation merely for letter shape. Preserve current names, account/broker separation, legitimate currencies, evidence audit and write/readback safeguards. No per-stock special case, new paid source, credentials, schema migration or generic enrichment framework.

## Direct evidence and design

Existing process_event_notification first evaluates the record, then only is_fund_name_candidate triggers _complete_event_fund_name, then cases are materialized. _asset_name has only Futu/fund evidence. Reuse this narrow policy and durable patch machinery for HK stocks; do not call get_asset_name display fallback. Existing HK provider calls Tencent but trusts request code and fetches FX; this is unsuitable for authoritative name evidence as-is.

### One behavior slice, two scoped fixes

Allowed modules: src/app/asset_name_service.py, holdings_reconciliation_service.py, holdings_validation.py, holdings_workflow_service.py; a small domain currency definition if needed; focused related tests; docs/holdings-event-listener-runbook.md; local gate artifacts. Do not change unrelated price valuation or account mapping.

Name evidence:
- Add strict HK identifier normalization owned by name evidence layer: nonzero ASCII digits of 1–5 characters, or explicit HK prefix/suffix forms (HK00700, HK.00700, 0700.HK, 700, HK700, 700.HK), normalized to five digits; reject other markets, malformed/ambiguous/mixed syntax and overlong codes. No mutation of holding asset_id.
- Add bounded HTTPS Tencent HK name lookup using requests Session and existing parse_multi_payload. One 5-second request; no cache or FX lookup, no retry/fallback to unrelated markets. Verify exactly one matching outer hk<canonical> assignment, inner numeric code matches, nonempty string name is not a code alias; reject duplicate/conflicting matching responses and malformed/error responses. Close session. Preserve source=tencent_hk, canonical code, market=HK and UTC observation timestamp (retrieved-at, not claimed exchange timestamp).
- Candidate requires explicit hk_stock, account, known non-Futu broker and empty string/None name; ordinary reconcile/NAV reads must not query this provider. Add injectable lookup like existing fund_name_lookup. Keep fund behavior compatible.
- Evidence bundle and validator accept HK name evidence only for matching candidate, code, market, source, nonempty name and source_as_of; valid proof yields missing_completable with authority hk_stock_quote. Missing/timeout/unproven evidence leaves missing_manual, not fabricated name. No existing name overwrite or conflict auto-resolution.
- Share existing automatic empty-name execution lifecycle with fund path using clear generalized internal helpers if necessary: account/record lock, fresh evaluation with lookup, identity and candidate recheck, persist attempt before narrow asset_name patch, digest protected raw reread, readback classification, durable unknown-outcome suppression. Use explicit HK automatic policy label, retain existing fund labels where tests/audit expect them. No workflow schema/state changes.

Currency:
- Current _CURRENCY_RE only checks shape. Add known currency-code membership validation for both provided current and any proposed authority-derived code. Preserve ISO 4217 currencies and explicit project CNH compatibility; do not restrict to models.Currency (only CNY/USD/HKD, existing EUR tests prove that too narrow). Use a documented local fixed code set, no runtime dependency/network; include supported historical codes only if required by existing fixtures/contracts, and document policy snapshot/provenance. Reject HDK and arbitrary letters, blank stays existing missing path. No autocorrection to HKD or blanket hk_stock denomination guess. Bump CURRENCY_POLICY_VERSION because case semantics change; no change to unrelated cash-flow validation.

## Plan alignment and minimality

Both changes directly address confirmed goals, in one slice because they meet in the same validator/event tests. No price provider rewrite, new storage, platform migrations, account connector changes or broad refactor. Name-only source avoids unnecessary valuation requests. A small local currency set is preferable to adding an external package/runtime lookup. Report any need for broader policy decisions before implementing.

## Verification

Independent reviewer owns plan review and later code/deepreview; implementation owner is separate. All tests run against synthetic records and mocked HTTP/provider/storage with production network unavailable. Configure isolated PM_DATA_DIR/HOME/config paths as appropriate after checking test harness; no existing user config or credentials. Preserve before-fix synthetic failures and post-fix exact commands/results in gate artifacts.

Assertions: HK success patches only name, source/time retained; aliases canonicalize consistently; foreign market/code mismatch/duplicate response/empty or code-only name/HTTP failure/timeout do not patch; existing name and Futu/unknown broker do not invoke HK provider; non-Futu accounts stay independent of Futu mapping; repeated/self event is no-op; human edits during lookup and raw patch recheck preserved; unknown outcome never auto-retries; original fund tests pass. Currency legal CNY/HKD/USD/EUR/JPY/CNH and existing fixtures survive, HDK/unknown codes blocked including source proposals, no automatic correction. NAV has account-level blocking for invalid currency and missing name; successful name evidence alone does not waive currency blocker.

Run focused tests first, then repository required pytest tests -q, compileall and ruff on src/skill_api/scripts targets, git diff --check. Audit test harness before full run to avoid integration/network behavior; use an external local runner guard if necessary, record any failures/skips honestly. No remote CI (push forbidden).

## Gates, artifacts and residual risks

Current gate: plan review. Next after acceptance: accepted plan local commit, implementation, independent code review/fix/re-review, accepted slice local commit, aggregate deepreview/fix/re-review, accepted deepreview local commit, ready-to-open-draft-PR (stop by explicit scope). Do not claim final closeout pass or draft-PR-pass.

Artifacts in docs/gateflow/holdings-name-currency and ignored docs/reviews (keep local review artifacts private; accepted checkpoint may reference them without publishing private diagnosis). All fixtures/docs generic, no real record/case/account numbers or production amounts.

Residual risk: Feishu lacks atomic compare-and-set, inherited patch race remains and is assigned to later work unit; do not redesign. Provider availability is best-effort, covered by no-write/manual outcome; live provider validation excluded by explicit scope, user owns later authorized acceptance. Static currency list maintenance is documented, no runtime catalog updater. Public delivery/remote CI/production acceptance remain requiring explicit later authorization. Final local handoff includes worktree/commits/diff, exact pass/fail/skip, independent findings disposition and these boundaries.
