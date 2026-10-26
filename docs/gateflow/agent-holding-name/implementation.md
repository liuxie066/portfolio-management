# Implementation — Agent single-record name interface

Implemented one approved behavior slice. No commit by implementer; source frozen for
independent review and controller regression. No external requests, production reads,
credentials, token provisioning or public operations performed.

## Contract and source changes

- `agent_holding_name_service.py`: fail-closed SHA-256 bearer authentication against
  trusted active principal policy; exact record/account/broker grants; principal policy
  digest binds operation nonce to current configuration. No client-selected principal.
- `service/agent_holdings.py`: strict preview/confirm/status router, no extra body/query
  parameters, sanitized errors and no-store responses; create_app injection supports
  isolated testing. Disabled endpoints do not construct Agent workflow/state or access rows.
- Preview reads only the exact authorized row before HK evidence lookup. Eligible
  previews persist a local safe snapshot with five-minute expiry, not cases or receipts.
  No raw quantity/cost/full source row or authentication material is persisted in this table.
- Additive `agent_name_operations` table/mixin owns atomic one-use claims and terminal
  results. Status uses read-only SQLite without initialization even on absent/old schema.
  Durable executing/unknown states never automatically reissue writes.
- `complete_name_from_preview` reuses existing account/record locking and protected
  `_execute_patch`, with fresh raw identity/digest checks before provider calls and
  postread validation. Name is the only patch field. Full fresh observed cases preserve
  unrelated same-record blockers. Receipt suppression propagates through discovery,
  absent-case closure and terminal/attention paths; legacy defaults remain enabled.
- Agent errors redact provider exception text; audit identifies principal/preview without
  bearer credentials. Readback identity race never fetches evidence for another account.
- Agent namespace stays in live OpenAPI but is excluded from the immutable OM export;
  existing pinned contract remains byte-identical. No contract tag/version regeneration.
- README plus `docs/agent-holding-name-interface.md` document disabled deployment,
  config-cache/reload semantics, exact access provisioning boundary, local preview write,
  expiry/replay/unknown behavior, no notification/currency mutation and retention limits.

## Implementation checks and corrections

Initial product test collection failed because fixture import omitted the tests package;
corrected import only. Independent QA exposed missing discovery descriptors despite
receipt enqueue disabled; descriptors are now supplied while enqueue remains false.
Controller identified race-to-Futu before live scope verification; workflow now freezes
and authorizes exact raw rows before evaluate_records for both prewrite and postread.
These were first implementation review corrections, not new scope.

Focused suite: tests/test_agent_holding_name.py, test_om_openapi_contract.py,
test_holding_event_inbox_service.py, test_holdings_workflow_service.py.
Runner: Python3.12.13 via /tmp/pm-agent-qa/run_isolated.py (network/private-file guards,
synthetic storage and mocked providers). Final result113passed1.95s in /tmp/pm-agent-qa/implementation-final-focused.log.
Earlier valid result112passed1.98s, before adding old-schema read-only regression.
Full required source Ruff and git diff--check passed. Controller owns full pytest and
compile verification; independent QA owns separate adversarial tests/review.

## Residual risks / ownership

- Existing Feishu non-atomic compare-and-set interval: later storage work unit.
- Agent principal/token/grant provisioning and policy reload/restart: separate explicit
  deployment approval; no secrets or real access configured in this implementation.
- Durable operation retention/pruning: later maintainer work; no new cleanup daemon.
- Unknown writes: require separately authorized inspection/recovery, never blind replay.
- Live provider and production acceptance: not run; mocks do not prove availability.
- Existing date-dependent NAV fixture failures: controller retains baseline evidence;
  implementation did not alter unrelated tests or product dates.
