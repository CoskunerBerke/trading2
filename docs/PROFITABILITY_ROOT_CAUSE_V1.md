# PROFITABILITY_ROOT_CAUSE_V1 — Neden zarar ediyoruz?

**Ölçüm anı:** 2026-09-05 · **Kod:** `710df45aea39c2965c9d3028074b11149c5d9735` ·
**Mod:** PAPER · **Kaynak:** kanonik `futures_ledger.json` + `entry_snapshot.jsonl` +
`entry_selectivity.json` + `exit_eval.json`.

> **Bu belge bir kârlılık vaadi değildir.** Hiçbir eşik bu 23 işlemi kârlı göstermek için
> ayarlanmamıştır. Aşağıdaki bulguların büyük kısmı **GÖZLEMDİR, NEDENSELLİK DEĞİLDİR** ve
> örneklem sonuç çıkarmak için **YETERSİZDİR**.

---

## 0. Örneklem yeterliliği — önce bu

| Ölçüm | Değer |
| --- | --- |
| Kapanmış kanonik işlem | **23** |
| Beklenti (R) %95 bootstrap GA | **[-0,9201, +0,0822]** |
| Sıfırı dışlıyor mu | **HAYIR** |

**Sonuç:** 23 işlemle negatif beklenti bile istatistiksel olarak kanıtlanmış değildir.
Güven aralığı sıfırı içeriyor. Bu belge "sistem kesin zarar ediyor" demez; "ölçülen
örneklemde beklenti negatif ve bunun nedeni şu olabilir" der. Tek bir alt grubun kârlı
görünmesi **kesinlikle** kârlılık kanıtı sayılmaz.

Ayrıca kritik bir veri sınırı vardır:

| Kanıt sınıfı | Sayı |
| --- | --- |
| Değişmez giriş snapshot'ı olan kapanış (`PROMOTION`) | **1 / 23** (yalnız F00030) |
| `LEGACY_MEMORY` türetilmiş gözlem (`OBSERVATION_ONLY`) | **22 / 23** |
| Tam çıkış yolu (`position_path`) olan kapanış | **1 / 23** |

Yani A–H kararları ve çıkış karşı-olguları 22 işlem için **karar anında kaydedilmiş
veriden değil**, `trade_memory` girişlerinden geriye dönük türetilmiştir. Bunlar
gözlemdir; terfi kanıtı **değildir**.

---

## 1. Portföy geneli (23 kapanış)

| Ölçüm | Değer |
| --- | --- |
| İşlem | 23 |
| Kazanan / Kaybeden / Başabaş | **5 / 18 / 0** |
| Kazanma oranı | **%21,74** |
| Brüt kâr / Brüt zarar (R) | +8,54 / 19,24 |
| Profit factor | **0,4436** |
| Beklenti (R) | **-0,4655** |
| Beklenti (USDT) | **-0,3560** |
| Net PnL (USDT) | **-8,1884** |
| Toplam R | **-10,7061** |
| Ortalama kazanan | **+1,7074 R** |
| Ortalama kaybeden | **-1,0691 R** |
| Ödeme oranı (payoff) | **1,5971** |
| **Bu ödeme oranında kırılma noktası kazanma oranı** | **%38,50** |
| **Bu kazanma oranında gereken ödeme oranı** | **3,60** |
| Maksimum düşüş | **-10,7061 R** (seri monoton) |
| CVaR5 | **-1,3248 R** |
| En uzun ardışık kayıp | **5** (diziler: 5, 5, 5, 3) |
| Ortalama tutma süresi | 66,34 saat |
| Ortalama kaldıraç | 2,17x |

**Aritmetik çekirdek:** ödeme oranı 1,60 ile başabaş için **%38,5** kazanma oranı gerekir;
ölçülen **%21,7**. Alternatif okuma: %21,7 kazanma oranıyla başabaş için ödeme oranının
**3,60** olması gerekirdi; ölçülen 1,60. Açık **kazanma oranı tarafındadır**, ortalama
kazancın küçüklüğünde değil.

---

## 2. Sürtünme (maliyet) dökümü

