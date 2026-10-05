# Defter araştırması — book_v1 sonuçları: ARA DURUM, dondurulmuş koşu bekliyor (2026-10-05)

Ön kayıt: `docs/BOOK_RESEARCH_V1.md` (`3e42da4`). Kod `1f3b19f`, kod ağacı `0504c721cd5acab9`, mühür `BOOK_REGISTRY_SHA =
2a3cecc16e0a85c1`. Salt araştırma; hiçbir defter, strateji, config ya da çalışma zamanı değişmedi.

**Bu belgede hiçbir sonuç yoktur.** Hiçbir sinyal sayısı, R, kazanma oranı ya da aylık % hesaplanmadı. Belge yalnız koşu
denemesini (§0.7) ve veri kapsamını (§0.4) kayda geçirir. Koşu tamamlanınca bu dosya sonuçlarla yeniden yazılır.

**Değişiklik 1 (2026-10-05, hiçbir sonuç görülmeden; ön kayıt §16):** `PRICE_END` 2026-10-01 00:00'dan 2026-09-30 00:00'a,
`FUNDING_END` 2026-10-02 00:00'dan 2026-10-01 00:00'a alındı. Doğrulama artık 2025-01-01 ≤ t < 2026-09-30'dur. Fonlama
dosyaları 2022-12 … 2026-09'dur. `PRICE_END` sonrası uzlaşmalar (2026-09-30 00:00, 08:00, 16:00) yayımlanmış 2026-09
dosyasındadır. Böylece aşağıdaki durma nedeni ortadan kalkar ve koşu 2026-11'i beklemez. Başka hiçbir kural, eşik, evren ya da
ay listesi değişmedi; deneme sayısı aynı. Yeni mühür `BOOK_REGISTRY_SHA = 37dd3f2854c890ad` (eski `2a3cecc16e0a85c1`), yeni kod
ağacı `ea36b877ac6fbc97` (eski `0504c721cd5acab9`). Aşağıdaki §1–§3 ilk denemenin ve eski pencerenin kaydıdır; olduğu gibi
bırakıldı. Yeni plan §7'de.

## 1. Sonuç

**Dondurulmuş koşu bugün hüküm veremez. İlk deneme ön kayıtlı veri kuralıyla durdu.**

- Kural (§4.1, §5.2, okunuş 3 ve 37): `PRICE_END`'e (2026-10-01 00:00 UTC) ulaşan her coinin fonlama serisinde
  [2026-10-01 00:00, 2026-10-02 00:00) aralığında en az bir uzlaşma olmalı. Bu, veri sonunda kapanan (`DATA_END`) işlemlerin
  fonlaması için gerekir. Yoksa koşu durur, rapor yazılmaz, aynı kodla yeniden denenir.
- Bu uzlaşmalar yalnız 2026-10 aylık `fundingRate` dosyasında bulunur. Arşivde günlük fonlama klasörü yoktur. 2026-10 dosyası
  ay bitince yayımlanır; 2026-10-05'te arşivde yoktu.
- Koşu ilk coinde (BTC) durdu. İşlem üretme adımına hiç geçilmedi; rapor, işlem dosyası ve meta yazılmadı.
- Bu bir kod hatası değildir; ön kaydın bilerek koyduğu kapalı-güvenli kuraldır. Kural gevşetilmedi, kod değişmedi.

| Defter | Hüküm (standart / sıkı) | Doğrulama aylık % (düz / kapasiteli) | Hedef (+%1/ay) | Plaseboya göre |
|---|---|---|---|---|
| D4 (13 varyant, 15 hücre) | hesaplanmadı | hesaplanmadı | hüküm yok | hesaplanmadı |
| C4 (9 varyant, 27 hücre) | hesaplanmadı | hesaplanmadı | hüküm yok | hesaplanmadı |
| Formasyon (10 varyant + kontrol, 12 hücre) | hesaplanmadı | hesaplanmadı | hüküm yok | hesaplanmadı |

Dürüst sonuç: bugün hiçbir defter için "ayda +%1 net veriyor mu" sorusunun cevabı yoktur. Canlı PAPER kanıtı (§2) yalnız
motivasyon olarak kalır.

## 2. Koşu denemeleri (§0.7)

| Deneme | Başlangıç (UTC) | Bitiş (UTC) | Commit | Kod ağacı | Kirli ağaç | Mühür | Durum |
|---|---|---|---|---|---|---|---|
| `13a1209454e5` | 2026-10-05 11:53:40 | 2026-10-05 11:54:53 | `1f3b19f9b9822380c9be2c0e91991acc5a93aec5` | `0504c721cd5acab9` | hayır | `2a3cecc16e0a85c1` | ERROR |

