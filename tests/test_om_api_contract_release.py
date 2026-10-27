from __future__ import annotations

import hashlib
import json
import subprocess

import pytest

from scripts.om_api_contract_release import (
    APIContractError,
    validate_release,
    vendor_release,
)


@pytest.fixture
def published_contract(tmp_path):
    """A tagged test repository, independent of the unreleased working tree."""
    root = tmp_path / "producer"
    target = root / "contracts" / "om-api"
    target.mkdir(parents=True)
    source = target / "v1.openapi.json"
    source.write_text(json.dumps({"openapi": "3.1.0", "paths": {"/api/v1/accounts": {}}}))
    manifest = {
        "api_version": "portfolio.api.v1",
        "release_state": "published",
        "contract_release": "pm-api-v1.0.0",
        "canonical_path": "contracts/om-api/v1.openapi.json",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    (target / "manifest.json").write_text(json.dumps(manifest))

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Contract Test", "-c", "user.email=contract@example.invalid", *args],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init")
    git("add", "contracts")
    git("-c", "commit.gpgsign=false", "commit", "-m", "test published contract")
    git("-c", "tag.gpgsign=false", "tag", "-a", "pm-api-v1.0.0", "-m", "test contract tag")
    return root, source, manifest, git


def test_vendor_openapi_contract_updates_om_manifest(tmp_path, published_contract) -> None:
    root, source, _, _ = published_contract
    consumer = tmp_path / "om"
    target = consumer / "contracts" / "portfolio-management"
    target.mkdir(parents=True)
    (target / "vendor-manifest.json").write_text("{}\n", encoding="utf-8")
    release = validate_release(tag="pm-api-v1.0.0", source_root=root)
    manifest = vendor_release(
        source_root=root,
        consumer_root=consumer,
        upstream_commit=release["tag_commit"],
        release=release,
    )
    assert manifest["upstream_contract_release"] == "pm-api-v1.0.0"
    assert manifest["upstream_commit"] == release["tag_commit"]
    assert json.loads((target / "vendor-manifest.json").read_text()) == manifest
    assert (target / "v1.openapi.json").read_bytes() == source.read_bytes()


def test_current_unreleased_contract_is_not_a_published_tag() -> None:
    with pytest.raises(APIContractError, match="not published"):
        validate_release(tag="pm-api-v1.0.0")


def test_published_contract_still_requires_annotated_tag_and_hash(published_contract):
    root, source, _, git = published_contract
    release = validate_release(tag="pm-api-v1.0.0", source_root=root)
    assert release["contract_release"] == "pm-api-v1.0.0"
    assert release["tag_commit"] == git("rev-parse", "HEAD")
    assert release["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(APIContractError, match="tag does not match"):
        validate_release(tag="pm-api-v9.9.9", source_root=root)
    git("tag", "-d", "pm-api-v1.0.0")
    git("-c", "tag.gpgsign=false", "tag", "pm-api-v1.0.0")
    with pytest.raises(APIContractError, match="annotated tag"):
        validate_release(tag="pm-api-v1.0.0", source_root=root)
    source.write_text(source.read_text() + "\n")
    with pytest.raises(APIContractError, match="SHA-256"):
        validate_release(tag="pm-api-v1.0.0", source_root=root, require_annotated_tag=False)


@pytest.mark.parametrize("path", ["/holdings", "/api/v1/analysis/cash-facts"])
def test_published_contract_still_rejects_unsupported_paths(published_contract, path):
    root, source, manifest, _ = published_contract
    source.write_text(json.dumps({"openapi": "3.1.0", "paths": {path: {}}}))
    manifest["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    (source.parent / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(APIContractError, match="only contain|not onboarded"):
        validate_release(tag="pm-api-v1.0.0", source_root=root)
