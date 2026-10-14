# -*- coding: utf-8 -*-
"""SIKI GÜÇLÜ ADAY (signal_lab.STRICT_RULE_TR): standart kuralın "eşini geçiyor" zayıflığı — genel sürüklenmesi olan
piyasada avantajsız şekil eşini şans eseri iki dönemde de geçer — doğrulama farkının gün kümeli aralığıyla kapanır."""
from __future__ import annotations

import copy
import json
import time

import numpy as np
import pytest

from tradingbot import candle_lab as CL
from tradingbot import candle_variations as V
from tradingbot import signal_lab as L

DAY = 86_400_000
T0 = 1_700_006_400_000                                         # gün başı (UTC)
CV0 = "CV000_EXAMPLE_BULL3"


def _evs(name: str, family: str, rs: np.ndarray, days: np.ndarray, side: str = L.LONG, tf: str = "4h") -> list[dict]:
    return [{"symbol": f"S{k % 3}", "tf": tf, "family": family, "name": name, "side": side,
             "t_ms": T0 + int(d) * DAY + (k % 6) * 3_600_000, "r": float(r), "cost_r": 0.05, "ctx": {"hacim": "normal"}}
            for k, (r, d) in enumerate(zip(rs, days))]


def _span(rng, n: int, d0: int = 0, d1: int = 200) -> np.ndarray:
    """n işlem, [d0, d1) günlerine; uçlar dahil (keşif/doğrulama kesimi her iki taraf için aynı yerde)."""
    days = rng.integers(d0, d1, n)
    days[0], days[-1] = d0, d1 - 1
    return days


def _pick(agg: dict, name: str) -> dict:
    return next(g for g in agg["groups"] if g["name"] == name and g["context"] == "HEPSİ")


# ================================================================== birim: hüküm ve aralık
def test_common_drift_fools_standard_but_not_strict():
    """Gerçek ve eşi AYNI pozitif sürüklenme (avantaj yok): fark iki dönemde de pozitif (+0,02R) → standart GÜÇLÜ ADAY;
    farkın aralığı 0'ı içerir → sıkı ZAYIF İZ."""
    rng = np.random.default_rng(1)
    rs = rng.normal(0.3, 1.0, 300)
    days = _span(rng, 300)
    evs = _evs("SIG", "extra", rs, days) + _evs("PLACEBO_RANDOM", "placebo", rs - 0.02, days)
    g = _pick(L.aggregate(evs, L.LabConfig()), "SIG")
    assert g["verdict"] == L.V_STRONG and g["vs_placebo"]["IS"] > 0 and g["vs_placebo"]["OOS"] > 0
    ci = g["vs_placebo"]["ci95"]
    assert ci["OOS"][0] <= 0 < ci["OOS"][1] and ci["IS"] is not None
    assert g["verdict_strict"] == L.V_WEAK


def test_real_edge_passes_both_rules():
    rng = np.random.default_rng(2)
    evs = _evs("SIG", "extra", rng.normal(0.4, 1.0, 300), _span(rng, 300)) \
        + _evs("PLACEBO_RANDOM", "placebo", rng.normal(0.0, 1.0, 600), _span(rng, 600))
    g = _pick(L.aggregate(evs, L.LabConfig()), "SIG")
    assert g["verdict"] == L.V_STRONG and g["verdict_strict"] == L.V_STRONG
    assert g["vs_placebo"]["ci95"]["OOS"][0] > 0 and g["vs_placebo"]["ci95"]["IS"][0] > 0


def test_fewer_than_five_days_fails_strict():
    """Eşin doğrulama işlemleri 4 güne yığılı: standart hüküm eşin yalnız ortalamasına bakar (GÜÇLÜ ADAY), farkın
    aralığı kurulamaz (None) → sıkı şart geçmez."""
    rng = np.random.default_rng(3)
    p_days = np.concatenate([_span(rng, 200, 0, 133), np.array([133, 150, 170, 199] * 25)])
    evs = _evs("SIG", "extra", rng.normal(0.4, 1.0, 300), _span(rng, 300)) \
        + _evs("PLACEBO_RANDOM", "placebo", rng.normal(0.0, 1.0, 300), p_days)
    g = _pick(L.aggregate(evs, L.LabConfig()), "SIG")
    assert g["verdict"] == L.V_STRONG and g["vs_placebo"]["ci95"]["OOS"] is None
    assert g["vs_placebo"]["ci95"]["IS"] is not None and g["verdict_strict"] == L.V_WEAK
    four = np.repeat(np.arange(4), 10)
    assert L.diff_ci(np.ones(40), four, np.zeros(50), np.arange(50), 200) is None
    assert L.diff_ci(np.ones(50), np.arange(50), np.zeros(40), four, 200) is None
    assert L.verdict_strict(L.V_STRONG, None) == L.V_WEAK and L.verdict_strict(L.V_STRONG, {"OOS": None}) == L.V_WEAK
    for v in (L.V_WEAK, L.V_NONE, L.V_LOSS, L.V_THIN):
        assert L.verdict_strict(v, {"OOS": [0.5, 0.9]}) == v


