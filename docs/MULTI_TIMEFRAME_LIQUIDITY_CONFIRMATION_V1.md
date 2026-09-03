# MULTI_TIMEFRAME_LIQUIDITY_CONFIRMATION_V1 (H ailesi)

**Durum:** SHADOW · `applied = 0` · `auto_promotion = false` · terfi bugün **imkânsız**.

H, giriş challenger ailelerinin sekizincisidir (A–E: olasılık/rejim/dağılım/likidite/ısı;
F–G: haftalık yapı ve yapısal R:R). Mevcut sistemlerin hiçbirini yeniden yazmaz; onların
yanına **tamamen izole** bir gözlem katmanı ekler.

---

## 1. Kaynak hipotez ve neden kanıt değildir

Hipotez bir eğitim videosundan alınmıştır:

> Üst zaman dilimi bağlamı, likidite seviyesini ve hedefi verir; alt zaman dilimi girişi
> teyit eder. `D → H1`, `H4 → M15`, `H1 → M5`, `M15 → M1`.

**Video kârlılık kanıtı DEĞİLDİR.** Nedenleri, sistemin geri kalanında uygulanan kanıt
çıtasıyla aynıdır:

* Seçilmiş örneklerdir; kaybeden örneklerin dağılımı görünmez (survivorship).
* Kavramlar (“likidite”, “sweep”, “displacement”, “order block”) görsel ve **takdire
  dayalıdır**; aynı grafiği iki kişi farklı etiketler.
* Maliyet (komisyon, funding, kayma) hiçbir görsel iddiada düşülmemiştir.
* Örneklem sayısı, güven aralığı, örneklem-dışı doğrulama ve rejim kapsamı yoktur.
* Grafik üzerinde geriye dönük bakınca her süpürme “belliydi”; **karar anında** belli
  değildi.

Bu yüzden H burada yalnız **yanlışlanabilir bir araştırma hipotezi** olarak ele alınır. Her
kavramın deterministik, karar anında hesaplanabilir ve tekrarlanabilir bir tanımı vardır
(§3). Hiçbir eşik, sonuçlara bakılarak ayarlanmamıştır.

---

## 2. Güvenlik sözleşmesi

H **hiçbir koşulda**:

| Yasak | Koruma |
| --- | --- |
| Aktif giriş kabulünü değiştirmek | `applied` daima `False`; karar yalnız snapshot'a yazılır |
| Sıralamayı / coin-head çıktısını değiştirmek | H, sıralama sonrası saf bir gözlemcidir |
| Yön / miktar / kaldıraç / stop / TP değiştirmek | `structural_*` alanları **yalnız araştırma** |
| RiskEngine sonucunu değiştirmek | modül RiskEngine'i ithal bile etmez (test 38) |
| Deftere yazmak | dosya/IO çağrısı yok (test 37) |
| Gateway'e dokunmak | gateway/ccxt/borsa adı kodda yok (test 36) |
| Pozisyon açmak/kapatmak/değiştirmek | emir yolu ile bağlantısı yok |
| A–G ya da çıkış ailelerini etkilemek | çapraz ithal yok (test 39, 40) |
| Öğrenme ağırlıklarını oynatmak | learner'a yazmaz |
| `PAPER_BOUNDED` / `ACTIVE` moda geçmek | `config_v3` fail-closed reddeder (test 42, 43) |
| Otomatik terfi etmek | `mtf_auto_promotion=true` → `ConfigError` (test 44) |
| Geçmişi yeniden yazmak | eski snapshot/outcome/ders/defter **değiştirilmez** |

**Bayt-özdeşlik.** H açık ve kapalıyken aktif risk alanları ve açılan pozisyon parmak izleri
**birebir aynıdır**; tek fark H'nin kendi `mtf_context` alanıdır (test 34, 35).

Eksik/belirsiz veri **sıfır değildir**: ilgili alan `None` kalır, `field_provenance` içinde
`MISSING` işaretlenir ve karar `ABSTAIN` olur.

