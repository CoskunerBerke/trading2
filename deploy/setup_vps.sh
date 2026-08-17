#!/usr/bin/env bash
# Ubuntu/Debian VPS'te tek komutla kurulum. Kullanım: bash deploy/setup_vps.sh <GITHUB_REPO_URL> [VAULT_GIT_URL]
set -e
REPO="${1:?Bot repo URL gerekli (ör. https://github.com/KULLANICI/trading-bot.git)}"
VAULT_REPO="${2:-}"
apt-get update -y && apt-get install -y python3 python3-pip git
rm -rf /opt/tradingbot && git clone "$REPO" /opt/tradingbot
cd /opt/tradingbot && pip3 install --break-system-packages -r requirements.txt matplotlib websocket-client
mkdir -p /opt/tradingbot-vault
if [ -n "$VAULT_REPO" ]; then
  git clone "$VAULT_REPO" /opt/tradingbot-vault || true
  cd /opt/tradingbot-vault && git config user.name "tradingbot" && git config user.email "bot@local"
fi
cp /opt/tradingbot/deploy/tradingbot.service /etc/systemd/system/tradingbot.service
systemctl daemon-reload && systemctl enable --now tradingbot
echo "Kuruldu. Log: journalctl -u tradingbot -f"
