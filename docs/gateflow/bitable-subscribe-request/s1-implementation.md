# Gateflow S1 Implementation — Bitable Subscribe Request

- Gate: `implementation`
- Work unit: `bitable-subscribe-request`
- Slice: `S1 — correct and lock the subscription wire request`
- Status: accepted after `docs/reviews/code-review-20260801-092803.md`
- Artifact path:
  `docs/gateflow/bitable-subscribe-request/s1-implementation.md`

## Objective and outcome

Both public Feishu Bitable subscription paths now serialize only the supported
document-subscription query: exact `file_token` plus `file_type=bitable`.
Neither path calls the SDK `event_type` builder method.

## Changed files

- `src/feishu/bitable_event_adapter.py`
  - removed the invalid `event_type` builder call from the combined holdings and
    cash-flow subscription path;
- `src/feishu/holdings_event_adapter.py`
  - removed the same invalid builder call from the holdings-only compatibility
    path;
- `tests/test_feishu_bitable_event_adapter.py`
  - replaced token-only assertions with complete request-map assertions for
    same-file deduplication and distinct-file ordering;
- `tests/test_feishu_holdings_event_adapter.py`
  - updated the complete compatibility request assertion to require no
    `event_type` key.

## Preserved contracts

- long-connection callback registration still accepts only
  `drive.file.bitable_record_changed_v1`;
- target validation, file deduplication, result metadata, response/error
  handling, CLI confirmation, inbox state, receipts, and business-data writes
  are unchanged;
- no production Feishu call or service restart occurred in this implementation
  gate.

## Validation

Focused tests:

```text
PYTHONPYCACHEPREFIX=/tmp/pm_bitable_subscribe_pycache \
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_feishu_bitable_event_adapter.py \
  tests/test_feishu_holdings_event_adapter.py \
  tests/test_pm_cli.py

52 passed in 1.00s
```

Full suite:

```text
PYTHONPYCACHEPREFIX=/tmp/pm_bitable_subscribe_full_pycache \
python3.12 -m pytest -q -p no:cacheprovider

1014 passed in 20.96s
```

## Documentation decision

No operator documentation changed because the public commands and activation
sequence are unchanged. This artifact records the corrected protocol request.

## Residual risks

- `subscription_event_type` remains compatibility metadata and is not request
  evidence. Any public rename/removal is assigned to a separate contract cleanup
  work unit.
- Live Feishu re-subscription is intentionally excluded; production already has
  successful subscriptions created with the corrected request.
- The unrelated untracked review artifact is preserved and excluded from this
  slice.
