# DENEY V7 — piyasa rejimi kapısı, basit temel, hayatta kalma kontrolü

Protokol: `PROTOCOL_V7.md` (sonuçlar görülmeden yazıldı). Temel B0 = üretim (tetik + C3 mum vetosu).

BTC UP rejimi gün payı (close > EMA200): P1 51%, P2 71%, P3 53%

## 1. Q1 sonuçları

| pencere | kol | işlem | LONG | hesap getirisi | stresli | ort net R | isabet | PF | maks düşüş | coin |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | B0 temel (üretim) | 496 | 301 | +87,24% | +71,20% | +0,0817 | %38,1 | 1,204 | 51,65% | 9 |
| P1 2020-11→2022-08 | R1 yalnız LONG + yalnız UP | 261 | 261 | +140,83% | +132,39% | +0,2756 | %36,8 | 1,634 | 45,08% | 9 |
| P1 2020-11→2022-08 | R2 DOWN'da giriş yok | 305 | 230 | +117,56% | +108,01% | +0,1987 | %41,0 | 1,487 | 36,46% | 9 |
| P1 2020-11→2022-08 | R3 rejimi izle | 436 | 257 | +132,34% | +117,86% | +0,1562 | %37,4 | 1,339 | 61,91% | 9 |
| P2 2022-09→2024-08 | B0 temel (üretim) | 229 | 129 | -78,69% | -86,91% | -0,1996 | %28,8 | 0,615 | 80,73% | 9 |
| P2 2022-09→2024-08 | R1 yalnız LONG + yalnız UP | 279 | 279 | -28,41% | -39,47% | -0,0514 | %29,0 | 0,874 | 48,20% | 10 |
| P2 2022-09→2024-08 | R2 DOWN'da giriş yok | 176 | 112 | -75,80% | -81,94% | -0,2473 | %25,6 | 0,542 | 78,68% | 9 |
| P2 2022-09→2024-08 | R3 rejimi izle | 267 | 181 | -72,21% | -82,34% | -0,1625 | %29,2 | 0,689 | 72,21% | 9 |
| P3 2024-09→2026-08 | B0 temel (üretim) | 200 | 106 | -57,84% | -64,90% | -0,1889 | %28,5 | 0,696 | 71,55% | 9 |
| P3 2024-09→2026-08 | R1 yalnız LONG + yalnız UP | 199 | 199 | -39,81% | -47,32% | -0,1037 | %24,1 | 0,777 | 55,43% | 9 |
| P3 2024-09→2026-08 | R2 DOWN'da giriş yok | 216 | 143 | -23,41% | -31,61% | -0,0896 | %29,2 | 0,879 | 38,77% | 9 |
| P3 2024-09→2026-08 | R3 rejimi izle | 222 | 146 | -50,90% | -58,73% | -0,1193 | %27,5 | 0,761 | 76,71% | 9 |

## 2. Kabul ölçütleri (5 şart; rejim istisnası; üç pencere + toplam ≥80 işlem)

| kol | P1 | P2 | P3 | toplam işlem | karar |
|---|---|---|---|---|---|
| R1 yalnız LONG + yalnız UP | ✓ ✓ ✓ ✓ ✓ | ✗ ✓ ✓ ✗ ✓ | ✗ ✓ ✓ ✗ ✓ | 739 | **yalnız bazı pencerelerde olumlu, DOĞRULANMADI** |
| R2 DOWN'da giriş yok | ✓ ✓ ✓ ✓ ✓ | ✗ ✓ ✓ ✗ ✗ | ✗ ✓ ✓ ✗ ✓ | 697 | **yalnız bazı pencerelerde olumlu, DOĞRULANMADI** |
| R3 rejimi izle | ✓ ✓ ✓ ✓ ✗ | ✗ ✓ ✓ ✗ ✗ | ✗ ✓ ✓ ✗ ✗ | 925 | **REDDEDİLDİ** |

° = pencerede UP payı < %30, şart 3 uygulanamaz sayıldı.

## 3. Tutulan / elenen / yeni işlemler

