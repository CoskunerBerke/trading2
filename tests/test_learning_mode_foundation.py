"""ÖĞRENME MODU L1 — TEMEL (2026-09-28, öğrenme modu).

Kapsam: `learning_mode` (anahtar, çalışma zamanı kapısı, profil kopyası, `fit_size`, kaldıraç tabanı, karşı-olgusal
sınıfları), `learning_cf.CounterfactualRecorder`, `FuturesLedgerV2.open(allow_shrink=…)` çağrı-başı ezme ve
`learn.shadow.ShadowTrade` isteğe bağlı alanları. Anahtar KAPALIYKEN davranışın bit-aynı kaldığı ayrıca kanıtlanır.
"""
from __future__ import annotations

import copy
import dataclasses
import itertools
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec
from tradingbot.accounting.futures_ledger import R_INSUFFICIENT_MARGIN
from tradingbot.accounting.models import MarketType, SymbolFilters
from tradingbot.learn.shadow import ShadowBook, ShadowTrade
from tradingbot.learning_cf import HORIZON_BARS, CounterfactualRecorder
from tradingbot.learning_mode import (
    BOOK_NAMES,
    COUNTERFACTUAL_NEVER,
    COUNTERFACTUAL_OK,
    LEVERAGE_FALLBACK,
    OVERRIDE_KEYS,
    SIZE_BUMP,
    SIZE_SHRUNK,
    SIZE_SLOT,
    BookLearning,
    BookLearningCfg,
    LearningMode,
    counterfactual_ok,
    fit_size,
    leverage_fallback,
    profile_for,
)
from tradingbot.risk.profiles import PROFILES

T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
H4 = timedelta(hours=4)
EPS = 1e-9


# ============================================================================ yardımcılar
@dataclasses.dataclass
class _Sec:
    """config_v3.LearningModeSection yerine geçen sade nesne (modül SAF: config'e bağımlı değil)."""
    enabled: bool = True
    risk_per_trade_pct: float = 0.5
    max_total_open_risk_pct: float = 100.0
    margin_reserve_pct: float = 5.0
    liq_buffer_mult: float = 2.0
    min_notional_bump: bool = True
    counterfactual: bool = True
    counterfactual_max_pending: int = 2000
    books: dict = dataclasses.field(default_factory=lambda: {
        "main": {"enabled": True, "slots": 20},
        "t2_trend_regime": {"enabled": True, "slots": 40, "leverage_max": 4},
        "b1_box_fade": BookLearningCfg(enabled=True, slots=20, leverage_max=4, min_stop_pct=0.32),
        "d4_donchian_20_10": {"enabled": True, "slots": 20, "leverage_max": 3, "symbols": "universe"},
        "c4s_candle_variations_strict": {"enabled": False},
    })
    strategy_overrides: dict = dataclasses.field(default_factory=lambda: {
        "regime_gate_shadow": True, "candle_veto_shadow": True,
        "structures_entry_shadow": ["main", "t2_trend_regime", "m2_tsmom28"],
        "economics_exploration": True, "leverage_confidence_fallback": True})


class _Gate:
    def __init__(self, ok=True, why="OK"):
        self.ok, self.why, self.calls = ok, why, 0

    def __call__(self):
        self.calls += 1
        return self.ok, self.why


# ============================================================================ LearningMode
def test_missing_section_is_a_disabled_instance_and_every_reader_returns_baseline():
    from tradingbot.config_v3 import V3Config
    for cfg in (None, object(), V3Config()):
        lm = LearningMode.from_config(cfg, _Gate())
        assert lm.enabled is False and lm.on is False
        assert lm.refresh() is False and lm.on is False
        assert lm.active() == (False, "DISABLED")
        assert all(lm.book(n) is None for n in BOOK_NAMES)
        assert lm.override("regime_gate_shadow", "BASE") == "BASE"
        assert lm.structures_entry_shadow("main") is False
        st = lm.status()
        assert st["enabled"] is False and st["active"] is False and st["reason"] == "DISABLED"


def test_disabled_section_never_consults_the_gate():
    g = _Gate()
    lm = LearningMode(_Sec(enabled=False), mode_gate=g)
    assert lm.refresh() is False and g.calls == 0
    assert lm.book("main") is None


def test_string_true_does_not_enable():
    """Fail-closed: yalnız gerçek `True` açar (YAML'da `"true"` yazımı açmaz)."""
    lm = LearningMode(_Sec(enabled="true"), mode_gate=_Gate())
    assert lm.enabled is False and lm.refresh() is False


def test_active_gate_yields_immutable_book_views():
    lm = LearningMode(_Sec(), mode_gate=_Gate())
    assert lm.on is False and lm.book("main") is None          # refresh ÖNCESİ baseline
    assert lm.refresh() is True and lm.on is True
    b = lm.book("t2_trend_regime")
    assert isinstance(b, BookLearning) and b.on is True and b.name == "t2_trend_regime"
    assert (b.slots, b.leverage_max, b.risk_pct, b.reserve_pct, b.liq_buffer_mult) == (40, 4, 0.5, 5.0, 2.0)
    assert b.min_notional_bump is True and b.counterfactual is True and b.max_pending == 2000
    assert b.hard_cap_pct == 2.0 and b.min_stop_pct is None and b.symbols is None
    box = lm.book("b1_box_fade")
    assert box.min_stop_pct == pytest.approx(0.32) and box.leverage_max == 4
    assert lm.book("d4_donchian_20_10").symbols == "universe"
    assert lm.book("main").leverage_max == 3                   # varsayılan
    assert lm.book("c4s_candle_variations_strict") is None     # defter kapalı
    assert lm.book("m2_tsmom28") is None                       # listede yok → kapalı
    assert lm.book("no_such_book") is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        b.slots = 1                                            # type: ignore[misc]
    st = lm.status()
    assert st["active"] is True and st["reason"] == "ACTIVE" and st["since"]
    assert "c4s_candle_variations_strict" not in st["books"] and "main" in st["books"]


