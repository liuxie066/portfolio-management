# Gateflow S2 Fix Artifact — DeepReview

- Gate: `fix`
- Work unit: `nav-valuation-evidence-replay`
- Slice: `S2`
- Review artifact: `docs/reviews/code-review-20260814-180300.md`
- Status: `fix and re-review complete`

## Finding decisions

### DR-S2-01 — accepted — 已修复

Historical routing now reuses the existing ETF code classifier before the
generic fund branch. A 15x/5xx exchange code therefore uses the exact-date
OpenD close even when its compatibility type is `FUND`. The provider-derived
currency must also match the validated Holding.

### DR-S2-02 — accepted — 已修复

Artifact load now symmetrically validates required audit fields, timestamp
syntax, and the allowed preparation values in addition to hash and valuation
integrity. A rehashed artifact with an empty source run is rejected.

### DR-S2-03 — accepted — 已修复

Focused tests now exercise the production OpenD DataFrame/return-code parser
and Eastmoney JSON latest-eligible-date parser with injected responses and no
network access.

## Re-review

- Confirmed generic `FUND + 159941` routes to `SZ.159941` OpenD history.
- Confirmed fixed, exchange, and fund prices bind native currency.
- Confirmed malformed audit fields fail at evidence load.
- Focused suite: 67 passed.
- Compile check and `git diff --check`: passed.
- Final finding status: no unresolved high or critical finding in S2.
