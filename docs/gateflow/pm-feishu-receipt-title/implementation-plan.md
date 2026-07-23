# Gateflow Implementation Plan

- Gate: `plan`
- Work unit: `pm-feishu-receipt-title`
- Branch: `fix/pm-feishu-receipt-title`
- Base: `origin/main@ce5cfb4`
- Goal artifact: `docs/gateflow/pm-feishu-receipt-title/goal-confirmation.md`
- Artifact path: `docs/gateflow/pm-feishu-receipt-title/implementation-plan.md`
- Status: `plan review pass`

## Goal and motivation

Make PM's Futu holdings-sync and NAV History receipts use Feishu's native post
title and rich-text section emphasis so structural Markdown markers are no
longer visible, while preserving the current business message and best-effort
notification contract.

## Success signals

- The final Feishu request uses `msg_type="post"`.
- A Futu `sy` receipt produces native title `PM · 回执 · sy`.
- The first H1 does not appear in the post body.
- H2 section markers become bold text nodes with no visible `##`.
- Both receipt services call the new post interface.
- Dry-run, missing-config, send failure, business-result isolation, and message
  content behavior remain unchanged.
- Focused and full validation plus all Gateflow review gates pass.

## Non-goals and scope boundary

- Do not alter `render_receipt()` output, receipt fields, warning aggregation,
  section omission, change-row limit, NAV values, Futu sync logic, or result
  state machines.
- Do not remove or change `send_text_message()`.
- Do not create a generic Markdown AST/parser, message-card layer, or new
  dependency.
- Do not modify OM, release, deploy, send a live canary, or mutate production.

## First-principles judgment and direct evidence

The defect is a transport mismatch, not a content-generation defect:

- `render_receipt()` deliberately emits H1/H2 structure.
- The two services pass that structure unchanged to a sender that fixes
  `msg_type="text"`.
- Plain text cannot express a native Feishu title or node style.
- Feishu post content already provides the exact two primitives required:
  `zh_cn.title` and paragraph text nodes with `style=["bold"]`.

The correction therefore belongs in an additive Feishu transport adapter. It
does not belong in NAV/Futu business logic, and changing `#` to another
Markdown spelling would leave the same transport mismatch.

## Affected files and ownership

### Production

- `src/feishu_client.py`
  - Own the new post request contract and receipt-shell conversion.
- `src/app/futu_sync_receipt_service.py`
  - Change only the outbound method name and argument name.
- `src/app/nav_history_receipt_service.py`
  - Change only the outbound method name and argument name.

### Tests

- `tests/test_feishu_client.py`
  - Lock the serialized Feishu post payload and input validation.
- `tests/test_futu_sync_receipt_service.py`
  - Prove the Futu service calls only the post interface and preserves its
    existing state semantics.
- `tests/test_nav_history_receipt_service.py`
  - Prove the NAV service calls only the post interface and preserves its
    existing state semantics.
- Existing `tests/test_notification_shells.py` remains unchanged and continues
  to prove that business rendering did not change.
- `tests/test_service_application.py` is validation-only to confirm receipt
  failure remains isolated from the parent business result.

### Gateflow artifacts

- `docs/gateflow/pm-feishu-receipt-title/`
- Timestamped plan/code/PR review artifacts under `docs/reviews/`.

## Public interface and protocol contract

Add:

```python
def send_post_message(
    self,
    *,
    open_id: str,
    markdown: str,
) -> Dict[str, Any]:
    ...
```

This is additive. `send_text_message()` remains unchanged.

### Input normalization and validation

1. Normalize `open_id` and `markdown` with the same string-and-strip behavior as
   the text sender.
2. Reject an empty `open_id` with `ValueError("open_id is required")`.
3. Reject empty Markdown with `ValueError("markdown is required")`.
4. Require the normalized first line to be exactly the structural form
   `# <non-empty title>`; otherwise raise a deterministic `ValueError`.
5. Require at least one non-empty body line after the title so invalid Feishu
   post content fails locally before token or HTTP work.

The two production call sites always consume `render_receipt()`, which satisfies
this contract.

### Markdown-to-post conversion

Given:

```text
# PM · 回执 · sy

类型｜持仓同步
状态｜✅ 成功

## 持仓变化
US.AAPL: 数量 1→2
```

construct:

```python
{
    "zh_cn": {
        "title": "PM · 回执 · sy",
        "content": [
            [{"tag": "text", "text": "类型｜持仓同步"}],
            [{"tag": "text", "text": "状态｜✅ 成功"}],
            [{"tag": "text", "text": "\u00a0"}],
            [{
                "tag": "text",
                "text": "持仓变化",
                "style": ["bold"],
            }],
            [{"tag": "text", "text": "US.AAPL: 数量 1→2"}],
        ],
    }
}
```

Conversion rules:

1. Remove the first H1 line from the body and use its trimmed text as
   `zh_cn.title`.
2. Trim leading and trailing blank body lines.
3. Collapse each internal run of blank lines to one non-breaking-space text
   paragraph, preserving visual section separation without leaking a marker.
4. Convert only exact body lines beginning `## ` with a non-empty remainder to
   one bold text paragraph without the prefix.
5. Convert every other non-empty body line to one plain text paragraph without
   interpreting or deleting hashes elsewhere in business data.
6. Keep paragraph order and Unicode exactly.

### Feishu request and response

Reuse `_request()`:

```python
self._request(
    "POST",
    "/im/v1/messages",
    params={"receive_id_type": "open_id"},
    json={
        "receive_id": open_id_value,
        "msg_type": "post",
        "content": json.dumps(post_content, ensure_ascii=False),
    },
)
```

Return the same shape as `send_text_message()`:

```python
{
    "success": True,
    "message_id": data.get("message_id"),
    "receive_id_type": "open_id",
}
```

The existing `_request()` remains authoritative for token acquisition, timeout,
rate limiting, HTTP failure, Feishu non-zero code, and response extraction.

## State and failure invariants

- Dry-run exits before configuration validation, client creation, Markdown
  conversion, or sending.
- Missing configuration returns the same receipt-local `failed` result.
- Conversion, client, token, HTTP, Feishu API, or response errors remain inside
  each receipt service's existing broad exception boundary.
- A receipt failure never changes the parent Futu sync or NAV job `success`.
- Successful sending preserves `message_id`.
- Message building still occurs only for non-dry-run, configured sends.
- The post converter has no mutable state and introduces no retry or idempotency
  behavior beyond `_request()`.

## Implementation slice S1 — Post receipt transport

### Objective

Introduce the post transport contract and move both named receipt services to
it as one atomic change.

### Allowed production files

- `src/feishu_client.py`
- `src/app/futu_sync_receipt_service.py`
- `src/app/nav_history_receipt_service.py`

### Allowed test files

- `tests/test_feishu_client.py`
- `tests/test_futu_sync_receipt_service.py`
- `tests/test_nav_history_receipt_service.py`

### Exact changes

1. Implement `FeishuClient.send_post_message()` with the validation, conversion,
   payload, and return contracts above.
2. Change Futu's call from
   `send_text_message(open_id=..., text=build_message(...))` to
   `send_post_message(open_id=..., markdown=build_message(...))`.
3. Make the same one-boundary change for NAV.
4. Change each service fake/failed client to expose only
   `send_post_message()`, making a regression to the old interface fail.
5. Add exact client payload assertions for the `sy` receipt example:
   `msg_type`, decoded `zh_cn.title`, removed H1, bold H2, ordinary lines,
   spacer paragraph, Unicode, endpoint, receive-id type, and return shape.
6. Add deterministic invalid-input cases for empty open ID, empty Markdown,
   missing/empty H1, and missing body.
7. Retain the existing service dry-run, missing-config, success, and send-failure
   assertions.

### Non-goals

- No change to the shared receipt renderer or business payloads.
- No other Feishu call-site migration.
- No live API request or credential use.

### Completion signal

- Changed-path tests prove the serialized request and both service migrations.
- Existing notification-shell and application isolation tests still pass.
- DeepReview finds no accepted protocol, failure-state, or scope issue.

### Stop condition

Stop and request user direction only if current code or official protocol
evidence contradicts the `zh_cn.title`/paragraph contract, if either service
requires a different visible business result, or if unrelated dirty changes
overlap the allowed files.

## Test and validation commands

Focused:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_feishu_client.py \
  tests/test_notification_shells.py \
  tests/test_futu_sync_receipt_service.py \
  tests/test_nav_history_receipt_service.py \
  tests/test_service_application.py
```

Full:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m pytest -q -p no:cacheprovider
```

Compile:

```bash
python3.12 -X pycache_prefix=/tmp/pm_feishu_receipt_title \
  -m compileall -q src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```

Static change checks:

```bash
git diff --check
git status --short
```

Expected assertions:

- Client request has `msg_type="post"` and JSON-string `content`.
- Decoded `zh_cn.title` equals `PM · 回执 · sy`.
- No post body element contains the structural H1 or H2 hash prefix from the
  receipt shell.
- Section nodes use `style=["bold"]`; ordinary content remains plain.
- Both services invoke `send_post_message` and cannot fall back silently to
  `send_text_message`.
- Dry-run makes zero client calls.
- Failed delivery stays receipt-local.
- All previous business-content assertions remain true against the Markdown
  passed to the post sender.

## Documentation decision

No public CLI, config, schema, architecture, or runbook change is required. The
operator-facing change is the corrected Feishu rendering, fully described by
tests and Gateflow artifacts. Release notes belong to a separately authorized
release work unit.

## Why this is not over-designed

- One additive client method and two call-site substitutions solve the complete
  bug.
- The converter recognizes only the two structures the existing shell emits.
- No new dependency, class hierarchy, protocol type, renderer replacement,
  schema, configuration, state, or general-purpose formatting layer is added.
- The original text sender and all unrelated notifications remain untouched.

## Risks and open questions

- Live client-specific spacing remains unverified without an authorized send:
  `residual risk assigned to an operator-owned release canary`.
- Feishu may enforce an undocumented size ceiling, but these bounded receipts
  already fit the existing text path and this work unit does not change their
  content: `unchanged low risk; no speculative size framework added`.
- Blocking open questions: none.

## Plan review decision

- Review artifact: `docs/reviews/plan-review-20260723-174115.md`
- Conclusion: `pass`
- Findings: none.
- Residual risks are classified under an operator-owned release canary and the
  intentional two-service compatibility boundary.

## Review and acceptance sequence

1. Run `planreview` against the confirmed goal and this plan.
2. Fix accepted plan findings and re-review until pass.
3. Commit the accepted goal/plan/review artifacts.
4. Implement S1 and run focused validation.
5. Run `deepreview` on S1; fix/re-review until pass; commit the accepted slice.
6. Run full validation and aggregate `deepreview` against `origin/main`;
   fix/re-review until pass; commit accepted aggregate artifacts.
7. Push, create a Draft PR, review the PR, fix/re-review if required, create the
   accepted PR-review checkpoint, push, and record final closeout.

## Completion report format

- What changed.
- What was verified, with exact pass counts.
- Plan/code/aggregate/PR finding status.
- Documentation decision.
- Residual risks and owners.
- Draft PR URL and next user-controlled action.

## Completion state

- Current gate: `plan review pass`.
- Next gate: `accepted plan commit`.
