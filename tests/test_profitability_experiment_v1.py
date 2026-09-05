"""FAZ 6 — `PROFITABILITY_EXPERIMENT_V1` regresyon ve güvenlik paketi.

22 maddelik sözleşmenin tamamı: izolasyon, point-in-time, idempotency, çökme kurtarma,
maliyet, düşük örneklem kapıları, pano dayanıklılığı ve sır sızıntısı.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tradingbot.learn import profitability_experiment as PX
from tradingbot.learn.profitability_experiment import (ABSTAIN, ACCEPT, FILTER, N_TRIALS,
                                                       P0, P1, P2, P3, P4, POLICIES,
                                                       ExperimentConfig, PolicyBook,
                                                       close_simulated, closed_returns,
                                                       compare, correlation, decide_entry,
                                                       open_simulated, root_cause_summary,
                                                       sidak_alpha)
from tradingbot.learn.profitability_store import (EV_CLOSE, EV_DECISION, EV_OPEN,
                                                  ExperimentStore)

HOUR = 3_600_000
AS_OF = 1000 * 86_400_000
START = "2026-09-05T00:00:00+00:00"


def cfg(**kw) -> ExperimentConfig:
    base = dict(evaluation_start_at=START, frozen_at=START, code_sha="deadbeef")
    base.update(kw)
    return ExperimentConfig.from_dict(base)


def cand(tid="T1", *, side="LONG", a="ACCEPT", e="ACCEPT", entry=100.0, stop=95.0,
         qty=1.0, sym="X/USDT", rets=None, accepted=True, risk=5.0):
    return {"trade_id": tid, "symbol": sym, "side": side, "entry": entry, "qty": qty,
            "initial_stop": stop, "targets": [110.0], "risk_usdt": risk, "leverage": 2,
            "entry_fee": 0.01, "slippage_cost": 0.02,
            "opened_at": "2026-09-05T01:00:00+00:00", "champion_accepted": accepted,
            "candidate_id": f"c_{tid}",
            "entry_families": ({"A_x": {"decision": a}, "E_y": {"decision": e}}
                               if (a or e) else None),
            "returns_1h": (rets if rets is not None else [0.01, -0.02] * 25)}


def books() -> dict[str, PolicyBook]:
    return {p: PolicyBook(p) for p in POLICIES}


# ============================================================ 1-6: İZOLASYON (kaynak seviyesi)

def _code_only(path: str) -> str:
    """Modülün KODU — docstring hariç. Sözleşmeyi ANLATAN docstring ihlal değildir."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(n, "body", None)
            if (b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)
                    and isinstance(b[0].value.value, str)):
                b.pop(0)
    return ast.unparse(tree)


MODULES = ("tradingbot/learn/profitability_experiment.py",
           "tradingbot/learn/profitability_store.py")


def test_05_no_gateway_or_order_imports_or_calls():
    for m in MODULES:
        code = _code_only(m)
        for bad in ("gateway", "Gateway", "create_order", "place_order", "submit_order",
                    "ccxt", "binance", "OrderRouter"):
            assert bad not in code, f"{m}:{bad}"


def test_03_04_no_risk_engine_or_canonical_ledger_mutation():
    for m in MODULES:
        code = _code_only(m)
        for bad in ("RiskEngine", "risk_engine", "FuturesLedgerV2", "SpotLedger",
                    "futures_ledger", "spot_ledger", "open_position", "close_position",
                    "reduce_position", "position_size"):
            assert bad not in code, f"{m}:{bad}"


def test_06_no_capital_leverage_or_risk_budget_changes():
    for m in MODULES:
        code = _code_only(m)
        for bad in ("starting_equity", "wallet_balance", "risk_per_trade_pct",
                    "max_total_open_risk_pct", "leverage_default", "set_leverage"):
            assert bad not in code, f"{m}:{bad}"


