# Decision: separate daily NAV facts from report preview logic

Date: 2026-05-25

Daily report payload construction consumes the recorded `nav_record` and its
valuation snapshot directly. It must not call `FullReportService` or any
synthetic NAV preview path.

`ReportQueryService` owns read-only full-report queries. `FullReportService`
remains only as a backward-compatible alias for older imports.

Synthetic NAV preview is isolated in `NavPreviewService` and is only for
read-only full-report queries before today's NAV fact has been recorded.
