# Tur çekişmesi V2 — pattern kanıtı kNN sorgusu, sonucu değiştirmeden hızlı

Tarih: 2026-10-06 · Taban: `8d37d05` (VPS'te çalışan `8db1faf` + etkisiz araştırma motoru commit'leri) · Dal:
`impl/knn-fast`.

Bu bir **karar-nötr performans onarımıdır**. `SimilarPatternEngine.query`'nin döndürdüğü sözlük — komşular, sıraları,
mesafeler, istatistik, kodlar — eski olay-başına döngüyle **bit bit aynıdır** (§4 kanıt, §6 testler). Strateji, eşik,
defter, config değeri, indeks içeriği, yayım kodu ve kuralı, önbellek anahtarı değişmez. Geri dönüş anahtarı:
`history.evidence_fast_knn: false` (§7).

Gözden geçirme turu 1 (2026-10-06, hüküm GEÇTİ, 4 küçük bulgu — hepsi kapatıldı): zaman damgası sütunu yalnız KAYIPSIZ
int64 dönüşümüyle (§3, §4.1; denetçinin yeniden üretimi test oldu), herhangi bir dizi kurulumu düşerse motor bir kez
işaretlenir (§3), anahtar kanıt motoru kuran HER yere ulaşır (§7), bellek ve tazelik notu (§5.1). Aynı turda pattern
kanıtı ALT SÜRECİ (V1) varsayılan KAPALI oldu (§9).

---

## 1. VPS gerçekleri (sahibin günlüğü, 2026-10-06, `8db1faf`)

| Olay | Değer |
|---|---|
| İndeks | 152.531–152.557 olay, 13 seri; kurulum 120–174 sn |
| Ön ısıtma (13 sembol × LONG+SHORT) | 977,0 / 1.046,5 / 1.054,5 / 996,4 sn (≈ 75 sn/sembol) — alt süreçte de süreç içinde de |
| Yeni sürümü bekleyen turlar | `kanit` 881,2 / 942,4 / 1.756,4 sn (toplam 1.406 / 1.560 / 2.151 sn; olağan tur 316–371 sn) |
| Yayım | her 4h kapanışında İKİ kez (~:10 ve ~:28–:35 UTC; ikincisi 1 olay ekler) → iki tam yeniden hesap |
| Bellek | alt sürecin özel belleği 558–1.536 MB; worker cgroup tepesi 5.957/6.144 MB (%97), hwm RSS 3.520 MB |

Alt süreç yolu (V1) bu yüzden kapatıldı (`history.evidence_subprocess: false`). Teşhis: GIL açlığı değil; tur yeni
sürümün kanıtını **bekliyor** ve kanıtın kendisi sembol başına ~75 sn.

## 2. Kök neden

`SimilarPatternEngine.query` (eski `engine.py` satır 253–311) her sorguda ~152k olayın HEPSİNİ Python döngüsüyle
gezer: olay başına pandas `.iloc` ile çıkış zaman damgası, `self._std(e.snap)`, `np.mean`, `ndarray.std()` ve
`np.corrcoef` (2×16). Olay başına ~0,25–0,3 ms → sorgu başına ~37 sn (VPS) / ~43 sn (yerel); sembol başına iki sorgu. Üretimde küme eşlemesi
boş olduğu için (`risk_profiles.clusters` yok) her yabancı olay "cluster" seviyesindedir ve rejim süzgeci hiç devreye
girmez: aday kümesi pratikte bütün indekstir. Oysa seçim (k = 60) sıralı listenin yalnız ilk birkaç yüz adayına bakar (ölçülen: sorgu başına ort. 326 mesafe yeter).

## 3. Çözüm

Sorgu kodu yalnız `tradingbot/patterns/engine.py`'de değişti; ayrıca config anahtarı (`config_v3.py`), kurulumda
atanması ve başlangıç log satırı (`engine_v3.py`), karar kimliğinden düşülmesi (`engine_v3.py`,
`shared_experience/collector.py`).

