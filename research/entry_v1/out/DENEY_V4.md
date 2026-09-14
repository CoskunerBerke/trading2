# DENEY V4 — mum formasyonu giriş onayı

Protokol: `PROTOCOL_V4.md` (sonuçlar görülmeden yazıldı). Kod: `out/v4_code.patch`.
Kollar tetikli (= V3 A1). 5 kol × 2 pencere = 10 koşu; başka koşu YAPILMADI.

## 0. C0 hash paritesi (v3_a1 ile)

| pencere | v3_a1 | v4_c0 | aynı |
|---|---|---|---|
| P1 | `c7a943021cc2` | `c7a943021cc2` | ✓ |
| P2 | `139eb1069b37` | `139eb1069b37` | ✓ |

## 1. Sonuçlar

| pencere | kol | işlem | hesap getirisi | stresli | ort net R | medyan R | isabet | PF | maks düşüş | coin |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | C0 temel (tetikli, kuralsız) | 499 | +17,22% | +0,80% | +0,0143 | -0,1549 | %36,3 | 1,036 | 63,56% | 9 |
| P1 2020-11→2022-08 | C1 4h şekil | 501 | +42,56% | +25,69% | +0,0414 | -0,0954 | %34,3 | 1,094 | 44,98% | 9 |
| P1 2020-11→2022-08 | C2 4h şekil + teyit | 446 | +18,01% | +2,95% | +0,0246 | -0,1532 | %32,7 | 1,043 | 60,05% | 9 |
| P1 2020-11→2022-08 | C3 karşı-şekil vetosu | 496 | +87,24% | +71,20% | +0,0817 | -0,0592 | %38,1 | 1,204 | 51,65% | 9 |
| P1 2020-11→2022-08 | C4 1d şekil | 446 | +60,52% | +45,20% | +0,0652 | -0,1618 | %35,0 | 1,146 | 46,28% | 9 |
| P2 2022-09→2024-08 | C0 temel (tetikli, kuralsız) | 198 | -80,12% | -86,96% | -0,2109 | -0,9379 | %28,8 | 0,560 | 80,87% | 9 |
| P2 2022-09→2024-08 | C1 4h şekil | 177 | -86,61% | -92,77% | -0,2859 | -0,9896 | %26,0 | 0,497 | 86,61% | 9 |
| P2 2022-09→2024-08 | C2 4h şekil + teyit | 170 | -85,90% | -92,09% | -0,2924 | -1,0128 | %25,9 | 0,503 | 88,06% | 9 |
| P2 2022-09→2024-08 | C3 karşı-şekil vetosu | 229 | -78,69% | -86,91% | -0,1996 | -0,2764 | %28,8 | 0,615 | 80,73% | 9 |
| P2 2022-09→2024-08 | C4 1d şekil | 239 | -68,91% | -77,89% | -0,1385 | -0,3468 | %30,5 | 0,681 | 71,86% | 9 |

## 2. Kabul ölçütleri (önceden yazılı, değiştirilmedi)

| kol | P1 (1/2/3/4) | P2 (1/2/3/4) | karar |
|---|---|---|---|
| C1 4h şekil | ✓ ✓ ✓ ✓ | ✗ ✗ ✓ ✗ | **tek pencerede olumlu, DOĞRULANMADI** |
| C2 4h şekil + teyit | ✓ ✓ ✓ ✓ | ✗ ✗ ✓ ✗ | **tek pencerede olumlu, DOĞRULANMADI** |
| C3 karşı-şekil vetosu | ✓ ✓ ✓ ✓ | ✗ ✓ ✓ ✗ | **tek pencerede olumlu, DOĞRULANMADI** |
| C4 1d şekil | ✓ ✓ ✓ ✓ | ✗ ✓ ✓ ✗ | **tek pencerede olumlu, DOĞRULANMADI** |

## 3. Belirsizlik (işlem başına ort R farkı, kol − C0; eşli blok bootstrap coin×ay, 2000)

