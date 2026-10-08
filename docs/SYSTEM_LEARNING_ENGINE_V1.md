# Sürekli Öğrenme Motoru v1 (`research_engine`) — tasarım

**Durum:** TASARIM (kod yok), **ikinci hakem turundan sonra revize edildi** (hakem kararı: "REVISE — yazıldığı gibi
onaylama/uygulama"). Bu belge, VPS üzerinde sürekli çalışan, kayıt-yalnız (record-only) bir öğrenme ve araştırma
motorunu tanımlar. Kod tabanı `1c2c6e2` (dev hattı) üzerinden okunarak yazıldı; VPS `f8b05fb` çalıştırıyor (aynı kod,
sonraki araştırma/test commitleri hariç). Hakemin her zorunlu maddesinin nerede karşılandığı ve reddedilen tek alt
iddianın gerekçesi §14'tedir.

**Kısa özet:** Veri VPS'e **bir kez** indirilir ve her gün yalnız eksik kısım eklenir. Her gece, AI kullanmadan ve ağ
erişimi olmadan:
- her defterin (vadeli **ve** ana botun spot defteri) her işlemi ve her cüzdan hareketi tüm maliyetleriyle arşivlenir;
- her işlem için "neden kaybetti / neden kazandı / nasıl kâra dönebilirdi" hesaplanır;
- bilinen tüm yöntemler ve varyasyonları walk-forward ile yeniden test edilir ve **mühür sonrası ileri veride**
  kendiliğinden izlenir (keşif katmanı);
- iyi görünenler önce kendi kâğıt defterlerinde **kayıt-yalnız ileri yeniden oynatma** ile denenir;
- kanıtlananlar sahibe (berke) **yazılı öneri** olarak sunulur.

Günlük +%1 hedefi her enstrüman (altın dahil) ve toplam için **ölçülür**; kanıt olmadan hiçbir yerde "tuttu" denmez.
Mevcut defterlerin hiçbir kararı değişmez. Her şey PAPER'dır.

**İlk aşama küçüldü:** P1 ikiye bölündü. **P1a** ağsızdır ve karar-nötrdür; sahte VPS'te sınanabilir: kapanış + cüzdan
hareketi arşivi (vadeli + spot), gecelik ölçülmüş anlık görüntü, günlük hedef (gerçekleşmiş + defterin kendi mark'ıyla
MTM), `scorecard --daily`, `engine-status` ve yalnız gece birimi. **P1b** veri deposunu, tohumlamayı, ilk doldurmayı ve
ağlı veri birimini getirir (§10).

Temel: hakemlerin seçtiği Tasarım 1 ("reuse-first", iki gece birimi, sabitlenmiş ayrı checkout). Tasarım 2'den
replay-fidelity kapısı, kural bağlamının yeniden kurulması (rehydration), EX_ANTE/HINDSIGHT ayrımı, önceden kayıtlı
bakışlar (looks) ve sıkı hedef hükmü alındı. Tasarım 3'ten çekirdek düzeyinde ağ yalıtımı (`PrivateNetwork=yes`), ertesi
gün arşiv uzlaştırması, evren anlık görüntüleri ve k*/iflas tablosu alındı. Birinci hakem turunun "mutlaka düzelt"
listesi §13'te, ikinci turun zorunlu maddeleri §14'te kapatıldı.

---

## 1. Amaç ve dürüst beklenti

### 1.1 Sahibin istekleri → bu tasarımdaki karşılığı

| Sahibin isteği | Karşılığı |
|---|---|
| "Her seferinde bir şeyleri indirip token yiyoruz" | Veri VPS'te kalıcı (`data/research/store`, `archive_cache`); günlük yalnız ek; gece işi deterministik Python; AI yalnız ≤8 KB özet okur. Kod değişikliklerinin gerçek token maliyeti ayrıca dürüstçe yazılır (§8). |
| Mevcut coinleri ve altını çok iyi öğrenen ve iyi işlem yapan sistem | Strateji kütüphanesi + gece walk-forward + **keşif katmanı** (her varyant, altın dahil, mühür sonrası ileri veride kendiliğinden izlenir) + kayıt-yalnız ileri adaylar + işlem dersleri (§5, §6). |
| Günde en az +%1 (tek coin/altın **veya** toplam) | Her gün, her enstrüman, her defter ve toplam için ölçülür; gün satırı kesinleşmeden hüküm yok; hükümler yalnız kayıtlı bakışlarda ve çoklu test düzeltmesiyle (§7). |
| Her işlemde her şeyi kaydet (gelir, ücret, kayma, fonlama, giriş yeri, büyüklük, kaldıraç, taktik, varyasyon) | İşlem günlüğü `tj_v1`, her alan kaynak etiketli (§4); P1a'dan itibaren ham kapanış ve cüzdan hareketi arşivi. |
| Kaybedince neden ve nasıl kâra dönebilirdi; kazanınca neden | Kural kodları + karşı-olgusal yeniden oynatma + istatistikle desteklenen dersler (§5). |
| VPS'te sürekli çalışan motor; iyi olan önce kayıt-yalnız denensin, kanıtlanan bana onaya gelsin | systemd zamanlayıcıları; keşif katmanı (her şey denenir, hiçbir şey terfi etmez); kayıt-yalnız ileri adaylar; terfi kapısı → yazılı öneri → sahip onayı → normal sürüm (§6.6–§6.8, §9). |

**Sahip hedefi (2026-10-06, sahibin sözleriyle):** "bizim amacımız her gün overall da %1 kar veya daha fazla etmek veya
bir ayın sonunda %30 veya daha fazla kar etmek". Bu, günlük %1 hedefinin (yukarıda) yanına **aylık toplam ≥ %30** ölçüsünü
ekler: her takvim ayı sonunda bütün defterlerin (otomatik PAPER defterleri ayrı satır ve "oto dahil" satırıyla) toplam net
kârı, ayın başındaki toplam özsermayeye oranla. Yalnız ölçüdür; kapı, eşik ya da risk kuralı bu hedef yüzünden gevşetilmez.
Dürüst not: %30/ay ≈ günde ~%0,9–1 bileşik demektir; bugünkü PAPER kanıtında buna yaklaşan defter yoktur (en iyisi M2).
Hedefe ulaşıp ulaşılmadığı her ay sonunda açıkça "ULAŞILDI / ULAŞILMADI" diye yazılır.

### 1.2 Bugünkü kanıt (PAPER, maliyet sonrası)

| Kapsam | Ölçülen | Günlük karşılığı |
|---|---|---|
| M2 (TSMOM28) — tek pozitif defter | +0,12R / 49 işlem (%0,5 risk); elverişli 30 günde +%7,4 | ≈ +%0,25/gün (kısa, elverişli dönem) |
| Ana bot | −0,07R / 163 işlem | negatif |
| D4 Donchian 4h | −0,89R / 13 | negatif |
| C4 mum varyasyonları | −0,09R / 16 | negatif |
| Formasyon | −0,48R / 13 | negatif |
| Box | stop < %0,5: −0,56R / 72; stop %0,5–1: +0,17R / 176 (2026-10-03'ten beri canlı kural: en az stop %0,5) | karışık |
| gold_v1 laboratuvarı | 32 hücrede 0 güçlü aday; hiçbir hücre **ayda** +%1'e ulaşmadı | — |
| gold_v2 laboratuvarı (wt-gold2 `db1828e`) | aile B (PAXG hafta sonu dönüşü): kanıt yok, doğrulamada −0,055R; aile A (Dukascopy 2006–2020) henüz koşulmadı | — |
| crowd lab fut_v2 | 8 hipotezde 0 | — |

### 1.3 Hedefin büyüklüğü

- Günde +%1 bileşik olarak 30 günde ×1,348, yılda ×37,8 eder. Bu, panelde zaten izlenen ve henüz tutmayan **aylık +%1**
  hedefinin yaklaşık 30 katıdır.
- İşlem başına %0,5 riskle günde +%1, defter başına **her gün net +2R** demektir. Bugünkü en iyi defter işlem başına
  +0,12R yapıyor.
- Kaldıraç veya risk artırmak tek tek +%1 günleri mekanik olarak üretebilir, ama iflas riskini de büyütür. Motor bunu
  yalnız lider tablosunda ve önerilerde, "HİPOTETİK" etiketli bir satırda, düşüş ve iflas olasılığıyla birlikte
  gösterir (§7.6). Özet başlığında ve `--check` özetinde göstermez; bunu asla başarı olarak sunmaz.

### 1.4 Gerçekçi beklenti

- **Kesin olanlar:** veri bir kez iner; rutin öğrenme 0 AI token harcar; her işlem tüm alanlarıyla kaydedilir; her
  kazanç ve kayıp kanıtla açıklanır; hiçbir defter sahibin yazılı onayı olmadan değişmez; "bugün +%1 yaptık mı, nerede,
  gerçek mi?" sorusuna her gün dürüst cevap verilir (gün kesinleşene kadar "GEÇİCİ" etiketiyle).
- **Muhtemel olanlar:** kütüphanenin büyük kısmı walk-forward'da elenir (bu, sistemin çalıştığını gösterir). Varsa
  birkaç taktik maliyet sonrası işlem başına +0,05…+0,2R civarı küçük bir avantaj gösterebilir. İlk 3–6 ayda toplam
  günlük getirinin ortalaması büyük olasılıkla −%0,1 ile +%0,1 arasında kalır. Kapı A çok sıkıdır (geçmiş
  laboratuvarlarda 0/32, 0/8, 0/40); bu yüzden "gerçekten dene" isteği Kapı A'yı beklemeden keşif katmanında karşılanır.
- **Beklenmeyen:** kanıtlı, sürdürülebilir +%1/gün. Tek coinde +%1 günleri **olacaktır**; ancak 40+ enstrüman arasında
  seçim ve şans yüzünden bunlar kanıt değildir. Motor bunları "HEDEF GÜNÜ" olarak sayar, başarı olarak saymaz.

---

## 2. Mimari

### 2.1 İlkeler

1. **Worker'a dokunulmaz.** Worker, defterler, `config.yaml` ve `/opt/tradingbot/app` değişmez. Motor ayrı bir
   klondan (`/opt/tradingbot/engine-app`), ayrı cgroup'larda çalışır.
2. **Tek yön.** işlemler → günlük → atıf → dersler → önceden kayıtlı aday → kayıt-yalnız ileri test → öneri → sahip
   onayı → normal sürüm. Motorun hiçbir karar yoluna yazma yolu yoktur.
3. **State salt-okunur.** Motor `data/state` ve `data/market` altına hiç yazmaz; bu çekirdek düzeyinde
   (`ProtectSystem=strict` + yalnız `ReadWritePaths=/opt/tradingbot/data/research`) garanti edilir.
4. **Ağ yalnız veri biriminde (P1b'den).** Gece birimi `PrivateNetwork=yes` ile çalışır: araştırma sırasında indirme
   çekirdek düzeyinde imkânsızdır. P1a'da ağlı birim hiç yoktur.
5. **Kendini doğrulayan yalıtım.** Çekirdek ayarlarına güvenmekle yetinilmez: her çalıştırma S0'da yalıtımın gerçekten
   yürürlükte olduğunu dener (state'e yazma reddedilmeli, gece biriminde soket açılamamalı, cgroup bellek sınırı
   beklenen değerde olmalı); biri tutmazsa çalıştırma durur (§2.8).
6. **Dolaylı kanallar ölçülür.** Motor hiçbir karar dosyasına yazmasa da paylaşılan IP (418/429), CPU/IO çekişmesi
   (Box zamanlayıcısı, koruyucu izleyici) ve mevcut ResearchCoordinator dolaylı karar değişikliği yaratabilir. Bunlar
   kabul ölçütlerinde açıkça ölçülür (§2.9).
7. **Mühürlü ön kayıt.** Her eşik, ızgara, kapı ve hedef kuralı koda ve bu belgeye sha ile sabitlenir; değişiklik =
   yeni sürüm + yeni deneme sayımı.
8. **Dürüst etiket.** Her alan MEASURED / RECONSTRUCTED / MODELED / MISSING olarak etiketlenir; eksik gün EKSİK, henüz
   kesinleşmemiş gün GEÇİCİ olarak görünür, sıfır sayılmaz.

### 2.2 Süreçler ve zamanlama (VPS saati Europe/Istanbul; tüm `OnCalendar` satırları açıkça `UTC` yazar)

| Birim | Aşama | Ne zaman | Ağ | Ne yapar | Kaynak |
|---|---|---|---|---|---|
| `tradingbot-worker` | — | sürekli (değişmez) | var | turlar (~20,5 dk'da bir, ~5,5 dk), defterler | 6G (VPS override) |
| `tradingbot-dashboard` | — | sürekli (P1'de değişmez) | — | panel | 512M |
| **YENİ** `tradingbot-engine-night.service` + `.timer` | **P1a** | `OnCalendar=*-*-* 01:37:00 UTC`, iç son tarih 03:40, `TimeoutStartSec=2h15min` (sert durma 03:52) | **yok** (`PrivateNetwork=yes`) | S0–S7b aşamaları (§6.1); P1a'da yalnız S0, S1a, S3, S7, S7b | P1a: MemoryHigh 0,4G / MemoryMax 0,5G; P2+: 1,2G / 1,5G (P0'da kesinleşir) |
| **YENİ** `tradingbot-engine-data.service` + `.timer` | P1b | `OnCalendar=*-*-* 00:41:00 UTC`, `TimeoutStartSec=50min` (sert durma 01:31) | **var** (tek ağlı birim) | arşiv-önce veri ekleme, REST kuyruğu (koruma şartlı), arşiv uzlaştırma, evren anlık görüntüsü, `data_status.json` | MemoryHigh 0,8G / MemoryMax 1G (P0'da kesinleşir) |
| `tb-engine-backfill` (geçici `systemd-run`) | P1b | bir kez, sahip başlatır | var | ilk doldurma; kaldığı yerden devam eder; ilerleme ve tahmini bitişi `data_status.json`'a yazar | MemoryMax 1G |

Zamanlama kuralları (birim sözleşme testiyle zorunlu):
- `OnCalendar` her satırında `UTC` yazmalıdır. Yoksa zamanlayıcılar 3 saat kayar ve 04:00 yayın penceresine düşer.
- Hiçbir motor zamanlayıcısı hh∈{00,04,08,12,16,20} için hh:00–hh:35 aralığında (4h indeks yayın pencereleri)
  **başlamaz**. Gece biriminin iç son tarihi (03:40) 04:00 penceresinden önce biter.
- **Yedekle çakışma saatle değil durumla denetlenir.** Yedek zamanlayıcısı `OnCalendar=hourly` +
  `OnUnitActiveSec=1h` + `RandomizedDelaySec=5min` + `OnBootSec=10min` kullanır; açılıştan sonra herhangi bir dakikaya
  kayabilir. Bu yüzden S0 `systemctl is-active tradingbot-backup.service` sorar; aktifse 30 sn aralıkla en fazla 15 dk
  bekler, sonra devam eder ve `BACKUP_OVERLAP` yazar.
- İlk doldurma (P1b) uzun sürer. Kaba tahmin: vadeli `metrics` yalnız **günlük** zip'tir (≈ 45 sembol × ≈ 1.400 gün
  ≈ 60 bin dosya, her biri `.CHECKSUM` ile iki istek), kline aylık zip'leri ≈ 14 bin dosyadır; toplam ≈ 150 bin istek.
  Sıralı ve 4h pencerelerinde duraklayarak bu **10–24 saat**, muhtemelen iki takvim günü sürer. Doldurma kendi saatine
  bakar, 4h pencerelerinde (hh:00–hh:35) **kendini duraklatır** ve ilerlemeyi (`done/total`, son 1 saatin hızı,
  tahmini bitiş) `data_status.json`'a yazar.
- `Persistent=false`: kaçırılan çalıştırma açılışta telafi edilmez (açılıştaki ilk turla çakışmasın diye).

### 2.3 Ayrı, sabitlenmiş klon; config ve sürüm kayması

- Kod `/opt/tradingbot/engine-app` altından çalışır. Bu, aynı deponun etiketli bir SHA'da sabitlenmiş **ayrı bir
  klonudur** (`git worktree` değildir). Böylece app sürüm betiklerinin temiz-ağaç denetimleri ve `worktree remove
  --force` adımları motoru hiç etkilemez. Motor sürümleri `/opt/tradingbot/app`'e ff-merge **yapılmaz**. Böylece worker
  durmaz, yeniden başlamaz ve 44 dakikalık ilk tura girmez.
- Venv paylaşılır (`/opt/tradingbot/venv`); yeni bağımlılık yok (pandas, numpy, pyarrow, PyYAML mevcut). `tradingbot`
  venv'e pip ile kurulu **değildir**. Bu yüzden `ExecStart=/opt/tradingbot/venv/bin/python -s -m tradingbot ...` ve
  `WorkingDirectory=/opt/tradingbot/engine-app` kullanılır: `-m` çalışma dizinini `sys.path`'e ekler. `python -I`
  **kullanılmaz**, çünkü `-I` hem `PYTHONPATH`'i hem çalışma dizinini yok sayar ve import'lar kırılır. Birim sözleşme
  testi bunu da denetler.
- **Config: `load_config` / `load_v3` hiç çağrılmaz.** `config_v3.load_v3`, `learning_mode` ve `shared_experience`
  bölümlerindeki bilinmeyen anahtarda `ConfigError` atar (config_v3.py:863–883). Sabitlenmiş eski bir motor, yeni bir app
  sürümünün eklediği anahtar yüzünden her gece kırılırdı (tersi de olabilir). Bu yüzden motor canlı config'i yalnız
  **ham YAML** olarak okur: `rawconfig.py`, `/opt/tradingbot/app/config.yaml`'ı salt-okunur açar, baytların sha256'sını
  `run_status.json`'a yazar ve `yaml.safe_load` ile yalnız ihtiyaç duyduğu birkaç anahtarı hoşgörülü biçimde çeker
  (bilinmeyen anahtar yok sayılır, eksik anahtar `None` olur). İhtiyaç listesi koddadır ve testlidir: defter
  `enabled`/`min_stop_pct`, `learning_mode.extra_entries`, `risk.starting_equity_usdt`. Bir AST testi
  `research_engine`'in `config.load_config`, `config_v3.load_v3` veya `config_v3` import etmesini yasaklar.
- **Sürüm kayması (SKEW) denetimi.** S0, çalışan app'in SHA'sını (`/opt/tradingbot/app/.git` altından salt-okunur
  okunur) ve engine-app SHA'sını karşılaştırır: app SHA'sı engine-app SHA'sının atası veya kendisi olmalıdır
  (`git -c safe.directory=/opt/tradingbot/engine-app -C /opt/tradingbot/engine-app merge-base --is-ancestor`). Değilse
  `SKEW` yazılır ve o gece yalnız S0, S1a (arşiv; rotasyon kaybı olmasın diye) ve S7 çalışır.
- **Yeniden sabitleme kuralı.** Bundan sonraki her app sürüm betiği, aynı çalıştırmada engine-app'i yeni app SHA'sına
  (veya onu içeren motor etiketine) yeniden sabitler ve `--check`'te iki SHA'yı da yazar. Bu kural `docs/OPERATIONS.md`
  sürüm listesine ve sürüm betiği şablonuna eklenir.
- **Ortam:** `EnvironmentFile` yoktur (sır yok). Yalnız şu `Environment=` satırları vardır:
  - `TRADINGBOT_DATA=/opt/tradingbot/data`
  - `TRADINGBOT_STATE_DIR=/opt/tradingbot/data/state`
  - `ALLOW_LIVE_TRADING=false`
  - `TZ=UTC`
  - `PYTHONUNBUFFERED=1`
  - `PYTHONIOENCODING=utf-8`
  - `PYTHONDONTWRITEBYTECODE=1` (engine-app salt-okunur; sürüm betiği ayrıca `compileall` ile önceden derler)
  - `ENGINE_EXPECTED_MEMORY_MAX=<bayt>` (S0 cgroup denetimi için; birim dosyasındaki `MemoryMax` ile aynı sayı, sözleşme
    testi eşitliği denetler)
- Her çalıştırma S0'da PAPER modunu doğrular (`state/mode.json` salt-okunur `json` ile okunur). PAPER değilse veya
  okunamıyorsa çıkar.

### 2.4 Kilit ve çakışma kuralları

- **İki ayrı motor kilidi** vardır (fcntl, `SingletonLock` deseni):
  - `data/research/locks/data.lock`: veri birimi, ilk doldurma ve sahibin elle çalıştırdığı `engine-data` komutları;
  - `data/research/locks/analysis.lock`: gece birimi ve elle `engine-night`.
  Tek bir motor-geneli kilit olsaydı, saatlerce süren ilk doldurma gece birimini `SKIPPED_LOCKED` yapar, kapanış
  arşivi durur ve rotasyon kalıcı veri kaybına yol açabilirdi.
- Kilidi alamayan birim bekleme yapmaz: `SKIPPED_LOCKED` sonucu yazar ve 0 koduyla çıkar. Bu, `--check`'te görünür.
- Gece birimi store'u (P1b+) yalnız son tamamlanmış veri çalıştırmasının `data_seal`'ında listelenen ay parçalarıyla
  okur. Okuduğu parçanın checksum'ı mühürdekinden farklıysa (o sırada doldurma yazıyorsa) o seri o gece `DATA_MOVING`
  ile atlanır; çalıştırma durmaz.
- Veri birimi en geç 01:31'de biter, gece birimi 01:37'de başlar; bu iki zamanlayıcı birimi aynı anda çalışmaz. Ama
  ilk doldurma gece birimiyle aynı anda çalışabilir. Bu yüzden bellek bütçesine **gece birimi + doldurma** birlikte
  girer (§2.5).
- Motor worker kilidini **hiç** almaz. Worker'ın HistoryStore'una (`data/market/history`) **hiç** yazmaz: ayrı kök
  kullanılır, worker'ın IndexRefresher'ı ile yazma yarışı yapı gereği yoktur (tohumlamadaki okuma yarışı §3.2).
- `engine-status` salt-okunurdur ve kilit almaz.

### 2.5 Kaynak sınırları

Motor birimleri için ortak sınırlar:

| Ayar | Değer |
|---|---|
| `User` | `tradingbot` |
| `Nice` | `19` |
| `CPUWeight` | `10` (worker varsayılanı 100) |
| `CPUQuota` | `100%` (`nproc` < 4 ise `60%`) |
| `IOSchedulingClass` / `IOWeight` | `idle` / `10` |
| `OOMScoreAdjust` | `1000`: bellek darlığında çekirdek önce motoru öldürür |
| Dosya sistemi | `ProtectSystem=strict`, `PrivateTmp=yes`, `ReadWritePaths=/opt/tradingbot/data/research`, `ReadOnlyPaths=/opt/tradingbot/data /opt/tradingbot/app` |
| Ağ | gece birimi `PrivateNetwork=yes`; veri birimi ağlı |
| Günlük okuma | yalnız veri biriminde `SupplementaryGroups=systemd-journal` (worker 429/418 korumasını okumak için, §3.4) |
| Diğer | `Type=oneshot`, `Persistent=false` |
| `OnFailure` | `tradingbot-alert@%n.service`, yalnız o birim VPS'te varsa (P0 denetimi) |

- `PrivateTmp=yes` zorunludur: `ProtectSystem=strict` `/tmp`'yi de salt-okunur yapar ve `tempfile` salt-okunur çalışma
  dizinine düşerdi.
- `/opt/tradingbot/data/research` sürüm betiği tarafından ilk başlatmadan **önce** `tradingbot` sahipliğiyle (0750)
  oluşturulur. Yoksa systemd `ReadWritePaths` bağlamasını kuramaz ve birim `226/NAMESPACE` ile düşer.

**P0 bellek kuralı:** worker MemoryMax 6G + dashboard 0,5G + gece birimi MemoryMax + doldurma/veri birimi MemoryMax
≤ fiziksel RAM − 1G. Sığmazsa `MemoryHigh` düşürülür; `OOMScoreAdjust=1000` motorun önce ölmesini garanti eder.

**Kabul ölçütü (her aşamada; ayrıntı §2.9):**
- Motor açık gecelerde worker tur p95 süresi, motor kapalı gecelere göre en fazla +%5 üstünde kalır (A/B geceleri).
- Worker `NRestarts` değişmez.
- Motor cgroup'unun `memory.peak` değeri ≤ 0,8 × MemoryMax olur.
- Dolaylı kanal ölçütleri (§2.9) tutar.

### 2.6 Bileşenler

Paket `tradingbot/research_engine/`; Türkçe docstring'ler; bu belge.

| Modül | Yeni / yeniden kullanım | Görev | Aşama |
|---|---|---|---|
| `paths.py`, `lock.py` | YENİ | kök düzeni, disk koruması (araştırma kökü ≤ 20 GB, boş disk ≥ 10 GB, yoksa çalışmayı reddet), `data.lock` / `analysis.lock` | P1a |
| `selfcheck.py` | YENİ | S0 çalışma zamanı iddiaları: PAPER, yalıtım (state'e yazma reddi, soket reddi, cgroup `memory.max`), SKEW, yedek çakışması | P1a |
| `rawconfig.py` | YENİ | hoşgörülü ham YAML okuma, config sha'sı, ihtiyaç listesi | P1a |
| `ledgers.py` | YENİ (saf JSON okuma) | `futures_ledger.json` ve `spot_ledger.json` ham, salt-okunur okuma; `schema_version` denetimi (fail-closed) | P1a |
| `closes.py` | YENİ | ham kapanış arşivi (vadeli + spot `history[]`), cüzdan hareketi arşivi (`entries[]`), gecelik ölçülmüş anlık görüntü, `rev`, rotasyon payı, uzlaştırma, `RESTORED` | P1a |
| `daily_target.py` | `bot_scorecard.find_books`/`_window` mantığı (spot eklenerek) | günlük hedef: kayıt görünümü, cüzdan görünümü, `LEDGER_MARK` MTM; GEÇİCİ/KESİN; REVİZE; kayıtlı bakış hükümleri | P1a |
| `summary.py` | YENİ | `engine_status`, `digest_tr.md`, `engine_summary.json` (boyut sınırlı) | P1a |
| `backup.py` | YENİ | küçük araştırma alt ağaçlarının günlük sıkıştırılmış yedeği + doğrulama | P1a |
| `night.py` | `replay/pipeline.py` + `deploy/replay_runner.sh` desenleri | aşama orkestrasyonu, son tarih, A/B takvimi, telemetri | P1a (iskelet) |
| `store.py` (`ResearchStore`) | `history/store.HistoryStore`'u ALT SINIF olarak kullanır | ay-parçası checksum'ı, satır kaynağı ve öncelik, kurtarma, seri kilidi, yeni türler `metrics_5m`, `duka_1h`, `duka_1m` | P1b |
| `universe.py` | `universe.json` salt-okunur + YENİ | araştırma evreni U_R, günlük evren ve `exchangeInfo` anlık görüntüsü | P1b |
| `datastore.py` | `HistoryCollector`, `IncrementalUpdater`, `ArchiveClient`, `RateBudget`/`BudgetPool` | spec listesi, seri başına yeniden deneme/yakalama, düşük REST bütçesi, worker 429/418 koruması, arşiv uzlaştırma, `data_status.json` + `data_seal` | P1b |
| `seed.py` | YENİ | worker deposundaki mevcut parquet'leri tutarlı biçimde salt-okunur kopyala + doğrula | P1b |
| `provider.py` (`StoreProvider`) | YENİ (~80 satır) | `.klines(symbol, interval, limit, start_ms, end_ms)` ile ağsız bar; parite testi | P1b |
| `journal.py` | ledger + shared_experience + trade_memory + provenance + position_path + plans + CF kayıtları + P1a arşivi | işlem günlüğü `tj_v1` | P2 |
| `pathrec.py` | `learning_cf` bar içi sıra kuralı | 1m/5m yol yeniden kurma | P2 |
| `rehydrate.py` | kuralların saf fonksiyonları (`box_theory`, `donchian_trend`, `candle_book`, `ema200_trend`, `paper_rules`) | defterlerin kapanışta düşürdüğü **hedefleri ve sinyal bağlamını** yeniden kurma (stop ölçülmüştür; 1 tick denetimi) | P2 |
| `fidelity.py` | `learning_cf.ExecModel.of_ledger`, `_net_replay` | gerçek işlemin yeniden oynatma doğruluğu (≤ 0,05R) | P2 |
| `attribution.py`, `cfgrid.py` | `learn/labels`, `learn/postmortem` kodları, `learning_cf._decompose`, `learning_cf_aux` | kural kodları, karşı-olgusal ızgara, ayrıştırma | P2 |
| `lessons.py` | `learn/lesson_store` (`build_lesson`, `transition`) + `learn/journal_archive.SegmentArchive` | ders deposu, ayrı kök | P3 |
| `trials.py` | YENİ | deneme defteri (`trials.jsonl`), bakış kaydı, bekletme okuma kaydı | P3 |
| `pit_universe.py` | YENİ (veri biriminde) | data.binance.vision listelerinden zaman noktasında (point-in-time) evren: delist olanlar dahil, listeleme/delist tarihleri | P3 |
| `library/` | kural fonksiyonları, `signal_lab`, `candle_lab`, `gold_lab(_v2)`, `book_lab` tanımları | strateji kütüphanesi `LIB_v1` | P3 |
| `wf.py`, `cscv.py` | `quant/walkforward` (`make_folds(validation_days>0)`, `run_three_way`, `leakage_check`, `fold_report`), `quant/execution_scenarios`, `validation.deflated_sharpe` / `probabilistic_sharpe` | gece walk-forward; PBO için YENİ CSCV | P3 |
| `explore.py` | YENİ | keşif katmanı: her varyantın mühür sonrası ileri OOS sonucu (§6.8) | P3 |
| `prospective.py` | `FuturesLedgerV2`, `ExecModel`, `learning_mode.fit_size` | kayıt-yalnız ileri yeniden oynatma adayları | P4 |
| `promotion.py` | `quant/champion.evaluate_challenger`, `research_policy` eşli istatistikleri | mühürlü terfi kapısı `PROMOTION_REGISTRY` | P4 |
| CLI (`cli_v3`) | YENİ alt komutlar, **tembel import** | `engine-night`, `engine-status [--brief]` (P1a); `engine-data {--backfill,--update,--status}` (P1b); `engine-query <konu>` (P2/P3) | P1a+ |

### 2.7 Veri akışı

```
data.binance.vision / fapi (düşük bütçe)          [engine-data 00:41 UTC, AĞLI, P1b]
        │
        ▼
research/archive_cache  ──►  research/store (ResearchStore)  ──►  data_status.json + data_seal
                                         │
════════════════════ PrivateNetwork=yes ═╪═══════ [engine-night 01:37–03:40 UTC] ═════════
                                         ▼
state/** (SALT-OKUNUR) ─► S1a kapanış + cüzdan hareketi arşivi + gecelik anlık görüntü (P1a)
   (vadeli + spot)                       │
                                         ▼
                         S1b günlük tj_v1 + yol + rehydrate + fidelity (P2)
                                         │
                                         ▼
                         S2 atıf (kodlar + cfgrid) ─► S6 dersler (önceden kayıtlı bakışlar)
                                         │                       │
S4 kütüphane walk-forward ◄──────────────┘     ders → aday spec ─┘
        │                                                 │
        ├──► keşif katmanı (mühür sonrası ileri OOS; terfi yok)
        ▼                                                 ▼
  trials.jsonl ──► Kapı A (kayıtlı bakış) ──► S5 kayıt-yalnız ileri yeniden oynatma ──► Kapı B
                                                                                     │
S3 günlük hedef (P1a) ──► S7 özet (digest_tr.md, engine_summary.json) Kapı C: ÖNERİ dosyası
                                                                                     │
                                                    SAHİP ONAYI ─► normal sürüm (yeni kâğıt defter)
```

### 2.8 Yalıtım ve doğrulama

- **AST testi (karar modülleri → motor yasak).** Şu modüllerin hiçbiri `research_engine`'i import etmez: `engine_v3`,
  `engine.py`, `strategy_paper*`, `box_timer`, `box_theory`, `protective_monitor`, `paper_rules`, `candle_book`,
  `donchian_trend`, `ema200_trend`, `pattern_trader/*`, `coinhead/*`, `learn/*`, `economics_gate`, `execution/*`,
  `accounting/*`, `risk/*`, `shared_experience/*`, `dashboard/*`.
- **CLI tembel import.** Worker `python -m tradingbot watch`'ı `cli` üzerinden çalıştırır ve `cli.py` her çağrıda
  `cli_v3.register`'ı yükler (cli.py:500). Bu yüzden `cli.py` ve `cli_v3.py`'de **üst düzey** `research_engine` import'u
  yasaktır; motor alt komutları modülü yalnız kendi işleyicileri içinde import eder. AST testi bunu denetler.
- **Motor → yasaklı modüller.** `research_engine` şunları import etmez: `config.load_config`, `config_v3` (§2.3),
  `sqlite3` (§4.1). Ağ kullanan modüller (`datastore.py`, `pit_universe.py`) yalnız veri biriminin giriş noktasından
  import edilir; gece biriminin import grafiği bunları içermez (test).
- **Dashboard sınırı:** Dashboard motor çıktılarını yalnız JSON olarak okur. Şema sabitleri, eşitlik testleriyle
  eşleştirilir; import yoktur (shared_experience sözleşmesiyle aynı).
- **Dosya açma modu denetimi:** Sahte bir state ile tam bir gece çalıştırılır; `open`/`os.replace`/`Path.write_*`
  monkeypatch'lenir. Testin kanıtladıkları: `data/research` dışına hiç yazma olmaz ve tüm ledger'lar `"r"` modunda
  açılır.
- **İkinci savunma hattı (özellik testi).** Rastgele üretilmiş sahte state ağaçlarında motor, `bot_scorecard.find_books`
  ile eşleşen hiçbir yola (`state/futures_ledger.json`, `state/*/futures_ledger.json`) ve `state/spot_ledger.json`'a
  asla yazmaz; çekirdekteki `ReadOnlyPaths`'ten bağımsız olarak.
- **Çalışma zamanı öz-denetimi (S0; biri tutmazsa çalıştırma `ISOLATION_BROKEN` ile durur, uyarı birimi tetiklenir):**
  1. `data/state`, `data/market` ve `/opt/tradingbot/app` altında `O_CREAT|O_EXCL` ile benzersiz bir deneme dosyası
     açmak `EROFS` veya `EACCES` ile **başarısız olmalıdır**. Beklenmedik biçimde başarılı olursa dosya hemen silinir
     ve çalıştırma durur.
  2. Gece biriminde bir genel IP'ye (`1.1.1.1:443`, DNS'siz) `socket.connect` 2 sn içinde **başarısız olmalıdır**.
  3. Birimin kendi cgroup'unun `memory.max` değeri (`/proc/self/cgroup` → `/sys/fs/cgroup/.../memory.max`)
     `ENGINE_EXPECTED_MEMORY_MAX`'e eşit olmalıdır.
  4. `/tmp`'ye yazılabilmeli (`PrivateTmp`), `data/research`'e yazılabilmelidir.
- **Ledger parmak izi gece değişmezi DEĞİLDİR.** `ops/fingerprint.py` `fingerprint_v1` açık pozisyonları da hash'ler ve
  canlı worker bunları her turda değiştirir. Bu yüzden gece öncesi/sonrası parmak izi her gece yanlış alarm verirdi.
  Parmak izi yalnız **sürüm (deploy) anında** kullanılır. Gece güvencesi çekirdekten (`ReadOnlyPaths`), S0 öz-denetiminden
  ve yukarıdaki açma modu testinden gelir.

**Sahte VPS ile gerçek VPS'in sınırı (açıkça):**

| Doğrulanan | Sahte VPS (yerel harness) | Yalnız gerçek VPS |
|---|---|---|
| Betik akışı (`--dry-run` / deploy / `--check` / geri alma), dosya düzeni, birim dosyası **içeriği** (sözleşme testi), kapılı kurulum mantığı (sahte `systemctl`), bağımsız değişmez koşucuları | ✔ | — |
| Arşiv doğruluğu, rotasyon, `rev`, RESTORED, günlük hedef sayıları, `scorecard --daily` eşitliği, idempotans | ✔ | — |
| `PrivateNetwork`, `ProtectSystem`/`ReadOnlyPaths` gerçekten uygulanıyor mu | — | ✔ (S0 öz-denetimi + elle smoke çalıştırma) |
| `MemoryMax`, `OOMScoreAdjust`, cgroup `memory.peak`, `CPUWeight`/IO etkisi | — | ✔ |
| Zamanlayıcının UTC'de ateşlenmesi, `226/NAMESPACE` olmaması, `SupplementaryGroups` ile günlük okuma | — | ✔ |
| Worker etkisi: tur p95, Box kaçan bar, koruyucu izleyici gecikmesi, 418/429, ret oranları | — | ✔ (A/B geceleri, §2.9) |

### 2.9 Dolaylı karar kanalları ve A/B geceleri

Motor hiçbir karar dosyasına yazmaz; ama şu kanallar defterlerin kararını **dolaylı** değiştirebilir. Her biri kabulde
ölçülür:

| Kanal | Nasıl karar değiştirir | Önlem | Kabul ölçütü |
|---|---|---|---|
| Paylaşılan IP, REST 418/429 | motorun yol açtığı bir yasak worker'ın veri çekimini düşürür; defterler girişi reddeder/atlar | P1a'da motor ağsız; P1b'de bütçe ≤ 0,1, başlık takibi, **worker son 60 dk'da 429/418 aldıysa REST adımı tamamen atlanır** (§3.4) | motor açık dönemde worker 429/418 sayısı = 0; `DATA_VERDICT_MISSING` / bayat veri retlerinin oranı motor kapalı gecelerle aynı (fark CI'ı 0'ı içerir) |
| CPU/IO çekişmesi | Box 5 dk zamanlayıcı iş parçacığı ve koruyucu izleyici zamana bağlı karar verir; gecikirlerse karar değişir | Nice 19, CPUWeight 10, IO idle, `--jobs 1` | Box kaçan bar oranı (`--check`'in zaten örneklediği `$T7-box-samples.txt`) ve koruyucu izleyici gecikmesi (`state/protective_monitor.json` `last_run.at` yaşı ve `duration_p50_s`, motorun çalıştığı dakikalarda örneklenir) motor kapalı gecelerin tabanı içinde |
| ResearchCoordinator | mevcut sistem, ana bot girişlerinde politikayı kendiliğinden etkinleştirebilir (`engine_v3:1915`, `PAPER_RESEARCH_ACTIVE`) | motor onun dosyalarını okumaz/yazmaz; sahibin P0 kararı (§9.1) | `PAPER_RESEARCH_ACTIVE` etkinleştirme sayısı `--check`'te raporlanır; motor açık/kapalı gecelerde fark beklenmez |

**A/B geceleri.** Motorun etkisi bayat bir ön tabanla değil, eşleşmiş gecelerle ölçülür. Her motor sürümünden sonraki
14 gecede, mühürlü bir takvimle (UTC tarihinin gün-yıl sırası çiftse AÇIK, tekse KAPALI) gece birimi KAPALI gecelerde
S0'dan sonra `AB_OFF` yazıp çıkar. İstisna: herhangi bir ledger'ın rotasyon payı 3 günden azsa S1a yine çalışır ve o gece
karşılaştırmadan çıkarılır (`AB_OFF_ZORUNLU_ARŞİV`). Sürüm betiğinin `--ab-report` komutu tur p50/p95, `memory.peak`,
Box kaçan bar, koruyucu izleyici gecikmesi, 418/429 ve ret oranlarını aynı saat penceresinde AÇIK ve KAPALI geceler için
yan yana verir.

**Taban çizgisi karışıklığı (wt-tourfix).** Paralel tur düzeltmesi (`34ae8d2`, çatallanmış kNN alt süreci) tur
sürelerini ve worker bellek profilini değiştirecek. Bu yüzden:
- P0 tur p50/p95, `memory.peak` ve Box zamanlayıcı tabanı **her worker sürümünden sonra** yeniden alınır;
- ~~motor sürümü, başka bir sürümün 7 günlük `--check` penceresi içinde dağıtılmaz;~~ (eski metin, tarihçe için)
  **Değişiklik (2026-10-06, sahip kararı: "3 temiz gün sonra"):** motor sürümü, başka bir sürümün yeniden başlatmasından
  (`deploy-logs/*-restart-at.txt`) en az **3 gün** sonra VE o sürümün ilk iki `--check` çıktısı temiz olduysa
  dağıtılabilir. 3 günü sürüm betiği zorlar (`3g-pencere-dışı` kapısı); iki temiz `--check` sahip + inceleyici
  yargısıdır (betik denetlemez). Motorun kendi etkisini A/B geceleri yine ölçer;
  **Değişiklik (2026-10-07, sahip kararı: "öğrenmeyi şimdi kur"):** pencere **1 gün** (`1g-pencere-dışı`; P1a betiği
  `tb-engine-4962209.sh`, 2026-10-08'den P1b betiği `tb-engine-57cfef1.sh` de); iki temiz `--check` kuralı aynen kalır;
- motorun etkisi yalnız A/B gecelerine göre değerlendirilir.

---

## 3. Veri deposu

### 3.1 Kök düzeni: `/opt/tradingbot/data/research/`

State altında olmadığı için saatlik yedekleri şişirmez. Küçük ve yeri doldurulamaz alt ağaçların kendi günlük yedeği
vardır (§3.6).

| Yol | İçerik | Saklama | Aşama |
|---|---|---|---|
| `closes/<book>/YYYY-MM.jsonl.gz` | ham kapanış arşivi: vadeli defterler + `main_spot` (ana botun `spot_ledger.json`'ı) | sonsuza dek, mühürlü segmentler | P1a |
| `entries/<book>/YYYY-MM.jsonl.gz` | cüzdan hareketi arşivi (`entries[]`: FEE, PNL, FUNDING — "late funding" ve "funding reversal" dahil —, TRANSFER, LIQ_FEE, TAX, SLIPPAGE_INFO) | sonsuza dek | P1a |
| `snapshots/YYYY-MM-DD.json.gz` | gecelik ölçülmüş anlık görüntü: her defterin `wallet_balance` (spot: `cash`, `locked_cash`, `assets`), açık pozisyonlar (miktar, giriş, `last_price`, gerçekleşmemiş), ledger `seq`/`updated_at`, okuma zamanı | sonsuza dek (küçük) | P1a |
| `target/daily.jsonl` | günlük hedef satırları (yalnız eklenir; `rev`) | sonsuza dek | P1a |
| `summary/` | `run_status.json`, `daily_target.json`, `engine_summary.json` (≤ 256 KB), `digest_tr.md` (≤ 8 KB); P1b'den `data_status.json` | en son | P1a |
| `runs/<run_id>/run_status.json` | çalıştırma geçmişi | son 30 | P1a |
| `backup/research-small-YYYY-MM-DD.tar.gz` + `.sha256` | küçük alt ağaçların günlük yedeği (§3.6) | 7 günlük + 4 haftalık | P1a |
| `locks/data.lock`, `locks/analysis.lock` | motor kilitleri | — | P1a |
| `store/<market>/<SYM>/<tf>/<YYYY>/<MM>.parquet` + `.sha256` yan dosyası + `manifest.json` (+ satır kaynağı sütunu `_src`) | mumlar, fonlama, `metrics_5m`, Dukascopy | sonsuza dek (değişmez geçmiş) | P1b |
| `archive_cache/<host>/<url yolu>` (+ `.missing`) | data.binance.vision zip aynası (`gold_lab.ArchiveCache` / `book_lab.ZipCache` düzeni, bayt-özdeş) | ≥5m zip'ler sonsuza dek; 1m zip'ler doğrulanmış alımdan sonra silinir (checksum manifestte kalır) | P1b |
| `dukascopy/XAUUSD/...` + `manifest.jsonl` | Dukascopy XAUUSD BID (yalnız tarihsel; hazır bir aynadan içe alınır) | sonsuza dek | P1b+ |
| `universe/YYYY-MM-DD.json`, `exchangeinfo/YYYY-MM-DD.json.gz` | günlük evren ve listeleme/delist anlık görüntüsü | sonsuza dek | P1b |
| `pit_universe/listings.json` | zaman noktasında evren (delist dahil, listeleme/delist tarihleri) | sonsuza dek | P3 |
| `journal/tj_v1/YYYY-MM.jsonl(.gz)` | işlem günlüğü | sonsuza dek | P2 |
| `paths/YYYY-MM/<trade_key>.parquet` | yeniden kurulmuş yollar | sonsuza dek (küçük) | P2 |
| `attribution/YYYY-MM.jsonl.gz` | kodlar + ızgara | sonsuza dek | P2 |
| `lessons/`, `trials/trials.jsonl`, `library/registry.json`, `library/config_epochs.json` | dersler, denemeler (bakışlar dahil), mühürlü kütüphane, config dönemleri | sonsuza dek | P3 |
| `explore/<lib_version>/<variant>/<SYM>.jsonl` | keşif katmanı ileri OOS sonuçları | sonsuza dek | P3 |
| `backtests/<tarih>/` | walk-forward ayrıntısı | 14 gece | P3 |
| `prospective/<aday_id>/` | aday defterleri | sonsuza dek | P4 |
| `proposals/<id>.json` + `.tr.md`, `approvals/approvals.jsonl` | öneriler, sahip kararları | sonsuza dek | P4 |

### 3.2 `ResearchStore` (P1b; HistoryStore alt sınıfı; worker deposu etkilenmez)

- **Ay-parçası checksum'ı.** `HistoryStore._recompute` (store.py:215) her yazımda tüm seriyi yeniden okur. 5m/1m
  doldurmada bu O(n²) olur. Alt sınıf yalnız dokunulan ayın parça checksum'ını günceller ve parçanın yanına bir
  `.sha256` yan dosyası yazar.
- **`data_seal` tam store'u yeniden okumaz.** Gecelik tam `canonical_checksum` (to_csv) 3–4 GB'lık depoda her gece
  yapılmaz. `data_seal` = sıralı `(seri, ay, parça checksum'ı)` listesinin sha256'sıdır. Tam kanonik checksum yalnız
  haftalık örneklemle (her hafta serilerin 1/7'si) ve istendiğinde hesaplanır.
- **Satır kaynağı ve öncelik.** `HistoryStore.write` aynı zaman damgasında **son** gelen satırı tutar
  (store.py:177–186). Sonra gelen bir REST yazımı arşivle doğrulanmış barı ezebilirdi. Alt sınıf her satırın kaynağını
  (`_src` ∈ {`archive`, `archive_unverified`, `rest`, `seed`}) tutar ve öncelik uygular: `archive` > `seed` >
  `archive_unverified` > `rest`. Düşük öncelikli bir satır yüksek öncelikli olanı **asla** ezmez. Yüksek öncelikli satır
  düşük öncelikliyi ezerken önce fark çıkarılır ve `runs/` altına yazılır (beklenen: 0).
- **CHECKSUM'sız zip doğrulanmış sayılmaz.** `ArchiveClient.fetch_month`, `.CHECKSUM` dosyası yoksa zip'i yine kabul
  eder (collector.py:111 `if chk:`). Motor bu durumda satırları `archive_unverified` işaretler; `data_status`'ta ayrı
  sayılır ve sonraki gecelerde checksum yeniden aranır. Doğrulanmış sayımlara ve parite kapısına girmez.
- **Kurtarma ile gerçek bozulma ayrıdır.** Yazım sırası: parça (`os.replace`) → parça `.sha256` → manifest. Birim
  `TimeoutStartSec` ile parça yazıldıktan sonra ama manifest kaydedilmeden öldürülürse `validate()` uyuşmazlık görür.
  - Parça okunabiliyor ve kendi `.sha256`'sıyla tutuyorsa durum `MANIFEST_LAG`'dir: manifest atomik parçalardan
    yeniden hesaplanır, o ay arşivden yeniden çekilip karşılaştırılır; seri devam eder.
  - Parça okunamıyor veya `.sha256`'sıyla tutmuyorsa durum `CORRUPT`'tur: parça karantinaya alınır, o ay yeniden
    çekilir. Seri yalnız aynı ay iki ardışık gece yeniden çekilemezse durur (fail-closed) ve `--check` bunu gösterir.
- **Fonlama aralığı veriden.** `step_ms_for("funding")` 8 saati sabit kodlar (store.py:51), oysa birçok USDⓈ-M sembolü
  4 saatte veya 1 saatte bir uzlaşır. Alt sınıf fonlama aralığını sembol ve ay başına veriden (ardışık uzlaşma zaman
  damgalarının medyan farkı) kaydeder ve boşlukları bu aralıkla hesaplar. Geriye testler fonlamayı sabit aralıkla değil,
  serideki **gerçek uzlaşma zaman damgalarında** fiyatlar.
- **Kilit.** Seri başına fcntl kilidi vardır (`data.lock`'un içinde, ek güvence).
- **Yeni türler.**
  - `metrics_5m`: `[timestamp, oi, oi_usdt, top_acct_ls, top_pos_ls, global_ls, taker_ls_vol]`; `futures_data` /
    `crowd_data` önbelleklerinin yerini alır.
  - `duka_1h`, `duka_1m`: `[timestamp, open, high, low, close, volume_proxy]`.
- **Yalnız kapanmış barlar** yazılır.
- **µs→ms normalizasyonu tüm spot serilerde.** Binance spot arşivi 2025'ten itibaren zaman damgalarını mikrosaniye
  verir. Normalizasyon yalnız PAXG'ye değil, 2025+ **bütün** spot serilerine (BTC/ETH spot bağlamı dahil) uygulanır;
  birim, değerin büyüklüğünden değil arşiv yolundan (spot) ve tarihten belirlenir ve testlidir.
- **İlk tohum (tutarlı kopya).** Worker'ın `data/market/history` altındaki mevcut parquet'leri salt-okunur kopyalanır,
  böylece worker'ın elindeki seriler (örn. 89 vadeli 4h serisi) yeniden indirilmez. Worker'ın IndexRefresher'ı kopya
  sırasında yazıyor olabilir. Bu yüzden seri başına: manifest okunur → parçalar kopyalanır → manifest yeniden okunur;
  manifest değiştiyse veya `validate()` uyuşmazlık verirse o seri bir kez yeniden kopyalanır, yine olmazsa o seri arşivden
  yeniden indirilir. Tohumlama hiçbir koşulda tüm işi başarısız saymaz.

### 3.3 Kapsam (araştırma evreni U_R)

| Piyasa | Semboller | Zaman dilimleri | Başlangıç |
|---|---|---|---|
| USDⓈ-M vadeli | giriş evreni (40, `universe.json`) ∪ BTC/ETH ∪ son 180 günde herhangi bir defterin veya CF'nin işlem yaptığı semboller | 5m, 15m, 1h, 4h, 1d; fonlama; `metrics_5m`; mark/premium 1h | listelenme (metrics ≈ 2021-12) |
| **Altın vadeli** | **XAUUSDT** (2025-12-01'den), **PAXGUSDT** (2025-03-01'den) | 1m–1d + fonlama | listelenme |
| Altın spot | PAXGUSDT | 5m, 1h, 4h, 1d | 2020-08 |
| Spot bağlam | BTCUSDT, ETHUSDT | 1h, 1d | 2020 |
| Spot (ana bot) | ana botun son 180 günde spot işlem yaptığı semboller | 1m (tembel, P2), 1h | son 400 gün |
| Dukascopy | XAUUSD BID | 1h (2006+), isteğe bağlı 1m (2019+) | yalnız **hazır bir aynadan** içe alınır (`gold_lab.duka_path` düzeni, `duka_coverage` ile doğrulanır). v1'de indirici yazılmaz; gold_v2'nin ayna işi VPS'te veya sahibin PC'sinde bittiyse tarball `scp` ile taşınır. Yoksa "yapılamadı" yazar. |
| 1m (yol için) | son 120 günde **herhangi bir defterin veya adayın** işlem yaptığı her sembol + iki altın vadelisi | 1m | son 400 gün, **tembel** (yalnız gerektiğinde, P2) |

**Değişiklik (2026-10-08, ilk doldurmadan önce):** VPS'te ilk `--backfill` disk kapısında durdu: evren 1351 seri (son 180 günün defter ve CF sembolleri ≈ 145 vadeli), tahmin 20 GB × 1,3 > araştırma sınırı 17 GB. Hiçbir şeye dokunulmadı. Düzeltme: giriş evreni ve BTC/ETH **dışındaki** vadelilerde (yalnız `islem_180g`/`cf_180g`) ince seriler (5m, 15m, `metrics_5m`) **son 400 günden** başlar (`EXTRA_FINE_DAYS`). Bu sembollerin 1h/4h/1d, fonlama ve mark/premium serileri tabandan kalır. Hiçbir sembol evrenden çıkmaz. 400 gün son 180 günün işlemlerini ve yol kaydını kapsar. Derin 5m geçmişi yalnız botların girdiği evrende tutulur. Böyle bir sembol sonra giriş evrenine girerse başlangıç tabana döner. Eksik eski dönem (sembol başına ≈ 1.400 `metrics` günü + ≈ 120 ay) `--update` onarım geçişinde gecede en çok 200 görevle gelir: haftalarca `ARCHIVE_HOLES`/`PARTIAL` (çıkış 0) ve `--check`'te [DİKKAT] beklenir; hızlısı `--backfill`'dir. Tersine, giriş evreninden çıkan sembolün 400 günden eski verisi ve tohumun worker deposundan kopyaladığı eski satırlar silinmez (budama yok; tahmine girmez, disk kapıları döngü içinde korur). `universe/<gün>.json` artık kısa serileri `series_short` (anahtar → başlangıç günü) altında yazar. Dağıtım betiğinin disk tahmini bunları kısa-seri tablosuyla hesaplar. Kaba hesap: 25 çekirdek × 132 MB + 120 kısa × 44 MB ≈ 8,6 GB, × 1,3 ≈ 11 GB.

Notlar:
- `config.yaml` PAXG'yi yaş filtresiyle işlem evreninden çıkarır. Altın, araştırma evrenine **açıkça** eklenir.
- **Hayatta kalma yanlılığı.** U_R bugünkü `universe.json` ve son işlem görenlerdir; `exchangeInfo` anlık görüntüleri
  ancak bugünden başlar. Bu evren tek-enstrüman taktikleri için kabul edilir ama **kesitsel ve portföy taktikleri
  (XSEC) için güçlü biçimde yanlıdır**. P3'te `pit_universe.py`, data.binance.vision listelerinden (delist olmuş
  semboller hâlâ listelidir) ilk/son aylık zip'e göre listeleme ve delist tarihlerini çıkarır. Bu zaman noktasında evren
  kurulana kadar XSEC taktikleri **Kapı A'ya uygun değildir** (yalnız uyarıyla gösterilmez, kapıdan dışlanır).

**Boyut tahmini (~45 B/satır).** Toplam ≈ 3–4 GB parquet + ≈ 2–5 GB zip. Kalem kalem:

| Kalem | Boyut |
|---|---|
| 5m | ≈ 1,0 GB |
| 15m | ≈ 0,35 GB |
| 1h / 4h / 1d | ≈ 0,15 GB |
| `metrics_5m` | ≈ 1,2 GB |
| 1m | ≈ 0,5 GB |
| P1a arşivleri (closes, entries, snapshots) | yılda < 0,2 GB |

Disk 72 GB, %38 dolu. Araştırma kökü için sınırlar: 15 GB'ta uyarı, 20 GB'ta çalışmayı reddet.

### 3.4 Günlük ekleme (`engine-data --update`, 00:41 UTC, P1b)

1. **Önce arşiv.** Eksik aylık zip'ler (ayın 2–4'ünde yayımlanır) ve dünün günlük zip'leri indirilir: klines (tüm
   zaman dilimleri), fundingRate, metrics. `.CHECKSUM` doğrulanır; yoksa satırlar `archive_unverified` olur (§3.2).
2. **Worker REST koruması.** REST'e dokunmadan önce veri birimi worker'ın son 60 dakikalık günlüğünü okur
   (`journalctl -u tradingbot-worker --since -60min`, `SupplementaryGroups=systemd-journal` ile salt-okunur) ve
   `market/http.py`'nin 429/418 satırlarını sayar (metin kalıbı testte sabittir). Sayı > 0 ise veya günlük
   okunamıyorsa REST adımı **tamamen atlanır** (`REST_GUARD_SKIP` / `REST_GUARD_UNKNOWN`); o gece yalnız arşiv
   kullanılır.
3. **REST kuyruğu.** Koruma geçerse, henüz arşivde olmayan son saatlerin **kapanmış** barları REST ile alınır ve
   `_src=rest` olarak işaretlenir.
4. **Arşiv uzlaştırma.** Günlük zip yayımlanınca REST'ten gelen barlar checksum doğrulanmış arşiv barlarıyla
   değiştirilir (öncelik kuralı, §3.2). Fark satır satır `runs/`'a yazılır (beklenen: 0).
5. **Anlık görüntü.** Evren ve `exchangeInfo` (listeleme/delist) günlük olarak kaydedilir.
6. **Hata yalıtımı.** Her seri 3 denemeli, titreşimli (jitter) bir yeniden denemeye sarılır ve istisnası yakalanır.
   Bir serinin hatası o seriyi `STALE` yapar; çalıştırmayı durdurmaz. Bu, `history-collect`'in ilk hatada durma
   açığını kapatır.
7. **Mühür.** `data_status.json` (şema `engine_data_status_v1`) her seri için `last_ts`, `rows`, `gaps` (veriden
   gelen fonlama aralığıyla), `quality`, `unverified_rows` ve `stale` alanlarını yazar. `data_seal` parça
   checksum'larının sha256'sıdır (§3.2). Sonraki her çıktı bu mührü anar; her sonuç mühürden yeniden üretilebilir.

**REST bütçesi (paylaşılan IP).** Worker `rate_budget_safety=0.7` (config_v3:86) ile 2400/dk fapi limitinin %70'ini
kullanır. `BudgetPool` süreç başınadır; bu yüzden motorun payı ayrıca sınırlanmalıdır:

| Kural | Değer |
|---|---|
| Motor güvenlik payı | **≤ 0,1** (≤ 240 ağırlık/dk); tüm seriler tek `BudgetPool` paylaşır |
| Öncelik | geçmiş aylar için REST yok, önce arşiv (CDN ağırlık harcamaz) |
| Worker koruması | worker son 60 dk'da 429/418 aldıysa veya günlük okunamıyorsa REST yok |
| Başlık takibi | her yanıtta `X-MBX-USED-WEIGHT-1M` okunur; IP limitinin %50'sine ulaşınca bekler |
| Ret kodları | 418/429 gelince REST adımı durur, ilgili seriler `STALE` olur, kayıt düşülür |
| Saatlik REST kuyruğu | yok (gece-önce tasarım) |

Tipik günlük maliyet birkaç yüz ağırlıktır.

### 3.5 `StoreProvider` ve laboratuvarlar

- `StoreProvider`, `.klines(symbol, interval, limit, start_ms, end_ms)` imzasını uygular (`signal_lab.ArchiveProvider`
  ile aynı 7 sütun + `is_closed`; geniş şema varyantı taker alanlarını da verir). Ayrıca `funding_frame()` sağlar
  (gerçek uzlaşma zaman damgalarıyla).
- **Parite kapısı.** Mühürlü bir laboratuvar `StoreProvider`'a ancak şu koşulla geçer: 3 sembol × 3 tf × 6 ay için
  float64 ve zaman damgası bayt eşitliği **ve** `book_lab` `series_digest` eşitliği sağlanmalı (REST kaynaklı kuyruk
  barları dahil; `archive_unverified` satırlar pariteye girmez). O zamana kadar mühürlü laboratuvarlar `--cache
  /opt/tradingbot/data/research/archive_cache --offline` ile çalışır. Baytlar özdeş olduğu için sha256 digest'leri geçerli
  kalır.

### 3.6 Araştırma yedeği ve geri yükleme

- Saatlik yedek yalnız state ve vault'u kapsar (`tradingbot-backup.service`
  `ReadOnlyPaths=/opt/tradingbot/data/state /opt/tradingbot/data/vault`). Oysa P1a'dan sonra `data/research/closes` ve
  `entries`, rotasyonla ledger'dan düşen kayıtların **tek kopyasıdır**; `trials.jsonl`, dersler, öneriler ve sahip
  onayları da yalnız orada yaşar.
- Bu yüzden gece biriminin son aşaması (S7b) şu küçük alt ağaçların sıkıştırılmış günlük yedeğini alır:
  `closes/`, `entries/`, `snapshots/`, `target/`, `trials/`, `lessons/`, `library/`, `explore/`, `prospective/`,
  `proposals/`, `approvals/`, `summary/`, son 30 `runs/`. **Dışarıda kalanlar:** `store/`, `archive_cache/`,
  `dukascopy/`, `backtests/`, `paths/` (yeniden üretilebilir). **P1b eki (2026-10-06):** `universe/` ve `exchangeinfo/`
  (§3.1; günlük evren ve listeleme/delist anlık görüntüsü) da DAHİLDİR: küçüktür ve o günün borsa durumunun tek
  kaydıdır, sonradan yeniden çekilemez; veri biriminin çalıştırma kayıtları (`runs/data/`) girmez. Arşiv `.sha256` ile
  yazılır ve hemen yeniden okunarak doğrulanır; 7 günlük + 4 haftalık tutulur. `docs/BACKUP_RESTORE.md`'deki "VPS dışına taşıyın" adımı
  `data/research/backup/`'ı da kapsayacak biçimde güncellenir.
- **Motor geri yüklemesi:** `engine-restore <arşiv>` kuru çalıştırma; `--yes` ile uygular, mevcut ağaç
  `research.pre-restore-<ts>` olarak kenara alınır (asla silinmez).
- **State geri yüklemesinden sonra.** State eski bir yedekten geri yüklenirse ledger'lar geri sarılır; arşiv, ledger'ın
  artık tutmadığı kayıtları taşır. Motor bunu ledger `seq`'inin bir önceki anlık görüntüye göre **azalmasından** ve veri
  kökünde yeni bir `state.pre-restore-<ts>` klasörü belirmesinden anlar. Bu durumda:
  - geri yükleme noktasından sonra kapanmış ve ledger'da artık bulunmayan arşiv kayıtları `RESTORED_AWAY` işaretlenir
    (silinmez, uzlaştırmadan çıkarılır, ayrı sayılır);
  - etkilenen günler `REVİZE (RESTORE)` olur;
  - uzlaştırma bu kayıtlar için `INCONSISTENT` vermez; `RESTORE_EVENT` satırı `run_status.json`'a ve özete yazılır.

---

## 4. İşlem günlüğü şeması (`tj_v1`) ve kapanış arşivi

### 4.1 Kaynaklar (hepsi salt-okunur)

- **Vadeli defterler:** her defterin `futures_ledger.json` `history[]` kayıtları: kanonik para. `TradeRecord`'da `pnl`
  = `net_pnl`, ve `fees`, `entry_fee`, `exit_fee`, `funding_paid`, `funding_received`, `slippage_cost`, `spread_cost`,
  `gross_pnl`, `fills[]`, `leverage`, `liquidation_price`, `quantity`, `effective_notional/margin` alanları vardır.
  `FuturesLedgerV2` her açılışta `features.initial_stop` (futures_ledger.py:409, `setdefault`) ve her kapanışta
  `features.risk_usdt` (futures_ledger.py:567) yazar; ana bot için `initial_stop` ayrıca motor tarafından plandan
  konur (engine_v3.py:2244).
- **Ana botun spot defteri:** `state/spot_ledger.json` (engine_v3.py:262 yükler, 1852 kaydeder). Ana bot spotta gerçekten
  işlem yapar (engine_v3.py:2654/2657 `self.spot2.market_buy`). `SpotLedger` de `TradeRecord` tutar, aynı
  `history_keep=5000` / `entries_keep=2000` rotasyonuyla (spot_ledger.py:75). Spot kaydında `venue=spot`, kaldıraç 1,
  fonlama yoktur. **Fark:** spot kaydı `features` taşımaz (spot_ledger.py:458; `r_multiple` 0 kalır) ve her satış
  dolumu ayrı bir kayıttır (`id = <emir>-<n>`); kısmi satışlar ayrı satırlar olarak gelir.
- **Cüzdan hareketleri:** her iki ledger türünün `entries[]` listesi (§4.2).
- **Açık pozisyonlar:** vadeli `positions[]` (`last_price` dahil); spot `lots`/`assets`/`cash`.
- `shared_experience` satırları `xp_entry` / `xp_outcome` / `xp_cf`: `situation_v1`, R cinsinden maliyet, kohort.
- `trade_memory.jsonl`: ana bot ajan bağlamı; strateji defterlerinde ema200/atr14.
- `entry_provenance.jsonl`: `decision_id`, `code_sha`.
- `position_path.jsonl`: ana bot yol anlık görüntüleri.
- `pattern_trader/plans.json`: Formasyon planları.
- `counterfactual_trades.json`, `shadow_book.json` ve arşivleri.
- Motorun kendi aday defterleri (P4).
- **SQLite okunmaz.** `state/tradingbot.db` WAL kipindedir (storage/db.py:68); `ReadOnlyPaths` altındaki bir okuyucu
  `-shm` dosyasını oluşturamaz. Motor SQLite'a hiç dokunmaz (AST testi `sqlite3` import'unu yasaklar). İleride gerekirse
  yalnız bir kopyası `mode=ro&immutable=1` ile açılır.

Bütün ledger okumaları ham JSON'dur (`ledgers.py`); dosya `"r"` ile açılır, `schema_version` bilinen değerlerden biri
değilse o defter o gece `SCHEMA_UNKNOWN` ile atlanır (fail-closed). Kitap listesi `bot_scorecard.find_books` mantığıyla
bulunur **ve** `state/spot_ledger.json` açıkça eklenir (`find_books` yalnız `futures_ledger.json`'ı tarar).

### 4.2 Satır kimliği, arşiv, rotasyon, kesinleşme

- **Kayıt anahtarı.** `trade_key = book|trade_id|opened_at`. Ana bot iki alt deftere ayrılır: `main_fut`
  (`state/futures_ledger.json`) ve `main_spot` (`state/spot_ledger.json`); raporlarda ikisi "Ana bot" altında birlikte ve
  ayrı satırlar olarak görünür. Spot kısmi satışları kendi kayıt kimlikleriyle ayrı anahtar alır; `position_group =
  main_spot|sembol|opened_at` ile gruplanır.
- **Kapanış arşivi (P1a).** Her gece her kapanmış kaydın birebir kopyası `closes/` altına eklenir. Satırlar yalnız
  eklenir. Bir kapanmış kaydın içeriği değişirse (`settle_late_funding` geç fonlama yazar, koruyucu izleyici geriye
  tarihli kapanış yazar), aynı anahtarla `rev+1` satırı eklenir. Son 14 günde kapananlar her gece yeniden eşitlenir.
- **Cüzdan hareketi arşivi (P1a).** `FuturesLedgerV2` ve `SpotLedger` `entries[]`'i `entries_keep=2000`'de kırpar
  (futures_ledger.py:239, :267). Her işlem en az 3 hareket, açıkken her fonlama uzlaşması için bir hareket daha yazar;
  yani cüzdan hareketleri `history`'den (5000) çok daha önce düşer. Oysa geriye doğru `E_book` ve cüzdan görünümü tam bu
  hareketlere dayanır. Bu yüzden `closes.py` her gece `entries[]`'i de arşivler:
  - anahtar = `sha256(book|ts|kind|ref_id|amount|note)` + aynı anahtarın kaçıncı tekrarı olduğu; idempotent;
  - hizalama: arşivin son 50 hareketinin dizisi ledger'daki listede aranır; bulunamazsa (arada rotasyon olmuşsa) aradaki
    boşluk `ENTRIES_GAP` olarak işaretlenir ve o pencerenin cüzdan görünümü EKSİK olur;
  - her hareket ilk görüldüğü gecenin `observed_at` damgasını taşır (cüzdan görünümü bununla günlere atanır, §7.1).
- **Gecelik ölçülmüş anlık görüntü (P1a).** Her gece S1a, her defter için `wallet_balance` (spot: `cash`,
  `locked_cash`, `assets`) ve açık pozisyonları (miktar, giriş, `last_price`, gerçekleşmemiş kâr) ile ledger `seq` ve
  `updated_at`'i `snapshots/` altına yazar (`MEASURED`, okuma zamanıyla). P1a'dan önceki günler için bu yoktur: o
  günler, hareketleri hâlâ ledger'da duruyorsa `RECONSTRUCTED`, düşmüşse `EKSİK` etiketlenir; asla "kesin" sayılmaz.
- **Rotasyon payı.** `--check` her defter için iki pay gösterir: (a) `history` için rotasyona kalan kayıt sayısı ve
  bunun ortalama günlük kapanışa göre gün karşılığı, (b) `entries` için aynı hesap. Herhangi biri 3 günün altına inerse
  uyarı verir. İlk sınıra `entries` ile Box ulaşacak.
- **Gün satırları kesin değildir (GEÇİCİ → KESİN).**
  - `settle_late_funding` (futures_ledger.py:772–840) kapanmış bir kaydın `pnl`/fonlamasını sonradan değiştirir ve
    FUNDING hareketini **o anın** zaman damgasıyla yazar.
  - Koruyucu izleyici `closed_at`'i bar kapanışına geri tarihleyebilir (f8b05fb betik notları;
    docs/PROTECTIVE_MONITOR_V1.md).
  - TP1 kısmi çıkışları, ortada henüz bir `TradeRecord` yokken cüzdanı değiştirir.
  - `features.funding_coverage` bekleyen uzlaşma gösterebilir.
  Bu yüzden her gün satırı en az **3 gün** ve o günün kayıtlarının fonlama kapsaması tamamlanana kadar `GEÇİCİ` kalır.
  7 gün sonra kapsama hâlâ eksikse gün `EKSİK (fonlama)` olur ve hükümlerden çıkar. Revizyonlar yalnız eklenir (`rev`)
  ve satırda görünür `REVİZE` işareti taşır. **Hükümler yalnız KESİN günleri kullanır.**
- **Geç fonlama hangi güne yazılır (tutarlı kural).** Kayıt görünümünde kaydın `closed_at` gününe (o günün satırı
  revize edilir); cüzdan görünümünde hareketin gözlendiği pencereye. Hedef hükmü cüzdan/MTM görünümünü kullanır
  (§7.1); kayıt görünümü açıklama içindir.
- **Uzlaştırma değişmezleri.**
  - Kayıt görünümü: ledger'ın **elinde tuttuğu pencerede**, defter ve ay başına Σ arşiv `net_pnl` (son rev) = Σ ledger
    `TradeRecord.pnl` (tolerans 1e-6 USDT); ücret, fonlama ve kayma toplamları ve kayıt sayısı için de aynısı.
  - Cüzdan görünümü: iki ardışık anlık görüntü arasında `wallet_balance` farkı = o arada gözlenen hareketlerin toplamı
    (1e-6). Spot için `cash` farkı ile spot hareketleri aynı biçimde.
  - Uyuşmazlıkta o gece `INCONSISTENT` işaretlenir; ders ve öneri yazılmaz. Geri yükleme kaynaklı farklar `RESTORED`
    olarak ayrılır (§3.6).
- **Ücret özdeşliği.** `fees == entry_fee + exit_fee` her işlemde denetlenir. `learn/labels.label_outcome`
  (labels.py:45), `entry_fee` varken `fees + entry_fee + exit_fee` toplayarak ücreti iki kez sayar. Motor bu fonksiyonu
  olduğu gibi **kullanmaz**; kendi hesabını yapar ve bunun için bir regresyon testi vardır. Worker içindeki düzeltme
  ayrı, onaylı bir iştir (P6).
- **Ucuz türetilmiş alanlar (P1a).** Kapanış arşivinin yanında küçük bir `closes_derived` satırı tutulur:
  `min_to_next_funding` (açılış anında, kayıttaki `funding_hours_utc`'den) ve provenance karar zamanı varsa
  `decision_delay_s`. Ham arşiv birebir kalır; türetilmiş alanlar ayrı satırdadır.

### 4.3 Alanlar (sahibin listesi kalın)

Her alanın yanında `field_source` ∈ {`MEASURED`, `RECONSTRUCTED`, `MODELED`, `MISSING`, `NOT_APPLICABLE`} tutulur.

| Grup | Alanlar |
|---|---|
| Kimlik | `schema`, `trade_key`, `rev`, `source` (`LIVE_PAPER` / `PROSPECTIVE_REPLAY` / `EXPLORATION_FWD` / `BACKTEST` / `COUNTERFACTUAL`; asla karıştırılmaz), `book`, `book_name`, `trade_id`, `decision_id`, `code_sha`, `config_hash`, `config_epoch` |
| Enstrüman | **`symbol`**, `instrument_class` (`COIN` / `GOLD`), `venue` (`UM_futures` / `spot`), `side` |
| **Taktik ve varyasyon** | **`tactic`** (`setup_type`/strateji; Box/D4/C4 için düzeltilmiş), `family` (TREND / MOMENTUM / FADE / BREAKOUT / CANDLE_PATTERN / CARRY / XSEC / SEASONAL / MAIN_ENSEMBLE), **`variation_id`**, `params_hash` (C4: `definition_sha`; T2/M2/Box/D4: kural modülü kaynağı + parametrelerin hash'i, P6'ya kadar), `cohort` (POLICY / CAPACITY / SELECTIVITY / UNTAGGED), `record_only`, `learning_unlocked_by[]` |
| Zaman | `signal_ts`, `opened_at`, `closed_at`, `closed_at_backdated` (bool), `hold_hours`, `bars_held`, `decision_delay_s` |
| **Giriş yeri** | `ref_price`, **`entry_fill`** (VWAP), `entry_slip_bps`, `range_pos_20` (0–1), `dist_20d_high_atr`, `dist_20d_low_atr`, `dist_ema200_atr`, `dist_level_atr` (kutu kenarı / kanal / tetik), `session` (ASIA / LONDON / NY / WEEKEND), `utc_hour`, `weekday`, `funding_rate_at_entry`, `min_to_next_funding`, `situation_entry` (`situation_v1`) |
| **Büyüklük ve kaldıraç** | **`qty`**, **`notional`**, `margin`, **`leverage`** (spot: 1, `MEASURED`), `liquidation_price` (spot: `NOT_APPLICABLE`), `liq_distance_in_stops`, `risk_usdt`, `risk_pct_of_equity`, `equity_at_entry`, `size_rule` (SLOT / BUMP_MIN_NOTIONAL / SHRUNK_TO_MARGIN) |
| Plan | `initial_stop`, `stop_dist_pct`, `stop_dist_atr`, `targets[]`, `tp1_fraction`, `breakeven_at_mfe_r`, `max_hold`, `planned_rr_after_cost` |
| Çıkış | `exit_price` (dolumların VWAP'ı), `exit_reason`, `exit_basis` (LEVEL / GAP), `tp1_done`, `fills[]` (tür, zaman, miktar, fiyat, ücret, kayma) |
| **Gelir ve maliyet** | **`gross_pnl`**, **`entry_fee`**, **`exit_fee`**, **`slippage_cost`** (MODELED), **`spread_cost`** (defterde 0 → `MODELED_ZERO`; ayrıca kaydedilmemiş tahmin `spread_est`), **`funding_paid`**, **`funding_received`** (spot: `NOT_APPLICABLE`), `funding_net`, `funding_complete`, **`net_pnl`**, `net_r`, `gross_r`, `cost_r{fee, slippage, funding}`, `pnl_pct_book_equity`, `pnl_pct_total_equity` |
| Yol | `mfe_r`, `mae_r`, `t_mfe`, `t_mae`, `order` (MFE_FIRST / MAE_FIRST), `time_to_1r`, `giveback_r`, `capture_ratio`, `path_source` (POSITION_PATH / STORE_1M / STORE_5M / EXTREMES_ONLY), `ambiguous_bars` |
| Bağlam | `situation_exit` (aynı saf fonksiyon ve `SCHEMA_SHA` ile store barlarından yeniden hesaplanır), `btc_ctx_entry/exit`, `oi_change_24h`, `taker_ratio`, ana bot ajan eğilimleri/uyarıları (varsa), `signal_ctx{}` (yeniden kurulmuş kural bağlamı), altına özgü: oturum, PAXG/XAU bazı |
| Atıf (P2) | `codes_loss[]`, `codes_win[]`, `primary_code`, `fidelity{ok, delta_r}`, `cf_grid_ref`, `lesson_ids[]` |
| Köken | `sources[{file, offset/row_id}]`, `data_seal`, `missing_fields[]`, `journal_version` |

**Stop ve R paydası (ölçülmüş).**
- Vadeli kayıtlarda `initial_stop` = `features.initial_stop` ve `risk_usdt` = `features.risk_usdt`; ikisi de
  **MEASURED**'dır.
- **R paydası her yerde `features.risk_usdt`'tir:** ledger'ın kendi `r_multiple`'ı ve `settle_late_funding`'in R'ı
  yeniden hesaplaması da bunu kullanır (futures_ledger.py:567 yorumu). Böylece motorun R'ı ledger'ınkiyle tutarlı kalır;
  geç fonlama sonrası `net_r` aynı payda ile yeniden hesaplanır.
- **`MISSING` yalnız** bu özellikleri taşımayan eski (legacy) kayıtlar içindir; o kayıtlarda net R yeniden hesaplanmaz,
  işlem karşı-olgusal ızgaraya ve ayrıştırmaya **girmez**. Asla tahminle doldurulmaz.
- **Spot kayıtları** `features` taşımaz: stop ve risk, varsa `trade_memory`/provenance'tan alınır (`MEASURED`, kaynak
  dosyasıyla); yoksa `MISSING`. Spot işlemleri USDT ve % toplamlarına (günlük hedef) her durumda tam girer; yalnız R
  tabanlı istatistiklerden, sayısı gösterilerek, dışarıda kalabilir.
- **`targets` ledger kaydında yoktur** (`TradeRecord` alanı değildir; açık `Position` taşır ama kapanışta düşer).
  Hedefler ve sinyal bağlamı için kaynak sırası: 1) `xp_entry` / provenance; 2) kural bağlamının yeniden kurulması
  (§5.2); 3) hiçbiri yoksa `MISSING`.

---

## 5. Neden kaybetti / neden kazandı

### 5.1 Yol yeniden kurma (`pathrec`)

- Kaynak: 1m barlar, yoksa 5m. Ana bot için ayrıca `position_path`.
- Bar içi sıra `learning_cf` kuralıdır: açılış → ters → lehte → kapanış.
- Yol, `opened_at`'tan `closed_at`'a kadar kurulur; üstüne çıkış sonrası 48 barlık kuyruk eklenir (STOPPED_THEN_REVERSED
  için). Geriye tarihli kapanışlarda (`closed_at_backdated`) yol kaydedilen `closed_at`'e göre kurulur.
- Etiket her zaman `RECONSTRUCTED`'dır. Belirsiz bar içi sıraya sahip işlemler sayılır ve sıraya dayanan kodlardan
  dışlanır.

### 5.2 Kural bağlamını yeniden kurma (`rehydrate`)

Strateji defterleri kapanışta hedefleri ve sinyal bağlamını düşürür; ilk stop ise ölçülmüştür (§4.3). Motor,
defterin deterministik kural fonksiyonunu store barları üzerinde `signal_ts` anına göre yeniden çalıştırır ve yalnız
şunları geri kazanır:

| Defter | Geri kazanılan bağlam |
|---|---|
| Box | `box_high` / `box_low` / `box_mid`, konum, gün aralığı, `params_label`, hedefler |
| D4 | `channel_high20`, ATR sınırları, hedefler |
| T2 / M2 | `ema200`, `atr14`, hedefler |
| C4 | sinyal bloğu, hedefler |

**Kabul koşulu (denetim, kaynak değil):** yeniden hesaplanan giriş referansı ledger girişiyle **ve** yeniden hesaplanan
ilk stop ölçülmüş `features.initial_stop` ile 1 tick içinde eşleşmelidir. Eşleşme yeniden kurmanın doğru bar ve doğru
parametrelerle yapıldığını kanıtlar; stop değeri her durumda ölçülmüş olandır. Eşleşmezse `RECONSTRUCT_FAILED` yazılır,
hedefler ve `signal_ctx` `MISSING` kalır; tahmin edilmez. Hedef: Box, D4, T2, M2, C4 için eşleşme oranı ≥ %95 (config
dönemi başına ayrı raporlanır, §6.2).

### 5.3 Yeniden oynatma doğruluğu (fidelity) kapısı

Her gerçek işlem, defterin kendi `ExecModel.of_ledger(book)` maliyet modeliyle (cf_label_v3 ile aynı kod yolu) atılabilir
bir `FuturesLedgerV2` içinde gerçek yol üzerinde yeniden oynatılır. R paydası her iki tarafta da kaydın
`features.risk_usdt`'idir.
- `|r_replay − r_actual| ≤ 0,05R` ise işlem karşı-olgusal ayrıştırmaya girer.
- Değilse işlem yalnız kural kodları alır. Nedeni (yol boşluğu, belirsiz bar, farklı dolum tabanı, geriye tarihli
  kapanış) sayılır ve özette gösterilir.
- Hedef doğruluk oranı ≥ %90'dır.

**Değişiklik (2026-10-08, inceleme düzeltmesi; gerçek veriye uygulanmadan önce).** Canlı defter stopu seviyeden değil, seviyenin
ötesindeki İLK gözlemden doldurur (60 sn fiyat örneği `GAP_FILL_AT_FIRST_OBSERVATION`/`PRICE`, kural barı açılışı,
ihtiyatlı kapanış). Yeniden oynatma bunu MODELLER: stop, kaydın çıkış anından geriye bir kural dilimi penceresi (Box 5m;
D4, C4, Formasyon, ana bot 4h; T2, M2 1d) içinde tetiklenirse dolum referansı kaydın gözlem fiyatıdır
(`fill_model = RECORDED_FILL`; kayma modeli yine uygulanır). Örnekleme aşması böylece `FILL_BASIS` sayılmaz ve işlemi
ızgaradan düşürmez (inceleme B1: Box stoplarının %0,32–1'i genişliğinde aşma tek başına > 0,05R idi). Pencere dışındaki
eski bir fitil (örneklerin kaçırdığı) gerçek bir farktır ve `FILL_BASIS` olarak listelenir.

### 5.4 Kural kodları (`attribution_v1`, eşikler mühürlü)

Kodlar yalnız işlemin **kendi** verisinden (yol, maliyet, bağlam) üretilir. Alternatif sonuçları kod tanımına
girmez; onlar ayrı tutulur (§5.5). Bir işlem birden çok kod taşıyabilir. Birincil kod sabit bir öncelik sırasıyla
seçilir.

| Kayıp kodu | Kural (özet) |
|---|---|
| `WRONG_DIRECTION` | MFE_R < 0,3 ve stop |
| `STOPPED_THEN_REVERSED` | stop sonrası kuyruk, orijinal ufuk içinde orijinal hedefe ulaştı |
| `STOP_TOO_TIGHT` | stop mesafesi < 0,5 × ATR14 (giriş tf'si) veya defterin ampirik gürültü bandının altında |
| `LATE_ENTRY` | dolum, sinyal kapanışının > 0,3 ATR ötesinde; veya MFE'den önce MAE_R > 0,7 |
| `GIVEBACK` | MFE_R ≥ 1 ve net_R ≤ 0; veya capture < 0,3 |
| `PROFIT_NOT_TAKEN` | MFE ≥ TP1 mesafesi ama TP1 dolmadı |
| `TIME_DECAY` | zaman stopu ve \|r_gross\| < 0,25 |
| `COST_KILLED` (`_FEE` / `_FUNDING` / `_SLIPPAGE`) | r_gross > 0 ≥ r_net; alt tür en büyük maliyet payına göre |
| `TIGHT_STOP_COST_MULTIPLIER` | cost_R > 0,25 çünkü stop mesafesi küçük (ücret R'ı 1/stop ile ölçeklenir) |
| `FUNDING_AGAINST` | funding_R < −0,1 |
| `GAP_FILL` | `exit_basis = GAP` |
| `REGIME_SHIFT` | `situation_v1` trend/vol kovası girişten çıkışa değişti |
| `AGAINST_BTC` | BTC 4h trendine ters ve BTC aleyhte gitti |
| `VOL_SPIKE` | çıkıştaki ATR oranı ≥ 1,5 × giriş |
| `LIQUIDATION_LEVERAGE` | stop'tan önce likidasyon |
| `NOISE_LOSS` | \|net_R\| maliyet bandında, başka anlamlı kod yok |
| Ana bot: `DISSENT_WAS_RIGHT`, `TOO_MANY_WARNINGS`, `LOW_RR` | kapanıştaki `last_decisions` değil, provenance'taki **giriş** kararı kullanılır |

| Kazanç kodu | Kural (özet) |
|---|---|
| `TREND_CONTINUATION` | MFE_FIRST, hedef, rejim değişmedi |
| `CLEAN_ENTRY` | +1R'a ulaşmadan önce MAE_R < 0,3 |
| `MEAN_REVERSION_DONE` | fade taktiği orta/hedefe ulaştı |
| `TRAIL_CAPTURE` | capture ≥ 0,6 |
| `FUNDING_TAILWIND` | funding_R > +0,1 |
| `COST_EFFICIENT` | cost_R < 0,05 |
| `BREAKEVEN_SAVED` | MAE sonrası başabaş çıkışı |
| `LUCKY_GAP` | hedef boşlukla doldu |
| `LUCKY_WIN` / `FRAGILE` | kazançtan önce MAE_R ≥ 0,8; derslerde "zayıf kazanç" sayılır |

Tek işlem kodu **gözlemdir**, neden kanıtı değildir. Kanıt yalnız toplu istatistikten gelir (§5.7).

#### Değişiklik (2026-10-08, inceleme düzeltmesi; gerçek veriye uygulanmadan önce)

`attribution_v1` 2026-10-06'da yalnız sentetik altın yollarla mühürlenmişti ve HİÇBİR gerçek işleme uygulanmadı (P2 VPS'e
kurulmadı; depoda gerçek sonuç yoktur). 2026-10-07 incelemesi (karar FAIL) tanımda hatalar buldu; düzeltme ilk gerçek veri
çalıştırmasından ÖNCE aynı sürüm adıyla yeniden mühürlendi. Hiçbir eşik gerçek bir sonuca bakılarak seçilmedi. Yukarıdaki
tablo tarih olarak kalır; geçerli okuma aşağıdadır.

- **Mühür:** `ATTRIBUTION_SHA` `3f4596e37822f1603db6c296937005d45bcfd0215a18f1dc993ba535a0236626` →
  `914875a1f6631822585ffd5e67e7ffa9a721069e47b6df7a9a624a8a789a6d7f` (`tests/test_research_engine_attribution.py`'de sabit).
- **`GAP_FILL` ve `exit_basis` (inceleme B1).** Eski okuma seviyenin ötesindeki her dolumu `GAP` sayıyordu; canlı defter
  stopu her zaman seviyenin ötesindeki ilk 60 sn örnekten doldurduğu için neredeyse her stop birincil `GAP_FILL`
  oluyordu. Yeni kural (`attribution.classify_exit_basis`, mühürlü): gözlemden dolan bir stop yalnız (a) bar yolunda
  (1m/5m) stopa ilk ulaşan bar stopun ÖTESİNDE açıldıysa (piyasa boşluğu; ilk işlem barı hariç) ya da (b) seviye ötesi
  dolum ≥ 0,25R ise (`GAP_OVERSHOOT_R`; belgenin TIME_DECAY / TIGHT_STOP_COST "maddi" eşiği) `GAP`'tir; seviye yolda sürekli
  işlem gördüyse ve aşma < 0,25R ise `LEVEL` (mekanizma `SAMPLED`). Aşma günlükte `exit_overshoot_r` (MEASURED) ve
  `exit_fill_detail`'de yazılır. Birincil öncelikte `GAP_FILL` karar kodlarının ARKASINA alındı: likidasyon, maliyet,
  stop-sonra-dönüş, yön, dar stop, geç giriş, **boşluk**, geri verme, … (dolumun seviye ötesi kısmı kaybın yalnız aşma
  payını açıklar).
- **`LATE_ENTRY` MAE kolu (M2).** "MFE'den önce MAE_R > 0,7"nin harfiyen okuması: en iyi lehte uca İLK ulaşılan
  gözlemden KESİN önceki gözlemlerde en kötü aleyhte < −0,7R VE MFE ≥ 0,3R (`LATE_ENTRY_MIN_MFE_R`; hiç lehte gitmeyen
  işlem `WRONG_DIRECTION`'dır, iki kod MFE'ye göre ayrılır). Bar yolu ister, bar içi sıraya dayanmaz. Eski kol (`order =
  MAE_FIRST` ve `mae_r < −0,7`) hiç lehte gitmeyen işlemlerde tetikleniyor, "önce −0,8R → MFE → stop" durumunu ise
  kaçırıyordu.
- **Yol gözlemleri (küçük).** Çıkışı içeren bar çıkış SONRASI fiyatları da taşır: MFE/MAE ve yol kodları çıkıştan önce
  kapanmış işlem barlarını (`ts + adım ≤ closed_at`) ve son gözlem olarak çıkış dolumunu kullanır (`pathrec.pre_exit_obs`).
  Yeniden oynatma (fidelity, ızgara) çıkış barını kullanmaya devam eder (stop orada tetiklenir).
- **Okumalar tabloya alındı — sahip onayı bekleniyor.** P2 uygulama notu 14'teki okumalar tanımın parçasıdır: `NOISE_LOSS`
  = başka kayıp kodu yok ve |r_gross| ≤ cost_R (harfiyen "|net_R| maliyet bandında" yalnız r_gross = 0'da oluşabilirdi);
  `GIVEBACK`'in capture < 0,3 kolu yalnız MFE ≥ 0,3R iken; `LATE_ENTRY` yukarıdaki gibi. Sahip onaylamazsa değişiklik
  `attribution_v2` + yeni deneme sayımıdır.

| Kayıp kodu (2026-10-08) | Kural (özet) |
|---|---|
| `GAP_FILL` | `exit_basis = GAP` (bar açılışı boşluğu ya da seviye ötesi dolum ≥ 0,25R); birincil öncelikte geç girişin ardında |
| `LATE_ENTRY` | dolum, sinyal kapanışının > 0,3 ATR ötesinde; veya MFE ≥ 0,3R ve MFE'den KESİN önceki gözlemlerde MAE_R < −0,7 |
| `GIVEBACK` | MFE_R ≥ 1 ve net_R ≤ 0; veya MFE_R ≥ 0,3 ve capture < 0,3 |
| `NOISE_LOSS` | başka kayıp kodu yok ve \|r_gross\| ≤ cost_R |

### 5.5 "Nasıl kâra dönebilirdi": karşı-olgusal ızgara (`cfgrid_v1`, mühürlü)

Bir taktiğin her işlemi **aynı** ızgarayla oynatılır, böylece karşılaştırmalar eşli (paired) olur ve seçilmiş
(cherry-picked) olmaz. Her varyant aynı gerçek yol, aynı `ExecModel` ve aynı `_net_replay` ile oynatılır. USDT riski
(`features.risk_usdt`) sabit tutulur; büyüklük stop mesafesine uyarlanır. Yaklaşık 24 varyant:

| Eksen | Varyantlar | Etiket |
|---|---|---|
| Giriş | gerçek; 1 bar gecikmeli; −0,25 ATR limit (yalnız dokunursa dolum; dolmazsa R=0, "kaçan" sayılır) | EX_ANTE |
| Stop | {0,75; 1; 1,5; 2} × ATR14 | EX_ANTE |
| Hedef | 1R, 2R, 3R, ATR iz süren 2×, hedefsiz + zaman stopu | EX_ANTE |
| Yönetim | TP1 açık/kapalı; 1R'da başabaş açık/kapalı | EX_ANTE |
| Büyüklük | yarı kaldıraç (R aynı; likidasyon/marj etkisi denetlenir) | EX_ANTE |
| Atla | r = 0 | EX_ANTE |
| İşlem başına en iyi hücre | `hindsight_best` | **HINDSIGHT**; asla tek başına ders olmaz |

- **EX_ANTE**, kuralı yalnız giriş anında bilinen bilgiyle ve sabit bir ızgara noktasıyla tanımlanan varyanttır.
  **Yalnız EX_ANTE varyantlar derse dönüşebilir.**
- Ayrıştırma (`_decompose`: yol / dolum / ücret / fonlama payları) varyant başına tutulur. Sıralamada her zaman
  `cf_aux_v1` muhafazakâr R kullanılır.
- Referans olarak taktiğin ızgara **medyanı** kullanılır, maksimumu değil. "En iyi olası" sonuç asla ölçü olmaz.
- **Eşleşmiş rastgele giriş kontrolü:** aynı sembol, aynı saat dilimi ve aynı durum kovasında tarihten alınan 20
  rastgele giriş, aynı stop/çıkış ile oynatılır. `signal_excess_R` = gerçek − kontrol medyanı. Bu, "sinyalin bu durumda
  avantajı var mıydı?" sorusunu cevaplar.

#### Değişiklik (2026-10-08, inceleme düzeltmesi; gerçek veriye uygulanmadan önce)

`cfgrid_v1` de yalnız sentetik altın yollarla mühürlenmiş, gerçek veriye hiç uygulanmamıştı. 2026-10-07 incelemesinin
bulgularıyla (M1, M3, B1, küçük "ufuk") aynı sürüm adıyla yeniden mühürlendi; eski metin yukarıda tarih olarak kalır.

- **Mühür:** `CFGRID_SHA` `4a96df71a989bcf9c5f81317c01e534920105ced1785dc9389240502abc9454d` →
  `7c299ae001aee643a76c697ee191026ba6cdb842e21c377d48ed86e0842adf68` (`tests/test_research_engine_cfgrid.py`'de sabit).
- **Sıralama aynı ölçüyle (M3).** Bir işlemin hücreleri TEK ölçüyle sıralanır: sıralamaya giren her hücrenin `cf_aux_v1`
  muhafazakâr R'si varsa o, yoksa HEPSİ için `r_net` (`rank_src`); aynı işlemde iki ölçü karışmaz. Defter görünümü (en iyi
  sabit hücre) yalnız `cf_aux_v1` ile sıralanmış işlemleri kullanır (diğerleri sayılır, karışmaz).
- **Kaldıraç önerisi yok (M3, §7.6).** "Yarı kaldıraç" hücresi (boyut ekseni) ızgarada ve `size_lev_R`'de kalır ama
  sıralamaya, işlem başına en iyi hücreye (HINDSIGHT), "kâra dönen hücrelere" ve defter görünümüne GİRMEZ; kaldıraç
  değişikliği hiçbir metinde "nasıl kâra dönebilirdi" olarak sunulmaz (kaldıraç HİPOTETİK).
- **Defter görünümü dürüst (M3).** En iyi EX_ANTE sabit hücre yalnız n ≥ 30 ve ≥ 10 farklı gün olan hücreler arasından
  (§5.7 uygunluk eşiği) ve yalnız ortalama eşli fark > 0 ise gösterilir; metin bunun "k sabit hücre arasından SEÇİM"
  olduğunu (seçim yanlılığı; tek başına ders değil) yazar. Hiçbiri iyileştirmiyorsa "iyileştiren hücre yok" yazılır.
- **Ufuk ex ante değildir (küçük; açıklama).** Hücreler giriş/stop/hedef/yönetim KURALI bakımından EX_ANTE'dir, ufuk
  bakımından değil: kural/zaman çıkışında gerçek çıkış anı, seviye çıkışında yol sonu (gerçek kapanış + 48 bar) ∩ tutma
  sınırı. Giriş diliminden kısa işlemlerde `E_DELAY1` `NO_BARS`'tır; sayılar `attribution/_build.json`'da
  (`cells_not_ok`) ve özette görünür.
- **Izgaraya giriş (B1).** Fidelity kaydın gözlemden stop dolumunu modeller (§5.3 notu); örnekleme aşması bir işlemi
  ızgaradan düşürmez. Taban hücre (`E_ACTUAL`) seviyeden dolar; aşma `cf_aux_v1` muhafazakâr R'sinde ve `baseline_gap_r`'de
  görünür.

### 5.6 R ayrıştırması (sıralı zincir, artık her zaman gösterilir)

`net_R = signal_R + timing_R + stop_R + exit_R + size_lev_R + cost_R + artık`

| Bileşen | Ne ölçer |
|---|---|
| `cost_R` | ücret + kayma + fonlama (ölçülen) |
| `signal_R` | nötr yürütmenin brüt R'ı − eşleşmiş rastgele giriş medyanı |
| `timing_R` | gerçek giriş − ızgara medyan girişi (stop ve çıkış sabit) |
| `stop_R` | gerçek stop − ızgara medyan stopu |
| `exit_R` | gerçek çıkış − ızgara medyan çıkışı |
| `size_lev_R` | yalnız likidasyon, marj küçültme veya min-notional yükseltme etkisi |

Kalan fark `artık` olarak her zaman gösterilir, gizlenmez.

Ayrıca `regime_fit` hesaplanır: işlem **açılmadan önce** mevcut derslerden (`as_of < opened_at`) taktik × durum
kovasının beklenen R'ı. Bu, "doğru taktik, yanlış rejim" kaybını şanssızlıktan ayırır.

#### Değişiklik (2026-10-08, inceleme düzeltmesi; gerçek veriye uygulanmadan önce)

P2b'nin "sıralı zinciri" (zamanlama = medyan(15 hücre) − medyan(17), stop = medyan(11) − medyan(15), …) eksen etkilerini
iki medyanın farkına indiriyordu ve harfiyen tanıma göre ~10 kat küçük çıkıyordu (inceleme M1: ortalama |zamanlama| 0,022
yerine 0,197). Yukarıdaki tablo HARFİYEN uygulanır (`cfgrid.decompose`; `cfgrid_v1` mührüne dahil):

| Bileşen | Hesap (G = kayma öncesi brüt R, dolum riskine göre) |
|---|---|
| `signal_R` | X1 − B; X1 = giriş/stop/çıkış/yönetim eksenlerinin bütün EX_ANTE hücrelerinin G medyanı ("nötr yürütme", 1x; boyut ve atla hariç), B = eşleşmiş rastgele giriş medyanı |
| `timing_R` | G(gerçek) − giriş ekseninin ızgara medyanı (gerçek, 1 bar gecikmeli, −0,25 ATR limit) |
| `stop_R` | G(gerçek) − stop ekseninin ızgara medyanı (gerçek, 0,75/1/1,5/2 × ATR14) |
| `exit_R` | G(gerçek) − çıkış ekseninin ızgara medyanı (gerçek, 1R/2R/3R, 2×ATR iz süren, hedefsiz + zaman) |
| `size_lev_R` | G(gerçek kaldıraç/tutar) − G(gerçek, 1x) |
| `cost_R` | net_R − G(gerçek kayıt) (ölçülen) |

Artık = net_R − Σ bileşenler, her zaman gösterilir; bütün girdiler varken artık = B + (G_gerçek − G_kaldıraçlı) +
(Me + Ms + Mx − X1 − 2·G(gerçek)) — rastgele taban, yeniden oynatma farkı ve eksen medyanlarının toplanamazlığı
(`residual_parts`). Eksik bileşen 0 sayılmaz (artığa kalır).

### 5.7 Ders istatistikleri

- **Hücreler:** (defter, taktik, varyasyon, enstrüman veya sınıf, durum kovası). Veri olan en kaba geri çekilme düzeyi
  kullanılır (shared_experience S0..S5 düzeni); çaprazlar en fazla 2 derinlikte tutulur.
- **Hücre başına hesaplananlar:**
  - n ve farklı gün sayısı;
  - ortalama net R;
  - Wilson aralıklı kazanma oranı;
  - ebeveyn hücreye doğru `HierarchicalRate` büzülmesi (alpha 10);
  - gün kümelenmeli bootstrap CI95 (sabit tohum, 5000 yeniden örnekleme);
  - her EX_ANTE varyant için gerçeğe göre **eşli** delta R ve kendi gün kümelenmeli CI'ı;
  - §6.4'teki tek istatistik `p_day` (tek yönlü, gün kümelenmeli bootstrap).
- **Ders türleri:**
  1. IZGARA: "T taktiği için V varyantı ortalama net R'ı Δ artırır".
  2. FİLTRE: "X kovasında T'yi atla"; eşsizdir, Δ = −Σ R.
  3. MALİYET: maker girişi, fonlamadan kaçınma.
  4. AYRIM: kazananı kaybedenden ayıran giriş özellikleri. Yalnız walk-forward OOS AUC CI > 0,5 ise raporlanır.
- **Gerçek ve CF kanıtı ayrı tutulur.** CF, `cf_aux_v1` `r_net_conservative` kullanır; ağırlığı gösterilir, sessizce
  birleştirilmez.
- **Kohort ve config dönemi.** Dersler yalnız aynı config dönemi içindeki işlemlerden hesaplanır veya dönem bir tabaka
  olarak açıkça modele girer (§6.2); politika (POLICY) ve öğrenme-ekstra kohortları karıştırılmaz.
- **Uygunluk:** n ≥ 30 ve ≥ 10 farklı gün.
- **Çoklu test:** BH-FDR aile tanımı (hangi hücreler × varyantlar tek bakışta test edilir) mühürlü kayıtta **önceden**
  sabitlenir; q = 0,10. Kümülatif deneme sayısı `trials.jsonl`'dan raporlanır.
- **İsteğe bağlı durma kontrolü.** Dersler her gece durum **değiştirmez**. Gece değerleri "ara görünüm" olarak gösterilir.
  - Durum değişikliği yalnız **önceden kayıtlı bakışlarda** olur: hücrenin n'i 30, 60, 120 ve 240'ı geçtiğinde.
  - Alfa bakışlara bölünür: 0,05 toplam = 0,01 / 0,01 / 0,015 / 0,015.
  - Her bakış `trials.jsonl`'a bir satır olarak yazılır.
- **İleri doğrulama.** Bir bulgu T_k anında hash'lenip `RESEARCH_HYPOTHESIS` olarak kaydedilir. Doğrulama **yalnız T_k'dan
  sonra kapanan** işlemlerle yapılır. Gereken: aynı işaret, doğrulama bakışında `p_day` ≤ o bakışın alfası, ve ≥ 3 aylık
  katmanın ≥ 2/3'ünde işaret tutarlılığı.

### 5.8 Ders deposu ve yaşam döngüsü

- `learn/lesson_store` kullanılır (`lesson_v2` şeması, `build_lesson`, `transition()`); segment arşivi için
  `learn/journal_archive.SegmentArchive` kullanılır (onu zaten `pattern_trader/book.py:1317` çağırıyor). `build_lesson` ve
  `transition` için motor ilk gerçek çağıran olur. Kök ayrıdır (`data/research/lessons`); `state/lesson_archive`'a
  dokunulmaz.
- Bağlam anahtarları genişletilir: `B|` defter, `I|` enstrüman, `A|` sınıf (COIN/GOLD), `T|` taktik, `V|` varyasyon,
  `C|` atıf kodu, `X|` durum kovası, `E|` config dönemi.
- Ders alanları:
  - `lesson_id`, `scope{}`, `observation_tr` (şablondan üretilir, LLM yok), `mechanism_code`;
  - `evidence{n, n_days, mean_r, ci95, p_day, effect_delta_r, q_bh, look_no, discovery_window, validation_window,
    data_seal, registry_sha, config_epoch}`;
  - karşı kanıt sayısı, inceleme için 3 en iyi + 3 en kötü `trade_key`, `status_history[]` (yalnız eklenir),
    `linked_candidate_id`.
- Durumlar:
  - `OBSERVATION`: uygunluk eşiğini geçmiş bulgu.
  - `RESEARCH_HYPOTHESIS`: keşif bakışında anlamlı; hash'lenmiş.
  - `VALIDATED_POLICY_CANDIDATE`: ileri doğrulama geçti **ve**, kural değişikliği gerektiriyorsa, ondan türeyen aday
    Kapı B'yi geçti.
  - `APPLIED_BOUNDED`: **yalnız** sahibin `engine-query --approve <id> --operator berke` komutuyla ve ardından normal bir
    sürümle.
  - `REJECTED` / `RETIRED`: sonraki bir bakışta işaret döndü, ya da veri veya atıf sürümü geçersizleşti.
- Gece çalıştırıcısı (runner) `APPLIED_BOUNDED` yazamaz; bunun için bir test vardır.

### 5.9 Dersten önceden kayıtlı aday kurala

Tek otomatik fikir üreticisi budur. Doğrulanmış (`VALIDATED`) bir derse **önceden kayıtlı dönüşüm listesinden** biri
uygulanır:
- hücre filtresini ekle;
- stop tabanını değiştir;
- çıkış varyantını değiştir;
- bir durum kovasını hariç tut.

Ortaya çıkan aday spec mühürlenir. Spec şunları sabitler:
- hedef defter/taktik ve tam parametre farkı;
- ölçüt (işlem başına net R **ve** gün başına özsermaye %'si);
- en az ileri n ve süre;
- alfa ve bakış takvimi;
- durdurma kuralı.

Her spec yeni bir kütüphane sürümü ve yeni bir `trial_id`'dir. Aday, değişmemiş orijinalin yanında kayıt-yalnız ileri
yeniden oynatmayla test edilir (§6.6). Zaten canlı olan bir kural (örn. Box en az stop %0,5, `record_selectivity`)
aday olarak üretilmez; dönüşüm listesi canlı config'in ham anahtarlarına karşı denetlenir. Hiçbir şey otomatik
uygulanmaz.

### 5.10 Akıl sağlığı testleri (geçmiş kanıtın config dönemine bölünerek yeniden üretilmesi)

Bunlar **aday değil**, tarihsel yeniden üretimdir. Her biri config dönemine (§6.2) bölünerek raporlanır; dönem
sınırını aşan bir karşılaştırma yapılmaz.

- **Box, dönem `min_stop_pct=0,32` (2026-10-03 öncesi):** stop < %0,5 için 72 işlem −0,56R; %0,5–1 için 176 işlem
  +0,17R yeniden üretilmeli. Beklenen ders: "Box: stop < %0,5 kaybettiriyordu (STOP_TOO_TIGHT /
  TIGHT_STOP_COST_MULTIPLIER)". Bu ders zaten sahip kararıyla uygulanmıştır (`books.b1_box_fade.min_stop_pct: 0.5`,
  2026-10-03); motor bunu aday üretmez, yalnız "uygulanmış karar, ileri izleme" olarak yeni dönemi izler.
- **M2 kapasite ekstraları** +0,15R (öğrenme-ekstra kohortu, dönem başına).
- **Seçicilik ekstraları** negatif (2026-10-03 öncesi dönem); bu, sonradan canlıya alınmış `learning_mode.extra_entries:
  record_selectivity` kararını doğrular. Yeni dönemde seçicilik ekstraları açılmaz, yalnız kaydedilir; motor bunları
  karşı-olgusal kayıtlardan izler.

---

## 6. Sürekli araştırma döngüsü

### 6.1 Gece aşamaları (`engine-night`, 01:37–03:40 UTC, ağsız, AI yok)

| Aşama | İş | İlk aşama |
|---|---|---|
| S0 ön kontrol | PAPER doğrulaması (`state/mode.json`); `analysis.lock`; disk; **çalışma zamanı yalıtım öz-denetimi** (§2.8; tutmazsa `ISOLATION_BROKEN` ile dur); **SKEW** denetimi (tutmazsa yalnız S0/S1a/S7); yedek birimi aktif mi (§2.2); A/B takvimi (`AB_OFF`, §2.9); P1b'den `data_status` tazeliği (eskiyse yalnız S1, S3 ve S7 çalışır, diğerleri `DATA_STALE`); canlı config sha'sı | P1a |
| S1a kapanış + hareket arşivi + anlık görüntü | vadeli **ve spot** ledger'lardan yeni ve revize kapanışlar; `entries[]` arşivi; gecelik ölçülmüş anlık görüntü; uzlaştırma; rotasyon payı; geri yükleme tespiti | P1a |
| S1b günlük + yol + rehydrate + fidelity | `tj_v1` | P2 |
| S2 atıf | kodlar + `cfgrid_v1` + ayrıştırma | P2 |
| S3 günlük hedef | yeni günün **GEÇİCİ** satırı; 3+ günlük satırların kesinleşmesi veya `REVİZE`; yalnız kayıtlı bakış gecelerinde hükümler | P1a (gerçekleşmiş + `LEDGER_MARK` MTM), P2 (00:00 UTC MTM) |
| S4 walk-forward + keşif | tüm varyasyonlar için artımlı son katman + 7 parçadan birinin tam yeniden hesabı (kütüphane haftada bir tam taranır); **keşif katmanının** yeni kapanmış barlarda adımı (§6.8) | P3 |
| S5 ileri adaylar | her aktif aday yeni kapanmış barlarda adım atar | P4 |
| S6 dersler, denemeler, kapı | önceden kayıtlı bakışlar, BH, durum geçişleri, öneriler | P3/P4 |
| S7 özet | `digest_tr.md`, `engine_summary.json`, `run_status.json` (aşama süreleri, CPU, tepe bellek, iki SHA, config sha, `data_seal`) | P1a |
| S7b araştırma yedeği | küçük alt ağaçların günlük sıkıştırılmış yedeği ve doğrulaması (§3.6) | P1a |

Her aşama son tarihe bakar ve iş kuyruğuna kontrol noktası yazar; biten iş kalırsa ertesi gece devam eder.
Shared-experience `--summary-out` raporları motor biriminden **çalıştırılmaz**, çünkü onlar `state/` altına yazar. İstenirse
çıktı yolu `research/summary`'ye yönlendirilerek ayrı bir değişiklikle ele alınır.

### 6.2 Strateji kütüphanesi (`LIB_v1`, mühürlü)

Her taktik saf bir fonksiyondur: `signals(bars, funding, metrics, ctx) → {signal_ts, side, entry, stop, targets,
exit_rules, max_hold}`. Parametre ızgarası sınırlıdır (sürüm başına aile başına ≤ 8–12 varyant).

| Kaynak | İçerik |
|---|---|
| Mevcut defterler (birebir kopya) | T2 EMA200 trend, M2 TSMOM28 (14/28/56), Box fade (stop genişliği kovaları), D4 Donchian 20/10 (+55/20, ATR çıkışları), C4/C4S CV001–CV008, Formasyon aileleri (belleğe sığarsa; yoksa yalnız ileri). Ana bot topluluğu ucuza yeniden oynatılamaz; yalnız canlı ölçülür. |
| Laboratuvarlar | `signal_lab` kuralları, `futures_lab` fut_v1, `crowd_lab` fut_v2, `gold_v1` hücreleri, `gold_v2` aile A (günlük/4h trend; sonuçlar yayımlanınca), `book_lab` D4/C4/Formasyon varyantları. Yalnız yayımlanmış kayıtları alınır; mühürlü bir laboratuvar aynı mühürle yeniden **çalıştırılmaz**. **gold_v2 aile B (PAXG hafta sonu dönüşü)** "kanıt yok" yayımlandı (wt-gold2 `db1828e`); sonuçları öncül deneme olarak içe alınır, aday listesine konmaz; yeniden girişi yalnız yeni bir LIB sürümü ve Kapı A üzerinden olur. |
| Klasik aileler | çok ufuklu TSMOM (7/14/28/56/84 g, oynaklık hedefli); Donchian 20/55; EMA/SMA kesişimi; Keltner/ATR kırılımı; BB-genişliği sıkışma kırılımı; RSI2/Bollinger aralık dönüşü (yalnız düşük ER20 rejiminde); aşırı fonlama fade/carry; OI–fiyat uyumsuzluğu; BTC–ETH spread z-skoru |
| Kesitsel / portföy (XSEC) | kesitsel momentum (40 coin, 7/28 g sıralama, üst-k long / alt-k short, haftalık, BTC-beta nötr: "toplam" hedefi için portföy taktiği); kesitsel 1 günlük dönüş. **Zaman noktasında evren (P3, §3.3) kurulana kadar Kapı A'ya uygun değildir**; keşif katmanında "HAYATTA KALAN EVREN — kapıya kapalı" etiketiyle izlenir. |
| Altına özgü | XAUUSDT Londra/NY açılış kırılımı, Asya kayması; PAXG pazar açılış boşluğu; ay sonu; altın günlük trend; altın/BTC oran trendi |
| Örtüler (bir seferde bir faktör) | ATR iz süren, chandelier, zaman stopu, TP merdiveni; büyüklük: sabit %0,5 risk (`learning_mode.fit_size`), oynaklık hedefi; kaldıraç büyüklükten türetilir, likidasyon mesafesi ≥ 2 × stop |
| Dersten türeyen | §5.9 |
| Yeni fikirler | AI veya sahip, `library/` altına kayıt girdisi ekleyen bir **kod PR'ı** açar; yeni fikirler **ayda bir LIB sürümünde toplanır** (§8); mühür ve deneme sayımı otomatik artar |

**Config dönemleri.** `library/config_epochs.json` (mühürlü), canlı defterlerin parametrelerinin geçerli olduğu
aralıkları tutar. Kaynaklar: dağıtılmış her sürüm SHA'sındaki `config.yaml`'ın git geçmişi (`deploy/releases/` listesi)
ve belgelenmiş sahip kararları. Örnek sınırlar: 2026-09-30 Box `slots` 20→40; 2026-10-03 Box `min_stop_pct` 0,32→0,5 ve
`learning_mode.extra_entries: record_selectivity`. Her gece `rawconfig` canlı config sha'sını son dönemle karşılaştırır;
yeni bir sha görülürse yeni dönem açılır ve özette görünür.

**Kopya paritesi (kohort ve dönem başına).** Canlı defterler gerçekten işlem açan öğrenme-modu kapasite ekstraları
içerir; oysa `strategy_paper.apply_action` yeniden oynatmada öğrenmeye her zaman `None` geçer (strategy_paper.py:637).
Politika da tarihin ortasında değişti. Bu yüzden:
- parite **yalnız POLICY kohortu** girişleri üzerinde ölçülür (öğrenme-ekstra, CAPACITY ve SELECTIVITY kohortları
  dışarıda);
- parite **her config dönemi için ayrı**, o dönemin parametreleriyle ölçülür;
- kapı: ≥ 20 POLICY girişi olan **her** dönemde, canlı girişlerin ≥ %95'i yeniden üretilmeli (aynı bar, aynı yön) ve
  eşleşen işlemlerde medyan |ΔR| ≤ 0,05 olmalı; < 20 girişli dönemler raporlanır ama kapıya girmez;
- slot/teminat dolu olduğu için açılamayan politika girişleri ayrıca sayılır ve pariteden önce açıklanır.
Bir defter kopyası lider tablosuna veya atıf tabanına ancak bu koşulla girer. Kopyalar `strategy_paper.apply_action` ile
oynatılır; yürütme canlı defterlerle aynıdır.

### 6.3 Walk-forward protokolü (`PROMOTION_REGISTRY`'de mühürlü)

| Konu | Kural |
|---|---|
| Katmanlar | `quant/walkforward.make_folds(validation_days > 0)` ile üç yollu düzen ve `run_three_way` (walkforward.py:320) sözleşmesi; çapalı ve kayan. ≥ 1h taktikler: eğitim ≥ 365 g, test 90 g. 5m/15m taktikler: eğitim 120 g, doğrulama 30 g, test 14 g. Altın vadelileri: eğitim ≥ 180 g; uzun vekil olarak PAXG spot 2020+ ve Dukascopy (gold_v2 ön kaydına uygun: 2006–2020 keşif, 2020→ doğrulama) |
| Arındırma | purge/embargo = sinyal tf'sinin 1 barı + `max_hold` |
| Kayan "kilitli bekletme" yok | Önceki taslaktaki "son 90 gün, bir kez açılır" kuralı kaldırıldı: pencere kaydığı ve Kapı A'ya ulaşan her spec onu okuduğu için tek kullanımlık değildi. Yerine **mühür sonrası ileri veri** kullanılır: bir spec'in mühür anından (`T_seal`) sonra kapanan barlar, hiçbir spec seçiminde ve parametre aramasında görülmemiş tek veridir; bu veri keşif katmanında birikir (§6.8) ve Kapı A'nın tersine dönme denetimi olarak okunur (§6.7). Her okuma `trials.jsonl`'a yazılır. |
| Parametre seçimi | yalnız eğitimde, ADVERSE maliyette en yüksek ortalama net R'a göre; sonra doğrulama ve test için dondurulur |
| Maliyet | base = taker %0,05 × 2 + 3 bps kayma + gerçek fonlama (gerçek uzlaşma zaman damgalarında); adverse = 2× kayma + 1 tick spread; stress = 3× kayma + %0,02 ek ücret |
| Sızıntı | `leakage_check` geçmezse parça atılır (`LEAK`) |
| Önbellek | sonuçlar (spec_sha, data_seal_month) ile anahtarlanır; her gece yalnız yeni katman hesaplanır |
| Kaynak | `--jobs 1` (nproc ≥ 4 ise 2); bir seferde tek enstrüman × tf; < 1 GB |

### 6.4 Çoklu test, tek istatistik ve deneme defteri

- **Tek istatistik `p_day`.** Kapı A, Kapı B, Kapı C ve ders bakışları aynı istatistiği kullanır: H0 "ortalama net R
  ≤ 0" için, **ADVERSE** maliyetle, **gün kümelenmeli** (günler yeniden örneklenir) tek yönlü bootstrap p-değeri (sabit
  tohum, 10.000 yeniden örnekleme, sıfır-ortalamaya kaydırılmış dağılım). CI'lar yalnız gösterim içindir; kapılar
  `p_day` ve bakış alfasıyla karar verir.
- **`trials.jsonl`** yalnız eklenir, asla silinmez. Laboratuvarlar ve motor tarafından değerlendirilen her
  spec × varyant × enstrüman × katman için ve **her bakış** için bir satır tutulur: `registry_sha`, `data_seal`,
  `code_sha`, parametreler, pencere, n, ortalama R, `p_day`, hüküm, `look_no`, `cumulative_N`, ileri veri okuması
  (`fwd_read`) varsa onun kaydı.
- **Geçmiş laboratuvar sonuçları** öncül deneme olarak içe alınır: `gold_v1` 0/32, crowd `fut_v2` 0/8, `book_v1`,
  `gold_v2` (aile B 0/8 dahil). Böylece N'e sayılırlar ve körlemesine yeniden çalıştırılmazlar.
- **Deflated Sharpe:** mevcut `tradingbot/validation.py` `deflated_sharpe` (satır 252) ve `probabilistic_sharpe`
  **yeniden kullanılır**. N, aile sayısı **değil**, `trials.jsonl`'daki **kümülatif varyant denemesi sayısıdır** (ham N;
  başlık bunu kullanır). Korelasyon kümelemesiyle belgelenmiş bir etkin N (günlük getirilerin |ρ| > 0,7 hiyerarşik
  kümelemesi; yöntem mühürlü) yalnız ikincil sütun olarak gösterilir ve hiçbir kapıda kullanılmaz.
- **Kayıtlı bakışlar.** Kapı A her gece yeniden değerlendirilmez (bu, büyüyen veride isteğe bağlı durma olurdu). Kapı A
  yalnız **ayda bir**, her ayın 5'inden sonraki ilk gecede (aylık arşivler yayımlandıktan sonra) değerlendirilir; her
  değerlendirme `trials.jsonl`'a `look_no` ile yazılır. Arada gece değerleri yalnız "ara görünüm"dür.
- **PBO henüz yok.** `quant/walkforward.fold_report` her zaman `pbo=None` döndürür (`pbo_state =
  "requires_candidate_matrix"` / `"not_computable"`). Varyant × katman matrisi üzerinde CSCV (16 blok) **yeni yazılır**
  ve önce cevabı bilinen sentetik veride test edilir. O zamana kadar PBO "hesaplanamaz" sayılır ve PBO gerektiren her
  kapı **kapalı kalır** (fail-closed).
- Ek denetimler:
  - BH-FDR (q = 0,10), aile = o bakışta değerlendirilen bütün spec × enstrüman satırları; aile tanımı kayıtta sabit;
  - plasebo: zaman kaydırılmış veya işaret çevrilmiş sinyaller, aynı tutma süresiyle, geçmemeli;
  - haftalık White/Hansen SPA, "hiçbir şey yapma" ve "BTC/XAU al-tut" ölçütlerine karşı (P3 sonu, isteğe bağlı).

### 6.5 Lider tablosu

Ham getiriyle değil DSR ile sıralanır. Her varyant × enstrüman ve varyant portföyü için şunlar gösterilir:
- OOS n;
- ortalama net R, gün kümelenmeli CI ve `p_day`;
- katmanların pozitif payı;
- PBO (veya "hesaplanamaz");
- plasebo farkı;
- maliyet stresi sonucu;
- en büyük düşüş;
- günlük ortalama % (%0,5 riskte);
- P(gün ≥ +%1);
- k* ve iflas olasılığı (§7.6; HİPOTETİK etiketiyle, **yalnız burada ve önerilerde**);
- mevcut defterlerle korelasyon;
- mühür sonrası ileri sonuç (keşif katmanından, §6.8).

Hayatta kalma yanlılığı uyarısı ("hayatta kalan evren") her satırda durur; XSEC satırları zaman noktasında evren
kurulana kadar "kapıya kapalı" yazar. Lider tablosunun ayrı bir **KEŞİF** bölümü vardır (§6.8).

### 6.6 Kayıt-yalnız ileri yeniden oynatma adayları (P4)

- **Adı ve etiketi.** Bunlar canlı kâğıt işlem **değildir**: motor, depolanmış barlar üzerinde gece gece adım atar ve
  dolumları modeller. Akış adı `PROSPECTIVE_REPLAY`, Türkçe etiketi "ileri yeniden oynatma (örneklem dışı) — gerçek
  defter değil"dir.
- Kapı A'yı geçen her varyant veya dersten türeyen aday, kendi atılabilir kâğıt defterini alır:
  `research/prospective/<id>/futures_ledger.json`. Defter worker'la aynı ücret/kayma/fonlama/likidasyon modelini
  kullanır.
- Kurallar, adım atılacak barlar oluşmadan **önce** mühürlenir. Her gece, son çalıştırmadan beri kapanmış barlarda adım
  atılır; kararlar yalnız `close_time < adım zamanı` olan barları kullanır, dolum sonraki barın açılışında olur. Bu
  gerçek bir örneklem dışı testtir ve 1 günlük gecikmesi kaydedilir.
- **Gerçekleşen maliyet = modellenen maliyet** (yapı gereği). Bu yüzden "gerçekleşen/modellenen maliyet oranı" bir kanıt
  değildir ve kapıda kullanılmaz; yerine sonuç ADVERSE ve STRESS maliyet senaryolarında ileri pencerede ayakta kalmalıdır
  (§6.7 Kapı B).
- En fazla 20–40 aktif aday olur; gecede < 5 dk sürer.
- XAUUSDT/PAXGUSDT adayları, keşif katmanından sonra altının ikinci kayıt-yalnız kaydını verir.
- Aday sonuçları canlı defter toplamına **asla** eklenmez. Panel ve özet onları "ileri yeniden oynatma — gerçek defter
  değil" etiketiyle gösterir.
- Önceden kayıtlı durdurma (yalnız sabit bakışlarda): ≥ 30 işlemden sonra ortalama R < −0,10; kayan 60 günlük ortalama
  R CI üst sınırı < 0; düşüş > 2 × geriye-test p95 düşüşü; günlük net R üzerinde CUSUM alarmı (h = 4σ).
- **İsteğe bağlı saatlik adım (sonraki aşama).** Gece yolu VPS'te kararlı hale gelince, 5m taktiklerin gecikmesini
  azaltmak için ayrı bir saatlik birim değerlendirilir. Bu birim ağsız olur ve `analysis.lock`'u gece birimiyle paylaşır
  (§2.4). Bellek toplamı P0 kuralına göre yeniden hesaplanır.

### 6.7 Terfi kapısı (`PROMOTION_REGISTRY`, ilk veri çalıştırmasından **önce** sha testte ve bu belgede sabitlenir)

**Kapı A — çevrimdışı; yalnız aylık kayıtlı bakışta.** Hepsi gerekli:
- walk-forward OOS'ta `p_day` (ADVERSE) o bakışın BH ailesinde anlamlı (q = 0,10);
- ≥ 100 OOS işlem ve ≥ 2 takvim yılı (altın vadelileri: ≥ 60 işlem ve ≥ 9 ay, `KISA_GEÇMİŞ` işaretli);
- katmanların ≥ %60'ı pozitif;
- PBO < 0,25 (hesaplanamazsa geçmez);
- DSR olasılığı ≥ 0,95, N = kümülatif ham varyant denemesi sayısı (§6.4);
- plasebo farkı CI alt sınırı > 0;
- **mühür sonrası ileri veri** (keşif katmanı): ≥ 60 gün (5m/15m taktikler: ≥ 30 gün) ve ≥ 20 işlem; ADVERSE
  maliyette ortalama net R ≥ 0. Bu bir tersine dönme denetimidir, anlamlılık testi değildir; okuması `trials.jsonl`'a
  yazılır;
- tek bir enstrüman kârın en fazla %50'si olabilir (tasarım gereği tek enstrümanlı taktikler hariç; altında XAU ve PAXG
  arasında tutarlılık aranır);
- XSEC ailesi değilse veya zaman noktasında evren kurulmuşsa;
- canlı bir defterin kopyası veya türevi ise kıyas aynı config dönemi içinde yapılır.

Geçerse bir kayıt-yalnız ileri yeniden oynatma adayı doğar. Bu otomatiktir, çünkü yalnız motorun kendi defterine yazar.

**Kapı B — ileri, kayıt-yalnız; yalnız kayıtlı bakışlarda.** Bakışlar: 50, 100 ve 150 kapanmış işlem (yavaş taktikler
ve altın: 30, 60, 90), alfa 0,01 / 0,015 / 0,025 (toplam 0,05). Bir bakışta hepsi gerekli:
- en az süre: ≥ 28 gün (yavaş taktikler: ≥ 90 gün; altın: ≥ 60 gün);
- ileri pencerede `p_day` (ADVERSE) ≤ bakışın alfası (yani pozitifliğin kanıtı; eski "CI alt sınırı > −0,05R" kuralı
  kaldırıldı);
- ileri pencerede STRESS maliyetiyle ortalama net R > 0 (maliyet senaryolarında ayakta kalma);
- ortalama net R, Kapı A OOS CI'ının içinde (sapma denetimi);
- düşüş ≤ 1,5 × geriye-test p95 düşüşü ve ≤ aday özsermayesinin %8'i;
- likidasyon yok;
- nedensellik (zaman yolculuğu) testi geçer;
- `DATA_STALE` günleri ≤ %10.

**Kapı C — öneri.**
- Öneriler ayda bir topluca hazırlanır: her ayın ilk UTC pazartesisi. Aile = o ay herhangi bir Kapı B bakışı olan
  **bütün** adaylar (yalnız geçenler değil); her birinin son bakıştaki `p_day`'ine Holm (aile genelinde α = 0,05)
  uygulanır. Öneri için hem Kapı B hem Holm geçmelidir.
- Her öneri için `proposals/<id>.json` ve `.tr.md` yazılır. İçerik: kanıt paketi, mühür sonrası ileri sonuç, tam
  kod/config farkı, işlem başı risk (başlangıç %0,25), kaldıraç tavanı, beklenen günlük % katkısı ve CI'ı, k* tablosu,
  önceden kararlaştırılmış durdurma kuralı, geri alma adımları.
- Boş bir ay için dürüstçe "öneri yok" yazılır.
- **Sahip karar verir.** Onay `engine-query --approve <id> --operator berke` ile kaydedilir (`approvals/approvals.jsonl`).
  Onay tek başına canlıda hiçbir şeyi değiştirmez; ayrı bir normal sürüm hazırlanır.
- Tercih edilen biçim **yeni bir kâğıt defterdir** (`strategy_paper_<id>`). Mevcut bir defterin değişmesi ayrı ve açık bir
  onay gerektirir.

**Değişiklik (2026-10-06, sahip kararı: "Otomatik, yalnız PAPER"):** Sahip, motorun her coin için kendi taktiğini
bulup geliştirmesini ve kâr etmeye çalışmasını istiyor. Kapı A + Kapı B + Holm'u geçen aday, sahibe sorulmadan **yeni
bir PAPER kâğıt defteri** olarak açılabilir (gerçek para asla; mevcut defterler değişmez; işlem başı risk %0,25 ile
başlar; önceden kayıtlı durdurma kuralı ve "SAPMA" izlemesiyle defter kendini kapatır). Sahip her açılışı/kapanışı
özetten ve `--check`'ten görür ve tek komutla kapatabilir. Bu otomasyonun nasıl kurulacağı (worker'ın motor terfilerini
okuyan ayrı, kapılı PAPER defter yolu; sürüm ve geri alma) P3–P5 tasarımında ayrıca ve ön kayıtla yazılacak; o yazılana
kadar Kapı C'nin öneri + onay yolu geçerlidir. Kapılar ve eşikler bu karardan sonra gevşetilmez.

**Sürüm sonrası izleme.** Motor canlı defteri Kapı B dağılımına karşı puanlamaya devam eder ve gerekirse "SAPMA"
uyarısı verir. Otomatik geri alma yoktur; sahip karar verir. Mevcut defterler için de aynı kurallarla yalnız
**düşürme önerisi** yazılır.

### 6.8 Keşif katmanı (mühür sonrası ileri OOS; P3'ten itibaren; kayıt-yalnız, terfi yok)

Sahibin "taktikleri ve varyasyonları gerçekten dene" isteği Kapı A'yı beklemez. Kapı A geçmiş laboratuvarlarda
0/32, 0/8 ve 0/40 verdi; yalnız kapıya bağlı bir sistem neredeyse hiçbir şeyi ileride denemezdi.

- **Kapsam:** `LIB_v1`'deki **her** varyant × uygun her enstrüman (altın dahil: XAUUSDT, PAXGUSDT vadeli, PAXG spot).
- **Veri:** yalnız o LIB sürümünün mühür anından (`T_seal`) sonra kapanan barlar. Bu veri hiçbir seçimde görülmemiştir.
- **Adım:** S4'te, yalnız yeni kapanmış barlarda, ileri yeniden oynatma ile aynı nedensellik kuralları (karar yalnız
  kapanmış barlarla, dolum sonraki barın açılışında), base/ADVERSE/STRESS maliyetle. Her varyant × enstrüman için %0,5
  sabit riskli sanal bir defter tutulur (günlük % için). Ek CPU yalnız yeni barlar kadardır; disk küçüktür.
