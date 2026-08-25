# Goal Confirmation — NAV Receipt CAGR

- Gate: `goal confirmation`
- Work unit: `nav-receipt-cagr`
- Status: `pass`
- Artifact path: `docs/gateflow/nav-receipt-cagr/goal-confirmation.md`

## Goal and motivation

Add the existing compound annual growth rate to each successful account row in
the NAV History tracking receipt, so the operator can see it beside MTD and YTD
NAV performance.

## Success signal

- A written account row displays `复合增长率 +x.xx%` or `-x.xx%` after
  `YTD NAV`.
- Missing CAGR displays `复合增长率 -`.
- Existing NAV calculation, persistence, delivery, skipped rows, and failure rows
  are unchanged.

## Direct code evidence

- `DailyReportPayloadService._build_daily_report()` already exposes `cagr` as a
  decimal ratio and `cagr_pct` as percentage points in the account report.
- `NavHistoryReceiptService._item_row()` is the single renderer for successful
  NAV History account rows and currently renders MTD and YTD but not CAGR.
- `_format_signed_pct()` already owns signed decimal-ratio formatting and the
  unavailable `-` representation.

## Scope boundary and non-goals

- Change only the successful receipt row and its focused tests.
- Do not change CAGR calculation, NAV schema, persistence, historical data,
  skipped/failure text, notification delivery, version metadata, release,
  deployment, or production state.
- Preserve unrelated untracked `assets/` content.

## Open questions

- None. The user confirmed the Chinese label `复合增长率` and the
  presentation-only boundary.

## Residual risks

- None unclassified. Unit mismatch is prevented by rendering decimal `cagr`
  through the existing decimal percentage formatter rather than rendering
  percentage-point `cagr_pct`.
