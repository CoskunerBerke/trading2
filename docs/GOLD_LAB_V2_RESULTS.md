# Altın laboratuvarı — gold_v2 sonuçları (2026-10-05; aile A bekliyor)

Ön kayıt: `docs/GOLD_LAB_V2.md` (`6f16fe2`, hiçbir sonuç hesaplanmadan commit'lendi). Kod `046c5b1` (ilk yapım `16b89ab`),
mühür `GOLD_V2_REGISTRY_SHA = 72182fc4343f9e3f`. Koşular 2026-10-05, bulutta, `scripts/gold_lab_v2.py` ile: aile B, vadeli
mekân ve sentetik ayar 07:13–07:15 UTC, PAXG görülmüş satırları 07:45 UTC. Aynı kodla 11:43 UTC'de ayrı bir klasöre yeniden
koşuldu; bütün sayılar birebir aynı çıktı (tek fark: Eylül 2026 PAXG verisi bu kez günlük dosyalar yerine aylık arşivden
geldi).

Geçmiş testtir; PAPER ya da canlı sonuç değildir. Kâr garantisi değildir. Kurallar sonuçtan sonra değiştirilmedi; koşular
sırasında kod ve ön kayıt değiştirilmedi.

## Kısa sonuç

- **Aile A (günlük/4h trend takibi, Dukascopy XAUUSD 2006–2020): henüz koşulmadı.** Dukascopy aynası hâlâ iniyor (aşağıda).
  Bu bir "yapılamadı" hükmü değildir: kapsama ve zaman damgası denetimleri daha hiç yapılmadı. A'nın 16 hücresi, iki ANA
  hücre (`A_DONCH_20_10` LONG, `A_TSMOM_28` LONG) dahil, bekliyor. Gold_v2'nin önceden belirlenmiş birinci sorusu bu yüzden
  henüz cevapsız.
- **Aile B (PAXG hafta sonu geri dönüşü): kenar yok.** ANA hücre `B_WKND_REV_ALL` İKİ YÖN için standart ve sıkı hüküm
  **KANIT YOK**. Doğrulamada (2024-01 → 2026-09) işlem başına ortalama −0,055 R, fonlama vekiliyle −0,052 R. Aylık getiri
  %0,5 riskte −%0,11, %95 aralık [−0,39, +0,13]. Hedef (+%1/ay) karşılanmadı.
- B'nin 8 hücresinin hiçbiri GÜÇLÜ ADAY değil: 2'si KANIT YOK, 6'sı VERİ AZ. Eşikli varyantlar 6 yılda yalnız 2–34 işlem
  açtı.
- Şimdilik geçerli sonuç cümlesi (yalnız aile B için): **bu kurallar altında maliyet ve fonlama sonrası +%1/ay %0,5 riskte
  bulunamadı.** Aile A'nın sonucu gelince bu cümle yeniden yazılır.
- **Öneri yok.** Hiçbir defter, strateji ya da ayar değişmedi.

## Aile A — neden henüz koşulmadı

- Ön kayıt kuralı: A yalnız ayna işi bitince koşar. Ayna işinin günlüğünde "hourly mirror pass finished" satırı olmalı,
  aynada `.part` dosyası olmamalı ve ayna kökünü kullanan bir süreç çalışmamalı (`mirror_ready`).
- Ayna işi 05:23 UTC'de başladı. 09:31 UTC'de, makine yeniden başladığı için, 2023-01 ayından sonra durdu; günlükte bitiş
  satırı yoktu.
- 11:38 UTC'de yalnız manifest ve dosya varlığı okundu:
  - 205 saatlik kayıt (2006-01 → 2023-01), hepsi status 200 ve bayt > 0.
  - Hüküm penceresinin (2006-01 → 2020-07) 175 ayının hepsinin dosyası aynada.
  - Eksik olanlar: 2023-02 → 2026-09 arası 44 ay ve 21 günlük dosya.
  - Hiçbir dosya çözülmedi. Kapsama eşiği ölçülmedi, hiçbir getiri hesaplanmadı.
- Sahibin isteğiyle ("İndirmeye devam et") aynı betik 11:40 UTC'de aynı köke yeniden başlatıldı. Kalan dosyalar gözlenen
  hızla yaklaşık 1–1,5 saat sürer.
- Bu sonuç oturumu ayna bitişini bekleyemedi: ortamın izin denetimi bekleme (yoklama) döngüsüne izin vermedi. Bu yüzden
  aile A ve Dukascopy görülmüş satırları (2020-08 → 2026-09) koşulmadı.
- Ayna bitince yapılacak tek koşu (B, mekân ve ayar sonuçları aynı klasörde birleşir):
  `python scripts/gold_lab_v2.py --sections A,seen --cache <önbellek> --out <out_v2> --duka-root <ayna> --duka-log <günlük>`.
  Kurallar değişmez. Kapsama ya da zaman damgası denetimi geçmezse A'nın hükmü ön kayıttaki gibi "yapılamadı" yazılır.

## ANA hücreler

Hüküm fonlamasız R ile verilir (laboratuvar tanımı). Hedef ve "daha yüksek risk" satırı yalnız R_fon ile hesaplanır
(fonlama vekili: 8 saatte %0,01, long öder, short alır).

| hücre | işlem keşif / doğr. | ort. R keşif / doğr. | ort. R_fon keşif / doğr. | hüküm plasebosu ort. R keşif / doğr. | plaseboya göre fark | fark %95 keşif | fark %95 doğr. | hüküm | sıkı |
|---|---|---|---|---|---|---|---|---|---|
| `A_DONCH_20_10` LONG | henüz koşulmadı | | | | | | | — | — |
| `A_TSMOM_28` LONG | henüz koşulmadı | | | | | | | — | — |
| `B_WKND_REV_ALL` İKİ YÖN | 163 / 142 | −0,041 / −0,055 | −0,040 / −0,052 | −0,1275 / −0,2005 | +0,09 / +0,15 | [−0,05, +0,23] | [−0,03, +0,33] | KANIT YOK | KANIT YOK |

**`B_WKND_REV_ALL` İKİ YÖN, aylık hedef ölçüsü (doğrulama, %0,5 risk):**

- 33 ay (2024-01 → 2026-09). İşlemsiz ay yok; 142 işlem.
- Ortalama aylık net (R_fon): **−%0,11/ay** (tam değer −0,1127), %95 aralık [−0,39, +0,13]. ≥ +%1 olan ay payı %6
  (33 ayın 2'si). Fonlamasız: −%0,12, [−0,40, +0,13].
- Hedefi karşılıyor mu: **hayır.** Not: ortalamanın %1'e tam yetmesi bile, gerçek ortalamanın %1'in altında olma
  olasılığının kabaca %50 olduğu anlamına gelirdi.
- "Daha yüksek risk" satırı: **kenar yok.** Bu satırın sayıları ön kayda göre yalnız sıkı hükmü GÜÇLÜ ADAY olan hücrelerde
  basılır. Burada ortalama aylık R_fon zaten eksidir (−0,225 R/ay); hangi riskte olursa olsun +%1/ay'a karşılık gelmez.
- En derin düşüş (laboratuvarın `r_stats`'ı, fonlamasız R, işlem sırasıyla): keşif −11,95 R, doğrulama −15,72 R. %0,5
  riskte doğrulama düşüşü, basit çarpımla, bakiyenin ≈ %7,9'udur (bileşiksiz).
- Diğer: kazanma oranı keşif %46,6, doğrulama %43,0; maliyet işlem başına 0,12 / 0,11 R. Ortalama R'nin ay kümeli %95
  aralığı (bilgi) keşif [−0,13, +0,04], doğrulama [−0,19, +0,06]. Eşzamanlı açık işlem en çok 1.

**Ne demek:** Pazar akşamı ana piyasa açılışında hafta sonu hareketinin tersine girmek iki dönemde de az da olsa zarar etti.
Aynı anda, aynı tutuşla, rastgele yönde giren hüküm plasebosu daha çok kaybetti (keşif −0,13, doğrulama −0,20 R; plasebo
hücresinin hükmü KAYBETTİRİR). Gerçek işlemlerin plasebodan iyi olması bir geri dönüş izi olabilir; ama fark iki dönemde de
istatistik olarak sıfırdan ayrılmıyor ve gerçek işlemlerin kendisi zararda. Para kazandıran bir kural değildir.

## İkincil hücreler (aile B)

| varyant | hücre | işlem keşif / doğr. | ort. R keşif / doğr. | ort. R_fon keşif / doğr. | plaseboya göre | fark %95 doğr. | hüküm | sıkı | aylık % (R_fon, %0,5) | %95 | ≥ %1 ay |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `B_WKND_REV_ALL` | LONG | 70 / 62 | −0,184 / −0,011 | −0,207 / −0,029 | +0,10 / +0,22 | [−0,05, +0,51] | KANIT YOK | KANIT YOK | −0,03 | [−0,21, +0,16] | %9 |
| `B_WKND_REV_050` | LONG | 5 / 3 | −0,275 / −0,462 | −0,299 / −0,477 | — | — | VERİ AZ | VERİ AZ | −0,02 | [−0,08, +0,02] | %0 |
| `B_WKND_REV_050` | İKİ YÖN | 18 / 16 | −0,145 / +0,224 | −0,139 / +0,238 | — | — | VERİ AZ | VERİ AZ | +0,06 | [−0,07, +0,20] | %6 |
| `B_WKND_REV_100` | LONG | 1 / 1 | +0,415 / +0,790 | +0,396 / +0,763 | — | — | VERİ AZ | VERİ AZ | +0,01 | [+0,00, +0,03] | %0 |
| `B_WKND_REV_100` | İKİ YÖN | 6 / 6 | −0,283 / +0,889 | −0,271 / +0,900 | — | — | VERİ AZ | VERİ AZ | +0,08 | [+0,01, +0,17] | %3 |
| `B_WKND_REV_050_LDN` | LONG | 5 / 3 | −0,082 / −0,172 | −0,095 / −0,185 | — | — | VERİ AZ | VERİ AZ | −0,01 | [−0,05, +0,03] | %0 |
| `B_WKND_REV_050_LDN` | İKİ YÖN | 18 / 16 | +0,091 / +0,301 | +0,094 / +0,307 | — | — | VERİ AZ | VERİ AZ | +0,07 | [−0,04, +0,21] | %3 |

- Bütün ikincil hücrelerde "daha yüksek risk" satırı ve C: **kenar yok**. Hiçbiri hedefi karşılamıyor.
- "—": işlem ya da plasebo sayısı hükmün alt sınırının altında (en az 30 keşif / 20 doğrulama işlemi; plasebonun iki
  dönemde de n ≥ 20 olması).
- PAXG'nin hafta sonu hareketi çoğu hafta küçük: |z| ≥ 0,5 eşiğini geçen (işlem açılan) hafta sonu 6 yılda 34, |z| ≥ 1,0
  olan 12.
- `B_WKND_REV_100` İKİ YÖN'ün doğrulaması artı (+0,889 R) ama yalnız 6 işlemdir ve keşif dönemi eksidir (−0,283 R).
  `B_WKND_REV_050_LDN` İKİ YÖN de 16 doğrulama işlemiyle artı (+0,301 R), aralığı sıfırı içerir. Bunlar tarama içindeki
  küçük örneklemlerdir. Ön kayda göre ANA hücre geçmezken ikincil bir hücrenin iyi görünmesi tarama yapıntısıdır, kanıt
  değildir; en fazla yeni bir ön kayıt (gold_v3) konusu olabilir. VERİ AZ hücreler o eşiğe bile gelmez.

## Plasebo karşılaştırması

- **Hüküm plasebosu (B):** aynı Pazar akşamı anında, aynı tutuşla, aynı 1 ATR_d stopla girer. LONG hücresinde yön sabit,
  hafta rastgele; İKİ YÖN hücresinde hücrenin haftalarında yön rastgele.
  - `PLACEBO_B_WKND_REV_ALL` LONG: 71 / 62 işlem, −0,2822 / −0,2308 R, hüküm KAYBETTİRİR.
  - `PLACEBO_B_WKND_REV_ALL` İKİ YÖN: 163 / 142 işlem, −0,1275 / −0,2005 R, hüküm KAYBETTİRİR.
  - Diğer 6 plasebo hücresi VERİ AZ.
- **Aday oranı:** gerçek hücrelerde 0, hüküm plasebo hücrelerinde 0 (B'de iki dönemde de sınanabilen 2 / 2 hücre). Toplam
  8 hücrede sıkı GÜÇLÜ ADAY 0.
- **Bilgi plasebosu (haftanın her saatinden giriş; hükme girmez):** `B_WKND_REV_ALL` İKİ YÖN için −0,170 / −0,155 R; gerçek
  işlemlerin farkı +0,13 / +0,10. LONG için −0,089 / −0,166 R; fark −0,09 / +0,15.

## Çoklu deneme

- gold_v2: 12 varyant, 24 hüküm hücresi (3'ü ANA). Şu ana kadar 8'i (aile B) değerlendirildi; aday 0. Aile A'nın 16 hücresi
  bekliyor.
- **Kümülatif altın:** 56 ön kayıtlı hücre (gold_v1 32 + gold_v2 24). Değerlendirilen 40 (gold_v1 32 + gold_v2 aile B 8);
  GÜÇLÜ ADAY 0.
- **Sentetik ayar (rastgele yürüyüş, gerçek veri yok):** 20 dünya × 24 hücre = 480 hücre. GÜÇLÜ ADAY 0 (standart ve sıkı).
  ZAYIF İZ: A hücrelerinde sınanabilen 307 hücrenin 19'u, B'de 143 hücrenin 0'ı. 24 hücrede tesadüfen beklenen aday 0,0
  çıktı; ön kayıttaki 0–0,12 tahmininin alt ucu.

## Bir ANA hücre geçseydi öneri nasıl olurdu (şu an YOK)

Ön kayda göre öneri yalnız şu dört şart birlikte tutarsa sunulur: bir ANA hücre (i) sıkı GÜÇLÜ ADAY, (ii) fonlama vekilli
doğrulama ortalaması %0,5 riskte ≥ +%1/ay, (iii) kendi ailesinin hüküm plasebo hücrelerinde aday oranı 0, (iv) "mekânda
tutmadı" notu yok. Aile B'nin ANA hücresi (i) ve (ii)'yi tutmadı.

Tutsaydı öneri şöyle olurdu:
- Yalnız PAPER ve **yalnız kayıt**: XAUUSDT için ayrı bir paper defter. Emir yok; sinyal, varsayımsal giriş/çıkış ve gerçek
  fonlama kaydedilir.
- Örnek (B tutsaydı): Pazar 18:00 New York'ta hafta sonu hareketinin tersine giriş, 1 ATR_d stop, Pazartesi 17:00 New York'ta
  çıkış; işlem başına %0,5 risk; 1 saatlik tur yeterli.
- Raporda vadeli mekândaki gerçek fonlama etkisi, %95 aralık, en derin düşüş ve kazanan laneti notu bulunurdu.
- Sahip onayı olmadan hiçbir şey açılmaz; hiçbir şey otomatik etkinleşmez.

Bilgi: bu hücrenin XAUUSDT vadeli satırı da zararda (2025-12 → 2026-09, 40 işlem, gerçek fonlamalı net −0,272 R; plaseboya
göre fark −0,334, aralık [−0,60, −0,06]).

## Bilgi eki (hüküm DEĞİL)

Bu bölümdeki satırlar ne hüküm ne de deneme sayısına girer. Görülmüş veri ve mekân satırları birbirinden ve hükümden
ayrıdır.

### Görülmüş veri — aile A kuralları, PAXG spot 2020-08-28 → 2026-09-30

Bu dönem gold_v1'de günlük/4h trend kuralları için zaten görülmüştü; bağımsız kanıt değildir. Binance'in kendi 1d/4h barları
(hafta sonu dahil). Dönem bölünmez. Fark = ort. R − maruziyet eşli plasebonun ort. R'si (fonlamasız).

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

- gold_v1'in iki izi (`A_DONCH_20_10` LONG ve `A_TSMOM_28` LONG) bu görülmüş dönemde **aynı tutuş süreli rastgele long
  girişten iyi değil** (fark −0,023 ve −0,008). Yani gold_v1'deki artı R, büyük ölçüde altının 2024–2026 yükselişinde long
  durmanın kendisiydi.
- Fonlama vekili long trend işlemlerinin kazancının büyük kısmını alıyor (örnek: `A_DONCH_20_10` LONG +0,222 R → R_fon
  −0,043 R). Vadeli bir defterde bu maliyet gerçektir.
- Dukascopy 2020-08 → 2026-09 görülmüş satırları henüz koşulmadı (aynaya bağlı).

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
  yükselişinin içindedir; hüküm plasebosu SMA200 süzgecini içermediği için fark rejim süzgecinin katkısını da kapsar. İkincil
  bir hücredir ve hükmü aile A'nın Dukascopy koşusundan gelecektir. Burada yalnız bilgi.
- Not: vadeli satırlarda gerçek fonlama stopla kapanan işlemde pencereyi stop barının açılışında kapatır; R_fon vekili stop
  barının kapanışında. Fark en çok bir barın fonlamasıdır.

### SHORT (tek başına bilgi; hücre değildir)

| varyant | işlem keşif / doğr. | ort. R keşif / doğr. | plaseboya göre | hüküm |
|---|---|---|---|---|
| `B_WKND_REV_ALL` | 93 / 80 | +0,066 / −0,089 | +0,05 / +0,16 | KANIT YOK |
| `B_WKND_REV_050` | 13 / 13 | −0,096 / +0,382 | — | VERİ AZ |
| `B_WKND_REV_100` | 5 / 5 | −0,423 / +0,909 | — | VERİ AZ |
| `B_WKND_REV_050_LDN` | 13 / 13 | +0,158 / +0,410 | — | VERİ AZ |

### KESİLDİ, BOŞLUK_TUTUŞ, eşzamanlı işlem, C

- Aile B'nin bütün hücrelerinde KESİLDİ ve BOŞLUK_TUTUŞ gerçek ve plaseboda 0 / 0. Eşzamanlı açık işlem en çok 1.
- C (oynaklık hedefli boyut): sıkı hükmü GÜÇLÜ ADAY hücre yok, **kenar yok**; sayı basılmaz.

### Veri notları

- PAXG spot 1h: 53.369 bar (2020-08-28 12:00 → 2026-09-30 23:00 UTC), eksik 19 bar (%0,04), 24 saatten uzun boşluk yok. 1d:
  2.225 bar, 4h: 13.347 bar, eksik yok. 1h ve 1d seriler tek segment.
- Hafta sonu: 318. Tatilden etkilenen: Kutsal Cuma 6, Noel 1, Yılbaşı 1 (kural değişmedi). Atlanan hafta: ATR_YOK 2,
  EKSİK_BAR 2, HACİMSİZ 0. `B_WKND_REV_ALL` İKİ YÖN 305 işlem açtı (LONG 132, SHORT 173).
- Vadeli: XAUUSDT 1h 7.048 / 4h 1.762 / 1d 294 bar, eksik yok; fonlama 1.761 satır, eksik ay yok. PAXGUSDT 1h 13.262 / 4h
  3.316 / 1d 553 bar, eksik yok; fonlama 3.315 satır, eksik ay yok. Arşivler pencere başından 10 (XAUUSDT) ve 26 (PAXGUSDT)
  gün sonra başlıyor.
- Maliyet: taraf başına taker %0,05 + kayma 3 bps (gidiş-dönüş %0,16). Aile B'de bu, işlem başına ≈ 0,10–0,14 R.

### Ön kayıttan önce veriden görülenler (ön kayıttaki açıklamanın tekrarı)

- Dukascopy dosya biçimi, aynanın dışında bir klasöre indirilen iki dosyayla doğrulandı: 2010-01 saatlik ve 2010 günlük.
  Görülenler: 2010-01-01'in ilk iki saatlik barı (≈ 1.096 USD), 2010-01-31 23:00 UTC barı (≈ 1.083), 2010-01-01 ve
  2010-01-02 günlük barları ve 2010-12-31 günlük kapanışı (≈ 1.420). Yani 2010'un yönü (≈ +%30) ve Ocak 2010'un yönü (≈ −%1)
  biliniyordu; ikisi de keşif dönemindedir. Doğrulama dönemine (2014-01 → 2020-07) ait hiçbir fiyat görülmedi.
- gold_v1 sırasında 2006-01-01 ve 2006-01-03 dakikalık dosyaları çözülmüştü (2006-01-03 gün içi ≈ 516–535 USD).
- Aynanın geri kalanından yalnız manifest okundu. Bu dosyalar hükümden çıkarılmaz; birkaç fiyat seviyesi kuralların sonucunu
  belirlemez.

### Bilinen sınırlar (kısa)

- PAXG'nin 2020–2021 hafta sonu likiditesi çok inceydi; 2021Q4–2025Q1 arasında 1 USDT'lik fiyat adımları vardı. Açılış
  anında 3 bps kayma iyimser olabilir. Spotta SHORT varsayımsaldır; gerçekte vadelide yapılırdı.
- Fonlama vekili sabit 8 saatte %0,01'dir; gerçek XAUUSDT fonlaması primle değişir (mekân satırları gerçek fonlamayı
  gösterir).
- Tek varlık: piyasa yönünü kural kenarından ayırmak zordur; plasebo bunu ancak kısmen ayırır.
- Geri dönüşün olmaması, hafta sonu hareketinin devam ettiğini kanıtlamaz; devam bu çalışmada sınanmadı (yeni ön kayıt
  gerekir).

## Koşu sırasında çıkan sorunlar

- Dukascopy ayna işi makine yeniden başlayınca 09:31 UTC'de yarıda kaldı. Sahibin isteğiyle 11:40 UTC'de aynı betikle
  yeniden başlatıldı. Bu oturum bitişini bekleyemediği için aile A koşulmadı (yukarıda).
- `--offline` ile yeniden koşu başlamadı: Eylül 2026 PAXG aylık arşivi önbellekte yoktu (ilk koşu o ayı günlük dosyalardan
  almıştı). `--offline` olmadan `data.binance.vision`'dan alındı; sayılar değişmedi.
- Kod hatası yok; hiçbir bölüm hata vererek durmadı.

## Sonra ne olur

- Ayna bitince aile A ve Dukascopy görülmüş satırları tek koşuyla koşulur; sonuç bu belgeye eklenir. Kurallar, eşikler ve
  dönemler değişmez.
- Aile B için: bu tanımla hafta sonu geri dönüşü kenar vermedi. Eşik ya da tutuş ayarlamak yeni sürüm (gold_v3), yeni ön kayıt
  ve yeni deneme sayısı demektir.
- Bot bugün altın işlemiyor; bu çalışma hiçbir defteri, stratejiyi ya da ayarı değiştirmedi.

## Deneme sayısı

gold_v2: 12 varyant, 24 hüküm hücresi (3'ü ANA). Değerlendirilen 8 (aile B), aday 0; aile A'nın 16 hücresi bekliyor.
Kümülatif altın: 56 ön kayıtlı hücre (gold_v1 32 + gold_v2 24), değerlendirilen 40, aday 0.
