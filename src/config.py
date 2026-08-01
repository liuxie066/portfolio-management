"""
统一配置管理

优先级：环境变量 > config.yaml > 默认值
"""
import json
import os
import hashlib
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from src.configuration.feishu_credentials import (
    BITABLE_APP_SECRET_CREDENTIAL,
    CONVERSATION_APP_SECRET_CREDENTIAL,
    FeishuCredentialConfigError,
    read_systemd_credential,
    secure_feishu_credentials_required,
)

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
    "cash_flow.effects.cutover_date": "PM_CASH_FLOW_EFFECTS_CUTOVER_DATE",
    "cash_flow.effects.db_path": "PM_CASH_FLOW_EFFECTS_DB_PATH",
    "service.host": "PORTFOLIO_SERVICE_HOST",
    "service.port": "PORTFOLIO_SERVICE_PORT",
    "service.url": "PORTFOLIO_SERVICE_URL",
    "quality.read_token": "PM_QUALITY_READ_TOKEN",
    "quality.instance_id": "PM_QUALITY_INSTANCE_ID",
    "quality.accounts": "PM_QUALITY_ACCOUNTS",
    "quality.onboarded": "PM_QUALITY_ONBOARDED",
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
    "feishu.bitable.app_id": "FEISHU_BITABLE_APP_ID",
    "feishu.bitable.app_secret": "FEISHU_BITABLE_APP_SECRET",
    "feishu.conversation.app_id": "FEISHU_CONVERSATION_APP_ID",
    "feishu.conversation.app_secret": "FEISHU_CONVERSATION_APP_SECRET",
    "feishu.conversation.open_id": "FEISHU_CONVERSATION_OPEN_ID",
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

CANONICAL_CONFIG_ALIASES = {
    "feishu.bitable.app_id": ("feishu.app_id",),
    "feishu.bitable.app_secret": ("feishu.app_secret",),
    "feishu.conversation.app_id": ("feishu.receipt.app_id",),
    "feishu.conversation.app_secret": ("feishu.receipt.app_secret",),
    "feishu.conversation.open_id": ("feishu.receipt.open_id",),
}

FEISHU_SECRET_CREDENTIALS = {
    "feishu.bitable.app_secret": BITABLE_APP_SECRET_CREDENTIAL,
    "feishu.conversation.app_secret": CONVERSATION_APP_SECRET_CREDENTIAL,
}

_CANONICAL_FOR_ALIAS = {
    alias: canonical
    for canonical, aliases in CANONICAL_CONFIG_ALIASES.items()
    for alias in aliases
}

OPERATOR_CONFIG_KEYS = (
    "account",
    "data.dir",
    "cash_flow.effects.cutover_date",
    "cash_flow.effects.db_path",
    "service.host",
    "service.port",
    "service.url",
    "quality.read_token",
    "quality.instance_id",
    "quality.accounts",
    "quality.onboarded",
    "calendar.holidays",
    "report.reports_dir",
    "report.publish_root",
    "report.sync_futu_cash_mmf",
    "futu.opend.host",
    "futu.opend.port",
    "futu.profiles",
    "feishu.bitable.app_id",
    "feishu.bitable.app_secret",
    "feishu.app_token",
    "feishu.connect_timeout",
    "feishu.read_timeout",
    "feishu.conversation.app_id",
    "feishu.conversation.app_secret",
    "feishu.conversation.open_id",
    "feishu.tables.holdings",
    "feishu.tables.nav_history",
    "feishu.tables.cash_flow",
    "feishu.tables.holdings_snapshot",
    "feishu.tables.transactions",
    "finnhub_api_key",
)

REQUIRED_DAILY_JOB_KEYS = (
    "feishu.bitable.app_id",
    "feishu.bitable.app_secret",
    "feishu.tables.holdings",
    "feishu.tables.nav_history",
    "feishu.tables.cash_flow",
    "feishu.tables.holdings_snapshot",
)

REQUIRED_CONVERSATION_KEYS = (
    "feishu.conversation.app_id",
    "feishu.conversation.app_secret",
    "feishu.conversation.open_id",
)

SECRET_KEYS = {
    "feishu.bitable.app_id",
    "feishu.bitable.app_secret",
    "feishu.conversation.app_id",
    "feishu.conversation.app_secret",
    "feishu.conversation.open_id",
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.user_token",
    "feishu.receipt.app_id",
    "feishu.receipt.app_secret",
    "feishu.receipt.open_id",
    "finnhub_api_key",
    "quality.read_token",
}

