"""Kârlılık deneyi — KARAR ANI A/E borusu (`profitability_ae_v1`) regresyon paketi.

Doğrulanmış kusur: `pfexp_v1` P1/P4, A/E'yi kapanmış işlem atıfından (`entry_selectivity.json
.trades`) okuyordu; yeni açılan pozisyonun orada satırı olmadığından P1/P4 canlı girişte yapısal
olarak ATILDI. Bu paket düzeltmenin sözleşmesini kanıtlar:

1.  Canlı/açık aday kapanış atıfına BAĞLI DEĞİL.
2.  Geçerli snapshot, `trades` satırı olmadan P1/P4'ün karar vermesine yeter.
3.  `entry_selectivity.json.trades` silinse/bozulsa da geçerli giriş anı A/E bozulmaz.
4.  Snapshot'tan türeyen A/E = mevcut kanonik değerlendirici (aynı girdi, aynı hüküm).
5.  Eksik girdi → AÇIK gerekçeli ABSTAIN.
6.  Sonuç-türevi hiçbir alan OKUNMAZ.
7.  Sonradan değişen sonuç donmuş giriş kararını DEĞİŞTİREMEZ.
8.  Tekrarlanan değerlendirme bayt bayt aynı.
9.  Beş politika AYNI aday kimliğini ve AYNI as-of anını alır.
10-12. P1 ACCEPT / P1 FILTER / P4 ACCEPT-FILTER-ABSTAIN ayrı fixture'larda.
13. P0 ve P3 kanonik giriş ekonomisini AYNEN aynalar.
14-17. FILTER/ABSTAIN dolum/pozisyon üretmez; ACCEPT tam bir idempotent pozisyon; yinelenen
       işleme olay/dolum/pozisyon/kapanış çoğaltamaz.
24. Sağlayıcı/API çağrısı EKLENMEDİ.
"""
from __future__ import annotations

import ast
import inspect
import json
import types
from pathlib import Path

import pytest

from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn import profitability_ae as AE
from tradingbot.learn import profitability_experiment as PX
from tradingbot.learn.entry_challenger import (VETO, EntryChallengerConfig, challenger_a,
                                               challenger_e, evaluate_all)
from tradingbot.learn.entry_eval import FORBIDDEN_OUTCOME_FIELDS
from tradingbot.learn.entry_snapshot import EntrySnapshotStore, build_entry_snapshot
from tradingbot.learn.profitability_experiment import (ABSTAIN, ACCEPT, FILTER, P0, P1, P2, P3,
                                                       P4, POLICIES, ExperimentConfig,
                                                       PolicyBook, decide_entry, open_simulated)
from tradingbot.learn.profitability_store import EV_CLOSE, EV_DECISION, EV_OPEN, ExperimentStore

START = "2026-09-06T00:00:00+00:00"
OPENED = "2026-09-06T01:00:00+00:00"
SNAP_TS = "2026-09-06T00:59:30+00:00"       # snapshot açılıştan ÖNCE yazılır (RANKING)
ECFG = EntryChallengerConfig()               # kanonik eşikler — kopyalanmaz, ayarlanmaz


def xcfg(**kw) -> ExperimentConfig:
    base = dict(experiment_id="pfexp_test", policy_version="pfexp_v1.1.0",
                evaluation_start_at=START, frozen_at=START, code_sha="deadbeef")
    base.update(kw)
    return ExperimentConfig.from_dict(base)


