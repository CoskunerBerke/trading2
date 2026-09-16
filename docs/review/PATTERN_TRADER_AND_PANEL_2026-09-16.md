# FORMASYON PAPER TRADER V1 + İŞLEM TERMİNALİ PANELİ (kapanış, 2026-09-16)

Dal `work/chart-analysis-v1`, SHA **60746c32bf31dbadea81e808bc1d3cba6e3399cc** (82c2895 üzerine).
CI: https://github.com/CoskunerBerke/trading2/actions/runs/35143658343 (chart-analysis #6, success).
**VPS'e dağıtım YAPILMADI; VPS HEAD'i bu oturumda okunmadı.** Paket hazır: `tb-60746c3.bundle` + `tb-deploy-60746c3.sh`.

Korunan: T2/M2 kural formülleri, on coin evreni, sermaye/risk ayarları, stop geometrisi, PAPER modu, ana botun
defteri/kararları, SPOT ve provenans engelleri, BTC referansı, 82c2895 fiyat/zaman sözleşmeleri, grafik onarımları,
geçmiş kayıtların değişmezliği. Yeni trader **AYRI** defter/dizin/sanal sermaye ile çalışır.

## 1. Bitiş ölçütleri

| # | ölçüt | kanıt |
|---|---|---|
| 1 | Fiyat/zaman/provenans vakaları kapalı | `test_strategy_paper_price_time_v1` (10), `test_strategy_paper_data_source_v1` (9) — 82c2895'te kapatıldı, bu SHA'da da geçiyor |
| 2 | Sabit on coin DIŞINDA bir sembol keşif → 15m/1h/4h → şekil → plan yoluna girdi | `test_pattern_trader_v1::test_1` (22 sembollü sahte exchangeInfo, 18 uygun), `::test_2` (NEWX/USDT, 2 günlük), `::test_7` (14 sembollü dönen kuyruk) |
| 3 | Yeni listelemede dilim başına hazır/yetersiz; 60/210 günlük gizli engel yok | `::test_2` (15m 30 bar hazır, 1h 6 / 4h 2 yetersiz; `policy.min_listing_age_days == 0`, `max_symbols is None`); eksik OHLCV uydurulmuyor (`trend_4h=UNKNOWN`, `n_zones_1h=0`) |
| 4 | Gerçek motor/ortak uygulayıcı/gerçek defter üzerinden bir LONG ve bir SHORT | `::test_3` (LONG: tetik → risk → giriş → **hedefte** kapanış), `::test_4` (SHORT: giriş → **koruyucu stop** + soğuma). Deterministik fiyat yolu; doğrulama kapısı mock DEĞİL |
| 5 | Tetiklenmeyen/bozulan/süresi dolan/riske sığmayan plan emir açmıyor; tekrar, restart, gecikme, kesinti | `::test_5` (a süre doldu, b bozuldu, c bayat fiyat → plan TETİKLENMİŞ bekler, fiyat gelince açılır ve **yinelenmez**, d yeniden başlatma, e kill-switch → REJECTED) |
| 6 | Gelecek mumlar geçmiş kararı değiştirmiyor | `::test_6` (aynı karar anı, kısa ve uzun seri → AYNI plan kimliği/seviyeleri/bulgu durumları; kapanmamış bar hiçbir bulguya girmiyor) |
| 7 | Aynı kayıt grafikte tespit → plan → gerçekleşme olarak okunuyor | `test_dashboard_terminal_v1::test_7` + tarayıcı ekranı (aşağıda); plan kutusu seçilen coine bağlı |
| 8 | Kaynak/kuyruk bütçesi ve gecikme ölçümü; artan coin çıkış izleyicisini durdurmuyor | `::test_7` (kapsama 14/14, hiç taranmayan 0, `by_group`; `exit_check` mum İNDİRMİYOR → `data.bars` çağrısı 0) |
| 9 | Yeni SHA'da CI | chart-analysis #6 success; iş akışına iki yeni dosya eklendi |
| 10 | Ekonomik raporda olumlu/olumsuz ve belirsizlik ayrı | `::test_8` + `pattern_report.json` (`BELİRSİZ (örneklem yetersiz)`, sıklık ve sonuç ayrı bölümler) |

## 2. Ürün zinciri (özet)

`pattern_trader/universe.py` (resmi metadata keşfi, kohortlar, yeni listeleme/delist olayları) →
`data.py` (MarketFeed artımlı 15m/1h/4h + panelin okuduğu CSV önbelleği + `PriceService` = T2/M2 ile aynı
`verified_price`) → `detect.py` (mevcut `candle_context` şekilleri, trend, teyitli pivot/bölge; kimlikli bulgu,
teyit/bozulma/süre) → `strategy.py` (sürümlü A/B/C katalogu; tetik, iptal, stop, hedef sırası, zaman aşımı, maliyet)
→ `book.py` (ortak RiskEngine + `apply_action` + gerçek `FuturesLedgerV2`; plan yaşam döngüsü) →
`scheduler.py` (arka plan dönen kuyruk, öncelik, kapsama) → `report.py` (sıklık vs sonuç). Ayrıntı:
`docs/PATTERN_TRADER_V1.md`.