| Bileşen | Toplam (R) | Ölçülen işlem |
| --- | --- | --- |
| Komisyon | **+0,8209** | 23/23 |
| Funding | **-0,0948** (net ALINDI) | 23/23 |
| Kayma (slippage) | **+1,0599** | 23/23 |
| **Toplam ölçülen sürtünme** | **+1,7860** | 23/23 |
| Etki (impact) maliyeti | **UNKNOWN** | 0/23 |

* Sürtünmenin toplam zarardaki payı: **%16,68**.
* **Sürtünme tamamen sıfırlansaydı beklenti yine `-0,3878 R` olurdu.**

**Hüküm:** maliyetler zararı büyütüyor ama **ana neden değil**. Hipotez 6 tek başına
yetersizdir.

---

## 3. Ölçülebilen kırılımlar

Yalnız alanın gerçekten dolu olduğu kırılımlar verilmiştir. `setup_type` 23/23 `pullback`,
`market_type` 23/23 `USDM_PERP`, `regime` 22/23 `UNKNOWN` → bu üç kırılım **ayrım
üretemez**.

### 3.1 MFE kovası — en güçlü gözlem

| Grup | n | Kazanma | Beklenti (R) | PF | Toplam R |
| --- | --- | --- | --- | --- | --- |
| MFE < 0,25 R | 6 | **%0** | -1,068 | 0,00 | -6,41 |
| **MFE 0,25–1 R** | **10** | **%0** | **-1,083** | **0,00** | **-10,82** |
| MFE ≥ 1 R | 7 | %71,4 | +0,932 | 4,25 | +6,53 |

**Lehe hareket eden ama 1R'ye ulaşamayan 10 işlemin tamamı tam kayıpla kapanmıştır.**
Ortalama vazgeçilen kâr (giveback) **1,4045 R**; ortalama MFE 0,939 R, ortalama
gerçekleşen -0,4655 R.

MFE ≥ 0,5 R olan 11 işlemin **6'sı negatif** kapanmıştır (o grubun ortalama MFE'si 1,713 R,
ortalama gerçekleşeni +0,181 R).

### 3.2 Çıkış nedeni

| Çıkış | n | Kazanma | Beklenti (R) | Toplam R |
| --- | --- | --- | --- | --- |
| `stop` | **18** | %0 | **-1,069** | -19,24 |
| `hedef2` | 3 | %100 | +2,403 | +7,21 |
| `başa-baş stop` | 2 | %100 | +0,663 | +1,33 |

18 kaybın **18'i de stop**. Kayıpların **16'sı -1R'den kötü** (ortalama -1,0691 R) — yani
stop aşımı/kayma var ama küçük. Sorun stop'un *derinliği* değil, **sıklığı**.

### 3.3 Kaldıraç (monoton, ama karıştırıcı)

| Kaldıraç | n | Kazanma | Beklenti (R) | Toplam USDT |
| --- | --- | --- | --- | --- |
| 1x | 7 | %42,9 | -0,082 | -0,14 |
| 2x | 5 | %20,0 | -0,436 | -0,61 |
| 3x | 11 | %9,1 | **-0,723** | **-7,43** |

Kaldıraç R'yi tanım gereği ölçeklemez (R stop mesafesine göredir), dolayısıyla bu ilişki
**seçim etkisidir**: 3x, sistemin farklı bir aday sınıfına verdiği karardır.
**NEDENSEL DEĞİL.**

### 3.4 Varlık sınıfı ve yön

| Grup | n | Kazanma | Beklenti (R) | PF | Toplam USDT |
| --- | --- | --- | --- | --- | --- |
| CRYPTO | 14 | %14,3 | **-0,695** | 0,243 | **-7,47** |
| COMMODITY | 6 | %50,0 | +0,374 | 1,709 | +0,96 |
| EQUITY | 3 | %0 | -1,075 | 0,00 | -1,68 |
| LONG | 18 | %27,8 | -0,296 | 0,616 | -3,13 |
| SHORT | **5** | **%0** | **-1,076** | 0,00 | **-5,05** |

n=6 ve n=3'lük gruplardan **kârlılık sonucu çıkarılamaz**. "Emtia kârlı" demek bu
örneklemde **geçersizdir**.

### 3.5 Zaman kırılımları — GÜRÜLTÜ