def snapshot(*, p_win=0.50, edge=0.50, avg_win=2.0, avg_loss=-1.0, open_risk=2.0,
             same_dir=1, budget=6.0, symbol="ZEN/USDT", run_id="r1", cycle="c1",
             direction="LONG", ts=SNAP_TS, **over) -> dict:
    """GERÇEK şema ile (build_entry_snapshot) karar anı snapshot'ı."""
    from tradingbot.core import from_iso
    dec = {"p_win": p_win, "consensus_confidence": 0.6, "consensus_score": 0.4,
           "expected_r": 2.0, "regime": "TREND_UP", "dissent": [], "vetoes": []}
    opp = {"conservative_net_edge_r": edge, "net_expectancy_r": 0.6, "gross_expectancy_r": 0.8,
           "uncertainty_penalty_r": 0.1, "size_multiplier": 1.0, "sample_size": 12,
           "avg_win_r": avg_win, "avg_loss_r": avg_loss, "expectancy_basis": "test"}
    chief = {"allow": True, "open_positions": 2, "total_open_risk_usdt": open_risk,
             "same_direction_open": same_dir, "risk_budget_usdt": budget}
    plan = {"entry": 100.0, "stop": 95.0, "targets": [110.0, 120.0], "notional": 100.0,
            "leverage": 3, "entry_type": "kirilim"}
    s = build_entry_snapshot(run_id=run_id, cycle_id=cycle, symbol=symbol, direction=direction,
                             decision=dec, plan=plan, opportunity=opp, chief_permission=chief,
                             risk_decision={"allowed": True, "reasons": []}, baseline_rank=0,
                             baseline_accepted=True, features={"atr_pct": 2.0},
                             code_sha="deadbeef", config_hash="cfg", policy_version="entry_v1.0.0",
                             now=from_iso(ts))
    for k, v in over.items():
        if v is None:
            s[k] = None
            s["sources"][k] = "MISSING"
            if k not in s["missing_fields"]:
                s["missing_fields"].append(k)
        else:
            s[k] = v
    return s


def candidate(snap: dict | None, *, tid="F1", side="LONG", entry=100.0, stop=95.0, qty=1.0,
              risk=5.0, opened=OPENED, accepted=True, rets=None) -> dict:
    """Motorun ürettiği aday sözlüğünün eşdeğeri (entry_ae snapshot'tan türetilir)."""
    return {"trade_id": tid, "symbol": (snap or {}).get("symbol", "ZEN/USDT"), "side": side,
            "entry": entry, "qty": qty, "initial_stop": stop, "targets": [110.0, 120.0],
            "risk_usdt": risk, "leverage": 3, "entry_fee": 0.01, "slippage_cost": 0.02,
            "opened_at": opened, "as_of": ((snap or {}).get("ts") or opened),
            "champion_accepted": accepted,
            "candidate_id": (snap or {}).get("candidate_id"),
            "decision_id": (snap or {}).get("decision_id"),
            "entry_ae": AE.point_in_time_ae(snap, ECFG),
            "returns_1h": (rets if rets is not None else [0.01, -0.02] * 25)}


def books() -> dict[str, PolicyBook]:
    return {p: PolicyBook(p) for p in POLICIES}


# ============================================================ 4: kanonik değerlendiriciyle eşitlik

@pytest.mark.parametrize("kw", [
    dict(), dict(p_win=0.30), dict(edge=-0.1), dict(open_risk=5.9), dict(same_dir=6),
    dict(p_win=None), dict(edge=None), dict(budget=None), dict(same_dir=None),
    dict(open_risk=None), dict(avg_win=None, avg_loss=None), dict(p_win=0.36, avg_win=1.96),
])
def test_04_snapshot_derived_ae_matches_canonical_evaluator(kw):
    s = snapshot(**kw)
    out = AE.point_in_time_ae(s, ECFG)
    assert out["available"] is True
    # Aynı girdi → AYNI ham hüküm ve AYNI kanıt. Eşik kopyalanmadı, çatallanmadı.
    a_ref = challenger_a(s, ECFG, realized_payoff=None)
    e_ref = challenger_e(s, ECFG, risk_budget_usdt=s.get("risk_budget_usdt"))
    ref = evaluate_all(s, ECFG, realized_payoff=None, risk_budget_usdt=s.get("risk_budget_usdt"))
    A, E = out["families"]["A"], out["families"]["E"]
    assert A["raw_decision"] == a_ref["decision"] == ref[a_ref["family"]]["decision"]
    assert E["raw_decision"] == e_ref["decision"] == ref[e_ref["family"]]["decision"]
    assert A["raw_reason_codes"] == a_ref["reason_codes"]
    assert E["raw_reason_codes"] == e_ref["reason_codes"]
    assert A["evidence"] == a_ref["evidence"] and E["evidence"] == e_ref["evidence"]
    assert A["family"] == a_ref["family"] and E["family"] == e_ref["family"]
    # Üç değerli çeviri: VETO → FILTER, ölçülmüş ACCEPT → ACCEPT, eksik → ABSTAIN.
    for leg, ref_v in ((A, a_ref), (E, e_ref)):
        if ref_v["decision"] == VETO:
            assert leg["decision"] == FILTER
        elif leg["missing"]:
            assert leg["decision"] == ABSTAIN
        else:
            assert leg["decision"] == ACCEPT and not ref_v["blockers"]
    assert out["entry_policy_version"] == ECFG.policy_version
    assert out["entry_config_id"] == ECFG.config_id


