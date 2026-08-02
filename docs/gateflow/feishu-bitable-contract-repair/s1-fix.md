# Gateflow S1 Fix — Deepreview Findings

## Gate Metadata

- Gate: fix
- Slice: S1
- Work unit: feishu-bitable-contract-repair
- Review artifact: docs/reviews/code-review-20260801-234152.md
- Recorded at: 2026-08-01T23:46:08+08:00
- Status: accepted after final S1 re-review
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/s1-fix.md

## Finding Decisions

### DR-S1-01 — accepted — fixed

- `_request()` now requires an explicit, integer-compatible top-level `code`.
- A successful response must also contain an object-valued `data` member.
- Missing/invalid code and missing/non-object success data raise `RuntimeError` before any caller can interpret the response as a normal empty result or confirmed write.
- Added regression cases for `{}`, data-without-code, null code, missing data, and list-valued data.

### DR-S1-02 — accepted — fixed

- Added `field_names_by_encoding()` as a registry-owned projection.
- Remote Number and JSON Text conversions in `FeishuStorage` now derive their field sets from the registry for both outbound and inbound conversion.
- Kept only the local-only `price_cache` Number set outside the remote registry.
- Removed the independent transactions/cash-flow/NAV numeric read sets, including the stale remote `tax` classification.
- Added all-table Number projection coverage and compensation JSON Text round-trip coverage.

### DR-S1-03 — accepted — fixed

- `schema_expectations()` and the generated documentation now both project sorted `RETIRED_REMOTE_TABLES` instead of hard-coding `price_cache`.

### DR-S1-RR-01 — accepted — fixed

- Replaced broad `int(...)` coercion with an exact `type(raw_code) is int` protocol check.
- Added fail-closed regression cases for boolean, fractional, and numeric-string codes.

### DR-S1-RR2-01 — accepted — fixed

- Restored `parse_docs_schema()` only as a registry-backed compatibility projection; it ignores its historical markdown path and cannot restore docs as runtime truth.
- Corrected the integration test fixture to supply exact live metadata and assert optional tables as skipped with `ok=null`.
- Recorded the test-only allowed-file correction in `s1-scope-correction.md`.

## Validation Evidence

- S1 scoped suite including integration consumer: 204 passed in 0.94s.
- Full suite: 1101 passed in 6.11s.
- Generated schema docs check: passed.
- Registry expectations export: passed.
- Python compileall: passed.
- `git diff --check`: passed.

## Next Gate

Accepted S1 commit, then S2 implementation.
