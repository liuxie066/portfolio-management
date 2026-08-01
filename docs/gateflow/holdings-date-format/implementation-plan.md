# Gateflow Implementation Plan

- Gate: `plan`
- Work unit: `holdings-date-format`
- Branch: `fix/holdings-date-format`
- Base: `origin/main@43bc738d`
- Goal artifact: `docs/gateflow/holdings-date-format/goal-confirmation.md`
- Artifact path: `docs/gateflow/holdings-date-format/implementation-plan.md`
- Plan review: `docs/reviews/plan-review-20260801-085712.md`
- Status: `plan review pass`

## Goal and motivation

Restore `YYYY/MM/DD` as the canonical Feishu holdings date representation so
historical production rows do not block Futu synchronization, and make every
holdings writer and validator consume one shared contract. Preserve bounded
read compatibility for full timestamps already written by recent releases so
the correction does not turn the incident inside out.

## Success signals

- All holdings write payloads and holdings cache snapshots serialize non-null
  `created_at`/`updated_at` as `YYYY/MM/DD`.
- Repository materialization and standalone holdings validation accept the
  canonical slash form.
- They also accept only the known predecessor
  `YYYY-MM-DD HH:MM:SS` as a transition input.
- Typed holdings construction explicitly parses both accepted forms.
- Arbitrary date strings remain rejected or reported as invalid.
- A production-shaped Futu holdings preload succeeds without publishing
  partially validated cache state.
- Focused/full tests, compileall, diff checks, code reviews, aggregate review,
  and PR review pass.

## Non-goals and scope boundary

- Do not change the global `DATETIME_FORMAT` or any non-holdings table.
- Do not mutate production rows, run a real or dry-run Futu sync, create NAV,
  alter service state, release, or upgrade.
- Do not weaken required-field, finite-number, currency, enum, tag, duplicate,
  account-scope, or cache atomicity validation.
- Do not introduce automatic date guessing, locale parsing, timezone inference,
  a schema migration framework, or a second timestamp model.
- Do not stage or modify the unrelated untracked asset-class review artifact.

## First-principles judgment and direct evidence

The failure is a split transport contract:

- Feishu holdings dates historically use day precision and slash separators.
- Repository validation and all current holdings writers use the global
  second-precision datetime format.
- Standalone validation duplicates that full-format assumption.
- The date fields are optional metadata and are not authority for position,
  quantity, cost, currency, or official NAV valuation.
- Production contains both formats, so a safe correction requires one canonical
  write form plus one bounded predecessor read form.

The correction belongs in a holdings-domain date boundary used by the
repository and validator. Changing the global format would alter unrelated data
contracts; directly editing 17 rows would invent a migration authority and
leave the code split intact.

## Affected files and ownership

### Production

- `src/domain/holding_dates.py` (new)
  - Own the canonical format, predecessor compatibility format, strict parse,
    and canonical formatting helpers.
- `src/feishu/repositories/holdings_repository.py`
  - Use canonical formatting at every holdings cache and Feishu write boundary;
    use the shared parser for typed reads.
- `src/app/holdings_validation.py`
  - Use the shared parser for optional-field classification and typed holding
    construction.

### Tests

- `tests/test_holdings_preload_minimal.py`
  - Lock canonical single/replace/MMF payloads and repository parse behavior.
- `tests/test_holdings_bulk_upsert_minimal.py`
  - Lock canonical bulk update/create payloads and cache representation.
- `tests/test_holdings_validation.py`
  - Lock canonical, predecessor-compatible, missing, and malformed validation
    outcomes plus typed construction.
- Add or adjust the smallest relevant local-cache/NAV preflight assertion only
  if focused execution shows an uncovered representation boundary.

### Documentation and Gateflow artifacts

- `docs/schema.md`
- `docs/gateflow/holdings-date-format/`
- Timestamped plan/code/aggregate/PR review artifacts under `docs/reviews/`.

## Contract decisions

