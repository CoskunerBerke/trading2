"""tradingbot.replay.fidelity testleri — plan cikarimi, giris paritesi, cikis kurallari, ileri-bakis yasagi.

Butun testler AG'SIZ calisir: mumlar `MemoryBars` ile verilir. Amac, sadakat kosumunun gercek
`FuturesLedgerV2` muhasebesini kullandigini ve kendi aritmetigini uydurmadigini kanitlamaktir.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tradingbot.accounting import AmountType, FeeSchedule, LiquidationParams, SizeSpec, SlippageModel
from tradingbot.core import D
from tradingbot.replay.fidelity import (
    BAR_ADVANCE_MS,
    Bar,
    MemoryBars,
    ReplayConfig,
    _bar_advance_flags,
    compare,
    derive_targets,
    exit_family,
    exit_fill_diagnosis,
    funding_lookup_from,
    load_plans,
    path_targets_index,
    plan_from_record,
    replay_config_from_ledger,
    replay_trade,
    summarise,
)

SYM = "TEST/USDT"
T0 = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
MIN_MS = 60_000


def _cfg(**kw) -> ReplayConfig:
    """Uretim defterinin ayarlari (fees 0.05 taker, kayma 3 bps, tp1 %50)."""
    base = dict(fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal("0.05")),
                slippage=SlippageModel(fixed_bps=Decimal("3")), liq_params=LiquidationParams(),
                tp1_fraction=Decimal("0.5"), breakeven_at_mfe_r=Decimal("0"), worst_case=True, tp_maker=False)
    base.update(kw)
    return ReplayConfig(**base)


def _bars(rows, *, start: datetime = T0, step_ms: int = MIN_MS) -> list[Bar]:
    """rows = [(o, h, l, c), ...] → ardisik mumlar."""
    t0 = int(start.timestamp() * 1000)
    return [Bar(open_ms=t0 + i * step_ms, open=D(str(o)), high=D(str(h)), low=D(str(low)), close=D(str(c)),
                close_ms=t0 + (i + 1) * step_ms - 1) for i, (o, h, low, c) in enumerate(rows)]


def _record(**kw) -> dict:
    """Gercek bir defter `history` kaydinin asgari sekli (LONG, giris 100, stop 90)."""
    rec = {
        "id": "F00001", "symbol": SYM, "side": "LONG", "entry": "100.03",
        "exit_reason": "stop", "opened_at": "2026-08-20T11:59:30+00:00", "closed_at": "2026-08-20T12:30:00+00:00",
        "r_multiple": "-1.0", "net_pnl": "-1.0", "funding": "0", "bars_held": 3, "mfe_pct": "0", "mae_pct": "-10",
        "leverage": 1, "quantity": "0.999", "entry_fee": "0.049964985", "exit_price": "90.0", "liquidation_price": "0",
        "requested_notional": "100", "amount_type": "NOTIONAL",
        "features": {"initial_stop": 90.0}, "exit_fee": "0.045",
        "fills": [{"kind": "entry", "price": "100.03", "ref_price": "100", "qty": "1.0", "ts": "2026-08-20T11:59:30+00:00"}],
    }
    rec.update(kw)
    return rec


# --------------------------------------------------------------------------- plan cikarimi
def test_derive_targets_is_2r_and_3r_both_directions():
    assert derive_targets(D("100"), D("90"), "LONG") == [D("120"), D("130")]
    assert derive_targets(D("100"), D("110"), "SHORT") == [D("80"), D("70")]


def test_plan_targets_prefer_recorded_path_then_fill_then_geometry():
    # (1) path kaydi
    p = plan_from_record(_record(), recorded_targets=[121.0, 131.0])
    assert p.target_source == "path" and p.targets == [D("121.0"), D("131.0")]
    # (2) gerceklesen TP fill'i — hedef fiyati fill'de KAYITLIDIR, geometri oradan geri cozulur
    rec = _record(fills=[{"kind": "entry", "price": "100.03", "ref_price": "100", "qty": "1.0", "ts": "2026-08-20T11:59:30+00:00"},
                         {"kind": "hedef1", "price": "122", "ref_price": "122", "qty": "0.5", "ts": "2026-08-20T12:10:00+00:00"}])
    p = plan_from_record(rec)
    assert p.target_source == "fill"
    assert abs(p.targets[0] - D("122")) < D("1e-20")     # T1 = e0 + 2R  -> e0 = (122 + 2*90)/3 = 100.6667
    assert abs(p.targets[1] - D("132.6666666667")) < D("1e-9")           # T2 = T1 + R, R = (122-90)/3
    # (3) hicbiri yoksa geometri
    p = plan_from_record(_record())
    assert p.target_source == "derived" and p.targets == [D("120"), D("130")]


def test_plan_from_record_requires_entry_fill_and_initial_stop():
    with pytest.raises(ValueError):
        plan_from_record(_record(fills=[]))
    with pytest.raises(ValueError):
        plan_from_record(_record(features={}))


def test_path_targets_index_takes_first_snapshot_and_skips_junk():
    lines = ['{"trade_id": "F1", "targets": [1, 2]}', "", "bozuk", '{"trade_id": "F1", "targets": [9, 9]}']
    assert path_targets_index(lines) == {"F1": [1.0, 2.0]}


def test_load_plans_skips_unusable_records_without_raising():
    assert len(load_plans({"history": [_record(), _record(id="F2", fills=[])]})) == 1


def test_replay_config_reads_production_numbers_verbatim():
    cfg = replay_config_from_ledger({"fees": {"maker_pct": "0.02", "taker_pct": "0.05"}, "slippage": {"fixed_bps": "3.0"},
                                     "liq_params": {"liq_fee_pct": "0.5", "fee_cushion_pct": "0", "use_brackets": True},
                                     "tp1_fraction": "0.5", "breakeven_at_mfe_r": "1.0", "worst_case": True})
    assert (cfg.fees.taker_pct, cfg.slippage.fixed_bps, cfg.tp1_fraction, cfg.breakeven_at_mfe_r) == \
           (Decimal("0.05"), Decimal("3"), Decimal("0.5"), Decimal("1.0"))


# --------------------------------------------------------------------------- giris paritesi
def test_entry_is_reproduced_by_the_real_ledger_not_by_local_arithmetic():
    """Giris fill'i defterin kendi `market_fill_price` + tick kuantizasyonundan gelir; replay kopyalamaz."""
    cfg = _cfg()
    led = cfg.new_ledger()
    pos = led.open(SYM, "LONG", D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=D("90"), now=T0)
    assert pos.entry_avg == D("100.03")            # 100*(1+0.0003)=100.03, 0.01 tick'e YUKARI
    plan = plan_from_record(_record())
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 100, 100, 100)])}), cfg, interval="1m")
    assert (out.entry_fill, out.quantity, out.entry_fee) == (pos.entry_avg, pos.qty, pos.entry_fee)
    row = compare(plan, out)
    assert row["entry_px_match"] and row["qty_match"] and row["entry_fee_match"]


