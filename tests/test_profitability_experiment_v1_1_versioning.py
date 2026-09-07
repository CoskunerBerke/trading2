"""`pfexp_v1` korunur, `pfexp_v1_1` sıfırdan başlar — sürümleme ve kanıt koruma paketi.

18. Eski sürüm olayı yeni sürüm kitabına GİREMEZ.
19. F00035 (v1 kanıtı) yeni sürüme taşınmaz, geriye dönük doldurulmaz; v1 dosyaları bayt bayt aynı.
20. Başlangıçtan ÖNCE açılan pozisyon, başlangıçtan SONRA kapansa da dışarıda kalır.
21. Kimlik / config / checksum uyuşmazlığı fail-closed (anlık görüntüye güvenilmez; yabancı
    olay reddedilir).
22. Kanonik defter/risk/sermaye/gateway/emir mutasyonu mümkün değil (kaynak seviyesi).
23. Deney açık/kapalı → kanonik kararlar ve snapshot dosyası bayt bayt aynı.
25. ACTIVE / PAPER_BOUNDED / auto-promotion / tarihsel kimlik / config'ten başlangıç → RED.
+   Başlangıç BİR KEZ dondurulur; başka deneyin başlangıcı devralınmaz; geriye çekilemez.
+   Tarihsel özet salt okunurdur ve dürüst statü taşır.
"""
from __future__ import annotations

import ast
import hashlib
import json
import types
from pathlib import Path

import pytest

from tradingbot.engine_v3 import TradingEngineV3
from tradingbot.learn import profitability_experiment as PX
from tradingbot.learn.entry_challenger import EntryChallengerConfig
from tradingbot.learn.entry_snapshot import EntrySnapshotStore
from tradingbot.learn.profitability_experiment import (ABSTAIN, ACCEPT, P0, P1, P2, P3, P4,
                                                       POLICIES, ExperimentConfig, PolicyBook,
                                                       open_simulated)
from tradingbot.learn.profitability_store import (BOOKS_FILE, EVENTS_FILE, IDENTITY_FILE,
                                                  LEGACY_EXPERIMENT_ID, REPORT_FILE,
                                                  EV_CLOSE, EV_DECISION, EV_OPEN,
                                                  ExperimentStore, legacy_v1_summary,
                                                  state_file_names)
from tests.test_profitability_point_in_time_ae_v1 import ECFG, candidate, snapshot

V1_START = "2026-09-05T06:31:03+00:00"
V11_START = "2026-09-06T12:00:00+00:00"
F35_OPENED = "2026-09-05T18:01:32+00:00"       # v1.1 başlangıcından ÖNCE


def v11(**kw) -> ExperimentConfig:
    """pfexp_v1_1 — AÇIKÇA sabitlenir (varsayılan artık pfexp_v1_2)."""
    base = dict(experiment_id="pfexp_v1_1", policy_version="pfexp_v1.1.0",
                evaluation_start_at=V11_START, frozen_at=V11_START, code_sha="newsha")
    base.update(kw)
    return ExperimentConfig.from_dict(base)


def v1() -> ExperimentConfig:
    return ExperimentConfig.from_dict(dict(experiment_id="pfexp_v1", policy_version="pfexp_v1.0.0",
                                           evaluation_start_at=V1_START, frozen_at=V1_START,
                                           code_sha="4f3c417"))


