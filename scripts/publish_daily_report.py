#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = REPO_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src import config as app_config

# Prefer the service bundle so NAV, report payload, and page fields share one snapshot.


@dataclass
class PublishConfig:
    repo_root: Path
    workspace: Path
    reports_dir: Path
    publish_root: Path
    account_label: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record NAV, render daily report HTML, and publish it to a static directory.")
    parser.add_argument("--account", default=None, help="Account to operate on. Defaults to config/PORTFOLIO_ACCOUNT.")
    parser.add_argument(
        "--account-label",
        default=app_config.get("report.account_label", app_config.get_account()),
        help="Display-only account label shown in the HTML report.",
    )
    parser.add_argument(
        "--reports-dir",
        default=str(app_config.get("report.reports_dir", str(REPO_ROOT / "reports"))),
        help="Directory for generated HTML report files.",
    )
    parser.add_argument(
        "--publish-root",
        default=str(app_config.get("report.publish_root", str(WORKSPACE / "prototypes"))),
        help="Root directory for published static pages.",
    )
    parser.add_argument("--price-timeout", type=int, default=30, help="Price fetch timeout in seconds.")
    parser.add_argument("--nav-date", default=None, help="NAV date to record (YYYY-MM-DD). Defaults to Beijing today.")
    nav_write_group = parser.add_mutually_exclusive_group()
    nav_write_group.add_argument(
        "--write-nav",
        dest="write_nav",
        action="store_true",
        help="Persist the report NAV row; requires --confirm.",
    )
    nav_write_group.add_argument(
        "--dry-run",
        dest="write_nav",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--confirm", action="store_true", help="Required with --write-nav.")
    parser.add_argument("--use-bulk-nav-upsert", action="store_true", help="Persist NAV through FeishuStorage.write_nav_records (single-row bulk mode).")
    parser.add_argument("--no-html", action="store_true", help="Do not render HTML; only record NAV + generate JSON bundle.")
    parser.add_argument("--no-publish", action="store_true", help="Do not write HTML files into reports/publish dirs.")
    parser.add_argument("--quiet", action="store_true", help="No stdout on success (scheduled mode).")
    parser.add_argument("--debug-internal", action="store_true", help="Do not suppress internal stdout prints (debug only).")
    parser.add_argument("--service-url", default=None, help="Local service URL; defaults to config/PORTFOLIO_SERVICE_URL.")
    parser.add_argument("--service-timeout", type=float, default=0.5, help="Local service timeout seconds before fallback.")
    parser.add_argument("--no-service", action="store_true", help="Bypass local service and call the local application service directly.")
    parser.add_argument("--require-service", action="store_true", help="Fail instead of falling back when local service is unavailable.")
    parser.add_argument("--run-id", default=None, help="Operator-supplied run id for tracing NAV/report artifacts.")
    parser.add_argument(
        "--sync-futu-cash-mmf",
        dest="sync_futu_cash_mmf",
        action="store_true",
        help="Sync Futu cash/MMF balances into holdings before building the report snapshot.",
    )
    parser.add_argument(
        "--no-sync-futu-cash-mmf",
        dest="sync_futu_cash_mmf",
        action="store_false",
        help="Disable Futu cash/MMF sync even if enabled in config/env.",
    )
    parser.add_argument(
        "--sync-futu-dry-run",
        dest="sync_futu_dry_run",
        action="store_true",
        help="Preview Futu cash/MMF sync without writing holdings (default).",
    )
    parser.add_argument(
        "--sync-futu-write",
        dest="sync_futu_dry_run",
        action="store_false",
        help="Actually write Futu cash/MMF holdings when --sync-futu-cash-mmf is set.",
    )
    parser.set_defaults(
        write_nav=False,
        sync_futu_cash_mmf=app_config.get_bool("report.sync_futu_cash_mmf", False),
        sync_futu_dry_run=app_config.get_bool("report.sync_futu_dry_run", True),
    )
    args = parser.parse_args()
    if args.write_nav and not args.confirm:
        parser.error("--write-nav requires --confirm")
    return args


@contextlib.contextmanager
def _suppress_internal_stdout(enabled: bool) -> Iterator[None]:
    """Context manager to suppress noisy internal stdout prints."""
    if not enabled:
        yield
        return
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
        yield


def build_config(args: argparse.Namespace) -> PublishConfig:
    return PublishConfig(
        repo_root=REPO_ROOT,
        workspace=WORKSPACE,
        reports_dir=Path(args.reports_dir),
        publish_root=Path(args.publish_root),
        account_label=args.account_label,
    )


def build_report_data(
    price_timeout: int,
    dry_run: bool = True,
    confirm: bool = False,
    use_bulk_nav_upsert: bool = False,
    sync_futu_cash_mmf: bool = False,
    sync_futu_dry_run: bool = True,
    account: Optional[str] = None,
    service_url: Optional[str] = None,
    service_timeout: float = 0.5,
    no_service: bool = False,
    require_service: bool = False,
    run_id: Optional[str] = None,
    nav_date: Optional[str] = None,
) -> dict[str, Any]:
    """Build a consistent bundle for publishing.

    Performance notes:
    - Avoid fetching holdings/prices more than once.
    - Use one snapshot for both record_nav and report generation.
    """
    from src.run_id import new_run_id

    if not dry_run and not confirm:
        raise ValueError("NAV persistence requires confirm=True")

    resolved_run_id = run_id or new_run_id("daily-report", account)

    if not no_service:
        try:
            bundle = _build_report_data_via_service(
                price_timeout=price_timeout,
                dry_run=dry_run,
                confirm=confirm,
                use_bulk_nav_upsert=use_bulk_nav_upsert,
                sync_futu_cash_mmf=sync_futu_cash_mmf,
                sync_futu_dry_run=sync_futu_dry_run,
                account=account,
                service_url=service_url,
                service_timeout=service_timeout,
                run_id=resolved_run_id,
                nav_date=nav_date,
            )
            bundle.setdefault("run_id", resolved_run_id)
            return bundle
        except Exception as e:
            from src.service.client import PortfolioServiceUnavailable

            if isinstance(e, PortfolioServiceUnavailable) and not require_service:
                pass
            else:
                raise

    if require_service:
        raise RuntimeError("local service is unavailable and --require-service was set")

    return _build_report_data_direct(
        price_timeout=price_timeout,
        dry_run=dry_run,
        confirm=confirm,
        use_bulk_nav_upsert=use_bulk_nav_upsert,
        sync_futu_cash_mmf=sync_futu_cash_mmf,
        sync_futu_dry_run=sync_futu_dry_run,
        account=account,
        run_id=resolved_run_id,
        nav_date=nav_date,
    )


def _build_report_data_via_service(
    *,
    price_timeout: int,
    dry_run: bool,
    confirm: bool,
    use_bulk_nav_upsert: bool,
    sync_futu_cash_mmf: bool,
    sync_futu_dry_run: bool,
    account: Optional[str],
    service_url: Optional[str],
    service_timeout: float,
    run_id: str,
    nav_date: Optional[str],
) -> dict[str, Any]:
    from src.service.client import PortfolioServiceClient

    client = PortfolioServiceClient(base_url=service_url, timeout=service_timeout)
    kwargs = {
        "account": account,
        "price_timeout": price_timeout,
        "dry_run": dry_run,
        "confirm": confirm,
        "overwrite_existing": True if dry_run else False,
        "use_bulk_persist": use_bulk_nav_upsert,
        "sync_futu_cash_mmf": sync_futu_cash_mmf,
        "sync_futu_dry_run": sync_futu_dry_run,
        "run_id": run_id,
    }
    if nav_date is not None:
        kwargs["nav_date"] = nav_date
    return client.daily_report_bundle(**kwargs)


def _build_report_data_direct(
    *,
    price_timeout: int,
    dry_run: bool = True,
    confirm: bool = False,
    use_bulk_nav_upsert: bool = False,
    sync_futu_cash_mmf: bool = False,
    sync_futu_dry_run: bool = True,
    account: Optional[str] = None,
    run_id: Optional[str] = None,
    nav_date: Optional[str] = None,
) -> dict[str, Any]:
    from src.service.application import PortfolioService

    kwargs = {
        "account": account,
        "price_timeout": price_timeout,
        "dry_run": dry_run,
        "confirm": confirm,
        "overwrite_existing": True if dry_run else False,
        "use_bulk_persist": use_bulk_nav_upsert,
        "sync_futu_cash_mmf": sync_futu_cash_mmf,
        "sync_futu_dry_run": sync_futu_dry_run,
        "run_id": run_id,
    }
    if nav_date is not None:
        kwargs["nav_date"] = nav_date
    bundle = PortfolioService().daily_report_bundle(**kwargs)
    if not bundle.get("success"):
        raise RuntimeError(json.dumps(bundle, ensure_ascii=False))
    return bundle


def render_daily_report_html(report_bundle: dict[str, Any], config: PublishConfig) -> tuple[str, str]:
    """Render daily report HTML using the single GitHub-style template.

    We keep only ONE template to reduce maintenance cost and avoid style drift.
    """
    # Reuse the GitHub-style renderer from generate_daily_report_html.py
    from scripts import generate_daily_report_html as gh

    # build_snapshot() is created in build_report_data(); reuse it to avoid extra price fetch.
    snapshot = report_bundle.get('snapshot') or {}

    # gh.render_html expects a bundle with keys: report/full/snapshot
    report = report_bundle.get('report') or {}
    nav_result = report_bundle.get('nav_result') or {}

    full = {
        'warnings': (report.get('warnings') or nav_result.get('warnings') or []),
        'run_id': report_bundle.get('run_id'),
    }

    dt = report.get('date') or date.today().isoformat()
    html = gh.render_html({'report': report, 'full': full, 'snapshot': snapshot})
    return dt, html


def publish_report(report_date: str, html: str, config: PublishConfig) -> dict[str, Any]:
    slug = f"investment-daily-{report_date}"
    report_path = config.reports_dir / f"{slug}.html"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html, encoding="utf-8")
    latest_path = config.reports_dir / "latest.html"
    latest_path.write_text(html, encoding="utf-8")

    out_dir = config.publish_root / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    return {
        "date": report_date,
        "slug": slug,
        "report_file": str(report_path),
        "latest_file": str(latest_path),
        "publish_dir": str(out_dir),
        "relative_path": f"{slug}/index.html",
        "public_url": None,
        "public_url_status": "disabled",
    }


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def main() -> None:
    args = parse_args()
    config = build_config(args)

    # Speed: scheduled daily report can skip expensive NAV runtime validation.
    # Enable only for this script via env var to avoid impacting other entry points.
    if app_config.get_bool("report.disable_nav_runtime_validation", False):
        os.environ["PORTFOLIO_NAV_DISABLE_RUNTIME_VALIDATION"] = "1"

    timings: dict[str, int] = {}
    t0 = _now_ms()

    t1 = _now_ms()
    with _suppress_internal_stdout(enabled=(not bool(args.debug_internal))):
        report_bundle = build_report_data(
            price_timeout=args.price_timeout,
            dry_run=not bool(args.write_nav),
            confirm=bool(args.confirm),
            use_bulk_nav_upsert=bool(args.use_bulk_nav_upsert),
            sync_futu_cash_mmf=bool(args.sync_futu_cash_mmf),
            sync_futu_dry_run=bool(args.sync_futu_dry_run),
            account=args.account,
            service_url=args.service_url,
            service_timeout=float(args.service_timeout),
            no_service=bool(args.no_service),
            require_service=bool(args.require_service),
            run_id=args.run_id,
            nav_date=args.nav_date,
        )
    timings['build_report_data_ms'] = _now_ms() - t1

    # Fast mode: only compute bundle (record_nav + generate_report + get_nav)
    if bool(args.no_html):
        timings['total_ms'] = _now_ms() - t0
        out = {
            "success": True,
            "account": report_bundle.get("account"),
            "run_id": report_bundle.get("run_id"),
            "nav_result": report_bundle.get("nav_result"),
            "report": report_bundle.get("report"),
            "nav_snapshot": report_bundle.get("nav_snapshot"),
            "stage_timings": report_bundle.get("stage_timings"),
            "futu_sync_result": report_bundle.get("futu_sync_result"),
            "timings": timings,
        }
        if not bool(args.quiet):
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    t2 = _now_ms()
    report_date, html = render_daily_report_html(report_bundle, config)
    timings['render_html_ms'] = _now_ms() - t2

    publish_result = None
    if not bool(args.no_publish):
        t3 = _now_ms()
        publish_result = publish_report(report_date, html, config)
        if publish_result is not None and report_bundle.get("run_id"):
            publish_result["run_id"] = report_bundle.get("run_id")
        timings['publish_ms'] = _now_ms() - t3

    timings['total_ms'] = _now_ms() - t0

    result = {
        "success": True,
        "account": report_bundle.get("account"),
        "run_id": report_bundle.get("run_id"),
        "date": report_date,
        "nav_result": report_bundle["nav_result"],
        "futu_sync_result": report_bundle.get("futu_sync_result"),
        "publish": publish_result,
        "timings": timings,
    }
    if not bool(args.quiet):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    
if __name__ == "__main__":
    main()
