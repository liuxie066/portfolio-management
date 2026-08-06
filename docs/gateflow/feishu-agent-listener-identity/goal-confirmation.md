# Gateflow Goal Confirmation — Feishu Agent/Listener Identity

- Gate: `goal confirmation`
- Work unit: `feishu-agent-listener-identity`
- Branch: `fix/feishu-agent-listener-identity`
- Base: `main@38ce3d93c6de312db07eac69aefe357b548c601b`
- Status: `accepted`
- Artifact path: `docs/gateflow/feishu-agent-listener-identity/goal-confirmation.md`

## Confirmed product contract

There are exactly two Feishu application identities:

1. **Agent** owns Agent conversations and receipts plus all Feishu Base API reads
   and writes, including Futu holdings/MMF synchronization.
2. **Listener** owns only holdings and cash-flow record-change subscription and
   long-connection ingress. Event-triggered fresh reads or business writes still
   execute through the Agent identity.

The user confirmed this topology and authorized local implementation and
verification. Release, remote upgrade, production credential rotation,
production configuration changes, service reloads, subscriptions, notifications,
real holdings writes, and NAV reruns remain independent non-goals.

## Motivation

The current v0.1.35 contract routes `FeishuClient` Base reads/writes and Bitable
event ingress through `feishu.bitable.*`, while receipt senders use
`feishu.conversation.*`. Production maps the original Agent app to the
conversation role and the Listener app to the bitable role. As a result, the
Listener identity attempted the scheduled MMF record update and Feishu rejected
it with HTTP 400 for both accounts.

## Success signals

- All Base API reads/writes and outbound messages resolve the Agent identity.
- Holdings/cash-flow event targets, subscriptions, and long connections resolve
  only the Listener identity.
- The listener process may receive both encrypted credentials because its worker
  fresh-reads and may perform separately authorized generated-field writes, but
  the Listener app credential is never used for those Base API calls.
- Canonical configuration and credential names express Agent and Listener roles;
  supported legacy inputs migrate deterministically and conflicts fail closed.
- Secure preflight reports both roles without exposing secret values or paths.
- Generated systemd units grant only the credentials required by each process.
- Focused configuration, client, listener, CLI, installer, and receipt tests pass;
  the full suite and static checks preserve the repository baseline.

## Direct code evidence

- `src/feishu_client.py` currently resolves `feishu.bitable.*` for every Base API
  client.
- `src/app/holdings_event_service.py`, `src/app/cash_flow_event_service.py`, and
  `src/feishu/bitable_event_adapter.py` also resolve `feishu.bitable.*` for event
  ingress.
- Receipt services resolve `feishu.conversation.*`.
- `src/config.py` and `src/configuration/feishu_credentials.py` define only the
  Bitable/Conversation role model and named credential files.
- `scripts/install_linux.py` grants those two old roles to generated units and
  documents the event listener as the Base reader/writer.

## Scope boundary

In scope:

- canonical Agent/Listener configuration and encrypted-credential contracts;
- compatibility resolution for supported legacy Bitable/Conversation/direct keys;
- Base-client, receipt, event-target, subscription, and long-connection routing;
- secure preflight and operator diagnostics;
- generated systemd credential grants and installation plan metadata;
- configuration examples, deployment and listener runbooks, and regression tests.

Out of scope:

- changing Base schemas, business field ownership, MMF calculation, Futu source
  semantics, receipt payloads, or event business workflows;
- creating a third identity, a hosted secret service, or a new notification path;
- any production mutation, release, upgrade, or real provider call.

## First-principles judgment

The work unit is necessary because identity authority is part of the write safety
contract. A listener-only credential must not be capable of becoming a writer
through a shared client default. Configuration-only swapping cannot satisfy the
confirmed topology because the current code deliberately couples Base API and
event ingress to one role.

## Overdesign guard

The implementation will keep exactly two identities and reuse the existing Base
client, event adapters, and receipt services. It will not introduce per-table
apps, a general credential broker, dynamic role policies, or runtime permission
discovery. Compatibility will be limited to the currently supported legacy key
families and the immediately preceding v0.1.35 role names.

## Blocking open questions

None. The user supplied the authoritative role topology and confirmed execution.

## Residual risks

- Production provisioning and rotation are assigned to a later explicitly
  authorized operation after a release exists.
- Live Feishu permissions cannot be proven by local tests and remain a deployment
  verification item.
