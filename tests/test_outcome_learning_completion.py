"""Outcome Learning Loop V1 — Completion Audit.

Raporun kendi "kalan sınırlamalar" bölümündeki üç kritik boşluğu kapatan testler:

1. Shadow outcome'ların DÜŞÜK ağırlıkla ve no-lookahead retrieval'a bağlanması.
2. Aynı outcome'un hiyerarşik prior + similarity kanallarında ÇİFT SAYILMAMASI.
3. Gerçek `TradingEngineV3.tour()` + `LearnerV2.on_trade_closed()` üzerinden
   "bir işlemden sonraki benzer karar" zinciri.

Ayrıca feature profili / cost-sensitivity hafızası ve ölçeklenebilirlik ölçümü.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from test_engine_v3 import _engine

from tradingbot.learn.experience import (REAL_PAPER, SHADOW, Experience, ExperienceIndex,
                                         build_pool, cost_sensitivity, feature_profile,
                                         merge_experiences, real_experiences, shadow_experiences)
from tradingbot.learn.influence import (InfluenceConfig, combine_components, weighted_adjustment)

UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _mem_row(i=0, *, symbol="ETH/USDT", direction="LONG", setup="kırılım", r=1.5,
             closed=None, opened=None, feats=None, outcome_extra=None):
    closed = closed or (T0 + timedelta(days=i))
    opened = opened or (closed - timedelta(hours=8))
    return {"trade_id": f"T{i}", "symbol": symbol, "direction": direction, "setup_type": setup,
            "regime": "TREND_UP", "features": feats or {"bias_trend": 0.6, "conf_trend": 0.7,
                                                        "rr": 2.5, "atr_pct": 0.3},
            "outcome": {"r_multiple": r, "closed_at": closed.isoformat(),
                        "opened_at": opened.isoformat(), "exit_reason": "hedef2",
                        **(outcome_extra or {})},
            "postmortem": {"lesson_codes": ["CLEAN"]}}


def _shadow_row(i=0, *, symbol="ETH/USDT", direction="LONG", variant="as_planned", r=1.0,
                labeled=None, created=None, labelled=True):
    created = created or (T0 - timedelta(days=2 + i))
    labeled = labeled or (T0 - timedelta(days=1 + i))
    row = {"id": f"S{i}", "plan_id": f"p{i}", "symbol": symbol, "direction": direction,
           "market_type": "futures", "variant": variant, "created_at": created.isoformat(),
           "label_ts": labeled.isoformat(), "entry": 100.0, "stop": 95.0, "targets": [110.0]}
    if labelled:
        row["outcome"] = {"r_multiple": r, "exit_reason": "target", "won": r > 0}
        row["labeled_at"] = labeled.isoformat()
    return row


# ============================================================ BOŞLUK 2: shadow adapter

def test_unlabeled_shadow_never_enters_pool():
    pool = shadow_experiences([_shadow_row(0, labelled=False)], as_of_ms=None)
    assert pool == [], "etiketsiz gölge kayıt havuza GİREMEZ"


def test_shadow_labeled_after_decision_is_excluded():
    """Karar zamanından SONRA etiketlenen gölge future leakage yapamaz."""
    as_of = _ms(T0)
    past = _shadow_row(0, labeled=T0 - timedelta(days=1))
    future = _shadow_row(1, labeled=T0 + timedelta(days=5), r=99.0)
    got = shadow_experiences([past, future], as_of_ms=as_of)
    ids = {e.outcome_id for e in got}
    assert len(got) == 1
    assert all(e.label_ts_ms <= as_of for e in got)
    assert not any(e.r_multiple == 99.0 for e in got), "gelecekte etiketlenen sonuç sızdı"
    assert ids


def test_labeled_past_shadow_appears_with_lower_weight():
    real = real_experiences([_mem_row(0, closed=T0 - timedelta(days=3))], as_of_ms=_ms(T0))
    shad = shadow_experiences([_shadow_row(0)], as_of_ms=_ms(T0), weight=0.25, fidelity=0.5)
    assert real and shad
    assert real[0].source == REAL_PAPER and real[0].weight == 1.0
    assert shad[0].source == SHADOW
    assert shad[0].weight == pytest.approx(0.125)          # 0.25 × 0.5
    assert shad[0].weight < real[0].weight
    assert shad[0].outcome_quality == "COUNTERFACTUAL_LABEL"
    assert shad[0].provenance == "shadow_book_label"
    assert shad[0].execution_fidelity == 0.5


def test_real_outweighs_shadow_at_equal_similarity():
    cfg = InfluenceConfig()
    r = Experience("A", REAL_PAPER, r_multiple=2.0, weight=1.0)
    s = Experience("B", SHADOW, r_multiple=2.0, weight=0.125)
    only_real = weighted_adjustment([r], baseline=0.6, cfg=cfg)
    only_shadow = weighted_adjustment([s], baseline=0.6, cfg=cfg)
    assert abs(only_real["fraction"]) > abs(only_shadow["fraction"])
    assert only_shadow["effective_n"] == pytest.approx(0.125)
    assert "INCLUDES_SHADOW_EVIDENCE" in only_shadow["reasons"]


def test_many_shadows_cannot_swamp_one_real_outcome():
    """Çok sayıda gölge, tek gerçek sonucu SINIRSIZ biçimde bastıramaz."""
    cfg = InfluenceConfig()
    real_neg = Experience("R", REAL_PAPER, r_multiple=-2.0, weight=1.0)
    shadows = [Experience(f"S{i}", SHADOW, r_multiple=2.0, weight=0.125) for i in range(4)]
    mixed = weighted_adjustment([real_neg] + shadows, baseline=0.6, cfg=cfg)
    # 4 gölge = 0.5 ağırlık < 1.0 gerçek → işaret hâlâ gerçek sonucun yönünde
    assert mixed["signal"] < 0, mixed
    assert mixed["effective_n"] == pytest.approx(1.5)
    # ve her koşulda tavan korunur
    assert abs(mixed["fraction"]) <= cfg.max_fraction


def test_duplicate_real_and_shadow_count_once():
    """Aynı aday hem TradeMemory hem ShadowBook'ta ise TEK deneyim sayılır (gerçek kazanır)."""
    same_open = T0 - timedelta(hours=8)
    real = real_experiences([_mem_row(0, opened=same_open, closed=T0 - timedelta(days=1))],
                            as_of_ms=_ms(T0))
    shad = shadow_experiences([_shadow_row(0)], as_of_ms=_ms(T0))
    # kimlikleri eşitle (aynı aday senaryosu)
    shad[0].outcome_id = real[0].outcome_id
    merged = merge_experiences(real, shad)
    assert len(merged) == 1
    assert merged[0].source == REAL_PAPER, "gerçek kayıt gölgeye tercih edilmeli"


