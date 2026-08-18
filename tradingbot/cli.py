"""Komut satırı.

  python -m tradingbot run                # tam döngü: veri → analiz → karar → kağıt işlem → Obsidian
  python -m tradingbot run --loop 240     # her 240 dakikada bir tekrar (4h bar kapanışlarıyla uyumlu)
  python -m tradingbot analyze --symbols BTC/USDT ETH/USDT
  python -m tradingbot sweep --symbols SOL/USDT --families rsi_mr donchian
  python -m tradingbot fetch              # sadece veriyi önbelleğe al
  python -m tradingbot obsidian           # son state/signals.json'dan Obsidian'ı yeniden yaz
  python -m tradingbot reset-portfolio    # kağıt portföyü sıfırla
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .analyzer import CoinAnalysis, analyze_symbol
from .config import BotConfig, load_config
from .data import MarketData
from .decision import Decision, DecisionSummary, decide
from .exchange_rules import ExchangeRules
from .obsidian import ObsidianWriter
from .obsidian_agents import ObsidianAgentWriter
from .portfolio import Portfolio
from .signals import apply_paper, build_report, console_table, now_iso, persist

log = logging.getLogger("tradingbot")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _market(cfg: BotConfig) -> MarketData:
    return MarketData(cfg.exchange.candidates, cfg.exchange.timeframe, cfg.exchange.history_days, cfg.cache_path,
                      source=cfg.exchange.source, tv_exchange=cfg.exchange.tv_exchange)


def _rules(cfg: BotConfig, symbols: list[str]) -> ExchangeRules:
    return ExchangeRules(cfg.cache_path / "exchange_rules.json", cfg.exchange.rules_exchange).load(symbols)


def _symbols(cfg: BotConfig, args) -> list[str]:
    return list(args.symbols) if getattr(args, "symbols", None) else list(cfg.coins)


# ------------------------------------------------------------------ komutlar
def cmd_fetch(cfg: BotConfig, args) -> int:
    md = _market(cfg)
    data = md.fetch_many(_symbols(cfg, args))
    for s, df in data.items():
        print(f"{s:<10} {len(df):>6} bar  {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d %H:%M}")
    print(f"Kaynak: {md.source_label}  Önbellek: {cfg.cache_path}")
    return 0


def _analyze_all(cfg: BotConfig, symbols: list[str], families: list[str] | None) -> tuple[list[CoinAnalysis], str]:
    md = _market(cfg)
    data = md.fetch_many(symbols)
    rules = _rules(cfg, symbols)
    analyses: list[CoinAnalysis] = []
    for s in symbols:
        if s not in data:
            a = CoinAnalysis(symbol=s)
            a.error = "Veri alınamadı"
            a.reasons.append(a.error)
            analyses.append(a)
            continue
        t0 = time.time()
        r = rules.get(s)
        a = analyze_symbol(s, data[s], cfg, families=families, min_notional=r.min_cost, amount_step=r.amount_step)
        log.info("%s analiz: %s skor=%d strateji=%s edge=%s (%.1fs)", s, a.signal, a.score, a.best_strategy, a.has_edge, time.time() - t0)
        analyses.append(a)
    return analyses, md.source_label


def cmd_analyze(cfg: BotConfig, args) -> int:
    analyses, ex = _analyze_all(cfg, _symbols(cfg, args), args.families)
    print(f"Borsa: {ex}")
    for a in analyses:
        print(f"\n== {a.symbol} → {a.signal} (skor {a.score}) ==")
        print(f"  fiyat {a.price:.6g} | RSI {a.rsi14:.0f} | rejim {a.regime} | ATR% {a.atr_pct:.2f} | ADX {a.adx14:.0f}")
        print(f"  strateji {a.best_strategy} | edge {a.has_edge} ({a.edge_note})")
        print(f"  IS  : {a.train_metrics}")
        print(f"  OOS : {a.test_metrics}")
        for r in a.reasons:
            print(f"  - {r}")
    return 0


def cmd_sweep(cfg: BotConfig, args) -> int:
    from .data import bars_per_year
    from .sweep import sweep_symbol
    md = _market(cfg)
    syms = _symbols(cfg, args)
    rules = _rules(cfg, syms)
    for s in syms:
        df = md.fetch(s)
        r = rules.get(s)
        res = sweep_symbol(s, df, bars_per_year=bars_per_year(cfg.exchange.timeframe), bt_cfg=cfg.backtest,
                           risk_cfg=cfg.risk, families=args.families, min_notional=r.min_cost, amount_step=r.amount_step)
        print(f"\n== {s} — {len(res.rows)} konfigürasyon | {res.edge_note}")
        if res.folds:
            print("  Walk-forward adımları:")
            for f in res.folds:
                print(f"    #{f.fold} eğitim→{f.train_end} test→{f.test_end}: {f.spec:<40} OOS Sharpe {f.test_sharpe:>6.2f}  getiri {f.test_return_pct:>6.2f}%  B&H {f.test_buy_hold_pct:>7.1f}%  işlem {f.test_trades}")
        print("  Tüm veri sıralaması (IS) + son %30 (bilgi):")
        print(f"{'#':>3} {'strateji':<44}{'IS Sharpe':>10}{'IS PF':>7}{'IS N':>6}{'s30 Sharpe':>11}{'s30 PF':>8}{'s30 N':>6}{'s30 %':>8}{'B&H %':>8}")
        for i, r in enumerate(res.rows[: args.top], 1):
            print(f"{i:>3} {r.spec.label:<44}{r.train.sharpe:>10.2f}{r.train.profit_factor:>7.2f}{r.train.trades:>6}"
                  f"{r.test.sharpe:>11.2f}{r.test.profit_factor:>8.2f}{r.test.trades:>6}{r.test.total_return_pct:>8.1f}{r.test.buy_hold_return_pct:>8.1f}")
    return 0


def _run_agents(cfg: BotConfig, symbols: list[str], analyses: list[CoinAnalysis] | None):
    from .agents import AgentRunner, persist_agents
    amap = {a.symbol: a for a in (analyses or [])}
    briefs, chief = AgentRunner(cfg).run_all(symbols, amap)
    path, alerts = persist_agents(briefs, chief, cfg.state_path)
    print("\n🧠 AJAN YÖNETİCİLERİ  —  " + chief.headline)
    for b in briefs:
        p = b.plan
        plan = (f" | {p.direction}: {p.trigger_text} · stop {p.stop:.6g} · R/R {p.rr} · lev≤{p.max_leverage}x" + (" ✅" if p.valid else f" ⛔ {p.invalid_reason}")) if b.verdict != "BEKLE" else ""
        print(f"  {b.symbol:<10} {b.verdict:<6} kanaat {b.conviction:>3}  {b.headline.split(' · ', 1)[-1][:70]}{plan}")
    for a in alerts:
        print(f"  🔔 {a}")
    return briefs, chief, alerts


def _load_last_analyses(cfg: BotConfig) -> list[CoinAnalysis]:
    src = cfg.state_path / "signals.json"
    if not src.exists():
        return []
    rep = json.loads(src.read_text(encoding="utf-8"))
    return [CoinAnalysis(**{k: v for k, v in a.items() if k in CoinAnalysis.__dataclass_fields__}) for a in rep["analyses"]]


def run_cycle(cfg: BotConfig, symbols: list[str], families: list[str] | None = None, *, write_obsidian: bool = True,
              paper: bool = True, agents: bool = True) -> dict:
    when = now_iso()
    analyses, exchange = _analyze_all(cfg, symbols, families)
    pf_path = cfg.state_path / "portfolio.json"
    portfolio = Portfolio.load(pf_path, cfg.risk.starting_equity_usdt)
    decisions, summary = decide(analyses, portfolio, cfg.risk)
    executed = apply_paper(decisions, portfolio, cfg.backtest.fee_pct, when) if paper else []
    if paper:
        portfolio.save(pf_path)
    report = build_report(cfg, exchange, analyses, decisions, summary, executed, portfolio, when)
    out = persist(report, cfg.state_path)
    print(console_table(decisions, analyses, summary))
    if executed:
        print("\nKağıt portföyde uygulanan işlemler:")
        for e in executed:
            print(f"  {e['side']:<4} {e['symbol']:<10} @ {e['price']:.6g}  " + (f"{e.get('notional', 0):.0f} USDT stop {e.get('stop', 0):.6g}" if e['side'] == 'BUY' else f"P&L {e.get('pnl', 0):.2f}"))
    print(f"\nRapor: {out}")
    briefs = chief = None
    alerts: list[str] = []
    if agents:
        try:
            briefs, chief, alerts = _run_agents(cfg, symbols, analyses)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ajan katmanı hatası: %s", exc)
    if write_obsidian:
        writer = ObsidianWriter(cfg.obsidian.root, cfg.obsidian.canvas_name)
        paths = writer.write_all(analyses, decisions, summary, report.portfolio, executed,
                                 exchange=exchange, timeframe=cfg.exchange.timeframe, run_time=when, briefs=briefs, chief=chief)
        if briefs:
            ObsidianAgentWriter(cfg.obsidian.root).write_all(briefs, chief, alerts)
        print(f"Obsidian: {paths['canvas']}")
    return report.to_dict()


def cmd_agents(cfg: BotConfig, args) -> int:
    """Yalnızca ajan katmanı (WFO taraması yapmaz; son `run` analizini kullanır)."""
    symbols = _symbols(cfg, args)
    analyses = _load_last_analyses(cfg)
    briefs, chief, alerts = _run_agents(cfg, symbols, analyses)
    if not args.no_obsidian:
        ObsidianAgentWriter(cfg.obsidian.root).write_all(briefs, chief, alerts)
        src = cfg.state_path / "signals.json"
        if src.exists():  # ana şemayı da yönetici kararlarıyla güncelle
            rep = json.loads(src.read_text(encoding="utf-8"))
            decisions = [Decision(**{k: v for k, v in d.items() if k in Decision.__dataclass_fields__}) for d in rep["decisions"]]
            summary = DecisionSummary(**rep["summary"])
            ObsidianWriter(cfg.obsidian.root, cfg.obsidian.canvas_name).write_all(
                analyses, decisions, summary, rep["portfolio"], rep.get("executed", []), exchange=rep["exchange"],
                timeframe=rep["timeframe"], run_time=rep["run_time"], briefs=briefs, chief=chief)
        print(f"Obsidian ajan şemaları: {cfg.obsidian.root / 'Agents'}")
    return 0


def _print_tour(summ: dict) -> None:
    sc = summ.get("scan")
    if sc:
        print(f"🔎 TARAMA: evren {sc['universe']} → tarandı {sc['scanned']} → işaretlendi {sc['flagged']} → setup {sc['setups']}")
    print(f"🧠 {summ['chief']}")
    led = summ["ledger"]
    print(f"📈 KAĞIT FUTURES: equity {led['equity_mtm']:.2f} USDT ({led['return_pct']:+.2f}%) · açık {led['open']} · kapanan {led['closed']} · kazanma %{led['win_rate']} · ort {led['avg_r']:+.2f}R")
    for o in summ.get("opened", []):
        print(f"   ➕ AÇILDI: {o}")
    for c in summ.get("closed", []):
        print(f"   ➖ KAPANDI: {c}")
    lr = summ["learning"]
    print(f"🎓 ÖĞRENME: {lr['trades']} işlem · kazanma %{lr['win_rate']} · beklenti {lr['expectancy_r']:+.2f}R · model {'AKTİF' if lr['ready'] else 'ısınıyor'} · {summ['seconds']}s")
    if summ.get("risk"):
        r = summ["risk"]
        print(f"🛡️ RİSK: profil {r['profile']} · kill switch {r['killswitch']}" + (f" · TETİK: {r['trips']}" if r.get("trips") else ""))


def cmd_scan(cfg: BotConfig, args) -> int:
    """Tüm piyasayı tara (Binance USDT perpetual evreni), setup'ları listele, Obsidian Scanner.md yaz."""
    from .scanner import MarketScanner, persist_scan, scanner_note
    sc = MarketScanner(cfg.scanner.min_volume_usdt, cfg.scanner.flag_score, args.top or cfg.scanner.top_n, max_symbols=args.max or cfg.scanner.max_symbols)
    res = sc.scan()
    persist_scan(res, cfg.state_path)
    print(f"Evren {res.universe} → tarandı {res.scanned} → işaretlendi {res.flagged} → setup {len(res.setups)} ({res.seconds}s)")
    print(f"{'#':>2} {'sembol':<14}{'yön':<6}{'skor':>5}{'trend':>7}{'mom':>6}{'hacim':>7}{'tetik':>7}{'risk':>6}{'24s%':>7}{'hacim24':>10}  etiketler")
    for i, r in enumerate(res.setups, 1):
        print(f"{i:>2} {r.symbol:<14}{r.direction:<6}{r.score:>5}{r.trend:>7}{r.momentum:>6}{r.volume:>7}{r.catalyst:>7}{r.risk:>6}{r.chg24_pct:>+7.1f}{r.vol24_usdt/1e6:>9,.0f}M  {', '.join(r.tags)}")
    if not args.no_obsidian:
        (cfg.obsidian.root / "Scanner.md").write_text(scanner_note(res), encoding="utf-8")
        print(f"Obsidian: {cfg.obsidian.root / 'Scanner.md'}")
    return 0


