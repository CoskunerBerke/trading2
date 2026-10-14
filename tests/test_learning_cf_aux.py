# -*- coding: utf-8 -*-
"""KARŞI-OLGUSAL YARDIMCI ETİKETLER — `cf_aux_v1` (2026-10-01, Box karşı-olgusal / gerçek farkı incelemesi). YALNIZ KAYIT.

İncelemenin doğruladığı iki etiketleyici iyimserliği (seviyeden stop dolumu; hiç yürünmeyen giriş barı) `r_net`e
UYGULANMAZ, `outcome["aux"]` altında ölçülür. Testler:
1. Alan birim testleri: giriş barı fitili (B1 yeniden üretimi: karşı-olgusal +6,80R / canlı −1,40R; LONG ve SHORT),
   aşma tahmini (temel, işaret, pencere, en az n, kaynak süzgeci), örneklenmiş tahmin, null kuralları, önbellek.
2. Karar özdeşliği: Box defteri çok geçişli senaryo ve ana bot etiket geçişi — `aux` AÇIK ve KAPALI (monkeypatch) koşularda
   defterler, karar günlükleri, karşı-olgusal dosyası (`aux` hariç), araştırma politikası girdileri ve ortak deneyim
   satırları birebir aynı.
3. Geriye uyumluluk: değişiklik öncesi `counterfactual_trades.json` yüklenir, etiketlenir, arşivlenir ve raporlanır.
4. Karne: net ortalama AYNEN, yanında ihtiyatlı ortalama ve kapsamı. Bellek: kayıt başına ek yük ölçülür ve sınırlıdır.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

import tradingbot.learning_cf_aux as A  # noqa: E402
from tradingbot.accounting import (AmountType, FuturesLedgerV2, SizeSpec, TickData,  # noqa: E402
                                   default_filters)
from tradingbot.learn.shadow import ShadowBook, ShadowTrade  # noqa: E402
from tradingbot.learning_cf import (CounterfactualRecorder, ExecModel, label_records, net_stats,  # noqa: E402
                                    outcome_r, relabel_net)

UTC = timezone.utc
M5, H4 = 300_000, 14_400_000
SYM = "BTC/USDT"
AUX_KEYS = {"aux_version", "entry_bar", "r_net_entry_bar", "overshoot_pct_est", "overshoot_n", "r_net_sampled_est",
            "r_net_conservative"}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _frame(bars, *, first_ts: int, tf_ms: int = M5) -> pd.DataFrame:
    return pd.DataFrame([{"timestamp": first_ts + i * tf_ms, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
                         for i, (o, h, lo, c) in enumerate(bars)])


def _led(**kw) -> FuturesLedgerV2:
    return FuturesLedgerV2(Decimal("10000"), **kw)


def _real_stops(led: FuturesLedgerV2, n: int, *, side: str = "SHORT", entry: str = "100", stop: str = "100.5",
                first: str = "100.6", t0: datetime | None = None, symbol: str = "R/USDT") -> None:
    """Gerçek defterde `n` ilk-örnek stop çıkışı (60 sn fiyat-yalnız tick, `GAP_FILL_AT_FIRST_OBSERVATION` / PRICE)."""
    t = t0 or datetime(2026, 9, 29, 0, 0, tzinfo=UTC)
    for _ in range(n):
        assert led.open(symbol, side, Decimal(entry), SizeSpec(Decimal("1000"), AmountType.NOTIONAL, 1), stop=Decimal(stop),
                        targets=[], now=t) is not None, led.last_reject_reason
        t += timedelta(seconds=60)
        recs = led.tick({symbol: TickData(last=Decimal(first), mark=Decimal(first), ts=t.isoformat())}, now_utc=t,
                        bar_advance=False)
        assert recs and recs[-1].exit_reason == "stop"
        assert recs[-1].features["exit_fill"]["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION"
        t += timedelta(seconds=60)


def _record(cf: CounterfactualRecorder, *, side: str, entry: float, stop: float, targets, created: datetime, horizon: int,
            tf: int = 5, key: str = "k") -> ShadowTrade:
    assert cf.record(signal_key=key, symbol=SYM, direction=side, entry=entry, stop=stop, targets=list(targets),
                     reason="INSUFFICIENT_MARGIN", created_at=created, tf_minutes=tf, horizon_bars=horizon,
                     label_kind="TARGET_STOP_TIME")
    return cf.sb.trades[-1]


# ============================================================================ 1) B1 yeniden üretimi (SHORT)
def _b1():
    """gapinv/challenge2/repro_b1.py: sinyal barı B0 10:00, Box zamanlayıcısı 10:05:10'da kaydeder; giriş barı B1 (10:05)
    stopun (100,5) ötesine 100,9'a fitil atar; sonraki barlar düşer. Etiketleyici B1'i yürümez → karşı-olgusal +6,80R."""
    b0 = _ms(datetime(2026, 9, 30, 10, 0, tzinfo=UTC))
    created = datetime.fromtimestamp((b0 + M5 + 10_000) / 1000, tz=UTC)
    bars = [(99.8, 100.1, 99.7, 100.0), (100.0, 100.9, 99.9, 100.2)]
    px = 100.2
    for _k in range(2, 40):
        o = px
        px = px - 0.1
        bars.append((o, o + 0.05, px - 0.02, px))
    return created, _frame(bars, first_ts=b0)


def _b1_live(created: datetime, led: FuturesLedgerV2) -> float:
    """Canlı defter: aynı sinyal girişte açılır; 60 sn mark örnekleri B1 içinde 100,6'yı görür → ilk örnekten stop."""
    p = led.open(SYM, "SHORT", Decimal("100"), SizeSpec(Decimal("1000"), AmountType.NOTIONAL, 1), stop=Decimal("100.5"),
                 targets=[Decimal("95")], filters=default_filters(SYM), now=created)
    assert p is not None
    for i, m in enumerate(["100.1", "100.6", "100.3", "100.2"]):
        t = created + timedelta(seconds=60 * (i + 1))
        recs = led.tick({SYM: TickData(last=Decimal(m), mark=Decimal(m), ts=t.isoformat())}, now_utc=t, bar_advance=False)
        if recs:
            assert recs[-1].exit_reason == "stop"
            return float(recs[-1].r_multiple)
    raise AssertionError("canlı defter stop olmadı")


