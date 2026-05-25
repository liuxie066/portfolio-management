# Decision: YAML config and Linux install assets

Date: 2026-05-25

`config.yaml` is the canonical operator config format. `PORTFOLIO_CONFIG_FILE`
points to the active config file, and environment variables keep highest
precedence for overrides. Explicit `.json` files remain readable only as a
short migration path when directly referenced.

Linux deployment uses:

- `/opt/portfolio-management/current` for code
- `/etc/portfolio-management/config.yaml` for real config
- `/etc/portfolio-management/portfolio-management.env` for systemd env
- `/var/lib/portfolio-management/.data` for runtime state/cache
- `/var/lib/portfolio-management/reports` for report output

The daily NAV systemd timer runs `pm daily-job --write --confirm --json`, with
optional `--sync-futu-cash-mmf`.
