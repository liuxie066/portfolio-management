# Gateflow Plan Review Fix — Daily NAV Holdings Receipt Aggregation

- Work unit: `daily-nav-holdings-receipt-aggregation`
- Source review: `docs/reviews/plan-review-20260801-105653.md`
- Status: findings accepted and plan corrected; pending re-review

## PR-01 — accepted — fixed in plan

The plan now makes aggregate propagation a post-preflight invariant. Once an
account preflight result exists, every later success, partial, or failure return
from `AccountNavRecorderService` and `DailyAccountNavService` must carry its
sanitized workflow facts. Snapshot/valuation/NAV-persistence and report-payload
exception tests are explicit completion requirements.

This prevents receipt suppression from turning a committed Case/Event
transition into a user-invisible state when a later Daily NAV stage fails.

## PR-02 — accepted — fixed in plan

The plan now preserves the existing actionable discovery contract inside the
single NAV envelope. Each account/global scope derives action items from the
workflow plan's frozen discovery payload and exposes at most five entries with
Case key, record id, field, state, and exact command, plus total and omitted
counts. Raw evidence remains excluded; close and supersede transitions remain
counts only.

This keeps blocking notifications directly actionable while bounding message
size and eliminating the per-Case flood.

## Residual risk decision

The existing process-death window between Case/Event commit and NAV-envelope
enqueue remains accepted. Audit state is durable, and this work unit will not
introduce another transactional outbox or distributed transaction without
operational evidence that the informational gap is material.
