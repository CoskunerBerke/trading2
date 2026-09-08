# PROFITABILITY_DUAL_EDGE_V2 — kaynak doğrulaması, F00036 mutabakatı ve eşleşmiş çift kapı

**Durum:** OFFLINE RESEARCH · üretim kodu/config'i DEĞİŞMEDİ · karar hattı yeniden
SIRALANMADI · olasılık kaynağı DEĞİŞTİRİLMEDİ · kapı/eşik/risk DOKUNULMADI · pfexp
DOKUNULMADI · VPS salt okunur · push/deploy YOK.

> **Bu belge kâr vaat etmez ve bir düzeltme reçetesi değildir.** Ölçülen tek şey, ekonomik
> kapının hangi girdiyi kullandığı ve aynı formülün ikinci bir olasılıkla ne verdiğidir.
> Kalibre tahmin, ekonomik replay denkliği ve kârlı bir ikame politikası **kanıtlanmamıştır**.

Bu belge `PROFITABILITY_RESEARCH_ACCELERATION_V1.md`'yi **değiştirmez**; onun bazı
ifadelerini düzeltir (§9). Eski kanıt ve çıktı dosyaları korunur.

Çıktı: `dual_edge_report.json` / `dual_edge_report.md` / `corrections.json`
(v1 çıktıları ayrı dosyalarda durur).

---

## 1. Doğrulanan durum

| Öğe | Beyan | Ölçüm (2026-09-08T07:11:50Z, salt okunur) |
| --- | --- | --- |
| Üretim HEAD | `12db804…` | ✔ aynı, working tree temiz, NRestarts 0 |
| Araştırma branch'i | `research/profitability-acceleration-v1` | ✔ |
| Araştırma commit'leri | `239f272`, `7e030ce` | ✔ |
| v1 regresyon | 1947 / 22 / 0 | ✔ (bu iş sonrası **1969 / 22 / 0**) |
| pfexp_v1_2 | ACTIVE_SHADOW, `4a79f6eeb5e95c27`, 19:38:58Z | ✔ `admissions_open=true`, **hâlâ 0 giriş** |
| pfexp_v1_1 | kabul kapalı, drain | ✔ `SUPERSEDED_E_SCOPE_MISMATCH_DRAINING`, `admissions_open=false` |
| pfexp_v1 | salt okunur | ✔ `e3863761d50c79c3` |
| Kanonik | 11 açık / 25 kapalı | ✔ değişmedi; **F00036 hâlâ AÇIK** |
| Güvenlik | PAPER / kill switch ARMED | ✔ `live_order_path=false`, `ALLOW_LIVE_TRADING=false` |

Doğal değişim: cüzdan 98.2226066642 → **98.2246866674** (funding tahakkuku, +0.00208 USDT).
Yeni kapanış ya da yeni giriş YOK.

---

## 2. Kaynak izi — ölçüldü, iddia edilmedi

`pipeline_trace` her koşuda kaynağı yeniden okur. **Sıra karşılaştırması yalnız aynı kapsayıcı
fonksiyon içinde yapılır**: bir fonksiyonun gövdesi, çağrıldığı satırdan sonra görünse bile o
çağrıdan sonra çalışmaz. (İlk uygulama bunu karıştırıp yanlışlıkla "sonradan yeniden hesap var"
demişti; düzeltildi ve testle sabitlendi.)

| Aşama | Dosya | Satır | Kapsayıcı fonksiyon |
| --- | --- | --- | --- |
| ön tahmin üretimi | `coinhead/head.py` | 354 | `decide` |
| **ekonomik hesap ÇAĞRISI** | `engine_v3.py` | **827** | `tour` |
| chief sıralaması | `engine_v3.py` | 829 | `tour` |
| **model olasılığı ataması** | `engine_v3.py` | **875** | `tour` |
| giriş döngüsü sırası | `engine_v3.py` | 1073 | `_execute_locked` |
| kabul (`opportunity.tradeable`) | `engine_v3.py` | 1131 | `_execute_locked` |

* `_assess_opportunities` çağrı yerleri: **[827]** — tek çağrı.
* Model atamasından sonra yeniden hesap çağrısı: **[]** → alternatif yol **YOK**.
* Kodun kendi yorumu (`engine_v3.py` ~848): *"`d.p_win` burada hala HEAD on tahmini — model
  henuz ezmedi."*

