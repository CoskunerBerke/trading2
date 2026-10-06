# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2b — atıf kodları `attribution_v1` (docs/SYSTEM_LEARNING_ENGINE_V1.md §5.4; P2 kabul 7).

Her kayıp ve kazanç kodu için SENTETİK ALTIN YOL: elle kurulmuş bar yolu (`pathrec.path_metrics` ile aynı yol ölçüleri:
MFE/MAE, sıra, capture) + maliyet/bağlam alanları → beklenen kod (ve birincil kod) çıkar. Kapsama testi kayıttaki
(`ATTRIBUTION_SPEC`) her kodun en az bir altın senaryosu olduğunu denetler. Kayıt mühürlüdür: `ATTRIBUTION_SHA`
burada ve belgede sabittir (eşik değişikliği = `attribution_v2`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from tradingbot.research_engine import attribution as AT
from tradingbot.research_engine import pathrec as PR

#: §9.2 mühür: `attribution_v1` tanımının sha256'sı (P2 uygulama notlarında da yazılıdır). DEĞİŞİRSE CI kırılır.
ATTRIBUTION_SHA_PINNED = "3f4596e37822f1603db6c296937005d45bcfd0215a18f1dc993ba535a0236626"
OPENED = "2026-09-20T10:00:00+00:00"
OPENED_MS = int(datetime.fromisoformat(OPENED).timestamp() * 1000)
NEXT_DAY_MS = int(datetime(2026, 9, 21, tzinfo=timezone.utc).timestamp() * 1000)
M1 = 60_000


def bars_from(points: list[tuple[float, float, float, float]], *, tail: list[tuple[float, float, float, float]] = ()) -> pd.DataFrame:
    """(açılış, yüksek, düşük, kapanış) listesi → 1m yol barları (işlem barları phase 0, kuyruk phase 1)."""
    rows = []
    for i, (o, h, lo, c) in enumerate(points):
        rows.append([OPENED_MS + (i + 1) * M1, o, h, lo, c, 0])
    n = len(points)
    for j, (o, h, lo, c) in enumerate(tail):
        rows.append([OPENED_MS + (n + j + 1) * M1, o, h, lo, c, 1])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "phase"])
    df["phase"] = df["phase"].astype("int8")
    return df


def golden(bars: pd.DataFrame | None, *, exit_px: float, exit_reason: str = "stop", side: str = "LONG", entry: float = 100.0,
           stop: float = 99.0, fee_r: float = 0.02, funding_r: float = 0.0, slip_r: float = 0.0, targets=None,
           exit_basis: str = "LEVEL", book: str = "strategy_paper_box", **extra) -> dict:
    """Altın satır: birim risk 1 (qty 1, risk 1 USDT) → R = fiyat farkı. Yol ölçüleri `path_metrics`'ten (gerçek kod)."""
    sg = 1 if side == "LONG" else -1
    gross_r = sg * (exit_px - entry) - slip_r
    net_r = gross_r - fee_r - funding_r
    m = PR.path_metrics(bars, side=side, entry=Decimal(str(entry)), rpu=Decimal(str(abs(entry - stop))), opened_ms=OPENED_MS,
                        exit_price=Decimal(str(exit_px)), step_ms=M1) if bars is not None else {}
    row = {"trade_key": f"{book}|T1|{OPENED}", "book": book, "side": side, "symbol": "SOL/USDT", "opened_at": OPENED,
           "closed_at": "2026-09-20T14:00:00+00:00", "entry_fill": f"{entry:.2f}", "ref_price": f"{entry:.2f}",
           "initial_stop": f"{stop:.2f}", "stop_dist_pct": abs(entry - stop) / entry * 100, "risk_usdt": "1", "qty": "1",
           "net_r": repr(net_r), "gross_r": repr(gross_r), "net_pnl": repr(net_r),
           "cost_r": {"fee": repr(fee_r), "funding": repr(funding_r), "slippage_in_fills": repr(slip_r),
                      "total": repr(fee_r + funding_r)},
           "exit_reason": exit_reason, "exit_basis": exit_basis, "targets": targets if targets is not None else [],
           "tp1_done": False, "family": "FADE" if book == "strategy_paper_box" else "TREND",
           "path_source": "STORE_1M" if bars is not None else "MISSING",
           "path": {"order_ambiguous": m.get("order_ambiguous"), "mae_pct": m.get("mae_pct"), "mfe_pct": m.get("mfe_pct")},
           "ambiguous_bars": m.get("ambiguous_bars", 0)}
    for k in ("mfe_r", "mae_r", "order", "capture_ratio", "giveback_r", "time_to_1r"):
        row[k] = m.get(k)
    row.update(extra)
    return row


