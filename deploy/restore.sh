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
  # (2026-09-30) CLI'da `--dry-run` bayrağı YOK: `--yes` verilmeyen çağrı zaten kuru çalıştırmadır
  exec "$PYBIN" -m tradingbot restore "$ARCHIVE"
fi
echo "worker durduruluyor..."; systemctl stop tradingbot-worker.service || true
# (2026-09-30) `--yes` olmadan komut yalnız KURU çalıştırmaydı (geri yükleme hiç yapılmıyordu). Geri yükleme, saatlik
# yedekte olmayan ortak deneyim / tavsiye segmentlerini önceki state'ten (state.pre-restore-<ts>) sha256 ile geri kopyalar;
# sonuçtaki "xp_segments.missing" boş değilse eksikler en yeni UTC-00 / günlük yedekten elle kopyalanmalıdır (gölge danışman
# o segmentte bekler: WAITING_SEGMENTS; karar ETKİLENMEZ).
OUT="$("$PYBIN" -m tradingbot restore "$ARCHIVE" --yes)"
echo "$OUT"
if grep -q '"missing": \[$' <<<"$OUT"; then
  echo "UYARI: geri yüklenen state'te eksik ortak deneyim segmenti var (xp_segments.missing) — en yeni UTC-00/günlük yedekten kopyalayın"
fi
echo "worker başlatılıyor..."; systemctl start tradingbot-worker.service
systemctl --no-pager status tradingbot-worker.service | head -5
