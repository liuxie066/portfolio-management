from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import install_linux

_REAL_SYSTEMD_CAPABILITY_PROBE = install_linux.verify_systemd_credential_capability


@pytest.fixture(autouse=True)
def _assume_supported_systemd_credentials(monkeypatch):
    monkeypatch.setattr(
        install_linux,
        "verify_systemd_credential_capability",
        lambda: {
            "required": True,
            "verified": True,
            "checks": [
                "systemd-creds",
                "systemd-analyze verify",
                "LoadCredentialEncrypted",
            ],
        },
    )


def _args(tmp_path: Path, *extra: str):
    credential_store = tmp_path / "credstore.encrypted"
    credential_store.mkdir(parents=True, exist_ok=True)
    for name in install_linux.FEISHU_CREDENTIAL_NAMES:
        (credential_store / name).write_bytes(b"encrypted-fixture")
    install_linux.ENCRYPTED_CREDENTIAL_STORE_DIR = credential_store
    return install_linux.build_parser().parse_args([
        "--app-dir", str(tmp_path / "app"),
        "--config-dir", str(tmp_path / "etc"),
        "--data-dir", str(tmp_path / "state"),
        "--reports-dir", str(tmp_path / "reports"),
        "--systemd-dir", str(tmp_path / "systemd"),
        "--launcher", str(tmp_path / "bin" / "pm"),
        "--run-user", "portfolio",
        "--options-monitor-env-file", str(tmp_path / "options-monitor.env"),
        *extra,
    ])


def test_install_linux_plan_uses_yaml_config_and_durable_receipt_timer(tmp_path):
    payload = install_linux.build_plan(_args(tmp_path))

    assert payload["success"] is True
    assert payload["dry_run"] is True
    assert payload["paths"]["config_file"].endswith("/etc/config.yaml")
    assert payload["paths"]["env_file"].endswith("/etc/portfolio-management.env")
    assert payload["paths"]["launcher"].endswith("/bin/pm")
    assert payload["systemd"]["morning"] == {
        "timer": install_linux.TIMER_NAME,
        "service": install_linux.SERVICE_NAME,
        "on_calendar": "Mon..Sat *-*-* 08:10:00 Asia/Shanghai",
        "mode": "morning",
    }
    assert payload["systemd"]["evening"] == {
        "timer": install_linux.EVENING_TIMER_NAME,
        "service": install_linux.EVENING_SERVICE_NAME,
        "on_calendar": "Mon..Fri *-*-* 17:10:00 Asia/Shanghai",
        "mode": "evening",
    }
    assert payload["systemd"]["cash_flow"] == {
        "timer": install_linux.CASH_FLOW_TIMER_NAME,
        "service": install_linux.CASH_FLOW_SERVICE_NAME,
        "on_calendar": "*-*-* *:00/15:00 Asia/Shanghai",
        "mode": "scan",
    }
    assert payload["systemd"]["enable_api_service"] is False
    assert payload["systemd"]["api"] == {
        "service": install_linux.API_SERVICE_NAME,
        "host": "127.0.0.1",
        "port": 8765,
    }
    assert payload["systemd"]["enable_quality_timer"] is False
    assert payload["systemd"]["quality"] == {
        "timer": install_linux.QUALITY_TIMER_NAME,
        "service": install_linux.QUALITY_SERVICE_NAME,
        "interval": "15min",
    }
    assert payload["systemd"]["receipts"] == {
        "timer": install_linux.RECEIPT_TIMER_NAME,
        "service": install_linux.RECEIPT_SERVICE_NAME,
        "interval": "5min",
    }
    assert payload["systemd"]["holdings_events"] == {
        "service": install_linux.HOLDINGS_EVENT_SERVICE_NAME,
        "enabled": False,
    }
    assert payload["systemd"]["enable_holdings_event_service"] is False
    assert payload["systemd"]["feishu_credentials"] == {
        "credential_names": list(install_linux.FEISHU_CREDENTIAL_NAMES),
        "capability": {
            "required": True,
            "verified": False,
            "checks": [
                "systemd-creds",
                "systemd-analyze verify",
                "LoadCredentialEncrypted",
            ],
        },
        "preflight_service": install_linux.FEISHU_PREFLIGHT_SERVICE_NAME,
        "preflight_enabled": False,
    }
    assert payload["feishu_receipt_env"]["secret_imported"] is False
    assert "daily_job_args" not in payload


