# Altın laboratuvarı — gold_v1: iki sosyal medya taktiği ve mevcut sinyaller altında (2026-10-04)

Salt araştırma. Hiçbir defter, strateji, ajan ya da parametre değişmez. PAPER/geçmiş testtir; kâr garantisi değildir.

**Ön kayıt:** bu belge altın fiyat verisi indirilmeden ve hiçbir sonuç görülmeden yazıldı (commit zamanı kanıttır). Kurallar
kodda `tradingbot/gold_lab.py` içindeki `GOLD_REGISTRY`'de tutulur. Bu kayıttan hesaplanan mühür `GOLD_REGISTRY_SHA` bir
testle sabitlenir. Biri değişirse mühür değişir: o yeni sürümdür, yeni deneme sayısıdır ve bu belge güncellenir. Sayımlar ya
da sonuçlar görüldükten sonra kural, eşik, dilim ya da dönem GEVŞETİLMEZ.

Ön kayıt mührü: GOLD_REGISTRY_SHA = ee32a9db510f41cd

## Soru

1. Instagram'daki "15 dakikalık grafikte 20/50 EMA kesişimi, 1:2,5 risk/ödül" sistemi altında maliyet sonrası kazandırıyor
   mu? Gönderi stop kuralını vermiyor; aşağıda iki stop tanımı ayrı ayrı sınanır.
2. Order block / FVG / BOS / CHoCH / likidite süpürmesi kavramları (birinci görsel) kurala çevrildiğinde altında
   kazandırıyor mu? Görseldeki etiketler sonradan çizilmiştir; burada her kavramın tek, sabit bir tanımı sınanır.
3. Sinyal laboratuvarındaki mevcut sinyaller (katalog, ek varyasyonlar, algoritmalar, C4 mum varyasyonları) altında ne
   veriyor? Bunlar keşif amaçlıdır; tanımları zaten kayıtlıdır ve altın için değiştirilmez.

Ölçüt sahibin hedefidir: **defter başına aylık net +%1**.

## Veri

- **Ana seri:** Binance spot PAXGUSDT (1 PAXG = 1 troy ons altın), `data.binance.vision` spot arşivi, 2020-08-01 →
  2026-09-30 (UTC), 15m / 1h / 4h / 1d. Hafta sonu dahildir; PAXG ve Binance'in altın vadelileri 7/24 işlem görür.
- **Mekân denetimi (yalnız bilgi):** Binance USDⓈ-M XAUUSDT vadeli (2025-12 →) ve PAXGUSDT vadeli (2025-03 →),
  2026-09-30'a kadar, aynı dilimler. Fonlama arşivi yalnız bilgi amaçlıdır.
- **Uzun geçmiş (koşullu, yalnız sağlamlık):** Dukascopy XAUUSD BID 1 dakikalık mumları, 2006-01-01 → 2026-09-30, UTC'ye
  hizalı 15m / 1h / 4h'ya yeniden örneklenir. Kapalı piyasa barları (hacim 0) atılır. Bulut IP'si bu kaynakta hız sınırına
  takılıyor (429/503); indirme yavaş yapılır. Günlerin %95'inden azı inerse bu bölüm "yapılamadı" diye raporlanır; hüküm
  yine ana seriden verilir.
- Fiyat verisi depoya yüklenmez; önbellek yerel klasördedir. Her seride eksik bar oranı ve 24 saatten uzun boşluklar
  raporlanır.

## Maliyet

- Botun modeli kullanılır: taraf başına taker %0,05 + kayma 3 bps, gidiş-dönüş ≈ %0,16. Bu, `LabConfig` varsayılanıdır
  (`config.yaml` `futures_taker_pct` ve `slippage_bps`).
- Bilgi amaçlı duyarlılık: maker %0,02 + 0 bps. Hükme girmez.
- Fonlama yalnız vadeli mekân koşusunda bilgi olarak raporlanır (`funding_r`). Hükme girmez.

## İşlem modeli

Sinyal laboratuvarının kuralları aynen kullanılır (`tradingbot/signal_lab.simulate`):

- Karar bar kapanışında verilir; giriş sonraki barın açılışındadır.
- Aynı barda hem stop hem hedef görülürse STOP sayılır. Boşlukla açılışta açılış fiyatı kullanılır.
- Giriş tetikten 1 ATR'den fazla uzaksa işlem yoktur (kovalama kuralı).
- Risk 0,1 ATR'den küçükse ya da 5 ATR'den büyükse işlem yoktur.
- Hedef = RR × gerçek girişteki risk. RR ve süre sınırı varyant başınadır (aşağıda); süre dolunca son barın kapanışında
  çıkılır.
- ATR, laboratuvarın ATR14'üdür. EMA, kapanış üzerinde üstel ortalamadır (`adjust=False`).
- Aynı varyant ve yön içinde üst üste binen işlemlere laboratuvardaki gibi izin verilir. Aylık hesapta eşzamanlı açık
  işlem sayısının en yükseği ve ortalaması raporlanır.

