"""OperationStateStore facade: one SQLite DB, six focused mixins."""
from ._base import OperationStateBase
from ._fx_confirmation_mixin import FxConfirmationMixin
from ._nav_receipt_mixin import NavReceiptMixin
from ._holding_case_mixin import HoldingCaseMixin
from ._holding_event_mixin import HoldingEventMixin
from ._cash_flow_event_mixin import CashFlowEventMixin
from ._operation_receipt_mixin import OperationReceiptMixin


class OperationStateStore(
    OperationStateBase,
    FxConfirmationMixin,
    NavReceiptMixin,
    HoldingCaseMixin,
    HoldingEventMixin,
    CashFlowEventMixin,
    OperationReceiptMixin,
):
    """SQLite state independent from Feishu facts and cash-flow effect cutover."""


__all__ = ["OperationStateStore"]
