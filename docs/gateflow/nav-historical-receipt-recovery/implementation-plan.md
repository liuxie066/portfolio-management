# Gateflow Implementation Plan — NAV Historical Receipt Recovery

- Work unit: `nav-historical-receipt-recovery`
- Base: `main@c54108b87a29cdb3ded01a0ea6581cad1032c470`
- Branch: `fix/nav-historical-receipt-replay`
- Design sources: confirmed recovery scope, production receipt evidence, current
  code, and Kimi read-only review
- Current gate: `plan review`
- Next entry point: adversarial plan review
- Work unit status: `planned`

## Goal, motivation, and success signal

Use the original durable NAV receipt as the source of historical Holdings facts
for `lx/2026-08-13`, without mutating current Holdings. Success requires exact
receipt scope validation, deterministic reconstruction of the old typed
Holdings snapshot and digest, an immutable receipt-bound valuation artifact,
and replay through the existing final NAV plus Holdings-snapshot writer while
fresh cash-flow authority still passes.

## First-principles judgment and direct code evidence

- Current Feishu Holdings cannot represent a past state after an authorized Futu
  sync; requiring equality is therefore not a valid historical recovery rule.
- The original outbox payload already contains every field outcome needed to
  reconstruct the typed Holdings rows plus the original raw/normalized digests.
- `ValidatedHoldingsSnapshot.from_evaluation()` already owns typed conversion,
  canonical ordering, and both digests. Recovery should rebuild its existing
  inputs rather than duplicate valuation models.
- `NavValuationEvidenceStore` already gives immutable digest-addressed evidence,
  and `AccountNavRecorderService` already owns no-price replay, fresh gates, NAV
  finality, and snapshot persistence.

## Contract and state changes

### Receipt Holdings reconstruction

Add one strict constructor/helper at the Holdings snapshot boundary. It accepts
only the serialized public validation payload plus its recorded provenance and:

1. requires current policy versions, `success=true`, `status=valid`, read-only
   source, zero blocking/actionable records, no evidence errors, and exact record
   count;
2. uses serialized outcomes only to reconstruct each raw row, rejecting
   missing/duplicate fields, record-id/identity mismatches, and invalid shapes;
3. verifies each serialized record digest and the aggregate raw digest, then
   discards serialized validation conclusions and reruns the current pure
   `HoldingsValidator` over the reconstructed `RawHoldingRecord` rows without
   live provider evidence;
4. requires that fresh validation has zero blockers/actionables and uses its
   evaluation with the existing typed conversion and canonical digest logic;
5. requires recomputed raw and normalized digests to equal the receipt
   provenance and the caller's expected normalized digest.

No receipt-supplied digest is trusted without recomputation.

### Historical preparation fallback

Keep the existing CLI unchanged. `HistoricalNavValuationEvidenceService` first
runs the existing fresh preflight. If its digest equals the expected digest, the
current behavior remains unchanged. If it differs, the service:

1. requires `source_run_id` to end in the exact `:<account>` scope;
2. derives the single outbox key from the parent run and reads it with the
   existing `OperationStateStore.get_nav_receipt()`;
3. requires exact parent/child run, account, NAV date, confirmed non-dry-run,
   failed-item, and unique-item matches;
4. reconstructs and verifies the historical snapshot as above;
5. continues through the unchanged cash-flow fingerprint/gate, historical price,
   normalized valuation, preview digest, and confirmed artifact write steps.

The artifact uses preparation `historical_receipt_recovery`, for which the
evidence contract requires a non-empty source receipt key, and carries
historical Holdings provenance inside the normalized valuation payload.

### Replay exception

Normal and live-captured evidence still require fresh Holdings digest equality.
Only a validated `historical_receipt_recovery` artifact may differ. Fresh
preflight must still succeed. The replay snapshot provenance comes from the
artifact's normalized valuation instead of the current preflight, and finality
provenance records both historical and fresh normalized digests plus the source
receipt key. Cash-flow fingerprint equality and the current gate remain
mandatory.

## Affected files/modules

- `src/app/holdings_nav_preflight_service.py`
- `src/app/nav_valuation_evidence_service.py`
- `src/app/account_nav_recorder_service.py`
- `tests/test_nav_valuation_evidence_service.py`
- `docs/nav-valuation-evidence-replay.md`
- `docs/gateflow/nav-historical-receipt-recovery/*`

No CLI, HTTP, database schema, dependency, or unrelated module change is
allowed without updating and re-reviewing this plan.

## Implementation slice S1 — Trusted receipt recovery and replay

- Objective: make the one legacy historical snapshot recoverable without live
  Holdings mutation.
- Prerequisite: accepted plan commit.
- Allowed changes: the files listed above.
- Call path: `pm nav evidence prepare-historical` → application service →
  `HistoricalNavValuationEvidenceService.prepare` → current preflight or exact
  outbox fallback → existing historical valuation/evidence store →
  `daily-job --valuation-ref` → existing canonical NAV writer.
- State transition: outbox remains read-only; preview writes nothing; confirmed
  evidence write is immutable/idempotent; daily-job dry-run writes nothing;
  confirmed daily-job writes one absent NAV row plus its exact historical
  Holdings snapshot and receipt.
- Error handling/invariants: every ambiguity, truncation, mismatch, invalid
  policy, current cash-flow change, failing gate, existing NAV row, or artifact
  tamper fails closed. No current Holdings write is reachable.
- Non-goals: new commands, general receipt search, current-price fallback,
  repair writer, overwrite, or other accounts/dates.
- Focused validation:
  `python3.12 -m pytest -q tests/test_nav_valuation_evidence_service.py`
  must cover exact round-trip, tamper/scope/count/digest failures, unchanged
  current-digest path, receipt fallback, current-policy revalidation independent
  of serialized outcome status, artifact binding/source-key requirements,
  historical replay digest drift, normal replay rejection, no price fetch, and
  artifact provenance.
- Full validation: `python3.12 -m compileall -q src scripts`;
  `python3.12 -m pytest -q -p no:cacheprovider`; `git diff --check`.
- Completion signal: all validation and review gates pass; production preview
  reproduces `c5c224...` before any write.
- Stop condition: real payload cannot reproduce the expected digest or replay
  can bypass current cash-flow authority/account/date scope.

## Docs decision

Update the existing operator runbook only. Document that receipt recovery is an
automatic fail-closed fallback for an exact legacy source run, and that current
Holdings remains unchanged.

## Risks and residual-risk classification

- Serialized legacy outcome values may not reproduce raw digest: covered by the
  production preview stop condition; no write occurs.
- Current Futu Holdings differs from the target date: fixed in this slice by the
  explicitly typed historical receipt artifact; current preflight remains a
  health gate, not a historical equality assertion.
- Historical provider availability: existing fail-closed operator-time risk,
  assigned to the current runbook.
- The following day's P&L reflects the later MMF increase: expected NAV
  continuity behavior; document in the production closeout, no code change.
- Multi-host writer coordination and local evidence backup: existing owners and
  boundaries, not expanded here.

No residual risk is unclassified.

## Why this is not overdesigned

It adds no new operator interface or persistence system. The smallest safe
change is one strict receipt deserializer plus two narrow branches in the
existing preparation/replay path; all valuation, cash-flow, NAV, finality, and
snapshot code remains shared.

## Completion report format

- exact changed behavior/files;
- focused/full validation and review findings;
- release/version/remote upgrade evidence;
- preview artifact digest and written valuation ref;
- final NAV/finality/snapshot/receipt/duplicate verification;
- remaining risks and owners.
