#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/pm" config doctor --require-secure-feishu --json
"$ROOT/pm" events status --json
