# -*- coding: utf-8 -*-
"""DOĞRUDAN GİRİŞ YOLU KOŞUCUSU (2026-10-03) — `test_learning_record_only_tours.py` bunu ALT SÜREÇTE çalıştırır.

Çok turlu koşucunun (`record_only_tour_runner.py`) kapsamadığı yolları — strateji defterleri (D4, T2, Box) ve Formasyon —
ve ana botun seçicilik/kapasite/politika adaylarını, ağacın KENDİ test düzeneğiyle (`test_learning_mode_books`,
`test_learning_mode_main`, `test_learning_mode_pattern`; bu dalda değişmediler) doğrudan koşar. Aynı senaryo hem bu ağaçla hem
`git archive 943345c` ile çıkarılan TABAN ağaçla koşulur: `--extra absent|open` iken her dosya bayt bayt aynı olmalıdır.
`--extra` `absent` değilse defter görünümüne (`BookLearning.extra_entries`) ya da config'e (`learning_mode.extra_entries`)
yazılır; taban ağaçta yalnız `absent` geçerlidir. Saat donuk, kimlikler deterministik, ağ kapalı.

Senaryolar: D4 evren dışı (BOOK_UNIVERSE) ve 12 sembol kapasite; T2 12 sembol kapasite ve yapı girişi gölgesi (iki tur); D4 kill
switch → aynı sinyal sonraki turlarda (karşı-olgusalın yerine geçme / yalnız-kayda dönüşme); Box %0,4 / %0,6 stop, taban
0,32 / 0,5; Formasyon R/R tabanı, politika, tick yuvarlaması (RISK_ABOVE_CAP_AFTER_ROUNDING) ve tarama (MAX_POSITIONS); ana bot
NEGATIVE_NET_EDGE, RESEARCH_SIZE_ONLY + TOTAL_OPEN_RISK, TOTAL_OPEN_RISK, politika ve kill switch → aynı sinyal.
Çıktı: senaryo klasörlerindeki her dosyanın sha256'sı (koşu kökü `<ROOT>` ile değiştirilmiş) ve karar özeti. pytest bu
dosyayı toplamaz (adı `test_` ile başlamaz).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
import shutil
import sys
from pathlib import Path


def _tags(x) -> list | None:
    lr = (getattr(x, "meta", None) or {}).get("learning") or (getattr(x, "features", None) or {}).get("learning")
    return list(lr.get("learning_unlocked_by") or []) if isinstance(lr, dict) else None


def _book_facts(book) -> dict:
    cf = getattr(book, "cf", None)
    return {"open": sorted([p.symbol, _tags(p)] for p in book.ledger.positions.values()),
            "closed": sorted([r.symbol, _tags(r)] for r in book.ledger.history),
            "cf": sorted([t.symbol, list(t.reason_not_opened or [])] for t in (cf.sb.trades if cf is not None else [])),
            "rejections": dict(sorted(getattr(book, "rejections", {}).items()))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True, help="kod ağacı kökü (tradingbot/ ve tests/ içerir)")
    ap.add_argument("--root", required=True, help="koşu dizini (silinip yeniden kurulur)")
    ap.add_argument("--extra", default="absent", help="learning_mode.extra_entries: absent | open | record_selectivity")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tree = Path(a.tree).resolve()
    sys.path[:0] = [str(tree), str(tree / "tests")]
    os.chdir(tree)
    import pytest
    import time_machine

    import test_learning_mode_books as LB
    import test_learning_mode_main as LMM
    import test_learning_mode_pattern as LP
    import test_shared_experience_no_decision_change_v1 as G
    import tradingbot
    import tradingbot.core.ids as ids_mod
    from tradingbot import engine as _engine_mod
    from tradingbot.core import utc_now
    from tradingbot.market import http as _http
    assert Path(tradingbot.__file__).resolve().parent == tree / "tradingbot", tradingbot.__file__

    root = Path(a.root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    kw = {} if a.extra == "absent" else {"extra_entries": a.extra}
    learn_pt = copy.deepcopy(LP.LEARN)
    learn_pt.update(kw)
    mp = pytest.MonkeyPatch()
    real_session = _http.HttpClient.session

    def _session(self):
        if self._session is None:
            raise _http.TransientHttpError(f"test: ağ yok ({self.base_url})")
        return real_session.fget(self)

    def _offline_fut(self):
        if self._fu is None:
            raise ConnectionError("test: ağ yok (ccxt binanceusdm)")
        return self._fu
    mp.setattr(_http.HttpClient, "session", property(_session))
    mp.setattr(_engine_mod.TradingEngine, "_fut", _offline_fut)
    mp.setattr(ids_mod, "os", G._DetOS())
    random.seed(7)
    facts: dict = {}

    def d(name: str) -> Path:
        p = root / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    try:
        with time_machine.travel(G.T0, tick=False):
            # ---------------------------------------------------------------- strateji defterleri
            syms2 = [LB.SYM, "SOL/USDT"]
            book = LB._book(d("d4_universe"), "d4_donchian_20_10", symbols=[LB.SYM])
            LB._step(book, LB._d4_fbs(tuple(syms2)), now_ms=LB.NOW_H4, px=104.1, symbols=syms2,
                     learning=LB._bl("d4_donchian_20_10", **kw))
            facts["d4_universe"] = _book_facts(book)
            syms12 = ["S%02d/USDT" % i for i in range(12)]
            for name in ("d4_donchian_20_10", "t2_trend_regime"):
                if name == "t2_trend_regime":
                    _flag, brk = LB._trend_rows()
                    fbs, now_ms, px = LB._trend_fbs(brk, syms12), LB._asof(brk), float(brk[-1]["close"])
                else:
                    fbs, now_ms, px = LB._d4_fbs(syms12), LB.NOW_H4, 104.1
                book = LB._book(d(name + "_capacity"), name, symbols=(syms12 if name == "d4_donchian_20_10" else None))
                LB._step(book, fbs, now_ms=now_ms, px=px, symbols=syms12, learning=LB._bl(name, **kw))
                facts[name + "_capacity"] = _book_facts(book)
            flag, _brk = LB._trend_rows()
            book = LB._book(d("t2_structure"), "t2_trend_regime", mode="ENFORCE")
            for k in range(2):
                book.run_id = "RUN-%d" % k
                LB._step(book, LB._trend_fbs(flag), now_ms=LB._asof(flag) + k * 900_000, px=float(flag[-1]["close"]),
                         learning=LB._bl("t2_trend_regime", **kw), structures_entry_shadow=True)
            facts["t2_structure"] = _book_facts(book)
            lrn = LB._bl("d4_donchian_20_10", **kw)
            book = LB._book(d("d4_killswitch"), "d4_donchian_20_10", symbols=[LB.SYM])
            fbs = LB._d4_fbs(tuple(syms2))
            book.risk.ks.trip("TEST", "manual trip")
            LB._step(book, fbs, now_ms=LB.NOW_H4, px=104.1, symbols=syms2, learning=lrn)
            book.risk.ks.reset("test", "reset")
            for k in (1, 2):
                LB._step(book, fbs, now_ms=LB.NOW_H4 + k * 15 * 60_000, px=104.1, symbols=syms2, learning=lrn)
            facts["d4_killswitch"] = _book_facts(book)
            for pct in (0.4, 0.6):
                hi = round(108.0 * (1 + pct / 100.0), 6)
                bars = [(100, 101, 99, 100), (100, 101, 99, 100), (108.1, hi, 108.05, 108.3), (108.3, 108.35, 107.9, 108.0)]
                for floor in (0.32, 0.5):
                    name = "box_%s_floor_%s" % (pct, floor)
                    book = LB._book(d(name), "b1_box_fade", mode="SHADOW")
                    LB._step(book, LB._box_fbs(bars), now_ms=LB.NOW_M5, px=108.0,
                             learning=LB._bl("b1_box_fade", min_stop_pct=floor, **kw))
                    facts[name] = _book_facts(book)
            # ---------------------------------------------------------------- Formasyon
            for name, plan, bl_over, ue in (("pt_rr_floor", LP._plan(min_rr=5.0), {}, None),
                                            ("pt_policy", LP._plan(), {}, None),
                                            ("pt_rounding", LP._plan(), {"slots": 1}, LP._ue(tick="0.5"))):
                _cfg, book = LP._direct(d(name), cfg_learning=learn_pt, bl_over=bl_over)
                res = LP._open(book, plan, ue=ue) if ue is not None else LP._open(book, plan)
                book.save({"BTC/USDT": 100.0}, LP._dt(LP.NOW))                 # defter/plan/özet dosyaları da karşılaştırılır
                facts[name] = dict(_book_facts(book), result=res, plan_status=plan.get("status"),
                                   counters=dict(sorted(book.learning_counters.items())))
            LP.P.CLOCK[0] = LP.P.T0
            try:
                sc, book, _prov, _ = LP._multi(d("pt_scan"), learning=learn_pt)
                book.set_learning(LP._bl(book.cfg), universe_symbols=[], gate=lambda: (True, "OK"))
                sc.scan_cycle(now_ms=LP.P.CLOCK[0])
                facts["pt_scan"] = _book_facts(book)
            finally:
                LP.P.CLOCK[0] = LP.P.T0
            # ---------------------------------------------------------------- ana bot
            now = utc_now().replace(microsecond=0)
            lm = LMM._lm()
            lm["learning_mode"].update(kw)
            for name, opp, prefill, ks in (("main_neg", LMM.NEG, False, False), ("main_res_capacity", LMM.RES, True, False),
                                           ("main_capacity", None, True, False), ("main_policy", None, False, False),
                                           ("main_killswitch", LMM.NEG, False, True)):
                eng = LMM._eng(d(name), mp, copy.deepcopy(lm))
                if prefill:
                    LMM._prefill(eng, notional=40.0, lev=2, stop_pct=5.0, n=3, now=now)
                decisions, chief, briefs, marks = LMM._cands(eng, LMM.SYMS[:2], opp=opp)
                logs = []
                if ks:
                    eng.killswitch.trip("MANUAL", "test")
                    logs.append(eng._execute(decisions, chief, briefs, None, marks, now)[1])
                    eng.killswitch.reset("test", "reset")
                    eng.run_id = "run_2"
                opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
                logs.append(risk_log)
                (d(name) / "risk_log.json").write_text(json.dumps(logs, sort_keys=True, default=str), encoding="utf-8")
                facts[name] = {"opened": len(opened),
                               "open": sorted([p.symbol, _tags(p)] for p in eng.ledger2.positions.values()),
                               "cf": sorted([t.symbol, list(t.reason_not_opened or [])] for t in LMM._main_cfs(eng)),
                               "block_codes": [[e.get("symbol"), e.get("block_code")] for e in risk_log]}
    finally:
        mp.undo()
    files = {}
    rb = str(root).encode("utf-8")
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes().replace(rb, b"<ROOT>")).hexdigest()
    Path(a.out).write_text(json.dumps({"files": files, "facts": facts}, sort_keys=True, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
