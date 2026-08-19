# SESSION HANDOFF — trading2 v3 (yeni Claude oturumu YALNIZ bu dosyayı okuyarak devam eder; repo'yu yeniden tarama)

- **Repository:** https://github.com/CoskunerBerke/trading2.git · yerel `C:\Users\berke\Trading bot` · **branch** `feature/trading-v3-paper-testnet`
- **HEAD:** `git rev-parse --short HEAD` (bu oturum: `docs: record historical learning session` — önceki 8 mantıksal commit aşağıda). `main` değişmedi; PR/merge/tag yok; yalnız feature branch'e normal push.
- **Testler:** `python -m pytest tests -q` → **196 passed** (156 → +8 snapshot, +6 ledger restart, +9 ops/risk/stop/runtime, +6 history, +7 patterns, +4 replay/learning). Ruff E9/F63/F7/F82/F401 temiz.
- **Mod:** PAPER / profil PAPER_RESEARCH · LIVE kapalı (`live_order_path_enabled: false`, `ALLOW_LIVE_TRADING` unset) · TESTNET kapalı · LLM `noop` · API anahtarı/.env yok · kill switch ARMED.
- **Gerçek emir 0 · LIVE çağrısı 0 · TESTNET çağrısı 0 · dış LLM çağrısı 0** (bütün oturum).

