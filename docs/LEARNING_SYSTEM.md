# LEARNING SYSTEM

"LLM 7/24 açık kalınca öğrenir" varsayımı yok. Öğrenme istatistikseldir ve katmanlıdır (`tradingbot/learn/`):

1. **Değişmez trade hafızası** (`memory.py`): `state/trade_memory.jsonl` yalnız ekleme (opsiyonel SQLite `learning_features`/`trade_outcomes`). Girişte: bütün uzman raporları, Coin Head kararı, dissent/veto, risk kararı, plan, rejim, model/prompt sürümleri, veri tazeliği; çıkışta sonuç + fiyat yolu + postmortem.
2. **Yapılandırılmış postmortem** (`postmortem.py`): neden açıldı, hangi ajan haklı/haksız, açılmamalı mıydı, stop/hedef/boyut doğru mu, funding/kayma etkisi, kaçırılan hareket, `lesson_codes` (makine okunur) + Türkçe dersler.
3. **İstatistiksel model** (`model.py`, `calibration.py`): özellikler v2 deterministik (`features.py`, saat özellikleri kapalı), **train-only** StandardScaler, L2 lojistik (batch GD, sınıf ağırlığı, recency half-life), Platt/izotonik kalibrasyon, Brier/log-loss/ECE/reliability; hiyerarşik Beta shrinkage global→rejim→sembol/setup (`HierarchicalRate`, α=10) — az verili coin'e aşırı güven yok; kara liste kanıt gerektirir (posterior R<−0.1 ve P(mean<0)>0.8, n≥5).
4. **Gölge / karşı-olgusal** (`shadow.py`): reddedilen güçlü adaylar `state/shadow_book.json`'da; etiketleme yalnız `label_ts` geçtikten sonra o ana kadarki kapalı mumlarla, stop hedeften **önce** (muhafazakâr); `is_counterfactual=True` — gerçek fill kadar güvenilir sayılmaz.
5. **Champion/Challenger** (`registry.py`): `state/models.json`; challenger yalnız kapıyı (holdout ≥ 30, ECE ≤ 0.15, log-loss ve Brier iyileşmesi, beklenti şampiyonun altında değil) geçerse; PAPER'da otomatik terfi opsiyonel, TESTNET/SHADOW/LIVE'da **manuel** (`validate-model --promote --operator <ad>`); drift kontrolü (log-loss/Brier/hit-rate/özellik kayması).
6. **Retrieval** (`retrieval.py`): yapılandırılmış filtreler + standardize kosinüs benzerliği; harici vektör DB yok (SQLite FTS5 opsiyonel).

`LearnerV2.predict` önsel ile modeli n_eff'e göre harmanlar (ani geçiş yok). Legacy `learning.py` korunur; `legacy_bridge` v1 durumunu kayıpsız alır. Etiket: R bazlı WIN/LOSS/SCRATCH (|R|<0.25 scratch), pnl>0 değil.

## Kayıpsız saklama (retention)

Aktif dosyalar performans için sınırlıdır; **öğrenme kayıtları sessizce silinmez**. Taşan kayıtlar önce sıkıştırılmış, checksum'lı, değişmez bir segmente mühürlenir; ancak ondan sonra aktif dosyadan çıkarılır (`learn/journal_archive.py::SegmentArchive`).

