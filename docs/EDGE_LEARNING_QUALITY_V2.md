# EDGE & LEARNING QUALITY V2

Amaç: **daha az ama daha kaliteli işlem**. Optimize edilen hedef win rate DEĞİLDİR; net OOS
expectancy + payoff + drawdown/tail kontrolü + calibration + execution kalitesi + rejim/sembol
tutarlılığıdır. Kârlılık garantisi verilmez.

Bu belge dört düzeltmeyi ve bunların nerede zorlandığını (kod + test) anlatır.

---

## 1. Tek sonuç bir olasılık tahminini doğrulamaz da yanlışlamaz da

**Eskiden** (`learning.py::_diagnose`, `learn/postmortem.py`):

```
"Model giriş öncesi P(kazanç)=%29 demişti → yanıldı"
"Model giriş öncesi P(kazanç)=%45 demişti → isabetli"
```

%29 olasılıklı bir olay yaklaşık her üç denemenin birinde gerçekleşir; gerçekleşmesi tahmini
yanlışlamaz. Aynı şekilde %45 diyip kaybetmek tahmini doğrulamaz.

**Şimdi** (`learn/prob_semantics.py`) tek sonuç yalnız **katkı** olarak kaydedilir:

| Alan | Anlamı |
| --- | --- |
| `brier_contribution` | `(p − y)²` |
| `log_loss_contribution` | `−[y·ln p + (1−y)·ln(1−p)]` |
| `surprise_bits` | `−log₂ P(gerçekleşen sonuç)` — sürpriz ÖLÇÜSÜ, hata ölçüsü değil |
| `calibration_bucket` / `bucket_n` | tahminin düştüğü güvenilirlik kovası ve örnek sayısı |
| `shrunk_observed_rate` | kova gözlemi, kova orta noktasına doğru büzülmüş (beta-binom) |
| `ci95_low/high` | Wilson skor aralığı |

Üretilebilen **tek** kod kümesi:

```
HIGH_SURPRISE_OUTCOME | LOW_SURPRISE_OUTCOME
CALIBRATION_EVIDENCE_ADDED | INSUFFICIENT_CALIBRATION_SAMPLE
```

`MODEL_WAS_RIGHT` / `MODEL_WAS_WRONG` üretilmez; `FORBIDDEN_VERDICTS` sabiti bunu testte
kilitler.

### CalibrationBook değişmezleri

* **No-lookahead**: `label_ts` bilinmiyorsa ya da `as_of`tan sonraysa kayıt REDDEDİLİR.
* **Duplicate**: aynı `trade_id` bir kez sayılır; gölge kopya gerçek olanı ikizleyemez.
* **Gerçek > gölge**: gölge `weight < 1` ile girer ve `real_n`e SAYILMAZ. Kova yeterliliği
  yalnız `real_n`den okunur → gölge tek başına bir kovayı "yeterli" yapamaz
  (`shadow_weight >= 0.5` `ValueError`).
* **Sabit kova sayısı** → yüksek kardinalite oluşamaz.
* Kalibrasyon **aktif RiskEngine'e dokunmaz**; ilk aşamada yalnız rapor/advisory/challenger'dır.
  Mevcut `PAPER_BOUNDED` `max_fraction` (%5) sınırı **büyütülmemiştir**.

---

## 2. Edge ile execution ayrıldı

Her kayıp "giriş yanlıştı" değildir. `learn/edge_execution.py` önce **tamamen gözlemsel**
sınıflar üretir:

```
LOW_MFE_STOP          HIGH_MFE_REVERSAL      TARGET_CAPTURED
PARTIAL_PROFIT_THEN_BE COST_DOMINATED        SLIPPAGE_DOMINATED
GAP_AFFECTED          NORMAL_PLANNED_LOSS    REGIME_TRANSITION_OBSERVED
CORRELATED_MOVE_OBSERVED                     DATA_INSUFFICIENT
```

