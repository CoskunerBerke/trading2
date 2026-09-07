"""Profitability Research Acceleration V1 — bu çalışmanın GETİRDİĞİ maddi riskler için testler.

Kapsanan riskler:
* gelecek sızıntısı (karar anından önceki bar simülasyona giremez),
* muhasebe çift sayımı (kapanmış net + açık gerçekleşmiş kısmi kâr),
* iyimser dolum (bar içi sıra, tetiklenmemiş plan fiyatı),
* sürüm/kanıt sınıfı kirlenmesi (retrospektif sonuç POINT_IN_TIME sayılamaz),
* yasak yazım (canlı `state/` altına çıktı),
* kısa-sonuç seçilim yanlılığı (eşit ufuk kohortu),
* tekilleştirme (aynı barın tur tekrarları bağımsız örnek değildir),
* determinizm (aynı girdi → aynı research_id, aynı bootstrap).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tradingbot.research import (CANONICAL_OBSERVED, MISSING_OR_UNMEASURABLE, POINT_IN_TIME,
                                 RETROSPECTIVE_RESEARCH)
from tradingbot.research.bars import (MAPPING_OK, MAPPING_SUSPECT, BarCache, is_fetchable,
                                      verify_mapping)
from tradingbot.research.dataset import (PATH_FULL, PATH_NONE, ResearchDataset, path_coverage,
                                         reconcile_economics, to_ms, trade_inventory)
from tradingbot.research.entry_lab import (FILL_NEXT_BAR_OPEN, FILL_PLAN_TRIGGER, HORIZON_CLOSE,
                                           OUT_OF_COHORT, RESOLVED, UNFILLED,
                                           build_opportunities, corrected_edge, portfolio_replay,
                                           ranking_null_test, score_definition_trace,
                                           simulate_opportunity)
from tradingbot.research.exit_lab import (build_exit_trades, intrabar_ambiguity,
                                          measured_cost_per_fill_r, paired_delta_ci)
from tradingbot.research.lessons import ResearchLesson, catalog, validate
from tradingbot.research.protocol import ResearchProtocol, stable_hash
from tradingbot.research.run import _finite, _guard_out, build_report

H = 3_600_000.0
BAR = 900_000.0


# ------------------------------------------------------------------ yardımcılar
def _bars(start_ms, n, *, open_=100.0, step=0.0, high_off=1.0, low_off=1.0):
    rows = []
    px = open_
    for i in range(n):
        rows.append([int(start_ms + i * BAR), px, px + high_off, px - low_off, px + step, 10.0])
        px += step
    return rows


class _FakeCache(BarCache):
    """Ağ YOK — testler yalnız enjekte edilmiş barları görür."""

    def __init__(self, tmp: Path, series: dict[str, list[list]]):
        super().__init__(tmp, offline=True)
        for sym, rows in series.items():
            data = self._load(sym, "15m")
            for r in rows:
                data[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                                   float(r[5])]


def _mk_export(tmp: Path, *, closed=None, positions=None, entries=None, memory=None,
               snapshots=None, cutoff="2026-09-07T21:16:41Z") -> Path:
    root = tmp / "exp"
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "MANIFEST.txt").write_text(f"cutoff_utc={cutoff}\napp_head=deadbeef\n",
                                       encoding="utf-8")
    led = {"starting_equity": 100.0, "wallet_balance": 100.0,
           "positions": positions or {}, "history": closed or [],
           "entries": entries or []}
    (root / "state" / "futures_ledger.json").write_text(json.dumps(led), encoding="utf-8")
    (root / "state" / "spot_ledger.json").write_text(json.dumps({"starting_equity": 0.0}),
                                                     encoding="utf-8")
    (root / "state" / "entry_snapshot.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (snapshots or [])), encoding="utf-8")
    (root / "state" / "trade_memory.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (memory or [])), encoding="utf-8")
    (root / "state" / "position_path.jsonl").write_text("", encoding="utf-8")
    return root


def _opp(**kw):
    base = {"opportunity_key": "S|LONG|4h|0|pullback", "symbol": "AAA/USDT", "direction": "LONG",
            "timeframe": "4h", "ts_ms": 1_000_000_000_000.0, "bar_open_ms": 1_000_000_000_000.0,
            "bar_close_ms": 1_000_000_000_000.0 + 14_400_000,
            "entry_price": 100.0, "stop_price": 95.0, "targets": [110.0, 115.0],
            "decision_close": None, "avg_win_r": 2.0, "avg_loss_r": -1.0,
            "p_win_model": 0.3, "p_win_prior": 0.65, "gross_expectancy_r": 0.0,
            "uncertainty_penalty_r": 0.0, "conservative_net_edge_r": 0.0}
    base.update(kw)
    return base


# ------------------------------------------------------------------ 1) gelecek sızıntısı
def test_simulation_never_uses_bars_before_the_decision(tmp_path):
    """Karar anından ÖNCEKİ barlar sonucu değiştiremez (no-look-ahead)."""
    ts = 1_000_000_000_000
    pre = _bars(ts - 20 * BAR, 20, open_=100.0, step=-2.0, high_off=0.5, low_off=30.0)
    post = _bars(ts, 12, open_=100.0, step=1.0)
    o = _opp(ts_ms=float(ts), bar_open_ms=float(ts))
    cache_with = _FakeCache(tmp_path / "a", {"AAA/USDT": pre + post})
    cache_without = _FakeCache(tmp_path / "b", {"AAA/USDT": post})
    cutoff = ts + 100 * BAR
    r1 = simulate_opportunity(o, cache_with, cutoff_ms=cutoff, cost_per_fill_r=0.0,
                              horizon_hours=3.0, fetch=False)
    r2 = simulate_opportunity(o, cache_without, cutoff_ms=cutoff, cost_per_fill_r=0.0,
                              horizon_hours=3.0, fetch=False)
    assert r1["state"] in (RESOLVED, HORIZON_CLOSE)
    # Geçmişteki derin düşüş (low_off=30) stop'u tetiklememelidir.
    assert r1["net_r"] == r2["net_r"]
    assert r1["mae_r"] == r2["mae_r"]


def test_out_of_cohort_when_full_horizon_is_unavailable(tmp_path):
    """Ufuk cutoff'a sığmıyorsa fırsat KOHORT DIŞIdır — kısaltılmış ufukla ölçülmez."""
    ts = 1_000_000_000_000
    cache = _FakeCache(tmp_path, {"AAA/USDT": _bars(ts, 200)})
    o = _opp(ts_ms=float(ts), bar_open_ms=float(ts))
    r = simulate_opportunity(o, cache, cutoff_ms=ts + 2 * H, cost_per_fill_r=0.0,
                             horizon_hours=48.0, fetch=False)
    assert r["state"] == OUT_OF_COHORT
    assert "net_r" not in r


