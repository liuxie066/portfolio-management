"""Run identifier helpers for traceable product workflows."""
from __future__ import annotations

import re
import uuid

from src.time_utils import bj_now_naive


_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_part(value: str) -> str:
    return _SAFE_PART_RE.sub("-", value).strip("-") or "default"


def new_run_id(kind: str, account: str | None = None) -> str:
    """Create a compact run id for correlating NAV/report artifacts."""
    timestamp = bj_now_naive().strftime("%Y%m%dT%H%M%S%f")
    parts = [_safe_part(kind), timestamp]
    if account:
        parts.append(_safe_part(account))
    parts.append(uuid.uuid4().hex[:8])
    return "-".join(parts)
