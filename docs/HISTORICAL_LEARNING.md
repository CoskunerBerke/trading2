# Tarihsel Pattern Zekâsı + 7/24 Spot/Futures PAPER Öğrenme — Mimari

> Geçmiş veri geleceği kesin belirlemez. Sistem yalnız **koşullu olasılık** ve **maliyet sonrası beklenti** üretir; "pattern kesin tekrar eder",
> "%90 win-rate risksiz", "LLM ne olacağını biliyor" gibi çıktılar tasarım gereği üretilmez. Kâr garantisi yoktur.

## Katmanlar
| Katman | Modül | Görev |
|---|---|---|
| Veri gölü | `tradingbot/history/` | Binance public spot + USD-M futures: OHLCV geniş şema (quote_volume, trades, taker_*), funding, OI; `market/symbol/tf/YYYY/MM` partition; manifest (provider, first/last, rows, gap_count, duplicate_count, checksum, schema, quality, bad_chunks, cursor); archive-first (`data.binance.vision` zip + `.CHECKSUM` sha256) → rate-budgeted REST; resume; idempotent; bozuk parça fail-closed |
| Kapsam | `history/tiers.py` + `HistorySection` | Tier A (evren 1h/4h/1d MAX_AVAILABLE) · Tier B (top-50 15m+) · Tier C (top-20 + açık pozisyon: 1m 90g, 5m 365g); config ile genişletilebilir |
| Causal feature store | `tradingbot/patterns/features.py` | Bar kapanışında bilinen veri: MA/EMA mesafe-eğim-kesişim, trend persistence, HH/LL, S/R; RSI/MACD/stoch/ROC/ADX/diverjans; ATR/rv/BB/vol-pctile/expansion/downside/dd; hacim z/trend/rel/impact/taker; mum anatomisi; funding+pctile, OI, squeeze proxy; BTC rejim/ret/corr/beta; `event_ts`, `cutoff_ts`, `schema_version`, `source`, `quality`, `miss_<grup>` — gelecek mumlar geçmiş satırı değiştirmez (test) |
| Sonuçlar | `patterns/outcomes.py` | Triple-barrier: stop / TP1 kısmi / TP2 / zaman / ufuk; aynı barda stop+TP → **önce stop** (worst-case); fee, slippage, funding (yön işaretli) → net R; MAE/MFE; bars_held; spot short **yasak**; long/short ayrı |
| Benzerlik | `patterns/engine.py` `SimilarPatternEngine` | Pencere 16/32/64/128; normalize getiri yolu + standardize özellik anlık görüntüsü; mesafe = standardize öklid + korelasyon; seviyeler aynı coin / küme / evren-aynı-rejim; **ileri bakış yok** (komşunun çıkışı da sorgudan önce), embargo, overlap purge/temporal separation, tekilleştirme; deterministik |
| İstatistik | `patterns/engine.compute_stats` | n, W/L/BE, posterior P(win) (Beta), Wilson CI, mean/median R, beklenti CI, payoff, PF, maxDD, MAE/MFE, çıkış dağılımı, 30/90/180/360g, edge decay, maliyet payı, coin/rejim/market kırılımı; **fail-closed** kodlar: INSUFFICIENT_SAMPLE, LOW_CONFIDENCE, NEGATIVE_EXPECTANCY, EDGE_DECAY, REGIME_MISMATCH, COST_ERODED_EDGE, DATA_INVALID (yüksek win-rate + negatif beklenti reddedilir) |
| Kanıt | `patterns/evidence.py` | `EvidencePacket` (decision_id, symbol/market, timestamp/timeframes, regime, pattern_id, neighbor ids, bağımsız n, win rate+CI, net beklenti+CI, MAE/MFE, fee/slippage/funding, recency/decay, kanıt/karşı-kanıt, veto) + deterministik Türkçe açıklama (LLM `noop`) |
| Coin Head | `coinhead/specialists.SimilarPatternAgent` | Faktör grubu `historical_edge`; kanıt yoksa `usable=False`; kod varsa bias 0 + uyarı; korelasyonlu göstergeler grup içinde de-correlate (mevcut `factors.aggregate`) |
| Öğrenme | `learn/memory` (source namespace: LIVE_PAPER / HISTORICAL_REPLAY / SHADOW / TESTNET / LIVE), `learn/model.HierarchicalRate` (global→market→cluster→regime→leaf, recency half-life) | Replay kayıtları gerçek PAPER hafızasıyla karışmaz; az verili coin ebeveyne çekilir; kenar bozulursa güven/size düşer, NO_TRADE; martingale yok; PAPER içi otomatik terfi audit'li, TESTNET/LIVE manuel kapı, LIVE kapalı |
| Replay | `tradingbot/replay/` | Event-time, birincil tf bar kapanışı, aynı CoinHead/Chief/RiskEngine/FuturesLedgerV2/SpotLedger/LearnerV2; `state/replay/<run_id>/` (gerçek state'e dokunmaz); walk-forward (anchored, purge+embargo, ileri; shuffle yok); aynı veri+config+seed → aynı işlem hash'i |
| Runtime | `engine_v3` | Tur başında sembol için LONG/SHORT kanıtı → `CoinHeadInputs.pattern_evidence`, `state/evidence/<SYM>.json`; **hızlı exit monitörü** `exit_check()` (`watch --exit-every 60`) tur/tarama beklemeden stop/TP/liq/zaman kontrolü, giriş açmaz; fill sonrası birleşik spot+futures risk durumu anında yenilenir (`risk.json`) |
| Ops | `ops/shutdown` + `python -m tradingbot stop` | Instance kaydı (pid+token), token doğrulamalı atomik stop isteği, worker 2 sn'de kontrol, yeni giriş kapanır, tur/defter işlemi biter, flush, yalnız kendi lock'u kalkar; force yok (opsiyonel `--force` graceful sayılmaz) |
| UI | `obsidian_coinheads` "Benzer Geçmiş Olaylar" · dashboard `GET /api/evidence/{base}` | Bağımsız n, P(win)+CI, net beklenti+CI, MAE/MFE, edge decay, kısıtlar, açıklama, komşular |

## LLM'in rolü
Kalıcı öğrenme: feature store, pattern index, trade memory, LearnerV2, model registry, postmortem, retrieval kayıtları. LLM (varsayılan `noop`) yalnız
yapılandırılmış EvidencePacket'i açıklar / postmortem üretir / hipotez önerir; fiyat-istatistik-trade uyduramaz, RiskEngine'i aşamaz, emir gönderemez,
size büyütemez. Gerçek sağlayıcı yalnız env + bütçe/circuit-breaker/cache ile açılabilir (mevcut `llm/` altyapısı).

## CLI
`history-plan` (dry-run: sembol/aralık/satır/disk/istek/süre) · `history-collect` (`--market spot|futures|both --symbols --timeframes --days --max-available --from --to --no-resume --no-archive --dry-run --universe`) ·
`history-validate` · `build-features` · `pattern-query` · `evidence-show` · `historical-replay` (`--seed --run-id --state-dir --from --to --stride --train-days --test-days --purge --embargo`) ·
`learning-status [--replay <run_id>]` · `stop [--target] [--timeout] [--force]` · `watch --exit-every N`.

## Bilinen sınırlamalar (dürüst)
- Mark/index kline geçmişi ve OI geçmişi Binance public API'de sınırlı (OI ~30 gün); mark için funding kaydındaki mark kullanılır.
- Replay içinde CoinHead tam uzman zinciriyle çalışır → yavaştır (`--stride`); intrabar yalnız bar uçları (1m/WebSocket mark yok).
- Gerçek WebSocket market-data döngüsü yok; exit monitörü REST/last fiyatla periyodiktir.
- Pattern index bellek içi; büyük evrende (yüzlerce coin × 1m) disk indeks gerekir.
- LLM sağlayıcı `noop`; açıklamalar deterministik şablon.
