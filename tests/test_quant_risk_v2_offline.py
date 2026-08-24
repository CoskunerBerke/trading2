"""Quant Evaluation V1 — Risk V2 offline rapor entegrasyonu testleri.

Kritik ispatlar:
* Girdi ÜRETİM şemasıdır: gerçek `FuturesLedgerV2.to_dict()` çıktısı okunur (elle kurgu değil).
* Rapor küme, maruziyet, pozisyon başına risk katkısı, mevcut/advisory kaldıraç ve gerekçe üretir.
* Advisory hiçbir koşulda mevcut kaldıracı ARTIRMAZ; eksik/eski/bozuk veride konservatiftir.
* Aktif `RiskEngine` kararı rapor üretiminden ETKİLENMEZ.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from tradingbot.accounting import FuturesLedgerV2, SizeSpec, SlippageModel
from tradingbot.quant.risk_v2 import (ABS_MAX_LEVERAGE, ABS_MIN_LEVERAGE, ADVISORY_BANNER,
                                      RiskV2Config, offline_risk_report, positions_from_ledger)
from tradingbot.risk import PROFILES, RiskEngine, build_state

UTC = timezone.utc
T0 = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
BASE = [1.0, -0.5, 2.0, -1.0, 0.5, 1.5, -0.8, 0.9, -0.2, 1.1] * 3


def _ledger_doc(specs=(("AAVE/USDT", "LONG", 3), ("ETH/USDT", "LONG", 2), ("LDO/USDT", "LONG", 3))):
    """GERÇEK ledger ile açılmış pozisyonlardan üretim şemalı sözlük."""
    led = FuturesLedgerV2(5000, slippage=SlippageModel.zero(), max_positions=10)
    for sym, side, lev in specs:
        stop = D(95) if side == "LONG" else D(105)
        led.open(sym, side, D(100), SizeSpec(D(300), leverage=lev), stop=stop, now=T0)
    return led.to_dict()


def _returns():
    return {"AAVE/USDT": BASE, "ETH/USDT": [x * 1.1 for x in BASE],
            "LDO/USDT": [x * 0.9 for x in BASE], "QQQ/USDT": [-x for x in BASE]}


def test_reads_production_ledger_schema():
    pos = positions_from_ledger(_ledger_doc())
    assert len(pos) == 3
    p = pos[0]
    assert p["symbol"] == "AAVE/USDT" and p["direction"] == "LONG"
    assert p["notional_usdt"] == pytest.approx(300.0, rel=1e-3)
    assert p["risk_usdt"] == pytest.approx(15.0, rel=1e-2)      # (100-95) * 3 adet
    assert p["leverage"] == 3
    assert positions_from_ledger(None) == [] and positions_from_ledger({}) == []


def test_closed_positions_excluded():
    doc = _ledger_doc()
    list(doc["positions"].values())[0]["status"] = "CLOSED"
    assert len(positions_from_ledger(doc)) == 2


def test_correlated_longs_reported_as_single_cluster():
    rep = offline_risk_report(positions_from_ledger(_ledger_doc()), _returns())
    assert rep["n_positions"] == 3 and rep["n_clusters"] == 1
    big = rep["largest_cluster"]
    assert set(big["symbols"]) == {"AAVE/USDT", "ETH/USDT", "LDO/USDT"}
    assert big["share_of_total"] == pytest.approx(1.0)
    assert rep["exposure"]["total_long_usdt"] > 0
    assert rep["exposure"]["total_short_usdt"] == 0.0


def test_risk_contributions_and_advisory_fields():
    rep = offline_risk_report(positions_from_ledger(_ledger_doc()), _returns())
    contrib = {c["symbol"]: c for c in rep["risk_contributions"]}
    assert set(contrib) == {"AAVE/USDT", "ETH/USDT", "LDO/USDT"}
    assert all(c["risk_share_of_total"] is not None for c in contrib.values())
    # paylar 6 haneye yuvarlanır → 3 × 0.333333 toplamı 1.0'a 1e-5 içinde yakındır
    assert sum(c["risk_share_of_total"] for c in contrib.values()) == pytest.approx(1.0, abs=1e-5)
    adv = {a["symbol"]: a for a in rep["advisories"]}
    for a in adv.values():
        assert ABS_MIN_LEVERAGE <= a["advised_leverage"] <= ABS_MAX_LEVERAGE
        assert a["advised_leverage"] <= max(a["current_leverage"], ABS_MIN_LEVERAGE)
        assert isinstance(a["derisk_reasons"], list) and a["derisk_reasons"]
    assert "CLUSTER_CONCENTRATION_HIGH" in adv["AAVE/USDT"]["derisk_reasons"]
    assert rep["increases_risk"] is False


def test_advisory_never_increases_risk_even_in_calm_market():
    calm = {s: [0.01] * 40 for s in ("AAVE/USDT", "ETH/USDT", "LDO/USDT")}
    rep = offline_risk_report(positions_from_ledger(_ledger_doc()), calm)
    assert rep["increases_risk"] is False
    for a in rep["advisories"]:
        assert a["advised_leverage"] <= max(a["current_leverage"], ABS_MIN_LEVERAGE)


def test_missing_returns_are_warned_not_assumed_independent():
    """Korelasyon kanıtı yokken pozisyonlar BAĞIMSIZ SAYILMAZ.

    Bu test eskiden `n_clusters == 3` (her sembol tek başına) bekliyordu; bu, "bağımsız
    varsayılmadı" uyarısıyla ÇELİŞİYOR ve konsantrasyonu olduğundan düşük gösteriyordu.
    Beklenti artık daha KATIDIR: aynı yöndeki üç pozisyon tek konservatif kümede toplanır.
    """
    rep = offline_risk_report(positions_from_ledger(_ledger_doc()), {})
    assert rep["correlation_quality"] == "UNAVAILABLE"
    assert rep["cluster_basis"] == "conservative_direction_fallback"
    assert any("BAĞIMSIZ SAYILMADI" in w for w in rep["warnings"])
    assert rep["n_clusters"] == 1                              # üç LONG → TEK küme
    assert rep["cluster_labels"] == ["UNKNOWN_CORRELATION_LONG"]
    c0 = rep["exposure"]["clusters"][0]
    assert sorted(c0["symbols"]) == ["AAVE/USDT", "ETH/USDT", "LDO/USDT"]
    assert c0["share_of_total"] == pytest.approx(1.0)          # gerçek konsantrasyon görünür
    for a in rep["advisories"]:
        assert "VOL_UNKNOWN_CONSERVATIVE" in a["derisk_reasons"]
        assert a["advised_leverage"] <= a["current_leverage"]  # risk ARTMAZ
    assert rep["increases_risk"] is False


def test_stale_and_degraded_data_flags_are_conservative():
    now = 1_800_000_000_000
    rep = offline_risk_report(positions_from_ledger(_ledger_doc()), _returns(),
                              data_as_of_ms=now - 10 * 86_400_000, now_ms=now,
                              max_data_age_ms=86_400_000)
    assert rep["data_stale"] is True and rep["data_quality_ok"] is False
    assert any("STALE_MARKET_DATA" in w for w in rep["warnings"])
    assert all("DATA_QUALITY_DEGRADED_DERISK" in a["derisk_reasons"] for a in rep["advisories"])
    assert rep["increases_risk"] is False


def test_empty_portfolio_is_safe():
    rep = offline_risk_report([], {})
    assert rep["n_positions"] == 0 and rep["n_clusters"] == 0
    assert rep["largest_cluster"] is None and rep["advisories"] == []
    assert rep["increases_risk"] is False


def test_report_declares_advisory_only_and_is_pure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rep = offline_risk_report(positions_from_ledger(_ledger_doc()), _returns())
    assert rep["advisory_only"] is True
    assert rep["applies_to_active_engine"] is False
    assert rep["banner"] == ADVISORY_BANNER == "ADVISORY ONLY — ACTIVE RISK ENGINE UNCHANGED"
    assert rep["leverage_bounds"] == {"min": 2, "max": 5, "paper_only": True}
    assert list(tmp_path.iterdir()) == []                      # dosya/emir/outbox üretilmedi
    assert rep == offline_risk_report(positions_from_ledger(_ledger_doc()), _returns())


def test_advisory_only_false_fails_closed():
    with pytest.raises(ValueError, match="RISK_V2_ADVISORY_ONLY"):
        offline_risk_report([], {}, cfg=RiskV2Config(advisory_only=False))


def test_active_risk_engine_decision_unchanged_by_report():
    eng = RiskEngine(PROFILES["PAPER_RESEARCH"])
    st = build_state(equity=50.0, starting_equity=50.0, available=50.0, used_margin=0.0,
                     positions=[], history=[], high_water_mark=None, now=T0)
    plan = {"symbol": "ETH/USDT", "market_type": "USDM_PERP", "direction": "LONG",
            "entry": 3000.0, "stop": 2940.0, "notional": 30.0, "margin": 15.0,
            "leverage": 2, "min_notional": 5.0}
    before = eng.evaluate(plan, st).to_dict()
    offline_risk_report(positions_from_ledger(_ledger_doc()), _returns())
    after = eng.evaluate(plan, st).to_dict()
    assert before == after                                     # aktif motor ETKİLENMEDİ