| karşılaştırma | pencere | gözlenen | %95 aralık |
|---|---|---|---|
| C1 − C0 | P1 | +0,0272 | [-0,0774, +0,1287] |
| C1 − C0 | P2 | -0,0750 | [-0,2961, +0,1337] |
| C2 − C0 | P1 | +0,0103 | [-0,1119, +0,1283] |
| C2 − C0 | P2 | -0,0815 | [-0,3005, +0,1344] |
| C3 − C0 | P1 | +0,0674 | [-0,0144, +0,1526] |
| C3 − C0 | P2 | +0,0113 | [-0,1654, +0,1773] |
| C4 − C0 | P1 | +0,0509 | [-0,0553, +0,1589] |
| C4 − C0 | P2 | +0,0724 | [-0,1313, +0,2832] |

## 4. Ret dökümü (kural kaynaklı retler)

| koşu | karar | uygulanabilir | açılan | kural retleri |
|---|---|---|---|---|
| v4_c0_p1 | 39971 | 20265 | 505 | — |
| v4_c1_p1 | 39971 | 20416 | 507 | C1_OPPOSITE_PATTERN=6239, C1_NO_PATTERN=5354 |
| v4_c2_p1 | 39971 | 22018 | 451 | C2_OPPOSITE_PATTERN=7272, C2_NO_PATTERN=5766, C2_NOT_CONFIRMED=4158 |
| v4_c3_p1 | 39971 | 19851 | 503 | C3_OPPOSITE_PATTERN=6075 |
| v4_c4_p1 | 39971 | 19855 | 453 | C1_NO_PATTERN=5805, C1_OPPOSITE_PATTERN=4895, C1_MISSING_BARS=119 |
| v4_c0_p2 | 43810 | 33169 | 199 | — |
| v4_c1_p2 | 43810 | 33895 | 177 | C1_OPPOSITE_PATTERN=10668, C1_NO_PATTERN=8619 |
| v4_c2_p2 | 43810 | 34417 | 170 | C2_OPPOSITE_PATTERN=11371, C2_NO_PATTERN=8783, C2_NOT_CONFIRMED=6558 |
| v4_c3_p2 | 43810 | 32431 | 230 | C3_OPPOSITE_PATTERN=10213 |
| v4_c4_p2 | 43810 | 31711 | 240 | C1_OPPOSITE_PATTERN=8353, C1_NO_PATTERN=7969 |

## 5. Yorum (sonuçlar görüldükten sonra yazıldı)

**Karar: dört kolun hiçbiri gölge PAPER'a girmez.** Hepsi P1'de dört şartı da sağlıyor, P2'de
hiçbiri pozitif değil. Bu, V3'teki A1 ile aynı örüntü: 2020-11→2022-08 penceresinde her şey
iyi görünüyor, 2022-09→2024-08'de hiçbir giriş kuralı hesabı kurtarmıyor.

### 5.1 Filtre mi, sermaye yeniden dağıtımı mı?

Kural yalnız aday eler; ama elenen her aday sermaye ve risk bütçesini serbest bırakır ve o
bütçeyle temelde AÇILMAYAN başka işlemler açılır. Aşağıdaki tablo bunu ölçer: C0'ın işlem
kümesinden kuralın TUTTUĞU işlemler, ELEDİĞİ işlemler ve yalnız serbest sermaye sayesinde
açılan YENİ işlemler (anahtar: coin × açılış zamanı).

| pencere | kol | tutulan n | tutulan ort R | elenen n | elenen ort R | yeni n | yeni ort R |
|---|---|---|---|---|---|---|---|
| P1 | C1 | 126 | -0,0678 | 373 | +0,0420 | 375 | +0,0782 |
| P1 | C2 | 69 | -0,0192 | 430 | +0,0197 | 377 | +0,0326 |
| P1 | C3 | 223 | +0,0065 | 276 | +0,0206 | 273 | +0,1431 |
| P1 | C4 | 83 | +0,0677 | 416 | +0,0036 | 363 | +0,0646 |
| P2 | C1 | 21 | -0,4347 | 177 | -0,1843 | 156 | -0,2659 |
| P2 | C2 | 23 | -0,4299 | 175 | -0,1821 | 147 | -0,2709 |
| P2 | C3 | 64 | -0,3845 | 134 | -0,1280 | 165 | -0,1278 |
| P2 | C4 | 27 | -0,1524 | 171 | -0,2201 | 212 | -0,1367 |

