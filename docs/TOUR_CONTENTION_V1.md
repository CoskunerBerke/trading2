# Tur çekişmesi V1 — pattern kanıtı sorguları ayrı süreçte

Tarih: 2026-10-05 · Taban: `1c2c6e2` (VPS'te çalışan `f8b05fb` + sonraki araştırma/belge/test commit'leri) ·
Dal: `impl/tourfix` · Düzeltme turu 1: iki mercekli denetimin bulguları (§3.1 fork asılması, §6 OOM, §5 saat-duvarı).
Düzeltme turu 2: kapanış alt süreci beklemez (§3.3), alt süreç devralınan tanımlayıcıları tutmaz (§3.2), işaret dosyası
yedeğe girmez (§3.1), geri alma sinyalleri ve kalan karar riski (§5, §9).

Bu bir **karar-nötr performans onarımıdır**: strateji, eşik, defter, boyut ve config değeri değişmez. Yeni indeksin
yayımlandığı kod noktası ve kuralı, turun indeks sürümünü okuma kuralı, kanıt önbelleğinin anahtarı ve kanıtın kendisi
aynıdır; değişen tek şey kNN kanıt sorgularının **hangi süreçte** koştuğudur. Geri dönüş anahtarı:
`history.evidence_subprocess: false`. Saat-duvarı zamanlaması (turlar ve yayımlar ne zaman biter) değişir — amaç budur;
sahibin değerlendirmesi için §5'te açıkça yazılıdır.

Bu belgedeki sayılar yerel makinelerde ölçülmüştür (4 çekirdek, Python 3.12, ağ yok). VPS'te ölçülmüş değildir; VPS'te
neyin doğrulanacağı §9'dadır. §2'deki büyük yavaşlama oranları **boştaki** makineden gelir; aynı betik yüklü makinede
çok daha küçük oranlar verdi (§2.2). VPS'teki uzun turların bu mekanizmayla açıklanması bir **hipotezdir**.

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
açıklamadığı VPS'in çekirdek sayısına ve yüküne bağlıdır; ikisi de bilinmiyor → dağıtım sonrası §9.1/§9.5 ile
doğrulanacak bir hipotez.

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
5. İş bitince ya da daha yeni yayım görülünce (sembol sınırında) alt süreç kapatılır.

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

## 6. Bellek

| Ölçü (yerel) | Değer |
|---|---|
| İndeks boyutu | ≈4,5 KB/olay (50,7k olay ≈ 233 MB; 100,3k olay ≈ 453 MB) |
| `fork` süresi | 8 ms (341 MB süreç), 16 ms (561 MB süreç) |
| Alt süreç özel belleği (USS), bir tam sorgu turundan sonra | indeksin %48–49'u (113 MB / 233 MB; 217 MB / 453 MB) |
| Gerçek uygulama, 100,3k olay, alt süreç 3 sembol hesaplarken ebeveyn tur benzeri iş + 17 tam `gc.collect()` | alt süreç özel belleği en çok 226 MB (%50); ebeveyn RSS 700 → 716 MB |
| Ebeveyn GC'si dondurulmazsa, ebeveynin tam toplaması sırasında ek kopya | +68 MB (335 MB süreç); dondurulunca 0 |
| Ebeveynin ayırdığı bellek (alt süreç yolu, tracemalloc) | indeks boyutunun < %10'u (test eşiği) |

Neden kopya oluşuyor: alt süreç sorgu sırasında dokunduğu her nesnenin başvuru sayacına yazar; o nesnelerin sayfaları
kopyalanır. Alt süreç `gc.freeze()` ile ebeveynden gelen nesneleri GC'de dolaşmaz; ebeveyn de alt süreç yaşarken kendi
GC'sini dondurur (iç içe güvenli sayaç; yalnız GC zamanlamasını etkiler, hiçbir değeri değil) ve son alt süreç kapanınca
çözer.

VPS'e ölçekleme (tahmin, ölçüm değil): ~152k olay ≈ 0,7 GB indeks → alt süreç ≈ 0,35 GB, yalnız ön ısıtma süresince
(yayım başına ~7 dk; CPU kotası altında en çok ~14 dk), kapanınca iade edilir. Bugünkü tepe (yeniden kurulumda eski +
yeni indeks, ~4,5 GB) alt süreç yaşarken genellikle oluşmaz: alt süreç yayımdan SONRA kurulur ve bir sonraki kurulum en
erken bir sonraki yenileme döngüsündedir. Çakışırsa tahmin ≈4,5 + 0,35 GB < 6 GB `MemoryMax` (ölçülmemiş tahmin).

