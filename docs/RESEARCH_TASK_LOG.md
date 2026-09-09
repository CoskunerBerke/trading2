# Araştırma ve Yürütme Görev Kaydı

Güncellenebilir çalışma durumu. Kesinti ya da bağlam yenilenmesinde **buradan devam et**.
Kural: önceki oturum raporları başlangıç ipucudur, kanıt değildir. Her iddia kaynaktan yeniden türetilir.

---

## 0. Doğrulanmış zemin (2026-09-09, bu oturumda yeniden ölçüldü)

| Ne | Değer | Nasıl doğrulandı |
|---|---|---|
| VPS çalışan kod | `8163a79`, dal `feature/quant-evaluation-v1`, ağaç temiz | `git -C /opt/tradingbot/app rev-parse HEAD` |
| Yerel sürüm dalı | `C:/Users/berke/release-lci` @ `8163a79` (bit birebir) | worktree + rev-parse |
| origin | `feature/evidence-repairs-v1` @ `8163a79` **push edildi**; `feature/quant-evaluation-v1` hâlâ `9be55d1` | `git ls-remote` |
| Commit zinciri | `9be55d1` (hassasiyet) → `8c99b8f` (V1, sert kapılar) → `8163a79` (V1.1, yumuşak ceza) | `git log` |
| Servisler | worker + dashboard `active`, `ready=200`, HEALTHY | systemctl + /health |
| Defter | seq=43, **14 açık**, **29 kapanış**, equity 95.95 (100.00 başlangıç) | futures_ledger.json |
| Mod | PAPER / PAPER_RESEARCH, `live_trading=false`, gateway=paper | health.json + config |

