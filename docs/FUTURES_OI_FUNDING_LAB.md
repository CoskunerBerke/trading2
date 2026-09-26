# Açık pozisyon (OI) ve fonlama laboratuvarı — fut_v1 (2026-09-26)

Sinyal laboratuvarına vadeli piyasa verisi eklenir: açık pozisyon (OI) ve fonlama oranı. Salt araştırmadır. Hiçbir
defter, strateji ya da parametre değişmez. Kâr garantisi değildir; geçmiş testtir.

**Ön kayıt mührü: `FUT_REGISTRY_SHA = 6f08eca51d8b97ee`** (`tradingbot/futures_lab.py`, `FUT_VERSION = "fut_v1"`).
Mühür bir testle sabittir (`tests/test_futures_lab.py`). Bu belge doğrulayıcı koşudan (2. adım) ÖNCE commit edilir.
Bir sabit ya da kural metni değişirse mühür değişir: bu fut_v2 demektir, belge güncellenir ve deneme sayısı artar.
Sayımlar ya da sonuçlar görüldükten sonra kural gevşetilmez.

## Ne sınanıyor

Soru: OI ve fonlama bilgisi, fiyat kuralına bir şey katıyor mu? Yoksa kenar yalnız fiyattan mı geliyor?

- Veri yalnız data.binance.vision arşivinden gelir. REST yedeği yoktur.
  - OI: `daily/metrics` gün dosyaları, 5 dakikalık satırlar, `sum_open_interest` (baz birim).
  - Fonlama: `monthly/fundingRate` ay dosyaları, `last_funding_rate`. İçinde bulunulan ay kullanılmaz.
- Bütün özellikler nedenseldir. Bar i'nin karar anı `T = ts[i] + step`'tir.
  - OI: `T − 15 dk ≤ t ≤ T − 5 dk` aralığındaki son satır. Satır yoksa NaN.
  - Fonlama: yalnız `calc ≤ T − 60 sn` olan uzlaşma görünür. Aralık (1/2/4/8 saat) ardışık farktan çıkarılır.
  - Eksik ya da bayat veri NaN olur. NaN hiçbir kuralı, kontrolü ya da plaseboyu tetiklemez. İleri taşıma yoktur.
- Pencereler: `WINDOW_DAYS = 20`. `k` = 24 saat (4h 6 bar, 1h 24, 1d 1). `W` = 20 gün (4h 120 bar, 1h 480, 1d 20).
- Özellikler: `doi = ln(oi[i]/oi[i−k])`, `dpx = ln(c[i]/c[i−k])`, `oi_z`, `px_z` (önceki W değere göre z),
  `oi_pct` (önceki W OI değerinden kesin küçük olanların payı), `f8 = oran × 8 / aralık`, `f8_hi` / `f8_lo` (son 20 günün
  diğer uzlaşmalarına göre kesin sıra; en az 30 değer). `OI_OK` ve `FUND_OK` bu değerlerin sonlu olmasıdır.

## Ön kayıtlı 8 hipotez

Doğrulayıcı aile tam 8'dir: **4h, bağlam HEPSİ, 4 kural × 2 yön**. 1h ve 1d yalnız bilgidir. Bütün kurallarda:
`i ≥ 210`, ATR sonlu ve > 0, karar i kapanışında, giriş i+1 açılışında.