def test_overrides_apply_only_while_active_and_revert_on_suspension():
    g = _Gate()
    lm = LearningMode(_Sec(), mode_gate=g)
    lm.refresh()
    assert lm.override("regime_gate_shadow", False) is True
    assert lm.effective("candle_veto_shadow", False) is True
    assert lm.structures_entry_shadow("main") is True and lm.structures_entry_shadow("m2_tsmom28") is True
    assert lm.structures_entry_shadow("b1_box_fade") is False
    # çalışma zamanı mod geçişi → ASKIYA ALINIR, her okuyucu baseline'a döner
    g.ok, g.why = False, "MODE_NOT_PAPER:TESTNET"
    assert lm.refresh() is False and lm.suspended is True
    assert lm.status()["reason"] == "LEARNING_MODE_SUSPENDED:MODE_NOT_PAPER:TESTNET"
    assert lm.override("regime_gate_shadow", False) is False
    assert lm.structures_entry_shadow("main") is False
    assert all(lm.book(n) is None for n in BOOK_NAMES)
    g.ok = True
    assert lm.refresh() is True and lm.book("main") is not None


def test_unknown_override_key_is_an_error_even_when_off():
    lm = LearningMode(None, mode_gate=_Gate())
    with pytest.raises(KeyError):
        lm.override("regime_gate_shadowz", False)
    assert set(OVERRIDE_KEYS) == {"regime_gate_shadow", "candle_veto_shadow", "structures_entry_shadow",
                                  "economics_exploration", "leverage_confidence_fallback"}


def test_gate_exception_suspends_fail_closed():
    def boom():
        raise RuntimeError("x")
    lm = LearningMode(_Sec(), mode_gate=boom)
    assert lm.refresh() is False
    assert lm.status()["reason"].startswith("LEARNING_MODE_SUSPENDED:MODE_GATE_ERROR")


def test_since_marks_state_transitions():
    now = [T0]
    g = _Gate()
    lm = LearningMode(_Sec(), mode_gate=g, clock=lambda: now[0])
    lm.refresh()
    s1 = lm.status()["since"]
    now[0] = T0 + H4
    lm.refresh()                                              # durum aynı → since değişmez
    assert lm.status()["since"] == s1 == "2026-09-01T00:00:00+00:00"
    g.ok = False
    now[0] = T0 + 2 * H4
    lm.refresh()
    assert lm.status()["since"] == "2026-09-01T08:00:00+00:00"


def test_real_research_mode_gate_contract():
    """Kapı, motorun `_research_mode_ok`u ile AYNI bileşimi kullanır."""
    from tradingbot.learn.research_coordinator import mode_gate
    for mode, gw, live, want in (("PAPER", "paper", False, True), ("TESTNET", "paper", False, False),
                                 ("PAPER", "binance_futures_testnet", False, False), ("PAPER", "paper", True, False)):
        lm = LearningMode(_Sec(), mode_gate=lambda m=mode, g=gw, v=live: mode_gate(m, g, v))
        assert lm.refresh() is want


# ============================================================================ profil
def test_profile_for_is_a_copy_and_never_registered():
    before = copy.deepcopy(PROFILES)
    base = PROFILES["PAPER_RESEARCH"]
    p = profile_for(base)
    pm = profile_for(base, main=True)
    assert p.max_total_open_risk_pct == 100.0 and p.risk_per_trade_pct == 2.0
    assert p.max_spot_allocation_pct == base.max_spot_allocation_pct == 30.0
    assert pm.max_spot_allocation_pct == 100.0 and pm.max_total_open_risk_pct == 100.0
    assert p.max_position_pct == base.max_position_pct and p.max_open_positions is None
    assert PROFILES == before and PROFILES["PAPER_RESEARCH"].max_total_open_risk_pct == 6.0
    assert all(v is not p and v is not pm for v in PROFILES.values())
    lm = LearningMode(_Sec(max_total_open_risk_pct=50.0), mode_gate=_Gate())
    assert lm.profile_for(base).max_total_open_risk_pct == 50.0


def test_learning_risk_engine_lifts_total_open_risk_only():
    """Öğrenme profiliyle 12 × %0,5 açık riskin üstüne 13. aday TOTAL_OPEN_RISK'e takılmaz; baz profil takılır."""
    from tradingbot.risk import KillSwitch, OpenPosition, PortfolioState, RiskEngine
    E = 200.0
    opens = [OpenPosition(symbol=f"S{i}/USDT", market_type="USDM_PERP", side="LONG", notional=25.0, margin=12.5,
                          risk_usdt=E * 0.005, entry=100.0, stop=96.0) for i in range(12)]
    state = PortfolioState(equity=E, starting_equity=E, high_water_mark=E, available=E - 150.0, used_margin=150.0,
                           open_positions=opens)
    plan = {"symbol": "NEW/USDT", "market_type": "USDM_PERP", "direction": "LONG", "entry": 100.0, "stop": 96.0,
            "notional": 25.0, "leverage": 2, "min_notional": 5.0}
    base = RiskEngine(PROFILES["PAPER_RESEARCH"], KillSwitch())
    learn = RiskEngine(profile_for(PROFILES["PAPER_RESEARCH"]), base.ks)
    assert "TOTAL_OPEN_RISK" in base.evaluate(plan, state).reasons
    rd = learn.evaluate(plan, state)
    assert rd.allowed, rd.reasons


# ============================================================================ fit_size — özellik ızgarası
S_GRID = (0.002, 0.0035, 0.005, 0.008, 0.012, 0.02, 0.03, 0.045, 0.07, 0.1, 0.13, 0.2, 0.4)   # 0.4: liq tamponu dalı
K_GRID = (4, 20, 40, 200)
L_GRID = (1, 2, 3, 4, 5)
E_GRID = (100.0, 200.0, 1000.0)
R_GRID = (0.25, 0.5, 2.0)
PX_GRID = ((0.05, 1.0), (1.3, 0.1), (100.0, 0.001), (60000.0, 0.001))    # (fiyat, qty adımı)
MN_GRID = (5.0, 50.0)


