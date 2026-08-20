# PHASE 8 — Bütün-Evren History Kapasitesi ve VPS Planı (2026-08-20)

Kaynaklar: `history-plan --universe` (95 uygun sembollü gerçek universe.json ile), pilotta ölçülen
gerçek disk yoğunlukları (455k satır / 19.6 MiB) ve `docs/HISTORICAL_LEARNING.md`. Satın alma YAPILMADI;
bu doküman kullanıcı onayı için tek öneri üretir.

## 1. Evren ve tier planı (ölçülmüş, tahmin değil uydurma)

`universe` komutu (public exchangeInfo + 24h ticker): spot 3.681 → **32 uygun**, futures 872 → **89 uygun**,
birleşik **95 sembol** (26 both). Eleme nedenleri kayıtlı (TRY/IDR/JPY quote, stable base, likidite, TRADIFI,
SETTLING, YOUNG_LISTING...). `state/universe.json` hacme göre sıralıdır.

| Tier | Kapsam | TF | Seri | Satır | Ham disk | İstek | ETA |
|---|---|---|---|---|---|---|---|
| A | 95 sembol × spot+futures | 1h, 4h, 1d (360g) | 570 | 2.013M | 89.6 MB | 16.3k | ~96 dk |
| B | ilk 50 | 15m (360g) | 100 | 3.112M | 130.7 MB | 3.0k | ~18 dk |
| C | ilk 20 + açık pozisyonlar | 1m (90g), 5m (365g) | 88 | 10.328M | 401.7 MB | 1.9k | ~12 dk |
| **Toplam** | | | **758** | **15.45M** | **622 MB** | **21.2k** | **~2 sa** |

