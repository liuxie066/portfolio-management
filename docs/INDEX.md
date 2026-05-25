# Documentation Index

`portfolio-management` is operated as a local CLI/service product. The
historical Skill/Python API remains available only as a compatibility adapter.

## Start Here

- Quick start and product overview: `README.md`
- Daily operations: `docs/runbook.md`
- Linux install and systemd timer: `docs/deploy-linux.md`
- Service API: `docs/service.md`
- Architecture map: `docs/architecture.md`
- Dependency graph: `docs/dependency-graph.md`
- Feishu schema: `docs/schema.md`
- Schema migration notes: `docs/migrations.md`

## Product Entrypoints

- CLI: `./pm`
- Installed CLI launcher: `pm`
- CLI implementation: `scripts/pm.py`
- Linux bootstrap installer: `scripts/install.sh`
- Local service manager: `scripts/service.py`
- HTTP routes: `src/service/http.py`
- Service facade: `src/service/application.py`
- Daily report publisher: `scripts/publish_daily_report.py`
- NAV repair CLI: `scripts/nav_history_repair.py`
- Compatibility Python API: `skill_api.py`
- MCP compatibility adapter: `mcp_server.py`

## Main Workflows

### Daily NAV Job

Use `./pm daily-job`.

It resolves the NAV date, audits duplicate `nav_history` rows, checks pending
manual `cash_flow` rows, optionally syncs Futu cash/MMF holdings, values each
account, writes NAV, and records holdings snapshots.

### Daily Report

Use `scripts/publish_daily_report.py`.

The publisher uses one priced snapshot for NAV recording, report payload
generation, and HTML rendering. The external daily-report domain is no longer
valid; only local static artifacts are produced.

### Configuration

Use `config.yaml`; production deployments should point
`PORTFOLIO_CONFIG_FILE` at `/etc/portfolio-management/config.yaml`.

Check readiness with:

```bash
./pm config inspect --json
./pm config doctor --json
```

## Core Boundaries

- `src/service/*`: service and HTTP boundary
- `src/app/*`: application orchestration
- `src/domain/*`: pure NAV/report calculations
- `src/pricing/*`: quote providers, cache policy, FX
- `src/feishu/repositories/*`: Feishu table-level storage
- `src/maintenance/*`: repair/backfill operations

Do not add new product behavior to `skill_api.py`. It should remain a caller
adapter that delegates inward.

## Invariants

- Business dates use Beijing date semantics.
- Writes default to dry-run and require explicit confirmation.
- `nav_history` writes must go through `FeishuStorage.write_nav_record()` or
  `FeishuStorage.write_nav_records()`.
- `holdings_snapshot` is written only after NAV write success.
- `daily-job` skips weekends and `calendar.holidays` unless explicitly forced.
- `daily-job` must block duplicate `nav_history` account/date rows before
  writing.
- `cash_flow` rows may be manually entered, but generated fields must be
  reconciled before daily NAV writes.

## Diagnostics

```bash
./pm config doctor --json
./pm nav duplicates --json
python scripts/migrate_schema.py check-live
python scripts/diagnose_pricing.py --account lx --json
python scripts/nav_history_repair.py backfill --account lx --from 2025-01-01 --to 2025-01-31 --dry-run
```

## Validation

```bash
python3 -m pytest tests -q
python3 tests/run_tests.py
git diff --check
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```