## Ortak tanımlar

- **Teyitli swing pivotu:** fraktal, k = 3. Bar j'nin yükseği önceki 3 ve sonraki 3 barın yükseklerinin hepsinden
  büyükse swing yüksektir (düşük için simetrik). Pivot ancak j+3 barının kapanışında teyitli olur; o andan önce hiçbir
  kural onu göremez.
- **Tampon:** b = 0,1 × ATR (karar barındaki ATR).

## Varyantlar (16 varyant; her biri LONG ve SHORT olarak 2 hücre, toplam 32 birincil hücre)

### A) EMA ailesi (görseldeki sistem), dilimler 15m ve 1h, RR 2,5

Süre sınırı: 15m'de 96 bar (24 saat), 1h'te 48 bar (2 gün). Tetik (kovalama ölçüsü): karar barının kapanışı.

| Ad | Giriş | Stop |
|---|---|---|
| `EMA_X_SWING` | Bar i'de EMA20 EMA50'yi yukarı keser (EMA20[i] > EMA50[i] ve EMA20[i−1] ≤ EMA50[i−1]) → LONG; aşağı keser → SHORT | LONG: i−9..i barlarının en düşüğü − b; SHORT: en yükseği + b |
| `EMA_X_ATR` | Aynı | Kapanış ∓ 1,5 ATR |
| `EMA_X_SWING_HTF` | `EMA_X_SWING` + yalnız son KAPANMIŞ 4h barında kapanış > EMA50(4h) iken LONG, < iken SHORT | `EMA_X_SWING` ile aynı |
| `EMA_X_SWING_SESSION` | `EMA_X_SWING` + karar anı (bar kapanışı) 07:00–20:00 UTC arasında | `EMA_X_SWING` ile aynı |

### B) SMC ailesi (birinci görsel), dilimler 1h ve 4h, RR 3

Süre sınırı: 1h'te 72 bar (3 gün), 4h'te 30 bar (5 gün). Aşağıdaki tanımlar LONG içindir; SHORT tamamen simetriktir.

| Ad | Kurulum ve giriş | Stop | Tetik |
|---|---|---|---|
| `SMC_BOS_OB` | **BOS:** kapanış son teyitli swing yükseği (H) yukarı kırar (close[i] > H ve close[i−1] ≤ H). **Order block:** son teyitli swing düşüğün barından i'ye kadar olan barlar içinde, BOS'tan önceki SON ayı mumu (close < open); yoksa kurulum yok. Bölge [OB_low, OB_high]. **Giriş:** sonraki 20 bar içinde low ≤ OB_high ve close ≥ OB_low olan İLK bar k'da karar verilir. Bu 20 bar içinde daha önce close < OB_low olursa kurulum iptal. Aynı order block'tan en çok bir işlem. | OB_low − b | OB_high |
| `SMC_SWEEP` | Son teyitli swing düşük S için low[i] < S ve close[i] > S (likidite süpürmesi). Aynı pivot en çok bir kez kullanılır. | low[i] − b | close[i] |
| `SMC_CHOCH` | Düşüş yapısı: son iki teyitli swing yüksek azalan VE son iki teyitli swing düşük azalan. Bu yapıdayken close[i] > son teyitli swing yüksek ve close[i−1] ≤ o (karakter değişimi). | Son teyitli swing düşük − b | close[i] |
| `SMC_FVG` | **Boğa FVG** bar m'de: low[m] > high[m−2], bar m−1 boğa ve gövdesi ≥ 1 ATR(m−1). Bölge [high[m−2], low[m]]. **Giriş:** sonraki 20 bar içinde low ≤ low[m] ve close ≥ high[m−2] olan İLK bar k'da karar verilir. Bu 20 bar içinde daha önce close < high[m−2] olursa kurulum iptal. | high[m−2] − b | low[m] |

### C) Mevcut laboratuvar setleri (değiştirilmeden, keşif)

- Katalog: 1h ve 4h. 15m'de çalıştırılmaz (6 yılda ~210 bin bar; maliyetli).
- `EXTRA_SIGNALS`: 15m, 1h ve 4h.
- `ALGOS`: 1h, 4h ve 1d.
- C4 mum varyasyonları (`--variations all`): 1h ve 4h.

Bu setler 32 birincil hücreye sayılmaz; laboratuvarın kendi hükmüyle ayrı raporlanır. Çok sayıda grup içerdikleri için tek
başına bir "aday" kanıt sayılmaz.

## Plasebo (her A/B varyantı için eşleştirilmiş)

- `PLACEBO_<ad>`: aynı dilim, aynı yön, aynı RR, aynı süre sınırı ve kovalama ölçüsü olarak karar barının kapanışı.
- Giriş anları rastgeledir. Her bar için u = crc32("<sembol>|<dilim>|<ad>|<yön>|<zaman damgası>") / 2³² hesaplanır;
  u < p ise plasebo olayı oluşur.
