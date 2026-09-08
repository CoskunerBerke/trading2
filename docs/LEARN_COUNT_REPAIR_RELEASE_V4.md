# LEARN_COUNT_REPAIR_RELEASE_V4 — sürüm paketi, durum stratejisi ve ödeme karşılaştırması

> ### ✅ GÜNCELLEME (2026-09-08) — bu belgenin planı YÜRÜTÜLDÜ
>
> §7'deki sürüm/geri alma planı uygulandı: `feature/quant-evaluation-v1` `12db804` → `9bafb57`
> ileri sarıldı, CI tam SHA üzerinde yeşil, `learn_v2.json` `f3cd29cd…` → `ec481a3d…`
> dönüştürüldü, kesinti 238 saniye, iki doğal tur geçti.
> **Yürütme kaydı: `docs/LEARN_COUNT_REPAIR_DEPLOYMENT_V4.md`.**
> Aşağıdaki "DAĞITILMADI" ifadeleri o tarihe (2026-09-08 öncesi) aittir ve TARİHSELDİR.

**Durum (hazırlık anında):** HAZIRLIK TAMAM · **O ANDA DAĞITILMAMIŞTI** · push/deploy/restart/
state göçü YOK · üretim modeli fit edilmedi · kapı kaynağı/eşik/risk değişmedi ·
pfexp v1/v1.1/v1.2 dokunulmadı · v1.3 oluşturulmadı.

> **O tarihte çalışan bot ONARILMAMIŞTI.** Aşağıdaki düzeltmeler yereldi. Yeşil test paketi,
> üretim öğrenmesinin değiştiği anlamına GELMEZ. Dağıtım kararı ayrıca alındı ve 2026-09-08'de
> uygulandı.

---

## 1. Yerel düzeltme ≠ çalışan üretim

| | Revizyon | Durum |
| --- | --- | --- |
| VPS `/opt/tradingbot/app` | `12db804c7301dbebe337813e20e44c9e967d260b` (`feature/quant-evaluation-v1`) | temiz, NRestarts 0, worker+dashboard `active` |
| Araştırma dalı | `research/profitability-acceleration-v1` @ `710a296` → bu iş | yerel |
| Sürüm adayı | `release/learn-count-integrity` (taban `12db804`) | yerel, **dağıtılmadı** |

Salt okunur ölçüm (2026-09-08T08:31:02Z):

| Kontrol | Üretimde | Kanıt |
| --- | --- | --- |
| Sayaç düzeltmesi | **HAYIR** | `learner_v2.py:154-155` hâlâ İKİ `win.add`; `leaves=` 0 eşleşme; `_keys_multi` 0 eşleşme |
| Sunum düzeltmesi | **HAYIR** | `_cell_pct_signal` 0, `COIN_HEAD_COLUMN_NOTES` 0, kolonlar hâlâ `Güven` / `P(kazanç)` |
| Üretim `learn_v2.json` | kirli | global `win` `n=50.0, s=14.0, ss=14.0`; `exp_r` `n=25.0`; `n_closed=25` |

---

## 2. Asgari dağıtılabilir yama (araştırma modülleri HARİÇ)

`release/learn-count-integrity`, `12db804` üzerine **yalnız** şu üretim dosyalarını değiştirir
(+207 / −19 satır):

| Dosya | Değişiklik |
| --- | --- |
| `tradingbot/learn/model.py` | `HierarchicalRate._keys_multi` + `add(..., leaves=…)`; atalar BİR KEZ. `leaf=` geriye uyumlu. |
| `tradingbot/learn/learner_v2.py` | `on_trade_closed` tek çağrı iki yaprak; `LEARNING_SEMANTICS`; derse `learning_keys` |
| `tradingbot/learn/reconcile.py` | `LearnedIndex.record(..., learning_keys=None)` — MEVCUT indekse opsiyonel alan |
| `tradingbot/dashboard/views.py` | kolon adları, `_cell_pct_signal`, `NOT_APPLICABLE`, kanıta bağlı `calibration_note` |
| `tradingbot/dashboard/app.py` | `_learning_status()` (salt okunur), alan anlamları legend'i |
| `tradingbot/dashboard/state.py` | `learn_v2` beyaz listeye (gösterim için) |
| `tests/test_learning_count_integrity.py`, `tests/test_learning_provenance.py` | sürüm ağacı regresyonları (araştırma bağımlılığı YOK) |

`tradingbot/research/*` sürüm ağacında **YOKTUR** — araştırma dalı bütünüyle dağıtılmaz.

