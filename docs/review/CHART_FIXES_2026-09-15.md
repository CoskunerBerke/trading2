# CHART ANALYSIS V1 — bağımsız inceleme bulgularının kapanışı (2026-09-15)

Kapsam: `work/chart-analysis-v1` dalında 2e31926e7348d1ed145a4615adceff30b85527ab üzerine yapılan onarımlar. Yeni
strateji, gösterge, kârlılık optimizasyonu ya da replay araştırması YOK; kâr etkisi iddia edilmiyor. Bu iş analiz
görüntüsünün doğruluğu ve izlenebilirliği içindir. VPS'e DAĞITILMADI (paket ayrı hazırlanır, taban 3501304).

## 1. Kapanış tablosu

| # | bulgu | yeniden üretildi | düzeltildi | doğrulandı | test |
|---|---|---|---|---|---|
| 1 | 15m/1h/1w isteği `closed_bars` KeyError (HTTP 500) | evet (`evidence-2026-09-15/repro_before.txt` [1]) | `tradingbot/timeframes.py` tek kaynak; `candle_confirmation.closed_bars` aynı tablo; bilinmeyen dilim 400 | beş dilim gerçek API'de 200; sınır: açılış+süre−1 ms hariç, kapanışta dahil; gerçek tarayıcıda 1h görünümü | `test_f1_every_supported_timeframe_*`, `test_f1_closed_bar_boundary_*` |
| 2 | kayıtta spot/futures ayrımı kayboluyor (seri anahtarı, latest, history, analysis_id) | evet [2] | store v2 (`book\|market\|symbol\|tf`, piyasa yolda ve satırda), v1 uyumluluk politikası, API kimlik doğrulaması (404), motor kaydı defter piyasasında (kâğıt defterler USDM_PERP; ana bot SPOT kaydı futures defterini taşımaz) | aynı zaman damgalı iki piyasa; farklı piyasanın analysis_id'si reddedildi; T2/M2 futures kapsamı; gerçek tarayıcıda spot↔futures | `test_f2_stored_analysis_is_market_strict_*`, `test_f2_legacy_v1_index_*`, `test_engine_records_are_market_strict_*` |
| 3A | aynı mum içinde defter değişimi canlı grafiğe yansımıyor (panel önbelleği; motor kaydı "şimdi" sayılıyor) | evet [3A, 3A'] | önbellek anahtarı canlı dosya sürüm imzası; "şimdi" = kimlik eşitliği sözleşmesi; `engine_record` + `live` katmanı; canlı işaret fiyatı | açılış/kısmi kapanış/stop/TP/kapanış aynı barda görünür; store ve geçici yollar ayrı ayrı; kod/config değişince eski kayıt güncel sayılmaz; gerçek tarayıcıda kapanış sonrası | `test_f3a_now_view_follows_ledger_changes_*`, `test_f3a_engine_record_is_shown_only_when_*` |
| 3B | miktar/hedef/kapanış değişimi aynı analysis_id (kayıt kaybı) | evet [3B] | parmak izi: miktar, hedefler, stop, kapanan işlem, plan, hüküm; sayısal normalizasyon; işaret fiyatı hariç | yeni kayıt oluşur, önceki dosya byte-aynı, özdeş tekrar dosya çoğaltmaz | `test_f3b_fingerprint_captures_*` |
| 4 | geçmişten bakarken kapsam değişimi yanlış istek; history'de geç yanıt koruması yok | evet (gerçek CHART_JS, node) | kapsam bir kez okunur, geçmiş sıfırlanır, `req` + kapsam + kimlik eşleştirmesi (grafik ve history) | node harness: geç/ters sıralı yanıtlar; gerçek tarayıcı ağ günlüğü: M2 geçmiş seçiliyken ana defter → `history?book=main`, `chart?book=main` (analysis_id yok) | `test_f4_chart_js_scope_change_*` |
| 5 | pivot/formasyon teyit zamanı bar açılışı (bir mum erken) | evet [5] | `confirmed_at`/`known_at` = teyit/kırılış/tanınma barının KAPANIŞI; x koordinatı değişmedi; paylaşılan dedektör alanları değişmedi | kapanıştan 1 ms önce teyitsiz, kapanışta teyitli; ekran/JSON/geçmiş aynı zaman; eski test düzeltildi | `test_f5_pivot_and_pattern_confirmation_*`, `test_f5_stored_record_api_and_history_*`, `test_pivots_confirmed_before_as_of_*` |
| 6 | arşivde olan eski mumlar için "veri yok" (önce kuyruk, sonra tarih) | evet [6] | `CandleSource.load(end_ts=)`: tarih süzgeci kuyruktan önce; dürüst 404 + arşiv aralığı | 1000 barlık arşivde 600. bar için n=50 ve n=300 açılır, analiz sonrası mum yok; gerçekten eksik tarih 404 | `test_f6_historical_candles_*` |
| K | JS `identity.analysis_id` okuyordu; kimlik kökte | evet [K] | kök alan; kaynak satırı ve PNG/JSON adı kimlik/piyasa/dilim/defter; sunucu dosya adı aynı | gerçek tarayıcıda `analiz <id> · motor kaydı · kod · defter · futures 4h`; `SOL_4h_futures_strategy_paper_<id>` / `..._canli-<an>` | `test_f4_*`, `test_chart_js_reads_analysis_id_from_root_*`, `test_snapshot_download_name_*` |

Köşeli parantezli numaralar `docs/review/evidence-2026-09-15/repro_before.txt` (8/8 BULGU_VAR, HEAD 2e31926) ve
`repro_after.txt` (8/8 BULGU_YOK, onarılmış kod) çıktılarının bölümleridir; betik `repro_findings.py` gerçek FastAPI
uygulamasını (TestClient) sentetik state ile çalıştırır.

## 2. Gerçek tarayıcı doğrulaması (sentetik veri, gerçek motor turu)

Ortam: `tests/test_engine_v3._engine` harness'ı ile GERÇEK motor turu (T2/M2 kâğıt defterleri, chart_analysis açık)
→ `state/chart_analysis/` (6 seri, ikinci tur yeni kayıt yazmadı) → motorun çerçevelerinden mum CSV'leri → panel
(uvicorn, 127.0.0.1) → Claude tarayıcı bölmesi. Gözlemler DOM (`#srcline`, `#explain`, `window.__chartTest`) ve ağ
günlüğünden okundu:

* T2 futures 4h "şimdi": `analysis_origin=store`, `analysis_stored=true`, `engine_record.matches_now=true`; kaynak satırı
  `canlı defter: … (strategy_paper/futures_ledger.json) · analiz 5875e9087cc455ed · an … · motor kaydı · kod 2e31926 ·
  defter strategy_paper · futures 4h`; TP YOK, GERÇEK GİRİŞ, STOP, Günlük EMA200 (kural); açıklama T2 satırları.
* 1h: `identity.timeframe=1h`, `tf_ms=3600000`, 300 bar, `panel hesabı (kaydedilmedi)`, geçmiş listesi sıfırlandı;
  indirme adı `SOL_1h_futures_strategy_paper_canli-202609151010`.
* M2 (1h ve 4h): `no_target` var, `target` yok; açıklama "M2: günlük kapanış … > 28 gün önceki kapanış … (2026-09-08)";
  4h'de motor kaydı eşleşti; geçmiş listesi yalnız M2 4h kaydını gösterdi; indirme adı `SOL_4h_futures_strategy_paper_m2_<id>`.
* Geçmiş seçiliyken (M2 kaydı) defteri ana bota çevirme: geçmiş seçimi temizlendi; ağ günlüğü
  `GET /api/chart/SOL/history?tf=4h&market=futures&book=main&req=5` ve `GET /api/chart/SOL?tf=4h&market=futures&book=main&n=300&req=6`
  (analysis_id YOK); çizilen kimlik `book_id=main`.
* Ana bot spot: `identity.market_type=SPOT`, motor kaydı (SPOT) eşleşti, pozisyon boş, spot planı; futures: `USDM_PERP`,
  pozisyon boş, spot kaydı listelenmedi (geçmiş listesi boş).
* Aynı bar içinde T2 pozisyon kapanışı (defter dosyası değiştirildi, mum verisi aynı): `panel-ephemeral`, pozisyon boş,
  `analysis.position=null`, kaynak satırında `motorun son kaydı 5875e9087cc455ed (…) şimdiki durumdan farklı: defter/karar
  durumu farklı (…)`; geçmişten eski kayıt seçilince `GEÇMİŞ ANALİZ … işlem katmanı o anki defter kaydı`, pozisyon
  (giriş 2505.56, stop 2279.37, TP yok) o anki haliyle; `max(t) == last_closed_bar`; indirme adı
  `SOL_4h_futures_strategy_paper_5875e9087cc455ed`.
* Ekran kanıtları (Plotly dışa aktarımı, sentetik veri): `screens/synthetic-2026-09-15-t2-historical-after-close-desktop.jpg`,
  `screens/synthetic-2026-09-15-t2-1h-now-desktop.jpg`.

## 3. Korunan davranış

* Ana bot / T2 / M2 kararları değişmedi: `test_engine_decisions_unchanged_and_snapshots_deduped` (açık/kapalı motor turu
  karşılaştırması) geçiyor; `chart_patterns`/`confirmed_swings`/`ema200_trend.decide` DEĞİŞMEDİ (zaman alanları tüketicide
  türetildi). Mum vetosu ENFORCE / grafik kapısı SHADOW config'ten okunur, değiştirilmedi.
* Kayıt dosyaları değişmez (`test_f3b_*`, `test_f2_legacy_*`: byte karşılaştırması); panel hiçbir state dosyasına yazmaz
  (`test_f3a_now_view_*`, `test_api_chart_missing_stale_history_and_read_only`).
* T2/M2 defterleri, bakiyeleri, sayaçları bu değişiklikle ilgisiz; BTC rejimi DOWN'da otomatik kapatma eklenmedi.

## 4. Açık kalanlar / notlar

* Panelin spot pozisyon kaynağı: V3 motoru `spot_ledger.json` yazar, 2e31926 paneli `portfolio.json` okuyordu; onarımla
  panel önce `spot_ledger.json`ı (varsa) okur, yoksa eski dosyaya düşer. Ana botun spot geçmişi `spot_ledger.json`
  history'sinden gelir.
* Trailing stop kullanan bir defterde stop değişimi kayda değer sayıldığı için tur başına bir kayıt çıkabilir (saklama
  sınırı seri başına 300 korunur; T2/M2 sabit stop).
* Kayıt boyutu bar/dayanak sayısına göre ~15–125 KB (2e31926 belgesi 10–25 KB diyordu).
* VPS'e dağıtım bu görevde YAPILMADI; paket `tb-deploy-<yeni sha>.sh` + `tb-<yeni sha>.bundle` (taban 3501304) ayrı hazırlanır.
