# CHART ANALYSIS V1 — botun gerçek hesaplarını ve karar gerekçelerini grafikte görünür kılma

Amaç: kullanıcı bir çizgiye baktığında **neden orada olduğunu** (dayanak pivotlar, teyit zamanı, gerekçe) ve
**işlem kararını etkileyip etkilemediğini** görsün. Bu katman salt gösterimdir: hiçbir giriş/çıkış kapısı,
defter, sermaye, risk bütçesi, evren ya da öğrenme durumu değişmez (bkz. `tests/test_chart_analysis_v1.py::
test_engine_decisions_unchanged_and_snapshots_deduped` ve `tests/test_chart_analysis_v1_fixes.py::
test_engine_records_are_market_strict_and_panel_now_view_matches_them`).

Sürüm notu: 2e31926 sonrası bağımsız incelemenin altı davranış bulgusu ve JS kimlik hatası 2026-09-15'te onarıldı
(§7). Bu belge onarılmış sözleşmeyi anlatır.

## 1. Düzeltilen grafik kusurları (2e31926)

| kusur | dosya | düzeltme | test |
|---|---|---|---|
| futures isteği spot dosyasına düşüyordu; parquet aramasında sembol/piyasa/dilim gevşekti | `tradingbot/dashboard/candles.py` `CandleSource.candidates/find/_parquet_matches` | piyasaya göre izinli önekler; parquet'te sembol/dilim/piyasa KESİN eşleşme; eksik veri `missing=True` ile açıkça eksik döner, başka piyasa/dilimle doldurulmaz | `test_candle_source_is_market_strict` |
| `api_candles` pozisyonu market'ten bağımsız futures defterinden, planı diğer piyasadan alıyordu | `tradingbot/dashboard/app.py` `_plan_for_market`, `_position_for_market` | spot yanıtı futures giriş/stop/plan taşımaz; futures yanıtı futures defteri; kâğıt defterler yalnız futures | `test_api_candles_and_chart_do_not_leak_futures_into_spot`, `tests/test_dashboard.py` |
| `render_signal_chart` hedefsiz açık LONG için "BEKLE" yazıp işlem katmanını çizmiyordu | `tradingbot/charts.py` | giriş+stop TP'den bağımsız çizilir; hedef yoksa "TP YOK" yazılır; hedef kutuları yalnız hedef varsa | `test_render_signal_chart_draws_targetless_position` |

## 2. Ortak analiz çıktısı (şema `chart_analysis_v1`)

`tradingbot/chart_analysis.py::build_snapshot` → tek analiz anı:

* **kimlik**: `exchange`, `market_type` (USDM_PERP/SPOT — **defterin** piyasası; kâğıt defterler her zaman USDM_PERP),
  `symbol`, `timeframe`, `book_id` (`main` | `strategy_paper` | `strategy_paper_m2`), `book_name`, `as_of`,
  `last_closed_bar` (ts, iso, close), `code_sha` (`TRADINGBOT_CODE_SHA` ya da `git rev-parse HEAD`), `config_hash`
  (entry_selectivity + strategy_paper + entry_universe + chart_analysis bölümlerinin özeti);
  `analysis_id` = sha256(sembol + piyasa + dilim + defter + son kapanmış bar + kod + config + karar parmak izi)[:16] —
  **kök alan** (`snapshot.analysis_id`; kimlik sözlüğünde tekrarlanmaz). Mum provenansı (`source.bar_market`,
  `source.provenance`) kimlikten AYRI yazılır: ana botun SPOT kaydı futures defterini taşımaz (tersi de).
* **karar parmak izi** (`decision_fingerprint_payload`): kural koşulu/rejim/sinyal barı, karar hükmü/engel kodu/risk
  izni, plan (giriş/stop/yön/geçerlilik/hedefler), pozisyon (id, yön, giriş, stop, **miktar, hedefler**, açılış),
  kapanmış işlem kuyruğu (**sayı + son kapanan işlemin id/kapanış/çıkış fiyatı/nedeni**; kuyruk `HISTORY_TAIL`=50, motor
  ve panel aynı). Sayısal alanlar normalize edilir (defter nesnesi float, defter JSON'u Decimal-string → aynı parmak
  izi). İşaret fiyatı ve açık K/Z parmak izine GİRMEZ (her fiyat güncellemesi dosya üretmez).