def test_install_linux_rendered_config_points_runtime_dirs(tmp_path):
    args = _args(tmp_path)
    paths = install_linux.build_paths(args)
    rendered = install_linux.render_config_yaml(paths)
    payload = yaml.safe_load(rendered)

    assert payload["data"]["dir"] == str(tmp_path / "state")
    assert payload["report"]["reports_dir"] == str(tmp_path / "reports")
    assert payload["feishu"]["bitable"] == {"app_id": ""}
    assert payload["feishu"]["conversation"] == {
        "app_id": "",
        "open_id": "",
    }
    assert "app_secret" not in rendered
    assert payload["quality"] == {
        "read_token": "",
        "instance_id": "portfolio-management-prod",
        "accounts": ["lx", "sy"],
        "onboarded": False,
    }


def test_install_linux_apply_writes_files_without_overwriting_existing_config(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("account: existing\n", encoding="utf-8")

    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    payload = install_linux.apply_install(args)

    assert payload["dry_run"] is False
    assert payload["writes"][str(paths.config_file)] == "skipped_exists"
    assert paths.config_file.read_text(encoding="utf-8") == "account: existing\n"
    assert paths.env_file.exists()
    assert (tmp_path / "bin" / "pm").exists()
    assert "PORTFOLIO_CONFIG_FILE" in (tmp_path / "bin" / "pm").read_text(encoding="utf-8")
    for unit_name in (
        install_linux.SERVICE_NAME,
        install_linux.TIMER_NAME,
        install_linux.EVENING_SERVICE_NAME,
        install_linux.EVENING_TIMER_NAME,
        install_linux.CASH_FLOW_SERVICE_NAME,
        install_linux.CASH_FLOW_TIMER_NAME,
        install_linux.API_SERVICE_NAME,
        install_linux.QUALITY_SERVICE_NAME,
        install_linux.QUALITY_TIMER_NAME,
        install_linux.RECEIPT_SERVICE_NAME,
        install_linux.RECEIPT_TIMER_NAME,
        install_linux.HOLDINGS_EVENT_SERVICE_NAME,
        install_linux.FEISHU_PREFLIGHT_SERVICE_NAME,
    ):
        assert (paths.systemd_dir / unit_name).exists()
    assert commands == [["systemctl", "daemon-reload"]]


def test_install_linux_enable_starts_all_timers(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    install_linux.apply_install(_args(tmp_path, "--apply", "--enable-timer"))

    assert commands == [
        ["systemctl", "daemon-reload"],
        [
            "systemctl",
            "enable",
            "--now",
            install_linux.TIMER_NAME,
            install_linux.EVENING_TIMER_NAME,
            install_linux.CASH_FLOW_TIMER_NAME,
            install_linux.RECEIPT_TIMER_NAME,
        ],
    ]


def test_install_linux_enable_api_service_is_independent_from_timers(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    install_linux.apply_install(_args(tmp_path, "--apply", "--enable-api-service"))

    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", install_linux.API_SERVICE_NAME],
    ]


def test_install_linux_enable_quality_timer_is_independent(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    install_linux.apply_install(_args(tmp_path, "--apply", "--enable-quality-timer"))

    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", install_linux.QUALITY_TIMER_NAME],
    ]


def test_install_linux_holdings_event_listener_is_generated_disabled_and_explicit(tmp_path, monkeypatch):
    paths = install_linux.build_paths(_args(tmp_path))
    unit = install_linux.render_holdings_event_service_unit(paths, run_user="portfolio")

    assert "events listen --confirm --json" in unit
    assert "holdings and cash-flow event listener" in unit
    assert "holdings events listen" not in unit
    assert "Restart=always" in unit
    assert "[Install]" in unit

    commands = []
    monkeypatch.setattr(
        install_linux.subprocess,
        "run",
        lambda command, check: commands.append(command),
    )
    payload = install_linux.apply_install(
        _args(tmp_path, "--apply", "--enable-holdings-event-listener")
    )

    assert payload["systemd"]["holdings_events"]["enabled"] is True
    assert commands == [
        ["systemctl", "daemon-reload"],
        [
            "systemctl",
            "enable",
            "--now",
            install_linux.HOLDINGS_EVENT_SERVICE_NAME,
        ],
    ]


def test_combined_event_listener_docs_preserve_fx_and_holding_effect_boundaries():
    docs = install_linux.REPO_ROOT / "docs"
    listener = (docs / "holdings-event-listener-runbook.md").read_text()
    cash_flow = (docs / "cash-flow-effects-runbook.md").read_text()
    schema = (docs / "schema.md").read_text()
    deploy = (docs / "deploy-linux.md").read_text()

    assert "pm events status --json" in listener
    assert "pm events subscribe --confirm --json" in listener
    assert "Base document is the\n  subscription boundary" in listener
    assert "never confirms or applies the separate\n  CASH holding effect" in listener
    assert "no rate is guessed" in cash_flow
    assert "manual exact-record reconcile command" in cash_flow
    assert "existing local\n  FX confirmation still matches exactly" in schema
    assert "pm events listen --confirm --json" in deploy


def test_dual_feishu_app_docs_preserve_secret_and_authorization_boundaries():
    root = install_linux.REPO_ROOT
    readme = (root / "README.md").read_text(encoding="utf-8")
    example = (root / "config.example.yaml").read_text(encoding="utf-8")
    deploy = (root / "docs" / "deploy-linux.md").read_text(encoding="utf-8")
    listener = (
        root / "docs" / "holdings-event-listener-runbook.md"
    ).read_text(encoding="utf-8")

    assert "feishu.bitable.app_id" in deploy
    assert "feishu.conversation.app_id" in deploy
    assert "pm-feishu-bitable-app-secret" in readme
    assert "pm-feishu-conversation-app-secret" in readme
    assert "app_secret:" not in example
    assert "App Secret 都不写入 YAML" in readme
    assert "不配置第三个 event-only 应用" in deploy
    assert "绝不会导入\n`OM_FEISHU_BOT_APP_SECRET`" in deploy
    assert "portfolio-feishu-preflight.service" in deploy
    assert "不请求飞书、不订阅、不连接 listener、不发送消息" in deploy
    assert "互相独立的授权边界" in deploy
    assert "单独授权后清理明文 shadow" in deploy
    assert "精确授予以应用身份发送消息 `im:message:send_as_bot`" in deploy
    assert "等价消息发送权限" not in deploy

    prepare_marker = "仅准备 credential-capable checkout/venv"
    provision_marker = "轮换并配置两份 encrypted credentials"
    apply_marker = "apply credential-capable config/env/units"
    preflight_marker = "secure preflight 通过"
    assert deploy.index(prepare_marker) < deploy.index(provision_marker)
    assert deploy.index(provision_marker) < deploy.index(apply_marker)
    assert deploy.index(apply_marker) < deploy.index(preflight_marker)

    subscription = listener.split("## Separately confirmed subscription", 1)[1]
    subscription = subscription.split("## Separately confirmed service activation", 1)[0]
    assert "systemd-run --wait --pipe --collect" in subscription
    assert "LoadCredentialEncrypted=pm-feishu-bitable-app-secret" in subscription
    assert "pm-feishu-conversation-app-secret" not in subscription
    assert (
        "CREDENTIALS_DIRECTORY=/run/credentials/"
        "portfolio-feishu-subscribe-once.service"
    ) in subscription
    assert "file_type=bitable" in subscription
    assert "outbound `event_type` is\nomitted" in subscription


def test_install_linux_api_service_is_loopback_only_and_long_running(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    unit = install_linux.render_api_service_unit(paths, run_user="portfolio")

    assert "Type=simple" in unit
    assert (
        "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
        f"CREDENTIALS_DIRECTORY=/run/credentials/{install_linux.API_SERVICE_NAME} "
        f"{paths.python_bin} {paths.app_dir / 'scripts' / 'serve.py'} "
        "--host 127.0.0.1 --port 8765"
    ) in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "--allow-remote" not in unit
    assert "scripts/service.py" not in unit
    assert "flock" not in unit


def test_install_linux_quality_units_are_read_only_scoped_and_every_15_minutes(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    service = install_linux.render_quality_service_unit(paths, run_user="portfolio")
    timer = install_linux.render_interval_timer_unit(
        interval="15min",
        service_name=install_linux.QUALITY_SERVICE_NAME,
        description="quality",
    )

    assert "Type=oneshot" in service
    assert (
        "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
        f"CREDENTIALS_DIRECTORY=/run/credentials/{install_linux.QUALITY_SERVICE_NAME} "
        f"/usr/bin/flock -n {install_linux.QUALITY_LOCK_FILE} "
        f"{paths.launcher_path} quality refresh --json"
    ) in service
    assert "RuntimeMaxSec=300" in service
    assert "OnBootSec=5min" in timer
    assert "OnUnitActiveSec=15min" in timer
    assert f"Unit={install_linux.QUALITY_SERVICE_NAME}" in timer
    assert "WantedBy=timers.target" in timer


def test_install_linux_receipt_units_retry_durable_outbox_every_five_minutes(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    service = install_linux.render_receipt_service_unit(
        paths,
        run_user="portfolio",
    )
    timer = install_linux.render_interval_timer_unit(
        interval="5min",
        service_name=install_linux.RECEIPT_SERVICE_NAME,
        description="receipts",
    )

    assert (
        "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
        f"CREDENTIALS_DIRECTORY=/run/credentials/{install_linux.RECEIPT_SERVICE_NAME} "
        f"/usr/bin/flock -n {install_linux.RECEIPT_LOCK_FILE} "
        f"{paths.launcher_path} receipts dispatch --limit 100 --confirm --json"
    ) in service
    assert "RuntimeMaxSec=60" in service
    assert "OnUnitActiveSec=5min" in timer
    assert f"Unit={install_linux.RECEIPT_SERVICE_NAME}" in timer


def test_install_linux_service_units_have_exact_feishu_credential_grants(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    rendered = {
        "morning": install_linux.render_service_unit(
            paths,
            run_user="portfolio",
            mode="morning",
        ),
        "evening": install_linux.render_service_unit(
            paths,
            run_user="portfolio",
            mode="evening",
        ),
        "cash_flow": install_linux.render_cash_flow_service_unit(
            paths,
            run_user="portfolio",
        ),
        "api": install_linux.render_api_service_unit(
            paths,
            run_user="portfolio",
        ),
        "quality": install_linux.render_quality_service_unit(
            paths,
            run_user="portfolio",
        ),
        "receipts": install_linux.render_receipt_service_unit(
            paths,
            run_user="portfolio",
        ),
        "events": install_linux.render_holdings_event_service_unit(
            paths,
            run_user="portfolio",
        ),
    }
    expected = {
        "morning": set(install_linux.FEISHU_CREDENTIAL_NAMES),
        "evening": set(install_linux.FEISHU_CREDENTIAL_NAMES),
        "cash_flow": set(install_linux.FEISHU_CREDENTIAL_NAMES),
        "api": set(install_linux.FEISHU_CREDENTIAL_NAMES),
        "quality": {install_linux.BITABLE_APP_SECRET_CREDENTIAL},
        "receipts": {install_linux.CONVERSATION_APP_SECRET_CREDENTIAL},
        "events": {install_linux.BITABLE_APP_SECRET_CREDENTIAL},
    }
    expected_units = {
        "morning": install_linux.SERVICE_NAME,
        "evening": install_linux.EVENING_SERVICE_NAME,
        "cash_flow": install_linux.CASH_FLOW_SERVICE_NAME,
        "api": install_linux.API_SERVICE_NAME,
        "quality": install_linux.QUALITY_SERVICE_NAME,
        "receipts": install_linux.RECEIPT_SERVICE_NAME,
        "events": install_linux.HOLDINGS_EVENT_SERVICE_NAME,
    }

    for unit_name, unit in rendered.items():
        grants = {
            line.split("=", 1)[1]
            for line in unit.splitlines()
            if line.startswith("LoadCredentialEncrypted=")
        }
        assert grants == expected[unit_name]
        assert unit.count("Environment=PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1") == 1
        assert unit.count(
            "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
            f"CREDENTIALS_DIRECTORY=/run/credentials/{expected_units[unit_name]} "
        ) == 1
        assert "App Secret" not in unit
        assert "OM_FEISHU_BOT_APP_SECRET" not in unit


def test_install_linux_preflight_is_disabled_local_and_nonmutating(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    unit = install_linux.render_feishu_preflight_service_unit(
        paths,
        run_user="portfolio",
    )

    assert "Type=oneshot" in unit
    assert "[Install]" not in unit
    assert set(install_linux.FEISHU_CREDENTIAL_NAMES) == {
        line.split("=", 1)[1]
        for line in unit.splitlines()
        if line.startswith("LoadCredentialEncrypted=")
    }
    assert (
        "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
        f"CREDENTIALS_DIRECTORY=/run/credentials/{install_linux.FEISHU_PREFLIGHT_SERVICE_NAME} "
        f"{paths.launcher_path} config doctor "
        "--require-secure-feishu --json"
    ) in unit
    assert (
        "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
        f"CREDENTIALS_DIRECTORY=/run/credentials/{install_linux.FEISHU_PREFLIGHT_SERVICE_NAME} "
        f"{paths.launcher_path} events status --json"
    ) in unit
    assert "events subscribe" not in unit
    assert "events listen" not in unit
    assert "--confirm" not in unit


def test_secure_feishu_exec_overrides_conflicting_shared_env_for_every_unit(
    tmp_path,
):
    paths = install_linux.build_paths(_args(tmp_path))
    hostile_env = (
        "PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=0\n"
        "CREDENTIALS_DIRECTORY=/tmp/not-systemd-credentials\n"
    )
    assert install_linux.render_env_file(
        paths,
        existing_content=hostile_env,
    ) == hostile_env

    units = {
        install_linux.SERVICE_NAME: install_linux.render_service_unit(
            paths, run_user="portfolio", mode="morning"
        ),
        install_linux.EVENING_SERVICE_NAME: install_linux.render_service_unit(
            paths, run_user="portfolio", mode="evening"
        ),
        install_linux.CASH_FLOW_SERVICE_NAME: (
            install_linux.render_cash_flow_service_unit(
                paths, run_user="portfolio"
            )
        ),
        install_linux.API_SERVICE_NAME: install_linux.render_api_service_unit(
            paths, run_user="portfolio"
        ),
        install_linux.QUALITY_SERVICE_NAME: (
            install_linux.render_quality_service_unit(
                paths, run_user="portfolio"
            )
        ),
        install_linux.RECEIPT_SERVICE_NAME: (
            install_linux.render_receipt_service_unit(
                paths, run_user="portfolio"
            )
        ),
        install_linux.HOLDINGS_EVENT_SERVICE_NAME: (
            install_linux.render_holdings_event_service_unit(
                paths, run_user="portfolio"
            )
        ),
        install_linux.FEISHU_PREFLIGHT_SERVICE_NAME: (
            install_linux.render_feishu_preflight_service_unit(
                paths, run_user="portfolio"
            )
        ),
    }

    for unit_name, unit in units.items():
        exec_lines = [
            line for line in unit.splitlines() if line.startswith("ExecStart=")
        ]
        assert exec_lines
        expected_prefix = (
            "ExecStart=/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
            f"CREDENTIALS_DIRECTORY=/run/credentials/{unit_name} "
        )
        assert all(line.startswith(expected_prefix) for line in exec_lines)


def test_systemd_credential_capability_probe_uses_exact_unit_syntax(tmp_path):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        unit_path = Path(command[-1])
        unit = unit_path.read_text(encoding="utf-8")
        assert unit.count("LoadCredentialEncrypted=") == 2
        assert install_linux.BITABLE_APP_SECRET_CREDENTIAL in unit
        assert install_linux.CONVERSATION_APP_SECRET_CREDENTIAL in unit
        assert "ExecStart=/bin/true" in unit
        return subprocess.CompletedProcess(command, 0, "", "")

    result = _REAL_SYSTEMD_CAPABILITY_PROBE(
        which=lambda command: f"/usr/bin/{command}",
        runner=runner,
    )

    assert result["verified"] is True
    assert calls[0][0][0:2] == [
        "/usr/bin/systemd-analyze",
        "verify",
    ]
    assert calls[0][1] == {
        "check": False,
        "capture_output": True,
        "text": True,
    }


def test_install_linux_missing_encrypted_credential_blocks_before_target_writes(
    tmp_path,
    monkeypatch,
):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    (
        install_linux.ENCRYPTED_CREDENTIAL_STORE_DIR
        / install_linux.CONVERSATION_APP_SECRET_CREDENTIAL
    ).unlink()
    monkeypatch.setattr(
        install_linux,
        "_mkdirs",
        lambda paths: (_ for _ in ()).throw(
            AssertionError("target directories must not be created")
        ),
    )

    with pytest.raises(RuntimeError, match="missing or invalid encrypted") as exc:
        install_linux.apply_install(args)

    assert install_linux.CONVERSATION_APP_SECRET_CREDENTIAL in str(exc.value)
    assert not paths.config_file.exists()


def test_install_linux_unsupported_systemd_blocks_before_target_writes(
    tmp_path,
    monkeypatch,
):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    sequence = []

    def unsupported():
        sequence.append("capability")
        raise RuntimeError("systemd credential capability unavailable")

    monkeypatch.setattr(
        install_linux,
        "verify_systemd_credential_capability",
        unsupported,
    )
    monkeypatch.setattr(
        install_linux,
        "_mkdirs",
        lambda paths: sequence.append("target-write"),
    )

    with pytest.raises(RuntimeError, match="capability unavailable"):
        install_linux.apply_install(args)

    assert sequence == ["capability"]
    assert not paths.config_file.exists()


def test_install_linux_supported_probe_precedes_every_target_write(
    tmp_path,
    monkeypatch,
):
    args = _args(tmp_path, "--apply")
    sequence = []
    real_mkdirs = install_linux._mkdirs
    real_write_text = install_linux._write_text

    def supported():
        sequence.append("capability")
        return {"required": True, "verified": True, "checks": []}

    def tracked_mkdirs(paths):
        sequence.append("target-mkdir")
        return real_mkdirs(paths)

    def tracked_write(*args, **kwargs):
        sequence.append("target-write")
        return real_write_text(*args, **kwargs)

    monkeypatch.setattr(
        install_linux,
        "verify_systemd_credential_capability",
        supported,
    )
    monkeypatch.setattr(install_linux, "_mkdirs", tracked_mkdirs)
    monkeypatch.setattr(install_linux, "_write_text", tracked_write)
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    install_linux.apply_install(args)

    assert sequence[0] == "capability"
    assert sequence.index("capability") < sequence.index("target-mkdir")
    assert sequence.index("capability") < sequence.index("target-write")


def test_install_linux_rejects_symlinked_encrypted_credential(tmp_path):
    _args(tmp_path)
    store = install_linux.ENCRYPTED_CREDENTIAL_STORE_DIR
    target = store / "opaque-target"
    target.write_bytes(b"encrypted-fixture")
    credential = store / install_linux.BITABLE_APP_SECRET_CREDENTIAL
    credential.unlink()
    credential.symlink_to(target)

    with pytest.raises(RuntimeError, match="missing or invalid encrypted"):
        install_linux.verify_encrypted_credential_presence()


def test_install_linux_clean_apply_is_idempotent(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    first = install_linux.apply_install(args)
    tracked_paths = [
        paths.env_file,
        paths.launcher_path,
        *(Path(path) for path in first["writes"] if Path(path) != paths.config_file),
    ]
    first_digests = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tracked_paths
    }

    second = install_linux.apply_install(args)
    second_digests = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in tracked_paths
    }

    assert second["writes"][str(paths.config_file)] == "skipped_exists"
    assert second_digests == first_digests


def test_install_linux_human_plan_reports_shadow_key_without_value(
    tmp_path,
    capsys,
):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "OM_FEISHU_BOT_APP_SECRET=do-not-render-this-value\n",
        encoding="utf-8",
    )
    payload = install_linux.build_plan(_args(tmp_path))

    install_linux._print_plan(payload, as_json=False)

    output = capsys.readouterr().out
    assert "options_monitor_env:OM_FEISHU_BOT_APP_SECRET" in output
    assert "do-not-render-this-value" not in output


def test_install_linux_root_apply_assigns_runtime_dirs_to_service_user(
    tmp_path,
    monkeypatch,
):
    paths = install_linux.build_paths(_args(tmp_path))
    paths.data_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    operation_db = paths.data_dir / "pm_operation_state.sqlite3"
    operation_db.write_text("", encoding="utf-8")
    commands = []
    monkeypatch.setattr(install_linux.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        install_linux.subprocess,
        "run",
        lambda command, check: commands.append(command),
    )

    rendered = install_linux._prepare_runtime_ownership(
        paths,
        run_user="portfolio",
    )

    assert ["chown", "portfolio", str(paths.data_dir)] in commands
    assert ["chown", "portfolio", str(paths.reports_dir)] in commands
    assert ["chown", "portfolio", str(operation_db)] in commands
    assert paths.data_dir.stat().st_mode & 0o777 == 0o750
    assert paths.reports_dir.stat().st_mode & 0o777 == 0o750
    assert rendered == [" ".join(command) for command in commands]


def test_install_linux_apply_imports_only_nonsecret_options_monitor_feishu_values(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "\n".join([
            "OM_FEISHU_BOT_APP_ID=cli_liukanshan",
            "OM_FEISHU_BOT_APP_SECRET=receipt_secret",
            "OM_FEISHU_BOT_USER_OPEN_ID=ou_user",
            "OPENAI_API_KEY=must_not_copy",
            "OM_ASSISTANT_API_KEY=must_not_copy_either",
            "",
        ]),
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    payload = install_linux.apply_install(args)
    rendered = paths.env_file.read_text(encoding="utf-8")

    assert payload["feishu_receipt_env"] == {
        "source": str(source),
        "target": str(paths.env_file),
        "keys": list(install_linux.OPTIONS_MONITOR_FEISHU_KEYS),
        "secret_imported": False,
        "status": "imported_nonsecret_values",
    }
    assert "OM_FEISHU_BOT_APP_ID=cli_liukanshan" in rendered
    assert "OM_FEISHU_BOT_USER_OPEN_ID=ou_user" in rendered
    assert "OM_FEISHU_BOT_APP_SECRET" not in rendered
    assert "receipt_secret" not in rendered
    assert "OPENAI_API_KEY" not in rendered
    assert "OM_ASSISTANT_API_KEY" not in rendered
    assert "receipt_secret" not in str(payload)
    assert payload["plaintext_feishu_shadows"] == {
        "detected": [
            {
                "location": "options_monitor_env",
                "key": "OM_FEISHU_BOT_APP_SECRET",
            }
        ],
        "requires_separate_cleanup": True,
    }


def test_install_linux_partial_source_updates_only_explicit_receipt_values(tmp_path, monkeypatch):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "OM_FEISHU_BOT_APP_ID=cli_new\n"
        "OM_FEISHU_BOT_APP_SECRET=secret_new\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    paths.env_file.write_text(
        "# keep this comment\n"
        "KEEP_EXISTING=1\n"
        "OM_FEISHU_BOT_APP_ID=cli_old\n"
        "OM_FEISHU_BOT_APP_SECRET=secret_old\n"
        "OM_FEISHU_BOT_USER_OPEN_ID=ou_keep\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    payload = install_linux.apply_install(args)
    rendered = paths.env_file.read_text(encoding="utf-8")

    assert payload["feishu_receipt_env"]["status"] == "imported_nonsecret_values"
    assert "# keep this comment\nKEEP_EXISTING=1\n" in rendered
    assert "OM_FEISHU_BOT_APP_ID=cli_new" in rendered
    assert "OM_FEISHU_BOT_APP_SECRET=secret_old" in rendered
    assert "secret_new" not in rendered
    assert "OM_FEISHU_BOT_USER_OPEN_ID=ou_keep" in rendered
    assert payload["plaintext_feishu_shadows"]["detected"] == [
        {
            "location": "options_monitor_env",
            "key": "OM_FEISHU_BOT_APP_SECRET",
        },
        {"location": "target_env", "key": "OM_FEISHU_BOT_APP_SECRET"},
    ]


def test_install_linux_missing_source_preserves_target_env_byte_for_byte(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    original = (
        "# production-only values\n"
        "KEEP_EXISTING=1\n"
        "OM_FEISHU_BOT_APP_ID=cli_keep\n"
        "OM_FEISHU_BOT_APP_SECRET=secret_keep\n"
        "OM_FEISHU_BOT_USER_OPEN_ID=ou_keep\n"
    )
    paths.env_file.write_text(original, encoding="utf-8")
    before_digest = hashlib.sha256(original.encode()).hexdigest()
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    payload = install_linux.apply_install(args)
    rendered = paths.env_file.read_text(encoding="utf-8")

    assert payload["feishu_receipt_env"]["status"] == "source_missing"
    assert hashlib.sha256(rendered.encode()).hexdigest() == before_digest
    assert rendered == original


def test_install_linux_rejects_duplicate_target_env_before_other_writes(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    paths.env_file.write_text("DUP=1\nDUP=2\n", encoding="utf-8")
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    try:
        install_linux.apply_install(args)
    except ValueError as exc:
        assert "duplicate target env key: DUP" in str(exc)
    else:
        raise AssertionError("expected duplicate target env to fail")

    assert not paths.config_file.exists()
    assert paths.env_file.read_text(encoding="utf-8") == "DUP=1\nDUP=2\n"


def test_install_linux_rejects_duplicate_source_env_before_other_writes(tmp_path, monkeypatch):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "OM_FEISHU_BOT_APP_ID=first\n"
        "OM_FEISHU_BOT_APP_ID=second\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    try:
        install_linux.apply_install(args)
    except ValueError as exc:
        assert "duplicate options-monitor env key: OM_FEISHU_BOT_APP_ID" in str(exc)
    else:
        raise AssertionError("expected duplicate source env to fail")

    assert not paths.config_file.exists()
    assert not paths.env_file.exists()


def test_install_linux_service_units_use_versioned_wrapper_and_shared_lock(tmp_path):
    args = _args(tmp_path)
    paths = install_linux.build_paths(args)
    morning = install_linux.render_service_unit(paths, run_user="portfolio", mode="morning")
    evening = install_linux.render_service_unit(paths, run_user="portfolio", mode="evening")

    assert f"Environment=PORTFOLIO_PM_BIN={tmp_path / 'bin' / 'pm'}" in morning
    assert "PORTFOLIO_FUTU_SY_ENV_FILE" not in morning
    assert f"{install_linux.SCHEDULE_LOCK_FILE} {tmp_path / 'app' / 'scripts' / 'portfolio_scheduled_job.sh'} morning" in morning
    assert f"{install_linux.SCHEDULE_LOCK_FILE} {tmp_path / 'app' / 'scripts' / 'portfolio_scheduled_job.sh'} evening" in evening
    assert "--sync-futu-cash-mmf" not in morning
    assert "--sync-futu-cash-mmf" not in evening


def test_install_linux_timer_units_are_persistent_and_target_correct_services(tmp_path):
    morning = install_linux.render_timer_unit(
        on_calendar="Mon..Sat *-*-* 08:10:00 Asia/Shanghai",
        service_name=install_linux.SERVICE_NAME,
        description="morning",
    )
    evening = install_linux.render_timer_unit(
        on_calendar="Mon..Fri *-*-* 17:10:00 Asia/Shanghai",
        service_name=install_linux.EVENING_SERVICE_NAME,
        description="evening",
    )

    assert "OnCalendar=Mon..Sat *-*-* 08:10:00 Asia/Shanghai" in morning
    assert "Unit=portfolio-nav-daily.service" in morning
    assert "OnCalendar=Mon..Fri *-*-* 17:10:00 Asia/Shanghai" in evening
    assert "Unit=portfolio-futu-evening.service" in evening
    assert "Persistent=true" in morning
    assert "Persistent=true" in evening


def test_install_shell_help_is_available():
    result = subprocess.run(
        ["bash", "scripts/install.sh", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "portfolio-management installer" in result.stdout
    assert "--enable-timer" in result.stdout
    assert "--enable-api-service" in result.stdout
    assert "--enable-quality-timer" in result.stdout
    assert "--enable-holdings-event-listener" in result.stdout
    assert "receipt timers" in result.stdout
    assert "--sync-futu-cash-mmf" not in result.stdout
    assert "OM_FEISHU_BOT_APP_ID" in result.stdout
    assert "OM_FEISHU_BOT_USER_OPEN_ID" in result.stdout
    assert "OM_FEISHU_BOT_APP_SECRET" not in result.stdout
    assert "installer never imports, copies from the source" in result.stdout
    assert "creates, or prints secret values" in result.stdout


def test_install_linux_direct_entrypoint_imports_runtime_contract_outside_repo_cwd(
    tmp_path,
):
    result = subprocess.run(
        [
            "python3.12",
            str(install_linux.REPO_ROOT / "scripts" / "install_linux.py"),
            "--help",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Install portfolio-management Linux systemd assets" in result.stdout
    assert install_linux.BITABLE_APP_SECRET_CREDENTIAL == (
        "pm-feishu-bitable-app-secret"
    )
    assert install_linux.CONVERSATION_APP_SECRET_CREDENTIAL == (
        "pm-feishu-conversation-app-secret"
    )
