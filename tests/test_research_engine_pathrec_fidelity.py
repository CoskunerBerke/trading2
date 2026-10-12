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
    """Canlı izleyici stopu seviyenin ÖTESİNDEKİ ilk 60 sn örnekten doldurur; yeniden oynatma bunu kural dilimi penceresinde
    modeller (`RECORDED_FILL`, 2026-10-08 inceleme B1) — örnekleme aşması artık FILL_BASIS değildir. Örneklerin kaçırdığı
    ESKİ bir fitil (pencere dışında) gerçek bir farktır: yeniden oynatma o barda seviyeden durur, fark > 0,05R, neden
    `FILL_BASIS` olarak listelenir; kapı oranı düşer."""
    from test_research_engine_p2_fixes import _b1_world
    _w, s, rows = _b1_world(tmp_path)
    for k in ("A", "B", "C"):
        assert rows[k]["fidelity"]["status"] == "OK" and rows[k]["fidelity"]["fill_model"] == "RECORDED_FILL", k
    bad = rows["D"]
    f = bad["fidelity"]
    assert f["status"] == "FAILED" and abs(D(f["delta_r"])) > D("0.05")
    assert f["primary_reason"] == "FILL_BASIS" and f["replay_exit_basis"] == "STOP_AT_LEVEL"
    assert bad["exit_basis"] == "LEVEL" and rows["B"]["exit_basis"] == "GAP"
    fd = s["fidelity"]
    assert (fd["eligible"], fd["ok"], fd["rate"], fd["gate_pass"]) == (4, 3, 0.75, False)
    assert fd["failures"] == [{"trade_key": bad["trade_key"], "book": "main_fut", "delta_r": f["delta_r"], "reasons": f["reasons"],
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


# ============================================================================ fidelity_v3: kural/zaman çıkışı (2026-10-12)
#: Box gün sonu düzleşmesinin CANLI zamanlaması: gün değiştikten sonraki ilk 5m değerlendirmesi (00:00 barı kapanınca,
#: + 3 sn yayım payı + yoklama) — karar 00:05:07'de, o anın perp MARK'ından `close_manual`.
EOD_DAY = utc(2026, 9, 29)
EOD_OPEN = EOD_DAY - timedelta(hours=1, minutes=25) + timedelta(seconds=4)       # 22:35:04 (5m kapanışı + 4 sn)
EOD_CLOSE = EOD_DAY + timedelta(minutes=5, seconds=7)                              # 00:05:07
RISE = [(0, .05, -.01, .04), (.04, .10, .03, .08), (.08, .14, .07, .12), (.12, .18, .11, .16), (.16, .22, .15, .20)]
FALL = [(.10, .12, -.10, -.05), (-.05, -.03, -.20, -.15), (-.15, -.12, -.30, -.25), (-.25, -.22, -.40, -.35)]
#: (a) çıkıştan SONRAKİ barda (00:05, çıkışı içeren bar) stopun ötesinde bir fitil, sonra düşüş: v2 bunu stop sayıyordu
POST_EXIT_WICK = RISE + [(.20, .24, -.70, .10)] + FALL
#: (b) fitilsiz, çıkıştan sonra düşüş: v2 çıkış barının kapanışından (5m yolda 00:10'daki son işlem fiyatı) kapatıyordu
POST_EXIT_FALL = RISE + [(.20, .24, .05, .10)] + FALL
#: (c) karardan ÖNCE (00:02) 60 sn örneklerin kaçırdığı son işlem fitili: gerçek bir fark (yeniden oynatma stopta çıkar)
PRE_EXIT_WICK = [(0, .05, -.01, .04), (.04, .10, .03, .08), (.08, .12, -.65, .10), (.10, .16, .09, .14), (.14, .20, .13, .18),
                 (.18, .22, .15, .20)] + FALL


def _eod_trade(tmp_path, design, *, opened=EOD_OPEN, closed=EOD_CLOSE, with_1m: bool = True):
    """Canlı Box akışının kaydı: 60 sn örnekli izleme (`walk_sampled`), karar anında mark'tan `BOX_EOD_FLAT`; yol
    `pathrec.reconstruct` ile (1m yoksa 5m)."""
    from research_engine_p2_fixtures import walk_sampled
    from test_research_engine_p2_fixes import _designed_m1
    m1 = _designed_m1({ms(EOD_DAY): design}, start=ms(utc(2026, 9, 27)), end=ms(utc(2026, 9, 30)))
    led = new_ledger()
    e = price_at(m1, ms(opened))
    stop, tgt = round(e - 0.6, 2), [round(e + 3.0, 2)]
    assert led.open("SOL/USDT", "LONG", D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=stop, targets=tgt,
                    now=opened) is not None
    assert walk_sampled(led, "SOL/USDT", m1, opened_ms=ms(opened), until_ms=ms(closed) // M1 * M1 - M1) is None
    row = m1[m1["timestamp"] == ms(closed) // M1 * M1].iloc[0]
    mark = round(float(row["open"]) + 0.01, 2)                 # karar anının mark'ı: bir bar kapanışı DEĞİL
    rec = led.close_manual("SOL/USDT", D(str(mark)), reason="BOX_EOD_FLAT", now=closed).to_dict()
    paths = EnginePaths.for_state(tmp_path / "data" / "state")
    sf = StoreFixture(paths, now=utc(2026, 10, 6))
    if with_1m:
        sf.add("futures", "SOLUSDT", "1m", m1)
    sf.add("futures", "SOLUSDT", "5m", aggregate(m1, M5))
    sf.seal()
    path = PR.reconstruct(rec, trade_key="strategy_paper_box|x|y", src=sf.source(), market="futures",
                          risk_usdt=D(rec["features"]["risk_usdt"]))
    return rec, path, tgt, FD.exec_params(led.to_dict()), mark


def _fd(rec, path, tgt, xp):
    from tradingbot.research_engine import rehydrate as RH
    return FD.replay(rec, kind="futures", bars=path.bars, tf=path.tf, path_gaps=path.gaps, targets=tgt, targets_known=True,
                     exec_par=xp, tick=RH.tick_of(rec), rule_tf_ms=M5)


def _without_exit_ref(rec):
    """Kapanış dolumunun gözlem referansı olmayan (eski) kayıt: v2 penceresiyle oynatılır (karşılaştırma için)."""
    old = json.loads(json.dumps(rec))
    for f in old["fills"]:
        if f.get("kind") == "BOX_EOD_FLAT":
            f["ref_price"] = None
    return old


def test_rule_exit_replays_to_the_decision_and_closes_at_the_recorded_mark(tmp_path):
    """VPS 2026-10-12 (P2 ilk tam gece): Box `BOX_EOD_FLAT` 151 `UNEXPLAINED`. Kök neden: v2 çıkışı İÇEREN barı da oynatıp
    ufukta o barın kapanışından (1m: ~1 dk, 5m: ~5 dk SONRA; son işlem fiyatı, mark değil) kapatıyordu ve o barda çıkıştan
    sonra gelen stopu çıkış sayıyordu (`EXIT_REASON_DIFFERS`). v3: yalnız karardan önce kapanmış barlar + kaydın gözlem
    referansı (kayma modeliyle) → fark ≈ 0."""
    for with_1m, name, design in ((w, n, d) for w in (True, False) for n, d in (("wick", POST_EXIT_WICK), ("fall", POST_EXIT_FALL))):
        rec, path, tgt, xp, mark = _eod_trade(tmp_path / f"{name}{int(with_1m)}", design, with_1m=with_1m)
        step = M1 if with_1m else M5
        assert path.tf == ("1m" if with_1m else "5m") and path.complete
        assert int(path.bars[path.bars["phase"] == 0]["timestamp"].max()) == ms(EOD_DAY) + 5 * M1, \
            "pathrec penceresi çıkışı içeren barı (00:05) taşır"
        f = _fd(rec, path, tgt, xp)
        assert f["version"] == FD.FIDELITY_VERSION == "fidelity_v3"
        assert f["status"] == "OK" and abs(D(f["delta_r"])) < D("0.005"), (name, f)
        assert (f["fill_model"], f["replay_exit_reason"], f["replay_exit_basis"]) == ("RECORDED_EXIT", "BOX_EOD_FLAT",
                                                                                       "RECORDED_EXIT")
        assert f["recorded_exit"] == format(D(str(mark)), "f") and f["window"] == "TO_EXIT_DECISION"
        # karar penceresi: son bar 00:04 (1m) / 00:00 (5m) — çıkıştan sonra kapanan bar okunmaz
        assert f["bars"] == (ms(EOD_DAY) + 5 * M1 - (ms(EOD_OPEN) // step + 1) * step) // step
        # aynı işlem v2 tabanıyla (çıkış barı dahil, bar kapanışından): tolerans dışı
        v2 = _fd(_without_exit_ref(rec), path, tgt, xp)
        assert v2["window"] == "THROUGH_EXIT_BAR" and v2["status"] == "FAILED" and abs(D(v2["delta_r"])) > D("0.05"), v2
        if name == "wick":
            assert v2["replay_exit_reason"] == "stop" and v2["primary_reason"] == "EXIT_REASON_DIFFERS"
        else:
            assert v2["replay_exit_reason"] == "horizon" and v2["primary_reason"] == "UNEXPLAINED"


def test_rule_exit_still_fails_when_the_path_stops_before_the_decision(tmp_path):
    """Karardan ÖNCE yolun stopu (60 sn örneklerin kaçırdığı son işlem fitili) gerçek bir farktır: yeniden oynatma o barda
    seviyeden çıkar, neden `EXIT_REASON_DIFFERS`; tolerans (0,05R) değişmedi."""
    rec, path, tgt, xp, _mark = _eod_trade(tmp_path, PRE_EXIT_WICK)
    assert rec["exit_reason"] == "BOX_EOD_FLAT", "canlı örnekler fitili görmedi"
    f = _fd(rec, path, tgt, xp)
    assert f["status"] == "FAILED" and abs(D(f["delta_r"])) > D("0.05") and f["window"] == "TO_EXIT_DECISION"
    assert (f["replay_exit_reason"], f["fill_model"], f["primary_reason"]) == ("stop", "LEVEL", "EXIT_REASON_DIFFERS")
    assert FD.TOLERANCE_R == D("0.05")


def test_rule_exit_without_a_bar_closed_before_the_decision_is_not_eligible(tmp_path):
    """Gece yarısından sonra dolan 23:55 sinyali (00:01:04) 00:05:07'de düzleşir: 5m yolda karardan önce kapanmış işlem barı
    yoktur (00:05 barı tamamen çıkıştan sonradır) → `NO_PRE_EXIT_BAR` (uygun değil, oranın paydasına girmez); 1m yolda
    00:02–00:04 barları vardır → oynatılır."""
    opened = EOD_DAY + timedelta(minutes=1, seconds=4)
    rec, path, tgt, xp, _m = _eod_trade(tmp_path / "5m", POST_EXIT_FALL, opened=opened, with_1m=False)
    f = _fd(rec, path, tgt, xp)
    assert (f["status"], f["primary_reason"]) == ("NOT_ELIGIBLE", "NO_PRE_EXIT_BAR") and "NO_PRE_EXIT_BAR" in FD.NOT_ELIGIBLE
    assert FD.summarize([{"book": "b", "trade_key": "k", "fidelity": f}])["not_eligible"] == {"NO_PRE_EXIT_BAR": 1}
    rec, path, tgt, xp, _m = _eod_trade(tmp_path / "1m", POST_EXIT_FALL, opened=opened)
    f = _fd(rec, path, tgt, xp)
    assert f["status"] == "OK" and f["bars"] == 3 and f["fill_model"] == "RECORDED_EXIT"


def test_level_exits_keep_the_exit_bar_and_the_recorded_exit_reads_the_closing_fill():
    assert all(FD.is_level_exit(r) for r in ("stop", "başa-baş stop", "hedef1", "hedef2", "hedef3", "likidasyon"))
    assert not any(FD.is_level_exit(r) for r in ("BOX_EOD_FLAT", "DONCHIAN_EXIT_LOW10", "ENTRY_STRUCTURE_FAILED", "horizon",
                                                 "BOX_OUTSIDE_BREAKOUT_EXIT", "manuel"))
    base = {"closed_at": iso(EOD_CLOSE),
            "fills": [{"kind": "entry", "ref_price": "100", "price": "100.03"},
                      {"kind": "hedef1", "ref_price": "101", "price": "101"},
                      {"kind": "BOX_EOD_FLAT", "ref_price": "100.21", "price": "100.18"}]}
    ref, at = FD.recorded_exit({**base, "exit_reason": "BOX_EOD_FLAT"})
    assert ref == D("100.21") and at == EOD_CLOSE, "TP1 sonrası kural çıkışı: kapanış dolumunun referansı"
    assert FD.recorded_exit({**base, "exit_reason": "stop"}) == (None, None)
    assert FD.recorded_exit({**base, "exit_reason": "DONCHIAN_EXIT_LOW10"}) == (None, None), "o nedenli dolum yok"


def test_journal_rows_rebuild_when_the_fidelity_version_changes(monkeypatch):
    """Fidelity semantiği değişince (`FIDELITY_VERSION`) günlük satırının girdi özeti değişir: VPS'teki satırlar yeniden
    kurulur (S1b), atıf satırları da (satır sha'sı) onu izler."""
    loc = J.ArchiveLoc(book="b", key="k", rev=0, status="closed", sha="a" * 64, seg="s", line=0, proj={}, position_group="g")
    a = J._input_digest(loc, {}, "s", "c", "e", "q")
    assert J._input_digest(loc, {}, "s", "c", "e", "q") == a
    monkeypatch.setattr(FD, "FIDELITY_VERSION", "fidelity_test")
    assert J._input_digest(loc, {}, "s", "c", "e", "q") != a


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
