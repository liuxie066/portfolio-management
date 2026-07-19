# S5 Fix Artifact

- Gate: `fix -> re-review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S5-pricing-correctness`
- Review artifact: `docs/reviews/code-review-20260719-185414.md`
- Status: `complete; re-review passed`
- Artifact path: `docs/gateflow/repo-review-27/S5-fix.md`

## Accepted finding

- Finding `01`: pricing canonicalization removed the explicit `.SH/.SZ/.HK/.US` market fact before provider routing, allowing numeric/name heuristics to select a conflicting market.

## Minimal correction

- Preserve the original caller code for market/type inference and use the canonical code only for cache/provider query keys.
- Derive `asset_type` from explicit terminal suffixes in both single and batch entry paths when the caller did not supply a stronger map.
- Make CN/ETF/Fund/HK provider support checks honor explicit conflicting asset types before applying code-shape heuristics.
- Keep result mapping keyed by the caller's original code.

## Regression coverage

- Ambiguous `004001.SH` routes as an A-share instead of an OTC fund.
- Numeric `510300.US` routes as US in both single and batch paths instead of ETF/HK/CN heuristics.
- `BRK.B` remains unchanged because only supported terminal market suffixes are stripped.

## Validation

- Required S5 focused suite -> `63 passed`.
- Full repository suite -> `635 passed`.
- `python3 -m compileall -q src tests` -> passed.
- `git diff --check` -> passed.
- Re-review decision -> `code review pass after fix and re-review`; finding `01` is `已修复`.

## Documentation decision

- No public command or operator workflow changed. The correction is internal routing semantics and is fully described by the implementation/review artifacts.

## Residual risks

- Exchange-holiday completeness remains `assigned to later work unit`.
- Historical suffixed cache rows remain `assigned to later migration decision`.
- External provider behavior is covered with deterministic fakes; a live-network canary remains a later deployment activity outside this work unit.
