# Ortak yapı politikası — `structures_v1` (kodlamadan ÖNCE yazıldı, 2026-09-22)

> PAPER. Eşikler aşağıda SABİTTİR; sonuçlara bakılarak seçilmedi ve bu sürümde sonuçlara göre DEĞİŞTİRİLMEZ. Değişiklik
> yeni sürüm adı ister (`structures_v2` …). `geometry_quality` kazanma olasılığı DEĞİLDİR; hiçbir yerde p_win/beklenti
> üretilmez. Karar yalnız KAPANMIŞ barlarla verilir.

## A. Katalog ve durum makinesi (bütün botlar aynı çıktıyı alır)

Kayıt durumları: **FORMING** (yapı tanındı, tetik kapanışı yok) → **CONFIRMED** (tetik seviyesinin ötesinde KAPANIŞ;
`confirmed_at` = o barın kapanışı) | **BROKEN** (geçersizlik seviyesinin ötesinde kapanış) | **EXPIRED** (tetik penceresi
doldu ya da teyit bayatladı). Pivot kullanan yapı, son pivotunun sağında `k=3` bar kapanmadan TANINMAZ (`detected_at`).

| Aile | Adlar (kanonik) | Taraf | Tetik / geçersizlik | Pencere |
|---|---|---|---|---|
| Mum (bağlamla) | HAMMER (öncesi DOWN) / HANGING_MAN (öncesi UP); INVERTED_HAMMER (DOWN) / SHOOTING_STAR (UP); BULLISH/BEARISH_ENGULFING; BULLISH/BEARISH_HARAMI(+_CROSS); MORNING/EVENING_STAR(+_DOJI_STAR, ABANDONED_BABY); PIERCING_LINE / DARK_CLOUD_COVER; TWEEZER_BOTTOM/TOP; THREE_WHITE_SOLDIERS / THREE_BLACK_CROWS; BELT_HOLD, KICKER, MEETING_LINES, HOMING_PIGEON, DESCENDING_HAWK | adın tarafı; bağlamsız çekiç/ters çekiç ve DOJI, SPINNING_TOP, MARUBOZU, TRI_STAR: taraf YOK | tetik = şekil yükseğinin (boğa) / düşüğünün (ayı) ötesinde kapanış; geçersizlik = karşı uç | 3 bar |
| Grafik | DOUBLE/TRIPLE_BOTTOM/TOP, HEAD_AND_SHOULDERS / INVERSE_…, ASCENDING/DESCENDING_TRIANGLE, BULL/BEAR_FLAG (**paralel kanal**), BULL/BEAR_PENNANT (**daralan kanal**) | yapının kırılış tarafı (üçgen: iki taraf) | tetik = boyun/sınır; geçersizlik = diplerin/tepelerin/kanalın ötesi | 20 bar |
| Senaryo | COMPRESSION_BREAKOUT (6 bar aralığı ≤ 1.0×ATR14; iki taraf), BREAK_RETEST_HOLD (kırılan seviyeye 0.25×ATR içinde dönüş + seviyenin doğru tarafında kapanış, 10 bar), SWEEP_RECLAIM (teyitli önceki tepe/dip ya da referans seviye fitille aşılıp aynı ya da sonraki barda İÇERİDE kapanış; `overshoot_closes` ≥ 1 ise "başarısız kırılım") | kırılış yönü / taşmanın TERSİ | kayıtta | 8 bar |
| Seviye bağlamı | teyitli destek/direnç bölgeleri (eşit pivot kümesi, 0.5×ATR), son teyitli tepe/dip, son salınımın 0.5/0.618/0.705/0.79 geri çekilme oranları | — | — | — |