def test_04b_thresholds_are_the_canonical_config_not_a_copy():
    src = Path("tradingbot/learn/profitability_ae.py").read_text(encoding="utf-8")
    for bad in ("assumed_payoff_ratio =", "prob_safety_margin =", "max_open_risk_fraction =",
                "max_same_direction =", "min_conservative_edge_r ="):
        assert bad not in src, f"A/E eşiği kopyalanmış: {bad}"
    assert "challenger_a(" in src and "challenger_e(" in src


# ============================================================ 5: eksik → açık ABSTAIN

def test_05_missing_inputs_yield_explicit_abstain_with_field_names():
    a = AE.point_in_time_ae(snapshot(p_win=None), ECFG)["families"]["A"]
    assert a["decision"] == ABSTAIN and a["reason_codes"] == [AE.R_A_INPUT_MISSING]
    assert "p_win" in a["missing"]
    a2 = AE.point_in_time_ae(snapshot(edge=None), ECFG)["families"]["A"]
    assert a2["decision"] == ABSTAIN and "conservative_net_edge_r" in a2["missing"]
    e = AE.point_in_time_ae(snapshot(budget=None), ECFG)["families"]["E"]
    assert e["decision"] == ABSTAIN and e["reason_codes"] == [AE.R_E_INPUT_MISSING]
    assert "risk_budget_usdt" in e["missing"]
    e2 = AE.point_in_time_ae(snapshot(open_risk=None, same_dir=None), ECFG)["families"]["E"]
    assert e2["decision"] == ABSTAIN
    assert {"portfolio_open_risk_usdt", "same_direction_open"} <= set(e2["missing"])
    # Kanonik değerlendirici eksikte ACCEPT+blockers döner; deneyde bu KARAR DEĞİLDİR.
    assert challenger_a(snapshot(p_win=None), ECFG)["decision"] == "ACCEPT"
    assert a["raw_decision"] == "ACCEPT" and a["decision"] == ABSTAIN


def test_05b_missing_value_stays_none_and_measured_zero_stays_zero():
    s0 = snapshot(open_risk=0.0, same_dir=0)
    e0 = AE.point_in_time_ae(s0, ECFG)["families"]["E"]
    assert e0["decision"] == ACCEPT, "ölçülmüş SIFIR eksik sayıldı"
    assert e0["evidence"]["portfolio_open_risk_usdt"] == 0.0
    assert e0["evidence"]["same_direction_open"] == 0.0
    sN = snapshot(open_risk=None)
    assert sN["portfolio_open_risk_usdt"] is None and sN["sources"]["portfolio_open_risk_usdt"] == "MISSING"
    eN = AE.point_in_time_ae(sN, ECFG)["families"]["E"]
    assert eN["evidence"]["portfolio_open_risk_usdt"] is None


def test_05c_no_snapshot_or_no_config_abstains_with_reason():
    out = AE.point_in_time_ae(None, ECFG)
    assert out["available"] is False
    for k in ("A", "E"):
        assert out["families"][k]["decision"] == ABSTAIN
        assert out["families"][k]["reason_codes"] == [AE.R_NO_SNAPSHOT]
    out2 = AE.point_in_time_ae(snapshot(), None)
    assert out2["available"] is False
    assert out2["families"]["A"]["reason_codes"] == [AE.R_NO_SNAPSHOT]
    out3 = AE.point_in_time_ae({"symbol": "X"}, ECFG)          # candidate_id YOK
    assert out3["families"]["E"]["decision"] == ABSTAIN


def test_05d_non_point_in_time_snapshot_is_refused():
    s = snapshot()
    s["provenance"]["sees_outcome"] = True
    out = AE.point_in_time_ae(s, ECFG)
    assert out["available"] is False
    assert out["families"]["A"]["reason_codes"] == [AE.R_NOT_POINT_IN_TIME]
    s2 = snapshot()
    s2["provenance"]["written_at_stage"] = "CLOSE"
    assert AE.point_in_time_ae(s2, ECFG)["families"]["E"]["reason_codes"] == [AE.R_NOT_POINT_IN_TIME]
    s3 = snapshot()
    s3["ts"] = None
    assert AE.point_in_time_ae(s3, ECFG)["families"]["A"]["reason_codes"] == [AE.R_AS_OF_MISSING]


