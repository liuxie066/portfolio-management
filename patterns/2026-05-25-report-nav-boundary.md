# Pattern: extract report calculations into domain modules

Date: 2026-05-25

NAV performance calculations live in `src/domain/nav/performance.py`.
Report-facing holdings projections live in
`src/domain/report/holdings_projection.py`.

Application services should compose these domain helpers around explicit facts:
`snapshot`, `nav_record`, and `nav_history`. Avoid passing through a broad
report facade when a narrower fact-driven builder is available.
