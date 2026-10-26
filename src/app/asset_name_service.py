"""Asset display-name lookup service."""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone

from src.domain.holding_names import normalize_hk_stock_code, verified_hk_name

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

    @staticmethod
    def lookup_hk_stock_name(asset_id: str) -> dict | None:
        """Fetch exact public HK identity evidence without prices, FX or cache writes.

        The five-second requests timeout is an I/O budget, not a hard wall-clock
        deadline. Duplicate target assignments are ambiguous and fail closed.
        """
        import requests
        from src.tencent_batch import parse_multi_payload

        code = normalize_hk_stock_code(asset_id)
        if code is None:
            return None
        query = f"hk{code}"
        try:
            with requests.Session() as session:
                response = session.get(f"https://qt.gtimg.cn/q={query}", timeout=5)
                response.raise_for_status()
                response.encoding = "gb2312"
                text = response.text
            assignments = re.findall(rf'\bv_{query}\s*=', text, re.IGNORECASE)
            if len(assignments) != 1:
                return None
            parts = parse_multi_payload(text).get(query)
            if not parts or len(parts) < 3 or parts[2] != code:
                return None
            return verified_hk_name({
                "code": code, "market": "HK", "name": parts[1],
                "source": "tencent_hk",
                "source_as_of": datetime.now(timezone.utc).isoformat(),
            }, code)
        except (requests.RequestException, ValueError, TypeError):
            return None

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
