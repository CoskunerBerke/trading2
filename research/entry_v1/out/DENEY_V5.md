# DENEY V5 — grafik formasyonu onayı (çift dip, OBO, üçgen, bayrak)

Protokol: `PROTOCOL_V5.md` (sonuçlar görülmeden yazıldı). Kod: `out/v5_code.patch`.
Temel B0 = üretim (tetik + C3 mum vetosu), `v4_c3_*` koşuları. 4 kol × 2 pencere = 8 koşu.

## 1. Sonuçlar

| pencere | kol | işlem | hesap getirisi | stresli | ort net R | medyan R | isabet | PF | maks düşüş | coin |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 2020-11→2022-08 | B0 temel (tetik + C3 mum vetosu = üretim) | 496 | +87,24% | +71,20% | +0,0817 | -0,0592 | %38,1 | 1,204 | 51,65% | 9 |
| P1 2020-11→2022-08 | P1 4h kırılış şart | 327 | -62,50% | -73,60% | -0,0984 | -0,9600 | %31,2 | 0,823 | 83,65% | 9 |
| P1 2020-11→2022-08 | P2 4h karşı-kırılış vetosu | 507 | +41,56% | +25,08% | +0,0393 | -0,0691 | %36,5 | 1,093 | 57,30% | 9 |
| P1 2020-11→2022-08 | P3 1d kırılış şart | 235 | +14,70% | +6,15% | +0,0286 | -0,1138 | %34,5 | 1,071 | 24,80% | 9 |
| P1 2020-11→2022-08 | P4 1d karşı-kırılış vetosu | 516 | +41,10% | +23,87% | +0,0398 | -0,1145 | %36,2 | 1,085 | 60,23% | 9 |
| P2 2022-09→2024-08 | B0 temel (tetik + C3 mum vetosu = üretim) | 229 | -78,69% | -86,91% | -0,1996 | -0,2764 | %28,8 | 0,615 | 80,73% | 9 |
| P2 2022-09→2024-08 | P1 4h kırılış şart | 122 | -87,76% | -92,29% | -0,4178 | -1,0066 | %27,1 | 0,298 | 87,76% | 10 |
| P2 2022-09→2024-08 | P2 4h karşı-kırılış vetosu | 241 | -78,71% | -87,49% | -0,2207 | -0,5841 | %29,5 | 0,621 | 80,76% | 9 |
| P2 2022-09→2024-08 | P3 1d kırılış şart | 161 | -83,57% | -89,81% | -0,3117 | -1,0043 | %27,3 | 0,463 | 83,57% | 9 |
| P2 2022-09→2024-08 | P4 1d karşı-kırılış vetosu | 194 | -83,34% | -90,11% | -0,2417 | -0,9946 | %27,8 | 0,547 | 86,01% | 9 |

## 2. Kabul ölçütleri (önceden yazılı, 5 şart; değiştirilmedi)

| kol | P1 (1/2/3/4/5) | P2 (1/2/3/4/5) | karar |
|---|---|---|---|
| P1 4h kırılış şart | ✗ ✗ ✓ ✗ ✗ | ✗ ✗ ✓ ✗ ✗ | **REDDEDİLDİ** |
| P2 4h karşı-kırılış vetosu | ✓ ✗ ✓ ✓ ✗ | ✗ ✗ ✓ ✗ ✓ | **REDDEDİLDİ** |
| P3 1d kırılış şart | ✓ ✗ ✓ ✓ ✗ | ✗ ✗ ✓ ✗ ✗ | **REDDEDİLDİ** |
| P4 1d karşı-kırılış vetosu | ✓ ✗ ✓ ✓ ✗ | ✗ ✗ ✓ ✗ ✗ | **REDDEDİLDİ** |

## 3. Tutulan / elenen / yeni işlemler (anahtar coin × açılış zamanı; V4 dersi)

| pencere | kol | tutulan n | tutulan ort R | elenen n | elenen ort R | yeni n | yeni ort R |
|---|---|---|---|---|---|---|---|
| P1 | P1 | 52 | -0,0829 | 444 | +0,1010 | 275 | -0,1013 |
| P1 | P2 | 364 | +0,0313 | 132 | +0,2207 | 143 | +0,0596 |
| P1 | P3 | 39 | -0,1015 | 457 | +0,0973 | 196 | +0,0545 |
| P1 | P4 | 324 | +0,0463 | 172 | +0,1484 | 192 | +0,0290 |
| P2 | P1 | 22 | -0,3798 | 207 | -0,1804 | 100 | -0,4261 |
| P2 | P2 | 121 | -0,1869 | 108 | -0,2137 | 120 | -0,2548 |
| P2 | P3 | 28 | -0,2762 | 201 | -0,1889 | 133 | -0,3192 |
| P2 | P4 | 111 | -0,3053 | 118 | -0,1001 | 83 | -0,1565 |

## 4. Belirsizlik (ort R farkı, kol − B0; eşli blok bootstrap coin×ay, 2000)

