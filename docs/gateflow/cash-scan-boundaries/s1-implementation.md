# S1 — scan and receipt boundary

Gate: implementation / code review / fix / re-review accepted. Recorded 2026-10-14T12:41:26.882179+08:00.

Changed paths: HoldingsRepository + facade narrow fresh read; cash-effect scan and baseline callers; CLI scan failure dispatch and independent receipt branch; receipt delivery same-host lock and current-scope runtime rendering; regression tests and existing runbook/deploy docs.

Decisions: select only definite cash/noncash facts; invalid cash never becomes an absent/zero observation. Reuse complete raw Feishu read and canonical conversion; do not publish partial caches. Return original failed scan exception even when notification fails. Missing effect DB is skipped without initialization; corrupt DB remains a failed branch. Per-database process lock protects outbox read/send/mark. Runtime timestamps come from scan completion (legacy creation time fallback), current status from latest exact-scope scan; stored original payload is not overwritten.

Validation: targeted tests 180 passed in 1.30s; complete required suite 1671 passed in 9.19s (log /tmp/pm-cash-boundaries-full2.log). Earlier integration iteration: 1667 passed; additional real repository/scanner and init pre-read tests then added. First timer test used an invalid factory patch, corrected to subclass; one appended test decorator was malformed and repaired before successful rerun. Compileall, Ruff required targets and diff check passed. No failed checks remain.

Docs: cash-flow-effects-runbook and deploy-linux updated; README positioning unchanged. Reviewers cover notifications independently and holdings/scanner/NAV boundary in main session. No production interaction in this slice.

Residual risks: send/mark crash gap and multi-host serialization assigned to later work unit, owner PM outbox maintainer; real transport acceptance assigned to later authorized deployment, owner operator. No unclassified risks. Next: code review / fix / re-review; only then accepted slice commit. Artifact: docs/gateflow/cash-scan-boundaries/s1-implementation.md.

Final review fixes: DB-parent lock, legacy same-scan recovery proof, reserved cash aliases. Receipt fix suite 16 passed; final full suite 1673 passed in 10.99s (/tmp/pm-cash-boundaries-final-tests.log); required Ruff/compileall/diff passed. Code review: docs/reviews/code-review-20261014-124550.md. All three accepted findings 已修复. Completion: S1 accepted; next entry aggregate deepreview after slice commit.