- **Çıktı (varyant × enstrüman başına):** n, ortalama net R (üç maliyetle), kümülatif R, %0,5 riskte günlük % serisi,
  P(gün ≥ +%1), en büyük düşüş, mühürden bu yana gün sayısı. `source = EXPLORATION_FWD`.
- **Gösterim:** lider tablosunun KEŞİF bölümü; `engine-query explore [--gold] [--variant <id>]`; özet yalnız şunu yazar:
  ileri verisi olan varyant sayısı, medyan varyantın sonucu ve "N varyant arasından en iyi 5 — seçim yanlılığı, kanıt
  değil" başlıklı kısa bir liste. Altın satırları ayrı gösterilir.
- **Sınırlar:** keşif hiçbir şeyi terfi ettirmez, hiçbir hüküm kelimesi (`KANITLANDI`, `TUTTU`) üretmez, canlı toplama
  girmez. Kapı A, keşif verisini yalnız kendi aylık bakışında tersine dönme denetimi olarak okur (§6.7). Terfi yolu
  ayrıdır.
- **Yeni LIB sürümleri** kendi `T_seal`'ından itibaren aynı biçimde birikir; eski sürümün ileri verisi yeni sürümün
  seçiminde kullanılmışsa yeni sürüm için "görülmüş" sayılır.