def seed_v1(state: Path) -> dict[str, str]:
    """VPS'teki v1 yerleşiminin sadık kopyası: F00035 için P0/P2/P3 ACCEPT+open, P1/P4 ABSTAIN."""
    state.mkdir(parents=True, exist_ok=True)
    c = v1()
    st = ExperimentStore(state, experiment_id=LEGACY_EXPERIMENT_ID)
    books = {p: PolicyBook(p) for p in POLICIES}
    cd = {"trade_id": "F00035", "symbol": "ZEN/USDT", "side": "LONG", "entry": 7.06,
          "qty": 1.497, "initial_stop": 6.145746696862394,
          "targets": [8.867506606275212, 9.774759909412818], "risk_usdt": 1.36863719,
          "leverage": 3, "entry_fee": 0.0026, "slippage_cost": None, "opened_at": F35_OPENED,
          "champion_accepted": True, "candidate_id": "91707e86fc80c9fc",
          "entry_families": None, "returns_1h": None}
    for p in POLICIES:
        d = PX.decide_entry(p, cd, books[p], c)
        st.append(PX.make_event(c, p, EV_DECISION, d, "F00035"))
        if d["decision"] == ACCEPT:
            books[p].n_accept += 1
            pos = open_simulated(books[p], cd)
            st.append(PX.make_event(c, p, EV_OPEN, {"position": pos.to_dict()}, "F00035"))
        else:
            books[p].n_abstain += 1
    st.save_books(books, c)
    (state / REPORT_FILE).write_text(json.dumps(PX.compare(books, c), default=str),
                                     encoding="utf-8")
    return {n: hashlib.sha256((state / n).read_bytes()).hexdigest()
            for n in (EVENTS_FILE, BOOKS_FILE, REPORT_FILE)}


def _engine(state: Path, *, cfg: ExperimentConfig, positions: dict, history: list,
            snap: dict | None = None, link_tid: str | None = None) -> types.SimpleNamespace:
    est = EntrySnapshotStore(state / "entry_snapshot.jsonl", max_per_cycle=50)
    if snap is not None and link_tid:
        est.append(snap)
        est.link_trade(snap["candidate_id"], link_tid)
    eng = types.SimpleNamespace(
        entry_snapshot_store=est, entry_cfg=ECFG, runner=types.SimpleNamespace(last_frames={}),
        experiment_drain=None, _experiment_drain_state={"status": None, "reason": "TEST"},
        _experiment_recent_decisions=TradingEngineV3._experiment_recent_decisions,
        experiment_cfg=cfg, experiment_store=ExperimentStore(state, experiment_id=cfg.experiment_id),
        experiment_mode="SHADOW", exit_policy_cfg=None, path_store=None, run_id="r",
        code_sha=lambda: cfg.code_sha, config_hash=lambda: "cfg",
        cfg=types.SimpleNamespace(state_path=state),
        ledger2=types.SimpleNamespace(positions=dict(positions), history=list(history)))
    for name in ("_experiment_candidates", "_experiment_closes", "_experiment_pre_count",
                 "_run_profitability_experiment", "_experiment_superseded_versions",
                 "_run_experiment_cycle", "_run_experiment_drain"):
        setattr(eng, name, (lambda n: (lambda *a, **k: getattr(TradingEngineV3, n)(eng, *a, **k)))(name))
    return eng


def _pos(tid, opened, sym="ZEN/USDT"):
    return types.SimpleNamespace(to_dict=lambda: {
        "id": tid, "symbol": sym, "side": "LONG", "entry_avg": 100.0, "initial_stop": 95.0,
        "qty": 1.0, "targets": [110.0], "leverage": 3, "entry_fee": 0.01,
        "slippage_cost": 0.02, "opened_at": opened})


# ============================================================ dosya yerleşimi

def test_versioned_state_layout_keeps_v1_names_and_separates_v1_1():
    old = state_file_names("pfexp_v1")
    assert old == {"events": EVENTS_FILE, "books": BOOKS_FILE, "report": REPORT_FILE,
                   "identity": IDENTITY_FILE}
    new = state_file_names("pfexp_v1_1")
    assert new == {"events": "profitability_experiment_v1_1_events.jsonl",
                   "books": "profitability_experiment_v1_1_books.json",
                   "report": "profitability_experiment_v1_1.json",
                   "identity": "profitability_experiment_v1_1_identity.json"}
    assert not set(old.values()) & set(new.values()), "v1 ve v1.1 dosyaları ÇAKIŞIYOR"
    assert all(v.startswith("profitability_experiment") for v in new.values())
    assert ExperimentConfig().experiment_id == "pfexp_v1_2"
    assert ExperimentConfig().policy_version == "pfexp_v1.2.0"
    assert ExperimentConfig().config_id != v1().config_id != v11().config_id
    assert ExperimentConfig().ae_source == PX.AE_SOURCE_POINT_IN_TIME
    assert v1().ae_source == PX.AE_SOURCE_LEGACY


