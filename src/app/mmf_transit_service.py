"""Operator-confirmed App receipt intake and OpenD settlement reconciliation."""
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal

from src.time_utils import TZ_SHANGHAI

from src.domain.mmf_transit import ConfirmedMmfTransfer, MmfSettlementFlow, TransferDirection

from .futu_balance_sync_service import FutuOpenApiBalanceProvider
from .futu_sync_evidence import FutuSyncEvidenceStore
from .operation_state import OperationStateStore


def current_mmf_balance(account):
    provider = FutuOpenApiBalanceProvider.from_account(account)
    receipt = FutuSyncEvidenceStore().latest(account) or {}
    metadata = receipt.get("source_metadata") or {}
    cash_write = receipt.get("cash_write") or {}
    snapshot = receipt.get("source_snapshot_id")
    if (
        not receipt.get("success") or receipt.get("account") != account
        or not snapshot or cash_write.get("snapshot_id") != snapshot
        or cash_write.get("status") != "verified"
        or metadata.get("account_fingerprint") != provider.account_fingerprint
        or metadata.get("trd_env") != provider.trd_env
    ):
        raise ValueError("A current verified Futu balance receipt for this account is required")
    return provider, snapshot


def _balance_date(account, snapshot):
    cash = (FutuSyncEvidenceStore().latest(account) or {}).get("cash_write") or {}
    if cash.get("snapshot_id") != snapshot:
        raise ValueError("The verified balance snapshot changed")
    observed = datetime.fromisoformat(cash["observed_at_utc"])
    if observed.tzinfo is None:
        raise ValueError("Verified balance requires an observation timestamp")
    return observed.astimezone(TZ_SHANGHAI).date()


def record_app_receipt(
    account, *, snapshot_id, receipt_id, direction, currency, amount,
    source_debit, target_credit, request_date, cancelled=False, confirm=False,
):
    provider, snapshot = current_mmf_balance(account)
    if snapshot_id != snapshot:
        raise ValueError("Selected balance snapshot is no longer current")
    requested = date.fromisoformat(request_date)
    if requested > min(_balance_date(account, snapshot), datetime.now(TZ_SHANGHAI).date()):
        raise ValueError("App request date is later than the confirmed balance")
    transfer = ConfirmedMmfTransfer(
        account=account, broker_account=provider.account_fingerprint,
        receipt_id=receipt_id, balance_snapshot_id=snapshot,
        direction=TransferDirection(direction), currency=currency,
        amount=Decimal(amount), source_debit=Decimal(source_debit),
        target_credit=Decimal(target_credit),
        request_date=requested, cancelled=cancelled,
    )
    if confirm:
        from src.process_lock import account_lock_key, process_lock

        with process_lock(account_lock_key(account)):
            if current_mmf_balance(account)[1] != snapshot:
                raise ValueError("Balance snapshot changed before confirmation")
            store = OperationStateStore()
            current = store.read_mmf_transfers(
                account=account, broker_account=transfer.broker_account,
                balance_snapshot_id=snapshot,
            )
            if transfer not in current:
                _invalidate_publication(account)
                store.record_mmf_transfer(transfer)
    return {
        "success": True, "recorded": confirm, "receipt_id": receipt_id,
        "balance_snapshot_id": snapshot, "currency": currency,
        "in_transit": str(transfer.in_transit),
    }


