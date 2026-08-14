# Gateflow S2 Implementation — Historical Evidence Preparation

- Gate: `implementation`
- Work unit: `nav-valuation-evidence-replay`
- Slice: `S2`
- Current gate: `slice completion`
- Next entry point: create the protected S2 commit
- Completion status: implementation and re-review complete

## Scope and changed files

- Added exact-date OpenD and no-later-than-date Eastmoney historical loaders in
  `src/app/nav_valuation_evidence_service.py`.
- Added fixed CASH/MMF/crypto price construction from explicit USDCNY/HKDCNY.
- Added current Holdings/cash-flow-gated, preview-first historical evidence
  preparation and exact digest write confirmation.
- Added the direct local `pm nav evidence prepare-historical` command through
  `src/service/application.py` and `scripts/pm.py`.
- Added focused provider/routing, fact-date, preview/write, digest, and CLI
  tests.
- Added `docs/nav-valuation-evidence-replay.md` and linked it from README.

## Invariants

- OpenD is queried with `AuType.NONE` and only an exact target-date daily row is
  accepted.
- Eastmoney fund NAV must have a fact date no later than the target date.
- There is no current-price or implicit-FX fallback.
- Holdings preflight is dry-run only; its digest and the fresh official
  cash-flow fingerprint must equal the supplied incident facts.
- Preview writes no artifact. Write additionally requires confirmation and the
  exact preview artifact digest.
- The normal official `ValuationService` remains the only issuer of the
  normalized valuation.

## Validation

- Focused tests: 67 passed after review fixes.
- Static compile and `git diff --check`: passed.
- `./pm nav evidence prepare-historical --help`: reviewed.

## Residual risks

- Provider availability remains an operator-time fail-closed dependency.
- No production historical query, artifact write, NAV replay, or notification
  was run during implementation.

No residual risk is unclassified.
