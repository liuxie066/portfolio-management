# Gateflow Final Closeout — NAV Historical Receipt Recovery

- Work unit: `nav-historical-receipt-recovery`
- PR: https://github.com/liuxie066/portfolio-management/pull/48
- Status: `final closeout pass`
- Completed gate: `draft-PR-pass -> final closeout`
- Next entry point: use the user's explicit authorization to mark ready and merge
  PR #48, then perform the separately authorized release/remote upgrade and
  `lx/2026-08-13` replay.

## What changed

- Historical preparation can reconstruct raw Holdings facts from the exact
  durable NAV receipt when current Holdings has legitimately drifted.
- Every record and aggregate raw digest is recomputed; current pure Holdings
  validation and exact normalized digest binding remain mandatory.
- Immutable evidence records the source receipt. Only this preparation type may
  differ from current Holdings, while fresh Holdings health and cash-flow
  fingerprint/gate checks remain mandatory.
- Canonical replay writes the historical target-date snapshot and audits both
  historical/current digests without changing live Holdings.

## Verification

- Focused implementation suite: 135 passed before review; 14 focused evidence
  tests passed after aggregate fix.
- Full repository suite after final fix: 1477 passed.
- Compile, whitespace, worktree, and branch diff checks: passed.
- GitHub quality contract: passed.

## Documentation

- Updated `docs/nav-valuation-evidence-replay.md`; no new CLI or schema.

## Finding status

- Plan DR-PLAN-01: fixed and re-reviewed.
- Aggregate DR-AGG-01: fixed and re-reviewed.
- Slice and PR reviews: no findings.

## Remaining risks and owners

- Real legacy receipt/provider compatibility: owned by the authorized read-only
  production preview; fail closed before artifact or NAV writes.
- Historical provider availability: existing operator-time dependency.
- Multi-host writer coordination and local evidence backup: existing owners and
  boundaries, not expanded.

No residual risk is unclassified. Gateflow work unit is complete.
