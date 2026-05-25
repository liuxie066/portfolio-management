# Pattern: Keep Operator Docs Around The Canonical Workflow

Date: 2026-05-25

When updating docs:
- Start from the operator goal: config check, duplicate audit, daily-job dry-run,
  daily-job write, report publish, verification.
- Keep `daily-job` as the scheduled NAV workflow in README/runbook/deploy docs.
- Mention `publish_daily_report.py` only as the report artifact publisher.
- Describe compatibility surfaces as adapters that delegate inward.
- Prefer paths that work in a generic Linux deployment, for example
  `/opt/portfolio-management/current` and
  `/etc/portfolio-management/config.yaml`.
- If a legacy public URL or class name is mentioned, make it clear it is removed
  or disabled rather than leaving it as an active operator path.
