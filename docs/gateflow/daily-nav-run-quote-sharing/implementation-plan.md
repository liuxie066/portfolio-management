# Gateflow Implementation Plan

- Gate: `plan`
- Work unit: `daily-nav-run-quote-sharing`
- Branch: `fix/daily-nav-run-quote-sharing`
- Base: `origin/main@a21bb1d`
- Goal artifact: `docs/gateflow/daily-nav-run-quote-sharing/goal-confirmation.md`
- Artifact path: `docs/gateflow/daily-nav-run-quote-sharing/implementation-plan.md`
- Status: `plan review pass`

## Goal

Make successful quotes reusable across accounts in one canonical daily NAV run, without changing provider behavior or weakening the existing blocking valuation contract.

## Contract decisions

### 1. Ownership and lifetime

Add an internal `RunQuotePool` in `src/app/run_quote_pool.py`. `DailyNavJobService.run()` creates exactly one pool after resolving a non-empty account list and passes the same object explicitly through:

```text
DailyNavJobService
  -> DailyAccountNavService.run
  -> AccountNavRecorderService.record
  -> PortfolioReadService.build_snapshot
  -> PortfolioManager.calculate_valuation
  -> ValuationService.calculate_valuation
  -> RunQuotePool.fetch_batch
  -> existing PriceFetcher.fetch_batch for eligible misses only
```

All new parameters are optional and default to `None`, so direct single-account and read-model callers retain current behavior. The pool is a local variable of one `run()` call; it is never stored on the service, portfolio manager, price fetcher, module, or persistent storage.

### 2. Quote identity

The sharing key is `(canonical_code, market_type)`:

- `canonical_code = canonicalize_pricing_code(raw_code)`;
- market is resolved with the holding's `asset_type`, `detect_market_type(raw_code)`, and existing `market_type_from_asset_type()`;
- asset/name maps accept raw, uppercase, and canonical lookups;
- aliases share only when both fields match;
- an unresolved market remains an explicit stable identity value rather than colliding with a known market.

Within one call, aliases resolving to the same identity use one representative provider request and receive separate copied result payloads.

The existing batch planner deduplicates by canonical code without retaining market. Therefore the pool must partition delegation so no one underlying `fetch_batch()` call contains two identities with the same canonical code but different markets. Non-colliding misses remain in one normal batch; only conflicting identities are split into separate calls, all using the original account deadline. This preserves the normal batching shape while making the cross-market contract true at the downstream boundary.

### 3. Success and validation contract

The pool delegates misses to the existing `PriceFetcher.fetch_batch()` with the original deadline, stale policy, concurrency, and routing options. It admits a result only when:

- the payload is a mapping;
- `price` is positive and finite;
- `cny_price` is positive and finite, or can use the same validated price only when currency is CNY.

The stored payload is copied. Every caller receives a new copy. A reused copy adds `is_from_run_pool=True` but preserves `source`, `is_from_cache`, `is_stale`, `fetched_at`, `cny_price`, and all other underlying evidence. Freshly fetched payloads are not marked as reused.

`ValuationService` continues to run its own `positive_finite_decimal()` checks for both fresh and reused payloads. The pool is an optimization boundary, not valuation authority.

### 4. Failure and retry contract

Missing, invalid, timed-out, or exceptional results never enter the quote map. Track attempts per identity:

```text
attempt 0 -> first eligible account may call provider
failure   -> later eligible account may call provider once more
attempt 2 -> no more provider calls in this run; identity remains missing
success   -> cache quote and serve later accounts from the run pool
```

The attempt count increments only for identities included in a delegated provider call. Provider exceptions mark those delegated identities as failed attempts and propagate as empty results to the valuation layer, matching current fail-closed behavior. Existing per-account deadlines are created by `ValuationService`; the pool does not create a run-wide deadline.

### 5. Ordering and blockers

Keep the current sequential account loop. Duplicate audit, cash-flow reconciliation, finality/existing-row checks, and optional Futu cash/MMF sync retain their positions. A blocked or skipped account never reaches snapshot construction and therefore never consumes attempt budget or triggers pricing.

Futu sync remains inside `AccountNavRecorderService.record()` before `build_snapshot()`, so the current account's holdings are refreshed before the pool resolves its requested securities.

### 6. Observability

Extend each account's existing warning to:

```text
[价格汇总] realtime=17, cache=0, stale_fallback=0, missing=0, run_reused=3
```

`run_reused` is independent of origin: a reused realtime quote still counts as realtime, and a reused cached/stale quote retains those classifications.

Add `pricing_summary` to the daily job result:

