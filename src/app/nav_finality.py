"""Versioned provenance contract for authoritative NAV history writes."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Mapping, Optional


FINALITY_VERSION = 1
FINALITY_STATUSES = frozenset({"final", "manual", "initial", "closed", "maintenance"})
FINALITY_WRITERS = frozenset(
    {"daily-nav-job", "nav-record", "daily-report", "init-nav", "close-nav", "nav-repair"}
)
FINALITY_STATUS_BY_WRITER = {
    "daily-nav-job": frozenset({"final"}),
    "nav-record": frozenset({"manual"}),
    "daily-report": frozenset({"manual"}),
    "init-nav": frozenset({"initial"}),
    "close-nav": frozenset({"closed"}),
    "nav-repair": frozenset({"final", "maintenance"}),
}


def _coerce_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _normalize_valuation_as_of(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = value.isoformat() if isinstance(value, datetime) else str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()


@dataclass(frozen=True)
class NavWriteContext:
    """Trusted internal classification for one NAV mutation."""

    status: str
    writer: str
    write_reason: str
    nav_date: date
    valuation_as_of: Optional[str] = None
    run_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in FINALITY_STATUSES:
            raise ValueError(f"unsupported NAV finality status: {self.status}")
        if self.writer not in FINALITY_WRITERS:
            raise ValueError(f"unsupported NAV finality writer: {self.writer}")
        if self.status not in FINALITY_STATUS_BY_WRITER[self.writer]:
            raise ValueError(
                f"NAV finality status {self.status} is invalid for writer {self.writer}"
            )
        reason = str(self.write_reason or "").strip()
        if not reason:
            raise ValueError("NAV finality write_reason is required")
        object.__setattr__(self, "write_reason", reason)
        object.__setattr__(self, "nav_date", _coerce_date(self.nav_date))
        object.__setattr__(self, "valuation_as_of", _normalize_valuation_as_of(self.valuation_as_of))
        if self.run_id is not None:
            run_id = str(self.run_id).strip()
            object.__setattr__(self, "run_id", run_id or None)

    def with_runtime(
        self,
        *,
        valuation_as_of: Any = None,
        run_id: Optional[str] = None,
    ) -> "NavWriteContext":
        """Fill runtime fields and reject conflicting facts."""
        runtime_valuation_as_of = _normalize_valuation_as_of(valuation_as_of)
        runtime_run_id = str(run_id).strip() if run_id is not None else None
        runtime_run_id = runtime_run_id or None
        if self.valuation_as_of and runtime_valuation_as_of and self.valuation_as_of != runtime_valuation_as_of:
            raise ValueError("NAV finality valuation_as_of conflicts with runtime snapshot_time")
        if self.run_id and runtime_run_id and self.run_id != runtime_run_id:
            raise ValueError("NAV finality run_id conflicts with runtime run_id")
        return replace(
            self,
            valuation_as_of=self.valuation_as_of or runtime_valuation_as_of,
            run_id=self.run_id or runtime_run_id,
        )

    def to_details(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": FINALITY_VERSION,
            "status": self.status,
            "nav_date": self.nav_date.isoformat(),
            "valuation_as_of": self.valuation_as_of,
            "writer": self.writer,
            "write_reason": self.write_reason,
        }
        if self.run_id:
            payload["run_id"] = self.run_id
        return payload


@dataclass(frozen=True)
class NavFinalityDecision:
    eligible: bool
    reason: str
    finality: Optional[dict[str, Any]]


def evaluate_nav_finality(
    details: Optional[Mapping[str, Any]],
    *,
    target_date: Any,
) -> NavFinalityDecision:
    """Return whether an existing row is a trustworthy final daily NAV."""
    raw = (details or {}).get("finality")
    if not isinstance(raw, Mapping):
        return NavFinalityDecision(False, "missing_finality", None)

    finality = dict(raw)
    version = finality.get("version")
    if isinstance(version, bool) or version != FINALITY_VERSION:
        return NavFinalityDecision(False, "unsupported_finality_version", finality)
    if finality.get("status") != "final":
        return NavFinalityDecision(False, "status_not_final", finality)
    writer = str(finality.get("writer") or "").strip()
    if not writer:
        return NavFinalityDecision(False, "missing_writer", finality)
    if writer not in FINALITY_WRITERS:
        return NavFinalityDecision(False, "unsupported_writer", finality)
    if "final" not in FINALITY_STATUS_BY_WRITER[writer]:
        return NavFinalityDecision(False, "writer_status_mismatch", finality)
    if not str(finality.get("write_reason") or "").strip():
        return NavFinalityDecision(False, "missing_write_reason", finality)
    if "valuation_as_of" not in finality:
        return NavFinalityDecision(False, "missing_valuation_as_of", finality)
    try:
        _normalize_valuation_as_of(finality.get("valuation_as_of"))
    except (TypeError, ValueError):
        return NavFinalityDecision(False, "invalid_valuation_as_of", finality)
    if "run_id" in finality and not str(finality.get("run_id") or "").strip():
        return NavFinalityDecision(False, "invalid_run_id", finality)

    expected_date = _coerce_date(target_date).isoformat()
    if str(finality.get("nav_date") or "") != expected_date:
        return NavFinalityDecision(False, "nav_date_mismatch", finality)
    return NavFinalityDecision(True, "eligible_final", finality)
