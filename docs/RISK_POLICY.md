# RISK POLICY

Global Risk Engine (`risk/engine.py`) deterministiktir; LLM'den bağımsızdır. Bir plan `RiskEngine.evaluate(plan, PortfolioState)` ile değerlendirilir; boyut yalnız **aşağı** ayarlanır.

## Profiller (`risk/profiles.py`)
| | PAPER_RESEARCH | TESTNET / SHADOW_LIVE | LIVE_LIMITED |
|---|---|---|---|
| risk/işlem % | **2.0 (mevcut davranış aynen)** | 0.5 | 0.25 |
| toplam açık risk % | 6 | 2 | 1 |
| futures max kaldıraç | 5 | 2 | 1 |
| max pozisyon (toplam / piyasa) | 6 / 3 | 3 / 3 | 2 / 2 |
| günlük / haftalık zarar durdur % | yok | 2 / 4 | 1 / 2 |
| max drawdown kill % | yok | 8 | 5 |
| küme cap · altcoin cap · marj kullanım cap | yok | 2 · 50% · 60% | 1 · 30% · 40% |
| min liq tamponu (× stop) · cooldown | yok | 3 · 3 zarar/24s, sembol 24s | 3 · 3/48s, 48s |
Migration notu: `risk.risk_per_trade_pct: 2.0` korunur (`PAPER_RESEARCH` = eski davranış). Başka profil seçilirse TESTNET önerilerinden gevşek her ayar **uyarı** üretir; saçma değerlerde (risk>10 bilinçsiz, kaldıraç>125) program başlamaz.

## Kontroller
`KILL_SWITCH_ACTIVE, STOP_PRESENT, MAX_POSITIONS, MAX_POSITIONS_MARKET, ALREADY_OPEN_SAME_SYMBOL, OPPOSITE_EXPOSURE_CONFLICT (spot long ↔ futures short), RISK_PER_TRADE, TOTAL_OPEN_RISK, LEVERAGE_CAP, MARGIN_UTILIZATION, LIQ_BUFFER, SPOT_NO_SHORT, MAX_POSITION_PCT, DAILY_LOSS, WEEKLY_LOSS, MAX_DRAWDOWN, CLUSTER_CAP, ALTCOIN_EXPOSURE, SPREAD, MIN_EXPECTED_R, CONSEC_LOSS_COOLDOWN, SYMBOL_COOLDOWN, MIN_ORDER_CONFLICT`. Spot+futures birleşik exposure. Yasak: martingale, averaging down, min emir için risk büyütme, stop kaldırma, likidasyon stop'u.

## Kill switch (`risk/killswitch.py`)
Kalıcı (`state/killswitch.json`), seviyeler ARMED → HALT_ENTRIES → HALT_ALL; tetikler: DAILY_LOSS, WEEKLY_LOSS, MAX_DRAWDOWN, STALE_DATA, WS_SEQUENCE_CORRUPTION, PRICE_DIVERGENCE, CLOCK_DRIFT, REPEATED_ORDER_REJECTION, RECONCILIATION_MISMATCH, DB_WRITE_FAILURE, DISK_FULL, RATE_LIMIT_BAN, LLM_SCHEMA_FAILURE_STREAK, MODEL_DRIFT, WIDE_SPREAD, EXTREME_VOLATILITY, EXCHANGE_MAINTENANCE, BALANCE_MISMATCH, UNEXPECTED_OPEN_POSITION, MANUAL. Yeni girişleri durdurur; koruyucu çıkışlar **her zaman** çalışır; Obsidian/dashboard'a yazılır; reset yalnız manuel `killswitch-reset --operator --note` (denetim kaydı).