# ============================================================ 18: eski olay yeni kitaba giremez

def test_18_old_version_events_cannot_enter_new_version_books(tmp_path: Path):
    c11 = v11()
    st = ExperimentStore(tmp_path, experiment_id=c11.experiment_id)
    # (a) append: yabancı deney kimliği REDDEDİLİR.
    old_ev = PX.make_event(v1(), P0, EV_DECISION, {"decision": ACCEPT, "trade_id": "F00035"},
                           "F00035")
    assert st.append(old_ev) is False and st.foreign_events == 1
    # (b) dosyaya elle sızdırılmış eski olay: replay YOK SAYAR ve sayar.
    st.dir.mkdir(parents=True, exist_ok=True)
    with open(st.events_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(old_ev) + "\n")
        fh.write(json.dumps(PX.make_event(v1(), P0, EV_OPEN,
                                          {"position": {"trade_id": "F00035", "policy": P0,
                                                        "symbol": "ZEN/USDT", "side": "LONG",
                                                        "qty": 1.0, "entry": 7.0,
                                                        "initial_stop": 6.0, "stop": 6.0,
                                                        "targets": [], "leverage": 3,
                                                        "risk_usdt": 1.0,
                                                        "opened_at": F35_OPENED,
                                                        "entry_fee": 0.0,
                                                        "slippage_cost": None,
                                                        "slippage_provenance": "MISSING",
                                                        "candidate_id": "c"}},
                                          "F00035")) + "\n")
    st._ids = None
    books = st.replay(c11)
    assert st.foreign_events == 2
    assert all(not b.positions and b.n_accept == 0 for b in books.values())
    # (c) aynı experiment_id ama farklı policy_version/config_id de YABANCIDIR.
    other = v11(policy_version="pfexp_v1.2.0")
    st2 = ExperimentStore(tmp_path / "b", experiment_id=c11.experiment_id)
    assert st2.append(PX.make_event(other, P0, EV_DECISION, {"decision": ACCEPT}, "T")) is True
    assert st2.replay(c11)[P0].n_accept == 0 and st2.foreign_events == 1
    assert st2.replay(other)[P0].n_accept == 1


# ============================================================ 19: F00035 taşınmaz

