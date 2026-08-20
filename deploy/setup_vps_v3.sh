#!/usr/bin/env bash
# Trading Bot v3 — Ubuntu 24.04 VPS kurulumu (idempotent; VERİYİ ASLA SİLMEZ).
#   sudo bash deploy/setup_vps_v3.sh <REPO_URL_or_PATH> [BRANCH]
# Yerleşim:
#   /opt/tradingbot/app          kod (git checkout)
#   /opt/tradingbot/venv         python sanal ortamı
#   /opt/tradingbot/data         KALICI VERİ: state/ market/ vault/ backups/ logs/  (asla rm -rf yapılmaz)
#   /opt/tradingbot/env          ortam dosyası (0600, sırlar burada; repo'ya girmez, bu betik okumaz/yazdırmaz)
#   /opt/tradingbot/preflight.sh kaynak-kontrollü systemd ExecStartPre preflight'i (deploy/preflight.sh'ten kurulur)
# Özel repo için SSH deploy key önerilir (salt-okunur):
#   sudo -u tradingbot ssh-keygen -t ed25519 -f /opt/tradingbot/.ssh/id_ed25519 -N ""
#   → public key'i GitHub → repo Settings → Deploy keys (read-only) olarak ekleyin.
# Bu betik hiçbir kimlik bilgisi saklamaz/istemez; ANTHROPIC_API_KEY vb. değerleri /opt/tradingbot/env dosyasına siz yazarsınız.
#
# Fail-fast + dürüst kısmi başarısızlık: her aşama PHASE olarak izlenir; bir komut başarısız olursa
# betik o aşamanın adıyla non-zero çıkar ve "Kuruldu" mesajı BASILMAZ. Tekrar çalıştırmak güvenlidir.
set -Eeuo pipefail

# --- test-edilebilirlik: yollar tek kaynaktan; üretimde varsayılanlar geçerli --------------------
BASE="${TRADINGBOT_BASE:-/opt/tradingbot}"
SYSTEMD_DIR="${TRADINGBOT_SYSTEMD_DIR:-/etc/systemd/system}"
APP="$BASE/app"
VENV="$BASE/venv"
DATA="$BASE/data"
ENVFILE="$BASE/env"
SVC_USER="${TRADINGBOT_SVC_USER:-tradingbot}"
PY=python3

PHASE="başlangıç"

on_error() {
  local rc=$?
  [[ -n "${PF_TMP:-}" && -f "${PF_TMP:-}" ]] && rm -f "$PF_TMP"   # yarım preflight geçici dosyası bırakma
  echo "" >&2
  echo "[KURULUM BAŞARISIZ] aşama: ${PHASE} (exit ${rc}) — sistem KISMEN kurulu olabilir; betik idempotenttir, sorunu giderip aynı komutla yeniden çalıştırın. Secret/env içeriği bu çıktıda YOKTUR." >&2
  exit "$rc"
}
trap on_error ERR

phase() { PHASE="$1"; echo "== $1"; }

