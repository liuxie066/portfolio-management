# Daily report publisher

`publish_daily_report.py` records the current NAV, renders the daily HTML report, and writes it into local static artifact directories.
It is the only entry point that may collect daily-report data. `generate_daily_report_html.py` is renderer-only and must receive a prepared JSON bundle.

## What it does

1. Prefer the local service bundle endpoint: `POST /report/daily-bundle`
2. Reuse one priced snapshot to record NAV, build the daily report payload, and enrich return / snapshot fields
3. Fall back to the direct `skill_api` compatibility path only when the local service is unavailable
4. Render HTML
5. Write the HTML into:
   - `reports/investment-daily-YYYY-MM-DD.html`
   - `reports/latest.html`
   - `<publish-root>/investment-daily-YYYY-MM-DD/index.html`

## Usage

```bash
cd /home/node/.openclaw/workspace/portfolio-management
. .venv/bin/activate
python scripts/publish_daily_report.py
python scripts/publish_daily_report.py --account alice
```

## Useful options

```bash
python scripts/publish_daily_report.py \
  --account alice \
  --account-label lx \
  --reports-dir ./reports \
  --publish-root ../prototypes

# Service controls
python scripts/publish_daily_report.py --require-service
python scripts/publish_daily_report.py --no-service
python scripts/publish_daily_report.py --run-id manual-20260523

# These defaults can also be configured under report.* in config.json.
```

## Notes

- `--account-label` is display-only.
- `--account` controls which portfolio account is loaded; if omitted, the script uses `PORTFOLIO_ACCOUNT` / `config.json` default.
- `--service-url` overrides the local service endpoint.
- `--publish-base-url` is deprecated and ignored. The old external daily-report domain is no longer valid.
- `--require-service` fails when the local service is unavailable; `--no-service` forces the direct compatibility path.
- `--run-id` lets operators supply a trace id; otherwise the script generates one and carries it through NAV, report bundle, HTML, and publish output.
- `report.sync_futu_cash_mmf` / `PM_SYNC_FUTU_CASH_MMF` controls scheduled Futu cash/MMF sync; CLI flags still override config.
- `publish_report(...)` returns local artifact paths. `public_url` is always `null` and `public_url_status` is `disabled`.
- This script is intentionally split into three layers:
  - data collection: `build_report_data(...)`
  - HTML rendering: `render_daily_report_html(...)`
  - file publishing: `publish_report(...)`
- Legacy HTML helpers must not instantiate `PortfolioSkill` or call `build_snapshot()` / `generate_report()` directly.
