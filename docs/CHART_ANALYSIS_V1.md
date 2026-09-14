# CHART ANALYSIS V1 — botun gerçek hesaplarını ve karar gerekçelerini grafikte görünür kılma

Amaç: kullanıcı bir çizgiye baktığında **neden orada olduğunu** (dayanak pivotlar, teyit zamanı, gerekçe) ve
**işlem kararını etkileyip etkilemediğini** görsün. Bu katman salt gösterimdir: hiçbir giriş/çıkış kapısı,
defter, sermaye, risk bütçesi, evren ya da öğrenme durumu değişmez (bkz. `tests/test_chart_analysis_v1.py::
test_engine_decisions_unchanged_and_snapshots_deduped`).

## 1. Düzeltilen grafik kusurları

| kusur | dosya | düzeltme | test |
|---|---|---|---|
| futures isteği spot dosyasına düşüyordu; parquet aramasında sembol/piyasa/dilim gevşekti | `tradingbot/dashboard/candles.py` `CandleSource.candidates/find/_parquet_matches` | piyasaya göre izinli önekler; parquet'te sembol/dilim/piyasa KESİN eşleşme; eksik veri `missing=True` ile açıkça eksik döner, başka piyasa/dilimle doldurulmaz | `test_candle_source_is_market_strict` |
| `api_candles` pozisyonu market'ten bağımsız futures defterinden, planı diğer piyasadan alıyordu | `tradingbot/dashboard/app.py` `_plan_for_market`, `_position_for_market` | spot yanıtı futures giriş/stop/plan taşımaz; futures yanıtı futures defteri; kâğıt defterler yalnız futures | `test_api_candles_and_chart_do_not_leak_futures_into_spot`, `tests/test_dashboard.py` |
| `render_signal_chart` hedefsiz açık LONG için "BEKLE" yazıp işlem katmanını çizmiyordu | `tradingbot/charts.py` | giriş+stop TP'den bağımsız çizilir; hedef yoksa "TP YOK" yazılır; hedef kutuları yalnız hedef varsa | `test_render_signal_chart_draws_targetless_position` |

## 2. Ortak analiz çıktısı (şema `chart_analysis_v1`)

`tradingbot/chart_analysis.py::build_snapshot` → tek analiz anı:

* **kimlik**: `exchange`, `market_type` (USDM_PERP/SPOT), `symbol`, `timeframe`, `book_id` (`main` |
  `strategy_paper` | `strategy_paper_m2`), `book_name`, `as_of`, `last_closed_bar` (ts, iso, close), `code_sha`
  (`TRADINGBOT_CODE_SHA` ya da `git rev-parse HEAD`), `config_hash` (entry_selectivity + strategy_paper +
  entry_universe + chart_analysis bölümlerinin özeti); `analysis_id` = sha256(kimlik + son kapanmış bar +
  kod + config + karar parmak izi)[:16].
* **öğeler** (`elements[]`): `kind`, `layer`, `label_tr`, `price` / `lower`–`upper`, `t0`/`t1` (+ eğik çizgide
  `y0`/`y1`), `anchors[]` (dayanak: zaman, fiyat, taraf, rol, teyit zamanı), `confirmed_at`,
  `invalidation_tr`, `rationale_tr`, `decision_impact` ∈ {USED_IN_DECISION, OBSERVATION_ONLY, UNCONFIRMED,
  INVALID}, `source` (modül/fonksiyon/parametre), `status` (trend: INTACT/BROKEN).
* **kural durumu** (`rule_state`, T2/M2): `ema200_trend.rule_state` — `decide` ile aynı okuma; close, signal_ts,
  ema200, atr14, ref_close/ref_ts (M2), above, regime, stop_if_open.
* **giriş anı** (`entry_features`): defter belleğinden (giriş anındaki close/EMA200/ATR14/stop, M2 için
  referans kapanış ve tarihi) — ŞU ANKİ analiz değerinden ayrı alan.
* **açıklama** (`explanation[]`): yapılandırılmış ölçümlerden Türkçe satırlar; LLM yok; hesaplanmamış
  olasılık/hedef yok; formasyon yoksa "bulunamadı".

Hangi çizgi hangi hesaptan gelir:

