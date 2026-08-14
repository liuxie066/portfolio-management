# Gateflow Implementation Plan — Non-Futu Cash Holdings Authority

- Work unit: `non-futu-cash-holdings-authority`
- Base: `origin/main@0f496dac5a38bf72d8410d3df7ca5d7e86bb712d`
- Branch: `fix/non-futu-cash-holdings-authority`
- Design source: confirmed user requirements, read-only incident evidence, and
  current repository behavior
- Current gate: `implementation S1`
- Next entry point: implement the approved S1 authority and preview contract
- Work unit status: `planned`

## Goal and motivation

Make broker authority explicit at the shared CASH fingerprint boundary:

- external deposits and withdrawals remain cash-flow facts for every broker,
  affect NAV shares, and are reflected in CASH holdings after confirmation;
- Futu holdings remain governed by OpenD absolute observations;
- non-Futu holdings, including CASH changes caused by trades, fees, and
  settlement, are manually maintained and therefore authoritative;
- structured cash-flow refusal facts reach the NAV receipt instead of being
  flattened into a raw nested exception string.

The current all-broker fingerprint blocker treats normal non-Futu maintenance
as an unresolved external effect. The receipt then exposes an internal gate
payload rather than a business reason or handling action.

## Success signals

1. A non-Futu manual CASH edit creates or updates only a terminal audit effect,
   confirms the observed fingerprint, and does not block NAV.
2. The existing `lx` shape—one pending non-Futu external-change effect and no
   competing cash-flow record—converges to `record_only` on one scan and is
   unchanged by subsequent scans.
3. An apply-mode cash-flow operation touching a non-Futu identity cannot be
   previewed until the operator chooses `apply_delta` or
   `already_reflected`.
4. `apply_delta` preserves the current `fresh + signed delta` behavior.
   `already_reflected` produces a CAS-checked no-op target, confirms the
   current fingerprint, and finishes the cash-flow effect as `applied`.
5. A later cash-flow correction after `already_reflected` applies only the
   correction delta, proving that the previous version remains part of the
   applied version chain.
6. Futu-only operations require no new action and still use OpenD absolute
   targets.
7. An official NAV refusal returns a structured failure code and blockers;
   the consolidated receipt contains broker/currency/actionable guidance and
   no serialized gate JSON.
8. Focused tests, full tests, static checks, and diff checks pass without
   changing unrelated untracked files.

## Non-goals and scope boundary

- No new effect state, effect kind, SQLite table, schema migration, Base field,
  timer, broker abstraction, or background automation.
- No automatic inference from row timestamps or amount similarity that a
  manual balance does or does not include a cash flow. The explicit preview
  action is the authority declaration.
- No change to Futu OpenD retrieval, CASH currency mapping, cash-flow totals,
  NAV share calculation, correction versioning, compensation, locking, CAS,
  fresh readback, or preview-hash invalidation.
- No generic replacement of all raw application errors. Unrelated legacy
  failure rows keep their existing rendering.
- No release, deployment, remote upgrade, production scan, effect resolution,
  NAV replay, or live notification.

## First-principles judgment and direct code evidence

- `CashFlowEffectService._scan_holding_fingerprints()` owns classification of
  CASH holding drift and currently creates `cash_holding_external_change`
  pending effects without inspecting `broker`.
- The same service already has `FUTU_BROKER`, `_futu_target()`, exact holding
  identities, `record_only`, fingerprint confirmation, preview hashes, account
  locks, CAS mutation targets, compensation, and fresh readback. Reusing these
  contracts is smaller and safer than adding another workflow.
- `_cash_flow_operations()` calculates a full amount for a new effect and a
  delta from `get_previous_applied()` for corrections. Consequently,
  `already_reflected` must finish as `applied`, not `record_only`, or future
  corrections would add the corrected full amount again.
- `_build_preview()` already accepts `external_action`, includes it in the
  preview hash, and builds absolute target rows. A current-quantity target is a
  normal CAS-checked no-op and needs no second confirm path.
