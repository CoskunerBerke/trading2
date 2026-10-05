# Tur çekişmesi V1 — pattern kanıtı sorguları ayrı süreçte

Tarih: 2026-10-05 · Taban: `1c2c6e2` (VPS'te çalışan `f8b05fb` + sonraki araştırma/belge/test commit'leri) ·
Dal: `impl/tourfix`

Bu bir **karar-nötr performans onarımıdır**: strateji, eşik, defter, boyut ve config değeri değişmez. Yeni indeksin
yayımlandığı nokta, turun indeks sürümünü okuma biçimi, kanıt önbelleğinin anahtarı ve kanıtın kendisi aynıdır;
değişen tek şey kNN kanıt sorgularının **hangi süreçte** koştuğudur. Geri dönüş anahtarı:
`history.evidence_subprocess: false`.

Bu belgedeki sayılar yerel makinede ölçülmüştür (4 çekirdek, Python 3.12.3, ağ yok). VPS'te ölçülmüş değildir; VPS'te
neyin doğrulanacağı §9'dadır.

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

## 2. Mekanizma — ölçüldü

### 2.1 Kim kimi aç bırakıyor

Ön ısıtma işçisi (`patterns/evidence_cache.py`, 2026-10-01) yayımdan hemen sonra worker sürecinin İÇİNDE, indeks
sembolleri × LONG/SHORT kNN sorgusu koşar (VPS: 138.891 olayda sorgu başına 12,6 sn; 16 sembol × 2 yön ≈ 400 sn CPU,
4h kapanışı başına iki yayım). Sorgu döngüsü (`patterns/engine.py` `query`) indeksteki HER olay için `np.corrcoef`
çağırır; bu çağrı BLAS içinde GIL'i bırakıp hemen geri alır — olay başına bir kez, yani milisaniyede onlarca kez.

CPython'da GIL bekleyen iş parçacığı, GIL 5 ms (`sys.getswitchinterval()`) boyunca HİÇ el değiştirmezse "bırak"
ister. Sık bırak-al yapan bir iş parçacığı her bırakışta bekleyeni uyandırır; bekleyen uyanıp kilidi alamadan sorgu
iş parçacığı onu geri alır ve bekleyenin 5 ms sayacı baştan başlar. Bekleyen GIL'i ancak yarışı şans eseri kazanınca
alır. Ölçüm — 1 ms uyuyan bir iş parçacığının GIL'i geri alma gecikmesi, arka planda tek bir işlem döngüdeyken:

| Arka plandaki işlem | p50 | p90 | en çok |
|---|---:|---:|---:|
| yok | 0,1 ms | — | 1,7 ms |
| saf Python döngüsü | 5,3 ms | 5,4 ms | 10,4 ms |
| pandas `iloc` | 5,3 ms | 5,4 ms | 11,0 ms |
| `np.corrcoef` (16 noktalı yol) | 3,3 ms | 24,3 ms | 106,3 ms |
| **`SimilarPatternEngine.query`** (7,5k olay) | **36,5 ms** | **143,1 ms** | **369,9 ms** |

Saf Python yük beklenen 5 ms'yi verir; aç bırakmayı sorgunun sık GIL bırakışı yapar.

### 2.2 Turun işleri ne kadar yavaşlıyor

Sentetik tur (ana iş parçacığı), gerçek `EvidenceCache` ve gerçek `SimilarPatternEngine` (21.936 olay) ile; ön ısıtma
ölçüm boyunca sürüyor. İşler: `py` saf Python, `numpy` `build_feature_frame` (ajan göstergelerine vekil), `net` ayrı
bir süreçten 16 KB'lık parçalarla 100 KB JSON okuma + çözme (HTTP vekili), `file` atomik JSON yazımı. Periyodik iş
parçacığı Box zamanlayıcısı vekilidir (0,5 sn'de bir küçük pandas işi).

| Durum | py | numpy | net | file | 1 ms uyku gecikmesi p50 / p90 / en çok | periyodik iş en uzun |
|---|---:|---:|---:|---:|---|---:|
| ön ısıtma yok | 1,0× | 1,0× | 1,0× | 1,0× | 0,7 / 2,8 / 109 ms | 1,6 ms |
| **bugün: süreç içi ön ısıtma** | **9,5×** | **94×** | **33×** | **145×** | **39 / 166 / 604 ms** | **882 ms** |
| arka planda indeks kurulumu (iş parçacığı) | 6,7× | 11,7× | 8,8× | 18× | — | — |
| **onarım: alt süreçte ön ısıtma** | **1,09×** | **0,77×** | **0,78×** | **1,11×** | **0,7 / 2,2 / 16 ms** | **2,1 ms** |

(0,8× değerleri gürültüdür: alt süreç başka çekirdekte koşar.) Tekrar çalıştırmak için ağsız betik:
`python scripts/bench_tour_contention.py --modes none,inproc,child` (sentetik veri, geçici dizin; worker'ın yanında
koşturmayın, CPU yarıştırır). Aynı mekanizmanın hızlı bir sürümü `tests/test_evidence_subprocess_v1.py`
`test_synthetic_tour_and_periodic_thread_are_not_slowed_by_the_child_prewarm` içinde CI'da koşar (yerelde 1,18×,
periyodik gecikme p90 2,0 ms; eşik 3× / 25 ms).

