# DENEY V13R — T2 ve M2'yi kırma denemesi (PROTOCOL_V13R bayrakları)

Kod `3501304`; yalnız araştırma paketi. Getiriler maliyet sonrası hesap getirisi (başlangıç 100 USDT),
üretim defteri kuralları (2% risk, %6 tavan, gerçek borsa filtreleri, funding). Tabanlar: `v9_t2_*`, `v11_m2_*`.

## R1 — coin çıkarma (LOO): tek coin yeni giriş açamaz

### T2 EMA200

| pencere | taban | −BTC | −ETH | −SOL | −BNB | −XRP | −LINK | −DOGE | −AVAX | −LTC | −AAVE | en düşük | F1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | +107.3% | +107.3% | +107.3% | +83.4% | +65.3% | +135.9% | +107.3% | +109.3% | +107.3% | +118.2% | +107.3% | +65.3% (−BNB) | hayır |
| P2 2022-09→2024-08 | +33.7% | +33.7% | +38.9% | +2.8% | +20.0% | +31.2% | +33.7% | +32.8% | +33.7% | +37.3% | +35.6% | +2.8% (−SOL) | hayır · yoğun |
| P3 2024-09→2026-08 | +10.2% | +10.2% | +10.1% | +50.6% | +43.7% | +15.3% | +10.2% | +39.9% | +10.2% | +10.2% | +10.2% | +10.1% (−ETH) | hayır |

### M2 TSMOM 28g

| pencere | taban | −BTC | −ETH | −SOL | −BNB | −XRP | −LINK | −DOGE | −AVAX | −LTC | −AAVE | en düşük | F1 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | +157.6% | +157.6% | +153.0% | +133.2% | +84.6% | +131.6% | +157.6% | +124.4% | +157.6% | +137.5% | +157.6% | +84.6% (−BNB) | hayır |
| P2 2022-09→2024-08 | +46.0% | +46.0% | +47.8% | +15.5% | +57.9% | +76.1% | +46.0% | +53.6% | +46.0% | +46.2% | +46.8% | +15.5% (−SOL) | hayır · yoğun |
| P3 2024-09→2026-08 | +35.0% | +35.0% | +35.0% | +82.5% | +71.9% | +37.2% | +35.0% | +58.8% | +35.0% | +36.8% | +35.0% | +35.0% (−BTC) | hayır |

## R2 — komşu parametreler

| pencere | M2 28g (taban) | M2 21g | M2 42g | F2 | T2 üretim | T2 kontrol (araştırma EMA200) | EMA150 | EMA250 | F2 |
|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | +157.6% | +118.7% | +121.9% | hayır | +107.3% | +107.3% | +139.6% | +102.7% | hayır |
| P2 2022-09→2024-08 | +46.0% | +16.6% | +25.6% | hayır | +33.7% | +30.1% | +46.4% | -15.0% | **EVET** |
| P3 2024-09→2026-08 | +35.0% | +94.2% | +22.7% | hayır | +10.2% | +11.3% | +15.6% | +6.0% | hayır |

Kontrol ile üretim T2 farkı, EMA hesap yönteminden gelir (üretim: gösterge sütunu; araştırma: kapanışlardan). 
T2 komşuları KONTROL ile karşılaştırılır.

## R3 — pencere kaydırma (+3 ay)

| pencere | T2 taban | T2 kaydırılmış | F3 | M2 taban | M2 kaydırılmış | F3 |
|---|---|---|---|---|---|---|
| P1 → 2021-02→2022-11 | +107.3% | +57.2% | hayır | +157.6% | +104.6% | hayır |
| P2 → 2022-12→2024-11 | +33.7% | +31.0% | hayır | +46.0% | +42.5% | hayır |
| P3 → 2024-12→2026-08 | +10.2% | -4.2% | **EVET** | +35.0% | +2.5% | hayır |

## R4 — yoğunlaşma (mevcut taban kayıtlarından)

| pencere | kural | işlem | en iyi coin (net payı) | en iyi 3 işlem / brüt kâr | F4 |
|---|---|---|---|---|---|
| P1 2020-11→2022-08 | T2 EMA200 | 21 | BNB (+69.7 USDT, net'in 65%) | 97% | **EVET** |
| P2 2022-09→2024-08 | T2 EMA200 | 56 | SOL (+40.8 USDT, net'in 121%) | 94% | **EVET** |
| P3 2024-09→2026-08 | T2 EMA200 | 29 | DOGE (+12.7 USDT, net'in 125%) | 94% | **EVET** |
| P1 2020-11→2022-08 | M2 TSMOM 28g | 47 | BNB (+65.8 USDT, net'in 42%) | 65% | **EVET** |
| P2 2022-09→2024-08 | M2 TSMOM 28g | 94 | SOL (+50.6 USDT, net'in 110%) | 85% | **EVET** |
| P3 2024-09→2026-08 | M2 TSMOM 28g | 55 | DOGE (+26.1 USDT, net'in 74%) | 83% | **EVET** |

**T2–M2 aylık net korelasyonu (birleşik defter çeşitlendirmesi):**

* P1 2020-11→2022-08: r = 0.36 (15 ay)
* P2 2022-09→2024-08: r = -0.03 (20 ay)
* P3 2024-09→2026-08: r = -0.15 (17 ay)

## Karar (PROTOCOL_V13R §2, mekanik)

| kural | F1 pencere | F2 pencere | F3 pencere | hüküm |
|---|---|---|---|---|
| T2 EMA200 | 0 | 1 | 1 | kırılamadı (kanıt değil, güven artışı) |
| M2 TSMOM 28g | 0 | 0 | 0 | kırılamadı (kanıt değil, güven artışı) |

Hayatta kalma yanlılığı bu turda ölçülmedi (V14, VPS verisi). VPS'te değişiklik yok.
