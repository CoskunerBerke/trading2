# DENEY V9 — sadeleştirme: tek kurallı trend, botun kendi defterinde

Protokol: `PROTOCOL_V9.md` (sonuçlar görülmeden yazıldı). Strateji modu: uzman yığını yok; defter, risk motoru,
borsa filtreleri, kayma ve arşiv funding üretimle aynı. B0 ve R1 satırları önceki koşulardan.

## 1. Sonuçlar

| pencere | kol | işlem | hesap getirisi | stresli | ort net R | isabet | PF | maks düşüş | maruziyet (gün) | ort bar | ücret | funding |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | B0 eski üretim (tetik + mum vetosu) | 496 | +87,24% | +71,20% | +0,0817 | %38,1 | 1,204 | 51,6% | %99 | 35.18 | 10.0 | -23.0 |
| P1 2020-11→2022-08 | R1 şu anki üretim (+ rejim kapısı) | 261 | +140,83% | +132,39% | +0,2756 | %36,8 | 1,634 | 45,1% | %54 | 30.33 | 5.2 | -25.8 |
| P1 2020-11→2022-08 | T1 saf EMA200 trend | 34 | +96,72% | +96,05% | +1,4465 | %26,5 | 6,961 | 8,0% | %67 | 198.47 | 0.4 | -65.8 |
| P1 2020-11→2022-08 | T2 trend + BTC rejimi | 21 | +107,34% | +106,83% | +2,5898 | %38,1 | 15,451 | 4,5% | %61 | 300.24 | 0.3 | -66.4 |
| P1 2020-11→2022-08 | T3 trend + botun çıkışı | 47 | +107,89% | +106,97% | +1,1603 | %21,3 | 5,967 | 9,7% | %67 | 196.68 | 0.6 | -81.0 |
| P2 2022-09→2024-08 | B0 eski üretim (tetik + mum vetosu) | 229 | -78,69% | -86,91% | -0,1996 | %28,8 | 0,615 | 80,7% | %97 | 35.88 | 5.1 | -2.0 |
| P2 2022-09→2024-08 | R1 şu anki üretim (+ rejim kapısı) | 279 | -28,41% | -39,47% | -0,0514 | %29,0 | 0,874 | 48,2% | %71 | 31.79 | 7.0 | -8.0 |
| P2 2022-09→2024-08 | T1 saf EMA200 trend | 74 | +16,22% | +14,77% | +0,2117 | %13,5 | 1,399 | 32,6% | %84 | 123.51 | 0.9 | -9.6 |
| P2 2022-09→2024-08 | T2 trend + BTC rejimi | 56 | +33,74% | +32,56% | +0,3849 | %12,5 | 2,078 | 22,3% | %74 | 145.46 | 0.7 | -10.1 |
| P2 2022-09→2024-08 | T3 trend + botun çıkışı | 117 | -11,13% | -13,51% | +0,0066 | %9,4 | 0,815 | 46,6% | %84 | 91.31 | 1.4 | -9.7 |
| P3 2024-09→2026-08 | B0 eski üretim (tetik + mum vetosu) | 200 | -57,84% | -64,90% | -0,1889 | %28,5 | 0,696 | 71,5% | %100 | 43.31 | 4.5 | -2.0 |
| P3 2024-09→2026-08 | R1 şu anki üretim (+ rejim kapısı) | 199 | -39,81% | -47,32% | -0,1037 | %24,1 | 0,777 | 55,4% | %55 | 30.28 | 4.8 | -4.1 |
| P3 2024-09→2026-08 | T1 saf EMA200 trend | 40 | +2,61% | +1,81% | +0,0456 | %17,5 | 1,151 | 9,2% | %67 | 166.07 | 0.5 | -2.3 |
| P3 2024-09→2026-08 | T2 trend + BTC rejimi | 29 | +10,15% | +9,60% | +0,1898 | %20,7 | 1,726 | 10,0% | %59 | 208.21 | 0.3 | -2.6 |
| P3 2024-09→2026-08 | T3 trend + botun çıkışı | 70 | +38,70% | +37,11% | +0,2763 | %14,3 | 2,330 | 11,7% | %67 | 126.09 | 1.0 | -5.9 |

## 2. Kabul ölçütleri (§4; üç pencerede de)

| kol | P1 (1/2/3/4) | P2 | P3 | karar |
|---|---|---|---|---|
| T1 saf EMA200 trend | ✓ ✗ ✓ ✓ | ✓ ✓ ✓ ✓ | ✓ ✓ ✓ ✓ | **bazı pencerelerde olumlu, DOĞRULANMADI** |
| T2 trend + BTC rejimi | ✓ ✗ ✓ ✓ | ✓ ✓ ✓ ✓ | ✓ ✓ ✓ ✓ | **bazı pencerelerde olumlu, DOĞRULANMADI** |
| T3 trend + botun çıkışı | ✓ ✗ ✓ ✓ | ✗ ✓ ✓ ✗ | ✓ ✓ ✓ ✓ | **bazı pencerelerde olumlu, DOĞRULANMADI** |

## 3. Çıkış nedenleri ve retler