### 2.3 VPS günlüğüyle tutarlılık

- **semboller** (ağ + ajan göstergeleri: GIL'i sık bırakan iş) 108 → 544 sn (5×); **yürütme** (defter/dosya yazımı,
  numpy) 26 → 856 sn (33×). Yerelde aynı türden işler 33–145× yavaşladı; VPS'te fazın ancak bir kısmı ön ısıtmayla
  çakıştı (sürüm 6 ön ısıtması 07:14'te, sürüm 7'ninki 07:33'te başladı).
- **pattern kanıtı 626 sn**: tur, ön ısıtmanın henüz yetişmediği sembolleri süreç içinde kendisi hesapladı; iki sorgu
  iş parçacığı `compute_lock`'ta sırayla koşup birbirini ve geri kalan her şeyi aç bıraktı. 07:33:49'daki ikinci yayım
  coin head sürerken sürümü değiştirdi; kalan semboller yeni sürüm için yeniden hesaplandı.
- **Box kaçan mum**: Box zamanlayıcısı aynı süreçte bir iş parçacığıdır; değerlendirmesi ağ + pandas işidir ve GIL'i her
  geri alışta onlarca–yüzlerce ms bekledi (yerelde periyodik iş en uzun 882 ms, uyanma gecikmesi en çok 604 ms).

### 2.4 Ne DEĞİLDİ

- **Kilitler.** `_entry_lock`'u yalnız tur alır (`_execute`, tek `with`); yenileyici ve ön ısıtma ne `_entry_lock`'a ne
  `_ledger_lock`'a dokunur. Yürütmenin 856 sn'si arka plan işinin arkasında kilit beklemesi olamaz. Paylaşılan tek
  kilit `compute_lock`'tur ve yalnız pattern kanıtı alt fazını etkiler (tasarım gereği: indekste aynı anda tek sorgu).
- **Rate-limit bütçesi.** Her `_futures_provider_factory()` çağrısı kendi `BudgetPool`'unu kurar; yenileyicinin arşiv
  güncellemesi döngü başına en çok `refresh_max_requests: 24` istek atar (dakikalık 1.680 ağırlık bütçesinin küçük bir
  kısmı). Dakikalarca bekleme üretmez.
- **İndeks kurulumu.** Kurulum da GIL'i paylaşır (yukarıdaki tabloda 6,7–18×) ama yayım başına ~40 sn sürer ve ön
  ısıtmanın (~400 sn) onda biridir; ayrıca §4'teki nedenle bilinçli olarak süreçte bırakıldı.

## 3. Tasarım

`tradingbot/patterns/evidence_child.py` (yeni) + `evidence_cache.py` + `engine_v3.py` (iki çağrı noktası).

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
aynı olduğunun garantisi yok) ya da ebeveynden pickle ile almalıdır (yüzlerce MB; ebeveynde GIL'i saniyelerce tutan tek
bir C çağrısı ve iş boyunca tam ikinci kopya). `fork` aynı bellek görüntüsünü bedelsiz verir; kanıtın bit-aynı olmasının
gerekçesi de budur: aynı motor nesnesi, aynı fonksiyon (`TradingEngineV3._evidence_query`), aynı girdiler.

Çok iş parçacıklı süreçte `fork` riskine karşı önlemler:

- Alt süreç yalnız saf hesap yapar: loglamaz, uyarı basmaz, stdout/stderr `/dev/null`'a yönlenir — `fork` anında başka
  bir iş parçacığının tuttuğu akış/log kilidine dokunmaz.
