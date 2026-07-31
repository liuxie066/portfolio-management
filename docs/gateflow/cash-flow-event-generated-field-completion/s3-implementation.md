# Gateflow S3 Implementation — Combined Runtime and Operator Contract

- Gate: `implementation`
- Work unit: `cash-flow-event-generated-field-completion`
- Slice: `S3`
- Plan checkpoint: `16ed0d5`
- Status: `implemented; pending code review`

## Objective and outcome

Compose the accepted holdings and cash-flow event paths into one future runtime
entry point while preserving every external-action boundary.

Implemented outcome:

- new `pm events status|subscribe|listen` commands;
- read-only status for both target identities and both durable inboxes, without
  constructing an SDK client or claiming remote health;
- exact target-registry validation before subscribe SDK work or listener worker
  startup;
- one shared Bitable adapter, one long connection, and local fan-out to the two
  exact table-specific inboxes;
- one operation-state store and one Feishu storage instance shared by both
  workers, including the cash-flow completion/terminal-receipt policy;
- coordinated shutdown that signals and joins both workers;
- the existing `portfolio-holdings-event-listener.service` name retained while
  its future-release template now executes the combined command;
- existing `pm holdings events ...` commands preserved unchanged;
- operator docs updated for file-level subscription, table-level routing,
  deterministic CNY completion, strict foreign FX evidence, and the separate
  CASH holding-effect authority.

## Safety boundaries

- `status` reads local configuration/inbox evidence only.
- `subscribe` and `listen` require explicit `--confirm`.
- Target collision is reported by status and raises before adapter construction,
  SDK access, storage construction, or worker startup on mutation commands.
- This implementation does not run subscription, start a live listener, install
  a unit, enable a service, publish, release, or deploy.
- The installer continues to generate the legacy-named unit disabled by default;
  enablement remains a separately explicit installer flag.
- Receipt delivery remains owned by the existing receipt dispatcher timer.

## Validation

Focused/regression tests:

```text
116 passed in 1.02s
```

Coverage includes parser confirmation guards, local-only status, target
collision refusal, exact fan-out including unknown tables, shared state
injection, both worker joins, same/multi-Base adapter behavior, legacy holdings
adapter/inbox regression, unit rendering, and documentation boundaries.

Static validation:

```text
ruff: All checks passed!
git diff --check: passed
```

## Residual risks

- The official SDK long connection and Base permissions need a separately
  authorized release/deployment canary.
- The systemd unit name remains holdings-specific for upgrade compatibility;
  its description and runbook explicitly state the combined scope.
- A running older installed unit will not change until a future controlled
  upgrade/restart; no runtime state was changed here.

## Next entry point

`code review`

