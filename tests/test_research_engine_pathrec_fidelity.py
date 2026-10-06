# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2a — fiyat yolu (`pathrec`, §5.1), yeniden oynatma doğruluğu (`fidelity`, §5.3; §10 P2 kabul 5)
ve tembel 1m doldurma (§3.3 "1m (yol için) … tembel"; veri birimi P1b kurallarıyla, REST yok).

Altın yollar elle kurulmuş barlardır (beklenen değerler elle hesaplandı). Ağ yok: veri birimi sahte
data.binance.vision + sahte REST ile çalışır (`tests/research_engine_data_fixtures.py`).
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_data_fixtures import DataEnv  # noqa: E402
from research_engine_fixtures import night_of  # noqa: E402
from research_engine_p2_fixtures import (H1, M1, M5, StoreFixture, World, aggregate, dt_of, ms, new_ledger, price_at,  # noqa: E402
                                         run_trade, standard_world, utc, walk, world_markets)

from tradingbot.accounting import AmountType, SizeSpec  # noqa: E402
from tradingbot.research_engine import datastore as DS  # noqa: E402
from tradingbot.research_engine import fidelity as FD  # noqa: E402
from tradingbot.research_engine import journal as J  # noqa: E402
from tradingbot.research_engine import lazy1m as LZ  # noqa: E402
from tradingbot.research_engine import pathrec as PR  # noqa: E402
from tradingbot.research_engine.ledgers import iso  # noqa: E402
from tradingbot.research_engine.paths import EnginePaths  # noqa: E402

NOW = utc(2026, 10, 6, 2)


def _bars(rows: list[tuple]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])
    df["phase"] = 0
    return df


# ============================================================================ altın yollar (path_metrics)
def test_golden_long_path_metrics():
    bars = _bars([(60_000, 100.0, 101.0, 99.5, 100.5),
                  (120_000, 100.5, 103.0, 100.0, 102.5),
                  (180_000, 102.5, 102.6, 97.0, 98.0)])
    m = PR.path_metrics(bars, side="LONG", entry=D("100"), rpu=D("2"), opened_ms=30_000, exit_price=D("98.4"), step_ms=M1)
    assert (m["mfe_pct"], m["mae_pct"], m["mfe_r"], m["mae_r"]) == (3.0, -3.0, 1.5, -1.5)
    assert (m["t_mfe"], m["t_mae"]) == (2.5, 3.5), "dakika: açılıştan ucun oluştuğu barın KAPANIŞINA"
    assert (m["order"], m["order_ambiguous"], m["ambiguous_bars"]) == ("MFE_FIRST", False, 0)
    assert m["time_to_1r"] == 2.5
    assert (m["exit_r_gross"], m["giveback_r"], m["capture_ratio"]) == (-0.8, 2.3, -0.533333)


def test_golden_short_path_with_unobservable_intrabar_order():
    bars = _bars([(60_000, 100.0, 100.5, 99.5, 100.0),
                  (120_000, 100.0, 101.0, 99.0, 99.2),
                  (180_000, 99.2, 99.6, 99.1, 99.3)])
    m = PR.path_metrics(bars, side="SHORT", entry=D("100"), rpu=D("1"), opened_ms=0, exit_price=D("99.3"), step_ms=M1)
    assert (m["mfe_r"], m["mae_r"]) == (1.0, -1.0)
    # iki uç da aynı barda yenilendi: sıra GÖZLENMEZ → kural sırası (ters önce) yazılır ama belirsiz işaretlenir
    assert (m["order"], m["order_ambiguous"], m["ambiguous_bars"]) == ("MAE_FIRST", True, 1)
    assert m["time_to_1r"] == 3.0 and m["exit_r_gross"] == 0.7 and m["capture_ratio"] == 0.7
    assert PR.path_metrics(bars, side="SHORT", entry=D("100"), rpu=None, opened_ms=0, exit_price=None, step_ms=M1)["mfe_r"] is None


# ============================================================================ yol kaynağı ve pencere
SYM = "XRPUSDT"
DAY = utc(2026, 9, 28)