# ------------------------------------------------------------------ 2) iyimser dolum
def test_intrabar_conservative_order_prefers_stop_over_target(tmp_path):
    """Aynı barda hem stop hem hedef erişilebilirse SONUÇ STOP olmalıdır."""
    ts = 1_000_000_000_000
    # tek bar: hem 110 hedefe hem 95 stop'a değiyor
    rows = [[ts, 100.0, 112.0, 94.0, 100.0, 10.0]] + _bars(ts + BAR, 4, open_=100.0)
    cache = _FakeCache(tmp_path, {"AAA/USDT": rows})
    o = _opp(ts_ms=float(ts), bar_open_ms=float(ts))
    r = simulate_opportunity(o, cache, cutoff_ms=ts + 100 * BAR, cost_per_fill_r=0.0,
                             horizon_hours=1.0, fetch=False)
    assert r["exit_reason"] in ("stop", "breakeven_stop")
    assert r["net_r"] < 0


def test_intrabar_ambiguity_is_counted_not_hidden():
    bars = [{"high": 112.0, "low": 94.0, "close": 100.0},
            {"high": 101.0, "low": 99.0, "close": 100.0}]
    amb = intrabar_ambiguity(bars, entry=100.0, stop=95.0, targets=[110.0], side="LONG")
    assert amb["n_ambiguous"] == 1
    assert amb["measurable"] is True
    assert amb["state"] == "AMBIGUOUS_INTRABAR"


