# -*- coding: utf-8 -*-
"""T2/M2 PAPER İŞLEM YOLU — VERİ KAYNAĞI DOĞRULAMASI (2026-09-16).

25adb9d: grafik kaydı SPOT/kaynağı bilinmeyen çerçeveden T2/M2 futures analizi yazmıyordu ama İŞLEM yolu aynı kontrolü
yapmıyordu (`_strategy_paper_tour` provenans iletmiyor, `StrategyBook.step` doğrulamıyor, `apply_action` kaynak bakmadan
futures PAPER pozisyonu açıyordu; tick'ler SPOT ticker/1h fitilleriyle besleniyordu). Bu dosya gerçek motor turu +
gerçek StrategyBook/defter ile (ağ yerine deterministik test adaptörleri: sentetik çerçeveler, sahte canlı snapshot) yeni
davranışı doğrular. Taklit edilen katman: veri SAĞLAYICISI (çerçeve + snapshot + ilan edilen provenans); doğrulama yolu
(`_bind_provenance` → `verify_paper_data` → `apply_action(data=)`) gerçek koddur; kontrolün kendisi mock'lanmaz.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_chart_analysis_v1_fixes import install_perp_frames  # noqa: E402
from test_engine_v3 import _engine  # noqa: E402
from test_risk_capacity_and_gates import EQUITY, _force_triggers, _profile  # noqa: E402
from test_strategy_paper_engine_v1 import BTC, SYMS, _install  # noqa: E402
from tradingbot.accounting import MarketType, TickData  # noqa: E402
from tradingbot.core import utc_now  # noqa: E402
from tradingbot.strategy_paper import PAPER_MARKET, DataVerdict, apply_action, verify_paper_data  # noqa: E402

BOOKS = ("strategy_paper", "strategy_paper_m2")


def _ov(chart: bool = False) -> dict:
    return _profile(6.0) | {"strategy_paper": {"enabled": True, "name": "t2_trend_regime", "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"}]},
                            "chart_analysis": {"enabled": chart, "keep_per_series": 20},
                            "news": {"enabled": False}}            # venue olayı için ağa çıkılmaz (ağsız test; yeniden deneme gecikmesi yok)


def _eng(root: Path, mp, *, market="USDM_PERP", btc_market="USDM_PERP", coin_above=True, btc_up=True, chart=False):
    eng = _engine(root, mp, _ov(chart), symbols=2, equity=EQUITY)
    _force_triggers(mp, False)
    _install(eng, mp, btc_up=btc_up, coin_above=coin_above, market=market, btc_market=btc_market)
    return eng


def _tour(eng) -> None:
    eng.tour(do_scan=False, obsidian=False, charts=False)


def _books(eng) -> dict:
    return {b.key: b for b in eng.strategy_books}


def _positions(eng) -> dict:
    return {k: sorted(b.ledger.positions) for k, b in _books(eng).items()}


def _summary(eng, key: str) -> dict:
    fn = "strategy_paper.json" if key == "strategy_paper" else key + ".json"
    return json.loads((eng.cfg.state_path / fn).read_text(encoding="utf-8"))


def _last_ts(df) -> int:
    return int(df["timestamp"].iloc[-1])


# ====================================================================== 1) SPOT-only çerçeveler
def test_1_spot_only_frames_open_no_paper_futures_position_and_reject_with_reason(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch, market=None, btc_market=None, chart=True)
    _tour(eng)
    assert {v.get("market") for v in eng._frame_provenance.values()} == {"SPOT"}
    assert _positions(eng) == {k: [] for k in BOOKS}
    for key, b in _books(eng).items():
        assert b.counters["opened"] == 0 and b.counters["rejected"] == 0 and b.counters["data_rejected"] == len(SYMS)
        assert b.rejections == {"DATA_MARKET_SPOT": len(SYMS)}, "veri kaynağı reddi risk/sinyal reddinden AYRI ve gerekçeli"
        for s in SYMS:
            assert b.data_checks[s]["ok"] is False and b.data_checks[s]["reason"] == "DATA_MARKET_SPOT" and b.data_checks[s]["market"] == "SPOT"
            assert b.last_actions[s]["action"] == "DATA_REJECTED" and b.last_actions[s]["stage"] == "SIGNAL"
        ev = [e for e in b.data_events if e["kind"] == "REJECT"]
        assert len(ev) == len(SYMS) and all(e["would_act"] == "OPEN" and e["stage"] == "SIGNAL" and e["market"] == "SPOT" for e in ev), "kural OPEN derdi; kanıtsız veriyle UYGULANMADI"
        doc = _summary(eng, key)
        assert doc["counters"]["data_rejected"] == len(SYMS) and doc["data_checks"][SYMS[0]]["reason"] == "DATA_MARKET_SPOT"
        assert doc["data_policy"]["market"] == PAPER_MARKET and doc["data_events_recent"][-1]["kind"] == "REJECT"
        assert not b.ledger.history_dicts(), "uydurma işlem yok"
    # grafik kaynak ayrımı korunur: kâğıt defter kaydı yok, skipped MARKET_MISMATCH
    cfgj = json.loads((eng.cfg.state_path / "chart_analysis" / "config.json").read_text(encoding="utf-8"))
    assert len(cfgj["skipped"]) == len(BOOKS) * len(SYMS) and all(v["status"] == "MARKET_MISMATCH" for v in cfgj["skipped"].values())


# ====================================================================== 2) provenans yok / bayat / çelişkili / giriş kapalı
def test_2_missing_stale_or_contradictory_provenance_never_authorises_an_entry(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}, "doğrulanmış veriyle giriş"
    book = _books(eng)["strategy_paper"]
    sym = SYMS[0]
    rej: list = []
    common = dict(symbol=sym, price=100.0, tick=None, now=utc_now(), ledger=book.ledger, risk=book.risk, profile=book.profile,
                  state=book._state({}), filters=book.filters_cache.get(sym, MarketType.USDM_PERP), run_id="x",
                  reject=lambda s, r: rej.append(r), on_closed=lambda rec: None)
    # ortak uygulayıcı fail-closed: hüküm YOKSA ne CLOSE ne OPEN
    assert apply_action({"action": "CLOSE", "reason": "X"}, **common) == "REJECTED" and rej[-1] == "DATA_VERDICT_MISSING"
    bad = DataVerdict(ok=False, entry_ok=False, reason="DATA_MARKET_SPOT")
    assert apply_action({"action": "CLOSE", "reason": "X"}, data=bad, **common) == "REJECTED" and rej[-1] == "DATA_MARKET_SPOT"
    assert sym in book.ledger.positions, "reddedilen CLOSE pozisyona dokunmadı"
    for s in list(book.ledger.positions):
        book.ledger.close_manual(s, float(book.ledger.positions[s].entry_avg), reason="TEST", now=utc_now())
    half = DataVerdict(ok=True, entry_ok=False, reason="DATA_BTC_MARKET_SPOT", market="USDM_PERP")
    opn = {"action": "OPEN", "direction": "LONG", "stop": 90.0, "targets": [], "leverage": 1, "name": "t2_trend_regime", "reason": "T"}
    assert apply_action(opn, **common) == "REJECTED" and rej[-1] == "DATA_VERDICT_MISSING"
    assert apply_action(opn, data=half, **common) == "REJECTED" and rej[-1] == "DATA_BTC_MARKET_SPOT"
    assert sym not in book.ledger.positions
    # tur 2: indirme başarısız → bu turda provenans YOK → eski turun onayı/çerçevesi yeniden giriş VEREMEZ
    for b in _books(eng).values():
        for s in list(b.ledger.positions):
            b.ledger.close_manual(s, float(b.ledger.positions[s].entry_avg), reason="TEST", now=utc_now())

    def boom(symbol, analysis=None, prefetched=None):
        raise RuntimeError("download failed")
    monkeypatch.setattr(eng.runner, "run_symbol", boom)
    _tour(eng)
    assert eng._frame_provenance == {} and _positions(eng) == {k: [] for k in BOOKS}
    for b in _books(eng).values():
        assert b.rejections.get("DATA_PROVENANCE_MISSING") == len(SYMS)
    # saf sözleşme: bayat tur kimliği, çerçeve uyuşmazlığı, sağlayıcının giriş kapatması
    frames = {s: eng.runner.last_frames[s] for s in SYMS + (BTC,)}

    def prov(tour, **over):
        p = {s: {"market": "USDM_PERP", "source": "test", "entry_ok": True, "tour_id": tour,
                 "frames": {tf: {"last_ts": _last_ts(df), "n": len(df)} for tf, df in frames[s].items()}} for s in frames}
        for s, ov in over.items():
            p[s] = {**p[s], **ov}
        return p
    # ZAMAN SÖZLEŞMESİ (2026-09-16): değerlendirme anı açık girdi (`as_of_ms`); verilmezse güncellik denetlenemez → ret.
    as_of = int(utc_now().timestamp() * 1000)
    ok = verify_paper_data(symbol=sym, frames=frames[sym], provenance=prov("NEW")[sym], run_id="NEW", btc_frames=frames[BTC], btc_provenance=prov("NEW")[BTC], as_of_ms=as_of)
    assert ok.ok and ok.entry_ok and ok.reason == "" and ok.bars["1d"] == _last_ts(frames[sym]["1d"]) and ok.btc["ok"] is True and ok.as_of_ms == as_of
    assert verify_paper_data(symbol=sym, frames=frames[sym], provenance=prov("NEW")[sym], run_id="NEW", btc_frames=frames[BTC], btc_provenance=prov("NEW")[BTC]).reason == "DATA_AS_OF_MISSING"
    stale = verify_paper_data(symbol=sym, frames=frames[sym], provenance=prov("OLD")[sym], run_id="NEW", btc_frames=frames[BTC], btc_provenance=prov("NEW")[BTC], as_of_ms=as_of)
    assert not stale.ok and stale.reason == "DATA_PROVENANCE_STALE"
    p = prov("NEW"); p[sym]["frames"]["1d"]["last_ts"] += 1
    mism = verify_paper_data(symbol=sym, frames=frames[sym], provenance=p[sym], run_id="NEW", btc_frames=frames[BTC], btc_provenance=p[BTC], as_of_ms=as_of)
    assert not mism.ok and mism.reason == "DATA_FRAME_MISMATCH_1D", "provenans bellekteki çerçeveyle ilişkilendirilemiyor"
    p = prov("NEW", **{sym: {"entry_ok": False, "reason": "FUTURES_FRAMES_UNAVAILABLE"}})
    blk = verify_paper_data(symbol=sym, frames=frames[sym], provenance=p[sym], run_id="NEW", btc_frames=frames[BTC], btc_provenance=p[BTC], as_of_ms=as_of)
    assert blk.ok and not blk.entry_ok and blk.reason == "DATA_ENTRY_BLOCKED:FUTURES_FRAMES_UNAVAILABLE"
    p = prov("NEW", **{sym: {"market": "SPOT"}})
    assert verify_paper_data(symbol=sym, frames=frames[sym], provenance=p[sym], run_id="NEW", btc_frames=frames[BTC], btc_provenance=p[BTC], as_of_ms=as_of).reason == "DATA_MARKET_SPOT"
    assert verify_paper_data(symbol=sym, frames=frames[sym], provenance=None, run_id="NEW", as_of_ms=as_of).reason == "DATA_PROVENANCE_MISSING"
    assert verify_paper_data(symbol=sym, frames={}, provenance=prov("NEW")[sym], run_id="NEW", as_of_ms=as_of).reason == "DATA_FRAME_MISSING_1D"


# ====================================================================== 3) coin USDM_PERP, BTC SPOT / eksik
@pytest.mark.parametrize("btc_market,reason", [("SPOT", "DATA_BTC_MARKET_SPOT"), (None, "DATA_BTC_PROVENANCE_MISSING")])
def test_3_verified_coin_but_unverified_btc_reference_blocks_new_entry(tmp_path: Path, monkeypatch, btc_market, reason):
    eng = _eng(tmp_path / "e", monkeypatch, btc_market=btc_market)
    _tour(eng)
    assert _positions(eng) == {k: [] for k in BOOKS}
    for b in _books(eng).values():
        assert b.rejections == {reason: len(SYMS)} and b.counters["data_rejected"] == len(SYMS)
        for s in SYMS:
            c = b.data_checks[s]
            assert c["ok"] is True and c["entry_ok"] is False and c["reason"] == reason and c["btc"]["required"] is True and c["btc"]["reason"] == reason
            assert b.last_actions[s]["stage"] == "ENTRY"


# ====================================================================== 4) doğrulanmış futures coin + BTC (fiyatlar spotun 2 katı)
def test_4_verified_futures_frames_and_price_are_used_for_entries_and_recorded(tmp_path: Path, monkeypatch):
    spot = _eng(tmp_path / "spot", monkeypatch, market=None, btc_market=None)
    _tour(spot)
    spot_mark = {s: float((spot._frame_provenance[s].get("perp_mark") or {}).get("price") or 0) for s in SYMS}
    assert all(v > 0 for v in spot_mark.values())
    eng = _eng(tmp_path / "perp", monkeypatch)
    install_perp_frames(eng, monkeypatch, scale=2.0)                # aynı zaman damgaları, belirgin farklı (×2) fiyatlar
    _tour(eng)
    assert {v.get("market") for v in eng._frame_provenance.values()} == {"USDM_PERP"}
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    for key, b in _books(eng).items():
        assert b.rejections == {} and b.counters["data_rejected"] == 0 and b.counters["opened"] == len(SYMS)
        for s in SYMS:
            pos = b.ledger.positions[s]
            entry, stop = float(pos.entry_avg), float(pos.stop)
            assert abs(entry / (2.0 * spot_mark[s]) - 1.0) < 0.01, "giriş DOĞRULANMIŞ perp fiyatından (spotun 2 katı), spot fiyatından DEĞİL"
            assert 0 < stop < entry
            ds = pos.features["data_source"]
            assert ds["market"] == "USDM_PERP" and ds["tour_id"] == eng.run_id and ds["bars"]["1d"] == _last_ts(eng.runner.last_frames[s]["1d"]) and ds["btc"]["ok"] is True
            assert pos.meta["data_source"]["market"] == "USDM_PERP"
            c = b.data_checks[s]
            assert c["ok"] and c["entry_ok"] and c["bars"]["1d"] == ds["bars"]["1d"]
            assert b.last_actions[s]["action"] == "OPENED" and b.last_actions[s]["data"]["market"] == "USDM_PERP"
        doc = _summary(eng, key)
        assert doc["data_checks"][SYMS[0]]["ok"] is True and doc["history_tail"] == []
    # yeniden tur: yinelenen giriş yok, ret yok
    _tour(eng)
    assert all(b.counters["opened"] == len(SYMS) and b.rejections == {} for b in _books(eng).values())


# ====================================================================== 5) açık pozisyon + çerçeve kaynağı sorunu + fiyat yolu
def test_5_open_position_keeps_protection_on_verified_price_ignores_spot_wick_and_never_fakes_a_fill(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    t2 = _books(eng)["strategy_paper"]
    entry = {s: float(t2.ledger.positions[s].entry_avg) for s in SYMS}
    stop = {s: float(t2.ledger.positions[s].stop) for s in SYMS}
    # tur 2: perp çerçeve alınamadı → SPOT ikamesi; SPOT 1h fitili stop'un altında (±20% süzgeci içinde); perp mark stop üstünde
    orig = eng.runner.run_symbol

    def spot_wick(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        eng._frame_provenance[symbol] = {"market": "SPOT", "source": "tradingview:BINANCE", "entry_ok": False, "reason": "FUTURES_FRAMES_UNAVAILABLE"}
        fr = dict(eng.runner.last_frames[symbol]); h1 = fr["1h"].copy()
        px = float(b.price)
        h1.loc[h1.index[-1], "low"] = px * 0.85
        h1.loc[h1.index[-1], "high"] = max(float(h1["high"].iloc[-1]), px * 1.01)
        fr["1h"] = h1; eng.runner.last_frames[symbol] = fr
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", spot_wick)
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}, "SPOT 1h fitili futures stop'unu TETİKLEMEDİ"
    for b in _books(eng).values():
        assert b.counters["closed"] == 0 and b.rejections == {"DATA_MARKET_SPOT": len(SYMS)}
        for s in SYMS:
            assert b.data_checks[s]["reason"] == "DATA_MARKET_SPOT" and b.last_actions[s]["action"] == "DATA_REJECTED"
            assert float(b.ledger.positions[s].last_price) > stop[s], "son fiyat doğrulanmış perp mark (stop üstü)"
    # tur 3: doğrulanmış perp fiyatı YOK (snapshot'ta funding.mark yok): tick yok, uydurma gerçekleşme yok, boşluk görünür
    orig_snap = eng.runner.live.snapshot
    monkeypatch.setattr(eng.runner.live, "snapshot", lambda s: {**orig_snap(s), "funding": {}})
    _tour(eng)
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    for key, b in _books(eng).items():
        assert set(b.data_gaps) == set(SYMS) and all(g["reason"] == "NO_VERIFIED_FUTURES_PRICE" for g in b.data_gaps.values())
        assert any(e["kind"] == "PRICE_GAP" for e in b.data_events) and b.counters["closed"] == 0
        assert all(b.last_actions[s]["action"] == "DATA_GAP" for s in SYMS)
        doc = _summary(eng, key)
        assert set(doc["data_gaps"]) == set(SYMS) and doc["positions"][SYMS[0]]["last_price"] == pytest.approx(float(b.ledger.positions[SYMS[0]].last_price))
    eng._strategy_paper_exit_check()                                  # 60 sn izleyici de uydurma fill üretmez
    assert _positions(eng) == {k: sorted(SYMS) for k in BOOKS}
    # tur 4 / izleyici: doğrulanmış perp mark stop'un ALTINDA → koruyucu stop çalışır (çerçeve SPOT olsa bile); boşluk kapanır
    monkeypatch.setattr(eng.runner.live, "snapshot", lambda s: {**orig_snap(s), "funding": {"rate": 0.0, "mark": stop[s] * 0.99}})
    eng._strategy_paper_exit_check()
    assert _positions(eng) == {k: [] for k in BOOKS}
    for b in _books(eng).values():
        assert b.counters["closed"] == len(SYMS) and all(c["exit_reason"] in ("stop", "STOP") or "stop" in str(c["exit_reason"]).lower() for c in b.closed_recent)
        assert b.data_gaps == {} and any(e["kind"] == "PRICE_RESTORED" for e in b.data_events)
        for h in b.ledger.history_dicts():
            assert float(h.get("exit_price") or h.get("exit") or 0) < entry[h["symbol"]], "gerçekleşme doğrulanmış perp mark ile stop'ta"


# ====================================================================== 6) kural kapanışı: sembol verisi doğrulanmış, BTC doğrulanmamış
def test_6_rule_close_uses_only_symbol_frames_btc_gap_does_not_block_the_exit(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch)
    _tour(eng)
    assert _positions(eng)["strategy_paper"] == sorted(SYMS)
    orig = eng.runner.run_symbol

    def cross_down_no_btc(symbol, analysis=None, prefetched=None):
        b = orig(symbol, analysis, prefetched)
        fr = dict(eng.runner.last_frames[symbol]); d1 = fr["1d"].copy()
        d1["ema200"] = d1["close"] * 1.05                            # günlük kapanış EMA200 altında → T2 CLOSE sinyali
        fr["1d"] = d1; eng.runner.last_frames[symbol] = fr
        eng._frame_provenance.pop(BTC, None)                        # BTC referansı bu turda doğrulanamıyor
        return b
    monkeypatch.setattr(eng.runner, "run_symbol", cross_down_no_btc)
    _tour(eng)
    t2 = _books(eng)["strategy_paper"]
    assert sorted(t2.ledger.positions) == [] and t2.counters["closed"] == len(SYMS)
    assert all(c["exit_reason"] == "EMA200_CROSS_DOWN" for c in t2.closed_recent), "kural kapanışı sembol verisiyle; BTC eksikliği engellemedi"
    for s in SYMS:
        assert t2.data_checks[s]["ok"] is True and t2.data_checks[s]["btc"]["required"] is False and t2.last_actions[s]["action"] == "CLOSED"
    assert not any(str(r).startswith("DATA_BTC") for r in t2.rejections)
    # aynı turda SPOT ikamesiyle kural CLOSE üretilmez (sembol verisi doğrulanmamış → kural değerlendirilmez)
    eng2 = _eng(tmp_path / "e2", monkeypatch)
    _tour(eng2)
    orig2 = eng2.runner.run_symbol

    def spot_cross_down(symbol, analysis=None, prefetched=None):
        b = orig2(symbol, analysis, prefetched)
        fr = dict(eng2.runner.last_frames[symbol]); d1 = fr["1d"].copy(); d1["ema200"] = d1["close"] * 1.05; fr["1d"] = d1
        eng2.runner.last_frames[symbol] = fr
        eng2._frame_provenance[symbol] = {"market": "SPOT", "source": "tradingview:BINANCE", "entry_ok": False}
        return b
    monkeypatch.setattr(eng2.runner, "run_symbol", spot_cross_down)
    _tour(eng2)
    t2b = _books(eng2)["strategy_paper"]
    assert sorted(t2b.ledger.positions) == sorted(SYMS) and t2b.counters["closed"] == 0 and t2b.rejections == {"DATA_MARKET_SPOT": len(SYMS)}
    assert all(e["would_act"] == "CLOSE" for e in t2b.data_events if e["kind"] == "REJECT"), "SPOT veriyle kural CLOSE derdi; UYGULANMADI, stop takibi sürüyor"


# ====================================================================== 7) yeniden başlatma + chart-analysis açık/kapalı
def test_7_restart_keeps_counters_and_positions_no_duplicate_entries_and_chart_flag_is_irrelevant(tmp_path: Path, monkeypatch):
    eng = _eng(tmp_path / "e", monkeypatch, market=None, btc_market=None)
    _tour(eng); _tour(eng)
    assert all(b.counters["data_rejected"] == 2 * len(SYMS) and b.counters["tours"] == 2 for b in _books(eng).values())
    eng2 = _eng(tmp_path / "e", monkeypatch)                          # yeniden başlatma: aynı state dizini, doğrulanmış veri
    for b in _books(eng2).values():
        assert b.counters["data_rejected"] == 2 * len(SYMS) and b.counters["tours"] == 2 and len(b.data_events) == 2 * len(SYMS), "sayaçlar/olaylar korunur"
    _tour(eng2)
    assert _positions(eng2) == {k: sorted(SYMS) for k in BOOKS}
    _tour(eng2)
    for b in _books(eng2).values():
        assert b.counters["opened"] == len(SYMS) and b.counters["data_rejected"] == 2 * len(SYMS) and b.counters["tours"] == 4
    eng3 = _eng(tmp_path / "e", monkeypatch)                          # ikinci yeniden başlatma: açık pozisyonlar ve sayaçlar aynı, yinelenen giriş yok
    assert _positions(eng3) == {k: sorted(SYMS) for k in BOOKS}
    _tour(eng3)
    assert all(b.counters["opened"] == len(SYMS) and b.counters["tours"] == 5 for b in _books(eng3).values())
    on = _eng(tmp_path / "on", monkeypatch, chart=True); _tour(on)
    off = _eng(tmp_path / "off", monkeypatch, chart=False); _tour(off)
    assert _positions(on) == _positions(off) == {k: sorted(SYMS) for k in BOOKS}
    assert {k: b.rejections for k, b in _books(on).items()} == {k: b.rejections for k, b in _books(off).items()} == {k: {} for k in BOOKS}


# ====================================================================== 8) replay ile ortak sözleşme
def test_8_replay_uses_the_same_contract_bound_to_the_archive_manifest(tmp_path: Path):
    from test_replay_strategy_mode_v1 import SYM, H4, _marks, _rep

    def strat(sym, t, fr, pos, rp):
        if pos is None:
            return {"action": "OPEN", "direction": "LONG", "stop": 1850.0, "targets": [], "leverage": 1, "name": "T", "reason": "probe"}
        return {"action": "CLOSE", "reason": "PROBE_EXIT"}

    rep = _rep(tmp_path / "fut", strat)                                 # futures arşivi + manifest → doğrulanmış
    t = int(rep.frames[SYM]["4h"]["timestamp"].iloc[-1])
    now = datetime.fromtimestamp((t + H4) / 1000, tz=timezone.utc)
    rep._strategy_step(t, now, *_marks(2000.0))
    assert SYM in rep.ledger2.positions and rep.result.n_opened == 1
    ds = rep.ledger2.positions[SYM].features["data_source"]
    assert ds["market"] == "USDM_PERP" and ds["source"].startswith("archive:") and ds["source"].endswith(":futures") and ds["bars"]["4h"] == t
    rep._strategy_step(t + H4, datetime.fromtimestamp((t + 2 * H4) / 1000, tz=timezone.utc), *_marks(2040.0))
    assert SYM not in rep.ledger2.positions and rep.result.trades[0]["exit_reason"] == "PROBE_EXIT"
    spot = _rep(tmp_path / "spot", strat, market="spot")               # spot arşivi: 'zaten futures' varsayımı YOK → ret
    spot._strategy_step(t, now, *_marks(2000.0))
    assert SYM not in spot.ledger2.positions and spot.result.n_opened == 0 and spot.result.rejections["by_reason"].get("DATA_MARKET_SPOT") == 1
    bare = _rep(tmp_path / "bare", strat, write_archive=False)         # manifest yok → kaynak kanıtsız → ret
    bare._strategy_step(t, now, *_marks(2000.0))
    assert SYM not in bare.ledger2.positions and bare.result.rejections["by_reason"].get("DATA_ARCHIVE_MANIFEST_MISSING") == 1