def _store(tmp_path, *, drop_1m: tuple[int, int] | None = None, with_1m: bool = True, with_5m: bool = True):
    paths = EnginePaths.for_state(tmp_path / "data" / "state")
    m1 = walk("xrp:1m", ms(DAY), 2 * 1440, M1, 1.5, vol=0.002)
    sf = StoreFixture(paths, now=utc(2026, 10, 6))
    if with_1m:
        df = m1 if drop_1m is None else m1[(m1["timestamp"] < drop_1m[0]) | (m1["timestamp"] >= drop_1m[1])]
        sf.add("futures", SYM, "1m", df.reset_index(drop=True))
    if with_5m:
        sf.add("futures", SYM, "5m", aggregate(m1, M5))
    sf.seal()
    return paths, sf, m1


def _rec(opened, closed, *, entry="1.5", mfe="1.0", mae="-0.5"):
    return {"id": "F1", "symbol": "XRP/USDT", "side": "LONG", "opened_at": iso(opened), "closed_at": iso(closed), "entry": entry,
            "quantity": "100", "exit_price": "1.51", "mfe_pct": mfe, "mae_pct": mae}


def test_path_prefers_1m_and_window_is_strictly_between_open_and_close(tmp_path):
    paths, sf, m1 = _store(tmp_path)
    o, c = DAY + timedelta(hours=3, seconds=21), DAY + timedelta(hours=5)       # kapanış tam dakika sınırında
    res = PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=sf.source(), market="futures", risk_usdt=D("2"))
    assert res.source == PR.SRC_1M and res.tf == "1m" and res.complete and res.gaps == 0
    tb = res.bars[res.bars["phase"] == 0]
    assert int(tb["timestamp"].min()) == ms(o) // M1 * M1 + M1, "girişi içeren bar yolun parçası DEĞİL"
    assert int(tb["timestamp"].max()) == ms(c) - M1, "kapanış anında BAŞLAYAN bar çıkıştan sonradır (kuyruk)"
    assert res.n_trade_bars == res.expected_trade_bars == 119
    tail = res.bars[res.bars["phase"] == 1]
    assert len(tail) == PR.TAIL_BARS == 48 and int(tail["timestamp"].iloc[0]) == ms(c)
    assert res.parts and all(p[0] == "futures/XRPUSDT/1m" for p in res.parts)
    s = res.summary()
    assert s["path_source"] == "STORE_1M" and s["intrabar_order"] == "open>adverse>favourable>close"
    # dosya: deterministik parquet, okununca aynı barlar
    rel = PR.write_path(paths, res, iso(c))
    assert PR.read_path(paths, rel).equals(res.bars.reset_index(drop=True))
    assert PR.path_bytes(res.bars) == (paths.research / rel).read_bytes()


def test_path_falls_back_to_5m_then_to_ledger_extremes(tmp_path):
    o, c = DAY + timedelta(hours=3, seconds=21), DAY + timedelta(hours=5, minutes=7, seconds=3)
    hole = (ms(DAY + timedelta(hours=4)), ms(DAY + timedelta(hours=4, minutes=10)))
    paths, sf, _m1 = _store(tmp_path / "a", drop_1m=hole)
    res = PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=sf.source(), market="futures", risk_usdt=D("2"))
    assert res.source == PR.SRC_5M and res.complete, "1m'de boşluk, 5m tam → 5m"
    assert res.n_trade_bars == res.expected_trade_bars == 25
    paths, sf, _m1 = _store(tmp_path / "b", drop_1m=hole, with_5m=False)
    res = PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=sf.source(), market="futures", risk_usdt=D("2"))
    assert res.source == PR.SRC_1M and res.gaps == 10 and res.status == "PATH_GAP" and not res.complete
    paths, sf, _m1 = _store(tmp_path / "c", with_1m=False, with_5m=False)
    res = PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=None, market="futures", risk_usdt=D("2"))
    assert res.source == PR.SRC_EXTREMES and res.metrics["mfe_pct"] == 1.0 and res.metrics["order"] is None
    assert res.metrics["mfe_r"] == pytest.approx(1.0 / 100 * 1.5 / 0.02)
    res = PR.reconstruct(_rec(o, c, mfe=None, mae=None), trade_key="b|F1|x", src=None, market="futures", risk_usdt=None)
    assert res.source == PR.SRC_MISSING and res.status == "NO_PATH"


