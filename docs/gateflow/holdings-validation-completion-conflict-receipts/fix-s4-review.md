# Gateflow Fix Artifact — S4 DeepReview

- Gate: `fix`
- Work unit: `holdings-validation-completion-conflict-receipts`
- Slice: `S4`
- Review artifact: `docs/reviews/code-review-20260731-225000.md`
- Status: `fix complete; re-review passed`

## Finding decisions and fixes

### DR-S4-01 — accepted — fixed

Formal preflight now exercises the workflow state contract even when there are
no current cases. Account fresh scans close absent cases only with complete
evidence. A repaired synthetic global orphan case transitions to
`resolved_external` with a deterministic closure receipt. Dry-run remains
read-only, and any formal state failure blocks NAV.

### DR-S4-02 — accepted — fixed

During a Futu outage only, durable `resolved_keep` decisions may receive manual
precedence after the current raw row independently reproduces the stored
identity, canonical current value, authority inputs, policy, precondition, and
confirmation scope. The override is per record and field. Fresh semantic Futu
evidence, scope drift, and unrelated unconfirmed blockers are never suppressed.

## Verification

- S4-related regression suite: `198 passed`.
- Ruff on all S4-touched Python files: passed.
- Python compilation: passed.
- `git diff --check`: passed.
- Re-review: `docs/reviews/code-review-20260731-225629.md` passed.
