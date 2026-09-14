# DENEY V2 — giriş kuralı araştırması

Protokol sonuçlar görülmeden yazıldı: `PROTOCOL_V2.md`. Bu dosya tamamen koşu
çıktılarından üretildi (`make_deney_v2.py`).

## 1. Referanslar — aynı veri, sermaye, maliyet, risk

| koşu | n | hesap getirisi | ort R | medyan R | isabet | PF | maks DD | Sharpe | eş zamanlı | maruziyet (gün) | coin | ay |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1a mevcut bot — KAPILI (2024-09→2026-09) | 3 | **-1.97%** | -1.0266 | -1.0269 | 0.0% | 0.000 | 1.97% | -1.158 | 0.01 | 0.8% | 3 | 1 |
| B1b mevcut bot — kapısız (2024-09→2026-09) | 279 | **-51.99%** | -0.1117 | -0.2180 | 30.1% | 0.784 | 60.54% | -0.625 | 2.28 | 99.7% | 9 | 23 |
| B1b mevcut bot — kapısız (GELİŞTİRME 2022-09→2026-09) | 229 | **-90.16%** | -0.2368 | -0.9840 | 28.4% | 0.583 | 90.16% | -0.832 | 1.23 | 73.2% | 9 | 38 |
| B2 nakit | 0 | **0,00%** | — | — | — | — | 0,00% | — | 0,00 | 0,0% | 0 | 0 |
| B3 al-tut (GELİŞTİRME) | — | **+114.98%** | — | — | — | — | — | — | — | 100% | 10 | — |
| B3 al-tut (2024-09→2026-09) | — | **+8.34%** | — | — | — | — | — | — | — | 100% | 10 | — |

B3 SPOT benzeri, kaldıraçsız, stop'suz farklı bir maruziyet sınıfıdır ve tek başına
üstünlük iddiası kurmaz. On coin 2026'da seçildiği için **hayatta kalma yanlılığı**
bu satırı yukarı çeker.

Sharpe günlük GERÇEKLEŞMİŞ özkaynak serisinden, işlemsiz günler DAHİL hesaplandı.
Gerçekleşmemiş kâr/zarar serilmediği için seri gerçekte olduğundan düzdür ve Sharpe
YUKARI yanlıdır.

## 2. Geliştirme ızgarası (2022-09-01 → 2026-09-01), 18 yapılandırma

### H1

| koşu | n | hesap getirisi | ort R | medyan R | isabet | PF | maks DD | Sharpe | eş zamanlı | maruziyet (gün) | coin | ay |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| temel (kural yok) | 229 | **-90.16%** | -0.2368 | -0.9840 | 28.4% | 0.583 | 90.16% | -0.832 | 1.23 | 73.2% | 9 | 38 |
| h1_r40_c0.1 | 200 | **-64.39%** | -0.2189 | -1.0025 | 28.5% | 0.668 | 66.08% | -0.839 | 0.93 | 69.5% | 9 | 48 |
| h1_r40_c0.2 | 197 | **-60.57%** | -0.1900 | -0.9901 | 29.9% | 0.682 | 61.11% | -0.881 | 0.94 | 66.5% | 9 | 48 |
| h1_r45_c0.1 | 208 | **-85.69%** | -0.2415 | -1.0097 | 26.9% | 0.609 | 85.69% | -1.288 | 0.95 | 60.4% | 9 | 37 |
| h1_r45_c0.2 | 158 | **-87.74%** | -0.3204 | -1.0107 | 23.4% | 0.492 | 87.75% | -1.749 | 0.68 | 47.3% | 9 | 31 |
| h1_r50_c0.1 | 158 | **-88.56%** | -0.3194 | -1.0005 | 20.9% | 0.425 | 88.56% | -1.607 | 0.78 | 51.4% | 9 | 33 |
| h1_r50_c0.2 | 152 | **-90.48%** | -0.3317 | -1.0005 | 21.7% | 0.412 | 90.48% | -1.566 | 0.68 | 46.9% | 9 | 33 |

### H2