def test_unverified_archive_rows_are_not_used_like_store_provider(tmp_path):
    """`StoreProvider`'ın parite kapısı: `.CHECKSUM`'sız zip satırları (`archive_unverified`) varsayılan olarak verilmez."""
    paths = EnginePaths.for_state(tmp_path / "data" / "state")
    m1 = walk("xrp:1m", ms(DAY), 1440, M1, 1.5, vol=0.002)
    sf = StoreFixture(paths, now=utc(2026, 10, 6))
    sf.store.write("futures", SYM, "1m", m1, src="archive_unverified", now_ms=sf.now_ms)
    sf.add("futures", SYM, "5m", aggregate(m1, M5))
    sf.seal()
    o, c = DAY + timedelta(hours=3, seconds=21), DAY + timedelta(hours=5)
    res = PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=sf.source(), market="futures", risk_usdt=D("2"))
    assert res.source == PR.SRC_5M
    src = PR.BarSource(sf.source().reader, include_unverified=True)
    assert PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=src, market="futures", risk_usdt=D("2")).source == PR.SRC_1M


def test_a_part_rewritten_after_the_seal_is_data_moving_not_read(tmp_path):
    paths, sf, m1 = _store(tmp_path)
    src = sf.source()
    extra = walk("xrp:x", ms(DAY + timedelta(days=2)), 60, M1, 1.5, vol=0.002)
    sf.add("futures", SYM, "1m", extra)                       # aynı ay parçası mühürden sonra yeniden yazıldı
    o, c = DAY + timedelta(hours=3, seconds=21), DAY + timedelta(hours=5)
    res = PR.reconstruct(_rec(o, c), trade_key="b|F1|x", src=src, market="futures", risk_usdt=D("2"))
    assert "1m:DATA_MOVING" in res.notes and res.source == PR.SRC_5M
    assert "futures/XRPUSDT/1m" in src.moving


# ============================================================================ fidelity (kabul 5)
@pytest.fixture(scope="module")
def built(tmp_path_factory):
    w = standard_world(tmp_path_factory.mktemp("fidelity_world"))
    summary = J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    return w, summary, list(J.iter_journal(w.paths))


def test_fidelity_gate_is_at_least_90_percent_on_the_live_replica(built):
    _w, summary, rows = built
    fd = summary["fidelity"]
    fut = [r for r in rows if r["kind"] == "futures"]
    assert fd == FD.summarize(rows)
    assert fd["eligible"] == len(fut) >= 30 and fd["not_eligible"] == {"SPOT": 2}
    assert fd["rate"] >= 0.9 and fd["gate_pass"] is True and fd["target"] == 0.9
    for r in fut:
        f = r["fidelity"]
        assert f["status"] in ("OK", "FAILED") and f["r_actual"] is not None and f["replay_entry_fill"]
        if f["ok"]:
            assert abs(D(f["delta_r"])) <= D("0.05")
        assert f["bars"] == r["path"]["n_trade_bars"] and f["tf"] == r["path"]["tf"] == "1m"
    by = fd["by_book"]
    assert set(by) == {r["book"] for r in fut} and all(v["rate"] >= 0.9 for v in by.values())


