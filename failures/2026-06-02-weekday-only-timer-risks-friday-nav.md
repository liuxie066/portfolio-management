# Failure Lesson: weekday-only timers risk Friday NAV gaps

Date: 2026-06-02

When the product records NAV on the day after the business date, skipping
Saturday timer runs can remove the normal window for Friday NAV recording.

The skip rule belongs to the resolved NAV date, not to the systemd timer run
date. If a deployment cannot run every day, it must at least cover Tuesday
through Saturday.
