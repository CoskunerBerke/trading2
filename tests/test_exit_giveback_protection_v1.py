"""EXIT GIVEBACK & PROFIT PROTECTION V1 — açık pozisyon yönetimi regresyonları.

Kapsam sözleşmesi (22 zorunlu regresyon, görev metniyle birebir):

 1. LONG/SHORT R hesabı SİMETRİK.
 2. Giveback doğru.
 3. Stop yalnız SIKILAŞIR.
 4. Stop markın yanlış tarafına GEÇMEZ.
 5. Bayat markta BLOCK.
 6. Duplicate snapshot/aksiyon YOK.
 7. Stop/TP ile yönetim yarışı DETERMİNİSTİK.
 8. Aynı turda en fazla BİR aksiyon.
 9. Kısmi azaltma muhasebesi doğru.
10. Asgari qty/notional KORUNUR.
11. Tam kapanış TEK outcome/ders üretir.
12. Restart duplicate kapanış ÜRETMEZ.
13. SHADOW deftere DOKUNMAZ.
14. PAPER dışı executor FAIL-CLOSED.
15. Live path açıkken FAIL-CLOSED.
16. `UNKNOWN` ekonomi EXIT ÜRETMEZ.
17. Eski MFE/MAE'den SAHTE yol üretilmez.
18. NO-LOOKAHEAD.
19. Fee/slippage challenger'a DAHİL.
20. Dashboard eksik/bozuk şemada 500 VERMEZ.
21. RiskEngine kararı DEĞİŞMEZ.
22. Açık pozisyon fingerprint'i SHADOW'da DEĞİŞMEZ.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.learn.exit_eval import (NO_COMPLETE_PATH, aggregate, evaluate_trade,
                                        replay_policy)
from tradingbot.learn.exit_executor import (ALLOWED_MODES, B_INCREASE, B_KILLSWITCH,
                                            B_LIVE_PATH, B_LOOSEN, B_MODE_SHADOW, B_NOT_PAPER,
                                            B_STALE, B_TOUR_LIMIT, PAPER_BOUNDED, SHADOW,
                                            ExitExecutor)
from tradingbot.learn.exit_policy import (CHALLENGER_A, CHALLENGER_B, CHALLENGER_C, CHAMPION,
                                          EXIT, HOLD, REDUCE, TIGHTEN_STOP, ExitPolicyConfig,
                                          ProfitLockStep, R_GIVEBACK, R_LOCK_ARMED,
                                          R_MIN_REMAINING, R_UNKNOWN_ECON, R_WRONG_SIDE,
                                          challenger_a, challenger_b, challenger_c,
                                          evaluate_all)
from tradingbot.learn.position_path import (REJ_BACKWARD_TS, REJ_DUPLICATE, REJ_NON_FINITE,
                                            REJ_NONPOSITIVE, REJ_STALE, TICK_BAR_EXTREMES,
                                            TICK_LAST_ONLY, PositionPathStore, build_snapshot,
                                            path_completeness, r_of, side_sign, snapshot_id)

CFG = ExitPolicyConfig()


class _Pos:
    """Açık pozisyon kılığı (`accounting.models.Position` alan adlarıyla)."""

    def __init__(self, **kw):
        self.id = kw.get("id", "F1")
        self.symbol = kw.get("symbol", "ETH/USDT")
        self.side = kw.get("side", "LONG")
        self.qty = Decimal(str(kw.get("qty", 1)))
        self.initial_qty = Decimal(str(kw.get("initial_qty", kw.get("qty", 1))))
        self.entry_avg = Decimal(str(kw.get("entry", 100.0)))
        self.initial_stop = Decimal(str(kw.get("initial_stop", 98.0)))
        self.stop = Decimal(str(kw["stop"])) if kw.get("stop") is not None else self.initial_stop
        self.targets = [Decimal("104"), Decimal("108")]
        self.targets_hit = kw.get("targets_hit", 0)
        self.tp1_done = kw.get("tp1_done", False)
        self.leverage = kw.get("leverage", 2)
        self.opened_at = kw.get("opened_at", "2026-08-20T10:00:00+00:00")
        self.bars_held = kw.get("bars_held", 6)
        self.mfe_pct = Decimal(str(kw.get("mfe", 0.0)))
        self.mae_pct = Decimal(str(kw.get("mae", 0.0)))
        self.fees_paid = Decimal(str(kw.get("fees", 0.0)))
        self.funding_paid = Decimal(str(kw.get("funding_paid", 0.0)))
        self.funding_received = Decimal(str(kw.get("funding_received", 0.0)))


def _snap(**kw) -> dict:
    """Doğrudan yol snapshot sözlüğü (politika testleri için)."""
    d = {"schema_version": "position_path_v1", "trade_id": kw.get("trade_id", "F1"),
         "snapshot_id": kw.get("snapshot_id", "s1"), "ts": kw.get("ts", "2026-08-21T10:00:00+00:00"),
         "ts_ms": kw.get("ts_ms", 1787_000_000_000), "symbol": "ETH/USDT",
         "side": kw.get("side", "LONG"), "entry": kw.get("entry", 100.0),
         "mark": kw.get("mark", 100.0), "qty": kw.get("qty", 1.0),
         "initial_qty": kw.get("initial_qty", 1.0),
         "initial_stop": kw.get("initial_stop", 98.0),
         "current_stop": kw.get("current_stop", 98.0),
         "initial_risk_usdt": kw.get("initial_risk_usdt", 2.0),
         "gross_r": kw.get("gross_r"), "mfe_r": kw.get("mfe_r"), "mae_r": kw.get("mae_r"),
         "giveback_r": kw.get("giveback_r"),
         "economics_evaluated": kw.get("economics_evaluated", False),
         "position_age_hours": kw.get("position_age_hours"),
         "remaining_edge_r": kw.get("remaining_edge_r"),
         "funding_drag_r": kw.get("funding_drag_r"),
         "tick_kind": kw.get("tick_kind", TICK_BAR_EXTREMES)}
    if d["gross_r"] is None:
        d["gross_r"] = r_of(d["mark"], entry=d["entry"], initial_stop=d["initial_stop"],
                            side=d["side"])
    if d["mfe_r"] is not None and d["gross_r"] is not None and d["giveback_r"] is None:
        d["giveback_r"] = d["mfe_r"] - d["gross_r"]
    return d


# ------------------------------------------------------------------ 1: simetri


def test_01_long_and_short_r_are_symmetric():
    """Aynı büyüklükte lehte hareket, iki yönde de AYNI R vermeli."""
    long_r = r_of(102.0, entry=100.0, initial_stop=98.0, side="LONG")
    short_r = r_of(98.0, entry=100.0, initial_stop=102.0, side="SHORT")
    assert long_r == pytest.approx(1.0) and short_r == pytest.approx(1.0)
    long_loss = r_of(98.0, entry=100.0, initial_stop=98.0, side="LONG")
    short_loss = r_of(102.0, entry=100.0, initial_stop=102.0, side="SHORT")
    assert long_loss == pytest.approx(-1.0) and short_loss == pytest.approx(-1.0)
    assert side_sign("LONG") == 1.0 and side_sign("SHORT") == -1.0
    assert side_sign("PositionSide.SHORT") == -1.0


def test_01b_snapshot_r_is_symmetric_for_both_sides():
    lo, _ = build_snapshot(position=_Pos(side="LONG", entry=100.0, initial_stop=98.0, mfe=4.0),
                           mark=102.0)
    sh, _ = build_snapshot(position=_Pos(side="SHORT", entry=100.0, initial_stop=102.0, mfe=4.0),
                           mark=98.0)
    assert lo["gross_r"] == pytest.approx(sh["gross_r"])
    assert lo["mfe_r"] == pytest.approx(sh["mfe_r"])
    assert lo["giveback_r"] == pytest.approx(sh["giveback_r"])


def test_01c_r_is_none_when_stop_distance_is_zero():
    assert r_of(101.0, entry=100.0, initial_stop=100.0, side="LONG") is None
    rec, _ = build_snapshot(position=_Pos(entry=100.0, initial_stop=100.0), mark=101.0)
    assert rec["gross_r"] is None and rec["capture_ratio"] is None, "sessiz 0 YASAK"


# ------------------------------------------------------------------ 2: giveback


def test_02_giveback_is_mfe_minus_current():
    rec, _ = build_snapshot(position=_Pos(entry=100.0, initial_stop=98.0, mfe=6.0), mark=102.0)
    assert rec["mfe_r"] == pytest.approx(3.0)     # %6 / (%2 stop) = 3R
    assert rec["gross_r"] == pytest.approx(1.0)
    assert rec["giveback_r"] == pytest.approx(2.0)
    assert rec["capture_ratio"] == pytest.approx(1 / 3, abs=1e-6)


def test_02b_capture_ratio_is_none_without_favorable_excursion():
    rec, _ = build_snapshot(position=_Pos(entry=100.0, initial_stop=98.0, mfe=0.0), mark=99.0)
    assert rec["capture_ratio"] is None
    assert rec["capture_ratio_state"] == "NO_FAVORABLE_EXCURSION"


# ------------------------------------------------------------------ 3-4: stop güvenliği


def test_03_stop_only_tightens_never_loosens():
    # Stop zaten 1.0R'de; 1.5R eşiği 0.5R kilit önerir → mevcut stop DAHA SIKI, aksiyon YOK.
    s = _snap(mark=103.0, mfe_r=1.6, current_stop=102.0)     # current_stop = +1.0R
    d = challenger_a(s, CFG)
    assert d["action"] == HOLD and "STOP_ALREADY_TIGHTER" in d["reason_codes"]
    # SHORT simetrisi: stop AŞAĞI inemez
    s2 = _snap(side="SHORT", entry=100.0, initial_stop=102.0, mark=97.0, mfe_r=1.6,
               current_stop=98.0)
    d2 = challenger_a(s2, CFG)
    assert d2["action"] == HOLD and "STOP_ALREADY_TIGHTER" in d2["reason_codes"]


def test_03b_tighten_moves_stop_in_the_profitable_direction_only():
    s = _snap(mark=103.5, mfe_r=1.6, current_stop=98.0)
    d = challenger_a(s, CFG)
    assert d["action"] == TIGHTEN_STOP and R_LOCK_ARMED in d["reason_codes"]
    assert d["stop_after"] > d["stop_before"], "LONG'da stop YUKARI çekilmeli"
    assert d["locked_r"] == 0.5 and d["stop_after"] == pytest.approx(101.0)
    s2 = _snap(side="SHORT", entry=100.0, initial_stop=102.0, mark=96.5, mfe_r=1.6,
               current_stop=102.0)
    d2 = challenger_a(s2, CFG)
    assert d2["action"] == TIGHTEN_STOP
    assert d2["stop_after"] < d2["stop_before"], "SHORT'ta stop AŞAĞI çekilmeli"
    assert d2["stop_after"] == pytest.approx(99.0)


def test_04_stop_is_never_placed_on_the_wrong_side_of_mark():
    """Kâr kilidi adı altında ANINDA tetiklenecek stop önerilemez."""
    # MFE 2.6R ama fiyat 1.2R'ye düşmüş: 1.5R kilit markın ÜSTÜNDE kalır → reddedilmeli.
    s = _snap(mark=102.4, mfe_r=2.6, current_stop=98.0)
    d = challenger_a(s, CFG)
    assert d["action"] == HOLD and R_WRONG_SIDE in d["reason_codes"]
    assert d["proposed_stop"] == pytest.approx(103.0)
    s2 = _snap(side="SHORT", entry=100.0, initial_stop=102.0, mark=97.6, mfe_r=2.6,
               current_stop=102.0)
    d2 = challenger_a(s2, CFG)
    assert d2["action"] == HOLD and R_WRONG_SIDE in d2["reason_codes"]


def test_04b_min_buffer_keeps_a_gap_between_stop_and_mark():
    """Tampon olmasaydı stop mark'a yapışırdı; tampon `min_stop_buffer_r` kadar mesafe bırakır."""
    cfg = ExitPolicyConfig(min_stop_buffer_r=0.5)
    s = _snap(mark=101.6, mfe_r=1.6, current_stop=98.0)      # kilit 101.0, mark 101.6
    assert challenger_a(s, cfg)["action"] == HOLD            # 0.5R tampon = 1.0 fiyat birimi
    assert challenger_a(_snap(mark=102.5, mfe_r=1.6, current_stop=98.0), cfg)["action"] == TIGHTEN_STOP


