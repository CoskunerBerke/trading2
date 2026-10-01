"""FAZ 1 — dört ileriye dönük bakım düzeltmesinin regresyon testleri.

Kapsam:

* **1A** `_entry_flush` bağ yazımı gözlenebilirliği — "işlem açılmadı" ile "bağ yazılamadı"
  asla aynı görünemez.
* **1B** geçersiz kara liste anahtarı (`-|LONG`) — ileriye dönük olarak ÜRETİLMEZ, eskiler
  silinmez ama hiçbir kararı ENGELLEYEMEZ.
* **1C** dürüst maliyet atfı — kayma AYRI raporlanır, iki kez sayılmaz, ölçülmeyen `None`.
* **1D** düşük örneklemde kapı dürüstlüğü — ön koşul düşünce bağımlı kapılar
  `NOT_EVALUABLE_LOW_SAMPLE`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tradingbot.learn.entry_eval import (GATE_MIN_LINKED_CLOSES, GATE_STATUS_EVALUATED,
                                         GATE_STATUS_LOW_SAMPLE, SAMPLE_DEPENDENT_GATES,
                                         _gates, cost_decomposition)
from tradingbot.learning import (INVALID_SETUP_TOKENS, LEGACY_INVALID_SETUP_KEY, Learner,
                                 is_valid_setup_key, normalize_setup_token, setup_stat_key)


# ---------------------------------------------------------------- 1B: geçersiz setup anahtarı

@pytest.mark.parametrize("bad", ["", "  ", "-", "--", "NONE", "none", "unknown", "UNKNOWN",
                                 "N/A", "null", None])
def test_1b_invalid_setup_tokens_never_produce_a_key(bad):
    assert normalize_setup_token(bad) is None
    assert setup_stat_key(bad, "LONG") is None
    assert setup_stat_key("kirilim", bad) is None


def test_1b_valid_setup_key_is_produced_and_recognised():
    assert setup_stat_key("kirilim", "LONG") == "kirilim|LONG"
    assert is_valid_setup_key("kirilim|LONG") is True
    assert is_valid_setup_key("-|LONG") is False
    assert is_valid_setup_key("kirilim") is False
    assert is_valid_setup_key(None) is False


def test_1b_invalid_key_can_never_block_an_engine_decision(tmp_path: Path):
    """Kara listede `-|LONG` DURSA BİLE kurulum ölçülemeyen bir aday ENGELLENEMEZ."""
    lr = Learner(tmp_path / "learning.json", min_trades=5)
    lr.state.blacklist = ["-|LONG", "kirilim|LONG"]
    # Geçersiz kurulum → anahtar kurulmaz → engelleme YOK.
    assert lr.is_blacklisted("-", "LONG") is False
    assert lr.is_blacklisted("", "LONG") is False
    assert lr.is_blacklisted(None, "LONG") is False
    assert lr.is_blacklisted("UNKNOWN", "LONG") is False
    # Gerçek kurulum hâlâ engellenebilir — düzeltme kara listeyi ETKİSİZLEŞTİRMEZ.
    assert lr.is_blacklisted("kirilim", "LONG") is True


def _rec(i: int, won: bool, *, setup: str | None = "kirilim") -> dict:
    r = {"id": f"F{i}", "symbol": "ETH/USDT", "side": "LONG", "entry": 100.0,
         "exit_reason": "hedef2" if won else "stop", "pnl": 2.0 if won else -1.0,
         "r_multiple": 2.0 if won else -1.0, "mae_pct": -0.5, "mfe_pct": 3.0 if won else 0.2,
         "bars_held": 4, "leverage": 2,
         "features": {"bias_trend": 0.6 if won else -0.6, "rr": 2.5, "atr_pct": 0.3}}
    if setup is not None:
        r["setup_type"] = setup
    return r


def test_1b_learning_never_creates_an_invalid_key_forward(tmp_path: Path):
    lr = Learner(tmp_path / "learning.json", min_trades=5)
    for i in range(12):
        lr.learn(_rec(i, i % 3 == 0, setup=None))       # kurulum ÖLÇÜLEMEDİ
    assert lr.state.setup_stats == {}, "kurulum yokken anahtar uretilmemeli"
    assert lr.state.blacklist == []


def test_1b_learning_records_a_measured_setup_from_the_record_root(tmp_path: Path):
    """`setup_type` kaydın KÖKÜNDEDİR; doğru adresten okunur (uydurma değil, ölçüm)."""
    lr = Learner(tmp_path / "learning.json", min_trades=5)
    for i in range(4):
        lr.learn(_rec(i, won=False, setup="kirilim"))
    assert "kirilim|LONG" in lr.state.setup_stats
    assert lr.state.setup_stats["kirilim|LONG"]["n"] == 4


def test_1b_blacklist_rebuild_drops_invalid_keys_but_keeps_history(tmp_path: Path):
    lr = Learner(tmp_path / "learning.json", min_trades=5)
    # Miras satır: geçmiş YENİDEN YAZILMAZ.
    lr.state.setup_stats["-|LONG"] = {"n": 40, "wins": 2, "sum_r": -30.0}
    for i in range(12):
        lr.learn(_rec(i, won=False, setup="kirilim"))
    assert "-|LONG" in lr.state.setup_stats, "miras istatistik SILINMEZ"
    assert "-|LONG" not in lr.state.blacklist, "gecersiz anahtar kara listeye GIREMEZ"
    assert "kirilim|LONG" in lr.state.blacklist


def test_1b_legacy_invalid_keys_are_surfaced_with_the_audit_code(tmp_path: Path):
    lr = Learner(tmp_path / "learning.json", min_trades=5)
    lr.state.setup_stats["-|LONG"] = {"n": 40, "wins": 2, "sum_r": -30.0}
    lr.state.setup_stats["kirilim|SHORT"] = {"n": 3, "wins": 1, "sum_r": 0.5}
    lr.state.blacklist = ["-|LONG"]
    rows = lr.legacy_invalid_setup_keys()
    assert [r["key"] for r in rows] == ["-|LONG"]
    assert rows[0]["code"] == LEGACY_INVALID_SETUP_KEY
    assert rows[0]["in_blacklist"] is True
    assert rows[0]["blocks_decisions"] is False
    assert rows[0]["n"] == 40


def test_1b_engine_v3_does_not_consume_the_blacklist():
    """Sözleşme koruması: v3 motoru bu kara listeyi TÜKETMEZ. Kazara bağlanırsa test düşer."""
    src = Path("tradingbot/engine_v3.py").read_text(encoding="utf-8")
    assert "is_blacklisted" not in src
    assert "blacklist" not in src


def test_1b_helpers_cover_every_declared_invalid_token():
    for tok in INVALID_SETUP_TOKENS:
        assert normalize_setup_token(tok) is None


# ---------------------------------------------------------------- 1C: dürüst maliyet atfı

def _close(**kw) -> dict:
    base = {"fees": 0.02, "funding": 0.0, "r_multiple": -1.0, "net_pnl": -1.0,
            "raw": {"slippage_cost": 0.05, "spread_cost": 0.0}}
    base.update(kw)
    return base


def test_1c_components_are_reported_separately_and_not_double_counted():
    d = cost_decomposition(_close(), risk=1.0)
    assert d["fee_drag_r"] == 0.02
    assert d["slippage_drag_r"] == 0.05
    # Miras alan yalnız komisyon+funding'dir; kayma İÇİNDE DEĞİLDİR.
    assert d["reported_cost_r"] == 0.02
    assert d["total_measured_friction_r"] == 0.07
    assert d["reported_cost_r"] + d["slippage_drag_r"] == d["total_measured_friction_r"]


def test_1c_measured_zero_is_zero_but_unmeasured_is_none():
    d = cost_decomposition(_close(), risk=1.0)
    assert d["funding_drag_r"] == 0.0, "olculmus sifir sifirdir"
    assert d["impact_drag_r"] is None, "olculmemis alan None kalir"
    assert d["cost_provenance"]["funding"] == "MEASURED"
    assert d["cost_provenance"]["impact_cost"] == "MISSING"


def test_1c_unmeasured_cost_is_never_converted_to_zero():
    d = cost_decomposition({"r_multiple": -1.0, "net_pnl": -1.0}, risk=1.0)
    assert d["fee_drag_r"] is None and d["funding_drag_r"] is None
    assert d["slippage_drag_r"] is None and d["impact_drag_r"] is None
    assert d["total_measured_friction_r"] is None
    assert d["n_measured_components"] == 0


def test_1c_missing_risk_yields_none_with_an_explicit_reason():
    d = cost_decomposition(_close(), risk=None)
    assert d["total_measured_friction_r"] is None
    assert d["cost_provenance"]["unavailable_reason"] == "RISK_USDT_UNKNOWN"


def test_1c_f00030_known_decomposition_is_reproduced():
    """F00030'un bilinen dökümü: fee 0,027941R, funding 0R ÖLÇÜLDÜ, kayma 0,083363R."""
    risk = 0.7168
    close = _close(fees=round(0.027941 * risk, 10), funding=0.0,
                   raw={"slippage_cost": round(0.083363 * risk, 10), "spread_cost": 0.0})
    d = cost_decomposition(close, risk=risk)
    assert d["fee_drag_r"] == pytest.approx(0.027941, abs=1e-5)
    assert d["funding_drag_r"] == 0.0
    assert d["slippage_drag_r"] == pytest.approx(0.083363, abs=1e-5)
    assert d["reported_cost_r"] == pytest.approx(0.027941, abs=1e-5)
    assert d["total_measured_friction_r"] == pytest.approx(0.111304, abs=1e-5)


