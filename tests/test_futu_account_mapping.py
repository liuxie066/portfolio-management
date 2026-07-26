from __future__ import annotations

import json

import pytest

from src import config


def _configure(monkeypatch, tmp_path, payload: dict) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_CONFIG_FILE", str(path))
    for account in ("LX", "SY"):
        for suffix in ("ACC_ID", "TRD_ENV", "TRD_MARKET", "CASH_CURRENCY"):
            monkeypatch.delenv(f"FUTU_{account}_{suffix}", raising=False)
    config.reload_config()


def test_account_mapping_is_explicit_unique_real_and_cnh(monkeypatch, tmp_path) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        {
            "futu": {
                "accounts": {
                    "lx": {
                        "acc_id": 101,
                        "trd_env": "REAL",
                        "trd_market": "US",
                        "cash_currency": "CNH",
                    },
                    "sy": {
                        "acc_id": 202,
                        "trd_env": "REAL",
                        "trd_market": "US",
                        "cash_currency": "CNH",
                    },
                }
            }
        },
    )
    result = config.validate_futu_account_mappings(["lx", "sy"])
    assert result["success"] is True
    assert result["mappings"][0]["account_fingerprint"].startswith("sha256:")
    assert "acc_id" not in result["mappings"][0]


@pytest.mark.parametrize(
    "override",
    [
        {},
        {"acc_id": 101, "trd_env": "SIMULATE", "trd_market": "US", "cash_currency": "CNH"},
        {"acc_id": 101, "trd_env": "REAL", "trd_market": "", "cash_currency": "CNH"},
        {"acc_id": 101, "trd_env": "REAL", "trd_market": "US", "cash_currency": "CNY"},
    ],
)
def test_account_mapping_fails_closed(monkeypatch, tmp_path, override: dict) -> None:
    _configure(monkeypatch, tmp_path, {"futu": {"accounts": {"lx": override}}})
    with pytest.raises(ValueError):
        config.get_futu_account_settings("lx")


def test_duplicate_acc_id_is_rejected(monkeypatch, tmp_path) -> None:
    account = {
        "acc_id": 101,
        "trd_env": "REAL",
        "trd_market": "US",
        "cash_currency": "CNH",
    }
    _configure(
        monkeypatch,
        tmp_path,
        {"futu": {"accounts": {"lx": account, "sy": dict(account)}}},
    )
    result = config.validate_futu_account_mappings(["lx", "sy"])
    assert result["success"] is False
    assert "duplicates account lx" in result["issues"][0]["error"]


def test_account_index_or_global_acc_id_is_never_a_fallback(monkeypatch, tmp_path) -> None:
    _configure(
        monkeypatch,
        tmp_path,
        {
            "futu": {
                "acc_id": 999,
                "accounts": {
                    "lx": {
                        "acc_index": 0,
                        "trd_env": "REAL",
                        "trd_market": "US",
                        "cash_currency": "CNH",
                    }
                },
            }
        },
    )
    with pytest.raises(ValueError, match="acc_id must be explicit"):
        config.get_futu_account_settings("lx")