---

## 3. Mekanik tanımlar

Bütün ölçümler **yalnız kapanmış barlar** üzerinde yapılır (§4).

### 3.1 HTF likidite seviyesi

Seviye yalnız **kayıtlı bir kaynaktan** gelebilir; kaynak ve destekleyen zaman damgaları
kaydedilir:

| Kaynak | Tanım |
| --- | --- |
| `CONFIRMED_SWING` | Fraktal salınım: `i` indeksinin her iki yanında `swing_lookback` kapanmış bar; tepe **tekil** olmalı. Teyit indeksi `i + lookback`. |
| `EQUAL_LEVEL_CLUSTER` | En az **iki** salınımın `equal_level_atr_tolerance × ATR` içinde kümelenmesi. ATR ölçülemezse küme üretilmez. |
| `PREVIOUS_CLOSED_PERIOD_EXTREME` | Son **tamamlanmış** HTF barının yüksek/düşüğü. |

Öncelik: küme → en son teyitli salınım → önceki dönem ucu. Seçim sonuçlara bakılarak
yapılmaz.

### 3.2 Süpürme / geri alma (sweep + reclaim)

Yükseliş biçimi:

1. Fiyat, satış-tarafı likiditenin **altına** en az `min_sweep_atr × ATR` iner.
2. **Kapanmış** bir mum seviyeyi geri alır (`reclaim_confirm_bars` kadar kapanışla teyit).
3. Ardından geçerli bir LTF yükseliş teyidi gelir (§3.4).

Düşüş biçimi simetriktir. **Fitil tek başına süpürme sinyali değildir**; aşım ve geri alma
ayrı ayrı ölçülür (`weekly_structure.classify_level_interaction` yeniden kullanılır).

### 3.3 Kabul edilmiş kırılım (accepted breakout)

* Yalnız fitille aşma **yetersizdir** (test 11).
* Seviyenin ötesinde, `breakout_close_buffer_atr × ATR` tamponuyla **gövde kapanışı** gerekir.
* `required_breakout_closes` kadar kapanmış teyit istenir (varyanta göre artırılabilir).
* **Kabul edilmiş kırılım sonradan süpürme olarak yeniden etiketlenemez** (test 12). Bu bir
  sözleşmedir, yapılandırmayla kapatılamaz.

### 3.4 LTF yapı kayması (structure shift)

* Yükseliş: kapanmış bir LTF mumu, **daha önce teyit edilmiş** bir LTF salınım yükseğinin
  üstünde kapanır.
* Düşüş: simetrik.
* Salınım, **kendi teyit indeksinden sonraki** bir mumla kırılmalıdır; teyit edilmemiş bir
  seviyeyi “kırmak” geriye dönük bir iddia olurdu.
* Aynı barda iki yönlü kayma → `AMBIGUOUS` → `ABSTAIN`.

### 3.5 Yer değiştirme (displacement)

ATR ile normalize edilir; **büyük mum tek başına yetmez**:

```
gövde / ATR        >= min_displacement_body_atr
aralık / ATR       >= min_displacement_range_atr
kapanış konumu     >= close_location_threshold      # (close-low)/range, düşüşte simetrik
yön hizası         kapanış yönü kayma yönüyle aynı
```

### 3.6 Retest

Kırılan yapı seviyesinin, kayma barından sonraki `retest_bar_limit` **kapanmış** bar içinde
test edilmesi:

* `low <= seviye + tolerans` (yükselişte) **ve** seviyenin üstünde kapanış → `RETEST_CONFIRMED`
* Seviyenin altında kapanış → `RETEST_INVALIDATED`
* Limitten sonra dönüş → `RETEST_LATE`
* Pencere dolmuş, dönüş yok → `RETEST_ABSENT`
* Pencere **henüz dolmamış** → `RETEST_PENDING` (→ `ABSTAIN`, “yok” denmez)

