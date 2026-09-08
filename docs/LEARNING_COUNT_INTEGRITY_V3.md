# LEARNING_COUNT_INTEGRITY_V3 — sayaç onarımı, hedef doğrulaması ve düzeltilmiş kıyaslama

> ### ⚠ DÜZELTME (2026-09-08) — `docs/LEARN_COUNT_REPAIR_RELEASE_V4.md`
>
> * **§3 "kapanış anı rejimi kalıcı saklanmaz" İDDİASI YANLIŞTI.**
>   `learn_v2.json["lessons"]` her kapanış için `regime` alanını taşır ve bu, `on_trade_closed`'ın
>   düğüm anahtarını kurarken kullandığı KAPANIŞ ANI rejimidir (giriş rejiminden 14/25 kapanışta
>   farklı). Derslerden yeniden kurulum saklanan durumu **100/100 düğümde birebir** üretir.
>   Gerçek sınır farklıdır: `save()` dersleri **son 500** ile sınırlar.
> * **§7 önerisi R1 (dokunma) → B (onar) olarak DEĞİŞTİ.** R2'nin blocker'ı geçersizdi; onarım
>   iki bağımsız yolla doğrulanır (`VERIFIED`).
> * **§5 yaşam boyu Brier ifadesi sınırlandırıldı:** sonuç 99 ÇÖZÜLMÜŞ FIRSATA aittir (kanonik
>   kapanış değil); 305/404 çözülmemiş fırsat sonucun dışındadır; nominal GA seçilim yanlılığını
>   ve ortak zaman bağımlılığını kaldırmaz; A/B sıralamalarının farklı olması aralarındaki farkın
>   testi DEĞİLDİR.
> * **§6'daki `expected_return_net = 0.0` "ölçülmüş sıfır" nitelemesi DÜZELTİLDİ:** giriş planı
>   üretilmeyen satırda bu değer dataclass VARSAYILANIdır; panel artık «yok (plan üretilmedi)»
>   gösterir.

**Durum:** YEREL onarım + test · push/deploy/restart YOK · üretim `learn_v2.json`,
kanonik defter, pfexp dosyaları ve öğrenilmiş indeks **DEĞİŞMEDİ** · model fit edilmedi ·
state göçü yapılmadı · PAPER / SHADOW / live-kapalı korundu · pfexp v1.3 **oluşturulmadı**.

> Geçen testler ya da daha çok adayın elenmesi **kâr iddiası DEĞİLDİR**. Bu belge bir sayım
> kusurunu onarır, bir hedefi doğrular ve yanıltıcı etiketleri düzeltir.

Çıktı: `integrity_report.json` / `integrity_report.md` (v1 ve v2 çıktıları korunur).

---

## 1. Sayaç kusurunun KESİN kök nedeni

Tek nihai kapanış izlendi: **F00001** (SUI/USDT SHORT, `stop`, R −1.2183, 2 fill,
`tp1_done=false`, `features.regime=TREND_DOWN`, `setup_type=pullback`).

`LearnerV2.on_trade_closed` bir kapanış için `win.add`'i **İKİ KEZ** çağırıyordu:

```python
self.win.add(won, regime=regime, leaf=f"{symbol}|{setup}")   # yaprak 1
self.win.add(won, regime=regime, leaf=symbol)                # yaprak 2
```

`HierarchicalRate.add` her çağrıda `_keys(...)` listesinin **tamamını** yazar ve bu liste
**her zaman ortak ataları içerir**: `""` (global) ve `regime:X`. Sonuç:

| Düğüm | Kapanış başına güncelleme | Meşru mu |
| --- | --- | --- |
| `""` (global) | **2** | **HAYIR** — aynı gözlem iki kez |
| `regime:TREND_DOWN` | **2** | **HAYIR** — aynı gözlem iki kez |
| `leaf:SUI/USDT\|pullback` | 1 | evet (ayrı düğüm) |
| `leaf:SUI/USDT` | 1 | evet (ayrı düğüm) |
| `regime:…\|leaf:SUI/USDT\|pullback` | 1 | evet |
| `regime:…\|leaf:SUI/USDT` | 1 | evet |
| `exp_r` bütün düğümleri | 1 | evet (tek `add` çağrısı) |

