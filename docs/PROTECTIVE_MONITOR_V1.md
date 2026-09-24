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
* Üretime benzer ölçüm (bu depoda/bulutta girdisi YOK):

  ```
  python scripts/measure_protective_monitor.py measure --source <VPS-kopyası>/data --work <boş-yol> \
      --config <VPS-kopyası>/config.yaml --out olcum.json
  ```
  Kaynak kopya salt okunur; `--work` altına kopyalanır (7,3 GB için yer gerekir). Rapor: süreç bellek tepesi (VmHWM),
  tur süresi, formasyon indeksi durumu, beş defterin açık pozisyon başına en uzun izleme aralığı, 60 sn aşımları.
  Binance public uçlarına erişim gerekir.

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
