# WIP HANDOFF — PROFITABILITY RESEARCH ACCELERATION V1 (çevrimdışı, tamamlandı)

Bu dosya yeni bir Claude oturumunun **yeniden inşa etmeden** devam etmesi içindir.
Ayrıntılı sonuç: `docs/PROFITABILITY_RESEARCH_ACCELERATION_V1.md`.

## 0. Verdict

`LEARN_COUNT_REPAIR_RELEASE_V4_PREPARED_NOT_DEPLOYED`

(önceki: `LEARNING_COUNT_INTEGRITY_V3_REPAIRED_LOCAL_NO_DEPLOY`)

(önceki: `PROFITABILITY_DUAL_EDGE_V2_COMPLETE_LOCAL_NO_DEPLOY`)

(önceki: `PROFITABILITY_RESEARCH_ACCELERATION_V1_COMPLETE_LOCAL_NO_DEPLOY`)

Araştırma hattı yazıldı, **çalıştırıldı** ve ölçülmüş sonuç üretti. Üretimde hiçbir şey
değişmedi; push/deploy YAPILMADI.

## 1. Konum ve sürüm

| | |
| --- | --- |
| Repo | `C:\Users\berke\Trading bot` → `github.com/CoskunerBerke/trading2` |
| Araştırma branch'i | `research/profitability-acceleration-v1` |
| Taban (üretim) | `feature/quant-evaluation-v1` @ `12db804c7301dbebe337813e20e44c9e967d260b` |
| VPS app HEAD | `12db804…` (değişmedi, salt okunur doğrulandı) |
| Bu iş için commit | aşağıdaki §6 |
| Testler | `1947 passed / 22 skipped / 0 failed` (taban 1921/22 + 26 yeni) · ruff temiz |

Obsidian vault değişiklikleri (63 M + 144 ??) **korundu**, commit'e alınmadı.

## 2. Veri: sınırlı salt okunur export

| | |
| --- | --- |
| Kaynak | VPS `/opt/tradingbot/data/state` (ubuntu + `sudo -n`, servisler DURDURULMADI) |
| Cutoff (UTC) | `2026-09-07T21:16:41Z` |
| Yerel kök | `C:\Users\berke\research\pfres_v1` |
| Bundle sha256 | `29ea315f2111898e805898cec9173f024dcc0f3e50a5b9258c92c8eda78376c9` (18 737 206 B) |
| Manifest | `pfres_v1/MANIFEST.txt` — her dosyanın sha256'sı, JSONL bayt sınırı, JSON kararlılık kontrolü |
| VPS temizliği | `/tmp/pfres_export*` **silindi** (doğrulandı) |

Yöntem: JSONL dosyaları `stat -c %s` ile bayt-sınırlı okundu ve eksik son satır atıldı (eşzamanlı
append satırı yırtmaz); JSON dosyaları okuma öncesi/sonrası sha256 ile karşılaştırıldı — **hepsi
`SAME`**.

Yeniden export gerekirse script: `scratchpad/vps_export.sh` deseni — `sudo -n bash` ile
çalıştırılır, çıktı `ubuntu`'ya chown edilir, tar `ubuntu` olarak alınır (root tar `/tmp`'de
izin hatası verdi).

## 3. Yeni kod (yalnız araştırma)

```
tradingbot/research/__init__.py     kanıt sınıfları + etiketler
tradingbot/research/protocol.py     dondurulmuş protokol, research_id, attempted_variants
tradingbot/research/dataset.py      envanter, kapsam, ekonomi mutabakatı
tradingbot/research/bars.py         önbellekli Binance public kline köprüsü + eşleme doğrulama
tradingbot/research/exit_lab.py     kanonik kapanış → quant/exit_challenger köprüsü + eşleşmiş GA
tradingbot/research/entry_lab.py    fırsat havuzu, skor izi, ileri simülasyon, kalibrasyon, null test
tradingbot/research/lessons.py      araştırma dersi kataloğu (üretimden AYRI)
tradingbot/research/run.py          TEK komut, JSON + Markdown, önbellek
tests/test_research_acceleration_v1.py   26 test
```

