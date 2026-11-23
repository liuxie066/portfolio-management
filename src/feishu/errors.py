"""Typed errors shared by Feishu transport and storage boundaries."""
from __future__ import annotations


class FeishuReadTimeout(TimeoutError):
    """A bounded Feishu read failed at a specific blocking stage."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"Feishu {stage}: {message}")


class FeishuRecordNotFoundError(LookupError):
    """Feishu explicitly reported that a concrete record ID does not exist."""

    def __init__(
        self, *, code: int, message: str,
        http_status: int | None = None, log_id: str | None = None,
    ):
        self.code = int(code)
        self.message = str(message or "RecordIdNotFound")
        self.http_status = http_status
        self.log_id = log_id
        status = f"HTTP {http_status}, " if http_status is not None else ""
        trace = f", log_id={log_id}" if log_id else ""
        super().__init__(
            f"Feishu record not found: {self.message} ({status}code={self.code}{trace})"
        )


class LegacyReadOnlyError(RuntimeError):
    """A retired Feishu archive was asked to perform a mutation."""

    def __init__(self, *, table: str, operation: str):
        self.table = str(table)
        self.operation = str(operation)
        super().__init__(
            f"Feishu {self.table} is a legacy read-only archive; "
            f"{self.operation} is disabled"
        )


class FeishuMessageNotSent(ValueError):
    """The message request was not issued or was explicitly rate-limited."""