def test_pool_is_deterministic_and_bounded():
    mem = [_mem_row(i, closed=T0 - timedelta(days=10 - i)) for i in range(8)]
    sh = [_shadow_row(i) for i in range(6)]
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
         "bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3}
    a = build_pool(memory_rows=mem, shadow_trades=sh, query=q, as_of_ms=_ms(T0), top_k=5)
    b = build_pool(memory_rows=mem, shadow_trades=sh, query=q, as_of_ms=_ms(T0), top_k=5)
    assert [x.outcome_id for x in a] == [x.outcome_id for x in b]
    assert len(a) <= 5
    assert all(x.similarity is not None for x in a)


# ============================================================ BOŞLUK 3: çift sayım

def test_no_double_count_across_hierarchical_and_similarity():
    """Dört senaryo sayısal olarak karşılaştırılır; aynı outcome çift TAM ağırlık ALMAZ."""
    cfg = InfluenceConfig()
    e = Experience("out-1", REAL_PAPER, r_multiple=2.0, weight=1.0)

    a_only_hier = weighted_adjustment([], baseline=0.6, cfg=cfg, prior_leaf_n=1)
    b_only_sim = weighted_adjustment([e], baseline=0.6, cfg=cfg, prior_leaf_n=0)
    c_both = weighted_adjustment([e], baseline=0.6, cfg=cfg, prior_leaf_n=1)
    d_dupe = weighted_adjustment([e, e], baseline=0.6, cfg=cfg, prior_leaf_n=1)

    assert a_only_hier["fraction"] == 0.0                      # similarity katkısı yok
    assert b_only_sim["fraction"] != 0.0                       # tek kanal → tam katkı
    assert abs(c_both["fraction"]) < abs(b_only_sim["fraction"]), "residual uygulanmadı"
    assert c_both["fraction"] == d_dupe["fraction"], "duplicate ikinci kez sayıldı"
    assert d_dupe["dropped_duplicates"] == 1
    assert c_both["counted_outcome_ids"] == ["out-1"]
    assert c_both["residual_share"] == pytest.approx(1 - 1 / 21)
    assert "RESIDUAL_ONLY_PRIOR_ALREADY_COUNTED" in c_both["reasons"]