def _check_result(res, *, E, s, K, Lmax, r, reserve, b, mmr, mn, px, step, available):
    m_slot = (1 - reserve / 100) * E / K
    if not res.ok:
        assert res.reason in ("MIN_ORDER_CONFLICT", "LIQ_BUFFER_TOO_THIN", "INSUFFICIENT_MARGIN"), res
        assert res.notional == 0 and res.risk_usdt == 0 and res.size_rule is None
        if res.reason == "MIN_ORDER_CONFLICT" and res.detail.get("why") == "RISK_CAP":
            assert res.detail["bump_risk_usdt"] > 0.02 * E
        return
    L = res.leverage
    assert 1 <= L <= Lmax
    assert 1.0 / L - mmr >= b * s, (L, s)                                  # liq ≥ b × stop
    assert res.margin == pytest.approx(res.notional / L, rel=1e-12)
    assert res.risk_usdt == pytest.approx(res.notional * s, rel=1e-9)
    if res.size_rule in (SIZE_SLOT, SIZE_SHRUNK):
        assert res.notional <= m_slot * L * (1 + EPS)                      # slot tavanı
        assert res.risk_usdt <= min(r, 2.0) / 100 * E * (1 + EPS)          # hedef risk (≤ %2 tavan)
    else:
        assert res.size_rule == SIZE_BUMP
        assert res.risk_usdt <= 0.02 * E * (1 + EPS)                       # yalnız sert tavan
        assert res.notional >= mn * (1 - 1e-12)
        if available is None:
            assert res.notional <= m_slot * L * (1 + EPS)
        else:
            assert res.margin <= available - reserve / 100 * E + 1e-9
        if step is not None:
            q = Decimal(res.detail["qty"])
            assert q % Decimal(str(step)) == 0                             # adıma oturmuş
            assert float(q) * px >= mn * (1 - 1e-12)                       # YUKARI yuvarlandı
            assert float(q - Decimal(str(step))) * px < mn                 # en küçük yeterli adım
    if available is not None:
        assert res.margin <= available - reserve / 100 * E + 1e-9


@pytest.mark.parametrize("reserve,b", [(5.0, 2.0), (0.0, 1.5), (10.0, 3.0)])
def test_fit_size_property_grid(reserve, b):
    mmr = 0.004
    seen: set[str] = set()
    n = 0
    for s, K, Lmax, E, r, (px, step), mn in itertools.product(S_GRID, K_GRID, L_GRID, E_GRID, R_GRID, PX_GRID, MN_GRID):
        for side in (1, -1):
            stop = px * (1 - side * s)
            # serbest marj: bilinmiyor / bol / slotu karşılar / dar (küçültme) / rezerv dışında sıfır
            for available in (None, E, E * 0.3, E * (reserve / 100 + 0.03), E * reserve / 100):
                res = fit_size(equity=E, entry=px, stop=stop, slots=K, leverage_max=Lmax, risk_pct=r, reserve_pct=reserve,
                               liq_buffer_mult=b, mmr=mmr, min_notional=mn, qty_step=step, available_margin=available)
                _check_result(res, E=E, s=s, K=K, Lmax=Lmax, r=r, reserve=reserve, b=b, mmr=mmr, mn=mn, px=px,
                              step=step, available=available)
                seen.add(res.size_rule or f"{res.reason}:{res.detail.get('why')}")
                n += 1
    assert n > 100_000
    # ızgara her dalı gerçekten yürütür (yalnız SLOT'u değil)
    assert {SIZE_SLOT, SIZE_BUMP, SIZE_SHRUNK, "MIN_ORDER_CONFLICT:RISK_CAP", "MIN_ORDER_CONFLICT:MARGIN",
            "INSUFFICIENT_MARGIN:None"} <= seen, seen
    if b >= 3.0:
        assert "LIQ_BUFFER_TOO_THIN:None" in seen


@pytest.mark.parametrize("E", E_GRID)
@pytest.mark.parametrize("K", (4, 20, 40))
@pytest.mark.parametrize("s", (0.005, 0.03, 0.13))
def test_k_opens_never_use_more_than_the_margin_pool(E, K, s):
    """K (ve K+5) ardışık açılıştan sonra Σ marj ≤ (1 − rezerv) × E — serbest marj verilse de verilmese de."""
    reserve = 5.0
    for Lmax in (1, 3, 5):
        for with_avail in (True, False):
            used, n_ok = 0.0, 0
            for _ in range(K + 5):
                res = fit_size(equity=E, entry=100.0, stop=100.0 * (1 - s), slots=K, leverage_max=Lmax, risk_pct=0.5,
                               reserve_pct=reserve, min_notional=5.0, qty_step=0.001,
                               available_margin=(E - used) if with_avail else None)
                if res.ok:
                    used += res.margin
                    n_ok += 1
                if not with_avail and n_ok >= K:
                    break                                    # serbest marj bilinmiyorsa çağıran K slotta durur
            assert used <= (1 - reserve / 100) * E * (1 + EPS), (E, K, s, Lmax, with_avail, used)


def test_fit_size_reproduces_spec_worked_numbers():
    m2 = fit_size(equity=200, entry=100, stop=87, slots=40, leverage_max=4, risk_pct=0.5)
    assert (m2.ok, m2.leverage, m2.size_rule) == (True, 2, SIZE_SLOT)
    assert (round(m2.notional, 2), round(m2.margin, 2), round(m2.risk_usdt, 2)) == (7.69, 3.85, 1.0)
    pt = fit_size(equity=100, entry=100, stop=96, slots=30, leverage_max=3, risk_pct=0.5)
    assert (pt.leverage, round(pt.notional, 2), round(pt.margin, 2), round(pt.risk_usdt, 2)) == (3, 9.5, 3.17, 0.38)
    box = fit_size(equity=200, entry=100, stop=97.2, slots=20, leverage_max=4, risk_pct=0.5)
    assert (box.leverage, round(box.notional, 1), round(box.margin, 1), round(box.risk_usdt, 2)) == (4, 35.7, 8.9, 1.0)
    c4 = fit_size(equity=200, entry=100, stop=97, slots=20, leverage_max=3, risk_pct=0.5)
    assert (c4.leverage, round(c4.notional, 1), round(c4.margin, 1)) == (3, 28.5, 9.5)
    assert c4.risk_usdt == pytest.approx(0.855)                            # SPEC tablosunda 0,86
    assert c4.detail["risk_fraction_of_budget"] == pytest.approx(0.855, abs=1e-3)


