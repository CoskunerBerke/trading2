# SESSION HANDOFF — trading2 v3 (yeni Claude oturumu buradan başlar; repo'yu yeniden tarama)

- **Repository:** https://github.com/CoskunerBerke/trading2.git · yerel: `C:\Users\berke\Trading bot`
- **Aktif branch:** `feature/trading-v3-paper-testnet` · **HEAD:** bkz. `git rev-parse --short HEAD` (fix `2d483e6`, ardından `docs: record paper soak phase 2`)
- **Remote:** branch origin'e push edildi (bu commit dahil); `main` değiştirilmedi (baseline `1728793`); PR/merge yok. origin/main'den 16 commit ileride.

## Tamamlanan v3 bileşenleri (özet)
`core` (Decimal/UTC/atomik yazım/id) · `storage` (SQLite WAL, journal, Parquet, idempotent migration) · `market` (Binance resmi provider'lar, rate budget, MarketFeed, DataQualityGate, universe, tier-1 scanner) · `accounting` (Decimal SpotLedger FIFO, FuturesLedgerV2 long/short izole, funding 00/08/16 UTC, MMR liq, TP/SL, gerçek başa-baş, MAE/MFE, bars_held, tick/step/min-notional, tax policy kapalı) · `execution` (order state machine, PaperGateway, testnet gateway'leri opt-in, LiveGateway kapalı, reconcile) · `coinhead` (19 uzman rolü, 11 faktör grubu, red team, spot/futures ayrı plan, registry, chief) · `risk` (profiller, RiskEngine, KillSwitch kalıcı, mod geçişleri manuel) · `learn` (değişmez hafıza, kalibre LR, Beta shrinkage, postmortem, shadow trades, champion/challenger, drift) · `llm` (noop varsayılan, POSTMORTEM_ONLY, şema, bütçe, fail-closed) · `dashboard` (13 sayfa, 127.0.0.1:8080, health/metrics/SSE) · `ops` (JSON log, lock, health, backup/restore, doctor, notify) · `engine_v3`, `cli_v3`, `config_v3`, `validation`, `futures_backtest`, `indicators_ext`, `obsidian_coinheads` · Dockerfile.v3 + compose + systemd + deploy scriptleri · 18 doküman + README v3.

## Son doğrulama (bu oturum)
- `python -m pytest tests -q` → **164 passed** (156 + 8 snapshot-sıralama regresyonu)
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
Snapshot sıralama bug'ı düzeltildi (`2d483e6 fix(coinhead): order snapshots by event time`) ve PAPER soak phase 2 yapıldı.

## Snapshot fix (commit 2d483e6)
- Kök neden: `CoinHeadRegistry.run` hash `snapshot_id`'yi sözlük sırasıyla karşılaştırıyordu → tur 2 "bayat" düşüyordu.
- Çözüm: sıralama anahtarı `(snapshot_at_ms, snapshot_seq)` (engine `now_ms` + tur sayacı); `snapshot_id` yalnız opak kimlik → aynı id tekrar gelirse idempotent red; `key <= prev` → STALE (fail-closed). `coin_heads.json` artık `snapshot_order` taşır; `registry.load()` engine başlangıcında okur. Legacy hash-only state: zaman bilinmez → ilk zaman damgalı snapshot kabul, aynı id red. `registry.drops` sayaçları eklendi. Risk/execution/accounting dokunulmadı.
- Testler: `tests/test_coinhead.py` +8 (yeni-zaman/küçük-id kabul, ardışık turlar, duplicate, gerçek eski STALE, persist sonrası sıra, legacy migration + bozuk dosya, replay determinizmi, spot/futures aynı kural); mevcut registry testi olay-zamanı kuralına uyarlandı.