def test_plan_trigger_fill_requires_a_real_touch(tmp_path):
    """Plan fiyatı işlem GÖRMEDİYSE dolum VARSAYILMAZ."""
    ts = 1_000_000_000_000
    # fiyat hep 200 civarı; plan girişi 100 → asla dokunulmaz
    rows = [[ts + i * BAR, 200.0, 201.0, 199.0, 200.0, 10.0] for i in range(200)]
    cache = _FakeCache(tmp_path, {"AAA/USDT": rows})
    o = _opp(ts_ms=float(ts), bar_open_ms=float(ts))
    r = simulate_opportunity(o, cache, cutoff_ms=ts + 500 * BAR, cost_per_fill_r=0.0,
                             horizon_hours=48.0, fill_mode=FILL_PLAN_TRIGGER, fetch=False)
    assert r["state"] == UNFILLED


def test_fill_shift_preserves_r_geometry(tmp_path):
    """Dolum fiyatı kaysa bile stop/hedef MUTLAK uzaklığı korunur — R büyümez."""
    ts = 1_000_000_000_000
    rows = [[ts, 104.0, 105.0, 103.0, 104.0, 10.0]] + \
        [[ts + i * BAR, 104.0, 104.5, 103.5, 104.0, 10.0] for i in range(1, 20)]
    cache = _FakeCache(tmp_path, {"AAA/USDT": rows})
    o = _opp(ts_ms=float(ts), bar_open_ms=float(ts))
    r = simulate_opportunity(o, cache, cutoff_ms=ts + 500 * BAR, cost_per_fill_r=0.0,
                             horizon_hours=4.0, fill_mode=FILL_NEXT_BAR_OPEN, fetch=False)
    assert r["fill_price"] == 104.0
    # Plan riski 100→95 = 5. Dolum 104 ise stop 99, hedefler 114/119 olur; en düşük fiyat
    # 103.0 → MAE = (103-104)/5 = -0.2 R. Dolum kaydığı için R BÜYÜMEZ.
    assert math.isclose(r["mae_r"], -0.2, abs_tol=1e-6)


# ------------------------------------------------------------------ 3) muhasebe
def test_reconciliation_separates_closed_and_open_realized(tmp_path):
    closed = [{"id": "T1", "net_pnl": -2.0, "gross_pnl": -1.9, "fees": 0.1, "funding": 0.0,
               "r_multiple": -1.0, "symbol": "A/USDT", "side": "LONG",
               "opened_at": "2026-09-01T00:00:00+00:00", "closed_at": "2026-09-02T00:00:00+00:00",
               "exit_reason": "stop", "fills": [1, 2]}]
    positions = {"B/USDT": {"id": "T2", "symbol": "B/USDT", "side": "LONG", "entry_avg": 10.0,
                            "last_price": 11.0, "qty": 1.0, "realized_pnl": 3.0,
                            "opened_at": "2026-09-03T00:00:00+00:00", "tp1_done": True}}
    entries = [{"kind": "PNL", "amount": -2.0}, {"kind": "PNL", "amount": 3.0},
               {"kind": "FEE", "amount": -0.5}]
    root = _mk_export(tmp_path, closed=closed, positions=positions, entries=entries)
    doc = json.loads((root / "state" / "futures_ledger.json").read_text(encoding="utf-8"))
    doc["wallet_balance"] = 100.0 + 0.5
    (root / "state" / "futures_ledger.json").write_text(json.dumps(doc), encoding="utf-8")
    rec = reconcile_economics(ResearchDataset.load(root))["futures"]
    assert rec["closed_trades"]["net_pnl_usdt"] == -2.0
    assert rec["open_positions"]["realized_partial_pnl_usdt"] == 3.0
    # açık MTM AYRI — gerçekleşmiş kısmi kâra eklenmez
    assert rec["open_positions"]["unrealized_mtm_usdt"] == 1.0
    assert rec["identity_check"]["closed_net_plus_open_realized"] == 1.0
    assert rec["reconciled"] is True


def test_slippage_is_not_charged_twice():
    """Kayma dolum fiyatının içindedir; maliyet modeli onu AYRICA saymaz."""
    rows = [{"net_pnl": -1.0, "r_multiple": -1.0, "fees": 0.02, "funding": 0.0, "n_fills": 2,
             "slippage_cost": 5.0}]
    c = measured_cost_per_fill_r(rows)
    assert math.isclose(c["cost_per_fill_r"], 0.01, rel_tol=1e-6)
    assert "çift sayım" in c["note"]