def test_19_f00035_is_not_migrated_or_backfilled_and_v1_files_are_untouched(tmp_path: Path):
    before = seed_v1(tmp_path)
    c11 = v11()
    # Yeni koşuda F00035 hâlâ AÇIK ve v1.1 başlangıcından ÖNCE açılmış → ön-deney.
    eng = _engine(tmp_path, cfg=c11, positions={"ZEN/USDT": _pos("F00035", F35_OPENED)},
                  history=[], snap=snapshot(symbol="ZEN/USDT"), link_tid="F00035")
    for _ in range(2):
        eng._run_profitability_experiment(None)
    # v1 dosyaları BAYT BAYT aynı.
    after = {n: hashlib.sha256((tmp_path / n).read_bytes()).hexdigest()
             for n in (EVENTS_FILE, BOOKS_FILE, REPORT_FILE)}
    assert after == before, "v1 kanıtı DEĞİŞTİ"
    # v1.1 defterinde F00035 YOK; hiç olay yok (sıfırdan başlar).
    st11 = eng.experiment_store
    evs = list(st11.iter_events())
    assert not [e for e in evs if "F00035" in json.dumps(e)]
    bk, meta = st11.load_books(c11)
    assert all(not b.positions and not b.closes for b in bk.values())
    assert all(b.n_accept == b.n_filter == b.n_abstain == 0 for b in bk.values())
    rep = json.loads((tmp_path / "profitability_experiment_v1_1.json").read_text(encoding="utf-8"))
    assert rep["experiment_id"] == "pfexp_v1_1" and rep["policy_version"] == "pfexp_v1.1.0"
    assert rep["evaluation_start_at"] == V11_START
    pre = rep["pre_experiment_excluded"]
    assert pre["open"] == 1 and pre["open_ids"][0]["trade_id"] == "F00035"
    assert pre["open_ids"][0]["label"] == "PRE_EXPERIMENT_OBSERVATION_ONLY"
    # Tarihsel özet raporun içinde, salt okunur ve dürüst.
    leg = rep["superseded_versions"][0]
    assert leg["status"] == "SUPERSEDED_INCOMPLETE_ENTRY_INPUT"
    assert leg["read_only"] is True and leg["evidence_rewritten"] is False
    assert leg["backfilled"] is False and leg["comparable_across_all_policies"] is False
    assert leg["profitability_conclusion"] is None
    assert leg["evaluation_start_at"] == V1_START
    assert leg["files_sha256"]["events"] == before[EVENTS_FILE]
    assert leg["files_sha256"]["books"] == before[BOOKS_FILE]
    t = leg["trades"]["F00035"]["policies"]
    assert {p: t[p]["decision"] for p in POLICIES} == {P0: ACCEPT, P1: ABSTAIN, P2: ACCEPT,
                                                       P3: ACCEPT, P4: ABSTAIN}
    assert PX.R_AE_UNKNOWN in t[P1]["reason_codes"] and PX.R_AE_UNKNOWN in t[P4]["reason_codes"]
    assert set(leg["coverage_defect"]["inert_policies"]) == {P1, P4}
    assert leg["sim_open_by_policy"] == {P0: ["F00035"], P1: [], P2: ["F00035"],
                                         P3: ["F00035"], P4: []}
    assert rep["statements_tr"] == list(PX.HONESTY_STATEMENTS_TR)
    assert rep["missing_means"] == "ABSTAIN" and rep["profitability_proven"] is False
    assert rep["winning_policy_selected"] is None
    assert rep["auto_promotion_possible_today"] is False
    # v1 raporu (profitability_experiment.json) bir daha YAZILMADI.
    assert (tmp_path / REPORT_FILE).read_bytes() == (tmp_path / REPORT_FILE).read_bytes()
    assert json.loads((tmp_path / REPORT_FILE).read_text(encoding="utf-8"))["experiment_id"] == "pfexp_v1"


def test_19b_legacy_summary_is_read_only_and_survives_missing_or_broken_files(tmp_path: Path):
    assert legacy_v1_summary(tmp_path) is None
    seed_v1(tmp_path)
    b1 = {n: (tmp_path / n).read_bytes() for n in (EVENTS_FILE, BOOKS_FILE, REPORT_FILE)}
    s = legacy_v1_summary(tmp_path)
    assert s["event_count"] == 8 and s["duplicate_event_ids"] == 0 and s["malformed"] == 0
    assert s["books_checksum_ok"] is True
    assert s["per_policy"][P0]["decisions"] == {ACCEPT: 1, "FILTER": 0, ABSTAIN: 0}
    assert s["per_policy"][P1]["decisions"][ABSTAIN] == 1 and s["per_policy"][P1]["opens"] == 0
    assert {n: (tmp_path / n).read_bytes() for n in b1} == b1, "özet v1 dosyasına YAZDI"
    # Bozuk kitap → checksum False, özet yine üretilir, dosya değişmez.
    (tmp_path / BOOKS_FILE).write_text("{bozuk", encoding="utf-8")
    s2 = legacy_v1_summary(tmp_path)
    assert s2["status"] == "SUPERSEDED_INCOMPLETE_ENTRY_INPUT" and s2["books_checksum_ok"] is None
    assert (tmp_path / BOOKS_FILE).read_text(encoding="utf-8") == "{bozuk"
    # Tarihsel depo için yeni kimlik DONDURULAMAZ.
    with pytest.raises(ValueError, match="SALT OKUNUR"):
        ExperimentStore(tmp_path / "empty", experiment_id=LEGACY_EXPERIMENT_ID).freeze_identity()


