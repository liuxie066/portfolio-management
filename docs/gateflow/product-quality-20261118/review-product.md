# Code Review

## Scope

- Mode: current changes, independent review of the combined PM product-quality work unit
- Branch: `cindy/vibrant-bohr`. Base: `43def4c3997f375bfd73c0c93ee42d9e17023481`.
- Output file: docs/reviews/code-review-20261118-150721.md
- Included scope: publisher/client and application-bundle handoff, report payload, both top-holdings producers, HTML rendering and fixture, CLI money/ratio/quantity and cash-flow discovery copy, NAV calculation equivalence and benchmark, canonical storage freshness and shared finality interactions.
- Excluded scope: production, live service credentials, external execution and provider delivery. Browser and deeper financial failure verification run in parallel under the controller and financial reviewer.

## Findings

### PQ-01-已复审-[中]-Report projection turns an unknown holding value into a priced zero

- **入口/函数**: `ReportQueryService.full_report` and `DailyReportPayloadService._build_daily_report` call the shared `merge_top_holdings`.
- **文件(行号)**: `src/domain/report/holdings_projection.py:36–42,47–65,75–80` in the initially reviewed diff. Renderer uses its result at `scripts/generate_daily_report_html.py:76–80,127–149`.
- **输入场景**: A trusted snapshot shape contains a holding with unavailable `market_value` plus other priced holdings. Unavailable values are modeled by the valuation snapshot projection at `src/app/portfolio_read_service.py:299–315`.
- **实际分支**: `float(holding.get("market_value") or 0.0)` lowers an unavailable input to zero before grouping. Grouped totals and ratios become finite and are always treated as available by the formatter.
- **预期行为**: Preserve an unknown subtotal if any component contributing to it has unknown market value. A zero balance with a positive known denominator still renders as zero.
- **实际行为**: The principal holdings table displays a priced zero or a partial aggregate. The broker table on the same page independently preserves an unavailable subtotal from the raw snapshot.
- **直接证据**: The projection repeats arithmetic on manufactured zeros and the renderer consumes its authoritative top-holdings list. The normal publisher NAV recorder rejects unpriced snapshot rows at target-digest construction even in dry-run, per `src/app/account_nav_recorder_service.py:355–365` and `src/domain/snapshot_contracts.py:795–803,1649–1669`. The separate report/full query and prebuilt renderer path are the relevant available readers of partial facts.
- **影响**: A report can misstate the value and weight of a position or mix two valuations for the same snapshot. Comes from existing shared projection behavior, exposed by the work unit's new unknown-value assurance.
- **建议改法和验证点**: Preserve `None` in the shared projection and calculate weights only when both subtotal and positive denominator are known. Check that known zeros and sort placement agree with the existing default top-N behavior. Exercise real producer-to-renderer contract with a synthetic partial input and both projection consumers. The report/full schema has unconstrained item value types at `src/service/http.py:176` and minor reports forward that same shape at `src/app/report_generation_service.py:62,80,111`.
- **修复风险（低/中/高）**: 中. Existing callers now receive null inside generic rows for missing facts.
- **严重程度（低/中/高/严重）**: 中.

### PQ-02-已复审-[中]-A global label can visually misidentify a selected account

- **入口/函数**: `publish_daily_report.py --account alice` without `--account-label`.
- **文件(行号)**: Initially `scripts/publish_daily_report.py:35–41,289–293` and `scripts/generate_daily_report_html.py:83–85,229–230`.
- **输入场景**: The operator uses the example or installer default label `lx` and selects a different account such as `alice`.
- **实际分支**: The global label became visible as the first label in the account header despite selecting the other account.
- **预期行为**: Label the selected account directly unless an alias applies to it.
- **实际行为**: Initial pass showed `lx · alice`.
- **直接证据**: `config.example.yaml:30` and `scripts/install_linux.py:166` supply `lx`, originally used unconditionally by the parser. Updated parser at `scripts/publish_daily_report.py:105–110` now limits the configured alias to the default account and continues to respect an explicit alias.
- **影响**: Visible conflicting account identity when comparing portfolios.
- **建议改法和验证点**: Compare selected account and resolved default before using the configured label. Exercise omitted account, matching account, different account and explicit label.
- **修复风险（低/中/高）**: 低.
- **严重程度（低/中/高/严重）**: 中.

### PQ-03-已复审-[低]-Receipt promises a full list via the unresolved work queue

- **入口/函数**: `CashFlowEffectReceiptService.build_message` discovery receipt.
- **文件(行号)**: `src/app/cash_flow_effect_receipt_service.py:206–243` and `src/app/cash_flow_effect_service.py:860–874`.
- **输入场景**: A receipt has more than ten effects, including an already applied or auto-acknowledged change.
- **实际分支**: The receipt lists at most ten scan effects. Review triggers a new scan and selects only latest unresolved effects.
- **预期行为**: The described command should surface the stated set of effects.
- **实际行为**: Initial wording advertised the full list, which cannot contain terminal effects.
- **直接证据**: `review()` passes `states=UNRESOLVED_STATES`. Revised receipt calls the command the destination for pending items.
- **影响**: Operator could mistake an omitted completed change for a lost event.
- **建议改法和验证点**: Use wording that identifies the command as the pending work queue. Verify an auto-processed and a pending receipt.
- **修复风险（低/中/高）**: 低.
- **严重程度（低/中/高/严重）**: 低.

