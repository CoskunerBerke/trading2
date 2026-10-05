# Defter araştırması — book_v1 sonuçları (2026-10-05)

Ön kayıt: `docs/BOOK_RESEARCH_V1.md` (`3e42da4`, 2026-10-05 06:06 UTC). Değişiklik 1: aynı belgenin §16'sı (`f4b49ba`, 12:42 UTC,
hiçbir sonuç görülmeden). Kod `f4b49bafedc21dfe4b345e693e3e921063d05faf`, kod ağacı `ea36b877ac6fbc97` (çalışma ağacı temiz), mühür
`BOOK_REGISTRY_SHA = 37dd3f2854c890ad`. Koşu 2026-10-05 12:47:59–13:38:30 UTC (deneme `dcf1fda577a1`); kapasite bölümü aynı
kodla, filtre tablosuyla 13:38:54–13:40:55 UTC'de (deneme `09f9fb74c0fa`, `--report-only --filters`). Bulutta, yalnız
`data.binance.vision` arşivinden; fiyat verisi depoya yüklenmedi.

Geçmiş testtir; PAPER ya da canlı sonuç değildir. Kâr garantisi değildir. Hiçbir defter, strateji, config ya da çalışma zamanı
değişmedi. Kurallar sonuçtan sonra değiştirilmedi.

Tablolardaki sayılar koşunun raporundan (`book_lab_report.json`) aynen alındı; tablolarda ondalık ayırıcı noktadır. Metinde
aynı sayılar virgülle yazılır. K = keşif (karar anı 2023-01-01 ≤ t < 2025-01-01), D = doğrulama (2025-01-01 ≤ t < 2026-09-30).
Aylık % işlem başına %0,5 riskle, defterin başlangıç bakiyesine göredir (bileşik yok).

## 0. Değişiklik 1 (2026-10-05, hiçbir sonuç görülmeden)

Ön kaydın §16'sında tam metni var. Özet:

- **Ne değişti:** yalnız iki sabit birer gün geri alındı. `PRICE_END` 2026-10-01 00:00 → **2026-09-30 00:00 UTC**,
  `FUNDING_END` 2026-10-02 00:00 → **2026-10-01 00:00 UTC**. Doğrulama artık 2025-01-01 ≤ t < 2026-09-30'dur. Son 4h bar
  2026-09-29 20:00, son 1d bar 2026-09-29'dur. Fonlama dosyaları 2022-12 … 2026-09'dur.
- **Neden:** ön kaydın fonlama kapsamı kuralı (§4.1, §5.2, okunuş 3 ve 37) `PRICE_END`'den sonra en az bir fonlama uzlaşması
  ister; veri sonunda kapanan (`DATA_END`) işlemin fonlaması buna dayanır. Eski `PRICE_END` ile bu uzlaşmalar yalnız 2026-10
  aylık `fundingRate` dosyasında olurdu. O dosya ay bitince, 2026-11 başında yayımlanır; arşivde günlük fonlama klasörü yoktur.
  İlk deneme (`13a1209454e5`) bu kuralla ilk coinde durdu (§7). Sahip sonucu 2026-11'i beklemeden istedi.
- **Neden en küçük değişiklik:** kural korunur (`FUNDING_END = PRICE_END + 1 gün`, son fonlama dosyası `PRICE_END`'in ayı,
  `PRICE_END` sonrası en az bir uzlaşma). `PRICE_END` gece yarısına hizalı kalmalıdır; 2026-09 dosyasında sonrası uzlaşma
  bırakan en geç gece yarısı 2026-09-30 00:00'dır. Bu koşuda 23 coinin hepsinde `PRICE_END` sonrası 3 uzlaşma var (2026-09-30
  00:00, 08:00, 16:00).
- **Değişmeyenler:** varyantlar, süzgeçler, plasebo, istatistik, çoklu test, kapasite, aylık ölçü, evrenler, PIT kuralı,
  tohumlar, eşikler, sürüm adı (`book_v1`) ve deneme sayısı. Ay listeleri aynı: doğrulama 2025-01 … 2026-09 (21 ay), keşif
  2023-01 … 2024-12 (24 ay). Tek etki: 2026-09 doğrulama ayı 30 değil 29 karar günü içerir; veri sonunda açık kalan işlemler
  2026-09-30 00:00'da `DATA_END` ile kapanır ve 2026-09'a yazılır.
- **Ne zaman:** değişiklik 12:42 UTC'de commit edildi. O ana kadar hiçbir sinyal sayısı, R, kazanma oranı, aylık % ya da
  plasebo sonucu hesaplanmamıştı (ilk deneme veri adımında durmuştu). Bu belgedeki bütün sonuçlar 12:47'de başlayan koşudandır.

## 1. Sonuç

**Hiçbir hücre hedefi karşılamadı. Öneri adayı yok.** Koşunun hükmü (aynen): "Hiçbir hücre ÖNERİ ADAYI değil: bu defterler
ve önceden yazılmış varyantları maliyet sonrası, kapasite içinde +%1/ay vermedi. Elendikleri basamaklar — HEDEFİ KARŞILAMIYOR:
54".

- 54 birincil hücrenin standart hükmü (fonlamasız): 4 GÜÇLÜ ADAY, 10 ZAYIF İZ, 40 KANIT YOK. KAYBETTİRİR ve VERİ AZ yok.
- Sıkı hüküm: 2 GÜÇLÜ ADAY, fonlamasız ve fonlamalı ikisinde de: `FM_00_BASE|LONG` ve `FM_05_COST_STOP|LONG`. İkisi de
  Formasyon'dur ve neredeyse aynı işlem kümesidir (§4).
- **Hedefin asıl engeli aylık belirsizlik.** §8.3'ün 3. şartı (kapasiteli, fonlamalı doğrulama aylık ortalamasının %95 aralığının
  alt ucu > 0, `BUMP` ve `NO_BUMP` ikisinde de) hiçbir hücrede sağlanmadı. Tek yakın durum `FM_09_REGIME_BOTH|BOTH`: `NO_BUMP`
  aralığı [+0,0910, +3,1274], ama `BUMP` aralığı [−0,4020, +7,6489] ve sıkı hükmü ZAYIF İZ.
- Çoklu test (Holm, 54 hücre): reddedilen hücre yok. En küçük p `FM_01_BTC_UP|LONG` 0,0031; ilk Holm eşiği 0,05 ÷ 54 ≈ 0,000926.
- Aday oranı (fonlamasız): gerçek hücreler %7,41, plasebo grupları %0. Kural gereği "TESADÜFLE AÇIKLANABİLİR" işareti yok; hedefi
  karşılayan hücre olmadığı için bu basamak zaten uygulanmadı.
- Sansür: 4 hücre (hepsi D4 1d LONG) "SANSÜR YÜKSEK" (§2). BAD_BAR payı ve gerçek işlemlerde NaN fonlama payı her hücrede 0.

| Defter | Hüküm (sıkı, fonlamasız) | Taban: D ort.R [%95] · plaseboya göre D fark [%95] | Taban: kapasiteli D aylık % fonlamalı, BUMP / NO_BUMP [%95] | Öne çıkan varyant | Hedef |
|---|---|---|---|---|---|
| D4 (13 varyant, 15 hücre) | 14 KANIT YOK, 1 ZAYIF İZ | `D4_00`: +0.0269 [-0.1856, +0.2454] · +0.0507 [-0.1596, +0.2986] | -1.2637 [-9.4878, +7.5803] / +0.8974 [-6.8027, +9.3701] | `D4_04_VOL_CONFIRM`: D +0.2035 R, ZAYIF İZ; kapasiteli +1.3937 / +2.9649, aralıklar 0'ı içeriyor | 0 / 15 |
| C4 (9 varyant, 27 hücre) | 25 KANIT YOK, 2 ZAYIF İZ | `C4_00\|BOTH`: -0.0120 [-0.1148, +0.0945] · +0.0273 [-0.0750, +0.1415] | +0.4175 [-5.5893, +6.5815] / +0.1702 [-4.1096, +4.4765] | `C4_04_WIDE_STOP\|LONG`: D +0.0998 R, ZAYIF İZ | 0 / 27 |
| Formasyon (10 varyant + kontrol, 12 hücre) | 2 GÜÇLÜ ADAY, 9 ZAYIF İZ, 1 KANIT YOK | `FM_00`: +0.2451 [+0.0551, +0.4165] · +0.2632 [+0.0351, +0.4659] | +2.7285 [-0.8561, +6.7558] / +1.0132 [-0.4943, +2.7321] | `FM_05_COST_STOP`: tabanın neredeyse aynısı; sıkı GÜÇLÜ ADAY, hedefte 2. ve 3. şart düştü | 0 / 12 |

**Dürüst sonuç:**

- D4 ve C4 için "ayda +%1 net" sorusunun cevabı **hayır**dır. İki defterin de doğrulama ortalaması plaseboya göre ayırt
  edilemiyor; hiçbir önceden yazılmış varyant bunu değiştirmedi. D4'ün keşifteki güçlü sonucu (taban +0,5221 R) doğrulamada
  sıfıra indi (+0,0269 R); aynı dönemde rastgele giriş de keşifte artıydı (+0,2182 R), yani yükselen piyasanın payı vardı.
- Formasyon tabanı (üç beyaz asker + RSI > 70) R ölçüsünde gerçek bir iz gösteriyor: doğrulamada +0,2451 R, plaseboyu
  +0,2632 R geçiyor ve fark aralığı sıfırın üstünde. Ama defterin gerçek sınırları içinde (100 USDT, 30 slot, min-notional)
  aylık getiri belirsiz: `BUMP` +%2,7285/ay [−0,8561, +6,7558], `NO_BUMP` +%1,0132/ay [−0,4943, +2,7321]. 21 aylık doğrulamada
  aralık sıfırı içeriyor, yani hedef kanıtlanmadı. Üstelik bu kural 1.412 hücreden seçildi, doğrulama dönemi seçim koşusunun
  doğrulamasıyla örtüşüyor (ön kayıt §3) ve taban hücre tek başına öneri dayanağı olamaz (§8.5). Çoklu testte de geçmedi.
- Defterler değişmez. Kapatma ya da değiştirme kararı sahibindir (ön kayıt §10).

## 2. D4 — Donchian trend takibi (13 varyant, 15 hücre)

**Taban (`D4_00_BASE`):** keşifte +0,5221 R (n 3.565), doğrulamada +0,0269 R [−0,1856, +0,2454] (n 2.988), kazanma %28,5.
Eşleştirilmiş plasebo keşifte +0,2182 R, doğrulamada −0,0238 R. Fark doğrulamada +0,0507 [−0,1596, +0,2986]. Hüküm dört
sürümde de KANIT YOK. Kapasiteli, fonlamalı doğrulama aylık %: `BUMP` −1,2637 [−9,4878, +7,5803], `NO_BUMP` +0,8974
[−6,8027, +9,3701]. Keşifte aynı ölçü +12,4027 / +10,4777 idi.

**Varyantlar tabana göre:**

- Hiçbir varyant doğrulamada plaseboyu aralığı sıfırın üstünde olacak şekilde geçmedi. Doğrulama ort.R −0,1128 ile +0,2035
  arasında; keşif ort.R +0,3553 ile +1,2065 arasında (SHORT hariç). Keşifteki güç doğrulamaya taşınmadı.
- En iyi: `D4_04_VOL_CONFIRM` (hacim teyitli kırılım): doğrulama +0,2035 R [−0,1029, +0,4931], plaseboya göre +0,0901
  [−0,2623, +0,4238]; ZAYIF İZ (dört sürüm). Kapasiteli fonlamalı doğrulama `BUMP` +1,3937 [−6,2183, +9,8787], `NO_BUMP`
  +2,9649 [−3,8809, +10,7880]. 1. (sıkı hüküm) ve 3. (aralık) şartlardan düştü.
- `D4_05_STOP3ATR` (canlıdaki 13/13 kaybın gerekçesi): doğrulama +0,0045 R; geniş stop yardım etmedi.
- 1d varyantları (`D4_07`, `D4_08`, `D4_09`, `D4_10|LONG`): doğrulama ort.R eksi (−0,0552, −0,1128, −0,1069, −0,1069) ve
  "SANSÜR YÜKSEK" (doğrulama işlemlerinin %10,8–%18,2'si `DATA_END` ile kapandı; eşik %10). Yavaş çıkış veri sonunda açık
  pozisyon bırakıyor.
- `D4_10_1D_REGIME_BOTH|SHORT`: keşif −0,8067 R, doğrulama +0,0221 R; KANIT YOK. Rejime bağlı short da avantaj göstermedi.
- Kapasitesiz (sade) aylık ölçü D4'te çok oynak: binlerce örtüşen işlem w = 1 ile toplanıyor (taban: fonlamalı +0,9566
  [−19,7650, +26,9128]). Bilgi amaçlıdır; hüküm kapasiteli ölçüyle verilir.
- Laboratuvarın özgün eşiyle karşılaştırma (bilgi, §7.3): `D4_00` − `PLACEBO_TREND_DONCHIAN_20_10` farkı keşifte +0,4007,
  doğrulamada +0,1969 [−0,0305, +0,4525]; hüküm KANIT YOK.
- D4'te en küçük Holm p 0,3180.

Sütunlar: n gerçek ve n plasebo keşif/doğrulama; ort.R ve aralıklar gün kümeli %95; "fark" = gerçek ort.R − eşleştirilmiş
plasebo ort.R (fonlamasız); hüküm standart / sıkı; Holm p dört basamak. Kapasiteli ölçüde "D" doğrulama, "K" keşif aylık
ortalamasıdır; aralık ay kümeli %95. "düşen şartlar" §8.3 numaralarıyla: 1 sıkı hüküm, 2 aylık ≥ +%1, 3 aralık alt ucu > 0, 4
keşif > 0, 7 sansür/veri. Bilgi tablosunda "plasebo çekilen" risk geometrisinden önce seçilen plasebo sayısıdır; "örtüşmesiz
ort.R" sembol başına tek pozisyon yaklaşımıdır (bilgi).