def _make_engine(cfg: BotConfig, legacy: bool = False):
    """v3 motoru (Coin Heads + Risk Engine + defter v2) varsayılan; `--legacy` ile eski motor."""
    if legacy or not (cfg.v3 and cfg.v3.coin_heads.enabled):
        from .engine import TradingEngine
        return TradingEngine(cfg)
    from .engine_v3 import TradingEngineV3
    return TradingEngineV3(cfg)


def cmd_tour(cfg: BotConfig, args) -> int:
    """Tek tur: tara → ajanlar → Coin Heads → Baş Yönetici → Risk Engine → kağıt futures/spot → öğren → Obsidian."""
    eng = _make_engine(cfg, getattr(args, "legacy", False))
    summ = eng.tour(do_scan=not args.no_scan, symbols_override=args.symbols or None, obsidian=not args.no_obsidian)
    _print_tour(summ)
    return 0


def cmd_watch(cfg: BotConfig, args) -> int:
    """7/24 izleme: her `interval` dakikada tam tur (tara→ajanlar→kağıt futures→öğren);
    her yeni 4h bar kapanışında ayrıca spot walk-forward döngüsü (`run`)."""
    import signal
    from .data import TIMEFRAME_MS
    lock = None
    try:
        from .ops.lock import AlreadyRunningError, SingletonLock
        lock = SingletonLock(cfg.state_path / ".lock")
        try:
            lock.acquire()
        except AlreadyRunningError as exc:
            print(f"⛔ Başka bir `watch` süreci çalışıyor ({exc}). Aynı state/vault'a iki yazar olmaz.")
            return 3
    except ImportError:
        pass
    eng = _make_engine(cfg, getattr(args, "legacy", False))
    tf_ms = TIMEFRAME_MS[cfg.exchange.timeframe]
    last_full_bar = -1
    scan_every = max(1, args.scan_every)
    n = 0
    stop_flag = {"stop": False}
    # kooperatif durdurma: instance kaydı (pid+token) + `python -m tradingbot stop` isteğini kısa aralıklarla kontrol
    from .ops.shutdown import InstanceRecord, StopWatcher
    inst = InstanceRecord(cfg.state_path, "worker", {"interval_min": args.interval, "scan_every": scan_every})
    inst.register()
    watcher = StopWatcher(cfg.state_path, inst.token)
    if hasattr(eng, "set_stop_check"):
        eng.set_stop_check(watcher.requested)      # tur ortasında istek gelirse: yeni giriş yok, çıkışlar/defter işlemi tamamlanır

    def _sigterm(signum, frame):   # graceful: mevcut tur biter, state kaydedilir, döngü çıkar
        log.info("SIGTERM alındı — mevcut tur bitince duruluyor")
        stop_flag["stop"] = True
    try:
        signal.signal(signal.SIGTERM, _sigterm)
    except (ValueError, OSError):
        pass
    log.info("İzleme başladı [%s · %s]: tur her %d dk (tarama her %d turda bir), spot WFO döngüsü her %s bar kapanışında. Ctrl+C ile durdur.",
             cfg.mode, type(eng).__name__, args.interval, scan_every, cfg.exchange.timeframe)
    while not stop_flag["stop"]:
        try:
            now_ms = int(time.time() * 1000)
            cur_bar = now_ms // tf_ms
            if cur_bar != last_full_bar and (now_ms % tf_ms) >= 120_000 and not args.no_wfo:
                print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — SPOT WFO DÖNGÜSÜ (yeni {cfg.exchange.timeframe} bar) =====")
                run_cycle(cfg, list(cfg.coins), args.families, write_obsidian=not args.no_obsidian, paper=not args.no_paper, agents=False)
                last_full_bar = cur_bar
            print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — TUR #{n+1} =====")
            summ = eng.tour(do_scan=(n % scan_every == 0), obsidian=not args.no_obsidian)
            _print_tour(summ)
            n += 1
        except KeyboardInterrupt:
            print("İzleme durduruldu.")
            break
        except Exception as exc:  # noqa: BLE001 — tur hatası döngüyü öldürmez; kayıt + sağlık dosyasına yazılır
            log.exception("İzleme turu hatası: %s", exc)
            try:
                from .core import atomic_write_json, iso
                atomic_write_json(cfg.state_path / "health.json", {"state": "DEGRADED", "at": iso(), "error": f"{type(exc).__name__}: {exc}"[:300]})
            except Exception:  # noqa: BLE001
                pass
        if watcher.requested():
            stop_flag["stop"] = True
        # bekleme: SIGTERM'e ve stop isteğine duyarlı (2 sn parçalar)
        remaining = max(1, args.interval) * 60
        exit_every = max(10, int(getattr(args, "exit_every", 60) or 60))
        since_exit = 0
        while remaining > 0 and not stop_flag["stop"]:
            time.sleep(min(2, remaining))
            remaining -= 2
            since_exit += 2
            if watcher.requested():
                stop_flag["stop"] = True
            if since_exit >= exit_every and hasattr(eng, "exit_check"):       # hızlı çıkış monitörü: tur/taramayı beklemez
                since_exit = 0
                try:
                    closed = eng.exit_check()
                    if closed:
                        print(f"  ⏱ exit-monitor: {', '.join(c.get('symbol', '?') for c in closed)} kapandı")
                except Exception as exc:  # noqa: BLE001
                    log.exception("exit-monitor hatası: %s", exc)
    # temiz çıkış: state/log flush → istek tüket → instance kaydı sil → yalnız kendi lock'unu kaldır
    coop = watcher.requested()
    try:
        from .core import atomic_write_json, iso
        atomic_write_json(cfg.state_path / "health.json", {"state": "STOPPED", "at": iso(), "reason": "cooperative_stop" if coop else "signal", "tours": n})
    except Exception:  # noqa: BLE001
        pass
    log.info("İzleme temiz durduruldu (kooperatif=%s, tur=%d)", coop, n)
    for h in list(log.handlers) + list(logging.getLogger().handlers):
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass
    watcher.consume()
    inst.unregister()
    if lock is not None:
        try:
            lock.release(remove_file=True)
        except Exception:  # noqa: BLE001
            pass
    return 0


