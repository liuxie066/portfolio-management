# Gateflow Fix Artifact — Plan Review

- Gate: `plan review fix`
- Work unit: `holdings-tag-json-text`
- Finding source: `docs/reviews/plan-review-20260801-132452.md`
- Artifact path:
  `docs/gateflow/holdings-tag-json-text/plan-review-fix.md`
- Status: `fix complete; pending plan re-review`

## Finding decision and fix

### PR-01 — accepted — fixed

The original plan would have reused an empty-list normalizer in
`canonical_record_payload()`. That could change missing `tag=None` from
canonical `null` to `[]`, altering `record_digest` and durable workflow case
identity for records unrelated to the reported warning.

The plan is narrowed as follows:

- Leave `canonical_record_payload()` unchanged. It already makes native and
  JSON-text arrays digest-equivalent while preserving the existing distinction
  between missing and empty-array facts.
- Decode and validate JSON text only in `_optional_tag()`.
- Carry the normalized list through `FieldOutcome.current` into
  `RecordValidation.to_holding()`.
- Add a canonical-output regression proving `None`/blank remain `None` and
  native/text empty arrays remain `[]`.

Final status: `已修复`.

## Validation

- Plan text no longer authorizes changes to digest canonicalization.
- S1 allowed behavior is limited to validation status and typed
  materialization.
- The test plan now guards existing case identity for missing tags.

## Residual risks

- None introduced by this plan fix.

## Completion state

- Current gate: `plan review fix pass`.
- Next gate: `plan re-review`.
