# Failure: Install Docs Without A Stable Launcher Are Fragile

Date: 2026-05-25

Issue:
- The earlier Linux deployment path required operators to remember the checkout
  path, virtualenv Python, and `PORTFOLIO_CONFIG_FILE` environment each time.
- That makes scheduled jobs easier to misconfigure and makes manual startup
  different from systemd startup.

Avoidance:
- Generate one launcher (`pm`) that pins the app checkout, venv Python, and
  default config file.
- Let systemd use the same launcher as operators.
- Keep docs centered on `pm config doctor`, `pm daily-job`, and
  `systemctl start portfolio-nav-daily.service`.