Üç büyüklük AYRI raporlanır (karıştırılmamalı):

* **benzersiz gözlem** = 25 (nihai kapanış; kısmi çıkış değil)
* **güncelleme çağrısı** = 50 (eski) → 25 (düzeltilmiş)
* **ağırlıklı kütle** (`n = Σw`) global düğümde = 50.0 (eski) → 25.0 (düzeltilmiş)

Üretimdeki `learn_v2.json` global `win` düğümü `n=50, s=14` idi; `n_closed=25`, kazanan 7.
`exp_r` global `n=25` — hiç yinelenmemiş. Yani **n=50 kasıtlı ağırlıklı kütle DEĞİL**, aynı
gözlemin ata düğümlerde iki kez sayılmasıdır.

Yalıtılmış yeniden oynatma (25 kanonik kapanış): **9 düğüm yinelenmiş** (1 global + 8 rejim),
**90 düğüm değişmemiş** (44 yaprak + 46 rejim|yaprak). Yani kusur YALNIZ ortak atalardadır.

---

## 2. Onarım (en küçük değişiklik) ve öncesi/sonrası

`HierarchicalRate.add` artık **aynı gözlem için birden çok yaprak** kabul eder ve ataları
**bir kez** sayar:

* `learn/model.py` → `_keys_multi(regime, leaves, market, cluster)`: ataları bir kez üretir,
  her yaprağı bir kez ekler, anahtarları sıra koruyarak tekilleştirir. `add(..., leaf=)`
  imzası **geriye uyumlu** kaldı (test ile sabitlendi).
* `learn/learner_v2.py` → tek çağrı: `win.add(won, regime=..., leaves=(f"{sym}|{setup}", sym))`.

`n`'yi ikiye bölen, sayaç silen ya da meşru çocuk-düğüm güncellemesini bastıran bir çözüm
kullanılmadı.

| | ÖNCE | SONRA |
| --- | --- | --- |
| tek kapanış → global `win` | 2 | **1** |
| tek kapanış → `regime:X` | 2 | **1** |
| tek kapanış → her yaprak | 1 | **1** (değişmedi) |
| 25 kapanış → global `win` n | 50.0 | **25.0** |
| 25 kapanış → `exp_r` global n | 25.0 | 25.0 (değişmedi) |

Doğrulananlar (test): bir gözlem her hedef düğüme bir kez katkı verir · aynı gözlemde
tekrarlanan yaprak iki kez sayılmaz · `save`/`load` sayaçları korur · iki ayrı gözlem ayrı
kalır · **tekrarlı teslim** mevcut idempotency otoritesi (`LearnedIndex` + `close_event_id`)
tarafından engellenir — **ikinci bir öğrenme defteri EKLENMEDİ** · kısmi çıkış (kapanış zamanı
ve nedeni olmayan kayıt) nihai kapanıştan FARKLI bir olay kimliği üretir · yenilik ağırlığı
(`half_life_days`) semantiği korunur (`n = Σw`).

---

## 3. Olasılık bileşenlerine etkisi — sadece metadata DEĞİL

Kusur iki bileşeni **gerçekten** etkiliyordu:

1. `sample_size = max(n_eff_win, n_eff_exp_r)` — `n_eff_win` iki katına çıkıyordu,
2. `uncertainty_penalty_r = 0.20 / sqrt(sample_size + 1)` — dolayısıyla **olduğundan KÜÇÜK**,
3. hiyerarşik posterior — çift kanıt, shrinkage'ı zayıflatıyordu.

**Point-in-time** ölçüm (her fırsat yalnız `as_of`tan önce kapanmış işlemleri görür):