def test_04c_profit_lock_config_rejects_a_lock_above_its_trigger():
    with pytest.raises(ValueError):
        ProfitLockStep(mfe_r=1.0, lock_r=1.5).validate()
    with pytest.raises(ValueError):
        ExitPolicyConfig(profit_lock_steps=[ProfitLockStep(mfe_r=2.0, lock_r=0.0),
                                            ProfitLockStep(mfe_r=1.0, lock_r=0.5)]).validate()


# ------------------------------------------------------------------ 5: bayat mark


def test_05_stale_and_invalid_marks_are_rejected(tmp_path):
    from tradingbot.core import iso, utc_now
    from datetime import timedelta
    now = utc_now()
    pos = _Pos()
    assert build_snapshot(position=pos, mark=float("nan"), now=now)[1] == REJ_NON_FINITE
    assert build_snapshot(position=pos, mark=float("inf"), now=now)[1] == REJ_NON_FINITE
    assert build_snapshot(position=pos, mark=0, now=now)[1] == REJ_NONPOSITIVE
    assert build_snapshot(position=pos, mark=-5, now=now)[1] == REJ_NONPOSITIVE
    old = iso(now - timedelta(hours=2))
    assert build_snapshot(position=pos, mark=101.0, now=now, mark_ts=old)[1] == REJ_STALE
    future = iso(now + timedelta(hours=1))
    assert build_snapshot(position=pos, mark=101.0, now=now, mark_ts=future)[1] == "FUTURE_TIMESTAMP"
    fresh = iso(now)
    rec, rej = build_snapshot(position=pos, mark=101.0, now=now, mark_ts=fresh)
    assert rej is None and rec is not None


