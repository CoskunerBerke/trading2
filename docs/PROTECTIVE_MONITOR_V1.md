# KORUYUCU İZLEYİCİ V1 — ana bot kesinti politikası + beş defterin turdan bağımsız izlemesi (2026-09-24)

Taban: `work/runtime-fixes-v1` @ `d8b0c8c` (0bb85e1 + d8b0c8c korunur). Mod PAPER. Strateji, sermaye, kaldıraç,
giriş/risk eşikleri, T2/M2 zaman ufukları ve `config.yaml` **değişmedi**. Defterler sıfırlanmaz.

## 1. Sorunlar (önceki yerel rapordan; bu oturumda üretimde yeniden ÖLÇÜLMEDİ)

* Ana bot kesintiyi `GapReconciler` ile geçmiş 1m/5m mumlarla oynatıyor, kesinti aralığında kapanmış 1h barları da
  imleçle uyguluyordu → canlı PAPER defterine **geçmiş zamanlı** kapanış yazılabiliyordu.
* `watch` tek iş parçacıklı: 60 sn'lik çıkış kontrolü yalnız turlar arasında koşuyordu. Ağır turda (≈442 sn) T2/M2
  ≈370 sn, formasyon defteri 527 sn izlenmedi; bloke turda hiç izlenmez.
* Ana defterin 60 sn izleyicisi (ve tur tiki) futures pozisyonunu spot ticker `last` ile değerlendiriyordu.

## 2. Ana bot — kesinti politikası (T2/M2/Box/formasyon ile AYNI)

* Süreçteki ilk defter etkinliğinde (tur, izleyici, `exit_check` — hangisi önce) bir kez: son canlı iz =
  `max(exit_watermark, defter.updated_at)`; `MONITORING_GAP_S` (2 sa) aşılmışsa `state/monitoring_gaps.jsonl`a
  `book=main`, `from/to`, **yükleme anındaki pozisyonlar** yazılır.
* Aralıkta kapanan 1h barlar ana deftere **uygulanmaz** (`gap_until_ms`; imleç geçer).
* **İki saatten KISA kesintiler (politika değişmedi, bilinçli):** eşik `MONITORING_GAP_S` = 2 sa (diğer dört defterle
  aynı). Bundan kısa bir kesinti kesinti olarak KAYDEDİLMEZ ve arada kapanan 1h barlar normal bar sözleşmesiyle
  (girişten sonra açılmış, bir kez) uygulanır — yani bu durumda **geçmiş zamanlı (bar kapanış anında) kapanış hâlâ
  MÜMKÜNDÜR**. Eski `GapReconciler` 5 dk'dan uzun her kesintiyi 1m mumlarla dolduruyordu; o yol kaldırıldı, 2 saatin
  altındaki bar uygulaması ise korunuyor. `GapReconciler` canlı deftere
  artık hiç çağrılmaz; yeni girişler kesinti yüzünden kilitlenmez (`_gap_blocked` daima False).
* Koruyucu yönetim **ilk geçerli güncel perp fiyatla** sürer; o gözlemin gerçek zamanı ve fiyatın kaynak zamanı
  `state/monitoring_gap_observations.jsonl`a (pozisyon başına bir satır) yazılır. Fiyat stopun ötesindeyse kapanış
  **şimdiki** zamanda, gözlenen fiyattan (`GAP_FILL_AT_FIRST_OBSERVATION`). Fiyat yoksa tick YOK (uydurma fiyat/dolum yok).
* Geçmiş uzlaştırma yalnız **ayrı simülasyon**: kesinti kaydı, yükleme anındaki defterin (pozisyon + cüzdan + maliyet
  ayarları) anlık görüntüsünü `state/outage_simulations/<defter>-<başlangıç>.ledger_at_load.json`a yazar.
  `python -m tradingbot outage-simulate --book main` bunu geçmiş mumlarla oynatır ve sonucu
  `SIMULATION_NOT_APPLIED_TO_PAPER_LEDGER` etiketiyle ayrı dosyaya yazar; canlı defter/damga/öğrenme değişmez.

## 3. Beş defterin koruyucu izleyicisi (`tradingbot/protective_monitor.py`)