**Sürüm ağacı testleri: 1940 passed / 22 skipped / 0 failed**, ruff temiz.
(Araştırma dalı ayrı: **2014 passed / 22 skipped / 0 failed** — bu sayı sürüm sayısı DEĞİLDİR.)

---

## 3. Durum kararı: **B (onar)** — kanıtla

### 3.1 Ters çevirmenin geçerliliği KANITLANDI (13/13 kontrol)

`learn_v2.json`'ın değişmez yerel kopyası üzerinde (`sha256 f3cd29cd…`):

| Kontrol | Sonuç |
| --- | --- |
| `SCHEMA_VERSION` = 2 | GEÇTİ |
| Zaten dönüştürülmemiş | GEÇTİ |
| **Çürüme/ağırlık YOK** (`half_life_days = None` her ikisinde) | GEÇTİ |
| **Bernoulli, ağırlıksız** (`s == ss` 100/100 düğümde) | GEÇTİ |
| Yabancı düğüm türü yok (`market:`/`cluster:`) | GEÇTİ |
| **legacy_bridge import'u ÇALIŞMAMIŞ** (sembolsüz yaprak 0, LEGACY ders 0) | GEÇTİ |
| **Ata parite ÇİFT** (8/8 ata düğümde `n` ve `s` çift tamsayı) | GEÇTİ |
| Σ(regime n) = global n ve Σ(regime s) = global s | GEÇTİ |
| global n = 2 × `n_closed` = 50 | GEÇTİ |
| global n = 2 × kanonik kapanış (25) · global s = 2 × kanonik kazanç (7) | GEÇTİ |

**Karışık kod sürümü yok:** iki çağrılı biçim `84bfc74` (2026-08-18T08:32Z) ile geldi; ilk
kanonik kapanış F00001 2026-08-19T15:06Z. Yani **25 kapanışın tamamı** aynı semantikle
işlenmiştir.

### 3.2 Önceki "blocker" GEÇERSİZ — düzeltme

V3 belgesinde "kapanış anı rejimi kalıcı saklanmıyor, bu yüzden tam olay replay'i imkânsız"
denmişti. **Bu YANLIŞ.** `learn_v2.json["lessons"]` her kapanış için `regime` alanını taşır ve
bu, `on_trade_closed`'ın düğüm anahtarını kurarken kullandığı **kapanış anı** rejiminin ta
kendisidir (giriş anı rejiminden **14/25 kapanışta FARKLI**).

Derslerden yeniden kurulum ile saklanan durum **birebir aynıdır**:

* `win`: 100/100 düğüm, `n` ve `s` farkı **0**
* `exp_r`: bütün düğümler eşit

Dolayısıyla onarım **iki bağımsız yolla** doğrulanır ve ikisi aynı sonucu verir:

1. ata düğümlerini yarılama (tekdüze iki katın tersi),
2. derslerden düzeltilmiş semantikle sıfırdan kurulum.

`verify_conversion` → **`VERIFIED`**.

### 3.3 Kuru çalıştırma farkı (8 düğüm; yaprak/`exp_r`/`agent_hit`/dersler/`n_closed` DEĞİŞMEZ)

```
düğüm                                  n (önce→sonra)         s (önce→sonra)
(global)                               50.0 → 25              14.0 → 7.0
regime:TREND_DOWN                      10 → 5                 0.0 → 0.0
regime:EUPHORIC                        4 → 2                  4.0 → 2.0
regime:SQUEEZE                         2 → 1                  0.0 → 0.0
regime:TREND_UP                        24.0 → 12              8.0 → 4.0
regime:BREAKOUT                        4 → 2                  2.0 → 1.0
regime:RANGE                           4 → 2                  0.0 → 0.0
regime:LOW_VOL                         2 → 1                  0.0 → 0.0
```

`n`, `s` ve `ss` **birlikte** dönüştürülür (yalnız `n` bölmek tutarsız durum üretirdi).
Önsel kütle `alpha`/`prior_mean` içinde ve gözlem kütlesinden AYRIdır — dokunulmaz.
Girdi belgesi değiştirilmez; çıktı `ancestor_count_repair` işaretiyle damgalanır ve ikinci kez
dönüştürme REDDEDİLİR.

### 3.4 A ile B arasındaki fark — 0.0188R "küçük" diye A seçilmedi

`half_life_days = None` → **yenilik ağırlığı YOK** → A'daki fazla kütle **asla kaybolmaz**:

| gelecek kapanış (SENARYO) | A global n | A posterior | B global n | B posterior | Δposterior | fazla kütle |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 50.0 | 0.316667 | 25.0 | 0.342857 | +0.026190 | **25.0** |
| 10 | 60.0 | 0.311429 | 35.0 | 0.328889 | +0.017460 | **25.0** |
| 25 | 75.0 | 0.305882 | 50.0 | 0.316667 | +0.010784 | **25.0** |
| 50 | 100.0 | 0.300000 | 75.0 | 0.305882 | +0.005882 | **25.0** |
| 100 | 150.0 | 0.293750 | 125.0 | 0.296296 | +0.002546 | **25.0** |

Bunlar **senaryodur, piyasa tahmini değildir** (sabit %28 kazanma oranıyla aritmetik gösterim).

**Karar gerekçesi (yalnız büyüklük değil):**

| Ölçüt | A (koru) | B (onar) |
| --- | --- | --- |
| Kanıt bütünlüğü | 25 kapanış ata düğümlerde 50 gözlem gibi durur — kayıt YANLIŞ kalır | sayaçlar gözlemle örtüşür |
| Olasılık/belirsizlik | shrinkage kalıcı olarak zayıf, ceza kalıcı olarak küçük | tasarlanan davranış |
| Uygulama riski | sıfır (dosya değişmez) | düşük: 8 düğüm, iki bağımsız doğrulama, işaretli, geri alınabilir |
| Kapı duyarlılığı | 2/404 satırda verdikt farkı (aşağıda ayrıştırıldı) | — |
| Kalıcılık | çürüme YOK → fazla kütle süresiz kalır | temizlenir |

**Seçim: B.** V3'teki R2 blocker'ı (§3.2) geçersiz olduğu için B artık kanıtlanabilir ve
tercih edilir.

### 3.5 Kapı verdikti değişimleri — kanıt sınıfına göre AYRIŞTIRILDI

| Aday | Sembol | Kayıtlı `sample_size` | Yeniden kurulan (eski) | Sınıf | Kenar |
| --- | --- | --- | --- | --- | --- |
| `a1592626729ca2c4` | BTC/USDT | 4.0 | 4 | **TAM YENİDEN ÜRETİM** | +0.018185 → −0.007842 |
| `fed44d90cddc48d1` | AVAX/USDT (**F00033**) | 4.0 | 2 | **YAKLAŞIK duyarlılık** | +0.006236 → −0.019715 |

* BTC adayı **kanonik hiçbir işleme bağlı değildir** (yalnız aday).
* **F00033 satırı tam yeniden üretilemedi** (kayıtlı 4 vs yeniden kurulan 2); bu yüzden onun
  kapı verdiktinin değişeceği **kanıtlanmış değildir**, yalnız duyarlılıktır.
* **F00033 hâlâ AÇIK bir pozisyondur** — gerçekleşmiş kâr/zararı yoktur; bu varsayımsal
  değişikliğe hiçbir sonuç ATFEDİLEMEZ.

---

## 4. Provenans — gelecekteki güncellemeler için asgari ek

Önce mevcut kayıtlar incelendi: `lessons` zaten kapanış anı rejimini taşıyor (§3.2), fakat
`save()` dersleri **son 500** ile sınırlar. Append-only idempotency otoritesi
(`learned_closes.jsonl`) ise düğüm anahtarlarını taşımıyordu.

Asgari **ek** değişiklik (ikinci defter YOK, idempotency anahtarı DEĞİŞMEDİ):

```json
"learning_keys": {"semantics": "v2-multileaf", "regime_at_close": "TREND_UP",
                  "win_leaves": ["ONDO/USDT|pullback", "ONDO/USDT"],
                  "exp_r_leaf": "pullback|LONG", "weight": 1.0, "feature_version": 3}
```

* `on_trade_closed` derse `learning_keys` ekler; `note_learned` bunu MEVCUT indekse yazar.
* Alan **opsiyoneldir** — eski satırlar ve `learning_keys` vermeyen çağrılar bozulmaz.
* **Giriş anı rejimi ile kapanış anı rejimi AYRI kalır**; learner'ın koşullandığı rejim
  DEĞİŞTİRİLMEDİ (`regime_at_close` yalnız kayda geçer).
* Değişmez giriş snapshot'larına sonuç EKLENMEZ; eski olaylar yeniden yazılmaz.

---

## 5. Hedefe hizalı ödeme modeli

### 5.1 `R` tanımı (kanonik defterden izlendi)

* payda = **başlangıç riski** = |`entry_avg` − `initial_stop`| × **`initial_qty`**
  (kısmi çıkış paydayı küçültmez),
