#!/usr/bin/env bash
# Elle yedek: bash deploy/backup.sh [hourly|daily|weekly|manual]  (varsayılan manual)
# CLI dört türü de tanır ve arşivi DOĞRULAR; doğrulama düşerse çıkış kodu 1 olur.
set -euo pipefail
KIND="${1:-manual}"
BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
PYBIN="${TRADINGBOT_PY:-$BASE/venv/bin/python}"
export TRADINGBOT_DATA="${TRADINGBOT_DATA:-$BASE/data}"
export TRADINGBOT_STATE_DIR="${TRADINGBOT_STATE_DIR:-$TRADINGBOT_DATA/state}"
export TRADINGBOT_BACKUPS_DIR="${TRADINGBOT_BACKUPS_DIR:-$TRADINGBOT_DATA/backups}"
cd "$BASE/app"
exec "$PYBIN" -m tradingbot backup "--$KIND"