@pytest.mark.parametrize("side,rows", [
    ("LONG", [(100, 101, 99, 100), (100, 101, 89, 95)]),
    ("SHORT", [(100, 101, 99, 100), (100, 111, 99, 105)]),
])
def test_round_trip_reproduces_a_ledger_written_record_exactly(side, rows):
    """ALTIN TEST: defterin KENDI yazdigi kaydi ayni mumlarla yeniden kos → her alan BIREBIR ayni.

    Boylece sadakat kosumunun kalan farklari yalnizca VERI (mum/funding/gozlem ani) farkindan
    gelebilir; muhasebe yolundan gelemez.
    """
    cfg = _cfg()
    stop = D("90") if side == "LONG" else D("110")
    bars = _bars(rows)
    led = cfg.new_ledger()
    pos = led.open(SYM, side, D("100"), SizeSpec(D("100"), AmountType.NOTIONAL, 1), stop=stop,
                   targets=derive_targets(D("100"), stop, side), now=T0 - timedelta(seconds=30),
                   features={"initial_stop": float(stop)})
    assert pos is not None
    closed = []
    for b in bars:
        # `bar_open`: defter, bar uclarini yalnizca bar pozisyonun omru icinde acilmissa kullanir.
        # REFERANS defter de uretimdeki gibi provenans vermelidir; aksi halde referans mark-only
        # calisir, replay uclari kullanir ve gidis-donus esitligi TANIM GEREGI bozulur.
        closed += led.tick({SYM: {"last": b.close, "mark": b.close, "high": b.high, "low": b.low,
                                  "bar_open": b.open_dt.isoformat()}}, now_utc=b.close_dt)
    assert closed, "kurulum kapanmadi"
    rec = closed[-1].to_dict()

    plan = plan_from_record(rec)
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): bars}), cfg, interval="1m")
    row = compare(plan, out)
    assert row["exit_match"] and row["entry_px_match"] and row["qty_match"] and row["entry_fee_match"]
    assert (out.exit_price, out.r_multiple, out.net_pnl) == (D(rec["exit_price"]), D(rec["r_multiple"]), D(rec["net_pnl"]))
    assert (row["d_r"], row["d_net"], row["d_exit_px_pct"]) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------- cikis kurallari
