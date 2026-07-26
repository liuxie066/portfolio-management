"""Discovery, preview, and confirmed application of CASH holding effects."""
from __future__ import annotations

import getpass
import socket
import uuid
from contextlib import ExitStack
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Dict, Optional

from src import config
from src.models import (
    CASH_ASSET_ID,
    HKD_CASH_ASSET_ID,
    USD_CASH_ASSET_ID,
    AssetClass,
    AssetType,
    CashFlow,
    Holding,
)
from src.process_lock import account_lock_key, process_lock
from src.time_utils import bj_now_naive, bj_today

from .business_calendar_service import BusinessCalendarService
from .cash_flow_effect_store import (
    HASH_CONTRACT_VERSION,
    TERMINAL_STATES,
    UNRESOLVED_STATES,
    CashFlowEffectStore,
    sha256_json,
)
from .compensation_service import CompensationService
from .nav_finality import evaluate_nav_finality


MONEY_QUANT = Decimal("0.01")
CASH_ASSETS = {
    "CNY": CASH_ASSET_ID,
    "USD": USD_CASH_ASSET_ID,
    "HKD": HKD_CASH_ASSET_ID,
}
CASH_NAMES = {
    "CNY": "人民币现金",
    "USD": "美元现金",
    "HKD": "港币现金",
}
FUTU_BROKER = "富途"