## Bu oturumun commitleri (hepsi origin'de)
1. `7cfe7d5 fix(ops): refresh intratour risk and add cooperative shutdown`
2. `48912cb feat(history): add resumable historical market data`
3. `5d765fa feat(patterns): add causal features and similar-pattern evidence`
4. `9eb374a feat(learning): add accelerated hierarchical replay learning`
5. `1039db5 feat(runtime): integrate evidence with paper spot and futures`
6. `d32c954 feat(ui): expose historical evidence in Obsidian and dashboard`
7. (testler her commit'in içinde; ayrı `test:` commit gerekmedi)
8. `67f29ae docs: document historical learning architecture` (`docs/HISTORICAL_LEARNING.md` + README işareti)
Önceki oturumlar: `2d483e6` snapshot olay-zaman sırası · `c28af1a` v1/v2 ledger restart izolasyonu · phase 1–4 soak kayıtları.

## Üç açık PAPER pozisyon (değişmedi; ledger semantik değerleri korundu)
`state/futures_ledger.json` schema 2 · wallet **49.981576985** · total_fees 0.018423015 · funding 0 · history 0 · entries 3 · fills F00001-e / F00002-e / F00003-e (her biri 1)
- **F00001 SUI/USDT SHORT** qty 23.076 @ 0.65 · margin 14.9994 · 1x · stop 0.677739 · TP 0.605321 / 0.581182 · entry_fee 0.0074997
- **F00002 KORU/USDT SHORT** qty 0.376 @ 18.21 · margin 6.84696 · stop 20.8773 · TP 12.9054 / 10.2481 · entry_fee 0.0034235
- **F00003 FIL/USDT SHORT** qty 23.809 @ 0.63 · margin 14.99967 · stop 0.668837 · TP 0.568224 / 0.534686 · entry_fee 0.0074998
Backup'lar: `backups/hourly/tradingbot-hourly-20260818T205349Z…20260819T072955Z` + `backups/manual/futures_ledger.pre-hist.*.json` (git dışı). Kod öncesi sha256 `453d7158…`; Phase 5 MTM tick'leri (last_price/MAE/MFE/bars_held/updated_at) dosya hash'ini değiştirdi, semantik değerler birebir aynı (her fazdan sonra karşılaştırıldı).

## Ops/risk düzeltmeleri (commit 1)
- **Intratour risk refresh:** her PAPER fill sonrası portföy durumu yetkili spot+futures defterlerinden yeniden hesaplanır, `risk.json` atomik yazılır; sonraki aday önceki fill'i görür; kritik bölge başında da yenilenir (RLock, reservation/commit); persist hatası → yeni giriş yok (`RISK_STATE_PERSIST_FAILED`), çıkışlar sürer; retry duplicate üretmez; spot+futures birleşik exposure. Testler: 3 fill → 4. red, persist hatası, birleşik exposure.
- **Kooperatif stop:** `ops/shutdown.py` — `state/.worker_instance.json` / `.dashboard_instance.json` (pid+token), `python -m tradingbot stop [--target worker|dashboard|all] [--timeout] [--force]` token doğrulamalı atomik `.stop_request.json`; worker 2 sn'de kontrol, yeni giriş kapanır (`SHUTDOWN_REQUESTED`), tur/atomik defter işlemi biter, `health.json` STOPPED, log flush, istek tüketilir, kayıt silinir, **yalnız kendi** lock'u kaldırılır; dashboard uvicorn `should_exit`; force yok (opsiyonel `--force` graceful sayılmaz); bayat instance yalnız raporlanır, lock probe OS kilidi serbestse bayat dosyayı kaldırır. Gerçek dashboard ile doğrulandı ("panel temiz durduruldu").
- `watch --exit-every N` (vars. 60 sn): `engine.exit_check()` açık pozisyon stop/TP/liq/zaman kontrolü tur/tarama beklemeden; giriş açmaz; defter+öğrenme+risk.json günceller.

## Tarihsel sistem (commit 2–6, ayrıntı `docs/HISTORICAL_LEARNING.md`)
- `tradingbot/history/`: HistoryStore (market/symbol/tf/YYYY/MM, geniş kline şeması, funding, OI, manifest checksum/gap/dup/quality/bad_chunks/cursor), HistoryCollector (archive `.CHECKSUM` doğrulamalı → REST, resume, idempotent, fail-closed), tier A/B/C + `HistorySection` config; CLI `history-plan/collect/validate`.
- `tradingbot/patterns/`: causal feature frame (future-mutation testli), triple-barrier R sonuçları (önce stop, fee/slippage/funding, spot short yasak), `SimilarPatternEngine` (16/32/64/128; no-look-ahead + embargo + overlap purge; aynı coin/küme/evren-aynı-rejim), `compute_stats` (n, Beta P(win), Wilson CI, beklenti CI, PF, maxDD, MAE/MFE, 30/90/180/360g, edge decay; kodlar INSUFFICIENT_SAMPLE/LOW_CONFIDENCE/NEGATIVE_EXPECTANCY/EDGE_DECAY/REGIME_MISMATCH/COST_ERODED_EDGE/DATA_INVALID), `EvidencePacket` + deterministik Türkçe açıklama; CoinHead `SimilarPatternAgent` (grup `historical_edge`); CLI `build-features/pattern-query/evidence-show`.
- `learn/memory` **source namespace** (LIVE_PAPER/HISTORICAL_REPLAY/SHADOW/TESTNET/LIVE); `HierarchicalRate` global→market→cluster→regime→leaf + recency half-life; `tradingbot/replay/` event-time `HistoricalReplay` (`state/replay/<run_id>/`, walk-forward purge/embargo, determinism hash); CLI `historical-replay`, `learning-status [--replay]`.
- Runtime: `engine_v3` tur başında `state/evidence/<SYM>.json` (LONG/SHORT paket + açıklama + komşular) → `CoinHeadInputs.pattern_evidence`; Obsidian coin notunda "Benzer Geçmiş Olaylar"; dashboard `GET /api/evidence/{base}`.

## Bounded gerçek veri pilotu (2026-08-18 21:36–23:05Z; API anahtarı yok, public)
- `history-plan`: 36 seri, ~506k satır, ~21.5 MB, ~1002 istek, ETA ≈ 6 dk (KORU spot'ta yok → yalnız futures).
- `history-collect` spot+futures × {BTC, ETH, SOL, SUI, FIL} + KORU futures × {15m, 1h, 4h}, 360 gün: **455.4k satır, gap 0, duplicate 0, bad_chunk 0, 168 arşiv ayı checksum'lı**; ikinci koşu +0 satır (idempotent); `history-validate` 45 seri invalid 0 (funding/OI dahil).
- `build-features`: 33 seri, **449.540 feature satırı**.
- `historical-replay` futures 4h, 5 sembol, seed 7, stride 2, train 180g / test 30g: pattern index 4955 olay; **4650 karar, 736 aday, 45 açılış, 42 kapanış**; tümü: n 42, beklenti **+0.16R**, win %40.5, maxDD 10.7R, PF 1.23; **out-of-sample: n 19, beklenti +0.05R, win %36.8, maxDD 7.9R, PF 1.08** (sıfırdan ayırt edilemez → kenar iddiası YOK); LONG −0.39R (n 17) / SHORT +0.53R (n 25); çıkışlar stop 25 / TP2 13 / BE 4; replay defteri 50 → 60.25 (in-sample dahil). **Aynı seed iki koşu → aynı determinism hash `0ee595ba…`.**
- Pattern kanıtı örneği (SUI 4h): LONG n 60, P(kazanç) 0.37 (0.26–0.49), net −0.20R → NEGATIVE_EXPECTANCY/LOW_CONFIDENCE; SHORT n 60, P 0.56, net +0.21R ama CI alt sınırı ≤0 → LOW_CONFIDENCE (fail-closed doğru).
- Bütün evren planı: `history-plan --universe` (Tier A/B/C tahmini) — büyük indirme sonraki 7/24 sunucu aşamasına bırakıldı.

## Phase 5 — doğal exit izleme (gerçek 3 pozisyon, `watch --interval 15 --scan-every 2 --exit-every 60`)
- **Deneme 1 (2026-08-18 23:11:59Z → ≈23:33Z, ~21 dk): USER_INTERRUPTED / INCOMPLETE** — Claude oturumu kapandı, host süreçleri sonlandırdı (bot hatası değil). Bu sürede: 3 pozisyon **bir kez** resume (fills değişmedi), pattern index 10074 olay, 6 kanıt dosyası, tur 1–2 (176 s / 40 s), traceback 0, ledger 23:33:22'de tur dışı kaydedildi (exit-monitor çalıştı), `stop` bayat kayıtları dürüst raporladı.
- **Deneme 2 (2026-08-19 07:29:55Z → …): sonuç aşağıdaki "Phase 5b" bölümünde.**

## Phase 5b SONUÇ (2026-08-19 07:29:55Z → 08:30:20Z, **kesintisiz 60 dk, kooperatif stop ile temiz kapanış**)
- Worker PID 11012 (`watch --interval 15 --scan-every 2 --exit-every 60`) + dashboard 16524; backup `hourly-20260819T072955Z`. Resume: 3 pozisyon **bir kez** yüklendi (fills F0000x-e ×1 değişmedi; duplicate 0).
- Health: live 200 / ready 200 (tur 1 sonrası) / metrics 200; heartbeat 07:30:47 → 07:50:27 → 08:08:14 → 08:26:09 (4 tur: 190/47/113/28 s, 2 tarama); pattern index 10074 olay, 6 kanıt dosyası; chief allow 0–1, risk_dec 0–1 (max pozisyon dolu → yeni giriş yok). Ağ: tur 3'te Binance `fapi` read-timeout (tarama atlandı, son tarama kullanıldı, tur tamamlandı — fail-safe; ERROR 1, uygulama hatası değil, crash yok).
- Pozisyonlar 60 dk sonunda **hâlâ açık** (doğal kapanış olmadı; manuel kapatma/fiyat/yapay mum yok): SUI mark 0.6583 (−1.3 %, MAE −1.34/MFE +0.55, bars 5), KORU mark 19.84 (−9.0 %, MAE −10.93/MFE +3.68, stop 20.88'e %5 kaldı, bars 5), FIL mark 0.6375 (−1.2 %, MAE −1.44/MFE +0.43, bars 4). Exit monitörü 60 sn'de çalıştı (tur dışı ledger kayıtları); 00:00 ve 08:00 UTC funding uzlaştırmaları muhasebeleştirildi: short'lar +0.0023366 aldı → wallet **49.9839135**, entries 7, fees 0.018423015 değişmedi, history 0.
- Kapanış: `python -m tradingbot stop --target all` → worker+dashboard 3 sn'de graceful (`health.json` STOPPED/cooperative_stop, tur 4; "İzleme temiz durduruldu", "panel temiz durduruldu"); lock/instance/istek dosyaları kalktı; port 8080 kapalı; süreç 0; doctor OK; kaynak/test/config/deploy değişmedi (yalnız state/log/backup/vault + bu dosya).
- Gerçek emir 0 · LIVE 0 · TESTNET 0 · LLM 0. Sentetik testlerde TP/SL/BE/zaman çıkış zinciri + trade memory + learner v2 doğrulanmıştır (test_ops_risk_stop / test_ledger_restart / test_engine_v3).

## Bilinen sınırlamalar (dürüst)
- Gerçek WebSocket veri döngüsü yok; exit monitörü REST/last fiyatla 60 sn periyotlu; intrabar yalnız bar uçları.
- Replay CoinHead tam zinciriyle yavaş (`--stride`); pattern index bellek içi.
- OI geçmişi Binance'te ~30 gün; mark kline geçmişi yok (funding kaydındaki mark).
- LLM `noop`: açıklamalar deterministik şablon; gerçek sağlayıcı yalnız env+bütçe ile.
- Windows: konsol sinyalleri güvenilmez → kooperatif `stop` birincil yol; ölen süreçten kalan bayat instance/lock dosyaları yalnız raporlanır (lock OS kilidi serbestse probe temizler).

## Sonraki oturumun TEK görevi
3 pozisyon hâlâ açık → doğal kapanış izlemeye devam (60 dk soak + kooperatif `stop`; KORU stop'a en yakın) ve ilk gerçek kapanışta zinciri (exit fill/fee/funding/net R, trade memory, learner v2, postmortem, Obsidian Trade→Lesson→Model) doğrula; ardından `history-plan --universe` ile Tier A/B/C bütün-evren indirmesini 7/24 sunucuda başlat.

## Kesin resume komutları
```bash
git status --short && git log --oneline -3
python -m pytest tests -q
python -m tradingbot doctor --quick && python -m tradingbot mode-status && python -m tradingbot health
python -m tradingbot stop --target all --timeout 5          # bayat instance/lock raporu
python -m tradingbot futures-status && python -m tradingbot learning-status
python -m tradingbot history-validate && python -m tradingbot evidence-show --symbol SUI/USDT --market futures --tf 4h --live
python -m tradingbot watch --interval 15 --scan-every 2 --exit-every 60   # + dashboard --host 127.0.0.1 --port 8080 ; sonda: python -m tradingbot stop
```
