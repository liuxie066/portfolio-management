# Daily report NAV source failure

Avoid deriving the official daily report NAV from a second read of history or a
synthetic full-report NAV after `record_nav()` has already run.

That makes the output depend on storage timing and fallback behavior. The safe
sequence is: build one priced snapshot, compute `record_nav`, pass that NAV
payload into report generation, then read recent NAV history only for the
secondary `nav_snapshot` section.