def codes(row: dict, inp: AT.AttrInputs | None = None, bars: pd.DataFrame | None = None) -> dict:
    inp = inp or AT.AttrInputs()
    if bars is not None:
        inp.bars = bars
    return AT.attribute(row, inp)


FLAT = [(100.0, 100.05, 99.95, 100.0)]
COVERED: set[str] = set()


def fired(res: dict, code: str, *, primary: bool = False) -> None:
    allc = res["codes_loss"] + res["codes_win"]
    assert code in allc, (code, allc, res["evidence"], res["not_evaluable"])
    if primary:
        assert res["primary_code"] == code, (res["primary_code"], allc)
    COVERED.add(code)


# ============================================================================ kayıp kodları
def test_golden_loss_codes_mechanical_and_cost():
    b = bars_from([(100, 100.1, 98.0, 98.2)])
    r = codes(golden(b, exit_px=97.5, exit_reason="likidasyon", exit_basis="LIQUIDATION"), bars=b)
    fired(r, "LIQUIDATION_LEVERAGE", primary=True)
    r = codes(golden(b, exit_px=98.6, exit_reason="stop", exit_basis="GAP"), bars=b)
    fired(r, "GAP_FILL", primary=True)
    up = bars_from([(100, 100.2, 99.9, 100.05)])
    # brüt (kayma öncesi) > 0 ≥ net; alt tür en büyük maliyet payı
    r = codes(golden(up, exit_px=100.05, exit_reason="BOX_EOD_FLAT", fee_r=0.10, slip_r=0.0), bars=up)
    fired(r, "COST_KILLED_FEE", primary=True)
    r = codes(golden(up, exit_px=100.05, exit_reason="BOX_EOD_FLAT", fee_r=0.02, funding_r=0.12), bars=up)
    fired(r, "COST_KILLED_FUNDING", primary=True)
    fired(r, "FUNDING_AGAINST")
    r = codes(golden(up, exit_px=100.05, exit_reason="BOX_EOD_FLAT", fee_r=0.02, slip_r=0.08), bars=up)
    fired(r, "COST_KILLED_SLIPPAGE", primary=True)
    r = codes(golden(up, exit_px=100.05, exit_reason="BOX_EOD_FLAT", fee_r=0.20, slip_r=0.10), bars=up)
    fired(r, "TIGHT_STOP_COST_MULTIPLIER")
    assert r["primary_code"] == "COST_KILLED_FEE"


def test_golden_stopped_then_reversed_and_wrong_direction():
    # stop (MFE 0,1R < 0,3) → WRONG_DIRECTION; kuyruk orijinal hedefe (+1,5R) gidiyor → STOPPED_THEN_REVERSED (öncelikli)
    b = bars_from([(100, 100.1, 99.5, 99.6), (99.6, 99.7, 98.9, 99.0)],
                  tail=[(99.0, 100.0, 98.95, 99.9), (99.9, 101.6, 99.8, 101.5)])
    row = golden(b, exit_px=99.0, exit_reason="stop", targets=[101.5])
    r = codes(row, bars=b)
    fired(r, "STOPPED_THEN_REVERSED", primary=True)
    fired(r, "WRONG_DIRECTION")
    # kuyruk hedefe ulaşmıyor → yalnız WRONG_DIRECTION
    b2 = bars_from([(100, 100.1, 99.5, 99.6), (99.6, 99.7, 98.9, 99.0)], tail=[(99.0, 99.5, 98.5, 98.7)])
    r = codes(golden(b2, exit_px=99.0, exit_reason="stop", targets=[101.5]), bars=b2)
    assert r["primary_code"] == "WRONG_DIRECTION" and "STOPPED_THEN_REVERSED" not in r["codes_loss"]
    # hedefsiz kural (targets == []): orijinal hedef +1R
    b3 = bars_from([(100, 100.1, 99.5, 99.6), (99.6, 99.7, 98.9, 99.0)], tail=[(99.0, 101.1, 98.95, 101.0)])
    r = codes(golden(b3, exit_px=99.0, exit_reason="stop", targets=[]), bars=b3)
    assert "STOPPED_THEN_REVERSED" in r["codes_loss"] and r["evidence"]["STOPPED_THEN_REVERSED"]["target_r"] == 1.0
    # kuyruk tutma sınırının (Box gün sonu) ötesinde hedefe ulaşırsa sayılmaz
    row3 = golden(b3, exit_px=99.0, exit_reason="stop", targets=[], max_hold={"rule": "EOD_UTC"})
    late = b3.copy()
    late.loc[late["phase"] == 1, "timestamp"] = NEXT_DAY_MS + 60_000              # ertesi UTC günü
    r = codes(row3, bars=late)
    assert "STOPPED_THEN_REVERSED" not in r["codes_loss"]


