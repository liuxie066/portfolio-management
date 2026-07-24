# Changelog

## 0.1.24 - 2026-07-24

- Added a read-only loopback valuation-evidence endpoint for multi-account consumers, returning current non-option holdings, supplemental quotes, explicit CNY FX evidence, quote provenance, freshness, and per-account quality.
- Reused the canonical valuation and shared run quote pool so options-monitor can request assignment underlyings without introducing a second pricing path.
- Kept CNY cash and MMF on fixed identity pricing, stale or missing evidence explicit, and all portfolio, NAV, and holdings state unmodified.

## 0.1.23 - 2026-07-23

- Sent Futu holdings-sync and NAV History receipts as Feishu post messages with native titles instead of unparsed Markdown headers.
- Converted receipt section headings into bold post content while keeping heading markers out of the visible message body.
- Preserved existing receipt content, dry-run behavior, notification failure isolation, and the legacy text-message API.

## 0.1.22 - 2026-07-23

- Shared successful market quotes across accounts within one daily NAV run, keyed by canonical code and market type.
- Kept failed quotes retryable with bounded attempts while preserving stale-price policy, provider diagnostics, and account isolation.
- Added run-level quote reuse and retry metrics to job results and consolidated NAV receipts.

## 0.1.21 - 2026-07-23

- Removed twelve unused internal report, cash, NAV-performance, configuration, and local-cache methods.
- Kept the cleanup deletion-first by using the existing direct calculation and batch cache paths instead of retaining redundant wrappers.
- Preserved compatibility and test-only APIs, with the full 711-test suite still passing.

## 0.1.20 - 2026-07-22

- Aligned the NAV History and Futu sync Feishu receipts with the options-monitor flat-markdown shell: `# PM · 回执 · ...` header, `标签｜值` fields, emoji status, and `##` sections with empty sections omitted.
- Flattened per-account NAV receipt details into single rows and dropped the redundant `告警：无` footer when no warnings exist.
- Added a shared `render_receipt` shell module with regression coverage for field flattening, empty-section omission, and placeholder values.

## 0.1.19 - 2026-07-21

- Added versioned NAV finality provenance so the canonical daily job skips only trustworthy final rows and fails closed on legacy or mismatched records.
- Made NAV overwrite behavior opt-in across public CLI, service, publisher, initialization, close, and maintenance write paths.
- Preserved finality through local NAV index refresh and restart reconstruction, using fresh remote duplicate-audit facts for existing-row decisions.
- Refused Feishu `FieldNameNotFound` compatibility retries that would drop authoritative `details.finality` from single or batch NAV writes.

## 0.1.18 - 2026-07-19

- Hardened financial writes with decimal-safe validation, idempotency, oversell prevention, durable compensation, and truthful partial-write reporting.
- Corrected Futu synchronization to preserve broker-reported quantities and true `average_cost`, with safer installer credential handling and empty-snapshot guards.
- Added recoverable NAV History write/repair state machines and tightened pricing, FX, deadline, cache-only, and market-time correctness.
- Strengthened Feishu storage consistency and loopback service safety, including timeout-aware batching, schema validation, and explicit outcome-unknown handling.

## 0.1.17 - 2026-07-17

- Added read-only MTD/YTD capital facts for per-account total-asset bridge analysis, including explicit period anchors, portfolio change, and cash-flow evidence.
- Exposed the capability through the loopback HTTP API and service client while preserving unavailable and partial upstream evidence instead of coercing missing values to zero.
- Added application, client, HTTP, documentation, and regression coverage for the new analysis boundary.

## 0.1.16 - 2026-07-17

- Added signed year-to-date NAV change to each successfully written account in the consolidated NAV History receipt.
- Displayed unavailable YTD NAV change explicitly as `-` and added regression coverage for positive, negative, and missing values.

## 0.1.15 - 2026-07-17

- Added the previously omitted fund allocation to NAV History receipts so the displayed stock, fund, and cash ratios account for the full portfolio.
- Replaced verbose per-account pricing diagnostics with one compact aggregate price status while keeping genuine warnings readable as separate lines.
- Added regression coverage for healthy aggregate pricing summaries and account-specific stale or missing price alerts.

