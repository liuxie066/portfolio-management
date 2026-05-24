# Yahoo Chart Pricing Cleanup

- The old US batch path hand-parsed Yahoo Chart and returned fewer fields than the single quote path, creating drift risk for `open`, `high`, `low`, and `volume`.
- A first shared-helper shape fetched FX before checking whether Yahoo returned usable quotes; that would have changed empty quote handling from `None` to an FX error.
- Tests added to `tests/run_tests.py` must not require pytest fixtures such as `monkeypatch`; use explicit patch contexts when a test is included in the manual runner.
