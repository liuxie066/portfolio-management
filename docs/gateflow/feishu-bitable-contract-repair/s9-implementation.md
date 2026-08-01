# Gateflow S9 Implementation — Replayable Normalized Valuation Snapshots

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S9
- Base: `833fcb0`
- Recorded at: 2026-08-02T07:09:57+08:00
- Status: implementation complete; pending DeepReview
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s9-implementation.md`

## Scope

The implementation follows the accepted S9 allowlist. One bounded test-fixture
scope correction updates `tests/test_compensation_service.py`: a legacy
compensation snapshot fixture now supplies the price, CNY price, and replayable
market value that the S9 row contract makes required. No compensation runtime
logic or S10 exact-set behavior changed. The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` remains excluded and untouched.

## Implemented Contract

- `NormalizedValuationSnapshot` is an immutable, versioned transmission with
  normalized holding rows, declared nonrow components, shares, price and
  holdings provenance, warnings, excluded-zero evidence, and stable digests.
- Quantity is parsed and normalized once at eight decimal places. Native and
  CNY unit prices remain exact `Decimal(str(value))` values in the normalized
  source. Only `quantity * cny_price` is rounded to persisted money precision.
- Every priced normalized row enforces its replay invariant. Missing price
  evidence remains visible in the immutable valuation for reporting, but the
  snapshot-row projection fails before any NAV or holdings-snapshot write.
- Zero-quantity holdings never become valuation or persisted snapshot rows.
  Their count and sorted business-key digest are retained in immutable
  provenance and in the snapshot target evidence.
- `PortfolioValuation` is now produced from the normalized object. The source
  attaches to its compatibility projection, and official NAV persistence
  compares a semantic digest over aggregates, rows, evidence, provenance, and
  warnings before it consumes the projection.
- `PortfolioReadService` returns both `normalized_valuation` and `valuation`.
  Fresh holdings provenance and warnings are applied by creating a new frozen
  transmission, then re-projecting the compatibility model.
- Account record and initialization flows pass the exact normalized object
  through `PortfolioManager` to `NavRecordService`. Official totals and
  holdings-snapshot rows are projected from that same object.
- Aggregate-only legacy `PortfolioManager.record_nav()` callers retain a
  bounded compatibility bridge: before calculation or mutation, the aggregate
  projection becomes immutable declared components. A compatibility payload
  containing holding rows cannot use this bridge and fails closed.
- `HoldingSnapshot` now requires nonblank account, asset id, broker, currency,
  and dedup key plus finite required quantity, price, CNY price, and market
  value. It preserves unit-price precision and independently verifies replay.
- `snapshot_digest()` is v2 canonical serialization over every persisted row
  field, not only quantity and market value. Native-price or metadata drift
  changes the digest.
- CLOSED builds one zero-row normalized snapshot from `ClosedNavTarget`, with
  explicit digest-covered user-input cash and noncash components. Its
  compatibility projection, persisted totals, weights, shares, NAV, and
  planned snapshot evidence are all derived from that object.
- The existing S1 write registry is exercised for both single and batch
  `holdings_snapshot` creates; both reject every missing required field before
  transport.

## Plan Arithmetic Correction

The accepted plan's example said `12.345 × 10.123` should persist `124.96`.
The exact product is `124.968435`, so standard half-up money rounding is
`124.97`. The implementation and regression use `124.97`; persisting `124.96`
would violate both the stated rounding rule and the replay invariant.

## Validation

- Exact S9 suite: `168 passed`.
- Full repository suite: `1316 passed`.
- Scoped Ruff, Python compileall, and `git diff --check`: passed.
- Regression coverage includes exact unit prices, row replay, v2 full-row
  digest changes, missing/NaN/Inf rejection, zero-row provenance, declared
  components, compatibility mutation rejection, single/batch required-field
  parity, CLOSED zero-row components, and existing compensation deserialization.
- No live Feishu/Futu read or write, schema mutation, business-data repair,
  merge, release, or deployment occurred.

## Residual Boundaries

- S9 intentionally does not make an account/date slice an exact set, remove
  stale rows, bind overwrite authority, durably prepare a multi-table write, or
  fresh-read snapshot completion. Those are S10 responsibilities.
- Existing compensation can still describe its historical upsert completion
  semantics; S10 replaces that with exact-set readback and cannot inherit a
  `complete` claim from the S9 planned evidence.
- Feishu Number is ultimately transported as JSON numeric data. The canonical
  Decimal truth and replay invariant are preserved before the transport
  boundary; no live Number canary is authorized in this slice.

## Next Gate

Run DeepReview over the complete uncommitted S9 diff from `833fcb0`, including
this artifact and the bounded fixture scope correction. Fix every accepted
finding and obtain a no-findings re-review before the scoped local S9 commit.