Okuma:

* **P1'de C1, C2 ve C3'ün elediği işlemler, tuttuğu işlemlerden DAHA İYİ.** C1 tutulan
  −0,068R, elenen +0,042R; C3 tutulan +0,007R, elenen +0,021R. Yani hesap getirisindeki
  +25 ile +70 puanlık fark, formasyonun "iyi girişi seçmesinden" DEĞİL, serbest kalan
  sermayeyle açılan yeni işlemlerden (+0,078R / +0,143R) geliyor. Bu, koşu yolu etkisidir;
  formasyonun seçicilik kanıtı değildir.
* **P2'de C1, C2, C3'ün tuttuğu işlemler, elediğinden DAHA KÖTÜ** (C1 −0,435R'ye karşı
  −0,184R). 4h formasyon onayı, düşüş rejiminde kötü işlemleri tutup nispeten iyilerini elemiş.
* **C4 (günlük bar) tek istisna:** iki pencerede de tutulan ≥ elenen (P1 +0,068 vs +0,004;
  P2 −0,152 vs −0,220). İşaret tutarlı ama iki aralık da sıfırı içeriyor
  ([−0,055, +0,159] ve [−0,131, +0,283]) ve P2 hesabı yine −%68,9.
* Tutulan kümeler küçük (C1 P2: 21 işlem, C2 P1: 69). Bu ölçekte "seçicilik" iddiası
  kurulamaz; yalnız reddedilebilir.

### 5.2 Kullanıcı sorusuna doğrudan cevap

"Bu tarz mumlar lazım" hipotezi bu ölçümde **desteklenmedi**: 4h formasyon onayı (tablonun
saf kullanımı, C1) ve makalenin teyitli kuralı (C2) iki pencerede de işlem seçiciliğini
iyileştirmedi; P2'de kötüleştirdi. Bot bu formasyonların 12'sini zaten tespit ediyordu;
eksik altısı eklendi (candle_v1.1.0) ve eklemek sonucu değiştirmedi.

Günlük formasyon (C4) ve karşı-formasyon vetosu (C3) "bir daha bak" listesine girer, ama
yalnız yeni bir ön kayıtlı protokolle ve üçüncü bir pencereyle; bu turun P1/P2 sonuçları
o protokolün girdisi olamaz (görüldü).

### 5.3 Dürüstlük notu

* C0 determinizm hash'i v3_a1 ile iki pencerede de birebir aynı: bu turun kod değişikliği
  temel koşuyu etkilemedi (parite korundu).
* Şekil eşikleri tek değer; ızgara yok. "Daha iyi eşik" sorusu açık ama bu veriyle
  cevaplanmamalı.
* Bu sonuçlar 6378c25 + kayıtsız yama ile üretildi (`out/v4_code.patch`); yama commit
  edilmedi.

### 5.4 Uygulama (sonuçlardan sonra, operatör kararı)

Kullanıcı sonuçları gördükten sonra "en mantıklı varyantı uygula ve VPS'e aktif et" dedi.
Seçilen varyant C3 (karşı-şekil vetosu): iki pencerede de hesap getirisinde temeli geçen,
işlem kümesini en az bozan kol. Uygulama `tradingbot/candle_confirmation.py` (tek kaynak);
canlı motor, replay ve `candle_rules.py` aynı fonksiyonu çağırır. Kanıt: config'i izleyen
replay (`v4_parity_c3_p1`) ile araştırma koşusu (`v4_c3_p1`) aynı determinizm hash'ini
(`d03ccb9dc38fa006…`) ve aynı 503 işlemi üretti. Bu bir doğrulanmış avantaj iddiası
DEĞİLDİR; §5.1'deki tutulan/elenen bulgusu ayakta.
