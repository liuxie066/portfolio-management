from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from scripts import serve


def test_serve_allow_remote_sets_app_environment_before_uvicorn_run(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs, os.environ.get("PORTFOLIO_SERVICE_ALLOW_REMOTE")))

    monkeypatch.delenv("PORTFOLIO_SERVICE_ALLOW_REMOTE", raising=False)
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))

    assert serve.main(["--host", "0.0.0.0", "--port", "9876", "--allow-remote"]) == 0
    assert calls == [
        (("src.service.http:app",), {"host": "0.0.0.0", "port": 9876, "reload": False}, "1"),
    ]
    assert "PORTFOLIO_SERVICE_ALLOW_REMOTE" not in os.environ