---

## 7. Günlük ve aylık hedef ölçümü

### 7.1 Tanımlar (`tgt_v1`, mühürlü)

Dört akış vardır ve **asla toplanmaz**:
- `LIVE_PAPER`: 8 mevcut defter (ana bot = `main_fut` + `main_spot`);
- `PROSPECTIVE_REPLAY`: motorun ileri yeniden oynatma adayları;
- `EXPLORATION_FWD`: keşif katmanı;
- `BACKTEST`: walk-forward OOS.

**Anlık görüntü ve pencere.** `S(D)`, D gününün gece çalıştırmasında (~01:37–01:45 UTC) alınan ölçülmüş anlık
görüntüdür (§4.2). P1a'nın MTM günü `W(D) = (S(D), S(D+1)]` penceresidir (≈ D 01:40 → D+1 01:40 UTC); satır her zaman
iki anlık görüntü zamanını yazar.

| Büyüklük | Tanım |
|---|---|
| Kayıt görünümü (gerçekleşmiş) | `pnl_rec(kapsam, D)` = UTC günü D'de (`closed_at`) kapanan kayıtların son revizyonlu Σ `TradeRecord.pnl`'i (vadeli + spot; giriş/çıkış ücreti, modellenmiş kayma ve net fonlama dahil). Geç fonlama `closed_at` gününe yazılır ve o günü REVİZE eder. **Açıklama içindir; hüküm vermez.** |
| Cüzdan görünümü (gerçekleşmiş) | `pnl_wal(kapsam, W)` = W içinde **gözlenen** (`observed_at`) cüzdan hareketlerinin toplamı: PNL, FEE, FUNDING ("late funding" ve "funding reversal" dahil), LIQ_FEE, TAX. TRANSFER P&L değildir, özsermaye düzeltmesi olarak ayrı tutulur. TP1 kısmi çıkışları burada, kayıt oluşmadan önce görünür. Geç fonlama gözlendiği pencereye yazılır. |
| Gerçekleşmemiş (`LEDGER_MARK`) | `U(S)` = anlık görüntüde açık pozisyonların gerçekleşmemiş kârı, **ledger'ın kendi** `positions[].last_price`'ıyla (store gerekmez). Spot varlıkların mark kaynağı sırası P1a'da bir fixture ile sabitlenir: (1) ana botun `position_path.jsonl`'ındaki ≤ 60 dk'lık son anlık görüntü, (2) worker HistoryStore'unda varsa son kapanmış spot barı (salt-okunur), (3) aynı sembolün herhangi bir vadeli ledger'daki `last_price`'ı (`PERP_PROXY`). Hiçbiri yoksa o defterin MTM'i `EKSİK`'tir. Mark kaynağı ve yaşı satırda yazar. |
| MTM gün P&L (hüküm görünümü) | `pnl_mtm(W) = E(S(D+1)) − E(S(D)) − TRANSFER(W) = pnl_wal(W) + ΔU`. Uzlaştırma bu eşitliği her gece 1e-6 ile denetler (§4.2). |
| Gün başı özsermaye | `E_book(S)` = `wallet_balance + U(S)` (spot: `cash + locked_cash + Σ assets × mark`), P1a'dan itibaren **MEASURED**. Ana bot = iki alt defterin toplamı. P1a'dan önceki günler: arşivlenmiş hareketler kapsıyorsa geriye doğru `RECONSTRUCTED`, kapsamıyorsa `EKSİK`; asla "kesin" değil. |
| UTC günü MTM (P2) | `E(00:00 D)` = 00:00'daki cüzdan (arşivlenmiş hareketlerden) + store'un 00:00 1m kapanışıyla `U` (`LAST_PRICE_PROXY`). 14 gün boyunca W-günüyle yan yana gösterilir; sonra başlık UTC gününe geçer ve sürüm `tgt_v2` olarak mühürlenir. |
| Toplam | `r_total = Σ pnl_mtm / Σ E_book` (LIVE_PAPER) |
| Defter | `r_book = pnl_mtm_book / E_book` |
| Enstrüman — toplam payda | `r_inst = Σ_defterler pnl_mtm(enstrüman) / E_total`: "toplam sermayeye katkı" (her coin, XAUUSDT, PAXGUSDT; altın toplamı ayrıca) |
| Enstrüman — defter payda | `r_book_inst = pnl_mtm(defter, enstrüman) / E_book`: "kendi defterinin sermayesine göre" |
| Marj üzerinden getiri | yalnız dipnot: "kaldıraçlı, hedef değil" |