def test_b1_entry_bar_wick_is_flagged_and_the_conservative_r_matches_the_live_ledger(tmp_path):
    created, df = _b1()
    live = _led()
    _real_stops(live, 10)                                    # aynı defterin önceki ilk-örnek stopları (aşma ≈ %0,10)
    live_r = _b1_live(created, live)
    assert live_r == pytest.approx(-1.40, abs=0.01)
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    t = _record(cf, side="SHORT", entry=100.0, stop=100.5, targets=[95.0], created=created, horizon=37)
    n = cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=5 * 40), exec_model=ExecModel.of_ledger(_led()),
                         real_history=lambda: live.history)
    assert n == 1
    o = t.outcome
    assert o["r_net"] == pytest.approx(6.80, abs=0.01), "karşı-olgusal AYNEN (r_net değişmez)"
    ax = o["aux"]
    assert set(ax) == AUX_KEYS and ax["aux_version"] == "cf_aux_v1"
    assert ax["entry_bar"] == {"checked": True, "stop_hit": True, "adverse": 100.9}
    assert ax["r_net_entry_bar"] == pytest.approx(-1.20, abs=0.01), "giriş barında seviyeden stop (aynı net yol)"
    assert ax["overshoot_n"] == 11 and ax["overshoot_pct_est"] == pytest.approx(0.1, abs=0.001)
    assert ax["r_net_sampled_est"] == o["r_net"], "zaman çıkışı: stop dolumu düzeltmesi yok"
    assert ax["r_net_conservative"] == pytest.approx(live_r, abs=0.03), (ax, live_r)
    assert ax["r_net_conservative"] < 0 < o["r_net"], "bayrak: işaret değişiyor"


# ============================================================================ 2) LONG ayna, bayraksız giriş barı, çerçeve dışı
def _long_case(tmp_path, entry_bar, *, include_entry_bar: bool = True, history=None, key="L"):
    t0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    created = t0 + timedelta(seconds=10)
    later = [(100.0, 100.6, 99.8, 100.5), (100.5, 102.2, 100.4, 102.0)]              # hedef 102
    bars = ([entry_bar] if include_entry_bar else []) + later
    df = _frame(bars, first_ts=_ms(t0) + (0 if include_entry_bar else M5))
    cf = CounterfactualRecorder(tmp_path / f"{key}.json", book="b1_box_fade")
    t = _record(cf, side="LONG", entry=100.0, stop=99.5, targets=[102.0], created=created, horizon=10, key=key)
    assert cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=60), exec_model=ExecModel.of_ledger(_led()),
                            real_history=history) == 1
    return t.outcome


def test_long_entry_bar_wick_below_the_stop_is_flagged(tmp_path):
    o = _long_case(tmp_path, (100.0, 100.3, 99.1, 100.0))
    assert o["exit_reason"] == "target" and o["r_net"] > 3.5
    ax = o["aux"]
    assert ax["entry_bar"] == {"checked": True, "stop_hit": True, "adverse": 99.1}
    assert ax["r_net_entry_bar"] == pytest.approx(-1.2, abs=0.05)
    assert ax["overshoot_pct_est"] is None and ax["overshoot_n"] == 0
    assert ax["r_net_sampled_est"] == o["r_net"] and ax["r_net_conservative"] is None, "aşma bilinmiyor → null"


def test_entry_bar_inside_the_stop_keeps_r_net_and_no_history_means_null_overshoot(tmp_path):
    o = _long_case(tmp_path, (100.0, 100.3, 99.6, 100.0))
    ax = o["aux"]
    assert ax["entry_bar"] == {"checked": True, "stop_hit": False, "adverse": 99.6}
    assert ax["r_net_entry_bar"] == o["r_net"] == ax["r_net_sampled_est"] == ax["r_net_conservative"]


def test_entry_bar_missing_from_the_frame_is_not_fetched_and_reports_unchecked(tmp_path):
    o = _long_case(tmp_path, None, include_entry_bar=False)
    ax = o["aux"]
    assert ax["entry_bar"] == {"checked": False, "stop_hit": None, "adverse": None, "why": "ENTRY_BAR_NOT_IN_FRAME"}
    assert ax["r_net_entry_bar"] is None and ax["r_net_conservative"] is None
    assert ax["r_net_sampled_est"] == o["r_net"]


def test_entry_at_the_bar_open_is_still_the_entry_bar():
    t = SimpleNamespace(tf_minutes=5, created_at="2026-09-30T12:00:00+00:00", direction="SHORT", stop=101.0)
    ts = _ms(datetime(2026, 9, 30, 12, 0, tzinfo=UTC))
    import numpy as np
    arr = (np.array([ts - M5, ts], dtype="int64"), np.array([100.5, 101.2]), np.array([99.0, 99.5]))
    assert A.entry_bar_check(t, arr) == {"checked": True, "stop_hit": True, "adverse": 101.2, "bar_close_ms": ts + M5}


