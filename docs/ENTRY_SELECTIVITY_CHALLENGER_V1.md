# ENTRY_SELECTIVITY_CHALLENGER_V1

> **Mod: SHADOW. `applied = false`. Aktif giriş kararı, pozisyon büyüklüğü, kaldıraç, stop, TP,
> RiskEngine, muhasebe, defter, gateway ve canlı emir davranışı DEĞİŞMEDİ.**

Bu katman yalnız **gözlem ve karşı-olgusal karar** üretir. Beş bağımsız challenger ailesi, her
giriş adayı için "bu aile bu işlemi engelleseydi ne olurdu" sorusunu ölçer. Hiçbiri emir üretmez.

## 1. Neden

2026-09-02 üretim ölçümü (VPS, PAPER):

| Büyüklük | Değer |
| --- | --- |
| Kapanmış işlem | 20 |
| Kazanan / kaybeden | 5 / 15 |
| Beklenti | −0,3745R |
| Ortalama kazanan / kaybeden | +1,7074R / −1,0685R |
| Profit factor | 0,5327 |
| Toplam | −7,4901R / −4,828 USDT |
| Çıkış nedeni | 15 stop, 3 hedef2, 2 başa-baş stop |
| Yön | 16 LONG / 4 SHORT |

Ödeme oranı 1,60; kırılma noktası %38,5; gerçekleşen kazanma oranı %25.
**Sorun ödeme oranında değil, kabul edilen kaybeden oranındadır.**

### 1.1 En kritik bulgu — `p_win` ve `edge` TERS ayrım yapıyor

| Alan | Kazanan ort. | Kaybeden ort. |
| --- | --- | --- |
| `p_win` | 0,3430 (n=5) | 0,4209 (n=15) |
| `conservative_net_edge_r` | 0,4879 (n=3) | 0,5509 (n=11) |
| `consensus_score` | 0,3832 | 0,1954 |
| `atr_pct` | 1,9710 | 2,6566 |

Kabul kararını veren iki büyüklük (`p_win`, `conservative_net_edge_r`) **kazananları eleyecek
yönde** ayrım yapıyor. Bu ölçüm 19 kapanışta da 20 kapanışta da aynı yönde çıktı.

Bu bir **tasarım kısıtıdır**: A ailesi (olasılık/edge) tanımlanır ve ölçülür, ama bu örneklemde
ters çalışır. n=5 kazanan istatistiksel sonuç için çok azdır — bu bir gözlemdir, sonuç değildir.
`test_22_inverse_p_win_calibration_is_not_masked_by_a_fitted_threshold` bu gerçeğin uydurma bir
eşikle maskelenmediğini kalıcı olarak zorlar.

## 2. Bileşenler

| Dosya | Rol |
| --- | --- |
| `learn/entry_snapshot.py` | Sıralamaya giren HER aday için append-only karar anı snapshot'ı. Her alanın kaynağı (`MEASURED`/`MODELED`/`DEFAULTED`/`MISSING`) taşınır; eksik alan sıfır sayılmaz. |
| `learn/entry_challenger.py` | Beş bağımsız aile (A olasılık/edge, B rejim/yön, C konsensüs dağılımı, D likidite/maliyet-risk, E portföy ısısı). `applied` daima `False`. |
| `learn/entry_eval.py` | Sonuç atfı + terfi kapıları + sızıntı denetimi + walk-forward. |
| `learn/entry_replay.py` | Faz 5: geçmiş veriyle karar anı sadakatle yeniden üretilebilir mi (FAIL-CLOSED). |
| `config_v3.EntrySelectivitySection` | Fail-closed config; `SHADOW` dışı mod ve `auto_promotion=true` → `ConfigError`. |
| `engine_v3` | Sıralamada snapshot yaz → açılışta `trade_id` bağla → tur sonunda rapor yaz. Arıza turu durdurmaz. |
| `dashboard` | `/learning` altında salt okunur bölüm + `/api/entry-selectivity`. |

### Değiştirilmemesi gereken tasarım sözleşmeleri