def test_fidelity_failures_are_listed_with_their_reason(tmp_path):
    """Canlı izleyici stopun ÖTESİNDE gözlediği fiyattan doldurur (boşluk dolumu); yeniden oynatma seviyeden doldurur →
    fark > 0,05R, neden `FILL_BASIS` olarak listelenir; kapı oranı düşer."""
    w = World(tmp_path)
    mk = world_markets()
    sol = mk["SOLUSDT"]["1m"]
    led = new_ledger()
    w.v.futs[""] = led
    keys = []
    for k, (hh, clip) in enumerate(((3, False), (9, True))):
        t = ms(utc(2026, 9, 29, hh, 11, 5))
        e = price_at(sol, t)
        stop, tgt = round(e * 0.9985, 2), [round(e * 1.03, 2)]
        pos = led.open("SOL/USDT", "LONG", D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=stop, targets=tgt, now=dt_of(t))
        w.provenance(pos.id, "SOL/USDT", "LONG", stop=stop, targets=tgt, opened_at=pos.opened_at)
        rec = run_trade(led, "SOL/USDT", sol, opened_ms=t, hold_ms=3 * H1, clip_stop=clip)
        keys.append((f"main_fut|{rec.id}|{rec.opened_at}", rec.exit_reason))
    assert [x[1] for x in keys] == ["stop", "stop"]
    w.seal(frames={"SOLUSDT": mk["SOLUSDT"], "BTCUSDT": mk["BTCUSDT"]})
    w.v.night(night_of("2026-10-06"))
    s = J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    rows = {r["trade_key"]: r for r in J.iter_journal(w.paths)}
    bad, good = rows[keys[0][0]], rows[keys[1][0]]
    assert good["fidelity"]["status"] == "OK"
    f = bad["fidelity"]
    assert f["status"] == "FAILED" and abs(D(f["delta_r"])) > D("0.05")
    assert f["primary_reason"] == "FILL_BASIS" and f["replay_exit_basis"] == "STOP_AT_LEVEL"
    assert bad["exit_basis"] == "GAP" and good["exit_basis"] == "LEVEL"
    fd = s["fidelity"]
    assert (fd["eligible"], fd["ok"], fd["rate"], fd["gate_pass"]) == (2, 1, 0.5, False)
    assert fd["failures"] == [{"trade_key": keys[0][0], "book": "main_fut", "delta_r": f["delta_r"], "reasons": f["reasons"],
                               "primary_reason": "FILL_BASIS"}]
    assert fd["failures_by_reason"]["FILL_BASIS"] == 1


def test_not_eligible_trades_are_counted_apart_with_their_reason(built):
    w, _s, rows = built
    r = next(x for x in rows if x["book"] == "strategy_paper_box" and x["exit_reason"] == "hedef1")
    book = r["book"]
    loc = J.scan_archive(w.paths, book)[r["trade_key"]]
    rec = J.load_records(w.paths, book, {r["trade_key"]: loc})[r["trade_key"]]
    bars = PR.read_path(w.paths, r["path"]["file"])
    xp = J._exec_params_by_book(w.paths)[book]
    kw = dict(kind="futures", bars=bars, tf="1m", path_gaps=0, targets=r["targets"], targets_known=True, exec_par=xp)
    assert FD.replay(rec, **kw)["status"] == "OK"
    assert FD.replay(rec, **{**kw, "kind": "spot"})["primary_reason"] == "SPOT"
    assert FD.replay(rec, **{**kw, "bars": None})["primary_reason"] == "NO_PATH"
    assert FD.replay(rec, **{**kw, "exec_par": None})["primary_reason"] == "NO_EXEC_MODEL"
    assert FD.replay(rec, **{**kw, "targets": None, "targets_known": False})["primary_reason"] == "TARGETS_MISSING"
    old = json.loads(json.dumps(rec))
    old["features"].pop("risk_usdt")
    assert FD.replay(old, **kw)["primary_reason"] == "NO_RISK"
    rows2 = [{"book": "b", "trade_key": f"k{i}", "fidelity": {"status": "OK"}} for i in range(9)]
    rows2.append({"book": "b", "trade_key": "kx", "fidelity": {"status": "FAILED", "reasons": ["PATH_GAP", "AMBIGUOUS_BAR"],
                                                                "primary_reason": "PATH_GAP", "delta_r": "0.2"}})
    rows2.append({"book": "b", "trade_key": "ks", "fidelity": {"status": "NOT_ELIGIBLE", "primary_reason": "SPOT"}})
    s = FD.summarize(rows2)
    assert (s["eligible"], s["ok"], s["rate"], s["gate_pass"]) == (10, 9, 0.9, True)
    assert s["failures_by_reason"] == {"AMBIGUOUS_BAR": 1, "PATH_GAP": 1} and s["not_eligible"] == {"SPOT": 1}


