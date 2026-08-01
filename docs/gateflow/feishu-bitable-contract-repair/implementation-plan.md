# Gateflow Implementation Plan — Feishu Bitable Contract Repair

## Gate Metadata

- Gate: plan
- Work unit: feishu-bitable-contract-repair
- Branch: gateflow/feishu-bitable-contract-repair
- Base: origin/main@59625b0d6c666da338c0a520e85a221932846949
- Design document: docs/feishu-bitable-contract-repair-plan.md
- Audit evidence: docs/reviews/repo-review-20260801-210200.md
- Live schema evidence: docs/gateflow/feishu-bitable-contract-repair/live-schema-baseline.md
- Status: accepted after final plan re-review
- Artifact path: docs/gateflow/feishu-bitable-contract-repair/implementation-plan.md

## Goal

Make every Feishu Bitable method use one declared structure contract, one canonical domain calculation per financial fact, and one fresh runtime-fact dataset per official operation. Close all 29 unresolved audit findings and the live-schema enum drift discovered during plan evidence collection.

## Motivation

The repository currently has multiple competing definitions:

- docs/schema.md is parsed as runtime schema while also containing ambiguous prose encodings;
- FeishuClient has single-create REQUIRED_FIELDS but batch create does not reuse them;
- repositories maintain partial projection lists and independent serializers;
- Pydantic models can manufacture defaults before raw validation;
- normal writers, repair writers, backfill, reconciliation, and compensation repeat formulas or completion decisions;
- caches and requested targets can be mistaken for fresh remote facts.

These are not independent defects. They are manifestations of missing ownership at the structure, calculation, and runtime-fact boundaries.

## Success Signals

1. The typed registry is the only source for Feishu field structure, type/ui_type, encoding, schema-required fields, operation row-required fields, select options, ownership, clearability, and business keys.
2. docs/schema.md generated contract blocks match the registry with zero drift.
3. Strict live-schema comparison uses the registry and reports optional/unconfigured tables without false success.
4. All raw readers preserve missing/null/value before domain model creation.
5. Holdings/Futu mutations require complete identity, respect field ownership, and prove completion with fresh readback.
6. Official NAV uses exactly one account/run/date-bound cash-flow dataset and one normalized valuation snapshot.
7. NAV public reads are complete; repair/backfill and canonical writer use identical formulas and invariants.
8. holdings_snapshot is replayable and an exact set before NAV snapshot finality becomes complete.
9. transactions is explicitly read-only and compensation_tasks is an optional best-effort current-state mirror.
10. Every audit finding has a regression test and closure evidence.
11. All Gateflow review loops pass, a Draft PR is created, and final closeout records remaining operational gates.

## Non-goals and Authorization Boundary

- No live business-record read, create, update, delete, historical repair, deduplication, or normalization.
- No live schema mutation or select-option migration.
- No release, deployment, version bump, tag, GitHub Release, merge, approval, ready-for-review transition, reviewer request, issue mutation, or branch deletion.
- No transaction ledger reactivation.
- No cross-host lock, external-editor lease, distributed transaction, or new storage service.
- No generic schema DSL or formula interpreter.

## Design Alignment

This plan implements the design document with one refinement confirmed by the user: use layered unique sources of truth.

### Structure source of truth

Create src/feishu/contracts as a small typed Python package:

- enums: TableRole, FieldOwnership, FieldEncoding, WriteOperation;
- immutable FieldContract, WriteContract, TableContract;
- one registry keyed by logical table name;
- helpers for table refs, field sets, row-required validation, select-option validation, and schema comparison.

The registry contains metadata only. It does not contain executable financial formulas or runtime data.

### Calculation sources of truth

- holdings identity/canonicalization and asset_class authority live in domain helpers;
- cash-flow manual/completed validation and derivation live in domain helpers;
- NAV calculations and final invariants live in NavCalculator/domain helpers;
- snapshot normalization, replay, and digest live in snapshot domain helpers.

Every writer, repair, backfill, readback, and compensation path calls these helpers.

### Runtime fact sources of truth

- CashFlowDatasetSnapshot binds account, nav_date, run_id, fetched_at, source fingerprint, validated rows, aggregates, FX evidence, and effect-store revision.
- NormalizedValuationSnapshot binds valuation totals, normalized holding rows, excluded-zero provenance, and digest.
- Cache data accelerates nonofficial reads but cannot prove trusted/resolved/final.

## Live Schema Decisions

The authorized read-only metadata inspection established:

- holdings_snapshot.as_of is Text;
- cash_flow.flow_date and nav_history.date are DateTime/Date fields;
- holdings asset_type/asset_class/industry and cash_flow.flow_type are SingleSelect;
- transactions tx_type/asset_type are Text in the legacy archive;
- compensation_tasks and schema_version are not configured;
- optional cash_flow.updated_at and nav_history.updated_at are absent;
- live holdings asset_type/industry options differ from the broader domain enums.

Implementation policy:

- registry models the observed live wire contract;
- domain enums may remain broader for pricing/legacy compatibility;
- holdings writers reject values outside registry-approved select options before any request;
- add Industry.AI for valid observed live data;
- no live option mutation is attempted;
- transaction archive uses Text contracts and no mutation contract.

## Public Contract and State Decisions

### Missing/null/value

- Missing means do not modify.
- Null is accepted only for a contract-declared clearable field and only in update/replace operations.
- Value is canonicalized and validated.
- Create never silently drops a required null/missing field.

### Operation result states

- planned: deterministic target/plan exists, no write attempted.
- confirmed: API response confirms a record/stage.
- unknown: timeout/malformed response cannot prove whether the write happened.
- partial: some stages confirmed and the full target is not read back.
- trusted/resolved/final: fresh remote readback and all declared invariants match.

### Cash-flow dataset ownership and official entrypoints