* **Eksik veri VETO gerekçesi değildir** (`MISSING_MEANS_ACCEPT = True`). Ölçemediğimiz için
  reddetmek, ölçtüğümüzü iddia etmenin başka biçimidir. Aile `ACCEPT` döner ve `blockers` ile
  neyi ölçemediğini söyler.
* **Eşikler bu örnekleme uydurulmadı.** Kırılma noktası `p* = 1/(1+payoff)` ekonomik
  kimliğinden gelir; ödeme oranı da **yalnız o işlemden önce kapanmış** işlemlerden hesaplanır
  (genişleyen pencere → sızıntı yok).
* **Aileler birleştirilmez.** Birleşik bir süper filtre, hangi gerekçenin işe yaradığını
  ölçülemez kılar.

## 3. Veri akışı

```
sıralama (chief.priority + conservative_net_edge_r)
  → _entry_capture      (karar anı girdileri tamponlanır; sonuç GÖRÜLMEZ)
  → tetik → ekonomi → risk → ledger.open
  → _entry_flush        (append-only snapshot + açılan işlem için AYRI `link` satırı)
  → kapanış
  → _write_entry_eval   (karşı-olgusal atıf + kapılar + replay denetimi)
  → state/entry_selectivity.json
```

`link` satırı ayrı yazılır; snapshot **hiçbir koşulda yeniden yazılmaz**.

## 4. Terfi kapıları (aile başına 14)

| Kapı | Geçme koşulu |
| --- | --- |
| `MIN_LINKED_CLOSES` | ≥ 50 **LINKED** kapanış (`LEGACY_MEMORY` sayılmaz) |
| `MIN_OBSERVATION_DAYS` | ≥ 30 takvim günü |
| `DIRECTION_COVERAGE` | ≥ 2 yön, her biri ≥ 10 kapanış |
| `REGIME_COVERAGE` | ≥ 2 rejim, her biri ≥ 10 kapanış |
| `POSITIVE_EXPECTANCY_IMPROVEMENT` | Δbeklenti > 0 |
| `OUT_OF_SAMPLE_IMPROVEMENT` | kronolojik ikinci yarıda Δ > 0 |
| `WALK_FORWARD_CONSISTENCY` | 3 kronolojik katın **üçünde de** Δ > 0 |
| `CONFIDENCE_INTERVAL_EXCLUDES_ZERO` | fark serisinin bootstrap %95 aralığı sıfırı dışlar |
| `PROFIT_FACTOR_IMPROVEMENT` | PF artar (ya da kaybeden hiç kalmaz ve toplam R > 0) |
| `DRAWDOWN_NOT_WORSE` | karşı-olgusal maxDD kötüleşmez |
| `TAIL_RISK_NOT_WORSE` | karşı-olgusal CVaR5 kötüleşmez |
| `DISCRIMINATION_POSITIVE` | engellenen kaybeden oranı > kaçırılan kazanan oranı (Youden J > 0) |
| `SURVIVORS_ABOVE_BREAKEVEN` | hayatta kalan küme kendi kırılma noktasının üstünde |
| `SYMBOL_CONCENTRATION` | en yoğun sembol payı ≤ %50 |
| `NO_LEAKAGE_POINT_IN_TIME` | snapshot sonucu görmemiş, yasak alan yok, kapanıştan önce yazılmış |

`None` (ölçülemedi) **daima** `passed=False` sayılır. Kapılar geçilse bile terfi otomatik
değildir: `auto_promotion` config'de `true` yapılamaz (`ConfigError`).

### `GATE_MIN_PER_STRATUM = 10` neden 10?

10'un altında gözlenen oranın %95 güven aralığının yarı genişliği ±0,31'i aşar; o katman
hiçbir gerçekçi etkiyi ayırt edemez, dolayısıyla "kapsandı" sayılamaz.

## 5. Faz 5 — replay sadakati (üretim ölçümü, 2026-09-02)

`entry_replay.replay_audit` gerçek VPS verisiyle çalıştırıldı:

```
VERDICT: NOT_REPLAYABLE
REASON : hiçbir kaynak zorunlu alanların tamamını taşımıyor;
         kapanışların 0/20'i karar anına bağlanabiliyor
```

