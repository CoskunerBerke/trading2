# İnceleme kopyası — araştırma betikleri ve ölçüm kayıtları (2026-09-21)

> **Bu dizin bir ANLIK KOPYADIR, çalıştırma kaynağı DEĞİLDİR.**
> Asıl dizinler yerelde durur ve bu teslimde taşınmadı/değiştirilmedi:
> `C:\Users\berke\research\entry_v1` (araştırma betikleri) ·
> `C:\Users\berke\trading2-deploy\olcumler` (ölçüm betikleri + sonuçları).
> Betikler mutlak yollar içerir (`WT = C:/Users/berke/wt-entry`, `ROOT = C:/Users/berke/research/entry_v1`,
> `ARCHIVE = C:/Users/berke/research/bn_archive`, `wt-ten/data`). Buradan çalıştırılsalar bile yine o yerel
> dizinleri okur/yazarlar. Mantık değiştirilmedi: yerel kopyalar kaynaklarla bayt-bayt aynıdır; depoya girerken
> yalnız satır sonları LF'e normalleştirildi (`core.autocrlf=true`). Tek içerik istisnası devir notunun yayım
> kopyasıdır (aşağıda).

Üst belge: [`docs/review/REVIEW-2026-09-21.md`](../../../docs/review/REVIEW-2026-09-21.md).

## İçerik

| Dizin | Ne |
|---|---|
| `scripts/` | `research/entry_v1` betikleri: evren, veri toplayıcı, replay koşucusu, kural kancaları, box süpürmesi |
| `olcumler/` | 6 ölçüm betiği + 7 sonuç JSON'u + `trail.log` (trail dosyaları **canlı sürecin anlık kopyası**) |
| `olcumler/meta/` | Bu ölçümlerin `run_rule` meta kayıtları (`research/entry_v1/out/meta_<run_id>.json`), 111 dosya; henüz koşmamış 9 trail koşusunun metası yok |
| `reports/` | `PROTOCOL_V15.md`, `DENEY_V15.md`, `BOX_SWEEP_v15_a.{md,json}`, devir notunun yayım kopyası |
| `arsiv/` | Arşivlerin **kendi** `manifest.json`larından toplanmış kapsam tabloları, toplama raporu, borsa filtre dosyasının kimliği |
| `FILES.tsv` | Her dosyanın kaynağı, yerel boyutu, iki SHA256'sı ve notu |

**SHA256 ve satır sonları.** Kaynak dosyaların 150'sinden 129'u CRLF içerir; depo bunları LF olarak saklar.
`FILES.tsv`'de iki değer vardır: `sha256_yerel` = kaynak/yerel kopyanın baytları; `sha256_depo_LF` = CRLF→LF
dönüşümünden sonraki baytlar, yani depodaki blob. Satır sonu dışında fark olmadığı, commit'teki her blob'un
yerel dosyanın CRLF→LF hâline eşit olmasıyla doğrulandı. Depodan doğrulama:
`git show <commit>:research/entry_v1/review_2026_09_21/<yol> | sha256sum` → `sha256_depo_LF`.

## Betikler — rol ve inceleme dalıyla karşılaştırma

Karşılaştırma: `origin/review/2026-09-14-entry-research` (uzakta `9a25a93`) içindeki
`research/entry_v1/<ad>` blob'u ile yerel dosyanın `git hash-object` değeri.

| Betik | Rol | 2026-09-14 inceleme dalına göre |
|---|---|---|
| `run_rule.py` | `HistoricalReplay`'i doğrudan sürer; `meta_<run_id>.json` yazar; `require_all` ve evren provenansı | **FARKLI** |
| `strategy_rules.py` | T1/T2/M2 kancası → `tradingbot.ema200_trend.decide` (kaldıraç + trail parametreleri) | **FARKLI** |
| `universe.py` | Evren tek kaynak: `config.yaml → entry_universe.symbols` ∩ arşiv; düşenleri bildirir | YOK (yeni) |
| `collect_binance.py` | `www.binance.info` üzerinden klines/funding toplayıcı; filtre dosyasını `wt-ten`'den kopyalar | YOK (yeni) |
| `box_sweep.py`, `run_box_v15.py` | V15 box süpürmesi (varsayılan arşiv `wt-ten/data`) | YOK (yeni) |
| `box_rules.py`, `probe_box_cost.py` | Box'un üretim-replay kancası ve V15 maliyet/boyut sondası | YOK (yeni) |
| `momentum_rules.py`, `analyze.py` | M-ailesi kancaları; `trades`/defter metrikleri | AYNI |
| `entry_rules.py`, `candle_rules.py`, `chart_rules.py`, `regime_rules.py`, `session_rules.py`, `session_gate.py` | `run_rule.py`'nin CLI yolundaki (`--rule`) bağımlılıkları | AYNI |

