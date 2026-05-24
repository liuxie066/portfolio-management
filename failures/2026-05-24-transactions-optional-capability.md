# Transactions Optional Capability Failures

- Treating `transactions` as core created unnecessary pressure to migrate `tx_date` even though the table is not maintained.
- A single `ok` flag for schema checks hid the difference between core product readiness and optional capability completeness.
- Field cleanup on an unused optional table can become product baggage if it distracts from NAV and position distribution.
