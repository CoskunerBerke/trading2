# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2b — karşı-olgusal ızgara `cfgrid_v1` ve R ayrıştırması (docs/SYSTEM_LEARNING_ENGINE_V1.md §5.5,
§5.6; P2 kabul 7'nin ayrıştırma yarısı).

* Kanca kopyası (`_replay_ext`) kancasız çağrıldığında `learning_cf._net_replay` ile AYNI sonucu verir (aynı kod yolu).
* Sentetik altın yol "stop oldu, sonra döndü": dar stoplu hücreler kaybeder, geniş stoplu hücreler kâra döner; en iyi
  hücre HINDSIGHT etiketlidir; limit dolmazsa R = 0 (`MISSED`).
* Ayrıştırma: sıralı zincir bileşenleri hücre medyanlarından AYNEN hesaplanır ve `bileşenler + artık = net_R` (1e-6);
  artık = rastgele taban + yeniden oynatma farkı.
* Eşleşmiş rastgele kontrol: aynı gün saati, aynı kova, sabit tohum (aynı işlem → aynı seçim), 20 giriş.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from tradingbot.research_engine import cfgrid as CG
from tradingbot.research_engine import fidelity as FD

#: §9.2 mühür: `cfgrid_v1` tanımının sha256'sı (P2 uygulama notlarında da yazılıdır). DEĞİŞİRSE CI kırılır.
CFGRID_SHA_PINNED = "4a96df71a989bcf9c5f81317c01e534920105ced1785dc9389240502abc9454d"
UTC = timezone.utc
T0 = int(datetime(2026, 9, 20, 10, 0, 0, 213000, tzinfo=UTC).timestamp() * 1000)
M1, M5 = 60_000, 300_000


def model(**over):
    from tradingbot.accounting import FuturesLedgerV2, SlippageModel
    led = FuturesLedgerV2(Decimal("10000"), slippage=SlippageModel(fixed_bps=Decimal("3")))
    par = FD.exec_params(led.to_dict())
    par.update(over)
    return FD.exec_model(par)


def path(points, tail=(), *, step=M1, start=None):
    start = T0 if start is None else start
    first = (start // step + 1) * step
    rows = [[first + i * step, o, h, lo, c, 0] for i, (o, h, lo, c) in enumerate(points)]
    n = len(points)
    rows += [[first + (n + j) * step, o, h, lo, c, 1] for j, (o, h, lo, c) in enumerate(tail)]
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "phase"])
    df["phase"] = df["phase"].astype("int8")
    return df


def plan(bars, *, exit_reason="stop", net_r=-1.05, gross_pre=-1.0, targets=(101.5,), atr=1.0, book="strategy_paper_box",
         closed_ms=None):
    row = {"trade_key": f"{book}|T9|x", "book": book, "symbol": "SOL/USDT", "side": "LONG", "opened_at": _iso(T0),
           "closed_at": _iso(closed_ms or int(bars[bars["phase"] == 0]["timestamp"].max()) + M1), "initial_stop": "99.00",
           "ref_price": "100.00", "entry_fill": "100.03", "net_r": repr(net_r), "targets": list(targets),
           "exit_reason": exit_reason, "leverage": 3, "notional": "300",
           "gross_r": repr(gross_pre), "cost_r": {"fee": "0.05", "funding": "0", "slippage_in_fills": "0"}}
    p, err = CG.trade_plan(row, bars, atr=atr)
    assert p is not None, err
    return p


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def env(p, bars, **kw):
    return CG.GridEnv(plan=p, bars=bars, tf="1m", entry_bar=None, model=kw.pop("m", model()),
                      funding=CG.ChainFunding(CG.recorded_funding({}), None), **kw)


# ============================================================================ aynı kod yolu
def test_replay_ext_without_hooks_equals_net_replay():
    from tradingbot.learning_cf import _net_replay
    rng = random.Random(7)
    for k in range(12):
        px, pts = 100.0, []
        for _ in range(rng.randint(5, 60)):
            o = px
            c = max(1.0, o * (1 + rng.gauss(0, 0.004)))
            h, lo = max(o, c) * (1 + abs(rng.gauss(0, 0.002))), min(o, c) * (1 - abs(rng.gauss(0, 0.002)))
            pts.append((round(o, 2), round(h, 2), round(lo, 2), round(c, 2)))
            px = c
        df = path(pts)[["timestamp", "open", "high", "low", "close"]]
        side = "LONG" if k % 2 == 0 else "SHORT"
        sg = 1 if side == "LONG" else -1
        stop = round(100 - sg * rng.uniform(0.3, 1.5), 2)
        tg = [round(100 + sg * rng.uniform(0.5, 2.5), 2)] * (k % 3 > 0) + [round(100 + sg * 3.0, 2)] * (k % 3 == 2)
        m = model(tp1_fraction="0.5", breakeven_at_mfe_r="1" if k % 4 == 0 else "0")
        v = CG._view(symbol="SOL/USDT", side=side, created_ms=T0, entry=100.0, stop=stop, targets=tg,
                     label_ms=int(df["timestamp"].max()), tf="1m", n=len(df), pre_filled=bool(k % 5 == 0))
        a = _net_replay(v, df, {"bars": len(df), "exit_reason": "horizon"}, 0.0, model=m, filters=None, funding_lookup=None)
        b = CG._replay_ext(v, df, {"bars": len(df), "exit_reason": "horizon"}, model=m, funding_lookup=None)
        a.pop("label_version", None)
        assert a == b, (k, a, b)