* pay = pozisyonun **NET** PnL'i; bütün çıkış dolumları (TP1 kısmi dâhil) toplanır,
  giriş+çıkış ücretleri ve funding düşülmüş, kayma dolum fiyatının içinde,
* çıkış politikası = kanonik şampiyon.

**R zaten net** → ödeme modelinde maliyet TEKRAR düşülmedi. Muhasebe mutabakatı:
net −4.2474225161 = brüt −3.9502024026 − ücret 0.4103205579 + funding 0.1131004445,
**artık 0.0**.

### 5.2 Mevcut `avg_win_r` sınıfı: **BLEND** (ampirik koşullu ortalama DEĞİL)

```
realised_win_r = (exp_r + (1−p_h)·L) / p_h
avg_win_r      = w·realised_win_r + (1−w)·max(0.1, plan.expected_r),  w = n/(n+20)
```

`exp_r` **koşulsuz** beklenti tahminidir; `p_h` ile ters çevrilmesi E[R | R > 0.25] vermez ve
`p_h` değişince değer değişir. `avg_loss_r = −1.0` ise sabit varsayımdır.

### 5.3 −1R yaklaşımının yönü (bu kohorttan ölçüldü)

E[R | kazanç değil] = **−1.069052**, SCRATCH sayısı **0** →
`−1R gerçek kaybı OLDUĞUNDAN KÜÇÜK gösterir`. Sabit-72 saat kohortundaki 24 SCRATCH bu
kanonik tahmine **TAŞINMADI**.

### 5.4 Sonuçlar — iki SABİT olasılık paneli

Kohort 404 fırsat (temsilci politikası değişmedi), kapsam **%100** (her adayda `as_of`tan önce
en az bir W ve bir N gözlemi var). Belirsizlik ve yumuşak cezalar **DEĞİŞTİRİLMEDİ**.

μ_W aralığı **1.707…1.908**, μ_N aralığı **−1.0696…−1.0685** (sınıf sayıları her satırda
kayıtlı, ör. son satır n_W=7 / n_N=18).

| Panel (olasılık SABİT) | ort. Δgross | azami \|Δ\| | eski EVET→yeni HAYIR | eski HAYIR→yeni EVET |
| --- | --- | --- | --- | --- |
| `FIXED_SOURCE_p_win_prior` | **−0.137581** | 1.380702 | **5** | **0** |
| `FIXED_SOURCE_p_win_blend` | **−0.094536** | 0.444876 | **3** | **0** |

Hedefe hizalama her iki panelde de ekonomiyi **daha temkinli** yapar; hiçbir satır ters yönde
açılmaz. Ön tahmin panelinde YES→NO olan 5 adaydan **yalnız biri** kanonik bir işleme bağlıdır
(F00033).

**Bu bir kârlılık sonucu DEĞİLDİR.** Daha büyük EV ya da daha düşük kabul oranı kâr anlamına
gelmez. Paneller olasılık kaynağını sabit tutar ve hiçbir olasılığı doğrulamaz.

---

## 6. Düzeltilen sunum ve ifadeler

### 6.1 `P(kazanç)` etiketi — kanıta bağlı

Kolon adı **`P(kazanç) — istatistiksel tahmin`**. Açıklama hedefi ve ufku söyler; kalibrasyon
durumu artık **kanıttan** türetilir (`calibration_note`): `learn_v2.calibrator.n_fit` ve
`models.json` champion kaydı okunur. Kanıt yoksa "kalibrasyon durumu bu panelden
DOĞRULANAMADI" yazılır — kalıcı ve yanlışlanabilir sabit bir cümle YOKTUR.

### 6.2 Yönetim/karar satırlarındaki sıfırların ÜRETİCİSİ

* **Açık pozisyon yönetimi tablosu zaten doğruydu:** 11/11 satırda `p_win` ve
  `expected_net_return` **`"UNKNOWN"`** dizesidir (`economics_evaluated=false`) ve panel bunu
  `UNKNOWN` rozetiyle gösterip `n_economics_unknown=11` sayacını verir. **Değişmedi.**
* **Coin head satırlarındaki `0.0` farklıdır:** `expected_return_net`/`expected_r` YALNIZ
  geçerli bir giriş planı üretilen yolda atanır (`coinhead/head.py`); REDUCE/HOLD/NO_TRADE
  satırlarında **dataclass varsayılanı 0.0** kalır. Bu **ölçülmüş sıfır DEĞİLDİR**.
  Artık geçerli plan yoksa **`yok (plan üretilmedi)`** gösterilir; ham değer yükte korunur.
  Plan varken hesaplanmış `0.0` ise aynen `%0.00` / `0.00` olarak gösterilir.