## Paper soak phase 2 (2026-08-18 14:25:38Z–≈15:18:15Z, Windows, ~53 dk) — SONUÇ
- Worker PID 18168 (`watch --interval 15 --scan-every 2`) + dashboard PID 21460 (127.0.0.1:8080); önce `backup` (hourly 20260818T142520Z, 22 dosya). LLM `noop`, mod PAPER, LIVE/TESTNET kapalı.
- `/health/live` 200; `/health/ready` 503→200 (tur 1 sonrası); heartbeat 14:26:20 → 14:44:29 → 15:00:02 → 15:18:14 ilerledi.
- Tur 1 (tarama, 189 s, 15 head) · Tur 2 (33 s, 15 head, **kararlar üretildi — fix doğrulandı**) · Tur 3 (yeniden tarama, 192 s, 16 head) · Tur 4 (15:18:14, tüm borsalar ağ hatası → BTC/ETH/SOL DATA_INVALID, chief permission allow=false — fail-closed).
- Snapshot: kabul 15+15+16+3, `stale_snapshot` drop **0**, duplicate **0** (legacy hash-only `coin_heads.json` gerçek ortamda güvenle geçti; `snapshot_order` 16 sembol).
- Kararlar (tur 3): 16 head → FUTURES_SHORT 6, FUTURES_LONG 4, NO_TRADE 6 (LOW_CONSENSUS 2, RED_TEAM_VETO 4); chief ranking/permission dolu; risk `last_decisions` boş (tetik ateşlenmedi) → aday plan var, açılış 0, yapay trade yok.
- Ledger v2: 50.0 USDT, 0 pozisyon, 0 kapanış; kill switch ARMED; gerçek emir 0; LIVE çağrısı 0; traceback/ERROR 0.
- **Kesinti:** 60 dk hedefinin ~53. dakikasında worker+dashboard, Claude oturumu yeniden başlarken host tarafından sonlandırıldı (bot hatası değil; son log 15:18:15Z, kapanış satırı yok). Süreçler tekrar başlatılmadı; `state/.lock` bayat pid (18168) kaldı — doctor bayat pid'i tanıyor.
- İkincil gözlem: tur 4'te tüm ccxt borsaları aynı anda başarısız (host ağ kesintisi ile eşzamanlı) → DATA_INVALID zinciri doğru çalıştı.

## Paper soak phase 1 (2026-08-18 13:44–14:14Z, Windows, 30 dk) — SONUÇ
- Worker (`watch --interval 15 --scan-every 2`) + dashboard (127.0.0.1:8080) 30 dk kesintisiz; kendi PID'leri temiz durduruldu.
- `/health/live` 200, `/health/ready` 200 (ilk tur bitince), `/metrics` ok; heartbeat 13:45Z→14:04Z ilerledi.
- Tur 1: tarama evren 120 → tarandı 119 → işaretlendi 23 → setup 12 (125 s); 15 Coin Head kararı: çoğunluk NO_TRADE (LOW_CONSENSUS, RED_TEAM: WEAK_OOS_EDGE / LOW_LIQUIDITY), 4 aday plan; tetik ateşlenmedi → 0 açılış.
- Ledger v2: 50.0 USDT, 0 pozisyon, 0 kapanış; kill switch ARMED; gerçek emir 0; LIVE çağrısı 0; log'da traceback/exception 0.
- Obsidian: `Coin Heads/` 30 dosya, wikilink zinciri (Agents/Coins/Models/Portfolio/Runs) mevcut.
- **BULGU (düzeltilmedi, kaynak dokunulmadı):** Tur 2'de `CoinHeadRegistry.run` bayat-snapshot koruması hash tabanlı `snapshot_id`'yi sözlük sırasıyla karşılaştırıyor (`engine_v3.tour`: `snap_id = stable_id("snap", run_id)` monoton değil) → tur 2'nin bütün kararları "bayat" diye düşürüldü (decisions=0, chief boş). Küçük fix: engine'de `snap_id = f"{now_ms:013d}-{run_id}"` (veya registry'de zaman damgası karşılaştırması) + test.
- İkincil: `watch` stdout print'leri nohup yönlendirmesinde tamponlanıyor (`_print_tour` çıktısı geç görünür); JSON log'lar akıyor. Windows'ta `kill` graceful değil → `state/.lock` pid dosyası kalıyor ama doctor "kilit serbest" (bayat pid tanınıyor).

## Sonraki oturumun TEK görevi
Kesintisiz tam 60 dk PAPER soak phase 3: `stale_snapshot`=0 kalıcı doğrulama + tetik/aday plan → PaperGateway yolu (en az 1 paper emir ya da gerekçeli NO_TRADE); ardından Binance resmî provider'ları `engine_v3` ana mum akışına geçirme (sınırlama #1) planlanır.

## Sonraki oturum doğrulama komutları
```bash
git status --short && git log --oneline -3
python -m pytest tests -q
python -m tradingbot doctor --quick
python -m tradingbot mode-status
python -m tradingbot health
```