def test_recorded_funding_replays_the_ledgers_own_settlements(built):
    _w, _s, rows = built
    t2 = [r for r in rows if r["book"] == "strategy_paper"]
    assert t2 and all(r["funding_complete"] is True and D(r["funding_net"]) != 0 for r in t2), "T2 işlemleri 08:00'ı geçti"
    assert all(r["fidelity"]["ok"] for r in t2)
    rec = {"features": {"funding_settlements": [{"settlement": "2026-09-26T08:00:00+00:00", "rate": "0.0001", "mark": "17000"}],
                        "funding_settled_until": "2026-09-26T16:00:00+00:00"}}
    rf = FD.RecordedFunding(rec)
    assert rf("X", utc(2026, 9, 26, 8)) == D("0.0001") and rf.settlement_mark("X", utc(2026, 9, 26, 8)) == D("17000")
    assert rf("X", utc(2026, 9, 26, 16)) == 0, "watermark'a kadar kayıtta olmayan dönem sıfır oranlı"
    assert rf("X", utc(2026, 9, 27, 0)) is None, "watermark'tan sonrası bilinmez (dönem bekler)"


# ============================================================================ tembel 1m
def test_lazy_plan_filters_and_caps_newest_first():
    now = ms(utc(2026, 10, 6, 0, 41))
    needs = {"schema": LZ.NEEDS_SCHEMA, "series": {
        "futures/SOLUSDT/1m": ["2026-10-01", "2026-10-02", "2026-10-06", "2024-01-01"],
        "futures/XAUUSDT/1m": ["2026-10-01"], "futures/SOLUSDT/5m": ["2026-10-01"], "spot/ETHUSDT/1m": ["2026-09-30"],
        "futures/OLDUSDT/1m": ["2026-10-01"]}}
    plan = LZ.lazy_plan(needs, now_ms=now, plan_keys={"futures/XAUUSDT/1m"}, delisted={"futures/OLDUSDT"})
    assert [(mk, s, [d for d, _a, _b in ds]) for mk, s, ds in plan] == [
        ("futures", "SOLUSDT", ["2026-10-01", "2026-10-02"]), ("spot", "ETHUSDT", ["2026-09-30"])]
    a, b = plan[0][2][0][1:]
    assert b - a == 86_400_000 and a == ms(utc(2026, 10, 1))
    capped = LZ.lazy_plan(needs, now_ms=now, max_days=1)
    assert [(s, [d for d, _a, _b in ds]) for _mk, s, ds in capped] == [("SOLUSDT", ["2026-10-02"])], "en YENİ gün önce"
    assert LZ.lazy_plan({"schema": "x"}, now_ms=now) == [] and LZ.lazy_plan(None, now_ms=now) == []


def test_night_s1b_asks_only_for_days_whose_path_lacks_1m(tmp_path):
    w = World(tmp_path)
    mk = world_markets()
    led = new_ledger()
    w.v.futs[""] = led
    sol = mk["SOLUSDT"]["1m"]
    t = ms(utc(2026, 9, 29, 23, 11, 5))
    e = price_at(sol, t)
    pos = led.open("SOL/USDT", "LONG", D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=round(e * 0.97, 2), now=dt_of(t))
    assert pos is not None
    run_trade(led, "SOL/USDT", sol, opened_ms=t, hold_ms=2 * H1)
    w.seal(frames={"SOLUSDT": {tf: df for tf, df in mk["SOLUSDT"].items() if tf != "1m"}, "BTCUSDT": mk["BTCUSDT"]})
    w.v.night(night_of("2026-10-06"))
    out = J.run_s1b(w.paths, now=NOW, run_id="r1", app_dir=w.app)
    assert out["journal"]["path_sources"] == {"STORE_5M": 1}
    needs = LZ.read_needs(w.paths)
    assert needs["series"] == {"futures/SOLUSDT/1m": ["2026-09-29", "2026-09-30"]} and needs["trades"] == 1
    assert out["needs_1m"] == {"days_total": 2, "capped": 0, "series": 1}


