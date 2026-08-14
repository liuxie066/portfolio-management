# Gateflow Goal Confirmation — NAV Historical Receipt Recovery

- Work unit: `nav-historical-receipt-recovery`
- Base: `main@c54108b87a29cdb3ded01a0ea6581cad1032c470`
- Branch: `fix/nav-historical-receipt-replay`
- Decision: confirmed by the user on 2026-08-14
- Current gate: `plan`
- Next entry point: create and review the implementation plan

## Goal and motivation

Recover the already-failed `lx/2026-08-13` NAV without changing current
Holdings. The original durable NAV receipt contains the complete validated
33-row Holdings result, while the current Feishu Holdings differs only because
Futu MMF later moved from `752628.22` to `754468.25`. The existing historical
preparation command rejects that legitimate historical drift because it only
accepts the current Holdings digest.

## Direct evidence

- The source outbox row is uniquely keyed by
  `nav:daily-nav-job-20260814T081131264745-multi-00fc7d67`; its `lx` item binds
  account `lx`, date `2026-08-13`, child run id ending in `:lx`, confirmed
  non-dry-run execution, a valid 33-row holdings preflight, raw digest
  `51a93b...`, and normalized digest `c5c224...`.
- A fresh read is valid and has the same 32 other record digests. Only Futu
  `CNY-MMF` changed, producing current digest `ce79bed...`.
- The cash-flow effect is now `record_only` under the non-Futu manual Holdings
  authority and did not change Ping An cash.
- `HistoricalNavValuationEvidenceService.prepare()` currently has no trusted
  historical Holdings source; `AccountNavRecorderService` also requires fresh
  Holdings equality for every replay artifact.

## Success signals

- The existing `prepare-historical` command can use only the exact durable NAV
  receipt implied by `source_run_id` when the current Holdings digest differs.
- Receipt account/date/run/confirmation/status and Holdings validation payload
  are validated; rows are reconstructed, typed, and normalized digest is
  recomputed and must equal both receipt provenance and the operator-supplied
  expected digest.
- The resulting immutable artifact is explicitly marked as historical receipt
  recovery and binds the source receipt key and historical Holdings provenance.
- Replay still requires a successful fresh Holdings preflight and an unchanged,
  passing cash-flow financial fingerprint, but the historical receipt artifact
  may intentionally differ from the current Holdings digest.
- Replay writes the historical Holdings snapshot carried by the artifact, not
  current Holdings; it never changes the live Holdings table or refetches
  prices.
- Focused tests, full tests, static compile, reviews, release/upgrade checks, a
  preview, one confirmed NAV write, and post-write finality/receipt checks pass.

## Non-goals and scope boundary

- No arbitrary JSON/path input, approximate digest match, ignored asset class,
  Futu/MMF digest exemption, schema migration, new CLI, alternate NAV writer,
  current Holdings rollback, or multi-date/general backfill framework.
- No changes to NAV mathematics, cash-flow aggregation, overwrite defaults,
  normal daily-job behavior, other accounts, or current Holdings.
- Release, remote upgrade, and exactly one `lx/2026-08-13` NAV write are
  separately included by the user's explicit authorization; no other production
  mutation is allowed.

## Overengineering decision

Reuse the existing outbox getter, Holdings validation types, historical price
builder, immutable evidence store, and canonical replay writer. Add no storage,
command, dependency, queue, or generic recovery framework.

## Blocking open questions

None. Real production preview remains a fail-closed validation: if the receipt
payload cannot reproduce the recorded digest exactly, no artifact or NAV is
written.
