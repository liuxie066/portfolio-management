from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
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


def test_receipt_config_falls_back_to_options_monitor_bot_env():
    patch = MonkeyPatch()
    try:
        patch.delenv("FEISHU_RECEIPT_APP_ID", raising=False)
        patch.delenv("FEISHU_RECEIPT_APP_SECRET", raising=False)
        patch.delenv("FEISHU_RECEIPT_OPEN_ID", raising=False)
        patch.setenv("OM_FEISHU_BOT_APP_ID", "cli_liukanshan")
        patch.setenv("OM_FEISHU_BOT_APP_SECRET", "bot_secret")
        patch.setenv("OM_FEISHU_BOT_USER_OPEN_ID", "ou_user")

        assert config.get_with_source("feishu.receipt.app_id") == (
            "cli_liukanshan",
            "legacy-env:OM_FEISHU_BOT_APP_ID",
        )
        assert config.get("feishu.receipt.app_secret") == "bot_secret"
        assert config.get("feishu.receipt.open_id") == "ou_user"
    finally:
        patch.undo()


def test_canonical_feishu_roles_resolve_legacy_inputs_without_cross_role_fallback():
    patch = MonkeyPatch()
    try:
        patch.delenv("FEISHU_BITABLE_APP_ID", raising=False)
        patch.delenv("FEISHU_CONVERSATION_APP_ID", raising=False)
        patch.setenv("FEISHU_APP_ID", "cli_bitable")
        patch.setenv("FEISHU_RECEIPT_APP_ID", "cli_conversation")

        assert config.get_with_source("feishu.bitable.app_id") == (
            "cli_bitable",
            "legacy-env:FEISHU_APP_ID",
        )
        assert config.get_with_source("feishu.conversation.app_id") == (
            "cli_conversation",
            "legacy-env:FEISHU_RECEIPT_APP_ID",
        )
    finally:
        patch.undo()


def test_canonical_and_legacy_non_secret_identity_conflict_fails_redacted():
    patch = MonkeyPatch()
    try:
        patch.setenv("FEISHU_BITABLE_APP_ID", "cli_new")
        patch.setenv("FEISHU_APP_ID", "cli_old")

        with pytest.raises(
            config.FeishuCredentialConfigError,
            match=r"^conflicting_role_configuration: feishu\.bitable\.app_id$",
        ):
            config.get("feishu.bitable.app_id")
    finally:
        patch.undo()


def test_equal_canonical_and_legacy_identity_is_reported_as_redundant():
    patch = MonkeyPatch()
    try:
        patch.setenv("FEISHU_BITABLE_APP_ID", "cli_same")
        patch.setenv("FEISHU_APP_ID", "cli_same")

        inspected = config.inspect_config(keys=["feishu.bitable.app_id"])

        assert inspected["success"] is True
        assert inspected["warnings"] == [
            {
                "key": "feishu.bitable.app_id",
                "warning": "redundant_legacy_configuration",
                "sources": ["legacy-env:FEISHU_APP_ID"],
            }
        ]
    finally:
        patch.undo()


