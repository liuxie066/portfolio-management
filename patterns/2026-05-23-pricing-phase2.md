# Pricing Phase 2

- Move policy first, then providers: structured quote results, cache semantics, and FX services can be extracted without rewriting the optimized batch path.
- Preserve legacy facade behavior while adding typed internals: return legacy dicts from `PriceFetcher.fetch()` but test `PriceService.fetch_quote()` directly.
- Route compatibility facades through structured services, then convert back to legacy payloads at the edge.
- Move data-source implementation before deleting adapters: callers can keep old methods while planner/service use provider modules directly.
- Keep compatibility adapters thin and add direct planner/provider tests before deleting implementation from the facade.
- When shrinking a facade, move the canonical helper first, keep the old method name as a one-line wrapper, then move tests to the canonical helper.
- Keep dependency graphs aligned with runtime ownership after each extraction; stale graph edges make follow-up module analysis unreliable.
- Validate price refactors with targeted price tests, full pytest, the minimal runner, compileall, and `git diff --check`.
