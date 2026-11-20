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
    ):
        raise ValueError("A current verified Futu balance receipt for this account is required")
    return provider, snapshot


def record_app_receipt(
    account, *, snapshot_id, receipt_id, direction, currency, amount,
    source_debit, target_credit, confirm=False,
):
    provider, snapshot = current_mmf_balance(account)
    if snapshot_id != snapshot:
        raise ValueError("Selected balance snapshot is no longer current")
    transfer = ConfirmedMmfTransfer(
        account=account, broker_account=provider.account_fingerprint,
        receipt_id=receipt_id, balance_snapshot_id=snapshot,
        direction=TransferDirection(direction), currency=currency,
        amount=Decimal(amount), source_debit=Decimal(source_debit),
        target_credit=Decimal(target_credit),
    )
    if confirm:
        OperationStateStore().record_mmf_transfer(transfer)
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
    store = OperationStateStore()
    transfers = store.read_mmf_transfers(
        account=account, broker_account=provider.account_fingerprint,
        balance_snapshot_id=snapshot,
    )
    transfer = next((item for item in transfers if item.receipt_id == receipt_id), None)
    if transfer is None:
        raise ValueError("App receipt is unconfirmed for the selected balance")
    eligible = []
    for row in provider.fetch_mmf_cash_flows(day):
        flow_id = str(row.get("cashflow_id") or "").strip()
        settlement = row.get("settlement_date")
        if not flow_id or not settlement or date.fromisoformat(str(settlement)) > datetime.now(TZ_SHANGHAI).date():
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
    if confirm and current_mmf_balance(account)[1] != snapshot:
        raise ValueError("Balance snapshot changed before confirmation")
    reconciled = store.reconcile_mmf_transfer(transfer, flows, confirm=confirm)
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
    if not path.is_file():
        return {"success": True, "account": account, "status": "none"}
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='futu_mmf_transfer_observations'"
        ).fetchone()
        if not exists:
            return {"success": True, "account": account, "status": "none"}
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
    ]
    if not active:
        return {"success": True, "account": account, "status": "none"}
    receipt = FutuSyncEvidenceStore().latest(account) or {}
    cash = receipt.get("cash_write") or {}
    snapshot_id = receipt.get("source_snapshot_id")
    metadata = receipt.get("source_metadata") or {}
    if (
        not receipt.get("success") or receipt.get("account") != account
        or not snapshot_id or cash.get("snapshot_id") != snapshot_id
        or cash.get("status") != "verified"
        or metadata.get("account_fingerprint") != provider.account_fingerprint
        or any(item.balance_snapshot_id != snapshot_id for item in active)
        or cash.get("fund_assets") is None
    ):
        return {"success": True, "account": account, "status": "unverified"}
    amounts = adjustments_by_currency(
        active, account=account, broker_account=provider.account_fingerprint,
        balance_snapshot_id=snapshot_id,
    )
    return {
        "success": True, "account": account, "status": "verified",
        "account_fingerprint": provider.account_fingerprint, "environment": provider.trd_env,
        "snapshot_id": snapshot_id, "observed_at": cash.get("observed_at_utc"),
        "cash": cash["balances"], "fund_assets_cny": cash["fund_assets"],
        "cash_target_cny": cash.get("target"), "fx": (cash.get("fx") or {}).get("rates"),
        "adjustments": {currency: str(value) for currency, value in amounts.items()},
    }


def mmf_valuation_holdings(holdings, funding, *, account, capture=None):
    """Value one derived CNY row against the verified PM holdings baseline."""
    from src.models import CASH_ASSET_ID, MMF_ASSET_ID, AssetType, Holding

    if funding.get("status") != "verified" or funding.get("account") != account:
        raise ValueError("Money-fund transfer amounts need confirmation for the current balance")
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
