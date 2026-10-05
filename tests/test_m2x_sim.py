# -*- coding: utf-8 -*-
"""M2X simülasyonu (docs/M2_AGGRESSIVE_V1.md §3) — sentetik barlarla, ağsız:

* kol (a) üretecinin M2 kural koduyla denkliği (gerçek `StrategyBook` sürülür; açılış/çıkış günleri ve stoplar
  `ema200_trend.decide` ile birebir),
* koşum aracında kural/boyut mantığı olmadığı, sandbox dışına yazılmadığı, hafif yapı deposunun ve hızlı çerçevenin
  kararı değiştirmediği,
* M2X koşucusunun tam/seyrek besleme denkliği, açık risk tavanı ve merdiven/durdurma davranışı,
* bootstrap'ın belirlenimciliği."""
from __future__ import annotations

import ast
import inspect
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import ema200_trend, paper_rules
from tradingbot import m2x_policy as P
from tradingbot import m2x_sim as S
from tradingbot import signal_lab as L
from tradingbot.regime_gate import UP

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yaml"
SYMS = ["AAA/USDT", "BBB/USDT"]
D0 = S._ms("2022-01-01")
WIN = (S._ms("2022-10-15"), S._ms("2022-12-20"))


def _hourly(seed: int, *, base: float, drift: float, amp: float, period_days: float, crash_at: int | None = None) -> pd.DataFrame:
    n = int((S._ms("2023-01-01") - D0) // S.HOUR_MS)
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=np.int64) * S.HOUR_MS + D0
    x = np.arange(n) / 24.0
    logp = np.log(base) + drift * x + amp * np.sin(2 * np.pi * x / period_days) + np.cumsum(rng.normal(0, 0.002, n))
    if crash_at is not None:
        logp[crash_at:] -= 0.6                               # ani %45'lik düşüş (stop/likidasyon yolu)
    c = np.exp(logp)
    o = np.concatenate([[c[0]], c[:-1]])
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.002, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.002, n)))
    return pd.DataFrame({"timestamp": t, "open": o, "high": hi, "low": lo, "close": c, "volume": 1000.0,
                         "close_time": t + S.HOUR_MS - 1})