# ------------------------------------------------------------------ 4) kanıt sınıfı / sürüm
def test_entry_snapshot_evidence_requires_an_explicit_link(tmp_path):
    """Sembol/zaman yakınlığıyla snapshot ATANMAZ — bağ yoksa kanıt MISSING'dir."""
    closed = [{"id": "T1", "symbol": "A/USDT", "side": "LONG", "net_pnl": -1.0,
               "r_multiple": -1.0, "fees": 0.0, "funding": 0.0,
               "opened_at": "2026-09-03T00:00:00+00:00", "closed_at": "2026-09-04T00:00:00+00:00",
               "exit_reason": "stop", "fills": []}]
    snaps = [{"candidate_id": "c1", "symbol": "A/USDT", "ts": "2026-09-03T00:00:00+00:00",
              "ts_ms": 1.0, "direction": "LONG", "timeframe": "4h", "policy_version": "entry_v1.0.0"}]
    root = _mk_export(tmp_path, closed=closed, snapshots=snaps)
    inv = trade_inventory(ResearchDataset.load(root))
    assert inv["closed"][0]["entry_snapshot"] == MISSING_OR_UNMEASURABLE
    assert inv["closed"][0]["economics_evidence"] == CANONICAL_OBSERVED


def test_retrospective_results_are_never_labelled_point_in_time(tmp_path):
    ts = 1_000_000_000_000
    cache = _FakeCache(tmp_path, {"AAA/USDT": _bars(ts, 40)})
    r = simulate_opportunity(_opp(ts_ms=float(ts), bar_open_ms=float(ts)), cache,
                             cutoff_ms=ts + 500 * BAR, cost_per_fill_r=0.0, horizon_hours=4.0,
                             fetch=False)
    assert r["evidence_class"] == RETROSPECTIVE_RESEARCH
    o = build_opportunities([{"symbol": "A/USDT", "direction": "LONG", "timeframe": "4h",
                              "ts_ms": 1.0, "candidate_id": "c"}], {})
    assert o["opportunities"][0]["evidence_class"] == POINT_IN_TIME


def test_score_identity_detects_which_probability_drives_the_gate():
    """Kimlik testi formülü uydurmaz; hangi olasılığın kullanıldığını ÖLÇER."""
    opps = [_opp(p_win_prior=0.6, p_win_model=0.3, avg_win_r=2.0, avg_loss_r=-1.0,
                 gross_expectancy_r=0.6 * 2.0 - 0.4)]
    tr = score_definition_trace(opps)
    assert tr["with_p_win_prior"]["fraction"] == 1.0
    assert tr["with_p_win_model"]["fraction"] == 0.0
    assert tr["economic_gate_driver"] == "p_win_prior"


# ------------------------------------------------------------------ 5) yasak yazım
def test_out_dir_inside_state_is_refused(tmp_path):
    (tmp_path / "state" / "reports").mkdir(parents=True)
    with pytest.raises(SystemExit):
        _guard_out(tmp_path / "state" / "reports")
    _guard_out(tmp_path / "research_out")          # state dışı yol kabul edilir


def test_report_run_writes_only_under_out_dir(tmp_path):
    """Araştırma koşusu export dizinine ya da başka bir yere DOKUNMAZ."""
    closed = [{"id": "T1", "symbol": "A/USDT", "side": "LONG", "net_pnl": -1.0, "gross_pnl": -1.0,
               "r_multiple": -1.0, "fees": 0.0, "funding": 0.0, "slippage_cost": 0.0,
               "opened_at": "2026-09-03T00:00:00+00:00", "closed_at": "2026-09-04T00:00:00+00:00",
               "exit_reason": "stop", "fills": [1, 2], "leverage": 1, "quantity": 1.0,
               "entry": 100.0, "exit_price": 95.0, "features": {"initial_stop": 95.0}}]
    root = _mk_export(tmp_path, closed=closed)
    before = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    out = tmp_path / "out"
    built = build_report(root, out, offline=True, n_null=25)
    after = {p: p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()}
    assert before == after, "export dizini DEĞİŞMEMELİ"
    wb = built["report"]["write_boundary"]
    assert wb["canonical_state_written"] is False
    assert wb["production_code_changed"] is False
    assert wb["learned_index_touched"] is False


