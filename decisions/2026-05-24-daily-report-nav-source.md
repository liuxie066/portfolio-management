# Daily report NAV source

Decision: the official daily report must display the NAV produced by the same
`record_nav()` call in the report bundle.

`FullReportService` may still build a synthetic current-day NAV for read-only
preview reports, but `daily_report_bundle` passes the freshly computed
`nav_record` into `ReportGenerationService` as `nav_override`. This keeps the
published daily report aligned with the write/preview result instead of relying
on whether today's NAV was already visible through `get_nav_history()`.