| katman | kaynak (aynı fonksiyon, ikinci formül yok) | kararı etkiler mi |
|---|---|---|
| teyitli pivot (`pivot_high/low`) | `learn.multitimeframe_context.confirmed_swings` (formasyon dedektörünün pivotları; `confirmed_at` = i+lookback barı) | hayır (OBSERVATION_ONLY); son teyitsiz uç `pivot_pending` = UNCONFIRMED |
| destek/direnç/fiyat bölgesi (`zone`) | `equal_level_clusters` (ATR toleransı, ≥2 dayanak); alt/üst = üyelerin gerçek min/max | hayır |
| trend çizgisi (`trend_support/resistance`) | son iki teyitli dip/tepe; eğim, ek temas, kapanış ihlali hesaplanır | hayır; ihlal → INVALID |
| formasyon çizgileri (`pattern_line`) | `chart_patterns.detect_chart_patterns` kaydındaki `anchors` + `geometry` (boyun, düz/eğik sınır, direk, kırılış barı) | config'e göre: chart kapısı ENFORCE+taze → USED_IN_DECISION; SHADOW → OBSERVATION_ONLY (üretimde SHADOW) |
| günlük EMA200 / 28g referans (`rule_reference`) | `ema200_trend.rule_state` | T2/M2 için USED_IN_DECISION |
| plan giriş/stop/hedef (`plan_*`) | coin head planı (piyasaya kesin bağlı) | ana bot |
| gerçek giriş/çıkış/stop/hedef/LIQ/işaret (`trade_entry`, `trade_exit`, `entry`, `stop`, `target`, `no_target`, `liq`, `mark`) | seçili defterin `futures_ledger.json` (id = `book_id:trade_id`) | defter gerçeği |

Ana botun mum ENFORCE / grafik SHADOW / rejim ENFORCE durumları config'ten (`chart_analysis/config.json`
motor tarafından yazılır); karar kaydındaki hüküm (`risk.json.last_decisions`) açıklamada "KARARI ETKİLEDİ /
yalnız gözlem" olarak gösterilir. ZigZag numaraları dalga analizi olarak SUNULMAZ (panelde yok).

## 3. Saklama ve geçmiş

* Motor turu (`engine_v3._chart_analysis_tour`, `risk.json` yazıldıktan SONRA) her sembol × defter için analiz
  anını `state/chart_analysis/<book>/<SEMBOL>_<tf>/<as_of>_<analysis_id>.json` olarak yazar; `index.json`
  seri başına listeyi tutar. **Aynı analysis_id ikinci kez yazılmaz; var olan dosya hiçbir koşulda yeniden
  yazılmaz** (sonraki mumlar geçmişi değiştiremez). Yeni kayıt yalnız yeni kapanmış bar ya da karar
  değişiminde oluşur (parmak izi: kural koşulu/rejim, karar hükmü/engel kodu, plan giriş/stop, pozisyon).
* Boyut: bir kayıt ~10–25 KB; 15 dk turda 4h barla günde ~6 kayıt/seri; `chart_analysis.keep_per_series`
  (varsayılan 300 ≈ 50 gün) üstünde en eski silinir. On coin × 3 defter ≈ 30 seri × 300 × ~15 KB ≈ 135 MB üst
  sınır; mevcut yedekleme `state/` altını kapsadığı için otomatik yedeğe girer.
* Panel: `/api/chart/{base}?tf&market&book&n&analysis_id&req` — `analysis_id` verilirse saklanan kayıt ve
  mumlar o anın son kapanmış barına kadar KESİLİR (`historical: true`); verilmezse motorun son kaydı (son
  kapanmış barla aynıysa) ya da panel içi geçici hesap (yazılmaz; bellek önbelleği). Panel hiçbir state
  dosyasına yazmaz, borsa verisi indirmez. `/api/chart/{base}/history` liste, `/api/chart/{base}/snapshot/{id}`
  JSON indirme; PNG indirme istemci tarafında (`Plotly.downloadImage`).

## 4. Kullanıcı ekranı

`/coin/{BASE}?market=&book=&tf=`: piyasa, **defter (Ana bot / T2 / M2)**, TF, bar sayısı, **geçmiş analiz**
seçimi; katman aç/kapat (Seviyeler, Bölgeler, Trend, Formasyonlar, İşlemler, Göstergeler); veri kaynağı /
son bar / yaş / **BAYAT VERİ** uyarısı; **GEÇMİŞ ANALİZ** uyarısı; açıklama paneli; tıklanan öğenin detayı
(dayanak, teyit, eğim/temas/ihlal, gerekçe, geçersizleşme, kaynak). Geç gelen istek: her istek `req`
sayacı taşır, yanıt yankılar; eski yanıt yeni seçimi ezmez. Mobilde (≤600px) tek sütun, 10–13 px yazı.

T2: günlük kapanış vs EMA200, BTC günlük rejimi, gerçek giriş, mevcut stop, **TP yok**, çıkış kuralı.
M2: günlük kapanış vs 28 gün önceki referans (değer + tarih), rejim, giriş, stop, TP yok. Açıklama "BTC rejimi
DOWN'a dönünce açık pozisyon OTOMATİK KAPANMAZ" der (kod böyle bir kural uygulamıyor).

## 5. Sınırlar (dürüst)

* Panel içi geçici hesapta ana botun kapı hükümleri `risk.json`daki son kayıttan okunur; motor kaydı yoksa
  plan/karar "kayıt yok" gösterilir.
* Trend çizgisi ve bölge hesabı hiçbir kapı tarafından okunmaz; gösterimde OBSERVATION_ONLY olarak işaretlidir.
* Spot piyasa için kâğıt defterler yoktur (yalnız USDM_PERP).
* Ekran kanıtları sentetik veriyle üretildi ve dosya adı/afişle etiketlidir (`docs/review/screens/synthetic-*`).
