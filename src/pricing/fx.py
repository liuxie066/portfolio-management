"""FX rate service with memory and file fallback cache."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from src import config
from src.time_utils import bj_now_naive

from .payload import positive_finite_decimal, remaining_timeout, sleep_with_deadline


RATE_CACHE_FILE = config.get_data_dir() / "rate_cache.json"
_REQUIRED_RATES = ("USDCNY", "HKDCNY")


class FxRateService:
    """Fetch USD/HKD to CNY rates with a 24-hour cache and stale fallback."""

    def __init__(self, session, cache_file: Optional[Path] = None):
        self.session = session
        self.cache_file = cache_file or (config.get_data_dir() / "rate_cache.json")
        self._rate_cache: Dict[str, float] = {}
        self._rate_cache_time: Optional[datetime] = None

    @staticmethod
    def _validated_rates(rates) -> Optional[Dict[str, float]]:
        if not isinstance(rates, dict):
            return None
        try:
            return {
                key: float(positive_finite_decimal(rates[key], key))
                for key in _REQUIRED_RATES
            }
        except (KeyError, TypeError, ValueError):
            return None

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
        validated = self._validated_rates(rates)
        if validated is None:
            raise ValueError("cannot persist invalid FX rates")
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            now = bj_now_naive()
            data = {
                "rates": validated,
                "timestamp": now.isoformat(),
                "cached_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[警告] 保存汇率缓存文件失败: {e}")

    def fetch_exchange_rates(self, max_retries: int = 3, *, deadline: float | None = None) -> Dict[str, float]:
        now = bj_now_naive()
        memory_rates = self._validated_rates(self._rate_cache)
        if self._rate_cache_time and memory_rates is not None:
            if (now - self._rate_cache_time).total_seconds() < 86400:
                self._rate_cache = memory_rates
                return dict(memory_rates)
        elif self._rate_cache_time:
            self._rate_cache_time = None

        file_cache = self.load_cache_from_file() if not self._rate_cache_time else None
        file_rates = self._validated_rates(file_cache.get("rates")) if file_cache else None
        file_cache_time = None
        if file_cache and file_cache.get("timestamp"):
            try:
                file_cache_time = datetime.fromisoformat(file_cache["timestamp"])
            except (ValueError, TypeError):
                file_cache_time = None
        if file_rates is not None and file_cache_time is not None:
            if (now - file_cache_time).total_seconds() < 86400:
                self._rate_cache = file_rates
                self._rate_cache_time = file_cache_time
                print(
                    f"[汇率] 从文件加载缓存: USD/CNY={file_rates['USDCNY']}, "
                    f"HKD/CNY={file_rates['HKDCNY']}"
                )
                return dict(file_rates)

        def fetch_single_rate(currency: str) -> float:
            sources = (
                self._fetch_from_open_er_api,
                self._fetch_from_exchangerate_api,
                self._fetch_from_chinamoney,
                self._fetch_from_exchangerate_host,
            )
            last_error: Exception | None = None
            for source in sources:
                for attempt in range(max_retries):
                    try:
                        rate = source(currency, deadline=deadline)
                        return float(positive_finite_decimal(rate, f"{currency}CNY"))
                    except Exception as exc:
                        last_error = exc
                        if attempt < max_retries - 1:
                            sleep_with_deadline(2 ** attempt, deadline)
            raise RuntimeError(f"all FX sources failed: {last_error}")

        try:
            rates = {
                "USDCNY": round(fetch_single_rate("USD"), 4),
                "HKDCNY": round(fetch_single_rate("HKD"), 4),
            }
            validated = self._validated_rates(rates)
            if validated is None:
                raise RuntimeError("FX providers returned invalid rates")
            self._rate_cache = validated
            self._rate_cache_time = now
            self.save_cache_to_file(validated)
            print(f"[汇率] 已更新缓存: USD/CNY={validated['USDCNY']}, HKD/CNY={validated['HKDCNY']}")
            return dict(validated)
        except Exception as exc:
            fallback_rates = memory_rates or file_rates
            fallback_time = self._rate_cache_time if memory_rates is not None else file_cache_time
            if fallback_rates is not None:
                age_str = "未知"
                if fallback_time is not None:
                    age_str = f"{(now - fallback_time).total_seconds() / 3600:.1f}"
                print(f"[⚠️ 警告] 获取实时汇率失败: {exc}")
                print(
                    f"[⚠️ 警告] 使用 {age_str} 小时前的过期汇率: "
                    f"USD/CNY={fallback_rates['USDCNY']}, HKD/CNY={fallback_rates['HKDCNY']}"
                )
                self._rate_cache = fallback_rates
                self._rate_cache_time = fallback_time
                return dict(fallback_rates)
            self._rate_cache_time = None
            raise RuntimeError(f"获取汇率失败且没有可用缓存: {exc}") from exc

    def _fetch_from_open_er_api(self, currency: str, *, deadline: float | None = None) -> float:
        url = f"https://open.er-api.com/v6/latest/{currency}"
        response = self.session.get(url, timeout=remaining_timeout(deadline, 10))
        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success":
            raise ValueError(f"open.er-api 返回异常: {data}")
        return data["rates"]["CNY"]

    def _fetch_from_exchangerate_api(self, currency: str, *, deadline: float | None = None) -> float:
        url = f"https://api.exchangerate-api.com/v4/latest/{currency}"
        response = self.session.get(url, timeout=remaining_timeout(deadline, 10))
        response.raise_for_status()
        return response.json()["rates"]["CNY"]

    def _fetch_from_chinamoney(self, currency: str, *, deadline: float | None = None) -> float:
        currency_pair = f"{currency}CNY"
        url = f"https://hq.sinajs.cn/list=fx_s{currency_pair.lower()}"
        response = self.session.get(
            url,
            timeout=remaining_timeout(deadline, 10),
            headers={"Referer": "https://finance.sina.com.cn"},
        )
        response.raise_for_status()
        content = response.text
        if "var hq_str_" in content:
            parts = content.split('"')[1].split(",")
            if len(parts) >= 8:
                return (float(parts[0]) + float(parts[2])) / 2
        raise ValueError(f"无法解析新浪汇率数据: {content[:100]}")

    def _fetch_from_exchangerate_host(self, currency: str, *, deadline: float | None = None) -> float:
        url = f"https://api.exchangerate.host/convert?from={currency}&to=CNY&amount=1"
        response = self.session.get(url, timeout=remaining_timeout(deadline, 10))
        response.raise_for_status()
        return response.json()["result"]
