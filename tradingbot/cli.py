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
from pathlib import Path

from .analyzer import CoinAnalysis, analyze_symbol
from .config import BotConfig, load_config
from .data import MarketData
from .decision import Decision, DecisionSummary, decide
from .exchange_rules import ExchangeRules
from .obsidian import ObsidianWriter
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


def run_cycle(cfg: BotConfig, symbols: list[str], families: list[str] | None = None, *, write_obsidian: bool = True,
              paper: bool = True) -> dict:
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
    if write_obsidian:
        writer = ObsidianWriter(cfg.obsidian.root, cfg.obsidian.canvas_name)
        paths = writer.write_all(analyses, decisions, summary, report.portfolio, executed,
                                 exchange=exchange, timeframe=cfg.exchange.timeframe, run_time=when)
        print(f"Obsidian: {paths['canvas']}")
    return report.to_dict()


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
    s = sub.add_parser("obsidian", help="Son rapordan Obsidian'ı yeniden yaz"); s.set_defaults(fn=cmd_obsidian)
    s = sub.add_parser("reset-portfolio", help="Kağıt portföyü sıfırla"); s.set_defaults(fn=cmd_reset_portfolio)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe/emoji güvenliği
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = load_config(args.config)
    if not cfg.coins:
        print("config.yaml içinde coin listesi boş.")
        return 2
    return int(args.fn(cfg, args))


if __name__ == "__main__":
    sys.exit(main())