- `CashFlowSummaryService.build_dataset(...)` is the only storage-backed builder for `CashFlowDatasetSnapshot`; the domain type itself has no storage dependency.
- `AccountNavRecorderService` and `NavInitializationService` are the two official top-level builders. Manual/service/skill/daily and explicit CLOSED paths converge on `AccountNavRecorderService`; initialization builds the same contract directly and creates an explicit run_id.
- `PortfolioManager.record_nav(...)` and `NavRecordService.record_nav(...)` accept an already-built dataset and never reconcile or re-read cash_flow. Every `persist=True` call without a compatible dataset fails closed.
- Repair/backfill `persist=False` calls must explicitly build a fresh target-date dataset through the same builder; preview/UI-only summary calls remain nonofficial and cannot be used as write evidence.
- The dataset window is always `[config.start_year-01-01, nav_date]`; gap flow separately uses `(previous_nav_date, nav_date]`. The same window metadata is persisted in NAV details.

### Normalized valuation transmission

- `ValuationService.calculate_normalized_valuation(...)` constructs one immutable `NormalizedValuationSnapshot` from canonical holdings and price evidence.
- `NormalizedValuationSnapshot.from_closed_input(...)` is the only CLOSED factory: it has zero holding rows and explicit user-sourced manual cash/noncash components. CLOSED is not allowed to construct an independent `NAVHistory` calculation.
- Existing `PortfolioValuation` remains a compatibility projection produced from that object; it is not an independent calculation source.
- `PortfolioReadService.build_snapshot(...)` returns both `normalized_valuation` and the compatibility `valuation`.
- Official record/init flows pass both explicitly. `NavRecordService` validates their shared digest, calculates official NAV from `normalized_valuation`, and passes the same object to `SnapshotService`.
- `SkillAPI.close_nav` delegates to `AccountNavRecorderService.record_closed`; it never calls the repository directly. The same dataset, normalized transmission, final invariant, and exact-set authority boundaries apply.
- A caller-supplied compatibility projection whose totals/rows disagree with the normalized digest is rejected for official persistence.

### Snapshot write authority

- `SnapshotWriteAuthority` is an immutable scoped value created only by the confirmed top-level record/init flow. It contains account, as_of, run_id, issuer, overwrite_existing, confirmed, and normalized target_digest.
- The exact-set engine binds that request to its fresh-read before-set and deterministic plan_digest, then fsyncs both digests and the authority before the first NAV mutation.
- First write requires confirmed write intent but not overwrite authority. A nonempty existing slice requires both overwrite_existing and confirmed. Compensation may reuse only an authority whose account/as_of/target_digest/plan_digest match the durable prepared event.
- Any scope or digest change requires a new top-level confirmation; recovery never widens authority.

### Cross-table NAV/snapshot state

1. Build CashFlowDatasetSnapshot and NormalizedValuationSnapshot.
2. Build NAV target and holdings_snapshot exact target.
3. Before first non-dry-run NAV mutation, fsync a local-prepared snapshot target.
4. Write/readback NAV.
5. Apply snapshot create/update/clear/delete plan.
6. Fresh-read the full snapshot slice, replay it, and verify v2 digest.
7. Patch NAV snapshot details complete and resolve the local target.
8. Any unknown/partial stage remains recoverable and never becomes complete.

## Implementation Slices

Each slice follows:

implementation → code review using deepreview → fix → re-review → accepted slice commit.

No slice may be released or deployed independently. Residual risks explicitly assigned to a later approved slice do not block the earlier slice; all must be closed or reclassified before aggregate deepreview.

### S1 — Typed Feishu structure registry and shared boundary

**Objective**

Create the structure source of truth and make config, client validation, schema inspection, and generated documentation consume it.

**Findings**

- F14 strict schema false green
- F23 read errors swallowed as not-found
- F26 table ref parser drift
- enabling infrastructure for F12
- C01 live select-option/domain-enum drift

**Allowed files**

- src/feishu/contracts/__init__.py
- src/feishu/contracts/models.py
- src/feishu/contracts/registry.py
- src/feishu/errors.py
- src/config.py
- src/feishu_client.py
- src/feishu_storage.py
- scripts/migrate_schema.py
- scripts/generate_feishu_schema_docs.py
- docs/schema.md
- config.example.yaml only if retired remote price_cache config must be removed
- tests/test_feishu_contracts.py
- tests/test_schema_check.py
- tests/test_feishu_client.py
- tests/test_feishu_storage.py
- tests/test_config.py
- tests/test_models.py

**Exact changes**

1. Define immutable contracts for holdings, cash_flow, nav_history, holdings_snapshot, transactions, compensation_tasks, and schema_version.
2. Mark price_cache retired/local-only and remove its remote client contract.
3. Add parse_table_ref as the only parser; config, FeishuClient, event adapters, and deploy validation use it.
4. Add FeishuRecordNotFoundError. Only an explicit structured API not-found maps to it; optional storage reads catch only this type.
5. Replace client REQUIRED_FIELDS with registry write contracts. create_record and batch_create_records call the same per-row validator and report table/operation/row index.
6. Preserve null in update payloads; reject null for nonclearable fields at repository mutation planning boundaries.
7. Make scripts/migrate_schema.py consume registry, compare exact type/ui_type/options, and classify core, configured optional, and unconfigured optional.
8. Generate only the field-contract blocks in docs/schema.md between stable markers; preserve human policy prose. Generator supports --check.
9. Add Industry.AI; keep domain-only legacy enum values but reject unsupported holdings writes through registry select-option validation.
10. Remove remote price_cache config/client registration without changing local price cache behavior.

**Invariants**

- repository/app modules do not define a second table field/type list without a coverage assertion against the registry;
- docs are a generated projection, never parsed as runtime truth;
- live schema never mutates the registry;
- optional unconfigured table is skipped, not passed;
- transactions has no active repository write contract even though transport metadata remains readable.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s1 python3.12 -m pytest -q -p no:cacheprovider tests/test_feishu_contracts.py tests/test_schema_check.py tests/test_feishu_client.py tests/test_feishu_storage.py tests/test_config.py tests/test_models.py
- python3.12 scripts/generate_feishu_schema_docs.py --check
- python3.12 scripts/migrate_schema.py expectations

