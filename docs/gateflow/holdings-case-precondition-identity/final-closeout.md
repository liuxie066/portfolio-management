# Gateflow Final Closeout

- Work unit: `holdings-case-precondition-identity`
- Branch: `codex/fix-holdings-case-precondition-identity`
- Draft PR: `https://github.com/liuxie066/portfolio-management/pull/41`
- PR review: `docs/reviews/pr-41-review-20260801-124909.md`
- Aggregate review: `docs/reviews/code-review-20260801-105404.md`
- Status: `draft-PR-pass; implementation work unit closed`
- Artifact path: `docs/gateflow/holdings-case-precondition-identity/final-closeout.md`

## Delivered Outcome

Holdings cases now separate stable user-visible fact identity from fresh-action
preconditions:

- `holdings-case.v1` semantic keys remain unchanged;
- new cases use field-specific `holdings-precondition.v2` tokens;
- only `currency` and `asset_class` retain raw `asset_type` as a cross-field
  dependency;
- eligible legacy rows migrate atomically with one audit event and no migration
  receipt;
- validated legacy `resolved_keep` decisions remain continuous through NAV
  dry-run, formal preflight, and bounded provider-outage handling;
- manual notify, event listener, and direct action paths share the same
  compatibility predicate and no-write/transaction boundaries.

The exact `sy / SPY` regression proves that correcting `asset_type` and
`asset_name` closes only the obsolete name case while unchanged timestamp cases
keep their keys, states, and receipt silence. Repeat processing is idempotent.

## Accepted Evidence

- Accepted plan commit: `ac4d47e`.
- Accepted implementation commit: `8cc4fa7`.
- Accepted aggregate DeepReview commit: `d0bf47a`.
- Accepted pre-sync PR review commit: `cb2204e`.
- Current-main synchronization and conflict-resolution commit: `cacaf82`.
- Focused holdings suites: 126 passed.
- Post-sync integration suites: 117 passed.
- Full repository baseline on current main: 1047 passed.
- Python compile and diff checks: passed.
- GitHub `quality-contract`: passed in 25 seconds on the synchronized source
  head `cacaf82b3ecafa7932c3d35c8a23a1885217c875`, run `30684632756`.
- Draft PR #41 is open, mergeable, and clean against
  `main@b36c908faff25ebc19b5d6586e75581d9c3947de`.
- Aggregate and PR-level DeepReview: pass with no findings or blocking open
  questions.

## Documentation Decision

No public schema, HTTP, CLI, or user documentation changed. The private digest
format, migration matrix, receipt behavior, and rollout constraints are
recorded in the committed plan, implementation, aggregate review, PR review,
and this closeout artifact.

## Finding Status

- Plan review findings: fixed and re-reviewed before implementation.
- Implementation review findings: none.
- Aggregate DeepReview findings: none.
- PR review findings: none.
- Current-main synchronization re-review findings: none.
- Deferred findings: none.

## Remaining Risks and Owners

- **Later authorized release/upgrade work unit:** once v2 state is persisted,
  the preceding binary cannot consume it. The rollout owner must use the
  suspended canary, paired operation-database backup, outbox hold, and
  forward-only gate from the accepted plan.
- **Same later rollout verification work unit:** inventory legacy cases and
  verify the exact SPY case/event/outbox delta before holdings materializers,
  NAV preflight, and listener processing resume.
- No live Feishu or production operation-database canary was run in this work
  unit.

## Scope and External-State Status

- No linked GitHub issue was supplied, so no issue link or closeout comment was
  created.
- Draft PR #41 remains draft; it was not approved, marked ready, or merged.
- No version metadata, tag, Release, production write, remote upgrade, or
  deployment was performed.
- Unrelated local `docs/reviews/code-review-20260801-084655.md` remains
  untracked and untouched.

## Next Entry Point

The implementation work unit is complete at `final closeout pass`. The next
separate authorization boundary is merging Draft PR #41. Release and remote
upgrade remain independent later boundaries after merge.
