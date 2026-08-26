"""Quant Evaluation V1 — champion–challenger testleri.

Kapsam: izolasyon kanıtı olmadan red, aynı maliyet modeli zorunluluğu, yetersiz örnek →
KEEP_CHAMPION, leakage/veri kalitesi başarısızlığı → REJECT_CHALLENGER, otomatik terfi yok,
bütün kapılar geçince yalnız öneri, duplicate üretmeyen shadow altyapısının izolasyonu
(ShadowBook ana ledger'a dokunmaz).
"""
from __future__ import annotations

from tradingbot.learn.shadow import ShadowBook
from tradingbot.quant.champion import (KEEP_CHAMPION, PROMOTE_CANDIDATE, REJECT_CHALLENGER,
                                       PromotionGates, evaluate_challenger)


def _metrics(n=200, exp=0.15, dd=-3.0, tail=-1.2, sym_share=0.2, trade_share=0.1,
             ci=(0.05, 0.3), insufficient=False, payoff=1.5, win_rate=0.5,
             calibration=None):
    return {"n": n, "insufficient_sample": insufficient, "expectancy_r": exp,
            "max_drawdown_r": dd, "tail_loss_r_cvar5": tail,
            "payoff_ratio": payoff, "win_rate": win_rate,
            "calibration": calibration if calibration is not None else {"brier": 0.2, "n": 200, "state": "ok"},
            "bootstrap_ci_mean_r": {"state": "ok", "low": ci[0], "high": ci[1]},
            "concentration": {"top_symbol_share": sym_share, "top_trade_share": trade_share}}


_SAFE = dict(leakage_passed=True, data_quality_passed=True, isolation_verified=True,
             same_cost_model=True, fold_consistency=0.8)


def test_default_is_keep_champion_without_evidence():
    out = evaluate_challenger(_metrics(exp=0.05), _metrics())
    # güvenlik kanıtı verilmedi (None) → sert kapılar GEÇMİŞ sayılmaz → asla PROMOTE olmaz;
    # açık başarısızlık olmadığı için de REJECT değil, varsayılan KEEP_CHAMPION döner.
    assert out["decision"] == KEEP_CHAMPION
    assert out["auto_promotion"] is False
    hard = {c["code"]: c["passed"] for c in out["checks"]}
    assert not hard["LEAKAGE"] and not hard["ISOLATION"]      # kanıtsız kapı geçilmedi


def test_leakage_or_data_quality_failure_rejects():
    for kw in ({"leakage_passed": False}, {"data_quality_passed": False},
               {"isolation_verified": False}, {"same_cost_model": False}):
        args = {**_SAFE, **kw}
        out = evaluate_challenger(_metrics(exp=0.05), _metrics(), **args)
        assert out["decision"] == REJECT_CHALLENGER, kw


def test_insufficient_sample_keeps_champion():
    out = evaluate_challenger(_metrics(exp=0.05), _metrics(n=30), **_SAFE)
    assert out["decision"] == KEEP_CHAMPION
    assert any(c["code"] == "MIN_SAMPLES" and not c["passed"] for c in out["checks"])
    out2 = evaluate_challenger(_metrics(exp=0.05), _metrics(insufficient=True), **_SAFE)
    assert out2["decision"] == KEEP_CHAMPION


def test_all_gates_pass_is_only_a_recommendation():
    out = evaluate_challenger(_metrics(exp=0.05, dd=-3.0, tail=-1.2), _metrics(exp=0.2), **_SAFE)
    assert out["decision"] == PROMOTE_CANDIDATE
    assert out["auto_promotion"] is False
    assert "öneri" in out["note"] and "TEST DATA" in out["label"]


def test_each_soft_gate_failure_keeps_champion():
    champ = _metrics(exp=0.05, dd=-3.0, tail=-1.2)
    cases = [
        _metrics(exp=-0.01),                                   # OOS expectancy negatif
        _metrics(exp=0.07),                                    # delta < 0.05
        _metrics(exp=0.2, ci=(-0.05, 0.4)),                    # CI alt sınırı ≤ 0
        _metrics(exp=0.2, dd=-10.0),                           # DD 1.2 katın üstünde
        _metrics(exp=0.2, tail=-5.0),                          # tail 1.5 katın üstünde
        _metrics(exp=0.2, sym_share=0.9),                      # tek sembol yoğunlaşması
        _metrics(exp=0.2, trade_share=0.6),                    # tek trade yoğunlaşması
    ]
    for ch in cases:
        out = evaluate_challenger(champ, ch, **_SAFE)
        assert out["decision"] == KEEP_CHAMPION, ch
    low_consistency = {**_SAFE, "fold_consistency": 0.3}
    assert evaluate_challenger(champ, _metrics(exp=0.2), **low_consistency)["decision"] == KEEP_CHAMPION


def test_pbo_unavailable_is_warning_not_fake_number():
    out = evaluate_challenger(_metrics(exp=0.05), _metrics(exp=0.2), **_SAFE,
                              pbo_state="not_computable")
    warn = next(c for c in out["checks"] if c["code"] == "PBO_WARNING")
    assert "hesaplanamadı" in warn["detail"] or "not_computable" in warn["detail"]
    assert out["decision"] == PROMOTE_CANDIDATE               # uyarı, sahte blokaj değil


def test_gates_are_configurable_but_default_conservative():
    g = PromotionGates()
    assert g.min_samples >= 100 and g.min_delta_expectancy_r > 0
    loose = PromotionGates(min_samples=10, min_delta_expectancy_r=0.0)
    out = evaluate_challenger(_metrics(exp=0.05), _metrics(n=20, exp=0.06), gates=loose, **_SAFE)
    assert out["decision"] in (PROMOTE_CANDIDATE, KEEP_CHAMPION)


def test_shadow_infrastructure_isolated_and_duplicate_free(tmp_path):
    """Challenger'ın kullandığı shadow altyapısı: ayrı dosya, ana ledger'a sıfır dokunuş, dedup."""
    book_path = tmp_path / "challenger_shadow.json"
    book = ShadowBook(book_path)
    plan = {"plan_id": "cand1", "symbol": "ETH/USDT", "direction": "LONG", "entry": 100.0,
            "stop": 95.0, "targets": [110.0, 120.0], "market_type": "futures", "leverage": 2}
    first = book.add(plan, ["CHALLENGER_SHADOW"])
    dup = book.add(plan, ["CHALLENGER_SHADOW"])               # aynı olay ikinci kez
    assert len(first) == 1 and dup == []                      # duplicate ÜRETİLMEDİ
    assert book_path.exists()
    others = sorted(p.name for p in tmp_path.iterdir())
    assert others == ["challenger_shadow.json"]               # ledger/outbox dosyası YOK
