# -*- coding: utf-8 -*-
"""P3e — keşif katmanı (docs/ENGINE_BRAIN_V1.md §5.1, §8.1 P3e kabulü; ana belge §6.8; `research_engine/explore.py`).

Kabul: zaman yolculuğu yok (yalnız ≥ `T_SEAL_MS`; özellik testi), artımlı = baştan (bayt-özdeş), hüküm kelimesi yok
(metin testi), 3.745 seri (kayıttan), kapı dışı işaretler, kapıya giden tek yol `fwd_reads`, mühürlü/arşiv-doğrulanmış
okuma ve `DATA_MOVING`, `engine-query explore` sınırları; ölçekli dünyada ≤ 10 dk (`slow`). Sentetik veri, ağ yok.
"""
from __future__ import annotations

import ast
import json
import random
import time
from pathlib import Path

import pandas as pd
import pytest

from tradingbot.research_engine import explore as EX
from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import registry as RG
from tradingbot.research_engine import stats as ST
from tradingbot.research_engine import synthetic as SY
from tradingbot.research_engine import universe as U
from tradingbot.research_engine import wf as WF

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "tradingbot" / "research_engine"
D1 = 86_400_000
H1 = 3_600_000
SEAL = RG.T_SEAL_MS
DAY0 = SEAL // D1 * D1
#: config.yaml `entry_universe.symbols` (40; mühür anındaki giriş evreni)
COINS40 = ["BTC", "ETH", "ZEC", "SOL", "NEAR", "UNI", "XRP", "ONE", "BNB", "ARB", "DOGE", "LSK", "SUI", "ENA", "G", "WLD",
           "ADA", "ONDO", "1000PEPE", "COTI", "LINK", "REZ", "DASH", "AAVE", "TAO", "BCH", "AVAX", "DOT", "FIL", "APT", "SYN",
           "LTC", "XLM", "INJ", "FET", "TRX", "XMR", "1000SHIB", "OP", "KSM"]
TICK = 0.001


def _fam(f: str) -> list[str]:
    return [v["variant_id"] for v in RG.LIB_V1["variants"] if v["family"] == f and not v.get("alias_of")]


#: hızlı ama her yolu kapsayan alt küme: 1d/4h/1h saf aileler, F2 + Box ailesi (5m), H (fonlama kararı), D4 ailesi
#: (F_BTC dahil), C4 varyasyonları + örtüleri (one_entry_per_signal)
MIX = (["TS_7_LS", "MAX_20_50_4h_LS", "DON_20_10_1d_LS", "KEL_20_2_1h", "RSI2_4h", "BBRE_1h", _fam("F2")[0],
        "FUND_FADE_24h", "FUND_CARRY", "K_BOX", "O_BOX_X_TIME", "O_BOX_F_ER", "K_D4", "O_D4_X_TRAIL2", "O_D4_F_BTC",
        "K_C4_CV004", "O_C4_X_TPL", "O_C4_F_VOL"])


def _inputs(world, now, *, fund_lag_days=None, mark_nan=False, drop_funding=(), drop_1h=(), markpx=True,
            drop_markpx=()):
    out = {}
    for c, w in world.items():
        mp = None
        if markpx and w.get("markpx_1h") is not None:
            mp = [r for r in w["markpx_1h"] if not any(a <= r["timestamp"] < b for a, b in drop_markpx)]
        fr = []
        for t, r, m in w["funding"] or ():
            if any(a <= t < b for a, b in drop_funding):
                continue
            src = "rest" if fund_lag_days is not None and t >= SEAL and t > now - fund_lag_days * D1 else "archive"
            fr.append((t, r, float("nan") if mark_nan else m, src))
        bars = dict(w["bars"])
        if drop_1h and "1h" in bars:
            bars["1h"] = [r for r in bars["1h"] if not any(a <= r["timestamp"] < b for a, b in drop_1h)]
        out[c] = EX.inputs_from_rows(c, bars, fr if w["funding"] is not None else None, TICK, now_ms=now, markpx=mp)
    return out


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "_run.json"}


def _night(k: int, hour: int = 1) -> int:
    return DAY0 + k * D1 + hour * H1


# ============================================================================ 3.745 seri (kayıttan)
def test_series_plan_is_the_sealed_3745_from_the_registry():
    plan = EX.series_plan(COINS40)
    c = EX.plan_counts(plan)
    assert len(plan) == c["total"] == RG.explore_series() == 3745 == 45 * 40 + 13 * 40 + 35 * 40 + 5 + 2 + 6 * 3
    assert RG.LIB_V1["budget_declared"]["EXPLORE_SERIES"] == 3745
    assert (c["STANDARD"], c["COPY"], c["COVER"], c["XSEC"], c["SPREAD"], c["gold"]) == (1800, 520, 1400, 5, 2, 18)
    assert (c["blocked"], c["run"]) == (25, 3720)
    assert {s.family for s in plan if s.blocked} == {"I", "J", "G"} and all(s.gaps for s in plan if s.blocked)
    assert len({s.series_id for s in plan}) == 3745
    assert not any(RG.variants_by_id()[s.variant_id].get("alias_of") for s in plan)       # alias'lar tekilleşir
    assert {s.instrument for s in plan if s.gold} == set(RG.LIB_V1["explore"]["gold_instruments"])
    assert set(RG.LIB_V1["p23"]["symbols"]) <= {s.instrument for s in plan}                # P23 ⊂ giriş evreni
    # sembol biçimleri aynı coin'e çözülür; sayım mühürlü: 39 ya da 41 coin kapalı hata
    assert [s.series_id for s in EX.series_plan([f"{c}/USDT" for c in COINS40])] == [s.series_id for s in plan]
    assert [s.series_id for s in EX.series_plan([f"{c}USDT" for c in COINS40])] == [s.series_id for s in plan]
    # çalışma anındaki evren dondurulmuş 40 coin'den farklıysa koşu yok, fark raporlanır (koordinatör kararı)
    assert list(U.ENTRY_UNIVERSE_FROZEN) == COINS40 and U.ENTRY_UNIVERSE_SEALED is False      # mühür bekliyor
    for bad, missing, extra, dup in ((COINS40[:-1], ["KSM"], [], []), (COINS40 + ["NEW"], [], ["NEW"], []),
                                     (COINS40[:-1] + ["BTC/USDT"], ["KSM"], [], ["BTC"]),
                                     (COINS40[:-1] + ["NEWUSDT"], ["KSM"], ["NEW"], [])):
        with pytest.raises(EX.EntryUniverseRefused, match="ENTRY_UNIVERSE_DIFF") as ei:
            EX.series_plan(bad)
        assert (ei.value.diff["missing"], ei.value.diff["extra"], ei.value.diff["duplicates"]) == (missing, extra, dup)
        with pytest.raises(WF.EntryUniverseRefused) as wi:                     # WF'nin evren kapısı aynı fark
            WF.require_entry_universe(bad)
        assert wi.value.diff == ei.value.diff
    assert WF.require_entry_universe(list(reversed(COINS40)))["ok"]           # sıra önemsiz
    # çalıştırılabilir varyantların hepsinin penceresi sonludur (artımlı = baştan ancak böyle kanıtlanır)
    assert len(EX.runnable_variants()) == 93 and all(EX.meta(v).lookback for v in EX.runnable_variants())


