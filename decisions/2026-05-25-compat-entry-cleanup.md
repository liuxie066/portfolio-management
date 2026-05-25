# Decision: compatibility entry cleanup

Date: 2026-05-25

`FullReportService` was only a compatibility alias for `ReportQueryService` and
has been removed. Full-report behavior is owned directly by
`ReportQueryService`.

`skill_api.py` and `PortfolioSkill` remain because they are still the historical
Python/Skill API surface, but they are explicitly compatibility adapters. The
CLI direct fallback should use `PortfolioService`, not `skill_api.py`.
