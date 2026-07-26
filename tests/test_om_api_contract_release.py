from __future__ import annotations

import json
from pathlib import Path

from scripts.om_api_contract_release import vendor_release


def test_vendor_openapi_contract_updates_om_manifest(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    consumer = tmp_path / "om"
    target = consumer / "contracts" / "portfolio-management"
    target.mkdir(parents=True)
    (target / "vendor-manifest.json").write_text("{}\n", encoding="utf-8")
    source = root / "contracts" / "om-api" / "v1.openapi.json"
    release = {
        "api_version": "portfolio.api.v1",
        "contract_release": "pm-api-v1.0.0",
        "canonical_path": str(source.relative_to(root)),
        "sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest(),
    }

    manifest = vendor_release(
        source_root=root,
        consumer_root=consumer,
        upstream_commit="b" * 40,
        release=release,
    )

    assert manifest["upstream_contract_release"] == "pm-api-v1.0.0"
    assert manifest["upstream_commit"] == "b" * 40
    assert json.loads((target / "vendor-manifest.json").read_text()) == manifest
    assert (target / "v1.openapi.json").read_bytes() == source.read_bytes()
