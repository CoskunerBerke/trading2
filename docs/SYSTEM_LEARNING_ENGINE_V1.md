# Sürekli Öğrenme Motoru v1 (`research_engine`) — tasarım

**Durum:** TASARIM (kod yok). Bu belge, VPS üzerinde sürekli çalışan, kayıt-yalnız (record-only) bir öğrenme ve araştırma
motorunu tanımlar. Kod tabanı `1c2c6e2` (dev hattı) üzerinden okunarak yazıldı; VPS `f8b05fb` çalıştırıyor (aynı kod,
sonraki araştırma/test commitleri hariç).

**Kısa özet:** Veri VPS'e **bir kez** indirilir ve her gün yalnız eksik kısım eklenir. Her gece, AI kullanmadan ve ağ
erişimi olmadan:
- her defterin her işlemi tüm maliyetleriyle günlüğe yazılır;
- her işlem için "neden kaybetti / neden kazandı / nasıl kâra dönebilirdi" hesaplanır;
- bilinen tüm yöntemler ve varyasyonları walk-forward ile yeniden test edilir;
- iyi görünenler önce kendi kâğıt defterlerinde **kayıt-yalnız** ileriye dönük denenir;
- kanıtlananlar sahibe (berke) **yazılı öneri** olarak sunulur.

Günlük +%1 hedefi her enstrüman (altın dahil) ve toplam için **ölçülür**; kanıt olmadan hiçbir yerde "tuttu" denmez.
Mevcut defterlerin hiçbir kararı değişmez. Her şey PAPER'dır.

Temel: hakemlerin seçtiği Tasarım 1 ("reuse-first", iki gece birimi, sabitlenmiş ayrı checkout). Tasarım 2'den
replay-fidelity kapısı, kural bağlamının yeniden kurulması (rehydration), EX_ANTE/HINDSIGHT ayrımı, önceden kayıtlı
bakışlar (looks) ve sıkı hedef hükmü alındı. Tasarım 3'ten çekirdek düzeyinde ağ yalıtımı (`PrivateNetwork=yes`), ertesi
gün arşiv uzlaştırması, evren anlık görüntüleri ve k*/iflas tablosu alındı. Hakemlerin "mutlaka düzelt" listesindeki her
madde §9 ve ilgili bölümlerde kapatıldı (bkz. §13).

---

## 1. Amaç ve dürüst beklenti

### 1.1 Sahibin istekleri → bu tasarımdaki karşılığı

| Sahibin isteği | Karşılığı |
|---|---|
| "Her seferinde bir şeyleri indirip token yiyoruz" | Veri VPS'te kalıcı (`data/research/store`, `archive_cache`); günlük yalnız ek; gece işi deterministik Python; AI yalnız ≤8 KB özet okur (§8). |
| Mevcut coinleri ve altını çok iyi öğrenen ve iyi işlem yapan sistem | Strateji kütüphanesi + gece walk-forward + kayıt-yalnız ileri adaylar + işlem dersleri (§5, §6). |
| Günde en az +%1 (tek coin/altın **veya** toplam) | Her gün, her enstrüman, her defter ve toplam için ölçülür; hükümler istatistik kuralına bağlı (§7). |
| Her işlemde her şeyi kaydet (gelir, ücret, kayma, fonlama, giriş yeri, büyüklük, kaldıraç, taktik, varyasyon) | İşlem günlüğü `tj_v1`, her alan kaynak etiketli (§4). |
| Kaybedince neden ve nasıl kâra dönebilirdi; kazanınca neden | Kural kodları + karşı-olgusal yeniden oynatma + istatistikle desteklenen dersler (§5). |
| VPS'te sürekli çalışan motor; iyi olan önce kayıt-yalnız denensin, kanıtlanan bana onaya gelsin | İki systemd zamanlayıcısı; kayıt-yalnız adaylar; terfi kapısı → yazılı öneri → sahip onayı → normal sürüm (§6.7, §9). |

### 1.2 Bugünkü kanıt (PAPER, maliyet sonrası)

| Kapsam | Ölçülen | Günlük karşılığı |
|---|---|---|
| M2 (TSMOM28) — tek pozitif defter | +0,12R / 49 işlem (%0,5 risk); elverişli 30 günde +%7,4 | ≈ +%0,25/gün (kısa, elverişli dönem) |
| Ana bot | −0,07R / 163 işlem | negatif |
| D4 Donchian 4h | −0,89R / 13 | negatif |
| C4 mum varyasyonları | −0,09R / 16 | negatif |
| Formasyon | −0,48R / 13 | negatif |
| Box | stop < %0,5: −0,56R / 72; stop %0,5–1: +0,17R / 176 | karışık |
| gold_v1 laboratuvarı | 32 hücrede 0 güçlü aday; hiçbir hücre **ayda** +%1'e ulaşmadı | — |
| crowd lab fut_v2 | 8 hipotezde 0 | — |

### 1.3 Hedefin büyüklüğü

- Günde +%1 bileşik olarak 30 günde ×1,348, yılda ×37,8 eder. Bu, panelde zaten izlenen ve henüz tutmayan **aylık +%1**
  hedefinin yaklaşık 30 katıdır.
- İşlem başına %0,5 riskle günde +%1, defter başına **her gün net +2R** demektir. Bugünkü en iyi defter işlem başına
  +0,12R yapıyor.
- Kaldıraç veya risk artırmak tek tek +%1 günleri mekanik olarak üretebilir, ama iflas riskini de büyütür. Motor bunu
  yalnız "HİPOTETİK" etiketli bir satırda, düşüş ve iflas olasılığıyla birlikte gösterir (§7.6). Bunu asla başarı
  olarak sunmaz.

### 1.4 Gerçekçi beklenti

- **Kesin olanlar:** veri bir kez iner; rutin öğrenme 0 AI token harcar; her işlem tüm alanlarıyla kaydedilir; her
  kazanç ve kayıp kanıtla açıklanır; hiçbir defter sahibin yazılı onayı olmadan değişmez; "bugün +%1 yaptık mı, nerede,
  gerçek mi?" sorusuna her gün dürüst cevap verilir.
- **Muhtemel olanlar:** kütüphanenin büyük kısmı walk-forward'da elenir (bu, sistemin çalıştığını gösterir). Varsa
  birkaç taktik maliyet sonrası işlem başına +0,05…+0,2R civarı küçük bir avantaj gösterebilir. İlk 3–6 ayda toplam
  günlük getirinin ortalaması büyük olasılıkla −%0,1 ile +%0,1 arasında kalır.
- **Beklenmeyen:** kanıtlı, sürdürülebilir +%1/gün. Tek coinde +%1 günleri **olacaktır**; ancak 40+ enstrüman arasında
  seçim ve şans yüzünden bunlar kanıt değildir. Motor bunları "HEDEF GÜNÜ" olarak sayar, başarı olarak saymaz.

---

## 2. Mimari

### 2.1 İlkeler

1. **Worker'a dokunulmaz.** Worker, defterler, `config.yaml` ve `/opt/tradingbot/app` değişmez. Motor ayrı bir
   checkout'tan, ayrı cgroup'larda çalışır.
2. **Tek yön.** işlemler → günlük → atıf → dersler → önceden kayıtlı aday → kayıt-yalnız ileri test → öneri → sahip
   onayı → normal sürüm. Motorun hiçbir karar yoluna yazma yolu yoktur.
3. **State salt-okunur.** Motor `data/state` ve `data/market` altına hiç yazmaz; bu çekirdek düzeyinde
   (`ProtectSystem=strict` + yalnız `ReadWritePaths=/opt/tradingbot/data/research`) garanti edilir.
4. **Ağ yalnız veri biriminde.** Gece araştırma birimi `PrivateNetwork=yes` ile çalışır: araştırma sırasında indirme
   çekirdek düzeyinde imkânsızdır.
5. **Mühürlü ön kayıt.** Her eşik, ızgara, kapı ve hedef kuralı koda ve bu belgeye sha ile sabitlenir; değişiklik =
   yeni sürüm + yeni deneme sayımı.
6. **Dürüst etiket.** Her alan MEASURED / RECONSTRUCTED / MODELED / MISSING olarak etiketlenir; eksik gün EKSİK olarak
   görünür, sıfır sayılmaz.

### 2.2 Süreçler ve zamanlama (VPS saati Europe/Istanbul; tüm `OnCalendar` satırları açıkça `UTC` yazar)