# ============================================================================ altın yol: stop oldu, sonra döndü
STOP_REVERSE = path([(100.0, 100.2, 99.6, 99.7), (99.7, 99.8, 98.8, 98.9)],
                    tail=[(98.9, 99.6, 98.6, 99.5), (99.5, 100.8, 99.4, 100.7), (100.7, 102.0, 100.6, 101.9)])


def test_golden_stopped_then_reversed_grid():
    p = plan(STOP_REVERSE)
    assert p.level_exit and p.horizon_ms == int(STOP_REVERSE["timestamp"].max()) + 1
    cells = CG.run_grid(env(p, STOP_REVERSE))
    r = {k: c.r_net for k, c in cells.items()}
    assert r["E_ACTUAL"] < -0.95 and cells["E_ACTUAL"].exit_reason == "stop"
    assert r["S_ATR075"] < -0.95 and r["S_ATR100"] < -0.95                     # stop 99,25 / 99 → stop
    assert r["S_ATR150"] > 0.8 and cells["S_ATR150"].exit_reason == "hedef1"     # stop 98,5 → hedef 101,5 (risk 1,5)
    assert r["S_ATR200"] > 0.65 and cells["S_ATR200"].exit_reason == "hedef1"    # stop 98 → (101,5−100)/2 ≈ 0,75R
    assert cells["SKIP"].r_net == 0.0 and cells["SKIP"].label == CG.EX_ANTE
    assert cells["E_LIMIT_025"].status == "OK" and cells["E_LIMIT_025"].entry == 99.75
    out = CG.evaluate(env(p, STOP_REVERSE), None)
    hb = out["hindsight_best"]
    assert hb["label"] == CG.HINDSIGHT and hb["cell"] == "S_ATR150" and "kural DEĞİL" in hb["note"]
    assert {"S_ATR150", "S_ATR200"} <= set(out["profitable_cells_hindsight"])
    assert [c["id"] for c in out["cells"]] == list(CG.VARIANT_IDS) + ["Z_ACTUAL_LEV"]
    d = out["decomposition"]
    assert d["identity_ok"] and abs(d["residual_R"] + sum(v for v in d["components"].values() if v is not None) - p.net_r) < 1e-6
    assert d["missing"] == ["signal_R"]                                          # rastgele kontrol yok → sinyal yok


def test_limit_entry_unfilled_is_missed_with_zero_r_and_delay_uses_entry_tf():
    up = path([(100.0, 100.3, 99.9, 100.2)] + [(100.2 + i * 0.1, 100.4 + i * 0.1, 100.1 + i * 0.1, 100.3 + i * 0.1)
                                              for i in range(12)])
    p = plan(up, exit_reason="hedef1", net_r=1.4, gross_pre=1.5, targets=(101.5,))
    cells = CG.run_grid(env(p, up))
    c = cells["E_LIMIT_025"]
    assert c.status == "MISSED" and c.r_net == 0.0 and c.g == 0.0 and "kaçan" in c.note
    d = cells["E_DELAY1"]                                 # Box giriş dilimi 5m → ilk ≥ açılış + 5 dk barının açılışı
    first5 = int(up[up["timestamp"] >= T0 + M5]["timestamp"].iloc[0])
    assert d.status == "OK" and d.entry == float(up[up["timestamp"] == first5]["open"].iloc[0])
    p2 = plan(up, exit_reason="hedef1", net_r=1.4, gross_pre=1.5, targets=(101.5,), book="strategy_paper")   # 1d
    assert CG.run_grid(env(p2, up))["E_DELAY1"].status == "NO_BARS"