def _daily(h: pd.DataFrame) -> pd.DataFrame:
    d = h.assign(day=(h["timestamp"] // S.DAY_MS) * S.DAY_MS).groupby("day").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    d = d.reset_index().rename(columns={"day": "timestamp"})
    d["close_time"] = d["timestamp"] + S.DAY_MS - 1
    return d


def _write_cache(cache: Path, frames: dict[str, pd.DataFrame]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for s, h in frames.items():
        h.to_csv(L.cache_path(cache, s, "1h"), index=False, compression="gzip")
        _daily(h).to_csv(L.cache_path(cache, s, "1d"), index=False, compression="gzip")
        ft = np.arange(D0, S._ms("2023-01-01"), 8 * S.HOUR_MS, dtype=np.int64)
        pd.DataFrame({"t_ms": ft, "rate": 0.0001, "interval_file": 8.0}).to_csv(S.funding_cache_path(cache, s), index=False,
                                                                                 compression="gzip")
        h[["timestamp", "open", "close"]].to_csv(S.mark_cache_path(cache, s), index=False, compression="gzip")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    root = tmp_path_factory.mktemp("m2x")
    cache = root / "cache"
    _write_cache(cache, {"BTC/USDT": _hourly(1, base=20000, drift=0.002, amp=0.02, period_days=60),
                         "AAA/USDT": _hourly(2, base=100, drift=0.0005, amp=0.18, period_days=45),
                         "BBB/USDT": _hourly(3, base=5, drift=0.001, amp=0.12, period_days=33, crash_at=7500)})
    data = S.ArchiveData(SYMS, cache)
    return {"root": root, "cache": cache, "data": data}


def _harness(synth, name: str, **kw):
    h = S.M2Harness(config_path=CONFIG, data=synth["data"], run_dir=synth["root"] / name, **kw)
    h.universe = list(SYMS)
    return h


@pytest.fixture(scope="module")
def stream_off(synth):
    """Yapı katmanı KAPALI M2 (kural denkliği için): öğrenme modu açık, config.yaml'ın geri kalanı aynen."""
    h = _harness(synth, "run_off")
    h.book.structure_mode = "OFF"
    return h, h.run(*WIN)


@pytest.fixture(scope="module")
def stream_cfg(synth):
    """config.yaml'daki gibi (yapı ENFORCE + öğrenme giriş gölgesi)."""
    h = _harness(synth, "run_cfg")
    return h, h.run(*WIN)


# ---------------------------------------------------------------------------- E1: üreteç = M2 kod yolu
def _rule(data, sym, now_ms, held):
    frames = {"1d": data.daily_frame(sym, now_ms, full=True)}
    btc = paper_rules.daily_rows({"1d": data.daily_frame("BTC/USDT", now_ms, full=True)}, now_ms=now_ms)
    rows = paper_rules.daily_rows(frames, now_ms=now_ms)
    return ema200_trend.decide("m2_tsmom28", daily_rows=rows, btc_daily_rows=btc, position_open=held, atr_mult=3.0)


def test_generator_matches_m2_rule_code_on_synthetic_bars(synth, stream_off):
    _h, st = stream_off
    data = synth["data"]
    by_t = S.stream_index(st)
    held: dict[str, str] = {}
    n_open = n_rule_close = 0
    for d in st["days"]:
        T = int(d["t"])
        evs = by_t.get(T, [])
        opened = {e["symbol"]: e for e in evs if e["kind"] == "open" and e["phase"] == "step"}
        rule_closed = {e["symbol"]: e for e in evs if e["kind"] == "close" and e["phase"] == "step"}
        for s in SYMS:
            act = _rule(data, s, T, s in held)
            a = str((act or {}).get("action") or "NONE")
            if s in held:
                assert (a == "CLOSE") == (s in rule_closed), (s, S._iso_ms(T), act)
                if s in rule_closed:
                    assert rule_closed[s]["reason"] == "M2_TSMOM28_CROSS_DOWN"
                    n_rule_close += 1
            else:
                assert (a == "OPEN") == (s in opened), (s, S._iso_ms(T), act)
                if s in opened:
                    assert opened[s]["stop"] == pytest.approx(float(act["stop"]), rel=1e-12)
                    assert act["regime"] == UP
                    n_open += 1
        S._apply_m2(held, evs)
        for k in range(1, 25):
            S._apply_m2(held, by_t.get(int(d["day"]) + k * S.HOUR_MS, []))
    assert n_open >= 3 and n_rule_close >= 1


def test_generator_opens_are_rule_opens_with_structures_enforced(synth, stream_cfg):
    _h, st = stream_cfg
    data = synth["data"]
    opens = [e for e in st["events"] if e["kind"] == "open"]
    assert opens
    for e in opens:
        act = _rule(data, e["symbol"], int(e["t"]), False)
        assert act and act["action"] == "OPEN" and e["stop"] == pytest.approx(float(act["stop"]), rel=1e-12)


def test_learning_view_comes_from_engine_constructor(stream_off):
    h, st = stream_off
    assert st["learning"]["book"]["slots"] == 40 and st["learning"]["book"]["leverage_max"] == 4
    assert st["learning"]["risk_pct"] == 0.5 and st["learning"]["extra_entries"] == "record_selectivity"
    assert all(d["learning_on"] for d in st["days"])


def test_harness_has_no_rule_logic():
    src = inspect.getsource(S.M2Harness)
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    forbidden = {"decide", "decide_for", "decide_from_rows", "decide_with_structures", "read_daily", "fit_size",
                 "fit_with_reserve", "apply_action", "trend_decide", "btc_regime", "exit_measure", "close_manual"}
    assert not (names & forbidden), names & forbidden
    # deftere yalnız StrategyBook'un kendi yolları dokunur
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "book"}
    assert calls <= {"step", "apply_closed_bars", "tick", "reconcile_funding", "bind_funding"}, calls


def test_fast_daily_frame_matches_full(synth):
    data = synth["data"]
    for s in SYMS + ["BTC/USDT"]:
        for now in (S._ms("2022-09-01") + S.TOUR_OFFSET_MS, S._ms("2022-12-01") + S.TOUR_OFFSET_MS):
            a = data.daily_frame(s, now)
            b = data.daily_frame(s, now, full=True)
            ra = ema200_trend.daily_rows_from_frame(a, tail=320)
            rb = ema200_trend.daily_rows_from_frame(b, tail=320)
            assert len(ra) == len(rb)
            pd.testing.assert_frame_equal(pd.DataFrame(ra), pd.DataFrame(rb), check_exact=True)
            assert int(a["timestamp"].iloc[-1]) + S.DAY_MS <= now        # kapanmamış bar düşer
            j = int(np.searchsorted(data.d1_t[s], now, side="right"))
            assert len(a) == min(data.window, j) - 1                     # canlı pencere (açık bar dahil 400) − açık bar


def test_light_store_is_decision_neutral(synth, stream_cfg):
    _h, st = stream_cfg
    h2 = _harness(synth, "run_cfg_realstore", light_store=False)
    st2 = h2.run(*WIN)
    assert S.trade_list_key(st) == S.trade_list_key(st2)
    assert (synth["root"] / "run_cfg_realstore" / "state" / "structures" / "decisions_latest.json").exists()


def test_sandbox_writes_only_under_run_dir(synth, tmp_path):
    before = {p for p in synth["root"].rglob("*")}
    state_dir = ROOT / "state"
    existed = state_dir.exists()
    snap = {p: p.stat().st_mtime for p in state_dir.rglob("*")} if existed else {}
    h = _harness(synth, "run_sandbox")
    h.run(WIN[0], WIN[0] + 10 * S.DAY_MS)
    new = {p for p in synth["root"].rglob("*")} - before
    assert new and all(str(p).startswith(str(synth["root"] / "run_sandbox")) for p in new)
    assert state_dir.exists() == existed
    if existed:
        assert {p: p.stat().st_mtime for p in state_dir.rglob("*")} == snap
    assert h.cfg.state_path == (synth["root"] / "run_sandbox" / "state").resolve()


def test_cf_on_off_trade_lists_compared(synth):
    lists = []
    for cf in (True, False):
        h = _harness(synth, "run_cf_%s" % cf, counterfactual=cf)
        lists.append(S.trade_list_key(h.run(WIN[0], WIN[0] + 25 * S.DAY_MS)))
    assert lists[0] == lists[1]


# ---------------------------------------------------------------------------- M2X koşucusu
def test_sparse_feed_equals_full_feed_without_funding(synth, stream_off):
    h, st = stream_off
    data = synth["data"]
    params = S.LedgerParams.from_v3(h.cfg.v3)
    full = S.run_historical_arm(S.ARMS["b"], st, data, params, with_funding=False, end_ms=WIN[1])
    lib = S.trade_library(st, data, start_ms=WIN[0], end_ms=WIN[1])
    n = lib["n_days"]
    sparse = S.run_boot_path(lib, np.array([0]), block=n, horizon=n, arm=S.ARMS["b"], params=params, keep_series=True)
    assert len(full["entries"]) == sparse["n_entries"] > 0
    m2id = {"T%d_%d" % (i, t["day"]): t["m2_id"] for i, t in enumerate(lib["trades"])}
    fc = sorted((c["m2_id"], c["reason"], round(c["r"], 6)) for c in full["closes"])
    sc = sorted((m2id[c["m2_id"]], c["reason"], round(c["r"], 6)) for c in sparse["closes"])
    assert fc == sc
    fe = [s["equity"] for s in full["snapshots"]] + [full["final"]["equity"]]
    assert len(fe) == len(sparse["equity"])
    assert np.allclose(fe, sparse["equity"], rtol=1e-7, atol=1e-7)


def _cand(i, sym, stop=0.9, order=None):
    return {"id": "M%d" % i, "symbol": sym, "ref": 1.0, "stop": stop, "order": order or i}


def test_open_risk_cap_scales_simultaneous_candidates():
    run = S.M2xRunner(S.ARMS["b"], params=S.LedgerParams(), record_hourly=False)
    syms = ["S%d/USDT" % i for i in range(20)]
    opens = [_cand(i, s, stop=0.88) for i, s in enumerate(syms)]           # s ≈ %12 → L_liq 4, CLᵢ ≈ 2,1 d
    m2_open = {s: "M%d" % i for i, s in enumerate(syms)}
    run.on_tour(S.BOOT_BASE_MS, marks={s: 1.0 for s in syms}, rule_closes=[], opens=opens, m2_open=m2_open, u=0.0)
    snap = run.snapshots[-1]
    assert snap["binding"] == "OR"
    # 20 aday × tam boy (%2) = %40 > %20 tavan → λ = 0,5, hepsi yarım boy
    assert len(run.entries) == 20 and all(e["scale"] == pytest.approx(0.5, rel=1e-6) for e in run.entries)
    assert snap["or"] <= 0.20 * snap["equity"] * (1 + 1e-6)
    # ertesi gün yeni aday: OR boşluğu yok → atlanır
    run.on_tour(S.BOOT_BASE_MS + S.DAY_MS, marks={**{s: 1.0 for s in syms}, "Z/USDT": 1.0}, rule_closes=[],
                opens=[_cand(99, "Z/USDT")], m2_open={**m2_open, "Z/USDT": "M99"}, u=0.0)
    assert run.skips[-1]["reason"] == P.SKIP_OR


def test_many_candidates_below_quarter_scale_are_taken_in_m2_order():
    run = S.M2xRunner(S.ARMS["b"], params=S.LedgerParams(), record_hourly=False)
    syms = ["S%d/USDT" % i for i in range(60)]
    opens = [_cand(i, s, stop=0.88, order=60 - i) for i, s in enumerate(syms)]   # M2'nin sırası ters
    m2_open = {s: "M%d" % i for i, s in enumerate(syms)}
    run.on_tour(S.BOOT_BASE_MS, marks={s: 1.0 for s in syms}, rule_closes=[], opens=opens, m2_open=m2_open, u=0.0)
    # λ = 20 / 120 < 0,25 → sırayla 0,25 boy (0,5 OR) → 40 aday; kalan 20 atlanır
    assert len(run.entries) == 40 and all(e["scale"] == 0.25 for e in run.entries)
    taken = {e["symbol"] for e in run.entries}
    assert taken == {"S%d/USDT" % i for i in range(20, 60)}
    assert sum(1 for s in run.skips if s["reason"] == P.SKIP_OR) == 20


def test_crash_budget_binds_with_wide_stops():
    run = S.M2xRunner(S.ARMS["b"], params=S.LedgerParams(), record_hourly=False)
    syms = ["W%d/USDT" % i for i in range(12)]
    # stop ≈ %19 → kaldıraç 2, likidasyon ~%50 → CLᵢ ≈ 0,501 × d / 0,19 ≈ 2,6 d > OR tavanı/bütçe oranı (2,5)
    opens = [_cand(i, s, stop=0.81) for i, s in enumerate(syms)]
    m2_open = {s: "M%d" % i for i, s in enumerate(syms)}
    run.on_tour(S.BOOT_BASE_MS, marks={s: 1.0 for s in syms}, rule_closes=[], opens=opens, m2_open=m2_open, u=0.0)
    assert run.snapshots[-1]["binding"] == "CL"
    s = run.snapshots[-1]
    assert s["equity"] - 0.5 * s["peak"] - s["cl"] >= -1e-6


def test_min_notional_bump_opens_at_the_smallest_order():
    """§2.6: risk boyutu en küçük emrin altında kalan aday en küçük emre ÇIKARILIR (miktar adıma YUKARI) ve defter onu
    reddetmez. 2026-10-05 düzeltmesi: miktar adımı ve en küçük miktar `fit_size`a verilmiyordu; çıkarılmış notional
    defterde adıma AŞAĞI yuvarlanınca 4,999… < 5 olup her çıkarma `M2X_LEDGER_MIN_NOTIONAL` ile reddediliyordu."""
    run = S.M2xRunner(S.ARMS["p"], params=S.LedgerParams(), record_hourly=False)
    # (p) K1: risk = %20 / 28 ≈ %0,714 → d ≈ 1,43 USDT. X: s ≈ %30 → notional ≈ 4,76 < 5; ETH (varsayım 20): s ≈ %10 → ≈ 14,3 < 20
    opens = [_cand(1, "X/USDT", stop=0.7), _cand(2, "ETH/USDT", stop=0.9)]
    run.on_tour(S.BOOT_BASE_MS, marks={"X/USDT": 1.0, "ETH/USDT": 1.0}, rule_closes=[], opens=opens,
                m2_open={"X/USDT": "M1", "ETH/USDT": "M2"}, u=0.0)
    assert not run.skips
    assert {e["symbol"]: e["size_rule"] for e in run.entries} == {"X/USDT": "BUMP_MIN_NOTIONAL", "ETH/USDT": "BUMP_MIN_NOTIONAL"}
    e0 = run.snapshots[-1]["equity"]
    cap = min(2 * P.Knobs(proportional_n=28).tier_risk_pct(0), 2.0) / 100.0 * e0
    for sym, mn in (("X/USDT", 5.0), ("ETH/USDT", 20.0)):
        p = run.ledger.positions[sym]
        notional = float(p.qty) * float(p.entry_avg)
        assert mn <= notional < mn * (1 + 1e-9)                    # en küçük emir; fazlası değil
        assert next(e for e in run.entries if e["symbol"] == sym)["risk_usdt"] <= cap * (1 + 1e-9)
    # tavanı aşan çıkarma açılmaz, politika nedeniyle atlanır (defter reddi değil)
    run2 = S.M2xRunner(S.ARMS["p"], params=S.LedgerParams(), record_hourly=False)
    run2.on_tour(S.BOOT_BASE_MS, marks={"ETH/USDT": 1.0}, rule_closes=[], opens=[_cand(3, "ETH/USDT", stop=0.8)],
                 m2_open={"ETH/USDT": "M3"}, u=0.0)
    assert not run2.entries and [s["reason"] for s in run2.skips] == [P.SKIP_MIN_NOTIONAL]


def test_ladder_cuts_risk_and_halt_stops_entries():
    run = S.M2xRunner(S.ARMS["b"], params=S.LedgerParams(), record_hourly=False)
    t = S.BOOT_BASE_MS
    run.on_tour(t, marks={"A/USDT": 1.0}, rule_closes=[], opens=[_cand(1, "A/USDT", stop=0.6)], m2_open={"A/USDT": "M1"}, u=0.0)
    # piyasa düşer: tek pozisyon %2 risk; özkaynağı elle düşürmek için cüzdanı azalt (yalnız test)
    from decimal import Decimal
    run.ledger.wallet_balance -= Decimal("40")                 # DD ≈ %20 → K2
    run.on_tour(t + S.DAY_MS, marks={"A/USDT": 1.0, "B/USDT": 1.0}, rule_closes=[], opens=[_cand(2, "B/USDT", stop=0.9)],
                m2_open={"A/USDT": "M1", "B/USDT": "M2"}, u=0.0)
    assert run.st.tier == 1
    e = [x for x in run.entries if x["symbol"] == "B/USDT"][0]
    assert e["tier"] == "K2" and e["risk_usdt"] == pytest.approx(0.01 * run.snapshots[-1]["equity"], rel=1e-3)
    run.ledger.wallet_balance -= Decimal("60")                 # DD ≥ %50 → DUR
    run.on_tour(t + 2 * S.DAY_MS, marks={"A/USDT": 1.0, "B/USDT": 1.0, "C/USDT": 1.0}, rule_closes=[],
                opens=[_cand(3, "C/USDT")], m2_open={"A/USDT": "M1", "B/USDT": "M2", "C/USDT": "M3"}, u=0.0)
    assert run.st.halted and run.skips[-1]["reason"] == P.SKIP_HALTED


def test_parent_closed_and_orphan_mirror():
    run = S.M2xRunner(S.ARMS["b"], params=S.LedgerParams(), record_hourly=False)
    t = S.BOOT_BASE_MS
    run.on_tour(t, marks={"A/USDT": 1.0, "B/USDT": 1.0}, rule_closes=[], opens=[_cand(1, "A/USDT"), _cand(2, "B/USDT")],
                m2_open={"A/USDT": "M1"}, u=0.0)
    assert [s["reason"] for s in run.skips] == [P.SKIP_PARENT_CLOSED]
    run.on_tour(t + S.DAY_MS, marks={"A/USDT": 1.1}, rule_closes=[("M1", "A/USDT", 1.1, "M2_TSMOM28_CROSS_DOWN")], opens=[],
                m2_open={}, u=0.0)
    assert run.closes[-1]["how"] == "mirror" and run.closes[-1]["reason"].startswith("MIRROR_PARENT_CLOSED")
    run.on_tour(t + 2 * S.DAY_MS, marks={"C/USDT": 1.0}, rule_closes=[], opens=[_cand(3, "C/USDT")], m2_open={"C/USDT": "M3"},
                u=0.0)
    run.on_tour(t + 3 * S.DAY_MS, marks={"C/USDT": 1.0}, rule_closes=[], opens=[], m2_open={}, u=0.0)
    assert run.closes[-1]["how"] == "orphan" and run.divergence.get("orphan") == 1


# ---------------------------------------------------------------------------- bootstrap
def test_bootstrap_is_deterministic(synth, stream_off):
    h, st = stream_off
    lib = S.trade_library(st, synth["data"], start_ms=WIN[0], end_ms=WIN[1])
    params = S.LedgerParams.from_v3(h.cfg.v3)
    a = S.bootstrap_serial(lib, arm="b", horizon=40, block=10, n_paths=4, params=params, seed=7)
    b = S.bootstrap_serial(lib, arm="b", horizon=40, block=10, n_paths=4, params=params, seed=7)
    assert a == b
    s1 = S.boot_starts(lib["n_days"], horizon=40, block=10, n_paths=4, seed=7)
    s2 = S.boot_starts(lib["n_days"], horizon=40, block=10, n_paths=4, seed=8)
    assert s1.shape == (4, 4) and not np.array_equal(s1, s2)
    agg = S.boot_aggregate(a)
    assert agg["n_paths"] == 4 and 0.0 <= agg["halt_freq"] <= 1.0


def test_series_metrics_months_and_drawdown():
    t0 = S._ms("2023-01-01") + S.TOUR_OFFSET_MS
    pts = [(t0 + i * S.DAY_MS, 100.0 * (1.02 ** i if i < 40 else 1.02 ** 40 * 0.8)) for i in range(70)]
    m = S.series_metrics(pts)
    assert m["months"][0]["period"] == "2023-01"
    assert m["months"][0]["return"] == pytest.approx(1.02 ** 31 - 1, rel=1e-9)
    assert m["max_dd_daily"] == pytest.approx(1 - 1.02 * 0.8, rel=1e-9)
    assert m["daily"]["share_ge_1pct"] > 0.5 and math.isfinite(m["cagr"])