* SOL'daki **"%0 güven"** bir fallback değildi: `confidence_calibrated = 0.0019`'un
  yuvarlanmasıydı (38 head'in hiçbirinde tam sıfır yok). `_cell_pct_signal` artık küçük ama
  sıfır olmayan değeri gizlemez (`%0.2`), ölçülmüş sıfırı `%0` bırakır, eksiği `—` yapar.

### 6.3 Yaşam boyu Brier ifadesinin sınırlandırılması (V3 §5 düzeltmesi)

V3'te "ön tahmin naif temel orandan ölçülebilir biçimde daha kötü" denmişti. Geçerli ama
**sınırlandırılmalıdır**:

* Sonuç **99 çözülmüş fırsata** aittir — bunlar **kanonik kapanış DEĞİLDİR**; 404 fırsatın
  **305'i çözülmemiş** ve bu sonucun DIŞINDADIR.
* GA yalnız **sembol kümesi** bootstrap'ıdır; sıfırı dışlaması seçilim yanlılığını ya da ortak
  zaman/piyasa bağımlılığını ORTADAN KALDIRMAZ.
* **A ve B hedeflerinin sıralamasının farklı olması, aralarındaki farkın istatistiksel testi
  DEĞİLDİR**; hedefler tanım gereği FARKLIdır.
* AUC 0.33 kârlı bir ters sinyal KANITLAMAZ.

Yeni bir parametre araması yapılmadı; yalnız ifade düzeltildi.

---

## 7. Sürüm / geri alma planı (UYGULANMADI)

**Ön koşullar:** operatör onayı · worker durdurulmuş (kooperatif `stop`) · `deploy/backup.sh
daily` ile yedek + sha256.

**Sıra:**

1. `release/learn-count-integrity` bundle ile `12db804` → sürüm SHA'sı (GitHub 401 → bundle).
2. **Durum onarımı yalnız YEREL kopyada:** VPS'ten `learn_v2.json` salt okunur çekilir →
   `python -m tradingbot.research.run --stage readiness` ile `analyse`/`convert`/
   `verify_conversion` çalıştırılır → `VERIFIED` değilse **DUR**. Dönüştürülmüş dosya
   yüklenir. (Dönüştürücü araştırma dalındadır; sürüm ağacında ÇALIŞTIRILMAZ.)
3. Worker + dashboard restart · canary 2 doğal tur · `/health/ready` 200 · 0 Traceback.

**Geri alma — kapsam KRİTİK:**

* Kod: `.last_good_commit` = `12db804` ile geri alınır.
* Durum: **yalnız `learn_v2.json`** onarım öncesi kopyasıyla geri alınır.
  **Kanonik defter, `trade_memory.jsonl`, pfexp dosyaları, `learned_closes.jsonl` ve pozisyonlar
  GERİ ALINMAZ** — aksi hâlde bu sırada oluşan doğal dolumlar ve kapanışlar kaybolurdu.
* `learn_v2.json` geri alınırsa o sırada eklenen kapanışların öğrenmesi geri alınır; bu yüzden
  geri alma penceresi canary ile SINIRLI tutulmalıdır.

**Bu adımların hiçbiri çalıştırılmadı.**

---

## 8. Tamamlanan vs bekleyen

**Tamamlanan (yerel, ölçülmüş):** üretim revizyonu doğrulaması · asgari sürüm dalı ve ağacında
tam regresyon (1940/22/0) · durum onarımının kanıtı ve iki bağımsız doğrulaması ·
kuru çalıştırma farkı · A/B karşılaştırması · provenans eki · ödeme modeli karşılaştırması ·
sunum düzeltmeleri · araştırma dalı tam regresyonu (2014/22/0).

**[TAMAMLANDI 2026-09-08]** operatör onayı · dağıtım (bundle gerekmedi, GitHub erişimi geri
gelmişti) · `learn_v2.json` dönüşümünün üretime yüklenmesi · restart + iki doğal tur canary ·
dağıtım sonrası doğrulama. Ayrıntı: `docs/LEARN_COUNT_REPAIR_DEPLOYMENT_V4.md`.

---

## 9. Komutlar

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out" --stage readiness
```

Sürüm ağacı doğrulaması (`C:/Users/berke/release-lci`):

```bash
python -m pytest tests -q
```
