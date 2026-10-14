# -*- coding: utf-8 -*-
"""M2X politikası v1 (docs/M2_AGGRESSIVE_V1.md §2) — mühür, kademe merdiveni, histerezis, durdurma, açık risk tavanı,
kriz bütçesi, ortak küçültme ve boyut. Ağ yok, saat yok."""
from __future__ import annotations

import math

import pytest

from tradingbot import m2x_policy as P


def _snap(st, x, *, u=None, gap=False, knobs=P.V1, at="t"):
    return P.observe(st, x=x, kind=P.SNAPSHOT, at=at, u=u, data_gap=gap, knobs=knobs)


# ---------------------------------------------------------------------------- mühür
def test_policy_seal_is_frozen():
    # ön kayıt sayıları değişirse mühür değişir → bu test düşer (değişiklik = m2x_v2 + yeni ön kayıt)
    assert P.M2X_POLICY_SHA == "dfc2f68d2cdf4035"
    assert P.POLICY_VERSION == "m2x_v1"


def test_v1_knobs_match_registry():
    assert P.V1.risk_pct == (2.0, 1.0, 0.5, 0.0)
    assert P.V1.or_cap_pct == (20.0, 12.0, 6.0, 0.0)
    assert [t[1] for t in P.M2X_POLICY_V1["tiers"]] == [0.0, 0.15, 0.25, 0.35]
    assert P.M2X_POLICY_V1["halt_dd"] == 0.5 and P.M2X_POLICY_V1["step_up_streak"] == 5
    assert P.V1.slots == 10 and P.V1.leverage_mode == "l_liq" and P.V1.key_b and P.V1.ladder and P.V1.halt


def test_proportional_arm_risk():
    k = P.Knobs(proportional_n=28)
    assert k.tier_risk_pct(0) == pytest.approx(20.0 / 28)
    assert k.tier_risk_pct(1) == pytest.approx(12.0 / 28)
    assert k.tier_risk_pct(2) == pytest.approx(6.0 / 28)
    assert P.V1.tier_risk_pct(0) == 2.0


# ---------------------------------------------------------------------------- merdiven
@pytest.mark.parametrize("ddk,tier", [(0.0, 0), (0.1499, 0), (0.15, 1), (0.2499, 1), (0.25, 2), (0.3499, 2), (0.35, 3),
                                      (0.49, 3), (0.8, 3)])
def test_tier_of_dd_bounds(ddk, tier):
    assert P.tier_of_dd(ddk) == tier


def test_down_is_immediate_and_can_skip_tiers():
    st = P.PolicyState.start(100.0)
    ev = _snap(st, 70.0)                                    # DDₖ %30 → K1'den doğrudan K3
    assert st.tier == 2 and ev[0]["kind"] == "DOWN" and ev[0]["from"] == 0 and ev[0]["to"] == 2
    assert P.entry_gate(st) is None


def test_entry_observation_moves_down_but_never_up_or_peak():
    st = P.PolicyState.start(100.0)
    P.observe(st, x=120.0, kind=P.ENTRY)                   # O2: zirve güncellenmez
    assert st.peak == 100.0 and st.tier_peak == 100.0
    P.observe(st, x=80.0, kind=P.ENTRY)
    assert st.tier == 1
    for _ in range(10):
        P.observe(st, x=99.0, kind=P.ENTRY)                # O2'de yukarı çıkış yok
    assert st.tier == 1


def test_step_up_needs_five_consecutive_a_passes_with_hysteresis_band():
    st = P.PolicyState.start(100.0)
    _snap(st, 84.0)                                         # K2 (DDₖ %16)
    assert st.tier == 1
    for _ in range(4):
        _snap(st, 89.0)                                     # DDₖ %11 > %10: bant içinde, A geçmez
    assert st.tier == 1 and st.streak == 0
    for _ in range(4):
        _snap(st, 90.5)                                     # DDₖ %9,5 ≤ %10: A geçer
    assert st.tier == 1 and st.streak == 4
    _snap(st, 89.5)                                         # geçmeyen görüntü sayacı sıfırlar
    assert st.streak == 0
    for _ in range(5):
        _snap(st, 90.5)
    assert st.tier == 0 and st.streak == 0
    assert st.events[-1]["kind"] == "UP" and st.events[-1]["key"] == "A" and not st.events[-1]["anchored"]