def test_rule_exit_horizon_is_the_actual_exit_time():
    bars = path([(100.0, 100.3, 99.8, 100.1)] * 10, tail=[(100.1, 103.0, 100.0, 102.9)] * 5)
    closed = int(bars[bars["phase"] == 0]["timestamp"].max()) + M1
    p = plan(bars, exit_reason="BOX_EOD_FLAT", net_r=0.05, gross_pre=0.1, closed_ms=closed)
    assert not p.level_exit and p.horizon_ms == closed
    cells = CG.run_grid(env(p, bars))
    assert all(c.exit_reason in (None, "horizon") for c in cells.values() if c.status == "OK"), \
        {k: c.exit_reason for k, c in cells.items()}                       # kuyruktaki 103 hiçbir hücreye girmez


# ============================================================================ ayrıştırma (saf altın)
def _cells(gs: dict[str, float]) -> dict[str, CG.Cell]:
    return {k: CG.Cell(k, "x", CG.EX_ANTE if k != "Z_ACTUAL_LEV" else CG.REFERENCE, status="OK", r_net=v, g=v)
            for k, v in gs.items()}


def test_golden_decomposition_chain_and_residual():
    g = {vid: 0.0 for vid in CG.VARIANT_IDS}
    g.update({"E_ACTUAL": -1.0, "E_DELAY1": 0.4, "E_LIMIT_025": 0.6, "S_ATR075": -1.0, "S_ATR100": -1.0, "S_ATR150": 1.0,
              "S_ATR200": 0.75, "X_T1R": -1.0, "X_T2R": -1.0, "X_T3R": -1.0, "X_TRAIL2ATR": -0.5, "X_NONE_TIME": -1.0,
              "M_TP1_ON": -1.0, "M_TP1_OFF": -1.0, "M_BE1R_ON": -1.0, "M_BE_OFF": -1.0, "Z_HALF_LEV": -1.0,
              "Z_ACTUAL_LEV": -1.0})
    p = plan(STOP_REVERSE, net_r=-1.07, gross_pre=-1.02)
    rc = {"status": "OK", "median_g": -0.2, "median_r_net": -0.25}
    d = CG.decompose(p, _cells(g), rc)
    ex = [g[v] for v in CG.VARIANT_IDS if v != "SKIP"]
    x1 = sorted(ex)[len(ex) // 2] if len(ex) % 2 else (sorted(ex)[len(ex) // 2 - 1] + sorted(ex)[len(ex) // 2]) / 2
    c = d["components"]
    assert d["chain"]["X1"] == round(x1, 6) and c["signal_R"] == round(x1 - (-0.2), 6)
    assert d["chain"]["X2"] == -1.0 and c["timing_R"] == round(-1.0 - x1, 6)       # girişi gerçek hücrelerin medyanı
    assert d["chain"]["X3"] == -1.0 and c["stop_R"] == 0.0 and c["exit_R"] == 0.0 and c["size_lev_R"] == 0.0
    assert c["cost_R"] == round(-1.07 - (-1.02), 6)
    total = sum(v for v in c.values() if v is not None)
    assert abs(d["residual_R"] + total - (-1.07)) < 1e-9 and d["identity_ok"]
    # artık = rastgele taban + yeniden oynatma farkı (zincir teleskopik)
    rp = d["residual_parts"]
    assert abs(d["residual_R"] - (rp["random_baseline_R"] + rp["replay_gap_R"])) < 1e-6
    # kontrol yoksa sinyal eksik ve artığa kalır (gizlenmez)
    d2 = CG.decompose(p, _cells(g), {"status": "RC_TOO_FEW"})
    assert d2["components"]["signal_R"] is None and d2["missing"] == ["signal_R"] and d2["identity_ok"]


# ============================================================================ eşleşmiş rastgele kontrol
def _h4(days=200, end=T0):
    rng = random.Random(3)
    step = 14_400_000
    n = days * 6
    start = (end // step) * step - n * step
    rows, px = [], 100.0
    for i in range(n):
        o = px
        c = o * (1 + rng.gauss(0.0004, 0.01))
        rows.append([start + i * step, o, max(o, c) * 1.003, min(o, c) * 0.997, c, 100.0 + i % 7])
        px = c
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _five(a, b):
    first = (a // M5) * M5
    ts = list(range(first, b + 1, M5))
    rng = random.Random(first)
    rows, px = [], 100.0
    for t in ts:
        o = px
        c = o * (1 + rng.gauss(0, 0.002))
        rows.append([t, o, max(o, c) * 1.001, min(o, c) * 0.999, c])
        px = c
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])


def test_random_control_same_time_of_day_same_bucket_deterministic():
    p = plan(STOP_REVERSE)
    h4 = _h4()
    dur = p.horizon_ms - p.opened_ms
    own, cands = CG.rc_candidates(p, h4, dur)
    assert own is not None and len(cands) >= CG.RC_N
    assert all((p.opened_ms - t) % 86_400_000 == 0 and t + dur < p.opened_ms for t in cands)
    assert all(CG.bucket_at(h4, t - 1) == own for t in cands)
    cache: dict = {}
    assert CG.rc_candidates(p, h4, dur, cache=cache) == (own, cands) == CG.rc_candidates(p, h4, dur, cache=cache)
    picks = CG.rc_pick(p, cands)
    assert picks == CG.rc_pick(p, list(reversed(cands))) and len(picks) == CG.RC_N
    rc = CG.random_control(p, h4=h4, bars_5m=_five, model=model(), funding=None)
    assert rc["status"] == "OK" and rc["n"] == CG.RC_N and rc["median_r_net"] is not None
    assert rc == CG.random_control(p, h4=h4, bars_5m=_five, model=model(), funding=None)       # sabit tohum
    few = CG.random_control(p, h4=h4, bars_5m=lambda a, b: None, model=model(), funding=None)
    assert few["status"] == "RC_TOO_FEW" and few["median_g"] is None


def test_coarsen_keeps_the_real_path_extremes():
    bars = path([(100 + i * 0.01, 100.5 + i * 0.01, 99.5 + i * 0.01, 100 + i * 0.01) for i in range(1000)])
    out, tf = CG.coarsen(bars, "1m")
    assert tf == "5m" and len(out) <= CG.REPLAY_MAX_BARS
    assert out["high"].max() == bars["high"].max() and out["low"].min() == bars["low"].min()
    same, tf2 = CG.coarsen(bars.head(100), "1m")
    assert tf2 == "1m" and len(same) == 100


def test_cfgrid_registry_is_sealed():
    assert CG.CFGRID_SHA == CFGRID_SHA_PINNED, CG.CFGRID_SHA
    spec = CG.CFGRID_SPEC
    assert spec["id"] == "cfgrid_v1" and spec["stop_atr"] == [0.75, 1.0, 1.5, 2.0] and spec["target_r"] == [1.0, 2.0, 3.0]
    assert spec["random_control"]["n"] == 20 and spec["reference"] == "median, never max"
    labels = {v[2] for v in CG.VARIANTS}
    assert labels == {CG.EX_ANTE}, "ızgara hücreleri EX_ANTE; HINDSIGHT yalnız işlem başına en iyi hücre"


def test_indexed_prior_equals_the_list_definitions_and_has_no_lookahead():
    """S2'nin önek yapıları (`analysis.BookPrior`) liste tanımlarıyla (`attribution.noise_band`, `cfgrid.overshoot_history`,
    `cfgrid.regime_fit`) AYNI sonucu verir; öncekiler yalnız `closed_at < opened_at` olanlardır."""
    from tradingbot.learning_cf_aux import overshoot_estimate
    from tradingbot.research_engine import analysis as AN
    from tradingbot.research_engine import attribution as AT
    rng = random.Random(5)
    rows = []
    for i in range(400):
        c = datetime(2026, 7, 1, tzinfo=UTC).timestamp() + rng.randint(0, 80) * 86_400 + rng.randint(0, 3) * 3600
        rows.append({"trade_key": f"strategy_paper_box|F{i:05d}|x", "book": "strategy_paper_box", "tactic": rng.choice("ab"),
                     "closed_at": _iso(int(c * 1000)), "opened_at": _iso(int(c * 1000) - 3_600_000),
                     "net_r": repr(rng.uniform(-1.2, 2.0)), "path": {"mae_pct": -rng.uniform(0, 1.5) if rng.random() > 0.1 else None},
                     "situation_entry": {"h4_trend": rng.choice(["UP", "DOWN"]), "h4_vol_regime": "NORMAL"},
                     "cf_inputs": {"exit_overshoot_pct": rng.uniform(0, 0.05) if rng.random() > 0.5 else None}})
    bp = AN._prior_index(rows)["strategy_paper_box"]
    for r in rng.sample(rows, 60):
        ctx = AN._prior_of({"strategy_paper_box": bp}, r)
        prior = [x for x in rows if AT.closed_key(x)[0] < AT.closed_key({"closed_at": r["opened_at"]})[0]]
        assert sorted(x["trade_key"] for x in ctx.rows()) == sorted(x["trade_key"] for x in prior)
        assert ctx.noise_band() == AT.noise_band(prior)
        assert overshoot_estimate(ctx.overshoot_history()) == overshoot_estimate(CG.overshoot_history(prior))
        assert ctx.regime_fit(r) == CG.regime_fit(r, prior)
