# Gateflow S2 Implementation — Exact Completion, FX Gate, and Receipts

- Gate: `implementation`
- Work unit: `cash-flow-event-generated-field-completion`
- Slice: `S2`
- Plan checkpoint: `9b1ba3e`
- Status: `implemented; pending code review`

## Objective and outcome

Add the cash-flow-specific business policy behind the durable S1 inbox without
changing the holdings workflow, NAV authority, or cash-holding effect boundary.

Implemented outcome:

- fresh exact-record dry-run classification for every actionable event;
- deterministic CNY generated-field completion under an exact-record same-host
  lock, with a second preview before write and a fresh readback after write;
- one shared FX-confirmation evaluator used by both the event handler and NAV
  preflight;
- foreign-row auto-completion only when the existing local confirmation matches
  source hash, flow date, rate, CNY amount, source, and evidence type exactly;
- semantic typed receipts for invalid data, missing/stale FX confirmation, and
  exhausted event processing;
- exactly four event-processing attempts, with 1/5/15-minute retry delays and
  an atomic fourth-attempt `processed/attention_required + receipt` transition;
- cash-flow receipt rendering and delivery through the existing durable
  operation receipt outbox.

## Changed files

- `src/app/cash_flow_event_completion_service.py`
- `src/app/cash_flow_fx_confirmation.py`
- `src/app/cash_flow_receipt_service.py`
- `src/app/cash_flow_event_inbox_service.py`
- `src/app/nav_record_service.py`
- `src/app/operation_receipt_outbox_service.py`
- `src/app/operation_state_store.py`
- `src/process_lock.py`
- focused tests for the services, inbox, outbox, NAV gate, store, and lock key

## Safety and idempotency

- Event payload fields never authorize a write; the handler reads the exact
  current Feishu row through repository reconciliation.
- Missing and already-converged records are silent terminal no-ops.
- Invalid manual data and missing/stale foreign evidence never call apply.
- Provider FX apply remains refused at repository level; this slice adds no FX
  provider and writes no evidence into Feishu.
- Remote write uncertainty remains retryable. A later fresh read sees a
  converged record and finishes without repeating the mutation.
- Receipt identity excludes event ID, revision, timestamps, and generated
  fields. FX error text is derived from the stable reason code so duplicate
  semantic issues cannot collide on the same key with different payload bytes.
- Cash-flow event completion and receipt insertion share one SQLite transaction.
- Cash-flow generated-field completion does not confirm or apply CASH holding
  effects.

## Validation

Focused/regression tests:

```text
61 passed in 1.09s
```

The set covers exact-record CNY apply/readback, self-write no-op, deletion,
invalid rows, missing/stale/valid FX evidence, apply uncertainty,
non-convergence, semantic receipt identity, four-attempt exhaustion, receipt
routing, NAV shared validation, SQLite state, and record locks.

Static validation:

```text
ruff: All checks passed!
git diff --check: passed
```

## Residual risks

- Runtime composition is intentionally deferred to approved S3; the current
  cash-flow inbox has no production CLI listener wiring yet.
- Live Feishu subscription/write behavior remains outside this local work unit
  and requires separate future release and deployment authority.
- The lock coordinates this host only; external Feishu writers are handled by
  repeat preview/readback and retry, not a distributed transaction.

## Next entry point

`code review`