def test_step_up_is_one_tier_at_a_time():
    st = P.PolicyState.start(100.0)
    _snap(st, 66.0)                                         # K3
    assert st.tier == 2
    for _ in range(5):
        _snap(st, 99.0)                                     # A her kademe için geçer ama tek seferde bir kademe
    assert st.tier == 1
    for _ in range(5):
        _snap(st, 99.0)
    assert st.tier == 0


def test_key_b_steps_up_and_reanchors_tier_peak_not_true_peak():
    st = P.PolicyState.start(100.0)
    for i in range(21):
        _snap(st, 100.0, u=0.0)
    _snap(st, 70.0, u=-5.0)                                 # K3; U geçmişi var
    assert st.tier == 2
    # defter dipte kalıyor (A geçmez) ama M2'nin birim-R eğrisi 20 görüntüde artıda
    for i in range(30):
        _snap(st, 70.0, u=-5.0 + 0.5 * (i + 1))
        if st.tier < 2:
            break
    assert st.tier == 1
    up = [e for e in st.events if e["kind"] == "UP"][-1]
    assert up["key"] == "B" and up["anchored"]
    assert st.peak == 100.0                                 # P değişmez
    assert st.ddk(70.0) == pytest.approx(0.20)              # Pₖ := E / (1 − (0,25 − 0,05))
    _snap(st, 70.0, u=20.0)                                  # demirlemeden sonra hemen geri inmez
    assert st.tier == 1


def test_key_b_needs_twenty_snapshot_history_and_measurable_u():
    st = P.PolicyState.start(100.0)
    _snap(st, 80.0, u=0.0)
    assert st.tier == 1
    for i in range(10):
        _snap(st, 80.0, u=float(i))                          # yalnız 11 görüntü: ΔU20 yok → B geçmez
    assert st.tier == 1 and st.streak == 0
    st2 = P.PolicyState.start(100.0)
    for i in range(25):
        _snap(st2, 100.0, u=float(i))
    _snap(st2, 80.0, u=None)                                 # U ölçülemedi → B geçmez
    assert st2.tier == 1 and st2.streak == 0


def test_key_b_disabled_in_arm_j():
    st = P.PolicyState.start(100.0)
    k = P.Knobs(key_b=False)
    for i in range(21):
        _snap(st, 100.0, u=0.0, knobs=k)
    _snap(st, 80.0, u=0.0, knobs=k)
    for i in range(20):
        _snap(st, 80.0, u=float(i + 1), knobs=k)
    assert st.tier == 1


def test_soft_halt_blocks_entries_and_halt_is_permanent():
    st = P.PolicyState.start(100.0)
    _snap(st, 64.0)
    assert st.tier == 3 and P.entry_gate(st) == P.SKIP_SOFT_HALT
    _snap(st, 50.0)                                         # DD %50 → DUR
    assert st.halted and st.tier_name == P.HALTED and P.entry_gate(st) == P.SKIP_HALTED
    for _ in range(30):
        _snap(st, 120.0, u=float(_))                        # toparlanma DUR'u kaldırmaz (yalnız sahip)
    assert st.halted and P.entry_gate(st) == P.SKIP_HALTED


def test_halt_is_measured_from_true_peak_not_tier_peak():
    st = P.PolicyState.start(100.0)
    for i in range(21):
        _snap(st, 100.0, u=0.0)
    _snap(st, 62.0, u=-5.0)                                 # K4 (DDₖ %38)
    for i in range(10):
        _snap(st, 62.0, u=-5.0 + i + 1)                     # B ile K3'e çıkar, Pₖ demirlenir
    assert st.tier <= 2 and st.tier_peak < st.peak
    _snap(st, 50.5)
    assert not st.halted
    _snap(st, 49.9)
    assert st.halted


def test_data_gap_changes_nothing_and_resets_streak():
    st = P.PolicyState.start(100.0)
    _snap(st, 84.0)
    for _ in range(3):
        _snap(st, 95.0)
    assert st.streak == 3
    ev = _snap(st, 10.0, gap=True)                          # veri boşluğu DUR'u asla tetiklemez
    assert ev[0]["kind"] == "GAP" and not st.halted and st.tier == 1 and st.streak == 0


def test_new_entries_off_gate():
    st = P.PolicyState.start(100.0)
    assert P.entry_gate(st, new_entries=False) == P.SKIP_NEW_ENTRIES_OFF