def test_experiment_writes_only_its_own_state_files():
    from tradingbot.learn import profitability_store as PS
    for f in (PS.EVENTS_FILE, PS.BOOKS_FILE, PS.REPORT_FILE):
        assert f.startswith("profitability_experiment")
    code = _code_only("tradingbot/learn/profitability_store.py")
    for bad in ("futures_ledger.json", "spot_ledger.json", "risk.json", "learning.json",
                "entry_selectivity.json", "exit_eval.json", "mtf_eval.json"):
        assert bad not in code, bad


def test_applied_is_always_false_everywhere():
    c = cfg()
    b = books()
    for p in POLICIES:
        d = decide_entry(p, cand(), b[p], c)
        assert d["applied"] is False
    doc = compare(b, c)
    assert doc["applied_to_canonical"] is False
    assert doc["mode"] == "SHADOW_PAPER_ONLY"
    for rep in doc["policies"].values():
        assert rep["applied"] is False


# ============================================================ 1-2: kanonik karar değişmezliği

def _stub_engine(tmp_path: Path, *, enabled: bool):
    import types
    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.learn.profitability_store import ExperimentStore
    eng = types.SimpleNamespace(
        experiment_cfg=(cfg() if enabled else None),
        experiment_store=(ExperimentStore(tmp_path) if enabled else None),
        experiment_mode="SHADOW", exit_policy_cfg=None, path_store=None,
        entry_snapshot_store=None, run_id="r",
        code_sha=lambda: "deadbeef", config_hash=lambda: "cfg",
        cfg=types.SimpleNamespace(state_path=tmp_path),
        ledger2=types.SimpleNamespace(positions={}, history=[]))
    eng._experiment_candidates = lambda c: []
    eng._experiment_closes = lambda c: {}
    eng._experiment_pre_count = lambda c: {"open": 0, "closed": 0}
    eng._run_profitability_experiment = (
        lambda now: TradingEngineV3._run_profitability_experiment(eng, now))
    return eng


def test_01_02_layer_on_off_does_not_touch_canonical_objects(tmp_path: Path):
    from tradingbot.core import utc_now
    off = _stub_engine(tmp_path / "off", enabled=False)
    on = _stub_engine(tmp_path / "on", enabled=True)
    (tmp_path / "on").mkdir(parents=True, exist_ok=True)
    before_off = (dict(off.ledger2.positions), list(off.ledger2.history))
    before_on = (dict(on.ledger2.positions), list(on.ledger2.history))
    assert off._run_profitability_experiment(utc_now()) == {}
    on._run_profitability_experiment(utc_now())
    assert (dict(off.ledger2.positions), list(off.ledger2.history)) == before_off
    assert (dict(on.ledger2.positions), list(on.ledger2.history)) == before_on
    # Katman kapalıyken HİÇBİR deney dosyası oluşmaz.
    assert not (tmp_path / "off").exists() or not list((tmp_path / "off").glob("profit*"))


