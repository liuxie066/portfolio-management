# Gateflow S1 Fix — Feishu Agent/Listener Identity

- Gate: `S1 review fix`
- Work unit: `feishu-agent-listener-identity`
- Review: `docs/reviews/code-review-20260806-095907.md`
- Status: `fix complete; pending re-review`
- Artifact path: `docs/gateflow/feishu-agent-listener-identity/s1-fix.md`

## Finding decision and fix

### S1-DR-01 — accepted — 已修复

Expanded ambiguous migration evidence so every old role-named App ID and App
Secret (`bitable`, `conversation`, `receipt`, and the OM receipt fallbacks) is
considered when either new Agent or Listener identity is absent. The old direct
Base-writer `feishu.app_id/app_secret` remains the sole automatic Agent alias and
does not supply Listener.

Canonical identities still win when explicitly configured; old role sources are
then reported as redacted migration shadows. `open_id` remains Agent-owned and
therefore only recognizes old Conversation/receipt/OM sources as ambiguous.

## Regression coverage

- Added cross-role cases for Bitable-only -> Agent ambiguity.
- Added Conversation/receipt/OM-only -> Listener ambiguity.
- Existing tests continue to prove the safe direct-writer Agent alias and
  canonical-plus-old shadow behavior.

## Residual risk

- Runtime consumers remain unchanged until S2.
- Production role mapping and new encrypted credential provisioning remain a
  separately authorized deployment prerequisite.