def test_1c_pnl_is_never_modified_by_the_decomposition():
    c = _close()
    before = {k: (dict(v) if isinstance(v, dict) else v) for k, v in c.items()}
    cost_decomposition(c, risk=1.0)
    assert c == before, "maliyet dokumu kapanis kaydini DEGISTIREMEZ"


# ---------------------------------------------------------------- 1D: düşük örneklem dürüstlüğü

def _fam_rep() -> dict:
    """n=1 patolojisi: karşı-olgusal seri baseline ile AYNI → eşitlik kapıları trivial geçer."""
    b = {"max_drawdown_r": -1.06, "tail_loss_r_cvar5": -1.06, "profit_factor": 0.0,
         "profit_factor_state": "no_wins", "total_r": -1.06}
    return {"baseline": b, "counterfactual": dict(b), "delta_expectancy_r": 0.0,
            "delta_ci": {}}


def _run_gates(n_linked: int) -> dict[str, dict]:
    g = _gates(_fam_rep(), n_linked=n_linked, days=float(n_linked),
               dir_counts={"LONG": n_linked}, regime_counts={"TREND_UP": n_linked},
               top_symbol_share=0.2, wf={}, folds={},
               leakage={"clean": True, "state": "ok", "checked": n_linked})
    return {x["code"]: x for x in g}