### PQ-04-已修复-[低]-Unexpected snapshot status type can stop finality checks

- **入口/函数**: `evaluate_nav_finality` consumers are scheduled NAV, cash effects and quality producer.
- **文件(行号)**: `src/app/nav_finality.py:113–130`. `src/models.py:415–416` permits arbitrary nested JSON values in details.
- **输入场景**: Supported finality with an unhashable snapshot status, such as a JSON array or object in legacy data.
- **实际分支**: Initial version used hash-set membership after accepting finality metadata.
- **预期行为**: Keep the intended finality decision deterministic for compatible rows and reject explicit incomplete markers.
- **实际行为**: Hash membership attempts to hash a list or dict and raises before returning a decision.
- **直接证据**: Controller replaced hash membership with an equality-based check. Independent direct probe with valid `NavWriteContext` and list, dict and null markers returned a decision each time.
- **影响**: Could abort a quality producer refresh or obscure cash-effect gating instead of yielding a determinate result.
- **建议改法和验证点**: Equality comparisons preserve the intended scope. Probe prepared, failed, complete, missing and malformed values.
- **修复风险（低/中/高）**: 低.
- **严重程度（低/中/高/严重）**: 低.

## Coverage and evidence

- Re-review of the shared holdings projection confirmed that duplicate-code and cash/MMF groups propagate an unavailable component into both subtotal and weight. An independent synthetic probe through the full and daily report producers confirmed the distinction from a priced zero and rendered the daily payload. The follow-up report/entrypoint/HTTP suites had 120 passing tests in the inspected workspace.
- Re-ran 55 targeted report entrypoint, renderer, performance and receipt tests under Python 3.12. All passed at the inspected snapshot. Read the controller's initial full-suite log with 1,990 passed. Later edits require updated checks.
- Re-ran `docs/gateflow/product-quality-20261118/benchmark.py`. Identical financial payloads after removing stage timings. Medians were 10.405 to 1.536 ms for 2,500 NAV rows, and 41.897 to 6.788 ms for 10,000 rows. Tracemalloc peaks were 87.8 to 64.6 KiB and 323.7 to 244.9 KiB. Fixed generated rows and seed, no provider I/O, same Python and host, baseline calculator loaded from the selected Git tree. A separate seeded comparison matched 1,000 baseline and current period calculations over randomized unsorted inputs, duplicates, missing periods and zero or negative starts. The benchmark statically has one fake storage call per build. Its assertion checks call arguments, while the count in the JSON is labeled as a constant.
- Followed the publisher through service and direct application routes to snapshot reuse. CNY values originate in the snapshot compatibility view. Notes are deduplicated and HTML escaped. Dynamic account, date, trace, warnings, broker names and holdings labels are escaped. Operational status is derived from explicit recorder flags. Asset captions, heading anchors, table scope and responsive rows were inspected in source and exercised by the synthetic DOM tests. Narrow-screen CSS changes layout without dropping financial columns.
- Read the shared fresh NAV chain through FeishuStorage and its canonical repository. The quality artifact reader retains its offline read ownership and canonical quality producer adds a read. The confirmed cash application calls the same finality evaluator after a fresh account refresh. Separate financial review covers scanner transaction failure and duplicate trust behavior. The reporting projection remains read-only and the UI status concerns the report's own recorder result.
- Checked repository instructions and relevant authorization, source ownership, dependency direction and public contract boundaries. Changes introduce no external resources for report rendering, no new persistence schema and no public route. The docs describe behavior changes in the relevant workflow and report owners. `git diff --check` passed at the initial review snapshot.

## Open Questions

- 无.

## Final re-review

- Report projection now requires a positive total before computing a weight. A zero-value holding has zero weight with a positive denominator and an unavailable weight with a non-positive denominator.
- Shared finality accepts missing or null snapshot status for compatibility. A present non-null status must be complete, and an explicit false persistence flag blocks even when the status claims completion. Malformed list and object markers produce an ineligible decision. The controller was notified to align the cash-flow operator documentation with this rule.
- Discovery receipts with zero pending items omit the review command. Pending receipts describe the command as the route to unresolved items and display the ten-row limit. The existing no-action baseline test passed on retest.
- All 51 targeted checks passed, including the exact weight and no-action baseline regressions. The controller's final full-suite log records 2,012 passing tests and one dependency warning. No remaining code blocker in the reviewed scope.

## Residual Risk

- Screen-reader table announcements and print output on Apple/WebKit assistive technology were not independently verified. Mobile screenshot/browser evidence belongs to the controller. The holdings CSS changes native table display on narrow screens, so verifying the accessibility tree is more useful than relying on markup alone.
- Synthetic timing measurements cover in-memory report payload computation. Production latency, datasets and delivery behavior remain outside this review.
