from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from scripts.export_om_openapi import render_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "om-api" / "v1.openapi.json"
MANIFEST = ROOT / "contracts" / "om-api" / "manifest.json"


def test_checked_in_openapi_matches_fastapi_and_manifest() -> None:
    assert CONTRACT.read_text(encoding="utf-8") == render_contract()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["api_version"] == "portfolio.api.v1"
    assert manifest["release_state"] == "unpublished"
    assert manifest["planned_contract_release"] == "pm-api-v1.0.0"
    assert len(manifest["source_commit"]) == 40
    assert manifest["sha256"] == hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    pinned = subprocess.run(
        ["git", "show", f"{manifest['source_commit']}:{manifest['canonical_path']}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    assert pinned == CONTRACT.read_bytes()


def test_openapi_required_response_contracts_are_machine_readable() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    capital = document["paths"]["/api/v1/analysis/capital-facts"]["get"]
    valuation = document["paths"]["/api/v1/analysis/valuation-evidence"]["post"]
    assert capital["responses"]["200"]["content"]["application/json"]["schema"]
    assert valuation["responses"]["200"]["content"]["application/json"]["schema"]
    assert capital["responses"]["503"]["content"]["application/json"]["schema"]
    assert "NavRecordRequest" not in document["components"]["schemas"]
    assert "DailyNavJobRequest" not in document["components"]["schemas"]
