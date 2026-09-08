# PROFITABILITY_RESEARCH_ACCELERATION_V1 — çevrimdışı araştırma hattı ve ölçülen sonuçlar

**Durum:** OFFLINE RESEARCH · üretim kodu/config'i DEĞİŞMEDİ · VPS salt okunur ·
`applied_to_canonical = false` · terfi kapıları AYNEN duruyor (≥50 karşılaştırılabilir kapanış,
≥30 gün) · offline sonuç hiçbir ileri sayacı ARTIRMAZ.

> **Bu belge kâr vaat etmez.** Hiçbir politika kârlı kanıtlanmadı, hiçbir strateji seçilmedi,
> hiçbir eşik bir sonucu güzelleştirmek için ayarlanmadı. Aşağıdaki sayıların bir kısmı
> ARİTMETİK KİMLİK (kesin), bir kısmı GÖZLEM (belirsiz), bir kısmı RETROSPEKTİF SİMÜLASYONdur
> (ileri kanıt DEĞİL).

> ### ⚠ DÜZELTME UYARISI (2026-09-08)
>
> Bu belgenin bazı ifadeleri `docs/PROFITABILITY_DUAL_EDGE_V2.md` §9 ile **düzeltilmiştir**.
> Gövde, eski kanıtı korumak için OLDUĞU GİBİ bırakılmıştır. Kısaca:
>
> * §2/§5.1 — `p_win` "model/kalibre çıktı" DEĞİLDİR: champion `p_win_lr` modeli yok,
>   Platt kalibratörü fit edilmemiş (`n_fit=0`); değer 0.5·hiyerarşik önsel + 0.5·legacy
>   tahmin harmanıdır (C-03).
> * §5.1 — kimlik testi `gross_expectancy_r` üzerindedir; `conservative_net_edge_r` ondan
>   belirsizlik + yumuşak ceza düşülerek TÜRETİLİR. Basit ağırlıklı beklenti "conservative
>   NET edge" değildir (C-01). "256/256 ve 0/404" farklı paydalardı; kapı olasılığı artık
>   404/404 için geri kazanıldı (C-02).
> * §5.2/RL-02 — kalibrasyon hedef-uyumsuz bir etiketle ölçülmüştü; hedef-uyumlu etiketle
>   ve geçmiş-yalnız taban çizgisiyle yeniden koşuldu (C-04).
> * §5.3/RL-03 — "sadakat 1.0" KATEGORİK uyumdur; ekonomik büyüklük karşılaştırmasında
>   0 EXACT / 18 WITHIN_TOLERANCE / 7 MISMATCH (C-05).
> * §5.4/RL-04 — "düzeltilmiş sıralama" nitelemesi kaldırıldı (C-06).
> * Gerçekleşen zarar bir OLGUdur; güven aralığı yalnız popülasyon beklentisi hakkındadır (C-07).
>
> v1 sayısal sonuçları yeniden koşuldu ve **birebir aynı** çıktı (yalnız `research_id`
> kod SHA'sına bağlı olduğu için `d0cf2a0ca55860ad` → `10297e21231b9e1d`).

Koşu kimliği: `research_id = d0cf2a0ca55860ad` · kod `12db804` · export cutoff
`2026-09-07T21:16:41Z`.

---

## 1. Neden bu hat gerekliydi

`PROFITABILITY_ROOT_CAUSE_V1` 23 kapanışla çalışabiliyordu ve iki duvara çarpıyordu:

| Duvar | Eski durum | Şimdi |
| --- | --- | --- |
| Çıkış karşı-olgusu | `exit_eval.json`: `n_path_complete = 1/23` → `INSUFFICIENT_EXIT_SAMPLE` | **25/25** kapanış gerçek 15dk barlarla yeniden yürütüldü |
| Reddedilen fırsatlar | şampiyon-yalnız deney onları GÖREMİYOR | **404** tekilleştirilmiş fırsat ileri simüle edildi |
| Bar yolu | `position_path.jsonl` yalnız 2026-09-02 sonrası ve yalnız `mark` (bar içi uç YOK) | bağımsız piyasa geçmişi (Binance public klines, önbellekli) |

Hızlandırmanın kaynağı YENİ BİR MOTOR DEĞİL, iki eksik köprüdür:
`trade_memory` giriş kaydındaki plan geometrisi (stop + hedefler, **36/36** işlemde mevcut) ve
gerçek bar geçmişi. İkisi birleşince mevcut `quant/exit_challenger.py` motoru bütün kapanışlarda
çalışabiliyor.

---

## 2. Yeniden kullanım haritası (kaynaktan, tahmin değil)

| Katman | Yeniden kullanılan mevcut kod | Ne olduğu |
| --- | --- | --- |
| Çıkış simülasyonu | `quant/exit_challenger.py` (`simulate_exit`, `compare_exit_policies`) | **elle yazılmış deterministik kural** — fit edilmiş model DEĞİL |
| Çıkış politikaları | `CHAMPION_AS_IS` + `EARLY_PARTIAL_BE`, `VOL_TRAILING`, `TIME_STOP` | önceden tanımlı, grid araması YOK |
| Metrikler | `quant/attribution.py::group_metrics` | deterministik bootstrap (kendi LCG'si) |
| Fold/leakage | `quant/walkforward.py::make_folds`, `replay/engine.walk_forward_windows` | purge + embargo, fail-closed |
| Seçicilik | `quant/selectivity.py` | eşik YALNIZ train'de fit edilir (bu koşuda ÇALIŞTIRILMADI, bkz. §6) |
| Ekonomi | `accounting/*`, kanonik `futures_ledger.json` `entries` defteri | ölçülmüş, mutabık |
| Olasılık | `learn/model.py::HierarchicalRate` (Beta shrinkage), `learn/learner_v2.py` (`LogisticModel`, Platt calibrator) | **fit edilmiş istatistiksel model VAR** |
| Ön tahmin | `coinhead/head.py:354` `p_win = 0.5 + 0.25·conf·[|score|≥eşik]` | **elle yazılmış formül**, [0.5, 0.75] aralığı |
| LLM | `llm.provider = noop`, `mode = POSTMORTEM_ONLY` | **kapalı**; kapalı olması istatistiksel model olmadığı anlamına GELMEZ |
| SHADOW görüşler | `research_policy`, `entry_selectivity`, `mtf_eval`, `weekly_structure` | hepsi `applied = 0` — karar yolunu etkilemiyor |

Yeni yazılan **yalnız** `tradingbot/research/` paketidir (adaptörler + protokol + rapor).
İkinci bir motor, ikinci bir pano, yeni bir ajan hiyerarşisi ya da strateji arama platformu
kurulmadı.

---

## 3. Export ve kapsam

Sınırlı, tutarlı, salt okunur export (`/tmp` altında üretildi, indirildikten sonra silindi):
JSONL dosyaları bayt-sınırlı bir önekle donduruldu (eşzamanlı append satırı YIRTMAZ), JSON
dosyaları okuma öncesi/sonrası sha256 ile kararlılık kontrolünden geçti (**hepsi `SAME`**).
Bot durdurulmadı.

| Ölçüm | Değer |
| --- | --- |
| Kapanmış işlem | **25** |
| Açık pozisyon | **11** |
| Değişmez giriş snapshot'ı olan kapanış | **3 / 25** |
| Karar anı planı (stop + hedef) olan kapanış | **25 / 25** |
| Tam gözlenmiş yol (`position_path`) olan kapanış | **3 / 25** (kısmi 3, hiç yok 19) |
| Giriş kararı kaydı | 2053 satır → **404** tekil fırsat (115 sembol, 6 gün) |
| Giriş kaydı penceresi | 2026-09-02T19:06Z → 2026-09-07T21:08Z (**≈5,9 gün**) |

Önceki denetimin "23 kapanışın 22'sinde değişmez giriş snapshot'ı yok" bulgusu **doğrulandı ve
güncellendi**: 25 kapanışın **22'sinde** hâlâ yok. Ancak "tam yol yok" duvarı artık bağlayıcı
değil — plan geometrisi + bağımsız piyasa geçmişi yeterli.

---

## 4. Ekonomi mutabakatı (CANONICAL_OBSERVED, kesin)

| Kalem | USDT |
| --- | --- |
| Cüzdan değişimi (100 → 98.2226) | **-1.7773933358** |
| Defter kalemleri FEE + FUNDING + PNL | **-1.7773933358** |
| **Mutabakat artığı** | **-0.0** ✔ |
| Kapanmış 25 işlemin neti | -4.2474225161 |
| Açık pozisyonların GERÇEKLEŞMİŞ kısmi kârı | +2.5388176485 |
| Açık pozisyonların MTM'i (ayrı) | +2.4078043 |
| Ücret / funding / kayma | 0.4103 / +0.1131 (net ALINDI) / 0.8920 |

Kayma dolum fiyatının içindedir; ayrıca düşülmez. TP1 kısmi çıkışları kapanmış işlemin
`net_pnl`'ine zaten dahildir. Spot (stopsuz BNB holding) AYRI raporlanır ve futures stop-risk
kovasına girmez.

**Sonuç:** zarar bir muhasebe kusuru değildir.

---

## 5. Bulgular

### 5.1 RL-01 — Ekonomik kapı, öğrenilmiş olasılığı GÖRMÜYOR (DEFECT, doğrulandı)

Sınanan kimlik: `gross_expectancy_r = p·avg_win_r − (1−p)·|avg_loss_r|`.

| `p` adayı | Kimliği sağlayan kayıt |
| --- | --- |
| `p_win` (model/kalibre çıktı, snapshot'a yazılan) | **0 / 404** |
| `p_win_prior` (planın taşıdığı ön tahmin) | **256 / 256** (azami sapma 5e-07) |

Yani sıralama, boyutlandırma ve `tradeable` kararını veren tek büyüklük —
`conservative_net_edge_r` — **`p_win_prior`den** türüyor. `p_win_prior`, `learn/snapshot.py:505`
üzerinden planın `p_win`'idir ve o da `coinhead/head.py:354`'teki elle yazılmış
`0.5 + 0.25·conf` formülüdür (aralık [0.5, 0.75]).

Kaynak sırası her koşuda yeniden ölçülür (`verify_gate_probability_order`):

* `engine_v3.py:827` → `self._assess_opportunities(...)` (ekonomik kapı)
* `engine_v3.py:875` → `d.p_win = b.p_win` (öğrenilmiş/etkilenmiş olasılık)

Kapı **önce** çalışıyor. Kodun kendi yorumu da bunu söylüyor:
*"`d.p_win` burada hala HEAD on tahmini -- model henuz ezmedi."*

Ölçülen fark:

| Büyüklük | Ortalama |
| --- | --- |
| `p_win_prior` (kapıyı süren) | **0.6223** |
| `p_win` (model, kayda yazılan) | **0.2891** |
| Fark | **+0.3333** |
| Korelasyon | **0.0135** (pratikte ilişkisiz) |
| Gerçekleşen kanonik kazanma oranı | **0.28** (7/25) |

Handoff'taki F00036 örneği (`p_win=0.293`, `avg_win_r=1.851`, `conservative_net_edge_r=0.677`)
bu yüzden çelişkili GÖRÜNÜYOR ama aritmetik kusur değildir: iki alan **aynı dağılımı
tanımlamıyor**. `p_win=0.293` ile ağırlıklı beklenti −0.1646 R'dir; kayıttaki 0.878907 ise
`p_win_prior=0.659` ile hesaplanmıştır.

**Bu bir düzeltme reçetesi değildir.** Ölçülen tek şey, kapının hangi girdiyi kullandığıdır.
Model olasılığını kapıya bağlamak neredeyse bütün adayları negatif beklentiye düşürür
(0.289×~1.95 − 0.711 ≈ −0.15 R) — yani sistem büyük olasılıkla HİÇ işlem açmaz. Bu, ayrı bir
karar konusudur ve bu görevin kapsamı dışındadır.

### 5.2 RL-02 — İki skor da bu kohortta ayrıştırıcı değil (NEGATIVE)

| Skor | n | Brier | Ortalama skor | Gerçekleşen kazanma | Kalibrasyon açığı |
| --- | --- | --- | --- | --- | --- |
| `p_win` (model) | 238 | 0.3579 | 0.2492 | 0.5630 | **-0.3138** |
| `p_win_prior` | 142 | 0.2613 | 0.6298 | 0.5563 | **+0.0734** |

Kova tablosunda **sıralama TERS**: `p_win`'in en düşük kovası %61.7, en yükseği %36.2 kazandı.
`p_win_prior` de zayıf ters eğilim gösteriyor. İki skor FARKLI alt kümelerde ölçüldü
(`p_win_prior` NO_TRIGGER satırlarında yok) — doğrudan karşılaştırma bu yüzden sınırlıdır.

### 5.3 RL-03 — Çıkış alternatifleri iyileştiriyor ama gürültüden ayrılmıyor (MIXED)

25/25 kanonik kapanış, gerçek 15dk barlar, ölçülmüş maliyet **0.016173 R/dolum**.
Şampiyon simülasyonu kanonik gerçeği yeniden üretiyor: **işaret uyumu 1.0**, **çıkış nedeni
uyumu 1.0**, ortalama |R| hatası **0.0702**.

| Politika | Beklenti (R) | PF | Kazanma | Eşleşmiş fark | GA95 (sembol kümesi) | Ayrılıyor mu |
| --- | --- | --- | --- | --- | --- | --- |
| CHAMPION_AS_IS | -0.1900 | 0.7444 | 0.28 | — | — | — |
| EARLY_PARTIAL_BE | -0.0867 | 0.8386 | 0.48 | +0.1033 | [-0.1423, +0.3485] | **HAYIR** |
| VOL_TRAILING | -0.0709 | **0.8927** | 0.32 | +0.1191 | [-0.1119, +0.3280] | **HAYIR** |
| TIME_STOP | -0.0259 | **0.7333** ↓ | 0.04 | +0.1641 | [-0.4204, +0.6757] | **HAYIR** |

Şampiyonun kendi beklentisinin bootstrap GA95'i **[-0.6786, +0.3609]** — **sıfırı içeriyor**.
Yani 25 kapanışla negatif beklenti bile hâlâ istatistiksel olarak kanıtlanmış değildir
(`PROFITABILITY_ROOT_CAUSE_V1` §0 ile aynı sonuç, güncel örneklemle).

İki yön birlikte raporlanır:

* **Kurtarılan kayıplar** (yüksek MFE → stop): EARLY_PARTIAL_BE +0.377 R (n=4),
  VOL_TRAILING **+0.935 R** (n=4), TIME_STOP +0.159 R (n=4).
* **Kesilen büyük kazananlar** (net ≥ 2R): EARLY_PARTIAL_BE **-0.600 R** (n=5),
  VOL_TRAILING **-0.152 R** (n=5), TIME_STOP **-2.464 R** (n=5).

TIME_STOP beklentiyi en çok yükseltiyor ama **profit factor'ü ŞAMPİYONUN ALTINA düşürüyor** ve
büyük kazananları ortalama 2.46 R kesiyor. "Bütün stopları sıkılaştır" sonucu bu veriden
ÇIKMAZ.

Olumsuz maliyet senaryosu (dolum başına maliyet ×2): şampiyon -0.2269 R; sıralama değişmiyor,
farklar korunuyor (+0.100 / +0.124 / +0.168).

Bar içi belirsizlik: **12 664 barın 0'ında** aynı bar hem stop'a hem ilk hedefe değiyor →
muhafazakâr sıra varsayımı bu örneklemde HİÇ bağlayıcı olmuyor (sonuç bu varsayımdan bağımsız). Ufuk kuralı: kapanış + min(tutma süresi, 7 gün), export
cutoff ile sınırlı; **şampiyon ve bütün challenger'lar AYNI barları görüyor**.

### 5.4 RL-04 — Sıralama ölçütü rastgeleden ayırt edilemiyor (NEGATIVE)

Sermaye kısıtlı replay (aynı havuz, aynı sonuçlar, YALNIZ sıralama değişiyor), 200 permütasyon:

| Varyant | Alınan | Kapasite reddi | Beklenti (R) | Null medyan | Null GA95 | Yüzdelik | Ayrılıyor mu |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `conservative_net_edge_r` @K=11 | 23 | 215 | 0.4218 | 0.2995 | [0.1153, 0.4625] | 0.945 | HAYIR |
| model `p_win` ile düzeltilmiş @K=11 | 23 | 215 | 0.2633 | 0.2995 | [0.1153, 0.4625] | 0.365 | HAYIR |
| `conservative_net_edge_r` @K=3 | 7 | 231 | 0.0667 | 0.2327 | [-0.204, 0.7686] | 0.230 | HAYIR |
| model `p_win` ile düzeltilmiş @K=3 | 6 | 232 | 0.3128 | 0.2327 | [-0.204, 0.7686] | 0.575 | HAYIR |

Üretim sıralaması K=11'de null dağılımın üst ucuna yakın (yüzdelik 0.945) ama GA'nın İÇİNDE;
K=3'te null medyanın ALTINDA. **Düzeltilmiş sıralama da daha iyi değil** — yani RL-01 bir kusur
olsa bile "düzeltirsek kâr eder" sonucu bu veriden ÇIKMAZ.

Boşta kalan sermaye görünür: 238 sonuçlu fırsattan yalnız 23'ü alınabiliyor, 215'i kapasite
nedeniyle kaçıyor.

### 5.5 RL-05 — Muhasebe tam mutabık (POSITIVE)

§4. Artık sıfır. Zarar bir muhasebe/çift sayım kusuru değildir.

---

## 6. YAPILAMAYAN analiz ve nedeni (kapı GEVŞETİLMEDİ)

**Seçicilik challenger'ı (eşik fit eden giriş filtresi) ÇALIŞTIRILMADI.**

Sızıntısız üç yollu walk-forward, örtüşen sonuç ufku kadar purge + embargo ister. Sonuç ufku 48
saat = 12 adet 4h bar → her sınırda 2 gün, iki sınırda 4 gün. Train(2) + validation(1) + test(1)
+ 4 = **en az 8 gün** gerekiyor; giriş kaydı **5,9 gün**.

`make_folds` fail-closed reddetti. Purge/embargo'yu sonuç ufkundan kısa tutarak yapay fold
üretilmedi. Bu koşuda yalnız **parametresiz** karşılaştırmalar raporlanır: kalibrasyon,
permütasyon null testi, sabit çıkış politikaları.

Denenen bütün varyantlar `protocol.attempted_variants` altında kayıtlıdır (RUN ve BLOCKED
dahil).

---

## 7. Yöntem kararları (önceden kayıtlı)

* **Eşit ufuk kohortu.** Ufku export cutoff'unda kesmek, hızlı çözülen fırsatları öne çıkarırdı
  (kısa-sonuç seçilim yanlılığı). Tam ufku olmayan fırsat KOHORT DIŞIdır; ufuk sonunda
  kapanan pozisyon TANIMLI bir sonuçtur (piyasa fiyatı), sıfır sayılmaz.
* **İki dolum varsayımı.** Birincil `NEXT_BAR_OPEN_FILL` (muhafazakâr, tetik varsayımı yok);
  dayanıklılık `PLAN_TRIGGER_FILL` (plan seviyesi ilk 16 barda GERÇEKTEN işlem görmeliydi,
  görmediyse `UNFILLED`). Dolum kaysa bile stop/hedef mutlak uzaklığı korunur → R BÜYÜMEZ.
* **Tekilleştirme.** `symbol|direction|timeframe|kapanmış bar|setup`; 2053 ham satır → 404
  fırsat. Aynı barın tur tekrarları bağımsız örnek DEĞİLDİR.
* **Enstrüman eşlemesi.** Kararın kullandığı kapanmış 4h barın kapanışı, 15dk barlarla %0.5
  toleransta doğrulanır. Kanonik işlemlerde ayrıca giriş fiyatı doğrulandı: **25/25 MAPPING_OK**.
  ASCII olmayan ticker'lar (ör. `我踏马来了/USDT`) açıkça dışlanır ve sayılır.
* **Belirsizlik.** Eşleşmiş fark bootstrap'ı hem işlem hem **sembol kümesi** düzeyinde; karar
  için GENİŞ olan (küme) esas alınır. Marklar ve politika kopyaları bağımsız örnek sayılmaz.

---

## 8. Bilinen sınırlamalar (dürüst)

1. Giriş kararı verisi **≈5,9 gün**; kohort **%97 LONG** ve tek bir kısa piyasa penceresinde.
   Rejim etkisi kenar etkisinden AYRILAMAZ. Kohort beklentisinin (+0.27 R) kanonik
   beklentiden (-0.24 R) yüksek olması bir kenar bulgusu DEĞİLDİR.
2. Bu kapanışlar ve raporlar önceki oturumlarda incelenmiştir → **DOKUNULMAMIŞ holdout
   DEĞİLDİR**. İleri PAPER doğrulaması bağımsız kalmalı; holdout'a karşı tekrar tekrar ayar
   yapılmamalı.
3. `position_path` yalnız `mark` serisidir (bar içi uç yok) ve 2026-09-02'de başlar; gözlenmiş
   yol analizi 3 tam + 3 kısmi kapanışla sınırlıdır. Çıkış karşılaştırması bu yüzden bağımsız
   piyasa geçmişine dayanır.
4. Tarihsel emir defteri, gerçek spread/derinlik ve model durumları GERİ GETİRİLEMEZ; fiyat
   geçmişi bunları restore etmez.
5. Portföy replay'i R cinsindendir: kaldıraç, marj ve funding modellenmedi.
6. Reddedilen fırsat sonuçları `RETROSPECTIVE_RESEARCH`tir; orijinal snapshot değildir,
   ileri kanıt sayılmaz ve hiçbir terfi kapısını karşılamaz.

---

## 9. Tekrarlanabilir komut

```bash
python -m tradingbot.research.run --export <export_dir> --out <out_dir>
```

* Girdi **salt okunur**; yazım YALNIZ `--out` altındadır. `--out` bir `state` dizini içeriyorsa
  koşu fail-closed reddedilir.
* Önbellek kimliği = girdi sha256'ları + kod SHA + cutoff. Aynı kimlikte ikinci koşu
  `RESEARCH_CACHE_HIT` döner; sonuç ve ders ÇOĞALTILMAZ. `--force` yeniden hesaplar.
* `--offline` hiçbir ağ isteği yapmaz (yalnız bar önbelleği).
* Ölçülen süre: soğuk **220.9 s** (113 bar isteği, 35.4k bar), önbellek sıcakken **~3 s**,
  önbellek isabetinde **~1.5 s**.
* Çıktılar: `research_report.json` (kesin JSON, NaN/Infinity yok), `research_report.md`,
  `research_lessons.json`.

---

## 10. Yazma sınırı (doğrulandı)

| Sınır | Durum |
| --- | --- |
| Üretim kodu / config değişikliği | **YOK** |
| Kanonik state / defter / snapshot / deney dosyası yazımı | **YOK** |
| VPS yazımı | **YOK** — yalnız `/tmp/pfres_export*` üretildi ve **silindi** |
| Deployment / restart / manuel tur / zorlanmış işlem | **YOK** |
| Üretim dersleri / learned-index | **DOKUNULMADI** (araştırma kataloğu AYRI dosyada) |
| Terfi sayaçları | **ARTMADI**; kapılar (≥50 kapanış, ≥30 gün) AYNEN |
| PAPER / gateway=paper / live_order_path=false / ALLOW_LIVE_TRADING=false / kill switch ARMED | **KORUNDU** |

`tests/test_research_acceleration_v1.py` bu sınırları test ediyor: export dizini mtime'ları
koşudan sonra birebir aynı, `state` altına `--out` reddediliyor.

---

## 11. Kanıta dayalı sonraki adım (tek)

`conservative_net_edge_r`'yi hem mevcut `p_win_prior` hem de model `p_win` ile **çift
hesaplayıp**, ikisinin `tradeable` kararını SHADOW olarak kaydeden bir ölçüm ekle. Karar
değişmez, hiçbir işlem etkilenmez; ileriye dönük olarak "hangi olasılık daha iyi kapı üretiyor"
sorusu ≥50 karşılaştırılabilir kapanışta yanıtlanabilir hâle gelir.

Bu adım kâr vaat etmez; RL-01'i ileri kanıta çevirir ya da çürütür.
