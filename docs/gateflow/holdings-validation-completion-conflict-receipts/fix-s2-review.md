# Gateflow Fix Artifact — S2 DeepReview

- Gate: `fix`
- Work unit: `holdings-validation-completion-conflict-receipts`
- Slice: `S2`
- Review artifact: `docs/reviews/code-review-20260731-213427.md`
- Status: `fix complete; pending re-review`

## Finding decisions and fixes

### DR-S2-01 — accepted — fixed

`recover` now accepts only `applying`, `failed_retryable`, or
`apply_outcome_unknown` cases with a durable apply attempt and target. It
rechecks the state and attempt after acquiring account then record locks.
Pending apply, confirmation, and manual-edit cases cannot use recovery to
bypass their intended confirmation paths.

### DR-S2-02 — accepted — fixed

Re-observing the exact semantic problem after `resolved_accept`,
`resolved_external`, or `superseded` now reopens the deterministic case into
its current pending state, clears obsolete apply working fields, and appends a
`reopened` event. The original deterministic discovery receipt is not resent.
An unchanged `resolved_keep` remains effective, while a changed semantic scope
supersedes the old keep-current decision and creates the new pending case.

### DR-S2-03 — accepted — fixed

Cases now durably store canonical `asset_id`/`account`/`broker` identity.
Recovery compares that frozen identity before absolute field classification;
identity drift is `superseded`, even if the observed target field happens to
equal the old target.

### DR-S2-04 — accepted — fixed

`OperationStateStore` now reads and rejects an unsupported newer holdings
feature marker before running any v1 workflow DDL or migrations. A regression
test compares `sqlite_master` before and after the rejected startup.

## Verification

- Focused S2/S1 regression suite: `126 passed`.
- Ruff on all touched Python files: passed.
- Python compilation: passed.
- `git diff --check`: passed.
