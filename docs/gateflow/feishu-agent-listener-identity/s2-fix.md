# Gateflow S2 Fix — Feishu Agent/Listener Identity

- Gate: `S2 review fix`
- Work unit: `feishu-agent-listener-identity`
- Review: `docs/reviews/code-review-20260806-100608.md`
- Status: `fix complete; pending re-review`
- Artifact path: `docs/gateflow/feishu-agent-listener-identity/s2-fix.md`

## Finding decision and fix

### S2-DR-01 — accepted — 已修复

`FeishuClient` now treats a non-empty constructor `user_token` as an isolated,
explicit authentication path. It keeps only explicitly supplied App credentials
and does not resolve the production Agent App ID/Secret. A missing or blank token
continues through the default Agent resolver.

This does not restore any configured user-token fallback: default clients still
ignore `feishu.user_token` / `FEISHU_USER_TOKEN`, and secure configuration
validation still blocks those sources as `insecure_identity_override`.

## Regression coverage

- Added a constructor test that makes any Agent config read fail, injects a token,
  and proves token headers are produced without Agent resolution.
- Retained default-client tests proving configured user tokens are ignored and
  Agent credentials are selected.

## Residual risk

- The explicit token capability remains available to code that deliberately
  passes it; it is not reachable through production configuration.
- Live Feishu permissions remain outside local validation.
