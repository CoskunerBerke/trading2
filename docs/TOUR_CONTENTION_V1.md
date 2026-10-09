# Tur çekişmesi V1 — pattern kanıtı sorguları ayrı süreçte

Tarih: 2026-10-05 · Taban: `1c2c6e2` (VPS'te çalışan `f8b05fb` + sonraki araştırma/belge/test commit'leri) ·
Dal: `impl/tourfix` · Düzeltme turu 1: iki mercekli denetimin bulguları (§3.1 fork asılması, §6 OOM, §5 saat-duvarı).
Düzeltme turu 2: kapanış alt süreci beklemez (§3.3), alt süreç devralınan tanımlayıcıları tutmaz (§3.2), işaret dosyası
yedeğe girmez (§3.1), geri alma sinyalleri ve kalan karar riski (§5, §9).
Düzeltme turu 3: BELLEK (§6 baştan yazıldı, denetim bulgusu doğrulandı): fork anında canlı olan eski indeks ebeveynde
bırakılınca alt süreçte kalıyordu (≈ bir indeks boyutu) → alt süreç öldürülüp yeniden kurulur; eski sürümün alt süreci
yeni yayım ANINDA öldürülür; cgroup/sistem bellek payı koruması alt süreci `MemoryMax`'tan önce öldürür (§3.4). Sahibin
aktardığı VPS gerçekleri (4 vCPU, RAM, `MemoryMax`, `MemoryPeak`, `hwm`) belgeye işlendi; sürüm notu metni §9.1.

> **DURUM 2026-10-06 — alt süreç yolu VARSAYILAN KAPALI** (`history.evidence_subprocess` kod varsayılanı `false`;
> docs/TOUR_CONTENTION_V2.md §9). VPS'te (`8db1faf`, 2026-10-06) alt süreç worker cgroup'unda 0,5–1,5 GB özel bellek tuttu
> (cgroup tepesi 5.957/6.144 MB, %97) ve turları KISALTMADI: tur yeni indeks sürümünün kanıtını bekliyordu ve kanıtın
> kendisi sembol başına ~75 sn sürüyordu (iki yolda da). Sahip anahtarı 13:56 UTC'de kapattı; kök neden hızlı kNN sorgusuyla
> (`history.evidence_fast_knn`, V2) çözüldü. Bu belgedeki alt süreç yolu, anahtar açıkça `true` yapılırsa aynen çalışır
> (testler kilitli); config_hash'e yine girmez. Aşağıdaki "geri dönüş anahtarı `false`" ifadeleri artık varsayılanı anlatır.

Bu bir **karar-nötr performans onarımıdır**: strateji, eşik, defter, boyut ve config değeri değişmez. Yeni indeksin
yayımlandığı kod noktası ve kuralı, turun indeks sürümünü okuma kuralı, kanıt önbelleğinin anahtarı ve kanıtın kendisi
aynıdır; değişen tek şey kNN kanıt sorgularının **hangi süreçte** koştuğudur. Geri dönüş anahtarı:
`history.evidence_subprocess: false`. Saat-duvarı zamanlaması (turlar ve yayımlar ne zaman biter) değişir — amaç budur;
sahibin değerlendirmesi için §5'te açıkça yazılıdır.

Bu belgedeki sayılar yerel makinelerde ölçülmüştür (4 çekirdek, Python 3.12, ağ yok). VPS'te ölçülmüş değildir; VPS'te
neyin doğrulanacağı §9'dadır. §2'deki büyük yavaşlama oranları **boştaki** makineden gelir; aynı betik yüklü makinede
çok daha küçük oranlar verdi (§2.2). VPS'teki uzun turların bu mekanizmayla açıklanması bir **hipotezdir**. VPS'in
çekirdek sayısı artık biliniyor (sahibin aktardığı: 4 vCPU — yerel ölçüm makinesiyle aynı); yükü bilinmiyor.

---

## 1. Belirti (sahibin günlüğü, 2026-10-02..05, 141 tur)

Olağan tur 5,2–5,9 dk, aralık ~20,5 dk. Yeniden başlatma sonrası ilk tur dışındaki uzun turlar (>15 dk):

| Tur bitişi (TR) | Süre | Sürüm | Yayım (tur başladıktan sonra) |
|---|---:|---|---|
| 2026-10-03 07:55 | 2.653,7 sn | fc63481 | 07:16:28 |
| 2026-10-04 03:56 | 2.655,3 sn | fc63481 | 03:15:18 |
| 2026-10-05 07:54 | 2.596,4 sn | f8b05fb | 07:14:08 (sürüm 6) ve 07:33:49 (sürüm 7) |

