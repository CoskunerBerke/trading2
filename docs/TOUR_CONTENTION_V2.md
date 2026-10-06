# Tur çekişmesi V2 — pattern kanıtı kNN sorgusu, sonucu değiştirmeden hızlı

Tarih: 2026-10-06 · Taban: `8d37d05` (VPS'te çalışan `8db1faf` + etkisiz araştırma motoru commit'leri) · Dal:
`impl/knn-fast`.

Bu bir **karar-nötr performans onarımıdır**. `SimilarPatternEngine.query`'nin döndürdüğü sözlük — komşular, sıraları,
mesafeler, istatistik, kodlar — eski olay-başına döngüyle **bit bit aynıdır** (§4 kanıt, §6 testler). Strateji, eşik,
defter, config değeri, indeks içeriği, yayım kodu ve kuralı, önbellek anahtarı değişmez. Geri dönüş anahtarı:
`history.evidence_fast_knn: false` (§7).

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
  beklenmeyen bir istisna olursa o sorgu eski döngüyle yapılır (aynı sonuç ya da aynı istisna). Dizi kurulamazsa (ör.
  bellek) o motorda bir daha denenmez. Uyarı motor başına bir kez, yalnız motoru kuran süreçte:
  `pattern kNN hızlı yolu … ; eski döngü kullanılıyor (kanıt aynı, yalnız yavaş)`.

## 4. Kesinlik kanıtı

Gösterim: C = eski döngünün süzgecinden geçen adaylar; d(c) = eski kodun mesafesi (`_pair_dist`, float64);
κ(c) = (d(c), sembol, olay_ts, olay sırası). Eski `sort` (d, sembol, olay_ts) üzerinde **kararlıdır**, yani eşitlikte
olay listesi sırasını korur: eski sıralama tam olarak κ sırasıdır.

1. **Süzgeç aynı.** Hızlı yolun aday kümesi C'dir: aynı yüklemler, önceden çıkarılmış aynı değerler (`e.cutoff_ts`,
   aynı mum sütunundan `int(ts[exit_idx])` — `iloc` ile aynı değer; yalnız sayısal sütun, değilse eski döngü; dizge
   eşitliği kodlarla). Mesafesi hesaplanan her aday eski Python yüklemiyle (çıkış damgası dışında) yeniden denetlenir
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
* Mevcut karar-nötrlük testleri değişmeden geçer: `test_tour_perf_no_decision_change_v1.py` (5 gerçek tur, iki yayım,
  `state/` bayt bayt), `test_evidence_subprocess_v1.py`, `test_evidence_prewarm_v1.py`, `test_pattern_evidence_cache.py`,
  `test_patterns.py`. Karar kimliği özetini "alan eklenmeden önceki" haliyle karşılaştıran dört testte yeni anahtar da
  düşülür (`evidence_subprocess` ile aynı kalıp).

## 7. Geri dönüş anahtarı

```yaml
history:
  evidence_fast_knn: false   # varsayılan true; yalnız true/false (tırnaklı "false" config doğrulamasında reddedilir)
```

`false` → `SimilarPatternEngine.fast_query = False` (`_build_pattern_index`'te atanır): eski olay-başına döngü (aynı
`_pair_dist`/`_select` satırları), dizi kurulmaz. Anahtar iki değerinde de bit-aynı kanıt verdiği için karar kimliğine
(`config_hash`) girmez; etkin değer başlangıçta bir kez loglanır: `pattern kanıtı kNN sorgusu: HIZLI yol
(history.evidence_fast_knn=True; …)` ya da `… ESKİ olay-başına döngü (…)`. Worker yeniden başlatılınca geçerli.

## 8. VPS'te bakılacaklar ve riskler

1. Yayım sonrası `pattern kanıtı ön ısıtıldı: sürüm N, 13 sembol hesaplandı, … S sn` → S ~1.000 yerine birkaç saniye.
   `tur fazları` satırında `kanit` ~0; yeni sürüm yayımlandıktan sonraki tur olağan sürede (316–371 sn).
2. `pattern kNN hızlı yolu` ile başlayan uyarı YOK. Varsa kanıt yine aynıdır ama yol yavaştır (S yine büyük) → bildir.
3. Bellek: motor başına ~27 MB kalıcı ek dizi (pencere 64, iki yön); yayım anında eski ve yeni motor birlikte
   yaşarken ~54 MB. `MemoryPeak`'te belirgin artış beklenmez (alt süreç kapalıyken).

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