#### D4 — hüküm tablosu

| Hücre | n gerçek K/D | n plasebo K/D | ort.R K | ort.R D [%95] | kazanma D | plasebo ort.R K/D | fark K/D | D fark %95 | hüküm fonlamasız (std / sıkı) | hüküm fonlamalı (std / sıkı) | Holm p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `D4_00_BASE\|LONG` | 3565/2988 | 3523/2976 | +0.5221 | +0.0269 [-0.1856, +0.2454] | %28.5 | +0.2182/-0.0238 | +0.3039/+0.0507 | [-0.1596, +0.2986] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3475 |
| `D4_01_BTC_UP\|LONG` | 3072/1706 | 3091/1737 | +0.4174 | +0.0207 [-0.2297, +0.3183] | %29.9 | +0.3030/+0.0128 | +0.1144/+0.0079 | [-0.2718, +0.3131] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.4994 |
| `D4_02_COIN_UP\|LONG` | 1964/1081 | 1939/1109 | +0.3896 | -0.0435 [-0.2256, +0.1757] | %29.4 | +0.2440/-0.0605 | +0.1456/+0.0170 | [-0.2030, +0.2722] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.4485 |
| `D4_03_VOL_OK\|LONG` | 2699/2643 | 2640/2590 | +0.4160 | +0.0348 [-0.1869, +0.2767] | %28.4 | +0.2215/-0.0104 | +0.1945/+0.0452 | [-0.2024, +0.3112] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3789 |
| `D4_04_VOL_CONFIRM\|LONG` | 2074/1607 | 2025/1535 | +0.7066 | +0.2035 [-0.1029, +0.4931] | %31.7 | +0.3054/+0.1134 | +0.4012/+0.0901 | [-0.2623, +0.4238] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.3180 |
| `D4_05_STOP3ATR\|LONG` | 3565/2988 | 3528/2997 | +0.4047 | +0.0045 [-0.1430, +0.1584] | %31.3 | +0.2278/-0.0007 | +0.1769/+0.0052 | [-0.1454, +0.1702] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.4837 |
| `D4_06_EXIT_LO20\|LONG` | 3565/2988 | 3582/2951 | +0.9484 | -0.0341 [-0.2613, +0.2178] | %24.0 | +0.3294/+0.0534 | +0.6190/-0.0875 | [-0.3333, +0.1928] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.7327 |
| `D4_07_1D\|LONG` | 685/408 | 643/405 | +0.8276 | -0.0552 [-0.2713, +0.1799] | %25.7 | +0.4671/+0.0633 | +0.3605/-0.1185 | [-0.4032, +0.1714] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.7785 |
| `D4_08_1D_55_20\|LONG` | 424/209 | 401/224 | +1.2065 | -0.1128 [-0.3923, +0.2108] | %23.0 | +1.2167/+0.1696 | -0.0102/-0.2824 | [-0.7183, +0.1604] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.8976 |
| `D4_09_1D_BTC_UP\|LONG` | 632/250 | 647/267 | +0.6856 | -0.1069 [-0.3555, +0.1776] | %26.0 | +0.3825/-0.0894 | +0.3031/-0.0175 | [-0.3606, +0.3124] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5399 |
| `D4_10_1D_REGIME_BOTH\|BOTH` | 726/598 | 716/596 | +0.4924 | -0.0319 [-0.2226, +0.1620] | %33.4 | +0.2243/-0.0468 | +0.2681/+0.0149 | [-0.2079, +0.2428] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.4557 |
| `D4_10_1D_REGIME_BOTH\|LONG` | 632/250 | 623/239 | +0.6856 | -0.1069 [-0.3555, +0.1776] | %26.0 | +0.3579/-0.1605 | +0.3277/+0.0536 | [-0.2860, +0.3937] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3808 |
| `D4_10_1D_REGIME_BOTH\|SHORT` | 94/348 | 93/357 | -0.8067 | +0.0221 [-0.2507, +0.3192] | %38.8 | -0.6706/+0.0294 | -0.1361/-0.0073 | [-0.3033, +0.3246] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5156 |
| `D4_11_BTC_COIN_UP\|LONG` | 1875/950 | 1835/972 | +0.3553 | -0.0396 [-0.2515, +0.1841] | %29.3 | +0.0803/-0.0262 | +0.2750/-0.0134 | [-0.2779, +0.2298] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5448 |
| `D4_12_COST_STOP\|LONG` | 3481/2877 | 3475/2926 | +0.4909 | -0.0132 [-0.2116, +0.2155] | %27.8 | +0.2409/-0.0348 | +0.2500/+0.0216 | [-0.2265, +0.2808] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.4448 |

#### D4 — kapasitesiz (sade) aylık ölçü

| Hücre | D fonlamasız [%95] | D fonlamalı [%95] | K fonlamasız | K fonlamalı | ≥ +%1 ay payı D (fonlamalı) | işlemsiz ay D | en kötü ay D (fonlamalı) |
|---|---|---|---|---|---|---|---|
| `D4_00_BASE\|LONG` | +1.9156 [-19.1415, +28.3724] | +0.9566 [-19.7650, +26.9128] | +38.7743 | +36.2802 | %28.6 | 0 | 2025-12 -45.1747 |
| `D4_01_BTC_UP\|LONG` | +0.8386 [-11.9812, +17.8980] | +0.1991 [-12.4976, +16.9055] | +26.7133 | +24.4468 | %14.3 | 7 | 2025-06 -43.4819 |
| `D4_02_COIN_UP\|LONG` | -1.1197 [-7.1421, +7.0472] | -1.5705 [-7.5262, +6.4687] | +15.9416 | +14.3503 | %14.3 | 0 | 2025-01 -21.2407 |
| `D4_03_VOL_OK\|LONG` | +2.1908 [-18.2660, +27.2716] | +1.3124 [-18.8798, +26.1006] | +23.3928 | +21.4349 | %28.6 | 0 | 2025-12 -44.8599 |
| `D4_04_VOL_CONFIRM\|LONG` | +7.7921 [-8.5951, +28.6580] | +7.2498 [-8.8801, +27.7821] | +30.5269 | +28.9926 | %23.8 | 0 | 2026-04 -27.9614 |
| `D4_05_STOP3ATR\|LONG` | +0.3349 [-14.8594, +19.0166] | -0.3786 [-15.3096, +17.9887] | +30.0412 | +28.0418 | %28.6 | 0 | 2025-12 -36.7588 |
| `D4_06_EXIT_LO20\|LONG` | -2.4466 [-26.6366, +26.1460] | -3.7902 [-27.4238, +24.2295] | +70.4581 | +65.7210 | %23.8 | 0 | 2025-06 -61.2091 |
| `D4_07_1D\|LONG` | +2.6713 [-3.2217, +10.3020] | +2.2124 [-3.3888, +9.4816] | +9.0046 | +7.5456 | %28.6 | 2 | 2026-01 -15.1712 |
| `D4_08_1D_55_20\|LONG` | +3.6894 [-1.2991, +9.6076] | +3.2141 [-1.4810, +8.7780] | +6.9377 | +4.6460 | %28.6 | 3 | 2025-05 -9.1898 |
| `D4_09_1D_BTC_UP\|LONG` | +2.5709 [-1.8633, +8.8748] | +2.1579 [-2.0145, +8.0339] | +6.2213 | +4.9264 | %23.8 | 9 | 2025-05 -11.9241 |
| `D4_10_1D_REGIME_BOTH\|BOTH` | +2.7536 [-2.8026, +9.7039] | +2.2460 [-3.0398, +8.8299] | +4.6415 | +3.3350 | %42.9 | 0 | 2025-04 -12.2434 |
| `D4_10_1D_REGIME_BOTH\|LONG` | +2.5709 [-1.8633, +8.8748] | +2.1579 [-2.0145, +8.0339] | +6.2213 | +4.9264 | %23.8 | 9 | 2025-05 -11.9241 |
| `D4_10_1D_REGIME_BOTH\|SHORT` | +0.1827 [-2.5119, +3.3185] | +0.0881 [-2.5546, +3.1742] | -1.5798 | -1.5914 | %19.1 | 9 | 2025-04 -12.2434 |
| `D4_11_BTC_COIN_UP\|LONG` | -0.8959 [-6.5250, +7.1155] | -1.3026 [-6.9102, +6.5600] | +13.8783 | +12.2608 | %9.5 | 8 | 2025-01 -21.2407 |
| `D4_12_COST_STOP\|LONG` | -0.9045 [-19.6458, +22.1017] | -1.7875 [-20.2248, +20.8024] | +35.6011 | +33.2363 | %28.6 | 0 | 2025-06 -42.8862 |

#### D4 — kapasiteli aylık ölçü ve hedef (§8.2, §8.3)

