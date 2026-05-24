# Full Report NAV

- For read-side calculations that mirror write-side business math, call the same domain calculator or portfolio facade instead of reimplementing formulas locally.
- When building a "today" synthetic record, first split NAV history into `date < today` and `date == today`; the latter wins and prevents synthetic recomputation.
- Regression tests for report calculations should cover both branches: recorded today NAV and synthetic before write.
- Keep `tests/run_tests.py` aligned when adding lightweight pytest tests that should also run without pytest.
- Validate calculation refactors with targeted tests, `tests/run_tests.py`, full pytest, compileall, and `git diff --check`.
