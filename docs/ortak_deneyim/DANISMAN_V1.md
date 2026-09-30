# Gölge Danışman `advisor_v1`: ön kayıt (2026-09-29)

**Durum: ÖN KAYIT.** Bu belge, danışmanın gerçek veride tek bir çıktısı üretilmeden ÖNCE yazıldı ve öyle kaydedilir.

- Kuralın sabitleri gerçek sonuçlara bakılarak seçilmedi. Hepsi KARARLAR.md 4-5'ten (n ≥ 30, küme ≥ 5, k = 20, geri çekilme sırası) ve görev tanımından geldi.
- Bu belge ile mühendislik tasarımı çelişirse **bu belge geçerlidir** (KARARLAR.md'nin SPEC_V1'e üstünlüğü gibi).
- Belge koddan ÖNCE yazıldı; uygulama (2026-09-30) bu belgeye göre yazıldı ve yalnız sentetik depolarda / çevrimdışı düzenekte çalıştırıldı — gerçek veride henüz tek bir tavsiye yok.

**Kullanıcının hedefi (aynen):** "bütün verileri tek bir yere kaydedelim ki, botlarımız bu taktiklere bakarak evet böyle yapmışım ve kar getirmiş diyip devam etsin veya tam tersi".

**Danışman ne yapar?** Her gerçek giriş ve her "olsaydı" (karşı-olgusal) sinyali için şu soruyu o ana ait olarak cevaplar ve kaydeder: *ortak hafıza bu anda ne derdi?*

- Cevap dört etiketten biridir: **GİR**, **GİRME**, **NÖTR**, **VERİ AZ**.
- Cevap yalnız sinyalden ÖNCE kesinleşmiş sonuçlardan hesaplanır.
- Haftalar sonra önceden yazılmış kurallarla şu sorulur: "GİRME dediklerini atlasaydık net R artar mıydı?"

**Danışman ne yapmaz?** Hiçbir kararı, boyutu, kapıyı, öğreniciyi, defteri DEĞİŞTİRMEZ. Bu aşamada yalnız KAYIT vardır.

---

## 1. Değişmez kurallar

1. **Yalnız KAYIT.** Danışman yalnız `state/shared_experience/advice/**` altına yazar.
   - Ortak deneyim deposu (`experience.jsonl`, arşiv), `cursor.json` ve bütün karar dosyaları danışman AÇIK da KAPALI da olsa bayt bayt aynıdır.
2. **Tüketici yok.** Karar yollarındaki hiçbir modül danışmanı ya da tavsiye dosyalarını okumaz. AST testiyle korunur. Kapsanan modüller:
   - motor ve `_prepared_experience_pool`;
   - `learn/*`, `learning_mode`, `learning_basis`, `learning_cf`;
   - `strategy_paper`, `pattern_trader`, `box_timer`.
3. **Yalnız PAPER.** Canlı para hiçbir aşamada kapsamda değildir.
4. **Mod yalnız OFF | RECORD.**
   - ADVISE / ENFORCE diye bir mod YOKTUR; yazılırsa yapılandırma hatası verir.
   - **GİR hiçbir aşamada girişi zorlamaz ve boyutu büyütmez.**

---

## 2. Tanımlar

### 2.1 Hedef (tavsiye alan sinyal)

| Hedef | Satır | Anahtar | Karar anı (`as_of`) |
|---|---|---|---|
| Gerçek giriş | `xp_entry` | `trade_key` | `opened_at` |
| Olsaydı sinyali | `xp_cf`, rev 0 | `cf_key` | `created_at` |

- Anahtar başına YALNIZ İLK görülen satır hedeftir; tekrarlar yok sayılır.
- **Durum boyutları** hedefin kendi `situation_v1` anlık görüntüsündendir: trend, oynaklık, BTC, hacim, yapı (`situation.bucket`).
  - Anlık görüntü OK değilse, ya da şema/mühür `situation_v1`/`SCHEMA_SHA` değilse, yalnız durumsuz seviyeler (S5, F5) kullanılır.

### 2.2 Kanıt: iki AYRI kanal

