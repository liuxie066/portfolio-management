# Pattern: separate timer cadence from business-date guards

Date: 2026-06-02

For delayed daily jobs, keep the scheduler cadence broad enough to trigger the
next-day window, then let the application resolve and validate the business
date. This avoids encoding business-calendar assumptions in systemd
`OnCalendar` expressions.
