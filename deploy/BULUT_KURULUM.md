# PC kapalıyken 7/24 çalıştırma rehberi

Botun 7/24 çalışması için bir bilgisayarın sürekli açık olması gerekir. Üç seçenek var; **Seçenek B (ucuz Linux VPS)** öneriyorum.

> Önemli: Binance, ABD IP'lerinden erişimi engeller. Sunucuyu **Avrupa** (Almanya/Finlandiya/Hollanda) bölgesinde aç.

---

## Seçenek A — Kendi PC'n (ücretsiz, ama PC açık kalmalı)

1. `Ayarlar → Sistem → Güç → Ekran ve uyku`: "Uyku: Hiçbir zaman". Laptop ise `Kapak kapatıldığında: Hiçbir şey yapma`.
2. `scripts\watch_7_24.bat`'a çift tıkla, pencereyi küçült.
3. (İsteğe bağlı) PC her açıldığında otomatik başlasın: `Win+R` → `shell:startup` → açılan klasöre `watch_7_24.bat`'ın kısayolunu koy.

Obsidian zaten aynı PC'de olduğu için ek senkron gerekmez.

---

## Seçenek B — Ucuz Linux VPS (≈ 4–6 $/ay) — ÖNERİLEN

Örnek sağlayıcılar: Hetzner (CX22, Nürnberg/Helsinki), DigitalOcean (Frankfurt/Amsterdam), Contabo. 2 vCPU / 2-4 GB RAM yeterli. **Ubuntu 22.04/24.04** seç.

### 1) Kodunu bir GitHub deposuna koy (private)
Bilgisayarında (PowerShell):
```bash
cd "C:\Users\berke\Trading bot"
git remote add origin https://github.com/KULLANICI_ADIN/trading-bot.git
git push -u origin main
```
(GitHub'da önce boş private repo oluştur. Kimlik doğrulama için GitHub'ın "Personal Access Token"ını sen girersin.)

### 2) Obsidian kasası için ayrı bir private repo (senkron için)
```bash
cd "C:\Users\berke\Trading bot\Trading_bot"
git init && git add -A && git commit -m "vault"
git remote add origin https://github.com/KULLANICI_ADIN/trading-vault.git
git push -u origin main
```

### 3) VPS'te tek komut kurulum
Sunucuya SSH ile bağlan (sağlayıcının konsolu ya da `ssh root@SUNUCU_IP`), sonra:
```bash
apt-get update -y && apt-get install -y git && git clone https://github.com/KULLANICI_ADIN/trading-bot.git /tmp/tb && bash /tmp/tb/deploy/setup_vps.sh https://github.com/KULLANICI_ADIN/trading-bot.git https://github.com/KULLANICI_ADIN/trading-vault.git
```
Bu script: Python + bağımlılıkları kurar, botu `/opt/tradingbot`'a, kasayı `/opt/tradingbot-vault`'a klonlar, **systemd servisi** kurar (çökerse/sunucu yeniden başlarsa otomatik kalkar).

Kasa senkronunu aç: sunucuda `/opt/tradingbot/config.yaml` içinde `obsidian.git_sync: true` yap (ya da servise `Environment=TRADINGBOT_VAULT_GIT_SYNC=1` ekle) ve `systemctl restart tradingbot`. Bot her turdan sonra kasayı GitHub'a push eder.
Private repo push için sunucuda bir kez `git config --global credential.helper store` + ilk push'ta token gir (ya da SSH anahtarı).

Log izleme: `journalctl -u tradingbot -f` · durdur/başlat: `systemctl stop|start tradingbot`

### 4) Obsidian'da görmek (PC ve telefon)
- **Obsidian Git** topluluk eklentisini kur (Ayarlar → Topluluk eklentileri → "Obsidian Git").
- PC'de: mevcut `Trading_bot` kasası zaten git deposu → eklentide "auto pull" aralığını 5 dk yap. Telefonda: Obsidian → yeni kasa → Obsidian Git ile `trading-vault` deposunu klonla, auto pull aç.
- Böylece PC kapalı olsa da bot bulutta yazar, sen telefondan **Scanner / Agents / Paper Futures / Learning / Charts**'ı görürsün.

