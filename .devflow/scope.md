goal: Design a per-user macOS launchd deployment with full Linux task coverage, Keychain delivery for Agent and Listener App Secrets, and Futu SDK checks in the launchd interpreter.
non_goals:
  - Implement or activate LaunchAgents or Keychain items in this workflow.
  - Change Linux systemd deployment, Feishu roles/schema, NAV business rules, or add automatic historical replay.
  - Commit, push, create a PR, release, upgrade, subscribe Feishu, or perform business writes.
scope: Brainstorm, save one macOS design owner, and improve that design through four independent suggestions and any applicable plan review.
success_signals:
  - The design maps every current Linux task to an explicit macOS trigger, command, credential role, and activation boundary.
  - The Keychain design is fail closed, keeps secrets out of argv/env/plists/logs, and defines unattended preflight, rotation, and recovery.
  - Futu SDK import is evidenced for the current global Python and required again in the future launchd runtime interpreter.
  - Four independent design suggestions are adjudicated and the final design has no unresolved blocking risk.
  - The Mac Keychain backend resolves the three named secrets from one pinned file, fails closed, and preserves Linux behavior.
  - The Mac installer renders the approved jobs and preflights with strict venv binding and no automatic activation.
  - The runtime Futu import, lx/sy profile, and OpenD checks are explicit hard gates before financial timers.
authorized_slices:
  - slice: S0 approved design input
    design_doc_ref: docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
    success_signal:
      - The design maps every current Linux task to an explicit macOS trigger, command, credential role, and activation boundary.
      - Four independent design suggestions are adjudicated and the final design has no unresolved blocking risk.
    depends_on: []
  - slice: S1 Keychain and quality failure
    design_doc_ref: docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
    success_signal:
      - The Keychain design is fail closed, keeps secrets out of argv/env/plists/logs, and defines unattended preflight, rotation, and recovery.
      - The Mac Keychain backend resolves the three named secrets from one pinned file, fails closed, and preserves Linux behavior.
    depends_on:
      - S0 approved design input
  - slice: S2 launchd assets
    design_doc_ref: docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
    success_signal:
      - The Mac installer renders the approved jobs and preflights with strict venv binding and no automatic activation.
    depends_on:
      - S1 Keychain and quality failure
  - slice: S3 Futu acceptance
    design_doc_ref: docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
    success_signal:
      - Futu SDK import is evidenced for the current global Python and required again in the future launchd runtime interpreter.
      - The runtime Futu import, lx/sy profile, and OpenD checks are explicit hard gates before financial timers.
    depends_on:
      - S1 Keychain and quality failure
      - S2 launchd assets
slice_checkpoints:
  - slice: S0 approved design input
    diff_fingerprint: 6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
    validation: Four usable panel results and planreview pass-with-risks recorded in design evidence.
    done: true
  - slice: S1 Keychain and quality failure
    diff_fingerprint: ba085aa5689c7f470fd5107298eaf81dba81b1e3dccf6d50b2cec23632bd660b
    validation: Keychain 10, existing config 39, and HTTP 19 tests passed with Python 3.12; Ruff focused checks passed.
    done: true
  - slice: S2 launchd assets
    diff_fingerprint: 525df371590d60dfec73d282a227b374e6ccb3aee1648a9bf73419018631492b
    validation: Installer, strict venv, preflight ordering, plist and native lock tests passed; no LaunchAgent was loaded.
    done: true
  - slice: S3 Futu acceptance
    diff_fingerprint: 8d7651c15c2082045f5814a87f7856b49cb498a88bd4a13e539cb2ed79a670bc
    validation: Same-interpreter SDK, lx/sy mapping and OpenD readiness fake tests passed; complete suite 1550 passed, compileall, Ruff and diff check passed.
    done: true
user_confirmation:
  - 'User: 用 [$devflow] 单独设计 launchd 流程及 Mac 上的凭据交付。以及Futu SDK 的导入检查。'
  - 'User: 全部按你的推荐 [完整设计路径、覆盖现有 Linux 任务、Keychain]'