**Expected assertions**

- single and batch create produce the same missing-field decision;
- table refs reject whitespace-only, empty, and extra segments identically;
- 404 returns None only through optional read; 403/timeout/malformed propagate;
- exact live types/options in the saved fixture compare cleanly;
- transaction Text types and unconfigured optional tables are not false failures;
- domain-only holding select values are rejected before transport.

**Non-goals**

- no repository projection migration beyond coverage helpers;
- no live schema request in deterministic tests;
- no live schema mutation.

**Completion signal**

Registry, generator, schema checker, table parser, write validation, and error classification have one source and scoped tests pass.

**Stop condition**

If current source has an active production dependency on remote price_cache or a core live schema type differs from the recorded baseline, stop.

### S2 — Holdings canonical identity, ownership, clear, and fresh proof

**Objective**

Make holdings mutations use complete keys and tri-state mutation objects without clearing manual fields accidentally.

**Findings**

- F05 holding absolute replace cannot clear optional fields
- F09 mutation may omit broker and select an arbitrary row
- F25 validation canonicalizes a copy but writes the original

**Allowed files**

- src/domain/holdings.py
- src/domain/holding_mutations.py
- src/feishu/repositories/holdings_repository.py
- src/feishu/_holdings_mixin.py
- src/feishu_storage.py
- src/app/cash_service.py
- src/app/cash_flow_effect_service.py
- src/app/compensation_service.py
- tests/test_feishu_storage.py
- tests/test_holdings_bulk_upsert_minimal.py
- tests/test_cash_service.py
- tests/test_cash_flow_effect_service.py
- tests/test_compensation_service.py
- tests/test_holding_mutations.py

**Exact changes**

1. Define canonical HoldingIdentity(asset_id, account, broker), UNSET, HoldingPatch, and HoldingTarget.
2. Trim identities, uppercase currency, validate finite quantity/cost, and validate registry select options once at the write boundary.
3. Require broker for update/delete/replace mutations. Compatibility lookup without broker returns only a unique candidate or raises AmbiguousHoldingIdentityError.
4. HoldingPatch sends only set fields. HoldingTarget expresses a complete absolute target and carries owned_fields, base_record_id, and fresh base digest.
5. Only avg_cost, asset_class, and industry are clearable by an authorized target; tag changes only when explicitly owned.
6. System paths carry manual fields from the same fresh base and cannot create clears from Pydantic defaults.
7. After replace/bulk/compensation mutation, fresh-read the account slice and compare identity plus owned fields before updating caches or resolving compensation.
8. Cache keys and returned models use the canonical object actually sent.

**Invariants**

- all mutations use (asset_id, account, broker);
- Missing and Null are distinct;
- system workflows never own arbitrary manual metadata;
- requested target/cache is not readback proof.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s2 python3.12 -m pytest -q -p no:cacheprovider tests/test_holding_mutations.py tests/test_feishu_storage.py tests/test_holdings_bulk_upsert_minimal.py tests/test_cash_service.py tests/test_cash_flow_effect_service.py tests/test_compensation_service.py

**Expected assertions**

- two brokers plus omitted broker fails with zero write;
- whitespace/lowercase input produces one canonical payload/cache key;
- explicit clear sends null only for allowed fields;
- partial/default model cannot clear manual metadata;
- optimistic cache disagreement prevents resolved.

**Non-goals**

- no historical holding normalization;
- no Futu provider parsing change in this slice.

**Completion signal**

All holdings mutation entrypoints accept canonical mutation contracts and fresh proof.

**Stop condition**

If a caller cannot supply broker or owned_fields without changing user-visible authority, stop and report that caller.

### S3 — Futu source validation, economic exposure, and reconciliation

**Objective**

Fail closed on invalid provider facts, use the shared asset_class authority, and compare writes with fresh remote rows.

**Findings**

- F04 invalid Futu quantity silently becomes zero
- F07 Futu reconciliation reads optimistic cache
- F08 Futu derives asset_class from currency/listing market

**Allowed files**

- src/app/futu_balance_sync_service.py
- src/app/futu_sync_reconciler.py
- src/app/holdings_validation.py
- src/domain/holdings.py
- tests/test_futu_balance_sync_service.py
- tests/test_futu_sync_reconciler.py
- tests/test_futu_sync_evidence.py
- tests/test_holdings_validation.py

**Exact changes**

1. Parse provider quantity into explicit valid/missing/invalid states; never default invalid/missing to zero.
2. Validate the complete authoritative account/profile position slice before building any diff.
3. Preserve explicit zero as a valid close target; block short/unknown sides per current product policy.
4. New rows require explicit valid provider currency. Existing rows preserve valid currency and manual metadata.
5. Reuse the single asset_class authority function: A share → 中国资产, CASH/MMF → 现金, otherwise no automatic value without instrument-level evidence.
6. Reconciler calls get_holdings_fresh on every attempt and refreshes cache only after parsing remote rows successfully.
7. Receipt differences list actual remote/requested fields and cannot become trusted from cache.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s3 python3.12 -m pytest -q -p no:cacheprovider tests/test_futu_balance_sync_service.py tests/test_futu_sync_reconciler.py tests/test_futu_sync_evidence.py tests/test_holdings_validation.py

**Expected assertions**

- mixed valid + N/A/NaN/Inf qty produces zero writes;
- explicit zero closes and clears avg_cost through S2 target semantics;
- HK ETF and US-listed China exposure remain unclassified without evidence;
- manual asset_class remains unchanged;
- optimistic cache/remote mismatch is not trusted.

**Non-goals**

- no OpenD protocol or account mapping redesign;
- no live Futu/Feishu call.

**Completion signal**

Futu diff begins only from a fully valid source slice and trusted receipts use fresh Feishu evidence.