| Birim | Ne zaman | Ağ | Ne yapar | Kaynak |
|---|---|---|---|---|
| `tradingbot-worker` | sürekli (değişmez) | var | turlar (~20,5 dk'da bir, ~5,5 dk), defterler | 6G (VPS override) |
| `tradingbot-dashboard` | sürekli (P1'de değişmez) | — | panel | 512M |
| **YENİ** `tradingbot-engine-data.service` + `.timer` | `OnCalendar=*-*-* 00:41:00 UTC`, `TimeoutStartSec=50min` (sert durma 01:31) | **var** (tek ağlı birim) | arşiv-önce veri ekleme, REST kuyruğu, arşiv uzlaştırma, evren anlık görüntüsü, `data_status.json` | MemoryHigh 0,8G / MemoryMax 1G (P0'da kesinleşir) |
| **YENİ** `tradingbot-engine-night.service` + `.timer` | `OnCalendar=*-*-* 01:37:00 UTC`, iç son tarih 03:40, `TimeoutStartSec=2h15min` (sert durma 03:52) | **yok** (`PrivateNetwork=yes`) | S0–S8 aşamaları (§6.1) | MemoryHigh 1,2G / MemoryMax 1,5G (P0'da kesinleşir) |
| `tb-engine-backfill` (geçici `systemd-run`) | bir kez, sahip başlatır | var | ilk doldurma; kaldığı yerden devam eder | MemoryMax 1G |

Zamanlama kuralları (birim sözleşme testiyle zorunlu):
- `OnCalendar` her satırında `UTC` yazmalıdır. Yoksa zamanlayıcılar 3 saat kayar ve 04:00 yayın penceresine düşer.
- Hiçbir motor işi hh∈{00,04,08,12,16,20} için hh:00–hh:35 aralığında (4h indeks yayın pencereleri) ve hh:00–hh:06
  aralığında (saatlik yedekler) **başlamaz**. Gece biriminin iç son tarihi (03:40) 04:00 penceresinden önce biter.
- İlk doldurma 2–4 saat sürebilir. Bu yüzden kendi saatine bakar ve 4h pencerelerinde (hh:00–hh:35) **kendini duraklatır**.
- `Persistent=false`: kaçırılan çalıştırma açılışta telafi edilmez (açılıştaki ilk turla çakışmasın diye).

### 2.3 Ayrı, sabitlenmiş checkout; config ve ortam

- Kod `/opt/tradingbot/engine-app` altından çalışır. Bu, aynı deponun etiketli bir SHA'da sabitlenmiş ayrı bir
  checkout'udur. Motor sürümleri `/opt/tradingbot/app`'e ff-merge **yapılmaz**. Böylece worker durmaz, yeniden başlamaz
  ve 44 dakikalık ilk tura girmez.
- Venv paylaşılır (`/opt/tradingbot/venv`); yeni bağımlılık yok (pandas, numpy, pyarrow mevcut). `tradingbot` venv'e
  pip ile kurulu **değildir**. Bu yüzden `ExecStart=/opt/tradingbot/venv/bin/python -s -m tradingbot ...` ve
  `WorkingDirectory=/opt/tradingbot/engine-app` kullanılır: `-m` çalışma dizinini `sys.path`'e ekler. `python -I`
  **kullanılmaz**, çünkü `-I` hem `PYTHONPATH`'i hem çalışma dizinini yok sayar ve import'lar kırılır. Birim sözleşme
  testi bunu da denetler.
- **Config kayması:** `config.DEFAULT_CONFIG_PATH = PROJECT_ROOT/config.yaml` olduğu için ayrı checkout kendi
  `config.yaml`'ını yükler. Motor bu yüzden canlı config'i **açık yolla ve salt-okunur** yükler:
  `--config /opt/tradingbot/app/config.yaml`. Bu dosyanın sha256'sını her çalıştırmada `run_status.json`'a yazar.
  Varsayılan yolla yüklemeyi reddeden bir test vardır.
- **Ortam:** `EnvironmentFile` yoktur (sır yok). Yalnız şu `Environment=` satırları vardır:
  - `TRADINGBOT_DATA=/opt/tradingbot/data`
  - `TRADINGBOT_STATE_DIR=/opt/tradingbot/data/state`
  - `ALLOW_LIVE_TRADING=false`
  - `TZ=UTC`
  - `PYTHONUNBUFFERED=1`
  - `PYTHONIOENCODING=utf-8`
- Her çalıştırma S0'da PAPER modunu doğrular (mod durumu salt-okunur okunur). PAPER değilse veya okunamıyorsa çıkar.

### 2.4 Kilit ve çakışma kuralları

- **Tek bir motor-geneli kilit** vardır: `data/research/locks/engine.lock` (fcntl, `SingletonLock` deseni). Data, night,
  backfill ve sahibin elle çalıştırdığı her `engine-*` komutu bu kilidi alır.
- Kilidi alamayan birim bekleme yapmaz: `SKIPPED_LOCKED` sonucu yazar ve 0 koduyla çıkar. Bu, `--check`'te görünür.
- Zaman ayrımı ek güvencedir: data en geç 01:31'de biter, night 01:37'de başlar. Tasarım gereği iki zamanlayıcı birimi
  aynı anda çalışmaz. Bu yüzden bellek bütçesinde yalnız **en büyük** motor biriminin `MemoryMax`'ı hesaba girer.
- Motor worker kilidini **hiç** almaz. Worker'ın HistoryStore'una (`data/market/history`) **hiç** yazmaz: ayrı kök
  kullanılır, worker'ın IndexRefresher'ı ile yarış yapı gereği yoktur.

### 2.5 Kaynak sınırları

Her iki birim için ortak sınırlar:

| Ayar | Değer |
|---|---|
| `User` | `tradingbot` |
| `Nice` | `19` |
| `CPUWeight` | `10` (worker varsayılanı 100) |
| `CPUQuota` | `100%` (`nproc` < 4 ise `60%`) |
| `IOSchedulingClass` / `IOWeight` | `idle` / `10` |
| `OOMScoreAdjust` | `1000`: bellek darlığında çekirdek önce motoru öldürür |
| Dosya sistemi | `ProtectSystem=strict`, `ReadWritePaths=/opt/tradingbot/data/research`, `ReadOnlyPaths=/opt/tradingbot/data /opt/tradingbot/app` |
| Diğer | `Type=oneshot`, `Persistent=false` |
| `OnFailure` | `tradingbot-alert@%n.service`, yalnız o birim VPS'te varsa (P0 denetimi) |

**P0 bellek kuralı:** worker MemoryMax 6G + dashboard 0,5G + en büyük motor birimi ≤ fiziksel RAM − 1G. Sığmazsa
`MemoryHigh` düşürülür; `OOMScoreAdjust=1000` motorun önce ölmesini garanti eder.

**Kabul ölçütü (her aşamada):**
- Motor çalışırken worker tur p95 süresi, P0 taban çizgisinin en fazla +%5 üstünde kalır.
- Worker `NRestarts` değişmez.
- Motor cgroup'unun `memory.peak` değeri ≤ 0,8 × MemoryMax olur.

### 2.6 Bileşenler

Paket `tradingbot/research_engine/`; Türkçe docstring'ler; bu belge.

| Modül | Yeni / yeniden kullanım | Görev | Aşama |
|---|---|---|---|
| `paths.py`, `lock.py` | YENİ | kök düzeni, disk koruması (araştırma kökü ≤ 20 GB, boş disk ≥ 10 GB, yoksa çalışmayı reddet), motor kilidi | P1 |
| `store.py` (`ResearchStore`) | `history/store.HistoryStore`'u ALT SINIF olarak kullanır | ay-parçası checksum'ı, seri başına kilit, yeni türler `metrics_5m`, `duka_1h`, `duka_1m` | P1 |
| `universe.py` | `universe.json` salt-okunur + YENİ | araştırma evreni U_R, günlük evren ve `exchangeInfo` anlık görüntüsü | P1 |
| `datastore.py` | `HistoryCollector`, `IncrementalUpdater`, `ArchiveClient`, `RateBudget`/`BudgetPool` | spec listesi, seri başına yeniden deneme/yakalama, düşük REST bütçesi, arşiv uzlaştırma, `data_status.json` + `data_seal` | P1 |
| `seed.py` | YENİ | worker deposundaki mevcut parquet'leri salt-okunur kopyala + doğrula (yeniden indirme yok) | P1 |
| `provider.py` (`StoreProvider`) | YENİ (~80 satır) | `.klines(symbol, interval, limit, start_ms, end_ms)` ile ağsız bar; parite testi | P1 |
| `closes.py` | `FuturesLedgerV2.load` (saf okuma) | ham kapanış arşivi: her kapanmış `TradeRecord`'un birebir kopyası, revizyonlu | P1 |
| `daily_target.py` | `bot_scorecard.find_books`, `_window` | günlük hedef ölçümü (P1: gerçekleşmiş; P2: MTM) | P1/P2 |
| `summary.py` | YENİ | `engine_status`, `digest_tr.md`, `engine_summary.json` (boyut sınırlı) | P1 |
| `journal.py` | ledger + shared_experience + trade_memory + provenance + position_path + plans + CF kayıtları | işlem günlüğü `tj_v1` | P2 |
| `pathrec.py` | `learning_cf` bar içi sıra kuralı | 1m/5m yol yeniden kurma | P2 |
| `rehydrate.py` | kuralların saf fonksiyonları (`box_theory`, `donchian_trend`, `candle_book`, `ema200_trend`, `paper_rules`) | defterlerin kapanışta düşürdüğü sinyal bağlamını yeniden kurma (1 tick eşleşme) | P2 |
| `fidelity.py` | `learning_cf.ExecModel.of_ledger`, `_net_replay` | gerçek işlemin yeniden oynatma doğruluğu (≤ 0,05R) | P2 |
| `attribution.py`, `cfgrid.py` | `learn/labels`, `learn/postmortem` kodları, `learning_cf._decompose`, `learning_cf_aux` | kural kodları, karşı-olgusal ızgara, ayrıştırma | P2 |
| `lessons.py` | `learn/lesson_store` (`build_lesson`, `transition`, `SegmentArchive`; ilk gerçek çağıranlar) | ders deposu, ayrı kök | P3 |
| `trials.py` | YENİ | deneme defteri (`trials.jsonl`) | P3 |
| `library/` | kural fonksiyonları, `signal_lab`, `candle_lab`, `gold_lab(_v2)`, `book_lab` tanımları | strateji kütüphanesi `LIB_v1` | P3 |
| `wf.py`, `cscv.py` | `quant/walkforward` (`make_folds`, `leakage_check`, `fold_report`, `run_three_way`), `quant/execution_scenarios`, `validation.deflated_sharpe` / `probabilistic_sharpe` | gece walk-forward; PBO için YENİ CSCV | P3 |
| `prospective.py` | `FuturesLedgerV2`, `ExecModel`, `learning_mode.fit_size` | kayıt-yalnız ileri adaylar | P4 |
| `promotion.py` | `quant/champion.evaluate_challenger`, `research_policy` eşli istatistikleri | mühürlü terfi kapısı `PROMOTION_REGISTRY` | P4 |
| `night.py` | `replay/pipeline.py` + `deploy/replay_runner.sh` desenleri | aşama orkestrasyonu, son tarih, telemetri | P1 (iskelet) |
| CLI (`cli_v3`) | YENİ alt komutlar | `engine-data`, `engine-night`, `engine-status [--brief]`, `engine-query <konu>` | P1/P3 |

### 2.7 Veri akışı

```
data.binance.vision / fapi (düşük bütçe)          [engine-data 00:41 UTC, AĞLI]
        │
        ▼
research/archive_cache  ──►  research/store (ResearchStore)  ──►  data_status.json + data_seal
                                         │
════════════════════ PrivateNetwork=yes ═╪═══════ [engine-night 01:37–03:40 UTC] ═════════
                                         ▼
state/** (SALT-OKUNUR) ─► S1 kapanış arşivi + günlük tj_v1 ─► yol + rehydrate + fidelity
                                         │
                                         ▼
                         S2 atıf (kodlar + cfgrid) ─► S6 dersler (önceden kayıtlı bakışlar)
                                         │                       │
S4 kütüphane walk-forward ◄──────────────┘     ders → aday spec ─┘
        │                                                 │
        ▼                                                 ▼
  trials.jsonl ──► Kapı A ──► S5 kayıt-yalnız ileri aday (kendi kâğıt defteri) ──► Kapı B
                                                                                     │
S3 günlük hedef ──► S7 özet (digest_tr.md, engine_summary.json)       Kapı C: ÖNERİ dosyası
                                                                                     │
                                                    SAHİP ONAYI ─► normal sürüm (yeni kâğıt defter)
```

### 2.8 Yalıtım ve doğrulama

- **AST testi:** Hiçbir karar modülü (`engine_v3`, `strategy_paper*`, `pattern_trader`, `coinhead`, `learn/*`,
  `economics_gate`, `risk/*`, `shared_experience/*`, `dashboard/*`) `research_engine`'i import etmez.
- **Dashboard sınırı:** Dashboard motor çıktılarını yalnız JSON olarak okur. Şema sabitleri, eşitlik testleriyle
  eşleştirilir; import yoktur (shared_experience sözleşmesiyle aynı).
- **Dosya açma modu denetimi:** Sahte bir state ile tam bir gece çalıştırılır; `open`/`os.replace`/`Path.write_*`
  monkeypatch'lenir. Testin kanıtladıkları: `data/research` dışına hiç yazma olmaz ve tüm ledger'lar `"r"` modunda
  açılır.
- **Ledger parmak izi gece değişmezi DEĞİLDİR.** `ops/fingerprint.py` `fingerprint_v1` açık pozisyonları da hash'ler ve
  canlı worker bunları her turda değiştirir. Bu yüzden gece öncesi/sonrası parmak izi her gece yanlış alarm verirdi.
  Parmak izi yalnız **sürüm (deploy) anında** kullanılır. Gece güvencesi çekirdekten (`ReadOnlyPaths`) ve yukarıdaki
  açma modu testinden gelir.

---

## 3. Veri deposu

### 3.1 Kök düzeni: `/opt/tradingbot/data/research/`

State altında olmadığı için saatlik yedekleri şişirmez.

| Yol | İçerik | Saklama |
|---|---|---|
| `store/<market>/<SYM>/<tf>/<YYYY>/<MM>.parquet` + `manifest.json` | mumlar, fonlama, `metrics_5m`, Dukascopy | sonsuza dek (değişmez geçmiş) |
| `archive_cache/<host>/<url yolu>` (+ `.missing`) | data.binance.vision zip aynası (`gold_lab.ArchiveCache` / `book_lab.ZipCache` düzeni, bayt-özdeş) | ≥5m zip'ler sonsuza dek; 1m zip'ler doğrulanmış alımdan sonra silinir (checksum manifestte kalır) |
| `dukascopy/XAUUSD/...` + `manifest.jsonl` | Dukascopy XAUUSD BID (yalnız tarihsel) | sonsuza dek |
| `universe/YYYY-MM-DD.json`, `exchangeinfo/YYYY-MM-DD.json.gz` | günlük evren ve listeleme/delist anlık görüntüsü | sonsuza dek |
| `closes/<book>/YYYY-MM.jsonl.gz` | ham kapanış arşivi (P1) | sonsuza dek, mühürlü segmentler |
| `journal/tj_v1/YYYY-MM.jsonl(.gz)` | işlem günlüğü (P2) | sonsuza dek |
| `paths/YYYY-MM/<trade_key>.parquet` | yeniden kurulmuş yollar | sonsuza dek (küçük) |
| `attribution/YYYY-MM.jsonl.gz` | kodlar + ızgara | sonsuza dek |
| `lessons/`, `trials/trials.jsonl`, `library/registry.json` | dersler, denemeler, mühürlü kütüphane | sonsuza dek |
| `backtests/<tarih>/` | walk-forward ayrıntısı | 14 gece |
| `prospective/<aday_id>/` | aday defterleri | sonsuza dek |
| `proposals/<id>.json` + `.tr.md` | öneriler | sonsuza dek |
| `target/daily.jsonl` | günlük hedef satırları | sonsuza dek |
| `summary/` | `data_status.json`, `run_status.json`, `daily_target.json`, `engine_summary.json` (≤ 256 KB), `digest_tr.md` (≤ 8 KB) | en son |
| `runs/<run_id>/run_status.json` | çalıştırma geçmişi | son 30 |
| `locks/engine.lock` | motor kilidi | — |

### 3.2 `ResearchStore` (HistoryStore alt sınıfı; worker deposu etkilenmez)

- **Ay-parçası checksum'ı.** `HistoryStore._recompute` (store.py:215) her yazımda tüm seriyi yeniden okur. 5m/1m
  doldurmada bu O(n²) olur. Alt sınıf yalnız dokunulan ayın parça checksum'ını günceller. Tam seri checksum'ı gecede
  bir kez, data biriminin sonunda hesaplanır.
- **Kilit.** Seri başına fcntl kilidi vardır (motor-geneli kilidin içinde, ek güvence).
- **Yeni türler.**
  - `metrics_5m`: `[timestamp, oi, oi_usdt, top_acct_ls, top_pos_ls, global_ls, taker_ls_vol]`; `futures_data` /
    `crowd_data` önbelleklerinin yerini alır.
  - `duka_1h`, `duka_1m`: `[timestamp, open, high, low, close, volume_proxy]`.
- **Yalnız kapanmış barlar** yazılır. µs→ms normalizasyonu yapılır (PAXG spot 2025+). Checksum uyuşmazlığında bozuk
  parça işaretlenir ve o seri durur (fail-closed).
- **İlk tohum.** Worker'ın `data/market/history` altındaki mevcut parquet'leri salt-okunur kopyalanır (cp, ardından
  validate). Böylece worker'ın elindeki seriler (örn. 89 vadeli 4h serisi) yeniden indirilmez.

### 3.3 Kapsam (araştırma evreni U_R)

| Piyasa | Semboller | Zaman dilimleri | Başlangıç |
|---|---|---|---|
| USDⓈ-M vadeli | giriş evreni (40, `universe.json`) ∪ BTC/ETH ∪ son 180 günde herhangi bir defterin veya CF'nin işlem yaptığı semboller | 5m, 15m, 1h, 4h, 1d; fonlama; `metrics_5m`; mark/premium 1h | listelenme (metrics ≈ 2021-12) |
| **Altın vadeli** | **XAUUSDT** (2025-12-01'den), **PAXGUSDT** (2025-03-01'den) | 1m–1d + fonlama | listelenme |
| Altın spot | PAXGUSDT | 5m, 1h, 4h, 1d | 2020-08 |
| Spot bağlam | BTCUSDT, ETHUSDT | 1h, 1d | 2020 |
| Dukascopy | XAUUSD BID | 1h (2006+), isteğe bağlı 1m (2019+) | yalnız VPS erişebiliyorsa (P0); değilse sahip bir kez kendi PC'sinde indirip `scp` ile taşır |
| 1m (yol için) | son 120 günde **herhangi bir defterin veya adayın** işlem yaptığı her sembol + iki altın vadelisi | 1m | son 400 gün, **tembel** (yalnız gerektiğinde) |

Not: `config.yaml` PAXG'yi yaş filtresiyle işlem evreninden çıkarır. Altın, araştırma evrenine **açıkça** eklenir.

**Boyut tahmini (~45 B/satır).** Toplam ≈ 3–4 GB parquet + ≈ 2–5 GB zip. Kalem kalem:

| Kalem | Boyut |
|---|---|
| 5m | ≈ 1,0 GB |
| 15m | ≈ 0,35 GB |
| 1h / 4h / 1d | ≈ 0,15 GB |
| `metrics_5m` | ≈ 1,2 GB |
| 1m | ≈ 0,5 GB |

Disk 72 GB, %38 dolu. Araştırma kökü için sınırlar: 15 GB'ta uyarı, 20 GB'ta çalışmayı reddet.

### 3.4 Günlük ekleme (`engine-data --update`, 00:41 UTC)

1. **Önce arşiv.** Eksik aylık zip'ler (ayın 2–4'ünde yayımlanır) ve dünün günlük zip'leri indirilir: klines (tüm
   zaman dilimleri), fundingRate, metrics. `.CHECKSUM` doğrulanır.
2. **REST kuyruğu.** Henüz arşivde olmayan son saatlerin **kapanmış** barları REST ile alınır. Bu barlar manifestte
   `source=rest` olarak işaretlenir.
3. **Arşiv uzlaştırma.** Günlük zip yayımlanınca REST'ten gelen barlar checksum doğrulanmış arşiv barlarıyla
   değiştirilir. Fark satır satır `runs/`'a yazılır (beklenen: 0).
4. **Anlık görüntü.** Evren ve `exchangeInfo` (listeleme/delist) günlük olarak kaydedilir. Hayatta kalma yanlılığı
   bugünden itibaren düzeltilebilir hale gelir.
5. **Hata yalıtımı.** Her seri 3 denemeli, titreşimli (jitter) bir yeniden denemeye sarılır ve istisnası yakalanır.
   Bir serinin hatası o seriyi `STALE` yapar; çalıştırmayı durdurmaz. Bu, `history-collect`'in ilk hatada durma
   açığını kapatır.
6. **Mühür.** `data_status.json` (şema `engine_data_status_v1`) her seri için `last_ts`, `rows`, `gaps`, `quality` ve
   `stale` alanlarını yazar. `data_seal` tüm manifest checksum'larının sha256'sıdır. Sonraki her çıktı bu mührü anar;
   her sonuç mühürden yeniden üretilebilir.

**REST bütçesi (paylaşılan IP).** Worker `rate_budget_safety=0.7` (config_v3:86) ile 2400/dk fapi limitinin %70'ini
kullanır. `BudgetPool` süreç başınadır; bu yüzden motorun payı ayrıca sınırlanmalıdır:

| Kural | Değer |
|---|---|
| Motor güvenlik payı | **≤ 0,1** (≤ 240 ağırlık/dk); tüm seriler tek `BudgetPool` paylaşır |
| Öncelik | geçmiş aylar için REST yok, önce arşiv (CDN ağırlık harcamaz) |
| Başlık takibi | her yanıtta `X-MBX-USED-WEIGHT-1M` okunur; IP limitinin %50'sine ulaşınca bekler |
| Ret kodları | 418/429 gelince REST adımı durur, ilgili seriler `STALE` olur, kayıt düşülür |
| Saatlik REST kuyruğu | yok (gece-önce tasarım) |

Tipik günlük maliyet birkaç yüz ağırlıktır.

### 3.5 `StoreProvider` ve laboratuvarlar

- `StoreProvider`, `.klines(symbol, interval, limit, start_ms, end_ms)` imzasını uygular (`signal_lab.ArchiveProvider`
  ile aynı 7 sütun + `is_closed`; geniş şema varyantı taker alanlarını da verir). Ayrıca `funding_frame()` sağlar.
- **Parite kapısı.** Mühürlü bir laboratuvar `StoreProvider`'a ancak şu koşulla geçer: 3 sembol × 3 tf × 6 ay için
  float64 ve zaman damgası bayt eşitliği **ve** `book_lab` `series_digest` eşitliği sağlanmalı (REST kaynaklı kuyruk
  barları dahil). O zamana kadar mühürlü laboratuvarlar `--cache /opt/tradingbot/data/research/archive_cache --offline`
  ile çalışır. Baytlar özdeş olduğu için sha256 digest'leri geçerli kalır.

---

## 4. İşlem günlüğü şeması (`tj_v1`)

### 4.1 Kaynaklar (hepsi salt-okunur)

- Her defterin `futures_ledger.json` `history[]` kayıtları: kanonik para. `TradeRecord`'da `pnl` = `net_pnl`, ve
  `fees`, `entry_fee`, `exit_fee`, `funding_paid`, `funding_received`, `slippage_cost`, `spread_cost`, `gross_pnl`,
  `fills[]`, `leverage`, `liquidation_price`, `quantity`, `effective_notional/margin` alanları vardır.
- `shared_experience` satırları `xp_entry` / `xp_outcome` / `xp_cf`: `situation_v1`, R cinsinden maliyet, kohort.
- `trade_memory.jsonl`: ana bot ajan bağlamı; strateji defterlerinde ema200/atr14.
- `entry_provenance.jsonl`: `decision_id`, `code_sha`.
- `position_path.jsonl`: ana bot yol anlık görüntüleri.
- `pattern_trader/plans.json`: Formasyon planları.
- `counterfactual_trades.json`, `shadow_book.json` ve arşivleri.
- Motorun kendi aday defterleri (P4).

### 4.2 Satır kimliği, revizyon, rotasyon

- `trade_key = book|trade_id|opened_at`. Satırlar yalnız eklenir. Bir kapanmış kaydın içeriği değişirse
  (`settle_late_funding` geç fonlama yazar), aynı anahtarla `rev+1` satırı eklenir. Son 7 günde kapananlar her gece
  yeniden eşitlenir.
- **Rotasyon.** Ledger `history_keep=5000` sınırını aşınca en eskileri atar (futures_ledger:573). Ham kapanış arşivi
  (P1) her gece her kapanmış kaydın birebir kopyasını alır; bu yüzden rotasyon artık veri kaybettirmez. İlk sınıra
  Box ulaşacak. `--check`, her defter için "rotasyona kalan kayıt" payını gösterir; pay 1 günlük kapanışın altına
  inerse uyarır.
- **Uzlaştırma değişmezi.** Ledger'ın **elinde tuttuğu pencerede**, defter ve ay başına Σ günlük `net_pnl` = Σ ledger
  `TradeRecord.pnl` (tolerans 1e-6 USDT) olmalıdır. Aynı eşitlik ücret, fonlama ve kayma toplamları için de aranır.
  Kayıt sayısı eşitliği de aranır. Uyuşmazlıkta o gece `INCONSISTENT` işaretlenir; ders ve öneri yazılmaz.
- **Ücret özdeşliği.** `fees == entry_fee + exit_fee` her işlemde denetlenir. `learn/labels.label_outcome`
  (labels.py:45), `entry_fee` varken `fees + entry_fee + exit_fee` toplayarak ücreti iki kez sayar. Motor bu fonksiyonu
  olduğu gibi **kullanmaz**; kendi hesabını yapar ve bunun için bir regresyon testi vardır. Worker içindeki düzeltme
  ayrı, onaylı bir iştir (P6).

### 4.3 Alanlar (sahibin listesi kalın)

Her alanın yanında `field_source` ∈ {`MEASURED`, `RECONSTRUCTED`, `MODELED`, `MISSING`} tutulur.

| Grup | Alanlar |
|---|---|
| Kimlik | `schema`, `trade_key`, `rev`, `source` (`LIVE_PAPER` / `PROSPECTIVE_PAPER` / `BACKTEST` / `COUNTERFACTUAL`; asla karıştırılmaz), `book`, `book_name`, `trade_id`, `decision_id`, `code_sha`, `config_hash` |
| Enstrüman | **`symbol`**, `instrument_class` (`COIN` / `GOLD`), `venue` (`UM_futures` / `spot`), `side` |
| **Taktik ve varyasyon** | **`tactic`** (`setup_type`/strateji; Box/D4/C4 için düzeltilmiş), `family` (TREND / MOMENTUM / FADE / BREAKOUT / CANDLE_PATTERN / CARRY / XSEC / SEASONAL / MAIN_ENSEMBLE), **`variation_id`**, `params_hash` (C4: `definition_sha`; T2/M2/Box/D4: kural modülü kaynağı + parametrelerin hash'i, P6'ya kadar), `cohort` (POLICY / CAPACITY / SELECTIVITY / UNTAGGED), `record_only`, `learning_unlocked_by[]` |
| Zaman | `signal_ts`, `opened_at`, `closed_at`, `hold_hours`, `bars_held`, `decision_delay_s` |
| **Giriş yeri** | `ref_price`, **`entry_fill`** (VWAP), `entry_slip_bps`, `range_pos_20` (0–1), `dist_20d_high_atr`, `dist_20d_low_atr`, `dist_ema200_atr`, `dist_level_atr` (kutu kenarı / kanal / tetik), `session` (ASIA / LONDON / NY / WEEKEND), `utc_hour`, `weekday`, `funding_rate_at_entry`, `min_to_next_funding`, `situation_entry` (`situation_v1`) |
| **Büyüklük ve kaldıraç** | **`qty`**, **`notional`**, `margin`, **`leverage`**, `liquidation_price`, `liq_distance_in_stops`, `risk_usdt`, `risk_pct_of_equity`, `equity_at_entry`, `size_rule` (SLOT / BUMP_MIN_NOTIONAL / SHRUNK_TO_MARGIN) |
| Plan | `initial_stop`, `stop_dist_pct`, `stop_dist_atr`, `targets[]`, `tp1_fraction`, `breakeven_at_mfe_r`, `max_hold`, `planned_rr_after_cost` |
| Çıkış | `exit_price` (dolumların VWAP'ı), `exit_reason`, `exit_basis` (LEVEL / GAP), `tp1_done`, `fills[]` (tür, zaman, miktar, fiyat, ücret, kayma) |
| **Gelir ve maliyet** | **`gross_pnl`**, **`entry_fee`**, **`exit_fee`**, **`slippage_cost`** (MODELED), **`spread_cost`** (defterde 0 → `MODELED_ZERO`; ayrıca kaydedilmemiş tahmin `spread_est`), **`funding_paid`**, **`funding_received`**, `funding_net`, `funding_complete`, **`net_pnl`**, `net_r`, `gross_r`, `cost_r{fee, slippage, funding}`, `pnl_pct_book_equity`, `pnl_pct_total_equity` |
| Yol | `mfe_r`, `mae_r`, `t_mfe`, `t_mae`, `order` (MFE_FIRST / MAE_FIRST), `time_to_1r`, `giveback_r`, `capture_ratio`, `path_source` (POSITION_PATH / STORE_1M / STORE_5M / EXTREMES_ONLY), `ambiguous_bars` |
| Bağlam | `situation_exit` (aynı saf fonksiyon ve `SCHEMA_SHA` ile store barlarından yeniden hesaplanır), `btc_ctx_entry/exit`, `oi_change_24h`, `taker_ratio`, ana bot ajan eğilimleri/uyarıları (varsa), `signal_ctx{}` (yeniden kurulmuş kural bağlamı), altına özgü: oturum, PAXG/XAU bazı |
| Atıf (P2) | `codes_loss[]`, `codes_win[]`, `primary_code`, `fidelity{ok, delta_r}`, `cf_grid_ref`, `lesson_ids[]` |
| Köken | `sources[{file, offset/row_id}]`, `data_seal`, `missing_fields[]`, `journal_version` |

**`TradeRecord`'da olmayanlar.** `initial_stop` ve `targets` ledger kaydında **yoktur** (accounting/models.py:457).
Kaynak sırası şöyledir:
1. `xp_entry` / provenance;
2. kural bağlamının yeniden kurulması (§5.2); bu yalnız giriş referansı ve ilk stop ledger ile **1 tick** içinde
   eşleşirse kabul edilir;
3. hiçbiri yoksa `MISSING`.

`MISSING` stop'u olan işlemin net R'ı yeniden hesaplanmaz; işlem karşı-olgusal ızgaraya ve ayrıştırmaya **girmez**.
Asla tahminle doldurulmaz.

---

## 5. Neden kaybetti / neden kazandı

### 5.1 Yol yeniden kurma (`pathrec`)

- Kaynak: 1m barlar, yoksa 5m. Ana bot için ayrıca `position_path`.
- Bar içi sıra `learning_cf` kuralıdır: açılış → ters → lehte → kapanış.
- Yol, `opened_at`'tan `closed_at`'a kadar kurulur; üstüne çıkış sonrası 48 barlık kuyruk eklenir (STOPPED_THEN_REVERSED
  için).
- Etiket her zaman `RECONSTRUCTED`'dır. Belirsiz bar içi sıraya sahip işlemler sayılır ve sıraya dayanan kodlardan
  dışlanır.

### 5.2 Kural bağlamını yeniden kurma (`rehydrate`)

Strateji defterleri kapanışta bağlamı düşürür. Motor, defterin deterministik kural fonksiyonunu store barları üzerinde
`signal_ts` anına göre yeniden çalıştırır ve şunları geri kazanır:

| Defter | Geri kazanılan bağlam |
|---|---|
| Box | `box_high` / `box_low` / `box_mid`, konum, gün aralığı, `params_label` |
| D4 | `channel_high20`, ATR sınırları |
| T2 / M2 | `ema200`, `atr14` |
| C4 | sinyal bloğu |

Kabul koşulu: yeniden hesaplanan giriş referansı ve ilk stop ledger ile 1 tick içinde eşleşmelidir. Eşleşmezse
`RECONSTRUCT_FAILED` yazılır; tahmin edilmez. Hedef: Box, D4, T2, M2, C4 için eşleşme oranı ≥ %95.

### 5.3 Yeniden oynatma doğruluğu (fidelity) kapısı

Her gerçek işlem, defterin kendi `ExecModel.of_ledger(book)` maliyet modeliyle (cf_label_v3 ile aynı kod yolu) atılabilir
bir `FuturesLedgerV2` içinde gerçek yol üzerinde yeniden oynatılır.
- `|r_replay − r_actual| ≤ 0,05R` ise işlem karşı-olgusal ayrıştırmaya girer.
- Değilse işlem yalnız kural kodları alır. Nedeni (yol boşluğu, belirsiz bar, farklı dolum tabanı) sayılır ve özette
  gösterilir.
- Hedef doğruluk oranı ≥ %90'dır.

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

### 5.5 "Nasıl kâra dönebilirdi": karşı-olgusal ızgara (`cfgrid_v1`, mühürlü)

Bir taktiğin her işlemi **aynı** ızgarayla oynatılır, böylece karşılaştırmalar eşli (paired) olur ve seçilmiş
(cherry-picked) olmaz. Her varyant aynı gerçek yol, aynı `ExecModel` ve aynı `_net_replay` ile oynatılır. USDT riski
sabit tutulur; büyüklük stop mesafesine uyarlanır. Yaklaşık 24 varyant:

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

### 5.7 Ders istatistikleri

- **Hücreler:** (defter, taktik, varyasyon, enstrüman veya sınıf, durum kovası). Veri olan en kaba geri çekilme düzeyi
  kullanılır (shared_experience S0..S5 düzeni); çaprazlar en fazla 2 derinlikte tutulur.
- **Hücre başına hesaplananlar:**
  - n ve farklı gün sayısı;
  - ortalama net R;
  - Wilson aralıklı kazanma oranı;
  - ebeveyn hücreye doğru `HierarchicalRate` büzülmesi (alpha 10);
  - gün kümelenmeli bootstrap CI95 (sabit tohum, 5000 yeniden örnekleme);
  - her EX_ANTE varyant için gerçeğe göre **eşli** delta R ve kendi gün kümelenmeli CI'ı.
- **Ders türleri:**
  1. IZGARA: "T taktiği için V varyantı ortalama net R'ı Δ artırır".
  2. FİLTRE: "X kovasında T'yi atla"; eşsizdir, Δ = −Σ R.
  3. MALİYET: maker girişi, fonlamadan kaçınma.
  4. AYRIM: kazananı kaybedenden ayıran giriş özellikleri. Yalnız walk-forward OOS AUC CI > 0,5 ise raporlanır.
- **Gerçek ve CF kanıtı ayrı tutulur.** CF, `cf_aux_v1` `r_net_conservative` kullanır; ağırlığı gösterilir, sessizce
  birleştirilmez.
- **Uygunluk:** n ≥ 30 ve ≥ 10 farklı gün.
- **Çoklu test:** BH-FDR aile tanımı (hangi hücreler × varyantlar tek bakışta test edilir) mühürlü kayıtta **önceden**
  sabitlenir; q = 0,10. Kümülatif deneme sayısı `trials.jsonl`'dan raporlanır.
- **İsteğe bağlı durma kontrolü.** Dersler her gece durum **değiştirmez**. Gece değerleri "ara görünüm" olarak gösterilir.
  - Durum değişikliği yalnız **önceden kayıtlı bakışlarda** olur: hücrenin n'i 30, 60, 120 ve 240'ı geçtiğinde.
  - Alfa bakışlara bölünür: 0,05 toplam = 0,01 / 0,01 / 0,015 / 0,015.
- **İleri doğrulama.** Bir bulgu T_k anında hash'lenip `RESEARCH_HYPOTHESIS` olarak kaydedilir. Doğrulama **yalnız T_k'dan
  sonra kapanan** işlemlerle yapılır. Gereken: aynı işaret, doğrulama bakışında CI alt sınırı > 0, ve ≥ 3 aylık katmanın
  ≥ 2/3'ünde işaret tutarlılığı.

### 5.8 Ders deposu ve yaşam döngüsü

- `learn/lesson_store` kullanılır (`lesson_v2` şeması, `build_lesson`, `transition()`, `SegmentArchive`). Bunlar bu
  fonksiyonların ilk gerçek çağıranlarıdır. Kök ayrıdır (`data/research/lessons`); `state/lesson_archive`'a dokunulmaz.
- Bağlam anahtarları genişletilir: `B|` defter, `I|` enstrüman, `A|` sınıf (COIN/GOLD), `T|` taktik, `V|` varyasyon,
  `C|` atıf kodu, `X|` durum kovası.
- Ders alanları:
  - `lesson_id`, `scope{}`, `observation_tr` (şablondan üretilir, LLM yok), `mechanism_code`;
  - `evidence{n, n_days, mean_r, ci95, effect_delta_r, q_bh, look_no, discovery_window, validation_window, data_seal,
    registry_sha}`;
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
- alfa;
- durdurma kuralı.

Her spec yeni bir kütüphane sürümü ve yeni bir `trial_id`'dir. Aday, değişmemiş orijinalin yanında kayıt-yalnız ileri
test edilir (§6.6). Hiçbir şey otomatik uygulanmaz.

### 5.10 Akıl sağlığı testleri (mevcut kanıtın yeniden üretilmesi)

- Box: stop < %0,5 için 72 işlem −0,56R; %0,5–1 için 176 işlem +0,17R. Beklenen ders: "Box: stop < %0,5 kaybettiriyor
  (STOP_TOO_TIGHT / TIGHT_STOP_COST_MULTIPLIER)". Beklenen aday: "Box en az stop %0,5".
- M2 kapasite ekstraları +0,15R.
- Seçicilik ekstraları negatif; bu, mevcut `record_selectivity` ayarını doğrular.

---

## 6. Sürekli araştırma döngüsü

### 6.1 Gece aşamaları (`engine-night`, 01:37–03:40 UTC, ağsız, AI yok)

| Aşama | İş | İlk aşama |
|---|---|---|
| S0 ön kontrol | PAPER doğrulaması; motor kilidi; disk; `data_status` tazeliği (eskiyse yalnız S1–S3 ve S7 çalışır, diğerleri `DATA_STALE`); canlı config sha'sı | P1 |
| S1a kapanış arşivi | ledger'lardan yeni ve revize kapanışlar; uzlaştırma | P1 |
| S1b günlük + yol + rehydrate + fidelity | `tj_v1` | P2 |
| S2 atıf | kodlar + `cfgrid_v1` + ayrıştırma | P2 |
| S3 günlük hedef | dünün kesin satırları; kayan hükümler | P1 (gerçekleşmiş), P2 (MTM) |
| S4 walk-forward | tüm varyasyonlar için artımlı son katman + 7 parçadan birinin tam yeniden hesabı (kütüphane haftada bir tam taranır) | P3 |
| S5 ileri adaylar | her aktif aday yeni kapanmış barlarda adım atar | P4 |
| S6 dersler, denemeler, kapı | önceden kayıtlı bakışlar, BH, durum geçişleri, öneriler | P3/P4 |
| S7 özet | `digest_tr.md`, `engine_summary.json`, `run_status.json` (aşama süreleri, CPU, tepe bellek) | P1 |

Her aşama son tarihe bakar ve iş kuyruğuna kontrol noktası yazar; biten iş kalırsa ertesi gece devam eder.
Shared-experience `--summary-out` raporları motor biriminden **çalıştırılmaz**, çünkü onlar `state/` altına yazar. İstenirse
çıktı yolu `research/summary`'ye yönlendirilerek ayrı bir değişiklikle ele alınır.

### 6.2 Strateji kütüphanesi (`LIB_v1`, mühürlü)

Her taktik saf bir fonksiyondur: `signals(bars, funding, metrics, ctx) → {signal_ts, side, entry, stop, targets,
exit_rules, max_hold}`. Parametre ızgarası sınırlıdır (sürüm başına aile başına ≤ 8–12 varyant).

| Kaynak | İçerik |
|---|---|
| Mevcut defterler (birebir kopya) | T2 EMA200 trend, M2 TSMOM28 (14/28/56), Box fade (stop genişliği kovaları), D4 Donchian 20/10 (+55/20, ATR çıkışları), C4/C4S CV001–CV008, Formasyon aileleri (belleğe sığarsa; yoksa yalnız ileri). Ana bot topluluğu ucuza yeniden oynatılamaz; yalnız canlı ölçülür. |
| Laboratuvarlar | `signal_lab` kuralları, `futures_lab` fut_v1, `crowd_lab` fut_v2, `gold_v1` hücreleri, `gold_v2` (günlük/4h trend, PAXG hafta sonu dönüşü), `book_lab` D4/C4/Formasyon varyantları. Yalnız yayımlanmış kayıtları alınır; mühürlü bir laboratuvar aynı mühürle yeniden **çalıştırılmaz**. |
| Klasik aileler | çok ufuklu TSMOM (7/14/28/56/84 g, oynaklık hedefli); Donchian 20/55; EMA/SMA kesişimi; Keltner/ATR kırılımı; BB-genişliği sıkışma kırılımı; RSI2/Bollinger aralık dönüşü (yalnız düşük ER20 rejiminde); aşırı fonlama fade/carry; OI–fiyat uyumsuzluğu; kesitsel momentum (40 coin, 7/28 g sıralama, üst-k long / alt-k short, haftalık, BTC-beta nötr: "toplam" hedefi için portföy taktiği); kesitsel 1 günlük dönüş; BTC–ETH spread z-skoru |
| Altına özgü | XAUUSDT Londra/NY açılış kırılımı, Asya kayması; PAXG pazar açılış boşluğu, pazartesi dönüşü; ay sonu; altın günlük trend; altın/BTC oran trendi |
| Örtüler (bir seferde bir faktör) | ATR iz süren, chandelier, zaman stopu, TP merdiveni; büyüklük: sabit %0,5 risk (`learning_mode.fit_size`), oynaklık hedefi; kaldıraç büyüklükten türetilir, likidasyon mesafesi ≥ 2 × stop |
| Dersten türeyen | §5.9 |
| Yeni fikirler | AI veya sahip, `library/` altına kayıt girdisi ekleyen bir **kod PR'ı** açar; mühür ve deneme sayımı otomatik artar |

**Kopya pariteleri.** Bir defter kopyası lider tablosuna veya atıf tabanına ancak şu koşulla girer: canlı dönemi
yeniden oynatınca canlı defterin girişlerinin ≥ %95'ini (aynı bar, aynı yön) üretmeli ve eşleşen işlemlerde medyan
|ΔR| ≤ 0,05 olmalı. Kopyalar `strategy_paper.apply_action` ile oynatılır; yürütme canlı defterlerle aynıdır.

### 6.3 Walk-forward protokolü (`PROMOTION_REGISTRY`'de mühürlü)

| Konu | Kural |
|---|---|
| Katmanlar | `quant/walkforward.make_folds`, `validation_days > 0` ile üç yollu düzen; çapalı ve kayan. ≥ 1h taktikler: eğitim ≥ 365 g, test 90 g. 5m/15m taktikler: eğitim 120 g, doğrulama 30 g, test 14 g. Altın vadelileri: eğitim ≥ 180 g; uzun vekil olarak PAXG spot 2020+ ve Dukascopy (gold_v2 ön kaydına uygun: 2006–2020 keşif, 2020→ doğrulama) |
| Arındırma | purge/embargo = sinyal tf'sinin 1 barı + `max_hold` |
| Kilitli bekletme | son 90 gün (`holdout_days`), yalnız Kapı A incelemesinde **bir kez** açılır; okuma koruması enstrümanlıdır |
| Parametre seçimi | yalnız eğitimde, ADVERSE maliyette en yüksek ortalama net R'a göre; sonra doğrulama ve test için dondurulur |
| Maliyet | base = taker %0,05 × 2 + 3 bps kayma + gerçek fonlama; adverse = 2× kayma + 1 tick spread; stress = 3× kayma + %0,02 ek ücret |
| Sızıntı | `leakage_check` geçmezse parça atılır (`LEAK`) |
| Önbellek | sonuçlar (spec_sha, data_seal_month) ile anahtarlanır; her gece yalnız yeni katman hesaplanır |
| Kaynak | `--jobs 1` (nproc ≥ 4 ise 2); bir seferde tek enstrüman × tf; < 1 GB |

### 6.4 Çoklu test ve deneme defteri

- **`trials.jsonl`** yalnız eklenir, asla silinmez. Laboratuvarlar ve motor tarafından değerlendirilen her
  spec × varyant × enstrüman × katman için bir satır tutulur: `registry_sha`, `data_seal`, `code_sha`, parametreler,
  pencere, n, ortalama R, CI, hüküm, `cumulative_N`.
- **Geçmiş laboratuvar sonuçları** öncül deneme olarak içe alınır: `gold_v1` 0/32, crowd `fut_v2` 0/8, `book_v1`,
  `gold_v2`. Böylece N'e sayılırlar ve körlemesine yeniden çalıştırılmazlar.
- **Deflated Sharpe:** mevcut `tradingbot/validation.py` `deflated_sharpe` (satır 252) ve `probabilistic_sharpe`
  **yeniden kullanılır**, N `trials.jsonl`'daki aile sayısıdır.
- **PBO henüz yok.** `quant/walkforward.fold_report` her zaman `pbo=None` döndürür (`pbo_state =
  "requires_candidate_matrix"` / `"not_computable"`). Varyant × katman matrisi üzerinde CSCV (16 blok) **yeni yazılır**
  ve önce cevabı bilinen sentetik veride test edilir. O zamana kadar PBO "hesaplanamaz" sayılır ve PBO gerektiren her
  kapı **kapalı kalır** (fail-closed).
- Ek denetimler:
  - BH-FDR (q = 0,10), aile kayıtta sabit;
  - plasebo: zaman kaydırılmış veya işaret çevrilmiş sinyaller, aynı tutma süresiyle, geçmemeli;
  - haftalık White/Hansen SPA, "hiçbir şey yapma" ve "BTC/XAU al-tut" ölçütlerine karşı (P3 sonu, isteğe bağlı).

### 6.5 Lider tablosu

Ham getiriyle değil DSR ile sıralanır. Her varyant × enstrüman ve varyant portföyü için şunlar gösterilir:
- OOS n;
- ortalama net R ve gün kümelenmeli CI;
- katmanların pozitif payı;
- PBO (veya "hesaplanamaz");
- plasebo farkı;
- maliyet stresi sonucu;
- en büyük düşüş;
- günlük ortalama % (%0,5 riskte);
- P(gün ≥ +%1);
- k* ve iflas olasılığı (§7.6);
- mevcut defterlerle korelasyon.

Hayatta kalma yanlılığı uyarısı ("hayatta kalan evren") her satırda durur. Gün gün evren anlık görüntüleri biriktikçe
bu uyarı daralır.

### 6.6 Kayıt-yalnız ileri adaylar (P4)

- Kapı A'yı geçen her varyant veya dersten türeyen aday, kendi atılabilir kâğıt defterini alır:
  `research/prospective/<id>/futures_ledger.json`. Defter worker'la aynı ücret/kayma/fonlama/likidasyon modelini
  kullanır.
- Kurallar, adım atılacak barlar oluşmadan **önce** mühürlenir. Her gece, son çalıştırmadan beri kapanmış barlarda adım
  atılır; kararlar yalnız `close_time < adım zamanı` olan barları kullanır, dolum sonraki barın açılışında olur. Bu
  gerçek bir örneklem dışı testtir ve 1 günlük gecikmesi kaydedilir.
- En fazla 20–40 aktif aday olur; gecede < 5 dk sürer.
- **Altın ilk kez burada işlem görür.** XAUUSDT/PAXGUSDT adayları, altının ilk kâğıt kaydını verir.
- Aday sonuçları canlı defter toplamına **asla** eklenmez. Panel ve özet onları "kayıt-yalnız aday — gerçek defter
  değil" etiketiyle gösterir.
- Önceden kayıtlı durdurma (yalnız sabit bakışlarda): ≥ 30 işlemden sonra ortalama R < −0,10; kayan 60 günlük ortalama
  R CI üst sınırı < 0; düşüş > 2 × geriye-test p95 düşüşü; günlük net R üzerinde CUSUM alarmı (h = 4σ).
- **İsteğe bağlı saatlik adım (sonraki aşama).** Gece yolu VPS'te kararlı hale gelince, 5m taktiklerin gecikmesini
  azaltmak için ayrı bir saatlik birim değerlendirilir. Bu birim ağsız olur ve gece birimiyle kilidi paylaşır (§2.4).
  Bellek toplamı P0 kuralına göre yeniden hesaplanır.

### 6.7 Terfi kapısı (`PROMOTION_REGISTRY`, ilk veri çalıştırmasından **önce** sha testte ve bu belgede sabitlenir)

**Kapı A — çevrimdışı.** Hepsi gerekli:
- ADVERSE maliyette OOS net ortalama R > 0 ve gün kümelenmeli CI95 alt sınırı > 0;
- ≥ 100 OOS işlem ve ≥ 2 takvim yılı (altın vadelileri: ≥ 60 işlem ve ≥ 9 ay, `KISA_GEÇMİŞ` işaretli);
- katmanların ≥ %60'ı pozitif;
- PBO < 0,25 (hesaplanamazsa geçmez);
- DSR olasılığı ≥ 0,95;
- BH anlamlı;
- plasebo farkı CI alt sınırı > 0;
- kilitli bekletmede ortalama R ≥ 0;
- tek bir enstrüman kârın en fazla %50'si olabilir (tasarım gereği tek enstrümanlı taktikler hariç; altında XAU ve PAXG
  arasında tutarlılık aranır).

Geçerse bir kayıt-yalnız ileri aday doğar. Bu otomatiktir, çünkü yalnız motorun kendi defterine yazar.

**Kapı B — ileri, kayıt-yalnız.** Hepsi gerekli:
- ≥ 28 gün **ve** ≥ 50 kapanmış işlem (yavaş taktikler: ≥ 90 gün ve ≥ 30 işlem; altın: ≥ 60 gün ve ≥ 30 işlem);
- ortalama net R > 0 ve Kapı A OOS CI'ının içinde (sapma denetimi);
- son bakışta CI alt sınırı > −0,05R (n ≥ 100 ise > 0);
- düşüş ≤ 1,5 × geriye-test p95 düşüşü ve ≤ aday özsermayesinin %8'i;
- likidasyon yok;
- gerçekleşen maliyet ≤ 1,3 × modellenen maliyet;
- fidelity ≥ %90;
- `DATA_STALE` günleri ≤ %10.

**Kapı C — öneri.**
- Öneriler ayda bir topluca hazırlanır: her ayın ilk UTC pazartesisi, o ayın Kapı B geçenlerine Holm (α = 0,05)
  uygulanır.
- Her öneri için `proposals/<id>.json` ve `.tr.md` yazılır. İçerik: kanıt paketi, kilitli bekletme sonucu, tam
  kod/config farkı, işlem başı risk (başlangıç %0,25), kaldıraç tavanı, beklenen günlük % katkısı ve CI'ı, k* tablosu,
  önceden kararlaştırılmış durdurma kuralı, geri alma adımları.
- Boş bir ay için dürüstçe "öneri yok" yazılır.
- **Sahip karar verir.** Onay `engine-query --approve <id> --operator berke` ile kaydedilir. Onay tek başına canlıda
  hiçbir şeyi değiştirmez; ayrı bir normal sürüm hazırlanır.
- Tercih edilen biçim **yeni bir kâğıt defterdir** (`strategy_paper_<id>`). Mevcut bir defterin değişmesi ayrı ve açık bir
  onay gerektirir.

**Sürüm sonrası izleme.** Motor canlı defteri Kapı B dağılımına karşı puanlamaya devam eder ve gerekirse "SAPMA"
uyarısı verir. Otomatik geri alma yoktur; sahip karar verir. Mevcut defterler için de aynı kurallarla yalnız
**düşürme önerisi** yazılır.

---

## 7. Günlük ve aylık hedef ölçümü

### 7.1 Tanımlar (`tgt_v1`, mühürlü; gün = 00:00–24:00 UTC)

Üç akış vardır ve **asla toplanmaz**:
- `LIVE_PAPER`: 8 mevcut defter;
- `PROSPECTIVE_PAPER`: motor adayları;
- `BACKTEST`: walk-forward OOS.

| Büyüklük | Tanım |
|---|---|
| Gerçekleşmiş gün P&L | `pnl_real(kapsam, D)` = D içinde kapanan işlemlerin Σ `TradeRecord.pnl`'i (giriş/çıkış ücreti, modellenmiş kayma ve net fonlama dahil). Kesindir. |
| MTM gün P&L (P2) | `pnl_mtm = pnl_real + ΔU`. U = 00:00 UTC'de açık pozisyonların gerçekleşmemiş kârı. Açık pozisyonlar `fills[]`'tan kalan miktarla kurulur (`opened_at ≤ t < closed_at`). Fiyat, store'un 00:00'daki 1m kapanışıdır; tarihsel mark fiyatı saklanmadığı için `LAST_PRICE_PROXY` etiketlidir. |
| Gün başı özsermaye | `E_book(D)` **geriye doğru** kurulur: bugünkü cüzdan bakiyesinden D'den sonra gerçekleşen P&L ve cüzdan hareketleri çıkarılır (kapanış arşivi ve ledger kayıtları), üstüne 00:00'daki MTM eklenir. `starting_equity + geçmiş` ile ileri doğru kurmak rotasyonla kaybolan kayıtlar yüzünden yanlış olur. Açık pozisyon fonlamasının cüzdana yazılma anı bir fixture testiyle doğrulanır (§12). |
| Toplam | `r_total(D) = Σ pnl / Σ E_book` (LIVE_PAPER) |
| Defter | `r_book(D) = pnl_book / E_book` |
| Enstrüman (katkı) | `r_inst(D) = Σ_defterler pnl(enstrüman) / E_total` (her coin, XAUUSDT, PAXGUSDT; altın toplamı ayrıca) |
| Defter × enstrüman | `r_book_inst(D) = pnl / E_book` ("defter bazında" ikincil satır) |
| Marj üzerinden getiri | yalnız dipnot: "kaldıraçlı, hedef değil" |

Her satır şunları da taşır: işlem sayısı, ücret/kayma/fonlama toplamları, Σ net R, 00:00'da açık pozisyonlar,
`data_completeness`. Verisi eksik gün **EKSİK** işaretlenir ve hükümlerden çıkarılır; sıfır sayılmaz.

**Altın.** Bugün hiçbir canlı defter altın işlemiyor. `LIVE_PAPER`'da altın satırı "işlem yok" yazar. Altın yalnız
ayrı `PROSPECTIVE_PAPER` akışında görünür, ta ki sahip bir altın defterini onaylayana kadar.

### 7.2 Gün isabeti

- `TOTAL_HIT(D) := r_total(D) ≥ %1,00`.
- `SINGLE_HIT(D) := max_i r_inst(D) ≥ %1,00` (hangi enstrüman olduğu yazılır).
- `DAY_HIT(D) := TOTAL_HIT veya SINGLE_HIT` (sahibin "veya"sı açık hale getirilmiştir).
- **Seçim yanlılığı koruması.** ~42 enstrümanın maksimumu şansla şişer. Her gün o günün işlem sonuçlarının işaretleri
  rastgele çevrilerek (1000 çekim) "bir enstrümanın şansla +%1'e ulaşma olasılığı" hesaplanır ve gözlenen isabet oranının
  yanında **beklenen şans oranı** olarak gösterilir.

### 7.3 Hüküm kuralları (mühürlü, Türkçe, aynen gösterilir)

| Hüküm | Koşul |
|---|---|
| `HEDEF GÜNÜ` | tek bir günde `DAY_HIT`. Sayılır, **asla başarı denmez**. |
| `HEDEF KANITLANDI (PAPER)` | kapsam için **hepsi**: kayan 60 günlük ortalama günlük %'nin blok-bootstrap (5 günlük bloklar) **CI95 alt sınırı ≥ %1,00**; 60 günde ≥ 40 tam gün; ≥ 30 kapanmış işlem; tek bir gün pencere toplamının en fazla %25'i; ayrıca yalnız-gerçekleşmiş hesapla da aynı koşul sağlanmalı. |
| `HEDEF YOLUNDA (umut verici)` | nokta ortalaması ≥ %1,00, ama CI alt sınırı < %1,00 |
| `HEDEFİN ALTINDA` | diğer durumlar (açık fark ile) |
| `VERİ YETERSİZ` | asgari koşullar sağlanmıyor |

- **Tek enstrüman yolu.** Bir enstrüman ancak şu iki yoldan biriyle `KANITLANDI` olabilir:
  1. **önceden seçilmişse**: sahip enstrümanı kayıtta önceden bildirir (§12);
  2. veya tüm enstrümanlar arasında Holm düzeltmesinden sonra kendi 60 günlük CI alt sınırı ≥ %1,00 ise.
  Geriye dönük "en iyi enstrüman" yalnız Holm düzeltmeli gösterilir.
- `PROSPECTIVE_PAPER` ve `BACKTEST` aynı tabloyu büyük bir "canlı değil" etiketiyle alır; **asla** `KANITLANDI`
  üretemez.
- Bu kuralların hepsi bir özellik testiyle (property test) korunur: 40 tam günden az veya CI alt sınırı < %1 iken
  `KANITLANDI` çıkamaz.

### 7.4 Kayan istatistikler (7/30/60/90 gün)

- isabet günü sayısı ve oranı (Wilson CI) ile beklenen şans oranı;
- aritmetik ve geometrik (bileşik) ortalama;
- medyan;
- blok-bootstrap CI95;
- en kötü gün ve en büyük düşüş (isabet oranının hemen yanında, böylece −%5 günle alınan +%1 gün görünür);
- gerekli ile gerçekleşen karşılaştırması: %1/gün ≈ 30 günde ×1,348, yılda ×37,8; %0,5 riskte defter başına günde +2R.

### 7.5 Aylık hedef

- Mevcut `bot_scorecard.py` "AYLIK HEDEF" bloğu korunur: ayda en az +%1 net, her defter kendi kâğıt bakiyesinde
  (`MONTHLY_TARGET_PCT = 1.0`).
- Motor aynı aylık hesabı enstrüman ve toplam için de raporlar.
- Günlük hedef aylık hedefin ~30 katıdır. Bu oran raporda açıkça yazılır.

### 7.6 Kaldıraç gerçekliği (HİPOTETİK)

Her lider tablosu varyantı ve kanıtlı her kapsam için şunlar hesaplanır:
- `k* = %1 ÷ ortalama günlük %`: +%1/gün ortalaması için gereken risk katı;
- k* altında benzetilen en büyük düşüş;
- bir yıl içinde %50 düşüş olasılığı (günlük getirilerin blok bootstrap'i).

Bu satır "HİPOTETİK — gerçekleşmedi" etiketiyle gösterilir. "Kaldıracı artır" cevabının iflas maliyetini görünür kılar.
Motor kaldıraç değişikliğini **asla** önermez.

### 7.7 Nerede görünür

| Yer | Ne zaman | İçerik |
|---|---|---|
| `scripts/bot_scorecard.py --daily [--days N] [--out]` | **P1** | yalnız-gerçekleşmiş, salt-okunur, doğrudan ledger'lardan bağımsız hesap (sahip motor olmadan doğrulayabilsin diye); "GÜNLÜK HEDEF" bloğu "AYLIK HEDEF"in yanında; eski D4 etiketi ("Trend 4h …") "D4 Donchian 4h" olarak düzeltilir. Engine-app checkout'undan çalışır, app sürümü gerekmez. |
| `engine-status --brief` | **P1** | ≤ 60 satır: veri tazeliği, son çalıştırma, günlük hedef tablosu |
| Sürüm betiği `--check` › "GECE ÖĞRENME MOTORU" › "GÜNLÜK HEDEF" | **P1** | son 7 gün toplam + defter, en iyi enstrüman ve şans oranı, 30/60 g hükümleri |
| `digest_tr.md` başlık satırı | P1 | "Dün: toplam +x,xx% (hedef %1: TUTMADI/TUTTU); en iyi enstrüman Y +z,zz% (şans oranı …); 60g ort. … [CI …] → HEDEFİN ALTINDA" |
| Panel `/hedef` ("Günlük hedef ve öğrenme") | **bir sonraki normal worker/dashboard sürümüyle** | tablo + 90 günlük çubuk grafik (r_total ve en iyi enstrüman, %1 çizgisine karşı), altın satırı, ayrı aday bölümü, rapor yaşı, "> 24s ESKİ RAPOR", şerit "tanımlayıcı; karar yok; kanıt değil; PAPER" |

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

Bir kabul testi `daily_target` ile `scorecard --daily`'nin aynı sayıyı verdiğini denetler.

---

## 8. Token planı

**Hedef:** rutin öğrenme 0 AI token ve 0 tekrarlı indirme.

1. **Veri kalıcı.** Tüm veri VPS'te kalır (`store`, `archive_cache`, `dukascopy`). İlk doldurmadan sonra yalnız günlük
   fark iner. Rutin iş için GitHub Actions laboratuvar çalıştırmaları ve scratchpad önbellekleri bırakılır.
2. **Deterministik gece işi.** Gece işinin tamamı deterministik Python'dur. Botun içindeki LLM noop /
   `POSTMORTEM_ONLY` olarak kalır; motor hiç LLM çağırmaz. Türkçe metinler şablondan üretilir.
3. **AI yalnız şunları okur** (sahip yapıştırır):
   - `engine-status --brief` (≤ 60 satır) veya `summary/digest_tr.md` (≤ 8 KB, ≈ 2,5k token);
   - belirli bir soru için `engine-query <konu>` (≤ 4 KB / ≤ 150 satır): `lessons --scope`, `trials --like <fikir>`,
     `challenger <id>`, `proposal <id>`, `day <tarih>`, `trade <trade_key>`, `why-lost <defter>`;
   - bir önerinin `PROPOSAL.tr.md`'si (≤ 10 KB).
   AI ham günlüğü, yolları, geriye-test ayrıntısını veya store'u **asla** okumaz.
4. **Önce deneme defteri.** Bir AI oturumu yeni bir şey önermeden önce `engine-query trials --like <fikir>` çalıştırır;
   aynı mühürle zaten test edilmiş fikri yeniden önermez. Bu kural `CLAUDE.md`/belgelere yazılır.
5. **AI'nın görevleri yalnız şunlardır:**
   - haftalık özet ve öneri incelemesi (oturum başına ≈ 10–40k token);
   - yeni kütüphane girdisi veya atıf kodu yazan küçük kod PR'ları (≈ 50–150k token / fikir);
   - Türkçe öneri belgeleri;
   - `run_status.json`'dan arıza teşhisi.
6. **Oturum kuralları.** Claude oturumları piyasa verisi **indirmez**; testler `tests/fixtures` altındaki küçük
   sentetik verilerle çalışır. Gerçek veri gerektiren laboratuvarlar VPS'te sahibin komutuyla
   (`--cache /opt/tradingbot/data/research/archive_cache --offline`) veya kayıt girdisi eklendikten sonra gece
   çalıştırıcısıyla koşar.
7. **Boyut sınırları.** Özet ve sorgu çıktı boyut sınırları testlerle zorlanır.

**Beklenen tasarruf:** oturum başına O(100k–1M token + indirme süresi) yerine haftada O(5k token) okuma, artı gerçek
yeni fikir oturumları.

---

## 9. Güvenlik ve onaylar

### 9.1 Ne kayıt-yalnız, ne onay ister

| Öğe | Durum | Onay |
|---|---|---|
| Veri deposu, günlük, atıf, dersler (`APPLIED_BOUNDED` hariç), lider tablosu, günlük hedef | kayıt-yalnız; hiçbir karar yolu okumaz (AST testi) | sürüm onayı (her sürüm gibi) |
| İleri adaylar (motorun kendi kâğıt defterleri) | kayıt-yalnız; canlı toplamlara eklenmez | Kapı A'dan otomatik doğar; `PROMOTION_REGISTRY` metni sahibin onayıyla mühürlenir (P4 öncesi) |
| Öneri dosyaları | yalnız metin + kanıt | — |
| Yeni kâğıt defter (onaylı öneriden) | canlı PAPER | **sahibin açık onayı** + normal sürüm |
| Mevcut bir defterin herhangi bir kararı | — | **ayrı, açık sahip onayı**; motor bunu asla otomatik yapmaz |
| Worker'a kayıt-yalnız alan ekleme (P6) | karar değişmez | **sahibin açık onayı** (worker kodu değişir, yeniden başlatma gerekir) |
| Telegram "günlük rapor hazır" | kapalı | config değişikliği → onay |
| `research_enabled=True` (ResearchCoordinator) | **mevcut sistem**: `engine_v3:1915` ana bot girişlerinde filtre/küçültme politikasını otomatik etkinleştirebilir (`PAPER_RESEARCH_ACTIVE`) | **P0'da sahibin açık kararı**: önceden onaylı mı, yoksa ayrı onaylı bir değişiklikle kapatılsın mı. Motor buna dokunmaz. Bu karar verilmeden "hiçbir şey otomatik uygulanmaz" iddiası tüm sistem için doğru değildir. |

### 9.2 Sert kurallar (testlerle)

| Kural | Nasıl korunur |
|---|---|
| Yalnız PAPER | S0 doğrular; `ALLOW_LIVE_TRADING=false` |
| Sır yok | `EnvironmentFile` yok; özetlerde sır yok |
| State'e yazma yok | çekirdek (`ReadOnlyPaths`) + açma modu denetimi |
| Worker deposuna yazma yok | ayrı `ResearchStore` kökü |
| Config değişmez | sürüm değişmezi: `config.yaml` baytları değişmemiş olmalı |
| Ağ yalnız data biriminde | `PrivateNetwork=yes` gece biriminde; birim sözleşme testi |
| Zaman pencereleri | `OnCalendar` UTC ve pencere dışı; birim sözleşme testi |
| Mühürler | `attribution_v1`, `cfgrid_v1`, `LIB_v1`, `PROMOTION_REGISTRY`, `tgt_v1` sha'ları testte ve bu belgede sabit; sha değişirse CI kırılır |

### 9.3 Birim kurulum güvenliği (daemon-reload)

- Depodaki worker birimi `MemoryMax=4G` der; VPS'te 6G override vardır. `daemon-reload` (`enable`'ın örtük reload'u
  dahil) bu kaymayı uygulayabilir.
- Kurulum adımı bu yüzden kapılıdır:
  1. worker ve dashboard için `NeedDaemonReload=no` ve worker `MemoryMax=6G` doğrulanır;
  2. `install -m0644` ile birimler kurulur;
  3. `daemon-reload` yapılır;
  4. `MemoryMax=6G` **yeniden** doğrulanır (değilse betik hemen geri alır ve durur);
  5. `enable --now` zamanlayıcılar;
  6. `is-enabled` ve `is-active` doğrulanır.
- `setup_vps_v3.sh` asla yeniden çalıştırılmaz.
- P1'de iki birim **bir kez** kurulur. Sonraki aşamalar yalnız engine-app sabitini (pin) taşır; birim dosyası
  değişmezse yeniden reload gerekmez.

### 9.4 Kapatma düğmeleri

- Anında durdurma: `sudo systemctl disable --now tradingbot-engine-data.timer tradingbot-engine-night.timer`.
  Veri yerinde kalır.
- Tam geri alma: zamanlayıcıları kapat, birim dosyalarını sil, (kapılı) `daemon-reload`, `/opt/tradingbot/engine-app`'i
  kaldır.
- Disk koruması: 20 GB üstünde motor kendiliğinden çalışmayı reddeder.
- Tek bir adayı durdurmak: `engine-query --retire <id> --operator berke`.

---

## 10. Aşamalı teslim

Her aşama tek bir dry-run, tek bir deploy ve günlük `--check` ile yürütülür. Sahip her VPS komutunu kendisi çalıştırır.

### P0 — VPS bilgileri ve taban çizgisi (salt-okunur; kod yok)

**Teslimatlar:** aşağıdaki salt-okunur kontrol listesi ve sahip çıktıları yapıştırdıktan sonra kaydedilen
değerler.

```
nproc; free -m; df -h /opt; timedatectl | grep -i zone
systemctl show tradingbot-worker -p MemoryMax,MemoryPeak,CPUQuotaPerSecUSec,NeedDaemonReload,NRestarts
systemctl show tradingbot-dashboard -p MemoryPeak,NeedDaemonReload
cat /sys/fs/cgroup/system.slice/tradingbot-worker.service/memory.peak
systemctl cat tradingbot-alert@.service >/dev/null 2>&1 && echo ALERT_VAR || echo ALERT_YOK
sudo du -sh /opt/tradingbot/data/market/history
sudo find /opt/tradingbot/data/market/history -name manifest.json | awk -F/ '{print $(NF-3),$(NF-1)}' | sort | uniq -c | head -60
sudo -u tradingbot -H bash -c 'cd /opt/tradingbot/app && /opt/tradingbot/venv/bin/python -m tradingbot research-status; /opt/tradingbot/venv/bin/python -m tradingbot model-status; /opt/tradingbot/venv/bin/python -m tradingbot mode-status'
curl -sI https://data.binance.vision/data/futures/um/monthly/klines/XAUUSDT/1h/XAUUSDT-1h-2026-08.zip | head -1
curl -sI https://datafeed.dukascopy.com/datafeed/XAUUSD/2024/00/02/BID_candles_min_1.bi5 | head -1
sudo journalctl -u tradingbot-worker --since "-24h" | grep -i "tur" | tail -80   # tur süreleri, p50/p95 taban çizgisi
```

**Kabul:**
- değerler kaydedildi;
- birim belleği formülle hesaplandı (§2.5);
- Dukascopy yolu seçildi (doğrudan VPS veya bir kerelik tarball);
- `alert@` var/yok bilgisi kaydedildi;
- tur p50/p95 taban çizgisi kaydedildi;
- **ResearchCoordinator kararı yazılı olarak verildi.**

**Sahip:** komutları çalıştırır, çıktıyı yapıştırır, sabit sınırları (bellek, pencereler, 20 GB) onaylar.

### P1 — Veri temeli + gerçekleşmiş günlük hedef + kapanış arşivi (YAKINDA DAĞITILABİLİR, KARAR-NÖTR)

**Kapsam (tam):**

| Dosya | İçerik |
|---|---|
| `tradingbot/research_engine/__init__.py`, `paths.py`, `lock.py` | kök düzeni, disk koruması, motor-geneli fcntl kilidi (`SKIPPED_LOCKED`) |
| `research_engine/store.py` | `ResearchStore(HistoryStore)`: ay-parçası checksum'ı + gecelik tam checksum, seri kilidi, `metrics_5m` / `duka_1h` / `duka_1m` türleri, yalnız kapanmış bar, µs→ms |
| `research_engine/universe.py` | U_R (40 giriş evreni ∪ BTC/ETH ∪ son 180 günde işlem görenler ∪ XAUUSDT, PAXGUSDT vadeli, PAXGUSDT spot); günlük evren + `exchangeInfo` anlık görüntüsü |
| `research_engine/seed.py` | worker deposunu salt-okunur kopyala + doğrula |
| `research_engine/datastore.py` | arşiv-önce ekleme, seri başına 3 deneme + yakalama (`STALE`), tek `BudgetPool` güvenlik 0,1, `X-MBX-USED-WEIGHT-1M` takibi (%50'de bekle), 418/429'da REST'i durdur, REST kuyruğu `source=rest`, ertesi gün arşiv uzlaştırma + fark kaydı, `data_status.json` + `data_seal`, ilk doldurmada 4h pencerelerinde kendini duraklatma |
| `research_engine/provider.py` | `StoreProvider` + fixture üzerinde parite testi (laboratuvarlar bu aşamada **geçirilmez**; `archive_cache --offline` kullanırlar) |
| `research_engine/closes.py` | ham kapanış arşivi: her defterin kapanmış `TradeRecord`'larının birebir kopyası, `rev`, son 7 günün yeniden eşitlenmesi; elde tutulan pencerede uzlaştırma (Σ pnl, ücret, fonlama, kayma, sayı) |
| `research_engine/daily_target.py` | **yalnız gerçekleşmiş** günlük hedef (defter, enstrüman, toplam; altın "işlem yok"); geriye doğru `E_book`; EKSİK günler; §7.3 hükümleri (bu aşamada MTM yok, bu yüzden hüküm "gerçekleşmiş" etiketli) |
| `research_engine/summary.py`, `night.py` (iskelet) | S0, S1a, S3, S7; `run_status.json` (aşama süreleri, CPU, tepe bellek, config sha, data_seal) |
| `tradingbot/cli_v3.py` | `engine-data {--backfill,--update,--status}`, `engine-night`, `engine-status [--brief]`; `--config` zorunlu açık yol |
| `scripts/bot_scorecard.py` | `--daily [--days N]` (bağımsız, salt-okunur) + D4 etiketi düzeltmesi |
| `deploy/tradingbot-engine-data.{service,timer}`, `deploy/tradingbot-engine-night.{service,timer}` | §2.2–2.5 ayarları; gece biriminde `PrivateNetwork=yes` |
| `deploy/releases/tb-engine-<sha7>.sh` | `tb-deploy-f8b05fb.sh` iskeleti: `--dry-run` / deploy / `--check`; worktree testleri; engine-app checkout'unu kur veya sabitle; kapılı birim kurulumu (§9.3); ilk doldurma komutunu yazdırır; adlandırılmış değişmezler |
| `docs/SYSTEM_LEARNING_ENGINE_V1.md` (bu belge) | "Veri" ve "P1 işletim" bölümleri güncellenir |

**P1'de olmayanlar:**
- worker / app / dashboard / config değişikliği;
- panel sayfası;
- günlük `tj_v1`, atıf, kütüphane, adaylar.

**Depo kabul testleri:**
1. Sahte sağlayıcıyla idempotent yeniden çalıştırma 0 satır ekler ve checksum'lar özdeş kalır.
2. Parça checksum'ı ile tam checksum tutarlıdır.
3. Yalnız kapanmış bar yazılır.
4. Bir serinin hatası diğerlerini durdurmaz.
5. 418/429 REST adımını durdurur.
6. Bütçe hiçbir zaman 0,1 payı aşmaz.
7. Arşiv uzlaştırma REST barlarını değiştirir ve farkı kaydeder.
8. Birim sözleşmesi:
   - `OnCalendar` `UTC` içerir ve hh%4==0 için hh:00–hh:35'te, ayrıca hh:00–hh:06'da başlamaz;
   - `Nice=19`, `CPUWeight=10`, `IOSchedulingClass=idle`, `OOMScoreAdjust=1000`;
   - `ReadWritePaths` yalnız `data/research`;
   - gece biriminde `PrivateNetwork=yes`;
   - `EnvironmentFile` yok;
   - `TRADINGBOT_DATA` açık;
   - `python -s -m`, `-I` yok;
   - `WorkingDirectory=/opt/tradingbot/engine-app`.
9. `systemd-analyze verify` ve `shellcheck` temiz; sahte `systemctl` ile sandbox kurulumu.
10. AST yalıtımı.
11. Sahte state üzerinde tam çalıştırma: yazmalar yalnız `data/research` altında; ledger'lar `"r"` modunda açılıyor.
12. Kapanış arşivi:
    - 5000 rotasyonunu atlatır (sentetik ledger);
    - geç fonlama `rev+1` üretir;
    - uzlaştırma 1e-6.
13. `daily_target` ile `scorecard --daily` aynı sayıyı verir.
14. `KANITLANDI` özellik testi.
15. Config varsayılan yolla yüklenmez.

**VPS kabul ölçütleri:**
1. İlk doldurma tamamlanır.
2. `data_status` her planlanan seriyi `last_ts ≤ 26 saat` ile gösterir. XAUUSDT, PAXGUSDT vadeli ve PAXGUSDT spot buna
   dahildir; Dukascopy ya hazırdır ya "yapılamadı" yazar.
3. Boşluklar dürüstçe sayılır.
4. İlk 3 gece arşiv uzlaştırma farkı 0'dır (veya açıklanmıştır).
5. Kapanış arşivi kayıt sayısı = her defterde ledger'ın elde tuttuğu history.
6. 7 gün boyunca:
   - worker tur p95 ≤ taban + %5;
   - `NRestarts` değişmez;
   - worker `MemoryMax` hâlâ 6G;
   - 418/429 yok;
   - motor `memory.peak` ≤ 0,8 × MemoryMax.
7. Disk kullanımı tahminin ±%30'u içindedir.
8. `scorecard --daily` ile `engine-status` aynı sayıyı verir.

**Sahibin VPS adımları:**

```
sudo bash tb-engine-<sha7>.sh --dry-run
sudo bash tb-engine-<sha7>.sh            # worker durmaz; birimler kapılı kurulur
# İlk doldurma (betik bu komutu yazdırır; 4h pencerelerinde kendini duraklatır; hata olursa aynı komutla devam):
sudo systemd-run --unit=tb-engine-backfill --uid=tradingbot --gid=tradingbot \
  -p WorkingDirectory=/opt/tradingbot/engine-app -p MemoryMax=1G -p Nice=19 -p CPUWeight=10 \
  -p IOSchedulingClass=idle -p OOMScoreAdjust=1000 \
  -p Environment=TRADINGBOT_DATA=/opt/tradingbot/data -p Environment=ALLOW_LIVE_TRADING=false -p Environment=TZ=UTC \
  /opt/tradingbot/venv/bin/python -s -m tradingbot engine-data --backfill --config /opt/tradingbot/app/config.yaml
sudo bash tb-engine-<sha7>.sh --check     # 7 gün boyunca her gün
sudo -u tradingbot /opt/tradingbot/venv/bin/python /opt/tradingbot/engine-app/scripts/bot_scorecard.py \
  --state /opt/tradingbot/data/state --daily --days 30
```

Dukascopy VPS'ten erişilemiyorsa: sahip `scripts/dukascopy_mirror.py`'yi bir kez kendi PC'sinde çalıştırır ve tarball'ı
`data/research/dukascopy` altına `scp` eder.

### P2 — İşlem günlüğü, yol, rehydrate, fidelity, atıf, MTM günlük hedef, özet

**Teslimatlar:**
- `journal.py` (`tj_v1`), `pathrec.py`, `rehydrate.py`, `fidelity.py`, `attribution.py` (`attribution_v1` mühürlü),
  `cfgrid.py` (`cfgrid_v1` mühürlü; EX_ANTE/HINDSIGHT; eşleşmiş rastgele kontrol; medyan referanslı ayrıştırma);
- MTM günlük hedef (`LAST_PRICE_PROXY`), şans oranı permütasyonu, k*/iflas tablosu;
- `digest_tr.md` tam sürüm;
- `engine-query` (`trade`, `why-lost`, `day`);
- gece S1b, S2;
- tembel 1m doldurma.

Birim dosyası değişmez; yalnız sabit taşınır.

**Kabul:**
1. Her defterin her `TradeRecord`'u günlükte tam bir kez bulunur; Σ net_pnl, ücret, fonlama ve kayma 1e-6 ile eşittir.
2. Sahibin her alanı ya doludur ya `MISSING`/`MODELED` etiketlidir.
3. Ücret özdeşliği ve çift sayım regresyon testi geçer.
4. Fidelity ≥ %90; başarısızlıklar nedenleriyle listelenir.
5. Rehydrate eşleşmesi Box/D4/T2/M2/C4'te ≥ %95.
6. Her kod ve ayrıştırma için sentetik yol altın testleri geçer (ayrıştırma ± artık = net_R).
7. Aynı mühür aynı bayt-özdeş çıktıyı verir.
8. İleriye bakma (lookahead) yoktur: `regime_fit` `as_of < opened_at`.
9. MTM `E_book` 00:00 test anlık görüntüsüyle %0,01 içinde eşleşir.
10. Box stop-genişliği bulgusu gerçek veriden yeniden üretilir.
11. Özet ≤ 8 KB, sorgular ≤ 150 satır.
12. 3 ardışık gece 03:40'tan önce `SUCCESS`; tur etkisi ≤ %5.

**Sahip:** dry-run, deploy (worker durmaz), günlük `--check`. İlk haftalık özeti isteğe bağlı olarak bir AI
incelemesine yapıştırır.

### P3 — Strateji kütüphanesi, walk-forward, denemeler, CSCV/PBO, dersler

**Teslimatlar:**
- `library/` (`LIB_v1` mühürlü; ön kayıt metni bu belgede ve testte, ilk VPS çalıştırmasından önce);
- `wf.py` (7 parçalı rotasyon, kilitli bekletme okuma koruması);
- `cscv.py` (PBO);
- `trials.py` (geçmiş laboratuvar sonuçları içe alınmış);
- `lessons.py` (ayrı kök, genişletilmiş anahtarlar, önceden kayıtlı bakışlar, alfa harcama);
- lider tablosu;
- `engine-query lessons|trials`.

**Kabul:**
1. Kopya paritesi: M2/T2/D4/C4 için ≥ %95 sinyal, medyan |ΔR| ≤ 0,05.
2. `leakage_check` geçer.
3. Dikilmiş avantajlı sentetik veri Kapı A'yı geçer; rastgele yürüyüş ve plasebo geçemez (200 boş denemede yanlış pozitif
   ≤ q).
4. CSCV cevabı bilinen veride doğrudur.
5. Bilinen laboratuvar sonuçları tolerans içinde yeniden üretilir (gold_v1 0/32, crowd fut_v2 0/8).
6. Deneme sayacı hiç azalmaz; mühür artırılmadan spec değişikliği CI'ı kırar.
7. Ders durumu yalnız kayıtlı bakışta değişir (özellik testi).
8. Tam kütüphane taraması 7 gecede, son tarih içinde biter.

**Sahip:** deploy; ilk Kapı A tablosunu okur (beklenen: çoğu spec elenir).

### P4 — Kayıt-yalnız ileri adaylar (kripto + altın) ve terfi kapısı

**Teslimatlar:**
- `prospective.py` (≤ 20–40 aday, nedensel adım, önceden kayıtlı durdurma, CUSUM);
- `promotion.py` (`PROMOTION_REGISTRY`; metni **sahip onaylar**, sha testte ve belgede sabitlenir);
- aylık Holm öneri toplu işi;
- `engine-query --approve|--reject|--retire --operator berke`;
- ilk adaylar:
  - ders kaynaklı klonlar (örn. "Box en az stop %0,5", "D4 55/20", "seçicilik ekstraları olmayan ana bot");
  - M2 varyantları;
  - XAUUSDT/PAXGUSDT altın trend ve hafta sonu adayları.

**Kabul:**
1. Aday defterleri yalıtılmıştır: `research/prospective` dışına yazma yoktur.
2. Zaman yolculuğu fixture'ıyla nedensellik testi geçer.
3. Her aday işleminin tam `tj_v1` satırı ve atfı vardır.
4. Durdurma yalnız kayıtlı bakışta tetiklenir.
5. Öneri yalnız tüm Kapı B koşulları sağlanınca üretilir (altın test).
6. Aday sonuçları canlı toplama asla eklenmez.
7. Runner `APPLIED_BOUNDED` yazamaz.

**Sahip:** kapı metnini onaylar; deploy; öneri çıktığında yazılı karar verir.

### P5 — Panel sayfası `/hedef` (ve sonra `/ders`) — bir sonraki normal worker sürümüyle

**Teslimatlar:**
- `StateReader` araştırma özeti kökü;
- `/hedef` ve `/ders` sayfaları ve `NAV_MORE` girdisi;
- GET-only, 405, dosya yok, eski dosya testleri.

**Kabul:** sayfa dosya yokken bayt-özdeş; 405 testleri; boyut sınırı; "> 24s ESKİ RAPOR" notu.

**Sahip:** normal sürümün parçası olarak onaylar (worker boşta durdurulup başlatılır).

### P6 — İsteğe bağlı worker kayıt-yalnız enstrümantasyonu (SAHİP ONAYI GEREKİR)

**Teslimatlar:**
- `TradeRecord`'a eklemeli `meta` / `targets`, ya da defter başına `closed_meta.jsonl` yan dosyası (karar anlık
  görüntüsü, risk anlık görüntüsü, `be_by_mfe`, D4/C4 sinyal bloğu, Box bağlamı, ilk hedefler);
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
- günlükte ilgili alanlar `RECONSTRUCTED` → `MEASURED` olur.

**Sahip:** açık onay; normal sürüm, boşta durdurma ve yeniden başlatma; 3 gün `--check`.

### P7 — Onaylı terfiler

Onaylanan her öneri için normal bir sürüm yeni bir kâğıt defter ekler: %0,25 risk, kendi ledger dizini, karne girdisi,
öğrenme modu etiketleri, yeni değişmezler. Defter günlük hedef toplamına girer. Motor önceden kararlaştırılan durdurma
kuralını raporlar; karar sahibindir.

---

## 11. Riskler

| Risk | Azaltma |
|---|---|
| Aşırı uydurma ve çoklu test: yüzlerce varyant × 45 enstrüman şanslı kazananlar üretir | mühürlü kayıtlar; kilitli bekletme; BH; kümülatif N ile DSR; CSCV PBO; plasebo; önceden kayıtlı bakışlar; zorunlu ileri Kapı B; aylık Holm. Yine de Kapı A geçenlerin çoğunun Kapı B'de düşmesi beklenir. |
| İsteğe bağlı durma (her gece yeniden test) | durum değişikliği yalnız kayıtlı bakışlarda, alfa harcamalı; gece değerleri "ara görünüm" |
| Hedef baskısı: +%1/gün için kaldıraç/risk artırma isteği | işlem başına sabit %0,5 riskte net R ile puanlama; düşüş isabet oranının yanında; k*/iflas tablosu; kaldıraç değişikliği asla otomatik önerilmez |
| "Bugün bir coin +%1 yaptı" yanılgısı | şans oranı permütasyonu, Holm, önceden seçilmiş enstrüman kuralı, 60 günlük CI alt sınırı ≥ %1 |
| Worker yavaşlaması veya OOM (RAM/vCPU bilinmiyor) | P0 boyutlandırma; Nice 19, CPUWeight 10, IO idle, OOMScoreAdjust 1000; pencereler; kabulde tur p95 ≤ +%5 |
| `daemon-reload` kayması (4G ↔ 6G) | kapılı kurulum, önce/sonra 6G doğrulaması, otomatik geri alma; P1'den sonra birim dosyası değişmez |
| Saat dilimi (VPS Europe/Istanbul) | her `OnCalendar`'da `UTC`; test |
| Paylaşılan IP REST ağırlığı | arşiv-önce; güvenlik payı 0,1; başlık takibi; 418/429'da durma; saatlik REST yok |
| Maliyetler modellenmiş, ölçülmemiş (3 bps sabit kayma, sıfır spread, stop seviyesinde dolum) | ADVERSE/STRESS kapıda; `cf_aux_v1` muhafazakâr R; kaydedilmemiş spread tahmini ayrıca; ileri aşamada gerçekleşen/modellenen maliyet oranı |
| Kısa altın geçmişi (XAUUSDT 2025-12, PAXG vadeli 2025-03; ince likidite; Dukascopy OTC bid farklı) | `KISA_GEÇMİŞ` işareti; daha sıkı Kapı B; uzun vekiller ayrı etiketli; gold_v1 zaten 0/32 buldu |
| Hayatta kalma yanlılığı (evren bugünün listesi) | günlük evren ve `exchangeInfo` anlık görüntüleri; arşivde varsa delist semboller; her raporda uyarı |
| Yeniden kurma hataları (bağlam, yol) | 1 tick eşleşme; fidelity kapısı; `RECONSTRUCT_FAILED` ve `MISSING` görünür; P6 bunları ölçüme çevirir |
| Küçük örneklemler (D4 13, M2 49) | dersler aylarca `OBSERVATION`'da kalır; CF kanıtı ayrı ve muhafazakâr; adaylar örneklemi defterlere dokunmadan büyütür; "VERİ YETERSİZ" görünür kalır |
| Ledger rotasyonu ve geç fonlama | gecelik kapanış arşivi; `rev`; elde tutulan pencerede uzlaştırma; `--check` rotasyon payı |
| Dosyalar arası tutarsız okuma (worker `os.replace` ile yazar) | anahtarlı, idempotent birleştirme; sonraki gece `rev+1` ile tamamlama |
| Mühür değişikliği (arşiv yeniden yayımı, REST düzeltmesi) | her çıktı `data_seal` anar; uzlaştırma farkı kaydedilir |
| Disk büyümesi | 14 günlük geriye-test ayrıntısı; 1m zip silme; 20 GB sınırı; state altında hacimli bir şey yok |
| Mevcut otomatik öğrenici (ResearchCoordinator) | P0'da sahibin kararı; motor dokunmaz |
| İki kod sürümü aynı makinede (app ve engine-app) | motor state'i yalnız kararlı şemalarla okur (`schema_version` denetimi fail-closed); `--check` iki SHA'yı da yazar |
| Sahibin iş yükü | aşama başına tek betik; `--dry-run` / `--check`; tek komutluk kapatma; kısa özet |

---

## 12. Açık sorular (sahip için)

1. **ResearchCoordinator** (`research_enabled=True`): mevcut otomatik filtre/küçültme etkinleştirmesi önceden onaylı mı,
   yoksa ayrı onaylı bir değişiklikle kapatılsın mı?
2. **RAM / vCPU:** P0 çıktısına göre motor birimleri için `MemoryMax` ve `CPUQuota` değerleri.
3. **Dukascopy:** VPS'ten erişilebiliyor mu? Erişilemiyorsa bir kerelik tarball'ı sahip mi taşıyacak?
4. **Tek enstrüman hedefi:** "en az bir coin veya altın" için önceden bir enstrüman bildirilsin mi (örn. XAUUSDT veya
   BTCUSDT)? Bildirilirse o enstrüman Holm düzeltmesi olmadan değerlendirilir; diğerleri Holm'lu kalır.
5. **Gün sınırı:** UTC gün mü kullanılsın (önerilen; 4h yayın düzeniyle uyumlu), yoksa İstanbul günü mü?
6. **Kapı metni:** `PROMOTION_REGISTRY` eşikleri (§6.7) bu haliyle onaylanıyor mu? Onay P4'ten önce gerekir.
7. **Altın defteri:** Bir altın adayı Kapı B'yi geçerse yeni bir altın kâğıt defteri kabul edilir mi? Hangi risk
   tavanıyla (öneri: %0,25)?
8. **Saatlik aday adımı:** 5m taktikler için gecikmeyi azaltacak saatlik birim (§6.6) gece yolu kararlı olduktan sonra
   istenir mi?
9. **Telegram:** "günlük rapor hazır" bildirimi istenir mi? Config değişikliği gerektirir.
10. **P6:** Worker'a kayıt-yalnız alan ekleme (kararı değiştirmeyen, yeniden başlatma gerektiren) ne zaman yapılsın?
11. **Açık pozisyon fonlaması:** `FuturesLedgerV2` açık pozisyondaki fonlamayı cüzdana anında mı yazıyor? Geriye doğru
    `E_book` hesabının bunu tam yakalayıp yakalamadığı P2 fixture testiyle doğrulanacak; uyuşmazsa satır `TAHMİN`
    etiketlenir.

---

## 13. Hakem "mutlaka düzelt" listesinin kapanışı

| Madde | Nerede |
|---|---|
| Gece öncesi/sonrası ledger parmak izi değişmezi kaldırıldı; çekirdek + açma modu denetimi | §2.8 |
| Panel yalnız normal app sürümüyle; o zamana kadar `--check` / `engine-status` / `scorecard --daily` | §7.7, P5 |
| REST bütçesi ≤ 0,1, başlık takibi, 418/429, saatlik REST yok | §3.4 |
| Tek motor kilidi, `SKIPPED_LOCKED`, zaman ayrımı, bellek kuralı | §2.4, §2.5 |
| Rotasyon ve geç fonlama; elde tutulan pencerede uzlaştırma; geriye doğru `E_book` | §4.2, §7.1 |
| Hedef hükmü: CI alt sınırı ≥ %1,00, ≥ 40 tam gün / 60, ≥ 30 işlem, %25 yoğunlaşma | §7.3 |
| İsteğe bağlı durma: kayıtlı bakışlar, alfa harcama, sabit BH ailesi | §5.7 |
| State'e yazma yok (shared-experience özetleri motor biriminden çalışmaz) | §2.1, §6.1 |
| Birim kayması kapısı (`NeedDaemonReload=no`, 6G önce/sonra), `setup_vps_v3.sh` yok | §9.3 |
| `OnCalendar` UTC + pencere testi | §2.2, P1 testleri |
| ResearchCoordinator için P0 sahip kararı | §9.1, P0, §12 |
| PBO yok → CSCV yazılır; o zamana kadar kapı kapalı | §6.4 |
| `python -I` yok; `-s -m` + `WorkingDirectory` | §2.3 |
| Config kayması: açık `--config` yolu, sha kaydı; `TRADINGBOT_DATA` açık | §2.3 |
| `TradeRecord`'da `initial_stop` / `targets` yok → xp / provenance / 1 tick rehydrate; yoksa `MISSING` ve ızgara dışı | §4.3, §5.2 |
| `label_outcome` çift ücret: yeniden kullanılmaz; regresyon testi | §4.2 |
| Altın: `LIVE_PAPER`'da "işlem yok", yalnız aday akışında | §7.1 |
| Rotasyon riski `--check`'te | §4.2 |
| MTM `LAST_PRICE_PROXY`; EKSİK günler hariç | §7.1 |
| Paylaşılan IP: tek `BudgetPool`, düşük pay, seri başına yeniden deneme | §3.4 |
