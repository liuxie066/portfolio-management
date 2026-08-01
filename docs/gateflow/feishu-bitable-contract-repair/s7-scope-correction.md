# Gateflow S7 Scope Correction — Canonical NAV Test Fixtures

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S7
- Recorded at: 2026-08-02T05:04:04+08:00
- Added test-only file: `tests/test_feishu_storage.py`
- Production scope change: none

## Reason

The post-fix full repository suite found six legacy NAV storage tests whose
mock responses omit `account`. Each test exercises an account-scoped read whose
request explicitly projects the canonical `account` field, so the fixture does
not represent a valid normal response under the accepted S1/S7 structure
contract.

S7 now correctly refuses to manufacture a missing account identity. Weakening
that production rule to satisfy incomplete mocks would reintroduce
DR-S7-RR-01. The smallest truthful correction is therefore to add
`account="测试账户"` to only those valid-response fixtures.

## Boundary

- No assertion, behavior expectation, or production source is broadened by
  this correction.
- Missing-account, mismatched-account, and missing-record-ID failure behavior
  remains covered in `tests/test_nav_bulk_upsert_minimal.py`.
- No live Feishu/Futu request or business-data mutation is authorized.
