# Kalabalık laboratuvarı — fut_v2: kalabalığı takip mi, kalabalığa karşı mı? (2026-09-30)

Sinyal laboratuvarına kalabalık verisi eklenir: taker (agresif alış/satış) akışı, açık pozisyon (OI), long/short oranları ve
fonlama. Salt araştırmadır. Hiçbir defter, strateji, ajan ya da parametre değişmez. Kâr garantisi değildir; geçmiş testtir.

**Ön kayıt mührü: `CROWD_REGISTRY_SHA = 14cbf0523ef25ef1`** (`tradingbot/crowd_lab.py`, `CROWD_VERSION = "fut_v2"`).
- Mühür bir testle sabittir (`tests/test_crowd_lab.py`, `PINNED_SHA`). Bu belge yoklamadan ve doğrulayıcı koşudan ÖNCE yazıldı.
- Mühre giren her şey `CROWD_REGISTRY`'dedir: bütün sabitler, kural ve kontrol metinleri, plasebo tanımı, çıkış/maliyet
  ayarları (`exit_cfg`), istatistik, katkı ve defter metinleri, hipotez listesi, `CROWD_FEATURE_CONSTANTS` (formül metinleri
  dahil), fut_v1'in `FF.FEATURE_CONSTANTS`'ı, arşiv sütun eşlemesi (`COLUMN_MAP`) ve hizalama kuralı.
- Biri değişirse mühür değişir: yeni sürüm, bu belgenin güncellenmesi ve yeni deneme sayısı demektir. Sayımlar ya da
  sonuçlar görüldükten sonra kural, eşik ya da dilim GEVŞETİLMEZ.
- fut_v1 mührü (`FUT_REGISTRY_SHA = 6f08eca51d8b97ee`, `docs/FUTURES_OI_FUNDING_LAB.md`) değişmedi.
- Deneme sayısı: bugüne kadar **2 vadeli aile, 16 hipotez** (fut_v1: 8, aday çıkmadı; fut_v2: 8).

## Ne sınanıyor

Soru: fiyat tetiği aynıyken kalabalığın durumu işlemin sonucunu değiştiriyor mu? Değiştiriyorsa kalabalığı takip etmek mi,
kalabalığa karşı gitmek mi kazandırıyor?

Kalabalık iki ayrı şeyle ölçülür:
- **Herkes nerede (konum, `pos_side`):** tüm hesapların long/short oranı, büyük traderların pozisyon long/short oranı ve
  fonlama oranı. Her biri son 20 güne göre z-puanına çevrilir, ±3'te kırpılır ve ortalaması `crowd_z` olur.
  `crowd_z ≥ +1` → LONG, `≤ −1` → SHORT, arası NONE.
- **Şu an kim giriyor (akış, `flow_side`):** 24 saatlik OI değişiminin z-puanı (`oi_z`, fut_v1 tanımı) ve 24 saatlik taker
  dengesinin z-puanı (`cvd_share_z`). `oi_z ≥ 1` ve `cvd_share_z ≥ +1` → LONG, `oi_z ≥ 1` ve `cvd_share_z ≤ −1` → SHORT, aksi NONE.

Not: ana botun piyasa ajanı bugün zaten "kalabalık çok long ise long'a karşı" yönünde küçük bir eğilim veriyor
(`agents/market.py`, `coinhead/factors.py`). Bu eğilim hiç sınanmadı. Bu laboratuvar o sezgiyi değiştirmez; yalnız ölçer.

## Veri (yalnız data.binance.vision arşivi)

REST yoktur (GitHub koşucularında fapi 451 döner). Önbellek ayrıdır: `signal_lab_data/crowd/`.