- Zincir: `hot journal → atomic sealed segment (.jsonl.gz) → sha256 + manifest → hot journal budanır`. Sıra bozulamaz: arşiv yazımı ya da checksum doğrulaması düşerse **budama yapılmaz** ve `ARCHIVE_FAILED` alarmı üretilir (tur fail-safe sürer).
- Yollar state kökünden türer: `state/decision_archive/` (karar günlüğü, `decision_journal_max_lines` varsayılan 20.000) ve `state/shadow_archive/` (gölge defteri, `MAX_TRADES` 5.000). Mutlak yol hard-code edilmez; arşiv kalıcı state altındadır, dolayısıyla mevcut yedeklemeye (`ops/backup.py`) otomatik dahildir.
- Varsayılan saklama **sınırsızdır** (`decision_archive_max_segments: 0` → `UNLIMITED_NO_DELETION`). Silme yalnız bu değer açıkça pozitif yapılırsa mümkündür.
- `segment_id` ve dosya adı tamamen içerikten türer; aynı blok ikinci kez mühürlenirse aynı sha256 çıkar → retry idempotenttir. Çökme sonrası `pending_trim` ile devam edilir (ne kayıp ne çift kayıt), manifeste düşmemiş segmentler `recover()` ile geri alınır.
- Sıcak döngü arşivi **taramaz**: deneyim havuzu yalnız aktif `trade_memory.jsonl` + `shadow_book.json` okur, `retention_stats()` yalnız manifestten O(1) özet verir. Arşiv okuması offline/rapor yolundadır (`DecisionJournal.iter_all_rows`, `ShadowBook.iter_all_trades`, `quant.run --shadow-archive`) ve kimliğe göre tekilleştirir.
- Checksum'ı tutmayan segment öğrenmeye katılmaz; `SegmentArchive.verify()` ve dashboard `Saklama` bloğu durumu görünür kılar.
- **Dersler de aynı sözleşmededir** (`learn/lesson_store.py`, EDGE & LEARNING QUALITY V2). Eskiden `learning.py` `lessons[-200:]` ile taşan dersleri KALICI olarak siliyordu; artık 200 yalnız **sıcak/dashboard penceresidir**. Taşan dersler `state/lesson_archive/` altında mühürlenir, ders indeksi (bağlam anahtarı → segment) ve sınırlı aggregate sayacları yazılır; retrieval kapsamı `HOT / INDEXED / AGGREGATE` olarak dashboard'da görünür. Arşiv yazılamazsa budama da yapılmaz.
- `lesson_min_rotate_block` (varsayılan 50): `SegmentArchive.commit()` manifesti bastan yazdığı için çok sayıda küçük segment maliyeti O(segment²)'ye taşır. Taşma bu eşiğe ulaşana kadar mühürleme ERTELENİR — ders silinmez, sıcak liste geçici olarak pencereyi aşar.

## Uzun vadeli retrieval (deneyim indeksi)

Kayıpsız saklama tek başına yetmez: arşive taşınan bir gölge sonuç, retrieval yalnız aktif dosyaları okuduğu sürece karar etkisinden düşer. Ölçülen tempoda (~82 gölge/gün, `MAX_TRADES=5000`) bu ~61 günde gerçekleşiyordu — kapsam `HOT_ONLY` idi.

`learn/experience_index.py::ExperienceIndexStore` bu boşluğu kapatır:

```
mühürlenmiş segment (.jsonl.gz) → checksum → BİR KEZ normalize → kompakt shard
→ aktif + indekslenmiş geçmiş = hazır havuz → aday başına SINIRLI benzerlik sorgusu
```

- **Artımlı:** segment yalnız YENİ göründüğünde okunur; `refresh()` tur başına bir kez çağrılır ve kararlı durumda tek manifest okumasıdır (ölçülen: 1–9 ms). Aday başına arşiv **taranmaz**.
- **Türev veri:** indeks silinebilir; `rebuild()` kayıpsız arşivden deterministik olarak yeniden kurar (aynı satırlar, aynı vektörler, aynı parmak izi). Segment `segment_id` + `sha256` + `block_sha256` manifeste yazılır; aynı segment ikinci kez işlenmez.
- **Atomik:** shard yazımı tmp + fsync + `os.replace`, manifest `atomic_write_json`.
- **Fail-closed:** checksum'ı tutmayan segment ve etiket zamanı çözülemeyen kayıt indekse **girmez**; durum `corrupt_segments` / `skipped_rows` olarak görünür.
- **Tek kimlik, tek deneyim:** aktif ve arşiv aynı `outcome_id`'yi taşısa da bir kez sayılır; gerçek fill (`REAL_PAPER`) aynı kimlikteki gölgeyi daima yener (`merge_sources`). Hiyerarşik prior'ın residual çift sayım koruması aynen sürer.
- **No-lookahead:** `as_of` filtresi arşivden gelen satırlara da uygulanır ve zamanı bilinmeyen kayıt elenir (fail-closed).
- **Vektörleme tek uygulama:** `experience.experience_vector` hem aktif havuz hem indeks tarafından kullanılır — bir kaydın arşive taşınması benzerlik skorunu değiştirmez.
- **Sınırlı sorgu:** `query_pool(..., max_scan=learning_v3.retrieval_max_scan)` (varsayılan 5.000). Havuz sınırın altındaysa **tam tarama** yapılır ve sonuç eski davranışla birebir aynıdır; üstündeyse tarama sembol+yön kovası → sembol kovası → en yeni kullanılabilir kuyruk sırasıyla bütçelenir (ikili arama, `label_ts` sıralı listeler). Ölçülen: 10k havuzda 37.4 ms, 100k'da 37.7 ms, 1M'de 40.6 ms → **arşiv boyutuyla doğrusal büyümez**.
- **Dürüst raporlama:** `retrieval_scope` indeksin gerçek durumundan türer — indeks yok/boşsa `HOT_ONLY`, bozuksa `DEGRADED`, yalnız gerçekten indekslenmiş satır varsa `HOT_PLUS_INDEXED_HISTORY`.
- **Karar günlüğü arşivi bir deneyim kaynağı DEĞİLDİR.** Gerçek sonuç için `TradeMemory`, karşı-olgusal sonuç için `ShadowBook`/arşivi canonical kaynaktır; karar günlüğü audit/link kanıtı olarak kalır. Aksi halde aynı outcome üçüncü kez sayılırdı.