# ------------------------------------------------------------------ 6) tekilleştirme
def test_repeated_tour_rows_collapse_to_one_opportunity():
    """Aynı barın tur tekrarları BAĞIMSIZ örnek değildir."""
    rows = [{"symbol": "A/USDT", "direction": "LONG", "timeframe": "4h", "setup": "pullback",
             "ts_ms": 14_400_000 * 10 + i * 60_000, "candidate_id": f"c{i}",
             "baseline_accepted": i == 3, "baseline_reject_reason": None if i == 3 else "X"}
            for i in range(12)]
    out = build_opportunities(rows, {})
    assert out["n_raw_rows"] == 12
    assert out["n_unique"] == 1
    o = out["opportunities"][0]
    assert o["n_repeats"] == 12
    assert o["baseline_accepted"] is True     # bar boyunca birleştirildi


# ------------------------------------------------------------------ 7) determinizm
def test_protocol_id_is_deterministic_and_records_every_variant():
    def mk():
        p = ResearchProtocol(run_label="x", code_sha="abc", cutoff_utc="t",
                             input_hashes={"a": "1"}, universe=["B", "A"], seed=7)
        p.record_variant("v1", kind="entry", outcome="RUN")
        p.record_variant("v2", kind="entry", outcome="BLOCKED", note="yetersiz pencere")
        return p
    a, b = mk(), mk()
    assert a.research_id == b.research_id
    d = a.to_dict()
    assert [v["outcome"] for v in d["attempted_variants"]] == ["RUN", "BLOCKED"]
    assert d["promotion_effect"].startswith("NONE")
    assert stable_hash({"x": 1}) == stable_hash({"x": 1})


def test_paired_bootstrap_is_deterministic_and_reports_cluster_ci():
    from tradingbot.quant.exit_challenger import DEFAULT_CHALLENGERS
    trades = []
    for i in range(12):
        trades.append({"id": f"T{i}", "trade_id": f"T{i}", "symbol": f"S{i % 3}/USDT",
                       "direction": "LONG", "entry_price": 100.0, "initial_stop": 95.0,
                       "targets": [110.0, 115.0],
                       "price_path": [{"high": 100 + i, "low": 99.0, "close": 100.0}
                                      for _ in range(10)]})
    a = paired_delta_ci(trades, DEFAULT_CHALLENGERS[0], cost_per_fill_r=0.01, seed=7)
    b = paired_delta_ci(trades, DEFAULT_CHALLENGERS[0], cost_per_fill_r=0.01, seed=7)
    assert a == b
    assert a["n_clusters"] == 3
    assert len(a["ci95_by_symbol_cluster"]) == 2


def test_ranking_null_test_is_deterministic(tmp_path):
    opps = [_opp(opportunity_key=f"k{i}", symbol=f"S{i%4}/USDT",
                 conservative_net_edge_r=float(i)) for i in range(20)]
    sims = [{"opportunity_key": f"k{i}", "state": RESOLVED, "net_r": (1.0 if i % 3 else -1.0),
             "win": bool(i % 3), "bars_held": 4} for i in range(20)]
    kw = {"rank_key": lambda o: o.get("conservative_net_edge_r"), "label": "x",
          "max_concurrent": 3, "n_null": 30, "seed": 7}
    assert ranking_null_test(opps, sims, **kw) == ranking_null_test(opps, sims, **kw)


# ------------------------------------------------------------------ 8) kapsam / eşleme
def test_path_coverage_never_invents_a_path():
    cov = path_coverage([], opened_at_ms=0.0, closed_at_ms=1000.0)
    assert cov["state"] == PATH_NONE
    assert cov["covered_fraction"] == 0.0
    # kısmi pencere (ilk kayıt açılıştan çok sonra ya da kapsam < %95) FULL sayılmaz
    partial = path_coverage([{"ts_ms": 100.0}, {"ts_ms": 900.0}], opened_at_ms=0.0,
                            closed_at_ms=1000.0)
    assert partial["state"] == "PARTIAL_PATH"
    assert math.isclose(partial["covered_fraction"], 0.8, abs_tol=1e-6)
    full = path_coverage([{"ts_ms": 10.0}, {"ts_ms": 995.0}], opened_at_ms=0.0,
                         closed_at_ms=1000.0)
    assert full["state"] == PATH_FULL
    assert full["covered_fraction"] >= 0.95


