# Gateflow Implementation Plan — Feishu Agent/Listener Identity

- Gate: `plan`
- Work unit: `feishu-agent-listener-identity`
- Branch: `fix/feishu-agent-listener-identity`
- Base: `main@38ce3d93c6de312db07eac69aefe357b548c601b`
- Goal artifact: `docs/gateflow/feishu-agent-listener-identity/goal-confirmation.md`
- Status: `accepted after plan re-review`
- Artifact path: `docs/gateflow/feishu-agent-listener-identity/implementation-plan.md`

## Goal and motivation

Represent the confirmed two-app topology directly in the configuration and
runtime contract:

- Agent: Agent conversations, receipts, and every Base API read/write.
- Listener: holdings/cash-flow subscription and long-connection ingress only.

This prevents the listener-only app from inheriting business write authority
through the default `FeishuClient` and fixes the identity-routing regression that
caused scheduled MMF updates to receive HTTP 400.

## Success signals

- `FeishuClient` and every receipt sender resolve `feishu.agent.*` only.
- event targets and adapters resolve `feishu.listener.*` only.
- event-triggered workers use a normal `FeishuClient`, therefore use Agent
  credentials for fresh reads and generated-field writes.
- secure configuration requires exactly one Agent and one Listener credential;
  diagnostics remain redacted and fail closed on conflicts or missing files.
- only semantically safe legacy inputs have deterministic compatibility routes;
  ambiguous v0.1.35 role layouts fail closed until explicitly migrated.
- systemd units load only the credential roles needed by their process.
- focused and full validation pass without a provider call.

## Non-goals and boundaries

- No Feishu/Futu network call, Base mutation, notification, subscription, or live
  permission probe.
- No release, push, draft PR, deployment, service reload, credential provisioning,
  Secret rotation, or production config edit in the user-authorized local scope.
- No schema, MMF arithmetic, holdings ownership, receipt payload, or event workflow
  changes.
- No per-table credentials, runtime policy engine, hosted secret service, or third
  application identity.

## First-principles decision and direct evidence

Identity is an authority boundary, not presentation metadata. The default Base
client must bind to the Agent identity at construction, while event routing must
bind to the Listener identity before accepting a payload. Current evidence:

- `src/feishu_client.py` defaults to `feishu.bitable.*`.
- both event target factories and adapters also use `feishu.bitable.*`.
- receipt services default to `feishu.conversation.*`.
- the event-listener process constructs `PortfolioService().storage`, so its
  worker performs Base operations through the default client even though ingress
  itself is listener-owned.
- generated event-listener units currently load only the old Bitable credential,
  which is insufficient once Base calls correctly move to Agent.

## Public configuration contract

### Canonical logical keys

```text
feishu.agent.app_id
feishu.agent.app_secret
feishu.agent.open_id
feishu.listener.app_id
feishu.listener.app_secret
```

Non-secret environment overrides:

```text
FEISHU_AGENT_APP_ID
FEISHU_AGENT_OPEN_ID
FEISHU_LISTENER_APP_ID
```

Secret environment names exist only for non-secure local compatibility and
plaintext-shadow detection; production secure mode accepts named encrypted
credentials only:

```text
pm-feishu-agent-app-secret
pm-feishu-listener-app-secret
```

### Compatibility mapping

The only automatic legacy mapping is the pre-v0.1.35 Base writer identity:

```text
feishu.app_id/app_secret -> feishu.agent.*
```

This mapping is semantically safe because those direct keys were consumed by the
default Base API client. It does not supply the required Listener identity.

The following old inputs are detected and reported as migration sources but are
not automatically promoted into a canonical identity:

```text
feishu.bitable.*
feishu.conversation.*
feishu.receipt.*
OM_FEISHU_BOT_*
```

Those names described different authority under previous public contracts and
cannot prove that an app is the new Agent or Listener. If canonical Agent/Listener
keys are absent while any ambiguous old role key is present, validation returns
`ambiguous_legacy_role_configuration` with key names only. There is no
Agent-to-Listener or Listener-to-Agent fallback. Canonical and semantically safe
legacy non-secret candidates must agree; disagreement returns
`conflicting_role_configuration`. In non-secure mode, more than one plaintext
secret source for Agent fails closed as ambiguous rather than silently choosing a
value. Secure mode reads only the new role-named credential files and treats old
plaintext inputs as reported shadows.

The old encrypted credential filenames are not silently reused because their
role names are materially wrong. A future production upgrade must provision both
new role-named files before applying new units; this is an explicit deployment
gate, not an implementation side effect.

