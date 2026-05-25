# Failure Lesson: compatibility facades become hidden dependencies

Date: 2026-05-25

Leaving `PortfolioService.backend` in place after the service migration made
the codebase look Skill-first even though the real runtime path had moved to
`src/service/application.py` and `src/app/*`.

The same problem appeared in repair and audit scripts: direct
`PortfolioSkill()` construction pulled in more behavior than the script needed
and made dependency boundaries unclear. Prefer direct storage/app dependencies
for maintenance tooling.
