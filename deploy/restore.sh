#!/usr/bin/env bash
# Geri yükleme: bash deploy/restore.sh <arşiv.tar.gz> [--dry-run]
# Worker durdurulur, arşiv doğrulanır (sha256), mevcut state `state.pre-restore-<ts>` olarak korunur, yeni state'in sahibi
# eski state'in sahibine (servis kullanıcısı) verilir, worker yeniden başlar. Hata olursa worker DURMUŞ kalır ve ne yapılacağı yazılır.
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
STATE="$TRADINGBOT_STATE_DIR"
# (2026-09-30) SAHİPLİK: geri yükleme root olarak çalışır ve arşiv `tar` "data" süzgeciyle açılır → açılan dosyaların sahibi
# root olur; worker servis kullanıcısıyla çalıştığı için root'a ait state'i okuyamaz/yazamaz. Bu yüzden geri yüklemeden ÖNCE
# mevcut state klasörünün (yoksa data klasörünün) sahibi alınır, geri yüklemeden SONRA yeni state'e (ve varsa yeni
# vault.restored-* kopyasına) aynen verilir. Önceki state (state.pre-restore-<ts>) eski sahibinde kalır.
OWN_REF="$STATE"; [[ -d "$OWN_REF" ]] || OWN_REF="$TRADINGBOT_DATA"
OWNER="$(stat -c '%U:%G' "$OWN_REF")"
restore_failed() {
  echo "GERİ YÜKLEME BAŞARISIZ (satır $1). Worker DURDURULDU ve yeniden BAŞLATILMADI." >&2
  echo "  Mevcut state: $STATE — önceki kopyalar: $TRADINGBOT_DATA/state.pre-restore-* (varsa)." >&2
  echo "  Hata doğrulamada olduysa state'e dokunulmamıştır. Kontrol edip başlatın: sudo systemctl start tradingbot-worker.service" >&2
}
trap 'restore_failed $LINENO' ERR
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
echo "sahiplik geri veriliyor: $OWNER → $STATE"
chown -R "$OWNER" "$STATE"
for v in "$TRADINGBOT_DATA"/vault.restored-*; do
  if [[ -e "$v" ]] && [[ "$(stat -c '%U' "$v")" == "root" ]]; then chown -R "$OWNER" "$v"; fi
done
trap - ERR
echo "worker başlatılıyor..."; systemctl start tradingbot-worker.service
systemctl --no-pager status tradingbot-worker.service | head -5