def test_golden_stop_too_tight_by_atr_and_by_noise_band():
    b = bars_from([(100, 100.1, 98.9, 99.0)])
    r = codes(golden(b, exit_px=99.0), AT.AttrInputs(atr_entry=5.0), bars=b)
    fired(r, "STOP_TOO_TIGHT")
    assert r["evidence"]["STOP_TOO_TIGHT"]["stop_dist_atr"] == 0.2
    r = codes(golden(b, exit_px=99.0), AT.AttrInputs(atr_entry=1.5), bars=b)
    assert "STOP_TOO_TIGHT" not in r["codes_loss"]
    # ampirik gürültü bandı: önceki 30+ kazancın |mae%| medyanı (1,5%) > stop %1
    prior = [{"net_r": "1", "closed_at": f"2026-09-{d:02d}T00:00:00+00:00", "path": {"mae_pct": -1.5}} for d in range(1, 19)] * 2
    band, n = AT.noise_band(prior)
    assert (band, n) == (1.5, 36)
    r = codes(golden(b, exit_px=99.0), AT.AttrInputs(noise_band_pct=band, noise_band_n=n), bars=b)
    assert r["evidence"]["STOP_TOO_TIGHT"]["noise_band_pct"] == 1.5
    r = codes(golden(b, exit_px=99.0), AT.AttrInputs(noise_band_pct=band, noise_band_n=29), bars=b)
    assert "STOP_TOO_TIGHT" not in r["codes_loss"]                    # n < 30: bant kullanılmaz


def test_golden_late_entry_giveback_profit_not_taken_time_decay():
    b = bars_from([(100, 100.1, 99.5, 99.6), (99.6, 99.7, 98.9, 99.0)])
    r = codes(golden(b, exit_px=99.0, signal_ctx={"signal_close": 99.5, "atr14": 1.0}), bars=b)
    fired(r, "LATE_ENTRY")
    assert r["evidence"]["LATE_ENTRY"]["fill_beyond_signal_atr"] == 0.5
    # MFE'den önce MAE −0,8R (sıra MAE_FIRST, belirsiz değil)
    b2 = bars_from([(100, 100.05, 99.2, 99.3), (99.3, 100.3, 99.25, 100.2), (100.2, 100.25, 99.6, 99.7)])
    r = codes(golden(b2, exit_px=99.7, exit_reason="STRATEGY_EXIT"), bars=b2)
    assert r["evidence"]["LATE_ENTRY"]["mae_r_before_mfe"] == -0.8
    # MFE ≥ 1R ve net ≤ 0 → GIVEBACK; ayrıca TP1 (101) dokundu ama alınmadı
    b3 = bars_from([(100, 101.2, 99.9, 101.0), (101.0, 101.05, 99.8, 99.9)])
    r = codes(golden(b3, exit_px=99.9, exit_reason="STRATEGY_EXIT", targets=[101.0]), bars=b3)
    fired(r, "GIVEBACK", primary=True)
    fired(r, "PROFIT_NOT_TAKEN")
    # zaman çıkışı, |brüt| < 0,25 → TIME_DECAY
    b4 = bars_from([(100, 100.1, 99.95, 100.0), (100.0, 100.08, 99.9, 99.95)])
    r = codes(golden(b4, exit_px=99.9, exit_reason="TIME_STOP_6_BARS", fee_r=0.02), bars=b4)
    fired(r, "TIME_DECAY", primary=True)