| koşu | n | hesap getirisi | ort R | medyan R | isabet | PF | maks DD | Sharpe | eş zamanlı | maruziyet (gün) | coin | ay |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| temel (kural yok) | 229 | **-90.16%** | -0.2368 | -0.9840 | 28.4% | 0.583 | 90.16% | -0.832 | 1.23 | 73.2% | 9 | 38 |
| h2_b25_v1.15 | 144 | **-83.09%** | -0.3831 | -1.0143 | 26.4% | 0.384 | 83.11% | -1.972 | 0.40 | 34.5% | 9 | 33 |
| h2_b25_v1.3 | 211 | **-74.66%** | -0.2386 | -0.5877 | 29.9% | 0.565 | 74.87% | -1.042 | 0.58 | 55.3% | 9 | 47 |
| h2_b35_v1.15 | 269 | **-68.48%** | -0.2002 | -1.0048 | 28.6% | 0.709 | 72.74% | -0.554 | 0.87 | 74.8% | 9 | 48 |
| h2_b35_v1.3 | 164 | **-83.96%** | -0.3753 | -1.0183 | 27.4% | 0.442 | 83.97% | -1.495 | 0.44 | 42.1% | 9 | 35 |
| h2_b45_v1.15 | 169 | **-80.61%** | -0.3135 | -1.0002 | 27.2% | 0.473 | 82.38% | -0.989 | 0.52 | 47.1% | 9 | 38 |
| h2_b45_v1.3 | 99 | **-87.00%** | -0.6019 | -1.0201 | 18.2% | 0.121 | 87.00% | -2.653 | 0.24 | 20.0% | 9 | 17 |

### H3

| koşu | n | hesap getirisi | ort R | medyan R | isabet | PF | maks DD | Sharpe | eş zamanlı | maruziyet (gün) | coin | ay |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| temel (kural yok) | 229 | **-90.16%** | -0.2368 | -0.9840 | 28.4% | 0.583 | 90.16% | -0.832 | 1.23 | 73.2% | 9 | 38 |
| h3_p0.15_r30 | 39 | **-21.20%** | -0.4198 | -1.0186 | 33.3% | 0.421 | 26.58% | -0.886 | 0.11 | 11.8% | 9 | 24 |
| h3_p0.15_r35 | 96 | **-54.55%** | -0.3893 | -1.0152 | 31.2% | 0.409 | 64.32% | -1.107 | 0.31 | 29.5% | 9 | 41 |
| h3_p0.2_r30 | 41 | **-23.91%** | -0.4518 | -1.0186 | 31.7% | 0.392 | 27.87% | -0.976 | 0.12 | 12.2% | 9 | 25 |
| h3_p0.2_r35 | 104 | **-52.25%** | -0.3590 | -1.0120 | 31.7% | 0.476 | 62.16% | -0.941 | 0.35 | 32.7% | 9 | 42 |
| h3_p0.25_r30 | 44 | **-21.71%** | -0.3722 | -1.0181 | 34.1% | 0.473 | 26.58% | -0.838 | 0.15 | 15.4% | 9 | 26 |
| h3_p0.25_r35 | 111 | **-46.27%** | -0.3015 | -1.0080 | 32.4% | 0.541 | 58.68% | -0.857 | 0.40 | 35.8% | 9 | 43 |

## 3. Seçim kuralı (PROTOCOL_V2 sec. 4, önceden yazılı)

