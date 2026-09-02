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
