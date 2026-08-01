# Gateflow S1 Scope Correction — Integration Test Ownership

## Gate Metadata

- Gate: fix scope correction
- Slice: S1
- Work unit: feishu-bitable-contract-repair
- Trigger: full-suite integration validation after scoped re-review
- Status: accepted test-only correction
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/s1-scope-correction.md

## Correction

`tests/test_entrypoint_consolidation.py` was listed under S8 allowed files, but two tests in that file directly import and validate the S1 `scripts/migrate_schema.py` contract. The S1 allowed-file list omitted this integration consumer.

The file is brought into S1 for test-only updates:

- preserve the legacy `parse_docs_schema` import through a registry-backed compatibility projection that never parses markdown;
- supply exact registry type, UI type, and select-option metadata to the schema-check fixture;
- assert optional unconfigured tables as `status=skipped_unconfigured` and `ok=null`, not passed.

No S8 NAV behavior, production entrypoint, live schema, or write authority is changed.

## Evidence

- Before correction, full-suite collection first failed on the removed compatibility import.
- After the registry-backed compatibility helper, the full suite ran 1101 tests and only the stale optional/schema fixture failed.
- This correction preserves the accepted S1 semantics instead of weakening the exact comparator for an old test.

## Next Gate

Run scoped and full validation, then final S1 re-review and accepted commit.
