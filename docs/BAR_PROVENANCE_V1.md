# Bar Provenansı V1 — giriş öncesi bir bar, pozisyona dokunamaz

**Durum:** onarıldı, testlerle kilitlendi, PAPER dağıtımına hazır.
**Taban:** `1c4cba1` · **Onarım:** `a1d5892` (dal `feature/evidence-repairs-v1`)
**Ölçüm betikleri:** `scripts/research/mfe_recon.py`, `scripts/research/mfe_source.py`,
`scripts/research/open_mfe.py` (hepsi Binance USD-M public API'sinden gerçek 1m/1h bar çeker,
yanıtları diske önbelleğe alır).

---

## 1. Kusur

`engine_v3._marks()` her turda, önbellekteki 1h çerçevesinin **son kapanmış barının** high/low
değerlerini defterin `tick()` çağrısına veriyordu:

```python
hi, lo = float(h1["high"].iloc[-1]), float(h1["low"].iloc[-1])
```

Çerçeveler `drop_unclosed_last_bar()` ile oluşuyor, yani son satır **kapanmış** bir bardır ve
penceresi `[bar_open, bar_open + 1h)`'dir. Pozisyon 16:24'te açıldıysa, 16:34'teki turda son
kapanmış bar **15:00–16:00**'dır: uçları pozisyon **henüz yokken** oluşmuştur.

Defter bu uçları iki yerde kullanıyordu:

* `pos.mae_pct` / `pos.mfe_pct` güncellemesi,
* stop, hedef ve likidasyon **tetikleri**.

Motorda zaten bir koruma vardı ama yalnız **aynı turda açılan** pozisyonlar için uçları siliyordu
(`for desc in opened: marks[sym] = TickData(last=..., mark=...)`). Bir sonraki tur korumasızdı.
Tarama her turda yapılmadığı için (`--scan-every 2`) çerçeve daha da bayat olabiliyordu.

## 2. Kanıt — 29 kapanmış işlem, gerçek 1m barlarla

Pozisyonun ömrü (`opened_at`, `closed_at`) içindeki **bütün** 1m barların uçlarından gerçek MFE/MAE
hesaplandı ve deftere kaydedilenle karşılaştırıldı.

| | adet |
|---|---:|
| kayıtla birebir tutan (±0.05 puan) | 17 |
| **fiziksel olarak imkânsız (kayıtlı > gerçek)** | **5** |
| eksik ölçülmüş (kayıtlı < gerçek) | 6 |
| MFE = 0.00 taban etkisi (kusur değil) | 1 |

Beş imkânsız değerin **beşi de** giriş öncesi bir 1h barının ucuyla **kuruşuna kadar** eşit:

| İşlem | Sembol | Kayıtlı MFE | Gerçek MFE | Kaynak bar | Girişe uzaklık |
|---|---|---:|---:|---|---|
| F00015 | STX/USDT | 1.57 | **−0.04** | 02:00 1h | 5.1 sa önce |
| F00022 | SUI/USDT | 2.01 | 0.97 | 23:00 1h (low 0.6957) | 1.5 sa önce |
| F00025 | SPCX/USDT | 1.31 | 0.27 | 15:00 1h (high 145.00) | 1.6 sa önce |
| F00034 | NVDA/USDT | 1.13 | 0.73 | 14:00 1h (high 234.87) | 1.0 sa önce |
| F00020 | AAPL/USDT | 0.44 | 0.35 | 15:00 1h (high 322.48) | 1.4 sa önce |

STX en açık örnek: işlem **hiç artıya geçmedi** (gerçek MFE −0.04 %), buna rağmen deftere +1.57 %
yazılmıştı.

Üç işlemde MAE de gerçekten daha kötü kaydedilmişti (F00001 −5.06 / −4.83, F00002 −15.16 / −14.50,
F00020 −1.92 / −1.85). Aynı mekanizma, ters yön.

**Kusur olmayanlar — açıkça ayrıldı:**
* F00030 NATGAS `mfe_pct = 0.00`, gerçek −0.20. `mfe_pct` sıfırdan başlar ve `max()` ile güncellenir,
  yani negatife inemez. Bu bir **taban etkisidir**, bozulma değil.
