# -*- coding: utf-8 -*-
"""ÇOK TURLU SENARYO KOŞUCUSU (2026-10-03) — `test_learning_record_only_tours.py` bunu ALT SÜREÇTE çalıştırır.

Neden alt süreç: aynı senaryo hem bu ağaçla hem `git archive 943345c` ile çıkarılmış TABAN ağaçla koşulur; iki `tradingbot`
paketi aynı süreçte yan yana içe aktarılamaz. Koşucu `--tree`in `tradingbot/` ve `tests/` klasörlerini `sys.path`in BAŞINA
koyar (ağacın kendi test düzeneği: `test_engine_v3._engine`, `test_strategy_paper_engine_v1._install`, ortak deneyim altın
testinin `_overrides`/`_DetOS`/`T0`), saati dondurur, kimlikleri deterministik yapar, ağı kapatır (conftest'teki koruma) ve
GERÇEK `TradingEngineV3.tour`u beş tur koşar: ana bot, T2/M2/D4 kâğıt defterleri, Formasyon defteri, öğrenme modu AÇIK, ana
bot ve T2/M2'de yapı girişi gölgede (config.yaml gibi), ortak deneyim katmanı RECORD (`--xp`; xp satırları, imleç ve danışman
dosyaları da karşılaştırılır); tur 0 kill switch, tur 1 sıfırlama, tur 3 fiyat oynaması. İki senaryo:
`books` (BTC yükselişte: T2/M2 politika ve TOTAL_OPEN_RISK kapasite-ekstra işlemleri) ve `main_gates` (BTC düşüşte, rejim ve
mum kapıları ENFORCE → öğrenmede gölgede: ana botta REGIME_VETO seçicilik-ekstra). Çıktı: `state/` altındaki her dosyanın
sha256'sı (koşu kökü `<ROOT>` ile değiştirilmiş; `health.json` süreç belleği ve katman süresi ölçümleri, xp `status.json` süre
özetleri ve `heartbeat.json` pid'i düşülmüş) ve karar özeti (defter başına pozisyon/işlem etiketleri, karşı-olgusal
nedenleri). pytest bu dosyayı toplamaz (adı `test_` ile başlamaz).
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
from datetime import timedelta
from pathlib import Path


def _facts(eng) -> dict:
    def tags_of(x) -> list | None:
        lr = (getattr(x, "meta", None) or {}).get("learning") or (getattr(x, "features", None) or {}).get("learning")
        return list(lr.get("learning_unlocked_by") or []) if isinstance(lr, dict) else None

    out = {}
    books = [("main", eng.ledger2, [t for t in eng.shadow.trades if t.book == "main"])]
    for b in eng.strategy_books:
        books.append((b.key, b.ledger, list(b.cf.sb.trades) if b.cf is not None else []))
    pb = getattr(eng, "pattern_book", None)
    if pb is not None:
        books.append((pb.key, pb.ledger, list(pb.cf.sb.trades) if pb.cf is not None else []))
    for key, led, cfs in books:
        out[key] = {"open": sorted([p.symbol, tags_of(p)] for p in led.positions.values()),
                    "closed": sorted([r.symbol, tags_of(r)] for r in led.history),
                    "cf": sorted([t.symbol, list(t.reason_not_opened or [])] for t in cfs)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True, help="kod ağacı kökü (tradingbot/ ve tests/ içerir)")
    ap.add_argument("--root", required=True, help="koşu dizini (silinip yeniden kurulur)")
    ap.add_argument("--extra", default="absent", help="learning_mode.extra_entries: absent | open | record_selectivity")
    ap.add_argument("--tours", type=int, default=5)
    ap.add_argument("--scenario", default="books", choices=("books", "main_gates"),
                    help="books: BTC yükselişte (T2/M2 politika + kapasite-ekstra); main_gates: BTC düşüşte, rejim ve mum "
                         "kapıları ENFORCE → öğrenmede gölgede, ana botta REGIME_VETO seçicilik-ekstra")
    ap.add_argument("--xp", default="RECORD", choices=("OFF", "RECORD"),
                    help="ortak deneyim katmanı (RECORD: xp satırları ve danışman meta dosyaları da karşılaştırılır)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    tree = Path(a.tree).resolve()
    sys.path[:0] = [str(tree), str(tree / "tests")]
    os.chdir(tree)
    import pandas as pd
    import pytest
    import time_machine

    import test_engine_v3 as E
    import test_shared_experience_no_decision_change_v1 as G
    import test_strategy_paper_engine_v1 as SP
    import tradingbot
    import tradingbot.core.ids as ids_mod
    from tradingbot import engine as _engine_mod
    from tradingbot.market import http as _http
    assert Path(tradingbot.__file__).resolve().parent == tree / "tradingbot", tradingbot.__file__

    root = Path(a.root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    mp = pytest.MonkeyPatch()
    # ağ koruması (tests/conftest.py `_offline_network` ile aynı)
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
    mp.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
    mp.setattr("tradingbot.box_timer.BoxTimer.start", lambda self: None)
    ov = copy.deepcopy(G._overrides(lm=True, xp={"enabled": a.xp == "RECORD", "mode": a.xp}))
    ov["learning_mode"]["strategy_overrides"]["structures_entry_shadow"] = ["main", "t2_trend_regime", "m2_tsmom28"]
    gates = a.scenario == "main_gates"
    if gates:
        ov["entry_selectivity"] = {"regime_gate_mode": "ENFORCE", "candle_confirmation_mode": "ENFORCE"}
    if a.extra != "absent":
        ov["learning_mode"]["extra_entries"] = a.extra
    try:
        with time_machine.travel(G.T0, tick=False) as clock:
            eng = E._engine(root, mp, ov, symbols=4, equity=200.0, p_win=0.64)
            for fr in eng._fake_live._frames.values():
                for tf, ms in (("4h", 14_400_000), ("1h", 3_600_000)):
                    fr[tf]["timestamp"] = fr[tf]["timestamp"] + ms
                    fr[tf].index = fr[tf].index + pd.Timedelta(milliseconds=ms)
            SP._install(eng, mp, btc_up=not gates, coin_above=True)
            px = {s: float(fr["4h"]["close"].iloc[-1]) for s, fr in eng._fake_live._frames.items()}
            for i in range(int(a.tours)):
                if i == 0:
                    eng.killswitch.trip("MANUAL", "golden")           # adaylar bloklanır → karşı-olgusal
                if i == 1:
                    eng.killswitch.reset("golden", "reset")           # aynı sinyaller açılır → SUPERSEDED
                if i == 3:                                            # fiyat oynar: stop/hedef → kapanışlar
                    for k, s in enumerate(sorted(px)):
                        eng._fake_live.price[s] = px[s] * (0.85 if k % 2 == 0 else 1.2)
                eng._fake_live._now_s = __import__("time").time()
                eng.tour(do_scan=False, obsidian=False, charts=False)
                clock.shift(timedelta(minutes=10))
    finally:
        mp.undo()
    files = {}
    rb = str(root).encode("utf-8")
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        data = p.read_bytes().replace(rb, b"<ROOT>")
        if rel == "state/health.json":                       # süreç RSS'i ölçümdür, karar değil (altın testteki gibi)
            h = json.loads(data)
            mem = (h.get("learning_mode") or {}).get("memory")
            if isinstance(mem, dict):
                for k in ("rss_mb", "hwm_mb"):
                    mem.pop(k, None)
            if isinstance(h.get("shared_experience"), dict):  # katman adımının süresi ölçümdür (ms), karar değil
                h["shared_experience"].pop("step_ms", None)
            data = json.dumps(h, sort_keys=True).encode("utf-8")
        elif rel == "state/shared_experience/status.json":   # aynı süre ölçümünün özetleri
            h = json.loads(data)
            for k in ("step_ms_last", "step_ms_p50", "step_ms_p95"):
                h.pop(k, None)
            data = json.dumps(h, sort_keys=True).encode("utf-8")
        elif rel == "state/heartbeat.json":                  # süreç kimliği (pid) alt süreçten alt sürece değişir
            h = json.loads(data)
            h.pop("pid", None)
            data = json.dumps(h, sort_keys=True).encode("utf-8")
        files[rel] = hashlib.sha256(data).hexdigest()
    facts = _facts(eng)
    xp_rows = root / "state" / "shared_experience" / "experience.jsonl"
    if xp_rows.exists():                                     # xp satırları: tür sayıları + karşı-olgusal satırlarının nedenleri
        rows = [json.loads(x) for x in xp_rows.read_text(encoding="utf-8").splitlines() if x.strip()]
        kinds: dict = {}
        for r in rows:
            kinds[str(r.get("kind"))] = kinds.get(str(r.get("kind")), 0) + 1
        facts["xp"] = {"kinds": kinds, "cf": sorted([str(r.get("book")), str(r.get("symbol")), list(r.get("reasons") or [])]
                                                    for r in rows if r.get("kind") == "xp_cf")}
    Path(a.out).write_text(json.dumps({"files": files, "facts": facts}, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
