"""Tencent batch quote provider for CN/HK stocks and fund NAV."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.asset_utils import detect_market_type
from src.tencent_batch import fetch_batch as tencent_fetch_batch

from ..cache import market_type_from_asset_type


def fetch_tencent_quotes_batch(
    fetcher,
    codes: List[str],
    name_map: Dict[str, str] | None = None,
    asset_type_map: Dict[str, Any] | None = None,
) -> Tuple[Dict[str, Dict], List[str]]:
    """Fetch Tencent quotes in batch for cn/hk/fund(jj)."""
    name_map = name_map or {}
    results: Dict[str, Dict] = {}
    leftover: List[str] = []

    cn_query: List[Tuple[str, str]] = []
    hk_query: List[Tuple[str, str]] = []
    fund_query: List[Tuple[str, str]] = []

    for code in codes:
        market_type = detect_market_type(code)
        if asset_type_map is not None and code in asset_type_map:
            market_type = market_type_from_asset_type(code, asset_type_map.get(code), market_type)

        normalized = (code or "").upper().strip()
        if market_type == "cn":
            if normalized.startswith(("SH", "SZ")):
                query_code = normalized.lower()
            elif normalized.isdigit() and len(normalized) == 6:
                query_code = ("sh" + normalized) if normalized.startswith(("6", "5")) else ("sz" + normalized)
            else:
                leftover.append(code)
                continue
            cn_query.append((code, query_code))
        elif market_type == "hk":
            num = normalized[2:] if normalized.startswith("HK") else normalized
            if not num.isdigit():
                leftover.append(code)
                continue
            hk_query.append((code, "hk" + num.zfill(5)))
        elif market_type == "fund":
            fund_code = normalized[2:] if normalized.startswith(("SH", "SZ")) else normalized
            if not (fund_code.isdigit() and len(fund_code) == 6):
                leftover.append(code)
                continue
            fund_query.append((code, "jj" + fund_code))
        else:
            leftover.append(code)

    query_codes = [query for _, query in (cn_query + hk_query + fund_query)]
    if not query_codes:
        return results, leftover

    parts_map, meta = tencent_fetch_batch(fetcher.session, query_codes, timeout=8, chunk_size=50)
    fetcher._last_tencent_batch_meta = meta

    try:
        hkd_cny = fetcher._fetch_exchange_rates()["HKDCNY"]
    except Exception:
        hkd_cny = None

    def build_by_orig(orig: str, query: str, kind: str) -> Optional[Dict]:
        data = parts_map.get(query)
        if not data:
            return None

        if kind in ("cn", "hk"):
            if len(data) <= 45:
                return None
            price = float(data[3])
            payload = {
                "code": orig,
                "name": data[1] or name_map.get(orig) or orig,
                "price": price,
                "prev_close": float(data[4]) if data[4] else None,
                "open": float(data[5]) if data[5] else None,
                "high": float(data[33]) if data[33] else None,
                "low": float(data[34]) if data[34] else None,
                "change": float(data[31]) if data[31] else None,
                "change_pct": float(data[32]) if data[32] else None,
                "volume": float(data[36]) * 100 if len(data) > 36 and data[36] else 0,
                "time": data[30] if len(data) > 30 else None,
                "source": "tencent_batch",
            }
            if kind == "cn":
                payload.update({"currency": "CNY", "cny_price": price, "market_type": "cn"})
            elif hkd_cny:
                payload.update(
                    {
                        "currency": "HKD",
                        "cny_price": price * hkd_cny,
                        "exchange_rate": hkd_cny,
                        "market_type": "hk",
                    }
                )
            else:
                payload.update({"currency": "HKD", "market_type": "hk"})
            return fetcher._normalize_price_payload(payload)

        if kind == "fund":
            if len(data) < 6:
                return None
            try:
                nav = float(data[5])
            except Exception:
                return None
            if not nav or nav <= 0:
                return None
            return fetcher._normalize_price_payload(
                {
                    "code": orig,
                    "name": data[1] or name_map.get(orig) or orig,
                    "price": nav,
                    "currency": "CNY",
                    "cny_price": nav,
                    "market_type": "fund",
                    "source": "tencent_jj_batch",
                }
            )

        return None

    for orig, query in cn_query:
        result = build_by_orig(orig, query, "cn")
        if result:
            results[orig] = result
        else:
            leftover.append(orig)

    for orig, query in hk_query:
        result = build_by_orig(orig, query, "hk")
        if result:
            results[orig] = result
        else:
            leftover.append(orig)

    for orig, query in fund_query:
        result = build_by_orig(orig, query, "fund")
        if result:
            results[orig] = result
        else:
            leftover.append(orig)

    return results, leftover