| aile | en iyi | getiri | n | coin | ay | şart 2 (n≥60, coin≥6, ay≥12) | şart 3 (6 komşunun ≥4'ü temelden iyi) |
|---|---|---|---|---|---|---|---|
| H1 | `h1_r40_c0.2` | -60.57% | 197 | 9 | 48 | GEÇTİ | GEÇTİ (5/6) |
| H2 | `h2_b35_v1.15` | -68.48% | 269 | 9 | 48 | GEÇTİ | GEÇTİ (6/6) |
| H3 | `h3_p0.15_r30` | -21.20% | 39 | 9 | 24 | KALDI | GEÇTİ (6/6) |

**Seçilen aday: `h1_r40_c0.2`** (geliştirme getirisi -60.57%, n=197).

Temelle fark (işlem başına net R): gözlenen **+0.0468**, eşleştirilmiş %95 [-0.1649, +0.2564], eşleştirilmemiş %95 [-0.1586, +0.2597].

## 4. Değerlendirme penceresi (2020-11-01 → 2022-08-31) — hiç kullanılmamış

| koşu | n | hesap getirisi | ort R | medyan R | isabet | PF | maks DD | Sharpe | eş zamanlı | maruziyet (gün) | coin | ay |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| temel — kapısız | 532 | **+91.10%** | +0.0965 | -0.1542 | 36.1% | 1.181 | 22.03% | 1.093 | 4.38 | 99.9% | 9 | 22 |
| aday `h1_r40_c0.2` | 82 | **+6.67%** | +0.0392 | -0.1131 | 37.8% | 1.083 | 25.85% | 0.279 | 0.76 | 52.6% | 9 | 22 |
| B3 al-tut | — | **+627.54%** | — | — | — | — | — | — | — | 100% | 10 | — |

Bu pencere geliştirmeden ÖNCEdir, ileri dönem DEĞİLDİR. 2021 yükselişi ve 2022
düşüşünü kapsar; al-tut referansı bu yüzden çok yüksektir ve seçim yanlılığı burada
en büyüktür.

## 5. Adayın dağılımı (geliştirme)

**coin:** BNB/USDT n=29 net=-16.5, SOL/USDT n=25 net=+4.3, ETH/USDT n=25 net=-10.2, AAVE/USDT n=24 net=-18.4, XRP/USDT n=22 net=-22.7, LTC/USDT n=20 net=+0.2, DOGE/USDT n=19 net=-15.2, LINK/USDT n=18 net=+15.2

**rejim:** TREND_DOWN n=96 net=-24.9, TREND_UP n=82 net=-16.3, BREAKOUT n=19 net=-19.3

**yön:** SHORT n=107 net=-35.4, LONG n=90 net=-25.2

**çıkış:** stop n=101 net=-184.0, başa-baş stop n=66 net=+10.0, hedef2 n=25 net=+100.5, hedef1 n=5 net=+12.9


## 6. KARAR

**Aday `h1_r40_c0.2` GÖLGE PAPER'a ALINMADI.**

Değerlendirme penceresinde aday pozitif (+6,67%) ama **aynı penceredeki filtresiz temelin
çok gerisinde** (+91,10%). İşlem başına fark **−0,0573R**, %95 aralık [−0,343, +0,252].
Yani filtre işlem sayısını 532'den 82'ye düşürdü, işlem KALİTESİNİ iyileştirmedi. Getirinin
büyük kısmı maruziyetin kesilmesiyle kayboldu (maruziyet %99,9 → %52,6).

Geliştirme penceresinde de hikâye aynı: üç ailenin en iyisi bile işlem başına ölçülebilir
bir iyileşme üretmiyor (H1 +0,047R, H2 +0,037R, H3 −0,065R; üç aralık da sıfırı içeriyor).
Hesap getirisindeki iyileşme (−90% → −61%) daha az/daha farklı işlemden ve bileşiklenmeden
geliyor, daha iyi girişten değil.

**Üç hipotez de reddedildi.** Hiçbiri gölge PAPER'a aday değildir.

## 7. ASIL BULGU — kaybolan avantaj

Filtresiz temelin işlem başına net R'si takvim yılına göre (iki koşu birleştirildi;
karşılaştırılabilir büyüklük ortalama R'dir, net USDT değil çünkü sermaye bileşikleniyor):

| yıl | işlem | ort net R | isabet | PF |
|---|---|---|---|---|
| 2020 | 37 | -0.0687 | 35.1% | 0.83 |
| 2021 | 281 | +0.1240 | 38.4% | 1.23 |
| 2022 | 263 | -0.0107 | 32.7% | 0.98 |
| 2023 | 103 | -0.1541 | 28.2% | 0.74 |
| 2024 | 61 | -0.2188 | 24.6% | 0.66 |
| 2025 | 8 | -0.2473 | 37.5% | 0.58 |
| 2026 | 8 | -0.1471 | 37.5% | 0.63 |

Yön kırılımı sebebi gösteriyor:

| yıl | LONG n / ort R | SHORT n / ort R |
|---|---|---|
| 2021 | 200 / +0.2410 | 81 / -0.1648 |
| 2022 | 76 / -0.1735 | 187 / +0.0555 |
| 2023 | 59 / -0.1880 | 44 / -0.1085 |
| 2024 | 45 / -0.1838 | 16 / -0.3173 |

2021 yükselişinde LONG tarafı kazandı, 2022 düşüşünde SHORT tarafı. 2023'ten itibaren
**iki yön de negatif**. Yani kurulum, güçlü ve sürekli trend varken hizalanıp para
kazanıyor; piyasa kırılganlaştığında kaybediyor. Bu, "hiçbir zaman avantaj yoktu"
demekle aynı şey DEĞİLDİR ve önceki turun çerçevesini düzeltir.

**Sınır:** on coin 2026'da seçildi. 2021'de bu on coinin hepsi hayatta kalan kazananlardı;
seçim yanlılığı 2021 satırını YUKARI çeker. 2025-2026 satırları 8'er işlemdir (sermaye
tükendiği için kapasite çöktü) ve tek başına yorumlanamaz.

