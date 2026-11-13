#!/usr/bin/env bash
set -euo pipefail

# Linux bootstrap installer inspired by the Hermes Agent install flow:
# - clear inherited Python environment that can shadow the checkout;
# - install a verified runtime release in a stable FHS-style directory;
# - create a venv and install dependencies;
# - delegate system config/systemd writes to scripts/install_linux.py.

unset PYTHONPATH
unset PYTHONHOME

REPO_URL="${PORTFOLIO_REPO_URL:-https://github.com/liuxie066/portfolio-management.git}"
REF="${PORTFOLIO_REF:-latest}"
SOURCE=false
RELEASE_TMP=""
APP_DIR="${PORTFOLIO_APP_DIR:-}"
CONFIG_DIR="${PORTFOLIO_CONFIG_DIR:-}"
DATA_DIR="${PORTFOLIO_DATA_DIR:-}"
REPORTS_DIR="${PORTFOLIO_REPORTS_DIR:-}"
SYSTEMD_DIR="${PORTFOLIO_SYSTEMD_DIR:-/etc/systemd/system}"
LAUNCHER="${PORTFOLIO_LAUNCHER:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUN_USER="${SUDO_USER:-${USER:-portfolio}}"
APPLY=false
ENABLE_TIMER=false
ENABLE_API_SERVICE=false
ENABLE_QUALITY_TIMER=false
ENABLE_HOLDINGS_EVENT_LISTENER=false
OVERWRITE_CONFIG=false
PIP_INDEX_URL_VALUE="${PIP_INDEX_URL:-}"
SCRIPT_DIR=""
SCRIPT_REPO_ROOT=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$SCRIPT_DIR/../VERSION" ]]; then
    SCRIPT_REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  fi
fi

usage() {
  cat <<'USAGE'
portfolio-management installer

Usage:
  scripts/install.sh [options]

Options:
  --apply                 Verify config/credentials, then write env/systemd/launcher files.
  --enable-timer          Enable morning NAV, evening Futu, Cash Flow, and receipt timers.
  --enable-api-service     Enable and start the loopback-only portfolio HTTP API.
  --enable-quality-timer   Enable the independent 15-minute quality refresh timer.
  --enable-holdings-event-listener
                           Enable the Feishu holdings long-connection service.
  --overwrite-config      Disabled safety flag; config.yaml is never overwritten.
  --repo URL              GitHub repository URL.
  --ref REF               Release tag (default: latest stable release).
  --source                Use a full Git checkout for development or existing source deployments.
                          With --source, --ref also accepts branches/commits (default: main).
  --dir PATH              App installation directory.
  --config-dir PATH       Config directory.
  --data-dir PATH         Runtime data directory.
  --reports-dir PATH      Report output directory.
  --systemd-dir PATH      systemd unit directory.
  --launcher PATH         pm launcher path.
  --python PATH           Python executable used to create the venv.
  --run-user USER         systemd service user.
  --pip-index-url URL     Optional pip index URL.
  -h, --help              Show this help.

Defaults:
  root Linux: code=/opt/portfolio-management/current, launcher=/usr/local/bin/pm
  user Linux: code=~/.portfolio-management/current, launcher=~/.local/bin/pm

By default this downloads the runtime release and verifies SHA256SUMS, prepares
its Python environment, then prints the system install plan. Runtime packages
exclude tests, development records and release tooling. Add --apply to write
system files. Full Git checkouts require --source; existing source deployments
are never converted or cleaned automatically.

When /etc/options-monitor/options-monitor.env exists, the installer scans only
legacy Feishu role key names as migration evidence and copies no values. The
explicit feishu.agent/feishu.listener mapping and encrypted credentials
pm-feishu-agent-app-secret and pm-feishu-listener-app-secret must already be
prepared. The installer never imports, copies from the source, creates,
decrypts, or prints secret values.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=true; shift ;;
    --enable-timer) ENABLE_TIMER=true; shift ;;
    --enable-api-service) ENABLE_API_SERVICE=true; shift ;;
    --enable-quality-timer) ENABLE_QUALITY_TIMER=true; shift ;;
    --enable-holdings-event-listener) ENABLE_HOLDINGS_EVENT_LISTENER=true; shift ;;
    --overwrite-config) OVERWRITE_CONFIG=true; shift ;;
    --source) SOURCE=true; shift ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --dir) APP_DIR="$2"; shift 2 ;;
    --config-dir) CONFIG_DIR="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --reports-dir) REPORTS_DIR="$2"; shift 2 ;;
    --systemd-dir) SYSTEMD_DIR="$2"; shift 2 ;;
    --launcher) LAUNCHER="$2"; shift 2 ;;
    --python) PYTHON_BIN="$2"; shift 2 ;;
    --run-user) RUN_USER="$2"; shift 2 ;;
    --pip-index-url) PIP_INDEX_URL_VALUE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$APP_DIR" ]]; then
  if [[ -n "$SCRIPT_REPO_ROOT" ]]; then
    APP_DIR="$SCRIPT_REPO_ROOT"
  elif [[ "$(uname -s)" == "Linux" && "$(id -u)" -eq 0 ]]; then
    APP_DIR="/opt/portfolio-management/current"
  else
    APP_DIR="$HOME/.portfolio-management/current"
  fi