def test_run_report_counts_every_planned_series(tmp_path):
    w = SY.explore_world(["BTC"], seed=2, seal_ms=SEAL, fwd_days=3)
    rep = EX.explore_step(tmp_path, _inputs(w, _night(3)), entry_symbols=COINS40, only=["TS_7_LS", "KEL_20_2_1h"])
    assert sum(rep["status"].values()) == 25 + 2 * 40 and rep["status"][EX.ST_BLOCKED] == 25
    assert rep["status"][EX.ST_OK] == 2 and rep["plan"]["total"] == 3745
    assert json.loads((tmp_path / "_run.json").read_text(encoding="utf-8"))["plan"]["total"] == 3745
    before = _files(tmp_path)
    swapped = COINS40[:-1] + ["NEW"]
    rep = EX.explore_step(tmp_path, _inputs(w, _night(3)), entry_symbols=swapped, only=["TS_7_LS"])
    assert rep["status"] == {"REFUSED": 1} and rep["refused"]["missing"] == ["KSM"] and rep["refused"]["extra"] == ["NEW"]
    assert _files(tmp_path) == before                                          # hiçbir seri koşmadı
    lb = "\n".join(EX.leaderboard_lines(EX.read_states(tmp_path), EX.read_run(tmp_path)))
    assert "ÇALIŞMADI" in lb and "KSM" in lb and "NEW" in lb and EX.verdict_word_violations(lb.splitlines()) == []


def test_lazy_loader_matches_mapping_and_deadline_leaves_series_untouched(tmp_path):
    w = SY.explore_world(["BTC", "ETH"], seed=14, seal_ms=SEAL, fwd_days=4)
    data = _inputs(w, _night(4))
    only = ["KEL_20_2_1h", "K_M2", "O_M2_F_BTC"]
    calls: list[str] = []

    def load(c):
        calls.append(c)
        return data.get(c)
    EX.explore_step(tmp_path / "a", load, entry_symbols=COINS40, only=only)
    assert sorted(calls) == sorted(COINS40)                          # her coin bir kez (BTC önce, 1d/4h'si tutulur)
    EX.explore_step(tmp_path / "b", data, entry_symbols=COINS40, only=only)
    assert _files(tmp_path / "a") == _files(tmp_path / "b")
    calls.clear()
    rep = EX.explore_step(tmp_path / "c", load, entry_symbols=COINS40, only=only, deadline=0.0, clock=lambda: 1.0)
    assert rep["status"][EX.ST_DEADLINE] == 3 * 40 and not list((tmp_path / "c").glob("*/*")) and calls == ["BTC"]


# ============================================================================ artımlı = baştan (bayt-özdeş)
@pytest.mark.parametrize("seed", [5, 9])
def test_incremental_steps_are_byte_identical_to_a_from_scratch_recompute(tmp_path, seed):
    w = SY.explore_world(["BTC", "ETH"], seed=seed, seal_ms=SEAL, fwd_days=12)
    rnd = random.Random(seed)
    nights = sorted(rnd.sample(range(1, 12), 4)) + [12]
    inc = tmp_path / "inc"
    for k in nights:          # fonlama arşivi 3 gün geriden gelir → kesinleşmemiş işlemler gece gece kesinleşir
        rep = EX.explore_step(inc, _inputs(w, _night(k, rnd.randrange(24)), fund_lag_days=3), entry_symbols=COINS40,
                              only=MIX)
        assert not rep["errors"], rep["errors"]
    last = _night(12, rnd.randrange(24))
    rep = EX.explore_step(inc, _inputs(w, last, fund_lag_days=3), entry_symbols=COINS40, only=MIX)
    scr = tmp_path / "scr"
    rep2 = EX.explore_step(scr, _inputs(w, last, fund_lag_days=3), entry_symbols=COINS40, only=MIX)
    assert not rep["errors"] and not rep2["errors"] and rep2["rebuilt"] == 0
    a, b = _files(inc), _files(scr)
    assert a.keys() == b.keys() and len(a) > 2 * len(MIX)
    assert [k for k in a if a[k] != b[k]] == []
    sts = EX.read_states(scr)
    assert sum(st["summary"]["n"] for st in sts.values()) > 50                # gerçekten işlem var
    assert any(st["unsettled"] for st in sts.values())                        # fonlama gecikmesi yolu sınandı
    assert any(st["used"] for st in sts.values())                             # one_entry_per_signal yolu sınandı
    assert any(st["open"] for st in sts.values())
    # aynı gece yeniden: hiçbir şey değişmez (UNCHANGED), dosyalar aynı
    rep3 = EX.explore_step(inc, _inputs(w, last, fund_lag_days=3), entry_symbols=COINS40, only=MIX)
    assert rep3["status"].get(EX.ST_UNCHANGED) == len(MIX) * 2 and _files(inc) == a


def _shift_world(sd: KN.SeriesData, tf: str, fwd: int) -> tuple[dict, dict, list]:
    """P3b sentetik dünyasını mühürün etrafına taşı: `fwd` sinyal barı mühürden sonra."""
    rows = sd.bars[tf]
    off = DAY0 + D1 - int(rows[len(rows) - fwd]["timestamp"])

    def sh(xs):
        return [dict(r, timestamp=int(r["timestamp"]) + off) for r in xs or ()]
    bars = {k: sh(v) for k, v in sd.bars.items()}
    btc = {"1d": sh(sd.btc_1d), "4h": sh(sd.btc_4h)}
    fund = [(t + off, r, m) for t, r, m in sd.funding or ()]
    return bars, btc, fund, sh(sd.markpx_1h)


