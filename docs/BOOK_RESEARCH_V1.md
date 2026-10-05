# Defter araştırması — book_v1: D4, C4 ve Formasyon için ön kayıtlı varyantlar; ana bot için yapılabilirlik (2026-10-05)

Salt araştırma. Hiçbir defter, strateji, config ya da çalışma zamanı değişmez. PAPER/geçmiş testtir; kâr garantisi değildir.

**Ön kayıt:** bu belge araştırma dönemine ait fiyat ya da fonlama verisi indirilmeden ve hiçbir sinyal sonucu (sinyal sayısı,
R, kazanma oranı, aylık %) hesaplanmadan yazıldı. İlk taslak bağımsız bir eleştiriden geçti; düzeltmeler de hiçbir sonuç
hesaplanmadan yapıldı (§14). Commit zamanı kanıttır. Kurallar kodda `tradingbot/book_lab.py` içindeki `BOOK_REGISTRY`'de
tutulacak. Bu kayıttan hesaplanan mühür `BOOK_REGISTRY_SHA` bir testle sabitlenir ve aşağıdaki satıra koşudan ÖNCE yazılır.
Biri değişirse mühür değişir: o yeni sürümdür (book_v2), yeni deneme sayısıdır ve bu belge güncellenir. Sayımlar ya da
sonuçlar görüldükten sonra kural, eşik, dilim, evren ya da dönem GEVŞETİLMEZ.

Ön kayıt mührü: `BOOK_REGISTRY_SHA = 2a3cecc16e0a85c1`

Belgenin commit'i kuralları dondurur. Mühür yalnız kodun bu belgeye sadakatini sabitler. Kod yazılırken bir belirsizlik
çıkarsa okunuşu `readings_tr`'ye yazılır ve belgeye eklenir; okunuş sonuç görülmeden yapılır ve kuralı gevşetemez.

## 0. Süreç disiplini (koşudan önce sabit)

1. Bu belge commit edilmeden kimse araştırma verisinde sinyal sonucu hesaplamaz.
2. `readings_tr` tamamlanır ve hem bu belgeye hem `BOOK_REGISTRY`'ye yazılıp commit edilir. Mühür bu belgeye yazılır. Bunların
   hepsi araştırma dönemine ait herhangi bir fiyat ya da fonlama verisi indirilmeden önce olur.
3. İnceleme düzeltmeleri yalnız hiçbir sonuç hesaplanmamışken yapılabilir.
4. Gerçek veri, dondurulmuş koşudan önce yalnız sinyal tespiti gerektirmeyen kapsam ve kalite sayımları için kullanılabilir
   (ilk bar, eksik bar, boşluk, bozuk bar, dosya sha256). Sinyal sayıları plasebo oranını beslediği için (§7.1) sonuca
   yakındır; dondurulmuş koşudan önce sayılmaz.
5. Kodun çökmeden çalıştığı yalnız sentetik veriyle denetlenir (uçtan uca kuru koşu). Gerçek veride "deneme koşusu" yoktur.
6. Dondurulmuş koşu başladıktan sonra yapılan her kod değişikliği o koşuyu book_v2 yapar. Ağ ya da indirme hatası kod
   değişikliği gerektirmez; aynı kodla yeniden denenir.
7. Her koşu denemesi kaydedilir: başarısız ve yarıda kalanlar dahil (başlangıç anı, kod commit'i, mühür, çıkış durumu, hata
   iletisi). Başarısız denemenin çıktılarına bakılmaz; yalnız hata iletisi okunur. Kayıt sonuç belgesine girer.

## 1. Soru ve hedef

Sahibin hedefi: **her algoritma (defter) kendi kâğıt bakiyesinde ayda en az net +%1** kazansın.

Bu çalışma üç defter için şunu sorar: bugünkü kural ya da önceden yazılmış, gerekçeli ve az sayıda değişikliği, maliyet sonrası
ve defterin gerçek kapasite sınırları içinde bu hedefe ulaşıyor mu? Ana bot için önce şunu sorar: kararları arşiv verisinden
sadakatle yeniden üretilebilir mi?

Bu bir öneri belgesi değildir. Hedefi karşılayan bir varyant ancak §8.5'teki bütün basamakları geçerse yalnız-kayıt bir PAPER
önerisi olur (§10).

## 2. Canlı PAPER kanıtı (motivasyon, kanıt değil)

VPS, 2026-10-05. Örneklemler küçüktür; bunlar yalnız bu çalışmanın nedenidir, hüküm değildir.

| Defter | Durum klasörü | İşlem | Ortalama R |
|---|---|---|---|
| D4 (`d4_donchian_20_10`) | `strategy_paper_trend4h` | 13 | −0,89 |
| C4 (`c4_candle_variations`) | `strategy_paper_candle4h` | 16 | −0,09 |
| Formasyon (`pattern_trader`, `momentum_4h_v3`) | `pattern_trader` | 13 | −0,48 |
| Ana bot (geri çekilme kurulumu, `engine_v3`) | `state` | 163 | −0,07 |
| M2 (`m2_tsmom28`) | `strategy_paper_m2` | 49 | +0,12 |

İşlem başına risk bu defterlerde %0,5'tir (`config.yaml` → `learning_mode.risk_per_trade_pct`). D4, C4 ve Formasyon'un
canlı işlemleri 2026-09-25'ten sonra açıldı. 2026-09'da açılanlar bu çalışmanın doğrulama dönemine (§8) düşer ve
görüldüler (§3).

## 3. Bugüne kadar denenenler (dürüst deneme sayımı)

Bu çalışmanın varyantları boşlukta seçilmedi. Aşağıdakiler daha önce sonuçları görülmüş denemelerdir. Birim "hücre"dir:
bir sinyal × yön × dilim (× bağlam dilimi) grubu.

| Ne | Kaynak | Hücre | Sonuç |
|---|---|---|---|
| Sinyal laboratuvarı: katalog + ek sinyaller + bağlam dilimleri (30 coin, 1h ve 4h) | GitHub run 36123072720 | 1.412 (belgelerdeki sayı; iş kaydı artık API'den okunamadığı için yeniden sayılamadı) | Tek sıkı aday: 4h üç beyaz asker + RSI14 > 70 LONG → Formasyon v3 |
| Sinyal laboratuvarı: algoritmalar + ek sinyaller (`--no-catalog --symbols genis --tfs 4h,1d`) | GitHub run 36150821072 | 38 HEPSİ hücresi (4 algoritma × 2 yön × 2 dilim = 16; XSMOM 1d × 2 yön = 2; 10 ek sinyal-yön × 2 dilim = 20). Bağlam dilimleriyle en çok 38 × 14 = 532 (10'dan az işlemli dilim oluşmaz) | Aday yok; en yakın 4h Donchian 20/10 LONG → D4. Short tarafında avantaj yok |
| C4 mum varyasyonları CV001–CV008 (4h birincil; 1h, 1d bilgi) | GitHub run 36277302747 | 8 birincil + 16 bilgi = 24 HEPSİ; bağlam dilimleriyle en çok 336 | CV001 ZAYIF İZ, diğer 7'si KANIT YOK; sıkı aday yok |
| Vadeli laboratuvar fut_v1 | `docs/FUTURES_OI_FUNDING_LAB.md` | 8 | Aday yok |
| Kalabalık laboratuvarı fut_v2 | `docs/CROWD_LAB_FUT_V2.md` | 8 | Aday yok |
| Altın laboratuvarı gold_v1 | `docs/GOLD_LAB_V1_RESULTS.md` | 32 | Aday yok; 1d Donchian 20/10 LONG ve 1d TSMOM_28 LONG ZAYIF İZ (keşif seti) |
| Ana bot giriş deneyleri V4–V15 | `docs/review/REVIEW-2026-09-21.md` | 55 kol (mum vetosu 4, grafik 4, rejim 3, T2 1, M2 1, Box 42) | Rejim kapısı r1 "kayıp azaltıcı"; Box'ta kol yok |
| Kârlılık deneyleri pfexp v1 / v1.1 / v1.2 | `docs/PROFITABILITY_EXPERIMENT_V1*.md` | 5 politika (ileriye dönük) | Kârlı politika kanıtlanmadı |
| Ana bot kök neden ölçümü | `docs/PROFITABILITY_ROOT_CAUSE_V1.md` (2026-09-05, 23 işlem) | betimsel, hücre yok | 0,25–1R'ye gidip geri dönen işlemler tam kayıpla kapandı → `C4_02` gerekçesi |
| Çıkış geri verme ölçümü (SHADOW) | `docs/EXIT_GIVEBACK_AND_PROFIT_PROTECTION_V1.md` (2026-09-02) | karşı-olgusal çıkış politikaları, betimsel | geri verme örüntüsü → `C4_02` gerekçesi |
| Giriş challenger aileleri A–H (SHADOW) | `docs/ENTRY_SELECTIVITY_CHALLENGER_V1.md` (A–E), `docs/WEEKLY_MARKET_STRUCTURE_V1.md` (F–G), `docs/MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1.md` (H) | 8 aile | Hiçbiri terfi kapısını geçmedi. Rejim ailesi (B) ve ana botun rejim ölçümü, bu çalışmanın rejim ve oynaklık süzgeçlerinin seçiminde bilgi olarak kullanıldı |

Bunun dört sonucu var:

1. **Formasyon kuralı 1.412 hücreden, D4 kuralı en az 38 (dilimlerle en çok 532) hücreden seçildi.** Rastgele yürüyüşte
   hücrelerin %0–0,5'i tesadüfen GÜÇLÜ ADAY çıkar; 1.412 hücrede bu tesadüfen ~0–7 adaydır. İki defterin taban kanıtı bu
   yüzden zayıftır.
2. **Doğrulama dönemi temiz değil.** Önceki koşuların doğrulama dönemleri (kabaca 2025 ortası → 2026-09) bu çalışmanın
   doğrulama dönemiyle (2025-01 → 2026-09) örtüşür ve o sonuçlar görüldü. Varyantlar "D4 son dönemde zayıfladı" bilgisiyle
   tasarlandı. D4, C4 ve Formasyon'un canlı PAPER işlemlerinin (D4_05'in gerekçesi) 2026-09'da açılanları da bu
   çalışmanın doğrulama döneminin içindedir.
3. **Taban hücreler (`D4_00`, `C4_00`, `FM_00`) kendi seçim koşularının kullandığı verinin çoğunu yeniden kullanır.** Tek
   başlarına öneri dayanağı olamazlar; yalnız kıyas içindir (§8.5).
4. Bu çalışmada gerçekten örneklem dışı olan tek ölçüm, belgenin commit'inden sonraki ileriye dönük PAPER sonucudur (§10).

## 4. Veri

### 4.1 Kaynak ve dondurulmuş pencere

- **Kaynak:** yalnız `data.binance.vision` USDⓈ-M arşivi (`signal_lab.ArchiveProvider`). fapi/api.binance.com bu ortamdan 451
  döner. Fonlama: yalnız `futures_data.FUNDING_URL` (aylık `fundingRate` dosyaları; arşivde günlük fonlama dosyası yoktur).
- **Pencere çalıştırma tarihine bağlı değildir.** `signal_lab.load_series` pencereyi `now_ms − days` ile seçer; bu yüzden
  koşucu seriyi şu sabit sınırlara kırpar (UTC, ms, bar açılış zamanı = `open_time`):

| Seri | İlk bar | Son bar |
|---|---|---|
| Her coinin 4h serisi | `open_time ≥ 1640995200000` (2022-01-01 00:00) ya da listeleme, hangisi sonraysa | `open_time < PRICE_END` |
| Her coinin 1d serisi (D4 1d sinyalleri ve `COIN_UP`) | `open_time ≥ 1577836800000` (2020-01-01 00:00) ya da listeleme | `open_time < PRICE_END` |
| BTCUSDT 1d (`BTC_UP` / `BTC_DOWN`) | `open_time ≥ 1577836800000` (2020-01-01 00:00) | `open_time < PRICE_END` |
| Fonlama uzlaşmaları | `funding_time ≥ 1669852800000` (2022-12-01 00:00) | `funding_time < 1790899200000` (2026-10-02 00:00) |

- **`PRICE_END = 1790812800000` (2026-10-01 00:00 UTC).** Bütün çıkışlar, süre sınırları ve `DATA_END` bu tek sınırla
  verilir. Son 4h barın açılışı 2026-09-30 20:00, son 1d barın açılışı 2026-09-30'dur; ikisi de 2026-10-01 00:00'da kapanır.
- **Karar penceresi:** karar anı t (bar kapanışı = `open_time` + dilim) için `1672531200000 ≤ t < 1790812800000`
  (2023-01-01 00:00 ≤ t < 2026-10-01 00:00).
- **Fonlama penceresi** `PRICE_END`'den sonra en az bir uzlaşma içerir (2026-10 aylık dosyasındaki uzlaşmalar). Böylece
  veri sonunda kapanan işlemin de "çıkıştan sonraki uzlaşma" şartı sağlanır (§5.2).
- **Göstergelerin tohumu bu sınırlardan başlar.** ATR14, RSI14, EMA (EWM) ve `atr_med` serinin ilk barından hesaplanır;
  `i ≥ 210` sayımı ve C4'ün 500 barlık penceresi bu ilk bara göre yapılır. `COIN_UP` ve `BTC_UP`'ın `regime_gate.ema_last`
  tohumu ilgili 1d serisinin ilk 200 kapanışıdır (2020-01-01 ya da listeleme).
- **Bayt kanıtı:** koşucu her serinin pencere içindeki satırlarını sabit biçimli CSV metnine çevirip sha256'sını, her fonlama
  dosyasının da sha256'sını çıktıya yazar. (Önbellekteki `.csv.gz` dosyasının baytları gzip başlığındaki zaman damgası
  yüzünden kanıt olamaz.) Yeniden koşu aynı özetleri vermelidir; vermezse fark rapora yazılır.
- **Önbellek ve çıktı:** önbellek `.../scratchpad/books/cache`, çıktılar `.../scratchpad/books/out`. Fiyat verisi depoya
  yüklenmez.

### 4.2 Boşluklar, bozuk barlar, yeniden adlandırma

- **Bozuk bar:** sonlu olmayan OHLC, `h < max(o, c)`, `l > min(o, c)`, sonlu olmayan ya da negatif hacim (`candle_lab.valid_ends`
  ile aynı tanım).