* **Diziler (motor başına bir kez, TEMBEL):** ilk sorguda — yani yayımdan SONRA, ön ısıtma iş parçacığında —
  `_KnnIndex` kurulur: `cutoff_ts`, yön başına çıkış zaman damgası (eski `.iloc` aramasının aynı değeri), sembol / küme
  / rejim kodları, standart anlık görüntüler (satır satır motorun kendi `_std`'siyle, sonra float32) ve sorgulanan
  pencerenin birim yolları (float32). Motorla birlikte serbest kalır; `add_series`/ölçekleyici/mum tablosu değişirse
  yeniden kurulur. Yayım kodu, indeks kurulumu ve indeks içeriği DEĞİŞMEZ.
* **1. aşama (vektörel):** eski süzgecin aynısı (embargo, çıkış < sorgu anı, seviye kuralları, evren seviyesinde
  rejim, yön/pencere varlığı) maskelerle; her aday için eski mesafenin **kanıtlı alt sınırı** `lo(c) ≤ d(c)`.
  BLAS çağrısı yok (çarp-topla; `tests/test_evidence_subprocess_v1.py` izin listesi değişmedi).
* **2. aşama (kesin):** adaylar `lo` sırasıyla alınır, mesafeleri ESKİ kodla (`_pair_dist` — eski satırlar aynen)
  hesaplanıp bir yığına konur ve eski TAM sıralamanın sırasıyla üretilir; seçim ESKİ döngüdür (`_select` — eski satırlar
  aynen; eski yol da artık aynı iki fonksiyonu çağırır). Seçim `k`'ya ulaşınca hesap durur.
* **Yedek:** sorgu yolu iyi koşullu değilse (sabit/neredeyse sabit fiyat), |z| ≥ 10^30 ise ya da hızlı yolda
  beklenmeyen bir istisna olursa o sorgu eski döngüyle yapılır (aynı sonuç ya da aynı istisna). Sorgunun ihtiyaç duyduğu
  dizilerden HERHANGİ biri (motor başına ortak kısım, yön, pencere) kurulamazsa — bellek, kayıplı zaman damgası sütunu,
  bozuk bir olay — motor bir kez işaretlenir ve o motorda bir daha DENENMEZ: sonraki sorgular diziyi yeniden kurmaya
  çalışıp düşmez, doğrudan eski döngüye gider (`_knn_arrays`; gözden geçirme bulgusu: önce yalnız ortak kısmın arızası
  önbelleğe alınıyordu, yön/pencere arızası her sorguda ~152k olayı yeniden geziyordu). İşaret motorla birlikte gider
  (sonraki yayımın motoru yeniden dener). Uyarı motor başına bir kez, yalnız motoru kuran süreçte:
  `pattern kNN hızlı yolu kurulamadı; eski döngü kullanılıyor (kanıt aynı, yalnız yavaş): …` (sorgu arızasında
  `… bir sorguda başarısız; …`).

## 4. Kesinlik kanıtı

Gösterim: C = eski döngünün süzgecinden geçen adaylar; d(c) = eski kodun mesafesi (`_pair_dist`, float64);
κ(c) = (d(c), sembol, olay_ts, olay sırası). Eski `sort` (d, sembol, olay_ts) üzerinde **kararlıdır**, yani eşitlikte
olay listesi sırasını korur: eski sıralama tam olarak κ sırasıdır.

1. **Süzgeç aynı.** Hızlı yolun aday kümesi C'dir: aynı yüklemler, önceden çıkarılmış aynı değerler (`e.cutoff_ts`,
   aynı mum sütunundan `int(ts[exit_idx])` — `iloc` ile aynı değer; dizge eşitliği kodlarla). Zaman damgası dizisi
   (`_ts_values`) YALNIZ kayıpsız int64 dönüşümüyle kurulur: sütunun kendi dtype'ı numpy tamsayısı (uint değerleri int64'e
   sığar) ya da her değeri sonlu, tam sayı ve |v| < 2^63 olan numpy kayan noktası; `to_numpy()` dtype'ı değiştirmemeli.
   Başka her şey (pandas genişletme tipleri, object, tarih) → eski döngü. Gözden geçirme bulgusu: NA'lı `Int64` sütunu
   `to_numpy()` ile float64'e döner ve 2^53 üstünü yuvarlar; eski kodun `iloc`'u tam değeri verir → 1. aşamanın çıkış
   süzgeci farklı aday seçebilirdi (2. aşama çıkışı yeniden denetlemez). Denetçinin yeniden üretiminde eski `_ts_values`
   ile 24/24 sorgu farklı, düzeltmeyle 0/24 (motor eski döngüye düşer). Üretimde mum sütunu int64'tür. Mesafesi hesaplanan her aday eski Python yüklemiyle (çıkış damgası dışında) yeniden denetlenir
   (ayrışma → eski döngü).
