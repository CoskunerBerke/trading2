# SESSION HANDOFF — trading2 v3 (yeni Claude oturumu buradan başlar; repo'yu yeniden tarama)

- **Repository:** https://github.com/CoskunerBerke/trading2.git · yerel: `C:\Users\berke\Trading bot`
- **Aktif branch:** `feature/trading-v3-paper-testnet` · **HEAD:** bkz. `git rev-parse --short HEAD` (bu dosyanın commit'i `docs: add compact v3 session handoff`)
- **Remote:** branch origin'e push edildi (bu commit dahil); `main` değiştirilmedi (baseline `1728793`); PR/merge yok. origin/main'den 16 commit ileride.

## Tamamlanan v3 bileşenleri (özet)
`core` (Decimal/UTC/atomik yazım/id) · `storage` (SQLite WAL, journal, Parquet, idempotent migration) · `market` (Binance resmi provider'lar, rate budget, MarketFeed, DataQualityGate, universe, tier-1 scanner) · `accounting` (Decimal SpotLedger FIFO, FuturesLedgerV2 long/short izole, funding 00/08/16 UTC, MMR liq, TP/SL, gerçek başa-baş, MAE/MFE, bars_held, tick/step/min-notional, tax policy kapalı) · `execution` (order state machine, PaperGateway, testnet gateway'leri opt-in, LiveGateway kapalı, reconcile) · `coinhead` (19 uzman rolü, 11 faktör grubu, red team, spot/futures ayrı plan, registry, chief) · `risk` (profiller, RiskEngine, KillSwitch kalıcı, mod geçişleri manuel) · `learn` (değişmez hafıza, kalibre LR, Beta shrinkage, postmortem, shadow trades, champion/challenger, drift) · `llm` (noop varsayılan, POSTMORTEM_ONLY, şema, bütçe, fail-closed) · `dashboard` (13 sayfa, 127.0.0.1:8080, health/metrics/SSE) · `ops` (JSON log, lock, health, backup/restore, doctor, notify) · `engine_v3`, `cli_v3`, `config_v3`, `validation`, `futures_backtest`, `indicators_ext`, `obsidian_coinheads` · Dockerfile.v3 + compose + systemd + deploy scriptleri · 18 doküman + README v3.

## Son doğrulama (bu oturum)
- `python -m pytest tests -q` → **156 passed**
- `python -m tradingbot doctor --quick` → **OK** (1 uyarı: pyarrow opsiyonel eksik)
- `python -m tradingbot replay --symbols BTC/USDT` → deterministic: true
- Mod **PAPER**, `live_order_path_enabled: false`, `ALLOW_LIVE_TRADING` ayarlı değil
- Hassas dosya taraması: takip edilen .env/key/secret/db/log/backup **yok**

## Güvenlik durumu
Gerçek emir gönderilmedi. Binance API anahtarı istenmedi/bağlanmadı. LIVE/LIVE_LIMITED config ile açılamaz (`ConfigError`); `LiveGateway.submit` her koşulda reddeder. TESTNET ve Anthropic gerçek ağda denenmedi.

## Bilinen dört ana sınırlama
1. Ana mum akışı hâlâ TradingView/ccxt; Binance resmî provider'lar hazır/testli ama `engine_v3` ana akışına tam geçirilmedi.
2. Intrabar stop/TP kontrolü 1h uçları + last fiyat; 1m/WebSocket mark-price yok.
3. TESTNET gateway'leri ve Anthropic provider yalnız sahte HTTP/testle doğrulandı.
4. mypy çalıştırılmadı (kurulu değil); ruff yalnız syntax/undefined-name/unused-import seviyesinde temiz.

## Çalışma ağacı sınıflandırması
- Commit edilmiş: tüm `tradingbot/**`, `tests/**`, `docs/**`, `deploy/**`, `Dockerfile.v3`, `.dockerignore`, `config.yaml`, `requirements*.txt`, `README.md`.
- Commit edilmemiş meşru kaynak: yok.
- Kullanıcı Obsidian dosyaları (DOKUNMA, commit etme): `Trading_bot/**` (16 değişik + `Signals/2026-08-18 0054.md`) — bot çıktısı, kullanıcı kasası.
- Runtime/state (gitignore'da, DOKUNMA): `state/`, `data/`, `logs/`, `backups/`, `*.db`. `state/futures_ledger.json` ve `portfolio.json` 50 USDT boş defter; migration ile silinmez.
- Hassas/şüpheli: yok.

## En son tamamlanan görev
PAPER soak phase 1 (30 dk) yapıldı ve raporlandı; kaynak değişmedi.

## Paper soak phase 1 (2026-08-18 13:44–14:14Z, Windows, 30 dk) — SONUÇ
- Worker (`watch --interval 15 --scan-every 2`) + dashboard (127.0.0.1:8080) 30 dk kesintisiz; kendi PID'leri temiz durduruldu.
- `/health/live` 200, `/health/ready` 200 (ilk tur bitince), `/metrics` ok; heartbeat 13:45Z→14:04Z ilerledi.
- Tur 1: tarama evren 120 → tarandı 119 → işaretlendi 23 → setup 12 (125 s); 15 Coin Head kararı: çoğunluk NO_TRADE (LOW_CONSENSUS, RED_TEAM: WEAK_OOS_EDGE / LOW_LIQUIDITY), 4 aday plan; tetik ateşlenmedi → 0 açılış.
- Ledger v2: 50.0 USDT, 0 pozisyon, 0 kapanış; kill switch ARMED; gerçek emir 0; LIVE çağrısı 0; log'da traceback/exception 0.
- Obsidian: `Coin Heads/` 30 dosya, wikilink zinciri (Agents/Coins/Models/Portfolio/Runs) mevcut.
- **BULGU (düzeltilmedi, kaynak dokunulmadı):** Tur 2'de `CoinHeadRegistry.run` bayat-snapshot koruması hash tabanlı `snapshot_id`'yi sözlük sırasıyla karşılaştırıyor (`engine_v3.tour`: `snap_id = stable_id("snap", run_id)` monoton değil) → tur 2'nin bütün kararları "bayat" diye düşürüldü (decisions=0, chief boş). Küçük fix: engine'de `snap_id = f"{now_ms:013d}-{run_id}"` (veya registry'de zaman damgası karşılaştırması) + test.
- İkincil: `watch` stdout print'leri nohup yönlendirmesinde tamponlanıyor (`_print_tour` çıktısı geç görünür); JSON log'lar akıyor. Windows'ta `kill` graceful değil → `state/.lock` pid dosyası kalıyor ama doctor "kilit serbest" (bayat pid tanınıyor).

## Sonraki oturumun TEK görevi
Snapshot-id monotonluk bug'ını düzelt (engine_v3 + registry testi), 156+ testin geçtiğini doğrula, commit et; sonra 1 saatlik PAPER soak phase 2 (tur 2+ kararlarının üretildiğini ve chief/risk log'unun dolduğunu doğrula).

## Sonraki oturum doğrulama komutları
```bash
git status --short && git log --oneline -3
python -m pytest tests -q
python -m tradingbot doctor --quick
python -m tradingbot mode-status
python -m tradingbot health
```
