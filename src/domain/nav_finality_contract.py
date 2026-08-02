"""Pure canonical vocabulary and validation for NAV finality provenance."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Optional

FINALITY_VERSION = 1
FINALITY_STATUSES = frozenset({
    "final",
    "manual",
    "initial",
    "closed",
    "maintenance",
})
FINALITY_WRITERS = frozenset({
    "daily-nav-job",
    "nav-record",
    "daily-report",
    "init-nav",
    "close-nav",
    "nav-repair",
})
FINALITY_STATUS_BY_WRITER = {
    "daily-nav-job": frozenset({"final"}),
    "nav-record": frozenset({"manual"}),
    "daily-report": frozenset({"manual"}),
    "init-nav": frozenset({"initial"}),
    "close-nav": frozenset({"closed"}),
    "nav-repair": frozenset({"final", "maintenance"}),
}


def _target_date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date().isoformat()


def finality_validation_reason(
    payload: Any,
    *,
    target_date: Any,
    expected_status: Optional[str] = None,
) -> Optional[str]:
    """Return the canonical first failure reason, or ``None`` when valid."""

    if not isinstance(payload, Mapping):
        return "missing_finality"
    version = payload.get("version")
    if isinstance(version, bool) or version != FINALITY_VERSION:
        return "unsupported_finality_version"
    status = str(payload.get("status") or "").strip()
    if expected_status is not None and status != expected_status:
        return f"status_not_{expected_status}"
    if status not in FINALITY_STATUSES:
        return "unsupported_status"
    writer = str(payload.get("writer") or "").strip()
    if not writer:
        return "missing_writer"
    if writer not in FINALITY_WRITERS:
        return "unsupported_writer"
    if status not in FINALITY_STATUS_BY_WRITER[writer]:
        return "writer_status_mismatch"
    if not str(payload.get("write_reason") or "").strip():
        return "missing_write_reason"
    if "valuation_as_of" not in payload:
        return "missing_valuation_as_of"
    valuation_as_of = payload.get("valuation_as_of")
    if valuation_as_of not in (None, ""):
        text = (
            valuation_as_of.isoformat()
            if isinstance(valuation_as_of, datetime)
            else str(valuation_as_of).strip()
        )
        try:
            datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return "invalid_valuation_as_of"
    if "run_id" in payload and not str(payload.get("run_id") or "").strip():
        return "invalid_run_id"
    try:
        expected_date = _target_date_text(target_date)
    except (TypeError, ValueError):
        return "invalid_target_date"
    if str(payload.get("nav_date") or "") != expected_date:
        return "nav_date_mismatch"
    return None