- `CashFlowDatasetBlocker.as_dict()` already owns structured blocker
  serialization. The loss occurs only when `assert_official_scope()` embeds it
  in `ValueError` text and `AccountNavRecorderService` catches only the generic
  exception.
- `DailyAccountNavService` and `DailyNavJobService` already preserve a failed
  account result dictionary. A typed exception and one structured `failure`
  field can therefore reach `NavHistoryReceiptService` without changing the
  job envelope or outbox.

## Authority and state-machine contract

### Manual CASH holding drift

For each `(asset_id, account, broker)` CASH identity:

1. Observe the fresh holding and persist the observation as today.
2. If the observation equals the confirmed fingerprint, retain the current
   resolution behavior.
3. If the broker is Futu, retain the existing pending external-change path and
   OpenD recovery target.
4. Preserve the existing compensation authority before any broker branch:
   identities owned by a `compensation_pending` target are never accepted as
   manual drift, and a `compensation_pending` external effect is never
   auto-transitioned. The compensation task remains the sole recovery owner and
   continues to block NAV.
5. If the broker is non-Futu after the compensation guard:
   - construct the existing `cash_holding_external_change` source and hash;
   - when the latest same-source effect is unresolved, transition it to
     `record_only` with an automatic-policy confirmation and audit event, except
     that `compensation_pending` is never eligible;
   - when the source is new, create the new version directly as terminal
     `record_only` with event `manual_baseline_auto_accepted`;
   - confirm the observed fingerprint with that effect id;
   - never write the holding from this branch and never leave a blocker.
6. Repeated scans with the same holding and fingerprint perform no state or
   holding mutation.

This path doubles as the idempotent migration for existing pending non-Futu
effects. `create_version()` already supersedes an older unresolved version when
the observed source changes.

### Non-Futu cash-flow preview choice

For an apply-mode `cash_flow` effect after `_cash_flow_operations()` is known:

- if at least one operation targets a non-Futu broker, require
  `external_action` to be exactly `apply_delta` or `already_reflected`;
- `apply_delta` keeps `fresh quantity + operation delta`, including the
  existing nonnegative estimate guard;
- `already_reflected` sets each non-Futu target to its fresh current quantity;
  Futu rows in a mixed correction continue to use their OpenD absolute target;
- both choices produce ordinary `mode=apply` target rows, bind the action into
  the preview hash, pass through the existing CAS/readback path, confirm every
  target fingerprint, and finish as `applied`;
- a correction touching more than one non-Futu identity applies the selected
  declaration to all non-Futu operations in that effect. A mixed real-world
  state must be normalized by the operator before confirmation; the preview
  exposes every before/target row and cannot silently combine declarations.

Ordinary record-only historical previews keep the existing early return and
require no new action. Once `historical_apply=True` explicitly requests a real
holding write, any operation touching non-Futu must choose `apply_delta` or
`already_reflected` exactly like a current apply-mode effect. Futu-only effects
keep the existing preview contract.

### Structured NAV refusal

Add one domain exception, `CashFlowDatasetRefusal(ValueError)`, carrying:

- stable `reason_code`;
- immutable blockers;
- optional simple diagnostic details;
- the current human-readable exception message for log compatibility.

`CashFlowDatasetSnapshot.assert_official_scope()` raises it for scope/integrity
mismatch, embedded blockers, and incomplete effect gates. No caller parses
exception text.

`AccountNavRecorderService` catches this type before the generic exception and
returns:

```json
{
  "success": false,
  "error": "existing diagnostic message",
  "failure": {
    "code": "CASH_FLOW_DATASET_BLOCKED",
    "blockers": []
  }
}
```

The raw `error` remains in the durable job payload for diagnostics. The NAV
receipt prefers the structured `failure` contract and never renders the raw
cash-flow JSON. Unknown structured refusal codes use a generic Run-ID handling
message; unrelated results without this contract retain current rendering.

