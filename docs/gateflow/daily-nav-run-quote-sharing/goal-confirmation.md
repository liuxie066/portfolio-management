# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `daily-nav-run-quote-sharing`
- Branch: `fix/daily-nav-run-quote-sharing`
- Base: `origin/main@a21bb1d`
- Artifact path: `docs/gateflow/daily-nav-run-quote-sharing/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `启动修复代码`, followed by `确认` for the protected-branch change
- Confirmed at: `2026-07-23 +0800`

## Why this work unit exists

The 2026-07-23 NAV run `daily-nav-job-20260723T081022216404-multi-9adec476` wrote `lx` and `hb` but rejected `sy` because SPY, BABA, FUTU, PDD, and TCOM had no usable price. FUTU, PDD, and TCOM were already valued successfully for `lx` earlier in the same multi-account run, but every account built an isolated local `prices` mapping and called the provider chain again. The existing global/persistent price cache did not prevent the repeated calls because the batch pricing path reads that cache but does not persist every successful batch result into it.

## Target outcome

Add one explicit run-scoped quote-sharing context to the canonical multi-account daily NAV path so later accounts reuse earlier successful quotes with the same canonical security and market, while preserving fail-closed valuation, account ordering, per-account timeout budgets, provider behavior, and account-level failure isolation.

## Success signals

1. In one multi-account daily run, a later account reuses an earlier valid quote for the same `(canonical_code, market_type)` without calling the provider chain again.
2. Raw aliases such as `BABA.US` and `BABA` share when their resolved market is the same; the same code in different markets does not share.
3. Only positive finite `price` and `cny_price` payloads enter the pool. Reused payloads still pass the existing valuation validation and cannot bypass blocking warnings.
4. A missing/invalid first attempt is not cached as a quote. A later account may retry it once; each identity has at most two run-level network attempts.
5. The pool is local to one `DailyNavJobService.run()` call. Separate runs and concurrent service instances share no mutable state.
6. Existing account order, duplicate/finality/cash-flow blockers, optional Futu cash/MMF sync timing, and each account's independent price deadline remain unchanged.
7. Account warnings expose `run_reused=N` without changing the underlying realtime/cache/stale classification. The job result exposes deterministic run-level pricing metrics.
8. Focused tests, the full suite, compile checks, and review gates pass.

## Scope boundary

### In scope

- One small run-scoped quote pool owned by `DailyNavJobService.run()`.
- Explicit optional context propagation through the current daily account snapshot/valuation call path.
- Canonical `(code, market)` identity, success-only storage, copy-on-return, and two-attempt run-level cap.
- Account warning, job result, and consolidated receipt observability for same-run reuse.
- Deterministic unit/integration tests for reuse, isolation, retry, validation, blockers, and receipt rendering.

### Out of scope

- Changing Finnhub, Sina, Futu, Yahoo, Tencent, FX, provider priority, timeout, or fallback behavior.
- Turning the run pool into a process-global, module-global, disk, Redis, or Feishu cache.
- Changing persistent `PriceCachePolicy` write semantics.
- Eagerly prefetching the union of all account holdings.
- Fixing the independent `sy` `CNY-CASH` row whose currency is `HKD`.
- Backfilling the missing 2026-07-22 `sy` NAV, releasing, deploying, or mutating production data.

## Direct code evidence

- `ValuationService.calculate_valuation()` creates a fresh local `prices` dict for every account and directly invokes `price_fetcher.fetch_batch()` with that account's holdings.
- `DailyNavJobService.run()` processes accounts sequentially but passes no shared pricing context to the account runner.
- `PriceCachePolicy.get()` reads persistent cache entries, while successful batch payloads are not guaranteed to be persisted by the batch fetch path; therefore the cache cannot provide same-run sharing semantics.
- `ValuationService` already validates `price` and `cny_price` with `positive_finite_decimal()` and emits blocking missing-price warnings, which must remain authoritative.
- The receipt parser currently understands only realtime/cache/stale/missing counts and has no independent same-run reuse metric.

## Minimality decision

- Reuse the existing provider fetcher for misses instead of adding a second pricing engine or prefetch stage.
- Keep the pool as an explicit per-run value passed only through the daily NAV call path; do not attach it to `PortfolioManager` or `PriceFetcher`.
- Preserve successful payload content and underlying origin metadata; add only a copied `is_from_run_pool` marker for observability.
- Use one coherent implementation slice because the core, propagation, metrics, and regression tests form one atomic behavioral change.

## Blocking open questions

- None. The user approved the incremental same-run sharing design and authorized implementation.

## Residual risks

- A provider can still fail for a security no earlier account resolved: `preserved fail-closed behavior`.
- Successful quotes can become older during a long run: `bounded to the existing sequential run duration; no cross-run reuse`.
- Provider internals still discard diagnostics unless `--debug-internal` is used: `unchanged operational behavior; not required for this fix`.
- Persistent batch-cache semantics remain incomplete: `assigned to a separate pricing-cache work unit`.

## Completion state

- Current gate: `goal confirmation pass`.
- Next gate: `plan review`.