### 1. Canonical holdings date

Define one transport-specific constant:

```python
HOLDING_DATE_FORMAT = "%Y/%m/%d"
```

`format_holding_date(datetime_value)` always returns that form. It is used for:

- persistent holdings cache snapshots;
- in-memory holdings cache field snapshots;
- `patch_holding_record()` system `updated_at`;
- `upsert_holding()` update/create;
- `replace_holding()` update/create through `_holding_to_dict()`;
- `upsert_holdings_bulk()` update/create;
- `update_holding_quantity()`;
- `_holding_to_dict()` for any non-null timestamp.

No generic Feishu serializer or other repository changes.

### 2. Strict transition parser

`parse_holding_date(value, field_name=...)` has four outcomes:

1. `None` or empty string -> `None`;
2. exact `YYYY/MM/DD` -> parsed `datetime` at day precision;
3. exact predecessor `YYYY-MM-DD HH:MM:SS` -> parsed `datetime`, retained only
   for read compatibility;
4. anything else -> deterministic `ValueError("invalid <field>: <value>")`.

The parser is not permissive: no ISO `T`, hyphen date-only, non-zero-padded
components, locale guessing, or epoch values.

### 3. Repository validation and cache

`HoldingsRepository._strict_holding_timestamp()` delegates to the shared parser
or is removed in favor of direct helper use. All existing aggregate error,
preload-before-publish, identity, and cache invalidation behavior remains
unchanged.

Both the memory snapshot and persistent cache store the canonical slash form.
The existing cache policy version remains valid because predecessor full values
remain parseable and the cache schema/required fields do not change. A cache
loaded from the predecessor representation is canonicalized on the next normal
cache publication; no eager disk mutation is added.

### 4. Standalone validation and typed construction

`HoldingsValidator._optional_transport_field()` calls the shared parser:

- accepted canonical/predecessor input -> `valid`, nonblocking;
- blank -> `optional_missing`, nonblocking;
- parse failure -> `invalid`, nonblocking.

`RecordValidation.to_holding()` must not pass slash text directly to Pydantic.
For a valid timestamp outcome it calls the shared parser and passes the parsed
`datetime`; for missing/invalid optional outcomes it passes `None`. This
preserves the plan's existing rule that optional timestamp defects do not block
official NAV typed snapshot construction.

### 5. Public/schema behavior

Update `docs/schema.md` to say holdings timestamps are Text dates written as
`YYYY/MM/DD`. Document the bounded predecessor read compatibility without
describing mixed physical data as the desired steady state.

No API/CLI/schema column, receipt text, status code, or reason-code change.

## State and failure invariants

- A malformed timestamp still prevents repository cache publication for the
  entire fresh slice; no partially updated cache becomes authoritative.
- Standalone holdings validation continues to report malformed optional
  timestamps as nonblocking warnings.
- Required facts and duplicate identities remain fail-closed.
- Writer output never preserves or re-emits predecessor full timestamps.
- Read compatibility performs no Feishu write and no eager cache rewrite.
- Futu synchronization confirmation, stage ordering, partial-write flag, and
  evidence persistence remain unchanged.

## Implementation slice S1 - Canonical holdings date boundary

### Objective and expected outcome

Introduce the shared holdings date contract, route every holdings write and
both validation paths through it, and prove production-shaped rows can proceed
without weakening other integrity checks.

### Allowed production files

- `src/domain/holding_dates.py`
- `src/feishu/repositories/holdings_repository.py`
- `src/app/holdings_validation.py`
- `docs/schema.md`

### Allowed test files

- `tests/test_holdings_preload_minimal.py`
- `tests/test_holdings_bulk_upsert_minimal.py`
- `tests/test_holdings_validation.py`
- A directly affected holdings cache or NAV preflight test file only if required
  by an observed failing boundary, recorded in the implementation artifact.

### Exact allowed changes

