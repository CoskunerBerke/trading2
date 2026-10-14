# Sahip el kitabı — 7–14 Ekim 2026

Bu belge, Claude'a yazamadığın günlerde ne yapacağını anlatır. GitHub'da açık; Claude olmadan da okunur.
Bütün komutları VPS'te sen çalıştırırsın (`ssh ubuntu@vps-5426050a.vps.ovh.net`). Bot yalnız PAPER çalışır.

## 0. Önce bil

- **Bot kendi başına işlem açar ve kapatır.** Senin bir şey yapman gerekmez. Botun kendi öğrenme modu zaten
  açık (`learning_mode`).
- **Hızlı formasyon hesabı (db5db96) KURULU** (7 Ekim 22:31). Turlar ~8 dk; eski kapatma satırı kurulumla kalktı.
- **Öğrenme motoru** her gece 04:37'de (TR) kendi çalışır; veri birimi 03:41'de piyasa verisini günceller. Yapay zekâ ya
  da token kullanmaz. Her kapanan işlemi kaydeder ve her gün %1 hedefini ölçer. Botun kararlarına dokunmaz.
- Çıktıları **dosyaya kaydet**. Claude'a yeniden yazabildiğinde onları yapıştırırsın.

## 1. Her sabah — sağlık kontrolü (1 dakika, isteğe bağlı ama önerilir)

```
sudo bash ~/tb-deploy-db5db96.sh --check > ~/check-bot-$(date +%F).txt 2>&1; sed -n '/== servisler/,/== ÖĞRENME MODU/p' ~/check-bot-$(date +%F).txt; grep -E "\[(tamam|TETİKLENDİ|ölçülemedi)\]" ~/check-bot-$(date +%F).txt
```

`~/tb-deploy-db5db96.sh` sha256: `d6a843e58d12bd8717b3d8866e204a40e6834c989b922631cdcd1fa6e298da11`

**Neye bakılır:**

| Satır | İyi | Ne yaparsın |
|---|---|---|
| `tradingbot-worker.service: active` | active, yeniden başlama 0 | active değilse → bölüm 4 |
| `OOM kill:` | 0 | 0 değilse → çıktıyı sakla, bölüm 4 |
| `[tamam] bellek` | tamam | `[TETİKLENDİ] bellek` → bölüm 4, "bellek" |
| `[TETİKLENDİ] tur süresi` | — | çıktıyı sakla, bir şey yapma |
| `[tamam] Box kaçan bar` | tamam | tetiklenirse çıktıyı sakla, bir şey yapma |

## 2. Kurulumlar

**Öğrenme motoru (P1a): KURULDU — 7 Ekim 2026 21:20 (25/25, deneme SUCCESS).** Betik `~/tb-engine-4962209.sh`
(sha256 `6a33bb9b9a0fbf49fb80ffb08162b13b92c4f6711226a2ef0a54ce0b56890da6`). Her gece 04:37'de kendi çalışır.

**Öğrenme motoru veri katmanı (P1b): 79b27cf KURULDU — 9 Ekim sabahı (33/33, yükseltme yolu; 57cfef1 8 Ekim 23:45'te
kurulmuştu).** Betik `~/tb-engine-79b27cf.sh` (sha256 `5f1d18886588c91712b92c482d0f07ba50f36ef8d5988df6bf6c7815b591f2e2`).
İlk doldurma 9 Ekim sabahı başladı; saatler sürer. İlerleme ve bitiş (salt-okunur; hiçbir şey başlatmaz):
`sudo bash ~/tb-engine-79b27cf.sh --check 2>&1 | grep -i 'V1 ilk doldurma'` — sürerken "SÜRÜYOR …", bitince
**"TAMAMLANDI …"** (`systemctl is-active tb-engine-backfill.service` → `inactive` yalnız sürecin durduğunu söyler). Bittikten sonra
`--backfill` ÇALIŞTIRMA: yeni bir doldurma turu başlatır ve P2 kurulumu o tur bitene dek reddedilir. Yalnız `--check`
"bitmedi; kaldığı yerden: … --backfill" yazarsa `--backfill` ile sürdür.

