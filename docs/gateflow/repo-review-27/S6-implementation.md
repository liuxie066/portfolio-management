# S6 Implementation Artifact

- Gate: `implementation -> code review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S6-feishu-boundary`
- Base commit: `b676543`
- Status: `complete; code review passed`
- Recorded at: `2026-07-19 19:11:15 +0800`
- Artifact path: `docs/gateflow/repo-review-27/S6-implementation.md`

## Objective

Fix source findings 12, 13, 14, and 27: bound every Feishu HTTP request, reject ambiguous batch responses, preserve truthful confirmed counts and record IDs, fail closed on delete, and validate the complete typed live schema across all pages.

## Changed production files

- `src/config.py` / `config.example.yaml` — configurable positive Feishu connect/read timeout defaults (5s/30s) with environment overrides.
- `src/feishu_client.py` — token and data requests receive timeout tuples; delete errors propagate; batch create/update/delete validate cardinality and record IDs; partial chunk failures raise `FeishuBatchWriteError` with operation, table, chunk offset, and prior confirmed results.
- `src/feishu/repositories/holdings_repository.py` — counts and created record IDs come from confirmed responses; cache removal occurs only after confirmed delete.
- `src/feishu/repositories/cash_flow_repository.py` — applied count comes from confirmed update responses and cache invalidation follows success.
- `src/feishu/repositories/nav_history_repository.py` — counts/created IDs come from confirmed responses; compatibility fallback is blocked when an exception reports confirmed prior chunks.
- `src/feishu/repositories/snapshots_repository.py` — created/updated counts come from confirmed responses.
- `scripts/migrate_schema.py` — typed documented schema model, official Feishu field-type mapping, full pagination, strict required-type comparison, and non-blocking optional/extra reporting.
- `docs/schema.md` / `docs/migrations.md` — document accepted type families, pagination, strict behavior, and official field type IDs.

## Changed tests

- `tests/test_feishu_client.py`
- `tests/test_feishu_storage.py`
- `tests/test_nav_bulk_upsert_minimal.py`
- `tests/test_schema_check.py` (new)

## Invariants proved

- Tenant-token and all session requests receive the configured connect/read timeout unless a caller supplies a smaller explicit timeout.
- Empty, short, long, missing-ID, mismatched-ID, and later-chunk failures cannot be returned as a complete batch success.
- A structured batch failure exposes only results from prior fully validated chunks as confirmed.
- Update request IDs are validated before any network write, and response updates are mapped back by record ID.
- A delete configuration, transport, HTTP, or API failure propagates and does not remove the holding cache entry.
- Repository success counts and created record IDs derive from returned confirmed records rather than requested payload length.
- NAV legacy-field fallback cannot replay a prior confirmed chunk.
- Live schema inspection follows `has_more/page_token` to exhaustion and strict mode blocks required field type drift.
- Documented alternatives map literally: text=1, number=2, select=3/4, date=5, datetime=5 with `ui_type=DateTime`, and JSON text=1.

## Validation

- Required S6 focused command -> `134 passed`.
- Full repository suite -> `650 passed`.
- `python3 -m compileall -q src scripts tests` -> passed.
- `git diff --check` -> passed.

## Documentation decision

- Operator/schema documentation was required because timeout keys and strict typed-schema semantics are externally visible configuration/maintenance contracts.
- No schema mutation is automated; the checker remains read-only.

## Residual risks

- Future undocumented Feishu response/type changes are `covered by strict failure and later deployment canary`.
- A timeout after Feishu accepted a write remains outcome-unknown; S6 reports the failure truthfully and S7 prevents automatic write replay through the service fallback path.
- Previously committed remote chunks in a structured partial batch require caller/operator recovery using `confirmed_results`; no automatic compensation mechanism was added in this slice.

## Review artifact

- Code review: `docs/reviews/code-review-20260719-191407.md`.
- Decision: `code review pass`; accepted findings: `0`.

## Completion state

- Current gate: `accepted slice commit`.
- Next entry point: create `gateflow: accept repo-review-27 S6-feishu-boundary`, then begin S7 implementation.
