# Code Review

## Scope

- Mode — current changes, final combined review of the product-quality work unit.
- Branch — `cindy/vibrant-bohr`.
- Base and precommit HEAD — `43def4c3997f375bfd73c0c93ee42d9e17023481`.
- Output file — `docs/reviews/code-review-20261118-150728.md`.
- Included — all production and test changes in the combined worktree, report fixture and source documentation, benchmark source/results, and work-unit record. The implementation was inspected through the CLI and local API boundaries and compared with the selected base. The findings document the reviewed revision and identify fix checks separately.
- Excluded — complete unchanged implementation outside affected call chains, raw browser/binary evidence, actual Feishu delivery, live prices, trading, production latency and migration. No network or production access.
- Parallel review coverage — independent reviewer traced the combined worktree. Other exploratory reviewer findings are tracked by the controller.

## Findings

### CR-01 — 已验证修复｜高｜NAV 读取暂时失败曾替换事实未变的现金流 effect

- **入口/函数** — `pm cash-flow effects scan` → `_commit_scan` and `preview` / `confirm` → `_current_cash_flow_source`.
- **文件(行号)** — `src/app/cash_flow_effect_service.py:213`, `:448`, `:977` and `:1005` on the reviewed revision.
- **输入场景** — Any unchanged current apply-mode cash flow with an existing pending effect and a transient failure of the new canonical NAV read, such as a Feishu outage.
- **实际分支** — Broad exception handling covers both the normalization of canonical cash facts and `_initial_state`, which now performs the fresh NAV I/O. A read exception switches to `_raw_source` and a different hash. The store creates a new blocked correction and supersedes the original effect.
- **预期行为** — An unavailable prerequisite should abort/retry the scan or confirmation and preserve the cash source hash and existing actionable effect. A malformed cash row can still be represented as a blocked raw-source effect.
- **实际行为** — With the unchanged row and otherwise isolated test store, forcing only the fresh NAV read to fail returned scan `success=True` and `status=completed`, with one changed/blocked effect. Preview threw “cash_flow source changed” and named a new correction effect. Each operation persisted a superseded original and a blocked replacement whose error was the NAV outage.
- **直接证据** — The source normalization and prerequisite share the same try block in both call sites. `scan` already has a separate rollback/failed-outbox path when exceptions escape the transaction. The read exception is swallowed before that path.
- **影响** — Unchanged inputs lose their stable workflow identity and preview reference. Scanner receipt and history indicate a successful complete source pass and a correction when the cause was a failed prerequisite read.
- **建议改法和验证点** — Preserve normalization and source hashing independently of finality. Let canonical NAV failures drive the existing failed/retry path. In the source-refresh helper, compare canonical source hash before requesting state for a correction. Run scan/preview/confirm across transient read failure and recovery and assert unchanged source hash, effect ID, workflow history and no CASH writes before successful confirmation.
- **修复风险（低/中/高）** — 中. Keep invalid-source, scheduled activation and same-source terminal recovery behavior.
- **严重程度（低/中/高/严重）** — 高.
- **复审** — Scan and source-refresh now call the prerequisite outside the raw-source fallback. Scan emits its failed receipt and rolls back effect changes if canonical NAV is unavailable. A regression exercises both scan and preview against an unchanged source and checks unchanged version and no holdings writes. Confirmation retains its separate fresh check immediately before any application and its existing source-first safeguards.

### CR-02 — 已验证修复｜中｜正式日报曾对同一缺价持仓展示零市值与未知市值

- **入口/函数** — Publisher → daily report bundle → `DailyReportPayloadService._build_daily_report` → `merge_top_holdings` → HTML holdings and broker tables.
- **文件(行号)** — `src/app/valuation_service.py:211`, `src/app/portfolio_read_service.py:301`, `src/app/daily_report_payload_service.py:126`, `src/domain/report/holdings_projection.py:36` and `scripts/generate_daily_report_html.py:52`, `:127` on the reviewed revision.
- **输入场景** — A nonzero holding with no effective price, an eligible preview and a small enough portfolio for the position to be among the ten rows. Also applies to a merged security or cash bucket with one known and one unknown value.
- **实际分支** — Canonical valuation creates a holding with a null market value. The principal holdings projection converts null to zero before computing the group’s value and weight. The broker projection consumes raw snapshot rows and preserves the null.
- **预期行为** — An unknown value or group subtotal remains unknown and its financial ratio remains unknown. A genuine valuation of zero stays zero.
- **实际行为** — Synthetic paired projection with priced holding B and missing-price holding A produced market value `0.0` and weight `0.0` for A in principal holdings, and null amount/ratio for A’s broker.
- **直接证据** — The renderer formats the cooked `report.top_holdings` row at face value. The integration fixture populates cooked rows directly, so renderer-level absence tests cannot catch the lost evidence.
- **影响** — The holdings table gives the impression that an unpriced security has a known zero financial contribution. Mixed groups can show a known-looking partial subtotal.
- **建议改法和验证点** — Preserve missing values in the owner of the holdings projection and exercise the real report assembler with unknown/known-zero, same-security mixed broker, cash/MMF mixed valuation and missing denominator inputs. Check CLI report consumers of the shared shape.
- **修复风险（低/中/高）** — 中. The same projection feeds the report query service.
- **严重程度（低/中/高/严重）** — 中.
- **复审** — The shared holdings projection preserves null values through both security and cash/MMF accumulation and preserves unknown weight for a nonpositive denominator. Tests cover both orderings of known/unknown constituents and distinguish an actual zero market value. Existing report query and renderer pass the projection through without treating null as 0.