Komut: `python scripts/book_lab.py --cache .../scratchpad/books/cache --out .../scratchpad/books/out --jobs 3` (birincil
evren, D4 + C4 + Formasyon, filtre tablosu yok).

Hata iletisi (aynen):

```text
BookDataError: fonlama BTC/USDT: [PRICE_END, 2026-10-02T00:00:00Z) aralığında uzlaşma yok — 2026-10 aylık fundingRate dosyası henüz yayımlanmamış olabilir (arşivde günlük fonlama yok) — koşu ÇALIŞTIRILMADI; aynı kodla yeniden denenir (§0.6)
```

Kanıt (2026-10-05, S3 ad listesi): `data/futures/um/monthly/fundingRate/BTCUSDT/` son dosya `BTCUSDT-fundingRate-2026-09.zip`;
`data/futures/um/daily/` altında `fundingRate` klasörü yok (yalnız `aggTrades`, `bookDepth`, `bookTicker`, `indexPriceKlines`,
`klines`, `markPriceKlines`, `metrics`, `premiumIndexKlines`, `trades`).

## 3. Veri kapsamı ve kalitesi (§0.4: yalnız kapsam ve kalite sayımı, sinyal tespiti yok)

Denemeden sonra, koşucunun veri adımı donmuş kütüphane fonksiyonlarıyla aynen tekrarlandı (`load_klines`, `load_funding`,
`series_quality`, `coverage`, `data_problem`; sinyal, plasebo ya da işlem hesabı yok). Amaç: veri sorununu bugünden görmek ve
önbelleği ısıtmak. Zaman 2026-10-05 11:55 UTC, 4.071 arşiv isteği. Fiyat verisi depoya yüklenmedi.

Not (Değişiklik 1): bu bölüm eski pencereyle (`PRICE_END` 2026-10-01) sayıldı. Aynı sayım yeni pencereyle, önbellekten ve ağsız
tekrarlandı (sinyal yok). Sonuç: 23 coinin hepsinde veri sorunu yok. Her coinde 4h serisi 6 bar, 1d serisi 1 bar kısa (2026-09-30
günü pencere dışında). Son 4h bar 2026-09-29 20:00, son 1d bar 2026-09-29. `PRICE_END` sonrası uzlaşma her coinde 3'tür
(2026-09-30 00:00, 08:00, 16:00; 2026-09 dosyasından). 2026-10 dosyası artık istenmez. Bozuk bar 0. Aşağıdaki tablo ve §3.1
özetleri eski pencereye aittir; yeni koşunun özetleriyle karşılaştırılmaz.

Bulgular:

- 23 coinin hepsinin 4h ve 1d serisi `PRICE_END`'e ulaşıyor (son 4h bar 2026-09-30 20:00, son 1d bar 2026-09-30). Bozuk bar 0.
- Arşivde verisi olmayan coin yok (dışarıda kalan yok).
- Fonlama: 23 coinin hepsinde son uzlaşma 2026-09-30 16:00; `PRICE_END` sonrası uzlaşma 0. Durmanın nedeni yalnız bu. Fiyat
  tarafında koşuyu durduracak sorun yok.