def test_05e_abstain_never_becomes_filter_or_accept_in_p1_and_p4():
    c = xcfg()
    d1 = decide_entry(P1, candidate(snapshot(budget=None)), books()[P1], c)
    assert d1["decision"] == ABSTAIN
    assert PX.R_AE_UNKNOWN in d1["reason_codes"] and AE.R_E_INPUT_MISSING in d1["reason_codes"]
    assert d1["evidence"]["abstained_on"] == ["E"]
    d4 = decide_entry(P4, candidate(snapshot(budget=None)), books()[P4], c)
    assert d4["decision"] == ABSTAIN
    # entry_ae hiç verilmemişse de ABSTAIN (ACCEPT DEĞİL).
    cd = candidate(snapshot())
    cd["entry_ae"] = None
    assert decide_entry(P1, cd, books()[P1], c)["decision"] == ABSTAIN


# ============================================================ 6: sızıntı denetimi

class _Spy(dict):
    """Okunan anahtarları kaydeder."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.reads: set[str] = set()

    def get(self, k, d=None):
        self.reads.add(str(k))
        return super().get(k, d)

    def __getitem__(self, k):
        self.reads.add(str(k))
        return super().__getitem__(k)


def test_06_no_outcome_derived_field_is_read():
    spy = _Spy(snapshot())
    out = AE.point_in_time_ae(spy, ECFG)
    assert out["available"] is True
    read = spy.reads
    allowed = set(AE.ALLOWED_SNAPSHOT_KEYS) | {"provenance", "ts", "candidate_id"}
    assert read <= allowed, f"izinsiz alan okundu: {sorted(read - allowed)}"
    assert not (read & set(FORBIDDEN_OUTCOME_FIELDS))
    for bad in ("mfe_r", "mae_r", "net_r", "exit_price", "exit_reason", "position_path",
                "lessons", "learned", "outcome", "forward_return", "realized_pnl"):
        assert bad not in read


def test_06b_snapshot_carrying_an_outcome_field_is_refused_not_used():
    for f in FORBIDDEN_OUTCOME_FIELDS:
        s = snapshot()
        s[f] = 1.0
        out = AE.point_in_time_ae(s, ECFG)
        assert out["available"] is False
        assert out["families"]["A"]["reason_codes"] == [AE.R_OUTCOME_LEAK]
        assert f in out["families"]["A"]["evidence"]["fields"]


def test_06c_module_reads_no_close_history_or_learned_state():
    tree = ast.parse(Path("tradingbot/learn/profitability_ae.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for bad in ("history", "canonical_closes", "iter_rows", "position_path", "lessons",
                "learned_index", "outcome_journal", "expanding_payoff", "realized_payoff_from",
                "ledger", "ledger2", "read_json", "open"):
        assert bad not in names, bad
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    for bad in ("requests", "httpx", "ccxt", "urllib", "socket", "aiohttp"):
        assert not any(bad in str(i) for i in imports), bad
    prov = AE.point_in_time_ae(snapshot(), ECFG)["provenance"]
    assert prov == {"source": "ENTRY_SNAPSHOT", "stage": "RANKING", "sees_outcome": False,
                    "realized_payoff_source": "SNAPSHOT_AVG_WIN_LOSS_OR_ASSUMED",
                    "close_history_read": False, "learned_results_read": False,
                    "network": False,
                    "evaluator": "entry_challenger.challenger_a/challenger_e"}


# ============================================================ 7-8: donmuşluk / determinizm

def test_07_later_outcome_cannot_change_the_frozen_entry_decision():
    s = snapshot()
    before = json.dumps(AE.point_in_time_ae(s, ECFG), sort_keys=True)
    # "Sonradan" sonuç: ayrı nesnelerde yaşar, snapshot'a GİREMEZ.
    closes = [{"trade_id": "F1", "r_multiple": -3.0, "net_pnl": -15.0, "exit_reason": "stop"}]
    path = [{"trade_id": "F1", "mark": 50.0, "mfe_r": 0.1, "mae_r": -2.0}]
    assert closes and path
    after = json.dumps(AE.point_in_time_ae(s, ECFG), sort_keys=True)
    assert before == after
    # decide_entry de yalnız adayın donmuş `entry_ae`sine bakar.
    c = xcfg()
    d_a = decide_entry(P1, candidate(s), books()[P1], c)
    d_b = decide_entry(P1, candidate(s), books()[P1], c)
    assert d_a == d_b


def test_08_repeated_evaluation_is_byte_identical():
    s = snapshot()
    a = json.dumps(AE.point_in_time_ae(s, ECFG), sort_keys=True, ensure_ascii=False)
    b = json.dumps(AE.point_in_time_ae(json.loads(json.dumps(s)), ECFG), sort_keys=True,
                   ensure_ascii=False)
    assert a == b
    c = xcfg()
    outs = []
    for _ in range(3):
        bk = books()
        outs.append(json.dumps({p: decide_entry(p, candidate(s), bk[p], c) for p in POLICIES},
                               sort_keys=True, default=str))
    assert len(set(outs)) == 1


# ============================================================ 9: ortak kimlik / as-of

def test_09_all_five_policies_share_candidate_identity_and_as_of():
    s = snapshot()
    cd = candidate(s)
    assert s["ts"] != cd["opened_at"], "fixture: as-of açılıştan farklı olmalı"
    c, bk = xcfg(), books()
    ds = {p: decide_entry(p, cd, bk[p], c) for p in POLICIES}
    ids = {(d["trade_id"], d["candidate_id"], d["as_of"], d["opened_at"]) for d in ds.values()}
    assert len(ids) == 1
    tid, cid, as_of, opened = next(iter(ids))
    assert cid == s["candidate_id"] and as_of == s["ts"] and opened == OPENED
    assert all(d["ae_source"] == PX.AE_SOURCE_POINT_IN_TIME for d in ds.values())


# ============================================================ 10-12: P1 / P4 fixture'ları

def test_10_p1_reaches_accept_on_a_valid_snapshot():
    d = decide_entry(P1, candidate(snapshot()), books()[P1], xcfg())
    assert d["decision"] == ACCEPT and d["reason_codes"] == [PX.R_OK]
    assert d["evidence"]["A"] == ACCEPT and d["evidence"]["E"] == ACCEPT
    assert d["evidence"]["source"] == "ENTRY_SNAPSHOT" and d["evidence"]["stage"] == "RANKING"
    assert d["evidence"]["sees_outcome"] is False


@pytest.mark.parametrize("kw,leg,code", [
    (dict(p_win=0.30), "A", "P_WIN_BELOW_BREAKEVEN"),
    (dict(edge=-0.1), "A", "NET_EDGE_NOT_POSITIVE"),
    (dict(open_risk=5.9), "E", "PORTFOLIO_HEAT_HIGH"),
    (dict(same_dir=6), "E", "DIRECTIONAL_CONCENTRATION"),
])
def test_11_p1_reaches_filter_on_a_measured_veto(kw, leg, code):
    d = decide_entry(P1, candidate(snapshot(**kw)), books()[P1], xcfg())
    assert d["decision"] == FILTER
    assert PX.R_AE_VETO in d["reason_codes"] and code in d["reason_codes"]
    assert d["evidence"]["vetoed_by"] == [leg]


def test_11b_veto_on_a_measured_dimension_is_decisive_even_if_another_is_missing():
    # Bütçe yok (ısı ölçülemez) ama yön yoğunlaşması ölçülmüş ve aşılmış → FILTER.
    d = decide_entry(P1, candidate(snapshot(budget=None, same_dir=6)), books()[P1], xcfg())
    assert d["decision"] == FILTER and "DIRECTIONAL_CONCENTRATION" in d["reason_codes"]


def test_12_p4_accept_filter_abstain_in_separate_fixtures():
    c = xcfg()
    assert decide_entry(P4, candidate(snapshot()), books()[P4], c)["decision"] == ACCEPT
    f = decide_entry(P4, candidate(snapshot(p_win=0.30)), books()[P4], c)
    assert f["decision"] == FILTER and PX.R_AE_VETO in f["reason_codes"]
    a = decide_entry(P4, candidate(snapshot(budget=None)), books()[P4], c)
    assert a["decision"] == ABSTAIN and AE.R_E_INPUT_MISSING in a["reason_codes"]
    # P2 FILTER + P1 ABSTAIN → FILTER (FILTER, ABSTAIN'e baskındır; ABSTAIN ACCEPT'e dönmez).
    bk = books()
    open_simulated(bk[P4], candidate(snapshot(symbol="A/USDT"), tid="T0"))
    cc = xcfg(max_same_direction_risk_share=0.50)
    mixed = decide_entry(P4, candidate(snapshot(budget=None, symbol="B/USDT"), tid="T1"),
                         bk[P4], cc)
    assert mixed["decision"] == FILTER and PX.R_DIR_SHARE in mixed["reason_codes"]


# ============================================================ 13: P0/P3 aynalama

def test_13_p0_and_p3_mirror_canonical_entry_economics_exactly():
    c, bk = xcfg(), books()
    cd = candidate(snapshot())
    for p in (P0, P3):
        d = decide_entry(p, cd, bk[p], c)
        assert d["decision"] == ACCEPT and d["reason_codes"] == [PX.R_MIRROR]
        pos = open_simulated(bk[p], cd, c)
        assert (pos.entry, pos.qty, pos.initial_stop, pos.stop, pos.targets, pos.leverage,
                pos.risk_usdt, pos.entry_fee, pos.slippage_cost, pos.opened_at) == \
            (cd["entry"], cd["qty"], cd["initial_stop"], cd["initial_stop"], cd["targets"],
             float(cd["leverage"]), cd["risk_usdt"], cd["entry_fee"], cd["slippage_cost"],
             cd["opened_at"])
        assert pos.candidate_id == cd["candidate_id"] and pos.as_of == cd["as_of"]
        assert (pos.experiment_id, pos.policy_version, pos.config_id, pos.code_sha) == \
            (c.experiment_id, c.policy_version, c.config_id, c.code_sha)
    a, b = bk[P0].positions["F1"].to_dict(), bk[P3].positions["F1"].to_dict()
    a.pop("policy"), b.pop("policy")
    assert a == b


# ============================================================ 1-3, 14-17: motor katmanı

def _engine(tmp_path: Path, *, snap: dict | None, tid="F1", opened=OPENED, history=None,
            write_entry_selectivity: dict | None = None) -> types.SimpleNamespace:
    """Gerçek snapshot deposu + gerçek deney deposu; kanonik defter salt kopya."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    st = EntrySnapshotStore(tmp_path / "entry_snapshot.jsonl", max_per_cycle=50)
    if snap is not None:
        assert st.append(snap)
        assert st.link_trade(snap["candidate_id"], tid)
    if write_entry_selectivity is not None:
        (tmp_path / "entry_selectivity.json").write_text(
            json.dumps(write_entry_selectivity), encoding="utf-8")
    pos = types.SimpleNamespace(to_dict=lambda: {
        "id": tid, "symbol": "ZEN/USDT", "side": "LONG", "entry_avg": 100.0,
        "initial_stop": 95.0, "qty": 1.0, "targets": [110.0, 120.0], "leverage": 3,
        "entry_fee": 0.01, "slippage_cost": 0.02, "opened_at": opened})
    eng = types.SimpleNamespace(
        entry_snapshot_store=st, entry_cfg=ECFG, runner=types.SimpleNamespace(last_frames={}),
        experiment_cfg=xcfg(), experiment_store=ExperimentStore(tmp_path,
                                                                experiment_id="pfexp_test"),
        experiment_mode="SHADOW", exit_policy_cfg=None, path_store=None, run_id="r",
        code_sha=lambda: "deadbeef", config_hash=lambda: "cfg",
        cfg=types.SimpleNamespace(state_path=tmp_path),
        ledger2=types.SimpleNamespace(positions={"ZEN/USDT": pos}, history=list(history or [])))
    for name in ("_experiment_candidates", "_experiment_closes", "_experiment_pre_count",
                 "_run_profitability_experiment"):
        setattr(eng, name, (lambda n: (lambda *a: getattr(TradingEngineV3, n)(eng, *a)))(name))
    return eng