| Kaynak | Kayıt | Tam mı | Tamamen boş zorunlu alan |
| --- | --- | --- | --- |
| `decision_journal` | 49 ACCEPTED | hayır | **19** |
| `trade_memory` | 29 giriş | hayır | **5** |
| `entry_snapshot` | 0 | hayır | (depo yeni) |

`decision_journal`'da tamamen boş olanlar: `avg_loss_r`, `avg_win_r`, `code_sha`, `config_hash`,
`conservative_net_edge_r`, `depth_ratio`, `entry_price`, `est_slippage_pct`, `liquidity_ok`,
`market_type`, `net_expectancy_r`, `policy_version`, `portfolio_open_risk_usdt`,
`same_direction_open`, `sample_size`, `setup`, `spread_pct`, `stop_distance_pct`, `stop_price`.

**Her üç kaynakta birden boş olanlar (asıl kırılma noktası):** `code_sha`, `config_hash`,
`policy_version`, `portfolio_open_risk_usdt`, `same_direction_open`.

### 5.1 Handoff'un düzeltilen bulgusu

Önceki denetim likidite alanlarının **her yerde** boş olduğunu (0/52) ve D ailesinin üretimde
karar veremeyeceğini bildiriyordu. Bu yalnız `decision_journal` için doğrudur. `trade_memory`
ölçümü farklıdır:

| Alan | `decision_journal` | `trade_memory` |
| --- | --- | --- |
| `spread_pct` | 0/49 | **29/29** |
| `est_slippage_pct` | 0/49 | **23/29** |
| `depth_ratio` | 0/49 | **23/29** |
| `liquidity_ok` | 0/49 | **23/29** |
| `conservative_net_edge_r` | 0/49 | **23/29** |

Veri **vardı**, karar günlüğüne yazılmıyordu. Bu yüzden `_entry_attach_features` artık karar anı
`FeatureSnapshotV3`ini de snapshot'a bağlıyor. Sonuç (e2e ölçümü): kabul edilen bir adayın
snapshot'ında **19 MEASURED + 12 MODELED, 0 MISSING**. D ailesi artık üretimde gerçekten karar
verebilir.

> `snapshot.vector()` **kullanılmaz**: eksik alanı `0.0` ile doldurur ve "ölçülmedi" ile
> "ölçüldü ve sıfır" ayrımını yok ederdi. Yalnız `values` içindeki, `missing` listesinde
> bulunmayan alanlar alınır.

`NOT_REPLAYABLE` bu aşamada **beklenen ve dürüst** sonuçtur; tek başına görevi başarısız kılmaz.
Denetim hiçbir koşulda R/PnL/beklenti üretmez (`synthetic_profitability: null`).

## 6. Bugünkü durum — terfi İMKÂNSIZ

| Büyüklük | Değer |
| --- | --- |
| `LINKED` kapanış | 0 (snapshot deposu yeni) |
| `LEGACY_MEMORY` gözlem | `trade_memory` köprüsünden, **kanıt değil** |
| `verdict` | `INSUFFICIENT_ENTRY_SAMPLE` |
| `applied_total` | 0 |
| `auto_promotion` | false |

Aktivasyondan önce **en az 50 gerçekten bağlı kapanış ve 30 takvim günü** birikmeli.

## 7. LLM sayfası dürüstlüğü

Panel bugüne kadar yalnız boş bütçe kartları gösteriyordu; bu "bütçe henüz harcanmadı" gibi
okunuyordu. Ölçülen gerçek: **motor hiçbir yerde bir LLM servisi kurmuyor** (`tradingbot/llm/`
paketi hiçbir çağrı yolundan import edilmiyor), dolayısıyla hiç çağrı yapılamaz.

`state/llm_status.json` artık gerçek durumu taşır: `DISABLED` / `NOT_CONFIGURED` / `NO_CALLS` /
`ACTIVE`. Anahtarın kendisi **hiçbir koşulda okunmaz/yazılmaz**; yalnız env değişkeni **adı** ve
o adın tanımlı olup olmadığı (bool) raporlanır. Bu uç LLM'i etkinleştirmez, sağlayıcı eklemez.

