# Calculation Cleanup

- For portfolio distributions, compute valuation once and aggregate over the priced holdings. This keeps distribution math aligned with NAV and valuation warnings.
- Put write-side quality gates at the service boundary that persists state, not in read-only valuation helpers.
- When a legacy provider may normalize symbols, keep both requested keys and normalized keys in the success check.
- Small duplicated formulas should be replaced with delegation to the canonical service before adding new abstraction.
- Regression coverage should include both direct service tests and the older facade tests when a facade changes behavior.
- After calculation changes, run targeted pytest, full pytest, `tests/run_tests.py`, compileall, and `git diff --check`.