## Adaptive Quant Learning Core V1

Amaç bir TradingView analistini taklit etmek değil; insan aynı anda takip edemeyecek kadar çok geçmiş olayı tutan, karar anında yalnız gerekli/kanıtlanmış az sayıda parametre kullanan sürekli quant öğrenme sistemi. Zincir: `40-60 sembollü ucuz tarama → umut vadedenler derin analiz → HER adayın kaydı → outcome aynı kimlikle bağlanır → kâr/zarar nedeni parametre/maliyet/rejim bazında → coin+yön+rejim+setup+profil hafızası → tüm tarihsel hafızadan benzer olaylar → n=1'den itibaren küçük/sınırlı/açıklanabilir ders → sonraki benzer kararda bounded p_win ayarı → hard risk/emir kapıları aşılmaz → sonuç geldikçe yeniden öğrenme.`

- **Full-history bounded memory** (`learn/aggregate_memory.py`): exemplar penceresi (`retrieval_max_scan`) dışında kalan arşiv sonuçları seviye×ay kovalı toplamlardan katkı verir. Sahiplik: prior=gerçek tam geçmiş, exemplar=pencere içi, aggregate=arşiv kalanı (sayılan exemplar düşülür, clamp ≥0). No-lookahead: yalnız `as_of`'tan önce bitmiş aylar. Kapsam taksonomisi dürüst: `HOT_ONLY | HOT_PLUS_RECENT_INDEX | FULL_HISTORY_BOUNDED | DEGRADED`.
- **Feature yönetişimi** (`learn/feature_registry.py`): 8 bilgi ailesi, 12 karar-düzeyi yumuşak girdi (typed tavanlar, fail-closed config). İndikatör tek başına sert veto olamaz (decision_gates taksonomisi testle kilitli). Yedek gruplar (momentum+RSI, funding çifti, meta-konsensüs) bağımsız tam kanıt sayılmaz. Research-only alanlar aktif vektöre giremez; aktivasyon sözleşmesi: hipotez → point-in-time veri → provenance → availability → walk-forward ablation (`learn/ablation.py`) → net katkı → operatör onayı. Otomatik aktivasyon yok.
- **Dinamik evren + huni** (`universe_eval.py`): Tier A 40-60 (tarayıcı verisinden, ek API çağrısı 0), Tier B ≤25 derin, Tier C sıralamaya girenler. Tier A'nın TAMAMI journal paydasında; elenene küçük `SCREENED_OUT` kaydı (neden/skor/sıra/artifact SHA). Sayı yapay doldurulmaz. `/api/universe` tüm evreni taşır; panel top 10-15 gösterir.
- **Açıklanabilirlik**: her kayıtta `why_summary_tr` (kanıttan türer), `feature_contributions` (şampiyon model logit katkıları, aile toplamları), `learning_influence` (baseline→learned→effective, exemplar/aggregate ağırlıkları, `decision_changed_by_learning`). Postmortem `next_time_policy` bounded yön verir (increase/decrease/hold) — deterministik gelecek iddiası yasak.
- **Aktivasyon**: `TRADINGBOT_LEARNING_INFLUENCE_MODE` typed env override (systemd drop-in için; fail-closed, PAPER-only, secret'sız). Etki tavanı `influence_max_fraction ≤ 0.05`; öğrenme yalnız `p_win` alanını değiştirebilir (alan listesi testle kilitli), risk/emir/stop/TP/kaldıraç döndüremez.
