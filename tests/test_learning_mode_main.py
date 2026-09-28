"""ÖĞRENME MODU — ANA BOT (2026-09-28, öğrenme modu; L1 aşama C1).

Gerçek `TradingEngineV3._execute` → `RiskEngine.evaluate` → `FuturesLedgerV2.open` sırası sürülür (ağsız harness,
`tests/test_engine_v3.py`). İki sınıf test:

* KAPALI PARİTE: bölüm yok / `enabled: false` / etkin ama kapı düşmüş (askıda) → karar, red kodu, defter ve gölge
  kayıtları BİREBİR aynı; öğrenme anahtarı hiçbir yere sızmaz.
* AÇIK DAVRANIŞ: TOTAL_OPEN_RISK / INSUFFICIENT_MARGIN ile reddedilen aday açılır; rejim/mum/yapı kapıları gölgede
  (hüküm özellikte kalır); NEGATIVE_NET_EDGE keşif etiketiyle açılır; kaldıraç tabanı yalnız izinli küme için 2x;
  kill switch ve DUPLICATE_SIGNAL yine durdurur; aynı sinyal anahtarı için tek karşı-olgusal.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as E  # noqa: E402

import tradingbot.engine_v3 as M  # noqa: E402
from tradingbot.accounting import AmountType, SizeSpec, TickData  # noqa: E402
from tradingbot.coinhead.schema import Verdict  # noqa: E402
from tradingbot.core import utc_now  # noqa: E402
from tradingbot.learning_mode import LEVERAGE_FALLBACK_REASON, SIZE_BUMP, SIZE_SLOT  # noqa: E402
from tradingbot.risk.leverage import LeverageDecision  # noqa: E402

SYMS = ["ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]
ALL_OVERRIDES = {"regime_gate_shadow": True, "candle_veto_shadow": True, "structures_entry_shadow": ["main"],
                 "economics_exploration": True, "leverage_confidence_fallback": True}


def _lm(enabled: bool = True, overrides: dict | None = None, **main) -> dict:
    return {"learning_mode": {"enabled": enabled, "books": {"main": {"enabled": True, "slots": 20, **main}},
                              "strategy_overrides": dict(ALL_OVERRIDES if overrides is None else overrides)}}


def _eng(tmp_path: Path, monkeypatch, lm: dict | None = None, extra: dict | None = None, *, n: int = 4,
         suspended: bool = False):
    ov = dict(extra or {})
    if lm is not None:
        ov.update(lm)
    eng = E._engine(tmp_path, monkeypatch, ov, symbols=n, equity=100.0)
    monkeypatch.setattr(eng, "_trigger_fired", lambda *a, **k: True)
    if suspended:                               # çalışma anı mod kapısı düştü (canlı emir yolu açıldı) → ASKIDA
        monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: True)
    eng._lm_refresh()
    return eng


def _cands(eng, syms, *, direction: str = "SHORT", notional: float = 40.0, lev: int = 2, stop_pct: float = 5.0,
           opp: dict | None = None, verdict=None):
    briefs = [eng.runner.run_symbol(s) for s in syms]
    decisions, marks = {}, {}
    sgn = 1.0 if direction == "SHORT" else -1.0
    for b in briefs:
        px = float(b.price)
        plan = SimpleNamespace(valid=True, entry=px, stop=px * (1 + sgn * stop_pct / 100.0),
                               targets=[px * (1 - sgn * 0.07), px * (1 - sgn * 0.10)], notional=notional, margin=notional / lev,
                               size=SimpleNamespace(leverage=lev), expected_r=1.5, entry_type="pullback", entry_trigger="t",
                               time_horizon_bars=12)
        v = verdict or (Verdict.FUTURES_SHORT if direction == "SHORT" else Verdict.FUTURES_LONG)
        d = SimpleNamespace(is_actionable=True, active_plan=plan, verdict=v, direction=direction, specialist_reports=[],
                            coin_head_id="ch", regime="TREND_DOWN", consensus_score=-0.5, consensus_confidence=0.6,
                            confidence_calibrated=0.6, data_freshness={}, dissent=[], vetoes=[], expected_r=1.5,
                            expected_cost=0.1, model_versions={}, p_win=0.5, opportunity=(dict(opp) if opp else None),
                            to_dict=lambda include_reports=False: {})
        decisions[b.symbol] = d
        marks[b.symbol] = TickData(last=Decimal(str(px)), mark=Decimal(str(px)))
    chief = SimpleNamespace(priority=list(syms), permission={s: {"allow": True, "reason": None} for s in syms},
                            to_dict=lambda: {}, market_risk_mode="NÖTR")
    return decisions, chief, briefs, marks


def _prefill(eng, *, notional: float, lev: int, stop_pct: float, n: int = 3, now=None):
    """Adaylardan BAĞIMSIZ sembollerde doğrudan defter pozisyonu (risk bütçesi / marj tüketimi senaryosu)."""
    for i, sym in enumerate(("XRP/USDT", "DOGE/USDT", "TRX/USDT", "BNB/USDT")[:n]):
        px = Decimal("1.0")
        pos = eng.ledger2.open(sym, "LONG", px, SizeSpec(Decimal(str(notional)), AmountType.NOTIONAL, lev),
                               stop=px * Decimal(str(1 - stop_pct / 100.0)), targets=[px * Decimal("1.2")], now=now or utc_now())
        assert pos is not None, eng.ledger2.last_reject_reason


def _log(risk_log, sym) -> dict:
    return [e for e in risk_log if e.get("symbol") == sym][-1]


def _main_cfs(eng) -> list:
    return [t for t in eng.shadow.trades if t.book == "main"]


# ============================================================================ KAPALI PARİTE
def _scenario(eng, now):
    _prefill(eng, notional=40.0, lev=2, stop_pct=5.0, n=3, now=now)       # 3 × 2 USDT risk = %6 bütçe DOLU
    decisions, chief, briefs, marks = _cands(eng, SYMS[:2])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    pos = {s: p.to_dict() for s, p in eng.ledger2.positions.items()}
    shadows = [(t.plan_id, t.symbol, t.direction, list(t.reason_not_opened), t.to_dict().keys()) for t in eng.shadow.trades]
    return opened, json.dumps(risk_log, sort_keys=True, default=str), json.dumps(pos, sort_keys=True, default=str), \
        shadows, dict(eng._funnel)


def test_off_parity_absent_disabled_and_suspended_are_bit_identical(tmp_path, monkeypatch):
    now = utc_now().replace(microsecond=0)
    runs = {}
    # askıdaki örnek, AYNI mod durumundaki (canlı emir yolu açık) öğrenmesiz motorla karşılaştırılır: mod geçişinin
    # kendisi araştırma katmanını da salt-okunur yapar (`MODE_GATE:...`), bu öğrenmeden bağımsızdır.
    for name, lm, susp in (("absent", None, False), ("disabled", _lm(enabled=False), False),
                           ("absent_live_path", None, True), ("suspended", _lm(enabled=True), True)):
        eng = _eng(tmp_path / name, monkeypatch, lm, suspended=susp)
        assert eng._lm_main is None and eng._lm_ovr == {}
        runs[name] = _scenario(eng, now)
        if name == "suspended":
            st = eng.lm.status()
            assert st["enabled"] and not st["active"]
            assert st["reason"] == "LEARNING_MODE_SUSPENDED:LIVE_ORDER_PATH_ENABLED"
            assert eng.lm.book("main") is None and eng.lm.override("regime_gate_shadow", "BASE") == "BASE"
        if name.startswith("absent"):
            assert eng.risk_learning is None and not eng.lm.enabled
    base = runs["absent"]
    assert base[0] == [], "senaryo: %6 bütçe dolu → baseline hiçbir adayı açmaz"
    assert runs["disabled"] == base
    assert runs["suspended"] == runs["absent_live_path"]
    rl = json.loads(base[1])
    assert all(e.get("block_code") == "RISK_CAPACITY_BLOCKED" and "TOTAL_OPEN_RISK" in e["risk_reasons"] for e in rl)
    assert not any(k in e for e in rl for k in ("learning", "learning_fit", "counterfactual"))
    assert not any(k in base[4] for k in M._LM_FUNNEL_KEYS)
    # öğrenme AKTİF: aynı senaryo farklı davranır (parite testi boş değil)
    eng = _eng(tmp_path / "on", monkeypatch, _lm())
    on = _scenario(eng, now)
    assert len(on[0]) == 2 and on[1] != base[1]


def test_off_tour_writes_no_learning_keys(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, _lm(enabled=False), symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    assert "learning_mode" not in json.loads((st / "health.json").read_text(encoding="utf-8"))
    assert "learning_mode" not in json.loads((st / "risk.json").read_text(encoding="utf-8"))
    fun = json.loads((st / "decision_funnel.json").read_text(encoding="utf-8"))
    assert not any(k in fun["run"] or k in fun["rolling_24h"] for k in M._LM_FUNNEL_KEYS)
    rows = [json.loads(x) for x in (st / "decision_journal.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows and not any("learning" in r or "counterfactual" in r for r in rows)
    for p in eng.ledger2.positions.values():
        assert "learning" not in p.meta and "learning" not in p.features


# ============================================================================ KAPASİTE
@pytest.mark.parametrize("case", ["TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN"])
def test_learning_opens_candidate_that_baseline_blocks_for_capacity(tmp_path, monkeypatch, case):
    now = utc_now().replace(microsecond=0)
    runs = {}
    for name, lm in (("off", None), ("on", _lm())):
        eng = _eng(tmp_path / name, monkeypatch, lm)
        if case == "TOTAL_OPEN_RISK":
            _prefill(eng, notional=40.0, lev=2, stop_pct=5.0, n=3, now=now)      # risk 6/6, marj 60/100
        else:
            _prefill(eng, notional=170.0, lev=2, stop_pct=0.5, n=1, now=now)     # risk 0.85, marj 85/100
        decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
        opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
        runs[name] = (eng, opened, _log(risk_log, SYMS[0]))
    _, off_opened, off_e = runs["off"]
    assert off_opened == []
    if case == "TOTAL_OPEN_RISK":
        assert off_e["block_code"] == "RISK_CAPACITY_BLOCKED" and "TOTAL_OPEN_RISK" in off_e["risk_reasons"]
    else:
        assert off_e["block_code"] == "EXCHANGE_REJECTED" and off_e["exec_reject"] == "INSUFFICIENT_MARGIN"
    eng, on_opened, on_e = runs["on"]
    assert len(on_opened) == 1 and SYMS[0] in eng.ledger2.positions
    pos = eng.ledger2.positions[SYMS[0]]
    tags = pos.meta["learning"]
    assert tags == pos.features["learning"] == on_e["learning"]
    assert tags["size_rule"] == SIZE_SLOT and tags["slots"] == 20 and tags["book"] == "main"
    assert case in tags["learning_unlocked_by"], tags
    # %0,5 hedef risk (E=100 → ≤ 0.5 USDT), 2 × stop likidasyon tamponu, slot marjı ≤ 0.95·E/K
    assert tags["risk_usdt"] <= 0.5 + 1e-9 and 0 < tags["risk_fraction_of_budget"] <= 1.0 + 1e-9
    assert float(pos.isolated_margin) <= 0.95 * 100 / 20 + 1e-6
    assert (1.0 / pos.leverage - 0.004) >= 2.0 * 0.05 - 1e-9
    assert on_e["risk_allowed"] is True and eng._funnel["learning_opened"] == 1 and eng._funnel["learning_unlocked"] == 1
    # öğrenme RiskEngine'i taban profilin KOPYASI; kill switch AYNI nesne
    assert eng.risk_learning.profile.max_total_open_risk_pct == 100.0 and eng.profile.max_total_open_risk_pct == 6.0
    assert eng.risk_learning.ks is eng.killswitch
    from tradingbot.risk.profiles import PROFILES
    assert PROFILES["PAPER_RESEARCH"].max_total_open_risk_pct == 6.0
    assert PROFILES["PAPER_RESEARCH"].max_spot_allocation_pct == eng.profile.max_spot_allocation_pct
    assert all(p is not eng.risk_learning.profile for p in PROFILES.values())
    # defterin KALICI `allow_shrink` özniteliği değişmedi
    eng.ledger2.save(eng.ledger_path)
    assert json.loads(eng.ledger_path.read_text(encoding="utf-8")).get("allow_shrink", False) is False


def test_spot_long_uses_learning_profile_and_slot_sizing(tmp_path, monkeypatch):
    """A14: ana botun öğrenme profilinde spot tahsisi 100; boyut `fit_size` (kaldıraç 1, serbest spot nakdi)."""
    res = {}
    for name, lm in (("off", None), ("on", _lm())):
        eng = _eng(tmp_path / name, monkeypatch, lm)
        decisions, chief, briefs, marks = _cands(eng, SYMS[:1], direction="LONG", lev=1, verdict=Verdict.SPOT_LONG)
        opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
        res[name] = (eng, opened, _log(risk_log, SYMS[0]))
    off = res["off"][2]
    assert res["off"][1] == [] and "SPOT_ALLOCATION" in off["risk_reasons"]           # 40 > %30 × 100
    eng, opened, e = res["on"]
    assert len(opened) == 1 and "SPOT" in opened[0], e
    assert eng.risk_learning.profile.max_spot_allocation_pct == 100.0
    assert e["learning"]["leverage"] == 1 and e["learning"]["risk_usdt"] <= 0.5 + 1e-9
    assert "SPOT_ALLOCATION" in e["learning"]["learning_unlocked_by"]
    assert SYMS[0] in eng.spot2.positions() and not eng.ledger2.positions


def test_min_notional_bump_rounds_up_to_step_and_tags_bump(tmp_path, monkeypatch):
    """Stop geniş (%20) → slot notional'ı (0.5/0.2 = 2.5) min-notional'ın altında; %2 tavan içinde çıkarılır."""
    eng = _eng(tmp_path, monkeypatch, _lm())
    now = utc_now().replace(microsecond=0)
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1], stop_pct=20.0, lev=1)
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, now)
    e = _log(risk_log, SYMS[0])
    assert opened, e
    pos = eng.ledger2.positions[SYMS[0]]
    f = eng.filters.get(SYMS[0])
    assert pos.meta["learning"]["size_rule"] == SIZE_BUMP and e["learning_fit"]["size_rule"] == SIZE_BUMP
    assert float(pos.qty) * float(pos.entry_avg) >= float(f.min_notional) - 1e-9
    assert pos.amount_type is AmountType.QUANTITY and str(pos.qty) == e["learning_fit"].get("qty", str(pos.qty))
    assert float(pos.qty) == pytest.approx(0.010)                   # 5/535 → adıma (0.001) YUKARI: 0.010
    assert pos.meta["learning"]["risk_usdt"] <= 2.0 + 1e-9                     # %2 sert tavan (E=100)
    assert eng._funnel["min_notional_bumped"] == 1
    # baseline da açardı (2% riskle, 10 USDT) → politika işlemi: kilit açan kapı YOK
    assert pos.meta["learning"]["learning_unlocked_by"] == [] and eng._funnel["learning_unlocked"] == 0


def test_min_notional_bump_above_hard_cap_is_refused_with_counterfactual(tmp_path, monkeypatch):
    """%40 stop: adıma YUKARI yuvarlanmış min-notional riski %2 tavanı aşar → red (MIN_ORDER_CONFLICT) + karşı-olgusal."""
    eng = _eng(tmp_path, monkeypatch, _lm())
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1], stop_pct=40.0, lev=1)
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    e = _log(risk_log, SYMS[0])
    assert opened == [] and e["risk_reasons"] == ["MIN_ORDER_CONFLICT"] and e["learning_fit"]["why"] == "RISK_CAP"
    assert [t.reason_not_opened[0] for t in _main_cfs(eng)] == ["MIN_ORDER_CONFLICT"]


# ============================================================================ STRATEJİ KAPILARI (S1/S2/S3/S5)
def _btc_up(eng):
    """BTC 1d kapanışları EMA200'ün ÜSTÜNDE (rejim UP) → r1 SHORT'u engeller."""
    fr = dict(eng.runner.last_frames[SYMS[0]])
    d1 = fr["1d"].copy()
    d1["ema200"] = d1["close"] * 0.95
    fr["1d"] = d1
    eng.runner.last_frames["BTC/USDT"] = fr


def _regime_run(tmp_path, monkeypatch, lm):
    eng = _eng(tmp_path, monkeypatch, lm, {"entry_selectivity": {"regime_gate_mode": "ENFORCE",
                                                                   "regime_gate_variant": "r1_long_only_uptrend"}})
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])                  # SHORT aday
    _btc_up(eng)
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    return eng, opened, _log(risk_log, SYMS[0])


def test_short_passes_regime_gate_in_shadow_and_verdict_is_a_feature(tmp_path, monkeypatch):
    _, off_opened, off_e = _regime_run(tmp_path / "off", monkeypatch, None)
    assert off_opened == [] and off_e["block_code"] == "REGIME_VETO:R1_SHORT_BLOCKED"
    eng, opened, e = _regime_run(tmp_path / "on", monkeypatch, _lm())
    assert len(opened) == 1, e
    rg = e["regime_gate"]
    assert rg["mode"] == "SHADOW" and rg["blocks"] is False and rg["verdict"]["ok"] is False
    assert rg["learning_override"] == {"from": "ENFORCE", "to": "SHADOW", "would_block": True}
    pos = eng.ledger2.positions[SYMS[0]]
    assert pos.features["regime_gate"]["verdict"] == {"ok": False, "reason": "R1_SHORT_BLOCKED"}
    assert pos.features["regime_gate"]["regime"] == "UP"
    assert "REGIME_VETO:R1_SHORT_BLOCKED" in pos.meta["learning"]["learning_unlocked_by"]
    # config DEĞİŞMEDİ: ezme yalnız okuma anında (askıya alınınca kendiliğinden döner)
    assert eng.cfg.v3.entry_selectivity.regime_gate_mode == "ENFORCE"


def test_regime_gate_without_override_stays_enforce_and_writes_one_counterfactual(tmp_path, monkeypatch):
    eng, opened, e = _regime_run(tmp_path, monkeypatch, _lm(overrides={}))
    assert opened == [] and e["block_code"] == "REGIME_VETO:R1_SHORT_BLOCKED"
    cfs = _main_cfs(eng)
    assert len(cfs) == 1 and cfs[0].reason_not_opened[0] == "REGIME_VETO:R1_SHORT_BLOCKED"
    assert cfs[0].label_kind == "TARGET_STOP_TIME" and cfs[0].learning_unlocked is False
    assert cfs[0].features["regime_gate"]["verdict"]["ok"] is False
    assert e["counterfactual"]["recorded"] is True


def test_candle_veto_is_shadow_while_learning_is_active(tmp_path, monkeypatch):
    import tradingbot.candle_confirmation as CC
    monkeypatch.setattr(CC, "evaluate_variant", lambda v, **k: (False, "C3_TEST_OPPOSITE"))
    ex = {"entry_selectivity": {"candle_confirmation_mode": "ENFORCE", "candle_confirmation_variant": "c3_4h_veto"}}
    res = {}
    for name, lm in (("off", None), ("on", _lm())):
        eng = _eng(tmp_path / name, monkeypatch, lm, ex)
        decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
        opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
        res[name] = (eng, opened, _log(risk_log, SYMS[0]))
    assert res["off"][1] == [] and res["off"][2]["block_code"] == "CANDLE_VETO:C3_TEST_OPPOSITE"
    eng, opened, e = res["on"]
    assert len(opened) == 1
    assert e["candle_confirmation"]["mode"] == "SHADOW" and e["candle_confirmation"]["learning_override"]["would_block"]
    pos = eng.ledger2.positions[SYMS[0]]
    assert pos.features["candle_confirmation"]["verdict"]["ok"] is False
    assert "CANDLE_VETO:C3_TEST_OPPOSITE" in pos.meta["learning"]["learning_unlocked_by"]


def _struct_dec(reason: str):
    return ({"bot": "main", "action": "WAIT", "reason_code": reason, "pattern_ids": [], "primary": None,
             "text_tr": "bekle"}, {})


@pytest.mark.parametrize("reason,opens", [("OPPOSING_CONFIRMED", True),
                                          ("STRUCTURE_FRAME_MARKET_MISMATCH:SPOT", False)])
def test_structures_entry_shadow_for_main_keeps_data_identity_block(tmp_path, monkeypatch, reason, opens):
    import tradingbot.structures.bots as SB
    monkeypatch.setattr(SB, "main_entry_decision", lambda **k: _struct_dec(reason))
    ex = {"structures": {"enabled": True, "main": "ENFORCE"}}
    eng = _eng(tmp_path, monkeypatch, _lm(), ex)
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    e = _log(risk_log, SYMS[0])
    assert eng._structure_mode_main() == "ENFORCE"                           # yönetim (stop sıkılaştırma) ENFORCE kalır
    if opens:
        assert len(opened) == 1
        pos = eng.ledger2.positions[SYMS[0]]
        assert pos.features["structure"]["shadow"] is True                    # yapı girişin dayanağı SAYILMAZ
        assert "STRUCTURE:OPPOSING_CONFIRMED" in pos.meta["learning"]["learning_unlocked_by"]
    else:
        assert opened == [] and e["block_code"] == "STRUCTURE:" + reason
        assert _main_cfs(eng) == []                                           # veri kimliği → karşı-olgusal YOK


def test_structures_off_learning_blocks_entry(tmp_path, monkeypatch):
    import tradingbot.structures.bots as SB
    monkeypatch.setattr(SB, "main_entry_decision", lambda **k: _struct_dec("OPPOSING_CONFIRMED"))
    eng = _eng(tmp_path, monkeypatch, None, {"structures": {"enabled": True, "main": "ENFORCE"}})
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert opened == [] and _log(risk_log, SYMS[0])["block_code"] == "STRUCTURE:OPPOSING_CONFIRMED"


NEG = {"tradeable": False, "research_only": False, "hard_block_codes": [], "size_multiplier": 0.0,
       "conservative_net_edge_r": -0.2, "net_expectancy_r": -0.1}
RES = {"tradeable": False, "research_only": True, "hard_block_codes": [], "size_multiplier": 0.25,
       "conservative_net_edge_r": -0.05, "net_expectancy_r": 0.05}


@pytest.mark.parametrize("opp,code,tag", [(NEG, "NEGATIVE_NET_EDGE", "NEG_EDGE"), (RES, "RESEARCH_SIZE_ONLY", "RESEARCH_SIZE")])
def test_economics_exploration_opens_with_tag(tmp_path, monkeypatch, opp, code, tag):
    res = {}
    for name, lm in (("off", None), ("on", _lm())):
        eng = _eng(tmp_path / name, monkeypatch, lm)
        decisions, chief, briefs, marks = _cands(eng, SYMS[:1], opp=opp)
        opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
        res[name] = (eng, opened, _log(risk_log, SYMS[0]))
    assert res["off"][1] == [] and res["off"][2]["block_code"] == code
    eng, opened, e = res["on"]
    assert len(opened) == 1, e
    pos = eng.ledger2.positions[SYMS[0]]
    assert pos.meta["learning"]["exploration"] == tag and pos.features["exploration"] == tag
    assert code in pos.meta["learning"]["learning_unlocked_by"]
    assert eng._funnel["learning_exploration"] == 1


def test_economics_hard_codes_still_block_and_write_no_counterfactual(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, _lm())
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1], opp=dict(NEG, hard_block_codes=["ZERO_STOP_DISTANCE"]))
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert opened == [] and _log(risk_log, SYMS[0])["block_code"] == "NEGATIVE_NET_EDGE"
    assert _main_cfs(eng) == []


def test_negative_edge_without_exploration_override_writes_counterfactual(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, _lm(overrides={}))
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1], opp=NEG)
    opened, _ = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert opened == [] and [t.reason_not_opened[0] for t in _main_cfs(eng)] == ["NEGATIVE_NET_EDGE"]


def test_short_and_futures_only_penalties_are_recorded_not_applied(tmp_path, monkeypatch):
    ex = {"futures_v3": {"short_penalty_r": 0.5}, "universe": {"futures_only_penalty_r": 0.35}}
    out = {}
    for name, lm in (("off", None), ("on", _lm())):
        eng = _eng(tmp_path / name, monkeypatch, lm, ex)
        monkeypatch.setattr(eng.spot_listing, "is_listed", lambda s: False)
        b = SimpleNamespace(symbol="ETH/USDT", dont_list=[])
        plan = SimpleNamespace(valid=True, stop_pct=3.0, expected_cost_pct=0.2, expected_r=1.8, entry_type="pullback",
                               market_type="futures", soft_flags=[])
        d = SimpleNamespace(is_actionable=True, active_plan=plan, direction="SHORT", regime="TREND_DOWN", p_win=0.55,
                            soft_flags=[], opportunity=None)
        eng._assess_opportunities({"ETH/USDT": d}, [b])
        out[name] = d.opportunity
    codes = lambda o: {s["code"] for s in o["soft_evidence"]}  # noqa: E731
    assert {"SHORT_SEGMENT_PENALTY", "FUTURES_ONLY_SEGMENT_PENALTY"} <= codes(out["off"])
    assert not {"SHORT_SEGMENT_PENALTY", "FUTURES_ONLY_SEGMENT_PENALTY"} & codes(out["on"])
    assert "learning_penalties" not in out["off"]
    lp = out["on"]["learning_penalties"]
    assert lp["applied"] is False and lp["would_apply_r"] == pytest.approx(0.85)
    assert out["on"]["conservative_net_edge_r"] > out["off"]["conservative_net_edge_r"]


# ============================================================================ KALDIRAÇ TABANI (B1/S7)
def _lev_run(tmp_path, monkeypatch, fails, lm):
    eng = _eng(tmp_path, monkeypatch, lm, {"leverage": {"enabled": True}})
    monkeypatch.setattr(M, "select_leverage", lambda ctx, cfg=None: LeverageDecision(
        0, reasons=["BASE_GATES_FAILED"], blocked_higher=list(fails), tier_checks={"base": list(fails)}))
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    return eng, opened, _log(risk_log, SYMS[0])


@pytest.mark.parametrize("fails,allowed", [
    (["DEPTH_BELOW_BASE"], True),
    (["STOP_TOO_FAR_FOR_LEVERAGE", "DEPTH_BELOW_BASE"], True),
    (["CONFIDENCE_BELOW_BASE"], True),                      # S7 ezmesi açık
    (["SPREAD_ABOVE_BASE"], False),
    (["LIQ_BUFFER_TOO_THIN_FOR_BASE"], False),
    (["DATA_STALE"], False),
    (["DEPTH_BELOW_BASE", "CONFIDENCE_UNKNOWN"], False),
])
def test_leverage_fallback_only_for_allowed_set(tmp_path, monkeypatch, fails, allowed):
    eng, opened, e = _lev_run(tmp_path, monkeypatch, fails, _lm())
    if allowed:
        assert len(opened) == 1, e
        pos = eng.ledger2.positions[SYMS[0]]
        assert pos.leverage == 2 and pos.meta["learning"]["leverage_fallback"] == LEVERAGE_FALLBACK_REASON
        assert pos.meta["leverage_decision"]["learning_fallback"]["reason"] == LEVERAGE_FALLBACK_REASON
        assert pos.meta["leverage_decision"]["leverage"] == 2
    else:
        assert opened == [] and e["block_code"] == "LEVERAGE_GATE_BLOCKED"
        cfs = _main_cfs(eng)
        if "DATA_STALE" in fails:
            assert cfs == []                                     # veri bayat → karşı-olgusal YOK
        else:
            assert [t.reason_not_opened[0] for t in cfs] == ["LEVERAGE_GATE_BLOCKED"]


def test_confidence_fallback_needs_its_override(tmp_path, monkeypatch):
    ov = dict(ALL_OVERRIDES, leverage_confidence_fallback=False)
    eng, opened, e = _lev_run(tmp_path, monkeypatch, ["CONFIDENCE_BELOW_BASE"], _lm(overrides=ov))
    assert opened == [] and e["block_code"] == "LEVERAGE_GATE_BLOCKED"


def test_leverage_gate_off_learning_blocks(tmp_path, monkeypatch):
    _, opened, e = _lev_run(tmp_path, monkeypatch, ["DEPTH_BELOW_BASE"], None)
    assert opened == [] and e["block_code"] == "LEVERAGE_GATE_BLOCKED"


# ============================================================================ KALANLAR (kill switch / duplicate / CF)
def test_kill_switch_still_blocks_and_is_shared(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, _lm())
    eng.killswitch.trip("MANUAL", "test")
    decisions, chief, briefs, marks = _cands(eng, SYMS[:2])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert opened == [] and not eng.ledger2.positions
    for s in SYMS[:2]:
        assert "KILL_SWITCH_ACTIVE" in _log(risk_log, s)["risk_reasons"]
    assert sorted(t.symbol for t in _main_cfs(eng)) == sorted(SYMS[:2])


def test_duplicate_signal_still_blocks_without_counterfactual(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, _lm())
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    opened, _ = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert len(opened) == 1
    opened2, risk_log2 = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert opened2 == [] and _log(risk_log2, SYMS[0])["block_code"] == "DUPLICATE_SIGNAL"
    assert _main_cfs(eng) == [] and len(eng.ledger2.positions) == 1


def test_one_counterfactual_per_signal_key_across_repeated_tours(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, _lm())
    eng.killswitch.trip("MANUAL", "test")
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    for i in range(4):                                       # aynı 4h barında 4 tur (her turun run_id'si farklı)
        eng.run_id = "run_%d" % i
        eng._execute(decisions, chief, briefs, None, marks, utc_now())
    cfs = _main_cfs(eng)
    b, d = briefs[0], decisions[SYMS[0]]
    sig = eng._signal_id(SYMS[0], "USDM_PERP", d, d.active_plan, b)
    assert len(cfs) == 1 and cfs[0].plan_id == cfs[0].signal_key == sig             # P1: plan_id = sinyal anahtarı
    # yeni bar → yeni sinyal → ikinci kayıt
    b.last_bar_4h = "2099-01-01 00:00:00+00:00"
    eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert len(_main_cfs(eng)) == 2
    # diskte kalıcı ve eski ShadowTrade şemasıyla yüklenebilir
    from tradingbot.learn.shadow import ShadowBook
    again = [t for t in ShadowBook(eng.shadow.path).trades if t.book == "main"]
    assert len(again) == 2 and all(t.features for t in again)


def test_exchange_reject_under_learning_is_open_failed_with_counterfactual(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, _lm())
    seen: dict = {}

    def _reject(self, **kw):
        seen.update(kw)
        self.ledger2.last_reject_reason = "MIN_NOTIONAL"
        return None
    monkeypatch.setattr(M.TradingEngineV3, "_execute_futures_entry", _reject)
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    opened, risk_log = eng._execute(decisions, chief, briefs, None, marks, utc_now())
    e = _log(risk_log, SYMS[0])
    assert opened == [] and e["block_code"] == "EXCHANGE_REJECTED" and e["exec_reject"] == "MIN_NOTIONAL"
    assert "learning" not in e and e["learning_fit"]["ok"] is True                   # işlem etiketi YOK, boyut kaydı VAR
    assert seen["allow_shrink"] is True and seen["meta"]["learning"]["size_rule"] == SIZE_SLOT
    assert [t.reason_not_opened[0] for t in _main_cfs(eng)] == ["MIN_NOTIONAL"]
    from tradingbot.learn.decision_journal import OPEN_FAILED, classify_outcome
    assert classify_outcome(e, is_actionable=True, has_valid_plan=True)[0] == OPEN_FAILED


def test_counterfactual_switch_off_writes_nothing(tmp_path, monkeypatch):
    lm = _lm()
    lm["learning_mode"]["counterfactual"] = False
    eng = _eng(tmp_path, monkeypatch, lm)
    eng.killswitch.trip("MANUAL", "test")
    decisions, chief, briefs, marks = _cands(eng, SYMS[:1])
    eng._execute(decisions, chief, briefs, None, marks, utc_now())
    assert _main_cfs(eng) == []


# ============================================================================ B5: baş planı vetolanmaz
def test_size_position_bump_is_opt_in_and_capped():
    from tradingbot.risk.engine import LEARNING_MIN_NOTIONAL_BUMP, size_position
    kw = dict(equity=100.0, risk_pct=2.0, entry=100.0, stop=97.0, min_notional=50.0, max_leverage=1,
              max_position_pct=30.0, liq_buffer_mult=3.0, requested_leverage=1)
    base = size_position(**kw)
    assert not base.ok and base.reason == "NO_TRADE_MIN_ORDER_CONFLICT"                # varsayılan: bit-aynı
    bumped = size_position(**kw, min_notional_bump_cap_pct=2.0)
    assert bumped.ok and bumped.reason == LEARNING_MIN_NOTIONAL_BUMP and bumped.notional == 50.0
    assert bumped.risk_usdt == pytest.approx(1.5)
    too_risky = size_position(**dict(kw, stop=95.0), min_notional_bump_cap_pct=2.0)      # 50 × %5 = 2.5 > 2
    assert not too_risky.ok and too_risky.reason == "NO_TRADE_MIN_ORDER_CONFLICT"


def test_head_does_not_veto_min_order_conflict_only_when_flagged():
    from tradingbot.coinhead.head import CoinHead, CoinHeadConfig, CoinHeadInputs
    from tradingbot.coinhead.schema import PlanSize, TradePlanV3

    def _plan():
        return TradePlanV3(market_type="futures", direction="LONG", entry_zone=(100.0, 100.0), stop=97.0,
                           targets=[106.0], size=PlanSize(0.0, "NOTIONAL", 1), valid=True)
    head = CoinHead("BTC/USDT", CoinHeadConfig(equity_usdt=100.0, risk_pct=2.0))
    p0 = _plan()
    head._size(p0, "futures", CoinHeadInputs(frames={}, filters={"futures": {"min_notional": 50.0, "max_leverage": 1}}))
    assert p0.valid is False and "MIN_ORDER_CONFLICT" in p0.invalid_reason
    assert not getattr(p0, "learning_min_notional_bump", False)
    p1 = _plan()
    head._size(p1, "futures", CoinHeadInputs(frames={}, filters={"futures": {
        "min_notional": 50.0, "max_leverage": 1, "learning_min_notional_bump_cap_pct": 2.0}}))
    assert p1.valid is True and p1.notional == 50.0 and p1.learning_min_notional_bump is True
    assert "learning_min_notional_bump" not in p1.to_dict()                              # şema değişmedi


# ============================================================================ uçtan uca tur
def test_learning_tour_tags_journal_health_and_funnel(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, _lm(), symbols=2)
    s = eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    h = json.loads((st / "health.json").read_text(encoding="utf-8"))
    assert h["learning_mode"]["active"] is True and h["learning_mode"]["reason"] == "ACTIVE"
    assert eng._lm_main is not None and eng._lm_main.name == "main" and eng._lm_main.slots == 20
    rj = json.loads((st / "risk.json").read_text(encoding="utf-8"))
    assert rj["learning_mode"]["risk_profile"]["max_total_open_risk_pct"] == 100.0
    assert rj["learning_mode"]["risk_profile"]["risk_per_trade_cap_pct"] == 2.0
    assert rj["profile"]["max_total_open_risk_pct"] == 6.0                   # taban profil AYNEN (PROFILES değişmedi)
    fun = json.loads((st / "decision_funnel.json").read_text(encoding="utf-8"))
    assert all(k in fun["run"] and k in fun["rolling_24h"] for k in M._LM_FUNNEL_KEYS)
    rows = [json.loads(x) for x in (st / "decision_journal.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if not s["opened"]:
        pytest.skip("fikstür bu turda pozisyon açmadı")
    assert fun["run"]["learning_opened"] == len(s["opened"])
    opened_rows = [r for r in rows if r.get("trade_id")]
    assert opened_rows and all(r["learning"]["size_rule"] in (SIZE_SLOT, SIZE_BUMP, "SHRUNK_TO_MARGIN")
                               and "learning_unlocked_by" in r["learning"] for r in opened_rows)
    for p in eng.ledger2.positions.values():
        assert p.meta["learning"]["book"] == "main" and p.features["learning"]["slots"] == 20
    mem = [json.loads(x) for x in (st / "trade_memory.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any((m.get("features") or {}).get("learning") for m in mem if m.get("kind") == "entry")
