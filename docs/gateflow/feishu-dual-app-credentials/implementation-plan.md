# Implementation Plan — Feishu Dual-App Credentials

- Gate: `plan`
- Work unit: `feishu-dual-app-credentials`
- Base: `origin/main@59625b0d6c666da338c0a520e85a221932846949`
- Goal confirmation: `docs/gateflow/feishu-dual-app-credentials/goal-confirmation.md`
- Status: `accepted after plan re-review 20260802-011016`

## Goal, motivation, and success signal

Make the existing two Feishu application identities explicit and keep both App
Secrets out of production YAML and `EnvironmentFile` storage:

- **Bitable application**: Base reads/writes, file subscription, and record-change
  long connection.
- **Conversation application**: workflow receipt delivery to one configured
  `open_id`.

The implementation succeeds when every relevant call path uses its canonical
role, systemd units receive only required secrets as credentials, secure mode
rejects plaintext fallbacks, generated artifacts contain no App Secret, and all
existing business/workflow tests remain green.

## First-principles judgment and code evidence

Two identities are sufficient. The callback is only a trigger: workers fresh-read
and write through `FeishuClient`, so an event-only third app would force users to
configure a third identity without providing another business boundary. The
Bitable app should own both the external event protocol and table API authority.

Current ownership:

- `src/feishu_client.py`: Base API via `feishu.app_*`.
- `src/app/holdings_event_service.py`, `src/app/cash_flow_event_service.py`,
  `src/feishu/bitable_event_adapter.py`, and the compatibility holdings adapter:
  event routing/subscription/connection via the same `feishu.app_*`.
- receipt service classes under `src/app/*receipt_service.py`: delivery via
  `feishu.receipt.*`.
- `src/config.py`: environment-first resolver with YAML fallback, but no
  credential-file source or conflict detection.
- `scripts/install_linux.py`: copies `OM_FEISHU_BOT_APP_SECRET` into a shared
  `EnvironmentFile`, then attaches that file to every service.

## Public configuration contract

### Canonical logical keys

```text
feishu.bitable.app_id
feishu.bitable.app_secret
feishu.conversation.app_id
feishu.conversation.app_secret
feishu.conversation.open_id
```

Only the two `*.app_secret` keys are secret-valued. App IDs and `open_id` may be
stored in the operator config or non-secret environment, while still being
redacted by operator-facing inspection for privacy.

### Runtime credential names

```text
pm-feishu-bitable-app-secret
pm-feishu-conversation-app-secret
```

When systemd sets `CREDENTIALS_DIRECTORY`, the resolver reads those exact files.
It accepts at most 4096 bytes of UTF-8 with one optional final newline, rejects
missing, empty, NUL-containing, non-regular, symlinked, or 4097-byte-and-larger
values, and never includes the path or value in an exception or inspection
payload.

### Compatibility aliases

```text
feishu.app_id                    -> feishu.bitable.app_id
feishu.app_secret                -> feishu.bitable.app_secret
feishu.receipt.app_id            -> feishu.conversation.app_id
feishu.receipt.app_secret        -> feishu.conversation.app_secret
feishu.receipt.open_id           -> feishu.conversation.open_id
OM_FEISHU_BOT_*                  -> conversation compatibility inputs
```

Canonical and legacy **non-secret** inputs are compared when both are present.
Equal App IDs/open IDs are accepted and reported as redundant; different values
produce a redacted configuration conflict before any SDK client or network
request is constructed. Secret values are never read merely to compare aliases:
once a credential is selected, legacy secret sources are detected only by key
presence and reported as `plaintext_shadow_detected`. There is no
Bitable-to-conversation or conversation-to-Bitable fallback.

### Source order and secure mode

For the two canonical App Secret keys:

1. explicit constructor argument (existing test/integration seam);
2. named systemd credential;
3. canonical environment value;
4. canonical YAML value;
5. same-role legacy environment/YAML compatibility value.

`PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1` permits only source 1 or 2. All generated
production systemd units set this flag. When a credential exists, an older
plaintext source is not read or compared and does not block the migration canary;
inspection emits the redacted `plaintext_shadow_detected` warning until the
separately authorized cleanup. When no credential exists, secure mode refuses to
fall back to a plaintext source and returns `insecure_secret_source`. Non-secure
mode exists only for tests, local development, and the pre-migration release
window; config inspection warns whenever a plaintext secret is selected.

## Configuration state and error semantics

The resolver returns source metadata without secret material:

```text
credential:pm-feishu-bitable-app-secret
env:FEISHU_BITABLE_APP_SECRET
file:<operator-config-path>
legacy-env:FEISHU_APP_SECRET
legacy-file:<operator-config-path>
```

New typed configuration errors distinguish:

- `missing_secure_credential`;
- `insecure_secret_source`;
- `conflicting_role_configuration`;
- `invalid_credential_file`.

`plaintext_shadow_detected` is a warning, not an error, only when a valid secure
credential has already been selected. The resolver detects the legacy key without
loading its value.

`config inspect`, `config doctor`, and `events status` convert these to redacted
structured issues instead of tracebacks. Mutating/network commands fail before
client construction.

## systemd and installer contract

The installer never creates, encrypts, decrypts, copies, prints, or accepts a
secret value. It renders references to operator-provisioned encrypted credential
files under systemd's credential store and verifies presence only by credential
name/regular-file metadata.

Credential grants are least-privilege by unit:

| Unit | Bitable | Conversation | Reason |
|---|---:|---:|---|
| morning NAV/Futu service | yes | yes | reads/writes tables and sends immediate receipts |
| evening Futu service | yes | yes | writes holdings and sends immediate receipts |
| cash-flow scan service | yes | yes | reads/writes tables and immediately dispatches pending effect receipts |
| API service | yes | yes | public facade can enter current Futu/NAV use cases that send receipts |
| quality refresh service | yes | no | reads PM facts only |
| receipt dispatcher | no | yes | sends frozen receipts only |
| Bitable event listener | yes | no | subscribes/listens, fresh-reads, writes allowed generated fields, enqueues receipts |

Every credential-bearing unit includes:

```text
Environment=PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1
LoadCredentialEncrypted=<credential-name>
```

The shared `EnvironmentFile` remains for non-secret settings, but the new
installer stops importing `OM_FEISHU_BOT_APP_SECRET`. Existing plaintext secret
lines are detected by key name and reported as migration shadows; they are not
read, compared, logged, or silently deleted. A missing secure credential is the
blocker. The runbook requires explicit operator removal only after both encrypted
credentials pass preflight and the new release can read them.

Before any apply-mode write, the installer verifies that `systemd-creds` exists
and uses `systemd-analyze verify` against a temporary rendered unit to prove the
installed service manager accepts the exact `LoadCredentialEncrypted` syntax.
This capability probe is preferred over guessing support from a version string.
Unsupported hosts return a redacted blocker before config, env, launcher, or unit
files are changed. Dry-run reports the required capability without claiming it
was verified. A second verification of the final installed units remains a
deployment gate; local macOS tests assert rendering and probe sequencing but do
not claim runtime systemd verification.

Because direct shell commands do not receive `CREDENTIALS_DIRECTORY`, production
subscription/status preflight is executed through a generated, disabled
`portfolio-feishu-preflight.service` oneshot. It loads both credentials, runs
`pm config doctor --require-secure-feishu --json` followed by local
`pm events status --json`, and performs no Feishu mutation. Subscription remains
a separately confirmed transient operation documented with a bounded
`systemd-run` invocation that loads only the Bitable credential.

## Migration and rollback state machine

```text
legacy_plaintext
  -> code_installed_legacy_mode
  -> encrypted_credentials_provisioned
  -> secure_preflight_passed
  -> services_switched_secure
  -> listener_subscription_verified
  -> controlled_canary_passed
  -> plaintext_removed
```

Rules:

- No step automatically advances the next step.
- A failed preflight leaves old services/config untouched.
- Before `services_switched_secure`, rollback is the previous unit/release.
- After the switch but before plaintext removal, rollback may restore previous
  units without reconstructing secret values.
- Plaintext removal is a separately authorized destructive production action.
- After plaintext removal, rollback keeps the credential-capable release/unit
  contract; rolling back to a pre-credential release requires rotation and is
  not an automatic recovery path.
- Real credential provisioning, subscription, canary, deletion, release, and
  remote upgrade remain deployment work, not implementation actions here.

## Implementation slices

### S1 — Canonical role and credential resolver

- **Objective**: implement the two-role config contract and redacted failure
  semantics without changing any Feishu business call path yet.
- **Allowed files**: `src/config.py`, a focused new module under `src/configuration/`
  only if `src/config.py` cannot keep credential IO isolated, `tests/test_config.py`,
  `tests/test_pm_cli.py`.
- **Changes**:
  - add canonical keys, non-secret same-role conflict detection, credential-file
    reading, secure-mode enforcement, shadow-key detection without reading shadow
    secret values, and redacted inspection/doctor issues;
  - retain legacy direct key lookup for callers until S2;
  - add `--require-secure-feishu` to `pm config doctor`;
  - never include secret values or credential filesystem paths in output.
- **Invariants**: no network access; constructor injection remains possible;
  same-role aliases only; App ID/open_id conflicts fail closed; selected secure
  secrets do not cause legacy secret values to be loaded for comparison.
- **Tests**: credential precedence, newline handling, empty/NUL/4096/4097/symlink
  rejection, missing secure credential, plaintext fallback rejection in secure
  mode, credential plus different legacy secret succeeding with a shadow warning,
  legacy warning outside secure mode, non-secret equal duplicate and conflicting
  duplicate, redaction of every error/inspect payload.
- **Validation**:
  `python3.12 -m pytest -q -p no:cacheprovider tests/test_config.py tests/test_pm_cli.py`.
- **Stop condition**: any output contains fixture secret bytes or existing config
  inspection schema regresses without an explicit compatibility field.

### S2 — Move all Feishu call paths to the two canonical roles

- **Objective**: make role ownership executable and prove there is no third
  event identity.
- **Allowed files**: `src/feishu_client.py`, the Bitable event target/adapter
  modules, receipt service modules, `scripts/pm.py`, and their focused tests.
- **Changes**:
  - Base client and all event targets/adapters use `feishu.bitable.*`;
  - all receipt render/send services use `feishu.conversation.*`;
  - `events status` reports the Bitable role only and remains read-only;
  - missing/conflicting credentials fail before SDK construction;
  - compatibility adapter and combined adapter retain identical wire protocol,
    deduplication, callback, and subscription behavior.
- **Non-goals**: no event payload, workflow, write authority, retry, receipt body,
  or Feishu request-map change.
- **Tests**: exact role-key assertions, cross-role non-fallback, same Bitable App ID
  across API/events, constructor override, missing secure source, and unchanged
  subscription request maps.
- **Validation**:
  `python3.12 -m pytest -q -p no:cacheprovider tests/test_feishu_client.py tests/test_feishu_bitable_event_adapter.py tests/test_feishu_holdings_event_adapter.py tests/test_pm_cli.py tests/test_nav_history_receipt_service.py tests/test_futu_sync_receipt_service.py tests/test_holdings_workflow_service.py tests/test_operation_receipt_outbox_service.py`.
- **Stop condition**: any business test requires a third app identity or a role
  silently resolves the other role's secret.

### S3 — systemd credential wiring and non-secret installer output

- **Objective**: make Linux service deployment consume encrypted credentials and
  stop copying the conversation secret into `EnvironmentFile`.
- **Allowed files**: `scripts/install_linux.py`, `scripts/install.sh`,
  `tests/test_install_linux.py`, installer-focused docs.
