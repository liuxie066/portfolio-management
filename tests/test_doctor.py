from __future__ import annotations

import json

from scripts import doctor


def test_doctor_returns_nonzero_when_report_not_ok(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_check_import", lambda _name: {"ok": True, "version": "test"})
    monkeypatch.setattr(doctor, "_http_head", lambda _url: {"ok": True, "status": 200})
    monkeypatch.setattr(doctor, "_check_feishu", lambda: {"ok": False, "error": "missing holdings"})
    monkeypatch.setattr(doctor, "_check_finnhub_config", lambda: {"ok": True, "enabled": False, "status": "disabled_no_key"})

    assert doctor.main() == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["feishu"]["error"] == "missing holdings"
    assert set(payload["imports"]) == {"pydantic", "requests"}


def test_doctor_returns_zero_when_required_checks_pass(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "_check_import", lambda _name: {"ok": True, "version": "test"})
    monkeypatch.setattr(doctor, "_http_head", lambda _url: {"ok": True, "status": 200})
    monkeypatch.setattr(doctor, "_check_feishu", lambda: {"ok": True, "holdings": "app/table", "code": 0})
    monkeypatch.setattr(doctor, "_check_finnhub_config", lambda: {"ok": True, "enabled": True, "status": "configured"})

    assert doctor.main() == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["pricing"]["finnhub"]["status"] == "configured"
