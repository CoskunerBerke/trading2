#!/usr/bin/env bash
# Güncelleme: bash deploy/update.sh [BRANCH]
# DOĞRULANMIŞ yedek -> git pull --ff-only -> pip -> doctor -> restart.
# Doktor başarısızsa önceki commit'e geri döner. State'e DOKUNMAZ.
#
# SIRA ÖNEMLİ: yedek ve DOĞRULAMASI, git ya da servis mutasyonundan ÖNCE tamamlanır.
# Yedek alınamaz ya da doğrulanamazsa deployment hiç başlamaz — rollback ağı olmadan
# ilerlemek, geri dönüşü olmayan bir adımı kör atmaktır.
set -euo pipefail
BRANCH="${1:-}"
BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"
export TRADINGBOT_DATA="${TRADINGBOT_DATA:-$BASE/data}"
export TRADINGBOT_STATE_DIR="${TRADINGBOT_STATE_DIR:-$TRADINGBOT_DATA/state}"
export TRADINGBOT_BACKUPS_DIR="${TRADINGBOT_BACKUPS_DIR:-$TRADINGBOT_DATA/backups}"
cd "$APP"

# 1) YEDEK + DOĞRULAMA (git/servis mutasyonundan ÖNCE, fail-fast)
# `backup.sh manual` CLI'da `--manual` bayrağı olmadığı için sessizce kırıktı; CLI artık
# dört türü de tanıyor ve arşivi sha256 + tar okunabilirliğiyle DOĞRULUYOR (çıkış kodu 1).
echo "== yedek (doğrulamalı)"
if ! bash "$APP/deploy/backup.sh" "${TRADINGBOT_BACKUP_KIND:-manual}"; then
  echo "YEDEK BAŞARISIZ ya da DOĞRULANAMADI -> deployment DURDURULDU (git/servis dokunulmadı)" >&2
  exit 1
fi

# 2) Rollback işaretçisi ancak yedek güvenceye alındıktan SONRA yazılır.
PREV=$(git rev-parse HEAD)
echo "$PREV" > "$BASE/.last_good_commit"

echo "== git"; git fetch --all --prune
if [[ -n "$BRANCH" ]]; then git checkout -q "$BRANCH"; fi
git pull --ff-only
echo "== pip"; "$VENV/bin/pip" install -q -r requirements.txt
echo "== doktor"
if ! "$VENV/bin/python" -m tradingbot doctor --quick; then
  echo "doktor başarısız -> geri alınıyor: $PREV"
  git checkout -q "$PREV"
  "$VENV/bin/pip" install -q -r requirements.txt
  exit 1
fi
echo "== restart"; systemctl restart tradingbot-worker.service tradingbot-dashboard.service
sleep 3; systemctl --no-pager status tradingbot-worker.service | head -5
echo "güncellendi: $PREV -> $(git rev-parse --short HEAD)"