| Hücre | BUMP D fonlamasız | BUMP D fonlamalı [%95] | BUMP K fonlamalı | NO_BUMP D fonlamasız | NO_BUMP D fonlamalı [%95] | NO_BUMP K fonlamalı | kabul/aday BUMP · NO_BUMP | çıkarılan (BUMP) | NaN fonlama D (BUMP/NO_BUMP) | sansür K/D | hedef (§8.3) | düşen şartlar | §8.5 durumu |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `D4_00_BASE\|LONG` | -0.9085 | -1.2637 [-9.4878, +7.5803] | +12.4027 | +1.1956 | +0.8974 [-6.8027, +9.3701] | +10.4777 | 2778/6553 · 2521/6553 | 349 | %0.0/%0.0 | %0.0/%0.1 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_01_BTC_UP\|LONG` | +1.1499 | +0.8852 [-4.4897, +6.6901] | +7.9793 | +2.2435 | +2.0319 [-2.9529, +7.6402] | +6.3916 | 2027/4778 · 1825/4778 | 271 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_02_COIN_UP\|LONG` | -0.8119 | -0.9986 [-3.3493, +1.9739] | +6.4137 | -0.0078 | -0.1252 [-2.0675, +2.3813] | +4.6063 | 1314/3045 · 1087/3045 | 244 | %0.0/%0.0 | %0.0/%0.4 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_03_VOL_OK\|LONG` | -1.0870 | -1.4387 [-9.5313, +7.3591] | +11.0286 | +0.6947 | +0.3995 [-7.0420, +8.6050] | +9.6919 | 2508/5342 · 2287/5342 | 285 | %0.0/%0.0 | %0.0/%0.1 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_04_VOL_CONFIRM\|LONG` | +1.6814 | +1.3937 [-6.2183, +9.8787] | +10.1318 | +3.1969 | +2.9649 [-3.8809, +10.7880] | +8.5263 | 2088/3681 · 1848/3681 | 273 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_05_STOP3ATR\|LONG` | -0.3017 | -0.5986 [-7.3915, +6.8140] | +10.1871 | +1.8206 | +1.6172 [-3.9172, +7.8690] | +8.8665 | 2569/6553 · 2095/6553 | 548 | %0.0/%0.0 | %0.0/%0.1 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_06_EXIT_LO20\|LONG` | -1.7621 | -2.1932 [-11.6442, +8.3513] | +18.7172 | -0.2817 | -0.6429 [-9.4787, +9.2567] | +16.7030 | 2322/6553 · 2150/6553 | 285 | %0.0/%0.0 | %0.0/%0.9 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_07_1D\|LONG` | +2.0258 | +1.7096 [-2.5799, +7.0271] | +4.8416 | +1.3373 | +1.2163 [-1.4109, +4.5941] | +3.7675 | 459/1093 · 325/1093 | 146 | %0.0/%0.0 | %0.0/%10.8 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP, 7:SANSÜR YÜKSEK | HEDEFİ KARŞILAMIYOR |
| `D4_08_1D_55_20\|LONG` | +3.2669 | +2.9740 [-0.3515, +6.9825] | +2.8997 | +2.2668 | +2.1598 [-0.1433, +4.9123] | +2.6504 | 241/633 · 166/633 | 76 | %0.0/%0.0 | %0.0/%18.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP, 7:SANSÜR YÜKSEK | HEDEFİ KARŞILAMIYOR |
| `D4_09_1D_BTC_UP\|LONG` | +2.2102 | +1.9309 [-1.3696, +6.2412] | +2.8080 | +1.2050 | +1.1027 [-0.8381, +3.6634] | +2.8619 | 380/882 · 267/882 | 124 | %0.0/%0.0 | %0.0/%16.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP, 7:SANSÜR YÜKSEK | HEDEFİ KARŞILAMIYOR |
| `D4_10_1D_REGIME_BOTH\|BOTH` | +2.9766 | +2.6605 [-1.3839, +7.3091] | +0.9982 | +1.5905 | +1.4530 [-0.7347, +4.1009] | +2.0637 | 590/1324 · 395/1324 | 216 | %0.0/%0.0 | %0.0/%6.7 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_10_1D_REGIME_BOTH\|LONG` | +2.2069 | +1.9284 [-1.3648, +6.2278] | +2.8339 | +1.2050 | +1.1027 [-0.8381, +3.6634] | +2.8619 | 380/882 · 267/882 | 123 | %0.0/%0.0 | %0.0/%16.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP, 7:SANSÜR YÜKSEK | HEDEFİ KARŞILAMIYOR |
| `D4_10_1D_REGIME_BOTH\|SHORT` | +0.7697 | +0.7321 [-1.3014, +2.7847] | -1.8357 | +0.3856 | +0.3502 [-0.5385, +1.3447] | -0.7982 | 210/442 · 128/442 | 93 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_11_BTC_COIN_UP\|LONG` | -0.5972 | -0.7789 [-3.1020, +2.1966] | +5.6691 | +0.0849 | -0.0263 [-1.9210, +2.4589] | +3.8619 | 1239/2825 · 1017/2825 | 239 | %0.0/%0.0 | %0.0/%0.4 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `D4_12_COST_STOP\|LONG` | -1.3070 | -1.6642 [-9.7427, +7.0056] | +12.0994 | +1.0267 | +0.7287 [-6.9445, +9.1700] | +10.3727 | 2739/6358 · 2484/6358 | 348 | %0.0/%0.0 | %0.0/%0.1 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |

#### D4 — plasebo, çıkış ve maliyet (bilgi)

| Hücre | ham sinyal | temiz pencere dışı | plasebo çekilen | NO_REAL_RISK payı | BAD_BAR payı | çıkışlar D | ort. tutma D (bar) | maliyet R D | örtüşmesiz ort.R K/D |
|---|---|---|---|---|---|---|---|---|---|
| `D4_00_BASE\|LONG` | 7516 | 93 | 6499 | %0.0 | %0.0 | RULE 1602, STOP 1382, DATA_END 4 | 19.46 | 0.0434 | +0.5038/+0.0575 (n 1483/1395) |
| `D4_01_BTC_UP\|LONG` | 4808 | 93 | 4828 | %0.0 | %0.0 | RULE 907, STOP 795, DATA_END 4 | 19.63 | 0.0402 | +0.3548/+0.1163 (n 1345/757) |
| `D4_02_COIN_UP\|LONG` | 3080 | 93 | 3048 | %0.0 | %0.0 | RULE 591, STOP 486, DATA_END 4 | 20.5 | 0.0482 | +0.4329/+0.0057 (n 844/480) |
| `D4_03_VOL_OK\|LONG` | 6200 | 93 | 5230 | %0.0 | %0.0 | RULE 1427, STOP 1214, DATA_END 2 | 19.33 | 0.0449 | +0.5502/+0.0531 (n 1274/1304) |
| `D4_04_VOL_CONFIRM\|LONG` | 4237 | 93 | 3560 | %0.0 | %0.0 | RULE 870, STOP 733, DATA_END 4 | 20.05 | 0.0429 | +0.5591/+0.1820 (n 1126/993) |
| `D4_05_STOP3ATR\|LONG` | 7516 | 93 | 6525 | %0.0 | %0.0 | RULE 2370, STOP 614, DATA_END 4 | 22.83 | 0.0289 | +0.3949/+0.0412 (n 1376/1307) |
| `D4_06_EXIT_LO20\|LONG` | 7516 | 93 | 6533 | %0.0 | %0.0 | STOP 1865, RULE 1097, DATA_END 26 | 28.07 | 0.0433 | +0.9420/+0.0178 (n 1205/1224) |
| `D4_07_1D\|LONG` | 1528 | 43 | 1048 | %0.0 | %0.0 | STOP 184, RULE 180, DATA_END 44 | 17.23 | 0.0182 | +1.1598/-0.0140 (n 273/215) |
| `D4_08_1D_55_20\|LONG` | 902 | 11 | 625 | %0.0 | %0.0 | STOP 124, RULE 47, DATA_END 38 | 18.58 | 0.0197 | +1.7019/+0.1350 (n 157/99) |
| `D4_09_1D_BTC_UP\|LONG` | 1221 | 43 | 914 | %0.0 | %0.0 | STOP 113, RULE 97, DATA_END 40 | 16.11 | 0.0162 | +0.8728/-0.0302 (n 271/137) |
| `D4_10_1D_REGIME_BOTH\|BOTH` | 1964 | 99 | 1312 | %0.0 | %0.0 | RULE 350, STOP 208, DATA_END 40 | 20.73 | 0.0136 | +0.5023/+0.0898 (n 348/278) |
| `D4_10_1D_REGIME_BOTH\|LONG` | 1221 | 43 | 862 | %0.0 | %0.0 | STOP 113, RULE 97, DATA_END 40 | 16.11 | 0.0162 | +0.8728/-0.0302 (n 271/137) |
| `D4_10_1D_REGIME_BOTH\|SHORT` | 743 | 56 | 450 | %0.0 | %0.0 | RULE 253, STOP 95 | 24.05 | 0.0117 | -0.8032/+0.2070 (n 82/142) |
| `D4_11_BTC_COIN_UP\|LONG` | 2835 | 93 | 2807 | %0.0 | %0.0 | RULE 508, STOP 438, DATA_END 4 | 20.23 | 0.0444 | +0.3882/+0.0054 (n 825/424) |
| `D4_12_COST_STOP\|LONG` | 7516 | 93 | 6563 | %0.0 | %0.0 | RULE 1523, STOP 1350, DATA_END 4 | 19.1 | 0.0398 | +0.4736/+0.0226 (n 1462/1369) |

## 3. C4 — 4h mum varyasyonları (9 varyant, 27 hücre)

**Taban (`C4_00_BASE|BOTH`):** keşifte +0,0068 R (n 2.591), doğrulamada −0,0120 R [−0,1148, +0,0945] (n 2.630), kazanma %38,7.
Plasebo keşifte −0,0554, doğrulamada −0,0393. Fark doğrulamada +0,0273 [−0,0750, +0,1415]. KANIT YOK (dört sürüm).
Kapasiteli fonlamalı doğrulama aylık %: `BUMP` +0,4175 [−5,5893, +6,5815], `NO_BUMP` +0,1702 [−4,1096, +4,4765]; keşifte
`NO_BUMP` −0,1537 (4. şart da düştü). LONG ve SHORT tabanları da KANIT YOK.

**Varyantlar tabana göre:**

- 27 hücrenin doğrulama ort.R'si −0,1004 ile +0,0998 arasında. GÜÇLÜ ADAY yok.
- ZAYIF İZ yalnız `C4_04_WIDE_STOP` (stop formasyon ucundan 1 ATR): LONG hücre doğrulamada +0,0998 R [−0,0258, +0,2417],
  plaseboya göre +0,1646 [+0,0147, +0,2980], dört sürümde ZAYIF İZ; BOTH hücre fonlamasız ZAYIF İZ, fonlamalı KANIT YOK. Keşif
  aralığı sıfırı içerdiği için GÜÇLÜ ADAY olamadı. Holm p 0,0138 (LONG).
- `C4_02_EXIT_1R` (hızlı hedef): kazanma %50,4'e çıktı ama doğrulama −0,0334 R. İsabet oranı maliyeti ve kaybı karşılamadı.
- `C4_03_EXIT_3R|BOTH`: kapasiteli aylık % en yüksek C4 hücresi (`BUMP` +3,2448 [−3,7100, +10,6648], `NO_BUMP` +2,6599
  [−2,8800, +8,5566]); ama R ölçüsünde KANIT YOK (doğrulama +0,0487 [−0,0846, +0,1877]).
- `C4_01_REGIME` ve `C4_08_REGIME_COST`: SHORT tarafı keşifte −0,4608 / −0,4734 R; rejim süzgeci yardım etmedi.
- `C4_07_TREND_ONLY` (veri gözetlemeli): doğrulama +0,0043 R, KANIT YOK.
- C4 plasebosu candle_lab'ın %10 oranıyla seçildiği için büyüktür (dönem başına binlerce). `NO_REAL_RISK` payı %7,4–%28,6
  (`C4_06_VOL_OK` en yüksek); kalan plasebo n her hücrede 2.000'in üstünde.

Sütunlar: n gerçek ve n plasebo keşif/doğrulama; ort.R ve aralıklar gün kümeli %95; "fark" = gerçek ort.R − eşleştirilmiş
plasebo ort.R (fonlamasız); hüküm standart / sıkı; Holm p dört basamak. Kapasiteli ölçüde "D" doğrulama, "K" keşif aylık
ortalamasıdır; aralık ay kümeli %95. "düşen şartlar" §8.3 numaralarıyla: 1 sıkı hüküm, 2 aylık ≥ +%1, 3 aralık alt ucu > 0, 4
keşif > 0, 7 sansür/veri. Bilgi tablosunda "plasebo çekilen" risk geometrisinden önce seçilen plasebo sayısıdır; "örtüşmesiz
ort.R" sembol başına tek pozisyon yaklaşımıdır (bilgi).

#### C4 — hüküm tablosu

| Hücre | n gerçek K/D | n plasebo K/D | ort.R K | ort.R D [%95] | kazanma D | plasebo ort.R K/D | fark K/D | D fark %95 | hüküm fonlamasız (std / sıkı) | hüküm fonlamalı (std / sıkı) | Holm p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `C4_00_BASE\|BOTH` | 2591/2630 | 28510/29022 | +0.0068 | -0.0120 [-0.1148, +0.0945] | %38.7 | -0.0554/-0.0393 | +0.0622/+0.0273 | [-0.0750, +0.1415] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3102 |
| `C4_00_BASE\|LONG` | 1459/1023 | 13977/13514 | -0.0231 | +0.0130 [-0.1169, +0.1595] | %38.2 | -0.0454/-0.0867 | +0.0223/+0.0997 | [-0.0521, +0.2382] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.1030 |
| `C4_00_BASE\|SHORT` | 1132/1607 | 14533/15508 | +0.0453 | -0.0279 [-0.1677, +0.1203] | %39.0 | -0.0651/+0.0020 | +0.1104/-0.0299 | [-0.2013, +0.1426] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.6341 |
| `C4_01_REGIME\|BOTH` | 1586/1721 | 14853/15526 | -0.1333 | -0.0318 [-0.1614, +0.1029] | %38.5 | -0.1406/-0.0238 | +0.0073/-0.0080 | [-0.1433, +0.1360] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5423 |
| `C4_01_REGIME\|LONG` | 1347/673 | 12358/6958 | -0.0751 | +0.0679 [-0.1179, +0.2502] | %39.5 | -0.0900/-0.0614 | +0.0149/+0.1293 | [-0.0888, +0.3268] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.1178 |
| `C4_01_REGIME\|SHORT` | 239/1048 | 2495/8568 | -0.4608 | -0.0959 [-0.2478, +0.0831] | %37.9 | -0.3916/+0.0067 | -0.0692/-0.1026 | [-0.2922, +0.0877] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.8386 |
| `C4_02_EXIT_1R\|BOTH` | 2591/2630 | 28510/29022 | -0.0203 | -0.0334 [-0.1111, +0.0417] | %50.4 | -0.0516/-0.0567 | +0.0313/+0.0233 | [-0.0530, +0.0999] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.2736 |
| `C4_02_EXIT_1R\|LONG` | 1459/1023 | 13977/13514 | -0.0532 | -0.0235 [-0.1144, +0.0786] | %50.6 | -0.0558/-0.0856 | +0.0026/+0.0621 | [-0.0453, +0.1622] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.1314 |
| `C4_02_EXIT_1R\|SHORT` | 1132/1607 | 14533/15508 | +0.0221 | -0.0396 [-0.1488, +0.0709] | %50.2 | -0.0476/-0.0315 | +0.0697/-0.0081 | [-0.1309, +0.1191] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5482 |
| `C4_03_EXIT_3R\|BOTH` | 2591/2630 | 28510/29022 | +0.0591 | +0.0487 [-0.0846, +0.1877] | %31.1 | -0.0553/-0.0335 | +0.1144/+0.0822 | [-0.0702, +0.2349] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.1342 |
| `C4_03_EXIT_3R\|LONG` | 1459/1023 | 13977/13514 | +0.0362 | +0.0630 [-0.1012, +0.2643] | %30.8 | -0.0098/-0.1022 | +0.0460/+0.1652 | [-0.0260, +0.3555] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.0547 |
| `C4_03_EXIT_3R\|SHORT` | 1132/1607 | 14533/15508 | +0.0887 | +0.0396 [-0.1497, +0.2370] | %31.2 | -0.0992/+0.0262 | +0.1879/+0.0134 | [-0.2128, +0.2582] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.4555 |
| `C4_04_WIDE_STOP\|BOTH` | 2578/2626 | 28468/28585 | +0.0501 | +0.0492 [-0.0445, +0.1445] | %43.6 | -0.0289/-0.0145 | +0.0790/+0.0637 | [-0.0337, +0.1688] | ZAYIF İZ / ZAYIF İZ | KANIT YOK / KANIT YOK | 0.1054 |
| `C4_04_WIDE_STOP\|LONG` | 1453/1021 | 13970/13083 | +0.0506 | +0.0998 [-0.0258, +0.2417] | %43.6 | +0.0100/-0.0648 | +0.0406/+0.1646 | [+0.0147, +0.2980] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0138 |
| `C4_04_WIDE_STOP\|SHORT` | 1125/1605 | 14498/15502 | +0.0496 | +0.0170 [-0.1202, +0.1626] | %43.6 | -0.0663/+0.0281 | +0.1159/-0.0111 | [-0.1716, +0.1506] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5544 |
| `C4_05_COST_STOP\|BOTH` | 2380/2446 | 26587/26908 | +0.0025 | -0.0094 [-0.1186, +0.0991] | %38.7 | -0.0481/-0.0321 | +0.0506/+0.0227 | [-0.0896, +0.1387] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3451 |
| `C4_05_COST_STOP\|LONG` | 1354/916 | 13204/12600 | -0.0339 | +0.0139 [-0.1301, +0.1564] | %38.0 | -0.0360/-0.0910 | +0.0021/+0.1049 | [-0.0485, +0.2659] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.0975 |
| `C4_05_COST_STOP\|SHORT` | 1026/1530 | 13383/14308 | +0.0505 | -0.0233 [-0.1673, +0.1370] | %39.1 | -0.0601/+0.0197 | +0.1106/-0.0430 | [-0.2134, +0.1299] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.6804 |
| `C4_06_VOL_OK\|BOTH` | 1878/2080 | 20710/22908 | +0.0074 | -0.0111 [-0.1213, +0.1043] | %39.1 | -0.0605/-0.0370 | +0.0679/+0.0259 | [-0.0881, +0.1359] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3228 |
| `C4_06_VOL_OK\|LONG` | 1005/806 | 9849/10408 | -0.0582 | +0.0186 [-0.1438, +0.1813] | %38.5 | -0.1186/-0.0854 | +0.0604/+0.1040 | [-0.0629, +0.2738] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.1244 |
| `C4_06_VOL_OK\|SHORT` | 873/1274 | 10861/12500 | +0.0829 | -0.0298 [-0.1787, +0.1261] | %39.6 | -0.0077/+0.0032 | +0.0906/-0.0330 | [-0.1973, +0.1334] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.6542 |
| `C4_07_TREND_ONLY\|BOTH` | 2212/2284 | 8651/7882 | -0.0054 | +0.0043 [-0.1200, +0.1268] | %39.4 | -0.0420/-0.0297 | +0.0366/+0.0340 | [-0.0974, +0.1801] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.3121 |
| `C4_07_TREND_ONLY\|LONG` | 1307/846 | 4504/2814 | -0.0397 | +0.0489 [-0.1124, +0.2128] | %39.7 | -0.0492/-0.0828 | +0.0095/+0.1317 | [-0.0619, +0.2992] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.0855 |
| `C4_07_TREND_ONLY\|SHORT` | 905/1438 | 4147/5068 | +0.0442 | -0.0220 [-0.1835, +0.1381] | %39.1 | -0.0342/-0.0001 | +0.0784/-0.0219 | [-0.2115, +0.1982] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5938 |
| `C4_08_REGIME_COST\|BOTH` | 1482/1620 | 13942/14500 | -0.1452 | -0.0386 [-0.1660, +0.1046] | %38.3 | -0.1335/-0.0153 | -0.0117/-0.0233 | [-0.1675, +0.1259] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.6237 |
| `C4_08_REGIME_COST\|LONG` | 1261/624 | 11721/6631 | -0.0876 | +0.0600 [-0.1320, +0.2516] | %39.1 | -0.0836/-0.0625 | -0.0040/+0.1225 | [-0.0770, +0.3344] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.1351 |
| `C4_08_REGIME_COST\|SHORT` | 221/996 | 2221/7869 | -0.4734 | -0.1004 [-0.2686, +0.0899] | %37.8 | -0.3968/+0.0245 | -0.0766/-0.1249 | [-0.3246, +0.0844] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.8797 |

#### C4 — kapasitesiz (sade) aylık ölçü

| Hücre | D fonlamasız [%95] | D fonlamalı [%95] | K fonlamasız | K fonlamalı | ≥ +%1 ay payı D (fonlamalı) | işlemsiz ay D | en kötü ay D (fonlamalı) |
|---|---|---|---|---|---|---|---|
| `C4_00_BASE\|BOTH` | -1.0236 [-8.1661, +6.2469] | -1.2714 [-8.4278, +6.0065] | +0.6030 | +0.3555 | %42.9 | 0 | 2025-01 -32.0155 |
| `C4_00_BASE\|LONG` | +0.2905 [-3.5167, +4.6330] | +0.1565 [-3.6315, +4.4459] | -0.6812 | -1.0696 | %23.8 | 0 | 2025-01 -14.5210 |
| `C4_00_BASE\|SHORT` | -1.3141 [-7.4239, +5.3756] | -1.4278 [-7.5260, +5.2616] | +1.2842 | +1.4252 | %33.3 | 0 | 2026-02 -25.2453 |
| `C4_01_REGIME\|BOTH` | -1.3304 [-7.6152, +5.5594] | -1.5592 [-7.8510, +5.3270] | -4.3809 | -4.7762 | %28.6 | 0 | 2026-02 -25.2453 |
| `C4_01_REGIME\|LONG` | +1.0623 [-2.3322, +4.9538] | +0.9495 [-2.4176, +4.8024] | -2.0863 | -2.4702 | %19.1 | 7 | 2025-01 -14.5210 |
| `C4_01_REGIME\|SHORT` | -2.3927 [-7.4498, +3.2093] | -2.5087 [-7.6136, +3.1053] | -2.2946 | -2.3059 | %14.3 | 7 | 2026-02 -25.2453 |
| `C4_02_EXIT_1R\|BOTH` | -2.2875 [-7.0682, +2.5025] | -2.4305 [-7.2105, +2.3640] | -0.9228 | -1.0934 | %42.9 | 0 | 2025-01 -19.5530 |
| `C4_02_EXIT_1R\|LONG` | -0.5729 [-2.3813, +1.4291] | -0.6449 [-2.4406, +1.3295] | -1.6171 | -1.8684 | %28.6 | 0 | 2026-04 -6.1706 |
| `C4_02_EXIT_1R\|SHORT` | -1.7146 [-5.5374, +2.4651] | -1.7856 [-5.6229, +2.4042] | +0.6943 | +0.7750 | %28.6 | 0 | 2026-02 -14.2564 |
| `C4_03_EXIT_3R\|BOTH` | +2.7529 [-7.7863, +13.4655] | +2.4088 [-8.1321, +13.1434] | +3.4504 | +3.1005 | %47.6 | 0 | 2025-01 -40.7662 |
| `C4_03_EXIT_3R\|LONG` | +1.5089 [-3.9418, +7.6327] | +1.3104 [-4.0603, +7.3620] | +1.1220 | +0.5307 | %28.6 | 0 | 2025-01 -20.4161 |
| `C4_03_EXIT_3R\|SHORT` | +1.2441 [-7.9396, +11.6233] | +1.0984 [-8.1177, +11.4781] | +2.3284 | +2.5698 | %28.6 | 0 | 2025-12 -28.5855 |
| `C4_04_WIDE_STOP\|BOTH` | +2.8594 [-4.7581, +10.3730] | +2.6354 [-4.9793, +10.1631] | +2.8801 | +2.5977 | %52.4 | 0 | 2025-01 -29.4046 |
| `C4_04_WIDE_STOP\|LONG` | +2.4989 [-1.2436, +6.9122] | +2.3631 [-1.3303, +6.7140] | +1.4664 | +1.0404 | %42.9 | 0 | 2025-01 -9.5631 |
| `C4_04_WIDE_STOP\|SHORT` | +0.3605 [-6.0635, +7.3813] | +0.2723 [-6.1661, +7.2893] | +1.4136 | +1.5573 | %28.6 | 0 | 2026-02 -21.2330 |
| `C4_05_COST_STOP\|BOTH` | -0.8181 [-7.5092, +5.9789] | -1.0376 [-7.7318, +5.7618] | +0.3609 | +0.1046 | %42.9 | 0 | 2025-01 -29.1928 |
| `C4_05_COST_STOP\|LONG` | +0.2765 [-3.1457, +4.0931] | +0.1556 [-3.2389, +3.9322] | -0.9341 | -1.3085 | %28.6 | 0 | 2025-01 -12.8213 |
| `C4_05_COST_STOP\|SHORT` | -1.0946 [-6.9330, +5.3226] | -1.1932 [-7.0448, +5.2378] | +1.2950 | +1.4131 | %28.6 | 0 | 2026-02 -26.0408 |
| `C4_06_VOL_OK\|BOTH` | -0.8208 [-6.8853, +5.2870] | -1.0192 [-7.1105, +5.0989] | +0.5270 | +0.3831 | %42.9 | 0 | 2026-02 -24.4209 |
| `C4_06_VOL_OK\|LONG` | +0.3305 [-2.9645, +4.0889] | +0.2090 [-3.0586, +3.9318] | -1.1969 | -1.4622 | %19.1 | 0 | 2025-01 -9.8391 |
| `C4_06_VOL_OK\|SHORT` | -1.1513 [-6.5796, +4.4560] | -1.2282 [-6.6667, +4.3907] | +1.7239 | +1.8453 | %42.9 | 0 | 2026-02 -24.8607 |
| `C4_07_TREND_ONLY\|BOTH` | -0.0148 [-6.9594, +7.1508] | -0.2814 [-7.2405, +6.8716] | -0.0330 | -0.3200 | %42.9 | 0 | 2026-02 -27.8940 |
| `C4_07_TREND_ONLY\|LONG` | +0.9846 [-2.4578, +5.0169] | +0.8481 [-2.5603, +4.8394] | -1.0812 | -1.4534 | %19.1 | 0 | 2025-01 -10.8422 |
| `C4_07_TREND_ONLY\|SHORT` | -0.9995 [-6.8445, +5.3531] | -1.1294 [-7.0127, +5.2194] | +1.0482 | +1.1334 | %28.6 | 0 | 2026-02 -27.3664 |
| `C4_08_REGIME_COST\|BOTH` | -1.5150 [-7.4281, +4.7585] | -1.7148 [-7.6128, +4.5612] | -4.4595 | -4.8400 | %28.6 | 0 | 2026-02 -26.0408 |
| `C4_08_REGIME_COST\|LONG` | +0.8660 [-2.1701, +4.2695] | +0.7639 [-2.2528, +4.1112] | -2.2800 | -2.6484 | %19.1 | 7 | 2025-01 -12.8213 |
| `C4_08_REGIME_COST\|SHORT` | -2.3810 [-7.2523, +2.9044] | -2.4788 [-7.3855, +2.8107] | -2.1795 | -2.1916 | %19.1 | 7 | 2026-02 -26.0408 |

#### C4 — kapasiteli aylık ölçü ve hedef (§8.2, §8.3)

| Hücre | BUMP D fonlamasız | BUMP D fonlamalı [%95] | BUMP K fonlamalı | NO_BUMP D fonlamasız | NO_BUMP D fonlamalı [%95] | NO_BUMP K fonlamalı | kabul/aday BUMP · NO_BUMP | çıkarılan (BUMP) | NaN fonlama D (BUMP/NO_BUMP) | sansür K/D | hedef (§8.3) | düşen şartlar | §8.5 durumu |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `C4_00_BASE\|BOTH` | +0.5844 | +0.4175 [-5.5893, +6.5815] | +0.4490 | +0.3082 | +0.1702 [-4.1096, +4.4765] | -0.1537 | 4091/5221 · 3582/5221 | 562 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_00_BASE\|LONG` | +0.3025 | +0.1931 [-2.1982, +2.8381] | +0.2849 | +0.2788 | +0.2100 [-1.7258, +2.3320] | -1.1675 | 2014/2482 · 1731/2482 | 312 | %0.0/%0.0 | %0.0/%0.5 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_00_BASE\|SHORT` | -0.0532 | -0.1068 [-5.5283, +5.8172] | +0.4513 | -0.2346 | -0.3014 [-4.3590, +4.0408] | +1.1417 | 2261/2739 · 1996/2739 | 277 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_01_REGIME\|BOTH` | -0.8155 | -0.9732 [-5.6220, +3.9133] | -3.0282 | -0.7198 | -0.8467 [-4.6696, +3.1209] | -3.2563 | 2657/3307 · 2295/3307 | 392 | %0.0/%0.0 | %0.0/%0.3 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_01_REGIME\|LONG` | +0.5313 | +0.4341 [-1.7391, +2.8939] | -0.7421 | +0.6460 | +0.5884 [-0.9730, +2.3974] | -1.9117 | 1627/2020 · 1376/2020 | 276 | %0.0/%0.0 | %0.0/%0.7 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_01_REGIME\|SHORT` | -1.3468 | -1.4072 [-5.4100, +2.8883] | -2.2861 | -1.3658 | -1.4351 [-4.8892, +2.1806] | -1.3446 | 1030/1287 · 919/1287 | 116 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_02_EXIT_1R\|BOTH` | +0.1073 | -0.0116 [-4.6776, +4.8154] | -0.8021 | -0.4810 | -0.5763 [-3.9235, +2.7930] | -0.5928 | 4626/5221 · 4025/5221 | 642 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_02_EXIT_1R\|LONG` | +0.5487 | +0.4768 [-1.0337, +2.1387] | -0.8901 | +0.0548 | +0.0161 [-1.2029, +1.2871] | -1.4576 | 2250/2482 · 1915/2482 | 354 | %0.0/%0.0 | %0.0/%0.4 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_02_EXIT_1R\|SHORT` | -0.7939 | -0.8403 [-4.7146, +3.3889] | +0.2128 | -0.7792 | -0.8355 [-3.6651, +2.2254] | +0.9905 | 2499/2739 · 2202/2739 | 310 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_03_EXIT_3R\|BOTH` | +3.4345 | +3.2448 [-3.7100, +10.6648] | +1.4434 | +2.8190 | +2.6599 [-2.8800, +8.5566] | +1.2980 | 3423/5221 · 3030/5221 | 486 | %0.0/%0.0 | %0.0/%0.6 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_03_EXIT_3R\|LONG` | +0.8902 | +0.7470 [-2.6422, +4.3955] | +0.5461 | +0.7368 | +0.6501 [-2.0548, +3.7419] | -0.4769 | 1749/2482 · 1528/2482 | 269 | %0.0/%0.0 | %0.0/%1.7 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_03_EXIT_3R\|SHORT` | +2.0260 | +1.9743 [-4.3130, +9.2450] | +0.8059 | +1.8440 | +1.7685 [-3.3828, +7.5962] | +1.5173 | 1913/2739 · 1699/2739 | 245 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_04_WIDE_STOP\|BOTH` | +2.4929 | +2.3404 [-3.7213, +8.2084] | +2.6959 | +1.8958 | +1.7782 [-2.0759, +5.5031] | +1.5799 | 3594/5204 · 2945/5204 | 692 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_04_WIDE_STOP\|LONG` | +1.4431 | +1.3231 [-1.5865, +4.7106] | +1.9567 | +1.1510 | +1.0876 [-0.8629, +3.3901] | +0.5143 | 1793/2474 · 1426/2474 | 387 | %0.0/%0.0 | %0.0/%0.5 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_04_WIDE_STOP\|SHORT` | +1.0102 | +0.9785 [-4.2077, +6.6667] | +0.3036 | +0.6323 | +0.5758 [-2.8425, +4.3362] | +0.5777 | 2035/2730 · 1693/2730 | 356 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_05_COST_STOP\|BOTH` | +0.7617 | +0.5981 [-5.3321, +6.6011] | +0.3238 | +0.4175 | +0.2851 [-3.9180, +4.4768] | -0.1556 | 3779/4826 · 3299/4826 | 532 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_05_COST_STOP\|LONG` | +0.4082 | +0.2993 [-2.0612, +2.9261] | +0.2088 | +0.3555 | +0.2879 [-1.6167, +2.3360] | -1.1075 | 1838/2270 · 1572/2270 | 294 | %0.0/%0.0 | %0.0/%0.4 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_05_COST_STOP\|SHORT` | +0.0129 | -0.0422 [-5.3767, +5.7516] | +0.4399 | -0.1777 | -0.2436 [-4.2313, +4.0042] | +1.1056 | 2098/2556 · 1849/2556 | 261 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_06_VOL_OK\|BOTH` | +0.9688 | +0.8423 [-4.1971, +5.9791] | +0.3027 | +0.2532 | +0.1460 [-3.2647, +3.4905] | -0.0659 | 3174/3958 · 2851/3958 | 357 | %0.0/%0.0 | %0.0/%0.1 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_06_VOL_OK\|LONG` | +0.2463 | +0.1510 [-2.0748, +2.6446] | -0.9488 | +0.1565 | +0.0933 [-1.7146, +2.0833] | -1.4854 | 1513/1811 · 1322/1811 | 207 | %0.0/%0.0 | %0.0/%0.4 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_06_VOL_OK\|SHORT` | +0.3505 | +0.3216 [-4.3852, +5.4128] | +1.4049 | -0.1218 | -0.1649 [-3.6687, +3.4454] | +1.5590 | 1794/2147 · 1636/2147 | 167 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_07_TREND_ONLY\|BOTH` | +0.8037 | +0.6301 [-5.2255, +6.5620] | -0.1461 | +0.2094 | +0.0643 [-4.0760, +4.1796] | -0.4267 | 3578/4496 · 3107/4496 | 511 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_07_TREND_ONLY\|LONG` | +0.8482 | +0.7410 [-1.4566, +3.2978] | -0.1002 | +0.5265 | +0.4574 [-1.2329, +2.4260] | -1.3811 | 1697/2153 · 1445/2153 | 280 | %0.0/%0.0 | %0.0/%0.6 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_07_TREND_ONLY\|SHORT` | -0.0445 | -0.1109 [-5.2657, +5.4426] | -0.0459 | -0.3171 | -0.3931 [-4.1967, +3.5743] | +0.9543 | 1881/2343 · 1662/2343 | 231 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_08_REGIME_COST\|BOTH` | -0.8648 | -1.0197 [-5.6134, +3.7049] | -3.0247 | -0.7453 | -0.8686 [-4.6339, +2.9387] | -3.2031 | 2492/3102 · 2145/3102 | 376 | %0.0/%0.0 | %0.0/%0.2 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_08_REGIME_COST\|LONG` | +0.4760 | +0.3802 [-1.6986, +2.7486] | -0.7714 | +0.6374 | +0.5819 [-0.8926, +2.3070] | -1.8767 | 1519/1885 · 1279/1885 | 264 | %0.0/%0.0 | %0.0/%0.6 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `C4_08_REGIME_COST\|SHORT` | -1.3409 | -1.3999 [-5.4012, +2.8024] | -2.2533 | -1.3826 | -1.4504 [-4.8740, +2.0635] | -1.3264 | 973/1217 · 866/1217 | 112 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |

#### C4 — plasebo, çıkış ve maliyet (bilgi)

| Hücre | ham sinyal | temiz pencere dışı | plasebo çekilen | NO_REAL_RISK payı | BAD_BAR payı | çıkışlar D | ort. tutma D (bar) | maliyet R D | örtüşmesiz ort.R K/D |
|---|---|---|---|---|---|---|---|---|---|
| `C4_00_BASE\|BOTH` | 6050 | 0 | 63209 | %17.1 | %0.0 | STOP 1444, TARGET 683, TIME 498, DATA_END 5 | 11.01 | 0.05 | +0.0080/+0.0007 (n 2081/2028) |
| `C4_00_BASE\|LONG` | 2756 | 0 | 30088 | %18.3 | %0.0 | STOP 553, TARGET 288, TIME 177, DATA_END 5 | 10.65 | 0.0547 | +0.0013/+0.0125 (n 1194/832) |
| `C4_00_BASE\|SHORT` | 3294 | 0 | 33121 | %16.0 | %0.0 | STOP 891, TARGET 395, TIME 321 | 11.23 | 0.047 | +0.0215/-0.0202 (n 979/1289) |
| `C4_01_REGIME\|BOTH` | 3896 | 0 | 33786 | %17.1 | %0.0 | STOP 942, TARGET 426, TIME 348, DATA_END 5 | 11.21 | 0.049 | -0.1136/-0.0328 (n 1321/1352) |
| `C4_01_REGIME\|LONG` | 2066 | 0 | 19660 | %18.3 | %0.0 | STOP 353, TARGET 204, TIME 111, DATA_END 5 | 10.47 | 0.0492 | -0.0437/+0.0469 (n 1106/533) |
| `C4_01_REGIME\|SHORT` | 1830 | 0 | 14126 | %16.0 | %0.0 | STOP 589, TIME 237, TARGET 222 | 11.69 | 0.0489 | -0.4731/-0.0847 (n 215/819) |
| `C4_02_EXIT_1R\|BOTH` | 6050 | 0 | 63209 | %17.1 | %0.0 | TARGET 1265, STOP 1215, TIME 146, DATA_END 4 | 7.3 | 0.05 | -0.0175/-0.0170 (n 2333/2311) |
| `C4_02_EXIT_1R\|LONG` | 2756 | 0 | 30088 | %18.3 | %0.0 | TARGET 502, STOP 458, TIME 59, DATA_END 4 | 6.99 | 0.0547 | -0.0404/-0.0025 (n 1335/927) |
| `C4_02_EXIT_1R\|SHORT` | 3294 | 0 | 33121 | %16.0 | %0.0 | TARGET 763, STOP 757, TIME 87 | 7.5 | 0.047 | +0.0170/-0.0380 (n 1057/1449) |
| `C4_03_EXIT_3R\|BOTH` | 6050 | 0 | 63209 | %17.1 | %0.0 | STOP 1747, TARGET 610, TIME 256, DATA_END 17 | 18.4 | 0.05 | +0.0625/+0.0819 (n 1757/1683) |
| `C4_03_EXIT_3R\|LONG` | 2756 | 0 | 30088 | %18.3 | %0.0 | STOP 676, TARGET 249, TIME 81, DATA_END 17 | 17.37 | 0.0547 | +0.0479/+0.0544 (n 1032/730) |
| `C4_03_EXIT_3R\|SHORT` | 3294 | 0 | 33121 | %16.0 | %0.0 | STOP 1071, TARGET 361, TIME 175 | 19.06 | 0.0469 | +0.0545/+0.0726 (n 844/1076) |
| `C4_04_WIDE_STOP\|BOTH` | 6050 | 0 | 63209 | %17.1 | %0.0 | STOP 1083, TIME 1035, TARGET 503, DATA_END 5 | 15.83 | 0.0327 | +0.0617/+0.0613 (n 1844/1788) |
| `C4_04_WIDE_STOP\|LONG` | 2756 | 0 | 30088 | %18.3 | %0.0 | STOP 412, TIME 376, TARGET 228, DATA_END 5 | 15.45 | 0.0361 | +0.0838/+0.0690 (n 1059/757) |
| `C4_04_WIDE_STOP\|SHORT` | 3294 | 0 | 33121 | %16.0 | %0.0 | STOP 671, TIME 659, TARGET 275 | 16.07 | 0.0305 | +0.0054/+0.0409 (n 902/1149) |
| `C4_05_COST_STOP\|BOTH` | 6050 | 0 | 63209 | %17.1 | %0.0 | STOP 1332, TARGET 622, TIME 488, DATA_END 4 | 11.32 | 0.0424 | +0.0033/+0.0078 (n 1909/1888) |
| `C4_05_COST_STOP\|LONG` | 2756 | 0 | 30088 | %18.3 | %0.0 | STOP 490, TARGET 252, TIME 170, DATA_END 4 | 11.01 | 0.0436 | -0.0055/+0.0237 (n 1105/744) |
| `C4_05_COST_STOP\|SHORT` | 3294 | 0 | 33121 | %16.0 | %0.0 | STOP 842, TARGET 370, TIME 318 | 11.51 | 0.0417 | +0.0243/-0.0146 (n 883/1222) |
| `C4_06_VOL_OK\|BOTH` | 4625 | 0 | 47565 | %25.8 | %0.0 | STOP 1157, TARGET 547, TIME 373, DATA_END 3 | 10.69 | 0.0546 | +0.0063/+0.0061 (n 1539/1638) |
| `C4_06_VOL_OK\|LONG` | 2019 | 0 | 21765 | %28.6 | %0.0 | STOP 447, TARGET 236, TIME 120, DATA_END 3 | 10.17 | 0.0598 | -0.0548/+0.0088 (n 850/664) |
| `C4_06_VOL_OK\|SHORT` | 2606 | 0 | 25800 | %23.3 | %0.0 | STOP 710, TARGET 311, TIME 253 | 11.01 | 0.0513 | +0.0820/-0.0129 (n 754/1043) |
| `C4_07_TREND_ONLY\|BOTH` | 5181 | 0 | 18938 | %10.6 | %0.0 | STOP 1233, TARGET 599, TIME 447, DATA_END 5 | 11.28 | 0.0477 | -0.0038/+0.0120 (n 1802/1793) |
| `C4_07_TREND_ONLY\|LONG` | 2336 | 0 | 7929 | %14.6 | %0.0 | STOP 445, TARGET 247, TIME 149, DATA_END 5 | 10.91 | 0.0538 | -0.0178/+0.0634 (n 1044/663) |
| `C4_07_TREND_ONLY\|SHORT` | 2845 | 0 | 11009 | %7.4 | %0.0 | STOP 788, TARGET 352, TIME 298 | 11.49 | 0.0442 | +0.0156/-0.0181 (n 758/1130) |
| `C4_08_REGIME_COST\|BOTH` | 3896 | 0 | 33786 | %17.1 | %0.0 | STOP 884, TARGET 386, TIME 346, DATA_END 4 | 11.52 | 0.0426 | -0.1220/-0.0376 (n 1234/1273) |
| `C4_08_REGIME_COST\|LONG` | 2066 | 0 | 19660 | %18.3 | %0.0 | STOP 326, TARGET 183, TIME 111, DATA_END 4 | 10.76 | 0.0419 | -0.0522/+0.0447 (n 1034/496) |
| `C4_08_REGIME_COST\|SHORT` | 1830 | 0 | 14126 | %16.0 | %0.0 | STOP 558, TIME 235, TARGET 203 | 11.99 | 0.0431 | -0.4830/-0.0901 (n 200/777) |