def test_05b_executor_blocks_on_stale_mark():
    ex = ExitExecutor(CFG)
    intent = challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    out = ex.execute(intent, mode_value="PAPER", live_order_path=False,
                     killswitch_state="ARMED", position_open=True, mark_stale=True)
    assert out["applied"] is False and B_STALE in out["blockers"]


# ------------------------------------------------------------------ 6: duplicate


def test_06_duplicate_snapshots_and_actions_are_rejected(tmp_path):
    store = PositionPathStore(tmp_path / "p.jsonl", min_interval_s=0.0, min_r_change=0.0)
    rec, _ = build_snapshot(position=_Pos(), mark=101.0)
    assert store.append(rec) is True
    assert store.append(dict(rec)) is False, "aynı snapshot_id ikinci kez yazılamaz"
    assert store.rejected.get(REJ_DUPLICATE) == 1
    assert len(list(store.iter_rows())) == 1
    # Aksiyon idempotency anahtarı deterministik ve aksiyona duyarlı
    s = _snap(mark=103.5, mfe_r=1.6, current_stop=98.0)
    a1, a2 = challenger_a(s, CFG), challenger_a(s, CFG)
    assert a1["idempotency_key"] == a2["idempotency_key"]
    assert challenger_b(_snap(mark=103.5, mfe_r=2.0, current_stop=98.0), CFG)["idempotency_key"] \
        != a1["idempotency_key"]


def test_06b_snapshot_id_is_deterministic():
    a = snapshot_id("F1", "2026-08-21T10:00:00+00:00", 101.5)
    assert a == snapshot_id("F1", "2026-08-21T10:00:00+00:00", 101.5)
    assert a != snapshot_id("F1", "2026-08-21T10:00:01+00:00", 101.5)
    assert a != snapshot_id("F2", "2026-08-21T10:00:00+00:00", 101.5)


def test_06c_backward_timestamps_are_rejected(tmp_path):
    store = PositionPathStore(tmp_path / "p.jsonl", min_interval_s=0.0, min_r_change=0.0)
    from tradingbot.core import utc_now
    from datetime import timedelta
    now = utc_now()
    r1, _ = build_snapshot(position=_Pos(), mark=101.0, now=now)
    r2, _ = build_snapshot(position=_Pos(), mark=102.0, now=now - timedelta(minutes=5))
    assert store.append(r1) is True
    assert store.append(r2) is False, "zaman geriye AKAMAZ"
    assert store.rejected.get(REJ_BACKWARD_TS) == 1


def test_06d_executor_blocks_an_already_applied_key():
    ex = ExitExecutor(CFG)
    intent = challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    ex.applied_keys.add(intent["idempotency_key"])
    out = ex.execute(intent, mode_value="PAPER", live_order_path=False,
                     killswitch_state="ARMED", position_open=True, mark_stale=False)
    assert "ACTION_ALREADY_APPLIED" in out["blockers"]


# ------------------------------------------------------------------ 7-8: yarış ve tur sınırı


def test_07_stop_tp_and_management_race_is_deterministic(tmp_path):
    """Defter tick'i ÖNCE çalışır: kapanan pozisyon için yönetim aksiyonu üretilmez.

    Sıra `FuturesLedgerV2.tick()` içinde sabittir: likidasyon > stop > TP. Yönetim katmanı
    tick'ten SONRA çağrılır ve yalnız hâlâ açık pozisyonları görür.
    """
    from tradingbot.accounting import FuturesLedgerV2
    from tradingbot.accounting.models import AmountType, SizeSpec, TickData

    led = FuturesLedgerV2(Decimal("1000"))
    pos = led.open("ETH/USDT", "LONG", Decimal("100"),
                   SizeSpec(Decimal("200"), AmountType.NOTIONAL, 1),
                   stop=Decimal("98"), targets=[Decimal("104"), Decimal("108")])
    assert pos is not None
    closed = led.tick({"ETH/USDT": TickData(last=Decimal("97"), mark=Decimal("97"),
                                            high=Decimal("99"), low=Decimal("97"))})
    assert len(closed) == 1 and closed[0].exit_reason == "stop"
    assert "ETH/USDT" not in led.positions
    ex = ExitExecutor(CFG)
    intent = challenger_a(_snap(mark=97.0, mfe_r=1.6, current_stop=98.0), CFG)
    out = ex.execute(intent, mode_value="PAPER", live_order_path=False,
                     killswitch_state="ARMED", position_open=False, mark_stale=False)
    assert "POSITION_NOT_OPEN" in out["blockers"]


def test_08_at_most_one_action_per_position_per_tour():
    ex = ExitExecutor(CFG)
    intent = challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    ok = ex.preflight(intent, mode_value="PAPER", live_order_path=False,
                      killswitch_state="ARMED", position_open=True, mark_stale=False,
                      actions_this_tour=0)
    assert B_TOUR_LIMIT not in ok
    blocked = ex.preflight(intent, mode_value="PAPER", live_order_path=False,
                           killswitch_state="ARMED", position_open=True, mark_stale=False,
                           actions_this_tour=1)
    assert B_TOUR_LIMIT in blocked
    with pytest.raises(ValueError):
        ExitPolicyConfig(max_actions_per_position_per_tour=2).validate()


def test_08b_cooldown_blocks_a_second_action():
    from tradingbot.core import utc_now
    from datetime import timedelta
    now = utc_now()
    ex = ExitExecutor(CFG, clock=lambda: now)
    intent = challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    ex.last_action_ts[intent["trade_id"]] = now - timedelta(seconds=10)
    assert "COOLDOWN_ACTIVE" in ex.preflight(
        intent, mode_value="PAPER", live_order_path=False, killswitch_state="ARMED",
        position_open=True, mark_stale=False)
    ex.last_action_ts[intent["trade_id"]] = now - timedelta(seconds=CFG.action_cooldown_s + 1)
    assert "COOLDOWN_ACTIVE" not in ex.preflight(
        intent, mode_value="PAPER", live_order_path=False, killswitch_state="ARMED",
        position_open=True, mark_stale=False)


# ------------------------------------------------------------------ 9-10: azaltma muhasebesi


def test_09_partial_reduce_accounting_is_correct():
    s = _snap(mark=102.5, mfe_r=2.0, current_stop=98.0, qty=1.0, initial_qty=1.0)
    d = challenger_b(s, CFG)
    assert d["action"] == REDUCE and R_GIVEBACK in d["reason_codes"]
    assert d["reduce_fraction"] == 0.5
    assert d["qty_before"] == 1.0 and d["qty_after"] == pytest.approx(0.5)
    assert d["notional_after"] == pytest.approx(51.25)
    assert d["remaining_fraction"] == pytest.approx(0.5)
    assert d["qty_after"] < d["qty_before"], "azaltma pozisyonu KÜÇÜLTMELİ"


def test_09b_reduce_requires_both_a_real_run_up_and_a_real_giveback():
    # MFE yeterli ama geri verme yetersiz
    assert challenger_b(_snap(mark=103.0, mfe_r=1.8, current_stop=98.0), CFG)["action"] == HOLD
    # Geri verme yeterli ama MFE yetersiz
    assert challenger_b(_snap(mark=99.0, mfe_r=1.0, current_stop=98.0), CFG)["action"] == HOLD


