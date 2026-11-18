# Code Review

## Scope

- Mode: current changes
- Branch: `cindy/vibrant-bohr`
- Base: `04df26e` (current HEAD; released v0.2.15 tree)
- Output file: `docs/reviews/code-review-20261118-161641.md`
- Included scope: `src/service/http.py`, `docs/service.md`, `tests/test_daily_report_http_serialization.py`, and `docs/gateflow/product-quality-20261118/postdeploy-correction.md`.
- Excluded scope: ignored `reports/product-quality/` receipts and logs were treated as evidence only; no other source paths, PR metadata, or production state were reviewed in this pass.
- Parallel review coverage: none.

## Findings

未发现实质性问题。

The HTTP handler makes a shallow copy of the successful bundle and, when its snapshot is a dictionary, makes a new snapshot dictionary omitting only `normalized_valuation` (`src/service/http.py:782-792`). It leaves the direct `PortfolioService.daily_report_bundle()` return untouched. The recorded writer path requires that exact object before persistence (`src/app/account_nav_recorder_service.py:347-364`), while the report renderer consumes the serializable snapshot valuation/report/NAV fields (`scripts/publish_daily_report.py:274-306`). The route is an internal service endpoint, not the `/api/v1` OM contract; no schema or consumer migration is implicated.

The added regression creates both issuer-backed and compatibility `NormalizedValuationSnapshot` values, attaches each to its valuation, traverses the real FastAPI route, and checks the response is serializable, omits the normalized object/sentinel, retains report and valuation data, and leaves the original application bundle and eligibility state intact (`tests/test_daily_report_http_serialization.py:10-32`). A separate case preserves an application failure payload without a snapshot (`:35-39`). The project service documentation accurately records the transport boundary. The correction note distinguishes the post-deployment E2E failure on v0.2.15 from the post-fix candidate and keeps any future release as a separate boundary.

Baseline adjudication: the original failure is pre-existing, not introduced by this patch. The affected HTTP, application, daily-bundle, recorder, snapshot-contract, and source-snapshot paths are unchanged from v0.2.14. On the unchanged path, `PortfolioReadService` places `normalized_valuation` into the snapshot (`src/app/portfolio_read_service.py:325-329`), `DailyAccountNavService` returns that snapshot (`src/app/daily_account_nav_service.py:138-149`), and the normalized dataclass contains the in-process `_official_issuer` object (`src/domain/snapshot_contracts.py:879-899`). Before the patch, the HTTP handler returned the bundle as-is; this was directly reproduced by the pre-fix post-deployment E2E attempt. The candidate filters the capability only at the HTTP transport boundary, which is the correct owner and preserves the direct application writer path.

The supplied candidate E2E log records six passes, including the native publisher through HTTP to HTML. The supplied full-suite and required-check results are reported by the parent as passed; this reviewer did not rerun them or make production calls.

## Open Questions

None for the reviewed patch.

## Residual Risk

- The correction is not part of deployed v0.2.15. Until a separately authorized release and upgrade, service-mode daily report publishing on that deployed version retains the known serialization failure.
- This review did not exercise a live ledger, broker, or message flow. Synthetic E2E and existing production read-only evidence remain bounded to the cases in their receipts.
- Full pytest (2,020 passed), compileall, Ruff 0.15.14, OpenAPI drift, diff checks, and the six-case candidate E2E are based on the parent-provided/current local records; they were not rerun for this review.
