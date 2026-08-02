"""Typed errors shared by Feishu transport and storage boundaries."""
from __future__ import annotations


class FeishuRecordNotFoundError(LookupError):
    """Feishu explicitly reported that a concrete record ID does not exist."""

    def __init__(self, *, code: int, message: str):
        self.code = int(code)
        self.message = str(message or "RecordIdNotFound")
        super().__init__(f"Feishu record not found: {self.message} (code={self.code})")


class LegacyReadOnlyError(RuntimeError):
    """A retired Feishu archive was asked to perform a mutation."""

    def __init__(self, *, table: str, operation: str):
        self.table = str(table)
        self.operation = str(operation)
        super().__init__(
            f"Feishu {self.table} is a legacy read-only archive; "
            f"{self.operation} is disabled"
        )