2. **Sıra aynı.** `_knn_ordered` yığının en küçüğü x'i ancak d(x) < lo(sıradaki hesaplanmamış aday) iken verir.
   Hesaplanmamış her c için d(c) ≥ lo(c) ≥ o lo > d(x), yani κ(c) > κ(x); hesaplanmış olanlar yığında ve κ ≥ κ(x).
   Demek ki x kalanların κ-en küçüğüdür; tümevarımla üretilen dizi eski sıralı listenin KENDİSİdir (tükenene kadar).
3. **Seçim aynı.** `_select` eski döngüdür, adayları birer birer tüketir ve yalnız o ana kadarki öneke bakar (`k`,
   tekilleştirme, min_sep, auto kuralı); aynı dizi → aynı seçilenler (mesafe float'ları dahil). Sözlüğü üreten kod
   (istatistik, seviyeler, komşular) değişmedi.
4. **Alt sınır geçerli:** lo(c) = a(c) − B(c), a = yaklaşık mesafe, B(c) = 2^-24·RMS(z_c)·(1+10^-6) + 2^-25 + 10^-12.
   d = g(s) + ¼(1 − r), g(s) = ½s/(1+s), s = RMS(qz − z_c), r = korelasyon.
   * Anlık görüntü: z_c bitleri eski kodunkiyle aynı (aynı `_std`), float32'ye yuvarlama |Δz| ≤ 2^-24|z| (alt-normal:
     2^-150). RMS bir norm: |RMS(qz − z32) − RMS(qz − z)| ≤ RMS(z32 − z) ≤ 2^-24·RMS(z). İki yolun float64 RMS'i
     (18 negatif olmayan terimin her sırada toplamı, bölme, karekök) göreli ≤ ~12u (u = 2^-53). g(s₁) − g(s₂) =
     ½(s₁ − s₂)/((1+s₁)(1+s₂)) → |Δg| ≤ ½·2^-24·RMS(z) + ~30u.
   * Yol: yalnız İYİ KOŞULLU yollar için (std ∈ [0,5; 2], |ortalama| ≤ 0,25·std — bütün z-skorlu yollar). Eski
     `np.corrcoef` (ortalama çıkarma, iç çarpım — her toplama sırası/FMA/BLAS için γ₁₆ sınırı —, karekök, bölmeler,
     [−1, 1] kırpması) gerçek korelasyon ρ'dan ≤ ~110u sapar; float32 birim vektörlerle iç çarpım ≤ 2^-24 + ~80u.
     → |Δ(¼(1 − r))| ≤ ¼·2^-24 + ~50u.
   * Toplam |a − d| ≤ ½·2^-24·RMS(z) + ¼·2^-24 + ~10^-13 < B: float32 terimlerinde 2 kat, float64 terimlerinde
     50 kattan fazla pay. İyi koşullu olmayan olay yolları için lo = −∞ (her zaman eski kodla hesaplanır).
   * Korumalar (sağlanmazsa eski döngü): |z| < 10^30 (taşma yok, d sonlu ve NaN değil); sorgu yolu iyi koşullu.

Sonuç sayısal kütüphane ayrıntısına (numpy indirgeme sırası, BLAS çekirdeği) DAYANMAZ: çıktıya giren her değer eski
kodla hesaplanır; 1. aşama yalnız "hangi adayın mesafesi hesaplanacak" sorusunu yanıtlar ve hatası sınırla örtülüdür.

