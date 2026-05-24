# Full Report NAV

- `FullReportService.full_report()` must prefer a real NAV record for the current Beijing date when one exists.
- A synthetic same-day NAV is only for the read-only report gap before the daily NAV has been written.
- Synthetic report NAV construction should reuse the same core calculation/build path as `record_nav`: `_calc_nav_metrics()` and `_build_nav_record()`, with `NavCalculator` as fallback for tests or thin facades.
- Synthetic NAV inputs must use only historical NAV rows before today; including today's stored row can double-count the day's cash flow into shares.
- The synthetic marker stays in `details.is_synthetic` so report consumers can tell live report estimates from persisted NAV rows.
