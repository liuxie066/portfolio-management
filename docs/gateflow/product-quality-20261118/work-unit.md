# PM product quality pass — 2026-11-18

## Scope and evidence

User authorized an independent, comprehensive audit, necessary improvements, isolated
synthetic verification, independent review, commit/push and a reviewable PR. No
production reads/writes, migration work, merge, release or deployment. Worktree:
`vibrant-bohr`, branch `cindy/vibrant-bohr`; clean start and refreshed main both
`43def4c` (v0.2.14). The parallel migration task and other checkouts are untouched.

Product surfaces actually present: CLI, local HTTP API, Feishu tables/receipts and
static daily HTML. There is no interactive portfolio web application to redesign.
Feishu remains the ledger authority, SQLite the workflow store; transactions are
a legacy read-only archive. Existing dry-run/confirmation/finality rules remain.

## Page decisions

1. First show whose report this is, its NAV date, snapshot and recording status,
   total assets and daily PnL. A preview must never look like a recorded NAV.
2. Explain returns with cash flow, period PnL, principal holdings, actual broker
   distribution, valuation notes and source identifiers. Unknown is not zero.
3. Help the reader judge the result, inspect notes and trace its source. A static
   report does not initiate ledger writes; existing CLI/Feishu workflows own them.

Reading order: account/date/status → valuation notes → assets/daily change →
period returns → principal holdings → broker distribution → audit details.
Chinese labels, tabular right-aligned amounts, independent positive/negative
period values, semantic tables, keyboard navigation, responsive data preservation,
dark/print styles. Single-file output, no CDN/scripts/fonts or new dependency.

## Plan and priority

One implementation unit; independent exploration covers experience, reliability
and performance while the parent implements and verifies the combined result.

- P1: preserve report account/preview and unknown values; surface source notes.
- P1: reproduce and fix stale capital bridge NAV reads using the established
  fresh-read boundary, preserving account scope and calendar/accounting rules.
- P2: refine the existing report layout, labels, mobile and accessible states.
- P2: measure synthetic NAV/report computation and remove proven redundant work
  without new cache or changed financial calculations; retain peak measurements.
- Inspect CLI and receipts, concurrency/recovery, config/auth and shared API
  boundaries; only fix concrete issues supported by evidence.

Acceptance: synthetic before/after HTML and browser evidence, same-environment
performance comparison with output equality, regression cases for each behavior
fix, full pytest/compileall/Ruff/diff/OpenAPI checks, independent final review and
necessary fix/re-review, exact-head CI and PR delivery verification.

Plan checked against the user's explicit goal and autonomous authorization.
Inspection reproduced a shared completion bug: cash effects and quality accept
finality metadata on NAV whose snapshot is explicitly incomplete. Extend the
existing read-only `evaluate_nav_finality` decision to reject explicit incomplete
snapshot evidence, retaining legacy final rows without snapshot markers. Its
only consumers are daily-job, cash effects and quality. Daily-job continues to
provide its richer recovery result before evaluating finality. No writer or
schema change. Add caller-level failed/prepared/complete/legacy regressions.

Freshness: capital facts and quality producer must request canonical NAV reads,
not stale per-process rows. Cash-effect history presence is scoped to each scan,
and confirmation refreshes again before the existing finality recheck. Account
scope, errors and missing-data gates remain strict. The quality HTTP artifact
reader remains read-only and does not refresh sources. The cost is an additional
canonical read where previously cached facts could be stale. Independent plan
review applies to these shared trust decisions before their implementation.

Plan review PR-FIN-01 accepted: clearing the service memo alone cannot invalidate
the storage cache. `_has_nav_history` will explicitly call the existing real
storage `get_nav_history(..., fresh=True)` (no cached compatibility fallback).
That read replaces the account in-memory index consumed by `get_nav_on_date`.
Reset the per-operation presence memo at scan/preview entry; confirm already
evicts the account before its write gate and must perform a new canonical read.
Tests will exercise complete → failed and absent → present transitions through
real FeishuStorage with fake canonical rows, plus read failure (no CASH write).
No long-lived negative memo or cached point read can replace that fresh read.

## Status

Local implementation, validation and independent review are complete. Next
entry: PR creation and exact-head delivery verification. Final remote receipts
belong to the PR description/status and local `reports/product-quality/delivery.json`;
they are not a reason to create another source commit after CI.

Full suite: 2,017 passed (one dependency deprecation warning). Compileall, existing
Ruff 0.15.14, OpenAPI drift check and diff whitespace checks passed. The optional
new Ruff 0.17.5 check failed on both base and change; see `validation.txt`.

Independent review records are archived here from their timestamped local
`docs/reviews/` originals (normally gitignored): `plan-review.md`,
`review-complete.md`, `review-product.md`. Accepted findings and affected
re-reviews all completed:

| Finding family | Resolution | Final status |
| --- | --- | --- |
| Cached NAV despite presence-memo reset | Explicit account canonical read on scan/preview/confirm and read-only evidence consumers | 已修复 |
| Read outage mistaken for changed cash source | Gate evaluation outside source-normalization catch; transaction failure does not supersede facts | 已修复 |
| Missing quote coerced upstream into zero | Shared projection preserves null aggregates/weights across daily/full consumers, true zero retained | 已修复 |
| Malformed snapshot marker crashes or claims completion | Any explicit non-null non-complete status blocks; absent/null legacy status remains compatible | 已修复 |
| Default account alias applied to another bundle | Bind configured label to its account; explicit CLI label remains intentional | 已修复 |
| Receipt implies complete history query | Label only pending items, show cap, omit action for automatically accepted baseline | 已修复 |

No accepted finding remains unresolved. No new contract/schema/migration/SDK,
version metadata or release changes. `top_holdings` nullable evidence is within
the existing flexible row schema and documented for API consumers.

## Coverage and conclusions

| Surface / concern | Inspected path and verification | Result / priority |
| --- | --- | --- |
| Daily HTML, desktop/mobile/a11y | publisher → renderer; synthetic JSON and browser | Fixed P1 account/recording ambiguity, unknown-as-zero, buried notes; P2 labels, hierarchy, numeric alignment, mobile preservation, light/dark/print |
| CLI/operator output | `scripts/pm.py`, CLI integration suite | Fixed missing/invalid amounts shown as zero; JSON unchanged. Existing JSON detail remains available for review/preview/confirm; no new command UI framework |
| Feishu receipts and manual workflows | notification shells, cash-effect/NAV/holding receipt builders | Existing type/status/recovery commands preserved. Discovery now labels its 10-row limit and points to full review. No messages sent |
| Holdings / cash / transaction archive | table repositories, mutation services, frozen cash-flow dataset and tests | Account revalidation present in holdings, NAV, cash; Decimal formal path retained. Optional transaction archive remains read-only |
| NAV completion / recovery | recorder → snapshot readback/compensation → daily-job/cash effects/quality | Fixed P1 explicitly incomplete snapshots accepted as final; shared evaluator preserves existing richer recovery and legacy finals |
| Cross-process freshness | real FeishuStorage + fake canonical writer | Fixed P1 stale capital/quality and cash-effect history decisions; errors block rather than reuse stale trust |
| Concurrency / idempotency | account process locks, operation state/outboxes, NAV recovery tests | Existing locks, durable acceptance, terminal duplicates and retry semantics retained; no new locks/cache/state machine |
| Config / auth / isolation | config, bind, HTTP middleware, quality bearer token; HTTP tests | Loopback and dedicated read token retained; `/health` still only process health. No secrets or actual accounts used |
| Performance / redundant work | report payload and shared return calculator profile | Removed two history sorts and per-row date formatting; identical report payload; measured allocation peak |
| Cold NAV index / request I/O | canonical repository read/index and audit paths | Duplicate decode/index work measured; deferred below. Fresh canonical reads deliberately retained |
| Startup / resident runtime | isolated import/help and synthetic ASGI probes | Measured separately; no production memory or latency claims |
| Public API / schema consumers | unchanged OpenAPI export and full service/contract suite | No route, schema, migration or shared SDK change; OM consumers benefit from fresh facts within existing shape |

### Decisions and remaining limitations

- The first mobile preview wrapped monetary decimals at 320 px. Switched narrow
  financial metric grids to one column; preserve readable values over density.
- Broker totals are display projections of the supplied snapshot, not a new
  ledger calculation. Missing subtotals/denominators remain unknown.
- Freshness fixes add one canonical NAV read per affected request/account scan;
  quality also retains its independent duplicate audit. This is a correctness
  cost, not a claimed I/O speedup. Feishu cross-table atomicity is unchanged.
- Optional cold-index duplicate model reconstruction is assigned to later work
  (owner: PM maintainers, destination: performance backlog in this record). It
  is nonblocking: bounded synthetic cost, current source validation preserved;
  sharing its assembler safely warrants separate diagnostic/duplicate review.
- Full source-by-source exhaustive audit is not claimed. Coverage is by actual
  product/workflow and risk boundary, supported by the full automated suite.
- No new portfolio frontend, chart framework, dependencies, live fetches or
  unverified benchmark extrapolation. Existing styling is extended in place.

## Uncovered environments

Production timings, actual Feishu layouts/delivery, broker login/trading and live
migration behavior are outside authorization. Owner: operator, destination:
separately authorized deployment acceptance. Synthetic results are not production
benefit claims. No production acceptance is required for this PR.
