"""DAVRANIŞ testleri: risk kapasitesi yalnız GERÇEKTEN açılan pozisyonlarla tükenir.

Bu dosya statik string araması YAPMAZ. Gerçek `TradingEngineV3._execute_locked` →
`ChiefPortfolioManager` → `RiskEngine.evaluate` → `FuturesLedgerV2.open` sırasını sürer ve
sonucu ölçer. Kapatılan dört mimari blocker:

1. Chief tetiklenmemiş adaylara risk AYIRMIYOR (rezervasyon yok; kapasite yalnız açık pozisyonlardan).
2. RiskEngine ham plan boyutunu değil NİHAİ boyutu/riski görüyor.
3. Red-team ekonomik zayıflıkları SERT VETO değil, üst sınırlı YUMUŞAK ceza.
4. Kayıtsız kapı kodu FAIL-CLOSED (sessiz soft kabul yok).

Bot işlem SAYISI hedeflemez: kaliteli fırsat yoksa 0, yüz bağımsız kaliteli fırsat varsa ve risk
serbestse 100 işlem açılabilmelidir. Asgari işlem kotası da YOKTUR.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_engine_v3 import _engine  # noqa: E402

import tradingbot.engine_v3 as E  # noqa: E402
from tradingbot.coinhead.redteam import RedTeamContext, review  # noqa: E402
from tradingbot.decision_gates import (FORBIDDEN_QUOTA_CODES, GateLedger,  # noqa: E402
                                       UnknownGateCode, gate_class, is_known)
from tradingbot.engine_v3 import _as_multiplier  # noqa: E402
from tradingbot.risk.profiles import resolve_profile  # noqa: E402

EQUITY = 5_000.0
EPS = 1e-6


# ============================================================================= yardımcılar
def _opp(edge: float, *, mult: float = 1.0, risk_pct: float = 2.0, tradeable: bool = True) -> dict:
    return {"conservative_net_edge_r": edge, "net_expectancy_r": max(edge, 0.01),
            "opportunity_score": min(1.0, max(0.0, edge)), "size_multiplier": mult,
            "risk_pct_requested": risk_pct, "tradeable": tradeable, "research_only": False,
            "hard_block_codes": []}


def _force_opportunities(monkeypatch, table: dict[str, dict]):
    """Her sembole sabit fırsat değerlendirmesi ver → sıralama ve nihai boyut deterministik olur."""
    def _fixed(self, decisions, briefs):
        for sym, d in decisions.items():
            if getattr(d, "is_actionable", False):
                d.opportunity = dict(table.get(sym) or _opp(0.1))
    monkeypatch.setattr(E.TradingEngineV3, "_assess_opportunities", _fixed)


def _force_triggers(monkeypatch, fires: dict[str, bool] | bool):
    """Tetiği doğrudan kontrol et (sentetik veriye bağlı kalmadan)."""
    def _fixed(self, b, direction, entry, entry_type):
        return bool(fires) if isinstance(fires, bool) else bool(fires.get(b.symbol, False))
    monkeypatch.setattr(E.TradingEngineV3, "_trigger_fired", _fixed)


def _force_final_risk_pct(monkeypatch, target_pct: float, edges: dict[str, float]):
    """Boyut çarpanını, her adayın NİHAİ riski tam `target_pct` olacak şekilde ayarla.

    Çarpan gerçek plan/uygulama fiyatından hesaplanır → test "nihai riski %X olan N aday"
    ifadesini fixture tuhaflıklarına bağlı kalmadan kurar. Çarpan 1.0'ı ASLA aşmaz (büyütme yok).
    """
    def _fixed(self, decisions, briefs):
        bmap = {b.symbol: b for b in briefs}
        eq = self.ledger2.starting_equity
        target = float(eq) * target_pct / 100.0
        for sym, d in decisions.items():
            if not getattr(d, "is_actionable", False):
                continue
            plan, b = d.active_plan, bmap.get(sym)
            exec_entry = self._execution_entry((b.price if b else 0) or plan.entry, d.direction)
            stop_frac = abs(exec_entry - plan.stop) / exec_entry
            plan_risk = float(plan.notional) * stop_frac
            mult = min(1.0, target / plan_risk) if plan_risk > 0 else 0.0
            d.opportunity = _opp(edges.get(sym, 0.5), mult=round(mult, 8))
    monkeypatch.setattr(E.TradingEngineV3, "_assess_opportunities", _fixed)


def _profile(max_total_open_risk_pct: float, risk_per_trade_pct: float = 2.0) -> dict:
    return {"risk_profiles": {"profile": "PAPER_RESEARCH",
                              "overrides": {"max_total_open_risk_pct": max_total_open_risk_pct,
                                            "risk_per_trade_pct": risk_per_trade_pct}}}


def _funnel(eng) -> dict:
    return json.loads((eng.cfg.state_path / "decision_funnel.json").read_text(encoding="utf-8"))


def _risk_log(eng) -> list[dict]:
    return json.loads((eng.cfg.state_path / "risk.json").read_text(encoding="utf-8"))["last_decisions"]


def _open_risk(eng) -> float:
    """Defterlerden GERÇEK toplam açık risk (rezervasyon değil)."""
    marks = {s: float(p.entry_avg) for s, p in eng.ledger2.positions.items()}
    return eng._portfolio_state(marks).total_open_risk_usdt


def _sym(desc: str) -> str:
    return desc.split(" ")[0]


# ============================================================================= 1) tetiklenmeyen güçlü aday
def test_1_untriggered_top_candidate_does_not_consume_capacity(tmp_path, monkeypatch):
    """En yüksek skorlu aday TETİKLEMİYOR, ikinci aday tetikliyor, kapasite yalnız BİR işleme yeter.

    Beklenen: ikinci aday AÇILIR ve birinci aday hiç risk tüketmez.
    (Eski davranışta chief, tetiklenmeyen ETH için %2 rezerve ediyor ve SOL `RISK_CAPACITY_BLOCKED`
    alıyordu.)
    """
    eng = _engine(tmp_path, monkeypatch, _profile(3.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {"ETH/USDT": _opp(0.90), "SOL/USDT": _opp(0.50)})
    _force_triggers(monkeypatch, {"ETH/USDT": False, "SOL/USDT": True})
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    assert [_sym(x) for x in s["opened"]] == ["SOL/USDT"], s["opened"]
    assert f["no_trigger"] >= 1 and f["trigger_fired"] == 1 and f["opened"] == 1
    assert f["risk_capacity_blocked"] == 0, "tetiklenmeyen aday kapasite tüketmemeli"
    # ETH hiçbir kapasite telemetrisini artırmadı.
    eth = next(r for r in _risk_log(eng) if r.get("symbol") == "ETH/USDT")
    assert eth["block_code"] == "NO_TRIGGER" and eth.get("final_risk_usdt") is None
    assert eth["risk_allowed"] is None, "tetiklenmeyen aday risk motoruna hiç gitmemeli"


# ============================================================================= 2) duplicate güçlü aday
def test_2_duplicate_top_candidate_does_not_consume_capacity(tmp_path, monkeypatch):
    """En güçlü aday DUPLICATE, ikinci aday benzersiz → ikinci aday açılır."""
    eng = _engine(tmp_path, monkeypatch, _profile(3.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {"ETH/USDT": _opp(0.90), "SOL/USDT": _opp(0.50)})
    _force_triggers(monkeypatch, True)
    monkeypatch.setattr(E.TradingEngineV3, "_signal_id",
                        lambda self, sym, market, d, plan, b: f"SIG::{sym}")
    eng._seen_signals = ["SIG::ETH/USDT"]                 # ETH bu sinyalle zaten açılmış
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    assert [_sym(x) for x in s["opened"]] == ["SOL/USDT"], s["opened"]
    assert f["duplicate_blocked"] == 1 and f["opened"] == 1
    assert f["risk_capacity_blocked"] == 0, "duplicate aday kapasite tüketmemeli"
    eth = next(r for r in _risk_log(eng) if r.get("symbol") == "ETH/USDT")
    assert eth["block_code"] == "DUPLICATE_SIGNAL" and eth["risk_allowed"] is None


# ============================================================================= 3) reddedilen ilk fill
def test_3_rejected_fill_releases_capacity_for_next_candidate(tmp_path, monkeypatch):
    """İlk aday ledger/borsa tarafından reddediliyor → kapasite sonraki adaya kalır ve o açılır."""
    eng = _engine(tmp_path, monkeypatch, _profile(3.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {"ETH/USDT": _opp(0.90), "SOL/USDT": _opp(0.50)})
    _force_triggers(monkeypatch, True)
    real_open, calls = eng.ledger2.open, {"n": 0}

    def _reject_first(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            eng.ledger2.last_reject_reason = "TEST_EXCHANGE_REJECT"
            return None
        return real_open(*a, **kw)
    monkeypatch.setattr(eng.ledger2, "open", _reject_first)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    assert f["exchange_rejected"] == 1 and f["opened"] == 1, f
    assert [_sym(x) for x in s["opened"]] == ["SOL/USDT"], s["opened"]
    eth = next(r for r in _risk_log(eng) if r.get("symbol") == "ETH/USDT")
    assert eth["block_code"] == "EXCHANGE_REJECTED"
    assert eth["risk_allowed"] is True, "reddedilen aday kapasiteyi ALDI ama AÇILMADI"
    # Reddedilen aday gerçek açık riske katkı yapmadı.
    assert len(eng.ledger2.positions) == 1
    assert _open_risk(eng) <= EQUITY * 3.0 / 100.0 + EPS


# ============================================================================= 4-5) 4 × %0.5 / %2 kapasite
def _four_small_trades(tmp_path, monkeypatch, spy: list | None = None):
    eng = _engine(tmp_path, monkeypatch, _profile(2.0), symbols=4, equity=EQUITY,
                  ledger_max_positions=8)
    edges = {"ETH/USDT": 0.9, "SOL/USDT": 0.8, "AVAX/USDT": 0.7, "LINK/USDT": 0.6}
    # Ham plan riski işlem başına %2 tavanındadır; NİHAİ risk çarpanla tam %0.5'e çekilir.
    _force_final_risk_pct(monkeypatch, 0.5, edges)
    _force_triggers(monkeypatch, True)
    if spy is not None:
        real_eval = eng.risk.evaluate

        def _spy(plan, state, ctx=None):
            spy.append(dict(plan))
            return real_eval(plan, state, ctx)
        monkeypatch.setattr(eng.risk, "evaluate", _spy)
    return eng, eng.tour(do_scan=False, obsidian=False, charts=False)


def test_4_four_half_percent_trades_fit_a_two_percent_budget(tmp_path, monkeypatch):
    """Nihai riski %0.5 olan dört aday, toplam %2 kapasiteye SIĞAR → 4/4 açılır."""
    eng, s = _four_small_trades(tmp_path, monkeypatch)
    f = _funnel(eng)["run"]
    assert f["opened"] == 4, (f, s["opened"])
    assert f["risk_capacity_blocked"] == 0 and f["capacity_approved"] == 4
    assert len(eng.ledger2.positions) == 4
    budget = EQUITY * 2.0 / 100.0
    assert _open_risk(eng) <= budget + EPS, "gerçek toplam açık risk tavanı aşmamalı"
    per = [r["final_risk_pct"] for r in _risk_log(eng) if r.get("final_risk_pct") is not None]
    assert len(per) == 4 and all(x == pytest.approx(0.5, abs=1e-4) for x in per), per
    assert sum(per) <= 2.0 + 1e-4, sum(per)
    # Ham plan riski gerçekten %2 tavanındaydı: küçültme olmasa 4 işlem ASLA sığmazdı.
    raw = [r["plan_notional"] / r["final_notional"] for r in _risk_log(eng)
           if r.get("final_notional")]
    assert all(x > 3.0 for x in raw), raw


def test_5_risk_engine_only_ever_sees_the_final_size(tmp_path, monkeypatch):
    """Ham plan riski %2 olsa bile RiskEngine YALNIZ nihai %0.5 değerini görür."""
    spy: list[dict] = []
    eng, _s = _four_small_trades(tmp_path, monkeypatch, spy=spy)
    assert spy, "risk.evaluate hiç çağrılmadı"
    budget_pct = 2.0
    log = [r for r in _risk_log(eng) if r.get("final_notional") is not None]
    assert len(spy) == len(log) == 4
    for seen, entry in zip(spy, log):
        # Risk motoruna giden notional NİHAİ boyuttur, ham plan boyutu DEĞİL.
        assert seen["notional"] == pytest.approx(entry["final_notional"], rel=1e-6)
        assert seen["notional"] < entry["plan_notional"] / 3.0
        stop_frac = abs(seen["entry"] - seen["stop"]) / seen["entry"]
        risk_pct = seen["notional"] * stop_frac / EQUITY * 100.0
        assert risk_pct == pytest.approx(0.5, abs=1e-4), risk_pct
        assert risk_pct < budget_pct / 3, "risk motoru ham %2'yi GÖRMEMELİ"
        # Risk motoru emrin gerçekten dolacağı fiyatı görür (plan fiyatını değil).
        assert seen["entry"] == pytest.approx(entry["execution_entry"], rel=1e-9)


# ============================================================================= 6) yumuşak ceza kapasiteye yansır
def test_6_soft_penalty_shrinks_the_risk_used_for_capacity(tmp_path, monkeypatch):
    """Yumuşak ceza riski küçülttüğünde kapasite KÜÇÜLTÜLMÜŞ riskle hesaplanır."""
    spy: list[dict] = []
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {"ETH/USDT": _opp(0.9, mult=0.40), "SOL/USDT": _opp(0.8, mult=1.0)})
    _force_triggers(monkeypatch, True)
    real_eval = eng.risk.evaluate

    def _spy(plan, state, ctx=None):
        spy.append(dict(plan))
        return real_eval(plan, state, ctx)
    monkeypatch.setattr(eng.risk, "evaluate", _spy)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    log = {r["symbol"]: r for r in _risk_log(eng) if r.get("final_notional") is not None}
    eth, sol = log["ETH/USDT"], log["SOL/USDT"]
    assert eth["size_multiplier_total"] == pytest.approx(0.40)
    assert eth["final_notional"] == pytest.approx(eth["plan_notional"] * 0.40, rel=1e-6)
    assert eth["final_risk_usdt"] < sol["final_risk_usdt"]
    seen = {p["symbol"]: p for p in spy}
    assert seen["ETH/USDT"]["notional"] == pytest.approx(eth["final_notional"], rel=1e-6)
    assert _open_risk(eng) <= EQUITY * 6.0 / 100.0 + EPS


# ============================================================================= 7) gerçek toplam risk tavanı
@pytest.mark.parametrize("cap_pct", [1.0, 2.0, 6.0])
def test_7_real_total_open_risk_never_exceeds_the_profile_ceiling(tmp_path, monkeypatch, cap_pct):
    eng = _engine(tmp_path, monkeypatch, _profile(cap_pct), symbols=4, equity=EQUITY,
                  ledger_max_positions=8)
    _force_opportunities(monkeypatch, {s: _opp(0.9 - i * 0.1, mult=0.3)
                                       for i, s in enumerate(eng.cfg.coins)})
    _force_triggers(monkeypatch, True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert _open_risk(eng) <= EQUITY * cap_pct / 100.0 + EPS, (cap_pct, _open_risk(eng))


# ============================================================================= 8-9) red team sert/yumuşak
def test_8_soft_only_red_team_keeps_the_plan_valid_and_shrinks_size():
    """Red team YALNIZ yumuşak ekonomik kod ürettiğinde plan GEÇERLİ kalır ve boyut küçülür."""
    from tradingbot.opportunity import assess
    hard, soft = review(RedTeamContext(direction="LONG", has_edge=False, oos_trades=5, corr_btc=0.9,
                                       same_direction_open=3, btc_regime="TREND_DOWN", stop_pct=10,
                                       atr_pct=1, funding_pct=0.08, listing_age_days=10,
                                       spread_pct=0.5, depth_usdt=10_000))
    assert hard == [], hard
    assert len(soft) >= 8, soft
    base = dict(symbol="ETH/USDT", side="LONG", setup="pullback", p_win=0.58, avg_win_r=1.6,
                avg_loss_r=1.0, sample_size=200, cost_pct_notional=0.2, stop_dist_pct=3.0)
    clean = assess(gates=GateLedger(), **base)
    g = GateLedger()
    for code in soft:
        g.penalise(code, E._SOFT_PENALTY_R[code], detail="red team")
    penalised = assess(gates=g, **base)
    assert penalised.hard_block_codes == [], "yumuşak kodlar SERT engel üretmemeli"
    assert penalised.tradeable or penalised.research_only
    assert 0 < penalised.size_multiplier < clean.size_multiplier, "boyut küçülmeli"
    assert g.soft_penalty_r() <= 0.60, "toplam yumuşak ceza üst sınırlı"


def test_9_real_hard_red_team_code_rejects_the_plan(tmp_path, monkeypatch):
    """Gerçek SERT red-team kodunda plan REDDEDİLİR (motor yolunda da işlem açılmaz)."""
    import test_coinhead as T
    from tradingbot.coinhead import CoinHead, CoinHeadConfig
    for ctx_kw, code in ((dict(data_stale=True), "STALE_DATA"),
                         (dict(kill_switch_active=True), "KILL_SWITCH_ACTIVE"),
                         (dict(spread_pct=2.0), "LIQUIDITY_UNTRADEABLE"),
                         (dict(liq_distance_pct=1.0, stop_pct=5.0), "LIQ_BEFORE_STOP"),
                         (dict(expected_cost_pct=1.0, expected_return_gross_pct=0.5), "COSTS_EXCEED_EDGE")):
        hard, _soft = review(RedTeamContext(direction="LONG", **ctx_kw))
        assert code in hard, (code, hard)
    # Uçtan uca: kill switch bayrağı → plan geçersiz → NO_TRADE_RED_TEAM_VETO
    cfg = CoinHeadConfig(consensus_threshold=0.05, min_confidence=0.05)
    fr = T.frames(seed=5, drift=0.0015)
    reports, brief = T.legacy(fr)
    inp = T._inputs(fr, reports, brief)
    inp.portfolio = {"kill_switch_active": True}
    d = CoinHead("ETH/USDT", cfg).decide(inp)
    assert not d.is_actionable and d.vetoes, d.verdict
    assert any("KILL_SWITCH_ACTIVE" in v for v in d.vetoes), d.vetoes


# ============================================================================= 10) bilinmeyen kapı kodu
def test_10_unknown_gate_code_is_fail_closed(tmp_path, monkeypatch):
    """Kayıtsız kod istisna üretir ve karar yolunda ASLA sessizce yumuşak kabul edilmez."""
    assert not is_known("KILL_SWITCH_ACTIV")                 # gerçekçi yazım hatası
    with pytest.raises(UnknownGateCode):
        gate_class("KILL_SWITCH_ACTIV")
    with pytest.raises(UnknownGateCode):
        GateLedger().block("KILL_SWITCH_ACTIV")
    with pytest.raises(UnknownGateCode):
        GateLedger().penalise("KILL_SWITCH_ACTIV", 0.05)
    # Motor yolunda: bilinmeyen soft_flag → aday SERT reddedilir, emir AÇILMAZ, motor çökmez.
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    real_predict = E.TradingEngineV3._assess_opportunities

    def _inject(self, decisions, briefs):
        for d in decisions.values():
            if getattr(d, "is_actionable", False):
                d.soft_flags = list(d.soft_flags) + ["KILL_SWITCH_ACTIV"]
        return real_predict(self, decisions, briefs)
    monkeypatch.setattr(E.TradingEngineV3, "_assess_opportunities", _inject)
    _force_triggers(monkeypatch, True)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s["opened"] == [], "kayıtsız kod fail-closed olmalı — işlem açılmamalı"
    for sym, d in eng.registry.last_decisions.items():
        opp = getattr(d, "opportunity", None) or {}
        if opp:
            assert "UNKNOWN_GATE_CODE" in opp.get("hard_block_codes", []), (sym, opp)


# ============================================================================= 11) sıfır çarpan
def test_11_zero_size_multiplier_never_opens_an_order(tmp_path, monkeypatch):
    """`size_multiplier == 0.0` KESİNLİKLE emir açmamalı (0.0 artık 1.0'a yuvarlanmıyor)."""
    assert _as_multiplier(None) == 1.0, "verilmedi → tam boyut"
    assert _as_multiplier(0.0) == 0.0, "açıkça 0.0 → sıfır (eski `or 1.0` hatası)"
    assert _as_multiplier(0.25) == 0.25 and _as_multiplier(3.0) == 1.0
    assert _as_multiplier("bozuk") == 0.0 and _as_multiplier(float("nan")) == 0.0
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    # tradeable=True ama nihai çarpan 0.0 → ekonomi kapısını geçer, emir yine de AÇILMAZ.
    _force_opportunities(monkeypatch, {s: _opp(0.9, mult=0.0) for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    assert s["opened"] == [] and f["opened"] == 0
    assert f["size_multiplier_zero"] >= 1 and f["capacity_approved"] == 0
    assert len(eng.ledger2.positions) == 0


# ============================================================================= 12-13) profil sözleşmeleri
def test_12_paper_has_no_daily_or_per_run_trade_count_quota(tmp_path, monkeypatch):
    from tradingbot.coinhead.chief import ChiefConfig
    p = resolve_profile("PAPER_RESEARCH")
    assert p.max_open_positions is None and p.max_positions_per_market is None
    c = ChiefConfig()
    assert c.max_new_positions_per_run is None and c.daily_trade_cap is None
    eng = _engine(tmp_path, monkeypatch, symbols=2, equity=EQUITY)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)
    assert f["daily_trade_cap"] is None and f["per_run_trade_cap"] is None
    for rel in ("engine_v3.py", "coinhead/chief.py", "risk/engine.py"):
        text = (Path(__file__).resolve().parents[1] / "tradingbot" / rel).read_text(encoding="utf-8")
        for bad in FORBIDDEN_QUOTA_CODES:
            assert bad not in text, f"{rel}: yasak kota kodu {bad}"


@pytest.mark.parametrize("name,expect", [("TESTNET", (0.5, 2.0, 3, 3, 2)),
                                         ("SHADOW_LIVE", (0.5, 2.0, 3, 3, 2)),
                                         ("LIVE_LIMITED", (0.25, 1.0, 2, 2, 1)),
                                         ("LIVE", (0.5, 2.0, 3, 3, 2))])
def test_13_testnet_and_live_limits_are_unchanged(name, expect):
    """TESTNET/SHADOW_LIVE/LIVE adet ve risk tavanları DEĞİŞMEDİ (yalnız PAPER None kullanır)."""
    p = resolve_profile(name)
    assert (p.risk_per_trade_pct, p.max_total_open_risk_pct, p.max_open_positions,
            p.max_positions_per_market, p.futures_max_leverage) == expect


# ============================================================================= 14) 100 ardışık benzersiz işlem
def test_14_hundred_sequential_unique_trades_are_never_counter_blocked():
    """Her işlem kapanıp riski serbest bıraktığında 100 benzersiz fırsat sayaçla ENGELLENMEZ."""
    from tradingbot.risk import RiskEngine, build_state
    eng = RiskEngine(resolve_profile("PAPER_RESEARCH"))
    allowed, codes = 0, set()
    for i in range(100):
        state = build_state(equity=EQUITY, starting_equity=EQUITY, available=EQUITY,
                            used_margin=0.0, positions=[], history=[])   # önceki işlem kapandı
        plan = {"symbol": f"S{i:03d}/USDT", "market_type": "USDM_PERP", "direction": "LONG",
                "entry": 100.0, "stop": 97.0, "targets": [106.0], "notional": 1_000.0,
                "margin": 1_000.0, "leverage": 1, "amount_type": "NOTIONAL", "expected_r": 2.0,
                "min_notional": 5.0}
        rd = eng.evaluate(plan, state)
        allowed += 1 if rd.allowed else 0
        codes.update(rd.reasons)
    assert allowed == 100, f"sabit sayaç engelledi: {allowed}/100, kodlar={codes}"
    assert not (codes & set(FORBIDDEN_QUOTA_CODES))
    assert "MAX_POSITIONS" not in codes and "MAX_POSITIONS_MARKET" not in codes


# ============================================================================= 15) negatif edge → 0 doğru
def test_15_all_negative_edge_means_zero_trades_and_no_minimum_quota(tmp_path, monkeypatch):
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=4, equity=EQUITY,
                  ledger_max_positions=8)
    _force_opportunities(monkeypatch, {s: _opp(-0.5, mult=0.0, tradeable=False)
                                       for s in eng.cfg.coins})
    _force_triggers(monkeypatch, True)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    assert s["opened"] == [] and f["opened"] == 0
    assert f["negative_edge_blocked"] >= 1
    assert _funnel(eng)["daily_trade_cap"] is None       # asgari kota da YOK
    assert len(eng.ledger2.positions) == 0


# ============================================================================= 16) hiçbir eleme kapasite tüketmez
@pytest.mark.parametrize("scenario", ["no_trigger", "duplicate", "fill_rejected"])
def test_16_eliminated_candidates_never_touch_capacity_telemetry(tmp_path, monkeypatch, scenario):
    """Tetiklemeyen / duplicate / fill-reddedilen adaylar `risk_used`, gerçek açık risk ve
    kapasite telemetrisini ARTIRMAZ."""
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=2, equity=EQUITY)
    _force_opportunities(monkeypatch, {"ETH/USDT": _opp(0.90), "SOL/USDT": _opp(0.50)})
    if scenario == "no_trigger":
        _force_triggers(monkeypatch, {"ETH/USDT": False, "SOL/USDT": True})
    else:
        _force_triggers(monkeypatch, True)
    if scenario == "duplicate":
        monkeypatch.setattr(E.TradingEngineV3, "_signal_id",
                            lambda self, sym, market, d, plan, b: f"SIG::{sym}")
        eng._seen_signals = ["SIG::ETH/USDT"]
    if scenario == "fill_rejected":
        real_open, calls = eng.ledger2.open, {"n": 0}

        def _reject_first(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                eng.ledger2.last_reject_reason = "TEST_REJECT"
                return None
            return real_open(*a, **kw)
        monkeypatch.setattr(eng.ledger2, "open", _reject_first)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    assert f["risk_capacity_blocked"] == 0, f
    # Gerçek açık risk YALNIZ açılan pozisyonlardan gelir.
    expected = sum(abs(float(p.entry_avg) - float(p.stop)) * float(p.qty)
                   for p in eng.ledger2.positions.values() if p.stop)
    assert _open_risk(eng) == pytest.approx(expected, rel=1e-3, abs=1e-6)
    assert len(eng.ledger2.positions) == f["opened"]
    # Chief kapasite RAPORU rezervasyon değil, gerçek açık risktir.
    heads = json.loads((eng.cfg.state_path / "coin_heads.json").read_text(encoding="utf-8"))
    assert heads["chief"]["exposure"]["authoritative_risk_reservation"] is False


# ============================================================================= 17) huni tutarlılığı
def test_17_funnel_stages_are_consistent_after_opens(tmp_path, monkeypatch):
    """ranked ≥ trigger_fired ≥ … ≥ capacity_approved = opened + exchange_rejected."""
    eng = _engine(tmp_path, monkeypatch, _profile(6.0), symbols=4, equity=EQUITY,
                  ledger_max_positions=8)
    edges = {s: 0.9 - i * 0.1 for i, s in enumerate(eng.cfg.coins)}
    _force_opportunities(monkeypatch, {s: _opp(e, mult=0.3) for s, e in edges.items()})
    _force_triggers(monkeypatch, {s: (i != 0) for i, s in enumerate(eng.cfg.coins)})
    real_open, calls = eng.ledger2.open, {"n": 0}

    def _reject_second(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            eng.ledger2.last_reject_reason = "TEST_REJECT"
            return None
        return real_open(*a, **kw)
    monkeypatch.setattr(eng.ledger2, "open", _reject_second)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    f = _funnel(eng)["run"]
    for key in ("ranked", "trigger_fired", "duplicate_blocked", "capacity_approved",
                "exchange_rejected", "opened"):
        assert key in f, key
    assert f["ranked"] == 4 and f["no_trigger"] == 1 and f["trigger_fired"] == 3
    assert f["capacity_approved"] == f["opened"] + f["exchange_rejected"]
    assert f["exchange_rejected"] == 1 and f["opened"] == 2
    assert f["ranked"] >= f["trigger_fired"] >= f["capacity_approved"] >= f["opened"]
    assert len(eng.ledger2.positions) == f["opened"]
    assert _open_risk(eng) <= EQUITY * 6.0 / 100.0 + EPS