## 5. Ölçüm (yerel, 4 çekirdek, Python 3.12, numpy 2.5; `scripts/bench_knn_query.py`)

Gerçekçi sentetik indeks: 13 seri × 11.860 4h bar = **152.503 olay** (BTC bağlamı, funding, düz fiyat ve sıfır hacim
aralıkları). Eski ve hızlı yol AYNI indekste; sonuçlar `==` ve JSON ile karşılaştırıldı.

| Ölçü (152.503 olay, 13 seri, `k=60`, auto, pencere 64) | Eski döngü | Hızlı yol | Oran |
|---|---:|---:|---:|
| Ön ısıtma, 13 sembol × LONG+SHORT — SOĞUK (yayımdan sonraki gibi; dizi kurulumu dahil) | **1.125,8 sn** (13 sembolün hepsi ölçüldü) | **5,3–5,5 sn** (ilk sembol 3,3–3,4 sn: dizi kurulumu ~3,2 sn) | **~205×** |
| Aynı, diziler hazırken (aynı indeksin ikinci ön ısıtması) | 1.125,8 sn | 2,45–2,66 sn | ~420–460× |
| Sembol başına (LONG+SHORT) | 80,8–92,2 sn, ort. 86,6 | 0,16–0,25 sn, ort. ~0,19 | ~420–530× |
| Sorgu başına | ~43 sn | ~0,09–0,10 sn | ~450× |
| Eski kodla hesaplanan mesafe, sorgu başına | 152,5k (hepsi) | ort. 326 (26 sorguda 8.472) | — |
| Sonuç | | 26/26 sorgu: sözlük `==` ve JSON AYNI | |

Yerelde eski yol sembol başına 86,6 sn (VPS: ~75 sn) — aynı mertebe; oranlar VPS'e taşınabilir. Hedef (≥ 20×) soğuk
ön ısıtmada ~10 kat aşıldı.

Bellek (aynı koşu): motor başına kalıcı diziler **26,8 MB** (`knn_index_nbytes`; pencere 64, iki yön —
olay başına 184 B); ilk sorgu (dizi kurulumu + sorgu) boyunca tracemalloc ile ek tepe **32,4 MB**, sonra kalan
25,5 MB; RSS 809,5 → 825,7 MB (+16 MB), VmHWM değişmedi. Sıcak bir sorgunun geçici tepe belleği 5,8 MB. Eski döngü
sorgu başına ~152k demetlik aday listesi tutuyordu (geçici, ~14 MB); hızlı yol bunu tutmaz. Yayım anında eski ve
yeni motor kısa süre birlikte yaşar: en çok ~2 × 27 MB. Worker'ın 6 GiB'lık cgroup'unda (RSS 2,6–3,5 GB) bu %1'in altında;
alt süreç yolunun (V1) 0,5–1,5 GB'lık özel belleğiyle karşılaştırılamayacak kadar küçük.

### 5.1 Bellek ve tazelik (gözden geçirme notu)

* **Bellek kimde, ne kadar, ne zaman gider.** Diziler motor nesnesinin özniteliğidir (`_knn_ix`); motorla birlikte
  serbest kalır (dış referans yok; test `test_extra_memory_is_bounded_and_freed_with_the_engine`). Kalıcı ek: olay başına
  100 B + sorgulanan yön başına 9 B + pencere başına 66 B (üretim çağrısı: pencere 64, iki yön → 184 B/olay, 152,5k olayda
  ~27 MB). Yayım anında eski motor, ona referans tutan son tur/ön ısıtma işi bitene dek yaşar: en çok ~2 × 27 MB, birkaç
  dakika. Kurulum anındaki geçici tepe ~32 MB (parça parça, `KNN_CHUNK`). Alt süreç açıkken (varsayılan KAPALI, §9): fork anında
  motorun dizileri kuruluysa alt süreç onları yazınca-kopyala ile paylaşır; kurulu değilse kendi kopyasında kurar (alt
  sürecin özel belleğine ~27 MB) ve iş bitince alt süreçle birlikte gider. Kurulum düşerse (ör. `MemoryError`) motor işaretlenir, ikinci
  deneme yapılmaz (§3).
