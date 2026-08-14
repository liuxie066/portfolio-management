# Gateflow S1 Implementation — Evidence Capture and Canonical Replay

- Gate: `implementation`
- Work unit: `nav-valuation-evidence-replay`
- Slice: `S1`
- Artifact path: `docs/gateflow/nav-valuation-evidence-replay/s1-implementation.md`
- Current gate: `slice completion`
- Next entry point: create the protected S1 commit
- Completion status: implementation and re-review complete

## Scope and changed files

- Added strict canonical valuation rehydration in
  `src/domain/snapshot_contracts.py`.
- Added finality provenance in `src/app/nav_finality.py`.
- Added immutable digest-addressed storage in
  `src/app/nav_valuation_evidence_service.py` and exported it from
  `src/app/__init__.py`.
- Added gate-only evidence capture and fresh-gate replay in
  `src/app/account_nav_recorder_service.py`.
- Reused one snapshot projection owner through
  `src/app/portfolio_read_service.py`.
- Threaded `valuation_ref` through daily account/job, CLI, service client,
  application service, and HTTP request contracts.
- Added focused contract, orchestration, CLI, and HTTP coverage.

## Decisions and invariants

- Evidence is saved only for a daily-nav-job typed refusal with reason
  `CASH_FLOW_DATASET_BLOCKED` or `CASH_FLOW_EFFECT_GATE_INCOMPLETE`, strict
  account/date/run scope, and nonblank financial fingerprint/effect revision.
- Evidence uses exclusive creation, fsync, deterministic canonical JSON, and an
  account/date/digest-only reference parser; no path or raw JSON is accepted.
- Replay requires official holdings preflight and exact holdings digest, then a
  fresh cash-flow dataset and exact financial fingerprint.
- Source/current effect revisions are audited but may differ.
- Replay builds the report snapshot from the existing `PortfolioReadService`
  projection and calls the existing NAV/snapshot persistence path.
- Normal daily jobs keep `canonical_daily_nav_job`; replay uses
  `canonical_daily_nav_replay` with bound provenance.

## Validation

- `python3.12 -m compileall -q src scripts` — passed.
- `python3.12 -m pytest -q -p no:cacheprovider tests/test_nav_valuation_evidence_service.py tests/test_daily_nav_services.py tests/test_pm_cli.py tests/test_service_http.py` — 114 passed after review fixes.
- `git diff --check` — passed.
- `./pm daily-job --help` exposes `--valuation-ref` only on daily-job.

## Docs decision

Operator documentation is covered by approved S2; S1 changes no independent
operator procedure until historical preparation is available.

## Residual risks and uncovered areas

- Historical evidence creation is covered by later approved slice S2.
- Provider availability/fact-date parsing is covered by later approved slice S2.
- Full-suite regression is required after S2 and before aggregate review.
- Runtime-root backup remains assigned to operations.
- Cross-host writer coordination remains assigned to the existing NAV
  persistence boundary.

No residual risk is unclassified.