1. Add strict parse and canonical format helpers with no I/O or mutable state.
2. Replace holdings-only `strftime(DATETIME_FORMAT)` calls with the canonical
   formatter, including every update `now_text`.
3. Replace repository timestamp parsing with the shared parser.
4. Replace validator timestamp parsing and typed construction with the shared
   parser.
5. Add exact payload assertions for canonical single, bulk, replacement, MMF,
   patch, and quantity updates using existing fakes where available.
6. Add repository and validator cases for canonical slash, predecessor full,
   empty, and malformed values.
7. Add a production-shaped multi-row preload/Futu regression proving the 17-row
   incident class no longer raises `HoldingsIntegrityError`; synthetic data is
   sufficient and must not contact production.
8. Update the holdings schema documentation only.

### Non-goals

- No general datetime cleanup or refactor.
- No migration command, background normalization, production API call, or
  deployment logic.
- No timestamp timezone semantics beyond the existing naive `datetime` model.

### Completion signal

- Focused tests prove every listed write boundary emits slash dates and both
  accepted read forms materialize safely.
- Existing strict integrity and sync regressions pass.
- DeepReview finds no accepted contract, missing-write-path, cache, or NAV
  blocker issue.

### Stop condition

Stop for user direction if current code reveals a holdings timestamp consumer
that requires sub-day precision for investment facts, if another active change
overlaps the allowed production files, or if full-format compatibility cannot
be bounded without a production migration.

## Tests and validation

Focused:

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_holdings_date_focused \
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_holdings_preload_minimal.py \
  tests/test_holdings_bulk_upsert_minimal.py \
  tests/test_holdings_validation.py \
  tests/test_holdings_nav_preflight_service.py \
  tests/test_futu_balance_sync_service.py
```

Full:

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_holdings_date_full \
python3.12 -m pytest -q -p no:cacheprovider
```

Compile:

```bash
python3.12 -X pycache_prefix=/tmp/pm_holdings_date_compile \
  -m compileall -q src skill_api.py scripts/pm.py
```

Static checks:

```bash
git diff --check
git status --short
```

Expected assertions:

- Exact written timestamp text matches `^\\d{4}/\\d{2}/\\d{2}$` and not the
  predecessor full format.
- Canonical and predecessor values produce typed `datetime` fields.
- Invalid values retain deterministic record-level errors.
- Optional invalid timestamps remain nonblocking in standalone validation.
- Failed required-field or duplicate rows publish no partial cache.
- Full regression baseline remains green.

## Documentation decision

Update `docs/schema.md` because the Feishu holdings timestamp representation is
an operator-visible storage contract. Do not update changelog/version metadata;
those belong to a separately authorized release.

## Risks and open questions

- Mixed physical source representations persist until future authorized writes:
  `classified as accepted transition state owned by the compatibility parser`.
- Full timestamp sub-day precision is intentionally lost on the next canonical
  write: `classified as explicit user contract`.
- Production remains broken until release/upgrade: `classified as operator-owned
  later authorization boundary`.
- No blocking open questions.

## Review and acceptance sequence

1. Planreview this artifact and fix/re-review any accepted finding.
2. Commit the accepted plan artifacts.
3. Implement S1 and run focused validation.
4. DeepReview S1, fix/re-review accepted findings, and commit the accepted slice.
5. Run aggregate DeepReview against `origin/main`, fix/re-review, full tests,
   compile, and static checks, then commit the accepted aggregate review.
6. Push, create a Draft PR, review the PR, fix/re-review if needed, record the
   accepted PR review state, push, and complete final closeout.

## Completion report format

- Canonical/read-compatible date contract implemented.
- Exact production/test/docs files changed.
- Focused/full/compile/static validation results.
- Finding dispositions and residual-risk ownership.
- Draft PR URL and explicit statement that no release, upgrade, sync retry, NAV
  run, or production data mutation occurred.

## Completion state

- Current gate: `plan review pass`.
- Next gate: `accepted plan commit`.
