"""UÇTAN UCA öğrenme döngüsü — GERÇEK modüller, deterministik sentetik senaryo.

Zincir (her adım gerçek kaynak kodu çalıştırır):

    kapanmış barlar → coin-head kararı + factor scores + pattern kanıtı
    → HistoricalReplay._snapshot (FeatureSnapshotV3)
    → TradeMemory.record_entry / record_exit → trades(closed_only) JOIN
    → coverage gate → loss attribution → bounded candidate üretimi
    → walk-forward OOS (baseline ↔ candidate, test fold'u seçime GİRMEZ)
    → ResearchPolicyBook durum makinesi (SHADOW → ACTIVE → RETIRED/REVIEW)
    → telemetri + dashboard state

Senaryoya BİLİNÇLİ bir örüntü gömülür: `SHORT + HIGH_VOL + negatif pattern` sistematik zarar eder,
`LONG + NORMAL_VOL + pozitif pattern` kazandırır. Sistem bu kombinasyonu VERİDEN bulmalı, açıklanabilir
bir aday üretmeli ve aday OOS'ta baseline'ı geçmelidir. Örüntü snapshot'a elle yazılmaz: yüksek
volatilite gerçekten oynak barlardan, pattern güveni gerçek `patterns/engine.py::query` şeklinden gelir.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import synth_bars
from tradingbot.coinhead.schema import CoinHeadDecision, FactorGroupScore, PlanSize, TradePlanV3, Verdict
from tradingbot.learn import TradeMemory
from tradingbot.learn.attribution import LOSS_CLASSES, attribution_report
from tradingbot.learn.coverage import coverage_report
from tradingbot.learn.policy import (MAX_CHANGES_PER_CANDIDATE, CandidatePolicy, baseline_policy,
                                     candidates_from_attribution, validate_policy)
from tradingbot.learn.research_policy import (ACTIVE, OFFLINE_VALIDATED, PROPOSED, RETIRED, REVIEW,
                                              SHADOW, ResearchGates, ResearchPolicyBook,
                                              ResearchSafetyError, apply_research_policy)
from tradingbot.learn.telemetry import SnapshotTelemetry
from tradingbot.replay.engine import HistoricalReplay
from tradingbot.replay.policy_eval import evaluate_policies

H4 = 14_400_000
DAY = 86_400_000
BASE = datetime(2026, 3, 1, tzinfo=timezone.utc)
SYMBOLS = ("ETH/USDT", "SOL/USDT", "BNB/USDT", "AVAX/USDT")
N_TRADES = 160

# Senaryo: yüksek volatilite gerçekten geniş barlardan gelir (sahte alan değil)
HL_NORMAL, HL_HIGH = 0.012, 0.020


# =========================================================================== senaryo kurucusu
def _pattern_evidence(side: str, *, good: bool) -> dict:
    """`patterns/engine.py::query` çıktısının gerçek şekli; iyi/kötü benzerlik kanıtı."""
    stats = ({"p_win_posterior": 0.61, "mean_net_r": 0.22, "profit_factor": 1.5, "expectancy_ci": [0.04, 0.40]}
             if good else
             {"p_win_posterior": 0.41, "mean_net_r": -0.18, "profit_factor": 0.7, "expectancy_ci": [-0.34, -0.02]})
    return {side: {"ok": True, "codes": [], "n": 45, "stats": stats,
                   "neighbors": [{"symbol": "BTC/USDT", "distance": 0.12 if good else 0.38}],
                   "levels": ({"same_coin": 40} if good else {"same_coin": 20, "cluster": 15, "universe": 10})}}


def _decision(symbol: str, side: str, *, good: bool, entry: float = 100.0) -> tuple[CoinHeadDecision, TradePlanV3]:
    long = side == "LONG"
    strength = 0.45 if good else -0.15
    plan = TradePlanV3(market_type="futures", direction=side, entry_type="pullback",
                       entry_zone=(entry, entry), stop=entry * (0.97 if long else 1.03),
                       targets=[entry * (1.06 if long else 0.94), entry * (1.12 if long else 0.88)],
                       time_horizon_bars=12, size=PlanSize(amount=1.0, leverage=1), margin=15.0,
                       notional=30.0, expected_cost_pct=0.18, expected_r=1.9 if good else 1.2, valid=True)
    d = CoinHeadDecision(coin_head_id="ch", run_id="e2e", snapshot_id="s", symbol=symbol,
                         market_type="futures", regime="TREND_UP" if good else "HIGH_VOL", direction=side,
                         verdict=Verdict.FUTURES_LONG if long else Verdict.FUTURES_SHORT)
    d.futures_plan = plan
    d.confidence_calibrated = 0.5 + 0.25 * abs(strength)
    d.consensus_confidence = 0.5 + 0.2 * abs(strength)
    d.consensus = {"trend": strength, "momentum": strength / 2.0}
    d.dissent = ["volatility"] if good else ["volatility", "liquidity", "derivatives", "risk"]
    d.vetoes = []
    d.p_win = 0.55 if good else 0.47
    d.expected_r = plan.expected_r
    d.factor_scores = [FactorGroupScore(group=g, score=strength, confidence=0.6, data_quality=1.0,
                                        n_independent=2, conflict=0.1)
                       for g in ("trend", "momentum", "volatility", "volume_flow", "liquidity", "derivatives")]
    d.pattern_evidence = _pattern_evidence(side, good=good)
    return d, plan


def _replay_snapshot(d, plan, *, symbol: str, hl: float, seed: int, telemetry=None):
    """GERÇEK `HistoricalReplay._snapshot` gövdesi (gerçek `_slice` dahil)."""
    end = 1_700_000_000_000 + seed * H4
    bars = synth_bars(end_ms=end, seed=3 + (seed % 11), bar_ms=H4, hl_pct=hl)
    frames = {symbol: {"4h": bars}, "BTC/USDT": {"4h": synth_bars(end_ms=end, seed=7, bar_ms=H4, drift=0.03)}}
    fake = SimpleNamespace(frames=frames, tf="4h", lookback_bars=400, run_id="e2e", seed=7,
                           snap_telemetry=telemetry or SnapshotTelemetry())
    fake._slice = HistoricalReplay._slice.__get__(fake)
    t = int(bars["timestamp"].iloc[-1])
    return HistoricalReplay._snapshot(fake, symbol, t, d, plan, "USDM_PERP", {symbol: 100.0})


def _outcome_r(i: int, side: str, good: bool) -> float:
    """Gömülü örüntü — mükemmel ayrışma DEĞİL (gürültü var), yoksa test kolaycılık yapardı."""
    if not good:                                   # SHORT + HIGH_VOL + negatif pattern
        return 0.6 if i % 9 == 0 else -1.2         # ~%11 tesadüfi kazanç
    return -0.5 if i % 7 == 0 else 0.9             # ~%14 tesadüfi kayıp


def build_memory(tmp_path: Path, *, n: int = N_TRADES, telemetry=None) -> tuple[TradeMemory, list[dict]]:
    """GERÇEK TradeMemory'ye giriş+çıkış yazar ve join'lenmiş satırları döndürür."""
    mem = TradeMemory(tmp_path / "trade_memory.jsonl", source="HISTORICAL_REPLAY")
    for i in range(n):
        sym = SYMBOLS[i % len(SYMBOLS)]
        side = "SHORT" if i % 2 else "LONG"
        good = side == "LONG"
        snap = _replay_snapshot(*_decision(sym, side, good=good), symbol=sym,
                                hl=(HL_NORMAL if good else HL_HIGH), seed=i, telemetry=telemetry)
        assert snap is not None, "snapshot üretilemedi — senaryo kurulamaz"
        opened = BASE + timedelta(days=i)
        closed = opened + timedelta(days=1)
        r = _outcome_r(i, side, good)
        mem.record_entry({"trade_id": f"E{i:03d}", "symbol": sym, "direction": side, "market_type": "USDM_PERP",
                          "setup_type": "pullback", "regime": "TREND_UP" if good else "HIGH_VOL",
                          "features": snap.vector(), "snapshot": snap.to_dict(),
                          "recorded_at": opened.isoformat()})
        mem.record_exit(f"E{i:03d}", {"symbol": sym, "side": side, "r_multiple": r,
                                      "mae_pct": -0.4 if r > 0 else -2.6, "mfe_pct": 3.1 if r > 0 else 0.3,
                                      "exit_reason": "hedef1" if r > 0 else "stop", "bars_held": 6,
                                      "opened_at": opened.isoformat(), "closed_at": closed.isoformat()})
    rows = mem.trades(closed_only=True)
    for r in rows:                                  # walk-forward zaman alanları
        o = r.get("outcome") or {}
        r["_open_ms"] = int(datetime.fromisoformat(o["opened_at"]).timestamp() * 1000)
        r["_close_ms"] = int(datetime.fromisoformat(o["closed_at"]).timestamp() * 1000)
    return mem, rows


def _bounds(n_folds: int = 4, *, span: int = N_TRADES) -> list[dict]:
    """Ardışık walk-forward pencereleri: train geçmiş, purge+embargo, ayrı OOS test."""
    def ms(day: int) -> int:
        return int((BASE + timedelta(days=day)).timestamp() * 1000)
    out, width = [], span // (n_folds + 1)
    for k in range(n_folds):
        tr_e = width * (k + 1)
        out.append({"idx": k, "train_start_ms": ms(0), "train_end_ms": ms(tr_e),
                    "purge_start_ms": ms(tr_e), "purge_end_ms": ms(tr_e + 1),
                    "embargo_start_ms": ms(tr_e + 1), "embargo_end_ms": ms(tr_e + 2),
                    "test_start_ms": ms(tr_e + 2), "test_end_ms": ms(min(span, tr_e + width)),
                    "purge_bars": 1, "embargo_bars": 1, "bar_ms": DAY})
    return out


def _cfg(tmp_path: Path):
    import json
    from tradingbot.config import BotConfig
    from tradingbot.config_v3 import load_v3
    cfg = BotConfig()
    cfg.project_root = tmp_path
    cfg.obsidian.vault_path = str(tmp_path / "vault")
    cfg.v3 = load_v3({"mode": "PAPER", "learning_v3": {"min_samples_train": 20}})
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    (cfg.state_path / "mode.json").write_text(json.dumps({"mode": "PAPER"}), encoding="utf-8")
    return cfg


# =========================================================================== 1) hafıza + join + coverage
def test_pipeline_produces_joined_learnable_memory(tmp_path):
    tel = SnapshotTelemetry()
    _mem, rows = build_memory(tmp_path, telemetry=tel)
    assert len(rows) == N_TRADES
    assert all(r.get("snapshot") and r.get("outcome") for r in rows)       # entry/outcome JOIN
    assert tel.counters["snapshot_success_total"] == N_TRADES and tel.counters["snapshot_failure_total"] == 0
    rep = coverage_report(rows, source="HISTORICAL_REPLAY")
    assert rep["ok"], rep["problems"]
    assert rep["required_available_pct"] >= 90.0 and rep["overall_available_pct"] >= 55.0
    assert rep["invalid_timestamps"] == 0 and rep["join"]["broken"] == 0
    # senaryo gerçekten iki farklı volatilite rejimi üretmiş olmalı (elle yazılmadı)
    regimes = {r["snapshot"]["values"].get("vol_regime_code") for r in rows}
    assert regimes == {1.0, 2.0}, regimes


# =========================================================================== 2) kayıp analizi örüntüyü buluyor
def test_attribution_finds_the_planted_bad_combination(tmp_path):
    _mem, rows = build_memory(tmp_path)
    rep = attribution_report(rows, min_bucket=8)
    labels = {(f["cut"], f["label"]): f for f in rep["findings"]}
    short_hv = labels.get(("side_x_regime", "SHORT|HIGH_VOL"))
    assert short_hv and short_hv["direction"] == "NEGATIF", rep["negative_findings"][:5]
    assert short_hv["expectancy_r"] < 0 and short_hv["ci95_low"] < 0 and short_hv["n"] >= 8
    assert labels[("vol_regime", "HIGH_VOL")]["direction"] == "NEGATIF"
    assert labels[("side", "SHORT")]["direction"] == "NEGATIF"
    assert labels[("pattern_conf", "PATTERN_NEGATİF")]["direction"] == "NEGATIF"
    # LONG/NORMAL tarafı pozitif ayrışmalı
    assert labels[("side", "LONG")]["direction"] == "POZITIF"
    # işlem bazlı yapılandırılmış analiz
    losers = rep["trades"]
    assert losers and all(t["net_r"] < 0 for t in losers)
    t0 = next(t for t in losers if t["side"] == "SHORT")
    assert t0["snapshot_hash"] and t0["strongest_adverse_conditions"]
    assert set(t0["loss_classes"]) & {"VOLATILITY_MISMATCH", "PATTERN_NEGATIVE", "AGENT_DISAGREEMENT"}
    assert all(c in LOSS_CLASSES for t in losers for c in t["loss_classes"])
    assert t0["association_not_causation"] is True and "nedensellik" in rep["disclaimer"].lower()


# =========================================================================== 3) aday üretimi
def _candidates(rows) -> list[CandidatePolicy]:
    return candidates_from_attribution(attribution_report(rows, min_bucket=8), seed=7, max_candidates=12)


def test_candidates_are_explainable_bounded_and_minimal(tmp_path):
    _mem, rows = build_memory(tmp_path)
    cands = _candidates(rows)
    assert cands, "kayıp analizinden aday üretilemedi"
    for c in cands:
        validate_policy(c, risk_profile_max_leverage=1.0)
        assert c.rationale and c.source_findings                          # neden üretildiği yazılı
        assert 0 < len(c.changed_params()) <= MAX_CHANGES_PER_CANDIDATE   # az sayıda, ölçülebilir değişiklik
        assert c.size_multiplier <= 1.0 and c.max_leverage_cap <= 1.0     # risk YÜKSELTİLEMEZ
        assert c.filters_enabled and c.to_dict()["capabilities"]["can_only_filter_or_shrink"]
    # gömülü örüntüyü hedefleyen aday gerçekten üretilmiş olmalı
    assert any("SHORT|HIGH_VOL" in (c.side_regime_veto or []) for c in cands)
    assert any(c.max_vol_regime is not None for c in cands)
    # determinizm: aynı girdi → aynı id sırası
    assert [c.policy_id for c in cands] == [c.policy_id for c in _candidates(rows)]


def test_candidate_can_only_filter_or_shrink_never_raise_risk(tmp_path):
    _mem, rows = build_memory(tmp_path)
    snap_bad = {"vol_regime_code": 2.0, "consensus_score": -0.1, "spread_pct": 0.02, "pattern_ci_low": -0.3,
                "n_dissent": 4.0}
    for c in _candidates(rows):
        for side in ("LONG", "SHORT"):
            d = c.decide(snap_bad, side=side, symbol="ETH/USDT", p_win=0.47, expected_net_r=1.2)
            assert 0.0 <= d["size_multiplier"] <= 1.0                     # asla > 1.0
            assert d["allow"] in (True, False)
    base = baseline_policy()
    assert base.decide(snap_bad, side="SHORT", symbol="X", p_win=0.0, expected_net_r=-9)["allow"] is True


# =========================================================================== 4) walk-forward OOS
def _evaluate(tmp_path, rows, cands, **kw):
    cfg = _cfg(tmp_path)
    rdir = cfg.state_path / "replay" / "e2e"
    rdir.mkdir(parents=True, exist_ok=True)
    return evaluate_policies(cfg, rdir, rows, _bounds(), seed=7, min_test_trades=8,
                             candidates=cands, **kw)


def test_walk_forward_candidate_beats_baseline_on_oos(tmp_path):
    _mem, rows = build_memory(tmp_path)
    rep = _evaluate(tmp_path, rows, _candidates(rows), point_in_time=True, survivorship_present=False)
    assert rep["scored_folds"] >= 2
    assert rep["candidate"]["expectancy_r"] > rep["baseline"]["expectancy_r"]
    assert rep["delta_expectancy_r"] > 0 and rep["paired_diff_mean"] > 0
    assert rep["fold_consistency"] >= 0.6
    assert rep["duplicate_test_rows"] == 0 and rep["gates"]["no_duplicate_test_rows"]
    assert rep["verdict"] in ("SHADOW_CANDIDATE", "RESEARCH_ONLY")
    assert rep["promotion"]["live_promotion"] is False and rep["promotion"]["promote_called"] is False
    # kırılım YALNIZ OOS satırlarından ve fold'un seçtiği adaydan
    assert sum(m["n"] for m in rep["breakdown"]["baseline"]["by_side"].values()) == rep["oos_test_rows"]
    assert rep["breakdown"]["candidate"]["by_side"].keys() <= {"LONG", "SHORT"}
    # zorunlu metrikler mevcut
    for k in ("expectancy_r", "win_rate", "profit_factor", "max_dd_r", "n"):
        assert k in rep["candidate"] and k in rep["baseline"]
    assert rep["candidate"]["ci95"] and rep["baseline"]["ci95"]


def test_all_required_walk_forward_gates_are_evaluated(tmp_path):
    """Madde 9'daki zorunlu kapıların tamamı raporda bulunmalı ve gerçek veriden ölçülmeli."""
    _mem, rows = build_memory(tmp_path)
    rep = _evaluate(tmp_path, rows, _candidates(rows), point_in_time=True, survivorship_present=False)
    required = {"feature_coverage_valid", "no_timestamp_leakage", "join_intact", "policy_bounds_valid",
                "drawdown_acceptable", "enough_oos", "candidate_positive", "beats_baseline",
                "candidate_ci_low_above_zero", "profit_factor_above_one", "fold_consistency",
                "enough_folds", "no_duplicate_test_rows", "point_in_time", "survivorship_clean"}
    assert required <= set(rep["gates"]), required - set(rep["gates"])
    assert rep["gates"]["feature_coverage_valid"] and rep["gates"]["no_timestamp_leakage"]
    assert rep["gates"]["join_intact"] and rep["gates"]["drawdown_acceptable"]
    assert rep["coverage"]["ok"] and rep["coverage"]["invalid_timestamps"] == 0
    assert "Brier" in rep["model_calibration_note"]          # nerede ölçüldüğü açıkça yazılı


