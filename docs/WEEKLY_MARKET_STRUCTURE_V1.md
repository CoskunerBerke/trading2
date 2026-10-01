# WEEKLY MARKET STRUCTURE AND CONTEXTUAL PRICE-ACTION CHALLENGERS V1

> **Mod: SHADOW. `applied = 0`. Aktif giriş kararı, sıralama, yön, miktar, kaldıraç,
> RiskEngine, stop/TP, defter ve gateway DEĞİŞMEDİ.**

`ENTRY_SELECTIVITY_CHALLENGER_V1`in uzantısı. Mevcut beş aile (A–E) aynen durur; yanlarına
iki yeni aile eklenir. Amaç, makul görünen piyasa-yapısı fikirlerini **yanlışlanabilir,
point-in-time, maliyet-farkında SHADOW kanıta** çevirmektir.

**Bu tekniklerin kârlı olduğu iddia EDİLMEMEKTEDİR.** Bugünkü örneklemde hiçbir aile terfi
kapılarını geçmemektedir ve geçip geçmeyeceği ölçülecektir.

## 1. Bileşenler

| Dosya | Rol |
| --- | --- |
| `learn/weekly_structure.py` | Faz 1+2: önceki tamamlanmış hafta + süpürme/geri alma sınıflandırması |
| `learn/candle_context.py` | Faz 3: bağlamsal mum şekilleri (yön iddiası YOK) |
| `learn/entry_challenger_v2.py` | Faz 4+5: F ve G aileleri, 4 yapılandırma varyantı |
| `learn/entry_eval_v2.py` | Faz 6+7: sonuç atfı + 16 terfi kapısı |
| `ops/fingerprint.py` | Kanonik parmak izi sözleşmesi (bakım düzeltmesi) |

## 2. Faz 1 — haftalık yapı, point-in-time

Karar anında **yalnız kapanmış barlar** kullanılır (`drop_unclosed_last_bar` veri hattında
zaten uygulanır; modül ayrıca `excluded_unclosed_bars` ve `excluded_future_bars` sayar).

Hafta sınırı ISO/UTC Pazartesi 00:00'dır. **Seans profili varsayılmaz, ölçülür:**

| Ölçüm | Profil |
| --- | --- |
| 7 bar/hafta + hafta sonu barı var | `CRYPTO_CONTINUOUS_ISO_UTC` |
| 5 bar/hafta + hafta sonu barı yok | `SESSION_WEEKDAY_ISO_UTC` |
| Düzensiz | `UNKNOWN` → haftalık alanlar `UNKNOWN` kalır |

Bu, `AAPL/USDT` ya da `MSFT/USDT` gibi seans temelli enstrümanlara kripto sınırı dayatmayı
önler. Doğru tamamlanmış hafta güvenilir biçimde kurulamıyorsa alanlar `UNKNOWN` olur.

## 3. Faz 2 — süpürme ve geri alma

**Seviyenin ötesindeki bir fitil kendiliğinden süpürme sinyali DEĞİLDİR.** Aşım ve geri alma
ayrı ölçülür; sınıflar:

`NO_INTERACTION` · `TOUCH_ONLY` · `BREAKOUT_UNCONFIRMED` · `ACCEPTED_BREAKOUT` ·
`HIGH_SWEEP_RECLAIM` · `LOW_SWEEP_RECLAIM` · `AMBIGUOUS` · `DATA_UNAVAILABLE`

Eşikler ATR/tick cinsinden yapılandırılabilir ve yalnız **kapanmış** barlara bakar.
`accepted_breakout_never_sweep` bir **sözleşmedir**: config ile kapatılamaz (`ConfigError`).

## 4. Faz 3 — bağlamsal mum

Modül **şekil** ölçer, **etiket** üretmez. Her kayıt `directional_claim = "NONE"` taşır.

* *Hammer* ile *Hanging Man* **aynı şekildir**; farkı önceki trend ve konum yaratır.
* *Inverted Hammer* ile *Shooting Star* için de aynısı geçerlidir.
* *Doji* dengedir; garanti bir dönüş değildir.
* Doğru ad **Three White Soldiers**'tır ("Three White Crows" kanonik bir ad değildir).

Çıktı biçimi: `pattern_shape=HAMMER_LIKE, trend_context=DOWNTREND,
level_context=NEAR_PREVIOUS_WEEK_LOW, confirmation=UNCONFIRMED, directional_claim=NONE`.

Teyit, formasyondan **sonra kapanmış** barlarla ölçülür. Sonraki bar yoksa durum `UNKNOWN`'dır:
"teyit edilmedi" ile "henüz bakılamadı" ayrı şeylerdir.

## 5. Faz 4+5 — F ve G aileleri

**F — `F_weekly_sweep_reclaim`**

* Teyitli YÜKSEK süpürme+geri alma SHORT bağlamını destekler, LONG'a karşıdır (ve tersi).
* Kabul edilmiş kırılım süpürme sayılmaz; süreklilik lehine okunur.
* Eksik/belirsiz veri `ABSTAIN`/`UNKNOWN` üretir — **BLOCK değil**.