**Ölçülen tasarım kusuru ve onarımı:** bekleyen planlar kuyruğu açlığa düşürüyordu — 14 sembolün 9'u hiç
taranmıyordu. Bütçenin en az yarısı (`ROTATION_RESERVE`) yeni listelenenlere/rotasyona ayrıldı.

**İkinci kusur (bu oturumda üretildi ve kapatıldı):** ortak kapanmış-bar yardımcısı `StrategyBook` sınıf gövdesinin
ortasına eklenince `save()` modül düzeyine düştü ve T2/M2 turu her turda sessizce `AttributeError` veriyordu.
`test_pattern_trader_v1::test_0` artık iki defterin API yüzeyini kilitliyor.

## 3. Panel

Dört ana bölüm (Genel bakış / İşlemler / Tarayıcı / Gelişmiş menüsü); 13 teknik sayfa menüde ve derin bağlantılarla
erişilebilir. Hesap başına dört kart, **her hesap kendi yetkili defterinden** (özkaynaklar toplanmaz; bilinmeyen değer
"Veri yok"). Büyük grafik + açık işlem listesi; satıra tıklayınca aynı ekranda grafik, seçim vurgusu ve plan kutusu
değişir (sayfa yenilenmez, katman/dilim seçimi korunur, geç gelen yanıt korumaları duruyor). Kapanışlar Türkçe
gerekçeyle; kısmi kapanış çoğaltılmaz. Canlı güncelleme SSE + 20 sn yoklama; bağlantı koparsa son doğrulanmış içerik
kalır ve uyarı görünür. Kullanım: `docs/PANEL_KULLANIM.md`.

**Ölçülen yerleşim (gerçek tarayıcı, sentetik durum):**

| genişlik | kartlar | ana bölme | grafik | yatay taşma |
|---|---|---|---|---|
| 1440 px | 4 × 346,6 px | 951 px + 453 px | 460 px | yok (`scrollWidth == clientWidth == 1440`) |
| 390 px | 1 × 378,4 px | tek sütun | 320 px | yok (`scrollWidth == clientWidth == 390`) |

Mobil sıra: özet → grafik → açık işlemler → son kapanışlar. Satır tıklaması doğrulandı: `__chartBase` BTC → ETH,
satır vurgulandı, URL `?coin=ETH`, plan kutusu ETH SHORT pozisyonuna geçti.

**EKRAN GÖRÜNTÜLERİ SENTETİK DURUMLADIR** (`scripts/demo_panel_state.py`) — gerçek VPS görüntüsü DEĞİLDİR ve gerçek
piyasa sonucu göstermez.

## 4. Test kanıtı (etiketli)

* **Gerçek motor/defter + sahte sağlayıcı:** `test_pattern_trader_v1` (10) — `PatternBook`, `FuturesLedgerV2`,
  `RiskEngine`, `apply_action`, `PatternScanner`, `MarketFeed` gerçek; ağ yerine `MockProvider`.
* **Gerçek FastAPI + gerçek defter okuma:** `test_dashboard_terminal_v1` (8) — state dosyaları sentetik.
* **Hedefli regresyon (yerel, bu SHA):** 33 dosya, **465 geçti, 0 hata** (dashboard, chart-analysis, candle/pattern,
  strategy_paper ×6, pattern_trader, engine_v3, entry_universe, market, history).
* **Dağıtım betiği değişmezleri (yerelde, yol ikamesiyle):** **77/77 OK** (V17/V18 korunuyor, V19 eklendi).
* **Uzak CI:** chart-analysis #6 success.
* **VPS doğrulaması:** YOK. Gerçek Binance `exchangeInfo`/`klines`/`premiumIndex` yolu yalnız kod ve sahte sağlayıcı
  ile sınandı; kapsama/gecikme sayıları sentetik evrendendir.
* **Tam paket bu SHA'da koşulmadı** (önceki tam paket 82c2895 öncesinde; hedefli küme + CI kapısı koşuldu).

## 5. Ekonomik durum (dürüst)

Bu sürümün kârlılık kanıtı **YOKTUR**. `p_win` ölçülmemiştir; rapor 30 kapanmış işlemin altında hüküm vermez;
"yeni coin avantajı" iddia edilmemiştir. Yeni listelenen ve yerleşik kohortlar için sıklık ve maliyet sonrası sonuç
aynı kural sürümü ve maliyet yöntemiyle AYRI ayrı ölçülür, karşılaştırma gözlemseldir (nedensellik iddiası yok).
Gerçek yeni kanıt, dondurulan bu sürümün VPS'te toplayacağı ileri test verisidir ve **henüz oluşmamıştır**.

## 6. Kalan somut engel

VPS dağıtımı: SSH anahtarı passphrase'li olduğundan bu oturumdan bağlanılamaz. Paket ve komutlar
`RESUME-pattern-trader-60746c3.md` içindedir; `sudo bash ~/tb-deploy-60746c3.sh` kullanıcı kararıyla çalıştırılır
(betik taban HEAD 3501304 değilse durur).
