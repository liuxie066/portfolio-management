#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VPY="$ROOT/.venv/bin/python"
unset PYTHONHOME
export PYTHONPATH="$ROOT"

if [[ -x "$VPY" ]]; then
  exec "$VPY" "$ROOT/scripts/pm.py" "$@"
fi

exec python3 "$ROOT/scripts/pm.py" "$@"