| koşu | çıkışlar | retler (ilk 4) |
|---|---|---|
| v9_t1_p1 | EMA200_CROSS_DOWN=33, stop=1 | TOTAL_OPEN_RISK=7788, MIN_NOTIONAL=1178, MIN_ORDER_CONFLICT=1102, STEP_ZERO_QTY=1018 |
| v9_t2_p1 | EMA200_CROSS_DOWN=21 | TOTAL_OPEN_RISK=7679, MIN_NOTIONAL=754, STEP_ZERO_QTY=517, MIN_ORDER_CONFLICT=119 |
| v9_t3_p1 | EMA200_CROSS_DOWN=37, başa-baş stop=9, stop=1 | STEP_ZERO_QTY=3240, MIN_NOTIONAL=2923, MIN_ORDER_CONFLICT=1163, TOTAL_OPEN_RISK=844 |
| v9_t1_p2 | EMA200_CROSS_DOWN=67, stop=7 | TOTAL_OPEN_RISK=9117, MIN_NOTIONAL=1792, STEP_ZERO_QTY=1028, MIN_ORDER_CONFLICT=19 |
| v9_t2_p2 | EMA200_CROSS_DOWN=52, stop=4 | TOTAL_OPEN_RISK=9779, MIN_NOTIONAL=1243, STEP_ZERO_QTY=893, MAX_POSITION_PCT=13 |
| v9_t3_p2 | EMA200_CROSS_DOWN=83, başa-baş stop=22, stop=12 | STEP_ZERO_QTY=3930, MIN_NOTIONAL=3875, INSUFFICIENT_MARGIN=1459, TOTAL_OPEN_RISK=1052 |
| v9_t1_p3 | EMA200_CROSS_DOWN=37, stop=3 | TOTAL_OPEN_RISK=9180, STEP_ZERO_QTY=861, MIN_NOTIONAL=487, MAX_POSITION_PCT=25 |
| v9_t2_p3 | EMA200_CROSS_DOWN=26, stop=3 | TOTAL_OPEN_RISK=8898, STEP_ZERO_QTY=1166, MIN_NOTIONAL=518, MIN_ORDER_CONFLICT=37 |
| v9_t3_p3 | EMA200_CROSS_DOWN=49, başa-baş stop=17, stop=4 | STEP_ZERO_QTY=4233, MIN_NOTIONAL=2741, TOTAL_OPEN_RISK=1070, MAX_POSITION_PCT=180 |

## 4. Belirsizlik ve bileşik sonuç (sonuçlar görüldükten sonra hesaplandı)

| kol | ort R %95 (P1) | (P2) | (P3) | üç pencere bileşik | en kötü pencere düşüşü |
|---|---|---|---|---|---|
| R1 şu anki üretim | [+0,13, +0,43] | [-0,19, +0,10] | [-0,26, +0,06] | +4% | 55,4% |
| T1 saf trend | [-0,18, +4,13] | [-0,31, +1,06] | [-0,24, +0,42] | +135% | 32,6% |
| T2 trend + rejim | [-0,03, +6,70] | [-0,31, +1,65] | [-0,26, +0,77] | +205% | 22,3% |
| T3 trend + botun çıkışı | [+0,05, +2,74] | [-0,31, +0,55] | [-0,18, +1,05] | +156% | 46,6% |

## 5. Yorum (sonuçlar görüldükten sonra yazıldı)

**Harfiyen karar:** üç kol da "bazı pencerelerde olumlu, DOĞRULANMADI". Düşen tek şart, P1'de (boğa
penceresi) şu anki üretimi geçmek: R1 +140,8%, T1 +96,7%, T2 +107,3%. Diğer her şart üç pencerede sağlandı.

**Kâr sorusuna cevap:** T1 ve T2, botun kendi sistemi içinde üç pencerede de maliyet ve funding sonrası
POZİTİF olan ilk kurallar. Şu anki üretim (R1) boğa penceresinde daha çok kazanıyor, ama sonraki iki
pencerede −28% ve −40% yapıyor; T2 aynı pencerelerde +34% ve +10%. Üç pencere bileşik: R1 yaklaşık +4%,
T2 yaklaşık +200%; T2'nin en kötü pencere düşüşü %22, R1'in %55. Kâra en yakın şey, bu.

**Neden çalışıyor:** işlem sayısı az (21–76 / pencere), tutma uzun (120–300 bar ≈ 20–50 gün), isabet
düşük (%13–38) ama kazananlar büyük (PF 1,4–15). Bu, kullanıcının ilk tablosundaki "pozisyon / trend
ticareti" stili; botun 35 barlık swing tarzının tersi. Funding bu uzun tutmalarda ciddi (P1'de −66 USDT)
ve dahil edildi.

**T3 (trend girişi + botun başa-baş koruması):** P2'de −11%: botun çıkış koruması trend işlemlerini
erken kesiyor (22 başa-baş stop). Botun çıkış yönetimi, trend stratejisine ZARAR veriyor.

**Sınırlar, açıkça:** işlem sayısı küçük ve %95 aralıkları geniş; büyük kazançlar az sayıda işlemden
geliyor (trend takibinin doğası, ama örneklem hassasiyeti demek). Evren 2026 seçimi (lehte). 100 USDT
hesapta BTC açılamıyor ve toplam risk tavanı çoğu sinyali reddediyor (TOTAL_OPEN_RISK ~8–9 bin ret):
strateji, hesabın taşıyabildiği kadarını aldı. Tek EMA200, ızgara yok.

**Sonraki adım (protokol §4 gereği ayrı karar):** T2'yi VPS'te PAPER modda, AYRI bir defterle, mevcut
botun yanında canlı ileri teste almak. Bu üretim motoruna "strateji modu" gerektirir (replay'deki ile
aynı kanca); ölçülmeden hiçbir şey gerçek paraya yaklaşmaz.
