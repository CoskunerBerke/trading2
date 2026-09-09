# Replay Sadakati V1 — defterin kendi işlemlerini yeniden üretebiliyor muyuz?

**Dal:** `research/replay-fidelity-v1` (temel `8163a79`, üretimle aynı kod)
**Veri:** 2026-09-09T18:07Z üretim anlık görüntüsü — `futures_ledger.json` (29 kapanmış işlem, 14 açık), `position_path.jsonl`, `entry_snapshot.jsonl`
**Fiyat kaynağı:** Binance USDⓈ-M `fapi` 1m ve 1h mumları + gerçek `fundingRate` geçmişi
**Kod:** `tradingbot/replay/fidelity.py`, `scripts/replay_fidelity.py`, `tests/test_replay_fidelity.py`

Amaç: strateji değişikliklerini ölçmeden ÖNCE, replay'in botun kendi kararlarını ve nakit sonucunu
belgelenmiş tolerans içinde tekrar ürettiğini kanıtlamak. Bu belge o toleransları ve kalan her
uyuşmazlığın nedenini verir. **Hiçbir sayı kayda uydurulmadı**; uyuşmayanlar uyuşmayan olarak durur.

---

## 1. Kurulum — neyi, nasıl yeniden koşuyoruz

* **Aritmetik yeniden yazılmadı.** Giriş, kısmi TP, başa-baş çekme, stop dolumu, komisyon, funding,
  likidasyon — hepsi gerçek `FuturesLedgerV2` (`tradingbot/accounting/`) üzerinden yürür. Replay
  deftere yalnızca **mum besler**. `tests/test_replay_fidelity.py` içindeki *altın test*
  (`test_round_trip_reproduces_a_ledger_written_record_exactly`) defterin kendi yazdığı bir kaydı aynı
  mumlarla yeniden koşup **her alanın birebir** çıktığını gösterir; dolayısıyla aşağıdaki kalan
  farklar muhasebe yolundan değil, YALNIZCA veri (mum/funding/gözlem anı) farkından gelebilir.
* **Ayarlar uydurulmadı.** `fees` (taker %0.05), `slippage` (3 bps), `liq_params`, `tp1_fraction`
  (0.5), `tp_maker` (False) doğrudan defter anlık görüntüsünden okunur (`replay_config_from_ledger`).
  Filtre verilmez: üretimde de doğrulanmamış varsayılan filtre kullanıldı (`price_tick` 0.01,
  `qty_step` 0.001).
* **İleri bakış yok.** Yalnızca `opened_at` dakikasından SONRA açılan mumlar beslenir; giriş mumunun
  giriş öncesi bölümü hiç görülmez (`test_entry_bar_prefix_is_never_fed`).
* **Ufuk:** kayıtlı `closed_at + 72 saat`. Replay daha geç kapatabilir ya da hiç kapatmayabilir; bu
  durumda `closed=False` döner ve rapora `OPEN` olarak girer — tahmin üretilmez.
* **Zaman çözünürlüğü:** 1m (baş sonuç) ve 1h (bar-içi belirsizliğin etkisini ölçmek için).
* **Gözlem noktası (`mark_mode`):** `close` (mum kapanışı, nötr varsayılan) ve `adverse` (mumun
  aleyhte ucu — canlı yoklamanın en kötü noktaya denk gelmesi). Boşluktan geçen stop MARK'tan
  dolduğu için dolum fiyatı bu varsayıma duyarlıdır; ikisi de raporlanır.

### 1.1 Plan geri çıkarımı ve doğrulaması

Defterin `history` kaydı hedefleri saklamıyor. Hedefler şu geometriden gelir ve bu **ölçülerek
doğrulandı**:

```
R0 = |E0 − S|        E0 = karar anı fiyatı (giriş fill'inin ref_price'ı), S = initial_stop
T1 = E0 ± 2·R0       T2 = E0 ± 3·R0
```

