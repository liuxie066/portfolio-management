# Pattern: conservative install and config doctor

Date: 2026-05-25

Expose deployment readiness through `pm config inspect` and `pm config doctor`
before writing live data. Install scripts should default to dry-run, render a
machine-readable plan, avoid overwriting `config.yaml`, and only enable systemd
timers through an explicit flag.

Runtime paths should be operator-configurable (`data.dir` / `PM_DATA_DIR`,
`report.reports_dir` / `PM_REPORTS_DIR`) instead of requiring warehouse-local
symlinks.
