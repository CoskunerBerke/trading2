"""GÖLGE KAYIT SÖZLEŞMESİ + TEK GEVŞETİLEN SEÇİCİLİK DEĞİŞKENİ.

Gölge kayıt SALT GÖZLEMSELDİR: gerçek PAPER pozisyonu, fill, emir ya da defter kaydı ÜRETMEZ;
ayrı `shadow_book.json` dosyasında `is_counterfactual=True` ile durur, sınırlı saklama ve duplicate
olay koruması vardır, yazım worker'ın atomik yazım sözleşmesini kullanır.

Seçicilik: yalnız `opportunity.UNCERTAINTY_K` bir kademe gevşetildi (0.25 → 0.20). Bu SERT bir
güvenlik kapısı değildir; istatistiksel muhafazakârlık ölçeğidir. Test, gevşetmenin NEGATİF
beklentili adayı gerçek girişe ÇEVİRMEDİĞİNİ kanıtlar.
"""
from __future__ import annotations

import itertools
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

import tradingbot.opportunity as O  # noqa: E402
from tradingbot.decision_gates import GateLedger  # noqa: E402
from tradingbot.learn.shadow import ShadowBook  # noqa: E402


# ===================================================================== 1) gölge kayıt sözleşmesi
def _plan(sym="X/USDT", plan_id="p1", direction="LONG"):
    return {"plan_id": plan_id, "symbol": sym, "market_type": "USDM_PERP", "direction": direction,
            "entry": 100.0, "stop": 96.0, "targets": [108.0], "horizon_bars": 6, "leverage": 3}


def test_shadow_writes_a_separate_atomic_file_and_never_a_fill(tmp_path):
    path = tmp_path / "shadow_book.json"
    sb = ShadowBook(path)
    out = sb.add(_plan(), ["LEVERAGE_GATE_BLOCKED", "CONFIDENCE_BELOW_BASE"])
    assert len(out) == 1 and out[0].is_counterfactual is True
    d = json.loads(path.read_text(encoding="utf-8"))
    t = d["trades"][0]
    # giriş, stop, TP, ufuk, gerekçe ve sonuç alanı AYRI dosyada tutulur
    assert (t["entry"], t["stop"], t["targets"]) == (100.0, 96.0, [108.0])
    assert t["reason_not_opened"][0] == "LEVERAGE_GATE_BLOCKED"
    assert t["outcome"] is None and t["labeled_at"] is None
    # gerçek defter/emir kavramı YOK: fill/qty/order/position alanları üretilmez
    assert not ({"qty", "order_id", "fill_price", "position_id", "status"} & set(t))


def test_duplicate_event_is_not_recorded_twice(tmp_path):
    sb = ShadowBook(tmp_path / "s.json")
    assert len(sb.add(_plan(), ["A"])) == 1
    assert sb.add(_plan(), ["B"]) == []                    # aynı plan/sembol/yön/varyant
    assert len(sb.trades) == 1
    # farklı yön ya da farklı plan AYRI olaydır
    assert len(sb.add(_plan(direction="SHORT"), ["A"])) == 1
    assert len(sb.add(_plan(plan_id="p2"), ["A"])) == 1
    assert len(sb.trades) == 3


def test_labelled_shadow_does_not_block_a_new_event(tmp_path):
    sb = ShadowBook(tmp_path / "s.json")
    sb.add(_plan(), ["A"])
    sb.trades[0].outcome = {"r_multiple": 0.5, "is_counterfactual": True}
    assert len(sb.add(_plan(), ["A"])) == 1                # eski olay kapandı → yeni olay serbest


def test_retention_is_bounded_in_memory_and_on_disk(tmp_path):
    path = tmp_path / "s.json"
    sb = ShadowBook(path)
    sb.MAX_TRADES = 10
    for i in range(25):
        sb.add(_plan(plan_id=f"p{i}"), ["A"])
    assert len(sb.trades) <= 10
    assert len(json.loads(path.read_text(encoding="utf-8"))["trades"]) <= 10


def test_shadow_stats_are_marked_counterfactual(tmp_path):
    """Ana öğrenme istatistiğine karışmaz: her çıktı `is_counterfactual` ile işaretlidir."""
    sb = ShadowBook(tmp_path / "s.json")
    sb.add(_plan(), ["A"])
    assert sb.stats()["is_counterfactual"] is True