# ============================================================================ 3) örneklenmiş stop dolumu tahmini
def _stop_case(tmp_path, *, side: str, history, gap: bool = False, key: str = "S"):
    """0,32% stoplu kayıt; giriş barı ve ilk yol barı stopa değmez, ikinci yol barı stopu deler (ya da stopun ötesinde
    AÇILIR: gerçek boşluk — yalnız ilk OLMAYAN yol barında)."""
    t0 = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)
    created = t0 + timedelta(seconds=10)
    sg = 1 if side == "LONG" else -1
    e = 100.0
    st = e * (1 - sg * 0.0032)
    eb = (e, e + 0.05, e - 0.05, e)
    nxt_open = (st - sg * 0.2) if gap else e
    nxt = (nxt_open, max(nxt_open, st) + 0.3, min(nxt_open, st) - 0.3, st - sg * 0.1)
    df = _frame([eb, eb, nxt], first_ts=_ms(t0))
    cf = CounterfactualRecorder(tmp_path / f"{key}.json", book="b1_box_fade")
    t = _record(cf, side=side, entry=e, stop=st, targets=[e + sg * 3.0], created=created, horizon=10, key=key)
    assert cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=60), exec_model=ExecModel.of_ledger(_led()),
                            real_history=history) == 1
    return t.outcome


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_cf_stop_at_the_level_is_charged_the_books_median_overshoot(tmp_path, side):
    real = _led()
    if side == "LONG":
        _real_stops(real, 12, side="LONG", entry="100", stop="99", first="98.95")        # aşma ≈ %0,05
    else:
        _real_stops(real, 12, side="SHORT", entry="100", stop="101", first="101.05")
    o = _stop_case(tmp_path, side=side, history=lambda: real.history)
    assert o["net_exit_reason"] == "stop" and o["net_exit_basis"] == "STOP_AT_LEVEL"
    ax = o["aux"]
    assert ax["overshoot_n"] == 12 and ax["overshoot_pct_est"] == pytest.approx(0.05, abs=1e-3)
    stop_pct = abs(o["entry_fill"] - (100.0 * (1 - (1 if side == "LONG" else -1) * 0.0032))) / o["entry_fill"] * 100
    assert ax["r_net_sampled_est"] == pytest.approx(o["r_net"] - ax["overshoot_pct_est"] / stop_pct, abs=1e-6)
    assert ax["r_net_sampled_est"] < o["r_net"] and ax["entry_bar"]["stop_hit"] is False
    assert ax["r_net_conservative"] == ax["r_net_sampled_est"]


def test_a_cf_gap_fill_at_the_bar_open_is_not_charged_again(tmp_path):
    real = _led()
    _real_stops(real, 12, side="LONG", entry="100", stop="99", first="98.95")
    o = _stop_case(tmp_path, side="LONG", history=lambda: real.history, gap=True)
    assert o["net_exit_basis"] == "GAP_FILL_AT_BAR_OPEN"
    assert o["aux"]["r_net_sampled_est"] == o["r_net"] == o["aux"]["r_net_conservative"]


def test_a_cf_stop_exit_without_an_overshoot_estimate_is_null_not_r_net(tmp_path):
    real = _led()
    _real_stops(real, 9, side="LONG", entry="100", stop="99", first="98.95")              # < 10 gözlem
    o = _stop_case(tmp_path, side="LONG", history=lambda: real.history)
    ax = o["aux"]
    assert ax["overshoot_n"] == 9 and ax["overshoot_pct_est"] is None
    assert ax["r_net_sampled_est"] is None and ax["r_net_conservative"] is None


def test_break_even_stop_is_adjusted_only_when_the_full_quantity_was_open(tmp_path):
    """Ana defter (TP1 %50, MFE başa-baş 1R): iki hedefte başa-baş stopta kalan pay bilinmez → null; tek hedefte (TP1
    olamaz) başa-baş yalnız MFE başa-başıdır, miktar tam → düzeltilir."""
    from test_learning_cf_net import _ledger
    real = _led()
    _real_stops(real, 12, side="LONG", entry="100", stop="99", first="98.95")
    main = ExecModel.of_ledger(_ledger(be="1.0"))
    t0 = datetime(2026, 9, 30, 16, 0, tzinfo=UTC)
    created = t0 + timedelta(seconds=10)
    bars = [(100.0, 100.1, 99.9, 100.0), (100.0, 102.4, 99.9, 101.5), (101.5, 101.6, 99.0, 99.2)] + \
        [(99.2, 99.6, 98.9, 99.2)] * 5                                       # brüt: ufuk; net: MFE başa-baş → stop
    out = {}
    for tg in ((103.0, 106.0), (106.0,)):
        cf = CounterfactualRecorder(tmp_path / f"be{len(tg)}.json", book="main")
        t = _record(cf, side="LONG", entry=100.0, stop=98.0, targets=tg, created=created, horizon=6, tf=240)
        assert cf.label_pending({SYM: {"4h": _frame(bars, first_ts=_ms(t0), tf_ms=H4)}}, created + timedelta(hours=33),
                                exec_model=main, real_history=lambda: real.history) == 1
        out[len(tg)] = t.outcome
    for o in out.values():
        assert o["net_exit_reason"] == "başa-baş stop" and o["net_exit_basis"] == "STOP_AT_LEVEL", o
    assert out[2]["aux"]["r_net_sampled_est"] is None and out[2]["aux"]["r_net_conservative"] is None
    a1 = out[1]["aux"]
    assert a1["r_net_sampled_est"] < out[1]["r_net"] and a1["r_net_conservative"] == a1["r_net_sampled_est"]


# ============================================================================ 4) aşma tahmini
def _rec(side, stop, first, entry=100.0, *, reason="stop", basis="GAP_FILL_AT_FIRST_OBSERVATION", source="PRICE"):
    return SimpleNamespace(exit_reason=reason, side=side, entry=Decimal(str(entry)),
                           features={"exit_fill": {"basis": basis, "first_source": source, "first_price": str(first),
                                                   "stop": str(stop)}})


