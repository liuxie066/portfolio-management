"""Compatibility re-export for OperationStateStore.

The implementation moved to ``src/app/operation_state/`` (one base module plus
six focused mixins). Import from there directly for new code; this module only
preserves the historical import path.
"""

from .operation_state import OperationStateStore

__all__ = ["OperationStateStore"]
