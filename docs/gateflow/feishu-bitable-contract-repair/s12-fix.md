# Gateflow S12 Fix — DeepReview Findings

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S12
- Base: `b33ff48`
- Review artifact: `docs/reviews/code-review-20260802-092405.md`
- Recorded at: 2026-08-02T09:35:08+08:00
- Status: accepted findings fixed; pending re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s12-fix.md`

## Finding Decisions

### DR-S12-01 — accepted — fixed

`error` had been made `schema_required=False` while removing it from create
row-required fields. Those are independent contracts. The false structural
optionality would let strict live-schema comparison report `ok=True` for a
configured mirror table that could not accept PENDING/FAILED error details.

The registry now keeps `error` structurally required, marks it clearable, and
omits it only from create `required_fields`. The generated schema reports
`required / clearable=yes` plus a create contract that does not require the
row value. A regression constructs an otherwise exact live schema without
`error` and proves the comparator reports `missing_required=["error"]` and
`ok=False`; the existing RESOLVED update assertion proves `error=None` remains
legal.

Final status: `已修复`.

### DR-S12-02 — accepted — fixed

The service and registry separately hardcoded compensation statuses, while the
public status method accepted any known status from any current state. An
already mirror-eligible PENDING task could therefore append PREPARED locally,
then fail Feishu select validation because PREPARED is deliberately not a
mirror status.

Added `src/domain/compensation_contracts.py` as the lifecycle semantic owner.
It defines the complete local status set, mirrorable projection, activation
set, and legal transition matrix. The service validates current-to-next before
append; the registry derives SingleSelect options from the same projection.
PREPARED can only remain PREPARED or advance, actionable states cannot regress
to PREPARED, RUNNING/FAILED transitions are constrained to recovery paths, and
RESOLVED is absorbing. A regression proves PENDING->PREPARED raises before any
new event or mirror call, while a registry assertion proves select options are
the domain projection.

This is a bounded production-scope correction to the accepted S12 plan. It is
necessary to satisfy the work unit's unique-truth success signal without
making the local authority depend on the Feishu adapter.

Final status: `已修复`.

## Validation

- Exact S12 suite: `118 passed`.
- Full repository suite: `1369 passed`.
- Schema generator `--check`: passed.
- Ruff passed for the domain contract, compensation service, registry, and
  focused test. The legacy composition/storage test files passed with only
  their pre-existing F401/E712/F811/F841 baseline rules excluded.
- Python compileall and `git diff --check`: passed.
- No live Feishu/Futu request, schema mutation, business-data mutation, push,
  merge, release, or deployment occurred.

## Re-review Scope

Re-review the complete S12 diff from `b33ff48`, with emphasis on:

- domain lifecycle ownership and all legal production transitions;
- append-before-mirror ordering after transition validation;
- schema-required versus create-row-required error semantics;
- generated schema projection and tests proving both findings;
- optional mirror failure remaining non-authoritative.
