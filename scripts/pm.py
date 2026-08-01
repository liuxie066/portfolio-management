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
import threading
from datetime import date
from pathlib import Path
from uuid import uuid4

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


def _service_or_fallback(args, service_call, fallback_call, *, allow_fallback):
    if not bool(getattr(args, "no_service", False)):
        from src.service.client import (
            PortfolioServiceClient,
            PortfolioServiceOutcomeUnknown,
            PortfolioServiceUnavailable,
        )

        try:
            client = PortfolioServiceClient(
                base_url=getattr(args, "service_url", None),
                timeout=float(getattr(args, "service_timeout", 0.5)),
            )
            return _call_backend(args, lambda: service_call(client))
        except PortfolioServiceUnavailable as exc:
            if not allow_fallback:
                raise PortfolioServiceOutcomeUnknown(
                    "local service write outcome is unknown: the request may already have executed; "
                    "direct fallback was not attempted. Do not blindly retry; inspect state first, "
                    "or use --no-service only when intentionally bypassing the service."
                ) from exc
            if bool(getattr(args, "require_service", False)):
                raise SystemExit("local service is unavailable and --require-service was set")
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

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
    _dump(res, args.json)
    return res


def cmd_holdings_reconcile(args):
    """Fresh holdings validation with separately confirmed workflow actions."""

    from src.app.holdings_reconciliation_service import HoldingsReconciliationService
    from src.app.holdings_workflow_service import HoldingsWorkflowService, operator_context
    from src.service.application import PortfolioService

    notify = bool(getattr(args, "notify", False))
    apply = bool(getattr(args, "apply", False))
    if (notify or apply) and not bool(getattr(args, "confirm", False)):
        raise SystemExit("holdings workflow mutation requires --confirm")
    record_id = getattr(args, "record_id", None)
    account = getattr(args, "account", None)
    if apply and not record_id:
        raise SystemExit("holdings apply requires exactly one --record-id")
    if apply and account:
        raise SystemExit("holdings apply does not support account or all-record scope")
    application = PortfolioService()
    reconciliation = HoldingsReconciliationService(storage=application.storage)
    workflow = (
        HoldingsWorkflowService(
            storage=application.storage,
            reconciliation=reconciliation,
        )
        if notify or apply
        else None
    )
    result = _call_backend(
        args,
        lambda: (
            workflow.apply_missing(
                record_id=record_id,
                confirmed_operator=operator_context(command_mode="holdings_reconcile_apply"),
            )
            if apply
            else workflow.notify(
                account=account,
                record_id=record_id,
                trigger={"mode": "manual_notify"},
            )
            if notify
            else reconciliation.reconcile(account=account, record_id=record_id)
        ),
    )
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_holdings_cases(args):
    from src.app.holdings_workflow_service import HoldingsWorkflowService
    from src.service.application import PortfolioService

    workflow = HoldingsWorkflowService(storage=PortfolioService().storage)
    case_key = getattr(args, "case_key", None)
    result = (
        workflow.show_case(case_key)
        if case_key
        else workflow.list_cases(
            account=getattr(args, "account", None),
            state=getattr(args, "state", None),
        )
    )
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_holdings_resolve(args):
    if not bool(args.confirm):
        raise SystemExit("holdings resolve requires --confirm")
    from src.app.holdings_workflow_service import HoldingsWorkflowService, operator_context
    from src.service.application import PortfolioService

    result = HoldingsWorkflowService(storage=PortfolioService().storage).resolve(
        case_key=args.case_key,
        decision=args.decision,
        reason=args.reason,
        confirmed_operator=operator_context(command_mode="holdings_resolve"),
    )
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_holdings_recover(args):
    if not bool(args.confirm):
        raise SystemExit("holdings recover requires --confirm")
    from src.app.holdings_workflow_service import HoldingsWorkflowService, operator_context
    from src.service.application import PortfolioService

    result = HoldingsWorkflowService(storage=PortfolioService().storage).recover(
        case_key=args.case_key,
        confirmed_operator=operator_context(command_mode="holdings_recover"),
    )
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_holdings_events_status(args):
    from src import config
    from src.app.holdings_event_service import HoldingsEventTarget
    from src.app.operation_state_store import OperationStateStore
    from src.feishu.holdings_event_adapter import FeishuHoldingsEventAdapter

    target = HoldingsEventTarget.from_config()
    sdk_available = FeishuHoldingsEventAdapter.sdk_available()
    app_secret_configured = bool(config.get("feishu.app_secret"))
    result = {
        "success": bool(sdk_available and app_secret_configured),
        "read_only": True,
        "target": target.as_dict(),
        "credentials": {
            "app_id_configured": True,
            "app_secret_configured": app_secret_configured,
        },
        "sdk_available": sdk_available,
        "local_inbox": OperationStateStore.inspect_holding_event_status(),
        "remote_subscription_verified": False,
        "listener_connection_verified": False,
        "note": "local/config status only; no Feishu request was made",
    }
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_holdings_events_subscribe(args):
    if not bool(args.confirm):
        raise SystemExit("holdings events subscribe requires --confirm")
    from src.feishu.holdings_event_adapter import FeishuHoldingsEventAdapter

    result = FeishuHoldingsEventAdapter().subscribe()
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_holdings_events_listen(args):
    if not bool(args.confirm):
        raise SystemExit("holdings events listen requires --confirm")
    from src.app.holding_event_inbox_service import HoldingEventInboxService
    from src.feishu.holdings_event_adapter import FeishuHoldingsEventAdapter
    from src.service.application import PortfolioService

    inbox = HoldingEventInboxService(storage=PortfolioService().storage)
    adapter = FeishuHoldingsEventAdapter(target=inbox.target)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=inbox.run_worker_loop,
        kwargs={
            "stop_event": stop_event,
            "poll_seconds": float(args.poll_seconds),
            "limit": int(args.limit),
        },
        name="holdings-event-worker",
        daemon=True,
    )
    worker.start()
    try:
        adapter.start(inbox.accept)
    finally:
        stop_event.set()
        worker.join(timeout=10)
    return {"success": True, "status": "stopped"}