def test_04_no_canonical_position_is_opened_closed_or_resized(tmp_path: Path):
    from tradingbot.core import utc_now
    eng = _stub_engine(tmp_path, enabled=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    eng.ledger2.positions = {"AVAX/USDT": {"id": "F1", "qty": 1.0}}
    snap = json.dumps(eng.ledger2.positions, sort_keys=True)
    eng._run_profitability_experiment(utc_now())
    assert json.dumps(eng.ledger2.positions, sort_keys=True) == snap


# ============================================================ 7: PRE_EXPERIMENT dışlaması

def test_07_pre_experiment_trades_are_excluded():
    c = cfg()
    assert c.evaluation_start_at == START
    # Deney penceresinden ÖNCE açılan aday motor katmanında hiç üretilmez;
    # burada sözleşme sabiti ve etiketi doğrulanır.
    assert PX.PRE_EXPERIMENT == "PRE_EXPERIMENT_OBSERVATION_ONLY"
    assert PX.R_PRE_EXPERIMENT == "OPENED_BEFORE_EVALUATION_START"


def test_07b_engine_filters_by_evaluation_start(tmp_path: Path):
    """Motor süzgeci: `opened_at < evaluation_start_at` olan pozisyon aday OLAMAZ."""
    import types
    from tradingbot.engine_v3 import TradingEngineV3
    eng = types.SimpleNamespace(
        entry_snapshot_store=None, runner=types.SimpleNamespace(last_frames={}),
        cfg=types.SimpleNamespace(state_path=tmp_path),
        ledger2=types.SimpleNamespace(positions={
            "OLD": types.SimpleNamespace(to_dict=lambda: {
                "id": "F_OLD", "symbol": "A/USDT", "side": "LONG", "entry_avg": 10.0,
                "initial_stop": 9.0, "qty": 1.0, "targets": [],
                "opened_at": "2026-09-01T00:00:00+00:00"}),
            "NEW": types.SimpleNamespace(to_dict=lambda: {
                "id": "F_NEW", "symbol": "B/USDT", "side": "LONG", "entry_avg": 10.0,
                "initial_stop": 9.0, "qty": 1.0, "targets": [],
                "opened_at": "2026-09-06T00:00:00+00:00"})}, history=[]))
    got = TradingEngineV3._experiment_candidates(eng, cfg())
    assert [r["trade_id"] for r in got] == ["F_NEW"]


# ============================================================ 8: point-in-time

def test_08_point_in_time_rejects_future_and_unclosed_bars():
    bars = [{"timestamp": AS_OF - (5 - i) * HOUR, "open": 1, "high": 1, "low": 1,
             "close": 100.0 + i} for i in range(5)]
    bars.append({"timestamp": AS_OF - HOUR // 2, "open": 1, "high": 1, "low": 1,
                 "close": 9999.0})                       # KAPANMAMIŞ
    bars.append({"timestamp": AS_OF + 5 * HOUR, "open": 1, "high": 1, "low": 1,
                 "close": 8888.0})                       # GELECEK
    r = closed_returns(bars, as_of_ms=AS_OF, lookback=120)
    assert all(abs(v) < 1.0 for v in r), "gelecek/kapanmamış bar getiriye sızdı"
    assert len(r) == 4


def test_08b_as_of_none_yields_no_returns():
    bars = [{"timestamp": AS_OF - HOUR, "open": 1, "high": 1, "low": 1, "close": 100.0}]
    assert closed_returns(bars, as_of_ms=None, lookback=50) == []


# ============================================================ 9: eksik → ABSTAIN

def test_09_missing_price_or_risk_means_abstain():
    c, b = cfg(), books()
    for p in POLICIES:
        d = decide_entry(p, cand() | {"entry": None}, b[p], c)
        assert d["decision"] == ABSTAIN and PX.R_NO_PRICE in d["reason_codes"]
        d2 = decide_entry(p, cand() | {"risk_usdt": None}, b[p], c)
        assert d2["decision"] == ABSTAIN and PX.R_NO_RISK in d2["reason_codes"]


def test_09b_missing_ae_decision_abstains_not_filters():
    c, b = cfg(), books()
    d = decide_entry(P1, cand(a=None, e=None) | {"entry_families": None}, b[P1], c)
    assert d["decision"] == ABSTAIN
    assert PX.R_AE_UNKNOWN in d["reason_codes"]


def test_09c_abstain_is_never_converted_to_veto():
    c, b = cfg(), books()
    d = decide_entry(P1, cand(a="ABSTAIN", e="ABSTAIN"), b[P1], c)
    assert d["decision"] == ACCEPT, "ABSTAIN VETO'ya çevrilemez"


def test_09d_unknown_correlation_is_reported_not_zeroed():
    assert correlation([0.1] * 10, [0.2] * 10, min_overlap=30) is None
    c, b = cfg(), books()
    open_simulated(b[P2], cand("T0", side="LONG", sym="Z/USDT", rets=[0.01] * 3))
    b[P2].returns["Z/USDT"] = [0.01] * 3            # örtüşme YETERSİZ
    # Karşı yön seçilir ki yön kısıtı ÖNCE tetiklenmesin; ölçülen şey KORELASYONdur.
    d = decide_entry(P2, cand("T1", side="SHORT", rets=[0.02] * 3), b[P2], c)
    assert PX.R_CORR_UNKNOWN in d["reason_codes"]
    assert d["evidence"]["correlation_state"] == "UNKNOWN"


# ============================================================ 10-11: idempotency / çökme

def test_10_no_duplicate_events_after_repeated_processing(tmp_path: Path):
    c = cfg()
    st = ExperimentStore(tmp_path)
    ev = PX.make_event(c, P0, EV_DECISION, {"decision": ACCEPT}, "T1")
    assert st.append(ev) is True
    assert st.append(ev) is False                     # aynı event_id İKİNCİ KEZ yazılmaz
    assert st.append(dict(ev)) is False
    assert st.duplicates == 2
    assert len(list(st.iter_events())) == 1


def test_10b_replay_is_deterministic_and_idempotent(tmp_path: Path):
    c = cfg()
    st = ExperimentStore(tmp_path)
    b = PolicyBook(P0)
    pos = open_simulated(b, cand("T1"))
    st.append(PX.make_event(c, P0, EV_DECISION, {"decision": ACCEPT}, "T1"))
    st.append(PX.make_event(c, P0, EV_OPEN, {"position": pos.to_dict()}, "T1"))
    a1 = st.replay(c)
    a2 = st.replay(c)
    assert json.dumps(a1[P0].to_dict(), sort_keys=True, default=str) == \
        json.dumps(a2[P0].to_dict(), sort_keys=True, default=str)
    assert a1[P0].n_accept == 1 and "T1" in a1[P0].positions


def test_11_crash_between_journal_and_book_repairs_from_journal(tmp_path: Path):
    c = cfg()
    st = ExperimentStore(tmp_path)
    b = PolicyBook(P0)
    pos = open_simulated(b, cand("T1"))
    st.append(PX.make_event(c, P0, EV_DECISION, {"decision": ACCEPT}, "T1"))
    st.append(PX.make_event(c, P0, EV_OPEN, {"position": pos.to_dict()}, "T1"))
    st.save_books({P0: b}, c)
    # ÇÖKME: kitap anlık görüntüsü bozuldu.
    st.books_path.write_text('{"books": {"P0_CHAMPION_MIRROR": {}}, '
                             '"checksum_sha256": "bozuk"}', encoding="utf-8")
    got, meta = st.load_books(c)
    assert meta["source"] == "REPLAY" and meta["checksum_ok"] is False
    assert "T1" in got[P0].positions, "defterden onarım başarısız"


def test_11b_valid_snapshot_is_used_and_checksum_verified(tmp_path: Path):
    c = cfg()
    st = ExperimentStore(tmp_path)
    b = PolicyBook(P0)
    open_simulated(b, cand("T1"))
    res = st.save_books({p: (b if p == P0 else PolicyBook(p)) for p in POLICIES}, c)
    assert res["ok"] is True
    got, meta = st.load_books(c)
    assert meta["source"] == "SNAPSHOT" and meta["checksum_ok"] is True
    assert "T1" in got[P0].positions


def test_malformed_rows_are_reported_never_silently_ignored(tmp_path: Path):
    st = ExperimentStore(tmp_path)
    st.dir.mkdir(parents=True, exist_ok=True)
    st.events_path.write_text('{"event_id":"a","policy":"P0_CHAMPION_MIRROR"}\n'
                              "BOZUK-SATIR\n[1,2,3]\n", encoding="utf-8")
    rows = list(st.iter_events())
    assert len(rows) == 1
    assert st.malformed == 2
    # Yeniden tarama sayacı BİRİKTİRMEZ (tam yeniden okuma).
    assert st.stats()["malformed"] == 2
    assert len(list(st.iter_events())) == 1 and st.malformed == 2


# ============================================================ 12-13: maliyet

def test_12_costs_are_not_double_counted(tmp_path: Path):
    b = PolicyBook(P0)
    open_simulated(b, cand("T1", entry=100.0, stop=95.0, qty=1.0))
    c = close_simulated(b, "T1", exit_price=110.0, closed_at="2026-09-05T02:00:00+00:00",
                        exit_kind=PX.X_CANONICAL, fees=0.5, funding=-0.1)
    # brüt 10.0, komisyon 0.5 BİR KEZ, funding -0.1 BİR KEZ
    assert c.net_pnl == pytest.approx(10.0 - 0.5 - 0.1)
    assert c.fees == pytest.approx(0.5)
    assert c.r_multiple == pytest.approx((10.0 - 0.6) / 5.0)
    # kayma ayrı alandadır ve PnL'den TEKRAR düşülmez
    assert c.slippage_cost == pytest.approx(0.02)
    assert c.slippage_provenance == PX.SLIP_MEASURED


def test_13_slippage_provenance_distinguishes_measured_and_missing():
    b = PolicyBook(P0)
    p1 = open_simulated(b, cand("T1"))
    assert p1.slippage_provenance == PX.SLIP_MEASURED
    b2 = PolicyBook(P1)
    p2 = open_simulated(b2, cand("T2") | {"slippage_cost": None})
    assert p2.slippage_provenance == PX.SLIP_MISSING and p2.slippage_cost is None
    assert PX.SLIP_MODELED == "MODELED"


def test_13b_report_counts_measured_vs_missing_slippage():
    b = PolicyBook(P0)
    open_simulated(b, cand("T1"))
    close_simulated(b, "T1", exit_price=110.0, closed_at="x", exit_kind=PX.X_CANONICAL,
                    fees=0.1, funding=0.0)
    open_simulated(b, cand("T2") | {"slippage_cost": None})
    close_simulated(b, "T2", exit_price=90.0, closed_at="y", exit_kind=PX.X_CANONICAL,
                    fees=0.1, funding=0.0)
    rep = PX.policy_report(b, cfg())
    assert rep["n_slippage_measured"] == 1 and rep["n_slippage_missing"] == 1


# ============================================================ 14-16: adil karşılaştırma

def test_14_all_five_policies_receive_identical_inputs():
    c, b = cfg(), books()
    cd = cand()
    seen = {}
    for p in POLICIES:
        before = json.dumps(cd, sort_keys=True, default=str)
        seen[p] = decide_entry(p, cd, b[p], c)
        assert json.dumps(cd, sort_keys=True, default=str) == before, \
            "politika aday nesnesini DEĞİŞTİRDİ"
    assert set(seen) == set(POLICIES)
    assert len(POLICIES) == N_TRIALS == 5


def test_15_filtered_trades_cannot_produce_fabricated_fills():
    c, b = cfg(), books()
    d = decide_entry(P1, cand(a="VETO"), b[P1], c)
    assert d["decision"] == FILTER
    assert not b[P1].positions, "elenen işlem için pozisyon UYDURULDU"
    # Şampiyonun kabul etmediği aday hiçbir politikada açılamaz.
    for p in POLICIES:
        d2 = decide_entry(p, cand("T9", accepted=False), b[p], c)
        assert d2["decision"] == FILTER
        assert PX.R_NOT_CHAMPION_ACCEPTED in d2["reason_codes"]
        assert "T9" not in b[p].positions


def test_16_p3_p4_exit_logic_cannot_touch_canonical_stops_or_targets():
    """P3/P4 yalnız KENDİ `SimPosition.stop`unu değiştirir; kanonik nesneye erişmez."""
    src = _code_only("tradingbot/learn/profitability_experiment.py")
    for bad in ("position.stop =", "ledger", "targets_hit", "tp1_done ="):
        assert bad not in src, bad
    b = PolicyBook(P3)
    p = open_simulated(b, cand("T1"))
    canonical_stop = 95.0
    p.stop = 99.0                                  # simüle sıkıştırma
    assert p.initial_stop == canonical_stop, "başlangıç stopu DEĞİŞTİ"
    assert p.stop != p.initial_stop


def test_16b_p0_p1_p2_never_modify_stop():
    b = PolicyBook(P0)
    p = open_simulated(b, cand("T1"))
    assert p.stop == p.initial_stop == 95.0


# ============================================================ 17-18: kapılar

def test_17_low_sample_gates_report_not_evaluable():
    c = cfg()
    b = books()
    for p in POLICIES:                              # 3 kapanış → 50'nin ÇOK altında
        for i in range(3):
            open_simulated(b[p], cand(f"T{i}"))
            close_simulated(b[p], f"T{i}", exit_price=110.0, closed_at=f"2026-09-0{i+5}",
                            exit_kind=PX.X_CANONICAL, fees=0.1, funding=0.0)
    doc = compare(b, c)
    for name, g in doc["promotion_eligibility"].items():
        codes = {x["code"]: x for x in g["gates"]}
        assert codes["MIN_COMPARABLE_CLOSES"]["passed"] is False
        for dep in ("POSITIVE_POST_COST_EXPECTANCY", "PROFIT_FACTOR_ABOVE_ONE",
                    "CONFIDENCE_INTERVAL_EXCLUDES_ZERO", "DRAWDOWN_NOT_WORSE",
                    "CVAR5_NOT_WORSE"):
            assert codes[dep]["status"] == "NOT_EVALUABLE_LOW_SAMPLE", (name, dep)
            assert codes[dep]["passed"] is False
        assert g["all_passed"] is False
        assert g["promotion_possible"] is False
        assert g["auto_promotion"] is False


def test_17b_existing_promotion_gates_cannot_be_weakened():
    c = cfg()
    assert c.promotion_min_closes == 50 and c.promotion_min_days == 30
    with pytest.raises(ValueError, match="GEVŞETİLEMEZ"):
        ExperimentConfig.from_dict({"promotion_min_closes": 10})
    with pytest.raises(ValueError, match="GEVŞETİLEMEZ"):
        ExperimentConfig.from_dict({"promotion_min_days": 5})


def test_17c_early_directionality_is_informational_and_cannot_promote():
    c, b = cfg(), books()
    doc = compare(b, c)
    e = doc["early_directionality"]
    assert e["state"] == "NOT_EVALUABLE_LOW_SAMPLE"
    assert e["activates_anything"] is False
    assert e["required"] == c.early_directionality_min_closes == 10


def test_18_multiple_testing_count_is_recorded():
    doc = compare(books(), cfg())
    mt = doc["multiple_testing"]
    assert mt["n_trials"] == 5
    assert mt["per_trial_alpha_sidak"] == sidak_alpha()
    assert 0.0 < mt["per_trial_alpha_sidak"] < mt["family_alpha"]
    for g in doc["promotion_eligibility"].values():
        assert any(x["code"] == "MULTIPLE_TESTING_ACCOUNTED" for x in g["gates"])


# ============================================================ 19-20: pano / sır

def test_19_dashboard_handles_missing_and_corrupt_state(tmp_path: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    sd, dd = tmp_path / "state", tmp_path / "data"
    sd.mkdir(), dd.mkdir()
    (sd / "learning.json").write_text('{"n_trades":0,"lessons":[],"blacklist":[]}',
                                      encoding="utf-8")
    c = TestClient(create_app(sd, dd))
    r1 = c.get("/learning")                          # dosya YOK
    assert r1.status_code == 200
    assert "Kârlılık Deneyi" in r1.text and "Neden Zarar Ediyoruz?" in r1.text
    (sd / "profitability_experiment.json").write_text("{bozuk", encoding="utf-8")
    assert c.get("/learning").status_code == 200     # BOZUK dosya
    doc = compare(books(), cfg())
    doc["root_cause"] = root_cause_summary(
        [{"r_multiple": -1.0, "net_pnl": -1.0, "exit_reason": "stop"},
         {"r_multiple": 2.0, "net_pnl": 2.0, "exit_reason": "hedef2"}])
    (sd / "profitability_experiment.json").write_text(json.dumps(doc, default=str),
                                                      encoding="utf-8")
    r3 = c.get("/learning")
    assert r3.status_code == 200
    for probe in ("SHADOW", "P0_CHAMPION_MIRROR", "P4_COMBINED",
                  "DEĞERLENDİRİLEMEZ", "NEDENSEL DEĞİL", "GÖZLEMDİR"):
        assert probe in r3.text, probe


def test_20_secrets_never_render(tmp_path: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    sd, dd = tmp_path / "state", tmp_path / "data"
    sd.mkdir(), dd.mkdir()
    (sd / "learning.json").write_text('{"n_trades":0,"lessons":[],"blacklist":[]}',
                                      encoding="utf-8")
    doc = compare(books(), cfg())
    doc["leak_probe"] = {"api_key": "SEKRET_ANAHTAR_123456", "token": "ghp_XXXXSECRET"}
    (sd / "profitability_experiment.json").write_text(json.dumps(doc, default=str),
                                                      encoding="utf-8")
    t = TestClient(create_app(sd, dd)).get("/learning").text
    assert "SEKRET_ANAHTAR_123456" not in t
    assert "ghp_XXXXSECRET" not in t


def test_20b_no_prohibited_marketing_language(tmp_path: Path):
    from fastapi.testclient import TestClient

    from tradingbot.dashboard.app import create_app
    sd, dd = tmp_path / "state", tmp_path / "data"
    sd.mkdir(), dd.mkdir()
    (sd / "learning.json").write_text('{"n_trades":0,"lessons":[],"blacklist":[]}',
                                      encoding="utf-8")
    (sd / "profitability_experiment.json").write_text(
        json.dumps(compare(books(), cfg()), default=str), encoding="utf-8")
    t = TestClient(create_app(sd, dd)).get("/learning").text
    i = t.find("Kârlılık Deneyi")
    seg = t[i:]
    for bad in ("garantili", "en iyi strateji", "kesin kâr", "guaranteed"):
        assert bad.lower() not in seg.lower(), bad


# ============================================================ 21: rotasyon

def test_21_rotation_is_archive_first_and_lossless(tmp_path: Path):
    st = ExperimentStore(tmp_path, max_lines=2, archive=None)
    st.dir.mkdir(parents=True, exist_ok=True)
    c = cfg()
    for i in range(6):
        st.append(PX.make_event(c, P0, EV_DECISION, {"decision": ACCEPT}, f"T{i}"))
    res = st.rotate()
    assert res["health"] == "DISABLED_NO_DELETION"
    assert res["trimmed"] == 0
    assert len(list(st.iter_events())) == 6, "arşiv yokken satır SİLİNDİ"
    assert st.stats()["silent_deletion"] is False

    class FailingArchive:
        def seal(self, lines):
            raise OSError("disk full")

    st2 = ExperimentStore(tmp_path / "b", max_lines=2, archive=FailingArchive())
    st2.dir.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        st2.append(PX.make_event(c, P0, EV_DECISION, {"decision": ACCEPT}, f"U{i}"))
    r2 = st2.rotate()
    assert r2["health"] == "ARCHIVE_FAILED" and r2["trimmed"] == 0
    assert len(list(st2.iter_events())) == 6, "arşiv düşerken BUDAMA yapıldı"


# ============================================================ politika sözleşmesi

def test_exactly_five_frozen_policies_with_stable_identity():
    assert POLICIES == (P0, P1, P2, P3, P4) and len(POLICIES) == 5
    a, b_ = cfg().config_id, cfg().config_id
    assert a == b_, "config_id kararsız"
    # Kimlik SONUÇLARDAN bağımsızdır: `frozen_at`/`code_sha` değişse de aynı kalır.
    assert cfg(frozen_at="2099-01-01T00:00:00+00:00", code_sha="zzz").config_id == a
    # Eşik değişirse kimlik DEĞİŞİR.
    assert cfg(max_same_direction_risk_share=0.5).config_id != a


def test_p0_mirrors_and_p4_is_exactly_p1_plus_p2():
    c, b = cfg(), books()
    assert decide_entry(P0, cand(), b[P0], c)["decision"] == ACCEPT
    assert decide_entry(P4, cand(a="VETO"), b[P4], c)["decision"] == FILTER
    assert decide_entry(P4, cand(a="ACCEPT", e="ACCEPT"), b[P4], c)["decision"] == ACCEPT


def test_p2_limits_are_frozen_and_documented():
    c = cfg()
    assert c.max_same_direction_risk_share == 0.60
    assert c.max_cluster_risk_share == 0.35
    assert c.max_positions_per_cluster == 3
    assert c.correlation_min_overlap == 30


def test_p2_filters_on_same_direction_concentration():
    c = cfg(max_same_direction_risk_share=0.50)
    b = PolicyBook(P2)
    open_simulated(b, cand("T0", side="LONG", sym="A/USDT"))
    d = decide_entry(P2, cand("T1", side="LONG", sym="B/USDT"), b, c)
    assert d["decision"] == FILTER
    assert PX.R_DIR_SHARE in d["reason_codes"]


def test_avoided_loss_and_missed_gain_are_reported_separately():
    c, b = cfg(), books()
    for p in (P0, P1):
        open_simulated(b[p], cand("W1"))
        close_simulated(b[p], "W1", exit_price=110.0, closed_at="2026-09-06",
                        exit_kind=PX.X_CANONICAL, fees=0.0, funding=0.0)
    open_simulated(b[P0], cand("L1"))
    close_simulated(b[P0], "L1", exit_price=95.0, closed_at="2026-09-07",
                    exit_kind=PX.X_CANONICAL, fees=0.0, funding=0.0)
    doc = compare(b, c)
    assert doc["policies"][P1]["avoided_loss_r"] > 0
    assert doc["policies"][P1]["missed_gain_r"] == 0.0
    assert doc["policies"][P0]["avoided_loss_r"] == 0.0


def test_root_cause_summary_is_observational_only():
    r = root_cause_summary([{"r_multiple": -1.0, "net_pnl": -1.0, "exit_reason": "stop"},
                            {"r_multiple": 2.0, "net_pnl": 2.0, "exit_reason": "hedef2"}])
    assert r["causal"] is False
    assert r["evidence_grade"] == "OBSERVATION_ONLY"
    assert r["state"] == "LOW_SAMPLE"
    assert all(o["causal"] is False for o in r["observations"])
    assert root_cause_summary([])["state"] == "NO_DATA"


def test_config_rejects_active_and_auto_promotion():
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


def test_22_deterministic_repeated_evaluation():
    c = cfg()
    b1, b2 = books(), books()
    for b in (b1, b2):
        for p in POLICIES:
            open_simulated(b[p], cand("T1"))
            close_simulated(b[p], "T1", exit_price=110.0, closed_at="2026-09-06",
                            exit_kind=PX.X_CANONICAL, fees=0.1, funding=0.0)
    d1, d2 = compare(b1, c), compare(b2, c)
    for d in (d1, d2):
        d.pop("generated_at", None)
    assert json.dumps(d1, sort_keys=True, default=str) == \
        json.dumps(d2, sort_keys=True, default=str)