def test_ladder_and_halt_off_for_arm_c():
    st = P.PolicyState.start(100.0)
    k = P.Knobs(ladder=False, halt=False)
    _snap(st, 40.0, knobs=k)
    assert st.tier == 0 and not st.halted and P.entry_gate(st) is None


# ---------------------------------------------------------------------------- risk ölçüleri ve tavanlar
def test_crash_loss_formula():
    # likidasyon yakın: q·(m − liq) + 0,005·q·m
    assert P.crash_loss_one(2.0, 100.0, 80.0) == pytest.approx(2 * 20 + 0.005 * 200)
    # likidasyon uzak (kaldıraç 1): q·m·(0,5 + 0,001)
    assert P.crash_loss_one(2.0, 100.0, 1.0) == pytest.approx(200 * 0.501)
    assert P.crash_loss_one(2.0, 100.0, None) == pytest.approx(200 * 0.501)
    # kârdaki pozisyon: kriz kaybı gerçekleşmemiş kârı içerir
    assert P.crash_loss_one(1.0, 150.0, 80.0) == pytest.approx(70 + 0.75)
    assert P.crash_loss_one(1.0, 150.0, 75.0) == pytest.approx(150 * 0.501)   # %50 düşüş likidasyondan önce gelir


def test_open_risk_mark_vs_entry_measure():
    assert P.open_risk_one(2.0, 110.0, 90.0) == pytest.approx(40.0)
    assert P.open_risk_one(2.0, 80.0, 90.0) == 0.0
    assert P.open_risk_one(2.0, 110.0, 90.0, entry=100.0, measure="entry") == pytest.approx(20.0)


def test_gaps_and_disabled_caps():
    h_or, h_cl = P.gaps(equity=200.0, peak=220.0, open_risk=30.0, crash_loss=50.0, or_cap_pct=20.0)
    assert h_or == pytest.approx(10.0) and h_cl == pytest.approx(200 - 110 - 50)
    k = P.Knobs(or_cap=False, crash_budget=False)
    assert P.gaps(equity=200.0, peak=220.0, open_risk=30.0, crash_loss=50.0, or_cap_pct=20.0, knobs=k) == (math.inf, math.inf)


def test_allocate_common_scaling_and_sequential_floor():
    lam, al = P.allocate([(4.0, 8.0)] * 3, 12.0, 100.0)    # OR boşluğu tam yetiyor
    assert lam == pytest.approx(1.0) and all(s == pytest.approx(1.0) for s, _ in al)
    lam, al = P.allocate([(4.0, 8.0)] * 4, 8.0, 100.0)     # λ = 0,5 → hepsi yarım boy
    assert lam == pytest.approx(0.5) and all(s == pytest.approx(0.5) for s, _ in al)
    lam, al = P.allocate([(4.0, 8.0)] * 10, 3.0, 100.0)    # λ = 0,075 < 0,25 → sırayla 0,25 boy (1 OR), kalan atlanır
    assert lam < 0.25
    assert [s for s, _ in al[:3]] == [0.25, 0.25, 0.25]
    assert all(s is None and why == P.SKIP_OR for s, why in al[3:])
    lam, al = P.allocate([(4.0, 8.0)] * 10, 100.0, 4.0)    # kriz bütçesi bağlıyor
    assert [s for s, _ in al[:2]] == [0.25, 0.25] and al[2] == (None, P.SKIP_CL)
    lam, al = P.allocate([(4.0, 8.0)] * 2, -1.0, 100.0)    # boşluk yok → hepsi atlanır
    assert all(s is None for s, _ in al)


def test_shrink_fraction_h2():
    f = P.shrink_fraction(equity=100.0, peak=120.0, open_risk=24.0, crash_loss=30.0, or_cap_pct=12.0)
    assert f == pytest.approx(0.5)
    f = P.shrink_fraction(equity=100.0, peak=180.0, open_risk=24.0, crash_loss=30.0, or_cap_pct=None)
    assert f == pytest.approx(10.0 / 30.0)
    assert P.shrink_fraction(equity=80.0, peak=200.0, open_risk=1.0, crash_loss=1.0, or_cap_pct=None) == 0.0


