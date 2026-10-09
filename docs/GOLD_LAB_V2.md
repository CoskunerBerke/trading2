# Altın laboratuvarı — gold_v2: günlük/4 saatlik trend takibi ve hafta sonu geri dönüşü (ön kayıt, 2026-10-05)

Salt araştırma. Hiçbir defter, strateji, ajan ya da parametre değişmez. PAPER/geçmiş testtir; kâr garantisi değildir.
Olumsuz sonuç çıkarsa olduğu gibi yazılır.

**Ön kayıt:** bu belge, aşağıdaki kurallarla hiçbir getiri, R, sinyal ya da işlem sayımı hesaplanmadan yazıldı ve
commit'lendi (commit zamanı kanıttır). Taslak bir eleştiri turundan geçti; eleştirmen de hiçbir sonuç hesaplamadı. Belge
commit'lenene kadar bu verilerin hiçbirinde sonuç hesaplanmaz; commit'ten sonra kurallar donar. Kurallar kodda yeni modül
`tradingbot/gold_lab_v2.py` içindeki `GOLD_V2_REGISTRY`'de, bu belgenin birebir karşılığı olarak tutulur. Kayıttan
hesaplanan mühür `GOLD_V2_REGISTRY_SHA` bir testle sabitlenir. Biri değişirse mühür değişir: o yeni sürümdür, yeni deneme
sayısıdır ve bu belge güncellenir. Sonuç görüldükten sonra kural, eşik, dönem, stop ya da çıkış GEVŞETİLMEZ.
gold_v1'in kaydı (`GOLD_REGISTRY`, mühür `ee32a9db510f41cd`) değişmez.

Ön kayıt mührü: GOLD_V2_REGISTRY_SHA = 72182fc4343f9e3f

### Bu belge yazılırken veriden ne görüldü (biçim doğrulaması)

- gold_v2 için Dukascopy dosya biçimi, 2026-10-05 05:20–05:21 UTC'de aynanın DIŞINDA bir klasöre (`scratchpad/gold/duka_h`)
  indirilen deneme dosyalarıyla doğrulandı. Çözülen dosyalar yalnız ikidir: **2010-01 saatlik**
  (`2010/00/BID_candles_hour_1.bi5`, 744 kayıt = 31 × 24, kapalı saatler dahil) ve **2010 günlük**
  (`2010/BID_candles_day_1.bi5`, 365 kayıt). Her birinin ilk iki ve son kaydı fiyatlarıyla ekrana basıldı. Yani şunlar
  GÖRÜLDÜ: 2010-01-01 00:00 ve 01:00 UTC saatlik barları (≈ 1.096 USD), 2010-01-31 23:00 UTC (Pazar) saatlik barı
  (kapanış ≈ 1.083, hacim > 0), 2010-01-01 günlük barı (açılış ≈ 1.096), 2010-01-02 günlük barı (Cumartesi; düz, hacim 0) ve
  2010-12-31 günlük barı (kapanış ≈ 1.420). Bundan 2010 yılının yönü (≈ +%30) ve Ocak 2010'un yönü (≈ −%1) bilinir. İkisi de
  keşif dönemindedir; doğrulama dönemine (2014-01 → 2020-07) ait hiçbir fiyat görülmedi. İkisi de kamuya açık bilgidir.
- Aynı anda indirilen 2006-06 saatlik dosyası (`2006/05/BID_candles_hour_1.bi5`) çözülmedi.
  `2015/00/BID_candles_day_1.bi5` isteği 503 döndü (107 baytlık hata gövdesi; çözülmedi).
- gold_v1 sırasında (2026-10-04) dakikalık biçimi doğrulamak için 2006-01-01 (Pazar; düz, hacim 0) ve 2006-01-03 dakikalık
  dosyaları çözüldü; ilk üç / son iki kayıt ve sütunların en küçük/en büyük değerleri basıldı (2006-01-03 gün içi ≈ 516–535
  USD). Eski dakikalık aynada 2006-01-01 → 2006-06-29 arası 155 gün var. gold_v1'in Dukascopy bölümü kapsama eşiğine
  takıldığı için (%2,4) hiç koşmadı ve hiçbir olay üretmedi.
- Aynanın geri kalanından yalnız manifest (indirme durumu, bayt) okundu. Hiçbir getiri dizisi, gösterge, sinyal, işlem ya da
  R hesaplanmadı. Bu dosyalar hükümden çıkarılmaz (birkaç fiyat seviyesi kuralların sonucunu belirlemez); bu açıklama sonuç
  raporunda tekrarlanır.

## Soru

1. **Trend takibi (aile A):** günlük ve 4 saatlik trend takibi kuralları, bu depoda hiç koşulmamış 2006–2020 verisinde
   maliyet sonrası, aynı maruziyetteki rastgele girişten daha iyi mi? gold_v1'de PAXG üzerinde yalnız "keşif izi" veren iki
   kural (1d `TREND_DONCHIAN_20_10` LONG ve 1d `TSMOM_28` LONG) burada değiştirilmeden, bağımsız veride sınanır. Bu,
   gold_v2'nin önceden belirlenmiş birinci sorusudur.
2. **Hafta sonu geri dönüşü (aile B):** ana altın piyasası hafta sonu kapalıyken 7/24 işlem gören PAXG'deki hafta sonu
   hareketi, ana piyasa Pazar akşamı açıldığında geri dönüyor mu?

Ölçüt sahibin hedefidir: **defter başına aylık net +%1** (işlem başına %0,5 riskte; maliyet ve fonlama vekili dahil,
aşağıda).

Önceden kayıtlı **ANA hücreler** (ayrıntı "Çoklu deneme"de): `A_DONCH_20_10` LONG, `A_TSMOM_28` LONG ve `B_WKND_REV_ALL`
İKİ YÖN. Öneri yalnız bir ANA hücreden doğabilir.

## gold_v1'den ne biliniyor ve neden bu tasarım

- gold_v1: 16 kısa vadeli varyant (15m/1h EMA 20/50, 1h/4h SMC), 32 hücre, 0 güçlü aday. 15m sistemler çoğunlukla
  maliyete kaybetti (maliyet R'nin 0,59–1,36'sı). Bu yüzden gold_v2 yalnız 1d / 4h / aylık tutuşlara ve hafta başına en çok
  bir işleme bakar; stoplar geniş, maliyet R'nin küçük bir payıdır.
- gold_v1'in keşif izleri (1d Donchian 20/10 LONG, 1d TSMOM_28 LONG, 4h yükselen üçgen LONG) altının 2024–2026 yükselişiyle
  uyumludur ve **kanıt değildir**. PAXG 2020-08 → 2026-09 günlük/4h trend kuralları için "görülmüş" veridir; aile A'nın
  hükmünde kullanılamaz.
- Hafta sonu davranışı gold_v1'de hiç sınanmadı. PAXG 2020-08 → 2026-09 bu aile için görülmemiş sayılır.
- Dürüstlük notu (genel seyir): altının 2006–2020 genel seyri (2011 zirvesi, 2013 düşüşü, 2019–2020 yükselişi) kamuya açık
  bilgidir. Kural seçimi bu bilgiyle değil literatürle yapıldı; ama yazarın bu dönemlere tamamen kör olduğu iddia edilmez.
  Maruziyet eşli plasebo (aynı yön, aynı tutuş süresi, rastgele zaman) piyasa yönünün etkisini ayırmak için vardır.
- **Dürüstlük notu (literatür örtüşmesi):** "2006–2020 hiç kullanılmadı" yalnız bu depo için doğrudur. Yayımlanmış trend
  takibi örneklemleri altını bu yıllarda kapsar: Moskowitz–Ooi–Pedersen 2012'nin verisi 2009'a, Hurst–Ooi–Pedersen 2017'nin
  ("A Century of Evidence on Trend-Following Investing") verisi yaklaşık 2016'ya kadar gider. Yani keşif döneminin bir kısmı
  ve doğrulama döneminin 2014–2016 yılları, trend kurallarının (altını da içeren çeşitlendirilmiş vadeli sepetlerinde)
  işlediğini gösteren yayımlanmış kanıtla örtüşür. Aile A'nın önseli bu yıllardan bağımsız değildir. Doğrulama "literatürden
  bağımsız" bir sınama değil, "bu depoda görülmemiş veride, tek varlıkta, botun maliyetiyle" bir sınamadır.

