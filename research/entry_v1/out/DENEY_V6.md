# DENEY V6 — seans ve hafta sonu kapısı

Protokol: `PROTOCOL_V6.md` (sonuçlar görülmeden yazıldı). P1/P2 bu soru için KİRLİ (bölmeden doğdu); P3 temiz.
Temel B0 = üretim (tetik + C3 mum vetosu). 3 kol × 3 pencere + B0 P3 = 10 koşu.

## 1. Sonuçlar

| pencere | kol | işlem | hesap getirisi | stresli | ort net R | medyan R | isabet | PF | maks düşüş | coin |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | B0 temel (üretim) | 496 | +87,24% | +71,20% | +0,0817 | -0,0592 | %38,1 | 1,204 | 51,65% | 9 |
| P1 2020-11→2022-08 | S1 yalnız 12-20 UTC | 436 | +9,48% | -5,18% | +0,0007 | -0,1995 | %31,2 | 1,022 | 57,59% | 9 |
| P1 2020-11→2022-08 | S2 hafta sonu yok | 515 | +56,19% | +38,69% | +0,0447 | -0,1425 | %36,7 | 1,119 | 49,13% | 9 |
| P1 2020-11→2022-08 | S3 ikisi birlikte | 397 | -19,75% | -32,57% | -0,0293 | -0,2988 | %30,5 | 0,951 | 75,91% | 9 |
| P2 2022-09→2024-08 | B0 temel (üretim) | 229 | -78,69% | -86,91% | -0,1996 | -0,2764 | %28,8 | 0,615 | 80,73% | 9 |
| P2 2022-09→2024-08 | S1 yalnız 12-20 UTC | 211 | -78,50% | -86,34% | -0,2029 | -0,9248 | %28,9 | 0,613 | 78,50% | 9 |
| P2 2022-09→2024-08 | S2 hafta sonu yok | 174 | -86,48% | -92,73% | -0,2734 | -0,1318 | %28,2 | 0,451 | 86,48% | 9 |
| P2 2022-09→2024-08 | S3 ikisi birlikte | 173 | -86,07% | -92,63% | -0,3072 | -0,9995 | %24,3 | 0,494 | 86,84% | 10 |
| P3 2024-09→2026-08 | B0 temel (üretim) | 200 | -57,84% | -64,90% | -0,1889 | -1,0065 | %28,5 | 0,696 | 71,55% | 9 |
| P3 2024-09→2026-08 | S1 yalnız 12-20 UTC | 331 | -39,19% | -51,85% | -0,0640 | -0,2707 | %35,4 | 0,864 | 98,74% | 9 |
| P3 2024-09→2026-08 | S2 hafta sonu yok | 425 | -41,27% | -57,61% | -0,0905 | -0,3766 | %32,0 | 0,883 | 66,68% | 9 |
| P3 2024-09→2026-08 | S3 ikisi birlikte | 306 | -36,56% | -48,36% | -0,0788 | -0,9861 | %34,0 | 0,864 | 66,57% | 9 |

## 2. Kabul ölçütleri (5 şart; üç pencerede de sağlanmalı)

| kol | P1 kirli | P2 kirli | P3 temiz | karar |
|---|---|---|---|---|
| S1 yalnız 12-20 UTC | ✓ ✗ ✓ ✗ ✗ | ✗ ✓ ✓ ✗ ✓ | ✗ ✓ ✓ ✗ ✗ | **REDDEDİLDİ** |
| S2 hafta sonu yok | ✓ ✗ ✓ ✓ ✗ | ✗ ✗ ✓ ✗ ✗ | ✗ ✓ ✓ ✗ ✗ | **REDDEDİLDİ** |
| S3 ikisi birlikte | ✗ ✗ ✓ ✗ ✗ | ✗ ✗ ✓ ✗ ✓ | ✗ ✓ ✓ ✗ ✗ | **REDDEDİLDİ** |

## 3. Tutulan / elenen / yeni işlemler

| pencere | kol | tutulan n | tutulan ort R | elenen n | elenen ort R | yeni n | yeni ort R |
|---|---|---|---|---|---|---|---|
| P1 | S1 | 73 | +0,0679 | 423 | +0,0841 | 363 | -0,0128 |
| P1 | S2 | 219 | +0,0777 | 277 | +0,0849 | 296 | +0,0202 |
| P1 | S3 | 65 | -0,0403 | 431 | +0,1001 | 332 | -0,0272 |
| P2 | S1 | 21 | -0,0896 | 208 | -0,2107 | 190 | -0,2155 |
| P2 | S2 | 68 | -0,2539 | 161 | -0,1766 | 106 | -0,2859 |
| P2 | S3 | 17 | +0,0552 | 212 | -0,2200 | 156 | -0,3467 |
| P3 | S1 | 19 | -0,4467 | 181 | -0,1618 | 312 | -0,0407 |
| P3 | S2 | 70 | -0,2374 | 130 | -0,1628 | 355 | -0,0615 |
| P3 | S3 | 16 | -0,5553 | 184 | -0,1570 | 290 | -0,0525 |

## 4. Belirsizlik (ort R farkı, kol − B0; eşli blok bootstrap)

