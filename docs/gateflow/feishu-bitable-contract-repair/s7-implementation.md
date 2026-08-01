# Gateflow S7 Implementation — Complete NAV Facts and Stage-aware Writes

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S7
- Base: `433f04a`
- Recorded at: 2026-08-02T04:44:55+08:00
- Status: accepted after final S7 re-review
- Artifact path: `docs/gateflow/feishu-bitable-contract-repair/s7-implementation.md`

## Scope

Production changes are limited to the S7 allowlist:

- `src/feishu/repositories/nav_history_repository.py`
- `src/app/account_service.py`
- `src/app/portfolio_read_service.py`
- `src/app/report_query_service.py`
- `src/app/reporting_service.py`
- `src/app/audit_service.py`

Regression changes are limited to the S7 test allowlist, including the new
`tests/test_account_service.py`. The unrelated untracked
`docs/reviews/code-review-20260801-084655.md` remains excluded and untouched.

## Implemented Contract

- The canonical Feishu table registry now supplies the complete NAV read
  projection. Its historical `NAV_INDEX_PROJECTION_FIELDS` name remains only
  as a compatibility alias and no longer means a partial row.
- `nav_history` memory/disk rows serialize every `NAVHistory` field plus
  record ID and update timestamp. A version marker rejects old lossy cache
  payloads and replaces them with a fresh complete read.
- Lightweight `date_identity_index`, month/year bases, inception, and latest
  rows carry only date/account/record identity. Public reads reconstruct their
  objects only from complete canonical cache rows.
- Incremental writes republish a complete versioned cache payload, so both the
  row store and derived identity indexes agree across process restart.
- Batch update and create stages retain mutually exclusive confirmed, failed,
  and unknown target scopes. A stage-aware `FeishuBatchWriteError` also records
  the failed stage, partial-write possibility, and fresh-cache rebuild outcome.
- A later create failure after confirmed updates is raised even when
  `allow_partial=True`; it cannot be converted into a false zero-write result.
  The repository first discards memory/disk authority, then rebuilds from a
  fresh complete Feishu read without publishing the optimistic create row.
- Legacy `FieldNameNotFound: details` compatibility retries keep cache state
  aligned with what was actually sent: create has no details, while update
  retains the prior remote details.
- Remote and versioned-cache rows must carry their own record ID and account;
  scoped reads reject account mismatch. Invalid date or identity clears cache
  authority and blocks public reads, audit success, and write planning.
- Scoped and global duplicate audits validate the same complete canonical rows.
- Internal consumer variables distinguish runtime equity/fund categories from
  persisted non-cash value. Persisted `stock_value` is never added to its
  `fund_value` subset. Runtime values form non-cash from the two disjoint
  categories before entering the compatibility NAV boundary.
- Regional values are named as exposure internally and remain compatible with
  Feishu `cn/us/hk_stock_value`; classified funds and ETFs may contribute to
  those exposure totals without a second fund addition.

## Validation

- Focused batch/cache suite: `38 passed`.
- S7 scoped suite plus the new account contract tests: `80 passed`.
- Expanded NAV/report/service regression: `193 passed`.
- Scope-corrected legacy NAV storage class: `8 passed`.
- Full repository suite: `1260 passed` with `FEISHU_APP_TOKEN` unset and dummy
  app credentials.
- Scoped Ruff, Python compileall, and `git diff --check`: passed.
- No live Feishu/Futu request, schema mutation, business-data write, release,
  deployment, or merge occurred.

## Expected Assertions Closed

- Every canonical NAV field survives fresh load, memory reuse, versioned disk
  restart, and public repository reads.
- A persisted `stock_value=800` with `fund_value=100` yields
  `non_cash_value=800` and equity-only report value 700.
- A confirmed update followed by an unknown create reports the update record
  ID and unknown create date; an explicit schema rejection instead reports a
  failed create target. Both retain the failed stage and a fresh rebuilt cache
  with no optimistic create row.
- Runtime regional exposure can include classified funds/ETFs and remains
  distinct from equity/fund category totals.

## Residual Risks

- Feishu does not provide an atomic transaction across the update and create
  endpoints. Stage scopes and fresh readback make the outcome observable but
  do not roll back a confirmed prefix.
- A fresh readback can still race an external editor; cross-host CAS and
  distributed snapshot isolation remain outside this work unit.
- S8 owns NAV formula, repair/backfill, daily/gap cash-flow, and CLOSED target
  invariants; S7 deliberately does not rewrite historical rows.

## Review Closure

- Initial DeepReview: `docs/reviews/code-review-20260802-044903.md`.
- First aggregate re-review: `docs/reviews/code-review-20260802-050054.md`.
- Accepted final re-review: `docs/reviews/code-review-20260802-050654.md`
  (`未发现实质性问题`).

## Next Gate

Create the accepted local S7 commit, then begin S8 from that exact commit.
