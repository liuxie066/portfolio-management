# Gateflow Final Closeout — Feishu Bitable Contract Repair

## Gate Metadata

- Work unit: `feishu-bitable-contract-repair`
- Branch: `gateflow/feishu-bitable-contract-repair`
- Historical implementation base:
  `59625b0d6c666da338c0a520e85a221932846949`
- Current Draft PR base:
  `main@cf47cd92848036c30e7212c1925ec53a4c882a6d`
- Accepted reviewed source/documentation head:
  `e5269a3796ad1197c5ddca2a3693932b87e08094`
- Draft PR: `https://github.com/liuxie066/portfolio-management/pull/45`
- Recorded at: `2026-08-02T10:23:33+08:00`
- Status: source work unit complete; Draft PR remains pending user decision
- Artifact path:
  `docs/gateflow/feishu-bitable-contract-repair/final-closeout.md`

## Outcome

The source implementation is complete and accepted inside the confirmed Draft
PR boundary. Feishu Bitable definitions are now a layered unique source of
truth rather than a second independently maintained schema:

1. Domain models and pure functions own business meaning: requiredness,
   identity, enum/lifecycle domains, authority, formulas, precision, and
   cross-field invariants.
2. `src/feishu/contracts` owns the structural Bitable projection: remote field
   names and types, select options projected from domain owners, operation
   capabilities, business keys, encoding, ownership metadata, and schema
   expectations.
3. Clients, repositories, services, and maintenance flows consume those two
   layers for raw preservation, typed promotion, validation, serialization,
   fresh readback, exact-set behavior, and cache publication.
4. Generated documentation and contract tests are derived conformance evidence;
   they are not alternative definition authorities.

This is deliberately not one monolithic schema file. Structure and business
semantics each have one owner, and executable projection checks prevent drift
between them.

## Accepted Commits

| Gate | Commit | Decision |
|---|---|---|
| Plan | `e45a30ec8934162a77396d7f46938ddfe9b8ff8d` | accepted |
| S1 | `4af82f64f5bbc7bed14e18ebe495eaf99de992a0` | accepted |
| S2 | `5ac4756bfc2e49eead0e12804b71abd50ba77460` | accepted |
| S3 | `8ef9c8c74e9e844fe8d38f82831b8a36e7c3a0ed` | accepted |
| S4 | `8c9266ee549cbd2d26e6e3602586039adfe77d0d` | accepted |
| S5 | `cca91c66cb42909040d7e1cedf9ccd1227b4da38` | accepted |
| S6 | `433f04a1d6f7b849a3839185dad21a1e31aa9e65` | accepted |
| S7 | `6cbe4110b72f0d5c5e212890207ff8859a9490b1` | accepted |
| S8 | `833fcb08d3c895e82619f7162d9ec6a4ca18bf34` | accepted |
| S9 | `dc807bc59e552f9d1786936b6526121d40def44c` | accepted |
| S10 | `803bf3bdb3dc110f7d8cb909b64d0a4244d7f241` | accepted |
| S11 | `b33ff48973707a8076d433bbb6722853d4cf2855` | accepted |
| S12 | `58b03beb7e7e19a95a0d439d4c1271456b41b241` | accepted |
| Aggregate review/fix | `02906c2829410e6e40ddacf251253712ba8f3c34` | accepted |
| Current-main integration | `09cd07b72c9d5ae6a07d9f77dc2761c91c977584` | accepted |
| Actual PR review | `e5269a3796ad1197c5ddca2a3693932b87e08094` | accepted |

The current-main integration incorporates the already merged dual-App
credential work from `main@cf47cd9` while preserving this work unit's canonical
table-reference and Bitable-contract boundaries.

## Slice-to-Module Map

