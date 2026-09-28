# -*- coding: utf-8 -*-
"""ÖĞRENME MODU L1 — MOTOR BAĞLANTISI, CONFIG, PANEL, KARNE (2026-09-28, öğrenme modu; aşama C2).

* Motor her kâğıt deftere (`StrategyBook.step`), Box zamanlayıcısına ve formasyon defterine (`set_learning`) o turun
  değişmez `BookLearning` görünümünü YALNIZ öğrenme aktifken verir; `symbols: universe` defterleri (C9) ana giriş evreniyle
  genişler. Askıda → herkes baseline (görünüm None), `health.json` LEARNING_MODE_SUSPENDED:<neden> taşır.
* Anahtar kapalıyken (bölüm yok / `enabled: false`) `step` çağrısı, fiyat kapsamı, Box kurulumu ve formasyon defteri
  BİT-AYNI (ek anahtar geçilmez, `set_learning` çağrılmaz, durum dosyası yazılmaz).
* `learning_mode_since` ilk aktif anda BİR KEZ `state/learning_mode.json`a yazılır; yeniden başlatma onu sıfırlamaz.
* `config.yaml` CONTRACT değerleriyle yüklenir ve doğrulanır; panel bölümle/bölümsüz çizilir; karne öncesi/sonrası ve
  politika / öğrenme-ekstra / karşı-olgusal ayrımını yapar (karşı-olgusal P&L'e girmez).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_engine_v3 as E  # noqa: E402
from test_dashboard_terminal_v1 import _client, _pos, _setup, _trade, _w  # noqa: E402
from test_funding_five_ledgers_v2 import BOX_PARAMS  # noqa: E402

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec  # noqa: E402
from tradingbot.agents.manager import ChiefAgent, learning_capacity_rule  # noqa: E402
from tradingbot.config import load_config  # noqa: E402
from tradingbot.config_v3 import load_v3  # noqa: E402
from tradingbot.core import ConfigError, utc_now  # noqa: E402
from tradingbot.dashboard import learning_view as lv  # noqa: E402
from tradingbot.dashboard.state import StateReader  # noqa: E402
from tradingbot.learning_mode import BookLearning  # noqa: E402

UTC = timezone.utc
EU = ["ETH/USDT", "SOL/USDT", "AVAX/USDT", "LINK/USDT"]
D4_OWN = ["ETH/USDT"]
#: öğrenme bölümü (M2 bilerek KAPALI: açık anahtarda bile kapalı defter görünüm almaz)
LM_ON = {"enabled": True,
         "books": {"main": {"enabled": True, "slots": 20},
                   "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
                   "m2_tsmom28": {"enabled": False},
                   "b1_box_fade": {"enabled": True, "slots": 20, "leverage_max": 4, "min_stop_pct": 0.32},
                   "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
                   "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3}},
         "strategy_overrides": {"structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"]}}
LM_OFF = dict(LM_ON, enabled=False)
#: `step`in anahtar kapalıyken aldığı anahtarlar (HEAD ile aynı çağrı)
BASE_STEP_KEYS = {"symbols", "frames_by_symbol", "marks", "marks_f", "now", "provenance_by_symbol", "data_gaps"}


def _ov(lm: dict | None) -> dict:
    ov = {"strategy_paper": {"enabled": True, "name": "t2_trend_regime",
                             "extra": [{"name": "m2_tsmom28", "state_dir": "strategy_paper_m2"},
                                       {"name": "b1_box_fade", "state_dir": "strategy_paper_box", "rule_params": dict(BOX_PARAMS)},
                                       {"name": "d4_donchian_20_10", "state_dir": "strategy_paper_trend4h",
                                        "symbols": list(D4_OWN), "rule_params": {"leverage": 1}}]},
          "pattern_trader": {"enabled": True, "protocol": "momentum_4h_v3", "scan_seconds": 3600.0,
                             "universe_refresh_minutes": 0.0},
          "entry_universe": {"enabled": True, "symbols": list(EU)},
          "chart_analysis": {"enabled": False}, "news": {"enabled": False}}
    if lm is not None:
        ov["learning_mode"] = copy.deepcopy(lm)
    return ov


def _eng(tmp_path: Path, monkeypatch, lm: dict | None, *, suspended: bool = False):
    monkeypatch.setattr("tradingbot.pattern_trader.scheduler.PatternScanner.start", lambda self: None)
    monkeypatch.setattr("tradingbot.box_timer.BoxTimer.start", lambda self: None)
    eng = E._engine(tmp_path, monkeypatch, _ov(lm), symbols=4, equity=200.0)
    if suspended:                    # çalışma anı mod kapısı düştü (canlı emir yolu açıldı) → ASKIDA
        monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: True)
    return eng


def _spy_books(eng, monkeypatch) -> tuple[dict, list]:
    """Her defterin `step` çağrısını kaydeder; fiyat kapsamı (`_paper_marks`) da kaydedilir (ağ yok)."""
    calls: dict[str, dict] = {}
    scopes: list[list[str]] = []
    for b in eng.strategy_books:
        monkeypatch.setattr(b, "step", lambda _n=b.name, **kw: calls.__setitem__(_n, kw))
    monkeypatch.setattr(eng, "_paper_marks", lambda scope, now=None: (scopes.append(list(scope)) or ({}, {}, {})))
    monkeypatch.setattr(eng, "_paper_closed_bars", lambda scope, marks_f: {})
    return calls, scopes


def _tour_books(eng, monkeypatch):
    calls, scopes = _spy_books(eng, monkeypatch)
    eng.run_id = "rid"
    eng._strategy_paper_tour(list(EU), {}, {}, {}, False, datetime.now(UTC))
    return calls, scopes


# ============================================================================ motor → defterler
def test_each_book_gets_its_own_view_only_while_learning_is_active(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, LM_ON)
    eng._lm_refresh()
    assert eng.lm.on and set(eng._lm_books) == {"t2_trend_regime", "b1_box_fade", "d4_donchian_20_10", "pattern_trader"}
    calls, scopes = _tour_books(eng, monkeypatch)
    assert set(calls) == {"t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10"}
    t2, m2, box, d4 = (calls[n] for n in ("t2_trend_regime", "m2_tsmom28", "b1_box_fade", "d4_donchian_20_10"))
    assert isinstance(t2["learning"], BookLearning) and t2["learning"].on and t2["learning"].name == "t2_trend_regime"
    assert t2["learning"] is eng._lm_books["t2_trend_regime"], "turun anlık görüntüsü (tur ortasında değişmez)"
    assert t2["learning"].slots == 40 and t2["learning"].leverage_max == 4 and t2["structures_entry_shadow"] is True
    assert m2["learning"] is None and m2["structures_entry_shadow"] is False, "defter kapalı → görünüm yok (baseline)"
    assert box["learning"].min_stop_pct == 0.32 and box["structures_entry_shadow"] is False
    # C9: D4 kendi listesi ∪ ana giriş evreni; diğerleri evren (liste boş) — genişleme fiyat kapsamına da girer
    assert d4["learning"].symbols == "universe" and d4["symbols"] == D4_OWN + [s for s in EU if s not in D4_OWN]
    assert t2["symbols"] == EU and m2["symbols"] == EU
    assert set(scopes[0]) == set(EU)


def test_suspended_gate_gives_every_book_the_baseline_path_and_health_says_why(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, LM_ON, suspended=True)
    eng._lm_refresh()
    st = eng.lm.status()
    assert st["enabled"] and not st["active"] and st["reason"] == "LEARNING_MODE_SUSPENDED:LIVE_ORDER_PATH_ENABLED"
    assert eng._lm_books == {} and eng._lm_main is None
    calls, _ = _tour_books(eng, monkeypatch)
    for name, kw in calls.items():
        assert kw["learning"] is None and kw["structures_entry_shadow"] is False, name
    assert calls["d4_donchian_20_10"]["symbols"] == D4_OWN, "askıda C9 genişlemesi YOK"
    eng._lm_publish(utc_now())
    assert not (eng.cfg.state_path / "learning_mode.json").exists(), "hiç aktif olmadı → since yazılmaz"


@pytest.mark.parametrize("lm", [None, LM_OFF], ids=["absent", "disabled"])
def test_off_parity_step_calls_scope_and_box_timer_are_unchanged(tmp_path, monkeypatch, lm):
    eng = _eng(tmp_path, monkeypatch, lm)
    eng._lm_refresh()
    calls, scopes = _tour_books(eng, monkeypatch)
    for name, kw in calls.items():
        assert set(kw) == BASE_STEP_KEYS, (name, set(kw) - BASE_STEP_KEYS)
    assert calls["d4_donchian_20_10"]["symbols"] == D4_OWN
    ref = _eng(tmp_path / "ref", monkeypatch, None)
    ref._lm_refresh()
    rcalls, rscopes = _tour_books(ref, monkeypatch)
    assert scopes == rscopes and {n: kw["symbols"] for n, kw in calls.items()} == {n: kw["symbols"] for n, kw in rcalls.items()}
    eng.ensure_box_timer()
    assert eng.box_timer.learning is None and "learning" not in eng.box_timer.status()
    eng._lm_publish(utc_now())
    assert not (eng.cfg.state_path / "learning_mode.json").exists()
    assert eng.runner.chief.learning_rule is None


def test_box_timer_gets_the_learning_switch_and_refreshes_it_itself(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, LM_ON)
    eng._lm_refresh()
    eng.ensure_box_timer()
    t = eng.box_timer
    assert t.learning is eng.lm, "zamanlayıcı kapıyı her 5m geçişinde kendisi tazeler"
    bl, es = t._learning_pass()
    assert bl is not None and bl.min_stop_pct == 0.32 and es is False
    assert t.status()["learning"]["active"] is True and t.status()["learning"]["min_stop_pct"] == 0.32
    monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: True)
    bl, _ = t._learning_pass()
    assert bl is None and t.status()["learning"]["reason"].startswith("LEARNING_MODE_SUSPENDED:")


def test_pattern_book_receives_view_universe_and_gate_each_tour(tmp_path, monkeypatch):
    eng = _eng(tmp_path, monkeypatch, LM_ON)
    eng._lm_refresh()
    pb = eng.pattern_book
    assert pb.learning_capable
    now = utc_now()
    eng._pattern_trader_tour(now)
    assert pb._lm_next is eng._lm_books["pattern_trader"] and pb._lm_universe_next == frozenset(EU)
    assert pb._lm_gate() == (True, "ACTIVE"), "kapı `LearningMode.active` (tarayıcı iş parçacığından sorulur)"
    assert pb.begin_cycle(now) is True and pb.learning_on
    assert set(EU) <= set(pb.entry_symbols()), "S9: protokol coinleri ∪ ana giriş evreni"
    # askıya alınınca SONRAKİ formasyon turu baseline
    monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: True)
    eng._lm_refresh()
    eng._pattern_trader_tour(now)
    assert pb._lm_next is None and pb.begin_cycle(now) is False
    assert pb.learning_summary()["reason"] == "LEARNING_MODE_SUSPENDED:LIVE_ORDER_PATH_ENABLED"
    assert pb.entry_symbols() == pb.allowed_symbols


@pytest.mark.parametrize("lm", [None, LM_OFF], ids=["absent", "disabled"])
def test_pattern_book_is_not_touched_when_the_switch_is_off(tmp_path, monkeypatch, lm):
    eng = _eng(tmp_path, monkeypatch, lm)
    seen: list = []
    monkeypatch.setattr(eng.pattern_book, "set_learning", lambda *a, **k: seen.append((a, k)))
    eng._lm_refresh()
    eng._pattern_trader_tour(utc_now())
    assert seen == [] and not eng.pattern_book.learning_capable
    doc = json.loads((eng.cfg.state_path / eng.pattern_book.summary_file).read_text(encoding="utf-8"))
    assert "learning" not in doc


# ============================================================================ sağlık + learning_mode_since + şef metni
def _main_only(lm: dict | None) -> dict:
    return {"learning_mode": copy.deepcopy(lm)} if lm is not None else {}


def test_tour_publishes_active_state_and_records_since_once(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, _main_only(LM_ON), symbols=2)
    st = eng.cfg.state_path
    eng.tour(do_scan=False, obsidian=False, charts=False)
    h = json.loads((st / "health.json").read_text(encoding="utf-8"))
    rec = json.loads((st / "learning_mode.json").read_text(encoding="utf-8"))
    assert h["learning_mode"]["active"] is True and h["learning_mode"]["reason"] == "ACTIVE"
    assert rec["since"] and h["learning_mode"]["learning_mode_since"] == rec["since"]
    assert eng.runner.chief.learning_rule and "ÖĞRENME MODU" in eng.runner.chief.learning_rule
    ag = json.loads((st / "agents.json").read_text(encoding="utf-8"))
    rules = " ".join((ag.get("chief") or {}).get("rules") or [])
    assert "ÖĞRENME MODU" in rules and "≤ %6" not in rules, "şef brifingi öğrenme bütçesini söyler"
    # yeniden başlatma: since SIFIRLANMAZ (dosya ezilmez)
    eng2 = E._engine(tmp_path, monkeypatch, _main_only(LM_ON), symbols=2)
    eng2._lm_refresh()
    eng2._lm_publish(utc_now() + timedelta(days=3))
    assert json.loads((st / "learning_mode.json").read_text(encoding="utf-8"))["since"] == rec["since"]
    assert eng2._lm_since() == rec["since"]


def test_tour_health_says_suspended_with_the_reason(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, _main_only(LM_ON), symbols=2)
    monkeypatch.setattr(eng.mode_state, "is_live_order_path_enabled", lambda: True)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    h = json.loads((eng.cfg.state_path / "health.json").read_text(encoding="utf-8"))
    assert h["learning_mode"]["enabled"] is True and h["learning_mode"]["active"] is False
    assert h["learning_mode"]["reason"] == "LEARNING_MODE_SUSPENDED:LIVE_ORDER_PATH_ENABLED"
    assert h["learning_mode"]["learning_mode_since"] is None
    assert eng.runner.chief.learning_rule is None


@pytest.mark.parametrize("lm", [None, LM_OFF], ids=["absent", "disabled"])
def test_tour_off_parity_health_has_no_learning_key_and_no_state_file(tmp_path, monkeypatch, lm):
    eng = E._engine(tmp_path, monkeypatch, _main_only(lm), symbols=2)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    st = eng.cfg.state_path
    assert "learning_mode" not in json.loads((st / "health.json").read_text(encoding="utf-8"))
    assert not (st / "learning_mode.json").exists()
    rules = json.loads((st / "agents.json").read_text(encoding="utf-8"))["chief"]["rules"]
    assert any("en fazla 3 pozisyon; toplam riske atılan sermaye ≤ %6" in r for r in rules)


def test_chief_capacity_rule_text():
    ch = ChiefAgent(max_concurrent=3)
    base = ch.decide([]).rules
    assert "Aynı anda en fazla 3 pozisyon; toplam riske atılan sermaye ≤ %6" in base
    ch.learning_rule = learning_capacity_rule(slots=20, risk_pct=0.5, max_total_open_risk_pct=100.0)
    rules = ch.decide([]).rules
    assert rules[1] == ch.learning_rule and "en fazla 20 eşzamanlı pozisyon" in rules[1] and "%0,5" in rules[1]
    assert "toplam açık risk ≤ %100" in rules[1] and len(rules) == len(base)
    ch.learning_rule = None
    assert ch.decide([]).rules == base


# ============================================================================ config.yaml
CONTRACT_SECTION = {
    "enabled": True, "risk_per_trade_pct": 0.5, "max_total_open_risk_pct": 100, "margin_reserve_pct": 5,
    "liq_buffer_mult": 2.0, "min_notional_bump": True, "counterfactual": True, "counterfactual_max_pending": 2000,
    "books": {"main": {"enabled": True, "slots": 20},
              "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
              "m2_tsmom28": {"enabled": True, "slots": 40, "leverage_max": 4},
              "b1_box_fade": {"enabled": True, "slots": 20, "leverage_max": 4, "min_stop_pct": 0.32},
              "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
              "c4_candle_variations": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
              "c4s_candle_variations_strict": {"enabled": False},
              "pattern_trader": {"enabled": True, "slots": 30, "leverage_max": 3}},
    "strategy_overrides": {"regime_gate_shadow": True, "candle_veto_shadow": True,
                           "structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"],
                           "economics_exploration": True, "leverage_confidence_fallback": True}}


def test_config_yaml_section_is_exactly_the_contract_and_validates(monkeypatch):
    monkeypatch.delenv("TRADINGBOT_LEARNING_MODE", raising=False)
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["learning_mode"] == CONTRACT_SECTION
    cfg = load_config(ROOT / "config.yaml")
    lm = cfg.v3.learning_mode
    assert lm.enabled is True and lm.risk_per_trade_pct == 0.5 and lm.max_total_open_risk_pct == 100
    assert lm.books["b1_box_fade"].min_stop_pct == 0.32 and lm.books["c4s_candle_variations_strict"].enabled is False
    assert lm.books["d4_donchian_20_10"].symbols == "universe" and lm.books["pattern_trader"].slots == 30
    assert not any("learning_mode" in w for w in cfg.v3.warnings)
    txt = (ROOT / "config.yaml").read_text(encoding="utf-8")
    block = txt[txt.index("# ---- ÖĞRENME MODU"):txt.index("learning_mode:\n")]
    for needle in ("YALNIZ PAPER", "enabled: false", "1/4", "R (kâr/zarar ÷ ilk stop riski)"):
        assert needle in block, needle
    assert raw["pattern_trader"]["max_open_positions"] == 3, "askıda defter düzeyi tavan olarak kalır"


@pytest.mark.parametrize("path,value", [(("mode", "mode"), "TESTNET"), (("mode", "mode"), "SHADOW_LIVE"),
                                        (("execution", "gateway"), "binance_futures_testnet"),
                                        (("execution", "testnet_enabled"), True), (("risk_profiles", "profile"), "TESTNET")])
def test_config_yaml_learning_section_refuses_non_paper(monkeypatch, path, value):
    """Depodaki config.yaml PAPER dışına taşınırsa ConfigError ÖĞRENME kapısından gelir (bölüm kapalıyken aynı config
    yüklenir). Dinamik kaldıraç kendi PAPER kapısıyla önce düşmesin diye mod denemesinde kapatılır."""
    monkeypatch.delenv("TRADINGBOT_LEARNING_MODE", raising=False)
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw.setdefault(path[0], {})[path[1]] = value
    raw["leverage"]["enabled"] = False
    with pytest.raises(ConfigError, match="LEARNING_MODE_PAPER_ONLY"):
        load_v3(copy.deepcopy(raw))
    raw["learning_mode"]["enabled"] = False
    assert load_v3(raw).learning_mode.enabled is False


def test_config_yaml_env_off_switch(monkeypatch):
    monkeypatch.setenv("TRADINGBOT_LEARNING_MODE", "off")
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert load_v3(raw).learning_mode.enabled is False


# ============================================================================ panel
LEARN_FEATS = {"learning": {"book": "t2_trend_regime", "size_rule": "BUMP_MIN_NOTIONAL", "slots": 40, "risk_pct": 0.5,
                            "risk_fraction_of_budget": 1.3, "learning_unlocked_by": ["TOTAL_OPEN_RISK"]},
               "in_lab_universe": False}
POLICY_FEATS = {"learning": {"book": "main", "size_rule": "SLOT", "learning_unlocked_by": [], "exploration": "NEG_EDGE"},
                "exploration": "NEG_EDGE"}


def _learning_state(tmp_path: Path, *, active: bool = True, reason: str = "ACTIVE") -> None:
    sp = _pos("SOL/USDT", "LONG", 100.0, 95.0, 101.0, qty=0.2) | {"id": "F901", "features": LEARN_FEATS}
    mp = _pos("ETH/USDT", "SHORT", 2000.0, 2100.0, 1990.0, qty=0.005) | {"id": "F902", "features": POLICY_FEATS}
    hist = [_trade("AVAX/USDT", "LONG", 30.0, 31.0, 0.4, "hedef1") | {"features": LEARN_FEATS}]
    _setup(tmp_path, main_pos=[mp], sp_pos=[sp], sp_hist=hist)
    doc = json.loads((tmp_path / "strategy_paper.json").read_text(encoding="utf-8"))
    doc["learning"] = {"active": active, "since": "2026-09-28T08:00:00+00:00",
                       "book": {"name": "t2_trend_regime", "slots": 40, "leverage_max": 4, "risk_pct": 0.5},
                       "counters": {"opened": 3, "learning_unlocked": 2, "min_notional_bumped": 1, "shrunk_to_margin": 0},
                       "counterfactual": {"book": "t2_trend_regime", "pending": 4, "labeled": 7, "dropped": 0,
                                          "expired": 0, "recorded_total": 11, "max_pending": 2000}}
    _w(tmp_path, "strategy_paper.json", doc)
    _w(tmp_path, "health.json", {"state": "HEALTHY", "at": "2026-09-28T09:00:00+00:00",
                                 "learning_mode": {"enabled": True, "active": active, "reason": reason,
                                                   "since": "2026-09-28T08:00:00+00:00", "books": ["main", "t2_trend_regime"],
                                                   "learning_mode_since": "2026-09-28T08:00:00+00:00"}})
    _w(tmp_path, "learning_mode.json", {"since": "2026-09-28T08:00:00+00:00"})
    if active:
        _w(tmp_path, "risk.json", {"learning_mode": {"active": True, "book": {"name": "main", "slots": 20, "risk_pct": 0.5,
                                                                             "reserve_pct": 5.0},
                                                     "risk_profile": {"name": "PAPER_RESEARCH", "max_total_open_risk_pct": 100.0,
                                                                      "risk_per_trade_cap_pct": 2.0},
                                                     "equity_basis": 100.0, "max_total_open_risk_usdt": 100.0,
                                                     "slot_margin_usdt": 4.75}})
    if active:
        _w(tmp_path, "decision_funnel.json", {"run": {"opened": 2, "actionable": 5, "learning_opened": 2, "learning_unlocked": 1,
                                                      "counterfactual_recorded": 3},
                                              "rolling_24h": {"learning_opened": 9, "counterfactual_recorded": 12}})
    _w(tmp_path, "shadow_book.json", {"trades": [
        {"id": "s1", "plan_id": "sig1", "symbol": "ETH/USDT", "book": "main", "outcome": {"r_multiple": 1.2}},
        {"id": "s2", "plan_id": "sig2", "symbol": "SOL/USDT", "book": "main", "outcome": None},
        {"id": "s3", "plan_id": "plan-old", "symbol": "SOL/USDT", "outcome": {"r_multiple": -1.0}}]})


def test_dashboard_without_the_section_renders_exactly_as_before(tmp_path):
    _setup(tmp_path, main_pos=[_pos("ETH/USDT", "LONG", 2000.0, 1900.0, 2010.0, qty=0.005)],
           sp_pos=[_pos("SOL/USDT", "LONG", 100.0, 95.0, 101.0)], sp_hist=[_trade("AVAX/USDT", "LONG", 30.0, 31.0, 0.4, "hedef1")])
    st = StateReader(tmp_path)
    assert lv.learning_status(st) is None and lv.section_html(st) == "" and lv.book_badge_for(st, "strategy_paper") == ""
    assert lv.banner_html(None) == "" and lv.tags_html({"structure": {}}) == "" and lv.tags_html(None) == ""
    c = _client(tmp_path)
    for url in ("/", "/trades?book=strategy_paper&tab=open", "/trades?book=strategy_paper&tab=closed", "/risk", "/health",
                "/portfolio/strategy"):
        html = c.get(url).text
        for marker in ("ÖĞRENME MODU", "lmban", "lmtags", "lmbook", "Öğrenme modu — defterler", "Öğrenmede açılan",
                       "Öğrenme risk bütçesi"):
            assert marker not in html, (url, marker)
    assert c.get("/api/learning-mode").json() == {"enabled": False, "status": None, "books": []}


def test_dashboard_with_active_learning_shows_banner_badges_tags_and_counts(tmp_path):
    _learning_state(tmp_path)
    c = _client(tmp_path)
    home = c.get("/?book=strategy_paper").text
    assert "ÖĞRENME MODU AÇIK — yalnız PAPER" in home and "Öğrenme modu — defterler" in home
    assert "slot 1/40" in home, "seçili hesabın rozeti: açık/K"
    assert "karşı-olgusal 11 kayıt · 7 etiketli · 4 bekleyen" in home
    assert "öğrenme-ekstra: TOTAL_OPEN_RISK" in home and "min. tutara çıkarıldı" in home and "lab evreni dışı" in home
    closed = c.get("/trades?book=strategy_paper&tab=closed").text
    assert "öğrenme-ekstra: TOTAL_OPEN_RISK" in closed
    main_open = c.get("/trades?book=main&tab=open").text
    assert "politika" in main_open and "keşif: negatif kenar" in main_open
    risk = c.get("/risk").text
    assert "Öğrenme risk bütçesi (ana bot)" in risk and "1 / 20" in risk
    health = c.get("/health").text
    assert "learning_mode_since" in health and "AKTİF" in health
    assert "Öğrenmede açılan" in health and "24s: 9" in health and "Karşı-olgusal kayıt" in health
    api = c.get("/api/learning-mode").json()
    rows = {r["book_id"]: r for r in api["books"]}
    assert api["enabled"] is True and api["status"]["since"] == "2026-09-28T08:00:00+00:00"
    assert rows["strategy_paper"]["slots_k"] == 40 and rows["strategy_paper"]["open_extra"] == 1
    assert rows["strategy_paper"]["counterfactual"]["recorded_total"] == 11
    assert rows["main"]["counterfactual"] == {"pending": 1, "labeled": 1, "dropped": None, "recorded_total": 2}, \
        "ana bot: yalnız book=main kayıtları (öğrenme öncesi gölge sayılmaz)"
    assert rows["strategy_paper"]["margin_frac"] == pytest.approx(0.2 * 100.0 / 100.0)


def test_dashboard_suspended_banner_names_the_reason(tmp_path):
    _learning_state(tmp_path, active=False, reason="LEARNING_MODE_SUSPENDED:LIVE_ORDER_PATH_ENABLED")
    html = _client(tmp_path).get("/").text
    assert "ÖĞRENME MODU ASKIDA: LIVE_ORDER_PATH_ENABLED" in html and "ÖĞRENME MODU AÇIK" not in html


def test_dashboard_after_switching_off_explains_the_open_learning_positions(tmp_path):
    """Kapatıldı (health'te alan yok) ama öğrenme dönemi yaşandı (`learning_mode.json`): açık pozisyonlar ve %6 notu."""
    _learning_state(tmp_path)
    _w(tmp_path, "health.json", {"state": "HEALTHY", "at": "2026-09-29T09:00:00+00:00"})
    html = _client(tmp_path).get("/").text
    assert "ÖĞRENME MODU KAPALI" in html and "TOTAL_OPEN_RISK" in html and "ÖĞRENME MODU AÇIK" not in html
    assert "28.09.2026 08:00:00 UTC" in html, "learning_mode_since görünür"


# ============================================================================ bot karnesi
_spec = importlib.util.spec_from_file_location("bot_scorecard_lm", ROOT / "scripts" / "bot_scorecard.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
T0 = datetime(2026, 9, 1, tzinfo=UTC)
SINCE = T0 + timedelta(hours=20)


def _book_ledger(path: Path, trades: list[tuple[float, dict | None]]) -> None:
    """Saat başına bir işlem (giriş T0 + i saat), çıkış fiyatı ve (varsa) öğrenme etiketi."""
    led = FuturesLedgerV2(D("1000"))
    for i, (px, feats) in enumerate(trades):
        t = T0 + timedelta(hours=i)
        assert led.open("ETH/USDT", "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=D("95"),
                        targets=[D("110")], features=dict(feats or {}), now=t) is not None
        assert led.close_manual("ETH/USDT", D(str(px)), now=t + timedelta(minutes=30)) is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    led.save(path)


def _extra(codes):
    return {"learning": {"size_rule": "SLOT", "learning_unlocked_by": list(codes)}}


def test_scorecard_splits_before_after_policy_extra_and_counterfactuals(tmp_path):
    state = tmp_path / "state"
    _book_ledger(state / "futures_ledger.json", [(96.0, None)] * 20 + [(104.0, _extra([]))] * 3 + [(96.0, _extra(["REGIME_VETO:x"]))] * 2)
    _book_ledger(state / "strategy_paper" / "futures_ledger.json",
                 [(104.0, None)] * 20 + [(104.0, None)] * 2 + [(96.0, _extra(["TOTAL_OPEN_RISK"]))] * 5)
    _w(state, "strategy_paper/counterfactual_trades.json", {"trades": [
        {"id": "c1", "book": "t2_trend_regime", "outcome": {"r_multiple": 2.0}},
        {"id": "c2", "book": "t2_trend_regime", "outcome": {"r_multiple": -1.0}, "approx": True},
        {"id": "c3", "book": "t2_trend_regime", "outcome": None}], "meta": {"schema_version": "learning_cf_v1"}})
    _w(state, "shadow_book.json", {"trades": [{"id": "m1", "book": "main", "outcome": {"r_multiple": 0.5}},
                                              {"id": "old", "outcome": {"r_multiple": -3.0}}]})
    before_files = {p: p.read_bytes() for p in state.rglob("*.json")}
    card_off = S.scorecard(state)
    assert "learning_mode" not in card_off and "ÖĞRENME" not in S.render(card_off), "since yok → karne AYNEN eskisi"
    _w(state, "learning_mode.json", {"since": SINCE.isoformat()})
    card = S.scorecard(state)
    lm = card["learning_mode"]
    assert lm["since"] == SINCE.isoformat() and lm["source"] == "learning_mode.json"
    m, t2 = lm["books"]["main"], lm["books"]["strategy_paper"]
    assert (m["before"]["n"], m["after"]["n"], m["policy"]["n"], m["learning_extra"]["n"]) == (20, 5, 3, 2)
    assert m["before"]["mean_r"] < 0 < m["policy"]["mean_r"] and m["learning_extra"]["mean_r"] < 0
    assert (t2["before"]["n"], t2["after"]["n"], t2["policy"]["n"], t2["learning_extra"]["n"]) == (20, 7, 2, 5)
    cf = t2["counterfactual"]
    assert (cf["recorded"], cf["labeled"], cf["pending"], cf["approx"]) == (3, 2, 1, 1) and cf["mean_r"] == 0.5
    assert cf["in_pnl"] is False and "net_usdt" not in cf, "karşı-olgusal P&L'e GİRMEZ"
    assert m["counterfactual"]["recorded"] == 1 and m["counterfactual"]["mean_r"] == 0.5, "öğrenme öncesi gölge sayılmaz"
    # R esas; defterlerin P&L kartı karşı-olgusaldan etkilenmez
    assert {k: v for k, v in card.items() if k != "learning_mode"} == card_off
    txt = S.render(card)
    assert "ÖĞRENME MODU AYRIMI" in txt and "karşı-olgusal (P&L dışı)" in txt and "2/3 etiketli" in txt
    assert {p: p.read_bytes() for p in state.rglob("*.json") if p.name != "learning_mode.json"} == before_files, "salt okunur"
    # CLI geçersiz kılma: dosya yerine --learning-since
    out = tmp_path / "k.json"
    assert S.main(["--state", str(state), "--learning-since", (T0 + timedelta(hours=22)).isoformat(), "--out", str(out)]) == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["learning_mode"]["source"] == "--learning-since"
    assert doc["learning_mode"]["books"]["strategy_paper"]["before"]["n"] == 22
