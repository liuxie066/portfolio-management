# S1 fix and re-review

Gate: fix / re-review. Scope: receipt service/tests and reserved-cash classification/test. Artifact: docs/gateflow/cash-scan-boundaries/s1-fix.md.

Accepted findings 1–3 in docs/reviews/code-review-20261014-124550.md are 已修复: DB-parent lock guarantees identical lock path across data-dir configs; exact same completed recovery scan proves current health for legacy recovery timestamps; noncash rows using reserved cash aliases cannot become silent absence.

Validation: receipt tests 16 passed, spawned-process duplicate regression passes; full/static checks recorded in S1 implementation artifact. No new schema/config/dependencies. Docs contract already describes same-host perDB lock and latest-scope recovery, no further product change.

Residual risks: approved external send/mark crash gap and multi-host coordination assigned to PM outbox maintainer later work unit; live acceptance assigned to operator after authorized deployment. All accepted findings fixed and re-reviewed. Next gate: accepted slice commit after required full checks.
