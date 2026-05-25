# Failure Lesson: aliases can keep old architecture alive

Date: 2026-05-25

Leaving `FullReportService` available as a compatibility alias is acceptable,
but importing it from `skill_api.py` kept the old Skill-first report boundary
alive in practice.

Compatibility aliases should be fenced off with tests and avoided by all new or
actively maintained paths.