def test_pit_or_survivorship_caps_verdict_at_research_only(tmp_path):
    _mem, rows = build_memory(tmp_path)
    rep = _evaluate(tmp_path, rows, _candidates(rows), point_in_time=False, survivorship_present=True)
    assert rep["verdict"] in ("RESEARCH_ONLY", "REJECTED")
    assert not rep["gates"]["point_in_time"] and not rep["gates"]["survivorship_clean"]


def test_selection_never_sees_its_own_test_fold(tmp_path):
    """Aday seçimi yalnız train'in iç validation dilimini görür — sözleşme raporda yazılı ve
    seçim skoru test fold'undan bağımsız."""
    _mem, rows = build_memory(tmp_path)
    rep = _evaluate(tmp_path, rows, _candidates(rows))
    assert "test fold'u seçime GİRMEZ" in rep["method"]["selection"]
    for f in rep["folds"]:
        if "selected_policy" in f:
            assert f["validation_n"] > 0 and f["validation_n"] <= f["n_train"]


# =========================================================================== 5) araştırma durum makinesi
def _gates() -> ResearchGates:
    return ResearchGates(min_shadow_obs=6, min_active_obs=6, min_review_obs=10, cooldown_hours=0.0,
                         retire_delta_r=-0.10, activate_delta_r=0.0, review_delta_r=0.05)


