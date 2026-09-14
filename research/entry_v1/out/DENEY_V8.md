# DENEY V8 — mum formasyonu GİRİŞ SİNYALİ olarak ("görünce gir")

Protokol: `PROTOCOL_V8.md` (sonuç görülmeden yazıldı). Dedektör candle_v1.2.0 (üretim modülü), geniş taraf kümeleri.
Tek kurallı sistem: sinyal → sonraki bar açılışında giriş; stop 2,5×ATR14; hedef 2R; zaman stopu 24 bar; coin kovası
sermaye/10, işlem riski %2, kaldıraç ≤5×; taker %0,05 + kayma %0,05 her yön; funding dahil. Uzman ve kapı YOK.

## 1. Sonuçlar

| kol | pencere | getiri | işlem | ort R | isabet | PF | ücret | funding | çıkış (stop/hedef/zaman) |
|---|---|---|---|---|---|---|---|---|---|
| S-A tüm formasyonlar, iki yön | P1 | +11,35% | 2277 | +0,0310 | %44,5 | 1,049 | 11.9 | 4.0 | 927/387/956 |
| S-A tüm formasyonlar, iki yön | P2 | -2,05% | 2536 | +0,0033 | %43,8 | 0,991 | 20.7 | 2.1 | 1033/424/1071 |
| S-A tüm formasyonlar, iki yön | P3 | -5,96% | 2528 | -0,0139 | %42,2 | 0,976 | 20.8 | 1.4 | 1058/418/1042 |
| S-B yalnız boğa, LONG | P1 | +12,49% | 2137 | +0,0347 | %45,5 | 1,049 | 12.8 | 10.3 | 873/337/917 |
| S-B yalnız boğa, LONG | P2 | -5,00% | 2373 | -0,0064 | %43,5 | 0,978 | 19.6 | 5.0 | 973/376/1014 |
| S-B yalnız boğa, LONG | P3 | -1,90% | 2344 | +0,0057 | %44,2 | 0,992 | 19.8 | 3.2 | 962/365/1012 |
| S-C boğa + BTC rejimi UP | P1 | +38,31% | 1150 | +0,1447 | %48,9 | 1,284 | 6.8 | 10.0 | 435/231/484 |
| S-C boğa + BTC rejimi UP | P2 | -22,18% | 1762 | -0,0657 | %40,4 | 0,862 | 13.2 | 4.4 | 758/267/737 |
| S-C boğa + BTC rejimi UP | P3 | +7,11% | 1321 | +0,0369 | %44,2 | 1,053 | 10.5 | 2.4 | 555/235/526 |
| S-D boğa + teyit barı | P1 | +13,20% | 1848 | +0,0404 | %46,0 | 1,062 | 11.1 | 9.1 | 740/308/790 |
| S-D boğa + teyit barı | P2 | -6,71% | 2033 | -0,0131 | %43,9 | 0,965 | 16.7 | 4.5 | 829/313/881 |
| S-D boğa + teyit barı | P3 | +1,22% | 1986 | +0,0141 | %43,6 | 1,006 | 17.2 | 3.0 | 784/330/866 |

## 2. Karar (baştan yazılı ölçüt: üç pencerede de pozitif)

| kol | P1 | P2 | P3 | karar |
|---|---|---|---|---|
| S-A | ✓ | ✗ | ✗ | **KAPANDI** |
| S-B | ✓ | ✗ | ✗ | **KAPANDI** |
| S-C | ✓ | ✗ | ✓ | **KAPANDI** |
| S-D | ✓ | ✗ | ✓ | **KAPANDI** |

## 3. Yorum

**Hiçbir kol üç pencerede pozitif değil. "Formasyonu görünce gir" fikri bu on coin ve bu maliyetlerle KAPANDI.**

* Dört kolun on iki hesabında PF 0,86 ile 1,28 arasında; ortalama R sıfır civarında. Formasyon sinyali
  4h barda ~%50 oranında ateşliyor (pencere başına 2.000+ işlem); maliyet ve funding sonucu belirliyor.
* En iyi kol S-C (boğa formasyonu + BTC rejimi UP): P1 +38%, P3 +7%, ama P2 −22%. BTC 2023-24'te günlerin
  %71'inde EMA200 üstündeydi ve formasyon girişleri yine kaybetti. Rejim, formasyonu kurtarmıyor.
* Teyit barı (S-D, makalenin kuralı) işlem sayısını azaltıyor ama sonucu değiştirmiyor.

**Yan bulgu, asıl önemli olan:** bu naif sistem 2022 sonrası pencerelerde botun çok üstünde (S-B: −5% / −2% vs bot −79% / −58%).
Yani "herhangi bir mum formasyonunda gir, 2,5 ATR stop, 2R hedef" kadar basit bir giriş bile botun 20 uzmanlı seçiminden
daha az kaybediyor. V7-Q2 (EMA200 trend temeli botu geçiyor) ile birlikte bu, bot karar yığınının 2022 sonrasında
NEGATİF seçicilik ürettiğini ikinci kez gösteriyor. Bir sonraki turun konusu artık süzgeç değil, sadeleştirme.

