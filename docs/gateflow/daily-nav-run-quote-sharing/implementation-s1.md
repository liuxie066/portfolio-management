# Gateflow Implementation Artifact — S1

- Gate: `implementation`
- Work unit: `daily-nav-run-quote-sharing`
- Slice: `S1 — Same-run quote sharing`
- Branch: `fix/daily-nav-run-quote-sharing`
- Base plan commit: `421f6b3`
- Artifact path: `docs/gateflow/daily-nav-run-quote-sharing/implementation-s1.md`
- Code review: `docs/reviews/code-review-20260723-085325.md`
- Re-review: `docs/reviews/code-review-20260723-085633.md`
- Status: `implemented; code review pass`

## Implemented contract

- Added a caller-owned `RunQuotePool` with `(canonical_code, market_type)` identities.
- Stored only positive finite price/CNY-price payloads and used deep copies for storage and return values.
- Reused successful quotes across sequential accounts while limiting failed identities to two delegated attempts per run.
- Partitioned same-canonical/different-market misses so the existing canonical-only batch planner cannot merge them.
- Preserved the account's existing deadline and fetcher options for every delegated miss batch.
- Passed the optional pool through daily job, account runner, recorder, read snapshot, portfolio manager, and valuation service without attaching it to shared services.
- Kept duplicate, cash-flow, existing-row, and Futu sync ordering unchanged.
- Added independent holding-level `run_reused` price-summary counts and boundary-accurate job metrics: `unique_requested`, `fetch_attempted`, `fetcher_resolved`, `run_reused`, `retried`, and `failed_unique`.
- Extended consolidated receipt parsing/rendering while retaining the legacy price-summary format.

## Files changed

- `src/app/run_quote_pool.py` (new)
- `src/app/daily_nav_job_service.py`
- `src/app/daily_account_nav_service.py`
- `src/app/account_nav_recorder_service.py`
- `src/app/portfolio_read_service.py`
- `src/app/valuation_service.py`
- `src/app/nav_history_receipt_service.py`
- `src/portfolio.py`
- `tests/test_run_quote_pool.py` (new)
- `tests/test_valuation_service.py`
- `tests/test_daily_nav_services.py`
- `tests/test_portfolio_read_service.py`
- `tests/test_nav_history_receipt_service.py`

## Focused validation

Command:

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_run_quote_pool.py \
  tests/test_valuation_service.py \
  tests/test_daily_nav_services.py \
  tests/test_portfolio_read_service.py \
  tests/test_nav_history_receipt_service.py
```

Initial result: `59 passed in 0.31s`.

Post-review-fix result: `64 passed in 0.30s`.

Additional check: `git diff --check` passed.

## Regression evidence

- Earlier FUTU/PDD/TCOM successes are reused and the later account delegates only SPY/BABA.
- `.US`/raw aliases share under the same US identity and returned payloads are independent copies.
- Simultaneous `.US`/`.SH` requests with the same canonical code use separate delegated calls and retain distinct payloads.
- Invalid payloads never enter the pool; a failed identity is delegated at most twice.
- Later-account retry can resolve an earlier miss; a separate pool starts empty.
- Reused cache/stale quotes remain classified as cache/stale, while `run_reused` is counted independently.
- A custom pool returning an invalid reused payload is still rejected by `ValuationService` and emits the existing blocking missing-price warning.
- Futu cash/MMF sync still runs before snapshot construction, and the same pool reaches that snapshot.
- A unique later-account missing quote produces a partial job without rewriting the earlier account's success.
- Legacy receipt warning strings remain parseable; new summaries render `同轮复用 N` only when non-zero.

## Scope confirmation

- No provider ordering, timeout, persistent-cache, CLI/config, or public input contract changed.
- No `CNY-CASH/HKD` data fix was included.
- No release, deployment, production NAV write, or NAV backfill was performed.

## Completion state

- Current gate: `slice code review pass`.
- Next gate: `accepted slice commit`.
