goal: Keep automatic NAV on the preceding eligible CN/HK/US trading date, block final writes without target-period
  holdings/price/FX evidence, and make the public NAV API observe writes from the separate scheduled process.
non_goals:
- Change CN/HK/US any-open trading-date eligibility.
- Relabel or overwrite existing production NAV rows.
- Implement code, commit, push, release, upgrade, or mutate production in this design-only workflow.
scope: Design final daily NAV evidence admission and public NAV read freshness, including failure behavior, source
  gaps, and regression acceptance.
success_signals:
- Final daily NAV cannot use unproved target-period holdings, prices, or FX.
- A valid target-date evidence artifact can still use the canonical NAV writer.
- Public NAV read observes a separate-process successful write without an API restart.
- Full Devflow design optimization closes blocking design findings.
authorized_slices:
- slice: Completed design optimization input
  design_doc_ref: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
  success_signal:
  - Full Devflow design optimization closes blocking design findings.
  depends_on: []
- slice: Fresh public NAV reads
  design_doc_ref: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
  success_signal:
  - Public NAV read observes a separate-process successful write without an API restart.
  depends_on: []
- slice: Final daily NAV admission
  design_doc_ref: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
  success_signal:
  - Final daily NAV cannot use unproved target-period holdings, prices, or FX.
  - A valid target-date evidence artifact can still use the canonical NAV writer.
  depends_on: []
slice_checkpoints:
- slice: Completed design optimization input
  diff_fingerprint: 469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
  validation: Four-reviewer panel and planreview completed in the prior design node.
  done: true
- slice: Fresh public NAV reads
  diff_fingerprint: 2363fd5dd1879778de3ae08a475b332309ec36b3b1854b42bce5761876bf5384
  validation: Red stale-read and empty double-fetch regressions; 46 focused tests passed; final full suite passed.
  done: true
- slice: Final daily NAV admission
  diff_fingerprint: e1f57ff61683d469eb21460f7468c0df08d50cbc26020e1f6f60b53daab0190f
  validation: Current-only and legacy evidence block; proved replay, cutoff, closed market, FX and dry-run regressions
    pass; final full suite passed.
  done: true
user_confirmation:
- 'User: /devflow 设计优化方案'
- 'User: 完整流程（推荐）'
- 'User: 维持前一交易日 NAV，并在目标日证据不足时阻断'
prd_doc: not-applicable
prd_doc_ref: not-applicable
design_doc: docs/nav-daily-consistency.md
design_ref: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
implementation_workspace: /private/tmp/pm-nav-daily-consistency-impl
review_base: 4a5aa35de470e1ebea030652dcf92296210a5422
authorization_diffs:
- when: 2026-09-28
  what: User authorized Impl; source-dependent routine evidence slice remains deferred by the design until source
    capability is proven.
  ref: 'User: impl'
workflow_version: 2
mode: node
workflow_path: null
node_sequence:
- Impl
current_node: Impl
internal_step: Final scope closure
status: completed
next_action: No further action in this authorized Impl node; routine source validation and delivery are separate.
approved_scope_ref: user-message-previous-trading-day-target-evidence
path_approval_ref: user-message-full-design-workflow
implementation_baseline:
  design_doc: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
  implementation_workspace: /private/tmp/pm-nav-daily-consistency-impl
  review_base: 4a5aa35de470e1ebea030652dcf92296210a5422
  head: 4a5aa35de470e1ebea030652dcf92296210a5422
  git_status: ' M .devflow/design-panel.md;  M .devflow/scope.md; ?? docs/nav-daily-consistency.md'
  staged: []
  unstaged:
  - path: .devflow/design-panel.md
    hash: 7cb3485bd173189c75ddd151835579301f53ac6fd194db134d0062d351aab63d
    size: 2934
  - path: .devflow/scope.md
    hash: e30865af91e032705ae88206d2c82fd38582cb541427a0bc710ee7771d22933e
    size: 2878
  untracked:
  - path: docs/nav-daily-consistency.md
    hash: 469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
    size: 15323
inventory:
- path: .devflow/design-panel.md
  status: ' M'
  hash: 7cb3485bd173189c75ddd151835579301f53ac6fd194db134d0062d351aab63d
  size: 2934
  type: file
  mode: 0o644
  classification: inherited-design-input
  evidence_ref: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
