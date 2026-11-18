"""Classify confirmed and indeterminate receipt delivery consistently."""
from typing import Any, Mapping


def receipt_outcome(delivery: Mapping[str, Any]) -> str:
    state = str(delivery.get("delivery_state") or "").lower()
    if delivery.get("success") and state == "accepted":
        return "sent"
    if state == "failed":
        return "failed"
    return "unknown"
