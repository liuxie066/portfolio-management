# Gateflow Goal Confirmation — Feishu Bitable Contract Repair

## Gate

- Gate: goal confirmation
- Work unit: feishu-bitable-contract-repair
- Branch: gateflow/feishu-bitable-contract-repair
- Base: origin/main@59625b0d6c666da338c0a520e85a221932846949
- Status: passed
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/goal-confirmation.md

## Confirmed Goal

Unify Feishu Bitable reads, raw validation, domain calculations, mutations, and fresh-readback proof across holdings, cash_flow, nav_history, holdings_snapshot, transactions, and compensation_tasks. Close the 29 unresolved findings in docs/reviews/repo-review-20260801-210200.md without reimplementing F27/F28, which are already fixed on the accepted base.

## Motivation and Direct Evidence

- FeishuClient validates required fields for single create but not batch create.
- NAV public reads are backed by a lossy projection while repair paths can perform full replacement.
- Cash-flow precheck and NAV calculation can consume different scans or stale aggregate caches.
- Futu parsing can collapse invalid quantity into zero and reconciliation can read optimistic cache.
- Holding replacement cannot distinguish an omitted field from an explicit clear.
- HoldingSnapshot quantizes unit price as money, and snapshot upsert is not an exact-set replacement.
- Transactions is documented as a read-only archive but still exposes mutation methods.
- Compensation mirror state is not folded after initial creation.

## Confirmed Source-of-Truth Design

The user confirmed a layered unique-source-of-truth design:

1. Feishu structure contract source:
   - typed Python registry under src/feishu/contracts/;
   - table/field identity, Feishu type/ui_type, encoding, schema-required fields, operation row-required fields, ownership, clearability, and business keys;
   - docs/schema.md, schema checking, migration inspection, and single/batch validation derive from this registry.
2. Domain calculation sources:
   - one canonical function or service for asset_class authority, cash-flow derivation and validation, NAV invariants, snapshot normalization/replay, business keys, and dedup keys;
   - writers, repair, backfill, and readback reuse those functions.
3. Runtime fact sources:
   - official calculations consume one immutable fresh-read dataset;
   - caches, model defaults, and requested payloads are not proof of remote facts.

The live Feishu schema is observed external state, not the desired contract source. It must be compared with the registry and must never update the registry automatically.

## Success Signals

- All 29 unresolved findings have a unique owner, regression test, and closure evidence.
- All public reads return their declared complete contract.
- Missing, null, and value remain distinguishable through raw validation and mutation planning.
- Official NAV consumes one account/run/date-bound cash-flow dataset.
- trusted, resolved, and final states are supported by fresh readback or remain explicitly partial.
- Snapshot rows replay their own valuation and account/date slices are exact sets.
- Generated docs match the structure registry with zero drift.
- Full test suite, compileall, contract generation check, diff check, slice reviews, aggregate deepreview, and PR review pass.
- A Draft PR is created; merge, release, deployment, and live data repair remain outside this work unit.

## Scope

- Source and tests for the shared Feishu boundary, holdings/Futu, cash flow, NAV, snapshots, transactions archive enforcement, and compensation mirror.
- Canonical contract registry and generated schema documentation.
- Read-only live schema metadata comparison.
- Gateflow artifacts, accepted local commits, push, Draft PR, and required review loops.

## Non-goals

- No live business-record reads beyond what is strictly required for separately authorized schema metadata inspection.
- No live record create/update/delete.
- No historical row repair, deduplication, normalization, or snapshot rewrite.
- No live schema mutation.
- No version bump, tag, Release, deployment, upgrade, merge, approval, ready-for-review transition, reviewer request, or branch deletion.
- No transaction-ledger reactivation.
- No cross-host transaction/lease system.

## Why This Is Not Overdesigned

- The contract registry replaces the currently duplicated and contradictory schema definitions; it does not introduce a generic schema platform.
- Domain formulas remain in domain code instead of becoming metadata expressions.
- Existing FeishuBatchWriteError, compensation log, fresh-read APIs, process locks, and NAV finality mechanisms are reused.
- New immutable datasets and mutation DTOs exist only where current defaulting, stale-cache, or null-clear ambiguity creates a demonstrated failure.

## Confirmed Authorization

- The user confirmed creation of this isolated branch.
- The user confirmed the layered unique-source-of-truth design.
- The user authorized read-only live Feishu schema metadata inspection.
- Live record mutation, live schema mutation, release, deployment, merge, and historical data repair are not authorized.

## Blocking Open Questions

None at goal confirmation. If live schema metadata differs from the intended canonical contract, implementation must stop at the relevant schema-dependent gate and record the mismatch instead of guessing or migrating.

## Next Gate

plan