- path: .devflow/scope.md
  status: ' M'
  hash: null
  size: null
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/app/account_nav_recorder_service.py
  status: ' M'
  hash: e26d7dcd3b90d363b86b471e61ae8c4dd8bc97b831cb99348366fbdb12254bc4
  size: 29428
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/app/daily_account_nav_service.py
  status: ' M'
  hash: c0d7ce02a48d50df004a46ed64e64d12c635c494772cd3b7b32f0479db6416cb
  size: 6511
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/app/daily_nav_job_service.py
  status: ' M'
  hash: 7a672f8982f6a47ee362dda0331f5340721f27878bbcb556aa4083e8a53c0431
  size: 21435
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/app/nav_valuation_evidence_service.py
  status: ' M'
  hash: 89f25bc17468a1f75cfbd7ebbf3762f0adf00de614e1d6e0f662f06810632561
  size: 33391
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/feishu/_nav_mixin.py
  status: ' M'
  hash: 2fa3f6ae981d097a16f099254c398f02b3edc9246a271c33a0738f126f29090b
  size: 3328
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/feishu/repositories/nav_history_repository.py
  status: ' M'
  hash: e9711dbc3de5af23fdf0259883283c127b1b1c08cd3b4723ed0e9fab8d187786
  size: 66299
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: src/service/application.py
  status: ' M'
  hash: aec4785308d206bb97c79cc1fe36edd703d94a1fcf8012fb9e2c659388ed357a
  size: 29943
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: tests/test_daily_nav_services.py
  status: ' M'
  hash: 07979007be39072826ba5e21a811c0bcfb97ebac511ff4949bfba62827cccece
  size: 63003
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: tests/test_nav_valuation_evidence_service.py
  status: ' M'
  hash: 0d03074370d499213e4e16f1d73ccafa1bb03107db5ae4631a09b8768a4209aa
  size: 42224
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: tests/test_service_application.py
  status: ' M'
  hash: 0cafb06ced2b832c3e3421cf8fd7f31d9907c5f32c53b5b1fd912bdd50f87b54
  size: 50964
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: docs/nav-daily-consistency.md
  status: ??
  hash: 469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
  size: 15323
  type: file
  mode: 0o644
  classification: inherited-design-input
  evidence_ref: docs/nav-daily-consistency.md@469fb09b0acaa357e0da11d5d58bd25ed683a527f02fa259d022a9382e2f8ee1
- path: src/app/nav_target_evidence.py
  status: ??
  hash: 2b88efbae75421cd1818b2143dd544da013201afd1596b560649c01f14318cc3
  size: 10303
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
- path: tests/test_nav_public_freshness.py
  status: ??
  hash: e2f172853f17ac05cee7c60ee338266cf69c14a2e83d277978ad295c8ed48b28
  size: 2776
  type: file
  mode: 0o644
  classification: planned
  evidence_ref: 'Impl validation: 1528 passed, compileall, Ruff, diff-check'
content_revision: implementation@396084a8d09c76c33a1c6830ba54d2c6b32baa23a77011214d236d1b07f2757c
planreview_round: 1
deepreview_round: 0
in_flight: []
evidence_paths:
- .devflow/design-panel.md
- docs/reviews/plan-review-20260928-092131.md
- tests/test_nav_public_freshness.py
- tests/test_daily_nav_services.py
- tests/test_nav_valuation_evidence_service.py
blocking_findings: []
residual_risks:
- item: Existing 2026-09-25 rows may lack target-date evidence.
  classification: assigned-to-later-work-unit
  owner: portfolio operations
  destination: read-only audit and separately authorized repair
- item: Routine holdings and FX source lacks a proven target-period effective date.
  classification: assigned-to-later-work-unit
  owner: NAV data-source design
  destination: source validation before routine evidence slice
- item: Feishu list-read visibility immediately after a concurrent write is unverified.
  classification: assigned-to-later-work-unit
  owner: API operations
  destination: live acceptance before claiming immediate read-after-write consistency
- item: No routine source currently proves target-period holdings, session closes, and FX effective coverage; scheduled
    NAV may remain blocked.
  classification: assigned-to-later-work-unit
  owner: NAV data-source design
  destination: Verify provider receipts and implement the deferred routine evidence source slice.
