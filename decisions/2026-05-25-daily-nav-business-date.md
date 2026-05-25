# Decision: daily NAV defaults to previous business day

Date: 2026-05-25

The scheduled daily NAV job resolves an omitted `nav_date` to the most recent
business day before the run date, not simply calendar yesterday.

The business calendar remains intentionally simple: weekends plus configured
`calendar.holidays`. This keeps the deployed daily job aligned with the current
product rule that different market calendars are out of scope.