def test_stop_fills_at_level_when_mark_stays_on_the_safe_side():
    """Bar ici dip stop'u deler ama kapanis stop'un ustunde: dolum TAM stop seviyesinden (kayma ile)."""
    plan = plan_from_record(_record())
    bars = _bars([(100, 101, 89, 95)])
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): bars}), cfg=_cfg(), interval="1m")
    assert out.closed and exit_family(out.exit_reason) == "STOP"
    assert out.exit_price == D("90") * (Decimal("1") - Decimal("0.0003"))


def test_stop_gaps_through_and_fills_at_mark():
    """Kapanis stop'un ALTINDA: bosluktan gecis, dolum mark'tan (daha kotu fiyat)."""
    plan = plan_from_record(_record())
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 101, 85, 86)])}), cfg=_cfg(), interval="1m")
    assert out.exit_price == D("86") * (Decimal("1") - Decimal("0.0003"))


def test_tp1_takes_half_and_pulls_stop_to_break_even():
    """T1 dokunusu: `tp1_fraction` kadar kismi kapanis, kalan icin stap GERCEK basa-basa cekilir."""
    plan = plan_from_record(_record())
    bars = _bars([(100, 121, 100, 120), (120, 120, 99, 101)])    # once T1=120, sonra basa-bas stop
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): bars}), cfg=_cfg(), interval="1m")
    assert exit_family(out.exit_reason) == "BE_STOP"
    # T1'de yarisi ~2R'den alindi, kalan basa-basta kapandi -> yaklasik 1R
    assert out.r_multiple is not None and Decimal("0.9") < out.r_multiple < Decimal("1.05")


def test_last_target_closes_the_whole_position_at_the_target_price():
    plan = plan_from_record(_record())
    bars = _bars([(100, 121, 100, 120), (120, 131, 120, 130)])
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): bars}), cfg=_cfg(), interval="1m")
    assert exit_family(out.exit_reason) == "TP2"
    # T1 0.499 adet @120, kalan 0.500 adet @130 -> miktar agirlikli ortalama, ikisi de TAM hedef fiyati
    assert out.exit_price == (D("0.499") * D("120") + D("0.5") * D("130")) / D("0.999")


def test_same_bar_stop_and_target_resolves_to_stop_worst_case():
    """Belgelenmis belirsizlik politikasi: ayni mumda stop ve hedef → STOP."""
    plan = plan_from_record(_record())
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 125, 89, 100)])}), cfg=_cfg(), interval="1m")
    assert exit_family(out.exit_reason) == "STOP"
    assert out.ambiguous_bars == 1 and out.ambiguity_decided


def test_mfe_breakeven_can_arm_and_be_hit_in_the_same_bar():
    """MFE basa-bas kurali acikken bar ici siralama onemlidir; teshis bayragi bunu isaretler."""
    plan = plan_from_record(_record())
    # kapanis 105: basa-bas (~100.09) mark'in DOGRU tarafinda kalir, kural kurulur; ayni mumun dibi 99 onu vurur
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 111, 99, 105)])}),
                       cfg=_cfg(breakeven_at_mfe_r=Decimal("1")), interval="1m")
    assert exit_family(out.exit_reason) == "BE_STOP" and out.be_armed_and_hit_same_bar


def test_breakeven_rule_can_be_gated_by_deployment_time():
    """`be_mfe_active_from` oncesi kural KAPALI kosar — dagitim tarihine sadakat."""
    plan = plan_from_record(_record())
    src = MemoryBars({(SYM, "1m"): _bars([(100, 111, 100, 105), (105, 105, 89, 95)])})
    late = replay_trade(plan, src, _cfg(breakeven_at_mfe_r=Decimal("1")), interval="1m",
                        be_mfe_active_from=T0 + timedelta(days=1))
    assert exit_family(late.exit_reason) == "STOP"              # kural devrede degil → asil stop
    early = replay_trade(plan, src, _cfg(breakeven_at_mfe_r=Decimal("1")), interval="1m")
    assert exit_family(early.exit_reason) == "BE_STOP"


def test_position_that_never_exits_is_reported_open_not_guessed():
    plan = plan_from_record(_record())
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 101, 99, 100)] * 5)}), cfg=_cfg(), interval="1m")
    assert not out.closed and out.note == "STILL_OPEN" and out.exit_reason is None
    assert compare(plan, out)["rep_exit"] == "OPEN"


def test_missing_bars_are_reported_not_silently_skipped():
    out = replay_trade(plan_from_record(_record()), MemoryBars({}), cfg=_cfg(), interval="1m")
    assert not out.closed and out.note == "NO_BARS" and out.n_bars == 0