def test_overshoot_estimate_median_sign_window_min_n_and_filters():
    longs = [_rec("LONG", 99.0, 99.0 - k * 0.01) for k in range(1, 6)]            # %0,01 … %0,05
    shorts = [_rec("SHORT", 101.0, 101.0 + k * 0.01) for k in range(6, 11)]         # %0,06 … %0,10
    noise = [_rec("LONG", 99.0, 98.0, source="BAR_OPEN"), _rec("LONG", 99.0, 98.0, basis="STOP_AT_LEVEL"),
             _rec("LONG", 99.0, 98.0, reason="hedef1"), SimpleNamespace(exit_reason="stop", side="LONG", features={})]
    pct, n = A.overshoot_estimate(longs + noise + shorts)
    assert n == 10 and pct == pytest.approx(0.055, abs=1e-9), "medyan; SHORT da pozitif; süzgeç dışı satırlar yok"
    assert A.overshoot_estimate(longs + shorts[:4]) == (None, 9), "en az 10 gözlem"
    old = [_rec("LONG", 99.0, 98.0)] * 300                                          # %1 (eski)
    recent = [_rec("LONG", 99.0, 98.98)] * 200                                      # %0,02 (son 200)
    assert A.overshoot_estimate(old + recent) == (pytest.approx(0.02, abs=1e-9), 200)
    be = _rec("LONG", 100.1, 100.05, reason="başa-baş stop")
    assert A.overshoot_estimate([be] * 10) == (pytest.approx(0.05, abs=1e-9), 10)


def test_overshoot_is_computed_once_per_pass_cached_across_passes_and_only_when_something_is_labelled(tmp_path, monkeypatch):
    calls = []
    real_fn = A.overshoot_estimate
    monkeypatch.setattr(A, "overshoot_estimate", lambda h, **kw: calls.append(len(list(h))) or real_fn(h, **kw))
    real = _led()
    _real_stops(real, 10, side="LONG", entry="100", stop="99", first="98.95")
    hist_reads = []

    def history():
        hist_reads.append(1)
        return real.history
    t0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    created = t0 + timedelta(seconds=10)
    cf = CounterfactualRecorder(tmp_path / "c.json", book="b1_box_fade")
    for k in range(5):
        _record(cf, side="LONG", entry=100.0, stop=99.5, targets=[102.0], created=created, horizon=10, key="k%d" % k)
    bars = [(100.0, 100.3, 99.6, 100.0), (100.0, 100.6, 99.8, 100.5)]
    xm = ExecModel.of_ledger(_led())
    # 1) hiçbir kayıt kesinleşmiyor → tahmin hiç hesaplanmaz (geçmiş bile okunmaz)
    assert cf.label_pending({SYM: {"5m": _frame(bars, first_ts=_ms(t0))}}, created + timedelta(minutes=12),
                            exec_model=xm, real_history=history) == 0
    assert calls == [] and hist_reads == []
    # 2) beş kayıt bir geçişte kesinleşir → BİR hesap
    bars.append((100.5, 102.2, 100.4, 102.0))
    assert cf.label_pending({SYM: {"5m": _frame(bars, first_ts=_ms(t0))}}, created + timedelta(minutes=20),
                            exec_model=xm, real_history=history) == 5
    assert calls == [10] and {t.outcome["aux"]["overshoot_n"] for t in cf.sb.trades} == {10}
    # 3) sonraki geçiş, geçmiş aynı → önbellekten; geçmiş büyüdü → yeniden
    _record(cf, side="LONG", entry=100.0, stop=99.5, targets=[102.0], created=created, horizon=10, key="k5")
    assert cf.label_pending({SYM: {"5m": _frame(bars, first_ts=_ms(t0))}}, created + timedelta(minutes=20),
                            exec_model=xm, real_history=history) == 1
    assert calls == [10]
    _real_stops(real, 1, side="LONG", entry="100", stop="99", first="98.95", t0=datetime(2026, 9, 29, 6, 0, tzinfo=UTC))
    _record(cf, side="LONG", entry=100.0, stop=99.5, targets=[102.0], created=created, horizon=10, key="k6")
    assert cf.label_pending({SYM: {"5m": _frame(bars, first_ts=_ms(t0))}}, created + timedelta(minutes=20),
                            exec_model=xm, real_history=history) == 1
    assert calls == [10, 11]


# ============================================================================ 5) mevcut alanlar değişmez, aux yalnız yeni net etikette
def _random_outcomes(tmp_path, *, enabled: bool, monkeypatch, seed: int = 7):
    from test_learning_cf_net import _ledger
    monkeypatch.setattr(A, "ENABLED", enabled)
    rnd = random.Random(seed)
    real = _led()
    _real_stops(real, 12, side="LONG", entry="100", stop="99", first="98.9")
    out = []
    for model_name, model, tf in (("box", ExecModel.of_ledger(_ledger()), 5), ("main", ExecModel.of_ledger(_ledger(be="1.0")), 240)):
        tf_ms = tf * 60_000
        t0 = datetime(2026, 9, 30, 0, 0, tzinfo=UTC)
        for i in range(40):
            side = rnd.choice(["LONG", "SHORT"])
            sg = 1 if side == "LONG" else -1
            e = 100.0
            st = e * (1 - sg * rnd.choice([0.0032, 0.006, 0.015]))
            r = abs(e - st)
            tg = [e + sg * 1.5 * r, e + sg * 3 * r] if model_name == "main" else [e + sg * 2.5 * r]
            px, bars = e, []
            for _k in range(14):
                o = px
                c = px * (1 + rnd.gauss(0, 0.004))
                bars.append((o, max(o, c) * (1 + abs(rnd.gauss(0, 0.002))), min(o, c) * (1 - abs(rnd.gauss(0, 0.002))), c))
                px = c
            created = t0 + timedelta(milliseconds=tf_ms * 3 + 7_000)
            cf = CounterfactualRecorder(tmp_path / f"{model_name}{i}{enabled}.json", book="x")
            t = _record(cf, side=side, entry=e, stop=st, targets=tg, created=created, horizon=10, tf=tf, key="r%d" % i)
            cf.label_pending({SYM: {{5: "5m", 240: "4h"}[tf]: _frame(bars, first_ts=_ms(t0), tf_ms=tf_ms)}},
                             t0 + timedelta(milliseconds=tf_ms * 15), exec_model=model, real_history=lambda: real.history)
            out.append(t.outcome)
    return out