- **Boşluk:** ardışık iki barın açılış farkı dilimden farklı.
- **Sinyal için temiz pencere şartı** (bütün defterler; C4'te `valid_ends` bunu zaten yapar):
  - D4 (4h ve 1d): sinyal barı i ile biten 210 bar (i−209 … i) boşluksuz ve bozuk barsız.
  - Formasyon: katalog analizinin 300 barlık penceresi (i−299 … i) boşluksuz ve bozuk barsız.
  - C4: `valid_ends` (500 bar).
  - `BTC_UP` / `BTC_DOWN` / `COIN_UP`: kullanılan 1d barıyla biten son 200 1d bar boşluksuz ve bozuk barsız; değilse değer
    bilinmiyor → giriş yok.
  - Şart sağlanmazsa o barda sinyal (ve plasebo) yoktur. Sayısı raporlanır.
- **Boşluk geçen işlem** laboratuvar gibi satır satır simüle edilir: boşluktan sonraki ilk açılış gerçek dolum fiyatıdır (borsa
  kapalıyken çıkılamazdı). Süre sınırı bar sayısıyla sayılır. Bu işlemlerin sayısı ve boşluk süreleri raporlanır.
- **Bozuk bar geçen işlem** (giriş barından çıkış barına kadar) hükümden ve aylık ölçüden atılır (`BAD_BAR`); gerçek ve
  plasebo için aynı. Bir hücrede bu işlemler %1'i aşarsa hücre "VERİ BOZUK" diye işaretlenir ve hedefi karşılayamaz.
- **Yeniden adlandırma / yeniden listeleme:** yalnız bugünkü sembolün arşiv geçmişi kullanılır; eski sembolün geçmişi
  birleştirilmez. Bilinen durumlar (ör. bilgi evrenindeki `G`) kalite raporunda sembol başına listelenir.
- **Kalite raporu:** her seri için ilk bar (listeleme), eksik bar oranı, 24 saatten uzun boşlukların listesi, bozuk bar sayısı.

### 4.3 Evren

**Birincil evren (hüküm ve hedef): defterlerin canlıda gerçekten işlem açtığı 23 coin.**

```
BTC ETH SOL BNB XRP DOGE ADA AVAX LINK LTC DOT NEAR TRX BCH UNI FIL XLM AAVE APT ARB OP INJ SUI   (hepsi /USDT perp)
```

- Neden bu liste: 2026-10-03'ten beri `learning_mode.extra_entries: record_selectivity`. Bu kipte `BOOK_UNIVERSE` ve
  `NOT_IN_PROTOCOL_UNIVERSE` kodlu girişler yalnız kaydedilir, açılmaz. D4 ve C4 yalnız config'teki kendi 23 coinlik
  listesinde işlem açar. Formasyon `strategy_v3.V3_SYMBOLS` (30 coin) ile tur kapsamının (giriş evreni, `analyze_outside:
  false`) kesişiminde açar; bu kesişim aynı 23 coindir.
- Coin, pencere içindeki ilk barından itibaren evrene girer. Sonradan listelenen coin yalnız kendi başlangıcından sonra
  işlem üretir. **Gerçek sayılar ölçülür ve raporlanır:** her dönem için en az bir geçerli barı olan coin sayısı, dönemin
  tamamını kapsayan coin sayısı ve coin başına ilk bar.
- **Bilgi evreni:** botun giriş evreni (`config.yaml` → `entry_universe.symbols`, 40 coin). Yalnız rapor; hükme ve hedefe
  girmez. Burada geçen bir varyant evren değişikliği içerir ve öneri olamaz.

**Seçim yanlılığı (geleceğe bakış), yönüyle:** iki liste de bugünün bilgisiyle seçildi. Laboratuvarın 30 coini "uzun geçmişli,
likit" perp'lerdir (2026-09). Giriş evreni 2026-09-18'de o günün 24 saatlik hacmine, listeleme yaşı ≥ 747 güne ve spread'e göre
seçildi. Birincil evrendeki her coin bugün likit ve hayattadır. 2023–2026'da çöken ya da listeden çıkarılan coinler (arşivde
hâlâ duruyorlar) yoktur. Bu, LONG ve trend hücrelerini olduğundan iyi, SHORT hücrelerini olduğundan kötü gösterir. Düzeltme,
öneriden önce zorunlu bir zaman noktası (PIT) teyit koşusudur (§8.5); geçiş ölçütü aşağıda şimdiden yazılıdır.

**PIT evreni (yalnız §8.5 teyidi):**
- Sembol havuzu: `data.binance.vision` USDⓈ-M aylık kline listesindeki bütün semboller, listeden çıkarılanlar dahil. Dışarıda
  kalanlar: alt çizgili vadeli sözleşmeler, kotası USDT olmayanlar, endeks sözleşmeleri (`BTCDOM`, `DEFI` vb.), stabil coin
  tabanlı olanlar (`USDC`, `BUSD`, `TUSD`, `FDUSD`, `USDP`, `DAI` vb.) ve kripto olmayan dayanaklar (`XAU`, `XAG`, `PAXG`, hisse
  ve endeks perp'leri). Tam dışlama listesi koşudan önce `readings_tr`'ye yazılır.
- Her UTC ay başı m (2023-01 … 2026-09): havuzdaki sembolleri m'den önceki 30 günün toplam kotasyon hacmine (1d kline'ın
  `quote_asset_volume` sütunu) göre sırala; o 30 günde en az 30 1d barı olanlardan ilk 40'ı m ayının evrenidir. Karar anı m
  ayında olan sinyaller yalnız bu 40 sembolde işlem olur.
- Evrenden çıkan sembolün açık pozisyonu kendi çıkışıyla kapanır (canlı `entry_universe` kuralı). Listeden çıkarılan sembolde
  açık pozisyon son barın kapanışında kapanır (`DELISTED`), kapanış maliyeti dahil.
- Sembol listesi alınamazsa ya da PIT ay-sembol çiftlerinin %5'inden fazlasında veri eksikse teyit yapılamaz: durum "PIT
  DOĞRULANAMADI" olur ve hücre öneri olamaz.

## 5. Ortak işlem modeli ve tanımlar

### 5.1 İşlem modeli

- Karar kapanmış barın kapanışında verilir; giriş sonraki barın açılışındadır.
- Simülasyon kuralları laboratuvarınkidir (`signal_lab.simulate`, `signal_lab.simulate_rule`): aynı barda hem stop hem hedef →
  STOP; boşlukla açılışta açılış fiyatı; stop bar içinde kötümser denetlenir; kural çıkışı k barının kapanışında tetiklenir,
  çıkış k+1 barının açılışındadır; süre sınırında son barın kapanışında çıkılır.
- `book_lab` bu kuralları kendi simülatöründe uygular. Simülatör, veri sonuna değmeyen her işlemde `simulate` /
  `simulate_rule` ile birebir aynı sonucu verir (parite testi). Farkları yalnız iki: `DATA_END` tamamlaması (aşağıda) ve kanal
  çıkışının referans serisinin seçilebilmesi (lo10, lo20, hi10).
- **Veri sonu (`DATA_END`), hükümde ve aylık ölçüde aynı:** giriş barı (i+1) pencerede varsa işlem simüle edilir. `PRICE_END`'e
  kadar çıkış olmazsa son barın kapanışında kapatılır (`DATA_END`), kapanış maliyeti dahil. Son barda kural tetiklenirse de
  çıkış `DATA_END`'dir (k+1 barı yoktur). Giriş barı yoksa işlem yoktur (`NO_ENTRY_BAR`, sayılır). Laboratuvarın
  `NO_FUTURE_DATA` atlaması bu çalışmada KULLANILMAZ: o kural veri sonunda hâlâ açık olanları, yani çoğunlukla koşan trend
  kazançlarını atardı. Aynı kural gerçek ve plasebo işlemlerine uygulanır.
- **Sansür payı:** her hücre ve dönem için `DATA_END` ile kapanan işlemlerin payı raporlanır. Doğrulama döneminde bu pay %10'u
  aşarsa hücre "SANSÜR YÜKSEK" diye işaretlenir ve hedefi karşılayamaz (fail-closed).
- Risk aralığı: sabit hedefli kurallarda 0,1 < risk/ATR ≤ 5 (`LabConfig`), kural çıkışlılarda 0,1 < risk/ATR ≤ 10
  (`simulate_rule`). ATR, sinyal barı i'deki laboratuvar ATR14'üdür (C4'te pencere ATR14'ü, `candle_lab`). Kovalama (yalnız
  tetiği olan olaylarda): giriş tetikten 1 ATR'den uzaksa işlem yok.
- Aynı varyant içinde üst üste binen işlemlere hükümde izin vardır (laboratuvar gibi). Defterin "sembol başına tek pozisyon"
  ve slot sınırları yalnız kapasite modelinde uygulanır (§8.2).

### 5.2 Maliyet ve fonlama

- Botun modeli: taraf başına taker %0,05 + kayma 3 bps, gidiş-dönüş ≈ %0,16 (`LabConfig` varsayılanı = `config.yaml`
  `futures_taker_pct` ve `slippage_bps`). R = (net PnL) ÷ (giriş − stop).
- **Fonlama:** her gerçek işlem ve her plasebo için `funding_r`, `futures_lab.funding_carry` tanımıyla hesaplanır (ts[j] < t ≤
  çıkış anındaki uzlaşmalar, −s · Σ oran · fiyat ÷ risk; dizi kesintiliyse NaN).
- **`DATA_END` işleminin fonlaması:** çıkış anı `PRICE_END`'dir (son barın kapanışı; `funding_carry`'nin TIME kuralıyla aynı).
  Fonlama penceresi `PRICE_END`'den sonraki uzlaşmayı içerdiği için "sonraki uzlaşma var" şartı sağlanır.
- **NaN fonlama tutucu doldurulur:** önce NaN payı (doldurmadan önce) yazılır. Sonra NaN değer yerine, işlemin yönü ne olursa
  olsun, tutma süresinin başlamış her 8 saati için giriş fiyatının %0,01'i ödenmiş sayılır:
  `funding_r = −0,0001 × giriş × ⌈tutma_saat ÷ 8⌉ ÷ risk`. (%0,01, Binance'in 8 saatlik taban faizidir; LONG için ortalamaya
  yakın, SHORT için kötümserdir.) NaN'ı 0 saymak LONG için iyimser olurdu.
- Fonlamalı R = R + `funding_r`. Hüküm şartı ve aylık hedef ikisinde de fonlamalı sürüm aranır (§8.3).

### 5.3 Ortak süzgeç tanımları

Bu süzgeçler varyant tablolarında adlarıyla kullanılır. Hepsi karar anı t'de, yalnız t'den önce kapanmış barlarla bakılır.
Bilinmeyen değer → giriş yok (fail-closed).

