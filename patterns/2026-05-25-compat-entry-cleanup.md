# Pattern: adapters delegate inward

Date: 2026-05-25

Keep product entrypoints thin:

- CLI falls back to `PortfolioService` directly when HTTP is unavailable.
- Historical Python API functions remain in `skill_api.py` only as caller
  adapters.
- App behavior lives in `src/app/*`, and service wiring lives in
  `src/service/application.py`.

Delete alias modules once every real caller has moved to the canonical service.