def test_every_existing_field_is_identical_with_aux_on_and_off(tmp_path, monkeypatch):
    on = _random_outcomes(tmp_path / "on", enabled=True, monkeypatch=monkeypatch)
    off = _random_outcomes(tmp_path / "off", enabled=False, monkeypatch=monkeypatch)
    assert len(on) == len(off) == 80 and all(o is not None for o in on)
    for a, b in zip(on, off):
        assert "aux" not in b and set(a) - set(b) == {"aux"}
        assert {k: v for k, v in a.items() if k != "aux"} == b
        assert outcome_r(a) == outcome_r(b), "araştırma eşleşmesinin okuduğu R aynı"
    hits = [o for o in on if o["aux"]["entry_bar"]["stop_hit"]]
    stops = [o for o in on if o["aux"]["r_net_sampled_est"] not in (None, o["r_net"])]
    assert hits and stops, "senaryo her iki düzeltmeyi de içeriyor"
    assert net_stats(on) == net_stats(off), "kayıtçı özeti (defter özet dosyası) aynı"


def test_no_aux_without_exec_model_and_lazy_relabel_never_backfills_aux(tmp_path):
    created, df = _b1()
    cf = CounterfactualRecorder(tmp_path / "v1.json", book="b1_box_fade")
    t = _record(cf, side="SHORT", entry=100.0, stop=100.5, targets=[95.0], created=created, horizon=37)
    cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=5 * 40))          # v1 yolu (exec_model yok)
    assert "aux" not in t.outcome and "label_version" not in t.outcome
    rc = relabel_net([t], {SYM: {"5m": df}}, created + timedelta(minutes=5 * 40), exec_model=ExecModel.of_ledger(_led()))
    assert rc["relabeled"] == 1 and t.outcome["r_net"] == pytest.approx(6.80, abs=0.01)
    assert "aux" not in t.outcome, "tembel net dolgu yardımcı etiket YAZMAZ (geriye dönük doldurma yok)"


def test_aux_failure_never_touches_the_net_label(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(A, "entry_bar_check", boom)
    created, df = _b1()
    cf = CounterfactualRecorder(tmp_path / "e.json", book="b1_box_fade")
    t = _record(cf, side="SHORT", entry=100.0, stop=100.5, targets=[95.0], created=created, horizon=37)
    assert cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=5 * 40),
                            exec_model=ExecModel.of_ledger(_led())) == 1
    assert t.outcome["r_net"] == pytest.approx(6.80, abs=0.01)
    assert t.outcome["aux"] == {"aux_version": "cf_aux_v1", "aux_status": "AUX_ERROR:RuntimeError"}


# ============================================================================ 6) karar özdeşliği — Box defteri (çok geçiş)
class _DetOS:
    def __init__(self, seed: int = 4242) -> None:
        self._rng = random.Random(seed)

    def __getattr__(self, name):
        return getattr(os, name)

    def urandom(self, n: int) -> bytes:
        return bytes(self._rng.getrandbits(8) for _ in range(n))


def _xp_rows(trades, book_key: str) -> list[str]:
    """Ortak deneyim katmanının karşı-olgusal satırları — toplayıcının KENDİ kopya + satır yolu (mühürlü modüller)."""
    from tradingbot.shared_experience import collector as C
    from tradingbot.shared_experience import rows as R
    env = R.make_env(recorded_at="2026-09-28T12:00:00+00:00", learning_since="2026-09-28T00:00:00+00:00")
    out = []
    for t in trades:
        facts = C._cf_facts(t)
        status = R.LABELLED if isinstance(facts.get("outcome"), dict) else R.PENDING
        row = R.cf_row(facts, book=book_key, rev=0, status=status, env=env)
        q = C._cf_q(t)
        out.append(json.dumps({"row": row, "q": list(q[1:]) if q else None}, sort_keys=True, default=str))
    return out


def _strip_aux(doc: dict) -> dict:
    d = json.loads(json.dumps(doc))
    for t in d.get("trades") or []:
        if isinstance(t.get("outcome"), dict):
            t["outcome"].pop("aux", None)
    return d


def _box_run(tmp_path: Path, monkeypatch, *, enabled: bool) -> dict:
    time_machine = pytest.importorskip("time_machine")
    import tradingbot.core.ids as ids_mod
    from test_learning_mode_books import BOX_WIDE, NOW_M5, _bl, _book, _box_fbs, _step
    from tradingbot.accounting.models import MarketType, SymbolFilters
    root = tmp_path / "run"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    s1, s2 = "ETH/USDT", "SOL/USDT"
    big = SymbolFilters(symbol=s1, market_type=MarketType.USDM_PERP, price_tick=Decimal("0.01"), qty_step=Decimal("0.001"),
                        min_qty=Decimal("0.001"), min_notional=Decimal("5000"), max_leverage=20)
    with monkeypatch.context() as mp:
        mp.setattr(ids_mod, "os", _DetOS())
        mp.setattr(A, "ENABLED", enabled)
        mp.setattr(A, "OVERSHOOT_MIN_N", 1)                  # tek gerçek stop yeter (iki koşuda da aynı)
        with time_machine.travel(datetime.fromtimestamp(NOW_M5 / 1000, tz=UTC), tick=False) as clock:
            book = _book(root, "b1_box_fade", mode="SHADOW", filters=[big])
            lrn = _bl("b1_box_fade")
            syms = [s1, s2]
            bars = list(BOX_WIDE)
            plan = [((107.0, 110.6, 106.5, 107.5), {s1: 107.5, s2: 107.5}, "110.7"),      # giriş barı: stop fitili
                    ((107.5, 107.6, 89.0, 89.5), {s1: 89.5, s2: 89.5}, None),              # hedef → etiket
                    ((89.5, 91.0, 89.0, 90.5), {s1: 90.5, s2: 90.5}, None),
                    ((90.5, 92.0, 90.0, 91.5), {s1: 91.5, s2: 91.5}, None)]
            now_ms = NOW_M5
            _step(book, _box_fbs(bars, now_ms=now_ms, syms=syms), now_ms=now_ms, px={s1: 107.0, s2: 107.0}, symbols=syms,
                  learning=lrn)
            for bar, px, stop_tick in plan:
                if stop_tick is not None and s2 in book.ledger.positions:
                    tk = datetime.fromtimestamp((now_ms + 60_000) / 1000, tz=UTC)
                    clock.move_to(tk)
                    book.tick({s2: TickData(last=Decimal(stop_tick), mark=Decimal(stop_tick), ts=tk.isoformat())}, now=tk,
                              bar_advance=False, source="box_timer")
                bars.append(bar)
                now_ms += M5
                clock.move_to(datetime.fromtimestamp(now_ms / 1000, tz=UTC))
                _step(book, _box_fbs(bars, now_ms=now_ms, syms=syms), now_ms=now_ms, px=px, symbols=syms, learning=lrn)
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    return {"files": files, "book": book, "cf_rows": _xp_rows(book.cf.sb.trades, "strategy_paper_box"),
            "stats": book.cf.stats()}