### CR-03 — 已验证修复｜中｜快照状态异常值曾中断整份质量产物

- **入口/函数** — `PMQualityService._valuation_and_nav_datasets` → `evaluate_nav_finality`, also consumed by daily-job and cash effects.
- **文件(行号)** — `src/app/nav_finality.py:128`, `src/app/quality/service.py:516` and `:557`, `src/models.py:416` on the reviewed revision.
- **输入场景** — One persisted NAV row with supported finality metadata and a user-edited JSON array or object in `details.snapshot_status`. The model explicitly supports arbitrary nested detail values.
- **实际分支** — A successful finality metadata check reaches a set membership test with an unhashable value. The quality method catches source read failures, then calls the evaluator outside that try block.
- **预期行为** — A malformed explicit snapshot marker should yield an ineligible decision and a diagnostic. Legacy rows without the marker should remain eligible under their existing rule.
- **实际行为** — Directly reproduced `TypeError` for both JSON-shaped values. The quality producer receives an exception instead of a decision for the account.
- **直接证据** — Set membership hashes input before comparison. The read/validation boundary accepts the surrounding map and never checks these nested values.
- **影响** — A single malformed row can abort build/publication of the multi-account quality artifact rather than produce scoped unavailable or untrusted evidence.
- **建议改法和验证点** — Classify invalid marker shape as ineligible in the shared evaluator and test nested object/array with existing legacy and complete fixtures.
- **修复风险（低/中/高）** — 低.
- **严重程度（低/中/高/严重）** — 中.
- **复审** — The shared evaluator compares the optional marker without hashing it. Any non-null explicit marker other than the writer's `complete` status is ineligible. Quality regression coverage includes object and array evidence and demonstrates scoped untrusted status alongside legacy and source failure paths.

### CR-04 — 已验证修复｜中｜跨账户日报曾继承默认账户显示名

Before the fix, publisher CLI supplied the default account key as its implicit label. Applying that label to an override in the new report hero introduced two identities. The parser now scopes the configured alias to its configured account and gives an explicit CLI label precedence. The publisher checks the confirmed bundle account before applying the configured alias, including when the service chooses a different default. The targeted account override, matching default, different service default and explicit-label cases passed.

### CR-05 — 已验证修复｜低｜发现回执曾承诺可查询所有变化

Discovery payload details are capped at ten. Cash flow review returns the latest unresolved effects, which excludes resolved changes. The updated label says “待处理项” and preserves the existing command. The targeted receipt assertion passed.

## Covered paths and verification

- Capital facts follows the public service adapter, resolved account, account-scoped canonical repository read, existing period-end and prior-calendar anchor selection, and unchanged formulas. The application maps the source exception to its existing failed response. Quality reads canonical facts and retains its independently scoped duplicate audit and separate read-only local artifact endpoint. No new protocol or schema exposure observed.
- NAV recorder prepares and commits the exact-set holdings snapshot, records incomplete evidence during failure, and verifies completion. Daily-job still prefers its compensation task and recovery evidence before shared finality. Blank-marker legacy behavior and completed/prepared/failed paths were checked against cash confirmation and quality regression coverage.
- Cash scan retains scanner lock and SQLite transaction. Confirm takes scanner, sorted account and effect locks, validates latest version, recomputes the preview hash, and checks preceding NAV before operational writes. Record-only and observe-only routes retain their own gates. Finding CR-01 identifies the new failure mode before the original transaction and recovery handling.
- The domain period calculator preserves stable first-row behavior for same-date ties and consumes generators once. Independently matched 4,000 deterministic baseline comparisons across shuffled, duplicate, zero-base, missing and malformed modern-period samples. Benchmark source swaps only the performance implementation, uses equal payload assertions and measures allocation separately from elapsed time.
- Publisher resolves report account and mode from the bundle, performs no renderer-side refresh, escapes untrusted text and preserves direct file fallback. Reactivity and external assets are absent. Renderer tests cover missing/zero distinction, statuses, navigation targets, warnings and table markup. Report producer ownership gap recorded in CR-02. Numeric CLI changes preserve JSON payloads and cover missing strings and nonfinite values. Human formatting uses existing float precision for valid amounts.
- Targeted renderer, return, cash receipt, cash effect, quality and capital facts tests passed (122). Reviewed the 1,990-pass full pytest log as evidence for the preceding revision only, since fixes and new tests followed. Final mandatory checks belong to the delivery revision. Scope excludes network and production checks per authorization.

## Open Questions

- 无.

## Residual Risk

- Remote reads and writes continue to use Feishu’s existing consistency limits. No stronger cross-table atomicity is inferred. Where a read requires a previous point row, the existing repository logs duplicate rows and exposes its first match. The duplicate audit remains a separate ownership boundary from the new freshness change.
- The canonical NAV loader rebuilds the whole account projection and the quality producer separately audits duplicates. Added cost is acknowledged in the work-unit record, and the benchmark concerns CPU/allocations of report construction only.
- HTML browser artifacts are synthetic, and exact distributed production semantics, Feishu appearance and production timing were outside the review. Targeted text and structural tests do not measure assistive-technology behavior.
