# Gateflow Plan Review Fix — NAV Historical Receipt Recovery

- Gate: `plan review / fix / re-review`
- Work unit: `nav-historical-receipt-recovery`
- Review artifact: `docs/reviews/plan-review-20260814-212806.md`
- Status: `pass`
- Current gate: `accepted plan commit`
- Next entry point: create the accepted plan commit, then implement S1

## Finding decision

### DR-PLAN-01 — accepted — 已修复

The plan now treats serialized validation outcomes only as a lossless transport
for raw field values. Recovery must verify every record digest and the aggregate
raw digest, discard the serialized validation conclusions, rerun the current
pure `HoldingsValidator` without live provider evidence, and build the typed
snapshot only from that fresh evaluation.

This path was proven read-only against the real `lx` production receipt:

- all 33 reconstructed record digests matched;
- recomputed raw digest matched `51a93b...`;
- current pure validation returned zero blocking/actionable records;
- recomputed normalized digest matched `c5c224...`.

## Re-review

The revised plan is code-generation-ready. The outbox is a historical raw-fact
source, not a validation authority. Existing current Holdings, cash-flow,
artifact, NAV, finality, and snapshot gates remain in their existing owners.

## Residual risks

- Historical provider availability remains a fail-closed operator-time risk.
- Production preview must still reproduce the complete digest before any
  artifact or NAV write.

No residual risk is unclassified.