`trading2-deploy` kökündeki `grid.py lev.py rank40.py run40.py slots.py trail.py be_measure.json`
kopyaları `olcumler/` altındakilerle **SHA256 olarak aynıdır** (ikinci bir kaynak yok).

## Ölçümler — kod kimliği (provenans)

Hiçbir ölçüm kaydı kod SHA'sı taşımaz. Zaman aralıkları `meta_<run_id>.json` değişiklik zamanlarından
(bitiş) ve satırdaki `elapsed_s` alanından (başlangıç) türetildi; saatler yerel (+03).
"Sonra değişen" sütunu, `wt-entry`'deki o sırada commit edilmemiş dosyalardan ve
`C:\Users\berke\research\entry_v1` betiklerinden hangilerinin ölçüm başladıktan **sonra** değiştiğini (dosya
zamanı) gösterir. Bu dosyaların ölçüm anındaki içeriği hiçbir yerde saklanmadı.

"7 kaynak" = commit `5818efa`'daki yedi `tradingbot/` dosyası. `run_rule.py` 09-20 11:09'da,
`strategy_rules.py` 09-20 23:23'te son kez değişti; yani bu paketteki kopyaları, kendilerinden önceki
ölçümlerde kullanılan sürümler **değildir**.

| Kayıt | Betik | Koşu | Aralık (+03) | Sonra değişen | Kod kimliği |
|---|---|---|---|---|---|
| `be_measure.json` | **üretici betik yerelde bulunamadı** (yalnız `meta_be_*` var) | 12 ok | 09-19 11:40 → 12:50 | 7 kaynak + `config.yaml` + `run_rule.py` + `strategy_rules.py` | kaydedilmemiş ara hâl |
| `run40.json` | `run40.py` | 12 ok | 09-19 17:11 → 22:54 | 7 kaynak + `config.yaml` + `run_rule.py` + `strategy_rules.py` | kaydedilmemiş ara hâl |
| `rank40.json` | `rank40.py` | 12 ok | 09-19 23:34 → 09-20 01:35 | 7 kaynak + `config.yaml` + `run_rule.py` + `strategy_rules.py` | kaydedilmemiş ara hâl |
| `grid.json` | `grid.py` | 12 ok | 09-20 01:38 → 02:17 | 7 kaynak + `config.yaml` + `run_rule.py` + `strategy_rules.py` | kaydedilmemiş ara hâl |
| `slots.json` | `slots.py` | 24 ok | 09-20 02:17 → 04:49 | 7 kaynak + `config.yaml` + `run_rule.py` + `strategy_rules.py` | kaydedilmemiş ara hâl |
| `lev.json` | `lev.py` | 30 ok | 09-20 11:39 → 17:26 | 6 kaynak (`futures_ledger` hariç) + `config.yaml` + `strategy_rules.py` | kaydedilmemiş ara hâl |
| `trail.json` süreç-1 | `trail.py` | 7 ok + 11 hata | 09-20 23:24 → 09-21 00:48 | `ema200_trend`, `box_theory`, `paper_rules` (00:24), `replay/engine` (00:25), `strategy_paper` (00:26), `config.yaml` (00:41), `config_v3` (22:58) | kaydedilmemiş ara hâl |
| `trail.json` süreç-2 | `trail.py` (PID 1472) | anlık kopyada 2 ok | 09-21 22:59:23 → sürüyor | **yok** (son kaynak değişikliği 22:58:08) | commit `9095ce6` içeriği (dosya zamanı kanıtıyla) |

**Sonuç:** süreç-2 dışındaki hiçbir ölçüm `9095ce6`'ya bağlanamaz. Aynı `run_id` için bir ölçümün
`ok=True` olması, onu başka bir sürecin sonucuyla birleştirmeye yetmez.

### Evren ve aday yolu (meta kayıtlarından)

Canlı kâğıt defterler adayları **sıralamaz** (`candidate_order=None`, tek geçiş). Aşağıdaki kayıtların bir kısmı
araştırmaya özgü iki fazlı/sıralı yolda koştu; sonuçları canlı davranışa doğrudan aktarılamaz.

