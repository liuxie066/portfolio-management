"""Snapshot-bound valuation of confirmed cash and money-fund transfers."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class TransferDirection(StrEnum):
    SUBSCRIPTION = "subscription"
    REDEMPTION = "redemption"


@dataclass(frozen=True)
class ConfirmedMmfTransfer:
    """One app receipt with the amounts reflected in an identified balance."""

    account: str
    broker_account: str
    receipt_id: str
    balance_snapshot_id: str
    direction: TransferDirection
    currency: str
    amount: Decimal
    source_debit: Decimal
    target_credit: Decimal
    request_date: date | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not all((self.account, self.broker_account, self.receipt_id, self.balance_snapshot_id)):
            raise ValueError("Account, receipt and balance snapshot identities are required")
        if not isinstance(self.direction, TransferDirection):
            raise ValueError("An internal money-fund transfer direction is required")
        if self.currency not in {"CNY", "HKD", "USD"}:
            raise ValueError("Unsupported transfer currency")
        amounts = (self.amount, self.source_debit, self.target_credit)
        if any(not isinstance(amount, Decimal) or not amount.is_finite() for amount in amounts):
            raise ValueError("Transfer amounts must be finite decimals")
        if self.amount <= 0 or not 0 <= self.target_credit <= self.source_debit <= self.amount:
            raise ValueError("Inconsistent transfer amount, debit or credit")

        if self.cancelled and self.source_debit != self.target_credit:
            raise ValueError("Cancelled transfer requires proof of zero remaining adjustment")

    @property
    def in_transit(self) -> Decimal:
        return self.source_debit - self.target_credit


def adjustments_by_currency(
    transfers: list[ConfirmedMmfTransfer],
    *,
    account: str,
    broker_account: str,
    balance_snapshot_id: str,
) -> dict[str, Decimal]:
    """Use verified amounts only with the balance they were verified against."""
    result: dict[str, Decimal] = {}
    receipts: set[str] = set()
    for transfer in transfers:
        if (
            transfer.account != account
            or transfer.broker_account != broker_account
            or transfer.balance_snapshot_id != balance_snapshot_id
        ):
            raise ValueError("Transfer evidence does not match the account and balance snapshot")
        if transfer.receipt_id in receipts:
            raise ValueError("Duplicate transfer receipt")
        receipts.add(transfer.receipt_id)
        if transfer.in_transit:
            result[transfer.currency] = result.get(transfer.currency, Decimal(0)) + transfer.in_transit
    return result


@dataclass(frozen=True)
class MmfSettlementFlow:
    """One broker cash-flow row already classified as an internal transfer."""

    flow_id: str
    broker_account: str
    direction: TransferDirection
    currency: str
    amount: Decimal

    def __post_init__(self) -> None:
        if not self.flow_id or not self.broker_account:
            raise ValueError("Settlement flow and broker account identities are required")
        if not isinstance(self.direction, TransferDirection):
            raise ValueError("An internal money-fund transfer direction is required")
        if self.currency not in {"CNY", "HKD", "USD"}:
            raise ValueError("Unsupported settlement currency")
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite() or self.amount <= 0:
            raise ValueError("Settlement amount must be a positive finite decimal")


def reconciled_amount(
    transfer: ConfirmedMmfTransfer,
    matched_flows: list[MmfSettlementFlow],
) -> Decimal:
    """Close only the amount supported by both attributed flows and the balance."""
    by_id: dict[str, MmfSettlementFlow] = {}
    for flow in matched_flows:
        if (flow.broker_account, flow.direction, flow.currency) != (
            transfer.broker_account, transfer.direction, transfer.currency,
        ):
            raise ValueError("Settlement flow does not match the transfer scope")
        original = by_id.setdefault(flow.flow_id, flow)
        if original != flow:
            raise ValueError("Conflicting settlement flow evidence")
    amount = sum((flow.amount for flow in by_id.values()), start=Decimal(0))
    if amount > transfer.amount:
        raise ValueError("Settlement flows exceed the original transfer amount")
    return min(amount, transfer.target_credit)
