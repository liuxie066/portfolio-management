# Daily report NAV source pattern

When a workflow records or previews NAV and then renders a report in the same
bundle, pass the in-memory NAV result forward explicitly.

Use `src.app.nav_payload.format_nav_payload()` for NAVHistory-like read payloads
instead of reformatting the same fields in each adapter. This keeps service,
skill, full-report, and nav-read payloads using the same field names.
