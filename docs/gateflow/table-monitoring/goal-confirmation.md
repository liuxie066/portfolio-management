# Goal confirmation — table monitoring

User confirmed on 2026-10-14 after the concrete scope proposal.

- G1: distinguish Cash Flow facts from Holdings cash observations; terminal record_only must not demand manual processing, unresolved effects must remain visible.
- G2: automatically fill an empty non-Futu domestic fund name from an exact-code trusted source; preserve user-entered names and fail closed on missing evidence.
- G3: show manual correction instructions before the revalidation command.
- Success: regressions reproduce the reported cases and prove truthful receipts, exact-record name-only writes, race protection and no repeat write after unknown outcome.
- Boundaries: branch codex/fix-table-monitoring, Gateflow through Draft PR and final closeout. No merge, release, deployment, production data edits, new provider or general auto-repair.
- Evidence: cash_flow_effect_service._commit_scan includes cash fingerprint changes; discovery renderer hardcodes pending; holdings event validation only notifies; _asset_name only has Futu evidence; renderer omits action.description.
- Existing assets: FundProvider Tencent exact jj-code parser; HoldingsWorkflowService._execute_patch durable attempts; HoldingRepairPatch digest and fresh readback.
- Baseline: e611684 (main and freshly fetched origin/main match). Clean initial checkout. Prior read-only investigation: 123 targeted tests and two isolated reproductions passed. Production SSH unavailable; original effect identity unverified.
- No blocking product questions. Fund source availability remains an external limitation, not authority to guess names.
