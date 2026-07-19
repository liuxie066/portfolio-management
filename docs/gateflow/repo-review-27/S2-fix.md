# S2 Review Fix Artifact

- Gate: `fix -> re-review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S2-compensation-recovery`
- Finding: `S2-01`
- Status: `已修复`
- Artifact path: `docs/gateflow/repo-review-27/S2-fix.md`

## Accepted finding

`CompensationService._apply_snapshot_target()` read the current NAV through `get_nav_on_date()`, while `NavHistoryRepository` populated that read model from an index projection that omitted `details`. In production the recovery CAS could therefore compare `{}` against the recorded failed/original details and incorrectly return `state_conflict`.

## Fix

- Added `details` to `NavHistoryRepository.NAV_INDEX_PROJECTION_FIELDS` so the authoritative persisted recovery evidence reaches `get_nav_on_date()`.
- Added a focused contract test that prevents the recovery field from being removed from the projection.

## Re-review evidence

- Snapshot retry test proves failed details -> snapshot batch upsert -> complete detail patch -> `RESOLVED`.
- Idempotency test proves already-complete details do not write snapshots or patch details again.
- Focused and broad S2 test sets pass.

## Residual risk classification

- No residual risk from this finding remains in S2.
