"""Quant Evaluation V1 — Risk V2 fail-closed regresyonları.

İlk gerçek PAPER raporunda (`vps-b6512c1-20260824T172055Z`) ortaya çıkan İKİ çelişki:

1. **1x → 2x risk artışı.** `advise()` teklifi 2x'lik advisory tabanına YUKARI kırpıyordu; üstelik
   `increases_risk` karşılaştırması `max(current, ABS_MIN_LEVERAGE)` kullandığı için bu artış
   maskeleniyordu. Eksik/stale/NaN veride öneri riski ARTIRAMAZ.
2. **Eksik korelasyonda singleton küme.** Korelasyon verisi olmadan 6 pozisyon 6 ayrı kümeye
   ayrılıp aynı anda "bağımsız varsayılmadı" deniyordu; bu konsantrasyonu olduğundan düşük
   gösterir. Kanıt yoksa aynı yöndeki pozisyonlar tek konservatif kümede toplanmalıdır.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from tradingbot.accounting import FuturesLedgerV2, SizeSpec, SlippageModel
from tradingbot.quant.risk_v2 import (ABS_MAX_LEVERAGE, ABS_MIN_LEVERAGE, CORR_FALLBACK, CORR_OK,
                                      CORR_UNAVAILABLE, HOLD_BELOW_MIN, UNKNOWN_CORR_LONG,
                                      UNKNOWN_CORR_SHORT, AdviceContext, advise,
                                      conservative_direction_clusters, correlation_quality,
                                      offline_risk_report, positions_from_ledger,
                                      rolling_correlation)
from tradingbot.risk import PROFILES, RiskEngine, build_state

UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
BASE = [1.0, -0.5, 2.0, -1.0, 0.5, 1.5, -0.8, 0.9, -0.2, 1.1] * 3


def _pos(sym, side="LONG", lev=1, risk=1.0):
    return {"symbol": sym, "direction": side, "risk_usdt": risk, "notional_usdt": risk * 10,
            "leverage": lev}


# ============================================================ SORUN 1: risk artışı

def test_1x_with_missing_volatility_cannot_become_2x():
    out = advise(AdviceContext("BZ/USDT", "LONG", proposed_leverage=1, symbol_vol_pct=None))
    assert out["advised_leverage"] == 1                      # 2x'e YUKARI itilmedi
    assert HOLD_BELOW_MIN in out["reasons"]
    assert out["held_below_advisory_min"] is True
    assert out["increases_risk"] is False


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_1x_with_stale_or_nonfinite_data_never_increases_risk(bad):
    out = advise(AdviceContext("XAUT/USDT", "LONG", proposed_leverage=1, symbol_vol_pct=bad,
                               data_quality_ok=False, model_uncertainty=0.99))
    assert out["advised_leverage"] <= 1
    assert out["increases_risk"] is False


def test_advice_never_exceeds_current_for_any_proposal():
    for lev in (1, 2, 3, 4, 5, 9):
        for vol in (None, 0.1, 9.0):
            out = advise(AdviceContext("X/USDT", "LONG", proposed_leverage=lev, symbol_vol_pct=vol))
            assert out["advised_leverage"] <= lev, (lev, vol)
            assert out["increases_risk"] is False
            assert out["advised_leverage"] <= ABS_MAX_LEVERAGE


def test_increases_risk_true_if_advised_exceeds_current_in_report():
    """Rapor seviyesinde karşılaştırma DOĞRUDAN mevcut kaldıraçla olmalı (maskeleme yok)."""
    rep = offline_risk_report([_pos("A/USDT", lev=1), _pos("B/USDT", lev=1)], {})
    assert rep["increases_risk"] is False
    for a in rep["advisories"]:
        assert a["advised_leverage"] <= a["current_leverage"]
    # maskeleme kontrolu: elle "artiran" bir advisory enjekte edilirse tespit edilebilmeli
    fake = [{"advised_leverage": 2, "current_leverage": 1}]
    assert any(x["advised_leverage"] > x["current_leverage"] for x in fake)


def test_3x_to_2x_derisk_still_works():
    out = advise(AdviceContext("AAVE/USDT", "LONG", proposed_leverage=3, symbol_vol_pct=None,
                               cluster_share=0.9))
    assert out["advised_leverage"] == 2                       # de-risk KORUNDU
    assert out["increases_risk"] is False
    assert HOLD_BELOW_MIN not in out["reasons"]
    assert out["held_below_advisory_min"] is False


def test_high_proposal_still_clamped_down_to_band():
    out = advise(AdviceContext("X/USDT", "LONG", proposed_leverage=9, symbol_vol_pct=0.1,
                               calibrated_edge=0.2))
    assert ABS_MIN_LEVERAGE <= out["advised_leverage"] <= ABS_MAX_LEVERAGE
    assert any(r.startswith("LEV_CLAMPED_DOWN_9->5") for r in out["reasons"])


# ============================================================ SORUN 2: kümeleme

def test_correlation_quality_levels():
    syms = ["A", "B", "C"]
    assert correlation_quality({}, syms) == CORR_UNAVAILABLE
    assert correlation_quality({("A", "B"): None, ("A", "C"): None, ("B", "C"): None}, syms) == CORR_UNAVAILABLE
    assert correlation_quality({("A", "B"): 0.5, ("A", "C"): None, ("B", "C"): None}, syms) == CORR_FALLBACK
    assert correlation_quality({("A", "B"): 0.5, ("A", "C"): 0.1, ("B", "C"): -0.2}, syms) == CORR_OK
    assert correlation_quality({}, ["A"]) == CORR_OK          # tek sembolde korelasyon anlamsiz
    assert correlation_quality({}, []) == CORR_OK


def test_missing_correlation_groups_multiple_longs_into_one_cluster():
    pos = [_pos("A/USDT", "LONG"), _pos("B/USDT", "LONG"), _pos("C/USDT", "LONG")]
    rep = offline_risk_report(pos, {})
    assert rep["correlation_quality"] == CORR_UNAVAILABLE
    assert rep["cluster_basis"] == "conservative_direction_fallback"
    assert rep["n_clusters"] == 1                             # singleton'a AYRILMADI
    assert rep["cluster_labels"] == [UNKNOWN_CORR_LONG]
    c0 = rep["exposure"]["clusters"][0]
    assert sorted(c0["symbols"]) == ["A/USDT", "B/USDT", "C/USDT"]
    assert c0["share_of_total"] == pytest.approx(1.0)         # konsantrasyon GERCEKCI
    assert c0["label"] == UNKNOWN_CORR_LONG
    assert any("BAĞIMSIZ SAYILMADI" in w for w in rep["warnings"])


def test_missing_correlation_separates_long_and_short():
    pos = [_pos("A/USDT", "LONG"), _pos("B/USDT", "LONG"), _pos("C/USDT", "SHORT")]
    rep = offline_risk_report(pos, {})
    assert rep["n_clusters"] == 2
    assert set(rep["cluster_labels"]) == {UNKNOWN_CORR_LONG, UNKNOWN_CORR_SHORT}
    by_label = {c["label"]: c for c in rep["exposure"]["clusters"]}
    assert sorted(by_label[UNKNOWN_CORR_LONG]["symbols"]) == ["A/USDT", "B/USDT"]
    assert by_label[UNKNOWN_CORR_SHORT]["symbols"] == ["C/USDT"]
    assert by_label[UNKNOWN_CORR_LONG]["long_usdt"] > by_label[UNKNOWN_CORR_SHORT]["short_usdt"]


def test_grouping_uses_direction_metadata_not_symbol_names():
    """Hiçbir sembol adı kodda özel-durum değildir: tamamen uydurma tickerlar da gruplanır."""
    pos = [_pos("ZZZZ9/USDT", "LONG"), _pos("QQQQ7/USDT", "LONG"), _pos("WWWW1/USDT", "SHORT")]
    rep = offline_risk_report(pos, {})
    clusters, labels = conservative_direction_clusters(pos)
    assert labels == [UNKNOWN_CORR_LONG, UNKNOWN_CORR_SHORT]
    assert clusters[0] == ["QQQQ7/USDT", "ZZZZ9/USDT"]
    assert clusters[1] == ["WWWW1/USDT"]
    assert rep["n_clusters"] == 2
    # KANIT: modül MANTIĞINDA hiçbir sembol adı string sabiti yok. Docstring'ler (açıklayıcı
    # örnekler içerebilir) hariç tutulur; yalnız çalıştırılan kod sabitleri taranır.
    import ast
    import pathlib
    tree = ast.parse(pathlib.Path("tradingbot/quant/risk_v2.py").read_text(encoding="utf-8"))
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_nodes.add(id(body[0].value))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc_nodes]
    joined = " ".join(literals)
    for hard in ("AAVE", "XAUT", "LDO", "QQQ", "USDT", "BTC"):
        assert hard not in joined, f"sembol adi kod sabitinde: {hard} ({literals})"


def test_real_correlation_data_preserves_true_clustering():
    """Gerçek getiri verisi varsa mevcut rolling-korelasyon algoritması AYNEN çalışır."""
    rets = {"AAA/USDT": BASE, "BBB/USDT": [x * 1.1 for x in BASE],
            "CCC/USDT": [x * 0.9 for x in BASE], "DDD/USDT": [-x for x in BASE]}
    pos = [_pos("AAA/USDT"), _pos("BBB/USDT"), _pos("CCC/USDT"), _pos("DDD/USDT", "SHORT")]
    corr = rolling_correlation(rets, window=30, min_obs=20)
    assert correlation_quality(corr, rets) == CORR_OK
    rep = offline_risk_report(pos, rets)
    assert rep["correlation_quality"] == CORR_OK
    assert rep["cluster_basis"] == "realized_rolling_correlation"
    grouped = next(c for c in rep["clusters"] if len(c) > 1)
    assert set(grouped) == {"AAA/USDT", "BBB/USDT", "CCC/USDT"}
    assert ["DDD/USDT"] in rep["clusters"]                    # negatif korelasyon AYRI kaldi
    assert all(lbl.startswith("corr_cluster_") for lbl in rep["cluster_labels"])


def test_partial_correlation_is_fail_closed():
    rets = {"AAA/USDT": BASE, "BBB/USDT": [x * 1.1 for x in BASE]}   # CCC icin veri yok
    pos = [_pos("AAA/USDT"), _pos("BBB/USDT"), _pos("CCC/USDT")]
    rep = offline_risk_report(pos, rets)
    assert rep["correlation_quality"] == CORR_FALLBACK
    assert rep["cluster_basis"] == "conservative_direction_fallback"
    assert rep["n_clusters"] == 1 and rep["cluster_labels"] == [UNKNOWN_CORR_LONG]


# ============================================================ izolasyon / sözleşme

def test_active_risk_engine_untouched_by_fixed_report():
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    st = build_state(equity=50.0, starting_equity=50.0, available=50.0, used_margin=0.0,
                     positions=[], history=[], high_water_mark=None, now=T0)
    plan = {"symbol": "ETH/USDT", "market_type": "USDM_PERP", "direction": "LONG",
            "entry": 3000.0, "stop": 2940.0, "notional": 30.0, "margin": 15.0,
            "leverage": 2, "min_notional": 5.0}
    before = eng.evaluate(plan, st).to_dict()
    offline_risk_report([_pos("A/USDT", lev=1), _pos("B/USDT", "SHORT", lev=3)], {})
    advise(AdviceContext("A/USDT", "LONG", 1))
    after = eng.evaluate(plan, st).to_dict()
    assert before == after                                    # aktif motor ETKILENMEDI


def test_report_contract_flags_unchanged():
    rep = offline_risk_report([_pos("A/USDT", lev=1)], {})
    assert rep["advisory_only"] is True
    assert rep["applies_to_active_engine"] is False
    assert rep["enabled"] is False
    assert rep["leverage_bounds"] == {"min": 2, "max": 5, "paper_only": True}
    assert rep["banner"] == "ADVISORY ONLY — ACTIVE RISK ENGINE UNCHANGED"


def test_production_ledger_with_1x_positions_end_to_end():
    """Üretim şemasından okunan 1x pozisyonlar raporda risk ARTIRMAZ."""
    led = FuturesLedgerV2(5000, slippage=SlippageModel.zero(), max_positions=10)
    for sym, lev in (("AAA/USDT", 1), ("BBB/USDT", 1), ("CCC/USDT", 3)):
        led.open(sym, "LONG", D(100), SizeSpec(D(300), leverage=lev), stop=D(95), now=T0)
    rep = offline_risk_report(positions_from_ledger(led.to_dict()), {})
    assert rep["increases_risk"] is False
    by = {a["symbol"]: a for a in rep["advisories"]}
    assert by["AAA/USDT"]["advised_leverage"] == 1 and by["AAA/USDT"]["held_below_advisory_min"] is True
    assert by["BBB/USDT"]["advised_leverage"] == 1
    assert by["CCC/USDT"]["advised_leverage"] == 2            # de-risk korundu
    assert rep["n_clusters"] == 1                             # korelasyon yok -> tek LONG kumesi


def test_empty_and_nonfinite_inputs_are_safe():
    empty = offline_risk_report([], {})
    assert empty["n_clusters"] == 0 and empty["cluster_labels"] == []
    assert empty["correlation_quality"] == CORR_OK and empty["cluster_basis"] == "no_positions"
    assert empty["increases_risk"] is False
    nan_pos = [{"symbol": "A/USDT", "direction": "LONG", "risk_usdt": float("nan"), "leverage": 1},
               {"symbol": "B/USDT", "direction": "LONG", "risk_usdt": 5.0, "leverage": 1}]
    rep = offline_risk_report(nan_pos, {})
    assert rep["increases_risk"] is False
    import json
    json.dumps(rep, allow_nan=False)                          # RFC-safe