## 4. Formasyon — 4h üç beyaz asker + RSI14 > 70 (10 varyant + kontrol, 12 hücre)

**Taban (`FM_00_BASE`):** keşifte +0,1586 R [+0,0340, +0,2690] (n 898), doğrulamada +0,2451 R [+0,0551, +0,4165] (n 799),
kazanma %51,1. Plasebo keşifte +0,0645, doğrulamada −0,0181. Fark keşifte +0,0941, doğrulamada +0,2632 [+0,0351, +0,4659].
Hüküm: **GÜÇLÜ ADAY, sıkı GÜÇLÜ ADAY**, fonlamasız ve fonlamalı (fonlamalı doğrulama +0,2372 R, fark +0,2573 [+0,0311,
+0,4591]). Holm p 0,0099.

Kapasiteli, fonlamalı aylık %:

| Biçim | Doğrulama [%95] | Keşif [%95] | Kabul / aday | Çıkarılan | Ret nedenleri |
|---|---|---|---|---|---|
| `BUMP` | +2.7285 [-0.8561, +6.7558] | +1.8108 [-0.6043, +4.4140] | 882 / 1697 | 425 | SAME_SYMBOL 667, MIN_ORDER_CONFLICT 148 |
| `NO_BUMP` | +1.0132 [-0.4943, +2.7321] | +0.7517 [-0.3171, +1.9401] | 486 / 1697 | 0 | MIN_ORDER_CONFLICT 839, SAME_SYMBOL 372 |

- Düşen şart yalnız 3: aralığın alt ucu iki biçimde de sıfırın altında. Aylık ortalamalar +%1'in üstünde, keşif artı, NaN
  fonlama 0, sansür 0.
