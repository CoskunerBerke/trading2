# -*- coding: utf-8 -*-
"""BEŞ BOT — ORTAK YAPI → GERÇEK PAPER KARAR ZİNCİRİ KANITI.

ETİKET: VERİ SENTETİKTİR (mum-nötr zemin + etiketli yapı parçaları, `tests/structure_fixtures.py`); karar, risk, defter,
çıkış ve kayıt kodu ÜRETİM kodudur (ana bot: `TradingEngineV3.tour`; T2/M2/Box: `StrategyBook.step` →
`paper_rules.decide_with_structures` → `strategy_paper.apply_action`; Formasyon: `PatternScanner.scan_cycle` →
`PatternBook` → `build_plans_v2` → `_follow_record` → `_try_open` → `apply_action`). Her satır bir işlem kimliği ya da
gerekçeli iptal/bekleme kararı (karar deposundaki `decision_id`) taşır. Kârlılık iddiası YOKTUR.

Çalıştırma (depo kökünden):  python docs/review/evidence-2026-09-22-shared/per_bot_chain_evidence.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

WT = Path(os.environ.get("EVIDENCE_WT") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(WT))
sys.path.insert(0, str(WT / "tests"))
OUT = Path(os.environ.get("EVIDENCE_OUT") or Path(__file__).resolve().parent)

import pytest  # noqa: E402

from tradingbot.structures.store import StructureStore  # noqa: E402


def _row(state_path, key):
    r = StructureStore(state_path).latest_decisions().get(key) or {}
    return {k: r.get(k) for k in ("decision_id", "bot", "action", "reason_code", "trade_id", "applied", "mode", "policy_version", "text_tr")}


def main_bot(tmp: Path) -> list[dict]:
    from test_risk_capacity_and_gates import _force_triggers, _risk_log
    from test_structures_main_bot_v1 import BEAR, BULL, NEUTRAL, SYMS, _eng, _install, _tour

    from tradingbot.core import from_iso, iso
    out = []
    mp = pytest.MonkeyPatch()
    try:
        eng = _eng(tmp / "main", mp, "ENFORCE")
        _install(eng, mp, {SYMS[0]: NEUTRAL, SYMS[1]: BULL})
        _force_triggers(mp, True)
        _tour(eng)
        recs = {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS}
        pos = eng.ledger2.positions[SYMS[1]]
        st = pos.features["structure"]
        out.append({"bot": "main", "scenario": "geri çekilme planı + teyitli uyumlu 4h yutan boğa", "result": "OPENED",
                    "trade_id": pos.id, "symbol": SYMS[1], "side": pos.side.value, "pattern_id": st["pattern_id"], "structure": st["name"],
                    "tf": st["timeframe"], "status": st["status"], "trigger_text_has_structure": "yapı:" in pos.trigger_text,
                    "decision": _row(eng.cfg.state_path, "main|USDM_PERP|%s" % SYMS[1])})
        out.append({"bot": "main", "scenario": "geri çekilme planı, yapı yok", "result": "WAIT (giriş yok)", "symbol": SYMS[0],
                    "block_code": recs[SYMS[0]]["block_code"], "decision": _row(eng.cfg.state_path, "main|USDM_PERP|%s" % SYMS[0])})
        # yönetim: girişten SONRA teyitli karşı yapı → stop sıkılaştırma (aynı turun eski bar uçları kullanılmaz)
        stop0 = pos.stop
        pos.opened_at = iso(from_iso(iso()) - timedelta(days=5))
        for f in pos.fills:
            f.ts = pos.opened_at
        _install(eng, mp, {SYMS[0]: NEUTRAL, SYMS[1]: BEAR})
        _force_triggers(mp, False)
        _tour(eng)
        pos = eng.ledger2.positions[SYMS[1]]
        out.append({"bot": "main", "scenario": "açık LONG, girişten sonra teyitli 4h yutan ayı", "result": "TIGHTEN_STOP",
                    "trade_id": pos.id, "stop_from": str(stop0), "stop_to": str(pos.stop), "meta": pos.meta.get("structure_stop"),
                    "decision": _row(eng.cfg.state_path, "main|USDM_PERP|%s" % SYMS[1])})
        eng2 = _eng(tmp / "main_opp", mp, "ENFORCE")
        _install(eng2, mp, {SYMS[0]: BEAR, SYMS[1]: BULL})
        _force_triggers(mp, True)
        _tour(eng2)
        recs2 = {e["symbol"]: e for e in _risk_log(eng2) if e.get("symbol") in SYMS}
        out.append({"bot": "main", "scenario": "LONG adayı, teyitli karşı (ayı) yapı", "result": "WAIT (giriş yok)", "symbol": SYMS[0],
                    "block_code": recs2[SYMS[0]]["block_code"], "decision": _row(eng2.cfg.state_path, "main|USDM_PERP|%s" % SYMS[0])})
    finally:
        mp.undo()
    return out


def strategy_books(tmp: Path) -> list[dict]:
    import test_structures_strategy_books_v1 as S
    from structure_fixtures import swing_high_then_sweep
    out = []
    flag, brk = S._daily_flag()
    btc = S._btc(len(brk))
    enf = S._book(S._cfg(tmp / "t2", "ENFORCE"), "t2_trend_regime")
    off = S._book(S._cfg(tmp / "t2off", "OFF"), "t2_trend_regime")
    S._step(enf, {"1d": flag}, as_of_ms=S._asof(flag), price=flag[-1]["close"], btc=btc[:len(flag)])
    S._step(off, {"1d": flag}, as_of_ms=S._asof(flag), price=flag[-1]["close"], btc=btc[:len(flag)])
    out.append({"bot": "t2_trend_regime", "scenario": "kural OPEN, 1d bayrak OLUŞUYOR", "result": "WAIT_TRIGGER (giriş yok)",
                "decision": _row(enf.cfg.state_path, "strategy_paper|USDM_PERP|%s" % S.SYM),
                "control_off_trade_id": off.ledger.positions[S.SYM].id})
    S._step(enf, {"1d": brk}, as_of_ms=S._asof(brk), price=brk[-1]["close"], btc=btc)
    p = enf.ledger.positions[S.SYM]
    out.append({"bot": "t2_trend_regime", "scenario": "kırılış kapanışı: teyitli bayrak", "result": "OPENED", "trade_id": p.id,
                "pattern_id": p.features["structure"]["pattern_id"], "structure": p.features["structure"]["name"],
                "decision": _row(enf.cfg.state_path, "strategy_paper|USDM_PERP|%s" % S.SYM)})
    after = swing_high_then_sweep(brk, step=S.DAY)
    btc2 = S._btc(len(after))
    m2 = S._book(S._cfg(tmp / "m2", "ENFORCE"), "m2_tsmom28")
    S._step(m2, {"1d": brk}, as_of_ms=S._asof(brk), price=brk[-1]["close"], btc=btc2[:len(brk)])
    tid = m2.ledger.positions[S.SYM].id
    S._step(m2, {"1d": after}, as_of_ms=S._asof(after), price=after[-1]["close"], btc=btc2)
    h = m2.ledger.history[-1]
    out.append({"bot": "m2_tsmom28", "scenario": "bayrak teyidiyle giriş, sonra teyitli süpürme (başarısız kırılım)",
                "result": "OPENED → CLOSED (%s)" % h.exit_reason, "trade_id": tid, "closed_trade_id": h.id,
                "entry_pattern_id": h.features["structure"]["pattern_id"], "exit_structure": (h.features.get("exit_structure") or {}).get("name"),
                "decision": _row(m2.cfg.state_path, "strategy_paper_m2|USDM_PERP|%s" % S.SYM)})
    day0 = S.T0 + 300 * S.DAY
    daily, m5 = S._box_frames("sweep", day0)
    box = S._book(S._cfg(tmp / "box", "ENFORCE"), "b1_box_fade")
    S._step(box, {"1d": daily, "5m": m5}, as_of_ms=S._asof(m5, S.M5), price=m5[-1]["close"])
    bp = box.ledger.positions[S.SYM]
    out.append({"bot": "b1_box_fade", "scenario": "kutu tepesinde teyitli taşma-geri dönüş", "result": "OPENED %s" % bp.side.value,
                "trade_id": bp.id, "pattern_id": bp.features["structure"]["pattern_id"], "structure": bp.features["structure"]["name"],
                "decision": _row(box.cfg.state_path, "strategy_paper_box|USDM_PERP|%s" % S.SYM)})
    t = m5[-1]["timestamp"]
    more = list(m5) + [{"timestamp": t + S.M5, "open": 104.7, "high": 105.7, "low": 104.6, "close": 105.4, "volume": 1.0},
                       {"timestamp": t + 2 * S.M5, "open": 105.4, "high": 106.0, "low": 105.2, "close": 105.8, "volume": 1.0}]
    S._step(box, {"1d": daily, "5m": more}, as_of_ms=S._asof(more, S.M5), price=more[-1]["close"])
    out.append({"bot": "b1_box_fade", "scenario": "açık fade'e karşı teyitli dış kırılım", "result": "CLOSED (%s)" % box.ledger.history[-1].exit_reason,
                "trade_id": box.ledger.history[-1].id, "decision": _row(box.cfg.state_path, "strategy_paper_box|USDM_PERP|%s" % S.SYM)})
    daily2, m5b = S._box_frames("breakout", day0)
    box2 = S._book(S._cfg(tmp / "box2", "ENFORCE"), "b1_box_fade")
    S._step(box2, {"1d": daily2, "5m": m5b}, as_of_ms=S._asof(m5b, S.M5), price=m5b[-1]["close"])
    out.append({"bot": "b1_box_fade", "scenario": "teyitli dış kırılım, karşı fade planı", "result": "CANCEL (giriş yok)",
                "decision": _row(box2.cfg.state_path, "strategy_paper_box|USDM_PERP|%s" % S.SYM)})
    return out


def pattern_bot(tmp: Path) -> list[dict]:
    import test_structures_pattern_bot_v2 as P
    from test_pattern_trader_v1 import CLOCK, M15, _exinfo, _provider, _scanner, _set_mark
    out = []
    flag, brk = P._flag15()
    pr = P._provider({P.SYM: P._frames(brk)}, _exinfo(P.EX))
    cfg = P._enforce_cfg(tmp / "pt")
    sc, book = _scanner(cfg, pr)
    CLOCK[0] = int(flag[-1]["timestamp"]) + M15
    _set_mark(pr, P.SYM, flag[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    pl = next(x for x in book.plans.values() if x["family"] == "D2_CHART_STRUCTURE")
    out.append({"bot": "pattern_trader", "scenario": "15m bayrak OLUŞUYOR", "result": "PLAN %s (emir yok)" % pl["status"],
                "plan_id": pl["plan_id"], "pattern_id": pl["pattern_id"], "trigger_text": pl["trigger"]["text_tr"],
                "decision": _row(cfg.state_path, "pattern_trader|USDM_PERP|%s" % P.SYM)})
    CLOCK[0] = int(brk[-1]["timestamp"]) + M15
    _set_mark(pr, P.SYM, brk[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    pos = book.ledger.positions[P.SYM]
    out.append({"bot": "pattern_trader", "scenario": "kırılış kapanışı: kayıt TEYİT → plan tetik → risk → giriş", "result": "OPENED",
                "plan_id": pl["plan_id"], "trade_id": pos.id, "triggered_at_ms": pl["triggered_at_ms"], "entry": float(pos.entry_avg),
                "decision": _row(cfg.state_path, "pattern_trader|USDM_PERP|%s" % P.SYM)})
    CLOCK[0] += 10 * M15
    _set_mark(pr, P.SYM, float(pl["target"]) * 1.001)
    sc.exit_check()
    h = book.ledger.history_dicts()[-1]
    out.append({"bot": "pattern_trader", "scenario": "hedefte kapanış", "result": "CLOSED (%s)" % h.get("exit_reason"),
                "trade_id": h.get("id"), "net_pnl_after_cost": h.get("net_pnl")})
    # kovalama sınırı: süpürme çok içeride kapandı → gerekçeli iptal
    rows = P._sweep15()
    last, p0 = rows[-1], rows[-8]["close"]
    rows[-1] = P._bar(last["timestamp"], last["open"], last["high"], p0 * 1.020, p0 * 1.030)
    pr2 = P._provider({P.SYM: P._frames(rows)}, _exinfo(P.EX))
    cfg2 = P._enforce_cfg(tmp / "pt2")
    sc2, book2 = _scanner(cfg2, pr2)
    CLOCK[0] = int(rows[-1]["timestamp"]) + M15
    _set_mark(pr2, P.SYM, rows[-1]["close"])
    sc2.scan_cycle(now_ms=CLOCK[0])
    e2 = next(x for x in book2.plans.values() if x["family"] == "E2_SWEEP_RECLAIM")
    out.append({"bot": "pattern_trader", "scenario": "teyitli süpürme, fiyat tetikten >1 ATR uzak", "result": "%s %s" % (e2["status"], e2["reasons"][-1]),
                "plan_id": e2["plan_id"], "cancel_detail": e2.get("cancel_detail")})
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="per_bot_evidence_"))
    import subprocess
    sha = subprocess.run(["git", "-C", str(WT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    doc = {"label": "SENTETİK VERİ (etiketli); karar/risk/defter/kayıt kodu üretim kodu; kârlılık iddiası yok",
           "code_sha": sha, "rows": []}
    for fn in (main_bot, strategy_books, pattern_bot):
        doc["rows"] += fn(tmp)
    (OUT / "per_bot_chain_evidence.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for r in doc["rows"]:
        print("%-16s | %-58s | %-34s | %s" % (r["bot"], r["scenario"][:58], r["result"][:34],
                                              r.get("trade_id") or r.get("plan_id") or (r.get("decision") or {}).get("decision_id")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