**G — `G_structural_risk_reward`**

* Karar anındaki geçersizleme ve hedef adayları (aktif plan, haftalık orta nokta, karşı
  haftalık sınır), brüt ve **maliyet düzeltilmiş** R:R.
* `BLOCK` yalnız yapı **ve** maliyet girdileri gerçekten ölçülmüşse üretilir.
* Aktif stop/TP'ye dokunulmaz. Gösterilen dolar kârı kanıt sayılmaz.
* `GanizAlgo V2 Alpha` göstergesi **kopyalanmamıştır**.

**Mum bağlamı yalnız `confidence` oynatır (±0.10); bir kararı çeviremez.**

Dört varyant aynı anda ölçülür (`base`, `strict_sweep`, `rr_1_5`, `observe_only`); hiçbiri
sonuçlara bakılarak seçilmez ve her biri kendi kapılarından ayrı geçmek zorundadır.

## 6. Faz 7 — terfi kapıları (aile başına 16)

V1'in **14 kapısının tamamı** korunur (`MIN_LINKED_CLOSES` 50, `MIN_OBSERVATION_DAYS` 30,
yön/rejim kapsamı, pozitif beklenti iyileşmesi, walk-forward tutarlılığı, sıfırı dışlayan
güven aralığı, PF iyileşmesi, drawdown/CVaR5 kötüleşmemesi, ayrım gücü, hayatta kalanların
kırılma noktası üstü, sembol yoğunlaşması, sızıntı/point-in-time). **Hiçbiri gevşetilmedi.**

İki **ek** kapı:

| Kapı | Koşul |
| --- | --- |
| `WEEKLY_DATA_COVERAGE` | kapanışların ≥ %60'ında haftalık yapı gerçekten ölçülmüş |
| `ABSTAIN_RATE_ACCEPTABLE` | ailenin kararsızlık oranı ≤ %50 |

`auto_promotion=true`, `PAPER_BOUNDED` ve `ACTIVE` normal config'den **imkânsızdır**.

## 7. Bakım düzeltmeleri (ayrı commit'ler)

| Sorun | Düzeltme |
| --- | --- |
| `deploy/update.sh` → `backup.sh manual`, CLI yalnız `--daily/--hourly` | CLI dört türü de tanır ve arşivi **doğrular** (çıkış kodu 1); `update.sh` yedeği git/pip/systemctl'den **önce** ve fail-fast çalıştırır |
| Parmak izinde olmayan `take_profit` alanı | `ops/fingerprint.py` alan kümesini `Position` şemasına karşı doğrular; eksik alan `FingerprintError`, boş projeksiyon `vacuous=True` |
| `entry_snapshot.jsonl` ömür boyu rotasyonsuz | `SegmentArchive` ile **arşiv-önce**, checksum'lı, idempotent, çökmeye dayanıklı rotasyon; arşiv düşerse budama YOK |

**Sıcak döngü arşivi açmaz.** Bu, hatta zaten korunan bir değişmezdir; `by_candidate`,
`trade_links`, `known_ids` varsayılan olarak sıcaktır ve arşiv erişimi `include_archive=True`
ya da `resolve_missing()` ile açıkça istenir. Varsayılan pencere (20.000 satır ≈ 50 gün) 30
günlük terfi penceresinden geniştir.

## 8. Panel

`/learning` → "Haftalık yapı ve bağlamsal fiyat hareketi (F / G)" ve
`/api/entry-selectivity` → `weekly_context`. Mevcut çıktı bozulmadı.

Dil sözleşmesi: hiçbir formasyon AL/SAT talimatı olarak gösterilmez. Sayfa "Yalnız SHADOW
gözlem", "aktif karara etkisi yok" ve formasyonların **bağlamsal** olduğunu açıkça yazar.
`/llm` dürüst kalır: sahte çağrı üretilmez ve deterministik öğrenmenin LLM gerektirdiği
ima edilmez.

## 9. Testler

`tests/test_weekly_context_v1.py` — 40 senaryo (parametrelendirmeyle 55 test). Ayrıca
`test_deploy_backup_contract.py` (14), `test_fingerprint_contract.py` (19),
`test_entry_snapshot_rotation.py` (19). Tam suite **1632 passed / 22 skipped**.

## 10. Bilinen sınırlar

* **`PENDING_FIRST_REAL_LINK`** — üretimde henüz doğal bir pozisyon açılmadı, dolayısıyla
  hiçbir `link` satırı yok ve bağlantı sözleşmesi üretimde sınanmadı.
* Terfi bugün **imkânsızdır**: 0 bağlı kapanış.
* `nearest_swing_reference` ölçülmüyor (`None`); uydurulmuyor.
* İleriye dönük 1/3/6 barlık getiriler **uygulanmadı**; karar snapshot'ına girmemeleri ve
  `label_available_at` sözleşmesi kanıtlanmadan terfiye dahil edilemeyecekleri için bu sürümde
  kapsam dışı bırakıldı (test 21 karar snapshot'ında bulunmadıklarını zorlar).