def reconcile_app_receipt(
    account, *, snapshot_id, receipt_id, clearing_date, flow_ids, confirm=False,
):
    day = date.fromisoformat(clearing_date).isoformat()
    provider, snapshot = current_mmf_balance(account)
    if snapshot_id != snapshot:
        raise ValueError("Selected balance snapshot is no longer current")
    if not confirm and not OperationStateStore.resolve_db_path_read_only().is_file():
        raise ValueError("App receipt is unconfirmed for the selected balance")
    store = OperationStateStore(read_only=not confirm)
    transfers = store.read_mmf_transfers(
        account=account, broker_account=provider.account_fingerprint,
        balance_snapshot_id=snapshot,
    )
    transfer = next((item for item in transfers if item.receipt_id == receipt_id), None)
    if transfer is None:
        raise ValueError("App receipt is unconfirmed for the selected balance")
    if transfer.request_date is None or transfer.cancelled:
        raise ValueError("A dated, non-cancelled App receipt is required")
    balance_day = _balance_date(account, snapshot)
    if not transfer.request_date.isoformat() <= day <= balance_day.isoformat():
        raise ValueError("Clearing date is outside the App request and confirmed balance window")
    eligible = []
    for row in provider.fetch_mmf_cash_flows(day):
        flow_id = str(row.get("cashflow_id") or "").strip()
        try:
            settlement = date.fromisoformat(str(row["settlement_date"]))
        except (ValueError, KeyError):
            continue
        if (
            not flow_id or row.get("clearing_date") != day
            or not date.fromisoformat(day) <= settlement <= balance_day
        ):
            continue
        direction = (
            TransferDirection.REDEMPTION if str(row.get("cashflow_direction")).upper() == "IN"
            else TransferDirection.SUBSCRIPTION
        )
        currency = str(row.get("currency") or "").upper()
        if currency == "CNH":
            currency = "CNY"
        if direction != transfer.direction or currency != transfer.currency:
            continue
        amount = Decimal(str(row["cashflow_amount"]))
        if (direction == TransferDirection.REDEMPTION and amount <= 0) or (
            direction == TransferDirection.SUBSCRIPTION and amount >= 0
        ):
            continue
        eligible.append(MmfSettlementFlow(
            flow_id, provider.account_fingerprint, direction, currency, abs(amount),
        ))
    selected = set(flow_ids)
    flows = [flow for flow in eligible if flow.flow_id in selected]
    if selected != {flow.flow_id for flow in flows}:
        raise ValueError("A selected flow is absent or does not match the transfer scope")
    if confirm and not flows:
        raise ValueError("Select confirmed matching OpenD flow IDs before reconciliation")
    if confirm:
        from src.process_lock import account_lock_key, process_lock

        with process_lock(account_lock_key(account)):
            if current_mmf_balance(account)[1] != snapshot:
                raise ValueError("Balance snapshot changed before confirmation")
            reconciled = store.reconcile_mmf_transfer(transfer, flows, confirm=True)
    else:
        reconciled = store.reconcile_mmf_transfer(transfer, flows)
    return {
        "success": True, "recorded": confirm, "receipt_id": receipt_id,
        "balance_snapshot_id": snapshot, "currency": transfer.currency,
        "eligible_flows": [{"id": flow.flow_id, "amount": str(flow.amount)} for flow in eligible],
        "reconciled_amount": str(reconciled),
        "closed": confirm and reconciled == transfer.amount,
    }


def read_mmf_funding(account):
    """Expose confirmed adjustments together with their original balance."""
    import sqlite3

    from src.domain.mmf_transit import adjustments_by_currency

    provider = FutuOpenApiBalanceProvider.from_account(account)
    path = OperationStateStore.resolve_db_path_read_only()
    rows = []
    if path.is_file():
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='futu_mmf_transfer_observations'"
            ).fetchone():
                rows = conn.execute(
                    """
                    SELECT o.* FROM futu_mmf_transfer_observations o
                    WHERE account = ? AND broker_account = ?
                      AND rowid = (SELECT MAX(p.rowid) FROM futu_mmf_transfer_observations p
                                   WHERE p.account=o.account AND p.broker_account=o.broker_account
                                     AND p.receipt_id=o.receipt_id)
                    """,
                    (account, provider.account_fingerprint),
                ).fetchall()
    active = [
        ConfirmedMmfTransfer(
            account=row["account"], broker_account=row["broker_account"],
            receipt_id=row["receipt_id"], balance_snapshot_id=row["balance_snapshot_id"],
            direction=TransferDirection(row["direction"]), currency=row["currency"],
            amount=Decimal(row["amount"]), source_debit=Decimal(row["source_debit"]),
            target_credit=Decimal(row["target_credit"]),
        )
        for row in rows
        if Decimal(row["target_credit"]) < Decimal(row["amount"])
        and ("cancelled" not in row.keys() or not row["cancelled"])
    ]
    receipt = FutuSyncEvidenceStore().latest(account) or {}
    cash = receipt.get("cash_write") or {}
    snapshot_id = receipt.get("source_snapshot_id")
    metadata = receipt.get("source_metadata") or {}
    if (
        not receipt.get("success") or receipt.get("account") != account
        or not snapshot_id or cash.get("snapshot_id") != snapshot_id
        or cash.get("status") != "verified"
        or metadata.get("account_fingerprint") != provider.account_fingerprint
        or metadata.get("trd_env") != provider.trd_env
        or any(item.balance_snapshot_id != snapshot_id for item in active)
        or cash.get("fund_assets") is None
    ):
        return {"success": True, "account": account, "status": "unverified"}
    amounts = adjustments_by_currency(
        active, account=account, broker_account=provider.account_fingerprint,
        balance_snapshot_id=snapshot_id,
    )
    return {
        "success": True, "account": account, "status": "verified" if active else "none",
        "account_fingerprint": provider.account_fingerprint, "environment": provider.trd_env,
        "snapshot_id": snapshot_id, "observed_at": cash.get("observed_at_utc"),
        "cash": cash["balances"], "fund_assets_cny": cash["fund_assets"],
        "cash_target_cny": cash.get("target"), "fx": (cash.get("fx") or {}).get("rates"),
        "adjustments": {currency: str(value) for currency, value in amounts.items()},
        "transfers": [
            {
                "receipt_id": item.receipt_id, "direction": item.direction.value,
                "currency": item.currency, "amount": str(item.amount),
                "source_debit": str(item.source_debit), "target_credit": str(item.target_credit),
            }
            for item in sorted(active, key=lambda item: item.receipt_id)
        ],
    }


