"""FX rate service with memory and file fallback cache."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from src import config
from src.time_utils import bj_now_naive


RATE_CACHE_FILE = config.get_data_dir() / "rate_cache.json"


class FxRateService:
    """Fetch USD/HKD to CNY rates with a 24-hour cache and stale fallback."""

    def __init__(self, session, cache_file: Optional[Path] = None):
        self.session = session
        self.cache_file = cache_file or (config.get_data_dir() / "rate_cache.json")
        self._rate_cache: Dict[str, float] = {}
        self._rate_cache_time: Optional[datetime] = None

    def load_cache_from_file(self) -> Optional[Dict]:
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return {
                    "rates": data.get("rates", {}),
                    "timestamp": data.get("timestamp"),
                }
        except (json.JSONDecodeError, IOError) as e:
            print(f"[警告] 加载汇率缓存文件失败: {e}")
        return None

    def save_cache_to_file(self, rates: Dict[str, float]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "rates": rates,
                "timestamp": bj_now_naive().isoformat(),
                "cached_at": bj_now_naive().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[警告] 保存汇率缓存文件失败: {e}")

    def fetch_exchange_rates(self, max_retries: int = 3) -> Dict[str, float]:
        now = bj_now_naive()

        if self._rate_cache_time and (now - self._rate_cache_time).total_seconds() < 86400:
            return self._rate_cache

        if not self._rate_cache_time:
            file_cache = self.load_cache_from_file()
            if file_cache and file_cache["timestamp"]:
                try:
                    cache_time = datetime.fromisoformat(file_cache["timestamp"])
                    if (now - cache_time).total_seconds() < 86400:
                        self._rate_cache = file_cache["rates"]
                        self._rate_cache_time = cache_time
                        print(
                            f"[汇率] 从文件加载缓存: USD/CNY={self._rate_cache.get('USDCNY')}, "
                            f"HKD/CNY={self._rate_cache.get('HKDCNY')}"
                        )
                        return self._rate_cache
                except (ValueError, TypeError):
                    pass

        def fetch_single_rate_with_fallback(currency: str) -> tuple:
            api_sources = [
                lambda: self._fetch_from_open_er_api(currency),
                lambda: self._fetch_from_exchangerate_api(currency),
                lambda: self._fetch_from_chinamoney(currency),
                lambda: self._fetch_from_exchangerate_host(currency),
            ]

            last_error = None
            for source_func in api_sources:
                for attempt in range(max_retries):
                    try:
                        rate = source_func()
                        if rate:
                            return currency, round(rate, 4), None
                    except Exception as e:
                        last_error = str(e)
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt)
                            continue
                        break
            return currency, None, f"所有API源失败: {last_error}"

        currencies = ["USD", "HKD"]
        results = {}
        errors = []

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {executor.submit(fetch_single_rate_with_fallback, c): c for c in currencies}
                for future in as_completed(futures):
                    currency, rate, error = future.result()
                    if error:
                        errors.append(f"{currency}: {error}")
                    else:
                        results[currency] = rate

            if len(results) != len(currencies):
                raise RuntimeError(f"获取汇率失败: {'; '.join(errors)}")

            self._rate_cache = {
                "USDCNY": results["USD"],
                "HKDCNY": results["HKD"],
            }
            self._rate_cache_time = now
            self.save_cache_to_file(self._rate_cache)
            print(f"[汇率] 已更新缓存: USD/CNY={self._rate_cache['USDCNY']}, HKD/CNY={self._rate_cache['HKDCNY']}")
            return self._rate_cache

        except Exception as e:
            fallback_cache = self._rate_cache or self.load_cache_from_file()
            if fallback_cache:
                rates = fallback_cache.get("rates", fallback_cache) if isinstance(fallback_cache, dict) else fallback_cache
                cache_time = self._rate_cache_time
                if not cache_time and isinstance(fallback_cache, dict):
                    try:
                        cache_time = datetime.fromisoformat(fallback_cache.get("timestamp", ""))
                    except (ValueError, TypeError):
                        cache_time = None

                if cache_time:
                    cache_age_hours = (now - cache_time).total_seconds() / 3600
                    age_str = f"{cache_age_hours:.1f}"
                else:
                    age_str = "未知"
                print(f"[⚠️ 警告] 获取实时汇率失败: {e}")
                print(f"[⚠️ 警告] 使用 {age_str} 小时前的过期汇率: USD/CNY={rates.get('USDCNY')}, HKD/CNY={rates.get('HKDCNY')}")

                self._rate_cache = rates
                self._rate_cache_time = cache_time or now
                return rates

            raise RuntimeError(f"获取汇率失败且没有可用缓存: {e}")

    def _fetch_from_open_er_api(self, currency: str) -> float:
        url = f"https://open.er-api.com/v6/latest/{currency}"
        response = self.session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success":
            raise ValueError(f"open.er-api 返回异常: {data}")
        return data["rates"]["CNY"]

    def _fetch_from_exchangerate_api(self, currency: str) -> float:
        url = f"https://api.exchangerate-api.com/v4/latest/{currency}"
        response = self.session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["rates"]["CNY"]

    def _fetch_from_chinamoney(self, currency: str) -> float:
        currency_pair = f"{currency}CNY"
        url = f"https://hq.sinajs.cn/list=fx_s{currency_pair.lower()}"
        response = self.session.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
        response.raise_for_status()
        content = response.text
        if "var hq_str_" in content:
            parts = content.split('"')[1].split(",")
            if len(parts) >= 8:
                buy = float(parts[0])
                sell = float(parts[2])
                return (buy + sell) / 2
        raise ValueError(f"无法解析新浪汇率数据: {content[:100]}")

    def _fetch_from_exchangerate_host(self, currency: str) -> float:
        url = f"https://api.exchangerate.host/convert?from={currency}&to=CNY&amount=1"
        response = self.session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data["result"]
