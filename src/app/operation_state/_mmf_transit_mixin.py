"""Durable, snapshot-bound confirmations of money-fund transfer evidence."""
from datetime import date
from decimal import Decimal

from src.domain.mmf_transit import ConfirmedMmfTransfer, TransferDirection


class MmfTransitMixin:
    def record_mmf_transfer(self, transfer: ConfirmedMmfTransfer) -> None:
        values = (
            transfer.account, transfer.broker_account, transfer.balance_snapshot_id,
            transfer.receipt_id, transfer.direction.value, transfer.currency,
            str(transfer.amount.normalize()), str(transfer.source_debit.normalize()),
            str(transfer.target_credit.normalize()),
            transfer.request_date.isoformat() if transfer.request_date else None, int(transfer.cancelled),
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            identity = conn.execute(
                """
                SELECT direction, currency, amount, request_date, target_credit, cancelled
                FROM futu_mmf_transfer_observations
                WHERE account = ? AND broker_account = ? AND receipt_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (transfer.account, transfer.broker_account, transfer.receipt_id),
            ).fetchone()
            if identity is not None:
                if tuple(identity)[:3] != values[4:7] or (
                    identity["request_date"] and identity["request_date"] != values[9]
                ):
                    raise ValueError("Transfer receipt direction, currency, amount and request date are immutable")
                if identity["cancelled"] and not transfer.cancelled:
                    raise ValueError("Cancelled transfer cannot be reopened")
                if Decimal(identity["target_credit"]) > transfer.target_credit:
                    raise ValueError("Confirmed credit cannot move backwards")
            before = conn.execute(
                """
                SELECT direction, currency, amount, source_debit, target_credit, request_date, cancelled
                FROM futu_mmf_transfer_observations
                WHERE account = ? AND broker_account = ? AND balance_snapshot_id = ?
                  AND receipt_id = ?
                """,
                values[:4],
            ).fetchone()
            if before is None:
                conn.execute(
                    """
                    INSERT INTO futu_mmf_transfer_observations (
                        account, broker_account, balance_snapshot_id, receipt_id, direction,
                        currency, amount, source_debit, target_credit, request_date, cancelled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
            elif tuple(before) != values[4:]:
                raise ValueError("Conflicting confirmation for the same transfer and balance")

    def read_mmf_transfers(
        self, *, account: str, broker_account: str, balance_snapshot_id: str,
    ) -> list[ConfirmedMmfTransfer]:
        with self._connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(futu_mmf_transfer_observations)")}
            if not columns:
                return []
            request_date = "request_date" if "request_date" in columns else "NULL AS request_date"
            cancelled = "cancelled" if "cancelled" in columns else "0 AS cancelled"
            rows = conn.execute(
                f"""
                SELECT receipt_id, direction, currency, amount, source_debit, target_credit,
                       {request_date}, {cancelled}
                FROM futu_mmf_transfer_observations
                WHERE account = ? AND broker_account = ? AND balance_snapshot_id = ?
                ORDER BY receipt_id
                """,
                (account, broker_account, balance_snapshot_id),
            ).fetchall()
        return [
            ConfirmedMmfTransfer(
                account=account,
                broker_account=broker_account,
                balance_snapshot_id=balance_snapshot_id,
                receipt_id=row["receipt_id"],
                direction=TransferDirection(row["direction"]),
                currency=row["currency"],
                amount=Decimal(row["amount"]),
                source_debit=Decimal(row["source_debit"]),
                target_credit=Decimal(row["target_credit"]),
                request_date=date.fromisoformat(row["request_date"]) if row["request_date"] else None,
                cancelled=bool(row["cancelled"]),
            )
            for row in rows
        ]

    def reconcile_mmf_transfer(self, transfer, flows, *, confirm=False) -> Decimal:
        """Attribute broker flow IDs once and report the balance-supported amount."""
        from src.domain.mmf_transit import reconciled_amount

        current = self.read_mmf_transfers(
            account=transfer.account, broker_account=transfer.broker_account,
            balance_snapshot_id=transfer.balance_snapshot_id,
        )
        if transfer not in current:
            raise ValueError("Confirmed transfer for the selected snapshot is required")
        reconciled_amount(transfer, flows)
        with self._connect() as conn:
            if confirm:
                conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT flow_id, amount FROM futu_mmf_settlement_flows
                WHERE broker_account = ? AND account = ? AND receipt_id = ?
                """,
                (transfer.broker_account, transfer.account, transfer.receipt_id),
            ).fetchall()
            amounts = {row["flow_id"]: Decimal(row["amount"]) for row in existing}
            for flow in flows:
                prior = conn.execute(
                    """
                    SELECT account, receipt_id, amount FROM futu_mmf_settlement_flows
                    WHERE broker_account = ? AND flow_id = ?
                    """,
                    (transfer.broker_account, flow.flow_id),
                ).fetchone()
                if prior and (
                    prior["account"] != transfer.account or prior["receipt_id"] != transfer.receipt_id
                    or Decimal(prior["amount"]) != flow.amount
                ):
                    raise ValueError("Broker cash flow is already attributed to different evidence")
                amounts[flow.flow_id] = flow.amount
            total = sum(amounts.values(), start=Decimal(0))
            if total > transfer.amount:
                raise ValueError("Attributed flows exceed the original transfer amount")
            if confirm:
                for flow_id, amount in amounts.items():
                    conn.execute(
                        """
                        INSERT INTO futu_mmf_settlement_flows (
                            broker_account, flow_id, account, receipt_id, balance_snapshot_id, amount
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(broker_account, flow_id) DO NOTHING
                        """,
                        (transfer.broker_account, flow_id, transfer.account, transfer.receipt_id,
                         transfer.balance_snapshot_id, str(amount)),
                    )
            return min(total, transfer.target_credit)
