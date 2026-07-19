# S7 Fix Artifact

- Gate: `fix -> re-review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S7-service-safety`
- Source review: `docs/reviews/code-review-20260719-192824.md`
- Status: `complete; re-review pending`
- Recorded at: `2026-07-19 19:28:24 +0800`
- Artifact path: `docs/gateflow/repo-review-27/S7-fix.md`

## Accepted finding disposition

### Finding 1 — explicit fallback classification

- Decision: `accepted`.
- Final state: `已修复`.
- Fix: removed the `allow_fallback` default from `_service_or_fallback()`; every current service-backed command now passes `True` or `False` explicitly.
- Proof: AST inspection found 12 call sites, all with an explicit keyword; the four write-capable commands are false and the eight read-only commands are true.

## Validation

- Required S7 focused suite after fix -> `67 passed`.
- Full repository suite after fix -> `657 passed`.
- `python3 -m compileall -q src scripts skill_api.py` -> passed.
- `git diff --check` -> passed.

## Residual risks

- No unclassified risk introduced by the fix.
- Explicit remote mode and operator-driven ambiguous-write inspection retain their previously classified owners.

## Completion state

- Current gate: `re-review`.
- Next entry point: DeepReview the corrected S7 diff against `c76b916`.
