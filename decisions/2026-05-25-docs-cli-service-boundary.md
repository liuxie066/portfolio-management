# Decision: Docs Describe CLI + Local Service As Product Boundary

Date: 2026-05-25

README, runbooks, architecture docs, dependency graph, service docs, and Skill
docs now describe `portfolio-management` as a CLI + local HTTP service product.

Decision:
- `./pm daily-job` is the canonical scheduled NAV workflow for one or many accounts.
- `scripts/publish_daily_report.py` is the single-account HTML report publisher,
  not the multi-account scheduler.
- `skill_api.py`, `PortfolioSkill`, and MCP are compatibility adapters only.
- Public daily-report URL publishing is disabled; docs must state that outputs
  are local artifacts with `public_url=null` and `public_url_status=disabled`.
- Normal configuration docs should use `config.yaml`; JSON config is no longer
  the operator-facing path.

Rationale:
- The product is being prepared for Linux installation and daily systemd timer
  execution.
- Operators need one clear path for NAV recording, one clear path for report
  artifact generation, and no stale public-domain assumptions.