Kullanıcının istediği yapı ile karşılaştırma: koddaki tier'lar ters adlandırılmıştır ama kapsama eşdeğerdir
(A=tüm evren kaba TF; B=ilk 50'ye 15m; C=ilk 20'ye 1m/5m). İstenen "Tier A ilk ~20'ye 1m..1d" birleşimi,
A∪B∪C kesişimiyle ilk 20 sembolde aynen sağlanır. `tier_b_top_n/tier_c_top_n` config'ten büyütülebilir;
disk maliyeti tabloda lineer ölçeklenir (ör. C'yi 80 sembole çıkarmak ≈ 4× ≈ 1.6 GB ham).

Funding + OI: sembol başına ~9 ek istek; OI geçmişi Binance'te ~30 gün (bilinen sınır).

## 2. Türetilmiş depolama/RAM modeli (pilottan ölçülen yoğunluklar)

- Ham kline (csv.gz): **~45 B/satır** (15m 44.4, 1h 47.9, 4h 52.6). pyarrow kurulursa parquet benzer/daha iyi.
- Feature store (78 kolon, csv.gz): **~468 B/satır ≈ ham×10.3**.
  - 15m/1h/4h/1d feature'ları: ~5.18M satır ≈ **2.4 GB**; ileride 1m/5m eklenirse +10.3M ≈ +4.8 GB.
- Replay/pattern RAM: **~3.7 KB/bar** (event ~3 KB + feature 0.62 KB + mum 0.09 KB), tamamen bellek içi
  (bilinen sınır; disk indeks yok). 4h tüm evren ≈ 0.40M olay ≈ **1.5 GB RAM** (rahat); 1h ≈ 1.6M ≈ 5.9 GB
  (stride ≥2 şart); 15m/1m tüm evren bellek içine SIĞMAZ → alt küme/stride veya gelecekte disk indeks.
- Aylık büyüme: ham ~3.9 MB/gün ≈ **0.12 GB/ay**; feature (15m/1h/4h) ~7 MB/gün ≈ **0.22 GB/ay**;
  log (rotasyonlu) ~50 MB/ay; günlük state yedeği ~1 MB × 30. Manifest/replay çıktıları ihmal seviyesinde.
- 1. yıl toplam ayak izi: OS+venv ~6 GB + ham 0.6→2 GB + feature 2.4→5 GB + log/yedek/replay ~2 GB ≈ **12–17 GB**.

## 3. Survivorship bias durumu (dürüst)

- `universe.json` yalnız BUGÜN TRADING olan sembolleri içerir; geçmişte listelenip kaldırılanlar
  (delisted) kapsam DIŞIDIR. Plan çıktısı artık bunu açıkça taşır: `point_in_time: false`,
  `survivorship_bias.present: true` (commit `a64b069`).
- Delisted sembolleri data.binance.vision arşivinden keşfedip point-in-time evren kurmak mümkündür ama bu
  depoda henüz kod yoktur → replay sonuçları "bugün hayatta kalanlar" evreninde okunmalı; kenar iddiaları
  bu bias notuyla raporlanır. (Gelecek iş: arşiv dizin taraması + listing/delist tarihli PIT manifest.)
- Delisted/verisiz sembol sessizce yutulmaz: collector kapsama/checksum hatasında fail-closed `bad_chunks`
  kaydeder; gap uzlaştırıcı veri yoksa GAP_AMBIGUOUS üretir.

## 4. VPS seçimi — tek öneri: **OVH VPS-2 (4 vCore, 8 GB RAM, 75 GB NVMe), Ubuntu 24.04 LTS x86_64**

Kural uygulaması: tahmini toplam kullanım **12–17 GB < 45 GB** → kullanıcı kuralına göre VPS-2 başlangıç
için yeterli; 75 GB diskte %40 boş pay (30 GB) ayrıldıktan sonra bile ~2.5× büyüme alanı var.
- Netcup VPS 500 G12 (4 GB RAM): 4h tüm-evren replay 1.5 GB sığar ama 1h replay + worker + dashboard
  birlikte sıkışır; yalnız veri collector'ı olarak anlamlı, iki rol tek kutuda isteniyorsa yetersiz.
- OVH VPS-3 (12 GB): yalnız 1h/15m'de daha geniş stride'sız replay isteniyorsa gerekli; bugün değil.
- Lokasyon: AB (ör. Gravelines/Strasbourg/Frankfurt). ABD lokasyonu SEÇME (binance.com coğrafi engeli).
- Satın alma SONRASI, deploy ÖNCESİ salt-okunur erişim testi (para/emir yok):
  `curl -s https://api.binance.com/api/v3/time` ve `curl -s https://fapi.binance.com/fapi/v1/time`
  ikisi de JSON zaman dönmeli; dönmüyorsa lokasyon değiştirilir.
- Tahmini maaliyet: VPS-2 sınıfı ~10–14 €/ay (kesin fiyatı sipariş ekranından doğrulayın; buradan satın
  alma yapılmadı ve yapılmayacak — kullanıcı onayı gerekir).

## 5. Deployment hazırlığı (bu depoda hazır)

- `deploy/setup_vps_v3.sh`: idempotent kurulum (non-root `tradingbot` kullanıcısı, venv, systemd,
  `/opt/tradingbot/data` asla silinmez) + **ufw yalnız SSH** + **`authority --claim` (tek yetkili worker)**.
- `tradingbot-worker.service` / `tradingbot-dashboard.service`: auto-restart, ayrı health, MemoryMax,
  dashboard yalnız 127.0.0.1 (erişim SSH tüneli), secrets yalnız `/opt/tradingbot/env` (0600).
- `tradingbot-backup.timer`: günlük yedek; off-instance şifreli kopya için `deploy/backup.sh` üzerine
  kullanıcı hedefi (rclone/scp) eklenecek — sunucu alınınca yapılandırılır.
- Split-brain: `state/worker_authority.json` — VPS kurulumda claim eder; PC'de `watch` fail-closed reddeder.
- Restart güvenliği: offline gap uzlaştırıcı (`ops/gap.py`) + watermark + GAP_AMBIGUOUS giriş kilidi +
  turlardan bağımsız 30 sn heartbeat (commit `02a4b77`, `1d0c5c1`).

## 6. Migrasyon sırası (sunucu hazır olunca; şimdi YAPILMADI)

1) PC: `python -m tradingbot stop --target all` → 2) final backup + semantik snapshot + sha256 →
3) hash doğrulamalı arşiv → 4) state/history/manifest aktar → 5) sunucuda salt-okunur `doctor` +
`history-validate` → 6) F00004/F00005 birebir doğrula → 7) yalnız sunucu worker'ı başlat (kurulum
authority'yi claim eder) → 8) PC'de `authority` markörü sayesinde yerel `watch` başlayamaz →
9) gap-reconcile raporu + duplicate=0 → 10) ilk 24 saat yalnız PAPER/noop soak.

