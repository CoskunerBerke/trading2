# Altın laboratuvarı — gold_v2 sonuçları (2026-10-05)

Ön kayıt: `docs/GOLD_LAB_V2.md` (`6f16fe2`, hiçbir sonuç hesaplanmadan commit'lendi). Kod `046c5b1` (ilk yapım `16b89ab`),
mühür `GOLD_V2_REGISTRY_SHA = 72182fc4343f9e3f`. Koşular 2026-10-05, bulutta, `scripts/gold_lab_v2.py` ile, hep aynı kodla:

- Aile B, vadeli mekân ve sentetik ayar: 07:13–07:15 UTC. PAXG görülmüş satırları: 07:45 UTC.
- Aile A ve bütün görülmüş satırlar (Dukascopy dahil): 13:10:57–13:11:09 UTC, Dukascopy aynası bittikten sonra.

Geçmiş testtir; PAPER ya da canlı sonuç değildir. Kâr garantisi değildir. Kurallar sonuçtan sonra değiştirilmedi; koşular
sırasında kod ve ön kayıt değiştirilmedi.

## Kısa sonuç

- **Hedef bulunamadı.** Bu kurallar altında maliyet ve fonlama sonrası +%1/ay %0,5 riskte bulunamadı.
- **24 hüküm hücresinin hiçbiri GÜÇLÜ ADAY değil** (standart ve sıkı hüküm). 3 ANA hücrenin hiçbiri geçmedi. Aday oranı
  gerçek hücrelerde 0, hüküm plasebo hücrelerinde 0.
- **Aile A (birinci soru, trend takibi):** gold_v1'in iki izi, bu depoda hiç kullanılmamış Dukascopy 2014–2020 doğrulamasında
  tutmadı.
  - `A_DONCH_20_10` LONG: hüküm ZAYIF İZ (sıkı da ZAYIF İZ). Fonlamasız doğrulama +0,181 R; plaseboya göre +0,09, aralık
    [−0,38, +0,55]. Fonlama vekiliyle doğrulama **−0,257 R**, aylık **−%0,12** [−0,31, +0,11].
  - `A_TSMOM_28` LONG: hüküm KANIT YOK. Doğrulama −0,009 R (R_fon −0,140), aylık **−%0,07** [−0,15, +0,03].
- **Aile B (PAXG hafta sonu geri dönüşü):** `B_WKND_REV_ALL` İKİ YÖN KANIT YOK. Doğrulama −0,055 R (R_fon −0,052), aylık
  **−%0,11** [−0,39, +0,13].
- 24 hücrenin doğrulama aylık ortalamalarının en yükseği +%0,08'dir (`B_WKND_REV_100` İKİ YÖN, VERİ AZ, 6 işlem). Hedefin
  (+%1) onda birine bile yaklaşan hücre yok.
- **Öneri yok.** Hiçbir defter, strateji ya da ayar değişmedi.

## ANA hücreler (önceden kayıtlı sorular)

Hüküm fonlamasız R ile verilir (laboratuvar tanımı). Hedef ve "daha yüksek risk" satırı yalnız R_fon ile hesaplanır
(fonlama vekili: 8 saatte %0,01, long öder, short alır). Dönemler: aile A keşif 2006-01 → 2013-12, doğrulama 2014-01 →
2020-07 (Dukascopy XAUUSD); aile B keşif 2020-08-28 → 2023-12, doğrulama 2024-01 → 2026-09 (PAXG spot).

| hücre | işlem keşif / doğr. | ort. R keşif / doğr. | ort. R_fon keşif / doğr. | plasebo işlem keşif / doğr. | hüküm plasebosu ort. R keşif / doğr. | plaseboya göre fark | fark %95 keşif | fark %95 doğr. | hüküm | sıkı |
|---|---|---|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` LONG | 120 / 73 | +0,458 / +0,181 | +0,077 / −0,257 | 600 / 365 | +0,2203 / +0,0951 | +0,24 / +0,09 | [−0,12, +0,61] | [−0,38, +0,55] | ZAYIF İZ | ZAYIF İZ |
| `A_TSMOM_28` LONG | 68 / 81 | +0,198 / −0,009 | +0,057 / −0,140 | 340 / 405 | +0,1107 / +0,0193 | +0,09 / −0,03 | [−0,18, +0,41] | [−0,24, +0,23] | KANIT YOK | KANIT YOK |
| `B_WKND_REV_ALL` İKİ YÖN | 163 / 142 | −0,041 / −0,055 | −0,040 / −0,052 | 163 / 142 | −0,1275 / −0,2005 | +0,09 / +0,15 | [−0,05, +0,23] | [−0,03, +0,33] | KANIT YOK | KANIT YOK |

### Aylık hedef ölçüsü (yalnız doğrulama, işlem başına %0,5 risk, R_fon)

| hücre | ay | doğr. işlem | işlemsiz ay | ort. aylık % | %95 aralık | ≥ +%1 ay payı | fonlamasız aylık % (bilgi) | hedef |
|---|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` LONG | 78 (2014-01 → 2020-06) | 73 | 36 | −0,12 (tam −0,1203) | [−0,31, +0,11] | %5 | +0,08 [−0,15, +0,37] | hayır |
| `A_TSMOM_28` LONG | 78 (2014-01 → 2020-06) | 81 | 34 | −0,07 (tam −0,0727) | [−0,15, +0,03] | %3 | −0,00 [−0,10, +0,12] | hayır |
| `B_WKND_REV_ALL` İKİ YÖN | 33 (2024-01 → 2026-09) | 142 | 0 | −0,11 (tam −0,1127) | [−0,39, +0,13] | %6 | −0,12 [−0,40, +0,13] | hayır |

- Ortalamanın %1'e tam yetmesi bile, gerçek ortalamanın %1'in altında olma olasılığının kabaca %50 olduğu anlamına gelirdi.
  Burada üç ortalama da eksidir.
- Ay kümeli %95 aralık (ortalama R için; bilgi, hükme girmez): `A_DONCH_20_10` LONG keşif [−0,10, +1,01], doğrulama
  [−0,33, +0,74]; `A_TSMOM_28` LONG keşif [−0,04, +0,49], doğrulama [−0,18, +0,21]; `B_WKND_REV_ALL` İKİ YÖN keşif
  [−0,13, +0,04], doğrulama [−0,19, +0,06].

### "Daha yüksek risk" satırı ve düşüş

- Ön kayda göre bu satırın sayıları (X, toplam açık risk, kaldıraç, X riskte düşüş) yalnız sıkı hükmü GÜÇLÜ ADAY olan
  hücrelerde basılır. Böyle bir hücre yok; 24 hücrenin hepsinde satır **"kenar yok"** der. C (oynaklık hedefli boyut) da
  aynı nedenle "kenar yok".
- Neden sayı yok: üç ANA hücrenin doğrulama ortalama aylık R_fon'u eksidir: `A_DONCH_20_10` LONG −0,2405 R/ay,
  `A_TSMOM_28` LONG −0,1454 R/ay, `B_WKND_REV_ALL` İKİ YÖN −0,2253 R/ay. Hiçbir risk düzeyi bunu +%1/ay'a çevirmez; riski
  artırmak beklenen zararı ve düşüşü aynı oranda büyütür.
- Bilgi olarak en derin düşüş (laboratuvarın `r_stats`'ı, fonlamasız R, işlem sırasıyla; %0,5 riske basit çarpım,
  bileşiksiz):

| hücre | keşif | doğrulama | %0,5 riskte keşif / doğr. |
|---|---|---|---|
| `A_DONCH_20_10` LONG | −18,87 R | −14,47 R | ≈ %9,4 / %7,2 |
| `A_TSMOM_28` LONG | −2,91 R | −9,17 R | ≈ %1,5 / %4,6 |
| `B_WKND_REV_ALL` İKİ YÖN | −11,95 R | −15,72 R | ≈ %6,0 / %7,9 |

- Diğer: kazanma oranı keşif / doğrulama `A_DONCH_20_10` LONG %40,8 / %32,9; `A_TSMOM_28` LONG %33,8 / %28,4;
  `B_WKND_REV_ALL` İKİ YÖN %46,6 / %43,0. Maliyet işlem başına 0,054 / 0,0705 R, 0,034 / 0,044 R ve 0,117 / 0,107 R.
  Eşzamanlı açık işlem (bütün hüküm serisi) en çok 13 (`A_DONCH_20_10` LONG; doğrulamada 8), 1 ve 1.

### Ne demek

- **`A_DONCH_20_10` LONG:** Fonlamasız R ile iki dönemde de artı ve doğrulamada kâr faktörü 1,317 olduğu için laboratuvar
  tanımıyla ZAYIF İZ. Ama:
  - Aynı tutuş süresiyle rastgele zamanda açılan long plasebo da artı (keşif +0,2203, doğrulama +0,0951 R; plasebo hücresinin
    hükmü de ZAYIF İZ). Kuralın plasebodan farkı iki dönemde de sıfırdan ayrılmıyor. Yani artı R'nin çoğu altının
    yükselişinde long durmanın kendisi.
  - Fonlama vekili haftalarca süren long tutuşta kazancı siliyor: doğrulama +0,181 R → R_fon −0,257 R.
  - Rapordaki aylık R_fon değerlerinin yıl toplamları (aritmetik): 2014 −8,03; 2015 −7,32; 2016 +4,05; 2017 −9,86; 2018
    −4,93; 2019 +10,28; 2020 (Oca–Haz) −2,94 R. Yalnız altının yükseldiği 2016 ve 2019 artı.
- **`A_TSMOM_28` LONG:** Keşifte +0,198 R, doğrulamada −0,009 R; plaseboya göre doğrulama farkı −0,03. İz tutmadı. Yıl
  toplamları (aritmetik): 2014 −3,58; 2015 −3,82; 2016 +0,40; 2017 −3,81; 2018 −3,90; 2019 +4,01; 2020 (Oca–Haz) −0,63 R.
- **`B_WKND_REV_ALL` İKİ YÖN:** Pazar akşamı ana piyasa açılışında hafta sonu hareketinin tersine girmek iki dönemde de az da
  olsa zarar etti. Aynı anda, aynı tutuşla, rastgele yönde giren hüküm plasebosu daha çok kaybetti (−0,1275 / −0,2005 R;
  plasebo hücresinin hükmü KAYBETTİRİR). Bu bir geri dönüş izi olabilir; ama fark iki dönemde de sıfırdan ayrılmıyor ve gerçek
  işlemlerin kendisi zararda. Para kazandıran bir kural değildir.
- **Sağdan kesilme (KESİLDİ, bilgi):** hüküm serisi 2020-07-31'de bittiği için kapanmamış işlemler sayılmadı.
  `A_DONCH_20_10` LONG'da 4 işlem (plaseboda 0); 2020-07-31'in son kapanışında piyasaya göre kapatılsalar ortalamaları
  +4,015 R (fonlamasız) olurdu. `A_TSMOM_28` LONG'da 1 işlem, +3,107 R. Ön kayıtta yazıldığı gibi bu yanlılık kurala karşıdır
  (2019–2020 yükselişinde açılıp süren long işlemler düşer). Hüküm ve aylık ölçü bu işlemler olmadan verilir; ön kayda göre
  değişmez.

## İkincil hücreler

### Aile A (Dukascopy XAUUSD, doğrulama 2014-01 → 2020-07)

| varyant | hücre | işlem keşif / doğr. | ort. R keşif / doğr. | ort. R_fon keşif / doğr. | plaseboya göre | fark %95 doğr. | hüküm | sıkı | aylık % (R_fon, %0,5) | %95 | ≥ %1 ay |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` | İKİ YÖN | 181 / 138 | +0,181 / −0,046 | +0,003 / −0,057 | +0,07 / −0,07 | [−0,34, +0,22] | KANIT YOK | KANIT YOK | −0,05 | [−0,35, +0,31] | %10 |
| `A_TSMOM_28` | İKİ YÖN | 137 / 162 | +0,055 / −0,086 | +0,035 / −0,091 | +0,06 / −0,06 | [−0,19, +0,09] | KANIT YOK | KANIT YOK | −0,09 | [−0,23, +0,04] | %5 |
| `A_DONCH_55_20` | LONG | 79 / 43 | +0,722 / −0,112 | +0,115 / −0,708 | +0,45 / −0,18 | [−0,66, +0,39] | KANIT YOK | KANIT YOK | −0,20 | [−0,35, −0,03] | %4 |
| `A_DONCH_55_20` | İKİ YÖN | 106 / 79 | +0,505 / −0,222 | +0,147 / −0,292 | +0,40 / −0,19 | [−0,54, +0,17] | KANIT YOK | KANIT YOK | −0,15 | [−0,36, +0,11] | %8 |
| `A_TSMOM_1M` | LONG | 51 / 42 | +0,133 / +0,104 | +0,030 / −0,043 | +0,15 / +0,04 | [−0,27, +0,35] | ZAYIF İZ | ZAYIF İZ | −0,01 | [−0,07, +0,05] | %0 |
| `A_TSMOM_1M` | İKİ YÖN | 95 / 78 | +0,024 / +0,012 | +0,015 / +0,001 | −0,12 / +0,03 | [−0,20, +0,23] | KANIT YOK | KANIT YOK | +0,00 | [−0,07, +0,08] | %0 |
| `A_TSMOM_3M` | LONG | 58 / 43 | +0,123 / −0,024 | +0,017 / −0,171 | −0,06 / −0,14 | [−0,41, +0,16] | KANIT YOK | KANIT YOK | −0,05 | [−0,10, +0,01] | %0 |
| `A_TSMOM_3M` | İKİ YÖN | 93 / 78 | +0,063 / −0,102 | +0,034 / −0,119 | −0,06 / −0,09 | [−0,29, +0,10] | KANIT YOK | KANIT YOK | −0,06 | [−0,13, +0,02] | %0 |
| `A_TSMOM_12M` | LONG | 67 / 40 | +0,156 / +0,056 | +0,051 / −0,085 | −0,05 / −0,06 | [−0,36, +0,24] | ZAYIF İZ | ZAYIF İZ | −0,02 | [−0,07, +0,04] | %0 |
| `A_TSMOM_12M` | İKİ YÖN | 84 / 78 | +0,151 / −0,034 | +0,088 / −0,033 | +0,32 / +0,01 | [−0,20, +0,23] | KANIT YOK | KANIT YOK | −0,02 | [−0,09, +0,06] | %0 |
| `A_SMA10M` | LONG | 64 / 46 | +0,165 / +0,048 | +0,055 / −0,100 | +0,14 / −0,02 | [−0,25, +0,24] | ZAYIF İZ | ZAYIF İZ | −0,03 | [−0,09, +0,03] | %0 |
| `A_SMA10M` | İKİ YÖN | 87 / 78 | +0,136 / −0,032 | +0,080 / −0,063 | −0,00 / −0,02 | [−0,22, +0,19] | KANIT YOK | KANIT YOK | −0,03 | [−0,10, +0,04] | %0 |
| `A_4H_DONCH_D200` | LONG | 429 / 240 | −0,008 / −0,196 | −0,151 / −0,371 | −0,00 / −0,10 | [−0,35, +0,17] | KANIT YOK | KANIT YOK | −0,55 | [−1,05, +0,04] | %6 |
| `A_4H_DONCH_D200` | İKİ YÖN | 561 / 399 | −0,008 / −0,210 | −0,096 / −0,256 | +0,04 / −0,05 | [−0,22, +0,15] | KAYBETTİRİR | KAYBETTİRİR | −0,63 | [−1,21, +0,05] | %13 |

- 14 ikincil A hücresinde GÜÇLÜ ADAY yok. 3'ü ZAYIF İZ (`A_TSMOM_1M`, `A_TSMOM_12M`, `A_SMA10M`; üçü de LONG); üçünün de
  doğrulama R_fon'u eksi (−0,043, −0,085, −0,100) ve aylık ortalaması −%0,01 ile −%0,03 arası. 1 hücre KAYBETTİRİR
  (`A_4H_DONCH_D200` İKİ YÖN), 10 hücre KANIT YOK.
- `A_DONCH_55_20` LONG keşifte +0,722 R, doğrulamada −0,112 R; fonlama vekiliyle −0,708 R. Aylık aralığın tamamı sıfırın
  altında ([−0,35, −0,03]).
- `A_4H_DONCH_D200` (4h Donchian + günlük SMA200 süzgeci) en çok kaybeden: aylık −%0,55 (LONG) ve −%0,63 (İKİ YÖN). Bütün
  en derin düşüş (`r_stats`, fonlamasız R): LONG keşif −73,9 / doğrulama −64,5 R, İKİ YÖN keşif −68,11 /
  doğrulama −101,57 R.
- Ön kayda göre ANA hücreler geçmezken ikincil bir hücrenin iyi görünmesi kanıt değildir; burada zaten hiçbiri GÜÇLÜ ADAY
  değil.
- Aylık varyantların karar atlamaları (bütün hüküm serisi, bilgi): `A_TSMOM_1M` ISINMA 1; `A_TSMOM_3M` ISINMA 1,
  GERİ_BAKIŞ_AYI 2; `A_TSMOM_12M` ISINMA 1, GERİ_BAKIŞ_AYI 11; `A_SMA10M` ISINMA 1, GERİ_BAKIŞ_AYI 8.
  SONRAKİ_AY_KULLANILAMAZ, SONRAKİ_AY_YOK ve AY_KULLANILAMAZ hepsinde 0.
- Aylık varyantlarda karar ayına yazma, işlemi tutulduğu aydan bir ay ÖNCE raporlar (Haziran'ın R'si Temmuz'da kazanılır).

### Aile B (PAXG spot, doğrulama 2024-01 → 2026-09)

| varyant | hücre | işlem keşif / doğr. | ort. R keşif / doğr. | ort. R_fon keşif / doğr. | plaseboya göre | fark %95 doğr. | hüküm | sıkı | aylık % (R_fon, %0,5) | %95 | ≥ %1 ay |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `B_WKND_REV_ALL` | LONG | 70 / 62 | −0,184 / −0,011 | −0,207 / −0,029 | +0,10 / +0,22 | [−0,05, +0,51] | KANIT YOK | KANIT YOK | −0,03 | [−0,21, +0,16] | %9 |
| `B_WKND_REV_050` | LONG | 5 / 3 | −0,275 / −0,462 | −0,299 / −0,477 | — | — | VERİ AZ | VERİ AZ | −0,02 | [−0,08, +0,02] | %0 |
| `B_WKND_REV_050` | İKİ YÖN | 18 / 16 | −0,145 / +0,224 | −0,139 / +0,238 | — | — | VERİ AZ | VERİ AZ | +0,06 | [−0,07, +0,20] | %6 |
| `B_WKND_REV_100` | LONG | 1 / 1 | +0,415 / +0,790 | +0,396 / +0,763 | — | — | VERİ AZ | VERİ AZ | +0,01 | [+0,00, +0,03] | %0 |
| `B_WKND_REV_100` | İKİ YÖN | 6 / 6 | −0,283 / +0,889 | −0,271 / +0,900 | — | — | VERİ AZ | VERİ AZ | +0,08 | [+0,01, +0,17] | %3 |
| `B_WKND_REV_050_LDN` | LONG | 5 / 3 | −0,082 / −0,172 | −0,095 / −0,185 | — | — | VERİ AZ | VERİ AZ | −0,01 | [−0,05, +0,03] | %0 |
| `B_WKND_REV_050_LDN` | İKİ YÖN | 18 / 16 | +0,091 / +0,301 | +0,094 / +0,307 | — | — | VERİ AZ | VERİ AZ | +0,07 | [−0,04, +0,21] | %3 |

- "—": işlem ya da plasebo sayısı hükmün alt sınırının altında (en az 30 keşif / 20 doğrulama işlemi; plasebonun iki
  dönemde de n ≥ 20 olması).
- PAXG'nin hafta sonu hareketi çoğu hafta küçük: |z| ≥ 0,5 eşiğini geçen (işlem açılan) hafta sonu 6 yılda 34, |z| ≥ 1,0
  olan 12.
- `B_WKND_REV_100` İKİ YÖN'ün doğrulaması artı (+0,889 R) ama yalnız 6 işlemdir ve keşif dönemi eksidir (−0,283 R).
  `B_WKND_REV_050_LDN` İKİ YÖN de 16 doğrulama işlemiyle artı (+0,301 R). Bunlar tarama içindeki küçük örneklemlerdir; ANA
  hücre geçmezken ikincil bir hücrenin iyi görünmesi tarama yapıntısıdır, kanıt değildir. VERİ AZ hücreler hüküm eşiğine
  bile gelmez.

## Plasebo karşılaştırması

**Aile A — hüküm plasebosu.** Gün/4h kural çıkışlı varyantlarda maruziyet eşli plasebo: her gerçek işlem için aynı dönemde,
aynı yönde, aynı tutuş süresiyle (H_τ bar) ve aynı ATR katı stopla K = 5 rastgele işlem. Aylık varyantlarda LONG hücresi için
aynı oranda rastgele seçilen ay sonları, İKİ YÖN için işaret rastgeleleştirme.

| hüküm plasebo hücresi | işlem keşif / doğr. | ort. R keşif / doğr. | hüküm (standart) |
|---|---|---|---|
| `PLACEBO_A_DONCH_20_10` LONG | 600 / 365 | +0,220 / +0,095 | ZAYIF İZ |
| `PLACEBO_A_DONCH_20_10` İKİ YÖN | 905 / 690 | +0,106 / +0,025 | KANIT YOK |
| `PLACEBO_A_TSMOM_28` LONG | 340 / 405 | +0,111 / +0,019 | KANIT YOK |
| `PLACEBO_A_TSMOM_28` İKİ YÖN | 685 / 810 | −0,010 / −0,021 | KANIT YOK |
| `PLACEBO_A_DONCH_55_20` LONG | 395 / 215 | +0,269 / +0,064 | ZAYIF İZ |
| `PLACEBO_A_DONCH_55_20` İKİ YÖN | 530 / 395 | +0,106 / −0,037 | KANIT YOK |
| `PLACEBO_A_TSMOM_1M` LONG | 53 / 42 | −0,022 / +0,068 | KANIT YOK |
| `PLACEBO_A_TSMOM_1M` İKİ YÖN | 95 / 78 | +0,142 / −0,014 | KANIT YOK |
| `PLACEBO_A_TSMOM_3M` LONG | 51 / 47 | +0,179 / +0,113 | ZAYIF İZ |
| `PLACEBO_A_TSMOM_3M` İKİ YÖN | 93 / 78 | +0,127 / −0,014 | KANIT YOK |
| `PLACEBO_A_TSMOM_12M` LONG | 55 / 52 | +0,209 / +0,116 | ZAYIF İZ |
| `PLACEBO_A_TSMOM_12M` İKİ YÖN | 84 / 78 | −0,167 / −0,042 | KANIT YOK |
| `PLACEBO_A_SMA10M` LONG | 57 / 57 | +0,021 / +0,064 | ZAYIF İZ |
| `PLACEBO_A_SMA10M` İKİ YÖN | 87 / 78 | +0,138 / −0,014 | KANIT YOK |
| `PLACEBO_A_4H_DONCH_D200` LONG | 2145 / 1200 | −0,007 / −0,100 | KAYBETTİRİR |
| `PLACEBO_A_4H_DONCH_D200` İKİ YÖN | 2805 / 1995 | −0,045 / −0,165 | KAYBETTİRİR |

- **Asıl karşılaştırma:** gerçek A hücrelerinin 4'ü ZAYIF İZ (hepsi LONG), plasebo hücrelerinin 5'i ZAYIF İZ (hepsi LONG).
  Rastgele zamanlı, aynı maruziyetli long girişler gerçek kurallar kadar sık "iz" veriyor. Yani A'daki zayıf izler kuraldan
  değil, 2006–2020'de altında long durmaktan geliyor.
- `A_4H_DONCH_D200`'ün plasebosu SMA200 süzgecini içermez; oradaki fark süzgecin katkısını da kapsar (ön kayıtta yazıldığı
  gibi). Fark yine de sıfırdan ayrılmıyor.
- **Aday oranı:** aile A gerçek 0, plasebo 0 (16 / 16 hücre hükme girdi). Aile B gerçek 0, plasebo 0 (2 / 2). Toplam 24
  hücrede sıkı GÜÇLÜ ADAY 0.
- **Bilgi plasebosu (gold_v1 tarzı kural çıkışlı rastgele giriş; hükme girmez):** `A_DONCH_20_10` LONG için −0,041 R
  (doğrulama), gerçeğin farkı +0,34 / +0,22; maruziyet eşli hüküm plasebosuna göre fark daha küçüktür (+0,24 / +0,09). Ön
  kayıt, kural çıkışlı rastgele girişin çoğu kez hemen çıkış şartına takılıp piyasada az kalması yüzünden hüküm için
  maruziyet eşli plaseboyu seçmişti.
  Diğer bilgi plaseboları (A, doğrulama ort. R, gerçeğin farkı): `A_DONCH_20_10` İKİ YÖN −0,185, +0,22 / +0,14;
  `A_TSMOM_28` LONG −0,009, +0,06 / −0,00; İKİ YÖN −0,022, +0,09 / −0,06; `A_DONCH_55_20` LONG +0,125, −0,01 / −0,24;
  İKİ YÖN +0,003, +0,03 / −0,23; `A_4H_DONCH_D200` LONG −0,036, −0,11 / −0,16; İKİ YÖN −0,042, +0,01 / −0,17.

**Aile B — hüküm plasebosu.** Aynı Pazar akşamı anında, aynı tutuşla, aynı 1 ATR_d stopla girer. LONG hücresinde yön sabit,
hafta rastgele; İKİ YÖN hücresinde hücrenin haftalarında yön rastgele.

- `PLACEBO_B_WKND_REV_ALL` LONG: 71 / 62 işlem, −0,2822 / −0,2308 R, hüküm KAYBETTİRİR.
- `PLACEBO_B_WKND_REV_ALL` İKİ YÖN: 163 / 142 işlem, −0,1275 / −0,2005 R, hüküm KAYBETTİRİR.
- Diğer 6 plasebo hücresi VERİ AZ.
- Bilgi plasebosu (haftanın her saatinden giriş): `B_WKND_REV_ALL` İKİ YÖN −0,170 / −0,155 R, gerçeğin farkı +0,13 / +0,10;
  LONG −0,089 / −0,166 R, fark −0,09 / +0,15.

**Sentetik ayar (rastgele yürüyüş, gerçek veri yok):** 20 dünya × 24 hücre = 480 hücre; GÜÇLÜ ADAY 0 (standart ve sıkı).
ZAYIF İZ: sınanabilen 307 A hücresinin 19'u, 143 B hücresinin 0'ı. 24 hücrede tesadüfen beklenen aday 0,0; ön kayıttaki
0–0,12 tahmininin alt ucu.

## Çoklu deneme

- gold_v2: 12 varyant, **24 hüküm hücresi** (3'ü ANA). 24'ü de değerlendirildi: GÜÇLÜ ADAY 0 (standart ve sıkı). Hüküm
  dağılımı: aile A 4 ZAYIF İZ, 11 KANIT YOK, 1 KAYBETTİRİR; aile B 2 KANIT YOK, 6 VERİ AZ.
- **Kümülatif altın: 56 ön kayıtlı hücre** (gold_v1 32 + gold_v2 24), hepsi değerlendirildi; aday 0. Tesadüfen beklenen
  0–0,28 aday.
- `A_DONCH_20_10` ve `A_TSMOM_28` gold_v1'in 1.133 keşif grubundan seçildiği için seçim yanlılığı not edilmişti. Bu iki ANA
  hücre geçseydi önceden belirlenmiş birinci sorunun cevabı olarak okunacaktı; geçmediler.

## Bir ANA hücre geçseydi öneri nasıl olurdu (şu an YOK)

Ön kayda göre öneri yalnız şu dört şart birlikte tutarsa sunulur: bir ANA hücre (i) sıkı GÜÇLÜ ADAY, (ii) fonlama vekilli
doğrulama ortalaması %0,5 riskte ≥ +%1/ay, (iii) kendi ailesinin hüküm plasebo hücrelerinde aday oranı 0, (iv) "mekânda
tutmadı" notu yok. Üç ANA hücre de (i) ve (ii)'yi tutmadı.

Tutsaydı öneri şöyle olurdu:

- Yalnız PAPER ve **yalnız kayıt**: XAUUSDT için ayrı bir paper defter. Emir yok; sinyal, varsayımsal giriş/çıkış ve gerçek
  fonlama kaydedilir.
- Örnek A (`A_DONCH_20_10` LONG geçseydi): günlük kapanışta (17:00 New York) 20 günlük kanal kırılımında long, 2 ATR stop,
  10 günlük kanalın altında kapanışta çıkış (en çok 300 gün); işlem başına %0,5 risk; botun 4h turu yeterli. Örnek B (`B_WKND_REV_ALL` İKİ YÖN
  geçseydi): Pazar 18:00 New York'ta hafta sonu hareketinin tersine giriş, 1 ATR_d stop, Pazartesi 17:00 New York'ta çıkış;
  1 saatlik tur yeterli.
- Öneri metni vadeli mekândaki gerçek fonlama etkisini, aylık %95 aralığı, ≥ +%1 ay payını, en derin düşüşü ve kazanan laneti
  notunu içerirdi.
- Sahip onayı olmadan hiçbir şey açılmaz; hiçbir şey otomatik etkinleşmez.

## Bilgi eki (hüküm DEĞİL)

Bu bölümdeki satırlar ne hükme ne deneme sayısına girer. Görülmüş veri ve mekân satırları birbirinden ve hükümden ayrıdır.

### Görülmüş veri — aile A kuralları, Dukascopy XAUUSD 2020-08 → 2026-09

Bu dönem gold_v1'in PAXG verisiyle örtüşür; günlük/4h trend kuralları için "görülmüş" sayılır ve bağımsız kanıt değildir.
Dönem bölünmez. Dukascopy'nin kendi işlem günleri (Pzt–Cum). 74 / 74 ay kullanılabilir, 36.458 saat, tek segment. Fark =
ort. R − maruziyet eşli plasebonun ort. R'si (fonlamasız).

| varyant | hücre | n | ort. R | ort. R_fon | plasebo ort. R | fark | fark %95 |
|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` | LONG | 78 | +0,118 | −0,194 | +0,313 | −0,195 | [−0,60, +0,21] |
| `A_DONCH_20_10` | İKİ YÖN | 114 | −0,092 | −0,231 | +0,147 | −0,239 | [−0,51, +0,05] |
| `A_TSMOM_28` | LONG | 58 | +0,324 | +0,173 | +0,191 | +0,133 | [−0,24, +0,60] |
| `A_TSMOM_28` | İKİ YÖN | 116 | +0,095 | +0,064 | +0,022 | +0,073 | [−0,14, +0,31] |
| `A_DONCH_55_20` | LONG | 60 | +1,291 | +0,646 | +0,781 | +0,510 | [−0,51, +1,62] |
| `A_DONCH_55_20` | İKİ YÖN | 74 | +0,898 | +0,426 | +0,577 | +0,321 | [−0,53, +1,31] |
| `A_TSMOM_1M` | LONG | 40 | +0,205 | +0,089 | +0,157 | +0,048 | [−0,33, +0,40] |
| `A_TSMOM_1M` | İKİ YÖN | 72 | +0,078 | +0,065 | −0,021 | +0,099 | [−0,13, +0,34] |
| `A_TSMOM_3M` | LONG | 45 | +0,228 | +0,110 | +0,097 | +0,131 | [−0,15, +0,45] |
| `A_TSMOM_3M` | İKİ YÖN | 70 | +0,126 | +0,092 | −0,009 | +0,135 | [−0,10, +0,37] |
| `A_TSMOM_12M` | LONG | 47 | +0,186 | +0,070 | +0,126 | +0,061 | [−0,23, +0,37] |
| `A_TSMOM_12M` | İKİ YÖN | 61 | +0,092 | +0,031 | −0,051 | +0,143 | [−0,10, +0,39] |
| `A_SMA10M` | LONG | 49 | +0,141 | +0,022 | +0,246 | −0,105 | [−0,42, +0,21] |
| `A_SMA10M` | İKİ YÖN | 64 | +0,050 | −0,015 | +0,009 | +0,041 | [−0,20, +0,27] |
| `A_4H_DONCH_D200` | LONG | 308 | +0,159 | −0,011 | +0,015 | +0,144 | [−0,17, +0,47] |
| `A_4H_DONCH_D200` | İKİ YÖN | 405 | +0,056 | −0,047 | −0,047 | +0,103 | [−0,15, +0,36] |

- 2020–2026 altın yükselişinde bile hiçbir satırın plaseboya göre fark aralığı sıfırın üstünde değil. `A_DONCH_20_10` LONG
  aynı maruziyetli rastgele long girişten kötü (fark −0,195); fonlama vekiliyle eksi (−0,194 R).

### Görülmüş veri — aile A kuralları, PAXG spot 2020-08-28 → 2026-09-30

Bu dönem gold_v1'de günlük/4h trend kuralları için zaten görülmüştü; bağımsız kanıt değildir. Binance'in kendi 1d/4h barları
(hafta sonu dahil; bar sayımlı kuralların takvim süresi Dukascopy'den kısadır). Dönem bölünmez.

| varyant | hücre | n | ort. R | ort. R_fon | plasebo ort. R | fark | fark %95 |
|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` | LONG | 92 | +0,222 | −0,043 | +0,245 | −0,023 | [−0,44, +0,44] |
| `A_DONCH_20_10` | İKİ YÖN | 140 | +0,030 | −0,066 | +0,060 | −0,030 | [−0,32, +0,29] |
| `A_TSMOM_28` | LONG | 77 | +0,244 | +0,115 | +0,252 | −0,008 | [−0,29, +0,31] |
| `A_TSMOM_28` | İKİ YÖN | 152 | +0,057 | +0,028 | +0,037 | +0,020 | [−0,13, +0,21] |
| `A_DONCH_55_20` | LONG | 61 | +1,174 | +0,683 | +0,353 | +0,821 | [−0,47, +2,28] |
| `A_DONCH_55_20` | İKİ YÖN | 80 | +0,728 | +0,409 | +0,206 | +0,522 | [−0,44, +1,69] |
| `A_TSMOM_1M` | LONG | 39 | +0,148 | +0,028 | +0,153 | −0,005 | [−0,36, +0,36] |
| `A_TSMOM_1M` | İKİ YÖN | 72 | +0,029 | +0,019 | −0,058 | +0,086 | [−0,16, +0,33] |
| `A_TSMOM_3M` | LONG | 44 | +0,174 | +0,050 | +0,221 | −0,047 | [−0,39, +0,29] |
| `A_TSMOM_3M` | İKİ YÖN | 70 | +0,058 | +0,023 | −0,047 | +0,105 | [−0,16, +0,35] |
| `A_TSMOM_12M` | LONG | 47 | +0,185 | +0,057 | +0,161 | +0,024 | [−0,33, +0,32] |
| `A_TSMOM_12M` | İKİ YÖN | 61 | +0,072 | +0,001 | −0,052 | +0,124 | [−0,17, +0,40] |
| `A_SMA10M` | LONG | 47 | +0,194 | +0,064 | +0,205 | −0,011 | [−0,35, +0,33] |
| `A_SMA10M` | İKİ YÖN | 64 | +0,081 | +0,016 | −0,054 | +0,135 | [−0,15, +0,41] |
| `A_4H_DONCH_D200` | LONG | 315 | +0,141 | +0,014 | −0,070 | +0,210 | [−0,06, +0,51] |
| `A_4H_DONCH_D200` | İKİ YÖN | 414 | +0,046 | −0,022 | −0,106 | +0,152 | [−0,08, +0,39] |

- gold_v1'in iki izi (`A_DONCH_20_10` LONG ve `A_TSMOM_28` LONG) bu görülmüş dönemde aynı tutuş süreli rastgele long
  girişten iyi değil (fark −0,023 ve −0,008). gold_v1'deki artı R büyük ölçüde altının 2024–2026 yükselişinde long durmanın
  kendisiydi; bağımsız Dukascopy doğrulaması da bunu destekliyor.

### Mekân — Binance vadeli, gerçek fonlama (XAUUSDT 2025-12-11 → 2026-09-30, PAXGUSDT 2025-03-27 → 2026-09-30)

Bu dönemler aile B'nin doğrulama dönemiyle ve aile A'nın görülmüş dönemiyle örtüşür. Uygulama mekânı denetimidir, bağımsız
kanıt değildir. Aile A'da sinyal PAXG spot barlarından hesaplanır, işlem vadeli barında simüle edilir. Net R = R − gerçek
fonlama; fark = net R − plasebonun net R'si.

| varyant | hücre | XAUUSDT n | XAUUSDT net R | XAUUSDT fark [%95] | PAXGUSDT n | PAXGUSDT net R | PAXGUSDT fark [%95] |
|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` | LONG | 16 | −1,106 | −0,875 [−1,27, −0,47] | 34 | −0,548 | −0,593 [−1,05, −0,12] |
| `A_DONCH_20_10` | İKİ YÖN | 24 | −0,642 | −0,452 [−0,91, +0,03] | 45 | −0,319 | −0,250 [−0,68, +0,17] |
| `A_TSMOM_28` | LONG | 9 | −0,263 | −0,404 [−0,73, −0,07] | 21 | −0,049 | −0,096 [−0,48, +0,50] |
| `A_TSMOM_28` | İKİ YÖN | 18 | −0,097 | −0,124 [−0,45, +0,29] | 42 | −0,079 | −0,080 [−0,35, +0,24] |
| `A_DONCH_55_20` | LONG | 11 | −1,411 | −1,316 [−1,79, −0,87] | 24 | −1,112 | −1,233 [−1,51, −0,99] |
| `A_DONCH_55_20` | İKİ YÖN | 16 | −1,035 | −0,995 [−1,49, −0,49] | 29 | −0,928 | −0,954 [−1,26, −0,63] |
| `A_TSMOM_1M` | LONG | 4 | +0,350 | +0,836 [—] | 12 | +0,392 | −0,152 [−0,88, +0,64] |
| `A_TSMOM_1M` | İKİ YÖN | 8 | +0,390 | +0,062 [−0,73, +0,85] | 17 | +0,324 | +0,337 [−0,28, +0,94] |
| `A_TSMOM_3M` | LONG | 4 | +0,073 | −0,117 [—] | 13 | +0,327 | −0,018 [−0,66, +0,63] |
| `A_TSMOM_3M` | İKİ YÖN | 8 | +0,129 | −0,200 [−1,03, +0,70] | 17 | +0,286 | +0,299 [−0,33, +0,89] |
| `A_TSMOM_12M` | LONG | 8 | +0,021 | +0,000 [−0,82, +0,80] | 17 | +0,246 | +0,000 [−0,59, +0,58] |
| `A_TSMOM_12M` | İKİ YÖN | 8 | +0,021 | −0,308 [−1,13, +0,53] | 17 | +0,246 | +0,286 [−0,33, +0,85] |
| `A_SMA10M` | LONG | 6 | −0,171 | −0,343 [−1,12, +0,50] | 15 | +0,199 | −0,146 [−0,79, +0,56] |
| `A_SMA10M` | İKİ YÖN | 8 | −0,267 | −0,596 [−1,37, +0,16] | 17 | +0,111 | +0,124 [−0,47, +0,72] |
| `A_4H_DONCH_D200` | LONG | 45 | +1,229 | +1,706 [−0,25, +3,73] | 131 | +0,683 | +0,608 [+0,01, +1,32] |
| `A_4H_DONCH_D200` | İKİ YÖN | 78 | +0,435 | +0,731 [−0,28, +2,10] | 164 | +0,416 | +0,389 [−0,11, +0,93] |
| `B_WKND_REV_ALL` | LONG | 20 | −0,413 | −0,328 [−0,69, +0,00] | 36 | −0,036 | −0,022 [−0,39, +0,38] |
| `B_WKND_REV_ALL` | İKİ YÖN | 40 | −0,272 | −0,334 [−0,60, −0,06] | 77 | −0,125 | +0,007 [−0,25, +0,26] |
| `B_WKND_REV_050` | LONG | 0 | — | — | 1 | −1,054 | −1,748 [—] |
| `B_WKND_REV_050` | İKİ YÖN | 3 | +0,220 | +0,220 [—] | 7 | +0,223 | +0,285 [−0,74, +1,21] |
| `B_WKND_REV_100` | LONG | 0 | — | — | 0 | — | — |
| `B_WKND_REV_100` | İKİ YÖN | 0 | — | — | 3 | +0,865 | +0,640 [—] |
| `B_WKND_REV_050_LDN` | LONG | 0 | — | — | 1 | −1,054 | −1,123 [—] |
| `B_WKND_REV_050_LDN` | İKİ YÖN | 3 | −0,288 | −0,327 [—] | 7 | +0,008 | +0,329 [−0,33, +1,03] |

- Hiçbir hüküm hücresi GÜÇLÜ ADAY olmadığı için "mekânda tutmadı" notu düşülecek hücre yok.
- `A_4H_DONCH_D200` LONG iki vadelide de artı (PAXGUSDT'de fark aralığı sıfırın hemen üstünde). Bu dönem 2025–2026
  yükselişinin içindedir. Aynı kural görülmemiş Dukascopy 2014–2020 doğrulamasında KANIT YOK (LONG, aylık −%0,55) ve
  KAYBETTİRİR (İKİ YÖN) çıktı. Yakın dönemin iyi görünmesi kenar değildir.
- Not: vadeli satırlarda gerçek fonlama stopla kapanan işlemde pencereyi stop barının açılışında kapatır; R_fon vekili stop
  barının kapanışında. Fark en çok bir barın fonlamasıdır.

### SHORT (tek başına bilgi; hücre değildir)

| varyant | işlem keşif / doğr. | ort. R keşif / doğr. | plaseboya göre | hüküm |
|---|---|---|---|---|
| `A_DONCH_20_10` | 61 / 65 | −0,363 / −0,302 | −0,25 / −0,25 | KAYBETTİRİR |
| `A_TSMOM_28` | 69 / 81 | −0,085 / −0,162 | +0,04 / −0,10 | KAYBETTİRİR |
| `A_DONCH_55_20` | 27 / 36 | −0,132 / −0,355 | +0,24 / −0,20 | VERİ AZ |
| `A_TSMOM_1M` | 44 / 36 | −0,102 / −0,095 | −0,15 / −0,00 | KANIT YOK |
| `A_TSMOM_3M` | 35 / 35 | −0,037 / −0,197 | −0,05 / −0,05 | KANIT YOK |
| `A_TSMOM_12M` | 17 / 38 | +0,131 / −0,128 | +0,41 / +0,00 | VERİ AZ |
| `A_SMA10M` | 23 / 32 | +0,055 / −0,146 | +0,11 / −0,15 | VERİ AZ |
| `A_4H_DONCH_D200` | 132 / 159 | −0,008 / −0,233 | +0,16 / +0,03 | KANIT YOK |
| `B_WKND_REV_ALL` | 93 / 80 | +0,066 / −0,089 | +0,05 / +0,16 | KANIT YOK |
| `B_WKND_REV_050` | 13 / 13 | −0,096 / +0,382 | — | VERİ AZ |
| `B_WKND_REV_100` | 5 / 5 | −0,423 / +0,909 | — | VERİ AZ |
| `B_WKND_REV_050_LDN` | 13 / 13 | +0,158 / +0,410 | — | VERİ AZ |

### KESİLDİ, BOŞLUK_TUTUŞ, eşzamanlı işlem, C

- KESİLDİ (gerçek / plasebo; piyasaya göre ort. R, fonlamasız): `A_DONCH_20_10` 4 / 0 (+4,015); `A_TSMOM_28` 1 / 0 (+3,107);
  `A_DONCH_55_20` 4 / 0 (+3,935); `A_4H_DONCH_D200` 7 / 0 (+5,484); aylık varyantların her birinde 1 / 1 (piyasaya göre değer
  yok). Aile B'de 0 / 0. BOŞLUK_TUTUŞ her yerde 0 / 0 (Dukascopy hüküm serisi tek segment).
- Eşzamanlı açık işlem (bütün hüküm serisi en çok / zaman ağırlıklı ort.): `A_DONCH_20_10` LONG 13 / 1,22; `A_DONCH_55_20`
  LONG 15 / 1,15; `A_4H_DONCH_D200` LONG 11 / 0,63; günlük `A_TSMOM_28` ve aylık varyantlar 1; aile B 1.
- C (oynaklık hedefli boyut): sıkı hükmü GÜÇLÜ ADAY hücre yok, **kenar yok**; sayı basılmaz.

### Veri notları

**Dukascopy XAUUSD (aile A hükmü):**

- Ayna işi 05:23 UTC'de başladı. 09:31 UTC'de makine yeniden başlayınca 2023-01 ayından sonra durdu. Sahibin isteğiyle
  ("İndirmeye devam et") aynı betik 11:40 UTC'de aynı köke yeniden başlatıldı ve 13:10:32 UTC'de "hourly mirror pass
  finished" satırıyla bitti. Manifest: saatlik 249 ay status 200; günlük 20 yıl status 200, 2026 günlük dosyası 404 (yıl
  bitmedi; hükümde kullanılmaz).
- Anlık görüntü: kesim anı 2026-10-05T13:10:32+00:00; manifest sha256 `c923f05156cae1c6…`; okunan 190 dosya (175 saatlik +
  15 günlük), özet `b1082173622752f5…`; koşu sonunda aynı.
- Zaman damgası denetimi: keşif 415 / 417 hafta (0,9952), doğrulama 336 / 343 hafta (0,9796) beklenen kümede; eşik 0,9 →
  geçti. Haftanın ilk saati h_w (UTC): yaz {21: 235, 22: 250, 23: 7, 2: 1}, kış {22: 129, 23: 137, 5: 1}. Cuma'nın son saatlik
  barı: yaz çoğunlukla 20:00 (487), kış 21:00 (260).
- Kapsama: keşif 96 / 96 ay kullanılabilir, ≥ 20 barlı Pzt–Cum günü 2.080 / 2.087 (0,9966); doğrulama 79 / 79 ay, 1.678 /
  1.718 gün (0,9767) → geçti. Eşik altındaki günlerin hepsi ABD ya da İngiltere resmî tatilleridir (örnek: 25 Aralık, 1 Ocak,
  Kutsal Cuma, Şükran Günü, 4 Temmuz).
- Temizlik: yıl başına 8.760–8.784 saatlik satır (2020 Ocak–Temmuz 5.112); kapalı piyasa barı (düz, hacim 0) atılan
  2.496–2.857 / yıl; hacimsiz hareketli bar 0; geçersiz satır 0; hacimli düz bar (tutulan) 241 (2013–2019).
- Seri: 88.981 saat, 3.785 işlem günü, 22.668 4h bar, 1 segment; BOŞLUK_GERİ_BAKIŞ 0; hafta sonuna düşen saat 0 (761 hafta);
  kısa seans günü 5; KISMİ_GÜN 1 (2006-01-02, serinin ilk günü; beklenen). Günlük dosyanın ay sonu kapanışı ile saatlik
  türetimin farkı: medyan 0,0, mutlak fark medyanı 0,395 USD (175 ay).
- Görülmüş Dukascopy serisi 2020-08 → 2026-09: 74 / 74 ay, 36.458 saat, tek segment, hafta sonu saati 0.

**PAXG spot (aile B hükmü, aile A görülmüş):**

- 1h: 53.369 bar (2020-08-28 12:00 → 2026-09-30 23:00 UTC), eksik 19 bar (%0,04), 24 saatten uzun boşluk yok. 1d: 2.225 bar,
  4h: 13.347 bar, eksik yok. 1h ve 1d seriler tek segment.
- Hafta sonu: 318. Tatilden etkilenen: Kutsal Cuma 6, Noel 1, Yılbaşı 1 (kural değişmedi). Atlanan hafta: ATR_YOK 2,
  EKSİK_BAR 2, HACİMSİZ 0. `B_WKND_REV_ALL` İKİ YÖN 305 işlem açtı (LONG 132, SHORT 173).

**Vadeli (mekân):** XAUUSDT 1h 7.048 / 4h 1.762 / 1d 294 bar, eksik yok; fonlama 1.761 satır, eksik ay yok. PAXGUSDT 1h
13.262 / 4h 3.316 / 1d 553 bar, eksik yok; fonlama 3.315 satır, eksik ay yok. Arşivler pencere başından 10 (XAUUSDT) ve 26
(PAXGUSDT) gün sonra başlıyor.

**Maliyet:** taraf başına taker %0,05 + kayma 3 bps (gidiş-dönüş %0,16). Aile A'da işlem başına 0,03–0,07 R (ANA hücreler),
aile B'de ≈ 0,10–0,14 R.

### Ön kayıttan önce veriden görülenler (ön kayıttaki açıklamanın tekrarı)

- Dukascopy dosya biçimi, aynanın dışında bir klasöre indirilen iki dosyayla doğrulandı: 2010-01 saatlik ve 2010 günlük.
  Görülenler: 2010-01-01'in ilk iki saatlik barı (≈ 1.096 USD), 2010-01-31 23:00 UTC barı (≈ 1.083), 2010-01-01 ve
  2010-01-02 günlük barları ve 2010-12-31 günlük kapanışı (≈ 1.420). Yani 2010'un yönü (≈ +%30) ve Ocak 2010'un yönü (≈ −%1)
  biliniyordu; ikisi de keşif dönemindedir. Doğrulama dönemine (2014-01 → 2020-07) ait hiçbir fiyat görülmedi.
- gold_v1 sırasında 2006-01-01 ve 2006-01-03 dakikalık dosyaları çözülmüştü (2006-01-03 gün içi ≈ 516–535 USD).
- Aynanın geri kalanından ön kayıttan önce yalnız manifest okundu. 11:38 UTC'deki ayna denetiminde de yalnız manifest ve dosya
  varlığı okundu; hiçbir dosya çözülmedi. Bu dosyalar hükümden çıkarılmaz; birkaç fiyat seviyesi kuralların sonucunu
  belirlemez.

### Bilinen sınırlar (kısa)

- Tek varlık: piyasa yönünü kural kenarından ayırmak zordur; maruziyet eşli plasebo bunu ancak kısmen ayırır.
- Dukascopy BID, Binance fiyatı değildir; 2006–2010 spread'leri bugünkünden genişti; iki yön de BID ile simüle edildi.
- Fonlama vekili sabit 8 saatte %0,01'dir; gerçek XAUUSDT fonlaması primle değişir (mekân satırları gerçek fonlamayı
  gösterir). Botun defterleri USDⓈ-M vadeli olduğu için bu maliyet gerçektir.
- Aylık varyantlarda her ay yeniden giriş maliyeti ödendi (gerçek defter pozisyonu taşırdı; sonucu biraz aşağı çeker). Aylık
  varyantların 5 ATR stopu yalnız R birimini tanımlar; Moskowitz–Ooi–Pedersen'de stop yoktur.
- Aile A'nın önseli yayımlanmış trend takibi kanıtıyla (yaklaşık 2016'ya kadar) örtüşür; doğrulama "bu depoda görülmemiş
  veride, tek varlıkta, botun maliyetiyle" bir sınamadır.