* Altı işlemdeki eksik ölçüm (en büyüğü KORU −4.50 puan) uçların hiç gelmediği `last_only` tiklerden
  gelir; bu onarımın konusu değildir, ayrı iş olarak açıktır.

## 3. Canlı etki — başa-baş kuralı

`breakeven_at_mfe_r = 1.0` tam bu alanı okur. Dağıtım anındaki 14 açık pozisyon gerçek 1m barlarla
tek tek denetlendi:

| | Sonuç |
|---|---|
| Başa-baş kuralı **ateşlenen** 3 pozisyon | **üçü de haklıydı**: F00027 CL gerçek 1.70R, F00028 BZ 1.83R, F00029 GOOGL 1.43R (kayıtlı 1.31R, yani **eksik** ölçmüş) |
| Şu anda **şişik** MFE taşıyan pozisyonlar | F00038 NATGAS 0.65R kayıtlı / **0.12R gerçek**; F00043 GPS 0.66R kayıtlı / **0.21R gerçek** |
| Diğer 9 pozisyon | ±0.05 puan içinde |

Yani **hiçbir stop yanlış bir MFE yüzünden taşınmadı** — ama iki açık pozisyon eşiğin yarısına
sahte biçimde yaklaşmış durumdaydı ve bir kirli tik daha erken tetiklemeye yetebilirdi.

**Kapanmış işlemlerde yanlış çıkış yok:** beş şişik kaydın hepsi `exit_reason = "stop"` ile kapandı,
`başa-baş stop` ile değil; `research/replay-fidelity-v1` harness'i de 29/29 çıkış nedenini gerçek
1m barlarla yeniden üretmişti.

## 4. Onarım

`TickData` artık `bar_open` taşır: high/low'un geldiği barın **açılış** zamanı.

```python
def extremes_usable_from(self, opened_at: str | None) -> bool:
    if not self.has_extremes:            return False
    if not self.bar_open or not opened_at: return False   # provenans yok → FAIL-CLOSED
    return from_iso(self.bar_open) >= from_iso(opened_at)
```

Defter (`FuturesLedgerV2.tick`) uçları yalnız bu koşul sağlanınca kullanır; sağlanmazsa `worst` ve
`best` **mark**'a düşer. Sonuç yalnız iki yönde olabilir: MAE/MFE daha az uç, tetikler daha **geç**.
Olmamış bir fiyattan tetikleme artık mümkün değildir.

Uç üreten üç üretim yolu provenansı bildirir:

| Yol | Kaynak |
|---|---|
| `engine_v3._marks()` | çerçevenin `timestamp` sütunu ya da zaman indeksi (`_frame_bar_open`) |
| `ops/gap.py` (kesinti uzlaştırması) | mumun `ts` alanı (açılış); `ts` alanı zaten kapanışı taşıyordu |
| `replay/engine.py` | satırın `timestamp` alanı |

Spot defteri etkilenmez: motor spot tarafına `marks_f = {k: float(v.last)}` yani **yalnız son fiyat**
verir, uç hiç ulaşmaz.

`self.bar_extremes_skipped` sayacı yalnız gözlem içindir; dosyaya yazılmaz, hiçbir karara girmez.

## 5. Testler

`tests/test_bar_provenance_v1.py` — 13 test. Guard yerine `_use_bar = td.has_extremes` konduğunda
**13'ün 7'si düşer** (tautoloji değil; mutasyonla ölçüldü, sonra geri alındı):

* giriş öncesi uç MFE'ye yazılmaz / stop tetiklemez / hedef tetiklemez,
* **aynı** uçlar provenans pozisyonun içine alınınca **tetikler** (differential kanıt),
* gerçek F00020 AAPL vakası,
* provenans yok / çözülemiyor → fail-closed,
* başa-baş kuralı giriş öncesi uçla ateşlenemez,
* uç üreten her üretim çağrısı `bar_open` vermek zorunda (sözleşme testi),
* guard `tick()` içinde MAE/MFE'den **ve** stop tetiğinden **önce** çalışır (sıra testi),
* `_frame_bar_open` iki şemayı da okur, çözemezse boş döner,
* GERÇEK çerçeve boru hattı (`prepare` → `drop_unclosed_last_bar` → göstergeler) provenans üretir.