## 6b. Replay araştırma hattı ve ilk Core-4 pilotu (yalnız PLAN — VPS'te çalıştırılmadı)

Komutlar (hepsi PAPER; canlı state/model/pozisyonlara yazmaz):
- `replay-plan` — read-only dry-run: manifestlerden satır/timeline/pattern-olay sayısı, tahmini bellek/CPU,
  host+worker rezervi düşülmüş bütçe ve risk sınıfı (LOW/MEDIUM/HIGH/BLOCKED). Veri okumaz, dizin yaratmaz.
  Yetersiz veri, bozuk parça, manifest hatası, bütçe aşımı ya da RAM ölçülemezliği → non-zero (fail-closed).
- `replay-train --run-id <id>` — YALNIZ `state/replay/<id>/` altındaki `HISTORICAL_REPLAY` hafızasından
  challenger eğitir; `train_manifest.json` (veri aralığı, seed, config, input/params/metrics hash'leri) yazar.
  İdempotent (aynı input hash → yeniden eğitim yok), deterministik (recency referansı = son kayıt zamanı,
  duvar saati değil). Canlı `models.json`/`learn_v2.json`/ledger/trade memory açılmaz; terfi YOK.
- `replay-evaluate --run-id <id>` — OOS raporu: closed/train/holdout, expectancy, PF, maxDD, win rate,
  Brier/ECE/log-loss, %95 alt sınır, veri aralığı, walk-forward pencere sayısı, determinism hash'leri,
  survivorship uyarısı. Yetersiz örnek, bayat/bozuk artifact, bölünme tutarsızlığı, zaman-sırası ihlali ya da
  CHAMPION işaretli model → non-zero. Çıktı en fazla "shadow adayı olabilir" der; kopyalama/terfi yapmaz.

Runner: `deploy/replay_runner.sh plan|train|evaluate|full <RUN_ID> [...]` — service user + APP cwd + açık
`TRADINGBOT_DATA`/`TRADINGBOT_STATE_DIR`, `env -i` (env dosyası yüklenmez, secret okunmaz/yazılmaz),
PAPER + `live_order_path_enabled=false` zorunlu, kapasite planı geçmeden iş başlamaz, iş `systemd-run --scope`
ile ayrı cgroup'ta (varsayılan `MemoryMax=2G`, `CPUQuota=60%`, `Nice=15`, `IOWeight=20`) çalışır; worker ve
dashboard **durdurulmaz**.

**İlk pilot (sunucuda ÇALIŞTIRILMADI; onay sonrası uygulanacak):**
```
BTC/USDT ETH/USDT SOL/USDT BNB/USDT · futures · 4h · stride=4 · seed=7 · 2022-01-01→2026-08-01 · patterns açık
```
1) `sudo bash /opt/tradingbot/app/deploy/replay_runner.sh plan core4_4h_s4_seed7 --symbols BTC/USDT ETH/USDT SOL/USDT BNB/USDT --market futures --tf 4h --from 2022-01-01 --to 2026-08-01 --stride 4 --seed 7`
   → risk sınıfı LOW/MEDIUM değilse DUR (stride artır ya da sembol azalt).
2) Replay koşusu (mevcut komut, ayrı state): `historical-replay --run-id core4_4h_s4_seed7 --symbols ... --stride 4 --seed 7 --from 2022-01-01 --to 2026-08-01`
3) `replay_runner.sh train core4_4h_s4_seed7` → `state/replay/core4_4h_s4_seed7/train_manifest.json`
4) `replay_runner.sh evaluate core4_4h_s4_seed7` → `evaluation.json`
5) Doğrula: worker `NRestarts=0`, `/health/ready=true`, `futures_ledger.json` sha256 pilot öncesiyle aynı,
   `state/models.json` ve `state/learn_v2.json` değişmemiş, BZ/XAUT/ZRO pozisyonları aynı.

## 7. Kullanıcıdan istenen TEK karar

**OVH VPS-2, AB lokasyonu, Ubuntu 24.04 LTS** siparişini onaylayıp açmak (~10–14 €/ay). Sunucu bilgileri
hazır olduğunda bu depo deploy'a hazırdır; IP/parola/anahtar bu oturumda istenmez ve saklanmaz.