- Dönem başına coin sayısı: keşifte (2023-01 → 2024-12) 23 coinin 23'ünün en az bir barı var, 21'i dönemi tamamen kapsıyor
  (ARB 2023-03-23, SUI 2023-05-03'te başlıyor). Doğrulamada (2025-01 → 2026-09) 23 / 23 tam.
- Yedi coinde (SOL, XRP, LTC, NEAR, TRX, FIL, XLM) arşivde iki boşluk var: 2022-02-25 20:00 → 2022-03-01 (4h'de 76 saat) ve
  2022-03-31 20:00 → 2022-04-03 (52 saat); 1d'de aynı günler (96 ve 72 saat). İkisi de 2022 ısınma yılında. Boşluktan sonra
  pencereler yeniden temizdir: C4'ün 500 4h barlık penceresi 2022-06-25'te, en uzun pencere (D4 1d, 210 gün) 2022-10-29 barında.
  Karar penceresi (2023-01-01'den) etkilenmez.

| Coin | 4h ilk bar | 4h bar | 4h eksik bar | 1d ilk bar | 1d bar | 1d eksik bar | 24 saatten uzun boşluk (4h / 1d) | Bozuk bar | Fonlama uzlaşması (ilk → son) | PRICE_END sonrası |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC | 2022-01-01 00:00 | 10.404 | 0 | 2020-01-01 | 2.465 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| ETH | 2022-01-01 00:00 | 10.404 | 0 | 2020-01-01 | 2.465 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| SOL | 2022-01-01 00:00 | 10.374 | 30 | 2020-09-14 | 2.203 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| BNB | 2022-01-01 00:00 | 10.404 | 0 | 2020-02-10 | 2.425 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| XRP | 2022-01-01 00:00 | 10.374 | 30 | 2020-01-06 | 2.455 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| DOGE | 2022-01-01 00:00 | 10.404 | 0 | 2020-07-10 | 2.274 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| ADA | 2022-01-01 00:00 | 10.404 | 0 | 2020-01-31 | 2.435 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| AVAX | 2022-01-01 00:00 | 10.404 | 0 | 2020-09-23 | 2.199 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| LINK | 2022-01-01 00:00 | 10.404 | 0 | 2020-01-17 | 2.449 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| LTC | 2022-01-01 00:00 | 10.374 | 30 | 2020-01-09 | 2.452 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| DOT | 2022-01-01 00:00 | 10.404 | 0 | 2020-08-22 | 2.231 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| NEAR | 2022-01-01 00:00 | 10.374 | 30 | 2020-10-15 | 2.172 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| TRX | 2022-01-01 00:00 | 10.374 | 30 | 2020-01-15 | 2.446 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| BCH | 2022-01-01 00:00 | 10.404 | 0 | 2020-01-01 | 2.465 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| UNI | 2022-01-01 00:00 | 10.404 | 0 | 2020-09-18 | 2.204 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| FIL | 2022-01-01 00:00 | 10.374 | 30 | 2020-10-16 | 2.171 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| XLM | 2022-01-01 00:00 | 10.374 | 30 | 2020-01-20 | 2.441 | 5 | 2 / 2 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| AAVE | 2022-01-01 00:00 | 10.404 | 0 | 2020-10-16 | 2.176 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| APT | 2022-10-19 00:00 | 8.658 | 0 | 2022-10-19 | 1.443 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| ARB | 2023-03-23 12:00 | 7.725 | 0 | 2023-03-23 | 1.288 | 0 | 0 / 0 | 0 | 3.863 (2023-03-23 08:00 → 2026-09-30 16:00) | 0 |
| OP | 2022-06-01 12:00 | 9.495 | 0 | 2022-06-01 | 1.583 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| INJ | 2022-08-17 00:00 | 9.036 | 0 | 2022-08-17 | 1.506 | 0 | 0 / 0 | 0 | 4.200 (2022-12-01 00:00 → 2026-09-30 16:00) | 0 |
| SUI | 2023-05-03 16:00 | 7.478 | 0 | 2023-05-03 | 1.247 | 0 | 0 / 0 | 0 | 3.739 (2023-05-03 16:00 → 2026-09-30 16:00) | 0 |

"İlk bar" pencere sınırından (4h 2022-01-01, 1d 2020-01-01) ya da listelemeden sonrakidir. "Eksik bar" ilk ve son bar
arasındaki beklenen sayıya göredir.

### 3.1 Bayt kanıtı (§4.1, okunuş 35)

Pencere içindeki satırların sabit biçimli CSV metninin sha256'sı. Yeniden koşu aynı özetleri vermelidir; vermezse fark sonuç
belgesine yazılır.

```text
BTC/USDT 1d  bb22f2cf40f39802dba9cd61cd11ce43a70da8754f82acaa948aa515aafd952a
BTC/USDT 4h  f7e28802e64db189d887cce0deff7634db69fafb4c2d6ceb5735eacf2f974b63
ETH/USDT 4h  380003ddcc6b19e75a8c667b1659cfd66680b7bbd4d4e1151c55853889f6d9eb
ETH/USDT 1d  6b9d5a7bfe16cee9f5e17095c9bde8006f9065800ca03fbc49f1731d78d48bba
SOL/USDT 4h  36b9071c4be32e1b5e7b1627658598c162ce715ebb396a9ac4b8b564afce9843
SOL/USDT 1d  2a4b1a88fe8490aec312f0f827165b9d4b1e81451c476ed49fccd5a42a490b30
BNB/USDT 4h  42e40552223c24a75e9e5ccfcae15233b9324303b051fa81a665613673f03d97
BNB/USDT 1d  6d75806e9e40cac9f170f1c7b22a7f44a3d5d1f8ba73f56f993a7b519be150f0
XRP/USDT 4h  f4a8f3069641f31323c3566f033e38deadeeccca960bd432e6573afcf052da47
XRP/USDT 1d  d81946ddae691c0238545aa3d895dddd42cc76842bdcbd733b350830ef617285
DOGE/USDT 4h c471a193323896e7540388a371a39dd3f308adca4aab9386636bc7096ef5e4c7
DOGE/USDT 1d 98c148cb55b1dbdbfbe0507f4c5d5f5486462cf46bf8c02f346742e7678f06a3
ADA/USDT 4h  5379dd3bc7ea3c3c4fe997cfa8c87172e0e7b0bd3f71bf99dd70d5681c5acddf
ADA/USDT 1d  03447d5c8c9870395ac8ba6dba5565aaad07fb342685ee3a4297995fa0fbb4de
AVAX/USDT 4h 27bc5744e8565fec3dfa7e3ba35b7cceea8d221912c7dd9e7c004b687b1e5c03
AVAX/USDT 1d 2ab533238166d91b3cce98c21866972e3790ff4e3bc5b9dfb6d7367e0a9272ec
LINK/USDT 4h a9c994ffc2fe4ded2bebfdb5c51e4898f2ac6a58c3a392d174ee6dac71b9c335
LINK/USDT 1d 5111393b37329aaa7c9c48042e908044ceb80bb75884643783b086fb0f20322a
LTC/USDT 4h  22f3ee58a04b4fec4cec2c6f3ab29e2d08ef844bf7cc34b9631f6e8654412d86
LTC/USDT 1d  eb8d7fde1aa327013174260f10f0c3ddb91cc56c9dd099f6a66bf42936c03888
DOT/USDT 4h  a33524ad7e473763f9bf1e1698f984f6b8489547e56aae0c482ca2bc35dad9c9
DOT/USDT 1d  460e0037d6a6675635d002fce2e43133a87cf7728960ebc71ea72c9ce6c75196
NEAR/USDT 4h e342bf9f1c4d6a831608817fbf2df596e7ee9f4797bd4de940f5fd98175dc1e7
NEAR/USDT 1d 682ebd3076ce0c939212eb4d14976364971d0a22e71b7c0c0abced84ad11ff3d
TRX/USDT 4h  50f622f973781efdaf7e7e93cc092b6bb112c5736bc1590770d158e326d95ff8
TRX/USDT 1d  da408ae5d5a58d3e1806ab26f2deedbfbe5d379ba92b493ca8f84e6dcfb6b9d1
BCH/USDT 4h  e43b51f9866c53fcead0b6cf32a84381530ce8bc0dbd898ac4a0cdad35ab5e53
BCH/USDT 1d  d51e479ad2609962d597ee99f31789bbd4d911c535f91dde49ef55617a89e0bf
UNI/USDT 4h  ccdfefcfd3ccddc83e6f90957a8bbc38c5c4bd7104fef8ade047fd0484dff1f8
UNI/USDT 1d  a34c23ba15e2ae83426a1e7ae1ab7c9f2c927f6d9f38f994af0d4ab0408f5a43
FIL/USDT 4h  becccebf6e68db489bcff26e9cdabf08bc740e0fcdc971f63390b05d494b5eea
FIL/USDT 1d  6207d1ba2ed87c4114c4ae1a429859e7de10c379d14932318fe87e243e88eed5
XLM/USDT 4h  0808b170c32618e1781eeb701bb3213a8fb3971422b7cb0565400e36f904df50
XLM/USDT 1d  674d31d0a1d43d964dd556de8fbdc1db2316c37e4b93185d181c567c868d5766
AAVE/USDT 4h 8cfbfcbaf8c41a42b83d9926d16c052da4016613f0f126d0990279c677a6a3e2
AAVE/USDT 1d 39ea70a8ad3b721e02d1ec4fb43c7123e38a3b736e9076d5394ab41ecec7d1db
APT/USDT 4h  ca3d9da0063dae945e070898917dfdb82929020c035e297ca6f04a001450f680
APT/USDT 1d  e22ad37e1e83c33b2bf274a2940022efea745360b9a5181d80ba45f15267de5f
ARB/USDT 4h  ff65a55cf6f04d5ea2dc72374c69d0610f4c8e5d3e4385de1521a1b691c11e75
ARB/USDT 1d  a60b2b376b10e9efe0923841565f31b5ba518b297b325bfe88cfae3c93975156
OP/USDT 4h   b03dc93a24a1caf7776a4a56a708bbcd4a1948e10f43bce00ce60fec20c061a4
OP/USDT 1d   1d63fda331e9ab54e09bf884a463414f0c8be17c00c74cd7feacbc9b7f5995b3
INJ/USDT 4h  114eb7a8406dafb237b0d579bac0ebae17db4a6207e446a75c599c4ab143c4be
INJ/USDT 1d  9f6595958f51a61ff6358ceeb87c12c63fcad213125dadf3983e41d58a1a8cdb
SUI/USDT 4h  4a4c3c77a47db2dff4700dd90464ac94fe7a28b3a0f443386ef0e743d0494d83
SUI/USDT 1d  96a4f0b231f3977182c989c9a59acbc7a83ace2a7cb8b9fb59da44585ca030a5
```

## 4. İkinci eksik: kapasite filtre tablosu (§8.2)

Koşu botun kendi filtre önbelleğinin salt okunur kopyasını ister (VPS `data/symbol_filters.json`, `verified_at` ve sha256'sıyla).
Bu ortamda o kopya yok. Fonlama kapısı açılsa bile tablo olmadan kapasiteli ölçü ve §8.3 hedef hükmü "FİLTRE BEKLİYOR" olur.
Tablo sonradan gelirse kapasite bölümü aynı kodla `--report-only --filters` ile yeniden koşar (deneme olarak kaydedilir).

## 5. Çoklu test ve deneme sayısı

- Ön kayıtlı deneme sayısı değişmedi: 32 varyant, 54 birincil hücre, 1 kontrol (§13).
- Bu deneme hiçbir hücre hesaplamadı; yeniden deneme yeni deneme değildir (§0.6: veri hatası aynı kodla yeniden denenir).
- §3'teki sayım sinyal saymaz; ön kaydın izin verdiği kapsam ve kalite sayımıdır (§0.4).
- Değişiklik 1 (ön kayıt §16) deneme sayısını değiştirmez. Hiçbir sonuç görülmeden yapıldı; durmuş deneme `13a1209454e5` kayıtta
  ERROR olarak kalır, sonraki denemeler yeni mühürle (`37dd3f2854c890ad`) kaydedilir.

## 6. Ana bot (§9, ön kayıtlı)

- Ana bot arşiv OHLCV ve fonlamasıyla sadakatle yeniden oynatılamaz (öğrenilmiş `p_win`, ekonomik kapı hafızası, canlı
  ticker/derinlik, coin başkanı uzlaşısı arşivde yok). Proxy kurulmadı; hiçbir hücre "ana bot" diye adlandırılmadı.
- Tek hüküm Q1'dir: ön kaydın commit'inden (`3e42da4`) SONRA açılıp kapanan ana bot işlemleri, tek bakış
  **2027-01-01 00:00 UTC**; ölçüt n ≥ 30, gün kümeli %95 aralığın alt ucu > 0 ve aylık net ≥ +%1.
- Bugüne kadarki 163 işlem (−0,07 R) görüldü; yalnız betimseldir, hükme girmez.
- Q1–Q5'i ölçecek salt okunur VPS betiği `scripts/main_book_audit.py` henüz yazılmadı. Bakıştan önce yazılıp ayrı bir
  incelemeden geçmesi gerekir (§9.3).

## 7. Yeniden deneme

Değişiklik 1'den sonraki plan:

1. Koşu bugün yapılabilir; 2026-10 fonlama dosyası beklenmez. Gereken bütün dosyalar (fiyat 2026-09'a kadar, fonlama
   2022-12 … 2026-09) yayımlanmış ve önbellekte.
2. Değişiklik 1 commit'inin temiz kod ağacıyla (`ea36b877ac6fbc97`), aynı komut ve aynı `--out` (deneme kaydı sürer; o dizinde
   önceki koşunun meta dosyası yok, mühür çakışması olmaz). Sahibin filtre tablosu varsa `--filters` ile verilir; yoksa
   kapasiteli ölçü ve hedef hükmü "FİLTRE BEKLİYOR" olur (§4).
3. Kural, eşik, dönem ya da evren başka değişmez. Seri özetleri yeni pencereye göre yeniden yazılır (§3.1'dekiler eski
   penceredir).

Eski plan (Değişiklik 1'den önce, artık geçersiz):

1. 23 coinin `<SEMBOL>-fundingRate-2026-10.zip` dosyası arşive gelince (2026-11 başı beklenir). Denetim: S3 listesi
   `data/futures/um/monthly/fundingRate/<SEMBOL>/`.
2. Mümkünse önce VPS filtre tablosunun salt okunur kopyası alınır (§4).
3. Aynı kod ağacıyla (`0504c721cd5acab9`; yalnız belge değiştiren commit'ler bunu değiştirmez), aynı komut ve aynı `--out`
   (deneme kaydı sürer). Kural, eşik, dönem ya da evren değişmez.
4. İndirilen serilerin özetleri §3.1 ile karşılaştırılır. Bir coinin 2026-10 fonlama dosyası hâlâ yoksa koşu yine durur ve
   aynı kodla yeniden denenir.
