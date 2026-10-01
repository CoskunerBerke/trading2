# OBSIDIAN

Mevcut klasörler korunur (Agents, Backtests, Charts, Coins, Learning, Signals, Dashboard, Paper Futures, Portfolio, canvas'lar). Yeni (yalnız v3 yazar, `obsidian_coinheads.py`): `Coin Heads/<BASE>.md + .canvas`, `Portfolio/`, `Trades/<trade_id>.md` (kapanınca dondurulur), `Runs/YYYY-MM-DD.md` (yalnız olay: karar değişimi, açılış/kapanış, incident, günlük özet), `Models/`, `Risk/`, `Operations/Incidents.md` (cap 200), `Data Quality/`.

- Coin Head notu: verdict, spot/futures planı, rejim, MA25/MA99/EMA25/EMA99/EMA200/RSI/MACD/ATR/Bollinger/hacim/orderbook/funding/OI/korelasyon/S-R (uzman metriklerinden), red team, risk, maliyet, son işlemler, dersler, model/veri tazeliği; wikilink: coin → ajan → Coin Head → sinyal/plan → işlem → ders → model → rejim.
- Canvas: grup düğümleri, deterministik id (`BASE:role`, `BASE:a->b`), sabit grid → düzen kaymaz.
- Bloat kuralları: `Signals/` yalnız yeni run_time'da (+30 gün/200 dosya), skip-if-unchanged atomik yazım, `Alarmlar.md` 500 satır, `Coin Heads/` 48 saat eski setup'lar temizlenir (`Agents/` dokunulmaz).
- Zaman: içerik UTC alanları + Europe/Istanbul gösterim. Vault yolu env ile (`TRADINGBOT_VAULT_PATH`) Windows/Linux. `git_sync` açıksa PNG churn için `Charts/` hariç tutulması ve ayrı private repo önerilir.
