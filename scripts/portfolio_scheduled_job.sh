#!/usr/bin/env bash
set -uo pipefail

MODE="${1:-}"
PM_BIN="${PORTFOLIO_PM_BIN:-/usr/local/bin/pm}"

if [[ "$MODE" != "morning" && "$MODE" != "evening" ]]; then
  echo "usage: $0 morning|evening" >&2
  exit 2
fi
if [[ ! -x "$PM_BIN" ]]; then
  echo "pm launcher is not executable: $PM_BIN" >&2
  exit 1
fi

sync_failed=0
"$PM_BIN" futu sync --account lx --write --confirm --json --no-service || sync_failed=1

"$PM_BIN" futu sync --account sy --write --confirm --json --no-service || sync_failed=1

if [[ "$sync_failed" -ne 0 ]]; then
  echo "Futu holdings sync failed; NAV job was not started" >&2
  exit 1
fi

if [[ "$MODE" == "morning" ]]; then
  exec "$PM_BIN" daily-job --accounts lx,hb,sy --write --confirm --json --no-service
fi
