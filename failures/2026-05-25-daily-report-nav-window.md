# Failure Lesson: recent NAV natural-day window breaks Monday jobs

Date: 2026-05-25

`DailyReportPayloadService` used `NavReadService.get_nav(days=2)` after building
a daily NAV record. On Monday, the job records the previous Friday, which can be
outside a two-natural-day read window.

Daily report payloads should build their recent NAV snapshot from the current
`nav_record` plus the already-loaded NAV history, not by re-reading a short
natural-day window from storage.