- Hedefi karşılasaydı bile öneri olamazdı: taban hücredir (§8.5 basamak 3) ve Holm'u geçmedi (basamak 2).
- Kapasitenin asıl sınırı min-notional. 100 USDT bakiye ve 30 slotla slot başına notional tavanı 9,5 USDT; borsanın en küçük
  emri 5–50 USDT (BTC 50, ETH/BCH/LTC/LINK 20). `BUMP`'ta 882 kabulün 425'i en küçük emre çıkarıldı; kabul edilenlerin
  %47,28'inde risk payı w > 1 (en çok 3,99, yani işlem başına %0,5 yerine ~%2 risk). `NO_BUMP` adayların 839'unu
  `MIN_ORDER_CONFLICT` ile reddetti. Doğrulamada en kötü ay `BUMP` 2025-03 −9,0705, `NO_BUMP` 2026-03
  −4,6785; +%1'i geçen ay payı %47,6 / %33,3. `BUMP` ortalamasını son iki ay taşıyor: fonlamalı seride 2026-08 +25,6589 ve
  2026-09 +20,8597. Bu iki ay olmadan kalan 19 ayın ortalaması yaklaşık +%0,57 olurdu (bu belgede seriden hesaplandı; rapor
  sayısı değil).
- Bilgi: K = 3 slotla doğrulama fonlamalı +0,3523; günlük işaretlemeli ölçü `BUMP` doğrulama +2,8194 [−0,7769, +6,8859].

**Varyantlar tabana göre:**

- `FM_05_COST_STOP`: tabanla neredeyse aynı küme (keşif/doğrulama 891/785 işlem, taban 898/799). Doğrulama +0,2471 R, fark
  +0,2714 [+0,0199, +0,4711]; sıkı GÜÇLÜ ADAY (iki sürüm). Holm p 0,0084. Kapasiteli `NO_BUMP` fonlamalı +0,9946 (< +1,0, şart 2)
  ve iki biçimde aralık sıfırı içeriyor (şart 3). HEDEFİ KARŞILAMIYOR.
- `FM_07_EXIT_TREND` (hedef yok, kanal çıkışı): doğrulama +0,2478 R; standart GÜÇLÜ ADAY, sıkı ZAYIF İZ (fark aralığı
  [−0,0157, +0,4310]). Holm p 0,0485. Kapasiteli `NO_BUMP` +0,7559.
- `FM_10_BTC_UP_EXIT_TREND`: fonlamasız standart GÜÇLÜ ADAY / sıkı ZAYIF İZ; fonlamalı ZAYIF İZ.
- `FM_01_BTC_UP`: doğrulama ort.R en yüksek (+0,3068) ve Holm p en küçük (0,0031), ama keşif aralığı sıfırı içeriyor
  (+0,1042 [−0,0145, +0,2301]) → ZAYIF İZ. Kapasiteli `NO_BUMP` +0,9703.
- `FM_02`, `FM_03`, `FM_04`, `FM_08`: ZAYIF İZ; doğrulama aralıkları sıfırı içeriyor.
- `FM_09_REGIME_BOTH`: BOTH ve LONG ZAYIF İZ; SHORT (üç kara karga + RSI < 30 + `BTC_DOWN`) KANIT YOK (keşif −0,3718, doğrulama
  +0,0406). BOTH hücre kapasiteli `NO_BUMP` aralığı sıfırın üstünde olan tek hücre ([+0,0910, +3,1274]); `BUMP` aralığı
  [−0,4020, +7,6489] ve sıkı hüküm ZAYIF İZ olduğu için hedefi karşılamadı.
- **Kontrol `FM_CTRL_NO_RSI`** (RSI süzgeci yok; hipotez değil): doğrulama +0,0826 R [−0,0349, +0,2081], plaseboya göre
  +0,1757 [+0,0368, +0,3162]; ZAYIF İZ. RSI > 70 süzgeci doğrulama ort.R'yi +0,0826'dan +0,2451'e çıkarıyor. Bu süzgecin 1.412
  hücreden seçildiği ve doğrulama döneminin seçim koşusuyla örtüştüğü unutulmamalı.
- `NO_REAL_RISK` payı %0,1–%1,5; plasebo n her hücrede yeterli (en az 132 / 606).

Sütunlar: n gerçek ve n plasebo keşif/doğrulama; ort.R ve aralıklar gün kümeli %95; "fark" = gerçek ort.R − eşleştirilmiş
plasebo ort.R (fonlamasız); hüküm standart / sıkı; Holm p dört basamak. Kapasiteli ölçüde "D" doğrulama, "K" keşif aylık
ortalamasıdır; aralık ay kümeli %95. "düşen şartlar" §8.3 numaralarıyla: 1 sıkı hüküm, 2 aylık ≥ +%1, 3 aralık alt ucu > 0, 4
keşif > 0, 7 sansür/veri. Bilgi tablosunda "plasebo çekilen" risk geometrisinden önce seçilen plasebo sayısıdır; "örtüşmesiz
ort.R" sembol başına tek pozisyon yaklaşımıdır (bilgi).

#### Formasyon — hüküm tablosu

| Hücre | n gerçek K/D | n plasebo K/D | ort.R K | ort.R D [%95] | kazanma D | plasebo ort.R K/D | fark K/D | D fark %95 | hüküm fonlamasız (std / sıkı) | hüküm fonlamalı (std / sıkı) | Holm p |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `FM_00_BASE\|LONG` | 898/799 | 1255/1004 | +0.1586 | +0.2451 [+0.0551, +0.4165] | %51.1 | +0.0645/-0.0181 | +0.0941/+0.2632 | [+0.0351, +0.4659] | GÜÇLÜ ADAY / GÜÇLÜ ADAY | GÜÇLÜ ADAY / GÜÇLÜ ADAY | 0.0099 |
| `FM_01_BTC_UP\|LONG` | 800/568 | 1063/717 | +0.1042 | +0.3068 [+0.0501, +0.5144] | %54.2 | +0.0095/-0.0756 | +0.0947/+0.3824 | [+0.1313, +0.6223] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0031 |
| `FM_02_COIN_UP\|LONG` | 597/370 | 750/479 | +0.1562 | +0.2013 [-0.0471, +0.4303] | %50.0 | +0.0119/-0.0538 | +0.1443/+0.2551 | [-0.0136, +0.4967] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0283 |
| `FM_03_VOL_OK\|LONG` | 637/669 | 828/889 | +0.1694 | +0.2282 [-0.0126, +0.4206] | %50.7 | +0.0594/-0.0489 | +0.1100/+0.2771 | [+0.0628, +0.4982] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0107 |
| `FM_04_VOL_CONFIRM\|LONG` | 496/345 | 823/575 | +0.1987 | +0.2120 [-0.0464, +0.4377] | %48.4 | +0.0122/-0.0966 | +0.1865/+0.3086 | [+0.0331, +0.5647] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0162 |
| `FM_05_COST_STOP\|LONG` | 891/785 | 1183/1024 | +0.1519 | +0.2471 [+0.0563, +0.4310] | %51.2 | +0.0574/-0.0243 | +0.0945/+0.2714 | [+0.0199, +0.4711] | GÜÇLÜ ADAY / GÜÇLÜ ADAY | GÜÇLÜ ADAY / GÜÇLÜ ADAY | 0.0084 |
| `FM_07_EXIT_TREND\|LONG` | 898/799 | 1139/1036 | +0.6031 | +0.2478 [+0.0248, +0.4642] | %38.8 | +0.3221/+0.0348 | +0.2810/+0.2130 | [-0.0157, +0.4310] | GÜÇLÜ ADAY / ZAYIF İZ | GÜÇLÜ ADAY / ZAYIF İZ | 0.0485 |
| `FM_08_EXIT_3R\|LONG` | 898/799 | 1156/1070 | +0.2980 | +0.1713 [-0.0787, +0.4178] | %37.9 | +0.1972/+0.0300 | +0.1008/+0.1413 | [-0.1258, +0.4024] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.1677 |
| `FM_09_REGIME_BOTH\|BOTH` | 877/1060 | 1184/1348 | +0.0624 | +0.1832 [+0.0125, +0.3636] | %48.6 | -0.0539/+0.0511 | +0.1163/+0.1321 | [-0.0706, +0.3090] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0880 |
| `FM_09_REGIME_BOTH\|LONG` | 800/568 | 1052/742 | +0.1042 | +0.3068 [+0.0501, +0.5144] | %54.2 | -0.0124/+0.0457 | +0.1166/+0.2611 | [+0.0054, +0.5066] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0249 |
| `FM_09_REGIME_BOTH\|SHORT` | 77/492 | 132/606 | -0.3718 | +0.0406 [-0.1974, +0.2803] | %42.1 | -0.3850/+0.0578 | +0.0132/-0.0172 | [-0.2878, +0.2354] | KANIT YOK / KANIT YOK | KANIT YOK / KANIT YOK | 0.5633 |
| `FM_10_BTC_UP_EXIT_TREND\|LONG` | 800/568 | 1005/721 | +0.5314 | +0.2905 [+0.0021, +0.5922] | %40.8 | +0.0560/+0.0126 | +0.4754/+0.2779 | [-0.0275, +0.5850] | GÜÇLÜ ADAY / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | 0.0377 |
| `FM_CTRL_NO_RSI\|LONG` | 3911/3508 | 4372/4046 | +0.0390 | +0.0826 [-0.0349, +0.2081] | %44.0 | +0.0052/-0.0931 | +0.0338/+0.1757 | [+0.0368, +0.3162] | ZAYIF İZ / ZAYIF İZ | ZAYIF İZ / ZAYIF İZ | — |

#### Formasyon — kapasitesiz (sade) aylık ölçü

| Hücre | D fonlamasız [%95] | D fonlamalı [%95] | K fonlamasız | K fonlamalı | ≥ +%1 ay payı D (fonlamalı) | işlemsiz ay D | en kötü ay D (fonlamalı) |
|---|---|---|---|---|---|---|---|
| `FM_00_BASE\|LONG` | +4.6625 [-0.1696, +9.9354] | +4.5121 [-0.2574, +9.7355] | +2.9678 | +2.7459 | %52.4 | 0 | 2026-03 -8.8126 |
| `FM_01_BTC_UP\|LONG` | +4.1492 [+0.2426, +8.4997] | +4.0323 [+0.1772, +8.3336] | +1.7374 | +1.5194 | %33.3 | 8 | 2025-03 -8.1064 |
| `FM_02_COIN_UP\|LONG` | +1.7733 [-0.1486, +3.9996] | +1.6933 [-0.2055, +3.8795] | +1.9430 | +1.7744 | %33.3 | 3 | 2025-01 -5.2663 |
| `FM_03_VOL_OK\|LONG` | +3.6350 [-0.4678, +7.9316] | +3.5013 [-0.5584, +7.7489] | +2.2482 | +2.0908 | %47.6 | 0 | 2026-03 -8.3032 |
| `FM_04_VOL_CONFIRM\|LONG` | +1.7412 [-0.8768, +4.8554] | +1.6835 [-0.9094, +4.7759] | +2.0534 | +1.9331 | %28.6 | 0 | 2026-03 -7.1839 |
| `FM_05_COST_STOP\|LONG` | +4.6176 [-0.1912, +9.8436] | +4.4691 [-0.2902, +9.6414] | +2.8192 | +2.5984 | %52.4 | 0 | 2026-03 -9.7537 |
| `FM_07_EXIT_TREND\|LONG` | +4.7145 [-2.0656, +14.2300] | +4.4484 [-2.2109, +13.7948] | +11.2835 | +10.7020 | %33.3 | 0 | 2025-08 -11.9174 |
| `FM_08_EXIT_3R\|LONG` | +3.2584 [-2.6964, +11.3731] | +3.0074 [-2.8625, +11.0324] | +5.5754 | +5.1654 | %42.9 | 0 | 2025-01 -11.0996 |
| `FM_09_REGIME_BOTH\|BOTH` | +4.6245 [-0.2499, +9.9448] | +4.4283 [-0.4140, +9.7097] | +1.1410 | +0.9163 | %42.9 | 0 | 2025-03 -10.0101 |
| `FM_09_REGIME_BOTH\|LONG` | +4.1492 [+0.2426, +8.4997] | +4.0323 [+0.1772, +8.3336] | +1.7374 | +1.5194 | %33.3 | 8 | 2025-03 -8.1064 |
| `FM_09_REGIME_BOTH\|SHORT` | +0.4753 [-2.6665, +4.0561] | +0.3960 [-2.7168, +3.9313] | -0.5964 | -0.6030 | %14.3 | 9 | 2025-04 -16.1593 |
| `FM_10_BTC_UP_EXIT_TREND\|LONG` | +3.9284 [-1.8747, +12.8201] | +3.7247 [-1.9844, +12.4240] | +8.8572 | +8.3171 | %23.8 | 8 | 2025-08 -11.9174 |
| `FM_CTRL_NO_RSI\|LONG` | +6.9043 [-4.5541, +18.9284] | +6.4497 [-4.9300, +18.3953] | +3.1749 | +2.1291 | %47.6 | 0 | 2025-12 -34.8151 |

#### Formasyon — kapasiteli aylık ölçü ve hedef (§8.2, §8.3)

