# Gateflow S7 Fix Artifact — DeepReview Findings

- Gate: fix
- Work unit: feishu-bitable-contract-repair
- Slice: S7
- Base: `433f04a`
- Review artifact: `docs/reviews/code-review-20260802-044903.md`
- Recorded at: 2026-08-02T04:57:00+08:00
- Status: initial and first re-review findings accepted and fixed; pending final re-review

## Finding Decisions

### DR-S7-01 — accepted — fixed

The complete NAV builders no longer skip malformed source rows. A stable
`NavHistoryReadIntegrityError` reports source, account, record ID, row index,
and the underlying reason. Remote preload, account-scoped duplicate audit, and
versioned disk replay all fail closed and discard both memory and disk cache
authority before returning the error. No partial canonical payload is
published and the write path cannot continue from an incomplete index.

Regression coverage proves that:

- remote rows with either a missing or invalid date fail explicitly;
- duplicate audit does not report success for an invalid remote row;
- a full NAV write sends no update/create request when its fresh source read is
  invalid; and
- a corrupt versioned disk row is rejected and discarded without being served
  as a public NAV fact.

Final status: `已修复`.

### DR-S7-RR-01 — accepted — fixed

Remote and versioned-cache builders now share one source-identity validator.
Every canonical row must carry an observed non-empty `record_id` and account;
an account-scoped read must match that exact account. The repository no longer
fills a missing account from the caller scope. All identity failures use the
same `NavHistoryReadIntegrityError`, clear scoped cache authority, and block
write planning before any mutation request.

Regression coverage includes remote and disk rows with missing account,
cross-account identity, and missing record ID, plus a write-before-read case
that proves zero update/create calls.

Final status: `已修复`.

### DR-S7-RR-02 — accepted — fixed

Both scoped and global duplicate audits now validate every source row through
the complete canonical builder before calculating duplicate groups. The
global path performs validation without publishing a cross-account cache, so
invalid date/account/record identity cannot yield `success=True`.

Final status: `已修复`.

## Scope Correction

The full suite exposed six legacy normal-response fixtures in
`tests/test_feishu_storage.py` that omitted the explicitly projected account
field. `s7-scope-correction.md` records the test-only correction. Only those
fixtures received `account="测试账户"`; production validation was not weakened.

### DR-S7-02 — accepted — fixed

The stage-aware write error now requires an explicit `failed` or `unknown`
classification for every unconfirmed target. Confirmed, failed, and unknown
target scopes are mutually exclusive and each target scope retains operation,
date, and record ID. Stage-level operation/chunk/reason metadata moved to the
separate `failure_stage` field.

An explicit `FieldNameNotFound` rejection is classified as failed, while
transport or malformed-response ambiguity remains unknown. The cache is still
discarded and rebuilt from a fresh complete read in both cases. Regression
coverage proves the distinct receipts for update-success/create-schema-failure
and update-success/create-transport-unknown.

Final status: `已修复`.

## Validation

- Focused batch/cache test file: `38 passed`.
- S7 scoped suite plus account contract tests: `80 passed`.
- Expanded NAV/report/service regression: `193 passed`.
- Corrected legacy NAV storage class: `8 passed`.
- Full repository suite with app token unset and dummy app credentials:
  `1260 passed`.
- Scoped Ruff, Python compileall, and `git diff --check`: passed.
- No live Feishu/Futu request, schema mutation, business-data write, merge,
  release, or deployment occurred.

## Remaining Gate

Run a fresh read-only DeepReview over the complete corrected S7 diff. S7 may be
accepted and committed only if that review has no unresolved findings.
