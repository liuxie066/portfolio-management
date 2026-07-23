# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `pm-feishu-receipt-title`
- Branch: `fix/pm-feishu-receipt-title`
- Base: `origin/main@ce5cfb4`
- Artifact path: `docs/gateflow/pm-feishu-receipt-title/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `确认`
- Confirmed at: `2026-07-23 17:36:52 +0800`

## Why this work unit exists

PM receipt messages are rendered as flat Markdown, including a first-line H1 and
optional H2 section headings. Both production receipt services currently pass
that Markdown to `FeishuClient.send_text_message()`, which sends
`msg_type="text"`. Feishu therefore renders `# PM · 回执 · ...` and `## ...`
literally instead of presenting a native title and emphasized section labels.

## Target outcome

Add a narrow Feishu `post` send boundary that converts the existing receipt
shell into Feishu rich-text structure, then migrate the Futu holdings-sync and
NAV History receipt services to that boundary without changing their business
content or delivery semantics.

## Success signals

1. A Futu receipt for account `sy` is sent with `msg_type="post"` and native
   `zh_cn.title="PM · 回执 · sy"`.
2. A NAV receipt is sent through the same post interface with native
   `zh_cn.title="PM · 回执 · NAV History"`.
3. The first H1 line is absent from the visible post body.
4. Receipt H2 lines such as `## 持仓变化`, `## 账户明细`, and `## 告警` are
   represented as bold text nodes without visible hash markers.
5. Normal receipt lines, ordering, internal blank-line separation, Unicode, and
   returned `message_id` remain intact.
6. Dry-run continues to skip client construction and sending. Missing
   configuration and send/build failures keep the existing best-effort receipt
   failure result and never rewrite the Futu sync or NAV job result.
7. Focused tests, the full suite, compile checks, diff checks, and all review
   gates pass.

## Scope boundary

### In scope

- One additive `FeishuClient.send_post_message()` interface for the existing
  receipt Markdown shell.
- Strict H1 extraction into `zh_cn.title`.
- Strict H2 conversion into bold post text nodes.
- Internal blank-line preservation with non-visible spacer paragraphs.
- Futu holdings-sync and NAV History receipt call-site migration.
- Deterministic payload, service-call, dry-run, and failure regression tests.

### Out of scope

- Changing `render_receipt()` or any receipt business field, ordering, wording,
  truncation rule, warning aggregation, or NAV performance formatting.
- Changing Futu synchronization, NAV calculation, NAV persistence, quote
  acquisition, finality, retry, or write authority.
- Replacing the existing text sender or migrating unrelated notifications.
- Adding a general Markdown parser, message card, localization framework, link,
  mention, image, or interactive-message abstraction.
- Modifying options-monitor.
- Live Feishu sending, production mutation, release, deployment, or remote
  upgrade.

## Direct code evidence

- `src/app/notification_shells.py` emits the first line as
  `# PM · 回执 · {title}` and non-empty sections as `## {section}`.
- `src/app/futu_sync_receipt_service.py` and
  `src/app/nav_history_receipt_service.py` both call
  `FeishuClient.send_text_message()`.
- `src/feishu_client.py` fixes that method's payload to
  `msg_type="text"` and serializes `{"text": ...}`.
- Both receipt services already skip dry-run before client creation, validate
  the same three configuration values, catch client/build/send exceptions, and
  return receipt-local failure data.
- Feishu's message-create contract accepts `msg_type="post"` with a JSON-string
  content object containing `zh_cn.title` and two-dimensional paragraph nodes.
- The focused pre-change baseline passed: `60 passed`.

## Minimality decision

- Keep `render_receipt()` as the single business presentation source and adapt
  only at the Feishu transport boundary.
- Keep `send_text_message()` unchanged for compatibility.
- Parse only the exact structural forms produced by the shared receipt shell:
  first-line `# ` and body-line `## `.
- Use one implementation slice because the client contract, two one-line
  call-site migrations, and their regressions form one atomic behavior change.

## Blocking open questions

- None. The user confirmed the target, scope, and success signals.

## Residual risks

- Exact desktop/mobile post spacing cannot be proven without a live send:
  `operator-owned release canary; no live mutation authorized in this work unit`.
- Unrelated callers can continue to use plain text and render Markdown
  literally: `intentional compatibility boundary; only the two named receipt
  services are in scope`.

## Completion state

- Current gate: `goal confirmation pass`.
- Next gate: `plan`.
