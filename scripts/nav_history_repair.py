#!/usr/bin/env python3
"""Canonical nav_history repair CLI entrypoint.

Subcommands:
- backfill: recompute derived NAV fields and persist through bulk upsert.
- patch: apply a validated field patch file.

New automation should use this entrypoint so all nav_history writes are easy to
audit. The implementation lives in ``src.maintenance.nav_history_repair``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Canonical nav_history repair entrypoint.")
    sub = parser.add_subparsers(dest="command", required=True)

    backfill = sub.add_parser("backfill", help="recompute/backfill nav_history derived fields")
    backfill.add_argument("--account", default="lx")
    backfill.add_argument("--input", help="Input JSON from audit/recompute output")
    backfill.add_argument("--from", dest="d_from", help="YYYY-MM-DD (required if --input absent)")
    backfill.add_argument("--to", dest="d_to", help="YYYY-MM-DD (required if --input absent)")
    backfill.add_argument("--mode", choices=["replace", "upsert"], default="replace")
    backfill.add_argument("--allow-partial", action="store_true")
    backfill_write = backfill.add_mutually_exclusive_group()
    backfill_write.add_argument("--apply", action="store_true", help="Actually write to Feishu")
    backfill_write.add_argument("--dry-run", action="store_true", help="Force dry-run (explicit no-write)")
    backfill.add_argument("--limit", type=int, default=0, help="Only process first N dates (debug)")

    patch = sub.add_parser("patch", help="apply validated nav_history patch file")
    patch.add_argument("--account", default=None)
    patch.add_argument("--patch-file")
    patch.add_argument("--mode", choices=["strong-consistency-gap"], default="strong-consistency-gap")
    patch_write = patch.add_mutually_exclusive_group(required=True)
    patch_write.add_argument("--dry-run", action="store_true")
    patch_write.add_argument("--apply", action="store_true")
    patch_write.add_argument("--resume-journal")
    patch_write.add_argument("--rollback-journal")
    patch.add_argument("--backup-file", default=None)
    patch.add_argument("--no-validate", action="store_true")
    patch.add_argument("--validate-level", choices=["basic", "full"], default="basic")
    patch.add_argument("--validate-scope", choices=["changed", "patched", "all"], default="changed")

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "backfill":
        from src.maintenance.nav_history_repair import backfill

        backfill.run(args)
        return 0

    if args.command == "patch":
        from src.maintenance.nav_history_repair import patch

        result = patch.run(args)
        return 0 if not isinstance(result, dict) or result.get("success") else 1

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
