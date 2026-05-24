# NAV History Repair Consolidation Failures

- Direct script execution failed until the canonical CLI inserted the repository root into `sys.path`.
- `parse_known_args` hid unsupported flags, which is unsafe for maintenance commands that can write production `nav_history`.
- Keeping old script files as wrappers would still leave two apparent entrypoints; moving implementation first made deletion safe.