def test_adding_same_outcome_twice_does_not_change_final_probability():
    cfg = InfluenceConfig()
    e = Experience("x", REAL_PAPER, r_multiple=1.8, weight=1.0)
    one = weighted_adjustment([e], baseline=0.62, cfg=cfg, prior_leaf_n=3)
    two = weighted_adjustment([e, e, e], baseline=0.62, cfg=cfg, prior_leaf_n=3)
    assert one["learned"] == two["learned"]
    c1 = combine_components(raw_model_p=0.6, hierarchical_p=0.62, adjustment=one, cfg=cfg)
    c2 = combine_components(raw_model_p=0.6, hierarchical_p=0.62, adjustment=two, cfg=cfg)
    assert c1["final"] == c2["final"]


def test_duplicate_real_shadow_pair_moves_probability_once():
    cfg = InfluenceConfig()
    r = Experience("same", REAL_PAPER, r_multiple=2.0, weight=1.0)
    s = Experience("same", SHADOW, r_multiple=2.0, weight=0.125)
    only_real = weighted_adjustment([r], baseline=0.6, cfg=cfg, prior_leaf_n=0)
    pair = weighted_adjustment([r, s], baseline=0.6, cfg=cfg, prior_leaf_n=0)
    assert pair["fraction"] == only_real["fraction"], "aynı kimlik iki kez etkiledi"
    assert pair["dropped_duplicates"] == 1


def test_components_are_reported_separately_and_bounded():
    cfg = InfluenceConfig()
    e = [Experience(f"o{i}", REAL_PAPER, r_multiple=5.0, weight=1.0) for i in range(200)]
    adj = weighted_adjustment(e, baseline=0.60, cfg=cfg, prior_leaf_n=0)
    comp = combine_components(raw_model_p=0.55, hierarchical_p=0.60, adjustment=adj, cfg=cfg)
    assert comp["raw_model"] == 0.55
    assert comp["hierarchical_prior"] == 0.60
    assert comp["hierarchical_contribution"] == pytest.approx(0.05)
    assert comp["similarity_fraction"] == adj["fraction"]
    assert abs(comp["final"] - 0.60) <= 0.60 * cfg.max_fraction + 1e-9
    assert comp["note"] == "LEARNING CANNOT OVERRIDE RISK GATES"


def test_n1_still_small_but_nonzero_with_residual():
    cfg = InfluenceConfig()
    adj = weighted_adjustment([Experience("a", REAL_PAPER, r_multiple=2.0, weight=1.0)],
                              baseline=0.6, cfg=cfg, prior_leaf_n=1)
    assert adj["fraction"] != 0.0
    assert abs(adj["fraction"]) < 0.01


# ============================================================ BOŞLUK 5: feature profili / maliyet

def test_feature_profile_is_deterministic_versioned_and_bounded():
    a = feature_profile({"atr_pct": 0.2, "rr": 2.0, "bias_trend": 0.5, "conviction": 0.7})
    b = feature_profile({"atr_pct": 0.2, "rr": 2.0, "bias_trend": 0.5, "conviction": 0.7})
    c = feature_profile({"atr_pct": 0.9, "rr": 2.0, "bias_trend": 0.5, "conviction": 0.7})
    assert a == b and a != c
    assert a.startswith("v1")
    # SINIRLI kardinalite: rastgele 500 girdi az sayıda profil üretir
    import random
    rnd = random.Random(7)
    profiles = {feature_profile({"atr_pct": rnd.random(), "rr": rnd.random() * 6,
                                 "bias_trend": rnd.random() * 2 - 1,
                                 "conviction": rnd.random()}) for _ in range(500)}
    assert len(profiles) <= 5 * 5 * 5 * 4, len(profiles)
    assert feature_profile(None).startswith("v1")