Giriş saati ve haftanın günü kovalarının hiçbiri n>7 değildir. En büyük grup (Salı, n=7,
beklenti -1,089) bile tek bir kötü haftadan gelebilir. **Bu kırılımlar rapora yalnız
tamlık için konmuştur; karar üretmezler.**

---

## 4. Mevcut challenger'ların karşı-olgusu (H9 / H10)

### 4.1 A–E giriş aileleri (22/23 `OBSERVATION_ONLY` — terfi kanıtı DEĞİL)

| Aile | VETO | Engellenen kaybeden | Engellenen kazanan | Kaçınılan R | Kaçırılan R | Kalanların beklentisi |
| --- | --- | --- | --- | --- | --- | --- |
| A (olasılık/edge) | 12 | 10 | **2** | +10,809 | **-4,844** | **-0,4310** (n=11) |
| B (rejim/yön) | 1 | 1 | 0 | +1,044 | 0 | -0,4392 (n=22) |
| C (konsensüs) | 0 | 0 | 0 | 0 | 0 | -0,4655 (n=23) |
| D (likidite/maliyet) | 16 | 13 | **3** | +13,920 | **-5,604** | **-0,3415** (n=7) |
| E (portföy ısısı) | 1 | 1 | 0 | +1,060 | 0 | -0,4385 (n=22) |
| **A veya E (P1 mantığı)** | **12** | **10** | **2** | **+10,809** | **-4,844** | **-0,4310** (n=11) |

**Bu tablonun en önemli satırı sonuncusudur.** P1'in tam mantığı (A ya da E VETO ise
filtrele) 23 işlemin 12'sini eler ve geriye kalan 11 işlemin beklentisi
**-0,4655 → -0,4310** olur. Profit factor ise **0,4436 → 0,4379'a DÜŞER**.

> **Mevcut giriş filtrelerinden hiçbiri bu tarihsel örneklemde beklentiyi pozitife
> çevirmiyor.** En agresif filtre (D, 16 VETO) bile kalanları -0,3415 R'de bırakıyor.
> "A/E vetoları zararı süzer" hipotezi bu veriyle **DESTEKLENMEMEKTEDİR**.

Uyarı: bu satırların 22'si karar anında yazılmış snapshot'tan değil, `trade_memory`
girişinden türetilmiştir. Point-in-time garantisi **YOKTUR**; ileriye dönük deney bu yüzden
gereklidir.

### 4.2 Çıkış challenger'ları

`exit_eval.json`: `n_evaluated=23`, **`n_path_complete=1`**, `n_no_complete_path=22`,
`verdict=INSUFFICIENT_EXIT_SAMPLE`, `applied_total=0`.

`position_path.jsonl` yalnız 15 benzersiz `trade_id` içeriyor ve bunlar geç işlemlerdir.
Politikalar (`champion`, `challenger_a_profit_lock`, `challenger_b_giveback_reduce`,
`challenger_c_time_carry`) tanımlı fakat **22 işlemde değerlendirilemiyor**.

> Kâr koruma hipotezi (H5) **ekonomik olarak en güçlü sinyale sahip** ama **çıkış
> challenger'larıyla henüz ÖLÇÜLEMEMİŞTİR**. Bu, deneyin P3 kolunun asıl gerekçesidir.

---

## 5. Hipotez değerlendirmesi

| # | Hipotez | Durum | Kanıt |
| --- | --- | --- | --- |
| 1 | Düşük giriş seçiciliği | **KISMEN — ama filtreler çözmüyor** | A/E filtresi beklentiyi -0,466→-0,431 yapıyor, PF düşüyor |
| 2 | Aynı yön / korelasyon yoğunlaşması | **GÖZLEM, ölçülemedi** | Kapanışlarda %78 LONG, açıkta %91 LONG; korelasyon ölçülmedi |
| 3 | Rejim uyumsuzluğu | **TEST EDİLEMEZ** | `regime` 22/23 UNKNOWN |
| 4 | Stop yerleşimi / -1R sıklığı | **KISMEN** | 18/18 kayıp stop; 16'sı -1R'den kötü ama ortalama yalnız -1,069 R |
| 5 | **MFE sonrası kâr geri verme** | **EN GÜÇLÜ GÖZLEM** | MFE 0,25–1R olan **10 işlemin 10'u da tam kayıp**; ort. giveback 1,4045 R |
| 6 | Komisyon / kayma / funding | **KATKI, ana neden DEĞİL** | Sürtünme zararın %16,7'si; sıfırlansa beklenti yine -0,3878 R |
| 7 | Ajan oylarının bağımsız olmaması | **TEST EDİLEMEZ** | `specialist_scores` 1/23 kapanışta mevcut |
| 8 | p_win kalibrasyonu / tersliği | **TEST EDİLEMEZ** | `p_win` 1/23 kapanışta mevcut |
| 9 | A/E vetolarının zararı süzmesi | **DESTEKLENMEDİ** | §4.1; PF düşüyor, beklenti hâlâ negatif |
| 10 | Çıkış challenger'larının MFE tutması | **ÖLÇÜLEMEDİ** | 22/23 tam yol yok |