| Slice | Contract | Principal production/tooling modules |
|---|---|---|
| S1 | Structural registry and schema projection | `src/feishu/contracts/{models,registry}.py`, `src/config.py`, `src/feishu_client.py`, `src/feishu_storage.py`, `src/feishu/errors.py`, `src/models.py`, `scripts/generate_feishu_schema_docs.py`, `scripts/migrate_schema.py`, `docs/schema.md` |
| S2 | Holdings identity, tri-state patch/target, fresh-base and readback proof | `src/domain/holding_mutations.py`, `src/feishu/repositories/holdings_repository.py`, `src/feishu/_holdings_mixin.py`, holdings/cash/effect/compensation services, `skill_api.py` |
| S3 | Strict Futu source snapshot and economic-exposure authority | `src/domain/holdings.py`, `src/app/holdings_validation.py`, `src/app/futu_balance_sync_service.py`, `src/app/futu_sync_reconciler.py` |
| S4 | Cash-flow raw/completed facts, Decimal aggregation, complete reads and creates | `src/domain/cash_flow_contracts.py`, `src/feishu/repositories/cash_flow_repository.py`, `src/feishu/_cash_flow_mixin.py`, cash-flow summary/effect services, `src/models.py` |
| S5 | Cash-flow duplicate/FX reconciliation and observed readback | `src/app/cash_flow_fx_confirmation.py`, cash-flow repository/domain contracts, `scripts/pm.py` |
| S6 | Immutable run-scoped cash-flow dataset consumed by official NAV paths | `src/domain/cash_flow_contracts.py`, cash-flow summary/effect services, account/daily/init/NAV record services, `src/portfolio.py`, `skill_api.py` |
| S7 | Complete canonical NAV reads and lossless cache/index publication | `src/feishu/repositories/nav_history_repository.py`, account/read/report/audit services |
| S8 | Canonical NAV calculation/finality and derived-only maintenance | `src/domain/nav_calculator.py`, `src/domain/nav_finality_contract.py`, NAV finality/record services, NAV repository, `src/maintenance/nav_history_repair/*`, `scripts/nav_history_repair.py`, `skill_api.py` |
| S9 | Replayable normalized valuation and snapshot rows | `src/domain/snapshot_contracts.py`, `src/snapshot_models.py`, valuation/read/NAV/snapshot services, snapshot repository, `src/portfolio.py` |
| S10 | Account/date snapshot exact-set authority and durable recovery | snapshot domain/service/repository/mixin, NAV/init/account services, `src/app/compensation_service.py`, `src/portfolio.py` |
| S11 | Strict read-only transactions archive | transaction repository/mixin, `src/feishu_storage.py`, registry/errors/models, generated schema |
| S12 | Local-authoritative compensation lifecycle with best-effort current-state mirror | `src/domain/compensation_contracts.py`, `src/app/compensation_service.py`, `src/feishu_storage.py`, registry, generated schema |
| Aggregate | Cross-layer zero-drift fixes for holdings name, cash-flow create contract, and snapshot identity | holdings validation, cash-flow event/domain contracts, snapshot domain/model/repository, registry models/projections, generated schema |

Each slice also added or changed its corresponding deterministic unit,
repository, service, CLI, integration, and contract tests. The slice artifacts
record their exact file lists and review chains.

## Finding Closure Matrix

All 29 originally unresolved findings are closed in source. F27 and F28 were
already fixed on the accepted base and were verified rather than reimplemented.
C01 is closed at the source-contract layer; a repeat live comparison remains an
operational pre-production gate because external schema can drift.