## 8. Protokolü uygularken bulunan boşluklar (sonradan DEĞİŞTİRİLMEDİ)

1. **Kârlılık tabanı yok.** Seçim kuralı "getirisi en yüksek yapılandırma" diyor; bu
   yüzden −60,57%'lik bir yapılandırma kurala göre "seçilen aday" oldu. Eşiği sonradan
   değiştirmedim; kuralı yazıldığı gibi uyguladım ve boşluğu buraya yazdım. Bir sonraki
   protokolde şart şu olmalı: **aday, kendi penceresindeki filtresiz temeli geçmeli.**
2. **Karşılaştırma penceresi belirsiz.** §7 "B1a'nın üstünde" diyor ama hangi pencerede
   ölçülen B1a olduğunu yazmıyor. Lafzen uygulanırsa aday (+6,67% > −1,97%) geçerdi;
   bu elmayla armut karşılaştırmasıdır. Doğru karşılaştırma AYNI penceredeki temeldir
   (+91,10%) ve aday orada kaybediyor. Kararı bu okumaya göre verdim.
3. **Değerlendirme penceresinin temeli, aday seçilmeden önce hesaplandı** (22 koşuluk
   toplu partide). Seçime GİRMEDİ — seçim yalnız geliştirme getirilerini kullanır — ama
   pencere artık "hiç görülmemiş" değil, "aday için ilk kez kullanıldı"dır.


## 9. Maliyet stresi — iki ayrı ölçüm

**(a) SABİT işlem kümesi, yalnız gider ×2.** Giriş/çıkış zamanları, yönler ve miktarlar
sabit. Defter kimliği `net = brüt − ücret + funding` olduğu için slippage fill fiyatına
gömülüdür ve ayrıca düşülmez; ikiye katlamak `slippage_cost` kadar EK gider ekler
(çift sayım değildir).

| koşu | n | net | ek ücret | ek slippage | stresli net | fark |
|---|---|---|---|---|---|---|
| temel (DEĞERLENDİRME) | 532 | +91,105 | 11,303 | 6,830 | **+72,971** | −18,133 |
| aday (DEĞERLENDİRME) | 82 | +6,667 | 1,774 | 1,109 | **+3,784** | −2,883 |
| aday (GELİŞTİRME) | 197 | −60,570 | 5,143 | 3,053 | **−68,766** | −8,196 |

Sözleşme tuttu: sabit kümede artan gider **daima** kötüleştiriyor, yani hesap kusuru yok.
Değerlendirme penceresindeki +%91,1 iki katı maliyet altında da pozitif kalıyor (+%72,97).

**(b) DİNAMİK portföy.** Sermaye ve kapılar yeniden çalıştığı için işlem kümesi değişir;
bu yüzden dinamik stres sonucu (a) ile karşılaştırılamaz ve "maliyet faydalı" diye
okunamaz. Önceki turda gözlenen "stresli koşu daha iyi" durumu tam olarak budur.