Doğrulama üç bağımsız kaynakla yapıldı: `entry_snapshot.jsonl` (14 kabul edilmiş aday),
`position_path.jsonl` (10 kapanmış işlem) ve gerçekleşen TP fill fiyatları (defter hedefi TAM hedef
fiyatından doldurur, dolayısıyla hedef fill'de kayıtlıdır). Kayıtlı hedefler ile formülden
türetilenler **%4·10⁻¹⁴ içinde** aynı. Öncelik sırası kodda: kayıtlı `path` hedefleri → gerçekleşen
TP fill'i → geometri. 29 işlemin dağılımı: **10 `path`, 5 `fill`, 14 `derived`**.

> **Bilinen tek sapma:** F00007 (ZEC) — TP fill'inden çözülen plan girişi 730.27, yürütme anındaki
> piyasa referansı 730.71. Karar ile yürütme arasında fiyat kaymış: plan girişinde %0.06, hedef
> fiyatında **%0.19** fark. Bu işlemde hedef `fill` kaynağından (doğru olandan) alınır. Hedefe hiç
> dokunmamış 14 `derived` işlemde aynı mertebede bir belirsizlik kalır; o işlemlerde fiyat hedefin
> yakınına bile gitmediği için sonucu değiştirmez.

### 1.2 Dağıtım tarihine sadakat — MFE başa-baş kuralı

Anlık görüntüdeki `breakeven_at_mfe_r = 1.0`, `8c99b8f` (Kanıt Onarımı V1) ile **2026-09-09'da**
geldi ve üretimde ilk kez **2026-09-09T11:24:45Z**'de tetiklendi (açık CL/BZ/GOOGL pozisyonlarındaki
`meta.be_by_mfe` damgaları). 29 kapanmış işlemin 27'si bu andan ÖNCE kapandı, yani bu kural olmadan
yaşandı. Baş sonuç bu yüzden `--be-mfe-from 2026-09-09T11:24:45+00:00` ile koşulur: kural yalnızca o
andan sonraki mumlarda çalışır. **Bu ayar oynaması değil, dağıtım tarihidir**; iki koşum da aşağıda
yan yana verilmiştir.

---

## 2. Sonuç — savunulan toleranslar

Baş yapılandırma: **1m mum, `mark_mode=close`, dağıtım tarihine göre config**.

| Alan | Sonuç | Savunulan tolerans |
|---|---|---|
| Giriş fill fiyatı | **29/29 birebir** | 0 — sapma kabul edilmez |
| Miktar (qty) | **29/29 birebir** | 0 |
| Giriş komisyonu | **29/29 birebir** | 0 |
| Likidasyon fiyatı | **29/29 birebir** (≤1e-9) | 0 |
| `exit_reason` | **29/29 eşleşiyor** | 0 uyuşmazlık |
| `r_multiple` | 12/29 ≤0.02 · **22/29 ≤0.05** · **28/29 ≤0.10** · 29/29 ≤0.20 | **±0.10R** (28/29); tek aşan F00007 +0.149R |
| `net_pnl` | 14/29 ≤0.01 · **23/29 ≤0.02** · 24/29 ≤0.05 · **29/29 ≤0.15** USDT | **±0.15 USDT** (29/29) |
| `r_multiple` (funding hariç) | **23/29 ≤0.05** · 28/29 ≤0.10 | ±0.10R |
| `exit_price` | 7/29 birebir · 23/29 ≤%0.10 · **28/29 ≤%0.60** | **±%0.60** (28/29); tek aşan F00007 %1.22 |
| `closed_at` | **29/29 ≤1 saat** (maks 0.97 saat) | ±1 saat |
| `mfe_pct` | 20/29 ≤0.1 pp · 25/29 ≤0.5 pp · **28/29 ≤2 pp** | **±2 pp** — ama §5'e bakın, sorun replay'de değil |
| `mae_pct` | 23/29 ≤0.1 pp · **28/29 ≤0.5 pp** | ±0.5 pp; tek aşan F00007 3.6 pp |
| `bars_held` | **0/29 eşleşiyor**; replay DAİMA ≤ kayıt (ort. −4.1, en fazla −16) | **Tolerans verilemez** — §5, F5 |

**Portföy toplamı (asıl kapı):** kayıtlı net −7.0265 USDT / −10.345R, replay −6.7312 USDT /
−10.014R. Toplam sürüklenme **+0.295 USDT (%4.2)** ve **+0.332R**.