Takdire dayalı **“order block” / “FVG” etiketleri kullanılmaz**: tam matematiksel tanımı
olmayan hiçbir kavram karara giremez.

### 3.7 Yapısal geometri (yalnız araştırma)

```
giriş   = karar anı fiyatı / teyit kapanışı
stop    = süpürme uç noktası ∓ stop_atr_buffer × ATR
hedef   = KARŞI taraftaki doğrulanmış HTF likiditesi
R:R     = |hedef − giriş| / |giriş − stop|
```

* Hedef yok → `NO_STRUCTURAL_TARGET_ABSTAIN`
* Stop/hedef yanlış tarafta, risk ≤ 0 → `INVALID_GEOMETRY_ABSTAIN`
* R:R eşiğin altında → varyant sözleşmesine göre `VETO` ya da `ABSTAIN`

**Gerçek pozisyonun stop'u, TP'si ve miktarı bu hesaptan ETKİLENMEZ.**

---

## 4. Point-in-time sözleşmesi

Yalnız `bar.close_time <= as_of_ms` koşulunu sağlayan barlar kullanılır. İki koruma **aynı
anda** uygulanır:

1. Kapanmamış bar düşürülür (`timestamp + çerçeve_süresi > as_of_ms`).
2. Açık `as_of_ms` süzgeci (karar anından sonraki bar görülemez).

Ek kurallar:

* Açık HTF mumu reddedilir (test 1); açık LTF mumu reddedilir (test 2).
* Gelecekteki bir yem bar, önceden hesaplanmış sonucu **değiştiremez** (test 3).
* `close_time == as_of_ms` sınırı **dâhildir** (test 4).
* HTF bağlamı LTF teyidinden önce, LTF teyidi aday snapshot'ından önce oluşur.
* **Sunucu saati ile borsa bar saati ayrıdır** ve raporda `server_time_vs_bar_time: DISTINCT`
  olarak işaretlenir.
* Kripto sürekli UTC seansı **açıkça etiketlenir** (`CRYPTO_CONTINUOUS_ISO_UTC`).
* Hisse/emtia için seans **uydurulmaz**: profil ölçülemezse `SESSION_UNKNOWN_ABSTAIN`.
* Eksik bar hiçbir zaman yükseliş ya da düşüş kanıtı sayılmaz.
* F00030'un nihai sonucu hiçbir eşiğin hesabına girmemiştir.

---

## 5. Desteklenen çiftler ve veri bütçesi

| Çift | Durum | Kareler | Yeni API isteği |
| --- | --- | --- | --- |
| `D → H1` | **SUPPORTED** | `1d` → `1h` | **0** |
| `H4 → M15` | `DATA_UNAVAILABLE_ABSTAIN` | `4h` → `15m` | 0 (çalıştırılmıyor) |
| `H1 → M5` | `FUTURE_RESEARCH_ONLY` | — | 0 |
| `M15 → M1` | `FUTURE_RESEARCH_ONLY` | — | 0 |

### 5.1 D → H1 neden bedavadır

`AgentRunner.FRAME_SPECS` üretimde zaten `{"1d": 420, "4h": 730, "1h": 30}` çeker. H, bu
karelerin **halihazırda bellekteki referansını** okur (`last_frames`); yeni bir `fetch`
çağrısı yapmaz, `MarketData`yı ithal bile etmez (test 57). Tur başına ek istek: **0**.

### 5.2 H4 → M15 neden kapalı — ölçülmüş gerekçe

Bu oturumda **gerçek sağlayıcıyla** ölçüldü (TradingView, 5 sembol):

| Çerçeve | Gün | Bar | Ortalama gecikme |
| --- | --- | --- | --- |
| `1d` | 420 | 419 | 1,745 s |
| `4h` | 730 | 4379 | 3,822 s |
| `1h` | 30 | 719 | 2,112 s |
| **`15m`** | **10** | **959** | **2,014 s** |
| `15m` | 30 | 2879 | 2,516 s |