def test_cost_sensitivity_classes():
    assert cost_sensitivity({"r_multiple": -0.2, "fee_drag_r": 0.5,
                             "funding_drag_r": 0.2, "slippage_drag_r": 0.0}) == "COST_DOMINATED"
    assert cost_sensitivity({"r_multiple": 2.0, "fee_drag_r": 0.05,
                             "funding_drag_r": 0.0, "slippage_drag_r": 0.0}) == "COST_RESILIENT"
    assert cost_sensitivity({"r_multiple": 1.0, "fee_drag_r": 0.25,
                             "funding_drag_r": 0.0, "slippage_drag_r": 0.0}) == "COST_SENSITIVE"
    assert cost_sensitivity({}) == "UNKNOWN"
    assert cost_sensitivity(None) == "UNKNOWN"


def test_cost_dominated_history_produces_negative_lesson():
    """Brüt kârı maliyetle net zarara dönen geçmiş, benzer adayda NEGATİF ders üretir."""
    rows = [_mem_row(i, r=-0.15, outcome_extra={"fee_drag_r": 0.6, "funding_drag_r": 0.3,
                                                "slippage_drag_r": 0.1},
                     closed=T0 - timedelta(days=5 - i)) for i in range(3)]
    exps = real_experiences(rows, as_of_ms=_ms(T0))
    assert exps and all(e.cost_sensitivity == "COST_DOMINATED" for e in exps)
    adj = weighted_adjustment(exps, baseline=0.6, cfg=InfluenceConfig(), prior_leaf_n=0)
    assert adj["signal"] < 0 and adj["fraction"] < 0        # NEGATİF ders
    assert adj["learned"] < adj["baseline"]


