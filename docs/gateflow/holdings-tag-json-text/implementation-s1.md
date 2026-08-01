# Gateflow Implementation Artifact — S1

- Gate: `implementation`
- Work unit: `holdings-tag-json-text`
- Slice: `S1 — Normalize Holdings tag JSON text end to end`
- Base checkpoint: `253a830`
- Artifact path:
  `docs/gateflow/holdings-tag-json-text/implementation-s1.md`
- Status: `code re-review pass; pending accepted slice commit`

## Objective and outcome

Align the documented Holdings `text/json` transport with standalone validation
and frozen typed snapshots without changing record digests, workflow identity,
schema, repository behavior, or NAV blocking rules.

Outcome:

- `_optional_tag()` now parses string values with strict `json.loads()`.
- Only arrays containing strings are accepted.
- Empty native/text arrays normalize to `optional_missing` with `current=[]`.
- Nonempty native/text arrays normalize to `valid` with a list-valued current.
- Malformed JSON, non-list JSON, and non-string members retain the raw value in
  a nonblocking `TAG_INVALID` outcome.
- `RecordValidation.to_holding()` now reads the normalized field outcome rather
  than the raw transport value, preventing JSON text from becoming characters.
- `canonical_record_payload()` was not changed.

## Changed files

- `src/app/holdings_validation.py`
- `tests/test_holdings_validation.py`
- `tests/test_holdings_nav_preflight_service.py`
- `docs/gateflow/holdings-tag-json-text/implementation-s1.md`

The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` remains untouched.

## Tests added

- Native and JSON-text empty tags share `optional_missing` semantics.
- Native and JSON-text nonempty tags share normalized outcomes and typed
  `Holding.tag` values.
- Seven malformed/non-list/non-string representations remain nonblocking
  invalid values with raw evidence preserved.
- Canonical digest input preserves the existing missing/blank `None` versus
  native/text empty-array `[]` distinction.
- A production-shaped preflight row with `tag="[]"` creates no case or warning
  and freezes an empty tag tuple.

## Validation

Red phase before production edit:

```text
3 failed, 79 passed
```

The failures were exactly the text-empty validator, text-nonempty typed
validator, and preflight warning regressions.

Green focused gate after implementation:

```text
168 passed in 1.05s
```

Command covered:

- `tests/test_holdings_validation.py`
- `tests/test_holdings_nav_preflight_service.py`
- `tests/test_feishu_storage.py`
- `tests/test_holdings_preload_minimal.py`

`git diff --check` passed.

## Docs decision

No schema/business documentation change. `docs/schema.md` already defines the
correct `text/json` representation. Gateflow artifacts record the repair.

## Residual risks and uncovered areas

- Full-suite and compile validation: `covered by the approved post-review
  validation gate`.
- Durable cases already created in production: `assigned to the existing
  formal-preflight lifecycle`.
- Production warning removal: `assigned to a separately authorized release and
  upgrade`.
- Live validation against modified code: `not possible without deployment and
  not required for this local slice`.

All residual risks are classified; none blocks code review.

## Completion state

- Current gate: `code re-review pass`.
- Next gate: `accepted slice commit`.