Üretim dosyalarına **tek satır** dokunulmadı.

## 4. Tekrarlanabilir komut

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out"
```

* Soğuk koşu **220.9 s** (113 bar isteği, 35 447 bar indirildi). Sıcak önbellekte `--force`
  ile **~3 s**. Değişiklik yoksa `RESEARCH_CACHE_HIT` (**~1.5 s**), sonuç/ders çoğaltılmaz.
* `--offline` ağ isteği yapmaz. `--force` yeniden hesaplar. `--out` bir `state` dizini
  içeriyorsa fail-closed reddedilir.
* Çıktı: `out/research_report.json`, `out/research_report.md`, `out/research_lessons.json`,
  `out/cache/bars_15m/*.json`.
* `research_id = d0cf2a0ca55860ad` (girdi sha256 + kod SHA + cutoff'tan deterministik).

## 5. Ölçülen sonuçlar (özet)

| Bulgu | Kanıt sınıfı | Sonuç |
| --- | --- | --- |
| RL-01 ekonomik kapı `p_win_prior` kullanıyor, model `p_win`'i GÖRMÜYOR | POINT_IN_TIME + kaynak izi | **256/256** kimlik; `engine_v3.py:827` < `:875` |
| RL-02 iki olasılık skoru da ayrıştırıcı değil | RETROSPECTIVE | Brier 0.358 / 0.261; kova sıralaması TERS |
| RL-03 çıkış alternatifleri gürültüden ayrılmıyor | CANONICAL_OBSERVED | +0.103 / +0.119 / +0.164 R; GA'lar sıfırı içeriyor |
| RL-04 sıralama rastgeleden ayrılmıyor | RETROSPECTIVE | 200 permütasyon; yüzdelik 0.945 / 0.365 / 0.230 / 0.575 |
| RL-05 muhasebe tam mutabık | CANONICAL_OBSERVED | artık **-0.0** |

Kapsam: 25 kapanış (3'ünde değişmez giriş snapshot'ı, 25'inde karar anı planı, 3 tam yol),
11 açık pozisyon, 404 tekil giriş fırsatı.

**YAPILAMAYAN:** seçicilik challenger'ı. Sızıntısız üç yollu fold en az 8 gün ister; giriş
kaydı 5,9 gün. Purge/embargo GEVŞETİLMEDİ, yapay fold üretilmedi
(`INSUFFICIENT_WINDOW_FOR_LEAKFREE_FOLDS`).

## 6. Commit(ler)

Bu oturumun commit'leri `research/profitability-acceleration-v1` üzerinde:

* `feat(research): offline profitability research acceleration v1`
* `docs(research): record acceleration v1 results and handoff`

`main` ve `feature/quant-evaluation-v1` DEĞİŞMEDİ. Push YOK, deploy YOK, CI çalıştırılmadı.

## 7. Yazma sınırı (doğrulandı)

Üretim kodu/config'i, kanonik defter/pozisyon/snapshot/deney dosyası, üretim dersleri ve
learned-index **değişmedi**. VPS'e yazılmadı (yalnız `/tmp` export, silindi). Deployment,
restart, manuel tur, zorlanmış işlem, kanonik backfill, risk limiti değişikliği, deney sıfırlama
**yok**. PAPER / `gateway=paper` / `live_order_path=false` / `ALLOW_LIVE_TRADING=false` / kill
switch **ARMED** / bütün katmanlar SHADOW / auto-promotion flag'leri **false** korundu.
Terfi kapıları (≥50 karşılaştırılabilir kapanış, ≥30 gün) aynen duruyor; offline sonuç hiçbir
ileri sayacı artırmadı.

## 8. Üretim durumu (export anındaki gözlem — DEĞİŞTİRİLMEDİ)

* `pfexp_v1_2` ACTIVE SHADOW, start `2026-09-07T19:38:58Z`, config `4a79f6eeb5e95c27`.
  Export anında **v1.2 girişi henüz yok**.
* `pfexp_v1_1` DRAINING; F00036 simülasyonları P0/P2/P3'te açık.
* **F00036 (ONDO/USDT LONG, 2026-09-07T02:43:40Z) export anında HÂLÂ AÇIK.**
* Kanonik: 11 açık / 25 kapalı. Servisler `active`, worker/dashboard sağlıklı.

## 9. Sonraki adım (tek, kanıta dayalı)

`conservative_net_edge_r`'yi hem `p_win_prior` hem model `p_win` ile ÇİFT hesaplayıp ikisinin
`tradeable` kararını SHADOW olarak kaydeden bir ölçüm ekle (karar değişmez, işlem etkilenmez).
RL-01 böylece ileri kanıta çevrilir ya da çürütülür. Kâr vaadi yoktur.

İkinci adım (bu iş için gerekli değil): `position_path`'in yalnız `mark` taşıması nedeniyle
gözlenmiş yol analizi sınırlı; bar uçlarının kaydı ayrı ve kontrollü bir deployment konusudur.


---

# EK — DUAL-EDGE V2 (2026-09-08, çalıştırıldı)

Ayrıntı: `docs/PROFITABILITY_DUAL_EDGE_V2.md`. v1 çıktıları KORUNDU.

## E1. Doğrulanan durum (salt okunur, 2026-09-08T07:11:50Z)

Üretim HEAD `12db804…` (tree temiz, NRestarts 0) · 11 açık / 25 kapalı **değişmedi** ·
**F00036 hâlâ AÇIK** · pfexp_v1_2 `ACTIVE_SHADOW` (`4a79f6eeb5e95c27`, 19:38:58Z,
`admissions_open=true`, **hâlâ 0 giriş**) · pfexp_v1_1 `..._DRAINING`, `admissions_open=false` ·
pfexp_v1 `e3863761d50c79c3` salt okunur · PAPER / kill switch ARMED /
`ALLOW_LIVE_TRADING=false`. Tek doğal değişim: cüzdan 98.2226066642 → 98.2246866674 (funding).

## E2. Doğrulanan gerçekler

* **Kaynak izi (her koşuda yeniden ölçülür):** `engine_v3.py:827` (`tour`) ekonomik kapı,
  `:875` (`tour`) model olasılığı ataması; `_assess_opportunities` **tek çağrı**, model
  atamasından sonra yeniden hesap **yok**. Sınıflandırma: **`MISSING_INTEGRATION`**
  (mod `PAPER_BOUNDED`, 1083/1083 kayıtta `applied=true`).
* **`p_win` fit edilmiş model çıktısı DEĞİL:** `state/models.json` YOK, kalibratör `n_fit=0`.
  Değer = 0.5·hiyerarşik önsel + 0.5·legacy v1 + PAPER_BOUNDED etkisi.
* **Kapı olasılığı 404/404 için geri kazanıldı** (kimlik tersi), doğrulama hatası **0.0**.
* **Üretim kenarının yeniden üretimi: 404/404 birebir.**

## E3. F00036 — ÇÖZÜLDÜ

`4c7bed8f3bb17655` / `20eab956a587dd6e` / as_of `2026-09-07T02:43:40Z`.
`0.878907` (gross) ile `0.677204` (conservative) **aynı kaydın iki alanıdır**;
boşluk `0.201703` = belirsizlik `0.041703` + yumuşak ceza `0.160000`, artık **0.0**.
`cost_r = 0` çünkü `expectancy_basis = NET_OUTCOME`. Model olasılığıyla kenar `−0.366317`.

## E4. Dört eşleşmiş kapı sayacı (404/404 hesaplanabilir)

|  | model EVET | model HAYIR |
| --- | --- | --- |
| ön tahmin EVET | **4** | **386** |
| ön tahmin HAYIR | **0** | **14** |

Anlaşmazlık 0.9554. Kanonik işleme bağlanmış **7/7** aday: kapı(ön)=EVET, kapı(model)=HAYIR.
Bunlar YALNIZ ekonomik kapı verdiktidir — kaldıraç/risk kapasitesi/chief AYRI.

## E5. Kalibrasyon (hedef-uyumlu) — YETERSİZ ama yönlü

Etiket `label_v1_scratch_excluded`, ortak 72 sa ufuk, sansür **0.8465** (62/404 etiketli):

| Tahmin edici | n | Brier | Log loss | AUC |
| --- | --- | --- | --- | --- |
| `p_win` (model) | 62 | 0.225417 | 0.650646 | **0.3321** |
| `p_win_prior` | 39 | 0.336495 | 0.875191 | 0.5448 |
| geçmiş-yalnız temel oran | 62 | **0.210559** | **0.614667** | 0.3845 |

**Naif taban çizgisi her iki skoru da yeniyor.** Kesin sonuç DEĞİL (n küçük, tek pencere,
%97 LONG, ağır sansür, farklı alt kümeler).

## E6. Replay sadakati (ekonomik)

EXACT **0** / WITHIN_TOLERANCE **18** / MISMATCH **7** (n=25). Azami |ΔR| **0.358989**,
ortalama 0.070187, azami |Δ net USDT| 0.18203; çıkış zamanı ≤1 bar **18/25**, azami zaman
hatası ≈3.05 bar; dolum uyuşmazlığı **0**. Kanonik kapanış zamanı yalnız pencere üst sınırıdır;
neden/PnL simülasyona verilmedi. Ortalama |ΔR| challenger farklarıyla aynı mertebede →
"gürültüden ayrılmıyor" sonucu güçlendi.

## E7. Sürekli gözlem

**Mevcut snapshot'lar YETERLİ** (404/404 çift kapı hesaplanabilir; geri kazanma hatası 0.0).
**Enstrümantasyon yaması GEREKMİYOR ve HAZIRLANMADI.** Gözlemci = `--stage dual_edge`
(idempotent, sınırlı salt okunur export, ayrı çıktı, üretime yazım yok).

## E8. Komut ve dosyalar

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out" --stage dual_edge
```

Süre **~2 s** (sıcak önbellek). Çıktı: `dual_edge_report.json` / `.md` / `corrections.json`.
`--stage v1` özgün raporu yeniden üretir (**birebir aynı**; yalnız `research_id`
`d0cf2a0ca55860ad` → `10297e21231b9e1d`, çünkü kod SHA'sına bağlı).

Yeni dosyalar: `tradingbot/research/dual_edge.py`, `tradingbot/research/dual_edge_run.py`,
`tests/test_research_dual_edge_v1.py`. Değişen: `entry_lab.py` (temsilci politikası,
geriye uyumlu), `exit_lab.py` (`economic_fidelity`), `run.py` (`--stage`).
Testler: **1969 passed / 22 skipped / 0 failed**, ruff temiz.

## E9. Açık kalan

1. Model olasılığını kapıya bağlamak bu veriyle DESTEKLENMİYOR (AUC 0.33; 404 adayın 400'ü
   elenir).
2. Asıl eksik "hangi olasılık" değil, **kalibre edilebilir bir olasılık** (champion model yok,
   kalibratör fit değil, n_closed=25).
3. `avg_loss_r = −1.0` sabit varsayımı (ölçülen −1.0323) — ayrı ve ölçülmemiş konu.
4. `win.add` global düğüme işlem başına iki kez yazıyor (n=50 vs n_closed=25); oran
   etkilenmez, shrinkage gücü etkilenir. Ölçüldü, düzeltme ÖNERİLMEDİ.


---

# EK 2 — ÖĞRENME SAYAÇ ONARIMI + DÜZELTİLMİŞ KIYASLAMA (V3, 2026-09-08)

Ayrıntı: `docs/LEARNING_COUNT_INTEGRITY_V3.md`. v1/v2 çıktıları KORUNDU.
Yeni çıktı: `integrity_report.json` / `.md`.

## F1. Kök neden (kanıtlandı)

`LearnerV2.on_trade_closed` bir nihai kapanış için `win.add`'i İKİ KEZ çağırıyordu
(`leaf=SYM|setup` ve `leaf=SYM`). `HierarchicalRate.add` her çağrıda ORTAK ATALARI da
(`""` ve `regime:X`) yazdığı için bu iki düğüm kapanış başına iki kez sayılıyordu.
Yaprak düğümler (44 + 46) etkilenmiyordu — onlar meşru ayrı granülerliklerdir.
`exp_r` (tek çağrı) hiç yinelenmemişti (global n=25).

Üç büyüklük ayrı: benzersiz gözlem **25** · güncelleme çağrısı **50 → 25** ·
global ağırlıklı kütle **50.0 → 25.0**. Yinelenen düğüm **9**, değişmeyen **90**.

## F2. Onarım

`learn/model.py`: `add(..., leaves=(...))` + `_keys_multi` — atalar BİR KEZ, her yaprak bir kez,
anahtarlar tekilleştirilir. `leaf=` imzası geriye uyumlu. `learn/learner_v2.py`: tek çağrı.
`n`'yi bölen / sayaç silen / çocuk güncellemesi bastıran çözüm KULLANILMADI.
Tekrarlı teslim koruması MEVCUT `LearnedIndex`tedir — ikinci defter eklenmedi.

## F3. Bileşen etkisi (point-in-time, sızıntısız)

Sadece metadata DEĞİL: `sample_size` yarıya iner → `uncertainty_penalty_r` BÜYÜR.
Ortalama Δceza **+0.018828 R** (azami +0.026027), ortalama Δ`p_hier` **+0.028670**.
Etkilenmeyen: `p_win_prior`, `avg_win_r`, plan geometrisi.

F00036 birebir doğrulandı: kayıtlı `sample_size=22` / ceza `0.041703` eski semantikle
aynen çıkıyor; düzeltilmişte `11` / `0.057735`.

Kenar duyarlılığı: **2 / 404** satırda ekonomik kapı verdikti değişiyor —
`fed44d90cddc48d1` (AVAX, **F00033: gerçekleşen işlem**, +0.006236 → −0.019715) ve
`a1592626729ca2c4` (BTC, +0.018185 → −0.007842).

**Yeniden kurulum sınırı:** kayıtlı `sample_size` 202/404 satırda birebir üretiliyor; üretimde
düğüm anahtarı KAPANIŞ ANI rejimiyle kuruluyor ve bu değer saklanmıyor. Global düğüm ölçümü
rejimden bağımsız ve üretimle birebir aynı.

## F4. Hedef sözleşmesi (kaynaktan)

SCRATCH **paydada**, kazanç sayılmaz, üçüncü sınıf değil. `p_win` **koşulsuz**
`P(r > +0.25R)`, ufuk işlem **ömrü**. v2'deki kalibrasyon SCRATCH'i dışlıyordu → düzeltildi.
`avg_loss_r = -1.0` sabit varsayımı kayıp tarafını ABARTIR (kayda geçirildi).

## F5. Eşleşmiş kıyaslama (AYNI kimlik kümesi)

"39 satır" nedeni: v2 yalnız DOĞRUDAN kayıtlı `features.p_win_prior` satırlarını alıyordu.
Artık kimlik tersinden DERIVED provenansıyla geri kazanılıyor.

**Hedef A (yaşam boyu, sansür 305/404):** n=99, hash `655226d1b9b1316b`.
Brier: taban çizgisi **0.216986** < harman 0.230404 < ön tahmin 0.344555.
Eşleşmiş fark **`p_win_prior` − taban çizgisi = +0.127569, GA95 [+0.0066, +0.2417] → SIFIRI
DIŞLIYOR** (tek kurulmuş sonuç). Diğerleri sıfırı içeriyor.

**Hedef B (sabit 72 sa MTM):** n=165, hash `aa737f2aaa0e2bcb`, 24 SCRATCH.
Brier: ön tahmin **0.287864** < harman 0.322477 < taban çizgisi 0.325084.
Bütün eşleşmiş farklar sıfırı içeriyor.

**Sıralama hedefe göre TERSİNE DÖNÜYOR** → A ve B birbirinin yerine geçemez; 72 sa MTM,
yaşam boyu olasılığın kalibrasyonu DEĞİLDİR.

## F6. Panel

`Güven` → `Konsensüs gücü`, `P(kazanç)` → `P(kazanç) — model` (+ hedef ve
"kalibratör FIT EDİLMEMİŞ" notu, + "son değerlendirme ≠ giriş kanıtı").
SOL'daki "%0 güven" bir fallback DEĞİL: `confidence_calibrated = 0.0019`'un yuvarlanmasıydı;
38 head'in hiçbirinde tam sıfır yok. `expected_return_net = 0.0` / `expected_r = 0.0`
ÖLÇÜLMÜŞ sıfırdır ve korunur. `_cell_pct_signal` ölçülmüş sıfırı `%0`, eksiği `—`, küçük
sıfır-olmayanı `%0.2` gösterir. **Yönetim tablosu zaten doğruydu** (11/11 `UNKNOWN` rozeti,
`n_economics_unknown=11`) — değişiklik gerekmedi.

## F7. Durum onarım planı

`PREPARED_NOT_EXECUTED`, `execution_authorized: false`. Kapsam yalnız `learn_v2.json` →
`win.stats` ata düğümleri (9 kirlenmiş). **Öneri R1 = DOKUNMA**: kod düzeltildi, kazanç küçük
(0.0188 R), R2 kapanış-anı-rejimi saklanmadığı için birebir doğrulanamaz.

## F8. Testler ve komut

**1990 passed / 22 skipped / 0 failed** (1969 + 21 yeni), ruff temiz.
Yeni: `tests/test_learning_count_integrity.py`.

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out" --stage integrity
```

## F9. Sonraki adım

Ödeme modelini etikete uydur: `avg_loss_r`'yi ölçülmüş koşullu ortalamayla
(`E[R | r ≤ 0.25R]`) değiştiren bir SHADOW hesabı kaydet — karar değişmez. Hedef ile ödeme
modelini AYNI olay üzerinde tanımlamak, yeni bir tahmin edici tasarlamadan ÖNCEKİ adımdır.


---

# EK 3 — SÜRÜM PAKETİ + ÖDEME KARŞILAŞTIRMASI (V4, 2026-09-08)

Ayrıntı: `docs/LEARN_COUNT_REPAIR_RELEASE_V4.md`. **DAĞITILMADI.**

## G1. Üretim ≠ yerel

VPS HEAD `12db804…` (temiz, NRestarts 0). **Sayaç düzeltmesi ÜRETİMDE DEĞİL**
(`learner_v2.py:154-155` hâlâ iki `win.add`); **sunum düzeltmesi de DEĞİL**.
Üretim `learn_v2.json`: global `win` n=50/s=14, `exp_r` n=25, `n_closed`=25.

## G2. Sürüm adayı

Dal `release/learn-count-integrity` (taban `12db804`), worktree `C:/Users/berke/release-lci`.
6 üretim dosyası + 2 test dosyası, +207/−19. `tradingbot/research/*` **DAHİL DEĞİL**.
**Sürüm ağacı: 1940 passed / 22 skipped / 0 failed** (araştırma dalı ayrı: 2014/22/0).

## G3. Durum kararı: **B (onar)** — V3'teki R1 önerisi DEĞİŞTİ

V3'ün blocker'ı YANLIŞTI: `lessons` kapanış anı rejimini taşıyor (giriş rejiminden 14/25
kapanışta farklı) ve derslerden yeniden kurulum saklanan durumu **100/100 düğümde birebir**
üretiyor. Onarım iki bağımsız yolla doğrulandı → `verify_conversion` = **VERIFIED**.

13/13 değişmez geçti: şema 2 · çürüme YOK (`half_life_days=None`) · `s==ss` 100/100 ·
legacy import ÇALIŞMAMIŞ · ata parite çift 8/8 · Σregime = global · global n = 2×n_closed =
2×kanonik kapanış · global s = 2×kanonik kazanç. Karışık kod sürümü yok (iki çağrılı biçim
`84bfc74`, ilk kapanıştan ÖNCE).

Kuru çalıştırma: **8 ata düğüm**, `n`/`s`/`ss` BİRLİKTE yarılanır; yaprak/`exp_r`/`agent_hit`/
dersler/`n_closed`/`alpha`/`prior_mean` DEĞİŞMEZ; girdi mutasyona uğramaz; işaretli ve ikinci
dönüşüm reddedilir.

A vs B: `half_life_days=None` → fazla kütle **kalıcı 25** (k=0,10,25,50,100'de aynı).
Δposterior 0.0262 → 0.0025. B seçildi (kanıt bütünlüğü + kalıcılık + düşük risk).

**Kapı değişimleri ayrıştırıldı:** BTC `a1592626729ca2c4` **TAM yeniden üretim** (kanonik işleme
bağlı DEĞİL); AVAX `fed44d90cddc48d1`/**F00033** **YAKLAŞIK** (kayıtlı n=4, yeniden kurulan 2)
→ verdikt değişimi KANITLANMIŞ değil. **F00033 hâlâ AÇIK** — hiçbir kâr/zarar atfedilemez.

## G4. Provenans (asgari ek)

`on_trade_closed` derse `learning_keys` ekler (`semantics`, `regime_at_close`, `win_leaves`,
`exp_r_leaf`, `weight`, `feature_version`); `note_learned` bunu MEVCUT `LearnedIndex`e yazar.
Opsiyonel alan, idempotency anahtarı DEĞİŞMEDİ, ikinci defter YOK, giriş/kapanış rejimi ayrı.

## G5. Ödeme modeli

R: payda |entry−initial_stop|×**initial_qty**, pay NET (kısmi çıkışlar dâhil) → **maliyet
tekrar düşülmedi** (muhasebe artığı 0.0). `avg_win_r` = **BLEND** (ampirik koşullu ortalama
değil). E[R|kazanç değil] = **−1.069052**, SCRATCH 0 → −1R **kaybı KÜÇÜK gösterir**
(72sa kohortunun 24 SCRATCH'i taşınmadı).

Kapsam %100 (404), μ_W 1.707–1.908, μ_N −1.0696…−1.0685:

| Panel (olasılık SABİT) | ort. Δgross | EVET→HAYIR | HAYIR→EVET |
| --- | --- | --- | --- |
| `p_win_prior` | −0.137581 | **5** | **0** |
| `p_win_blend` | −0.094536 | **3** | **0** |

Hedefe hizalama her iki panelde ekonomiyi DAHA TEMKİNLİ yapar. Kârlılık sonucu DEĞİLDİR.

## G6. Sunum

`P(kazanç) — istatistiksel tahmin`; kalibrasyon durumu artık **kanıttan** (`calibrator.n_fit`,
champion kaydı), sabit iddia YOK. Coin head satırlarındaki `0.0`, giriş planı üretilmediğinde
**dataclass varsayılanıdır** → «yok (plan üretilmedi)». Plan varken hesaplanan 0.0 aynen
korunur. Yönetim tablosu zaten doğruydu (11/11 UNKNOWN) — değişmedi.

## G7. Komut

```bash
python -m tradingbot.research.run --export "C:/Users/berke/research/pfres_v1" --out "C:/Users/berke/research/pfres_v1/out" --stage readiness
```

## G8. BEKLEYEN yürütme (kapsam dışı)

Operatör onayı · bundle dağıtımı · `learn_v2.json` dönüşümünün yüklenmesi · restart + canary.
Geri alma kapsamı: kod `.last_good_commit`=`12db804`; durum YALNIZ `learn_v2.json` —
kanonik defter/hafıza/pfexp/`learned_closes` GERİ ALINMAZ (sonraki doğal dolum ve kapanışlar
korunmalı).
