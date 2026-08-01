# Gateflow Goal Confirmation — Feishu Dual-App Credentials

- Gate: `goal confirmation`
- Work unit: `feishu-dual-app-credentials`
- Base: `origin/main@59625b0d6c666da338c0a520e85a221932846949`
- Branch: `codex/feishu-dual-app-credentials`
- Status: `confirmed by user objective: 新建分支，按方案完成优化`

## Goal and motivation

Portfolio Management must expose exactly two user-configured Feishu application
identities:

1. the **Bitable application**, which owns Base reads/writes, Drive/Bitable
   subscription, and the record-change long connection; and
2. the **conversation application**, which sends workflow receipts and addresses
   the configured user.

Their App Secrets must no longer need to be stored as plaintext in YAML or a
systemd `EnvironmentFile`. The deployed Linux services must receive only the
credentials needed by their role and fail closed when secure credential mode is
enabled but a secret resolves from a plaintext source.

## Direct code evidence

- `src/feishu_client.py` and both Bitable event targets/adapters currently share
  `feishu.app_id` / `feishu.app_secret`. This is already one application identity
  for table data and event ingress, not two different bots.
- Receipt services use `feishu.receipt.*`, with compatibility fallbacks to
  `OM_FEISHU_BOT_*`. This is the second identity.
- `scripts/install_linux.py` currently imports the conversation App Secret into
  `/etc/portfolio-management/portfolio-management.env`, and every generated unit
  consumes the same `EnvironmentFile`.
- The event listener runbook explicitly says `feishu.app_*` belongs to the PM data
  enterprise app rather than the receipt bot.

## Success signals

- Canonical, role-named configuration makes the two identities unambiguous.
- Table API calls, Base subscription, and long-connection startup resolve the
  same Bitable application identity.
- Receipt senders resolve only the conversation identity.
- systemd credential files are the highest-priority secret source; generated
  production units enable secure mode and load only required credentials.
- Secure mode rejects plaintext App Secret values from YAML or environment
  variables without echoing them.
- Generated config/env/install output contains no App Secret value.
- Legacy keys remain migration inputs only and conflicting canonical/legacy
  identities fail closed.
- Existing holdings, cash-flow, NAV, receipt, event routing, manual-confirmation,
  and write-authority behavior is unchanged.

## Non-goals

- No third Feishu application or separate event-only App Secret.
- No multi-tenant database, OAuth onboarding UI, external Secrets Manager, Vault,
  KMS, or cloud-specific client.
- No new conversational command/state machine; current receipt delivery semantics
  remain unchanged.
- No Feishu permission mutation, subscription mutation, real credential creation,
  production file edit, release, or remote upgrade in this work unit.
- No general rewrite of all project secrets; only the two Feishu App Secrets are
  brought behind the new credential-source boundary.

## Boundary judgment

The work unit is justified because the current runtime conflates user-facing
roles in generic key names and persists one App Secret through an environment
file. A generic secret-management framework or third application would exceed
the request. A small role-specific resolver plus systemd credential integration
is sufficient for the current single-host product and leaves a later hosted
Secrets Manager implementation behind the same logical configuration contract.

## Blocking open questions

None. Real secrets will be rotated and provisioned only during a separately
authorized deployment.