**Öğrenme motoru P2 (işlem günlüğü + "neden kaybetti / nasıl kâra dönerdi" atıfı): KURULU (engine-app 44d5353; ilk
doldurma bitti).** Aşağıdaki adımlar kayıt içindir. Betik
`~/tb-engine-44d5353.sh` (sha256 `a4f04e6c26cd9f0709bed5902113b23896e8109ddb161bcbeecd88b6a28cddd9`). **Ne zaman:** ilk doldurma bittikten sonra
(yukarıdaki `--check` satırında "TAMAMLANDI"; sürerken betik kendisi reddeder). Saat (TR): 07:40–09:45, 11:40–13:45,
15:40–17:45 ya da 19:40–21:45 (betik bir sonraki 4 saatlik kapanışa en az 75 dk ister). **Nasıl** (çıktı dosyaya; bağlantı
koparsa betik o anki adımı bitirip kendini 79b27cf'ye geri alır, kayıt `/opt/tradingbot/deploy-logs/engine-44d5353-*.log`'da
sürer):
1. `sudo bash ~/tb-engine-44d5353.sh --dry-run > ~/engine-dryrun.txt 2>&1; tail -15 ~/engine-dryrun.txt` — sonda
   **"KURU ÇALIŞMA: 22/22 değişmez geçti"** (≈ 15–25 dk).
2. `sudo bash ~/tb-engine-44d5353.sh > ~/engine-deploy.txt 2>&1; tail -15 ~/engine-deploy.txt` — sonda
   **"DAĞITILDI: 33/33 değişmez geçti"** (≈ 45–70 dk). Geçmezse betik kendisi 79b27cf'ye döner ("--rollback GEREKMEZ"
   yazar); çıktıyı sakla ve bölüm 6'daki gibi ilet.
3. Ertesi sabahtan itibaren bölüm 3'teki kontrolü bu betikle yap (`tb-engine-79b27cf.sh` yerine `tb-engine-44d5353.sh`).
   İlk gecelerde `S1B_BACKLOG` / `S2_BACKLOG` görmek normaldir (geçmiş işlemlerin günlüğü birkaç geceye yayılır).

**12 Ekim 2026 — Öğrenme motoru P2.1 (ölçüm düzeltmesi): HAZIR, henüz KURULMADI.** Ön koşul: P2 (44d5353) kurulu ve ilk
doldurma bitmiş. Betik `~/tb-engine-b94bedf.sh` (sha256 `746970b0211bccf95a0a03e031fc9b9aca9edb3598fef6889f8927d1c674af48`).
**Ne değişir:** yalnız motorun ölçümü; botun kararları, worker ve birim dosyaları DEĞİŞMEZ (yeniden yükleme yok).
- Box'un gün sonu kapanışları ve gözlemden dolan stop'lar için "kayıt yeniden oynatılınca aynı sonucu veriyor mu"
  ölçümü (fidelity) düzeltildi (`fidelity_v3`). İlk P2 gecesinde %82,7 çıkmıştı; beklenen **≈ %93–95** (ölçüt ≥ %90).
- Arşivin hiç yayımlamadığı 4 seri (`牛来USDT` / `龙虾USDT` mark/premium) artık "üst kaynakta yok" (`ABSENT_UPSTREAM`)
  diye ayrı listelenir; bayat sayılmaz.
- İlk tam gecede işlem günlüğü satırları **bir kez** yeniden kurulur (`S1B_BACKLOG` görmek normal; birkaç geceye
  yayılabilir). Yeni 14 gecelik A/B dönemi başlar (yarısı `AB_OFF`: normal).

**Nasıl** (saat TR: 07:40–09:45, 11:40–13:45, 15:40–17:45 ya da 19:40–21:45):
1. `sudo bash ~/tb-engine-b94bedf.sh --dry-run > ~/engine-dryrun.txt 2>&1; tail -15 ~/engine-dryrun.txt` — sonda
   **"KURU ÇALIŞMA: 20/20 değişmez geçti"** (≈ 15–25 dk).
2. `sudo bash ~/tb-engine-b94bedf.sh > ~/engine-deploy.txt 2>&1; tail -15 ~/engine-deploy.txt` — sonda
   **"DAĞITILDI: 28/28 değişmez geçti"** (≈ 40–70 dk). Geçmezse betik kendisi 44d5353'e döner ("--rollback GEREKMEZ").