| Kanal | Kanıtın değeri | Durum koşulu |
|---|---|---|
| **gerçek** (birincil) | kapanmış gerçek işlemin **İLK kesin (`final`) net** sonuç revizyonundaki `r_net` | girişin anlık görüntüsü OK |
| **olsaydı** | karşı-olgusalın **İLK** `LABELLED` + `r_basis = NET` + `label_version = cf_label_v3` revizyonundaki `r_net` | rev-0 anlık görüntüsü OK |

**Olsaydı kanalının ayrıntıları:**
- `cf_label_v1`, `v1c`, `v2`, sürümsüz ya da bilinmeyen etiketler KANIT DEĞİLDİR.
- Değiştirildi / süresi doldu / düşürüldü / kayboldu durumundaki kayıtlar KANIT DEĞİLDİR.
- A15, `approx` ve bar içi belirsiz etiketler dahildir ve sayılır.

**Anahtar başına ilk nitelikli revizyon geçerlidir; sonraki revizyonlar yok sayılır.**

**Kökenler.** Bütün kökenler (canlı ve geri doldurma) ve bütün kohortlar (politika, öğrenme-ekstra, etiketsiz, öğrenme öncesi) kanıta girer. R boyuttan bağımsızdır.

**Olsaydı kanıtının ağırlığı: birincil tavsiyede SIFIR.** Gerekçeler:

1. **Hacim tavsiyeyi ele geçirir.** Günde ~1.170 Box karşı-olgusalına karşılık bütün defterlerde ~150–200 gerçek giriş vardır, oran ~8:1. Ağırlık 0,25 bile olsa etkin örneklemin ~2/3'ü olsaydı olur.
2. **Bilinen iyimserlik kalıntısı.** v3 maliyet farkını kapattı. Ancak iki iyimserlik kaynağı kalıyor ve büyüklüğü ölçülmedi:
   - seviyeden dolum iki 60 sn fiyat arasındaki aşmayı görmez;
   - bar içi belirsiz barlar "sürer" dalını alır.
3. **Seçilim.** Olsaydı yalnız belirli ret nedenlerinde (kapasite, doluluk, kapı…) oluşur.
4. **İlke.** KARARLAR ve rapor gerçek ile olsaydıyı tek ortalamada birleştirmez.

Olsaydı kanalı AYNI kurallarla ayrıca hesaplanır ve kaydedilir (`advice_cf`). Olsaydı kanıtının ileride kabul edilip edilmeyeceğini §5.6'daki **H2** testi söyler.

### 2.3 Kanıtın görünürlük saati (nedensellik)

```
görünür(e) = max( yazım anı(e'nin değer satırı), yazım anı(e'nin bağlam satırı), olay anı(e) )
yazım anı : satırı yazan toplu yazımın MONOTON saati = max(önceki toplu yazım saati, recorded_at)
olay anı  : gerçek → closed_at · olsaydı → labeled_at            (label_ts ASLA kullanılmaz)
e, hedef s için kanıttır  ⇔  görünür(e) < as_of(s)   (KESİN küçük; aynı saniye dışarıda)
```

- **Yazım anı** = satırı yazan toplu yazımın monoton saati. `recorded_at` turun BAŞLANGIÇ saatidir; saat hiçbir zaman geriye gitmez.
  - Tur içinde olan bir etiket, yazım anından sonra gerçekleşmiş olabilir. Bu yüzden olay anı da alınır.
  - Tembel v1 → v3 net yeniden etiketi, `labeled_at` değişmeden sonradan yazılır. Bu yüzden yazım anı da alınır.