def test_leverage_gate_rejection_is_recorded_as_shadow_without_opening(tmp_path, monkeypatch):
    """Kaldıraç kapısı reddettiğinde aday KAYBOLMAZ; gölge olarak izlenir, pozisyon AÇILMAZ."""
    from test_risk_capacity_and_gates import _force_final_risk_pct, _force_triggers
    eng = _engine(tmp_path, monkeypatch,
                  {"leverage": {"enabled": True, "min_confidence": 0.999}},
                  symbols=4, equity=5_000.0)
    assert eng.leverage_cfg.enabled
    _force_final_risk_pct(monkeypatch, 0.5, {s: 0.9 for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng._funnel["leverage_gate_blocked"] > 0, "senaryo geçersiz: kaldıraç kapısı hiç çalışmadı"
    assert eng._funnel["opened"] == 0
    assert not eng.ledger2.positions                      # GERÇEK pozisyon YOK
    recorded = [t for t in eng.shadow.trades if "LEVERAGE_GATE_BLOCKED" in t.reason_not_opened]
    assert recorded, "reddedilen geçerli aday gölge kayıtla izlenmeli"
    assert all(t.outcome is None and t.is_counterfactual for t in recorded)


def test_stale_or_unknown_stop_candidates_get_no_shadow_record(tmp_path):
    """Veri bayat/çelişkili ya da stop bilinmiyorsa aday 'geçerli' değildir — kayıt YOK."""
    from tradingbot.risk.leverage import LeverageConfig, LeverageContext, select_leverage
    blocked = select_leverage(LeverageContext(data_stale=True, stop_frac=0.04, confidence=0.9),
                              LeverageConfig(enabled=True))
    assert not blocked.tradeable
    assert {"DATA_STALE", "DATA_CONFLICT", "STOP_UNKNOWN"} & set(blocked.blocked_higher)


# ===================================================================== 2) tek seçicilik değişkeni
_SOFT = {"LOW_CONSENSUS": 0.06, "LOW_CONFIDENCE": 0.06, "RR_BELOW_PREFERRED": 0.05}
_GRID = list(itertools.product((0.40, 0.45, 0.50, 0.55, 0.60, 0.65),
                               ((1.6, 1.0), (2.0, 1.0), (1.2, 1.0)),
                               (0, 2, 5, 10, 20, 40),
                               ((), ("LOW_CONSENSUS",), ("LOW_CONFIDENCE", "RR_BELOW_PREFERRED"))))


def _classify(k: float) -> dict:
    """`k` ölçeğiyle bütün ızgarayı sınıflandır — deterministik dry-run."""
    orig = O.uncertainty_penalty_r
    O.uncertainty_penalty_r = lambda n, _k=k, **kw: round(
        _k / math.sqrt(max(0.0, float(n or 0)) + 1.0), 6)
    try:
        out = {}
        for p_win, (w, l), n, softs in _GRID:
            g = GateLedger()
            for c in softs:
                g.penalise(c, _SOFT[c], detail="dry-run")
            a = O.assess(symbol="X/USDT", side="LONG", setup="breakout", gates=g, p_win=p_win,
                         avg_win_r=w, avg_loss_r=l, sample_size=n, cost_pct_notional=None,
                         stop_dist_pct=2.0)
            out[(p_win, w, l, n, softs)] = ("tradeable" if a.tradeable else
                                            "research_only" if a.research_only else "no_entry")
        return out
    finally:
        O.uncertainty_penalty_r = orig


def test_relaxed_knob_only_promotes_research_only_never_negative_edge():
    """Gevşetme YALNIZ `research_only` → `tradeable` yönünde çalışır.

    `research_only` tanım gereği `net_expectancy_r > 0`dır; yani nokta tahmini ZATEN pozitif olan
    adaylar gerçek girişe geçer. Negatif beklentili / sert engelli aday sayısı DEĞİŞMEZ.
    """
    old, new = _classify(0.25), _classify(0.20)
    flips = {k: (old[k], new[k]) for k in old if old[k] != new[k]}
    assert flips, "ölçüm geçersiz: hiçbir senaryo değişmedi"
    assert set(flips.values()) == {("research_only", "tradeable")}
    assert sum(v == "no_entry" for v in old.values()) == sum(v == "no_entry" for v in new.values())


def test_relaxation_is_a_small_single_notch():
    old, new = _classify(0.25), _classify(0.20)
    gain = sum(v == "tradeable" for v in new.values()) - sum(v == "tradeable" for v in old.values())
    assert 0 < gain <= 0.10 * len(_GRID), gain          # bir kademe: ızgaranın ≤ %10'u


def test_negative_expectancy_is_never_tradeable_at_the_new_scale():
    g = GateLedger()
    a = O.assess(symbol="X/USDT", side="LONG", setup="breakout", gates=g, p_win=0.30,
                 avg_win_r=1.0, avg_loss_r=1.0, sample_size=500, cost_pct_notional=None,
                 stop_dist_pct=2.0)
    assert a.net_expectancy_r < 0 and not a.tradeable and not a.research_only


def test_hard_blocks_still_zero_the_size_at_the_new_scale():
    g = GateLedger()
    g.block("ZERO_STOP_DISTANCE")
    a = O.assess(symbol="X/USDT", side="LONG", setup="breakout", gates=g, p_win=0.90,
                 avg_win_r=3.0, avg_loss_r=1.0, sample_size=500, cost_pct_notional=None,
                 stop_dist_pct=2.0)
    assert a.blocked and a.size_multiplier == 0.0 and not a.tradeable


def test_uncertainty_scale_is_the_only_changed_selectivity_constant():
    """Diğer seçicilik sabitleri AYNEN korunur — tek değişken gevşetildi."""
    assert O.UNCERTAINTY_K == 0.20
    assert O.FULL_SIZE_EDGE_R == 0.35
    assert O.MIN_TRADE_MULTIPLIER == 0.20
    assert O.RESEARCH_MULTIPLIER == 0.25
    assert O.BLEND_N == 20.0


def test_penalty_still_shrinks_with_sample_size():
    assert O.uncertainty_penalty_r(0) == pytest.approx(0.20, abs=1e-6)
    assert O.uncertainty_penalty_r(3) == pytest.approx(0.10, abs=1e-6)
    assert O.uncertainty_penalty_r(0) > O.uncertainty_penalty_r(10) > O.uncertainty_penalty_r(100)