`nav_gate()` adds safe broker, currency, and signed-amount fields to each effect
blocker so the receipt does not parse `record_id` or source text.

## Public interface and compatibility changes

- Extend `pm cash-flow effects preview/confirm --external-action` choices with
  `apply_delta` and `already_reflected` while retaining `accept_current` and
  `restore` for historical/manual external-change review.
- Apply-mode non-Futu cash-flow preview without one of the two new choices is a
  deliberate fail-closed contract change with an actionable error.
- Preview and confirm must receive the same action because it is already part
  of the preview hash.
- No existing Futu-only command changes.
- Daily NAV JSON gains a structured `failure` object for cash-flow dataset
  refusals; existing top-level fields remain.

## Affected files and modules

### Slice S1

- `src/app/cash_flow_effect_service.py`
- `scripts/pm.py`
- `tests/test_cash_flow_effect_service.py`
- `docs/cash-flow-holding-effects.md`
- `docs/cash-flow-effects-runbook.md`
- `README.md`

### Slice S2

- `src/domain/cash_flow_contracts.py`
- `src/app/cash_flow_effect_service.py` (safe blocker projection only)
- `src/app/account_nav_recorder_service.py`
- `src/app/nav_history_receipt_service.py`
- `tests/test_cash_flow_summary_service.py`
- `tests/test_daily_nav_services.py`
- `tests/test_nav_history_receipt_service.py`

No change is planned for `cash_flow_effect_store.py`; its schema, terminal
states, update CAS, event log, and fingerprint methods already satisfy the
contract.

## Implementation slices

### S1 — Broker-aware CASH authority and explicit non-Futu cash-flow action

- Objective: remove false non-Futu drift blockers without weakening Futu and
  preserve exactly-once holding semantics for deposits, withdrawals, and later
  corrections.
- Allowed files: S1 files listed above plus Gateflow/review artifacts.
- Prerequisites: accepted plan commit.
- Exact changes:
  1. Add the non-Futu terminal baseline branch in
     `_scan_holding_fingerprints()` using existing effect/fingerprint methods,
     strictly after the compensation-identity guard and excluding
     `compensation_pending` transitions.
  2. Require and validate `apply_delta` or `already_reflected` only for
     apply-mode cash-flow operations touching non-Futu.
  3. Build no-op absolute targets for `already_reflected`; do not add a special
     confirm state transition.
  4. Extend CLI choices and update command documentation.
  5. Replace the old explicit-accept non-Futu drift test with automatic,
     idempotent baseline assertions; add legacy pending convergence.
  6. Add action, correction, mixed/Futu, historical-apply, and compensation
     regression tests.
- Invariants: no holding write from scanner; cash-flow confirmation remains the
  only writer; preview hash/CAS/fresh readback remain mandatory; Futu is exact.
- Non-goals: receipt formatting and domain refusal changes.
- Completion signal: S1 focused tests and static checks pass; code review has no
  unresolved accepted finding.
- Stop condition: any evidence of double application, Futu target regression,
  unresolved non-Futu drift after rescan, or correction using the full amount.

### S2 — Structured NAV refusal and actionable receipt

- Objective: preserve cash-flow blocker structure through the NAV service chain
  and render an actionable consolidated failure.
- Allowed files: S2 files listed above plus Gateflow/review artifacts.
- Prerequisites: accepted S1 commit.
- Exact changes:
  1. Add the typed refusal and replace the three official-scope raises without
     changing their log messages.
  2. Serialize the typed refusal into the account result's existing `failure`
     field before the generic exception path.
  3. Add safe broker/currency/amount fields to effect gate blocker output.
  4. Add a pure receipt formatter for structured cash-flow refusals and prefer
     it in `_item_row()`.
  5. Add domain, propagation, rendering, fallback, and raw-JSON exclusion tests.
- Invariants: official NAV still rejects every existing blocker; raw diagnostic
  facts remain in the job result; unrelated errors render as before.