- **Duvar saati geri adımı** (yeniden başlatma, NTP; 2026-09-30 inceleme bulgusu). Saat geri atarsa o sırada yazılan satırların `recorded_at`'i ve olay saatleri geriye damgalanır. Satırın kendi `recorded_at`'i kullanılsaydı, adımdan ÖNCE açılmış ve saat düzeldikten sonra yazılan bir hedef, kendisinden SONRA kapanmış bir sonucu görebilirdi (sonuç sızıntısı; o hedef anomali bayrağı taşımadığı için H1'e uygun olurdu).
  - Bu yüzden yazım anı satırın kendi `recorded_at`'i DEĞİL, toplu yazımın monoton saatidir.
  - **Saati ilerletmeyen** bir toplu yazımın (`recorded_at` ≤ önceki saat ya da okunamaz) gerçek yazım anı bilinmez; yalnız öncekinden sonra olduğu bilinir. Orada katlanan kanıt BEKLER ve saati **ilerleten** sonraki toplu yazımın başında, `görünür = max(görünür, o toplu yazımın saati)` ile hücrelere girer. (Yalnız monoton saat yetmezdi: son normal toplu yazım ile adım arasında açılmış bir hedef yine sızıntı görürdü.)
  - Saati ilerletmeyen toplu yazımın kendi hedefleri `clock_anomaly` ile işaretlenir ve değerlendirmeden çıkar (§4.1).
- **`label_ts` neden kullanılmaz?** `label_ts` ufkun planlanan sonudur. Stop/hedefle erken biten etiket ondan ÖNCE, ufuk etiketi ondan SONRA bilinir.
- **Saat dönüşümü.** Bütün saatler ISO metinden tam sayı milisaniyeye çevrilir. Saniyeye kırpılmış saatlerin kesin karşılaştırması sonraki bir olayı asla içeri almaz.
- **Saat anomalisi.** `closed_at < opened_at` ya da `labeled_at < created_at` olan kayıt dışarıda kalır ve sayılır.

### 2.4 Katlama (canlı = çevrimdışı)

Danışman, ortak deneyim deposunun **yazım sırası** üzerinde deterministik bir katlamadır.

- **Toplu yazım.** Bir toplu yazım, aynı `recorded_at`'li ardışık satırlardır; bu, toplayıcının bir adımına eşittir.
- **Aynı kod.** Canlı toplayıcı her toplu yazımı katlar; çevrimdışı CLI aynı kodla arşiv + sıcak dosyayı katlar.
- **Aynı sonuç.** Aynı hedef için tavsiyenin çekirdeği (`core_sha`) iki yolda BİREBİR aynıdır.

**72 saat kuralı.** Toplu yazım saatinden 72 saatten eski `as_of`'lu hedef **GEÇ (LATE)** olur ve tavsiye almaz. Bu, geri doldurma hedeflerini ve patolojik taslakları kapsar.

---

## 3. Kural

### 3.1 Seviyeler (önceden kayıtlı; raporun sırasıyla AYNI)

| Seviye | Grup | Tutulan boyutlar |
|---|---|---|
| S0 | kurulum `setup_key` (defter\|setup_type\|yön) | trend · oynaklık · BTC · hacim · yapı |
| S1 | kurulum | − yapı |
| S2 | kurulum | − yapı, hacim |
| S3 | kurulum | − yapı, hacim, BTC |
| S4 | kurulum | yalnız trend |
| S5 | kurulum | boyutsuz (kurulumun bütün durumları) |
| F0 … F5 | aile `family_key` (aile\|yön; defterler arası) | aynı sıra |

- **Birleşmeyenler.** TREND / FADE / BREAKOUT / CANDLE_PATTERN / MOMENTUM aileleri ASLA birleşmez; yönler ASLA birleşmez.
- **Aile düzeyinin olmadığı durum.** Ailesi olmayan kurulumda F seviyesi yoktur: C4S ve eşlenmeyen kurulumlar.
- **Önce kendi kurulum.** Kendi kurulumun bütün seviyeleri, başka botların deneyiminden (aile) ÖNCE gelir.

### 3.2 Cevap seviyesi ve karar

- **Yeterli seviye:** n ≥ **30** ve en az **5** farklı gün-kümesi.
  - Küme, kanıtın kendi `as_of`'unun UTC günüdür; bütün coinler birlikte sayılır.
- **Cevap seviyesi = sıradaki İLK yeterli seviye.**
  - Daha sonraki bir seviyede "daha kesin" cevap ARANMAZ; bu, anlamlılık avcılığını önler.
- **Aralık.** Ortalama net R için gün-kümeli %95 aralık kullanılır: CR1 küme-sağlam standart hata, Student t (C−1 serbestlik).
  - Bütün toplamlar tam sayı mikro-R'dir, sonuç sırasından bağımsızdır.
  - t tablosu tasarımdadır; ara serbestlikte bir alttaki (daha büyük) t alınır.

| Etiket | Koşul |
|---|---|
| **GİRME** | aralığın üst ucu **< 0** |
| **GİR** | aralığın alt ucu **> 0** |
| **NÖTR** | yeterli seviye var ama aralık 0'ı kapsıyor |
| **VERİ AZ** | hiçbir seviye yeterli değil |

**Birincil tavsiye = gerçek kanalın etiketi.** Olsaydı kanalının etiketi ayrı alandadır; birleşik etiket YOKTUR.

### 3.3 Coin başına kısmi havuz (yalnız bilgi)

Cevap seviyesinde hedefin coin'i için:

```
büzülmüş = (n_coin · ort_coin + 20 · ort_havuz) / (n_coin + 20)
```

Bu değer kaydedilir. `dissent` işareti: n_coin ≥ 10 ve büzülmüş ile havuz ortalamasının işareti farklı.

**Etiketi DEĞİŞTİRMEZ.** Coin düzeyinde karar ikinci bir test (çoklu karşılaştırma) ve coinler arası varyans modeli gerektirir. Coin bilgisinin katkısı keşif analizi **E1** ile ölçülür.

### 3.4 Kayıt

Her hedef için ayrı depoya **bir** satır yazılır:

- **Konum ve şema:** `state/shared_experience/advice/advice.jsonl`, şema `shared_experience_advice_v1`, tür `xp_advice`.
- **Hedef:** kimliği, kurulumu, ailesi, coin'i, kohortu, `as_of`, boyutları.
- **Her kanal için:** etiket, cevap seviyesi, n, küme sayısı, ortalama, aralık, standart hata, t, coin ve büzülmüş değerler, köken karışımı, denenen bütün seviyeler.
- **Genel alanlar:**
  - `evidence_cutoff_ms` (= `as_of`; kural: görünür < kesim);
  - `advisor_sha`, `situation_schema_sha`, `core_sha`;
  - `effect: "NONE"`.
- **Ana depoya dokunulmaz.** Ortak deneyim satır şeması (`shared_experience_row_v1`, `KINDS`) DEĞİŞMEZ.

---

## 4. Değerlendirme (kanıt ölçütü)

### 4.1 Uygun hedef

Aşağıdakilerin hepsi sağlanmalıdır:

- Köken CANLI.
- **İleriye dönük:** hedefin toplu yazımı danışmanın canlı doğumundan (`advisor_born_ms`) sonra.
  - `advisor_born_ms`, ilk yetişmeden sonra canlı katlanan ilk toplu yazımın saatidir.
- Durum OK; saat anomalisi yok.
- **Kendi sonucu kesinleşmiş:** gerçek için ilk kesin net revizyon, olsaydı için ilk `cf_label_v3` net etiket.

Sonuç `y` = o `r_net`.

### 4.2 Birincil hipotez H1 ve ölçüler

**H1:** gerçek kanal tavsiyesi, gerçek hedeflerde (bütün gerçek kohortlar birlikte). T = uygun hedefler, G = GİRME denenler.

| Ölçü | Tanım | Rol |
|---|---|---|
| **U_ort** | `ort(T \ G) − ort(T)` | **BİRİNCİL** — GİRME'yi atlamanın işlem başı net R kazancı. Tavsiyedeki BİLGİYİ ölçer. `T \ G` boşsa `−ort(T)`. |
| U_top100 | `−Σ_G y / |T| · 100` | koruma — 100 sinyal başına kazanılan net R. Strateji bütünüyle zarardaysa da pozitif çıkar; bu yüzden birincil değildir. |
| Δ | `ort(GİR) − ort(GİRME)` | koruma — ayırt edicilik |

- **Ayrıca raporlanır:** her etiketin n'i ve ortalaması.
- **Kırılımlar:** defter, aile, POLİTİKA / ÖĞRENME-EKSTRA, olsaydı hedefleri, ve durumsal seviye (S0–S4, F0–F4) ile temel seviye (S5, F5) ayrımı.
- **Temel seviye uyarısı.** GİRME'lerin %80'inden fazlası S5/F5'ten geliyorsa rapor bunu açıkça yazar: kazanç durumdan değil kurulum düzeyinden (karne bilgisi) geliyordur.

**Güven aralığı:** gün-kümeli bootstrap.

- Küme, hedefin `as_of`'unun UTC günüdür; kümeler artan gün sırasıyla alınır.
- B = **10.000**, tohum **20261001**, numpy PCG64.
- Yüzdelik sınırlar: `sıralı[⌊α/2·B⌋]` ve `sıralı[⌊(1−α/2)·B⌋]`.

### 4.3 Bakışlar (ara bakış kanıt değildir)

| Bakış | Tarih | α (aralık) |
|---|---|---|
| **L1** | `advisor_born + 28 gün`den sonraki, **asgariler** sağlanan ilk UTC gece yarısı | 0,01 (%99) |
| **L2** | L1 + 28 gün | 0,01 (%99) |
| **L3** | L1 + 56 gün | 0,03 (%97) |

**Asgariler** sonuçlara bakmaz, yalnız sayar:
- en az **500** uygun gerçek hedef;
- en az **50** GİRME, en az **10** farklı günde — GİRME **değerlendirilen kanalın** etiketlerinden sayılır (H1: birincil `advice`, H2: `advice_cf`);
- en az **28** farklı hedef günü.

**Bakış tarihleri** birincil kanaldan (H1) bir kez bulunur; H2 AYNI tarihlerde değerlendirilir. H2'nin asgarileri o tarihlerde sağlanmıyorsa o bakışta H2 KALIR (2026-09-30 inceleme bulgusu: H2 eskiden birincil kanalın GİRME'lerini sayıyordu; birkaç olsaydı-GİRME'siyle geçebilirdi).

**Bakış kuralları:**
- Toplam α (Bonferroni) 0,05'tir.
- Her bakış, `as_of < kesim` olan ve sonucu `kesim + 7 gün`e kadar kesinleşen hedefleri içerir; o gün hesaplanır.
- L1 `advisor_born + 16 hafta`ya kadar oluşmazsa sonuç **ULAŞILAMADI**'dır.
- Bakışlar arasındaki bütün sayılar (panel, CLI) yalnız tanımlayıcıdır.

### 4.4 Başarı ölçütü (yalnız bir ÖNERİ yazmaya izin verir)

Lk bakışında H1 için aşağıdakilerin **HEPSİ** sağlanırsa sonuç **GEÇTİ(Lk)** olur:

1. Asgariler o bakışın verisinde sağlanıyor.
2. U_ort'un (1−α_k) aralığının **alt ucu > 0**.
3. **İki yarıda kararlılık.** Hedef günleri, birikimli hedef sayısının %50'ye ulaştığı ilk günde ikiye bölünür. U_ort nokta tahmini **iki yarıda da > 0**.
4. **Korumalar.** U_top100 > 0 ve Δ > 0 (nokta tahmin).
5. **Bütünlük** (bakışın KENDİ penceresinde; 2026-09-30):
   - uygun satırlarda canlı ile çevrimdışı `core_sha` uyumu ≥ %99,5 ve açıklanamayan uyumsuzluk sıfır;
     - pencere: `as_of < kesim` olan ve `kesim + 7 gün`e kadar tavsiye edilmiş uygun hedefler, tavsiye deposunda `kesim + 7 gün`e kadar yazılmış İLK kopyalarıyla karşılaştırılır;
     - böylece bir bakışın hükmü sonradan yeniden çalıştırılınca DEĞİŞMEZ (ör. L1 + 7 günden sonraki bir kesinti L1'i geriye dönük bozmaz);
   - pencere boyunca her sürüm `--check`'inde "karar değişmedi" değişmezleri yeşil.
     - Operatör bunu açıkça beyan eder (`--invariants-green`). Beyan yoksa bütünlük BİLİNMİYOR sayılır: istatistiği geçen bakış **BEKLİYOR** (`INVARIANTS_UNDECLARED`) olur, KALDI olmaz. Yalnız açık kırmızı beyan, uyum < %99,5 ya da açıklanamayan uyumsuzluk bu koşulu bozar.

İlk geçen bakış testi bitirir. L3'e kadar geçen yoksa sonuç **KALDI**. Test kapanır; kayıt sürebilir ama yeni kural `advisor_v2`'dir ve yeni ön kayıt ile yeni pencere ister.

### 4.5 Geçerse ne olur, ne olmaz

**GEÇTİ**, yalnız PAPER'da "GİRME'yi atla" için bir **ÖNERİ** yazılmasına izin verir. Öneri:
- defter başına U_ort'u ve GİRME sayısını listeler;
- 20'den az GİRME'si olan ya da defter başı U_ort nokta tahmini ≤ 0 olan defterleri kapsam dışı bırakır;
- durumsal ve temel seviye ayrımını belirtir. Kazanç temel seviyedense, konu kullanıcıya **defter/strateji kararı** olarak sunulur.

**Geçmek neyi sağlamaz:**
- Uygulama yeni bir mod, ayrı testler, sürüm ve **kullanıcının açık onayı** olmadan YAPILMAZ.
- GİR hiçbir zaman girişi zorlamaz ve boyutu büyütmez.
- **O güne kadar HİÇBİR ŞEY DEĞİŞMEZ.**

### 4.6 İkincil ve keşif analizleri (karar değildir)

- **H2 (önceden kayıtlı ikincil).** Olsaydı kanalının tavsiyesi GERÇEK hedeflerde test edilir; bakış tarihleri (H1'inkiler), α ve ölçüt aynıdır. Asgarilerdeki GİRME sayıları olsaydı kanalının kendi etiketlerindendir (§4.3).
  - GEÇERSE olsaydı deneyimi gerçek sonucu öngörüyor demektir.
  - Bu, olsaydı kanıtını kabul eden bir `advisor_v2` önerisine izin verir; ağırlık yeni ön kayıtta belirlenir.
- **E1.** Coin büzülmesinin kalıntı sonucu öngörüp öngörmediği.
- **E2.** Olsaydı kanıtından `approx` / bar içi belirsiz / A15 çıkarıldığında duyarlılık.
- **E3.** Kanıt yalnız CANLI kökenle sınırlandığında: f9a61dd öncesi muhasebeyle kapanmış geri doldurma işlemleri dışarıda kalır.
- **E4.** Olay saatli **geriye dönük** mod (`--clock event`), geri doldurma aylarına erken bakmak içindir.
  - "RETRO — keşif; kanıt değil" şeridiyle basılır.
  - Kuralı DEĞİŞTİRMEK için KULLANILAMAZ.

---

## 5. Değişiklik prosedürü

- **Mühür.** Kuralın bütün sabitleri `ADVISOR_SPEC`'tedir ve `ADVISOR_SHA` ile mühürlenir (`situation_v1`'in `SCHEMA_SHA`'sı gibi). Mühür `situation_v1`'in `SCHEMA_SHA`'sını da içerir.
- **Değerlendirme protokolü.** §4'teki kurallar `WF_SPEC`'tedir ve `WF_SHA` ile ayrıca mühürlenir.
- **Herhangi bir değişiklik** `advisor_v2` demektir:
  - yeni SHA ve yeni satır kimlikleri;
  - yeni ön kayıt;
  - v2'nin canlı doğumundan başlayan YENİ değerlendirme penceresi.
- v1 satırları yeniden yazılmaz.
- **İşletim ayarları** (bütçe, anlık görüntü sıklığı, depo boyutu) mühürde değildir. Tavsiyenin NE olduğunu değil, NE ZAMAN hesaplandığını değiştirirler.
  - **Ölçüm (2026-09-30, 200 bin satırlık üretim boyutlu depo).** İlk açılışta danışman depoyu baştan katlar (yetişme). Hız ~2,1 satır/ms: `advisor_catch_up_budget_ms = 1000` ile ~95 tur (15 dk turda ~1 gün); 250 ms ile ~310 tur (~3 gün) sürerdi. Doğum (`advisor_born_ms`) ancak yetişme bitince kurulur; ilk sürüm denetimi yetişme (`mode: catch_up`, azalan `lag_rows`) görür.
  - Okunamayan ana depo segmenti atlanmaz: danışman `WAITING_SEGMENTS` durumunda bekler. Tavsiye deposu yazılamıyorsa durum `RECORD_BLOCKED` olur. İkisi de kararı ETKİLEMEZ.

---

## 6. Altın örnekler (testte sabit)

5 gün × 6 değer (R), t(4) = 2,776445. Değerler noktalı virgülle ayrılmıştır (ondalık ayırıcı virgül):

| Örnek | Gün 1 · Gün 2 · Gün 3 · Gün 4 · Gün 5 | Ort. | Aralık | Etiket |
|---|---|---|---|---|
| A | [−1; −1; −1; −1; 0,5; −0,2] · [−1; −1; −1; 2; −1; −0,4] · [−1; −1; −1; −1; −1; 1,5] · [−1; 0,8; −1; −1; −0,3; −1] · [−1 ×6] | −0,636667 | [−0,910193 ; −0,363140] | GİRME |
| B | [−1; 2,5; −1; 0,4; 1,2; −1] · [1,8; −1; −1; 0,3; 2,2; −1] · [−1; −1; 3; 0,1; −0,5; 1] · [0,6; −1; −1; 1,4; 2; −0,2] · [−1; 1,1; 0,9; −1; −1; 0,7] | 0,183333 | [0,012057 ; 0,354610] | GİR |

**Sınır örnekleri:**
- B'de 5. gün 4 değere inerse (n = 28): **VERİ AZ**.
- A'da 5. günün değerleri 4. güne taşınırsa (küme = 4): **VERİ AZ**.

**Değerlendirme oyuncağı:** 4 gün, 10 gerçek hedef.

| Gün | Hedefler (etiket, y) |
|---|---|
| 1 | (GİR, +1) · (NÖTR, −0,5) · (GİRME, −1) |
| 2 | (GİR, −1) · (GİRME, −1) · (VERİ AZ, +0,5) |
| 3 | (NÖTR, +2) · (GİRME, +0,5) |
| 4 | (GİR, +0,5) · (GİRME, −1) |

| Ölçü | Değer |
|---|---|
| ort(T) | 0 |
| ort(GİRME) | −0,625 |
| **U_ort** | **0,416667** |
| U_top100 | 25 |
| Δ | 0,791667 |
| Yarılar (2. günden sonra bölünür) | U_ort 0,333333 ve 0,75 → ikisi de > 0 |

**Coin büzülmesi:** (n_coin 10, ort −1, havuz 0,2) → −0,2.

---

## 7. Mühür (uygulama bitince, canlıya almadan ÖNCE doldurulur)

| Alan | Değer |
|---|---|
| `ADVISOR_SHA` | `8a89fd7e69a2d33b` |
| `WF_SHA` | `b34d6b7a313d24d1` |
| `situation_v1 SCHEMA_SHA` | `640fd10e5d6f727c` |
| Mühür commit'i | _(bu belgeyi ve uygulamayı içeren commit; canlıya almadan önce)_ |
| `advisor_born_ms` | _(ilk canlı toplu yazımda; `advice/advisor_meta.json`)_ |

**Mühürden önceki değişiklikler (2026-09-30, inceleme; henüz canlı veri, mühür commit'i ve dağıtım YOK — bu yüzden hâlâ v1):**
- `ADVISOR_SHA` `b4bcba95dcc6a2ef` → `8a89fd7e69a2d33b`: yazım anı = toplu yazımın monoton saati; saati ilerletmeyen toplu yazımda katlanan kanıt, saati ilerleten sonrakine kadar bekler (§2.3). Monoton saatli depolarda tavsiye BİREBİR aynıdır.
- `WF_SHA` `546a804d1ecd3f2c` → `b34d6b7a313d24d1`: H2 asgarileri kendi kanalından, bakış tarihleri H1'den (§4.3); bütünlük bakışın kendi penceresinde; beyan edilmemiş değişmezler → BEKLİYOR (§4.4).