# --------------------------------------------------------------------------- ileri bakis yasagi
def test_entry_bar_prefix_is_never_fed():
    """Giris 11:59:30'da; 11:59 mumunun giris ONCESI dibi stop'u delse bile replay onu GORMEZ."""
    plan = plan_from_record(_record())
    bars = _bars([(100, 100, 50, 100), (100, 101, 99, 100)], start=datetime(2026, 8, 20, 11, 59, tzinfo=timezone.utc))
    out = replay_trade(plan, MemoryBars({(SYM, "1m"): bars}), cfg=_cfg(), interval="1m")
    assert out.n_bars == 1 and not out.closed


def test_bar_advance_flags_fire_only_on_4h_boundaries():
    start = datetime(2026, 8, 20, 11, 0, tzinfo=timezone.utc)
    bars = _bars([(1, 1, 1, 1)] * 3, start=start, step_ms=BAR_ADVANCE_MS // 4)   # 11:00, 12:00, 13:00
    assert _bar_advance_flags(bars, start) == [False, True, False]               # yalniz 12:00 UTC


# --------------------------------------------------------------------------- funding ve teshis
def test_funding_lookup_matches_settlement_within_tolerance_only():
    t = int(datetime(2026, 8, 20, 16, tzinfo=timezone.utc).timestamp() * 1000)
    lk = funding_lookup_from([(t, Decimal("0.0001"))])
    hit = lk(SYM, datetime(2026, 8, 20, 16, tzinfo=timezone.utc))
    assert hit["rate"] == Decimal("0.0001")
    # Kayit venue gecmisinden geliyor: DOGRULANMIS olmali, aksi halde defter donemi kapatamaz.
    assert hit["verified"] is True
    assert lk(SYM, datetime(2026, 8, 20, 8, tzinfo=timezone.utc)) is None


def test_funding_is_applied_at_settlement_hours():
    """Pozisyon 16:00 UTC settlement'ini gecerse funding tahakkuk EDER (LONG pozitif oranda ODER)."""
    plan = plan_from_record(_record(opened_at="2026-08-20T15:58:00+00:00", closed_at="2026-08-20T16:30:00+00:00"))
    start = datetime(2026, 8, 20, 15, 59, tzinfo=timezone.utc)
    t = int(datetime(2026, 8, 20, 16, tzinfo=timezone.utc).timestamp() * 1000)
    src = MemoryBars({(SYM, "1m"): _bars([(100, 101, 99, 100)] * 5, start=start)}, funding={SYM: [(t, Decimal("0.001"))]})
    out = replay_trade(plan, src, _cfg(), interval="1m")
    assert out.funding < 0                                   # LONG, oran pozitif → oder
    assert replay_trade(plan, src, _cfg(), interval="1m", use_funding=False).funding == 0


def test_exit_fill_diagnosis_separates_level_fills_from_market_observations():
    src = MemoryBars({(SYM, "1m"): _bars([(90, 91, 89, 90)], start=datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc))})
    at_stop = _record(fills=[{"kind": "entry", "price": "100.03", "ref_price": "100", "qty": "1", "ts": "2026-08-20T11:59:30+00:00"},
                             {"kind": "stop", "price": "89.97", "ref_price": "90.0", "qty": "1", "ts": "2026-08-20T12:30:00+00:00"}])
    assert exit_fill_diagnosis(at_stop, src)["class"] == "AT_STOP"
    gap = _record(fills=[{"kind": "entry", "price": "100.03", "ref_price": "100", "qty": "1", "ts": "2026-08-20T11:59:30+00:00"},
                         {"kind": "stop", "price": "89.5", "ref_price": "89.5", "qty": "1", "ts": "2026-08-20T12:30:00+00:00"}])
    d = exit_fill_diagnosis(gap, src)
    assert d["class"] == "GAP_THROUGH" and d["in_recorded_minute"] and d["lag_min"] == 0


def test_summarise_counts_and_quantiles_are_honest_about_open_trades():
    plan = plan_from_record(_record())
    closed = compare(plan, replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 101, 89, 95)])}), _cfg(), interval="1m"))
    still_open = compare(plan, replay_trade(plan, MemoryBars({(SYM, "1m"): _bars([(100, 101, 99, 100)])}), _cfg(), interval="1m"))
    s = summarise([closed, still_open])
    assert s["n"] == 2 and s["closed"] == 1 and s["exit_match"] == 1
    assert s["abs_d_r_max"] is not None                      # acik islem yuzdeliklerde SAYILMAZ, cokmez