def test_golden_context_codes_regime_btc_vol_and_main_bot():
    b = bars_from([(100, 100.1, 98.9, 99.0)])
    row = golden(b, exit_px=99.0, situation_entry={"h4_trend": "UP", "h4_vol_regime": "NORMAL"},
                 situation_exit={"h4_trend": "RANGE", "h4_vol_regime": "NORMAL"}, btc_ctx_entry={"trend": "DOWN"})
    r = codes(row, AT.AttrInputs(btc_move_pct=-1.2, atr_pct_entry=0.01, atr_pct_exit=0.02), bars=b)
    fired(r, "REGIME_SHIFT")
    fired(r, "AGAINST_BTC")
    fired(r, "VOL_SPIKE")
    assert r["primary_code"] == "WRONG_DIRECTION"
    r = codes(row, AT.AttrInputs(btc_move_pct=+1.2, atr_pct_entry=0.01, atr_pct_exit=0.014), bars=b)
    assert not {"AGAINST_BTC", "VOL_SPIKE"} & set(r["codes_loss"])
    # ana bot: provenance'taki GİRİŞ kararı (kapanıştaki last_decisions değil)
    row = golden(b, exit_px=99.0, book="main_fut",
                 agents_ctx={"entry_features": {"n_dissent": 3.0, "n_vetoes": 2.0, "rr": 1.5}})
    r = codes(row, bars=b)
    for c in ("DISSENT_WAS_RIGHT", "TOO_MANY_WARNINGS", "LOW_RR"):
        fired(r, c)
    row = golden(b, exit_px=99.0, book="main_fut", targets=[103.0], agents_ctx={"entry_features": {"n_dissent": 0.0}})
    r = codes(row, bars=b)
    assert "LOW_RR" not in r["codes_loss"] and "DISSENT_WAS_RIGHT" not in r["codes_loss"]


def test_golden_noise_loss_and_unclassified():
    b = bars_from([(100, 100.02, 99.97, 99.99)])
    r = codes(golden(b, exit_px=99.99, exit_reason="BOX_EOD_FLAT", fee_r=0.02), bars=b)
    assert r["primary_code"] == "TIME_DECAY"
    r = codes(golden(b, exit_px=99.99, exit_reason="STRATEGY_EXIT", fee_r=0.02), bars=b)
    fired(r, "NOISE_LOSS", primary=True)
    # büyük kural çıkışı kaybı, başka kod yok → sınıflanmadı (uydurulmaz)
    b2 = bars_from([(100, 100.1, 99.4, 99.5)])
    r = codes(golden(b2, exit_px=99.5, exit_reason="STRATEGY_EXIT", fee_r=0.01), bars=b2)
    assert r["primary_code"] is None and r["codes_loss"] == [] and r["outcome"] == AT.LOSS


# ============================================================================ kazanç kodları
def test_golden_win_codes():
    b = bars_from([(100, 101.6, 99.95, 101.5)])
    r = codes(golden(b, exit_px=101.6, exit_reason="hedef1", exit_basis="GAP", targets=[101.5]), bars=b)
    fired(r, "LUCKY_GAP", primary=True)
    b2 = bars_from([(100, 100.05, 99.1, 99.2), (99.2, 101.6, 99.15, 101.5)])
    r = codes(golden(b2, exit_px=101.5, exit_reason="hedef1", targets=[101.5]), bars=b2)
    fired(r, "LUCKY_WIN", primary=True)
    assert r["evidence"]["LUCKY_WIN"]["alias"] == "FRAGILE"
    same = {"h4_trend": "UP", "h4_vol_regime": "NORMAL"}
    b3 = bars_from([(100, 100.6, 100.0, 100.5), (100.5, 101.6, 100.4, 101.5)])
    r = codes(golden(b3, exit_px=101.5, exit_reason="hedef1", targets=[101.5], situation_entry=same, situation_exit=same,
                     book="strategy_paper_trend4h", fee_r=0.01), bars=b3)
    fired(r, "TREND_CONTINUATION", primary=True)
    fired(r, "CLEAN_ENTRY")
    fired(r, "COST_EFFICIENT")
    fired(r, "TRAIL_CAPTURE")
    r = codes(golden(b3, exit_px=100.8, exit_reason="BOX_EOD_FLAT", signal_ctx={"box_mid": 100.5}), bars=b3)
    fired(r, "MEAN_REVERSION_DONE", primary=True)
    r = codes(golden(b3, exit_px=100.03, exit_reason="başa-baş stop", fee_r=0.0), bars=b3)
    fired(r, "BREAKEVEN_SAVED", primary=True)
    r = codes(golden(b3, exit_px=100.6, exit_reason="STRATEGY_EXIT", funding_r=-0.15, book="strategy_paper"), bars=b3)
    fired(r, "FUNDING_TAILWIND")


