# NAV Write Path Trim

- `NavRecordService` now writes NAV only through `write_nav_record()` and `write_nav_records()`.
- Removed legacy mock/runtime fallbacks to `save_nav()` and `upsert_nav_bulk()` from the application path.
- `use_bulk_persist=True` still means single-row bulk replace through `write_nav_records(..., mode="replace", allow_partial=False, dry_run=False)` when doing a confirmed real write.
- The `--use-bulk-nav-upsert` CLI flag name remains for operator compatibility, but its help now describes the current `write_nav_records` implementation.