def test_mapping_suspect_excludes_the_symbol():
    bars = [{"timestamp": 0, "close_ms": BAR, "high": 101.0, "low": 99.0, "close": 100.0}]
    ok = verify_mapping(bars, reference_price=100.0, reference_ms=10.0)
    bad = verify_mapping(bars, reference_price=250.0, reference_ms=10.0)
    assert ok["state"] == MAPPING_OK
    assert bad["state"] == MAPPING_SUSPECT
    assert verify_mapping([], reference_price=1.0, reference_ms=1.0)["state"] != MAPPING_OK


def test_non_ascii_symbols_are_excluded_not_silently_broken():
    assert is_fetchable("BTC/USDT") is True
    assert is_fetchable("我踏马来了/USDT") is False


def test_exit_trades_reject_when_stop_or_targets_are_missing(tmp_path):
    cache = _FakeCache(tmp_path, {"A/USDT": _bars(0, 40)})
    rows = [{"trade_id": "T1", "symbol": "A/USDT", "side": "LONG", "entry": 100.0,
             "initial_stop": None, "targets": [], "opened_at_ms": 0.0,
             "closed_at_ms": 10 * BAR, "r_multiple": -1.0, "exit_reason": "stop"}]
    built = build_exit_trades(rows, cache, cutoff_ms=100 * BAR, fetch=False)
    assert built["trades"] == []
    assert built["rejected"][0]["reason"] == "MISSING_ENTRY_STOP_OR_TIME"


# ------------------------------------------------------------------ 9) dersler
def test_lesson_without_contradicting_evidence_is_incomplete():
    ls = ResearchLesson(lesson_id="X", title="t", claim="c", evidence_class=CANONICAL_OBSERVED,
                        sample_size=5, net_effect="e", uncertainty="u",
                        supporting_ids=["T1"], contradicting_ids=[],
                        alternative_explanations=["a"], falsifiable_next_test="f")
    d = ls.to_dict()
    assert d["completeness"]["state"] == "INCOMPLETE"
    assert any("contradicting_ids" in m for m in d["completeness"]["missing_fields"])
    ls.contradicting_ids = ["T2"]
    assert ls.to_dict()["completeness"]["state"] == "COMPLETE"


def test_lesson_catalog_cannot_touch_production_learning():
    ls = ResearchLesson(lesson_id="X", title="t", claim="c", evidence_class=RETROSPECTIVE_RESEARCH,
                        sample_size=5, net_effect="e", uncertainty="u", supporting_ids=["T1"],
                        contradicting_ids=["T2"], alternative_explanations=["a"],
                        falsifiable_next_test="f")
    cat = catalog([ls])
    assert cat["lessons"][0]["counts_toward_promotion"] is False
    assert cat["lessons"][0]["production_effect"].startswith("NONE")
    assert validate({"evidence_class": "NOPE"})["state"] == "INCOMPLETE"


# ------------------------------------------------------------------ 10) JSON güvenliği
def test_non_finite_numbers_never_reach_json():
    cleaned = _finite({"a": float("inf"), "b": [float("nan"), 1.0], "c": {"d": -math.inf}})
    assert cleaned == {"a": None, "b": [None, 1.0], "c": {"d": None}}
    json.dumps(cleaned, allow_nan=False)


def test_portfolio_replay_reports_missed_opportunities():
    """Kapasite kısıtı görünür olmalı — hayatta kalan beklentisi tek başına yetersizdir."""
    opps = [_opp(opportunity_key=f"k{i}", bar_close_ms=float(i)) for i in range(30)]
    sims = [{"opportunity_key": f"k{i}", "state": RESOLVED, "net_r": 1.0, "win": True,
             "bars_held": 1000} for i in range(30)]
    rep = portfolio_replay(opps, sims, rank_key=lambda o: 1.0, label="x", max_concurrent=2)
    assert rep["n_taken"] == 2
    assert rep["n_skipped_capacity"] == 28
    assert "kaldıraç" in rep["simplification"]


def test_corrected_edge_only_swaps_the_probability():
    o = _opp(p_win_model=0.4, avg_win_r=2.0, avg_loss_r=-1.0, uncertainty_penalty_r=0.1)
    assert math.isclose(corrected_edge(o), 0.4 * 2.0 - 0.6 - 0.1, rel_tol=1e-9)
    assert corrected_edge(_opp(p_win_model=None)) is None
