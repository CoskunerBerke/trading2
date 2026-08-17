# BASELINE AUDIT — trading2 v3 öncesi durum tespiti

- **İncelenen commit:** `1728793ca7304135e166ddc71ec6ff8cc8989bc5` (2026-08-17)
- **Çalışma dalı:** `feature/trading-v3-paper-testnet` (main'den ayrıldı; main'e dokunulmadı)
- **Audit tarihi:** 2026-08-18
- **Yöntem:** Lead Architect + 6 paralel salt-okunur audit alt ajanı (REPOSITORY_AUDITOR, EXCHANGE_ACCOUNTING, QUANT_BACKTEST, RISK_RED_TEAM, DATA_LEARNING_LLM, OBSIDIAN_DASHBOARD_DEVOPS). Hiçbir dosya değiştirilmedi; state ve vault dokunulmadı.

## 0. Baseline test sonucu

```
python -m pytest tests -q   →  24 passed in 4.64s   (Python 3.13.14, pytest 9.1.1)
```

Kurulu paketler: ccxt 4.5.61, pandas 3.0.3, numpy 2.5.0, pydantic 2.13.4, fastapi 0.139, uvicorn 0.50, plotly 6.8, matplotlib 3.11.1, websocket-client 1.9, PyYAML 6.0.3. `anthropic` SDK **yok**. `requirements.txt` yalnızca ccxt/pandas/numpy/PyYAML/python-dotenv/pytest içeriyor (matplotlib ve websocket-client eksik → README kurulumu TradingView'i sessizce ccxt'ye düşürür, grafikler sessizce kapanır).

Kullanıcının çalışma ağacındaki değişiklikler (yalnızca `Trading_bot/` vault çıktıları) **korundu**, stash/reset yapılmadı.

## 1. Mevcut mimari (doğrulanmış)

Paket ≈ 5.2k satır. İki paralel karar sistemi var:

1. **Spot WFO döngüsü** (`cli.run_cycle`): `MarketData` (TradingView WS → ccxt yedek) → `analyzer.analyze_symbol` (64 strateji × 4 anchored walk-forward fold) → `decision.decide` → `Portfolio` (`state/portfolio.json`) → `signals.json` → `ObsidianWriter`.
2. **7/24 tur** (`engine.TradingEngine.tour`): `MarketScanner` (Binance USDⓈ-M perp evreni, ccxt) → `AgentRunner` (9 ajan: volatility, trend, candles, volume, levels, momentum, analog, edge, market) → `CoinManagerAgent` (ağırlıklı bias ortalaması → LONG/SHORT/BEKLE + TradePlan) → `ChiefAgent` (BTC verdict → RISK-ON/OFF) → `_process_triggers` → `FuturesLedger` (`futures_ledger.json`) → `Learner` (`learning.json`) → grafikler → Obsidian.

State dosyaları: `portfolio.json` (50 USDT, boş), `futures_ledger.json` (50 USDT, boş, seq 0), `signals.json` (100 KB), `signals_log.jsonl`, `agents.json` (3 brief), `scan.json`, `triggers.json` (5 sembol), `alerts.log`; `learning.json` henüz oluşmamış. Migration riski bugün düşük (defterler boş) ama loader'lar geriye uyumlu olmak zorunda.

README ile kod farkları: ajan sayısı 8/9/7 tutarsız; `Portfolio.md` kapanan işlemler hiç yazılmıyor (`history` rapora konmuyor); `watch` modunda 10 coinden 7'si için ajan çalışmıyor (`agents=False`); `bars_held` hiç artmıyor; `[[Learning/Günlük]]` hiç yazılmıyor; scanner notu eşik 60'ı hard-code ediyor; requirements eksik.

## 2. Kritik bulgular (öncelikli, birleşik)

### 2.1 Veri dayanıklılığı ve durum yönetimi (HIGH)
- Tüm state/vault yazımları `write_text` ile **atomik değil** (portfolio.py:53, paper_futures.py:91, learning.py:109, engine.py:187, signals.py:67, runner.py:99, scanner.py:233, exchange_rules.py:94, obsidian*.py). Yarım JSON → `Portfolio.load`/`FuturesLedger.load` crash-loop; `Learner` bozuk dosyada **sessizce sıfırlanır** (learning.py:103) → öğrenme verisi kaybı.
- Sıralama tehlikesi: `learner.learn()` `learning.json`'ı **ledger kaydedilmeden önce** yazıyor (engine.py:121-125) → crash penceresinde aynı işlem iki kez öğrenilir/tekrar kapatılır.
- Trade hafızası kesiliyor: `history[-1000:]`, `lessons[-200:]`, `agents.json` her tur üzerine yazılıyor → değişmez dataset yok.
- Singleton kilidi yok: PC + VPS (veya `watch` + `run --loop`) aynı state/vault'a yazabilir.

