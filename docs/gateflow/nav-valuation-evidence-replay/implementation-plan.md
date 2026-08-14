# Gateflow Implementation Plan — NAV Valuation Evidence Replay

- Work unit: `nav-valuation-evidence-replay`
- Base: `origin/main@814d92070f3ac688513c2d643ae7d6843b723760`
- Branch: `fix/nav-valuation-evidence-replay`
- Design source: confirmed user plan, Kimi design review, incident evidence, and
  current repository behavior
- Current gate: `plan review`
- Next entry point: adversarial plan review
- Work unit status: `planned`

## Goal, motivation, and success signal

Retain a completed normalized valuation when the later cash-flow gate blocks a
scheduled NAV, then replay that exact valuation only after fresh holdings and
cash-flow authority checks pass. Recalculate history-dependent NAV fields and
persist through the existing canonical NAV plus holdings-snapshot exact-set
path.

The work is complete when a blocked run returns a loadable immutable
`valuation_ref`, a bound single-account daily-job replay performs no price
fetch, mismatch cases fail closed, and the `lx/2026-08-13` legacy case can be
prepared from dated market facts under a two-step preview/apply contract.

## Non-goals and scope boundary

- No alternate NAV writer, database/schema change, queue, automatic retry,
  artifact listing API, retention service, or general evidence framework.
- No change to NAV math, cash-flow math, holdings reconciliation policy,
  compensation, overwrite semantics, or normal daily-job scheduling.
- No caller-provided path or raw JSON replay.
- No release, deploy, remote mutation, NAV replay, or notification.

## First-principles judgment and direct code evidence

- `AccountNavRecorderService.record()` owns the point where a validated holdings
  snapshot, a `NormalizedValuationSnapshot`, and the refused cash-flow dataset
  coexist. Persisting evidence in its typed refusal branch loses no information
  and does not burden successful runs.
- `NormalizedValuationSnapshot.canonical_payload()` and `digest` already own the
  valuation representation and integrity identity. Rehydration belongs beside
  that contract and must issue official eligibility only after complete payload
  and digest validation.
- `DailyNavJobService` already owns duplicate checks, global holdings preflight,
  finality, and one-account orchestration. A `valuation_ref` is a narrow input to
  that route; replay must not enter `nav-repair`.
- `NavRecordService.record_nav()` already reloads NAV history, rebuilds the
  cash-flow summary, calculates shares/performance, and executes the exact-set
  holdings snapshot transition. It remains the only persistence path.
- Existing fixed-price helpers cover CASH/MMF/crypto with supplied FX. Only the
  target-date OpenD close and Eastmoney historical fund NAV queries are absent.

## Contract and state changes

### Immutable evidence contract

Add `pm.nav_valuation_evidence.v1` with:

- `account`, `nav_date`, `source_run_id`, `snapshot_time`, `captured_at`;
- `holdings_digest`;
- `cash_flow_financial_fingerprint` and `source_effect_store_revision`;
- normalized valuation canonical payload and its digest;
- preparation provenance (`cash_flow_gate_failure` or `historical_recovery`);
- artifact digest over every field except the digest itself.

The public reference is
`nav-valuation-evidence:v1:<encoded-account>:<nav-date>:<artifact-digest>`.
The store resolves only this grammar beneath
`.data/nav_valuation_evidence/<account>/<nav-date>/<digest>.json`, writes with
exclusive create plus fsync, treats an identical existing artifact as
idempotent, and rejects collision/tamper/scope/digest failures.

### Trusted rehydration

Add a private `NormalizedValuationSnapshot` evidence constructor that rebuilds
rows/components from the canonical payload, verifies contract version,
`official_eligible=true`, `source=valuation_service`, exact canonical equality,
and the caller-supplied valuation digest before restoring official eligibility.
It is called only after the store validates an immutable server-side reference.

### Replay state transition