def test_secure_credential_wins_without_reading_or_comparing_legacy_secret():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        credential_dir = root / "credentials"
        credential_dir.mkdir()
        credential_path = credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL
        credential_path.write_text("secure-do-not-leak\n", encoding="utf-8")
        config_file = root / "config.yaml"
        config_file.write_text(
            "feishu:\n  app_secret: legacy-do-not-leak\n",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            patch.setenv("PM_REQUIRE_SECURE_FEISHU_CREDENTIALS", "1")
            patch.delenv("FEISHU_BITABLE_APP_SECRET", raising=False)
            patch.delenv("FEISHU_APP_SECRET", raising=False)
            config.reload_config()

            assert config.get_with_source("feishu.bitable.app_secret") == (
                "secure-do-not-leak",
                f"credential:{config.BITABLE_APP_SECRET_CREDENTIAL}",
            )
            inspected = config.inspect_config(
                keys=["feishu.bitable.app_secret"],
                redact=False,
            )
            encoded = json.dumps(inspected, ensure_ascii=False)
            assert inspected["success"] is True
            assert inspected["values"]["feishu.bitable.app_secret"]["value"] == (
                "sec...eak"
            )
            assert inspected["warnings"] == [
                {
                    "key": "feishu.bitable.app_secret",
                    "warning": "plaintext_shadow_detected",
                    "sources": ["config:feishu.app_secret"],
                }
            ]
            assert "secure-do-not-leak" not in encoded
            assert "legacy-do-not-leak" not in encoded
            assert str(credential_dir) not in encoded
        finally:
            patch.undo()
            config.reload_config()


def test_secure_mode_rejects_plaintext_fallback_and_reports_missing_credential():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        credential_dir = root / "credentials"
        credential_dir.mkdir()
        config_file = root / "config.yaml"
        config_file.write_text(
            "feishu:\n  app_secret: plaintext-do-not-leak\n",
            encoding="utf-8",
        )
        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            patch.setenv("PM_REQUIRE_SECURE_FEISHU_CREDENTIALS", "1")
            patch.delenv("FEISHU_BITABLE_APP_SECRET", raising=False)
            patch.delenv("FEISHU_APP_SECRET", raising=False)
            config.reload_config()

            with pytest.raises(
                config.FeishuCredentialConfigError,
                match=r"^insecure_secret_source: feishu\.bitable\.app_secret$",
            ):
                config.get("feishu.bitable.app_secret")
            inspected = config.inspect_config(
                keys=["feishu.bitable.app_secret"],
                redact=False,
            )
            assert inspected["success"] is False
            assert inspected["issues"] == [
                {
                    "key": "feishu.bitable.app_secret",
                    "error": "insecure_secret_source",
                }
            ]
            assert "plaintext-do-not-leak" not in json.dumps(inspected)

            config_file.write_text("{}\n", encoding="utf-8")
            config.reload_config()
            with pytest.raises(
                config.FeishuCredentialConfigError,
                match=r"^missing_secure_credential: feishu\.bitable\.app_secret$",
            ):
                config.get("feishu.bitable.app_secret")
        finally:
            patch.undo()
            config.reload_config()


def test_invalid_secure_mode_value_fails_closed_without_plaintext_fallback():
    patch = MonkeyPatch()
    try:
        patch.setenv("PM_REQUIRE_SECURE_FEISHU_CREDENTIALS", "treu")
        patch.setenv("FEISHU_APP_SECRET", "plaintext-must-not-leak")
        patch.delenv("CREDENTIALS_DIRECTORY", raising=False)

        with pytest.raises(
            config.FeishuCredentialConfigError,
            match=r"^invalid_secure_mode: feishu\.credentials\.secure_mode$",
        ):
            config.get("feishu.bitable.app_secret")
        inspected = config.inspect_config(keys=["feishu.bitable.app_secret"])
        encoded = json.dumps(inspected, ensure_ascii=False)
        assert inspected["success"] is False
        assert inspected["issues"] == [
            {
                "key": "feishu.credentials.secure_mode",
                "error": "invalid_secure_mode",
            }
        ]
        assert "plaintext-must-not-leak" not in encoded
    finally:
        patch.undo()


@pytest.mark.parametrize(
    "payload",
    [b"", b"contains\x00nul", b"two\nlines", b"x" * 4097],
)
def test_systemd_feishu_credential_rejects_invalid_payloads(payload):
    with TemporaryDirectory() as tmp:
        credential_dir = Path(tmp)
        (credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL).write_bytes(payload)
        patch = MonkeyPatch()
        try:
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            with pytest.raises(
                config.FeishuCredentialConfigError,
                match=r"^invalid_credential_file: feishu\.bitable\.app_secret$",
            ):
                config.get("feishu.bitable.app_secret")
        finally:
            patch.undo()


def test_systemd_feishu_credential_accepts_exact_4096_byte_boundary():
    with TemporaryDirectory() as tmp:
        credential_dir = Path(tmp)
        value = "x" * 4096
        (credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL).write_text(
            value,
            encoding="utf-8",
        )
        patch = MonkeyPatch()
        try:
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            assert config.get("feishu.bitable.app_secret") == value
        finally:
            patch.undo()


def test_systemd_feishu_credential_rejects_symlink():
    with TemporaryDirectory() as tmp:
        credential_dir = Path(tmp) / "credentials"
        credential_dir.mkdir()
        source = Path(tmp) / "source"
        source.write_text("do-not-read", encoding="utf-8")
        (credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL).symlink_to(source)
        patch = MonkeyPatch()
        try:
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            with pytest.raises(
                config.FeishuCredentialConfigError,
                match=r"^invalid_credential_file: feishu\.bitable\.app_secret$",
            ):
                config.get("feishu.bitable.app_secret")
        finally:
            patch.undo()


def test_invalid_credential_traceback_suppresses_path_and_raw_bytes():
    import traceback

    with TemporaryDirectory() as tmp:
        credential_dir = Path(tmp)
        credential_path = credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL
        credential_path.write_bytes(b"private-prefix-\xff-private-suffix")
        patch = MonkeyPatch()
        try:
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            try:
                config.get("feishu.bitable.app_secret")
            except config.FeishuCredentialConfigError:
                rendered = traceback.format_exc()
            else:
                raise AssertionError("invalid UTF-8 credential should fail")
            assert str(credential_dir) not in rendered
            assert "private-prefix" not in rendered
            assert "private-suffix" not in rendered
            assert "UnicodeDecodeError" not in rendered
        finally:
            patch.undo()


def test_credential_open_error_traceback_suppresses_path(monkeypatch, tmp_path):
    import traceback
    from src.configuration import feishu_credentials

    credential_path = tmp_path / config.BITABLE_APP_SECRET_CREDENTIAL
    credential_path.write_text("fixture-secret", encoding="utf-8")

    def fail_open(path, _flags):
        raise OSError(f"cannot open {path}")

    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(feishu_credentials.os, "open", fail_open)
    try:
        config.get("feishu.bitable.app_secret")
    except config.FeishuCredentialConfigError:
        rendered = traceback.format_exc()
    else:
        raise AssertionError("credential open failure should fail")

    assert str(tmp_path) not in rendered
    assert "fixture-secret" not in rendered
    assert "OSError" not in rendered


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
  receipt:
    app_secret: receiptsecret123
    open_id: ou_abcdef123456
""",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            _clear_env(
                patch,
                "account",
                "data.dir",
                "feishu.app_id",
                "feishu.app_secret",
                "feishu.receipt.app_secret",
                "feishu.receipt.open_id",
            )
            config.reload_config()

            payload = config.inspect_config(keys=[
                "account",
                "data.dir",
                "feishu.app_secret",
                "feishu.receipt.app_secret",
                "feishu.receipt.open_id",
            ])
            assert payload["success"] is True
            assert payload["config_format"] == "yaml"
            assert payload["values"]["account"]["value"] == "lx"
            assert payload["values"]["data.dir"]["value"] == "/var/lib/portfolio-management/.data"
            assert payload["values"]["feishu.app_secret"]["value"] == "sec...456"
            assert payload["values"]["feishu.app_secret"]["source"] == f"legacy-file:{config_file}"
            assert payload["values"]["feishu.receipt.app_secret"]["value"] == "rec...123"
            assert payload["values"]["feishu.receipt.open_id"]["value"] == "ou_...456"
        finally:
            patch.undo()
            config.reload_config()


def test_quality_config_token_is_redacted_and_accounts_are_normalized():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(
            """
account: fallback
quality:
  read_token: quality-secret-123
  accounts: [LX, sy, lx]
""",
            encoding="utf-8",
        )
        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.delenv("PM_QUALITY_READ_TOKEN", raising=False)
            patch.delenv("PM_QUALITY_ACCOUNTS", raising=False)
            config.reload_config()

            inspected = config.inspect_config(keys=["quality.read_token"])
            assert inspected["values"]["quality.read_token"]["value"] == "qua...123"
            assert config.get_quality_accounts() == ["lx", "sy"]
        finally:
            patch.undo()
            config.reload_config()


def test_feishu_table_ref_accepts_qualified_and_shared_app_token(monkeypatch):
    values = {
        "feishu.tables.holdings": "base_a/table_a",
        "feishu.app_token": "shared_base",
    }
    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))

    assert config.get_feishu_table_ref("holdings") == ("base_a", "table_a")

    values["feishu.tables.holdings"] = "table_b"
    assert config.get_feishu_table_ref("holdings") == ("shared_base", "table_b")


def test_feishu_table_ref_fails_closed_on_ambiguous_or_incomplete_config(monkeypatch):
    values = {"feishu.tables.holdings": "base/table/extra"}
    monkeypatch.setattr(config, "get", lambda key, default=None: values.get(key, default))

    with pytest.raises(ValueError, match="expected app_token/table_id"):
        config.get_feishu_table_ref("holdings")

    values["feishu.tables.holdings"] = "table_only"
    with pytest.raises(ValueError, match="missing feishu.app_token"):
        config.get_feishu_table_ref("holdings")


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


def test_validate_futu_config_requires_receipt_bot_credentials():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(
            """
feishu:
  app_id: cli_table
  app_secret: table_secret
  app_token: appToken
  tables:
    holdings: appToken/tbl_holdings
    nav_history: appToken/tbl_nav
    cash_flow: appToken/tbl_cash
    holdings_snapshot: appToken/tbl_snapshot
futu:
  opend:
    host: 127.0.0.1
    port: 11111
""",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            _clear_env(
                patch,
                *config.REQUIRED_DAILY_JOB_KEYS,
                "futu.opend.host",
                "futu.opend.port",
                "feishu.receipt.app_id",
                "feishu.receipt.app_secret",
                "feishu.receipt.open_id",
            )
            config.reload_config()

            payload = config.validate_deploy_config(require_futu=True)

            assert payload["success"] is False
            assert {issue["key"] for issue in payload["issues"]} >= {
                "feishu.conversation.app_id",
                "feishu.conversation.app_secret",
                "feishu.conversation.open_id",
            }
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
