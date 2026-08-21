"""Train/serve paritesi, ajan eşlemesi, coverage ve otomatik terfi kapalılığı — GERÇEK çağrı yolları.

Buradaki parite testi `build_snapshot()`'ı iki kez çağırmaz (totolojik olurdu). Bunun yerine
`TradingEngineV3._snapshot_v3` ve `HistoricalReplay._snapshot` metotlarının **gerçek gövdeleri**
hafif birer `self` vekiliyle çalıştırılır; böylece iki çağrı yerinin alan eşlemesi, timeframe seçimi
ve plan/karar erişimleri gerçekten karşılaştırılır. (Eski hata: iki yol da var olmayan `agent_reports`
anahtarını okuyordu ve totolojik test bunu göremiyordu.)
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from conftest import synth_bars
from tradingbot.coinhead.factors import FACTOR_GROUPS
from tradingbot.coinhead.schema import CoinHeadDecision, FactorGroupScore, PlanSize, TradePlanV3, Verdict
from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn.coverage import coverage_report
from tradingbot.learn.snapshot import (AGENT_NAMES, AUDIT_ONLY_FIELDS, PREDICTION_FIELD_NAMES,
                                       FeatureSnapshotV3, prediction_feature_names, prediction_schema_hash)
from tradingbot.learn.telemetry import SnapshotTelemetry, sanitize_code
from tradingbot.replay.engine import HistoricalReplay

H4 = 14_400_000
SYM = "ETH/USDT"
END = 1_700_000_000_000


# =========================================================================== fixture yardımcıları
def _plan(direction: str = "LONG", entry: float = 100.0) -> TradePlanV3:
    long = direction == "LONG"
    return TradePlanV3(market_type="futures", direction=direction, entry_type="pullback",
                       entry_zone=(entry, entry), stop=entry * (0.97 if long else 1.03),
                       targets=[entry * (1.06 if long else 0.94), entry * (1.12 if long else 0.88)],
                       time_horizon_bars=12, size=PlanSize(amount=1.0, leverage=2), margin=15.0,
                       notional=30.0, expected_cost_pct=0.18, expected_r=1.8, valid=True)


def _pattern_evidence(direction: str) -> dict:
    """`patterns/engine.py::query` çıktısının gerçek şekli (taraf kırılımlı)."""
    return {direction: {"ok": True, "codes": [], "n": 42,
                        "stats": {"p_win_posterior": 0.58, "mean_net_r": 0.12, "profit_factor": 1.31,
                                  "expectancy_ci": [-0.04, 0.28]},
                        "neighbors": [{"symbol": "BTC/USDT", "distance": 0.11}],
                        "levels": {"same_coin": 30, "cluster": 12}}}


def _decision(direction: str = "LONG", strength: float = 0.4, *, entry: float = 100.0,
              pattern: bool = True, symbol: str = SYM) -> tuple[CoinHeadDecision, TradePlanV3]:
    plan = _plan(direction, entry)
    d = CoinHeadDecision(coin_head_id="ch", run_id="r", snapshot_id="s", symbol=symbol,
                         market_type="futures", regime="TREND_UP", direction=direction,
                         verdict=Verdict.FUTURES_LONG if direction == "LONG" else Verdict.FUTURES_SHORT)
    d.futures_plan = plan
    d.confidence_raw = 0.7
    d.confidence_calibrated = 0.62
    d.consensus_confidence = 0.55
    d.consensus = {"trend": strength, "momentum": strength / 2.0}
    d.dissent = ["volatility"]
    d.vetoes = []
    d.p_win = 0.54
    d.expected_r = plan.expected_r
    d.factor_scores = [FactorGroupScore(group=g, score=strength, confidence=0.6, data_quality=1.0,
                                        n_independent=2, conflict=0.1)
                       for g in ("trend", "momentum", "volatility", "volume_flow", "liquidity", "derivatives")]
    if pattern:
        d.pattern_evidence = _pattern_evidence(direction)
    return d, plan


class _Live:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}

    def snapshot(self, symbol: str) -> dict:
        return self.payload


def _live_snapshot(d, *, frames: dict, live_payload: dict | None = None, symbol: str = SYM,
                   telemetry: SnapshotTelemetry | None = None):
    """GERÇEK `TradingEngineV3._snapshot_v3` gövdesi, hafif `self` vekiliyle."""
    fake = SimpleNamespace(runner=SimpleNamespace(last_frames=frames, live=_Live(live_payload)),
                           run_id="parity", snap_telemetry=telemetry or SnapshotTelemetry())
    return TradingEngineV3._snapshot_v3(fake, symbol, d)


def _replay_snapshot(d, plan, *, frames: dict, t: int, symbol: str = SYM, entry: float = 100.0,
                     telemetry: SnapshotTelemetry | None = None, tf: str = "4h"):
    """GERÇEK `HistoricalReplay._snapshot` gövdesi (gerçek `_slice` dahil), hafif `self` vekiliyle."""
    fake = SimpleNamespace(frames=frames, tf=tf, lookback_bars=400, run_id="parity", seed=7,
                           snap_telemetry=telemetry or SnapshotTelemetry())
    fake._slice = HistoricalReplay._slice.__get__(fake)
    return HistoricalReplay._snapshot(fake, symbol, t, d, plan, "USDM_PERP", {symbol: entry})


def _frames(symbol: str = SYM, *, seed: int = 3, tf: str = "4h", with_btc: bool = True) -> tuple[dict, int]:
    bars = synth_bars(end_ms=END, seed=seed, bar_ms=H4)
    frames = {symbol: {tf: bars}}
    if with_btc:
        frames["BTC/USDT"] = {tf: synth_bars(end_ms=END, seed=seed + 11, drift=0.03, bar_ms=H4)}
    return frames, int(bars["timestamp"].iloc[-1])


# =========================================================================== 1) ajan eşlemesi
def test_agent_names_match_real_factor_groups():
    """Snapshot ajan yuvaları, kararın GERÇEKTEN ürettiği `factor_group` adlarıyla birebir olmalı."""
    assert tuple(AGENT_NAMES) == tuple(FACTOR_GROUPS)


def test_agent_fields_are_actually_filled_from_factor_scores():
    frames, t = _frames()
    snap = _replay_snapshot(*_decision(strength=0.4), frames=frames, t=t)
    assert snap is not None
    filled = [g for g in ("trend", "momentum", "volatility", "volume_flow", "liquidity", "derivatives")
              if f"agent_bias_{g}" not in snap.missing]
    assert len(filled) == 6, f"ajan alanları dolmadı: {snap.missing[:20]}"
    assert snap.values["agent_bias_trend"] == pytest.approx(0.4)
    assert snap.values["agent_conf_trend"] == pytest.approx(0.6)
    # raporlanmayan grup açıkça EKSİK kalır (sahte 0 değil)
    assert "agent_bias_catalyst" in snap.missing


# =========================================================================== 2) rr ve pattern gerçekten dolu
def test_plan_rr_is_really_computed_and_reaches_snapshot():
    plan = _plan("LONG", 100.0)
    assert plan.rr == pytest.approx(2.0)                      # |106-100| / |100-97|
    frames, t = _frames()
    snap = _replay_snapshot(*_decision(), frames=frames, t=t)
    assert "rr" not in snap.missing and snap.values["rr"] == pytest.approx(2.0)


def test_pattern_fields_come_from_existing_evidence_without_requery():
    frames, t = _frames()
    d, plan = _decision("LONG", pattern=True)
    snap = _replay_snapshot(d, plan, frames=frames, t=t)
    assert snap.values["pattern_n"] == pytest.approx(42)
    assert snap.values["pattern_p_win"] == pytest.approx(0.58)
    assert snap.values["pattern_expectancy_r"] == pytest.approx(0.12)
    assert snap.values["pattern_pf"] == pytest.approx(1.31)
    assert snap.values["pattern_ci_low"] == pytest.approx(-0.04)
    assert snap.values["pattern_distance"] == pytest.approx(0.11)
    assert snap.values["pattern_fallback_level"] == pytest.approx(1.0)      # cluster'a genişlemiş
    # taraf seçimi gerçek: SHORT kararı LONG kanıtını KULLANMAZ
    d_s, plan_s = _decision("SHORT", pattern=True)
    d_s.pattern_evidence = _pattern_evidence("LONG")
    snap_s = _replay_snapshot(d_s, plan_s, frames=frames, t=t)
    assert "pattern_n" in snap_s.missing


def test_pattern_query_is_not_called_a_second_time_for_snapshot():
    """Snapshot yalnız karardaki hazır kanıtı okur; pahalı sorgu fonksiyonuna erişmez."""
    src = inspect.getsource(HistoricalReplay._snapshot) + inspect.getsource(TradingEngineV3._snapshot_v3)
    assert "_pattern_evidence(" not in src and ".query(" not in src


# =========================================================================== 3) TRAIN/SERVE PARİTESİ
def test_replay_and_live_prediction_vectors_are_identical_on_same_context():
    """Aynı sentetik as-of bağlamda iki GERÇEK çağrı yolu aynı ad sırası, değer, missing ve hash üretir."""
    frames, t = _frames()
    d, plan = _decision("LONG")
    live = _live_snapshot(d, frames=frames, live_payload={})          # replay gibi: mikroyapı verisi yok
    rep = _replay_snapshot(d, plan, frames=frames, t=t)
    assert live is not None and rep is not None
    lv, rv = live.prediction_vector(), rep.prediction_vector()
    assert list(lv) == list(rv) == prediction_feature_names()          # ad + SIRA
    assert lv == rv                                                    # değerler
    assert sorted(live.missing) == sorted(rep.missing)                 # missing indicators
    assert live.hash() == rep.hash()
    assert live.timeframe == rep.timeframe == "4h"
    assert live.decision_ts == rep.decision_ts


def test_only_microstructure_differs_when_live_has_orderbook_data():
    """Canlıda mikroyapı varsa fark YALNIZ o alanlarda olur; şema/sıra/hash sözleşmesi bozulmaz."""
    frames, t = _frames()
    d, plan = _decision("LONG")
    live = _live_snapshot(d, frames=frames, live_payload={
        "ticker": {"spread_pct": 0.02, "depth_ratio": 1.1, "age_s": 12.0}, "funding": {"rate": 0.0001}})
    rep = _replay_snapshot(d, plan, frames=frames, t=t)
    lv, rv = live.prediction_vector(), rep.prediction_vector()
    assert list(lv) == list(rv)
    differing = {k for k in lv if lv[k] != rv[k]}
    assert differing == {"spread_pct", "depth_ratio", "data_freshness_s", "funding_rate",
                         "miss_spread_pct", "miss_depth_ratio", "miss_data_freshness_s", "miss_funding_rate"}
    for f in ("spread_pct", "depth_ratio", "data_freshness_s", "funding_rate"):
        assert lv[f"miss_{f}"] == 0.0 and rv[f"miss_{f}"] == 1.0       # eksiklik açıkça işaretli


def test_training_and_serving_use_the_same_builder_function():
    """Eğitim yolu (`prediction_vector_from_row`) ile serve yolu aynı vektörü üretir."""
    from tradingbot.learn.snapshot import prediction_vector_from_row
    frames, t = _frames()
    snap = _replay_snapshot(*_decision(), frames=frames, t=t)
    row = {"trade_id": "T1", "snapshot": snap.to_dict(), "outcome": {"r_multiple": 1.0}}
    assert prediction_vector_from_row(row) == snap.prediction_vector()
    assert prediction_vector_from_row({"snapshot": None}) is None       # v3 yoksa eğitime girmez
    assert prediction_vector_from_row({"snapshot": {"values": {}, "feature_version": 2}}) is None


# =========================================================================== 4) dairesel sızıntı / audit ayrımı
def test_prediction_vector_excludes_circular_and_post_decision_fields():
    assert "p_win_prior" in AUDIT_ONLY_FIELDS and "p_win_prior" not in PREDICTION_FIELD_NAMES
    assert "risk_allowed" in AUDIT_ONLY_FIELDS and "risk_allowed" not in PREDICTION_FIELD_NAMES
    names = prediction_feature_names()
    assert not any(n.startswith("miss_p_win_prior") or n == "p_win_prior" for n in names)
    # audit alanları YİNE DE kaydedilir (attribution/policy için) — yalnız modele girmez
    frames, t = _frames()
    snap = _replay_snapshot(*_decision(), frames=frames, t=t)
    assert "p_win_prior" in snap.values and "p_win_prior" not in snap.prediction_vector()


def test_missingness_indicators_reach_the_model_input():
    frames, t = _frames()
    snap = _replay_snapshot(*_decision(), frames=frames, t=t)
    pv = snap.prediction_vector()
    miss_keys = [k for k in pv if k.startswith("miss_")]
    assert len(miss_keys) >= 50 and pv["miss_spread_pct"] == 1.0


def test_prediction_schema_hash_is_stable_and_order_sensitive():
    import tradingbot.learn.snapshot as S
    h1 = prediction_schema_hash()
    assert h1 == prediction_schema_hash() and len(h1) == 64
    original = S.PREDICTION_FIELD_NAMES
    try:
        S.PREDICTION_FIELD_NAMES = list(reversed(original))
        assert prediction_schema_hash() != h1                   # sıra değişirse hash değişir
    finally:
        S.PREDICTION_FIELD_NAMES = original
    assert prediction_schema_hash() == h1


# =========================================================================== 5) timeframe doğruluğu
def test_live_snapshot_writes_the_actual_frame_key_not_a_hardcoded_4h():
    """4h yokken başka bir frame kullanıp yine "4h" yazmak yasak (namespace bozulur)."""
    bars = synth_bars(end_ms=END, seed=5, bar_ms=H4)
    frames = {SYM: {"1h": bars}}
    d, _ = _decision("LONG")
    snap = _live_snapshot(d, frames=frames)
    assert snap is not None and snap.timeframe == "1h"


def test_live_frame_selection_is_deterministic():
    bars_a = synth_bars(end_ms=END, seed=5, bar_ms=H4)
    bars_b = synth_bars(end_ms=END, seed=6, bar_ms=H4)
    d, _ = _decision("LONG")
    one = _live_snapshot(d, frames={SYM: {"1d": bars_a, "1h": bars_b}})
    two = _live_snapshot(d, frames={SYM: {"1h": bars_b, "1d": bars_a}})   # ekleme sırası farklı
    assert one.timeframe == two.timeframe == "1d"                        # sorted() -> deterministik
    assert one.hash() == two.hash()


def test_last_bar_ts_never_exceeds_decision_ts():
    frames, t = _frames()
    snap = _replay_snapshot(*_decision(), frames=frames, t=t)
    assert snap.last_bar_ts <= snap.decision_ts


# =========================================================================== 6) GERÇEK replay yolundan coverage
def _real_rows(n: int = 40) -> list[dict]:
    """Coverage'ı GERÇEK replay çağrı imzasından üretilen snapshot'larla ölç (elle kurgu değil)."""
    rows = []
    symbols = ("ETH/USDT", "SOL/USDT", "BNB/USDT", "BTC/USDT")
    for i in range(n):
        sym, side = symbols[i % 4], ("LONG" if i % 2 else "SHORT")
        frames, t = _frames(sym, seed=3 + (i % 9))
        d, plan = _decision(side, strength=0.4 if i % 2 else -0.3, symbol=sym)
        snap = _replay_snapshot(d, plan, frames=frames, t=t, symbol=sym)
        assert snap is not None
        rows.append({"trade_id": f"T{i}", "source": "HISTORICAL_REPLAY",
                     "recorded_at": f"2024-01-{(i % 28) + 1:02d}T00:00:00+00:00",
                     "features": snap.vector(), "snapshot": snap.to_dict(),
                     "outcome": {"symbol": sym, "side": side, "r_multiple": 1.0 if i % 3 else -1.0}})
    return rows


def test_real_replay_call_path_passes_the_unchanged_coverage_gate():
    """Eşikler DEĞİŞTİRİLMEDEN, gerçek alanlar dolduğu için geçmeli."""
    from tradingbot.learn.coverage import MAX_CONSTANT_RATIO, MIN_OVERALL_AVAILABLE, MIN_REQUIRED_AVAILABLE
    assert (MIN_REQUIRED_AVAILABLE, MIN_OVERALL_AVAILABLE, MAX_CONSTANT_RATIO) == (0.90, 0.55, 0.60)
    rep = coverage_report(_real_rows(), source="HISTORICAL_REPLAY")
    assert rep["ok"], rep["problems"]
    assert rep["required_available_pct"] >= 90.0
    assert rep["overall_available_pct"] >= 55.0
    assert rep["prediction_available_pct"] >= 55.0
    assert rep["nonconstant_ratio_pct"] > 40.0
    assert rep["invalid_timestamps"] == 0 and rep["join"]["broken"] == 0
    assert rep["missing_field_rate"] and rep["prediction_fields"] == len(PREDICTION_FIELD_NAMES)


def test_coverage_report_exposes_every_required_measurement():
    rep = coverage_report(_real_rows(12), source="HISTORICAL_REPLAY")
    for key in ("required_available_pct", "overall_available_pct", "prediction_available_pct",
                "nonconstant_ratio_pct", "missing_field_rate", "invalid_timestamps", "join"):
        assert key in rep


# =========================================================================== 7) telemetri
def test_snapshot_failure_is_counted_and_never_silent(tmp_path):
    t = SnapshotTelemetry.load(tmp_path)
    frames = {SYM: {"4h": synth_bars(end_ms=END, seed=3, bar_ms=H4).head(10)}}   # 30 bardan az
    d, _ = _decision("LONG")
    assert _live_snapshot(d, frames=frames, telemetry=t) is None
    frames_ok, _ts = _frames()
    assert _live_snapshot(d, frames=frames_ok, telemetry=t) is not None
    assert t.counters["snapshot_success_total"] == 1
    t.failure(ValueError("bir sey oldu"))
    assert t.counters["snapshot_failure_total"] == 1 and t.last_failure_code.startswith("ValueError")
    t.save()
    again = SnapshotTelemetry.load(tmp_path)
    assert again.counters["snapshot_failure_total"] == 1                # kalıcı, monoton


def test_leakage_failure_is_counted_separately():
    """`_slice` bozulsa bile builder fail-closed kalır: replay LeakageError'ı YUTMAZ, sayar ve yükseltir."""
    from tradingbot.learn.snapshot import LeakageError
    tel = SnapshotTelemetry()
    frames, ts = _frames()
    d, plan = _decision("LONG")
    bad = SimpleNamespace(frames=frames, tf="4h", lookback_bars=400, run_id="parity", seed=7,
                          snap_telemetry=tel)
    bad._slice = lambda sym, t: frames[sym]        # kasten bozuk dilimleme: gelecek barları sızdırır
    with pytest.raises(LeakageError):
        HistoricalReplay._snapshot(bad, SYM, ts - 5 * H4, d, plan, "USDM_PERP", {SYM: 100.0})
    assert tel.counters["leakage_failure_total"] == 1 and tel.counters["snapshot_failure_total"] == 1


