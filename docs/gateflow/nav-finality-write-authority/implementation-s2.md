# Gateflow Implementation Artifact — Slice S2

- Gate: `implementation`
- Work unit: `nav-finality-write-authority`
- Slice: `S2 — Mutation serialization and safe write defaults`
- Branch: `fix/nav-finality-write-authority`
- Base commit: `14cb8c3`
- Created: `2026-07-21T12:38:36+08:00`
- Artifact path: `docs/gateflow/nav-finality-write-authority/implementation-s2.md`
- Completion status: `implementation and code review complete; ready for accepted slice commit`

## Scope completed

- Added the canonical global same-host `nav_history_lock_key()`.
- Serialized all public repository mutations under the same lock:
  - single full-row write, including remote existence check and create/update;
  - bulk full-row write;
  - derived-field patch;
  - details/recovery patch;
  - record deletion.
- Kept public lock acquisition non-recursive; internal helpers remain lock-free.
- Added `patch_nav_details` to the storage compatibility facade.
- Changed `overwrite_existing` defaults from `true` to `false` through repository, storage, application, portfolio, service HTTP/client, skill compatibility, and close/manual/report entry points.
- Changed `pm nav record` and `pm daily` to explicit `--overwrite`; retained hidden `--no-overwrite` compatibility without restoring unsafe defaults.
- Changed the daily-report publisher to NAV dry-run by default while preserving HTML artifact generation.
- Added explicit `--write-nav --confirm`; rejected missing confirmation and conflicting `--dry-run` / `--write-nav` flags.
- Kept publisher dry-run previews usable for an existing NAV date by passing `overwrite_existing=true` only when `dry_run=true`; explicit publisher writes remain non-overwriting.
- Updated README, runbook, service API, and publisher documentation.

## Invariants

1. Every public authoritative `nav_history` mutation uses the same repository lock key.
2. Single-row existence lookup and create/update stay in one same-host critical section.
3. Repository public methods do not recursively acquire the NAV lock.
4. Manual/report/close write surfaces do not overwrite existing rows unless explicitly requested.
5. `daily-job --overwrite` remains available and unchanged.
6. Publisher default execution can render and publish HTML while sending `dry_run=true`, `confirm=false` for NAV.
7. Publisher NAV persistence requires both `--write-nav` and `--confirm` and sends `overwrite_existing=false`.
8. Repair/backfill paths retain explicit overwrite intent.

## Validation

Corrected pre-implementation baseline:

```text
98 passed in 0.66s
```

S2 focused and adjacent regression suite:

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_nav_bulk_upsert_minimal.py \
  tests/test_nav_history_patch.py \
  tests/test_nav_write_defaults.py \
  tests/test_nav_record_service.py \
  tests/test_entrypoint_consolidation.py \
  tests/test_pm_cli.py \
  tests/test_service_client.py \
  tests/test_service_http.py \
  tests/test_service_application.py \
  tests/test_daily_report_entrypoints.py \
  tests/test_daily_nav_services.py \
  tests/test_nav_history_receipt_service.py

169 passed in 0.72s
```

Compile and whitespace validation:

```text
python3.12 -X pycache_prefix=/tmp/pm_nav_finality_s2_compile \
  -m compileall -q src skill_api.py scripts
# pass

git diff --check
# pass
```

## Test coverage added

- All five public repository mutation methods use `nav-history-write`.
- Public single write acquires the lock once; internal delegation does not nest it.
- Remote existence check and create execute while the lock is active.
- Two concurrent same-date same-host single writes produce one create and one blocked writer.
- Public defaults are non-overwriting across all documented layers.
- Manual CLI defaults, explicit `--overwrite`, and legacy `--no-overwrite` behavior.
- Publisher default NAV dry-run with HTML files still written.
- Publisher confirmation requirement, explicit-write payload, and conflicting-flag rejection.
- Existing repair, CLOSED-row, service, and finality regression coverage remains green.

## Residual risks

- The lock coordinates only processes using the same data directory on the same host. Cross-host uniqueness remains an infrastructure-level follow-up.
- Existing legacy rows remain intentionally unclassified; operators must use validated repair rather than automatic migration.
- The Feishu API remains the persistence authority and does not expose a transactional uniqueness constraint to this application layer.

## Review outcome

- DeepReview: `docs/reviews/code-review-20260721-123836.md` (`pass`).
- Findings: `未发现实质性问题`.

## Next gate

`accepted slice commit` for S2.
