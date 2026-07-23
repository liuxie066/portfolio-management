# Gateflow Implementation Artifact — S1

- Gate: `implementation`
- Work unit: `pm-feishu-receipt-title`
- Slice: `S1 — Post receipt transport`
- Branch: `fix/pm-feishu-receipt-title`
- Base: `origin/main@ce5cfb4`
- Accepted plan commit: `b46c906`
- Plan:
  `docs/gateflow/pm-feishu-receipt-title/implementation-plan.md`
- Artifact path:
  `docs/gateflow/pm-feishu-receipt-title/implementation-s1.md`
- Status: `code review pass`

## Scope implemented

- Added `FeishuClient.send_post_message(open_id, markdown)`.
- Extracted the exact first-line H1 into native `zh_cn.title`.
- Removed that H1 from the post body.
- Converted exact H2 section lines into bold text nodes without hash markers.
- Preserved internal blank separation with a non-breaking-space paragraph.
- Kept all ordinary receipt lines as ordered Unicode text paragraphs.
- Migrated only `FutuSyncReceiptService` and `NavHistoryReceiptService`.
- Kept `send_text_message()` and the shared `render_receipt()` unchanged.

## Changed production files

- `src/feishu_client.py`
  - Added local input validation, deterministic shell conversion, post payload
    serialization, `_request()` reuse, and the existing success return shape.
- `src/app/futu_sync_receipt_service.py`
  - Replaced the text send call with
    `send_post_message(open_id=..., markdown=...)`.
- `src/app/nav_history_receipt_service.py`
  - Replaced the text send call with
    `send_post_message(open_id=..., markdown=...)`.

## Changed tests

- `tests/test_feishu_client.py`
  - Locks endpoint, receive-id type, `msg_type="post"`, JSON-string content,
    `PM · 回执 · sy`, removed H1, bold H2, spacer paragraph, Unicode, response
    shape, and pre-request invalid-input rejection.
- `tests/test_futu_sync_receipt_service.py`
  - Uses a `sy` write result and a fake exposing only
    `send_post_message()`.
- `tests/test_nav_history_receipt_service.py`
  - Success and failure fakes expose only `send_post_message()`.

## Preserved invariants

- Dry-run performs no client construction or send.
- Missing configuration remains a receipt-local failure.
- Conversion or delivery exceptions remain inside the existing service
  exception boundary.
- Parent Futu sync and NAV job success remain independent of receipt delivery.
- `message_id`, business message fields, ordering, warning content, section
  omission, and change-row truncation are unchanged.
- `_request()` still owns tokens, timeouts, rate limiting, HTTP/API errors, and
  response extraction.

## Validation

- Focused implementation tests:

```text
PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_feishu_client.py \
  tests/test_notification_shells.py \
  tests/test_futu_sync_receipt_service.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_service_application.py

87 passed in 0.34s
```

- Production changed-file compile:

```text
python3.12 -X pycache_prefix=/tmp/pm_feishu_receipt_title_impl \
  -m compileall -q \
  src/feishu_client.py \
  src/app/futu_sync_receipt_service.py \
  src/app/nav_history_receipt_service.py

pass
```

- `git diff --check`: pass.

## Documentation decision

No public CLI, configuration, schema, architecture, or runbook documentation
changed. The behavior is an internal transport correction covered by tests and
Gateflow artifacts.

## Residual risks and uncovered areas

- Live Feishu desktop/mobile visual spacing:
  `assigned to an operator-owned release canary; not authorized in this work
  unit`.
- Unrelated plain-text notification callers:
  `intentional compatibility boundary; outside this work unit`.
- Full repository suite:
  `covered by the later approved aggregate-validation gate`.

## Code review decision

- Review artifact: `docs/reviews/code-review-20260723-174921.md`
- Findings: none.
- Conclusion: `pass`.
- No fix or re-review was required.

## Completion state

- Current gate: `code review pass`.
- Next gate: `accepted slice commit`.