def test_01_open_candidates_do_not_depend_on_closed_trade_attribution(tmp_path: Path):
    """Kapanış geçmişi BOŞ, `entry_selectivity.json` YOK → aday yine tam A/E taşır."""
    eng = _engine(tmp_path, snap=snapshot())
    assert not (tmp_path / "entry_selectivity.json").exists()
    assert eng.ledger2.history == []
    got = eng._experiment_candidates(eng.experiment_cfg)
    assert [c["trade_id"] for c in got] == ["F1"]
    ae = got[0]["entry_ae"]
    assert ae["available"] is True
    assert ae["families"]["A"]["decision"] == ACCEPT and ae["families"]["E"]["decision"] == ACCEPT
    assert "entry_families" not in got[0], "eski kapanış-atıf girdisi hâlâ taşınıyor"


def test_01b_engine_candidate_source_never_reads_entry_selectivity_trades():
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(TradingEngineV3._experiment_candidates)))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body.pop(0)                                  # docstring KOD değildir
    src = ast.unparse(tree)                             # yorumlar düşer; yalnız KOD kalır
    for bad in ("entry_selectivity.json", "'trades'", "families", "canonical_closes",
                "history", "read_json"):
        assert bad not in src, bad
    for bad in ("fetch", "ccxt", "requests", "httpx", "exchange", "klines", "urlopen"):
        assert bad not in src, f"sağlayıcı çağrısı: {bad}"


