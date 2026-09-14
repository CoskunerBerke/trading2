# -*- coding: utf-8 -*-
"""Giris kurali kosucusu — `HistoricalReplay`i DOGRUDAN surer (kancaya callable gecmek icin).

CLI yolu callable alamaz; bu betik ayni adimlari yapar: config yukle, HistoryStore ac,
replay dizinini kanonik dogrulamayla coz, walk-forward pencerelerini kur, kosdur.
Uretim state'ine ve kullanicinin vault'una DOKUNMAZ.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import os
import sys
import time
from pathlib import Path

WT = Path(r"C:/Users/berke/wt-entry")
DATA = r"C:/Users/berke/wt-ten/data"
ROOT = Path(r"C:/Users/berke/research/entry_v1")
ST = ROOT / "state"
OUT = ROOT / "out"
OUT.mkdir(parents=True, exist_ok=True)
SYMS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]

os.environ["TRADINGBOT_CACHE_DIR"] = DATA
os.environ["TRADINGBOT_STATE_DIR"] = str(ST)
sys.path.insert(0, str(WT))
sys.path.insert(0, str(ROOT))


def _ms(v: str) -> int:
    return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def run(run_id: str, rule, date_from: str, date_to: str, *, gate: bool = False,
        config_path: str | None = None, stride: int = 1,
        trigger: bool = False, veto: bool = False, symbols: list[str] | None = None,
        strategy=None, no_breakeven: bool = False, exclude: list[str] | None = None,
        cache_dir: str | None = None) -> dict:
    syms = list(symbols) if symbols else list(SYMS)
    if cache_dir:                                   # V14: AYRI arsiv (varsayilan: modul basindaki DATA = wt-ten)
        os.environ["TRADINGBOT_CACHE_DIR"] = str(cache_dir)
    from tradingbot.config import load_config
    from tradingbot.history import HistoryStore
    from tradingbot.replay import HistoricalReplay, walk_forward_windows
    from tradingbot.replay.research import resolve_replay_dir

    cfg = load_config(config_path or str(WT / "config.yaml"))
    if no_breakeven:
        cfg.v3.futures_v3.breakeven_at_mfe_r = 0.0      # T1/T2: saf trend, basa-bas korumasi KAPALI
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    rdir = resolve_replay_dir(cfg.state_path, run_id, None)
    rp = HistoricalReplay(cfg, run_id=run_id, store=store, symbols=syms, market="futures",
                          tf="4h", seed=0, state_root=rdir.parent, pattern_engine=None,
                          start_ms=_ms(date_from), end_ms=_ms(date_to), decision_stride=stride,
                          economics_gate=gate, spot_listed=set(syms), entry_rule=rule,
                          entry_trigger=trigger, legacy_veto=veto, strategy=strategy)
    rp.load()
    if rule is not None and hasattr(rule, "attach"):
        rule.attach(rp)          # kural replay'in kendi kapanmis-bar dilimine erisir (BTC rejimi)
    ws = walk_forward_windows(rp.result.start_ms, rp.result.end_ms, train_days=180,
                              test_days=30, purge_bars=6, embargo_bars=6, tf="4h")
    t0 = time.time()
    res = rp.run(windows=ws)
    meta = {"run_id": run_id, "from": date_from, "to": date_to, "gate": bool(gate), "symbols": syms,
            "strategy": bool(strategy), "no_breakeven": bool(no_breakeven), "exclude": list(exclude or []),
            "history_root": str(store.root),          # provenans: hangi arsiv okundu
            "trigger": bool(trigger), "veto": bool(veto),
            "stride": stride, "elapsed_s": round(time.time() - t0, 1),
            "n_decisions": res.n_decisions, "n_actionable": res.n_actionable,
            "n_opened": res.n_opened, "determinism_hash": res.determinism_hash,
            "rejections": res.rejections,
            "funding_coverage": getattr(res, "funding_coverage", None)}
    io.open(OUT / ("meta_%s.json" % run_id), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=1, default=str))
    return meta


if __name__ == "__main__":
    import argparse

    import entry_rules

    ap = argparse.ArgumentParser()
    ap.add_argument("--rule", required=True, help="entry_rules adı ya da 'none'")
    ap.add_argument("--run-id", dest="run_id", required=True)
    ap.add_argument("--from", dest="f", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--trigger", action="store_true")
    ap.add_argument("--veto", action="store_true")
    ap.add_argument("--symbols", nargs="*", default=None, help="evren (varsayilan: on coin)")
    ap.add_argument("--strategy", default=None, help="strategy_rules adi (uzman yigini ATLANIR)")
    ap.add_argument("--no-breakeven", dest="no_breakeven", action="store_true")
    ap.add_argument("--cache-dir", dest="cache_dir", default=None, help="V14: TRADINGBOT_CACHE_DIR (varsayilan wt-ten/data)")
    ap.add_argument("--exclude", nargs="*", default=None,
                    help="V13R LOO: bu semboller YENI giris acamaz (veri yine yuklenir; BTC rejimi etkilenmez)")
    a = ap.parse_args()
    if a.rule == "none":
        r = None
    else:
        try:
            r = entry_rules.build(a.rule)
        except KeyError:
            import candle_rules
            try:
                r = candle_rules.build(a.rule)
            except KeyError:
                import chart_rules
                try:
                    r = chart_rules.build(a.rule)
                except KeyError:
                    import session_rules
                    try:
                        r = session_rules.build(a.rule)
                    except KeyError:
                        import regime_rules
                        r = regime_rules.build(a.rule)
    strat = None
    if a.strategy:
        import strategy_rules
        try:
            strat = strategy_rules.build(a.strategy)
        except KeyError:
            import momentum_rules
            strat = momentum_rules.build(a.strategy)
    if a.exclude and strat is not None:
        _inner, _ex = strat, set(a.exclude)

        def strat(sym, t, fr, pos, rp, _inner=_inner, _ex=_ex):   # noqa: F811 -- LOO sargisi
            if sym in _ex and pos is None:
                return None
            return _inner(sym, t, fr, pos, rp)
    print(json.dumps(run(a.run_id, r, a.f, a.to, gate=a.gate, trigger=a.trigger, veto=a.veto, symbols=a.symbols,
                         strategy=strat, no_breakeven=a.no_breakeven, exclude=a.exclude, cache_dir=a.cache_dir),
                     ensure_ascii=False, default=str))