* **Tazelik.** Diziler bir indeks SÜRÜMÜNÜN (motor nesnesinin) içeriğinden türetilir; üretimde yayımlanan motor bir daha
  değiştirilmez (yeni veri = yeni motor = yeni diziler). Geçerlilik anahtarı (`_knn_key`) olay listesini (nesne, uzunluk,
  son olay), ölçekleyiciyi (`_mu`, `_sd` nesneleri) ve mum tablolarını (DataFrame nesneleri) kimlikle izler: `add_series`,
  yeni ölçekleyici ya da bir mum tablosunun DEĞİŞTİRİLMESİ dizileri yeniden kurdurur. Görmediği tek şey yerinde
  değiştirmedir — mevcut bir olayın `snap`/`path`/`outcomes`'ının ya da mum DataFrame'inin hücrelerinin yayımdan sonra
  yerinde değiştirilmesi (denetçi sentetik olarak gösterdi: bayat dizi farklı sonuç verir). Üretimde ve testlerde hiçbir
  kod bunu yapmaz; yapılırsa `history.evidence_fast_knn: false` ya da yeni bir motor gerekir. Sürüm sınırı: tur ve ön
  ısıtma motoru `IndexRefresher.bundle`'dan alır, yani her sürümün kendi dizisi vardır; eski sürümün dizisi yeni sürümün
  sorgusuna hiç girmez (motorlar dizi paylaşmaz).

VPS'e ölçekleme (tahmin): eski yol orada sembol başına ~75 sn ölçüldü; hızlı yolun maliyeti ~152k satırlık 1. aşama
(onlarca ms) + ~100–300 eski-kod mesafesi (sorgu başına onlarca ms) + motor başına bir kez dizi kurulumu (birkaç sn).
Yayım başına ön ısıtma ~1.000 sn yerine birkaç saniye beklenir; yeni sürümü bekleyen tur artık beklemez.

## 6. Testler

* `tests/test_knn_fast_identity_v1.py` — her karşılaştırmada sözlük `==`, JSON ve SEÇİLEN LİSTENİN TAMAMI (yuvarlanmamış
  mesafe float'ları bit bit, seviye, olay nesnesi): gerçekçi 13 seri üzerinde rastgele sorgular (bütün seviyeler, iki
  yön, dört pencere, k ∈ {0 … 400}, tarihsel idx, query_ts/now_ts, embargo 0–3, min_separation, min_sample 3–500, küme
  eşlemesi); üretim çağrısı 13 sembol × 2 yön; çekişmeli indeks (birebir aynı iki seri → mesafe eşitlikleri; aynı
  sembol spot + futures + 1h → aynı (sembol, olay_ts); bütün sütunu NaN anlık görüntüler; sıfır-sapmalı ve normalize
  edilmemiş yollar; aday listesinin tükenmesi; min_sep ile derin tarama); embargo/çıkış sınırları (eşik ve eşik±1,
  gerçekten isabet eder); alt sınırın >10k adayda geçerliliği; 2. aşamanın sınırı sağlayan HER yaklaşıklıkla (rastgele,
  sıkı uç lo = d, kısmen −∞) aynı seçimi vermesi; yedeğe düşüş, geri dönüş anahtarı, dizilerin yeniden kurulması ve
  motorla birlikte serbest kalması; config (yalnız bool), karar kimliği, kurulumdaki atama, başlangıç log satırı; depodaki
  tur/alt süreç testlerinin indeks verisinde yeniden oynatma; GERÇEK turlar (`test_tour_perf_no_decision_change_v1`
  düzeneği: 5 tur, iki yayım, süreç içi ön ısıtma = VPS ayarı) hızlı yol ile eski döngü arasında `state/` bayt bayt aynı.
