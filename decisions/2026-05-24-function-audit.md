# Function Audit Cleanup

- The old external daily-report publish domain is invalid. Daily publishing now returns local artifact paths only; `public_url` stays `null` with `public_url_status=disabled`.
- `--publish-base-url` remains accepted for CLI compatibility but is ignored. Runtime config no longer maps `report.publish_base_url` from `OPENCLAW_PUBLISH_BASE_URL`.
- The OpenClaw public publish wrapper was removed because it existed only to copy HTML into the invalid public-domain path.
- `skill_api.py` account discovery and multi-account overview now delegate to `AccountService` instead of carrying duplicated account normalization, account scanning, and value-breakdown formulas.
- Unused formatting helpers were removed from `scripts/publish_daily_report.py`; HTML formatting belongs in `scripts/generate_daily_report_html.py`.