## Runtime ownership and data flow

```text
Agent credential
  -> FeishuClient
  -> FeishuStorage/repositories
  -> Base reads and writes
  -> receipt/message senders

Listener credential
  -> HoldingsEventTarget/CashFlowEventTarget
  -> FeishuBitableEventAdapter
  -> subscribe + WebSocket ingress
  -> durable inbox
  -> worker -> FeishuStorage -> Agent credential
```

Event payload `header.app_id` is matched against the Listener target. The worker
never promotes event payload fields to authority and continues to fresh-read the
record through Agent-owned storage.

The default `FeishuClient` no longer reads `feishu.user_token` or
`FEISHU_USER_TOKEN`. In secure mode, either configured source is an
`insecure_identity_override` blocker. Explicit constructor `user_token` remains
available only for isolated tests/non-default callers and never participates in
production config resolution.

## systemd least-privilege matrix

| Unit | Agent | Listener | Reason |
|---|---:|---:|---|
| morning/evening Futu + NAV | yes | no | Base reads/writes and receipts |
| cash-flow scan | yes | no | Base reads/writes and receipts |
| API | yes | no | current API use cases access Base/send receipts |
| quality refresh | yes | no | Base reads only |
| receipt dispatcher | yes | no | message sends only |
| holdings/cash-flow event listener | yes | yes | Listener ingress plus Agent-owned worker Base calls |
| Feishu secure preflight | yes | yes | validates both configured roles |
| transient event subscribe command | no | yes | document subscription only |

## Implementation slices

### S1 — Canonical Agent/Listener resolver

- **Objective**: establish the new public config and encrypted credential contract
  before changing business call paths.
- **Allowed files**: `src/config.py`,
  `src/configuration/feishu_credentials.py`, `src/configuration/__init__.py`,
  `scripts/pm.py`, `tests/test_config.py`, `tests/test_pm_cli.py`.
- **Changes**:
  - add canonical Agent/Listener keys, env mappings, required-key groups,
    role-named credential constants, inspection entries, redaction, and shadow
    detection;
  - implement the exact compatibility mapping above;
  - compare all non-secret candidates, not only canonical-versus-first-legacy;
  - reject ambiguous v0.1.35 Bitable/Conversation/receipt/OM role inputs unless
    canonical Agent and Listener identities are explicitly present;
  - reject ambiguous multi-source plaintext secrets outside secure mode;
  - detect configured user-token identity overrides and fail secure validation;
  - make config doctor require Agent for daily/Futu and both roles for explicit
    secure Feishu preflight;
  - make event status report independent Listener ingress and Agent worker
    credential readiness, with top-level success requiring both.
- **Invariants**: no network; no secret value/path in diagnostics; constructor
  injection remains supported; no cross-role fallback.
- **Tests**:
  - canonical success, safe direct-writer legacy route, ambiguous old-role
    rejection, equal duplicates, conflicting duplicates, missing secure
    credential, plaintext shadow, ambiguous plaintext sources, user-token
    override rejection, redaction, 4096-byte/invalid file behavior;
  - doctor requirements for daily, Futu, and secure preflight;
  - event status covers Listener-only, Agent-only, and both-ready states.
- **Validation**:
  `python3.12 -m pytest -q -p no:cacheprovider tests/test_config.py tests/test_pm_cli.py`
- **Completion signal**: focused suite passes and no runtime consumer has been
  rerouted yet.

### S2 — Runtime identity routing

- **Objective**: bind every call path to the confirmed authority owner.
- **Allowed files**: `src/feishu_client.py`, event target/adapter modules under
  `src/app/` and `src/feishu/`, receipt services under `src/app/`, and their
  directly corresponding tests.
- **Changes**:
  - default `FeishuClient` to Agent App ID/Secret;
  - stop the default client from reading any configured user token while retaining
    explicit constructor injection for isolated callers;
  - default all receipt/message services to Agent App ID/Secret/open_id;
  - default event targets and adapters to Listener App ID/Secret;
  - update missing-config errors and status labels without changing payload or
    workflow behavior.
- **Invariants**: explicit constructor credentials still override config; event
  payload target validation remains exact; storage/repositories remain unchanged;
  Listener credential never reaches `FeishuClient`.
