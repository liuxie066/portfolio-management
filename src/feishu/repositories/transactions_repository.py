"""Repository for the Feishu transactions table."""
from datetime import date
from typing import List, NoReturn, Optional

from ...models import ArchivedTransaction, Transaction
from ..errors import LegacyReadOnlyError


class TransactionsRepository:
    """Transactions table operations."""

    def __init__(self, storage):
        self.storage = storage

    def __getattr__(self, name: str):
        return getattr(self.storage, name)

    @staticmethod
    def _is_missing_field_error(error: Exception) -> bool:
        msg = str(error)
        lowered = msg.lower()
        return (
            'fieldnamenotfound' in lowered or
            ('field' in lowered and 'not found' in lowered) or
            '字段不存在' in msg or
            '不存在' in msg
        )

    def add_transaction(self, _tx: Transaction) -> NoReturn:
        """Reject writes to the retired transactions archive before transport."""
        raise LegacyReadOnlyError(table="transactions", operation="create")

    def find_archived_transaction_by_request_id(
        self,
        *,
        account: str,
        request_id: str,
    ) -> Optional[ArchivedTransaction]:
        """Read one archived transaction by its account-scoped request key."""
        if not isinstance(account, str) or not account.strip():
            raise ValueError("archive transaction lookup requires nonblank account")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("archive transaction lookup requires nonblank request_id")

        filter_str = (
            f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'
            f' AND CurrentValue.[request_id] = "{self._escape_filter_value(request_id)}"'
        )
        try:
            records = self.client.list_records('transactions', filter_str=filter_str)
        except Exception as e:
            if self._is_missing_field_error(e):
                raise ValueError(
                    "Feishu transactions 表缺少 account/request_id 字段，"
                    "无法按归档业务键读取"
                ) from e
            raise RuntimeError(
                "transaction archive lookup failed for "
                f"account={account}, request_id={request_id}"
            ) from e

        matches: list[ArchivedTransaction] = []
        for record in records:
            archived = self._record_to_archived_transaction(record)
            if archived.account != account or archived.request_id != request_id:
                raise ValueError(
                    "transaction archive lookup returned an out-of-scope row: "
                    f"requested=({account}, {request_id}), "
                    f"actual=({archived.account}, {archived.request_id})"
                )
            matches.append(archived)

        if len(matches) > 1:
            raise ValueError(
                "transaction archive business key is ambiguous: "
                f"account={account}, request_id={request_id}, matches={len(matches)}"
            )
        if matches:
            return matches[0]

        return None

    def _find_by_dedup_key(self, table: str, dedup_key: str) -> Optional[str]:
        """通过 dedup_key 查找记录（用于内容指纹防重，带本地缓存）

        Returns:
            record_id if found, else None
        """
        if not dedup_key:
            return None

        cache_key = f"{table}:{dedup_key}"
        cached_record_id = self._dedup_key_cache.get(cache_key)
        if cached_record_id:
            try:
                record = self.client.get_record_strict(table, cached_record_id)
                if record:
                    return cached_record_id
            except Exception:
                self._dedup_key_cache.pop(cache_key, None)

        filter_str = f'CurrentValue.[dedup_key] = "{self._escape_filter_value(dedup_key)}"'
        try:
            records = self.client.list_records(table, filter_str=filter_str)
            if records:
                record_id = records[0]['record_id']
                self._dedup_key_cache[cache_key] = record_id
                return record_id
        except Exception as e:
            if self._is_missing_field_error(e):
                raise ValueError(f"Feishu {table} 表缺少 dedup_key 字段，无法保证防重；请先补齐表字段") from e
            raise

        return None

    def get_transaction(self, record_id: str) -> Optional[ArchivedTransaction]:
        """Read one strict archived transaction by record id."""
        record = self._read_record('transactions', record_id)
        if not record:
            return None
        return self._record_to_archived_transaction(record)

    def get_transactions(self, account: Optional[str] = None,
                        start_date: Optional[date] = None,
                        end_date: Optional[date] = None,
                        tx_type: Optional[str] = None) -> List[ArchivedTransaction]:
        """Read strict archive rows with ISO Text date filters pushed down."""
        conditions = []

        if account:
            conditions.append(f'CurrentValue.[account] = "{self._escape_filter_value(account)}"')
        if tx_type:
            conditions.append(f'CurrentValue.[tx_type] = "{self._escape_filter_value(tx_type)}"')
        if start_date:
            conditions.append(f'CurrentValue.[tx_date] >= "{start_date.strftime("%Y-%m-%d")}"')
        if end_date:
            conditions.append(f'CurrentValue.[tx_date] <= "{end_date.strftime("%Y-%m-%d")}"')

        filter_str = ' AND '.join(conditions) if conditions else None
        records = self.client.list_records('transactions', filter_str=filter_str)

        transactions: list[ArchivedTransaction] = []
        for record in records:
            transactions.append(self._record_to_archived_transaction(record))

        transactions.sort(key=lambda transaction: transaction.tx_date, reverse=True)
        return transactions

    def _record_to_archived_transaction(self, record: dict) -> ArchivedTransaction:
        fields = self._from_feishu_fields(record.get('fields') or {}, 'transactions')
        fields['record_id'] = record.get('record_id')
        return ArchivedTransaction.model_validate(fields)

    def delete_transaction_by_record_id(self, _record_id: str) -> NoReturn:
        """Reject deletes from the retired transactions archive before transport."""
        raise LegacyReadOnlyError(table="transactions", operation="delete")