| Etki | Değer |
| --- | --- |
| ortalama Δ`uncertainty_penalty_r` | **+0.018828 R** (düzeltme cezayı BÜYÜTÜR) |
| azami Δ`uncertainty_penalty_r` | +0.026027 R |
| ortalama Δ`p_hier` | +0.028670 |
| azami Δ`p_hier` | +0.053030 |
| etkilenmeyen | `p_win_prior` (learner'dan gelmez), `avg_win_r` (`exp_r` yinelenmiyor), plan geometrisi |

**Yön:** düzeltme sistemi tasarlandığı kadar **temkinli** yapar; olasılıkları ikiye katlamaz.

**F00036 doğrulaması (birebir):** kayıttaki `sample_size = 22.0` ve
`uncertainty_penalty_r = 0.041703`, ESKİ semantikle yeniden kurulumda **aynen** çıkıyor;
düzeltilmişte `sample_size = 11`, `uncertainty_penalty_r = 0.057735` (Δ +0.016032).

**Kenar duyarlılığı** (aynı kayıt, düzeltilmiş ceza, her şey sabit): ortalama Δ = −0.018828 R,
azami |Δ| = 0.026027 R, **kapı verdikti değişen 2 / 404 satır**:

| Aday | Sembol | as_of | kayıtlı kenar | düzeltilmiş | işlem |
| --- | --- | --- | --- | --- | --- |
| `fed44d90cddc48d1` | AVAX/USDT | 2026-09-03T23:32 | +0.006236 | **−0.019715** | **F00033 (gerçekleşen işlem)** |
| `a1592626729ca2c4` | BTC/USDT | 2026-09-05T20:01 | +0.018185 | **−0.007842** | — |

Yani sayım kusuru, gerçekten açılmış **bir** pozisyonun (F00033) ekonomik kapıyı geçmesine
katkıda bulunmuş görünüyor. Bu bir yeniden karar DEĞİLDİR; üretim kararları ve tarihsel
snapshot'lar olduğu gibi korunur.

### Yeniden kurulumun sınırı (dürüst)

Kayıttaki `sample_size` **202 / 404** satırda birebir yeniden üretilebiliyor. Neden tamamı
değil: üretimde `on_trade_closed` düğüm anahtarını `decision_snapshot.regime` ile kurar ve bu,
**kapanış anındaki son karara** ait rejimdir (`engine_v3` `last_decisions[symbol]`). Bu değer
kapanışla birlikte **kalıcı saklanmaz**; defterdeki/hafızadaki `features.regime` GİRİŞ anı
rejimidir. Bu yüzden rejim düğümleri birebir yeniden kurulamaz.

**Sağlam ölçüm rejimden bağımsızdır:** global düğüm 50 → 25, ve bu üretimdeki dosyayla birebir
aynıdır. Satır bazındaki Δ değerleri TAHMİNdir; yön ve büyüklük mertebesi güvenilir.

*Ayrı gözlem (bu görevde düzeltilmedi, davranış değişikliği olurdu):* öğrenilen hiyerarşi
kanonik kayıtlardan tam olarak üretilemiyor — provenans boşluğu.

---

## 4. Öğrenme hedefinin GERÇEK sözleşmesi (kaynaktan)

| Soru | Cevap (kaynak) |
| --- | --- |
| Sınıf kurulumu | `\|r\| < 0.25R → SCRATCH`; aksi halde `r > 0 → WIN`, `r ≤ 0 → LOSS` (`labels.py`) |
| `won` | `outcome_class == "WIN"` |
| Hiyerarşi | `win.add(1.0 if won else 0.0)` → **SCRATCH 0.0 ekler ve n'i ARTIRIR** |
| Sınıflandırıcı hedefi | `train_challenger`: `y = (r_multiple > 0.25)` |
| **SCRATCH** | **PAYDADA** — üçüncü sınıf DEĞİL, dışlanmaz, kazanç sayılmaz |
| Koşulluluk | **KOŞULSUZ** `P(r > +0.25R)`; "scratch olmayan" koşuluna bağlı DEĞİL |
| Ufuk | işlem **ÖMRÜ** — sabit ufuk DEĞİL |

**Ödeme modeli uyumsuzluğu (kayda geçirildi):** `avg_loss_r = −1.0` SABİT varsayımdır, ama
"kazanç değil" sınıfı SCRATCH'i de içerir. Her kazanç-olmayanı −1R saymak kayıp tarafını
**abartır**. Kohort B'de 24 SCRATCH sonucu var; kanonik 25 kapanışta SCRATCH yok.

**Önceki v2 ölçümü SCRATCH'i DIŞLIYORDU** → koşullu bir olasılığı koşulsuz bir skora karşı
ölçüyordu. Düzeltildi.

---

## 5. Düzeltilmiş eşleşmiş kıyaslama

Kohort/temsilci politikası **değiştirilmedi** (`linked_or_accepted_else_first`, 404 fırsat).
Ayrı ayrı raporlanır: ham snapshot satırı **2053**, fırsat grubu **404**, kanonik bağ **7**,
kanonik kapanış **25**. Örtüşen fırsatlar bağımsız işlem DEĞİLDİR.

**39 satır sorusunun cevabı:** v2'deki ön tahmin tablosu yalnız `features.p_win_prior`
DOĞRUDAN kayıtlı olan satırları alıyordu; etiketlenmiş 62 satırın yalnız 39'unda bu alan
vardı. Artık kimlik tersinden (`p = (gross+|L|)/(W+|L|)`, doğrulama hatası 0.0) **DERIVED**
provenansıyla geri kazanılıyor ve üç tahmin edici **AYNI kimlik kümesinde** ölçülüyor.

### Hedef A — YAŞAM BOYU (şampiyon çıkış), sansürlü

n = **99**, sansürlü **305** (%75.5), gerçekleşen oran 0.303, kimlik kümesi sha256
`655226d1b9b1316b` (üç tahmin edici için aynı, 99/99/99), ön tahmin provenansı
{kayıtlı 62, DERIVED 37}, kohortta SCRATCH 0.

| Tahmin edici | n | Brier ↓ | Log loss ↓ | AUC | skor sd |
| --- | --- | --- | --- | --- | --- |
| `past_only_base_rate` | 99 | **0.216986** | **0.628770** | 0.4138 | 0.0118 |
| `p_win_blend` | 99 | 0.230404 | 0.660658 | 0.3428 | 0.0668 |
| `p_win_prior` | 99 | 0.344555 | 0.892901 | 0.4698 | 0.0492 |

Eşleşmiş Brier farkı (sembol kümesi bootstrap, 41 küme):

| Fark | ortalama | GA95 | sıfırı dışlıyor |
| --- | --- | --- | --- |
| `p_win_prior` − taban çizgisi | **+0.127569** | **[+0.006606, +0.241657]** | **EVET** |
| `p_win_blend` − taban çizgisi | +0.013417 | [−0.002420, +0.027822] | HAYIR |
| `p_win_blend` − `p_win_prior` | −0.114152 | [−0.222453, +0.003114] | HAYIR |

**Tek istatistiksel olarak kurulmuş sonuç:** ekonomik kapıyı süren `p_win_prior`, ödeme
modelinin ima ettiği olayı öngörmekte **naif geçmiş-yalnız temel orandan ÖLÇÜLEBİLİR biçimde
DAHA KÖTÜ**. Diğer bütün farklar sıfırı içeriyor.

### Hedef B — SABİT 72 SAAT (dondurulmuş MTM)

n = **165**, sansürlü **239**, gerçekleşen oran 0.503, kimlik kümesi `aa737f2aaa0e2bcb`,
kohortta **24 SCRATCH** (paydada NOT_WIN).

| Tahmin edici | n | Brier ↓ | Log loss ↓ | AUC |
| --- | --- | --- | --- | --- |
| `p_win_prior` | 165 | **0.287864** | **0.775305** | 0.3881 |
| `p_win_blend` | 165 | 0.322477 | 0.865112 | 0.4251 |
| `past_only_base_rate` | 165 | 0.325084 | 0.870990 | 0.3908 |

**Bütün eşleşmiş farklar sıfırı içeriyor.**

### Kritik gözlem: sıralama hedefe göre TERSİNE DÖNÜYOR

A'da taban çizgisi en iyi, ön tahmin en kötü; B'de ön tahmin en iyi, taban çizgisi en kötü.
**Bu, iki hedefin birbirinin yerine geçemeyeceğinin doğrudan kanıtıdır.** 72 saatlik MTM,
yaşam boyu olasılığın kalibrasyonu DEĞİLDİR ve denklik gösterilmedi.

### Sınırlamalar

* Ağır sansür (A'da %75.5); kalan küme HIZLI çözülenlere kayar — çözülmüş-yalnız skorlama
  yanlıdır.
* Tek kısa pencere, ağırlıklı LONG, yüksek sembol/zaman bağımlılığı. GA yalnız **sembol
  kümesi** bootstrap'ıdır; zaman bağımlılığı ayrıca modellenmedi.
* Taban çizgisi kanonik kapanışlardan gelir; aday evreni FARKLI bir popülasyondur.
* **AUC < 0.5 kârlı bir ters sinyal KANITLAMAZ.** v2'deki 0.33, eşleşmiş kohortta 0.343 (A) /
  0.425 (B) oldu; olasılık doğruluğu, sıralama ve ekonomik değer AYRI sorulardır.

---

## 6. Panel alan anlamları düzeltildi

Kaynak izi (ölçüldü, varsayılmadı):

| Panel alanı | Gerçek kaynak | Ne DEĞİL |
| --- | --- | --- |
| `Güven` → **`Konsensüs gücü`** | `confidence_calibrated` — uzman konsensüsünün kalibre GÜCÜ | bir olasılık DEĞİL |
| `P(kazanç)` → **`P(kazanç) — model`** | `p_win` — hiyerarşik önsel + legacy harman (+PAPER_BOUNDED) | kalibre DEĞİL (Platt `n_fit=0`), champion model YOK |
| `Beklenen Net Getiri`, `E[R]` | plan geometrisi | gerçekleşmiş sonuç DEĞİL |
| `Karar zamanı` | EN SON değerlendirme | giriş anı kanıtı DEĞİL (o `entry_snapshot`tadır) |

**SOL/USDT örneği (2026-09-08T07:57:55Z, salt okunur anlık görüntü):** `verdict = REDUCE`,
`confidence_calibrated = 0.0019`, `p_win = 0.329`, `expected_return_net = 0.0`,
`expected_r = 0.0`, `time_horizon = 0`.

* Ekrandaki **"%0" güven bir eksik/fallback DEĞİL** — 0.0019'un 0 ondalığa yuvarlanmasıydı.
  38 head'in **hiçbirinde** `confidence_calibrated` tam olarak 0.0 değil.
* `expected_return_net = 0.0` ve `expected_r = 0.0` **ÖLÇÜLMÜŞ sıfırdır** (yönetim satırında
  giriş planı yoktur) — N/A'ya çevrilmedi, aynen korunur.

Yapılan (yalnız sunum; model çalıştırmaz, eksik metadata türetmez, kararı değiştirmez):

1. Kolon adları kaynağı söyleyecek biçimde düzeltildi.
2. `_cell_pct_signal`: **ölçülmüş sıfır `%0` kalır**, eksik `—` olur, sıfır olmayan küçük
   değer artık gizlenmez (`0.0019 → %0.2`). Gerçek sayısal değer DEĞİŞTİRİLMEDİ.
3. `column_notes` (hedef + kalibrasyon durumu + "son değerlendirme ≠ giriş kanıtı") panel
   yükünde taşınıyor ve tablo altında katlanır bir açıklama olarak gösteriliyor.

**Açık pozisyon yönetimi tablosu zaten doğruydu:** 11 satırın 11'inde `p_win` ve
`expected_net_return` değeri `"UNKNOWN"`, `economics_evaluated = false`, ve panel bunları
`UNKNOWN` rozetiyle gösterip `n_economics_unknown = 11` sayacını ayrıca veriyor. Orada sıfır
gösterilmiyor — değişiklik gerekmedi.

---

## 7. Üretim durumu onarım planı (HAZIRLANDI, UYGULANMADI)

Kapsam yalnız `state/learn_v2.json` → `win.stats` ata düğümleri. Kapsam dışı: kanonik defter,
`trade_memory.jsonl`, pfexp dosyaları, öğrenilmiş indeks, modeller. Kirlenen düğüm: **9**.

| # | Seçenek | Ne yapar | Risk |
| --- | --- | --- | --- |
| **R1** | **DOKUNMA (ÖNERİLEN)** | kod düzeltildi; bundan sonraki kapanışlar tek sayılır, mevcut sayaçlar zamanla seyrelir | düşük |
| R2 | ata düğümleri yeniden kur | kanonik kapanışlardan düzeltilmiş semantikle yeniden kurulum | orta — **blocker:** kapanış anı rejimi saklanmadığı için rejim düğümleri birebir doğrulanamaz |
| R3 | kapanış anı rejimini kalıcı yap | hiyerarşi kanonik kayıtlardan üretilebilir hale gelir | düşük ama ŞEMA/DAVRANIŞ değişikliği — bu görevin kapsamı dışında |

**Öneri: R1.** Yeniden yazmanın kazancı küçük (ortalama belirsizlik cezası farkı 0.0188 R) ve
R2 birebir doğrulanamıyor. `execution_authorized: false`.

---

## 8. Değişen yollar ve testler

| Yol | Değişiklik |
| --- | --- |
| `tradingbot/learn/model.py` | `HierarchicalRate.add(leaves=…)` + `_keys_multi` (ata tek sayım); `leaf=` geriye uyumlu |
| `tradingbot/learn/learner_v2.py` | `on_trade_closed` tek çağrı, iki yaprak |
| `tradingbot/dashboard/views.py` | kolon adları, `_cell_pct_signal`, `COIN_HEAD_COLUMN_NOTES`, yükte `column_notes` |
| `tradingbot/dashboard/app.py` | alan anlamları legend'i, kartlarda aynı hassasiyet ve ayrım |
| `tradingbot/research/learning_replay.py` | YENİ — yalıtılmış eski/yeni replay, point-in-time bileşenler, kenar duyarlılığı |
| `tradingbot/research/probability_benchmark.py` | YENİ — hedef sözleşmesi, A/B hedefleri, eşleşmiş kıyaslama |
| `tradingbot/research/integrity_run.py` | YENİ — `--stage integrity` çıktı üreticisi + onarım planı |
| `tradingbot/research/run.py` | `--stage integrity` |
| `tests/test_learning_count_integrity.py` | YENİ — 21 test |

**Ölçülen testler: 1990 passed / 22 skipped / 0 failed** (önceki 1969 + 21 yeni), ruff temiz.

Komut:

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out" --stage integrity
```

---

## 9. Kanıta dayalı sonraki adım

**Ödeme modelini etikete uydur.** Ölçülen üç şey bunu işaret ediyor: (a) hedef koşulsuz
`P(r > +0.25R)`, (b) "kazanç değil" sınıfı SCRATCH'i içeriyor, (c) `avg_loss_r = −1.0` sabit
varsayımı kayıp tarafını abartıyor (ölçülen ortalama kayıp −1.0323, ama SCRATCH'ler ~0).
Kayıp tarafını **ölçülmüş koşullu ortalamayla** (`E[R | r ≤ 0.25R]`) değiştiren bir SHADOW
hesabı, hiçbir kararı değiştirmeden kaydedilebilir ve ileriye dönük olarak
`conservative_net_edge_r`'nin hangi tanımla daha tutarlı olduğunu gösterir.

Bu bir kâr vaadi değildir; hedef ile ödeme modelini AYNI olay üzerinde tanımlamak, yeni bir
tahmin edici tasarlamadan önceki zorunlu adımdır.
