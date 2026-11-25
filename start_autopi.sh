#!/usr/bin/env bash
set -euo pipefail

# AutoPi: 24/7 Digital Employee & Supervisor Hub Startup Script
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH:-}"

PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python3"
if [ ! -f "${PYTHON_BIN}" ]; then
  PYTHON_BIN="python3"
fi

HOST="${AUTOPI_HOST:-127.0.0.1}"
PORT="${AUTOPI_PORT:-9100}"
DIR="${AUTOPI_DIR:-${PROJECT_ROOT}/.autopi}"
export GEMINI_ACCOUNT_URL="${GEMINI_ACCOUNT_URL:-https://gemini.google.com/u/0/app?pageId=none}"

echo "======================================================================"
echo "🚀 启动 AutoPi 24 小时数字员工自驱引擎 & 老板监工前台"
echo "======================================================================"

exec "${PYTHON_BIN}" -m autopi start --port "${PORT}" --host "${HOST}" --dir "${DIR}" "$@"