* `watch`, motor kurulunca `ensure_protective_monitor(interval_s=--exit-every)` ile **ayrı iş parçacığı** başlatır
  (varsayılan 60 sn). İzleyici canlıyken eşzamanlı `exit_check` çağrılmaz; ana iş parçacığı yalnız izleyicinin ana
  defter kapanışlarını öğrenir (`drain_protective_closes`, beklemede 2 sn'de bir ve turda). İzleyici kurulamaz/ölürse
  eski eşzamanlı yol (turlar arası) devreye girer.
* **Ağ kilit dışında:** her geçişte önce kısa kilitle kimlik anlık görüntüsü (`held_ids`), sonra kilitsiz tek toplu
  `premiumIndex` isteği, sonra defter başına tek kısa atomik bölüm `protect` (boşluk → korumalı tick → kayıt).
  Funding ağ adımı turda/tarayıcıdadır (kilitsiz); tick yalnız bellekteki gerçekleşmiş kaynağı okur. Formasyon
  defterinde tetik anındaki likidite isteği sürerken defter kilidi bırakılır (`PatternBook._call_unlocked`).
* **Fiyat kimliği:** yalnız doğrulanmış USDⓈ-M perp mark (borsa zamanıyla, yaş ≤ 180 sn, gelecekte değil). Ana defterin
  tur tiki de artık `_paper_marks` (perp mark, bu an) kullanır; spot ticker futures pozisyonuna uygulanmaz.
* **Çift kapanış yok:** tur ile izleyici aynı defter kilidinde sıralanır (ana defter: `_exit_lock`; `open`, funding
  uzlaştırma, yapı stop'u sıkılaştırma ve tur tiki de bu kilitte). Kilit altında pozisyon kimliği yeniden doğrulanır
  (`POSITION_CHANGED`); miktar daima güncel pozisyondan okunur (TP1 kısmi sonrası yalnız kalan kapanır).
* **Zaman ve tazelik (PR #1 düzeltmesi):** fiyat tiki borsa **kaynak** zamanını (`src_ts`) ve bizim **alınma** zamanımızı
  (`fetched_ts`) ayrı taşır; pozisyona uygulananlar `pos.meta.mark_src_ts_ms` / `mark_fetched_ts_ms`e yazılır (defterle
  kalıcı). İki fiyatın da borsa zamanı varsa daha eski borsa zamanlı fiyat **paysız** reddedilir (`OLDER_THAN_APPLIED`;
  önceki 10 sn'lik `ORDER_SKEW_S` payı kaldırıldı — 5 sn eski fiyatla stop kapanabiliyordu); borsa zamanı karşılaştırılamıyorsa
  alınma zamanları karşılaştırılır (önbellekten gelen eski alınma reddedilir). Tazelik **uygulama anında**, kilit altında
  yeniden denetlenir (`apply_clock`): fiyat zamanı 180 sn'den eskiyse `STALE_AT_APPLY`, 120 sn'den ileriyse
  `PRICE_TIME_IN_FUTURE` → tick yok. Hiç zamanı olmayan tik (yalnız test/elle yollar) denetlenemez; uygulanır ama sırayı
  ilerletmez. Kapanmış bar uçları ayrı sözleşmedir.
* **Turda açılan pozisyon (Windows ölçümü #2 düzeltmesi):** tur giriş fiyatını adımın başında alır, pozisyonu sonra
  açar; pozisyon izleyicinin geçişinden hemen sonra deftere girerse ilk taze fiyatlı kontrol bir sonraki düzenli geçişi
  bekliyordu (M2 ARB: 84 sn). Artık ana bot girişi (`_execute_futures_entry`) ve T2/M2 adımı yeni pozisyon açınca
  izleyiciyi dürter (`ProtectiveMonitor.poke`): izleyici hemen bir geçiş yapar (kilit tutulmadan çağrılır, ağ izleyici
  iş parçacığında). Giriş fiyatı ve alım-satım kararları değişmez. Box ve formasyon defterleri kendi iş parçacıklarında
  açar; onlara dürtme eklenmedi (ölçümde yeni formasyon pozisyonunun ilk aralığı 43,6 sn).
* **Yeniden başlatma:** ana defter kapanışları öğrenilene kadar `state/protective_learn_queue.json`da kalır; yeni süreç
  bir kez öğrenir; zincir onarımı (`_complete_close_chain`) kuyruktakilere dokunmaz (çift öğrenme yok). Pozisyon dict'i
  kopyala-yaz güncellenir (kilitsiz okuyucular "dictionary changed size" ile düşmez); atomik yazımın geçici dosyası iş
  parçacığı başına ayrıdır; izleme damgası monotondur.
* **Ölçüm:** `state/protective_monitor.json` → defter/pozisyon başına gözlem sayısı, en uzun ve süren aralık, kaynak
  (monitor/tour/box_timer/scanner), 60 sn aşımları. Açık pozisyonu olmayan defter `open_positions: 0` — doğrulanmış
  sayılmaz.

## 4. Doğrulama

* `tests/test_protective_monitor_v1.py` (21 davranış testi; denetlenebilir saat + `threading.Event`/`Semaphore`, uyku
  yok; biri 1 sn gerçek aralıklı kısa kontrollü senaryo). Tabanda (d8b0c8c) 20'si başarısız (1'i yeni mantığın
  sahte-kesinti koruması — tabanda da geçer); başarısızlık nedenleri: kesintinin 1m mumlarla oynatılması (5 sayfa
  istek), geçmiş zamanlı stop kapanışı (1h kesinti barı), spot ticker ile kapanış, izleyici modülünün yokluğu.
* `python scripts/measure_protective_monitor.py index-check` — pyarrow ile Parquet'ten formasyon indeksinin kurulup
  sorgulandığını gösterir (SENTETİK).
* Üretime benzer ölçüm (bu depoda/bulutta girdisi YOK) — bkz. §4.1.

### 4.1 Yerel ölçüm adımları (Windows ya da Linux)

Betik (`scripts/measure_protective_monitor.py`) Windows ve Linux'ta çalışır; ek paket gerekmez. Windows işi CI'da da
(`measure-script-windows`, boşluklu yol ve Windows konsoluyla) sınanır.

1. Kod: `git fetch origin && git checkout claude/gifted-knuth-0ehpcs` (PR #1).
2. Ortam (Windows PowerShell örneği): `py -3.12 -m venv .venv` → `.venv\Scripts\activate` → `pip install -r requirements.txt`.
3. Ön kontrol — kopyalamaz, çalıştırmaz; engel varsa "HAZIR DEĞİL" ve çıkış kodu 1:
   ```
   python scripts\measure_protective_monitor.py preflight --source D:\tb-olcum\data --work D:\tb-work --config D:\tb-olcum\config.yaml --net
   ```
   Denetler: Python ≥ 3.11, bellek ölçüm yöntemi, pyarrow, `state/` + `market/` düzeni ve boyutu, boş disk (kopya + %5 +
   512 MB), `--work` ile `--source`un iç içe olmaması, config modunun PAPER olması, state/önbellek/kasa/log/yedek
   yollarının çalışma kopyasında kalması, `--net` ile Binance public erişimi.
4. Ölçüm (yaklaşık 10–15 dk + kopyalama):
   ```
   python scripts\measure_protective_monitor.py measure --source D:\tb-olcum\data --work D:\tb-work --config D:\tb-olcum\config.yaml --out olcum.json
   ```
5. `olcum.json` sonucu: `memory.peak_rss_process_mb` (< 4096 hedef), `monitor.books.<defter>.verdict`
   (`WITHIN_60S` / `OVER_60S` / `NOT_MEASURED_NO_OPEN_POSITION`), `monitor.over_60s`, `tour_s`, `pattern_index`.

Aralık tanımı: pozisyon başına iki ardışık koruyucu gözlem arasındaki süre; gözlem zamanı UYGULANAN fiyatın kendi zamanıdır
(borsa, yoksa alınma), son gözlem geriye gitmez. Ölçüm sırasında açılan pozisyon, izleyicinin onu içermeyen son anlık
görüntüsünden sayılır (tur girişi `opened_at`'ı turun karar anıyla yazar). Ölçüm sırasında açık pozisyonu kalmayan defter
yalnız ilk kontrolüyle ölçülmüş sayılır; zaman içindeki kadansı DOĞRULANMIŞ değildir.

İlk yerel sonuç (2026-09-24, Windows 11, Python 3.13, 2,2 GB saatlik yedek kopyası; ölçücü düzeltmesinden ÖNCE):
tur 665 sn; bellek tepesi 2432 MB çalışma kümesi / 3262 MB işlenmiş (VPS sınırıyla birebir karşılaştırılamaz); izleyici
13 geçiş, 0 hata, geçiş aralığı ≤ 60,1 sn, en uzun geçiş 23 sn. Pozisyon aralıkları çoğunlukla 60,0–60,8 sn (60 sn aralık +
fiyat alma gecikmesi — hedefi 1 sn'den az aşıyor), bir geçişte 79,4 sn (fiyat alma ~20 sn sürdü; gerçek). 108,7 sn (T2 ZEN)
ve 551,9 sn (M2 ARB) ölçücü hatasıydı (tur `now`u geri yazılıyor / giriş karar anından sayılıyordu; düzeltildi); M2 ARB'nin
108,7 sn'lik değeri gerçek olabilir, yeni ölçüm gösterir. Box'ın iki pozisyonu ilk kontrolde (0,9 sn) kapandı: Box kadansı
ölçülmedi. Sonuç: 60 sn hedefi bu ölçümde tam tutmadı; VPS (Linux) üzerinde ölçülmedi.

İkinci yerel sonuç (aynı ortam ve veri, `8e2f9ec`, ölçücü düzeltmesinden SONRA, dürtmeden ÖNCE): tur 615 sn; bellek tepesi
2358 MB çalışma kümesi; izleyici 12 geçiş, 0 hata, geçiş aralığı ≤ 60,1 sn, en uzun geçiş 8,4 sn. Ana/T2/formasyon en uzun
61,0 sn (60 sn aralık + fiyat alma gecikmesi). M2 ARB 84,0 sn (gerçek): tur girişi izleyici geçişinden sonra deftere girdi,
ilk taze kontrol sonraki düzenli geçişte geldi — yukarıdaki dürtme bunu hedefler; dürtmeli sürüm henüz ÖLÇÜLMEDİ. Box'ın
pozisyonları yine ilk kontrolde (4,6 sn) kapandı: Box kadansı ölçülmedi. Kalan ~1 sn'lik aşım aralık kararıdır
(`--exit-every`, ör. 45 sn); bu sürümde değiştirilmedi.

Üçüncü yerel sonuç (aynı ortam ve veri, `86a4377`, dürtmeli): tur 618 sn; bellek tepesi 2360 MB çalışma kümesi; izleyici
12 geçiş (1 dürtme), 0 hata, geçiş aralığı ≤ 60,2 sn, en uzun geçiş 10,3 sn. Ana/T2/formasyon ve M2'nin yüklemedeki
pozisyonları en uzun 60,0 sn. M2 turda yine ARB açtı; dürtme çalıştı (izleyicinin ARB'yi ilk taze fiyatla gördüğü geçiş
dürtmeyle başladı). Kalan 69,0 sn, ARB'nin giriş fiyatının yaşıdır: tur fiyatları adımın başında alıyor, M2 adımı ~1 dk
sonra bu fiyatla açıyor; ölçücü aralığı pozisyonun karşılaştırıldığı son fiyattan sayar. Bu, izleme değil GİRİŞ fiyatı
zamanlamasıdır (T2/M2 dolum fiyatı); bu PR'da değiştirilmedi. Box kadansı yine ölçülmedi (pozisyonlar 4,7 sn'de kapandı).

Güvenlik: `--source` yalnız okunur; `--work` varsa betik durur (silmez). Config, kopyadan ÖNCE doğrulanır. Bütün veri
yolları çalışma kopyasına zorlanır (ortamdaki `TRADINGBOT_VAULT_PATH` dahil — gerçek Obsidian kasasına yazılmaz); Obsidian
git senkronu ve Telegram/Discord bildirimleri o süreçte kapatılır (rapor `neutralized`). Ctrl+C: temizlik yapılır, rapor
`INTERRUPTED` olarak yazılır (çıkış kodu 130).

Bellek ölçütü: Linux'ta `/proc` RSS/VmHWM (VPS 4G sınırına en yakın). Windows'ta Win32 çalışma kümesi (WorkingSet /
PeakWorkingSet) ve işlenmiş bellek tepesi — Linux RSS'e **yakın ama aynı değil**; rapor bunu
`memory.comparable_to_vps_limit: false` ile yazar. Kesin 4G yargısı için ölçüm Linux'ta (ya da WSL2'de) tekrarlanmalıdır.

## 5. Dağıtım notları

* Paket bu oturumda güncellenmedi (depo dışı `trading2-deploy`, bulutta yok). Yeni uç için:
  ```
  git fetch origin <dal> && git bundle create tb-<uç>.bundle 3b0ae8e..origin/<dal>   # dal ref'iyle
  sha256sum tb-<uç>.bundle
  ```
  ve betikteki beklenen uç SHA'sı ile bundle sha256'sı güncellenmeli.
* **Geri dönüş uyarısı:** `3b0ae8e`'ye dönmek OOM'u (655 MB aday dosyasının belleğe alınması) GERİ GETİRİR — sağlıklı
  geri dönüş değildir. `d8b0c8c`'ye dönmek OOM onarımını korur ama ana botun geçmiş mumlarla kapanış yazmasını ve ağır
  turdaki izleme gecikmesini geri getirir.
* Yeni state dosyaları: `protective_monitor.json`, `protective_learn_queue.json`, `monitoring_gap_observations.jsonl`,
  `outage_simulations/`. `gap_status.json` artık canlı yolda yazılmaz.