def test_leverage_is_capped_by_the_liquidation_buffer():
    # s = %13 → floor(1 / (2×0.13 + 0.004)) = 3; L_max 5 olsa bile ≤ 3
    r = fit_size(equity=1_000_000, entry=100, stop=87, slots=4, leverage_max=5, risk_pct=0.5)
    assert r.ok and r.leverage <= 3 and 1 / r.leverage - 0.004 >= 2 * 0.13
    thin = fit_size(equity=200, entry=100, stop=40, slots=20, leverage_max=5, risk_pct=0.5)
    assert not thin.ok and thin.reason == "LIQ_BUFFER_TOO_THIN"


def test_invalid_inputs_are_rejected_not_sized():
    for kw in (dict(entry=0, stop=1), dict(entry=100, stop=100), dict(entry=100, stop=0), dict(entry=float("nan"), stop=1)):
        r = fit_size(equity=200, slots=20, leverage_max=3, risk_pct=0.5, **kw)
        assert not r.ok and r.reason in ("INVALID_INPUT", "INVALID_STOP")
    assert fit_size(equity=0, entry=100, stop=96, slots=20, leverage_max=3, risk_pct=0.5).reason == "INVALID_INPUT"
    assert fit_size(equity=200, entry=100, stop=96, slots=0, leverage_max=3, risk_pct=0.5).reason == "INVALID_INPUT"


def test_risk_pct_above_hard_cap_is_clamped():
    r = fit_size(equity=200, entry=100, stop=96, slots=4, leverage_max=5, risk_pct=5.0)
    assert r.ok and r.risk_usdt <= 0.02 * 200 * (1 + EPS)


# ============================================================================ min-notional çıkarma
def test_bump_rounds_qty_up_to_step_within_cap_and_margin():
    # E=200, s=%13 → slot notional 7.69 (≥5) ama ETH 3000'de adım 0.001 → 0.002 qty = 6.0 > 5; 7.69 zaten ≥ 5 → SLOT
    r = fit_size(equity=200, entry=3000, stop=2610, slots=40, leverage_max=4, risk_pct=0.5, min_notional=5, qty_step=0.001)
    assert r.ok and r.size_rule == SIZE_SLOT
    # min_notional 20 → çıkarma: 20/3000 = 0.00667 → 0.007 → 21.0 USDT, risk 2.73 ≤ %2 × 200 = 4
    b = fit_size(equity=200, entry=3000, stop=2610, slots=40, leverage_max=4, risk_pct=0.5, min_notional=20,
                 qty_step=0.001, available_margin=200)
    assert b.ok and b.size_rule == SIZE_BUMP and b.detail["qty"] == "0.007"
    assert b.notional == pytest.approx(21.0) and b.risk_usdt == pytest.approx(21.0 * 0.13)
    assert b.risk_usdt > 0.005 * 200 and b.risk_usdt <= 0.02 * 200


def test_bump_rejected_above_hard_cap_btc_in_m2():
    """SPEC: M2'de BTC (s=%13), E=200 → 50 × 0,13 = 6,5 > 4 → MIN_ORDER_CONFLICT; E=1000'de başarılı."""
    r = fit_size(equity=200, entry=60000, stop=52200, slots=40, leverage_max=4, risk_pct=0.5, min_notional=50,
                 qty_step=0.001, available_margin=200)
    assert not r.ok and r.reason == "MIN_ORDER_CONFLICT" and r.detail["why"] == "RISK_CAP"
    ok = fit_size(equity=1000, entry=60000, stop=52200, slots=40, leverage_max=4, risk_pct=0.5, min_notional=50,
                  qty_step=0.001, available_margin=1000)
    assert ok.ok and ok.size_rule == SIZE_BUMP and ok.detail["qty"] == "0.001" and ok.notional == pytest.approx(60.0)
    assert ok.risk_usdt <= 0.02 * 1000


def test_bump_exactly_at_cap_passes_and_step_rounding_can_push_it_over():
    """SPEC: Formasyon BTC (s=%4, E=100): 50 × 0,04 = 2,0 = tavan → geçer; qty adımı 60 USDT'ye çıkarırsa geçmez."""
    at = fit_size(equity=100, entry=60000, stop=57600, slots=30, leverage_max=3, risk_pct=0.5, min_notional=50,
                  available_margin=100)
    assert at.ok and at.size_rule == SIZE_BUMP and at.risk_usdt == pytest.approx(2.0)
    over = fit_size(equity=100, entry=60000, stop=57600, slots=30, leverage_max=3, risk_pct=0.5, min_notional=50,
                    qty_step=0.001, available_margin=100)
    assert not over.ok and over.reason == "MIN_ORDER_CONFLICT" and over.detail["why"] == "RISK_CAP"


def test_bump_needs_free_margin():
    kw = dict(equity=1000, entry=60000, stop=52200, slots=40, leverage_max=4, risk_pct=0.5, min_notional=50, qty_step=0.001)
    no_room = fit_size(available_margin=1000 * 0.05 + 5, **kw)          # rezerv dışında 5 USDT
    assert not no_room.ok and no_room.reason == "MIN_ORDER_CONFLICT" and no_room.detail["why"] == "MARGIN"
    # serbest marj bilinmiyorsa çıkarma tek slotu aşamaz (60/3 = 20 < m_slot 23,75 → geçer)
    assert fit_size(**kw).ok
    assert not fit_size(**{**kw, "slots": 100}).ok                        # m_slot 9,5 < 20


