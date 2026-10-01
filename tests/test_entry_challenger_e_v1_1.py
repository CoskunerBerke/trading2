"""entry_v1.1.0 E ailesi — KAPSAM EŞLİ ısı/yoğunlaşma + rapor yolu tutarlılığı.

7.  E v1.1 yalnız kapsam-eşli futures alanlarını kullanır.
8.  E v1.1 `portfolio_open_risk_usdt`e ASLA düşmez.
9.  E v1.1 `same_direction_open`a ASLA düşmez.
10. Yeni alan eksikse AÇIK ABSTAIN (VETO/ACCEPT değil).
11. Ölçülmüş sıfır sıfırdır.
12-13. F00036'nın kayıtlı v1.1 kararı dokunulmaz; her denetlenen tanımla yeniden değerlendirme
       hiçbir olayı YENİDEN YAZMAZ.
14. Giriş anı E == kapanış-atıf E (aynı değişmez snapshot) bayt bayt.
15. Kapanış anındaki canlı equity donmuş E sonucunu DEĞİŞTİREMEZ.
16. entry_v1.0.0 davranışı okunur ve DEĞİŞMEMİŞTİR (F00036 kayıtlı kanıtı aynen yeniden üretilir).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tradingbot.learn import profitability_ae as AE
from tradingbot.learn import profitability_experiment as PX
from tradingbot.learn.entry_challenger import (ABSTAIN, ACCEPT, E_SCOPE_COMBINED_DIAGNOSTIC,
                                               E_SCOPE_FUTURES_BUCKET, ENTRY_POLICY_V1_0,
                                               ENTRY_POLICY_V1_1, FAM_HEAT, VETO,
                                               EntryChallengerConfig, challenger_e,
                                               evaluate_all)
from tradingbot.learn.entry_eval import evaluate_trade
from tradingbot.learn.entry_snapshot import build_entry_snapshot
from tradingbot.learn.profitability_store import EV_DECISION, ExperimentStore

CFG10 = EntryChallengerConfig(policy_version=ENTRY_POLICY_V1_0)
CFG11 = EntryChallengerConfig(policy_version=ENTRY_POLICY_V1_1)
TS = "2026-09-07T02:43:40+00:00"

#: F00036'nın KAYITLI v1.1 E kanıtı (VPS, salt okunur denetim 2026-09-07) — pin.
F36_RECORDED_E_EVIDENCE = {"portfolio_open_risk_usdt": 13.076588219602494, "risk_budget_usdt": 6.0,
                           "open_risk_fraction": 2.179431, "max_open_risk_fraction": 0.8,
                           "same_direction_open": 11.0, "max_same_direction": 6,
                           "open_positions": 12.0}
F36_RECORDED_E_REASONS = ["PORTFOLIO_HEAT_HIGH", "DIRECTIONAL_CONCENTRATION"]


def snap(*, version=ENTRY_POLICY_V1_1, fut_risk=1.0, same_fut=1, budget=6.0,
         combined=13.076588219602494, same_comb=11, n_open=12, p_win=0.5, edge=0.5,
         **over) -> dict:
    chief = {"allow": True, "open_positions": n_open, "total_open_risk_usdt": combined,
             "same_direction_open": same_comb, "risk_budget_usdt": budget,
             "futures_stop_risk_usdt": fut_risk, "same_direction_open_futures": same_fut}
    dec = {"p_win": p_win, "consensus_score": 0.4, "regime": "TREND_UP", "dissent": [],
           "vetoes": []}
    opp = {"conservative_net_edge_r": edge, "avg_win_r": 2.0, "avg_loss_r": -1.0,
           "sample_size": 12}
    from tradingbot.core import from_iso
    s = build_entry_snapshot(run_id="r", cycle_id="85", symbol="ONDO/USDT", direction="LONG",
                             decision=dec, plan={"entry": 0.39, "stop": 0.3682, "targets": [0.43]},
                             opportunity=opp, chief_permission=chief,
                             baseline_accepted=True, policy_version=version,
                             code_sha="x", config_hash="y", now=from_iso(TS))
    for k, v in over.items():
        if v is None:
            s[k] = None
            s["sources"][k] = "MISSING"
            s["missing_fields"] = sorted(set(s["missing_fields"]) | {k})
        else:
            s[k] = v
    return s


def f36_snapshot() -> dict:
    """F00036'nın snapshot'ı gibi: entry_v1.0.0 ile yazılmış, YENİ alanları YOK."""
    s = snap(version=ENTRY_POLICY_V1_0, p_win=0.293, edge=0.677204)
    for k in ("portfolio_futures_stop_risk_usdt", "same_direction_open_futures", "portfolio_scope"):
        s.pop(k, None)
        s["sources"].pop(k, None)
    s["missing_fields"] = [m for m in s["missing_fields"] if not m.startswith(("portfolio_futures", "same_direction_open_f"))]
    return s


# ============================================================ 7-9: yalnız kapsam-eşli alanlar

def test_07_e_v11_uses_only_futures_bucket_fields():
    v = challenger_e(snap(fut_risk=1.0, same_fut=1), CFG11)
    assert v["decision"] == ACCEPT and v["e_scope"] == E_SCOPE_FUTURES_BUCKET
    ev = v["evidence"]
    assert ev["portfolio_futures_stop_risk_usdt"] == 1.0 and ev["risk_budget_usdt"] == 6.0
    assert ev["futures_heat_fraction"] == pytest.approx(1.0 / 6.0, abs=1e-6)
    assert ev["risk_budget_source"] == "ENTRY_SNAPSHOT_FROZEN"
    assert ev["same_direction_open_futures"] == 1
    # Eşikler AYNI.
    assert ev["max_open_risk_fraction"] == 0.8 and ev["max_same_direction"] == 6
    # Birleşik değerler yalnız TANI bölümünde ve karar için KULLANILMAZ.
    d = ev["diagnostics"]
    assert d["portfolio_open_risk_usdt_combined"] == pytest.approx(13.076588219602494)
    assert d["non_futures_component_usdt"] == pytest.approx(12.076588)
    assert d["same_direction_open_combined"] == 11
    assert d["combined_is_diagnostic_not_enforced"] is True and d["used_for_decision"] is False


@pytest.mark.parametrize("combined,same_comb", [(0.0, 0), (13.076588, 11), (999.0, 99), (None, None)])
def test_08_09_e_v11_never_falls_back_to_combined_fields(combined, same_comb):
    """Birleşik eski alanlar ne olursa olsun (sıcak, sıfır, eksik) v1.1 kararı DEĞİŞMEZ."""
    kw = {"portfolio_open_risk_usdt": combined, "same_direction_open": same_comb} \
        if combined is None else {}
    s = snap(fut_risk=1.0, same_fut=1, combined=(combined or 0.0), same_comb=(same_comb or 0), **kw)
    v = challenger_e(s, CFG11)
    assert v["decision"] == ACCEPT and v["reason_codes"] == ["PASSES_FILTER"]
    assert v["evidence"]["futures_heat_fraction"] == pytest.approx(1.0 / 6.0, abs=1e-6)
    # Tersi: futures kovası SICAK, birleşik alanlar serin → yine VETO (karar futures'tan).
    s2 = snap(fut_risk=5.9, same_fut=6, combined=0.0, same_comb=0)
    v2 = challenger_e(s2, CFG11)
    assert v2["decision"] == VETO
    assert set(v2["reason_codes"]) == {"PORTFOLIO_HEAT_HIGH", "DIRECTIONAL_CONCENTRATION"}


def test_08b_caller_budget_is_recorded_but_never_used_in_v11():
    s = snap(fut_risk=3.0, budget=6.0)                       # 0.5 → ACCEPT
    v_hi = challenger_e(s, CFG11, risk_budget_usdt=1.0)      # çağıran "1.0" derse VETO olurdu
    v_lo = challenger_e(s, CFG11, risk_budget_usdt=100.0)
    v_none = challenger_e(s, CFG11)
    for v in (v_hi, v_lo, v_none):
        assert v["decision"] == ACCEPT
        assert v["evidence"]["futures_heat_fraction"] == pytest.approx(0.5)
        assert v["evidence"]["caller_budget_used"] is False
    assert v_hi["evidence"]["caller_risk_budget_usdt"] == 1.0
    a, b = dict(v_hi), dict(v_lo)
    a["evidence"], b["evidence"] = dict(a["evidence"]), dict(b["evidence"])
    a["evidence"].pop("caller_risk_budget_usdt"), b["evidence"].pop("caller_risk_budget_usdt")
    assert a == b


# ============================================================ 10-11: eksik → ABSTAIN, sıfır → 0

@pytest.mark.parametrize("field", ["portfolio_futures_stop_risk_usdt", "same_direction_open_futures",
                                   "risk_budget_usdt"])
def test_10_missing_scope_matched_field_gives_explicit_abstain(field):
    s = snap(**{field: None})
    v = challenger_e(s, CFG11)
    assert v["decision"] == ABSTAIN and v["reason_codes"] == ["MISSING_DATA"]
    assert f"MISSING_DATA:{field}" in v["blockers"]
    assert v["decision"] not in (ACCEPT, VETO)
    # Deney adaptörü de ABSTAIN'e çevirir; FILTER ya da ACCEPT olmaz.
    leg = AE.point_in_time_ae(s, CFG11)["families"]["E"]
    assert leg["decision"] == "ABSTAIN" and leg["reason_codes"] == [AE.R_E_INPUT_MISSING]
    assert field in leg["missing"] and leg["raw_decision"] == ABSTAIN


def test_10b_old_snapshot_without_new_fields_is_abstain_under_v11_not_reinterpreted():
    s = f36_snapshot()
    s["policy_version"] = ENTRY_POLICY_V1_1            # yeni sürümle DEĞERLENDİRİLMEYE zorlansa bile
    v = challenger_e(s, CFG11)
    assert v["decision"] == ABSTAIN
    assert {"MISSING_DATA:portfolio_futures_stop_risk_usdt",
            "MISSING_DATA:same_direction_open_futures"} <= set(v["blockers"])
    assert v["evidence"]["futures_heat_fraction"] is None


def test_11_measured_zero_stays_zero():
    v = challenger_e(snap(fut_risk=0.0, same_fut=0), CFG11)
    assert v["decision"] == ACCEPT
    assert v["evidence"]["portfolio_futures_stop_risk_usdt"] == 0.0
    assert v["evidence"]["futures_heat_fraction"] == 0.0
    assert v["evidence"]["same_direction_open_futures"] == 0
    assert v["blockers"] == []
    # Bütçe sıfır/negatif ölçülemez → ABSTAIN (bölme uydurulmaz).
    assert challenger_e(snap(budget=0.0), CFG11)["decision"] == ABSTAIN


# ============================================================ 16: entry_v1.0.0 aynen

def test_16_legacy_v10_behaviour_unchanged_and_reproduces_f00036_recorded_evidence():
    s = f36_snapshot()
    v = challenger_e(s, CFG10, risk_budget_usdt=6.0)
    assert v["decision"] == VETO and v["reason_codes"] == F36_RECORDED_E_REASONS
    assert v["evidence"] == F36_RECORDED_E_EVIDENCE, "tarihsel E kanıtı DEĞİŞTİ"
    assert v["e_scope"] == E_SCOPE_COMBINED_DIAGNOSTIC
    # v1.0.0 eksikte tarihsel davranışını KORUR (ACCEPT + blockers), ABSTAIN üretmez.
    s2 = f36_snapshot()
    s2["portfolio_open_risk_usdt"] = None
    s2["same_direction_open"] = None
    assert challenger_e(s2, CFG10, risk_budget_usdt=6.0)["decision"] == ACCEPT
    # Snapshot'ın kendi sürümü yönlendirir: v1.1 config verilse bile v1.0.0 satırı v1.0.0 ile okunur.
    ev = evaluate_trade(snapshot=s, close={"close_event_id": "c", "trade_id": "F00036",
                                           "r_multiple": -1.0, "net_pnl": -1.0,
                                           "closed_at": "2026-09-08T00:00:00+00:00"},
                        cfg=CFG11, risk_budget_usdt=6.0)
    assert ev["entry_policy_version_evaluated"] == ENTRY_POLICY_V1_0
    assert ev["e_scope"] == E_SCOPE_COMBINED_DIAGNOSTIC
    assert ev["families"][FAM_HEAT]["evidence"] == F36_RECORDED_E_EVIDENCE


# ============================================================ 14-15: rapor yolu tutarlılığı

def test_14_entry_time_and_close_attribution_e_are_byte_identical_for_v11_snapshot():
    s = snap(fut_risk=4.850588, same_fut=10, budget=6.0)
    entry_e = AE.point_in_time_ae(s, CFG11)["families"]["E"]
    close = {"close_event_id": "c1", "trade_id": "T1", "r_multiple": 2.0, "net_pnl": 2.0,
             "closed_at": "2026-09-09T00:00:00+00:00", "symbol": "ONDO/USDT"}
    rep = evaluate_trade(snapshot=s, close=close, cfg=CFG11, risk_budget_usdt=5.87)  # canlı equity!
    close_e = rep["families"][FAM_HEAT]
    assert rep["entry_policy_version_evaluated"] == ENTRY_POLICY_V1_1
    ev_entry = dict(entry_e["evidence"])
    ev_close = dict(close_e["evidence"])
    # Çağıran bütçesi yalnız KAYIT; karar/kanıt aynı.
    assert ev_entry.pop("caller_risk_budget_usdt") == 6.0   # adaptör snapshotın kendi bütçesini geçirir
    assert ev_close.pop("caller_risk_budget_usdt") == 5.87
    assert json.dumps(ev_entry, sort_keys=True) == json.dumps(ev_close, sort_keys=True)
    assert entry_e["raw_decision"] == close_e["decision"] == VETO
    assert entry_e["raw_reason_codes"] == close_e["reason_codes"] == ["PORTFOLIO_HEAT_HIGH",
                                                                    "DIRECTIONAL_CONCENTRATION"]


@pytest.mark.parametrize("live_budget", [0.5, 3.0, 6.0, 9.0, 60.0, None])
def test_15_close_time_live_equity_cannot_change_frozen_e(live_budget):
    s = snap(fut_risk=4.0, same_fut=2, budget=6.0)              # 0.667 → ACCEPT
    rep = evaluate_trade(snapshot=s, close={"close_event_id": "c", "trade_id": "T",
                                            "r_multiple": 1.0, "net_pnl": 1.0,
                                            "closed_at": "2026-09-09T00:00:00+00:00"},
                         cfg=CFG11, risk_budget_usdt=live_budget)
    e = rep["families"][FAM_HEAT]
    assert e["decision"] == ACCEPT
    assert e["evidence"]["futures_heat_fraction"] == pytest.approx(4.0 / 6.0, abs=1e-6)
    assert e["evidence"]["risk_budget_usdt"] == 6.0


# ============================================================ 12-13: F00036 dokunulmaz

def _f36_decision_event(cfg: PX.ExperimentConfig) -> dict:
    """VPS'teki kayıtlı P1 kararının yapısal eşdeğeri (kanıt alanları pin'li)."""
    payload = {"policy": "P1_SELECTIVE_AE", "trade_id": "F00036", "candidate_id": "4c7bed8f3bb17655",
               "symbol": "ONDO/USDT", "side": "LONG", "as_of": TS, "opened_at": TS,
               "ae_source": PX.AE_SOURCE_POINT_IN_TIME, "applied": False, "decision": "FILTER",
               "reason_codes": ["ENTRY_FAMILY_A_OR_E_VETO", "DIRECTIONAL_CONCENTRATION",
                                "ENTRY_FAMILY_A_VETO", "ENTRY_FAMILY_E_VETO",
                                "PORTFOLIO_HEAT_HIGH", "P_WIN_BELOW_BREAKEVEN"],
               "evidence": {"A": "FILTER", "E": "FILTER",
                            "legs": {"E": {"evidence": F36_RECORDED_E_EVIDENCE}}}}
    return PX.make_event(cfg, "P1_SELECTIVE_AE", EV_DECISION, payload, "F00036")


def test_12_13_f00036_stored_decision_untouched_by_reevaluation_under_every_definition(tmp_path: Path):
    c11 = PX.ExperimentConfig.from_dict(dict(experiment_id="pfexp_v1_1", policy_version="pfexp_v1.1.0",
                                             evaluation_start_at="2026-09-05T22:22:03+00:00",
                                             frozen_at="2026-09-05T22:22:03+00:00", code_sha="7b6b4e9"))
    assert c11.config_id == "5d3549e72a2049ff"
    st = ExperimentStore(tmp_path, experiment_id="pfexp_v1_1")
    ev = _f36_decision_event(c11)
    assert st.append(ev) is True
    before = hashlib.sha256(st.events_path.read_bytes()).hexdigest()
    s = f36_snapshot()
    # Denetlenen her tanım: (1) yalnız futures 4.850588, (2) projeksiyon 5.989209, (3) birleşik 13.0766.
    for fut in (4.850588, 5.989209):
        s_alt = dict(s) | {"portfolio_futures_stop_risk_usdt": fut, "same_direction_open_futures": 10,
                           "policy_version": ENTRY_POLICY_V1_1}
        assert challenger_e(s_alt, CFG11)["decision"] == VETO
    assert challenger_e(s, CFG10, risk_budget_usdt=6.0)["decision"] == VETO
    AE.point_in_time_ae(s, CFG10)
    evaluate_all(s, CFG10, risk_budget_usdt=6.0)
    # Hiçbir değerlendirme olay YAZMAZ; aynı olay tekrar eklenemez (idempotent).
    assert hashlib.sha256(st.events_path.read_bytes()).hexdigest() == before
    assert st.append(_f36_decision_event(c11)) is False and st.duplicates == 1
    got = [e for e in st.iter_events() if e["kind"] == EV_DECISION]
    assert len(got) == 1 and got[0]["payload"]["decision"] == "FILTER"
    assert got[0]["payload"]["evidence"]["legs"]["E"]["evidence"] == F36_RECORDED_E_EVIDENCE
    assert got[0]["policy_version"] == "pfexp_v1.1.0", "v1.1 kanıtı v1.2 olarak YENİDEN ETİKETLENEMEZ"


# ============================================================ sürüm yönlendirme

def test_config_for_snapshot_routes_by_snapshot_version_only_for_known_versions():
    assert CFG11.for_snapshot({"policy_version": ENTRY_POLICY_V1_0}).policy_version == ENTRY_POLICY_V1_0
    assert CFG10.for_snapshot({"policy_version": ENTRY_POLICY_V1_1}).policy_version == ENTRY_POLICY_V1_1
    assert CFG11.for_snapshot({"policy_version": None}).policy_version == ENTRY_POLICY_V1_1
    assert CFG11.for_snapshot({"policy_version": "entry_v9.9.9"}).policy_version == ENTRY_POLICY_V1_1
    assert CFG11.for_snapshot(None) is CFG11
    # Eşikler sürümler arasında AYNI; yalnız kimlik/kapsam etiketi değişir.
    a, b = CFG10.to_dict(), CFG11.to_dict()
    a.pop("policy_version"), b.pop("policy_version")
    assert a == b and CFG10.config_id != CFG11.config_id


def test_pit_adapter_requires_matching_entry_policy_version():
    s = snap(version=ENTRY_POLICY_V1_1)
    ok = AE.point_in_time_ae(s, CFG11, required_entry_policy_version=ENTRY_POLICY_V1_1)
    assert ok["available"] is True and ok["provenance"]["e_scope"] == E_SCOPE_FUTURES_BUCKET
    old = AE.point_in_time_ae(f36_snapshot(), CFG11, required_entry_policy_version=ENTRY_POLICY_V1_1)
    assert old["available"] is False
    assert old["families"]["E"]["reason_codes"] == [AE.R_ENTRY_POLICY_MISMATCH]
    wrong_cfg = AE.point_in_time_ae(s, CFG10, required_entry_policy_version=ENTRY_POLICY_V1_1)
    assert wrong_cfg["available"] is False