- Enstrüman MTM'i: hareketin `ref_id`'si pozisyon/kayıt üzerinden sembole bağlanır; bağlanamayan hareket "enstrüman
  bilinmiyor" satırına gider ve sayısı gösterilir.
- Her satır şunları da taşır: işlem sayısı, ücret/kayma/fonlama toplamları, Σ net R (vadeli; R'sı olmayan spot işlem
  sayısı ayrıca), anlık görüntüde açık pozisyonlar, `data_completeness`, durum (`GEÇİCİ` / `KESİN` / `REVİZE` / `EKSİK`),
  iki anlık görüntü zamanı, mark kaynağı.
- **Kesinleşme (§4.2):** satır en az 3 gün ve fonlama kapsaması tamamlanana kadar GEÇİCİ'dir; hükümler yalnız KESİN
  günleri kullanır. Verisi eksik gün **EKSİK** işaretlenir ve hükümlerden çıkarılır; sıfır sayılmaz.
- **Ayna defterler (Değişiklik 2026-10-06).** M2X agresif defteri (`state/strategy_paper_m2x/`) M2'nin gerçek
  işlemlerinin kopyasıdır (docs/M2_AGGRESSIVE_V1.md §4.2). Arşiv (S1a) onu da kaydeder; ama toplam, ana bot grubu,
  defter grupları, enstrümanlar, altın, en iyi enstrüman, şans oranı ve hükümler onu SAYMAZ (aynı işlemler iki kez
  sayılırdı). Satırda ayrı `mirror_books` anahtarında kendi defterine göre yalnız bilgi olarak yazılır (`verdict` yok);
  `engine-status` tablosunda ve `engine-status --daily`'de ayrı "ayna defterler" bölümündedir.
  `scripts/bot_scorecard.py --daily` de onu `find_books` ile dışlar (K6 eşitliği korunur). Liste motorun içinde
  tanımlıdır (`research_engine/ledgers.MIRROR_BOOKS`; betiğin `MIRROR_BOOKS` anahtarlarıyla eşitliği testlidir). Ayna
  defter yoksa satırlar ve çıktılar öncekiyle bayt bayt aynıdır.

**Altın.** Bugün hiçbir canlı defter altın işlemiyor. `LIVE_PAPER`'da altın satırı "işlem yok" yazar. Altın
`EXPLORATION_FWD` (P3) ve `PROSPECTIVE_REPLAY` (P4) akışlarında, "canlı değil" etiketiyle görünür; sahip bir altın
defterini onaylayana kadar böyle kalır.

### 7.2 Gün isabeti (yalnız KESİN günler, MTM görünümü)

- `TOTAL_HIT(D) := r_total(D) ≥ %1,00`.
- `SINGLE_HIT(D) := max_i r_inst(D) ≥ %1,00` (hangi enstrüman olduğu yazılır). **Payda:** sahip §12 soru 12'yi
  cevaplayana kadar hüküm paydası `E_total`'dır (`r_inst`); `r_book_inst` her zaman yanında etiketiyle gösterilir.
  Özetteki "en iyi enstrüman" **hükümle aynı paydayı** kullanır.
- `DAY_HIT(D) := TOTAL_HIT veya SINGLE_HIT` (sahibin "veya"sı açık hale getirilmiştir).
- **Seçim yanlılığı koruması.** ~42 enstrümanın maksimumu şansla şişer. Her gün o günün işlem sonuçlarıyla (1000 çekim)
  "bir enstrümanın şansla +%1'e ulaşma olasılığı" hesaplanır ve gözlenen isabet oranının yanında **beklenen şans
  oranı** olarak gösterilir. İki sıfır hipotezi vardır:
  - **başlık (muhafazakâr):** net sonuçların işaretleri rastgele çevrilir (net sonuçların sıfır etrafında simetrik
    olduğu varsayılır); bu, şans oranını daha yüksek verir;
  - **ikincil (maliyet bilinçli):** brüt sonuçların işaretleri çevrilir, sonra gerçek maliyet düşülür (dağılım ortalama
    maliyet kadar negatife kayar).
  Başlığın hangisini kullandığı satırda yazar.

### 7.3 Hüküm kuralları (mühürlü, Türkçe, aynen gösterilir)

**Kayıtlı bakışlar.** Hükümler her gün yeniden hesaplanmaz (bu, kayan pencerede tekrarlı test olurdu). Hüküm yalnız
**aylık bakışta** üretilir: her ayın 3. UTC gününden sonraki ilk gece (önceki ayın günleri kesinleşmiş olsun diye). Pencere
= son 60 KESİN gün.

**İstatistik.** `p_tgt` = H0 "ortalama günlük % ≤ %1,00" için 5 günlük blok-bootstrap tek yönlü p-değeri (MTM
görünümü). Bakış alfası `α_bakış = 0,05 / 12` (yıllık 0,05 hata bütçesi 12 aylık bakışa eşit bölünür).

**Hüküm ailesi ve Holm.** Holm düzeltmesi bakıştaki **bütün hüküme uygun kapsamlar** üzerinde uygulanır, yalnız
enstrümanlar arasında değil:
- sahip §12 soru 4'te önceden bir enstrüman bildirdiyse hükme uygun kapsamlar yalnız {toplam, bildirilen enstrüman}'dır;
  diğer defter ve enstrümanlar yalnız tanımlayıcıdır;
- bildirmediyse hükme uygun kapsamlar: toplam + 8 defter + bütün enstrümanlar + altın toplamı.

| Hüküm | Koşul |
|---|---|
| `HEDEF GÜNÜ` | tek bir KESİN günde `DAY_HIT`. Sayılır, **asla başarı denmez**. |
| `HEDEF KANITLANDI (PAPER)` | bakış gecesinde, hükme uygun bir kapsam için **hepsi**: Holm-düzeltmeli `p_tgt` ≤ `α_bakış` (eşdeğer olarak Holm-düzeltmeli tek yönlü güven sınırı ≥ %1,00); 60 günde ≥ 40 tam KESİN gün; ≥ 30 kapanmış işlem; tek bir gün pencere toplamının en fazla %25'i; ayrıca yalnız-gerçekleşmiş (cüzdan görünümü) hesapla da aynı koşul sağlanmalı. |
| `HEDEF YOLUNDA (umut verici)` | bakışta nokta ortalaması ≥ %1,00, ama KANITLANDI koşulları sağlanmıyor |
| `HEDEFİN ALTINDA` | diğer durumlar (açık fark ile) |
| `VERİ YETERSİZ` | asgari koşullar sağlanmıyor |

- **Bakışlar arası.** Özet "son bakış hükmü (tarih)" ile birlikte "ara görünüm: 60g ort. x% [CI …] — hüküm değildir"
  yazar; ara görünümde hüküm kelimesi kullanılmaz.
- Geriye dönük "en iyi enstrüman" yalnız Holm düzeltmeli gösterilir.
- `PROSPECTIVE_REPLAY`, `EXPLORATION_FWD` ve `BACKTEST` aynı tabloyu büyük bir "canlı değil" etiketiyle alır; **asla**
  `KANITLANDI` üretemez.
- Bu kuralların hepsi bir özellik testiyle (property test) korunur: `KANITLANDI` yalnız bakış gecesinde, yalnız KESİN
  günlerle, yalnız hükme uygun kapsamda ve yalnız `LIVE_PAPER`'da çıkabilir; 40 KESİN günden az iken, Holm-düzeltmeli
  `p_tgt` > `α_bakış` iken veya yalnız-gerçekleşmiş satırdan asla çıkamaz.

### 7.4 Kayan istatistikler (7/30/60/90 gün; yalnız KESİN günler, ara görünüm)

- isabet günü sayısı ve oranı (Wilson CI) ile beklenen şans oranı;
- aritmetik ve geometrik (bileşik) ortalama;
- medyan;
- blok-bootstrap CI95;
- en kötü gün ve en büyük düşüş (isabet oranının hemen yanında, böylece −%5 günle alınan +%1 gün görünür);
- gerekli ile gerçekleşen karşılaştırması: %1/gün ≈ 30 günde ×1,348, yılda ×37,8; %0,5 riskte defter başına günde +2R.

### 7.5 Aylık hedef

- Mevcut `bot_scorecard.py` "AYLIK HEDEF" bloğu korunur: ayda en az +%1 net, her defter kendi kâğıt bakiyesinde
  (`MONTHLY_TARGET_PCT = 1.0`). Ana botun spot defteri bu bloğa eklenir.
- Motor aynı aylık hesabı enstrüman ve toplam için de raporlar.
- Günlük hedef aylık hedefin ~30 katıdır. Bu oran raporda açıkça yazılır.

### 7.6 Kaldıraç gerçekliği (HİPOTETİK)