`daily-job --valuation-ref` requires exactly one explicit `--account` and one
explicit non-`auto` `--nav-date`; it rejects `--accounts`, Futu sync flags, and
cross-scope references.

For that account:

1. Run the existing duplicate/existing-row and global holdings prechecks.
2. Run the existing fresh account holdings preflight.
3. Load the server-side evidence and require the fresh normalized holdings
   digest to equal the artifact digest.
4. Build a fresh official cash-flow dataset. Require its financial fingerprint
   to equal the artifact fingerprint and require its current gate to pass.
   Effect-store revision may differ because resolving the blocker creates a new
   revision; both source and replay revisions are retained as provenance.
5. Rehydrate the normalized valuation without any price provider call, project
   its compatibility valuation/report snapshot, and call the existing
   `record_nav` path.
6. Write finality as `writer=daily-nav-job`, `status=final`,
   `write_reason=canonical_daily_nav_replay`, with valuation ref, source run,
   valuation digest, and source/current effect revisions in finality provenance.

Normal daily-job behavior is unchanged when `valuation_ref` is absent.

### Historical preparation

Add `pm nav evidence prepare-historical` as a direct local command. Required
scope/evidence inputs are account, NAV date, source run id, expected holdings
digest, expected cash-flow fingerprint, source effect-store revision,
valuation-as-of timestamp, USDCNY, and HKDCNY. The command:

1. runs the existing holdings preflight in dry-run mode and requires the
   expected holdings digest;
2. builds a fresh cash-flow dataset, requires the expected financial
   fingerprint, and requires the current gate to pass;
3. obtains exact target-date unadjusted daily closes from OpenD for A/H/US and
   exchange funds;
4. obtains the latest Eastmoney fund NAV whose fact date is no later than the
   target date for OTC funds;
5. uses existing fixed quote helpers with the explicitly supplied FX rates for
   CASH/MMF/crypto;
6. constructs the official valuation through `ValuationService` with the frozen
   price snapshot and validated holdings;
7. previews the full artifact and digest by default. Persistence additionally
   requires `--write --confirm --expected-digest <preview digest>`.

Every price payload records provider, fact date, retrieval timestamp, native
currency, supplied FX rate, and CNY price. Missing or date-ineligible facts fail
the whole preparation.

## Affected files and modules

### Slice S1 — Evidence capture and canonical replay

- `src/domain/snapshot_contracts.py`
- `src/app/nav_finality.py`
- `src/app/nav_valuation_evidence_service.py` (new)
- `src/app/account_nav_recorder_service.py`
- `src/app/portfolio_read_service.py`
- `src/app/daily_account_nav_service.py`
- `src/app/daily_nav_job_service.py`
- `src/app/__init__.py`
- `src/service/application.py`
- `src/service/client.py`
- `src/service/http.py`
- `scripts/pm.py`
- `tests/test_nav_valuation_evidence_service.py` (new)
- `tests/test_daily_nav_services.py`
- `tests/test_pm_cli.py`
- `tests/test_service_http.py`

### Slice S2 — Historical evidence preparation and operator documentation

- `src/app/nav_valuation_evidence_service.py`
- `src/service/application.py`
- `scripts/pm.py`
- `tests/test_nav_valuation_evidence_service.py`
- `tests/test_pm_cli.py`
- `docs/nav-valuation-evidence-replay.md` (new)
- `README.md`

Gateflow artifacts may be changed in every slice. No other file is allowed
without first updating this plan and re-running plan review.

## Implementation slices

### S1 — Evidence capture and canonical replay

- Objective: make future cash-flow-gated valuations durable and replayable
  without refetching prices.
- Prerequisite: accepted plan commit.
- Exact changes: add the immutable store and trusted rehydration; save only for
  `CASH_FLOW_DATASET_BLOCKED` or `CASH_FLOW_EFFECT_GATE_INCOMPLETE` after a
  normalized valuation exists and after rechecking dataset account/date/run id,
  financial fingerprint, and effect revision; never save scope/integrity
  refusals; extract the existing `PortfolioReadService` snapshot projection so
  normal and replay inputs share one owner; thread `valuation_ref` through
  CLI/HTTP/service/daily-job/account runner; enforce single-account scope and
  mismatch checks; add replay finality provenance.