Teyitli yapı karar için **taze** sayılır: `confirmed_at`tan sonra en çok **2** kapanmış bar. Stop = geçersizlik ∓ 0.25×ATR14
(dilim). Hedef = ölçülü hareket (yapı yüksekliği tetikten) varsa; yoksa boş (bot kendi hedefini kullanır). Geri çekilme
oranlarının **bu sürümde karar etkisi YOKTUR** (yalnız bağlam/gösterim). "Stop avı/niyet" iddiası üretilmez: SWEEP_RECLAIM
yalnız taşma ve geri dönüşü ölçer. Desteklenmeyen/belirsiz adlar: Wolfe dalgası (öznel), "likidite avı", "stop hunt"
(niyet iddiası) → UNSUPPORTED; "pin bar" → çekiç ailesi; "bull/bear trap", "fakeout" → SWEEP_RECLAIM (başarısız kırılım).

## B. Karar önceliği (her bot için aynı sıra, deterministik)

Botun **niyetli yönü** (kuralın/konsensüsün yönü) sabittir; yapı bu yönü ASLA tersine çevirmez.

1. Analiz hesaplanamıyor (bar yetersiz / veri kimliği) → **ETKİSİZ** (gerekçe kodu: hangi dilim, kaç bar gerekiyordu).
2. Değerlendirilen dilimlerden birinde **taze teyitli KARŞI** yapı → **BEKLE** (`OPPOSING_CONFIRMED`).
3. Karar diliminde son 2 bar içinde **BOZULMUŞ uyumlu** yapı → **BEKLE/PLAN İPTALİ** (`COMPATIBLE_BROKEN`).
4. Karar diliminde **taze teyitli uyumlu** yapı → **GİRİŞ ADAYI** (teyit kapanışından sonraki ilk doğrulanmış fiyat;
   fiyat tetikten 1.0 × ATR14'ten uzaksa **İPTAL** `CHASE_LIMIT`).
5. Karar diliminde **FORMING uyumlu** yapı → **TETİĞİ BEKLE** (`WAIT_TRIGGER`; tetik/geçersizlik/son kullanma kayıtta).
6. Hiçbiri → **ETKİSİZ** (`NO_STRUCTURE`): botun kendi kuralı aynen geçerli.

(Uygulamadan önce düzeltildi: ilk taslakta 4 ile 5 ters sıradaydı; teyitli uyumlu yapı varken başka bir oluşan yapı
yüzünden beklemek kuralın amacına ters düşüyordu. Hiçbir sonuç görülmeden, politika kodu yazılmadan değiştirildi.)

Aynı yapı (aynı `pattern_id`) bir defterde **bir kez** giriş üretir (yeniden tarama/yeniden başlatma ikinci işlem açmaz).

## C. Bot × aile × bağlam matrisi

| Bot | Dilimler (karar/bağlam) | Uyumlu (giriş zamanlaması) | Geçersizlik / karşı | Açık pozisyon |
|---|---|---|---|---|
| **Ana bot** | 4h / 1d | niyetli yöndeki mum + grafik + senaryo kayıtları. **Geri çekilme planı** uyumlu teyitli yapı ister (planın "alıcı/satıcı mumu" şartı); kırılım planı değişmez | 2 + 3 → BEKLE | girişten SONRA teyitli karşı yapı (4h) → stop, teyit barının ucuna **sıkılaştırılır** (yalnız sıkılaştırır, fiyatın yanlış tarafına konmaz) |
| **T2** | 1d | BULL_FLAG, BULL_PENNANT, ASCENDING_TRIANGLE (boğa), BREAK_RETEST_HOLD, COMPRESSION_BREAKOUT, boğa mum dönüşleri (geri çekilme sonu) — kural OPEN derken 4/5 uygulanır | karşı: tepe/OBO/ayı bayrak/flama/ayı mumları/SWEEP_RECLAIM (ayı) | **ETKİSİZ** (T2 yalnız kendi trend kuralıyla çıkar; gerekçe `TREND_EXIT_RULE_ONLY`) |
| **M2** | 1d | momentum devamı: BULL_FLAG, BULL_PENNANT, ASCENDING_TRIANGLE, COMPRESSION_BREAKOUT, BREAK_RETEST_HOLD | karşı: SWEEP_RECLAIM (ayı = başarısız kırılım), DOUBLE/TRIPLE_TOP, HEAD_AND_SHOULDERS, DESCENDING_TRIANGLE (ayı) | **ÇIKIŞ**: girişten sonra teyitli karşı dönüş/başarısız kırılım (`M2_STRUCTURE_EXIT`) ya da girişe dayanak yapının BOZULMASI (`M2_ENTRY_STRUCTURE_FAILED`) |
| **Box** | 5m (kutu 1d) | TOP'ta ayı dönüş mumu ya da kutu tepesinin SWEEP_RECLAIM'i → SHORT; BOTTOM'da ayna → LONG. Eski "kırmızı mum" tetiği bu sürümde katalog teyidiyle DEĞİŞİR; stop = kaydın geçersizliği; hedef/gün sonu kuralı aynı | **teyitli dış kırılım** (kenarın ötesinde ardışık 2 kapanış, içeri dönüş yok) → o kenardaki dönüş planı **İPTAL** (`BOX_OUTSIDE_BREAKOUT`) | açık fade'e karşı teyitli dış kırılım → **ÇIKIŞ** (`BOX_OUTSIDE_BREAKOUT_EXIT`) |
| **Formasyon** | 15m giriş / 1h yapı / 4h bağlam | katalog kaydı → koşullu plan (tetik/geçersizlik/stop/hedef KAYITTAN). Aileler v2: A2 trend geri çekilme mumu (4h trend uyumlu), B2 seviye dönüş mumu (1h bölgeye ≤0.5 ATR), C2 sıkışma (iki taraf, biri dolunca diğeri İPTAL), D2 grafik yapısı (15m/1h), E2 SWEEP_RECLAIM, F2 BREAK_RETEST_HOLD | kayıt BOZULURSA plan BROKEN; süre dolarsa EXPIRED; kovalama/RR yeniden hesabı `_try_open`da | defter stop/hedef + zaman stopu (değişmedi) |

Beş botun aynı yapıyı aynı emre çevirmesi GEREKMEZ: tablo rolü gösterir. Aynı coinde aynı yöndeki işlemler bağımsız kanıt
sayılmaz; panelde üst üste binen maruziyet gösterilir. Risk/boyut/maliyet kapıları atlanmaz (yapı yalnız zamanlamayı,
beklemeyi, iptali ve yönetimi etkiler). Sürüm etiketi her kararda ve işlem kaydında durur; eski ölçümlerle birleştirilmez.

## D. `structures_v1.1` — uygulama sonrası düzeltmeler (2026-09-23, dağıtımdan ÖNCE)

**Hiçbir eşik değişmedi.** Değişiklikler durum makinesinin ve bayrak kimliğinin ANLAMINDA; gerçek arşivde ileri
yürüyüş bütünlük denetimiyle (`tradingbot/structures/audit.py`, kanıt: `docs/review/evidence-2026-09-22-shared/`)
bulundu. İşlem sonucu/PnL'e bakılmadı. Sürüm adı bu yüzden `structures_v1` → `structures_v1.1` (kimlikler değişir).

1. **Terminal durum kalıcıdır.** Teyitten sonra tazelik penceresinde (2 bar) geçersizlik kapanışı → BROKEN; pencere
   dolunca → EXPIRED. Sonraki kapanışlar durumu DEĞİŞTİRMEZ (önce EXPIRED kayıt sonraki bir kapanışla BROKEN'a
   dönüyordu: 2 coin 1d'de 203, 4h'de 408 dönüş). Süre dolduktan SONRAKİ ilk geçersizlik kapanışı ayrı bir olaydır:
   `broken_at_ms` + `broken_after_expiry=True`. B-3 ("son 2 barda bozulmuş uyumlu yapı → BEKLE") ve M2'nin giriş-yapısı
   çıkışı `broken_at_ms`i okur — amaçları korunur, durum geri yazılmaz.
2. **Seviyeler olay barında donar.** Teyit/bozulma/süre olayından sonra tetik (eğik çizgide o barın değeri),
   geçersizlik, stop, hedef ve geometri değişmez. OLUŞAN kayıt ise gelişebilir (bayrak uzar, eğik çizgi ilerler, yeni
   dayanak eklenir) — bu "revizyon"dur, geriye boyama değildir.
3. **Bayrak/flama kimliği = direk ucu** (boğa: en yüksek tepe, ayı: en düşük dip). Aynı konsolidasyonun farklı direk
   başlangıçlı yorumları tek yapıdır; kimlik başına TEK kayıt: olay yaşamış yorum varsa İLK olay (ilk kırılış), yoksa
   TETİĞİ EN YAKIN yorum (ilk teyit olacak olan). Bayrak ↔ flama adı kimliği değiştirmez. Direk ucu taranan pencerenin
   başına direk uzunluğundan (8 bar) yakınsa kimliğin bütün yorumları hesaplanamaz → kayıt üretilmez
   (`FLAG_IDENTITY_AT_WINDOW_EDGE`). Önce: oluşan kayıt ile kırılışta teyit olan kayıt farklı yorum/kimlik olabiliyordu.
   Eski dedektör çıktısı (`detect_chart_patterns`) bit-bit aynıdır (5410 pencere, 62439 formasyon, 0 fark).
4. **Formasyon botu planı kaydı izler.** Bekleyen v2 plan kendi seviyesini dondurup ayrı tetik değerlendirmesi YAPMAZ:
   kayıt TEYİT → TETİKLENDİ (teyit kapanışı anı), BOZULDU → BOZULDU, SÜRESİ DOLDU → SÜRESİ DOLDU, analizden ÇEKİLDİ →
   İPTAL (`RECORD_WITHDRAWN`); oluşurken seviyeler kayıttan yenilenir (`record_revisions`, `revision_history`).
   Yeni bir pivotla yeniden tanımlanan yapı (üçgen) yeni kimliktir: eski plan çekilir, aynı taramada yeni plan kurulur.
5. Doğrulama tek kaynakta: `structures.catalog.validate_settings` (ENFORCE gerçek parayla açılamaz).

## E. `structures_v1.2` — bağımsız doğrulayıcı bulguları (2026-09-23, dağıtımdan ÖNCE)

Adversaryal doğrulayıcı 18 bulgu raporladı (3 yüksek, 4 orta, 11 düşük); hepsi kaynaktan yeniden doğrulandı ve
düzeltildi, her biri için düzeltmeden ÖNCEKİ kodda düşen bir gerileme testi var
(`tests/test_structures_verifier_findings_v1.py`). **Eşik değişmedi**; yine sonuca/PnL'e bakılmadı. Anlamı değişenler:

* **SHADOW = OFF (bit-bit)**: ana botun mum ajanı katalog oyunu yalnız ENFORCE'ta kullanır (#1); replay ana modu canlıyla
  aynı modu ve çerçeve piyasasını ajanlara verir (#3). Gölge kararlar işlem kaydında `shadow` işaretlidir ve girişin
  dayanağı SAYILMAZ; yalnız gölge olmayan ENTER referansı "kullanılmış yapı" ve "giriş yapısı" olur (#5).
* **Durum makinesi**: tanınmadan (son pivot teyidinden) önceki kapanışlar yapıyı yalnız BOZABİLİR, teyit EDEMEZ; teyit
  tanınma barında ya da sonrasında tetiğin ötesinde kapanış ister (#7). Tanındıktan sonra tetik tanımsızlaşırsa (üçgen
  tepe noktası geçildi) → EXPIRED `TRIGGER_UNREACHABLE_APEX_PASSED` (#17). Teyitli kaydın `expires_at_ms`i bayatlama
  kapanışına eşittir (#9). Önbellek içeriği paylaşır ama karar anı ve provenans çağıranındır (#8).
* **Geç doğan kayıt yok (#13)**: kırılım-geri test her seviye ve ufuk içindeki HER kesişme için kayıt üretir (önce yalnız
  ilk kesişme); sıkışma dedektörü kırılış barını da kendi penceresiyle değerlendirir. Denetime `RECORD_BORN_LATE` eklendi.
* **Box (#6)**: teyitli dış kırılım, gün içinde içeri KAPANIŞ olana kadar o kenarın dönüş planını iptal eder (önce 2 barlık
  tazelikle sınırlıydı). **M2 (#4)**: giriş yapısı analiz penceresinden düşse de girişten sonraki günlük kapanışlar,
  işlem kaydındaki DONMUŞ geçersizlik seviyesiyle ölçülür. **Ana bot**: geri çekilme planı analiz yokken bekler (#14);
  kovalama ölçüsü doğrulanmış perp mark'tan (#12); çalışma anı modu LIVE ise ENFORCE → SHADOW (#15); yapıyla
  sıkılaştırılmış stopu, sıkılaştırmadan ÖNCE açılmış 1h barının uçları sonraki turlarda da tetiklemez (#2).
* **Arıza izolasyonu (#16)**: yapı katmanı istisnası botun kendi çıkışını düşürmez (kural yeniden sorulur; ENFORCE'ta
  yalnız YENİ giriş engellenir); formasyon defterinde analiz arızası taramayı/zaman stopunu durdurmaz.
* **Formasyon (#10, #11, #18)**: tetiklenmiş (fiyat bekleyen) plan da kaydını izler; plan yalnız doğrulanmış perp
  diliminin analizinden kurulur ve o dilimin piyasası girişte denetlenir; her plan sonucu (red/iptal/bozulma/süre/kapanış)
  karar deposuna yazılır — reddedilen plan panelde "girdi" görünmez.

## F. `structures_v1.3` — ikinci doğrulama turu (2026-09-23, dağıtımdan ÖNCE)

`905098d` üzerinde ikinci bağımsız doğrulayıcı 9 bulgu raporladı (2 orta, 7 düşük; kritik/yüksek yok); hepsi kaynaktan
doğrulandı ve düzeltildi; her biri için `905098d`'de düşen gerileme testi var (`test_structures_verifier_findings_v1.py`,
`test_r3_*`). **Eşik değişmedi.** Anlamı değişenler:

* **"O anki" seviye kümesi**: süpürme/aralık kırılımı/geri test olayı, OLAYIN BAŞLADIĞI anda bilinen son `swing_levels`
  salınımla değerlendirilir; seviye kümeden sonradan çıkınca kayıt DÜŞMEZ (önce teyitli-taze kayıtların %6–13'ü bir bar
  sonra çıktıdan siliniyordu). Üçgende her ardışık eğim-pivot çifti kendi adayıdır; yeni bir eğim pivotu teyit olunca
  oluşan aday `SUPERSEDED_BY_NEW_PIVOT` ile sona erer, önce teyit olduysa yaşar (eski dedektör çıktısı değişmedi).
  Denetime `CONFIRMED_WITHDRAWN_WHILE_FRESH` eklendi.
* **Box**: açık fade, girişten SONRA teyit olan dış kırılımla (içeri kapanış yoksa) kayıt tazeliğinden bağımsız kapanır
  (tur aralığı 5m tazeliğinden uzun olabilir); SHADOW kaydı ENFORCE ile aynı iptali yazar.
* **Arıza yedeği tek yerde**: `paper_rules.decide_with_structures` yapı katmanı istisnasında botun kendi kararını korur
  (ENFORCE'ta yeni giriş yok) — canlı ve replay aynı.
* **Formasyon**: tetiklenmiş v2 plan yalnız kaydı o taramada teyitli-tazeyken açılır (analiz arızası/yokluğunda açılmaz;
  kayıt görünmüyorsa iptal değil bekler, süre sınırı işler); kardeş-plan iptali giriş satırını ezmez; izleyiciden gelen
  kapanış satırı başka sembolün analizini taşımaz.
* Süresi dolan kaydın `expires_at_ms`i sona erdiği andır; eğik tetikli oluşan üçgenin son geçerliliği tepe noktasını aşmaz.
  Önbellekte kayıt düzeyinde de an ve kaynak çağıranındır.
