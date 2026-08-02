"""Domain-owned compensation lifecycle contract."""
from __future__ import annotations

from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class CompensationStatus(str, Enum):
    PREPARED = "PREPARED"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    RESOLVED = "RESOLVED"


COMPENSATION_STATUS_VALUES = tuple(status.value for status in CompensationStatus)
MIRROR_COMPENSATION_STATUS_VALUES = tuple(
    status.value
    for status in CompensationStatus
    if status is not CompensationStatus.PREPARED
)
MIRROR_ACTIVATING_STATUS_VALUES = frozenset({
    CompensationStatus.PENDING.value,
    CompensationStatus.RUNNING.value,
    CompensationStatus.FAILED.value,
})

_ACTIONABLE_TRANSITIONS = frozenset({
    CompensationStatus.PENDING,
    CompensationStatus.RUNNING,
    CompensationStatus.FAILED,
    CompensationStatus.RESOLVED,
})
COMPENSATION_TRANSITIONS: Mapping[
    CompensationStatus,
    frozenset[CompensationStatus],
] = MappingProxyType({
    CompensationStatus.PREPARED: frozenset(CompensationStatus),
    CompensationStatus.PENDING: _ACTIONABLE_TRANSITIONS,
    CompensationStatus.RUNNING: frozenset({
        CompensationStatus.RUNNING,
        CompensationStatus.FAILED,
        CompensationStatus.RESOLVED,
    }),
    CompensationStatus.FAILED: frozenset({
        CompensationStatus.FAILED,
        CompensationStatus.RUNNING,
        CompensationStatus.RESOLVED,
    }),
    CompensationStatus.RESOLVED: frozenset({CompensationStatus.RESOLVED}),
})


def compensation_status(value: Any) -> CompensationStatus:
    if isinstance(value, CompensationStatus):
        return value
    try:
        return CompensationStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"unsupported compensation status: {value}") from exc


def validate_compensation_transition(current: Any, target: Any) -> str:
    current_status = compensation_status(current)
    target_status = compensation_status(target)
    if target_status not in COMPENSATION_TRANSITIONS[current_status]:
        raise ValueError(
            "invalid compensation transition: "
            f"{current_status.value} -> {target_status.value}"
        )
    return target_status.value
