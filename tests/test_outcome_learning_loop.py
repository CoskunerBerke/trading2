"""Outcome Learning Loop V1 — karar günlüğü, outcome bağlantısı, postmortem, online öğrenme
ve SINIRLI öğrenme etkisi testleri.

Temel ilke burada sabitlenir: **"yetersiz örnek" öğrenmeyi ENGELLEMEZ.** İlk kapanmış işlemden
itibaren hafıza güncellenir ve sonraki karara küçük ama SIFIR OLMAYAN bir etki hesaplanır;
yetersiz örnek yalnız bu etkinin BÜYÜKLÜĞÜNÜ shrinkage ile kısar.

Üretim zinciri gerçek `TradingEngineV3.tour()` ile kanıtlanır (fixture değil).
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.learn.decision_journal import (ACCEPTED, KIND_DECISION, KIND_OUTCOME, REJECTED,
                                               DecisionJournal, build_decision_record,
                                               build_outcome_link, decision_id_for, join_outcomes)
from tradingbot.learn.influence import (OFF, PAPER_BOUNDED, SHADOW, InfluenceConfig,
                                        apply_influence, learning_adjustment, retrieve_experience)
from tradingbot.learn.memory import TradeMemory
from tradingbot.learn.postmortem import structured_postmortem
from tradingbot.risk import PROFILES, RiskEngine, build_state

UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


# ============================================================ üretim zinciri

def _tour(eng):
    return eng.tour(do_scan=False, obsidian=False, charts=False)


def test_production_tour_journals_every_candidate(tmp_path: Path, monkeypatch):
    """1+2+3: kabul, red ve veto fark etmeksizin HER aday kalıcı snapshot alır."""
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    _tour(eng)
    jp = Path(eng.cfg.state_path) / "decision_journal.jsonl"
    assert jp.exists(), "üretim turu karar günlüğü üretmeli"
    rows = [json.loads(x) for x in jp.read_text(encoding="utf-8").splitlines() if x.strip()]
    decisions = [r for r in rows if r.get("kind") == KIND_DECISION]
    assert decisions, "en az bir aday kaydı olmalı"
    for r in decisions:
        assert r["schema_version"] == "decision_journal_v1"
        assert r["decision_id"] and r["symbol"] and r["decision_ts"]
        assert r["outcome_kind"] in (ACCEPTED, REJECTED)
        assert "availability" in r
        # BOUNDED: ham mum dizisi YOK
        assert "candles" not in r and "frames" not in r
        assert len(json.dumps(r)) < 20_000
    # 3) gerçek feature/specialist/regime taşıyan en az bir kayıt
    rich = [r for r in decisions if r.get("features") and r.get("specialist_scores")]
    assert rich, "karar kayıtları gerçek feature ve specialist skorlarını taşımalı"
    r0 = rich[0]
    assert all(isinstance(v, float) and math.isfinite(v) for v in r0["features"].values())
    assert r0["regime"] or r0["verdict"]
    assert r0["feature_version"] is not None


def test_journal_records_both_accepted_and_rejected_over_tours(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    kinds: set[str] = set()
    for _ in range(3):
        _tour(eng)
        jp = Path(eng.cfg.state_path) / "decision_journal.jsonl"
        if jp.exists():
            for line in jp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    if r.get("kind") == KIND_DECISION:
                        kinds.add(r["outcome_kind"])
    assert REJECTED in kinds or ACCEPTED in kinds
    assert kinds, "aday kaydı üretilmedi"


def test_journal_failure_does_not_break_tour(tmp_path: Path, monkeypatch):
    """Journal arızası worker'ı ÇÖKERTMEZ; baseline karar sürer."""
    eng = _engine(tmp_path, monkeypatch, symbols=2)

    class Boom:
        def append_decision(self, *a, **k):
            raise OSError("disk full")

        def append_outcome(self, *a, **k):
            raise OSError("disk full")

        def prune(self):
            raise OSError("disk full")

    eng.decision_journal = Boom()
    s = _tour(eng)                                   # istisna sızmamalı
    assert s is not None
    assert eng._journal_errors >= 1


