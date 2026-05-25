# Pattern: schedule jobs resolve data date before orchestration

Date: 2026-05-25

Date selection belongs in `BusinessCalendarService`. Job orchestration should
consume the resolved NAV date and keep the account loop focused on duplicate
guards, cash-flow gates, existing-record checks, and account recording.

For scheduled production jobs, default to the previous eligible data date and
let explicit `--nav-date` remain the operator override.
