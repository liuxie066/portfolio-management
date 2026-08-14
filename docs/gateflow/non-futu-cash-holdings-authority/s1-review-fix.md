# Gateflow S1 Fix Artifact — Code Review

- Work unit: `non-futu-cash-holdings-authority`
- Slice: `S1`
- Gate: `fix`
- Review artifact: `docs/reviews/code-review-20260814-104221.md`
- Re-review artifact: `docs/reviews/code-review-20260814-104637.md`
- Status: accepted; no unresolved findings

## Finding decisions and fixes

### DR-S1-01 — accepted — fixed

Moved the compensation target identity guard before every ordinary
observation/drift resolution branch. Observation resolution now also excludes
`compensation_pending` from its CAS states. Added a regression proving a
matching current observation cannot terminate a compensation-owned external
effect.

### DR-S1-02 — accepted — fixed

When a repeated manual value matches an older external effect source, the
scanner now records the automatic manual-authority event even if that effect
was already terminal. It changes a prior `applied` restore to `record_only`,
clears stale preview/target/compensation metadata, records the automatic-policy
confirmation, and then confirms the fingerprint. Added restore -> repeated
manual value -> idempotent rescan coverage.

### DR-S1-03 — accepted — fixed

Changed the aggregate target source for heterogeneous target rows to neutral
`mixed`. Added a Futu exact + non-Futu `already_reflected` correction preview
test that verifies both row-level sources and the aggregate value.

## Validation after fixes

```text
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_s1_fix python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_effect_service.py
27 passed in 0.55s

python3.12 -m ruff check src/app/cash_flow_effect_service.py scripts/pm.py tests/test_cash_flow_effect_service.py
All checks passed!

git diff --check
pass
```

No live external state was read or changed.
