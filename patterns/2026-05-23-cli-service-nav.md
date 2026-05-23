# CLI Service NAV Boundary

- Move product CLI commands to service-first by adding the application method, client method, HTTP route, then CLI wiring in that order.
- For write-capable CLI paths, keep parser support for service flags on the write subcommand itself because users often place global flags after subcommands.
- Test service-first CLI behavior separately from direct fallback by using `--no-service` in direct-path tests.
- Use POST for write-capable service actions even when the command defaults to dry-run.
- When moving a method out from `skill_api`, preserve the response payload shape first, then test that the backend method is not called.
- Let service-level direct paths use injectable storage/portfolio/read-service factories so tests can prove the boundary without touching Feishu.
- Update dependency graphs in the same slice as service-boundary migrations so the architecture map stays useful for selecting the next module to shrink.
- For read-only history commands, move response formatting into a small app service first; the service facade should only resolve account and dependencies.
- When an app read service already owns both light storage reads and priced snapshot reads, migrate the service facade directly to that service instead of adding another wrapper.
- If a side-effect app service already owns a bounded domain like cash holdings, put the matching simple read model there unless it would pull in unrelated reporting dependencies.
- For multi-account orchestration, migrate discovery and aggregation first while injecting the heavier per-account report function; this removes facade coupling without forcing a large report rewrite.
- When moving a larger read workflow like `full_report`, first extract the workflow into an app service, then have both `PortfolioService` and the legacy Skill facade call that service to avoid duplicate algorithms.
- Keep snapshot reuse explicit in report services: accept an injected snapshot for compatibility tests and build exactly one priced snapshot when the service path owns the read.
- Split full-report calculation from report-payload assembly: `FullReportService` owns valuation/NAV/distribution, while `ReportGenerationService` owns daily/monthly/yearly response shaping.
- For multi-step workflows where consistency depends on one valuation, add a service bundle endpoint instead of chaining separate endpoints from the client.
- Keep scheduled publishers service-first but retain an explicit direct mode for local recovery, with tests proving both paths.
- For write/report workflows, generate the trace id once at the outer workflow boundary and pass it downward; do not let each internal stage create its own unrelated id.
- Keep internal diagnostic stdout suppression scoped to noisy computation only; final CLI/publisher JSON output must remain outside the suppression block, with `--quiet` tested explicitly.
