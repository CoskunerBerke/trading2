# -*- coding: utf-8 -*-
"""FORMASYON BOTU v2 ↔ ORTAK KATALOG — üretim tarama yolu (`PatternScanner.scan_cycle` → `PatternBook.process_symbol`
→ katalog → `build_plans_v2` → tetik → `_try_open` → `apply_action` → defter → çıkış izleyicisi).

Veri SENTETİK ve etiketlidir (MockProvider, `structure_fixtures`); dedektör, plan, risk ve defter kodu gerçektir.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_fixtures import _bar, breakout_bar, neutral_trend, pole_and_consolidation  # noqa: E402
from test_pattern_trader_v1 import CLOCK, DAY, H1, H4, M15, T0, _cfg, _df, _exinfo, _provider, _scanner, _set_mark  # noqa: E402

from tradingbot.pattern_trader.strategy import PL_AWAITING, PL_CLOSED, PL_MANAGED, PL_TRIGGERED  # noqa: E402
from tradingbot.structures.store import StructureStore  # noqa: E402

SYM = "LNG/USDT"


@pytest.fixture(autouse=True)
def _reset_clock():
    CLOCK[0] = T0
    yield
    CLOCK[0] = T0


def _enforce_cfg(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.v3.structures.enabled = True
    cfg.v3.structures.pattern_trader = "ENFORCE"
    return cfg


def _agg(rows, to_step):
    """Tam (eksiksiz) üst dilim barları: alt barların OHLC birleşimi. Eksik grup (kapanmamış üst bar) ALINMAZ."""
    groups: dict[int, list] = {}
    for r in rows:
        groups.setdefault(int(r["timestamp"]) // to_step * to_step, []).append(r)
    step = int(rows[1]["timestamp"]) - int(rows[0]["timestamp"])
    out = []
    for t in sorted(groups):
        g = groups[t]
        if len(g) != to_step // step:
            continue
        out.append(_bar(t, g[0]["open"], max(x["high"] for x in g), min(x["low"] for x in g), g[-1]["close"]))
    return out


def _split4(rows1h):
    """Her 1h barı 4 adet 15m bara böler (o→dip→tepe→c boğa, o→tepe→dip→c ayı); birleşimi 1h barın KENDİSİDİR."""
    out = []
    for r in rows1h:
        o, h, lo, c = r["open"], r["high"], r["low"], r["close"]
        q = [o, lo, h, c, c] if c >= o else [o, h, lo, c, c]
        for i in range(4):
            a, b = q[i], q[i + 1]
            out.append(_bar(int(r["timestamp"]) + i * M15, a, max(a, b), min(a, b), b))
    return out


def _frames(rows15):
    return {"15m": _df(rows15), "1h": _df(_agg(rows15, H1)), "4h": _df(_agg(rows15, H4))}


def _flag15():
    base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=0.3)
    # GENİŞ bayrak: kaldıraç 1'de tek pozisyon tavanı (%30) ile boyutlanabilmesi için stop ≥ ~%6,7 (risk %2 / %30).
    # Dar bir 15m bayrak üretimde MAX_POSITION_PCT ile REDDEDİLİR — bu testin konusu değil (ayrı testte kanıtlanıyor).
    flag = pole_and_consolidation(base, step=M15, l_off=0.045, slope=0.005)
    # Oluşan kayıt TETİĞİ EN YAKIN yorumdur (en kısa geçerli konsolidasyon = son 3 bar): kırılış barı onun biraz üstünde kapanır
    top = max(r["high"] for r in flag[-3:])
    return flag, breakout_bar(flag, step=M15, above=top, pct=0.004)


def _open_rows(plans, fam):
    return [(pl["family"], pl["entry_tf"], pl["status"], pl.get("reasons")) for pl in plans.values() if fam is None or pl["family"] == fam]


EX = [("LNGUSDT", "LNG", "USDT", "PERPETUAL", "TRADING", T0 - 500 * DAY)]


def test_catalog_flag_becomes_a_plan_waits_for_its_close_opens_after_it_and_closes_at_target(tmp_path):
    flag, brk = _flag15()
    p = _provider({SYM: _frames(brk)}, _exinfo(EX))
    cfg = _enforce_cfg(tmp_path)
    sc, book = _scanner(cfg, p)
    assert book.structure_mode == "ENFORCE"
    # bayrak OLUŞUYOR (kırılış barı henüz kapanmadı) → koşullu LONG planı, emir YOK
    CLOCK[0] = int(flag[-1]["timestamp"]) + M15
    _set_mark(p, SYM, flag[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    pls = [pl for pl in book.plans.values() if pl["family"] == "D2_CHART_STRUCTURE" and pl["entry_tf"] == "15m"]
    assert len(pls) == 1 and pls[0]["status"] == PL_AWAITING and not book.ledger.positions, _open_rows(book.plans, None)
    plan = pls[0]
    assert plan["structure"]["name"] == "BULL_FLAG" and plan["structure"]["status"] == "FORMING"
    assert plan["stop"] < plan["trigger"]["level"] < plan["target"]
    assert plan["target_source"] in ("structure_measured_move", "fallback_rr", "opposing_1h_zone")
    assert plan["p_win"] is None and plan["version"] == "pattern_protocol_v2.0.0"
    assert any(f["shape"] == "BULL_FLAG" and f["status"] == "FORMING" for f in book.findings.values()), "bulgular katalogdan"
    # kırılış barı KAPANDI → tetik → risk → giriş (tetik kapanışından sonraki doğrulanmış fiyat)
    CLOCK[0] = int(brk[-1]["timestamp"]) + M15
    _set_mark(p, SYM, brk[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert SYM in book.ledger.positions, (plan["status"], plan.get("reasons"), plan.get("reject_detail"))
    pos = book.ledger.positions[SYM]
    assert plan["status"] == PL_MANAGED and plan["triggered_at_ms"] == int(brk[-1]["timestamp"]) + M15
    assert float(pos.entry_avg) == pytest.approx(brk[-1]["close"], rel=2e-3), "fill = tetik kapanışından SONRAKİ doğrulanmış fiyat"
    assert float(pos.stop) == pytest.approx(plan["stop"]) and [float(t) for t in pos.targets] == [pytest.approx(plan["target"])]
    st = pos.features["structure"]
    assert st["name"] == "BULL_FLAG" and st["pattern_id"] == plan["pattern_id"] and st["timeframe"] == "15m"
    assert pos.meta["plan_id"] == plan["plan_id"] and pos.meta["protocol_version"] == "pattern_protocol_v2.0.0"
    row = StructureStore(cfg.state_path).latest_decisions()["pattern_trader|USDM_PERP|%s" % SYM]
    assert row["trade_id"] == pos.id and row["action"] == "ENTER" and plan["pattern_id"] in row["pattern_ids"]
    # hedef → kapanış
    CLOCK[0] += 10 * M15
    _set_mark(p, SYM, float(plan["target"]) * 1.001)
    recs = sc.exit_check()
    assert len(recs) == 1 and not book.ledger.positions and plan["status"] == PL_CLOSED
    h = book.ledger.history_dicts()[-1]
    assert (h.get("features") or {}).get("structure", {}).get("pattern_id") == plan["pattern_id"]
    # aynı veriyle yeniden tarama ve YENİDEN BAŞLATMA aynı yapıdan ikinci işlem açmaz
    sc.scan_cycle(now_ms=CLOCK[0])
    assert not book.ledger.positions and book.counters["opened"] == 1
    sc2, book2 = _scanner(cfg, p)
    sc2.scan_cycle(now_ms=CLOCK[0] + 60_000)
    assert not book2.ledger.positions and book2.counters["opened"] == 1, _open_rows(book2.plans, None)


def test_a_narrow_flag_is_planned_triggered_and_then_refused_by_the_unchanged_risk_cap(tmp_path):
    """Dar (~%5 stop) bayrak: plan ve tetik çalışır; kaldıraç 1'de tek pozisyon tavanı (%30) aşılır → RED, gerekçeli.
    Kapılar gevşetilmedi: yapı bağlantısı işlem sayısını artırmak için risk tavanını ATLAMAZ."""
    base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=0.3)
    flag = pole_and_consolidation(base, step=M15)
    brk = breakout_bar(flag, step=M15, above=max(r["high"] for r in flag[-5:]))
    p = _provider({SYM: _frames(brk)}, _exinfo(EX))
    sc, book = _scanner(_enforce_cfg(tmp_path), p)
    CLOCK[0] = int(flag[-1]["timestamp"]) + M15
    _set_mark(p, SYM, flag[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = int(brk[-1]["timestamp"]) + M15
    _set_mark(p, SYM, brk[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    d2 = [pl for pl in book.plans.values() if pl["family"] == "D2_CHART_STRUCTURE"]
    assert d2 and d2[0]["triggered_at_ms"] == int(brk[-1]["timestamp"]) + M15, _open_rows(book.plans, None)
    assert d2[0]["status"] == "REJECTED" and "MAX_POSITION_PCT" in d2[0]["reasons"] and not book.ledger.positions


def _sweep15():
    base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=2.0)
    p0, t = base[-1]["close"], base[-1]["timestamp"]
    seq = [(1.000, 1.030, 0.990, 1.020), (1.020, 1.060, 1.010, 1.050), (1.050, 1.100, 1.040, 1.080),   # tepe: 1.100
           (1.078, 1.085, 1.040, 1.050), (1.050, 1.070, 1.020, 1.030), (1.030, 1.060, 1.010, 1.040),
           (1.040, 1.170, 1.035, 1.090)]                                   # süpürme: 1.170 > 1.100, kapanış 1.090 içeride
    return list(base) + [_bar(t + (i + 1) * M15, p0 * o, p0 * h, p0 * lo, p0 * c) for i, (o, h, lo, c) in enumerate(seq)]


def test_a_confirmed_sweep_creates_a_triggered_plan_that_opens_in_the_same_scan(tmp_path):
    rows15 = _sweep15()
    p = _provider({SYM: _frames(rows15)}, _exinfo(EX))
    sc, book = _scanner(_enforce_cfg(tmp_path), p)
    CLOCK[0] = int(rows15[-1]["timestamp"]) + M15
    _set_mark(p, SYM, rows15[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    e2 = [pl for pl in book.plans.values() if pl["family"] == "E2_SWEEP_RECLAIM"]
    assert e2 and e2[0]["side"] == "SHORT", _open_rows(book.plans, None)
    pl = e2[0]
    assert any(h["status"] == PL_TRIGGERED for h in pl["status_history"])
    assert pl["triggered_at_ms"] == int(rows15[-1]["timestamp"]) + M15, "tetik = teyit kapanışı (geriye yazılmaz)"
    assert SYM in book.ledger.positions, (pl["status"], pl.get("reasons"), pl.get("reject_detail"), pl.get("cancel_detail"))
    pos = book.ledger.positions[SYM]
    assert pos.side.value == "SHORT" and pos.features["structure"]["name"] == "SWEEP_RECLAIM"


def test_a_sweep_reclaimed_far_inside_is_cancelled_by_the_chase_limit(tmp_path):
    rows15 = _sweep15()
    last = rows15[-1]
    p0 = rows15[-8]["close"]
    rows15[-1] = _bar(last["timestamp"], last["open"], last["high"], p0 * 1.020, p0 * 1.030)   # 1.100'ün 0.07 altı (>1 ATR)
    p = _provider({SYM: _frames(rows15)}, _exinfo(EX))
    sc, book = _scanner(_enforce_cfg(tmp_path), p)
    CLOCK[0] = int(rows15[-1]["timestamp"]) + M15
    _set_mark(p, SYM, rows15[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    e2 = [pl for pl in book.plans.values() if pl["family"] == "E2_SWEEP_RECLAIM"]
    assert e2 and e2[0]["status"] == "CANCELLED" and "CHASE_LIMIT" in e2[0]["reasons"], _open_rows(book.plans, None)
    assert e2[0]["cancel_detail"]["distance_atr"] > e2[0]["chase_atr"] and not book.ledger.positions


def _flag1h_frames():
    base1h = neutral_trend(100, start_ms=T0 - 120 * H1, step=H1, px0=50.0, up=True, scale=0.3)
    flag1h = pole_and_consolidation(base1h, step=H1, l_off=0.045, slope=0.005)
    top = max(r["high"] for r in flag1h[-3:])                 # oluşan kaydın (en yakın yorum) tetiği
    t_open = int(flag1h[-1]["timestamp"]) + H1
    last = flag1h[-1]["close"]
    subs = [_bar(t_open, last, top * 1.008, last * 0.999, top * 1.005),               # 15m kapanışı 1h tetiğin ÜSTÜNDE
            _bar(t_open + M15, top * 1.005, top * 1.006, top * 1.001, top * 1.002),
            _bar(t_open + 2 * M15, top * 1.002, top * 1.005, top * 1.001, top * 1.004),
            _bar(t_open + 3 * M15, top * 1.004, top * 1.005, top * 1.002, top * 1.003)]
    return flag1h, _split4(flag1h), subs, t_open, top


def test_a_1h_structure_is_triggered_by_the_1h_close_not_by_a_15m_close(tmp_path):
    flag1h, rows15, subs, t_open, top = _flag1h_frames()
    p = _provider({SYM: {"15m": _df(rows15 + subs[:1]), "1h": _df(flag1h), "4h": _df(_agg(flag1h, H4))}}, _exinfo(EX))
    sc, book = _scanner(_enforce_cfg(tmp_path), p)
    # 15m bar kapandı (1h tetiğin üstünde) — 1h bar KAPANMADI → 1h plan TETİKLENMEZ
    CLOCK[0] = t_open + M15
    _set_mark(p, SYM, subs[0]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    d2 = [pl for pl in book.plans.values() if pl["family"] == "D2_CHART_STRUCTURE" and pl["entry_tf"] == "1h"]
    assert d2 and d2[0]["status"] == PL_AWAITING and d2[0]["trigger"]["tf"] == "1h", _open_rows(book.plans, None)
    assert d2[0]["structure"]["name"] == "BULL_FLAG" and not book.ledger.positions, _open_rows(book.plans, None)
    # 1h kırılış barı KAPANDI → 1h tetik → giriş
    rows1h = flag1h + _agg(subs, H1)
    p._candles[(SYM.replace("/", ""), "15m")] = _df(rows15 + subs)
    p._candles[(SYM.replace("/", ""), "1h")] = _df(rows1h)
    CLOCK[0] = t_open + H1 + 60_000
    _set_mark(p, SYM, subs[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert d2[0]["triggered_at_ms"] == t_open + H1, (d2[0]["status"], d2[0].get("reasons"))
    assert SYM in book.ledger.positions, (d2[0]["status"], d2[0].get("reasons"), d2[0].get("reject_detail"), d2[0].get("cancel_detail"))
    assert book.ledger.positions[SYM].features["structure"]["timeframe"] == "1h"


def _compression15(close_mult: float):
    base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=3.0)
    p0, t = base[-1]["close"], base[-1]["timestamp"]
    seq = [(1.000, 1.030, 0.975, 1.010), (1.010, 1.025, 0.980, 0.990), (0.990, 1.020, 0.978, 1.005),
           (1.005, 1.028, 0.982, 0.995), (0.995, 1.022, 0.979, 1.010), (1.010, 1.026, 0.985, 1.000)]   # 6 bar: 1.030/0.975
    rows = list(base) + [_bar(t + (i + 1) * M15, p0 * o, p0 * h, p0 * lo, p0 * c) for i, (o, h, lo, c) in enumerate(seq)]
    brk = _bar(rows[-1]["timestamp"] + M15, p0 * 1.000, p0 * (close_mult + 0.004), p0 * 0.998, p0 * close_mult)
    return rows, rows + [brk]


def test_one_price_structure_seen_as_flag_and_paired_compression_opens_once_and_ends_every_other_plan_with_a_reason(tmp_path):
    """Güçlü direkten sonraki 6 barlık dar aralık hem BAYRAK hem iki taraflı SIKIŞMA kaydıdır. Kırılış kapanışında:
    SHORT sıkışma planı (kaydı BOZULDUĞU için) BOZULUR; bayrak kaydı teyitte KİMLİĞİNİ KORUR; iki LONG plan aynı kapanışta tetiklenir, ilk oluşturulan dolar,
    diğeri OTHER_PLAN_FILLED ile iptal edilir. Sembolde TEK pozisyon; bekleyen plan kalmaz."""
    rows, withbrk = _compression15(1.035)
    p = _provider({SYM: _frames(withbrk)}, _exinfo(EX))
    sc, book = _scanner(_enforce_cfg(tmp_path), p)
    CLOCK[0] = int(rows[-1]["timestamp"]) + M15
    _set_mark(p, SYM, rows[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    c2 = {pl["side"]: pl for pl in book.plans.values() if pl["family"] == "C2_COMPRESSION"}
    d2 = [pl for pl in book.plans.values() if pl["family"] == "D2_CHART_STRUCTURE"]
    assert set(c2) == {"LONG", "SHORT"} and all(pl["status"] == PL_AWAITING for pl in c2.values()), _open_rows(book.plans, None)
    assert len(d2) == 1 and d2[0]["structure"]["name"] == "BULL_FLAG" and d2[0]["status"] == PL_AWAITING
    CLOCK[0] = int(withbrk[-1]["timestamp"]) + M15
    _set_mark(p, SYM, withbrk[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert len(book.ledger.positions) == 1 and book.ledger.positions[SYM].side.value == "LONG"
    assert d2[0]["status"] == PL_MANAGED and book.ledger.positions[SYM].features["structure"]["name"] == "BULL_FLAG"
    assert c2["SHORT"]["status"] == "BROKEN" and c2["SHORT"]["reasons"][-1] == "RECORD_BROKEN", "plan ortak kaydı izler"
    assert c2["LONG"]["triggered_at_ms"] == d2[0]["triggered_at_ms"] and c2["LONG"]["status"] == "CANCELLED"
    assert c2["LONG"]["reasons"][-1] == "OTHER_PLAN_FILLED:%s" % d2[0]["plan_id"]
    assert not any(pl["status"] in (PL_AWAITING, PL_TRIGGERED) for pl in book.plans.values())


def _run_v1_chain(tmp_path, mode):
    from test_pattern_trader_v1 import _trend_series, long_15m
    rows15 = long_15m()
    rows4h = _trend_series(30, start_ts=T0 - 30 * H4, step=H4, px0=70.0, drift=1.0)
    rows1h = _trend_series(30, start_ts=T0 - 30 * H1, step=H1, px0=95.0, drift=0.2)
    p = _provider({SYM: {"15m": _df(rows15), "1h": _df(rows1h), "4h": _df(rows4h)}}, _exinfo(EX))
    cfg = _cfg(tmp_path)
    if mode != "OFF":
        cfg.v3.structures.enabled = True
        cfg.v3.structures.pattern_trader = mode
    sc, book = _scanner(cfg, p)
    for k, px in ((39, rows15[38]["close"]), (40, rows15[39]["close"]), (41, rows15[40]["close"])):
        CLOCK[0] = T0 + k * M15
        _set_mark(p, SYM, px)
        sc.scan_cycle(now_ms=CLOCK[0])
    CLOCK[0] = T0 + 50 * M15
    pl = next(pl for pl in book.plans.values() if pl["family"] == "A_TREND_PULLBACK")
    _set_mark(p, SYM, float(pl["target"]) + 0.5)
    sc.exit_check()
    strip = ("id", "trade_id", "opened_at", "closed_at", "features", "meta", "ledger_notes")
    hist = [{k: v for k, v in h.items() if k not in strip} for h in book.ledger.history_dicts()]
    plans = sorted((q["family"], q["side"], q["status"], q["trigger"]["level"], q["stop"], q["target"]) for q in book.plans.values())
    return hist, plans, cfg


def test_shadow_mode_leaves_the_v1_chain_unchanged_and_records_the_v2_view_as_shadow(tmp_path):
    h_off, p_off, _ = _run_v1_chain(tmp_path / "off", "OFF")
    CLOCK[0] = T0
    h_sh, p_sh, cfg = _run_v1_chain(tmp_path / "shadow", "SHADOW")
    assert h_off and h_off == h_sh and p_off == p_sh, "SHADOW işlem/plan sonucunu DEĞİŞTİRMEZ"
    rows = [r for r in StructureStore(cfg.state_path).latest_decisions().values() if r.get("book_id") == "pattern_trader"]
    assert rows and all(r.get("mode") == "SHADOW" and not r.get("trade_id") for r in rows), rows
    assert any(r.get("reason_code") == "PLAN_A2_TREND_PULLBACK" for r in rows), "v2 aynı geri çekilmeyi katalogdan görüyor"


def test_the_plan_follows_the_record_while_the_flag_develops_and_is_cancelled_when_the_record_is_withdrawn(tmp_path):
    """Plan kendi seviyesini DONDURMAZ: bayrak bir bar daha uzayınca (aynı kimlik) tetik kayıttan yenilenir; konsolidasyon
    tanımı bozulunca (direğin yarısından fazlası geri verilir) kayıt analizden çekilir → plan RECORD_WITHDRAWN ile iptal."""
    base = neutral_trend(100, start_ms=T0, step=M15, px0=50.0, up=True, scale=0.3)
    f5 = pole_and_consolidation(base, step=M15, n=5, l_off=0.045, slope=0.005)
    f6 = pole_and_consolidation(base, step=M15, n=6, l_off=0.045, slope=0.005)
    last = f6[-1]
    deep = f6 + [_bar(last["timestamp"] + M15, last["close"], last["close"] * 1.001, last["close"] * 0.80, last["close"] * 0.81)]
    p = _provider({SYM: _frames(deep)}, _exinfo(EX))
    sc, book = _scanner(_enforce_cfg(tmp_path), p)
    CLOCK[0] = int(f5[-1]["timestamp"]) + M15
    _set_mark(p, SYM, f5[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    pl = next(x for x in book.plans.values() if x["family"] == "D2_CHART_STRUCTURE")
    t1, inv1, pid = pl["trigger"]["level"], pl["invalidation"]["level"], pl["pattern_id"]
    assert pl["status"] == PL_AWAITING and pl["record_revisions"] == 0
    # bayrak bir bar uzadı: AYNI kimlik, tetik kayıttan yenilendi (plan donmuş seviye taşımıyor)
    CLOCK[0] = int(f6[-1]["timestamp"]) + M15
    _set_mark(p, SYM, f6[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert pl["status"] == PL_AWAITING and pl["pattern_id"] == pid and pl["record_revisions"] == 1
    assert pl["invalidation"]["level"] < inv1, "konsolidasyon aşağı uzadı: geçersizlik/stop kayıttan yenilendi"
    assert pl["trigger"]["level"] <= t1 and pl["revision_history"][-1]["invalidation"]["level"] == pytest.approx(inv1)
    # direğin yarısından fazlası geri verildi: bayrak tanımı artık sağlanmıyor → kayıt çekildi → plan iptal (işlem YOK)
    CLOCK[0] = int(deep[-1]["timestamp"]) + M15
    _set_mark(p, SYM, deep[-1]["close"])
    sc.scan_cycle(now_ms=CLOCK[0])
    assert pl["status"] == "CANCELLED" and pl["reasons"][-1] == "RECORD_WITHDRAWN" and not book.ledger.positions