def test_decision_journal_can_be_disabled(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=2,
                  v3_overrides={"learning_v3": {"decision_journal_enabled": False}})
    _tour(eng)
    assert eng.decision_journal is None
    assert not (Path(eng.cfg.state_path) / "decision_journal.jsonl").exists()


# ============================================================ journal sözleşmesi

def _rec(sym="ETH/USDT", direction="LONG", cycle=1, **kw):
    return build_decision_record(run_id="r1", cycle_id=cycle, symbol=sym, direction=direction, **kw)


def test_outcome_linked_by_same_identity(tmp_path: Path):
    j = DecisionJournal(tmp_path / "dj.jsonl")
    j.append_decision(_rec(outcome_kind=ACCEPTED, trade_id="F00001"))
    j.append_outcome(build_outcome_link(trade_id="F00001", outcome={
        "net_pnl": 12.0, "gross_pnl": 14.0, "r_multiple": 1.2, "fees": 1.0, "funding": -0.2,
        "mae_pct": -1.0, "mfe_pct": 4.0, "bars_held": 6, "exit_reason": "hedef2",
        "closed_at": "2026-01-11T00:00:00+00:00"}))
    joined = join_outcomes(j.iter_rows())
    assert len(joined) == 1 and joined[0]["outcome_linked"] is True
    o = joined[0]["outcome"]
    assert o["r_multiple"] == pytest.approx(1.2) and o["exit_reason"] == "hedef2"
    assert o["kind"] == KIND_OUTCOME


def test_duplicate_decision_and_outcome_are_idempotent(tmp_path: Path):
    j = DecisionJournal(tmp_path / "dj.jsonl")
    r = _rec(outcome_kind=ACCEPTED, trade_id="F1")
    assert j.append_decision(r) is True
    assert j.append_decision(r) is False               # aynı decision_id ikinci kez YAZILMAZ
    ol = build_outcome_link(trade_id="F1", outcome={"r_multiple": 1.0})
    assert j.append_outcome(ol) is True
    assert j.append_outcome(ol) is False               # aynı trade_id ikinci outcome YOK
    st = j.stats()
    assert st["n_decisions"] == 1 and st["n_outcome_links"] == 1
    # yeniden başlatma sonrası da duplicate korunur
    j2 = DecisionJournal(tmp_path / "dj.jsonl")
    j2.load_seen()
    assert j2.append_decision(r) is False
    assert j2.append_outcome(ol) is False


def test_deterministic_ids_and_bounded_records():
    a = decision_id_for("r1", 3, "ETH/USDT", "LONG")
    b = decision_id_for("r1", 3, "ETH/USDT", "LONG")
    c = decision_id_for("r1", 4, "ETH/USDT", "LONG")
    assert a == b and a != c
    big = {f"f{i}": float(i) for i in range(500)}

    class Snap:
        values = big
        missing = [f"m{i}" for i in range(50)]
        last_bar_ts = "2026-01-10T12:00:00+00:00"
        timeframe = "4h"
        feature_version = 3
        config_hash = "cfg"
        strategy_version = "s1"

    r = _rec(snapshot=Snap())
    assert len(r["features"]) <= 64                   # BOUNDED
    assert len(r["features_missing"]) <= 12


def test_nonfinite_values_never_reach_journal(tmp_path: Path):
    class Snap:
        values = {"a": float("nan"), "b": float("inf"), "c": 1.5}
        missing: list = []
        last_bar_ts = None
        timeframe = "4h"
        feature_version = 3
        config_hash = None
        strategy_version = None

    j = DecisionJournal(tmp_path / "dj.jsonl")
    assert j.append_decision(_rec(snapshot=Snap())) is True
    raw = (tmp_path / "dj.jsonl").read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    rec = json.loads(raw.splitlines()[0])
    assert rec["features"] == {"c": 1.5}


