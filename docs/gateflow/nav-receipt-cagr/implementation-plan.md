# Implementation Plan — NAV Receipt CAGR

- Gate: `plan`
- Work unit: `nav-receipt-cagr`
- Status: `accepted; plan review pass`
- Artifact path: `docs/gateflow/nav-receipt-cagr/implementation-plan.md`

## Goal, motivation, and success signal

Expose the already-computed compound annual growth rate in each successful NAV
History receipt account row. Success is one new `复合增长率` field with
signed two-decimal percentage formatting and `-` when unavailable, with all
existing receipt and NAV behavior preserved.

## Non-goals and scope boundary

- No new calculation, fallback, data source, schema, persistence, backfill,
  delivery, configuration, or public API.
- No change to skipped or failed account rows.
- No release, deployment, or production mutation.
- Untracked `assets/` remains untouched.

## First-principles judgment and code evidence

The feature is valid because the receipt payload already contains the required
fact. `DailyReportPayloadService._build_daily_report()` publishes decimal
`cagr`; `NavHistoryReceiptService._item_row()` owns the target presentation;
and `_format_signed_pct()` already implements the exact signed/missing format.
Recomputing CAGR or adding a formatter would create a second source of truth.

## Affected files and ownership

- `src/app/nav_history_receipt_service.py`: append the display field in the
  successful-row renderer.
- `tests/test_nav_history_receipt_service.py`: feed CAGR through the existing
  receipt fixture helper and verify positive, negative, and missing rendering.
- `docs/gateflow/nav-receipt-cagr/` and `docs/reviews/`: required Gateflow
  artifacts only.

## Contract, schema, state, and interface changes

- User-visible receipt text: written account rows gain
  `｜复合增长率 <signed percentage>` immediately after `YTD NAV`.
- Input contract remains the existing `item.report.cagr` decimal ratio.
- No schema, state-machine, persistence, delivery, or public-interface change.

## Implementation decision

Reuse `_format_signed_pct(report.get("cagr"))`. Do not use `cagr_pct`, because
that field is already expressed in percentage points and the existing formatter
expects a decimal ratio.

## Slice S1 — receipt presentation

- Objective: render the existing CAGR fact in successful account rows.
- Allowed production file: `src/app/nav_history_receipt_service.py`.
- Allowed test file: `tests/test_nav_history_receipt_service.py`.
- Exact change: insert one formatted field after YTD; extend the existing test
  helper and existing signed/missing assertions.
- Data flow: `DailyReportPayloadService.report.cagr` -> daily job item `report`
  -> `NavHistoryReceiptService._item_row()` -> Feishu receipt markdown.
- Invariant: no calculation or mutation occurs in the renderer.
- Completion signal: focused tests pass and assert positive, negative, and
  unavailable output.
- Stop condition: any evidence that `report.cagr` is not a decimal ratio or is
  absent from the real written-item path.

## Validation

Expected assertions:

- positive `0.0888` renders `复合增长率 +8.88%`;
- negative `-0.0456` renders `复合增长率 -4.56%`;
- `None` renders `复合增长率 -`;
- existing receipt assertions remain green.

Commands:

```bash
python3 -m pytest tests/test_nav_history_receipt_service.py -q
python3 -m pytest tests -q
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
git diff --check
```

## Documentation decision

No user documentation or changelog change: this is a self-explanatory receipt
presentation addition. Gateflow and review artifacts document the work unit.

## Risks and open questions

- Risk: confusing `cagr` decimal ratio with `cagr_pct` percentage points.
  Mitigation: consume only `cagr` with the existing formatter and test `0.0888`.
- Open questions: none.

## Why this is not over-designed

The implementation adds one presentation field and reuses the existing payload
and formatter. It adds no helper, dependency, fallback, configuration, or
calculation.

## Completion report format

Report the displayed text, validation results, finding status, residual risks,
and draft PR URL. Keep release and deployment explicitly out of scope.