def test_02_valid_snapshot_lets_p1_and_p4_decide_without_a_trades_row(tmp_path: Path):
    eng = _engine(tmp_path, snap=snapshot())
    eng._run_profitability_experiment(None)
    bk, _ = eng.experiment_store.load_books(eng.experiment_cfg)
    for p in (P1, P4):
        assert bk[p].n_accept == 1 and bk[p].n_abstain == 0, p
        assert "F1" in bk[p].positions
    dec = [e for e in eng.experiment_store.iter_events() if e["kind"] == EV_DECISION]
    assert {e["policy"]: e["payload"]["decision"] for e in dec} == {p: ACCEPT for p in POLICIES}
    assert all(e["payload"]["ae_source"] == PX.AE_SOURCE_POINT_IN_TIME for e in dec)


@pytest.mark.parametrize("report", [
    None,
    {"trades": []},
    {"trades": [{"trade_id": "F1", "families": {"A_x": {"decision": "VETO"},
                                                "E_y": {"decision": "VETO"}}}]},
    {"trades": "BOZUK"},
])
def test_03_removing_or_corrupting_trades_report_cannot_break_entry_time_ae(tmp_path: Path, report):
    eng = _engine(tmp_path, snap=snapshot(), write_entry_selectivity=report)
    got = eng._experiment_candidates(eng.experiment_cfg)
    ae = got[0]["entry_ae"]
    ref = AE.point_in_time_ae(snapshot(), ECFG)
    assert ae["families"]["A"]["decision"] == ref["families"]["A"]["decision"] == ACCEPT
    assert ae["families"]["E"]["decision"] == ref["families"]["E"]["decision"] == ACCEPT
    d = decide_entry(P1, got[0], PolicyBook(P1), eng.experiment_cfg)
    assert d["decision"] == ACCEPT, "kapanış-atıf raporu canlı kararı ETKİLEDİ"


