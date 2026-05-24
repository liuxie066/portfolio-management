#!/usr/bin/env python3
"""portfolio-management CLI for service-first workflows.

Design goals:
- Provide a few common read-only commands.
- Prefer the local HTTP service, with direct skill_api fallback.
- Fast defaults (no writes; avoid slow realtime price fetch unless asked).
- Human-readable by default; `--json` for automation.

Usage examples:
  . .venv/bin/activate
  ./pm daily --json
  ./pm daily --write --confirm
  python scripts/pm.py cash
  python scripts/pm.py cash --account alice
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

# Ensure repo root is on sys.path so `import skill_api` works.
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
        from skill_api import get_holdings

        return get_holdings(include_price=bool(args.include_price), account=args.account)

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_cash(args):
    def via_service(client):
        return client.get_cash(account=_default_account(args.account))

    def direct():
        from skill_api import get_cash

        return get_cash(account=args.account)

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


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
        from skill_api import list_accounts

        return list_accounts(include_default=not bool(args.exclude_default))

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
        from skill_api import multi_account_overview

        return multi_account_overview(
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
        from skill_api import get_nav

        return get_nav(account=args.account, days=int(getattr(args, "days", 30)))

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
        if getattr(args, "run_id", None):
            kwargs["run_id"] = args.run_id
        return client.record_nav(**kwargs)

    def direct():
        from skill_api import record_nav

        kwargs = {
            "price_timeout": args.timeout,
            "dry_run": bool(args.dry_run),
            "confirm": bool(args.confirm),
            "overwrite_existing": not bool(args.no_overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
            "account": args.account,
        }
        if getattr(args, "run_id", None):
            kwargs["run_id"] = args.run_id
        return record_nav(**kwargs)

    res = _service_or_fallback(args, via_service, direct)
    _dump(res, args.json)
    return res


def cmd_positions_distribution(args):
    def via_service(client):
        return client.get_distribution(account=_default_account(args.account))

    def direct():
        from skill_api import get_distribution

        return get_distribution(account=args.account)

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
        if getattr(args, "run_id", None):
            bundle_kwargs["run_id"] = args.run_id
        return _daily_parts_from_bundle(client.daily_report_bundle(**bundle_kwargs))

    def direct():
        from skill_api import get_skill

        skill = get_skill(args.account)
        snapshot = skill.build_snapshot(price_timeout_seconds=args.timeout)
        if getattr(args, "run_id", None):
            snapshot["run_id"] = args.run_id
        nav = skill.record_nav(
            price_timeout=args.timeout,
            dry_run=bool(args.dry_run),
            confirm=bool(args.confirm),
            overwrite_existing=not bool(args.no_overwrite),
            use_bulk_persist=bool(args.use_bulk_persist),
            snapshot=snapshot,
            run_id=getattr(args, "run_id", None),
        )
        if not nav.get("success"):
            distribution = {"success": False, "error": "skipped because NAV failed"}
            return nav, distribution
        distribution = skill.get_distribution(holdings_data=snapshot)
        return nav, distribution

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
        from skill_api import generate_report

        return generate_report(
            report_type=args.type,
            record_nav=False,
            price_timeout=args.timeout,
            account=args.account,
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
        from skill_api import init_nav_history

        return init_nav_history(
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
    p.add_argument("--no-service", action="store_true", help="bypass local service and call skill_api directly")
    p.add_argument("--require-service", action="store_true", help="fail instead of falling back when local service is unavailable")
    p.add_argument("--debug-internal", action="store_true", help="Do not suppress internal stdout prints (debug only).")

    sp = p.add_subparsers(dest="cmd", required=True)

    # Allow putting global flags after the subcommand (e.g. `pm cash --json`).
    # argparse doesn't support this natively; we implement it by also adding --json
    # to each subparser.
    def add_service_args(subparser):
        subparser.add_argument("--service-url", default=argparse.SUPPRESS, help="local service URL")
        subparser.add_argument("--service-timeout", type=float, default=argparse.SUPPRESS, help="local service timeout seconds before fallback")
        subparser.add_argument("--no-service", action="store_true", default=argparse.SUPPRESS, help="bypass local service and call skill_api directly")
        subparser.add_argument("--require-service", action="store_true", default=argparse.SUPPRESS, help="fail instead of falling back when local service is unavailable")

    def add_nav_write_args(subparser):
        subparser.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
        subparser.add_argument("--dry-run", action="store_true", default=True, help="preview only (default)")
        subparser.add_argument("--write", dest="dry_run", action="store_false", help="actually write nav_history")
        subparser.add_argument("--confirm", action="store_true", help="required with --write")
        subparser.add_argument("--no-overwrite", action="store_true", help="refuse to overwrite an existing row for the same date")
        subparser.add_argument("--use-bulk-persist", action="store_true", help="use nav_history bulk upsert path")
        subparser.add_argument("--run-id", default=None, help="operator-supplied run id for tracing")
        subparser.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
        subparser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")

    p_daily = sp.add_parser("daily", help="calculate daily NAV and position distribution; dry-run by default")
    add_nav_write_args(p_daily)
    add_service_args(p_daily)
    p_daily.set_defaults(func=cmd_daily)

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

    p_positions = sp.add_parser("positions", help="position analytics")
    positions_sub = p_positions.add_subparsers(dest="positions_cmd", required=True)
    p_positions_distribution = positions_sub.add_parser("distribution", help="show position distribution by type, broker, and currency")
    p_positions_distribution.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_positions_distribution.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_positions_distribution)
    p_positions_distribution.set_defaults(func=cmd_positions_distribution)

    p_distribution = sp.add_parser("distribution", help="shortcut for positions distribution")
    p_distribution.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
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