### S4 — Cash-flow raw/completed contracts and complete projections

**Objective**

Create the canonical cash-flow domain facts and make every reader/writer preserve and validate the same fields.

**Findings**

- F15 typed reader defaults missing currency/flow_type
- F21 list projection omits remark/source/dedup
- F22 cross-field validation differs by entrypoint
- F29 exact read drops persisted dedup_key
- F30 direct foreign add pollutes hot cache
- F31 missing date enters only cumulative

**Allowed files**

- src/domain/cash_flow_contracts.py
- src/models.py
- src/feishu/repositories/cash_flow_repository.py
- src/app/cash_flow_summary_service.py
- src/app/cash_flow_effect_service.py
- src/feishu/_cash_flow_mixin.py
- tests/test_cash_flow_contracts.py
- tests/test_cash_flow_summary_service.py
- tests/test_cash_flow_effect_service.py
- tests/test_feishu_storage.py

**Exact changes**

1. Add immutable RawCashFlowRecord, ManualCashFlowFacts, CompletedCashFlowFacts, CashFlowValidationIssue.
2. Manual validation requires valid date, nonblank account/broker, supported currency, and finite nonzero Decimal amount.
3. Completed validation additionally requires sign-consistent flow_type, positive finite rate, Decimal cny_amount formula, persisted dedup_key, and source.
4. CNY rate is 1. Foreign currency requires cny_amount/rate; no fallback to original amount.
5. Complete list and exact-read projections include every raw/digest/readback field.
6. Exact reader preserves observed dedup_key and missing state.
7. Direct add accepts only CompletedCashFlowFacts and updates cache from validated cny_amount.
8. Missing/invalid date produces a validation blocker and is excluded from every aggregate.
9. Cache invalidation covers create/update/delete and old/new account when identity changes.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s4 python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_contracts.py tests/test_cash_flow_summary_service.py tests/test_cash_flow_effect_service.py tests/test_feishu_storage.py

**Expected assertions**

- missing currency/flow_type remains missing and blocks completed use;
- remark-only/source/dedup changes are visible;
- NaN/Inf/zero/blank identity fail before write;
- foreign add without completed CNY fields performs zero write/cache update;
- missing date contributes to no aggregate.

**Non-goals**

- no FX provider or reconcile workflow change;
- no NAV orchestration change.

**Completion signal**

All cash-flow entrypoints share raw/manual/completed field semantics.

### S5 — Cash-flow reconciliation, FX evidence, and duplicate gate

**Objective**

Make reconciliation deterministic and ensure duplicates or invalid FX evidence cannot become completed financial facts.

**Findings**

- F16 manual cash-flow duplicates can double-count
- F17 FX date mismatch writes before being rejected

**Allowed files**

- src/domain/cash_flow_contracts.py
- src/feishu/repositories/cash_flow_repository.py
- src/app/cash_flow_fx_confirmation.py
- src/app/cash_flow_event_completion_service.py
- src/app/operation_state_store.py only for reading/binding existing confirmation evidence
- scripts/pm.py
- tests/test_cash_flow_contracts.py
- tests/test_cash_flow_event_completion_service.py
- tests/test_cash_flow_fx_confirmation.py
- tests/test_pm_cli.py

**Exact changes**

1. Reconcile sequence is fresh full scan → manual validation → expected dedup grouping → generated patch plan → optional write → fresh readback → completed validation.
2. Group by expected canonical dedup, never observed dedup. Two or more distinct in-scope record_ids in one expected-dedup group block every member; a singleton group remains eligible.
3. Validate manual rate, rate source, positivity, finiteness, and rate_date == flow_date before any update/confirmation write.
4. Bind local FX confirmation to record_id, flow_date, and observed generated-field fingerprint.
5. Flow-type conflict is a system-owned proposed correction; downstream use blocks until readback confirms it.
6. Add read-only pm cash-flow duplicates --json. It never deletes or rewrites rows.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s5 python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_contracts.py tests/test_cash_flow_event_completion_service.py tests/test_cash_flow_fx_confirmation.py tests/test_pm_cli.py

**Expected assertions**

- duplicate groups block all members;
- a singleton expected-dedup group completes normally;
- duplicate detection uses the fresh full-account scan and cannot be hidden by a date filter or a tampered observed dedup_key;
- rate-date mismatch results in zero Feishu update and zero confirmation;
- stale confirmation fingerprint blocks;
- flow_type conflict remains pending until readback.

**Non-goals**

- no duplicate deletion/merge;
- no new Feishu FX evidence fields.

**Completion signal**

Only fresh, unique, fully evidenced rows become CompletedCashFlowFacts.

### S6 — Run-scoped cash-flow dataset and official NAV handoff

**Objective**

Eliminate stale-cache and double-scan divergence in official NAV.

**Finding**

- F06 cash aggregate cache can feed stale facts to NAV

**Allowed files**

- src/domain/cash_flow_contracts.py
- src/app/cash_flow_summary_service.py
- src/app/cash_flow_effect_service.py
- src/app/daily_nav_job_service.py
- src/app/account_nav_recorder_service.py
- src/app/nav_initialization_service.py
- src/app/nav_record_service.py
- src/portfolio.py
- src/service/application.py only if required to pass the application contract
- skill_api.py
- tests/test_cash_flow_summary_service.py
- tests/test_daily_nav_services.py
- tests/test_nav_record_service.py
- tests/test_cash_flow_effect_service.py
- tests/test_service_application.py

**Exact changes**