**OOM'da ne olur (düzeltildi).** Önceki sürüm "alt süreç ölür, worker süreç içi yola düşer" diyordu; bu systemd'nin
varsayılanında **doğru değil**. Depodaki birimde `OOMPolicy=` yok → `DefaultOOMPolicy=stop`: cgroup'taki HERHANGİ bir
süreç (`oom_score_adj=1000` ile önce seçilen alt süreç dahil) çekirdeğin OOM öldürücüsüyle öldürülürse systemd bütün
birimi durdurur (sonuç `oom-kill`) ve `Restart=on-failure` yeniden başlatır — bugünkü bir OOM ile aynı sonuç (30 sn
bekleme, tam indeks kurulumu, uzun ilk tur). Alt sürecin yeni olan tek katkısı yayım başına 7–14 dk boyunca ≈0,35 GB ek
bellektir. `oom_score_adj=1000` yine de işe yarar: öldürülen worker değil alt süreç olur, worker birim durdurulurken
düzenli kapanır (yazma ortasında çekirdekçe öldürülmez). "Alt süreç ölür, worker sürer" davranışı istenirse VPS
drop-in'ine `OOMPolicy=continue` eklenmelidir — yalnız işletim, karar-nötr, **sahip onayı ister** (öneri, §10).

İzleme notu: `health.json` `hwm_mb` (VmHWM) yalnız worker sürecini ölçer; alt süreci İÇERMEZ — `f8b05fb`'nin `--check`
bellek tetiği alt sürecin belleğini görmez. Cgroup toplamı için `systemctl show tradingbot-worker -p MemoryPeak` (ya da
`MemoryCurrent` örnekleri) okunmalı; bir sonraki sürüm betiğinin bellek tetiği buna dayanmalı (§10).

## 7. CPU

Depodaki birim `CPUQuota=150%` der (VPS'te farklı olabilir; VPS'in çekirdek sayısını bilmiyorum). Alt süreç ve tur
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
  evidence_subprocess: false   # varsayılan true; yalnız true/false (tırnaklı "false" config doğrulamasında reddedilir)
```

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
   `alt süreçte hesaplanamadı` / `alt süreci KAPALI`) YOK. Not: `f8b05fb`'nin `--check`'i bu yeni uyarıları ne sayar ne
   gösterir (yalnız `pattern kanıtı ön ısıt` ve `PREWARM_ERR_RE`'yi okur); bir sonraki sürüm betiği bunları saymalı (§10).
4. Deadman olayı: günlükte `status=14/ALRM` ve `state/pattern_evidence_fork.marker` dosyası YOK. Varsa: fork asıldı,
   worker bir kez yeniden başladı ve alt süreç o makinede kapalı; karar sahibin (§10'daki `OPENBLAS_NUM_THREADS=1`
   önerisi ya da anahtarı kapatmak). Dosya silinince bir sonraki yayımda yeniden denenir.
5. Box `missed_bars` artmıyor, `lag_max_s` olağan.
6. Cgroup bellek tepesi (`MemoryPeak`) ve alt sürecin özel belleği (log satırındaki M) — tahmin ≈0,35 GB.
   `health.json` `hwm_mb` alt süreci içermez (§6).
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
  değişmez ama tasarım çalışmıyor demektir).

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
  düşmediğine göre ayır.
- **`health.json`'a alt süreç sayaçları:** `EvidenceCache.stats` (`child_*`) bugün yalnız bellekte. `health.json` bayt
  bayt karşılaştırılan altın testlere girdiği için bu iş o testlerin bilinçli güncellenmesini ister; bu turda günlük
  satırlarıyla yetinildi.
- Sorgu döngüsünü vektörleştirmek (olay başına Python + `np.corrcoef` yerine toplu numpy) 12,6 sn'lik sorguyu büyük
  ölçüde kısaltır ve GIL sorununu kökten azaltır; ama kanıtın bit-aynı kaldığı ayrıca kanıtlanmalıdır (komşu
  sıralamasında eşitlik bozma, kayan nokta toplama sırası). Karar riski taşıdığı için bu işin kapsamı dışında bırakıldı.
- İndeks kurulumu (`add_series`, satır başına `feats.iloc[i]`) da aynı şekilde hızlandırılabilir; yayım anını öne
  çekeceği için sahip onayı gerektirir.