> **Bu ne demek:** Bu temel üzerinde ölçülecek bir strateji değişikliğinin 29 işlemlik bir örnekte
> anlamlı sayılabilmesi için toplam etkisinin **>0.33R / >0.30 USDT** olması gerekir. Bunun altındaki
> farklar replay gürültüsünden ayırt edilemez. Daha ince farklar ancak örneklem büyütülerek veya
> gözlem noktası belirsizliği (§4) kapatılarak ölçülebilir.

### 2.1 Yapılandırma karşılaştırması

| Koşum | `exit_reason` | \|ΔR\|≤0.05 | Toplam replay R |
|---|---|---|---|
| 1m / close / **dağıtım-tarihine-göre** | **29/29** | 22/29 | −10.014 |
| 1m / close / anlık-görüntü config (`be_mfe=1.0` hep açık) | 26/29 | 19/29 | −6.901 |
| 1h / close / dağıtım-tarihine-göre | 29/29 | 20/29 | — |
| 1m / adverse / dağıtım-tarihine-göre | 29/29 | 19/29 | — |
| 1h / adverse / dağıtım-tarihine-göre | 29/29 | 11/29 | — |

Anlık-görüntü config'inde uyuşmayan 3 işlem, kuralın YENİ olmasından: F00004 BZ (−0.983R → +0.015R),
F00014 BMNR (−1.026R → +0.004R), F00019 MSFT (−1.094R → −0.015R). Yani **MFE başa-baş kuralı bu 29
işlemde 3 kaybı başa-başa çevirirdi, toplam +3.11R** — ve bu örnekte hiçbir kazananı erken kesmezdi
(4 TP2 işleminin dördü de 1m çözünürlükte yine TP2'ye gitti). Bu bir karşı-olgusaldır, sadakat
sonucu değildir.

---

## 3. İşlem bazında fark tablosu (1m / close / dağıtım-tarihine-göre)

`ΔR` = replay − kayıt. `ΔRx` = funding iki taraftan çıkarıldıktan sonraki ΔR. `risk` = R'nin paydası (USDT).

| id | sembol | yön | çıkış | kayıt R | replay R | ΔR | ΔRx | Δnet | Δçıkış% | risk | hedef | Δfunding | Δkapanış(saat) | Δbar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| F00001 | SUI | SHORT | STOP | −1.218 | −1.126 | **+0.093** | +0.094 | +0.059 | −0.381 | 0.64 | derived | −0.0007 | +0.01 | −2 |
| F00002 | KORU | SHORT | STOP | −1.044 | −1.027 | +0.018 | +0.015 | +0.018 | −0.191 | 1.00 | derived | +0.0025 | +0.03 | −4 |
| F00003 | FIL | SHORT | STOP | −1.017 | −1.070 | **−0.053** | −0.051 | −0.049 | +0.293 | 0.92 | derived | −0.0025 | −0.80 | −5 |
| F00004 | BZ | LONG | STOP | −0.983 | −1.033 | **−0.050** | +0.004 | −0.019 | +0.011 | 0.37 | derived | −0.0203 | −0.10 | −11 |
| F00005 | XAUT | LONG | BE_STOP | +0.567 | +0.522 | −0.045 | −0.052 | −0.011 | −0.090 | 0.23 | fill | +0.0016 | −0.01 | −16 |
| F00006 | ZRO | LONG | TP2 | +2.366 | +2.366 | −0.000 | −0.000 | −0.000 | 0.000 | 1.00 | fill | −0.0000 | 0.00 | −4 |
| F00007 | ZEC | LONG | BE_STOP | +0.760 | +0.909 | **+0.149** | +0.149 | +0.095 | **+1.222** | 0.64 | fill | 0.0000 | 0.00 | −1 |
| F00008 | LDO | LONG | STOP | −1.016 | −1.016 | −0.000 | +0.000 | −0.000 | 0.000 | 0.86 | derived | −0.0000 | −0.97 | −2 |
| F00009 | AAVE | LONG | STOP | −1.016 | −1.016 | −0.000 | +0.000 | −0.000 | 0.000 | 0.84 | derived | −0.0000 | −0.96 | −2 |
| F00011 | QQQ | SHORT | STOP | −1.015 | −1.067 | **−0.052** | −0.036 | −0.006 | +0.035 | **0.11** | derived | −0.0018 | +0.01 | −4 |
| F00012 | PAXG | LONG | STOP | −1.123 | −1.131 | −0.008 | −0.020 | −0.004 | −0.039 | 0.44 | derived | +0.0053 | −0.01 | −6 |
| F00014 | BMNR | LONG | STOP | −1.026 | −1.006 | +0.020 | +0.011 | +0.017 | +0.095 | 0.85 | derived | +0.0080 | +0.01 | −7 |
| F00015 | STX | LONG | STOP | −0.910 | −0.980 | **−0.071** | **0.000** | −0.105 | 0.000 | 1.48 | derived | **−0.1045** | −0.43 | −4 |
| F00017 | SPY | LONG | STOP | −1.325 | −1.296 | +0.029 | −0.004 | +0.004 | −0.003 | **0.13** | derived | +0.0042 | −0.02 | −1 |
| F00018 | NVDA | LONG | STOP | −1.037 | −1.037 | 0.000 | 0.000 | 0.000 | 0.000 | 0.81 | derived | 0.0000 | 0.00 | 0 |
| F00019 | MSFT | LONG | STOP | −1.094 | −1.070 | +0.024 | +0.035 | +0.011 | +0.080 | 0.44 | path | −0.0053 | −0.29 | −12 |
| F00020 | AAPL | LONG | STOP | −1.118 | −1.070 | +0.048 | +0.048 | +0.018 | +0.089 | 0.37 | derived | +0.0002 | +0.01 | 0 |
| F00021 | XPD | LONG | STOP | −1.053 | −1.078 | −0.025 | −0.025 | −0.017 | −0.090 | 0.69 | derived | 0.0000 | 0.00 | 0 |
| F00022 | SUI | SHORT | STOP | −1.086 | −1.022 | **+0.065** | +0.062 | +0.129 | −0.478 | 2.00 | path | +0.0054 | −0.04 | −7 |
| F00023 | BZ | LONG | TP2 | +2.429 | +2.441 | +0.012 | −0.000 | +0.006 | 0.000 | 0.48 | fill | +0.0057 | −0.00 | −1 |
| F00024 | CL | LONG | TP2 | +2.415 | +2.440 | +0.025 | **0.000** | +0.013 | 0.000 | 0.53 | fill | +0.0133 | −0.18 | −1 |
| F00025 | SPCX | LONG | STOP | −1.125 | −1.100 | +0.025 | +0.025 | +0.009 | +0.057 | 0.38 | path | 0.0000 | −0.01 | −3 |
| F00026 | AAPL | LONG | STOP | −1.070 | −1.070 | 0.000 | +0.005 | +0.000 | +0.011 | 0.40 | path | −0.0020 | −0.02 | −7 |
| F00030 | NATGAS | LONG | STOP | −1.060 | −1.060 | 0.000 | 0.000 | 0.000 | 0.000 | 0.72 | path | 0.0000 | −0.01 | 0 |
| F00031 | LTC | LONG | TP2 | +2.410 | +2.415 | +0.005 | 0.000 | +0.005 | 0.000 | 0.95 | path | +0.0050 | +0.01 | −4 |
| F00032 | BNB | LONG | TP2 | +2.411 | +2.418 | +0.007 | 0.000 | +0.005 | 0.000 | 0.68 | path | +0.0047 | −0.22 | −2 |
| F00034 | NVDA | LONG | STOP | −1.127 | −1.094 | +0.033 | −0.002 | +0.018 | −0.004 | 0.53 | path | +0.0184 | −0.16 | −4 |
| F00036 | ONDO | LONG | STOP | −1.117 | −1.030 | **+0.087** | +0.087 | +0.100 | +0.519 | 1.14 | path | +0.0002 | −0.17 | −6 |
| F00039 | XAUT | SHORT | STOP | −1.123 | −1.126 | −0.003 | −0.002 | −0.001 | +0.003 | 0.38 | path | −0.0004 | +0.01 | −3 |

**Tamamen birebir yeniden üretilen 4 işlem** (Δçıkış fiyatı = 0, |ΔR| ≤ 5·10⁻⁴, |Δnet| ≤ 5·10⁻⁴
USDT): F00008 (LDO), F00009 (AAVE), F00018 (NVDA), F00030 (NATGAS). F00006 (ZRO) pratikte birebir:
ΔR ≈ 2.6·10⁻⁶. Çıkış fiyatı tam tutan işlem sayısı 7: yukarıdakilere F00015 (STX), F00031 (LTC),
F00032 (BNB) eklenir — bunlarda kalan fark yalnız funding kaleminden gelir.

---

## 4. Uyuşmazlıkların teşhisi — her biri

|ΔR| > 0.05 olan **7 işlem** ve nedenleri:

### (A) Gözlem noktası / boşluktan geçen stop dolumu — 5 işlem
`F00001 SUI (+0.093)`, `F00036 ONDO (+0.087)`, `F00022 SUI (+0.065)`, `F00003 FIL (−0.053)`,
`F00007 ZEC (+0.149)`

Defter, stop bir mum içinde delinip mark stop'un ötesine geçtiğinde **stop seviyesinden değil,
MARK'tan** doldurur (`futures_ledger.py` `tick()`). Canlı motorun mark'ı ~60 sn'de bir alınan bir
**anlık son fiyat**; replay'in mark'ı **1m mumun kapanışı**. İkisi aynı dakikada bile farklı
noktalardır. Ölçülen etki: çıkış fiyatı farkı %0.29–%1.22.

* **F00007 ZEC** en uç örnek: canlı, başa-baş stop'u (~731.9) 704.03'te gözlemleyip oradan doldurdu
  (%3.8 boşluk); replay aynı geçişi mum kapanışında daha iyi bir fiyatta yakaladı. `mark_mode=adverse`
  ile aynı işlem −0.207R'ye düşüyor — yani kayıtlı değer iki varsayımın arasında.
* **F00036 ONDO** ters yön: replay, canlının hiç görmediği bir 1m dibini gördü ve stop'u boşluğa
  düşmeden TAM seviyeden doldurdu (kayıt: 10 dk sonra 0.3663'ten).
* `mark_mode=adverse` bu sınıfı sistematik olarak **kapatmıyor**: kayıtlı R, 29 işlemin yalnız
  5'inde [close, adverse] aralığının içinde. Yani canlı gözlem noktası iki uçtan da dışarı
  taşabiliyor — kapatmak için tick verisi gerekir, mum yeterli değil.

### (B) Funding modeli farkı — 2 işlem (ve 6 işlemde daha ölçülebilir katkı)
`F00015 STX (−0.071 → funding çıkarılınca 0.000)`, `F00004 BZ (−0.050 → +0.004)`

Canlı motor, funding oranı olarak **o anki tek bir oranı** kaçırılan BÜTÜN settlement'lara uygular;
replay gerçek tarihsel `fundingRate` kayıtlarını kullanır. Bu iki işlemde fark **tamamen** budur:
funding iki taraftan çıkarıldığında ΔR sıfıra iner. Ayrıntı §6/F2–F3.

### (C) Küçük risk paydası — 2 işlem
`F00011 QQQ (−0.052)`, `F00017 SPY (+0.029)`

Bu işlemlerin R paydası 0.11 ve 0.13 USDT. QQQ'da nakit farkı yalnızca **0.006 USDT** ama R'ye
bölününce 0.052 oluyor. Sadakat kusuru değil, ölçek etkisi: `net_pnl` toleransı (±0.15 USDT) bu
işlemlerde `r_multiple` toleransından çok daha anlamlı.

**Kalan 22 işlem** |ΔR| ≤ 0.05 içinde; bunların 7'si birebir.

---

## 5. Bar-içi belirsizlik gerçekte ne kadar önemli?

Klasik "aynı mumda hem stop hem hedef" belirsizliği **hiç oluşmadı**: 1m'de beslenen 238.181 barın,
1h'de 3.971 barın **hiçbirinde** stop ve sıradaki hedef aynı mumun menzilinde değildi. Sebep geometrik:
stop 1R aşağıda, ilk hedef 2R yukarıda; bunu tek bara sığdırmak 3R genişliğinde bir mum ister. Bu
işlem kümesinde öyle bir mum yok. Yani `worst_case` politikası (aynı mumda stop+hedef ⇒ STOP)
**doğrudur ama bu örnekte hiç devreye girmemiştir**.

Bar-içi sıralama yine de bir kez belirleyici oldu — **farklı bir kuralda**: MFE tabanlı başa-baş.
Anlık-görüntü config'iyle 1h çözünürlükte F00006 (ZRO) `BE_STOP` veriyor (+0.14R), 1m çözünürlükte
`TP2` (+2.37R): 1h barın içinde kural önce kurulup sonra vuruluyor, gerçekte ise fiyat önce hedefe
gitti. **2.22R'lik bir hata, yalnızca çözünürlükten.** Dağıtım-tarihine-göre koşumda 1m ve 1h
`exit_reason`'ları 29/29 aynı, ama `|ΔR|` 1h'de ortalama 0.049, en fazla 0.285 daha kötü.

**Sonuç:** 1m zorunludur. 1h replay çıkış nedenini bu örnekte doğru bulur ama R'yi 0.28'e kadar
kaydırır ve MFE tabanlı kural açıkken çıkış nedenini de bozar.

---

## 6. Canlı defterin muhasebesinde şüpheli görünenler (BULGU — üretimde düzeltilmedi)

Bunlar replay kusuru değildir; replay bunları **ortaya çıkardı**. Hiçbiri üretimde değiştirilmedi.

**F1 — `worst_case` bayrağı ölü ayardır.** `FuturesLedgerV2.__init__` alıyor, JSON'a yazıyor
(`futures_ledger.py:132, 590, 624`), fakat `tick()` içinde **hiç okunmuyor**; likidasyon > stop > TP
sırası koda gömülü. Davranış `worst_case=True` ile aynı olduğu için sonuç hatası yok, ama bayrağı
`False` yapmak hiçbir şeyi değiştirmez — yanıltıcı.

**F2 — Oran gelmeyen sembolde funding sessizce SIFIR kalıyor.** `engine_v3.py`'de `funding` sözlüğü
yalnızca market ajanı `funding_pct` bildirirse dolar; `FundingSchedule.accrue` oran bulamayınca
(tasarım gereği) durur ve watermark'ı ilerletmez. Ölçüldü: **29 kapanmış işlemin 10'unda kayıtlı
funding tam 0.000000** (F00002, F00014, F00018, F00019, F00020, F00021, F00024, F00025, F00026,
F00030) — aynı pencerelerdeki gerçek Binance funding'i toplam +0.0168 USDT. Fail-closed olduğu için
sessiz kayıp yok, ama tutma maliyeti sistematik olarak eksik.

**F3 — Funding, tek bir anlık oranla bütün settlement'lara uygulanıyor.** En uç ölçüm F00015 (STX,
4.7 gün): kayıtlı +0.1446 USDT, gerçek +0.0401 → **3.6 kat fazla alacak**. 29 işlem toplamı: kayıtlı
+0.0674 USDT, gerçek +0.0045 USDT. Mutlak büyüklük küçük, fakat uzun tutuşta ve daha büyük
sermayede ölçek büyür; şu anda `net_pnl` farklarının en büyük tek kalemi.

**F4 — `mfe_pct` / `mae_pct` güvenilir değil (en ciddi bulgu).**
* **3 işlemde kayıtlı MFE fiziksel olarak imkânsız** — tutma penceresi boyunca ne `fapi` ne de spot
  o fiyata değmiş:
  | işlem | kayıtlı MFE | gerektirdiği fiyat | gerçek uç (fapi / spot) |
  |---|---|---|---|
  | F00015 STX | %1.57 | 0.28440 | 0.2799 / 0.2804 (**%1.4 altında**) |
  | F00022 SUI | %2.01 | 0.69573 | 0.7031 / 0.7038 (**%1.0 üstünde**) |
  | F00025 SPCX | %1.31 | 144.995 | 143.50 (**%1.0 altında**) |
* **10 işlemde kayıtlı MFE gerçek hareketi eksik ölçüyor** — en uç F00002 (KORU): kayıtlı %3.68,
  gerçek %8.18. Bu yönün nedeni bilinen: yol kayıtlarının **%79'u `last_only`** (bar uçları yok),
  yani canlı ölçüm nokta örneklemesi.
* **Neden önemli:** yeni dağıtılan MFE tabanlı başa-baş kuralı ve çıkış-geri-verme (giveback)
  metrikleri **tam da bu alanı** girdi olarak kullanıyor. Alan hem eksik hem de bazen hiç olmamış
  fiyatları gösteriyorsa, o kuralın tetiklenmesi de güvenilmez.

**F5 — `bars_held` bir bar sayısı değil.** `bar_advance`, kripto + hisse + emtia karışık evrende
`max(b.last_bar_4h)` değiştiğinde tetikleniyor; duvar saatiyle 4h sınırına bağlı değil. Ölçüldü:
**25/29 işlemde kayıtlı `bars_held` gerçek 4h sınır sayısından büyük**, replay'e göre ortalama 4.1,
en fazla 16 fazla (F00005 XAUT: kayıt 70, replay 54). Süre bazlı hiçbir analizde bu alan
kullanılmamalı.

**F6 — Çıkış zaman damgası ile çıkış fiyatı tutarsız olabilir.** Boşluktan geçen 20 çıkışın
**5'inde** kayıtlı referans fiyat, kayıtlı `closed_at` dakikasının 1m barında yok; ±1–2 dakika
ötede beliriyor (F00001, F00002, F00012, F00005, F00020). Kök neden tur yolu: `tour()` içinde `now`
turun başında alınıyor, `_marks()` turun ortasında çalışıyor. Motor bu kusuru `position_path` için
zaten düzeltmiş (`snap_now = utc_now()`), fakat **defter tick'i hâlâ tur-başı `now`'u kullanıyor**.

**F7 — Doğrulanmamış fiyat adımı girişi ciddi bozuyor** (kodda zaten uyarı var:
`R_UNVERIFIED_PRECISION`; burada büyüklüğü ölçüldü). `price_tick=0.01` varsayılanıyla giriş fill'i
karar fiyatından medyan %0.033 aleyhte, **ortalama %0.201, en fazla %1.670** (F00015 STX:
0.2754 → 0.28). **8/29 işlemde kayma modelinin (3 bps) iki katından fazla.** Yani gerçek giriş
maliyetinin baskın kalemi kayma modeli değil, tik yuvarlamasıdır.

---

## 7. Sadakatin kalan sınırları — bu rapor neyi KANITLAMAZ

1. **Gözlem noktası kapatılamadı.** Mum verisi canlı botun 60 sn'lik nokta örneklemesini yeniden
   üretemez; boşluktan geçen stop dolumlarında ±%0.5 mertebesinde bir belirsizlik kalıcıdır. Kapatmak
   için tick/aggTrade verisi gerekir.
2. **Fiyat kaynağı özdeş değil.** Canlı motorun 1h çerçeveleri ve son fiyatları her zaman `fapi`
   klines ile örtüşmüyor (F4'teki 3 imkânsız MFE bunun kanıtı). Bu, replay'in kontrolü dışındadır.
3. **Portföy etkileşimi yok.** Her işlem izole bir defterde koşar (`max_positions=1`, tavan kapalı).
   Marj rekabeti, pozisyon tavanı ve giriş sırası yeniden üretilmez. Girişler zaten kayıtlı olduğu
   için bu, kapanmış işlem sadakatini etkilemez; **fakat strateji değişikliği girişleri değiştirecekse
   bu temel yetmez** — o zaman portföy düzeyinde bir replay gerekir.
4. **14 açık pozisyon kapsam dışı.**
5. **Örneklem 29 işlem.** Yukarıdaki oranların güven aralıkları geniştir.

---

## 8. Yeniden koşma

```bash
# baş sonuç (dağıtım tarihine göre, 1m + 1h, kötümser mod dahil)
python scripts/replay_fidelity.py \
  --snapshot <anlik_goruntu_dizini> \
  --adverse --be-mfe-from 2026-09-09T11:24:45+00:00 \
  --out data/rows_asof.json

# anlık görüntü config'i (MFE başa-baş hep açık) — karşı-olgusal
python scripts/replay_fidelity.py --snapshot <dizin> --out data/rows_snapcfg.json

python -m pytest tests/test_replay_fidelity.py -q     # 24 test, ağ gerekmez
python -m ruff check tradingbot/replay/fidelity.py scripts/replay_fidelity.py
```

Mumlar `data/replay_bars/` altına onbelleklenir (`.gitignore`'da). İlk koşum ~200 `fapi` isteği
çeker; sonrakiler ağa çıkmaz.