ve bunların üstüne yalnız **araştırılabilir hipotez** koyar:

```
ENTRY_QUALITY_CANDIDATE  EXIT_POLICY_CANDIDATE  COST_FILTER_CANDIDATE
REGIME_FILTER_CANDIDATE  THEME_RISK_CANDIDATE   NO_POLICY_CHANGE
```

Her kayıt `classification_version`, `evidence_level`, `n_similar`, `mfe_r`, `mae_r`,
`realized_r`, `capture_ratio`, `bars_held`, `exit_reason`, `fee_drag_r`, `funding_drag_r`,
`slippage_drag_r`, `regime_at_entry/exit`, `data_quality` ve `causal_claim=false` taşır.

### R normalizasyonu neden şart

MFE/MAE yüzde olarak ölçülür; karşılaştırılabilir olması için **stop mesafesine** bölünür.
Gerçek örnek (yerel PAPER state):

| İşlem | MFE % | Stop mesafesi % | **MFE R** | Sınıf |
| --- | --- | --- | --- | --- |
| KORU/USDT SHORT | 3.68 | 14.65 | **0.25** | `LOW_MFE_STOP` |

Yüzde olarak bakınca "%3.7 lehe gitti sonra döndü" (çıkış politikası sorusu) görünür; R olarak
bakınca **0.25R** çıkar — yani fiyat risk birimi cinsinden neredeyse hiç lehe gitmemiştir ve bu
bir **giriş kalitesi** sorusudur. Yüzde bazlı okuma yanlış politika hipotezi üretiyordu.

**Eksik veri iyimser sıfıra dönmez**: stop mesafesi bilinmiyorsa `mfe_r`/`mae_r` `None` kalır ve
`DATA_INSUFFICIENT` işaretlenir.

### Capture ratio (kısmi kapanış dâhil)

`capture_ratio = realized_r / mfe_r`

* `realized_r` kısmi çıkışların **ağırlıklı net** sonucudur (defterden gelir).
* `mfe_r` **tam pozisyonun** görebildiği en iyi R'dir.
* Yani TP1'de yarı kapatılıp kalan başa-baş kapanan işlemde oran, "mevcut en iyi hareketin ne
  kadarını bankaya yazdık" sorusunu ölçer — "TP1 doğru muydu" sorusunu DEĞİL.
