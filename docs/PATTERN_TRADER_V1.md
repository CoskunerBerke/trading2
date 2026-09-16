# FORMASYON PAPER TRADER V1 (2026-09-16) — yeni listeleme öncelikli mum formasyonu defteri

Amaç: botun **sabit on coinle sınırlı kalmadan** Binance USDⓈ-M perpetual evrenini keşfetmesi, 15m/1h/4h kapalı
mumlarda formasyon ve piyasa yapısını sayısal olarak saptaması, **koşullu LONG/SHORT planlar** üretmesi, şartlar
gerçekleşince risk kontrolünden geçirip **AYRI bir PAPER defterinde gerçekten işlem açması**, pozisyonu yönetmesi
ve maliyet sonrası sonucu kaydetmesi.

**Bu sürüm bir kârlılık iddiası DEĞİLDİR.** Aile kuralları (A/B/C) sonuçlar görülmeden yazılmış, sürümlü
hipotezlerdir; `p_win` ÖLÇÜLMEMİŞTİR (`null`) ve rapor örneklem yetersizken hüküm vermez. Ana bot, T2 ve M2
defterleri/evrenleri/sermayeleri DEĞİŞMEZ; bu defterin kendi sanal bakiyesi ve kendi state dizini vardır.

## 1. Zincir ve modüller

| adım | modül | özet |
|---|---|---|
| keşif | `pattern_trader/universe.py` | Resmi USDⓈ-M `exchangeInfo`: yalnız `PERPETUAL` + `USDT` + `TRADING`. Sembol adından ürün türü tahmin EDİLMEZ. Yaş = `onboardDate` (futures ilk işlem); token doğumu / spot listeleme AYRI alanlardır ve bilinmiyorsa `null`. |
| veri | `pattern_trader/data.py` | `MarketFeed` ile artımlı indirme, kapanmamış bar düşürme, boşluk/tazelik; panelin okuduğu CSV önbelleği (`binanceusdm_<BASE>-<QUOTE>_<tf>.csv`). `PriceService`: perp mark + `verified_price` (T2/M2 ile AYNI sözleşme). |
| bulgu | `pattern_trader/detect.py` | Şekiller `learn.candle_context._shapes` (ikinci formül YOK), trend `detect_trend`, seviye `chart_analysis.pivots` + `equal_level_clusters`. Bulgu kimlikli kanıt nesnesidir; durum sonraki kapanışlarla güncellenir. |
| plan | `pattern_trader/strategy.py` | Sürümlü katalog (`pattern_protocol_v1.0.0`): A trend geri çekilmesi, B seviye dönüşü, C sıkışma kırılımı. Tetik/iptal/stop/hedef/zaman aşımı/maliyet açıkça tanımlı. |
| işlem | `pattern_trader/book.py` | Ortak `RiskEngine` + ortak `strategy_paper.apply_action` + gerçek `FuturesLedgerV2`. Plan yaşam döngüsü kalıcı. |
| tarama | `pattern_trader/scheduler.py` | Dönen kuyruk, öncelik, kapsama/gecikme ölçümü; ARKA PLAN iş parçacığı. |
| rapor | `pattern_trader/report.py` | (1) sıklık ve (2) maliyet sonrası sonuç AYRI; kohortlar örtüşmez; az örneklemde hüküm YOK. |

## 2. Evren ve öncelik

* Yaş filtresi **YOKTUR** ve sembol sayısı sınırsızdır: genel `universe.min_listing_age_days=60` ve `max_symbols=200`
  bu deftere **miras kalmaz** (`pattern_universe.json → policy` alanında açıkça yazılır).
* Kohortlar (tam saat, örtüşmez): `0-24h`, `1-7d`, `7-30d`, `30-90d`, `90d+`; bilinmeyen yaş `UNKNOWN`.
  **Öncelikli tarama grubu**: yaş <= 720 saat (30 gün). Bu bir ürün tercihidir, kanıtlanmış kârlılık eşiği değildir;
  bekleme süresi de değildir (30 günün dolması BEKLENMEZ).
* Tarama sırası: açık pozisyonlar → tetik bekleyen planlar → yeni listelenenler → dönen kuyruk (en eski taranan önce).
  Bütçenin en az yarısı (`ROTATION_RESERVE`) yeni/rotasyona ayrılır: ölçüldü ki aksi hâlde plan üreten ilk semboller
  kuyruğu kilitliyor ve 14 sembolün 9'u hiç taranmıyordu.
* Uygunluk: hacim eşiği, makas (bilinmiyorsa **iyi likidite SAYILMAZ**), filtre bilgisi. Derinlik/makas **giriş anında**
  yeniden ölçülür; ölçülemezse giriş `LIQUIDITY_UNKNOWN`/`DEPTH_UNKNOWN` ile reddedilir.
* Delist/durum değişimi kayda geçer: yeni plan/giriş durur, **açık pozisyonun yönetimi ve geçmişi korunur**.

## 3. Veri yeterliliği (tek ve keyfî bir eşik YOK)

`data.REQUIREMENTS` modüllerden türetilir: şekil 3 bar, teyit 1 kapanmış bar, ATR14 15 bar, önceki trend 11 bar,
seviye/bölge 13 bar, sıkışma 21 bar. Her dilim **ayrı** raporlanır (`symbol_scans`): 15m hazır ama 4h yetersizse bu
görünür ve 4h bağlamını şart koşan aile (A) çalışmaz; C ailesi 4h/1h ZORUNLU DEĞİL diye açıkça tanımlıdır ve yeni
listelenen coinde 15m yeterli olunca çalışabilir. **Eksik mum uydurulmaz.**

## 4. Strateji protokolü (ölçülecek hipotezler)