* **öğeler** (`elements[]`): `kind`, `layer`, `label_tr`, `price` / `lower`–`upper`, `t0`/`t1` (+ eğik çizgide
  `y0`/`y1`), `anchors[]` (dayanak: zaman, fiyat, taraf, rol, teyit zamanı), `confirmed_at`,
  `invalidation_tr`, `rationale_tr`, `decision_impact` ∈ {USED_IN_DECISION, OBSERVATION_ONLY, UNCONFIRMED,
  INVALID}, `source` (modül/fonksiyon/parametre), `status` (trend: INTACT/BROKEN).
* **zaman sözleşmesi** (`time_contract`, bulgu #5): `timestamp` / `t0` / `t1` / `anchors[].timestamp` / `break_at` /
  `recognition_at` = ilgili barın **AÇILIŞI** (çizim x koordinatı; paylaşılan dedektör alanları değişmedi).
  `confirmed_at` / `anchors[].confirmed_at` / `break_known_at` / `recognition_known_at` / `broken_at` / `touches[].known_at`
  = bilginin **ilk bilinebildiği an = ilgili barın KAPANIŞI** (açılış + dilim süresi). Pivot, teyit barı (i+lookback)
  kapanmadan 1 ms önce teyitli DEĞİLDİR; kapanış anında teyitlidir. Her `confirmed_at` ≤ `as_of`. Göstergeler son
  kapanmış barın kapanışında, günlük kural referansları (EMA200 / 28g) günlük barın kapanışında bilinir.
* **kural durumu** (`rule_state`, T2/M2): `ema200_trend.rule_state` — `decide` ile aynı okuma; close, signal_ts,
  ema200, atr14, ref_close/ref_ts (M2), above, regime, stop_if_open.
* **giriş anı** (`entry_features`): defter belleğinden (giriş anındaki close/EMA200/ATR14/stop, M2 için
  referans kapanış ve tarihi) — ŞU ANKİ analiz değerinden ayrı alan.
* **açıklama** (`explanation[]`): yapılandırılmış ölçümlerden Türkçe satırlar; LLM yok; hesaplanmamış
  olasılık/hedef yok; formasyon yoksa "bulunamadı".

Hangi çizgi hangi hesaptan gelir:

| katman | kaynak (aynı fonksiyon, ikinci formül yok) | kararı etkiler mi |
|---|---|---|
| teyitli pivot (`pivot_high/low`) | `learn.multitimeframe_context.confirmed_swings` (formasyon dedektörünün pivotları; `confirmed_at` = i+lookback barının KAPANIŞI) | hayır (OBSERVATION_ONLY); son teyitsiz uç `pivot_pending` = UNCONFIRMED |
| destek/direnç/fiyat bölgesi (`zone`) | `equal_level_clusters` (ATR toleransı, ≥2 dayanak); alt/üst = üyelerin gerçek min/max | hayır |
| trend çizgisi (`trend_support/resistance`) | son iki teyitli dip/tepe; eğim, ek temas, kapanış ihlali (`breaks[].known_at` = ihlal barı kapanışı) | hayır; ihlal → INVALID |
| formasyon çizgileri (`pattern_line`) | `chart_patterns.detect_chart_patterns` kaydındaki `anchors` + `geometry` (boyun, düz/eğik sınır, direk, kırılış barı); `confirmed_at` = tanınma barı kapanışı | config'e göre: chart kapısı ENFORCE+taze → USED_IN_DECISION; SHADOW → OBSERVATION_ONLY (üretimde SHADOW) |
| günlük EMA200 / 28g referans (`rule_reference`) | `ema200_trend.rule_state` | T2/M2 için USED_IN_DECISION |
| plan giriş/stop/hedef (`plan_*`) | coin head planı — piyasaya KESİN bağlı ve yalnız geçerliyse (`chart_analysis.plan_for_market`; motor ve panel aynı kural) | ana bot |
| gerçek giriş/çıkış/stop/hedef/LIQ (`trade_entry`, `trade_exit`, `entry`, `stop`, `target`, `no_target`, `liq`) | seçili defterin ledger'ı (id = `book_id:trade_id`); ana bot SPOT kaydında `spot_ledger.json` | defter gerçeği |
| güncel fiyat + açık K/Z (`mark`) | `chart_analysis.mark_element` — TEK formül: BRÜT K/Z = yön × (fiyat − ortalama giriş) × miktar (ücret/fonlama yok). `price_source.kind`: motor kaydında `ticker_last` (tur anındaki canlı tik); panelin "şimdi" görünümünde `candle_close` = mum dosyasındaki SON mumun kapanışı (çoğu zaman KAPANMAMIŞ bar; `file`, `bar_open_ms`, `bar_closed`) — doğrulanmış borsa mark fiyatı DEĞİLDİR ve etiket bunu söyler. "Şimdi" görünümünün her dönüş yolunda (panel-ephemeral, panel-cache, eşleşen/eşleşmeyen motor kaydı, her dilim) bu öğe güncel fiyattan KOPYA üzerinde yeniden kurulur; tarihsel kayıt kendi fiyatını korur (2026-09-16 #2) | defter gerçeği (gösterim) |

Zaman dilimleri TEK kaynaktır (`tradingbot/timeframes.py`: 15m, 1h, 4h, 1d, 1w). Mum kapısı (`candle_confirmation.closed_bars`),
analiz (`closed_bars_at`), panel ve motor aynı tabloyu okur; bilinmeyen dilim sessizce 4h sayılmaz (panel HTTP 400,
kütüphane ValueError). Kapanmış bar: `timestamp + tf_ms <= now` (eşitlik dahil).

Ana botun mum ENFORCE / grafik SHADOW / rejim ENFORCE durumları config'ten (`chart_analysis/config.json`
motor tarafından yazılır); karar kaydındaki hüküm (`risk.json.last_decisions`) açıklamada "KARARI ETKİLEDİ /
yalnız gözlem" olarak gösterilir. ZigZag numaraları dalga analizi olarak SUNULMAZ (panelde yok).

## 3. Saklama ve geçmiş

* Motor turu (`engine_v3._chart_analysis_tour`, `risk.json` yazıldıktan SONRA) her sembol × defter için analiz
  anını `state/chart_analysis/<book>/<market_type>/<SEMBOL>_<tf>/<as_of>_<analysis_id>.json` olarak yazar;
  `index.json` (şema `chart_analysis_index_v2`) seri (`book|market|symbol|tf`) başına listeyi tutar; satırda
  `market_type` vardır. **Aynı analysis_id ikinci kez yazılmaz; var olan dosya hiçbir koşulda yeniden yazılmaz**
  (sonraki mumlar geçmişi değiştiremez). Yeni kayıt yalnız yeni kapanmış bar ya da parmak izi değişiminde oluşur
  (pozisyon açılış/kapanış/kısmi kapanış, stop/hedef değişimi, kapanan işlem, plan, hüküm, kural koşulu/rejim).
* Motor kaydının piyasa doğrulaması (2026-09-16 #3): çerçeve piyasası YALNIZ `_frame_provenance`'tan okunur; provenans
  yoksa kaynak kanıtsız USDM_PERP sayılmaz. Kâğıt defter (T2/M2) kaydı yalnız doğrulanmış USDM_PERP çerçeveyle yazılır;
  çerçeve SPOT ikamesiyse (perpetual mum alınamadı) spot mumlar futures diye YENİDEN ETİKETLENMEZ ve kayıt yazılmaz — neden
  `chart_analysis/config.json` içinde `skipped["book|symbol|tf"] = {status: MARKET_MISMATCH | NO_PROVENANCE, bar_market,
  book_market, reason, at}` olarak bildirilir; panel bunu `engine_record.status` ile "MOTOR KAYDI YOK: piyasa uyuşmazlığı"
  diye gösterir. Ana bot kaydı çerçeve piyasasında (SPOT/USDM_PERP) yazılır; `source.daily_market` (sembolün günlük çerçevesi
  aynı sağlayıcıdan) ve `source.btc_market` / `btc_market_ok` (BTC rejim çerçevesinin piyasası; kâğıt defterde USDM_PERP
  değilse işaret) kayda girer. Bu kural İŞLEM yolunu (defter.step/tick) değiştirmez.
* Uyumluluk (2e31926'nın v1 indeksi: `book|symbol|tf`, satırda piyasa yok): satır dosyasındaki
  `identity.market_type` ile serisine taşınır; piyasası okunamayan satır `?` piyasası altında kalır ve hiçbir piyasa
  isteğinde listelenmez/son kayıt sayılmaz (kimlikle `load` döner; panel kimliği isteğin piyasasıyla doğrular).
  Kayıt dosyaları geriye dönük değiştirilmez; yalnız indeks ilk yazımda v2'ye çevrilir. Üretimde (VPS) bu paket
  daha önce dağıtılmadığı için v1 indeks beklenmiyor.
* Boyut: bir kayıt ~15–125 KB (bar sayısına ve formasyon/dayanak listelerine göre); saklama sınırı
  `chart_analysis.keep_per_series` (varsayılan 300, seri başına en eski silinir). Stop değişimi de kayda değer
  olduğu için trailing stop kullanan bir defterde tur başına bir kayıt çıkabilir (T2/M2 sabit stop kullanır);
  mevcut yedekleme `state/` altını kapsadığı için otomatik yedeğe girer.
* Panel: `/api/chart/{base}?tf&market&book&n&analysis_id&req` — **sözleşme**:
  * `analysis_id` verildi (**geçmiş**): saklanan kayıt AYNEN döner (`historical: true`, `analysis_stored: true`);
    sembol/piyasa/dilim/defter kimliği doğrulanır (uyuşmazsa 404, mesajda kayıt ve istek kimliği). Mumlar analiz
    anına göre seçilir: `timestamp <= last_closed_bar` süzgeci **son n bar + gösterge ısınmasından ÖNCE** uygulanır
    (bulgu #6); analiz anından sonraki mum görünmez; tarih arşivde gerçekten yoksa 404 + arşiv aralığı. İşlem katmanı
    o anki defter kaydıdır (canlı katman YOK).
  * `analysis_id` yok (**şimdi**): panel güncel kapanmış bar + güncel defter/karar/plan + config.json'daki kod/config ile
    analiz kimliğini hesaplar (bellek önbelleği; anahtar: defter/karar dosyalarının sürüm imzasını içerir). Motorun son
    kaydı **tam aynı kimlikteyse** o kayıt gösterilir (`analysis_origin: store`, `analysis_stored: true`; işaret fiyatı
    ve açık K/Z canlı değerle KOPYA üzerinde güncellenir, dosya değişmez); değilse panelin geçici hesabı gösterilir
    (`panel-ephemeral` / `panel-cache`, `analysis_stored: false`) ve `engine_record` motorun son kaydını + farkını
    (bar / kod / config / defter-karar) bildirir. Eski bir kayıt hiçbir koşulda "şimdi" gibi gösterilmez.
  * `live`: canlı katmanın zamanı ve kaynağı (`as_of`, `source` = GERÇEKTEN kullanılan ledger dosyası — ana bot spot için
    `spot_ledger.json` okunabiliyorsa o, değilse `portfolio.json`; `position`, `plan`, `mark_price`, `mark_price_source`).
    Panel önbelleği canlı dosyaların (futures_ledger, spot_ledger, portfolio, risk, coin_heads, agents, trade_memory,
    strategy_paper, defter ledger'ları, chart_analysis config/index) sürüm imzasıyla anahtarlanır; SSE `spot_ledger` olayı da
    "şimdi" görünümünü yeniler (2026-09-16 #1).
  * `req` her iki uçta (grafik ve `/history`) yankılanır; geçmiş listesi `/api/chart/{base}/history?tf&market&book&req`
    PİYASA-kesindir; `/api/chart/{base}/snapshot/{id}` JSON indirme (dosya adı `SEMBOL_dilim_piyasa_defter_id.json`).
    Panel hiçbir state dosyasına yazmaz, borsa verisi indirmez.

## 4. Kullanıcı ekranı

`/coin/{BASE}?market=&book=&tf=`: piyasa, **defter (Ana bot / T2 / M2)**, TF (15m/1h/4h/1d/1w — API ile aynı liste),
bar sayısı, **geçmiş analiz** seçimi; katman aç/kapat (Seviyeler, Bölgeler, Trend, Formasyonlar, İşlemler, Göstergeler);
kaynak satırı: veri kaynağı / son bar / yaş / **BAYAT VERİ**; **canlı defter** zamanı ve dosyası; analiz kimliği ·
analiz anı · **motor kaydı / panel hesabı (kaydedilmedi)** · kod · defter · piyasa dilim; **GEÇMİŞ ANALİZ** uyarısı;
motorun son kaydı şimdiki durumdan farklıysa farkı; açıklama paneli; tıklanan öğenin detayı (dayanak, teyit = bar
kapanışı, kırılış barı açılış → kapanışta bilindi, eğim/temas/ihlal, gerekçe, geçersizleşme, kaynak, canlı işaret).

İstek sözleşmesi (`tradingbot/dashboard/chart_js.py`, bulgu #4): kapsam (dilim/piyasa/defter/bar/geçmiş) DOM'dan **bir
kez** okunur ve istekle taşınır; dilim/piyasa/defter değişince geçmiş seçimi temizlenir (eski defterin analysis_id'si
yeni kapsama gönderilmez), liste ve grafik isteği aynı yeni kapsamla gider; yanıtlar `req` sırası + yanıtın
taşıdığı kapsam (tf/market/book) + analiz kimliği (identity.timeframe/book_id/market_type, analysis_id) ile
eşleştirilir — geç gelen ya da başka kapsama ait yanıt seçenekleri/grafiği EZEMEZ. Yenile, seçili geçmişi korur.
İndirme adı: `BASE_tf_piyasa_defter_<analysis_id>` (kayıtlı/geçmiş) ya da `..._canli-<an>` (kaydedilmemiş panel
hesabı); JSON kayıtlıysa sunucudan, değilse tarayıcı blob'u.

T2: günlük kapanış vs EMA200, BTC günlük rejimi, gerçek giriş, mevcut stop, **TP yok**, çıkış kuralı.
M2: günlük kapanış vs 28 gün önceki referans (değer + tarih), rejim, giriş, stop, TP yok. Açıklama "BTC rejimi
DOWN'a dönünce açık pozisyon OTOMATİK KAPANMAZ" der (kod böyle bir kural uygulamıyor).

## 5. Sınırlar (dürüst)

* Panel içi geçici hesapta ana botun kapı hükümleri `risk.json`daki son kayıttan okunur; motor kaydı yoksa
  plan/karar "kayıt yok" gösterilir.
* Trend çizgisi ve bölge hesabı hiçbir kapı tarafından okunmaz; gösterimde OBSERVATION_ONLY olarak işaretlidir.
* Spot piyasa için kâğıt defterler yoktur (yalnız USDM_PERP). Ana botun spot pozisyon/geçmişi `spot_ledger.json`dan
  okunur (V3 motorunun spot defteri); dosya yoksa eski `portfolio.json`.
* Panelin geçici hesabı CSV mumlarından, motor kaydı runner çerçevelerinden kurulur; gösterge sütunları
  (ema20/50/200) yalnız motor kaydında olabilir. Kimlik eşleşmesi bar/karar/kod/config üzerinden olduğu için ikisi
  aynı analiz anını gösterir; küçük gösterge farkları kimliği değiştirmez.
* Ekran kanıtları sentetik veriyle üretildi ve dosya adıyla etiketlidir (`docs/review/screens/synthetic-*`);
  2026-09-15 kanıtları gerçek tarayıcıdan (Plotly dışa aktarımı) alındı, veri yine sentetik (test motoru).

## 6. Testler

`tests/test_chart_analysis_v1.py` (13) + `tests/test_chart_analysis_v1_fixes.py` (14; altı bulgu, node altında gerçek
`CHART_JS` davranışı, motor kaydı ↔ panel kimlik paritesi). CI: `.github/workflows/chart-analysis.yml`.

## 7. 2026-09-15 onarımları (bağımsız inceleme bulguları, 2e31926 üzerine)

| # | bulgu (2e31926) | onarım | test |
|---|---|---|---|
| 1 | 15m/1h/1w isteği `closed_bars` KeyError (500) | `timeframes.py` tek kaynak; bilinmeyen dilim 400 | `test_f1_*` |
| 2 | kayıtta spot/futures ayrımı yok (seri anahtarı, latest, history, analysis_id doğrulaması) | seri `book|market|symbol|tf`, v2 indeks + v1 uyumluluk politikası, API kimlik doğrulaması, motor kaydı defter piyasasında | `test_f2_*`, `test_engine_records_*` |
| 3 | aynı mum içinde defter değişimi görünmüyor (önbellek anahtarı, "latest = şimdi"); miktar/hedef/kapanış parmak izinde yok | canlı sürüm imzalı önbellek; "şimdi" = kimlik eşitliği sözleşmesi + `engine_record`/`live`; parmak izi genişletildi ve normalize edildi | `test_f3a_*`, `test_f3b_*` |
| 4 | geçmişten bakarken kapsam değişimi yanlış istek (eski analysis_id, eski defter listesi); history'de geç yanıt koruması yok | kapsam bir kez okunur, geçmiş sıfırlanır, `req` + kapsam + kimlik eşleştirmesi iki uçta | `test_f4_*` (node) |
| 5 | pivot/formasyon teyit zamanı bar açılışı (bir mum erken) | `confirmed_at`/`known_at` = bar kapanışı; dedektör alanları değişmedi | `test_f5_*` |
| 6 | arşivde olan eski mumlar için "veri yok" | `CandleSource.load(end_ts=)`; analiz anına göre seçim; dürüst 404 | `test_f6_*` |
| K | JS `identity.analysis_id` okuyordu (kökte) | kök alan; indirme adı kimlik/piyasa/dilim/defter | `test_f4_*`, `test_chart_js_reads_*` |
| 7 (2026-09-16) | spot defteri (`spot_ledger.json`) değişimi panel önbelleğini/SSE yenilemesini tetiklemiyor, `live.source` yanlış | `_LIVE_FILES` + `__onState` spot_ledger; `state.spot_source()` gerçek kaynak | `test_f7_*` |
| 8 (2026-09-16) | geçici analizde fiyat/K-Z donuyor (yalnız eşleşen motor kaydında güncelleniyordu) | `mark_element` tek formül; her "şimdi" yolunda kopya üzerinde canlı fiyat; kaynak etiketi (mum kapanışı ≠ borsa mark) | `test_f8_*` |
| 9 (2026-09-16) | T2/M2 kaydı SPOT çerçeveyi USDM_PERP kimliğiyle yazıyordu; provenans yoksa USDM_PERP varsayılıyordu | kâğıt defter kaydı yalnız doğrulanmış USDM_PERP çerçeveyle; aksi hâlde `config.json.skipped` + panel durumu | `test_f9_*` |