- **Changes**:
  - render exact per-unit `LoadCredentialEncrypted` grants and secure-mode flag;
  - render the disabled preflight oneshot;
  - import only non-secret conversation App ID/open_id compatibility values;
  - detect and report legacy secret-key locations by key name without reading
    them into plan/result payloads;
  - run systemd credential capability detection before any apply-mode write and
    report unsupported hosts without partial installation;
  - keep install dry-run side-effect free and event listener disabled by default.
- **Tests**: exact unit grant matrix, no over-grant, no App Secret in config/env/
  plan/unit output, preflight disabled/no network mutation, partial source import,
  idempotent re-render, legacy blocker reporting, and supported/unsupported
  systemd capability preflight before writes.
- **Validation**:
  `python3.12 -m pytest -q -p no:cacheprovider tests/test_install_linux.py`.
- **Stop condition**: installer accepts a secret CLI argument, materializes a
  secret, deletes a legacy source, or enables any service implicitly.

### S4 — Documentation, aggregate verification, and migration evidence

- **Objective**: make the two-app setup and separately authorized migration
  executable without exposing a secret.
- **Allowed files**: README and Feishu/deployment runbooks plus focused docs tests.
- **Changes**:
  - document the two roles, exact permissions, encrypted-credential names,
    preflight, subscription, canary, rollback, rotation, and explicit plaintext
    cleanup boundary;
  - label legacy keys as migration-only;
  - state that the previously disclosed secret must be rotated, without recording
    it or any identifier in artifacts.
- **Tests/validation**:
  - docs assertions in `tests/test_install_linux.py`;
  - `rg` secret-name/value-pattern audit limited to tracked artifacts;
  - `git diff --check`;
  - full `python3.12 -m pytest -q -p no:cacheprovider`.
- **Stop condition**: docs imply that install, release, subscription, service
  activation, plaintext deletion, or remote upgrade are the same authorization.

## Affected files and ownership

Expected ownership is limited to:

- configuration: `src/config.py` and focused configuration tests;
- role consumers: `src/feishu_client.py`, Bitable event modules, receipt services,
  `scripts/pm.py`, and focused tests;
- deployment: `scripts/install_linux.py`, `scripts/install.sh`, unit-render tests;
- documentation: README and existing Feishu/Linux runbooks;
- Gateflow/review artifacts for this work unit.

No holdings/cash-flow domain, repository mutation, NAV calculation, SQLite schema,
or Feishu request payload is in scope.

## Validation and expected assertions

Required before completion:

1. focused S1-S3 suites pass;
2. full suite passes with Python 3.12;
3. `git diff --check` passes;
4. tracked-file search finds no real credential or new secret fixture outside
   tests, and rendered installer outputs contain no fixture secret;
5. config and unit tests prove exact two-role routing and per-unit least privilege;
6. no live Feishu, systemd, release, or remote mutation is performed.

## Documentation decision

Documentation changes are required because setup, migration, rollback, and secret
handling are user/operator-visible contracts. Existing event and deployment
runbooks are updated rather than creating a second competing runbook.

## Why this is not overengineered

The work adds one role resolver boundary and uses the Linux service manager's
existing credential delivery mechanism. It does not add a database, network
secret service, plugin system, generic provider registry, tenant model, or third
Feishu app. Compatibility exists only to migrate current installations; secure
mode prevents it from becoming the production steady state.

## Risks and residual-risk destinations

- systemd encrypted credentials may be host-bound. Cross-host backup/restore is
  assigned to a later deployment/secret-recovery work unit; this implementation
  does not claim portability.
- Hosted multi-user secret storage is assigned to the future product platform;
  the canonical role contract is stable, but no cloud adapter is built now.
- Existing production plaintext removal requires separate authorization and a
  fresh read-only migration preflight.
- Feishu permission correctness and live subscription health require a controlled
  deployment canary and are not inferable from local tests.

## Open questions

None for local implementation. Release, provisioning, and remote apply remain
separate authorization boundaries.

## Completion report format

The final report will list accepted commits, changed role/storage contracts,
focused/full validation, review finding status, documentation, and residual risks.
It will explicitly state that no real secret, release, subscription, service
activation, plaintext deletion, or remote upgrade occurred.
