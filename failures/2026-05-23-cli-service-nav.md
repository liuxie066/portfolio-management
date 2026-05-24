# CLI Service NAV Boundary

- Adding service-first behavior to a subcommand is incomplete if the subparser does not also accept `--service-url`, `--no-service`, and `--require-service`; argparse will reject flags placed after nested commands.
- Direct-path CLI tests can accidentally exercise the service client and return a service error envelope instead of the command payload; pass `--no-service` when testing direct fallback semantics.
- A service facade can appear service-first while still routing core behavior through `skill_api`; add explicit tests where the backend method raises if called.
- Architecture docs can lag behind code migrations and keep pointing future work at the wrong boundary; include dependency-graph updates in the done criteria for each migrated service method.
- After migrating one side of a product loop, the paired read path can still leak through the legacy facade; treat write and read endpoints as one closure unit for core NAV workflows.
- Mechanical facade rewrites can leave syntax debris in a hot service module; run a fast targeted pytest or compileall immediately after edits, before broader validation.
- Compatibility-delegate tests must shrink as methods migrate; otherwise they keep asserting legacy delegation for paths that should now be direct service behavior.
- Multi-account overview can look migrated while still depending on a global skill function; inject the per-account report function explicitly so tests can prove only the intended heavy path remains.
- After migrating `full_report`, tests that only check direct `PortfolioService.full_report()` are not enough; multi-account overview must also assert the backend `full_report` is not called because it consumes the same injected report function.
- After migrating the last delegated service method, remove stale architecture/doc wording that says service still falls back to `skill_api`; stale notes can steer the next slice at already-solved coupling.
- Chaining `/nav/record`, `/report/daily`, and `/nav` from the publisher would rebuild priced snapshots, making the stored NAV and rendered report potentially inconsistent while adding avoidable pricing latency.
- If only the HTTP/service layer generates a run id, direct fallback and scheduled publisher output can lose traceability; resolve or accept the id at the publisher boundary and pass it into either path.
- Suppressing internal stdout around the entire daily publisher swallowed the final JSON result and made `--quiet` meaningless; suppress only noisy build stages, then print the operator-facing result outside that context.
- A long-running local service can be healthy but stale after code changes; restart it before validating new routes like `/report/daily-bundle`.
- `daily_report_bundle` initially let storage/config exceptions escape as HTTP 500, and multi-account overview returned failure details only in `errors`; both made real configuration issues look like service bugs instead of actionable operator errors.
