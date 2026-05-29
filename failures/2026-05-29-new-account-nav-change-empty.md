# Failure Lesson: new-account NAV change stayed empty

Date: 2026-05-29

The `hb` and `sy` accounts had current-month NAV history starting on
2026-05-01, but no previous month-end or previous year-end anchor. The persisted
`mtd_nav_change` and `ytd_nav_change` logic required those strict anchors, while
report performance queries already fell back to current-period starts.

Avoid maintaining different period-return semantics between persisted NAV facts
and report/query calculations.
