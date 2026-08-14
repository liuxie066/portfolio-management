# Gateflow Goal Confirmation — NAV Valuation Evidence Replay

- Work unit: `nav-valuation-evidence-replay`
- Base: `origin/main@814d92070f3ac688513c2d643ae7d6843b723760`
- Branch: `fix/nav-valuation-evidence-replay`
- Decision: confirmed by the user on 2026-08-14
- Current gate: `plan`
- Next entry point: create and review the implementation plan

## Goal and motivation

Make a scheduled NAV valuation recoverable when price collection and holdings
normalization succeeded but the later cash-flow effect gate refused the write.
The recovery must reuse the original valuation facts, rerun the fresh holdings
and cash-flow gates, recalculate shares/performance from current official data,
and persist NAV plus the holdings snapshot through the existing canonical write
path.

For the already-failed `lx/2026-08-13` run, provide a guarded historical
preparation path because that release did not retain its normalized valuation.

## Direct evidence

- `AccountNavRecorderService.record()` builds the official normalized valuation
  before `build_cash_flow_dataset()`, but its `CashFlowDatasetRefusal` branch
  returns only the refusal and discards the completed valuation.
- `NavRecordService` already owns NAV share/performance calculation, finality,
  NAV persistence, and holdings-snapshot exact-set persistence. Recovery should
  feed this path instead of creating another NAV writer.
- `NormalizedValuationSnapshot.canonical_payload()` already provides a stable,
  digestible representation of valuation rows, price evidence, holdings
  provenance, warnings, and components.
- The current account recorder rejects caller snapshots whenever official
  holdings preflight is active, so replay needs a narrow trusted rehydration
  contract rather than reopening arbitrary snapshot injection.
- The failed production receipt was truncated and cannot safely carry the full
  valuation payload. A digest-addressed server-side artifact is therefore the
  smallest durable recovery boundary.

## Success signals

- A cash-flow-gated daily NAV failure stores exactly one immutable valuation
  evidence artifact and returns a server-side `valuation_ref`.
- The artifact binds account, NAV date, source run, normalized valuation digest,
  holdings digest, cash-flow financial fingerprint, and effect-store revision.
- `daily-job --account ... --nav-date ... --valuation-ref ...` rejects arbitrary
  paths and JSON, refetches no prices, rechecks fresh holdings and cash-flow
  authority, and refuses any account/date/digest/fingerprint mismatch.
- A valid replay recalculates shares/performance and uses the existing
  `record_nav` plus holdings-snapshot exact-set path with explicit replay
  finality/provenance.
- The historical preparation command defaults to preview, pins target-date
  exchange closes, fund NAV fact dates no later than the target date, and the
  explicitly supplied original FX observation. Writing requires both a digest
  confirmation and an explicit write flag.
- Focused tests, the full test suite, and static checks pass without touching
  unrelated untracked files.

## Non-goals and scope boundary

- No second NAV table writer, repair-row insertion path, schema migration,
  background retry service, artifact registry database, or new dependency.
- No automatic write immediately after a cash-flow effect is resolved.
- No arbitrary local file/JSON replay input and no reuse of an artifact across
  accounts or NAV dates.
- No change to cash-flow aggregation, NAV share mathematics, snapshot
  compensation, overwrite defaults, Futu synchronization, or daily-job
  multi-account scheduling.
- No release, remote upgrade, service restart, production artifact generation,
  production NAV write, or notification in this work unit.

## Overengineering decision

Use one versioned JSON contract, one local immutable store, and the existing NAV
write pipeline. Do not add a database, plugin system, queue, generic evidence
framework, or alternate persistence service. Historical sourcing is limited to
the providers and asset classes required by the confirmed recovery case.

## Blocking open questions

None. The user confirmed the design, authorized implementation, and separately
confirmed creation of the isolated work branch.
