# Changelog

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
