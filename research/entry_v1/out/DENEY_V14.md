# DENEY V14 — hayatta kalma yanlılığı: Ekim-2020 evreni (BTC ETH LINK YFI BCH UNI BNB LTC XRP DOT)

Kod `3501304`; yalnız araştırma paketi; ayrı önbellek (`wt-2020/data`). Aynı kural, aynı defter kuralları,
aynı üç pencere; tek fark işlem evreni. Bugünkü evren tabanları: `v9_t2_*`, `v11_m2_*`.

| pencere | kural | bugünkü evren | 2020 evreni | işlem (2020) | coin (2020) | maks düşüş (2020) | S1 | S2 | S3 |
|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | T2 EMA200 | +107.3% | +89.4% | 20 | 5 | 2.3193% | hayır | hayır | hayır |
| P2 2022-09→2024-08 | T2 EMA200 | +33.7% | -11.9% | 48 | 6 | 18.7164% | **EVET** | hayır | hayır |
| P3 2024-09→2026-08 | T2 EMA200 | +10.2% | +35.2% | 27 | 7 | 8.4382% | hayır | hayır | hayır |
| P1 2020-11→2022-08 | M2 TSMOM 28g | +157.6% | +101.0% | 44 | 6 | 4.7452% | hayır | hayır | hayır |
| P2 2022-09→2024-08 | M2 TSMOM 28g | +46.0% | +21.1% | 114 | 8 | 17.6469% | hayır | **EVET** | hayır |
| P3 2024-09→2026-08 | M2 TSMOM 28g | +35.0% | +9.6% | 71 | 8 | 11.3793% | hayır | **EVET** | hayır |

## Coin başına net (2020 evreni)

* t2_p1: BNB +69.7, LTC +20.7, DOT +1.3, XRP -0.5, BCH -1.8
* t2_p2: ETH +1.9, DOT -1.8, YFI -1.9, XRP -2.0, UNI -3.0, BNB -5.1
* t2_p3: XRP +36.3, BNB +6.0, BCH +0.3, YFI -0.9, LTC -1.5, ETH -2.0, UNI -3.0
* m2_p1: BNB +65.7, XRP +17.6, LTC +14.4, ETH +2.9, DOT +0.7, BCH -0.2
* m2_p2: BCH +15.1, BNB +12.4, UNI +6.8, YFI +3.5, DOT -1.0, XRP -4.6, LTC -4.8, ETH -6.4
* m2_p3: BNB +14.2, UNI +3.7, YFI +0.5, ETH -0.4, LTC -1.1, XRP -1.7, DOT -2.5, BCH -3.0

## Karar (PROTOCOL_V14 §3, mekanik)

| kural | S1 pencere | S2 pencere | hüküm |
|---|---|---|---|
| T2 EMA200 | 1 | 0 | beklenti aşağı revize, ileri test sürer |
| M2 TSMOM 28g | 0 | 2 | beklenti aşağı revize, ileri test sürer |

VPS'te değişiklik yok. Evren hâlâ 'bugün listede olan' coinlerden oluşuyor (delist edilmiş coin verisi yok).
