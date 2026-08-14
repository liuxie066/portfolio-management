# Gateflow S1 Implementation — NAV Historical Receipt Recovery

- Gate: `implementation`
- Work unit: `nav-historical-receipt-recovery`
- Slice: `S1`
- Status: `implemented; pending code review`
- Current gate: `code review`
- Next entry point: deepreview current S1 changes

## Scope and changed files

- `src/app/holdings_nav_preflight_service.py`: reconstruct raw receipt facts,
  verify record/raw digests, rerun current pure validation, and rebuild the typed
  snapshot.
- `src/app/nav_valuation_evidence_service.py`: exact outbox fallback, receipt
  scope checks, receipt-bound artifact contract, and preview provenance.
- `src/app/account_nav_recorder_service.py`: narrowly allow historical receipt
  artifacts to differ from current Holdings while preserving fresh preflight and
  cash-flow gates; use historical snapshot provenance and audit both digests.
- `tests/test_nav_valuation_evidence_service.py`: round-trip/tamper, preparation,
  artifact binding, replay exception, and normal replay regression coverage.
- `docs/nav-valuation-evidence-replay.md`: operator contract for the fallback.

## Decisions and invariants

- Serialized validation statuses are discarded after raw field reconstruction;
  current pure validation owns authorization.
- Existing CLI, outbox schema, artifact store, historical providers, NAV writer,
  finality, and snapshot persistence are reused unchanged.
- Only preparation `historical_receipt_recovery` with an exact source receipt
  key may differ from current Holdings; every other artifact retains equality.
- Current Holdings is never written or rolled back.

## Validation

- `tests/test_nav_valuation_evidence_service.py`: 14 passed.
- Holdings/daily NAV/evidence/CLI focused suite: 135 passed.
- `python3.12 -m compileall -q src scripts`: passed.
- `git diff --check`: passed.

## Docs decision

Updated the existing focused runbook; no new operator document or command.

## Residual risks

- Real legacy payload and historical providers still require production preview:
  covered by the authorized post-release fail-closed preview.
- Historical/current Futu drift is intentional and audited in finality:
  fixed in this slice.
- Historical provider availability and existing multi-host/backup boundaries:
  retained existing owners.

No residual risk is unclassified.