def test_incremental_equals_scratch_for_history_reading_copies(tmp_path):
    """T2/M2 (+ örtüleri; `ctx.history` yapı kümesi) ve Formasyon: geçmiş JSON'dan yeniden verilir — bayt-özdeş."""
    t_bars, t_btc, t_fund, t_mp = _shift_world(SY.trend_world(7, 700), "1d", 160)
    f_bars, _fb, f_fund, f_mp = _shift_world(SY.fm_world(3, 1400), "4h", 420)
    world = {"BTC": {"bars": t_btc, "funding": None}, "SOL": {"bars": t_bars, "funding": t_fund, "markpx_1h": t_mp},
             "BNB": {"bars": f_bars, "funding": f_fund, "markpx_1h": f_mp}}
    only = ["K_T2", "K_M2", "K_FM"] + KN.covers_of("T2") + KN.covers_of("M2")
    nights = [25, 61, 62, 110, 150]
    inc = tmp_path / "inc"
    for k in nights:
        rep = EX.explore_step(inc, _inputs(world, _night(k)), entry_symbols=COINS40, only=only)
        assert not rep["errors"], rep["errors"]
    rep2 = EX.explore_step(tmp_path / "scr", _inputs(world, _night(150)), entry_symbols=COINS40, only=only)
    assert not rep2["errors"]
    a, b = _files(inc), _files(tmp_path / "scr")
    assert a.keys() == b.keys() and [k for k in a if a[k] != b[k]] == []
    sts = EX.read_states(tmp_path / "scr")
    t2 = [st for sid, st in sts.items() if sid.endswith("@SOL") and ("T2" in sid or "M2" in sid)]
    assert sum(st["summary"]["n"] for st in t2) >= 10
    rows = [json.loads(x) for x in (tmp_path / "scr" / "K_T2" / "SOL.jsonl").read_text("utf-8").splitlines()]
    assert any(r["k"] == "T" and r["evidence"].get("structure") for r in rows)      # geçmişin okuduğu kanıt saklanır


def test_changed_past_data_or_versions_rebuild_from_scratch(tmp_path):
    w = SY.explore_world(["BTC"], seed=4, seal_ms=SEAL, fwd_days=8)
    only = ["KEL_20_2_1h", "TS_7_LS", "K_D4"]
    EX.explore_step(tmp_path / "inc", _inputs(w, _night(4)), entry_symbols=COINS40, only=only)
    # mühürden sonraki doğrulanmış bir 1h bar değişti (ör. arşiv düzeltmesi) → parmak izi tutmaz → baştan
    w2 = {"BTC": {"bars": dict(w["BTC"]["bars"]), "funding": w["BTC"]["funding"], "markpx_1h": w["BTC"]["markpx_1h"]}}
    h = [dict(r) for r in w2["BTC"]["bars"]["1h"]]
    j = next(i for i, r in enumerate(h) if r["timestamp"] >= SEAL + 30 * H1)
    h[j]["close"] *= 1.01
    h[j]["high"] = max(h[j]["high"], h[j]["close"])
    w2["BTC"]["bars"]["1h"] = h
    rep = EX.explore_step(tmp_path / "inc", _inputs(w2, _night(8)), entry_symbols=COINS40, only=only)
    assert rep["rebuilt"] == len(only)
    EX.explore_step(tmp_path / "scr", _inputs(w2, _night(8)), entry_symbols=COINS40, only=only)
    assert _files(tmp_path / "inc") == _files(tmp_path / "scr")
    # yarım kalmış ekleme (durumdan uzun jsonl) ya da sürüm değişikliği de baştan kurar
    p = tmp_path / "inc" / "KEL_20_2_1h" / "BTC.jsonl"
    p.write_bytes(p.read_bytes() + b'{"k":"T"}\n')
    st = tmp_path / "inc" / "TS_7_LS" / "BTC.state.json"
    d = json.loads(st.read_text("utf-8"))
    d["kernel"] = "kernel_v1"
    st.write_text(EX.dumps(d) + "\n", encoding="utf-8")
    rep = EX.explore_step(tmp_path / "inc", _inputs(w2, _night(8)), entry_symbols=COINS40, only=only)
    assert rep["rebuilt"] == 2
    assert _files(tmp_path / "inc") == _files(tmp_path / "scr")


# ============================================================================ zaman yolculuğu (özellik testi)
class _Spy:
    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def signals(self, bars, ctx):
        self._log.append((self._inner.variant_id, int(ctx["now_ms"]), max(
            (int(r["timestamp"]) for rows in bars.values() for r in rows[-1:]), default=0)))
        return self._inner.signals(bars, ctx)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _perturb(world, cut_of, factor):
    out = {}
    for c, w in world.items():
        bars = {}
        for tf, rows in w["bars"].items():
            cut = cut_of(tf)
            bars[tf] = [dict(r, open=r["open"] * factor, high=r["high"] * factor, low=r["low"] * factor,
                             close=r["close"] * factor) if r["timestamp"] < cut else r for r in rows]
        out[c] = {"bars": bars, "funding": w["funding"], "markpx_1h": w.get("markpx_1h")}
    return out


def _rows_only(root: Path) -> dict[str, bytes]:
    """jsonl satırları + durumun özet/işlem alanları (veri parmak izi hariç: o, okunan bütün satırlara bağlıdır)."""
    out = {}
    for k, v in _files(root).items():
        if k.endswith(".state.json"):
            d = json.loads(v)
            d.pop("data_fp")
            v = EX.dumps(d).encode()
        out[k] = v
    return out