- PAXG'nin 2020–2021 hafta sonu likiditesi çok inceydi; 2021Q4–2025Q1 arasında 1 USDT'lik fiyat adımları vardı. Açılış
  anında 3 bps kayma iyimser olabilir. Spotta SHORT varsayımsaldır.
- Geri dönüşün olmaması, hafta sonu hareketinin devam ettiğini kanıtlamaz; devam bu çalışmada sınanmadı (yeni ön kayıt
  gerekir).

## Koşu sırasında çıkan sorunlar

- **Ayna kesintisi:** Dukascopy ayna işi makine yeniden başlayınca 09:31 UTC'de yarıda kaldı (yukarıda). İlk sonuç belgesi
  (`db1828e`, 11:49 UTC) bu yüzden yalnız aile B'yi içeriyordu. Ayna 13:10:32 UTC'de bitti; aile A ondan sonra koşuldu.
  Kapsama ve zaman damgası denetimleri koşu anında yapıldı ve geçti; aile A "yapılamadı" değildir.
- **Aile A iki kez, eşzamanlı koşuldu:** 13:10:57 UTC'de iki oturum (ana oturum ve bu sonuç adımı) aynı komutu
  (`--sections A,seen`, aynı kod, aynı ayna anlık görüntüsü, aynı rapor klasörü) birbirinden habersiz başlattı. İkisi de
  hatasız bitti ve aynı özeti bastı (aile A 2.434 işlem, 32 görülmüş satır, aynı sonuç cümlesi). Laboratuvar belirlenimcidir
  (crc32 plasebo seçimi, sabit bootstrap tohumları); ikinci koşu kural ya da parametre farkı içermez, yeni deneme değildir.
  Klasördeki rapor 1 saniye sonra yazılan koşunundur; bu belgedeki sayılar ondan alındı.