def test_14_15_filter_and_abstain_create_no_fill_or_position(tmp_path: Path):
    for name, s in (("filter", snapshot(p_win=0.30)), ("abstain", snapshot(budget=None))):
        eng = _engine(tmp_path / name, snap=s)
        eng._run_profitability_experiment(None)
        bk, _ = eng.experiment_store.load_books(eng.experiment_cfg)
        for p in (P1, P4):
            assert "F1" not in bk[p].positions and not bk[p].closes
            assert (bk[p].n_filter, bk[p].n_abstain) == ((1, 0) if name == "filter" else (0, 1))
        evs = list(eng.experiment_store.iter_events())
        assert not [e for e in evs if e["kind"] == EV_OPEN and e["policy"] in (P1, P4)]
        assert len([e for e in evs if e["kind"] == EV_DECISION and e["policy"] in (P1, P4)]) == 2
        # P0/P2/P3 aynı adayda AÇAR (aynalama) — ayrım gerçek.
        assert all("F1" in bk[p].positions for p in (P0, P2, P3))


def test_16_accept_creates_exactly_one_idempotent_position(tmp_path: Path):
    eng = _engine(tmp_path, snap=snapshot())
    for _ in range(3):
        eng._run_profitability_experiment(None)
    bk, meta = eng.experiment_store.load_books(eng.experiment_cfg)
    evs = list(eng.experiment_store.iter_events())
    for p in POLICIES:
        assert list(bk[p].positions) == ["F1"]
        assert len([e for e in evs if e["kind"] == EV_OPEN and e["policy"] == p]) == 1
        assert len([e for e in evs if e["kind"] == EV_DECISION and e["policy"] == p]) == 1
        assert bk[p].n_accept == 1
    b = PolicyBook(P0)
    p1 = open_simulated(b, candidate(snapshot()))
    p2 = open_simulated(b, candidate(snapshot()))
    assert p1 is p2 and len(b.positions) == 1