| Finding | Closure owner | Accepted evidence | Status |
|---|---|---|---|
| F01 | S7 | complete NAV projection/index/read path, `6cbe411` | closed |
| F02 | S8 | canonical valuation mapping and final invariant check, `833fcb0` | closed |
| F03 | S8 | daily flow plus versioned gap-flow basis, `833fcb0` | closed |
| F04 | S3 | provider numeric tri-state and complete-slice validation, `8ef9c8c` | closed |
| F05 | S2 | explicit clear plus fresh holdings readback, `5ac4756` | closed |
| F06 | S6 | immutable fresh cash-flow dataset for NAV, `433f04a` | closed |
| F07 | S3 | reconciliation consumes fresh remote account slice, `8ef9c8c` | closed |
| F08 | S3 | shared economic-exposure authority, `8ef9c8c` | closed |
| F09 | S2 | complete `(asset_id, account, broker)` mutation identity, `5ac4756` | closed |
| F10 | S9 | price precision and row replay invariant, `dc807bc` | closed |
| F11 | S10 | snapshot exact-set plan, residual actions, and readback, `803bf3b` | closed |
| F12 | S9, enabled by S1 | strict row/model and single/batch create validation, `dc807bc` | closed |
| F13 | S7 | explicit partial-write evidence and complete NAV repository semantics, `6cbe411` | closed |
| F14 | S1 | strict executable registry/schema comparison, `4af82f6` | closed |
| F15 | S4 | raw missing/null/value preserved before typed promotion, `8c9266e` | closed |
| F16 | S5 | duplicate audit precedes manual completion, `cca91c6` | closed |
| F17 | S5 | FX date/provenance validation precedes write and confirmation, `cca91c6` | closed |
| F18 | S7 | canonical persisted valuation semantics exposed by NAV reads, `6cbe411` | closed |
| F19 | S11 | transactions mutation tombstones and strict archive model, `b33ff48` | closed |
| F20 | S11 | account-scoped `(account, request_id)` archive identity, `b33ff48` | closed |
| F21 | S4 | complete cash-flow projection includes remark/source facts, `8c9266e` | closed |
| F22 | S4 | one domain validation path for finite/nonzero/completed facts, `8c9266e` | closed |
| F23 | S1 | only explicit not-found maps to absence, `4af82f6` | closed |
| F24 | S12 | local-authoritative folded lifecycle and mirror upsert receipts, `58b03be` | closed |
| F25 | S2 | canonical holding copy drives payload, result, identity, and cache, `5ac4756` | closed |
| F26 | S1 | one strict `parse_table_ref`, `4af82f6` | closed |
| F27 | accepted base verification | outbound subscription payload regression on `59625b0`, preserved through `cf47cd9` | base-fixed and verified |
| F28 | accepted base verification | tag JSON-text normalization regression on `59625b0`, preserved through `cf47cd9` | base-fixed and verified |
| F29 | S4 | exact reader preserves observed dedup key; readback compares it, `8c9266e` | closed |
| F30 | S4 | incomplete foreign-currency create fails before write/cache, `8c9266e` | closed |
| F31 | S4 | missing date blocks every aggregate view, `8c9266e` | closed |
| C01 | S1 | live metadata baseline plus strict enum/select projection, `4af82f6` | source closed; repeat live gate retained |

### Aggregate Review Findings

| Finding | Closure | Status |
|---|---|---|
| DR-AGG-01 | holdings required fields project from registry; missing `asset_name` cannot be defaulted into typed truth | closed |
| DR-AGG-02 | cash-flow domain owns manual/completed fields and flow types; registry create contract is a projection and requires business keys | closed |
| DR-AGG-03 | snapshot domain owns key order and dedup; model/registry/payload/digest coverage is exact | closed |

The aggregate re-review and post-main-integration review found no additional
material findings.

## Validation and Review Evidence

| Gate | Result |
|---|---|
| Aggregate focused regression | `176 passed` |
| Pre-main-integration full repository suite | `1375 passed` |
| Incoming-main integration regression | `248 passed` |
| Final merge-focused regression | `178 passed` |
| Final source full repository suite | `1415 passed` |
| Ruff | passed for scoped clean/touched surfaces; recorded legacy exclusions unchanged |
| Python compileall | passed for `src`, `scripts`, and `skill_api.py` |
| Generated schema | `python3.12 scripts/generate_feishu_schema_docs.py --check` passed |
| Migration contract | `python3.12 scripts/migrate_schema.py expectations` passed |
| Diff/merge hygiene | exact-remote `git diff --check`, merge-marker, and unmerged-entry checks passed |
| Actual PR inventory | GitHub API 205 files; exact remote-ref diff 205 files |
| Actual PR review | no material findings; `docs/reviews/pr-45-review-20260802-101856.md` |
| GitHub CI on accepted PR-review head | `quality-contract` passed in 23 seconds, run `30728698665` |

