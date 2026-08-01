# Gateflow S3 Implementation — Futu Source Contracts

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S3
- Base: `5ac4756`
- Recorded at: 2026-08-02T02:08:23+08:00
- Status: accepted after DeepReview and re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s3-implementation.md`

## Implemented Contract

- Added explicit valid, missing, and invalid provider numeric states. Quantity
  and average cost retain source-state evidence; missing or malformed values can
  no longer silently become zero.
- Validate the complete authoritative position slice before any holdings read,
  diff, or mutation. Invalid identity, duplicate normalized identity, unknown
  classification, invalid quantity/cost/currency, short quantity, and unknown
  position side fail closed as one source-validation failure.
- Preserve an explicit zero quantity as a close target and clear average cost.
  A zero row without an existing holding remains a no-op rather than creating
  an empty record.
- Require an explicit valid provider currency for new rows. Existing rows keep
  their valid currency and all manual metadata.
- Centralized instrument-type economic-exposure authority: A shares map to
  China assets, CASH/MMF map to cash, and other instruments remain unclassified
  without instrument-level evidence.
- Futu diff construction and every reconciliation attempt now consume a fresh,
  complete Feishu account slice. Repository failure produces unavailable
  evidence and never falls back to optimistic cache.
- Reconciliation mismatches include identity, field, actual remote value,
  requested value, and record ID. Explicit-zero completion also proves that
  average cost was cleared.

## Deterministic Verification

- Initial scoped S3 suite: `119 passed`.
- Impacted service suite before final boundary fixes: `208 passed`.
- Final scoped S3 suite: `134 passed`.
- Full repository suite: `1169 passed`.
- Python compileall and diff hygiene: passed.
- No live Futu or Feishu request was made.

## Expected Assertions Closed

- A mixed snapshot containing a valid row and an N/A, NaN, Inf, malformed, or
  float-overflowing quantity performs zero Feishu reads for diff and zero
  writes.
- Explicit zero closes an existing row and clears `avg_cost`, while preserving
  manual class, industry, tag, name, and existing valid currency.
- New HK ETFs and US-listed China-exposure ETFs remain unclassified; A shares
  receive the one deterministic China classification.
- A cache match cannot make a fresh remote mismatch trusted; remote read failure
  remains unavailable after retry.

## Next Gate

Commit the accepted S3 scope, then start S4.