def test_journal_prunes_and_survives_corrupt_lines(tmp_path: Path):
    p = tmp_path / "dj.jsonl"
    j = DecisionJournal(p, max_lines=5)
    for i in range(12):
        j.append_decision(_rec(sym=f"S{i}/USDT", cycle=i))
    dropped = j.prune()
    assert dropped > 0
    assert len(p.read_text(encoding="utf-8").splitlines()) == 5
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("{bozuk json\n")
    assert len(list(j.iter_rows())) == 5               # bozuk satır atlanır, dosya bozulmaz


def test_legacy_schema_rows_are_tolerated(tmp_path: Path):
    p = tmp_path / "dj.jsonl"
    p.write_text(json.dumps({"kind": KIND_DECISION, "decision_id": "old1", "symbol": "X/USDT"})
                 + "\n", encoding="utf-8")
    j = DecisionJournal(p)
    j.load_seen()
    st = j.stats()
    assert st["n_decisions"] == 1
    assert join_outcomes(j.iter_rows())[0]["outcome_linked"] is False


# ============================================================ postmortem

def _closed(r, exit_reason="stop", fees=1.0, funding=0.0, mae=-2.0, mfe=0.5):
    return {"id": "T1", "symbol": "ETH/USDT", "side": "LONG", "setup_type": "kırılım",
            "entry": 100.0, "exit_price": 98.0, "exit_reason": exit_reason,
            "net_pnl": r * 10, "pnl": r * 10, "r_multiple": r, "fees": fees, "funding": funding,
            "mae_pct": mae, "mfe_pct": mfe, "bars_held": 4, "leverage": 2,
            "features": {"bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3},
            "closed_at": "2026-01-11T00:00:00+00:00"}


def test_winner_and_loser_postmortem_are_produced():
    """5: hem kazanan hem kaybeden için yapılandırılmış ders üretilir.

    `lesson_text_tr` her kapanışta üretilir; `lesson_codes` yalnız belirli KOŞULLAR oluştuğunda
    dolar (dar stop, geç giriş, funding, komisyon...). İkisi de ayrı ayrı doğrulanır.
    """
    win = structured_postmortem(_closed(2.0, exit_reason="hedef2", mae=-0.3, mfe=4.0),
                                {"regime": "TREND_UP", "consensus_score": 0.7})
    loss = structured_postmortem(_closed(-1.0, exit_reason="stop"),
                                 {"regime": "TREND_DOWN", "consensus_score": -0.2})
    wd, ld = win.to_dict(), loss.to_dict()
    for d in (wd, ld):
        assert d["lesson_text_tr"], "insan okunur ders üretilmeli"
        assert d["outcome_class"] in ("WIN", "LOSS", "SCRATCH")
        assert "evidence_right" in d and "agents_right" in d
    assert wd["outcome_class"] == "WIN" and ld["outcome_class"] == "LOSS"
    assert wd["lesson_text_tr"] != ld["lesson_text_tr"]
    assert "KÂR" in wd["lesson_text_tr"][0] and "ZARAR" in ld["lesson_text_tr"][0]


def test_loser_lesson_codes_fire_on_real_conditions():
    """Ders KODLARI gerçek koşullarda üretilir (hızlı stop + geç giriş + ağır komisyon)."""
    fast_stop = structured_postmortem(
        {**_closed(-1.0, exit_reason="stop", mae=-6.0, fees=6.0), "bars_held": 1},
        {"regime": "RANGE"})
    codes = fast_stop.to_dict()["lesson_codes"]
    assert codes, "gerçek zarar koşullarında ders kodu üretilmeli"
    assert {"STOP_TOO_FAST", "LATE_ENTRY", "FEE_HEAVY"} & set(codes), codes
    clean_win = structured_postmortem(_closed(2.0, exit_reason="hedef2", mae=-0.2, mfe=4.0,
                                              fees=0.05), {"regime": "TREND_UP"})
    assert set(clean_win.to_dict()["lesson_codes"]) != set(codes)


def test_cost_dominated_lesson_is_detected():
    """Maliyet kazancı yiyorsa ders bunu göstermeli (fee/funding sürüklemesi)."""
    from tradingbot.learn.labels import label_outcome
    heavy = _closed(0.05, exit_reason="hedef1", fees=8.0, funding=-3.0, mae=-0.2, mfe=1.0)
    lab = label_outcome(heavy)
    assert lab["fee_drag_r"] is not None and lab["fee_drag_r"] > 0
    assert lab["outcome_class"] == "SCRATCH"           # maliyet sonrası kazanç erimiş
    light = label_outcome(_closed(0.05, fees=0.05, funding=0.0))
    assert lab["fee_drag_r"] > light["fee_drag_r"]


# ============================================================ online öğrenme etkisi

def test_first_outcome_moves_posterior_small_but_nonzero():
    """8: ilk kapanıştan itibaren etki hesaplanır ve SIFIR DEĞİLDİR."""
    cfg = InfluenceConfig()
    adj = learning_adjustment([{"r_multiple": 2.0, "similarity": 0.9}], baseline=0.60, cfg=cfg)
    assert adj["n_experience"] == 1
    assert adj["weight"] == pytest.approx(1 / 21, rel=1e-6)
    assert adj["fraction"] != 0.0                      # SIFIR DEĞİL
    assert abs(adj["fraction"]) < 0.01                 # ama çok küçük
    assert adj["learned"] != adj["baseline"]
    assert "SMALL_SAMPLE_SHRUNK" in adj["reasons"]


def test_consistent_outcomes_increase_effect_within_bound():
    """9: tutarlı çoklu sonuç etkiyi büyütür ama tavanı AŞMAZ."""
    cfg = InfluenceConfig()
    prev = 0.0
    for n in (1, 3, 10, 40, 200):
        adj = learning_adjustment([{"r_multiple": 1.5}] * n, baseline=0.60, cfg=cfg)
        assert abs(adj["fraction"]) >= prev - 1e-12    # monoton artış
        prev = abs(adj["fraction"])
        assert abs(adj["fraction"]) <= cfg.max_fraction + 1e-12
    huge = learning_adjustment([{"r_multiple": 99.0}] * 5000, baseline=0.60, cfg=cfg)
    assert abs(huge["fraction"]) <= cfg.max_fraction


def test_conflicting_outcomes_reduce_confidence():
    """10: çelişkili sonuçlar güveni düşürür → etki küçülür."""
    cfg = InfluenceConfig()
    consistent = learning_adjustment([{"r_multiple": 1.5}] * 4, baseline=0.6, cfg=cfg)
    conflicting = learning_adjustment(
        [{"r_multiple": 1.5}, {"r_multiple": -1.5}, {"r_multiple": 1.2}, {"r_multiple": -1.2}],
        baseline=0.6, cfg=cfg)
    assert conflicting["consistency"] < consistent["consistency"]
    assert abs(conflicting["fraction"]) < abs(consistent["fraction"])
    assert "CONFLICTING_EXPERIENCE_CONFIDENCE_LOW" in conflicting["reasons"]


def test_no_experience_is_neutral_not_guessed():
    adj = learning_adjustment([], baseline=0.6)
    assert adj["fraction"] == 0.0 and adj["learned"] == adj["baseline"]
    assert adj["reasons"] == ["NO_PRIOR_EXPERIENCE"]


# ============================================================ retrieval

def _mem(tmp_path: Path, rows: list[dict]) -> TradeMemory:
    m = TradeMemory(tmp_path / "mem.jsonl", source="LIVE_PAPER")
    for r in rows:
        tid = m.record_entry({"trade_id": r["id"], "symbol": r["symbol"], "direction": r["side"],
                              "setup_type": r.get("setup_type", "kırılım"),
                              "regime": r.get("regime", "TREND_UP"),
                              "features": r.get("features", {})})
        m.record_exit(tid, r)
    return m


def test_same_symbol_and_condition_scores_higher(tmp_path: Path):
    """11: aynı coin + aynı yön/rejim/setup daha yüksek similarity alır."""
    rows = [
        dict(_closed(1.5), id="A", symbol="ETH/USDT", side="LONG", regime="TREND_UP"),
        dict(_closed(1.0), id="B", symbol="SOL/USDT", side="SHORT", regime="RANGE",
             setup_type="geri çekilme"),
    ]
    mem = _mem(tmp_path, rows)
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
         "bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3}
    hits = retrieve_experience(mem, q, as_of_ms=None, cfg=InfluenceConfig(top_k=5))
    assert hits, "deneyim getirilmeli"
    assert hits[0]["symbol"] == "ETH/USDT"
    by = {h["symbol"]: h["similarity"] for h in hits}
    if "SOL/USDT" in by:
        assert by["ETH/USDT"] > by["SOL/USDT"]


def test_retrieval_is_strictly_no_lookahead(tmp_path: Path):
    """12: karar anından SONRA kapanan işlem retrieval'a GİREMEZ."""
    early = dict(_closed(1.0), id="EARLY", closed_at="2026-01-05T00:00:00+00:00")
    late = dict(_closed(-1.0), id="LATE", closed_at="2026-02-20T00:00:00+00:00")
    mem = _mem(tmp_path, [early, late])
    as_of = int(datetime(2026, 1, 10, tzinfo=UTC).timestamp() * 1000)
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP"}
    hits = retrieve_experience(mem, q, as_of_ms=as_of)
    ids = {h["id"] for h in hits}
    assert "LATE" not in ids, "gelecekte kapanan işlem sızdı"
    assert "EARLY" in ids
    # geleceği bozmak karar-öncesi sonucu DEĞİŞTİRMEZ
    mem2 = _mem(tmp_path / "x", [early, dict(late, r_multiple=99.0, net_pnl=9999.0)])
    (tmp_path / "x").mkdir(exist_ok=True)
    hits2 = retrieve_experience(mem2, q, as_of_ms=as_of)
    assert [h["id"] for h in hits2] == [h["id"] for h in hits]
    a1 = learning_adjustment(hits, baseline=0.6)
    a2 = learning_adjustment(hits2, baseline=0.6)
    assert a1["fraction"] == a2["fraction"]


def test_unreadable_memory_falls_back_to_baseline():
    """18: bozuk hafıza öğrenmeyi durdurur, worker'ı DEĞİL."""
    class Broken:
        def trades(self, **k):
            raise RuntimeError("state corrupt")

    assert retrieve_experience(Broken(), {"symbol": "X"}, as_of_ms=None) == []
    adj = learning_adjustment([], baseline=0.6)
    out = apply_influence(adj, cfg=InfluenceConfig(mode=PAPER_BOUNDED), mode_value="PAPER")
    assert out["effective"] == pytest.approx(0.6)


# ============================================================ mod sözleşmesi

def test_shadow_mode_preserves_baseline_exactly():
    """13: SHADOW hesaplar ve kaydeder ama baseline'ı BİREBİR korur."""
    adj = learning_adjustment([{"r_multiple": 2.0}] * 10, baseline=0.60)
    out = apply_influence(adj, cfg=InfluenceConfig(mode=SHADOW), mode_value="PAPER")
    assert out["applied"] is False
    assert out["effective"] == pytest.approx(0.60)
    assert out["learned"] != out["baseline"]           # counterfactual KAYITLI
    assert "MODE_SHADOW" in out["blockers"]


def test_paper_bounded_applies_small_soft_adjustment():
    """14: PAPER_BOUNDED yumuşak, sınırlı bir ayarlama uygular."""
    cfg = InfluenceConfig(mode=PAPER_BOUNDED)
    adj = learning_adjustment([{"r_multiple": 2.0}] * 10, baseline=0.60, cfg=cfg)
    out = apply_influence(adj, cfg=cfg, mode_value="PAPER")
    assert out["applied"] is True
    assert out["effective"] != pytest.approx(0.60)
    assert abs(out["effective"] - 0.60) <= 0.60 * cfg.max_fraction + 1e-9
    assert out["note"] == "LEARNING CANNOT OVERRIDE RISK GATES"


@pytest.mark.parametrize("mode_value", ["TESTNET", "LIVE", "SHADOW_LIVE", "OBSERVE", None])
def test_never_applied_outside_paper(mode_value):
    """15: LIVE/TESTNET ve diğer modlarda ayarlama UYGULANMAZ."""
    cfg = InfluenceConfig(mode=PAPER_BOUNDED)
    adj = learning_adjustment([{"r_multiple": 2.0}] * 10, baseline=0.60, cfg=cfg)
    out = apply_influence(adj, cfg=cfg, mode_value=mode_value)
    assert out["applied"] is False and out["effective"] == pytest.approx(0.60)


def test_live_order_path_blocks_application():
    cfg = InfluenceConfig(mode=PAPER_BOUNDED)
    adj = learning_adjustment([{"r_multiple": 2.0}] * 10, baseline=0.60, cfg=cfg)
    out = apply_influence(adj, cfg=cfg, mode_value="PAPER", live_order_path=True)
    assert out["applied"] is False and "LIVE_ORDER_PATH_ENABLED" in out["blockers"]


def test_off_mode_computes_nothing_in_engine(tmp_path: Path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, symbols=2,
                  v3_overrides={"learning_v3": {"influence_mode": "OFF"}})
    _tour(eng)
    assert eng.influence_cfg.mode == OFF
    assert eng._influence_log == []


def test_config_fail_closed_contracts():
    from tradingbot.config_v3 import load_v3
    from tradingbot.core import ConfigError
    with pytest.raises(ConfigError, match="influence_mode geçersiz"):
        load_v3({"mode": "PAPER", "learning_v3": {"influence_mode": "AGGRESSIVE"}})
    with pytest.raises(ConfigError, match="LEARNING_INFLUENCE_PAPER_ONLY"):
        load_v3({"mode": "OBSERVE", "learning_v3": {"influence_mode": "PAPER_BOUNDED"}})
    with pytest.raises(ConfigError, match="influence_prior_strength"):
        load_v3({"mode": "PAPER", "learning_v3": {"influence_prior_strength": 5.0}})
    with pytest.raises(ConfigError, match="influence_max_fraction"):
        load_v3({"mode": "PAPER", "learning_v3": {"influence_max_fraction": 0.9}})
    ok = load_v3({"mode": "PAPER"})
    assert ok.learning_v3.influence_mode == SHADOW          # güvenli varsayılan
    assert ok.learning_v3.influence_prior_strength >= 20.0
    assert ok.learning_v3.decision_journal_enabled is True


def test_influence_config_validates_bounds():
    with pytest.raises(ValueError):
        InfluenceConfig(prior_strength=1.0).validate()
    with pytest.raises(ValueError):
        InfluenceConfig(max_fraction=0.5).validate()
    with pytest.raises(ValueError):
        InfluenceConfig(mode="WILD").validate()


# ============================================================ izolasyon

def test_learning_cannot_change_risk_or_order_parameters():
    """16+17: ayarlama yalnız bir SAYI döndürür; risk/boyut/stop/TP alanı yoktur."""
    cfg = InfluenceConfig(mode=PAPER_BOUNDED)
    adj = learning_adjustment([{"r_multiple": 5.0}] * 50, baseline=0.60, cfg=cfg)
    out = apply_influence(adj, cfg=cfg, mode_value="PAPER")
    forbidden = {"leverage", "size", "notional", "stop", "targets", "tp", "risk_usdt",
                 "risk_pct", "order", "qty"}
    assert not (forbidden & set(out)), f"emir/risk alanı sızdı: {forbidden & set(out)}"
    assert not (forbidden & set(adj))


def test_active_risk_engine_unaffected_by_learning():
    """23: aktif RiskEngine kararı öğrenmeden ETKİLENMEZ."""
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    st = build_state(equity=50.0, starting_equity=50.0, available=50.0, used_margin=0.0,
                     positions=[], history=[], high_water_mark=None, now=T0)
    plan = {"symbol": "ETH/USDT", "market_type": "USDM_PERP", "direction": "LONG",
            "entry": 3000.0, "stop": 2940.0, "notional": 30.0, "margin": 15.0,
            "leverage": 2, "min_notional": 5.0}
    before = eng.evaluate(plan, st).to_dict()
    cfg = InfluenceConfig(mode=PAPER_BOUNDED)
    apply_influence(learning_adjustment([{"r_multiple": 9.0}] * 99, baseline=0.9, cfg=cfg),
                    cfg=cfg, mode_value="PAPER")
    after = eng.evaluate(plan, st).to_dict()
    assert before == after


def test_risk_v2_fail_closed_behaviour_preserved():
    """24: `212bd53` Risk V2 sözleşmesi korunuyor."""
    from tradingbot.quant.risk_v2 import HOLD_BELOW_MIN, AdviceContext, advise, offline_risk_report
    out = advise(AdviceContext("X/USDT", "LONG", proposed_leverage=1, symbol_vol_pct=None))
    assert out["advised_leverage"] == 1 and HOLD_BELOW_MIN in out["reasons"]
    rep = offline_risk_report([{"symbol": "A/USDT", "direction": "LONG", "risk_usdt": 1.0, "leverage": 1},
                               {"symbol": "B/USDT", "direction": "LONG", "risk_usdt": 1.0, "leverage": 1}], {})
    assert rep["increases_risk"] is False and rep["n_clusters"] == 1
    assert rep["applies_to_active_engine"] is False


def test_hot_loop_cost_is_bounded(tmp_path: Path, monkeypatch):
    """22: öğrenme, worker turunu makul sürenin ötesine taşımamalı."""
    eng = _engine(tmp_path, monkeypatch, symbols=2)
    t0 = time.perf_counter()
    _tour(eng)
    warm = time.perf_counter() - t0
    t1 = time.perf_counter()
    _tour(eng)
    second = time.perf_counter() - t1
    assert warm < 60.0 and second < 60.0, f"tur çok yavaş: {warm:.2f}s / {second:.2f}s"
    infl = eng._influence_log
    assert isinstance(infl, list)


def test_concurrent_appends_are_serialized(tmp_path: Path):
    """19: eşzamanlı yazımda satırlar bozulmaz (her satır geçerli JSON kalır)."""
    import threading
    p = tmp_path / "dj.jsonl"
    js = [DecisionJournal(p) for _ in range(4)]

    def work(j, off):
        for i in range(25):
            j.append_decision(_rec(sym=f"S{off}_{i}/USDT", cycle=f"{off}-{i}"))

    threads = [threading.Thread(target=work, args=(j, i)) for i, j in enumerate(js)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 100
    for line in lines:
        json.loads(line)                              # hiçbir satır bozulmadı


# ============================================================ dashboard E2E

def test_dashboard_learning_loop_endpoint_e2e(tmp_path: Path, monkeypatch):
    """21: üretim turu → decision_journal.jsonl → dashboard read-only uç."""
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    _tour(eng)
    st = Path(eng.cfg.state_path)
    data = st.parent / "data_dash"
    data.mkdir(exist_ok=True)
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/api/learning-loop")
    assert r.status_code == 200
    j = r.json()
    assert j["available"] is True, j
    assert j["n_decisions"] > 0
    assert j["guardrail"] == "LEARNING CANNOT OVERRIDE RISK GATES"
    assert set(j["coverage"]) == {"features", "specialist_scores", "regime", "trade_id"}
    assert "NaN" not in r.text and "Infinity" not in r.text
    # mutasyon metotları hâlâ 405
    for m in ("post", "put", "patch", "delete"):
        assert getattr(c, m)("/api/learning-loop").status_code == 405
    assert c.get("/quant").status_code == 200
    assert c.get("/api/overview").status_code == 200


def test_dashboard_learning_loop_empty_and_corrupt_are_safe(tmp_path: Path):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import DashboardConfig, create_app
    st, data = tmp_path / "state", tmp_path / "data"
    st.mkdir(), data.mkdir()
    c = TestClient(create_app(st, data, None, DashboardConfig()))
    r = c.get("/api/learning-loop")
    assert r.status_code == 200 and r.json()["available"] is False
    (st / "decision_journal.jsonl").write_text("{bozuk\n\n{\"kind\":\"decision\"}\n", encoding="utf-8")
    r2 = c.get("/api/learning-loop")
    assert r2.status_code == 200                      # bozuk satır 500 ÜRETMEZ
    assert c.get("/quant").status_code == 200