def test_diff_interval_is_deterministic():
    rng = np.random.default_rng(4)
    a, ad = rng.normal(0.2, 1.0, 300), rng.integers(0, 100, 300)
    b, bd = rng.normal(0.0, 1.0, 400), rng.integers(0, 100, 400)
    first = L.diff_ci(a, ad, b, bd, 1000)
    assert first == L.diff_ci(a.copy(), ad.copy(), b.copy(), bd.copy(), 1000) and first[0] < first[1]
    evs = _evs("SIG", "extra", a, ad) + _evs("PLACEBO_RANDOM", "placebo", b, bd)
    assert _pick(L.aggregate(evs, L.LabConfig()), "SIG") == _pick(L.aggregate(copy.deepcopy(evs), L.LabConfig()), "SIG")


# ================================================================== Monte Carlo: standart kuralın zayıflığı
def test_monte_carlo_strict_false_positive_rate():
    """200 tohum, avantaj YOK + ortak pozitif sürüklenme (+0,3R; keşif 200 işlem/133 gün, doğrulama 100 işlem/67 gün,
    eşi aynı boyutta). Standart kural bu grupların yaklaşık dörtte birini GÜÇLÜ ADAY sayar (belgedeki zayıflık); sıkı
    kural bunların en çok %10'unu geçirir."""
    t0 = time.time()
    cfg = L.LabConfig()
    std = strict = 0
    for seed in range(200):
        rng = np.random.default_rng(seed)
        sides = {}
        for who in ("r", "p"):
            for per, n, d, off in (("IS", 200, 133, 0), ("OOS", 100, 67, 133)):
                sides[(who, per)] = (rng.normal(0.3, 1.0, n), off + rng.integers(0, d, n))
        st = {k: L.r_stats(r, cfg.bootstrap_iters, days=d) for k, (r, d) in sides.items()}
        vs = {p: st[("r", p)]["mean_r"] - st[("p", p)]["mean_r"] for p in ("IS", "OOS")}
        v = L.verdict(st[("r", "IS")], st[("r", "OOS")], cfg, vs)
        if v != L.V_STRONG:
            continue
        std += 1
        ci = {p: L.diff_ci(*sides[("r", p)], *sides[("p", p)], cfg.bootstrap_iters) for p in ("IS", "OOS")}
        strict += L.verdict_strict(v, ci) == L.V_STRONG
    msg = f"standart GÜÇLÜ ADAY {std}/200 (%{100 * std / 200:.0f}); sıkı şartı da geçen {strict}/{std}"
    assert std >= 30, "senaryo standart kuralı sık kandırmalı — " + msg
    assert strict <= 0.10 * std, msg
    assert time.time() - t0 < 10, "ucuz kalmalı"


# ================================================================== aggregate ve kayıt
def test_aggregate_marks_every_group_and_intervals_only_where_needed():
    rng = np.random.default_rng(5)
    days = _span(rng, 300)
    evs = (_evs("STRONG", "extra", rng.normal(0.6, 1.0, 300), days)
           + _evs("FLAT", "extra", rng.normal(0.0, 1.0, 300), _span(rng, 300))
           + _evs("PLACEBO_RANDOM", "placebo", rng.normal(0.0, 1.0, 600), _span(rng, 600))
           + _evs("ALONE", "extra", rng.normal(0.4, 1.0, 300), days, side=L.SHORT))
    agg = L.aggregate(evs, L.LabConfig())
    assert agg["groups"] and all("verdict_strict" in g for g in agg["groups"])
    for g in agg["groups"]:
        has_ci = bool(g.get("vs_placebo")) and "ci95" in g["vs_placebo"]
        assert has_ci == (g["verdict"] == L.V_STRONG and g["vs_placebo"] is not None), (g["name"], g["context"])
        if g["verdict"] != L.V_STRONG:
            assert g["verdict_strict"] == g["verdict"]
    assert _pick(agg, "STRONG")["verdict_strict"] == L.V_STRONG
    alone = _pick(agg, "ALONE")
    assert alone["vs_placebo"] is None and alone["verdict"] == alone["verdict_strict"] == L.V_WEAK
    txt = L.render({"groups": agg["groups"], "symbols": ["S0"], "timeframes": ["4h"]})
    assert "sıkı şartı da geçen: " in txt and "· sıkı: GÜÇLÜ ADAY" in txt
    json.dumps(agg["groups"], allow_nan=False)