| Kaynak | Arşiv dosyası | Sütun → alan | Kullanım |
|---|---|---|---|
| Açık pozisyon | `daily/metrics` (5 dk) | `sum_open_interest` → `oi` (baz birim; `_value` kullanılmaz) | `oi_z`, `oi_quad` (fut_v1 tanımları) |
| Tüm hesaplar L/S | `daily/metrics` | `count_long_short_ratio` → `gls` | kalabalık puanı |
| Büyük trader pozisyon L/S | `daily/metrics` | `sum_toptrader_long_short_ratio` → `tpls` | kalabalık puanı |
| Büyük trader hesap L/S | `daily/metrics` | `count_toptrader_long_short_ratio` → `tals` | yalnız bilgi |
| Taker L/S hacim oranı | `daily/metrics` | `sum_taker_long_short_vol_ratio` → `tkls` | yalnız bilgi |
| Taker alış hacmi | `{monthly,daily}/klines/{sym}/{1h,4h}` | sütun 0 açılış, 5 hacim, 7 quote hacim, 8 işlem sayısı, 9 `taker_buy_volume` → `tb` | `dshare`, `cvd_share` |
| Fonlama | `monthly/fundingRate` (fut_v1 önbelleği) | `last_funding_rate` | `f8`, `f8_z` |

Ayrıştırma kuralları (`tradingbot/crowd_data.py`):
- Metrics başlıktan okunur; `create_time` ve `sum_open_interest` zorunludur (yoksa hata; sessiz NaN yok). Başlıksız dosyada
  konum 0, 2, 4, 5, 6, 7 kullanılır.
- Bir hücre sonlu değilse ya da ≤ 0 ise yalnız o hücre NaN olur; satır atılmaz. Zamanı okunamayan satırı olan dosya `error`
  sayılır ve önbelleğe girmez (fut_v1 kuralı).
- Mum dosyalarında µs zaman damgası ms'ye çevrilir. Bitmiş aylar ay dosyasından, içinde bulunulan ay (ve ay dosyası henüz
  yayımlanmamış yakın ay) gün dosyalarından alınır.
- Taker satırı laboratuvarın kendi mumuna açılış zamanıyla bağlanır. Hacim uyuşmazsa (göreli fark > 1e-9) o barın taker'ı
  NaN olur. `tb` geçersizse (sonlu değil, `tb < 0`, `tb > v·(1+1e-9)`) de NaN olur; bar atılmaz.
- fut_v1 önbelleğine (`futures/{SYM}_oi5m…`) yazılmaz. Yalnız ilk metrics günü `oi5m.json`'dan okunur; yoksa ikili arama.
- İndirme ve durdurma kuralları fut_v1 ile aynıdır: art arda 50 hata ya da isteklerin %20'sinden fazlası hata →
  laboratuvar ÇALIŞMAZ, inen veri önbellekte kalır. Sembolün günlerinin %5'inden fazlası hata → `PARTIAL_ERROR`.

## Özellikler (`tradingbot/crowd_features.py`, lab ile ileride canlı kayıtçının ortak kodu)

Gösterim fut_v1 ile aynıdır. Bar i'nin karar anı `T = ts[i] + step`'tir (bar kapanışı). `k` = 24 saat (4h 6 bar, 1h 24),
`W` = 20 gün (4h 120 bar, 1h 480).

Nedensellik ve eksik veri:
- Yalnız kapanmış barlar kullanılır.
- Metrics: `T − 15 dk ≤ t ≤ T − 5 dk` aralığındaki SON satır; bütün sütunlar aynı satırdan. Değer sonlu ve > 0 değilse NaN;
  daha eski satıra düşülmez.
- Fonlama: yalnız `calc ≤ T − 60 sn` olan uzlaşma görünür (fut_v1 `align_funding`).
- Bütün z pencereleri i'nin kendisini HARİÇ tutar ve yalnız pencere içeriğine bağlıdır (önek değişmezliği testle bağlı).
- Eksik girdi → NaN. Kategorik alan → UNKNOWN (bağlam kovasında `bilinmiyor`). NaN hiçbir zaman 0'a çevrilmez, ileri taşınmaz.
  NaN hiçbir kuralı, kontrolü ya da plaseboyu tetiklemez.

