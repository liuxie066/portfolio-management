# Decision: remove Skill-first residue from service and repair paths

Date: 2026-05-25

`PortfolioService` is the application boundary for CLI, HTTP, publisher, and
scheduled jobs. It must not carry a `backend` dependency or lazy-load
`skill_api.py`.

`scripts/nav_history_repair.py` uses `src/maintenance/nav_history_repair/*`
with `src.storage.create_storage()` and `PortfolioManager` directly. Audit
scripts that inspect Feishu should also create storage directly instead of
instantiating `PortfolioSkill`.

Daily report publishing writes local static artifacts only. The old public
daily-report domain is invalid; `public_url` stays `null` and
`public_url_status` stays `disabled`.