## 8. Uçlar

| Uç | İçerik |
| --- | --- |
| `/learning` → "Giriş seçiciliği" | aile tablosu, kapılar, replay denetimi, işlem bazlı karar, **politika/config/kod kimliği** |
| `/api/entry-selectivity` | aynı rapor + snapshot kapsamı; dosya yoksa 200 + `available=false` |
| `/api/llm-status` | LLM gerçek durumu; sır içermez |

Rapor kendi kimliğini taşır (`policy_version`, `config_id`, `code_sha`, `config_hash`, `run_id`)
ve panel bunu gösterir: kimliğini söylemeyen bir kanıt belgesi, sonradan hangi sürümün ürettiği
bilinemediği için denetlenemez.

Hepsi salt okunurdur ve bozuk/eksik şemada **500 vermez** (`test_28`).

## 9. Testler

`tests/test_entry_selectivity_challenger_v1.py` — 37 zorunlu senaryo, parametrelendirme ile 56
toplanan test. Kapsam: no-lookahead, `UNKNOWN` sözleşmesi, deterministik kimlik, tekilleştirme,
legacy dışlama, AST izolasyonu, aktif kararın değişmezliği, pozisyon fingerprint'i, maliyet ve
funding, walk-forward izolasyonu, panel dayanıklılığı, PAPER/live değişmezleri, beş ailenin her
biri için ACCEPT/VETO/`MISSING_DATA`, `applied=false`, config fail-closed ve `p_win` ters
kalibrasyonunun maskelenmediği.

## 10. Deployment

CI tamamen yeşil olmadan VPS'e geçilmez. Sonra sırayla: salt okunur preflight, doğrulanmış yedek,
futures + spot fingerprint, `merge --ff-only`, yalnız SHADOW config, restart, iki doğal tur
canary, `applied = 0` kanıtı, açık pozisyon/stop/TP/boyut/kaldıraç/sermaye/risk bütçesinin
değişmediğinin kanıtı, endpoint smoke.

Fingerprint alan kümesi — futures: `side, qty, entry_avg, stop, take_profit, targets,
targets_hit, leverage, isolated_margin, tp1_done, initial_stop, initial_qty`; spot: `assets,
lots, locked_assets, position_meta, cash, open_orders` (**`positions` anahtarı spot defterinde
YOKTUR**; boş sözlük üzerinden hash almak vacuous kanıt üretir).

Elle pozisyon açma/kapatma/değiştirme YOK. Giriş filtresi ya da çıkış politikası aktive etme YOK.
Sermaye, risk bütçesi, kaldıraç değiştirme YOK.

---

## 11. Üretim deployment kaydı (2026-09-02, PAPER SHADOW)

| Alan | Değer |
| --- | --- |
| Başlangıç VPS SHA | `8fc75030c4fd6fc92b02e2326c0e3aa65da81f45` |
| Ara SHA (canary 1) | `619386994ec548870e55a9849ea36c26f0e25ab8` — CI run 33666234372, 3/3 yeşil |
| **Nihai VPS SHA** | **`dda788cffbfabc790224c2274183a4356d5fe30a`** — CI run 33673523741, 3/3 yeşil |
| Branch | `feature/quant-evaluation-v1`, çalışma ağacı temiz |
| Yedek | `data/backups/daily/tradingbot-daily-20260902T190108Z.tar.gz` |
| Yedek sha256 | `ad6411b067fc18362613586141d1b33cdc59fd3494c3988904d2a43b9fb7a84c` |
| Yedek içeriği | 374 dosya / 382 tar üyesi, **hepsi `state/` altında**, `sha256sum -c` OK, `tar -tzf` OK |
| Rollback (özellik öncesi) | tag `backup/vps-pre-entry-selectivity-8fc7503` |
| Rollback (asgari) | `.last_good_commit` = `6193869` |

### 11.1 Transfer yolu — bundle

