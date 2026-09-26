goal: >-
  A Beijing-date NAV is eligible when any of CN, HK, or US trades on that date;
  the automatic NAV date and preceding-NAV finality use the same rule.
non_goals:
  - Change intraday MarketTimeUtil or quote-cache policy.
  - Change timer scheduling, market-specific NAV calculation, or other NAV write gates.
  - Commit, publish, release, or upgrade production.
scope: BusinessCalendarService, DailyNavJobService, CashFlowEffectService, runtime calendar configuration, current NAV operations documentation, and relevant tests.
success_signals:
  - An open CN, HK, or US market makes a date eligible, including 2026-09-25 when CN is closed and HK/US are open.
  - Three confirmed closures skip a date; unavailable evidence without a confirmed opening blocks automatic writes.
  - Automatic NAV date and preceding-final-NAV lookup use the same rule.
  - Existing NAV authorization and write gates remain intact; focused and project gates pass.
authorized_slices:
  - slice: Calendar evidence
    design_doc_ref: docs/nav-trading-calendar.md@b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f
    success_signal:
      - An open CN, HK, or US market makes a date eligible, including 2026-09-25 when CN is closed and HK/US are open.
      - Three confirmed closures skip a date; unavailable evidence without a confirmed opening blocks automatic writes.
    depends_on: []
  - slice: Consumers and operations
    design_doc_ref: docs/nav-trading-calendar.md@b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f
    success_signal:
      - Automatic NAV date and preceding-final-NAV lookup use the same rule.
      - Existing NAV authorization and write gates remain intact; focused and project gates pass.
    depends_on: [Calendar evidence]
slice_checkpoints:
  - slice: Calendar evidence
    diff_fingerprint: 222b19d582aea8198b45c4b2aadb3294c105aafee1d2f480fcd6ffe581fd1163
    validation: python3.12 -m pytest tests/test_daily_nav_services.py -q; 43 passed
    done: true
  - slice: Consumers and operations
    diff_fingerprint: df9e43940e06f35ea59d20643fe6d1eacc60a1df4d8a83515cd19c2c8d9214d2
    validation: python3.12 -m pytest tests -q; 1519 passed; compileall, Ruff, git diff --check passed
    done: true
user_confirmation:
  - 'User: nav判断是不是交易日的时候，只要cn、hk、us 中，有一个没有休市，就更新净值'
  - 'User: 用 devflow 实现'
  - 'User: full'
prd_doc: not-applicable
prd_doc_ref: not-applicable
design_doc: docs/nav-trading-calendar.md
design_ref: docs/nav-trading-calendar.md@b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f
implementation_workspace: /private/tmp/pm-nav-calendar-devflow
review_base: 8e8cbf392da946544412bd31f904f1b322a1959e
authorization_diffs: []
workflow_version: 2
mode: workflow
workflow_path: full
node_sequence: [Brainstorm, Save Design, Improve Design, Impl, Review]
current_node: null
internal_step: null
status: completed
next_action: No remaining authorized Devflow action; delivery requires separate authorization.
approved_scope_ref: user-message-nav-any-cn-hk-us
path_approval_ref: user-message-full
implementation_baseline:
  design_doc: docs/nav-trading-calendar.md@b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f
  implementation_workspace: /private/tmp/pm-nav-calendar-devflow
  review_base: 8e8cbf392da946544412bd31f904f1b322a1959e
  head: 8e8cbf392da946544412bd31f904f1b322a1959e
  git_status: "?? .devflow/; ?? docs/nav-trading-calendar.md"
  staged: []
  unstaged: []
  untracked:
    - {path: .devflow/scope.md, hash: 4ffddb654037a1f5e4cd75ade14daaf91360dbe83eaf1b3847c8a14c2bbd7a81, size: 2450}
    - {path: .devflow/design-panel.md, hash: b78107dd1fb83240fe15b59aa1e02ca3f44a949cb33c074c09e3d7b992530a60, size: 2202}
    - {path: docs/nav-trading-calendar.md, hash: b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f, size: 7707}
    - {path: docs/reviews/plan-review-20260926-133445.md, hash: dcf647b75cfef808a4a2c1db6c2611cfdc29acfc47e24d543bfeaa0c62e6cbd6, size: 2859}
    - {path: docs/reviews/plan-review-20260926-133522.md, hash: 7e45c93f2f70e2d562fe303673123d36fadbf34472497277749a0f543ad5e7ec, size: 1865}
