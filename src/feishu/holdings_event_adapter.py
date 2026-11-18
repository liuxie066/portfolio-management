"""Narrow official-SDK adapter for holdings Base events and subscription."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src import config
from src.app.holdings_event_service import (
    HOLDINGS_FILE_TYPE,
    HOLDINGS_SUBSCRIPTION_EVENT_TYPE,
    HoldingsEventTarget,
)

from src.feishu.bitable_event_adapter import FeishuBitableEventAdapter


class FeishuHoldingsEventAdapter:
    def __init__(
        self,
        *,
        target: Optional[HoldingsEventTarget] = None,
        app_secret: Optional[str] = None,
        sdk_module: Any = None,
        subscribe_request_class: Any = None,
    ) -> None:
        self.target = target or HoldingsEventTarget.from_config()
        self.app_secret = str(
            app_secret
            if app_secret is not None
            else config.get("feishu.listener.app_secret") or ""
        ).strip()
        if not self.app_secret:
            raise ValueError(
                "missing feishu.listener.app_secret for holdings event listener"
            )
        self._adapter = FeishuBitableEventAdapter(
            targets=(self.target,),
            app_secret=self.app_secret,
            sdk_module=sdk_module,
            subscribe_request_class=subscribe_request_class,
        )

    sdk_available = staticmethod(FeishuBitableEventAdapter.sdk_available)

    def start(self, callback: Callable[[Dict[str, Any]], Any]) -> None:
        self._adapter.start(callback)

    def subscribe(self) -> Dict[str, Any]:
        outcome = self._adapter.subscribe()["results"][0]
        if not outcome["success"]:
            raise RuntimeError(outcome["error"])
        return {
            "success": True,
            "target": self.target.as_dict(),
            "file_type": HOLDINGS_FILE_TYPE,
            "subscription_event_type": HOLDINGS_SUBSCRIPTION_EVENT_TYPE,
            "response": outcome["response"],
        }
