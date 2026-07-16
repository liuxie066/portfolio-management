#!/usr/bin/env python3
"""portfolio-management CLI for service-first workflows.

Design goals:
- Provide a few common read-only commands.
- Prefer the local HTTP service, with direct application fallback.
- Fast defaults (no writes; avoid slow realtime price fetch unless asked).
- Human-readable by default; `--json` for automation.

Usage examples:
  . .venv/bin/activate
  ./pm daily --json
  ./pm daily --write --confirm
  python scripts/pm.py cash
  python scripts/pm.py cash --account alice
  python scripts/pm.py futu sync --account alice --json
  python scripts/pm.py accounts
  python scripts/pm.py overview --accounts alice,bob --json
  python scripts/pm.py holdings
  python scripts/pm.py holdings --include-price --timeout 25
  python scripts/pm.py nav
  python scripts/pm.py nav record --write --confirm
  python scripts/pm.py cash-flow reconcile --account alice
  python scripts/pm.py cash-flow reconcile --account alice --apply --confirm
  python scripts/pm.py positions distribution --json
  python scripts/pm.py report daily --preview
  python scripts/pm.py report daily --preview --timeout 25 --json

Safety:
- Write paths default to dry-run and require explicit confirmation.
- `report` is preview-only. Official daily data/HTML publishing must use
  `scripts/publish_daily_report.py`.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path for direct local imports.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _call_backend(args, call):
    if bool(getattr(args, "debug_internal", False)):
        return call()
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull):
        return call()


def _exit_code(payload) -> int:
    if isinstance(payload, int):
        return payload
    if isinstance(payload, dict) and payload.get("success") is False:
        return 1
    return 0


def _dump(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
    else:
        # simple human-readable
        if isinstance(obj, dict):
            print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))
        else:
            print(obj)


def _money(value) -> str:
    try:
        return f"¥{float(value or 0):,.2f}"
    except (TypeError, ValueError):
        return "¥0.00"


def _pct(value) -> str:
    try:
        return f"{float(value or 0) * 100:.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _print_distribution(payload):
    if not isinstance(payload, dict):
        print(payload)
        return
    if payload.get("success") is False:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    if payload.get("by_asset") is not None:
        _print_asset_distribution(payload)
        return

    print(f"Total value: {_money(payload.get('total_value'))}")

    sections = (
        ("By type", "by_type", "type"),
        ("By broker", "by_broker", "broker"),
        ("By currency", "by_currency", "currency"),
    )
    for title, key, label_key in sections:
        rows = payload.get(key) or []
        if not rows:
            continue
        print(f"\n{title}")
        for row in rows:
            label = row.get(label_key) or "unknown"
            print(f"  {label}: {_money(row.get('value'))} ({_pct(row.get('ratio'))})")


def _qty(value) -> str:
    try:
        return f"{float(value or 0):,.4f}"
    except (TypeError, ValueError):
        return "0.0000"


def _print_asset_distribution(payload):
    rows = payload.get("by_asset") or []
    if not rows:
        print("No asset positions found.")
        return

    include_value = "total_value" in payload
    if include_value:
        print(f"Total value: {_money(payload.get('total_value'))}")

    accounts = payload.get("accounts") or []
    if accounts:
        print(f"Accounts: {', '.join(str(a) for a in accounts)}")

    print("")
    for row in rows:
        code = row.get("code") or "unknown"
        name = row.get("name") or code
        asset_type = row.get("normalized_type") or row.get("type") or "unknown"
        line = f"{code} ({name}) [{asset_type}] qty={_qty(row.get('quantity'))}"
        if include_value:
            line += f" value={_money(row.get('value'))} ({_pct(row.get('ratio'))})"
        else:
            line += f" ({_pct(row.get('quantity_ratio'))})"
        print(line)

        breakdown = row.get("breakdown") or []
        for item in breakdown:
            account = item.get("account") or "default"
            broker = item.get("broker") or "-"
            detail = f"    {account}/{broker}: qty={_qty(item.get('quantity'))}"
            if include_value and "value" in item:
                detail += f" value={_money(item.get('value'))}"
            print(detail)


def _print_daily(payload):
    if not isinstance(payload, dict):
        print(payload)
        return
    if payload.get("success") is False:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    account = payload.get("account") or "default"
    nav = payload.get("nav") or {}
    print(f"Daily NAV [{account}]")
    print(f"  date: {nav.get('date')}")
    print(f"  mode: {'dry-run' if nav.get('dry_run') else 'write'}")
    print(f"  nav: {nav.get('nav')}")
    print(f"  shares: {nav.get('shares')}")
    print(f"  total value: {_money(nav.get('total_value'))}")

    distribution = payload.get("distribution") or {}
    if distribution:
        print("")
        _print_distribution(distribution)


def _print_cash_flow_reconcile(payload):
    if not isinstance(payload, dict):
        print(payload)
        return
    if payload.get("success") is False:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    account = payload.get("account") or "all"
    mode = "dry-run" if payload.get("dry_run") else "apply"
    print(f"Cash flow reconcile [{account}]")
    print(f"  mode: {mode}")
    print(f"  scanned: {payload.get('scanned', 0)}")
    print(f"  changes: {payload.get('change_count', 0)}")
    print(f"  updated: {payload.get('updated_count', 0)}")
    if payload.get("error_count"):
        print(f"  errors: {payload.get('error_count')}")

    for row in payload.get("rows") or []:
        if row.get("status") not in {"pending", "error"}:
            continue
        label = row.get("record_id") or "(no record id)"
        if row.get("status") == "error":
            print(f"  - {label}: error: {row.get('error')}")
            continue
        fields = ", ".join(sorted((row.get("updates") or {}).keys()))
        print(f"  - {label}: fill {fields}")


def _emit_distribution(payload, as_json: bool):
    if as_json:
        _dump(payload, True)
    else:
        _print_distribution(payload)


def _emit_daily(payload, as_json: bool):
    if as_json:
        _dump(payload, True)
    else:
        _print_daily(payload)


def _emit_cash_flow_reconcile(payload, as_json: bool):
    if as_json:
        _dump(payload, True)
    else:
        _print_cash_flow_reconcile(payload)


def _print_daily_job(payload):
    if not isinstance(payload, dict):
        print(payload)
        return
    if payload.get("success") is False:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    print(f"Daily NAV job [{payload.get('date')}]")
    print(f"  status: {payload.get('status')}")
    print(f"  mode: {'dry-run' if payload.get('dry_run') else 'write'}")
    summary = payload.get("summary") or {}
    if summary:
        print(f"  summary: {json.dumps(summary, ensure_ascii=False, default=str)}")
    for item in payload.get("items") or []:
        account = item.get("account") or "default"
        status = item.get("status") or ("ok" if item.get("success") else "failed")
        nav_result = item.get("nav_result") if isinstance(item.get("nav_result"), dict) else item
        nav = nav_result.get("nav") if isinstance(nav_result, dict) else None
        suffix = f", nav={nav}" if nav is not None else ""
        print(f"  - {account}: {status}{suffix}")


def _service_or_fallback(args, service_call, fallback_call):
    if not bool(getattr(args, "no_service", False)):
        from src.service.client import PortfolioServiceClient, PortfolioServiceUnavailable

        try:
            client = PortfolioServiceClient(
                base_url=getattr(args, "service_url", None),
                timeout=float(getattr(args, "service_timeout", 0.5)),
            )
            return _call_backend(args, lambda: service_call(client))
        except PortfolioServiceUnavailable:
            if bool(getattr(args, "require_service", False)):
                raise SystemExit("local service is unavailable and --require-service was set")
            pass
    return _call_backend(args, fallback_call)


def _default_account(account):
    if account:
        return account
    from src import config

    return config.get_account()


def _daily_parts_from_bundle(bundle):
    if not isinstance(bundle, dict):
        nav = {"success": False, "error": "invalid daily bundle response"}
        distribution = {"success": False, "error": "daily bundle did not return a distribution"}
        return nav, distribution

    if bundle.get("success") is False:
        distribution = {"success": False, "error": "skipped because daily bundle failed"}
        return bundle, distribution

    nav = bundle.get("nav_result") or bundle.get("nav")
    if not isinstance(nav, dict):
        nav = {"success": False, "error": "daily bundle missing nav_result"}

    distribution = bundle.get("distribution")
    if not isinstance(distribution, dict):
        report_distribution = (bundle.get("report") or {}).get("distribution")
        if isinstance(report_distribution, dict):
            distribution = report_distribution
        elif isinstance(report_distribution, list):
            distribution = {
                "success": True,
                "total_value": nav.get("total_value"),
                "by_type": report_distribution,
            }
        else:
            distribution = {"success": False, "error": "daily bundle missing distribution"}

    return nav, distribution


def cmd_holdings(args):
    def via_service(client):
        return client.get_holdings(
            account=_default_account(args.account),
            include_price=bool(args.include_price),
        )

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().get_holdings(include_price=bool(args.include_price), account=args.account)

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_cash(args):
    def via_service(client):
        return client.get_cash(account=_default_account(args.account))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().get_cash(account=args.account)

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_futu_sync(args):
    if not bool(args.dry_run) and not bool(args.confirm):
        raise SystemExit("Futu holdings write requires --confirm. Re-run without --write for dry-run.")
    if bool(args.allow_empty_stock_snapshot) and not bool(args.confirm):
        raise SystemExit("--allow-empty-stock-snapshot requires --confirm.")

    kwargs = {
        "account": _default_account(getattr(args, "account", None)),
        "dry_run": bool(args.dry_run),
        "confirm": bool(args.confirm),
        "allow_empty_stock_snapshot": bool(args.allow_empty_stock_snapshot),
    }

    def via_service(client):
        return client.sync_futu_holdings(**kwargs)

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().sync_futu_holdings(**kwargs)

    result = _service_or_fallback(args, via_service, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_reconcile(args):
    if bool(args.apply) and not bool(args.confirm):
        raise SystemExit("cash-flow reconcile --apply requires --confirm. Re-run without --apply for dry-run.")

    def direct():
        from src.feishu_storage import FeishuStorage

        storage = FeishuStorage()
        return storage.reconcile_cash_flows(
            account=getattr(args, "account", None),
            dry_run=not bool(args.apply),
        )

    res = _call_backend(args, direct)
    _emit_cash_flow_reconcile(res, args.json)
    return res


def cmd_accounts(args):
    def via_service(client):
        return client.list_accounts(include_default=not bool(args.exclude_default))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().list_accounts(include_default=not bool(args.exclude_default))

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_overview(args):
    def via_service(client):
        return client.multi_account_overview(
            accounts=args.accounts,
            price_timeout=args.timeout,
            include_details=bool(args.details),
        )

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().multi_account_overview(
            accounts=args.accounts,
            price_timeout=args.timeout,
            include_details=bool(args.details),
        )

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_nav(args):
    def via_service(client):
        return client.get_nav(account=_default_account(args.account), days=int(getattr(args, "days", 30)))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().get_nav(account=args.account, days=int(getattr(args, "days", 30)))

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_nav_record(args):
    if not bool(args.dry_run) and not bool(args.confirm):
        raise SystemExit("nav record write requires --confirm. Re-run without --write for dry-run.")

    def via_service(client):
        kwargs = {
            "account": _default_account(args.account),
            "price_timeout": args.timeout,
            "dry_run": bool(args.dry_run),
            "confirm": bool(args.confirm),
            "overwrite_existing": not bool(args.no_overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
        }
        if getattr(args, "nav_date", None):
            kwargs["nav_date"] = args.nav_date
        if getattr(args, "run_id", None):
            kwargs["run_id"] = args.run_id
        return client.record_nav(**kwargs)

    def direct():
        from src.service.application import PortfolioService

        kwargs = {
            "account": args.account,
            "price_timeout": args.timeout,
            "dry_run": bool(args.dry_run),
            "confirm": bool(args.confirm),
            "overwrite_existing": not bool(args.no_overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
        }
        if getattr(args, "nav_date", None):
            kwargs["nav_date"] = args.nav_date
        if getattr(args, "run_id", None):
            kwargs["run_id"] = args.run_id
        return PortfolioService().record_nav(**kwargs)

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_positions_distribution(args):
    accounts = getattr(args, "accounts", None)
    by_asset = bool(getattr(args, "by_asset", False))
    include_value = not bool(getattr(args, "no_value", False))
    group_cash = bool(getattr(args, "group_cash", False))

    def via_service(client):
        kwargs = {
            "by_asset": by_asset,
            "include_value": include_value,
        }
        if group_cash:
            kwargs["group_cash"] = True
        if accounts is not None:
            kwargs["accounts"] = accounts
        else:
            kwargs["account"] = _default_account(args.account)
        return client.get_distribution(**kwargs)

    def direct():
        from src.service.application import PortfolioService

        kwargs = {
            "by_asset": by_asset,
            "include_value": include_value,
        }
        if group_cash:
            kwargs["group_cash"] = True
        if accounts is not None:
            kwargs["accounts"] = accounts
        else:
            kwargs["account"] = args.account
        return PortfolioService().get_distribution(**kwargs)

    res = _service_or_fallback(args, via_service, direct)
    _emit_distribution(res, args.json)
    return res


def cmd_daily(args):
    if not bool(args.dry_run) and not bool(args.confirm):
        raise SystemExit("daily write requires --confirm. Re-run without --write for dry-run.")

    from src import config

    account = args.account or config.get_account()

    def via_service(client):
        bundle_kwargs = {
            "account": account,
            "price_timeout": args.timeout,
            "dry_run": bool(args.dry_run),
            "confirm": bool(args.confirm),
            "overwrite_existing": not bool(args.no_overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
        }
        if getattr(args, "nav_date", None):
            bundle_kwargs["nav_date"] = args.nav_date
        if getattr(args, "run_id", None):
            bundle_kwargs["run_id"] = args.run_id
        return _daily_parts_from_bundle(client.daily_report_bundle(**bundle_kwargs))

    def direct():
        from src.service.application import PortfolioService

        bundle_kwargs = {
            "account": account,
            "price_timeout": args.timeout,
            "dry_run": bool(args.dry_run),
            "confirm": bool(args.confirm),
            "overwrite_existing": not bool(args.no_overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
        }
        if getattr(args, "nav_date", None):
            bundle_kwargs["nav_date"] = args.nav_date
        if getattr(args, "run_id", None):
            bundle_kwargs["run_id"] = args.run_id
        return _daily_parts_from_bundle(PortfolioService().daily_report_bundle(**bundle_kwargs))

    nav_result, distribution_result = _service_or_fallback(args, via_service, direct)
    success = bool(nav_result.get("success")) and bool(distribution_result.get("success"))
    payload = {
        "success": success,
        "command": "daily",
        "account": account,
        "dry_run": bool(args.dry_run),
        "nav": nav_result,
        "distribution": distribution_result,
    }
    payload_run_id = nav_result.get("run_id") or getattr(args, "run_id", None)
    if payload_run_id:
        payload["run_id"] = payload_run_id
    if not success:
        payload["status"] = "failed"
    _emit_daily(payload, args.json)
    return payload


def cmd_daily_job(args):
    if not bool(args.dry_run) and not bool(args.confirm):
        raise SystemExit("daily-job write requires --confirm. Re-run without --write for dry-run.")

    def job_kwargs():
        kwargs = {
            "account": getattr(args, "account", None),
            "accounts": getattr(args, "accounts", None),
            "nav_date": getattr(args, "nav_date", None),
            "run_date": getattr(args, "run_date", None),
            "price_timeout": args.timeout,
            "dry_run": bool(args.dry_run),
            "confirm": bool(args.confirm),
            "overwrite_existing": bool(args.overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
            "sync_futu_cash_mmf": bool(args.sync_futu_cash_mmf),
            "force_non_business_day": bool(args.force_non_business_day),
            "run_id": getattr(args, "run_id", None),
        }
        if getattr(args, "sync_futu_dry_run", None) is not None:
            kwargs["sync_futu_dry_run"] = bool(args.sync_futu_dry_run)
        return {key: value for key, value in kwargs.items() if value is not None}

    def via_service(client):
        return client.daily_nav_job(**job_kwargs())

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().daily_nav_job(**job_kwargs())

    res = _service_or_fallback(args, via_service, direct)
    if args.json:
        _dump(res, True)
    else:
        _print_daily_job(res)
    return res


def cmd_nav_duplicates(args):
    def via_service(client):
        return client.audit_nav_history_duplicates(account=getattr(args, "account", None))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().audit_nav_history_duplicates(account=getattr(args, "account", None))

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_config_inspect(args):
    from src import config

    keys = None
    if getattr(args, "keys", None):
        keys = [item.strip() for item in args.keys.split(",") if item.strip()]
    res = config.inspect_config(keys=keys, redact=not bool(args.show_secrets))
    _dump(res, args.json)
    return res


def cmd_config_doctor(args):
    from src import config

    res = config.validate_deploy_config(require_futu=bool(args.require_futu))
    _dump(res, args.json)
    return res


def cmd_report(args):
    if not bool(args.preview):
        raise SystemExit(
            "pm report is preview-only. Re-run with --preview, or use "
            "scripts/publish_daily_report.py for the official daily report."
        )

    def via_service(client):
        return client.generate_report(
            account=_default_account(args.account),
            report_type=args.type,
            price_timeout=args.timeout,
        )

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().generate_report(
            account=args.account,
            report_type=args.type,
            price_timeout=args.timeout,
        )

    res = _service_or_fallback(args, via_service, direct)
    if isinstance(res, dict):
        res.setdefault("preview_only", True)
        res.setdefault("canonical_entrypoint", "scripts/publish_daily_report.py")
    _dump(res, args.json)
    return res


def cmd_init_nav(args):
    if not bool(args.confirm) and not bool(args.dry_run):
        raise SystemExit("init-nav write requires --confirm. Re-run with --dry-run or add --confirm.")

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().init_nav_history(
            date_str=args.date,
            price_timeout=args.timeout,
            dry_run=bool(args.dry_run),
            confirm=bool(args.confirm),
            use_bulk_persist=bool(args.use_bulk_persist),
            account=args.account,
        )

    res = _call_backend(args, direct)
    _dump(res, args.json)
    return res


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pm", description="portfolio-management CLI")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--account", default=None, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p.add_argument("--service-url", default=None, help="local service URL; defaults to config/PORTFOLIO_SERVICE_URL")
    p.add_argument("--service-timeout", type=float, default=0.5, help="local service timeout seconds before fallback")
    p.add_argument("--no-service", action="store_true", help="bypass local service and call the direct local fallback")
    p.add_argument("--require-service", action="store_true", help="fail instead of falling back when local service is unavailable")
    p.add_argument("--debug-internal", action="store_true", help="Do not suppress internal stdout prints (debug only).")

    sp = p.add_subparsers(dest="cmd", required=True)

    # Allow putting global flags after the subcommand (e.g. `pm cash --json`).
    # argparse doesn't support this natively; we implement it by also adding --json
    # to each subparser.
    def add_service_args(subparser):
        subparser.add_argument("--service-url", default=argparse.SUPPRESS, help="local service URL")
        subparser.add_argument("--service-timeout", type=float, default=argparse.SUPPRESS, help="local service timeout seconds before fallback")
        subparser.add_argument("--no-service", action="store_true", default=argparse.SUPPRESS, help="bypass local service and call the direct local fallback")
        subparser.add_argument("--require-service", action="store_true", default=argparse.SUPPRESS, help="fail instead of falling back when local service is unavailable")

    def add_nav_write_args(subparser):
        subparser.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
        subparser.add_argument("--nav-date", default=None, help="NAV date (YYYY-MM-DD); defaults to Beijing today")
        subparser.add_argument("--dry-run", action="store_true", default=True, help="preview only (default)")
        subparser.add_argument("--write", dest="dry_run", action="store_false", help="actually write nav_history")
        subparser.add_argument("--confirm", action="store_true", help="required with --write")
        subparser.add_argument("--no-overwrite", action="store_true", help="refuse to overwrite an existing row for the same date")
        subparser.add_argument("--use-bulk-persist", action="store_true", help="use nav_history bulk upsert path")
        subparser.add_argument("--run-id", default=None, help="operator-supplied run id for tracing")
        subparser.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
        subparser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")

    def add_daily_job_args(subparser):
        subparser.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
        subparser.add_argument("--nav-date", default=None, help="NAV date (YYYY-MM-DD), or auto for previous business day before run date")
        subparser.add_argument("--run-date", default=None, help="Job run date used when --nav-date is omitted/auto")
        subparser.add_argument("--accounts", default=None, help="comma-separated accounts; defaults to current non-zero holdings accounts")
        subparser.add_argument("--account", default=argparse.SUPPRESS, help="single account to operate on")
        subparser.add_argument("--dry-run", action="store_true", default=True, help="preview only (default)")
        subparser.add_argument("--write", dest="dry_run", action="store_false", help="actually write nav_history")
        subparser.add_argument("--confirm", action="store_true", help="required with --write")
        subparser.add_argument("--overwrite", action="store_true", help="overwrite an existing NAV row for the same date")
        subparser.add_argument("--use-bulk-persist", action="store_true", help="use nav_history bulk upsert path")
        subparser.add_argument("--sync-futu-cash-mmf", action="store_true", help="sync Futu cash/MMF holdings before each account snapshot")
        subparser.add_argument("--sync-futu-dry-run", dest="sync_futu_dry_run", action="store_true", default=None, help="preview Futu cash/MMF sync without writing holdings")
        subparser.add_argument("--sync-futu-write", dest="sync_futu_dry_run", action="store_false", help="write Futu cash/MMF sync results when the job is also writing NAV")
        subparser.add_argument("--force-non-business-day", action="store_true", help="run even when calendar marks the NAV date non-business")
        subparser.add_argument("--run-id", default=None, help="operator-supplied run id for tracing")
        subparser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")

    p_daily = sp.add_parser("daily", help="calculate daily NAV and position distribution; dry-run by default")
    add_nav_write_args(p_daily)
    add_service_args(p_daily)
    p_daily.set_defaults(func=cmd_daily)

    p_daily_job = sp.add_parser("daily-job", help="run the unified single/multi-account daily NAV job")
    add_daily_job_args(p_daily_job)
    add_service_args(p_daily_job)
    p_daily_job.set_defaults(func=cmd_daily_job)

    p_config = sp.add_parser("config", help="inspect and validate deployment config")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)
    p_config_inspect = config_sub.add_parser("inspect", help="show effective config values and sources")
    p_config_inspect.add_argument("--keys", default=None, help="comma-separated config keys to inspect")
    p_config_inspect.add_argument("--show-secrets", action="store_true", help="show unredacted secret values")
    p_config_inspect.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_config_inspect.set_defaults(func=cmd_config_inspect)
    p_config_doctor = config_sub.add_parser("doctor", help="validate config needed by scheduled daily NAV jobs")
    p_config_doctor.add_argument("--require-futu", action="store_true", help="require Futu OpenD settings and SDK importability")
    p_config_doctor.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_config_doctor.set_defaults(func=cmd_config_doctor)

    p_hold = sp.add_parser("holdings", help="list holdings")
    p_hold.add_argument("--include-price", action="store_true", help="include price fields (may be slow)")
    p_hold.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_hold.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_hold)
    p_hold.set_defaults(func=cmd_holdings)

    p_cash = sp.add_parser("cash", help="show cash positions")
    p_cash.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_cash.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_cash)
    p_cash.set_defaults(func=cmd_cash)

    p_futu = sp.add_parser("futu", help="Futu holdings synchronization")
    futu_sub = p_futu.add_subparsers(dest="futu_cmd", required=True)
    p_futu_sync = futu_sub.add_parser("sync", help="sync Futu cash/MMF and stock/ETF quantity + average cost")
    p_futu_sync.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_futu_sync.add_argument("--dry-run", action="store_true", default=True, help="preview only (default)")
    p_futu_sync.add_argument("--write", dest="dry_run", action="store_false", help="write holdings changes")
    p_futu_sync.add_argument("--confirm", action="store_true", help="required with --write and empty-snapshot override")
    p_futu_sync.add_argument("--allow-empty-stock-snapshot", action="store_true", help="allow an empty eligible stock snapshot to zero existing Futu stocks")
    p_futu_sync.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_futu_sync)
    p_futu_sync.set_defaults(func=cmd_futu_sync)

    p_cash_flow = sp.add_parser("cash-flow", help="cash-flow ledger maintenance")
    cash_flow_sub = p_cash_flow.add_subparsers(dest="cash_flow_cmd", required=True)
    p_cash_flow_reconcile = cash_flow_sub.add_parser(
        "reconcile",
        help="fill generated fields for manually entered cash_flow rows",
    )
    p_cash_flow_reconcile.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to all accounts")
    p_cash_flow_reconcile.add_argument("--apply", action="store_true", help="write derived fields back to Feishu")
    p_cash_flow_reconcile.add_argument("--confirm", action="store_true", help="required with --apply")
    p_cash_flow_reconcile.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_cash_flow_reconcile.set_defaults(func=cmd_cash_flow_reconcile)

    p_accounts = sp.add_parser("accounts", help="list discovered accounts")
    p_accounts.add_argument("--exclude-default", action="store_true", help="do not include the configured default account when it has no data")
    p_accounts.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_accounts)
    p_accounts.set_defaults(func=cmd_accounts)

    p_overview = sp.add_parser("overview", help="show read-only multi-account overview")
    p_overview.add_argument("--accounts", default=None, help="comma-separated accounts; defaults to discovered accounts")
    p_overview.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
    p_overview.add_argument("--details", action="store_true", help="include each account's full report payload")
    p_overview.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_overview)
    p_overview.set_defaults(func=cmd_overview)

    p_nav = sp.add_parser("nav", help="show latest nav or record today's nav")
    p_nav.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_nav.add_argument("--days", type=int, default=30, help="history days to read (default 30)")
    p_nav.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_nav)
    p_nav.set_defaults(func=cmd_nav)
    nav_sub = p_nav.add_subparsers(dest="nav_cmd")
    p_nav_record = nav_sub.add_parser("record", help="calculate and record today's NAV; dry-run by default")
    add_nav_write_args(p_nav_record)
    add_service_args(p_nav_record)
    p_nav_record.set_defaults(func=cmd_nav_record)
    p_nav_duplicates = nav_sub.add_parser("duplicates", help="audit duplicate nav_history account/date rows")
    p_nav_duplicates.add_argument("--account", default=argparse.SUPPRESS, help="account to audit; defaults to all accounts")
    p_nav_duplicates.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_nav_duplicates)
    p_nav_duplicates.set_defaults(func=cmd_nav_duplicates)

    p_positions = sp.add_parser("positions", help="position analytics")
    positions_sub = p_positions.add_subparsers(dest="positions_cmd", required=True)
    p_positions_distribution = positions_sub.add_parser("distribution", help="show position distribution by type, broker, currency, or asset")
    p_positions_distribution.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_positions_distribution.add_argument("--accounts", default=None, help="comma-separated accounts to merge; overrides --account")
    p_positions_distribution.add_argument("--by-asset", action="store_true", help="group distribution by asset code across accounts")
    p_positions_distribution.add_argument("--group-cash", action="store_true", help="collapse cash and MMF into one row")
    p_positions_distribution.add_argument("--no-value", action="store_true", help="hide market value fields; show quantities only")
    p_positions_distribution.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_positions_distribution)
    p_positions_distribution.set_defaults(func=cmd_positions_distribution)

    p_distribution = sp.add_parser("distribution", help="shortcut for positions distribution")
    p_distribution.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_distribution.add_argument("--accounts", default=None, help="comma-separated accounts to merge; overrides --account")
    p_distribution.add_argument("--by-asset", action="store_true", help="group distribution by asset code across accounts")
    p_distribution.add_argument("--group-cash", action="store_true", help="collapse cash and MMF into one row")
    p_distribution.add_argument("--no-value", action="store_true", help="hide market value fields; show quantities only")
    p_distribution.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_distribution)
    p_distribution.set_defaults(func=cmd_positions_distribution)

    p_rep = sp.add_parser("report", help="preview report data (read-only; not the official daily entry)")
    p_rep.add_argument("type", choices=["daily", "monthly", "yearly"], help="report type")
    p_rep.add_argument("--preview", action="store_true", help="acknowledge this command is preview-only")
    p_rep.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
    p_rep.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_rep.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_rep)
    p_rep.set_defaults(func=cmd_report)

    p_init_nav = sp.add_parser("init-nav", help="initialize first nav_history row for a new account")
    p_init_nav.add_argument("--date", default=None, help="nav date (YYYY-MM-DD); defaults to today")
    p_init_nav.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
    p_init_nav.add_argument("--dry-run", action="store_true", default=True, help="preview only (default)")
    p_init_nav.add_argument("--write", dest="dry_run", action="store_false", help="actually write nav_history")
    p_init_nav.add_argument("--confirm", action="store_true", help="required with --write")
    p_init_nav.add_argument("--use-bulk-persist", action="store_true", help="use nav_history bulk upsert path")
    p_init_nav.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_init_nav.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_init_nav.set_defaults(func=cmd_init_nav)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _exit_code(args.func(args))
    except Exception as exc:
        from src.service.client import PortfolioServiceError

        if isinstance(exc, PortfolioServiceError):
            if bool(getattr(args, "json", False)):
                _dump({"success": False, "error": str(exc)}, True)
                return 1
            raise SystemExit(str(exc)) from exc
        raise
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
