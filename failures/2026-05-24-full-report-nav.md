# Full Report NAV

- The old full-report path removed today's persisted NAV from `working_navs` and then synthesized a new today NAV from `all_navs`; if `all_navs[-1]` was already today, same-day cash flow could be added to shares a second time.
- Hand-written synthetic formulas in `FullReportService._build_synthetic_nav()` created drift risk against `NavRecordService.record_nav()` and `NavCalculator`.
- A first refactor attempt used `_summarize_cash_flows()` directly; legacy tests that mock only daily/monthly/yearly/period cash-flow helpers then hit real storage and failed on missing `cash_flow` table config. Keep a fallback to the older helper surface for compatibility.