def test_order_dependent_codes_are_excluded_on_ambiguous_or_barless_paths():
    # aynı barda yeni MFE ve yeni MAE (ilk bar dışında) → belirsiz → LUCKY_WIN değerlendirilmez
    b = bars_from([(100, 100.2, 99.9, 100.1), (100.1, 101.6, 99.1, 101.5)])
    row = golden(b, exit_px=101.5, exit_reason="hedef1", targets=[101.5])
    assert row["ambiguous_bars"] == 1 and row["path"]["order_ambiguous"]
    r = codes(row, bars=b)
    assert r["excluded_order"] and "LUCKY_WIN" not in r["codes_win"] and "TREND_CONTINUATION" not in r["codes_win"]
    # yolsuz satır (EXTREMES_ONLY): sıra yok
    row = golden(None, exit_px=101.5, exit_reason="hedef1", targets=[101.5], mfe_r=1.6, mae_r=-0.9, order=None)
    row["path_source"] = "EXTREMES_ONLY"
    r = codes(row)
    assert r["excluded_order"] and "LUCKY_WIN" not in r["codes_win"]


def test_r_less_rows_are_not_guessed():
    row = golden(bars_from(FLAT), exit_px=99.0)
    for k in ("net_r", "gross_r", "risk_usdt"):
        row[k] = None
    row["cost_r"] = None
    row["net_pnl"] = "-3.2"
    r = codes(row)
    assert r["outcome"] == AT.LOSS and not r["has_r"]
    assert {"COST_KILLED", "GIVEBACK"} <= set(r["not_evaluable"]) and "NOISE_LOSS" not in r["codes_loss"]


def test_every_registered_code_has_a_golden_path():
    for fn in (test_golden_loss_codes_mechanical_and_cost, test_golden_stopped_then_reversed_and_wrong_direction,
               test_golden_stop_too_tight_by_atr_and_by_noise_band, test_golden_late_entry_giveback_profit_not_taken_time_decay,
               test_golden_context_codes_regime_btc_vol_and_main_bot, test_golden_noise_loss_and_unclassified, test_golden_win_codes):
        fn()
    registered = set(AT.LOSS_RULES) | set(AT.WIN_RULES)
    assert registered == set(AT.LOSS_PRIORITY) | set(AT.WIN_PRIORITY)
    assert registered - COVERED == set(), sorted(registered - COVERED)


def test_attribution_registry_is_sealed():
    assert AT.ATTRIBUTION_SHA == ATTRIBUTION_SHA_PINNED, AT.ATTRIBUTION_SHA
    spec = AT.ATTRIBUTION_SPEC
    assert spec["id"] == "attribution_v1" and spec["thresholds"]["WRONG_DIRECTION_MFE_R"] == 0.3
    assert spec["thresholds"]["STOP_TOO_TIGHT_ATR"] == 0.5 and spec["thresholds"]["LUCKY_WIN_MAE_R"] == 0.8
    # sonuç her satırda kayıt sha'sını taşır
    r = codes(golden(bars_from(FLAT), exit_px=99.0))
    assert r["registry_sha"] == AT.ATTRIBUTION_SHA and r["version"] == "attribution_v1"