def test_box_decisions_ledgers_journals_and_xp_rows_are_identical_with_aux_on_and_off(tmp_path, monkeypatch):
    on = _box_run(tmp_path, monkeypatch, enabled=True)
    off = _box_run(tmp_path, monkeypatch, enabled=False)
    cf_name = [k for k in on["files"] if k.endswith("counterfactual_trades.json")]
    assert len(cf_name) == 1
    cf_name = cf_name[0]
    assert set(on["files"]) == set(off["files"])
    diff = [k for k in on["files"] if on["files"][k] != off["files"][k]]
    assert diff == [cf_name], "defter, özet, işlem hafızası, yapı karar günlüğü bayt bayt aynı; yalnız aux farkı"
    a, b = (json.loads(r["files"][cf_name]) for r in (on, off))
    assert _strip_aux(a) == b and _strip_aux(b) == b
    assert on["cf_rows"] == off["cf_rows"] and "aux" not in "".join(on["cf_rows"]), "xp_cf satırları aynı, aux sızmaz"
    assert on["stats"] == off["stats"]
    # anlamlı senaryo: gerçek stop (aşma kaynağı), etiketlenen karşı-olgusal, giriş barı bayrağı
    book = on["book"]
    assert any(h.features.get("exit_fill", {}).get("basis") == "GAP_FILL_AT_FIRST_OBSERVATION" for h in book.ledger.history)
    lab = [t for t in book.cf.sb.trades if t.outcome]
    assert lab and all("aux" in t.outcome for t in lab)
    ax = lab[0].outcome["aux"]
    assert ax["entry_bar"]["stop_hit"] is True and ax["r_net_conservative"] < 0 < lab[0].outcome["r_net"]
    assert ax["overshoot_n"] >= 1 and ax["overshoot_pct_est"] > 0


# ============================================================================ 7) karar özdeşliği — ana bot (araştırma girdileri)
def _main_run(tmp_path: Path, monkeypatch, *, enabled: bool) -> dict:
    time_machine = pytest.importorskip("time_machine")
    import tradingbot.core.ids as ids_mod
    from test_learning_mode_main import _eng, _lm
    root = tmp_path / "run"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
    created = t0 + timedelta(hours=2, minutes=7)                       # 08:00 barı = giriş barı
    now = created + timedelta(hours=4 * 6 + 4)
    seen: list[dict] = []
    with monkeypatch.context() as mp:
        mp.setattr(ids_mod, "os", _DetOS())
        mp.setattr(A, "ENABLED", enabled)
        with time_machine.travel(now, tick=False):
            eng = _eng(root, mp, _lm())
            led = eng.ledger2
            tr = t0 - timedelta(days=2)
            for _ in range(10):                                         # ana defterin gerçek ilk-örnek stopları
                assert led.open("XRP/USDT", "LONG", Decimal("1.0"), SizeSpec(Decimal("20"), AmountType.NOTIONAL, 2),
                                stop=Decimal("0.99"), targets=[Decimal("1.2")], now=tr) is not None, led.last_reject_reason
                tr += timedelta(minutes=1)
                assert led.tick({"XRP/USDT": TickData(last=Decimal("0.9895"), mark=Decimal("0.9895"), ts=tr.isoformat())},
                                now_utc=tr, bar_advance=False)
                tr += timedelta(minutes=1)
            plan = {"symbol": "S/USDT", "market_type": "USDM_PERP", "direction": "LONG", "entry": 100.0, "stop": 90.0,
                    "targets": [130.0], "horizon_bars": 6}
            rows = []
            for k, sig in enumerate(("sig-a", "sig-b")):
                (row,) = eng.shadow.add(dict(plan, plan_id=sig, symbol="S%d/USDT" % k), ["TOTAL_OPEN_RISK"], now=created)
                row.book, row.signal_key, row.label_kind = "main", sig, "TARGET_STOP_TIME"
                eng.research.add_pending("pol-x", row.id, {"decision": {"reasons": ["R%d" % k]}})
                rows.append(row)
            eng.shadow.save()
            h4 = []
            for i in range(8):
                ts = _ms(t0 + timedelta(hours=4 * i))
                lo = 88.0 if i == 0 else 99.0                          # giriş barı stopun (90) altına fitil
                hi = 131.0 if i == 3 else 101.0                         # sonra hedef 130
                h4.append({"timestamp": ts, "open": 100.0, "high": hi, "low": lo, "close": 100.0, "volume": 1.0})
            for k in range(2):
                eng.runner.last_frames["S%d/USDT" % k] = {"4h": pd.DataFrame(h4)}
            real_observe = eng.research.observe
            mp.setattr(eng.research, "observe", lambda pid, **kw: seen.append(dict(kw, policy_id=pid)) or real_observe(pid, **kw))
            eng._label_shadows()
    state = Path(eng.cfg.state_path)
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}
    return {"files": files, "seen": seen, "rows": rows, "state": state.relative_to(root).as_posix(),
            "xp": _xp_rows([t for t in eng.shadow.trades if t.book == "main"], "main"),
            "pending": dict(eng.research.pending)}