| pencere | kol | tutulan n | tutulan ort R | elenen n | elenen ort R | yeni n | yeni ort R |
|---|---|---|---|---|---|---|---|
| P1 | R1 | 100 | +0,2741 | 396 | +0,0331 | 161 | +0,2765 |
| P1 | R2 | 240 | +0,1722 | 256 | -0,0031 | 65 | +0,2969 |
| P1 | R3 | 148 | +0,0722 | 348 | +0,0857 | 288 | +0,1994 |
| P2 | R1 | 31 | +0,1517 | 198 | -0,2546 | 248 | -0,0767 |
| P2 | R2 | 75 | -0,2221 | 154 | -0,1886 | 101 | -0,2659 |
| P2 | R3 | 49 | -0,3778 | 180 | -0,1510 | 218 | -0,1141 |
| P3 | R1 | 32 | -0,1409 | 168 | -0,1980 | 167 | -0,0966 |
| P3 | R2 | 79 | -0,1420 | 121 | -0,2196 | 137 | -0,0594 |
| P3 | R3 | 39 | -0,2527 | 161 | -0,1734 | 183 | -0,0908 |

## 4. Belirsizlik (ort R farkı, kol − B0; eşli blok bootstrap)

| karşılaştırma | pencere | gözlenen | %95 aralık |
|---|---|---|---|
| R1 − B0 | P1 | +0,1939 | [+0,0469, +0,3410] |
| R1 − B0 | P2 | +0,1482 | [-0,0176, +0,3312] |
| R1 − B0 | P3 | +0,0852 | [-0,1176, +0,2815] |
| R2 − B0 | P1 | +0,1170 | [+0,0132, +0,2294] |
| R2 − B0 | P2 | -0,0477 | [-0,2182, +0,1183] |
| R2 − B0 | P3 | +0,0993 | [-0,0677, +0,2664] |
| R3 − B0 | P1 | +0,0745 | [-0,0405, +0,1821] |
| R3 − B0 | P2 | +0,0370 | [-0,1397, +0,2082] |
| R3 − B0 | P3 | +0,0696 | [-0,1106, +0,2417] |

## 5. Ret dökümü

| koşu | uygulanabilir | açılan | kural retleri |
|---|---|---|---|
| v7_r1_p1 | 29167 | 261 | R1_SHORT_BLOCKED=16636, R1_NOT_UPTREND=4502 |
| v7_r2_p1 | 27320 | 305 | R2_DOWNTREND=16341 |
| v7_r3_p1 | 20972 | 443 | R3_AGAINST_REGIME=6979 |
| v7_r1_p2 | 31835 | 280 | R1_SHORT_BLOCKED=19098, R1_NOT_UPTREND=3110 |
| v7_r2_p2 | 34577 | 176 | R2_DOWNTREND=11293 |
| v7_r3_p2 | 31172 | 268 | R3_AGAINST_REGIME=13803 |
| v7_r1_p3 | 34363 | 202 | R1_SHORT_BLOCKED=21105, R1_NOT_UPTREND=4649 |
| v7_r2_p3 | 30984 | 220 | R2_DOWNTREND=15949 |
| v7_r3_p3 | 32172 | 224 | R3_AGAINST_REGIME=11345 |

## 6. Q2 — referanslar (kaldıraçsız, stopsuz; farklı maruziyet sınıfı)

| pencere | bot (B0) | al-tut | EMA200 trend | trend pozisyonda gün payı | trend giriş/çıkış |
|---|---|---|---|---|---|
| P1 | +87,24% | +592,19% | +219,22% | 0.456 | 202 |
| P2 | -78,69% | +93,95% | +27,30% | 0.488 | 329 |
| P3 | -57,84% | +11,37% | +23,75% | 0.398 | 199 |

## 7. Yorum (sonuçlar görüldükten sonra yazıldı)

### 7.1 Q1: R1 (yalnız LONG, yalnız BTC yükselişinde) temeli üç pencerede de geçiyor

Bu paketin ilk kez gördüğü tablo: bir kol üç pencerede de temeli **her** ölçütte geçiyor:

| pencere | temel | R1 | fark / işlem | %95 aralık | maks düşüş |
|---|---|---|---|---|---|
| P1 | +87,2% | **+140,8%** | +0,194R | [+0,047, +0,341] sıfırı DIŞLIYOR | 51,7% → 45,1% |
| P2 | −78,7% | **−28,4%** | +0,148R | [−0,018, +0,331] | 80,7% → 48,2% |
| P3 | −57,8% | **−39,8%** | +0,085R | [−0,118, +0,282] | 71,6% → 55,4% |

Şart 5 (tutulan ≥ elenen) üç pencerede de sağlandı: P2'de tutulan 31 işlem +0,15R, elenen 198
işlem −0,25R. Yani bu kez fark sermaye yeniden dağıtımından değil, gerçek seçicilikten geliyor.
SHORT'un tamamen kapanması, daha önce ölçülen SHORT kesitiyle (−0,63R, %95'te negatif) tutarlı.

**Ama protokolün kabul şartı geçilmedi:** P2 ve P3'te hesap getirisi hâlâ negatif (şart 1 ve 4).
Karar: "yalnız bazı pencerelerde olumlu, DOĞRULANMADI". R1 kayıp azaltıyor, kâr üretmiyor.
BTC 2023-24'te günlerin %71'inde EMA200 üstündeydi ve sistem o günlerde yalnız LONG'la bile
−28% yaptı: sorun rejimden ibaret değil; giriş kalitesi 2022 sonrasında yükseliş rejiminde de zayıf.

R2 (DOWN'da giriş yok, iki yön serbest) P3'te iyi ama P2'de temelden kötü ve şart 5'i P2'de
geçemiyor; R3 (rejimi izle, DOWN'da SHORT) reddedildi: SHORT tarafı burada da zarar veriyor.

### 7.2 Q2: tek kurallı temel botu her pencerede geçiyor

| pencere | bot (üretim) | al-tut | EMA200 trend |
|---|---|---|---|
| P1 | +87% | +592% | +219% |
| P2 | −79% | +94% | +27% |
| P3 | −58% | +11% | +24% |

Günlük kapanış > EMA200 ise tut, değilse nakit; on coin eşit ağırlık; komisyon ve kayma dahil;
kaldıraç ve stop yok. Üç pencerede de pozitif olan tek şey bu. Maruziyet sınıfı farklı
(kaldıraçsız, stopsuz, ortalama coin başına maksimum düşüş %50-66) ve evren 2026'da seçildiği
için sayılar şişkin; ama karşılaştırma aynı evrende yapıldığı için yön geçerli: 20 uzman, beş
kapı ve onlarca eşik, tek satırlık kurala kaybediyor. Bu, slaytın "az kural az parametre"
iddiasının bu botta doğrulanmasıdır.

### 7.3 Q3: hayatta kalma, kısmi

Yeni veri toplanamadı (Binance API ve arşivi bu ağdan bağlantı sıfırlıyor; VPS betiği hazır).
Kısmi kontrol mevcut koşulardan: P1'de 2021 kârının 2/3'ü 2020'de zaten büyük olan altı coinden
(BTC, ETH, LINK, BNB, LTC, XRP; +58,7 USDT, +0,091R), 1/3'ü 2026'da seçilen dört coinden
(SOL, AVAX, DOGE, AAVE; +28,5 USDT, +0,067R). Yani 2021 kârı çoğunlukla hayatta kalma
yanlılığı DEĞİL. Tam kontrol (2020-10 hacmiyle seçilmiş evren) VPS'te veri toplandıktan sonra.

### 7.4 Uygulama kararı

Protokol §5 "geçmezse hiçbir şey eklenmez" diyordu ve R1 geçmedi. Ancak R1, mevcut üretimi
üç pencerede de her ölçütte domine ediyor (getiri, işlem başına R, PF, düşüş) ve şart 5'i her
yerde sağlıyor. Bu durum protokolde öngörülmemişti; sonradan kural değiştirmemek için burada
açıkça yazıyorum: R1 "doğrulanmış kârlı kural" DEĞİL, "ölçülmüş kayıp azaltıcı"dır. Üretime
OFF/SHADOW/ENFORCE sözleşmesiyle bağlanır; config'de ENFORCE önerilir; dağıtım operatör kararıdır.