`zdm(x, W, taban)[i] = (x_i − μ) / max(σ, taban)`; μ ve σ (ddof 0) `x[i−W .. i−1]`'in sonlu değerlerinden, en az `W/2` değer
gerekir, `x_i` sonlu olmalıdır.

| Alan | Tanım |
|---|---|
| `dshare` | `delta / v`, `delta = 2·tb − v` (v = 0 → NaN) |
| `dshare_z` | `zdm(dshare, W, 0,01)` |
| `cvd_share` | son k barın `Σ delta / Σ v` (penceredeki bir `tb` NaN ya da `Σv ≤ 0` → NaN) |
| `cvd_share_z` | `zdm(cvd_share, W, 0,01)` |
| `cvd_slope` | son k barın birikimli delta doğrusunun EKK eğimi / ortalama hacim (bilgi) |
| `divergence` | son 21 bar: yeni kapanış zirvesi ama CVD önceki zirvesini geçmedi → BEAR_DIV; ayna → BULL_DIV (bilgi) |
| `absorption` | hacim ≥ 1,5 × önceki 20 bar, `h − l ≤ 0,6·atr14s`, `|dshare| ≥ 0,10` ve kapanış ters yönde (bilgi) |
| `exhaustion_t` | son 10 barda ≥ 3 kapanış zirvesi, son üçünde `dshare` kesin azalan → UP_EXHAUST; ayna (bilgi) |
| `oi`, `doi_k`, `oi_z`, `oi_pct`, `dpx_k`, `px_z` | fut_v1 `bar_features` aynen; `doi_1 = ln(oi_i / oi_{i−1})` |
| `oi_quad` | `|oi_z| < 0,5` ya da `dpx_k = 0` → FLAT; LONG_BUILD / SHORT_BUILD / SHORT_COVER / LONG_UNWIND |
| `gls_z`, `tpls_z`, `tals_z`, `smart_retail_z` | `zdm(ln x, W, 0,02)`; `smart_retail = ln tpls − ln gls` |
| `f8`, `f8_hi`, `f8_lo`, `funding_bucket` | fut_v1 tanımları; kova NEG (< 0) / BASE (≤ 1 bp) / HIGH |
| `f8_z` | `(f8 − ort) / max(sd, 0,000025)`, fut_v1'in 20 günlük diğer uzlaşmalar penceresi (≥ 30 değer) |
| `pos_ok`, `crowd_z`, `pos_side` | üç z de sonlu; `crowd_z = ort(clip±3 gls_z, clip±3 tpls_z, clip±3 f8_z)`; ±1 eşik |
| `flow_ok`, `flow_side` | `oi_z` ve `cvd_share_z` sonlu; yukarıdaki akış kuralı |
| `crowd_state` | konum önce: LONG_CROWDED / SHORT_CROWDED; konum NONE ise LONGS_ENTERING / SHORTS_ENTERING / CALM |

Canlı REST ihtiyacı (testle bağlı, ≤ 28 gün): 1h 505 bar, 4h 127 bar; metrics `(W+k)·step + 15 dk` ≈ 21 gün; fonlama 20 gün + 9 saat.

## Ön kayıtlı 8 hipotez

Doğrulayıcı aile tam 8'dir: **4h, bağlam HEPSİ, 4 kural × 2 yön**. 1h yalnız bilgidir. Bütün kurallarda: `i ≥ 210`,
laboratuvar ATR'si (EWM 14) sonlu ve > 0, karar i kapanışında, giriş i+1 açılışında, maliyet taraf başına %0,05 + 3 bps.

Tetikler kalabalığa göre nötrdür (trend ya da hacim süzgeci yoktur):
- **BRK** (Donchian ilk kapanış kırılımı): `c[i] > hi20[i]` ve `c[i−1] ≤ hi20[i−1]` → `d = +1`; `c[i] < lo20[i]` ve
  `c[i−1] ≥ lo20[i−1]` → `d = −1`. `hi20`/`lo20` önceki 20 barın en yüksek/en düşüğüdür.