def test_bump_disabled_or_min_qty():
    off = fit_size(equity=200, entry=3000, stop=2610, slots=40, leverage_max=4, risk_pct=0.5, min_notional=20,
                   qty_step=0.001, min_notional_bump=False)
    assert not off.ok and off.reason == "MIN_ORDER_CONFLICT" and off.detail["why"] == "BUMP_DISABLED"
    mq = fit_size(equity=200, entry=3000, stop=2610, slots=40, leverage_max=4, risk_pct=0.5, min_notional=5,
                  qty_step=0.001, min_qty=0.004, available_margin=200)
    assert mq.ok and mq.size_rule == SIZE_BUMP and mq.detail["qty"] == "0.004"


def test_shrink_to_margin_when_pool_is_short():
    kw = dict(equity=200, entry=100, stop=97, slots=20, leverage_max=3, risk_pct=0.5, qty_step=0.001)
    full = fit_size(**kw)
    # yalnız 12 USDT serbest (rezerv 10) → 2 USDT kullanılabilir; L 3'te 6 USDT notional
    s = fit_size(available_margin=12.0, **kw)
    assert s.ok and s.size_rule == SIZE_SHRUNK and s.notional == pytest.approx(6.0) and s.margin <= 2.0 + 1e-9
    assert s.risk_usdt < full.risk_usdt
    small = fit_size(available_margin=10.5, **kw)                         # 0,5 × 3 = 1,5 < 5 → çıkarma marja sığmaz
    assert not small.ok and small.reason == "MIN_ORDER_CONFLICT" and small.detail["why"] == "MARGIN"
    none = fit_size(available_margin=10.0, **kw)                          # rezerv dışında hiç marj yok
    assert not none.ok and none.reason == "INSUFFICIENT_MARGIN"
    # önce kaldıraçla sığdırılır: L_need 3'ten küçükken marj darsa L yükselir, risk DEĞİŞMEZ
    kw2 = dict(equity=200, entry=100, stop=90, slots=20, leverage_max=4, risk_pct=0.5)
    base = fit_size(**kw2)
    assert base.leverage == 2
    tight = fit_size(available_margin=10 + base.notional / 3 + 1e-6, **kw2)
    assert tight.ok and tight.size_rule == SIZE_SLOT and tight.leverage == 3
    assert tight.risk_usdt == pytest.approx(base.risk_usdt)


def test_r_multiple_is_invariant_to_equity(tmp_path):
    """Kapanan işlemin R'si E=200 ile E=1000'de aynıdır (boyut ölçeği R'yi değiştirmez)."""
    f = SymbolFilters("ETH/USDT", MarketType.USDM_PERP, qty_step=Decimal("0.00001"), min_qty=Decimal("0.00001"))
    for entry, stop, exit_ in ((100.0, 96.0, 108.0), (100.0, 96.0, 97.0), (2500.0, 2450.0, 2600.0)):
        rs = []
        for E in (200.0, 1000.0):
            res = fit_size(equity=E, entry=entry, stop=stop, slots=20, leverage_max=3, risk_pct=0.5,
                           qty_step=0.00001, available_margin=E)
            assert res.ok
            led = FuturesLedgerV2(E, max_positions=None)
            pos = led.open("ETH/USDT", "LONG", entry, SizeSpec(Decimal(str(res.notional)), AmountType.NOTIONAL,
                                                                 res.leverage), stop=stop, filters=f, now=T0)
            assert pos is not None, led.last_reject_reason
            rec = led.close_manual("ETH/USDT", exit_, now=T0 + H4)
            rs.append(float(rec.r_multiple))
        assert rs[0] == pytest.approx(rs[1], abs=5e-3), rs


# ============================================================================ kaldıraç tabanı
def test_leverage_fallback_only_for_the_allowed_failure_set():
    assert leverage_fallback(["STOP_TOO_FAR_FOR_LEVERAGE"], stop_atr=5.0, allow_confidence=False) == LEVERAGE_FALLBACK == 2
    assert leverage_fallback(["DEPTH_BELOW_BASE", "STOP_TOO_FAR_FOR_LEVERAGE"], stop_atr=None, allow_confidence=False) == 2
    assert leverage_fallback(["STOP_TOO_TIGHT_FOR_LEVERAGE"], stop_atr=0.1, allow_confidence=False) == 2
    assert leverage_fallback(["STOP_TOO_TIGHT_FOR_LEVERAGE"], stop_atr=0.05, allow_confidence=False) is None
    assert leverage_fallback(["STOP_TOO_TIGHT_FOR_LEVERAGE"], stop_atr=None, allow_confidence=False) is None
    assert leverage_fallback(["CONFIDENCE_BELOW_BASE"], stop_atr=1.0, allow_confidence=True) == 2
    assert leverage_fallback(["CONFIDENCE_BELOW_BASE"], stop_atr=1.0, allow_confidence=False) is None
    for keep in ("DATA_STALE", "DATA_CONFLICT", "STOP_UNKNOWN", "CONFIDENCE_UNKNOWN",
                 "LIQ_BUFFER_TOO_THIN_FOR_BASE", "SPREAD_ABOVE_BASE"):
        assert leverage_fallback([keep], stop_atr=1.0, allow_confidence=True) is None, keep
        assert leverage_fallback(["DEPTH_BELOW_BASE", keep], stop_atr=1.0, allow_confidence=True) is None, keep
    assert leverage_fallback([], stop_atr=1.0, allow_confidence=True) is None