### Sınıflandırma: `MISSING_INTEGRATION`

Sıra tek başına yeterli kanıt değildir; sınıflandırma dört kanıta dayanır:

1. Ekonomik kapı model atamasından önce çalışıyor **ve** sonradan yeniden hesaplanmıyor.
2. Yapılandırılmış öğrenme etkisi modu **PAPER_BOUNDED** (kaynak: env drop-in
   `learning.conf`; `decision_journal.learning_influence.mode` ile 1083/1083 kayıtta
   doğrulandı, hepsi `applied=true`). Etki `p_win` üzerinde uygulanıyor — ama `p_win`
   ekonomik kapıya **hiçbir yoldan ulaşmıyor**. Etki bu yüzden ölçülebilir bir ekonomik sonuç
   üretmiyor.
3. Snapshot `p_win` alanına kapının **kullanmadığı** değeri yazıyor → **tutarsız raporlama**.
4. Champion `p_win_lr` modeli **YOK** (`state/models.json` mevcut değil) ve Platt kalibratörü
   **fit edilmemiş** (`n_fit = 0`).

Mod `OFF`/`SHADOW` olsaydı sınıf `INTENTIONAL_SHADOW(+tutarsız raporlama)` olurdu; test bunu
sabitler.

---

## 3. Olasılık sözleşmeleri — hedef ve ufuk

| Büyüklük | Öngördüğü olay | Ufuk | Fit edilmiş | Kalibre |
| --- | --- | --- | --- | --- |
| `p_win_prior` (kapıyı süren) | **TANIMSIZ** — konsensüs kanaatinin monoton dönüşümü (`0.5 + 0.25·conf`), aralık [0.50, 0.75] | TANIMSIZ | HAYIR | HAYIR |
| `p_win` (kayda yazılan) | **P(kapanışta `r_multiple` > +0.25R)** — `learn/labels.py::label_outcome`, SCRATCH dışlanmış | işlem **ömrü** (değişken: stop/hedef/BE/zaman) | HAYIR | HAYIR |
| ödeme modeli (`avg_win_r`, `avg_loss_r`) | E[R\|kazanç] ve E[R\|kayıp]; kayıp tarafı **sabit −1.0 varsayımı** (ölçülen −1.0323) | işlem ömrü | kısmen | — |

**Önemli düzeltme:** `p_win` fit edilmiş bir modelin çıktısı DEĞİLDİR. Champion model
olmadığı için `LearnerV2.predict` `ready=False` döner ve motor
`0.5·hiyerarşik_önsel + 0.5·legacy_v1_tahmin` harmanını kullanır; ardından PAPER_BOUNDED etkisi
(`influence_max_fraction ≤ 0.05`) uygulanır. F00036'da bu etki
`fraction = −6.988e-05` (0.293 → 0.29297953) — pratikte ihmal edilebilir.

**Hedef uyumu:**

* ön tahmin ↔ ödeme modeli: **INCOMPATIBLE_TARGET** (ön tahminin öngördüğü olay tanımsız).
* model ↔ ödeme modeli: **PARTIAL_COMPATIBLE** — aynı kaynak (kapanmış PAPER işlemleri), aynı
  ufuk (işlem ömrü); tek sapma, model etiketinin SCRATCH'i (|r| < 0.25R) kazanç saymaması,
  ödeme modelinin ise kazancı R > 0 kabul etmesi. Mevcut 25 kapanışta SCRATCH **yok**
  (en küçük |R| = 0.5666), bu yüzden sapma şu an maddi değil — ama kayıtlıdır.

Hedef-vuruş olasılığı, nihai net-kârlı-işlem olasılığı ve sabit ufuklu pozitif getiri olasılığı
FARKLI büyüklüklerdir; bu rapor bunları birbirinin yerine koymaz.

---

## 4. Kullanılabilirlik — TEK payda (404)

v1 raporundaki "256/256 ve 0/404" ifadesi **iki farklı paydaydı**. Tek paydalı tablo:

| Alan | Var | Yok |
| --- | --- | --- |
| `p_win_prior` (doğrudan kayıtlı) | 256 | 148 |
| `p_win` (model) | 404 | 0 |
| ödeme (`avg_win_r`, `avg_loss_r`) | 404 | 0 |
| `gross_expectancy_r` | 404 | 0 |
| `conservative_net_edge_r` | 404 | 0 |
| kimlik `p_win_prior` ile sınanabilir | 256 | 148 |
| kimlik model `p_win` ile sınanabilir | 404 | 0 |

