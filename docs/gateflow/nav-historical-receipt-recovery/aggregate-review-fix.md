# Gateflow Aggregate Review Fix — NAV Historical Receipt Recovery

- Gate: `aggregate deepreview / fix / re-review`
- Work unit: `nav-historical-receipt-recovery`
- Review artifact: `docs/reviews/code-review-20260814-214011.md`
- Status: `pass`
- Current gate: `accepted deepreview commit`
- Next entry point: create accepted deepreview commit, then draft PR chain

## Finding decision

### DR-AGG-01 — accepted — 已修复

Every reconstructed receipt row now must have `account` equal to the requested
recovery account after identity self-consistency and before record digest/current
validation. A cross-account row therefore cannot be accepted even if its own
identity and serialized digest are internally consistent.

## Re-review and validation

- Added a regression that changes both row field and identity to `sy` while the
  recovery scope remains `lx`; it fails with `receipt holdings account mismatch`
  before digest authorization.
- Focused evidence suite: 14 passed after the fix.
- Full repository suite: 1477 passed after the fix.
- Static compile, branch/worktree diff checks, and whitespace checks: passed.

## Residual risks

- Real legacy payload/provider preview remains the fail-closed rollout gate.
- Existing historical provider availability, multi-host coordination, and local
  evidence backup owners are unchanged.

No residual risk is unclassified.