- **SWEEP** (fut_v1 #10 geometrisi): `h > hi20`, `c < hi20`, üst fitil ≥ bar aralığının %50'si → `d = +1`; `l < lo20`,
  `c > lo20`, alt fitil ≥ %50 → `d = −1`.

| Kural | Koşul (bilinmeli) | Yön | Stop | Çıkış |
|---|---|---|---|---|
| `CROWD_BRK_FLOW_FOLLOW` | `flow_ok` ve `flow_side == d` (kalabalık kırılım yönünde giriyor) | d | `c ∓ 2 ATR` | 10 bar karşı kanal, en çok 300 bar |
| `CROWD_BRK_FLOW_CONTRA` | aynı | −d | kırılım barının ucu ± 0,25 ATR | kovalama referansı `c[i]`; 2R hedef, 24 bar |
| `CROWD_SWEEP_POS_CONTRA` | `pos_ok` ve `pos_side == d` (kalabalık süpürme yönünde konumlu = tuzakta) | −d | fitil ucu ± 0,25 ATR | kovalama `c[i]`; 2R, 24 bar |
| `CROWD_SWEEP_POS_FOLLOW` | aynı | d | karşı bar ucu ∓ 0,25 ATR | kovalama `c[i]`; 2R, 24 bar |

- Her kural × {LONG, SHORT} = 8 hipotez. LONG/SHORT, işlemin yönüdür.
- Çıkış ayarları mühürdedir: `simulate` en çok 24 bar, hedef 2R, risk 0,1–5 ATR, giriş tetikten 1 ATR'den uzaksa atlanır
  (CHASE); `simulate_rule` (BRK_FOLLOW) risk 0,1–10 ATR.
- Yönler önceden sabittir. Bir kural KAYBETTİRİR çıkarsa ters yön ayrı bir ön kayıttır.

## Kontroller

- Kontrol (`CTRL_<kural>`, aile `control`) aynı fiyat tetiği, yön, stop ve çıkışı kullanır; kalabalık koşulu BİLİNEN ve
  YANLIŞ olan barlarda açılır. Kural ∪ kontrol = tetik ∩ OK; ikisi ayrıktır (testle bağlı).
- Kontrol hipotez değildir. Aday oranına, `tested`'a ve dilim özetine girmez.

## Plasebolar

- Her kuralın kendi eşi vardır (`PLACEBO_<kural>`). Kalabalık grubu yalnız kendi eşiyle karşılaştırılır; `PLACEBO_RANDOM`'a
  düşmez. Seçim yalnız barın zaman damgası ve veri maskesine bağlıdır; gerçek sinyale ve seri uzunluğuna bağlı değildir.
- `CROWD_BRK_FLOW_FOLLOW` eşi (fut_v1 #1 gibi): barların %1'i (`crc32` karması), `flow_ok` ve geçerli ATR; yön karmadan
  (`u < 0,005` → LONG); stop 2 ATR, kanal çıkışı.
- Üç `simulate()` kuralının eşi (fut_v1 #10 gibi): barların %2'si, kuralın OK maskesi (BRK_CONTRA `flow_ok`, SWEEP_* `pos_ok`);
  yön ikinci bir karmadan (sha256); risk `q`, aynı yöndeki ÖNCEKİ bir gerçek olaydan (kural ∪ kontrol) seçilir; öncesinde
  olay yoksa `PLACEBO_…:NO_REAL_RISK` sayılır. Tetik `c[j]`.
- Plasebo kalabalık koşuluyla eşleştirilmez. Koşulun katkısını kontrol ölçer.

## Hüküm, sıkı hüküm, katkı ve defter adaylığı

- Hükümler `signal_lab.aggregate` / `verdict` / `verdict_strict` ile DEĞİŞMEDEN verilir. GÜÇLÜ ADAY: iki dönemde de %95
  aralık > 0 ve eşini iki dönemde de geçiyor. Sıkı: ayrıca doğrulama döneminde eşine göre farkın gün kümeli aralığı > 0.
- Keşif/doğrulama kesimi (2/3) dilim başına yalnız kalabalık koşusunun olaylarından alınır (`--futures crowd`: katalog, ek
  sinyal ve algoritma yok).

Katkı (kural − kontrol), `Δ_P = ort.R(kural, P) − ort.R(kontrol, P)`, gün kümeli bağımsız bootstrap (`diff_ci`):

| Etiket | Şart |
|---|---|
| KESİN | Δ_IS > 0, Δ_OOS > 0 ve OOS farkının %95 aralığının alt ucu > 0 |
| VAR | Δ_IS > 0 ve Δ_OOS > 0 |
| YOK | yukarıdakilerin hiçbiri |
| ÖLÇÜLEMEDİ | bir dönemde taraflardan birinde n < 20, ya da OOS aralığı yok |

Defter adaylığı (yalnız 4h HEPSİ):
- **Standart:** hüküm GÜÇLÜ ADAY ve katkı VAR ya da KESİN.
- **Sıkı:** sıkı hüküm GÜÇLÜ ADAY ve katkı KESİN.
- Hüküm GÜÇLÜ ama katkı YOK → "kenar fiyat tetiğinden; kalabalık koşulu gerekçesiz".
- 1h satırları hiçbir zaman aday yapmaz.
- Sıkı aday yalnız bir PAPER gözlem ÖNERİSİdir (C4'ün CV001–8'i gözlemlemesi gibi) ve kullanıcı onayı ister. Canlı akış
  raporu (flow_v1, sonraki adım) aynı koşulu gerçek ve "olsaydı" kayıtlarında ön kayıtlı tekrar olarak izler.

## "Takip mi karşı mı" satırı

Her tetik × yön için TAKİP ve KARŞI kuralları yan yana yazılır (`pairs`, günlükte `CROWD_PAIR`):
- yukarı kırılım, kalabalık long giriyor: TAKİP = BRK_FLOW_FOLLOW LONG, KARŞI = BRK_FLOW_CONTRA SHORT;
- aşağı kırılım, kalabalık short giriyor: TAKİP SHORT, KARŞI LONG;
- tepe süpürmesi, kalabalık long konumlu: TAKİP = SWEEP_POS_FOLLOW LONG, KARŞI = SWEEP_POS_CONTRA SHORT;
- dip süpürmesi, kalabalık short konumlu: TAKİP SHORT, KARŞI LONG.

Cevap: sıkı aday olan taraf → "TAKİP (sıkı)" ya da "KARŞI (sıkı)"; yoksa standart aday olan taraf → "(standart)"; iki taraf da
aday → "ikisi de (çelişki)"; hiçbiri → "kanıt yok". 1h satırı "bilgi"dir.

## Fonlama taşıma (yalnız bilgi)

Kalabalık ailelerinin her işlemine fut_v1 tanımıyla `funding_r` yazılır (`futures_lab.funding_carry(names=…)`). Hüküm R'sine
girmez. Penceredeki bir uzlaşma bilinmiyorsa NaN'dır; rapor ortalamayı ve bilinmeyen payını yazar.

## Kör sayım (VERİ AZ, tasarım gereği)

Yoklama R hesaplamaz; yalnız ad, yön ve yaklaşık dönem başına olay sayar. 4h kuralında öngörülen IS < 30 ya da OOS < 20 →
`VERİ AZ (tasarım gereği)`. Yerine kural, eşik, dilim ya da sembol listesi DEĞİŞMEZ. Akış yönü iki z-puanının birlikte ±1'i
geçmesini ister; kırılım kuralları bu yüzden ince çıkabilir. Bu önceden bilinir ve kabul edilir.

## Bağlam dilimleri (KEŞİF, hipotez değil)

`--futures crowd-ctx` mevcut algoritma ve ek sinyal olaylarına (ve `--futures crowd`'da kalabalık olaylarına) dört kova ekler:
- `kalabalik_poz` / `kalabalik_akis`: `pos_side` / `flow_side` işlem yönüne göre `kalabalıkla` / `kalabalığa_karşı` / `kalabalık_yok`;
- `oi_ceyrek`: `oi_quad` etiketi;
- `taker_24s`: `cvd_share_z·s ≥ 1` kalabalıkla, `≤ −1` kalabalığa karşı, arası kalabalık yok.

NaN → `bilinmiyor`. Bu dilimler hipotez değildir; birini kullanmak yeni bir ön kayıt ve yeni deneme demektir.

## Sabitler (mühürde)

| Sabit | Değer |
|---|---|
| Pencereler | `WINDOW_DAYS = 20` (W), k = 24 saat, `DIV_LOOKBACK = 20`, `ATR_S_N = 14`, `ABS_VOL_N = 20` |
| z tabanları | pay 0,01; ln oran 0,02; fonlama 0,000025 (0,25 bp); kırpma ±3 |
| Eşikler | `CROWD_Z = 1`, `FLOW_OI_Z = 1`, `FLOW_CVD_Z = 1`, `TAKER_REL_Z = 1`, `OI_QUAD_DEAD = 0,5` |
| Emilim / tükenme | 1,5 / 0,6 / 0,10; 10 / 20 / 3 üye |
| Hizalama | metrics `T−15 dk … T−5 dk`; fonlama `calc ≤ T − 60 sn`; en az 30 uzlaşma |
| Veri | `TB_TOL = 1e-9`, `VOL_AGREE_RTOL = 1e-9`, ısınma 22 gün |
| Kurallar | `START_BAR = 210`, BRK stop 2 ATR, CONTRA tampon 0,25 ATR, fitil %50, süpürme tampon 0,25 ATR |
| Plasebo | %1 (LONG < 0,005) ve %2 (yön < 0,5) |
| Çıkış / maliyet | 24 bar, 2R, kovalama 1 ATR, risk 0,1–5 ATR (kural çıkışında 0,1–10), %0,05 + 3 bps |
| İstatistik | kesim 2/3, `min_is = 30`, `min_oos = 20`, bootstrap 1000, sıkı tohum 20260926, sıkı en az 5 gün |

## Koşu sırası

Argümanlar `research/signal_lab_args.txt`'ten okunur (`.github/workflows/signal-lab.yml`, `--source archive --jobs 4` iş
akışı ekler). Her adım ayrı commit'tir. Başka bir koşu sürüyorsa beklenir. Bakımcı yalnız iş günlüğünü okur: `CROWD_*`
satırları ayrıca "Print crowd lines" adımında basılır.

1. **Yoklama:** `--futures crowd-probe --symbols genis --tfs 4h,1h --days 4h=1460,1h=730`
   - P1 kapsama; P2 sütun başına ilk dolu gün ve yıl başına boş payı; P3 taker şeması ve taker/hacim uyumu; P4 eksik anahtar
     404 mü 403 mü; P5 checksum örneklemi; P6 5 dk ızgarası ve yinelenen satır çatışmaları; P7 hız ve disk; P8 kör sayım ve
     `pos_side` / `flow_side` payları. Günlükte `CROWD_COV`, `CROWD_PROBE`, `CROWD_COUNT`, `CROWD_SHARE` satırları.
   - İlk koşu ≈ 45 bin metrics gün dosyasını yeniden indirir (8 iş parçacığı, ≈ 20–30 dk; önbelleğe ≈ 300 MB).
   - Ayrıştırıcıyla çelişki çıkarsa yalnız `crowd_data.py`'nin ayrıştırması değişir. Bir sütunun ANLAMI (`COLUMN_MAP`)
     değişirse mühür değişir; bu belgeye gerekçesiyle yazılır. Yoklama R hesaplamadığı için bu, sonuç görülmeden yapılır.
2. **Doğrulayıcı koşu:** `--futures crowd --symbols genis --tfs 4h,1h --days 4h=1460,1h=730`
   - Katalog, ek sinyal ve algoritma yoktur. Günlükte her satır için `CROWD_RESULT` ve her tetik × yön için `CROWD_PAIR`.
3. **Keşif:** `--futures crowd-ctx --no-catalog --symbols genis --tfs 4h --days 4h=1460`
   - Donchian, TSMOM, RSI2, BB ve ek sinyaller dört kalabalık kovasına göre dilimlenir.
4. Sonuç: bu belgeye fut_v1'deki gibi bir tablo ve kullanıcıya 3–5 satırlık düz Türkçe cevap ("kırılımda kalabalık girerken
   takip / karşı: …; süpürmede kalabalık tuzaktayken karşı / takip: …"). Sıkı aday yoksa "kanıt yok" yazılır, hiçbir şey değişmez.

Komut satırı kuralları: kalabalık kipleri yalnız `--source archive` (ya da `--offline`) ve yalnız 1h/4h dilimleriyle koşar;
`--variations` / `--only-variations` ile birlikte koşmaz (çıkış kodu 2). `crowd` = katalog, ek sinyal ve algoritma yok.
`crowd-ctx` algoritmaları tutar; `--only-futures` ile birlikte koşmaz. `--futures off` (varsayılan) ve fut_v1 kipleri
(`probe`, `ctx`, `rules`) bayt bayt aynıdır; varsayılan yol `crowd_*` modüllerini hiç içe aktarmaz (testlerle bağlı).

## Sınırlar

- Metrics satırının zaman damgası pencere başı mı sonu mu bilinmiyor. `T−15 … T−5 dk` kuralı iki durumda da güvenlidir.
  Canlı REST ile arşiv arasındaki eşleme VPS eşlik yoklamasıyla (her sütunda ≥ %99 birebir) ayrıca doğrulanacaktır.
- Oran sütunlarının arşivdeki başlangıç günü OI'den farklı olabilir; P2 bunu ölçer. Eksik günler NaN kalır.
- İçinde bulunulan ayın fonlaması yoktur (yalnız bitmiş ay dosyaları). Son ayın barlarında `f8_z`, dolayısıyla `pos_side`
  bilinmez; süpürme kuralları o barlarda ateşlemez.
- OI hizalaması fut_v1 ile aynıdır; tek fark, OI hücresi ≤ 0 olan satır burada atılmadığı için o anda OI NaN olur (fut_v1
  bir önceki satıra düşebilirdi). Bu nadirdir ve güvenli taraftadır.
- Liste bugünün hayatta kalan coinleridir. Kontrol ve plasebo farkları mutlak R'den daha az etkilenir.
- Kalabalık uçları coinler arasında ve günler boyunca kümelenir. Gün kümeli aralık, sıkı kural ve KESİN katkı bunun savunmasıdır.
- 4h konum/akış durumu yavaş değişir; 1h yalnız bilgidir.

## Sonuç: fut_v2 (2026-09-30)

- **Yoklama** ([run 36642581545](https://github.com/CoskunerBerke/trading2/actions/runs/36642581545)): ayrıştırıcıyla çelişki yok; `COLUMN_MAP`, kural ve sabit değişmedi.
  - Metrics: 30/30 coin OK, hata 0, toplam 20 eksik gün; 5 dk ızgara payı 1,0; pencerede yinelenen satır çatışması 0.
  - Büyük trader pozisyon oranı (`tpls`) 2022-12-14'ten başlıyor (ARB, SUI, ICP listelendikleri günden). OI ve tüm hesap
    oranı pencerenin başından var. 2023 sonrası boş hücre payı ≤ %0,3.
  - Taker mumları: 789.115 bar; bulunma %100, hacim uyuşmazlığı 0, geçersiz 0; taker payı ≈ 0,49.
  - Eksik anahtar 404 (403 değil); checksum örneklemi 120/120; 47.821 dosya, hata 0, önbellek 358 MB.
  - 4h `pos_side`: LONG %10,9, SHORT %10,0, NONE %79,1 (UNKNOWN %7,5). `flow_side`: LONG %3,9, SHORT %1,9.
  - Kör sayım: 4h ailesinde her hipotezde IS ≥ 312 ve OOS ≥ 238; VERİ AZ yok.
- **Doğrulayıcı koşu** ([run 36646091499](https://github.com/CoskunerBerke/trading2/actions/runs/36646091499)): **8 hipotezin hiçbiri aday değil; dört "takip mi karşı mı" satırının dördü de "kanıt yok".**

| 4h kural | Yön | n (keşif/doğr.) | Keşif ort.R | Doğrulama ort.R | Eşine göre (doğr.) | Kontrole göre (keşif / doğr.) | Katkı | Hüküm / sıkı |
|---|---|---|---|---|---|---|---|---|
| BRK_FLOW_FOLLOW | LONG | 1030 / 477 | +0,28 | +0,27 | +0,29 | −0,16 / +0,22 | YOK | ZAYIF İZ / ZAYIF İZ |
| BRK_FLOW_FOLLOW | SHORT | 322 / 301 | +0,08 | +0,11 | +0,05 | +0,17 / +0,16 | VAR | ZAYIF İZ / ZAYIF İZ |
| BRK_FLOW_CONTRA | LONG | 322 / 301 | −0,31 | −0,19 | +0,08 | −0,11 / +0,04 | YOK | KANIT YOK / KANIT YOK |
| BRK_FLOW_CONTRA | SHORT | 1030 / 474 | −0,21 | −0,23 | −0,08 | +0,01 / +0,03 | VAR | KAYBETTİRİR / KAYBETTİRİR |
| SWEEP_POS_CONTRA | LONG | 312 / 392 | +0,15 | −0,20 | −0,03 | +0,25 / −0,01 | YOK | KANIT YOK / KANIT YOK |
| SWEEP_POS_CONTRA | SHORT | 436 / 238 | −0,24 | −0,10 | −0,09 | −0,12 / −0,05 | YOK | KANIT YOK / KANIT YOK |
| SWEEP_POS_FOLLOW | LONG | 436 / 238 | −0,17 | +0,00 | +0,23 | +0,06 / +0,22 | VAR | KANIT YOK / KANIT YOK |
| SWEEP_POS_FOLLOW | SHORT | 312 / 392 | −0,11 | −0,10 | +0,06 | +0,06 / +0,03 | VAR | KANIT YOK / KANIT YOK |

- **Kırılımda kalabalık girerken:** kalabalıkla birlikte (takip) girmek iki dönemde de artıda (LONG +0,28 / +0,27R), ama
  doğrulama aralığı sıfırı kapsıyor ve katkı YOK: aynı kırılım kalabalık girmezken (kontrol) keşif döneminde daha iyi gitti.
  Kazancın kaynağı kalabalık değil, kırılımın kendisi. Kalabalığa karşı (kırılımı söndürmek) SHORT tarafında iki dönemde de
  kaybettirdi (−0,21 / −0,23R), ama kontrole göre fark ~0: kırılıma karşı girmek zaten kaybettiriyor, kalabalık bunu
  değiştirmiyor.
- **Süpürmede kalabalık tuzaktayken:** ne karşı ne takip; hiçbir satır iki dönemde de sıfırdan ayrışmadı.
- **1h (yalnız bilgi, aday olamaz):** kırılım takip LONG +0,41 / +0,41R, katkı VAR ama ZAYIF İZ. Kırılım karşı ve süpürme
  takip satırları KAYBETTİRİR.
- Fonlama taşıma (bilgi): kırılım takip LONG'da işlem başı ≈ −0,02R; hükme girmedi.

Bu kurallarla kalabalık verisi (taker akışı, OI, long/short oranları, fonlama) 4h'de gösterilebilir bir ek avantaj
vermedi. Hiçbir defter değişmedi. Ana botun piyasa ajanındaki "kalabalık çok long ise long'a karşı" eğilimi de bu koşuyla
desteklenmedi ya da çürütülmedi (süpürme konum satırları kanıt yok); dokunulmadı.

Bir satırı (örneğin 1h kırılım takip LONG) test etmek yeni bir ön kayıt (fut_v3) ve yeni deneme demektir. Deneme sayısı
şimdi 2 vadeli aile, 16 hipotez; aday 0.
