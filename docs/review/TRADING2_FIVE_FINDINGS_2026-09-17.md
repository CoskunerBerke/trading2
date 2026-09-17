# TRADING2 — BEŞ BULGUNUN SINIRLI DÜZELTMESİ (kapanış, 2026-09-17)

Dal `work/chart-analysis-v1`. İnceleme tabanı **60746c32bf31dbadea81e808bc1d3cba6e3399cc** (dal ucu a9b9a15; ikisi
arasındaki tek fark belgedir — dal bu oturumda İLERLEMEMİŞTİ, eski SHA'ya dönülmedi).

**Test edilen kod SHA'sı: `d26d2a4666797c1fd2f198d272d1b09ad2c424b1`** (4 commit: 6a36596 beş bulgu ·
61a643e funding ön ısınması · c5071ee CI · d26d2a4 kapsama sayacı + kurgu onarımı).
CI (bu kod SHA'sında, yeni paketler dâhil): https://github.com/CoskunerBerke/trading2/actions/runs/35209877258 — **success**.
Tam paket (aynı SHA, değişiklikler dondurulduktan sonra, tek koşu): **2491 passed, 22 skipped, 0 failed** (38dk44sn).

**VPS'e dağıtım YAPILMADI. VPS HEAD'i bu oturumda OKUNMADI → doğrulanmadı.** Gerçek para açılmadı; mevcut ileri
testler, defterler, bakiyeler ve sayaçlar sıfırlanmadı.

Korunan: T2/M2 kural formülleri, on coin giriş evreni, sermaye/risk ayarları, stop geometrisi, PAPER modu, ana botun
defteri ve kararları, kapı modları (`regime_gate`/`candle_confirmation` ENFORCE, `chart_confirmation` SHADOW),
15m/1h/4h analiz zinciri, formasyon trader'ının yeni listeleme önceliği ve sembol sayısı sınırsızlığı. Formasyon
trader'ına on coin sınırı **geri getirilmedi**; hiçbir risk tavanı gevşetilmedi.

---

## 1. Beş bulgu: yeniden üretim → düzeltme → kanıt

| # | Bulgu | ÖNCE (ölçülen, gerçek kod yolunda) | DÜZELTME | SONRA (kanıt) |
|---|---|---|---|---|
| 1 | Sert fitiller stop kontrolünden kalıcı düşüyor | LONG 100/stop 95, mark 101, bar (h=103, l=70, c=101) → olay `BAR_OUT_OF_RANGE`, imleç barın açılışında, aynı bar yeniden beslendiğinde **sessiz no-op**, pozisyon AÇIK | `sane` üç ayrı hükme bölündü: bütünlük (`BAR_CORRUPT`, uç büyüklüğü dahil), ölçek (`BAR_SCALE_UNVERIFIED`, **yalnız barın KAPANIŞIYLA**), piyasa kimliği (`BAR_MARKET_MISMATCH`). Uygulanamayan bar `meta.ohlc_gaps`'e yazılır, **yeniden denenebilir** ve **sonraki geçerli barı engellemez** | `test_trading2_fix_bar_range_v1` (17) — fitil stop'u tetikliyor, `exit_price <= 95` |
| 2 | Yeni coin varsayılan emir kurallarıyla açılıyor | kaynak `default`, `price_tick` 0,01, `qty_step` 0,001 → fill 0,13 (mark 0,12345'ten %5,3 sapma), miktar resmî `step_size=1` ile olanaksız | Keşiften girişe **doğrulanmış resmî filtre** bağı (`from_universe_entry`); eksik/geçersiz/**tarihsiz**/bayat → giriş YOK, gerekçe kayıtta; yuvarlama sonrası **R/R ve risk** yeniden denetlenir | `test_trading2_fix_official_filters_v1` (11) — fill 0,12349, miktar 225 (tam sayı lot) |
| 3 | Funding hiç sorulmuyor | 07:59→08:01 arasında 08:00 settlement'ı var; watermark **07:59'da çakılı**, `funding_paid` 0,0, kayıt `funding=0.0` — "ölçüldü ve sıfır" ile "hiç sorulmadı" ayırt edilemiyor | `FundingRates` (yalnız **gerçekleşmiş** `/fapi/v1/fundingRate` satırları) tarama + bar + çıkış izleyicisine bağlandı; sözleşme aralığı `fundingInfo`'dan, **alınamazsa 8 saat varsayılmaz**; kapsama kayda ve rapora yazılır | `test_trading2_fix_pattern_funding_v1` (17) — gerçekleşen %1 uygulanıyor, canlı tahmin %5 **uygulanmıyor** |
| 4 | Süresi dolmuş tetiklenmiş plan açılıyor | fiyat boşluğunda bekleyen, süresi dolmuş plan fiyat gelince `OPENED/MANAGED` | `is_expired` **tek tanım** (`>` , sınır anı geçerli), `_try_open`ın **en başında**, **taze** karar anıyla; okunamayan alan **fail-closed**; `PL_EXPIRED` terminal ve terminal plan yeniden ele alınmaz | `test_trading2_fix_plan_expiry_v1` (12) — açılmıyor, yeniden başlatmada dirilmiyor |
| 5 | Panelde defter/piyasa/işlem kimliği kayboluyor | (a) formasyon planı main/spot ve M2/futures ekranlarında görünüyor; (b) `account_snapshot` piyasa almıyor; (c) `/coin/SYM` bağlantıları kimliksiz; (d) yenileme `planhost`'u atlıyor; (e) boş ama geçerli defter, bayat özetten pozisyon diriltiyor | Uçtan uca `book + market + symbol + trade_id/as_of`; desteklenmeyen birleşim **açıkça** söylenir; geçmiş **piyasaya göre** ayrılır; brüt açık K/Z **net diye etiketlenmez**; gerçekleşen **tam geçmişten**; boş defter ≠ eksik dosya | `test_trading2_fix_panel_identity_v1` (19) + **gerçek tarayıcı** (aşağıda) |

Yeniden üretimlerin tamamı **gerçek uygulama yolunda** yapıldı (gerçek `PatternBook`/`PatternScanner`/
`FuturesLedgerV2`/`RiskEngine`/`apply_action`/`StateReader`/FastAPI); ağ yerine deterministik `MockProvider`
kullanıldı. AST ile ayrılmış fonksiyon denemesi **tam uygulama testi diye sunulmadı** — `test_trading2_fix_contracts_v1`
açıkça "sözleşme kapısı"dır ve davranış kanıtı yerine geçmez.

## 2. Bağımsız (karşıt) doğrulama — ve onun bulduğu gerileme

Kendi onarımımın tek onaylayanı olmamak için 8 ajanlı adversaryal tur koşuldu. **Sekizi de DEFECTIVE dedi**;
41 bulgu (5 kritik, 19 majör, 17 minör). En önemlisi benim ürettiğim bir gerilemeydi:

> **KRİTİK (4 ajan bağımsız buldu):** ilk onarımda uygulanamayan bar `break` ile **zinciri durduruyordu**. Ölçülen:
> 48 barlık pencerede bozuk/ölçek dışı bir bar yüzünden, **sonraki geçerli barın içindeki koruyucu stop 45 saat
> boyunca uygulanmadı**; canlı fiyat bu arada hedefe ulaşınca gerçek bir **−50,98 zarar işlemi +98,95 kâr olarak**
> kaydedildi. Ayrıca o aşamadaki testler `break` ile `continue` arasındaki farkı **hiç görmüyordu**.

Onarıldı (`continue` + boşluk kaydı + yeniden deneme) ve **geri alma sondasıyla** doğrulandı: `break`e döndürülünce
3 test, uç büyüklük sınırı kaldırılınca 2 test düşüyor. Diğer başlıca bulgular ve karşılıkları:

| Bulgu | Karşılık |
|---|---|
| **KRİTİK** `_resolve_filters` paylaşılan `FiltersCache`e yazıyor; `put()` önbellek geneli `verified_at`i yeniliyor → **ana botun** resmî `exchangeInfo` yenilemesi "taze" sanılıp atlanıyor (kilitsiz nesneye tarayıcı iş parçacığından yazma riski de var) | Paylaşılan önbelleğe **yazılmıyor**; defterin kendi `_filters_memo` belleği |
| **MAJÖR** Ölçek hükmü kapanışa taşınınca uçlar **bağsız** kaldı: uydurma high/low uygulanabilir | Uçlar barın kendi kapanışına göre sınırlı (`0,2×…5×`) |
| **MAJÖR** `DataService.bars` `market`i **sabit** `USDM_PERP` yazıyordu → piyasa kapısı anlamsız (`MarketFeed` SPOT sağlayıcıya düşebiliyor) | Kimlik sonucun **kendi provenansından**; giriş yolunda da denetleniyor (`DATA_MARKET_*`) |
| **MAJÖR** Önbellek dalında yaş denetimi yok; tarihsiz kayıt "bayat değil" sayılıyor | Her iki kaynakta yaş denetimi; tarihsiz kayıt `FILTERS_UNDATED` → RET |
| **MAJÖR** `is_expired` istisnayı yutup `False` dönüyordu → bozuk `plans.json` kaydına süresiz emir hakkı | Fail-closed (okunamayan alan = geçersiz) |
| **MAJÖR** `fundingInfo` alınamayınca 8 saat sessizce varsayılıyor, kapsama yanlışlıkla TAM görünüyor | `_intervals_known`; doğrulanmadan varsayılan yayılmaz |
| **MAJÖR** Kapsama iki aralığı çıkararak türetiliyordu (atlanan settlement görünmez) + kapanış yolunda kilit altında HTTP | Gerçekten uygulanan settlement anları kayda geçer; kapsama **ağa çıkmaz** |
| **MAJÖR** `MockProvider.funding_history` o anın tahminini geçmişe damgalıyordu → **baş iddia hiçbir yerde sınanmıyordu** | Varsayılan dal artık geçmişe damgalamıyor; gerçekleşen oran (%1) ≠ canlı tahmin (%5) kurgusu |
| **MAJÖR** `/coin` varsayılanı `spot`tu; pozisyon bölümü defter/piyasaya bağlanınca açık futures pozisyonu kayboluyordu | Varsayılan `futures`; gerçekten spot olan bağlantı açıkça `market=spot` |
| **MAJÖR** Ana bot geçmişi futures+spot **karışık**; spot kapanışı futures kaydı gibi etiketleniyordu | `_book_history`/`book_trade` piyasaya göre ayrık |
| **MİNÖR→gerçek** `_try_open` terminal planı yeniden işleyip `CANCELLED`→`EXPIRED` yapıyor, iki sayaca birden giriyordu | Terminal plan dokunulmadan bırakılır |
| **MİNÖR→gerçek** Yuvarlama sonrası R/R'de giriş kayması **iki kez** sayılıyordu | `cost_fraction_at_fill` (2×taker + 1×kayma) |
| **MİNÖR→gerçek** Toplam testi 40 satırla kuruluydu (görünen sınır 2000) — onarım geri alınsa da geçiyordu | 2500 satır |
| **MİNÖR→gerçek** `live_refresh_js`e defter/piyasa **kaçışsız** gömülüyordu | `_js()` ile JSON kaçışı + saldırgan değer testi |

**Kapsanmayan (kapsam dışı, kayda geçiriliyor):** `C_COMPRESSION_BREAKOUT` planlarının `anchor_ts` üzerinden
yenilenmesi (süresi dolan kurulumun sayısal olarak aynı ardılı üretilmesi) ve `plans.json`ın budanmaması —
raporun `plans_per_1k_bars` paydasını şişirir. Bu, beş bulgunun hiçbiri değil, **ayrı** bir plan-kimliği tasarım
sorunudur. Ana botun T2/M2 turunda `static_rates` hâlâ o anın tahminini geçmiş settlement'lara uygulayabiliyor
(`engine_v3`); formasyon defteri için düzeltildi, ana bot için **düzeltilmedi** — ayrı bir değişiklik turudur.

## 3. Ürün zinciri korundu

`universe.discover` (yeni listeleme önceliği) → `DataService` 15m/1h/4h kapalı mum → `detect` → koşullu plan →
kapanış teyitli tetik → **süre** + **resmî filtre** + risk (`RiskEngine`) → **ayrı** PAPER defteri (`apply_action`,
`FuturesLedgerV2`) → yönetim (bar uçları + canlı mark + zaman stopu) → kapanış (funding kapsamasıyla) → rapor.
Yeni strateji ailesi, yeni parametre araması ve optimizasyon ızgarası **açılmadı**.

## 4. Gerçek sağlayıcı kontrolü (salt okunur)

`scripts/pattern_provider_smoke.py` — kimlik bilgisi YOK, imza YOK, emir YOK; 9 public uç: `exchangeInfo`
(metadata/filtre), `klines` 15m/1h/4h, `premiumIndex` (güncel perp mark), `depth` + `bookTicker` (likidite),
`fundingRate` (gerçekleşmiş settlement) ve `fundingInfo` (sözleşme aralığı).

**SONUÇ: erişilen 0 / erişilemeyen 9.** Bu bir **erişim engelidir, kod hatası değildir** ve sahte veriyle
örtülmemiştir:

* `fapi.binance.com`, `api.binance.com`, `data-api.binance.vision` → TCP bağlanıyor, TLS/HTTP **sıfırlanıyor**
  (`WinError 10054`, 4 denemede).
* Kontrol hostları aynı makineden **200** dönüyor (`pypi.org`, `example.com`) → genel ağ çalışıyor.
* Tarayıcı paneli de aynı alan adlarını açamadı (navigasyon reddedildi).

Betik erişimi olan bir makinede (örn. VPS) çalıştırılabilir: `python scripts/pattern_provider_smoke.py --json out.json`
(çıkış kodu 0 hepsi erişildi · 2 BLOCKED · 3 kısmi). **Bu kontrol fiyat geleceği ya da kârlılık kanıtı değildir.**

## 5. Testler

* Beş bulgunun kendi paketleri + sözleşme kapıları: **94 test** (17 bar · 11 filtre · 17 funding · 12 süre · 19 panel · 18 sözleşme kapısı).
* Ortak muhasebe/T2/M2/panel gerilemeleri: `test_accounting`, `test_gap_reconcile`, `test_replay_funding`,
  `test_strategy_paper_*`, `test_dashboard*`, `test_chart_analysis_v1*` — hepsi geçiyor.
* **Gerçek tarayıcı doğrulaması** (gerçek FastAPI + sentetik state; ekran SENTETİKTİR, VPS görüntüsü değildir):
  T2 defteri seçiliyken pozisyon sunucu tarafında kapatıldı → plan kutusundaki «Açık işlem» satırı **ekrandan
  kalktı**, kartlar «AÇIK İŞLEM 0 / KAPANAN İŞLEM 2»ye döndü, konsolda hata yok. T2+Spot seçiminde kartlar, plan
  kutusu, pozisyon listesi ve kapanışların **dördü de** "bu kapsamda kayıt yok" dedi; başka hesabın sayısı sızmadı.
  main+Spot seçiminde SPOT kaydı (giriş 101,11) ve spot özkaynağı okundu.
* Tam paket **değişiklikler donduktan sonra** koşuldu. İlk koşuda 8 kırmızı test çıktı; **hiçbiri bu turun
  değişikliklerinden değildi** — dokunulmamış `a9b9a15` üzerinde birebir aynı şekilde düşüyorlar (ayrı bir
  worktree ile ölçüldü). İkisi de onarıldı ve paket yeniden koşuldu:
  * **Ürün gerilemesi (inceleme tabanından):** terminal görünümüne geçilirken `overview()` `_coin_heads_heading()`
    çağrısını düşürmüş; "Açık pozisyon kapsamı: N / M" sayacı ve eksik kapsamda çıkan **kırmızı uyarı** panelden
    kaybolmuştu. Bu tablo bir açık pozisyon listesi değildir ama açık pozisyonların tamamının orada olup
    olmadığını **ölçerek** gösterir; yokluğunda operatör "pozisyon yok" izlenimi alabilir. Başlık geri kondu.
  * **Kurgu hatası:** `test_learning_provenance_engine` motoru `object.__new__` ile kuruyor ve 60746c3 formasyon
    defterini çıkış yoluna ekledikten sonra `pattern_book` alanını kurmuyordu (ürün kodu sağlam:
    `engine_v3.__init__` koşulsuz `self.pattern_book = None` yazar). Bu testler çıkış→öğrenme zincirini kapsar ve
    bu turun funding değişikliği `ledger.tick`e dokunduğu için kapsamlarının geri gelmesi önemliydi.
  Paralel parça kullanılmadı; aynı paket iki farklı yöntemle tekrar çalıştırılmadı.

## 6. Sınırlar ve dürüstlük notları

* Sentetik fiyat yollarıyla **davranış** kanıtlandı; bunlar **gerçek piyasa sonucu değildir** ve kârlılık iddiası
  taşımaz. Funding testlerindeki ±%1 ve %5 **test değerleridir**, gerçek borsa oranı değildir.
* 30 kapanış rapor için tanımlanan **asgari örneklem sayısıdır**; avantaj ya da kârlılık kanıtı değildir.
* `funding_measured=False` iken rapor funding toplamını **ölçülmüş** diye sunmaz; eksik kapsama görünürdür.
  Eski (kanıtsız) işlemlere **uydurma maliyet eklenmedi**; kapanmış kayıtlar için geriye dönük mutabakat
  **yazılmadı** — gerekirse ayrı, idempotent bir turda yapılmalıdır.
* VPS HEAD'i okunmadı; dağıtım betiğinin beklenen tabanı önceki doğrulanmış üretim SHA'sıdır ve betik başka bir
  HEAD görürse **hiçbir şeye dokunmadan durur**.
