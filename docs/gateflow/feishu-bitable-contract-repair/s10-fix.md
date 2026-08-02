# Gateflow S10 Fix Artifact — Snapshot Exact Set and Durable Recovery

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S10
- Review artifact: `docs/reviews/code-review-20260802-081536.md`
- Base: `dc807bc`
- Recorded at: 2026-08-02T08:36:36+08:00
- Status: all accepted findings fixed; pending aggregate re-review

## Finding Decisions and Fixes

### DR-S10-01 — accepted — fixed

Dry-run `snapshot_status=planned` is now classified as a successful preview.
The public NAV result carries `status=dry_run`, snapshot status/persistence,
plan digest, and mutation preview. It does not invoke the real-write failure
classifier and does not create recovery failure semantics.

### DR-S10-02 — accepted — fixed

CLOSED now uses the same snapshot outcome classifier as normal recording.
Incomplete exact-set completion returns `success=false`, `status=partial`, the
snapshot error, durable task id, and retry command instead of an unconditional
success message.

### DR-S10-03 — accepted — fixed

The account lock owner moved from the persistence-only helper to the public
normal and CLOSED write entrypoints. History loading, last-NAV selection,
history-dependent calculation, NAV persistence, snapshot exact-set mutation,
and completion readback now share one account critical section without nested
account locks. A deterministic concurrent-writer regression proves the second
writer observes the first writer's NAV.

### DR-S10-04 — accepted — fixed

The typed Feishu registry now declares all exact-set-owned optional snapshot
fields (`asset_name`, `avg_cost`, `source`, and `remark`) clearable. The real
write validator is exercised for every value-to-null transition.

### DR-S10-05 — accepted — fixed

Snapshot recovery now owns a typed NAV transition contract. The durable target
contains a digest of non-snapshot base details and independent digests of the
prepared and complete details. Parsing validates scope, bound authority,
evidence, task identity, planned/complete status semantics, row/plan digests,
and identical non-snapshot bases.

Both the original path and compensation classify fresh NAV details as exactly
`incomplete` or `complete` before snapshot mutation and again before the
completion patch. Any finality, cash-flow, valuation-quality, or other
non-snapshot base drift becomes a state conflict; compensation performs zero
snapshot and NAV mutation. The original path also avoids snapshot mutation
when drift is already visible before exact-set application. A transient final
readback miss that is immediately followed by an exact complete fresh read is
resolved rather than leaving a false FAILED task.

### DR-S10-06 — accepted — fixed

Before planning or remote write preview, the state machine requires the
top-level snapshot authority's overwrite flag, run id, and issuer to equal the
effective NAV write intent and finality context. Mismatch in either overwrite
direction, run id, or issuer fails with zero snapshot read/write and zero NAV
write.

## Bounded Scope Expansion

- Production: `src/feishu/contracts/registry.py` was added solely to fix
  DR-S10-04 at the unique typed field-contract owner.
- Tests: the previously recorded fixture-only additions remain limited to
  `tests/test_holdings_nav_preflight_service.py`, `tests/test_portfolio.py`, and
  `tests/test_service_application.py`.
- The unrelated untracked
  `docs/reviews/code-review-20260801-084655.md` remains excluded and untouched.

## Validation

- Focused snapshot/NAV/compensation suite: `97 passed`.
- Exact S10 suite from the accepted plan: `133 passed`.
- Full repository suite: `1355 passed`.
- Scoped Ruff: passed.
- Python compileall: passed.
- `git diff --check`: passed.
- No live Feishu/Futu reads or writes, live schema mutation, historical
  rewrite, merge, release, or deployment occurred.

## Next Gate

Run aggregate DeepReview re-review over the complete uncommitted S10 diff from
`dc807bc`, including this fix artifact. S10 may be locally committed only after
the re-review reports no findings.