1. Add frozen CashFlowDatasetSnapshot with account/nav_date/run_id, fetched_at, contract version, raw/completed rows, blockers, duplicate groups, daily/monthly/yearly/cumulative Decimal maps, financial/full fingerprints, FX confirmation fingerprint, and effect-store revision/gate. `CashFlowSummaryService.build_dataset` is its only storage-backed builder.
2. Stable fingerprint sorts by record_id and encodes Missing/Null explicitly.
3. Scope aggregates to start_year through nav_date. Future valid rows are audit-only; missing date blocks because scope is unknowable.
4. DailyNavJobService removes its separate `_cash_flow_blocker` reconcile scan. AccountNavRecorderService constructs one dataset/effect gate from the same fresh full-account raw scan after resolving run_id/nav_date and passes that object through PortfolioManager to NavRecordService.
5. NavInitializationService creates a run_id, constructs the same target-date dataset, and passes it through the same lower-level contract. Service and normal Skill entrypoints already converge on these two top-level services and require no independent builder.
6. PortfolioManager/NavRecordService signatures accept `cash_flow_dataset`. Persisting official NAV without a complete account/nav_date/run_id/window/effect-revision match fails closed. Downstream services do not call reconcile or cash-flow storage.
7. Nonofficial summary entrypoints may independently request a fresh dataset; old aggregate cache is UI-only.
8. NAV details include dataset fingerprint, fetched_at, window, contract version, FX evidence fingerprint, and effect-store revision.
9. `SkillAPI.close_nav` delegates to `AccountNavRecorderService.record_closed`, which creates a run_id and the same dataset before calling `PortfolioManager.record_closed_nav`/`NavRecordService.record_closed_nav`. The compatibility entrypoint performs no direct repository call. S8 owns the CLOSED calculation invariant; S9/S10 later replace its compatibility valuation/snapshot persistence with the normalized exact-set contract before aggregate acceptance.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s6 python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_summary_service.py tests/test_daily_nav_services.py tests/test_nav_record_service.py tests/test_cash_flow_effect_service.py tests/test_service_application.py

**Expected assertions**

- external add/edit/delete after old cache preload is reflected;
- precheck and NAV use the same object/fingerprint;
- manual/service/skill, daily job, and initialization paths each build exactly one dataset at the approved top-level boundary;
- CLOSED uses the same builder and makes zero direct `write_nav_record` calls from `skill_api.py`;
- daily job performs no separate reconcile scan before the builder;
- a mismatched run/account/date or effect revision blocks;
- future rows do not affect current totals; missing-date rows block;
- process restart/disk cache cannot replace official fresh scan.

**Non-goals**

- no distributed snapshot transaction with external editors;
- no cache removal for nonofficial UI paths.

**Completion signal**

One official NAV run has one immutable cash-flow fact set.

### S7 — NAV complete reads, stage-aware writes, and consumer semantics

**Objective**

Make public NAV reads lossless, preserve confirmed partial-write facts, and align all consumers with persisted non-cash semantics.

**Findings**

- F01 NAV public projection loses fields and repair can clear facts
- F13 create failure loses confirmed update facts
- F18 stock_value and regional names disagree with persisted semantics

**Allowed files**

- src/feishu/repositories/nav_history_repository.py
- src/app/account_service.py
- src/app/portfolio_read_service.py
- src/app/report_query_service.py
- src/app/reporting_service.py
- src/app/audit_service.py
- src/app/nav_history_receipt_service.py
- tests/test_nav_history_index.py
- tests/test_feishu_nav_repository_boundary.py
- tests/test_nav_bulk_upsert_minimal.py
- tests/test_account_service.py if added
- tests/test_portfolio_read_service.py
- tests/test_report_query_service.py
- tests/test_reporting_service.py

**Exact changes**

1. Separate internal date/identity index from public full canonical NAV reads.
2. Full memory/disk cache rows preserve every NAVHistory field across restart.
3. Rename internal semantics to non_cash_value and cn/us/hk_exposure_value while keeping Feishu compatibility column names.
4. Inventory every stock_value + fund_value consumer. Persisted stock_value already includes fund_value; consumers never add fund again.
5. Regional compatibility fields aggregate non-cash exposure and may include fund/ETF.
6. Batch write tracks update/create confirmed IDs independently. A later-stage failure raises FeishuBatchWriteError with confirmed/unknown/failed scopes and invalidates/rebuilds cache.
7. Public read/audit uses complete rows.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s7 python3.12 -m pytest -q -p no:cacheprovider tests/test_nav_history_index.py tests/test_feishu_nav_repository_boundary.py tests/test_nav_bulk_upsert_minimal.py tests/test_portfolio_read_service.py tests/test_report_query_service.py tests/test_reporting_service.py

**Expected assertions**

- all canonical fields survive fresh/memory/disk restart/public API;
- stock=800/fund=100 produces noncash=800;
- update success/create failure reports update IDs and no optimistic cache;
- regional exposure includes classified funds/ETFs without double count.

**Non-goals**

- no NAV formula/backfill changes in this slice;
- no live history repair.

**Completion signal**

Public NAV facts are lossless and batch outcomes are truthful.

### S8 — NAV calculation, daily/gap semantics, and safe repair/backfill

**Objective**

Make canonical write and legacy derived-field maintenance share one calculation and final invariant set without manufacturing historical base facts.

**Findings**

- F02 backfill double-counts fund and overwrites after validation
- F03 cash_flow has daily vs gap meanings

**Allowed files**

- src/domain/nav_calculator.py
- src/app/nav_record_service.py
- src/app/account_nav_recorder_service.py
- src/feishu/repositories/nav_history_repository.py
- src/maintenance/nav_history_repair/patch.py
- src/maintenance/nav_history_repair/backfill.py
- src/maintenance/nav_history_repair/common.py if required
- scripts/nav_history_repair.py
- tests/test_nav_calculator.py
- tests/test_nav_record_service.py
- tests/test_nav_history_patch.py
- tests/test_audit_fixes.py
- tests/test_entrypoint_consolidation.py

**Exact changes**

