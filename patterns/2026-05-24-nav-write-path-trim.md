# NAV Write Path Trim

- Keep production code aligned with the documented storage boundary: full NAV writes use `write_nav_record` / `write_nav_records`; derived-field repair uses `patch_nav_derived_fields`.
- When removing old storage compatibility branches, update tests to assert the canonical write methods rather than preserving mock-only legacy patch points.
- Leave historical method names only in docs that explicitly state they are deleted; avoid old names in test names and help text.
- Guard this cleanup with NAV service tests, portfolio tests, service application tests, daily-report entrypoint tests, full pytest, legacy test runner, compileall, and `git diff --check`.