def _combined_event_targets():
    from src.app.cash_flow_event_service import CashFlowEventTarget
    from src.app.holdings_event_service import HoldingsEventTarget
    from src.feishu.bitable_event_adapter import validate_bitable_targets

    return validate_bitable_targets(
        (HoldingsEventTarget.from_config(), CashFlowEventTarget.from_config())
    )


def cmd_events_status(args):
    from src import config
    from src.app.cash_flow_event_service import CashFlowEventTarget
    from src.app.holdings_event_service import HoldingsEventTarget
    from src.app.operation_state_store import OperationStateStore
    from src.feishu.bitable_event_adapter import (
        FeishuBitableEventAdapter,
        validate_bitable_targets,
    )

    targets = []
    registry_error = None
    try:
        targets = [
            HoldingsEventTarget.from_config(),
            CashFlowEventTarget.from_config(),
        ]
        validate_bitable_targets(targets)
    except Exception as exc:
        registry_error = str(exc) or exc.__class__.__name__
    sdk_available = FeishuBitableEventAdapter.sdk_available()
    app_id_configured = bool(str(config.get("feishu.app_id") or "").strip())
    app_secret_configured = bool(
        str(config.get("feishu.app_secret") or "").strip()
    )
    registry_valid = registry_error is None
    result = {
        "success": bool(
            registry_valid
            and sdk_available
            and app_id_configured
            and app_secret_configured
        ),
        "read_only": True,
        "target_registry": {
            "valid": registry_valid,
            "error": registry_error,
            "targets": [target.as_dict() for target in targets],
        },
        "credentials": {
            "app_id_configured": app_id_configured,
            "app_secret_configured": app_secret_configured,
        },
        "sdk_available": sdk_available,
        "local_inboxes": {
            "holdings": OperationStateStore.inspect_holding_event_status(),
            "cash_flow": OperationStateStore.inspect_cash_flow_event_status(),
        },
        "remote_subscription_verified": False,
        "listener_connection_verified": False,
        "note": "local/config status only; no Feishu request was made",
    }
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_events_subscribe(args):
    if not bool(args.confirm):
        raise SystemExit("events subscribe requires --confirm")
    from src.feishu.bitable_event_adapter import FeishuBitableEventAdapter

    targets = _combined_event_targets()
    result = FeishuBitableEventAdapter(targets=targets).subscribe()
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_events_listen(args):
    if not bool(args.confirm):
        raise SystemExit("events listen requires --confirm")

    targets = _combined_event_targets()

    from src.app.cash_flow_event_completion_service import (
        CashFlowEventCompletionService,
    )
    from src.app.cash_flow_event_inbox_service import CashFlowEventInboxService
    from src.app.holding_event_inbox_service import HoldingEventInboxService
    from src.app.operation_state_store import OperationStateStore
    from src.feishu.bitable_event_adapter import FeishuBitableEventAdapter
    from src.service.application import PortfolioService

    holdings_target, cash_flow_target = targets
    storage = PortfolioService().storage
    store = OperationStateStore()
    holdings_inbox = HoldingEventInboxService(
        storage=storage,
        store=store,
        target=holdings_target,
    )
    completion = CashFlowEventCompletionService(
        storage=storage,
        operation_store=store,
    )
    cash_flow_inbox = CashFlowEventInboxService(
        store=store,
        record_handler=completion,
        terminal_failure_receipt_factory=completion.terminal_failure_receipts,
        target=cash_flow_target,
    )
    adapter = FeishuBitableEventAdapter(targets=targets)

    def accept(payload):
        outcomes = {
            "holdings": holdings_inbox.accept(payload),
            "cash_flow": cash_flow_inbox.accept(payload),
        }
        accepted_by = [
            name
            for name, outcome in outcomes.items()
            if bool(outcome.get("accepted"))
        ]
        if len(accepted_by) > 1:
            raise RuntimeError(
                "bitable event was accepted by multiple table handlers"
            )
        return {
            "success": all(outcome.get("success") for outcome in outcomes.values()),
            "accepted_by": accepted_by,
            "outcomes": outcomes,
        }

    stop_event = threading.Event()
    workers = [
        threading.Thread(
            target=holdings_inbox.run_worker_loop,
            kwargs={
                "stop_event": stop_event,
                "poll_seconds": float(args.poll_seconds),
                "limit": int(args.limit),
            },
            name="holdings-event-worker",
            daemon=True,
        ),
        threading.Thread(
            target=cash_flow_inbox.run_worker_loop,
            kwargs={
                "stop_event": stop_event,
                "poll_seconds": float(args.poll_seconds),
                "limit": int(args.limit),
            },
            name="cash-flow-event-worker",
            daemon=True,
        ),
    ]
    started_workers = []
    try:
        for worker in workers:
            worker.start()
            started_workers.append(worker)
        adapter.start(accept)
    finally:
        stop_event.set()
        for worker in started_workers:
            worker.join(timeout=10)
    result = {"success": True, "status": "stopped"}
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_cash(args):
    def via_service(client):
        return client.get_cash(account=_default_account(args.account))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().get_cash(account=args.account)

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
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

    result = _service_or_fallback(args, via_service, direct, allow_fallback=False)
    _dump(result, args.json)
    return result