def test_main_research_inputs_shadow_book_and_xp_rows_are_identical_with_aux_on_and_off(tmp_path, monkeypatch):
    on = _main_run(tmp_path, monkeypatch, enabled=True)
    off = _main_run(tmp_path, monkeypatch, enabled=False)
    assert on["seen"] == off["seen"] and len(on["seen"]) == 2, "research_policy.observe girdileri birebir aynı"
    for kw, row in zip(on["seen"], on["rows"]):
        assert kw["baseline_r"] == pytest.approx(row.outcome["r_net"]) and kw["kind"] == "blocked"
    assert on["pending"] == off["pending"] == {}
    shadow = on["state"] + "/shadow_book.json"
    assert set(on["files"]) == set(off["files"])
    diff = [k for k in on["files"] if on["files"][k] != off["files"][k]]
    assert diff == [shadow], diff
    assert _strip_aux(json.loads(on["files"][shadow])) == json.loads(off["files"][shadow])
    assert on["xp"] == off["xp"] and "aux" not in "".join(on["xp"])
    for row in on["rows"]:
        ax = row.outcome["aux"]
        assert row.outcome["exit_reason"] == "target" and row.outcome["r_net"] > 2
        assert ax["entry_bar"] == {"checked": True, "stop_hit": True, "adverse": 88.0}
        assert ax["overshoot_n"] == 10 and ax["overshoot_pct_est"] == pytest.approx(0.05, abs=1e-3)
        assert ax["r_net_entry_bar"] < -1.0 and ax["r_net_conservative"] < ax["r_net_entry_bar"]


# ============================================================================ 8) geriye uyumluluk
_OLD_V3 = {"r_multiple": -1.0, "exit_reason": "stop", "exit_price": 99.68, "bars": 2, "mae_pct": -0.4, "mfe_pct": 0.1,
           "won": False, "veto_was_right": True, "is_counterfactual": True, "label_kind": "TARGET_STOP_TIME", "approx": False,
           "label_method": "PATH", "label_version": "cf_label_v3", "r_gross": -1.0, "r_net": -1.371,
           "cost_r": 0.371, "cost_parts_r": {"path": 0.0, "exit_fill_model": 0.0, "entry_fill": 0.09, "exit_slippage": 0.09,
                                             "fees": 0.19, "funding": 0.0},
           "net_exit_reason": "stop", "net_exit_basis": "STOP_AT_LEVEL", "net_exit_price": 99.65, "entry_fill": 100.03,
           "won_net": False, "veto_was_right_net": True, "funding_complete": True, "funding_missing": None,
           "intrabar_ambiguous_bars": 0, "net_status": "OK",
           "exec_model": {"contract": "cf_net_ledger_replay_v2", "taker_pct": 0.05}}


def _old_file(path: Path, created: datetime) -> dict:
    """Değişiklik öncesi kodun yazdığı biçim: etiketli v3 (aux YOK), etiketli v1 (yalnız brüt) ve bekleyen kayıt."""
    def tr(i, outcome, **kw):
        d = {"id": "cf_old%d" % i, "plan_id": "signal_ts:%d" % i, "symbol": SYM, "market_type": "USDM_PERP",
             "direction": "SHORT", "created_at": created.isoformat(), "entry": 100.0, "stop": 100.5, "targets": [95.0],
             "horizon_bars": 37, "variant": "as_planned", "reason_not_opened": ["INSUFFICIENT_MARGIN"],
             "label_ts": (created + timedelta(minutes=5 * 37)).isoformat(), "tf_minutes": 5, "leverage": 1.0,
             "outcome": outcome, "labeled_at": (created + timedelta(minutes=15)).isoformat() if outcome else None,
             "is_counterfactual": True, "book": "b1_box_fade", "signal_key": "signal_ts:%d" % i,
             "label_kind": "TARGET_STOP_TIME", "learning_unlocked": False, "rule_version": "b1", "approx": False}
        d.update(kw)
        return d
    v1 = {k: _OLD_V3[k] for k in ("r_multiple", "exit_reason", "exit_price", "bars", "mae_pct", "mfe_pct", "won",
                                   "veto_was_right", "is_counterfactual", "label_kind", "approx", "label_method")}
    doc = {"trades": [tr(1, dict(_OLD_V3)), tr(2, v1), tr(3, None, plan_id="k", signal_key="k")],
           "meta": {"schema_version": "learning_cf_v1", "book": "b1_box_fade", "dropped": 0, "expired": 0,
                    "recorded_total": 3, "superseded": 0}}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def test_a_pre_change_counterfactual_file_loads_labels_archives_and_reports_as_before(tmp_path, monkeypatch):
    import bot_scorecard as S
    created, df = _b1()
    path = tmp_path / "counterfactual_trades.json"
    old = _old_file(path, created)
    card_before = S.counterfactual_card(path)
    cf = CounterfactualRecorder(path, book="b1_box_fade")
    stats_before = cf.stats()
    assert [t.outcome is None for t in cf.sb.trades] == [False, False, True]
    n = cf.label_pending({SYM: {"5m": df}}, created + timedelta(minutes=5 * 40), exec_model=ExecModel.of_ledger(_led()))
    assert n == 1
    cf.save()
    doc = json.loads(path.read_text(encoding="utf-8"))
    old_rows = {t["id"]: t for t in old["trades"]}
    new_rows = {t["id"]: t for t in doc["trades"]}
    assert new_rows["cf_old1"] == {k: v for k, v in old_rows["cf_old1"].items() if v is not None}, "eski v3 kayıt aynen"
    assert "aux" not in new_rows["cf_old1"]["outcome"]
    assert new_rows["cf_old2"]["outcome"]["label_version"] == "cf_label_v1", "eski v1: yalnız mevcut tembel kural (aux YOK)"
    assert "aux" not in new_rows["cf_old2"]["outcome"]
    assert set(new_rows["cf_old3"]["outcome"]["aux"]) == AUX_KEYS, "yalnız bundan sonra etiketlenen kayıt aux taşır"
    st = cf.stats()
    assert {k: st[k] for k in ("recorded_total", "dropped", "expired", "superseded")} == \
        {k: stats_before[k] for k in ("recorded_total", "dropped", "expired", "superseded")}
    # karne: eski sayılar aynı formülle; ihtiyatlı ortalama yalnız aux'lu kayıttan
    assert card_before["n_conservative"] == 0 and card_before["mean_r_conservative"] is None and card_before["n_aux"] == 0
    card = S.counterfactual_card(path)
    assert card["n_aux"] == 1 and card["n_conservative"] == 0, "aşma tahmini yok (gerçek geçmiş verilmedi) → kapsam dışı"
    # arşiv: aktif sınır taşınca eski ve yeni kayıtlar KAYIPSIZ arşive (aux dahil, eski kayıt aynen)
    rows = []

    class _Arc:
        def recover(self):
            return None

        def seal(self, block):
            rows.extend(json.loads(x) for x in block)
            return {"n": len(block)}

        def commit(self, meta):
            return None
    sb = ShadowBook(path, archive=_Arc())
    monkeypatch.setattr(ShadowBook, "MAX_TRADES", 1)
    sb.save()
    assert [r["id"] for r in rows] == ["cf_old1", "cf_old2"] and rows[0] == new_rows["cf_old1"]
    # geri alma: eski kod `ShadowTrade(**…)` ile yükler, aux'u (sözlük içinde) olduğu gibi taşır
    sb2 = ShadowBook(path)
    assert [t.id for t in sb2.trades] == ["cf_old3"] and set(sb2.trades[0].outcome["aux"]) == AUX_KEYS