3. Ertesi sabahtan itibaren bölüm 3'teki kontrolü bu betikle yap (`tb-engine-44d5353.sh` yerine `tb-engine-b94bedf.sh`).
   Geri alma (gerekirse): `sudo bash ~/tb-engine-b94bedf.sh --rollback` (44d5353'e döner).

**14 Ekim 2026 — Öğrenme motoru P3g (keşif + walk-forward + dersler; ANTİGRAVİTY varyantları dahil): HAZIR, henüz
KURULMADI.** Ön koşul: **önce P2.1 (`tb-engine-b94bedf.sh`, yukarıda) kurulmuş olmalı** ve kurulumundan en az 1 gün geçmiş,
ilk `--check`'leri temiz olmalı. Betik `~/tb-engine-7a03cd9.sh` (sha256 `6e69581e1c9dc2c70ea34e211bc9b7901f8f9dc1bc79be0a9d82866eba2bb700`;
`a56c8e2` adlı eski betiğin yerine — elinde varsa onu ÇALIŞTIRMA, incelemede durduruldu).
**Ne değişir:** yalnız motor; botun kararları, worker ve birim dosyaları DEĞİŞMEZ (yeniden yükleme yok). Gece motoru artık
üç adım daha yapar: (1) kütüphane varyantlarının mühürden sonraki ileri sonuçlarını izler (keşif), (2) geçmiş veride
walk-forward sınamalarını her gece biraz ilerletir (kuyruk; ilk ay birkaç gece sürer), (3) deneme defterini ve dersleri
denetler. ANTİGRAVİTY dökümlerinden çıkarılan 38 mekanik varyant (41 sembol, XAUUSDT dahil) da aynı kurallarla sınanır;
deneme sayısı defterde sonuçlardan ÖNCE yazılır. Hiçbir varyant bu sürümde terfi ettirilmez (Kapı A bakışı sonraki sürüm).
- İlk tam gecede işlem günlüğü bir kez yeniden kurulur; o gece walk-forward adımı atlanır (`S1B_REBUILD`, normal).
- Yeni 14 gecelik A/B dönemi başlar (yarısı `AB_OFF`: normal).

**Nasıl** (saat TR: 07:40–09:45, 11:40–13:45, 15:40–17:45 ya da 19:40–21:45):
1. `sudo bash ~/tb-engine-7a03cd9.sh --dry-run > ~/engine-dryrun.txt 2>&1; tail -15 ~/engine-dryrun.txt` — sonda
   **"KURU ÇALIŞMA: 20/20 değişmez geçti"** (≈ 20–30 dk).
2. `sudo bash ~/tb-engine-7a03cd9.sh > ~/engine-deploy.txt 2>&1; tail -15 ~/engine-deploy.txt` — sonda
   **"DAĞITILDI: 28/28 değişmez geçti"** (≈ 45–75 dk). Geçmezse betik kendisi b94bedf'ye döner ("--rollback GEREKMEZ").
