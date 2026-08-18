"""Official-SDK transport shared by exact Bitable table event handlers."""

from __future__ import annotations

from collections.abc import Mapping
import importlib.util
import json
import logging
from typing import Any, Callable, Dict, Iterable, Optional

from src import config
from src.app.bitable_event_contract import (
    BITABLE_FILE_TYPE,
    BITABLE_RECORD_CHANGED_EVENT_TYPE,
    BITABLE_SUBSCRIPTION_EVENT_TYPE,
    MAX_EVENT_IDENTIFIER_LENGTH,
)


_LOG = logging.getLogger(__name__)
_ACCEPTED_TARGETS = {"cash_flow", "holdings"}


def _safe_identifier(value: Any) -> str:
    resolved = value.strip() if isinstance(value, str) else ""
    return resolved if len(resolved) <= MAX_EVENT_IDENTIFIER_LENGTH else ""


def _event_identity(payload: Any) -> Dict[str, str]:
    header = payload.get("header") if isinstance(payload, dict) else None
    event = payload.get("event") if isinstance(payload, dict) else None
    header = header if isinstance(header, dict) else {}
    event = event if isinstance(event, dict) else {}
    return {
        "event_id": _safe_identifier(header.get("event_id")),
        "event_type": _safe_identifier(header.get("event_type")),
        "file_token": _safe_identifier(event.get("file_token")),
        "table_id": _safe_identifier(event.get("table_id")),
    }


def _log_stage_failure(stage: str, exc: Exception) -> None:
    _LOG.error(
        "feishu_bitable_event %s",
        json.dumps(
            {"exception_class": exc.__class__.__name__, "stage": stage},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _log_callback(payload: Any, outcome: Any, exc: Optional[Exception] = None) -> None:
    accepted_by = []
    success = None
    if isinstance(outcome, Mapping):
        raw_accepted_by = outcome.get("accepted_by")
        if isinstance(raw_accepted_by, list):
            accepted_by = [
                item
                for item in raw_accepted_by
                if isinstance(item, str) and item in _ACCEPTED_TARGETS
            ]
        raw_success = outcome.get("success")
        if isinstance(raw_success, bool):
            success = raw_success
    fields = {
        **_event_identity(payload),
        "accepted_by": accepted_by,
        "exception_class": exc.__class__.__name__ if exc is not None else None,
        "stage": "callback",
        "success": success,
    }
    level = logging.ERROR if exc is not None or success is False else logging.INFO
    _LOG.log(
        level,
        "feishu_bitable_event %s",
        json.dumps(fields, sort_keys=True, separators=(",", ":")),
    )


def validate_bitable_targets(targets: Iterable[Any]) -> tuple[Any, ...]:
    """Freeze targets and reject ambiguous local table routing."""

    resolved = tuple(targets)
    if not resolved:
        raise ValueError("bitable event listener requires at least one target")
    identities = []
    for target in resolved:
        app_id = str(getattr(target, "app_id", "") or "").strip()
        file_token = str(getattr(target, "file_token", "") or "").strip()
        table_id = str(getattr(target, "table_id", "") or "").strip()
        event_type = str(getattr(target, "event_type", "") or "").strip()
        if not app_id or not file_token or not table_id:
            raise ValueError("bitable event target requires app_id/file_token/table_id")
        if event_type != BITABLE_RECORD_CHANGED_EVENT_TYPE:
            raise ValueError(f"unsupported bitable event type: {event_type}")
        identities.append((app_id, file_token, table_id))
    if len({identity[0] for identity in identities}) != 1:
        raise ValueError("bitable event targets must use one Feishu app_id")
    if len(set(identities)) != len(identities):
        raise ValueError(
            "bitable event target collision: app_id/file_token/table_id must be distinct"
        )
    return resolved


class FeishuBitableEventAdapter:
    def __init__(
        self,
        *,
        targets: Iterable[Any],
        app_secret: Optional[str] = None,
        sdk_module: Any = None,
        subscribe_request_class: Any = None,
    ) -> None:
        self.targets = validate_bitable_targets(targets)
        self.app_id = str(self.targets[0].app_id)
        self.app_secret = str(
            app_secret
            if app_secret is not None
            else config.get("feishu.listener.app_secret") or ""
        ).strip()
        if not self.app_secret:
            raise ValueError(
                "missing feishu.listener.app_secret for bitable event listener"
            )
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
                "lark-oapi is required for bitable event ingress"
            ) from exc
        return lark

    def start(self, callback: Callable[[Dict[str, Any]], Any]) -> None:
        """Start one blocking official long-connection client."""

        lark = self._sdk()

        def on_event(data: Any) -> None:
            try:
                marshalled = lark.JSON.marshal(data)
            except Exception as exc:
                _log_stage_failure("sdk_marshal", exc)
                raise
            try:
                payload = json.loads(marshalled)
            except Exception as exc:
                _log_stage_failure("json_decode", exc)
                raise
            try:
                outcome = callback(payload)
            except Exception as exc:
                _log_callback(payload, None, exc)
                raise
            _log_callback(payload, outcome)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_customized_event(BITABLE_RECORD_CHANGED_EVENT_TYPE, on_event)
            .build()
        )
        client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        client.start()

    def subscribe(self) -> Dict[str, Any]:
        """Subscribe every unique configured Base file and report per-token state."""

        lark = self._sdk()
        if self._subscribe_request_class is None:
            from lark_oapi.api.drive.v1 import SubscribeFileRequest
        else:
            SubscribeFileRequest = self._subscribe_request_class

        client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .build()
        )
        results = []
        for file_token in sorted({str(target.file_token) for target in self.targets}):
            request = (
                SubscribeFileRequest.builder()
                .file_token(file_token)
                .file_type(BITABLE_FILE_TYPE)
                .build()
            )
            try:
                response = client.drive.v1.file.subscribe(request)
                if response.success():
                    results.append(
                        {
                            "success": True,
                            "file_token": file_token,
                            "response": json.loads(lark.JSON.marshal(response.data)),
                        }
                    )
                else:
                    results.append(
                        {
                            "success": False,
                            "file_token": file_token,
                            "error": (
                                "Feishu Bitable document subscription failed: "
                                f"code={response.code}, msg={response.msg}, "
                                f"log_id={response.get_log_id()}"
                            ),
                        }
                    )
            except Exception as exc:
                results.append(
                    {
                        "success": False,
                        "file_token": file_token,
                        "error": str(exc) or exc.__class__.__name__,
                    }
                )
        return {
            "success": all(item["success"] for item in results),
            "targets": [target.as_dict() for target in self.targets],
            "file_type": BITABLE_FILE_TYPE,
            "subscription_event_type": BITABLE_SUBSCRIPTION_EVENT_TYPE,
            "results": results,
        }


__all__ = ["FeishuBitableEventAdapter", "validate_bitable_targets"]