25 çekimde hata yok. Sembol başına mevcut veri maliyeti 7,68 s; `15m`(10 gün) eklemek bunu
9,69 s yapar → **+%26,2**. ~12–15 sembollük derin analiz kümesinde tur başına **+24–30 s**.

**Yine de etkinleştirilmedi.** Gerekçe maliyet değil, **izolasyon ve doğrulanamayan risk**:

1. `15m` eklemek `AgentRunner.markets` sözlüğünü değiştirir. Bu sözlük **paylaşılan aktif
   veri yoludur**: çıktısı `CoinContext.frames`e girer ve her teknik ajanın gördüğü nesne
   grafiğini değiştirir. Bu, “H açık/kapalı aktif sistem bayt-özdeş olmalı” sözleşmesini
   doğrudan ihlal eder.
2. `MarketData.fetch` için **bellek içi TTL önbelleği yoktur**: TradingView yolunda CSV geri
   okunmaz, her çağrı canlı istektir. Ek çerçeve = koşulsuz ek istek.
3. VPS tarafı ölçülemedi: hız-sınırı payı, soğuk başlangıç etkisi, tam evrende davranış ve
   sağlayıcı bozulmasında geri düşme. VPS IP'sinin dış servislerce farklı ele alındığı
   bilinmektedir (GitHub anonim git-RPC 401 vakası).

Bu üç maddenin **hiçbiri lokal ölçümle kapatılamaz**. Sözleşmenin öngördüğü dürüst sonuç
uygulanmıştır: **D→H1 yayımlanır, H4→M15 şeması tanımlanır ve `DATA_UNAVAILABLE_ABSTAIN`
olarak işaretlenir.** İleride etkinleştirmek isteyen bir oturum, `AgentRunner`dan **yalıtık**
bir çekme yolu kurmalı ve yukarıdaki üç ölçümü VPS üzerinde yapmalıdır.

### 5.3 M5 / M1

Kapsam dışıdır. Üretim veri hattında `5m` ve `1m` **tanımlı değildir** ve hiçbir istek
üretilmez (test 61, 62).

---

## 6. Karar sözleşmesi

Yalnız üç sonuç: `ALLOW`, `VETO`, `ABSTAIN`.

### ALLOW — tüm koşullar sağlanır

taze ve yeterli veri · geçerli HTF hipotezi · baseline yönü HTF ile hizalı · aynı yönde LTF
yapı kayması · yer değiştirme eşiği geçti · varyant istiyorsa retest geçti · geometri geçerli
· yapısal R:R eşiğin üstünde.

### VETO — yalnız **tam ölçülmüş** veriyle

HTF yönü baseline'a açıkça karşıt · ters yönde LTF yapı kayması · gerekli teyit karar anında
**kesin olarak** yok · yapısal R:R öncedan taahhüt edilmiş asgarinin altında.

### ABSTAIN — ölçemediğimiz her şey

eksik/bayat çerçeve · açık mum · bilinmeyen seans · HTF etkileşimi yok · belirsiz yapı ·
ATR yok · hedef yok · yetersiz bar · sağlayıcı/önbellek arızası · yön sonucu taşımayan
geçersiz geometri.

**`ABSTAIN` hiçbir zaman `ALLOW` ya da `VETO` sayılmaz** (test 51). Kapsam (`coverage`) ve
çekimserlik oranı ayrı raporlanır.

---

## 7. Önceden taahhüt edilmiş varyantlar

Dördü de **sonuçlara bakılmadan** tanımlanmış, hepsi **aynı anda** gölgede ölçülür. “En
iyisi” sonuçlara bakılarak seçilmez; her biri kendi terfi kapılarından ayrı ayrı geçmek
zorundadır.