def _old_cmd_watch(cfg: BotConfig, args) -> int:
    """(eski) 7/24 izleme"""
    from .data import TIMEFRAME_MS
    symbols = _symbols(cfg, args)
    tf_ms = TIMEFRAME_MS[cfg.exchange.timeframe]
    last_full_bar = -1
    log.info("İzleme başladı: ajanlar her %d dk, tam döngü her %s bar kapanışında. Ctrl+C ile durdur.", args.interval, cfg.exchange.timeframe)
    while True:
        try:
            now_ms = int(time.time() * 1000)
            cur_bar = now_ms // tf_ms
            if cur_bar != last_full_bar and (now_ms % tf_ms) >= 120_000:   # bar kapanışından ≥2 dk sonra
                print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — TAM DÖNGÜ (yeni {cfg.exchange.timeframe} bar) =====")
                run_cycle(cfg, symbols, args.families, write_obsidian=not args.no_obsidian, paper=not args.no_paper, agents=True)
                last_full_bar = cur_bar
            else:
                print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} — AJAN TURU =====")
                analyses = _load_last_analyses(cfg)
                briefs, chief, alerts = _run_agents(cfg, symbols, analyses)
                if not args.no_obsidian:
                    ObsidianAgentWriter(cfg.obsidian.root).write_all(briefs, chief, alerts)
        except KeyboardInterrupt:
            print("İzleme durduruldu.")
            return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("İzleme turu hatası: %s", exc)
        time.sleep(max(1, args.interval) * 60)