| Ad | Tanım | Kaynak |
|---|---|---|
| `BTC_UP` / `BTC_DOWN` | `open_time + 86.400.000 ≤ t` olan son BTCUSDT 1d barında close > EMA200 → UP, değilse DOWN. (Binance'in `close_time`'ı değil.) EMA200 = `regime_gate.ema_last` (ilk 200 kapanışın SMA'sıyla tohumlanan EMA), 2020-01-01'den o bara kadarki 1d kapanışlar | `tradingbot/regime_gate.py` (r1/r3, ana botta ENFORCE) |
| `COIN_UP` | Aynı tanım, coinin kendi 1d serisiyle (2020-01-01 ya da listelemeden); en az 200 kapanış yoksa bilinmiyor | T2 kuralı (`ema200_trend`, 1d close > EMA200) |
| `VOL_OK` (D4, Formasyon) | Laboratuvarın volatilite kovası "yüksek" değil: ATR%(i) ÷ `atr_med`(i) ≤ 1,25. `atr_med` = önceki 200 barın ATR% medyanı (en az 50), TAM seriden (`signal_lab.indicators`). Oran bilinmiyorsa giriş yok | laboratuvar bağlam kovası |
| `VOL_OK_DSL` (yalnız C4) | DSL bağlamı `context.atr_regime: [None, 1.25]`. ATR ve medyan 500 barlık PENCEREDEN hesaplanır (`candle_dsl`). **Aynı fikir, iki ayrı hesap:** D4/Formasyon'daki `VOL_OK` ile sayısal olarak aynı değildir | DSL bağlam anahtarı |
| `VOL_CONFIRM` | Sinyal (teyit) barının hacmi ÷ önceki 20 barın hacim ortalaması > 1,5 (laboratuvarın "yüksek(>1.5x)" kovası) | laboratuvar bağlam kovası |
| `COST_STOP` | Gerçek girişte stop mesafesi s = \|giriş − stop\| ÷ giriş ≥ %1,6. Gidiş-dönüş maliyet %0,16 bu durumda ≤ ~0,10R olur. Altında işlem yok (`STOP_TOO_TIGHT_FOR_COST`). Giriş anında denetlenir | maliyetin R'yi yediği belgelenmiş durumlar (gold_v1 15m, Box stop tabanı) |

## 6. Varyantlar

Her varyant tam ve yeniden oynatılabilir bir tanımdır. Değişken sayısı bilinçli olarak az tutuldu: rejim/trend süzgeci,
dilim (4h ve 1d), çıkış tasarımı, volatilite süzgeci, tek yön ya da iki yön ve maliyet bilinçli en küçük stop. Parametre
taraması yapılmaz. Her defterde varyant 0 bugünkü kuraldır (taban).

**Plaseboya uygulanan süzgeçler** sütunu: eşleştirilmiş plasebo (§7) varyantın SİNYAL olmayan süzgeçlerini sağlar. Sinyalin
kendisi (kırılım, mum şekli, RSI, hacim teyidi) plaseboya uygulanmaz.

### 6.1 D4 — Donchian trend takibi (13 varyant, 15 birincil hücre)

**Taban kural (`tradingbot/donchian_trend.py`, laboratuvardaki `TREND_DONCHIAN_20_10`, `signal_lab.algo_events`):**
- Göstergeler `signal_lab.indicators` (ATR14) ve `signal_lab.aux_series` (hi20, lo10, lo20, hi10: önceki N barın en
  yükseği/en düşüğü, i barı hariç). Bar indeksi i ≥ 210 ve §4.2'nin 210 barlık temiz pencere şartı.
- Giriş: c[i] > hi20[i] ve c[i−1] ≤ hi20[i−1] (ilk kapanış kırılımı) → LONG.
- İlk stop: c[i] − 2 × ATR14[i]. Çıkış: c[k] < lo10[k] → k+1 açılışı. En çok 300 bar. Hedef yok. Risk aralığı
  0,1 < risk/ATR ≤ 10. Kovalama yok.
- SHORT (yalnız D4_10): c[i] < lo20[i] ve c[i−1] ≥ lo20[i−1]; stop c[i] + 2 × ATR; çıkış c[k] > hi10[k].
- **1d varyantlarında süre sınırı 300 GÜNLÜK bardır (≈ 300 gün)**, laboratuvarın algoritma tanımıyla aynı bar sayısı. 4h'de
  300 bar = 50 gündür.

| Kimlik | Dilim | Değişiklik (tabana göre) | Yön | Plaseboya uygulanan süzgeçler | Gerekçe |
|---|---|---|---|---|---|
| `D4_00_BASE` | 4h | yok (bugünkü kural) | LONG | — | taban (tek başına öneri dayanağı olamaz, §3) |
| `D4_01_BTC_UP` | 4h | + `BTC_UP` | LONG | `BTC_UP` | Trend takibi ayı piyasasında sahte kırılım üretir; ana botun rejim ölçümü (V7) kaybı azalttı |
| `D4_02_COIN_UP` | 4h | + `COIN_UP` | LONG | `COIN_UP` | Mutlak momentum (zaman serisi momentumu literatürü; T2 kuralı): yalnız uzun vadeli yükselişteki coinde kırılım |
| `D4_03_VOL_OK` | 4h | + `VOL_OK` | LONG | `VOL_OK` | Aşırı oynak dönemde 2 ATR stop gürültü içinde kalır, kırılım tükenme olabilir |
| `D4_04_VOL_CONFIRM` | 4h | + `VOL_CONFIRM` | LONG | — | Hacimle teyitli kırılımın devam etme olasılığı (klasik teknik analiz varsayımı) |
| `D4_05_STOP3ATR` | 4h | ilk stop c[i] − 3 × ATR14 | LONG | — | Canlıda 13 işlemin 13'ü kayıp: dar stop sahte kırılımda erken patlıyor olabilir; M2 (tek pozitif defter) 3 ATR kullanır. Bu gerekçe doğrulama dönemindeki görülmüş işlemlere dayanır (§3) |
| `D4_06_EXIT_LO20` | 4h | çıkış c[k] < lo20[k] (daha yavaş çıkış) | LONG | — | Trend takibinde kârı koşturma; Kaplumbağa Sistem 2 çıkışı 20 bar |
| `D4_07_1D` | 1d | taban kuralın 1d hâli (ATR14, hi20, lo10 1d'de; i ≥ 210; en çok 300 günlük bar) | LONG | — | Daha uzun dilimde maliyet payı küçük; klasik Kaplumbağa günlük çalıştı; gold_v1'de 1d Donchian ZAYIF İZ |
| `D4_08_1D_55_20` | 1d | giriş c[i] > hi55[i] ve c[i−1] ≤ hi55[i−1] (ilk kapanış kırılımı; hi55 = önceki 55 barın en yükseği, i hariç); çıkış c[k] < lo20[k] → k+1 açılışı; stop c[i] − 2 × ATR14; risk aralığı 0,1 < risk/ATR ≤ 10; i ≥ 210; en çok 300 günlük bar | LONG | — | Kaplumbağa Sistem 2 (55/20), literatürdeki tanım |
| `D4_09_1D_BTC_UP` | 1d | `D4_07` + `BTC_UP` | LONG | `BTC_UP` | `D4_07` ve `D4_01` gerekçeleri birlikte (önceden belirlenmiş birleşim) |
| `D4_10_1D_REGIME_BOTH` | 1d | `D4_07` kuralı; LONG yalnız `BTC_UP`, SHORT yalnız `BTC_DOWN` (r3 rejimi takip) | iki yön | yönüne göre `BTC_UP` / `BTC_DOWN` | Simetrik trend takibi düşüş rejiminde short kazanabilir; 4h short tarafı rejimsiz test edildi ve avantaj yoktu |
| `D4_11_BTC_COIN_UP` | 4h | + `BTC_UP` + `COIN_UP` | LONG | `BTC_UP`, `COIN_UP` | `D4_01` ve `D4_02` birlikte (önceden belirlenmiş birleşim) |
| `D4_12_COST_STOP` | 4h | + `COST_STOP` | LONG | `COST_STOP` | Düşük oynaklıkta 2 ATR stop dar kalır ve maliyet R'yi büyütür |

Birincil hücreler: 12 tek yönlü varyant × 1 + `D4_10` × 3 (iki yön birlikte, LONG, SHORT) = **15**.

### 6.2 C4 — 4h mum varyasyonları (9 varyant, 27 birincil hücre)

**Taban kural (`tradingbot/candle_book.py`, `candle_variations.py`, `candle_dsl.py`, `candle_lab.py`):** defterin bugünkü
listesindeki sekiz varyasyon, config'teki öncelik sırasıyla. Dedektör `candle_dsl.detect_last`, son 500 kapanmış 4h barlık
pencere (`candle_lab.variation_events`, `valid_ends`). Stop formasyon ucu ∓ 0,25 ATR; hedef gerçek girişten 2R; en çok 24 bar;
risk aralığı 0,1–5 ATR (`candle_lab.vcfg`). Tanımlar koddaki gibidir; mühürleri:

| Öncelik | Kimlik | Yön | `definition_sha` | Özet |
|---|---|---|---|---|
| 1 | `CV001_BREAKOUT20_TREND_VOL_L` | LONG | `568292b548308023` | trendle 20 bar tepesinin ilk kapanış kırılımı, gövde ≥ %55 yeşil, hacim ≥ 1,3x |
| 2 | `CV002_BREAKOUT20_TREND_VOL_S` | SHORT | `d2a63f46ff2a72f5` | CV001 aynası |
| 3 | `CV003_PULLBACK_ENGULF_L` | LONG | `6a51dc3b125a6dea` | trendle, RSI14 40–55, kırmızıyı yutan yeşil, hacim ≥ 1,0x |
| 4 | `CV004_PULLBACK_ENGULF_S` | SHORT | `904dcdb6954657b4` | CV003 aynası (RSI14 45–60) |
| 5 | `CV005_SUPPORT_HARAMI_L` | LONG | `e681cff403c840b3` | 20 bar dibine ±0,5 ATR uzun kırmızı + içinde hacmi artan yeşil, RSI14 ≤ 40 |
| 6 | `CV006_RESIST_HARAMI_S` | SHORT | `e7dafeddb50d7f62` | CV005 aynası (RSI14 ≥ 60) |
| 7 | `CV007_SWEEP_REJECT_ENGULF_L` | LONG | `07fc988844ebe90f` | 20 bar dibini süpürüp geri kapanan, alt fitil ≥ %45 + hacimli (≥ 1,5x) yutan yeşil |
| 8 | `CV008_SWEEP_REJECT_ENGULF_S` | SHORT | `fd7a16f7a2274991` | CV007 aynası |

**Tekilleştirme (canlı defterin davranışı, `candle_book.decide`):**
1. Tekilleştirme TESPİT anında yapılır: aynı sembolde aynı karar barında eşleşen varyasyonlardan yalnız en yüksek öncelikli
   olanın olayı işleme aday olur (diğerleri `also_matched`). Risk aralığı (`STOP_TOO_CLOSE` / `STOP_TOO_FAR`) ve girişteki
   denetimler tekilleştirmeden SONRA uygulanır; seçilen varyasyon girişte elenirse alttakine düşülmez (canlıdaki gibi).
2. Tespit anında bilinen süzgeçler (`BTC_UP` / `BTC_DOWN`, `VOL_OK_DSL`) eşleşmenin parçasıdır: tekilleştirmeden ÖNCE
   uygulanır. Örnek: DOWN rejiminde aynı barda CV001 LONG ve CV004 SHORT eşleşirse `C4_01`'de CV001 rejimden elenir ve işlem
   CV004 SHORT olur. Giriş fiyatına bağlı süzgeç (`COST_STOP`) girişte denetlenir: tekilleştirmeden SONRA, düşmesiz.
3. Yalnız-LONG ve yalnız-SHORT hücreler, defter yalnız o yönün CV'lerini listeliyormuş gibi KENDİ yönleri içinde
   tekilleştirilir (bir LONG olayını bir SHORT CV bastıramaz). Havuzlu hücre sekiz CV'nin hepsiyle tekilleştirilir.
4. Bu tekilleştirme hükümde ve kapasite modelinde aynıdır.

**Varyant değişiklikleri DSL'de yazılır** (her CV'ye aynı değişiklik). Değişen tanımlar yeni `definition_sha` alır; bunlar
`BOOK_REGISTRY`'de tutulur, `candle_variations.VARIATIONS`'a EKLENMEZ (defter kaydı değişmez).

| Kimlik | Değişiklik (her CV'de) | Kullanılan CV'ler | Plaseboya uygulanan süzgeçler | Gerekçe |
|---|---|---|---|---|
| `C4_00_BASE` | yok (bugünkü defter) | CV001–CV008 | (candle_lab'ın kendi eşleşmesi) | taban (tek başına öneri dayanağı olamaz, §3) |
| `C4_01_REGIME` | LONG CV'ler yalnız `BTC_UP`, SHORT CV'ler yalnız `BTC_DOWN` (tespit anında) | CV001–CV008 | `BTC_UP` / `BTC_DOWN` | Rejim yönüyle işlem (r3); ana botun rejim ölçümü |
| `C4_02_EXIT_1R` | `exit.target_r: 1.0`, `max_hold_bars: 24` | CV001–CV008 | — | Ana bot kök neden analizi ve geri verme ölçümü (§3): 0,25–1R'ye gidip geri dönen işlemlerin hepsi tam kayıpla kapandı; hızlı hedef isabet oranını yükseltir |
| `C4_03_EXIT_3R` | `exit.target_r: 3.0`, `max_hold_bars: 60` | CV001–CV008 | — | Kazancın maliyete oranını büyütmek; 2R/24 bar laboratuvar varsayılanıydı, kullanıcı kuralı değildi |
| `C4_04_WIDE_STOP` | `stop.atr_buffer: 1.0` | CV001–CV008 | — | Formasyon ucuna 0,25 ATR çok yakın; gürültüde erken stop |
| `C4_05_COST_STOP` | + `COST_STOP` (girişte) | CV001–CV008 | `COST_STOP` | Mum stopları sık sık dar; maliyet R'yi yer |
| `C4_06_VOL_OK` | `context.atr_regime: [None, 1.25]` eklenir (`VOL_OK_DSL`) | CV001–CV008 | (DSL bağlamı; candle_lab eşi bunu sağlar) | Aşırı oynak dönemde mum sinyali gürültü |
| `C4_07_TREND_ONLY` | yok | yalnız CV001–CV004 (trend yönündeki kırılım/geri çekilme) | (candle_lab'ın kendi eşleşmesi) | Dönüş mumlarını bırakmak: 4h kriptoda momentum dönüşten güçlü varsayımı. **Veri gözetlemeli:** önceki koşuda CV001 dışındaki yedisi KANIT YOK çıktı; bu alt küme o bilgiyle seçildi. Deneme sayısına girer, sonuç tablosunda işaretlenir, tek başına öneri olamaz (§8.5) |
| `C4_08_REGIME_COST` | `C4_01` + `C4_05` | CV001–CV008 | `BTC_UP` / `BTC_DOWN`, `COST_STOP` | önceden belirlenmiş birleşim |

Birincil hücreler: 9 varyant × 3 (iki yön birlikte, yalnız LONG CV'ler, yalnız SHORT CV'ler) = **27**.

C4 için 1d dilimi bu sürümde yoktur: defter yalnız 4h işler (`candle_dsl.NOT_ENABLED_TFS`) ve 500 barlık 1d penceresi
evrenin yarısını yıllarca dışarıda bırakırdı.

### 6.3 Formasyon — 4h üç beyaz asker + RSI14 > 70 (10 varyant + 1 kontrol, 12 birincil hücre)

**Taban kural (`tradingbot/pattern_trader/strategy_v3.py`, `docs/PATTERN_TRADER_V3.md`):**
- Sinyal: ortak katalogda (`structures.analyze`, 4h, laboratuvarın `catalog_events` yolu, pencere 300 bar) `THREE_WHITE_SOLDIERS`
  LONG kaydının teyidi. Şekil: üç ardışık boğa gövdesi ve yükselen kapanışlar (`learn/candle_context.py`). Tetik formasyonun
  tepesi; teyit 3 bar içinde tepenin üstünde ilk kapanış; geçersizlik formasyonun dibinin altında kapanış
  (`structures/analysis.py` `_run_status`).
- **Değerlendirme adımı 1 bar (`LabConfig.stride = 1`).** Canlı Formasyon her kapanmış barda değerlendirir. Laboratuvarın
  varsayılan adımı 3'tür; adım 3'te aradaki barda teyit edilen bir kayıt, 2 sonraki barı da görmüş bir analizden okunur
  (BROKEN/EXPIRED geçişleri, stop'un varlığı, kaydın kendisi). Bu yüzden Formasyon hücreleri adım 1 ile koşar. Önceki
  koşu (36123072720) adım 3'tü; `FM_00` o koşuyla bu yüzden de birebir aynı olmayabilir.
- Süzgeç: teyit barı kapanışında RSI14 (Wilder, `signal_lab.indicators`) > 70. §4.2'nin 300 barlık temiz pencere şartı.
- Kovalama: giriş tetikten 1 ATR'den uzaksa yok. Risk aralığı 0,1–5 ATR (ATR = laboratuvar ATR14, sinyal barında).
- **Stop: katalog kaydının stop'u, DEĞİŞTİRİLMEDEN** (kayıt kendi içinde geçersizlik − 0,25 × kataloğun kendi ATR'si olarak
  kurar; canlı v3 bunu aynen kullanır). Hedef: gerçek girişten 2R; katalog kaydının hedefi kullanılmaz (canlı v3 ile aynı).
  En çok 24 bar.
- SHORT (yalnız `FM_09`): `THREE_BLACK_CROWS` SHORT kaydının teyidi + RSI14 < 30; stop katalog kaydının stop'u,
  değiştirilmeden; aynı çıkışlar.

| Kimlik | Değişiklik (tabana göre) | Yön | Plaseboya uygulanan süzgeçler | Gerekçe |
|---|---|---|---|---|
| `FM_00_BASE` | yok (bugünkü kural) | LONG | — | taban (tek başına öneri dayanağı olamaz, §3) |
| `FM_01_BTC_UP` | + `BTC_UP` | LONG | `BTC_UP` | Momentum sinyali piyasa yükselişte çalışır varsayımı; ana botun rejim ölçümü |
| `FM_02_COIN_UP` | + `COIN_UP` | LONG | `COIN_UP` | Kısa vadeli momentumun uzun vadeli trendle aynı yönde olması |
| `FM_03_VOL_OK` | + `VOL_OK` | LONG | `VOL_OK` | RSI > 70 ve aşırı oynaklık birlikte tükenme olabilir |
| `FM_04_VOL_CONFIRM` | + `VOL_CONFIRM` (teyit barında) | LONG | — | Hacimle teyitli momentum |
| `FM_05_COST_STOP` | + `COST_STOP` | LONG | `COST_STOP` | Üç mumluk formasyonun dibi yakın olabilir; maliyet R'yi yer |
| `FM_07_EXIT_TREND` | hedef yok; çıkış c[k] < lo10[k] → k+1 açılışı (`kind: channel`); en çok 300 bar; stop aynı; tabanın kovalama ve 0,1–5 ATR risk aralığı girişte aynen uygulanır | LONG | — | Momentum sinyali, kârı kesen 2R hedef yerine trend çıkışıyla daha uzun koşabilir |
| `FM_08_EXIT_3R` | hedef 3R, en çok 60 bar | LONG | — | Kazancın maliyete oranını büyütmek |
| `FM_09_REGIME_BOTH` | LONG taban sinyal yalnız `BTC_UP`; SHORT üç kara karga + RSI14 < 30 yalnız `BTC_DOWN` | iki yön | yönüne göre `BTC_UP` / `BTC_DOWN` | Rejimsiz short testi geçemedi; rejime bağlı simetrik momentum |
| `FM_10_BTC_UP_EXIT_TREND` | `FM_01` + `FM_07` | LONG | `BTC_UP` | önceden belirlenmiş birleşim |
| `FM_CTRL_NO_RSI` | **kontrol**: RSI süzgeci yok (üç beyaz asker tek başına) | LONG | — | Hipotez değildir. RSI > 70 süzgecinin 1.412 hücreden seçilmiş olmasının etkisini gösterir. Aday sayısına, deneme sayısına ve hedef hükmüne girmez |

- **`FM_06_EXIT_1R` çıkarıldı** (sonuç görülmeden, eleştiri üzerine). 1R hedefte maliyet sonrası R/R yapısı gereği 1,0'ın
  altındadır. Canlı protokolün `min_rr_after_cost: 1.0` kapısı (`RR_BELOW_MIN_AT_ENTRY`) bu yüzden her `FM_06` işlemini yalnız
  kaydederdi; öneri olması için ikinci bir protokol kapısı değişikliği gerekirdi. Kimlikler değişmesin diye numaralar
  kaydırılmadı.
- `FM_07` ve `FM_10` hedefsizdir. Canlı protokolün R/R kapısı hedefsiz plan için tanımlı değildir. Bu varyantlardan biri öneri
  olursa kapının hedefsiz planda nasıl uygulanacağı önerinin parçasıdır; kuralın değişikliği zaten çıkış tasarımıdır.

Birincil hücreler: 9 tek yönlü varyant × 1 + `FM_09` × 3 = **12**. Kontrol ayrıca raporlanır.

Formasyon için 1d dilimi bu sürümde yoktur: ortak katalog bugüne kadar yalnız 15m–4h'de ölçüldü; 1d'de katalog davranışı
denetlenmeden bir varyant yazmak kuralı belirsiz bırakırdı.

### 6.4 Toplam

| Defter | Varyant | Birincil hücre |
|---|---|---|
| D4 | 13 | 15 |
| C4 | 9 | 27 |
| Formasyon | 10 (+1 kontrol) | 12 |
| **Toplam** | **32** | **54** |

## 7. Eşleştirilmiş plasebo (her varyant için)

Plasebo tek soruyu cevaplar: varyantın giriş sinyali, aynı süzgeçler, aynı stop geometrisi ve aynı çıkış altında rastgele
girişten fazla R katıyor mu? Plasebo işlemleri gerçek işlemlerle aynı simülatörden, aynı `DATA_END`, aynı bozuk bar kuralı ve
aynı fonlamayla (§5.2) geçer.

### 7.1 D4 ve Formasyon (gold_v1 tarzı, kendi sinyal oranında)

- `PLACEBO_<kimlik>`: aynı dilim, aynı yön, aynı çıkış kuralı, aynı süre sınırı, aynı risk aralığı.
- **Uygun barlar:** varyantın "plaseboya uygulanan süzgeçlerini" sağlayan, göstergeleri geçerli ve §4.2'nin temiz pencere
  şartını sağlayan barlar (D4'te ayrıca i ≥ 210). Karar anı karar penceresinin dışında kalan barlar (ısınma) plasebo üretmez.
- **Seçim:** her uygun bar için u = crc32("<sembol>|<dilim>|<kimlik>|<yön>|<barın açılış zamanı ms>") / 2³²
  (`signal_lab._h`); u < p ise plasebo olayı.
- **Oran p, dönem başına:** p = (varyantın bu seri, yön ve dönemdeki HAM sinyal sayısı) ÷ (aynı seri, yön ve dönemdeki uygun
  bar sayısı). Dönemler §8'deki keşif ve doğrulama dönemleridir; p ikisi için ayrı hesaplanır. Böylece plasebonun zaman
  dağılımı gerçeğinkine uyar. "Ham sinyal" tespit edilen ve varyantın bütün sinyal ve tespit anı süzgeçlerini sağlayan
  olaydır; risk aralığı, kovalama, `COST_STOP` ve simülasyon elemelerinden ÖNCE sayılır. p sonuçtan bağımsızdır.
- **Plasebo stop'u:**
  - D4: c[j] ∓ (varyantın ATR katı) × ATR14[j] (2, `D4_05`'te 3). Kural zaten ATR'ye dayalıdır.
  - Formasyon: `candle_lab` yöntemi. Stop = c[j] ∓ q × ATR14[j]; q (risk/ATR) bir gerçek isabetin değeridir.
    - **q havuzu:** AYNI sembol ve yöndeki (aynı serideki), varyantın KENDİ ham isabetleri (risk aralığı, kovalama ve
      `COST_STOP`'tan ÖNCE), sinyal barı i < j olanlar. Havuz serinin yüklenen ilk barından başlar; 2022 ısınma yılının
      isabetleri de girer (karar penceresinde işlem olmasalar da j'den öncedirler, geleceğe bakmaz). q = (teyit kapanışı −
      kayıt stop'u) ÷ ATR14[i], yönle işaretli.
    - Seçim zaman damgasıyla belirlenimcidir (`_h(sembol, dilim, kimlik, "q", ts)`). Havuz boşsa plasebo atlanır
      (`NO_REAL_RISK`, sayılır).
    - Gerekçe: üç mumun şekli stop mesafesini belirler; stop kuralını rastgele bara uygulamak plaseboya farklı stop geometrisi
      verirdi.
  - Formasyon plasebosunun tetiği karar barının kapanışıdır (kovalama ölçüsü).
- `COST_STOP` süzgeci plaseboya girişte, gerçek olaydaki gibi uygulanır.
- **Beklenen etki (önceden yazılır; sayım değil, önceki koşunun kaba ölçeği):**
  - p tanımı gereği plasebo sayısı kabaca ham sinyal sayısına eşittir; eksi `NO_REAL_RISK`, risk aralığı ve `COST_STOP`
    elemeleri.
  - `FM_00`: önceki koşu 30 coinde keşifte 1.412, doğrulamada 736 işlem verdi. 23 coin ve bu dönemlerle dönem başına birkaç yüz
    işlem beklenir.
  - `NO_REAL_RISK`: q havuzu 2022'den başladığı için 2022 öncesi listelenen coinlerde ihmal edilebilir (< %2) beklenir.
    2022'den sonra listelenenlerde (APT, ARB, OP, SUI) ilk aylarda daha yüksektir.
  - `FM_04` ve özellikle `FM_09` SHORT (üç kara karga + RSI < 30 + `BTC_DOWN`) seyrek beklenir. Gerçek ya da plasebo
    örneklemi 20'nin altında kalırsa plaseboya göre karşılaştırma yoktur (`vs_placebo` None), hüküm GÜÇLÜ ADAY olamaz ve hücre
    hedefi karşılayamaz (fail-closed). Bu beklenen bir sonuçtur, sürpriz sayılmaz.
  - Gerçek sayılar (hücre başına plasebo n, `NO_REAL_RISK` payı) raporlanır.

### 7.2 C4 (candle_lab'ın kendi eşi, değişmeden)

- Her CV için `candle_lab.variation_events`'in `PLACEBO_<id>` olayları: aynı yön, aynı DSL bağlamı ve `prior_move`, aynı
  risk/ATR dağılımı (q havuzu: aynı serideki o CV'nin j'den önceki gerçek isabetleri), aynı çıkış ve risk aralığı.
- **Bilinçli sapma:** seçim olasılığı candle_lab'ın sabit `PLACEBO_P = 0,10` değeridir, varyantın kendi sinyal oranı
  değildir. Gerekçe: C4 eşi bağlam süzgecinden SONRA seçilir; kendi oranıyla seçilse plasebo örneklemi gerçek olaylardan çok
  küçük kalırdı. Oran bir ortalamayı yanlılaştırmaz, yalnız plasebonun aralığını daraltır. Bu, defterin kayıtlı laboratuvar
  sonuçlarıyla karşılaştırılabilirliği de korur.
- Tespit anı süzgeçleri (`BTC_UP` / `BTC_DOWN`) plaseboya karar barında, `COST_STOP` girişte, aynı tanımla uygulanır.
- Havuzlu ve tek yönlü hücrelerin plasebosu, hücredeki CV'lerin plasebolarının birleşimidir. **Plasebo tekilleştirilmez:**
  aynı sembolde aynı barda iki CV'nin plasebosu birlikte bulunabilir (her CV'nin seçimi kendi karmasıyla bağımsızdır). Gerçek
  taraf tekilleştirildiği için birleşimin CV karışımı gerçeğinkinden biraz farklıdır; bu bilinçli bir basitleştirmedir.

### 7.3 Karşılaştırma

- Gerçek hücre yalnız kendi eşleştirilmiş plasebosuyla karşılaştırılır (genel `PLACEBO_RANDOM`'a düşülmez).
- İki yönlü havuzlu hücrenin plasebosu iki yönün plasebolarının birleşimidir; her yönün plasebosu kendi p'siyle seçilir.
- Plasebolar kapasite modeline girmez. Fonlamalı hüküm için fonlanır (§5.2).
- **Bilgi:** `D4_00` ayrıca laboratuvarın özgün eşi `PLACEBO_TREND_DONCHIAN_20_10` (barların %1'i, rastgele yön, aynı çıkış) ile
  de karşılaştırılır; run 36150821072 ile kıyas içindir. Hükme girmez.

## 8. İstatistik ve hüküm (sonuçtan önce sabit)

- **Dönemler (sabit tarih, 2/3 değil):** keşif (IS) = karar anı 2023-01-01 ≤ t < 2025-01-01 (`1672531200000 ≤ t <
  1735689600000`); doğrulama (OOS) = 2025-01-01 ≤ t < 2026-10-01. Laboratuvarın `aggregate` fonksiyonu KULLANILMAZ (2/3 kesimi
  ve yön başına gruplaması yüzünden). İstatistik fonksiyonları (`r_stats`, `replicated`, `verdict`, `diff_ci`,
  `verdict_strict`) DEĞİŞMEDEN, hücrenin işlem kümesiyle doğrudan çağrılır.
- **Kümeleme günü:** gün = t_ms // 86.400.000 (karar anının UTC günü; laboratuvarın tanımı).
- **Havuzlu hücre istatistiği:** iki yönlü hücrede iki yönün işlemleri tek kümede birleştirilir; `r_stats` bu kümeyle, gün
  kümeli çağrılır. Plasebo kümesi, her biri kendi p'siyle seçilmiş yön plasebolarının birleşimidir. C4'te havuzlu hücre §6.2'nin
  tekilleştirilmiş kümesidir.
- **Plaseboya göre fark (`vs_placebo`):** `aggregate`'in kuralıyla. Plasebo kümesinin IS ve OOS işlem sayısı ikisi de ≥ 20 ve
  gerçek kümede iki dönemde de işlem varsa {IS, OOS} = gerçek ort.R − plasebo ort.R; değilse None. None iken hüküm GÜÇLÜ ADAY
  olamaz.
- **En az işlem:** IS ≥ 30, OOS ≥ 20 (`LabConfig.min_is`, `min_oos`); azsa VERİ AZ.
- **Aralıklar:** gün kümeli bootstrap, 1.000 tekrar, laboratuvarın tohumları (`r_stats` 20260925, sıkı kural 20260926 /
  20260927).
- **Hüküm kelimeleri:** GÜÇLÜ ADAY / ZAYIF İZ / KAYBETTİRİR / KANIT YOK / VERİ AZ (laboratuvarınki). **SIKI GÜÇLÜ ADAY =
  `signal_lab.STRICT_RULE_TR`**: standart GÜÇLÜ ADAY'ın bütün şartları ve doğrulama döneminde (gerçek ort.R − eşleştirilmiş
  plasebo ort.R) farkının gün kümeli %95 aralığının alt ucu > 0.
- **İki sürüm:** hüküm fonlamasız R ile ve fonlamalı R ile (gerçek ve plasebo ikisi de fonlamalı) ayrı ayrı verilir.
- **Birincil hücreler:** her varyant için bağlam "HEPSİ"; iki yönlü varyantlarda iki yön birlikte, yalnız LONG ve yalnız
  SHORT. Bağlam dilimleri (hacim, RSI, trend, volatilite) yalnız bilgidir.
- **Bilgi amaçlı ek ölçüler:** maliyetin R içindeki payı (`cost_r`), çıkış nedeni dağılımı, ortalama tutma, örtüşmesiz
  ortalama R (`candle_lab.non_overlap_mean_r`), sansür payı, bilgi evreninde (§4.3) hücre sonucu, %1 risk, K = 3, fonlamasız
  aylık ölçü, bağlam dilimleri, sıralama duyarlılığı (§8.2).
- **Bilgi amaçlı ölçüler hiçbir zaman öneri dayanağı olamaz.** Yalnız §8.3'ün %0,5 riskli, birincil evrendeki kararı ve §8.5'in
  basamakları sayılır.

### 8.1 Aylık hedef ölçüsü

- **Ay (birincil tanım): işlemin GERÇEKLEŞTİĞİ (çıkış) ay.** Kâğıt defter PnL'i kapanışta yazar; bakiye böyle hareket eder.
  Çıkış ayı, çıkış barının `open_time`'ının UTC ayıdır: STOP ve HEDEF'te isabet barı, kural çıkışında çıkışın yapıldığı açılışın
  barı (k+1), süre sınırında ve `DATA_END`'de son bar. 4h ve 1d barlar UTC gece yarısına hizalı olduğu için bar ay sınırını
  aşmaz. `DATA_END` işlemleri bu yüzden 2026-09'a yazılır.
- **Dönem sınırları:** keşifte açılıp doğrulamada kapanan işlem, kapandığı doğrulama ayına yazılır (defter böyle görür).
  Doğrulama ayları 2025-01 … 2026-09 (**21 ay**), keşif ayları 2023-01 … 2024-12 (24 ay). İşlemsiz ay 0 sayılır.
- **Aylık net %:** o ayda kapanan işlemlerin Σ (w × R) × 0,5. 0,5 = işlem başına risk yüzdesi (`learning_mode.risk_per_trade_pct`).
  w işlemin risk payıdır: kapasitesiz ölçüde 1, kapasiteli ölçüde §8.2'deki değer. Fonlamalı sürümde R yerine R + `funding_r`
  (fonlama da işlemin çıkış ayına yazılır; defter onu uzlaşma anında yazar, ay sınırını aşan uzun işlemlerde küçük bir kayma
  olur).
- **Bilgi amaçlı ikinci tanım:** günlük işaretlemeli (mark-to-market) özsermaye. Kapasiteli defterin açık pozisyonları her UTC
  gün sonunda son kapanmış barın kapanışıyla değerlenir; aylık değişim raporlanır. Ayrıca taslaktaki karar ayı ataması da
  bilgi olarak raporlanır. İkisi de hükme girmez.
- **Aralık:** doğrulama aylık ortalamasının ay kümeli bootstrap %95 aralığı: 21 aylık değer yerine koyarak yeniden örneklenir,
  10.000 tekrar, tohum `BOOK_MONTH_SEED = 20261005`, aralık %2,5 / %97,5 yüzdelikleri.
- Raporlanır: keşif ve doğrulama için ortalama aylık %, aralık, ≥ +%1 olan ay payı, işlemsiz ay sayısı, en kötü ay, aylık seri.
- Bileşik getiri yoktur. Taban, defterin başlangıç bakiyesidir (D4 ve C4: 200 USDT; Formasyon: 100 USDT). Canlı defter boyutu
  güncel özsermaye tabanıyla kurar; küçük PnL'de fark küçüktür.

### 8.2 Kapasiteli sürüm (defterin gerçek sınırları)

Her hücre kendi başına bir defter gibi koşar. Sınırlar bugünkü config'ten (`learning_mode` açık, canlıdaki gibi) alınır:

| Defter | Başlangıç bakiyesi E | Slot K | Kaldıraç tavanı | İşlem riski | Tek pozisyon tavanı |
|---|---|---|---|---|---|
| D4 | 200 USDT | 20 | 3 | %0,5 | %30 × kaldıraç (PAPER_RESEARCH `max_position_pct`) |
| C4 | 200 USDT | 20 | 3 | %0,5 | aynı |
| Formasyon | 100 USDT | 30 | 3 | %0,5 | aynı |

Ortak: marj rezervi %5, likidasyon tamponu 2 × stop mesafesi, bakım marjı 0,004, min-notional çıkarma tavanı %2
(`learning_mode.fit_size`, `HARD_CAP_PCT`, `strategy_paper.LEARNING_MMR`).

**Başlangıç:** defter 2023-01-01 00:00 UTC'de boş başlar ve `PRICE_END`'e kadar KESİNTİSİZ koşar. Keşifte açılan pozisyonlar
doğrulamaya taşınır; doğrulama ölçüsü bu kesintisiz defterden okunur.

**Kabul algoritması** (karar anlarının zaman sırasıyla):
1. Karar anı t'de önce çıkış anı ≤ t olan pozisyonlar kapanır, marjları serbest kalır. Çıkış anı: STOP ve HEDEF → isabet
   barının KAPANIŞI (bar içi an bilinmediği için kötümser); kural çıkışı → çıkışın olduğu açılış; süre sınırı → son barın
   kapanışı; `DATA_END` → `PRICE_END`.
2. **Sıra (nötr, belirlenimci):** t anındaki adaylar `signal_lab._h(sembol, t_ms, kimlik)` değerine göre küçükten büyüğe işlenir.
   Config sırası (bugünkü hacim sıralaması) sıralamada kullanılmaz: o, bugünün kazananlarına öncelik verirdi. C4'te aday zaten
   tekilleştirilmiştir. İki yönlü hücrede sembol başına tek pozisyon vardır.
3. Ret nedenleri sırasıyla: `SAME_SYMBOL` (o sembolde açık pozisyon var), `SLOTS_FULL` (açık pozisyon sayısı ≥ K),
   `TOTAL_RISK` (açık risk payları toplamı + yeni > %100 bakiye; bu sınırlarla bağlayıcı olmaz, yine de denetlenir), sonra
   `fit_size`'ın ret nedenleri (`LIQ_BUFFER_TOO_THIN`, `MIN_ORDER_CONFLICT`, `INSUFFICIENT_MARGIN`).
4. **Risk payı `learning_mode.fit_size` çağrılarak, DEĞİŞTİRİLMEDEN** hesaplanır: `equity = E`, `entry` = laboratuvarın girişi
   (sonraki açılış), `stop`, `slots = K`, `leverage_max = 3`, `risk_pct = 0,5`, `reserve_pct = 5`, `liq_buffer_mult = 2`,
   `mmr = 0,004`, `min_notional` / `qty_step` / `min_qty` sabitlenmiş filtre tablosundan, `price_for_step = entry`,
   `hard_cap_pct = 2`, `max_position_pct = 30`, `available_margin = E − Σ açık pozisyonların marjı`. Politika rezervi 0'dır.
   w = `fit.risk_usdt` ÷ (0,005 × E). Min-notional'a çıkarılan işlemde w 4'e kadar çıkabilir (%2 ÷ %0,5).
   - Örnek (çıkarma yokken): K = 20 ve L = 3 iken slot notional tavanı 0,95 × 200 ÷ 20 × 3 = 28,5 USDT ve w = min(1, 28,5 × s);
     s ≥ %3,51 tam risk, s = %2 → w = 0,57. Formasyon (E = 100, K = 30): tavan 9,5 USDT, w = min(1, 19 × s).
5. **Min-notional ve miktar adımı nötr değildir; iki işlem biçimi birlikte raporlanır:**
   - `BUMP` (canlı davranış): `min_notional_bump = True`. Tavanın altına düşen emir, risk ≤ %2 ve marj serbestse en küçük
     emre çıkarılır (w ≤ 4); değilse `MIN_ORDER_CONFLICT` ile reddedilir.
   - `NO_BUMP`: `min_notional_bump = False`. Çıkarma gerektiren her işlem reddedilir.
   - §8.3 şartları iki biçimin KÖTÜ olanında aranır.
   - **Filtre tablosu:** sembol başına `min_notional`, `qty_step`, `min_qty`. Kaynak: botun kendi Binance exchangeInfo önbelleği
     (VPS `data/symbol_filters.json`, `FiltersCache`, `verified_at` damgalı). Salt okunur alınır. `verified_at` ve sha256'sı
     çıktıya ve sonuç belgesine yazılır. Tablo koşucunun girdisidir, kural değildir; kaynağı ve alma kuralı burada
     sabittir.
   - Bugünkü filtreler geçmiş fiyatlara uygulanır; filtrelerin tarihçesi arşivde yoktur. Bu bir yaklaşıklıktır.
   - Tablo koşudan önce alınamazsa kapasiteli ölçü ve §8.3 "FİLTRE BEKLİYOR" olur, hedef hükmü verilmez. Tablo sonradan
     yalnız bu kaynaktan alınabilir; kapasite bölümü aynı kodla yeniden koşar (deneme olarak kaydedilir). Diğer sonuçlar
     etkilenmez.
6. Kabul edilen işlem slotu ve marjı çıkış anına kadar tutar. Aylık katkısı 0,5 × w × R (fonlamalı sürümde R + `funding_r`),
   çıkış ayına (§8.1).

**Bilinçli basitleştirmeler:**
- Stop mesafesi kaymasız girişten ölçülür (defter kaymalı dolumdan ölçer; fark baz puan düzeyinde).
- Kullanılabilir marj PnL ile değişmez (bileşik yok); politika rezervi ve taban görünümü kapıları modellenmez.

**Modellenmeyen canlı kapılar** (`record_selectivity` kipinde bunlar girişi yalnız-kayda çevirir, yani canlıda işlem AÇILMAZ;
geriye dönük test hepsini dışarıda bırakır, etkinin yönü bilinmez):
- Formasyon: `COOLDOWN_AFTER_LOSS`, `THIN_DEPTH` / `LIQUIDITY_UNKNOWN` / `DEPTH_UNKNOWN`, `RR_BELOW_MIN_AT_ENTRY` /
  `RR_BELOW_MIN_AFTER_ROUNDING` (`min_rr_after_cost` 1,0), yapı politikası (`structures: pattern_trader: ENFORCE`;
  `PATTERN_ALREADY_USED`, `STRUCTURE_*`).
- RiskEngine (üç defter): `DAILY_LOSS`, `WEEKLY_LOSS`, `MAX_DRAWDOWN`, `CONSEC_LOSS_COOLDOWN`, `SYMBOL_COOLDOWN`,
  `KILL_SWITCH_ACTIVE`, `SPREAD`.
- Üç defter: 60 dakikalık giriş penceresi (`entry_window_min`): tur geç kalırsa sinyal kaçar.

**Raporlanır:**
- Kabul edilen ve reddedilen sinyal sayıları (nedene göre, `BUMP` ve `NO_BUMP` ayrı) ve çıkarılan işlem sayısı.
- Eşzamanlı açık pozisyonun en çoğu ve zaman ağırlıklı ortalaması; w dağılımı.
- **Bilgi:** öğrenme modu kapalıyken defterin taban sınırı olan K = 3 ile aynı hesap.
- **Bilgi:** aynı t'de sıralama config sırasıyla yapılsaydı aylık ölçünün ne kadar değiştiği (sıralama duyarlılığı).

### 8.3 Hedefi karşılıyor

Bir hücre **HEDEFİ KARŞILIYOR** ancak birincil evrende bunların HEPSİ doğruysa:

1. Sıkı hüküm GÜÇLÜ ADAY (`verdict_strict`), **fonlamasız R ile ve fonlamalı R ile ayrı ayrı** (fonlamalıda gerçek ve plasebo
   ikisi de fonlamalı).
2. Kapasiteli doğrulama ortalama aylık net % ≥ +1,0, fonlamalı ve fonlamasız iki sürümde de (yuvarlanmamış değerle).
3. Kapasiteli, fonlamalı doğrulama aylık ortalamasının ay kümeli %95 aralığının alt ucu > 0 (§8.1). Gerekçe: tek başına nokta
   tahmini belirsizlik taşımaz ve §8.4 tesadüfen birkaç sıkı aday bekler.
4. Kapasiteli, fonlamalı keşif aylık ortalaması > 0. Gerekçe: yalnız doğrulamadaki şanslı bir seri geçemesin.
5. 2.–4. şartlar `BUMP` ve `NO_BUMP` biçimlerinin kötü olanında sağlanır (§8.2). Filtre tablosu yoksa: "FİLTRE BEKLİYOR".
6. Kapasitenin KABUL ettiği doğrulama işlemlerinde NaN fonlama payı (doldurmadan önce) ≤ %5. Aşarsa hücre "FONLAMA EKSİK"
   diye işaretlenir ve hedefi karşılayamaz.
7. Hücre "SANSÜR YÜKSEK" (§5.1) ya da "VERİ BOZUK" (§4.2) değildir.
8. İki yönlü havuzlu hücrede ayrıca iki yönden hiçbirinin hükmü (fonlamasız ve fonlamalı) KAYBETTİRİR değildir.

Tek yönlü hücrenin kapasiteli ölçüsü yalnız o yönün sinyalleriyle koşan defterdir; havuzlu hücreninki iki yönle birlikte koşan
defterdir.

### 8.4 Çoklu test

- 54 birincil hücre. Hücreler bağımsız değildir (aynı defterin varyantları sinyallerin çoğunu paylaşır); etkin deneme sayısı
  daha küçüktür.
- Laboratuvarın rastgele yürüyüş ölçümünde hücrelerin %0–0,5'i tesadüfen GÜÇLÜ ADAY çıkar: 54 hücrede tesadüfen **0–0,3**
  aday beklenir.
- Ortak sürüklenmeli, avantajsız sentetik gruplarda (yükselen piyasada LONG) standart kural ~%25 yanılır, sıkı kural %10'dan
  azını geçirir (`tests/test_signal_lab_strict.py`). Kriptonun 2023–2026 yükselişi düşünülürse kötü durumda tesadüfen
  **en çok ~5** sıkı aday görülebilir.
- **Çokluk denetimli test (ön kayıtlı):**
  - Her birincil hücre için doğrulama dönemindeki (gerçek ort.R − eşleştirilmiş plasebo ort.R) farkı, fonlamasız R ile,
    `diff_ci` ile aynı düzende bootstrap edilir: iki taraf ayrı, gün kümeli, taraf başına B = 20.000 tekrar, tohumlar
    `BOOK_MT_SEED = 20261006` (gerçek) ve 20261007 (plasebo).
  - Tek yönlü p değeri: p = (1 + #{fark ≤ 0}) ÷ (B + 1). Plaseboya göre karşılaştırması olmayan hücrede (taraflardan birinde
    gün < 5) p = 1.
  - 54 p değerine Holm adım adım düzeltmesi, aile hatası α = 0,05: p'ler küçükten büyüğe p(1) ≤ … ≤ p(54); p(k) ≤ 0,05 ÷
    (54 − k + 1) olduğu sürece H(k) reddedilir, ilk başarısızlıkta durulur. Reddedilen hücre **ÇOKLU TESTİ GEÇTİ**.
  - HEDEFİ KARŞILIYOR ama çoklu testi geçmeyen hücrenin durumu **"ÇOKLU TESTTE ELENDİ"**dir. Öneri olamaz; yalnız ileriye
    dönük veriyle yeni bir ön kaydı (book_v2) gerekçelendirebilir.
- **Aday oranı kuralı:** gerçek hücrelerin aday oranı ile eşleştirilmiş plasebo gruplarının aday oranı (laboratuvarın
  `candidate_rate` tanımı: VERİ AZ olmayanlar içinde iki dönemde aralık alt ucu > 0 ve keşif ortalaması > 0 olanların payı)
  54 birincil hücre ve 54 plasebo grubu üzerinde hesaplanır. Gerçek oran ≤ plasebo oranı ise geçen her hücre **"TESADÜFLE
  AÇIKLANABİLİR"** diye işaretlenir ve öneri olamaz.
- Hedefi karşılayan bir hücre bulunursa yanında hangi defterin kaç varyantından biri olduğu yazılır.

### 8.5 Öneriye giden basamaklar

Bir hücre ancak bunların HEPSİNİ geçerse **ÖNERİ ADAYI** olur (yine de yalnız yalnız-kayıt PAPER önerisi, §10):

1. HEDEFİ KARŞILIYOR (§8.3).
2. ÇOKLU TESTİ GEÇTİ (§8.4) ve "TESADÜFLE AÇIKLANABİLİR" değil.
3. Taban hücre (`D4_00`, `C4_00`, `FM_00`) ya da veri gözetlemeli hücre (`C4_07`) değil. Bunlar tek başına öneri dayanağı
   olamaz.
4. **PIT teyidi geçti.** Aynı kod (mühürlü, koşudan önce yazılmış PIT kipi) hücreyi §4.3'ün PIT evreninde koşar. Geçiş ölçütü
   (şimdiden sabit):
   - (a) kapasiteli doğrulama ortalama aylık net % ≥ +1,0, fonlamalı ve fonlamasız;
   - (b) fonlamalı R ile doğrulama (gerçek ort.R − eşleştirilmiş plasebo ort.R) nokta tahmini > 0;
   - (c) standart hüküm (fonlamasız ve fonlamalı) KAYBETTİRİR değil.
   - PIT kipinde kapasite modeli min-notional çıkarmasız (`NO_BUMP`, min-notional 0) koşar: listeden çıkarılmış semboller için
     filtre yoktur. Bu, PIT teyidini kapasite açısından biraz iyimser yapar; bilinçli bir seçimdir.
   - Teyit yapılamazsa "PIT DOĞRULANAMADI"; ölçüt sağlanmazsa "PIT TEYİDİ GEÇMEDİ". İkisinde de öneri yoktur.

## 9. Ana bot: arşivden yeniden oynatılamaz, proxy kurulmaz

### 9.1 Neden yeniden oynatılamaz

Ana botun giriş kararı (`engine_v3.tour` → ajanlar → coin başkanları → ekonomik kapı → şef → kapılar → risk motoru) şu
girdilere dayanır. Bunların bir kısmı arşivde yoktur, bir kısmı botun kendi geçmişine bağlıdır:

| Girdi | Nerede | Arşivden üretilebilir mi |
|---|---|---|
| Eski ajanların seviye planı (destek/direnç, geri çekilme/kırılım) | `agents/technical.py`, `agents/market.py`, `agents/analog.py` → `CoinBrief.plan` | Kısmen (mumlardan), ama analog ajan botun geçmiş işlem hafızasına bakar |
| Coin başkanı uzlaşısı, uzman faktörleri, kırmızı takım | `coinhead/head.py`, `specialists.py`, `redteam.py`, `factors.py` | Canlı ticker, spread, derinlik, veri hükmü ve haber girdileri arşivde yok |
| Öğrenilmiş `p_win` | `learn/learner_v2.py` (Platt, yarı ömür 60 gün), kendi kapanışlarıyla eğitilir | Hayır: botun kendi geçmiş işlemlerine bağlı, yol bağımlı |
| Ekonomik kapı (`opportunity.hierarchical_expectancy`) | `opportunity.py`, `engine_v3._assess_opportunities` | Hayır: hiyerarşik hafızadan (botun sonuçları) beslenir |
| Benzer desen kanıtı, ortak deneyim danışmanı | `SimilarPatternEngine`, `shared_experience/advisor*.py` | Hayır: botun kayıtları |
| Haber | `NewsStore` (`state/news.jsonl`) | Hayır; ama bugün yalnız gözlemdir (`news_catalyst` uzmanı bias'ı 0'da tutar, kapıya girmez) |
| Canlı veri anlık görüntüsü | `runner.live.snapshot` (ticker, spread, derinlik) | Hayır (emir defteri arşivde yok) |
| Mum vetosu, rejim kapısı, yapı kataloğu, kaldıraç kapısı | `candle_confirmation`, `regime_gate`, `structures`, `decision_gates` | Evet (mumlardan), ama yalnız zincirin bir parçası |
| Öğrenme modu geçersiz kılmaları ve yalnız-kayıt sınıflaması | `learning_mode.py` (2026-09-28, 2026-10-03) | Kod sürümüne bağlı |
| LLM politikası | `llm/` | Motorda bağlı değil: `_write_llm_status` "motorda kurulu LLM servisi yok" yazar. Engel değil, ama danışman alanları boş |

Ayrıca kod ve config 2026-08'den beri sık değişti (ekonomik kapı, kapı sırası onarımı, mum vetosu, rejim kapısı, yapı
ENFORCE, öğrenme modu, yalnız-kayıt girişleri). Deponun kendi denetimi de aynı sonuca varır: `learn/entry_replay.py`
(`entry_replay_v1`) üretim kayıtlarında `NOT_REPLAYABLE` döner. Kapanmış işlem sadakat koşumu (`replay/fidelity`) yalnız
çıkış ve muhasebeyi yeniden üretir, girişi değil.

**Karar:** arşiv OHLCV ve fonlamasıyla ana bot sadakatle yeniden oynatılamaz. Bu çalışmada ana bot için bir proxy kurulmaz
ve hiçbir hücre "ana bot" diye adlandırılmaz. Kod yolundan harfiyen alınabilecek tek parça (ATR planı:
`CoinHead._plan_from_atr`, stop 2,5 × ATR%, hedefler 2R/3R) giriş ANINI belirlemez; giriş anını uzlaşı, öğrenici ve
kapılar belirler. Bu yüzden "geri çekilme proxy" ailesi de eklenmez.

### 9.2 Soruyu cevaplayacak VPS verisi

Ana bot için doğru veri VPS'te zaten birikiyor (`state/` altında):

| Dosya | İçerik | Kullanım |
|---|---|---|
| `futures_ledger.json` | gerçek işlemler (`history[]`, `fills[]`, `features`, `meta.learning`) | gerçekleşen net R, öğrenme etiketi (politika / açılan ekstra) |
| `entry_snapshot.jsonl` (+ arşiv) | karar anındaki aday kayıtları: `p_win`, `gross_expectancy_r`, `opportunity`, vetolar, stop, özellikler | kalibrasyon, sıralama gücü, kapı ayrıştırması |
| `counterfactual_trades.json` | açılmayan geçerli sinyallerin "olsaydı" kayıtları ve net etiketleri (`net_outcome`, v2+), `LEARNING_RECORD_ONLY` dahil | seçicilik değerinin ölçümü |
| `shared_experience/experience.jsonl` | `xp_entry` / `xp_outcome` / `xp_cf` satırları ve girişteki durum (4h trend, oynaklık, BTC) | rejim ve durum ayrıştırması |
| `decision_journal.jsonl`, `entry_outcomes.jsonl`, `position_path.jsonl`, `learned_closes.jsonl` | karar ve sonuç kayıtları, çıkış yolu | sayım ve çapraz denetim |
| `learning_mode.json` | `learning_mode_since` (öğrenmenin ilk aktif anı; kod 2026-09-28'de devreye girdi, kesin an dosyadan okunur ve rapora yazılır) | betimsel öncesi/sonrası ayrımı |

### 9.3 Sonra yazılacak salt okunur VPS betiği (ön kayıtlı sorular)

`scripts/main_book_audit.py` (şema `main_book_audit_v1`). Sözleşme `scripts/box_cf_gap_audit.py` ile aynıdır: ağ yok;
`--state` içine asla yazmaz; tek çıktı `--json-out` (state içinde ya da girdiyle aynıysa çıkış kodu 2); `.jsonl` satır satır
okunur; büyük JSON boyut denetimli; bootstrap'lar belirlenimci (`random.Random(--seed + bölüm)`); çıkış kodları 0 / 2 / 3.

Ön kayıtlı sorular (betik bunları ölçer; karar değiştirmez):

1. **Q1 — gerçek işlemler (tek hüküm):**
   - **Görülmemiş veri:** yalnız bu belgenin commit anından SONRA açılan ve bakış anına kadar kapanan ana bot işlemleri. Bugüne
     kadarki 163 işlem (−0,07R) görüldü; yalnız betimsel raporlanır, hükme girmez.
   - **Nüfus:** ana defterde gerçekten açılan bütün işlemler (politika işlemleri + açılan öğrenme ekstraları; bakiye bunlarla
     hareket eder). Politika ve ekstralar ayrıca betimsel raporlanır. Yalnız-kayıt girişler (`LEARNING_RECORD_ONLY`
     karşı-olgusalları) dışarıdadır.
   - **Ölçü:** net R (ücret, kayma, fonlama dahil), gün kümeli %95 aralık. Aylık net % = penceredeki gerçekleşmiş net PnL ÷
     pencere başındaki defter özsermayesi × 100 × 30,4375 ÷ pencere gün sayısı. Kısmi aylar böylece gün oranıyla girer.
   - **Bakış takvimi:** tek bakış **2027-01-01 00:00 UTC**'de (pencere: commit anı → bakış anı).
   - **Hüküm ölçütü:** n ≥ 30 ve aralığın alt ucu > 0 ve aylık net ≥ +%1. Aksi halde "kanıtlanmadı". n < 30 ise "VERİ AZ"
     (kanıtlanmadı). O zaman 2027-04-01 00:00 UTC'de ikinci ve son bir bakış yapılabilir. Her ek bakış deneme sayısına eklenir
     ve raporda sayılır.
   - Pencere içinde ana botun giriş kodunu ya da config'ini değiştiren sürümler raporda listelenir. Hüküm belirli bir kod
     sürümü için değil, işletildiği hâliyle defter içindir.
2. **Q2 — seçicilik:** etiketli karşı-olgusallar ret nedenine göre (ekonomik kapı, mum vetosu, rejim, yapı, kapasite,
   `LEARNING_RECORD_ONLY`): ortalama net R ve aralık; kapıyı geçen ve geçmeyen adayların farkı. Soru: kapılar zararı
   azaltıyor mu? **Not:** karşı-olgusal etiketler botun kendi dolum varsayımlarıyla üretilir (bar üzerinde stop/hedef
   dokunuşu, botun maliyet modeli); gerçek dolumla aynı değildir.
3. **Q3 — olasılık:** `p_win` kalibrasyonu (Brier, 5 kovalı güvenilirlik), sıralama gücü (Spearman; üst ve alt üçte bir
   farkı) gerçekleşen ve etiketli sonuçlara karşı.
4. **Q4 — yön ve rejim:** LONG/SHORT ve BTC rejimine (§5.3 tanımı) göre ayrışım.
5. **Q5 — kapasite:** slot doluluğu ve kapasite ret sayıları; kapasite yüzünden kaçan sinyallerin etiketli sonucu.

Q2–Q5 açıklayıcıdır ve deneme sayılmaz; yalnız Q1 bir hükümdür. Betik bu belgenin commit'inden sonra yazılır ve ayrı bir
incelemeden geçer. Örneklem küçük olabilir: Q1'in sonucu büyük olasılıkla "kanıtlanmadı" olur ve bu bir başarısızlık
sayılmaz.

## 10. Sonuçtan sonra ne olur

- **Ayarlama yok.** Sonuçtan sonra hiçbir kural, eşik, dilim, süzgeç, evren ya da dönem değiştirilmez. Değişiklik book_v2,
  yeni ön kayıt ve yeni deneme demektir.
- **Hiçbir hücre ÖNERİ ADAYI olmazsa:** sonuç açıkça yazılır ("bu defterler ve önceden yazılmış varyantları maliyet sonrası,
  kapasite içinde +%1/ay vermedi" ya da hangi basamakta elendikleri). Defterler değişmez; kapatma ya da değiştirme kararı
  sahibindir.
- **ÖNERİ ADAYI olursa:** yalnız PAPER, yalnız-kayıt ("olsaydı") bir defter önerisi olarak sahibe sunulur. Sahip onayı olmadan
  hiçbir şey açılmaz, hiçbir config değişmez. Öneri şunları içerir: kuralın tam tanımı, mühür, bu çalışmanın sayıları, gereken
  ek protokol değişiklikleri (ör. §6.3'teki R/R kapısı) ve ileriye dönük değerlendirme ölçütü.
- **İleriye dönük ölçüt (gerçek örneklem dışı):** önerilen varyantın yalnız-kayıt sonuçları en az 90 gün ya da 30 kapanmış
  işlem birikince (hangisi sonra gelirse) raporlanır; ortalama net R, gün kümeli aralık ve aylık %. Bu rapor da hükmü
  kendiliğinden değiştirmez; sahibin kararına girdi olur.
- **Raporlama:** sonuçlar `docs/BOOK_RESEARCH_V1_RESULTS.md`'ye yazılır:
  - 54 hücrenin standart ve sıkı hükmü (fonlamasız ve fonlamalı), plaseboya göre fark ve aralığı, Holm p değeri ve sonucu;
  - kapasiteli ve kapasitesiz aylık ölçü (keşif ve doğrulama; fonlamalı/fonlamasız; `BUMP` / `NO_BUMP`);
  - sansür payı, NaN fonlama payı, plasebo n ve `NO_REAL_RISK` payı;
  - dönem başına coin sayıları, veri kalitesi, bayt özetleri, koşu denemeleri kaydı;
  - kontrol hücresi, aday oranları, her hücrenin §8.5 durumu.

## 11. Uygulama planı (bu belgeden sonra)

1. Bu belge commit edilir (kurallar donar). O ana kadar araştırma verisi üzerinde hiçbir sinyal sonucu hesaplanmaz.
2. `tradingbot/book_lab.py`: `BOOK_REGISTRY` (bütün varyant tanımları, süzgeçler, plasebo, istatistik, çoklu test, kapasite,
   aylık ölçü, sabit veri pencereleri, evrenler, PIT kipi, ana bot kapsamı, sonuç metinleri, `readings_tr`) ve
   `BOOK_REGISTRY_SHA` = sha256(json.dumps(BOOK_REGISTRY, sort_keys=True, ensure_ascii=False))[:16]. Olaylar
   `signal_lab.Event`; istatistik ve hüküm `signal_lab` fonksiyonlarıyla değişmeden; C4 tespiti `candle_lab` / `candle_dsl` ile;
   boyut `learning_mode.fit_size` ile. Defter modüllerine, `candle_variations.VARIATIONS`'a ve config'e dokunulmaz.
3. `scripts/book_lab.py` (koşucu) ve `tests/test_book_lab.py`:
   - mührü sabitleyen test ve varyant tanımlarının birim testleri (sentetik seri);
   - taban varyantların defter/laboratuvar fonksiyonlarıyla eşleşmesi (D4: `donchian_trend`; C4: `candle_lab.variation_events`;
     Formasyon: adım 1'de `catalog_events` + RSI);
   - simülatörün sansürsüz işlemlerde `simulate` / `simulate_rule` ile paritesi ve `DATA_END` örnekleri;
   - C4 tekilleştirme sırasının örnekleri (§6.2) ve kapasite algoritmasının elle hesaplanmış örnekleri (`BUMP` / `NO_BUMP`
     dahil);
   - geleceğe bakış yokluğu (önek değişmezliği; Formasyon için adım 1);
   - sabit pencere kırpması, bayt özetleri;
   - sentetik veriyle uçtan uca kuru koşu.
4. İki bağımsız inceleme (ön kayda uygunluk; geleceğe bakış, hesap ve güvenlik). Bulgular düzeltilir.
5. `readings_tr`, PIT dışlama listesi ve mühür bu belgeye ve kayda yazılıp commit edilir. Filtre tablosu VPS'ten salt okunur
   alınır (§8.2).
6. Ancak bundan sonra veri indirilir: yalnız `data.binance.vision` arşivinden, sabit pencerelerle. Kapsam ve kalite sayımları
   (§0.4) yapılır. Bu aşamada kural değişmez; eksik coin dışarıda kalır ve raporlanır.
7. Dondurulmuş koşu bulutta. Koşu sırasında kod ve belge değiştirilmez; her deneme kaydedilir (§0).
8. HEDEFİ KARŞILAYAN hücre varsa aynı kodla PIT teyidi koşar (§8.5).
9. Sonuç belgesi ve sahibe sade Türkçe rapor.

## 12. Kapsam dışı

- Hiçbir defter, strateji, ajan, config ya da çalışma zamanı değişmez. VPS'e hiçbir şey gitmez (VPS'ten yalnız salt okunur
  filtre tablosu alınır).
- T2, M2 ve Box bu çalışmada yoktur (M2 tek pozitif defterdir; Box ayrı ölçüldü).
- Altın (XAUUSDT/PAXG) bu çalışmanın konusu değildir; ayrı bir sürümde (gold_v2) ele alınır.
- Ana botun kendisi bu çalışmada sınanmaz (§9); yalnız VPS analizinin soruları ön kayda alınır.

## 13. Deneme sayısı

book_v1: **32 varyant, 54 birincil hücre** (D4 13/15, C4 9/27, Formasyon 10/12) + 1 kontrol hücresi (deneme sayılmaz) + ana
bot için 1 ön kayıtlı VPS hükmü (Q1; her ek bakış +1). PIT teyidi yeni hücre değildir; aynı hücrenin ikinci, daha sert sınavıdır.

Defter ailelerinin bugüne kadarki dürüst toplamı (hücre):
- D4 ailesi: run 36150821072'nin 38 HEPSİ hücresi (dilimlerle en çok 532) + book_v1'in 14 yeni hücresi (12 yeni varyant;
  `D4_10` üç hücre).
- C4 ailesi: run 36277302747'nin 24 HEPSİ hücresi (8'i birincil) + book_v1'in 24 yeni hücresi (8 yeni varyant × 3).
- Formasyon ailesi: run 36123072720'nin 1.412 hücresi + book_v1'in 11 yeni hücresi (9 yeni varyant; `FM_09` üç hücre).

## 14. Eleştiriye yanıt

İlk taslak bağımsız bir eleştiriden geçti. Hüküm: "commit'e hazır değil". Hiçbir sonuç hesaplanmadı, araştırma verisi
indirilmedi. Zorunlu değişikliklerin hepsi uygulandı; reddedilen yoktur. Kısmen farklı çözülenlerin nedeni yazılıdır.

| # | Eleştiri | Yanıt |
|---|---|---|
| 1 | Pencere çalıştırma tarihiyle kayıyor; bayt kanıtı yok | Sabit ms sınırları (§4.1): 4h 2022-01-01, 1d 2020-01-01 (BTC ve coinler), `PRICE_END` 2026-10-01, fonlama 2026-10-02'ye kadar. `COIN_UP` tohumu yazıldı. Bayt kanıtı için sabit biçimli CSV metninin sha256'sı (gzip başlığı zaman damgası taşıdığından dosya baytları değil). Fark: coinlerin 1d serisi de 2020'den yüklenir (EMA200 tohumu 2023'e kadar sönsün) |
| 2 | Sağdan sansür yanlı; hüküm ile aylık ölçü farklı işlem kümeleri | `DATA_END` hem hükümde hem aylık ölçüde, gerçek ve plaseboda aynı (§5.1); `NO_FUTURE_DATA` kullanılmaz. 1d süre sınırı 300 günlük bar. Sansür payı raporlanır; doğrulamada > %10 → "SANSÜR YÜKSEK", hedef yok. `DATA_END` fonlaması tanımlandı (§5.2) |
| 3 | Evren geleceğe bakıyor | Yanlılık yönüyle yazıldı (LONG/trend lehine, SHORT aleyhine). Seçenek (b): zorunlu PIT teyidi; PIT evreni ve geçiş ölçütü şimdiden yazılı (§4.3, §8.5) |
| 4 | Kapasitede eşitlik sırası geleceğe bakıyor | Birincil sıra `_h(sembol, t_ms, kimlik)`; config sırası yalnız duyarlılık bilgisi (§8.2) |
| 5 | Birincil evren canlı defter değil | Birincil evren canlıda işlem açılan 23 coin (D4/C4 listesi = `V3_SYMBOLS` ∩ giriş evreni). 40 coin yalnız bilgi; orada geçen varyant evren değişikliği içerir ve öneri olamaz (§4.3) |
| 6 | Min-notional / miktar adımı nötr değil | `fit_size` değiştirilmeden çağrılır; botun kendi filtre önbelleği (VPS `data/symbol_filters.json`, `verified_at` + sha256). `BUMP` ve `NO_BUMP` birlikte; hedef kötü olanında aranır. Tablo yoksa "FİLTRE BEKLİYOR" (§8.2, §8.3) |
| 7 | Kapasite başlangıcı, modellenmeyen kapılar, FM_06 | Kesintisiz defter 2023-01-01'den. Modellenmeyen canlı kapılar listelendi (§8.2). `FM_06` çıkarıldı (§6.3); deneme sayısı 33/55 → 32/54 |
| 8 | Aylık atama | Birincil: çıkış (gerçekleşme) ayı. Bilgi: günlük işaretlemeli özsermaye ve karar ayı. Dönem sınırları ve `DATA_END` tanımlı (§8.1) |
| 9 | Nokta tahmini kapısı | Ay kümeli %95 aralığın alt ucu > 0 şartı eklendi; keşif ortalaması > 0 da eklendi (§8.3) |
| 10 | Çoklu test | 54 hücrede Holm (B = 20.000, sabit tohumlar); "ÇOKLU TESTTE ELENDİ" durumu öneri olamaz. Aday oranı kuralı: gerçek ≤ plasebo → "TESADÜFLE AÇIKLANABİLİR" (§8.4) |
| 11 | Plasebo oranı p belirsiz | Ham sinyal (elemelerden önce), seri × yön × DÖNEM başına (§7.1) |
| 12 | Formasyon q havuzu | Aynı seri, varyantın kendi ham isabetleri, `COST_STOP`'tan önce, 2022 ısınması dahil. `NO_REAL_RISK` ve plasebo n < 20 beklentisi ve fail-closed sonucu yazıldı (§7.1) |
| 13 | C4 tekilleştirme sırası | Tespitte tekilleştirme; tespit anı süzgeçleri önce, `COST_STOP` sonra ve düşmesiz; tek yönlü hücreler kendi içinde; plasebo birleşimi aynı bar tekrarlarını tutabilir (§6.2, §7.2) |
| 14 | Formasyon adım 3 geleceğe bakıyor | Formasyon hücreleri adım 1 ile koşar (§6.3) |
| 15 | Stop ifadesi | "Katalog kaydının stop'u, değiştirilmeden" (taban ve `FM_09`) (§6.3) |
| 16 | D4/Formasyon için boşluk ve bozuk bar | Temiz pencere şartı (210 / 300 / 500 bar; 1d süzgeçler için 200), boşluk geçen işlem satır satır, bozuk bar geçen işlem atılır ve > %1 → "VERİ BOZUK" (§4.2) |
| 17 | Fonlama | Şart 1 fonlamalı R ile de aranır (gerçek ve plasebo fonlamalı). NaN tutucu doldurulur (her 8 saat %0,01 ödeme); NaN payı kapasitenin kabul ettiği doğrulama işlemlerinde (§5.2, §8.3) |
| 18 | Önceki denemeler eksik | Hücre biriminde sayılar (§3, §13); kök neden, geri verme ve A–H aileleri eklendi; canlı işlemlerin doğrulama döneminde olduğu yazıldı; taban hücreler öneri dayanağı olamaz. **Kısmi:** run 36123072720'nin 1.412 sayısı yeniden sayılamadı (iş kaydı API'den okunamıyor); belgelerdeki sayı kullanıldı ve bu yazıldı. Run 36150821072'nin sayısı koşu argümanlarından ve koddan yapısal olarak çıkarıldı (38 HEPSİ, en çok 532) |
| 19 | Süreç disiplini | §0 eklendi; plan sırası buna göre (§11) |
| 20 | Ana bot Q1 görülmüş veride | Q1 commit sonrası işlemlerde; 163 işlem betimsel; tek bakış 2027-01-01, ek bakış sayılır; nüfus, kısmi aylar, `learning_mode_since`, Q2 etiketlerinin kaynağı yazıldı (§9.3) |
| 21 | Eksik varyant ayrıntıları | `D4_08` tam tanım; `VOL_OK` ve `VOL_OK_DSL` ayrımı; `open_time + 1 gün ≤ t`; havuzlu hücre istatistiği (§5.3, §6.1, §8) |

İsteğe bağlı öneriler:

| Öneri | Yanıt |
|---|---|
| `D4_00`'ı laboratuvarın özgün eşiyle de raporla | Uygulandı, bilgi (§7.3) |
| Keşif kapasite aylık ortalamasını raporla, > 0 iste | Uygulandı, §8.3 şart 4 |
| Bilgi ölçüleri öneri dayanağı olamaz | Uygulandı (§8) |
| Config sırası ile karma sırası duyarlılığı | Uygulandı, bilgi (§8.2) |
| `C4_07`'yi veri gözetlemeli işaretle | Uygulandı: deneme sayısında kalır (dürüst sayım), işaretlenir, tek başına öneri olamaz (§6.2, §8.5) |
| `NO_REAL_RISK` ve plasebo n beklentisi | Uygulandı, kaba ölçekle (§7.1). Hücre başına sayı verilmedi: sinyal saymak dondurulmuş koşudan önce yasaktır (§0.4) |
| ≥ 24 saat boşluk raporu ve yeniden adlandırmalar | Uygulandı (§4.2) |
| §2'deki kazanma sütunu | Sütun kaldırıldı (yalnız D4'te doluydu) |

## 15. Okunuşlar (`readings_tr`, mühre dahil)

Kod (`tradingbot/book_lab.py`) bu belgeye göre yazılırken belirsiz kalan yerlerin okunuşu. Hepsi hiçbir sonuç görülmeden ve araştırma dönemine ait hiçbir fiyat ya da fonlama verisi indirilmeden yazıldı. Kural metni değişmedi; okunuşlar kuralı gevşetmez. Kodda `READINGS_TR` olarak durur, mühre girer ve `tests/test_book_lab.py` her birinin burada harfiyen durduğunu denetler.

PIT dışlama listesi için `data.binance.vision` USDⓈ-M aylık kline klasörlerinin sembol ADLARI (S3 listesi, 2026-10-05) okundu. Fiyat, hacim ya da fonlama verisi indirilmedi; ayrıştırma denetimi için arşiv ayı da indirilmedi (denenen tek dosya, pencerelerin dışındaki BTCUSDT 1d 2019-12, arşivde yok: 404). Ayrıştırıcılar sentetik zip'lerle sınandı.

```text
1. anahtarlar: signal_lab._h ile; <sembol> 'BTC/USDT' biçimi (laboratuvar ve evren listesi yazımı), <dilim> '4h'/'1d', <kimlik> varyant kimliği (ör. D4_00_BASE; PLACEBO_ öneki YOK), <yön> LONG/SHORT, zaman damgası = barın AÇILIŞ zamanı (ms tamsayı)
2. karar anı t = sinyal barının open_time + dilim (laboratuvarın t_ms'i); dönem t ile: keşif 2023-01-01 ≤ t < 2025-01-01, doğrulama 2025-01-01 ≤ t < 2026-10-01; karar penceresi dışındaki sinyal (ısınma) işlem ve plasebo üretmez
3. pencere kırpması: barın open_time'ı [ilk bar sınırı, PRICE_END) aralığında; 4h 2022-01-01, 1d 2020-01-01 (coinler ve BTC); fonlama uzlaşma zamanı [2022-12-01, 2026-10-02); fonlama yalnız aylık fundingRate dosyalarından, 2022-12 … 2026-10 (arşivde günlük fundingRate klasörü yok, 2026-10-05 S3 listesi); PRICE_END sonrası uzlaşmalar 2026-10 dosyasındandır (ay dosyası ay bitince yayımlanır)
4. temiz pencere: bozuk bar = candle_lab.valid_ends tanımı (sonlu olmayan OHLC, h < max(o,c), l > min(o,c), sonlu olmayan ya da negatif hacim); boşluk = ardışık iki açılış farkı ≠ dilim; i'de biten w barlık pencere temiz = i−w+1..i barlarının hiçbiri bozuk değil VE pencere içindeki her ardışık çift boşluksuz (pencerenin ilk barından önceki boşluk sayılmaz; valid_ends ile aynı); D4 w = 210 (4h ve 1d), Formasyon w = 300, C4 valid_ends (500), BTC_UP/COIN_UP için 1d w = 200
5. BTC_UP/BTC_DOWN/COIN_UP: kullanılan 1d bar k = open_time + 1 gün ≤ t olan son bar; EMA200 = regime_gate.btc_regime ile aynı: serinin ilk barından k'ye kadar SONLU kapanışlar üzerinde regime_gate.ema_last (ilk 200 kapanışın SMA'sıyla tohum); close[k] > EMA → UP, değilse DOWN; close[k] sonlu değil, sonlu kapanış < 200, k'de biten 200 barlık 1d pencere temiz değil ya da k barı t'den önceki son 24 saatte kapanmamış (1d seride t'ye değen boşluk; bayat değer kullanılmaz) ise BİLİNMİYOR → giriş yok, plasebo yok; REGIME_FOLLOW = LONG için BTC_UP, SHORT için BTC_DOWN
6. VOL_OK = signal_lab.context'in volatilite kovası 'düşük' ya da 'normal' (ATR%/atr_med ≤ 1,25; tam seri); VOL_CONFIRM = hacim kovası 'yüksek(>1.5x)' (v[i] / önceki 20 bar ortalaması > 1,5); kova 'bilinmiyor' → geçmez
7. COST_STOP: s = |giriş − stop| / giriş, giriş = sonraki barın açılışı (kaymasız laboratuvar girişi); s ≥ 0,016 geçer (eşitlik geçer); geçmezse STOP_TOO_TIGHT_FOR_COST (gerçek ve plasebo aynı)
8. atlama sırası: NO_ENTRY_BAR (giriş barı yok), NO_ATR, STOP_TOO_CLOSE (risk ≤ alt × ATR), STOP_TOO_FAR (risk > üst × ATR), CHASE (yalnız tetikli olaylarda: s × (giriş − tetik) > 1 ATR), STOP_TOO_TIGHT_FOR_COST, sonra simülasyon ve BAD_BAR
9. ATR (risk aralığı ve kovalama): D4 ve Formasyon için sinyal barı i'deki laboratuvar ATR14'ü (tam seri), C4 için candle_lab'ın atr_sig'i (pencere ATR14'ü); plasebo için kendi barı j'deki aynı tanım
10. D4 SHORT (yalnız D4_10): giriş donchian_trend._fresh_breakout'un aynası (−close, −lo20); stop c[i] + 2 × ATR14[i]; çıkış c[k] > hi10[k]; hi55 = önceki 55 barın en yükseği (i hariç), aux_series'in kanal kuruluşuyla aynı
11. Formasyon: kayıtlar signal_lab.catalog_events (LabConfig stride=1, pencere 300) ile; LONG THREE_WHITE_SOLDIERS, SHORT THREE_BLACK_CROWS; RSI14 = signal_lab.indicators (tam seri) i'de; LONG RSI > 70 kesin, SHORT RSI < 30 kesin, NaN geçmez; tetik = kaydın trigger.level'ı (kovalama ölçüsü); stop = kaydın stop'u; ATR14[i] sonlu ve > 0 olmalı; tetiği olmayan kayıt sinyal değildir (canlı v3'ün RECORD_GEOMETRY_INCOMPLETE'i)
12. ham sinyal (plasebo oranı p ve Formasyon q havuzu): tespit + temiz pencere + ATR14[i] sonlu ve > 0 (+ D4'te i ≥ 210) + varyantın bütün sinyal ve tespit anı süzgeçleri (VOL_CONFIRM, BTC_UP/DOWN, COIN_UP, VOL_OK; Formasyon'da RSI); risk aralığı, kovalama, COST_STOP, NO_ENTRY_BAR ve BAD_BAR'dan ÖNCE
13. uygun bar (D4 ve Formasyon plasebosu): ATR14[j] sonlu ve > 0, temiz pencere (D4 210, Formasyon 300), D4'te j ≥ 210, varyantın plaseboya uygulanan tespit anı süzgeçleri (COST_STOP girişte), karar anı karar penceresinde; p = dönemdeki ham sinyal / dönemdeki uygun bar (uygun bar 0 → p 0); seçim u = _h(sembol, dilim, kimlik, yön, ts[j]) < p(dönem)
14. Formasyon plasebo stop'u: q = s × (close[i] − kayıt stop'u) / ATR14[i] (aynı seri ve yönde, varyantın kendi ham isabetleri, i < j, 2022 ısınması dahil); seçilen q = havuz[int(_h(sembol, dilim, kimlik, 'q', ts[j]) × havuz boyu)]; stop = c[j] − s × q × ATR14[j]; sonlu, > 0 ve koruyucu tarafta değilse BAD_STOP (sayılır); havuz boş → NO_REAL_RISK; tetik c[j]
15. C4: değişmiş tanımlar CV kimliğini KORUR (candle_lab'ın plasebo anahtarı PLACEBO_<CV> aynı kalır; varyantı hücre ayırır); çıkışı dışında aynı olan tanımlar (C4_02, C4_03) tabanın tespitini ve plasebosunu paylaşır (detect_last ve placebo_context çıkışı okumaz; testle denetlenir); simülasyon varyantın kendi target_r / max_hold_bars / risk_atr_bounds değeriyle
16. C4 tekilleştirme önceliği CV001 > CV002 > … > CV008 (config sırası); aynı sembol ve aynı karar barında tespit anı süzgecinden (rejim) geçen eşleşmelerden yalnız en öncelikli olanın olayı adaydır; yalnız-LONG/yalnız-SHORT hücreleri kendi yönlerinin CV'leri içinde, havuzlu hücre varyantın bütün CV'leriyle tekilleştirilir; elenen seçilenden sonra alttakine düşülmez
17. havuzlu hücrede yön hükmü (§8.3 şart 8) = aynı varyantın yalnız LONG ve yalnız SHORT birincil hücrelerinin hükmü (fonlamasız ve fonlamalı)
18. simülatör çıkışları: STOP/HEDEF signal_lab.simulate gibi (aynı barda ikisi → STOP; ilk bar dışında açılış boşluğu); kanal çıkışı simulate_rule gibi (k kapanışında tetik → k+1 açılışı); süre sınırında j+H−1 kapanışı; veri bitince son barın kapanışı: son barın kapanışı ≥ PRICE_END ise DATA_END, değilse DELISTED (seri erken bitti; sansür payına girmez, ayrıca sayılır)
19. çıkış anı (kapasite, fonlama doldurma, işaretleme): STOP ve HEDEF isabet barının kapanışı; kural çıkışı çıkış açılışı; süre sınırı, DATA_END ve DELISTED son barın kapanışı (tam seride PRICE_END); çıkış ayı = çıkış barının open_time ayı
20. boşluk geçen işlem: giriş barından çıkış barına kadar (giriş barının kendisinden önceki boşluk dahil) bir boşluk; süre = eksik zaman (açılış farkı − dilim) saat
21. BAD_BAR: giriş barından çıkış barına kadar (ikisi dahil) bozuk bar geçen işlem hükümden, aylıktan ve kapasiteden atılır; VERİ BOZUK = hücrenin gerçek işlemleri içinde BAD_BAR payı > %1 (bütün karar penceresi)
22. sansür payı = hücrenin gerçek işlemleri içinde DATA_END ile kapananların payı, dönem karar anına göre; doğrulamada > %10 → SANSÜR YÜKSEK
23. fonlama: futures_lab.funding_carry DEĞİŞMEDEN (DATA_END/DELISTED TIME gibi: son barın kapanışı); NaN doldurma = −0,0001 × giriş × ⌈tutma saati / 8⌉ / risk, tutma saati = (çıkış anı − giriş barının açılışı) / 1 saat; fonlamalı R = R + funding_r
24. fonlama NaN payı (§8.3 şart 6) = kapasitenin kabul ettiği ve çıkış ayı doğrulamada olan işlemlerde doldurmadan önceki NaN payı; BUMP ve NO_BUMP ikisinde de ≤ %5 olmalı (aksi FONLAMA EKSİK)
25. aylık ölçü: ay = çıkış barının open_time UTC ayı; keşif ayları 2023-01…2024-12 (24), doğrulama 2025-01…2026-09 (21); işlemsiz ay 0; aylık % = Σ (w × R) × 0,5; ortalama yuvarlanmamış değerle sınanır; ay kümeli aralık: rng = numpy default_rng(20261005), integers(0, ay, (10000, ay)), satır ortalamalarının np.quantile 0,025 / 0,975'i; keşif aralığı bilgi (aynı tohum)
26. kapasite: defter 2023-01-01'de boş başlar; karar anları sırayla; t'de önce çıkış anı ≤ t olanlar kapanır; aynı t'de adaylar _h(sembol, t_ms, kimlik) (kimlik = varyant kimliği) küçükten büyüğe; ret sırası SAME_SYMBOL, SLOTS_FULL, TOTAL_RISK (açık risk + nominal yeni risk 0,005 × E > E), sonra fit_size; adaylar BAD_BAR olmayan gerçek işlemler
27. kapasite filtre tablosu: botun FiltersCache JSON'u ('futures' bölümü; anahtar 'BTC/USDT' ya da 'BTCUSDT'); evrenin bir sembolü tabloda yoksa kapasite FİLTRE BEKLİYOR; PIT kipinde tablo yok: min_notional 0, adım yok, NO_BUMP
28. kapasite bilgileri: K = 3 ve config sırası duyarlılığı BUMP ile; config sırası = evren listesinin sırası (birincil evrende D4/C4 config listesi); günlük işaretleme ay sonlarında, açık pozisyon son kapanmış barın kapanışıyla gidiş-dönüş maliyetli, fonlamasız; eşzamanlılık [karar anı, çıkış anı) aralıklarıyla
29. hüküm: r_stats (1000 tekrar, gün = t // 86 400 000), vs_placebo = round(gerçek ort.R − plasebo ort.R, 4) (r_stats'ın yuvarlanmış ortalamalarıyla, aggregate gibi) yalnız plasebo IS ve OOS n ≥ 20 ve gerçek iki dönemde işlem varken; diff_ci iki dönem için hesaplanıp rapora yazılır, sıkı hüküm verdict_strict ile (yalnız standart GÜÇLÜ ADAY'da etkili)
30. çoklu test: signal_lab._day_boot_means (gerçek 20261006, plasebo 20261007, 20 000 tekrar, fonlamasız, doğrulama); gün < 5 → p = 1; p = (1 + #{fark ≤ 0}) / (B + 1); Holm 54 hücrede
31. aday oranı: fonlamasız; plasebo grubunun VERİ AZ ayrımı verdict(..., vs None) ile; oran = VERİ AZ olmayanlar içinde replicated ve keşif ort.R > 0 olanların payı (candidate_rate)
32. bilgi: D4_00'ın laboratuvar eşi = signal_lab.algo_events'in PLACEBO_TREND_DONCHIAN_20_10 LONG olayları, karar penceresinde, bu simülatörden (DATA_END dahil); temiz pencere uygulanmaz (özgün eş); bağlam dilimleri signal_lab.context kovalarıyla
33. PIT havuzu: S3 listesi (data/futures/um/monthly/klines/) adları; dışlama PIT_EXCLUDE (kurallar + adlar; adlara göre yapılan sınıflandırma belirsizlik taşır, seçilen semboller rapora yazılır); sıralama ay başı m'den önceki 30 günde (open_time ∈ [m − 30 gün, m)) 1d quote_volume toplamı, en az 30 bar, eşitlikte sembol adı; ilk 40
34. PIT eksik veri: evrendeki (ay, sembol) çifti eksik = o aydaki 4h bar sayısı < 0,90 × 6 × o aydaki 1d bar sayısı (1d bar > 0); eksik çift payı > %5 ya da liste alınamazsa PIT DOĞRULANAMADI; işlem karar ayının evrenindeyse sayılır
35. bayt kanıtı: penceredeki her satır '%d,%r,%r,%r,%r,%r\n' (open_time, açılış, yüksek, düşük, kapanış, hacim; Python repr) metninin sha256'sı; fonlama dosyası baytlarının sha256'sı
36. yeniden adlandırma: yalnız bugünkü sembolün arşivi kullanılır, eski sembolün geçmişi birleştirilmez; bilinen durum G/USDT (Galxe GAL → Gravity G, 2024; GALUSDT geçmişi kullanılmaz, G'nin ilk barı kalite raporunda görünür)
37. veri hatası → koşu durur, rapor yazılmaz, deneme ERROR; aynı kodla yeniden denenir (§0.6): bir sembolün mum ya da fonlama dosyası indirilemedi ya da okunamadı; BTC 1d serisi ya da birincil/bilgi evreninde bir coinin 4h serisi (ve yüklendiyse 1d serisi) PRICE_END'e ulaşmıyor (DELISTED yalnız PIT kipinde olur); PRICE_END'e ulaşan bir sembolün [PRICE_END, 2026-10-02) aralığında fonlama uzlaşması yok (2026-10 dosyası henüz yayımlanmadı); arşivde hiç verisi olmayan coin dışarıda kalır ve sonuçta yazılır (§11.6); PIT kipinde 1d serisi defter seçiminden bağımsız yüklenir (eksik çift denetimi) ve yüklenmemiş seçili sembolün her evren ayı eksik çift sayılır; aynı --out'taki önceki koşunun işlemleriyle birleştirme, yalnız-rapor ve PIT sonucunun birincil rapora yazılması aynı kod ağacını (git HEAD:tradingbot, HEAD:scripts) ve temiz çalışma ağacını ister; açık izinle yapılan birleştirme rapora yazılır
38. PIT dışlama listesi (tam; taban adları, sonuna USDT eklenir): endeks: BTCDOM, DEFI, FOOTBALL, BLUEBIRD; stabil: USDC, BUSD, TUSD, FDUSD, USDP, DAI, USDE, PYUSD, RLUSD, USD1, EUR, AEUR; emtia_doviz: XAU, XAG, XPT, XPD, XAUT, PAXG, COPPER, NATGAS, CL, BZ, USDBRL; hisse_etf_halka_arz_oncesi: AAOI, AAPL, ACN, ADBE, ALAB, AMAT, AMD, AMZN, ANET, ANTHROPIC, APLD, APP, ARM, ASML, ASTS, AVGO, AXTI, BABA, BITO, BMNR, BRKB, BYD, CBRS, CIEN, COHR, COIN, COST, CRCL, CRDO, CRM, CRWD, CRWV, CSCO, CSOPSAMSUNG2L, CSOPSKHYNIX2L, CVNA, CXMT, DDOG, DELL, DIS, DJT, DKNG, DRAM, EBAY, EWJ, EWY, EWZ, FLNC, GDX, GEV, GLW, GME, GOOGL, GTLB, HANA, HANMI, HIMS, HK0625, HK0700, HK0992, HK1810, HOOD, HPE, HUT, HYUNDAI, IBM, INTC, IONQ, IREN, IWM, JPM, KLAC, KODEX200, KUAISHOU, LGELECTRONICS, LITE, LLY, LRCX, MARA, MDB, MEITUAN, META, MINIMAX, MRK, MRNA, MRVL, MSFT, MSTR, MU, MUU, NAVER, NBIS, NFLX, NKE, NOK, NOW, NVDA, NVDL, NVO, OKLO, OPENAI, ORCL, PANW, PDD, PLTR, POPMART, PYPL, QCOM, QQQ, RDDT, RIVN, RKLB, SAMSUNG, SAMSUNGEM, SHOP, SKHY, SKHYNIX, SLX, SMCI, SMH, SNDK, SNOW, SOFI, SONY, SOXL, SOXS, SPCX, SPY, SQQQ, STRC, TEM, TENCENT, TQQQ, TSLA, TSLL, TSM, TTWO, TXN, TZA, UBER, UNH, UNITREE, URNM, UVXY, WDC, WMT, XBI, XLE, XOM, ZHIPU, ZHONGJI; ayrıca alt çizgili her sembol ve USDT ile bitmeyen her sembol
```
