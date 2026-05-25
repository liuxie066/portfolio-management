# Failure Lesson: calendar yesterday skips Monday NAV

Date: 2026-05-25

Using `run_date - 1 day` as the default NAV date makes a Monday morning timer
target Sunday, then the non-business-day guard skips the job before Friday NAV
can be recorded.

Scheduled NAV jobs should look back to the previous business day before applying
the non-business-day guard.
