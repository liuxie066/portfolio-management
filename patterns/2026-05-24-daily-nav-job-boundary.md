# Daily NAV job pattern

- Put business workflow in `src/app/*` services first, then make HTTP/client/CLI/publisher/skill API functions thin parameter adapters.
- Keep NAV recording and report payload assembly as separate services: the recorder writes the NAV fact; the payload builder consumes the existing snapshot/NAV fact and must not refetch prices or write NAV.
- Keep `ReportGenerationService` read-only; do not reintroduce `record_nav` switches into report generation.
- Compatibility facades may preserve method names, but should delegate to `AccountNavRecorderService` instead of reimplementing NAV result formatting.
- For daily NAV, run a read-only duplicate audit before any write attempt and let storage-level duplicate blocking remain the final guard.
- Run the cash-flow generated-field gate before same-day existing-NAV skip; existing rows provide idempotence but should not hide incomplete manual cash-flow edits.
- Keep the business calendar simple for now: Beijing run date, target `run_date - 1`, skip weekends plus configured `calendar.holidays`.
- Add adapter tests that assert call payloads, and service tests that assert blockers run before account-level calculation.
- For broker balance sync, expose a read-safe preview but make the production job path update holdings before snapshot construction. Treat fetched broker balances as absolute targets.
- Document response payload shape for workflows that feed publishers; avoid making HTML/publisher code infer contracts from incidental service internals.