GitHub anonim git-RPC'si bu VPS IP'sinden **401** dönüyor (protokol v0 ile de). Depo herkese
açık olmasına rağmen (`info/refs` 200) `fetch` başarısız. Deployment bu yüzden **doğrulanmış
git bundle** ile yapıldı: bundle yerelde üretildi, sha256'sı iki uçta karşılaştırıldı, VPS'te
`git bundle verify` ile doğrulandı ve `merge --ff-only` sonrası HEAD SHA'sı hedefle birebir
eşleştirildi. `reset --hard`, `clean`, `rebase`, force push YOK.

> İlk `fetch` denemesi 401 aldığında betiğin SHA kapısı devreye girdi ve **bayat bir
> `origin/...` ref'iyle yanlış deployment yapılmasını engelledi** (HEAD `8fc7503`te kaldı).

### 11.2 Config değişikliği YAPILMADI

`config.yaml` içinde `entry_selectivity:` bölümü **yoktur ve eklenmemiştir**. Dataclass
varsayılanları zaten `mode=SHADOW`, `snapshot_enabled=true`, `auto_promotion=false` üretir.
Restart'tan ÖNCE gerçek `config.yaml` ile yüklenip doğrulandı; ayrıca `PAPER_BOUNDED`/`ACTIVE`/
`auto_promotion=true` üçü de üretimde canlı olarak `ConfigError` üretti. Fail-closed tasarımın
karşılığı budur: kodu deploy etmek SHADOW'dan başka bir şey üretemez.

### 11.3 Değişmezlik kanıtı — altı fingerprint

Deploy öncesi (18:56:49Z), deploy sonrası (19:06:12Z) ve iki deploy + üç restart + dört doğal
turdan sonra (20:08:20Z) **birebir aynı**:

| Fingerprint | Değer |
| --- | --- |
| futures (9 pozisyon × 11 alan) | `195d0506c017744acc178357632084b8277fe4f4942751b585ed990282aa1e5f` |
| spot (`assets/lots/locked_assets/position_meta/cash/open_orders`) | `ff3b6a3df374c96dfd98b00da70559f1dc6a14bcc09897a254ef8706b9c720e8` |
| katkı sermayesi | `c51a9db2ad9a504fa5b04c82ab71b1ae5e9e9fc06b93061a415caa88369cf5ae` |
| risk profili | `de5c141317fadc8d008ba8cb13626dd24074bd9fdedb82ea685f1f4d62588e7b` |
| pozisyon ekonomisi | `57f1c493e012802f07d0cc8619917aed8e5203b4a683fd64d0c1dcbfa057616d` |
| kapanışlar | `2d1f74e927a11d0f848369345b498a65c70d4c7fa440f9fd29f6bc93204f21ef` |

> **`take_profit` fingerprint alan kümesinden ÇIKARILMALI:** üretimdeki pozisyon nesnelerinde
> böyle bir alan YOKTUR (11/12 alan mevcut). TP bilgisi `targets` + `targets_hit` içindedir.
> Olmayan bir anahtar üzerinden hash almak, spot defterindeki `positions` anahtarıyla aynı
> vacuous-kanıt hatasıdır.

### 11.4 Canary — dört doğal tur

| Tur | SHA | Başlangıç (UTC) | `seconds` | Sembol | Aday | Yazılan | Dup | Hata |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `6193869` | 19:06:26 | 637,7 | 19 | 10 | 10 | 0 | 0 |
| 2 | `6193869` | 19:34:56 | 19,1 | 10 | 1 | 1 | 0 | 0 |
| 3 | `dda788c` | 19:38:48 | 626,8 | 19 | 10 | 10 | 0 | 0 |
| 4 | `dda788c` | 20:07:31 | 18,0 | 10 | 1 | 1 | 0 | 0 |

**İlk-tur maliyeti kodla ilgili DEĞİLDİR.** İki ayrı SHA'da iki ayrı restart, 637,7 s ve
626,8 s verdi — sabit bir restart maliyeti. Nedeni loglanmış: pattern index yüklemesi
(577.796 olay / 89 seri). Kararlı durum turları 19,1 s ve 18,0 s; log span 12,3 s ve
deploy öncesi 10 sembollü tur aralığı 10,9–12,9 s.