## 0.1.14 - 2026-07-16

- Removed eight unused private compatibility and helper methods from the Skill, storage, portfolio, and pricing facades.
- Removed the obsolete imports left behind by those methods without changing public CLI, service, or Python compatibility behavior.
- Kept the cleanup deletion-only: 66 lines removed with the full test suite still passing.

## 0.1.13 - 2026-07-16

- Added an explicitly enabled, loopback-only portfolio HTTP API systemd service for same-host consumers such as options-monitor.
- Kept the API service independent from NAV/Futu timers and fixed its supported long-running bind to `127.0.0.1:8765`.
- Updated the Linux installer and deployment/service documentation with the opt-in `--enable-api-service` workflow and unauthenticated loopback safety boundary.

## 0.1.12 - 2026-07-16

- Classified currency-valued virtual-asset account buckets as `crypto`, added
  fixed FX-backed pricing for `*-CRYPTO-{CNY,USD,HKD}` identifiers, and kept
  them out of cash/MMF aggregation. The production Binance records use
  `TRADING-CRYPTO-USD` and `WALLET-CRYPTO-USD`, with the broker retained in the
  dedicated `broker` field.
- Removed obsolete MCP, storage-factory, migration-state, module-export, and
  compatibility HTTP surfaces, together with historical audit/pattern/failure
  artifacts, and aligned the documentation with the current CLI + local HTTP
  service architecture.
- Dropped the unused `mcp` and `pytz` dependencies, using the standard-library
  timezone implementation and direct current service/storage paths instead.

## 0.1.11 - 2026-07-16

- Made `--group-cash` automatically use asset-level distribution so rows with
  the same asset code are merged across accounts and brokers.
- Renamed the combined cash/MMF distribution row to `现金及等价物` while
  retaining the stable `CASH+MMF` code and per-account breakdown.
- Added CLI, application-service, reporting, and documentation coverage for
  the consolidated lx/sy holdings distribution workflow.

## 0.1.10 - 2026-07-16

- Added `pm futu sync` to synchronize Futu cash, MMF, STOCK/ETF quantities,
  and `avg_cost`, using only Futu `average_cost` with empty-snapshot and
  unsupported-position safety guards.
- Added best-effort Feishu receipts for real Futu synchronization and one
  consolidated multi-account NAV History run, reusing only the three
  `OM_FEISHU_BOT_*` credentials from options-monitor.
- Split production scheduling into a Monday-Saturday 08:10 Beijing morning
  workflow (lx/sy sync, then one lx/hb/sy NAV job) and a Monday-Friday 17:10
  Beijing holdings-only workflow, with a shared lock and persistent timers.
- Updated the Linux installer, service/CLI interfaces, documentation, and
  regression coverage for the new synchronization and scheduling boundaries.

## 0.1.9 - 2026-06-30

- Added asset-level position distribution for CLI and service callers,
  including multi-account merging by asset code.
- Preserved per-account and broker breakdowns, with optional cash/MMF grouping
  and quantity-only output.
- Made the initial NAV audit test independent of the rolling default audit
  window.

## 0.1.8 - 2026-06-30

- Let confirmed daily NAV writes automatically reconcile system-managed fields
  for manually entered `cash_flow` rows before NAV calculation.
- Kept dry-run daily jobs as a non-mutating guard that reports pending
  `cash_flow` generated fields without writing Feishu.
- Added regression coverage for the write-mode reconcile path.

## 0.1.7 - 2026-06-02

- Made the Linux installer default systemd timer run daily at
  `08:10 Asia/Shanghai`, matching the next-day NAV recording workflow.
- Documented that weekend/holiday skipping applies to NAV dates, not timer run
  dates; Saturday runs are needed to record Friday NAV on the next day.
- Added calendar coverage for Saturday/Sunday timer runs resolving to the prior
  Friday business date.

## 0.1.6 - 2026-05-29

- Suppressed swapped MTD/YTD NAV-change audit false positives when both values
  legitimately share the same current-period fallback base.

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