### 2.2 Muhasebe doğruluğu (HIGH)
- `bar_advance` hiç `True` gelmiyor → `bars_held` her kayıtta 0 (engine.py:121 → paper_futures.py:161); "stop çok hızlı geldi" dersi her stopta yanlış tetikleniyor.
- Futures açılışında `amount_step` hiç geçilmiyor (engine.py:181); spot LOT_SIZE değerleri futures için kullanılıyor; futures MIN_NOTIONAL/PRICE_FILTER/leverage bracket yok.
- Funding: 00/08/16 UTC settlement'a hizalı değil, kaçırılan dönemler tek dönem sayılıyor, mark yerine last, tarihsel oran yerine anlık oran (paper_futures.py:166-177).
- Likidasyon: bakım marjı/bracket/fee yok, `margin*0.95` yaklaşımı, kayıp marja kırpılmıyor, likidasyon ücreti yok (paper_futures.py:58-61,183). ETH 3000 / 2x örneğinde gerçek ≈1506, kod 1575.
- "Başa-baş stop" = entry; komisyon+kayma hesaba katılmadığı için küçük zarar (paper_futures.py:194).
- Ücretler hard-code (`FUT_FEE_PCT=0.05`, `SLIP_PCT=0.03`); spot paper **backtest** fee'sini kullanıyor; maker yolu, `verified_at`, fee kaynağı yok. Float aritmetik her yerde; `int(units/step)*step` epsilon'suz.
- Stop/TP/likidasyon yalnızca 15 dk'lık tur `last` fiyatıyla; fitiller görünmez → paper sonuçları iyimser, öğrenme kirleniyor.
- Aynı tikte TP2 ve TP1 → TP2 tam kapanış (TP1 atlanır); intrabar belirsizlik kuralı yok.
- Spot `equity()` çıkış ücretini düşmez (brüt), `realized` net → karşılaştırılamaz; history'de `fee` alanı yok; spot paper'da kayma yok (backtest'te var).

### 2.3 Risk kontrolleri (HIGH)
- **Hiçbir yerde uygulanmayan:** günlük/haftalık zarar limiti, max drawdown kill switch, korelasyon/küme cap, net beta, altcoin cap, marj kullanım cap, min likidasyon tamponu, ardışık zarar cooldown, sembol cooldown, spot+futures birleşik exposure (spot BTC LONG + futures BTC SHORT mümkün; 3+3 = 6 pozisyon), gerçekleşmemiş zarara duyarlı marj kontrolü.
- Futures boyutlandırma **statik `starting_equity` (50)** üzerinden, canlı equity değil (runner.py:59, manager.py:168); %30 sabit (manager.py:171,173, engine.py:180) `max_position_pct`'yi yok sayıyor.
- **Bug:** 4h frame eksikse `last_close_4h=0` → SHORT kırılım koşulu (`0 < lvl`) her zaman doğru, dedupe yok (engine.py:166, manager.py:54).
- Stale veri kontrolü yok; ticker yoksa 1h/4h/1d kapanışına düşülüyor ve bu bayat fiyat fill/stop için kullanılıyor (base.py:70-77). `_tv_failed` süreç ömrü boyunca yapışkan (data.py:102).
- 30 adet geniş `except Exception` — kısmi veri → ajanlar yine oy verir → plan yine üretilir → işlem yine açılır. Hata sayacı/backoff/alarm yok (cli.py:250-267).
- Geri çekilme tetiği stop-out sonrası hemen tekrar girebilir (engine.py:173).

### 2.4 Backtest / istatistik (HIGH)
- Edge kapısı (OOS Sharpe ≥ 0.30, PF ≥ 1.10, ≥ 8 işlem, ~1.1 yıl OOS) coin başına ~%37 yanlış pozitif; 64 konfig × 10-22 coin için çoklu test düzeltmesi yok (deflated Sharpe, bootstrap CI, komşu parametre kararlılığı yok).
- Deploy edilen konfig = tam örneklem IS optimumu; WFO prosedürü doğrular, seçilen konfigi değil; `analyzer.py:47-49` alan adları ters (train=tam veri, test=WFO, full=son %30).
- **Futures long/short backtester yok**; yönetici planları hiç tarihsel simüle edilmedi.
- Analog ajanı: z-skorlama tam örneklem üzerinden (sıralamada hafif sızıntı), NaN ısınma satırları sıfırlanıp aday kalıyor, koşulsuz drift ile karşılaştırma yok. Forward pencere gömülü (embargo) — doğru.
- Doğrulananlar: sinyal t kapanışı → t+1 açılış fill; stop intrabar gap-aware; ücret+kayma iki yönlü; açık mum tüketicilerde düşürülüyor; Donchian shift(1).
- Gösterge kütüphanesi ince (EMA/SMA/RSI/ATR/ADX/BB/Donchian); MACD/ROC/OBV/BBW/ATR-pct/realized-vol/swing ad hoc tekrarlanıyor. Scanner'da listing yaşı filtresi yok; genç coinlerde NaN EMA200 trend skorunu sessizce bozuyor.

### 2.5 Öğrenme (HIGH)
- Online LR **normalize edilmemiş** özelliklerle (leverage 1-5, n_warnings 0-10 vs bias ±1); scaler/holdout/kalibrasyon yok → `p_win` kapısı güvenilmez.
- `datetime.now()` özelliklerde (hour_sin/cos) → tekrar üretilemez; özellikler tahmin anında ve açılışta iki kez hesaplanıyor.
- Etiket `pnl > 0` (R yok, scratch = win); "model giriş öncesi dedi" satırı kapanış-anı ağırlıklarını kullanıyor.
- Setup kara listesi n=10 ile küresel, shrinkage yok; ajan ağırlıkları n<30'da yüksek varyanslı.
- Sadece 8 uyarı + stance saklanıyor; tam raporlar, chief, gate kararları, OI/LSR, spread/depth, veri tazeliği, model/prompt sürümü, fiyat yolu, fill'ler, maliyet dökümü, karşı-olgusal sonuç yok. Negatif örnek (açılmayan planlar) hiç kaydedilmiyor.
- LLM katmanı yok.

### 2.6 Obsidian / DevOps (HIGH)
- **Dockerfile:** `.dockerignore` yok → `COPY . .` `.git/state/data/Trading_bot` imaja giriyor; `/app/state` gerçek dizin olduğundan `ln -sfn` `/app/state/state` yaratıyor → **state volume'a değil efemer katmana yazılıyor**; `sh -c` exec'siz → SIGTERM Python'a ulaşmıyor; root kullanıcı; healthcheck yok.
- `deploy/setup_vps.sh:7` `rm -rf /opt/tradingbot` → tekrar çalıştırmada state siliniyor. systemd root, hardening yok. `BULUT_KURULUM.md` düz metin git credential store öneriyor; vault iç içe git repo.
- `git_sync` her 15 dk ~15 PNG commit ediyor (~200 MB/gün), `add -A` workspace.json'ı alıyor, `pull` timeout'suz; varsayılan vault bot reposu içinde olduğundan yerelde açılırsa **bot reposunu** commit'ler.
- `Signals/<zaman>.md` her tur (~96/gün) aynı içerikle; `Agents/` eski setup notları (META, GOOGL, CL…) hiç silinmiyor; `Alarmlar.md` sınırsız.
- Canvas koordinatları düğüm sayısından türetiliyor (düzen kayıyor); grup yok; UTC/yerel saat karışık; `data/` önbelleği volume dışında.
- Log: düz metin, rotasyonsuz, run_id'siz.

### 2.7 Test kapsamı
24 test matematik ve yazıcıları doğruluyor; **runtime omurgası** (`cli`, `engine`, `scanner._features`, `data`, `tradingview`, `market`, `exchange_rules.load`, funding accrual, corrupt-state recovery, config yükleme) sıfır kapsam.

## 3. Alt ajan raporları — özet

| Ajan | İncelenen | Ana çıktı |
|---|---|---|
| REPOSITORY_AUDITOR | tüm dosya ağacı, README, state/, testler | mimari şeması, README-kod farkları (12), teknik borç listesi, state şemaları, 10 öncelikli bulgu |
| EXCHANGE_ACCOUNTING | paper_futures, portfolio, signals, exchange_rules, backtest, manager, engine | satır satır muhasebe doğrulaması, 17 bulgu, 50 USDT/2x NOTIONAL vs MARGIN sayısal örnek, Decimal model + FeeSchedule/FundingSchedule/liq/TaxPolicy tasarımı, gateway + order state machine + clientOrderId şeması |
| QUANT_BACKTEST | indicators, strategies, backtest, sweep, analyzer, analog, scanner, data | look-ahead denetimi (temiz), WFO/çoklu test kritiği, eksik metrikler, futures backtester ve validation modülü planı, champion/challenger eşikleri |
| RISK_RED_TEAM | engine, cli, decision, manager, ledgers, data, deploy | risk kontrol envanteri, uygulanmayanlar, kill-switch tetik tablosu, RiskEngine/KillSwitch API, risk profilleri (PAPER_RESEARCH geriye uyumlu), veto kodları, restart/reconciliation adımları |
| DATA_LEARNING_LLM | learning, engine, agents, obsidian, state | istatistiksel zayıflıklar, eksik özellikler, SQLite+Parquet şema, 6 katmanlı öğrenme mimarisi, LLM provider/şema/bütçe tasarımı |
| OBSIDIAN_DASHBOARD_DEVOPS | obsidian*, charts, Dockerfile, deploy, vault | vault haritası, bloat kaynakları, yeni Coin Heads yapısı, dashboard yerleşimi, compose/backup/health tasarımı |

Çelişki tespiti: raporlar arasında çelişki yok; tek yorum farkı grafik kütüphanesi (Plotly vs lightweight-charts) — karar: dashboard için pip'te zaten kurulu **plotly**'nin paketle gelen `plotly.min.js` dosyası yerel olarak servis edilecek (CDN yok, ek indirme yok); mobil için sunucu tarafında bar sayısı sınırlanacak.

## 4. Korunacaklar (dokunulmayacak davranışlar)

- Mevcut CLI komutları (`fetch/analyze/sweep/run/agents/scan/tour/watch/obsidian/reset-portfolio`) aynen çalışmaya devam eder.
- `state/*.json` dosyaları okunmaya devam eder; yeni SQLite/Parquet katmanı **yanına** eklenir; migration idempotent ve kayıpsız.
- `risk.risk_per_trade_pct: 2.0` ve mevcut %30/3 pozisyon davranışı `PAPER_RESEARCH` profilinde **aynen** korunur; yeni muhafazakâr profiller opsiyoneldir ve başlangıçta çözümlenen etkin limitler loglanır.
- Obsidian mevcut klasörleri (Agents, Backtests, Charts, Coins, Learning, Signals, Dashboard, Paper Futures, Portfolio, canvas'lar) silinmez; yeni klasörler eklenir.
- Windows .bat ve Linux/Docker kullanımı bozulmaz.

## 5. Uygulama planı (dosya sahipliği)

Paralel implementasyon alt ajanları **yalnızca yeni paketlerde** çalışır; paylaşılan dosyalar (`config.py`, `cli.py`, `engine.py`, `requirements.txt`, `README.md`, `tests/test_bot.py`) Lead Architect'e aittir.

| Faz | Paket / dosyalar | Sahip |
|---|---|---|
| 1 | `tradingbot/core/` (ids, timeutil, atomic io, money/Decimal, events), `tradingbot/storage/` (SQLite WAL şema, journal, legacy migration) | Lead + IMPL-A |
| 2 | `tradingbot/market/` (Binance resmi REST provider'ları, rate-limit bütçesi, universe, data quality gate, mock/replay) | IMPL-C |
| 3 | `tradingbot/accounting/` (models, filters, fees, funding, liquidation, tax, spot_ledger, futures_ledger v2 + legacy import) | IMPL-B |
| 4 | `tradingbot/coinhead/` (schema, factor groups, specialists adapter, red team, head, registry, chief) | IMPL-D |
| 5 | `tradingbot/risk/` (profiles, engine, killswitch, modes) | IMPL-D |
| 6 | `tradingbot/learn/` (memory, model, calibration, shadow, registry, postmortem, retrieval) | IMPL-E |
| 7 | `tradingbot/llm/` (provider, schema, budget, service) | IMPL-E |
| 8 | `tradingbot/dashboard/`, `tradingbot/obsidian_coinheads.py` | IMPL-F |
| 9 | `tradingbot/ops/` (health, logging, backup, doctor, lock), `deploy/` (compose, systemd sertleştirme, scriptler), `.dockerignore` | IMPL-F |
| 10 | `tradingbot/execution/` (gateway, order state machine, reconcile) | IMPL-G |
| entegrasyon | `config.py`, `cli.py`, `engine.py`, `requirements*.txt`, testler, docs, README | Lead |

Her faz sonunda: `pytest`, `ruff` (varsa), migration kontrolü, kısa commit.

## 6. Kabul için gerekli düzeltmeler (bu audit'ten türeyen zorunlu iş listesi)

1. Atomik yazım + tek `save_state()` sırası (ledger → learner → triggers) + singleton kilit.
2. `bars_held`, `amount_step`, funding hizalama, likidasyon formülü, break-even fee düzeltmesi, Decimal.
3. 4h frame eksikliğinde `NO_TRADE_DATA_INVALID`; stale fiyat/ticker kontrolü.
4. Global Risk Engine + kill switch; futures boyutlandırma canlı equity ile (bayrakla, varsayılan eski davranış).
5. Değişmez trade hafızası (SQLite), shadow trades, kalibrasyon, model registry.
6. Dockerfile/compose/systemd düzeltmeleri; `.dockerignore`; backup/restore; health.
7. Obsidian bloat kuralları + Coin Heads yapısı; deterministik canvas id'leri.
8. Testler: 50 USDT/2x, funding, liq, step, kill switch, schema, atomic, replay, chaos, güvenlik.