Mevcut testlerden 6'sı, uçlarını hangi bardan aldıklarını **açıkça** söyleyecek şekilde güncellendi;
kural gevşetilmedi. `test_gap_reconcile._mk_ledger` artık pozisyonu kesinti penceresinden **önce**
açar — gerçek senaryo budur; `now` verilmediğinde açılış duvar saatine kayıyor ve bütün tarihsel
barlar "giriş öncesi" sayılıyordu.

## 5b. Sözleşme testi gerçek bir sessiz gerilemeyi yakaladı

Fail-closed kuralın bedeli şudur: provenans vermeyen bir çağrı yeri **hata vermez**, sessizce
bütün bar uçlarını düşürür ve tetikler yalnız mark'a kalır.

Araştırma dalı (`work/research-c-v1`) birleştirildiğinde `test_10` **üç** çağrı yerini birden
yakaladı: `replay/fidelity.py:448`, `replay/challengers.py:120`, `replay/counterfactual.py:232`.
Üçü de `high`/`low` veriyordu, `bar_open` vermiyordu.

Etkisi ölçüldü — 29 kapanmış işlem, gerçek 1m barlar, sadakat koşumunun üç durumu:

| Karşılaştırma | Farklı alan |
|---|---:|
| taban (`1c4cba1`, guard YOK) ↔ guard var + **provenans YOK** | **78** |
| taban ↔ guard var + **provenans VAR** | **0** |

En kötü tekil vaka ZEC F00007: replay R'si **+0.909 → −0.138**, çıkış fiyatı **790 → 723**.
Yani eksiklik kozmetik değildi; onarım ise **tam olarak nötr** — harness kendi tabanını bit birebir
yeniden üretiyor.

**Devralınan bir rakam doğrulamayı geçmedi.** Harness'in "29/29 çıkış nedeni yeniden üretildi"
dendiği kayıtlıydı; bu anlık görüntüye karşı 1m'de **dokunulmamış taban commit'te 26/29** üretiyor
ve bu sayı bu onarımdan önce ve sonra **aynı**. Üç ıska BZ, BMNR ve MSFT: defterde düz `stop`,
replay'de `başa-baş stop`. Sebebi `be_mfe_from = None` olduğu için harness'in **bugünkü** başa-baş
kuralını, o kural henüz yokken kapanmış işlemlere de uygulaması. Bu harness'in modelleme tercihidir,
sadakat başarısızlığı değildir ve buradaki değişiklikten kaynaklanmaz.

## 6. Kalan iş (bu onarımda kapanmadı)

1. **İki açık pozisyonun kayıtlı MFE'si şişik kalır — kalıntı SAYILDI.** `mfe_pct` koşan bir
   maksimumdur; onarım onu geriye düzeltmez ve defter yeniden yazılmaz. Değer artık sahte biçimde
   **büyüyemez** (bundan sonraki katkılar gerçek barlardan gelir), ama mevcut sapma **kalıcı bir
   ofset** olarak kalır:

   | Pozisyon | Kayıtlı | Gerçek | Kalıcı ofset | Kayıtlı 1.00R olduğunda gerçek |
   |---|---:|---:|---:|---:|
   | F00038 NATGAS | 0.65R | 0.12R | 0.53R | **0.47R** |
   | F00043 GPS | 0.67R | 0.21R | 0.46R | **0.54R** |

   Yani bu iki pozisyonda başa-baş kuralı, amaçlanan eşiğin kabaca **yarısında** ateşleyebilir.
   Etki tek yönlüdür — stop erken sıkışır, asla gevşemez — ve yalnız bu iki pozisyonla sınırlıdır;
   bundan sonra açılan her pozisyon temiz başlar.
2. **Eksik ölçüm (`last_only` tikler) açık.** Pozisyon o turun brief'lerinde yoksa `_marks()` yalnız
   `last_price` verir ve o pencerenin uçları hiç görülmez. Bu, MFE'yi **eksik** ölçer; başa-baş
   kuralını geç tetikler, yani muhafazakârdır. Ayrı iş.
3. `worst_case` config alanı hâlâ ölü (`N16`): saklanıyor, serileştiriliyor, `tick()` içinde hiç
   okunmuyor; likidasyon > stop > TP sırası sabit kodlanmış. Davranış doğru, belge/kod ayrışması
   duruyor.
