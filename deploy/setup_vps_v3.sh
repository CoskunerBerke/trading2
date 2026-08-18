#!/usr/bin/env bash
# Trading Bot v3 — Ubuntu 24.04 VPS kurulumu (idempotent; VERİYİ ASLA SİLMEZ).
#   sudo bash deploy/setup_vps_v3.sh <REPO_URL_or_PATH> [BRANCH]
# Yerleşim:
#   /opt/tradingbot/app      kod (git checkout)
#   /opt/tradingbot/venv     python sanal ortamı
#   /opt/tradingbot/data     KALICI VERİ: state/ market/ vault/ backups/ logs/  (asla rm -rf yapılmaz)
#   /opt/tradingbot/env      ortam dosyası (0600, sırlar burada; repo'ya girmez)
# Özel repo için SSH deploy key önerilir (salt-okunur):
#   sudo -u tradingbot ssh-keygen -t ed25519 -f /opt/tradingbot/.ssh/id_ed25519 -N ""
#   → public key'i GitHub → repo Settings → Deploy keys (read-only) olarak ekleyin.
# Bu betik hiçbir kimlik bilgisi saklamaz/istemez; ANTHROPIC_API_KEY vb. değerleri /opt/tradingbot/env dosyasına siz yazarsınız.
set -euo pipefail

REPO="${1:-}"
BRANCH="${2:-main}"
BASE=/opt/tradingbot
APP="$BASE/app"
VENV="$BASE/venv"
DATA="$BASE/data"
ENVFILE="$BASE/env"
SVC_USER=tradingbot
PY=python3

if [[ $EUID -ne 0 ]]; then echo "root olarak çalıştırın (sudo)"; exit 1; fi
if [[ -z "$REPO" && ! -d "$APP/.git" ]]; then echo "kullanım: $0 <REPO_URL_or_PATH> [BRANCH]"; exit 1; fi

echo "== paketler"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends python3 python3-venv python3-pip git ca-certificates tzdata sqlite3 curl
timedatectl set-timezone Europe/Istanbul || true
timedatectl set-ntp true || true

echo "== kullanıcı"
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$BASE" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$BASE" "$DATA/state" "$DATA/market" "$DATA/vault" "$DATA/backups" "$DATA/logs" "$BASE/.ssh"
chmod 700 "$BASE/.ssh"

echo "== kod"
if [[ -d "$APP/.git" ]]; then
  git -C "$APP" fetch --all --prune
  git -C "$APP" checkout -q "$BRANCH"
  git -C "$APP" pull --ff-only
else
  git clone --branch "$BRANCH" "$REPO" "$APP"
fi

echo "== venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  $PY -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel >/dev/null
"$VENV/bin/pip" install -r "$APP/requirements.txt"
"$VENV/bin/pip" install "matplotlib>=3.8" "websocket-client>=1.6" "fastapi>=0.110" "uvicorn>=0.27" "plotly>=5.18" "pyarrow>=15" "requests>=2.31" "httpx>=0.27"

echo "== ortam dosyası"
if [[ ! -f "$ENVFILE" ]]; then
  cp "$APP/deploy/env.example" "$ENVFILE"
  echo "  $ENVFILE oluşturuldu — ANTHROPIC_API_KEY vb. değerleri elle doldurun (bu betik sormaz/saklamaz)."
fi
chmod 600 "$ENVFILE"

echo "== sahiplik"
chown -R "$SVC_USER:$SVC_USER" "$BASE"
chmod 750 "$DATA"

echo "== systemd"
for u in tradingbot-worker.service tradingbot-dashboard.service tradingbot-backup.service tradingbot-backup.timer; do
  install -m 0644 "$APP/deploy/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable tradingbot-worker.service tradingbot-dashboard.service tradingbot-backup.timer >/dev/null

echo "== doktor"
sudo -u "$SVC_USER" env TRADINGBOT_DATA="$DATA" TRADINGBOT_STATE_DIR="$DATA/state" "$VENV/bin/python" -m tradingbot doctor --quick \
  || echo "  (doktor uyarı verdi — env dosyasını doldurup tekrar deneyin)"

echo "== başlat"
systemctl restart tradingbot-worker.service tradingbot-dashboard.service
systemctl start tradingbot-backup.timer

cat <<MSG

Kuruldu.
  durum   : systemctl status tradingbot-worker tradingbot-dashboard
  log     : journalctl -u tradingbot-worker -f
  panel   : ssh -L 8080:127.0.0.1:8080 <sunucu>  ->  http://127.0.0.1:8080  (dışarı açmak için reverse proxy + TLS + DASHBOARD_TOKEN)
  yedek   : systemctl list-timers tradingbot-backup.timer ; bash $APP/deploy/backup.sh
  güncelle: bash $APP/deploy/update.sh ; geri al: bash $APP/deploy/rollback.sh <commit>
Not: $DATA hiçbir zaman silinmez; state.pre-restore-* dizinleri geri yüklemelerde korunur.
MSG