# ============================================================ 20: başlangıç-sonrası kapanış

def test_20_pre_start_position_stays_excluded_when_it_closes_after_new_start(tmp_path: Path):
    c11 = v11()
    close = {"id": "F00035", "symbol": "ZEN/USDT", "side": "LONG", "opened_at": F35_OPENED,
             "closed_at": "2026-09-07T03:00:00+00:00", "exit_reason": "stop", "net_pnl": -1.3,
             "r_multiple": -1.0, "fees": 0.005, "funding": 0.0, "exit_avg": 6.14}
    eng = _engine(tmp_path, cfg=c11, positions={}, history=[close],
                  snap=snapshot(symbol="ZEN/USDT"), link_tid="F00035")
    assert eng._experiment_closes(c11) == {}
    assert eng._experiment_candidates(c11) == []
    eng._run_profitability_experiment(None)
    evs = list(eng.experiment_store.iter_events())
    assert not [e for e in evs if e["kind"] in (EV_CLOSE, EV_OPEN, EV_DECISION)]
    rep = json.loads((tmp_path / "profitability_experiment_v1_1.json").read_text(encoding="utf-8"))
    assert rep["pre_experiment_excluded"]["closed"] == 1
    assert rep["pre_experiment_excluded"]["closed_after_start_still_excluded"] == 1
    assert rep["n_comparable_closes"] == 0


# ============================================================ 21: kimlik / checksum fail-closed

