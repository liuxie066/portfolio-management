# Daily CLI single snapshot

Decision: `pm daily` must calculate NAV and position distribution from one
priced snapshot.

The direct CLI path builds one `PortfolioSkill` snapshot, passes it into
`record_nav()`, then passes the same snapshot into `get_distribution()`. The
service path uses `daily_report_bundle()` and reads its distribution payload
instead of calling `/nav/record` and `/distribution` separately.

This keeps the top-level NAV total and distribution total at the same market
time, including for large multi-market accounts where live prices can change
between calls.