# Servis kullanıcısı olarak, HER ZAMAN APP dizininden çalıştır (python -m tradingbot çağıranın
# dizinine bağlı kalmasın; /root, /tmp veya başka cwd'den çağrılınca da aynı sonuç).
run_as_svc() {
  ( cd "$APP" && sudo -u "$SVC_USER" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$DATA/state" "$@" )
}

REPO="${1:-}"
BRANCH="${2:-}"          # boş = mevcut checkout'un branch'i korunur; SESSİZCE main SEÇİLMEZ

if [[ "${TRADINGBOT_SETUP_ALLOW_NON_ROOT:-0}" != "1" && $EUID -ne 0 ]]; then
  echo "root olarak çalıştırın (sudo)"; exit 1
fi
if [[ ! -d "$APP/.git" && ( -z "$REPO" || -z "$BRANCH" ) ]]; then
  echo "kullanım: $0 <REPO_URL_or_PATH> <BRANCH>   (yeni clone için repo VE branch zorunlu; sessiz varsayılan yok)"; exit 1
fi

phase "paketler"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates tzdata sqlite3 curl
timedatectl set-timezone Europe/Istanbul || true
timedatectl set-ntp true || true

phase "kullanıcı"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$BASE" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$BASE" "$DATA/state" "$DATA/market" "$DATA/vault" "$DATA/backups" "$DATA/logs" "$BASE/.ssh"
chmod 700 "$BASE/.ssh"

phase "kod"
# Bütün git işlemleri repo sahibi SERVICE USER ile yapılır (root'la karışık sahiplik → "dubious
# ownership" riski yok; global safe.directory eklenmez). Yalnız fetch + checkout + fast-forward;
# reset/rebase/force yok — behind/diverged/conflict durumunda --ff-only dürüstçe hata verir.
git_svc() { sudo -u "$SVC_USER" git "$@"; }
if [[ -d "$APP/.git" ]]; then
  CUR_BRANCH="$(git_svc -C "$APP" symbolic-ref --quiet --short HEAD || true)"
  if [[ -z "$CUR_BRANCH" && -z "$BRANCH" ]]; then
    echo "HATA: mevcut checkout detached HEAD ve branch argümanı verilmedi — branch'i açıkça belirtin: $0 <REPO_URL_or_PATH> <BRANCH>" >&2
    exit 1
  fi
  TARGET_BRANCH="${BRANCH:-$CUR_BRANCH}"      # argümansız → MEVCUT branch korunur (sessizce main'e geçilmez)
  git_svc -C "$APP" fetch --all --prune
  git_svc -C "$APP" checkout -q "$TARGET_BRANCH"
  git_svc -C "$APP" pull --ff-only
else
  mkdir -p "$APP"
  chown "$SVC_USER:$SVC_USER" "$APP"
  git_svc clone --branch "$BRANCH" "$REPO" "$APP"
fi

phase "venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel >/dev/null
"$VENV/bin/pip" install -r "$APP/requirements.txt"
"$VENV/bin/pip" install "matplotlib>=3.8" "websocket-client>=1.6" "fastapi>=0.110" "uvicorn>=0.27" "plotly>=5.18" "pyarrow>=15" "requests>=2.31" "httpx>=0.27"

phase "ortam dosyası"
if [[ ! -f "$ENVFILE" ]]; then
  cp "$APP/deploy/env.example" "$ENVFILE"
  echo "  $ENVFILE oluşturuldu — ANTHROPIC_API_KEY vb. değerleri elle doldurun (bu betik sormaz/saklamaz/yazdırmaz)."
fi
chmod 600 "$ENVFILE"

phase "rollback paketi (mevcut unit/preflight yedeği)"
ROLLBACK_DIR="$BASE/rollback/units-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ROLLBACK_DIR"
for u in tradingbot-worker.service tradingbot-dashboard.service tradingbot-backup.service tradingbot-backup.timer; do
  if [[ -f "$SYSTEMD_DIR/$u" ]]; then cp -p "$SYSTEMD_DIR/$u" "$ROLLBACK_DIR/"; fi
done
if [[ -f "$BASE/preflight.sh" ]]; then cp -p "$BASE/preflight.sh" "$ROLLBACK_DIR/preflight.sh"; fi
echo "  rollback paketi: $ROLLBACK_DIR"

phase "preflight kurulumu"
# GERÇEKTEN atomik: aynı dosya sistemi ($BASE) üzerinde mktemp → içerik+mode doğrulama → mv (rename).
# Eski çalışan preflight hiçbir anda yarım dosyayla değiştirilmez; hata durumunda geçici dosya
# on_error tarafından temizlenir. Sahiplik "sahiplik" fazında $SVC_USER'a geçer.
PF_TMP="$(mktemp "$BASE/.preflight.sh.XXXXXX")"
cp "$APP/deploy/preflight.sh" "$PF_TMP"
chmod 0755 "$PF_TMP"
if [[ ! -s "$PF_TMP" || ! -x "$PF_TMP" ]]; then
  echo "HATA: preflight geçici kopyası doğrulanamadı (boş ya da çalıştırılamaz)" >&2
  exit 1
fi
mv -f "$PF_TMP" "$BASE/preflight.sh"
PF_TMP=""

phase "sahiplik"
chown -R "$SVC_USER:$SVC_USER" "$BASE"
chmod 750 "$DATA"

phase "systemd unit kurulumu"
for u in tradingbot-worker.service tradingbot-dashboard.service tradingbot-backup.service tradingbot-backup.timer; do
  install -m 0644 "$APP/deploy/$u" "$SYSTEMD_DIR/$u"
done
systemctl daemon-reload
systemctl enable tradingbot-worker.service tradingbot-dashboard.service >/dev/null

phase "güvenlik duvarı (yalnız SSH; dashboard 127.0.0.1'de kalır, erişim SSH tüneliyle)"
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null
  ufw --force enable >/dev/null
  ufw status | head -5
else
  echo "  (ufw yok — sağlayıcı güvenlik grubunda yalnız 22/tcp açık bırakın)"
fi

phase "tek yetkili worker (split-brain koruması)"
run_as_svc "$VENV/bin/python" -m tradingbot authority --claim --note "vps kurulum"

phase "doktor (bilgilendirici)"
DOCTOR_WARN=0
run_as_svc "$VENV/bin/python" -m tradingbot doctor --quick || DOCTOR_WARN=1
if [[ "$DOCTOR_WARN" == "1" ]]; then
  echo "  UYARI: doctor sorun bildirdi — servisler yine kurulur; başlangıçta preflight fail-closed karar verir."
fi

phase "servis başlatma"
systemctl restart tradingbot-worker.service tradingbot-dashboard.service

phase "backup timer etkinleştirme ve doğrulama"
systemctl enable --now tradingbot-backup.timer >/dev/null
TIMER_ENABLED="$(systemctl is-enabled tradingbot-backup.timer)"
TIMER_ACTIVE="$(systemctl is-active tradingbot-backup.timer)"
if [[ "$TIMER_ENABLED" != "enabled" || "$TIMER_ACTIVE" != "active" ]]; then
  echo "  HATA: tradingbot-backup.timer enabled='$TIMER_ENABLED' active='$TIMER_ACTIVE' — kurulum başarılı SAYILMAZ." >&2
  exit 1
fi
echo "  tradingbot-backup.timer: enabled + active ✓"

PHASE="bitti"
cat <<MSG

Kuruldu (bütün aşamalar tamamlandı)$( [[ "$DOCTOR_WARN" == "1" ]] && echo " — doctor uyarısı var, journalctl ile inceleyin" ).
  durum   : systemctl status tradingbot-worker tradingbot-dashboard
  log     : journalctl -u tradingbot-worker -f
  panel   : ssh -L 8080:127.0.0.1:8080 <sunucu>  ->  http://127.0.0.1:8080  (dışarı açmak için reverse proxy + TLS + DASHBOARD_TOKEN)
  yedek   : systemctl list-timers tradingbot-backup.timer ; bash "$APP/deploy/backup.sh"
  güncelle: bash "$APP/deploy/update.sh" ; geri al: bash "$APP/deploy/rollback.sh" <commit>
Not: $DATA hiçbir zaman silinmez; state.pre-restore-* dizinleri geri yüklemelerde korunur.
MSG