| Varyant | Farkı |
| --- | --- |
| `H_LENIENT` | gövde 0,30 · aralık 0,45 · konum 0,50 · R:R 1,0 · düşük R:R'de `ABSTAIN` |
| `H_BALANCED` | varsayılan: gövde 0,50 · aralık 0,70 · konum 0,60 · R:R 1,5 |
| `H_STRICT` | gövde 0,75 · aralık 1,00 · konum 0,70 · süpürme 0,20 ATR · R:R 2,0 |
| `H_RETEST_REQUIRED` | retest **zorunlu**, `retest_bar_limit = 4` |

`config_id`, politika sürümü + varyant adı + **bütün eşiklerin** kararlı özetidir: salınım
bakışı, eşit-seviye ATR toleransı, asgari süpürme ATR'si, kırılım kapanış tamponu, gereken
kırılım kapanışı, asgari gövde/aralık ATR'si, kapanış konumu eşiği, retest bar sınırı, stop
ATR tamponu ve asgari yapısal R:R.

---

## 8. Değişmez snapshot ve bağlantı

* H bağlamı, aday snapshot'ı **append edilmeden ÖNCE** eklenir → değişmez kaydın parçasıdır.
* Sonuç görüldükten sonra **geriye dönük yazılmaz**; `sees_outcome=false`,
  `written_at_stage="RANKING"`.
* Bağlantı gerçek `trade_id` üzerindendir (ayrı `kind: "link"` satırı; snapshot yeniden
  yazılmaz).
* Eski snapshot'lar **değiştirilmez**.

**Ön-H dışlaması.** Yalnız şu tam dizi terfi kanıtıdır:

```
H snapshot → gerçek trade bağı → kanonik kapanış → H atfı
```

H yayımından önce açılmış **hiçbir** pozisyon — sonradan kapansa bile — H kanıtı değildir.
Eski snapshot'lara H alanı **asla geriye dönük doldurulmaz**.

| İşlem | Muamele |
| --- | --- |
| `F00030` NATGAS/USDT | `PRE_H_OBSERVATION_ONLY` — değişmez snapshot'ı H'den öncedir |
| `F00031` LTC/USDT | H'den önce açıldıysa kapanışında da `PRE_H_EXCLUDED` |
| `F00032` BNB/USDT | aynı kural |

Çevrimdışı bir yeniden kurulum üretilirse **`RESEARCH_ONLY_RECONSTRUCTION`** olarak
etiketlenmelidir; terfi kanıtı olamaz.

F00030 için A ve E `VETO`, B/C/D `ACCEPT`, F/G `ALLOW` demişti. Bu **tek bir gözlemdir** ve
H'nin eşikleri bu sonucu “doğru elemek” için ayarlanmamıştır.

---

## 9. Atıf

`state/mtf_eval.json`, varyant başına: `n_h_snapshots`, `n_h_links`, `n_h_linked_closes`,
`allow/veto/abstain`, `coverage`, `abstain_rate`, `blocked_winners/losers`,
`avoided_loss_r/usdt`, `missed_gain_r/usdt`, `allowed_net_r/usdt`, `expectancy_delta_r`,
`profit_factor`, `max_drawdown_r`, `cvar5_r`, maliyet dökümü, `cost_sensitivity`,
yön/sembol yoğunlaşması, rejim ve çift kapsamı, bootstrap güven aralığı, walk-forward.

**Maliyet iki kez sayılmaz.** Miras `reported_cost_r` yalnız komisyon + funding'dir (anlamı
korunmuş ve belgelenmiştir); ölçülen kayma **ayrı** alandadır. Kayma zaten gerçekleşen dolum
fiyatının içindedir ve PnL'den ikinci kez düşülmez. Ölçülmeyen bileşen `None`, ölçülmüş
sıfır `0.0`dır.

---

## 10. Terfi kapıları

Mevcut kapılar **gevşetilmemiştir**. H için gerekenler:

en az **50 H-tam bağlı kapanış** · en az **30 takvim günü** · point-in-time uygunluk ·
sızıntı yok · veri kalitesi · doğrulanmış izolasyon · aynı maliyet modeli · yeterli kapsam
(≥ 0,30) · kabul edilebilir çekimserlik (≤ 0,70) · pozitif maliyet-düzeltilmiş iyileşme ·
sıfırı dışlayan güven aralığı · düşüş kötüleşmedi · CVaR5 kötüleşmedi · sembol/yön
yoğunlaşması kabul edilebilir · birden çok sembol ve rejim · walk-forward işaret tutarlılığı.

**Örneklem ön koşulu düştüğünde** bağımlı başarım kapıları `PASS` değil
**`NOT_EVALUABLE_LOW_SAMPLE`** olarak raporlanır ve `passed=false` sayılır. Ham metrik
gizlenmez, yalnız “geçti” iddiası geri çekilir.

**Otomatik terfi hiçbir koşulda mümkün değildir.** `mtf_auto_promotion=true` config
doğrulamasında reddedilir.

---

## 11. Bilinen sınırlamalar

1. **H'nin kârlı olduğuna dair hiçbir kanıt yoktur.** Bugün örneklem sıfır/bire yakındır.
2. `H4 → M15` ölçülmemiş VPS riski nedeniyle kapalıdır (§5.2). `H1→M5` ve `M15→M1` kapsam
   dışıdır.
3. Yalnız baseline'ın **zaten aday gösterdiği** işlemler üzerinde çalışır: bir seçicilik
   filtresidir, sinyal üreteci değildir. Baseline'ın reddettiği bir fırsatı keşfedemez.
4. Likidite seviyesi seçimi (küme → salınım → dönem ucu) bir **tasarım tercihidir**; başka
   sıralamalar farklı sonuç verebilir ve bu ölçülmemiştir.
5. Süpürme uç noktası, HTF ATR'siyle normalize edilmiş **ölçülen aşımdan** türetilir; gerçek
   emir defteri likiditesi ölçülmez.
6. Kayma/etki maliyeti çoğu kapanışta ölçülmemiştir; `impact_drag_r` üretimde tipik olarak
   `None` kalır.
7. Seans çıkarımı yalnız günlük çerçevede yapılır; gün-içi çerçevelerde sürekli piyasa
   varsayımı **yalnız UTC etiketi** içindir ve seans iddiası taşımaz.
8. Dört varyant aynı veriyi paylaşır; aralarındaki karşılaştırma **bağımsız deney değildir**
   (çoklu karşılaştırma düzeltmesi uygulanmamıştır).
9. Bağ yazımı sayaçları (1A) yalnız ileriye dönüktür; geçmiş turların bağ sağlığı bilinmez.

---

## 12. Geri alma (rollback)

H tamamen katkısaldır:

* **Config ile durdurma:** `entry_selectivity.mtf_enabled: false` → `_attach_mtf_context`
  erken döner, hiçbir H alanı yazılmaz, aktif davranış değişmez.
* **Kod ile geri alma:** dağıtım öncesi SHA'ya `git reset --hard <rollback_sha>` + servis
  yeniden başlatma. H'ye ait tek kalıcı çıktı `state/mtf_eval.json` ve snapshot içindeki
  `mtf_context` alanıdır; ikisi de **salt gözlemdir** ve okuyucular `.get()` kullandığı için
  eksiklikleri şema kırmaz.
* Defter, pozisyon, risk yapılandırması ve öğrenme durumu H tarafından **hiç yazılmadığı
  için** geri alma sırasında düzeltilmesi gereken bir yan etki yoktur.

---

## 13. İlgili belgeler

`docs/ENTRY_SELECTIVITY_CHALLENGER_V1.md` (A–E) ·
`docs/WEEKLY_MARKET_STRUCTURE_V1.md` (F/G) ·
`docs/EXIT_GIVEBACK_AND_PROFIT_PROTECTION_V1.md` ·
`docs/PAPER_LEARNING_LOOP_INTEGRITY_V3.md` ·
`docs/VPS_DEPLOYMENT.md` · `docs/BACKUP_RESTORE.md`