| karşılaştırma | pencere | gözlenen | %95 aralık |
|---|---|---|---|
| S1 − B0 | P1 | -0,0810 | [-0,1969, +0,0402] |
| S1 − B0 | P2 | -0,0034 | [-0,1678, +0,1591] |
| S1 − B0 | P3 | +0,1249 | [-0,0490, +0,3075] |
| S2 − B0 | P1 | -0,0370 | [-0,1360, +0,0564] |
| S2 − B0 | P2 | -0,0738 | [-0,2452, +0,1262] |
| S2 − B0 | P3 | +0,0984 | [-0,0610, +0,2667] |
| S3 − B0 | P1 | -0,1110 | [-0,2323, +0,0055] |
| S3 − B0 | P2 | -0,1077 | [-0,2751, +0,0692] |
| S3 − B0 | P3 | +0,1101 | [-0,1049, +0,3062] |

## 5. Ret dökümü

| koşu | karar | uygulanabilir | açılan | kural retleri |
|---|---|---|---|---|
| v4_c3_p1 | 39971 | 19851 | 503 | — |
| v6_s1_p1 | 39971 | 21975 | 441 | S1_OUTSIDE_US_SESSION=14569 |
| v6_s2_p1 | 39971 | 20579 | 523 | S2_WEEKEND=6213 |
| v6_s3_p1 | 39971 | 23662 | 401 | S3_OUTSIDE_US_SESSION=15701, S3_WEEKEND=2317 |
| v4_c3_p2 | 43810 | 32431 | 230 | — |
| v6_s1_p2 | 43810 | 33482 | 211 | S1_OUTSIDE_US_SESSION=22337 |
| v6_s2_p2 | 43810 | 34205 | 175 | S2_WEEKEND=9818 |
| v6_s3_p2 | 43810 | 34933 | 173 | S3_OUTSIDE_US_SESSION=23297, S3_WEEKEND=3324 |
| v6_b0_p3 | 43750 | 30120 | 203 | — |
| v6_s1_p3 | 43750 | 27693 | 333 | S1_OUTSIDE_US_SESSION=18494 |
| v6_s2_p3 | 43750 | 27930 | 427 | S2_WEEKEND=8190 |
| v6_s3_p3 | 43750 | 29764 | 308 | S3_OUTSIDE_US_SESSION=19874, S3_WEEKEND=2850 |

## 6. Yorum (sonuçlar görüldükten sonra yazıldı)

**Karar: üç kol da REDDEDİLDİ. Üretime hiçbir şey eklenmez (PROTOCOL_V6 §5).**

### 6.1 Bölme kendini doğrulamadı

Hipotez P1 bölmesinden doğmuştu ("ABD seansında açılanlar +0,175R"). Kontrollü kol (S1) aynı
pencerede temelden KÖTÜ: +9,5% vs +87,2%, işlem başına −0,08R. Bölmede iyi görünen kesit, kendi
kolu olarak koşturulunca tersine döndü; bu paketin daha önce de gördüğü örüntü
(bkz. split-is-not-an-experiment). Sebep §3'te: S1 temelin 496 işleminden yalnız 73'ünü tuttu,
363 yeni işlem açtı ve o yeni işlemler sıfırın altında.

### 6.2 Temiz pencere (P3) ne diyor

Üç kol da P3'te temeli hesap getirisinde geçiyor (−39% / −41% / −37% vs −58%) ama:
* hepsi ağır eksi (şart 1 ve 4 düşüyor),
* tuttukları işlemler elediklerinden belirgin KÖTÜ (S1 −0,45R vs −0,16R; S2 −0,24R vs −0,16R),
* fark yine "yeni" işlemlerden geliyor (S1: 312 yeni işlem −0,04R). Seçicilik yok.

### 6.3 Bu turun asıl yeni bilgisi: üretim sistemi P3'te de kaybediyor

`v6_b0_p3` ilk kez ŞU ANKİ üretim yolunu (tetik + C3 mum vetosu) en yeni iki yıllık pencerede
ölçtü: **200 işlem, −57,8%, işlem başına −0,19R, isabet %28,5.** Bu, 2022-09→2024-08 (−78,7%)
ile aynı yönde. Yani sistemin kaybı bir pencereye özgü değil; 2022 sonrasının tamamında var.
Bugüne kadar sınanan hiçbir giriş süzgeci (H1/H2/H3, tetik, veto, mum ×4, grafik ×4, seans ×3)
bunu değiştirmedi. Giriş süzgeci aramaya devam etmenin ölçülmüş getirisi sıfır.

### 6.4 Dürüstlük notu

* P3 kısa (24 ay) ama temel 200 işlem üretti; şart 3 sağlandı, "ölçülemedi" durumu yok.
* Seans kapısı modülü üretime GİRMEDİ; kod araştırma paketine taşındı (`session_gate.py`).
* Hafta sonu ve seans saatleri kripto için "kapalı" anlamına gelmez; kullanıcının sorusu
  bu turla cevaplandı: bot bunları dikkate almıyor ve ölçüm dikkate almasını desteklemiyor.
