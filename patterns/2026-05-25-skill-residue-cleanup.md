# Pattern: keep adapters thin after service migration

Date: 2026-05-25

When moving behavior out of `skill_api.py`, remove both the production edge and
the test mock edge. Tests should verify the active service/app/storage calls
directly instead of asserting that a legacy backend mock was not called.

Maintenance commands should have a small context object that names their real
dependencies (`account`, `storage`, `portfolio`). This keeps one-off repair
scripts testable without making `PortfolioSkill` a hidden service locator.