# ============================================================================ karşı-olgusal sınıfları
def test_counterfactual_classes():
    assert not (COUNTERFACTUAL_OK & COUNTERFACTUAL_NEVER)
    assert all(counterfactual_ok(c) for c in COUNTERFACTUAL_OK)
    assert not any(counterfactual_ok(c) for c in COUNTERFACTUAL_NEVER)
    for ok in ("TOTAL_OPEN_RISK", "INSUFFICIENT_MARGIN", "MIN_NOTIONAL", "STRUCTURE:WAIT_CONFIRMED_STRUCTURE",
               "STRUCTURE_WAIT_TRIGGER", "CANDLE_VETO:C3_OPPOSITE_PATTERN", "REGIME_VETO:R1_SHORT_BLOCKED",
               "RISK_OUTSIDE_TESTED_RANGE", "LEVERAGE_GATE_BLOCKED", "NEGATIVE_NET_EDGE", "ALSO_MATCHED",
               "position_open"):
        assert counterfactual_ok(ok), ok
    for never in ("DATA_STALE", "DATA_BAR_MISMATCH_4H", "DUPLICATE_SIGNAL", "SIGNAL_ALREADY_USED",
                  "PATTERN_ALREADY_USED", "NO_TRIGGER", "STRATEGY_BAD_STOP", "BAD_STOP", "UNRESOLVED_PRECISION",
                  "STRUCTURE:STRUCTURE_FRAME_MARKET_MISMATCH:SPOT", "CANDLE_VETO:C1_MISSING_BARS",
                  "REGIME_VETO:R1_NO_REGIME", "STRUCTURE:?", "", None, "SOMETHING_NEW", "STRATEGY_ERROR:ValueError"):
        assert not counterfactual_ok(never), never


# ============================================================================ karşı-olgusal kayıtçı
def _rec(cf: CounterfactualRecorder, **kw) -> bool:
    base = dict(signal_key="sig-1", symbol="ETH/USDT", direction="LONG", entry=100.0, stop=96.0, targets=[108.0],
                reason="TOTAL_OPEN_RISK", created_at=T0 + timedelta(minutes=1), tf_minutes=240, horizon_bars=6,
                label_kind="TARGET_STOP_TIME", features={"atr": 1.5, "bad": float("nan")})
    base.update(kw)
    return cf.record(**base)


def _bars(n: int, *, start: datetime = T0, step: timedelta = H4, close=100.0, lows=None, highs=None) -> pd.DataFrame:
    rows = []
    for i in range(n):
        lo = (lows or {}).get(i, close - 1.0)
        hi = (highs or {}).get(i, close + 1.0)
        rows.append({"timestamp": int((start + i * step).timestamp() * 1000), "open": close, "high": hi, "low": lo,
                     "close": close, "volume": 1.0})
    return pd.DataFrame(rows)


def test_one_signal_over_sixteen_tours_is_one_record(tmp_path):
    """P1: aynı barın sinyali 16 tur boyunca kapasiteye takılsa da TEK kayıt; plan_id = sinyal anahtarı."""
    cf = CounterfactualRecorder(tmp_path / "counterfactual_trades.json", book="t2_trend_regime")
    wrote = [_rec(cf, created_at=T0 + timedelta(minutes=1 + 15 * i), reason=("TOTAL_OPEN_RISK" if i % 2 else
                                                                          "INSUFFICIENT_MARGIN")) for i in range(16)]
    assert wrote.count(True) == 1 and len(cf.sb.trades) == 1
    t = cf.sb.trades[0]
    assert t.plan_id == "sig-1" == t.signal_key and t.book == "t2_trend_regime" and t.variant == "as_planned"
    assert t.is_counterfactual is True and t.learning_unlocked is False and t.features == {"atr": 1.5, "bad": None}
    # farklı yön / varyasyon / sinyal ayrı olaydır
    assert _rec(cf, direction="SHORT", stop=104.0, targets=[92.0])
    assert _rec(cf, variation="CV002") and _rec(cf, variation="CV003")
    assert not _rec(cf, variation="CV002")
    assert _rec(cf, signal_key="sig-2")
    assert cf.stats()["recorded_total"] == 5 and cf.stats()["pending"] == 5
    cf.save()
    cf2 = CounterfactualRecorder(tmp_path / "counterfactual_trades.json", book="t2_trend_regime")
    assert not _rec(cf2) and not _rec(cf2, variation="CV003")           # yeniden başlatma da tekrar yazmaz
    assert cf2.stats() == cf.stats()


def test_no_record_for_untrusted_or_duplicate_reasons_and_bad_geometry(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="d4_donchian_20_10")
    for reason in ("DATA_STALE", "DUPLICATE_SIGNAL", "NO_TRIGGER", "SIGNAL_ALREADY_USED", "STRATEGY_BAD_STOP",
                   "UNRESOLVED_PRECISION", "UNKNOWN_THING"):
        assert not _rec(cf, reason=reason), reason
    assert not _rec(cf, stop=100.0) and not _rec(cf, stop=101.0)          # LONG stop girişin üstünde
    assert not _rec(cf, direction="SHORT", stop=99.0)
    assert not _rec(cf, entry=0.0) and not _rec(cf, entry=float("nan")) and not _rec(cf, stop=-1.0)
    assert not _rec(cf, direction="FLAT") and not _rec(cf, label_kind="WHATEVER") and not _rec(cf, signal_key="")
    assert not _rec(cf, tf_minutes=0) and not _rec(cf, horizon_bars=0)
    assert cf.stats()["recorded_total"] == 0 and cf.sb.trades == []


def test_pending_cap_drops_the_oldest_and_counts(tmp_path):
    p = tmp_path / "cf.json"
    cf = CounterfactualRecorder(p, book="c4_candle_variations", max_pending=5)
    for i in range(8):
        assert _rec(cf, signal_key=f"s{i}")
    st = cf.stats()
    assert st["pending"] == 5 and st["dropped"] == 3 and st["recorded_total"] == 8
    assert [t.signal_key for t in cf.sb.trades] == ["s3", "s4", "s5", "s6", "s7"]
    assert not _rec(cf, signal_key="s0")                                   # düşürülen olay yeniden yazılmaz
    cf.save()
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["meta"]["dropped"] == 3 and d["meta"]["recorded_total"] == 8 and len(d["trades"]) == 5
    assert CounterfactualRecorder(p, book="c4_candle_variations", max_pending=5).stats()["dropped"] == 3