| karşılaştırma | pencere | gözlenen | %95 aralık |
|---|---|---|---|
| P1 − B0 | P1 | -0,1801 | [-0,3188, -0,0394] |
| P1 − B0 | P2 | -0,2182 | [-0,4108, -0,0154] |
| P2 − B0 | P1 | -0,0424 | [-0,1125, +0,0304] |
| P2 − B0 | P2 | -0,0211 | [-0,1535, +0,1128] |
| P3 − B0 | P1 | -0,0531 | [-0,2147, +0,1191] |
| P3 − B0 | P2 | -0,1122 | [-0,3073, +0,0715] |
| P4 − B0 | P1 | -0,0419 | [-0,1144, +0,0311] |
| P4 − B0 | P2 | -0,0421 | [-0,1792, +0,0921] |

## 5. Ret dökümü ve formasyon sayımı (kural kaynaklı retler)

| koşu | karar | uygulanabilir | açılan | kural retleri |
|---|---|---|---|---|
| v5_p1_p1 | 39971 | 27448 | 329 | P1_NO_PATTERN=19410, P1_OPPOSITE_PATTERN=1843, P1_AMBIGUOUS=290 |
| v5_p2_p1 | 39971 | 20178 | 513 | P2_OPPOSITE_PATTERN=1400 |
| v5_p3_p1 | 39971 | 29399 | 238 | P3_NO_PATTERN=24850, P3_OPPOSITE_PATTERN=630, P3_AMBIGUOUS=150, P3_MISSING_BARS=119 |
| v5_p4_p1 | 39971 | 20155 | 522 | P4_OPPOSITE_PATTERN=551 |
| v5_p1_p2 | 43810 | 35636 | 122 | P1_NO_PATTERN=23990, P1_OPPOSITE_PATTERN=2903, P1_AMBIGUOUS=581 |
| v5_p2_p2 | 43810 | 32288 | 242 | P2_OPPOSITE_PATTERN=2678 |
| v5_p3_p2 | 43810 | 34554 | 161 | P3_NO_PATTERN=25313, P3_OPPOSITE_PATTERN=1437, P3_AMBIGUOUS=384 |
| v5_p4_p2 | 43810 | 33554 | 195 | P4_OPPOSITE_PATTERN=1533 |

## 6. Yorum (sonuçlar görüldükten sonra yazıldı)

**Karar: dört kol da REDDEDİLDİ. Modül üretime yalnız SHADOW olarak girer (PROTOCOL_V5 §7).**

### 6.1 Taze kırılış şartı (P1) ölçülebilir biçimde ZARARLI

Bu turun en güçlü bulgusu. "Adayın yönünde taze bir formasyon kırılışı olmadan girme" kuralı,
iki pencerede de temeli belirgin kötüleştirdi: işlem başına −0,18R (%95 aralık [−0,32, −0,04])
ve −0,22R ([−0,41, −0,02]). Bu paketin şimdiye kadar ölçtüğü ilk "aralığı sıfırı dışlayan"
giriş kuralı ve yönü olumsuz. Tutulan/elenen tablosu sebebi gösteriyor: kuralın tuttuğu
işlemler (−0,08R) elediklerinden (+0,10R) açıkça kötü. Yani bu sistemde kırılıştan hemen sonra
girmek, kırılışı kovalamaktır; üretimdeki tetik zaten kovalamayı yasaklıyor (CHASE_FORBIDDEN)
ve P1 tam tersini zorluyor. Bulkowski'nin "formasyon performansı 1990'lardan bu yana yarıya
indi" ölçümüyle de tutarlı.

### 6.2 Karşı-kırılış vetosu (P2, P4) yardım etmiyor

P2 iki pencerede de temelden biraz kötü (−0,04R / −0,02R, aralıklar sıfırı içeriyor). Asıl
uyarı §3'te: P1 penceresinde P2'nin ELEDİĞİ 132 işlem +0,22R ile temelin en iyi kesitiydi.
Karşı yönde taze kırılış görülen adaylar bu sistemde kötü değil, iyi işlemlerdi. Günlük
sürümler (P3, P4) de aynı örüntüde ve daha az işlemle.

### 6.3 Formasyon sayımı

4h'de karar noktalarının yaklaşık %30'unda son 3 bar içinde teyitli bir kırılış var
(P1 koşusu: 27.448 uygulanabilir kararın 19.410'u "formasyon yok", 5.905'i aynı taraf,
1.843'ü karşı taraf, 290'ı iki taraf). Bu sıklık, %1,5 toleransla çift dip/tepenin kripto
4h'de sık oluştuğunu söylüyor; dedektörün daha seçici bir ayarı olabilir ama bu turda ızgara
yoktu ve aynı veriyle ayar yapmak sonucu geçersiz kılar.

### 6.4 Kullanıcı sorusuna doğrudan cevap

İki görseldeki her şey artık sistemde: mum formasyonları karar yolunda (C3, ENFORCE), grafik
formasyonları dedektörde ve her adayın kaydında (SHADOW). Ölçüm, grafik formasyonlarını giriş
onayı olarak kullanmanın kâr getirmediğini, en doğal kullanımının (kırılışta gir) zarar
verdiğini gösterdi. Bu yüzden ENFORCE açılmadı. Açmak tek satır config değişikliği ama bu
ölçümün karşısında bunu önermiyorum.

### 6.5 Dürüstlük notu

* Dedektör yeni; sentetik seriyle doğrulandı. Gerçek veride sayım dağılımı ilk kez bu turda
  görüldü (§6.3) ve raporlandı. Eşikler tek değer, ızgara yok.
* Kod `0cfe3f3` + yama (`out/v5_code.patch`), bu yorum yazılırken commit edilmemişti.