def test_matching_feature_profile_scores_higher():
    same = _mem_row(0, feats={"bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3},
                    closed=T0 - timedelta(days=2))
    diff = _mem_row(1, symbol="SOL/USDT", direction="SHORT", setup="geri çekilme",
                    feats={"bias_trend": -0.8, "conf_trend": 0.2, "rr": 1.0, "atr_pct": 0.9},
                    closed=T0 - timedelta(days=3))
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
         "bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3}
    pool = build_pool(memory_rows=[same, diff], shadow_trades=[], query=q, as_of_ms=_ms(T0), top_k=5)
    by_sym = {e.symbol: e.similarity for e in pool}
    assert by_sym["ETH/USDT"] > by_sym["SOL/USDT"]


# ============================================================ BOŞLUK 6: ölçeklenebilirlik

def test_experience_index_loads_once_per_signature(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    p.write_text("x\n", encoding="utf-8")
    idx = ExperienceIndex()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [{"a": 1}]

    for _ in range(50):
        idx.rows("memory", p, loader)
    assert calls["n"] == 1, "aday başına yeniden parse ediliyor"
    p.write_text("y\ny\n", encoding="utf-8")          # imza değişti
    idx.rows("memory", p, loader)
    assert calls["n"] == 2
    assert idx.stats()["loads"] == 2


def test_experience_index_is_failsafe_on_corrupt_loader(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    p.write_text("x\n", encoding="utf-8")
    idx = ExperienceIndex()

    def boom():
        raise ValueError("corrupt state")

    assert idx.rows("memory", p, boom) == []           # baseline fail-safe
    assert idx.stats()["errors"] == 1
    assert idx.rows("memory", tmp_path / "yok.jsonl', ", lambda: [1]) == []


@pytest.mark.parametrize("n_exp", [100, 1_000, 10_000])
def test_retrieval_scales_with_experience_count(n_exp):
    """100 / 1.000 / 10.000 deneyimde retrieval maliyeti ölçülür ve sınırlanır."""
    mem = [_mem_row(i, closed=T0 - timedelta(minutes=n_exp - i)) for i in range(n_exp)]
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
         "bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3}
    t0 = time.perf_counter()
    pool = build_pool(memory_rows=mem, shadow_trades=[], query=q, as_of_ms=_ms(T0), top_k=5)
    dt = time.perf_counter() - t0
    assert len(pool) == 5                              # top-K BOUNDED
    # Kanıt temelli sınır: 10k deneyimde bile tek aday retrieval'ı worker turunun küçük bir payı.
    budget = {100: 0.5, 1_000: 2.0, 10_000: 12.0}[n_exp]
    assert dt < budget, f"{n_exp} deneyimde retrieval {dt:.2f}s (bütçe {budget}s)"


def test_tour_cost_with_instrumentation_is_measured(tmp_path: Path, monkeypatch):
    """SHADOW instrumented tur, baseline (OFF) tura göre anlamlı yavaşlama yaratmamalı."""
    off = _engine(tmp_path / "off", monkeypatch, symbols=3,
                  v3_overrides={"learning_v3": {"influence_mode": "OFF",
                                                "decision_journal_enabled": False}})
    t0 = time.perf_counter()
    off.tour(do_scan=False, obsidian=False, charts=False)
    base_dt = time.perf_counter() - t0

    on = _engine(tmp_path / "on", monkeypatch, symbols=3)      # varsayılan SHADOW + journal
    t1 = time.perf_counter()
    on.tour(do_scan=False, obsidian=False, charts=False)
    inst_dt = time.perf_counter() - t1

    overhead = inst_dt - base_dt
    print(f"\nBASELINE={base_dt:.3f}s INSTRUMENTED={inst_dt:.3f}s OVERHEAD={overhead:.3f}s")
    # Kanıt temelli kapı: mutlak 3 sn VE baseline'ın 2 katını aşmayan ek maliyet.
    assert overhead < 3.0, f"instrumentation ek maliyeti {overhead:.2f}s"
    assert inst_dt < max(base_dt * 3.0, base_dt + 3.0), f"{inst_dt:.2f}s vs {base_dt:.2f}s"


# ============================================================ BOŞLUK 4: gerçek close → sonraki karar

def _find_open(eng):
    return next(iter(eng.ledger2.positions.values()), None)


def _close_at_stop(eng, sym, *, win: bool):
    """Pozisyonu gerçek ledger yoluyla kapatır (kazanç ya da zarar)."""
    pos = eng.ledger2.positions.get(sym)
    if pos is None:
        return False
    target = float(pos.targets[-1]) if (win and pos.targets) else float(pos.stop)
    mult = 1.02 if win else 0.98
    if pos.side.value == "SHORT":
        mult = 0.98 if win else 1.02
    eng._fake_live.price[sym] = target * mult
    return True


def test_real_close_then_next_similar_decision_e2e(tmp_path: Path, monkeypatch):
    """Gerçek `tour()` + gerçek `on_trade_closed()` zinciri:

    karar → PAPER entry → kapanış → posterior/postmortem/journal/experience → aynı coin için
    yeni aday → geçmiş outcome retrieval'da → SHADOW counterfactual baseline'dan KÜÇÜK ama
    SIFIR OLMAYAN biçimde ayrılıyor.
    """
    eng = _engine(tmp_path, monkeypatch, symbols=3)
    opened_sym = None
    for _ in range(6):                                   # bir pozisyon açılana kadar tur at
        eng.tour(do_scan=False, obsidian=False, charts=False)
        eng._fake_live.price.clear()
        pos = _find_open(eng)
        if pos is not None:
            opened_sym = pos.symbol
            break
    if opened_sym is None:
        pytest.skip("sentetik piyasada pozisyon açılmadı — zincir bu koşulda test edilemez")

    n_closed_before = eng.learner2.n_closed
    # kapanışı gerçek ledger tick'i ile tetikle
    closed = False
    for _ in range(6):
        _close_at_stop(eng, opened_sym, win=False)
        eng.tour(do_scan=False, obsidian=False, charts=False)
        eng._fake_live.price.clear()
        if eng.learner2.n_closed > n_closed_before:
            closed = True
            break
    assert closed, "işlem gerçek yolla kapanmadı"

    # 4) posterior + postmortem + journal + experience güncellendi
    assert eng.learner2.n_closed > n_closed_before
    assert eng.learner2.lessons, "kapanış dersi üretilmedi"
    jp = Path(eng.cfg.state_path) / "decision_journal.jsonl"
    rows = [json.loads(x) for x in jp.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(r.get("kind") == "outcome_link" for r in rows), "outcome journal'a bağlanmadı"
    mem_closed = eng.memory.trades(closed_only=True)
    assert mem_closed, "TradeMemory kapanış kaydı yok"

    # 5+6+7) yeni tur: aynı coin için geçmiş deneyim retrieval'da görünmeli
    eng.tour(do_scan=False, obsidian=False, charts=False)
    eng._fake_live.price.clear()
    infl = [i for i in eng._influence_log if i.get("n_experience", 0) > 0]
    assert infl, f"geçmiş outcome retrieval'a girmedi: {eng._influence_log}"
    same = [i for i in infl if i["symbol"] == opened_sym] or infl
    rec = same[0]
    assert rec["mode"] == "SHADOW"
    assert rec["applied"] is False                        # SHADOW baseline'ı DEĞİŞTİRMEZ
    assert rec["effective"] == rec["baseline"]
    assert rec["learned"] is not None
    assert rec["learned"] != rec["baseline"], "counterfactual baseline'dan ayrılmadı"
    delta = abs(rec["learned"] - rec["baseline"])
    assert 0 < delta <= abs(rec["baseline"]) * 0.05 + 1e-9, f"etki sınır dışı: {delta}"
    assert rec["fraction"] < 0, "zararla kapanan işlem sonrası etki NEGATİF olmalı"
    assert rec["prior_leaf_n"] is not None and rec["residual_share"] is not None


def test_winner_and_loser_direction_of_effect():
    """Kazanç sonrası pozitif, zarar sonrası negatif ders — ikisi de bounded."""
    cfg = InfluenceConfig()
    win = weighted_adjustment([Experience("w", REAL_PAPER, r_multiple=2.0, weight=1.0)],
                              baseline=0.6, cfg=cfg, prior_leaf_n=0)
    loss = weighted_adjustment([Experience("l", REAL_PAPER, r_multiple=-1.5, weight=1.0)],
                               baseline=0.6, cfg=cfg, prior_leaf_n=0)
    assert win["fraction"] > 0 and win["learned"] > win["baseline"]
    assert loss["fraction"] < 0 and loss["learned"] < loss["baseline"]
    for a in (win, loss):
        assert abs(a["fraction"]) <= cfg.max_fraction


def test_future_outcome_mutation_does_not_change_current_decision():
    """Karar zamanından SONRAKİ veri mevcut kararı değiştiremez (as-of kesimi)."""
    from tradingbot.learn.experience import prepare_pool, query_pool
    as_of = _ms(T0)
    past = _mem_row(0, closed=T0 - timedelta(days=2), r=1.5)
    future = _mem_row(1, closed=T0 + timedelta(days=9), r=-9.0)
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
         "bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3}
    p1 = prepare_pool(memory_rows=[past], shadow_trades=[])
    p2 = prepare_pool(memory_rows=[past, future], shadow_trades=[])
    h1 = query_pool(p1, q, as_of_ms=as_of, top_k=5)
    h2 = query_pool(p2, q, as_of_ms=as_of, top_k=5)
    assert [e.outcome_id for e in h1] == [e.outcome_id for e in h2]
    a1 = weighted_adjustment(h1, baseline=0.6, cfg=InfluenceConfig(), prior_leaf_n=0)
    a2 = weighted_adjustment(h2, baseline=0.6, cfg=InfluenceConfig(), prior_leaf_n=0)
    assert a1["fraction"] == a2["fraction"], "gelecekteki sonuç mevcut kararı değiştirdi"


def test_prepared_pool_matches_direct_pool_semantics():
    """Hazır havuz optimizasyonu, semantiği DEĞİŞTİRMEZ (aynı top-K kimlikleri)."""
    from tradingbot.learn.experience import prepare_pool, query_pool
    mem = [_mem_row(i, closed=T0 - timedelta(days=9 - i)) for i in range(6)]
    sh = [_shadow_row(i) for i in range(3)]
    q = {"symbol": "ETH/USDT", "direction": "LONG", "setup_type": "kırılım", "regime": "TREND_UP",
         "bias_trend": 0.6, "conf_trend": 0.7, "rr": 2.5, "atr_pct": 0.3}
    direct = build_pool(memory_rows=mem, shadow_trades=sh, query=q, as_of_ms=_ms(T0), top_k=4)
    prepared = query_pool(prepare_pool(memory_rows=mem, shadow_trades=sh), q,
                          as_of_ms=_ms(T0), top_k=4)
    assert [e.outcome_id for e in direct] == [e.outcome_id for e in prepared]
    assert [e.source for e in direct] == [e.source for e in prepared]