def test_labelling_uses_only_closed_bars_stop_first(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="pattern_trader")
    assert _rec(cf, targets=[101.5])                                       # hedef ve stop AYNI barda
    # bar 1 (T0+4h) hem stopu (96) hem hedefi (101.5) görür; bar 2 henüz şekilleniyor
    df = _bars(3, lows={1: 95.0}, highs={1: 102.0})
    frames = {"ETH/USDT": {"4h": df}}
    assert cf.label_pending(frames, T0 + H4 + timedelta(hours=3)) == 0     # bar 1 KAPANMADI
    assert cf.sb.trades[0].outcome is None
    assert cf.label_pending(frames, T0 + 2 * H4) == 1                      # bar 1 kapandı (açılış + 4h ≤ now)
    out = cf.sb.trades[0].outcome
    assert out["exit_reason"] == "stop" and out["r_multiple"] == pytest.approx(-1.0)   # ÖNCE stop
    assert out["label_kind"] == "TARGET_STOP_TIME" and out["approx"] is False and out["is_counterfactual"] is True
    assert cf.sb.trades[0].labeled_at == "2026-09-01T08:00:00+00:00"
    assert cf.stats()["labeled"] == 1 and cf.stats()["pending"] == 0


def test_forming_bar_never_labels(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="b1_box_fade")
    assert _rec(cf, tf_minutes=5, horizon_bars=12, created_at=T0 + timedelta(seconds=30))
    df = _bars(3, step=timedelta(minutes=5), lows={1: 90.0})               # bar 1 (T0+5m) stopu deler
    now = T0 + timedelta(minutes=9, seconds=59)                            # bar 1 kapanmadan 1 sn önce
    assert cf.label_pending({"ETH/USDT": {"5m": df}}, now) == 0
    assert cf.label_pending({"ETH/USDT": {"5m": df}}, T0 + timedelta(minutes=10)) == 1


def test_target_and_time_stop(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="c4_candle_variations")
    assert _rec(cf, signal_key="tgt", targets=[104.0])
    assert _rec(cf, signal_key="time", symbol="SOL/USDT", targets=[120.0], horizon_bars=2)
    frames = {"ETH/USDT": {"4h": _bars(4, highs={2: 105.0})}, "SOL/USDT": {"4h": _bars(6, close=101.0)}}
    n = cf.label_pending(frames, T0 + 3 * H4)                              # bar 2 kapandı; SOL ufku (bar 1-2) doldu mu?
    tgt = next(t for t in cf.sb.trades if t.signal_key == "tgt")
    tim = next(t for t in cf.sb.trades if t.signal_key == "time")
    assert tgt.outcome["exit_reason"] == "target" and tgt.outcome["r_multiple"] == pytest.approx(1.0)
    # SOL: ufuk = created + 2 × 4h = T0+8h01; pencerenin son barı (T0+8h) T0+12h'de kapanır → henüz YOK
    assert tim.outcome is None and n == 1
    assert cf.label_pending(frames, T0 + 3 * H4 + timedelta(minutes=1)) == 1
    assert tim.outcome["exit_reason"] == "horizon" and tim.outcome["r_multiple"] == pytest.approx(0.25)


def test_horizon_kind_ignores_targets_is_30_bars_and_approx(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="t2_trend_regime")
    assert _rec(cf, label_kind="HORIZON", tf_minutes=1440, horizon_bars=210, targets=[104.0])
    t = cf.sb.trades[0]
    assert t.horizon_bars == HORIZON_BARS == 30 and t.approx is True and t.label_kind == "HORIZON"
    D1 = timedelta(days=1)
    df = _bars(40, step=D1, close=103.0, highs={3: 110.0})                 # hedef görülür ama HORIZON hedefe bakmaz
    frames = {"ETH/USDT": {"1d": df}}
    assert cf.label_pending(frames, T0 + 20 * D1) == 0                     # 30 bar dolmadı
    assert cf.label_pending(frames, T0 + 32 * D1) == 1
    out = t.outcome
    assert out["exit_reason"] == "horizon" and out["r_multiple"] == pytest.approx(0.75)
    assert out["approx"] is True and out["label_method"] == "HORIZON" and out["label_kind"] == "HORIZON"
    assert t.variant == "as_planned"                                       # kaydın kendi varyantı değişmez


def test_horizon_still_respects_stop_first_and_rule_exit_falls_back(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="m2_tsmom28")
    assert _rec(cf, label_kind="RULE_EXIT", tf_minutes=1440, horizon_bars=10)
    D1 = timedelta(days=1)
    df = _bars(10, step=D1, lows={2: 95.0})
    assert cf.label_pending({"ETH/USDT": {"1d": df}}, T0 + 3 * D1) == 1
    out = cf.sb.trades[0].outcome
    assert out["exit_reason"] == "stop" and out["label_method"] == "HORIZON_FALLBACK" and out["approx"] is True


def test_frame_formats_datetime_index_and_dict_rows(tmp_path):
    for fmt in ("index", "rows", "int_key"):
        cf = CounterfactualRecorder(tmp_path / f"cf_{fmt}.json", book="d4_donchian_20_10")
        assert _rec(cf)
        df = _bars(3, lows={1: 95.0})
        if fmt == "index":
            raw = df.drop(columns=["timestamp"]).set_index(pd.to_datetime(df["timestamp"], unit="ms", utc=True))
            frames = {"ETH/USDT": {"4h": raw}}
        elif fmt == "rows":
            frames = {"ETH/USDT": {"4h": df.to_dict("records")}}
        else:
            frames = {"ETH/USDT": {240: df}}
        assert cf.label_pending(frames, T0 + 2 * H4) == 1, fmt
        assert cf.sb.trades[0].outcome["exit_reason"] == "stop"


def test_unlabelable_records_expire_instead_of_pinning_the_cap(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "cf.json", book="d4_donchian_20_10")
    assert _rec(cf)
    assert cf.label_pending({}, T0 + 10 * H4) == 0 and cf.stats()["pending"] == 1
    assert cf.label_pending({}, T0 + 40 * H4) == 0
    st = cf.stats()
    assert st["pending"] == 0 and st["expired"] == 1 and st["labeled"] == 0


