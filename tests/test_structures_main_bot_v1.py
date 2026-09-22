# -*- coding: utf-8 -*-
"""ANA BOT ↔ ORTAK YAPI (structures_v1) — gerçek `TradingEngineV3.tour` sürülür.

Zincir: uzmanlar (mum ajanı oyunu ortak katalogdan, tek oy) → konsensüs/plan (geri çekilme planı) → tetik → C3/p2/R1
kapıları → **yapı kapısı** (`structures.bots.main_entry_decision`) → ekonomi/yinelenme/araştırma → risk → defter →
yönetim (girişten sonra teyitli karşı yapı → stop sıkılaştırma, aynı tur tick'i eski bar uçlarını KULLANMAZ).
Motorun okuduğu 4h/1d çerçevelerinin SON barları etiketli sentetik OHLC ile değiştirilir (ajanların gördüğü orijinal
kareye dokunulmaz — karar yönü değişmez); tespit ve karar kodu taklit edilmez.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from structure_fixtures import neutral_trend  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_opportunities, _force_triggers, _funnel, _opp, _profile, _risk_log  # noqa: E402

from tradingbot.core import from_iso, iso  # noqa: E402
from tradingbot.structures.store import StructureStore  # noqa: E402

SYMS = ("ETH/USDT", "SOL/USDT")


def _rel_neutral(n: int) -> list[tuple[float, float, float, float]]:
    """Göreli (başlangıç kapanışına oran) mum-nötr OHLC dizisi."""
    rows = neutral_trend(n, start_ms=0, step=1, px0=1.0, up=True, scale=0.3)
    return [(r["open"], r["high"], r["low"], r["close"]) for r in rows]


def _bull_engulf_confirmed() -> list[tuple[float, float, float, float]]:
    """Ayı bar + onu yutan boğa bar (BULLISH_ENGULFING, LONG) + boğanın TEPESİNİN ÜSTÜNDE kapanan teyit barı."""
    return [(1.001, 1.002, 0.987, 0.989), (0.987, 1.006, 0.986, 1.004), (1.005, 1.016, 1.004, 1.014)]


def _bear_engulf_confirmed() -> list[tuple[float, float, float, float]]:
    return [(1.001, 1.013, 0.999, 1.011), (1.013, 1.015, 0.992, 0.994), (0.993, 0.994, 0.982, 0.984)]


def _full(df, structure=()):
    """Motorun okuduğu karenin TAMAMI: mum-nötr zemin + (varsa) sondaki yapı parçası. Kısmi kuyruk özgün verinin eski
    tepelerini fitille aşıp gerçek bir taşma-geri dönüş üretebilirdi; tam zemin bunu dışlar (zaman damgaları aynı)."""
    struct = list(structure)
    rows = _rel_neutral(len(df) - 1 - len(struct))
    if struct:
        c = rows[-1][3]
        rows += [(o * c, h * c, lo * c, cl * c) for (o, h, lo, cl) in struct]
    # ölçek: son kapanış ORİJİNAL son kapanışa eşit (canlı fiyat = FakeLive o kapanıştan) → kovalama ölçüsü anlamlı
    base = float(df["close"].iloc[-1]) / rows[-1][3]
    for i, (o, h, lo, c) in enumerate(rows):
        idx = 1 + i
        df.iloc[idx, df.columns.get_loc("open")] = base * o
        df.iloc[idx, df.columns.get_loc("high")] = base * h
        df.iloc[idx, df.columns.get_loc("low")] = base * lo
        df.iloc[idx, df.columns.get_loc("close")] = base * c
    return df


def _install(eng, monkeypatch, plan: dict, market_decl: str | None = "USDM_PERP"):
    """`run_symbol` SONRASI motorun okuduğu 4h/1d karelerinin KOPYASINI değiştirir: plan[sym] = {"4h": yapı, "1d": yapı}
    (yapı = göreli OHLC listesi; boş liste = yalnız nötr zemin)."""
    orig = eng.runner.run_symbol

    def wrapped(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        segs = plan.get(symbol)
        if segs is not None:
            fr = dict(eng.runner.last_frames[symbol])
            for tf, struct in segs.items():
                fr[tf] = _full(fr[tf].copy(), struct)
            eng.runner.last_frames[symbol] = fr
            # İlan bir simülasyondur (strateji testleriyle aynı kalıp): çerçeve USDⓈ-M perpetual kabul edilir; motor
            # bağı `_bind_provenance` ile kurar. İlan edilmezse futures kararı spot çerçeveden yapı OKUMAZ (ayrı test).
            if market_decl:
                eng._frame_provenance[symbol] = {"market": market_decl, "source": "test:%s" % market_decl, "entry_ok": True}
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", wrapped)


def _eng(tmp_path, monkeypatch, mode: str):
    ov = _profile(6.0) | {"structures": {"enabled": True, "main": mode}, "news": {"enabled": False}}
    eng = _engine(tmp_path, monkeypatch, ov, symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {s: _opp(0.80) for s in SYMS})
    return eng


def _tour(eng):
    return eng.tour(do_scan=False, obsidian=False, charts=False)


NEUTRAL = {"4h": [], "1d": []}
BULL = {"4h": _bull_engulf_confirmed(), "1d": []}
BEAR = {"4h": _bear_engulf_confirmed(), "1d": []}


def test_pullback_plan_needs_a_confirmed_compatible_structure_and_its_levels_reach_the_trade(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, "ENFORCE")
    first, second = SYMS
    _install(eng, monkeypatch, {first: NEUTRAL,
                                second: BULL})
    _force_triggers(monkeypatch, True)
    s = _tour(eng)
    opened = [x.split(" ")[0] for x in s["opened"]]
    recs = {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS}
    assert all("LONG" in str(recs[x]["verdict"]) for x in SYMS), "senaryo LONG adaylar varsayar"
    # yapı yok → geri çekilme planı BEKLER; teyitli uyumlu yapı → GİRER
    assert recs[first]["block_code"] == "STRUCTURE:PULLBACK_NEEDS_CONFIRMED_STRUCTURE", recs[first].get("block_code")
    assert opened == [second], opened
    pos = eng.ledger2.positions[second]
    st = pos.features["structure"]
    assert st["action"] == "ENTER" and st["side"] == "LONG" and st["status"] == "CONFIRMED" and st["timeframe"] == "4h"
    assert "yapı:" in pos.trigger_text, "katalog teyidi nihai planın gerekçesinde"
    assert _funnel(eng)["run"]["structure_blocked"] == 1
    row = StructureStore(eng.cfg.state_path).latest_decisions()["main|USDM_PERP|%s" % second]
    assert row["trade_id"] == pos.id and row["action"] == "ENTER"


def test_opposing_confirmed_structure_blocks_and_shadow_or_off_do_not(tmp_path, monkeypatch):
    first, second = SYMS
    plan = {first: BEAR,
            second: BULL}
    out = {}
    for mode in ("ENFORCE", "SHADOW", "OFF"):
        eng = _eng(tmp_path / mode, monkeypatch, mode)
        _install(eng, monkeypatch, plan)
        _force_triggers(monkeypatch, True)
        s = _tour(eng)
        out[mode] = (sorted(x.split(" ")[0] for x in s["opened"]),
                     {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS})
    assert out["ENFORCE"][0] == [second]
    assert out["ENFORCE"][1][first]["block_code"] == "STRUCTURE:OPPOSING_CONFIRMED"
    assert out["ENFORCE"][1][first]["structure"]["side"] == "SHORT"
    assert out["SHADOW"][0] == sorted(SYMS) and out["SHADOW"][1][first]["structure"]["action"] == "WAIT"
    assert out["OFF"][0] == sorted(SYMS) and "structure" not in out["OFF"][1][first]


def test_opposing_structure_after_entry_tightens_the_stop_without_using_old_bar_extremes(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, "ENFORCE")
    first, second = SYMS
    _install(eng, monkeypatch, {first: NEUTRAL,
                                second: BULL})
    _force_triggers(monkeypatch, True)
    _tour(eng)
    pos = eng.ledger2.positions[second]
    stop0 = pos.stop
    # pozisyonu daha eski bir girişe taşı (karşı yapı girişten SONRA teyitli olsun) ve 4h'ye ayı yutan + teyit koy
    h4 = eng.runner.last_frames[second]["4h"]
    pos.opened_at = iso(from_iso(iso()) - timedelta(days=5))
    for f in pos.fills:
        f.ts = pos.opened_at
    _install(eng, monkeypatch, {first: NEUTRAL,
                                second: BEAR})
    _force_triggers(monkeypatch, False)
    _tour(eng)
    assert second in eng.ledger2.positions, "sıkılaştırma aynı turun tick'inde eski bar uçlarıyla stoplatmamalı"
    pos = eng.ledger2.positions[second]
    ss = pos.meta.get("structure_stop")
    assert ss is not None and Decimal(ss["to"]) > (stop0 or Decimal("0")), (ss, stop0)
    cb_low = min(float(x) for x in eng.runner.last_frames[second]["4h"]["low"].iloc[-1:])
    assert abs(float(pos.stop) - cb_low) < 1e-9, "stop = teyit barının dibi"
    assert pos.features["structure_management"]["action"] == "TIGHTEN_STOP"
    assert h4 is not None


def test_futures_decision_never_reads_structures_from_spot_frames(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, "ENFORCE")
    first, second = SYMS
    _install(eng, monkeypatch, {first: BULL, second: BULL}, market_decl=None)        # harness: SPOT etiketli kare
    _force_triggers(monkeypatch, True)
    s = _tour(eng)
    recs = {e["symbol"]: e for e in _risk_log(eng) if e.get("symbol") in SYMS}
    assert s["opened"] == []
    for x in SYMS:
        assert recs[x]["block_code"].startswith("STRUCTURE:STRUCTURE_FRAME_MARKET_MISMATCH"), recs[x].get("block_code")