---

## 6. En olası üç zarar sürücüsü

Her biri için örneklem, güven, kanıt sınıfı ve nedensellik ayrı verilmiştir.

### Sürücü 1 — Kâr geri verme / çıkış zamanlaması
* **Kanıt:** MFE 0,25–1 R kovasında n=10, kazanma **%0**, toplam **-10,82 R** — tek başına
  toplam zararın tamamı kadar. Ortalama giveback 1,4045 R.
* **Örneklem:** 10 (kova), 23 (portföy).
* **Güven:** portföy beklentisi GA'sı sıfırı içeriyor → **DÜŞÜK**.
* **Kanıt sınıfı:** `OBSERVATION_ONLY` (22/23 legacy).
* **Point-in-time:** MFE/MAE kapanışta hesaplanır; **karar anında mevcut değildi**.
* **Nedensel mi:** **HAYIR.** "Kâr korunsaydı kâr kalırdı" totolojiye yakındır; gerçek test
  ileriye dönük çıkış politikasıdır.

### Sürücü 2 — Kazanma oranı ile ödeme oranının uyumsuzluğu
* **Kanıt:** başabaş için %38,5 gerekiyor, ölçülen %21,7; ya da 3,60 ödeme oranı gerekiyor,
  ölçülen 1,60.
* **Örneklem:** 23.
* **Güven:** **DÜŞÜK** (GA sıfırı içeriyor).
* **Kanıt sınıfı:** kanonik defter — **bu aritmetik güvenilirdir**, yorumu değildir.
* **Nedensel mi:** Bu bir **kimlik**, neden değil. Hangi tarafın düzeltileceğini söylemez.

### Sürücü 3 — Yön ve varlık yoğunlaşması
* **Kanıt:** SHORT n=5 → **0 kazanan**, -5,05 USDT; CRYPTO n=14 → -7,47 USDT; açık
  pozisyonların **%91'i LONG**.
* **Örneklem:** 5 / 14 / 11 — **hepsi küçük**.
* **Güven:** **ÇOK DÜŞÜK**.
* **Kanıt sınıfı:** kanonik defter (yön/sembol), korelasyon **ÖLÇÜLMEDİ**.
* **Nedensel mi:** **HAYIR.** Aynı yönde 11 pozisyon tek bir piyasa hareketinin
  tekrarlanmış bahsi olabilir; bu ölçülmemiş bir risktir, kanıtlanmış bir zarar nedeni değil.

---

## 7. Dürüst sonuç

1. **23 işlem sonuç çıkarmak için yetersizdir.** Beklenti GA'sı sıfırı içeriyor.
2. Maliyetler zararı açıklamıyor (sıfırlansa bile beklenti negatif).
3. Mevcut giriş filtrelerinin hiçbiri tarihsel örneklemde beklentiyi pozitife çevirmiyor;
   A/E kombinasyonu profit factor'ü **düşürüyor**.
4. En güçlü *gözlem* kâr geri vermedir, ama bu geriye dönük ve büyük ölçüde totolojiktir;
   **ileriye dönük ölçüm** gerekir.
5. Bu yüzden bir sonraki adım yeni bir strateji değil, **var olan politikaların adil,
   ileriye dönük, izole bir PAPER yarışmasıdır** (bkz.
   `docs/PROFITABILITY_EXPERIMENT_V1.md`).

**Bu belge hiçbir politikanın kârlı olacağını iddia etmez.** Bir challenger'ın tek bir
kaybedeni elemesi başarı sayılmaz.