# ---------------------------------------------------------------------------- boyut ve kaldıraç
def test_plan_entry_raises_leverage_to_l_liq_and_keeps_buffer():
    # s = %12 → L_need 1 (marj slotu yeterli), L_liq 4 (1/4 − 0,004 ≥ 0,24)
    pl = P.plan_entry(equity=200.0, fill=100.0, stop=88.0, risk_usdt=4.0, tier_risk_pct=2.0, available=200.0, min_notional=5.0)
    assert pl.ok and pl.leverage == 4 and pl.l_liq == 4
    assert pl.risk_usdt == pytest.approx(4.0)
    assert (100.0 - pl.liq) / 100.0 >= 2 * 0.12 - 1e-9
    g = P.plan_entry(equity=200.0, fill=100.0, stop=88.0, risk_usdt=4.0, tier_risk_pct=2.0, available=200.0, min_notional=5.0,
                     knobs=P.Knobs(leverage_mode="l_need"))
    assert g.ok and g.leverage == g.l_need < pl.leverage


def test_plan_entry_wide_stop_has_no_leverage():
    pl = P.plan_entry(equity=200.0, fill=100.0, stop=70.0, risk_usdt=4.0, tier_risk_pct=2.0, available=200.0, min_notional=5.0)
    assert pl.ok and pl.leverage == 1


def test_plan_entry_slot_margin_cap_limits_tight_stop():
    # s = %2 → n_risk = 200; slot marjı 0,95·200/10 = 19; L ≤ 4 → notional ≤ 76
    pl = P.plan_entry(equity=200.0, fill=100.0, stop=98.0, risk_usdt=4.0, tier_risk_pct=2.0, available=200.0, min_notional=5.0)
    assert pl.ok and pl.notional == pytest.approx(76.0) and pl.risk_usdt < 4.0


def test_min_notional_bump_cap_two_times_tier_risk():
    # K3 (%0,5): E 100, d 0,5; s = %50'ye yakın → notional ~1 < 5 → çıkarma riski 2,5 > min(2×0,5, 2) = %1 → atla
    pl = P.plan_entry(equity=100.0, fill=100.0, stop=60.0, risk_usdt=0.5, tier_risk_pct=0.5, available=100.0, min_notional=5.0)
    assert not pl.ok and pl.reason in (P.SKIP_MIN_NOTIONAL, P.SKIP_LIQ)
    pl = P.plan_entry(equity=100.0, fill=100.0, stop=90.0, risk_usdt=0.4, tier_risk_pct=0.5, available=100.0, min_notional=5.0)
    assert pl.ok and pl.size_rule == "BUMP_MIN_NOTIONAL" and pl.risk_usdt == pytest.approx(0.5)


def test_plan_entry_shrunk_below_quarter_is_margin_skip():
    # serbest marj 12, rezerv 10 → kullanılabilir 2 → notional ≤ 8 (L 4) → risk 0,4 < 0,25·d = 1 → M2X_MARGIN
    pl = P.plan_entry(equity=200.0, fill=100.0, stop=95.0, risk_usdt=4.0, tier_risk_pct=2.0, available=12.0, min_notional=5.0,
                      min_risk_usdt=1.0)
    assert not pl.ok and pl.reason == P.SKIP_MARGIN
    # en küçük emir bile sığmıyor (kullanılabilir 1 → notional ≤ 4 < 5) → yine marj atlaması
    pl = P.plan_entry(equity=200.0, fill=100.0, stop=95.0, risk_usdt=4.0, tier_risk_pct=2.0, available=11.0, min_notional=5.0,
                      min_risk_usdt=1.0)
    assert not pl.ok and pl.reason == P.SKIP_MARGIN
    ok = P.plan_entry(equity=200.0, fill=100.0, stop=95.0, risk_usdt=4.0, tier_risk_pct=2.0, available=20.0, min_notional=5.0,
                      min_risk_usdt=1.0)
    assert ok.ok and ok.size_rule == "SHRUNK_TO_MARGIN" and 1.0 <= ok.risk_usdt < 4.0


def test_liq_price_matches_ledger_formula():
    from decimal import Decimal

    from tradingbot.accounting import AmountType, FuturesLedgerV2, SizeSpec, default_brackets
    led = FuturesLedgerV2(200, brackets=default_brackets())
    pos = led.open("X/USDT", "LONG", Decimal("100"), SizeSpec(Decimal("50"), AmountType.NOTIONAL, 3), stop=Decimal("90"))
    assert pos is not None
    assert P.liq_price_long(float(pos.entry_avg), float(pos.qty), float(pos.isolated_margin)) == \
        pytest.approx(float(pos.liquidation_price), rel=1e-9)
