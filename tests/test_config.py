from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from pytest import MonkeyPatch

from src import config


def _clear_env(patch: MonkeyPatch, *keys: str) -> None:
    for key in keys:
        env_key = config.ENV_MAP.get(key)
        if env_key:
            patch.delenv(env_key, raising=False)


def test_config_typed_getters_use_yaml_file_then_env_overrides():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(
            """
report:
  sync_futu_cash_mmf: false
futu:
  opend:
    port: 1234
nav:
  disable_runtime_validation: true
""",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setattr(config, "_CONFIG_FILE", config_file)
            patch.delenv(config.CONFIG_FILE_ENV, raising=False)
            patch.delenv("PM_SYNC_FUTU_CASH_MMF", raising=False)
            patch.delenv("FUTU_OPEND_PORT", raising=False)
            patch.delenv("PORTFOLIO_NAV_DISABLE_RUNTIME_VALIDATION", raising=False)
            config.reload_config()

            assert config.get_bool("report.sync_futu_cash_mmf", True) is False
            assert config.get_int("futu.opend.port") == 1234
            assert config.get_bool("nav.disable_runtime_validation", False) is True

            patch.setenv("PM_SYNC_FUTU_CASH_MMF", "1")
            patch.setenv("FUTU_OPEND_PORT", "2222")
            patch.setenv("PORTFOLIO_NAV_DISABLE_RUNTIME_VALIDATION", "0")

            assert config.get_bool("report.sync_futu_cash_mmf", False) is True
            assert config.get_int("futu.opend.port") == 2222
            assert config.get_bool("nav.disable_runtime_validation", True) is False

            patch.setenv("FUTU_OPEND_PORT", "not-an-int")
            assert config.get_int("futu.opend.port", 99) == 99
        finally:
            patch.undo()
            config.reload_config()


def test_config_file_env_can_point_to_legacy_json_for_migration():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "legacy.json"
        config_file.write_text(json.dumps({"account": "legacy"}), encoding="utf-8")

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.delenv("PORTFOLIO_ACCOUNT", raising=False)
            config.reload_config()

            assert config.get_account() == "legacy"
            value, source = config.get_with_source("account")
            assert value == "legacy"
            assert source == f"file:{config_file}"
        finally:
            patch.undo()
            config.reload_config()


def test_inspect_config_redacts_values_and_reports_sources():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(
            """
account: lx
data:
  dir: /var/lib/portfolio-management/.data
feishu:
  app_id: cli_abc123456
  app_secret: secret123456
""",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            _clear_env(patch, "account", "data.dir", "feishu.app_id", "feishu.app_secret")
            config.reload_config()

            payload = config.inspect_config(keys=["account", "data.dir", "feishu.app_secret"])
            assert payload["success"] is True
            assert payload["config_format"] == "yaml"
            assert payload["values"]["account"]["value"] == "lx"
            assert payload["values"]["data.dir"]["value"] == "/var/lib/portfolio-management/.data"
            assert payload["values"]["feishu.app_secret"]["value"] == "sec...456"
            assert payload["values"]["feishu.app_secret"]["source"] == f"file:{config_file}"
        finally:
            patch.undo()
            config.reload_config()


def test_validate_deploy_config_accepts_complete_yaml_config():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(
            """
feishu:
  app_id: cli_abc
  app_secret: secret
  app_token: appToken
  tables:
    holdings: appToken/tbl_holdings
    nav_history: appToken/tbl_nav
    cash_flow: appToken/tbl_cash
    holdings_snapshot: appToken/tbl_snapshot
""",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            _clear_env(
                patch,
                "feishu.app_id",
                "feishu.app_secret",
                "feishu.app_token",
                "feishu.tables.holdings",
                "feishu.tables.nav_history",
                "feishu.tables.cash_flow",
                "feishu.tables.holdings_snapshot",
            )
            config.reload_config()

            payload = config.validate_deploy_config()
            assert payload["success"] is True
            assert payload["issues"] == []
        finally:
            patch.undo()
            config.reload_config()


def test_data_dir_can_be_configured_from_yaml():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        configured_data = root / "state"
        config_file = root / "config.yaml"
        config_file.write_text(f"data:\n  dir: {configured_data}\n", encoding="utf-8")

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.delenv("PM_DATA_DIR", raising=False)
            config.reload_config()

            assert config.get_data_dir() == configured_data
            assert configured_data.exists()
        finally:
            patch.undo()
            config.reload_config()