inventory:
  - path: .devflow/design-panel.md
    status: ??
    hash: b78107dd1fb83240fe15b59aa1e02ca3f44a949cb33c074c09e3d7b992530a60
    size: 2202
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: README.md
    status: M
    hash: 1e9d63a413efa6bdd32f4474d369ef0d64199c0118d0dfcb6c9fa6ee3a6789ee
    size: 13481
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: config.example.yaml
    status: M
    hash: 866572bb2bf02ce29b0d25b966ddd436b2fcb27a6ce305738026f49484d72a20
    size: 1840
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/INDEX.md
    status: M
    hash: 1a4c3437e4a7ab8e46aafbfeb674f1de2759d6ea736187d630412cdab7420178
    size: 4049
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/architecture.md
    status: M
    hash: 7dc5526a7f88f4fa576f3e391d3647664d214f2ce07bf099bf8f348634f44692
    size: 8338
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/deploy-linux.md
    status: M
    hash: e0bc4bdaf58d593f1bd9de6944880a2dbad48663ebbac3c67a2e87797d1cff2a
    size: 16985
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/nav-trading-calendar.md
    status: ??
    hash: b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f
    size: 7707
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/runbook-onepage.md
    status: M
    hash: c68d19e5f6725bc0f8c12e4c7918662a06c25336073bd13801c7077c504b1d3a
    size: 4471
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/runbook.md
    status: M
    hash: 6921ebd41636a18194e97571ef9dedbbff7eb0794b562feeea683605e93e15d5
    size: 8589
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: docs/service.md
    status: M
    hash: 0f866204c4e5f6f15c97a0b7c36abd0a1da0c1745a9306c188f8642bce8e28d9
    size: 12657
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: scripts/install_linux.py
    status: M
    hash: 778aabcad5f799c6cb6ee57ea82c62ede39d8dd1ac80b95be8ed6384bdba4589
    size: 44905
    type: file
    mode: 0o755
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: src/app/business_calendar_service.py
    status: M
    hash: 500942342981888167f07a894792c4f9f63276da944a31e2db944ad8a501da0a
    size: 4775
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: src/app/cash_flow_effect_service.py
    status: M
    hash: b36ccbb6c64e4ffb4eca72ba6ebc6cb860c9a08f81eb9d3fe50fabebea898f0c
    size: 97421
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: src/config.py
    status: M
    hash: 58055cc07171cfe18348006861f26395b56505322bafc6d0c4e1a6e2783455cf
    size: 34256
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: tests/test_cash_flow_effect_service.py
    status: M
    hash: c1c7c8fe3984573b84764494cff9173746d1b547a14af80647525581aac714a6
    size: 51922
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
  - path: tests/test_daily_nav_services.py
    status: M
    hash: 96c25c31b9947b4cba530b4b67809929039f5523d6afc679c445edadfe785512
    size: 61406
    type: file
    mode: 0o644
    classification: planned
    evidence_ref: docs/nav-trading-calendar.md
content_revision: docs/nav-trading-calendar.md@b590fcf2cf2f97466c83fe107b064d709a3d6ddf6b2ae6c7b9c0e7ffc90d063f
planreview_round: 2
deepreview_round: 1
in_flight: []
evidence_paths:
  - .devflow/design-panel.md
  - docs/reviews/plan-review-20260926-133445.md
  - docs/reviews/plan-review-20260926-133522.md
  - docs/reviews/code-review-20260926-134321.md
blocking_findings: []
residual_risks:
  - item: Futu calendar excludes temporary exchange closures.
    classification: needs-new-issue-or-user-decision
    owner: product/operations
    destination: decide a live-state or exchange incident source if temporary closures become a requirement
  - item: Changed adapter has not run against production OpenD.
    classification: assigned-to-later-work-unit
    owner: delivery/operations
    destination: verify after separately authorized source delivery and deployment