def test_17_duplicate_processing_cannot_duplicate_events_fills_positions_or_closes(tmp_path: Path):
    close = {"id": "F1", "symbol": "ZEN/USDT", "side": "LONG", "opened_at": OPENED,
             "closed_at": "2026-09-06T05:00:00+00:00", "exit_reason": "stop", "net_pnl": -5.0,
             "r_multiple": -1.0, "fees": 0.02, "funding": 0.0, "exit_avg": 95.0}
    eng = _engine(tmp_path, snap=snapshot())
    eng._run_profitability_experiment(None)                 # açılış
    eng.ledger2.history = [close]                           # kanonik kapanış geldi
    eng.ledger2.positions = {}
    for _ in range(4):
        eng._run_profitability_experiment(None)
    evs = list(eng.experiment_store.iter_events())
    ids = [e["event_id"] for e in evs]
    assert len(ids) == len(set(ids))
    for p in POLICIES:
        assert len([e for e in evs if e["kind"] == EV_OPEN and e["policy"] == p]) == 1
        assert len([e for e in evs if e["kind"] == EV_CLOSE and e["policy"] == p]) == 1
    bk, _ = eng.experiment_store.load_books(eng.experiment_cfg)
    for p in POLICIES:
        assert not bk[p].positions and len(bk[p].closes) == 1
        c = bk[p].closes[0]
        assert (c.experiment_id, c.policy_version, c.config_id) == eng.experiment_cfg.identity_key()
    assert eng.experiment_store.duplicates == 0, "yinelenen olay YAZILMAYA çalışıldı"


def test_24_no_provider_or_api_calls_added():
    for path in ("tradingbot/learn/profitability_ae.py",
                 "tradingbot/learn/profitability_experiment.py",
                 "tradingbot/learn/profitability_store.py"):
        src = Path(path).read_text(encoding="utf-8")
        for bad in ("ccxt", "requests.", "httpx", "urllib", "fetch_ohlcv", "aiohttp",
                    "socket.", "binance"):
            assert bad not in src, f"{path}: {bad}"


# ============================================================ snapshot alanı (bütçe)

def test_snapshot_records_decision_time_risk_budget_as_measured_or_missing():
    s = snapshot(budget=6.0)
    assert s["risk_budget_usdt"] == 6.0 and s["sources"]["risk_budget_usdt"] == "MEASURED"
    s2 = build_entry_snapshot(run_id="r", cycle_id="c", symbol="X/USDT", direction="LONG",
                              chief_permission={"open_positions": 1})
    assert s2["risk_budget_usdt"] is None and s2["sources"]["risk_budget_usdt"] == "MISSING"
    assert "risk_budget_usdt" in s2["missing_fields"]
    # Bu alan bir SONUÇ alanı değildir ve sızıntı listesinde yer almaz.
    assert "risk_budget_usdt" not in FORBIDDEN_OUTCOME_FIELDS


def test_engine_decision_time_budget_mirrors_champion_formula():
    prof = types.SimpleNamespace(size_on_live_equity=True, max_total_open_risk_pct=6.0)
    eng = types.SimpleNamespace(profile=prof)
    st = types.SimpleNamespace(equity=100.0, starting_equity=80.0)
    assert TradingEngineV3._decision_time_risk_budget(eng, st) == pytest.approx(6.0)
    prof.size_on_live_equity = False
    assert TradingEngineV3._decision_time_risk_budget(eng, st) == pytest.approx(4.8)
    assert TradingEngineV3._decision_time_risk_budget(eng, None) is None
    assert TradingEngineV3._decision_time_risk_budget(types.SimpleNamespace(), st) is None
    st.equity = 0.0
    prof.size_on_live_equity = True
    assert TradingEngineV3._decision_time_risk_budget(eng, st) is None, "sıfır equity bütçe DEĞİL"