- Hazır el sıkışması 30 sn (`START_TIMEOUT_S`). İstek sürerken alt süreç 60 sn **hiç CPU harcamazsa**
  (`STALL_TIMEOUT_S`; kilitlenen süreç CPU harcamaz, CPU kotasıyla kısılmış ama çalışan süreç harcar) öldürülür; mutlak
  üst sınır 1.800 sn (`REQUEST_TIMEOUT_S`). Ölüm/OOM boru kapanışıyla anında görülür.
- `PR_SET_PDEATHSIG=SIGKILL`: worker ölürse alt süreç de ölür (devraldığı tekil kilit tanımlayıcısıyla ortada kalıp
  yeniden başlatılan worker'ı bekletemez).
- `nice +10` (worker `Nice=5` → 15) ve `oom_score_adj=1000` (bellek baskısında önce alt süreç ölür, worker değil).
- Her arıza (fork yok, başlamama, ölüm, asılma, protokol) loglanır ve o işin kalanı bugünkü süreç içi yolla hesaplanır;
  tur o sembolü bugünkü gibi kendisi hesaplar. Alt süreçte bir sembolün hesabı istisna verirse tur o sembolü süreç
  içi yeniden hesaplar (bugünkü istisna ya da sonuç birebir); ön ısıtma yalnız loglar (bugünkü gibi).

## 4. Neden indeks kurulumu süreçte kaldı

Kurulumu alt sürece taşımak motoru ebeveyne geri taşımayı gerektirir (≈150k olay pickle'ı: ebeveynde saniyelerce GIL,
geçici yüzlerce MB ve iş boyunca ikinci tam kopya) ve yayım anını kaydırırdı. Yayım noktası, iş parçacığı ve kodu
**değişmedi**. Kurulumun çekişmesi ön ısıtmanınkinin küçük bir kısmıdır (§2.4); kalan risk §9'da.

## 5. Karar-nötrlük

| Değişmez | Nasıl korunuyor | Test |
|---|---|---|
| Yayım anı | yenileyici kodu değişmedi; `fork` ön ısıtma işçisinde, yenileyici iş parçacığında değil; yavaş `fork` yayımı geciktirmez | `test_the_publish_moment_and_version_visibility_are_unchanged` |
| Turun gördüğü sürüm | motor ve sürüm tek paket okumasından (değişmedi); alt süreç yalnız kendi motorunun sorgusunu yapar | `test_a_newer_publish_closes_the_old_child_and_never_serves_the_old_version` |
| Anahtar | `(sembol, sürüm, son bar)` değişmedi | aynı |
| Kanıt değeri | aynı motor/fonksiyon/girdi; pickle float'ı kayıpsız taşır; serileştirilmiş `==` ve nesne `==` | `test_child_evidence_is_bit_identical_to_in_process_and_files_match` |
| Karar kimliği (`config_hash`, karar günlüğü/provenans satırlarına yazılır) | yeni anahtar iki değerinde de karar-nötr → özetten çıkarılır (ortak deneyim bölümüyle aynı kural); özet bu alandan önceki kodla aynı | `test_the_switch_is_not_part_of_the_decision_identity`, `test_learning_record_only_tours.py` (`943345c` ile bayt bayt) |
| Bütün durum dosyaları | gerçek `tour()` × 5, iki yayım, alt süreç AÇIK / süreç içi / ön ısıtma KAPALI → `state/` bayt bayt aynı | `test_tour_perf_no_decision_change_v1.py` (3 karşılaştırma) |

Saat-duvarı zamanlaması elbette değişir (amaç budur): tur coin head'e daha erken ulaşır. Bu, her hızlandırmada olduğu
gibi bir turun yayımdan önce mi sonra mı okuduğunu değiştirebilir; okuma KURALI (o anki yayımlanmış paket, tek okuma)
aynıdır ve hiçbir yayım bilerek ertelenmez ya da öne alınmaz.

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
yeni indeks, ~4,5 GB) alt süreç yaşarken oluşmaz: alt süreç yayımdan SONRA kurulur ve bir sonraki kurulum en erken bir
sonraki yenileme döngüsündedir. Çakışırsa bile ≈4,5 + 0,35 GB < 6 GB `MemoryMax`; bellek baskısında `oom_score_adj=1000`
alt süreci öldürür, worker süreç içi yola düşer. Not: `health.json` `hwm_mb` (VmHWM) yalnız worker sürecini ölçer; alt
süreci içermez. Cgroup toplamı için `systemctl show tradingbot-worker -p MemoryPeak` (ya da `MemoryCurrent`) okunmalı.