| Hücre | BUMP D fonlamasız | BUMP D fonlamalı [%95] | BUMP K fonlamalı | NO_BUMP D fonlamasız | NO_BUMP D fonlamalı [%95] | NO_BUMP K fonlamalı | kabul/aday BUMP · NO_BUMP | çıkarılan (BUMP) | NaN fonlama D (BUMP/NO_BUMP) | sansür K/D | hedef (§8.3) | düşen şartlar | §8.5 durumu |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `FM_00_BASE\|LONG` | +2.8194 | +2.7285 [-0.8561, +6.7558] | +1.8108 | +1.0441 | +1.0132 [-0.4943, +2.7321] | +0.7517 | 882/1697 · 486/1697 | 425 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_01_BTC_UP\|LONG` | +2.6970 | +2.6262 [-0.1153, +5.6725] | +0.6465 | +0.9951 | +0.9703 [-0.0909, +2.2689] | +0.3183 | 687/1368 · 364/1368 | 345 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_02_COIN_UP\|LONG` | +1.2587 | +1.2120 [-0.1312, +2.8655] | +0.4666 | +0.3768 | +0.3646 [-0.2271, +1.0767] | +0.3577 | 465/967 · 226/967 | 248 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_03_VOL_OK\|LONG` | +2.5159 | +2.4310 [-0.7674, +5.9291] | +1.2194 | +0.5774 | +0.5484 [-0.6506, +1.8453] | +0.5312 | 702/1306 · 413/1306 | 311 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_04_VOL_CONFIRM\|LONG` | +1.5240 | +1.4759 [-1.1475, +4.5384] | +1.6740 | +0.5839 | +0.5688 [-0.4454, +1.7965] | +0.6953 | 561/841 · 297/841 | 274 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_05_COST_STOP\|LONG` | +2.8011 | +2.7099 [-0.8796, +6.7419] | +1.8155 | +1.0258 | +0.9946 [-0.5012, +2.6953] | +0.7564 | 878/1676 · 482/1676 | 425 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_07_EXIT_TREND\|LONG` | +2.9256 | +2.7912 [-0.9996, +7.2411] | +4.8473 | +0.8030 | +0.7559 [-0.7902, +2.8380] | +3.1327 | 733/1697 · 419/1697 | 344 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_08_EXIT_3R\|LONG` | +2.7944 | +2.6484 [-1.7254, +7.5749] | +2.5385 | +0.7525 | +0.7008 [-1.0068, +2.8960] | +0.9368 | 787/1697 · 431/1697 | 391 | %0.0/%0.0 | %0.0/%1.9 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_09_REGIME_BOTH\|BOTH` | +3.6641 | +3.5516 [-0.4020, +7.6489] | +0.2171 | +1.5701 | +1.5196 [+0.0910, +3.1274] | +0.1056 | 988/1937 · 525/1937 | 493 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_09_REGIME_BOTH\|LONG` | +2.6970 | +2.6262 [-0.1153, +5.6725] | +0.6465 | +0.9951 | +0.9703 [-0.0909, +2.2689] | +0.3183 | 687/1368 · 364/1368 | 345 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_09_REGIME_BOTH\|SHORT` | +0.9671 | +0.9254 [-1.7781, +4.1562] | -0.4294 | +0.5750 | +0.5493 [-0.3613, +1.6348] | -0.2127 | 301/569 · 161/569 | 148 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 2:AYLIK_BUMP_unfunded, 2:AYLIK_BUMP_funded, 3:ARALIK_BUMP, 4:KESIF_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP, 4:KESIF_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_10_BTC_UP_EXIT_TREND\|LONG` | +2.1225 | +2.0223 [-0.7267, +5.3040] | +2.9278 | +0.7819 | +0.7435 [-0.5292, +2.6636] | +2.6128 | 571/1368 · 315/1368 | 277 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 2:AYLIK_NO_BUMP_unfunded, 2:AYLIK_NO_BUMP_funded, 3:ARALIK_NO_BUMP | HEDEFİ KARŞILAMIYOR |
| `FM_CTRL_NO_RSI\|LONG` | +1.7179 | +1.4662 [-5.4142, +9.1086] | +2.2389 | +1.3546 | +1.2458 [-1.7790, +4.4147] | +0.8638 | 3731/7419 · 2401/7419 | 1464 | %0.0/%0.0 | %0.0/%0.0 | HEDEFİ KARŞILAMIYOR | 1:SIKI_FONLAMASIZ, 1:SIKI_FONLAMALI, 3:ARALIK_BUMP, 3:ARALIK_NO_BUMP | KONTROL (hipotez değil) |

#### Formasyon — plasebo, çıkış ve maliyet (bilgi)

| Hücre | ham sinyal | temiz pencere dışı | plasebo çekilen | NO_REAL_RISK payı | BAD_BAR payı | çıkışlar D | ort. tutma D (bar) | maliyet R D | örtüşmesiz ort.R K/D |
|---|---|---|---|---|---|---|---|---|---|
| `FM_00_BASE\|LONG` | 2681 | 126 | 2462 | %0.1 | %0.0 | TIME 371, STOP 246, TARGET 182 | 17.14 | 0.0307 | +0.1882/+0.1656 (n 534/435) |
| `FM_01_BTC_UP\|LONG` | 1951 | 126 | 2003 | %0.7 | %0.0 | TIME 267, STOP 168, TARGET 133 | 16.79 | 0.0288 | +0.1257/+0.2318 (n 473/294) |
| `FM_02_COIN_UP\|LONG` | 1370 | 126 | 1364 | %1.5 | %0.0 | TIME 159, STOP 129, TARGET 82 | 16.75 | 0.0356 | +0.1618/+0.1421 (n 341/196) |
| `FM_03_VOL_OK\|LONG` | 2025 | 126 | 1842 | %0.3 | %0.0 | TIME 301, STOP 219, TARGET 149 | 17.17 | 0.032 | +0.1809/+0.1620 (n 389/375) |
| `FM_04_VOL_CONFIRM\|LONG` | 1675 | 126 | 1624 | %0.3 | %0.0 | TIME 169, STOP 101, TARGET 75 | 17.39 | 0.0281 | +0.2165/+0.2073 (n 369/252) |
| `FM_05_COST_STOP\|LONG` | 2681 | 126 | 2432 | %0.2 | %0.0 | TIME 369, STOP 239, TARGET 177 | 17.23 | 0.0291 | +0.1907/+0.1608 (n 533/432) |
| `FM_07_EXIT_TREND\|LONG` | 2681 | 126 | 2406 | %0.2 | %0.0 | RULE 631, STOP 168 | 26.23 | 0.0307 | +0.6064/+0.2230 (n 432/367) |
| `FM_08_EXIT_3R\|LONG` | 2681 | 126 | 2414 | %0.2 | %0.0 | STOP 397, TIME 228, TARGET 159, DATA_END 15 | 34.17 | 0.0306 | +0.2490/+0.1640 (n 472/386) |
| `FM_09_REGIME_BOTH\|BOTH` | 3156 | 260 | 2760 | %0.6 | %0.0 | TIME 577, STOP 289, TARGET 194 | 17.75 | 0.0261 | +0.0675/+0.1562 (n 539/561) |
| `FM_09_REGIME_BOTH\|LONG` | 1951 | 126 | 1995 | %0.8 | %0.0 | TIME 267, STOP 168, TARGET 133 | 16.79 | 0.0288 | +0.1257/+0.2318 (n 473/294) |
| `FM_09_REGIME_BOTH\|SHORT` | 1205 | 134 | 765 | %0.3 | %0.0 | TIME 310, STOP 121, TARGET 61 | 18.86 | 0.023 | -0.3490/+0.0730 (n 66/267) |
| `FM_10_BTC_UP_EXIT_TREND\|LONG` | 1951 | 126 | 1975 | %1.0 | %0.0 | RULE 443, STOP 125 | 25.95 | 0.0288 | +0.5075/+0.2891 (n 391/241) |
| `FM_CTRL_NO_RSI\|LONG` | 9848 | 126 | 8699 | %0.0 | %0.0 | STOP 1439, TIME 1314, TARGET 755 | 16.05 | 0.0372 | +0.0546/+0.0474 (n 2106/1822) |

## 5. Çoklu test ve tesadüf (§8.4)

- **Holm (54 hücre, α = 0,05, B = 20.000):** reddedilen yok. En küçük sekiz p (dört basamak; eşik 0,05 ÷ (54 − sıra + 1)):

| Sıra | Hücre | p | Holm eşiği |
|---|---|---|---|
| 1 | `FM_01_BTC_UP\|LONG` | 0.0031 | 0.000926 |
| 2 | `FM_05_COST_STOP\|LONG` | 0.0084 | 0.000943 |
| 3 | `FM_00_BASE\|LONG` | 0.0099 | 0.000962 |
| 4 | `FM_03_VOL_OK\|LONG` | 0.0107 | 0.000980 |
| 5 | `C4_04_WIDE_STOP\|LONG` | 0.0138 | 0.001000 |
| 6 | `FM_04_VOL_CONFIRM\|LONG` | 0.0162 | 0.001020 |
| 7 | `FM_09_REGIME_BOTH\|LONG` | 0.0249 | 0.001042 |
| 8 | `FM_02_COIN_UP\|LONG` | 0.0283 | 0.001064 |

- Holm ilk adımda durdu (0,0031 > 0,000926). Hiçbir hücre "ÇOKLU TESTİ GEÇTİ" değil.
- **Beklenti (ön kayıt):** rastgele yürüyüşte 54 hücrede tesadüfen 0–0,3 GÜÇLÜ ADAY; ortak sürüklenmeli kötü durumda en çok ~5
  sıkı aday. Bu koşuda 2 sıkı GÜÇLÜ ADAY çıktı; ikisi aynı kuralın neredeyse aynı işlem kümesidir. Sayı beklenen tesadüf
  aralığının içindedir.
- **Aday oranı:** gerçek %7,41 (54 hücrenin 4'ü: `FM_00`, `FM_05`, `FM_07`, `FM_10`; iki dönemde aralık alt ucu > 0 ve keşif
  ortalaması > 0), plasebo %0. Gerçek > plasebo olduğu için "TESADÜFLE AÇIKLANABİLİR" işareti konmadı.
- **Seçim yanlılığı:** Formasyon kuralı 1.412 hücreden, D4 kuralı en az 38 hücreden seçildi; doğrulama dönemi önceki koşuların
  doğrulamasıyla örtüşüyor ve canlı işlemlerin bir kısmı bu dönemde (ön kayıt §3). Formasyon'un R ölçüsündeki izi bu yüzden
  temiz örneklem dışı kanıt sayılmaz.

## 6. Kapasite filtre tablosu (§8.2)

- Tablo: botun `FiltersCache`'inin sahip tarafından verilen sıkıştırılmış kopyası (`symbol_filters_compact.json`, 40 sembol;
  `min_notional`, `qty_step`, `min_qty`). `verified_at = 2026-10-04T22:08:27+00:00`. sha256
  `f59a3dbed8800f07a30864846cad968511f1b4b5a25fe865703b0a20881c66a4` (kopyanın baytları; VPS'teki `data/symbol_filters.json`
  dosyasının baytları değil). Birincil evrenin 23 sembolünün hepsi tabloda.
- Zamanlama: tablo çalışma alanına 13:09:51 UTC'de geldi. Tam koşu o sırada sürüyordu ve tablosuz başlamıştı (12:47:59); hiçbir
  çıktı henüz yazılmamıştı (ilk çıktı 13:38). Ön kaydın yolu izlendi: kapasite bölümü aynı kodla `--report-only --filters`
  ile yeniden koştu ve deneme olarak kaydedildi (§7). Tablo bir girdidir, kural değildir.
- İki denemede kapasite dışındaki her şey (hükümler, ort.R, plasebo, sade aylık ölçü, Holm p, aday oranı, işlem dosyası özeti
  `35c8d6a1…`) birebir aynı; denetlendi. Tablosuz denemede kapasiteli ölçü "FİLTRE BEKLİYOR" idi ve kapasite dışındaki
  şartları geçen hücreler `FM_00_BASE|LONG` ve `FM_05_COST_STOP|LONG` idi; tabloyla ikisi de 3. şarttan düştü.
- Yaklaşıklık: bugünkü filtreler geçmiş fiyatlara uygulanır (filtrelerin tarihçesi arşivde yoktur).

## 7. Koşu denemeleri (§0.7)

| Deneme | Başlangıç (UTC) | Bitiş (UTC) | Commit | Kod ağacı | Kirli ağaç | Mühür | Durum | Not |
|---|---|---|---|---|---|---|---|---|
| `13a1209454e5` | 2026-10-05 11:53:40 | 11:54:53 | `1f3b19f9b982` | `0504c721cd5acab9` | hayır | `2a3cecc16e0a85c1` | ERROR | eski pencere; `BookDataError: fonlama BTC/USDT: [PRICE_END, 2026-10-02T00:00:00Z) aralığında uzlaşma yok …`; sonuç yok |
| `dcf1fda577a1` | 2026-10-05 12:47:59 | 13:38:30 | `f4b49bafedc2` | `ea36b877ac6fbc97` | hayır | `37dd3f2854c890ad` | OK | tam koşu, birincil evren, D4 + C4 + FM, `--jobs 3`, filtre tablosu yok (3.029,9 s) |
| `09f9fb74c0fa` | 2026-10-05 13:38:54 | 13:40:55 | `f4b49bafedc2` | `ea36b877ac6fbc97` | hayır | `37dd3f2854c890ad` | OK | `--report-only --filters` (118,9 s); bu belgenin kapasite sayıları |

Komut: `python scripts/book_lab.py --cache .../scratchpad/books/cache --out .../scratchpad/books/out --jobs 3` (ikinci denemede
ayrıca `--filters .../scratchpad/books/symbol_filters_compact.json --report-only`). İşlem dosyası sha256 (sıkıştırılmamış CSV
metni): `35c8d6a1a247d1273db8fa7dcd7914644586e6b1ececee6730058ece9f73bf13`. Çıktılar (rapor JSON/MD, işlem dosyası, meta) depoya
konmadı; bulut çalışma alanında `.../scratchpad/books/out` altındadır.

## 8. Veri (§4)

- 23 coinin hepsinin 4h ve 1d serisi `PRICE_END`'e (2026-09-30 00:00) ulaşıyor: son 4h bar 2026-09-29 20:00, son 1d bar
  2026-09-29. Bozuk bar 0. Arşivde verisi olmayan coin yok.
- Dönem başına coin: keşifte 23 coinin 23'ünün en az bir barı var, 21'i dönemi tamamen kapsıyor (ARB 2023-03-23, SUI
  2023-05-03'te başlıyor). Doğrulamada 23 / 23 tam.
- Yedi coinde (SOL, XRP, LTC, NEAR, TRX, FIL, XLM) 2022 ısınma yılında iki arşiv boşluğu var (2022-02-25 → 2022-03-01 ve
  2022-03-31 → 2022-04-03). Karar penceresi (2023-01-01'den) etkilenmez.
- Fonlama: her coinde `PRICE_END` sonrası 3 uzlaşma (2026-09 dosyasından). ARB ve SUI'de listelemeden önceki aylar için dosya
  yok (ARB 2022-12 … 2023-02, SUI 2022-12 … 2023-04); beklenen durum.
- Temiz pencere dışında kalan tespit sayısı ve hücre başına plasebo/`NO_REAL_RISK` sayıları §2–§4'teki bilgi tablolarındadır.

| Coin | 4h ilk → son bar | 4h bar | 4h eksik | 1d ilk → son bar | 1d bar | 1d eksik | 24 saatten uzun boşluk (4h / 1d) | bozuk bar (4h / 1d) | fonlama uzlaşması | PRICE_END sonrası |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-01-01 → 2026-09-29 | 2464 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| ETH | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-01-01 → 2026-09-29 | 2464 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| SOL | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-09-14 → 2026-09-29 | 2202 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| BNB | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-02-10 → 2026-09-29 | 2424 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| XRP | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-01-06 → 2026-09-29 | 2454 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| DOGE | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-07-10 → 2026-09-29 | 2273 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| ADA | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-01-31 → 2026-09-29 | 2434 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| AVAX | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-09-23 → 2026-09-29 | 2198 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| LINK | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-01-17 → 2026-09-29 | 2448 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| LTC | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-01-09 → 2026-09-29 | 2451 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| DOT | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-08-22 → 2026-09-29 | 2230 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| NEAR | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-10-15 → 2026-09-29 | 2171 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| TRX | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-01-15 → 2026-09-29 | 2445 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| BCH | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-01-01 → 2026-09-29 | 2464 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| UNI | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-09-18 → 2026-09-29 | 2203 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| FIL | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-10-16 → 2026-09-29 | 2170 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| XLM | 2022-01-01 00:00 → 2026-09-29 20:00 | 10368 | 30 | 2020-01-20 → 2026-09-29 | 2440 | 5 | 2 / 2 | 0 / 0 | 4200 | 3 |
| AAVE | 2022-01-01 00:00 → 2026-09-29 20:00 | 10398 | 0 | 2020-10-16 → 2026-09-29 | 2175 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| APT | 2022-10-19 00:00 → 2026-09-29 20:00 | 8652 | 0 | 2022-10-19 → 2026-09-29 | 1442 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| ARB | 2023-03-23 12:00 → 2026-09-29 20:00 | 7719 | 0 | 2023-03-23 → 2026-09-29 | 1287 | 0 | 0 / 0 | 0 / 0 | 3863 | 3 |
| OP | 2022-06-01 12:00 → 2026-09-29 20:00 | 9489 | 0 | 2022-06-01 → 2026-09-29 | 1582 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| INJ | 2022-08-17 00:00 → 2026-09-29 20:00 | 9030 | 0 | 2022-08-17 → 2026-09-29 | 1505 | 0 | 0 / 0 | 0 / 0 | 4200 | 3 |
| SUI | 2023-05-03 16:00 → 2026-09-29 20:00 | 7472 | 0 | 2023-05-03 → 2026-09-29 | 1246 | 0 | 0 / 0 | 0 / 0 | 3739 | 3 |

"İlk bar" pencere sınırından (4h 2022-01-01, 1d 2020-01-01) ya da listelemeden sonrakidir. "Eksik" ilk ve son bar arasındaki
beklenen sayıya göredir.

### 8.1 Bayt kanıtı (§4.1, okunuş 35)

Pencere içindeki satırların sabit biçimli CSV metninin sha256'sı, yeni pencereyle (`PRICE_END` 2026-09-30). Ara durum
belgesindeki eski özetler eski pencereye (2026-10-01) aitti; bunlarla karşılaştırılmaz. Fonlama dosyalarının bayt
sha256'ları raporun `funding_files_sha256` alanındadır; burada tekrarlanmadı.

```text
BTC/USDT 1d  f3f3084d241d9edc59ca1af1c40eae95ddbf56791de782df76cdd115d0b22b76
BTC/USDT 4h  daad7731a21a8703cb9ad26ccf243f60ba9c4854a97ff2ef64db8db3125c0a52
ETH/USDT 4h  26965eed2108e956007dc47e01fc226a6cc500e2d209ca7ec06d9507e4dc32a0
ETH/USDT 1d  81f2d2c5be3b220b00e5dc80bb1e96bcfc4802ae55a18d879ab49f5d24bf426f
SOL/USDT 4h  8794e9071fb8a787a80371eb8375db16c5e3c77800d4ba6b353ad9963d2852af
SOL/USDT 1d  17456491c78ce78354e9271540bc628ed1cf6a47bfca86601e9411e3e05fd65a
BNB/USDT 4h  2018eab1154b9fd312c02591d6524deeb1cc368090ebfdcb762b91da6859acde
BNB/USDT 1d  4810df56edbaf25353810ba7707d65daf0f5626f8a7db646d106c121d80fc9fe
XRP/USDT 4h  58b08e87319467695d8e9ba240dc7ccdb313030e19d1db9dd9cd5c9fe159ce3b
XRP/USDT 1d  a81f476317bbf66930c68dd7d4b9b6cf7fe48d0bee2b4378818ff05ef6e84483
DOGE/USDT 4h 62ea76e1b4193f7f2b49133cd2664adbf07eec1be6cb11ae28434332a8892ddc
DOGE/USDT 1d 2f4a357296ea22e664672d912e6d99ff2b93dd44ff87ef4ddb543736eadd0c9c
ADA/USDT 4h  ec9f353ac7ca871e79be11175a8f5f123b32dc4f4da0c1735c34bebc682625c4
ADA/USDT 1d  ab4132b96dc9065a297bb55c96d474b42e686e46b07e2145a54a1785e2c45dba
AVAX/USDT 4h e7bc0ea695b1d30249343877b78954a588ba6c909732226726720a51756a1a55
AVAX/USDT 1d b725c43f4ba43c1128142edb07ed18d76a39876cf48073ab4a77b17260b6d685
LINK/USDT 4h df560222ed8305e9fa4aa4249bc9557296a4b709315498d1da524020c3006cfc
LINK/USDT 1d 9a32599a2b64225fa1d20e5c363e10efe9f2bf502f45155cbea0da76c1e11c14
LTC/USDT 4h  75514300c5457ef9b3351b5544aa5e27a074526fad1b350bfba56e9ac5538d9b
LTC/USDT 1d  55f49a9468bdc13f4d77751d1a6c7e85b19714ca15ba2843354810d47fb2d5e5
DOT/USDT 4h  8015ffae59060d98725c00ba067c3c3f9fa47358a7e73cd3f1e71d75cb07042c
DOT/USDT 1d  de86f3ceb62628f85e225e3b2b69371384e087ae7ef7f082a1ad1b9058164d42
NEAR/USDT 4h 81d01478af1bd1af2e2907fe4ecc58d467bcd3b645dfd0cc1b262e7650891540
NEAR/USDT 1d 94c420162dc11104cd92c1f640cfe9ed8ff1284d0c17eb7e442aedebaaafee91
TRX/USDT 4h  c99e63c5d377b466fe159a6aaaf819e3df65b470cf5cc33c5d74a0978d8abd7b
TRX/USDT 1d  b87a933e15b347d50fc5ab35d4bde2099af976db89317e73a40121b7a4fa7964
BCH/USDT 4h  ae96467a8d3d7c659e390f4fa0e5f7bf3f2513ef7b047372284e8d707a97c5a3
BCH/USDT 1d  e3ce492585dd922a2e87e3e83a37a0168864bb9d3f869f93637de2cdc1c5789b
UNI/USDT 4h  19cc897a2a4333ccb179391e35caf79cb2cf1e49619c669fdf18558f19d29e41
UNI/USDT 1d  2d462575fe0f9e9dd9a781146907e272ed118d578bdbb3238935b19eb58cdc0c
FIL/USDT 4h  73ca2ecbec80e05c9f8a4f847c24d1a7823f9467deff8143a0d556193ecd2018
FIL/USDT 1d  59114f420f7b88acc951c3c4070ad1962c56877ab1e6880da5637135ce447406
XLM/USDT 4h  84216f813f6429a22eb505612769ba4e11e1d1a2070deb30ac56af7d50b982c0
XLM/USDT 1d  79f378d1a256b9b98b18359171a70c326c3e879c2f5970fcde717db3721eb7f6
AAVE/USDT 4h 78c4fac00504319128b0699453ede17cacaf085bd7cd17110e2883a47aeeb0ea
AAVE/USDT 1d 696cf6bb7950ddaac53a747ba3c4a9d5674a469fabfa9f8966afc47a07212cc2
APT/USDT 4h  6ed1066e622c07766158831ad0d2ccc6a9a3c956d4762275bb8424ecb83f805c
APT/USDT 1d  9b3c3d4796dca32e8a63e6f3abdd707aa789ce47178367f2267356a6e6e71610
ARB/USDT 4h  3aa4622f0e6d799c61715fa8d65bed970f63f231269f7fc424f2727e9ab0f1cd
ARB/USDT 1d  55511a20523504ba96c3595d3ce6b1b3372fd83cd63e3b9a0d204754878d8760
OP/USDT 4h   0028fa41ba31e0faecbad62a90ee7ec7d95bb7b95eb051687e7067b1414a4bb1
OP/USDT 1d   fb207ed6fcd8068f3307af573d9f5eb1ca524f967490106a799c12436f17cdf1
INJ/USDT 4h  8542af61ce0cc734c255024457e1c5f766bf221afaecc7b1f22bd01c4054471f
INJ/USDT 1d  e6bded9215a1d65440588f048504c6c4fdf68f1169f02d9a8b8456e321e372b2
SUI/USDT 4h  e834fb4adecdbf2cfca56ad5ccc0f0a369b22f9791f5f25584a18fc9f24aa1bf
SUI/USDT 1d  fa98bc119b0b2148e60e2d1e958f8cbcf0ac90a85770572bce4cadb79c26c790
```

## 9. Ana bot (§9, ön kayıtlı)

- Ana bot arşiv OHLCV ve fonlamasıyla sadakatle yeniden oynatılamaz: öğrenilmiş `p_win`, ekonomik kapı hafızası, benzer
  desen kanıtı, canlı ticker/spread/derinlik ve coin başkanı uzlaşısı arşivde yoktur ya da botun kendi geçmişine bağlıdır.
  Proxy kurulmadı; hiçbir hücre "ana bot" diye adlandırılmadı. Bu koşu ana bot hakkında hüküm vermez.
- Tek hüküm Q1'dir: ön kaydın commit'inden (`3e42da4`, 2026-10-05 06:06 UTC) SONRA açılıp bakış anına kadar kapanan ana bot
  işlemleri. Tek bakış **2027-01-01 00:00 UTC**. Ölçüt: n ≥ 30, gün kümeli %95 aralığın alt ucu > 0 ve aylık net ≥ +%1; aksi
  halde "kanıtlanmadı" (n < 30 ise VERİ AZ; o zaman 2027-04-01'de ikinci ve son bakış, deneme sayısına eklenir).
- Bugüne kadarki 163 işlem (−0,07 R) görüldü; yalnız betimseldir, hükme girmez.
- Q1–Q5'i ölçecek salt okunur VPS betiği `scripts/main_book_audit.py` henüz yazılmadı. Bakıştan önce yazılıp ayrı bir
  incelemeden geçmesi gerekir (§9.3).

## 10. Deneme sayısı

- book_v1: 32 varyant, 54 birincil hücre (D4 13/15, C4 9/27, Formasyon 10/12) + 1 kontrol (deneme sayılmaz). Değişiklik 1 bunu
  değiştirmedi. Hedefi karşılayan hücre 0, öneri adayı 0.
- Ailelerin dürüst toplamı (ön kayıt §13): D4 38 (+ bağlam dilimleriyle en çok 532) + 14; C4 24 + 24; Formasyon 1.412 + 11.
- Hedefi karşılayan hücre olmadığı için PIT teyidi (§8.5) koşulmadı; gerekmiyor. Bilgi evreni (40 coin, hükme girmez) bu
  aşamada koşulmadı; istenirse aynı kodla `--universe info` ile koşulabilir.
- Ana bot: 1 ön kayıtlı VPS hükmü (Q1), 2027-01-01'de.

## 11. Sonra ne olur

- **Ayarlama yok.** Sonuçtan sonra hiçbir kural, eşik, dilim, süzgeç, evren ya da dönem değiştirilmez. Değişiklik book_v2, yeni
  ön kayıt ve yeni deneme sayısı demektir.
- Bu sonuca göre hiçbir defter için değişiklik **önerilmiyor**. D4, C4 ve Formasyon olduğu gibi kalır; kapatma ya da değiştirme
  kararı sahibindir.
- Formasyon'un R ölçüsündeki izi (taban ve `FM_05`) tek başına öneri değildir. Sahip isterse yalnız ileriye dönük veriyle yeni
  bir ön kaydı (book_v2) gerekçelendirebilir: örneğin canlı Formasyon defterinin bundan sonraki işlemleri, önceden yazılmış bir
  ölçütle. Bu koşunun kapasiteli aylık aralığı (21 ay) hedefi kanıtlamaya yetmedi; daha uzun ileriye dönük kayıt gerekir.
- Ana bot için tek hüküm 2027-01-01'deki Q1 bakışıdır.
