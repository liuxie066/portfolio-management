# NAV History Repair Consolidation

- `scripts/nav_history_repair.py` is the only supported CLI entrypoint for `nav_history` repair/backfill operations.
- Backfill and patch implementations live under `src/maintenance/nav_history_repair/` so historical script names do not keep carrying production logic.
- The CLI owns argument parsing, strict unknown-argument rejection, and mutually exclusive `--apply` / `--dry-run` write gates before dispatching to implementation modules.
- Deleted historical entrypoints: `scripts/backfill_nav_history_bulk.py` and `scripts/nav_history_patch.py`.