- p = o varyantın aynı seri ve yöndeki sinyal sayısı / bar sayısı. Sonuçtan bağımsızdır, sabittir.
- Plasebo stopu:
  - `EMA_X_SWING`, `EMA_X_SWING_HTF` ve `EMA_X_SWING_SESSION`: son 10 barın uç değeri ∓ b.
  - `EMA_X_ATR`: kapanış ∓ 1,5 ATR.
  - `SMC_SWEEP`: o barın düşüğü − b (SHORT: yükseği + b).
  - `SMC_CHOCH`, `SMC_BOS_OB` ve `SMC_FVG`: son teyitli swing düşük − b (SHORT: yüksek + b).
- Laboratuvarın karşılaştırması aynen geçerlidir: gerçek grup ile eşleştirilmiş plasebo aynı dilim, yön ve bağlamda
  karşılaştırılır.

## İstatistik ve hüküm (laboratuvarın kuralları, değişmeden)

- **Dönemler:** her dilimde dönemin ilk 2/3'ü keşif (IS), kalanı doğrulamadır (OOS). Ana seride bu kabaca 2020-08 →
  2024-07 / 2024-07 → 2026-09 eder.
- **Hükümler:** en az 30 IS ve 20 OOS işlem; gün kümeli bootstrap %95 aralık; GÜÇLÜ ADAY / ZAYIF İZ / ZARAR / YOK / İNCE
  laboratuvardaki gibi. SIKI GÜÇLÜ ADAY = `STRICT_RULE_TR`.
- **Birincil hücreler:** 16 varyant × 2 yön, bağlam "HEPSİ". Bağlam dilimleri yalnız bilgidir.
- **Çoklu deneme:** gerçek hücrelerin aday oranı plaseboların oranıyla karşılaştırılır. Laboratuvarın rastgele yürüyüş
  ölçümünde hücrelerin %0–0,5'i tesadüfen GÜÇLÜ ADAY çıkmıştı; 32 hücrede tesadüfen 0–1 aday beklenir.

## Aylık hedef ölçüsü (sahibin hedefi)

- Her birincil hücre için, ayrıca her varyantın iki yönü birlikte, OOS'ta takvim ayı başına toplam R hesaplanır.
- İşlem başına risk %0,5'tir: strateji defterlerinin `risk_per_trade_pct` değeri (`config.yaml`, `learning_mode` ayarı).
  Buna göre aylık % = aylık toplam R × 0,5. Bilgi amaçlı %1 risk de raporlanır.
- **Hedefi karşılıyor =** OOS ortalama aylık net ≥ +%1 VE sıkı hüküm GÜÇLÜ ADAY. Aylık ortalamanın ay kümeli bootstrap
  %95 aralığı da raporlanır.
- Bu hesap bileşik getiri içermez ve defterin bütün işlemleri taşıyabildiğini varsayar. Eşzamanlı açık işlem sayısı bu
  yüzden ayrıca raporlanır.

## Mekân denetimi ve uzun geçmiş

- **Binance vadeli (bilgi):** aynı 16 varyant XAUUSDT ve PAXGUSDT vadeli serilerinde, dönem bölmeden tüm dönemde koşar.
  Ortalama R, işlem sayısı ve fonlama R'si raporlanır. Hükme girmez. Ana seride GÜÇLÜ ADAY olan bir hücre burada n ≥ 20
  iken ortalama R ≤ 0 verirse "mekânda tutmadı" notu düşülür.
- **Dukascopy (koşullu):** aynı 16 varyant 2006 → 2026'da, aynı maliyet ve 2/3 bölmeyle (≈ 2006–2019 / 2019–2026) koşar.
  Ana seride GÜÇLÜ ADAY olan bir hücre burada OOS ortalama R ≤ 0 verirse "uzun geçmişte tutmadı" notu düşülür.

## Sonuç ve sonraki adım

- Hiçbir hücre hedefi karşılamazsa sonuç açıkça yazılır: bu taktikler altında maliyet sonrası kazandırmadı. Sonuçtan sonra
  ayarlama yapılmaz; ayarlama yeni sürüm ve yeni deneme demektir.
- Hedefi karşılayan bir hücre olursa: yalnız PAPER, önce yalnız-kayıt bir defter önerilir. Sahip onayı olmadan hiçbir şey
  açılmaz.
- Bot bugün 1h/4h turlarıyla çalışır; 15 dakikalık bir hücre için ayrı bir döngü gerekir (mimari iş, bu çalışmanın dışında).
- Mevcut ajan ekibinin (`tradingbot/agents`) sinyalleri bu çalışmada sınanmaz; geçmişe dönük tekrar oynatma altyapısı yoktur.

## Deneme sayısı

gold_v1: 16 varyant (32 birincil hücre) + mevcut laboratuvar setlerinin altına uygulanması (keşif).
