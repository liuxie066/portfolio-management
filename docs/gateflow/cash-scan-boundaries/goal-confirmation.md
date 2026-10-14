# Cash scan boundaries — confirmed goal

Recorded 2026-10-14T12:33:13.125101+08:00. User confirmed then requested continuation on branch `codex/fix-cash-scan-boundaries`; baseline `a59f8e3`.

Goal: failed cash-flow scans dispatch their durable runtime receipt immediately; existing receipt timer retries independently; delayed errors show occurrence time and current recovery state. Clearly unrelated noncash holdings missing names cannot block cash scans, while uncertain cash identity/type/quantity still blocks. NAV full validation remains strict.

Success: regression tests prove failed scans still fail, failed sends retry without a successful scan, recovered historical alerts cannot look like fresh failures, noncash missing names are excluded without cache pollution or fabricated holdings, unsafe cash reads never advance fingerprints.

Scope: plan/reviews/implementation, push and Draft PR/final closeout. No merge/release/upgrade/production changes, no new timer/database/dependency. Prior v0.2.5 delivery and data completion remain completed.

Evidence: scan queues and raises in `CashFlowEffectService.scan`; `cmd_cash_flow_effects_scan` dispatches only after scan returns; `cmd_receipts_dispatch` covers only NAV/operations; generic repository converts every row before filtering.

Open questions: none. Next gate: plan.