| Kayıt | `n_symbols_loaded` | `candidate_order` (meta) | Yol |
|---|---|---|---|
| `be_measure.json` | **10** | alan yok (o sürümde parametre yoktu) | tek geçiş |
| `run40.json` | 10 ve 40 | alan yok | tek geçiş (üretim yolu) |
| `rank40.json` | 40 | `true` | iki fazlı (kontrol + sıralı kollar) |
| `grid.json` | 10 | `true` | iki fazlı (B/C kolları; A kolu `run40`'tan) |
| `slots.json` | 10 ve 40 | `true` | **sıralı** (`_guc` = (kapanış − eşik)/ATR14) |
| `lev.json` | 40 | `true` | **sıralı** (`_guc`), `equity` 100 veya 200 |
| `trail.json` | 40 | `false` | tek geçiş (üretim yolu) |

Tüm metalarda `universe_complete: true`.

### Trail ayrıntısı

* Süreç-1 (09-20 23:24'te başladı): P3'ün 6 kolu ve P2 m2 A3D1 başarılı. 00:41:27'de `config.yaml`'a
  `rule_params.leverage_max` yazıldı; bellekteki eski `TrendParams` bu alanı tanımadığı için kalan 11 koşu
  `ConfigError` ile ~0 sn'de düştü (satırlar `ok=False`, `hata` alanında).
* Süreç-2 (09-21 22:59:23, PID 1472, `trading2-deploy/olcumler/trail.py`): `ok=True` olan `run_id`'leri
  atlar, düşenleri yeniden koşar. Anlık kopyada `tl_m2_p2_a3d2` ve `tl_m2_p2_a2d1` **iki kez** geçer:
  önce süreç-1'in hata satırı, sonra süreç-2'nin başarılı satırı. Analizde ayrılmalıdır.
* Anlık kopya: 20 satır (9 ok, 11 hata), SHA256 `FILES.tsv`'de. Süreç bu teslim sırasında çalışmaya devam
  etti; güncel durum için yerel `trading2-deploy/olcumler/trail.{json,log}` okunmalıdır.
* Taban karşılaştırması `run40.json` → `kol=40coin` satırlarıdır (09-19 kodu). Trail kolları farklı süreçlerden
  ve farklı kod hâllerinden gelir; "trail X'i bozdu/düzeltti" hükmü sürüm bağı kurulmadan verilemez.
* Trail koşuları **kaldıraç 1**, bakiye 100 (config `futures.starting_equity_usdt`), başa-baş kapalıdır.
  Config'teki 200 USDT + 2→4 kaldıraç bileşimi trail ile ölçülmedi.

## Veri

| Arşiv | Kullanan | Kapsam (mevcut manifestlerden) |
|---|---|---|
| `C:\Users\berke\research\bn_archive` (≈101 MB) | `run40`, `rank40`, `grid`, `slots`, `lev`, `trail` (`cache_dir=ARCHIVE`, `require_all=True`) | 40 sembol × `1d`/`4h`/`funding`: ilk bar 2020-10-01 (sonradan listelenenlerde 2024-08-16'ya kadar), son bar 2026-09-19; gap 0, kopya 0, kalite 1,0. **`5m`: yalnız 6/40** (BTC, ETH, NEAR, SOL, UNI, ZEC), 2024-08-01 → 2026-09-20. `1h` yok. |
| `C:\Users\berke\wt-ten\data` | V15 box süpürmesi (`box_sweep.CACHE_DEFAULT`) | 10 sembol × `5m`/`15m`/`1h`/`4h`/`1d`/`funding`/`oi_1h`. Manifestlerde LTC, SOL, XRP serilerinde 5 günlük boşluk kayıtlı (5m'de 1440 bar; kalite 0,9977–0,9979). `DENEY_V15.md`'deki "0 boşluk" ifadesi BTC içindir. |

* `arsiv/bn_archive_manifests.json` (126 seri) ve `arsiv/wt-ten_history_manifests.json` (70 seri) arşivlerin
  kendi `manifest.json` dosyalarından toplandı. `checksum` alanları toplayıcının yazdığı değerlerdir;
  bu teslimde veri dosyalarının hash'i **yeniden hesaplanmadı**.
* `arsiv/collect_binance.json`: `bn_archive` için 2026-09-19 toplama raporu (40 sembol, 120 satır = 40 × `1d`/`4h`/`funding`, `failed: []`,
  `base: https://www.binance.info`; `timeframes` alanı yalnız `1d`/`4h` yazar, funding ayrıca toplanmıştır).
* Borsa kuralları: `bn_archive/symbol_filters.json` yayımlanmadı; boyutu ve bu oturumda hesaplanan
  SHA256'sı `arsiv/symbol_filters.identity.json`'da. `wt-ten/data/symbol_filters.json` ile aynı dosyadır.
* **Uç erişimi:** `www.binance.info`'nun bu makineden erişilebildiği önceki oturumların yerel kontrolüdür
  (`collect_binance.py` başlığı: 2026-09-19; devir notu §0). Bu teslimde yeniden sınanmadı. Testlerdeki
  sahte sağlayıcılar gerçek uç erişimini kanıtlamaz.

## Ölçüm kurulumu (kaynaktan)

* Evren: `universe.resolve()` → `config.yaml: entry_universe.symbols` (40 sembol) ∩ arşiv;
  `run_rule(require_all=True)` eksik sembolde hata verir; meta `symbols_loaded`/`symbols_skipped`/`universe_complete` yazar.
  10 coinlik kollar sabit listeyi `symbols=` ile verir.
* Piyasa: USDⓈ-M perpetual (`market="futures"`), replay adımı `4h`, kural girdisi kapanmış `1d` barlar.
* Pencereler: P1 2020-11-01→2022-08-31 · P2 2022-09-01→2024-08-31 · P3 2024-09-01→2026-08-31;
  `walk_forward_windows(train_days=180, test_days=30, purge_bars=6, embargo_bars=6)`.
* Maliyet (config `fees`): futures taker %0,05, maker %0,02, kayma 3 bps. Funding arşivden
  (meta `funding_coverage`; ölçüm satırlarında `TAM(n/n)`). Borsa filtreleri `symbol_filters.json`.
* Defter: başlangıç `futures.starting_equity_usdt` = 100 (lev/slots kolları `equity=`/`risk_pct=` ile değiştirir),
  `no_breakeven=True`, kaldıraç 1 (lev kolları hariç). `max_entry_drift_pct` replay'e geçirilmez (kapalı).

## Yeniden çalıştırma komutları (yerel; bu teslimde ÇALIŞTIRILMADI)

Önkoşul: `C:\Users\berke\wt-entry` (kod), `C:\Users\berke\research\entry_v1` (betikler),
ilgili arşiv; Python 3.13 + depo `requirements.txt`. Aynı betiği aynı anda iki kez başlatmayın
(aynı sonuç dosyasına yazarlar).

```bash
# Trail — sürdürülebilir: ok=True olan run_id'leri atlar. 2026-09-21 22:59:23 itibarıyla PID 1472 olarak ZATEN çalışıyordu.
cd C:/Users/berke/research/entry_v1 && python C:/Users/berke/trading2-deploy/olcumler/trail.py

# 5m arşiv (6/40'ta kaldı; toplayıcı süreç çalışmıyor)
cd C:/Users/berke/research/entry_v1 && python collect_binance.py --dest "C:/Users/berke/research/bn_archive" --timeframes 5m --from 2024-08-01 --to 2026-09-21 --no-funding --out "C:/Users/berke/research/entry_v1/out/collect_5m.json"

# Diğer ölçümler (sonuç dosyası betiğin yanına yazılır)
python C:/Users/berke/trading2-deploy/olcumler/run40.py      # rank40.py, grid.py, slots.py, lev.py aynı biçimde

# V15 box süpürmesi (wt-ten/data okur; çıktı research/entry_v1/out/BOX_SWEEP_v15_a.{json,md})
cd C:/Users/berke/research/entry_v1 && python run_box_v15.py A
```

Box için planlanan dört kollu deney (onarımsız / kayma kapısı / kaldıraç tavanı / ikisi) **başlatılmadı**
ve bunun için yazılmış bir betik yok; yalnız bekleyen iştir.

## Hariç tutulanlar

* Mum/funding arşivlerinin veri dosyaları (`bn_archive` ≈83 MB, `wt-ten/data/history`) — yalnız manifest özetleri.
* `research/entry_v1/state/` (≈625 MB replay durumu/defterleri), `out/` içindeki pakete alınmayan ~250 dosya (işlem/karar
  günlükleri ve eski deneylerin metaları; eskileri 2026-09-14 inceleme dalında).
* `research/entry_v1/vps/tb-diag-*.tar.gz` (VPS teşhis paketi), `configs/`, `__pycache__/`.
* `trading2-deploy` altındaki bundle'lar, dağıtım betikleri, yerel yedek (`yedek/`), `run40-kismi.json` (kısmi, eski).
* Obsidian vault, `.env`, SSH anahtarları, token'lar — hiçbiri kopyalanmadı.
* Devir notu `reports/DEVIR-2026-09-21.yayim-kopyasi.md`: yalnız 44. satırdaki VPS `kullanıcı@host` maskelendi;
  depo herkese açıktır ve bu host adı depoda daha önce yer almıyordu. Yerel dosya değiştirilmedi.
