#!/usr/bin/env python3
"""Environment doctor for portfolio-management.

Checks:
- Python deps (pydantic/requests)
- Network reachability for quote sources
- Optional Finnhub key status
- Feishu credentials sanity (can list fields for holdings)

Usage:
  . .venv/bin/activate
  python scripts/doctor.py
"""

# Ensure repo root is on sys.path when executed as a script.
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import socket
import ssl
import urllib.request
from typing import Dict, Any


def _check_import(name: str) -> Dict[str, Any]:
    try:
        mod = __import__(name)
        ver = getattr(mod, '__version__', None)
        return {"ok": True, "version": ver}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _http_head(url: str, timeout: int = 5) -> Dict[str, Any]:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "status": resp.status}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _check_feishu() -> Dict[str, Any]:
    try:
        from src.feishu_client import FeishuClient
        c = FeishuClient()
        app_token, table_id = c._get_table_config('holdings')
        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        data = c._request("GET", endpoint, params={"page_size": 5})
        return {"ok": True, "holdings": f"{app_token}/{table_id}", "code": data.get('code')}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _check_finnhub_config() -> Dict[str, Any]:
    try:
        from src import config

        api_key = config.get("finnhub_api_key")
    except Exception as e:
        return {"ok": True, "enabled": False, "status": "config_unavailable", "error": str(e)}

    if api_key:
        return {"ok": True, "enabled": True, "status": "configured"}
    return {"ok": True, "enabled": False, "status": "disabled_no_key"}


def main() -> int:
    report: Dict[str, Any] = {
        "imports": {
            "pydantic": _check_import("pydantic"),
            "requests": _check_import("requests"),
        },
        "network": {
            "tencent_qt": _http_head("http://qt.gtimg.cn/q=sh600519"),
            "yahoo_chart": _http_head("https://query1.finance.yahoo.com/v8/finance/chart/AAPL?interval=1d&range=2d"),
            "fx_erapi": _http_head("https://open.er-api.com/v6/latest/USD"),
        },
        "pricing": {
            "finnhub": _check_finnhub_config(),
        },
        "feishu": {},
        "ok": True,
    }

    report["feishu"] = _check_feishu()

    # overall ok
    if any(not item.get("ok") for item in report["imports"].values()):
        report["ok"] = False
    if not report["feishu"].get("ok"):
        report["ok"] = False

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
