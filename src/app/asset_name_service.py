"""Asset display-name lookup service."""
from __future__ import annotations
import logging

import threading
import time
from typing import Any


class AssetNameService:
    """Resolve full asset names with bounded latency."""

    def __init__(self, manager: Any):
        self.manager = manager

    @staticmethod
    def lookup_fund_name(asset_id: str) -> dict | None:
        """Return exact-source evidence, never the display-name code fallback."""
        from src.price_fetcher import PriceFetcher

        fetcher = PriceFetcher(use_cache=False, cache_writes=False)
        try:
            result = fetcher.fetch(
                asset_id,
                force_refresh=True,
                asset_type_map={asset_id: "otc_fund"},
                deadline=time.monotonic() + 5.0,
            )
        finally:
            fetcher.session.close()
        # Tencent's jj response identifier is matched to the requested code by FundProvider.
        if (
            not result
            or result.get("source") != "tencent_jj"
            or result.get("code") != asset_id
            or result.get("is_stale")
            or result.get("is_from_cache")
        ):
            return None
        name = result.get("name")
        if not isinstance(name, str) or not name.strip() or name.strip() == asset_id:
            return None
        return {
            "code": asset_id,
            "name": name.strip(),
            "source": "tencent_jj",
            "source_as_of": result.get("fetched_at"),
        }

    def get_asset_name(self, asset_id: str, user_provided_name: str = None, timeout: float = 5.0) -> str:
        """Resolve asset name via price lookup with timeout fallback."""
        result = {"data": None, "error": None}

        def fetch_name():
            try:
                result["data"] = self.manager.price_fetcher.fetch(asset_id)
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=fetch_name, daemon=True)
        thread.start()
        thread.join(timeout=timeout)

        if result["data"] and result["data"].get("name"):
            return result["data"]["name"]
        if thread.is_alive():
            logging.getLogger(__name__).warning(f"[警告] 获取资产名称超时 {asset_id}，使用备选名称")
        elif result["error"]:
            logging.getLogger(__name__).warning(f"[警告] 获取资产名称失败 {asset_id}: {result['error']}")

        if user_provided_name:
            return user_provided_name
        return asset_id