def test_21_identity_config_or_checksum_mismatch_fails_closed(tmp_path: Path):
    c11 = v11()
    st = ExperimentStore(tmp_path, experiment_id=c11.experiment_id)
    b = {p: PolicyBook(p) for p in POLICIES}
    open_simulated(b[P0], candidate(snapshot()), c11)
    st.append(PX.make_event(c11, P0, EV_DECISION, {"decision": ACCEPT}, "F1"))
    st.append(PX.make_event(c11, P0, EV_OPEN, {"position": b[P0].positions["F1"].to_dict()}, "F1"))
    assert st.save_books(b, c11)["ok"] is True
    ok, meta = st.load_books(c11)
    assert meta["source"] == "SNAPSHOT" and meta["identity_ok"] is True
    # (a) farklı policy_version → anlık görüntüye GÜVENİLMEZ → REPLAY (yabancı olaylar dışarıda)
    other = v11(policy_version="pfexp_v1.2.0")
    got, m = st.load_books(other)
    assert m["source"] == "REPLAY" and m["reason"] == "IDENTITY_MISMATCH"
    assert not got[P0].positions and m["foreign_events"] == 2
    # (b) farklı config_id (eşik değişti) → aynı
    got2, m2 = st.load_books(v11(max_cluster_risk_share=0.30))
    assert m2["source"] == "REPLAY" and not got2[P0].positions
    # (c) checksum bozuk → REPLAY, defterden onarım
    d = json.loads(st.books_path.read_text(encoding="utf-8"))
    d["checksum_sha256"] = "0" * 64
    st.books_path.write_text(json.dumps(d), encoding="utf-8")
    got3, m3 = st.load_books(c11)
    assert m3["source"] == "REPLAY" and m3["reason"] == "CHECKSUM_MISMATCH"
    assert "F1" in got3[P0].positions
    # (d) kitap üst kimliği doğru ama GÖMÜLÜ pozisyon kimliği yabancı → REPLAY
    st.save_books(got3, c11)
    d = json.loads(st.books_path.read_text(encoding="utf-8"))
    d["books"][P0]["positions"]["F1"]["experiment_id"] = "pfexp_v1"
    blob = json.dumps(d["books"], sort_keys=True, ensure_ascii=False, default=str)
    d["checksum_sha256"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    st.books_path.write_text(json.dumps(d), encoding="utf-8")
    got4, m4 = st.load_books(c11)
    assert m4["source"] == "REPLAY" and m4["identity_ok"] is False
    # (e) kitap yazımı: config kimliği depoyla uyuşmazsa YAZILMAZ
    assert st.save_books(b, v1())["ok"] is False


# ============================================================ kimlik dondurma

def test_start_is_frozen_once_never_inherited_never_moved(tmp_path: Path):
    from datetime import datetime, timedelta, timezone
    seed_v1(tmp_path)                                        # v1 kitabı: V1_START taşır
    st = ExperimentStore(tmp_path, experiment_id="pfexp_v1_1")
    t0 = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    f1 = st.freeze_identity(now=t0, code_sha="newsha")
    assert f1["source"] == "FROZEN_NOW" and f1["evaluation_start_at"] == V11_START
    assert f1["evaluation_start_at"] != V1_START, "v1 başlangıcı DEVRALINDI"
    ident = json.loads(st.identity_path.read_text(encoding="utf-8"))
    assert ident["experiment_id"] == "pfexp_v1_1" and ident["frozen_by_code_sha"] == "newsha"
    # İkinci/üçüncü kurulum (daha geç ya da daha ERKEN 'şimdi') aynı değeri döner.
    f2 = st.freeze_identity(now=t0 + timedelta(days=3))
    f3 = ExperimentStore(tmp_path, experiment_id="pfexp_v1_1").freeze_identity(
        now=t0 - timedelta(days=3))
    assert f2["evaluation_start_at"] == f3["evaluation_start_at"] == V11_START
    assert f2["source"] == f3["source"] == "IDENTITY_FILE"
    # Kimlik dosyası yoksa ama aynı deneyin kitabı varsa kitaptan okunur (ileriye çekilmez).
    st.identity_path.unlink()
    c11 = v11()
    st.save_books({p: PolicyBook(p) for p in POLICIES}, c11)
    f4 = st.freeze_identity(now=t0 + timedelta(days=9))
    assert f4["evaluation_start_at"] == V11_START and f4["source"] == "BOOKS_SNAPSHOT"


def test_engine_identity_policy_rejects_legacy_and_ignores_config_start():
    pol = TradingEngineV3._experiment_identity_policy
    with pytest.raises(ValueError, match="SUPERSEDED"):
        pol({"experiment_id": "pfexp_v1"})
    with pytest.raises(ValueError, match="SUPERSEDED"):
        pol({"policy_version": "pfexp_v1.0.0"})
    out = pol({"evaluation_start_at": "2020-01-01T00:00:00+00:00", "frozen_at": "x",
               "max_cluster_risk_share": 0.35})
    assert "evaluation_start_at" not in out and "frozen_at" not in out
    assert out == {"max_cluster_risk_share": 0.35}
    assert pol({}) == {} and pol(None) == {}


# ============================================================ 22: kaynak seviyesi izolasyon

def _code_only(path: str) -> str:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(n, "body", None)
            if (b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                b.pop(0)
    return ast.unparse(tree)


@pytest.mark.parametrize("path", ["tradingbot/learn/profitability_ae.py",
                                  "tradingbot/learn/profitability_store.py",
                                  "tradingbot/learn/profitability_experiment.py"])
def test_22_no_canonical_ledger_risk_capital_gateway_or_order_mutation(path):
    # `portfolio_open_positions` bir SNAPSHOT alan adıdır (karar anı sayacı), defter çağrısı değil.
    code = _code_only(path).replace("portfolio_open_positions", "portfolio_n_open")
    for bad in ("gateway", "Gateway", "create_order", "place_order", "submit_order", "ccxt",
                "RiskEngine", "risk_engine", "FuturesLedgerV2", "SpotLedger", "futures_ledger",
                "spot_ledger", "open_position", "close_position", "reduce_position",
                "starting_equity", "wallet_balance", "risk_per_trade_pct", "set_leverage",
                "futures_ledger.json", "risk.json", "learning.json"):
        assert bad not in code, f"{path}:{bad}"


# ============================================================ 23: açık/kapalı → kanonik aynı

def test_23_experiment_on_vs_off_leaves_canonical_state_and_snapshot_bytes_identical(tmp_path: Path):
    """Deney AÇIKKEN de KAPALIYKEN de: kanonik pozisyon/geçmiş ve snapshot JSONL'i aynı."""
    def run(enabled: bool, d: Path):
        d.mkdir(parents=True, exist_ok=True)
        cfg_ = v11()
        eng = _engine(d, cfg=cfg_, positions={"ZEN/USDT": _pos("F9", "2026-09-06T13:00:00+00:00")},
                      history=[{"id": "F8", "symbol": "A/USDT", "side": "LONG",
                                "opened_at": "2026-09-06T12:30:00+00:00",
                                "closed_at": "2026-09-06T14:00:00+00:00", "exit_reason": "stop",
                                "net_pnl": -1.0, "r_multiple": -1.0, "fees": 0.0, "funding": 0.0}],
                      snap=snapshot(), link_tid="F9")
        if not enabled:
            eng.experiment_cfg = None
            eng.experiment_store = None
        snap_before = (d / "entry_snapshot.jsonl").read_bytes()
        eng._run_profitability_experiment(None)
        pos = json.dumps({k: v.to_dict() for k, v in eng.ledger2.positions.items()}, sort_keys=True)
        hist = json.dumps(eng.ledger2.history, sort_keys=True, default=str)
        assert (d / "entry_snapshot.jsonl").read_bytes() == snap_before, "snapshot dosyası DEĞİŞTİ"
        return pos, hist

    assert run(True, tmp_path / "on") == run(False, tmp_path / "off")
    assert (tmp_path / "on" / "profitability_experiment_v1_1.json").exists()
    assert not list((tmp_path / "off").glob("profitability_experiment*"))


# ============================================================ 25: config fail-closed

def test_25_active_paper_bounded_auto_promotion_and_legacy_identity_fail_validation():
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
    # Gevşetilmiş terfi kapısı da reddedilir.
    r = dict(raw)
    r["entry_selectivity"] = dict(r.get("entry_selectivity") or {}) | {
        "experiment_policy": {"promotion_min_closes": 10}}
    with pytest.raises(ConfigError, match="experiment_policy"):
        load_v3(r)
    # Üretim config'i varsayılan (v1.1) kimliği ve SHADOW modu üretir.
    v3 = load_v3(dict(raw))
    en = v3.entry_selectivity
    assert en.experiment_mode == "SHADOW" and en.experiment_auto_promotion is False
    assert en.experiment_enabled is True
    xp = TradingEngineV3._experiment_identity_policy(dict(en.experiment_policy or {}))
    assert ExperimentConfig.from_dict(xp).experiment_id == "pfexp_v1_2"


def test_entry_challenger_thresholds_are_unchanged_by_this_repair():
    """A/E eşikleri yeniden ayarlanmadı: kanonik varsayılanlar aynen duruyor."""
    c = EntryChallengerConfig()
    assert (c.assumed_payoff_ratio, c.prob_safety_margin, c.min_conservative_edge_r,
            c.max_open_risk_fraction, c.max_same_direction) == (1.5, 0.02, 0.0, 0.80, 6)
    assert c.policy_version == "entry_v1.0.0"
