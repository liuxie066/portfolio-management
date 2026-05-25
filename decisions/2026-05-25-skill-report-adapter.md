# Decision: keep skill report entrypoints as adapters

Date: 2026-05-25

`skill_api.py` must not depend on the `FullReportService` compatibility alias.
Report and return entrypoints in the Skill facade now delegate to:

- `PortfolioService` for ordinary `full_report()` and `generate_report()`
- `ReportQueryService` only for compatibility calls that pass an explicit
  snapshot or NAV history
- `src.domain.nav.performance` for return and risk calculations
- `src.domain.report.holdings_projection` for top-holdings projections

This keeps Skill as a caller-facing adapter instead of a report business layer.