def test_build_record_carries_strict_verdict(monkeypatch):
    monkeypatch.setattr(CL, "golden_sha", lambda var: "0" * 16)
    var = V.get(CV0)
    rng = np.random.default_rng(6)
    pname = CL.placebo_name(CV0)
    days = _span(rng, 300)
    evs = (_evs(CV0, CL.FAMILY, rng.normal(0.3, 1.0, 300), days) + _evs(pname, "placebo", rng.normal(0.28, 1.0, 300), days)
           + _evs(CV0, CL.FAMILY, rng.normal(0.4, 1.0, 300), _span(rng, 300), tf="1h")
           + _evs(pname, "placebo", rng.normal(0.0, 1.0, 600), _span(rng, 600), tf="1h"))
    agg = L.aggregate(evs, L.LabConfig())
    rep = {**agg, "timeframes": ["4h", "1h"], "symbols": ["S0", "S1", "S2"], "days": {"4h": 200, "1h": 200},
           "mode": {"only_variations": True}}
    rec = CL.build_record(rep, var, env={})
    assert rec["record_schema"] == CL.RECORD_SCHEMA == V.RECORD_SCHEMA == "candle_lab/2"
    assert rec["strict_rule_tr"] == L.STRICT_RULE_TR
    for tf in ("4h", "1h"):
        g = _pick({"groups": [x for x in agg["groups"] if x["tf"] == tf]}, CV0)
        b = rec["by_tf"][tf]
        assert b["verdict"] == g["verdict"] and b["verdict_strict"] == g["verdict_strict"]
        assert (b["vs_placebo"] or {}).get("ci95") == (g["vs_placebo"] or {}).get("ci95")
    assert rec["verdict"] == rec["by_tf"]["4h"]["verdict"] and rec["verdict_strict"] == rec["by_tf"]["4h"]["verdict_strict"]
    assert rec["by_tf"]["1h"]["verdict"] == rec["by_tf"]["1h"]["verdict_strict"] == L.V_STRONG
    assert "ci95" in rec["by_tf"]["1h"]["vs_placebo"]
    json.dumps(rec, allow_nan=False)
    rep["timeframes"] = ["1h"]                                    # birincil dilim koşulmadı → iki hüküm de None
    rec = CL.build_record(rep, var, env={})
    assert rec["verdict"] is None and rec["verdict_strict"] is None
    rep["timeframes"] = ["4h", "1h"]                              # grupta sıkı hüküm yok → standart hükme düşülmez
    rep["groups"] = [{k: v for k, v in g.items() if k != "verdict_strict"} for g in agg["groups"]]
    rec = CL.build_record(rep, var, env={})
    assert rec["verdict"] == rec["by_tf"]["4h"]["verdict"] and rec["verdict_strict"] is None
    assert rec["by_tf"]["1h"]["verdict"] == L.V_STRONG and rec["by_tf"]["1h"]["verdict_strict"] is None
    assert all(s["verdict_strict"] is None for s in rec["context_slices_info"])


@pytest.mark.parametrize("schema, ok", [("candle_lab/1", False), ("candle_lab/2", True)])
def test_gate_requires_current_record_schema(tmp_path, monkeypatch, schema, ok):
    from tradingbot import candle_dsl as D
    e = dict(copy.deepcopy(V.CV000_EXAMPLE_BULL3), id="CV911_SCHEMA", example=False,
             readback={"confirmed_by": "user", "date": "2026-09-26"},
             approval={"by": "user", "date": "2026-09-27", "observation": False, "run_id": "4242"})
    rec = {"record_schema": schema, "id": e["id"], "definition_sha": D.definition_sha(e), "dsl_version": D.DSL_VERSION,
           "window": D.WINDOW, "golden_sha": "0" * 16, "verdict": L.V_STRONG, "verdict_strict": L.V_STRONG,
           "run": {"github_run_id": "4242", "completed_at": "2026-09-27T10:00:00Z",
                   "mode": {"catalog": False, "algos": False, "extras": False, "only_variations": True}},
           "readback": e["readback"], "primary_tf": "4h", "by_tf": {"4h": {"OOS": {"n": 120}}}}
    (tmp_path / "CV911_SCHEMA.json").write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(V, "LAB_RECORDS_DIR", tmp_path)
    monkeypatch.setattr(V, "VARIATIONS", (V.CV000_EXAMPLE_BULL3, e))
    V.reset_cache()
    try:
        var, why = V.gate("CV911_SCHEMA")
    finally:
        V.reset_cache()
    assert (why is None and var is not None) if ok else (var is None and why == "LAB_RECORD_INVALID"), why