def cmd_run(cfg: BotConfig, args) -> int:
    symbols = _symbols(cfg, args)
    if args.loop:
        log.info("Döngü modu: her %d dakikada bir. Durdurmak için Ctrl+C.", args.loop)
        while True:
            try:
                run_cycle(cfg, symbols, args.families, write_obsidian=not args.no_obsidian, paper=not args.no_paper)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                log.exception("Döngü hatası: %s", exc)
            time.sleep(args.loop * 60)
    run_cycle(cfg, symbols, args.families, write_obsidian=not args.no_obsidian, paper=not args.no_paper)
    return 0


def cmd_obsidian(cfg: BotConfig, args) -> int:
    """state/signals.json'dan Obsidian'ı yeniden üret (ağ gerekmez)."""
    src = cfg.state_path / "signals.json"
    if not src.exists():
        print("state/signals.json yok — önce `run` çalıştır.")
        return 1
    rep = json.loads(src.read_text(encoding="utf-8"))
    analyses = [CoinAnalysis(**{k: v for k, v in a.items() if k in CoinAnalysis.__dataclass_fields__}) for a in rep["analyses"]]
    decisions = [Decision(**{k: v for k, v in d.items() if k in Decision.__dataclass_fields__}) for d in rep["decisions"]]
    summary = DecisionSummary(**rep["summary"])
    writer = ObsidianWriter(cfg.obsidian.root, cfg.obsidian.canvas_name)
    paths = writer.write_all(analyses, decisions, summary, rep["portfolio"], rep.get("executed", []),
                             exchange=rep["exchange"], timeframe=rep["timeframe"], run_time=rep["run_time"])
    print(f"Obsidian yeniden yazıldı: {paths['canvas']}")
    return 0