**Dışlama nedeni:** `p_win_prior` yalnız `features` bloğu doluyken kayıtlıdır; 148 satırın
temsilcisi `NO_TRIGGER` aşamasında yazılmış ve blok boştur.

### Kapı olasılığı 404/404 için geri kazanıldı

`gross = p·W − (1−p)·|L|` kimliğinin tersi: `p = (gross + |L|) / (W + |L|)`.
Doğrudan kayıtlı 256 satırda doğrulandı: **azami mutlak hata 0.0**, birim aralık dışına çıkan
satır 0. Bu bir model ya da varsayım değil, aynı kimliğin çözümüdür.

Sonuç: **çift kapı 404/404 satırda hesaplanabilir**.

---

## 5. F00036 mutabakatı — boşluk ÇÖZÜLDÜ

Kimlik: `candidate_id 4c7bed8f3bb17655` · `decision_id 20eab956a587dd6e` ·
as_of `2026-09-07T02:43:40Z` · ONDO/USDT LONG · `entry_v1.0.0` · code_sha `7b6b4e9…`
(v1.2 deploy'undan önce yazıldı).

| JSON yolu | Değer |
| --- | --- |
| `$.features.p_win_prior` | **0.659** |
| `$.p_win` (`sources.p_win = MODELED`) | **0.293** |
| `$.avg_win_r` / `$.avg_loss_r` | 1.851148 / −1.0 |
| `$.sample_size` | 22 |
| `$.expectancy_basis` | `NET_OUTCOME` |
| `$.gross_expectancy_r` | **0.878907** |
| `$.net_expectancy_r` | 0.878907 |
| `$.uncertainty_penalty_r` | **0.041703** |
| `$.conservative_net_edge_r` | **0.677204** |
| `$.size_multiplier` | 1.0 |
| `$.provenance` | `written_at_stage=RANKING`, `sees_outcome=false` |

Formül zinciri:

```
gross        = 0.659 × 1.851148 − 0.341 × 1.0            = 0.878907   (= 0.878906532, 6 hane)
cost_r       = 0        (expectancy_basis = NET_OUTCOME → maliyet zaten net R'lerin içinde)
net          = gross − cost_r                             = 0.878907
uncertainty  = UNCERTAINTY_K(0.20) / sqrt(22 + 1)         = 0.041703
soft_penalty = GateLedger.soft_penalty_r() (üst sınır 0.60) = 0.160000
conservative = 0.878907 − 0.041703 − 0.160000            = 0.677204
tradeable    = (not blocked) and conservative > 0 and size_multiplier > 0
```

**Boşluk `0.201703` = belirsizlik `0.041703` + yumuşak ceza `0.160000`; artık `0.0` →
`RESOLVED`.**

Açıklama: `0.878907` ile `0.677204` **aynı kaydın iki farklı alanıdır** — iki ayrı kanıt kaynağı
arasında çelişki YOKTUR. Kullanıcının verdiği iki satırdan ilki (`0.659` ile) tam olarak
`gross_expectancy_r`yi üretir; ikincisi (`0.293` ile, −0.164614) kapının kullanmadığı olasılıkla
hesaplandığı için kayıttaki hiçbir alanı üretmez.

**Etiket disiplini:** `0.878907` basit ağırlıklı beklentidir. "Conservative NET edge" nitelemesi
YALNIZ iki kesintiden sonraki `0.677204` için geçerlidir. v1 raporundaki karışık kullanım
düzeltildi (C-01).

Aynı kayıtla model olasılığı: `conservative_model = −0.366317` → **ekonomik kapı geçilmezdi**.

---

## 6. Eşleşmiş çift kapı — ÇALIŞTIRILDI

Sabit tutulan: geometri, ödeme (`avg_win_r`/`avg_loss_r`), `cost_r`, belirsizlik cezası,
yumuşak ceza, eşikler, `as_of`. **Değişen tek girdi olasılıktır.**

Üretim değerinin yeniden üretimi: **404 / 404 birebir** (`reproduces_production = true`).

|  | model **EVET** | model **HAYIR** |
| --- | --- | --- |
| **ön tahmin EVET** | **4** | **386** |
| **ön tahmin HAYIR** | **0** | **14** |

* Hesaplanabilir: **404 / 404** · eksik: 0 · **anlaşmazlık oranı 0.9554**
* Yön tek taraflı: ön tahminin geçirip modelin geçirmediği **386** satır var, tersi **0**.

### Kanonik işleme bağlanmış yedi aday

| İşlem | Sembol | as_of | p_gate | p_model | ön tahmin kenarı | model kenarı | kapı(ön) | kapı(model) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F00030 | NATGAS/USDT | 2026-09-03T02:30 | 0.630 | 0.319 | +0.610886 | −0.266217 | EVET | HAYIR |
| F00031 | LTC/USDT | 2026-09-03T12:08 | 0.604 | 0.245 | +0.470312 | −0.599410 | EVET | HAYIR |
| F00032 | BNB/USDT | 2026-09-03T15:25 | 0.621 | 0.233 | +0.413867 | −0.731583 | EVET | HAYIR |
| F00033 | AVAX/USDT | 2026-09-03T23:32 | 0.500 | 0.212 | +0.006236 | −0.849515 | EVET | HAYIR |
| F00034 | NVDA/USDT | 2026-09-04T15:00 | 0.627 | 0.218 | +0.629070 | −0.601717 | EVET | HAYIR |
| F00035 | ZEN/USDT | 2026-09-05T18:01 | 0.627 | 0.336 | +0.556520 | −0.304860 | EVET | HAYIR |
| F00036 | ONDO/USDT | 2026-09-07T02:43 | 0.659 | 0.293 | +0.677204 | −0.366317 | EVET | HAYIR |

**Gerçekleşen yedi girişin yedisi de** model olasılığıyla ekonomik kapıyı geçemezdi.

**Bu bir portföy kararı değildir.** Sayaçlar YALNIZ ekonomik kapı verdiktidir; kaldıraç kapısı
(`LEVERAGE_GATE_BLOCKED`), risk kapasitesi (`RISK_CAPACITY_BLOCKED`) ve chief izni ayrı
kapılardır ve burada değerlendirilmemiştir. Kanonik kabul/red nedenleri her satırda ayrıca
raporlanır.

**Uygulanabilir bir "model-kenar politikası" ÖNERİLMEZ.** Hedef uyumu yalnız kısmidir, ikame
edilen olasılık kalibre değildir ve §7 bu olasılığın ayrıştırıcı olmadığını gösteriyor.

---

## 7. Kalibrasyon — hedef-uyumlu, düzeltilmiş

v1'deki kalibrasyon 48 saatlik **sabit ufukta pozitif getiri** etiketiyle ölçülmüştü; bu, model
olasılığının hedefi DEĞİL. Düzeltilmiş ölçüm:

* etiket kuralı `label_v1_scratch_excluded(|r|<0.25 → SCRATCH; r>0 → WIN)` — üretimle aynı,
* **ortak** 72 saatlik gözlem ufku, yalnız kuralla çözülmüş (stop/hedef) gözlemler,
* ufuk sonunda kapananlar `PENDING_CENSORED` — hesaba GİRMEZ,
* taban çizgisi her aday için **yalnız `as_of`tan önce kapanmış kanonik işlemlerden**
  (değerlendirme sonuçlarından türetilmez, aynı etiketlere fit edilmez).

Sansür oranı yüksektir: 404 fırsatın **342'si** `PENDING_CENSORED`; etiketlenen **62** (18 WIN, 44 LOSS, 0 SCRATCH) → sansür oranı **0.8465**.

| Tahmin edici | n | Brier ↓ | Log loss ↓ | AUC | Ortalama skor | Gerçekleşen |
| --- | --- | --- | --- | --- | --- | --- |
| `p_win` (model) | 62 | 0.225417 | 0.650646 | **0.3321** | 0.2637 | 0.2903 |
| `p_win_prior` | 39 | 0.336495 | 0.875191 | 0.5448 | 0.6564 | 0.3077 |
| **geçmiş-yalnız temel oran** | 62 | **0.210559** | **0.614667** | 0.3845 | 0.2361 | 0.2903 |

Okunuşu:

* **Naif taban çizgisi her iki skoru da yeniyor** (Brier ve log loss). Ne ön tahmin ne model,
  "geçmiş kazanma oranını bil" bilgisinin üzerine ölçülebilir bilgi katıyor.
* Model AUC **0.33 < 0.5** → sıralama beklenen yönün TERSİ. Ön tahmin AUC 0.54 → sıralama
  bilgisi yok denecek kadar az; ayrıca seviyesi ciddi biçimde yüksek (0.656 vs 0.308).
* `p_win` ortalamasının (0.289) toplam kazanma oranına (0.28) yakın olması **kalibrasyon
  DEĞİLDİR**; dağılım standart sapması yalnız 0.065 — skor neredeyse sabit.

**Yetersizlik açıkça beyan edilir:** n = 62, tek bir kısa pencere, %97 LONG, yüksek bağımlılık,
ağır sansür (kalan küme hızlı çözülenlere kayar) ve `p_win_prior` FARKLI bir alt kümede (n = 39)
ölçüldü. Bu sonuçlar **kesin değildir**.

---

## 8. Replay sadakati — kategorik değil, EKONOMİK

v1'deki "işaret uyumu 1.0, çıkış nedeni uyumu 1.0" **kategorik** uyumdur. Büyüklük
karşılaştırması (`economic_fidelity`), toleranslar veriden türetilerek:

* zaman toleransı = bar cadence'i (15 dk) — bar içi an ölçülemez,
* R toleransı = o işlemin ortalama 15 dk bar aralığı / işlem riski (o enstrümanda 15 dk
  çözünürlüğün ürettiği belirsizlik).

| Ölçüm | Değer |
| --- | --- |
| EXACT | **0 / 25** |
| WITHIN_TOLERANCE | **18 / 25** |
| MISMATCH | **7 / 25** |
| Azami \|ΔR\| | **0.358989** (F00005) |
| Ortalama \|ΔR\| | 0.070187 |
| Azami \|Δ net USDT\| | 0.18203 |
| Çıkış zamanı ≤ 1 bar | **18 / 25** · azami hata 2 749 000 ms (≈3.05 bar) |
| Dolum sayısı uyuşmazlığı | **0 / 25** |
| Çıkış nedeni uyumu | 25 / 25 |

Simülasyona **girdi olarak verilmeyenler**: kanonik `exit_reason`, kanonik `net_pnl`, gelecek
bilgisiyle ayarlanmış stop. Kanonik `closed_at` yalnız gözlem penceresinin **üst sınırını**
belirler (uzatmayla birlikte) ve bu uzatma şampiyon dâhil bütün politikalar için AYNIdır;
şampiyon çıkışlarının hiçbiri "ufuk" nedeniyle oluşmamıştır (25/25 stop/hedef/BE).

**ÖLÇÜLEMEYENLER** (her satırda listelenir): kısmi çıkış miktarları, bar içi dolum fiyatı,
dolum başına gerçek ücret/funding.

**Etkisi:** ortalama |ΔR| (0.070), v1'de ölçülen challenger farklarıyla (+0.103 … +0.164) AYNI
büyüklük mertebesindedir. Bu, "hiçbir çıkış alternatifi gürültüden ayrılmıyor" sonucunu
zayıflatmaz — **güçlendirir**; ama nokta tahminlerine verilen ağırlığı düşürür.

**Ayrım korunur:** retrospektif 15 dk bar yolları ile gerçekten gözlenmiş üç tam yol
(F00030, F00031, F00032) karıştırılmaz.

---

## 9. Düzeltilen ifadeler (eski kanıt korunur)

| # | Etkilenen | Önceki | Düzeltilmiş |
| --- | --- | --- | --- |
| C-01 | V1 §5.1 / RL-01 | "`conservative_net_edge_r` 256/256 kayıtta `p_win_prior` ile üretiliyor" | Kimlik testi `gross_expectancy_r` üzerindedir; `conservative_net_edge_r` ondan iki kesintiyle TÜRETİLİR. Basit ağırlıklı beklenti "conservative NET edge" değildir. Sayısal sonuç değişmedi. |
| C-02 | V1 §5.1 payda | "256/256 ve 0/404" | Farklı paydalardı; tek paydalı tablo eklendi ve kapı olasılığı 404/404 için geri kazanıldı. |
| C-03 | V1 §2 | "`p_win` (model/kalibre çıktı)" | Champion model YOK, kalibratör fit edilmemiş. `p_win` = 0.5·hiyerarşik önsel + 0.5·legacy tahmin + PAPER_BOUNDED etkisi. "Kalibre" nitelemesi DESTEKLENMEZ. |
| C-04 | RL-02 | 48 sa sabit ufuk etiketiyle kalibrasyon | Hedef-uyumlu etiketle yeniden koşuldu; geçmiş-yalnız taban çizgisi eklendi (§7). |
| C-05 | RL-03 | "sadakat 1.0" | Kategorik uyum ≠ ekonomik denklik; büyüklük tablosu eklendi (§8). |
| C-06 | RL-04 | "düzeltilmiş sıralama" | "Düzeltilmiş" nitelemesi kaldırıldı; karşılaştırma BETİMLEYİCİdir, politika önerilmez. |
| C-07 | genel | "25 kapanışla negatif beklenti kanıtlanmış değil" | Doğru ama eksik: **gerçekleşen zarar bir OLGUdur** (cüzdan −1.7774 USDT); güven aralığı yalnız POPÜLASYON beklentisi hakkındadır. |

Denenen varyantlar ve protokol revizyonları (üç çıkış challenger'ı, eşit-ufuk değişikliği, üç
yollu fold denemesi, temsilci politikası) v1 raporunun `protocol.attempted_variants` alanında ve
bu belgede kayıtlıdır. v1 sonuçları **yeniden koşuldu ve birebir aynı çıktı** (yalnız
`research_id`, kod SHA'sına bağlı olduğu için `d0cf2a0ca55860ad` → `10297e21231b9e1d`).

---

## 10. Sürekli gözlem: mevcut snapshot'lar YETERLİ

| Ölçüm | Sonuç |
| --- | --- |
| Her iki kapı da hesaplanabilir | **404 / 404** |
| Kapı olasılığı geri kazanma azami hatası | **0.0** |
| Yeni üretim enstrümantasyonu gerekli mi | **HAYIR** |
| Hazırlanan yama | **YOK — gerekmiyor** |

Gerekçe: kapı olasılığı ya doğrudan `features.p_win_prior`den ya da `gross_expectancy_r`
kimliğinin tersinden çıkar; model olasılığı `p_win` alanındadır; ödeme, `cost_r`, belirsizlik ve
yumuşak ceza da kayıtlıdır. **Üretim koduna dokunmaya gerek yoktur** — bu yüzden bir
enstrümantasyon yaması hazırlanmadı (hazırlansaydı da KURULMAYACAKTI).

Gözlemcinin kendisi bu aşamadır: sınırlı salt okunur export üzerinde **idempotent** çalışır,
ayrı çıktı üretir, üretime yazmaz. Gözlem sırasında korunanlar: orijinal üretim değerleri,
olayın kendi `as_of`ı (toplama zamanından ayrı), model/kaynak/formül kimliği, etiket kuralı
kimliği. Tarihsel snapshot'lar ve pfexp olayları **yeniden yazılmaz**.

---

## 11. Komut

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out" --stage dual_edge
```

`--stage v1` özgün raporu, `--stage all` ikisini birden üretir. İkinci koşu
`DUAL_EDGE_CACHE_HIT` döner; `--force` yeniden hesaplar, `--offline` ağ isteği yapmaz.
Ölçülen süre: **~2 s** (bar önbelleği sıcak).

---

## 12. Açık kalan sorular

1. Model olasılığı kapıya bağlanmalı mı? **Bu veri bunu desteklemiyor** — model olasılığı
   ayrıştırıcı değil (AUC 0.33) ve naif taban çizgisinin altında. Bağlanırsa 404 adayın 400'ü
   elenir; sistem pratikte işlem açmaz.
2. Doğru düzeltme muhtemelen "hangi olasılığı kullanmalı" değil, **kalibre edilebilir bir
   olasılık üretmek**tir: champion model yok, kalibratör fit edilmemiş, n_closed = 25.
3. `avg_loss_r = −1.0` sabit varsayımı ölçülen −1.0323 ile değiştirilirse bütün kenarlar
   düşer; bu ayrı ve ölçülmemiş bir konudur.
4. `win.add` çağrısı global düğüme işlem başına **iki kez** yazıyor (`leaf=symbol|setup` ve
   `leaf=symbol`); global `n = 50` iken `n_closed = 25`. Oran etkilenmez ama shrinkage gücü
   (α = 10) şişirilmiş n'e karşı uygulanır. Ölçüldü, düzeltme ÖNERİLMEDİ.