1. **OI_BREAKOUT_20 LONG / SHORT** (kullanıcı fikri #1)
   - Tetik LONG: `c[i] > hi20[i]` ve `c[i−1] ≤ hi20[i−1]`; `c > ema50 > ema200`; `v > 1,5 × vol_avg`; `OI_OK`.
   - Tetik SHORT: ayna (`lo20`, `c < ema50 < ema200`).
   - Kural: tetik ve `doi > 0` (24 saatte OI artıyor). Kontrol: tetik ve `doi ≤ 0`.
   - Stop `c ∓ 2 ATR`. Çıkış: 10 bar karşı kanal, en çok 300 bar (TREND_DONCHIAN_20_10 ile aynı).
2. **OI_REGIME_CONT LONG / SHORT** (fikir #9, devam)
   - Tetik: yukarı kesişim `px_z[i] ≥ 1` ve `px_z[i−1] < 1`; aşağı kesişim `px_z[i] ≤ −1` ve `px_z[i−1] > −1`; `OI_OK`.
   - Aynı yönde son kabul edilen kesişimden sonraki k bar içindeki kesişim yok sayılır. Bu, kural/kontrol ayrımından önce yapılır.
   - Kural LONG: yukarı kesişim ve `oi_z ≥ 1`. Kural SHORT: aşağı kesişim ve `oi_z ≥ 1`. Kontrol: `oi_z < 1`.
   - Stop 2 ATR. Çıkış: k bar tut (24 saat).
3. **OI_REGIME_REVERT LONG / SHORT** (fikir #9, dönüş)
   - Aynı kesişim akışı.
   - Kural LONG: aşağı kesişim ve `oi_z ≤ −1` (long tasfiyesi → sıçrama). Kural SHORT: yukarı kesişim ve `oi_z ≤ −1`
     (short kapanışı → sönme). Kontrol: `oi_z > −1`.
   - Stop 2 ATR. Çıkış: k bar tut.
4. **FUNDING_SWEEP_REVERSAL LONG / SHORT** (fikir #10)
   - SHORT süpürme: `h > hi20`, `c < hi20`, üst fitil ≥ bar aralığının %50'si; `OI_OK` ve `FUND_OK`.
   - LONG süpürme: `l < lo20`, `c > lo20`, alt fitil ≥ %50.
   - Kalabalık SHORT: `f8 > 1 bp`, `f8_hi ≥ 0,90`, `oi_pct ≥ 0,80`. Kalabalık LONG: `f8 < 0`, `f8_lo ≥ 0,90`, `oi_pct ≥ 0,80`.
   - Kural: süpürme ve kalabalık. Kontrol: süpürme ve kalabalık yanlış.
   - Stop SHORT `h + 0,25 ATR`, LONG `l − 0,25 ATR`. Giriş kovalama kuralıyla (`trigger = c[i]`). Çıkış: 2R hedef, 24 bar.
   - 0,90 sayımdan önce seçildi. `VERİ AZ` çıkarsa sonuç budur; kural gevşetilmez.

Yönler önceden sabittir. Bir kural KAYBETTİRİR çıkarsa ters yön ayrı bir ön kayıttır (fut_v2).

Çıkış ve maliyet ayarları da mühürdedir (`exit_cfg`, `LabConfig` varsayılanlarından):
- #10 (`simulate`): en çok 24 bar, hedef 2R, risk 0,1–5 ATR aralığında; giriş tetikten (`c[i]`) 1 ATR'den uzaksa atlanır (CHASE).
- #1 ve #9 (`simulate_rule`): risk 0,1–10 ATR aralığında.
- Maliyet her R'de: taraf başına %0,05 ücret + 3 bps kayma.

## Kontroller

- Kontrol (`CTRL_<kural>`, aile `control`) aynı fiyat tetiği, yön, stop ve çıkışı kullanır.
- Yalnız OI/fonlama koşulunun BİLİNEN ve YANLIŞ olduğu barlarda açılır. Kural ∪ kontrol = tetik kümesi (tam tümleyen).
- Kontrol hipotez değildir. Aday oranına, `tested`'a ve dilim özetine girmez. Genel listelerde görünmez.

## Plasebolar

- Her kuralın kendi eşi vardır (`PLACEBO_<kural>`). Vadeli grup yalnız kendi eşiyle karşılaştırılır; `PLACEBO_RANDOM`'a düşmez.
- OI_BREAKOUT_20, OI_REGIME_CONT, OI_REGIME_REVERT: barların %1'i (`crc32` karması, zaman damgasından), `OI_OK` ve geçerli ATR. Yön karmadan. Stop 2 ATR, kuralın çıkışı.
- #10: barların %2'si, `OI_OK` ve `FUND_OK`. Yön ve risk ikinci bir karmadan (sha256). Risk `q`, aynı yöndeki ÖNCEKİ bir
  süpürme olayından (kural ∪ kontrol) alınır. Öncesinde olay yoksa `PLACEBO_…:NO_REAL_RISK` sayılır.
- Plasebo OI/fonlama koşuluyla eşleştirilmez. Koşul hipotezin kendisidir; katkısını kontrol ölçer.

## Katkı etiketleri (kural − kontrol)

`Δ_P = ort.R(kural, P) − ort.R(kontrol, P)`, P = keşif (IS) ve doğrulama (OOS). Aralık: gün kümeli bağımsız bootstrap
(`signal_lab.diff_ci`, sıkı hükümle aynı).

| Etiket | Şart |
|---|---|
| KESİN | Δ_IS > 0, Δ_OOS > 0 ve OOS farkının %95 aralığının alt ucu > 0 |
| VAR | Δ_IS > 0 ve Δ_OOS > 0 |
| YOK | yukarıdakilerin hiçbiri |
| ÖLÇÜLEMEDİ | bir dönemde taraflardan birinde n < 20, ya da OOS aralığı yok |

Plaseboya göre fark ve OOS aralığı da her 4h/1h/1d HEPSİ satırında yazılır. Bu aralık sıkı hükmü değiştirmez.

## Defter adaylığı (yalnız 4h HEPSİ)

- **Standart defter adayı:** hüküm GÜÇLÜ ADAY ve katkı VAR ya da KESİN.
- **Sıkı defter adayı:** sıkı hüküm GÜÇLÜ ADAY ve katkı KESİN.
- Hüküm GÜÇLÜ ama katkı YOK ise rapor şunu yazar: "kenar fiyat kuralından; OI/fonlama filtresi gerekçesiz". Fiyat-yalnız
  kural ayrı bir ön kayıt ister.
- 1h ve 1d satırları hiçbir zaman aday yapmaz.
- Aday olmak yalnız laboratuvar sonucudur. Canlı PAPER defteri bu adımın konusu değildir.

## Fonlama taşıma (yalnız bilgi)

- Vadeli ailelerin her işlemine `funding_r` yazılır. Hüküm R'sine girmez.
- Giriş açılışından (hariç) çıkışa (dahil) kadarki uzlaşmalar: `funding_r = −s × Σ oran × fiyat / risk`.
- Penceredeki bir uzlaşma bilinmiyorsa NaN'dır. Rapor ortalamayı ve bilinmeyen payını yazar.

## Bağlam dilimleri (KEŞİF)

`--futures ctx` her olaya üç kova ekler: `oi_rejim` (fiyat↑/↓ × OI↑/↓, yalnız işaret), `oi_seviye` (≤%20, orta, ≥%80),
`fonlama` (<0, taban 0–1 bp, >1 bp). NaN → `bilinmiyor`. Bu dilimler hipotez değildir.

## Koşu sırası

Argümanlar `research/signal_lab_args.txt`'ten okunur; her adım ayrı commit'tir. Başka bir koşu sürüyorsa beklenir.

1. Yoklama: `--futures probe --symbols genis --tfs 4h,1h,1d --days 4h=1460,1h=730,1d=1825`
   - Kapsama (P1), şema (P2, P3), eksik anahtar 404/403 (P4), checksum örneklemi (P5), günlük fonlama (P6), hız (P7),
     kör sayım (P8, R yok). Günlükte `FUT_COV`, `FUT_PROBE`, `FUT_COUNT` satırları.
   - 4h kuralında öngörülen IS < 30 ya da OOS < 20 → `VERİ AZ (tasarım gereği)`. Yerine kural, eşik ya da dilim değişmez.
   - P2/P3 ayrıştırıcıyla çelişirse yalnız `futures_data.py`'nin ayrıştırması değişir; mühür değişmez.
2. Bu belge commit edilir. Doğrulayıcı koşu: `--only-futures --symbols genis --tfs 4h,1h,1d --days 4h=1460,1h=730,1d=1825`
   - Günlükte her satır için `FUT_RESULT`.
3. Keşif: `--no-catalog --futures ctx --symbols genis --tfs 4h --days 4h=1460`
   - Donchian, TSMOM, RSI2, BB ve ek sinyaller üç vadeli kovaya göre dilimlenir. XSMOM olaylarında vadeli bağlam yoktur.

Komut satırı kuralları: `--futures` yalnız `--source archive` ile (ya da `--offline`), yalnız 1h/4h/1d dilimleriyle
koşar. `--variations` / `--only-variations` ile birlikte koşmaz (çıkış kodu 2). `--only-futures` = `--futures rules` +
katalog, ek sinyal ve algoritma yok.

## Sınırlar

- OI geçmişi 2021 sonu civarında başlar. 4h doğrulama dönemi yaklaşık son 16 aydır. 1d sonuçları incedir.
- İçinde bulunulan ayın fonlaması yoktur. Fonlama kuralı son ayı (ya da daha azını) kaybeder.
- `create_time` pencere başı mı sonu mu bilinmiyor. 5–15 dakikalık aralık iki durumda da güvenlidir.
- Liste bugünün hayatta kalan coinleridir. Plasebo ve kontrol karşılaştırması etkilenmez; mutlak R etkilenir.
- Fonlama uçları coinler ve haftalar arasında kümelenir. Gün kümeli aralık #10 için iyimser olabilir. Sıkı kural ve KESİN
  bunun savunmasıdır.
- 4 saatlik düzende tek eksik uzlaşma 8 saatlik aralık gibi görünür (çıkarım farktan). Bu özelliği ve taşımayı etkileyebilir.
- `--futures off` (varsayılan) bugünkü laboratuvarla bayt bayt aynıdır. Testlerle bağlıdır (`tests/test_signal_lab.py`).