prd_doc: not-applicable
prd_doc_ref: not-applicable
design_doc: docs/deploy-macos.md
design_ref: docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
implementation_workspace: /Users/liuxie/.codex/worktrees/pm-macos-launchd-design/portfolio-management
review_base: 9ddabcf175b0a2229262d5432fdcad2f2349e1cb
authorization_diffs:
  - when: 2026-09-28
    what: User authorized the single Impl node for the frozen macOS design S1-S3; previous design-only non-goal applies to the earlier completed workflow, not this new node. No service activation, business write, commit, push, PR, release, or upgrade authorization.
    ref: 'User: impl'
workflow_version: 2
mode: node
workflow_path: null
node_sequence:
  - Impl
current_node: null
internal_step: null
status: completed
next_action: null
approved_scope_ref: user-request-macos-design-and-futu-import
path_approval_ref: user-all-recommendations
implementation_baseline:
  design_doc: docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
  implementation_workspace: /Users/liuxie/.codex/worktrees/pm-macos-launchd-design/portfolio-management
  review_base: 9ddabcf175b0a2229262d5432fdcad2f2349e1cb
  head: 9ddabcf175b0a2229262d5432fdcad2f2349e1cb
  git_status: ' M .devflow/design-panel.md;  M .devflow/scope.md;  M docs/INDEX.md; ?? docs/deploy-macos.md'
  staged: []
  unstaged:
    - {path: .devflow/design-panel.md, hash: 23af0b5e51441f3b988c872ca1ad974f5710398fea9447882e88a63e852ed8eb, size: 3364}
    - {path: .devflow/scope.md, hash: 56de003970c5beb9294dae443135483caad3268127cbcd0a89f89c75620ce08a, size: 2875}
    - {path: docs/INDEX.md, hash: 5ccba0c90e89992bb2a80f8885fcac8d2bd01fb470109e292d4ec5f5f2fbda9a, size: 4122}
  untracked:
    - {path: docs/deploy-macos.md, hash: 6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d, size: 20721}
  ignored_design_artifacts:
    - {path: docs/reviews/plan-review-20260928-132847.md, hash: 17d340d73b40689f90174ac4c12087b3c8231563a172a7b4e54dcf0db72284b5, size: 3743}
    - {path: docs/reviews/plan-review-20260928-133111.md, hash: 9f261ecd73a9b188c162641c183c0521f8469d6cbfb8eae8ba575d122f0fa196, size: 2788}
