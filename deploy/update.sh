#!/usr/bin/env bash
# Güncelleme: bash deploy/update.sh [BRANCH]
# yedek al -> git pull --ff-only -> pip -> doctor -> restart. Doktor başarısızsa önceki commit'e geri döner. State'e dokunmaz.
set -euo pipefail
BRANCH="${1:-}"
BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"
export TRADINGBOT_DATA="${TRADINGBOT_DATA:-$BASE/data}"
export TRADINGBOT_STATE_DIR="${TRADINGBOT_STATE_DIR:-$TRADINGBOT_DATA/state}"
cd "$APP"
PREV=$(git rev-parse HEAD)
echo "$PREV" > "$BASE/.last_good_commit"
echo "== yedek"; bash "$APP/deploy/backup.sh" manual
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