Hepsi 4h kapanışından (00/04 UTC) ~11 dk sonra başladı ve yenileyici yeni indeksi turun 3–5. dakikasında yayımladı.
İki yayım da turların ARASINA düştüğünde turlar ~5,5 dk kaldı (3 günde 15 kapanışın ≈12'si).

2026-10-05 uzun turunun fazları: semboller 544,4 sn (olağan ~108), coin head 1.035,8 sn (içinde pattern kanıtı
626,3 sn; olağan ~0,1), yürütme 855,7 sn (olağan ~26). Box zamanlayıcısı bu turda bir 5m mumu kaçırdı (gecikme en çok
299,8 sn) — `--check`'te geri alma tetiği. `f8b05fb` yeniden başlatmasından sonraki ilk tur 3.763,7 sn sürdü
(semboller 1.173,9, coin head 1.269,8 — pattern kanıtı 0,1 —, yürütme 1.124,5).

## 2. Mekanizma — yerelde ölçüldü

### 2.1 Kim kimi aç bırakıyor

Ön ısıtma işçisi (`patterns/evidence_cache.py`, 2026-10-01) yayımdan hemen sonra worker sürecinin İÇİNDE, indeks
sembolleri × LONG/SHORT kNN sorgusu koşar (VPS: 138.891 olayda sorgu başına 12,6 sn; 16 sembol × 2 yön ≈ 400 sn CPU,
4h kapanışı başına iki yayım). Sorgu döngüsü (`patterns/engine.py` `query`) indeksteki HER olay için `np.corrcoef`
çağırır; bu çağrı GIL'i bırakıp hemen geri alır — olay başına bir kez, yani milisaniyede onlarca kez.

CPython'da GIL bekleyen iş parçacığı, GIL 5 ms (`sys.getswitchinterval()`) boyunca HİÇ el değiştirmezse "bırak"
ister. Sık bırak-al yapan bir iş parçacığı her bırakışta bekleyeni uyandırır; bekleyen uyanıp kilidi alamadan sorgu
iş parçacığı onu geri alır ve bekleyenin 5 ms sayacı baştan başlar. Bekleyen GIL'i ancak yarışı şans eseri kazanınca
alır. Ölçüm (boştaki 4 çekirdekli makine) — 1 ms uyuyan bir iş parçacığının GIL'i geri alma gecikmesi, arka planda tek
bir işlem döngüdeyken:

| Arka plandaki işlem | p50 | p90 | en çok |
|---|---:|---:|---:|
| yok | 0,1 ms | — | 1,7 ms |
| saf Python döngüsü | 5,3 ms | 5,4 ms | 10,4 ms |
| pandas `iloc` | 5,3 ms | 5,4 ms | 11,0 ms |
| `np.corrcoef` (16 noktalı yol) | 3,3 ms | 24,3 ms | 106,3 ms |
| **`SimilarPatternEngine.query`** (7,5k olay) | **36,5 ms** | **143,1 ms** | **369,9 ms** |

Saf Python yük beklenen 5 ms'yi verir; aç bırakmayı sorgunun sık GIL bırakışı yapar. **Yük altında farklı:** denetimde
aynı türden makine başka oturumlarla yüklüyken (yük ortalaması 6–7) doğrudan ölçümde sorgu iş parçacığının yanında
gecikme p50 0,34 ms / p90 3,3 ms çıktı (saf Python 5,26 ms) — yani açlığın şiddeti çekirdek sayısına ve yüke güçlü
biçimde bağlıdır.

### 2.2 Turun işleri ne kadar yavaşlıyor

Sentetik tur (ana iş parçacığı), gerçek `EvidenceCache` ve gerçek `SimilarPatternEngine` (21.936 olay) ile; ön ısıtma
ölçüm boyunca sürüyor. İşler: `py` saf Python, `numpy` `build_feature_frame` (ajan göstergelerine vekil), `net` ayrı
bir süreçten 16 KB'lık parçalarla 100 KB JSON okuma + çözme (HTTP vekili), `file` atomik JSON yazımı. Periyodik iş
parçacığı Box zamanlayıcısı vekilidir (0,5 sn'de bir küçük pandas işi). **Boştaki** 4 çekirdekli makine:

| Durum | py | numpy | net | file | 1 ms uyku gecikmesi p50 / p90 / en çok | periyodik iş en uzun |
|---|---:|---:|---:|---:|---|---:|
| ön ısıtma yok | 1,0× | 1,0× | 1,0× | 1,0× | 0,7 / 2,8 / 109 ms | 1,6 ms |
| **bugün: süreç içi ön ısıtma** | **9,5×** | **94×** | **33×** | **145×** | **39 / 166 / 604 ms** | **882 ms** |
| arka planda indeks kurulumu (iş parçacığı) | 6,7× | 11,7× | 8,8× | 18× | — | — |
| **onarım: alt süreçte ön ısıtma** | **1,09×** | **0,77×** | **0,78×** | **1,11×** | **0,7 / 2,2 / 16 ms** | **2,1 ms** |

(0,8× değerleri gürültüdür: alt süreç başka çekirdekte koşar.)

**Aynı betik, YÜKLÜ makine** (denetim; yük ortalaması 6–7, üç koşu): süreç içi ön ısıtma 1,0–2,7×, 1 ms uyku gecikmesi
p50 0,29–0,32 ms; alt süreç yolu 0,58–1,34× (bir koşuda `net` 3,27× — yük gürültüsü). **Orta yük** (bu düzeltme turu;
yük ortalaması 2–3,5, `--bars 800`, 10.736 olay): süreç içi 8,6× / 42× / 55× / 9,2× (py / numpy / net / file), uyku
gecikmesi p90 67 ms / en çok 379 ms; alt süreç 1,08× / 0,73× / 0,93× / 1,37×, p90 0,9 ms. Yani yukarıdaki 33–145× boştaki
ya da az yüklü makineye özgüdür ve ağır yükte yeniden üretilmez; her koşuda yeniden üretilen sonuç **alt süreç yolunun
~1×** olmasıdır (ön ısıtmanın çekişme payı ortadan kalkar). Bu payın VPS'teki 5× (semboller) ve 33×'ü (yürütme) açıklayıp
açıklamadığı VPS'in yüküne bağlıdır (çekirdek sayısı 4 vCPU, sahibin aktardığı; yükü bilinmiyor) → dağıtım sonrası §9
ölçüt 1 ve 5 ile doğrulanacak bir hipotez.

Tekrar çalıştırmak için ağsız betik: `python scripts/bench_tour_contention.py --modes none,inproc,child` (sentetik veri,
geçici dizin; worker'ın yanında koşturmayın, CPU yarıştırır). Aynı mekanizmanın hızlı bir sürümü
`tests/test_evidence_subprocess_v1.py` `test_synthetic_tour_and_periodic_thread_are_not_slowed_by_the_child_prewarm`
içinde CI'da koşar (yalnız alt süreç yolunu sınar: eşik 3× / periyodik gecikme p90 25 ms).

### 2.3 VPS günlüğüyle tutarlılık (hipotez)

- **semboller** (ağ + ajan göstergeleri: GIL'i sık bırakan iş) 108 → 544 sn (5×); **yürütme** (defter/dosya yazımı,
  numpy) 26 → 856 sn (33×). Boştaki yerel makinede aynı türden işler 33–145×, yüklü makinede 1–3× yavaşladı; VPS'te
  fazın ancak bir kısmı ön ısıtmayla çakıştı (sürüm 6 ön ısıtması 07:14'te, sürüm 7'ninki 07:33'te başladı).
- **pattern kanıtı 626 sn**: tur, ön ısıtmanın henüz yetişmediği sembolleri süreç içinde kendisi hesapladı; iki sorgu
  iş parçacığı `compute_lock`'ta sırayla koşup birbirini ve geri kalan her şeyi aç bıraktı. 07:33:49'daki ikinci yayım
  coin head sürerken sürümü değiştirdi; kalan semboller yeni sürüm için yeniden hesaplandı.
- **Box kaçan mum**: Box zamanlayıcısı aynı süreçte bir iş parçacığıdır; değerlendirmesi ağ + pandas işidir ve GIL'i her
  geri alışta onlarca–yüzlerce ms bekledi (boştaki yerel makinede periyodik iş en uzun 882 ms, uyanma gecikmesi en çok
  604 ms).
- **Açıklanmayan:** yeniden başlatma sonrası ilk tur (3.763,7 sn, pattern kanıtı yalnız 0,1 sn) ön ısıtmayla
  açıklanamaz; o turda süreç içi ilk indeks kurulumu çakışıyordu (§2.4, §9.7).

### 2.4 Ne DEĞİLDİ / ne kaldı

- **Kilitler.** `_entry_lock`'u yalnız tur alır (`_execute`, tek `with`); yenileyici ve ön ısıtma ne `_entry_lock`'a ne
  `_ledger_lock`'a dokunur. Yürütmenin 856 sn'si arka plan işinin arkasında kilit beklemesi olamaz. Paylaşılan tek
  kilit `compute_lock`'tur ve yalnız pattern kanıtı alt fazını etkiler (tasarım gereği: indekste aynı anda tek sorgu).
- **Rate-limit bütçesi.** Her `_futures_provider_factory()` çağrısı kendi `BudgetPool`'unu kurar; yenileyicinin arşiv
  güncellemesi döngü başına en çok `refresh_max_requests: 24` istek atar (dakikalık 1.680 ağırlık bütçesinin küçük bir
  kısmı). Dakikalarca bekleme üretmez.
- **İndeks kurulumu (KALDI).** Kurulum da GIL'i paylaşır (yukarıdaki tabloda 6,7–18×). Yayım başına süresi VPS'te
  **ölçülmedi** (eldeki "~40 sn" kodun kendi notudur). Bu düzeltmeden sonra yayım satırı süreyi yazar
  (`pattern indeksi yenilendi: sürüm N, … olay, … seri, kurulum S sn`); sahip kurulumun turlar ve Box zamanlayıcısıyla
  ne kadar çakıştığını oradan ölçebilir. Kurulum §4'teki nedenle bilinçli olarak süreçte bırakıldı; Box zamanlayıcısı
  kurulumun çekişmesinden KORUNMAZ, yalnız ön ısıtmanınkinden.

## 3. Tasarım

`tradingbot/patterns/evidence_child.py` (yeni) + `evidence_cache.py` + `engine_v3.py` (iki çağrı noktası + başlangıç
satırı) + `refresher.py` (yalnız yayım satırına kurulum süresi) + `config_v3.py` (anahtar ve doğrulaması).

1. Yenileyici yeni indeksi **bugünkü noktada, bugünkü iş parçacığında** yayımlar ve ön ısıtmayı kuyruğa koyar (değişmedi).
2. Ön ısıtma işçisi, işin ilk gerçekten hesaplanacak sembolünde yayımlanmış paketin motoruyla `fork` eder
   (`EvidenceChild.start`). Alt süreç indeksi yazınca-kopyala sayfalardan görür: kopyalama, pickle, yeniden kurulum yok.
3. İşçi her sembol için alt sürece `("q", sembol)` gönderir ve cevabı boruda bekler (beklerken GIL tutmaz). Cevap
   LONG+SHORT kanıtıdır ve bugünkü gibi `(sembol, sürüm, son bar)` anahtarıyla, tek birim olarak önbelleğe konur.
4. Alt süreç yaşarken turun önbellek ıskası da ona gider (`EvidenceCache.compute`, `compute_lock` altında): tur boruda
   bekler, GIL Box zamanlayıcısına ve koruyucu izleyiciye kalır. Alt süreç yalnız kendi motorunun sorgusunu yapar
   (kimlik denetimi); başka sürümü okuyan tur bugünkü gibi süreç içi hesaplar.
5. İş bitince alt süreç kapatılır. Daha yeni yayımda eski sürümün alt süreci yayım ANINDA öldürülür (düzeltme turu 3;
   önceden sembol sınırında kapanıyordu); uçuştaki tur isteği o sembolü süreç içi hesaplar (aynı kanıt), ön ısıtma eski
   işi bırakır.
6. Bellek (düzeltme turu 3, §3.4 ve §6): fork anında canlı olan eski motorlara ölüm kancası; bellek payı koruması.

Neden `fork` (spawn/forkserver değil): yeni bir süreç indeksi ya arşivden yeniden kurmalı (40+ sn CPU ve yayımlananla
aynı olduğunun garantisi yok) ya da ebeveynden pickle ile almalıdır (`PatternEvent` başına ayrı Python nesneleri:
yüzlerce MB; ebeveynde GIL'i saniyelerce tutan tek bir C çağrısı ve iş boyunca tam ikinci kopya). `fork` aynı bellek
görüntüsünü bedelsiz verir; kanıtın bit-aynı olmasının gerekçesi de budur: aynı motor nesnesi, aynı fonksiyon
(`TradingEngineV3._evidence_query`), aynı girdiler.

### 3.1 Fork'un KENDİSİ asılabilir (denetim bulgusu; yeniden üretildi)

CPython `fork()`'u GIL'i tutarak çağırır; libc önce yüklü kütüphanelerin fork öncesi kancalarını koşturur. numpy'nin
OpenBLAS'ı (çok çekirdekli makinede iş parçacığı havuzuyla) kancasında havuzu kapatıp `pthread_join` ile bekler. O anda
BAŞKA bir iş parçacığı çok iş parçacıklı bir BLAS işi yürütüyorsa havuz iş parçacığı kapatma işaretini kaçırır ve join
SONSUZA DEK bekler. GIL fork eden iş parçacığında kaldığı için bütün Python iş parçacıkları (tur, Box zamanlayıcısı,
koruyucu izleyici) donar; hiçbir Python zaman aşımı (`START_TIMEOUT_S` vb.) çalışamaz, çünkü `proc.start()` hiç dönmez.

Yeniden üretim: arka planda 300×300 matris çarpımı koşan bir iş parçacığı varken art arda `EvidenceChild.start` —
denetimde 14 koşunun 6'sı, bu düzeltme turunda 6 koşunun 6'sı (60'ar fork) asıldı; deadman'li denemede asılma ~3 sn
içinde geldi (gdb: `__run_prefork_handlers → blas_thread_shutdown_ → pthread_join`). Tetik başka bir iş parçacığında
çok iş parçacıklı seviye-3 BLAS ister. Worker
kodunda bugün yalnız vektör / matris-vektör çarpımı (`learn/model.py`, `learn/retrieval.py`, `agents/analog.py`), küçük
`corrcoef`/`cov` ve tamsayı çarpımı (`shared_experience/advisor_eval.py`, BLAS'a gitmez) var; denetimde matris-vektör
yüküyle (100.000×40'a kadar) 2.700 fork'ta asılma görülmedi. Yani risk bugün **gizli ama sıfır değil** ve gerçekleşirse
felaket olurdu. Önlemler:

- **Deadman** (`EvidenceChild.FORK_DEADMAN_S = 30`): yalnız bu modülün fork'u süresince süreç zamanlayıcısı
  (ITIMER_REAL) kurulur — `os.register_at_fork` kancası, iş parçacığına özel bayrakla (başka kodun fork'una dokunmaz) —
  ve fork dönünce söndürülür. Fork asılırsa SIGALRM'nin **varsayılan eylemi** worker'ı sonlandırır (bunu çekirdek yapar,
  GIL gerekmez); `Restart=on-failure` onu yeniden başlatır. Günlükte: `Main process exited, code=killed,
  status=14/ALRM`. Sonuç: süresiz donma yerine bir yeniden başlatma (o tur uzun sürer, Box mumu kaçabilir — `--check`
  bunu görür). SIGALRM başka bir amaçla kullanılıyorsa (işleyici kurulu, engelli ya da zamanlayıcı kurulu; worker'da
  bugün hiçbiri yok) fork **hiç yapılmaz**: bugünkü süreç içi yol. Olağan fork 8–16 ms; 30 sn, bellek baskısında yavaş
  bir fork'u öldürmemek için geniş pay. Gerçek OpenBLAS asılmasıyla sınandı: süreç ~3 sn'de `status=14` ile bitti
  (`test_with_a_real_multithreaded_blas_job_in_flight_a_fork_never_freezes_the_worker`).
- **İşaret dosyası** (`state/pattern_evidence_fork.marker`): fork'tan hemen önce yazılır (pid + çekirdeğin süreç
  başlangıç zamanı), fork dönünce silinir. Deadman worker'ı sonlandırırsa dosya kalır; yeniden başlayan worker başka bir
  sürecin kaydını görünce alt süreci **o makinede kapalı tutar** (her yayımda yeniden asılma/yeniden başlatma döngüsü
  olmaz), başlangıçta ve ilk yayımda birer uyarı yazar ve süreç içi yolla devam eder. Sahip dosyayı silince bir sonraki
  yayımda yeniden denenir (yeniden başlatma gerekmez). Worker fork'un ~10 ms'lik penceresinde başka bir nedenle
  öldürülürse (OOM, SIGKILL) dosya yanlışlıkla kalabilir: sonuç yalnız bugünkü süreç içi yoldur. Dosya **makine
  durumudur, veri değil**: yedeğe alınmaz (`ops/backup.py` `MACHINE_MARKERS`; eski bir yedekten dönüp alt süreci sessizce
  kapalı tutmaz) ve geri yüklemede makinedeki kopya yeni state'e taşınır (geri yükleme asılmayı yeniden denetmez).
- **Değişmez + izin listesi testi:** worker'da hiçbir iş parçacığı çok iş parçacıklı seviye-3 BLAS (matris-matris
  çarpımı, `np.linalg` ayrıştırması) koşmaz. `tests/test_evidence_subprocess_v1.py`
  `test_no_new_blas_call_site_appears_in_runtime_code_without_reviewing_the_fork_hazard`, `tradingbot/` altındaki
  BLAS'a gidebilen her çağrıyı (AST) gerekçeli bir izin listesiyle karşılaştırır; yeni bir çağrı bu riski
  değerlendirmeden eklenemez. **Sınırı:** tarama yalnız `tradingbot/` kodunu görür; üçüncü taraf kütüphanelerin iç
  çağrıları (pandas, matplotlib, scipy) taranmaz. Onların bir iş parçacığında çok iş parçacıklı seviye-3 BLAS koşturup
  koşturmadığı bilinmiyor; bilinen tek kanıt matris-vektör yüküyle 2.700 fork'ta asılma görülmemesidir. Bunlar için
  güvence yalnız deadman'dir (§5 "Kalan karar riski").
- **Teşhis:** yenileyici başlarken bir satır, OpenBLAS'ın iş parçacığı sayısını yazar (`pattern kanıtı sorguları: ALT
  SÜREÇTE (…; OpenBLAS iş parçacığı N; fork koruması 30 sn)`). N = 1 → havuz yok, bu risk yok.

### 3.2 Fork'tan sonraki önlemler

- Alt süreç yalnız saf hesap yapar: loglamaz, uyarı basmaz, stdout/stderr `/dev/null`'a yönlenir — `fork` anında başka
  bir iş parçacığının tuttuğu akış/log kilidine dokunmaz.
- Hazır el sıkışması 30 sn (`START_TIMEOUT_S`). İstek sürerken alt süreç 60 sn **hiç CPU harcamazsa**
  (`STALL_TIMEOUT_S`; kilitlenen süreç CPU harcamaz, CPU kotasıyla kısılmış ama çalışan süreç harcar) öldürülür; mutlak
  üst sınır 1.800 sn (`REQUEST_TIMEOUT_S`). Ölüm boru kapanışıyla anında görülür.
- Alt süreç devraldığı **ebeveyn** boru ucunu ilk iş olarak kapatır: ebeveyn ölünce boru EOF verir ve alt süreç çıkar
  (denetim: bu yapılmadan, PDEATHSIG olmadan alt süreç ebeveynin SIGKILL'inden 3 sn sonra hâlâ yaşıyordu). Linux'ta
  ayrıca `PR_SET_PDEATHSIG=SIGKILL`. İkisi birlikte, devralınan tekil kilit tanımlayıcısıyla ortada kalıp yeniden
  başlatılan worker'ı bekletmesini önler (test: `test_the_child_exits_on_parent_death_even_without_pdeathsig`).
- Alt süreç **SIGTERM'i yok sayar**: birim `KillMode=control-group` olsa bile (depodaki birim `mixed`) systemd'nin
  SIGTERM'i alt süreci öldürüp worker'ın son turunu süreç içi yola düşürmez; kapatmayı ebeveyn yapar (SIGKILL,
  PDEATHSIG ve boru EOF'u yine geçerli). Bunun kapanışa etkisi ve önlemi §3.3'tedir.
- **Devralınan tanımlayıcılar** (düzeltme turu 2): `fork` ebeveynin bütün açık tanımlayıcılarını kopyalar (`O_CLOEXEC`
  exec olmadan işlemez): HTTP keep-alive soketleri (ebeveyn kapatınca alt süreç çıkana dek FIN gitmez), tekil kilit
  dosyası ve o an bir `subprocess` çağrısının oluşturma penceresindeki boru uçları — ör. turun kasa `git` senkronunda
  (`engine._git_sync`, `add`/`commit`/`pull` zaman aşımsız) `git`'in stdout yazma ucu alt süreçte kalırsa ebeveynin
  `communicate`'i alt süreç çıkana dek (dakikalar) EOF görmezdi. Alt süreç ilk iş olarak 0–2 ve kendi borusu dışındaki
  her tanımlayıcıyı `/dev/null`'un bir kopyasıyla değiştirir (`dup2`: numara dolu kalır, devralınan bir nesnenin sonradan
  yanlış dosyayı kapatması olmaz). Sorgu hiçbir tanımlayıcı kullanmaz (salt bellek). Bu `multiprocessing`'in bekçi
  borusunu da kapattığı için ebeveyn alt sürecin bitişini `waitpid` yoklamasıyla bekler (`join(timeout)` bekçi erken EOF
  verince süresiz beklerdi). Testler: `test_the_child_keeps_no_inherited_descriptor_of_the_parent`,
  `test_close_is_bounded_although_the_child_dropped_the_multiprocessing_sentinel`.
- `nice +10` (worker `Nice=5` → 15) ve `oom_score_adj=1000` (cgroup OOM'unda çekirdek önce alt süreci seçer; birimin ne
  yapacağı için §6'ya bakın).
- Fork'tan sonraki her arıza (başlamama, ölüm, asılma, protokol) loglanır ve o işin kalanı bugünkü süreç içi yolla
  hesaplanır; tur o sembolü bugünkü gibi kendisi hesaplar. Alt süreçte bir sembolün hesabı istisna verirse tur o
  sembolü süreç içi yeniden hesaplar (bugünkü istisna ya da sonuç birebir); ön ısıtma yalnız loglar (bugünkü gibi).
- **Yalnız Linux:** başka platformda alt süreç hiç denenmez (uyarısız bugünkü yol). Üretim Linux'tur.

### 3.3 Kapanış alt süreci beklemez (düzeltme turu 2, denetim bulgusu)

Alt süreç SIGTERM'i yok saydığı için (§3.2) ilk sürümde süreç çıkışı **bütün ön ısıtma işini** bekliyordu:
`multiprocessing`'in `atexit` kancası (`util._exit_function`) daemon alt sürece SIGTERM yollar ve onu **zaman aşımsız**
`join` eder; ebeveynin ön ısıtma iş parçacığı çıkış sırasında da sorgu yollamayı sürdürdüğü için alt süreç EOF görmez.
`watch`'ın kapanışı da önbelleği durdurmuyordu. Denetimin yeniden üretimi: 10 sembol × 2 sn'lik işte çıkış 21,2 sn
(süreç içi yol 2,3 sn); `watch` tarzı SIGTERM bayrağıyla 10 sn/sembolde 61 sn sonra hâlâ yaşıyordu. VPS'te (sembol başına
~25 sn, yayım başına 7–14 dk iş) iş sürerken her `systemctl stop/restart` `TimeoutStopSec=90`'ı aşıp SIGKILL ile biterdi
(sonuç `timeout`; birim büyük olasılıkla `failed` olur, `OnFailure` uyarısı tetiklenir) — bir sonraki sürümün dağıtımı ve
bu sürümün geri alınması dahil.

Önlem, iki katman:

1. `watch` kapanışı izleyiciyi durdurduktan sonra, kilit ve instance kaydı bırakılmadan önce
   `TradingEngineV3.stop_pattern_evidence()` → `EvidenceCache.stop(2.0)` çağırır: canlı alt süreç SIGKILL ile öldürülür,
   işçi en çok 2 sn beklenir (süreç içi hesaplayan işçi beklenmez; daemon'dur). `stop()` **kalıcıdır**: kapanış sırasında
   gelen bir yayım işçiyi yeniden başlatıp fork etmez.
2. Her çıkış yolu için (ana iş parçacığının dönmesi, yakalanmamış istisna, tek tur `tour` komutu): ilk fork'ta,
   `multiprocessing.util` içe aktarıldıktan **sonra** bir `atexit` kancası kaydedilir. `atexit` ters sırada koştuğu için
   kanca `multiprocessing`'inkinden **önce** koşar: yeni fork'u kapatır (`exiting()`), canlı alt süreçleri SIGKILL ile
   öldürür ve en çok 2 sn biçer. Öldürülen alt süreç arıza sayılmaz (uyarı yok) ve iş süreç içi yola düşmez.

Ölçüm (yerel, denetimin betikleri): ana iş parçacığı dönünce çıkış ~0,45 sn (önce: 5 sembol × 2 sn'lik işin sonuna dek,
toplam 10,5 sn), `watch` tarzı SIGTERM'den çıkışa 0,2 sn (önce: 61 sn sonra hâlâ canlı); alt süreç ebeveynle birlikte
biter. Testler: `test_process_exit_never_waits_for_the_prewarm_job_and_leaves_no_child`
(ana iş parçacığı döner / SIGTERM / `stop()`, iş 200 sn, eşik 10 sn — eski kodda 120 sn zaman aşımına düşer),
`test_the_exit_hook_kills_live_children_and_refuses_new_forks`,
`test_a_stop_that_lands_inside_the_fork_window_closes_the_new_child_and_computes_nothing` (fork sırasında gelen `stop()`:
yeni alt süreç kapatılır, sembol süreç içi hesaplanmaz),
`test_watch_shutdown_stops_the_pattern_evidence_before_releasing_the_lock`. Geri dönüş anahtarı kapalıyken (alt süreç
yok) kanca hiç kaydedilmez; `watch` kapanışındaki `stop()` süreç içi bir sembol hesaplanıyorsa en çok 2 sn bekler.

### 3.4 Bellek önlemleri (düzeltme turu 3, denetim bulgusu)

Ayrıntı ve ölçüm §6'da. Üç önlem, hepsi `evidence_cache.py` + `evidence_child.py` içinde:

1. **Eski indeksin ölüm kancası + yeniden fork.** Ebeveynin gördüğü her motor zayıf referansla bilinir
   (`EvidenceCache.track_engine`: yayım kancası `note_published`, `request_prewarm` ve turun ıskası). Fork anında hâlâ
   canlı olan eski motorlara ölüm kancası kurulur (`EvidenceChild.start(watch=…)`). Eski motor ebeveynde serbest
   kalırken — kanca motorun içeriği bırakılmadan ÖNCE koşar — alt süreç öldürülür (`RETIRE_RETAINED`); bir sonraki
   istekte (`_child_for`, `compute_lock` altında, hangi iş parçacığı isterse) aynı motor için yeniden fork edilir ve
   uçuştaki sembol yeni alt süreçte yeniden istenir. Yeni alt süreç eski indeksi içermez (o anda ebeveynde yoktur). Eski
   motor tam el sıkışması sırasında ölürse fork bir kez daha yapılır. İş başına en çok 4 yeniden fork; sonra işin kalanı
   süreç içi (uyarı). Bilgi satırı: `pattern kanıtı: alt süreç yeniden kuruldu — …`; ön ısıtma satırında `; eski indeks
   için yeniden fork R` eki.
2. **Eski sürümün alt süreci yayım ANINDA ölür** (`note_published` / `request_prewarm` → `_retire_stale_child`,
   yenileyici iş parçacığında, kilitsiz — `stop()` gibi yalnız öldürür). Bilgi satırı: `pattern kanıtı: eski sürümün
   alt süreci (sürüm N) daha yeni yayımla hemen kapatıldı (sürüm M)`. Bedeli: o an alt süreçte bekleyen bir tur isteği
   varsa tur o sembolü süreç içi hesaplar (bugünkü yol: aynı kanıt, VPS'te ~25 sn ve o süre için bugünkü GIL
   çekişmesi); ön ısıtmanın uçuştaki eski sembolü atılır (eski sürüm zaten bırakılıyordu).
3. **Bellek payı koruması.** Pay = en dar olan: cgroup zincirindeki her sınır için `sınır − (kullanım − dosya
   önbelleği)` (v2: `memory.max`, `memory.current`, `memory.stat` `active_file + inactive_file`; v1 karşılıkları) ve
   sistemin `MemAvailable`'ı (takas yok). Dosya önbelleği çıkarılır: çekirdek onu OOM'dan önce geri alır (saatlik
   yedek gibi büyük dosya işleri sayacı şişirir ama OOM getirmez). Fork yalnız pay ≥ 1.536 MB iken yapılır
   (`MEMORY_FORK_HEADROOM_MB`; değilse uyarı `pattern kanıtı alt süreci başlatılmadı — bellek payı yetersiz (…)` ve iş
   süreç içi). Alt süreç yaşarken pay her istekten önce ve istek beklenirken saniyede bir okunur; 512 MB'ın
   (`MEMORY_KILL_HEADROOM_MB`) altına inerse alt süreç öldürülür (`RETIRE_MEMORY`, uyarı `pattern kanıtı alt süreci
   bellek koruması nedeniyle kapatıldı (…)`) ve işin kalanı süreç içi sürer.

Bilinçli öldürmelerin hiçbiri arıza sayılmaz (arıza sayacı ve `arızalandı` uyarısı yok); bilgi satırları
`pattern kanıtı alt süre…` önekini KULLANMAZ (o önek §9 ölçüt 3'te sorun satırlarınındır), bellek koruması uyarıları
kullanır. Kanıt her yolda aynı fonksiyon + aynı motorla hesaplanır. Testler (`tests/test_evidence_subprocess_v1.py`
bölüm 14): `test_a_newer_publish_kills_the_old_child_at_once_not_at_the_symbol_boundary`,
`test_a_tour_in_flight_in_the_stale_child_recomputes_in_process_with_the_same_result`,
`test_an_old_index_dropped_after_the_fork_kills_and_reforks_the_child_same_evidence`,
`test_a_tour_holding_the_old_index_while_the_new_child_forks_triggers_one_refork` (motor düzeyinde gerçek akış),
`test_the_child_does_not_keep_a_dropped_old_index_resident` (ölçüm; kancasız kontrol kolu eski indeksin alt süreçte
kaldığını gösterir), `test_the_memory_guard_kills_the_child_and_the_job_finishes_in_process_same_evidence`,
`test_no_fork_when_the_memory_headroom_is_short_same_evidence`,
`test_the_headroom_reader_handles_cgroup_v2_and_v1_and_excludes_page_cache`,
`test_a_retention_kill_during_the_fork_handshake_reforks_once_and_is_not_a_failure`.

## 4. Neden indeks kurulumu süreçte kaldı

Kurulumu alt sürece taşımak motoru ebeveyne geri taşımayı gerektirir (≈150k olay pickle'ı: ebeveynde saniyelerce GIL,
geçici yüzlerce MB ve iş boyunca ikinci tam kopya) ve yayım NOKTASINI kaydırırdı. Yayım noktası, iş parçacığı ve
mantığı **değişmedi** (yayım satırına yalnız süre eklendi). Kurulumun çekişmesi ön ısıtmanınkinin bir kısmıdır ve VPS'te
henüz ölçülmedi (§2.4); kalan risk §9'da.

## 5. Karar-nötrlük

| Değişmez | Nasıl korunuyor | Test |
|---|---|---|
| Yayım noktası ve kuralı | yenileyicinin yayım mantığı ve iş parçacığı değişmedi (yalnız log satırına süre); `fork` ön ısıtma işçisinde, yenileyici iş parçacığında değil; yavaş `fork` yayımı geciktirmez. **Saat-duvarı yayım ZAMANI yüke bağlıdır ve değişebilir** (aşağıda) | `test_the_publish_moment_and_version_visibility_are_unchanged` |
| Turun gördüğü sürüm (okuma kuralı) | motor ve sürüm tek paket okumasından (değişmedi); alt süreç yalnız kendi motorunun sorgusunu yapar | `test_a_newer_publish_closes_the_old_child_and_never_serves_the_old_version` |
| Anahtar | `(sembol, sürüm, son bar)` değişmedi | aynı |
| Kanıt değeri | aynı motor/fonksiyon/girdi; pickle float'ı kayıpsız taşır; serileştirilmiş `==` ve nesne `==` | `test_child_evidence_is_bit_identical_to_in_process_and_files_match` |
| Karar kimliği (`config_hash`, karar günlüğü/provenans satırlarına yazılır) | yeni anahtar iki değerinde de karar-nötr → özetten çıkarılır (ortak deneyim bölümüyle aynı kural); özet bu alandan önceki kodla aynı. Görünürlük için etkin değer başlangıçta bir kez loglanır; tırnaklı `"false"` gibi bool olmayan değer config doğrulamasında reddedilir | `test_the_switch_is_not_part_of_the_decision_identity`, `test_the_switch_is_linux_only_bool_only_and_logged_at_startup`, `test_learning_record_only_tours.py` (`943345c` ile bayt bayt) |
| Bütün durum dosyaları | gerçek `tour()` × 5, iki yayım; alt süreç AÇIK (ön ısıtma bitmiş / tur başlarken sürüyor / **ön ısıtma hiç hesaplamıyor, turun bütün ıskaları alt süreçte**), süreç içi, ön ısıtma KAPALI → `state/` bayt bayt aynı | `test_tour_perf_no_decision_change_v1.py` (4 karşılaştırma) |
| Bellek önlemleri (düzeltme turu 3) | eski alt süreci yayımda öldürmek, eski indeks bırakılınca yeniden fork, bellek koruması: yalnız kanıtın HANGİ süreçte hesaplandığını seçer; tur o sembolü süreç içi (aynı fonksiyon + motor) ya da yeni alt süreçte hesaplar; yayım noktası, sürüm okuma kuralı, anahtar değişmez | §3.4'teki testler (her birinde kanıt süreç içi hesapla bayt bayt karşılaştırılır) |

**Kalan karar riski — deadman yeniden başlatması.** Alt süreç varsayılan AÇIK'tır ve kararları değiştirebileceği
TEK yol §3.1'deki deadman olayıdır: fork asılırsa worker sonlandırılır ve yeniden başlar. Bugün worker hiç fork etmediği
için bu olay bugün **olamaz**. Yeniden başlatma boşluk mutabakatı ve uzun bir ilk tur getirir (Box bir 5m mumu kaçırabilir);
yani o saatlerde hangi turun ne zaman koştuğu değişir. Olasılığı iki şeye dayanır: izin listesi testi (yalnız
`tradingbot/` kodunu tarar; pandas/matplotlib iç çağrıları görünmez) ve matris-vektör yüküyle 2.700 fork'ta asılma
görülmemesi (ölçüm, kanıt değil). Olursa işaret dosyası alt süreci o makinede kapatır (ikinci olay olmaz). Riski tamamen
kaldıran `OPENBLAS_NUM_THREADS=1` ayrı bir karar-nötrlük kanıtı ister (§10). Sahip için geri alma sinyalleri: §9.

**Saat-duvarı zamanı — sahibin değerlendirmesine.** Kod ve kurallar aynı; zamanlama aynı değil ve olamaz:

- Turlar coin head'e ve sona daha erken ulaşır; Box zamanlayıcısının daha az mum kaçırması beklenir. Bunlar bugüne göre
  **amaçlanan davranış değişiklikleridir** (karar mantığı değil, zamanlama).
- 4h kapanışından sonraki **ikinci yayım** (ör. sürüm 7, 07:33:49) bugün süreç içi sürüm 6 ön ısıtmasıyla GIL için
  yarışan bir kurulumun sonunda geliyordu. Yenileyicinin bekleme aralığı kurulum bittikten sonra başladığı için, ön
  ısıtma alt süreçteyken kurulum daha hızlı bitip ikinci yayım saat olarak **daha erken** gelebilir.
- Bu ikisi birlikte, belli bir turun bir yayımdan önce mi sonra mı okuduğunu — yani saat-duvarı açısından hangi turun
  hangi sürümü gördüğünü — değiştirebilir. Bugün de aynı şey makinenin yüküne göre değişir (aynı kod, farklı gün,
  farklı zamanlama); hiçbir yayım bilerek ertelenmez ya da öne alınmaz ve okuma kuralı aynıdır. Bunun "karar
  değişikliği yok" kuralına uygun olup olmadığına sahip karar verir; uygun görmezse `history.evidence_subprocess: false`
  bugünkü zamanlamaya döner.

## 6. Bellek (düzeltme turu 3'te baştan yazıldı)

Bellek VPS'te bağlayıcı kısıttır. Bu bölümün önceki sürümü alt sürecin maliyetini yalnız yazınca-kopyala payıyla
(≈0,35 GB) veriyordu. Denetim, yayım bir turun ortasına düşünce alt sürecin ESKİ indeksi de taşıdığını ölçtü (≈ bir
indeks boyutu daha, işin sonuna dek). Bulgu burada yeniden üretildi (§6.4), önlendi (§3.4, §6.3) ve sayılar yeniden
ölçüldü.

### 6.1 VPS gerçekleri (sahibin aktardığı, 2026-10-05; salt okunur)

| | |
|---|---|
| CPU / RAM | 4 vCPU; 7.751 MB RAM, takas YOK (sayfa önbelleğiyle ~4,4 GB kullanılabilir) |
| Worker cgroup | `MemoryMax` 6 GiB (6.442 MB); `MemoryPeak` 5,45 GB — sayfa önbelleği DAHİL cgroup tepesi |
| Worker süreci | `health.json` `hwm_mb` ~3,5 GB RSS (~9 sa sonra; eski + yeni indeksin birlikte durduğu kurulumlar dahil) |
| Diğer | panel ~90 MB; saatlik yedek geçici olarak birkaç GB DİSK ister; worker birimine varsayılan `OOMPolicy=stop` uygulanır |
| İndeks | 89 futures 4h serisinden yeniden kuruluyor; olay sayısı yayım satırında (`… olay, … seri`) |

Önceki sürümdeki "bugünkü tepe ~4,5 GB" bir tahmindi; ölçülen süreç RSS tepesi ~3,5 GB'dır.

### 6.2 Alt süreç neden özel bellek taşır: iki kaynak

**(a) Yazınca-kopyala.** Alt süreç sorguda dokunduğu nesnelerin başvuru sayaçlarına yazar; o sayfalar kopyalanır.
Ölçülen: indeks boyutunun %48–64'ü (küçük indekste oran büyür, çünkü alt sürecin kendi taban belleği de sayılır).
Alt sürecin GC'si `gc.freeze()` ile ebeveynden gelen nesneleri dolaşmaz. Ebeveyn de alt süreç yaşarken kendi GC'sini
dondurur; dondurulmazsa ebeveynin tam toplaması ortak sayfaları kopyalatır (335 MB'lık süreçte +68 MB).

**(b) Devralınıp sonradan bırakılan sayfalar (denetim bulgusu, doğrulandı).** `fork` anında ebeveynde canlı olan her
nesnenin fiziksel sayfaları alt süreçte de eşlenir. Ebeveyn o nesneyi SONRA bırakırsa sayfalar serbest kalmaz; alt
sürecin özel belleği olur ve cgroup'ta sayılır. Bunun için sorgu gerekmez: ölçümde alt sürecin USS'i eski indeks
bırakıldığı anda 106 → 280 MB oldu (§6.4).

Ne zaman olur? Tur pattern kanıtını sembol başına bir `_pattern_evidence` çağrısıyla alır: paketi o çağrıda okur ve
motoru çağrı bitene dek tutar. Iska varsa bu süre hesabı ve panel yazımını kapsar; süreç içi bir eski sürüm ıskası
VPS'te ~25 sn sürer. Yayım bu pencereye denk gelirse ön ısıtma yeni sürüm için fork ettiğinde eski indeks canlıdır. Tur
çağrısı bitince eski indeks ebeveynde serbest kalır ve önlem olmasa işin sonuna dek (7–14 dk) alt süreçte kalırdı.

Denetim notu "tur paketi bir kez okur ve turun sonuna dek tutar" diyordu. Kodda okuma sembol başınadır, yani pencere
tek bir kanıt çağrısıdır. Etkisi yine de aynıdır: işin geri kalanında ≈ bir indeks.

Yayım turların arasına düşerse eski paket yayım anında bırakılır ve motor, başvuru sayımıyla hemen serbest kalır (GC
gerekmez). Alt süreç bu durumda eski indeksi hiç görmez.

### 6.3 Önlemler (ayrıntı §3.4)

1. **Ölüm kancası ve yeniden fork.** Fork anında canlı olan eski motorlara kanca kurulur. Kanca, motorun içeriği
   bırakılmadan ÖNCE koşar ve alt süreci SIGKILL ile öldürür; alt sürecin eski indeks yüzünden büyümesine fırsat
   kalmaz. Ardından aynı motor için yeniden fork edilir; yeni alt süreç eski indeksi içermez. Bedeli: uçuştaki bir
   sembolün alt süreçte yeniden hesabı (en çok ~25 sn CPU) ve bir fork (8–16 ms).
2. **Eski sürümün alt süreci yayım ANINDA ölür.** Yazınca-kopyala sayfaları hemen iade edilir. İki alt süreç hiçbir
   zaman bir arada yaşamaz.
3. **Bellek payı koruması.** Alt süreç ancak yeterli bellek payı varken kurulur ve pay daralırsa öldürülür (eşikler
   §6.5'te). Bu, tahminlerden bağımsız bir sınırdır.

### 6.4 Ölçüm (yerel)

Komut: `python scripts/bench_tour_contention.py --memory --bars N`. Her durum ayrı bir yorumlayıcıda koşar. İki indeks
(eski + yeni) bellekteyken yeni indeksin ön ısıtması başlar. Toplam PSS = ebeveyn + alt süreç; paylaşılan bir sayfa iki
sürece bölünür, yani çift sayım yoktur. Değerler MB'dır. M, işin log satırındaki "özel bellek M MB" değeridir (alt
sürecin iş boyunca bildirdiği en yüksek USS).

`--bars 3000` (45.936 olay/indeks, indeks ≈ 201 MB):

| Durum | iki indeks | ön ısıtma 2 sembol | eski indeks bırakıldı (+1 sn) | +2 sembol | tur benzeri iş | alt süreç USS | M |
|---|---:|---:|---:|---:|---:|---:|---:|
| süreç içi (anahtar kapalı) | 480 | 489 | 431 | 430 | 431 | — | — |
| alt süreç, yayım turlar ARASINDA (eski fork'tan önce bırakıldı) | 482 → 425 | 523 | — | — | 532 | 113 | 114,1 |
| alt süreç, yayım tur ORTASINDA, kancasız (düzeltme turu 3'ten önceki kod) | 480 | 585 | **705** | 701 | **705** | **285** | **289,2** |
| alt süreç, yayım tur ORTASINDA, bugünkü kod | 467 | 574 | 433 (yeni pid, USS 23) | 518 | 524 | 115 | 115,6 |

`--bars 1500` (21.936 olay/indeks, indeks ≈ 101 MB):

| Durum | iki indeks | ön ısıtma 2 sembol | eski indeks bırakıldı (+1 sn) | +2 sembol | tur benzeri iş | alt süreç USS | M |
|---|---:|---:|---:|---:|---:|---:|---:|
| süreç içi | 284 | 290 | 278 | 278 | 278 | — | — |
| alt süreç, turlar arasında | 284 → 277 | 334 | — | — | 343 | 63 | 64,1 |
| alt süreç, tur ortasında, kancasız | 276 | 334 | **414** | 413 | **417** | **149** | **149,9** |
| alt süreç, tur ortasında, bugünkü kod | 276 | 334 | 294 (yeni pid, USS 25) | 336 | 342 | 64 | 64,6 |

Okuma:

- Kancasız kodda eski indeks bırakılınca toplam bellek düşmedi, arttı (+120 MB / +80 MB). Alt süreç M ≈ 1,4 × indeks
  taşıdı: yazınca-kopyala payı artı eski indeksin ≈ %90'ı. Denetimin ölçümü (≈ bir indeksin %90'ı) yeniden üretildi.
- Bugünkü kodda tur ortası yayım, turlar arası yayımla aynıdır (524 / 532 MB; M 115,6 / 114,1). Alt süreç öldürülüp
  yeniden kurulduğu an USS 23–25 MB'a iner.
- Alt sürecin süreç içi yola göre ek maliyeti, iş sürerken: +93…+101 MB (201 MB'lık indekste, ≈ %50) ve +64 MB (101
  MB'lık indekste). İş bitince sıfırdır.
- Tur ortası yayımın kısa bir anı (eski indeks hâlâ turun çağrısındayken: "ön ısıtma 2 sembol" sütunu) iki indeksi ve
  alt süreci birlikte gösterir (574–585 MB). Bu, tur çağrısı bitene dek sürer ve en çok bir kanıt çağrısı uzunluğundadır.

Önceki turların ölçümleri hâlâ geçerlidir:

| Ölçü (yerel) | Değer |
|---|---|
| İndeks boyutu | ≈ 4,4–4,6 KB/olay (21,9k olay ≈ 101 MB; 45,9k ≈ 201 MB; 50,7k ≈ 233 MB; 100,3k ≈ 453 MB) |
| `fork` süresi | 8 ms (341 MB süreç), 16 ms (561 MB süreç) |
| Alt süreç USS, bir tam sorgu turundan sonra (büyük indeks) | indeksin %48–49'u (113 / 233 MB; 217 / 453 MB) |
| Ebeveyn GC'si dondurulmazsa, ebeveynin tam toplaması sırasında ek kopya | +68 MB (335 MB süreç); dondurulunca 0 |
| Ebeveynin ayırdığı bellek (alt süreç yolu, tracemalloc) | indeks boyutunun < %10'u (test eşiği) |

Testler: `test_the_child_does_not_keep_a_dropped_old_index_resident`. Bu test kancasız kontrol kolunda 160 MB'lık eski
nesnenin alt süreçte kaldığını (> %80), düzeltmede yeni alt sürecin 40 MB'ın altında kaldığını gösterir. Diğer testler
§3.4'te.

### 6.5 VPS'e ölçekleme (tahmin, ölçüm değil) ve güvenlik sınırı

- **İndeks boyutu.** I ≈ olay × 4,5 KB. Kodun notundaki 138.891 olay için I ≈ 0,62 GB. Sahip gerçek olay sayısını yayım
  satırından okur ve buna göre düzeltir.
- **Alt sürecin özel belleği.** Bugünkü kodla M ≈ 0,5–0,6 × I ≈ 0,3–0,4 GB. Bu, yayımın tur ortasına ya da turlar
  arasına düşmesinden bağımsızdır. Düzeltme turu 3'ten önceki kodla tur ortası yayımda M ≈ 1,4 × I ≈ 0,9 GB olurdu
  (denetimin ≈0,85 GB'ı).
- **Tepe.** Alt süreç yalnız bir yayımdan SONRA ve iş süresince (7–14 dk) yaşar. Worker'ın RSS tepesi (~3,5 GB, eski ve
  yeni indeksin birlikte durduğu kurulum) yayımdan ÖNCE oluşur. İkisi ancak bir sonraki kurulum, önceki işin alt süreci
  hâlâ yaşarken başlarsa çakışır. Aynı 4h kapanışının ikinci yayımı ~20 dk sonra gelir ve iş normalde ondan önce biter.
  Çakışırsa toplam ≈ 3,5 + 0,4 ≈ 3,9 GB anonim bellek olur. `MemoryMax` 6 GiB (6,44 GB) olduğundan pay ≈ 2,5 GB kalır.
  İki alt süreç hiçbir zaman bir arada yaşamaz (§6.3/2).
- **`MemoryPeak` 5,45 GB.** Bu değer sayfa önbelleğini içerir. Çekirdek sayfa önbelleğini OOM'dan önce geri alır;
  dolayısıyla anonim belleğin sınıra bu kadar yakın olduğu anlamına gelmez. Koruma da sayfa önbelleğini saymaz.
- **Güvenlik sınırı: bellek payı koruması.** Tahmin yanlış çıksa bile koruma geçerlidir.
  - Pay, en dar olan değerdir: her cgroup sınırı için `sınır − (kullanım − dosya önbelleği)`, ayrıca sistemin
    `MemAvailable`'ı.
  - Fork yalnız pay ≥ 1.536 MB iken yapılır.
  - Alt süreç yaşarken pay her istekten önce ve istek beklenirken saniyede bir okunur. 512 MB'ın altına inerse alt süreç
    öldürülür ve iş süreç içi yolla tamamlanır (bugünkü yol, ek bellek yok).
  - Böylece alt süreç cgroup'u `MemoryMax`'a itemez: sınıra yaklaşılırsa ilk giden odur.
  - Varsayımı: bellek bir saniyede 512 MB'tan hızlı büyümez. Alt sürecin kendi büyümesi (yazınca-kopyala) dakikalara
    yayılır; ölçümde birkaç MB/sn. Eski indeks kaynağı ise kancayla anında kapanır.
  - Worker'ın KENDİ büyümesine karşı koruma bugünkü kadardır; bu değişiklik onu değiştirmez.
- **Kalan küçük kaynak.** Ebeveynin fork'tan sonra bıraktığı geçici tur verisi de alt süreçte kalır (ölçüm: tur benzeri
  iş +6–9 MB). Bunun için yeniden fork yapılmaz; koruma sınırlar.

Sonuç: güvenlik ölçüm ve çalışma anı koruması ile gösterildiği için anahtar varsayılan AÇIK kalır. Sahip yine de
`history.evidence_subprocess: false` ile kapatabilir.

### 6.6 OOM'da ne olur

Önceki sürüm "alt süreç ölür, worker süreç içi yola düşer" diyordu; bu, systemd'nin varsayılanında **doğru değil**.
Depodaki birimde `OOMPolicy=` yok, dolayısıyla `DefaultOOMPolicy=stop` geçerlidir: cgroup'taki HERHANGİ bir süreç
(`oom_score_adj=1000` ile önce seçilen alt süreç dahil) çekirdeğin OOM öldürücüsüyle öldürülürse systemd bütün birimi
durdurur (sonuç `oom-kill`). `Restart=on-failure` birimi yeniden başlatır. Sonuç bugünkü bir OOM ile aynıdır: 30 sn
bekleme, tam indeks kurulumu, uzun ilk tur. Bu yüzden bellek payı koruması alt süreci OOM'dan ÖNCE öldürür (§6.5).
`oom_score_adj=1000` yine de işe yarar: öldürülen worker değil alt süreç olur ve worker birim durdurulurken düzenli
kapanır (yazma ortasında çekirdekçe öldürülmez). "Alt süreç ölür, worker sürer" davranışı istenirse VPS drop-in'ine
`OOMPolicy=continue` eklenmelidir. Bu yalnız işletim değişikliğidir, karar-nötrdür ve **sahip onayı ister** (öneri,
§10).

### 6.7 İzleme notu

`health.json` `hwm_mb` (VmHWM) yalnız worker sürecini ölçer; alt süreci İÇERMEZ. `f8b05fb`'nin `--check` bellek
tetiği bu yüzden alt sürecin belleğini görmez. Alt sürecin belleği ön ısıtma satırındaki M'dir. Cgroup toplamı için
`systemctl show tradingbot-worker -p MemoryPeak` (ya da `MemoryCurrent` örnekleri) okunmalıdır; bir sonraki sürüm
betiğinin bellek tetiği buna dayanmalıdır (§10).

## 7. CPU

Depodaki birim `CPUQuota=150%` der (VPS'te farklı olabilir; VPS 4 vCPU — sahibin aktardığı). Alt süreç ve tur
aynı cgroup'tadır: ikisi de CPU'ya doymuşken toplam 1,5 çekirdeğe kısılırlar, yani tur CPU'ya bağlı adımlarında en kötü
~0,75 çekirdek alır (≈1,3×). Alt sürecin `nice +10`'u tek çekirdekte turu öne alır; kotada yalnız hız etkilenir, sonuç
değil.

GIL açlığının şiddeti çekirdek sayısına ve makinenin yüküne bağlıdır: bekleyen iş parçacığı başka bir çekirdekte uyanıp
yarışı kaybettikçe büyür. Aynı betik (`--bars 1000`, 13.936 olay) `taskset` ile kısıtlanarak (arka planda başka bir test
koşarken; gösterge niteliğinde):

| Çekirdek | Süreç içi ön ısıtma (py / numpy / net / file) | Alt süreç (py / numpy / net / file) |
|---|---|---|
| 1 | 1,5× / 1,9× / 1,8× / 3,0× | 1,0× / 1,3× / 1,4× / 1,2× |
| 2 | 5,4× / 2,8× / 2,1× / 2,5× (uyanma gecikmesi en çok 382 ms) | 0,7× / 1,0× / 0,5× / 1,1× |
| 4, boşta (§2.2) | 9,5× / 94× / 33× / 145× | 1,1× / 0,8× / 0,8× / 1,1× |
| 4, yük ortalaması 2–3,5 (§2.2) | 8,6× / 42× / 55× / 9,2× | 1,1× / 0,7× / 0,9× / 1,4× |
| 4, yük ortalaması 6–7 (denetim, §2.2) | 1,0–2,7× | 0,6–1,3× (bir koşuda `net` 3,3×) |

VPS'teki 5× (semboller) ve 33× (yürütme) bu geniş aralığın içindedir; hangi satıra denk geldiği bilinmiyor. Alt süreç
yolu her durumda ~1×'tir.

## 8. Geri dönüş anahtarı

```yaml
history:
  evidence_subprocess: false   # 2026-10-05: varsayılan true idi → 2026-10-06'dan beri VARSAYILAN false (satır gerekmez);
                               # alt süreci açmak: true. Yalnız true/false (tırnaklı değer config doğrulamasında reddedilir)
```

**2026-10-06 notu:** kod varsayılanı `false` oldu (yukarıdaki DURUM notu; V2 §9). `false` yolu aşağıda anlatılan süreç
içi yoldur ve artık üretim yoludur; alt süreç yalnız `evidence_subprocess: true` ile (Linux) kurulur. Test adı
`test_tours_are_identical_with_the_in_process_prewarm_kill_switch` → `test_tours_are_identical_with_the_child_switch_on`
(alt süreç açık koşu), `test_the_switch_defaults_on_and_config_can_turn_it_off` →
`test_the_switch_defaults_off_and_config_can_turn_it_on`.

`false` iken `request_prewarm(..., use_child=False)` çağrılır ve tur ıskası doğrudan `_evidence_query` ile hesaplanır —
2026-10-01'den beri koşan süreç içi yolun kendisi: aynı kilitler (iş sonunda ek `compute_lock` alımı yok), aynı log
satırı (`pattern kanıtı ön ısıtıldı: sürüm N, … sn`, ek yok), alt süreç hiç kurulmaz (testler:
`test_kill_switch_off_never_forks...`, `test_the_kill_switch_path_logs_todays_line_and_takes_no_extra_lock`,
`test_tours_are_identical_with_the_in_process_prewarm_kill_switch`). Anahtar config.yaml'a yazılmadı (varsayılan
dataclass'tadır); kapatmak için yukarıdaki satır `history:` bölümüne eklenip worker yeniden başlatılır. Etkin değer
yenileyici başlarken bir kez loglanır (`pattern kanıtı sorguları: ALT SÜREÇTE …` ya da `… süreç içinde (…)`).

## 9. VPS'te doğrulanacaklar ve kalanlar

Dağıtım sonrası bakılacaklar (hiçbiri burada VPS'te ölçülmedi):

1. 4h kapanışlarından sonraki turlar: `tur fazları` satırında semboller/yürütme olağan düzeyde mi (~108 / ~26 sn).
   Ölçüt: yayımı tur ORTASINA düşen 4h-kapanışı turları ile yayımı turlar ARASINA düşenler karşılaştırılır (bugün
   ilki ~43 dk, ikincisi ~5,5 dk).
2. Başlangıçta bir kez `pattern kanıtı sorguları: ALT SÜREÇTE (history.evidence_subprocess=True; OpenBLAS iş parçacığı
   N; fork koruması 30 sn)` — N VPS'teki fork asılma riskini söyler (N = 1 → yok).
3. Her yayımdan sonra `pattern indeksi yenilendi: sürüm N, … olay, … seri, kurulum S sn` (kurulumun turla çakışma
   süresi) ve `pattern kanıtı ön ısıtıldı: sürüm N, … sn (alt süreç pid P, özel bellek M MB; alt süreçte X, süreç içi Y
   sembol)` — Y = 0 beklenir. `pattern kanıtı alt süre` ile başlayan satır (`alt süreci başlatılamadı` / `arızalandı` /
   `alt süreçte hesaplanamadı` / `alt süreci KAPALI` / `alt süreci başlatılmadı — bellek payı yetersiz` / `alt süreci
   bellek koruması nedeniyle kapatıldı` / `alt süreci bu işte … kez yeniden kuruldu`) YOK. Beklenen, sorun OLMAYAN bilgi
   satırları (önekleri bilerek farklı): `pattern kanıtı: eski sürümün alt süreci (sürüm N) daha yeni yayımla hemen
   kapatıldı` — bir yayım önceki iş sürerken gelince; `pattern kanıtı: alt süreç yeniden kuruldu — …` ve ön ısıtma
   satırında `; eski indeks için yeniden fork R` — yayım turun uçuştaki kanıt çağrısına denk gelince (§6.2). Not:
   `f8b05fb`'nin `--check`'i bu yeni satırları ne sayar ne gösterir (yalnız `pattern kanıtı ön ısıt` ve `PREWARM_ERR_RE`'yi
   okur); bir sonraki sürüm betiği bunları saymalı (§10).
4. Deadman olayı: günlükte `status=14/ALRM` ve `state/pattern_evidence_fork.marker` dosyası YOK. Varsa: fork asıldı,
   worker bir kez yeniden başladı ve alt süreç o makinede kapalı; karar sahibin (§10'daki `OPENBLAS_NUM_THREADS=1`
   önerisi ya da anahtarı kapatmak). Dosya silinince bir sonraki yayımda yeniden denenir.
5. Box `missed_bars` artmıyor, `lag_max_s` olağan.
6. Bellek (§6.5): ön ısıtma satırındaki M (alt sürecin iş boyunca en yüksek özel belleği). Tahmin, I ≈ 0,62 GB'lık
   indeks için M ≈ 0,3–0,4 GB (≈ 0,5–0,6 × I); yayım tur ORTASINA da turlar ARASINA da düşse aynı (eski indeks artık alt
   süreçte kalmaz). İki durumun M'si ayrı ayrı karşılaştırılır: tur ortası yayımın işi `; eski indeks için yeniden fork
   R` ekini taşır. M ≈ 1,4 × I (≈ 0,9 GB) görülürse eski indeks alt süreçte kalmış demektir: §6.3'ün önlemi çalışmıyor →
   sinyal.
   Cgroup tepesi `systemctl show tradingbot-worker -p MemoryPeak -p MemoryCurrent` (sayfa önbelleği DAHİL; bugün 5,45 GB);
   `health.json` `hwm_mb` alt süreci içermez (bugün ~3,5 GB). Bellek koruması uyarısı (ölçüt 3) bu sürümde
   beklenmez; görülürse pay 512 MB'ın altına inmiş demektir (alt süreç değil, worker'ın kendisi büyümüştür).
7. Yeniden başlatma sonrası ilk tur — dağıtımın kendi yeniden başlatması dahil: ilk indeks kurulumu hâlâ süreç içidir
   (§4); ön ısıtmanın çekişmesi gider ama ilk tur olağandan **uzun kalabilir ve bir Box mumu kaçabilir** (bugünkü
   3.763,7 sn'lik ilk tur ön ısıtmayla açıklanmıyordu). Bu, bu değişikliğin geri alma nedeni sayılmamalı; ölçüt 1'deki
   karşılaştırmadır.
8. Kapanış (§3.3): `systemctl stop/restart` bir ön ısıtma işi sürerken bile birkaç saniyede biter; günlükte
   `State 'stop-sigterm' timed out` / `Killing process` YOK; `İzleme temiz durduruldu` satırı var.

**Geri alma sinyalleri** (sürüm notuna ve bir sonraki `--check`'e; herhangi biri görülürse anahtar kapatılır ya da geri
alınır):

- Günlükte worker için `code=killed, status=14/ALRM` — deadman fork asılmasında worker'ı sonlandırdı (§3.1, §5 "Kalan
  karar riski"). Tek olay bile sinyaldir: bugün olamayan bir yeniden başlatmadır.
- `state/pattern_evidence_fork.marker` dosyasının varlığı — aynı olayın kalıcı izi (alt süreç o makinede artık kapalı).
- Bir `systemctl stop/restart`'ın `timeout` sonucu ya da `stop-sigterm timed out` — §3.3'ün önlediği kapanış beklemesi.
- Ölçüt 3'teki `pattern kanıtı alt süre…` uyarıları ya da ön ısıtma satırında `süreç içi Y` > 0 (alt süreç arızası; karar
  değişmez ama tasarım çalışmıyor demektir). Bellek koruması uyarıları da buraya girer: karar değişmez, alt süreç
  zaten kapatılmıştır; ama worker'ın bellek payının daraldığını söyler → `MemoryPeak`/`MemoryCurrent` ile bakılır.
- Ölçüt 6'da M ≈ 1,4 × I (≈ 0,9 GB; eski indeks alt süreçte kalmış) ya da `MemoryPeak`'in bugünkü 5,45 GB'ı belirgin
  aşması.

### 9.1 Sürüm notuna girecek metin (denetim: saat-duvarı paragrafı geri alma sinyallerinin YANINDA)

Sürüm notu sahibe şu iki parçayı **birlikte** göstermelidir; biri olmadan öteki eksik kalır:

> **Saat-duvarı zamanı — sahibin değerlendirmesine (§5, aynen).** Kod ve kurallar aynı; zamanlama aynı değil ve olamaz:
>
> - Turlar coin head'e ve sona daha erken ulaşır; Box zamanlayıcısının daha az mum kaçırması beklenir. Bunlar bugüne
>   göre **amaçlanan davranış değişiklikleridir** (karar mantığı değil, zamanlama).
> - 4h kapanışından sonraki **ikinci yayım** (ör. sürüm 7, 07:33:49) bugün süreç içi sürüm 6 ön ısıtmasıyla GIL için
>   yarışan bir kurulumun sonunda geliyordu. Yenileyicinin bekleme aralığı kurulum bittikten sonra başladığı için, ön
>   ısıtma alt süreçteyken kurulum daha hızlı bitip ikinci yayım saat olarak **daha erken** gelebilir.
> - Bu ikisi birlikte, belli bir turun bir yayımdan önce mi sonra mı okuduğunu — yani saat-duvarı açısından hangi turun
>   hangi sürümü gördüğünü — değiştirebilir. Bugün de aynı şey makinenin yüküne göre değişir (aynı kod, farklı gün,
>   farklı zamanlama); hiçbir yayım bilerek ertelenmez ya da öne alınmaz ve okuma kuralı aynıdır. Bunun "karar
>   değişikliği yok" kuralına uygun olup olmadığına sahip karar verir; uygun görmezse `history.evidence_subprocess:
>   false` bugünkü zamanlamaya döner.
>
> **Geri alma sinyalleri (§9).** `status=14/ALRM` · `state/pattern_evidence_fork.marker` · kapanışta `timeout` /
> `stop-sigterm timed out` · `pattern kanıtı alt süre…` uyarıları (bellek koruması dahil) ya da `süreç içi Y` > 0 ·
> ön ısıtma satırında M ≈ 0,9 GB (≈ 1,4 × indeks) ya da `MemoryPeak`'in 5,45 GB'ı belirgin aşması. Herhangi biri →
> anahtarı kapat (`history.evidence_subprocess: false`, worker yeniden başlatılır) ya da geri al.
>
> **Bellek (§6, ölçülen yerel, VPS için tahmin).** Alt süreç yalnız bir ön ısıtma işi süresince (yayım başına 7–14 dk)
> ≈ 0,5–0,6 × indeks ek bellek tutar (VPS'te ≈ 0,3–0,4 GB); eski indeksi tutmaz (yayımı tur ortasına düşen işlerde de).
> Bellek payı 1,5 GB'ın altındaysa kurulmaz, 512 MB'ın altına inerse kapatılır (cgroup `MemoryMax` ve sistem belleği;
> sayfa önbelleği sayılmaz). Kanıt ve kararlar her durumda aynıdır.

## 10. Bilinçli olarak yapılmayanlar / öneriler (ayrı iş, sahip onayı)

- **`OPENBLAS_NUM_THREADS=1`** (worker ortamı): OpenBLAS havuzunu ve §3.1'deki fork asılma riskini tamamen kaldırır.
  AMA matris-vektör ve uzun nokta çarpımlarının (ör. `LogisticModel.fit`'teki `Xs.T @ g`) toplama sırasını değiştirip
  son biti oynatabilir; bu olasılıklar karar eşiklerine girer. Önce iş parçacığı sayısının bu çağrıların sonucunu
  değiştirmediğinin (VPS'in CPU'su ve OpenBLAS sürümüyle) ayrı bir karar-nötrlük kanıtı gerekir. Karar riski taşıdığı
  için bu işte yapılmadı.
- **`OOMPolicy=continue`** (VPS drop-in): alt sürecin OOM'unda bütün birimin durmasını önler; yalnız işletim, karar-nötr.
- **`WatchdogSec` + `sd_notify`** (koruyucu izleyiciden): yalnız fork'u değil, worker'ın her türlü donmasını yeniden
  başlatmaya çevirir; birim + kod değişikliği.
- **Sonraki sürüm betiğinin `--check`'i:** `pattern kanıtı alt süre` satırlarını say/göster; ön ısıtma satırındaki
  `(alt süreç pid …; alt süreçte X, süreç içi Y sembol)` ekini raporla (Y > 0 → alt süreç bir işte arızalandı);
  `status=14/ALRM` ve işaret dosyasını **geri alma tetiği** olarak ara (§9 "Geri alma sinyalleri"); kapanış süresini ve
  `stop-sigterm timed out`'u raporla; bellek tetiğini `hwm_mb` yerine cgroup `MemoryPeak`/`memory.peak`'e dayandır;
  yayım satırındaki `kurulum S sn`'yi tur fazlarıyla birlikte göster; 4h-kapanışı turlarını yayımın tur ortasına düşüp
  düşmediğine göre ayır; ön ısıtma satırındaki M'yi ve `; eski indeks için yeniden fork R` / `; bellek koruması` /
  `; bellek payı yetersiz` eklerini raporla (M ≈ 1,4 × indeks → eski indeks alt süreçte kalmış: geri alma tetiği).
- **`health.json`'a alt süreç sayaçları:** `EvidenceCache.stats` (`child_*`) bugün yalnız bellekte. `health.json` bayt
  bayt karşılaştırılan altın testlere girdiği için bu iş o testlerin bilinçli güncellenmesini ister; bu turda günlük
  satırlarıyla yetinildi.
- Sorgu döngüsünü vektörleştirmek (olay başına Python + `np.corrcoef` yerine toplu numpy) 12,6 sn'lik sorguyu büyük
  ölçüde kısaltır ve GIL sorununu kökten azaltır; ama kanıtın bit-aynı kaldığı ayrıca kanıtlanmalıdır (komşu
  sıralamasında eşitlik bozma, kayan nokta toplama sırası). Karar riski taşıdığı için bu işin kapsamı dışında bırakıldı.
  → 2026-10-06: bit-aynılık kanıtıyla yapıldı, bkz. docs/TOUR_CONTENTION_V2.md (`history.evidence_fast_knn`).
- İndeks kurulumu (`add_series`, satır başına `feats.iloc[i]`) da aynı şekilde hızlandırılabilir; yayım anını öne
  çekeceği için sahip onayı gerektirir.