fi
if [[ -z "$CONFIG_DIR" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then
    CONFIG_DIR="/etc/portfolio-management"
  else
    CONFIG_DIR="$HOME/.portfolio-management"
  fi
fi
if [[ -z "$DATA_DIR" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then
    DATA_DIR="/var/lib/portfolio-management/.data"
  else
    DATA_DIR="$HOME/.portfolio-management/.data"
  fi
fi
if [[ -z "$REPORTS_DIR" ]]; then
  if [[ "$(id -u)" -eq 0 ]]; then
    REPORTS_DIR="/var/lib/portfolio-management/reports"
  else
    REPORTS_DIR="$HOME/.portfolio-management/reports"
  fi
fi
if [[ -z "$LAUNCHER" ]]; then
  if [[ "$(uname -s)" == "Linux" && "$(id -u)" -eq 0 ]]; then
    LAUNCHER="/usr/local/bin/pm"
  else
    LAUNCHER="$HOME/.local/bin/pm"
  fi
fi

log() {
  printf '==> %s\n' "$*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

need_cmd "$PYTHON_BIN"

prepare_release() {
  if [[ -e "$APP_DIR/.git" ]]; then
    echo "Install directory is a source checkout; use --source explicitly: $APP_DIR" >&2
    exit 1
  fi
  if [[ -n "$SCRIPT_REPO_ROOT" && "$APP_DIR" == "$SCRIPT_REPO_ROOT" ]] &&
     [[ "$REF" == latest || "$REF" == "v$(<"$APP_DIR/VERSION")" ]]; then
    log "Using current runtime installation: $APP_DIR"
    return
  fi
  if [[ -e "$APP_DIR" && ! -f "$APP_DIR/VERSION" ]]; then
    echo "Install directory exists but is not a runtime installation: $APP_DIR" >&2
    exit 1
  fi
  need_cmd curl
  local release_url="${REPO_URL%.git}/releases"
  if [[ "$REF" == latest ]]; then
    local latest_url
    latest_url="$(curl -fsSL -o /dev/null -w '%{url_effective}' "$release_url/latest")"
    REF="${latest_url##*/}"
  fi
  if [[ ! "$REF" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    echo "Runtime installation requires a release tag vX.Y.Z; branches/commits require --source." >&2
    exit 1
  fi
  RELEASE_TMP="$(mktemp -d)"
  trap 'rm -rf "$RELEASE_TMP"' EXIT
  local archive="portfolio-management-${REF#v}.tar.gz"
  log "Downloading runtime release $REF"
  curl -fsSL "$release_url/download/$REF/$archive" -o "$RELEASE_TMP/$archive"
  curl -fsSL "$release_url/download/$REF/SHA256SUMS" -o "$RELEASE_TMP/SHA256SUMS"
  "$PYTHON_BIN" -I - "$RELEASE_TMP" "$archive" "$APP_DIR" "${REF#v}" <<'PY'
import hashlib
from pathlib import Path
import shutil
import sys
import tarfile

staging, name, destination, version = sys.argv[1:]
staging = Path(staging)
archive = staging / name
expected = next(
    (line.split()[0] for line in (staging / "SHA256SUMS").read_text().splitlines()
     if len(line.split()) == 2 and line.split()[1] == name), None,
)
if expected != hashlib.sha256(archive.read_bytes()).hexdigest():
    raise SystemExit("Runtime release checksum mismatch")
with tarfile.open(archive) as source:
    source.extractall(staging / "unpacked", filter="data")
root = staging / "unpacked" / f"portfolio-management-{version}"
if (root / "VERSION").read_text().strip() != version:
    raise SystemExit("Runtime release version mismatch")
shutil.copytree(root, destination, dirs_exist_ok=True)
PY
}

prepare_checkout() {
  need_cmd git
  if [[ "$REF" == latest ]]; then
    REF=main
  fi
  if [[ -n "$SCRIPT_REPO_ROOT" && "$APP_DIR" == "$SCRIPT_REPO_ROOT" && -e "$APP_DIR/.git" ]]; then
    log "Using current checkout: $APP_DIR"
    return
  fi

  if [[ -e "$APP_DIR/.git" ]]; then
    log "Updating existing checkout: $APP_DIR"
    git -C "$APP_DIR" fetch --tags origin
    if git -C "$APP_DIR" rev-parse --verify --quiet "origin/$REF" >/dev/null; then
      git -C "$APP_DIR" checkout -B "$REF" "origin/$REF"
      git -C "$APP_DIR" pull --ff-only origin "$REF"
    else
      git -C "$APP_DIR" checkout "$REF"
    fi
    return
  fi

  if [[ -e "$APP_DIR" ]]; then
    echo "Install directory exists but is not a git checkout: $APP_DIR" >&2
    exit 1
  fi

  log "Cloning $REPO_URL -> $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone "$REPO_URL" "$APP_DIR"
  git -C "$APP_DIR" checkout "$REF"
}

prepare_venv() {
  log "Preparing Python virtualenv"
  "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
  "$APP_DIR/.venv/bin/python" -m pip install -U pip
  if [[ -n "$PIP_INDEX_URL_VALUE" ]]; then
    "$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt" -i "$PIP_INDEX_URL_VALUE"
  else
    "$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
  fi
}

run_asset_installer() {
  local args=(
    "$APP_DIR/scripts/install_linux.py"
    --app-dir "$APP_DIR"
    --config-dir "$CONFIG_DIR"
    --data-dir "$DATA_DIR"
    --reports-dir "$REPORTS_DIR"
    --systemd-dir "$SYSTEMD_DIR"
    --python "$APP_DIR/.venv/bin/python"
    --launcher "$LAUNCHER"
    --run-user "$RUN_USER"
  )
  if [[ "$APPLY" == true ]]; then
    args+=(--apply)
  fi
  if [[ "$ENABLE_TIMER" == true ]]; then
    args+=(--enable-timer)
  fi
  if [[ "$ENABLE_API_SERVICE" == true ]]; then
    args+=(--enable-api-service)
  fi
  if [[ "$ENABLE_QUALITY_TIMER" == true ]]; then
    args+=(--enable-quality-timer)
  fi
  if [[ "$ENABLE_HOLDINGS_EVENT_LISTENER" == true ]]; then
    args+=(--enable-holdings-event-listener)
  fi
  if [[ "$OVERWRITE_CONFIG" == true ]]; then
    args+=(--overwrite-config)
  fi

  log "Installing deployment assets"
  "$APP_DIR/.venv/bin/python" "${args[@]}"
}

if [[ "$SOURCE" == true ]]; then
  prepare_checkout
else
  prepare_release
fi
prepare_venv
run_asset_installer

SOURCE_OPTION=""
if [[ "$SOURCE" == true ]]; then
  SOURCE_OPTION=" --source"
fi
cat <<EOF

Next:
  create and review $CONFIG_DIR/config.yaml with canonical non-secret Agent/Listener routing
  provision both role-named encrypted credentials
  rerun this installer with --apply
  sudo systemctl start portfolio-feishu-preflight.service
  $LAUNCHER daily-job --json --no-service

To enable the timer later:
  sudo $APP_DIR/scripts/install.sh$SOURCE_OPTION --apply --enable-timer --dir $APP_DIR

To enable the loopback portfolio API later:
  sudo $APP_DIR/scripts/install.sh$SOURCE_OPTION --apply --enable-api-service --dir $APP_DIR

To enable quality artifact refresh later:
  sudo $APP_DIR/scripts/install.sh$SOURCE_OPTION --apply --enable-quality-timer --dir $APP_DIR

To enable the holdings event listener after its separate activation preflight:
  sudo $APP_DIR/scripts/install.sh$SOURCE_OPTION --apply --enable-holdings-event-listener --dir $APP_DIR
EOF