NON_DISCLOSABLE_KEYS = {
    "feishu.bitable.app_id",
    "feishu.bitable.app_secret",
    "feishu.conversation.app_id",
    "feishu.conversation.app_secret",
    "feishu.conversation.open_id",
    "feishu.app_id",
    "feishu.app_secret",
    "feishu.receipt.app_id",
    "feishu.receipt.app_secret",
    "feishu.receipt.open_id",
}

OPERATOR_DEFAULTS: Dict[str, Any] = {
    "account": "default",
    "data.dir": str(_PROJECT_ROOT / ".data"),
    "service.host": "127.0.0.1",
    "service.port": 8765,
    "service.url": "",
    "quality.instance_id": "portfolio-management-local",
    "quality.accounts": [],
    "quality.onboarded": False,
    "calendar.holidays": [],
    "report.reports_dir": "reports",
    "report.publish_root": "../prototypes",
    "report.sync_futu_cash_mmf": False,
    "futu.opend.host": "127.0.0.1",
    "futu.opend.port": 11111,
    "futu.profiles": {},
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


def _has_file_key(key: str) -> bool:
    node: Any = _load_config_file()
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _direct_candidates(
    key: str,
    *,
    default=None,
    legacy: bool = False,
) -> list[tuple[Any, str]]:
    candidates: list[tuple[Any, str]] = []
    for env_key in (ENV_MAP.get(key), *ENV_FALLBACKS.get(key, ())):
        if not env_key:
            continue
        value = os.environ.get(env_key)
        if value not in (None, ""):
            prefix = "legacy-env" if legacy else "env"
            candidates.append((value, f"{prefix}:{env_key}"))

    file_value, found = _get_from_file(key, default)
    if found and file_value not in (None, ""):
        prefix = "legacy-file" if legacy else "file"
        candidates.append((file_value, f"{prefix}:{get_config_file()}"))
    return candidates


def _plaintext_shadow_sources(canonical_key: str) -> list[str]:
    sources: list[str] = []
    for key in (canonical_key, *CANONICAL_CONFIG_ALIASES.get(canonical_key, ())):
        for env_key in (ENV_MAP.get(key), *ENV_FALLBACKS.get(key, ())):
            if env_key and env_key in os.environ:
                sources.append(f"environment:{env_key}")
        if _has_file_key(key):
            sources.append(f"config:{key}")
    return sorted(set(sources))


def _resolve_canonical_non_secret(key: str, default=None) -> tuple[Any, str]:
    canonical_candidates = _direct_candidates(key, default=default)
    legacy_candidates = [
        candidate
        for alias in CANONICAL_CONFIG_ALIASES.get(key, ())
        for candidate in _direct_candidates(alias, default=default, legacy=True)
    ]
    if canonical_candidates:
        selected_value, selected_source = canonical_candidates[0]
        if any(str(value) != str(selected_value) for value, _ in legacy_candidates):
            raise FeishuCredentialConfigError(
                "conflicting_role_configuration",
                key,
            )
        return selected_value, selected_source
    if legacy_candidates:
        return legacy_candidates[0]
    return default, "default"


def _resolve_canonical_secret(
    key: str,
    default=None,
    *,
    secure_override: Optional[bool] = None,
) -> tuple[Any, str]:
    credential_name = FEISHU_SECRET_CREDENTIALS[key]
    credential, found = read_systemd_credential(
        key=key,
        credential_name=credential_name,
    )
    if found:
        return credential, f"credential:{credential_name}"

    secure_required = (
        secure_feishu_credentials_required()
        if secure_override is None
        else bool(secure_override)
    )
    if secure_required:
        code = (
            "insecure_secret_source"
            if _plaintext_shadow_sources(key)
            else "missing_secure_credential"
        )
        raise FeishuCredentialConfigError(code, key)

    canonical_candidates = _direct_candidates(key, default=default)
    legacy_candidates = [
        candidate
        for alias in CANONICAL_CONFIG_ALIASES.get(key, ())
        for candidate in _direct_candidates(alias, default=default, legacy=True)
    ]
    plaintext_candidates = [*canonical_candidates, *legacy_candidates]
    if plaintext_candidates:
        return plaintext_candidates[0]
    return default, "default"


def _resolve_with_source(
    key: str,
    default=None,
    *,
    secure_override: Optional[bool] = None,
) -> tuple[Any, str]:
    canonical_key = _CANONICAL_FOR_ALIAS.get(key, key)
    if canonical_key in FEISHU_SECRET_CREDENTIALS:
        return _resolve_canonical_secret(
            canonical_key,
            default,
            secure_override=secure_override,
        )
    if canonical_key in CANONICAL_CONFIG_ALIASES:
        return _resolve_canonical_non_secret(canonical_key, default)

    candidates = _direct_candidates(key, default=default)
    if candidates:
        return candidates[0]
    return default, "default"


def get_with_source(key: str, default=None) -> tuple[Any, str]:
    return _resolve_with_source(key, default)


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


def get_futu_profile(account: str) -> Dict[str, Any]:
    """Return one explicit PM-account -> OpenD profile mapping.

    Cash effects and scheduled stock synchronization share this mapping.  No
    environment-based account switching or default-profile fallback is allowed.
    """
    profiles = get("futu.profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("futu.profiles must be a mapping keyed by PM account")
    raw = profiles.get(str(account))
    if not isinstance(raw, dict):
        raise ValueError(f"missing futu.profiles mapping for account={account}")
    required = ("host", "port", "acc_id", "trd_env", "trd_market")
    missing = [key for key in required if raw.get(key) in (None, "")]
    if missing:
        raise ValueError(
            f"incomplete futu.profiles.{account}: missing {', '.join(missing)}"
        )
    profile = {
        "host": str(raw["host"]).strip(),
        "port": int(raw["port"]),
        "acc_id": int(raw["acc_id"]),
        "trd_env": str(raw["trd_env"]).upper(),
        "trd_market": str(raw["trd_market"]).upper(),
    }
    if not profile["host"]:
        raise ValueError(f"futu.profiles.{account}.host must be explicit")
    if profile["port"] <= 0:
        raise ValueError(f"futu.profiles.{account}.port must be positive")
    if profile["acc_id"] <= 0:
        raise ValueError(f"futu.profiles.{account}.acc_id must be positive")
    return profile


def _futu_profile_fingerprint(profile: Dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            "host": profile["host"],
            "port": profile["port"],
            "acc_id": profile["acc_id"],
            "trd_env": profile["trd_env"],
            "trd_market": profile["trd_market"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _redact_value(key: str, value: Any) -> Any:
    if value in (None, ""):
        return value
    if key in SECRET_KEYS:
        text = str(value)
        if len(text) <= 6:
            return "***"
        return f"{text[:3]}...{text[-3:]}"
    return value


def _source_warnings(key: str, source: str) -> list[Dict[str, Any]]:
    canonical_key = _CANONICAL_FOR_ALIAS.get(key, key)
    if (
        canonical_key in CANONICAL_CONFIG_ALIASES
        and canonical_key not in FEISHU_SECRET_CREDENTIALS
    ):
        canonical_candidates = _direct_candidates(canonical_key)
        legacy_candidates = [
            candidate
            for alias in CANONICAL_CONFIG_ALIASES[canonical_key]
            for candidate in _direct_candidates(alias, legacy=True)
        ]
        if canonical_candidates and legacy_candidates:
            return [
                {
                    "key": canonical_key,
                    "warning": "redundant_legacy_configuration",
                    "sources": sorted(
                        {candidate_source for _, candidate_source in legacy_candidates}
                    ),
                }
            ]
    if canonical_key not in FEISHU_SECRET_CREDENTIALS:
        return []
    if source.startswith("credential:"):
        shadows = _plaintext_shadow_sources(canonical_key)
        if shadows:
            return [
                {
                    "key": canonical_key,
                    "warning": "plaintext_shadow_detected",
                    "sources": shadows,
                }
            ]
        return []
    if source != "default":
        return [
            {
                "key": canonical_key,
                "warning": "plaintext_secret_source",
                "source": source,
            }
        ]
    return []


def inspect_config(*, keys: Optional[Iterable[str]] = None, redact: bool = True) -> Dict[str, Any]:
    """Return operator-facing effective configuration with source metadata."""
    selected_keys = tuple(keys or OPERATOR_CONFIG_KEYS)
    values: Dict[str, Dict[str, Any]] = {}
    issues: list[Dict[str, Any]] = []
    warnings: list[Dict[str, Any]] = []
    for key in selected_keys:
        try:
            value, source = get_with_source(key, OPERATOR_DEFAULTS.get(key))
        except FeishuCredentialConfigError as exc:
            issues.append(exc.as_issue())
            values[key] = {
                "value": None,
                "source": "error",
                "env": ENV_MAP.get(key),
                "env_fallbacks": list(ENV_FALLBACKS.get(key, ())),
                "set": False,
                "error": exc.code,
            }
            continue
        warnings.extend(_source_warnings(key, source))
        must_redact = redact or key in NON_DISCLOSABLE_KEYS
        values[key] = {
            "value": _redact_value(key, value) if must_redact else value,
            "source": source,
            "env": ENV_MAP.get(key),
            "env_fallbacks": list(ENV_FALLBACKS.get(key, ())),
            "set": source != "default" and value not in (None, ""),
        }
    return {
        "success": not issues,
        "config_file": str(get_config_file()),
        "config_format": get_config_file().suffix.lower().lstrip(".") or "yaml",
        "config_file_exists": get_config_file().exists(),
        "config_file_env": CONFIG_FILE_ENV,
        "values": values,
        "issues": issues,
        "warnings": warnings,
    }


def validate_deploy_config(
    *,
    require_futu: bool = False,
    require_quality: bool = False,
    require_secure_feishu: bool = False,
) -> Dict[str, Any]:
    """Validate configuration needed by scheduled daily NAV jobs."""
    issues = []
    warnings = []

    try:
        environment_secure_mode = secure_feishu_credentials_required()
        secure_mode_valid = True
    except FeishuCredentialConfigError as exc:
        issues.append(exc.as_issue())
        environment_secure_mode = True
        secure_mode_valid = False
    effective_secure_mode = bool(require_secure_feishu or environment_secure_mode)

    def resolved(key: str, default=None):
        try:
            value, source = _resolve_with_source(
                key,
                default,
                secure_override=effective_secure_mode,
            )
        except FeishuCredentialConfigError as exc:
            if not any(item.get("key") == exc.key for item in issues):
                issues.append(exc.as_issue())
            return default
        warnings.extend(_source_warnings(key, source))
        return value

    for key in REQUIRED_DAILY_JOB_KEYS:
        value = resolved(key)
        if value in (None, ""):
            if not any(item.get("key") == key for item in issues):
                issues.append({"key": key, "error": "missing required value", "env": ENV_MAP.get(key)})

    if require_secure_feishu:
        for key in REQUIRED_CONVERSATION_KEYS:
            value = resolved(key)
            if value in (None, "") and not any(
                item.get("key") == key for item in issues
            ):
                issues.append(
                    {
                        "key": key,
                        "error": "missing required value",
                        "env": ENV_MAP.get(key),
                    }
                )

    app_token = resolved("feishu.app_token")
    for key in (
        "feishu.tables.holdings",
        "feishu.tables.nav_history",
        "feishu.tables.cash_flow",
        "feishu.tables.holdings_snapshot",
    ):
        value = resolved(key)
        if value and "/" not in str(value) and not app_token:
            issues.append({
                "key": key,
                "error": "table id requires feishu.app_token unless value is app_token/table_id",
                "env": ENV_MAP.get(key),
            })

    if require_futu:
        profiles = get("futu.profiles")
        if not isinstance(profiles, dict) or not profiles:
            issues.append({
                "key": "futu.profiles",
                "error": "missing explicit PM account -> Futu OpenD profiles",
                "env": None,
            })
        else:
            for account in sorted(profiles):
                try:
                    get_futu_profile(str(account))
                except (TypeError, ValueError) as exc:
                    issues.append({
                        "key": f"futu.profiles.{account}",
                        "error": str(exc),
                        "env": None,
                    })
        for key in REQUIRED_CONVERSATION_KEYS:
            if not resolved(key) and not any(
                item.get("key") == key for item in issues
            ):
                issues.append({"key": key, "error": "missing Futu sync receipt config", "env": ENV_MAP.get(key)})
        try:
            __import__("futu")
        except Exception:
            try:
                __import__("moomoo")
            except Exception:
                warnings.append({"key": "futu.sdk", "warning": "futu/moomoo SDK is not importable; Futu sync will fail unless installed"})
        mapping_result = validate_futu_account_mappings(get_quality_accounts())
        issues.extend(
            {
                "key": f"futu.profiles.{item['account']}",
                "error": item["error"],
                "env": None,
            }
            for item in mapping_result["issues"]
        )

    if require_quality:
        if not resolved("quality.read_token"):
            issues.append({
                "key": "quality.read_token",
                "error": "missing quality read token",
                "env": ENV_MAP.get("quality.read_token"),
            })
        if not resolved("quality.accounts"):
            issues.append({
                "key": "quality.accounts",
                "error": "at least one quality account is required",
                "env": ENV_MAP.get("quality.accounts"),
            })

    return {
        "success": not issues,
        "config_file": str(get_config_file()),
        "config_format": get_config_file().suffix.lower().lstrip(".") or "yaml",
        "config_file_exists": get_config_file().exists(),
        "issues": issues,
        "warnings": warnings,
        "required_keys": list(REQUIRED_DAILY_JOB_KEYS),
        "secure_feishu_required": bool(
            effective_secure_mode
        ),
        "secure_feishu_mode_valid": secure_mode_valid,
    }


def get_futu_account_settings(account: str) -> Dict[str, Any]:
    """Return quality evidence settings from the canonical Futu profile."""
    normalized = str(account or "").strip().lower()
    if not normalized or not normalized.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid portfolio account label")
    profile = get_futu_profile(normalized)
    acc_id = int(profile["acc_id"])
    if acc_id <= 0:
        raise ValueError(f"futu.profiles.{normalized}.acc_id must be positive")
    trd_env = str(profile["trd_env"]).upper()
    if trd_env != "REAL":
        raise ValueError(f"futu.profiles.{normalized}.trd_env must be REAL")
    trd_market = str(profile["trd_market"]).upper()
    if not trd_market:
        raise ValueError(f"futu.profiles.{normalized}.trd_market must be explicit")
    account_fingerprint = (
        f"sha256:{hashlib.sha256(str(acc_id).encode()).hexdigest()}"
    )
    return {
        "account": normalized,
        "acc_id": acc_id,
        "account_fingerprint": account_fingerprint,
        "profile_fingerprint": _futu_profile_fingerprint(profile),
        "host": profile["host"],
        "port": profile["port"],
        "trd_env": trd_env,
        "trd_market": trd_market,
    }


def validate_futu_account_mappings(accounts: Iterable[str]) -> Dict[str, Any]:
    issues = []
    mappings = []
    seen: Dict[int, str] = {}
    for account in accounts:
        try:
            settings = get_futu_account_settings(account)
        except ValueError as exc:
            issues.append({"account": str(account).lower(), "error": str(exc)})
            continue
        acc_id = settings["acc_id"]
        if acc_id in seen:
            issues.append({
                "account": settings["account"],
                "error": f"acc_id duplicates account {seen[acc_id]}",
            })
        else:
            seen[acc_id] = settings["account"]
        mappings.append({
            key: value
            for key, value in settings.items()
            if key != "acc_id"
        })
    return {
        "success": not issues,
        "mappings": mappings,
        "issues": issues,
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


def get_quality_accounts() -> list[str]:
    value = get("quality.accounts")
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = []
    accounts = [
        str(item or "").strip().lower()
        for item in raw
        if str(item or "").strip()
    ]
    return list(dict.fromkeys(accounts)) or [get_account().strip().lower()]


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


def get_feishu_table_ref(table_name: str) -> tuple[str, str]:
    """Resolve one configured Base table without creating a network client."""

    resolved_name = str(table_name or "").strip()
    if not resolved_name:
        raise ValueError("Feishu table name is required")
    raw_value = str(get(f"feishu.tables.{resolved_name}") or "").strip()
    if not raw_value:
        raise ValueError(f"missing Feishu table configuration: {resolved_name}")
    if "/" in raw_value:
        parts = raw_value.split("/")
        if len(parts) != 2 or not all(part.strip() for part in parts):
            raise ValueError(
                f"invalid Feishu table reference for {resolved_name}; "
                "expected app_token/table_id"
            )
        app_token, table_id = (part.strip() for part in parts)
    else:
        app_token = str(get("feishu.app_token") or "").strip()
        table_id = raw_value
    if not app_token:
        raise ValueError(
            f"missing feishu.app_token for table configuration: {resolved_name}"
        )
    return app_token, table_id