* Gözden geçirme turu 1 (2026-10-06) testleri: `test_lossy_timestamp_columns_never_enter_the_fast_path` (denetçinin
  yeniden üretimi: NA'lı `Int64` sütunu; eski `_ts_values` ile 24/24 sorgu farklıydı), kayıpsız/kayıplı dtype tabloları
  (`int64/int32/uint64/float64/float32` kabul ve `iloc`'a eşit; NA'sız `Int64`, object, NaN, kesirli, uint taşması,
  tarih, 2^63 ret), `test_an_array_build_failure_is_cached_per_engine_and_warned_once` (yön ve pencere dizisi arızası
  bir kez; eski kodla 6 kurulum denemesi), `test_the_cli_pattern_engine_applies_the_switch`.
* Mevcut karar-nötrlük testleri değişmeden geçer: `test_tour_perf_no_decision_change_v1.py` (5 gerçek tur, iki yayım,
  `state/` bayt bayt), `test_evidence_subprocess_v1.py`, `test_evidence_prewarm_v1.py`, `test_pattern_evidence_cache.py`,
  `test_patterns.py`. Karar kimliği özetini "alan eklenmeden önceki" haliyle karşılaştıran dört testte yeni anahtar da
  düşülür (`evidence_subprocess` ile aynı kalıp).

## 7. Geri dönüş anahtarı

```yaml
history:
  evidence_fast_knn: false   # varsayılan true; yalnız true/false (tırnaklı "false" config doğrulamasında reddedilir)
```

`false` → `SimilarPatternEngine.fast_query = False`: eski olay-başına döngü (aynı `_pair_dist`/`_select` satırları),
dizi kurulmaz. Anahtar, kanıt için `SimilarPatternEngine` kuran HER yerde atanır (gözden geçirme bulgusu; depoda
`SimilarPatternEngine(` yalnız bu ikisinde ve iki ölçüm betiğinde geçer):

* worker: `TradingEngineV3._build_pattern_index` — turlar, ön ısıtma, (açıksa) alt süreç (fork edilen AYNI motor nesnesi,
  özniteliğiyle birlikte) ve yenileyicinin her yayımı (her sürüm bu kurucudan geçer);
* CLI: `cli_v3._pattern_engine` — `pattern-query`, `evidence-show --live` ve `historical-replay` (ReplayEngine kendi
  motorunu kurmaz, kendisine verilen bu motoru sorgular);
* `scripts/bench_knn_query.py` / `scripts/bench_tour_contention.py` ölçüm betikleridir; karşılaştırma için iki yolu
  kendileri seçer (config okumaz).

Sınıf varsayılanı (`SimilarPatternEngine.fast_query = True`) yalnız config'siz kullanımlar (testler, betikler) içindir. Anahtar iki değerinde de bit-aynı kanıt verdiği için karar kimliğine
(`config_hash`) girmez; etkin değer başlangıçta bir kez loglanır: `pattern kanıtı kNN sorgusu: HIZLI yol
(history.evidence_fast_knn=True; …)` ya da `… ESKİ olay-başına döngü (…)`. Worker yeniden başlatılınca geçerli.

## 8. VPS'te bakılacaklar ve riskler

1. Yayım sonrası `pattern kanıtı ön ısıtıldı: sürüm N, 13 sembol hesaplandı, … S sn` → S ~1.000 yerine birkaç saniye.
   `tur fazları` satırında `kanit` ~0; yeni sürüm yayımlandıktan sonraki tur olağan sürede (316–371 sn).
2. `pattern kNN hızlı yolu` ile başlayan uyarı YOK. Varsa kanıt yine aynıdır ama yol yavaştır (S yine büyük) → bildir.
3. Bellek: motor başına ~27 MB kalıcı ek dizi (pencere 64, iki yön); yayım anında eski ve yeni motor birlikte
   yaşarken ~54 MB. `MemoryPeak`'te belirgin artış beklenmez (alt süreç kapalıyken).

Dağıtım betiğinin `--check`'i (bölüm "PATTERN KANITI HIZLI kNN") bunları otomatik çıkarır: yayım başına ön ısıtma
saniyesi ve sembol sayısı, yayımdan sonraki turların `pattern kanıtı` faz süresi, başlangıç satırı, hızlı yol uyarıları,
worker belleği ↔ `MemoryMax`. Beklenen VPS sinyali: yayımla çakışan turlar ~5–6 dk (bugün 23–36 dk), ön ısıtma birkaç sn
(bugün ~1.000 sn).

Riskler (sahibin değerlendirmesine):

* **Saat-duvarı zamanı değişir, kod ve kural değişmez.** Kanıt dakikalar yerine saniyelerde hazır olduğundan turlar
  yeni sürümü beklemez. Ayrıca 4h kapanışındaki İKİNCİ indeks kurulumu artık süreç içi ön ısıtmayla CPU/GIL için
  yarışmaz; kurulum daha kısa sürüp ikinci yayım saat olarak **daha erken** gelebilir. Yayım kodu, iş parçacığı ve
  kuralı aynıdır; ama belli bir turun bir yayımdan önce mi sonra mı okuduğu (hangi sürümü gördüğü) yüke bağlı olarak
  değişebilir — V1 §5 ile aynı durum; bugün de makinenin yüküyle değişiyor. Uygun görülmezse anahtar kapatılır.
* Olaylar yayımdan sonra yerinde değiştirilirse (üretimde yapılmıyor; paket değişmez) diziler bayatlar; anahtar yalnız
  `add_series`/ölçekleyici/mum tablosu değişimini görür.
* Kanıt §4'ün varsayımlarına dayanır: IEEE-754 float64/float32 (x86-64/ARM64), numpy'nin float32 dönüşümünün en
  yakına yuvarlaması. Testler bunu her koşuda sınar (alt sınırın geçerliliği, kullanılan pay < %75).
* İlk sorgu motor başına dizi kurulumunu öder (152k olayda yerelde birkaç sn; ön ısıtma iş parçacığında).

## 9. Pattern kanıtı alt süreci (V1) varsayılan KAPALI (2026-10-06)

`history.evidence_subprocess` kod varsayılanı `true` → `false`. Neden (VPS, `8db1faf`, §1): alt süreç worker cgroup'unda
0,5–1,5 GB özel bellek tuttu (cgroup tepesi 5.957/6.144 MB, %97) ve turları KISALTMADI — tur yeni sürümün kanıtını
bekliyordu ve kanıt iki yolda da sembol başına ~75 sn sürüyordu. Hızlı kNN ile ön ısıtma birkaç saniyedir; GIL'i paylaşan
süre de o kadar kısalır, alt süreç fayda getirmeden bellek ikiye katlar. Sahip anahtarı 13:56 UTC'de config.yaml'a
`evidence_subprocess: false` satırıyla kapatmıştı; yeni varsayılan bu durumu config satırı olmadan sürdürür.

* Anahtar çalışmaya devam eder: `evidence_subprocess: true` (Linux) alt süreç yolunu aynen açar (V1'in bütün testleri
  açık koşuda sürer: `tests/test_evidence_subprocess_v1.py` yardımcısı değeri açıkça verir;
  `test_tours_are_identical_with_the_child_switch_on`, `…_tours_own_misses_are_served_by_the_child`).
* Karar kimliğine (`config_hash`) iki değerinde de girmez (değişmedi; `test_the_switch_is_not_part_of_the_decision_identity`).
* Başlangıç satırı varsayılanda: `pattern kanıtı sorguları: süreç içinde (history.evidence_subprocess=False)`.
* Değişen testler: `test_the_switch_defaults_on_and_config_can_turn_it_off` → `…_defaults_off_and_config_can_turn_it_on`;
  `test_the_switch_is_linux_only_bool_only_and_logged_at_startup` (varsayılan satırı + açık `true`);
  `test_tour_perf_no_decision_change_v1`: üretim varsayılanı koşusu artık süreç içi (`child_started == 0`), eski "süreç
  içi kill switch" koşusu yerine alt süreç AÇIK koşu (`child=True`), tur ıskalarını alt sürece yollayan koşu `child=True`.
* VPS'teki config.yaml'da sahibin eklediği `evidence_subprocess: false` satırı yeni kodla da geçerlidir (aynı değer) ama
  dağıtım betiği temiz ağaç ister: betik o satırı (YALNIZ o satırı) tanır, doğrulanmış yedekten sonra geri alır ve geri
  almada aynen yerine koyar.
