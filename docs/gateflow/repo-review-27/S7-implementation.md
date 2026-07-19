# S7 Implementation Artifact

- Gate: `implementation -> code review`
- Work unit: `repo-review-27 correctness hardening`
- Slice: `S7-service-safety`
- Base commit: `c76b916`
- Status: `complete; code review passed`
- Recorded at: `2026-07-19 19:25:52 +0800`
- Artifact path: `docs/gateflow/repo-review-27/S7-implementation.md`

## Objective

Fix source findings 15 and 16: prevent ambiguous service write failures from replaying through the direct backend, and enforce the loopback-only boundary inside the ASGI application so direct Uvicorn startup cannot bypass it.

## Changed production files

- `src/service/client.py` / `src/service/__init__.py` — transport failures on POST raise the distinct `PortfolioServiceOutcomeUnknown` contract, while read transport failures retain `PortfolioServiceUnavailable` and may use read fallback.
- `scripts/pm.py` — `_service_or_fallback(..., allow_fallback=...)` requires explicit read/write classification; Futu sync, NAV record, daily bundle, and daily NAV job explicitly fail closed. `--no-service` remains the intentional single direct path.
- `src/service/bind.py` / `src/service/http.py` — strict actual-client IP loopback check, explicit `PORTFOLIO_SERVICE_ALLOW_REMOTE` parsing, and fail-closed HTTP middleware that ignores Host and forwarding headers.
- `scripts/serve.py` — `--allow-remote` sets the app environment before Uvicorn import/start and restores the caller environment after Uvicorn exits.
- `docs/service.md` — documents ambiguous-write handling, application-level loopback enforcement, direct-ASGI override, and unauthenticated remote-mode risk.

## Changed tests

- `tests/test_pm_cli.py` — read unavailability falls back exactly once; write unavailability never invokes the direct backend and returns outcome-unknown/no-blind-retry guidance; existing `--no-service` coverage proves one direct execution.
- `tests/test_service_client.py` — POST transport errors are outcome-unknown and are intentionally not `PortfolioServiceUnavailable`.
- `tests/test_service_http.py` — IPv4/IPv6 loopback pass, remote clients receive 403, spoofed Host/X-Forwarded-For do not bypass, module-level app defaults fail closed, and explicit parameter/environment override permits remote clients.
- `tests/test_service_cli.py` (new) — wrapper remote flag reaches the ASGI import/start environment and does not leak after the server returns.

## Invariants proved

- A POST transport failure cannot enter legacy `PortfolioServiceUnavailable` fallback consumers, including the official daily report service/direct boundary.
- CLI write-capable service commands never invoke direct fallback, including dry-run invocations; a generic unavailable signal is conservatively reported as outcome unknown.
- The operator-facing failure states that the request may have executed, direct fallback was not attempted, blind retry is unsafe, and `--no-service` is only an intentional bypass.
- Read-only commands retain current automatic fallback behavior.
- `--no-service` bypasses service construction and invokes the direct backend exactly once.
- ASGI access control uses only `request.client.host`; Host and forwarding headers do not affect authorization.
- Only IPv4/IPv6 loopback addresses pass by default. Explicit `allow_remote=True` or `PORTFOLIO_SERVICE_ALLOW_REMOTE=1` is required for remote clients.
- `scripts/serve.py --allow-remote` sets the environment before Uvicorn starts, including reload child inheritance, and restores prior process state afterward.

## Validation

- Required S7 focused command -> `67 passed`.
- Focused service CLI/HTTP isolation rerun -> `8 passed`.
- Full repository suite -> `657 passed`.
- `python3 -m compileall -q src scripts skill_api.py` -> passed.
- `git diff --check` -> passed.

## Documentation decision

- Service runbook documentation was required because ambiguous write outcomes and the explicit unauthenticated remote override are operator-visible safety contracts.
- No installer change was required: the generated systemd unit remains fixed to loopback and never enables the remote override.

## Residual risks

- Explicit remote mode remains unauthenticated and is `documented explicit operator risk`; it must be placed behind an authenticated network boundary.
- The client cannot prove whether a POST transport failure occurred before or after dispatch, so it conservatively reports every such failure as outcome unknown. This is `fixed by fail-closed behavior`, not by automatic retry.
- Determining the result of an ambiguous write still requires operator inspection or an existing run/request identifier; a new status API is outside this approved slice and is `assigned to a later work unit if operational evidence requires it`.

## Review artifact

- Initial code review: `docs/reviews/code-review-20260719-192824.md` — 1 accepted finding.
- Fix artifact: `docs/gateflow/repo-review-27/S7-fix.md`.
- Re-review: `docs/reviews/code-review-20260719-192825.md` — accepted finding fixed; no remaining findings.

## Completion state

- Current gate: `accepted slice commit`.
- Next entry point: create `gateflow: accept repo-review-27 S7-service-safety`, then begin aggregate validation and DeepReview.