GitHub's aggregate PR diff endpoint returns HTTP 406 because the diff exceeds
20,000 lines. The review used all three paginated PR-file API pages and the
exact remote refs; the API display limit is not an unreviewed scope gap.

## Generated Documentation Decision

- The contract block in `docs/schema.md` is generated from the canonical
  registry between stable markers.
- Human operating policy remains hand-maintained, but tests validate its field
  references against the registry.
- `scripts/generate_feishu_schema_docs.py --check` prevents generated drift.
- `scripts/migrate_schema.py expectations` consumes the same contract
  expectations rather than a separate field definition list.
- `docs/feishu-bitable-contract-repair-plan.md` records design rationale;
  `docs/gateflow/feishu-bitable-contract-repair/` records Gateflow decisions,
  implementations, reviews, fixes, re-reviews, live evidence, and this closeout.
- Historical review artifacts remain immutable.

## Live Access and Mutation Ledger

| Operation | Performed | Evidence/boundary |
|---|---:|---|
| Live Feishu field-metadata read | yes, once | authorized read-only evidence at `2026-08-01T22:59:33+08:00`; recorded in `live-schema-baseline.md` |
| Live Feishu business-record read | no | zero rows read |
| Live Feishu business-record write/delete | no | zero mutations |
| Live Feishu schema/option mutation | no | zero mutations |
| Live Futu request | no | deterministic fixtures only |
| Token/table identifier persistence in artifacts | no | baseline stores neither |
| Historical data repair/apply | no | separate authorization boundary |
| Service/config runtime change | no | source branch only |

The metadata observation covered configured core and archive table field
structures. `compensation_tasks` and `schema_version` were unconfigured at that
time. Because live state can drift, this observation is evidence for the plan,
not a timeless production guarantee.

## Residual Risks and Owners

| Residual | Owner/destination | Required gate |
|---|---|---|
| Live field type/select-option drift after baseline | production operator | repeat separately authorized read-only schema comparison before production use |
| Blank broker, invalid enum/options, duplicate business keys, or incomplete legacy cash-flow/snapshot rows | production operator, then data-repair work unit if found | separately authorized read-only business-row conformance audit |
| Physical null clear and exact-set create/update/delete/readback semantics | nonproduction operator | separately authorized nonproduction canaries |
| Feishu Number round-trip precision | nonproduction operator | separately authorized precision canary; stop on mismatch |
| Historical invalid/duplicate rows | data-repair work unit | explicit repair design and write authorization |
| Historical NAV base-value reconstruction | reconstruction work unit | date-bound normalized valuation/snapshot evidence and explicit write authorization |
| Cross-host uniqueness and external-editor races | future reliability work unit | stronger coordination/remote conflict design |
| Autonomous compensation mirror replay | future reliability work unit, only if promoted | explicit product/operations decision |
| Local compensation JSONL host loss | operations/backup owner | durable backup/restore control |
| Optional compensation mirror remains best effort | current contract; future product work if stronger SLO is required | no source-completion claim beyond local authority |
| Release, deployment, runtime upgrade, and production verification | user plus release/deployment workflow | separate explicit authorization |

These are classified follow-on gates, not hidden claims that deterministic
source tests prove external state.

## PR and Authorization Boundary

- PR #45 is `OPEN`, `Draft`, and was `MERGEABLE` at PR review.
- Issue linkage: not applicable; no issue was supplied.
- The branch was pushed only to the existing Draft PR.
- The PR was not marked ready and reviewers were not requested.
- `main` was not merged or mutated by this work unit.
- No version, tag, GitHub Release, release artifact, deployment, remote upgrade,
  restart, production notification, schema migration, or data repair was
  performed.

## Next Entry Point

The next decision belongs to the user: keep the PR in Draft for inspection or
separately authorize Ready/merge. After the user merges the Draft PR, begin a
new, separately authorized operational work unit from updated `main`; its first
gate is repeat read-only live-schema comparison, followed by read-only
business-row conformance and only then the explicitly authorized nonproduction
wire canaries. Merge does not imply release, deployment, runtime upgrade, or
business-data repair.
