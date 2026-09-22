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
4. Karar diliminde **FORMING uyumlu** yapı → **TETİĞİ BEKLE** (`WAIT_TRIGGER`; tetik/geçersizlik/son kullanma kayıtta).
5. Karar diliminde **taze teyitli uyumlu** yapı → **GİRİŞ ADAYI** (teyit kapanışından sonraki ilk doğrulanmış fiyat;
   fiyat tetikten `chase` × ATR'den uzaksa **İPTAL** `CHASE_LIMIT`).
6. Hiçbiri → **ETKİSİZ** (`NO_STRUCTURE`): botun kendi kuralı aynen geçerli.

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
