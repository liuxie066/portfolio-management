# Gateflow Implementation Plan — Holdings Tag JSON Text Normalization

- Work unit: `holdings-tag-json-text`
- Base: `origin/main@63cce99`
- Branch: `fix/holdings-tag-json-text`
- Design source: confirmed user request, live read-only evidence, and current
  Holdings schema/writer/validator contracts
- Current gate: `final closeout`
- Next entry point: `user review / separate merge authorization`

## Goal and motivation

Make the canonical Daily NAV Holdings preflight accept the documented Feishu
`text/json` representation of `tag` without weakening malformed-data detection.
The current writer emits JSON text, while the validator only accepts native
lists, so valid empty tags generate user-visible attention warnings.

## Success signals

- `tag=[]` and `tag="[]"` both yield `TAG_OPTIONAL_MISSING` with no case.
- `tag=["core"]` and `tag='["core"]'` both yield `TAG_VALID`, the same record
  digest, and typed `Holding.tag == ["core"]`.
- Malformed JSON, JSON objects/scalars/null, and arrays containing non-strings
  remain nonblocking `TAG_INVALID`.
- Account preflight with `tag="[]"` returns `status=valid`, no warnings, and a
  frozen row whose tag is empty.
- Focused and full validation pass without modifying production or unrelated
  worktree files.

## Non-goals and scope boundary

- No Feishu schema/data migration, serializer change, repository/cache change,
  workflow-state redesign, NAV calculation change, release, deployment, or
  production write.
- Do not silently coerce dicts, numbers, booleans, `null`, mixed arrays, or
  malformed text into empty tags.
- Do not add label vocabularies, deduplication, whitespace policy, or a general
  JSON codec that is unsupported by the reported bug.

## First-principles judgment and direct code evidence

- The source-of-truth schema says `tag` is JSON serialized into a text field.
- `_to_feishu_fields()` serializes list values to JSON text, while the raw
  preflight intentionally preserves transport values.
- The repository's typed read already accepts JSON text, proving this is an
  established compatibility contract rather than a new input format.
- `canonical_record_payload()` independently parses JSON text for stable case
  identity, but `_optional_tag()` does not; this split causes the warning.
- `to_holding()` currently consumes raw fields, so status-only acceptance would
  corrupt a nonempty text list into characters.

The smallest coherent repair is strict JSON-text decoding inside the existing
field validator, with typed materialization using the normalized outcome. The
already-correct digest canonicalization remains untouched, so the change does
not alter external schema, workflow identity, or state-machine behavior.

## Contract and state-machine decisions

### Strict tag normalization

Update `_optional_tag()` to classify a local normalized candidate:

- `None`, `""`, and native `[]` normalize to a valid empty list;
- a string is parsed with `json.loads()` and is valid only when the result is a
  list containing only strings;
- a native list is valid only when every item is a string;
- every other value is invalid and retains its raw value in the invalid
  `FieldOutcome` for evidence.

Whitespace-only strings remain invalid, preserving the existing strict
behavior. Empty valid lists produce `optional_missing`; nonempty valid lists
produce `valid` with the normalized list in `FieldOutcome.current`.

### Digest and typed-holding consistency

- `canonical_record_payload()` remains unchanged. It already makes native and
  JSON-text lists equivalent while preserving the existing `None` versus `[]`
  distinction used by workflow identities.
- `RecordValidation.to_holding()` obtains `tag` from normalized
  `outcome_by_field["tag"].current` when valid and otherwise uses `[]`.
- `TAG_INVALID` remains nonblocking and continues to generate the existing
  workflow warning. No case states or reason codes change.

## Affected files and modules

- `src/app/holdings_validation.py`
  - strict JSON-text decoding in `_optional_tag()`;
  - normalized typed-holding construction.
- `tests/test_holdings_validation.py`
  - native/text equivalence, typed materialization, and invalid-shape matrix.