def test_09c_only_one_reduce_per_position():
    s = _snap(mark=102.5, mfe_r=2.0, current_stop=98.0)
    assert challenger_b(s, CFG, reduces_done=0)["action"] == REDUCE
    d = challenger_b(s, CFG, reduces_done=1)
    assert d["action"] == HOLD and "ALREADY_REDUCED" in d["reason_codes"]


def test_10_minimum_remaining_qty_and_notional_are_protected():
    # Kalan notional asgarinin altına düşerse azaltma YAPILMAZ.
    s = _snap(mark=102.5, mfe_r=2.0, current_stop=98.0, qty=0.05, initial_qty=0.05)
    d = challenger_b(s, CFG)
    assert d["action"] == HOLD and R_MIN_REMAINING in d["reason_codes"]
    assert d["notional_after"] < CFG.min_remaining_notional_usdt
    # Kalan oran asgarinin altına düşerse de YAPILMAZ.
    cfg = ExitPolicyConfig(giveback_reduce_fraction=0.9, min_remaining_fraction=0.25)
    d2 = challenger_b(_snap(mark=102.5, mfe_r=2.0, current_stop=98.0), cfg)
    assert d2["action"] == HOLD and R_MIN_REMAINING in d2["reason_codes"]


def test_10b_executor_refuses_anything_that_grows_the_position():
    ex = ExitExecutor(CFG)
    bad = dict(challenger_b(_snap(mark=102.5, mfe_r=2.0, current_stop=98.0), CFG))
    bad["qty_after"] = bad["qty_before"] * 2
    assert B_INCREASE in ex.preflight(bad, mode_value="PAPER", live_order_path=False,
                                      killswitch_state="ARMED", position_open=True,
                                      mark_stale=False)
    bad2 = dict(bad)
    bad2["qty_after"] = 0.5
    bad2["reduce_fraction"] = 1.5
    assert B_INCREASE in ex.preflight(bad2, mode_value="PAPER", live_order_path=False,
                                      killswitch_state="ARMED", position_open=True,
                                      mark_stale=False)


def test_10c_executor_refuses_a_loosened_stop():
    ex = ExitExecutor(CFG)
    bad = dict(challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG))
    bad["stop_after"] = 97.0                      # 98.0'dan GEVŞEK
    assert B_LOOSEN in ex.preflight(bad, mode_value="PAPER", live_order_path=False,
                                    killswitch_state="ARMED", position_open=True,
                                    mark_stale=False)
    bad2 = dict(bad)
    bad2["stop_after"] = None
    assert B_LOOSEN in ex.preflight(bad2, mode_value="PAPER", live_order_path=False,
                                    killswitch_state="ARMED", position_open=True,
                                    mark_stale=False)


# ------------------------------------------------------------------ 13-15: executor güvenliği


def test_13_shadow_executor_never_touches_the_ledger():
    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(f"SHADOW modunda deftere DOKUNULDU: {name}")

    ex = ExitExecutor(CFG, ledger=_Boom())
    intents = [challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG),
               challenger_b(_snap(mark=102.5, mfe_r=2.0, current_stop=98.0), CFG)]
    res = ex.execute_many(intents, mode_value="PAPER", live_order_path=False,
                          killswitch_state="ARMED", position_open=True, mark_stale=False)
    assert res["applied"] == 0 and res["blocked"] == 2
    assert res["ledger_touched"] is False
    for r in res["results"]:
        assert r["applied"] is False and B_MODE_SHADOW in r["blockers"]
        assert r["counterfactual"] is True


def test_13b_executor_module_cannot_import_the_order_path():
    """Yapısal izolasyon: çıkış yürütücüsü emir yoluna BAĞLANAMAZ."""
    import ast
    src = Path("tradingbot/learn/exit_executor.py").read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)
    for banned in ("execution", "gateway", "accounting", "outbox", "notify"):
        assert banned not in joined, f"yasak bağımlılık: {banned} ({imported})"


def test_14_non_shadow_mode_is_fail_closed():
    assert ALLOWED_MODES == (SHADOW,)
    for bad in (PAPER_BOUNDED, "LIVE", "PAPER", ""):
        with pytest.raises(ValueError):
            ExitExecutor(CFG, mode=bad)
    ex = ExitExecutor(CFG)
    intent = challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    out = ex.execute(intent, mode_value="TESTNET", live_order_path=False,
                     killswitch_state="ARMED", position_open=True, mark_stale=False)
    assert B_NOT_PAPER in out["blockers"] and out["applied"] is False


def test_14b_config_refuses_to_activate_real_execution():
    from tradingbot.config_v3 import load_v3
    from tradingbot.core import ConfigError
    assert load_v3({"mode": "PAPER"}).exit_policy.action_mode == "SHADOW"
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "exit_policy": {"action_mode": "PAPER_BOUNDED"}})
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "exit_policy": {"action_mode": "LIVE"}})
    with pytest.raises(ConfigError):
        load_v3({"mode": "PAPER", "exit_policy": {"auto_promotion": True}})


