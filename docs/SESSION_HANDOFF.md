# SESSION HANDOFF — trading2 v3 (yeni Claude oturumu buradan başlar; repo'yu yeniden tarama)

- **Repository:** https://github.com/CoskunerBerke/trading2.git · yerel: `C:\Users\berke\Trading bot`
- **Aktif branch:** `feature/trading-v3-paper-testnet` · **HEAD:** bkz. `git rev-parse --short HEAD` (fix `2d483e6`, phase 2 `659ae70`, phase 3 `6591137`, phase 4 fail `1960f89`, ledger fix `c28af1a`, ardından `docs: record ledger restart fix and phase 4 retry`)
- **Remote:** branch origin'e push edildi (bu commit dahil); `main` değiştirilmedi (baseline `1728793`); PR/merge yok. origin/main'den 16 commit ileride.

## Tamamlanan v3 bileşenleri (özet)
`core` (Decimal/UTC/atomik yazım/id) · `storage` (SQLite WAL, journal, Parquet, idempotent migration) · `market` (Binance resmi provider'lar, rate budget, MarketFeed, DataQualityGate, universe, tier-1 scanner) · `accounting` (Decimal SpotLedger FIFO, FuturesLedgerV2 long/short izole, funding 00/08/16 UTC, MMR liq, TP/SL, gerçek başa-baş, MAE/MFE, bars_held, tick/step/min-notional, tax policy kapalı) · `execution` (order state machine, PaperGateway, testnet gateway'leri opt-in, LiveGateway kapalı, reconcile) · `coinhead` (19 uzman rolü, 11 faktör grubu, red team, spot/futures ayrı plan, registry, chief) · `risk` (profiller, RiskEngine, KillSwitch kalıcı, mod geçişleri manuel) · `learn` (değişmez hafıza, kalibre LR, Beta shrinkage, postmortem, shadow trades, champion/challenger, drift) · `llm` (noop varsayılan, POSTMORTEM_ONLY, şema, bütçe, fail-closed) · `dashboard` (13 sayfa, 127.0.0.1:8080, health/metrics/SSE) · `ops` (JSON log, lock, health, backup/restore, doctor, notify) · `engine_v3`, `cli_v3`, `config_v3`, `validation`, `futures_backtest`, `indicators_ext`, `obsidian_coinheads` · Dockerfile.v3 + compose + systemd + deploy scriptleri · 18 doküman + README v3.

## Son doğrulama (bu oturum)
- `python -m pytest tests -q` → **170 passed** (156 + 8 snapshot-sıralama + 6 v1/v2 ledger restart)
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
v1/v2 ledger restart fix (`c28af1a`) + Phase 4 RETRY: kesintisiz 60 dk, açık SUI pozisyonu bir kez resume edildi, 2 yeni gerçek PAPER pozisyon açıldı; hiçbiri kapanmadı (persisted açık).

## Snapshot fix (commit 2d483e6)
- Kök neden: `CoinHeadRegistry.run` hash `snapshot_id`'yi sözlük sırasıyla karşılaştırıyordu → tur 2 "bayat" düşüyordu.
- Çözüm: sıralama anahtarı `(snapshot_at_ms, snapshot_seq)` (engine `now_ms` + tur sayacı); `snapshot_id` yalnız opak kimlik → aynı id tekrar gelirse idempotent red; `key <= prev` → STALE (fail-closed). `coin_heads.json` artık `snapshot_order` taşır; `registry.load()` engine başlangıcında okur. Legacy hash-only state: zaman bilinmez → ilk zaman damgalı snapshot kabul, aynı id red. `registry.drops` sayaçları eklendi. Risk/execution/accounting dokunulmadı.
- Testler: `tests/test_coinhead.py` +8 (yeni-zaman/küçük-id kabul, ardışık turlar, duplicate, gerçek eski STALE, persist sonrası sıra, legacy migration + bozuk dosya, replay determinizmi, spot/futures aynı kural); mevcut registry testi olay-zamanı kuralına uyarlandı.