def cmd_futu_accounts(args):
    from src.app import FutuOpenApiBalanceProvider

    try:
        result = _call_backend(
            args,
            lambda: FutuOpenApiBalanceProvider(
                trd_market=str(args.market).upper(),
                verify_account=False,
            ).discover_accounts(),
        )
    except Exception:
        result = {
            "success": False,
            "read_only": True,
            "reason_code": "FUTU_ACCOUNT_DISCOVERY_FAILED",
            "error": "Futu account discovery failed",
        }
    _dump(result, args.json)
    return result


def cmd_cash_flow_reconcile(args):
    if bool(args.apply) and not bool(args.confirm):
        raise SystemExit("cash-flow reconcile --apply requires --confirm. Re-run without --apply for dry-run.")
    manual_values = (
        getattr(args, "exchange_rate", None),
        getattr(args, "rate_date", None),
        getattr(args, "rate_source", None),
    )
    manual_evidence = any(value not in (None, "") for value in manual_values)
    if manual_evidence:
        from src.domain.cash_flow_contracts import normalize_cash_flow_rate_source

        if not getattr(args, "record_id", None):
            raise SystemExit("manual FX evidence requires --record-id")
        if any(value in (None, "") for value in manual_values):
            raise SystemExit(
                "manual FX evidence requires --exchange-rate, --rate-date, and --rate-source"
            )
        try:
            rate_date = date.fromisoformat(str(args.rate_date))
        except ValueError as exc:
            raise SystemExit("--rate-date must be YYYY-MM-DD") from exc
        try:
            normalize_cash_flow_rate_source(str(args.rate_source))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        rate_date = None

    def direct():
        from src.feishu_storage import FeishuStorage
        from src.app.cash_flow_effect_service import CashFlowEffectService
        from src.app.operation_state_store import OperationStateStore

        operation_store = None
        if manual_evidence and bool(args.apply):
            operation_store = OperationStateStore()
        storage = FeishuStorage()
        result = storage.reconcile_cash_flows(
            account=getattr(args, "account", None),
            dry_run=not bool(args.apply),
            record_id=getattr(args, "record_id", None),
            manual_exchange_rate=getattr(args, "exchange_rate", None),
            rate_date=rate_date,
            rate_source=getattr(args, "rate_source", None),
        )
        if manual_evidence and bool(args.apply):
            if result.get("success") is False:
                return result
            rows = [
                row
                for row in result.get("rows") or []
                if row.get("record_id") == args.record_id
                and row.get("status") != "error"
            ]
            if len(rows) != 1:
                raise RuntimeError(
                    "manual FX confirmation requires exactly one valid cash_flow row"
                )
            row = rows[0]
            evidence = dict(row.get("fx_evidence") or {})
            if evidence.get("exchange_rate_evidence_type") != "manual_supplement":
                raise RuntimeError("manual FX confirmation lacks local evidence payload")
            generated_fingerprint = str(
                row.get("generated_fingerprint") or ""
            ).strip()
            if (
                row.get("completion_state") != "completed"
                or not bool(row.get("readback_verified"))
                or not generated_fingerprint
            ):
                raise RuntimeError(
                    "manual FX confirmation refused: Feishu readback does not "
                    "match the applied reconciliation"
                )
            confirmation_id = operation_store.record_fx_confirmation(
                confirmation_id=uuid4().hex,
                record_id=str(row["record_id"]),
                source_hash=generated_fingerprint,
                exchange_rate=str(row["exchange_rate"]),
                exchange_rate_date=str(evidence["exchange_rate_date"]),
                exchange_rate_source=str(evidence["exchange_rate_source"]),
                exchange_rate_evidence_type=str(evidence["exchange_rate_evidence_type"]),
                cny_amount=str(row["cny_amount"]),
                confirmation=CashFlowEffectService._operator_context(),
            )
            result = dict(result)
            result["fx_confirmation_id"] = confirmation_id
        return result

    res = _call_backend(args, direct)
    _emit_cash_flow_reconcile(res, args.json)
    return res


def cmd_cash_flow_duplicates(args):
    """Fresh read-only expected-key duplicate audit."""

    def direct():
        from src.feishu_storage import FeishuStorage

        return FeishuStorage().cash_flow.audit_cash_flow_duplicates(
            account=getattr(args, "account", None),
        )

    result = _call_backend(args, direct)
    _dump(result, bool(getattr(args, "json", False)))
    return result


def cmd_cash_flow_fx_import_legacy(args):
    if not bool(args.confirm):
        raise SystemExit("cash-flow fx-evidence import-legacy requires --confirm")
    from src.app.cash_flow_effect_store import CashFlowEffectStore
    from src.app.operation_state_store import OperationStateStore

    legacy_path = args.legacy_db or CashFlowEffectStore.resolve_db_path()
    result = {
        "success": True,
        "legacy_db": str(legacy_path),
        **OperationStateStore().import_legacy_fx_confirmations(legacy_path),
    }
    _dump(result, bool(args.json))
    return result


def _cash_flow_effect_components():
    from src import config
    from src.app.cash_flow_effect_service import CashFlowEffectService
    from src.app.cash_flow_effect_store import CashFlowEffectStore
    from src.feishu_storage import FeishuStorage

    store = CashFlowEffectStore()
    store.assert_cutover(config.get("cash_flow.effects.cutover_date"))
    storage = FeishuStorage()
    return storage, store, CashFlowEffectService(storage=storage, store=store)


