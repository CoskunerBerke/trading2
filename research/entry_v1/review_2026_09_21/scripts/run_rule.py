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

os.environ["TRADINGBOT_CACHE_DIR"] = DATA
os.environ["TRADINGBOT_STATE_DIR"] = str(ST)
sys.path.insert(0, str(WT))
sys.path.insert(0, str(ROOT))


def _ms(v: str) -> int:
    return int(_dt.datetime.fromisoformat(v).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def run(run_id: str, rule, date_from: str, date_to: str, *, gate: bool = False,
        config_path: str | None = None, stride: int = 1, require_all: bool = True,
        trigger: bool = False, veto: bool = False, symbols: list[str] | None = None,
        strategy=None, no_breakeven: bool = False, exclude: list[str] | None = None,
        cache_dir: str | None = None, candidate_order=None,
        risk_pct: float | None = None, equity: float | None = None) -> dict:
    # SIRA ONEMLI (2026-09-19): arsiv koku ONCE ayarlanir, evren SONRA cozulur.
    # `universe.resolve()` hangi sembollerin arsivde OLDUGUNU okur; cache_dir sonra
    # ayarlanirsa evren ESKI arsive gore cozulur ve yeni arsivdeki coinler sessizce
    # dusurulur. Bu, V14'te bir kosunun yanlis arsivi okumasina yol acan tuzagin aynisi.
    if cache_dir:                                   # V14: AYRI arsiv (varsayilan: modul basindaki DATA = wt-ten)
        os.environ["TRADINGBOT_CACHE_DIR"] = str(cache_dir)
    # EVREN: sabit liste YOK — canli config'ten CAGRI ANINDA cozulur (universe.py).
    from universe import resolve as _resolve_universe
    syms = list(symbols) if symbols else _resolve_universe()[0]
    from tradingbot.config import load_config
    from tradingbot.history import HistoryStore
    from tradingbot.replay import HistoricalReplay, walk_forward_windows
    from tradingbot.replay.research import resolve_replay_dir

    cfg = load_config(config_path or str(WT / "config.yaml"))
    if no_breakeven:
        cfg.v3.futures_v3.breakeven_at_mfe_r = 0.0      # T1/T2: saf trend, basa-bas korumasi KAPALI
    if risk_pct is not None:
        # SLOT SAYISI DENEYI (2026-09-20): eszamanli pozisyon sayisini belirleyen sey
        # `max_total_open_risk_pct / risk_per_trade_pct` oranidir. TOPLAM riski SABIT
        # tutup islem basina riski dusurmek, portfoy maruziyetini ARTIRMADAN slot sayisini
        # cogaltir — bu bir risk gevsetmesi degil, cesitlendirmedir. Olculdu: kar her kolda
        # en iyi 3 isleme yogun (net'in %100'unden fazlasi), yani az slotla kuyruk yakalamak
        # sansa kaliyor.
        cfg.v3.risk_profiles.overrides = dict(cfg.v3.risk_profiles.overrides or {})
        cfg.v3.risk_profiles.overrides["risk_per_trade_pct"] = float(risk_pct)
    if equity is not None:
        # BAKIYE: `MIN_NOTIONAL` / lot adimi gibi BORSA MINIMUMLARI mutlak USDT cinsindendir.
        # 100 USDT'de islem basina %2 risk = 2 USDT; slot sayisi arttikca pozisyon kuculur ve
        # minimumlara takilir (olculdu: 12 slotta MIN_ORDER_CONFLICT 14906, acilan 36 -> 17).
        # Bakiyeyi buyutmek bu kisiti gevsetir; kaldirac gevsetMEZ (o MAX_POSITION_PCT'yi acar).
        cfg.futures.starting_equity_usdt = float(equity)
    # KALDIRAC BURADA AYARLANMAZ: bu kosucu kurali DISARIDAN alir (`strategy=`), yani
    # `cfg.v3.strategy_paper.rule_params` yoluna HIC bakilmaz. Kaldirac kural kurulurken
    # verilir: `strategy_rules.build(ad, leverage=N)`. Config'e yazmak sessizce etkisiz
    # kalirdi — olculen bir sey degismeden "kaldirac 3'te olctuk" denirdi.
    store = HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)
    rdir = resolve_replay_dir(cfg.state_path, run_id, None)
    rp = HistoricalReplay(cfg, run_id=run_id, store=store, symbols=syms, market="futures",
                          tf="4h", seed=0, state_root=rdir.parent, pattern_engine=None,
                          start_ms=_ms(date_from), end_ms=_ms(date_to), decision_stride=stride,
                          economics_gate=gate, spot_listed=set(syms), entry_rule=rule,
                          entry_trigger=trigger, legacy_veto=veto, strategy=strategy,
                          candidate_order=candidate_order)
    # FAIL-CLOSED EVREN (2026-09-19): replay yeterli serisi olmayan sembolu SESSIZCE atliyordu ve
    # meta ISTENEN listeyi yaziyordu; 40 coinlik bir kosu 10 coinlik arsivde rc=0 ile bitip
    # "40 sembol" raporluyordu. Eksik sembolle kosmak AYRI bir deneydir: acikca istenmedikce hata.
    rp.load(require_all=require_all)
    if rule is not None and hasattr(rule, "attach"):
        rule.attach(rp)          # kural replay'in kendi kapanmis-bar dilimine erisir (BTC rejimi)
    ws = walk_forward_windows(rp.result.start_ms, rp.result.end_ms, train_days=180,
                              test_days=30, purge_bars=6, embargo_bars=6, tf="4h")
    t0 = time.time()
    res = rp.run(windows=ws)
    # EVREN PROVENANSI: ISTENEN degil, GERCEKTEN YUKLENEN evren raporlanir; dusenler sebebiyle yazilir.
    meta = {"run_id": run_id, "from": date_from, "to": date_to, "gate": bool(gate),
            "symbols_requested": syms, "symbols_loaded": sorted(res.loaded_symbols),
            "symbols_skipped": dict(res.skipped_symbols),
            "universe_complete": not res.skipped_symbols,
            "n_symbols_loaded": len(res.loaded_symbols),
            "strategy": bool(strategy), "no_breakeven": bool(no_breakeven), "exclude": list(exclude or []),
            "history_root": str(store.root),          # provenans: hangi arsiv okundu
            "trigger": bool(trigger), "veto": bool(veto),
            "candidate_order": bool(candidate_order), "risk_pct": risk_pct,
            "equity": equity,
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
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="evren (varsayilan: canli config entry_universe.symbols — universe.py ile TURETILIR)")
    ap.add_argument("--allow-partial-universe", dest="allow_partial", action="store_true",
                    help="arsivde eksik sembol VARSA yine de kos. Varsayilan KAPALI: eksik evrenle kosmak "
                         "ayri bir deneydir ve 'N coinde olctuk' hukmu kurulamaz. Meta her durumda "
                         "symbols_loaded/symbols_skipped yazar.")
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
                         strategy=strat, no_breakeven=a.no_breakeven, exclude=a.exclude, cache_dir=a.cache_dir,
                         require_all=not a.allow_partial),
                     ensure_ascii=False, default=str))