- `tests/test_holdings_nav_preflight_service.py`
  - production-shaped `"[]"` preflight regression proving no warning/case.
- `docs/gateflow/holdings-tag-json-text/`
  - required Gateflow artifacts.
- `docs/reviews/`
  - PlanReview and DeepReview artifacts.

## Implementation slice

### S1 — Normalize Holdings tag JSON text end to end

- Objective: align documented transport, validation, workflow, and typed
  snapshot behavior in one atomic slice.
- Allowed production file: `src/app/holdings_validation.py`.
- Allowed tests: `tests/test_holdings_validation.py` and
  `tests/test_holdings_nav_preflight_service.py`.
- Allowed non-product files: this work unit's Gateflow artifacts and generated
  review artifacts only.
- Prerequisites: accepted plan; unrelated untracked review artifact remains
  untouched.
- Exact changes:
  1. Update `_optional_tag()` to decode JSON text strictly and classify
     normalized empty/nonempty lists while retaining invalid raw values.
  2. Update `to_holding()` to consume normalized `FieldOutcome.current`.
  3. Add validator tests for native/text empty lists, native/text nonempty
     lists, typed tags, digest equivalence, malformed JSON, non-list JSON, and
     non-string array members.
  4. Add a canonical-output regression proving missing/blank tags remain
     canonical `None` while native/text empty arrays remain `[]`.
  5. Add one account-preflight regression with raw `tag="[]"`, asserting
     success, no warnings/cases, and an empty frozen tag tuple.
- Invariants:
  - raw source rows remain unchanged and auditable;
  - valid equivalent encodings keep identical record digests;
  - invalid values never become valid empty lists;
  - tag remains optional and nonblocking;
  - no production/business writes occur.
- Stop condition: any test demonstrates a required representation cannot be
  normalized without changing schema, case identity, or another module owner.
- Completion signal: focused tests and full validation pass, and review finds
  no unresolved accepted finding.

## Tests and validation

Focused behavioral gate:

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_holdings_tag_json_text python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_holdings_validation.py \
  tests/test_holdings_nav_preflight_service.py \
  tests/test_feishu_storage.py \
  tests/test_holdings_preload_minimal.py
```

Expected assertions include all success signals above plus continued rejection
of `tag="not-json"` by strict repository materialization.

Repository-wide gates:

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_holdings_tag_json_text_full python3.12 -m pytest -q -p no:cacheprovider
PYTHONPYCACHEPREFIX=/tmp/pm_holdings_tag_json_text_compile python3.12 -m compileall -q src scripts
git diff --check
```

Before every checkpoint, inspect `git status`, stage only work-unit files, and
verify the unrelated untracked review artifact remains untracked.

## Docs decision

No business/schema documentation change. `docs/schema.md` already correctly
defines `tag` as `text/json`; changing it would hide rather than repair the
validator defect. Gateflow and review artifacts document the implementation.

## Risks and open questions

- Risk: accepting JSON text but materializing raw characters. Mitigation: typed
  holding test and use of normalized `FieldOutcome.current`.
- Risk: malformed JSON becomes empty through a permissive parser. Mitigation:
  strict valid/invalid result and invalid-shape matrix.
- Risk: digest identity changes for existing equivalent encodings. Mitigation:
  do not change `canonical_record_payload()`; assert missing/blank and
  native/text empty-array canonical output explicitly.
- Risk: stale durable cases persist until the next formal preflight. Owner:
  existing workflow lifecycle; no direct state mutation in this work unit.
- Open questions: none.

## Why this is not overdesigned

The repair changes two methods in one existing production module. It does not
add a codec framework, schema migration, new public type, or workflow branch. A
single slice is safer than temporarily accepting text without fixing typed
construction.

## Completion report format

- Behavior changed and exact files.
- Focused/full validation results.
- Review findings and final status.
- Docs decision.
- Remaining production rollout boundary and draft PR URL if authorized by the
  Gateflow PR gate.