**Dondurulmuş veri anlık görüntüsü** (tüm ajanlar aynısını kullanır):
`.../259cbfe4-.../snapshot` — 120 MB; entry_snapshot.jsonl 93.6 MB / 2811 satır / 2797 aday,
position_path.jsonl 29.6 MB, futures_ledger.json, learn_v2.json, learned_closes.jsonl, coin_heads.json,
decision_funnel.json, exit_eval.json, config.yaml (efektif üretim config'i).

---

## 1. Kapanan doğrulamalar

| Kilometre taşı | Sonuç | Kanıt |
|---|---|---|
| İlk doğal girişte `meta.precision` | **KAPANDI** | F00040–F00043 hepsinde `rule_class=VERIFIED_VENUE`, `source=binance_api` |
| İlk doğal kapanışta `learning_keys` | **KAPANDI** | F00039 (13:24Z) ve F00036 (15:22Z): `semantics=v2-multileaf`, `regime_at_close` dolu |
| Yalnız bir kez öğrenme | **KAPANDI** | `learned_closes.jsonl` 29 satır, yinelenen `trade_id` YOK |
| `p_win = 0.5 + 0.25×confidence` kapıya sızıyor mu | **DOĞRULANMADI (kusur bu sürümde YOK)** | `head.py:354` önseli yazıyor ama `engine_v3.py:898-899` `if d: d.p_win = b.p_win` KOŞULSUZ çalışıyor ve öğrenici değerini yazıyor. Defterde kayıtlı p_win değerleri 0.197–0.578, formülün üretemeyeceği (<0.5) aralıkta. Ajan B veriyle bağımsız teyit ediyor. |

`learning_keys` içinde `leaf: None` — küçük bir boşluk, not edildi, ekonomik etkisi yok.

---

## 2. Bulunan üretim kusuru: worker OOM (KANITLI, ONARILDI)

**Belirti:** `tradingbot-worker.service: Main process exited, code=killed, status=9/KILL` /
`Failed with result 'oom-kill'` — 2026-09-09 15:57:25Z. cgroup `MemoryMax=4 GiB`, `MemoryPeak=3.87 GiB`.
Yeniden başlayan süreç 5 saatte 3.45 GB RSS'e çıktı; turlar arasında **sabit**, tur içinde sıçrıyor.

**Kök neden (ölçüldü, tahmin değil):**
1. `entry_snapshot.jsonl` 93.6 MB'a büyümüştü (rotasyon yapılandırılmamış, `max_lines=0`).
2. `EntrySnapshotStore.iter_hot_rows()` dosyanın TAMAMINI `read_text()` ile tek string, sonra
   `splitlines()` ile TÜM satırları ayrı liste olarak belleğe alıyordu.
3. `by_candidate()` 2797 satırın tamamını derin iç içe sözlük olarak tutuyordu.
4. Motor bunu **tur başına İKİ KEZ** çağırıyordu (`engine_v3.py:3065` kârlılık deneyi,
   `engine_v3.py:3455` giriş değerlendirmesi).
5. `PositionPathStore.iter_rows()` aynı deseni 29.6 MB dosyada tekrarlıyordu.

**Gerçek üretim dosyasıyla ölçüm:**

| Yol | Tepe bellek |
|---|---:|
| Eski, tek `by_candidate()` çağrısı | 631.1 MB |
| Eski, tur başına iki çağrı | **889.1 MB** |
| Yeni (akış + memo), tur başına iki çağrı | **258.2 MB** |
| Yeni, saf akışla tam gezinme (93.6 MB dosya) | **0.4 MB** |

**Tur başına kazanç: 630.9 MB (%71).** Dosya büyüdükçe fark artar.

**Onarım** (`tradingbot/learn/entry_snapshot.py`, `learn/position_path.py`, `engine_v3.py`):
- `iter_hot_rows()` ve `iter_rows()` satır satır akışa çevrildi; anlam birebir aynı.
- `by_candidate()` `(mtime_ns, size)` imzasıyla memolandı → tur başına TEK ayrıştırma.
  Çağırana **sığ kopya** verilir: bir çağıran dönen sözlüğe eski bellek kayıtları EKLİYOR, memo kirlenmemeli.
- Arşiv yolu (`include_archive=True`) memolanmaz; sıcak imza onu korumaz.
- `drop_hot_cache()` tur sonunda çağrılır → ayrıştırılmış grafik turlar arasında bellekte TUTULMAZ.

**Testler:** `tests/test_snapshot_memory_v1.py` — 11 test. Akışın davranışsal kanıtı (tepe bellek dosya
boyutunun %10'unun altında), memo bir kez ayrıştırma, dosya değişince geçersizleşme, çağıran mutasyonunun
memoyu kirletememesi, `drop_hot_cache`, arşiv yolunun memolanmaması, `known_ids`/tekilleştirme korunması.

**Kalan risk:** rotasyon hâlâ kapalı; dosya büyümeye devam edecek. Arşiv altyapısı var
(`entry_snapshot_archive`, kayıpsız, arşiv-önce). Ayrı iş olarak açık.

---

## 2b. EN BÜYÜK EKONOMİK SORUN: ekonomik kapı kalibre olasılığı hiç kullanmadı (KANITLI, ONARILDI)

**Bu, önceki oturumun bildirdiği NATGAS iddiasının doğrulanmış hâlidir. İlk okumam YANLIŞTI:**
girintiyi doğru gördüm ama `_assess_opportunities` ile `d.p_win` atamasının SIRASINI kontrol etmedim.

**Mekanizma** (`8163a79` ve ondan önceki 12 `code_sha`'nın tamamında):
1. `engine_v3.py:850` — ekonomik kapı `_assess_opportunities(decisions, briefs)`.
2. `engine_v3.py:891-899` — öğrenici döngüsü `d.p_win = b.p_win` — kapıdan **48 satır SONRA**.
3. Kapının içindeki `engine_v3.py:1840-1841` `if d.p_win:` dalı, yorumu *"kalibre model tahmini
   onceliklidir"* diyor. O anda `d.p_win` hâlâ `head.py:354`'ün önselidir:
   `0.5 + 0.25*confidence*(|consensus| ≥ 0.22)` — **daima ≥ 0.5, yani hiçbir zaman falsy**.
4. Sonuç: `opportunity.hierarchical_expectancy`'nin kalibre olasılığı **her adayda eziliyordu**.
   Kod kendi yorumunun tersini yapıyordu.

**Ölçüm** — kapının kimliğinden geri çözüm: `p = (gross_expectancy_r + |L|) / (W + |L|)`.
Üç bağımsız hesap (ben + A ajanı + B ajanı) aynı sonuca vardı:

| Kapının kullandığı olasılık | aday | pay |
|---|---:|---:|
| HEAD önseli `0.5+0.25*conf` | **2797** | **%100** |
| kayıtlı istatistiksel `p_win` | 0 | %0 |
| başka | 0 | %0 |

* Ortalama şişme: `p_implied − p_win` = **+0.338**; beklenti şişmesi **+0.988R/aday**.
* 2797 adayın **2113'ü (%75,5)** kendi kayıtlı olasılığında NEGATİF brüt beklentiye sahip.
* **NATGAS, 2026-09-08T13:51Z, KABUL EDİLDİ:** kapı `p=0.625`, istatistiksel `0.342`.
  `0.625×1.525382 − 0.375 = 0.578364` (kayıtla 6 hanede birebir). Gerçek olasılıkla
  brüt beklenti **−0.136R** → işlem açılmamalıydı. İkinci NATGAS (09-03): 0.630 / 0.319 → −0.100R.
* B ajanının karşı-olgusalı: gerçek `p_win` ile işlem yapılabilir aday **%91,7 → %1,9**;
  tam boyut alan aday **%83,3 → %0**; **kabul edilen 14 adayın 14'ü de** açılmazdı.
* Kusur `3c4c506`'dan (2026-08-21, ekonomik kapının eklendiği commit) beri var. Kapı kalibre
  olasılığı **bir kez bile** kullanmamış.

**Onarım:** öğrenici döngüsü kapıdan ÖNCEYE alındı. `legacy_chief.risk_mode` (öğrenme özelliği
`btc_mode`/`btc_align` için gerekli) ayrı ve erken bir `chief_mgr.decide(...)` çağrısından alınıyor —
`market_risk_mode` yalnız BTC rejimi + verdict sayılarından türer (`chief.py:93-99`), `d.opportunity`ye
bağımlı DEĞİLDİR; `ChiefPortfolioManager.decide` SAFTIR (kararları mutasyona uğratmaz), iki kez
çağrılabilir. Yetkili chief kararı kapıdan sonra kurulur, sıralama doğru edge ile yapılır.
**Yan kazanç:** `_influence_log` artık kapı anında dolu — B ajanının bulduğu bayat/çapraz-tur
`decision_changed_by_learning` kusuru da kapandı.

**Testler:** `tests/test_gate_uses_calibrated_pwin.py` — 6 test. Sıra değişmezi (kapı anında
`d.p_win` öğrenici değeri), kapı çıktısının kimliğinden geri çözümün öğrenici değerine eşitliği,
chief sıralamasının bozulmaması, `risk_mode`'un erken ve yetkili değerinin AYNI olması, önselin
asla falsy olamayacağının kanıtı.

**Beklenen davranış değişikliği:** işlem sayısı çok azalacak. Kabul ölçütleri (önceden kayıtlı)
`docs/ACCEPTANCE_CRITERIA_GATE_ORDER.md`. Azalma **başarısızlık değildir**; ölçülebilir kenarı
olmayan bir sistemde doğru işlem sayısı sıfıra yakındır.

**Test fikstürü etkisi (dürüstlük notu):** bu değişiklik 5 testi düşürdü. Hiçbir iddia
gevşetilmedi; beşinin de öznesi olasılık modeli değildi (yinelenen sinyal engellemesi, araştırma
yaşam döngüsü, spot/futures risk kovası). `TE._engine`'e açık `p_win` parametresi eklendi ve bu
testler olasılık modelinden **yalıtıldı**. Üretim yolu değişmedi.

---

## 3. Ajan görev dağılımı (hepsi aynı SHA `8163a79` + aynı dondurulmuş anlık görüntü)

| Ajan | Kapsam | Yazma yetkisi | Durum |
|---|---|---|---|
| A — Veri ve muhasebe | Cüzdan/equity mutabakatı, ücret+funding ayrıştırma, kısmi çıkış muhasebesi, **−1R varsayımının gerçek kayıp dağılımıyla uyumu**, etiket/zaman bütünlüğü, açık pozisyon ekonomisi | salt okunur | çalışıyor |
| B — Olasılık ve karar | NATGAS iddiasının veriyle kesin cevabı, p_win kalibrasyonu (Brier/AUC/permütasyon), ödeme modelinin gerçek dağılımla karşılaştırması, **olay/ufuk tutarlılığı**, boyutlandırma zinciri, denetlenebilirlik | salt okunur | çalışıyor |
| C — Replay sadakati | Kaydedilmiş kararları ve nakit sonuçları **belgelenmiş toleranslarla** yeniden üreten harness + sadakat raporu | kendi worktree'si `C:/Users/berke/wt-replay`, dal `research/replay-fidelity-v1` | çalışıyor |
| D — Portföy ve yürütme | Faktör yığılması (kripto/hisse/emtia), risk bütçesi ve kapasite, **aday sıralamasının bütçeyi tüketmesi**, yürütme sürtünmesi, çıkış davranışı, veri/işlenebilirlik kapıları | salt okunur | çalışıyor |
| E — Bağımsız doğrulayıcı | Sızıntı, aşırı uyum, yanlış karşılaştırma, yama hataları, dağıtım kanıtı. **Kendi yazdığını onaylamaz.** | salt okunur | A–D bittikten sonra |

GPT‑6 Astra: bu oturumda yetkili bir araç/API/CLI bağlantısı **yok** → "GPT‑6 Astra incelemesi yapılmadı".
Başka model Astra diye sunulmayacak.

---

## 3b. Ajan bulguları — sıradaki iş listesi (henüz ONARILMADI)

Kaynak: A (muhasebe), B (olasılık), D (portföy/yürütme). Etki sırasına göre.

| # | Bulgu | Ölçülen etki | Kategori |
|---|---|---|---|
| N1 | **Tokenize hisse kesiti**: 10 işlemde **0 kazanç**, hepsi `stop`, ort. −1.098R | **−5.395 USDT = toplam gerçekleşen zararın %77'si**; Welch t=−3.17 | B (strateji) |
| N2 | **Açık risk MEVCUT stoptan ölçülüyor** → başa-baş/TP1 taşıması bütçeyi sınırsız geri dönüştürüyor. Adet, marj kullanımı (%66,4) ve likidasyon tamponu PAPER'da hiç uygulanmıyor | 6 pozisyon **79,82 USDT nominali 0,106 USDT bütçeyle** (%1,8) taşıyor; defter 174,9 USDT = özkaynağın 1,82 katı | A (risk mimarisi) |
| N3 | `avg_loss_r` **sabit −1.0** (`opportunity.py:174,200`), 2797/2797 adayda; gerçekleşen −1.0774 (risk ağırlıklı −1.0579) | 0,897 USDT bütçe aşımı; +0,028R kenar şişmesi | A (kanıtlı kusur) |
| N4 | **Varsayılan 0,01 fiyat adımı** girişleri aleyhte yuvarladı (167 baz puana kadar) | **0,719 USDT = gerçekleşen kaymanın %73'ü, toplam zararın %10,2'si**; TRX plan geometrisi 2.00R→1.28R. `8163a79`'da ONARILDI ama 14 açık pozisyonun 10'unda kalıcı | A/D (onarıldı, kalıntı var) |
| N5 | Aynı kusur **MFE başa-başını sessizce devre dışı bırakıyor**: TRX'te 0,01 adım = 294 baz puan, hesaplanan başa-baş mark'ın üstüne düşüyor, `_right_side` reddediyor, kural HİÇ ateşlenmiyor | ~20 USDT nominalde koruma yok | A (kanıtlı kusur) |
| N6 | **Risk kovası açgözlü, headroom'a uydurmuyor**: dolu kovada en yüksek kenar reddedilirken en küçük riskli aday geçiyor | 7 döngü / 23 çift; AVAX **0,0062R** kenarla açıldı, aynı döngüde 0,37–0,66R'lik 7 aday bloklandı | D (tasarım) |
| N7 | **Risk onaylı adayların %55'i (17/31) defter tarafından reddedildi**, sebep hiçbir yere yazılmıyor (`exec_reject` kalıcı değil) | 7 günde 17 kayıp giriş; kök neden BELİRLENEMEDİ | D (gözlenebilirlik) |
| N8 | `est_slippage_pct` / `depth_ratio` / `liquidity_ok` **2797 kaydın %100'ünde boş**; red-team ve kaldıraç taban kapıları `is not None` korumalı, yani bilinmeyende **AÇIK kalıyor**; bilinmeyen ticker yaşı TAZE sayılıyor | adayların %31,1'i spread ölçümü olmadan değerlendirildi | D (fail-open) |
| N9 | `bars_held` **%22,4 fazla sayıyor** (`last_bar_seen` kalıcı değil, sembol evreni değişince sahte ilerleme) | doğrudan USDT yok; öğreniciye giden tutma süresi özellikleri bozuk | A (kanıtlı kusur) |
| N10 | `exit_reason="başa-baş stop"` etiketi **gap-through dolumlarına da** koşulsuz veriliyor | ZEC F00007 girişin %3,7 altında doldu, başa-baş sayıldı; çıkış taksonomisini bozuyor | A (kanıtlı kusur) |
| N11 | **Kayıt bütünlüğü:** `entry_snapshot.jsonl` kendi alanlarından yeniden üretilemiyor — `p_win` kapı SONRASI, `gross_expectancy_r` kapı ÖNCESİ değerden; 2797/2797 tutarsız | replay bu kayda dayanamaz | B (kanıtlı kusur) |
| N12 | `min_pwin: 0.45` config'i **yalnız v1 legacy motorda** uygulanıyor (`engine.py:160`), v3 yolunda ölü | istatistiksel olasılık hiçbir yerde kapı değil | B |

**Not:** N11'in bir kısmı kapı sırası onarımıyla kendiliğinden düzelir (artık ikisi de aynı
kalibre değerden türer); dağıtım sonrası doğrulanacak.

### 3c. Replay sadakati kuruldu (C ajanı) — optimizasyonun ön koşulu tamam

Dal `research/replay-fidelity-v1`, commit `3acd7b6` (birleştirilmedi, push edilmedi).
`tradingbot/replay/fidelity.py` + 24 test. Kapanmış 29 işlemin tamamı gerçek 1m barlarla,
defterin KENDİ muhasebe sınıflarıyla yeniden oynatıldı.

| Alan | Sonuç | Savunulan tolerans |
|---|---|---|
| giriş dolumu / miktar / ücret / likidasyon fiyatı | **29/29 bit-birebir** | 0 |
| `exit_reason` | **29/29** | 0 |
| `r_multiple` | 28/29 ≤0.10 | ±0.10R |
| `net_pnl` | 29/29 ≤0.15 USDT | ±0.15 USDT |
| `closed_at` | 29/29 ≤1 saat | ±1 saat |

**GÜRÜLTÜ TABANI: toplam sapma +0.295 USDT / +0.332R.** Bir strateji değişikliği 29 işlemlik
toplamı **0.33R'den fazla** oynatmıyorsa bu temelde ÖLÇÜLEMEZ. Bu sayı, ileride yapılacak her
karşılaştırmanın anlamlılık eşiğidir.

**1m bar ZORUNLU:** ZRO işleminde 1h çözünürlük `BE_STOP (+0.14R)`, 1m çözünürlük `TP2 (+2.37R)`
veriyor — yalnız çözünürlükten **2.22R hata**. Aynı bar içinde stop+hedef çakışması 238.181 adet
1m barın hiçbirinde olmadı (1R/2R geometrisi buna izin vermiyor).

**C ajanının üretim muhasebesinde bulduğu kusurlar (onarılmadı, sıraya alındı):**

| # | Bulgu | Etki |
|---|---|---|
| N13 | **`mfe_pct` GÜVENİLİR DEĞİL (en ciddisi)** — 3 kayıtlı MFE fiziksel olarak imkânsız (fiyat oraya hiç gitmedi: STX, SUI, SPCX); 10 tanesi eksik ölçüyor (KORU 3.68% kayıtlı / 8.18% gerçek). Sebep: yol tiklerinin %79'u `last_only`, yani 60 sn nokta örneği, bar uçları yok | **Canlı `breakeven_at_mfe_r` kuralı ve tüm giveback ölçümleri tam bu alana bakıyor.** Eksik ölçüm kuralı GEÇ tetikler (muhafazakâr); fazla ölçüm ERKEN tetikler (stop erken başa-başa gider) |
| N14 | **Funding sessizce sıfır** — piyasa ajanı oran veremediğinde 10/29 işlemde tam 0.000000 kaydedildi; gerçek Binance funding'i +0.0168 USDT | maliyet eksik ölçülüyor |
| N15 | **Tek anlık funding oranı tüm kaçırılmış settlement'lara uygulanıyor** — STX +0.1446 kayıtlı / +0.0401 gerçek (3.6×); 29 işlem toplamı +0.0674 kayıtlı / +0.0045 gerçek | funding kalemi 15× şişik |
| N16 | `worst_case` **ölü config** — saklanıyor, serileştiriliyor, `tick()` içinde HİÇ okunmuyor; likidasyon>stop>TP sırası sabit kodlanmış | belgelenen davranış ile kod ayrışmış |
| N17 | **Çıkış zaman damgası ile çıkış fiyatı tutarsız** — 20 gap-through çıkışın 5'inde `closed_at` anındaki 1m barda o fiyat yok (±1–2 dk sapma). Kök neden: `tour()` `now`'u tur başında alıyor, `_marks()` sonra çalışıyor; motor bunu `position_path` için `snap_now` ile düzeltmiş, defter tick'i hâlâ bayat `now` kullanıyor | replay/etiketleme zamanı bozuk |

**C ajanının kendi sınırı (kendi ifadesiyle):** her işlem izole defterde oynatılıyor, portföy
etkileşimi (marj rekabeti, pozisyon tavanı, giriş sıralaması) yeniden üretilmiyor. Kapanmış işlem
sadakati için yeterli, ancak **girişleri değiştiren** bir strateji değişikliği için YETERSİZ.

---

## 3d. Bağımsız doğrulama (E ajanı) — kendi onarımlarımda KUSUR BULDU

E ajanı iki değişikliği adversaryal olarak sınadı. **Onaylananlar:** kapı kusuru iddiası
(2797/2797, artık kalanı 0.0000), +0.988R şişme (birebir), düzeltmenin eksiksizliği (her tüketici
tek tek sayıldı), `chief_mgr.decide`'ın saflığı (ampirik parmak izi: `decisions_mutated=False`),
erken/yetkili `risk_mode` eşitliği, bütün bellek ölçümleri (374.3 / 631.1 / 889.1 / 0.4 / 258.2 MB),
memo güvenliği (4 tur boyunca 56 satır nesnesi, 0 yerinde mutasyon), imza yeterliliği.

**Bulduğu ve ONARDIĞIM kusurlar:**

| # | Kusur | Onarım | Doğrulama |
|---|---|---|---|
| E-1 | `drop_hot_cache()` **iki tüketicinin ARASINDA**: son tüketici (`_run_profitability_experiment`) yeniden ayrıştırıyor VE memo turlar arası ~258 MB kalıcı kalıyordu — yani yorumun söylediğinin tersi, temel sürüme göre **gerileme** | Bırakma son tüketiciden SONRAYA alındı; ayrıca tur BAŞINDA savunmacı bırakma (`tour()` genelinde try/finally yok, istisna atan tur memoyu asılı bırakabilirdi); `_drop_entry_snapshot_cache()` istisna sızdırmaz | `test_06`: çağrı sırası kaydediliyor, son bırakma son tüketiciden sonra olmalı; `test_06b`: asılı memo tur başında bırakılır |
| E-2 | `_hot_lines()` dönüştürülmemişti: `retention_stats()` üzerinden **her turda 374.3 MB** tepe | `_hot_line_count()` eklendi (akışla sayar); `retention_stats()` ve rotasyonun kapalı dalı onu kullanıyor | `test_05`: ölçülen **374.3 MB → 0.3 MB**, sonuç `_hot_lines()` ile birebir aynı |
| E-3 | `test_05` **BOŞTU**: `features_from_brief` tur içinde iki yerden çağrılıyor (öğrenici döngüsü ve `_execute`); spy SON çağrıyı kaydedince `risk_mode` yanlış sabitlense bile test geçiyordu | İLK gözlem esas alınıyor | `legacy_chief.risk_mode = "NOTR_HARDCODED"` enjekte edildi → test artık **DÜŞÜYOR** (doğrulayıcının kullandığı tam senaryo) |
| E-4 | `test_01` totolojiydi | Üretim formülüne bağlandı ve totoloji olduğu docstring'de açıkça yazıldı | — |
| E-5 | **Hiçbir test kapının `_execute`'tan önce çalıştığını iddia etmiyordu** — kapının ve chief'in emir yolundan sonraya taşındığı sahte bir "onarım" 6 testin hepsini geçerdi ve kapı tamamen devre dışı kalırdı | `test_05b_gate_runs_before_execution`: çağrı sırası + `_execute` anında `d.opportunity` dolu mu | — |

**Onarım sonrası ölçüm (gerçek 93.6 MB üretim dosyası):**

| Yol | Eski tepe | Yeni tepe |
|---|---|---|
| `retention_stats()` (her tur) | 374.3 MB | **0.3 MB** |
| tur içi iki `by_candidate()` | 889.1 MB | **258.2 MB** |
| turlar arası tutulan grafik | — | **yok** (`_bc_cache is None`) |

**Tur başına toplam tepe azalması ≈ 1.0 GB.**

**E ajanının kabul ettiğim uyarıları (onarılmadı, kayda geçti):**

* **OOM kök nedeni "abartılı"**: 889 MB, 4 GiB tavanın %22'si. Dokunulmayan diğer tur içi sıçramalar:
  `engine_v3.py:3229` `path_rows` **127.4 MB**, `position_path.paths_by_trade()` **129.7 MB**
  (tur başına iki kez). Bunlar da akışa çevrilmeli — sıraya alındı (N18).
* **Akışın TEK gerçek anlam farkı**: `str.splitlines()` U+2028/U+2029/U+0085 karakterlerinde de
  böler, dosya iterasyonu bölmez. `append()` `ensure_ascii=False` ile bunları HAM yazıyor.
  Yani bu karakterleri taşıyan bir satır eskiden **sessizce düşüyordu**, artık korunuyor.
  Yeni davranış daha doğru (sessiz veri kaybı onarıldı) ama "birebir aynı satırlar" iddiam
  yanlıştı. Dağıtım sonrası sıcak satır sayısı bir miktar artabilir — zararsız.
* **Testler artık gerçekçi olasılıkla giriş yolunu kapsamıyor**: 5 regresyon `p_win=0.65`
  sabitlenerek çözüldü. Meşru (o testlerin öznesi olasılık değil) ama bu boşluk açıkça duruyor.
* `rotate()` `_bc_sig`/`_bc_cache`'i açıkça temizlemiyor; imza yakaladığı için doğru çalışıyor.

---

## 4. Sıradaki adımlar

1. Bellek onarımını tam paket + lint sonrası PAPER'a dağıt (kanıtlı mühendislik kusuru → A kategorisi).
2. A/B/D raporlarını birleştir, **en büyük tek ekonomik sorunu** seç.
3. En fazla üç önceden tanımlanmış challenger; katkıları ayrı ölçülür, karıştırılmaz.
4. Kabul ölçütleri **sonuçlara bakmadan** yazılır ve buraya kaydedilir.
5. E ajanı bağımsız doğrular; ancak sonra dağıtım.

**Değiştirilmeyecekler:** gerçek para modu, sermaye/kaldıraç/toplam risk limitleri, kanonik defter,
açık pozisyonların giriş/miktar/stop/hedefleri, geçmiş dolumlar ve öğrenme sonuçları.

---

# OTURUM 2026-09-10 — bar provenansı, funding, kayıp modeli, maruziyet, bellek

Taban: `1c4cba1` (VPS'te çalışan sürüm, doğrulandı). Bu bölüm **bu oturumda kaynaktan yeniden
türetilmiş** bulguları taşır; önceki oturum raporları yalnız başlangıç ipucu sayıldı.

## 0. Doğrulanmış zemin (yeniden ölçüldü)

| Ne | Değer | Nasıl |
|---|---|---|
| Dağıtılmış sürüm | `1c4cba1`, `origin/feature/evidence-repairs-v1` ile aynı | `git ls-remote`, `git merge-base --is-ancestor` |
| Zincir | `9be55d1` → `8c99b8f` → `8163a79` → `1c4cba1` (hepsi ata) | `git merge-base` |
| **Dal ayrımı** | `research/profitability-acceleration-v1` dağıtım kodunu **İÇERMEZ**; yalnız dağıtım belgelerini taşır. Ortak ata `12db804c`. Çalışma `feature/evidence-repairs-v1` üzerinde sürdürüldü. | `git log --oneline A --not B` |
| Test tabanı | 2032 passed / 22 skipped @ `1c4cba1` | `pytest tests -q` |
| VPS erişimi | **Bu oturumdan SSH YOK** — `~/.ssh/trading2_ovh` parola korumalı (`aes256-ctr`), agent yok, `BatchMode` reddedildi | `ssh -o BatchMode=yes` → `Permission denied (publickey)` |
| GPT‑6 Astra | **Yetkili bağlantı YOK** (`list_connectors` boş) → **Astra incelemesi YAPILMADI**. Bağımsız denetim Claude adversaryal ajanıyla yapıldı. | connector listesi |
| Binance public API | erişilebilir (`fapi.binance.com`) → funding/1m/1h barlar gerçek veriyle çekildi | doğrudan istek |

## 1. Kapanan bulgular

| # | Bulgu | Sonuç | Kanıt |
|---|---|---|---|
| **N13** | `mfe_pct` güvenilir değil | **ONARILDI** — kök neden `_marks()`'ın giriş öncesi 1h barı; 5 kayıt tam olarak o barın ucuna eşit; aynı uçlar stop/hedef de tetikleyebiliyordu | `docs/BAR_PROVENANCE_V1.md` |
| **N15** | funding "15× şişik" | **BÜYÜKLÜK DÜZELTİLDİ + ONARILDI** — oran birbirini götüren iki toplamın oranı; gerçek büyüklük 0.209 USDT mutlak hata (%3). Kök neden `static_rates`, kusur yalnız canlı tur yolunda | `docs/FUNDING_AND_EXPOSURE_MEASUREMENT_V1.md` |
| **N14** | funding sessizce sıfır | **AÇIKLANDI** — 10 sıfırın 4'ü gerçekten 0 oranlı; kalanlarda oran hiç gelmediği için `accrue` fail-closed bekledi ve pozisyon kapandı. Onarım kapsıyor | aynı belge |
| **N3** | `avg_loss_r` sabit −1.0 | **ONARILDI** — 22 kayıpta ortalama 1.0774, %95 GA [1.042, 1.113], 1.0 aralığın DIŞINDA. Learner'ın kendi kapanışlarından daraltmalı kestirim: **1.0655** | `docs/FUNDING_AND_EXPOSURE_MEASUREMENT_V1.md` §2 |
| **N2** | açık risk mevcut stoptan | **AYRIŞTIRILDI** — kod kusuru DEĞİL, kapı tanımını doğru uygular; "sınırsız" da kanıtlanmadı (bütçe 5.83/6.00 ile bağlayıcı). Kanıtlı olan: profil yorumu margin/liq kapılarını sayıyor, ikisi de `None` yani hiç çalışmıyor. Gözlem eklendi, **politika değişmedi** | aynı belge §3 |
| — | OOM | **ÖLÇÜM ALTYAPISI KURULDU, DOĞRULAMA AÇIK** — kabul ölçütü sonuçlara bakılmadan yazıldı | `docs/MEMORY_VERIFICATION_V1.md` |

## 2. YENİ bulgu — kapı sırası onarımının hikâyesi eksikti

`_assess_opportunities` kalibre `p_win`'i kullanıyor ama `avg_win_r`'yi **başka** bir olasılıkla
geri çözülmüş hâliyle koruyor. Ölçüldü (2773 aday): kalibre olasılık hiyerarşiğin **%47**'si,
edge farkı ortalama **−0.266R**, `tradeable` kararı **%24** adayda değişiyor. Tutarlı tek olasılık
kullanılsaydı **698** aday işlem yapılabilir kalırdı, bugünkü **32** değil.

Yani `1c4cba1`'in "işlem yapılabilir oran %91.7 → %1.9" sonucunun önemli kısmı doğru olasılığın
kullanılmasından değil, **tutarsızlığın kendisinden** gelir. Onarım yanlış değildir; kaydı eksikti.

**Bilerek onarılmadı:** saf çözüm (`W`'yi kalibre `p` ile geri çözmek) `w=1`'de kalibre olasılığı
formülden sadeleştirir ve `1c4cba1`'in kapattığı kusuru geri açar. Doğru onarım kazanç büyüklüğünü
de **doğrudan ölçmektir** — kayıp büyüklüğünde bu turda yapılanın ikizi. Ayrıntı ve öneri:
`docs/PWIN_WIN_MAGNITUDE_INCONSISTENCY.md`.

## 3. Hâlâ açık (bu turda kapanmadı)

| # | Konu | Neden açık |
|---|---|---|
| N13-b | MFE **eksik** ölçümü (`last_only` tikler; KORU −4.50 puan) | Muhafazakâr yönde hata (kural geç tetikler). Ayrı iş. |
| — | İki açık pozisyonun şişik MFE'si (F00038, F00043) | `mfe_pct` koşan maksimum; defter yeniden yazılmaz. Değer artık **büyüyemez**. |
| — | Funding takvimi 8 saate sabit kodlu (`FUNDING_HOURS_UTC`) | 4h/1h funding aralıklı sembollerde eksik tahakkuk — onarılan hatadan büyük olabilir. |
| — | Funding önbellek ıskası anlık orana düşer ve watermark ilerler | Tek-settlement durumunda bugünkünden kötü olmama koşulunun bilinçli bedeli. |
| N16 | `worst_case` ölü config | Davranış doğru, belge/kod ayrışması duruyor. |
| N18 | `path_rows` ~127 MB + `paths_by_trade()` ~130 MB | Akışa çevrilmedi; OOM payı hâlâ dar. |
| N7 | `exec_reject` kalıcı değil | Risk onaylı adayların %55'i defterce reddediliyor, sebep yazılmıyor. |
| N8 | likidite alanları %100 boş, kapılar `is not None` ile fail-OPEN | Ayrı iş. |
| — | `p_win` / `avg_win_r` tutarsızlığı | Yukarıda; kendi ön-kayıtlı ölçütleriyle ayrı challenger. |