- **11:43 UTC denetim koşusu:** B, mekân, ayar ve PAXG görülmüş bölümleri aynı kodla ayrı bir klasöre yeniden koşuldu.
  Sayılar birebir aynı çıktı (JSON karşılaştırmasıyla doğrulandı). Tek fark: Eylül 2026 PAXG verisi bu kez günlük dosyalar
  yerine aylık arşivden geldi. O koşu `--offline` ile başlamadı (aylık arşiv önbellekte yoktu); `--offline` olmadan alındı.
  13:10 koşusu `--offline` ile çalıştı.
- **Kozmetik:** yalnız aile B'nin koşulduğu ara raporda (`gold_lab_v2_report.md`) aylık A varyantlarının "karar atlamaları"
  satırları sıfırlarla basılıyordu (A koşulmadığı hâlde). Sayıları etkilemez.
- Kod hatası yok; hiçbir bölüm hata vererek durmadı. Kod ve ön kayıt değiştirilmedi.

## Dürüst sonuç ve sonra ne olur

- gold_v2, gold_v1'in en iyi iki izini (1d Donchian 20/10 LONG ve 1d TSMOM_28 LONG) 14 yıllık, bu depoda hiç kullanılmamış
  Dukascopy verisinde değiştirmeden sınadı. İzler tutmadı: biri hiç kanıt vermedi, diğeri yalnız fonlamasız R ile zayıf bir
  iz verdi ve o iz aynı maruziyetli rastgele long girişten ayırt edilemedi.