def observe_futu_cash_result(
    *,
    storage: Any,
    account: str,
    cash_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Bridge an already-refreshed Futu observation into the SQLite workflow."""
    db_path = CashFlowEffectStore.resolve_db_path()
    configured_cutover = config.get("cash_flow.effects.cutover_date")
    if not db_path.exists() and not configured_cutover:
        return {"success": True, "status": "not_activated"}
    store = CashFlowEffectStore()
    store.assert_cutover(configured_cutover)
    return CashFlowEffectService(storage=storage, store=store).observe_futu_cash(
        account=account,
        cash_by_currency=dict(cash_result.get("cash_observations") or {}),
        account_id=cash_result.get("account_id"),
        profile_fingerprint=cash_result.get("profile_fingerprint"),
        source_name=str(cash_result.get("source") or "futu-openapi"),
    )


class CashFlowEffectService:
    """Fail-closed application boundary for every cash-flow CASH mutation."""

    def __init__(
        self,
        *,
        storage: Any,
        store: Optional[CashFlowEffectStore] = None,
        calendar: Optional[BusinessCalendarService] = None,
        futu_provider_factory: Optional[Callable[[str], Any]] = None,
        compensation: Optional[CompensationService] = None,
    ):
        self.storage = storage
        self.store = store or CashFlowEffectStore()
        self.calendar = calendar or BusinessCalendarService.from_config()
        self.futu_provider_factory = futu_provider_factory or self._default_futu_provider
        self.compensation = compensation or CompensationService(storage=storage)
        self._nav_history_cache: dict[str, bool] = {}
        self._current_flow_accounts_cache: Optional[dict[str, str]] = None

    @staticmethod
    def _default_futu_provider(account: str) -> Any:
        from .futu_balance_sync_service import FutuOpenApiBalanceProvider

        return FutuOpenApiBalanceProvider.from_account(account)

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(f"invalid decimal amount: {value!r}") from exc
        if not result.is_finite():
            raise ValueError(f"amount must be finite: {value!r}")
        return result

    @classmethod
    def _money(cls, value: Any) -> str:
        return format(
            cls._decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            "f",
        )

    @staticmethod
    def _identity(asset_id: str, account: str, broker: str) -> str:
        return f"{asset_id}|{account}|{broker}"

    @staticmethod
    def _identity_payload(asset_id: str, account: str, broker: str) -> Dict[str, str]:
        return {"asset_id": asset_id, "account": account, "broker": broker}

    @classmethod
    def _holding_payload(cls, holding: Optional[Holding]) -> Dict[str, Any]:
        if holding is None:
            return {"record_id": None, "quantity": "0.00"}
        return {
            "record_id": holding.record_id,
            "asset_id": holding.asset_id,
            "account": holding.account,
            "broker": holding.broker or "",
            "quantity": cls._money(holding.quantity),
        }

    @classmethod
    def _holding_hash(cls, holding: Optional[Holding]) -> str:
        return sha256_json(cls._holding_payload(holding))

    @staticmethod
    def _operator_context(run_id: Optional[str] = None) -> Dict[str, Any]:
        return {
            "method": "local_cli",
            "trusted_identity": False,
            "confirmed_at": bj_now_naive().isoformat(),
            "run_id": run_id or f"cash-flow-{uuid.uuid4().hex}",
            "hostname": socket.gethostname(),
            "local_username": getpass.getuser(),
        }

    @classmethod
    def source_from_cash_flow(cls, flow: CashFlow) -> Dict[str, Any]:
        account = str(flow.account or "").strip()
        broker = str(flow.broker or "").strip()
        currency = str(flow.currency or "").strip().upper()
        flow_type = str(flow.flow_type or "").strip().upper()
        amount = cls._decimal(flow.amount)
        if not flow.record_id:
            raise ValueError("cash_flow record_id is required")
        if not account:
            raise ValueError("cash_flow account is required")
        if not broker:
            raise ValueError("cash_flow broker is required")
        if currency not in CASH_ASSETS:
            raise ValueError(f"unsupported cash_flow currency: {currency}")
        if amount == 0:
            raise ValueError("cash_flow amount must be non-zero")
        if flow_type not in {"DEPOSIT", "WITHDRAW"}:
            raise ValueError(f"unsupported cash_flow flow_type: {flow_type}")
        if flow_type == "DEPOSIT" and amount <= 0:
            raise ValueError("DEPOSIT amount must be positive")
        if flow_type == "WITHDRAW" and amount >= 0:
            raise ValueError("WITHDRAW amount must be negative")
        return {
            "record_id": str(flow.record_id),
            "flow_date": flow.flow_date.isoformat(),
            "account": account,
            "broker": broker,
            "currency": currency,
            "flow_type": flow_type,
            "signed_amount": cls._money(amount),
        }

    @classmethod
    def _raw_source(cls, flow: CashFlow) -> Dict[str, Any]:
        return {
            "record_id": str(flow.record_id or ""),
            "flow_date": flow.flow_date.isoformat() if flow.flow_date else "",
            "account": str(flow.account or "").strip(),
            "broker": str(flow.broker or "").strip(),
            "currency": str(flow.currency or "").strip().upper(),
            "flow_type": str(flow.flow_type or "").strip().upper(),
            "signed_amount": str(flow.amount),
            "remark": str(flow.remark or ""),
        }

    def _has_nav_history(self, account: str) -> bool:
        if account in self._nav_history_cache:
            return self._nav_history_cache[account]
        getter = getattr(self.storage, "get_nav_history", None)
        if not callable(getter):
            self._nav_history_cache[account] = False
            return False
        try:
            rows = getter(account, days=9999)
        except TypeError:
            rows = getter(account)
        result = bool(rows)
        self._nav_history_cache[account] = result
        return result

    def _previous_nav_is_final(self, account: str, flow_date: date) -> bool:
        if not self._has_nav_history(account):
            return True
        previous_day = self.calendar.previous_business_day(before=flow_date)
        getter = getattr(self.storage, "get_nav_on_date", None)
        if not callable(getter):
            return False
        nav = getter(account, previous_day)
        if nav is None:
            return False
        return evaluate_nav_finality(
            getattr(nav, "details", None),
            target_date=previous_day,
        ).eligible

    def _initial_state(self, source: Dict[str, Any], mode: str) -> str:
        flow_date = date.fromisoformat(source["flow_date"])
        if flow_date > bj_today():
            return "scheduled"
        if mode == "apply" and not self._previous_nav_is_final(
            source["account"],
            flow_date,
        ):
            return "scheduled"
        return "pending"

    def initialize_fingerprints(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        """Explicit init-time acknowledgement of current CASH baselines."""
        holdings = self.storage.get_holdings_fresh(
            account=account,
            include_empty=True,
        )
        return self.initialize_fingerprints_from_holdings(
            holdings,
            account=account,
        )

    def initialize_fingerprints_from_holdings(
        self,
        holdings: list[Holding],
        *,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist baselines from the complete read performed before DB creation."""
        rows = [
            holding for holding in holdings
            if holding.asset_id in set(CASH_ASSETS.values())
            and holding.asset_type == AssetType.CASH
            and (account is None or holding.account == account)
        ]
        for holding in rows:
            identity = self._identity(
                holding.asset_id,
                holding.account,
                holding.broker or "",
            )
            self.store.confirm_fingerprint(
                holding_identity=identity,
                holding_record_id=holding.record_id,
                amount=self._money(holding.quantity),
                confirmation_hash=self._holding_hash(holding),
                effect_id=None,
            )
        return {
            "success": True,
            "account": account,
            "confirmed_baselines": len(rows),
        }

    def initialize_from_snapshot(
        self,
        *,
        flows: list[CashFlow],
        holdings: list[Holding],
    ) -> Dict[str, Any]:
        """Commit init baselines and the first scan from complete source reads."""
        self._current_flow_accounts_cache = {
            str(flow.record_id or ""): str(flow.account or "")
            for flow in flows
            if flow.record_id
        }
        with process_lock("cash-flow-effects:scanner"):
            with self.store.transaction():
                baselines = self.initialize_fingerprints_from_holdings(holdings)
                run_id = self.store.begin_scan(scope="all")
                scan = self._commit_scan(
                    flows=flows,
                    holdings=holdings,
                    account=None,
                    scan_run_id=run_id,
                    enqueue_receipts=False,
                )
        effects = self.list_effects(
            latest_only=True,
            states=UNRESOLVED_STATES,
        )
        return {
            "success": True,
            "baselines": baselines,
            "scan": scan,
            "effects": effects,
            "count": len(effects),
        }

    def scan(
        self,
        *,
        account: Optional[str] = None,
        enqueue_receipts: bool = False,
    ) -> Dict[str, Any]:
        """Complete Feishu scan; partial reads never become successful runs."""
        scope = account or "all"
        with process_lock("cash-flow-effects:scanner"):
            previous_scan = self.store.latest_scan(scope=scope)
            try:
                # Cash-flow identity is the Feishu record_id. Read the complete
                # source even for an account-scoped gate so an account move is
                # treated as one correction rather than a deletion plus an
                # unrelated new record.
                flows = self.storage.get_cash_flows()
                self._current_flow_accounts_cache = {
                    str(flow.record_id or ""): str(flow.account or "")
                    for flow in flows
                    if flow.record_id
                }
                holdings = self.storage.get_holdings_fresh(
                    account=account,
                    include_empty=True,
                )
                with self.store.transaction():
                    run_id = self.store.begin_scan(scope=scope)
                    result = self._commit_scan(
                        flows=flows,
                        holdings=holdings,
                        account=account,
                        scan_run_id=run_id,
                        enqueue_receipts=enqueue_receipts,
                    )
                    if (
                        enqueue_receipts
                        and previous_scan
                        and previous_scan.get("status") == "failed"
                    ):
                        self.store.enqueue_receipt(
                            receipt_key=f"scan-recovered:{run_id}",
                            receipt_type="runtime_recovered",
                            scan_run_id=run_id,
                            payload={"scope": scope, "scan_run_id": run_id},
                        )
                return result
            except Exception as exc:
                with self.store.transaction():
                    failed_run_id = self.store.begin_scan(scope=scope)
                    self.store.finish_scan(
                        failed_run_id,
                        status="failed",
                        error=str(exc),
                    )
                    if (
                        enqueue_receipts
                        and (
                            not previous_scan
                            or previous_scan.get("status") != "failed"
                        )
                    ):
                        self.store.enqueue_receipt(
                            receipt_key=f"scan-error:{failed_run_id}",
                            receipt_type="runtime_error",
                            scan_run_id=failed_run_id,
                            payload={
                                "scope": scope,
                                "scan_run_id": failed_run_id,
                                "error": str(exc),
                            },
                        )
                raise

    def _commit_scan(
        self,
        *,
        flows: list[CashFlow],
        holdings: list[Holding],
        account: Optional[str],
        scan_run_id: str,
        enqueue_receipts: bool,
    ) -> Dict[str, Any]:
        cutover = self.store.assert_cutover(
            config.get("cash_flow.effects.cutover_date")
        )
        added = changed = deleted = blocked = 0
        seen_records: set[str] = set()
        changed_effect_ids: list[str] = []

        for flow in flows:
            raw_source = self._raw_source(flow)
            record_id = str(flow.record_id or "")
            if record_id:
                seen_records.add(record_id)
            latest = self.store.get_latest_for_record(record_id)
            if (
                account
                and raw_source.get("account") != account
                and (latest or {}).get("account") != account
            ):
                continue
            try:
                source = self.source_from_cash_flow(flow)
                mode = "record_only" if flow.flow_date < cutover else "apply"
                desired_state = self._initial_state(source, mode)
                error = None
            except Exception as exc:
                source = raw_source
                mode = "apply"
                desired_state = "blocked"
                error = str(exc)
                blocked += 1
            source_hash = sha256_json({
                "contract": HASH_CONTRACT_VERSION,
                "source": source,
            })
            if latest and latest["source_hash"] == source_hash:
                if (
                    latest["state"] == "scheduled"
                    and desired_state == "pending"
                ):
                    self.store.update_effect(
                        latest["effect_id"],
                        state="pending",
                        event_type="scheduled_activated",
                        expected_states={"scheduled"},
                    )
                    changed_effect_ids.append(latest["effect_id"])
                    changed += 1
                continue
            if latest and latest["state"] == "compensation_pending":
                if self._defer_source_change(
                    latest,
                    new_source_hash=source_hash,
                    new_source=source,
                ):
                    changed_effect_ids.append(latest["effect_id"])
                    changed += 1
                continue
            effect = self.store.create_version(
                source=source,
                source_hash=source_hash,
                state=desired_state,
                mode=mode,
            )
            changed_effect_ids.append(effect["effect_id"])
            if error:
                self.store.update_effect(
                    effect["effect_id"],
                    fields={"last_error": error},
                    event_type="blocked",
                    event_payload={"error": error},
                    expected_states={"blocked"},
                )
            if latest:
                changed += 1
            else:
                added += 1

        for latest in self.store.list_effects(account=account, latest_only=True):
            if latest["effect_kind"] != "cash_flow":
                continue
            if latest["record_id"] in seen_records:
                continue
            if (latest.get("source") or {}).get("deleted"):
                continue
            previous_source = dict(latest.get("source") or {})
            deletion_source = {
                **previous_source,
                "deleted": True,
                "signed_amount": "0.00",
                "previous_source_hash": latest["source_hash"],
            }
            deletion_hash = sha256_json({
                "contract": HASH_CONTRACT_VERSION,
                "source": deletion_source,
            })
            if latest["state"] == "compensation_pending":
                if self._defer_source_change(
                    latest,
                    new_source_hash=deletion_hash,
                    new_source=deletion_source,
                ):
                    changed_effect_ids.append(latest["effect_id"])
                    deleted += 1
                continue
            mode = (
                "apply"
                if latest["state"] == "applied" and latest["mode"] == "apply"
                else "record_only"
            )
            deletion_effect = self.store.create_version(
                source=deletion_source,
                source_hash=deletion_hash,
                state="pending",
                mode=mode,
                event_type="deletion_discovered",
            )
            changed_effect_ids.append(deletion_effect["effect_id"])
            deleted += 1

        holding_changes, holding_effect_ids = self._scan_holding_fingerprints(
            holdings=holdings,
            account=account,
        )
        changed_effect_ids.extend(holding_effect_ids)
        changed += holding_changes
        digest_payload = {
            "cash_flows": sorted(
                (str(flow.record_id), self._raw_source(flow)) for flow in flows
            ),
            "cash_holdings": sorted(
                (
                    holding.record_id or "",
                    self._holding_payload(holding),
                )
                for holding in holdings
                if holding.asset_id in set(CASH_ASSETS.values())
                and holding.asset_type == AssetType.CASH
            ),
        }
        digest = sha256_json(digest_payload)
        scan = self.store.finish_scan(
            scan_run_id,
            status="completed",
            source_record_count=len(flows),
            source_digest=digest,
            added_count=added,
            changed_count=changed,
            deleted_count=deleted,
            blocked_count=blocked,
        )
        if enqueue_receipts and (added or changed or deleted or blocked):
            effect_summaries = []
            for effect_id in list(dict.fromkeys(changed_effect_ids))[:10]:
                effect = self.store.get_effect(effect_id)
                if effect:
                    effect_summaries.append({
                        "effect_id": effect["effect_id"],
                        "account": effect["account"],
                        "flow_date": effect["flow_date"],
                        "broker": effect["broker"],
                        "currency": effect["currency"],
                        "signed_amount": effect["signed_amount"],
                        "state": effect["state"],
                    })
            self.store.enqueue_receipt(
                receipt_key=f"scan:{digest}",
                receipt_type="discovery",
                scan_run_id=scan_run_id,
                payload={
                    "scope": account or "all",
                    "added": added,
                    "changed": changed,
                    "deleted": deleted,
                    "blocked": blocked,
                    "scan_digest": digest,
                    "effects": effect_summaries,
                },
            )
        return {
            "success": True,
            "scan_run": scan,
            "added": added,
            "changed": changed,
            "deleted": deleted,
            "blocked": blocked,
            "source_digest": digest,
        }

    def _scan_holding_fingerprints(
        self,
        *,
        holdings: list[Holding],
        account: Optional[str],
    ) -> tuple[int, list[str]]:
        cash_holdings = {
            self._identity(item.asset_id, item.account, item.broker or ""): item
            for item in holdings
            if item.asset_id in set(CASH_ASSETS.values())
            and item.asset_type == AssetType.CASH
        }
        known = {
            row["holding_identity"]: row
            for row in self.store.list_fingerprints(account=account)
        }
        compensation_identities = {
            self._identity(
                str(target.get("asset_id") or ""),
                str(target.get("account") or ""),
                str(target.get("broker") or ""),
            )
            for effect in self.store.list_effects(
                latest_only=True,
                states={"compensation_pending"},
            )
            for target in (effect.get("targets") or [])
            if isinstance(target, dict)
        }
        changed = 0
        changed_effect_ids: list[str] = []
        for identity in sorted(set(cash_holdings) | set(known)):
            holding = cash_holdings.get(identity)
            observed_hash = self._holding_hash(holding)
            observed_amount = self._money(holding.quantity if holding else 0)
            fingerprint = known.get(identity)
            self.store.observe_fingerprint(
                holding_identity=identity,
                holding_record_id=holding.record_id if holding else None,
                amount=observed_amount,
                observation_hash=observed_hash,
            )
            if fingerprint and fingerprint.get("last_confirmed_hash") == observed_hash:
                latest = self.store.get_latest_for_record(
                    f"holding:{identity}",
                    effect_kind="cash_holding_external_change",
                )
                if latest and latest["state"] in UNRESOLVED_STATES:
                    self.store.update_effect(
                        latest["effect_id"],
                        state="applied",
                        event_type="resolved_by_observation",
                        expected_states=UNRESOLVED_STATES,
                    )
                    changed_effect_ids.append(latest["effect_id"])
                    changed += 1
                continue
            if not fingerprint or not fingerprint.get("last_confirmed_hash"):
                previous_amount = None
            else:
                previous_amount = fingerprint.get("last_confirmed_amount")
            if identity in compensation_identities:
                # A partially applied target belongs to the compensation
                # workflow, not to an independent direct Feishu edit.
                continue
            asset_id, row_account, broker = identity.split("|", 2)
            currency = asset_id.split("-", 1)[0]
            existing_external = self.store.get_latest_for_record(
                f"holding:{identity}",
                effect_kind="cash_holding_external_change",
            )
            source = {
                "record_id": f"holding:{identity}",
                "holding_identity": identity,
                "holding_record_id": holding.record_id if holding else None,
                "flow_date": (
                    existing_external["flow_date"]
                    if existing_external
                    and (existing_external.get("source") or {}).get("observed_hash")
                    == observed_hash
                    else bj_today().isoformat()
                ),
                "account": row_account,
                "broker": broker,
                "currency": currency,
                "signed_amount": "0.00",
                "observed_amount": observed_amount,
                "observed_hash": observed_hash,
                "last_confirmed_amount": previous_amount,
            }
            source_hash = sha256_json({
                "contract": HASH_CONTRACT_VERSION,
                "source": source,
            })
            if (
                existing_external
                and existing_external["source_hash"] == source_hash
            ):
                continue
            if (
                existing_external
                and existing_external["state"] == "compensation_pending"
            ):
                if self._defer_source_change(
                    existing_external,
                    new_source_hash=source_hash,
                    new_source=source,
                ):
                    changed_effect_ids.append(existing_external["effect_id"])
                    changed += 1
                continue
            external_effect = self.store.create_version(
                source=source,
                source_hash=source_hash,
                state="pending",
                mode="apply",
                effect_kind="cash_holding_external_change",
                event_type="external_holding_change",
            )
            changed_effect_ids.append(external_effect["effect_id"])
            changed += 1
        return changed, changed_effect_ids

    def review(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        scan = self.scan(account=account, enqueue_receipts=False)
        audit = self.audit(account=account)
        effects = self.list_effects(
            account=account,
            latest_only=True,
            states=UNRESOLVED_STATES,
        )
        return {
            "success": True,
            "scan": scan,
            "audit": audit,
            "effects": effects,
            "count": len(effects),
        }

    def list_effects(
        self,
        *,
        account: Optional[str] = None,
        latest_only: bool = True,
        states: Optional[set[str]] = None,
    ) -> list[Dict[str, Any]]:
        """List by every affected account, including the previous identity."""
        effects = self.store.list_effects(
            latest_only=latest_only,
            states=states,
        )
        if account:
            effects = [
                effect
                for effect in effects
                if account in self._effect_accounts(effect)
            ]
        return effects

    def _current_flow_accounts(self) -> dict[str, str]:
        if self._current_flow_accounts_cache is None:
            flows = self.storage.get_cash_flows()
            self._current_flow_accounts_cache = {
                str(flow.record_id or ""): str(flow.account or "")
                for flow in flows
                if flow.record_id
            }
        return self._current_flow_accounts_cache

    def _defer_source_change(
        self,
        effect: Dict[str, Any],
        *,
        new_source_hash: str,
        new_source: Dict[str, Any],
    ) -> bool:
        """Keep compensation current until its recorded targets are resolved."""
        for event in reversed(self.store.list_events(effect["effect_id"])):
            if (
                event["event_type"] == "source_change_deferred_for_compensation"
                and (event.get("payload") or {}).get("new_source_hash")
                == new_source_hash
            ):
                return False
        self.store.append_event(
            effect["effect_id"],
            "source_change_deferred_for_compensation",
            {
                "old_source_hash": effect["source_hash"],
                "new_source_hash": new_source_hash,
                "new_source": new_source,
            },
        )
        return True

    def _current_cash_flow_source(
        self,
        effect: Dict[str, Any],
    ) -> tuple[Dict[str, Any], str, str, Optional[str]]:
        """Read the authoritative source and persist a correction when it moved."""
        if effect["effect_kind"] != "cash_flow":
            return effect["source"], effect["source_hash"], effect["mode"], None
        # Never scope this read by the old account. A user may have corrected
        # the account on the same Feishu record.
        flows = self.storage.get_cash_flows()
        self._current_flow_accounts_cache = {
            str(flow.record_id or ""): str(flow.account or "")
            for flow in flows
            if flow.record_id
        }
        current = next(
            (
                flow
                for flow in flows
                if str(flow.record_id or "") == str(effect["record_id"])
            ),
            None,
        )
        error: Optional[str] = None
        if current is None:
            existing_source = dict(effect.get("source") or {})
            source = (
                existing_source
                if existing_source.get("deleted")
                else {
                    **existing_source,
                    "deleted": True,
                    "signed_amount": "0.00",
                    "previous_source_hash": effect["source_hash"],
                }
            )
            mode = (
                "apply"
                if effect["mode"] == "apply"
                and effect["state"] in {"applying", "applied"}
                else "record_only"
            )
            state = "pending"
            event_type = "deletion_discovered"
        else:
            try:
                source = self.source_from_cash_flow(current)
                mode = (
                    "record_only"
                    if current.flow_date < self.store.cutover_date
                    else "apply"
                )
                state = self._initial_state(source, mode)
                event_type = "source_changed"
            except Exception as exc:
                source = self._raw_source(current)
                mode = "apply"
                state = "blocked"
                error = str(exc)
                event_type = "source_changed_blocked"
        source_hash = sha256_json({
            "contract": HASH_CONTRACT_VERSION,
            "source": source,
        })
        if source_hash == effect["source_hash"]:
            return source, source_hash, mode, None
        if effect["state"] == "compensation_pending":
            self._defer_source_change(
                effect,
                new_source_hash=source_hash,
                new_source=source,
            )
            return source, source_hash, mode, None
        correction = self.store.create_version(
            source=source,
            source_hash=source_hash,
            state=state,
            mode=mode,
            effect_kind="cash_flow",
            event_type=event_type,
        )
        if error:
            correction = self.store.update_effect(
                correction["effect_id"],
                state="blocked",
                fields={"last_error": error},
                event_type="blocked",
                event_payload={"error": error},
                expected_states={"blocked"},
            )
        self.store.append_event(
            effect["effect_id"],
            "authoritative_source_changed",
            {
                "old_source_hash": effect["source_hash"],
                "new_source_hash": source_hash,
                "correction_effect_id": correction["effect_id"],
            },
        )
        return source, source_hash, mode, correction["effect_id"]

    def observe_futu_cash(
        self,
        *,
        account: str,
        cash_by_currency: Dict[str, Any],
        account_id: Optional[int],
        profile_fingerprint: Optional[str],
        source_name: str = "futu-openapi",
    ) -> Dict[str, Any]:
        """Persist observe-only OpenD drift without changing cash_flow or holdings."""
        if account_id is None:
            raise RuntimeError("Futu observation lacks account_id evidence")
        if not profile_fingerprint:
            raise RuntimeError("Futu observation lacks profile fingerprint")
        created = resolved = suppressed = deferred = 0
        effects: list[Dict[str, Any]] = []
        with process_lock("cash-flow-effects:scanner"):
            for currency in sorted(CASH_ASSETS):
                raw = (cash_by_currency or {}).get(currency)
                if raw is None:
                    continue
                observed = self._money(raw)
                source = {
                    "record_id": (
                        "futu-reconciliation:"
                        f"{self._identity(CASH_ASSETS[currency], account, FUTU_BROKER)}"
                    ),
                    "flow_date": bj_today().isoformat(),
                    "account": account,
                    "broker": FUTU_BROKER,
                    "currency": currency,
                    "signed_amount": "0.00",
                    "observed_target": observed,
                    "account_id": int(account_id),
                    "profile_fingerprint": str(profile_fingerprint),
                    "observation_source": str(source_name),
                }
                competing = []
                for effect in self.store.list_effects(
                    latest_only=True,
                    states=UNRESOLVED_STATES,
                ):
                    if (
                        effect["effect_kind"] != "cash_flow"
                        or effect["mode"] != "apply"
                    ):
                        continue
                    if any(
                        operation_source.get("account") == account
                        and operation_source.get("broker") == FUTU_BROKER
                        and operation_source.get("currency") == currency
                        for operation_source, _ in self._cash_flow_operations(effect)
                    ):
                        competing.append(effect)
                record_id = source["record_id"]
                current_reconciliation = self.store.get_latest_for_record(
                    record_id,
                    effect_kind="broker_cash_reconciliation",
                )
                if competing:
                    if (
                        current_reconciliation
                        and current_reconciliation["state"] in UNRESOLVED_STATES
                    ):
                        if current_reconciliation["state"] == "compensation_pending":
                            if self._defer_source_change(
                                current_reconciliation,
                                new_source_hash=(
                                    f"cash_flow:{competing[-1]['effect_id']}"
                                ),
                                new_source={
                                    "cash_flow_effect_id": competing[-1]["effect_id"],
                                    "reason": "competing_cash_flow",
                                },
                            ):
                                deferred += 1
                        else:
                            self.store.update_effect(
                                current_reconciliation["effect_id"],
                                state="superseded_by_cash_flow",
                                event_type="superseded_by_cash_flow",
                                event_payload={
                                    "cash_flow_effect_id": competing[-1]["effect_id"]
                                },
                                expected_states=UNRESOLVED_STATES,
                            )
                    suppressed += 1
                    continue
                holding = self.storage.get_holding_fresh(
                    CASH_ASSETS[currency],
                    account,
                    FUTU_BROKER,
                )
                current_amount = self._money(holding.quantity if holding else 0)
                if current_amount == observed:
                    if (
                        current_reconciliation
                        and current_reconciliation["state"] in UNRESOLVED_STATES
                    ):
                        if current_reconciliation["state"] == "compensation_pending":
                            if self._defer_source_change(
                                current_reconciliation,
                                new_source_hash=f"observation_matches:{observed}",
                                new_source={
                                    "observed_target": observed,
                                    "reason": (
                                        "observation_matches_during_compensation"
                                    ),
                                },
                            ):
                                deferred += 1
                        else:
                            self.store.update_effect(
                                current_reconciliation["effect_id"],
                                state="applied",
                                event_type="resolved_by_observation",
                                event_payload={"observed_target": observed},
                                expected_states=UNRESOLVED_STATES,
                            )
                            resolved += 1
                    continue
                source["current_holding"] = current_amount
                source_hash = sha256_json({
                    "contract": HASH_CONTRACT_VERSION,
                    "source": source,
                })
                if (
                    current_reconciliation
                    and current_reconciliation["state"] == "compensation_pending"
                    and current_reconciliation["source_hash"] != source_hash
                ):
                    if self._defer_source_change(
                        current_reconciliation,
                        new_source_hash=source_hash,
                        new_source=source,
                    ):
                        deferred += 1
                    effects.append(current_reconciliation)
                    continue
                effect = self.store.create_version(
                    source=source,
                    source_hash=source_hash,
                    state="pending",
                    mode="apply",
                    effect_kind="broker_cash_reconciliation",
                    event_type="futu_cash_drift",
                )
                if (
                    not current_reconciliation
                    or current_reconciliation["effect_id"] != effect["effect_id"]
                ):
                    created += 1
                effects.append(effect)
        return {
            "success": True,
            "account": account,
            "created": created,
            "resolved": resolved,
            "deferred_for_compensation": deferred,
            "suppressed_by_cash_flow": suppressed,
            "effects": effects,
        }

    def _fresh_holding(self, source: Dict[str, Any]) -> Optional[Holding]:
        asset_id = CASH_ASSETS[source["currency"]]
        return self.storage.get_holding_fresh(
            asset_id,
            source["account"],
            source["broker"],
        )

    @staticmethod
    def _source_identity(source: Dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(source.get("account") or ""),
            str(source.get("broker") or ""),
            str(source.get("currency") or "").upper(),
        )

    def _cash_flow_operations(
        self,
        effect: Dict[str, Any],
        source: Optional[Dict[str, Any]] = None,
    ) -> list[tuple[Dict[str, Any], Decimal]]:
        """Return deterministic deltas for every identity touched by a version."""
        current_source = dict(source or effect.get("source") or {})
        current_amount = self._decimal(current_source.get("signed_amount") or "0")
        previous = self.store.get_previous_applied(effect["effect_id"])
        if not previous:
            return [(current_source, current_amount)]

        previous_source = dict(previous.get("source") or {})
        previous_amount = self._decimal(
            previous_source.get("signed_amount") or "0"
        )
        if self._source_identity(previous_source) == self._source_identity(
            current_source
        ):
            return [(current_source, current_amount - previous_amount)]

        operations = [
            (previous_source, -previous_amount),
            (current_source, current_amount),
        ]
        return [
            (operation_source, delta)
            for operation_source, delta in operations
            if delta != 0
        ]

    def _effect_accounts(self, effect: Dict[str, Any]) -> set[str]:
        accounts = {
            str((effect.get("source") or {}).get("account") or ""),
            str(effect.get("account") or ""),
        }
        if effect.get("effect_kind") == "cash_flow":
            previous = self.store.get_previous_applied(effect["effect_id"])
            if previous:
                accounts.add(
                    str((previous.get("source") or {}).get("account") or "")
                )
            if effect.get("state") == "compensation_pending":
                accounts.add(
                    self._current_flow_accounts().get(
                        str(effect.get("record_id") or ""),
                        "",
                    )
                )
        for target in effect.get("targets") or []:
            if isinstance(target, dict):
                accounts.add(str(target.get("account") or ""))
        return {account for account in accounts if account}

    @staticmethod
    def _is_nav_blocker(effect: Dict[str, Any], nav_date: date) -> bool:
        state = str(effect["state"])
        if state in TERMINAL_STATES or state.startswith("superseded"):
            return False
        flow_date = date.fromisoformat(str(effect["flow_date"]))
        return not (state == "scheduled" and nav_date < flow_date)

    def _blockers_for_account(
        self,
        *,
        account: str,
        nav_date: date,
    ) -> list[Dict[str, Any]]:
        return [
            effect
            for effect in self.store.list_effects(latest_only=True)
            if account in self._effect_accounts(effect)
            and self._is_nav_blocker(effect, nav_date)
        ]

    def _futu_target(
        self,
        source: Dict[str, Any],
        *,
        snapshot_cache: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, Dict[str, Any]]:
        account = source["account"]
        snapshot = (
            snapshot_cache.get(account)
            if snapshot_cache is not None
            else None
        )
        if snapshot is None:
            provider = self.futu_provider_factory(account)
            snapshot = provider.fetch_balances()
            if snapshot_cache is not None:
                snapshot_cache[account] = snapshot
        balances = dict(snapshot.cash_by_currency or {})
        currency = source["currency"]
        if currency not in balances or balances[currency] is None:
            raise RuntimeError(
                f"Futu OpenD response lacks authoritative {currency} cash"
            )
        if snapshot.account_id is None:
            raise RuntimeError("Futu observation lacks account_id evidence")
        profile_fingerprint = snapshot.profile_fingerprint
        if not profile_fingerprint:
            raise RuntimeError("Futu observation lacks profile fingerprint")
        return self._money(balances[currency]), {
            "source": snapshot.source,
            "account_id": snapshot.account_id,
            "profile_fingerprint": profile_fingerprint,
            "cash_by_currency": {
                key: self._money(value) if value is not None else None
                for key, value in balances.items()
            },
        }

    def _target_holding(
        self,
        *,
        before: Optional[Holding],
        source: Dict[str, Any],
        target_amount: str,
    ) -> Holding:
        if before:
            target = Holding(**before.model_dump())
            target.quantity = float(target_amount)
            return target
        currency = source["currency"]
        return Holding(
            asset_id=CASH_ASSETS[currency],
            asset_name=CASH_NAMES[currency],
            asset_type=AssetType.CASH,
            account=source["account"],
            broker=source["broker"],
            quantity=float(target_amount),
            currency=currency,
            asset_class=AssetClass.CASH,
            industry="现金",
        )

    def _build_preview(
        self,
        effect: Dict[str, Any],
        *,
        external_action: Optional[str] = None,
        historical_apply: bool = False,
    ) -> Dict[str, Any]:
        if effect["state"] == "scheduled":
            raise ValueError("scheduled effect cannot be previewed")
        if effect["state"] not in {"pending", "blocked", "previewed", "stale"}:
            raise ValueError(f"effect cannot be previewed from state={effect['state']}")
        source = dict(effect.get("source") or {})
        if effect["mode"] == "record_only" and not historical_apply:
            payload = {
                "hash_contract_version": HASH_CONTRACT_VERSION,
                "effect_id": effect["effect_id"],
                "version": effect["version"],
                "source_hash": effect["source_hash"],
                "mode": "record_only",
                "targets": [],
            }
            return {
                "effect_id": effect["effect_id"],
                "mode": "record_only",
                "before": None,
                "targets": [],
                "target_source": "record_only",
                "warnings": [],
                "preview_hash": sha256_json(payload),
                "hash_payload": payload,
            }

        warnings: list[str] = []
        snapshot_cache: Dict[str, Any] = {}
        operations: list[tuple[Dict[str, Any], Optional[Decimal]]]
        if effect["effect_kind"] == "cash_flow":
            operations = [
                (operation_source, delta)
                for operation_source, delta in self._cash_flow_operations(
                    effect,
                    source,
                )
            ]
        else:
            operations = [(source, None)]

        target_rows: list[Dict[str, Any]] = []
        for operation_source, delta in sorted(
            operations,
            key=lambda item: self._source_identity(item[0]),
        ):
            currency = str(operation_source.get("currency") or "").upper()
            if currency not in CASH_ASSETS:
                raise ValueError(f"unsupported target currency: {currency}")
            if not operation_source.get("account"):
                raise ValueError("target account is required")
            if not operation_source.get("broker"):
                raise ValueError("target broker is required")

            before = self._fresh_holding(operation_source)
            before_payload = self._holding_payload(before)
            evidence: Optional[Dict[str, Any]] = None

            if effect["effect_kind"] == "broker_cash_reconciliation":
                target_amount, evidence = self._futu_target(
                    operation_source,
                    snapshot_cache=snapshot_cache,
                )
                row_target_source = "futu_opend_currency_cash"
            elif effect["effect_kind"] == "cash_holding_external_change":
                if operation_source["broker"] == FUTU_BROKER:
                    target_amount, evidence = self._futu_target(
                        operation_source,
                        snapshot_cache=snapshot_cache,
                    )
                    row_target_source = "futu_opend_currency_cash"
                elif external_action == "accept_current":
                    payload = {
                        "hash_contract_version": HASH_CONTRACT_VERSION,
                        "effect_id": effect["effect_id"],
                        "version": effect["version"],
                        "source_hash": effect["source_hash"],
                        "mode": "record_only",
                        "external_action": external_action,
                        "before": before_payload,
                        "befores": [before_payload],
                        "targets": [],
                    }
                    return {
                        "effect_id": effect["effect_id"],
                        "mode": "record_only",
                        "external_action": external_action,
                        "before": before_payload,
                        "befores": [before_payload],
                        "targets": [],
                        "target_source": "accepted_external_baseline",
                        "target_sources": [],
                        "warnings": [],
                        "preview_hash": sha256_json(payload),
                        "hash_payload": payload,
                    }
                elif external_action == "restore":
                    if source.get("last_confirmed_amount") is None:
                        raise ValueError(
                            "external CASH change has no confirmed baseline to restore"
                        )
                    target_amount = self._money(source["last_confirmed_amount"])
                    row_target_source = "last_confirmed_baseline"
                else:
                    raise ValueError(
                        "non-Futu external CASH change requires "
                        "external_action=accept_current or restore"
                    )
            elif operation_source["broker"] == FUTU_BROKER:
                target_amount, evidence = self._futu_target(
                    operation_source,
                    snapshot_cache=snapshot_cache,
                )
                expected = self._decimal(before_payload["quantity"]) + (
                    delta or Decimal("0")
                )
                variance = self._decimal(target_amount) - expected
                if variance != 0:
                    warnings.append(
                        "Futu OpenD absolute cash differs from fresh holding plus "
                        f"event for {self._source_identity(operation_source)}: "
                        f"variance={self._money(variance)}"
                    )
                row_target_source = "futu_opend_currency_cash"
            else:
                target_decimal = self._decimal(before_payload["quantity"]) + (
                    delta or Decimal("0")
                )
                if target_decimal < 0:
                    raise ValueError(
                        "estimated non-Futu cash target cannot be negative for "
                        f"{self._source_identity(operation_source)}: "
                        f"{self._money(target_decimal)}"
                    )
                target_amount = self._money(target_decimal)
                row_target_source = "estimated_current_plus_event"
                warnings.append(
                    "target is estimated from fresh holding plus event for "
                    f"{self._source_identity(operation_source)}"
                )

            target_holding = self._target_holding(
                before=before,
                source=operation_source,
                target_amount=target_amount,
            )
            target_payload = CompensationService.serialize_holding(target_holding)
            if target_payload is None:
                raise RuntimeError("target holding serialization returned no target")
            target_rows.append({
                "identity": self._identity_payload(
                    target_holding.asset_id,
                    target_holding.account,
                    target_holding.broker,
                ),
                "before": before_payload,
                "target": target_payload,
                "delta": self._money(delta or 0),
                "target_source": row_target_source,
                "futu_evidence": evidence,
            })

        if not target_rows:
            raise RuntimeError("apply effect produced no holding targets")
        target_sources = [row["target_source"] for row in target_rows]
        target_source = (
            target_sources[0]
            if len(set(target_sources)) == 1
            else "mixed_futu_exact_and_estimated"
        )
        befores = [row["before"] for row in target_rows]
        targets = [row["target"] for row in target_rows]
        evidence_rows = [
            {
                "identity": row["identity"],
                "evidence": row["futu_evidence"],
            }
            for row in target_rows
            if row["futu_evidence"] is not None
        ]
        futu_evidence: Any = (
            evidence_rows[0]["evidence"]
            if len(evidence_rows) == 1
            else evidence_rows or None
        )
        payload = {
            "hash_contract_version": HASH_CONTRACT_VERSION,
            "effect_id": effect["effect_id"],
            "version": effect["version"],
            "source_hash": effect["source_hash"],
            "mode": "apply",
            "historical_apply": bool(historical_apply),
            "external_action": external_action,
            "before": befores[0],
            "befores": befores,
            "targets": targets,
            "target_source": target_source,
            "target_rows": target_rows,
        }
        return {
            "effect_id": effect["effect_id"],
            "mode": "apply",
            "external_action": external_action,
            "before": befores[0],
            "befores": befores,
            "targets": targets,
            "target_source": target_source,
            "target_sources": target_sources,
            "target_rows": target_rows,
            "warnings": warnings,
            "futu_evidence": futu_evidence,
            "preview_hash": sha256_json(payload),
            "hash_payload": payload,
        }

    def preview(
        self,
        effect_id: str,
        *,
        external_action: Optional[str] = None,
        historical_apply: bool = False,
    ) -> Dict[str, Any]:
        effect = self.store.get_effect(effect_id)
        if not effect:
            raise KeyError(f"cash-flow effect not found: {effect_id}")
        latest = self.store.get_latest_for_record(
            effect["record_id"],
            effect_kind=effect["effect_kind"],
        )
        if not latest or latest["effect_id"] != effect_id:
            raise ValueError("only the latest effect version can be previewed")
        _, _, _, correction_id = self._current_cash_flow_source(effect)
        if correction_id:
            raise ValueError(
                "cash_flow source changed; use latest correction effect: "
                f"{correction_id}"
            )
        try:
            preview = self._build_preview(
                effect,
                external_action=external_action,
                historical_apply=historical_apply,
            )
        except Exception as exc:
            if effect["state"] in {"pending", "previewed", "stale", "blocked"}:
                self.store.update_effect(
                    effect_id,
                    state="blocked",
                    fields={"last_error": str(exc)},
                    event_type="preview_blocked",
                    event_payload={"error": str(exc)},
                    expected_states={"pending", "previewed", "stale", "blocked"},
                )
            raise
        self.store.update_effect(
            effect_id,
            state="previewed",
            fields={
                "target_source": preview["target_source"],
                "before_json": preview.get("befores") or preview["before"],
                "targets_json": preview["targets"],
                "preview_hash": preview["preview_hash"],
                "warnings_json": preview["warnings"],
                "last_error": None,
            },
            event_type="previewed",
            event_payload={
                "preview_hash": preview["preview_hash"],
                "external_action": external_action,
                "historical_apply": historical_apply,
            },
            expected_states={"pending", "blocked", "previewed", "stale"},
        )
        return preview

    def confirm(
        self,
        effect_id: str,
        *,
        preview_hash: str,
        confirm: bool,
        external_action: Optional[str] = None,
        historical_apply: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("cash-flow effect write requires confirm=True")
        with process_lock("cash-flow-effects:scanner"):
            initial = self.store.get_effect(effect_id)
            if not initial:
                raise KeyError(f"cash-flow effect not found: {effect_id}")
            locked_accounts = sorted(self._effect_accounts(initial))
            with ExitStack() as lock_stack:
                for account in locked_accounts:
                    lock_stack.enter_context(process_lock(account_lock_key(account)))
                lock_stack.enter_context(
                    process_lock(f"cash-flow-effect:{effect_id}")
                )
                return self._confirm_under_locks(
                    effect_id=effect_id,
                    preview_hash=preview_hash,
                    external_action=external_action,
                    historical_apply=historical_apply,
                    run_id=run_id,
                    locked_accounts=set(locked_accounts),
                )

    def _confirm_under_locks(
        self,
        *,
        effect_id: str,
        preview_hash: str,
        external_action: Optional[str],
        historical_apply: bool,
        run_id: Optional[str],
        locked_accounts: set[str],
    ) -> Dict[str, Any]:
        effect = self.store.get_effect(effect_id)
        if not effect:
            raise KeyError(f"cash-flow effect not found: {effect_id}")
        latest = self.store.get_latest_for_record(
            effect["record_id"],
            effect_kind=effect["effect_kind"],
        )
        if not latest or latest["effect_id"] != effect_id:
            raise ValueError("only the latest effect version can be confirmed")
        _, _, _, correction_id = self._current_cash_flow_source(effect)
        if correction_id:
            raise ValueError(
                "cash_flow source changed; use latest correction effect: "
                f"{correction_id}"
            )
        recomputed = self._build_preview(
            effect,
            external_action=external_action,
            historical_apply=historical_apply,
        )
        if recomputed["preview_hash"] != preview_hash:
            self.store.update_effect(
                effect_id,
                state="stale",
                event_type="preview_invalidated",
                event_payload={
                    "provided": preview_hash,
                    "current": recomputed["preview_hash"],
                },
                expected_states={"previewed", "pending", "blocked", "stale"},
            )
            self.store.enqueue_receipt(
                receipt_key=(
                    f"effect:{effect_id}:stale:"
                    f"{sha256_json({'provided': preview_hash, 'current': recomputed['preview_hash']})}"
                ),
                receipt_type="stale",
                effect_id=effect_id,
                payload={
                    "effect_id": effect_id,
                    "account": effect["account"],
                    "state": "stale",
                    "provided_preview_hash": preview_hash,
                    "current_preview_hash": recomputed["preview_hash"],
                },
            )
            raise ValueError("preview hash is stale; review and confirm again")

        confirmation = self._operator_context(run_id)
        account = effect["account"]
        if recomputed["mode"] == "record_only":
            if effect["effect_kind"] == "cash_holding_external_change":
                source = effect["source"]
                current = self._fresh_holding(source)
                identity = source["holding_identity"]
                self.store.confirm_fingerprint(
                    holding_identity=identity,
                    holding_record_id=current.record_id if current else None,
                    amount=self._money(current.quantity if current else 0),
                    confirmation_hash=self._holding_hash(current),
                    effect_id=effect_id,
                )
            result = self.store.update_effect(
                effect_id,
                state="record_only",
                fields={"confirmation_json": confirmation},
                event_type="record_only_confirmed",
                event_payload={"preview_hash": preview_hash},
                expected_states={"previewed"},
            )
            self.store.enqueue_receipt(
                receipt_key=f"effect:{effect_id}:record_only:{preview_hash}",
                receipt_type="record_only",
                effect_id=effect_id,
                payload={
                    "effect_id": effect_id,
                    "account": account,
                    "state": "record_only",
                    "run_id": confirmation["run_id"],
                },
            )
            return {"success": True, "effect": result, "already_applied": False}

        target_accounts = {
            str(target.get("account") or "")
            for target in recomputed["targets"]
        }
        if not target_accounts.issubset(locked_accounts):
            raise RuntimeError(
                "confirmed target accounts changed outside the locked set; "
                "preview again"
            )

        targets: list[Holding] = []
        befores: list[Optional[Holding]] = []
        compensation_targets: list[Dict[str, Any]] = []
        for target_data in recomputed["targets"]:
            target = Holding(**target_data)
            target_source = {
                "account": target.account,
                "broker": target.broker,
                "currency": target.currency,
            }
            before = self._fresh_holding(target_source)
            targets.append(target)
            befores.append(before)
            compensation_targets.append({
                "type": "CASH_TARGET_SET",
                "identity": self._identity_payload(
                    target.asset_id,
                    target.account,
                    target.broker,
                ),
                "before": CompensationService.serialize_holding(before),
                "target": target_data,
            })

        self.store.update_effect(
            effect_id,
            state="applying",
            fields={"confirmation_json": confirmation},
            event_type="applying",
            event_payload={
                "preview_hash": preview_hash,
                "target_count": len(targets),
                "accounts": sorted(target_accounts),
            },
            expected_states={"previewed"},
        )
        already_applied = True
        readbacks: list[Optional[Holding]] = []
        try:
            for target, target_data, before in zip(
                targets,
                recomputed["targets"],
                befores,
            ):
                current_payload = CompensationService.serialize_holding(before)
                if current_payload != target_data:
                    self.storage.replace_holding(target)
                    already_applied = False
                target_source = {
                    "account": target.account,
                    "broker": target.broker,
                    "currency": target.currency,
                }
                readback = self._fresh_holding(target_source)
                readbacks.append(readback)
                if CompensationService.serialize_holding(readback) != target_data:
                    raise RuntimeError(
                        "holding fresh readback does not match confirmed target: "
                        f"{self._identity(target.asset_id, target.account, target.broker)}"
                    )
        except Exception as exc:
            task = self.compensation.record(
                operation_type="CASH_FLOW_EFFECT_TARGETS_INCOMPLETE",
                account=account,
                related_record_id=effect_id,
                payload={"targets": compensation_targets},
                error=exc,
            )
            failed = self.store.update_effect(
                effect_id,
                state="compensation_pending",
                fields={
                    "compensation_task_id": task.task_id,
                    "last_error": str(exc),
                },
                event_type="compensation_created",
                event_payload={
                    "task_id": task.task_id,
                    "error": str(exc),
                    "target_count": len(compensation_targets),
                },
                expected_states={"applying"},
            )
            self.store.enqueue_receipt(
                receipt_key=f"effect:{effect_id}:compensation:{task.task_id}",
                receipt_type="compensation_pending",
                effect_id=effect_id,
                payload={
                    "effect_id": effect_id,
                    "account": account,
                    "state": "compensation_pending",
                    "task_id": task.task_id,
                    "error": str(exc),
                },
            )
            return {
                "success": False,
                "status": "compensation_pending",
                "effect": failed,
                "task_id": task.task_id,
                "error": str(exc),
            }

        for target, readback in zip(targets, readbacks):
            self.store.confirm_fingerprint(
                holding_identity=self._identity(
                    target.asset_id,
                    target.account,
                    target.broker,
                ),
                holding_record_id=readback.record_id if readback else None,
                amount=self._money(readback.quantity if readback else 0),
                confirmation_hash=self._holding_hash(readback),
                effect_id=effect_id,
            )
        applied = self.store.update_effect(
            effect_id,
            state="applied",
            fields={"last_error": None},
            event_type="applied",
            event_payload={
                "preview_hash": preview_hash,
                "already_applied": already_applied,
                "target_count": len(targets),
            },
            expected_states={"applying"},
        )
        _, _, _, correction_id = self._current_cash_flow_source(applied)
        readback_payloads = [
            self._holding_payload(readback) for readback in readbacks
        ]
        if correction_id:
            self.store.enqueue_receipt(
                receipt_key=f"effect:{effect_id}:source_changed:{correction_id}",
                receipt_type="stale",
                effect_id=effect_id,
                payload={
                    "effect_id": effect_id,
                    "account": account,
                    "state": "correction_required",
                    "correction_effect_id": correction_id,
                    "run_id": confirmation["run_id"],
                },
            )
            return {
                "success": False,
                "status": "correction_required",
                "effect": applied,
                "correction_effect_id": correction_id,
                "readback": readback_payloads[0],
                "readbacks": readback_payloads,
            }
        source = effect["source"]
        self.store.enqueue_receipt(
            receipt_key=f"effect:{effect_id}:applied:{preview_hash}",
            receipt_type="applied",
            effect_id=effect_id,
            payload={
                "effect_id": effect_id,
                "account": account,
                "broker": source["broker"],
                "flow_date": source["flow_date"],
                "currency": source["currency"],
                "signed_amount": source["signed_amount"],
                "before": recomputed["before"],
                "befores": recomputed.get("befores") or [recomputed["before"]],
                "target_source": recomputed["target_source"],
                "targets": recomputed["targets"],
                "warnings": recomputed["warnings"],
                "run_id": confirmation["run_id"],
            },
        )
        return {
            "success": True,
            "effect": applied,
            "already_applied": already_applied,
            "readback": readback_payloads[0],
            "readbacks": readback_payloads,
        }

    def record_only(self, effect_id: str, *, confirm: bool) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("record-only requires confirm=True")
        preview = self.preview(effect_id)
        if preview["mode"] != "record_only":
            raise ValueError("effect is not eligible for record-only")
        return self.confirm(
            effect_id,
            preview_hash=preview["preview_hash"],
            confirm=True,
        )

    def _finalize_compensation_if_resolved(
        self,
        effect: Dict[str, Any],
    ) -> Dict[str, Any]:
        task_id = effect.get("compensation_task_id")
        if not task_id:
            raise RuntimeError("compensation_pending effect lacks task id")
        task = self.compensation.get_task(task_id)
        if not task or task.get("status") != "RESOLVED":
            return effect
        targets = list((task.get("payload") or {}).get("targets") or [])
        for target_spec in targets:
            target_data = target_spec.get("target")
            if not isinstance(target_data, dict):
                raise RuntimeError("resolved compensation target is invalid")
            target = Holding(**target_data)
            source = {
                "account": target.account,
                "broker": target.broker,
                "currency": target.currency,
            }
            readback = self._fresh_holding(source)
            if CompensationService.serialize_holding(readback) != target_data:
                raise RuntimeError(
                    "resolved compensation fresh readback does not match target"
                )
            self.store.confirm_fingerprint(
                holding_identity=self._identity(
                    target.asset_id,
                    target.account,
                    target.broker,
                ),
                holding_record_id=readback.record_id if readback else None,
                amount=self._money(readback.quantity if readback else 0),
                confirmation_hash=self._holding_hash(readback),
                effect_id=effect["effect_id"],
            )
        return self.store.update_effect(
            effect["effect_id"],
            state="applied",
            fields={"last_error": None},
            event_type="compensation_resolved",
            event_payload={"task_id": task_id},
            expected_states={"compensation_pending"},
        )

    def retry(self, effect_id: str, *, confirm: bool) -> Dict[str, Any]:
        if not confirm:
            raise ValueError("cash-flow effect retry requires confirm=True")
        effect = self.store.get_effect(effect_id)
        if not effect:
            raise KeyError(f"cash-flow effect not found: {effect_id}")
        if effect["state"] != "compensation_pending":
            raise ValueError("effect is not compensation_pending")
        task_id = effect.get("compensation_task_id")
        result = self.compensation.retry(str(task_id), confirm=True)
        if not result.get("success"):
            self.store.update_effect(
                effect_id,
                fields={"last_error": result.get("error")},
                event_type="compensation_retry_failed",
                event_payload=result,
                expected_states={"compensation_pending"},
            )
            return {"success": False, "effect_id": effect_id, "compensation": result}
        finalized = self._finalize_compensation_if_resolved(
            self.store.get_effect(effect_id) or effect
        )
        self.store.enqueue_receipt(
            receipt_key=f"effect:{effect_id}:compensation_resolved:{task_id}",
            receipt_type="applied",
            effect_id=effect_id,
            payload={
                "effect_id": effect_id,
                "account": effect["account"],
                "state": "applied",
                "compensation_task_id": task_id,
            },
        )
        _, _, _, correction_id = self._current_cash_flow_source(finalized)
        if correction_id:
            self.store.enqueue_receipt(
                receipt_key=f"effect:{effect_id}:source_changed:{correction_id}",
                receipt_type="stale",
                effect_id=effect_id,
                payload={
                    "effect_id": effect_id,
                    "account": effect["account"],
                    "state": "correction_required",
                    "correction_effect_id": correction_id,
                },
            )
            return {
                "success": False,
                "status": "correction_required",
                "effect": finalized,
                "correction_effect_id": correction_id,
                "compensation": result,
            }
        return {"success": True, "effect": finalized, "compensation": result}

    def audit(self, *, account: Optional[str] = None) -> Dict[str, Any]:
        finalized: list[str] = []
        corrections: list[str] = []
        errors: list[Dict[str, str]] = []
        for effect in self.list_effects(account=account, latest_only=True):
            if effect["state"] != "compensation_pending":
                continue
            try:
                updated = self._finalize_compensation_if_resolved(effect)
                if updated["state"] == "applied":
                    finalized.append(effect["effect_id"])
                    _, _, _, correction_id = self._current_cash_flow_source(
                        updated
                    )
                    if correction_id:
                        corrections.append(correction_id)
            except Exception as exc:
                errors.append({
                    "effect_id": effect["effect_id"],
                    "error": str(exc),
                })
        blockers = (
            self._blockers_for_account(account=account, nav_date=bj_today())
            if account
            else [
                effect
                for effect in self.store.list_effects(latest_only=True)
                if effect["state"] in UNRESOLVED_STATES
            ]
        )
        return {
            "success": not errors,
            "account": account,
            "finalized_compensations": finalized,
            "correction_effects": corrections,
            "errors": errors,
            "blocker_count": len(blockers),
            "blockers": blockers,
            "integrity": self.store.integrity_check(),
        }

    def nav_gate(self, *, account: str, nav_date: date) -> Dict[str, Any]:
        scan = self.scan(account=account, enqueue_receipts=False)
        blockers = self._blockers_for_account(
            account=account,
            nav_date=nav_date,
        )
        return {
            "success": not blockers,
            "account": account,
            "nav_date": nav_date.isoformat(),
            "scan_run_id": scan["scan_run"]["scan_run_id"],
            "blocker_count": len(blockers),
            "blockers": [
                {
                    "effect_id": item["effect_id"],
                    "effect_kind": item["effect_kind"],
                    "state": item["state"],
                    "record_id": item["record_id"],
                    "flow_date": item["flow_date"],
                    "last_error": item.get("last_error"),
                }
                for item in blockers
            ],
        }