def _best_candidate(rows) -> CandidatePolicy:
    cands = _candidates(rows)
    return next(c for c in cands if "SHORT|HIGH_VOL" in (c.side_regime_veto or []))


def test_research_state_machine_full_lifecycle(tmp_path):
    now = BASE + timedelta(days=200)
    _mem, rows = build_memory(tmp_path)
    cand = _best_candidate(rows)
    report = _evaluate(tmp_path, rows, [cand], point_in_time=True, survivorship_present=False)
    book = ResearchPolicyBook(tmp_path / "research_policy.json", _gates())
    rec = book.propose(cand, now=now)
    assert rec.state == PROPOSED
    assert book.record_offline(cand.policy_id, report, now=now) == OFFLINE_VALIDATED
    assert book.start_shadow(cand.policy_id, now=now) == SHADOW
    assert book.active_policy() is None                                   # SHADOW canlı davranışı DEĞİŞTİRMEZ
    for i in range(8):                                                    # aday kötü işlemleri eliyor
        book.observe(cand.policy_id, trade_id=f"O{i}", baseline_r=-1.2, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.maybe_activate(now=now + timedelta(hours=1)) == cand.policy_id
    assert book.active_policy().policy_id == cand.policy_id
    # sürdürülen iyileşme → yalnız MANUEL İNCELEME işareti (CHAMPION/LIVE DEĞİL)
    for i in range(8, 14):
        book.observe(cand.policy_id, trade_id=f"O{i}", baseline_r=-1.2, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.evaluate_active(now=now + timedelta(hours=2)) == REVIEW
    assert book.to_dict()["auto_promotion_possible"] is False
    reloaded = ResearchPolicyBook(tmp_path / "research_policy.json", _gates())   # kalıcı
    assert reloaded.get(cand.policy_id).state == REVIEW


def test_degrading_candidate_is_retired_and_rolls_back_to_baseline(tmp_path):
    now = BASE + timedelta(days=200)
    _mem, rows = build_memory(tmp_path)
    cand = _best_candidate(rows)
    book = ResearchPolicyBook(tmp_path / "rp.json", _gates())
    book.propose(cand, now=now)
    book.record_offline(cand.policy_id, {"verdict": "SHADOW_CANDIDATE"}, now=now)
    book.start_shadow(cand.policy_id, now=now)
    for i in range(7):
        book.observe(cand.policy_id, trade_id=f"G{i}", baseline_r=-1.0, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.maybe_activate(now=now + timedelta(hours=1)) == cand.policy_id
    for i in range(7, 20):                                                # aday artık KAZANANLARI eliyor
        book.observe(cand.policy_id, trade_id=f"G{i}", baseline_r=1.0, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.evaluate_active(now=now + timedelta(hours=2)) == RETIRED
    assert book.active() is None and book.active_policy() is None          # baseline'a dönüldü
    assert "kötüleşme" in book.get(cand.policy_id).retired_reason
    # baseline geri dönüş noktası: politika yokken hiçbir giriş elenmez
    res = apply_research_policy(None, {"vol_regime_code": 3.0}, side="SHORT", symbol="X",
                                p_win=0.0, expected_net_r=-5.0)
    assert res["allow"] and res["size_multiplier"] == 1.0


def test_rejected_offline_verdict_never_enters_research(tmp_path):
    now = BASE + timedelta(days=200)
    _mem, rows = build_memory(tmp_path)
    cand = _best_candidate(rows)
    book = ResearchPolicyBook(tmp_path / "rp.json", _gates())
    book.propose(cand, now=now)
    assert book.record_offline(cand.policy_id, {"verdict": "REJECTED", "failed_gates": ["candidate_positive"]},
                               now=now) == RETIRED
    with pytest.raises(ResearchSafetyError):
        book.start_shadow(cand.policy_id, now=now)
    assert book.maybe_activate(now=now + timedelta(days=1)) is None


def test_research_only_verdict_can_never_activate(tmp_path):
    now = BASE + timedelta(days=200)
    _mem, rows = build_memory(tmp_path)
    cand = _best_candidate(rows)
    book = ResearchPolicyBook(tmp_path / "rp.json", _gates())
    book.propose(cand, now=now)
    book.record_offline(cand.policy_id, {"verdict": "RESEARCH_ONLY"}, now=now)
    book.start_shadow(cand.policy_id, now=now)
    for i in range(20):
        book.observe(cand.policy_id, trade_id=f"R{i}", baseline_r=-1.0, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.maybe_activate(now=now + timedelta(days=1)) is None
    assert book.get(cand.policy_id).state == SHADOW


def test_insufficient_samples_and_cooldown_block_policy_change(tmp_path):
    now = BASE + timedelta(days=200)
    _mem, rows = build_memory(tmp_path)
    cand = _best_candidate(rows)
    gates = ResearchGates(min_shadow_obs=10, min_active_obs=10, cooldown_hours=48.0)
    book = ResearchPolicyBook(tmp_path / "rp.json", gates)
    book.propose(cand, now=now)
    book.record_offline(cand.policy_id, {"verdict": "SHADOW_CANDIDATE"}, now=now)
    book.start_shadow(cand.policy_id, now=now)
    for i in range(4):                                                    # yetersiz örnek
        book.observe(cand.policy_id, trade_id=f"C{i}", baseline_r=-1.0, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.maybe_activate(now=now + timedelta(days=10)) is None
    for i in range(4, 12):
        book.observe(cand.policy_id, trade_id=f"C{i}", baseline_r=-1.0, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    assert book.maybe_activate(now=now + timedelta(hours=1)) is None      # cooldown dolmadı
    assert book.maybe_activate(now=now + timedelta(hours=49)) == cand.policy_id


def test_same_trade_is_never_counted_twice(tmp_path):
    now = BASE + timedelta(days=200)
    _mem, rows = build_memory(tmp_path)
    cand = _best_candidate(rows)
    book = ResearchPolicyBook(tmp_path / "rp.json", _gates())
    book.propose(cand, now=now)
    assert book.observe(cand.policy_id, trade_id="X1", baseline_r=-1.0, candidate_r=0.0,
                        allowed=False, size_multiplier=0.0, at=now) is True
    assert book.observe(cand.policy_id, trade_id="X1", baseline_r=5.0, candidate_r=5.0,
                        allowed=True, size_multiplier=1.0, at=now) is False
    assert book.get(cand.policy_id).stats()["n_obs"] == 1


# =========================================================================== 6) negatif kontroller
def test_no_candidate_from_insufficient_or_flat_data(tmp_path):
    _mem, rows = build_memory(tmp_path, n=10)
    assert candidates_from_attribution(attribution_report(rows, min_bucket=8), seed=7) == []
    flat = [dict(r, outcome=dict(r["outcome"], r_multiple=0.5)) for r in rows]
    assert candidates_from_attribution(attribution_report(flat, min_bucket=3), seed=7) == []


def test_sparse_memory_is_blocked_by_coverage_gate():
    from conftest import sparse_features
    rows = [{"trade_id": f"S{i}", "source": "HISTORICAL_REPLAY", "features": sparse_features(),
             "outcome": {"symbol": "ETH/USDT", "side": "LONG", "r_multiple": -1.0}} for i in range(40)]
    rep = coverage_report(rows, source="HISTORICAL_REPLAY")
    assert not rep["ok"] and rep["code"] == "FEATURE_COVERAGE_INVALID"
    assert any("FeatureSnapshotV3" in p for p in rep["problems"])


def test_negative_oos_candidate_is_rejected(tmp_path):
    """Kazananları eleyen bir aday OOS'ta REJECTED olmalı ve araştırmaya giremez."""
    _mem, rows = build_memory(tmp_path)
    bad = CandidatePolicy(policy_id="bad_1", seed=7, side_veto=["LONG"],
                          rationale="kasten kötü: kazanan tarafı eliyor")
    rep = _evaluate(tmp_path, rows, [bad])
    assert rep["verdict"] == "REJECTED", rep["failed_gates"]
    book = ResearchPolicyBook(tmp_path / "rp2.json", _gates())
    book.propose(bad, now=BASE)
    assert book.record_offline(bad.policy_id, rep, now=BASE) == RETIRED


def test_timestamp_leakage_is_blocked_fail_closed():
    from tradingbot.learn.snapshot import LeakageError, build_snapshot
    bars = synth_bars(end_ms=1_700_000_000_000, bar_ms=H4)
    with pytest.raises(LeakageError):
        build_snapshot(symbol="X/USDT", market_type="USDM_PERP", timeframe="4h", side="LONG",
                       decision_ts_ms=int(bars["timestamp"].iloc[-1]) - 5 * H4, bars=bars,
                       plan={"entry": 100.0, "stop": 97.0, "targets": [106.0]})


def test_paper_auto_promotion_config_is_fail_closed():
    from tradingbot.config_v3 import ConfigError, load_v3
    with pytest.raises(ConfigError, match="PAPER_AUTO_PROMOTION_FORBIDDEN"):
        load_v3({"mode": "PAPER", "learning_v3": {"auto_promote_in_paper": True}})


def test_research_layer_cannot_touch_risk_or_open_positions(tmp_path):
    """Araştırma katmanı ne risk limitini ne de açık pozisyonun stop/TP'sini değiştirebilir."""
    import inspect
    from tradingbot.learn import research_policy as rp
    src = inspect.getsource(rp)
    for forbidden in ("ledger", ".stop =", "targets =", "risk_per_trade", "maybe_promote", "live_order"):
        assert forbidden not in src, f"araştırma katmanı {forbidden} içeremez"
    book = ResearchPolicyBook(tmp_path / "rp.json", _gates())
    with pytest.raises(Exception):                                        # riski yükselten aday deftere giremez
        book.propose(CandidatePolicy(policy_id="raise", seed=1, size_multiplier=1.5), now=BASE)
    for state in ("CHAMPION", "LIVE", "TESTNET", "PROMOTED"):
        rec = book.propose(CandidatePolicy(policy_id=f"p_{state}", seed=1, side_veto=["SHORT"]), now=BASE)
        with pytest.raises(ResearchSafetyError):
            book._set_state(rec, state, at=BASE)


# =========================================================================== 7) determinizm + gözlemlenebilirlik
def test_whole_loop_is_deterministic(tmp_path):
    a_mem, a_rows = build_memory(tmp_path / "a")
    b_mem, b_rows = build_memory(tmp_path / "b")
    assert [r["snapshot"]["snapshot_hash"] for r in a_rows] == [r["snapshot"]["snapshot_hash"] for r in b_rows]
    ca = [c.hash() for c in _candidates(a_rows)]
    cb = [c.hash() for c in _candidates(b_rows)]
    assert ca == cb and ca
    ra = _evaluate(tmp_path / "ea", a_rows, _candidates(a_rows))
    rb = _evaluate(tmp_path / "eb", b_rows, _candidates(b_rows))
    for k in ("verdict", "fold_consistency", "delta_expectancy_r", "selected_policies"):
        assert ra[k] == rb[k]


def test_dashboard_exposes_the_learning_state(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import json
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import DashboardConfig, create_app
    from tradingbot.dashboard.state import StateReader
    now = BASE + timedelta(days=200)
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(parents=True); data.mkdir()
    _mem, rows = build_memory(tmp_path / "mem")
    cand = _best_candidate(rows)
    book = ResearchPolicyBook(st / "research_policy.json", _gates())
    book.propose(cand, now=now)
    book.record_offline(cand.policy_id, {"verdict": "SHADOW_CANDIDATE"}, now=now)
    book.start_shadow(cand.policy_id, now=now)
    for i in range(8):
        book.observe(cand.policy_id, trade_id=f"D{i}", baseline_r=-1.2, candidate_r=0.0,
                     allowed=False, size_multiplier=0.0, at=now)
    book.maybe_activate(now=now + timedelta(hours=1))
    SnapshotTelemetry(path=st / "snapshot_telemetry.json",
                      counters={"snapshot_success_total": 160, "snapshot_failure_total": 0,
                                "leakage_failure_total": 0, "schema_mismatch_total": 0}).save()
    (st / "killswitch.json").write_text(json.dumps({"state": "ARMED", "since": "", "reasons": []}), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")

    lr = StateReader(st).learning_research()
    assert lr["active_policy_id"] == cand.policy_id and lr["active_rationale"]
    assert lr["active_changed_params"] and lr["auto_promotion_possible"] is False
    assert lr["active_stats"]["n_obs"] == 8 and lr["active_stats"]["blocked"] == 8

    c = TestClient(create_app(st, data, None, DashboardConfig()))
    ov = c.get("/api/overview").json()
    assert ov["learning_research"]["active_policy_id"] == cand.policy_id
    assert ov["mode"] == "PAPER"
    body = c.get("/metrics").text
    assert "tradingbot_research_policy_active 1" in body
    assert "tradingbot_research_observations 8" in body
    assert "tradingbot_auto_promotion_enabled 0" in body
    assert "tradingbot_snapshot_success_total 160" in body
    health = c.get("/health").text
    assert "Araştırma politikası" in health and "KAPALI" in health