def test_data_unit_fetches_only_the_needed_1m_day_zips_without_rest_and_never_marks_them_stale(tmp_path):
    listing = {"futures/SOLUSDT": ms(utc(2026, 8, 20))}
    e = DataEnv(tmp_path, start=utc(2026, 10, 6, 0, 41), listing=listing, only={"futures/SOLUSDT/1h"})
    needs = PR.plan_needs([{"market": "futures", "symbol": "SOL/USDT", "opened_ms": ms(utc(2026, 9, 29, 23, 11, 5)),
                            "closed_ms": ms(utc(2026, 9, 30, 1, 11))},
                           {"market": "futures", "symbol": "SOL/USDT", "opened_ms": ms(utc(2026, 10, 5, 22)),
                            "closed_ms": ms(utc(2026, 10, 6, 0, 10))}], now=utc(2026, 10, 6, 0, 41))
    assert needs["series"] == {"futures/SOLUSDT/1m": ["2026-09-29", "2026-09-30", "2026-10-05"]}, "bugünün günü istenmez"
    PR.write_needs(e.paths, needs)
    st = e.run("backfill")
    assert st["result"] == DS.R_SUCCESS, st.get("error")
    one_m = [u for u in e.fb.urls("/1m/")]
    want = {f"https://data.binance.vision/data/futures/um/daily/klines/SOLUSDT/1m/SOLUSDT-1m-{d}.zip"
            for d in ("2026-09-29", "2026-09-30", "2026-10-05")}
    assert set(u.removesuffix(".CHECKSUM") for u in one_m) == want, "yalnız istenen günlerin GÜN zip'leri"
    assert not [x for x in e.fb.rest_log() if (x[2] or {}).get("interval") == "1m"], "1m için REST yok"
    d = e.status()
    row = d["series"]["futures/SOLUSDT/1m"]
    assert row["lazy"] is True and row["planned"] is False and row["stale"] is False and row["rows"] == 3 * 1440
    assert d["totals"]["stale"] == 0 and d["last_run"]["lazy_1m"]["days"] == 3
    # yeniden çalıştırma: doğrulanmış gün yeniden indirilmez
    n = len(e.fb.urls("/1m/"))
    st = e.run("update")
    assert st["result"] == DS.R_SUCCESS and len(e.fb.urls("/1m/")) == n
    assert e.status()["last_run"]["lazy_1m"]["already_have"] == 3
    # gece birimi mühürlü depodan 1m yolunu kurar
    src, info = J.open_store(e.paths)
    assert src is not None, info
    rec = {"id": "F1", "symbol": "SOL/USDT", "side": "LONG", "opened_at": iso(utc(2026, 9, 29, 23, 11, 5)),
           "closed_at": iso(utc(2026, 9, 30, 1, 11)), "entry": "100", "quantity": "1", "exit_price": "100"}
    res = PR.reconstruct(rec, trade_key="main_fut|F1|x", src=src, market="futures", risk_usdt=D("1"))
    assert res.source == PR.SRC_1M and res.complete and res.n_tail_bars == 48


def test_datastore_hook_is_additive_and_network_free_in_the_night_graph():
    src = (Path(__file__).resolve().parents[1] / "tradingbot" / "research_engine" / "pathrec.py").read_text(encoding="utf-8")
    assert "import datastore" not in src and "from .datastore" not in src, "gece birimi (pathrec) ağ modülünü import etmez"
    assert "_lazy_1m_pass" in (Path(__file__).resolve().parents[1] / "tradingbot" / "research_engine" / "datastore.py").read_text(
        encoding="utf-8")