- `unique_requested`: number of distinct identities requested by eligible accounts;
- `fetch_attempted`: number of distinct identities delegated to the existing fetcher at least once;
- `fetcher_resolved`: distinct identities whose valid quote was returned by a delegated fetcher call;
- `run_reused`: number of later account/identity hits served from the pool;
- `retried`: identities delegated more than once;
- `failed_unique`: requested identities with no valid quote at run end.

These metrics deliberately do not claim actual network request counts. One delegated fetcher call can resolve from persistent cache, fixed cash/MMF logic, or a provider; the pool cannot distinguish every network request truthfully. Existing per-holding realtime/cache/stale source counts remain the source-origin view. Account-warning `run_reused` counts reused holdings to match the existing price-summary granularity, while job-level `run_reused` counts account/identity pool hits.

The receipt parser accepts the new optional field for backward compatibility and renders `同轮复用 N` only when non-zero. Existing warning strings without the field continue to parse.

## Implementation slice S1 — Same-run quote sharing

### Scope

1. Add `RunQuotePool` with identity resolution, success-only copy storage, attempt cap, and metrics.
2. Propagate one optional `run_quote_pool` argument through the daily account snapshot/valuation call path.
3. Route `ValuationService` batch fetches through the pool when present; otherwise call the existing fetcher unchanged.
4. Add independent per-account reuse counting and run-level `pricing_summary`.
5. Extend consolidated receipt parsing/rendering for optional same-run reuse.
6. Add focused regression tests and retain existing behavior for all non-daily callers.

### Primary files

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
- `tests/test_nav_history_receipt_service.py`

### Tests

- `lx` fetches FUTU/PDD/TCOM; `sy` reuses those identities and delegates only SPY/BABA.
- `BABA.US` and `BABA` share for US asset types.
- The same canonical code with different resolved markets does not share, including when both are requested in one pool call; the underlying calls are partitioned and payloads do not cross markets.
- A first failure is not cached; the next account retries and succeeds; a second failure reaches the deterministic cap.
- Two separate pools/runs share nothing.
- Invalid payloads are not admitted, and valuation still emits the blocking missing-price warning.
- A security unique to `sy` can fail without invalidating a successful `lx` result.
- Duplicate/finality/cash-flow blocked and skipped accounts trigger no price call.
- Optional Futu sync still occurs before the current holdings snapshot requests prices.
- Account warnings, boundary-accurate job `pricing_summary`, and receipt reuse rendering are correct; legacy warning format remains accepted. Persistent-cache and fixed-price resolutions are never labeled as network fetches.

### Validation

```bash
python3.12 -m pytest -q -p no:cacheprovider \
  tests/test_run_quote_pool.py \
  tests/test_valuation_service.py \
  tests/test_daily_nav_services.py \
  tests/test_nav_history_receipt_service.py
python3.12 -m pytest -q -p no:cacheprovider tests
python3.12 -X pycache_prefix=/tmp/pm_run_quote_pool -m compileall -q \
  src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```

### Acceptance criteria

- Focused and full validation pass.
- Review finds no cross-run mutable state, market-key collision, validation bypass, blocker regression, or changed provider contract.
- Existing direct `calculate_valuation`, snapshot, and single-account daily calls work without supplying a pool.
- No production write, NAV backfill, release, or deployment occurs in this work unit.

## Review and acceptance sequence

1. Run `planreview` against this goal and plan.
2. Fix accepted plan findings and re-review until pass.
3. Commit the accepted goal/plan/review artifacts.
4. Implement S1 and run focused validation.
5. Run `deepreview` on the implementation slice; fix/re-review until pass; commit the accepted slice.
6. Run full validation and aggregate `deepreview` against `origin/main`; fix/re-review until pass; commit aggregate review artifacts.
7. Push the branch, create a Draft PR, review the PR, fix/re-review if needed, and record final Gateflow closeout. Do not release, deploy, or backfill production NAV.

## Documentation decision

No public CLI/config/runbook documentation changes are required because this is an internal daily-job optimization with no new operator input. The additive job-result field and receipt wording are documented by deterministic tests and Gateflow artifacts.

## Plan review decisions

- `PR-01 accepted`: split only canonical collisions across resolved markets before delegating to the current batch planner; add a same-call cross-market regression.
- `PR-02 accepted`: replace unprovable `network_fetched` with `fetch_attempted` and `fetcher_resolved`; actual network counts remain outside this pool's evidence boundary.

## Residual risks

- Persistent cache write behavior remains `assigned to a separate pricing-cache work unit`.
- Provider-specific failures and timeout tuning remain `out of scope and fail closed`.
- Production `sy` data correction and missing NAV backfill remain `operator-owned and explicitly not authorized here`.

## Completion state

- Current gate: `plan review pass`.
- Next gate: `accepted plan commit`.
