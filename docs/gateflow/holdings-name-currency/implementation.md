# Implementation gate — local holdings name/currency slice

Status: implemented; frozen for independent code review/QA. No commit performed by implementer.
Scope: accepted plan and correction addendum; synthetic records only, no external requests,
production access, credentials, messages, push/PR/issues/comments, release or deployment.

## Changes and decisions

- Dedicated HTTPS Tencent HK name evidence lookup reuses existing payload parser but
  requires exact matching outer/inner identity and rejects repeated target assignments.
  One five-second I/O timeout, no price/FX/cache/account query, session closed.
- Pure `src/domain/holding_names.py` owns strict nonzero ASCII 1–5 digit/HK-alias
  normalization and evidence validation; added to the file boundary to avoid making
  pure validation depend on an HTTP service. It validates source, market, canonical
  code, non-code display name and timezone-aware observation time.
- Reconciliation adds injectable HK lookup; ordinary reads remain lookup-free.
  Existing fund empty-name machinery is generalized internally, retaining fund audit
  labels and adding HK-specific policy labels. Existing locks, digest check, durable
  attempt, readback and unknown outcome handling are reused unchanged.
- `src/domain/holding_currencies.py` contains a static local Foundation/ICU 78 snapshot,
  actually exported on 2026-10-26 using `Locale.Currency.isoCurrencies.identifier`.
  It accepts known/historical denominations including CNH, excludes XTS and XXX,
  and is explicitly not a current legal-tender catalog or FX support list.
- Currency v2 checks both raw and proposed codes. The separately approved reviewed
  typo rule HDK -> HKD offers blocking conflict only when valid authority evidence
  does not disagree. Static policy evidence has no invented timestamp; real
  corroborating evidence retains its timestamp and authority identity.
- Only explicit exact-case accept-proposed can write the correction. Event name
  completion and apply_missing leave currency conflicts untouched. Invalid currency
  cannot use keep-current, historical durable keep or direct typed conversion to
  bypass the NAV blocker. Valid currency keep behavior remains unchanged.
- Runbook documents both name policies, scope and currency confirmation boundaries.

## Validation performed by implementer

Runner: `/Users/liuxie/.pyenv/versions/3.12.13/bin/python3 /tmp/pm-holdings-qa/run_isolated.py`
with tests/test_asset_name_service.py tests/test_holdings_validation.py
 tests/test_holding_event_inbox_service.py tests/test_holdings_reconciliation_service.py
 tests/test_holdings_workflow_service.py tests/test_holdings_nav_preflight_service.py -q.
Runner blocks real network/subprocess/private/production file access and uses fresh
config/data paths. No skip requested. Before amendment: 242 passed. During first
expanded check: 241 passed, one failure was the intentionally changed currency v2
case digest fixture; updated its fixed hash with an explanatory comment, retaining
all state/precondition assertions. After amendment: 253 passed (final exact output
also available from controller). No full-suite duplicate run by implementer.

Changed-source ruff check passed. git diff --check passed. Independent QA and full
repository checks are owned by controller/reviewer and are not claimed here.

## Coverage

Mocked lookup aliases, invalid/foreign/ambiguous identity, empty/code-only name,
timeout and session close; evidence source/code/market/timestamp/cache guards;
non-candidates, no cross-broker lookup, unchanged existing names, ordinary read
no lookup, duplicate/self events, human edits during lookup/final check, unknown
write outcome no auto-repeat; invalid raw/proposed currencies, legal code
compatibility, reviewed typo static/real evidence, explicit accept-only currency
patch, stale confirmation, repeat confirmation, invalid keep and typed/NAV bypass.

## Residual risks

- Existing Feishu non-atomic CAS window: assigned to later work unit, unchanged.
- Live Tencent availability/response acceptance: later user-authorized acceptance;
  current scope prohibits live validation. Unavailable evidence stays manual.
- Currency snapshot maintenance and future typo rules: future reviewed maintainer
  work; no runtime catalog update and no fuzzy correction.
- Remote CI, public delivery and production acceptance: explicitly unexecuted;
  later authorization required. Next gate is independent code review/QA, not PR pass.
