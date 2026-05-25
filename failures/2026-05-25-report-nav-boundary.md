# Failure Lesson: broad report services hide write-path assumptions

Date: 2026-05-25

Letting daily report payload generation call the full-report service made the
write workflow depend on synthetic NAV preview logic that was intended only for
read-only queries.

The safer boundary is: NAV write workflows produce facts first, then payload
builders project from those facts. Preview-only logic should stay behind query
services and must not be part of the daily NAV recording path.
