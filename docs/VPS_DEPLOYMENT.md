# VPS DEPLOYMENT (PAPER)

Hedef: Ubuntu 24.04, 2 vCPU / 4 GB / 40 GB, Avrupa (Hetzner Nuremberg/Falkenstein/Helsinki CX22 sınıfı; ücretsiz alternatif Oracle Always Free — ARM/kapasite kontrol edin). Kaynak tahmini: worker 600–900 MB RSS, ~0.3 vCPU ortalama; dashboard ~120 MB.

## Docker Compose
```
git clone https://github.com/CoskunerBerke/trading2.git && cd trading2
cp deploy/env.example deploy/.env      # sırları buraya (anahtar YOK; PAPER için boş kalabilir)
docker build -f Dockerfile.v3 -t tradingbot:v3 .
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
docker compose -f deploy/docker-compose.yml logs -f worker
docker compose -f deploy/docker-compose.yml exec worker python -m tradingbot doctor
```
Servisler: `worker` (watch), `dashboard` (127.0.0.1:8080), `backup` (saatlik). Kalıcı volume `/data/{state,market,vault,backups,logs}`. Durdurma `stop_grace_period 90s` (SIGTERM → mevcut tur biter, state yazılır).

## systemd (native)
```
sudo bash deploy/setup_vps_v3.sh          # kullanıcı + venv + dizinler; mevcut veriyi SİLMEZ (idempotent)
sudo systemctl enable --now tradingbot-worker tradingbot-dashboard tradingbot-backup.timer
journalctl -u tradingbot-worker -f
```
Güncelleme/rollback: `deploy/update.sh`, `deploy/rollback.sh`. Erişim: `ssh -L 8080:127.0.0.1:8080 user@vps` → http://127.0.0.1:8080. Bu sürüm gerçek emir göndermez; VPS'te de PAPER.
