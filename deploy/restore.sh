#!/usr/bin/env bash
# Geri yükleme: bash deploy/restore.sh <arşiv.tar.gz> [--dry-run]
# Worker durdurulur, arşiv doğrulanır (sha256), mevcut state `state.pre-restore-<ts>` olarak korunur, yeni state'in sahibi
# worker'ın servis kullanıcısı yapılır, worker yeniden başlar. Hata ya da Ctrl+C olursa worker DURMUŞ kalır ve ne yapılacağı
# yazılır. SSH kopması (SIGHUP) ya da çıktının `| head` / `| less`'e verilmesi geri yüklemeyi yarıda KESMEZ.
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
# root olur; worker servis kullanıcısıyla çalıştığı için root'a ait state'i okuyamaz/yazamaz. Bu yüzden sahip geri
# yüklemeden ÖNCE belirlenir ve geri yüklemeden SONRA (worker başlamadan) yeni state'e (ve root'a ait vault.restored-*
# kopyasına) verilir. Sahip = worker biriminin `User=`'ı (sayısal uid:gid); birim User= bildirmiyorsa (root worker ya da
# systemd yok) mevcut state klasörünün (yoksa data klasörünün) GERÇEK sahibi (`stat -L`: sembolik bağın hedefi; sayısal:
# adı olmayan uid'de de çalışır). Önceki state (state.pre-restore-<ts>) eski sahibinde kalır.
OWN_REF="$STATE"; [[ -d "$OWN_REF" ]] || OWN_REF="$TRADINGBOT_DATA"
SVC_U="$(systemctl show -p User --value tradingbot-worker.service 2>/dev/null || true)"
if [[ -n "$SVC_U" ]]; then
  U_ID="$(id -u "$SVC_U" 2>/dev/null || true)"; G_ID="$(id -g "$SVC_U" 2>/dev/null || true)"
  if [[ -z "$U_ID" || -z "$G_ID" ]]; then
    echo "DUR: worker kullanıcısı '$SVC_U' bu sistemde yok — geri yükleme YAPILMADI, worker'a dokunulmadı." >&2; exit 1
  fi
  OWNER="$U_ID:$G_ID"
else
  OWNER="$(stat -L -c '%u:%g' "$OWN_REF")"
  if [[ "${OWNER%%:*}" == 0 ]]; then
    echo "NOT: worker birimi User= bildirmedi ve $OWN_REF root'a ait → yeni state root'a (0:0) verilecek." >&2 || true
  fi
fi
state_id() { stat -L -c '%d:%i' "$STATE" 2>/dev/null || echo yok; }
STATE_ID0="$(state_id)"
restore_failed() {
  [[ -z "${RF_DONE:-}" ]] || return 0
  RF_DONE=1
  {
    echo "GERİ YÜKLEME BAŞARISIZ ($1). Worker DURDURULDU ve yeniden BAŞLATILMADI."
    echo "  Mevcut state: $STATE — önceki kopyalar: $TRADINGBOT_DATA/state.pre-restore-* (varsa)."
    if [[ "$(state_id)" == "$STATE_ID0" ]]; then
      echo "  State'e DOKUNULMADI (hata/kesinti state değişmeden önce oldu)."
    elif [[ ! -d "$STATE" ]]; then
      echo "  DİKKAT: $STATE YOK → en yeni $TRADINGBOT_DATA/state.pre-restore-* klasörünü $STATE adına geri taşıyın (sudo mv)."
    else
      echo "  Yeni state YERİNE KONDU ama sahipliği VERİLMEMİŞ olabilir → önce:  sudo chown -R $OWNER $STATE"
    fi
    for t in "$TRADINGBOT_DATA"/tbrestore-*; do
      if [[ -e "$t" ]]; then echo "  Yarım kalan geçici açma klasörü (state değil; silinebilir): sudo rm -rf $t"; fi
    done
    echo "  Kontrol edip başlatın: sudo systemctl start tradingbot-worker.service"
  } >&2 || true
}
trap 'restore_failed "satır $LINENO"' ERR
trap 'restore_failed "kesildi: Ctrl+C / SIGTERM"; exit 130' INT TERM
# SSH kopması (HUP) ve kapanan boru (`| head`, `| less`, PIPE) betiği ÖLDÜRMEZ: geri yükleme, sahiplik ve worker başlatma
# yine tamamlanır; yazılamayan çıktı satırları `|| true` ile geçilir (alt süreçler de HUP/PIPE'ı yok sayar).
trap '' HUP PIPE
echo "worker durduruluyor..." || true; systemctl stop tradingbot-worker.service || true
# (2026-09-30) `--yes` olmadan komut yalnız KURU çalıştırmaydı (geri yükleme hiç yapılmıyordu). Geri yükleme, saatlik
# yedekte olmayan ortak deneyim / tavsiye segmentlerini önceki state'ten (state.pre-restore-<ts>) sha256 ile geri kopyalar;
# sonuçtaki "xp_segments.missing" boş değilse eksikler en yeni UTC-00 / günlük yedekten elle kopyalanmalıdır (gölge danışman
# o segmentte bekler: WAITING_SEGMENTS; karar ETKİLENMEZ).
OUT="$("$PYBIN" -m tradingbot restore "$ARCHIVE" --yes)"
echo "sahiplik geri veriliyor: $OWNER → $STATE" || true
chown -R "$OWNER" "$STATE"
for v in "$TRADINGBOT_DATA"/vault.restored-*; do
  if [[ -e "$v" ]] && [[ "$(stat -c '%U' "$v")" == "root" ]]; then chown -R "$OWNER" "$v"; fi
done
trap - ERR INT TERM
# Uzun çıktı (arşiv üyeleri) ANCAK sahiplik verildikten sonra basılır: okuyan taraf kapansa da iş bitmiştir.
echo "$OUT" || true
if grep -q '"missing": \[$' <<<"$OUT"; then
  echo "UYARI: geri yüklenen state'te eksik ortak deneyim segmenti var (xp_segments.missing) — en yeni UTC-00/günlük yedekten kopyalayın" || true
fi
echo "worker başlatılıyor..." || true; systemctl start tradingbot-worker.service
systemctl --no-pager status tradingbot-worker.service | head -5 || true
