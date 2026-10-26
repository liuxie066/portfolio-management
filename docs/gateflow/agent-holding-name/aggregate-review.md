# Aggregate deepreview — Agent single-record name interface

## Scope
- Mode: current changes aggregate review.
- Base:931fc086a23ec10a06edb2f7a81db80101cef3f0; reviewed head:d6e775fb422ff4a993473e5f7080d0e18770f109.
- Branch feat/agent-holding-name; worktree /tmp/pm-agent-holding-name.
- Complete changed source/tests/docs read, plus actual HTTP/auth, operation-state, workflow and Feishu repository mutation chain. Read prior independent review122039, implementation/validation artifacts and external QA tests. No subreviewers; no source edits, test execution, production/credentials or external writes by this reviewer.

## Findings

### AGG-01-已修复-[中]-Single-record confirm performs account-wide source refresh
- **入口/函数**: POST name-confirm -> AgentHoldingNameService.confirm -> HoldingsWorkflowService.complete_name_from_preview -> _execute_patch -> HoldingsRepository.patch_holding_record.
- **文件(行号)**: src/app/holdings_workflow_service.py:971; src/feishu/repositories/holdings_repository.py:553–554 and249–255.
- **输入场景**: An authorized principal has exactly one record grant and confirms a valid empty HK name preview.
- **实际分支**: Protected repository patch successfully writes/reads the exact target, then unconditionally calls _read_fresh_holding_account_slice(identity.account) and publishes that full slice to persistent cache.
- **预期行为**: Approved single-record interface does not scan other holdings; its narrow permission must be maintained through the real repository boundary, not just outer service methods.
- **实际行为**: get_raw_holdings(account=...) reads every holding in the account, including financial fields outside the record grant. This is not another remote holding write or demonstrated response leak, but it violates exact-record read scope and can make an otherwise proven patch depend on unrelated invalid holdings/cache refresh.
- **直接证据**: _read_fresh_holding_account_slice calls _convert_raw_holdings(self.get_raw_holdings(account=requested_account)); patch_holding_record invokes it after exact readback, then _publish_holding_account_slice flushes the persistent account index. Both product and independent new tests replace storage.patch_holding_record with fakes, so the claimed exact-record integration does not exercise this repository path.
- **影响**: Broader private-source reads than the grant; mock-only isolation proof incomplete.
- **建议改法和验证点**: Add a narrow mode to the existing protected patch boundary that preserves identity/base-digest/final exact-readback checks but invalidates affected cache rather than account-wide reload; existing callers retain legacy refresh default. Agent method must explicitly select narrow mode. Add real-repository/fake-client integration asserting only exact get_record calls and one asset_name patch (plus normal update timestamp), rejecting list_records/account scans; preserve unrelated cases/rows and cache correctness. No parallel direct DB/Feishu mutation implementation.
- **修复风险（低/中/高）**: 中, shared repository boundary requires default-behavior regression.
- **严重程度（低/中/高/严重）**: 中.
- **Disposition**: accepted / 已修复 after controller/implementer delta and independent QA; reviewer made no source changes. See re-review below.

## Paths reviewed / adversarial conclusions
Authentication rejects disabled/malformed configuration, missing/bad bearer, ambiguous principal/token and wildcard/blank grants; exact record+account+broker grants are checked before provider work. A nonce is insufficient authority. Active-policy digest binds prior operation to principal/token/grants, with cached-config revocation limits accurately documented. HTTP input shape/query rejection and generic errors/no-store reduce leakage; existing app bootstrap remains separate from disabled Agent calls.

Atomic previewed->executing claim binds principal/record/grant and expiry, serializing same nonce. Lock/fresh digest handles separate previews. Conservative executing/unknown states do not reissue; missing/old-schema status uses read-only SQLite and no provider. Source/evidence changes reject without name overwrite. A durable rejected result after claim returning200 is explicitly documented; unclaimed expired request409 remains distinct. Full same-record case observation prevents false currency-blocker closure; receipt suppression reaches materialization, absent closure and finalization. Natural self-event branch for existing cases does not insert discovery; tests cover it. Prior plan P1/QA-01/race-to-Futu fixes are present, not merely reported.