def test_failure_code_is_sanitized_and_bounded():
    assert sanitize_code("a\nb\r\nc") == "a b c"
    assert len(sanitize_code("x" * 500)) <= 120
    assert sanitize_code(ValueError("API_KEY=abc")).startswith("ValueError")


def test_telemetry_is_readable_through_dashboard_state_and_metrics(tmp_path):
    pytest.importorskip("fastapi")
    import json
    from fastapi.testclient import TestClient
    from tradingbot.dashboard.app import DashboardConfig, create_app
    from tradingbot.dashboard.state import StateReader
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(); data.mkdir()
    SnapshotTelemetry(path=st / "snapshot_telemetry.json",
                      counters={"snapshot_success_total": 7, "snapshot_failure_total": 2,
                                "leakage_failure_total": 1, "schema_mismatch_total": 3},
                      last_failure_code="LeakageError: test", last_failure_at="2026-08-21T10:00:00+00:00").save()
    (st / "killswitch.json").write_text(json.dumps({"state": "ARMED", "since": "", "reasons": []}), encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    tel = StateReader(st).snapshot_telemetry()
    assert tel["counters"]["schema_mismatch_total"] == 3 and tel["last_failure_code"]
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    body = c.get("/metrics").text
    assert "tradingbot_snapshot_success_total 7" in body
    assert "tradingbot_snapshot_failure_total 2" in body
    assert "tradingbot_leakage_failure_total 1" in body
    assert "tradingbot_schema_mismatch_total 3" in body
    assert c.get("/api/overview").json()["snapshot_telemetry"]["counters"]["snapshot_success_total"] == 7
    assert "uyuşmazlık" in c.get("/health").text


# =========================================================================== 8) otomatik terfi KAPALI
def test_auto_promote_in_paper_defaults_to_false():
    from tradingbot.config_v3 import LearningV3Section, load_v3
    assert LearningV3Section().auto_promote_in_paper is False
    # eski config'te alan yoksa güvenli varsayılan (geriye dönük uyumluluk)
    cfg = load_v3({"mode": "PAPER", "learning_v3": {"enabled": True, "min_samples_train": 40}})
    assert cfg.learning_v3.auto_promote_in_paper is False
    assert cfg.learning_v3.min_samples_train == 40                       # verilen alanlar korunur
    assert load_v3({"mode": "PAPER", "learning_v3": {"auto_promote_in_paper": True}}).learning_v3.auto_promote_in_paper is True


def test_engine_checks_the_flag_before_calling_maybe_promote():
    src = inspect.getsource(TradingEngineV3)
    assert "maybe_promote" in src and "auto_promote_in_paper" in src
    call = src.index("maybe_promote(")
    flag = src.rindex("auto_promote_in_paper", 0, call)
    assert 0 < flag < call, "terfi çağrısı bayrak kontrolünden önce gelemez"


def test_candidate_never_enters_prediction_path_without_explicit_promotion(tmp_path):
    from datetime import datetime, timedelta, timezone
    from tradingbot.learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory, prediction_schema_hash
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    mem = TradeMemory(tmp_path / "mem.jsonl")
    reg = ModelRegistry(tmp_path / "models.json")
    lr = LearnerV2(mem, reg, LearnConfig(min_samples_train=40, holdout_frac=0.25), tmp_path / "learn_v2.json")
    frames, t = _frames()
    for i in range(60):
        won = i % 3 != 0
        snap = _replay_snapshot(*_decision("LONG", strength=0.6 if won else -0.45), frames=frames, t=t)
        mem.record_entry({"trade_id": f"T{i}", "symbol": SYM, "direction": "LONG", "setup_type": "pullback",
                          "snapshot": snap.to_dict(), "features": snap.vector(),
                          "recorded_at": (now - timedelta(days=60 - i)).isoformat()})
        mem.record_exit(f"T{i}", {"symbol": SYM, "side": "LONG", "r_multiple": 2.0 if won else -1.0})
    out = lr.train_challenger(now=now)
    assert out and reg.challenger("p_win_lr"), "challenger eğitilmeli"
    # 40+ kapanışa rağmen OTOMATİK terfi YOK
    assert reg.champion("p_win_lr") is None
    pv = FeatureSnapshotV3.from_dict(_replay_snapshot(*_decision(), frames=frames, t=t).to_dict()).prediction_vector()
    pr = lr.predict(pv, regime="TREND_UP", symbol=SYM, setup="pullback", schema_hash=prediction_schema_hash())
    assert not pr.ready and pr.model_id is None, "CANDIDATE kendiliğinden tahmin yoluna giremez"
    # LIVE/TESTNET davranışı değişmedi: manuel onay olmadan terfi yok
    mid = reg.challenger("p_win_lr")["id"]
    ok, reasons = reg.promote(mid, operator="t", mode="TESTNET")
    assert not ok and any("manuel" in r for r in reasons)
    ok2, _ = reg.promote(mid, operator="t", mode="LIVE")
    assert not ok2
    assert reg.champion("p_win_lr") is None


def test_legacy_champion_is_not_fed_a_v3_vector(tmp_path):
    """Deploy öncesinden kalan LEGACY şampiyona v3 vektörü GİTMEZ: fail-closed, prior'a dönülür.

    Simetrik kontrol olmasaydı `build_features()` v3 vektöründe legacy adları bulamaz, sessizce
    neredeyse-sıfır bir girdiyle tahmin üretirdi.
    """
    from tradingbot.learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory, prediction_schema_hash
    from tradingbot.learn.features import feature_names
    from tradingbot.learn.model import LogisticModel
    mem = TradeMemory(tmp_path / "mem.jsonl")
    reg = ModelRegistry(tmp_path / "models.json")
    import numpy as np
    names = feature_names()
    rng = np.random.default_rng(0)                      # deterministik: yalnız fit edilmiş bir scaler gerekli
    Xl = rng.normal(size=(40, len(names)))
    legacy = LogisticModel(feature_names=names).fit(Xl, (Xl[:, 0] > 0).astype(float))
    mid = reg.register("p_win_lr", legacy.to_dict(), {"n_holdout": 50, "ece": 0.05}, "CANDIDATE")
    ok, _ = reg.promote(mid, operator="test", mode="PAPER", force=True)
    assert ok and reg.champion("p_win_lr")
    lr = LearnerV2(mem, reg, LearnConfig(), tmp_path / "learn_v2.json")
    frames, t = _frames()
    pv = _replay_snapshot(*_decision(), frames=frames, t=t).prediction_vector()
    bad = lr.predict(pv, regime="TREND_UP", symbol=SYM, setup="pullback", schema_hash=prediction_schema_hash())
    assert not bad.ready and not bad.schema_ok and lr.telemetry.counters["schema_mismatch_total"] == 1
    good = lr.predict({"bias_trend": 0.5, "conviction": 0.6}, regime="TREND_UP", symbol=SYM, setup="pullback")
    assert good.ready and good.model_id == mid          # legacy çağıran + legacy model: köprü korunur


def test_sparse_memory_never_produces_a_model(tmp_path):
    """v3 snapshot'ı olmayan eski hafızada model ÜRETİLMEZ (sahte 0 ile doldurma yok)."""
    from datetime import datetime, timedelta, timezone
    from conftest import sparse_features
    from tradingbot.learn import LearnConfig, LearnerV2, ModelRegistry, TradeMemory
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    mem = TradeMemory(tmp_path / "mem.jsonl")
    reg = ModelRegistry(tmp_path / "models.json")
    lr = LearnerV2(mem, reg, LearnConfig(min_samples_train=40), tmp_path / "learn_v2.json")
    for i in range(60):
        mem.record_entry({"trade_id": f"S{i}", "symbol": SYM, "direction": "LONG", "features": sparse_features(),
                          "recorded_at": (now - timedelta(days=60 - i)).isoformat()})
        mem.record_exit(f"S{i}", {"symbol": SYM, "side": "LONG", "r_multiple": 1.0 if i % 2 else -1.0})
    assert lr.train_challenger(now=now) is None
    assert reg.challenger("p_win_lr") is None and reg.champion("p_win_lr") is None
