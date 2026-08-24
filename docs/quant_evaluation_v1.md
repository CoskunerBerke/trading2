# Quant Evaluation V1 — Envanter ve Tasarım

Baseline: `2113f7ec781946a7261ffa384797333cd5817d9a` (branch `feature/quant-evaluation-v1`,
taban `feature/trading-v3-paper-testnet`). Baseline test: `925 passed, 19 skipped, 0 failed`.

Amaç: botu rastgele indikatör ekleyerek değil, **ölçülebilir ve doğrulanabilir** bir quant
araştırma sistemine dönüştürmek. Bu çalışma kârlılık garantisi vermez ve belirli bir kazanma
oranına optimize edilmez; ölçütler net expectancy, maliyet sonrası getiri, drawdown, tail risk,
kararlılık ve out-of-sample sonuçlardır.

## Phase 0 — Gerçek repository envanteri

| Alan | Mevcut dosya/sınıf | Durum | Eksik | V1 yaklaşımı |
| ---- | ------------------ | ----- | ----- | ------------ |
| Market-data ingest | `market/feed.py`, `market/providers.py`, `history/collector.py` | Tam çalışıyor (worker + history tier'ları) | — | Aynen kullan |
| Universe / uygunluk | `market/universe.py`, `market/scanner_fast.py` | Tam çalışıyor | Point-in-time universe replay'de sınırlı | Replay manifest'ine universe sürümü yaz |
| Coin-head / candidate | `coinhead/head.py`, `chief.py`, `specialists.py`, `schema.py` | Tam çalışıyor; deterministik `stable_id` kimlikler | — | Aynen kullan; journal join anahtarı `plan_id` |
| Rejim | `coinhead/factors.py` (regime alanları), `learn/snapshot.py` | Çalışıyor | — | Attribution boyutu olarak oku |
| Sinyal birleştirme / ensemble | `coinhead/chief.py`, `decision_gates.py` | Tam çalışıyor | — | Aynen kullan |
| Veto / risk redleri | `coinhead/redteam.py`, `risk/engine.py` (`RiskDecision.checks`) | Tam çalışıyor; red nedenleri kodlu | Red edilen adayların outcome bağlantısı dağınık | Unified journal shadow kayıtlarıyla birleştirir |
| Pozisyon büyüklüğü | `risk/engine.py:size_position` | Tam çalışıyor (deterministik) | Volatilite hedefleme yok | Risk V2 advisory (yeni, shadow-only) |
| Leverage kararı | `risk/leverage.py` (2–5x, PAPER-only, kapılar) | Tam çalışıyor, `1bf7886` ile açık | Cluster/belirsizlik girdisi yok | Risk V2 advisory önerisi; mutlak sınırlar değişmez |
| Entry/exit | `engine_v3.py`, `execution/gateway.py` (paper) | Tam çalışıyor | — | Dokunulmaz |
| PAPER execution | `execution/gateway.py`, `accounting/futures_ledger.py`, `spot_ledger.py` | Tam çalışıyor (Decimal, izole marj) | — | Aynen kullan |
| Funding/fee/slippage | `accounting/fees.py`, `funding.py`, `slippage.py` | Tam çalışıyor; fill notional üzerinden fee, 00/08/16 UTC funding | — | Replay/attribution maliyet kaynağı |
| Stop/TP + same-bar | `futures_ledger.py:tick` (likidasyon→stop→TP sırası) | Tam çalışıyor; **stop-first konservatif**, gap-through mark fill, slippage'lı | Politika manifest'te yazılı değil | Manifest'e `intrabar_policy` alanı (yeni `quant/manifest.py`) |
| Shadow book | `learn/shadow.py:ShadowBook` (dedup `_event_key`, `label_with_candles`) | Tam çalışıyor; ana ledger'dan izole (`state/shadow_book.json`) | Journal ile tek şemada birleşmiyor | Unified journal joiner |
| Outcome labeling | `learn/labels.py:label_outcome` (R bazlı WIN/LOSS/SCRATCH) | Tam çalışıyor | — | Aynen kullan |
| Decision→outcome kaydı | `learn/memory.py:TradeMemory` (JSONL, entry/exit, `snapshot.py` FeatureSnapshotV3) | Çalışıyor (kabul edilenler) | Kabul+red+shadow tek şemada değil; availability flag yok | **Phase 1: `quant/journal.py`** |
| Replay | `replay/engine.py:HistoricalReplay` (aynı Coin Head/Risk/Ledger kod yolu, ayrı state) | Tam çalışıyor; fee/slippage/funding gerçek ledger'dan | Maliyet modeli + intrabar policy manifest'te açık değil | **Phase 3: `quant/manifest.py`** (replay koduna dokunmadan) |
| Walk-forward | `replay/engine.py:walk_forward_windows` (anchored, purge+embargo bar, fail-closed tf) | Çalışıyor | Rolling mod, kilitli final holdout yok | **Phase 4: `quant/walkforward.py`** |
| Policy karşılaştırma | `replay/policy_eval.py:evaluate_policies` (train-only seçim, bootstrap CI, RESEARCH_ONLY tavanı) | Çalışıyor | PROMOTE/KEEP/REJECT kapı raporu yok | **Phase 6: `quant/champion.py`** |
| Challenger runtime | `learn/research_coordinator.py`, `research_policy.py` (durum makinesi, mode gate) | Çalışıyor (PAPER-only) | — | Aynen kullan; champion.py yalnız rapor üretir |
| Attribution | `learn/attribution.py` (kayıp analizi, `INSUFFICIENT_EVIDENCE`, min bucket) | Kısmen — association raporu | Çok boyutlu maliyet-sonrası metrik seti yok | **Phase 2: `quant/attribution.py`** |
| Calibration | `learn/calibration.py` (Platt/izotonik/Brier/ECE) | Tam çalışıyor | — | Attribution içinde yeniden kullan |
| Learning/metrics | `learn/learner_v2.py`, `telemetry.py`, dashboard `/learning` | Tam çalışıyor | — | Dokunulmaz |
| Dashboard | `dashboard/app.py` (read-only middleware: GET/HEAD dışı → 405), `views.py:json_safe` | Tam çalışıyor; RFC-safe JSON `2113f7e` ile | Quant görünümü yok | **Phase 7: `/quant` + `/api/quant/summary`** |
| Config modelleri | `config_v3.py` (dataclass bölümler, bilinmeyen anahtar uyarısı, fail-closed validate) | Tam çalışıyor | Quant eval bölümü yok | `QuantEvalSection` (güvenli varsayılanlar) |
| JSON güvenliği | `dashboard/views.py:json_safe`, `core/atomic.py` | Tam çalışıyor | — | Aynı yardımcılar kullanılır |
| Atomic write / ids | `core/atomic.py`, `core/ids.py` (`stable_id`, `payload_hash`) | Tam çalışıyor | — | Aynen kullan |
| Data quality | `market/quality.py` (`DATA_INVALID` kapısı) | Tam çalışıyor | Replay manifest bağlantısı | Manifest'e kalite raporu alanı |
| Kill switch / risk sınırları | `risk/killswitch.py`, `risk/engine.py` | Tam çalışıyor | — | Dış sınır olarak korunur |

Notlar:

* `learning_v3.auto_promote_in_paper=true` config düzeyinde zaten **ConfigError** (otomatik
  champion terfisi yasak). Quant Eval V1 aynı ilkeyi `quant_eval.auto_promotion` için tekrarlar.
* Shadow book kayıt sayısı (rapor edilen ~852 kayıt / 11 etiket) **VPS canlı state'idir**; lokal
  `state/shadow_book.json` boştur (0 kayıt) ve bu çalışmada canlı state okunmaz/değiştirilmez.
  Mekanizma kaynak (`learn/shadow.py`) ve testlerle (`tests/test_shadow_and_selectivity.py`)
  doğrulanmıştır.
* `state/`, `data/`, `logs/`, `backups/` gitignore'dadır; hiçbir runtime dosyası Git'e girmez.

## V1 mimarisi — yeni `tradingbot/quant/` paketi (yalnız offline/read-only araştırma)

Worker hot loop'una hiçbir modül eklenmez; tüm bileşenler offline çalışır ve mevcut state
dosyalarını yalnız **okur**. Yazılan tek şey kullanıcı tarafından belirtilen çıktı dizinindeki
rapor dosyalarıdır (atomic write).

| Modül | Görev | Yeniden kullandığı altyapı |
| ----- | ----- | -------------------------- |
| `quant/journal.py` | Kabul + red + shadow kayıtlarını tek `quant_journal_v1` şemasında outcome ile birleştirir; availability flag'ler, non-finite→null, idempotent dedup | `TradeMemory`, `ShadowBook`, `core.ids.stable_id`, `label_outcome`, `core.atomic` |
| `quant/attribution.py` | Çok boyutlu maliyet-sonrası attribution (JSON + insan okunur metin); `insufficient_sample`, deterministik bootstrap, PF sonsuzluk koruması, CVaR, Brier | `learn/calibration.brier`, journal satırları |
| `quant/manifest.py` | Replay/rapor run manifesti: code SHA, config hash, dataset SHA, universe, maliyet modeli, `intrabar_policy`, seed, sonuç hash, kalite raporu | `core.ids.payload_hash`, `replay/pipeline.artifact_summary` |
| `quant/walkforward.py` | Anchored+rolling fold üretimi, purge/embargo, **kilitli final holdout**, kronoloji/disjoint doğrulayıcıları, fold raporu | `replay/engine.walk_forward_windows` |
| `quant/risk_v2.py` | Advisory-only Risk V2: realized vol hedefleme, rolling korelasyon kümeleri, yönlü cluster exposure, 2–5x leverage önerisi; default disabled, emir/ledger/outbox yolu yok | `risk/leverage.LeverageConfig` sınırları (dış sınır) |
| `quant/champion.py` | Champion–challenger kapı değerlendirmesi → `PROMOTE_CANDIDATE` / `KEEP_CHAMPION` / `REJECT_CHALLENGER` (varsayılan KEEP); yalnız araştırma önerisi | `replay/policy_eval` çıktıları |
| `quant/run.py` | Offline rapor üretici (CLI): journal + attribution + champion özeti → `quant_eval.json`; canlı state'e yazmaz | üsttekiler |

Dashboard: `/quant` (HTML, boş-veri güvenli) + `/api/quant/summary` (read-only, `json_safe`).
Config: `quant_eval` bölümü — bütün flag'ler kapalı/read-only varsayılan, `auto_promotion=true`
→ ConfigError (fail-closed).

## V1 durumu ve kullanım

Bütün modüller offline'dır ve worker'a bağlanmaz. Rapor üretimi (yalnız operatör, elle):

```
python -m tradingbot.quant.run --memory state/trade_memory.jsonl \
    --shadow state/shadow_book.json --out reports/quant_eval.json
```

* Girdiler salt okunur açılır; tek yazım `--out` yoludur (atomic). `--out` bir `state/` dizinini
  gösteriyorsa açık `--allow-state-out` bayrağı gerekir (fail-closed).
* Dashboard `/quant` sayfası ve `GET /api/quant/summary`, state dizinindeki `quant_eval.json`
  dosyasını okur; dosya yoksa boş-veri güvenli davranır.
* `quant_eval` config bölümü: bütün flag'ler güvenli varsayılanda;
  `auto_promotion=true` → ConfigError.
* Walk-forward/risk-V2/champion bileşenleri kütüphane olarak kullanılır (testler örnektir);
  challenger kanıtları `replay/policy_eval` akışından gelir, `quant/run.py` tek başına asla
  `PROMOTE_CANDIDATE` üretmez.

## Completion Iteration 2 — tamamlanan zincirler

| Zincir | Durum | Kanıt |
| ------ | ----- | ----- |
| Gerçek `HistoricalReplay` E2E | `IMPLEMENTED_AND_INTEGRATED` | `tests/test_quant_replay_e2e.py` (offline) + `scripts/quant_public_smoke.py` (bounded public data) |
| Üç yollu walk-forward | `IMPLEMENTED_OFFLINE` | `quant/walkforward.py:run_three_way`, `tests/test_quant_walkforward_threeway.py` |
| Evidence bridge | `IMPLEMENTED_AND_INTEGRATED` | `quant/evidence.py`, `quant.run` içinde kullanılıyor |
| Execution senaryoları | `IMPLEMENTED_AND_INTEGRATED` | `quant/execution_scenarios.py`, rapor + dashboard |
| Risk V2 offline entegrasyon | `IMPLEMENTED_OFFLINE` (advisory) | `quant/risk_v2.py:offline_risk_report`, rapor + dashboard |
| Point-in-time eligibility | `PARTIAL_DATA` | `quant/eligibility.py` — sözleşme hazır, tarihsel arşiv henüz yok |
| Journal coverage kapıları | `IMPLEMENTED_AND_INTEGRATED` | `quant/coverage.py` → terfi kapısı |

### Ayrı komut: bounded public-data smoke

```
python scripts/quant_public_smoke.py --workdir <gecici_dizin>
```

Normal test suite AĞSIZDIR; bu betik ayrı çalıştırılır. Tek sembol, birkaç public istek, API
anahtarı yok, çıktı yalnız geçici dizine yazılır.

### Maliyet senaryoları ve provenance

Her maliyet bileşeni `OBSERVED` / `MODELED` / `FALLBACK` / `UNAVAILABLE` olarak sınıflandırılır.
Historical bid/ask ve order-book verisi **UNAVAILABLE**'dır; spread ve etki OHLCV'den türetilen
`MODELED` bileşenlerdir, latency ise bar-kesri `FALLBACK` yaklaşıklığıdır (milisaniye iddiası
yoktur). Senaryo şiddeti arttıkça maliyetler monoton artar; challenger yalnız `base`'te iyiyse
terfi önerilmez.

## Güvenlik değişmezleri

* PAPER dışı hiçbir yol açılmaz; challenger ana ledger'a/outbox'a/gateway'e dokunamaz.
* Risk V2 yalnız öneri üretir; mevcut `RiskEngine`/`KillSwitch`/leverage sınırları dış sınırdır.
* Aynı mumda stop+TP → stop-first konservatif politika (ledger davranışı; manifest'te beyan edilir).
* Yetersiz örnek → `insufficient_sample`; PF sonsuz → JSON'da `null` + flag; PBO hesaplanamıyorsa
  "hesaplanamadı" raporlanır, sahte sayı üretilmez.
* Örnek/backtest sonuçları TEST DATA olarak etiketlenir; kârlılık kanıtı değildir.
