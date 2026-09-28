#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPY="$ROOT/.venv/bin/python"
unset PYTHONHOME
export PYTHONPATH="$ROOT"

if [[ -x "$VPY" && ( "${PM_REQUIRE_VENV:-}" != "1" || -f "$ROOT/.venv/pyvenv.cfg" ) ]]; then
  exec "$VPY" "$ROOT/scripts/pm.py" "$@"
fi

if [[ "${PM_REQUIRE_VENV:-}" == "1" ]]; then
  echo "portfolio-management venv Python is unavailable" >&2
  exit 1
fi

exec python3 "$ROOT/scripts/pm.py" "$@"