- Invariants: successful normal runs write no artifact; replay never accepts a
  path/JSON; fresh holdings and cash-flow gates remain mandatory; source effect
  revision is audited but is not required to equal the new passing revision;
  existing NAV persistence and snapshot compensation remain untouched; a
  scope/integrity mismatch never creates a recovery reference.
- Non-goals: historical market retrieval.
- Validation:
  `python3.12 -m pytest -q tests/test_nav_valuation_evidence_service.py tests/test_daily_nav_services.py tests/test_pm_cli.py tests/test_service_http.py`
  must prove round-trip/tamper/idempotency, gate-only capture, scope mismatch
  non-capture, shared normal/replay report projection, no-price replay, all
  mismatch refusals, normal-path compatibility, and CLI/API plumbing.
- Completion signal: focused tests and static compile pass; slice review has no
  unresolved accepted finding.
- Stop condition: any evidence that replay can bypass fresh gates, cross scope,
  refetch prices, or use a noncanonical writer.

### S2 — Historical evidence preparation

- Objective: create a safe evidence artifact for the one legacy run that could
  not have saved one.
- Prerequisite: accepted S1 commit.
- Exact changes: add injected/testable OpenD and Eastmoney retrieval helpers,
  fixed quote construction from supplied FX, official valuation preparation,
  preview/digest confirmation, CLI parser/handler, and runbook.
- Invariants: target-date exact close for exchange assets; fund fact date must be
  `<= nav_date`; no current quote fallback; no hidden FX fetch; preview performs
  no artifact write; write requires confirmation plus exact digest.
- Non-goals: generic historical pricing API, multi-day backfill, caching, retry
  queue, or alternate providers.
- Validation:
  `python3.12 -m pytest -q tests/test_nav_valuation_evidence_service.py tests/test_pm_cli.py`
  must cover symbol mapping, fact-date enforcement, price construction, digest
  mismatch, preview purity, and idempotent write.
- Completion signal: focused tests, documentation checks, and slice review pass.
- Stop condition: any target can silently use a current price, stale future fund
  NAV, implicit FX, or a mismatched holdings/cash-flow source.

## Full validation and docs decision

- `python3.12 -m compileall -q src scripts`
- `python3.12 -m pytest -q -p no:cacheprovider`
- `git diff --check`
- Review the CLI `--help` output for both commands.
- Documentation is required because recovery has an operator safety contract;
  add a focused runbook and a concise README link.

## Risks and residual-risk classification

- OpenD and Eastmoney availability during historical preparation: accepted
  operator-time dependency; fail closed, no fallback.
- Local artifact loss: assigned to operations backup of the existing `.data`
  runtime root; no new replication system in this work unit.
- Holdings genuinely changed after the failed run: explicit failure requiring
  a separate evidence/repair decision, not bypassed here.
- Multi-host concurrent writers: existing NAV lock limitation, assigned to the
  existing persistence boundary and not expanded by replay.
- Historical provider contract drift: covered by injected response tests and
  explicit parse errors; a provider replacement is a later work unit.

No residual risk is unclassified and there are no blocking open questions.

## Why this is not overdesigned

The implementation adds one JSON store and one replay input to the existing
writer. It reuses the normalized valuation contract, holdings gate, cash-flow
gate, NAV calculator, snapshot exact-set persistence, installed HTTP client,
and existing fixed-price helpers. The historical adapter implements only the
two providers needed by the confirmed incident and no extensibility layer.

## Completion report format

- changed behavior and exact files;
- focused/full validation results;
- review findings and final status;
- docs status;
- residual risks and owners;
- current Gateflow gate and next authorized entry point;
- explicit reminder that release, upgrade, and production replay were not run.
