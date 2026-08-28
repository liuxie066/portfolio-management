from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.export_om_openapi import render_contract

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "om-api" / "v1.openapi.json"
MANIFEST = ROOT / "contracts" / "om-api" / "manifest.json"


def test_checked_in_openapi_matches_fastapi_and_manifest() -> None:
    assert CONTRACT.read_text(encoding="utf-8") == render_contract()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["api_version"] == "portfolio.api.v1"
    assert manifest["release_state"] == "published"
    assert manifest["contract_release"] == "pm-api-v1.0.0"
    assert "source_commit" not in manifest
    assert manifest["sha256"] == hashlib.sha256(CONTRACT.read_bytes()).hexdigest()


def test_openapi_required_response_contracts_are_machine_readable() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert set(document["components"]["schemas"]["PublicErrorResponse"]["required"]) == {
        "success",
        "error_code",
        "message",
        "request_id",
        "details",
    }
    capital = document["paths"]["/api/v1/analysis/capital-facts"]["get"]
    valuation = document["paths"]["/api/v1/analysis/valuation-evidence"]["post"]
    assert capital["responses"]["200"]["content"]["application/json"]["schema"]
    assert valuation["responses"]["200"]["content"]["application/json"]["schema"]
    assert capital["responses"]["503"]["content"]["application/json"]["schema"]
    refresh = document["paths"]["/api/v1/futu/holdings/refresh-requests"]["post"]
    assert "202" in refresh["responses"]
    assert "200" not in refresh["responses"]
    assert refresh["responses"]["202"]["content"]["application/json"]["schema"]
    assert refresh["responses"]["422"]["content"]["application/json"]["schema"]
    for response in refresh["responses"].values():
        assert (
            response["headers"]["X-PM-API-Version"]["schema"]["const"]
            == "portfolio.api.v1"
        )
    request_schema = document["components"]["schemas"]["FutuHoldingsRefreshRequest"]
    assert request_schema["additionalProperties"] is False
    assert "NavRecordRequest" not in document["components"]["schemas"]
    assert "DailyNavJobRequest" not in document["components"]["schemas"]