def cmd_reset_portfolio(cfg: BotConfig, args) -> int:
    p = cfg.state_path / "portfolio.json"
    if p.exists():
        backup = p.with_suffix(f".{int(time.time())}.bak.json")
        p.replace(backup)
        print(f"Eski portföy yedeklendi: {backup}")
    print(f"Kağıt portföy sıfırlandı (başlangıç {cfg.risk.starting_equity_usdt} USDT).")
    return 0


# ------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradingbot", description="Çok-coinli analiz/karar/sinyal botu (paper, Obsidian entegrasyonlu)")
    p.add_argument("--config", default=None, help="config.yaml yolu")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--symbols", nargs="*", help="Örn: BTC/USDT ETH/USDT (varsayılan: config)")
        sp.add_argument("--families", nargs="*", choices=["rsi2_pullback", "ema_pullback", "rsi_mr", "ema_trend", "donchian", "bb_mr"], help="Strateji aileleri")

    s = sub.add_parser("fetch", help="Veriyi çek/önbelleğe al"); common(s); s.set_defaults(fn=cmd_fetch)
    s = sub.add_parser("analyze", help="Coin analizlerini yazdır"); common(s); s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("sweep", help="Parametre taraması tablosu"); common(s); s.add_argument("--top", type=int, default=15); s.set_defaults(fn=cmd_sweep)
    s = sub.add_parser("run", help="Tam döngü"); common(s)
    s.add_argument("--loop", type=int, default=0, help="Dakika cinsinden tekrar aralığı (0 = tek sefer)")
    s.add_argument("--no-obsidian", action="store_true"); s.add_argument("--no-paper", action="store_true", help="Kağıt portföyü güncelleme")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("agents", help="Çoklu ajan analizi (coin yöneticileri + baş yönetici)"); common(s)
    s.add_argument("--no-obsidian", action="store_true"); s.set_defaults(fn=cmd_agents)
    s = sub.add_parser("scan", help="Tüm Binance USDT perpetual piyasasını tara, setup'ları sırala"); s.add_argument("--top", type=int, default=0)
    s.add_argument("--max", type=int, default=0, help="en fazla kaç sembol (hacme göre)"); s.add_argument("--no-obsidian", action="store_true"); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("tour", help="Tek tur: tara → ajanlar → plan → kağıt futures → öğren → Obsidian"); common(s)
    s.add_argument("--no-scan", action="store_true"); s.add_argument("--no-obsidian", action="store_true"); s.set_defaults(fn=cmd_tour)
    s = sub.add_parser("watch", help="7/24 izleme: tur periyodik, spot WFO döngüsü her bar kapanışında"); common(s)
    s.add_argument("--interval", type=int, default=15, help="Tur aralığı (dakika)")
    s.add_argument("--scan-every", type=int, default=2, help="Kaç turda bir tam piyasa taraması")
    s.add_argument("--no-obsidian", action="store_true"); s.add_argument("--no-paper", action="store_true"); s.add_argument("--exit-every", dest="exit_every", type=int, default=60, help="açık pozisyon çıkış kontrolü periyodu (sn)"); s.add_argument("--no-wfo", action="store_true")
    s.set_defaults(fn=cmd_watch)
    s = sub.add_parser("obsidian", help="Son rapordan Obsidian'ı yeniden yaz"); s.set_defaults(fn=cmd_obsidian)
    s = sub.add_parser("reset-portfolio", help="Kağıt portföyü sıfırla"); s.set_defaults(fn=cmd_reset_portfolio)
    for name in ("tour", "watch"):
        sub.choices[name].add_argument("--legacy", action="store_true", help="v2 motoru (Coin Heads/Risk Engine olmadan)")
    from .cli_v3 import register as _register_v3
    _register_v3(sub)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe/emoji güvenliği
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    from .core import ConfigError
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"⛔ YAPILANDIRMA HATASI (program başlatılmadı): {exc}")
        return 2
    try:   # JSON satır log + rotasyon (ops varsa)
        from .ops.logging_setup import setup_logging as _json_logs
        if cfg.v3 and cfg.v3.monitoring.json_logs and args.cmd in ("watch", "tour", "run"):
            _json_logs("DEBUG" if args.verbose else "INFO", True, cfg.logs_path, None)
    except ImportError:
        pass
    if not cfg.coins and args.cmd not in ("doctor", "migrate", "mode-status", "risk-status", "health", "paper-status", "dashboard", "backup", "restore"):
        print("config.yaml içinde coin listesi boş.")
        return 2
    return int(args.fn(cfg, args))


if __name__ == "__main__":
    sys.exit(main())