Alternatif senkron: **Syncthing** (PC ↔ VPS ↔ telefon klasör senkronu) veya **Obsidian Sync** (ücretli; VPS'e Obsidian kurmak gerekmez, kasa klasörünü Syncthing ile PC'ye taşıyıp Obsidian Sync PC'den dağıtır).

---

## Seçenek C — Docker / Railway / Fly.io

Depoda `Dockerfile` var. Herhangi bir konteyner servisinde:
- Build & run: `docker build -t tradingbot . && docker run -d --restart=always -v tb_data:/data --name tradingbot tradingbot`
- Railway/Fly.io: repo'yu bağla, **kalıcı disk (volume)** ekle ve `/data`'ya bağla (state + kasa orada), bölgeyi **Avrupa** seç, ortam değişkeni `TRADINGBOT_VAULT_GIT_SYNC=1` ve kasa deposu için git kimlik bilgisi (token) ver.
Railway'de daha önce projen olduğu için tanıdık gelecektir; ama VPS daha ucuz ve daha kontrol edilebilir.

---

## Sık sorulanlar
- **Gerçek para/API?** Bot gerçek emir göndermez, API anahtarı istemez. Bulutta da aynı: yalnızca okur + kağıt işlem yapar.
- **Kaç kaynak?** Tur başına ~3 dk CPU, birkaç yüz MB RAM. En küçük VPS yeter.
- **Zaman dilimi:** Servis `TZ=Europe/Istanbul` ile çalışır; notlardaki saatler Türkiye saatidir.
- **Güncelleme:** PC'de değişiklik yapıp `git push`; sunucuda `cd /opt/tradingbot && git pull && systemctl restart tradingbot`.

## Başlangıç preflight'i ve kurulum doğrulaması (v3)
- Worker `ExecStartPre=/opt/tradingbot/preflight.sh` (kaynak-kontrollü, `deploy/preflight.sh`'ten kurulur): doctor tamamen OK ya da **yalnız bayat heartbeat** (worker kapalıyken beklenen) ise başlar; diğer her doctor hatası, crash veya geçersiz çıktı fail-closed engeller. Normal `doctor` komutu gevşetilmedi.
- `setup_vps_v3.sh` fazlıdır: bir aşama başarısızsa aşama adıyla non-zero çıkar ve "Kuruldu" mesajı basılmaz; `tradingbot-backup.timer` enable --now sonrası **enabled + active** doğrulanmadan kurulum başarılı sayılmaz. Betik idempotenttir.
- Dashboard yalnız `data/logs` + `data/state`'e yazabilir (instance kaydı/kooperatif stop için); worker matplotlib cache'i systemd `CacheDirectory` altındadır (`/var/cache/tradingbot/matplotlib`).
- **Branch güvenliği:** `setup_vps_v3.sh` argümansız çalıştırılırsa mevcut checkout'un branch'i KORUNUR (sessizce `main` seçilmez); checkout detached HEAD ise açık hata verir. Yeni clone için repo VE branch zorunludur. Üretimde branch'i her zaman açık yazın: `sudo bash deploy/setup_vps_v3.sh <REPO> feature/trading-v3-paper-testnet`. Bütün git işlemleri repo sahibi `tradingbot` kullanıcısıyla yapılır (dubious-ownership yok); yalnız fast-forward, reset/rebase/force yok.
- **Bellek kapasitesi (ölçüm, garanti değil):** Tam Tier A futures pattern index (≈578k olay / 89 seri) hedef VPS'te worker'da **≈3.1 GB** kullandı; önceki 1.5G cgroup sınırı yükleme sırasında OOM-kill üretiyordu. Worker unit'i **`MemoryMax=4G`** ile doğrulandı (peak ≈3.11 GB, NRestarts=0, `/health/ready=true`). Bu bir ölçümdür; sabit tüketim taahhüdü değildir — evren/timeframe büyüdükçe artar. **8 GB sınıfı VPS önerilir** (7.6 GiB RAM + swap 0 ile doğrulandı).
- **Rollback paketi:** her setup, mevcut unit dosyalarını ve `/opt/tradingbot/preflight.sh`'i `/opt/tradingbot/rollback/units-<UTC>/` altına kopyalar. Geri dönüş (state'e dokunmadan): bu dizindeki unit'leri `/etc/systemd/system/`e ve `preflight.sh`'i `/opt/tradingbot/preflight.sh`'e kopyala → `systemctl daemon-reload` → servisleri restart et; kod için `git -C /opt/tradingbot/app checkout <önceki-branch-commit'i>` yerine branch'i eski SHA'ya sabitlemek gerekirse `deploy/rollback.sh <SHA>` kullanılır.
