# Daily NAV job boundary

- `AccountNavRecorderService` owns single-account NAV recording: optional Futu cash/MMF sync, one priced snapshot, and one NAV write.
- `DailyReportPayloadService` owns daily report payload assembly from an existing snapshot and NAV fact: distribution, `nav_history` report context, report payload, and recent NAV read. It must not refetch prices or write NAV.
- `DailyAccountNavService` is only the compatibility orchestrator for the single-account daily NAV bundle response shape.
- `ReportGenerationService.generate_report()` is read-only. The old `record_nav=True` path was removed; NAV writes go through `AccountNavRecorderService` via `record_nav` or `daily_report_bundle`.
- `PortfolioService.record_nav()` and `PortfolioSkill.record_nav()` both delegate to `AccountNavRecorderService` so the NAV write payload and holdings-snapshot failure handling have one implementation.
- `DailyNavJobService` owns the single/multi-account job orchestration: business-day skip, account discovery, duplicate `nav_history` audit, cash-flow generated-field gate, existing-row skip, and account runner loop.
- CLI, HTTP, publisher, and `skill_api.py` should call these application services instead of rebuilding the daily NAV workflow locally.
- Scheduled daily NAV jobs default to no-overwrite; manual single-account compatibility commands may still expose overwrite behavior explicitly.
- Futu cash/MMF sync is part of the daily NAV pre-snapshot stage when enabled. Dry-run jobs only preview holdings changes; write jobs update broker-synced holdings rows before valuation unless the operator explicitly asks for Futu sync dry-run.
- Broker-synced holdings rows should be absolute replacements, not additive cash deltas, so descriptor fields and `quantity` stay aligned with the external broker source.
