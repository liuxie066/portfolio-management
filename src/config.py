"""
统一配置管理

优先级：环境变量 > config.yaml > 默认值
"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

# 项目根目录（config.yaml 所在目录）
_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_FILE = _PROJECT_ROOT / "config.yaml"
CONFIG_FILE_ENV = "PORTFOLIO_CONFIG_FILE"

# 模块级缓存，避免重复读文件
_cached_config: Optional[dict] = None

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}

ENV_MAP = {
    "account": "PORTFOLIO_ACCOUNT",
    "data.dir": "PM_DATA_DIR",
    "service.host": "PORTFOLIO_SERVICE_HOST",
    "service.port": "PORTFOLIO_SERVICE_PORT",
    "service.url": "PORTFOLIO_SERVICE_URL",
    "nav.disable_runtime_validation": "PORTFOLIO_NAV_DISABLE_RUNTIME_VALIDATION",
    "report.account_label": "PM_REPORT_ACCOUNT_LABEL",
    "report.reports_dir": "PM_REPORTS_DIR",
    "report.publish_root": "PM_PUBLISH_ROOT",
    "report.sync_futu_cash_mmf": "PM_SYNC_FUTU_CASH_MMF",
    "report.sync_futu_dry_run": "PM_SYNC_FUTU_DRY_RUN",
    "report.disable_nav_runtime_validation": "PM_DISABLE_NAV_RUNTIME_VALIDATION",
    "calendar.holidays": "PM_BUSINESS_HOLIDAYS",
    "futu.opend.host": "FUTU_OPEND_HOST",
    "futu.opend.port": "FUTU_OPEND_PORT",
    "futu.trd_env": "FUTU_TRD_ENV",
    "futu.acc_id": "FUTU_ACC_ID",
    "futu.trd_market": "FUTU_TRD_MARKET",
    "futu.cash_currency": "FUTU_CASH_CURRENCY",
    "feishu.app_token": "FEISHU_APP_TOKEN",
    "feishu.app_id": "FEISHU_APP_ID",
    "feishu.app_secret": "FEISHU_APP_SECRET",
    "feishu.user_token": "FEISHU_USER_TOKEN",
    "feishu.connect_timeout": "FEISHU_CONNECT_TIMEOUT",
    "feishu.read_timeout": "FEISHU_READ_TIMEOUT",
    "feishu.receipt.app_id": "FEISHU_RECEIPT_APP_ID",
    "feishu.receipt.app_secret": "FEISHU_RECEIPT_APP_SECRET",
    "feishu.receipt.open_id": "FEISHU_RECEIPT_OPEN_ID",
    "feishu.tables.holdings": "FEISHU_TABLE_HOLDINGS",
    "feishu.tables.transactions": "FEISHU_TABLE_TRANSACTIONS",
    "feishu.tables.price_cache": "FEISHU_TABLE_PRICE_CACHE",
    "feishu.tables.nav_history": "FEISHU_TABLE_NAV_HISTORY",
    "feishu.tables.cash_flow": "FEISHU_TABLE_CASH_FLOW",
    "feishu.tables.holdings_snapshot": "FEISHU_TABLE_HOLDINGS_SNAPSHOT",
    "feishu.tables.compensation_tasks": "FEISHU_TABLE_COMPENSATION_TASKS",
    "feishu.tables.schema_version": "FEISHU_TABLE_SCHEMA_VERSION",
    "finnhub_api_key": "FINNHUB_API_KEY",
}

ENV_FALLBACKS = {
    "feishu.receipt.app_id": ("OM_FEISHU_BOT_APP_ID",),
    "feishu.receipt.app_secret": ("OM_FEISHU_BOT_APP_SECRET",),
    "feishu.receipt.open_id": ("OM_FEISHU_BOT_USER_OPEN_ID",),
}

OPERATOR_CONFIG_KEYS = (
    "account",
    "data.dir",
    "service.host",
    "service.port",
    "service.url",
    "calendar.holidays",
    "report.reports_dir",
    "report.publish_root",
    "report.sync_futu_cash_mmf",
    "futu.opend.host",
    "futu.opend.port",
    "futu.trd_env",
    "futu.acc_id",
    "futu.trd_market",
    "futu.cash_currency",
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.app_token",
    "feishu.connect_timeout",
    "feishu.read_timeout",
    "feishu.receipt.app_id",
    "feishu.receipt.app_secret",
    "feishu.receipt.open_id",
    "feishu.tables.holdings",
    "feishu.tables.nav_history",
    "feishu.tables.cash_flow",
    "feishu.tables.holdings_snapshot",
    "feishu.tables.transactions",
    "finnhub_api_key",
)

REQUIRED_DAILY_JOB_KEYS = (
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.tables.holdings",
    "feishu.tables.nav_history",
    "feishu.tables.cash_flow",
    "feishu.tables.holdings_snapshot",
)

SECRET_KEYS = {
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.user_token",
    "feishu.receipt.app_id",
    "feishu.receipt.app_secret",
    "feishu.receipt.open_id",
    "finnhub_api_key",
}

OPERATOR_DEFAULTS: Dict[str, Any] = {
    "account": "default",
    "data.dir": str(_PROJECT_ROOT / ".data"),
    "service.host": "127.0.0.1",
    "service.port": 8765,
    "service.url": "",
    "calendar.holidays": [],
    "report.reports_dir": "reports",
    "report.publish_root": "../prototypes",
    "report.sync_futu_cash_mmf": False,
    "futu.opend.host": "127.0.0.1",
    "futu.opend.port": 11111,
    "futu.trd_env": "REAL",
    "futu.trd_market": "HK",
    "futu.cash_currency": "CNH",
    "feishu.connect_timeout": 5.0,
    "feishu.read_timeout": 30.0,
}


def get_config_file() -> Path:
    """Return the active config file path.

    Linux deployments should set ``PORTFOLIO_CONFIG_FILE`` to keep secrets out
    of the checkout. Tests may still monkeypatch ``_CONFIG_FILE`` directly.
    """
    configured = os.environ.get(CONFIG_FILE_ENV)
    if configured:
        return Path(configured).expanduser()
    return _CONFIG_FILE


def _load_structured_config(config_file: Path) -> dict:
    suffix = config_file.suffix.lower()
    with open(config_file, "r", encoding="utf-8") as f:
        if suffix == ".json":
            loaded = json.load(f)
        else:
            loaded = yaml.safe_load(f)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("top-level config must be a mapping")
    return loaded


def _load_config_file() -> dict:
    """从 config.yaml 加载配置"""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    config_file = get_config_file()
    if config_file.exists():
        try:
            _cached_config = _load_structured_config(config_file)
        except (json.JSONDecodeError, yaml.YAMLError, ValueError, IOError) as e:
            print(f"[配置] 加载 {config_file} 失败: {e}")
            _cached_config = {}
    else:
        _cached_config = {}

    return _cached_config


def reload_config():
    """强制重新加载配置（测试用）"""
    global _cached_config
    _cached_config = None
    return _load_config_file()


def _get_from_file(key: str, default=None) -> tuple[Any, bool]:
    cfg = _load_config_file()
    parts = key.split(".")
    node: Any = cfg
    for part in parts:
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return default, False
    return (default, True) if node == "" else (node, True)


def get_with_source(key: str, default=None) -> tuple[Any, str]:
    env_keys = tuple(
        env_key
        for env_key in (ENV_MAP.get(key), *ENV_FALLBACKS.get(key, ()))
        if env_key
    )
    for env_key in env_keys:
        env_val = os.environ.get(env_key)
        if env_val not in (None, ""):
            return env_val, f"env:{env_key}"

    file_value, found = _get_from_file(key, default)
    if found:
        return file_value, f"file:{get_config_file()}"
    return default, "default"


def get(key: str, default=None):
    """获取配置值（支持点号分隔的嵌套 key）

    Args:
        key: 配置键名，支持 'feishu.app_token' 等嵌套路径
        default: 默认值

    Returns:
        配置值
    """
    value, _source = get_with_source(key, default)
    return value


def _redact_value(key: str, value: Any) -> Any:
    if value in (None, ""):
        return value
    if key in SECRET_KEYS:
        text = str(value)
        if len(text) <= 6:
            return "***"
        return f"{text[:3]}...{text[-3:]}"
    return value


def inspect_config(*, keys: Optional[Iterable[str]] = None, redact: bool = True) -> Dict[str, Any]:
    """Return operator-facing effective configuration with source metadata."""
    selected_keys = tuple(keys or OPERATOR_CONFIG_KEYS)
    values: Dict[str, Dict[str, Any]] = {}
    for key in selected_keys:
        value, source = get_with_source(key, OPERATOR_DEFAULTS.get(key))
        values[key] = {
            "value": _redact_value(key, value) if redact else value,
            "source": source,
            "env": ENV_MAP.get(key),
            "env_fallbacks": list(ENV_FALLBACKS.get(key, ())),
            "set": source != "default" and value not in (None, ""),
        }
    return {
        "success": True,
        "config_file": str(get_config_file()),
        "config_format": get_config_file().suffix.lower().lstrip(".") or "yaml",
        "config_file_exists": get_config_file().exists(),
        "config_file_env": CONFIG_FILE_ENV,
        "values": values,
    }


def validate_deploy_config(*, require_futu: bool = False) -> Dict[str, Any]:
    """Validate configuration needed by scheduled daily NAV jobs."""
    issues = []
    warnings = []

    for key in REQUIRED_DAILY_JOB_KEYS:
        value = get(key)
        if value in (None, ""):
            issues.append({"key": key, "error": "missing required value", "env": ENV_MAP.get(key)})

    app_token = get("feishu.app_token")
    for key in (
        "feishu.tables.holdings",
        "feishu.tables.nav_history",
        "feishu.tables.cash_flow",
        "feishu.tables.holdings_snapshot",
    ):
        value = get(key)
        if value and "/" not in str(value) and not app_token:
            issues.append({
                "key": key,
                "error": "table id requires feishu.app_token unless value is app_token/table_id",
                "env": ENV_MAP.get(key),
            })

    if require_futu:
        if not get("futu.opend.host"):
            issues.append({"key": "futu.opend.host", "error": "missing Futu OpenD host", "env": ENV_MAP.get("futu.opend.host")})
        if get_int("futu.opend.port") is None:
            issues.append({"key": "futu.opend.port", "error": "missing or invalid Futu OpenD port", "env": ENV_MAP.get("futu.opend.port")})
        for key in (
            "feishu.receipt.app_id",
            "feishu.receipt.app_secret",
            "feishu.receipt.open_id",
        ):
            if not get(key):
                issues.append({"key": key, "error": "missing Futu sync receipt config", "env": ENV_MAP.get(key)})
        try:
            __import__("futu")
        except Exception:
            try:
                __import__("moomoo")
            except Exception:
                warnings.append({"key": "futu.sdk", "warning": "futu/moomoo SDK is not importable; Futu sync will fail unless installed"})

    return {
        "success": not issues,
        "config_file": str(get_config_file()),
        "config_format": get_config_file().suffix.lower().lstrip(".") or "yaml",
        "config_file_exists": get_config_file().exists(),
        "issues": issues,
        "warnings": warnings,
        "required_keys": list(REQUIRED_DAILY_JOB_KEYS),
    }


def get_bool(key: str, default: bool = False) -> bool:
    """获取布尔配置值，支持 env/config 中常见字符串表示。"""
    value = get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0

    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES or normalized == "":
        return False
    return default


def get_int(key: str, default: Optional[int] = None) -> Optional[int]:
    """获取整数配置值；缺失或无法解析时返回 default。"""
    value = get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: Optional[float] = None) -> Optional[float]:
    """获取浮点配置值；缺失、非有限或非正数时返回 default。"""
    value = get(key)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0 or parsed != parsed or parsed in (float("inf"), float("-inf")):
        return default
    return parsed


# ========== 常用配置的便捷访问 ==========

def get_account() -> str:
    """获取默认账户标识"""
    return get("account", "default")


def get_initial_value() -> float:
    """获取初始账户净值（净值=1 时的总资产）"""
    val = get("initial_value")
    return float(val) if val is not None else 0.0


def get_start_year() -> int:
    """获取收益统计起始年份"""
    return get_int("start_year", 2024) or 2024


def get_data_dir() -> Path:
    """获取数据目录（.data/）"""
    configured = get("data.dir")
    data_dir = Path(configured).expanduser() if configured else (_PROJECT_ROOT / ".data")
    if not data_dir.is_absolute():
        data_dir = _PROJECT_ROOT / data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_service_host() -> str:
    """获取本地 HTTP 服务监听地址。"""
    return str(get("service.host", "127.0.0.1"))


def get_service_port() -> int:
    """获取本地 HTTP 服务端口。"""
    return get_int("service.port", 8765) or 8765


def get_service_url() -> str:
    """获取本地 HTTP 服务 URL。"""
    configured = get("service.url")
    if configured:
        return str(configured).rstrip("/")
    return f"http://{get_service_host()}:{get_service_port()}"