## 4. Sınırlar

* Referans simülatörü replay değil: aynı maliyet varsayımları, ama defter/kaldıraç/likidasyon modeli yok.
* Evren 2026 seçimi (lehte yanlılık); buna rağmen negatif.
* Eşikler tek değer; ızgara yok.

## 5. EK — ÖĞRENEN formasyon girişi (PROTOCOL_V8 §6; hesaplar görüldükten sonra yazıldı)

| kol | pencere | getiri | gerçek işlem / sinyal | ort R | isabet | PF | ücret | funding |
|---|---|---|---|---|---|---|---|---|
| S-E1 | P1 | +11,43% | 2128 / 17330 | +0,0330 | %45,3 | 1,045 | 12.8 | 10.3 |
| S-E1 | P2 | -6,10% | 2105 / 19065 | -0,0090 | %43,6 | 0,97 | 17.0 | 4.5 |
| S-E1 | P3 | -8,74% | 2068 / 18792 | -0,0132 | %43,0 | 0,957 | 16.7 | 2.9 |
| S-E2 | P1 | +9,18% | 1956 / 17330 | +0,0281 | %45,9 | 1,04 | 11.5 | 9.9 |
| S-E2 | P2 | +0,09% | 1435 / 19065 | +0,0034 | %43,3 | 1,001 | 12.0 | 3.4 |
| S-E2 | P3 | +4,42% | 1540 / 18792 | +0,0222 | %44,0 | 1,028 | 13.4 | 2.5 |

S-E1 (ort R > 0): P2 ve P3 negatif → KAPANDI. **S-E2 (alt %95 sınırı > 0): üç pencerede de pozitif** → protokol
gereği "ilgi çekici"; ama +9,2% / +0,09% / +4,4% ve PF 1,04 / 1,00 / 1,03: **başa baş**, kâr değil. Öğrenme,
en kötü formasyonları eleyip sistemi sıfıra getiriyor; EMA200 trend temeli (+219/+27/+24) çok daha güçlü.

Öğrenilmiş tablo, VERİ SONU (2026-08; her koşu zaman çizgisinin tamamını gezer, pencere içi kararlar yalnız o ana kadarki istatistiği kullanır) — n ≥ 30, en çok gözlenen etiketler:

* P1: THREE_WHITE_SOLDIERS n=15203 ort +0.034, BULLISH_HARAMI n=13516 ort -0.048, BULLISH_ENGULFING n=12208 ort -0.002, TWEEZER_BOTTOM n=11918 ort -0.027, MORNING_STAR n=9535 ort -0.033, HAMMER n=7152 ort -0.027, INVERTED_HAMMER n=6072 ort -0.005, BULLISH_BELT_HOLD n=5562 ort +0.000
* (aynı tablo) P2: THREE_WHITE_SOLDIERS n=15203 ort +0.034, BULLISH_HARAMI n=13516 ort -0.048, BULLISH_ENGULFING n=12208 ort -0.002, TWEEZER_BOTTOM n=11918 ort -0.027, MORNING_STAR n=9535 ort -0.033, HAMMER n=7152 ort -0.027, INVERTED_HAMMER n=6072 ort -0.005, BULLISH_BELT_HOLD n=5562 ort +0.000
* (aynı tablo) P3: THREE_WHITE_SOLDIERS n=15203 ort +0.034, BULLISH_HARAMI n=13516 ort -0.048, BULLISH_ENGULFING n=12208 ort -0.002, TWEEZER_BOTTOM n=11918 ort -0.027, MORNING_STAR n=9535 ort -0.033, HAMMER n=7152 ort -0.027, INVERTED_HAMMER n=6072 ort -0.005, BULLISH_BELT_HOLD n=5562 ort +0.000

Okuma: etiketlerin ortalama R'leri sıfır civarında (±0,05); "bu mum kâr getiriyor" diyebileceğimiz, tutarlı
ve büyük bir avantaj taşıyan formasyon YOK. Öğrenen kolun değeri, formasyon SEÇMEKTEN değil, çoğu sinyali
REDDETMEKTEN geliyor (S-E2 P2'de 19.065 sinyalin 1.435'ine girdi).

**Sonraki adım (protokol gereği):** S-E2'yi üretim defteriyle replay içinde yeniden kurmak için bir "formasyon sinyal
kaynağı + öğrenme deposu" gerekir; bu bir sonraki turun işi ve başa baş bir sistem için önceliği düşük. Kâr için asıl
aday V7-Q2'deki trend temelidir.

Not: n değerleri on binlerde; S-E2 pratikte yalnız ÜÇ BEYAZ ASKER'i (ort +0,034R) kabul eden bir süzgece dönüşüyor. Bu bile başa baş.
