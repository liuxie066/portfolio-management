# Gateflow S3 Fix — Feishu Agent/Listener Identity

- Gate: `S3 review fix`
- Work unit: `feishu-agent-listener-identity`
- Review: `docs/reviews/code-review-20260806-102831.md`
- Status: `fix complete; pending re-review`
- Artifact path: `docs/gateflow/feishu-agent-listener-identity/s3-fix.md`

## Finding decisions and fixes

### S3-DR-01 — accepted — 已修复

`apply_install()` no longer reads or rewrites an existing EnvironmentFile. It
renders a Secret-free default only for a missing file and writes with
`overwrite=False`. Existing files are scanned for relevant key names and remain
byte-for-byte unchanged. Canonical non-secret Agent/Listener env values may
participate in the explicit role-mapping gate and must agree with YAML.

### S3-DR-02 — accepted — 已修复

The YAML plaintext-secret scanner now detects block-style, quoted, and
flow-style `app_secret` key tokens before loading the non-secret mapping. Errors
contain only the logical key name and never the configured value.

### S3-DR-03 — accepted — 已修复

Any install invocation that requests timer, API, quality, or event-listener
activation now starts `portfolio-feishu-preflight.service` after daemon reload
and before the first `enable --now`. A failed preflight aborts the command
sequence, so no requested consumer is activated. Asset-only apply remains
separate and does not claim preflight success.

## Regression coverage

- Existing target env remains byte-for-byte unchanged and reports
  `skipped_exists`.
- Canonical non-secret role values in the target env satisfy the mapping gate;
  disagreements with YAML fail closed.
- Canonical and legacy plaintext Secret env keys are reported by key name only.
- Block-style and flow-style YAML App Secrets both fail before apply and never
  appear in the error.
- Every activation path runs secure preflight first; a simulated preflight
  failure proves no activation command is executed.

## Scope note

`docs/cash-flow-effects-runbook.md` and
`docs/cash-flow-holding-effects.md` were added to the S3 documentation scope
because they were direct active consumers of the removed receipt-role contract.
Their edits are limited to the Agent/Listener authority wording.

## Residual risk

- Live Feishu permissions and credential validity remain a later, separately
  authorized preflight/canary boundary.
- Production checkout/apply atomicity is unchanged and must be handled by the
  controlled upgrade workflow.
