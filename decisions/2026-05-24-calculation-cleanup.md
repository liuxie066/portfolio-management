# Calculation Cleanup

- Real NAV writes must fail fast when valuation warnings show missing FX or missing non-cash prices. Dry runs may still return diagnostics.
- Industry distribution should use the same valued holdings produced by `calculate_valuation()` instead of fetching prices through a separate path.
- Report generation with `record_nav=True` must propagate a failed NAV write as a failed report result.
- Return helpers exposed by `skill_api.py` should delegate to `FullReportService` period-return helpers instead of duplicating formulas.
- Cash-flow totals should prefer normalized `cny_amount` when present and only fall back to raw `amount` for legacy rows.
- Legacy price batch compatibility should compare normalized quote keys before reporting failed fetches.
- Audit period recomputation belongs in one helper shared by accuracy and reconcile audit paths.