def test_1d_low_sample_dependent_gates_are_not_evaluable():
    gates = _run_gates(1)
    for code in SAMPLE_DEPENDENT_GATES:
        assert gates[code]["status"] == GATE_STATUS_LOW_SAMPLE, code
        assert gates[code]["passed"] is False, code


def test_1d_the_named_gates_from_the_contract_are_covered():
    gates = _run_gates(1)
    for code in ("POSITIVE_EXPECTANCY_IMPROVEMENT", "DRAWDOWN_NOT_WORSE",
                 "TAIL_RISK_NOT_WORSE", "CONFIDENCE_INTERVAL_EXCLUDES_ZERO",
                 "WALK_FORWARD_CONSISTENCY"):
        assert gates[code]["status"] == GATE_STATUS_LOW_SAMPLE
        assert gates[code]["passed"] is False


def test_1d_equality_gates_would_have_passed_trivially_before_the_fix():
    """Düzeltmenin GEREKÇESİ: ham karşılaştırma n=1'de gerçekten 'geçti' diyordu."""
    gates = _run_gates(1)
    assert gates["DRAWDOWN_NOT_WORSE"]["raw_passed"] is True
    assert gates["TAIL_RISK_NOT_WORSE"]["raw_passed"] is True
    assert gates["DRAWDOWN_NOT_WORSE"]["passed"] is False


def test_1d_raw_metrics_are_still_displayed():
    gates = _run_gates(1)
    d = gates["DRAWDOWN_NOT_WORSE"]["detail"]
    assert "-1.06" in d, "ham metrik gizlenmez"
    assert GATE_STATUS_LOW_SAMPLE in d


def test_1d_sample_prerequisites_themselves_stay_evaluated():
    gates = _run_gates(1)
    assert gates["MIN_LINKED_CLOSES"]["status"] == GATE_STATUS_EVALUATED
    assert gates["MIN_LINKED_CLOSES"]["passed"] is False
    assert gates["NO_LEAKAGE_POINT_IN_TIME"]["status"] == GATE_STATUS_EVALUATED


def test_1d_above_the_threshold_gates_are_evaluated_normally():
    gates = _run_gates(GATE_MIN_LINKED_CLOSES)
    for code in SAMPLE_DEPENDENT_GATES:
        assert gates[code]["status"] == GATE_STATUS_EVALUATED, code
    assert gates["DRAWDOWN_NOT_WORSE"]["passed"] is True     # eşitlik: kötüleşmedi


def test_1d_promotion_requirements_are_not_weakened():
    from tradingbot.learn.entry_eval import GATE_MIN_DAYS
    assert GATE_MIN_LINKED_CLOSES == 50
    assert GATE_MIN_DAYS == 30
    gates = _run_gates(1)
    assert not all(g["passed"] for g in gates.values())


# ------------------------------------------------- 1B: panoda görünürlük (LEGACY_INVALID)

def test_1b_dashboard_surfaces_legacy_invalid_keys(tmp_path: Path):
    """Geçersiz miras anahtarı ekranda AÇIKÇA işaretlenir; sessizce silinmez."""
    import json

    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    sd, dd = tmp_path / "state", tmp_path / "data"
    sd.mkdir(), dd.mkdir()
    (sd / "learning.json").write_text(json.dumps({
        "n_trades": 12, "n_wins": 4, "sum_r": -1.0, "lessons": [],
        "blacklist": ["-|LONG", "kirilim|SHORT"],
        "setup_stats": {"-|LONG": {"n": 40, "wins": 2, "sum_r": -30.0},
                        "kirilim|SHORT": {"n": 12, "wins": 3, "sum_r": -2.0},
                        "NONE|LONG": {"n": 5, "wins": 1, "sum_r": -1.0}},
        "agent_weights": {}}), encoding="utf-8")
    r = TestClient(create_app(sd, dd)).get("/learning")
    assert r.status_code == 200
    assert LEGACY_INVALID_SETUP_KEY in r.text
    assert "kirilim|SHORT" in r.text            # geçerli anahtar normal görünür
    assert "NONE|LONG" in r.text                # kara listede olmayan geçersiz anahtar da