**Katmanın kendi maliyeti VPS'te doğrudan ölçüldü: 0,53 s/tur** (snapshot deposu 1,9 ms,
kanonik kapanışlar 4,9 ms, `trade_memory` 30,5 ms, `decision_journal` tam tarama 403,3 ms,
`build_report` 88,2 ms, `replay_audit` 1,5 ms). Toplamın %76'sı karar günlüğü taramasıdır.

### 11.5 Toplanan kanıt (4 tur sonunda)

* 22 snapshot, 22 benzersiz `candidate_id`, **0 duplicate, 0 hata**, `ts_ms` monoton (append-only).
* `code_sha` / `config_hash` / `policy_version` / `portfolio_open_risk_usdt` /
  `same_direction_open` → **22/22 dolu**. Bu beş alan eskiden HER kaynakta boştu;
  `empty_in_every_source` artık **boş liste**.
* Yasak sonuç alanı: **YOK**. `written_at_stage=RANKING`, `sees_outcome=false`.
* Alan kaynağı: ortalama 13,8 MEASURED + 12,0 MODELED + 5,2 MISSING.
* Baseline kararları dürüstçe kaydedildi: 12 `NO_TRIGGER`, 4 `RISK_CAPACITY_BLOCKED`,
  4 `RESEARCH_SIZE_ONLY`, 2 `LEVERAGE_GATE_BLOCKED`, 0 kabul.
* `applied_total = 0`, `auto_promotion = false`, `verdict = INSUFFICIENT_ENTRY_SAMPLE`,
  beş ailenin her biri 0/15 kapı.
* Worker log: 0 Traceback, 0 CRITICAL, 0 ERROR, 1 WARNING (önceden var olan spot gap-reconcile).

### 11.6 Depolama — DÜRÜST UYARI

`entry_snapshot.jsonl` ölçülen büyüme: **~4,85 KB/snapshot**, gün başına yaklaşık **1,97 MB**
(74 tur × ort. 5,5 aday). 30 günlük terfi penceresi ≈ 60 MB; disk 71,4 GB boş.

**Bu dosya için yapılandırılmış rotasyon YOKTUR.** `max_snapshots_per_cycle=200` yalnız TUR
BAŞI sınırdır, ömür boyu büyümeyi sınırlamaz. Bugün hiçbir kanıt silinmiyor (append-only,
budama yok) — fakat kalıcı çözüm gerektiğinde `journal_archive` kalıbı gibi **arşiv-önce**
bir rotasyon eklenmeli; sessiz silme bu hattın sözleşmesine aykırıdır.

### 11.7 İlk gerçek bağ — `PENDING_FIRST_REAL_LINK`

Dört doğal turun tamamında `opened = 0` olduğu için **hiçbir yeni pozisyon açılmadı** ve
`link` satırı üretilmedi (0 link). Bu beklenen ve dürüst durumdur; bağ üretmek için işlem
UYDURULMADI. Sıradaki doğal açılışta `trade_id → candidate_id` bağı yazılacaktır.

İzleme komutu:

```bash
sudo -n grep -c '"kind": "link"' /opt/tradingbot/data/state/entry_snapshot.jsonl
```

### 11.8 Rollback

```bash
sudo -n -u tradingbot git -C /opt/tradingbot/app merge --ff-only 6193869 || \
  sudo -n -u tradingbot git -C /opt/tradingbot/app checkout backup/vps-pre-entry-selectivity-8fc7503
sudo -n systemctl restart tradingbot-worker.service tradingbot-dashboard.service
```

State'e dokunmaz. Yedekten dönmek gerekirse: `deploy/restore.sh` +
`tradingbot-daily-20260902T190108Z.tar.gz`.

> `deploy/update.sh` KULLANILMAZ: 13. satırda `deploy/backup.sh manual` çağırıyor, CLI ise
> yalnız `--daily`/`--hourly` kabul ediyor → `set -euo pipefail` altında git adımına
> varmadan durur.