Agent namespace exclusion from OM exporter is precise and preserves the pinned OM contract boundary; no immutable contract artifact changed. No new credential provisioning, secret transfer, arbitrary executor, currency mutation, background retry daemon or production enablement in diff.

## Validation evidence (reused, not rerun)
113focused and39independent passed. Native full1764passed2known baseline date-fixture failures; scoped external clock supplemental1766passed. Compile/Ruff/diff pass. These are reported acceptance evidence for existing tests, not proof of uncovered real repository narrow-read behavior above. Original failures preserved in validation ledger. No claim native full-suite green.

## Open Questions
No other blocking question identified. AGG-01 is resolved/re-reviewed below; no remaining blocking question.

## Residual Risk
- Inherited Feishu non-atomic read/PATCH race: later storage work unit.
- Actual principal/token/grant provisioning, config reload and live enabling: separately approved deployment actions; controller owns public delivery.
- Durable operation retention and unknown/crash recovery: maintainer follow-up, no blind replay.
- Live provider and production end-to-end acceptance: not performed by local tests.
- Pre-existing date fixture reproducibility: maintainer test follow-up.

## Decision
Initial decision was **fail pending AGG-01 resolution**; preserved here as history. Final decision below supersedes it.


## AGG-01 fix re-review — final decision

Reviewed frozen working-tree delta from d6e775fb422ff4a993473e5f7080d0e18770f109 (controller will create the final accepted checkpoint). Agent complete_name_from_preview explicitly passes refresh_account_cache=False to _execute_patch; that passes the keyword through the actual FeishuStorage HoldingsMixin facade to HoldingsRepository.patch_holding_record. The repository retains exact identity/base-digest checks, name-only business patch and exact readback. Only the new False branch invalidates existing account cache entries instead of fetching/publishing all account records. Legacy default True and older callers retain prior behavior. No direct bypass mutation path was introduced.

Source delta for the three production files (workflow/facade/repository) reviewed SHA256: 8d3d8d19ee02a8cc8d159968ccdf12a926c7dd306dbe9e6dbb6e0f927d46b34f. Documentation now explicitly distinguishes asset_name business-field mutation from repository-generated updated_at metadata; this is existing metadata behavior, not a currency/quantity/cost write.

Read independent real-adapter test_agent_real_adapter.py: actual FeishuStorage/repository with synthetic exact client logs every record read and attempted list; asserts only target get/patch, correct metadata keys, other row/currency/quantity/cost unchanged, safe cache invalidation preserving other-account cache, zero receipts, no transport on replay/status. Independent result40passed0.82s. Product regression was strengthened during review to increment list_records_calls BEFORE raising, because the workflow can catch a transport assertion and recover from exact readback; this ensures the committed regression detects a broad-read attempt instead of falsely passing after exception absorption. The final assertion remains list_records_calls==[].

Reused corrected-source evidence: focused174passed2.11s; native full1765passed2known baseline date failures13.34s; supplemental full1767passed12.45s with only those two external fixture clocks fixed; compile/Ruff/diff passed. These replace pre-fix counts for corrected source. Reviewer did not rerun tests or perform production/credential/external operations. Controller confirmed test-only hardening regression: tests/test_agent_holding_name.py18passed0.81s, aggregate-test-hardening.log; source evidence remains valid. Controller owns final immutable checkpoint.

**Final aggregate decision: pass-with-risks. AGG-01 accepted/已修复; no unresolved substantive source finding.** Inherited CAS, config activation/credential provisioning, operation retention/unknown recovery, live acceptance and baseline fixture risks remain classified above. No permission to enable Agent access or perform real holding actions is implied by review. Next gate: accepted aggregate checkpoint and controller-authorized delivery gates.