def test_counterfactuals_touch_no_ledger(tmp_path):
    cf = CounterfactualRecorder(tmp_path / "counterfactual_trades.json", book="main")
    _rec(cf)
    cf.label_pending({"ETH/USDT": {"4h": _bars(3, lows={1: 90.0})}}, T0 + 2 * H4)
    cf.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["counterfactual_trades.json"]


# ============================================================================ ShadowTrade geriye uyum
_OLD_FIELDS = ("id", "plan_id", "symbol", "market_type", "direction", "created_at", "entry", "stop", "targets",
               "horizon_bars", "variant", "reason_not_opened", "label_ts", "tf_minutes", "leverage", "outcome",
               "labeled_at", "is_counterfactual")


def test_main_shadow_book_file_is_byte_identical(tmp_path):
    """Yeni alanlar ayarlanmadıkça ana botun `shadow_book.json` metni eski `asdict` çıktısıyla BİREBİR aynı."""
    p = tmp_path / "shadow_book.json"
    sb = ShadowBook(p)
    sb.add({"plan_id": "p1", "symbol": "X/USDT", "market_type": "USDM_PERP", "direction": "LONG", "entry": 100.0,
            "stop": 96.0, "targets": [108.0], "horizon_bars": 6, "leverage": 3}, ["TOTAL_OPEN_RISK"], now=T0)
    t = sb.trades[0]
    old = {"trades": [{k: getattr(t, k) for k in _OLD_FIELDS}]}
    assert p.read_text(encoding="utf-8") == json.dumps(old, indent=1, ensure_ascii=False)
    assert list(t.to_dict()) == list(_OLD_FIELDS)


def test_old_shadow_files_load_and_new_fields_round_trip(tmp_path):
    p = tmp_path / "s.json"
    old_row = {k: None for k in _OLD_FIELDS}
    old_row.update({"id": "shadow_1", "plan_id": "p", "symbol": "X/USDT", "market_type": "USDM_PERP",
                    "direction": "LONG", "created_at": "2026-09-01T00:00:00+00:00", "entry": 1.0, "stop": 0.9,
                    "targets": [], "horizon_bars": 3, "variant": "as_planned", "reason_not_opened": ["A"],
                    "label_ts": "2026-09-01T12:00:00+00:00", "tf_minutes": 240, "leverage": 1.0,
                    "is_counterfactual": True, "legacy_unknown_key": 1})
    p.write_text(json.dumps({"trades": [old_row]}), encoding="utf-8")
    sb = ShadowBook(p)
    t = sb.trades[0]
    assert t.book is None and t.signal_key is None and t.approx is None and sb.meta == {}
    t2 = ShadowTrade(**{**{k: v for k, v in old_row.items() if k in _OLD_FIELDS}, "id": "cf_2", "book": "main",
                        "signal_key": "k", "variation": "CV1", "label_kind": "HORIZON", "features": {"a": 1},
                        "learning_unlocked": False, "rule_version": "v1", "approx": True})
    sb.trades.append(t2)
    sb.save()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert "meta" not in raw
    assert set(raw["trades"][0]) == set(_OLD_FIELDS)
    back = ShadowBook(p).trades[1]
    assert (back.book, back.signal_key, back.variation, back.label_kind, back.features, back.learning_unlocked,
            back.rule_version, back.approx) == ("main", "k", "CV1", "HORIZON", {"a": 1}, False, "v1", True)


# ============================================================================ defter allow_shrink (P2)
def _f():
    return SymbolFilters("ETH/USDT", MarketType.USDM_PERP)


def _big():
    return SizeSpec(Decimal("200"), AmountType.NOTIONAL, 1)


def test_allow_shrink_per_call_override_is_never_persisted(tmp_path):
    led = FuturesLedgerV2(50)
    assert led.open("ETH/USDT", "LONG", 3000, _big(), stop=2900, filters=_f(), now=T0) is None
    assert led.last_reject_reason == R_INSUFFICIENT_MARGIN                 # öznitelik False → eski red
    pos = led.open("ETH/USDT", "LONG", 3000, _big(), stop=2900, filters=_f(), now=T0, allow_shrink=True)
    assert pos is not None and pos.isolated_margin + pos.entry_fee <= Decimal("50")
    assert pos.meta["shrunk_to_margin"]["requested_qty"] == "0.066"
    assert Decimal(pos.meta["shrunk_to_margin"]["filled_qty"]) == pos.qty < Decimal("0.066")
    assert led.allow_shrink is False
    led.save(tmp_path / "futures_ledger.json")
    d = json.loads((tmp_path / "futures_ledger.json").read_text(encoding="utf-8"))
    assert d["allow_shrink"] is False
    assert FuturesLedgerV2.load(tmp_path / "futures_ledger.json").allow_shrink is False


def test_allow_shrink_false_overrides_a_true_attribute_for_one_call():
    led = FuturesLedgerV2(50, allow_shrink=True)
    assert led.open("ETH/USDT", "LONG", 3000, _big(), stop=2900, filters=_f(), now=T0, allow_shrink=False) is None
    assert led.last_reject_reason == R_INSUFFICIENT_MARGIN and led.allow_shrink is True
    pos = led.open("ETH/USDT", "LONG", 3000, _big(), stop=2900, filters=_f(), now=T0)
    assert pos is not None and "shrunk_to_margin" not in pos.meta           # öznitelik yolu: eski davranış, ek alan YOK


def test_allow_shrink_none_is_bit_identical_to_the_old_call(tmp_path):
    outs = []
    for kw in ({}, {"allow_shrink": None}):
        led = FuturesLedgerV2(500)
        pos = led.open("ETH/USDT", "LONG", 3000, SizeSpec(Decimal("100"), AmountType.NOTIONAL, 2), stop=2900,
                       targets=[3200], filters=_f(), now=T0, meta={"run_id": "r"})
        assert pos is not None
        d = led.to_dict()
        d.pop("updated_at")
        outs.append(json.dumps(d, sort_keys=True, default=str))
    assert outs[0] == outs[1]