## Veri

### Dukascopy XAUUSD (aile A'nın hükmü)

- Kaynak: aynadaki saatlik BID mumları, `<kök>/XAUUSD/<YYYY>/<MM0>/BID_candles_hour_1.bi5` (MM0 = 0 tabanlı ay); günlük
  dosyalar `<kök>/XAUUSD/<YYYY>/BID_candles_day_1.bi5`. Kök: aynanın klasörü (`.../scratchpad/gold/data/dukascopy_h`). Yalnız
  okunur; aynaya yazılmaz; Dukascopy'den indirme yapılmaz.
- **Ayna anlık görüntüsü:** ayna bu belge yazılırken arka planda hâlâ yazılıyor. Koşu yalnız ayna işi bittikten sonra başlar:
  işin günlüğünde "hourly mirror pass finished" satırı var, ayna süreci çalışmıyor ve aynada `.part` dosyası yok. Başarısız
  (200/404 dışı) aylar için, sonuç görmeden, aynı betikle bir yeniden geçiş yapılabilir; gerçek veri koşusu bunun ardından
  bir kez yapılır. Koşu başında `manifest.jsonl`'nin ve kullanılan her bi5 dosyasının sha256'sı ile aynanın kesim anı
  (manifest'teki en geç `ts`) rapora yazılır. Koşu sonunda aynı dosyaların özeti yeniden alınır; biri değişmişse koşu
  geçersizdir (`GoldDataError`). Koşu sırasında yazılan ya da yarım kalmış bir dosya okunmaz.
- **Çözme:** LZMA alone; 24 baytlık big-endian kayıtlar: int32 saniye (saatlik dosyada ay başından, günlük dosyada yıl
  başından), int32 açılış, kapanış, düşük, yüksek (÷1000 = USD), float32 hacim. Saatlik dosyada saniye
  [0, ayın gün sayısı × 86400) içinde, kesin artan ve `t % 3600 == 0` olmalı; günlük dosyada [0, yılın gün sayısı × 86400)
  içinde, kesin artan ve `t % 86400 == 0`. Biri tutmazsa koşu durur (`GoldDataError`). gold_lab'ın `decode_bi5`'i gün
  sınırını denetlediği için ay/yıl sınırlı bir eşi yazılır; `bi5_sanity` aynen kullanılır.
- **Zaman damgası varsayımı ve biçim denetimi (sonuç hesaplamaz):** Dukascopy zaman damgalarının UTC olduğu varsayılır.
  Koşudan önce, hiçbir getiri hesaplamadan, her hafta için "haftanın ilk saati" h_w ölçülür: kendinden önceki tutulan bar
  40 saatten daha eski olan ilk tutulan saatlik barın UTC saati. Ana piyasa Pazar 17:00 ya da 18:00 New York'ta açıldığı
  için beklenen: ABD yaz saatinde h_w ∈ {21, 22}, kış saatinde h_w ∈ {22, 23} (yaz/kış, `zoneinfo` America/New_York ile o
  haftanın tarihine göre). Keşif ve doğrulama dönemlerinin HER BİRİNDE haftaların en az %90'ı bu kümeye düşmezse koşu durur
  (`GoldDataError`) ve aile A'nın hükmü "yapılamadı (zaman damgası)" olur. Varsayım sonuç görüldükten sonra düzeltilmez
  (yeni ön kayıt gerekir). h_w dağılımı yaz/kış ayrı, Cuma'nın son saatinin dağılımı bilgi olarak raporlanır.
- **Temizlik:** bir satır yalnız kapalı piyasa barıysa atılır: hacim == 0 VE açılış = yüksek = düşük = kapanış. Hacmi 0 ama
  fiyatı düz olmayan barlar TUTULUR ve ayrı sayılır; hacmi > 0 olan düz barlar da tutulur. `bi5_sanity`'yi geçmeyen satırlar
  atılır ve sayılır. Yıl başına toplam satır, atılan kapalı piyasa satırı, tutulan hacimsiz hareketli satır ve geçersiz satır
  raporlanır (erken yıllarda hacmin tümüyle 0 olması seriyi sessizce boşaltmasın diye).
- **İşlem günü:** saatlik bar t'nin günü D(t) = (t'nin New York yerel saati + 7 saat)'in takvim tarihidir (`zoneinfo`
  America/New_York). Yani işlem günü 17:00 New York'ta başlar. Pazar akşamı açılışı (yaz 21:00/22:00 UTC, kış 22:00/23:00
  UTC) Pazartesi'ye, Cuma'nın son barı Cuma'ya düşer. Yaz saatinde 21:00 UTC'de açılan bir Pazar barı da Pazartesi'ye düşer;
  atılmaz. Günlük bar: ilk açılış, en yüksek, en düşük, son kapanış, hacim toplamı; zaman damgası D−1 günü 17:00 New York,
  kapanış anı D günü 17:00 New York (UTC'ye çevrilir). Cumartesi ya da Pazar'a düşen saatler (D = Cmt/Paz) günlük ve 4h
  seriye girmez; sayılır ve haftaların %1'inden fazlasında görülürse raporda işaretlenir.
- **Ay başı notu:** ayın ilk işlem gününün ilk saatleri (önceki takvim gününün 17:00 New York sonrası) önceki ayın saatlik
  dosyasından gelir; Pazartesi 1'inde bu, Pazar akşamı saatleridir. Önceki ayın dosyası yoksa o günlük bar "KISMİ_GÜN"
  işaretlenir ve sayılır. (Önceki ay eksik olduğu için aşağıdaki boşluk kuralı da devrededir: bu gün yeni segmentin ilk
  günüdür ve ısınmaya düşer, hiçbir karar ya da giriş onu kullanmaz.)
- **4h bar:** kutu, işlem günü içindeki ⌊(New York yerel saati − 17:00) / 4 saat⌋ dilimidir; kutu başları 17:00, 21:00,
  01:00, 05:00, 09:00, 13:00 New York. Yalnız verisi olan kutular. Kapanış anı = kutu sonu.
- **Ay:** bir günlük barın ayı, D'nin takvim ayıdır. Ay sonu barı = o aydaki son günlük bar.
- Günlük dosya (`BID_candles_day_1.bi5`) hükümde kullanılmaz. Yalnız bilgi olarak ay sonu kapanışlarının saatlik
  türetimle farkının medyanı raporlanır.
- **Kapsama eşiği (koşu anında):** keşif (2006-01 → 2013-12, 96 ay) ve doğrulama (2014-01 → 2020-07, 79 ay) dönemlerinin
  HER BİRİNDE iki şart birlikte aranır:
  1. Ayların en az %95'i kullanılabilir. Ay kullanılabilir: manifest'te `kind: "hour"`, status 200 ve bayt > 0 kaydı var,
     dosya aynada boş değil, çözülebiliyor VE temizlikten sonra geçerli saatlik bar sayısı ≥ 0,85 × (ayın Pzt–Cum gün sayısı
     × 23).
  2. Pzt–Cum işlem günlerinin (D'ye göre) en az %95'inde ≥ 20 geçerli saatlik bar var.

  İki eşiğin altında kalan ay ve gün sayıları (ve listeleri) raporlanır. Bir dönemde şartlardan biri tutmazsa **aile A'nın
  hükmü "yapılamadı"** olarak yazılır; görülmüş/mekân satırları yine bilgi olarak raporlanır ama hüküm yerine geçmez.
- **Boşluk ve segment kuralı:** ardışık iki tutulan saatlik bar arasında 120 saatten uzun boşluk varsa seri orada bölünür;
  her parça bir "segment"tir. Bütün göstergeler (ATR14, hiN/loN, mom28, SMA200, ay sonu kapanışları, C'nin 60 günlük σ'sı)
  her segmentte segmentin başından yeniden hesaplanır ve ısınma segment başına uygulanır. Böylece hiçbir geri bakış
  penceresi bir boşluğu aşamaz; haftalar ötedeki fiyatlar bitişikmiş gibi karşılaştırılmaz. Boşluk sonrası ısınmada kalan
  bar sayısı "BOŞLUK_GERİ_BAKIŞ" olarak raporlanır. Girişi, tutuşu ya da çıkışı segment sonunu aşacak işlem atılır ve
  "BOŞLUK_TUTUŞ" olarak sayılır (plasebo dahil; gerçek ve plasebo sayıları yan yana). Normal hafta sonu ≈ 49–50 saat, uzun
  tatil hafta sonu ≈ 100 saatin altındadır; 120 saat bu yüzden eşiktir.
- **Kısa seans günleri (bilgi):** < 12 geçerli saatlik barı olan Pzt–Cum günleri (örneğin 24/31 Aralık, Şükran Günü Cuması)
  sayılır ve listelenir. Kural değişmez.
- **Seri sonu ve sağdan kesilme:** hüküm serisi 2020-07-31 23:59 UTC'de biter. Bu tarihten sonra veri gerektiren işlem
  sayılmaz (laboratuvar kuralı: veri bitmeden kapanmayan işlem atılır) ve "KESİLDİ" olarak sayılır; gerçek ve plasebo
  KESİLDİ sayıları hücre başına yan yana raporlanır. Yanlılığın yönü: 300 bara kadar süren trend işlemlerinden 2019–2020'de
  açılıp hâlâ süren, çoğunlukla kazanan uzun işlemler düşer; yani yanlılık kurala KARŞIDIR (muhafazakâr). Bilgi satırı
  (hükme girmez): KESİLDİ işlemler 2020-07-31'in son kapanışında piyasaya göre kapatılmış sayılarak ayrıca raporlanır.
- Dukascopy 2020-08 → 2026-09 yalnız "görülmüş dönem" bilgisidir (aynı temizlik, gün, segment kuralları).

### Binance (aile B'nin hükmü; aile A için görülmüş/mekân bilgisi)

- gold_v1'in önbelleği ve yükleyicileri aynen: spot PAXGUSDT 1h / 4h / 1d (2020-08-28 →), USDⓈ-M XAUUSDT vadeli (2025-12 →),
  PAXGUSDT vadeli (2025-03 →), fonlama arşivleri. Barlar Binance'in kendi UTC barlarıdır (7/24, hafta sonu dahil).
- Pencere sonu 2026-09-30 23:59 UTC. Eksik bar oranı ve 24 saatten uzun boşluklar gold_v1'deki gibi raporlanır. Segment
  kuralı Binance serilerinde de geçerlidir; eşik 48 saattir (7/24 piyasa).

## Maliyet ve fonlama

- Botun modeli: taraf başına taker %0,05 + kayma 3 bps (gidiş-dönüş ≈ %0,16), `LabConfig` varsayılanı.
- Dukascopy BID serisinde iki yön de BID fiyatıyla simüle edilir; spread ayrıca modellenmez, maliyet modelinin içinde
  sayılır. 2006–2010 spot spread'leri bugünkünden genişti; bu bir sınırdır ve raporda yazılır.
- **Fonlama vekili (hüküm serileri):** sahibin defteri bir XAUUSDT vadeli defteri olacaktır. Binance'te prim ≈ 0 iken temel
  fonlama, faiz bileşeni olan 8 saatte %0,01'dir (ayda ≈ %0,9; long öder, short alır). Fonlaması olmayan hüküm serilerinde
  (Dukascopy, PAXG spot) her işlem için vekilli net R yazılır:
  `R_fon = R − s × 0,0001 × n_8s × giriş / risk`.
  s = +1 LONG, −1 SHORT. n_8s = giriş anı < t ≤ çıkış anı aralığındaki 00:00/08:00/16:00 UTC uzlaşmalarının sayısıdır
  (hafta sonları dahil; vadeli pozisyon 7/24 taşınır). Çıkış anı: kural ya da `hold` çıkışında çıkış barının açılış anı;
  stopta stop barının kapanış anı; süre (`max_bars`) ve `month_end` çıkışında son barın kapanış anı. Plaseboya aynen
  uygulanır.
  - Hüküm (standart ve sıkı) laboratuvarın tanımıyla fonlamasız R üzerinden verilir; R_fon özeti her satırda yanında basılır.
  - **"Hedefi karşılıyor", "daha yüksek risk" satırı ve bölüm C YALNIZ R_fon ile hesaplanır.**
- Vadeli mekân satırlarında gerçek fonlama uygulanır: net R = R − `funding_r` (`futures_lab.funding_carry` tanımı). Fonlama
  dizisi kesintiliyse net R yoktur (NaN, sayılır, doldurulmaz); aynı atlama plaseboya da uygulanır.
- Aylık tutuşlu varyantlarda her ay ayrı bir işlem sayılır ve maliyet her ay yeniden ödenir (pozisyon aynı yönde sürse
  bile). Bu bilerek seçilmiş muhafazakâr bir varsayımdır.

## İşlem modeli

`signal_lab.simulate_rule`'un kuralları aynen geçerlidir. gold_lab_v2 yeni çıkış türleri ekler (`channel20`, `month_end`),
ama aynı anlamla:

- Karar karar barının kapanışında verilir; giriş sonraki barın açılışındadır.
- İlk stop bar içinde denetlenir (kötümser). Giriş barından sonra boşlukla açılışta açılış fiyatı kullanılır.
- Kural çıkışı bar k'nın kapanışında tetiklenirse çıkış k+1'in açılışındadır. `max_bars` dolarsa son barın kapanışında
  çıkılır. `month_end` çıkışı ay m+1'in son günlük barının kapanışındadır (aşağıda).
- Veri bitmeden kapanmayan işlem sayılmaz (KESİLDİ); segment sonunu aşan işlem sayılmaz (BOŞLUK_TUTUŞ).
- Risk = |giriş − stop|. Risk ≤ 0,1 ATR ya da > 10 ATR ise işlem yoktur; buradaki ATR, hücrenin stop ATR'sidir (aile A:
  kendi diliminin ATR14'ü; aile B: ATR_d). Kovalama kuralı yoktur (simulate_rule'da da yok).
- R = (yön × (çıkış − giriş) − maliyet) / risk.
- Aynı varyant ve yön içinde üst üste binen işlemlere laboratuvardaki gibi izin verilir. Eşzamanlı açık işlem sayısının en
  yükseği ve ortalaması raporlanır.

## Ortak tanımlar

- **ATR:** laboratuvarın ATR14'ü (`signal_lab.indicators`, Wilder, `alpha = 1/14`), ilgili dilimin barlarında, segment
  içinde.
- **Kanal:** hiN[i] = high[i−N..i−1]'in en büyüğü, loN[i] = low[i−N..i−1]'in en küçüğü (bar i HARİÇ).
- **mom28[i]** = close[i] / close[i−28] − 1. **SMA200(1d)** = son 200 günlük kapanışın ortalaması (gün dahil).
- **Isınma (segment başına):** gün ve 4h kurallarında segment başından sayılan i ≥ 210 ve bütün girdiler aynı segmentte
  tanımlı olmadan karar yoktur (signal_lab algoritma kuralı). `A_4H_DONCH_D200`'ün SMA200(1d) şartı için gereken 200 günlük
  bar da aynı segmentte olmalıdır. Aylık kurallarda i_m segment başından en az 30 günlük bar sonra olmalı ve geri bakılan
  bütün ay sonu barları (i_{m−L}; `A_SMA10M`'de i_{m−9}..i_m) aynı segmentte ve kullanılabilir aylarda olmalıdır.
- **Ay sonu kararı:** ay m'nin ay sonu barı i_m ayın son 3 Pzt–Cum günü içinde olmalıdır; değilse ay kullanılamaz. Karar
  i_m'nin kapanışında verilir; giriş ay m+1'in ilk günlük barının açılışındadır. Çıkış (`month_end`, ayrı çıkış türü): ay
  m+1'in son günlük barının KAPANIŞINDA; ay m+2'nin verisine bağlı değildir. Bu çıkış `max_bars` ile kısaltılmaz (güvenlik
  sınırı 31 günlük bar; bir ayda en çok 23 işlem günü olduğu için hiç bağlamaz). Ay m, geri bakılan aylar ya da m+1
  kullanılamıyorsa ya da aynı segmentte değilse o ay karar yoktur.
- **Hafta sonu zamanları (aile B):** F = Cuma 17:00 America/New_York, S = Pazar 18:00 America/New_York (ana altın vadeli
  piyasasının haftalık kapanışı ve açılışı). Her biri KENDİ tarihinde `zoneinfo` ile UTC'ye çevrilir. Yaz saatinde
  F = 21:00, S = 22:00 UTC; kış saatinde F = 22:00, S = 23:00 UTC. ABD saat değişimi hafta sonlarında pencere 49 saat yerine
  48 saat (Mart) ya da 50 saat (Kasım) olur. Tatil düzeltmesi yapılmaz.

## Varyantlar (12 varyant; her biri LONG ve İKİ YÖN olarak 2 hücre, toplam 24 hüküm hücresi)

Tanımlar LONG içindir; SHORT tamamen simetriktir. "LONG" hücresi yalnız-long sistemdir. "İKİ YÖN" hücresi long/short
sistemdir: LONG ve SHORT işlemlerinin birleşimi, tek grup olarak hükme girer. SHORT tek başına bilgi olarak raporlanır,
hücre sayılmaz. "Hüküm hücresi", gold_v1'deki "birincil hücre" ile aynı anlamdadır (deneme sayısına giren hücre); ANA ve
İKİNCİL ayrımı "Çoklu deneme"dedir.

### A) Trend takibi — Dukascopy XAUUSD (8 varyant)

Gerekçe: zaman serisi momentumu (Moskowitz–Ooi–Pedersen 2012, "Time Series Momentum"; altın dahil emtia vadelilerinde 1–12
aylık getirinin işareti sonraki ayı öngörüyor), kaplumbağa kanal kırılımları (Donchian; Turtle Sistem 1 = 20/10, Sistem 2 =
55/20, ilk stop 2N) ve aylık hareketli ortalama rejimi (Faber 2007, "A Quantitative Approach to Tactical Asset Allocation";
ay sonu kapanışı > 10 aylık ortalama). Mekanizma iddiası: yatırımcıların bilgiye yavaş tepkisi ve sürü davranışı fiyatı
haftalar–aylar boyunca aynı yönde taşır. Tek varlıkta bu etki zayıf ve gürültülüdür; bu yüzden maruziyet eşli plasebo
karşılaştırması zorunludur.

| Ad | Dilim | Giriş (karar barı i) | İlk stop | Çıkış |
|---|---|---|---|---|
| `A_DONCH_20_10` | 1d | signal_lab `TREND_DONCHIAN_20_10` aynen: close[i] > hi20[i] ve close[i−1] ≤ hi20[i−1] | close[i] − 2 ATR[i] | `channel`: close[k] < lo10[k] → k+1 açılışı; en çok 300 bar |
| `A_TSMOM_28` | 1d | signal_lab `TSMOM_28` aynen: mom28[i] > 0 ≥ mom28[i−1] | close[i] − 3 ATR[i] | `sign`: mom28[k] ≤ 0 → k+1 açılışı; en çok 300 bar |
| `A_DONCH_55_20` | 1d | close[i] > hi55[i] ve close[i−1] ≤ hi55[i−1] (Turtle Sistem 2) | close[i] − 2 ATR[i] | `channel20` (yeni tür): close[k] < lo20[k] → k+1 açılışı; en çok 300 bar |
| `A_TSMOM_1M` | aylık (1d barlar) | Ay sonu: close[i_m] / close[i_{m−1}] − 1 > 0 | close[i_m] − 5 ATR[i_m] | `month_end` (ay m+1'in son barının kapanışı) |
| `A_TSMOM_3M` | aylık | close[i_m] / close[i_{m−3}] − 1 > 0 | close[i_m] − 5 ATR[i_m] | `month_end` |
| `A_TSMOM_12M` | aylık | close[i_m] / close[i_{m−12}] − 1 > 0 (MOP'un ana tanımı) | close[i_m] − 5 ATR[i_m] | `month_end` |
| `A_SMA10M` | aylık | close[i_m] > son 10 ay sonu kapanışının ortalaması (m dahil) | close[i_m] − 5 ATR[i_m] | `month_end` |
| `A_4H_DONCH_D200` | 4h | 4h'te close[i] > hi20[i] ve close[i−1] ≤ hi20[i−1] VE kapanış anı ≤ karar anı olan son günlük barda close > SMA200(1d) | close[i] − 2 ATR(4h)[i] | `channel` (4h): close[k] < lo10[k] → k+1 açılışı; en çok 180 bar (30 işlem günü) |

Notlar:
- Aylık varyantlarda getiri tam 0 ya da kapanış ortalamaya eşitse karar yoktur. Stopla çıkılırsa bir sonraki ay sonu
  kararına kadar yeniden giriş yoktur.
- Aylık varyantların 5 ATR stopu "felaket stopu"dur: R birimini tanımlamak içindir. Gerekçe: bir aylık fiyat oynaklığı
  kabaca √21 günlük oynaklık ≈ 4–5 günlük σ'dır; 5 ATR bunun biraz üstündedir, yani stop olağan ay içi gürültüde
  tetiklenmemeli. MOP'ta stop yoktur; bu bir sapmadır ve raporda yazılır.
- `A_DONCH_20_10` ve `A_TSMOM_28` gold_v1'deki izlerin birebir aynısıdır; parametreleri değiştirilmez. Bar sayısı aynıdır ama
  takvim süresi aynı değildir: Dukascopy'de haftada 5, PAXG'de 7 günlük bar vardır (20 bar Dukascopy'de ≈ 4 hafta, PAXG'de
  ≈ 3 hafta).
- `A_DONCH_55_20` yeni bir kanal çıkış türü (`channel20`: LONG'da lo20, SHORT'ta hi20) gerektirir; signal_lab'ın `channel`
  türü lo10/hi10'a bağlıdır ve burada kullanılamaz.

### B) Hafta sonu geri dönüşü — PAXG spot 1h (4 varyant)

Gerekçe: ana altın piyasası (COMEX vadelileri, Londra/OTC spot) Cuma 17:00 NY ile Pazar 18:00 NY arasında kapalıdır;
Dukascopy'de hafta sonu barı yoktur. PAXG ve Binance'in altın vadelileri bu sürede ince likiditeyle işlem görür. Hipotez:
hafta sonu fiyatı ana piyasanın fiyat keşfi olmadan, küçük akışlarla sürüklenir. Ana piyasa açılınca fiyat arbitrajla ana
piyasanın seviyesine çekilir; bu yüzden hafta sonu hareketinin bir kısmı geri döner. Benzer bulgular: likidite sağlayıcılara
ödenen prim ve ters yönlü getiri (Nagel 2012, "Evaporating Liquidity"; Campbell–Grossman–Wang 1993), piyasa kapalıyken oluşan
fiyatların açılışta kısmen düzeltilmesi (French–Roll 1986). Bu kripto–altın köprüsü için yayımlanmış doğrudan bir çalışma
bilinmiyor; hipotez bu yüzden zayıf önseldir. Karşı hipotez: hafta sonu hareketi gerçek habere (jeopolitik) tepkidir ve ana
piyasa onu teyit eder (devam). Devam burada SINANMAZ; geri dönüş zarar ederse bu, devamı kanıtlamaz (yeni ön kayıt gerekir).

Ortak tanımlar (her hafta w için):
- P_F = PAXG 1h barının kapanışı; bar açılışı F − 1 saat (kapanışı F). P_S = açılışı S − 1 saat olan barın kapanışı. Karar
  barı i = bu son bar; giriş S'de açılan barın açılışında.
- ATR_d = PAXG Binance 1d barlarında ATR14; kapanış anı ≤ F olan son günlük bar (Perşembe barı, Cuma 00:00 UTC'de kapanır).
- z = (P_S − P_F) / ATR_d. z < 0 (hafta sonu düşüş) → LONG; z > 0 → SHORT; z = 0 → işlem yok.
- Stop = close[i] ∓ 1,0 ATR_d (LONG −). Risk filtresi (0,1–10 ATR) ATR_d ile uygulanır; `simulate_rule`'un varsayılan 1h
  ATR'si KULLANILMAZ (hafta sonu 1h ATR'si çok küçüktür ve işlemleri yanlışlıkla STOP_TOO_FAR diye reddederdi).
- Çıkış (`hold`): N saatlik bar sonra, yani S + N saatte açılan barın açılışında; stop önce gelirse stop.
- Atlamalar (ikisi de sayılır, plaseboya da uygulanır): F − 1 saat, S − 1 saat ya da tutuş barlarından biri eksikse o hafta
  atlanır ("EKSİK_BAR"). F − 1 saat ya da S − 1 saat barının hacmi 0 ise (bayat fiyat; çoğu 2020–2021) o hafta atlanır
  ("HACİMSİZ").
- Bilgi: tatilden etkilenen hafta sonları (Kutsal Cuma; Noel ya da Yılbaşı Cuma'ya düşerse) sayılır. Bu haftalarda F ana
  piyasanın gerçek kapanışı değildir; kural değişmez.
- Uygulanabilirlik notları: açılış anındaki ince hafta sonu defterinde 3 bps kayma iyimser olabilir. Spotta SHORT
  varsayımsaldır; gerçekte vadelide yapılırdı.

| Ad | Eşik | Tutuş N | Çıkış anı | Gerekçe |
|---|---|---|---|---|
| `B_WKND_REV_ALL` | \|z\| > 0 | 23 saat | S + 23 saat = Pazartesi 17:00 NY (ana piyasanın ilk gün kapanışı) | Eşiksiz: bütün haftalar, en büyük örneklem |
| `B_WKND_REV_050` | \|z\| ≥ 0,5 | 23 saat | Pazartesi 17:00 NY | Yalnız belirgin hafta sonu hareketleri |
| `B_WKND_REV_100` | \|z\| ≥ 1,0 | 23 saat | Pazartesi 17:00 NY | Yalnız büyük hareketler (daha az işlem) |
| `B_WKND_REV_050_LDN` | \|z\| ≥ 0,5 | 9 saat | S + 9 saat ≈ Londra 08:00 (Londra açılışı) | Geri dönüş açılışın ilk, ince Asya saatlerinde olur mu |

Not: S + 9 saat, ABD ve Avrupa yaz saati geçişlerinin örtüşmediği yılda birkaç haftada Londra 08:00'dan bir saat sapar;
kural zamanla değil, saat sayısıyla tanımlıdır.

### C) Oynaklık hedefli boyut (bilgi, ayrı hipotez DEĞİL)

Aynı işlemlerin aylık %'ye başka bir dönüşümüdür; hüküm, deneme sayısı ve hedef kararına girmez. Sahibin bunu bir sonuç
sanmaması için:
- **C YALNIZ sıkı hükmü GÜÇLÜ ADAY olan hücreler için basılır.** Diğer hücrelerde sayı basılmaz, yalnız "kenar yok" yazılır.
  Basıldığında "daha yüksek risk" satırındaki uyarının aynısı, %95 aralık ve en derin düşüş yanında yazılır.
- İşlemin net getirisi g = R_fon × risk / giriş (maliyet ve fonlama vekili dahil, kesir).
- Ağırlık f = min(0,10 / σ_yıllık, 2,0). σ_yıllık = kapanış anı ≤ karar anı olan son 61 günlük barın (aynı segmentte) 60
  günlük log getirisinin standart sapması × √(yıllık bar sayısı): Dukascopy için 260, Binance 1d için 365. Aile B'de bu
  barlar kapanış anı ≤ S olan Binance 1d barlarıdır. Tavan 2,0 = `futures_v3.leverage_max_paper_research`.
- **Toplam ağırlık tavanı:** bir hücrenin aynı anda açık işlemlerinin Σ f'si 2,0'ı aşamaz. Yeni işlem giriş sırasıyla
  ağırlık alır: f_yeni = min(f, 2,0 − Σ f_açık); bu 0 ya da altındaysa işlemin C'deki ağırlığı 0'dır. Tavansız en yüksek
  toplam ağırlık ve kısılan işlem sayısı bilgi olarak raporlanır; 2'yi aşmışsa işaretlenir.
- Aylık % = karar ayındaki işlemlerin Σ f × g × 100'ü. Doğrulama ortalaması, ay kümeli %95 aralığı ve bütün hüküm serisi
  üzerindeki en derin düşüş (aile A 2006–2020, 2008 ve 2013 dahil; aile B 2020-08 → 2026-09) raporlanır.

## Plasebo (hüküm plasebosu; her hüküm hücresi için eşleştirilmiş)

Ortak: seçimler crc32 ile belirlenimcidir. Maliyet, fonlama vekili ve bütün atlama kuralları (segment/BOŞLUK, EKSİK_BAR,
HACİMSİZ, mekân satırlarında kesintili fonlama) plaseboya da uygulanır. Veri bitmeden kapanmayan plasebo işlemi sayılmaz
(KESİLDİ) ve sayısı gerçek işlemlerinkiyle yan yana yazılır. `vs_placebo`, `diff_ci` ve `verdict_strict` YALNIZ aşağıdaki
hüküm plasebosuyla hesaplanır.

- **A, gün/4h kural çıkışlı varyantlar (`A_DONCH_20_10`, `A_TSMOM_28`, `A_DONCH_55_20`, `A_4H_DONCH_D200`) — maruziyet eşli
  plasebo:** `sign` ve `channel` çıkışlarında rastgele zamanda açılan bir giriş çoğu kez çıkış şartını hemen karşılar (ör.
  mom28 ≤ 0 iken açılan TSMOM plasebosu bir bar sonra çıkar). Böyle bir plasebonun piyasa maruziyeti gerçek işlemlerden çok
  azdır ve yükselen piyasada yalnız tutmakla geçilebilir. Bu yüzden hüküm plasebosu şöyledir:
  - Her tamamlanmış gerçek işlem τ için (karar barı i_τ, yön s_τ, dönemi P_τ) **K = 5** plasebo işlemi açılır.
  - Aday kümesi C(P_τ): kararı P_τ döneminde olan, ısınmayı geçmiş, ATR'si tanımlı ve işlemi H_τ bar boyunca aynı segmentte
    ve seri sonundan önce tamamlanabilecek bütün barlar. Varyantın giriş şartı ve filtreleri UYGULANMAZ.
  - k = 1..5 için plasebo karar barı = C(P_τ)[crc32("<sembol>|<dilim>|<ad>|<yön>|<ts[i_τ]>|<k>") mod |C(P_τ)|].
  - Yön = s_τ; stop = close[c] ∓ (varyantın ATR katı) × ATR[c]; çıkış `hold`: H_τ bar sonra, (c+1+H_τ) barının açılışında;
    stop önce gelirse stop (bar içinde, kötümser).
  - H_τ yalnız gerçek işlemin tutuş uzunluğuna bağlıdır, R'sine değil: kural çıkışında H_τ = çıkış barı − giriş barı;
    stopta H_τ = stop barı − giriş barı + 1; süre çıkışında H_τ = `max_bars`.
  - LONG hücresinin plasebosu LONG işlemlerinin eşleridir; İKİ YÖN hücresinin plasebosu iki yönün eşlerinin birleşimidir.
  - `A_4H_DONCH_D200`'ün plasebosu SMA200(1d) filtresini İÇERMEZ; bu yüzden orada "plaseboyu geçiyor" rejim filtresinin
    katkısını da kapsar. Raporda böyle yazılır.
- **A, aylık varyantlar:** LONG hücresi: aday = kararın mümkün olduğu bütün ay sonu barları (kullanılabilir ay, segment,
  ATR; kuralın işaret şartı hariç), hüküm serisinin tamamında. u = crc32("<sembol>|<dilim>|<ad>|LONG|<ts[i]>") / 2³² < p ise
  LONG plasebo; p = LONG sinyal sayısı / aday sayısı (sonuçtan bağımsız sayım). Stop 5 ATR, çıkış `month_end`. İKİ YÖN
  hücresi: **işaret rastgeleleştirme** — gerçek İKİ YÖN hücresinin karar verdiği aylarda, yön =
  crc32("<sembol>|<dilim>|<ad>|İKİ|<ts[i]>") / 2³² < 0,5 ise LONG, değilse SHORT; stop ve çıkış aynı. Aylık işlemler bir ay
  tutulduğu için maruziyet zaten eşittir.
- **B — S'ye bağlı plasebo:** aday = uygun haftaların S karar barları (EKSİK_BAR ve HACİMSİZ atlamalarını geçen, ATR_d'si
  tanımlı her hafta; z'den bağımsız). LONG hücresi: u = crc32("PAXGUSDT|1h|<ad>|LONG|<ts[i]>") / 2³² < p ise LONG;
  p = LONG sinyal sayısı / uygun hafta sayısı. İKİ YÖN hücresi: işaret rastgeleleştirme — hücrenin eşiği geçen (gerçek
  işlem açılan) haftalarında yön = crc32("PAXGUSDT|1h|<ad>|İKİ|<ts[i]>") / 2³² < 0,5 ise LONG, değilse SHORT. Stop 1 ATR_d
  (o haftanın ATR_d'si, kapanış ≤ F) ve tutuş N gerçek işlemle aynıdır. Böylece plasebo aynı Pazar akşamı anında, aynı
  tutuşla girer; hafta sonu ince saatleri ve yalnız sabit yönlü kayma karşılaştırmayı bozmaz.
- SHORT bilgi satırlarının plasebosu aynı kuralın SHORT eşidir.
- **Bilgi plaseboları (hükme girmez):**
  - A gün/4h için gold_v1/signal_lab tarzı kural çıkışlı plasebo: aday = ısınmadan sonra ATR'si tanımlı her bar;
    u = crc32("<sembol>|<dilim>|<ad>|<yön>|<ts[i]>") / 2³² < p, p = sinyal sayısı / aday bar sayısı; stop ve çıkış
    varyantınki (aynı ATR katı, aynı çıkış türü ve `max_bars`).
  - B için haftanın her saatinden plasebo: aday = ATR_d'si tanımlı her PAXG 1h barı; ATR_d, 1h bar i'nin kapanışından önce
    ya da tam o anda kapanan son Binance 1d barının ATR14'üdür; stop close[i] ∓ 1 ATR_d, tutuş aynı N.
  - Taslaktaki ayrı hafta sonu zaman kontrolü (WKND_CTRL) kaldırıldı: tasarımı `B_WKND_REV_ALL` İKİ YÖN hücresinin hüküm
    plasebosuyla aynıdır.

## Dönemler ve hüküm

- **Aile A (hüküm yalnız Dukascopy):** keşif 2006-01-01 → 2013-12-31, doğrulama 2014-01-01 → 2020-07-31 (UTC, karar anına
  göre). Laboratuvarın 2/3 kesimi KULLANILMAZ. Göstergeler segment içinde ısınır. Keşif sonuna yakın açılan işlem 2014'e
  taşabilir; hiçbir şey ayarlanmadığı için bu kabul edilir.
- **Aile B:** keşif 2020-08-28 → 2023-12-31, doğrulama 2024-01-01 → 2026-09-30, PAXG spot, karar anına (S) göre.
- Hüküm fonksiyonları değiştirilmeden: `signal_lab.r_stats` (gün kümeli bootstrap, 1000 tekrar), `verdict` (en az 30 keşif ve
  20 doğrulama işlemi; GÜÇLÜ ADAY / ZAYIF İZ / KAYBETTİRİR / KANIT YOK / VERİ AZ), hüküm plasebosuna göre fark (plasebonun
  iki dönemde de n ≥ 20 olması şartı), `diff_ci` + `verdict_strict` (`STRICT_RULE_TR`, `STRICT_SEED`). Yalnız sabit tarihli
  dönem etiketi `aggregate`'in kesiminin yerine geçer. Hüküm fonlamasız R ile verilir; R_fon özeti yanında basılır.
- Bilgi sütunu: ortalama R için ay kümeli %95 aralığı (aynı varyantın üst üste binen işlemleri aynı fiyat yolunu paylaştığı
  için gün kümeli aralık gün/4h trend hücrelerinde fazla dar olabilir). Hükme girmez.
- Hüküm hücreleri: 12 varyant × {LONG, İKİ YÖN}, bağlam "HEPSİ". Bağlam dilimleri hesaplanmaz.

## Aylık hedef ölçüsü (yalnız doğrulama dönemi)

- Takvim ayı başına, karar anı o ayda olan doğrulama işlemlerinin toplam **R_fon**'u (`gold_lab.monthly_stats` düzeni,
  `key` = R_fon). İşlemsiz ay 0 sayılır. Aylık % = ay R_fon × 0,5 (işlem başına %0,5 risk, botun standardı). Ay kümeli
  bootstrap %95 aralığı (`MONTH_SEED`) ve ≥ +%1 ay payı. Bileşik getiri yoktur. Aynı ölçü fonlamasız R ile bilgi olarak
  basılır.
- Ölçülen karar ayları: **aile A 2014-01 → 2020-06 (78 ay)**, `monthly_stats`'ın `open_end_ms`'i = 2020-07-01 00:00 UTC.
  Aylık varyantlarda Haziran 2020 kararı Temmuz 2020'nin son barının kapanışında (2020-07-31) çıktığı için serinin içinde
  kalır. Temmuz 2020'de verilen kararlar kenar ay olarak bilgidir. **Aile B 2024-01 → 2026-09 (33 ay).**
- Aylık varyantlarda karar ayına yazma, her işlemi tutulduğu aydan bir ay ÖNCE raporlar (Haziran'ın R'si Temmuz'da
  kazanılır). Raporda böyle yazılır.
- **Hedefi karşılıyor** = sıkı hüküm GÜÇLÜ ADAY VE doğrulama ortalama aylık net (R_fon, yuvarlanmamış) ≥ +%1, %0,5 riskte
  (`gold_lab.meets_target` mantığı, R_fon ile).
- Hedef her zaman nokta tahmini, %95 aralığı ve ≥ +%1 ay payıyla birlikte yazılır. Ortalamanın %1'e tam yetmesi, gerçek
  ortalamanın %1'in altında olma olasılığının kabaca %50 olduğu anlamına gelir; bu cümle raporda yer alır.
- **"Daha yüksek risk" bilgi satırı (yalnız bilgi; kenar değildir):**
  1. X ve aşağıdaki bütün sayılar YALNIZ sıkı hükmü GÜÇLÜ ADAY olan hücreler için hesaplanır ve basılır. Diğer bütün
     hücrelerde sayı basılmaz; satırda yalnız "kenar yok" yazar.
  2. X = 1 / (ortalama aylık R_fon) yüzde; ortalama aylık R_fon ≤ 0 ise X yoktur ve satırda "fonlama sonrası kenar yok"
     yazar. Metin tam olarak şudur: "ortalama tahmin X% riskte +%1/ay'a karşılık gelir; %95 aralık [a × X / 0,5,
     b × X / 0,5]; ulaşılacağı garanti değildir" ([a, b] = %0,5 riskteki aylık % aralığı).
  3. X > 2,0 ise (risk profili tavanı; config_v3 "profil tavanı (2.0)", PAPER_RESEARCH `risk_per_trade_pct` 2,0) satıra
     "profil tavanını aşar — uygulanamaz" yazılır.
  4. X riskte toplam açık risk = eşzamanlı açık işlem sayısının en yükseği × X; PAPER_RESEARCH profilinin
     `max_total_open_risk_pct`'si (6,0) ve öğrenme modunun varsayılanı (100,0) ile karşılaştırılır; 6,0'ı aşarsa işaretlenir.
  5. Plasebo üstü fazla: aylık fazla R = ay R_fon − (o aydaki gerçek işlem sayısı × hüküm plasebosunun doğrulama ortalama
     R_fon'u). X_fazla = 1 / (ortalama aylık fazla R) yüzde (fazla ≤ 0 ise "plasebo üstü fazla yok"). Yön kayması payı =
     (plasebo kısmı) / (ortalama aylık R_fon) yazılır: 2019–2020 ya da 2024–2026 gibi yükselişlerde long ağırlıklı bir trend
     kuralının kaldıraçlı piyasa yönünü "kenar" diye göstermemesi için.
  6. X riskteki en derin düşüş BÜTÜN hüküm serisi üzerinde (aile A 2006–2020, 2008 ve 2013 dahil; aile B 2020-08 → 2026-09),
     işlemler çıkış anına göre sıralı, birikimli R_fon'un tepeden en derin inişi × X (bileşiksiz). Bakiyenin %20'sini aşarsa
     işaretlenir. Doğrulama dönemi düşüşü ayrıca yazılır.
  7. X riskte ima edilen kaldıraç (pozisyon değeri / bakiye = X / (risk / giriş)); medyan ve en yüksek; 2'yi
     (`leverage_max_paper_research`) ve 5'i (`leverage.max_leverage`) aşarsa işaretlenir.
  8. Not: X, hükmü veren doğrulama ortalamasının kendisinden hesaplanır (kazanan laneti); bu yüzden iyimserdir.
- **Ön hesap (sonuç değil, aritmetik):** %0,5 riskte +%1/ay, ayda ortalama +2 R demektir. Ayda en çok bir işlem açan aylık
  varyantlar için bu, işlem başına ≥ +2 R gerektirir. 5 ATR stoplu bir aylık tutuşta bu gerçekçi değildir. Aile B haftada en
  çok bir işlem açar (ayda ≈ 4,3); eşiksiz varyantta işlem başına ≥ +0,46 R, eşikli varyantlarda daha fazlası gerekir.
  Fonlama vekili ayda nominalin ≈ %0,9'udur; r = risk / giriş ise LONG için R cinsinden ≈ 0,009 / r R/ay maliyettir (örnek:
  r = %2,5 ve 30 günlük tutuş ≈ 0,36 R; r = %5 ve bir aylık tutuş ≈ 0,18 R). Dolayısıyla bu varyantlarda asıl soru kenarın
  var olup olmadığıdır; hedef çoğu durumda ancak "daha yüksek risk" satırında görülebilir ve o satır da yalnız bilgidir.

## Görülmüş veri ve mekân satırları (bilgi; hüküm DEĞİL)

- **Aile A, görülmüş:** PAXG spot 2020-08-28 → 2026-09-30 (Binance kendi 1d/4h barları, hafta sonu dahil; bar sayımlı
  kuralların takvim süresi bu yüzden Dukascopy'den kısadır) ve Dukascopy 2020-08 → 2026-09. Dönem bölünmez; n, ortalama R ve
  R_fon, %95 aralık, hüküm plasebosuna göre fark. "Görülmüş/mekân" etiketi her satırda yazılır.
- **Aile A, mekân:** XAUUSDT ve PAXGUSDT vadeli. Uzun ısınma (SMA200, 12 ay) vadeli geçmişinden uzun olduğu için sinyal PAXG
  spot barlarından hesaplanır; işlem aynı zaman damgalı vadeli barlarında simüle edilir; gerçek fonlama uygulanır.
- **Aile B, mekân:** XAUUSDT vadeli (2025-12 →) ve PAXGUSDT vadeli (2025-03 →), aynı tanım kendi 1h/1d barlarıyla; gerçek
  fonlama uygulanır. Bu dönemler doğrulama dönemiyle örtüşür; bağımsız kanıt değil, uygulama mekânı denetimidir.
- Hükmü GÜÇLÜ ADAY olan bir hücre bir mekân satırında n ≥ 20 iken net ortalama R ≤ 0 verirse "mekânda tutmadı" notu düşülür.

## Çoklu deneme

- gold_v2: 12 varyant, **24 hüküm hücresi**. Hücreler bağımsız değildir (İKİ YÖN, LONG'u içerir; trend varyantları
  birbiriyle ilişkilidir).
- **Taramalar:** B'nin dört varyantı bir eşik × tutuş taramasıdır (|z| eşiği {0; 0,5; 1,0}, iç içe; tutuş 23 saat ve 9 saat).
  `A_TSMOM_1M/3M/12M` bir geri bakış taramasıdır; `A_DONCH_20_10` / `A_DONCH_55_20` bir kanal uzunluğu taramasıdır.
  Taramadaki her hücre ayrı bir denemedir.
- **ANA hücreler (önceden kayıtlı sorular):** `A_DONCH_20_10` LONG ve `A_TSMOM_28` LONG (gold_v1 izlerinin birebir
  yinelemesi; izler LONG'du) ve `B_WKND_REV_ALL` İKİ YÖN (geri dönüş hipotezinin eşiksiz, en büyük örneklemli, simetrik
  sınaması). Diğer 21 hücre **İKİNCİL**dir.
- `B_WKND_REV_ALL` İKİ YÖN geçmezken ikincil bir B hücresinin geçmesi tarama yapıntısıdır, kanıt değildir. A'da ANA hücreler
  geçmezken ikincil bir trend hücresinin geçmesi de yalnız "yeni ön kayıt gerekir" sonucunu doğurur.
- `B_WKND_REV_100` güçsüz kalabilir (VERİ AZ çıkabilir ya da plasebosunun n'si 20'nin altında kalabilir); yine de bir deneme
  sayılır.
- Laboratuvarın rastgele yürüyüş ölçümünde hücrelerin %0–0,5'i tesadüfen GÜÇLÜ ADAY çıkıyordu (kripto laboratuvarının grup
  yapısında ölçüldü): 24 hücrede tesadüfen 0–0,12 aday beklenir. Bu oran gerçek veriye dokunmadan, bu 24 hücrenin yapısıyla
  sentetik rastgele yürüyüşlerde yeniden ölçülür ve bilgi olarak raporlanır.
- **Kümülatif altın deneme sayısı:** gold_v1'in 32 birincil hücresi + gold_v2'nin 24 hüküm hücresi = **56 hücre**; tesadüfen
  beklenen 0–0,28 aday. gold_v1'in mevcut laboratuvar setleri (1.133 keşif grubu) bu sayıya hücre olarak girmez ama
  `A_DONCH_20_10` ve `A_TSMOM_28` oradan seçildiği için seçim yanlılığı not edilir: bu iki ANA hücrenin geçmesi önceden
  belirlenmiş birinci soru olarak yorumlanır; ikincil hücrelerden tek bir aday ise 56 hücre içinde zayıf kanıttır ve öneriye
  yol açmaz.
- Gerçek hücrelerin aday oranı plasebo hücrelerinin oranıyla karşılaştırılır (laboratuvardaki gibi), aile başına ve toplam.

## Sonuç ve sonraki adım

- Sonuçtan sonra hiçbir kural ayarlanmaz. Ayarlama yeni sürüm (gold_v3), yeni ön kayıt ve yeni deneme sayısı demektir.
- **Öneri kuralı:** sahibe bir paper öneri YALNIZ şu dört şart birlikte tutarsa sunulur: bir ANA hücre (i) sıkı GÜÇLÜ ADAY,
  (ii) fonlama vekilli doğrulama ortalaması ≥ +%1/ay (%0,5 riskte), (iii) kendi ailesinin hüküm plasebo hücrelerinde aday
  oranı 0, (iv) "mekânda tutmadı" notu yok. Öneri yalnız PAPER ve **yalnız-kayıt**tır (örneğin XAUUSDT için ayrı bir paper
  defter). Sahip onayı olmadan hiçbir şey açılmaz; otomatik hiçbir şey etkinleşmez. Öneri vadeli mekân satırındaki gerçek
  fonlama etkisini, %95 aralığı, en derin düşüşü ve kazanan laneti notunu içerir.
- İkincil bir hücre hedefi karşılarsa yalnız "yeni bir ön kayıt (gold_v3) gerekir" yazılır; bu ASLA öneriye dönüşmez.
- Bir ANA hücre sıkı GÜÇLÜ ADAY ama hedefin altındaysa "kenar var, hedefin altında" diye abartısız yazılır; "daha yüksek
  risk" satırı bilgi olarak basılır; öneri yapılmaz.
- Hiçbir hücre hedefi karşılamazsa açıkça yazılır: bu kurallar altında maliyet ve fonlama sonrası +%1/ay %0,5 riskte
  bulunamadı.
- Aile A "yapılamadı" ise (kapsama eşiği ya da zaman damgası denetimi) bu açıkça yazılır; PAXG/XAUUSDT satırları onun yerine
  geçmez.
- **Sonuç belgesinin düzeni:** önce yalnız ANA hücreler (hüküm, sıkı hüküm, R ve R_fon, hüküm plasebosuna göre fark ve
  aralığı, aylık hedef ölçüsü); sonra ikincil hücreler; görülmüş veri, mekân, SHORT, bilgi plaseboları ve C satırları açıkça
  ayrılmış bir bilgi ekinde.
- Uygulama notu (bu çalışmanın dışında): günlük/aylık kararlar botun 4h turlarıyla uyumludur; aile B Pazar akşamı belirli bir
  saatte giriş ve sabit saatte çıkış gerektirir (1h tur yeterli).

## Uygulama süreci

1. Bu belge tek başına (yalnız belge) commit'lenir.
2. `tradingbot/gold_lab_v2.py` ve testleri YALNIZ sentetik veriyle yazılır ve sınanır (sabit tohumlu rastgele yürüyüş,
   sentetik bi5 dosyaları); gerçek fiyat verisine dokunulmaz.
3. Kod bu belgeye karşı ayrıca gözden geçirilir; belgenin belirsiz yerlerinin okunuşu (`readings_tr`) gerçek veri koşusundan
   önce yazılır ve mühre girer.
4. Bu 24 hücrenin tesadüfi GÜÇLÜ ADAY oranı sentetik rastgele yürüyüşlerde ölçülür (bilgi).
5. Ayna işi biter, anlık görüntü özetleri alınır, zaman damgası biçim denetimi ve kapsama eşiği geçer; gerçek veride tek
   koşu yapılır.

## Bilinen sınırlar

- Tek varlık: piyasa yönü ile kural kenarını ayırmak zordur; maruziyet eşli plasebo bunu ancak kısmen ayırır.
- Dukascopy BID, Binance fiyatı değildir; 2006–2020 spread ve likiditesi farklıydı; maliyet modeli botunkidir.
- Fonlama vekili sabit %0,01/8 saattir; gerçek XAUUSDT fonlaması primle değişir (mekân satırları gerçek fonlamayı gösterir).
- PAXG'nin 2020–2021 hafta sonu likiditesi çok inceydi; 2021Q4–2025Q1 arasında 1 USDT'lik fiyat adımları vardı (gold_v1
  veri notu). Bunlar aile B'nin keşif döneminde gürültü yaratabilir. Açılış anında 3 bps kayma iyimser olabilir; spotta
  SHORT varsayımsaldır.
- Aylık varyantlarda her ay yeniden giriş maliyeti ödenir (gerçek defter pozisyonu taşırdı); bu sonucu aşağı çeker.
- Aile A'nın önseli yayımlanmış trend takibi kanıtıyla (2016'ya kadar) örtüşür (yukarıda).
- "Daha yüksek risk" satırı ve C, hükmü veren aynı doğrulama ortalamasından türer (kazanan laneti).

## Eleştiriye yanıt

Taslağı bir eleştirmen okudu ("commit'e hazır değil"; 15 zorunlu, 9 isteğe bağlı değişiklik). Eleştirmen hiçbir sonuç, R
ya da sinyal sayımı hesaplamadı. Zorunlu değişikliklerin hiçbiri reddedilmedi; aşağıda yalnız bir seçenek seçilen ya da
değiştirilerek uygulanan noktalar ve gerekçeleri var.

- **B plasebosu:** önerildiği gibi S'ye bağlandı (LONG: sabit yön, rastgele uygun hafta; İKİ YÖN: hücrenin haftalarında
  işaret rastgeleleştirme). Her saat plasebosu bilgi satırı olarak kaldı. WKND_CTRL kaldırıldı, çünkü tasarımı
  `B_WKND_REV_ALL` İKİ YÖN hüküm plasebosuyla aynıdır.
- **A maruziyet eşli plasebo:** önerilen birinci tasarım seçildi. Eş sayısı K = 5 olarak donduruldu (plasebo ortalamasının
  gürültüsünü azaltmak için; `diff_ci` iki tarafı ayrı yeniden örneklediği için daha çok eş aralığı yapay daraltmaz). Adaylar
  gerçek işlemin dönemiyle sınırlandı (keşif/doğrulama karşılaştırması aynı piyasa döneminde kalsın diye).
- **Boşluklar:** iki seçenekten "boşluktan sonra ısınma yeniden başlar" seçildi ve segment kuralı olarak yazıldı.
  "BOŞLUK_GERİ_BAKIŞ" sinyal sayısı değil, ısınmada kalan bar sayısıdır, çünkü ısınmada gösterge tanımsızdır ve sinyal
  hesaplanamaz.
- **Zaman damgası biçim denetimi (değiştirilerek):** beklenen ilk saat tek bir değer (yaz 22:00, kış 23:00 UTC) yerine iki
  değerlik küme olarak yazıldı (yaz {21, 22}, kış {22, 23}). Gerekçe: Pazar açılışı UTC'si doğru bir seride de 17:00 New York
  (forex açılışı) ya da 18:00 New York (COMEX açılışı) olabilir; tek değerlik denetim doğru bir seriyi durdurabilirdi. Buna
  bağlı olarak işlem günü sınırı sabit "t + 2 saat UTC" yerine 17:00 New York'a alındı. Böylece 21:00 UTC'lik bir Pazar barı
  atılmaz, Pazartesi'ye girer. Cumartesi/Pazar'a düşen saatler yine sayılır.
- **Aylık hedef ayları:** seçenek (b) seçildi: `month_end` çıkışı ay m+1'in son barının kapanışıdır. Ölçülen karar ayları
  2014-01 → 2020-06 olarak kaldı ve `open_end_ms` açıkça yazıldı.
- **C toplam ağırlık:** "rapor et ve işaretle" yerine tavan uygulandı (Σ f ≤ 2,0, giriş sırasıyla); tavansız en yüksek değer
  ayrıca raporlanır.
- **ANA hücreler:** `A_TSMOM_12M` ANA yapılmadı (eleştiride isteğe bağlıydı). Gerekçe: ayda en çok bir işlem açan aylık bir
  varyantın %0,5 riskte hedefe ulaşması için işlem başına ≥ +2 R gerekir (Ön hesap); öneri kuralı ANA hücreye bağlı olduğu
  için ANA kümeyi büyütmek yalnız çoklu deneme yükünü artırırdı. A'nın ANA hücreleri yinelenen izlerin yönü olan LONG,
  B'ninki hipotezin simetrik sınaması olan İKİ YÖN olarak seçildi.
- **Öneri kuralı, plasebo aday oranı:** "plasebo hücrelerinin aday oranı 0" şartı ANA hücrenin KENDİ ailesinin hüküm
  plasebolarına uygulanır; toplam oran da raporlanır. Gerekçe: A ve B plaseboları farklı seriler ve dönemlerdedir; 2006–2020
  Dukascopy'de kayan bir LONG plasebo, PAXG hafta sonu plasebosu hakkında bilgi vermez.
- **İsteğe bağlı değişiklikler:** dokuzu da uygulandı: sağdan kesilme (yön, yan yana KESİLDİ sayıları, piyasaya göre kapanış
  bilgi satırı), ay kümeli aralık bilgi sütunu, hedefin aralık ve ay payıyla yazılması, HACİMSİZ atlaması ve uygulanabilirlik
  notları, kısa seans ve tatil hafta sonu sayımları, bar/takvim süresi notu, sentetik yeniden kalibrasyon, sonuç belgesinin
  düzeni ve uygulama süreci.

## Deneme sayısı

gold_v2: 12 varyant (24 hüküm hücresi; 3'ü ANA). Kümülatif altın: 56 hücre (gold_v1 32 + gold_v2 24).
