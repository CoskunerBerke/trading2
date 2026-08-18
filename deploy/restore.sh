#!/usr/bin/env bash
# Geri yükleme: bash deploy/restore.sh <arşiv.tar.gz> [--dry-run]
# Worker durdurulur, arşiv doğrulanır (sha256), mevcut state `state.pre-restore-<ts>` olarak korunur, worker yeniden başlar.
set -euo pipefail
ARCHIVE="${1:?arşiv yolu gerekli (ör. /opt/tradingbot/data/backups/daily/tradingbot-daily-....tar.gz)}"
DRY="${2:-}"
BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
PYBIN="${TRADINGBOT_PY:-$BASE/venv/bin/python}"
export TRADINGBOT_DATA="${TRADINGBOT_DATA:-$BASE/data}"
export TRADINGBOT_STATE_DIR="${TRADINGBOT_STATE_DIR:-$TRADINGBOT_DATA/state}"
cd "$BASE/app"
if [[ "$DRY" == "--dry-run" ]]; then
  exec "$PYBIN" -m tradingbot restore "$ARCHIVE" --dry-run
fi
echo "worker durduruluyor..."; systemctl stop tradingbot-worker.service || true
"$PYBIN" -m tradingbot restore "$ARCHIVE"
echo "worker başlatılıyor..."; systemctl start tradingbot-worker.service
systemctl --no-pager status tradingbot-worker.service | head -5
