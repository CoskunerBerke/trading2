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
v3 implementasyonu + docs + güvenlik/kaos/replay testleri; GitHub feature branch'ine checkpoint push; bu handoff.

## Sonraki oturumun TEK görevi
PAPER soak testi: `python -m tradingbot watch --interval 15 --scan-every 2` ile (Windows veya VPS) birkaç tur çalıştır, `health`/`paper-status`/`risk-status` ile izle, hataları `docs/INCIDENT_RUNBOOK.md`'ye göre ele al. Yeni özellik/refactor yok; LIVE/TESTNET başlatma yok.

## Sonraki oturum doğrulama komutları
```bash
git status --short && git log --oneline -3
python -m pytest tests -q
python -m tradingbot doctor --quick
python -m tradingbot mode-status
python -m tradingbot health
```