3. Ertesi sabahtan itibaren bölüm 3'teki kontrolü bu betikle yap (`tb-engine-b94bedf.sh` yerine `tb-engine-7a03cd9.sh`).
   Yeni bölüm "kabul ölçütleri — P3g": `WF_BACKLOG` sayısı her gece büyür (normal); `[DİKKAT]` görürsen çıktıyı ilet.
   Geri alma (gerekirse): `sudo bash ~/tb-engine-7a03cd9.sh --rollback` (b94bedf'ye döner; araştırma verisi kalır).

**Hızlı formasyon hesabı (worker sürümü db5db96): KURULDU — 7 Ekim 22:31.** Aşağıdaki adımlar kayıt içindir. Uzun turları ve Box'un mum kaçırmasını düzeltir.
Betik zaten `~/tb-deploy-db5db96.sh` (sha256 `d6a843e58d12bd8717b3d8866e204a40e6834c989b922631cdcd1fa6e298da11`); 7 Ekim
akşamı deneme çalıştırması 56/56 geçti.

1. Önce motorun ilk gecesine bak (bölüm 3). Sonuç `SUCCESS` ya da `AB_OFF` olmalı.
2. Saat (TR): **07:40–09:30** (ya da 11:40–13:30). 03:00–07:39 arası başlatma.
3. Kurulum (bağlantıdan bağımsız; ekran donsa ya da bağlantı kopsa da sürer):
   ```
   sudo bash ~/tb-deploy-db5db96.sh --detach
   ```
   Sonunda **"DAĞITIM TAMAM: 8db1faf → db5db96"** görünmeli. Hata olursa betik 8db1faf'a ve kapatma satırlı config'e
   kendisi döner. Bağlantı koparsa: `sudo journalctl -u tb-deploy-db5db96 -o cat | tail -25`
4. ~1 saat sonra ve bir sonraki 4 saatlik kapanıştan (11:00/15:00) sonra:
   ```
   sudo bash ~/tb-deploy-db5db96.sh --check > ~/check-knn-$(date +%F-%H%M).txt 2>&1; grep -E "\[(tamam|TETİKLENDİ|ölçülemedi)\]|HIZLI|ön ısıt" ~/check-knn-*.txt | tail -20
   ```
   Beklenen: "HIZLI yol", ön ısıtma birkaç saniye, çakışma turu ~5–6 dk `[tamam]`, Box kaçan bar 0.
5. Kapatma (yalnız sorun olursa, **bir kez**): betiğin `--check` çıktısındaki `evidence_fast_knn: false` satırı.

Bağlanırken kopmaması için PowerShell'de: `ssh -o ServerAliveInterval=30 ubuntu@vps-5426050a.vps.ovh.net`

## 3. 8 Ekim'den itibaren her sabah — motor kontrolü

```
sudo bash ~/tb-engine-4962209.sh --check > ~/check-engine-$(date +%F).txt 2>&1; echo "çıkış kodu: $?"; tail -60 ~/check-engine-$(date +%F).txt
```

9 Ekim'den itibaren aynı kontrolü veri bölümüyle birlikte bu betik verir (yukarıdakinin yerine):
`sudo bash ~/tb-engine-79b27cf.sh --check > ~/check-engine-$(date +%F).txt 2>&1; echo "çıkış kodu: $?"; tail -80 ~/check-engine-$(date +%F).txt`

- İlk 14 gecenin yarısında motor bilerek **kapalı** çalışır (tek/çift gün sırası). Bu gecelerde sonuç `AB_OFF`
  görünür: **normal**. Böylece motorun botu yavaşlatıp yavaşlatmadığı ölçülür.
- "GÜNLÜK HEDEF" bölümü her defterin günlük sonucunu ve %1 hedefine göre durumunu gösterir.
- Çıktıyı sakla; Claude'a yazabildiğinde yapıştır. 14. geceden sonra bir kez:
  `sudo bash ~/tb-engine-4962209.sh --ab-report > ~/engine-ab.txt 2>&1`

## 4. Acil durumlar

**Worker çalışmıyor** (`inactive` / `failed`):
```
sudo systemctl status tradingbot-worker --no-pager | head -20 > ~/worker-down-$(date +%F-%H%M).txt; sudo journalctl -u tradingbot-worker -n 80 --no-pager >> ~/worker-down-$(date +%F-%H%M).txt; sudo systemctl restart tradingbot-worker; sleep 90; systemctl is-active tradingbot-worker
```
`active` yazarsa tamam. Yine düşerse bir daha deneme; dosyayı sakla ve Claude'u bekle.

**Bellek** (`[TETİKLENDİ] bellek` ya da OOM kill > 0): worker'ı bir kez yeniden başlat (belleği boşaltır):
`sudo systemctl restart tradingbot-worker`

**Motor** (P1a sorun çıkarırsa; botu etkilemez, yalnız motoru durdurur):
`sudo systemctl stop tradingbot-engine-night.timer`

**Disk** (`df -h /opt` çıktısında boş alan 10 GB'ın altına inerse): hiçbir şeyi elle silme; çıktıyı sakla, bekle.

## 5. YAPMA

- `do-release-upgrade`, `apt upgrade`, VPS'i yeniden başlatma ("System restart required" yazısı beklesin).
- `config.yaml`'ı elle düzenleme, kapatma satırını silme ya da ikinci kez ekleme.
- Eski dağıtım betiklerini dağıtım kipinde çalıştırma (`--check` dışında).
- Öğrenme motorunun sonraki aşamaları (P2, P3) **Claude ile birlikte** kurulur.
- P1b kuruluyken `tb-engine-4962209.sh`'i dağıtım kipinde çalıştırma (yalnız `--check`).

## 6. Claude'a geri döndüğünde

Şunları yapıştır: `~/engine-dryrun.txt` ve `~/engine-deploy.txt` (son 40 satır), en son `~/check-engine-*.txt`
ve `~/check-bot-*.txt` (yukarıdaki süzgeçle), varsa `~/worker-down-*.txt`.
