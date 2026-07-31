# Gateflow S1 Implementation — Shared Transport and Cash-Flow Inbox

- Gate: `implementation`
- Work unit: `cash-flow-event-generated-field-completion`
- Slice: `S1`
- Plan checkpoint: `9d68747`
- Status: `implemented; pending code review`

## Objective and outcome

Add exact, durable cash-flow event ingress without granting the slice any
cash-flow business-write capability.

Implemented outcome:

- exact cash-flow target normalization with the same bounded Feishu schema 2.0
  trigger contract as holdings;
- an additive, separately versioned `cash_flow_event_inbox` in the existing
  operation-state database;
- durable accept, collision detection, leased claims, retry, terminal completion,
  read-only status inspection, and atomic outcome/receipt completion primitives;
- a cash-flow worker shell that routes exact actionable record IDs to an injected
  handler and audits ignored delete actions;
- a shared official-SDK adapter that validates target uniqueness, opens one long
  connection, and subscribes each distinct Base file token once;
- no default record handler and therefore no cash-flow row read or write path in
  this slice.

## Changed files

- `src/app/cash_flow_event_service.py`
- `src/app/cash_flow_event_inbox_service.py`
- `src/app/operation_state_store.py`
- `src/feishu/bitable_event_adapter.py`
- `tests/test_cash_flow_event_service.py`
- `tests/test_cash_flow_event_inbox_service.py`
- `tests/test_feishu_bitable_event_adapter.py`

## State and error handling

- callback acceptance freezes event identity, target, actions, revision, and
  full payload digest before returning;
- same event ID/same payload is idempotent; same ID/different payload fails;
- expired claims return to retryable state under the existing five-minute lease;
- handler errors schedule retry through the inbox and do not mark success;
- completion verifies the exact claim and atomically records outcome plus any
  future typed receipt rows;
- read-only status never creates or migrates the operation-state database.

## Validation

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_cash_flow_event_service.py \
  tests/test_cash_flow_event_inbox_service.py \
  tests/test_feishu_bitable_event_adapter.py \
  tests/test_holdings_event_service.py \
  tests/test_holding_event_inbox_service.py \
  tests/test_feishu_holdings_event_adapter.py \
  tests/test_operation_state_store.py
```

Result: `34 passed in 0.69s`.

```text
ruff check <S1 changed Python files>
```

Result: `All checks passed!`.

`git diff --check`: passed.

## Docs decision

No operator documentation changes in S1. Runtime entry points and behavior are
not yet connected; docs belong to S3.

## Residual risks

- Four-attempt terminal attention policy is intentionally deferred to approved
  S2, which adds the receipt contract and concrete handler.
- The shared adapter is not used by a runtime CLI until S3.
- Live SDK/subscription behavior remains outside local implementation and needs
  a separately authorized future canary.

All residual risks are covered by later approved slices or the separately
authorized rollout boundary.

## Next entry point

`code review`