- Non-goals: generic error taxonomy or changes to the receipt outbox.
- Completion signal: S2 focused tests and static checks pass; code review has no
  unresolved accepted finding.
- Stop condition: any path bypasses `assert_official_scope`, loses blockers,
  exposes nested JSON, or changes unrelated failure rows.

## Validation commands and expected assertions

### S1 focused

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_s1 \
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_cash_flow_effect_service.py

python3.12 -m ruff check \
  src/app/cash_flow_effect_service.py scripts/pm.py \
  tests/test_cash_flow_effect_service.py
```

Expected assertions:

- non-Futu manual drift -> terminal audit + confirmed fingerprint + gate pass;
- existing pending drift -> one terminal transition, second scan unchanged;
- missing non-Futu action -> blocked preview with actionable choices;
- `apply_delta` -> one signed delta;
- `already_reflected` -> unchanged quantity + applied state;
- correction after no-op application -> correction delta only;
- Futu-only target -> unchanged OpenD behavior.
- explicit historical apply touching non-Futu -> same mandatory action;
- compensation-owned non-Futu drift -> no auto-baseline or external terminal
  transition, and NAV remains blocked by compensation.

### S2 focused

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_s2 \
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_cash_flow_summary_service.py \
  tests/test_daily_nav_services.py \
  tests/test_nav_history_receipt_service.py

python3.12 -m ruff check \
  src/domain/cash_flow_contracts.py \
  src/app/cash_flow_effect_service.py \
  src/app/account_nav_recorder_service.py \
  src/app/nav_history_receipt_service.py \
  tests/test_cash_flow_summary_service.py \
  tests/test_daily_nav_services.py \
  tests/test_nav_history_receipt_service.py
```

Expected assertions:

- typed refusal retains exact blockers and existing diagnostic message;
- account result contains stable failure code and serialized blockers;
- effect blockers include safe broker/currency/amount facts;
- receipt explains pending cash-flow vs Futu reconciliation and gives handling;
- receipt does not contain `EFFECT_GATE_BLOCKED`, `effect_store_revision`, or
  serialized blocker JSON;
- unrelated snapshot and legacy failure rows remain unchanged.

### Aggregate

```bash
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_full \
python3.12 -m pytest -q -p no:cacheprovider

python3.12 -m compileall -q src scripts tests
git diff --check
```

The full suite must pass. No live Feishu, Futu, timer, production SQLite, or NAV
write is part of validation.

## Documentation decision

Update the existing cash-flow authority document, runbook, and README command
examples. No new standalone user guide is needed. Gateflow artifacts document
design/review evidence but are not the public operational contract.

## Risks and residual-risk classification

- Operator selects the wrong non-Futu action: mitigated in current slices by
  mandatory explicit choice, complete before/target preview, preview hash, and
  confirm. No system can infer inclusion from two manually maintained facts.
- One corrected effect contains mixed non-Futu inclusion states: the action is
  effect-wide. The operator must normalize all affected non-Futu rows before
  confirm. This is an explicit current-slice limitation, documented and tested
  as fail-visible preview rows; no evidence justifies a per-target action API.
- Manual non-Futu typo becomes authoritative: accepted product consequence of
  the user-confirmed source-of-truth boundary. The terminal audit effect and
  Feishu row history preserve evidence; adding a second unverifiable approval
  is rejected as the original failure mode.
- Futu external-change and broker reconciliation overlap: unchanged existing
  behavior and assigned to a later work unit only if independent evidence shows
  duplicate blockers. It is outside this non-Futu incident.

No unclassified residual risk remains in the plan.

## Completion report format

Final closeout will report:

- authority/state-machine and receipt changes;
- focused/full validation and review finding status;
- docs updated;
- residual risks and owners;
- draft PR URL and merge as the next user-authorized step;
- explicit confirmation that no release, deployment, production effect change,
  NAV replay, or live notification occurred.
