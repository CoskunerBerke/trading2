#!/usr/bin/env bash
# Geri alma: bash deploy/rollback.sh [COMMIT]
# COMMIT verilmezse update.sh'nin kaydettiği son iyi commit kullanılır. State'e dokunmaz (gerekirse restore.sh).
set -euo pipefail
BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
APP="$BASE/app"; VENV="$BASE/venv"
TARGET="${1:-$(cat "$BASE/.last_good_commit" 2>/dev/null || true)}"
if [[ -z "$TARGET" ]]; then echo "commit verin ya da $BASE/.last_good_commit bulunmalı"; exit 1; fi
cd "$APP"
echo "== $(git rev-parse --short HEAD) -> $TARGET"
git fetch --all --prune
git checkout -q "$TARGET"
"$VENV/bin/pip" install -q -r requirements.txt
"$VENV/bin/python" -m tradingbot doctor --quick || echo "(doktor uyarı verdi)"
systemctl restart tradingbot-worker.service tradingbot-dashboard.service
sleep 3; systemctl --no-pager status tradingbot-worker.service | head -5
echo "geri alındı. State değişmedi; gerekiyorsa deploy/restore.sh ile yedekten dönün."
