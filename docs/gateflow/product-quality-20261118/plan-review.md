# Plan review — NAV finality and canonical reads

**Target** [Product quality work unit](work-unit.md), financial trust decisions in the implementation plan, reviewed against base `43def4c`. The scope and evidence section supplies the goal handoff. The review covers completion markers and read freshness for cash effects, quality, and capital facts. It used local source, tests, and a synthetic Feishu client.

## Assumptions tested

- The daily writer persists finality on the initial NAV row while snapshot status is `prepared` and persistence is false. A successful snapshot write patches the same row to `complete` and persistence true. The failure patch keeps finality and marks the snapshot `failed`. The snapshot and compensation writer already verify bound details. A change to the read-side eligibility decision fits its ownership and supports legacy final rows with absent snapshot markers. [Writer](../../../src/app/nav_record_service.py#L203) · [Completion patch](../../../src/app/nav_record_service.py#L384) · [Recovery classifier](../../../src/app/snapshot_service.py#L325)
- The daily job already returns `recovery_required` for rows with `snapshot_persisted=false` and for unresolved local tasks. Cash Effects and quality each use the shared finality eligibility rule for persisted NAV, while prospective NAV quality uses its own input-based gate. The prepared and failed cases should preserve daily-job recovery output and block eligibility in the other two consumers. [Daily job](../../../src/app/daily_nav_job_service.py#L124) · [Quality producer](../../../src/app/quality/service.py#L556) · [Prospective gate](../../../src/app/quality/policy.py#L81)
- Capital facts selects persisted month/year anchors under the existing calendar rules and obtains external cash flow separately. Its own account-scoped canonical read can preserve that contract. Quality refresh derives its NAV decision before the duplicate audit, so the audit's refresh cannot repair a decision already built from stale rows. [Capital facts](../../../src/app/capital_facts_service.py#L48) · [Capital bridge contract](../../service.md#capital-bridge-facts) · [Quality refresh order](../../../src/app/quality/service.py#L212)

## Findings

### 1-未修复-高-Cash-effect history refresh must reach Feishu

- **位置** Financial plan, cash-effect history presence and confirmation recheck.
- **问题类型** 契约缺失 / 缓存下的状态机漏洞。
- **当前写法** Scope history-presence memoization to the scan and refresh again before the confirmation finality check. The plan leaves the storage freshness of both reads implicit.
- **反例/失败场景** A long-lived service already has an account NAV indexed. A separate writer replaces its stored details with failed snapshot evidence, or finishes a previously prepared snapshot. Resetting the effect service memo still yields the old account index through both storage getters. A synthetic test with the real FeishuStorage, an in-memory index cache, and the Feishu test stub primed a complete row and changed its remote details to failed. Ordinary history and date-specific reads both returned complete. An explicit fresh history read returned failed. Baseline finality also incorrectly accepted that fresh failed row.
- **为什么有问题** Cash Effects relies on preceding final NAV to permit a principal change and on history presence for the new-account exception. The two decisions need account-scoped canonical facts. The history getter refreshes Feishu when requested. The point getter normally reads the existing index. The correction path calls the same state derivation outside scans.
- **直接证据** [Cash-effect state and both getters](../../../src/app/cash_flow_effect_service.py#L213) · [Correction path](../../../src/app/cash_flow_effect_service.py#L978) · [Confirmation guard](../../../src/app/cash_flow_effect_service.py#L1926) · [NAV repository getters](../../../src/feishu/repositories/nav_history_repository.py#L1142) · [Cash Effects state model](../../cash-flow-holding-effects.md#L306).
- **影响** A pending or previewed effect may pass confirmation against a stale apparently complete NAV. A newly completed prior NAV can also leave an eligible flow stuck in scheduled.
- **建议改法和验证点** Use an account-scoped canonical history result for both history presence and the previous trading day's row. Share it only within the scan. Obtain a separate canonical result for the confirmation gate under the existing locks. Preserve the calendar, new-account exception, and dependency-failure behavior. Exercise the same service across scans and change the canonical NAV after preview. Assert a full CASH write never begins when confirmation sees failed or prepared evidence, and a scheduled effect activates after the preceding NAV completes. Use stubs that distinguish ordinary from explicit fresh reads.
- **修复风险（低/中/高）** 低。Reuse the existing repository fresh read and scope reuse to a single application operation.
- **严重程度（低/中/高/严重）** 高。Accepted candidate for the work-unit controller.

## Open questions

No decision from the user is required. Implementation review should check recognition of explicit incomplete status and negative persistence while preserving absence of snapshot fields on legacy rows. The writer publishes nested evidence and top-level markers together. A nested marker that explicitly indicates incompleteness is relevant evidence when the eligibility rule checks snapshot markers. [Writer formats](../../../src/app/nav_record_service.py#L203) · [Failed format](../../../src/app/nav_record_service.py#L413)

## Residual risks and tracking

- A canonical read and the existing duplicate audit each list the account's NAV rows during quality refresh. The duplicate audit also checks uniqueness and publishes the index. Retain the two responsibilities in this work unit. Measure remote read cost under separately authorized rollout observation if it matters. [Duplicate audit](../../../src/feishu/repositories/nav_history_repository.py#L318)
- Feishu still supplies separate cross-table operations, with application-level locks and synthetic verification in this work unit. Live timing and operator recovery remain in the existing deployment acceptance boundary. [Product boundary](../../product.md#开发必须保持的约束)

## Conclusion

`fail` as written. Make canonical read semantics explicit for the cash-effect state and write gates. The shared read-side finality decision and the capital/quality freshness path fit the approved scope.

## Re-review adjudication

**PR-FIN-01 已修复（方案）**。The amended plan requires the existing fresh NAV history read for the scan and for confirmation under the existing locks. The history read refreshes the account index used by the preceding-day lookup. A failed canonical read reaches the existing error path. The plan specifies synthetic regressions for absent → present, complete → failed, and confirmation read failure before any CASH write. [Amended plan](work-unit.md#L67)

A local check with real FeishuStorage and the synthetic client exercised absent → present and complete → failed on the same instance. Each fresh read made one client listing. The following date lookup returned the refreshed status from memory without another listing or a disk-cache write. [Fresh read and date lookup](../../../src/feishu/repositories/nav_history_repository.py#L1142)

**Re-review conclusion** `pass`. The amended plan resolves the finding. The end-to-end state regressions and pre-write error assertion remain implementation acceptance checks.
