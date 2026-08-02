"""Compatibility facade for Feishu transactions operations."""

from datetime import date
from typing import List, NoReturn, Optional

from ..models import ArchivedTransaction, Transaction
from .repositories.transactions_repository import TransactionsRepository


class TransactionsMixin:
    """Expose the historical FeishuStorage transactions API via a repository."""

    @staticmethod
    def _is_missing_field_error(error: Exception) -> bool:
        return TransactionsRepository._is_missing_field_error(error)

    @property
    def transactions(self) -> TransactionsRepository:
        repo = getattr(self, "_transactions_repository", None)
        if repo is None:
            repo = TransactionsRepository(self)
            self._transactions_repository = repo
        return repo

    def add_transaction(self, tx: Transaction) -> NoReturn:
        """Compatibility tombstone: transactions is a read-only archive."""
        return self.transactions.add_transaction(tx)

    def find_archived_transaction_by_request_id(
        self,
        *,
        account: str,
        request_id: str,
    ) -> Optional[ArchivedTransaction]:
        return self.transactions.find_archived_transaction_by_request_id(
            account=account,
            request_id=request_id,
        )

    def _find_by_dedup_key(self, table: str, dedup_key: str) -> Optional[str]:
        return self.transactions._find_by_dedup_key(table, dedup_key)

    def get_transaction(self, record_id: str) -> Optional[ArchivedTransaction]:
        return self.transactions.get_transaction(record_id)

    def get_transactions(
        self,
        account: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        tx_type: Optional[str] = None,
    ) -> List[ArchivedTransaction]:
        return self.transactions.get_transactions(
            account=account,
            start_date=start_date,
            end_date=end_date,
            tx_type=tx_type,
        )

    def delete_transaction_by_record_id(self, record_id: str) -> NoReturn:
        """Compatibility tombstone: transactions is a read-only archive."""
        return self.transactions.delete_transaction_by_record_id(record_id)
