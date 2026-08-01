# Gateflow Implementation Artifact — S2

- Gate: `implementation`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S2 — canonical Feishu role consumers`
- Base: `078ec95`
- Status: `accepted after deepreview 20260802-013028`

## Implemented scope

- `FeishuClient` resolves only `feishu.bitable.app_id` and
  `feishu.bitable.app_secret` when constructor values are absent.
- Holdings and cash-flow event targets, the combined Bitable adapter, and the
  compatibility holdings adapter resolve only the Bitable role.
- Holdings, cash-flow, cash-flow-effect, NAV-history, and Futu-sync receipt
  senders resolve only the Conversation role.
- Local event status reports identify the Bitable role, convert credential
  resolver failures into redacted issues, and do not construct an SDK client or
  make a Feishu request.
- Existing constructor injection, event payload handling, target validation,
  subscription request maps, callbacks, receipt bodies, and write authority are
  unchanged.

## Evidence

```text
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_feishu_client.py \
  tests/test_feishu_bitable_event_adapter.py \
  tests/test_feishu_holdings_event_adapter.py \
  tests/test_pm_cli.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_futu_sync_receipt_service.py \
  tests/test_holdings_workflow_service.py \
  tests/test_operation_receipt_outbox_service.py \
  tests/test_cash_flow_effect_service.py

182 passed
```

## Non-actions

- No Feishu request, subscription, listener connection, table write, message
  send, production configuration change, release, or deployment was performed.
- No third Feishu application identity was introduced.
