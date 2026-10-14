# S2 implementation

## Scope and implementation
G2 implemented through AssetNameService evidence lookup, optional read-only reconciliation evidence and pure validation, then existing workflow repair. The internal inbox entry is renamed process_event_notification to reflect its write capability; all callers are updated.

The name case is pre-materialized without discovery delivery using the existing store API. The subsequent durable preparation sees all observed cases; other missing fields keep their normal receipts. Only name is patched, with repository timestamp ownership unchanged. Unknown name attempts block event completion before lookup and remain active in notification planning.

## Review finding and fix
P1 accepted: ordinary notify/NAV could supersede an unknown automatic attempt and permit a later write. Shared materialization now retains applying/unknown automatic attempts. Manual recovery preserves original apply_operator_context, so an inconclusive recovery cannot erase the protection. No schema migration, no change to ordinary manual attempt transitions.
Evidence: failing regression observed state superseded after notify before fix. New regressions cover notify, NAV preflight, failed durable finalization (applying), repeated inconclusive recovery and event redelivery; one write retained.

## Validation
Initial S2 focused: 148 passed. First full suite before review fix: 1616 passed. Shared-state focused after final fix: 72 passed. Final complete-suite result and independent re-review recorded in S2 review artifact before acceptance. Compileall, Ruff and diff checks passed; OpenAPI export check passed.

## Documentation
README and listener runbook explain event-only name fill, trusted source, no changes to other fields/ordinary reads, durable uncertainty and the existing Feishu non-atomic final-read/PATCH window.

## Residual risks
Existing Feishu compare-and-set limitation: assigned to later work unit. Provider/network unavailable: covered by manual-fallback policy. Original incident and deployment acceptance: assigned to future authorized production verification. No production reads/writes or messages were performed in this implementation.

Final validation: 1620 passed in 10.01s. Independent re-review passed. Review artifact: ../../reviews/code-review-20261014-093113.md.
Current gate: accepted slice commit. Next: aggregate deepreview.