- **Tests**:
  - Base client asks only for Agent keys;
  - every receipt sender asks only for Agent keys;
  - event targets/adapters ask only for Listener keys;
  - combined target validation retains one-listener-app and exact table routing;
  - ingress worker storage path still constructs the Agent-owned client.
  - a no-network Futu/MMF composition regression proves the scheduled
    `FutuBalanceSyncService -> CashService -> HoldingsRepository ->
    FeishuClient.update_record` path resolves Agent and never Listener.
- **Validation**:
  `python3.12 -m pytest -q -p no:cacheprovider tests/test_feishu_client.py tests/test_feishu_bitable_event_adapter.py tests/test_feishu_holdings_event_adapter.py tests/test_holdings_event_service.py tests/test_cash_flow_event_service.py tests/test_futu_sync_receipt_service.py tests/test_nav_history_receipt_service.py tests/test_operation_receipt_outbox_service.py`
- **Completion signal**: focused runtime tests prove the authority matrix without
  external calls.

### S3 — Installer, docs, and migration contract

- **Objective**: make generated deployment assets and operator instructions match
  the runtime authority split.
- **Allowed files**: `scripts/install_linux.py`, `scripts/install.sh`,
  `config.example.yaml`, `README.md`, `docs/deploy-linux.md`,
  `docs/holdings-event-listener-runbook.md`, `docs/runbook.md`, `docs/service.md`,
  repository `SKILL.md` only where it states the old roles,
  `tests/test_install_linux.py`, and direct documentation-contract tests.
- **Changes**:
  - render Agent/Listener config blocks and new credential names;
  - detect canonical and all legacy plaintext Secret keys without reading values;
  - render the systemd matrix above and verify both required encrypted files before
    any apply write;
  - update preflight, provisioning, rotation, migration, rollback, subscription,
    and canary instructions;
  - explicitly stop an upgrade until the new encrypted credential files and
    non-secret role mapping are verified.
- **Invariants**: installer never receives, prints, copies, decrypts, or silently
  deletes a Secret; production actions remain separately authorized.
- **Tests**:
  - exact unit credential grants and no excess credential;
  - new config template and plan metadata;
  - capability/presence checks happen before writes;
  - old plaintext key names are detected, values never appear;
  - docs contain the two-identity authority table and explicit migration stop.
- **Validation**:
  `python3.12 -m pytest -q -p no:cacheprovider tests/test_install_linux.py tests/test_pm_cli.py`
- **Completion signal**: generated assets and docs agree with runtime routing.

## Aggregate validation

After all slices and their review loops:

```text
PYTHONPYCACHEPREFIX=/tmp/pm_agent_listener python3.12 -m pytest -q -p no:cacheprovider
PYTHONPYCACHEPREFIX=/tmp/pm_agent_listener python3.12 -m compileall -q src scripts tests
python3.12 -m ruff check <changed Python files>
bash -n scripts/install.sh
git diff --check
```

Expected: full tests pass; compileall, scoped Ruff, shell syntax, and diff check
pass. Existing unrelated worktree files remain unstaged and unchanged.

## Documentation decision

Documentation changes are required because the current public role table,
credential names, subscription command, preflight contract, and migration
sequence all assert the wrong authority topology.

## Risks and mitigations

- **Version skew**: new code under old units lacks new credential names. Mitigation:
  fail closed and require provisioning/preflight before apply or restart.
- **Legacy semantic ambiguity**: v0.1.35 role names cannot prove the new app
  authority. Mitigation: require explicit canonical Agent/Listener configuration;
  only the old direct Base-writer identity may alias Agent.
- **Identity bypass**: a configured user token could avoid the Agent tenant token.
  Mitigation: default clients ignore it and secure validation rejects its presence.
- **Listener worker starvation**: Listener-only units cannot fresh-read. Mitigation:
  listener process receives both credentials while the adapter itself consumes
  Listener only.
- **Credential overgrant**: broad units may retain both credentials. Mitigation:
  assert the explicit matrix in installer tests.
- **Live permission uncertainty**: local tests cannot prove tenant scopes/Base
  access. Owner: later deployment canary with separate authorization.

## Why this is not overdesigned

The plan introduces no new application identity and no general authorization
framework. It renames the two existing roles to match actual authority, changes
the existing resolver defaults, and adjusts generated credential grants. Three
slices separate public config, runtime behavior, and deployment assets so each
can be reviewed without inventing future abstractions.

## Completion report format

- changed role/config/runtime/unit contracts;
- focused and aggregate validation results;
- review finding status and residual risks;
- local commit/branch state;
- explicit statement that no provider call, release, deployment, Secret rotation,
  service change, or real write occurred;
- next authorized boundary.