## Ledger restart fix (commit c28af1a)
- Kök neden: `engine.py` v1 taban sınıfı ortak `state/futures_ledger.json`'ı legacy `FuturesLedger.load` ile açıyordu; dosya v2 (`schema_version 2`, pozisyonda `market_type`) → `TypeError`, worker init'te çöktü (yalnız açık v2 pozisyon varken).
- Çözüm (migration yok): `paper_futures.read_ledger_schema` açık şema tespiti; v1 yükleyici v2/bilinmeyen/bozuk dosyayı parse ETMEZ, boş deftere ÇEVİRMEZ, üzerine YAZMAZ (`LedgerSchemaError`, dosya korunur). `engine.py` legacy defter yüklemesi `_load_legacy_ledger()`; `engine_v3` None döndürür → dosyanın tek sahibi `FuturesLedgerV2`. `FuturesLedgerV2.from_dict` desteklenmeyen `schema_version`/`kind` → `StorageError` (fail-closed). `--legacy` motor v2 dosyada kontrollü hata verir.
- Veri koruma: backup hourly 20260818T193541Z + `backups/manual/futures_ledger.pre-v1v2fix.20260818T193541Z.json`; SHA-256 `78ef85f372eaf36c…` fix öncesi/sonrası ve engine init sonrası **değişmedi**.
- Testler: `tests/test_ledger_restart.py` 6 test (v1 legacy dosya, v2 dosya, v1 red+no-overwrite+legacy motor fail-closed, bilinmeyen/bozuk şema, iki ardışık restart hash-stabil, resume→tick→hold→tek exit→restart'ta yeniden açılmaz).

## Paper soak phase 4 RETRY (2026-08-18 19:40:27Z–20:44:45Z, Windows) — **KESİNTİSİZ 60 dk, BAŞARILI (pozisyonlar açık)**
- Ön kontrol OK; backup hourly 20260818T194027Z. Worker PID 3992 (`watch --interval 15 --scan-every 2`, ayrı konsol) + dashboard PID 7040 (127.0.0.1:8080). İlk heartbeat (önceki) 17:06:31Z.
- Resume: SUI `F00001` **bir kez** yüklendi, aynı değerler (qty 23.076, entry 0.65, margin 14.9994, stop 0.677739, TP 0.605321/0.581182, entry_fee 0.0074997, fills 1); duplicate pozisyon/emir/fill 0.
- Health: live 200, ready 503→200, `/metrics` 200; heartbeat 19:41:10 → 19:58:50 → 20:15:16 → 20:33:02. Turlar 4 (T1/T3 tarama 160/165 s; T2/T4 38/38 s). Snapshot stale/dup 0; traceback/ERROR 0; ağ/veri hatası 0; LLM 0; gerçek emir 0; LIVE 0.
- Yeni gerçek PAPER tetikler: T1 **KORU/USDT SHORT F00002** (@18.21, notional 6.85, 1x, stop 20.8773, TP 12.9053/10.2480), T3 **FIL/USDT SHORT F00003** (@0.63, notional 15, 1x, stop 0.668838, TP 0.568224/0.534686). 3 açık pozisyon = max_positions; chief allow 2 (T1/T3), risk `last_decisions` 5.
- Son durum (20:41Z): SUI mark 0.6559 (unrealized ≈ −0.136), MAE −1.34 % / MFE 0, bars_held 2, stop mesafesi +3.3 %, TP1 −7.7 %; KORU mark 17.92 (≈ +0.109), MAE −1.04 % / MFE +2.25 %, bars 2; FIL mark 0.6329 (≈ −0.069), MAE −1.03 %, bars 1. Wallet 49.981577, total_fees 0.018423, funding 0, MTM equity ≈ 49.885; kill switch ARMED. Kapanış yok → trade memory exit/postmortem/learner güncellemesi yok (yapay üretilmedi).
- Gözlem: `risk.json` exposure aynı turda açılan pozisyonu bir sonraki turda gösteriyor (T1 sonrası open_positions 1, T2'de 2) — risk değerlendirmesi açılıştan önce yazılıyor; bug değil, raporlandı.
- **Shutdown bulgusu (Windows):** CTRL_C_EVENT (AttachConsole+GenerateConsoleCtrlEvent) iki sürece de etki etmedi (90/30 s); CTRL_BREAK ile dashboard **graceful** kapandı (uvicorn "shutdown complete"), worker çıktı ama graceful log satırı yok → `state/.lock` pid 3992 kaldı (doctor "kilit serbest", OK). Force kullanılmadı; temiz-graceful sayılmadı. Port 8080 kapalı, süreç 0, ledger okunabilir, kaynak/test/config değişmedi.

## Paper soak phase 4 (2026-08-18 19:28:25Z–19:29:23Z) — FAILED (özet)
Worker init'te v1 `FuturesLedger.load` v2 dosyada `TypeError` → `c28af1a` ile düzeltildi; pozisyon/ledger değişmedi.

## Paper soak phase 3 (2026-08-18 16:15:10Z–17:16:49Z, Windows) — **KESİNTİSİZ 60 dk, BAŞARILI**
- Ön kontrol: branch doğru, PAPER/PAPER_RESEARCH, LIVE kapalı (`live_order_path_enabled: false`, `ALLOW_LIVE_TRADING` unset), TESTNET kapalı, LLM `noop`, kill switch ARMED, doctor OK, önceki süreç 0, port 8080 boş, bayat lock (pid 18168, ölü) doctor "kilit serbest" → app kendi recovery'siyle lock'u devraldı. Backup: `backups/hourly/tradingbot-hourly-20260818T161510Z.tar.gz` (22 dosya).
- Worker PID 5416 (`watch --interval 15 --scan-every 2`) + dashboard PID 11248 (127.0.0.1:8080). İlk heartbeat (önceki) 15:18:14Z.
- Health: `/health/live` 200, `/health/ready` 503→200 (tur 1 sonrası), `/metrics` 200; heartbeat 16:15:51 → 16:33:31 → 16:48:57 → 17:06:31 (düzenli).
- Turlar: 4 tamamlandı — T1 tarama (160 s, 13 head), T2 çekirdek (26 s), T3 tarama (154 s), T4 çekirdek (31 s); gerçek tarama 2.
- Snapshot: yeni snapshot'lar her turda kabul; `stale_snapshot` drop **0**, duplicate drop **0**; `snapshot_order` 18 sembol persist.
- Coin Head → Chief → Risk: T1/T3 chief ranking 13, allow 2, risk `last_decisions` 6/7; T2/T4 (çekirdek) ranking 3–4, allow 0. Kararlar (T4): FUTURES_SHORT 4, FUTURES_LONG 2, HOLD 1 (SUI açık), NO_TRADE 6 (RED_TEAM_VETO 4, LOW_CONSENSUS 1, NO_VALID_PLAN 1). DATA_INVALID 0 (ağ/veri hatası yok; T2 fail-closed yolu phase 2'de gözlendi).
- **PaperGateway (gerçek piyasa tetiği):** T3'te SUI/USDT SHORT FUTURES @0.65, qty 23.076, notional 15, 1x, stop 0.6777, TP 0.6053/0.5812, P(win) %50 → PAPER emri (gerçek emir değil). T4: pozisyon HOLD, açık PnL ≈ −0.18 %.
- Ledger v2: equity 49.9925 USDT (komisyon sonrası), 1 açık pozisyon, 0 kapanış; tutarlı. Kill switch ARMED, tetiklenmedi. Gerçek emir 0, LIVE çağrısı 0, LLM çağrısı 0.
- Hatalar: traceback 0, ERROR 0, ağ/veri hatası 0.
- Kapanış: `taskkill /PID` (force yok) → her iki süreç kapandı, port 8080 kapalı, tradingbot süreci 0; `state/.lock` bayat pid 5416 dosyası kaldı (Windows'ta sinyalle temizlenmiyor) → doctor "kilit serbest", doctor OK. Kaynak/test/config/deploy değişmedi; yalnız runtime/state/log/backup + `Trading_bot/` vault değişti (commit edilmedi).

## Paper soak phase 2 (2026-08-18 14:25:38Z–≈15:18:15Z, ~53 dk) — **USER_INTERRUPTED / INCOMPLETE** (bot hatası değil; kullanıcı Claude penceresini kapattı, süreçler host tarafından sonlandı; süre phase 3'e eklenmedi)
- Worker PID 18168 (`watch --interval 15 --scan-every 2`) + dashboard PID 21460 (127.0.0.1:8080); önce `backup` (hourly 20260818T142520Z, 22 dosya). LLM `noop`, mod PAPER, LIVE/TESTNET kapalı.
- `/health/live` 200; `/health/ready` 503→200 (tur 1 sonrası); heartbeat 14:26:20 → 14:44:29 → 15:00:02 → 15:18:14 ilerledi.
- Tur 1 (tarama, 189 s, 15 head) · Tur 2 (33 s, 15 head, **kararlar üretildi — fix doğrulandı**) · Tur 3 (yeniden tarama, 192 s, 16 head) · Tur 4 (15:18:14, tüm borsalar ağ hatası → BTC/ETH/SOL DATA_INVALID, chief permission allow=false — fail-closed).
- Snapshot: kabul 15+15+16+3, `stale_snapshot` drop **0**, duplicate **0** (legacy hash-only `coin_heads.json` gerçek ortamda güvenle geçti; `snapshot_order` 16 sembol).
- Kararlar (tur 3): 16 head → FUTURES_SHORT 6, FUTURES_LONG 4, NO_TRADE 6 (LOW_CONSENSUS 2, RED_TEAM_VETO 4); chief ranking/permission dolu; risk `last_decisions` boş (tetik ateşlenmedi) → aday plan var, açılış 0, yapay trade yok.
- Ledger v2: 50.0 USDT, 0 pozisyon, 0 kapanış; kill switch ARMED; gerçek emir 0; LIVE çağrısı 0; traceback/ERROR 0.

## Paper soak phase 1 (2026-08-18 13:44–14:14Z, 30 dk) — SONUÇ (özet)
30 dk kesintisiz, health 200, tur 1 15 karar/4 aday plan/0 açılış, ledger 50 USDT; **bulgu:** tur 2 kararları hash `snapshot_id` sözlük sırası yüzünden bayat düşüyordu → `2d483e6` ile düzeltildi. İkincil: Windows'ta `kill` graceful değil → `state/.lock` bayat pid kalıyor (doctor tanıyor).

## Sonraki oturumun TEK görevi
Açık 3 PAPER pozisyonun (SUI F00001, KORU F00002, FIL F00003) doğal kapanışını izleyen 60 dk soak phase 5: TP1 kısmi/BE-stop, exit muhasebesi, trade memory + learner v2 + postmortem + Obsidian zinciri; ayrıca Windows graceful stop için `watch`'a SIGBREAK/CTRL_BREAK handler + lock release (küçük fix, testli) değerlendir.

## Sonraki oturum doğrulama komutları
```bash
git status --short && git log --oneline -3
python -m pytest tests -q
python -m tradingbot doctor --quick
python -m tradingbot mode-status
python -m tradingbot health
```