def test_15_live_order_path_and_killswitch_are_fail_closed():
    ex = ExitExecutor(CFG)
    intent = challenger_a(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    out = ex.execute(intent, mode_value="PAPER", live_order_path=True,
                     killswitch_state="ARMED", position_open=True, mark_stale=False)
    assert B_LIVE_PATH in out["blockers"]
    out2 = ex.execute(intent, mode_value="PAPER", live_order_path=False,
                      killswitch_state="HALT_ALL", position_open=True, mark_stale=False)
    assert B_KILLSWITCH in out2["blockers"]


# ------------------------------------------------------------------ 16: UNKNOWN ekonomi


def test_16_unknown_economics_never_produces_an_exit():
    s = _snap(economics_evaluated=False, position_age_hours=500.0, remaining_edge_r=-1.0,
              funding_drag_r=1.0)
    d = challenger_c(s, CFG)
    assert d["action"] == HOLD and R_UNKNOWN_ECON in d["reason_codes"]
    # Ekonomi ölçülmüş ama alanlar eksikse yine çıkış YOK.
    s2 = _snap(economics_evaluated=True, position_age_hours=None, remaining_edge_r=None)
    assert challenger_c(s2, CFG)["action"] == HOLD
    # Ölçülmüş ve eşikler aşılmışsa çıkış üretilir.
    s3 = _snap(economics_evaluated=True, position_age_hours=500.0, remaining_edge_r=-0.2)
    d3 = challenger_c(s3, CFG)
    assert d3["action"] == EXIT and "REMAINING_EDGE_EXHAUSTED" in d3["reason_codes"]


def test_16b_snapshot_carries_unknown_p_win_when_not_evaluated():
    from tradingbot.learn.position_mgmt import UNKNOWN
    rec, _ = build_snapshot(position=_Pos(), mark=101.0, decision=None)
    assert rec["economics_evaluated"] is False
    assert rec["p_win"] == UNKNOWN and rec["p_win"] not in (0, 0.0, 0.5)


# ------------------------------------------------------------------ 17: sahte yol yasağı


def test_17_no_synthetic_path_is_derived_from_mfe_mae_summary():
    """Eski kapanışın MFE/MAE özeti bir fiyat yolu DEĞİLDİR — challenger sonucu üretilemez."""
    close = {"id": "F00001", "symbol": "SUI/USDT", "side": "SHORT", "r_multiple": -1.218,
             "mfe_pct": 0.55, "mae_pct": -5.06, "net_pnl": -0.78,
             "opened_at": "2026-08-18T16:48:57+00:00",
             "closed_at": "2026-08-19T15:06:15+00:00", "exit_reason": "stop"}
    ev = evaluate_trade(trade_id="F00001", path=[], close=close, cfg=CFG)
    assert ev["status"] == NO_COMPLETE_PATH
    assert ev["results"] == {}
    assert ev["path"]["complete"] is False and ev["path"]["reason"] == "NO_PATH"
    assert ev["actual_r"] == pytest.approx(-1.218)


def test_17b_incomplete_path_is_reported_not_patched():
    rows = [_snap(ts_ms=1000, snapshot_id="a", mark=101.0, mfe_r=0.5)]
    comp = path_completeness(rows, min_snapshots=3)
    assert comp["complete"] is False and "TOO_FEW_SNAPSHOTS" in comp["reason"]
    big = [_snap(ts_ms=0, snapshot_id="a"), _snap(ts_ms=10_000_000, snapshot_id="b"),
           _snap(ts_ms=20_000_000, snapshot_id="c")]
    assert path_completeness(big)["complete"] is False
    assert "GAP_TOO_LARGE" in path_completeness(big)["reason"]


def test_17c_aggregate_lists_no_complete_path_ids():
    close = {"id": "F1", "symbol": "E/U", "side": "LONG", "r_multiple": -1.0}
    ev = evaluate_trade(trade_id="F1", path=[], close=close, cfg=CFG)
    agg = aggregate([ev], cfg=CFG)
    assert agg["n_path_complete"] == 0 and agg["n_no_complete_path"] == 1
    assert agg["no_complete_path_ids"] == ["F1"]
    assert agg["verdict"] == "INSUFFICIENT_EXIT_SAMPLE"
    assert agg["auto_promotion"] is False


# ------------------------------------------------------------------ 18-19: no-lookahead + maliyet


#: Karsi-olgusal testlerin ortak kapanis kaydi. Yol ts'leri BU pencereye oturur ki
#: `path_completeness` "acilis/kapanis kapsanmiyor" demesin.
_OPENED = "2026-08-20T10:00:00+00:00"
_CLOSED = "2026-08-20T11:00:00+00:00"
_CLOSE = {"id": "F1", "symbol": "ETH/USDT", "side": "LONG", "r_multiple": 0.4,
          "net_pnl": 0.8, "opened_at": _OPENED, "closed_at": _CLOSED, "exit_reason": "stop"}


def _rising_then_falling_path(trade="F1") -> list[dict]:
    """0 → +2.0R → +0.4R giden gerçekçi bir yol (kâr geri verme senaryosu)."""
    from tradingbot.core import from_iso, iso
    t0 = from_iso(_OPENED)
    base = int(t0.timestamp() * 1000)
    marks = [100.0, 101.0, 102.0, 103.0, 104.0, 102.0, 100.8]
    rows = []
    peak = 0.0
    for i, m in enumerate(marks):
        r = (m - 100.0) / 2.0
        peak = max(peak, r)
        ts_ms = base + i * 600_000                      # 10 dk arayla, 1 saatlik pencere
        rows.append(_snap(trade_id=trade, snapshot_id=f"s{i}", ts_ms=ts_ms,
                          ts=iso(__import__("datetime").datetime.fromtimestamp(
                              ts_ms / 1000, tz=__import__("datetime").timezone.utc)),
                          mark=m, gross_r=r, mfe_r=peak, giveback_r=peak - r,
                          current_stop=98.0, qty=1.0, initial_qty=1.0,
                          initial_risk_usdt=2.0))
    return rows


def test_18_no_lookahead_decisions_use_only_past_snapshots():
    """Politika kararı yalnız o ana kadarki bilgiyle verilir; sonuç sonradan değiştirilmez."""
    path = _rising_then_falling_path()
    full = replay_policy(path, CHALLENGER_A, CFG, final_r=0.4)
    # Yolu kesersek, kesim ANINDAKİ kararlar birebir aynı kalmalı.
    prefix = replay_policy(path[:5], CHALLENGER_A, CFG, final_r=2.0)
    keys_full = [a["idempotency_key"] for a in full["actions"]]
    keys_pre = [a["idempotency_key"] for a in prefix["actions"]]
    assert keys_pre == keys_full[:len(keys_pre)], "geçmiş kararlar SONRADAN değişemez"
    # Kilit 2.0R'de kurulduğu için politika 1.5R kilidinde çıkmalı, 0.4R'ye düşmemeli.
    assert full["locked_stop_r"] is not None and full["locked_stop_r"] >= 0.5
    assert full["net_r"] > 0.4, "kâr kilidi geri vermeyi SINIRLAMALI"


def test_18b_champion_takes_the_actual_ledger_result():
    path = _rising_then_falling_path()
    ch = replay_policy(path, CHAMPION, CFG, final_r=0.4)
    assert ch["n_actions"] == 0, "champion snapshot düzeyinde aksiyon üretmez"
    assert ch["net_r"] == pytest.approx(0.4)
    assert ch["mfe_r"] == pytest.approx(2.0)
    assert ch["giveback_r"] == pytest.approx(1.6)


def test_19_fees_and_slippage_are_included_in_challenger_results():
    path = _rising_then_falling_path()
    free = replay_policy(path, CHALLENGER_A, CFG, final_r=0.4, fee_rate=0.0, slip_rate=0.0)
    costly = replay_policy(path, CHALLENGER_A, CFG, final_r=0.4, fee_rate=0.01, slip_rate=0.01)
    assert costly["exit_cost_r"] > free["exit_cost_r"] > -1e-12
    assert costly["net_r"] < free["net_r"], "maliyet net sonucu DÜŞÜRMELİ"
    assert costly["gross_r"] == pytest.approx(free["gross_r"])


def test_19b_evaluate_trade_reports_missed_gain_and_avoided_loss_separately():
    path = _rising_then_falling_path()
    ev = evaluate_trade(trade_id="F1", path=path, close=_CLOSE, cfg=CFG)
    assert ev["status"] == "OK"
    a = ev["results"][CHALLENGER_A]
    assert "delta_vs_champion_r" in a
    assert a["missed_gain_r"] >= 0.0 and a["avoided_loss_r"] >= 0.0
    assert min(a["missed_gain_r"], a["avoided_loss_r"]) == 0.0, "ikisi aynı anda pozitif olamaz"
    assert a["avoided_loss_r"] > 0.0, "kilit bu senaryoda zararı ÖNLEMELİ"
    json.dumps(ev, allow_nan=False)


def test_19c_aggregate_computes_delta_and_fee_delta_against_champion():
    agg = aggregate([evaluate_trade(trade_id="F1", path=_rising_then_falling_path(),
                                    close=_CLOSE, cfg=CFG)], cfg=CFG)
    by = agg["by_policy"]
    assert by[CHAMPION]["n"] == 1
    assert by[CHALLENGER_A]["delta_expectancy_r"] is not None
    assert "fee_delta_r" in by[CHALLENGER_A]
    assert "delta_expectancy_r" not in by[CHAMPION]
    assert agg["verdict"] == "INSUFFICIENT_EXIT_SAMPLE"


# ------------------------------------------------------------------ 12: sonlu sayı + restart


def test_12_no_nan_or_infinity_is_published(tmp_path):
    store = PositionPathStore(tmp_path / "p.jsonl", min_interval_s=0.0, min_r_change=0.0)
    rec, _ = build_snapshot(position=_Pos(mfe=4.0), mark=101.0)
    assert store.append(rec) is True
    for line in (tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        json.dumps(d, allow_nan=False)
        for v in d.values():
            if isinstance(v, float):
                assert math.isfinite(v)
    path = _rising_then_falling_path()
    for p in (CHAMPION, CHALLENGER_A, CHALLENGER_B, CHALLENGER_C):
        json.dumps(replay_policy(path, p, CFG, final_r=0.4), allow_nan=False)


def test_12b_restart_reloads_state_and_does_not_duplicate(tmp_path):
    p = tmp_path / "p.jsonl"
    s1 = PositionPathStore(p, min_interval_s=0.0, min_r_change=0.0)
    rec, _ = build_snapshot(position=_Pos(), mark=101.0)
    assert s1.append(rec) is True
    s2 = PositionPathStore(p, min_interval_s=0.0, min_r_change=0.0)   # "restart"
    assert s2.append(dict(rec)) is False, "restart sonrası duplicate YAZILAMAZ"
    assert len(list(s2.iter_rows())) == 1


def test_store_skips_immaterial_intermediate_snapshots(tmp_path):
    """60 sn'lik monitör diski şişirmemeli: önemsiz ara adım atlanır, yapısal değişiklik yazılır."""
    from tradingbot.core import utc_now
    from datetime import timedelta
    store = PositionPathStore(tmp_path / "p.jsonl", min_interval_s=55.0, min_r_change=0.5)
    now = utc_now()
    r1, _ = build_snapshot(position=_Pos(), mark=101.0, now=now)
    assert store.append(r1) is True
    r2, _ = build_snapshot(position=_Pos(), mark=101.01, now=now + timedelta(seconds=60))
    assert store.append(r2) is False and store.skipped_unchanged == 1
    r3, _ = build_snapshot(position=_Pos(stop=99.0), mark=101.01, now=now + timedelta(seconds=120))
    assert store.append(r3) is True, "stop değişikliği DAİMA yazılır"
    r4, _ = build_snapshot(position=_Pos(stop=99.0), mark=103.0, now=now + timedelta(seconds=180))
    assert store.append(r4) is True, "büyük R hareketi yazılır"


# ------------------------------------------------------------------ 20: dashboard


httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from tradingbot.dashboard.app import DashboardConfig, create_app  # noqa: E402


def _dirs(tmp: Path) -> tuple[Path, Path]:
    st, data = tmp / "state", tmp / "data"
    st.mkdir(), data.mkdir()
    (st / "futures_ledger.json").write_text(json.dumps(
        {"schema_version": 2, "wallet_balance": "100", "starting_equity": "100",
         "positions": {}, "history": [], "total_fees": "0"}), encoding="utf-8")
    (st / "portfolio.json").write_text(json.dumps(
        {"cash": 100.0, "starting_equity": 100.0, "positions": {}, "history": []}),
        encoding="utf-8")
    (st / "mode.json").write_text(json.dumps({"mode": "PAPER", "history": []}), encoding="utf-8")
    (st / "learning.json").write_text(json.dumps(
        {"n_trades": 1, "n_wins": 1, "sum_r": 0.4, "lessons": [], "weights": {},
         "agent_weights": {}}), encoding="utf-8")
    return st, data


@pytest.mark.parametrize("doc", [
    None, {}, {"schema_version": "unknown_v99"},
    {"n_path_complete": "cok", "by_policy": "liste-degil", "trades": 5},
    {"n_path_complete": 2, "by_policy": {"champion": {"n": float("nan"),
                                                      "expectancy_r": float("inf")}},
     "trades": [{"trade_id": "F1", "actual_r": float("nan"), "results": None}],
     "promotion_gates": "bozuk"},
])
def test_20_dashboard_never_returns_500_on_missing_or_broken_exit_eval(tmp_path, doc):
    st, data = _dirs(tmp_path)
    if doc is not None:
        st.joinpath("exit_eval.json").write_text(json.dumps(doc, allow_nan=True), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    for path in ("/learning", "/api/exit-eval", "/", "/health/live"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code, r.text[:400])
    body = c.get("/api/exit-eval").text
    assert "NaN" not in body and "Infinity" not in body


def test_20b_dashboard_shows_shadow_mode_and_no_complete_path(tmp_path):
    st, data = _dirs(tmp_path)
    st.joinpath("exit_eval.json").write_text(json.dumps({
        "schema_version": "exit_eval_v1", "exit_action_mode": "SHADOW", "applied_total": 0,
        "n_evaluated": 3, "n_path_complete": 1, "n_no_complete_path": 2,
        "no_complete_path_ids": ["F00001", "F00002"], "observation_days": 1.5,
        "verdict": "INSUFFICIENT_EXIT_SAMPLE", "policy_version": "exit_v1.0.0",
        "promotion_gates": [{"code": "MIN_PATH_COMPLETE_CLOSES", "passed": False, "detail": "1/50"}],
        "by_policy": {"champion": {"n": 1, "expectancy_r": 0.4, "total_exit_cost_r": 0.0},
                      "challenger_a_profit_lock": {"n": 1, "expectancy_r": 1.4,
                                                   "delta_expectancy_r": 1.0,
                                                   "total_exit_cost_r": 0.01,
                                                   "fee_delta_r": 0.01}},
        "trades": [{"trade_id": "F1", "symbol": "ETH/USDT", "actual_r": 0.4, "status": "OK",
                    "results": {"champion": {"net_r": 0.4},
                                "challenger_a_profit_lock": {"net_r": 1.4}}}],
    }), encoding="utf-8")
    c = TestClient(create_app(st, data, cfg=DashboardConfig(read_only=True)))
    r = c.get("/learning")
    assert r.status_code == 200
    html = r.text
    assert "Çıkış politikası" in html and "SHADOW" in html
    assert "YETERSİZ ÖRNEK" in html
    assert "NO_COMPLETE_PATH" in html
    js = c.get("/api/exit-eval").json()
    assert js["available"] is True and js["applied_total"] == 0
    assert js["verdict"] == "INSUFFICIENT_EXIT_SAMPLE"
    assert js["path_coverage"]["open_positions"] == 0


# ------------------------------------------------------------------ 11, 21, 22: motor e2e

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import test_engine_v3 as E  # noqa: E402


def _fut_fingerprint(led) -> str:
    canon = {s: {k: str(getattr(p, k, None)) for k in
                 ("side", "qty", "entry_avg", "stop", "targets", "targets_hit", "leverage",
                  "isolated_margin", "tp1_done", "initial_stop")}
             for s, p in sorted(led.positions.items())}
    return hashlib.sha256(json.dumps(canon, sort_keys=True, default=str).encode()).hexdigest()[:16]


def test_e2e_tour_records_a_real_position_path(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert summ["opened"] and eng.ledger2.positions, "yol kanıtı için giriş gerekli"
    st = eng.cfg.state_path
    p = st / "position_path.jsonl"
    assert p.exists(), "position_path.jsonl yazılmalı"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    ids = {r["trade_id"] for r in rows}
    assert ids == {str(x.id) for x in eng.ledger2.positions.values()}
    for r in rows:
        assert r["tick_kind"] == TICK_BAR_EXTREMES
        assert r["code_sha"] is None or isinstance(r["code_sha"], str)
        assert r["config_hash"]
        # Açılış turunda karar bir GİRİŞ adayıdır; giriş ekonomisi açık pozisyonun kalan
        # avantajı DEĞİLDİR ve pozisyon ekonomisi saydırılamaz.
        assert r["economics_evaluated"] is False
        json.dumps(r, allow_nan=False)
    ev = json.loads((st / "exit_eval.json").read_text(encoding="utf-8"))
    assert ev["exit_action_mode"] == "SHADOW" and ev["applied_total"] == 0
    assert ev["auto_promotion"] is False
    assert ev["verdict"] == "INSUFFICIENT_EXIT_SAMPLE"


def test_22_open_position_fingerprint_is_unchanged_by_shadow_exit_layer(tmp_path, monkeypatch):
    """SHADOW katmanı açık pozisyonun hiçbir alanını DEĞİŞTİRMEZ."""
    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    assert eng.ledger2.positions
    before = _fut_fingerprint(eng.ledger2)
    ledger_bytes = (eng.cfg.state_path / "futures_ledger.json").read_bytes()
    # Yol kaydını ve politika değerlendirmesini tekrar tekrar çalıştır
    from tradingbot.learn.position_path import TICK_LAST_ONLY as _TL
    marks = {s: E.T and None for s in ()}  # noqa: F841  (aşağıda gerçek marks kurulur)
    from tradingbot.accounting.models import TickData as _TD
    marks = {s: _TD(last=p.entry_avg, mark=p.entry_avg)
             for s, p in eng.ledger2.positions.items()}
    for _ in range(3):
        eng._record_position_path(marks, None, __import__("tradingbot.core", fromlist=["utc_now"]).utc_now(), tick_kind=_TL)
    assert _fut_fingerprint(eng.ledger2) == before, "pozisyon alanları DEĞİŞTİ"
    assert (eng.cfg.state_path / "futures_ledger.json").read_bytes() == ledger_bytes


def test_21_risk_decision_is_identical_with_exit_layer_disabled(tmp_path, monkeypatch):
    """Çıkış katmanı AKTİF KARARI değiştirmemeli."""
    eng_a = E._engine(tmp_path / "a", monkeypatch, symbols=4)
    sa = eng_a.tour(do_scan=False, obsidian=False, charts=False)
    risk_a = json.loads((eng_a.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    eng_b = E._engine(tmp_path / "b", monkeypatch, symbols=4)
    eng_b.path_store = None                 # çıkış gözlemi KAPALI
    eng_b.exit_executor = None
    sb = eng_b.tour(do_scan=False, obsidian=False, charts=False)
    risk_b = json.loads((eng_b.cfg.state_path / "risk.json").read_text(encoding="utf-8"))

    def _norm(rows):
        return [{k: v for k, v in r.items() if k not in ("at", "trade_id")} for r in rows]
    assert _norm(risk_a["last_decisions"]) == _norm(risk_b["last_decisions"])
    assert sa["opened"] == sb["opened"]
    assert sa["ledger"]["equity"] == sb["ledger"]["equity"]
    assert _fut_fingerprint(eng_a.ledger2) == _fut_fingerprint(eng_b.ledger2)


def test_11_full_close_still_produces_exactly_one_outcome_and_lesson(tmp_path, monkeypatch):
    """Çıkış katmanı eklendikten sonra da kapanış zinciri TAM BİR kez çalışmalı."""
    from tradingbot.learn.close_chain import canonical_closes
    from tradingbot.learn.reconcile import LearnedIndex

    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    s1 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s1["opened"]
    for sym, pos in list(eng.ledger2.positions.items()):
        eng._fake_live.price[sym] = float(pos.stop) * (0.98 if pos.side.value == "LONG" else 1.02)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    eng._fake_live.price.clear()
    eng.tour(do_scan=False, obsidian=False, charts=False)
    hist = eng.ledger2.history
    assert hist, "stop tetiklendiği hâlde kapanış oluşmadı"
    idx = LearnedIndex(eng.cfg.state_path / "learned_closes.jsonl").load()
    ids = [x["id"] for x in eng.learner.state.lessons]
    exits = [r for r in eng.memory.iter_rows() if r.get("kind") == "exit"]
    for c in canonical_closes(hist):
        assert ids.count(c["trade_id"]) == 1, c["trade_id"]
        assert sum(1 for r in exits if r["trade_id"] == c["trade_id"]) == 1
        assert c["close_event_id"] in idx
    ev = json.loads((eng.cfg.state_path / "exit_eval.json").read_text(encoding="utf-8"))
    assert ev["n_evaluated"] == len(hist)
    assert ev["applied_total"] == 0


def test_12c_exit_monitor_close_is_recorded_in_the_learned_index(tmp_path, monkeypatch):
    """`exit_check` yolu eskiden öğrenildi indeksine HİÇ yazmıyordu — bu regresyonu kilitler."""
    from tradingbot.learn.close_chain import close_event_id
    from tradingbot.learn.reconcile import LearnedIndex

    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    s1 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s1["opened"]
    for sym, pos in list(eng.ledger2.positions.items()):
        eng._fake_live.price[sym] = float(pos.stop) * (0.98 if pos.side.value == "LONG" else 1.02)
    closed = eng.exit_check()
    assert closed, "exit-monitor kapanış üretmeliydi"
    idx = LearnedIndex(eng.cfg.state_path / "learned_closes.jsonl").load()
    for c in closed:
        ev = close_event_id(c["id"], c["closed_at"], c["exit_reason"])
        assert ev in idx, f"{c['id']} exit-monitor kapanışı indekse yazılmadı"
        assert idx[ev]["source"] == "EXIT_MONITOR"
    ids = [x["id"] for x in eng.learner.state.lessons]
    for c in closed:
        assert ids.count(c["id"]) == 1
    # Sonraki tur AYNI kapanışı yeniden öğrenmemeli
    before = len(eng.learner.state.lessons)
    eng._fake_live.price.clear()
    eng.tour(do_scan=False, obsidian=False, charts=False)
    after = [x["id"] for x in eng.learner.state.lessons]
    for c in closed:
        assert after.count(c["id"]) == 1, f"{c['id']} İKİNCİ kez öğrenildi"
    assert len(after) >= before


def test_e2e_exit_check_path_is_marked_last_only(tmp_path, monkeypatch):
    """`exit_check` bar uçlarını BİLMEZ ve bunu açıkça işaretler."""
    eng = E._engine(tmp_path, monkeypatch, symbols=4)
    s1 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s1["opened"]
    # Fiyatı ANLAMLI kaydır: önemsiz ara adımlar bilinçli olarak atlanır (disk koruması),
    # bu yüzden yalnız gerçek bir hareket `last_only` snapshot üretir.
    for sym, pos in list(eng.ledger2.positions.items()):
        mid = float(pos.entry_avg) + (float(pos.entry_avg) - float(pos.stop)) * 0.6 * (
            1 if pos.side.value == "LONG" else -1)
        eng._fake_live.price[sym] = mid
    eng.exit_check()
    rows = [json.loads(x) for x in
            (eng.cfg.state_path / "position_path.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]
    kinds = {r["tick_kind"] for r in rows}
    assert TICK_BAR_EXTREMES in kinds
    assert TICK_LAST_ONLY in kinds, "exit-monitor snapshot'ı last_only olarak işaretlenmeli"


def test_e2e_exit_layer_failure_does_not_stop_the_tour(tmp_path, monkeypatch):
    eng = E._engine(tmp_path, monkeypatch, symbols=3)

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("çıkış katmanı arızası")
    eng.path_store = _Boom()
    eng.exit_executor = _Boom()
    summ = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert summ["run_id"] and summ["risk"]["killswitch"] == "ARMED"
    h = json.loads((eng.cfg.state_path / "health.json").read_text(encoding="utf-8"))
    assert h["state"] in ("HEALTHY", "KILL_SWITCH")


def test_policy_config_is_versioned_and_not_hardcoded():
    """Eşikler config'ten gelir ve her karara `policy_version` + `config_id` yazılır."""
    a = ExitPolicyConfig()
    b = ExitPolicyConfig(giveback_trigger_r=1.25)
    assert a.config_id != b.config_id, "eşik değişimi config_id'yi DEĞİŞTİRMELİ"
    d = challenger_b(_snap(mark=102.5, mfe_r=2.0, current_stop=98.0), b)
    assert d["policy_version"] == b.policy_version and d["config_id"] == b.config_id
    rt = ExitPolicyConfig.from_dict(b.to_dict())
    assert rt.config_id == b.config_id, "to_dict/from_dict gidiş-dönüşü kimliği KORUMALI"


def test_evaluate_all_returns_champion_and_three_challengers_separately():
    res = evaluate_all(_snap(mark=103.5, mfe_r=1.6, current_stop=98.0), CFG)
    assert set(res) == {CHAMPION, CHALLENGER_A, CHALLENGER_B, CHALLENGER_C}
    assert res[CHAMPION]["action"] == HOLD
    for r in res.values():
        assert r["applied"] is False, "politika modülü hiçbir şey UYGULAMAZ"
        assert r["trade_id"] and r["snapshot_id"]


def test_e2e_tour_start_now_does_not_reject_mid_tour_marks(tmp_path, monkeypatch):
    """Yol snapshot'i KAYIT ANI ile damgalanir — tur baslangici `now` ile DEGIL.

    Uretimde olculdu (2026-09-02): `tour()` icindeki `now` turun EN BASINDA alinir, `_marks()`
    ise turun ortasinda calisip tick'leri o anki saatle damgalar. Tur ~600 sn surdugu icin
    tur-basi `now`a gore her mark GELECEKTEN gelmis gorunuyor ve `FUTURE_TIMESTAMP` ile
    reddediliyordu; 9 pozisyonun 9'u da sessizce atlandi ve dosya HIC olusmadi.
    """
    from datetime import timedelta
    from tradingbot.accounting.models import TickData as _TD
    from tradingbot.core import iso, utc_now
    from tradingbot.learn.position_path import TICK_BAR_EXTREMES

    eng = E._engine(tmp_path, monkeypatch, symbols=6)
    s1 = eng.tour(do_scan=False, obsidian=False, charts=False)
    assert s1["opened"] and eng.ledger2.positions
    p = eng.cfg.state_path / "position_path.jsonl"
    assert p.exists(), "ilk turda yol dosyasi olusmali"
    n_before = len(p.read_text(encoding="utf-8").splitlines())

    # Tur basi `now` (600 sn eski) + tick ts = SIMDI  -> eski kodda hepsi reddedilirdi.
    stale_now = utc_now() - timedelta(seconds=600)
    marks = {s: _TD(last=pos.entry_avg * Decimal("1.05"),
                    mark=pos.entry_avg * Decimal("1.05"), ts=iso(utc_now()))
             for s, pos in eng.ledger2.positions.items()}
    before_skipped = eng.path_store.skipped_unchanged
    res = eng._record_position_path(marks, None, stale_now, tick_kind=TICK_BAR_EXTREMES)
    assert res, "cikti bos olmamali"
    assert res["positions_considered"] == len(eng.ledger2.positions)
    # ASIL IDDIA: hicbiri REDDEDILMEDI. Eski kodda hepsi `FUTURE_TIMESTAMP` aliyordu.
    assert res["rejected"] == {}, f"hicbir snapshot reddedilmemeli: {res['rejected']}"
    # Snapshot uretildi ve append asamasina ULASTI; bu turda yazilmamasinin sebebi 55 sn'lik
    # aralik kisidir (bilincli disk korumasi), REDDEDILME degil.
    assert eng.path_store.skipped_unchanged > before_skipped

    # Aralik gecince AYNI cagri gercekten yazmali (throttle kalici bir engel DEGIL).
    later = {s: _TD(last=pos.entry_avg * Decimal("1.09"),
                    mark=pos.entry_avg * Decimal("1.09"), ts=iso(utc_now()))
             for s, pos in eng.ledger2.positions.items()}
    eng.path_store.min_interval_s = 0.0
    res2 = eng._record_position_path(later, None, stale_now, tick_kind=TICK_BAR_EXTREMES)
    assert res2["rejected"] == {} and res2["snapshots_written"] > 0
    assert len(p.read_text(encoding="utf-8").splitlines()) > n_before


def test_exit_eval_surfaces_path_health_so_failures_are_not_silent(tmp_path, monkeypatch):
    """`0 yol-tam kapanis` ile `yol hic yazilmiyor` AYRI durumlardir; rapor ikisini ayirir."""
    eng = E._engine(tmp_path, monkeypatch, symbols=4)
    eng.tour(do_scan=False, obsidian=False, charts=False)
    ev = json.loads((eng.cfg.state_path / "exit_eval.json").read_text(encoding="utf-8"))
    pc = ev.get("path_cycle") or {}
    assert pc, "exit_eval yol kaydinin sagligini TASIMALI"
    assert pc["positions_considered"] == len(eng.ledger2.positions)
    assert pc["snapshots_written"] > 0
    assert pc["rejected"] == {}
    assert pc["applied"] == 0
    ps = ev.get("path_store") or {}
    assert ps.get("total_snapshots", 0) > 0
    json.dumps(ev, allow_nan=False)
