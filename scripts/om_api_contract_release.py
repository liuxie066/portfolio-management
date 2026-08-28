#!/usr/bin/env python3
"""Validate and vendor immutable PM API contracts consumed by OM."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


class APIContractError(RuntimeError):
    """Raised when an OM-facing PM API contract is inconsistent."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise APIContractError(f"JSON object required: {path}")
    return value


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise APIContractError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def validate_release(
    *,
    tag: str,
    source_root: Path = DEFAULT_ROOT,
    require_annotated_tag: bool = True,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    manifest = _json(source_root / "contracts" / "om-api" / "manifest.json")
    if manifest.get("release_state") != "published" or not manifest.get("contract_release"):
        raise APIContractError("PM API contract is not published; validate the checked-in SHA-256 instead")
    if tag != manifest.get("contract_release"):
        raise APIContractError("tag does not match manifest contract_release")
    if require_annotated_tag and _git(source_root, "cat-file", "-t", tag) != "tag":
        raise APIContractError(f"{tag} must be an annotated tag")
    contract = source_root / str(manifest["canonical_path"])
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()
    if digest != manifest.get("sha256"):
        raise APIContractError("OpenAPI SHA-256 does not match manifest")
    document = _json(contract)
    if not document.get("openapi") or not document.get("paths"):
        raise APIContractError("invalid OpenAPI document")
    if any(not path.startswith("/api/v1/") for path in document["paths"]):
        raise APIContractError("OM contract may only contain /api/v1 paths")
    if "/api/v1/analysis/cash-facts" in document["paths"]:
        raise APIContractError("cash-facts is not onboarded in PM API v1")
    return {
        "api_version": manifest["api_version"],
        "contract_release": manifest["contract_release"],
        "canonical_path": manifest["canonical_path"],
        "sha256": digest,
        "tag_commit": _git(source_root, "rev-list", "-n", "1", tag),
    }


def vendor_release(
    *,
    source_root: Path = DEFAULT_ROOT,
    consumer_root: Path,
    upstream_commit: str,
    release: dict[str, Any],
) -> dict[str, Any]:
    if len(upstream_commit) != 40 or any(char not in "0123456789abcdef" for char in upstream_commit):
        raise APIContractError("upstream_commit must be a lowercase 40-character Git SHA")
    source_root = source_root.resolve()
    consumer_root = consumer_root.resolve()
    target_dir = consumer_root / "contracts" / "portfolio-management"
    manifest_path = target_dir / "vendor-manifest.json"
    if not manifest_path.is_file():
        raise APIContractError(f"consumer vendor manifest not found: {manifest_path}")
    source_contract = source_root / str(release["canonical_path"])
    target_contract = target_dir / source_contract.name
    target_contract.write_bytes(source_contract.read_bytes())
    manifest = {
        "api_version": release["api_version"],
        "upstream_repository": "portfolio-management",
        "upstream_contract_release": release["contract_release"],
        "upstream_commit": upstream_commit,
        "contract_path": str(target_contract.relative_to(consumer_root)),
        "sha256": release["sha256"],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "vendor"):
        command = subparsers.add_parser(name)
        command.add_argument("--tag", required=True)
        command.add_argument("--source-root", type=Path, default=DEFAULT_ROOT)
        command.add_argument("--allow-lightweight-tag", action="store_true")
        if name == "vendor":
            command.add_argument("--consumer-root", type=Path, required=True)
            command.add_argument("--upstream-commit", required=True)
    args = parser.parse_args()
    release = validate_release(
        tag=args.tag,
        source_root=args.source_root,
        require_annotated_tag=not args.allow_lightweight_tag,
    )
    result = (
        vendor_release(
            source_root=args.source_root,
            consumer_root=args.consumer_root,
            upstream_commit=args.upstream_commit,
            release=release,
        )
        if args.command == "vendor"
        else release
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