1. Define persisted/runtime valuation mapping in one domain function.
2. fund_value remains a subset of persisted stock_value/noncash.
3. cash_flow column is daily flow only.
4. gap flow, previous NAV date, inclusive/exclusive window, S6 dataset fingerprint, and contract version live in details.cash_flow_basis.
5. Final assert_nav_invariants validates total decomposition, nav/shares, weights, share change, daily/gap basis, PnL, and finality after all mapping/override logic.
6. Backfill treats input rows only as requested dates/expected base evidence. For each date it fresh-reads one complete canonical row, requires exactly one record_id, and treats identity plus total/cash/stock/fund/region decomposition as immutable observed facts. Input/remote base drift blocks the row before any plan is writable.
7. Repair and backfill create a FieldState-aware patch containing only derived fields/details allowed by the maintenance contract. Rollback stores Missing/Null/Value for only changed fields. Repository exposes one restricted field-patch method; maintenance never calls full `write_nav_record(s)`.
8. Legacy row without cash_flow_basis requires ledger dataset; no evidence means repair blocks.
9. Backfill/repair `persist=False` calls explicitly obtain a fresh target-date `CashFlowDatasetSnapshot` from the S6 builder; no maintenance path may fall back to cache or an implicit downstream scan.
10. Define `ClosedNavTarget` in the same calculation authority. It requires finite Decimal components, exact `total_value = cash_value + non_cash_value`, shares=0, nav=1, S6 daily/gap cash-flow basis, and `status=closed` finality; no default “all cash” or post-validation float rounding is allowed.
11. Backfill apply refuses a missing target date, duplicate date, `mode=upsert` creation, or any proposed base-field replacement. These cases report `historical_evidence_required` and perform zero write; creating/replacing historical base facts belongs to a separate authorized reconstruction work unit.
12. Derived-only apply and rollback use the same journal/CAS preflight, restricted field patch, and fresh readback. They preserve existing `evidence_version=legacy` and cannot set snapshot v2 complete.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s8 python3.12 -m pytest -q -p no:cacheprovider tests/test_nav_calculator.py tests/test_nav_record_service.py tests/test_nav_history_patch.py tests/test_audit_fixes.py tests/test_entrypoint_consolidation.py

**Expected assertions**

- stock=800/fund=100/cash=200 yields total=1000 and consistent NAV/weights;
- Friday NAV + weekend flow + Monday no flow persists daily=0 and gap>0 in details;
- field patch never includes untouched columns;
- post-validation override is impossible;
- legacy repair without ledger evidence blocks.
- CLOSED nonfinite or inconsistent decomposition blocks before repository preview/write, while a valid target carries the same cash-flow basis metadata.
- missing/duplicate target, base drift, and upsert-create each perform zero write and report `historical_evidence_required`;
- derived-only apply/readback/rollback changes only declared fields and retains legacy snapshot evidence.

**Non-goals**

- no historical row creation or base-value replacement;
- no new Feishu column.

**Completion signal**

All NAV-producing and repair paths satisfy one final invariant set.

### S9 — Replayable snapshot rows and required validation

**Objective**

Make a normalized valuation snapshot the sole input for NAV totals and persisted snapshot rows.

**Findings**

- F10 snapshot prices cannot replay market value
- F12 snapshot required price fields are optional and batch validation differs

**Allowed files**

- src/snapshot_models.py
- src/domain/snapshot_contracts.py
- src/app/valuation_service.py
- src/app/portfolio_read_service.py
- src/app/account_nav_recorder_service.py
- src/app/nav_initialization_service.py
- src/app/nav_record_service.py
- src/app/snapshot_service.py
- src/feishu/repositories/snapshots_repository.py
- src/portfolio.py
- tests/test_snapshot_service.py
- tests/test_snapshot_and_audit.py
- tests/test_valuation_service.py
- tests/test_decimal_valuation.py
- tests/test_feishu_client.py
- tests/test_nav_record_service.py
- tests/test_daily_nav_services.py

**Exact changes**

1. Add immutable NormalizedValuationSnapshot and normalized row factory. ValuationService owns construction and exposes a compatibility `PortfolioValuation` projection derived from it.
2. Normalize quantity once; preserve unit prices as Decimal(str(value)) without MONEY_QUANT.
3. Derive market_value_cny from the exact normalized quantity/cny_price and round only market value to 0.01.
4. PortfolioReadService returns `normalized_valuation` plus its compatibility projection. AccountNavRecorderService and NavInitializationService pass both through PortfolioManager; NavRecordService validates their shared digest and computes official totals from the normalized object. SnapshotService consumes that exact object rather than rebuilding rows from mutable holdings.
5. Exclude zero-quantity rows and record excluded count/key digest.
6. Require nonblank account/asset_id/broker/dedup and finite quantity/price/cny_price/market value for every persisted row.
7. Add replay invariant and full-row digest v2 canonical serialization.
8. S1 write contract validates both single and batch snapshot create.
9. `NormalizedValuationSnapshot.from_closed_input(ClosedNavTarget)` emits zero holding rows plus explicit manual cash/noncash components and source provenance. The compatibility projection and NAV values are derived from it; CLOSED may not accept a second independently calculated payload.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s9 python3.12 -m pytest -q -p no:cacheprovider tests/test_snapshot_service.py tests/test_snapshot_and_audit.py tests/test_valuation_service.py tests/test_decimal_valuation.py tests/test_feishu_client.py tests/test_nav_record_service.py tests/test_daily_nav_services.py

**Expected assertions**

- 12.345 × 10.123 persists a replayable 124.96;
- NAV total equals sum of normalized rows plus declared nonrow components;
- mutating the compatibility projection after normalization causes official persistence to fail instead of changing NAV or snapshot rows;
- None/NaN/Inf price fails before write;
- single/batch required validation matches;
- zero rows are excluded with provenance.
- CLOSED produces an empty holding-row set with digest-covered manual components and no direct float-built NAV payload.

**Non-goals**

- no delete/overwrite state machine;
- no live Number canary.

**Completion signal**

Every new snapshot row independently replays and NAV consumes identical normalized inputs.

### S10 — Snapshot exact-set, durability, and compensation recovery

**Objective**

Replace blind upsert with a recoverable exact-set state machine.

**Finding**

