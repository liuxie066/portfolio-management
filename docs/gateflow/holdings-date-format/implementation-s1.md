# Gateflow Implementation Artifact - S1

- Gate: `implementation`
- Work unit: `holdings-date-format`
- Slice: `S1 - canonical holdings date boundary`
- Branch: `fix/holdings-date-format`
- Base: accepted plan commit `4dee943`
- Status: `code review pass; ready for accepted slice commit`
- Artifact path: `docs/gateflow/holdings-date-format/implementation-s1.md`
- Code review: `docs/reviews/code-review-20260801-090321.md`

## Objective and outcome

Restore `YYYY/MM/DD` as the canonical holdings `created_at`/`updated_at`
representation at every Feishu and holdings-cache write boundary, while reading
the canonical form and the immediately preceding full timestamp through one
strict shared parser.

The implementation is complete. Production-shaped slash rows now preload
without `HoldingsIntegrityError`; predecessor full timestamps remain readable
and become day-granular when the validated cache is published.

## Changed files

### Production

- `src/domain/holding_dates.py`
  - Added canonical `HOLDING_DATE_FORMAT`.
  - Added the bounded predecessor format.
  - Added exact round-trip parse and canonical format helpers.
- `src/feishu/repositories/holdings_repository.py`
  - Canonicalized persistent cache, in-memory cache, patch, single upsert,
    replacement, bulk upsert, quantity update, and holding serialization dates.
  - Delegated strict repository timestamp parsing to the shared parser.
- `src/app/holdings_validation.py`
  - Reused the shared parser for optional transport field validation.
  - Parsed accepted timestamp text before constructing typed `Holding` objects.
- `docs/schema.md`
  - Documented slash-date writes and bounded predecessor read compatibility.

### Tests

- `tests/test_holdings_preload_minimal.py`
  - Added exact canonical assertions for patch, update, create, replace, MMF,
    quantity update, and cache publication.
  - Added a 17-row incident-class preload regression containing both accepted
    source representations.
  - Added malformed source rejection and no-cache-publication coverage.
- `tests/test_holdings_bulk_upsert_minimal.py`
  - Added exact canonical update/create and memory/persistent cache assertions.
- `tests/test_holdings_validation.py`
  - Added canonical and predecessor typed-construction cases.
  - Added exact rejection cases and retained the nonblocking invalid-optional
    contract.

## Contract decisions preserved

- The global `DATETIME_FORMAT` and every non-holdings table remain unchanged.
- Writer output is only `YYYY/MM/DD`.
- Read compatibility accepts only `YYYY/MM/DD` and
  `YYYY-MM-DD HH:MM:SS`; exact round-trip formatting rejects non-zero-padded or
  alternative ISO values.
- Repository source conversion stays aggregate/fail-closed and publishes no
  partial cache on a malformed timestamp.
- Standalone validation still reports malformed timestamps as nonblocking
  optional metadata, and typed NAV snapshot construction substitutes `None`
  only for those already-invalid optional fields.
- No external I/O, production mutation, sync retry, NAV run, release, or
  deployment occurred.

## Validation

- Focused tests:
  `117 passed in 0.80s`.
- Python compileall for `src`, `skill_api.py`, and `scripts/pm.py`: passed.
- `git diff --check`: passed.
- Initial focused run exposed one incorrect test expectation that predecessor
  sub-day time would survive canonical cache publication. The assertion was
  corrected to the approved day-granular contract; no production code change
  was needed for that failure.

## Residual risks

- Mixed physical Feishu rows remain until future writes:
  `accepted transition state; bounded parser owns it`.
- An existing full timestamp loses sub-day precision when published into the
  canonical holdings cache or written back:
  `explicit user-owned YYYY/MM/DD contract`.
- Live Feishu acceptance and production recovery are not exercised:
  `deterministic payload coverage now; release/upgrade remains separately
  authorized`.
- The unrelated untracked asset-class review artifact remains untouched and is
  excluded from this slice:
  `owned by prior work unit`.

All residual risks are classified; none blocks code review.

## Completion state

- Current gate: `code review pass`.
- Next gate: `accepted slice commit`.
