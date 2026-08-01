# Gateflow Implementation Plan — Bitable Subscribe Request

- Gate: `plan`
- Work unit: `bitable-subscribe-request`
- Base: `main@2a1ae72`
- Branch: `fix/bitable-subscribe-request`
- Status: accepted after `docs/reviews/plan-review-20260801-092543.md`

## Goal, motivation, and success signal

Correct the Feishu Bitable document-subscription wire request so both public
subscription commands omit the invalid `event_type` query parameter. Success
means deterministic tests prove the exact request maps contain only
`file_token` and `file_type=bitable`, while all existing subscription and event
listener behavior remains unchanged.

## Design alignment and direct code evidence

There is no separate design document for this incident. The accepted goal,
current adapters, production Feishu response, and successful corrected request
are authoritative.

- `src/feishu/bitable_event_adapter.py` builds one request per distinct Base
  file and currently calls `.event_type(BITABLE_SUBSCRIPTION_EVENT_TYPE)`.
- `src/feishu/holdings_event_adapter.py` builds the compatibility request and
  makes the same invalid builder call.
- The official SDK builder adds `event_type` directly to the query map, so this
  is a request-construction defect rather than an SDK transport failure.

## Public contract and state-machine impact

- CLI command names, confirmation guards, arguments, exit behavior, and JSON
  result keys remain unchanged.
- `subscription_event_type` remains the semantic event selected in the Feishu
  application and local listener; it is not used to construct the Base-file
  subscription API request after this fix.
- No schema, durable state, inbox, case, outbox, receipt, or business-data state
  transition changes.

## Affected files

- `src/feishu/bitable_event_adapter.py`
- `src/feishu/holdings_event_adapter.py`
- `tests/test_feishu_bitable_event_adapter.py`
- `tests/test_feishu_holdings_event_adapter.py`
- `docs/gateflow/bitable-subscribe-request/*`
- review artifacts under `docs/reviews/`

The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` is excluded and must not be
staged, edited, or deleted.

## Implementation decision

Remove `.event_type(...)` from the two existing `SubscribeFileRequest` builder
chains. Keep the event constants because they still define the only accepted
long-connection event and the existing result metadata. Do not introduce a new
request helper for two one-line call sites.

Strengthen the two adapter tests to assert the complete request maps:

- combined adapter, same Base: one request with `file_token=base_portfolio`
  and `file_type=bitable`;
- combined adapter, distinct Bases: two exact request maps in sorted token
  order, neither containing `event_type`;
- holdings compatibility adapter: exact request map with only
  `file_token=base_holdings` and `file_type=bitable`.

## Slice S1 — Correct and lock the subscription wire request

Objective: make both public subscription paths serialize the current Feishu
Bitable request contract.

Allowed changes:

- remove the two invalid builder calls;
- replace token-only assertions with exact request-map assertions;
- update the existing holdings request assertion;
- create required Gateflow and review artifacts.

Non-goals:

- no production request, subscription, listener restart, or Base mutation;
- no changes to callback event registration or normalization;
- no SDK upgrade or new dependency;
- no adapter refactor or result-schema change.

## Validation

Focused behavioral tests:

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_feishu_bitable_event_adapter.py \
  tests/test_feishu_holdings_event_adapter.py \
  tests/test_pm_cli.py
```

Expected assertions:

- exact subscribe request maps omit `event_type`;
- same-file subscription remains deduplicated;
- distinct-file subscription remains deterministic;
- both CLI confirmation guards and adapter wiring continue to pass.

Broader validation:

```bash
python3.12 -m pytest -q -p no:cacheprovider
```

If the full suite cannot run because the worktree lacks an environment or an
unrelated external dependency, record the exact failure and retain the focused
test result; do not weaken assertions.

## Documentation decision

No operator runbook change is required: the public command and activation
sequence remain correct. Gateflow artifacts will record the protocol correction
and production evidence without copying credentials or transient connection
URLs.

## Risks and controls

- Risk: accidentally removing the event type from the long-connection handler.
  Control: change only the subscription request chains and retain existing
  callback-registration tests.
- Risk: one public path remains broken. Control: exact request tests cover both
  adapters.
- Risk: test doubles accept an invalid builder call silently. Control: assert
  the complete built request dictionaries rather than only file tokens.
- Risk: unrelated dirty review artifact enters commits. Control: path-scoped
  staging and status checks before every commit.

## Residual risks

- Live Feishu may change the protocol again. Classified as external protocol
  drift; the current exact-wire regression plus explicit production evidence
  covers the known defect.
- Existing production subscriptions are already active through the corrected
  one-off request. This work unit will not mutate or verify them again.
- The legacy JSON key `subscription_event_type` is retained for compatibility
  and is not wire-request evidence. Any rename/removal is assigned to a
  separate public-contract cleanup work unit if one is later requested.

## Completion report

Report the changed request contract, focused/full test results, review finding
status, commit hashes, draft PR URL, excluded dirty file, and the separate
merge/release/deployment entry point.