- Vadeli bir defterde long tutuşun fonlaması (vekil: ayda nominalin ≈ %0,9'u) trend kurallarının küçük artısını siliyor
  (örnekler: `A_DONCH_20_10` LONG doğrulama +0,181 → −0,257 R; `A_DONCH_55_20` LONG −0,112 → −0,708 R).
- Hafta sonu geri dönüşü para kazandırmadı.
- İki ön kayıtlı çalışmada 56 hücre, 0 aday. %0,5 riskte +%1/ay, ayda ortalama +2 R demektir; bu çalışmalarda buna yaklaşan
  bir hücre yok. **Bu kurallar altında maliyet ve fonlama sonrası +%1/ay %0,5 riskte bulunamadı.**
- Bu sonuca göre altın için bir defter **önerilmiyor**. Sonuçtan sonra hiçbir kural ayarlanmaz; ayarlama yeni sürüm (gold_v3),
  yeni ön kayıt ve yeni deneme sayısı demektir. Yeni bir soru ancak sahibin isteğiyle ve ayrı bir ön kayıtla sorulabilir.
- Bot bugün altın işlemiyor; bu çalışma hiçbir defteri, stratejiyi ya da ayarı değiştirmedi.

## Deneme sayısı

gold_v2: 12 varyant, 24 hüküm hücresi (3'ü ANA), hepsi değerlendirildi; GÜÇLÜ ADAY 0 (standart ve sıkı). Kümülatif altın:
56 ön kayıtlı hücre (gold_v1 32 + gold_v2 24), hepsi değerlendirildi, aday 0.