Her lider tablosu varyantı ve kanıtlı her kapsam için şunlar hesaplanır:
- `k* = %1 ÷ ortalama günlük %`: +%1/gün ortalaması için gereken risk katı;
- k* altında benzetilen en büyük düşüş;
- bir yıl içinde %50 düşüş olasılığı (günlük getirilerin blok bootstrap'i).

Bu satır "HİPOTETİK — gerçekleşmedi" etiketiyle **yalnız lider tablosunda ve öneri belgelerinde** gösterilir; özet
başlığında, `engine-status --brief`'te ve `--check` özetinde yer almaz. Böylece sahibin ilk okuduğu şey asla "kaldıracı
artır" olmaz. Tablo, "kaldıracı artır" cevabının iflas maliyetini görünür kılar. Motor kaldıraç değişikliğini **asla**
önermez.

### 7.7 Nerede görünür

| Yer | Ne zaman | İçerik |
|---|---|---|
| `scripts/bot_scorecard.py --daily [--days N] [--out]` | **P1a** | gerçekleşmiş (kayıt + cüzdan görünümü) ve gerçekleşmemiş (`LEDGER_MARK`) yan yana; salt-okunur, motordan bağımsız hesap (sahip motor olmadan doğrulayabilsin diye); **`state/spot_ledger.json` dahil** ("Ana bot · spot" satırı); "GÜNLÜK HEDEF" bloğu "AYLIK HEDEF"in yanında; eski D4 etiketi ("Trend 4h …") "D4 Donchian 4h" olarak düzeltilir. Engine-app klonundan çalışır, app sürümü gerekmez. |
| `engine-status --brief` | **P1a** | ≤ 60 satır: son çalıştırma, öz-denetim sonucu, SKEW, rotasyon payları, günlük hedef tablosu (GEÇİCİ/KESİN), A/B durumu; P1b'den veri tazeliği |
| Motor sürüm betiği `--check` › "GECE ÖĞRENME MOTORU" › "GÜNLÜK HEDEF" | **P1a** | son 7 gün toplam + defter (durum etiketiyle), en iyi enstrüman (hüküm paydasıyla) ve şans oranı, son bakış hükmü; k* yok |
| `digest_tr.md` başlık satırları | P1a | aşağıdaki biçim |
| Panel `/hedef` ("Günlük hedef ve öğrenme") | **bir sonraki normal worker/dashboard sürümüyle** | tablo + 90 günlük çubuk grafik (r_total ve en iyi enstrüman, %1 çizgisine karşı), altın satırı, ayrı aday ve keşif bölümleri, rapor yaşı, "> 24s ESKİ RAPOR", şerit "tanımlayıcı; karar yok; kanıt değil; PAPER" |

**Özet başlık biçimi (P1a).** Yalnız-gerçekleşmiş bir satırda `TUTTU` veya `HEDEF GÜNÜ` kelimesi **asla** kullanılmaz;
her gerçekleşmiş sayının yanında açık pozisyonların gerçekleşmemiş kârı yazar.

```
Dün (GEÇİCİ · W 10-04 01:41 → 10-05 01:40 UTC): MTM toplam +x,xx% (LEDGER_MARK) · gerçekleşmiş +y,yy% · açık gerçekleşmemiş −z USDT (−w,ww%)
  en iyi enstrüman Y +a,aa% (payda: toplam sermaye; kendi defterine göre +b,bb%) · beklenen şans oranı %c (işaret çevirme)
  %1'e göre: altında (geçici; kesinleşme 10-07)
Son kesin gün 10-01: MTM +… → HEDEF GÜNÜ: hayır
Son bakış 10-04: toplam → VERİ YETERSİZ (12 KESİN gün / 40) · ara görünüm 60g ort. … [CI …] — hüküm değildir
```

Panel sayfası dashboard'un çalıştığı `/opt/tradingbot/app`'ten servis edilir. Bu yüzden motor-yalnız bir sürümle
gelemez. Worker çalışırken app ağacını ff-merge etmek güvensizdir, çünkü `engine_v3` modülleri tembel import eder ve
karışık kod çalıştırır. Sayfa depoya birleştirilir ve sahibin bir sonraki normal sürümüyle (worker boşta durdurulup
başlatılarak) canlıya çıkar. O zamana kadar sayılar `--check`, `engine-status` ve `scorecard --daily` üzerindendir.

Sayfanın davranışı:
- `/quant` örneğini izler: `StateReader`'a `TRADINGBOT_DATA/research/summary` kökü eklenir, okuma `_xp_json` tarzı boyut
  sınırlıdır.
- Yalnız GET kabul eder; POST'a 405 döner.
- Dosya yoksa kart çıkmaz ve sayfa bayt-özdeş kalır.
- Dashboard birimi zaten `ReadOnlyPaths=/opt/tradingbot/data` ile çalışır.

Bir kabul testi `daily_target` ile `scorecard --daily`'nin aynı sayıları (kayıt, cüzdan, gerçekleşmemiş; spot dahil)
verdiğini denetler.

---

## 8. Token planı (dürüst)

**Hedef:** rutin öğrenme 0 AI token ve 0 tekrarlı indirme. Kod değişiklikleri ucuz değildir; bu ayrıca yazılır.

1. **Veri kalıcı.** Tüm veri VPS'te kalır (`store`, `archive_cache`, `dukascopy`). İlk doldurmadan sonra yalnız günlük
   fark iner. Rutin iş için GitHub Actions laboratuvar çalıştırmaları ve scratchpad önbellekleri bırakılır.
2. **Deterministik gece işi.** Gece işinin tamamı deterministik Python'dur. Botun içindeki LLM noop /
   `POSTMORTEM_ONLY` olarak kalır; motor hiç LLM çağırmaz. Türkçe metinler şablondan üretilir.
3. **AI yalnız şunları okur** (sahip yapıştırır):
   - `engine-status --brief` (≤ 60 satır) veya `summary/digest_tr.md` (≤ 8 KB, ≈ 2,5k token);
   - belirli bir soru için `engine-query <konu>` (≤ 4 KB / ≤ 150 satır): `lessons --scope`, `trials --like <fikir>`,
     `explore`, `challenger <id>`, `proposal <id>`, `day <tarih>`, `trade <trade_key>`, `why-lost <defter>`;
   - bir önerinin `PROPOSAL.tr.md`'si (≤ 10 KB).
   AI ham günlüğü, yolları, geriye-test ayrıntısını veya store'u **asla** okumaz.
4. **Önce deneme defteri.** Bir AI oturumu yeni bir şey önermeden önce `engine-query trials --like <fikir>` çalıştırır;
   aynı mühürle zaten test edilmiş fikri yeniden önermez. Bu kural `CLAUDE.md`/belgelere yazılır.
5. **Gerçek maliyetler:**
   - haftalık özet okuma: ≈ 5k token/hafta (gerçekçi);
   - haftalık özet ve öneri incelemesi oturumu: ≈ 10–40k token;
   - **her kod sürümü** (yeni kütüphane girdisi, atıf kodu, motor aşaması): kod PR'ı + bu projenin çok turlu karşıt
     incelemeleri + sahte VPS geçişi + sürüm betiği; gözlenen maliyet **sürüm başına ≈ 10⁵–10⁶+ token**'dır (önceki
     taslaktaki "50–150k / fikir" tahmini gerçekçi değildi);
   - Türkçe öneri belgeleri ve `run_status.json`'dan arıza teşhisi: oturum başına ≈ 10–50k.
6. **Kadans ve bütçe.**
   - Yeni fikirler tek tek PR olmaz; **ayda bir LIB sürümünde** toplanır (`LIB_v1`, `LIB_v1.1`, …).
   - Sürüm başına bütçe: ≤ 1,5M token (inceleme turları dahil); aşılırsa kapsam küçültülür, inceleme atlanmaz.
   - Motor aşamaları (P1a, P1b, P2, …) birer sürümdür ve her biri bu bütçeye tabidir.
7. **Oturum kuralları.** Claude oturumları piyasa verisi **indirmez**; testler `tests/fixtures` altındaki küçük
   sentetik verilerle çalışır. Gerçek veri gerektiren laboratuvarlar VPS'te sahibin komutuyla
   (`--cache /opt/tradingbot/data/research/archive_cache --offline`) veya kayıt girdisi eklendikten sonra gece
   çalıştırıcısıyla koşar.
8. **Boyut sınırları.** Özet ve sorgu çıktı boyut sınırları testlerle zorlanır.

**Beklenen tasarruf (dürüst):** rutin "ne oldu, neden" soruları için oturum başına O(100k–1M token + indirme süresi)
yerine haftada O(5k token) okuma. Yeni fikirler ise ucuzlamaz: ayda bir LIB sürümü ≈ 10⁵–10⁶ token tutar. Kazanç,
aynı fikrin yeniden test edilmemesinden (deneme defteri) ve verinin yeniden indirilmemesinden gelir.

---

## 9. Güvenlik ve onaylar

### 9.1 Ne kayıt-yalnız, ne onay ister

| Öğe | Durum | Onay |
|---|---|---|
| Kapanış/hareket arşivi, anlık görüntüler, veri deposu, günlük, atıf, dersler (`APPLIED_BOUNDED` hariç), lider tablosu, keşif katmanı, günlük hedef | kayıt-yalnız; hiçbir karar yolu okumaz (AST testi) | sürüm onayı (her sürüm gibi) |
| İleri yeniden oynatma adayları (motorun kendi kâğıt defterleri) | kayıt-yalnız; canlı toplamlara eklenmez | Kapı A'dan otomatik doğar; `PROMOTION_REGISTRY` metni sahibin onayıyla mühürlenir (P4 öncesi) |
| Öneri dosyaları | yalnız metin + kanıt | — |
| Yeni kâğıt defter (onaylı öneriden) | canlı PAPER | **sahibin açık onayı** + normal sürüm |
| Mevcut bir defterin herhangi bir kararı | — | **ayrı, açık sahip onayı**; motor bunu asla otomatik yapmaz |
| Worker'a kayıt-yalnız alan ekleme (P6) | karar değişmez | **sahibin açık onayı** (worker kodu değişir, yeniden başlatma gerekir) |
| Telegram "günlük rapor hazır" | kapalı | config değişikliği → onay |
| `research_enabled=True` (ResearchCoordinator) | **mevcut sistem**: `engine_v3:1915` ana bot girişlerinde filtre/küçültme politikasını otomatik etkinleştirebilir (`PAPER_RESEARCH_ACTIVE`) | **P0'da sahibin açık kararı**: önceden onaylı mı, yoksa ayrı onaylı bir değişiklikle kapatılsın mı. Motor buna dokunmaz; etkinleştirme sayısı `--check`'te raporlanır (§2.9). Bu karar verilmeden "hiçbir şey otomatik uygulanmaz" iddiası tüm sistem için doğru değildir. |

### 9.2 Sert kurallar (testlerle)

| Kural | Nasıl korunur |
|---|---|
| Yalnız PAPER | S0 doğrular (`state/mode.json`); `ALLOW_LIVE_TRADING=false` |
| Sır yok | `EnvironmentFile` yok; özetlerde sır yok |
| State'e yazma yok | çekirdek (`ReadOnlyPaths`) + **S0 çalışma zamanı denemesi** + açma modu denetimi + `find_books`/spot yolları özellik testi |
| Ağ yalnız veri biriminde | `PrivateNetwork=yes` gece biriminde; birim sözleşme testi; **S0 soket denemesi** |
| Bellek sınırı gerçekten uygulanıyor | S0 cgroup `memory.max` denetimi |
| Worker deposuna yazma yok | ayrı `ResearchStore` kökü |
| Config değişmez ve kayma motoru kırmaz | sürüm değişmezi: `config.yaml` baytları değişmemiş olmalı; motor `load_config`/`load_v3` çağırmaz (AST testi); SKEW denetimi |
| Karar modülleri motoru import etmez | genişletilmiş AST listesi (§2.8); `cli.py`/`cli_v3.py`'de üst düzey import yok |
| SQLite'a dokunulmaz | `sqlite3` import yasağı (AST) |
| Zaman pencereleri | `OnCalendar` UTC ve 4h pencereleri dışı; birim sözleşme testi; yedek çakışması durumla denetlenir |
| Mühürler | `attribution_v1`, `cfgrid_v1`, `LIB_v1`, `PROMOTION_REGISTRY`, `tgt_v1`, `config_epochs` sha'ları testte ve bu belgede sabit; sha değişirse CI kırılır |

### 9.3 Birim kurulum güvenliği (daemon-reload) ve ilk çalıştırma

- Depodaki worker birimi `MemoryMax=4G` der; VPS'te 6G override vardır. `daemon-reload` (`enable`'ın örtük reload'u
  dahil) bu kaymayı uygulayabilir.