- F11 snapshot upsert leaves stale rows/fields and compensation closes early

**Allowed files**

- src/domain/snapshot_contracts.py
- src/app/snapshot_service.py
- src/feishu/repositories/snapshots_repository.py
- src/app/nav_record_service.py
- src/app/account_nav_recorder_service.py
- src/app/nav_initialization_service.py
- src/portfolio.py
- src/app/compensation_service.py
- skill_api.py
- tests/test_snapshot_service.py
- tests/test_snapshot_and_audit.py
- tests/test_nav_record_service.py
- tests/test_daily_nav_services.py
- tests/test_compensation_service.py

**Exact changes**

1. Validate one account/as_of slice and unique desired business keys.
2. Fresh-read remote slice and block duplicate remote keys.
3. Build deterministic create/update/clear/delete exact-set plan with before set, normalized target_digest, and plan_digest.
4. AccountNavRecorderService/NavInitializationService create a scoped SnapshotWriteAuthority after confirm checks and pass it through PortfolioManager/NavRecordService. First write disallows an existing slice; rewrite requires authority with overwrite_existing and confirmed.
5. Before NAV mutation, bind the authority to the exact plan and fsync a local-prepared HOLDINGS_SNAPSHOT_TARGET_SET event containing account/as_of/run_id/issuer/target_digest/plan_digest and overwrite scope. Do not mirror normal transient prepared events.
6. On NAV exception, fresh-read account/date: absent resolves no-replay, target match continues recovery, unknown stays pending.
7. Apply NAV, snapshot create/update/clear, then delete obsolete rows; record each confirmed/unknown stage.
8. Fresh-read exact set, verify replay and digest v2, then patch NAV snapshot details complete and resolve.
9. Compensation retry calls the same engine, reuses only the exact durable bound authority, and resolves only after exact readback. Scope/digest mismatch remains a conflict and performs no write.
10. Legacy final rows keep evidence_version=legacy. Canonical record/init/CLOSED new or explicit rewrite requires v2; S8 derived-only maintenance is not a valuation rewrite, preserves legacy evidence, and cannot claim v2 complete.
11. CLOSED uses the same engine with an exact empty holdings_snapshot target for its date. Existing same-day NAV or snapshot rows require the scoped overwrite authority; completion requires fresh readback of both the CLOSED NAV details and empty snapshot set.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s10 python3.12 -m pytest -q -p no:cacheprovider tests/test_snapshot_service.py tests/test_snapshot_and_audit.py tests/test_nav_record_service.py tests/test_daily_nav_services.py tests/test_compensation_service.py

**Expected assertions**

- A+B→A deletes B; optional value→null clears;
- empty target without overwrite authority blocks;
- overwrite without top-level confirm performs zero mutation;
- prepared authority scope/digest mismatch performs zero recovery mutation;
- NAV success/crash before snapshot is recoverable;
- NAV timeout but remote success does not cancel target;
- delete/readback failure remains partial;
- compensation exact readback is required for resolved.
- CLOSED performs no direct repository write, persists an exact empty snapshot set, and obeys the same overwrite/confirm recovery authority.

**Non-goals**

- no live destructive canary in source implementation;
- no historical exact-set rewrite.

**Completion signal**

New snapshot finality means exact remote set plus replayable v2 digest.

### S11 — Transactions archive enforcement

**Objective**

Make the public and repository contract match the declared legacy read-only role.

**Findings**

- F19 transactions writer/date/roundtrip is invalid
- F20 request_id scope differs

**Allowed files**

- src/feishu/errors.py
- src/feishu/repositories/transactions_repository.py
- src/feishu/_transactions_mixin.py
- src/feishu_storage.py
- src/models.py only if strict archive read requires a raw validation type
- tests/test_feishu_storage.py
- tests/test_trade_service.py
- tests/test_feishu_contracts.py

**Exact changes**

1. Add LegacyReadOnlyError and make all transaction add/delete surfaces fail before transport.
2. Preserve strict archive read; missing required values become validation errors, never BUY/CNY/0 defaults.
3. Any retained request lookup requires account + request_id, validates returned account, and is not advertised as writer idempotency.
4. Remove dead mutation capability from public storage protocols/callers.
5. Model observed Text date/type wire fields without schema migration.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s11 python3.12 -m pytest -q -p no:cacheprovider tests/test_feishu_storage.py tests/test_trade_service.py tests/test_feishu_contracts.py

**Expected assertions**

- all transaction mutations make zero Feishu requests;
- malformed archive rows do not manufacture defaults;
- two accounts sharing request_id do not cross-return.

**Non-goals**

- no ledger migration, date conversion, or new writer.

**Completion signal**

Transactions is observably and structurally read-only.

### S12 — Compensation current-state mirror

**Objective**

Make the optional Feishu mirror truthful without weakening local event-log authority.

**Finding**

- F24 compensation mirror remains PENDING

**Allowed files**

- src/app/compensation_service.py
- src/feishu_storage.py
- src/feishu/contracts/registry.py
- tests/test_compensation_service.py
- tests/test_feishu_storage.py

**Exact changes**

1. Local append/fold/fsync always precedes mirror.
2. Only mirror-eligible actionable tasks are mirrored; local-prepared successful operations are not.
3. Store mirror record_id in local event metadata. If absent, fresh lookup by task_id: zero create, one update, multiple warn/fail mirror only.
4. Best-effort update current status, retry_count, updated_at, resolved_at, resolution, and error after each fold.
5. Mirror failure appears in receipt/metric but never rolls back local state or recursively creates compensation.
6. If table is unconfigured, return explicit skipped_unconfigured rather than false success/failure.

**Tests**

- PYTHONPYCACHEPREFIX=/tmp/pm_s12 python3.12 -m pytest -q -p no:cacheprovider tests/test_compensation_service.py tests/test_feishu_storage.py

**Expected assertions**