Ortak: karar yalnız kapalı barla; tetik sonraki kapalı barın kapanışı; fill teyitten önceki fiyattan geriye yazılamaz
(ilk uygulanabilir doğrulanmış fiyat/zaman, `chase_atr` kovalamama sınırıyla). Stop yapının bozulduğu yerden
(`stop_buffer_atr`). Hedef sırası sabittir: karşı 1h bölge → (C) ölçülü hareket → `fallback_rr`; hiçbiri maliyet
sonrası `min_rr_after_cost`u sağlamazsa **plan yok**. Seçilen kaynak `target_source` ile kayda girer.

| aile | bağlam | tetik | stop | hedef |
|---|---|---|---|---|
| A_TREND_PULLBACK | 4h trend UP→LONG / DOWN→SHORT; 15m şekil teyitli; kapanış son 12 barın ucundan >= 1 ATR geri | 15m kapanış şekil yükseği üstü / düşüğü altı | şekil ucu ∓ buffer×ATR | karşı 1h bölge yoksa `fallback_rr` |
| B_LEVEL_REVERSAL | 15m şekil ucu teyitli 1h bölgeye <= 0.5 ATR yakın | aynı | bölge ucu ∓ buffer×ATR | aynı |
| C_COMPRESSION_BREAKOUT | 15m son 6 barın aralığı <= 1.0 ATR ya da 2 ardışık inside bar; **4h/1h zorunlu değil** | aralık ucunun üstü/altı kapanış | aralık karşı ucu ∓ buffer×ATR | ölçülü hareket, yetmezse `fallback_rr` |

## 5. İşlem ve emniyet

Durumlar: `AWAITING_TRIGGER → TRIGGERED → RISK_CHECK → OPENED → MANAGED → CLOSED`; alternatif sonlar `REJECTED`,
`BROKEN`, `EXPIRED`, `CANCELLED`. Aynı plan iki kez emir AÇAMAZ (`position_id`); aynı sembolde ikinci pozisyon
açılmaz; bir yön gerçekleşince karşı plan iptal olur; zarardan sonra sembol bazında soğuma vardır
(**otomatik tersleme / martingale YOK**). Zaman stopu `max_hold_bars` (15m × 96 = 24 saat).

Zorunlu kapılar: veri kaynağı ve 15m serisinin tazeliği, **doğrulanmış güncel perp mark** (yoksa/bayatsa giriş yok,
uydurma fill yok, plan tetiklenmiş bekler), makas/derinlik, stop mesafesi, maliyet sonrası R/R'nin giriş fiyatıyla
yeniden hesabı, kill-switch ve ortak `RiskEngine` (toplam risk, tek coin tavanı, kaldıraç, cluster).

**Soğuk başlangıç:** bu defterin kârlılık kanıtı YOKTUR. Ana botun başka kurallardan öğrendiği `p_win` burada
KULLANILMAZ; uydurma 0,50 ile kapı geçirilmez. `p_win: null`, `expected_r: null`, `edge_note_tr: "ölçülmedi"`.

## 6. Ölçüm (`pattern_report.json`)

İki AYRI soru: **(1) sıklık** — aynı kural sürümü ve aynı 15m dilimiyle incelenen kapalı bar başına şekil/teyit/plan;
**(2) sonuç** — bu girişlerin maliyet sonrası net R, profit factor, isabet, ortalama kazanç/kayıp, maksimum düşüş,
işlem sayısı ve %95 bootstrap aralığı. Kohortlar örtüşmez; kapanmamış pozisyonlar sonuç bölümüne GİRMEZ.
`MIN_TRADES_FOR_VERDICT = 30` altında hüküm `BELİRSİZ`dir. Sıklık yüksek olup net sonuç negatifse **"yeni coin
avantajı" ilan edilmez**; karşılaştırma gözlemseldir, coin yaşının nedensel etkisi ölçülmemiştir.

## 7. Yapılandırma ve dosyalar

`config.yaml → pattern_trader` (LIVE'da `ConfigError`). State: `state/pattern_trader.json` (defter özeti, planlar,
bulgular, sayaçlar), `state/pattern_trader/{futures_ledger.json,plans.json,findings.json,trade_memory.jsonl}`,
`state/pattern_scan.json` (kapsama/gecikme/kuyruk), `state/pattern_universe.json` (keşif), `state/pattern_report.json`.
Panel: `/patterns` sayfası ve `/api/patterns` ucu.

## 8. Testler (`tests/test_pattern_trader_v1.py`, 10)

Gerçek `PatternBook`/`FuturesLedgerV2`/`RiskEngine`/`apply_action`/`PatternScanner`/`MarketFeed`; ağ yerine
`MockProvider`. Kapsam: defter API sözleşmesi; resmi metadata keşfi (22 sembol, sözleşme/quote/durum/hacim elemeleri,
kohort sınırları, yeni listeleme/delist olayları, metadata hatasında eski evrenin korunması); yeni listelemede dilim
başına hazır/yetersiz ve C kolunun çalışması; **gerçek LONG** zinciri (tetik → risk → giriş → hedefte kapanış);
**gerçek SHORT** zinciri (→ koruyucu stop + soğuma); tetiklenmeyen/bozulan/süresi dolan/bayat fiyatlı/kill-switch'li
yolların emir AÇMAMASI + tekrar tarama ve yeniden başlatmada yinelenme olmaması; gelecek barların geçmiş kararı
değiştirmemesi; dönen kuyruk/öncelik/kapsama ve çıkış izleyicisinin taramadan bağımsızlığı; raporun sıklık ile sonucu
ayırması ve az örneklemde hüküm vermemesi; motorun tarayıcıyı arka planda başlatması ve raporu yazması.
