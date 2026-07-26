#!/usr/bin/env python3
"""Export or verify the OM-facing PM API v1 OpenAPI contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.service.http import create_app

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "contracts" / "om-api" / "v1.openapi.json"


def build_contract() -> dict:
    document = create_app().openapi()
    document["paths"] = {
        path: value
        for path, value in document["paths"].items()
        if path.startswith("/api/v1/")
    }
    return document


def render_contract() -> str:
    return json.dumps(build_contract(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_contract()
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"OpenAPI contract drift: regenerate {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