## 7. CPU

Depodaki birim `CPUQuota=150%` der (VPS'te farklı olabilir; VPS'in çekirdek sayısını bilmiyorum). Alt süreç ve tur
aynı cgroup'tadır: ikisi de CPU'ya doymuşken toplam 1,5 çekirdeğe kısılırlar, yani tur CPU'ya bağlı adımlarında en kötü
~0,75 çekirdek alır (≈1,3×). Alt sürecin `nice +10`'u tek çekirdekte turu öne alır; kotada yalnız hız etkilenir, sonuç
değil.

GIL açlığının şiddeti çekirdek sayısına bağlıdır: bekleyen iş parçacığı başka bir çekirdekte uyanıp yarışı kaybettikçe
büyür. Aynı betik (`--bars 1000`, 13.936 olay) `taskset` ile kısıtlanarak (arka planda başka bir test koşarken; gösterge
niteliğinde):

| Çekirdek | Süreç içi ön ısıtma (py / numpy / net / file) | Alt süreç (py / numpy / net / file) |
|---|---|---|
| 1 | 1,5× / 1,9× / 1,8× / 3,0× | 1,0× / 1,3× / 1,4× / 1,2× |
| 2 | 5,4× / 2,8× / 2,1× / 2,5× (uyanma gecikmesi en çok 382 ms) | 0,7× / 1,0× / 0,5× / 1,1× |
| 4 (§2.2) | 9,5× / 94× / 33× / 145× | 1,1× / 0,8× / 0,8× / 1,1× |

VPS'teki 5× (semboller) ve 33× (yürütme) bu aralığın içindedir. Alt süreç yolu her üç durumda da ~1×'tir.

## 8. Geri dönüş anahtarı

```yaml
history:
  evidence_subprocess: false   # varsayılan true
```

`false` iken `request_prewarm(..., use_child=False)` çağrılır ve tur ıskası doğrudan `_evidence_query` ile hesaplanır —
2026-10-01'den beri koşan süreç içi yolun kendisi; alt süreç hiç kurulmaz (test: `test_kill_switch_off_never_forks...`,
`test_tours_are_identical_with_the_in_process_prewarm_kill_switch`). Anahtar config.yaml'a yazılmadı (varsayılan
dataclass'tadır); kapatmak için yukarıdaki satır `history:` bölümüne eklenip worker yeniden başlatılır.

## 9. VPS'te doğrulanacaklar ve kalanlar

Dağıtım sonrası bakılacaklar (hiçbiri burada VPS'te ölçülmedi):

1. 4h kapanışlarından sonraki turlar: `tur fazları` satırında semboller/yürütme olağan düzeyde mi (~108 / ~26 sn).
2. Günlükte her yayımdan sonra `pattern kanıtı ön ısıtıldı: sürüm N, ... (alt süreç pid P, özel bellek M MB)`;
   `alt süreci arızalandı` / `başlatılamadı` YOK.
3. Box `missed_bars` artmıyor, `lag_max_s` olağan.
4. Cgroup bellek tepesi (`MemoryPeak`) ve alt sürecin özel belleği (log satırındaki M) — tahmin ≈0,35 GB.
5. Yeniden başlatma sonrası ilk tur: ilk kurulum hâlâ süreç içidir (§4); ön ısıtmanın çekişmesi gider ama ilk tur
   olağandan uzun kalabilir.

Bilinçli olarak yapılmayanlar / öneriler (ayrı iş):

- Sorgu döngüsünü vektörleştirmek (olay başına Python + `np.corrcoef` yerine toplu numpy) 12,6 sn'lik sorguyu büyük
  ölçüde kısaltır ve GIL sorununu kökten azaltır; ama kanıtın bit-aynı kaldığı ayrıca kanıtlanmalıdır (komşu sıralamasında
  eşitlik bozma, kayan nokta toplama sırası). Karar riski taşıdığı için bu işin kapsamı dışında bırakıldı.
- İndeks kurulumu (`add_series`, satır başına `feats.iloc[i]`) da aynı şekilde hızlandırılabilir; yayım anını öne
  çekeceği için sahip onayı gerektirir.
