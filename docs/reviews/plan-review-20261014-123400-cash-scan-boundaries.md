# Planreview — cash scan boundaries

Gate: plan review / fix / re-review. Target: docs/gateflow/cash-scan-boundaries/implementation-plan.md. Time 2026-10-14T12:34:00.727512+08:00.

Verdict: pass after one accepted finding fixed. Reviewed goal alignment, minimum design, ownership, execution/ordering/retry, slice cost and regression surfaces.

P1 accepted / 已修复: CLI init reads holdings before creating the DB (scripts/pm.py:1008), outside service.initialize_fingerprints. Narrowing only the service would leave inconsistent cash baseline acceptance. Plan now names the CLI pre-read; validation includes initialization failure before DB creation. Re-review confirms no automatic activation in receipt dispatch.

Adversarial decisions: filtering after typed conversion cannot solve incident; cash-only cache publication corrupts full slices; reserved-code/type/currency conflicts cannot be skipped; second drain requires shared send/mark serialization; newer failure must override stale recovery status. Plan explicitly addresses each. NAV full validation remains unchanged. One end-to-end slice is justified by coupled visible scan outcome/notification behavior and bounded diff.

Schema/public interface: new narrow read method and facade only, no schema/config/version changes. Docs choice: correct existing timer/runbook. Blocking questions: none.

Residual risks: external send/mark crash gap and multi-host execution assigned to later work unit, PM outbox maintainer; live acceptance assigned to later authorized deployment, operator. No deferred findings. Completion: accepted plan commit; next entry: implementation S1. Artifact: docs/reviews/plan-review-20261014-123400-cash-scan-boundaries.md.
