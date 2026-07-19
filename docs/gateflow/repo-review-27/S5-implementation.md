# S5 Implementation Artifact

- Gate: `implementation -> code review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S5-pricing-correctness`
- Base commit: `67135d3`
- Status: `complete; code review passed after fix and re-review`
- Artifact path: `docs/gateflow/repo-review-27/S5-implementation.md`

## Objective

Fix source findings 07, 08, 09, 10, 11, 20, 21, 22, and 26: make valuation reject unsafe foreign/raw-price fallbacks, enforce one deadline-owned pricing path, keep cache-only execution network-pure, validate quote/FX numerics, use New York-local US market time, preserve holding quantity precision, canonicalize supported market suffixes, and convert foreign MMF values through FX.

## Changed production files

- `src/app/valuation_service.py` — one monotonic deadline-bound fetch; no daemon worker or second cache fetch; only CNY CASH/MMF receive unit fallback; foreign and security price failures remain unvalued with blocking warnings.
- `src/price_fetcher.py` — propagates the optional absolute deadline through single, batch, retry, realtime, and FX paths.
- `src/pricing/service.py` — cache/cache-only is selected before fixed or provider branches; canonical cache/provider keys map back to caller codes; invalid quotes become structured failures.
- `src/pricing/batch.py` — removes executor-owned work, lazily fetches FX only when needed, uses bounded native batch HTTP plus sequential fallback, and returns before all owned work stops.
- `src/pricing/payload.py` — central positive-finite validation for `price`, `cny_price`, and `exchange_rate`; foreign quotes require `cny_price`; shared remaining-deadline/backoff helpers.
- `src/pricing/fixed.py` — CNY MMF remains unit-valued; USD/HKD MMF reuse the fixed cash FX path.
- `src/pricing/fx.py` — validates complete USD/HKD caches, removes concurrent workers, bounds retries/sleeps/HTTP by the absolute deadline, and never refreshes freshness from invalid data.
- `src/pricing/classifier.py` — one pricing canonicalization boundary strips only `.HK/.SH/.SZ/.US` terminal suffixes and preserves internal-dot symbols.
- `src/pricing/providers/*.py` — provider request and FX timeouts consume remaining deadline; US batch execution is sequential; invalid HK FX results are not emitted as foreign quotes without CNY values.
- `src/market_time.py` — US session state and next-open calculations use `America/New_York`, with absolute elapsed time across DST transitions; holidays remain outside the existing calendar contract.
- `src/snapshot_models.py` — quantity uses independent eight-decimal precision instead of money precision.

## Changed tests

- `tests/test_decimal_valuation.py`
- `tests/test_price_boundary_decimal.py`
- `tests/test_price_fetcher.py`
- `tests/test_price_fetcher_branch_normalization.py`
- `tests/test_price_fetcher_single_fetch_cache_only.py`
- `tests/test_pricing_classifier.py`
- `tests/test_pricing_providers.py`
- `tests/test_snapshot_service.py`

## Invariants proved

- Foreign holdings are valued only with a positive finite CNY price; missing/invalid FX never falls back to the raw foreign price.
- Missing CNY stock/fund prices stay unvalued; only CNY CASH/MMF use a unit fallback.
- Valuation performs one price-fetch attempt and passes one absolute monotonic deadline through all owned pricing and FX work.
- Cache-only fixed cash/MMF/crypto requests perform zero FX, provider, or HTTP calls on cache miss.
- Provider and cache payloads with missing, zero, negative, NaN, or infinite authoritative values are rejected before valuation or cache persistence.
- FX memory/file freshness requires both `USDCNY` and `HKDCNY` as positive finite values; deadline exhaustion stops retries before backoff can overrun.
- US market weekday/session evaluation uses New York local time, including Beijing/New York cross-date cases and the March DST weekend.
- `.HK/.SH/.SZ/.US` terminal suffixes normalize once; `BRK.B` remains intact; batch results map back to the caller's original code.
- CNY/USD/HKD MMF use correct CNY conversion semantics.
- Snapshot quantities preserve stock/fractional-fund/tiny-crypto precision independently from money fields.

## Validation

- Required S5 focused suite -> `63 passed`.
- Full repository suite -> `635 passed`.
- `python3 -m compileall -q src tests` -> passed.
- `git diff --check` -> passed.
- Pricing worker/executor search -> no `ThreadPoolExecutor`, `as_completed`, `threading.Thread`, or `daemon=True` matches in the reviewed pricing paths.

## Review artifacts

- Code review and re-review: `docs/reviews/code-review-20260719-185414.md`.
- Fix artifact: `docs/gateflow/repo-review-27/S5-fix.md`.
- Final review decision: `code review pass after fix and re-review`; accepted findings remaining: `0`.

## Documentation decision

- No operator or public command changed in this slice. Existing pricing diagnostics remain the user-facing surface; the market-holiday limitation is preserved rather than expanded into a new calendar subsystem.

## Residual risks

- Exchange holidays are still not modeled and are `assigned to later work unit` if holiday-aware cache timing becomes a requirement.
- HTTP clients can only enforce deadlines to the extent the underlying socket timeout implementation honors them; all repository-owned retries, sleeps, batches, and provider calls are now bounded and no background worker survives return.
- Historical cache rows stored under suffixed symbols are not migrated automatically; canonical lookup/write behavior is fixed for new execution and historical cleanup is `assigned to later migration decision`.

## Completion state

- Current gate: `accepted slice commit`.
- Next entry point: create `gateflow: accept repo-review-27 S5-pricing-correctness`, then begin S6 implementation.