- Kurulum adımı bu yüzden kapılıdır:
  1. worker ve dashboard için `NeedDaemonReload=no` ve worker `MemoryMax=6G` doğrulanır;
  2. `/opt/tradingbot/engine-app` ayrı klon olarak kurulur veya sabitlenir; `python -m compileall` ile önceden derlenir;
  3. `/opt/tradingbot/data/research` (ve `locks/`, `backup/`) `install -d -o tradingbot -g tradingbot -m0750` ile
     oluşturulur (yoksa `226/NAMESPACE`);
  4. `install -m0644` ile birimler kurulur;
  5. `daemon-reload` yapılır;
  6. `MemoryMax=6G` **yeniden** doğrulanır (değilse betik hemen geri alır ve durur);
  7. **elle smoke çalıştırma:** `systemctl start tradingbot-engine-night.service` (P1b'de veri birimi için de) bir kez
     elle çalıştırılır; çıkış kodu 0, `run_status.json`'da öz-denetim `OK`, `226/NAMESPACE` yok, beklenen dosyalar
     oluşmuş olmalıdır; değilse zamanlayıcı **etkinleştirilmez**;
  8. ancak bundan sonra `enable --now` zamanlayıcılar;
  9. `is-enabled` ve `is-active` doğrulanır.
- `setup_vps_v3.sh` asla yeniden çalıştırılmaz.
- P1a'da gece birimi, P1b'de veri birimi **bir kez** kurulur. Sonraki aşamalar yalnız engine-app sabitini (pin) taşır;
  birim dosyası değişmezse yeniden reload gerekmez.
- **VPS'te pytest yoktur.** Sürüm betiğinin `--dry-run`'ı depo testlerini koşamaz; f8b05fb'nin #46–#48 değişmezleri gibi
  **bağımsız koşucular** (ayrı süreç, servis ortamı aktarılmadan, küçük yerine-geçenlerle) kullanılır.
- **Küçük, ayrılmış sürüm betiği.** 4.716 satırlık `tb-deploy-f8b05fb.sh` kopyalanmaz. Motorun kendi betiği
  (`deploy/releases/tb-engine-<sha7>.sh`) yalnız motor adımlarını içerir: `--dry-run`, deploy, `--check`, `--ab-report`,
  `--rollback`; hedef < ~800 satır.

### 9.4 Kapatma düğmeleri

- Anında durdurma: `sudo systemctl disable --now tradingbot-engine-night.timer` (P1b'den sonra
  `tradingbot-engine-data.timer` da). Veri yerinde kalır.
- Tam geri alma: zamanlayıcıları kapat, birim dosyalarını sil, (kapılı) `daemon-reload`, `/opt/tradingbot/engine-app`
  klonunu kaldır. `data/research` kalır (sahip isterse ayrıca silinir; önce yedeği alınır).
- Disk koruması: 20 GB üstünde motor kendiliğinden çalışmayı reddeder.
- Tek bir adayı durdurmak: `engine-query --retire <id> --operator berke`.

---

## 10. Aşamalı teslim

Her aşama tek bir dry-run, tek bir deploy ve günlük `--check` ile yürütülür. Sahip her VPS komutunu kendisi çalıştırır.
~~Hiçbir motor aşaması başka bir sürümün 7 günlük `--check` penceresi içinde dağıtılmaz (§2.9).~~ (eski metin)
**Değişiklik (2026-10-06, sahip kararı: "3 temiz gün sonra"):** hiçbir motor aşaması başka bir sürümün yeniden
başlatmasından 3 günden önce ve o sürümün ilk iki `--check` çıktısı temiz olmadan dağıtılmaz (3 günü betik zorlar; iki
temiz `--check` sahip + inceleyici yargısı; motorun etkisi yine A/B gecelerinde ölçülür; §2.9).
**Değişiklik (2026-10-07, sahip kararı: "öğrenmeyi şimdi kur"; 2026-10-08 notu):** 3 gün yerine **1 gün** (P1a ve P1b
betikleri zorlar); iki temiz `--check` kuralı değişmedi.

### P0 — VPS bilgileri ve taban çizgisi (salt-okunur; kod yok)

**Teslimatlar:** aşağıdaki salt-okunur kontrol listesi ve sahip çıktıları yapıştırdıktan sonra kaydedilen
değerler. **Taban çizgisi her worker sürümünden sonra (örn. wt-tourfix `34ae8d2` dağıtılınca) yeniden alınır.**

```
nproc; free -m; df -h /opt; timedatectl | grep -i zone
systemctl show tradingbot-worker -p MemoryMax,MemoryPeak,CPUQuotaPerSecUSec,NeedDaemonReload,NRestarts
systemctl show tradingbot-dashboard -p MemoryPeak,NeedDaemonReload
cat /sys/fs/cgroup/system.slice/tradingbot-worker.service/memory.peak
systemctl cat tradingbot-alert@.service >/dev/null 2>&1 && echo ALERT_VAR || echo ALERT_YOK
systemctl cat tradingbot-backup.timer | grep -E 'OnCalendar|OnUnitActiveSec|RandomizedDelaySec|OnBootSec'
getent group systemd-journal
git -C /opt/tradingbot/app rev-parse HEAD
sudo du -sh /opt/tradingbot/data/market/history
sudo find /opt/tradingbot/data/market/history -name manifest.json | awk -F/ '{print $(NF-3),$(NF-1)}' | sort | uniq -c | head -60
sudo ls -la /opt/tradingbot/data/state/spot_ledger.json /opt/tradingbot/data/state/*/futures_ledger.json
sudo -u tradingbot -H bash -c 'cd /opt/tradingbot/app && /opt/tradingbot/venv/bin/python -m tradingbot research-status; /opt/tradingbot/venv/bin/python -m tradingbot model-status; /opt/tradingbot/venv/bin/python -m tradingbot mode-status'
sudo head -c 1500 /opt/tradingbot/data/state/protective_monitor.json; echo
curl -sI https://data.binance.vision/data/futures/um/monthly/klines/XAUUSDT/1h/XAUUSDT-1h-2026-08.zip | head -1
curl -sI https://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/02/BID_candles_min_1.bi5 | head -1
sudo journalctl -u tradingbot-worker --since "-24h" | grep -i "tur" | tail -80        # tur süreleri, p50/p95 taban çizgisi
sudo journalctl -u tradingbot-worker --since "-7d" -o cat | grep -cE '(^| )(429|418) https?://'   # 418/429 tabanı
sudo journalctl -u tradingbot-worker --since "-7d" -o cat | grep -c 'PAPER_RESEARCH_ACTIVE'        # ResearchCoordinator
```

**Kabul:**
- değerler kaydedildi;
- birim belleği formülle hesaplandı (§2.5; gece + doldurma birlikte);
- Dukascopy yolu seçildi (hazır ayna VPS'te mi, tarball mı, "yapılamadı" mı);
- `alert@` var/yok bilgisi kaydedildi;
- tur p50/p95, Box kaçan bar oranı, koruyucu izleyici gecikmesi, 418/429 ve `DATA_VERDICT_MISSING`/bayat ret oranı
  tabanları kaydedildi (A/B gecelerinin KAPALI tarafı bunları her sürümde yeniden ölçer);
- **ResearchCoordinator kararı yazılı olarak verildi.**

**Sahip:** komutları çalıştırır, çıktıyı yapıştırır, sabit sınırları (bellek, pencereler, 20 GB) onaylar.

### P1a — Kapanış/hareket arşivi + günlük hedef + gece birimi (AĞSIZ, KARAR-NÖTR, SAHTE VPS'TE SINANABİLİR)

**Kapsam (tam):**

| Dosya | İçerik |
|---|---|
| `tradingbot/research_engine/__init__.py`, `paths.py`, `lock.py` | kök düzeni, disk koruması, `analysis.lock` (`data.lock` yalnız tanımlı) ve `SKIPPED_LOCKED` |
| `research_engine/selfcheck.py` | S0: PAPER (`state/mode.json`), yalıtım denemeleri (state/market/app'e yazma reddi, soket reddi, cgroup `memory.max`, `/tmp` yazılabilir), SKEW (iki SHA, ata denetimi), yedek birimi aktif mi, A/B takvimi |
| `research_engine/rawconfig.py` | hoşgörülü ham YAML okuma, config sha'sı, ihtiyaç listesi; `load_config`/`load_v3` yok |
| `research_engine/ledgers.py` | `futures_ledger.json` + `state/spot_ledger.json` ham salt-okunur okuma, `schema_version` denetimi |
| `research_engine/closes.py` | vadeli + spot `history[]` arşivi (`rev`, son 14 günün yeniden eşitlenmesi, geriye tarihli kapanış); `entries[]` arşivi (anahtarlı, idempotent, hizalama, `ENTRIES_GAP`); gecelik ölçülmüş anlık görüntü; uzlaştırma (kayıt ve cüzdan görünümü); rotasyon payı; geri yükleme tespiti (`RESTORED_AWAY`); `closes_derived` (`min_to_next_funding`, `decision_delay_s`) |
| `research_engine/daily_target.py` | kayıt görünümü (UTC günü), cüzdan görünümü (gözlem penceresi), `LEDGER_MARK` gerçekleşmemiş ve MTM; GEÇİCİ/KESİN/REVİZE/EKSİK; iki payda; şans oranı (iki sıfır hipotezi); aylık bakış hükümleri (Holm, `α_bakış`) |
| `research_engine/summary.py`, `backup.py`, `night.py` (iskelet) | S0, S1a, S3, S7, S7b; `run_status.json` (aşama süreleri, CPU, tepe bellek, iki SHA, config sha); özet başlık biçimi (§7.7); araştırma yedeği |
| `tradingbot/cli_v3.py` | `engine-night`, `engine-status [--brief]`, `engine-restore`; **tembel import** |
| `scripts/bot_scorecard.py` | `--daily [--days N]` (bağımsız, salt-okunur; kayıt + cüzdan + gerçekleşmemiş; **spot dahil**) + D4 etiketi düzeltmesi |
| `deploy/tradingbot-engine-night.{service,timer}` | §2.2–2.5 ayarları; `PrivateNetwork=yes`, `PrivateTmp=yes`, `PYTHONDONTWRITEBYTECODE=1`, `ENGINE_EXPECTED_MEMORY_MAX` |
| `deploy/releases/tb-engine-<sha7>.sh` | **küçük, ayrılmış betik** (< ~800 satır): `--dry-run` / deploy / `--check` / `--ab-report` / `--rollback`; ayrı klon kur/sabitle + `compileall`; `data/research` dizinini oluştur; kapılı birim kurulumu ve elle smoke çalıştırma (§9.3); bağımsız değişmez koşucuları (pytest yok); adlandırılmış değişmezler |
| `tests/` + `tests/standalone/` | depo testleri + VPS'te koşan bağımsız koşucular |
| `docs/SYSTEM_LEARNING_ENGINE_V1.md` (bu belge), `docs/BACKUP_RESTORE.md`, `docs/OPERATIONS.md` | "P1a işletim" bölümü; araştırma yedeğinin VPS dışına taşınması; app sürümlerinde engine-app'i yeniden sabitleme kuralı |

**P1a'da olmayanlar:** ağ; store, tohum, evren, veri birimi, ilk doldurma; worker / app / dashboard / config değişikliği;
panel sayfası; günlük `tj_v1`, atıf, kütüphane, adaylar.

**Depo kabul testleri:**
1. Kapanış arşivi: 5000 `history` rotasyonunu atlatır (sentetik ledger); geç fonlama `rev+1` ve `REVİZE` üretir;
   geriye tarihli `closed_at` önceki günün satırını revize eder; kayıt görünümü uzlaştırması 1e-6.
2. **Spot:** sentetik bir `spot_ledger.json` satışı arşivde (`main_spot`) ve `r_total`'da görünür; kaldıraç 1, fonlama
   `NOT_APPLICABLE`; kısmi satışlar ayrı satır, aynı `position_group`.
3. Hareket arşivi: 2000 `entries` rotasyonunu atlatır; yeniden çalıştırma 0 satır ekler; FEE/PNL/FUNDING ("late
   funding", "funding reversal")/TRANSFER/LIQ_FEE arşivlenir; hizalama kaybında `ENTRIES_GAP` ve o pencere EKSİK.
4. Anlık görüntü: iki anlık görüntü arasında cüzdan farkı = gözlenen hareketlerin toplamı (1e-6); spot `cash` için de.
5. Günlük hedef: satır 3 gün ve fonlama kapsaması tamamlanana kadar GEÇİCİ; revizyon yalnız eklenir ve `REVİZE` görünür;
   hüküm yalnız bakış gecesinde; **yalnız-gerçekleşmiş satırlarda `TUTTU`/`HEDEF GÜNÜ` metni hiç geçmez** (özet,
   status ve `--check` çıktıları üzerinde metin testi); iki payda etiketli; "en iyi enstrüman" hüküm paydasıyla.
6. `KANITLANDI` özellik testi (§7.3).
7. `daily_target` ile `scorecard --daily` aynı sayıları verir (kayıt, cüzdan, gerçekleşmemiş; spot dahil).
8. Geri yükleme: `seq` azalınca `RESTORED_AWAY`, `INCONSISTENT` yok.
9. Birim sözleşmesi (gece birimi):
   - `OnCalendar` `UTC` içerir ve hh%4==0 için hh:00–hh:35'te başlamaz;
   - `Nice=19`, `CPUWeight=10`, `IOSchedulingClass=idle`, `OOMScoreAdjust=1000`;
   - `ReadWritePaths` yalnız `data/research`; `PrivateNetwork=yes`; `PrivateTmp=yes`;
   - `EnvironmentFile` yok; `TRADINGBOT_DATA` açık; `PYTHONDONTWRITEBYTECODE=1`;
   - `ENGINE_EXPECTED_MEMORY_MAX` = `MemoryMax` (bayt);
   - `python -s -m`, `-I` yok; `WorkingDirectory=/opt/tradingbot/engine-app`.
10. `systemd-analyze verify` ve `shellcheck` temiz; sahte `systemctl` ile sandbox kurulumu: dizin oluşturma, smoke
    çalıştırma başarısızsa zamanlayıcının etkinleşmediği, geri alma.
11. AST yalıtımı: genişletilmiş liste (§2.8); `cli.py`/`cli_v3.py`'de üst düzey `research_engine` import'u yok;
    `research_engine` `config_v3`/`load_config`/`sqlite3` import etmez.
12. Sahte state üzerinde tam çalıştırma: yazmalar yalnız `data/research` altında; ledger'lar `"r"` modunda açılıyor;
    `find_books` yolları ve `state/spot_ledger.json` için özellik testi.
13. S0 öz-denetimi: yazılabilir state, açık soket veya yanlış `memory.max` benzetildiğinde `ISOLATION_BROKEN` ile durur;
    SKEW benzetildiğinde yalnız S0/S1a/S7 çalışır.
14. `rawconfig`: `learning_mode`/`shared_experience`'ta bilinmeyen anahtar motoru kırmaz; sha kaydedilir.
15. Araştırma yedeği: içerik (dahil/hariç listesi), sha doğrulaması, saklama; `engine-restore` kuru çalıştırma.
16. Rotasyon payı hesabı ve 3 gün uyarısı; A/B takvimi ve `AB_OFF_ZORUNLU_ARŞİV`.
17. Özet ≤ 8 KB, status ≤ 60 satır.
18. Bağımsız koşucular, depo testlerinin VPS'te koşan alt kümesini (1–8, 13, 14) pytest olmadan çalıştırır.

**VPS kabul ölçütleri (yalnız gerçek VPS'te doğrulanabilenler §2.8 tablosunda):**
1. Elle smoke çalıştırma başarılı: öz-denetim `OK` (state'e yazma `EROFS`/`EACCES`, soket reddedildi, `memory.max`
   doğru), `226/NAMESPACE` yok.
2. İlk gece: kapanış arşivi kayıt sayısı = her defterde (`main_spot` dahil) ledger'ın elde tuttuğu `history`; hareket
   arşivi ve anlık görüntü yazıldı.
3. 14 gece boyunca `INCONSISTENT` yok (veya açıklanmış), `ISOLATION_BROKEN` yok, `SKEW` yok (bir app sürümü olduysa
   engine-app yeniden sabitlendi).
4. A/B raporu (`--ab-report`): AÇIK gecelerde tur p95 ≤ KAPALI + %5; `NRestarts` değişmez; worker `MemoryMax` hâlâ 6G;
   Box kaçan bar oranı ve koruyucu izleyici gecikmesi KAPALI tabanı içinde; 418/429 = 0; `DATA_VERDICT_MISSING`/bayat
   ret oranı değişmez; `PAPER_RESEARCH_ACTIVE` sayısı raporlandı.
5. Motor `memory.peak` ≤ 0,8 × MemoryMax.
6. `scorecard --daily` ile `engine-status` aynı sayıları verir.
7. Araştırma yedeği her gece yazıldı ve doğrulandı.
8. Bütün defterlerde rotasyon payı ≥ 3 gün (değilse uyarı gereği yapıldı).

**Sahibin VPS adımları:**

```
sudo bash tb-engine-<sha7>.sh --dry-run
sudo bash tb-engine-<sha7>.sh            # worker durmaz; klon + dizin + kapılı birim + elle smoke; smoke geçmezse zamanlayıcı açılmaz
sudo bash tb-engine-<sha7>.sh --check     # 14 gün boyunca her gün
sudo bash tb-engine-<sha7>.sh --ab-report # 14. günden sonra bir kez
sudo -u tradingbot /opt/tradingbot/venv/bin/python /opt/tradingbot/engine-app/scripts/bot_scorecard.py \
  --state /opt/tradingbot/data/state --daily --days 30
```

### P1b — Veri deposu, tohum, ilk doldurma ve veri birimi

**Kapsam:**

| Dosya | İçerik |
|---|---|
| `research_engine/store.py` | `ResearchStore(HistoryStore)`: ay-parçası checksum'ı + `.sha256` yan dosyası, parça checksum'larından `data_seal`, satır kaynağı `_src` ve öncelik (archive > seed > archive_unverified > rest), fark kaydı, `MANIFEST_LAG` kurtarma / `CORRUPT` karantina, veriden fonlama aralığı, seri kilidi, `metrics_5m` / `duka_1h` / `duka_1m`, yalnız kapanmış bar, 2025+ bütün spot serilerinde µs→ms |
| `research_engine/universe.py` | U_R (40 giriş evreni ∪ BTC/ETH ∪ son 180 günde işlem görenler ∪ ana botun spot sembolleri ∪ XAUUSDT, PAXGUSDT vadeli, PAXGUSDT spot); günlük evren + `exchangeInfo` anlık görüntüsü |
| `research_engine/seed.py` | worker deposunu tutarlı biçimde kopyala (manifest → parçalar → manifest), uyuşmazlıkta yeniden kopyala veya yeniden indir |
| `research_engine/datastore.py` | arşiv-önce ekleme, `.CHECKSUM` yoksa `archive_unverified`, worker 429/418 günlük koruması, seri başına 3 deneme + yakalama (`STALE`), tek `BudgetPool` güvenlik 0,1, `X-MBX-USED-WEIGHT-1M` takibi (%50'de bekle), 418/429'da REST'i durdur, REST kuyruğu `_src=rest`, ertesi gün arşiv uzlaştırma + fark kaydı, `data_status.json` + `data_seal`, ilk doldurmada ilerleme/ETA ve 4h pencerelerinde kendini duraklatma; hazır Dukascopy aynasını içe alma (`--import-dukascopy <dizin>`) |
| `research_engine/provider.py` | `StoreProvider` + fixture üzerinde parite testi (laboratuvarlar bu aşamada **geçirilmez**) |
| `tradingbot/cli_v3.py` | `engine-data {--backfill,--update,--status,--import-dukascopy}` (tembel import) |
| `deploy/tradingbot-engine-data.{service,timer}` | §2.2–2.5; ağlı; `SupplementaryGroups=systemd-journal`; `data.lock` |
| `tb-engine-<sha7>.sh` (yeni sürüm) | veri birimini kapılı kurar, elle smoke çalıştırır; `--backfill` alt komutu ilk doldurmayı birimle **aynı** özelliklerle (`ProtectSystem=strict`, `PrivateTmp=yes`, `ReadWritePaths`, `ReadOnlyPaths`, Nice/CPU/IO/OOM, `SupplementaryGroups`, ortam) `systemd-run` ile başlatır |

`scripts/dukascopy_mirror.py` adlı bir dosya hiçbir çalışma ağacında yoktur; önceki taslaktaki referans kaldırıldı.
v1'de Dukascopy indirici yazılmaz; yalnız hazır bir ayna içe alınır (§3.3).

**Depo kabul testleri:**
1. Sahte sağlayıcıyla idempotent yeniden çalıştırma 0 satır ekler ve checksum'lar özdeş kalır.
2. Parça checksum'ı ile `data_seal` tutarlıdır; `data_seal` hesabı tam store'u okumaz (okuma sayacı testi).
3. Yalnız kapanmış bar yazılır.
4. Bir serinin hatası diğerlerini durdurmaz.
5. 418/429 REST adımını durdurur; worker günlüğünde son 60 dk'da 429/418 varsa veya günlük okunamıyorsa REST hiç
   çağrılmaz.
6. Bütçe hiçbir zaman 0,1 payı aşmaz.
7. Arşiv uzlaştırma REST barlarını değiştirir ve farkı kaydeder; REST yazımı arşiv barını asla ezmez (öncelik testi);
   `.CHECKSUM`'sız zip `archive_unverified` olur ve doğrulanmış sayılmaz.
8. Parça yazıldıktan sonra manifest kaydedilmeden öldürülen çalıştırma `MANIFEST_LAG` ile kendiliğinden düzelir; bozuk
   parça karantinaya alınıp yeniden çekilir; seri yalnız iki ardışık başarısız geceden sonra durur.
9. Fonlama aralığı veriden çıkarılır (8h/4h/1h fixture'ları); boşluklar bu aralıkla sayılır.
10. 2025+ bütün spot serilerinde (BTC/ETH dahil) µs→ms.
11. Eşzamanlı yazıcı benzetimiyle tohumlama tutarlı kopya üretir veya yeniden indirir; asla tümden başarısız olmaz.
12. Veri birimi sözleşmesi: UTC, pencere dışı; `SupplementaryGroups=systemd-journal` yalnız veri biriminde; `PrivateTmp`;
    `ReadWritePaths` yalnız `data/research`.
13. İlk doldurma ilerleme ve ETA yazar; 4h pencerelerinde durur.
14. `StoreProvider` fixture paritesi.
15. Gece birimi doldurma sürerken `SKIPPED_LOCKED` olmaz (ayrı kilitler); okuduğu parça değişmişse `DATA_MOVING`.

**VPS kabul ölçütleri:**
1. İlk doldurma tamamlanır (ilerleme/ETA görünür; başka bir sürümün `--check` penceresi dışında başlatılır).
2. `data_status` her planlanan seriyi `last_ts ≤ 26 saat` ile gösterir. XAUUSDT, PAXGUSDT vadeli ve PAXGUSDT spot buna
   dahildir; Dukascopy ya hazırdır ya "yapılamadı" yazar.
3. Boşluklar gerçek fonlama aralıklarıyla dürüstçe sayılır; `archive_unverified` satır sayısı raporlanır.
4. İlk 3 gece arşiv uzlaştırma farkı 0'dır (veya açıklanmıştır).
5. 14 gecelik A/B: P1a'nın bütün ölçütleri **ve** veri birimi ile doldurma pencerelerinde worker 418/429 = 0,
   `DATA_VERDICT_MISSING`/bayat ret oranı değişmez.
6. Veri birimi ve doldurma için `memory.peak` ≤ 0,8 × MemoryMax; gece birimi doldurma yüzünden hiç `SKIPPED_LOCKED`
   olmaz.
7. Disk kullanımı tahminin ±%30'u içindedir.

**Sahibin VPS adımları:**

```
sudo bash tb-engine-<sha7>.sh --dry-run
sudo bash tb-engine-<sha7>.sh            # veri birimi kapılı kurulur + elle smoke
sudo bash tb-engine-<sha7>.sh --backfill  # ilk doldurma, birimle aynı yalıtımla; hata olursa aynı komutla devam
sudo bash tb-engine-<sha7>.sh --check     # 14 gün boyunca her gün
sudo bash tb-engine-<sha7>.sh --ab-report
```

#### P1b uygulama notları (2026-10-06)

Belgenin açık bıraktığı ya da kodla çeliştiği yerlerde uygulamanın seçtiği yorumlar (mühürlü/kayıtlı metin
değişmedi; ayrıntı modül başlıklarındadır: `research_engine/{store,datastore,universe,seed,provider}.py`):

1. **Arşiv önbelleği düzeni.** Dosyalar `archive_cache/archive/<sunucu>/<url yolu>` altındadır (§3.1 tablosu
   `archive_cache/<sunucu>/…` der). Laboratuvarların `ZipCache`/`ArchiveCache`'i `--cache` kökünün altına `archive/`
   ekler; §3.5'teki `--cache /opt/tradingbot/data/research/archive_cache --offline` komutu ancak bu düzenle bayt-özdeş
   dosyaları bulur. 1m zip'leri doğrulanmış alımdan sonra silinir; sha256'ları seri manifestinin dosya defterinde
   (`files`) kalır.
2. **Yeniden kullanım sınırı.** `HistoryStore` alt sınıf olarak, `BudgetPool`/`RateBudget` olduğu gibi kullanıldı.
   `ArchiveClient`/`HistoryCollector`/`IncrementalUpdater`/`HttpClient` kullanılmadı: `.CHECKSUM`'sız zip'i doğrulanmış
   sayarlar, µs'yi değerin büyüklüğünden çözerler, ilk hatada dururlar ve 429'u yeniden denerler — §3.2/§3.4'le çelişir.
   Mantık `datastore`'da bu kurallarla yeniden yazıldı; worker modülleri değişmedi.
3. **Giriş evreni (40).** `state/universe.json` borsadaki bütün uygun sembolleri (≈ 200) taşır; 40'lık liste
   `config.yaml → entry_universe.symbols`'tır. Motor bunu ham YAML'dan okur (`rawconfig` ihtiyaç listesine
   `entry_universe.enabled/symbols` eklendi); okunamazsa `state/frame_provenance.json`, o da yoksa bileşen boş ve
   `ENTRY_UNIVERSE_YOK` bayrağı. `state/universe.json`'un küçük kopyası günlük `universe/AAAA-AA-GG.json`'a girer.
   İşlem görenler: P1a kapanış arşivi + ledger `history[]`/açık pozisyon-lot + `state/**/counterfactual_trades.json` ve
   `state/shadow_book.json` (son 180 gün).
4. **Altın vadeli 1m** P1b'de `max(başlangıç, şimdi − 400 gün)`'den doldurulur (§3.3'ün "1m–1d" satırı ile "1m tembel,
   P2" satırının birleşimi); diğer sembollerin 1m'i P2'de tembeldir.
5. **Mark/premium 1h** türleri `markpx_1h`, `premium_1h` (`markPriceKlines`/`premiumIndexKlines`; sütunlar
   `timestamp, open, high, low, close`); worker'ın `mark_1h` türüyle karışmasın diye ayrı adlıdır.
6. **Fonlama.** data.binance.vision'da `daily/fundingRate` yoktur (2026-10-06 yoklaması 404): içinde bulunulan ay ve
   aylık zip'i henüz yayımlanmamış önceki ay REST `fundingRate`'ten gelir (tek istek, ağırlık 1, ≤ ~62 gün). Aralık:
   dakikaya yuvarlanmış ardışık farkların medyanı; boşluk `round(fark/aralık) − 1` (`calc_time` +0…+26 ms oynar).
   Nokta türlerinde (fonlama, `metrics_5m`) REST/tohum ↔ arşiv zaman damgası ±60 sn içindeki aynı değerli ikiz fark
   sayılmaz; fonlamada yalnız `rate` karşılaştırılır (`mark` yalnız REST'te vardır).
7. **REST kuyruğu.** Yalnız arşiv tabanı olan seriler; kline/mark/premium ve `metrics_5m` için en çok son 2 gün,
   fonlama için yukarıdaki pencere. `metrics_5m` REST eşlemesi `crowd_data.COLUMN_MAP`'tir (orada da "VPS'te
   doğrulanacak" diye işaretli); doğrulanmış gün zip'i kendi gününü yetkili olarak kapsar, arşivde olmayan REST satırları
   kaldırılır ve fark olarak `runs/data/<id>/diffs.jsonl.gz`'a yazılır. 403/451 (erişim engeli) de 418/429 gibi REST
   adımını durdurur. Spot IP limiti `ratelimit`'in muhafazakâr 1200'üdür (pay 120). Jeton kovasının üstüne KAYAN 60 sn
   penceresi konuldu (kova tek başına bir dakikada iki kapasiteye izin verirdi).
8. **`--update` tabansız seriyi atlar** (`NO_BASELINE`); ilk kapsama `--backfill`'in işidir. Böylece boş depoda veri
   biriminin elle smoke çalıştırması dakikalar sürer. Tohum yalnız `--backfill`'de, yalnız BOŞ araştırma serisine ve
   yalnız kline türleri + fonlama için yapılır; worker manifestinde `bad_chunks` varsa kopya tutarsız sayılır
   (yeniden indirme). Satır sayısı tam olan tohum ayları yeniden indirilmez.
9. **Kurtarma tespiti** ucuzdur: yazımdan önce `.inflight.json` işareti, her gece parça `stat`'ı (boyut, mtime_ns)
   manifestle karşılaştırılır; sha256 yalnız şüphede okunur. Haftalık örneklem: Pazar günleri, kararlı özeti
   `% 7 == ISO hafta % 7` olan seriler derin denetlenir (7 haftada bütün depo). "İki ardışık gece" = başarısız yeniden
   çekimin iki ardışık UTC takvim günü (ağ hatası da sayılır); `MANIFEST_LAG` ayının yeniden çekim hatası seriyi
   durdurmaz. Duran seriye yeni satır eklenmez ama yeniden çekim her gece denenir; başarılı olunca seri devam eder.
10. **Mühür.** Parça listesi `store/_seal/<data_seal>.json.gz`'dadır (son 5 tutulur; `store/` yedeğe girmez,
    yeniden üretilebilir). `summary/data_status.json` son TAMAMLANMIŞ çalıştırmanın mührünü ve sürüyorsa `running`
    altında ilerlemeyi taşır. Gece birimi S0'da bu dosyayı düz JSON olarak okur (`selfcheck.check_data_status`; P1b
    modülleri import edilmez) ve `data_seal`'ı `run_status.json`'a yazar; mühür 26 saatten eskiyse `DATA_STALE`
    bayrağı. P1b'de gece aşamalarının hiçbiri depoyu okumadığından plan değişmez (S7b dahil).
11. **Veri birimi S0'ı**: root reddi, PAPER, `data.lock` (alınamazsa `SKIPPED_LOCKED`, çıkış 0, `data_status.json`'a
    dokunulmaz), disk koruması, yalıtım (state/market/app'e yazma reddi, `memory.max` = `ENGINE_EXPECTED_MEMORY_MAX`,
    `/tmp` ve araştırma kökü yazılabilir; soket denemesi `NOT_APPLICABLE`). Çalıştırma kaydı `runs/data/<run_id>/`
    (gece biriminin `runs/<run_id>/` desenine uymaz; son 60 tutulur). Çıkış: 0 SUCCESS/PARTIAL/DEADLINE/SKIPPED_LOCKED,
    1 FAILED, 2 Dukascopy YAPILAMADI, 3 ISOLATION_BROKEN, 4 NOT_PAPER, 5 DISK_REFUSE, 6 ROOT_REFUSED.
12. **4h penceresi kuralı** her ağ isteğinden önce uygulanır (doldurma ve elle çalıştırmalar; 00:41 zamanlayıcısı
    pencereye hiç girmez). `--update` iç son tarihi başlangıç + 45 dk, 00:00–01:26 arasında başladıysa en geç 01:26.
13. **Listelenme**: en son `exchangeinfo/*.json.gz`'deki `onboardDate`; yoksa aylık 1d zip'lerinde ikili arama
    (`store/_meta/listings.json`). `exchangeInfo` anlık görüntüsü vadeli için tam liste (ağırlık 1), spot için U_R'nin
    spot sembolleriyle (`symbols=`, ağırlık 20); REST koruması geçerse günde bir kez.
14. **Dukascopy** içe alma: `.part` yok, her dosya LZMA + kayıt denetiminden geçer, kaynak iki geçişli sha256 ile
    değişmemiş olmalı; biri tutmazsa "yapılamadı" ve depoya hiçbir satır yazılmaz (önceki başarılı içe alma yerinde
    kalır, `last_attempt` not edilir). Kaynağın yayımlanmış checksum'ı olmadığından satırlar `archive_unverified`'dır.
    Günlük (yıllık) dosya yalnız denetlenir ve kopyalanır; 1m dosyaları varsa `duka_1m`'e girer. Aynanın
    `manifest.jsonl`'u isteğe bağlıdır (eksik kayıt sayılır, ölümcül değil).
15. **Depo kabul testi 12** (veri birimi sözleşmesi) birim dosyalarıyla birlikte sürüm/birim aşamasında yazılır; bu
    aşamanın testleri 1–11 ve 13–15'tir. `ENGINE_VERSION` `research_engine_v1_p1b` oldu; motor kod özeti değiştiği için
    sürümden sonra yeni bir A/B dönemi açılır (§2.9, beklenen).
16. **Veri birimi dosyaları** (`deploy/tradingbot-engine-data.{service,timer}`, birim/sürüm aşaması): gece biriminin
    bütün kaynak ve yalıtım satırları AYNEN (Nice/CPU/IO/OOM, `ProtectSystem=strict`, `PrivateTmp`, `ReadWritePaths`
    yalnız `data/research`, `ReadOnlyPaths`, sistem çağrısı ve yetenek kısıtları, ortam listesi §2.3); farklar yalnız
    `PrivateNetwork` YOK, `SupplementaryGroups=systemd-journal` (yalnız bu birimde), komut `engine-data --update`,
    `TimeoutStartSec=50min` ve `MemoryHigh=800M` / `MemoryMax=1G` (ölçülen tepe ≈ 227 MiB; `ENGINE_EXPECTED_MEMORY_MAX
    =1073741824`). `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` ağa (data.binance.vision, fapi/api.binance.com) ve
    DNS'e yeter; `After=`/`Wants=network-online.target`. Zamanlayıcı `*-*-* 00:41:00 UTC`, `AccuracySec=1min` (en geç
    01:32 sert durma; gece birimi 01:37). Depo kabul testi 12 ve iki birimin satır paritesi
    `tests/test_research_engine_data_unit.py`'dedir.
17. **Motor kod özeti** (A/B dönemi, `selfcheck.ENGINE_CODE_GLOBS`) veri birimi dosyalarını da kapsar: P1a gece birimi
    dosyalarını özete katıyordu; veri birimi de motorun çalışma ayarıdır (bellek, yalıtım, zaman), aynı muameleyi görür.
    Yalnız bir app sürümünün yeniden sabitlemesi yine yeni dönem açmaz.
18. **Bağımsız koşucu** (`tests/standalone/run_engine_invariants.py`) P1b depo kabul testlerinin VPS'te anlamlı alt
    kümesini de koşar (1–11, 13–15; P1a numaralarıyla çakışmasın diye 100 eklenerek 101–115; toplam 53 test).
    Parametreli testler koşucuda koşamaz: kabul 5'in günlük koruması ve 418/429 varyantları yalnız depoda koşar,
    koşucudaki eşi `market/http.py` biçimine sabitlenmiş metin kalıbı testidir. Kabul 12 birim DOSYASI testidir; VPS'te
    sürüm betiğinin birim sözleşmesi (anahtar satırlar) ve sha256 sabiti onun yerini tutar.
19. **S7b yedeği** `universe/` ve `exchangeinfo/`'yu da alır (§3.6 P1b eki; mühürlü metin değil, `backup.INCLUDE`):
    küçüktür (günde birkaç on KB) ve yeniden ÜRETİLEMEZ; geri yükleme de bunları kapsar. `store/`, `archive_cache/`,
    `store/_meta`, `runs/data/` yeniden üretilebilir ve girmez.
20. **Sürüm betiği** `deploy/releases/tb-engine-8d0a531.sh` (P1b; TIP 8d0a531, P1a betiğinin kapıları aynen: PAPER,
    root, saat pencereleri, başka sürümün yeniden başlatmasından 3 gün, app ⊑ hedef, disk, worker 6G, doğrulanmış son
    araştırma yedeği, kapılı reload). Kararlar:
    - **Ön koşul P1a:** gece birimi dosyaları P1a'nınkiyle bayt bayt aynı, gece zamanlayıcısı açık ve aktif, engine-app
      4962209 ya da torunu (hedefin torunuysa betik geri sarmaz); değilse hiçbir şeye dokunmadan durur. Gece birimi
      dosyaları değişmediği için yeniden kurulmaz.
    - **Sıra:** veri birimi dosyaları → kapılı reload → engine-app yeniden sabitleme → veri smoke'u (`engine-data
      --update`; boş depoda tabansız seriler atlanır, dakikalar sürer) → gece smoke'u (yeni kod; S0 veri mührünü görmeli)
      → veri zamanlayıcısı. Reload engine-app'ten ÖNCE yapılır: reload kapısı ya da 6G kayması engine-app'e dokunmadan
      geri alınır. Smoke geçmezse veri zamanlayıcısı açılmaz VE engine-app önceki sabitine döner (gece birimi bilinen-iyi
      kodla sürer). Veri smoke'u `REST_GUARD_UNKNOWN`'ı (worker günlüğü okunamadı → `SupplementaryGroups` ya da yetki)
      kabul ETMEZ: §2.8'in "yalnız gerçek VPS" satırındaki günlük okuma böylece dağıtımda doğrulanır.
    - **`--backfill`:** `systemd-run --unit=tb-engine-backfill`, özellikler KURULU veri biriminin (+ drop-in) [Service]
      satırlarından aynen türetilir; yalnız `Type=exec` (oneshot systemd-run'ı saatlerce bekletirdi), `ExecStart` (komut
      `engine-data --backfill`) ve `TimeoutStartSec` (süre sınırı yok) farklıdır. [Unit] satırları (OnFailure drop-in'i)
      geçici birime taşınmaz; izleme `--check`'tedir. Sürerken yeniden çalıştırmak ilerlemeyi/ETA'yı gösterir (yeniden
      başlatmaz); düşmüşse sıfırlanıp sürdürülür. 3 gün kuralı, saat, PAPER, disk (boş ≥ 10 GB + tahminin %130'u − mevcut)
      ve veri biriminin boşta olması kapıdır. Geçici birimin bu özellikleri kabul etmesi yalnız gerçek VPS'te görülür
      (systemd ≥ 245 denetlenir; kabul edilmezse hiçbir şey başlamaz).
    - **`--rollback` = P1a'ya dönüş** (veri birimi + ilk doldurma kalkar, engine-app 4962209'a döner, gece birimi ve
      `data/research` kalır); tam kaldırma ardından P1a betiğinin `--rollback`'idir.
    - **`--import-dukascopy` geçişi YOK:** belge bunu sürüm betiğinden istemez (yalnız `engine-data` alt komutu); hazır
      ayna yokken VPS kabul 2 "yapılamadı" ile karşılanır.
    - **Sapma:** betik ~1.140 satırdır (§9.3 hedefi "< ~800"): P1a'nın gece `--check`/`--ab-report`/K6'sını aynen
      taşır (yeni A/B dönemi bu betikle ölçülür) ve veri bölümünü (V1–V7, veri/doldurma pencereleri) ekler; yine de
      tb-deploy-*'nin dörtte birinden küçüktür. Sandbox testi `tests/test_research_engine_release_p1b.py`.

#### Değişiklik/uygulama notu (2026-10-06, inceleme düzeltmeleri)

`impl/engine-p1b` @ 42666a8'in iki incelemesinin (doğruluk: 1 BLOCKER, 4 MAJOR; güvenlik/işletim: 3 MAJOR) bulguları
düzeltildi. Mühürlü/kayıtlı metin DEĞİŞMEDİ; yukarıdaki 1–20 numaralı notlar o günün kaydıdır, bu not onları günceller.
Kod commit'i (yeni TIP) `008e602`; sürüm betiği artık `deploy/releases/tb-engine-008e602.sh` (`tb-engine-8d0a531.sh`'nin
yerine, `git mv`). Motor kod özeti ve veri birimi dosyası değişti: sürümden sonra yeni bir A/B dönemi açılır (beklenen).

1. **Gün zip yığını (BLOCKER).** Bir ayın gün zip'lerinden biri geçici hata verince önceki günler "bitti" sayılıp hiç
   yazılmadan kayboluyor, çalıştırma yine SUCCESS diyordu; `--update` 62 gün geriye baktığından daha eski aylar kalıcı boş
   kalıyordu. Artık görevler AYIN YAZIMI tamamlanınca "bitti" olur; yeniden deneme ayı baştan alır (zip'ler önbellekten,
   yeniden indirilmez). Denemeler tükenirse seri `STALE` olur ve açık dönemler `holes` olarak sayılır (`ARCHIVE_HOLES`,
   sonuç `PARTIAL`). `--update` pencerenin DIŞINDAKİ açık dönemleri (defteri kapanmamış ay/gün ve doğrulanmamış `u`
   dosyalar — §3.2 "sonraki gecelerde checksum yeniden aranır") çalıştırma başına en çok 200 görevle onarır (önce hiç
   kapsanmamışlar, sonra en uzun süredir denenmeyen doğrulamalar); sığmayan kısım `holes`'a yazılır.
2. **REST'te "kapanmış bar" isteğin GÖNDERİLDİĞİ ana göredir** (önceden yazım anına göre: 00:44:59,8'de giden 0,4 sn'lik
   istek 00:40 barını `_src=rest` yazıyordu). `metrics_5m`'de yalnız beş uç noktanın ilki gönderildiğinde var olan
   noktalar yazılır (yarım satır yok).
3. **Worker günlüğü koruması** (§3.4 madde 2) REST'ten hemen önce yeniden okunur ve her REST gönderiminden önce sonucu
   300 sn'den (`GUARD_MAX_AGE_S`) eski olamaz; önceden çalıştırma başında bir kez okunuyordu (ilk doldurmada REST adımı
   10–20 saat sonra geliyordu). Geçmezse kalan REST tamamen atlanır (`REST_GUARD_SKIP`/`REST_GUARD_UNKNOWN`; o gece
   yalnız arşiv, seriler `STALE` yapılmaz). Her okuma `rest.checks`'e yazılır.
4. **Arşiv 403/451** "dosya yok" değildir (laboratuvarlar gibi yalnız 404 eksiktir): arşiv adımı o çalıştırmada durur
   (`ARCHIVE_HALTED_<kod>`), kalıcı `.missing` ya da manifest `m` YAZILMAZ, kalan seriler `STALE`, sonraki çalıştırma aynı
   dosyaları dener. 5xx/408/429 geçicidir.
5. **Fonlama boşlukları yerel aralıkla** sayılır: her fark kendisi dahil ±3 komşu farkın medyanına bölünür, bütün seri
   üzerinde ve ay sınırları dahil (parça başına fark koşuları `part_meta.diffs`'te; parça okunmaz). Ay içinde 8h → 4h
   değişimi önceden 36 sahte boşluk (kalite 0,8) sayıyordu (uygulama notu 6'nın "ayın medyanı" kuralı). Parçanın
   `interval_ms`'i ayın medyanı olarak kalır (§3.2); manifestinki serinin EN SON yerel aralığıdır. Kalan sınır: serinin
   ilk üç uzlaşmasında aralık değişirse (önceki veri yok) bir sahte boşluk sayılabilir.
6. **Delist (terminal `DELISTED`).** Vadeli `exchangeInfo`'da durum TRADING değil ya da (sağlam) tam listede yok; spot
   `symbols=[…]` isteği `-1121` ile bütünüyle reddedilirse semboller tek tek sorulur ve yok olanlar `INVALID_SYMBOL`
   yazılır; ya da REST `-1121 Invalid symbol`. Veri delist anına kadar kalır, REST çağrılmaz, seri bayat ya da `PARTIAL`
   sayılmaz; `data_status.delisted`, `totals.delisted`, `engine-data --status` ve `--check` V2'de ayrı raporlanır.
   `exchangeInfo` sembolü yeniden TRADING gösterirse kayıt kalkar. Kayıt `store/_meta/delisted.json`. Önceden delist
   olmuş bir sembol her gece `STALE` → sonsuza dek `PARTIAL` ve V2/V3/V7 hiç değerlendirilmiyordu.
7. **Disk önceliği: P1a kapanış arşivi (gece S1a, yeniden üretilemez) depo ya da zip aynası yüzünden ASLA aç kalmaz.**
   Veri birimi ve ilk doldurma boş < 13 GB ya da araştırma kökü ≥ 17 GB'ta durur (`DATA_DISK_MARGIN` = 3 GB; gece
   biriminin reddi 10 / 20 GB). Denetim başlangıçta VE döngünün içinde yapılır (her ağ isteğinden ve her tohum serisinden
   önce; `statvfs` ucuzdur, araştırma boyutu son ölçüm + boş alandaki düşüşle muhafazakâr tahmin edilir, 30 dk'da bir
   yeniden ölçülür); sınırda çalıştırma `DISK_REFUSE` (çıkış 5) ile durur, o ana kadar yazılan veri mühürlenir, kalan
   seriler `SKIPPED_DISK`. İlerlemede (`running.progress.disk`) boş/kullanılan alan; `--check`'te "disk şimdi" satırı.
   `--backfill` kapısı smoke'un `universe/<gün>.json` seri listesinden tahminle (× 1,3 − mevcut) ve veri biriminin
   sınırlarıyla (boş ≥ 13 GB + ihtiyaç, araştırma + ihtiyaç ≤ 17 GB) çalışır.
8. **Bütçe beklemesinden sonra** saat penceresi, son tarih ve disk yeniden denetlenir (bekleme isteği 4h penceresine
   taşıyamaz; kayan pencereye yazılan an gönderim anıdır).
9. **REST durması** (418/429/403/451) ilgili serileri `STALE` yapar (§3.4 tablosu "ilgili seriler STALE olur").
10. **Sahte HALTED:** önceki ay CORRUPT ve aylık zip'i henüz yokken ay gün zip'lerinden (yeniden çekimde defterden
    bağımsız BÜTÜN günler) kurulur ve bu başarılı yeniden çekimdir; önceden her durumda "başarısız gece" yazılıyor, iki
    gece sonra seri sahte `HALTED` oluyordu. Yakın dönem fonlama ayı (gün zip'i yok) kuyrukta bekler, başarısız sayılmaz.
11. **Tohum** (§3.2 "hiçbir koşulda tüm işi başarısız saymaz"): NaN zaman damgası (önceden `astype` try dışındaydı →
    bütün ilk doldurma FAILED) ve beklenmeyen her hata o seriyi yeniden indirmeye düşürür (`SEED_ERROR`). Tohum ve
    kurtarma da 4h pencerelerinde bekler (tohum worker deposunu okur; IndexRefresher o pencerede yazar).
12. **Dukascopy içe alma:** depo satırları önce geçici bir depoda (mevcut seri kopyalanarak, aynı kurallarla) kurulur; yer
    değiştirme yalnız yeniden adlandırmadır. Önceden yerine koymadan sonra depo yazımı yarıda kalırsa önceki HAZIR içe
    almanın yanında yarım satırlar kalıyordu.
13. **Giriş evreni** listesi yalnız `entry_universe.enabled: true` iken kullanılır (worker gibi; varsayılan false;
    kapalıyken `frame_provenance.json` da boş liste yazar → `ENTRY_UNIVERSE_YOK`, işlem görenler yine U_R'dedir).
14. **SIGTERM** (`systemctl stop`) `STOPPED` (çıkış 1) olarak kayda geçer; `data_status.json`'da bayat `running` bloğu
    kalmaz, mühür yenilenmez. SIGKILL sonrası kalan blokta süreç yoksa okuyucu "DURDU (süreç … yok)" yazar.
15. **`engine-restore --yes`** `analysis.lock`'un yanında `data.lock`'u da alır (`store/` ve `archive_cache/` de kenara
    alınır; veri birimi ya da ilk doldurma yazarken eşzamanlı yazıcı kalmamalı).
16. **Veri birimi dosyası** (`deploy/tradingbot-engine-data.service`, sha256 `9a01ba82…`): ağlı TEK birim olduğundan aynı
    kullanıcının okuyabildiği sır yolları çekirdek düzeyinde erişilemez:
    `InaccessiblePaths=-/opt/tradingbot/env -/opt/tradingbot/.ssh -/opt/tradingbot/data/vault -/opt/tradingbot/data/backups`
    (yollar `deploy/setup_vps_v3.sh` — `ENVFILE`, `$BASE/.ssh`, `$DATA/vault` — ile worker ve yedek birimlerinden; servis
    kullanıcısının HOME'u `/opt/tradingbot` olduğundan `ProtectHome=yes` bunları kapsamıyordu; saatlik yedek vault'u da
    kopyalar; `/home`, `/root`, `/run/user` zaten `ProtectHome` ile kapalı; `-` = yoksa sorun değil). Motor bunların
    hiçbirini okumaz; state salt-okunur okunur, yazım yalnız `data/research`. Gece birimi P1a'ya bayt bayt sabittir ve
    değişmedi (ağsızdır; okuyabileceği sırrı dışarı çıkaramaz). **§2.5 bellek kararı (kayıt):** MemoryMax toplamı
    worker 6G + panel 0,5G + gece 0,5G + veri/doldurma 1G = 8 GiB, 7,7 GiB'lık VPS'te RAM − 1G'ye (6,7 GiB) HİÇBİR motor
    değeriyle sığmaz (worker + panel tek başına 6,5 GiB). Belgenin kuralı gereği `MemoryHigh` düşürüldü: veri birimi ve
    ilk doldurma 800M → **512M** (ölçülen tepe ≈ 227 MiB; ≈ 2,3 kat pay; üstünde çekirdek motoru geri kazanır/yavaşlatır,
    worker'ı değil), gece birimi zaten 400M; `MemoryMax` (1G) ve `ENGINE_EXPECTED_MEMORY_MAX` aynı; `OOMScoreAdjust=1000`
    motorun önce ölmesini sağlar. Betiğin bellek uyarısı bu kararı yazar ve durdurmaz (toplam, gerçek kullanım değil
    sınır toplamıdır; worker'ın ölçülen tepesi ≈ 3,1 GB); gerçek etki VPS kabul 6 (`memory.peak`) ve A/B (NRestarts, tur
    p95) ile ölçülür. Betiğin ağır adımları ayrıca `choom -n 1000` ile koşar (önceden `oom_score_adj` 0).
17. **Tahminler (düzeltildi).** `metrics` 2021-12-01'den ~45 sembolde ≈ 159 bin istek (gün zip'i + `.CHECKSUM`); aylık
    kline/mark/premium/fonlama ≈ 40 bin; toplam ≈ **200 bin istek** (§2.2'nin "≈ 150 bin"i ve önceki betiğin "~170 bin"i
    düşüktü), 4h duraklamaları dahil ≈ 13–32 saat. Disk ≈ **6 GB** (parquet ≈ 3,1 + zip ≈ 2,3 + ≈ 0,5 GB blok payı: ≈ 200
    bin küçük dosya, `du -sb` görmez). Betik ve `--check` artık blok baytını (`du -sB1`) ölçer ve ilk doldurma tahminini
    smoke'un evren listesinden seri başına kurar (evren sınırsızdır; ~45 sembol sabiti değil).
18. **Sürüm betiği** `tb-engine-008e602.sh`: hedef commit, birimler kurulmadan ÖNCE geçici klondan engine-app nesnelerine
    eklenir (önceden birimler kurulup reload edildikten SONRA GitHub'dan getiriliyordu; getirme hatası "hiçbir şey
    değişmedi" diyordu); adım 7'den sonra beklenmeyen bir hata engine-app'i önceki sabitine döndürür (veri zamanlayıcısı
    henüz açılmadıysa; smoke edilmemiş kod P1a gece zamanlayıcısıyla kalmaz); `--rollback` engine-app'i geri
    sabitleyemezse (gece penceresi, app ata değil, yerel değişiklik) ya da reload atlanırsa BAŞARI demez: çıkış 4 ve
    "GERİ ALMA YARIM KALDI"; `--check`'te `du`/pipefail hatası (doldurma sürerken silinen dosya → "N\n0") giderildi,
    "disk şimdi" ve IO zamanlayıcısı satırları eklendi; V1 betiğin adını yazar (`<betik>` değil). Bağımsız koşucu 57 test
    (kabul 103/104/105/109'un güçlendirilmiş testleri eklendi). Betik 1.200 satırdır.
19. **Bilerek yapılmayanlar (gerekçe).** İlk doldurmaya süre sınırı (`RuntimeMaxSec`) konmadı: kaldığı yerden sürer, gece
    birimi ayrı kilitle etkilenmez, P1b'de gece aşamaları depoyu okumadığından uzun doldurmadaki `DATA_STALE` yalnız
    bayraktır; bir sınır meşru bir doldurmayı öldürüp yeniden başlatmaktan başka bir şey yapmazdı. Zip önbelleğinin fsync
    sayısı azaltılmadı (atomik yazım sözleşmesi; IO etkisi A/B'de ölçülür, `--check` IO zamanlayıcısını gösterir).
    Dukascopy 1h boşlukları hafta sonu/tatil saatlerini de sayar (tarihsel seridir; tazelik ve V3 kabul ölçütlerine
    girmez). Gece birimine `InaccessiblePaths` eklenmedi (P1a'ya bayt bayt sabit; ağsız). Yedek dal
    (`refs/heads/claude/gifted-knuth-0ehpcs`) TIP'i henüz içermez: dağıtımdan önce PR dalı ileri sarılır (betik içeriği
    tam SHA ile getirir).
20. **2026-10-06, yeniden doğrulama küçükleri** (930118f'in son doğrulaması GEÇTİ; 7 küçük bulgu kapatıldı, kod `1b1feb9`,
    betik `tb-engine-1b1feb9.sh` `git mv` ile `tb-engine-008e602.sh`'nin yerine): araştırma sınırındaki red tahminle değil
    `du` yeniden ölçümüyle verilir; adım 7'de `engine-app=` değişikliği sabitlemeden ÖNCE kaydedilir, `revert_eng`
    gerçek çıkışı döndürür ("geri döndürülemedi" dürüstçe yazılır) ve `on_err` `$(…)` içindeki hatada yalnız üst kabukta
    bir kez çalışır; `data_run.json` `memory.events` high ve `memory.high` tutar, `--check` V6/`--backfill` bunu basar
    (bilgi); TRADING-dışı durum açık kanıt yoksa ancak art arda iki `exchangeInfo` görüntüsünde `DELISTED` olur, bilinen
    delist spot semboller toplu isteğe konmaz; veri birimi `InaccessiblePaths`'e `/opt/tradingbot/env.d` eklendi (sha256
    `d6bf3108…`); yalnız iki fonlama farkı varken yerel aralık küçük olandır.
21. **2026-10-08, VPS soyuna yeniden oynatma.** VPS'te çalışan soy: app `db5db96` (hızlı kesin kNN worker sürümü,
    2026-10-07 19:31 UTC; `deploy-logs/db5db96-restart-at.txt`), P1a `tb-engine-4962209.sh` ile 2026-10-07 18:20 UTC'de
    kuruldu (1 günlük pencere kapısı, sha256 `6a33bb9b…`) ve worker sürümü engine-app'i AYNI motor koduyla `db5db96`'ya
    yeniden sabitledi. P1b'nin on commit'i (`94f764f` … `14500d4`, `8d37d05` tabanlı) bu soyun üstüne sırayla
    `cherry-pick` edildi; çakışma çıkmadı (`test_research_engine_release.py`'de P1a'nın 1 gün beklentileri ile P1b'nin
    "yalnız kendi geçici klasörlerini say" düzeltmesi birlikte durur). Kod TIP'i `57cfef1` (`1b1feb9`'un yeniden
    oynatılmışı; `tradingbot/research_engine`, dört birim dosyası, bağımsız koşucu ve fikstürler `1b1feb9` ile bayt bayt
    aynı, dolayısıyla birim sha256 sabitleri ve A/B motor kod özeti değişmez; `db5db96` hedefin atası). Betik
    `git mv` ile `tb-engine-57cfef1.sh` (`tb-engine-1b1feb9.sh`'nin yerine). Betikteki iki değişiklik:
    (a) **Pencere kapısı 1 gün** (`1g-pencere-dışı`): sahip kararı 2026-10-07 "öğrenmeyi şimdi kur" P1a betiğinde 3 günü
    1 güne indirdi; aynı kural P1b'ye uygulandı (P1b betiğinde 3 gündü). İki temiz `--check` yine sahip + inceleyici
    yargısıdır; `--backfill`'in başlatılması da aynı kapıya bağlıdır. VPS'teki `db5db96-restart-at.txt` (2026-10-07
    ~19:31 UTC) ile en erken an 2026-10-08 ~19:31 UTC'dir; saat penceresiyle ilk uygun aralık 20:40–23:15 UTC.
    (b) **`--rollback` hedefi P1a motor kodudur:** app `4962209`'un atasıysa `4962209` (değişmedi); app `4962209`'un
    torunuysa ve `tradingbot/research_engine` + gece birimi dosyaları `4962209`'dakiyle AYNIysa app'in SHA'sı (VPS'te
    `db5db96`). Önceki betik app `db5db96` iken `4962209`'a dönemez, "BIRAKILDI" deyip çıkış 4 ile YARIM kalırdı. Ön koşul
    denetimi zaten engine-app'in `4962209`'un HERHANGİ bir torununda olmasını kabul ediyordu; sahte VPS'te app VE
    engine-app `db5db96` iken 1 günden önce red, sonra dağıtım ve `db5db96`'ya geri alma testle kanıtlandı
    (`test_vps_state_engine_app_at_a_p1a_descendant_deploys_after_one_day_and_rolls_back_to_it`). Betik 1.200 satır kaldı.

### P2 — İşlem günlüğü, yol, rehydrate, fidelity, atıf, UTC günü MTM, özet

**Teslimatlar:**
- `journal.py` (`tj_v1`, vadeli + spot, P1a arşivinden), `pathrec.py`, `rehydrate.py` (hedefler + sinyal bağlamı; stop
  ölçülmüş), `fidelity.py`, `attribution.py` (`attribution_v1` mühürlü), `cfgrid.py` (`cfgrid_v1` mühürlü;
  EX_ANTE/HINDSIGHT; eşleşmiş rastgele kontrol; medyan referanslı ayrıştırma);
- UTC günü MTM (`LAST_PRICE_PROXY`, store 1m), 14 gün W-günüyle yan yana, sonra `tgt_v2`;
- `digest_tr.md` tam sürüm;
- `engine-query` (`trade`, `why-lost`, `day`);
- gece S1b, S2;
- tembel 1m doldurma.

Birim dosyası değişmez; yalnız sabit taşınır. **Değişiklik (2026-10-08, inceleme M6):** gece birimi P2'de
`MemoryHigh=640M` / `MemoryMax=768M` (`ENGINE_EXPECTED_MEMORY_MAX=805306368`) olur; VPS'teki P1a birimi P2 sürüm betiği
kurana kadar 400M/512M kalır (P2 uygulama notu 34).

**Kabul:**
1. Her defterin (spot dahil) her `TradeRecord`'u günlükte tam bir kez bulunur; Σ net_pnl, ücret, fonlama ve kayma
   1e-6 ile eşittir.
2. Sahibin her alanı ya doludur ya `MISSING`/`MODELED`/`NOT_APPLICABLE` etiketlidir.
3. Ücret özdeşliği ve çift sayım regresyon testi geçer.
4. R paydası `features.risk_usdt`; motorun `net_r`'ı ledger `r_multiple` ile eşit (1e-9; geç fonlama sonrası da).
5. Fidelity ≥ %90; başarısızlıklar nedenleriyle listelenir.
6. Rehydrate eşleşmesi (giriş ve ölçülmüş stop'a 1 tick) Box/D4/T2/M2/C4'te, config dönemi başına ≥ %95.
7. Her kod ve ayrıştırma için sentetik yol altın testleri geçer (ayrıştırma ± artık = net_R).
8. Aynı mühür aynı bayt-özdeş çıktıyı verir.
9. İleriye bakma (lookahead) yoktur: `regime_fit` `as_of < opened_at`.
10. UTC günü MTM, W-günü `LEDGER_MARK` ile yan yana; 00:00 cüzdanı arşivlenmiş hareketlerle 1e-6 tutar.
11. Box stop-genişliği bulgusu, eski config döneminde gerçek veriden yeniden üretilir (§5.10).
12. Özet ≤ 8 KB, sorgular ≤ 150 satır.
13. 3 ardışık gece 03:40'tan önce `SUCCESS`; A/B'de tur etkisi ≤ %5.

**Sahip:** dry-run, deploy (worker durmaz), günlük `--check`. İlk haftalık özeti isteğe bağlı olarak bir AI
incelemesine yapıştırır.

#### P2 uygulama notları (2026-10-06)

P2a (günlük, yol, rehydrate, fidelity, UTC günü MTM, tembel 1m) için belgenin açık bıraktığı yerlerde uygulamanın
seçtiği yorumlar. Mühürlü/kayıtlı metin DEĞİŞMEDİ; ayrıntı modül başlıklarındadır:
`research_engine/{journal,pathrec,rehydrate,fidelity,utc_day,lazy1m}.py`. P2b (atıf, ızgara, özet, sorgu, S1b/S2
bağlantısı) bu notların üstüne kurulur.

1. **Yol penceresi (§5.1).** İşlem barları `opened < ts < closed` (bar açılışı): girişi içeren bar yolda DEĞİLDİR
   (girişten önceki fiyatları taşır; `learning_cf._net_replay`'in `ts > created` kuralı), çıkışı içeren bar yoldadır;
   kapanış tam bar sınırındaysa o anda BAŞLAYAN bar kuyruğun ilk barıdır. Kuyruk 48 bar (`phase=1`). Kaynak: 1m
   eksiksizse 1m, değilse 5m eksiksizse 5m, ikisi de eksikse doluluğu yüksek olan (boşluk sayısı `gaps`), ana botta
   sonra `position_path`, en son defterin uçları (`EXTREMES_ONLY`, sıra yok). Zamanlar (`t_mfe`, `t_mae`,
   `time_to_1r`) açılıştan ucun oluştuğu barın KAPANIŞINA dakikadır. Depo mühürlü okunur (`SealedReader`) ve
   `StoreProvider`'ın parite kapısıyla: `archive_unverified` satırlar kullanılmaz (o gün yolda eksik sayılır; doğrulama
   gelince parça değişir ve satır yeniden kurulur). Dosya `paths/YYYY-MM/<anahtar>~<sha10>.parquet` (yeniden
   üretilebilir, yedeğe girmez).
2. **Belirsiz bar içi sıra.** MFE ve MAE aynı barda oluştuysa `order = MAE_FIRST` (kural sırası) ama
   `order_ambiguous = true`; ilk işlem barı dışında iki koşan ucun AYNI barda yenilendiği barlar `ambiguous_bars`'ta
   sayılır. P2b sıraya dayanan kodlarda bu işlemleri dışlar.
3. **Rehydrate "giriş referansı" denetimi (§5.2).** Defter girişi canlı mark'tan yapılır; kural fiyatına eşit olamaz.
   Denetim: (a) kaydın okuduğu sinyal barının (`data_source.bars[tf]`) depoda bulunması ve yeniden hesaplanan sinyal
   barıyla aynı olması, (b) kuralın `signal_close`'unun işlem hafızasındaki ölçülmüş kopyasıyla 1 tick, (c) C4'te
   `definition_sha` eşitliği ve formasyon uçlarının 1 tick, (d) girişin stopun doğru tarafında olması; ayrıca yeniden
   hesaplanan ilk stop ölçülmüş `features.initial_stop` ile 1 tick. **Tick** = giriş dolum fiyatının yazıldığı ondalık
   hassasiyet (defter `price_tick`'e kuantize eder; "216.60" → 0,01). Çerçeve canlı motorunkiyle aynı kurulur
   (`PERP_FRAME_LIMITS` − oluşan bar; günlükte `ema200`/`atr14` aynı fonksiyonlar).
4. **Box `min_stop_pct` bir filtredir** (geometri değil; dönemle değişti): rehydrate'te 0 alınır
   (`filter_params_ignored`). Box `variation_id` bu yüzden filtre parametresi nötrlenmiş geometri etiketidir
   (`params_label`, `_ms…` yok); T2/M2 `atr<çarpan>`, D4 `donchian_20_10`, C4 varyasyon kimliği. `params_hash` P6'ya
   kadar `sha256(kural modülü kaynağı + parametreler)`, C4'te `definition_sha`. Parametreler canlı config'in HAM
   YAML'ından okunur (`rawconfig` ihtiyaç listesine `strategy_paper[.extra[]].atr_mult/rule_params` eklendi).
5. **Config dönemi (§6.2), P3'e kadar geçici.** `library/config_epochs.json` yokken dönem belgedeki sahip kararlarından
   türetilir: 2026-09-30 (yalnız Box, `slots`) ve 2026-10-03 (bütün defterler); kimlik `E0` / `E<sınır günü>`, etiket
   `RECONSTRUCTED`, kaynak `PROVISIONAL_DOC_BOUNDARIES`. Mühürlü dosya gelince o kullanılır (`LIBRARY_CONFIG_EPOCHS`).
6. **Fidelity (§5.3).** Yeniden oynatmanın giriş referansı kaydın giriş dolumunun `ref_price`'ıdır (kaymayı defter yine
   uygular); `_net_replay` R'yi kendi boyutunun riskine böldüğünden sonuç kaydın paydasına
   `r_net × |dolum_replay − stop| / |dolum_gerçek − stop|` ile çevrilir. Funding kaydın KENDİ settlement'larıdır (oran +
   mark; kayıtta olmayan dönem watermark'a kadar 0). Yürütme modeli ayarları ledger JSON'unun GÜNCEL alanlarıdır
   (`ExecModel.of_ledger`'ın okuduğu alanlar). Oranın paydasına girmeyenler ayrı sayılır: `SPOT`, `NO_RISK`, `NO_PATH`,
   `NO_EXEC_MODEL`, `TARGETS_MISSING` (hedefle çıkmış ama hedef bilinmiyor), `RECORD_UNREADABLE`. Başarısızlık nedenleri
   (öncelik sırasıyla): `PATH_GAP`, `AMBIGUOUS_BAR`, `FILL_BASIS`, `EXIT_REASON_DIFFERS`, `BACKDATED_CLOSE`,
   `ENTRY_FILL`, `REPLAY_ERROR`, `UNEXPLAINED`.
7. **Günlük alanları (§4.3).** Ölçülmüş alanlardan saf aritmetikle türetilen değer `MEASURED`'dır; depo/kural girdisi
   olan `RECONSTRUCTED`. `exit_basis`: `GAP` yalnız dolum seviyenin ÖTESİNDEyse (canlı izleyici seviyeyi tam gözlediyse
   `LEVEL`), likidasyon tabanları `LIQUIDATION`, `exit_fill` yoksa ve hedef değilse `MARKET`. `session`: hafta sonu
   `WEEKEND`, diğer günler ASIA [21:00, 07:00), LONDON [07:00, 13:00), NY [13:00, 21:00) UTC. `equity_at_entry`:
   `features.learning.equity_basis` (MEASURED), yoksa girişten önceki son gece görüntüsü (RECONSTRUCTED).
   `closed_at_backdated`: `exit_fill.first_source` (BAR_OPEN/UNKNOWN → geriye tarihli). `spread_est` kaydedilmediği için
   `MISSING`. `RESTORED_AWAY` kayıtlar günlüğe girmez. Ay dosyası kaydın `closed_at` ayıdır; satırın `data_seal`'ı
   okuduğu depo parçalarının `ResearchStore.seal_of`'udur; `_build.json` yalnız içeriği özetler (aynı mühür → aynı bayt).
8. **İleriye bakma (kabul 9).** Giriş bağlamı `as_of = opened_at − 1 ms` ile yalnız o anda KAPANMIŞ barlardan
   (`situation_v1`, BTC, 1d yer ölçüleri, fonlama, OI/taker); çıkış bağlamı `as_of = closed_at`. Spot işlemin yer
   ölçüleri ve `situation_v1`'i depoda varsa aynı sembolün vadeli barlarından hesaplanır (alanın notunda "vadeli 1d
   vekil"); yol ise spot barlarındandır.
9. **Spot.** Stop ve R paydası ana botun provenance'ından (alış emri = ilk FIFO lotunun dolum kimliğinin emir kısmı,
   `S000123-1` → `S000123`); yoksa R alanları `MISSING`. Fidelity'de `SPOT` (uygun değil).
10. **Tembel 1m (§3.3).** Gece (S1b) yolu eksiksiz 1m olmayan işlemlerin günlerini `paths/needs_1m.json`'a yazar (son
    120 günde kapanmış işlemler, son 400 gün, bugün hariç, en çok 400 gün — en yeni önce). Veri birimi REST adımından
    sonra bu günlerin YALNIZ arşiv gün zip'lerini P1b kurallarıyla çeker (`.CHECKSUM`, pencereler, disk, yeniden deneme;
    REST YOK); tembel seriler planlı değildir ve bayat sayılmaz (`lazy: true`); özet `data_status.last_run.lazy_1m`.
11. **UTC günü (§7.1).** `W(T)` iki çapadan hesaplanır ve eşitliği denetlenir (`anchor_check`); ayrıca
    `ΔW = Σ hareket` (`entries_check`). İlk çapadan önceki 00:00: çapada hareket listesi doluysa (rotasyon) ve elde kalan
    en eski hareket T'den sonraysa `EKSİK` (`HAREKETLER_ARŞİVDEN_ÖNCE_DÖNDÜ`). 00:00'da açık pozisyon arşiv kaydından
    (`opened ≤ T < closed`, miktar = ilk miktar − `ts ≤ T` çıkış dolumları), kayıt henüz arşivde yoksa çapadaki
    pozisyondan; T ile çapa arasında kısmi çıkış varsa miktar bilinmez → `EKSİK` (`U_QTY_UNKNOWN`), kayıt arşive girince
    satır `rev+1` ile düzelir. Satırlar ayrı dosyadadır (`target/daily_utc.jsonl`, yalnız eklenir); P1a `tgt_v1` satırları
    ve başlığı değişmez. Başlık, W-günü satırıyla yan yana yayımlanmış (bugünden önceki) 14 günden sonra `tgt_v2`'ye
    geçer; tanım `TGT_V2_SHA` ile mühürlüdür.
12. **Bağlantılar (P2b).** `journal.run_s1b` ve `utc_day.run_utc_day` yazıldı ama gece birimine bağlanmadı (P2b). S1b
    mühürlü depoyu okur (`provider`/`store`): §2.8 gece grafiğinden yalnız ağ modüllerini (`datastore`, `pit_universe`)
    dışlar; P1b'nin gece import-grafiği testi `store`/`provider`'ı da dışladığından P2b bağlarken o testi §2.8'e göre
    daraltmalıdır. Paket dışı import beyaz listesi (P1b sözleşme testi) P2 modülleri için genişletildi: kural
    modülleri (`paper_rules`, `ema200_trend`, `box_theory`, `donchian_trend`, `candle_*`), `learning_cf` +
    `learn.shadow` + `accounting` (cf_label_v3 kod yolu), `indicators`, `shared_experience`, `learning_mode` — hepsi
    salt-okunur hesap.


#### P2b uygulama notları (2026-10-06)

P2b (atıf, karşı-olgusal ızgara, R ayrıştırması, özet, sorgu, S1b/S2 bağlantısı) için belgenin açık bıraktığı yerlerde
uygulamanın seçtiği yorumlar (yukarıdaki 1–12'nin devamı). Mühürlü/kayıtlı metin DEĞİŞMEDİ; ayrıntı modül başlıklarındadır:
`research_engine/{attribution,cfgrid,analysis,report,query}.py`, `night.py`, `journal.py` (P2b ekleri).

13. **Mühürler (§9.2).** `attribution_v1` = `ATTRIBUTION_SHA` `3f4596e37822f1603db6c296937005d45bcfd0215a18f1dc993ba535a0236626`; `cfgrid_v1` = `CFGRID_SHA`
    `4a96df71a989bcf9c5f81317c01e534920105ced1785dc9389240502abc9454d` (tanım sözlükleri `ATTRIBUTION_SPEC` / `CFGRID_SPEC`, sha'lar `tests/test_research_engine_attribution.py`
    ve `tests/test_research_engine_cfgrid.py`'de sabit; değişiklik = `_v2` + yeni deneme sayımı). Kayıtlar gerçek bir
    işlem sonucu görülmeden, yalnız sentetik altın yollarla yazıldı. Tasarım sırasında TEK tanım değişti: `NOISE_LOSS`
    (aşağıda madde 14), altın yolu yazılırken tanımın mantıken boş olduğu görüldüğü için (sonuç görülerek değil).
14. **Atıf kodlarının okuması (§5.4 özet tablosu).** Sonuç sınıfı `net_r > 0` → kazanç kodları, aksi halde kayıp kodları;
    R'siz satırda `net_pnl` işareti, R gerektiren kod `not_evaluable` (tahmin yok). `r_gross` = kayma ÖNCESİ brüt
    (`gross_r + slippage_in_fills`; kayma dolumun içindedir, yoksa `COST_KILLED_SLIPPAGE` hiç oluşamazdı); `cost_R` = ücret
    + kayma + net fonlama; `funding_R` = −fonlama maliyeti. Sıraya dayanan kodlar (LATE_ENTRY'nin MAE kolu, LUCKY_WIN,
    TREND_CONTINUATION, CLEAN_ENTRY) yol bar değilse ya da bar içi sıra belirsizse (`order_ambiguous` / `ambiguous_bars > 0`,
    P2 notu 2) değerlendirilmez. GIVEBACK'in `capture < 0,3` kolu yalnız MFE ≥ 0,3R iken (aksi halde her kayıp bu kodu
    alırdı; 0,3 belgenin WRONG_DIRECTION eşiğidir). STOPPED_THEN_REVERSED: "orijinal hedef" `targets[0]`, hedefsiz
    kuralda (T2/M2/D4) +1R; ufuk = kuyruk (48 bar) ∩ kuralın tutma sınırı. STOP_TOO_TIGHT'ın gürültü bandı: aynı defterin
    bu işlem AÇILMADAN önce kapanmış son 200 kazancının |MAE %| medyanı (n ≥ 30); ATR kolu giriş diliminde (`ENTRY_TF`:
    T2/M2 1d, Box 5m, D4/C4/Formasyon/ana bot 4h). LATE_ENTRY dolum kolu kuralın `signal_close`'u ve ATR'si (rehydrate).
    BREAKEVEN_SAVED = başa-baş stop çıkışı (kazanç). AGAINST_BTC = giriş anı BTC 4h trendi yöne ters VE BTC giriş→çıkış
    aleyhte (5m kapanışları). VOL_SPIKE = giriş diliminde ATR%(çıkış) ≥ 1,5 × ATR%(giriş). Ana bot kodları provenance
    `entry_features`'tan (`n_dissent ≥ 1`; `n_dissent + n_vetoes ≥ 5`, postmortem eşiği; `rr < 2`, yoksa provenance
    stop/hedefinden). **NOISE_LOSS:** "|net_R| maliyet bandında"nın harfiyen okuması (net ∈ [−cost_R, 0]) yalnız r_gross ≥ 0
    iken mümkündür ve r_gross > 0 işlem zaten COST_KILLED alır — kod yalnız r_gross = 0'da oluşurdu; bant bu yüzden brüt
    hareket üzerindedir: başka kayıp kodu yok ve |r_gross| ≤ cost_R. Hiç kod tutmazsa birincil kod `None`
    ("SINIFLANMADI"; sayılır, uydurulmaz). Birincil öncelik: likidasyon, boşluk, maliyet, stop-sonra-dönüş, yön, dar stop,
    geç giriş, geri verme, … (`LOSS_PRIORITY` / `WIN_PRIORITY`; şans kodları kazançta önce — dürüstlük).
15. **Izgara (§5.5).** 18 hücre, her biri gerçek işlemden TEK eksende ayrılır (belge "yaklaşık 24"): giriş (gerçek; 1 bar
    — giriş diliminde — gecikmeli; referans − 0,25 ATR limit, 1 bar geçerli, dolmazsa R = 0 `MISSED`), stop {0,75; 1;
    1,5; 2} × ATR14, çıkış (tek hedef 1R/2R/3R; 2×ATR iz süren stop; hedefsiz + zaman stopu), yönetim (TP1 %50 açık/kapalı;
    1R'da başa-baş açık/kapalı), yarı kaldıraç, atla. Hepsi EX_ANTE; işlem başına en iyi hücre `hindsight_best` (HINDSIGHT,
    "kural değil") ve "kâra dönen hücreler" de HINDSIGHT etiketlidir. Ufuk: gerçek çıkış seviyeyle olduysa yol sonu
    (kuyruk) ∩ tutma sınırı; kural/zaman çıkışıysa gerçek çıkış anı (kuralın kararı yeniden üretilmez, zamanı korunur).
    Yeniden oynatma `learning_cf._net_replay`; iz süren stop ve gerçek kaldıraç için döngünün kancalı KOPYASI
    (`cfgrid._replay_ext`) — kancasız hâli `_net_replay` ile bayt-özdeş (test). Fonlama kaydın kendi uzlaşmaları
    (`fidelity.RecordedFunding`, günlüğe `cf_inputs` olarak taşınır), sonrası depo fonlaması. Sıralama `cf_aux_v1`
    `r_net_conservative` (`learning_cf_aux.AuxPass`; aşma geçmişi aynı defterin ÖNCEKİ kapanmış kayıtlarından); hesaplanamazsa
    `r_net` ("r_rank_src" yazar). Yol 480 barı aşarsa daha kaba dilime birleştirilir (aynı gerçek yol; `baseline_gap_r`
    gösterir). Izgaraya yalnız fidelity'yi geçen vadeli işlemler girer (§5.3).
16. **Eşleşmiş rastgele kontrol.** "Aynı saat dilimi" = aynı UTC gün saati: k = 1…120 gün önceki aynı an (işlem
    penceresinden önce biten); "aynı durum kovası" = `situation_v1`'in 4h trend × 4h oynaklık rejimi (aynı saf fonksiyon,
    `_tf_block(full=False)`); 20 seçim sabit tohumla (`sha256(trade_key|cfgrid_v1)`); giriş = o andaki son kapanmış 5m
    kapanışı, stop aynı yüzde, hedefler aynı R katları, çıkış aynı süre, 5m barlar (1m tembeldir, geçmişte yoktur).
    10'dan az eşleşme → `RC_TOO_FEW` (sinyal bileşeni eksik). `signal_excess_R` = gerçek net R − kontrol net R medyanı.
17. **R ayrıştırması (§5.6).** G = kayma öncesi brüt R (dolum riskine göre, günlüğün tanımıyla aynı). Sıralı zincir:
    X1 = EX_ANTE hücrelerin (atla hariç) G medyanı ("nötr yürütme"), X2 = girişi gerçek hücrelerin medyanı, X3 = girişi
    ve stopu gerçek hücrelerin medyanı, X4 = taban hücre, B = kontrol medyanı, G_lev = taban hücre gerçek kaldıraç/tutarla:
    signal = X1 − B, timing = X2 − X1, stop = X3 − X2, exit = X4 − X3, size_lev = G_lev − X4, cost = net − G_gerçek
    (ölçülen). Zincir teleskopiktir: artık = B (formülün adlandırmadığı rastgele/piyasa tabanı) + (G_gerçek − G_lev)
    (yeniden oynatma farkı); ikisi `residual_parts`'ta ayrı yazılır; eksik bileşen 0 sayılmaz (`missing`, artıkta kalır).
    `regime_fit`: ders deposu P3'te; o zamana kadar aynı defter × taktik × giriş kovasının, işlem AÇILMADAN önce kapanmış
    günlük satırlarından ortalama net R'ı (`JOURNAL_PRIOR`, n ≥ 10; kabul 9 ile uyumlu).
18. **Günlük ekleri (P2a üstüne).** `JOURNAL_VERSION` `tj_v1/p2b.1` (satırlar bir kez yeniden kurulur): `cf_inputs`
    (kaydın fonlama uzlaşmaları, watermark, fonlama saatleri, gerçek stop aşma yüzdesi) ve `agents_ctx.entry_features`
    (provenance giriş kararı: `n_dissent`, `n_vetoes`, `rr`, `consensus_score`, `risk_allowed`). Kurulum kaldığı yerden
    devam eder: değişen satırlar EN YENİ ay ve ay içinde en yeni kapanış önce kurulur, ay dosyası doğrusal birleşimle
    yazılır (madde 25), bütçe bitince kalan satırların eski hâli kalır (`pending`); adım adım kurulum tek seferlikle
    bayt-özdeştir (test). `load_records` yalnız istenen arşiv satırlarını ayrıştırır.
    Özet ve uzlaştırma bütün satırları bellekte tutmaz (küçük izdüşümler). S1b sonucu uzlaştırmayı da taşır
    (`JOURNAL_INCONSISTENT`, `FIDELITY_BELOW_TARGET` bayrakları). S2 de akışla çalışır: günlük iki kez okunur (izdüşüm +
    özet; sonra yalnız hesaplanacak satırlar, en yeni AY önce, ay içinde dosya sırası), bellekte en çok 1.000 yeni satır;
    "öncekiler" (gürültü bandı, aşma geçmişi, `regime_fit`) defter başına önek yapılarından O(log n) bulunur — liste
    tanımlarıyla eşitliği testlidir; depo parça önbelleği 32 parça.
19. **Gece (§6.1).** Plan S0, S1a, S3, S1b, S2, S7, S7b. S3 S1b'den önce: S1b/S2'ye bağlı değildir ve sahibin ana ölçümü
    uzun aşamaların payına bağlı kalmaz. UTC günü MTM S3'ün içinde (`utc_day.run_utc_day`, mühürlü depo); arızası tgt_v1
    sonucunu silmez, aşamayı FAILED yapar (`UTC_DAY_FAILED`). S1b iç son tarih − 42 dk'da, S2 − 12 dk'da durur
    (`S1B_RESERVE`, `S2_RESERVE`; pay < 60 sn ise `SKIPPED_DEADLINE`); kalan iş ertesi gece (`S1B_BACKLOG`/`S2_BACKLOG`,
    sonuç SUCCESS kalır). Gece penceresi DIŞINDA başlayan çalıştırmada (elle / sürüm smoke'u; 03:40 UTC sonrası) S1b ve
    S2 en çok 8'er dakika çalışır (`DAYTIME_STAGE_BUDGET_S`): ilk kurulum smoke'u saatlerce uzatmaz, kalan iş gece
    birimine kalır. Veri mührü eskiyse (§6.1 S0 "yalnız S1, S3 ve S7") S2 `SKIPPED (DATA_STALE)`; S1a, S1b, S3, S7 ve
    — depoyu okumayan — S7b yedeği çalışır (arşivin tek kopyası yedeksiz kalmasın). A/B KAPALI ve SKEW gecelerinin planı
    değişmedi (S1b/S2 yok). Tek işlemin S2 arızası aşamayı düşürmez: satır kodsuz ve nedeniyle yazılır (ızgara
    `ERROR:<tür>`, `S2_TRADE_ERRORS` bayrağı). `analysis.lock` P1a'daki gibi tüm gece tutulur; `engine-query` kilit almaz, hiçbir şey yazmaz.
20. **Yalıtım (§2.8).** Gece import grafiği artık mühürlü depo okuyucusunu (`store`, `provider`, `pathrec`) içerir; §2.8
    yalnız ağ modüllerini (`datastore`, `pit_universe`) dışlar — P1a/P1b gece grafiği testleri buna göre daraltıldı
    (P2 notu 12). Paket dışı import beyaz listesine `cfgrid` (`learning_cf`, `learning_cf_aux`, `learn.shadow`,
    `accounting`, `core`, `shared_experience`) ve `analysis` (`indicators`) eklendi; hepsi salt-okunur hesap.
21. **Yedek (§3.6).** `journal/` ve `attribution/` `backup.EXCLUDE`'a eklendi: arşivden (`closes/`), state yan
    kaynaklarından ve mühürlü depodan bayt-özdeş yeniden kurulur. Uyarı: yan kaynaklar (provenance, `trade_memory`,
    `xp_entry`) state'ten dönerse yeniden kurulan eski satırlarda o alanlar MISSING olur; sahip isterse `journal/` sonradan
    INCLUDE'a alınabilir (ay dosyaları deterministik, ≈ satır başına 8–10 KB ham).
22. **Özet ve sorgu (§7.7, §8).** `digest_tr.md` tam sürüm: hedef başlığı (+ UTC günü satırı, 14 gün yan yana), dün
    kapanan işlemler defter defter (birincil kod + diğer kodlar, maliyet R, kayıplarda HINDSIGHT "kâra dönen hücreler"),
    son 7 günün birincil kayıp kodları, defter başına en iyi EX_ANTE sabit hücre (BÜTÜN işlemlerde aynı hücrenin eşli
    farkı, muhafazakâr R; "ara görünüm, ders değil"), veri/doğruluk sağlığı (günlük, uzlaştırma, fidelity, rehydrate
    dönem başına, yol kaynağı, S2, mühürler, veri mührü), çalıştırma, arşiv. ≤ 8 KB: önce arşiv tablosu, 7 günlük
    tablo, EX_ANTE, kod ve aylık bölümler kısalır; yine sığmazsa işlem satırları defter başına 6 → 3 → 1'e iner; en
    sonda sert kesim. `engine_summary.json` `p2` bloğu; `engine-status` tek "P2:" satırı (S1b/S2, bekleyen iş, fidelity).
    `engine-query trade <id> | why-lost [--book B] [--days N] | day <YYYY-MM-DD>`: Türkçe, ≤ 150 satır ve ≤ 4 KB (§8);
    işlem kimliği defter başına sayaç olduğundan (`F00001` her defterde) birden çok eşleşmede tam `trade_key` istenir.
23. **Zamanlama (kabul 13).** Gerçekçi boyutta sentetik dünya (`tests/research_engine_p2b_fixtures.big_world`: 2.000
    işlem — Box 1.000, ana bot 300, T2 200, M2 200, D4 160, C4 140 —, 90 günlük 1m yol, 125 günlük 5m geçmiş, gerçek
    `FuturesLedgerV2` kayıtları) bu ortamda (4 çekirdek, başka işlerle paylaşımlı) ölçüldü: İLK tam gece (bütün arşivin
    günlüğü + atıfı) 1.649 sn ≈ 27,5 dk — S1b 296 sn (0,15 sn/işlem), S2 1.350 sn (0,68 sn/işlem; 18 hücre + 20 rastgele
    giriş ≈ 30 yeniden oynatma/işlem; 32 işlem fidelity'yi geçemediği için ızgara dışı), diğer aşamalar < 2 sn; ertesi
    (artımlı) gece 11,3 sn. Gece penceresi (01:37 → 03:40; S1b 02:58'e, S2 03:28'e kadar) bu boyutu bir gecede bitirir;
    VPS 3 kat yavaş olsa da (≈ 15 + 68 dk) sığar, daha yavaşsa iş kaldığı yerden ertesi geceye yayılır
    (`S1B_BACKLOG`/`S2_BACKLOG`). CI testi ölçeklenmiş dünyayla (≈ 120 işlem) işlem başına süreyi ölçer ve 2.000 × 3 pay
    ile pencereye sığdığını denetler; tam ölçüm `ENGINE_P2_TIMING_FULL=1`. A/B tur etkisi (≤ %5) yalnız VPS'te ölçülür:
    P2 sürümünden sonraki 14 gecede sürüm betiğinin `--ab-report`'u AÇIK/KAPALI geceleri (S1b/S2'nin en ağır olduğu ilk
    geceler dahil) yan yana verir; `--check` "GECE ÖĞRENME MOTORU" bölümü S1b/S2 süre, bekleyen iş ve bayraklarını
    gösterir.
24. **§5.10 Box bulgusu (kabul 11).** Gerçek veri testte yoktur: test, eski dönemde (E0) gerçek bulgunun sayılarıyla
    (72 işlem stop < %0,5, 176 işlem %0,5–1) iki kovanın brüt avantajı AYNI tanımlı sentetik bir dünyayı motorun gerçek
    boru hattından geçirir; dar kova negatif, %0,5–1 pozitif ve dar kovanın kayıplarında `TIGHT_STOP_COST_MULTIPLIER`
    baskın çıkar. Gerçek sayılar (−0,56R / +0,17R) VPS'te `attribution/_build.json` `sanity_5_10_box_stop_width`
    bloğunda (dönem başına kova) görülür; motor bu uygulanmış karar için aday üretmez.
25. **Bellek.** Gece birimi `MemoryHigh=400M` / `MemoryMax=512M` DEĞİŞTİRİLMEDİ (P2 teslimatı "Birim dosyası değişmez";
    §2.2'nin "P2+: 1,2G / 1,5G (P0'da kesinleşir)" satırıyla çelişki sürüm ajanına bildirildi; 7,7 GiB'lık VPS'te sınır
    toplamı zaten aşıktır, §2.5 bellek kararı). Ölçümler (`VmHWM`, ayrı süreç, üretim import yolu): rotasyon
    tavanlarında (9 defter × 5.000 kayıt = 45 bin işlem, depo yok) iki tam gece 265 MiB (bütçe 0,6 × MemoryMax = 307
    MiB; P1a ≈ 180); gerçekçi depolu dünyada (2.000 işlem, 1m yol) 283 MiB (import sonrası 106 MiB; aynı süreçte iki
    gecenin tepesi). Önlemler: S1b/S2 bütün satırları bellekte tutmaz — ay dosyaları `journal.MonthWriter` ile (1.000
    satırlık sıralı geçici parçalar `.spill-*` + k-yollu birleşim; doğrusal, deterministik gzip), özetler ve uzlaştırma
    akışla, `scan_archive` yalnız sıra/ay alanlarını, spot kayıtları bellekte tutulmaz, depo parça önbelleği S1b/S2'de
    32 parça; özet ve sorgular akışla (son 30 gün tek geçiş).
26. **Sürüm.** `ENGINE_VERSION` bu aşamada değişmedi (`research_engine_v1_p1b`); sürüm ajanı P2 için yükseltir (motor
    kod özeti değiştiği için yeni A/B dönemi açılır, §2.9, beklenen). Bilinen `learn/labels.py` ücret çift sayımı
    (labels.py:45) olduğu gibi duruyor (P6; motor o fonksiyonu kullanmaz).

#### Değişiklik (2026-10-08, inceleme düzeltmesi; gerçek veriye uygulanmadan önce)

2026-10-07 bağımsız incelemesi P2'yi (impl/engine-p2v2 @ cc88e9b) **FAIL** buldu: 1 BLOCKER (B1), 6 MAJOR (M1–M6) ve
küçükler. P2, dağıtılmış db5db96 çizgisindeki P1b'nin üstüne (impl/engine-next) yeniden uygulandı ve aşağıdaki
düzeltmelerle tamamlandı. Her madde önce BAŞARISIZ olan bir testle yazıldı (`tests/test_research_engine_p2_fixes.py`, 16
test; eski kodda 16'sı da kalır), sonra düzeltildi. `attribution_v1` ve `cfgrid_v1` yalnız sentetik altın yollarla
mühürlenmişti ve hiç gerçek veriye uygulanmamıştı; ilk gerçek veri çalıştırmasından ÖNCE yeniden mühürlendi (§5.4, §5.5,
§5.6 "Değişiklik" blokları; eski metinler tarih olarak durur). Depoda gerçek işlem sonucu YOKTUR; hiçbir eşik bir sonuca
bakılarak seçilmedi. Aşağıdaki maddeler yukarıdaki 1–26'yı (çelişen yerde) geçersiz kılar.

27. **Mühürler.** `ATTRIBUTION_SHA` `3f4596e3…6626` → `914875a1f6631822585ffd5e67e7ffa9a721069e47b6df7a9a624a8a789a6d7f`;
    `CFGRID_SHA` `4a96df71…454d` → `7c299ae001aee643a76c697ee191026ba6cdb842e21c377d48ed86e0842adf68`
    (testlerde sabit; madde 13'ün değerleri tarihtir).
28. **B1 — örnek dolumu boşluk değildir.** Canlı defter (`futures_ledger.exit_decision`) stopu seviyenin ötesindeki İLK
    60 sn fiyat örneğinden doldurur (`GAP_FILL_AT_FIRST_OBSERVATION`/`PRICE`); P2 bunu `exit_basis = GAP` sayıyor, `GAP_FILL`
    birincil öncelikte 2. olduğu için neredeyse her stop birincil `GAP_FILL` oluyordu, fidelity de seviyeden doldurduğu
    için aşmayı > 0,05R `FILL_BASIS` farkı sayıp işlemi ızgaradan düşürüyordu (Box stopları %0,32–1). Düzeltme: (a) günlük
    `exit_fill_detail` (gözlem fiyatı, seviye ötesi mi, aşma R, bar yolunda stopa ilk ulaşan bar ve stopun ötesinde açılıp
    açılmadığı; `pathrec.stop_crossing`) ve `exit_overshoot_r` (MEASURED) yazar; `exit_basis` mühürlü
    `attribution.classify_exit_basis`'ten: `GAP` yalnız bar açılışı boşluğu ya da aşma ≥ 0,25R, örnekleme aşması `LEVEL`
    (`SAMPLED`); (b) `GAP_FILL` birincil öncelikte geç girişin ardında; (c) fidelity gözlem dolumunu kural dilimi
    penceresinde modeller (`cfgrid._replay_ext(stop_fill=…)`, `RECORDED_FILL`; `fidelity_v2`). Test dünyası da gerçekçi
    hâle getirildi: `walk_sampled` (60 sn örnek + kural diliminin bar tiki) — eski fikstür stopu seviyeye kırparak hatayı
    gizliyordu; `big_world` ve §5.10 Box dünyası bununla kurulur.
29. **M1 — R ayrıştırması harfiyen.** zamanlama/stop/çıkış = G(gerçek) − o eksenin ızgara medyanı (eksen gerçek hücreyi
    içerir); sinyal = X1 (giriş/stop/çıkış/yönetim eksenlerinin EX_ANTE medyanı, 1x) − rastgele kontrol; artık parçaları
    rastgele taban + yeniden oynatma farkı + eksen etkileşimi (§5.6 Değişiklik).
30. **M2 — LATE_ENTRY MAE kolu** = MFE ≥ 0,3R ve MFE gözleminden KESİN önceki gözlemlerde en kötü aleyhte < −0,7R
    (`path.mae_before_mfe_r`); hiç lehte gitmeyen işlem artık LATE_ENTRY almaz. **Yol penceresi (küçük):** MFE/MAE ve
    yol ölçüleri çıkıştan önce kapanmış işlem barları + çıkış dolumu (`pathrec.pre_exit_obs`; çıkışı içeren bar çıkış
    sonrası fiyat taşır); yeniden oynatmalar çıkış barını kullanmaya devam eder.
31. **M3 — sıralama ve öneriler.** İşlem başına TEK ölçü (`grid.rank_src`: her sıralanan hücrenin `cf_aux_v1` R'si varsa
    o, yoksa hepsi `r_net`); boyut/kaldıraç ekseni sıralamaya, HINDSIGHT en iyi hücreye, "kâra dönen hücrelere" ve defter
    görünümüne girmez (kaldıraç önerilmez, §7.6); defter görünümü yalnız `cf_aux_v1` işlemleri, n ≥ 30 ve ≥ 10 gün, yalnız
    iyileştiren hücre ve "k sabit hücre arasından SEÇİM" etiketiyle (`analysis.VariantAcc.best`; özet ve `why-lost` metni).
32. **M4 — uzlaştırma S1a okumasına göre.** `reconcile_journal`'ın ledger kolu: S1a okumasından (`closes` çapası
    `observed_at`) sonra kapanan kayıt `after_s1a`, sonra içeriği değişen kayıt (sha farklı ve ledger `updated_at` > S1a)
    `revised_after_s1a` sayılır — tutarsızlık değildir (eskiden S1b sonunda canlı ledger yeniden okunduğu için ~5 gecede
    bir sahte `JOURNAL_INCONSISTENT`). S1a okumasından önce kapanmış ama arşivde/günlükte olmayan kayıt hâlâ tutarsızlıktır.
33. **M5 — artımlı kurulum dar.** Eski özet, satırın penceresiyle kesişen ay parçalarının sha'sıydı (1m dahil, [açılış −
    420 g, kapanış + 3 g]): günlük ekleme o ayın parçasını değiştirdiği için bir eklemede 44/60 (S1b) ve 34/60 (S2) satır
    yeniden kuruluyordu. Şimdi `pathrec.WindowDigest`: dilim başına okuma penceresi (`s1b_windows`, `s2_windows`; [açılış −
    dilime göre geriye bakış, kapanış + 6 sa]); pencerenin tamamen kapsadığı ay için parça sha'sı, kısmen kapsadığı ay için
    o parçanın penceredeki UTC günlerinin içerik özetleri (parça bir kez okunur; özetler parça sha'sıyla
    `journal/tj_v1/_window_days.json.gz` ve `attribution/_window_days.json.gz`'de saklanır). Satırın kaydettiği
    `data_seal`/`store_parts` da bu belirteçlerdir (artımlı ve tek seferlik kurulum bayt-özdeş kalır). Test: pencerelerden
    sonra bir günlük ekleme 0 satır kurdurur; bir penceredeki tek bar değişikliği yalnız o satırları kurdurur.
34. **M6 — bellek.** Gece birimi (bu depodaki P2 birimi) `MemoryHigh=640M`, `MemoryMax=768M`,
    `ENGINE_EXPECTED_MEMORY_MAX=805306368`; S1b/S2 depo parça önbelleği 32 → 16. VPS'te kurulu P1a gece birimi P2 sürüm
    betiği yenisini (kapılı daemon-reload, §9.3) kurana kadar 400M/512M kalır. Madde 25'in "birim değişmez" kararı bununla
    değişti. Ölçüm (bu ortam, 4 çekirdek paylaşımlı; ayrı süreç, üretim import yolu, `VmHWM`): 400 işlemlik gerçekçi dünya
    (10 gün 1m, 60 sn örnekli dolumlar) ilk gece 294 sn (S1b 64 sn, S2 229 sn), tepe 217 MiB (import sonrası 99 MiB),
    ertesi gece (değişiklik yok) 1,8 sn / 110 MiB; 2.000 işlemlik dünya (madde 23'ün boyutu, 90 gün 1m) ilk gece
    1.554 sn ≈ 25,9 dk (S1b 350 sn, S2 1.202 sn; fidelity 1.889/2.000 = %94,5 — nedenler EXIT_REASON_DIFFERS 102,
    BACKDATED_CLOSE 81, FILL_BASIS 50, AMBIGUOUS_BAR 21; örneklerin kaçırdığı fitiller ve kural barı dolumları), tepe 310 MiB
    (0,40 × 768 MiB; eski birimde 0,61 × 512 MiB, MemoryHigh 400M'e 90 MiB), ertesi gece (değişiklik yok) 7,7 sn / 131 MiB. VPS'te
    `memory.peak` P2 sürümünün smoke'unda ve ilk iki gecede denetlenir (0,8 × MemoryMax = 614 MiB).
35. **Küçükler.** S2 birikmiş işte ay içinde de EN YENİ kapanış önce (S1b gibi); kayıtta olmayan para alanları 0 diye
    uydurulmaz, `MISSING` (ücret özdeşliği bilinmiyorsa `None`); `journal/` yedeğe dahil (`backup.INCLUDE`; yan kaynaklara
    dayanır; ≈ 1,4 KB/satır gz), `attribution/` hariç kalır; `clean_spills` atomik yazımın öldürülmüş `.*.tmp` dosyalarını da
    siler (günlük, atıf, `paths/` ay klasörleri); ızgara ufkunun ex ante olmadığı ve `E_DELAY1` `NO_BARS` sayıları
    açıklanır (`cfgrid` spesifikasyonu `horizon_ex_ante: false`, `_build.json` `cells_not_ok`, özet satırı).
36. **Sürümler.** `ENGINE_VERSION` `research_engine_v1_p1b` → `research_engine_v1_p2` (motor kod özeti değişir → yeni A/B
    dönemi, §2.9); `JOURNAL_VERSION` `tj_v1/p2c.1`, `ANALYSIS_VERSION` `attr_v1/p2c.1`, `FIDELITY_VERSION` `fidelity_v2`
    (VPS'te P2 verisi yoktur; ilk P2 gecesi her şeyi zaten kurar).
37. **P2 sürüm betiğinin yapması gerekenler (henüz yazılmadı).** db5db96 çizgisinden (bu dalın başı) TIP sabitlemek;
    `ENGINE_VER="research_engine_v1_p2"`; yeni gece birimini (640M/768M, `ENGINE_EXPECTED_MEMORY_MAX=805306368`) P1b
    betiğinin kapılı daemon-reload düzeniyle kurmak ve worker `MemoryMax=6G` önce/sonra denetlemek; §2.5 RAM toplamı
    uyarısını yeni değerle hesaplamak; smoke'ta S1b/S2'nin `DAYTIME_STAGE_BUDGET_S` (8 dk) ile sınırlı olduğunu ve ilk
    tam kurulumun gece biriminde yayıldığını (`S1B_BACKLOG`/`S2_BACKLOG`) göstermek; `--check`'e P2 bölümü (S1b/S2 süre,
    bekleyen iş, uzlaştırma `after_s1a`/`revised_after_s1a`, fidelity oranı ve nedenleri, `rank_src` dağılımı, mühür
    sha'ları, `memory.peak` / 0,8 × MemoryMax); geri alma bir önceki motor sürümüne (birim 400M/512M'ye döner); bağımsız
    koşucu P2 alt kümesini
    (201, 205, 207, 208, 211, 212, 213) içerir; A/B tur etkisi ≤ %5 P2'den sonraki 14 gecede `--ab-report`.
38. **Açık kalanlar (düzeltilmedi, kayıtlı).** (a) `NOISE_LOSS`, `GIVEBACK` MFE ≥ 0,3 koruması ve `LATE_ENTRY`
    okumaları için sahip onayı (§5.4 Değişiklik). (b) §5.10: geçici 2026-09-30 dönem sınırı `min_stop_pct = 0,32`
    rejimini ikiye böler; kabul 11 gerçek veride tek dönemde yeniden üretilemeyebilir (`library/config_epochs.json` P3'te
    gelince düzelir). (c) Gece import grafiği `store` üzerinden `history.store`'u (sqlite3, `market.http`,
    `history.collector`) çeker (§2.8'e uygun; ileride tembel import ya da test). (d) `regime_fit` önceki satırların net
    R'ının son revizyonunu kullanır (geç fonlama; ihmal edilebilir).

### P3 — Strateji kütüphanesi, walk-forward, keşif katmanı, denemeler, CSCV/PBO, dersler, zaman noktasında evren

**Teslimatlar:**
- `library/` (`LIB_v1` mühürlü; ön kayıt metni bu belgede ve testte, ilk VPS çalıştırmasından önce) ve
  `library/config_epochs.json`;
- `wf.py` (7 parçalı rotasyon; `make_folds(validation_days>0)` + `run_three_way`);
- `explore.py` (keşif katmanı, §6.8);
- `cscv.py` (PBO);
- `trials.py` (geçmiş laboratuvar sonuçları içe alınmış; bakış ve ileri veri okuması kaydı; kümülatif N);
- `pit_universe.py` (veri biriminde; delist dahil listeleme/delist tarihleri);
- `lessons.py` (ayrı kök, genişletilmiş anahtarlar, önceden kayıtlı bakışlar, alfa harcama);
- lider tablosu (KEŞİF bölümüyle);
- `engine-query lessons|trials|explore`.

**Kabul:**
1. Kopya paritesi: M2/T2/D4/C4 için, **yalnız POLICY girişleri** ve ≥ 20 girişli **her config dönemi** için ≥ %95 sinyal,
   medyan |ΔR| ≤ 0,05.
2. `leakage_check` geçer.
3. Dikilmiş avantajlı sentetik veri Kapı A'yı geçer; rastgele yürüyüş ve plasebo geçemez (200 boş denemede yanlış pozitif
   ≤ q).
4. CSCV cevabı bilinen veride doğrudur.
5. Bilinen laboratuvar sonuçları tolerans içinde yeniden üretilir (gold_v1 0/32, crowd fut_v2 0/8, gold_v2 aile B).
6. Deneme sayacı hiç azalmaz; mühür artırılmadan spec değişikliği CI'ı kırar; DSR N = kümülatif ham N.
7. Kapı A ve ders durumu yalnız kayıtlı bakışta değişir (özellik testi); her bakış `trials.jsonl`'da.
8. Keşif katmanı yalnız `T_seal` sonrası barları kullanır (zaman yolculuğu testi); çıktısında hüküm kelimesi yoktur.
9. Zaman noktasında evren, bilinen bir delist sembolü (fixture) doğru tarihlerle içerir; kurulmadan XSEC Kapı A'ya
   giremez.
10. Tam kütüphane taraması 7 gecede, son tarih içinde biter; keşif adımı gecede < 10 dk.

**Sahip:** deploy; ilk Kapı A bakış tablosunu ve KEŞİF bölümünü okur (beklenen: çoğu spec elenir; keşif her şeyi
gösterir ama hiçbirini kanıt saymaz).

### P4 — Kayıt-yalnız ileri yeniden oynatma adayları (kripto + altın) ve terfi kapısı

**Teslimatlar:**
- `prospective.py` (≤ 20–40 aday, nedensel adım, önceden kayıtlı durdurma, CUSUM; `PROSPECTIVE_REPLAY` etiketi);
- `promotion.py` (`PROMOTION_REGISTRY`; metni **sahip onaylar**, sha testte ve belgede sabitlenir; Kapı A/B/C `p_day`);
- aylık Holm öneri toplu işi;
- `engine-query --approve|--reject|--retire --operator berke`;
- ilk aday havuzu — **hepsi yalnız Kapı A'yı kayıtlı bakışta geçerse aday olur**:
  - ders kaynaklı klonlar, canlıda zaten olmayanlar (örn. "D4 55/20");
  - M2 varyantları;
  - XAUUSDT/PAXGUSDT altın trend adayları (gold_v2 aile A yayımlandıktan sonra, onun sonucuna göre).
- **Listeden çıkarılanlar:** "Box en az stop %0,5" (f8b05fb'de canlı: `books.b1_box_fade.min_stop_pct: 0.5`);
  "seçicilik ekstraları olmayan ana bot" (canlı: `learning_mode.extra_entries: record_selectivity`); PAXG hafta sonu
  dönüşü (gold_v2 aile B kanıt yok, `db1828e`). Bunlar yeniden ancak yeni bir LIB sürümü ve Kapı A ile girebilir.

**Kabul:**
1. Aday defterleri yalıtılmıştır: `research/prospective` dışına yazma yoktur.
2. Zaman yolculuğu fixture'ıyla nedensellik testi geçer.
3. Her aday işleminin tam `tj_v1` satırı ve atfı vardır.
4. Durdurma ve Kapı B yalnız kayıtlı bakışta tetiklenir.
5. Öneri yalnız tüm Kapı B koşulları ve Kapı C Holm'u sağlanınca üretilir (altın test); Kapı B'de gerçekleşen/modellenen
   maliyet oranı kullanılmaz.
6. Aday sonuçları canlı toplama asla eklenmez.
7. Runner `APPLIED_BOUNDED` yazamaz.

**Sahip:** kapı metnini onaylar; deploy; öneri çıktığında yazılı karar verir.

### P5 — Panel sayfası `/hedef` (ve sonra `/ders`) — bir sonraki normal worker sürümüyle

**Teslimatlar:**
- `StateReader` araştırma özeti kökü;
- `/hedef` ve `/ders` sayfaları (aday ve keşif bölümleri ayrı) ve `NAV_MORE` girdisi;
- GET-only, 405, dosya yok, eski dosya testleri.

**Kabul:** sayfa dosya yokken bayt-özdeş; 405 testleri; boyut sınırı; "> 24s ESKİ RAPOR" notu; GEÇİCİ/KESİN etiketleri
görünür.

**Sahip:** normal sürümün parçası olarak onaylar (worker boşta durdurulup başlatılır).

### P6 — İsteğe bağlı worker kayıt-yalnız enstrümantasyonu (SAHİP ONAYI GEREKİR)

**Teslimatlar:**
- `TradeRecord`'a eklemeli `meta` / `targets`, ya da defter başına `closed_meta.jsonl` yan dosyası (karar anlık
  görüntüsü, risk anlık görüntüsü, `be_by_mfe`, D4/C4 sinyal bloğu, Box bağlamı, ilk hedefler);
- spot kayıtlarına `features.initial_stop` ve `features.risk_usdt` (bugün spot kaydı `features` taşımıyor);
- strateji defterleri doğru `setup_type` ile `trade_memory` çıkış satırı yazar;
- `xp_outcome` `exit_price` / `tp1_done` / `fills` yayar;
- `label_outcome` çift ücret düzeltmesi;
- ana bot postmortem'i giriş kararını kullanır;
- strateji defterlerinde aday başına ret satırları;
- dolumda canlı spread kaydı.

**Kabul:**
- tekrar oynatılan turda karar, ledger ve emirler bayt-özdeş;
- 48+ mevcut değişmez geçer;
- eski ledger'lar yüklenir;
- tur süresi ve bellek ±%5;
- günlükte ilgili alanlar `RECONSTRUCTED`/`MISSING` → `MEASURED` olur.

**Sahip:** açık onay; normal sürüm, boşta durdurma ve yeniden başlatma; 3 gün `--check`.

### P7 — Onaylı terfiler

Onaylanan her öneri için normal bir sürüm yeni bir kâğıt defter ekler: %0,25 risk, kendi ledger dizini, karne girdisi,
öğrenme modu etiketleri, yeni değişmezler. Defter günlük hedef toplamına girer. Motor önceden kararlaştırılan durdurma
kuralını raporlar; karar sahibindir.

---

## 11. Riskler

| Risk | Azaltma |
|---|---|
| Aşırı uydurma ve çoklu test: yüzlerce varyant × 45 enstrüman şanslı kazananlar üretir | mühürlü kayıtlar; kümülatif ham N ile DSR; tek istatistik `p_day`; Kapı A yalnız aylık bakışta; BH; CSCV PBO; plasebo; mühür sonrası ileri veri (kayan bekletme yerine); zorunlu ileri Kapı B (alfa harcamalı); aylık Holm. Yine de Kapı A geçenlerin çoğunun Kapı B'de düşmesi beklenir. |
| İsteğe bağlı durma (her gece yeniden test) | ders, Kapı A, Kapı B ve hedef hükümleri yalnız kayıtlı bakışlarda, alfa harcamalı; gece değerleri "ara görünüm" |
| Hedef hükmünün tekrarlı testi (kayan 60 gün × ~50 kapsam) | aylık bakış, `α_bakış = 0,05/12`, hükme uygun bütün kapsamlarda Holm, yalnız KESİN günler |
| Hedef baskısı: +%1/gün için kaldıraç/risk artırma isteği | işlem başına sabit %0,5 riskte net R ile puanlama; düşüş isabet oranının yanında; k*/iflas tablosu yalnız lider tablosunda ve önerilerde; kaldıraç değişikliği asla otomatik önerilmez |
| "Bugün bir coin +%1 yaptı" yanılgısı | şans oranı (muhafazakâr işaret çevirme başlıkta, maliyet bilinçli ikincil), Holm, önceden seçilmiş enstrüman kuralı, iki payda etiketli |
| Yalnız-gerçekleşmiş başlığın yanıltması | P1a'dan itibaren `LEDGER_MARK` MTM; gerçekleşmiş sayının yanında her zaman açık gerçekleşmemiş; yalnız-gerçekleşmiş satırda `TUTTU`/`HEDEF GÜNÜ` yok (metin testi) |
| Gün satırlarının sonradan değişmesi (geç fonlama, geriye tarihli kapanış, TP1, eksik fonlama kapsaması) | GEÇİCİ → KESİN (≥ 3 gün + kapsama), yalnız eklenen `rev`, görünür `REVİZE`, hükümler yalnız KESİN günlerde; geç fonlamanın gün ataması kayıt ve cüzdan görünümünde ayrı ve tutarlı |
| Ana botun spot defterinin unutulması | `state/spot_ledger.json` açıkça arşivde, günlükte, hedefte ve `scorecard --daily`'de; kabul testi |
| Cüzdan hareketlerinin rotasyonla kaybı (`entries_keep=2000`) | gecelik hareket arşivi, hizalama ve `ENTRIES_GAP`; ölçülmüş anlık görüntü; `--check`'te rotasyon payı; ayrı kilitler sayesinde doldurma arşivi durduramaz |
| Worker yavaşlaması veya OOM (RAM/vCPU bilinmiyor) | P0 boyutlandırma (gece + doldurma birlikte); Nice 19, CPUWeight 10, IO idle, OOMScoreAdjust 1000; pencereler; A/B gecelerinde tur p95 ≤ +%5 |
| CPU/IO çekişmesinin Box zamanlayıcısını ve koruyucu izleyiciyi geciktirmesi | A/B gecelerinde Box kaçan bar oranı ve koruyucu izleyici gecikmesi KAPALI tabanı içinde olmalı (§2.9) |
| Paylaşılan IP REST ağırlığı ve yasağı | P1a ağsız; P1b'de arşiv-önce, güvenlik payı 0,1, başlık takibi, 418/429'da durma, **worker son 60 dk'da 429/418 aldıysa REST yok**; kabulde worker 418/429 = 0 ve ret oranı değişmez |
| Taban çizgisi karışıklığı (wt-tourfix ve diğer worker sürümleri) | taban her worker sürümünden sonra yeniden alınır; ~~başka sürümün 7 günlük penceresinde motor dağıtılmaz~~ **Değişiklik (2026-10-06, sahip kararı):** başka sürümün yeniden başlatmasından en az 3 gün sonra ve o sürümün ilk iki `--check`'i temizse (3 günü betik zorlar); etki yalnız A/B ile ölçülür |
| App ↔ engine-app sürüm kayması | `load_config`/`load_v3` yok, ham YAML; S0 SKEW denetimi; her app sürüm betiği engine-app'i yeniden sabitler; ayrı klon (worktree değil) |
| Yalıtımın sessizce bozulması (yanlış birim dosyası, systemd sürümü) | S0 çalışma zamanı denemeleri (state'e yazma, soket, `memory.max`); `ISOLATION_BROKEN` ile durma; sahte VPS'in doğrulayamadıkları açıkça listeli |
| `daemon-reload` kayması (4G ↔ 6G) | kapılı kurulum, önce/sonra 6G doğrulaması, otomatik geri alma; elle smoke çalıştırma; P1a/P1b'den sonra birim dosyası değişmez |
| Saat dilimi (VPS Europe/Istanbul) | her `OnCalendar`'da `UTC`; test |
| Maliyetler modellenmiş, ölçülmemiş (3 bps sabit kayma, sıfır spread, stop seviyesinde dolum) | ADVERSE/STRESS kapıda; `cf_aux_v1` muhafazakâr R; kaydedilmemiş spread tahmini ayrıca; ileri yeniden oynatmada gerçekleşen = modellenen olduğu dürüstçe yazılır ve kanıt sayılmaz |
| Kısa altın geçmişi (XAUUSDT 2025-12, PAXG vadeli 2025-03; ince likidite; Dukascopy OTC bid farklı) | `KISA_GEÇMİŞ` işareti; daha sıkı Kapı B; uzun vekiller ayrı etiketli; gold_v1 0/32 ve gold_v2 aile B kanıt yok; keşif katmanında yine de izlenir |
| Hayatta kalma yanlılığı (evren bugünün listesi) | P3'te zaman noktasında evren (delist dahil); o zamana kadar XSEC Kapı A'ya kapalı; diğer satırlarda uyarı |
| Config dönemi değişiklikleri (Box stop tabanı, seçicilik) | `config_epochs`; parite ve dersler dönem başına; dönem sınırını aşan karşılaştırma yok |
| Yeniden kurma hataları (bağlam, yol) | stop ölçülmüş; rehydrate yalnız hedef ve bağlam, 1 tick denetimli; fidelity kapısı; `RECONSTRUCT_FAILED` ve `MISSING` görünür; P6 bunları ölçüme çevirir |
| Küçük örneklemler (D4 13, M2 49) | dersler aylarca `OBSERVATION`'da kalır; CF kanıtı ayrı ve muhafazakâr; adaylar ve keşif örneklemi defterlere dokunmadan büyütür; "VERİ YETERSİZ" görünür kalır |
| Dosyalar arası tutarsız okuma (worker `os.replace` ile yazar) | anahtarlı, idempotent birleştirme; sonraki gece `rev+1` ile tamamlama |
| Store bozulması veya yarım yazım | parça `.sha256`; `MANIFEST_LAG` kurtarma ile `CORRUPT` karantina ayrımı; satır kaynağı önceliği; doğrulanmamış zip ayrı sayılır |
| Mühür değişikliği (arşiv yeniden yayımı, REST düzeltmesi) | her çıktı `data_seal` anar; uzlaştırma farkı kaydedilir |
| Araştırma verisinin tek kopya olması | S7b günlük küçük yedek, doğrulama, VPS dışına taşıma; `engine-restore` |
| State geri yüklemesi | `seq` azalması ve `state.pre-restore-*` ile tespit; `RESTORED_AWAY`, `REVİZE (RESTORE)`; `INCONSISTENT` değil |
| Disk büyümesi | 14 günlük geriye-test ayrıntısı; 1m zip silme; 20 GB sınırı; state altında hacimli bir şey yok |
| Mevcut otomatik öğrenici (ResearchCoordinator) | P0'da sahibin kararı; motor dokunmaz; etkinleştirme sayısı `--check`'te |
| Keşif katmanında "en iyi varyant" yanılgısı | "N varyant arasından en iyi 5 — seçim yanlılığı, kanıt değil" başlığı; medyan varyant da gösterilir; hüküm kelimesi yok; terfi yok |
| Kod değişikliklerinin token maliyeti | ayda bir LIB sürümü; sürüm başına ≤ 1,5M token bütçesi; deneme defteri tekrarı önler (§8) |
| Sahibin iş yükü | aşama başına tek küçük betik; `--dry-run` / `--check` / `--ab-report`; tek komutluk kapatma; kısa özet |

---

## 12. Açık sorular (sahip için)

1. **ResearchCoordinator** (`research_enabled=True`): mevcut otomatik filtre/küçültme etkinleştirmesi önceden onaylı mı,
   yoksa ayrı onaylı bir değişiklikle kapatılsın mı?
2. **RAM / vCPU:** P0 çıktısına göre motor birimleri için `MemoryMax` ve `CPUQuota` değerleri (gece + doldurma birlikte).
3. **Dukascopy:** gold_v2'nin ayna işi bittiğinde hazır bir ayna VPS'te olacak mı, yoksa sahip bir kerelik tarball'ı mı
   taşıyacak? İkisi de yoksa altının uzun vekili "yapılamadı" kalır.
4. **Tek enstrüman hedefi:** "en az bir coin veya altın" için önceden bir enstrüman bildirilsin mi? Bildirilirse hükme
   uygun kapsamlar {toplam, o enstrüman} olur (Holm bu ikisi arasında); bildirilmezse bütün kapsamlar Holm'lu kalır.
   **Öneri:** toplam (varsayılan) veya BTCUSDT. XAUUSDT önerilmez: yalnız ~10 aylık geçmişi ve ince likiditesi var;
   2027'den önce 60 günlük güven sınırına dürüstçe ulaşamaz.
5. **Gün sınırı:** UTC gün mü kullanılsın (önerilen; 4h yayın düzeniyle uyumlu), yoksa İstanbul günü mü? Not: P1a'nın MTM
   günü geçici olarak gece anlık görüntüsünden gece anlık görüntüsüne (≈ 01:40 → 01:40 UTC) ölçülür; P2'de UTC gününe
   geçilir.
6. **Kapı metni:** `PROMOTION_REGISTRY` eşikleri (§6.7) bu haliyle onaylanıyor mu? Onay P4'ten önce gerekir.
7. **Altın defteri:** Bir altın adayı Kapı B'yi geçerse yeni bir altın kâğıt defteri kabul edilir mi? Hangi risk
   tavanıyla (öneri: %0,25)?
8. **Saatlik aday adımı:** 5m taktikler için gecikmeyi azaltacak saatlik birim (§6.6) gece yolu kararlı olduktan sonra
   istenir mi?
9. **Telegram:** "günlük rapor hazır" bildirimi istenir mi? Config değişikliği gerektirir.
10. **P6:** Worker'a kayıt-yalnız alan ekleme (kararı değiştirmeyen, yeniden başlatma gerektiren) ne zaman yapılsın?
11. **A/B geceleri:** her motor sürümünden sonraki 14 gecede motorun günaşırı kapalı kalması (arşiv yalnız rotasyon payı
    3 günün altındaysa çalışır) kabul mü? Bu, motorun etkisini ölçmenin tek dürüst yoludur.
12. **Günlük hedefin paydası:** "bir coin veya altın günde +%1" derken kastedilen **toplam sermayeye göre** mi
    (`r_inst = enstrüman P&L / E_total`), yoksa **o enstrümana / onu işleyen deftere ayrılan sermayeye göre** mi
    (`r_book_inst = P&L / E_book`)? İkisi de etiketiyle gösterilir; cevaba kadar hüküm paydası `E_total`'dır ve özetteki
    "en iyi enstrüman" aynı paydayı kullanır.
13. **Araştırma yedeği:** mevcut VPS dışı yedek akışı `data/research/backup/`'ı da taşıyacak biçimde genişletilsin mi?

Önceki taslaktaki "açık pozisyon fonlaması cüzdana anında mı yazılıyor?" sorusu artık açık değildir: P1a'dan itibaren
FUNDING hareketleri arşivlenir ve iki anlık görüntü arasındaki cüzdan eşitliği her gece ölçülür.

---

## 13. Birinci hakem turunun "mutlaka düzelt" listesinin kapanışı

| Madde | Nerede | İkinci turda değişti mi |
|---|---|---|
| Gece öncesi/sonrası ledger parmak izi değişmezi kaldırıldı; çekirdek + açma modu denetimi | §2.8 | + S0 çalışma zamanı öz-denetimi |
| Panel yalnız normal app sürümüyle; o zamana kadar `--check` / `engine-status` / `scorecard --daily` | §7.7, P5 | — |
| REST bütçesi ≤ 0,1, başlık takibi, 418/429, saatlik REST yok | §3.4 | + worker 429/418 günlük koruması |
| Tek motor kilidi, `SKIPPED_LOCKED`, zaman ayrımı, bellek kuralı | §2.4, §2.5 | **iki kilit** (`data.lock`, `analysis.lock`); bellek kuralında gece + doldurma |
| Rotasyon ve geç fonlama; elde tutulan pencerede uzlaştırma; geriye doğru `E_book` | §4.2, §7.1 | + hareket arşivi, ölçülmüş anlık görüntü; GEÇİCİ/KESİN |
| Hedef hükmü: CI alt sınırı ≥ %1,00, ≥ 40 tam gün / 60, ≥ 30 işlem, %25 yoğunlaşma | §7.3 | + aylık bakış, `α_bakış`, bütün kapsamlarda Holm |
| İsteğe bağlı durma: kayıtlı bakışlar, alfa harcama, sabit BH ailesi | §5.7, §6.4 | + Kapı A/B bakışları, tek istatistik |
| State'e yazma yok (shared-experience özetleri motor biriminden çalışmaz) | §2.1, §6.1 | — |
| Birim kayması kapısı (`NeedDaemonReload=no`, 6G önce/sonra), `setup_vps_v3.sh` yok | §9.3 | + dizin oluşturma, elle smoke çalıştırma |
| `OnCalendar` UTC + pencere testi | §2.2, P1a testleri | yedek çakışması saatle değil durumla |
| ResearchCoordinator için P0 sahip kararı | §9.1, P0, §12 | + `--check`'te etkinleştirme sayısı |
| PBO yok → CSCV yazılır; o zamana kadar kapı kapalı | §6.4 | — |
| `python -I` yok; `-s -m` + `WorkingDirectory` | §2.3 | — |
| Config kayması | §2.3 | **açık `--config` ile `load_config` yerine ham YAML; SKEW denetimi; yeniden sabitleme kuralı** |
| `initial_stop` / R paydası | §4.3, §5.2, §5.3 | **düzeltildi:** vadeli kayıtlarda `features.initial_stop` ve `features.risk_usdt` ölçülmüştür; R paydası `risk_usdt`; rehydrate yalnız hedef ve sinyal bağlamı (stop'a 1 tick denetimi); `MISSING` yalnız eski kayıtlar ve özelliksiz spot kayıtları |
| `label_outcome` çift ücret: yeniden kullanılmaz; regresyon testi | §4.2 | — |
| Altın: `LIVE_PAPER`'da "işlem yok", yalnız aday akışında | §7.1 | + keşif katmanı |
| Rotasyon riski `--check`'te | §4.2 | + `entries` rotasyon payı |
| MTM `LAST_PRICE_PROXY`; EKSİK günler hariç | §7.1 | P1a'da `LEDGER_MARK`, P2'de `LAST_PRICE_PROXY` |
| Paylaşılan IP: tek `BudgetPool`, düşük pay, seri başına yeniden deneme | §3.4 | + worker koruması |

---

## 14. Eleştiriye yanıt (ikinci hakem turu, karar "REVISE")

Her zorunlu madde kod üzerinde yeniden doğrulandı. Tek bir alt iddia dışında hepsi kabul edildi ve uygulandı.

### 14.1 Zorunlu maddeler

| # | Madde | Durum | Nerede |
|---|---|---|---|
| 1 | Spot defter eksik (`state/spot_ledger.json`; engine_v3.py:262, 2654/2657; spot_ledger.py:75) | **kabul** | §4.1, §4.2, §7.1, §7.5, §7.7, P1a kapsamı ve test 2 |
| 2 | "`initial_stop` TradeRecord'da yok" iddiası yanlış (futures_ledger.py:409, :567; engine_v3.py:2244) | **kabul**, bir inceltmeyle: iddia `FuturesLedgerV2` kayıtları için doğrudur; `SpotLedger` kayıtları `features` taşımaz (spot_ledger.py:458), bu yüzden spot stop/risk `trade_memory`/provenance'tan alınır veya `MISSING` olur ve P6'da ölçüme çevrilir | §4.3, §5.2, §5.3, §13, P2 kabul 4, P6 |
| 3 | `entries[]` 2000'de döner; geriye doğru `E_book` girdisini kaybeder | **kabul** | §3.1, §4.2, §7.1, P1a test 3–4 |
| 4 | Gün satırları kesin değil | **kabul** | §4.2, §6.1 S3, §7.1, §7.3 |
| 5 | MTM'siz P1 başlığı yanıltıcı | **kabul** (iki seçeneğin ikisi birden: `LEDGER_MARK` MTM **ve** her gerçekleşmiş sayının yanında gerçekleşmemiş; yalnız-gerçekleşmiş satırda hüküm kelimesi yok) | §7.1, §7.7, P1a test 5 |
| 6 | Config/sürüm kayması (config_v3.py:863–883) | **kabul** | §2.3, §6.1 S0, §9.2, P1a test 13–14, `docs/OPERATIONS.md` |
| 7 | Yalıtım çalışma zamanında kendini doğrulamalı; sahte VPS sınırı yazılmalı | **kabul** | §2.5, §2.8 (tablo dahil), §9.3, P1a |
| 8 | Dolaylı karar kanalları (418/429, CPU/IO) | **kabul**; ResearchCoordinator da aynı tabloya eklendi | §2.9, §3.4, P1a/P1b VPS kabulü |
| 9 | wt-tourfix ile taban karışıklığı | **kabul** | §2.9, P0, §10 girişi |
| 10 | P1 küçük değil; böl | **kabul**: P1a/P1b; iki kilit; bağımsız koşucular; küçük betik; `dukascopy_mirror.py` referansı kaldırıldı | §2.4, §9.3, §10 |
| 11 | Store doğruluğu (a–g) | **kabul**, yedi alt maddenin hepsi | §3.2, §3.4, P1b testleri |
| 12 | Hayatta kalma yanlılığı | **kabul**: zaman noktasında evren P3'te; o zamana kadar XSEC Kapı A'ya kapalı | §3.3, §6.2, §6.7, P3 |
| 13 | Çoklu test / veri madenciliği (a–e) | **kabul**. (c) için iki seçenekten "mühür sonrası ileri veri" seçildi: geçmiş veri laboratuvarlarca zaten görüldüğünden sabit bir takvim bekletmesi de gerçekten görülmemiş olmazdı | §6.3, §6.4, §6.7, §7.3 |
| 14 | İleriye dönük ≠ canlı kâğıt | **kabul**: `PROSPECTIVE_REPLAY` adı; maliyet oranı kapıdan çıktı, yerine ADVERSE/STRESS | §4.3, §6.6, §6.7, §7.1 |
| 15 | Kopya paritesi tanımsız | **kabul**: POLICY kohortu ve config dönemi başına | §5.7, §6.2, P3 kabul 1 |
| 16 | Bayat/çelişen adaylar | **kabul**: üçü de P4 listesinden çıktı; §5.10 dönem bölünmüş tarihsel yeniden üretim oldu | §1.2, §5.9, §5.10, §6.2, P4 |
| 17 | "Gerçekten dene" isteği karşılanmıyor | **kabul**: keşif katmanı | §1.1, §1.4, §6.8, P3 |
| 18 | Günlük hedef paydası belirsiz | **kabul** | §7.1, §7.2, §12 soru 12 |
| 19 | Yedekleme ve geri yükleme | **kabul** | §3.6, §6.1 S7b, P1a test 8 ve 15 |
| 20 | AST yalıtım kapsamı | **kabul** | §2.8, P1a test 11 |
| 21 | Token planı gerçekçiliği | **kabul** | §8 |
| 22 | Belge referans düzeltmeleri | **kısmen kabul** (aşağıda) | §2.2, §2.6, §4.1, §5.8, §6.3 |

**Madde 22 ayrıntısı:**
- `SegmentArchive`'in yeri `learn/journal_archive` (journal_archive.py:145) ve `pattern_trader/book.py:1317` onu zaten
  çağırıyor: **kabul**, düzeltildi (§2.6, §5.8).
- Yedek zamanlayıcısı hh:00–hh:06'ya bağlı değil: **kabul**; saat penceresi yerine `systemctl is-active
  tradingbot-backup.service` (§2.2).
- `state/tradingbot.db` WAL kipinde: **kabul**; motor SQLite'a hiç dokunmaz (§4.1, §9.2).
- **"`quant/walkforward`'ta `run_three_way` yok" — REDDEDİLDİ.** Fonksiyon vardır: `tradingbot/quant/walkforward.py:320`
  `def run_three_way(plan, assignment, *, fit_fn, candidates_fn, select_fn, evaluate_fn, ...)`, `f4190bc` ("feat(quant):
  explicit three-way train/validation/test walk-forward") ile eklendi ve `1c2c6e2`'de mevcuttur. Hakemin önerdiği
  `make_folds(validation_days>0)` de doğrudur ve ikisi birlikte kullanılır: `make_folds` üç yollu pencereleri üretir,
  `run_three_way` her adımın yalnız kendi verisini görmesini kod düzeyinde zorlar. Belge iki referansı da tutar (§2.6,
  §6.3).

### 14.2 İsteğe bağlı maddeler

| Madde | Durum | Nerede |
|---|---|---|
| k*/iflas tablosu başlıklardan ve `--check`'ten çıksın | kabul | §1.3, §6.5, §7.6, §7.7 |
| Şans oranı için maliyet bilinçli sıfır hipotezi; başlığın hangisini kullandığı yazılsın | kabul (başlık muhafazakâr işaret çevirme) | §7.2 |
| P1'de `decision_delay_s` ve sonraki fonlamaya uzaklık | kabul (`closes_derived`) | §4.2 |
| engine-app ayrı klon olsun, worktree değil | kabul | §2.3, §9.3 |
| İlk doldurma tahmini (`metrics` yalnız günlük zip) ve ilerleme/ETA | kabul (10–24 saat) | §2.2, P1b |
| Tek enstrüman için XAUUSDT yerine BTCUSDT veya toplam öner | kabul | §12 soru 4 |
| `find_books` yollarına asla yazmama özellik testi | kabul (spot yolu da eklendi) | §2.8, P1a test 12 |

### 14.3 Son Faz-1 kapsamı (P1a)

Ağsız, karar-nötr ve sahte VPS'te sınanabilir:
- `research_engine/{__init__, paths, lock, selfcheck, rawconfig, ledgers, closes, daily_target, summary, backup, night}.py`;
- vadeli **ve spot** kapanış arşivi, `entries[]` arşivi, gecelik ölçülmüş anlık görüntü, `rev`/`REVİZE`, `RESTORED_AWAY`,
  rotasyon payı;
- günlük hedef: kayıt görünümü, cüzdan görünümü, `LEDGER_MARK` MTM, GEÇİCİ/KESİN, iki payda, aylık bakış hükümleri;
- `scripts/bot_scorecard.py --daily` (spot dahil) ve D4 etiketi;
- `engine-night`, `engine-status [--brief]`, `engine-restore` (tembel import);
- yalnız `tradingbot-engine-night.{service,timer}`;
- küçük `tb-engine-<sha7>.sh` (`--dry-run`/deploy/`--check`/`--ab-report`/`--rollback`, ayrı klon, dizin oluşturma,
  kapılı kurulum, elle smoke çalıştırma, bağımsız koşucular).

P1a'da olmayanlar: ağ, store, tohum, evren, veri birimi, ilk doldurma (hepsi P1b); worker/app/dashboard/config
değişikliği; panel; `tj_v1`, atıf, kütüphane, adaylar.
