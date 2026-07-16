#!/usr/bin/env bash
set -euo pipefail

# Linux bootstrap installer inspired by the Hermes Agent install flow:
# - clear inherited Python environment that can shadow the checkout;
# - install/update code in a stable FHS-style directory;
# - create a venv and install dependencies;
# - delegate system config/systemd writes to scripts/install_linux.py.

unset PYTHONPATH
unset PYTHONHOME

REPO_URL="${PORTFOLIO_REPO_URL:-https://github.com/liuxie066/portfolio-management.git}"
REF="${PORTFOLIO_REF:-main}"
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
OVERWRITE_CONFIG=false
PIP_INDEX_URL_VALUE="${PIP_INDEX_URL:-}"
SCRIPT_DIR=""
SCRIPT_REPO_ROOT=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -d "$SCRIPT_DIR/../.git" ]]; then
    SCRIPT_REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  fi
fi

usage() {
  cat <<'USAGE'
portfolio-management installer

Usage:
  scripts/install.sh [options]

Options:
  --apply                 Write config/env/systemd/launcher files.
  --enable-timer          Enable and start morning NAV and evening Futu timers.
  --overwrite-config      Replace an existing config.yaml.
  --repo URL              Git repository URL.
  --ref REF               Branch, tag, or commit to checkout (default: main).
  --dir PATH              App checkout directory.
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

By default this prepares code and the Python environment, then prints the
system install plan. Add --apply to write system files.

When /etc/options-monitor/options-monitor.env exists, --apply copies only
OM_FEISHU_BOT_APP_ID, OM_FEISHU_BOT_APP_SECRET, and
OM_FEISHU_BOT_USER_OPEN_ID into portfolio-management.env.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=true; shift ;;
    --enable-timer) ENABLE_TIMER=true; shift ;;
    --overwrite-config) OVERWRITE_CONFIG=true; shift ;;
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

need_cmd git
need_cmd "$PYTHON_BIN"

prepare_checkout() {
  if [[ -n "$SCRIPT_REPO_ROOT" && "$APP_DIR" == "$SCRIPT_REPO_ROOT" ]]; then
    log "Using current checkout: $APP_DIR"
    return
  fi

  if [[ -d "$APP_DIR/.git" ]]; then
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
  if [[ "$OVERWRITE_CONFIG" == true ]]; then
    args+=(--overwrite-config)
  fi

  log "Installing deployment assets"
  "$APP_DIR/.venv/bin/python" "${args[@]}"
}

prepare_checkout
prepare_venv
run_asset_installer

cat <<EOF

Next:
  edit $CONFIG_DIR/config.yaml
  $LAUNCHER config doctor --json
  $LAUNCHER daily-job --json --no-service

To enable the timer later:
  sudo $APP_DIR/scripts/install.sh --apply --enable-timer --dir $APP_DIR
EOF
