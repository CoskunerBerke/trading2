# COIN HEADS

Her coin için mantıksal tek bir **Coin Head** (`coinhead/head.py`, `coin_head_id = stable_id("coinhead", symbol)`); `CoinHeadRegistry` sembol başına kilit, eşzamanlılık sınırı (`max_workers`), bayat snapshot koruması (`snapshot_id` monoton) ve `state/coin_heads.json` yazımı sağlar. Ayrı OS process'i değildir (hafif actor).

## Uzmanlar (SpecialistReport şeması)
Legacy 9 ajan (`agents/*`) adaptörle sarılır; `market` ajanı **liquidity** ve **derivatives** parçalarına bölünür. Yeni uzmanlar (`coinhead/specialists.py`): `data_integrity`, `market_regime`, `multi_timeframe`, `derivatives` (funding/z-skor/OI/LSR/basis), `correlation_beta`, `orderbook_liquidity`, `risk_sizing`, `news_catalyst` (kaynak yoksa "değerlendirilmedi" — asla uydurmaz), `red_team_veto` (piyasa bazında). Şema alanları: `analysis_id, run_id, snapshot_id, symbol, market_type, agent_name, agent_version, as_of_utc, data_sources, data_freshness_seconds, timeframes, stance, bias, confidence_raw, confidence_calibrated, evidence_for, evidence_against, metrics, levels, warnings, veto, veto_reason, error, latency_ms, factor_group`.

## Faktör grupları (`coinhead/factors.py`)
`trend, momentum, volatility, volume_flow, structure_levels, liquidity, derivatives, correlation, historical_edge, catalyst, risk`. Grup içinde korelasyonlu kanıt **tek oy** (güven ağırlıklı ortalama; `n_independent` = farklı ajan sayısı; `conflict` = std). Gruplar arası ağırlık rejime göre çarpılır (`REGIME_MULT`), `dissent` = toplam işarete karşı gruplar. EMA20/50/200 vb. aynı bilgi ayrı oy sayılmaz.

## Karar
`Verdict ∈ {SPOT_LONG, FUTURES_LONG, FUTURES_SHORT, HOLD, REDUCE, EXIT, NO_TRADE, DATA_INVALID, RISK_BLOCKED}`. Sıra: veri bütünlüğü (→ `NO_TRADE_DATA_INVALID`) → konsensüs (eşik `consensus_threshold`, `min_confidence`) → spot planı (yalnız long) + futures planı (long/short) → maliyet (gidiş-dönüş komisyon + kayma + spread + horizon funding) → `expected_r` (maliyet **sonrası**) ≥ `min_expected_r` → boyut (`risk.size_position`, min emir için risk büyütülmez → `NO_TRADE_MIN_ORDER_CONFLICT`) → red team (piyasa bazlı: funding/liq futures'a özgü) → geçerli planlar arasında maliyet sonrası R'ye göre seçim. Funding aşırı pozitifken futures reddedilip **spot** seçilebilir. Açık pozisyon varsa HOLD/REDUCE/EXIT.

## Baş Yönetici (`coinhead/chief.py`)
Risk modu (BTC rejimi + breadth), küme yığılması, tur başına yeni pozisyon limiti, sıralama (`expected_r × güven − dissent cezası`), izin tablosu. Nihai onay üç bayrak: `coin_head_valid ∧ no_red_team_veto ∧ risk_engine_allowed`. LLM onayı yetersiz; LLM yalnız veto edebilir (VETO_ONLY/ADVISORY).