@pytest.mark.parametrize("seed", list(range(6)))
def test_time_travel_no_pre_seal_row_is_ever_used_as_forward_data(tmp_path, monkeypatch, seed):
    rnd = random.Random(1000 + seed)
    fwd = rnd.randrange(3, 9)
    only = rnd.sample([v for v in MIX if not v.startswith(("K_BOX", "O_BOX"))], 6) + ["K_BOX"]
    w = SY.explore_world(["BTC", "ETH"], seed=50 + seed, seal_ms=SEAL, fwd_days=fwd + 2)
    now = _night(fwd, rnd.randrange(24))
    log: list[tuple[str, int, int]] = []
    real = EX._variants
    monkeypatch.setattr(EX, "_variants", lambda base, members: {k: _Spy(v, log) for k, v in real(base, members).items()})
    EX.explore_step(tmp_path / "a", _inputs(w, now), entry_symbols=COINS40, only=only)
    monkeypatch.setattr(EX, "_variants", real)
    # (1) kural hiçbir zaman açılışı T_SEAL'dan önceki bir barın kapanışında karar vermez; gördüğü son bar mühürden sonra
    assert log and all(now_ms >= EX.meta(v).start_ms and last >= SEAL for v, now_ms, last in log)
    # (2) hiçbir işlem / gün satırı mühür öncesine düşmez; bacaklar girişten sonra
    for st in EX.read_states(tmp_path / "a").values():
        start = EX.meta(st["variant_id"]).start_ms
        assert st["start_ms"] == start == SEAL + KN.TF_MS[st["signal_tf"]] and st["checkpoint_ms"] >= start
        trades = list(st["unsettled"]) + ([st["open"]] if st["open"] else [])
        p = tmp_path / "a" / st["variant_id"] / f"{st['instrument']}.jsonl"
        rows = [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []
        trades += [r for r in rows if r["k"] == "T"]
        for t in trades:
            assert t["entry_ts"] >= start and t["signal_ts"] < t["entry_ts"] + 1
            assert all(lg["ts"] >= t["entry_ts"] and lg["t_from"] >= t["entry_ts"] for lg in t["legs"])
            assert all(d >= SEAL // D1 for d, *_v in (t["mtm"] or []))
        assert all(r["d"] >= SEAL // D1 for r in rows if r["k"] == "D")
    # (3) mühür öncesi barlar yalnız mühürlü pencere kadar ısınma verir: pencerenin dışını rastgele bozmak hiçbir şeyi
    #     değiştirmez (seçim yok, işlem yok)
    lb = EX.warm_bars()
    w_pre = _perturb(w, lambda tf: SEAL - (lb[tf] + EX.SLICE_MARGIN + 2) * KN.TF_MS[tf], 1.0 + rnd.uniform(0.05, 0.5))
    EX.explore_step(tmp_path / "b", _inputs(w_pre, now), entry_symbols=COINS40, only=only)
    assert _rows_only(tmp_path / "a") == _rows_only(tmp_path / "b")
    # (4) cepheden sonrası (kapanmamış, doğrulanmamış ya da onlardan sonra gelen) görünmez
    w_post = _perturb(w, lambda tf: 10 ** 15, 1.0)
    for c in w_post:
        for tf, rows in w_post[c]["bars"].items():
            w_post[c]["bars"][tf] = [dict(r, close=r["close"] * 1.3, high=r["high"] * 1.3) if r["timestamp"] >= now
                                     else r for r in rows]
    EX.explore_step(tmp_path / "c", _inputs(w_post, now), entry_symbols=COINS40, only=only)
    assert _files(tmp_path / "a") == _files(tmp_path / "c")


def test_loader_rules_seal_archive_only_and_frontier_cut():
    rows = [{"timestamp": SEAL - 3 * H1, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "rest"},
            {"timestamp": SEAL - 2 * H1, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "archive"},
            {"timestamp": SEAL - 1 * H1, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "seed"},
            {"timestamp": SEAL, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "archive"},
            {"timestamp": SEAL + H1, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "archive"},
            {"timestamp": SEAL + 2 * H1, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "archive_unverified"},
            {"timestamp": SEAL + 3 * H1, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "archive"},
            {"timestamp": SEAL - 10 ** 12, "open": 1, "high": 1, "low": 1, "close": 1, "_src": "archive"}]
    cd = EX.inputs_from_rows("BTC/USDT", {"1h": rows}, [(SEAL - H1, 1e-4, 1.0, "archive"), (SEAL + H1, 1e-4, 1.0, "rest"),
                                                        (SEAL + 2 * H1, 1e-4, 1.0, "archive")], TICK, now_ms=SEAL + 10 * H1)
    assert [r["timestamp"] for r in cd.bars["1h"]] == [SEAL - 2 * H1, SEAL, SEAL + H1]
    assert cd.frontier["1h"] == SEAL + 2 * H1 and cd.funding == [(SEAL - H1, 1e-4, 1.0)]
    # kapanmamış bar cepheyi keser
    cd = EX.inputs_from_rows("BTC", {"1h": rows[3:5]}, None, TICK, now_ms=SEAL + H1 + 5)
    assert cd.frontier["1h"] == SEAL + H1 and cd.funding is None
    # ileri satır yoksa seri ileri verisizdir (çalıştırılmaz)
    assert EX.inputs_from_rows("BTC", {"1h": rows[:3]}, None, TICK, now_ms=SEAL + 9 * H1).frontier["1h"] is None


def test_store_loader_reads_only_archive_rows_and_data_moving_skips(tmp_path):
    from tradingbot.research_engine.provider import DataMoving
    from tradingbot.research_engine.store import ResearchStore

    w = SY.explore_world(["BTC"], seed=6, seal_ms=SEAL, fwd_days=4)
    store = ResearchStore(tmp_path / "store", clock_ms=lambda: _night(5))
    for tf, rows in w["BTC"]["bars"].items():
        df = pd.DataFrame(rows)
        cut = DAY0 + 3 * D1
        store.write("futures", "BTCUSDT", tf, df[df["timestamp"] < cut], src="archive", now_ms=_night(5))
        store.write("futures", "BTCUSDT", tf, df[df["timestamp"] >= cut], src="rest", now_ms=_night(5))
    fdf = pd.DataFrame(w["BTC"]["funding"], columns=["timestamp", "rate", "mark"])
    fdf["mark"] = float("nan")                                       # gerçek arşiv: fonlama satırında mark yok
    store.write("futures", "BTCUSDT", "funding", fdf, src="archive", now_ms=_night(5))
    store.write("futures", "BTCUSDT", "markpx_1h", pd.DataFrame(w["BTC"]["markpx_1h"]), src="archive", now_ms=_night(5))
    cd = EX.load_coin(store, "BTC", tick=TICK, now_ms=_night(5))
    assert cd.moving is None and cd.frontier["1h"] == DAY0 + 3 * D1 and cd.frontier["5m"] == DAY0 + 3 * D1
    assert all(r["timestamp"] < DAY0 + 3 * D1 for tf in cd.bars if tf != EX.MARKPX for r in cd.bars[tf])
    assert all(r["timestamp"] >= EX.warm_start(tf) for tf in cd.bars if tf != EX.MARKPX for r in cd.bars[tf])
    assert cd.frontier[EX.MARKPX] == DAY0 + 5 * D1 and all(m != m for _t, _r, m in cd.funding)
    root = tmp_path / "explore"
    EX.explore_step(root, {"BTC": cd}, entry_symbols=COINS40, only=["KEL_20_2_1h"])
    before = _files(root)
    assert before

    class Moving:
        def iter_parts(self, *a, **k):
            raise DataMoving("futures/BTCUSDT/1h", "2026/10", "parça mühürden sonra değişti")
            yield  # pragma: no cover

    cd2 = EX.load_coin(Moving(), "BTC", tick=TICK, now_ms=_night(6))
    assert cd2.moving and "parça mühürden sonra değişti" in cd2.moving
    rep = EX.explore_step(root, {"BTC": cd2}, entry_symbols=COINS40, only=["KEL_20_2_1h"])
    assert rep["status"][EX.ST_DATA_MOVING] == 1 and rep["moving"]["BTC"] and _files(root) == before
    # tick yoksa (maliyet spread'i ölçülemez) seri çalıştırılmaz — kapalı hata
    cd3 = EX.load_coin(store, "BTC", tick=None, now_ms=_night(5))
    rep = EX.explore_step(tmp_path / "e2", {"BTC": cd3}, entry_symbols=COINS40, only=["KEL_20_2_1h"])
    assert rep["status"][EX.ST_NO_TICK] == 1 and not list((tmp_path / "e2").glob("*/*"))


# ============================================================================ kapı dışı işaretler
def test_gate_excluded_trades_never_enter_any_statistic(tmp_path):
    w = SY.explore_world(["BTC", "ETH"], seed=8, seal_ms=SEAL, fwd_days=10)
    only = ["KEL_20_2_1h", "BBRE_1h", "RSI2_1h", "TS_7_LS", "MAX_20_50_4h_LS", "K_BOX", "KEL_20_2_4h", "RSI2_4h",
            "BBRE_4h", "BBSQ_20_4h"]
    gap = [(DAY0 + 3 * D1, DAY0 + 3 * D1 + 20 * H1)]                   # iki uzlaşma eksik → FUNDING_GAP
    hole = [(DAY0 + 5 * D1 + k * 4 * H1 + H1, DAY0 + 5 * D1 + k * 4 * H1 + 2 * H1) for k in range(24)]  # 4h barlarının
    #                                                                   1h alt barı eksik → değen barda PATH_1H_GAP
    EX.explore_step(tmp_path, _inputs(w, _night(10), drop_funding=gap, drop_1h=hole), entry_symbols=COINS40, only=only)
    sts = EX.read_states(tmp_path)
    n_ex = 0
    for st in sts.values():
        p = tmp_path / st["variant_id"] / f"{st['instrument']}.jsonl"
        rows = [json.loads(x) for x in p.read_text("utf-8").splitlines()] if p.exists() else []
        tr = [r for r in rows if r["k"] == "T"] + list(st["unsettled"])
        for r in tr:
            assert r["excluded"] == KN.gate_excluded(r) == bool(set(r["flags"]) & set(KN.GATE_EXCLUDED_FLAGS))
        inc = [r for r in tr if not r["excluded"]]
        sm = st["summary"]
        assert sm["n"] == len(inc) and sm["n_excluded"] == len(tr) - len(inc)
        assert sm["sum_r"][1] == pytest.approx(sum(r["r"]["ADVERSE"] for r in inc))
        days = {r["d"]: r["pct"] for r in rows if r["k"] == "D"}
        for d, v in days.items():                                    # kesin günler yalnız kapı-içi işlemlerin MTM'i
            want = sum(m[2] for r in inc for m in (r["mtm"] or []) if m[0] == d) / ST.EQUITY_BASE_USDT * 100.0
            assert v[1] == pytest.approx(want, abs=1e-9)
        n_ex += sm["n_excluded"]
    flags = {f for st in sts.values() for r in st["unsettled"] for f in r["flags"]}
    p = [json.loads(x) for q in tmp_path.rglob("*.jsonl") for x in q.read_text("utf-8").splitlines()]
    flags |= {f for r in p if r["k"] == "T" for f in r["flags"]}
    assert n_ex > 0 and {"FUNDING_GAP", "PATH_1H_GAP"} <= flags
    fw = EX.fwd_reads(tmp_path, [{"row_id": "KEL_20_2_1h@CL_MAJ", "row_type": "CLUSTER", "unit": "KEL_20_2_1h",
                                  "scope": "CL_MAJ"}])["KEL_20_2_1h@CL_MAJ"]
    k = [sts[f"KEL_20_2_1h@{c}"]["summary"] for c in ("BTC", "ETH")]
    assert fw["n"] == k[0]["n"] + k[1]["n"] and fw["excluded"] == k[0]["n_excluded"] + k[1]["n_excluded"]
    # mark fiyatı (markpx) yoksa: bir uzlaşmayı kapsayan her işlem FUNDING_GAP → kapıya ve istatistiğe girmez; içeride
    # kalan tek işlem türü uzlaşma görmeyen (fonlamasız) işlemdir
    EX.explore_step(tmp_path / "nan", _inputs(w, _night(10), mark_nan=True, markpx=False), entry_symbols=COINS40,
                    only=only)
    sn = EX.read_states(tmp_path / "nan")
    rows_nan = [json.loads(x) for q in (tmp_path / "nan").rglob("*.jsonl") for x in q.read_text("utf-8").splitlines()]
    rows_nan = [r for r in rows_nan if r["k"] == "T"] + [r for st in sn.values() for r in st["unsettled"]]
    assert sum(st["summary"]["n_excluded"] for st in sn.values()) > 0
    assert all(r["excluded"] or (r["funding_events"] == 0 and r["funding_usdt"] == 0.0) for r in rows_nan)


# ============================================================================ kapıya giden tek yol
GATE_MODULES = ("stats", "wf", "placebo", "cscv", "trials", "kernel", "night", "registry", "summary", "daily_target")


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            out.add((node.module or "") if node.level else "abs:" + (node.module or ""))
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update("abs:" + a.name for a in node.names)
    return out


def test_explore_reaches_the_gate_only_through_the_sealed_forward_read(tmp_path):
    # kapı/istatistik/defter modülleri keşfi import etmez; keşif deneme defterine yazmaz, kapıyı çağırmaz
    for m in GATE_MODULES:
        imp = _imports(PKG / f"{m}.py")
        assert "explore" not in imp and "abs:tradingbot.research_engine.explore" not in imp, m
    src = (PKG / "explore.py").read_text(encoding="utf-8")
    imp = _imports(PKG / "explore.py")
    assert "trials" not in imp and "TrialsLedger" not in src
    calls = {n.func.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"gate_a_look", "gate_a_evaluate", "append_rows", "seal_entries", "actionable_pass"}
    used = {n.attr for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id == "ST"}
    assert used == {"GATE_COST", "EQUITY_BASE_USDT", "mtm_allocation"}
    # ileri okuma: satırın KENDİ serileri; çözülemeyen satır yok (kapı FWD_MISSING)
    w = SY.explore_world(["BTC", "ETH"], seed=12, seal_ms=SEAL, fwd_days=6)
    only = ["KEL_20_2_1h", "K_D4", "O_D4_X_TIME", "DON_20_10_1d_LS"]
    EX.explore_step(tmp_path, _inputs(w, _night(6)), entry_symbols=COINS40, only=only)
    rows = RG.gate_a_rows()
    fw = EX.fwd_reads(tmp_path, rows, own_symbols={"D4": ["BTC", "ETH"]},
                      coin_fit_selection={"D@COIN_FIT:BTC": "DON_20_10_1d_LS", "D@COIN_FIT:ETH": "DON_55_20_1d_LS"})
    assert set(fw) == {"KEL_20_2_1h@CL_MAJ", "DON_20_10_1d_LS@CL_MAJ", "K_D4@COIN:BTC", "K_D4@COIN:ETH",
                       "K_D4@POOLED_OWN", "O_D4_X_TIME@POOLED_OWN", "O_D4_X_TIME@CL_MAJ", "D@COIN_FIT:BTC"}
    sts = EX.read_states(tmp_path)
    one = fw["K_D4@COIN:BTC"]
    sm = sts["K_D4@BTC"]["summary"]
    assert set(one) == {"source", "members", "n", "mean_adverse", "days", "intraday", "excluded", "frontier_ms"}
    assert (one["source"], one["members"], one["n"], one["days"], one["intraday"]) == (EX.SOURCE, 1, sm["n"],
                                                                                       sm["days_since_seal"], False)
    pool = fw["KEL_20_2_1h@CL_MAJ"]
    ks = [sts[f"KEL_20_2_1h@{c}"]["summary"] for c in ("BTC", "ETH")]
    assert pool["n"] == ks[0]["n"] + ks[1]["n"] and pool["members"] == 2
    assert pool["mean_adverse"] == pytest.approx((ks[0]["sum_r"][1] + ks[1]["sum_r"][1]) / pool["n"])
    with pytest.raises(EX.ExploreRefused):                         # donmuş seçim ailenin ızgarasından olmalı
        EX.fwd_reads(tmp_path, rows, coin_fit_selection={"D@COIN_FIT:BTC": "TS_7_LO"})
    # Kapı A (saf değerlendirme) ileri okumayı yalnız bu sözlükten alır: eşik altı → FWD_READ, yok → FWD_MISSING
    th = ST.GATE_A_PROVISIONAL
    assert pool["days"] < th["fwd_min_days"]
    ev_fails = _fwd_fails(fw, "KEL_20_2_1h@CL_MAJ")
    assert "FWD_READ" in ev_fails and "FWD_MISSING" not in ev_fails
    assert "FWD_MISSING" in _fwd_fails({}, "KEL_20_2_1h@CL_MAJ")


def _fwd_fails(fwd, rid):
    """`gate_a_evaluate`'in ileri okuma dalı (stats.py): satır istatistikleri olmadan yalnız bu koşulun kodu."""
    th = ST.GATE_A_PROVISIONAL
    f = fwd.get(rid)
    if not f:
        return ["FWD_MISSING"]
    need = th["fwd_min_days_intraday"] if f.get("intraday") else th["fwd_min_days"]
    ok = f.get("days", 0) >= need and f.get("n", 0) >= th["fwd_min_trades"] and f.get("mean_adverse") is not None \
        and f["mean_adverse"] >= th["fwd_min_mean_adverse"]
    return [] if ok else ["FWD_READ"]


def test_gate_a_evaluate_reads_fwd_reads_output_verbatim():
    """`stats.gate_a_evaluate` ileri okuma dalı `fwd_reads` biçimini aynen tüketir (FWD_READ satırına kopyalanır)."""
    src = (PKG / "stats.py").read_text(encoding="utf-8")
    assert 'f.get("days", 0) >= need_days and f.get("n", 0) >= th["fwd_min_trades"]' in src
    assert 's["fwd_read"] = dict(f) if f else None' in src


# ============================================================================ hüküm kelimesi yok (metin testi)
def _fake_state(sid, fam, n, sum_a, days=40, excl=0, open_=False):
    var, inst = sid.split("@")
    return {"schema": EX.STATE_SCHEMA, "series": sid, "variant_id": var, "instrument": inst, "family": fam,
            "status": EX.ST_OK, "summary": {"n": n, "n_excluded": excl, "sum_r": [sum_a + 1, sum_a, sum_a - 1],
                                            "mean_r": [(sum_a + 1) / n, sum_a / n, (sum_a - 1) / n] if n else None,
                                            "axis_days": days, "cum_pct": [1.0, 0.5, 0.2], "max_dd_pct": [2.0, 3.0, 4.0],
                                            "share_days_ge_1pct": [0.2, 0.1, 0.0] if days else None,
                                            "days_since_seal": days, "frontier_ms": SEAL + days * D1,
                                            "intraday": False, "open": {"side": "LONG"} if open_ else None}}


def test_rendered_explore_text_has_no_verdict_words(tmp_path):
    w = SY.explore_world(["BTC", "ETH"], seed=21, seal_ms=SEAL, fwd_days=6)
    EX.explore_step(tmp_path, _inputs(w, _night(6)), entry_symbols=COINS40, only=MIX)
    sts = EX.read_states(tmp_path)
    # uç değerli sahte seriler de: çok kazançlı, çok kayıplı, işlemsiz, kapı dışı
    sts.update({"TS_7_LO@SOL": _fake_state("TS_7_LO@SOL", "B", 120, 300.0, days=400),
                "TS_7_LS@SOL": _fake_state("TS_7_LS@SOL", "B", 120, -300.0, days=400, open_=True),
                "K_T2@SOL": _fake_state("K_T2@SOL", "K", 0, 0.0, excl=7)})
    texts = [EX.leaderboard_lines(sts, EX.read_run(tmp_path)), EX.digest_lines(sts),
             EX.query_lines(None, root=tmp_path), EX.query_lines(None, root=tmp_path, gold=True),
             EX.query_lines(None, root=tmp_path, variant="K_BOX"), EX.query_lines(None, root=tmp_path, variant="G_NY_ORB"),
             EX.query_lines(None, root=tmp_path, variant="DON_20_10_4h_LO"),
             EX.query_lines(None, root=tmp_path, variant="NOPE"), EX.query_lines(None, root=tmp_path / "empty")]
    for lines in texts:
        assert lines and EX.verdict_word_violations(lines) == [], lines
    lb = "\n".join(texts[0])
    assert EX.TOP_TITLE.format(n="3.745") in lb and "kanıt değil" in lb and "seçim yanlılığı" in lb
    assert "Altın (ayrı)" in lb and "Medyan serinin sonucu" in lb and "3.745 = 45 × 40 + 13 × 40 + 35 × 40" in lb
    # pozitif kontrol: dedektör hüküm kelimelerini (Türkçe büyük harf dahil) yakalar, betimsel ifadeleri yakalamaz
    for bad in ("Kapı A: GEÇTİ", "seri kazandı", "ONAYLANDI", "HEDEF KANITLANDI (PAPER)", "TUTTU", "GÜÇLÜ ADAY",
                "ZAYIF İZ", "KANIT YOK", "verdict PASS_A", "FAIL", "Başarılı seri", "KAYBETTİRİR", "VERİ AZ"):
        assert EX.verdict_word_violations([bad]) == [bad], bad
    for ok in ("seçim yanlılığı, kanıt değil", "mühürden bu yana geçen gün", "geçtiği", "kazanç %", "kapı dışı"):
        assert EX.verdict_word_violations([ok]) == [], ok
    with pytest.raises(ValueError, match="hüküm kelimesi"):
        EX._checked(["örnek: GEÇTİ"])


def test_engine_query_explore_is_bounded_and_read_only(tmp_path, monkeypatch, capsys):
    from tradingbot.research_engine.paths import EnginePaths
    from tradingbot.research_engine.query import run_query

    paths = EnginePaths.for_state(tmp_path / "state", tmp_path / "data")
    root = EX.explore_root(paths)
    w = SY.explore_world(["BTC", "ETH"], seed=31, seal_ms=SEAL, fwd_days=5)
    EX.explore_step(root, _inputs(w, _night(5)), entry_symbols=COINS40, only=MIX)
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*")}
    for kw in ({}, {"gold": True}, {"variant": "K_BOX"}, {"arg": "KEL_20_2_1h"}):
        lines, code = run_query(paths, topic="explore", **kw)
        assert code == 0 and len(lines) <= 150 and len("\n".join(lines).encode("utf-8")) <= 4096
        assert EX.verdict_word_violations(lines) == []
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*")} == before
    many = {f"X{i}@C": _fake_state(f"KEL_20_2_1h@C{i}", "E", 3, 1.0) for i in range(400)}
    assert len(EX._bound(EX.leaderboard_lines(many) + ["x" * 90] * 300)) <= 150
    import tradingbot.cli as cli

    def no_config(*a, **k):
        raise AssertionError("engine-query config yüklemez")
    monkeypatch.setattr(cli, "load_config", no_config)
    monkeypatch.setenv("TRADINGBOT_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TRADINGBOT_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["engine-query", "explore", "--variant", "K_BOX"]) == 0
    out = capsys.readouterr().out
    assert "KEŞİF — K_BOX" in out and "BTC · n" in out
    assert {p: p.stat().st_mtime_ns for p in root.rglob("*")} == before


# ============================================================================ ölçekli dünya (≤ 10 dk)
@pytest.mark.slow
def test_scaled_world_nightly_step_within_the_s4x_budget(tmp_path, capsys):
    """40 coin × bütün çalıştırılabilir seriler (3.720): 7 günlük birikmiş geri dönüş + bir gecelik adım; S4x bütçesi
    (§5.2) ≤ 10 dk. Ölçümler yazdırılır (§8.4 P3e notu)."""
    w = SY.explore_world(COINS40, seed=77, seal_ms=SEAL, fwd_days=10)
    t = time.monotonic()
    rep = EX.explore_step(tmp_path, _inputs(w, _night(7)), entry_symbols=COINS40)
    catch = time.monotonic() - t
    t = time.monotonic()
    rep2 = EX.explore_step(tmp_path, _inputs(w, _night(8)), entry_symbols=COINS40)
    night = time.monotonic() - t
    with capsys.disabled():
        print(f"\nP3e ölçek: geri dönüş 7 gün {catch:.1f} sn ({rep['trades_new']} işlem) · gece 1 gün {night:.1f} sn "
              f"({rep2['trades_new']} işlem, {rep2['bars']} sinyal barı) · durum {rep2['status']}")
    assert rep2["status"][EX.ST_OK] == 3720 and not rep2["errors"] and not rep["errors"]
    assert night <= 600.0 and catch <= 600.0


# ============================================================================ fonlama mark'ı = markpx 1h (koordinatör kararı)
def _mp(t0: int, n: int, rnd: random.Random, holes=()) -> list[dict]:
    out, px = [], 100.0
    for k in range(n):
        t = t0 + k * H1
        o = px
        px *= 1.0 + rnd.gauss(0.0, 0.004)
        if t not in holes:
            out.append({"timestamp": t, "open": o, "high": max(o, px) * 1.001, "low": min(o, px) * 0.999, "close": px})
    return out


def test_settlement_marks_use_markpx_open_then_close_else_gap():
    b = DAY0
    rnd = random.Random(3)
    mp = _mp(b - 2 * H1, 40, rnd, holes={b + 8 * H1, b + 16 * H1, b + 15 * H1})
    by = {r["timestamp"]: r for r in mp}
    nan = float("nan")
    rows = [(b + 3, 1e-4, nan), (b + 8 * H1, 1e-4, nan), (b + 16 * H1, 1e-4, nan),
            (b + 24 * H1 - 2, 1e-4, by[b + 24 * H1]["open"] * 1.004), (b + 32 * H1, 1e-4, by[b + 32 * H1]["open"] * 1.01)]
    got, mism = KN.settlement_marks(rows, mp)
    assert got[0][2] == by[b]["open"]                                    # o anda BAŞLAYAN barın açılışı (ms kayması)
    assert got[1][2] == by[b + 7 * H1]["close"]                          # yoksa o anda BİTEN barın kapanışı
    assert got[2][2] != got[2][2]                                        # ikisi de yok → NaN (okunamaz → FUNDING_GAP)
    assert got[3][2] == by[b + 24 * H1]["open"] and got[4][2] == by[b + 32 * H1]["open"]   # satır mark'ı yalnız denetim
    assert mism == {b + 32 * H1}                                         # %1 fark > %0,5 → işaret; %0,4 değil
    assert KN.settlement_marks(rows, None)[0][0][2] != KN.settlement_marks(rows, None)[0][0][2]   # markpx yok → NaN
    assert KN.FUNDING_MARK_SEALED is False and KN.REST_MARK_MAX_REL_DIFF == 0.005           # mühür bekliyor


@pytest.mark.parametrize("seed", range(20))
def test_funding_mark_never_uses_a_price_after_the_settlement(seed):
    """Özellik: bir uzlaşmanın mark'ı, uzlaşma anından SONRAKİ hiçbir markpx bilgisine (o anda başlayan barın
    yüksek/düşük/kapanışı, sonraki bütün barlar) bağlı değildir; yalnız o ana kadar bilinenle aynı sonucu verir."""
    rnd = random.Random(seed)
    n = 72
    holes = {DAY0 + k * H1 for k in range(n) if rnd.random() < 0.25}
    mp = _mp(DAY0, n, rnd, holes)
    sets = sorted(rnd.sample(range(2, n - 2), 8))
    rows = [(DAY0 + k * H1 + rnd.randrange(0, 900), rnd.gauss(1e-4, 1e-4), float("nan")) for k in sets]
    full, _m = KN.settlement_marks(rows, mp)
    for (t, _r, m), k in zip(full, sets):
        s_ms = DAY0 + k * H1
        known = []
        for r in mp:
            if r["timestamp"] + H1 <= s_ms:
                known.append(r)                                           # uzlaşmada kapanmış barlar aynen
            elif r["timestamp"] == s_ms:
                known.append(dict(r, high=rnd.random(), low=rnd.random(), close=rnd.random()))   # yalnız açılışı bilinir
            else:
                known.append({k2: (r[k2] if k2 == "timestamp" else rnd.uniform(1, 999)) for k2 in r})  # gelecek: çöp
        (_t, _r2, m2), = KN.settlement_marks([(t, _r, float("nan"))], known)[0]
        assert (m == m2) or (m != m and m2 != m2), (seed, k)


def test_kernel_charges_archive_rows_with_nan_mark_from_markpx():
    """Arşiv fonlama satırlarında mark yok (NaN): markpx varken çekirdek fonlamayı markpx'le yazar (satır mark'ıyla
    yazılan eski sonuçla bayt-aynı, sentetik markpx = satır mark'ı); markpx yoksa uzlaşma kapsayan işlem FUNDING_GAP;
    satır mark'ı markpx'ten > %0,5 farklıysa FUNDING_MARK_MISMATCH (bilgi) ve tutar markpx'le."""
    from dataclasses import replace

    sd = SY.fund_world(5, 1500)
    var = "DON_20_10_4h_LS"
    base = KN.run(KN.make_variant(var), sd)
    assert base.trades and any(t.funding_events for t in base.trades)
    nan_rows = [(t, r, float("nan")) for t, r, _m in sd.funding]
    same = KN.run(KN.make_variant(var), replace(sd, funding=nan_rows))
    assert [t.to_dict() for t in same.trades] == [t.to_dict() for t in base.trades]
    none = KN.run(KN.make_variant(var), replace(sd, funding=nan_rows, markpx_1h=None))
    for a, b in zip(base.trades, none.trades):
        assert (KN.FLAG_FUNDING_GAP in b.flags) == (a.funding_events > 0 or KN.FLAG_FUNDING_GAP in a.flags)
        assert b.funding_usdt == 0.0 and KN.gate_excluded(b) == (KN.FLAG_FUNDING_GAP in b.flags or KN.gate_excluded(a))
    off = KN.run(KN.make_variant(var), replace(sd, funding=[(t, r, m * 1.01) for t, r, m in sd.funding]))
    for a, b in zip(base.trades, off.trades):
        assert b.funding_usdt == a.funding_usdt and b.r == a.r
        assert (KN.FLAG_FUNDING_MARK_MISMATCH in b.flags) == (a.funding_events > 0)
        assert not KN.gate_excluded(b) or KN.gate_excluded(a)                     # bilgi işareti kapıdan çıkarmaz
    # parite başvurusunun defteri de aynı mark'ı görür
    from tradingbot.research_engine import parity as PA
    lk = PA._Lookup(KN.settlement_marks(nan_rows, sd.markpx_1h)[0])
    t0, _r0, m0 = sd.funding[10]
    from datetime import datetime, timezone
    assert float(lk.settlement_mark("X", datetime.fromtimestamp(KN.snap_ms(t0) / 1000, tz=timezone.utc))) == m0


def test_explore_funding_uses_markpx_and_counts_gate_exclusions(tmp_path):
    w = SY.explore_world(["BTC", "ETH"], seed=19, seal_ms=SEAL, fwd_days=8)
    only = ["KEL_20_2_4h", "RSI2_4h", "TS_7_LS", "MAX_20_50_4h_LS", "DON_20_10_4h_LS", "K_D4", "FUND_CARRY"]
    runs = {"row_mark": {}, "nan_mark_markpx": {"mark_nan": True}, "nan_mark_no_markpx": {"mark_nan": True, "markpx": False},
            "markpx_hole": {"mark_nan": True, "drop_markpx": [(DAY0 + 3 * D1 + 7 * H1, DAY0 + 3 * D1 + 9 * H1)]}}
    counts = {}
    for name, kw in runs.items():
        EX.explore_step(tmp_path / name, _inputs(w, _night(8), **kw), entry_symbols=COINS40, only=only)
        sts = EX.read_states(tmp_path / name)
        counts[name] = (sum(st["summary"]["n"] for st in sts.values()), sum(st["summary"]["n_excluded"] for st in sts.values()))
    # NaN satır mark'ı + markpx = satır mark'ıyla aynı dosyalar (mark kaynağı markpx; sentetikte değerler aynı)
    assert _rows_only(tmp_path / "row_mark") == _rows_only(tmp_path / "nan_mark_markpx")   # parmak izi satırı içerir
    assert counts["nan_mark_no_markpx"][1] > counts["markpx_hole"][1] >= counts["nan_mark_markpx"][1]
    assert counts["nan_mark_no_markpx"][0] < counts["nan_mark_markpx"][0]
    hole = [r for p in (tmp_path / "markpx_hole").rglob("*.jsonl") for r in map(json.loads, p.read_text("utf-8").splitlines())
            if r["k"] == "T" and "FUNDING_GAP" in r["flags"]]
    assert hole and all(r["entry_ts"] < DAY0 + 3 * D1 + 8 * H1 <= max(lg["ts"] for lg in r["legs"]) for r in hole)