# ============================================================================ 9) karne
def test_scorecard_shows_the_conservative_mean_and_coverage_next_to_the_unchanged_net_mean(tmp_path):
    import bot_scorecard as S
    created, _df = _b1()
    path = tmp_path / "cf.json"
    _old_file(path, created)
    doc = json.loads(path.read_text(encoding="utf-8"))
    base = dict(_OLD_V3)
    extra = []
    for i, (rn, rc) in enumerate(((2.0, -0.5), (-1.2, None), (0.4, 0.3))):
        o = dict(base, r_net=rn, aux={"aux_version": "cf_aux_v1", "r_net_conservative": rc,
                                      "entry_bar": {"checked": True, "stop_hit": False, "adverse": 100.2}})
        extra.append(dict(doc["trades"][0], id="cf_n%d" % i, outcome=o))
    doc["trades"] += extra
    path.write_text(json.dumps(doc), encoding="utf-8")
    card = S.counterfactual_card(path)
    plain = tmp_path / "plain.json"
    plain.write_text(json.dumps(_strip_aux(doc)), encoding="utf-8")
    ref = S.counterfactual_card(plain)
    new_keys = {"n_aux", "n_conservative", "mean_r_conservative", "mean_r_net_conservative_rows"}
    assert {k: v for k, v in card.items() if k not in new_keys | {"file"}} == \
        {k: v for k, v in ref.items() if k not in new_keys | {"file"}}, "mevcut sayılar AYNEN"
    assert card["n_aux"] == 3 and card["n_conservative"] == 2
    assert card["mean_r_conservative"] == pytest.approx(-0.1) and card["mean_r_net_conservative_rows"] == pytest.approx(1.2)
    lm = {"since": "2026-09-28", "source": "x", "note_tr": "not",
          "books": {"strategy_paper_box": {"name": "Box", "before": None, "after": None, S.POLICY: None,
                                           S.LEARNING_EXTRA: None, "counterfactual": card}}}
    txt = "\n".join(S.render_learning({"learning_mode": lm}))
    assert "ort.R %s" % S._fmt(card["mean_r"]) in txt
    assert "ihtiyatlı -0.10 (n=2, aynı kayıtlarda net 1.20)" in txt


# ============================================================================ 10) bellek
def test_aux_memory_overhead_per_loaded_record_is_bounded():
    """Kayıt başına ek bellek (yüklenmiş `ShadowTrade` + sözlük) ölçülür: ölçülen ≈ 0,6 KB; aktif dosya `MAX_TRADES`
    (5000) ile sınırlı → defter başına en kötü ≈ 3 MB."""
    created, _df = _b1()
    aux = {"aux_version": "cf_aux_v1", "entry_bar": {"checked": True, "stop_hit": False, "adverse": 100.123456},
           "r_net_entry_bar": -1.046667, "overshoot_pct_est": 0.065445, "overshoot_n": 200, "r_net_sampled_est": 0.563608,
           "r_net_conservative": 0.512345}

    def load(with_aux: bool) -> int:
        trades = []
        for i in range(2000):
            o = dict(_OLD_V3, r_net=0.001 * i)
            if with_aux:
                o["aux"] = dict(aux, r_net_conservative=0.001 * i - 0.2, entry_bar=dict(aux["entry_bar"], adverse=100 + i / 7))
            trades.append({"id": "c%d" % i, "plan_id": "p", "symbol": SYM, "market_type": "USDM_PERP", "direction": "SHORT",
                           "created_at": created.isoformat(), "entry": 100.0, "stop": 100.5, "targets": [95.0],
                           "horizon_bars": 37, "variant": "as_planned", "reason_not_opened": ["X"],
                           "label_ts": created.isoformat(), "tf_minutes": 5, "outcome": o})
        s = json.dumps({"trades": trades})
        tracemalloc.start()
        d = json.loads(s)
        objs = [ShadowTrade(**t) for t in d["trades"]]
        del d
        cur, _peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert len(objs) == 2000
        return cur
    per = (load(True) - load(False)) / 2000
    assert 0 < per < 1024, per