def cmd_cash_flow_effects_init(args):
    if not bool(args.confirm):
        raise SystemExit("cash-flow effects init requires --confirm")

    def direct():
        from src import config
        from src.app.cash_flow_effect_service import CashFlowEffectService
        from src.app.cash_flow_effect_store import CashFlowEffectStore
        from src.feishu_storage import FeishuStorage

        path = CashFlowEffectStore.resolve_db_path()
        if path.exists():
            raise RuntimeError(
                f"cash-flow effect database already exists; init cannot rebaseline: {path}"
            )
        configured = config.get("cash_flow.effects.cutover_date")
        if not configured:
            raise RuntimeError(
                "set cash_flow.effects.cutover_date in the unified config "
                "before initialization"
            )
        if date.fromisoformat(str(configured)[:10]) != date.fromisoformat(
            args.cutover_date
        ):
            raise RuntimeError(
                "configured cash_flow.effects.cutover_date differs from init argument"
            )
        storage = FeishuStorage()
        flows = storage.get_cash_flows()
        holdings = storage.get_holdings_fresh(include_empty=True)
        store = CashFlowEffectStore.initialize(
            cutover_date=args.cutover_date,
            db_path=path,
        )
        service = CashFlowEffectService(storage=storage, store=store)
        initialized = service.initialize_from_snapshot(
            flows=flows,
            holdings=holdings,
        )
        return {
            "success": True,
            "integrity": store.integrity_check(),
            **initialized,
        }

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_review(args):
    def direct():
        _, _, service = _cash_flow_effect_components()
        return service.review(account=getattr(args, "account", None))

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_scan(args):
    def direct():
        from src.app.cash_flow_effect_receipt_service import (
            CashFlowEffectReceiptService,
        )

        _, store, service = _cash_flow_effect_components()
        result = service.scan(
            account=getattr(args, "account", None),
            enqueue_receipts=True,
        )
        result = dict(result)
        result["receipts"] = CashFlowEffectReceiptService(
            store=store
        ).dispatch_pending()
        return result

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_list(args):
    def direct():
        _, _, service = _cash_flow_effect_components()
        return {
            "success": True,
            "effects": service.list_effects(
                account=getattr(args, "account", None),
                latest_only=not bool(args.all_versions),
            ),
        }

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_show(args):
    def direct():
        _, store, _ = _cash_flow_effect_components()
        effect = store.get_effect(args.effect_id)
        if not effect:
            raise KeyError(f"cash-flow effect not found: {args.effect_id}")
        return {
            "success": True,
            "effect": effect,
            "events": store.list_events(args.effect_id),
        }

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_preview(args):
    def direct():
        _, _, service = _cash_flow_effect_components()
        return service.preview(
            args.effect_id,
            external_action=getattr(args, "external_action", None),
            historical_apply=bool(args.historical_apply),
        )

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def _dispatch_effect_receipts(store):
    from src.app.cash_flow_effect_receipt_service import (
        CashFlowEffectReceiptService,
    )

    return CashFlowEffectReceiptService(store=store).dispatch_pending()


def cmd_cash_flow_effects_confirm(args):
    if not bool(args.confirm):
        raise SystemExit("cash-flow effects confirm requires --confirm")

    def direct():
        _, store, service = _cash_flow_effect_components()
        result = service.confirm(
            args.effect_id,
            preview_hash=args.preview_hash,
            confirm=True,
            external_action=getattr(args, "external_action", None),
            historical_apply=bool(args.historical_apply),
        )
        result = dict(result)
        result["receipts"] = _dispatch_effect_receipts(store)
        return result

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_record_only(args):
    if not bool(args.confirm):
        raise SystemExit("cash-flow effects record-only requires --confirm")

    def direct():
        _, store, service = _cash_flow_effect_components()
        result = service.record_only(args.effect_id, confirm=True)
        result = dict(result)
        result["receipts"] = _dispatch_effect_receipts(store)
        return result

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_retry(args):
    if not bool(args.confirm):
        raise SystemExit("cash-flow effects retry requires --confirm")

    def direct():
        _, store, service = _cash_flow_effect_components()
        result = service.retry(args.effect_id, confirm=True)
        result = dict(result)
        result["receipts"] = _dispatch_effect_receipts(store)
        return result

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_audit(args):
    def direct():
        _, _, service = _cash_flow_effect_components()
        return service.audit(account=args.account)

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_cash_flow_effects_backup(args):
    if not bool(args.confirm):
        raise SystemExit("cash-flow effects backup requires --confirm")

    def direct():
        _, store, _ = _cash_flow_effect_components()
        return store.backup(args.output)

    result = _call_backend(args, direct)
    _dump(result, args.json)
    return result


def cmd_compensation_list(args):
    from src.app.compensation_service import CompensationService

    tasks = CompensationService().list_tasks(include_resolved=bool(args.include_resolved))
    result = {"success": True, "count": len(tasks), "tasks": tasks}
    _dump(result, args.json)
    return result


def cmd_compensation_retry(args):
    if not bool(args.confirm):
        raise SystemExit("compensation retry requires --confirm")
    from src.service.application import PortfolioService

    result = _call_backend(
        args,
        lambda: PortfolioService().portfolio.compensation.retry(args.task_id, confirm=True),
    )
    _dump(result, args.json)
    return result


def cmd_accounts(args):
    def via_service(client):
        return client.list_accounts(include_default=not bool(args.exclude_default))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().list_accounts(include_default=not bool(args.exclude_default))

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
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

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
    _dump(res, args.json)
    return res


