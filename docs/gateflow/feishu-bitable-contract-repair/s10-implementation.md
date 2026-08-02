# Gateflow S10 Implementation — Snapshot Exact Set and Durable Recovery

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S10
- Base: `dc807bc`
- Recorded at: 2026-08-02T08:06:43+08:00
- Status: implementation complete; pending DeepReview
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s10-implementation.md`

## Scope

The production implementation follows the accepted S10 allowlist. Three
bounded test-fixture corrections extend the test-only scope to
`tests/test_holdings_nav_preflight_service.py`, `tests/test_portfolio.py`, and
`tests/test_service_application.py`. The first now returns a real
ValuationService-owned normalized valuation with complete prices; pure NAV
calculation tests explicitly use `persist=False`; application wiring fixtures
now carry a valuation-consistent `NormalizedValuationSnapshot` and assert that
confirmed snapshot authority is passed to the portfolio boundary. Production
write guards were not weakened. The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` remains excluded and untouched.

## Implemented Contract

- `SnapshotExactSetPlan` is an immutable, versioned before/target contract for
  exactly one account and date. It validates scope, canonical dedup keys,
  unique business keys and record ids, full row replay digests, target digest,
  and a deterministic plan digest.
- A fresh complete account/date read owns the before set. Duplicate remote
  business keys fail closed before mutation. Residual replay accepts only the
  bound before state, the desired state, or a safe partial progression between
  them; unbound keys, changed record ids, and third states are conflicts.
- The deterministic residual action set creates missing target rows, updates
  changed rows while preserving explicit null clears, and deletes obsolete
  rows only after creates and updates. A+B to A therefore removes B rather than
  leaving a stale upsert residue.
- `SnapshotWriteAuthority` represents top-level intent and is bound only after
  the fresh plan exists. Account, date, run id, issuer, target digest,
  overwrite scope, confirmation, and plan digest are covered by immutable
  authority digests. Existing slices require `overwrite_existing=True`; every
  real write requires a confirmed authority.
- Account record and NAV initialization create that authority only after their
  confirmation checks and pass it through `PortfolioManager` to
  `NavRecordService`. Dry-run persistence receives an unconfirmed preview
  authority and performs no remote mutation.
- The original NAV plus snapshot operation is serialized under the same
  account lock used by compensation retry. Before the first remote mutation it
  performs a read-only NAV preview, binds the plan, and fsyncs a local
  `HOLDINGS_SNAPSHOT_TARGET_SET` event containing the complete bound authority,
  before/target sets, planned and complete NAV details, and all scope/digests.
  Prepared transient intent is deliberately not mirrored to Feishu.
- A NAV exception is classified by a fresh account/date read. An absent row
  resolves the prepared task as no-replay and re-raises; an exact prepared
  binding proves the remote NAV succeeded and continues snapshot recovery; any
  other row remains pending with outcome unknown and raises
  `PartialWriteError`.
- After NAV confirmation, the exact-set engine fresh-reads current rows,
  applies remaining create/update/clear/delete actions, fresh-reads again, and
  verifies exact target equality plus the v2 row digest. Only then are complete
  snapshot details patched onto NAV and fresh-read back before the durable task
  resolves.
- Any snapshot action, deletion, NAV-details patch, or final readback failure
  leaves a durable failed recovery task and returns NAV with explicit partial
  snapshot evidence. It cannot claim complete from a successful transport call
  alone.
- Compensation parses and revalidates the exact durable plan and bound
  authority, fresh-reads NAV binding, invokes the same residual exact-set
  engine, patches completion details, and requires a final fresh NAV readback.
  Tampered scope/digests produce a state conflict and zero target mutation.
- CLOSED now enters the same state machine with the typed closed normalized
  valuation and an exact empty holdings-snapshot target. Existing NAV or
  snapshot rows require the same confirmed overwrite scope, and completion
  proves both the CLOSED NAV details and the empty remote snapshot set.

## Validation

- Exact S10 suite: `116 passed`.
- Full repository suite: `1338 passed`.
- Scoped Ruff, Python compileall, and `git diff --check`: passed.
- Regression coverage includes A+B to A deletion, optional value-to-null
  clearing, duplicate remote-key rejection, empty-target overwrite guards,
  unconfirmed zero mutation, prepared binding tamper rejection, fsync-before-
  write evidence, NAV timeout with confirmed remote success, absent and unknown
  NAV outcomes, stale delete/readback failure, exact compensation readback,
  CLOSED empty-set semantics, and account-lock coverage through remote actions.
- No live Feishu/Futu read or write, schema mutation, historical rewrite,
  business-data repair, merge, release, or deployment occurred.

## Residual Boundaries

- Same-host account locking coordinates source writers and compensation on this
  host, but Feishu has no remote transaction or compare-and-swap covering NAV
  and snapshot tables. External editors can still race after a fresh read;
  bound-state checks and final readback detect observed divergence and leave a
  recoverable conflict rather than claiming atomicity.
- S10 does not rewrite historical legacy snapshot evidence. Legacy final rows
  remain legacy unless a separately authorized canonical rewrite is performed.
- Live destructive exact-set canaries and physical Feishu null/missing behavior
  remain outside this source implementation gate.

## Next Gate

Run DeepReview over the complete uncommitted S10 diff from `dc807bc`, including
this artifact and the bounded test-fixture scope correction. Fix every accepted
finding and obtain a no-findings re-review before the scoped local S10 commit.