def mmf_valuation_holdings(holdings, funding, *, account, capture=None):
    """Value one derived CNY row against the verified PM holdings baseline."""
    from src.models import CASH_ASSET_ID, MMF_ASSET_ID, AssetType, Holding

    if funding.get("status") not in {"none", "verified"} or funding.get("account") != account:
        raise ValueError("Money-fund transfer amounts need confirmation for the current balance")
    if funding["status"] == "none":
        if capture is not None and capture.get("snapshot_id") != funding["snapshot_id"]:
            if datetime.fromisoformat(capture["observed_at_utc"]) < datetime.fromisoformat(funding["observed_at"]):
                raise ValueError("Shared zero adjustment is newer than formal NAV capture")
        return list(holdings), {"snapshot_id": funding["snapshot_id"], "amounts": {}, "value_cny": "0"}
    if capture is not None and capture.get("snapshot_id") != funding["snapshot_id"]:
        raise ValueError("Money-fund transfer balance does not match formal NAV capture")
    baseline = list(holdings)
    for asset_id, expected in (
        (CASH_ASSET_ID, funding.get("cash_target_cny")),
        (MMF_ASSET_ID, funding.get("fund_assets_cny")),
    ):
        matches = [
            item for item in baseline
            if item.account == account and item.broker == "富途" and item.asset_id == asset_id
        ]
        if len(matches) != 1 or matches[0].currency != "CNY" or matches[0].quantity != expected:
            raise ValueError("Money-fund transfer balance does not match PM holdings")
    adjustments = funding["adjustments"]
    total = Decimal(0)
    for currency, amount in adjustments.items():
        rate = Decimal(1) if currency == "CNY" else Decimal(
            str((funding.get("fx") or {}).get(f"{currency}CNY", "NaN"))
        )
        if not rate.is_finite() or rate <= 0:
            raise ValueError("Money-fund transfer valuation requires verified FX")
        total += Decimal(amount) * rate
    if total:
        baseline.append(Holding(
            account=account, broker="富途", asset_id="CNY-MMF-TRANSIT",
            asset_name="货基互转在途", asset_type=AssetType.MMF,
            currency="CNY", quantity=float(total),
        ))
    return baseline, {
        "snapshot_id": funding["snapshot_id"],
        "amounts": adjustments,
        "value_cny": str(total),
    }


def read_mmf_valuation_funding(account):
    """Read the shared file independently and bind its evidence to this PM account."""
    from .futu_mmf_shared import mmf_shared_configured, read_mmf_shared

    if not mmf_shared_configured():
        return {"status": "disabled"}
    funding = read_mmf_shared(account)
    if funding["status"] in {"none", "verified"}:
        provider = FutuOpenApiBalanceProvider.from_account(account)
        if (
            funding["account_fingerprint"] != provider.account_fingerprint
            or funding["environment"] != provider.trd_env
        ):
            raise ValueError("Shared money-fund reconciliation account mismatch")
        _, snapshot = current_mmf_balance(account)
        if funding["snapshot_id"] != snapshot:
            if funding["status"] == "verified":
                raise ValueError("Shared money-fund reconciliation is for a previous balance; confirm and publish the current snapshot before resuming NAV")
            current = (FutuSyncEvidenceStore().latest(account) or {}).get("cash_write") or {}
            if (
                current.get("snapshot_id") != snapshot
                or datetime.fromisoformat(current["observed_at_utc"]) < datetime.fromisoformat(funding["observed_at"])
            ):
                raise ValueError("Shared zero adjustment is newer than the current balance")
    return funding


def publish_mmf_funding(account, *, confirm=False):
    """Atomically publish the current account evidence in the shared directory."""
    from src.process_lock import account_lock_key, process_lock

    from .futu_mmf_shared import mmf_shared_path, read_mmf_shared

    path = mmf_shared_path(account)

    def payload():
        return {"schema": "futu.mmf-funding.v1", **read_mmf_funding(account)}

    if not confirm:
        return {"success": True, "published": False, "path": str(path), "evidence": payload()}
    with process_lock(account_lock_key(account), blocking=False):
        evidence = payload()
        _write_publication(path, evidence)
        if read_mmf_shared(account) != evidence:
            raise ValueError("Shared money-fund reconciliation readback did not match")
        return {
            "success": True, "published": True, "path": str(path), "status": evidence["status"],
        }



def _invalidate_publication(account):
    from .futu_mmf_shared import mmf_shared_configured, mmf_shared_path

    if mmf_shared_configured():
        _write_publication(mmf_shared_path(account), {
            "schema": "futu.mmf-funding.v1", "success": True,
            "account": account, "status": "unverified",
        })


def _write_publication(path, evidence):
    import json
    import os
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    raw = json.dumps(evidence, ensure_ascii=False).encode()
    if len(raw) > 262144:
        raise ValueError("Shared money-fund reconciliation file is too large")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=".mmf-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
