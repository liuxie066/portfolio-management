"""Transport-neutral holdings source contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RawHoldingRecord:
    """One untyped source row before any model or cache defaults."""

    record_id: str
    raw_fields: Mapping[str, Any]
    source: str = "feishu"
    fetched_at: Optional[datetime] = None

    def canonical_fields(self) -> dict[str, Any]:
        return {str(key): value for key, value in self.raw_fields.items()}