* `mfe_r <= 0` → **TANIMSIZ** (`None`), sıfır değil.
* `0 < mfe_r < 0.25R` → değer raporlanır ama `NOISY_NEGLIGIBLE_EXCURSION` işaretlenir
  (0.05R'ye bölmek −20 gibi anlamsız katsayı üretir).

---

## 3. Ders yaşam döngüsü ve kanıt seviyeleri

```
OBSERVATION → RESEARCH_HYPOTHESIS → VALIDATED_POLICY_CANDIDATE → APPLIED_BOUNDED
                     ↘ REJECTED → RETIRED
```

* **Tek işlem `OBSERVATION` seviyesini AŞAMAZ.** `promote_evidence_level(n_supporting=1)` her
  koşulda `OBSERVATION` döner.
* `RESEARCH_HYPOTHESIS` için asgari benzer örnek (`MIN_SIMILAR_FOR_HYPOTHESIS = 8`) ve destek >
  çelişki şartı.
* `VALIDATED_POLICY_CANDIDATE` yalnız gerçek walk-forward OOS + execution senaryoları geçilirse.
* Atlamalı geçiş `ValueError` (`lesson_store.ALLOWED_TRANSITIONS`).
* **Promosyon geçmişi DEĞİŞTİRMEZ**: `transition()` yeni sözlük döner, orijinal ders kaydı
  dokunulmadan kalır, `status_history` append-only büyür.

---

## 4. Kayıpsız ders saklama — 200 saklama sınırı DEĞİLDİR

**Eskiden**: `learning.py` her kapanışta `s.lessons = s.lessons[-200:]` yapıyordu. 200. dersten
sonra her yeni ders bir eskisini KALICI olarak siliyordu; dashboard da bunu "kayıt defteri en
fazla 200 ders tutar" diye yazıyordu.

**Şimdi** (`learn/lesson_store.py`, `journal_archive.SegmentArchive` üstüne):

```
ders üretici (learning.py::_diagnose)
→ sıcak pencere (learning.json → lessons, varsayılan 200)
→ atomik mühürlenmiş segment (.jsonl.gz + sha256)
→ manifest + ders indeksi (bağlam anahtarı → segment)
→ sınırlı aggregate sayaçları
→ retrieval (HOT / INDEXED / AGGREGATE)
→ dashboard
```

| Değişmez | Nasıl zorlanıyor |
| --- | --- |
| Arşivsiz silme YOK | `rotate()` `ArchiveError`/`OSError`da `hot`u OLDUĞU GİBİ döndürür |
| Varsayılan saklama SINIRSIZ | `max_segments=0` → `UNLIMITED_NO_DELETION` |
| Retrieval O(total archive) OLAMAZ | O(hot) + en fazla `max_segments_scanned` segment + O(1) aggregate |
| Yüksek kardinalite YOK | `max_cells` tavanı, aşan anahtar `cells_dropped` sayılır |
| İndeks TÜREV veridir | bozulursa `rebuild_index()` arşivden yeniden kurar |
| No-lookahead | `query(as_of=...)` sonrasında oluşmuş ders döndürmez |
| Yedeğe dâhil | arşiv `state/` altındadır → mevcut `ops/backup.py` kapsar |

### `min_rotate_block` neden var

`SegmentArchive.commit()` manifesti her çağrıda baştan yazar (maliyet O(segment sayısı)). Her
taşmada tek tek mühürlemek segment sayısını şişirir ve toplam maliyeti O(segment²)'ye taşır.
Bu yüzden taşma `min_rotate_block` (varsayılan 50) kadar birikene dek mühürleme **ertelenir**;
ders SİLİNMEZ, sıcak liste geçici olarak pencereyi aşar.

### Dashboard metni

```
Ekranda son 200 ders gösteriliyor.
Ömür boyu ayrıntılı dersler kayıpsız arşivleniyor.
Retrieval kapsamı: HOT / INDEXED / AGGREGATE.
```

---

## 5. Ajan ağırlığı: ÖNCE / DELTA / SONRA

"Trend yanıldı, ağırlığı düştü" denetlenebilir bir ifade değildir. Her ders artık
`agent_contributions` taşır:

`agent`, `outcome_contribution` (HIT/MISS/NEUTRAL), `sample_count`, `hits`, `laplace_rate`,
`shrinkage_prior`, `weight_before`, `applied_delta`, `weight_after`, `context_key`,
`evidence_quality`.

Tek sonucun deltası Laplace prior (kütle 4) nedeniyle `1/(n+4)` mertebesinde kalır — testte
`|applied_delta| < 0.05` olarak sabitlenmiştir.

---

## 6. Challenger'lar — hepsi OFFLINE

Aktif `RiskEngine`, kaldıraç, stop, TP, ledger, outbox ve gateway yolu **değişmez**.

### Çıkış politikası (`quant/exit_challenger.py`)

Champion + **en fazla 3** önceden tanımlı challenger (4.'sü `ValueError`): `EARLY_PARTIAL_BE`,
`VOL_TRAILING`, `TIME_STOP`. Parametre grid'i yoktur.

* Aynı giriş, aynı bar dizisi, aynı maliyet modeli. `cost_model_key` parmak izi farklıysa
  `assert_same_cost_model()` karşılaştırmayı REDDEDER.
* Maliyet = `cost_per_fill_r × dolum sayısı` → maliyet arttıkça net sonuç **asla iyileşemez**.
* Rapor iki soruyu **ayrı** gösterir:
  * `high_mfe_stop_rescue` — yüksek MFE→stop işlemlerinde zararı azaltıyor mu?
  * `big_winner_truncation` — ZRO gibi büyük kazananları erken kesiyor mu?

### Seçicilik (`quant/selectivity.py`)

Yeni indikatör EKLENMEZ; yalnız zaten kayıtlı sinyaller birleştirilir.

```
train      → eşik BURADA fit edilir (tek yer)
validation → adaylardan BİRİ seçilir
test       → yalnız SEÇİLEN aday ölçülür
holdout    → hiçbir seçime girmez
```

`select_candidate()` imzası test/holdout satırı **kabul etmez** → sızıntı yapısal olarak
imkânsızdır. Kapsam kapısı (`MIN_TRADES_ABS=30`, `MIN_TRADE_FRACTION=0.30`, `MIN_SYMBOLS=5`)
fail-closed'dır: işlem sayısını düşürmek tek başına başarı değildir.

### Portföy ısısı (`quant/risk_v2.portfolio_heat_challenger`)

Yalnız `ADVISORY` / `COUNTERFACTUAL_BLOCK` / `COUNTERFACTUAL_SIZE_REDUCTION` üretir. Eksik
korelasyon verisinde **bağımsızlık varsayılmaz** (`independence_assumed=False`); aynı yöndekiler
konservatif tek kümede toplanır.

---

## 7. Terfi kapıları

`PROMOTE_CANDIDATE` **yalnız araştırma önerisidir**; otomatik terfi yolu yoktur.

Mevcut kapılara ek olarak:

| Kapı | Neden |
| --- | --- |
| `PAYOFF_RATIO` (≥ 1.0) | %75 win-rate + `+0.40R`/`−1.00R` → beklenti yalnız `+0.05R`; tek kötü seri siler |
| `TAIL_LOSS_ABSOLUTE` (≥ −3.0R) | göreli oran kapısı champion tail'i ölçülememişse kötü tail'i geçiriyordu |
| `CALIBRATION` (Brier ≤ 0.30) | ölçülmüşse zorunlu; ölçülememişse UYARI — sahte blokaj değil |

Sekiz işlemle terfi mümkün değildir: `min_samples = 100` ve `insufficient_sample` fail-closed.
Bütün eşikler `PromotionGates` ile yapılandırılabilir ve gerekçeleri kod içinde yazılıdır.

### Win rate doğru kurgusu

| Profil | Win rate | Avg win | Avg loss | Payoff | Expectancy (maliyet öncesi) | Sonuç |
| --- | --- | --- | --- | --- | --- | --- |
| A | %75 | +0.40R | −1.00R | 0.40 | **+0.05R** | `PAYOFF_RATIO` kapısında düşer |
| B | %50 | +1.50R | −1.00R | 1.50 | **+0.25R** | kapıları geçebilir |

Dashboard "yüksek win rate = başarılı" iddiasında bulunmaz; oran, payoff ve beklenti tek kartta
**birlikte** gösterilir.

---

## 8. Dosya haritası

| Dosya | Rol |
| --- | --- |
| `tradingbot/learn/prob_semantics.py` | Brier/log-loss/sürpriz, no-lookahead `CalibrationBook` |
| `tradingbot/learn/edge_execution.py` | gözlem/hipotez kodları, R normalizasyonu, capture ratio, kanıt seviyeleri |
| `tradingbot/learn/lesson_store.py` | ders yaşam döngüsü + kayıpsız arşiv/indeks/aggregate/retrieval |
| `tradingbot/learning.py` | `_diagnose` gözlem+hipotez, ajan delta, kayıpsız budama |
| `tradingbot/learn/postmortem.py` | aynı olasılık/hipotez sözleşmesi |
| `tradingbot/quant/exit_challenger.py` | çıkış politikası challenger'ları (offline) |
| `tradingbot/quant/selectivity.py` | seçicilik challenger'ları + fold disiplini |
| `tradingbot/quant/champion.py` | payoff/tail/kalibrasyon terfi kapıları |
| `tradingbot/quant/risk_v2.py` | portföy ısısı challenger'ı (advisory) |
| `tradingbot/dashboard/templates.py` | retention/calibration/quality/observation blokları |
| `tests/test_edge_learning_quality_v2.py` | bölüm 16'daki 30 zorunlu test + ek sözleşmeler |

---

## 8b. Performans (ölçülmüş)

`min_rotate_block=50`, `hot_window=200`, 50 sembol × 3 setup × 3 rejim, ders başına ~150 bayt metin.

| ders | segment | indeks | rotate p50 | rotate p95 | rotate toplam | sorgu p50 | sorgu p95 | taranan segment |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 0 | 0 B | 0.004 ms | 0.004 ms | 0.0 s | 0.04 ms | 0.08 ms | 0 |
| 1.000 | 13 | 122 KB | 0.014 ms | 46.7 ms | 0.6 s | 9.3 ms | 11.0 ms | **4** |
| 10.000 | 163 | 143 KB | 0.017 ms | 56.0 ms | 8.6 s | 12.5 ms | 15.7 ms | **4** |
| 100.000 | 1.663 | 144 KB | 0.014 ms | 85.5 ms | 126.9 s | 34.2 ms | 38.8 ms | **4** |

* Aday başına taranan segment sayisi her ölçekte **4'te sabittir** → O(total archive) tarama yok.
* `aggregate` tüm geçmişi kapsar ve O(1) okunur (hücre sayısı 412'de sabit, `cells_dropped=0`).
* İndeks boyutu 1k→100k arasında **sabit** kalır (122→144 KB). `lesson_index_v1`'de segment başına
  anahtar listesi tutuluyordu ve aynı testte indeks **11,72 MB**'a, sorgu p50 **129,5 ms**'ye
  çıkıyordu; ters indeks bunu 144 KB / 34,2 ms'ye indirdi.
* Kalan doğrusal maliyet: `SegmentArchive.commit()` manifesti baştan yazar. 100k derste toplam
  rotasyon 126,9 s'dir — ama bu **1.663 rotasyonun toplamıdır**; tur başına p95 85,5 ms.
* 15 dakikalık worker temposunda tur başına ek yük: bir sorgu (~34 ms) + seyrek bir rotasyon
  (p95 85 ms) → döngünün **binde birinden azı**.

---

## 9. Bilinen sınırlar

* PAPER örneklemi **çok küçüktür**. Hiçbir kapı geçilmemiştir ve hiçbir politika değişmemiştir.
* Calibration kovaları `MIN_BUCKET_SAMPLE = 20` altında hüküm vermez; bugünkü veride hiçbir kova
  yeterli değildir (`INSUFFICIENT_CALIBRATION_SAMPLE`).
* **Exit challenger bugün gerçek PAPER verisiyle ÇALIŞTIRILAMAZ.** `engine_v3`,
  `LearnerV2.on_trade_closed`u `price_path` olmadan çağırır; bu yüzden
  `state/trade_memory.jsonl` içindeki exit kayıtlarında `price_path: []` durur. Canlı worker'a
  kapanış başına mum çekmek EKLENMEDİ (sıcak döngüye tarama yasağı). Kapatma yolu:
  `bars_from_frame()` köprüsüyle challenger'ı offline replay veri setinden
  (`replay/engine.py::HistoricalReplay`, aynı mumlara zaten sahip) beslemek. Yolu olmayan
  işlemler `skipped_no_data` sayılır; sessizce sıfır sayılmaz.
* `SegmentArchive.commit()` manifest yeniden yazma maliyeti segment sayısıyla doğrusaldır;
  `min_rotate_block` bunu üretim temposunda önemsiz kılar ama sınır kaldırılmamıştır.
* Kâr yoğunlaşması ölçümü pozisyon kaydında `realized_pnl` varsa yapılır; yoksa `None` döner
  (sıfır değil).