- PENDING→RUNNING→FAILED/RESOLVED mirrors current fold when configured;
- unconfigured table is explicit skip;
- duplicate task_id and remote error preserve local truth;
- transient successful prepared target creates no mirror.

**Non-goals**

- no live table creation;
- no cross-host compensation store.

**Completion signal**

Mirror semantics, implementation, and schema contract agree.

## Dependency Order

    accepted plan
         |
         v
        S1
         |
         +--> S2 --> S3
         |
         +--> S4 --> S5 --> S6
         |                   |
         +------------------>S7 --> S8
         |                           |
         +-------------------------->S9 --> S10
         |
         +--> S11
         |
         +-------------------------------> S12

Additional constraints:

- S8 consumes S6 cash dataset and S7 complete NAV reads.
- S9 consumes S2 canonical holding identities and S7/S8 NAV semantics.
- S10 consumes S9 normalized snapshots and S12 mirror eligibility semantics may be implemented after S10; until S12, local durability remains authoritative.
- S11 may begin after S1 but is sequenced late to minimize shared-file conflicts.

## Finding Ownership Matrix

| Finding | Owner |
|---|---|
| F01 | S7 |
| F02 | S8 |
| F03 | S8 |
| F04 | S3 |
| F05 | S2 |
| F06 | S6 |
| F07 | S3 |
| F08 | S3 |
| F09 | S2 |
| F10 | S9 |
| F11 | S10 |
| F12 | S9, enabled by S1 |
| F13 | S7 |
| F14 | S1 |
| F15 | S4 |
| F16 | S5 |
| F17 | S5 |
| F18 | S7 |
| F19 | S11 |
| F20 | S11 |
| F21 | S4 |
| F22 | S4 |
| F23 | S1 |
| F24 | S12 |
| F25 | S2 |
| F26 | S1 |
| F27 | base verification only |
| F28 | base verification only |
| F29 | S4 |
| F30 | S4 |
| F31 | S4 |
| C01 live select-option/domain drift | S1 |

F12 closure owner is S9; S1 supplies the common transport validator. All other unresolved findings have exactly one closure owner.

## Review and Validation Strategy

### Per-slice

- Add failing regression first where practical.
- Run the slice command listed above.
- Run python3.12 -m compileall for touched Python modules.
- Run generated-doc check if S1 registry/docs changed.
- Run git diff --check.
- Create implementation artifact.
- Invoke deepreview for current slice.
- Fix accepted findings, re-review, classify every residual risk, and create accepted slice commit.

### Aggregate

Run:

- PYTHONPYCACHEPREFIX=/tmp/pm_feishu_contract_full python3.12 -m pytest -q -p no:cacheprovider
- PYTHONPYCACHEPREFIX=/tmp/pm_feishu_contract_compile python3.12 -m compileall -q src scripts skill_api.py
- python3.12 scripts/generate_feishu_schema_docs.py --check
- python3.12 scripts/migrate_schema.py expectations
- git diff --check

Then use deepreview on the entire work unit from origin/main to HEAD, fix accepted findings, re-review, and create the accepted deepreview commit.

### External protocol evidence before production use

Not part of source implementation or Draft PR acceptance:

- repeat read-only live schema comparison;
- separately authorized read-only business-row conformance audit for blank broker, invalid enum/select values, duplicate business keys, and incomplete cash-flow/snapshot rows;
- separately authorized nonproduction canary for null clear;
- separately authorized nonproduction exact-set create/update/delete/readback canary;
- Number round-trip precision canary.

No source test may weaken an invariant merely because an external canary has not run.

## Docs Decision

- docs/schema.md generated contract blocks change in S1.
- Human manual-edit policy remains hand-maintained but is validated against registry field names.
- docs/feishu-bitable-contract-repair-plan.md is the design rationale.
- docs/gateflow/feishu-bitable-contract-repair contains goal, plan, implementation/review/fix/re-review artifacts, live schema evidence, and final closeout.
- Historical review artifacts remain immutable.

## Risks and Open Questions

### Classified risks

- Live select options may drift after the evidence read: handled by repeatable strict comparison; production use gate.
- External edit between fresh read and write: recorded as as-of provenance; stronger cross-table consistency belongs to a later concurrency work unit.
- Historical invalid/duplicate rows: assigned to a separately authorized data-repair work unit after source deployment.
- Historical NAV creation or base-value reconstruction: assigned to a separately authorized reconstruction work unit requiring date-bound normalized valuation and snapshot evidence; current maintenance is derived-only.
- Existing row compatibility is unknown because no business rows were read: assigned to a separately authorized read-only pre-deployment conformance audit; source remains fail-closed.
- Local compensation log host loss: assigned to operations/backup work, not this source repair.
- Legacy transaction caller discovered during S11: explicit slice stop condition and user decision.
- Feishu Number precision mismatch: external canary stop condition; no automatic schema migration.

### Blocking open questions

None at revised plan. The planreview questions are resolved by the explicit S6 dataset builder/window, S9 normalized transmission contract, and S10 scoped authority contract.

## Why the Plan Is Not Overdesigned

- The 12 slices are small review boundaries around demonstrated failure clusters, not 12 deployable services.
- The registry centralizes duplicated static metadata; formulas remain ordinary typed Python functions.
- Immutable datasets are introduced only for two existing TOCTOU/replay failures.
- Existing repository, storage, error, cache, compensation, receipt, and finality mechanisms are extended rather than replaced.
- Live schema/data migration is explicitly excluded.

## Completion Report Format

Final closeout must report:

- branch/base and accepted commit list;
- files/modules changed by slice;
- finding closure matrix, including F27/F28 base verification and C01;
- scoped/full validation commands and results;
- generated docs decision;
- live reads/writes performed;
- remaining risks with owner/destination;
- Draft PR URL and draft status;
- issue linkage status: not applicable unless an issue is later supplied;
- explicit statement that merge/release/deploy/data repair remain pending authorization;
- next entry point after the user merges the Draft PR.

## Next Gate

accepted plan commit, then S1 implementation
