# Gateflow Fix Artifact — S1 Review

- Gate: `fix`
- Work unit: `holdings-validation-completion-conflict-receipts`
- Slice: `S1 — raw validation, resolver, and cache safety`
- Review artifact: `docs/reviews/code-review-20260731-205546.md`
- Artifact path:
  `docs/gateflow/holdings-validation-completion-conflict-receipts/fix-s1-review.md`
- Re-review artifact: `docs/reviews/code-review-20260731-210927.md`
- Status: `fixed; re-review pass`

## Finding decisions and fixes

### DR-S1-01 — accepted — fixed

Holdings preload now validates the complete fresh slice before any cache
mutation. It aggregates duplicate business identities, rejects invalid required
and optional persisted fields without the loose Feishu parser, and preserves
the previously published cache when validation fails. All write paths validate
required typed fields before remote access, so cache v2 cannot be populated by
a new blank-broker holding.

Regression coverage includes duplicate rows, invalid optional JSON, aggregate
row errors, preservation of an existing cache, and pre-I/O write rejection.

### DR-S1-02 — accepted — fixed

Futu matching now parses a symbol plus optional market qualifier. Prefix and
suffix representations of the same market match; two explicit, different
markets do not. Unqualified symbols remain usable only when the completed
account snapshot yields a unique position.

Regression coverage includes same-market prefix/suffix normalization,
explicit market disagreement, and unqualified cross-market ambiguity.

### DR-S1-03 — accepted — fixed

The validation contract now owns `canonical_record_payload()` and
`record_digest()`. It hashes only validation-relevant persistent fields after
text trimming, enum/currency case normalization, finite Decimal normalization,
and stable tag handling. Transport metadata outcomes are classified but remain
nonblocking.

Regression coverage proves equivalent numeric/text/enum/tag representations
produce one digest and a semantic quantity change produces a different digest.

### DR-S1-04 — accepted — fixed

Raw reads now validate nonblank mutually exclusive scopes. Account reads reject
any returned row whose raw account differs from the requested account, and
record reads reject a returned `record_id` that differs from the requested id.
Neither path infers missing identities from its query.

Regression coverage includes malicious cross-account list output and a
mismatched one-record response.

## Additional fail-closed hardening

- A failed fresh Futu observation is now a source-incomplete result with
  `success=false`, even when populated fields happen to have no local blocker.
- Currency and numeric cache conversion reject non-ASCII currencies,
  booleans, non-finite values, and float overflow.
- Existing Feishu storage fixtures were updated to provide complete required
  raw identities instead of relying on removed query/default inference.

## Validation

- `305 passed` across validator, reconciliation, repository, cache, Feishu
  client/storage, Futu sync/reconciler, CLI, read service, application, and HTTP
  regression tests.
- `python3.12 -m compileall -q src scripts/pm.py` passed.
- `git diff --check` passed.

## Completion state

- Current gate: `slice code review pass`.
- Next gate: `accepted S1 slice commit`.