inventory:
  - {path: .devflow/design-panel.md, status: unstaged, hash: 23af0b5e51441f3b988c872ca1ad974f5710398fea9447882e88a63e852ed8eb, size: 3364, type: file, mode: '0644', classification: preexisting-design, evidence_ref: implementation_baseline}
  - {path: .devflow/scope.md, status: unstaged, hash: self-referential, size: null, type: file, mode: '0644', classification: workflow-record, evidence_ref: final-scope-yaml-validation}
  - {path: docs/INDEX.md, status: unstaged, hash: 9b9ebe701d9acf9ce15f25b8e7c5be6fa79104a9759e8a9a48b28437f14568fa, size: 4175, type: file, mode: '0644', classification: planned, evidence_ref: design-and-operations-index}
  - {path: pm, status: unstaged, hash: 0d648a40d0801b117228b288d3b1b4b1a2dcb77a16da466b80c9e9a077f3fbae, size: 462, type: file, mode: '0755', classification: planned, evidence_ref: test_strict_pm_launcher_rejects_missing_venv}
  - {path: scripts/pm.py, status: unstaged, hash: 66b72ee8f1f9eac3b2a25ff3665a3930a546e0ae14a15beb9f1335ba6d632f8d, size: 94228, type: file, mode: '0755', classification: required-correctness/safety, evidence_ref: test_macos_preflight.py}
  - {path: src/config.py, status: unstaged, hash: 6b7f071681f2034f908ff6986032f6fbfe95f0f3f057e8f2be18d7e99bd084c0, size: 35288, type: file, mode: '0644', classification: planned, evidence_ref: test_keychain_credentials.py}
  - {path: src/configuration/feishu_credentials.py, status: unstaged, hash: d6de0a21438b6b7a4d6236d4f2e353fb2352b4fc545fb3bbf2fd0d968ec6ff5a, size: 6108, type: file, mode: '0644', classification: planned, evidence_ref: test_keychain_credentials.py}
  - {path: src/service/http.py, status: unstaged, hash: 6c21e512b7a64e57c81e5b282182e8876fb7612d8ad2907eaebfd843af7780f8, size: 26736, type: file, mode: '0644', classification: planned, evidence_ref: test_service_http.py}
  - {path: tests/test_service_http.py, status: unstaged, hash: 0f0e9a6e510b9e3bc8682004ecabdb645894c5252bd5b3dca8508f1fc8172524, size: 24904, type: file, mode: '0644', classification: planned, evidence_ref: full-pytest-1549}
  - {path: docs/deploy-macos-operations.md, status: untracked, hash: 724b651d6f6d05acaf9093bf92c85c7934245c7e272647d8604457dea9a8c88f, size: 5593, type: file, mode: '0644', classification: planned, evidence_ref: operational-runbook}
  - {path: docs/deploy-macos.md, status: untracked, hash: 6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d, size: 20721, type: file, mode: '0644', classification: preexisting-design, evidence_ref: design_ref}
  - {path: scripts/install_macos.py, status: untracked, hash: 984ea861b180e74daaf4c24f2444420c49fbdae8969159f00e5058ebdbf8d7c4, size: 10185, type: file, mode: '0644', classification: planned, evidence_ref: test_install_macos.py}
  - {path: scripts/macos_preflight.sh, status: untracked, hash: 4db7f6cf5c44c4adfd791bd1e89d43dfb4be596704de185f9ed7cd13abfdd3d2, size: 175, type: file, mode: '0644', classification: planned, evidence_ref: test_macos_preflight.py}
  - {path: tests/test_install_macos.py, status: untracked, hash: 74afaa56af59d9fbf9538dc4f608cf3d54e29abb8f155bf900445636ae66d1a8, size: 6503, type: file, mode: '0644', classification: planned, evidence_ref: full-pytest-1550}
  - {path: tests/test_keychain_credentials.py, status: untracked, hash: 9f504ce35217cb8f943d4fd932068455ce8d95df5561ee0916fd539c08682b85, size: 5101, type: file, mode: '0644', classification: planned, evidence_ref: full-pytest-1550}
  - {path: tests/test_macos_preflight.py, status: untracked, hash: fd9f2a80256465af51932c5d61b6fa6e67e989bad3bf44eac981fffdab8fcfc7, size: 4821, type: file, mode: '0644', classification: planned, evidence_ref: full-pytest-1550}
implementation_variations:
  - The approved quality-only preflight could not use config doctor because doctor also requires daily Feishu keys; config quality-preflight checks only the quality token and scope.
  - A tenth manual futu-preflight LaunchAgent runs SDK, lx/sy, and OpenD gates in the actual launchd interpreter and context.
  - Operational commands live in docs/deploy-macos-operations.md so the frozen design document and design_ref remain unchanged.
  - The installer accepts an owned venv Python symlink because Python 3.12 virtual environments normally use one.
content_revision: 6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d
planreview_round: 2
deepreview_round: 0
in_flight: []
evidence_paths:
  - docs/deploy-macos.md
  - docs/deploy-macos-operations.md
  - .devflow/design-panel.md
  - tests/test_install_macos.py
  - tests/test_keychain_credentials.py
  - tests/test_macos_preflight.py
  - docs/reviews/plan-review-20260928-132847.md
  - docs/reviews/plan-review-20260928-133111.md
blocking_findings: []
residual_risks:
  - item: User-session Keychain and LaunchAgent do not equal Linux per-service secret isolation or always-on scheduling.
    classification: assigned-to-later-work-unit
    owner: macOS deployment operator
    destination: docs/deploy-macos.md accepted limits and activation decision
  - item: No stable Mac runtime venv, Keychain items, or launchd-context preflight were prepared or activated by this Impl node.
    classification: assigned-to-later-work-unit
    owner: macOS deployment operator
    destination: docs/deploy-macos-operations.md preparation and activation checks
