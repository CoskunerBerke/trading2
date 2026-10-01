"""pfexp_v1_2 (kapsam-eşli E) + pfexp_v1_1 KABUL-KAPALI DRAIN — sürümleme ve izolasyon paketi.

17. pfexp_v1 dosyaları değişmez.
18. pfexp_v1_1 mevcut olayları (karar/dolum) aynen kalır; drain yalnız EKLER (mark/kapanış).
19. pfexp_v1_2 başlangıç öncesi her pozisyonu (F00036 dâhil) reddeder.
20. Eski sürüm olayı v1.2 kitabına giremez.
21. v1.1, v1.2 başlangıcından sonra yeni kabul almaz.
22. Drain yalnız MEVCUT F00036 pozisyonlarına mark/çıkış uygular.
23. Drain yeni v1.1 dolum/pozisyon üretemez (yapısal + ikinci kilit).
24. v1.1 ancak her simüle pozisyon kapanınca COMPLETE olur.
25. P2 mantığı bayt bayt aynı (kaynak sha pin).
26. P0/P3 giriş aynalama bayt bayt aynı (kaynak sha pin + davranış).
27. P1/P4 düzeltilmiş E'yi YALNIZ pfexp_v1.2'de kullanır.
28. Sağlayıcı/API çağrısı yok. 29. Kanonik mutasyon yok. 30. Açık/kapalı → kanonik aynı.
31. ACTIVE / PAPER_BOUNDED / auto-promotion / v1 ve v1.1'i aktif başlatma → RED.
+   Drain kimliği birebir kurulamazsa KURULMAZ (salt okunur), izolasyon gevşetilmez.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import types
from pathlib import Path

import pytest

from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn import profitability_experiment as PX
from tradingbot.learn.entry_challenger import (E_SCOPE_FUTURES_BUCKET, ENTRY_POLICY_V1_0,
                                               ENTRY_POLICY_V1_1, EntryChallengerConfig)
from tradingbot.learn.entry_snapshot import EntrySnapshotStore, build_entry_snapshot
from tradingbot.learn.profitability_experiment import (ACCEPT, FILTER, P0, P1, P2, P3, P4,
                                                       POLICIES, ExperimentConfig)
from tradingbot.learn.profitability_store import (BOOKS_FILE, EVENTS_FILE, REPORT_FILE,
                                                  EV_CLOSE, EV_DECISION, EV_MARK, EV_OPEN,
                                                  ExperimentStore, legacy_v11_summary)
from tests.test_profitability_experiment_v1_1_versioning import seed_v1

V1_START = "2026-09-05T06:31:03+00:00"
V11_START = "2026-09-05T22:22:03+00:00"          # VPS'te donmuş gerçek v1.1 başlangıcı
V12_START = "2026-09-08T00:00:00+00:00"
F36_OPENED = "2026-09-07T02:43:40+00:00"         # v1.1 içinde, v1.2 ÖNCESİ
NEW_OPENED = "2026-09-08T01:00:00+00:00"         # v1.2 SONRASI
ECFG11 = EntryChallengerConfig(policy_version=ENTRY_POLICY_V1_1)
P2_SOURCE_SHA = "e41a9d09a8af4ada1d83a9d2868de2f679c208ab9670d1a604a81b48e904b066"
OPEN_SIM_SOURCE_SHA = "5ede0714df60a38050b3511bb0045d06bc596f5fea915d23e7bad437898eb405"


def v11() -> ExperimentConfig:
    c = ExperimentConfig.from_dict(dict(experiment_id="pfexp_v1_1", policy_version="pfexp_v1.1.0",
                                        evaluation_start_at=V11_START, frozen_at=V11_START,
                                        code_sha="7b6b4e9"))
    assert c.config_id == "5d3549e72a2049ff"       # VPS ile BİREBİR
    return c


def v12(**kw) -> ExperimentConfig:
    base = dict(experiment_id="pfexp_v1_2", policy_version="pfexp_v1.2.0",
                evaluation_start_at=V12_START, frozen_at=V12_START, code_sha="v12sha")
    base.update(kw)
    return ExperimentConfig.from_dict(base)


def snap_for(symbol: str, *, version=ENTRY_POLICY_V1_1, fut_risk=1.0, same_fut=1, budget=6.0,
             ts: str = NEW_OPENED, cycle="90", p_win=0.5, edge=0.5) -> dict:
    from tradingbot.core import from_iso
    chief = {"allow": True, "open_positions": 12, "total_open_risk_usdt": 13.076588,
             "same_direction_open": 11, "risk_budget_usdt": budget,
             "futures_stop_risk_usdt": fut_risk, "same_direction_open_futures": same_fut}
    dec = {"p_win": p_win, "consensus_score": 0.4, "regime": "TREND_UP", "dissent": [], "vetoes": []}
    opp = {"conservative_net_edge_r": edge, "avg_win_r": 2.0, "avg_loss_r": -1.0, "sample_size": 12}
    return build_entry_snapshot(run_id="r", cycle_id=cycle, symbol=symbol, direction="LONG",
                                decision=dec, plan={"entry": 100.0, "stop": 95.0, "targets": [110.0]},
                                opportunity=opp, chief_permission=chief, baseline_accepted=True,
                                policy_version=version, code_sha="x", config_hash="y",
                                now=from_iso(ts))


def _pos(tid, opened, sym):
    return types.SimpleNamespace(to_dict=lambda: {
        "id": tid, "symbol": sym, "side": "LONG", "entry_avg": 100.0, "initial_stop": 95.0,
        "qty": 1.0, "targets": [110.0], "leverage": 3, "entry_fee": 0.01, "slippage_cost": 0.02,
        "opened_at": opened})


def seed_v11(state: Path) -> dict:
    """VPS v1.1 yerleşiminin sadık kopyası: F00036 P0/P2/P3 ACCEPT+open, P1/P4 FILTER; kimlik dosyası."""
    from datetime import datetime, timezone
    state.mkdir(parents=True, exist_ok=True)
    c = v11()
    st = ExperimentStore(state, experiment_id="pfexp_v1_1")
    ident = st.freeze_identity(now=datetime(2026, 9, 5, 22, 22, 3, tzinfo=timezone.utc),
                               code_sha="7b6b4e9")
    assert ident["evaluation_start_at"] == V11_START
    books = {p: PX.PolicyBook(p) for p in POLICIES}
    cd = {"trade_id": "F00036", "symbol": "ONDO/USDT", "side": "LONG", "entry": 0.39, "qty": 52.307,
          "initial_stop": 0.36823195984508783, "targets": [0.4311360803098243, 0.4521041204647365],
          "risk_usdt": 1.1386208763829917, "leverage": 3, "entry_fee": 0.010199865,
          "slippage_cost": 0.0418456, "opened_at": F36_OPENED, "as_of": F36_OPENED,
          "champion_accepted": True, "candidate_id": "4c7bed8f3bb17655", "returns_1h": None,
          "entry_ae": {"families": {"A": {"decision": "FILTER", "reason_codes": ["ENTRY_FAMILY_A_VETO"]},
                                    "E": {"decision": "FILTER", "reason_codes": ["ENTRY_FAMILY_E_VETO"]}},
                       "provenance": {"source": "ENTRY_SNAPSHOT", "stage": "RANKING",
                                      "sees_outcome": False, "close_history_read": False},
                       "as_of": F36_OPENED}}
    for p in POLICIES:
        d = PX.decide_entry(p, cd, books[p], c)
        st.append(PX.make_event(c, p, EV_DECISION, d, "F00036"))
        if d["decision"] == ACCEPT:
            books[p].n_accept += 1
            pos = PX.open_simulated(books[p], cd, c)
            st.append(PX.make_event(c, p, EV_OPEN, {"position": pos.to_dict()}, "F00036"))
        else:
            books[p].n_filter += 1
    assert st.save_books(books, c)["ok"]
    dec = [e for e in st.iter_events() if e["kind"] == EV_DECISION]
    assert {e["policy"]: e["payload"]["decision"] for e in dec} == {P0: ACCEPT, P1: FILTER, P2: ACCEPT,
                                                                   P3: ACCEPT, P4: FILTER}
    return {"decision_sha": hashlib.sha256("\n".join(json.dumps(e, sort_keys=True) for e in dec).encode()).hexdigest(),
            "n_events": len(list(st.iter_events())),
            "events_bytes": st.events_path.read_bytes()}


def _engine(state: Path, *, cfg=None, positions=None, history=None, snaps=(), path_rows=None,
            entry_cfg=ECFG11) -> types.SimpleNamespace:
    cfg = cfg or v12()
    est = EntrySnapshotStore(state / "entry_snapshot.jsonl", max_per_cycle=50)
    for s, tid in snaps:
        est.append(s)
        est.link_trade(s["candidate_id"], tid)
    eng = types.SimpleNamespace(
        entry_snapshot_store=est, entry_cfg=entry_cfg, runner=types.SimpleNamespace(last_frames={}),
        experiment_cfg=cfg, experiment_store=ExperimentStore(state, experiment_id=cfg.experiment_id),
        experiment_mode="SHADOW", exit_policy_cfg=None,
        path_store=(types.SimpleNamespace(iter_rows=lambda: list(path_rows or [])) if path_rows else None),
        run_id="r", code_sha=lambda: cfg.code_sha, config_hash=lambda: "cfg",
        cfg=types.SimpleNamespace(state_path=state),
        ledger2=types.SimpleNamespace(positions=dict(positions or {}), history=list(history or [])),
        _experiment_recent_decisions=TradingEngineV3._experiment_recent_decisions,
        _futures_bucket_context=TradingEngineV3._futures_bucket_context)
    for name in ("_experiment_candidates", "_experiment_closes", "_experiment_pre_count",
                 "_run_profitability_experiment", "_experiment_superseded_versions",
                 "_run_experiment_cycle", "_run_experiment_drain", "_setup_experiment_drain"):
        setattr(eng, name, (lambda n: (lambda *a, **k: getattr(TradingEngineV3, n)(eng, *a, **k)))(name))
    eng._setup_experiment_drain(state, cfg)                 # gerçek drain kurulumu
    return eng


def _shas(state: Path) -> dict:
    return {n: hashlib.sha256((state / n).read_bytes()).hexdigest()
            for n in (EVENTS_FILE, BOOKS_FILE, REPORT_FILE)}


def _v11_decision_sha(state: Path) -> tuple[str, int]:
    st = ExperimentStore(state, experiment_id="pfexp_v1_1")
    dec = [e for e in st.iter_events() if e["kind"] == EV_DECISION]
    return (hashlib.sha256("\n".join(json.dumps(e, sort_keys=True) for e in dec).encode()).hexdigest(),
            len(dec))


# ============================================================ kimlik

def test_identity_pins_and_entry_policy_mapping():
    c1 = ExperimentConfig.from_dict(dict(experiment_id="pfexp_v1", policy_version="pfexp_v1.0.0",
                                         evaluation_start_at=V1_START))
    assert c1.config_id == "e3863761d50c79c3" and v11().config_id == "5d3549e72a2049ff"
    c12 = v12()
    assert c12.config_id not in (c1.config_id, v11().config_id)
    assert c1.entry_policy_version == v11().entry_policy_version == ENTRY_POLICY_V1_0
    assert c12.entry_policy_version == ENTRY_POLICY_V1_1 and c12.e_scope == E_SCOPE_FUTURES_BUCKET
    assert ExperimentConfig().experiment_id == "pfexp_v1_2"
    ident = c12.identity()
    assert ident["entry_policy_version"] == ENTRY_POLICY_V1_1 and ident["e_scope"] == E_SCOPE_FUTURES_BUCKET
    # Eşikler v1.1 ile AYNI (P2 limitleri / terfi kapıları).
    a, b = v11().to_dict(), c12.to_dict()
    for k in ("experiment_id", "policy_version", "evaluation_start_at", "frozen_at", "code_sha"):
        a.pop(k), b.pop(k)
    assert a == b


# ============================================================ 17-20: koruma / dışlama

def test_17_18_19_v1_and_v11_evidence_untouched_and_f00036_excluded_from_v12(tmp_path: Path):
    v1_before = seed_v1(tmp_path)
    s11 = seed_v11(tmp_path)
    marks = [{"trade_id": "F00036", "mark": 0.40, "ts": "2026-09-08T02:00:00+00:00", "snapshot_id": "m1"},
             {"trade_id": "F00036", "mark": 0.41, "ts": "2026-09-08T03:00:00+00:00", "snapshot_id": "m2"}]
    eng = _engine(tmp_path, positions={"ONDO/USDT": _pos("F00036", F36_OPENED, "ONDO/USDT")},
                  snaps=[(snap_for("ONDO/USDT", ts=F36_OPENED, cycle="85"), "F00036")],
                  path_rows=marks)
    assert eng.experiment_drain is not None, eng._experiment_drain_state
    for _ in range(3):
        eng._run_profitability_experiment(None)
    assert _shas(tmp_path) == v1_before, "pfexp_v1 DEĞİŞTİ"
    sha, n = _v11_decision_sha(tmp_path)
    assert (sha, n) == (s11["decision_sha"], 5), "v1.1 KARAR olayları değişti"
    ev11 = list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_1").iter_events())
    assert ev11[:s11["n_events"]] == [json.loads(ln) for ln in s11["events_bytes"].decode().splitlines()]
    kinds = {(e["policy"], e["kind"]) for e in ev11[s11["n_events"]:]}
    assert kinds <= {(P0, EV_MARK), (P2, EV_MARK), (P3, EV_MARK)}, kinds     # yalnız EKLENDİ, yalnız mark
    # v1.2: F00036 ön-deney, hiç olay yok.
    st12 = ExperimentStore(tmp_path, experiment_id="pfexp_v1_2")
    assert list(st12.iter_events()) == []
    rep = json.loads((tmp_path / "profitability_experiment_v1_2.json").read_text(encoding="utf-8"))
    assert rep["status"] == PX.STATUS_ACTIVE and rep["admissions_open"] is True
    assert rep["entry_policy_version"] == ENTRY_POLICY_V1_1
    pre = rep["pre_experiment_excluded"]
    assert [r["trade_id"] for r in pre["open_ids"]] == ["F00036"]
    assert pre["open_ids"][0]["label"] == "PRE_EXPERIMENT_OBSERVATION_ONLY"
    sv = {s["experiment_id"]: s for s in rep["superseded_versions"]}
    assert sv["pfexp_v1"]["status"] == "SUPERSEDED_INCOMPLETE_ENTRY_INPUT"
    assert sv["pfexp_v1_1"]["status"] == PX.STATUS_V11_DRAINING
    assert sv["pfexp_v1_1"]["admissions_open"] is False
    assert sv["pfexp_v1_1"]["admissions_closed_at"] == V12_START
    assert sv["pfexp_v1_1"]["superseded_by"] == "pfexp_v1_2"
    assert sv["pfexp_v1_1"]["n_decision_events"] == 5 and sv["pfexp_v1_1"]["decision_events_sha256"] == sha
    f36 = sv["pfexp_v1_1"]["trades"]["F00036"]["policies"]
    assert {p: f36[p]["decision"] for p in POLICIES} == {P0: ACCEPT, P1: FILTER, P2: ACCEPT, P3: ACCEPT, P4: FILTER}
    assert sv["pfexp_v1_1"]["coverage_defect"]["numerator_scope"] == "COMBINED_SPOT_FUTURES_DIAGNOSTIC"
    assert sv["pfexp_v1_1"]["coverage_defect"]["denominator_scope"] == "FUTURES_STOP_RISK_BUCKET"
    rep11 = json.loads((tmp_path / "profitability_experiment_v1_1.json").read_text(encoding="utf-8"))
    assert rep11["status"] == PX.STATUS_V11_DRAINING and rep11["admissions_open"] is False
    assert rep11["drain"]["open_sim_positions"] == {P0: ["F00036"], P1: [], P2: ["F00036"], P3: ["F00036"], P4: []}
    assert rep11["drain"]["rejected_admission_events"] == 0 and rep11["profitability_conclusion"] is None
    assert rep11["experiment_id"] == "pfexp_v1_1" and rep11["config_id"] == "5d3549e72a2049ff"


def test_20_old_version_events_cannot_enter_v12_books(tmp_path: Path):
    c12 = v12()
    st = ExperimentStore(tmp_path, experiment_id="pfexp_v1_2")
    for old in (ExperimentConfig.from_dict(dict(experiment_id="pfexp_v1", policy_version="pfexp_v1.0.0",
                                                evaluation_start_at=V1_START)), v11()):
        ev = PX.make_event(old, P0, EV_DECISION, {"decision": ACCEPT, "trade_id": "F00036"}, "F00036")
        assert st.append(ev) is False
        st.dir.mkdir(parents=True, exist_ok=True)
        with open(st.events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")                # elle sızdırma
    st._ids = None
    books = st.replay(c12)
    assert st.foreign_events == 2 and all(b.n_accept == 0 and not b.positions for b in books.values())
    got, meta = st.load_books(c12)
    assert meta["source"] == "REPLAY" and all(not b.positions for b in got.values())


# ============================================================ 21-24: drain

def _drain_world(tmp_path: Path, *, with_new_trade: bool, close_f36: bool = False, path_rows=None):
    seed_v1(tmp_path)
    s11 = seed_v11(tmp_path)
    positions = {"ONDO/USDT": _pos("F00036", F36_OPENED, "ONDO/USDT")}
    history = []
    if close_f36:
        positions = {}
        history = [{"id": "F00036", "symbol": "ONDO/USDT", "side": "LONG", "opened_at": F36_OPENED,
                    "closed_at": "2026-09-08T05:00:00+00:00", "exit_reason": "stop", "net_pnl": -1.2,
                    "r_multiple": -1.05, "fees": 0.02, "funding": 0.0, "exit_avg": 0.36823195984508783}]
    snaps = [(snap_for("ONDO/USDT", ts=F36_OPENED, cycle="85"), "F00036")]
    if with_new_trade:
        positions["NEAR/USDT"] = _pos("F00040", NEW_OPENED, "NEAR/USDT")
        snaps.append((snap_for("NEAR/USDT", ts=NEW_OPENED, cycle="90"), "F00040"))
    eng = _engine(tmp_path, positions=positions, history=history, snaps=snaps, path_rows=path_rows)
    assert eng.experiment_drain is not None
    return eng, s11


def test_21_23_v11_accepts_no_new_admissions_after_v12_start_and_drain_creates_no_fill(tmp_path: Path):
    marks = [{"trade_id": "F00036", "mark": 0.40, "ts": "2026-09-08T02:00:00+00:00", "snapshot_id": "a"},
             {"trade_id": "F00040", "mark": 101.0, "ts": "2026-09-08T02:00:00+00:00", "snapshot_id": "b"}]
    eng, s11 = _drain_world(tmp_path, with_new_trade=True, path_rows=marks)
    for _ in range(2):
        eng._run_profitability_experiment(None)
    ev11 = list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_1").iter_events())
    assert not [e for e in ev11 if "F00040" in json.dumps(e)], "v1.2-sonrası işlem v1.1'e GİRDİ"
    assert len([e for e in ev11 if e["kind"] == EV_DECISION]) == 5           # yeni karar YOK
    assert len([e for e in ev11 if e["kind"] == EV_OPEN]) == 3               # yeni dolum YOK
    bk11 = json.loads((tmp_path / "profitability_experiment_v1_1_books.json").read_text(encoding="utf-8"))
    assert all(sorted(b["positions"]) in ([], ["F00036"]) for b in bk11["books"].values())
    # 22: marklar yalnız F00036 (v1.1) ve yalnız F00040 (v1.2)
    assert {(e["payload"]["trade_id"]) for e in ev11 if e["kind"] == EV_MARK} == {"F00036"}
    ev12 = list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_2").iter_events())
    assert {e["payload"]["trade_id"] for e in ev12 if e["kind"] == EV_MARK} <= {"F00040"}
    dec12 = {e["policy"]: e["payload"] for e in ev12 if e["kind"] == EV_DECISION}
    assert set(dec12) == set(POLICIES) and all(d["trade_id"] == "F00040" for d in dec12.values())
    assert not [e for e in ev12 if "F00036" in json.dumps(e)]
    # 27: P1/P4 düzeltilmiş E'yi v1.2'de kullanır (scope + entry policy v1.1.0)
    p1 = dec12[P1]
    assert p1["ae_source"] == PX.AE_SOURCE_POINT_IN_TIME
    assert p1["evidence"]["entry_policy_version"] == ENTRY_POLICY_V1_1
    e_leg = p1["evidence"]["legs"]["E"]
    assert e_leg["evidence"]["scope"] == E_SCOPE_FUTURES_BUCKET
    assert e_leg["evidence"]["risk_budget_source"] == "ENTRY_SNAPSHOT_FROZEN"
    assert e_leg["evidence"]["futures_heat_fraction"] == pytest.approx(1.0 / 6.0, abs=1e-6)
    assert p1["decision"] == ACCEPT and dec12[P4]["decision"] == ACCEPT
    # 23 ikinci kilit: kabul kapalıyken sızmış karar/dolum olayı deftere GİREMEZ ve SAYILIR.
    d = eng.experiment_drain
    captured = {}
    orig = d["store"].append_many
    d["store"].append_many = lambda evs: captured.setdefault("kinds", [e["kind"] for e in evs]) and orig(evs) or orig([])
    eng._experiment_candidates = lambda c: [{"trade_id": "F00099", "symbol": "X/USDT", "side": "LONG",
                                            "entry": 1.0, "qty": 1.0, "initial_stop": 0.9, "targets": [],
                                            "risk_usdt": 0.1, "opened_at": NEW_OPENED, "as_of": NEW_OPENED,
                                            "champion_accepted": True, "candidate_id": "zz",
                                            "entry_ae": None, "returns_1h": None}]
    eng._run_experiment_drain(None)
    assert EV_DECISION not in captured.get("kinds", []) and EV_OPEN not in captured.get("kinds", [])
    ev11b = list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_1").iter_events())
    assert not [e for e in ev11b if "F00099" in json.dumps(e)]
    # 12: F00036 karar olayları bayt bayt aynı
    assert _v11_decision_sha(tmp_path) == (s11["decision_sha"], 5)
    # v1.2 raporunda son kararların kapsam alanları
    rep = json.loads((tmp_path / "profitability_experiment_v1_2.json").read_text(encoding="utf-8"))
    rd = rep["recent_entry_decisions"][-1]
    assert rd["trade_id"] == "F00040"
    assert rd["e_scope_fields"]["scope"] == E_SCOPE_FUTURES_BUCKET
    for k in ("futures_stop_risk_usdt", "futures_risk_budget_usdt", "futures_heat_fraction",
              "combined_diagnostic_exposure_usdt", "non_futures_component_usdt",
              "same_direction_open_futures", "same_direction_open_combined"):
        assert k in rd["e_scope_fields"], k
    assert rd["e_scope_fields"]["combined_diagnostic_exposure_usdt"] == pytest.approx(13.076588)


def test_24_v11_completes_only_after_every_sim_position_closes(tmp_path: Path):
    eng, s11 = _drain_world(tmp_path, with_new_trade=False, close_f36=False)
    eng._run_profitability_experiment(None)
    rep11 = json.loads((tmp_path / "profitability_experiment_v1_1.json").read_text(encoding="utf-8"))
    assert rep11["status"] == PX.STATUS_V11_DRAINING and rep11["drain"]["complete"] is False
    # Kanonik F00036 kapanışı gelir (v1.1 içinde açıldı, v1.2 sonrası kapandı) → v1.1 aynalar.
    eng.ledger2.positions = {}
    eng.ledger2.history = [{"id": "F00036", "symbol": "ONDO/USDT", "side": "LONG", "opened_at": F36_OPENED,
                            "closed_at": "2026-09-08T05:00:00+00:00", "exit_reason": "stop", "net_pnl": -1.2,
                            "r_multiple": -1.05, "fees": 0.02, "funding": 0.0, "exit_avg": 0.36823195984508783}]
    for _ in range(2):
        eng._run_profitability_experiment(None)
    ev11 = list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_1").iter_events())
    closes = [e for e in ev11 if e["kind"] == EV_CLOSE]
    assert {e["policy"] for e in closes} == {P0, P2, P3} and len(closes) == 3
    assert all(e["payload"]["close"]["trade_id"] == "F00036" and e["payload"]["close"]["exit_kind"] == PX.X_CANONICAL
               for e in closes)
    rep11 = json.loads((tmp_path / "profitability_experiment_v1_1.json").read_text(encoding="utf-8"))
    assert rep11["status"] == PX.STATUS_V11_COMPLETE and rep11["drain"]["complete"] is True
    assert rep11["drain"]["open_sim_positions"] == {p: [] for p in POLICIES}
    assert rep11["policies"][P0]["closed"] == 1 and rep11["policies"][P1]["closed"] == 0
    # v1.2: F00036 kapanışı v1.2 kitabına GİRMEZ (ön-deney), sayaç açıkça raporlanır.
    rep12 = json.loads((tmp_path / "profitability_experiment_v1_2.json").read_text(encoding="utf-8"))
    assert rep12["pre_experiment_excluded"]["closed_after_start_still_excluded"] == 1
    assert list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_2").iter_events()) == []
    assert _v11_decision_sha(tmp_path) == (s11["decision_sha"], 5)
    sv = {s["experiment_id"]: s for s in rep12["superseded_versions"]}
    assert sv["pfexp_v1_1"]["status"] == PX.STATUS_V11_COMPLETE


# ============================================================ drain fail-closed

def test_drain_is_not_set_up_when_identity_cannot_be_reconstructed(tmp_path: Path):
    # (a) kimlik dosyası YOK → salt okunur
    seed_v11(tmp_path)
    (tmp_path / "profitability_experiment_v1_1_identity.json").unlink()
    eng = _engine(tmp_path, positions={"ONDO/USDT": _pos("F00036", F36_OPENED, "ONDO/USDT")})
    assert eng.experiment_drain is None
    assert eng._experiment_drain_state == {"status": PX.STATUS_V11_READ_ONLY, "reason": "NO_V11_IDENTITY_FILE"}
    eng._run_profitability_experiment(None)
    rep = json.loads((tmp_path / "profitability_experiment_v1_2.json").read_text(encoding="utf-8"))
    sv = {s["experiment_id"]: s for s in rep["superseded_versions"]}
    assert sv["pfexp_v1_1"]["status"] == PX.STATUS_V11_READ_ONLY and sv["pfexp_v1_1"]["drain"]["enabled"] is False
    assert sv["pfexp_v1_1"]["sim_open_by_policy"][P0] == ["F00036"]     # dondurulmuş açık kalır
    ev11 = list(ExperimentStore(tmp_path, experiment_id="pfexp_v1_1").iter_events())
    assert len(ev11) == 8, "salt okunur modda v1.1'e olay EKLENDİ"
    # (b) kitap config_id uyuşmuyor → salt okunur
    d2 = tmp_path / "b"
    seed_v11(d2)
    bp = d2 / "profitability_experiment_v1_1_books.json"
    doc = json.loads(bp.read_text(encoding="utf-8"))
    doc["config_id"] = "deadbeefdeadbeef"
    bp.write_text(json.dumps(doc), encoding="utf-8")
    eng2 = _engine(d2)
    assert eng2.experiment_drain is None and eng2._experiment_drain_state["reason"].startswith("CONFIG_ID_MISMATCH")
    # (c) yabancı olay → salt okunur
    d3 = tmp_path / "c"
    seed_v11(d3)
    with open(d3 / "profitability_experiment_v1_1_events.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(PX.make_event(v12(), P0, EV_MARK, {"trade_id": "F00036", "mark": 1}, "x")) + "\n")
    eng3 = _engine(d3)
    assert eng3.experiment_drain is None and eng3._experiment_drain_state["reason"] == "FOREIGN_EVENTS:1"
    # (d) v1.1 hiç yoksa drain yok, sessizce (kurulmamış)
    d4 = tmp_path / "d"
    d4.mkdir()
    eng4 = _engine(d4)
    assert eng4.experiment_drain is None and eng4._experiment_drain_state["reason"] == "NO_V11_STATE"
    assert legacy_v11_summary(d4, drain_enabled=False, superseded_by="x", admissions_closed_at="y") is None


# ============================================================ 25-26: P2 / P0-P3 bayt bayt

def test_25_p2_logic_is_byte_identical():
    src = inspect.getsource(PX.decide_p2)
    assert hashlib.sha256(src.encode("utf-8")).hexdigest() == P2_SOURCE_SHA, "P2 mantığı DEĞİŞTİ"
    # Davranış: boş simüle defter → tek pozisyon payı 1.0 kısıt sayılmaz → ACCEPT.
    b = PX.PolicyBook(P2)
    d = PX.decide_p2({"risk_usdt": 1.1386, "side": "LONG", "returns_1h": None}, b, v12())
    assert d["decision"] == ACCEPT and d["evidence"]["n_open"] == 0 and d["evidence"]["same_direction_share"] == 1.0


def test_26_p0_p3_entry_mirroring_is_byte_identical():
    src = inspect.getsource(PX.open_simulated)
    assert hashlib.sha256(src.encode("utf-8")).hexdigest() == OPEN_SIM_SOURCE_SHA, "aynalama DEĞİŞTİ"
    c = v12()
    cd = {"trade_id": "T", "symbol": "NEAR/USDT", "side": "LONG", "entry": 100.0, "qty": 1.0,
          "initial_stop": 95.0, "targets": [110.0], "risk_usdt": 5.0, "leverage": 3, "entry_fee": 0.01,
          "slippage_cost": 0.02, "opened_at": NEW_OPENED, "as_of": NEW_OPENED, "champion_accepted": True,
          "candidate_id": "c", "entry_ae": None, "returns_1h": None}
    for p in (P0, P3):
        d = PX.decide_entry(p, cd, PX.PolicyBook(p), c)
        assert d["decision"] == ACCEPT and d["reason_codes"] == [PX.R_MIRROR]
    a = PX.open_simulated(PX.PolicyBook(P0), cd, c).to_dict()
    b = PX.open_simulated(PX.PolicyBook(P3), cd, c).to_dict()
    a.pop("policy"), b.pop("policy")
    assert a == b and a["entry"] == 100.0 and a["qty"] == 1.0 and a["initial_stop"] == 95.0


# ============================================================ 27: yalnız v1.2'de düzeltilmiş E

def test_27_corrected_e_only_in_v12_and_old_snapshot_not_reinterpreted(tmp_path: Path):
    # v1.2 + entry_v1.0.0 ile yazılmış snapshot → yeniden yorumlanmaz, P1/P4 ABSTAIN (mismatch).
    old_snap = snap_for("NEAR/USDT", version=ENTRY_POLICY_V1_0)
    eng = _engine(tmp_path, positions={"NEAR/USDT": _pos("F00040", NEW_OPENED, "NEAR/USDT")},
                  snaps=[(old_snap, "F00040")])
    cands = eng._experiment_candidates(eng.experiment_cfg)
    ae = cands[0]["entry_ae"]
    assert ae["available"] is False
    assert ae["families"]["E"]["reason_codes"] == ["ENTRY_SNAPSHOT_POLICY_VERSION_MISMATCH"]
    d = PX.decide_entry(P1, cands[0], PX.PolicyBook(P1), eng.experiment_cfg)
    assert d["decision"] == "ABSTAIN"
    # v1.1 cfg ile (tarihsel) aynı motor yolu entry_v1.0.0 ister; entry_v1.1.0 snapshot'ı reddeder.
    eng11 = _engine(tmp_path / "b", cfg=v11(), positions={"NEAR/USDT": _pos("F00040", NEW_OPENED, "NEAR/USDT")},
                    snaps=[(snap_for("NEAR/USDT", version=ENTRY_POLICY_V1_1), "F00040")])
    c11 = eng11._experiment_candidates(eng11.experiment_cfg)
    assert c11[0]["entry_ae"]["families"]["E"]["reason_codes"] == ["ENTRY_SNAPSHOT_POLICY_VERSION_MISMATCH"]


# ============================================================ 28-31

def test_28_no_provider_or_api_calls_in_experiment_or_drain_code():
    for fn in (TradingEngineV3._run_experiment_cycle, TradingEngineV3._run_experiment_drain,
               TradingEngineV3._setup_experiment_drain, TradingEngineV3._experiment_candidates,
               TradingEngineV3._experiment_recent_decisions, TradingEngineV3._futures_bucket_context):
        src = inspect.getsource(fn)
        for bad in ("fetch", "ccxt", "requests", "httpx", "exchange", "urlopen", "klines", "socket"):
            assert bad not in src, f"{fn.__name__}: {bad}"


def test_29_30_experiment_and_drain_never_mutate_canonical_and_on_off_identical(tmp_path: Path):
    def run(enabled: bool, d: Path):
        seed_v1(d)
        seed_v11(d)
        eng = _engine(d, positions={"ONDO/USDT": _pos("F00036", F36_OPENED, "ONDO/USDT"),
                                    "NEAR/USDT": _pos("F00040", NEW_OPENED, "NEAR/USDT")},
                      snaps=[(snap_for("NEAR/USDT"), "F00040")],
                      path_rows=[{"trade_id": "F00036", "mark": 0.4, "ts": "2026-09-08T02:00:00+00:00", "snapshot_id": "a"}])
        if not enabled:
            eng.experiment_cfg = None
            eng.experiment_store = None
            eng.experiment_drain = None
        snap_before = (d / "entry_snapshot.jsonl").read_bytes()
        pos_before = json.dumps({k: v.to_dict() for k, v in eng.ledger2.positions.items()}, sort_keys=True)
        eng._run_profitability_experiment(None)
        assert (d / "entry_snapshot.jsonl").read_bytes() == snap_before
        assert json.dumps({k: v.to_dict() for k, v in eng.ledger2.positions.items()}, sort_keys=True) == pos_before
        return pos_before, json.dumps(eng.ledger2.history, sort_keys=True, default=str)

    assert run(True, tmp_path / "on") == run(False, tmp_path / "off")
    assert (tmp_path / "on" / "profitability_experiment_v1_2.json").exists()
    assert not (tmp_path / "off" / "profitability_experiment_v1_2.json").exists()
    on11 = len(list(ExperimentStore(tmp_path / "on", experiment_id="pfexp_v1_1").iter_events()))
    off11 = len(list(ExperimentStore(tmp_path / "off", experiment_id="pfexp_v1_1").iter_events()))
    assert on11 > 8 and off11 == 8                        # drain yalnız açıkken EKLER


def test_31_active_paper_bounded_auto_promotion_and_superseded_ids_rejected():
    import yaml

    from tradingbot.config_v3 import ConfigError, load_v3
    raw = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    for k, v, code in (("experiment_mode", "ACTIVE", "NOT_ACTIVATED"),
                       ("experiment_mode", "PAPER_BOUNDED", "NOT_ACTIVATED"),
                       ("experiment_auto_promotion", True, "AUTO_PROMOTION_FORBIDDEN")):
        r = dict(raw)
        r["entry_selectivity"] = dict(r.get("entry_selectivity") or {}) | {k: v}
        with pytest.raises(ConfigError, match=code):
            load_v3(r)
    pol = TradingEngineV3._experiment_identity_policy
    for xp in ({"experiment_id": "pfexp_v1"}, {"policy_version": "pfexp_v1.0.0"},
               {"experiment_id": "pfexp_v1_1"}, {"policy_version": "pfexp_v1.1.0"}):
        with pytest.raises(ValueError, match="SUPERSEDED"):
            pol(xp)
    v3 = load_v3(dict(raw))
    en = v3.entry_selectivity
    assert en.policy_version == ENTRY_POLICY_V1_1 and en.experiment_mode == "SHADOW"
    c = ExperimentConfig.from_dict(pol(dict(en.experiment_policy or {})))
    assert (c.experiment_id, c.policy_version, c.entry_policy_version) == ("pfexp_v1_2", "pfexp_v1.2.0", ENTRY_POLICY_V1_1)
