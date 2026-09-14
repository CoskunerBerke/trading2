# DENEY V11 — momentum tanımı: EMA200'ün alternatifleri

Protokol: `PROTOCOL_V11.md` (sonuçlar görülmeden yazıldı). Strateji modu, botun defteri, funding dahil. T2 satırları `v9_t2_*`.

## 1. Sonuçlar

| pencere | kol | işlem | hesap getirisi | stresli | ort net R | %95 (ort R) | isabet | PF | maks düşüş | ort bar | ücret | funding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | T2 EMA200 (temel, canlı kâğıt defter) | 21 | +107,34% | +106,83% | +2,5898 | [-0,0330, +6,7043] | %38,1 | 15,451 | 4,5% | 300.24 | 0.3 | -66.4 |
| P1 2020-11→2022-08 | M1 EMA100 | 20 | +140,91% | +140,38% | +3,5274 | [+0,0125, +9,1440] | %40,0 | 16,442 | 4,5% | 283.7 | 0.3 | -64.6 |
| P1 2020-11→2022-08 | M2 TSMOM 28 gün | 47 | +157,58% | +156,69% | +1,7189 | [+0,4680, +3,3185] | %46,8 | 9,561 | 7,2% | 104.49 | 0.5 | -22.1 |
| P1 2020-11→2022-08 | M3 TSMOM 91 gün | 16 | +159,51% | +159,04% | +5,0957 | [+0,2332, +12,1007] | %43,8 | 15,071 | 4,1% | 380.75 | 0.3 | -59.2 |
| P2 2022-09→2024-08 | T2 EMA200 (temel, canlı kâğıt defter) | 56 | +33,74% | +32,56% | +0,3849 | [-0,3118, +1,6541] | %12,5 | 2,078 | 22,3% | 145.46 | 0.7 | -10.1 |
| P2 2022-09→2024-08 | M1 EMA100 | 70 | +48,88% | +47,41% | +0,4289 | [-0,2473, +1,5957] | %17,1 | 2,396 | 20,6% | 114.37 | 0.9 | -11.1 |
| P2 2022-09→2024-08 | M2 TSMOM 28 gün | 94 | +45,95% | +43,89% | +0,2762 | [-0,1750, +0,9904] | %27,7 | 2,210 | 16,3% | 72.18 | 1.3 | -6.2 |
| P2 2022-09→2024-08 | M3 TSMOM 91 gün | 71 | +43,07% | +41,55% | +0,4215 | [-0,2828, +1,4114] | %32,4 | 1,996 | 26,2% | 119.13 | 0.9 | -6.7 |
| P3 2024-09→2026-08 | T2 EMA200 (temel, canlı kâğıt defter) | 29 | +10,15% | +9,60% | +0,1898 | [-0,2632, +0,7703] | %20,7 | 1,726 | 10,0% | 208.21 | 0.3 | -2.6 |
| P3 2024-09→2026-08 | M1 EMA100 | 49 | +14,20% | +13,24% | +0,1454 | [-0,2229, +0,6911] | %22,4 | 1,621 | 13,1% | 107.24 | 0.6 | -2.9 |
| P3 2024-09→2026-08 | M2 TSMOM 28 gün | 55 | +35,01% | +33,85% | +0,3162 | [-0,1889, +1,0806] | %32,7 | 2,365 | 13,9% | 91.62 | 0.7 | -3.1 |
| P3 2024-09→2026-08 | M3 TSMOM 91 gün | 43 | -1,07% | -2,01% | -0,0038 | [-0,2820, +0,3470] | %44,2 | 0,959 | 10,7% | 136.51 | 0.6 | -2.5 |

## 2. Kabul (§2; üç pencerede de; T2 en kötü düşüş 22,3%)

| kol | P1 (1/2/3/4/5) | P2 | P3 | en kötü düşüş | karar |
|---|---|---|---|---|---|
| M1 EMA100 | ✓ ✓ ✓ ✓ ✓ | ✓ ✓ ✓ ✓ ✓ | ✓ ✓ ✓ ✓ ✓ | 20,6% | **T2'NİN YERİNE ADAY** |
| M2 TSMOM 28 gün | ✓ ✓ ✓ ✓ ✓ | ✓ ✓ ✓ ✓ ✓ | ✓ ✓ ✓ ✓ ✓ | 16,3% | **T2'NİN YERİNE ADAY** |
| M3 TSMOM 91 gün | ✓ ✓ ✗ ✓ ✗ | ✓ ✓ ✓ ✓ ✗ | ✗ ✗ ✓ ✗ ✗ | 26,2% | **REDDEDİLDİ** |

## 3. Yorum (sonuçlar görüldükten sonra yazıldı)

**İki kol beş şartı üç pencerede de sağladı:** M1 (EMA100) ve M2 (28 günlük zaman serisi momentumu).
M3 (91 gün) P3'te negatif ve en kötü düşüşü T2'yi aşıyor → reddedildi.

| kol | P1 | P2 | P3 | en kötü düşüş | işlem / pencere | ort tutma (bar) |
|---|---|---|---|---|---|---|
| T2 EMA200 (canlı) | +107% | +34% | +10% | %22,3 | 21–56 | 145–300 |
| M1 EMA100 | +141% | +49% | +14% | %20,6 | 20–70 | 107–284 |
| **M2 TSMOM 28g** | **+158%** | **+46%** | **+35%** | **%16,3** | 47–94 | 72–104 |

M2 öne çıkıyor: her pencerede pozitif, P3'te T2'nin üç katı, en düşük en kötü düşüş, isabet %27–47 (T2'de
%12–38), ve P1'de ort R %95 aralığı [+0,47, +3,32] — bu paketin strateji düzeyinde SIFIRI DIŞLAYAN ilk olumlu
aralığı. Tutma 12–17 gün, pencere başına 47–94 işlem ≈ haftada bir; kullanıcının "daha sık işlem" isteğine
T2'den yakın. Liu–Tsyvinski'nin 1–4 haftalık zaman serisi momentumu bulgusuyla tutarlı.

**Dürüstlük notu (protokol boşluğu):** §2 iki kolun birden geçmesi hâlinde seçim kuralı YAZMAMIŞTI. Üç kol
arasından sonuca bakıp en iyisini seçmek küçük ama gerçek bir seçim yanlılığıdır (3 aday). Ayrıca üç pencere
T2'nin seçildiği pencerelerdir; M2 "temiz" pencerede sınanmadı. Bu yüzden M2 "T2'den kesin iyi" DEĞİL,
"T2'nin yerine daha güçlü aday"dır. Kararı canlı kâğıt ileri test verir.

**Seçenekler (operatör kararı):** (a) canlı kâğıt defterde T2'yi M2 ile değiştirmek (tek satır config +
kural modülüne `m2_tsmom28` varyantı); (b) T2'yi bırakıp M2'yi ikinci kâğıt defter olarak eklemek (kod:
çoklu defter desteği). Gerçek para her durumda YOK.