def cmd_nav(args):
    def via_service(client):
        return client.get_nav(account=_default_account(args.account), days=int(getattr(args, "days", 30)))

    def direct():
        from src.service.application import PortfolioService

        return PortfolioService().get_nav(account=args.account, days=int(getattr(args, "days", 30)))

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
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
            "overwrite_existing": bool(args.overwrite),
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
            "overwrite_existing": bool(args.overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
        }
        if getattr(args, "nav_date", None):
            kwargs["nav_date"] = args.nav_date
        if getattr(args, "run_id", None):
            kwargs["run_id"] = args.run_id
        return PortfolioService().record_nav(**kwargs)

    res = _service_or_fallback(args, via_service, direct, allow_fallback=False)
    _dump(res, args.json)
    return res


def cmd_positions_distribution(args):
    accounts = getattr(args, "accounts", None)
    group_cash = bool(getattr(args, "group_cash", False))
    by_asset = bool(getattr(args, "by_asset", False) or group_cash)
    include_value = not bool(getattr(args, "no_value", False))

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

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
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
            "overwrite_existing": bool(args.overwrite),
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
            "overwrite_existing": bool(args.overwrite),
            "use_bulk_persist": bool(args.use_bulk_persist),
        }
        if getattr(args, "nav_date", None):
            bundle_kwargs["nav_date"] = args.nav_date
        if getattr(args, "run_id", None):
            bundle_kwargs["run_id"] = args.run_id
        return _daily_parts_from_bundle(PortfolioService().daily_report_bundle(**bundle_kwargs))

    nav_result, distribution_result = _service_or_fallback(
        args, via_service, direct, allow_fallback=False
    )
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

    res = _service_or_fallback(args, via_service, direct, allow_fallback=False)
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

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
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

    res = config.validate_deploy_config(
        require_futu=bool(args.require_futu),
        require_quality=bool(args.require_quality),
    )
    _dump(res, args.json)
    return res


def cmd_quality_status(args):
    def via_service(client):
        return client.quality_status()

    def direct():
        from src.service.application import PortfolioService

        payload = PortfolioService().quality_status()
        if payload is None:
            return {"success": False, "error": "quality status is unavailable"}
        return payload

    result = _service_or_fallback(args, via_service, direct, allow_fallback=True)
    _dump(result, args.json)
    return result


def cmd_quality_refresh(args):
    from src import config
    from src.service.application import PortfolioService

    accounts = (
        [item.strip().lower() for item in args.accounts.split(",") if item.strip()]
        if args.accounts
        else config.get_quality_accounts()
    )
    result = _call_backend(
        args,
        lambda: PortfolioService().refresh_quality_status(accounts=accounts),
    )
    _dump(result, args.json)
    return result


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

    res = _service_or_fallback(args, via_service, direct, allow_fallback=True)
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


def cmd_receipts_dispatch(args):
    if not bool(args.confirm):
        raise SystemExit("receipts dispatch requires --confirm")
    from src.app.nav_receipt_outbox_service import NavReceiptOutboxService
    from src.app.operation_receipt_outbox_service import OperationReceiptOutboxService

    branches = {}
    for name, dispatch in (
        ("nav", lambda: NavReceiptOutboxService().dispatch_pending(limit=int(args.limit))),
        (
            "operations",
            lambda: OperationReceiptOutboxService().dispatch_pending(limit=int(args.limit)),
        ),
    ):
        try:
            branches[name] = dispatch()
        except Exception as exc:
            branches[name] = {
                "success": False,
                "error": str(exc) or exc.__class__.__name__,
            }
    result = {
        "success": all(bool(item.get("success")) for item in branches.values()),
        "branches": branches,
    }
    _dump(result, bool(args.json))
    return result


