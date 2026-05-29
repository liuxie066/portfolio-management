# Changelog

## 0.1.5 - 2026-05-29

- Fixed MTD/YTD NAV change recording for new accounts that do not yet have a
  previous month-end or previous year-end NAV anchor.
- Kept MTD/YTD PnL on strict previous-period anchors while allowing NAV change
  to fall back to the first earlier NAV in the current month/year.
- Updated NAV audit and repair logic to distinguish strict period anchors from
  NAV-return fallback anchors.
- Added tests covering current-period fallback bases for NAV recording, audit,
  reconciliation, and NAV history indexing.

## 0.1.4 - 2026-05-25

- Fixed daily report payload assembly so scheduled Monday jobs can record the
  previous Friday's NAV without failing on a short natural-day NAV read window.
- Built recent NAV snapshots from the current NAV record plus loaded history,
  keeping dry-run and write-mode daily jobs on the same path.
- Documented the Linux production deployment split: `lx` runs with Futu
  cash/MMF sync, while `hb` and `sy` run normal daily NAV recording.
- Added deployment runbook notes for dry-run parity, explicit Asia/Shanghai
  timer scheduling, and Futu sync account scoping.

## 0.1.3 - 2026-05-25

- Added `scripts/install.sh` as a Hermes-style Linux bootstrap installer for
  clone/update, virtualenv creation, dependency installation, and deployment
  asset setup.
- Added a generated `pm` launcher for installed systems, defaulting to
  `/usr/local/bin/pm` for root Linux installs and `~/.local/bin/pm` for user
  installs.
- Updated systemd daily NAV jobs to invoke the same `pm` launcher used by
  operators, keeping manual and scheduled startup paths aligned.
- Hardened launch/install environment handling by clearing inherited Python
  environment variables that could shadow the installed checkout.
- Updated Linux deployment docs and runbooks around the new bootstrap install
  path while keeping timer activation explicit.
- Expanded installer coverage for launcher rendering, systemd unit behavior,
  and shell installer help output.

## 0.1.2 - 2026-05-25

- Added the unified `daily-job` workflow for single-account and multi-account
  NAV recording, including previous-business-day date resolution.
- Added duplicate `nav_history` auditing and write blocking to prevent repeated
  account/date rows.
- Added account NAV recording, daily NAV job, report payload, NAV preview, and
  NAV initialization application services.
- Moved Feishu table read/write behavior behind table-level repositories and
  kept mixins as thin storage facades.
- Removed the obsolete full-report alias layer and kept `skill_api.py` /
  `PortfolioSkill` as compatibility adapters only.
- Added YAML-based Linux installation and systemd timer support for scheduled
  daily NAV runs.
- Rewrote README, runbooks, service, architecture, dependency graph, schema,
  deployment, and Skill docs around the CLI + local service product boundary.
- Kept stale daily-report public URL publishing disabled; reports now document
  local artifacts with `public_url=null` and `public_url_status=disabled`.
- Expanded test coverage for config, CLI fallback behavior, service HTTP/client
  endpoints, daily NAV services, Feishu NAV repositories, Linux install, and
  report query boundaries.

## 0.1.1 - 2026-05-24

- Upgraded the portfolio CLI/service workflow around daily NAV calculation.
- Made `pm daily` derive NAV and position distribution from one priced snapshot.
- Added Feishu cash-flow reconciliation for manually entered ledger rows.
- Marked `transactions` as an optional capability table for the core NAV product.
- Consolidated Feishu schema documentation around manually maintained and system-filled fields.
- Simplified pricing around Yahoo Chart as the primary US price path with documented fallbacks.
- Removed retired compatibility scripts and moved NAV repair helpers under maintenance modules.
- Disabled stale public daily-report URL output; report publishing now returns local artifacts only.
- Added validation coverage for CLI/service daily bundles, NAV payloads, pricing, schema checks, and Feishu storage behavior.
