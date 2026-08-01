"""Narrow official-SDK adapter for holdings Base events and subscription."""

from __future__ import annotations

import importlib.util
import json
from typing import Any, Callable, Dict, Optional

from src import config
from src.app.holdings_event_service import (
    HOLDINGS_EVENT_TYPE,
    HOLDINGS_FILE_TYPE,
    HOLDINGS_SUBSCRIPTION_EVENT_TYPE,
    HoldingsEventTarget,
)


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
            app_secret if app_secret is not None else config.get("feishu.app_secret") or ""
        ).strip()
        if not self.app_secret:
            raise ValueError("missing feishu.app_secret for holdings event listener")
        self._sdk_module = sdk_module
        self._subscribe_request_class = subscribe_request_class

    @staticmethod
    def sdk_available() -> bool:
        return importlib.util.find_spec("lark_oapi") is not None

    def _sdk(self) -> Any:
        if self._sdk_module is not None:
            return self._sdk_module
        try:
            import lark_oapi as lark
        except ImportError as exc:
            raise RuntimeError(
                "lark-oapi is required for holdings event ingress"
            ) from exc
        return lark

    def start(self, callback: Callable[[Dict[str, Any]], Any]) -> None:
        """Start the blocking official long-connection client."""

        lark = self._sdk()

        def on_event(data: Any) -> None:
            payload = json.loads(lark.JSON.marshal(data))
            callback(payload)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_customized_event(HOLDINGS_EVENT_TYPE, on_event)
            .build()
        )
        client = lark.ws.Client(
            self.target.app_id,
            self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        client.start()

    def subscribe(self) -> Dict[str, Any]:
        """Create only the exact configured document event subscription."""

        lark = self._sdk()
        if self._subscribe_request_class is None:
            from lark_oapi.api.drive.v1 import SubscribeFileRequest
        else:
            SubscribeFileRequest = self._subscribe_request_class

        client = (
            lark.Client.builder()
            .app_id(self.target.app_id)
            .app_secret(self.app_secret)
            .build()
        )
        request = (
            SubscribeFileRequest.builder()
            .file_token(self.target.file_token)
            .file_type(HOLDINGS_FILE_TYPE)
            .build()
        )
        response = client.drive.v1.file.subscribe(request)
        if not response.success():
            raise RuntimeError(
                "Feishu holdings document subscription failed: "
                f"code={response.code}, msg={response.msg}, "
                f"log_id={response.get_log_id()}"
            )
        return {
            "success": True,
            "target": self.target.as_dict(),
            "file_type": HOLDINGS_FILE_TYPE,
            "subscription_event_type": HOLDINGS_SUBSCRIPTION_EVENT_TYPE,
            "response": json.loads(lark.JSON.marshal(response.data)),
        }