def cmd_receipts_resolve(args):
    if not bool(args.confirm):
        raise SystemExit("receipts resolve requires --confirm")
    from src.app.holdings_workflow_service import operator_context
    from src.app.operation_receipt_outbox_service import OperationReceiptOutboxService

    result = OperationReceiptOutboxService().resolve_unknown(
        receipt_key=args.receipt_key,
        decision=args.decision,
        operator_context=operator_context(command_mode="receipts_resolve"),
    )
    _dump(result, bool(args.json))
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pm", description="portfolio-management CLI")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--account", default=None, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p.add_argument("--service-url", default=None, help="local service URL; defaults to config/PORTFOLIO_SERVICE_URL")
    p.add_argument("--service-timeout", type=float, default=0.5, help="local service timeout seconds before read fallback or write failure")
    p.add_argument("--no-service", action="store_true", help="bypass local service and call the direct local fallback")
    p.add_argument("--require-service", action="store_true", help="fail instead of falling back when local service is unavailable")
    p.add_argument("--debug-internal", action="store_true", help="Do not suppress internal stdout prints (debug only).")

    sp = p.add_subparsers(dest="cmd", required=True)

    # Allow putting global flags after the subcommand (e.g. `pm cash --json`).
    # argparse doesn't support this natively; we implement it by also adding --json
    # to each subparser.
    def add_service_args(subparser):
        subparser.add_argument("--service-url", default=argparse.SUPPRESS, help="local service URL")
        subparser.add_argument("--service-timeout", type=float, default=argparse.SUPPRESS, help="local service timeout seconds before read fallback or write failure")
        subparser.add_argument("--no-service", action="store_true", default=argparse.SUPPRESS, help="bypass local service and call the direct local fallback")
        subparser.add_argument("--require-service", action="store_true", default=argparse.SUPPRESS, help="fail instead of falling back when local service is unavailable")

    def add_nav_write_args(subparser):
        subparser.add_argument("--timeout", type=int, default=30, help="price timeout seconds (default 30)")
        subparser.add_argument("--nav-date", default=None, help="NAV date (YYYY-MM-DD); defaults to Beijing today")
        subparser.add_argument("--dry-run", action="store_true", default=True, help="preview only (default)")
        subparser.add_argument("--write", dest="dry_run", action="store_false", help="actually write nav_history")
        subparser.add_argument("--confirm", action="store_true", help="required with --write")
        overwrite_group = subparser.add_mutually_exclusive_group()
        overwrite_group.add_argument(
            "--overwrite",
            action="store_true",
            default=False,
            help="overwrite an existing NAV row for the same date",
        )
        overwrite_group.add_argument(
            "--no-overwrite",
            dest="overwrite",
            action="store_false",
            help=argparse.SUPPRESS,
        )
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
        subparser.add_argument("--sync-futu-cash-mmf", action="store_true", help="observe Futu per-currency CASH and sync MMF before each account snapshot")
        subparser.add_argument("--sync-futu-dry-run", dest="sync_futu_dry_run", action="store_true", default=None, help="preview Futu CASH observation/MMF sync without writes")
        subparser.add_argument("--sync-futu-write", dest="sync_futu_dry_run", action="store_false", help="persist MMF sync and CASH reconciliation observations when NAV is also writing")
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
    p_config_doctor.add_argument("--require-quality", action="store_true", help="require quality producer token and account scope")
    p_config_doctor.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_config_doctor.set_defaults(func=cmd_config_doctor)

    p_quality = sp.add_parser("quality", help="read the last published data-quality status")
    quality_sub = p_quality.add_subparsers(dest="quality_cmd", required=True)
    p_quality_status = quality_sub.add_parser("status", help="read the last published quality artifact")
    p_quality_status.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_quality_status)
    p_quality_status.set_defaults(func=cmd_quality_status)
    p_quality_refresh = quality_sub.add_parser(
        "refresh",
        help="rebuild and atomically publish the local quality artifact",
    )
    p_quality_refresh.add_argument("--accounts", default=None, help="comma-separated account scope")
    p_quality_refresh.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_quality_refresh.set_defaults(func=cmd_quality_refresh)

    p_receipts = sp.add_parser("receipts", help="durable notification delivery")
    receipts_sub = p_receipts.add_subparsers(dest="receipts_cmd", required=True)
    p_receipts_dispatch = receipts_sub.add_parser(
        "dispatch",
        help="retry due NAV receipt outbox rows",
    )
    p_receipts_dispatch.add_argument("--limit", type=int, default=100)
    p_receipts_dispatch.add_argument("--confirm", action="store_true")
    p_receipts_dispatch.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="output JSON",
    )
    p_receipts_dispatch.set_defaults(func=cmd_receipts_dispatch)
    p_receipts_resolve = receipts_sub.add_parser(
        "resolve",
        help="explicitly resolve an unknown typed receipt delivery",
    )
    p_receipts_resolve.add_argument("--receipt-key", required=True)
    p_receipts_resolve.add_argument(
        "--decision",
        required=True,
        choices=("retry", "mark-sent"),
    )
    p_receipts_resolve.add_argument("--confirm", action="store_true")
    p_receipts_resolve.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_receipts_resolve.set_defaults(func=cmd_receipts_resolve)

    p_events = sp.add_parser(
        "events",
        help="combined exact-resource Feishu Bitable event ingress",
    )
    events_sub = p_events.add_subparsers(dest="events_cmd", required=True)
    p_events_status = events_sub.add_parser(
        "status",
        help="show combined local/config readiness without a Feishu request",
    )
    p_events_status.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_events_status.set_defaults(func=cmd_events_status)
    p_events_subscribe = events_sub.add_parser(
        "subscribe",
        help="create subscriptions for the configured Base documents",
    )
    p_events_subscribe.add_argument("--confirm", action="store_true")
    p_events_subscribe.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_events_subscribe.set_defaults(func=cmd_events_subscribe)
    p_events_listen = events_sub.add_parser(
        "listen",
        help="run one long connection and both leased local workers",
    )
    p_events_listen.add_argument("--confirm", action="store_true")
    p_events_listen.add_argument("--poll-seconds", type=float, default=1.0)
    p_events_listen.add_argument("--limit", type=int, default=100)
    p_events_listen.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_events_listen.set_defaults(func=cmd_events_listen)

    p_hold = sp.add_parser("holdings", help="list or validate holdings")
    p_hold.add_argument("--include-price", action="store_true", help="include price fields (may be slow)")
    p_hold.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_hold.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_hold)
    p_hold.set_defaults(func=cmd_holdings)
    holdings_sub = p_hold.add_subparsers(dest="holdings_cmd")
    p_hold_reconcile = holdings_sub.add_parser(
        "reconcile",
        help="fresh-read and validate holdings without writes",
    )
    reconcile_scope = p_hold_reconcile.add_mutually_exclusive_group()
    reconcile_scope.add_argument("--account", default=argparse.SUPPRESS)
    reconcile_scope.add_argument("--record-id", default=None)
    reconcile_action = p_hold_reconcile.add_mutually_exclusive_group()
    reconcile_action.add_argument(
        "--notify",
        action="store_true",
        help="persist cases and queue discovery receipts; never write holdings",
    )
    reconcile_action.add_argument(
        "--apply",
        action="store_true",
        help="apply eligible missing fields for exactly one record",
    )
    p_hold_reconcile.add_argument("--confirm", action="store_true")
    p_hold_reconcile.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="output JSON",
    )
    p_hold_reconcile.set_defaults(func=cmd_holdings_reconcile)
    p_hold_cases = holdings_sub.add_parser("cases", help="list or show durable holdings cases")
    p_hold_cases.add_argument("--account", default=argparse.SUPPRESS)
    p_hold_cases.add_argument("--state", default=None)
    p_hold_cases.add_argument("--case-key", default=None)
    p_hold_cases.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_hold_cases.set_defaults(func=cmd_holdings_cases)

    p_hold_resolve = holdings_sub.add_parser("resolve", help="resolve one conflict case")
    p_hold_resolve.add_argument("--case-key", required=True)
    p_hold_resolve.add_argument(
        "--decision",
        required=True,
        choices=("accept-proposed", "keep-current"),
    )
    p_hold_resolve.add_argument("--reason", required=True)
    p_hold_resolve.add_argument("--confirm", action="store_true")
    p_hold_resolve.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_hold_resolve.set_defaults(func=cmd_holdings_resolve)

    p_hold_recover = holdings_sub.add_parser("recover", help="classify one uncertain apply")
    p_hold_recover.add_argument("--case-key", required=True)
    p_hold_recover.add_argument("--confirm", action="store_true")
    p_hold_recover.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_hold_recover.set_defaults(func=cmd_holdings_recover)

    p_hold_events = holdings_sub.add_parser(
        "events",
        help="exact-resource Feishu holdings event ingress",
    )
    hold_events_sub = p_hold_events.add_subparsers(
        dest="holdings_events_cmd",
        required=True,
    )
    p_hold_events_status = hold_events_sub.add_parser(
        "status",
        help="show local/config readiness without a Feishu request",
    )
    p_hold_events_status.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_hold_events_status.set_defaults(func=cmd_holdings_events_status)
    p_hold_events_subscribe = hold_events_sub.add_parser(
        "subscribe",
        help="create the exact configured Base document event subscription",
    )
    p_hold_events_subscribe.add_argument("--confirm", action="store_true")
    p_hold_events_subscribe.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_hold_events_subscribe.set_defaults(func=cmd_holdings_events_subscribe)
    p_hold_events_listen = hold_events_sub.add_parser(
        "listen",
        help="run the long connection and leased local worker",
    )
    p_hold_events_listen.add_argument("--confirm", action="store_true")
    p_hold_events_listen.add_argument("--poll-seconds", type=float, default=1.0)
    p_hold_events_listen.add_argument("--limit", type=int, default=100)
    p_hold_events_listen.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS
    )
    p_hold_events_listen.set_defaults(func=cmd_holdings_events_listen)

    p_cash = sp.add_parser("cash", help="show cash positions")
    p_cash.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_cash.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_cash)
    p_cash.set_defaults(func=cmd_cash)

    p_futu = sp.add_parser("futu", help="Futu holdings synchronization")
    futu_sub = p_futu.add_subparsers(dest="futu_cmd", required=True)
    p_futu_accounts = futu_sub.add_parser(
        "accounts",
        help="read the OpenD account list needed for explicit account mapping",
    )
    p_futu_accounts.add_argument(
        "--market",
        required=True,
        choices=("US", "HK"),
        help="explicit Futu trade market context",
    )
    p_futu_accounts.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="output JSON",
    )
    p_futu_accounts.set_defaults(func=cmd_futu_accounts)
    p_futu_sync = futu_sub.add_parser("sync", help="observe Futu CASH; sync MMF and stock/ETF quantity + average cost")
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
    p_cash_flow_duplicates = cash_flow_sub.add_parser(
        "duplicates",
        help="fresh read-only audit of canonical cash-flow duplicate groups",
    )
    p_cash_flow_duplicates.add_argument(
        "--account",
        default=argparse.SUPPRESS,
        help="account to audit; defaults to all accounts",
    )
    p_cash_flow_duplicates.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="output JSON",
    )
    p_cash_flow_duplicates.set_defaults(func=cmd_cash_flow_duplicates)

    p_cash_flow_reconcile = cash_flow_sub.add_parser(
        "reconcile",
        help="fill generated fields for manually entered cash_flow rows",
    )
    p_cash_flow_reconcile.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to all accounts")
    p_cash_flow_reconcile.add_argument("--record-id", help="limit reconciliation to one Feishu cash_flow record")
    p_cash_flow_reconcile.add_argument("--exchange-rate", type=float, help="manual historical FX rate; requires single-record evidence")
    p_cash_flow_reconcile.add_argument("--rate-date", help="manual FX evidence date (must equal flow_date)")
    p_cash_flow_reconcile.add_argument("--rate-source", help="traceable manual FX evidence source")
    p_cash_flow_reconcile.add_argument("--apply", action="store_true", help="write derived fields back to Feishu")
    p_cash_flow_reconcile.add_argument("--confirm", action="store_true", help="required with --apply")
    p_cash_flow_reconcile.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_cash_flow_reconcile.set_defaults(func=cmd_cash_flow_reconcile)

    p_cash_flow_fx = cash_flow_sub.add_parser(
        "fx-evidence",
        help="manage local FX confirmation evidence",
    )
    cash_flow_fx_sub = p_cash_flow_fx.add_subparsers(
        dest="cash_flow_fx_cmd",
        required=True,
    )
    p_cash_flow_fx_import = cash_flow_fx_sub.add_parser(
        "import-legacy",
        help="idempotently import confirmations from an old effects database",
    )
    p_cash_flow_fx_import.add_argument("--legacy-db", default=None)
    p_cash_flow_fx_import.add_argument("--confirm", action="store_true")
    p_cash_flow_fx_import.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
    )
    p_cash_flow_fx_import.set_defaults(func=cmd_cash_flow_fx_import_legacy)

    p_cash_flow_review = cash_flow_sub.add_parser(
        "review",
        help="scan Feishu and show unresolved effects",
    )
    p_cash_flow_review.add_argument("--account", default=argparse.SUPPRESS)
    p_cash_flow_review.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_cash_flow_review.set_defaults(func=cmd_cash_flow_review)

    p_cash_flow_effects = cash_flow_sub.add_parser(
        "effects",
        help="durable cash-flow holding effect workflow",
    )
    cash_flow_effects_sub = p_cash_flow_effects.add_subparsers(
        dest="cash_flow_effects_cmd",
        required=True,
    )
    p_effects_init = cash_flow_effects_sub.add_parser(
        "init",
        help="initialize immutable SQLite workflow and CASH baselines",
    )
    p_effects_init.add_argument("--cutover-date", required=True)
    p_effects_init.add_argument("--confirm", action="store_true")
    p_effects_init.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_init.set_defaults(func=cmd_cash_flow_effects_init)

    p_effects_scan = cash_flow_effects_sub.add_parser(
        "scan",
        help="read Feishu, discover effects, and deliver durable receipts",
    )
    p_effects_scan.add_argument("--account", default=argparse.SUPPRESS)
    p_effects_scan.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_scan.set_defaults(func=cmd_cash_flow_effects_scan)

    p_effects_list = cash_flow_effects_sub.add_parser("list")
    p_effects_list.add_argument("--account", default=argparse.SUPPRESS)
    p_effects_list.add_argument("--all-versions", action="store_true")
    p_effects_list.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_list.set_defaults(func=cmd_cash_flow_effects_list)

    p_effects_show = cash_flow_effects_sub.add_parser("show")
    p_effects_show.add_argument("--effect-id", required=True)
    p_effects_show.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_show.set_defaults(func=cmd_cash_flow_effects_show)

    p_effects_preview = cash_flow_effects_sub.add_parser("preview")
    p_effects_preview.add_argument("--effect-id", required=True)
    p_effects_preview.add_argument(
        "--external-action",
        choices=("accept_current", "restore"),
    )
    p_effects_preview.add_argument("--historical-apply", action="store_true")
    p_effects_preview.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_preview.set_defaults(func=cmd_cash_flow_effects_preview)

    p_effects_confirm = cash_flow_effects_sub.add_parser("confirm")
    p_effects_confirm.add_argument("--effect-id", required=True)
    p_effects_confirm.add_argument("--preview-hash", required=True)
    p_effects_confirm.add_argument(
        "--external-action",
        choices=("accept_current", "restore"),
    )
    p_effects_confirm.add_argument("--historical-apply", action="store_true")
    p_effects_confirm.add_argument("--confirm", action="store_true")
    p_effects_confirm.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_confirm.set_defaults(func=cmd_cash_flow_effects_confirm)

    p_effects_record_only = cash_flow_effects_sub.add_parser("record-only")
    p_effects_record_only.add_argument("--effect-id", required=True)
    p_effects_record_only.add_argument("--confirm", action="store_true")
    p_effects_record_only.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_record_only.set_defaults(func=cmd_cash_flow_effects_record_only)

    p_effects_retry = cash_flow_effects_sub.add_parser("retry")
    p_effects_retry.add_argument("--effect-id", required=True)
    p_effects_retry.add_argument("--confirm", action="store_true")
    p_effects_retry.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_retry.set_defaults(func=cmd_cash_flow_effects_retry)

    p_effects_audit = cash_flow_effects_sub.add_parser("audit")
    p_effects_audit.add_argument("--account", required=True)
    p_effects_audit.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_audit.set_defaults(func=cmd_cash_flow_effects_audit)

    p_effects_backup = cash_flow_effects_sub.add_parser(
        "backup",
        help="create a verified SQLite online backup without overwriting",
    )
    p_effects_backup.add_argument("--output", required=True)
    p_effects_backup.add_argument("--confirm", action="store_true")
    p_effects_backup.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p_effects_backup.set_defaults(func=cmd_cash_flow_effects_backup)

    p_compensation = sp.add_parser("compensation", help="inspect and retry durable compensation tasks")
    compensation_sub = p_compensation.add_subparsers(dest="compensation_cmd", required=True)
    p_compensation_list = compensation_sub.add_parser("list", help="list unresolved compensation tasks")
    p_compensation_list.add_argument("--include-resolved", action="store_true", help="include resolved tasks")
    p_compensation_list.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_compensation_list.set_defaults(func=cmd_compensation_list)
    p_compensation_retry = compensation_sub.add_parser("retry", help="retry one supported compensation task")
    p_compensation_retry.add_argument("--task-id", required=True, help="compensation task id")
    p_compensation_retry.add_argument("--confirm", action="store_true", help="required to apply target writes")
    p_compensation_retry.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    p_compensation_retry.set_defaults(func=cmd_compensation_retry)

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
    p_positions_distribution.add_argument("--group-cash", action="store_true", help="group by asset and collapse cash/MMF into one 现金及等价物 row")
    p_positions_distribution.add_argument("--no-value", action="store_true", help="hide market value fields; show quantities only")
    p_positions_distribution.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="output JSON")
    add_service_args(p_positions_distribution)
    p_positions_distribution.set_defaults(func=cmd_positions_distribution)

    p_distribution = sp.add_parser("distribution", help="shortcut for positions distribution")
    p_distribution.add_argument("--account", default=argparse.SUPPRESS, help="account to operate on; defaults to config/PORTFOLIO_ACCOUNT")
    p_distribution.add_argument("--accounts", default=None, help="comma-separated accounts to merge; overrides --account")
    p_distribution.add_argument("--by-asset", action="store_true", help="group distribution by asset code across accounts")
    p_distribution.add_argument("--group-cash", action="store_true", help="group by asset and collapse cash/MMF into one 现金及等价物 row")
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
