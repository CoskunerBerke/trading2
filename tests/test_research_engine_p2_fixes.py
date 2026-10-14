# -*- coding: utf-8 -*-
"""Sürekli öğrenme motoru P2 — inceleme düzeltmeleri (2026-10-08; inceleme 2026-10-07 "FAIL": B1, M1–M6 ve küçükler).
Her madde önce BAŞARISIZ olan bir testle yazıldı, sonra düzeltildi (docs/SYSTEM_LEARNING_ENGINE_V1.md P2 "Değişiklik
(2026-10-08, inceleme düzeltmesi; gerçek veriye uygulanmadan önce)").

* **B1** Canlı defter stopu seviyenin ötesindeki İLK 60 sn örnekten doldurur (`GAP_FILL_AT_FIRST_OBSERVATION`,
  `first_source = PRICE`). Seviye yolda sürekli işlem gördüyse (geçiş barının açılışı stopun berisinde) bu bir
  ÖRNEKLEME aşmasıdır: `exit_basis = LEVEL` (`exit_fill_detail.mechanism = SAMPLED`); `GAP` yalnız bar açılışı boşluğu ya da
  aşma ≥ 0,25R. Fidelity kaydın örnek dolumunu modeller (`RECORDED_FILL`; kural dilimi penceresinde), aşmayı FILL_BASIS
  saymaz. `GAP_FILL` birincil öncelikte karar kodlarının (yön, dar stop, geç giriş) arkasındadır.
* **M1** R ayrıştırması belgenin harfiyen tanımıdır: zamanlama/stop/çıkış = gerçek − o eksenin ızgara medyanı.
* **M2** LATE_ENTRY'nin MAE kolu: MFE'den ÖNCE (MFE barından kesin önceki barlarda) > 0,7R aleyhte ve MFE ≥ 0,3R.
* **M3** Sıralama ayni ölçüyle (işlem başına tek ölçü; defter görünümü yalnız cf_aux_v1 işlemleri), boyut ekseni
  sıralamaya ve öneriye girmez, defter görünümü n ≥ 30 ve ≥ 10 gün, yalnız iyileştiren hücre, "seçim" etiketiyle.
* **M4** Uzlaştırmanın ledger kolu S1a okumasına göredir (sonradan kapanan/revize edilen kayıt tutarsızlık değildir).
* **M5** Artımlı kurulum yalnız satırın okuma penceresindeki içerik değişince yeniden kurar (günlük ekleme kurdurmaz).
"""
from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_engine_fixtures import night_of  # noqa: E402
from research_engine_p2_fixtures import (H1, KCOLS, M1, M5, World, aggregate, dt_of, ms, new_ledger, price_at,  # noqa: E402
                                         run_trade, utc)

from tradingbot.accounting import AmountType, SizeSpec  # noqa: E402
from tradingbot.research_engine import attribution as AT  # noqa: E402
from tradingbot.research_engine import journal as J  # noqa: E402

NOW = utc(2026, 10, 6, 2)
SYMX = "XRP/USDT"


# ============================================================================ B1: tasarlanmış 1m yollar
def _designed_m1(designs: dict[int, list[tuple[float, float, float, float]]], *, start: int, end: int) -> pd.DataFrame:
    """Düz taban (o = c = p, ±0,01 fitil) + `designs[t]`: `t` barından başlayarak girişe (= o anki seviye) göre ofsetli
    (açılış, yüksek, düşük, kapanış) barları. Fiyatlar 2 ondalık."""
    rows, p, t = [], 100.0, start
    while t < end:
        if t in designs:
            e = p
            for do, dh, dl, dc in designs[t]:
                o, h, lo, c = (round(e + x, 2) for x in (do, dh, dl, dc))
                rows.append([t, o, max(h, o, c), min(lo, o, c), c])
                t += M1
            p = rows[-1][4]
            continue
        rows.append([t, p, round(p + 0.01, 2), round(p - 0.01, 2), p])
        t += M1
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close"])
    df["volume"] = 100.0
    df["close_time"] = df["timestamp"] + M1 - 1
    df["quote_volume"] = (df["volume"] * df["close"]).round(4)
    df["trades"] = 10
    df["taker_buy_base"] = 50.0
    df["taker_buy_quote"] = (50.0 * df["close"]).round(4)
    return df[KCOLS]


DESCENT = [(0, .01, -.12, -.10), (-.10, -.08, -.25, -.22), (-.22, -.20, -.40, -.38), (-.38, -.36, -.46, -.45)]
#: A: sürekli geçiş, küçük örnek aşması (−0,54 örnek; stop −0,50 → 0,04 / 0,53 ≈ 0,075R) — B: bar açılışı boşluğu (−0,58
#: açılış) — C: sürekli geçiş, büyük örnek aşması (≈ 0,28R ≥ 0,25R) — D: örneklerin kaçırdığı fitil 5 saat önce (yeniden oynatma o
#: barda seviyeden durur; kayıt 5 saat sonra örnekten): kural dilimi penceresi (4h) dışında → gerçek FILL_BASIS farkı.
DESIGNS = {
    "A": DESCENT + [(-.45, -.44, -.55, -.54)],
    "B": DESCENT + [(-.58, -.57, -.61, -.59)],
    "C": DESCENT + [(-.45, -.44, -.66, -.65)],
    "D": [(0, .01, -.12, -.10), (-.10, -.08, -.55, -.20)] + [(-.20, -.19, -.21, -.20)] * 300 + [(-.20, -.18, -.40, -.38),
                                                                                               (-.38, -.36, -.62, -.60)],
}
ENTRY_AT = {"A": utc(2026, 9, 28, 3, 0, 13), "B": utc(2026, 9, 28, 9, 0, 13), "C": utc(2026, 9, 28, 15, 0, 13),
            "D": utc(2026, 9, 29, 1, 0, 13)}


def _b1_world(tmp_path):
    w = World(tmp_path)
    start, end = ms(utc(2026, 9, 27)), ms(utc(2026, 10, 1))
    m1 = _designed_m1({ms(t) // M1 * M1 + M1: DESIGNS[k] for k, t in ENTRY_AT.items()}, start=start, end=end)
    led = new_ledger()
    w.v.futs[""] = led
    keys = {}
    for k, t in ENTRY_AT.items():
        e = price_at(m1, ms(t))
        stop, tgt = round(e - 0.5, 2), [round(e + 0.75, 2)]
        pos = led.open(SYMX, "LONG", D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=stop, targets=tgt, now=t)
        w.provenance(pos.id, SYMX, "LONG", stop=stop, targets=tgt, opened_at=pos.opened_at)
        rec = run_trade(led, SYMX, m1, opened_ms=ms(t), hold_ms=7 * H1, sampled=True)
        assert rec is not None and rec.exit_reason == "stop", (k, rec and rec.exit_reason)
        ef = rec.features["exit_fill"]
        assert ef["basis"] == "GAP_FILL_AT_FIRST_OBSERVATION" and ef["first_source"] == "PRICE", (k, ef)
        keys[k] = f"main_fut|{rec.id}|{rec.opened_at}"
    w.seal(frames={"XRPUSDT": {"1m": m1, "5m": aggregate(m1, M5)}}, spot=())
    w.v.night(night_of("2026-10-06"))
    s = J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    rows = {r["trade_key"]: r for r in J.iter_journal(w.paths)}
    return w, s, {k: rows[v] for k, v in keys.items()}


def _rpu(row: dict) -> float:
    return float(D(row["risk_usdt"]) / D(row["qty"]))           # R birimi: |dolum − stop| (dolum 3 bps kaymalı)


def test_b1_sampled_stop_fill_is_level_and_gap_needs_a_bar_open_gap_or_a_large_overshoot(tmp_path):
    _w, _s, rows = _b1_world(tmp_path)
    a, b, c, d = rows["A"], rows["B"], rows["C"], rows["D"]
    assert abs(_rpu(a) - 0.53) < 1e-9
    # A: seviye sürekli işlem gördü (geçiş barının açılışı stopun berisinde) → örnekleme aşması, boşluk DEĞİL
    assert a["exit_basis"] == "LEVEL", a["exit_basis"]
    assert a["exit_fill_detail"]["mechanism"] == "SAMPLED" and a["exit_fill_detail"]["bar_open_gap"] is False
    assert abs(a["exit_overshoot_r"] - 0.04 / _rpu(a)) < 1e-6 and a["field_source"]["exit_overshoot_r"] == "MEASURED"
    # B: geçiş barı stopun ötesinde AÇILDI → piyasa boşluğu
    assert b["exit_basis"] == "GAP" and b["exit_fill_detail"]["mechanism"] == "BAR_OPEN_GAP"
    # C: sürekli geçiş ama örnek aşması 0,15 / 0,53 ≈ 0,28R ≥ 0,25R → boşluk benzeri dolum
    assert c["exit_basis"] == "GAP" and c["exit_fill_detail"]["mechanism"] == "SAMPLED"
    assert abs(c["exit_overshoot_r"] - 0.15 / _rpu(c)) < 1e-6 and c["exit_overshoot_r"] >= 0.25
    # D: fitil 5 saat önce (örnekler kaçırdı), dolum örnekten 0,10 / 0,53 ≈ 0,19R ötede → LEVEL (aşma < 0,25R)
    assert d["exit_basis"] == "LEVEL" and abs(d["exit_overshoot_r"] - 0.10 / _rpu(d)) < 1e-6


def test_b1_fidelity_models_the_recorded_sample_fill_and_keeps_real_differences(tmp_path):
    _w, s, rows = _b1_world(tmp_path)
    for k in ("A", "B", "C"):
        f = rows[k]["fidelity"]
        assert f["status"] == "OK" and f["fill_model"] == "RECORDED_FILL", (k, f)
        assert abs(D(f["delta_r"])) <= D("0.05"), (k, f["delta_r"])
    f = rows["D"]["fidelity"]
    assert f["status"] == "FAILED" and f["primary_reason"] == "FILL_BASIS" and f["fill_model"] == "LEVEL", f
    assert (s["fidelity"]["eligible"], s["fidelity"]["ok"]) == (4, 3)


def test_b1_gap_fill_is_not_the_primary_code_of_ordinary_stop_outs(tmp_path):
    _w, _s, rows = _b1_world(tmp_path)
    res = {k: AT.attribute(r) for k, r in rows.items()}
    assert "GAP_FILL" not in res["A"]["codes_loss"] and res["A"]["primary_code"] == "WRONG_DIRECTION"
    assert "GAP_FILL" not in res["D"]["codes_loss"]
    for k in ("B", "C"):
        assert "GAP_FILL" in res[k]["codes_loss"], (k, res[k]["codes_loss"])
        assert res[k]["primary_code"] == "WRONG_DIRECTION", (k, res[k]["primary_code"])  # boşluk karar kodunun ardında
    assert res["C"]["evidence"]["GAP_FILL"]["mechanism"] == "SAMPLED"
    assert AT.LOSS_PRIORITY.index("GAP_FILL") > AT.LOSS_PRIORITY.index("LATE_ENTRY")


# ============================================================================ M1: R ayrıştırması harfiyen (§5.6)
def _grid_cells(gs: dict[str, float]):
    from tradingbot.research_engine import cfgrid as CG
    axis = {v[0]: v[1] for v in CG.VARIANTS}
    return {k: CG.Cell(k, axis.get(k, "size"), CG.EX_ANTE if k != "Z_ACTUAL_LEV" else CG.REFERENCE, status="OK", r_net=v, g=v)
            for k, v in gs.items()}


def test_m1_decomposition_is_actual_minus_the_median_of_that_axis_grid():
    """timing = gerçek − giriş ekseninin ızgara medyanı (stop/çıkış sabit), stop = gerçek − stop ekseninin medyanı, çıkış =
    gerçek − çıkış ekseninin medyanı (eksen hücreleri gerçek hücreyi de içerir); sinyal = nötr yürütme (bütün EX_ANTE
    hücrelerinin medyanı) − rastgele kontrol medyanı; boyut = gerçek kaldıraç − 1x; maliyet ölçülen; artık her zaman
    gösterilir ve parçaları (rastgele taban + yeniden oynatma farkı + eksenlerin etkileşimi) artığa eşittir."""
    import statistics

    from tradingbot.research_engine import cfgrid as CG
    g = {vid: 0.1 for vid in CG.VARIANT_IDS}
    g.update({"E_ACTUAL": -1.0, "E_DELAY1": 0.4, "E_LIMIT_025": 0.6, "S_ATR075": -1.0, "S_ATR100": -0.8, "S_ATR150": 1.0,
              "S_ATR200": 0.75, "X_T1R": -1.0, "X_T2R": 0.3, "X_T3R": 0.2, "X_TRAIL2ATR": -0.5, "X_NONE_TIME": 0.9,
              "Z_HALF_LEV": -1.0, "Z_ACTUAL_LEV": -1.02, "SKIP": 0.0})
    plan = CG.TradePlan(key="b|T|x", book="strategy_paper_box", symbol="SOL/USDT", side="LONG", opened_ms=0, closed_ms=60_000,
                        ref=100.0, pre_filled=False, stop=99.0, targets=[101.5], level_exit=True, horizon_ms=120_000,
                        entry_tf="5m", atr=1.0, leverage=3, notional=None, net_r=-1.07, gross_pre_r=-1.03)
    d = CG.decompose(plan, _grid_cells(g), {"status": "OK", "median_g": -0.2, "median_r_net": -0.25})
    c = d["components"]
    med = statistics.median
    x1 = med([g[v[0]] for v in CG.VARIANTS if v[1] in ("entry", "stop", "exit", "manage")])   # nötr yürütme: 1x, atla yok
    assert c["signal_R"] == round(x1 - (-0.2), 6)
    assert c["timing_R"] == round(-1.0 - med([-1.0, 0.4, 0.6]), 6) == -1.4
    assert c["stop_R"] == round(-1.0 - med([-1.0, -1.0, -0.8, 1.0, 0.75]), 6) == -0.2
    assert c["exit_R"] == round(-1.0 - med([-1.0, -1.0, 0.3, 0.2, -0.5, 0.9]), 6) == round(-1.0 - (-0.15), 6)
    assert c["size_lev_R"] == round(-1.02 - (-1.0), 6) and c["cost_R"] == round(-1.07 - (-1.03), 6)
    total = sum(v for v in c.values() if v is not None)
    assert abs(d["residual_R"] + total - (-1.07)) < 1e-9 and d["identity_ok"]
    rp = d["residual_parts"]
    assert abs(rp["random_baseline_R"] + rp["replay_gap_R"] + rp["interaction_R"] - d["residual_R"]) < 1e-6


# ============================================================================ M2: LATE_ENTRY MAE kolu + yol penceresi
def test_m2_late_entry_mae_arm_is_mae_before_the_mfe_not_a_never_favourable_trade():
    from test_research_engine_attribution import bars_from, golden
    # hiç lehte gitmeyen stop (MFE ≈ 0): WRONG_DIRECTION'dır, LATE_ENTRY DEĞİL
    down = bars_from([(100, 100.0, 99.6, 99.7), (99.7, 99.7, 99.2, 99.25), (99.25, 99.3, 99.05, 99.1)])
    r = AT.attribute(golden(down, exit_px=99.0, exit_reason="stop"), AT.AttrInputs(bars=down))
    assert "WRONG_DIRECTION" in r["codes_loss"] and "LATE_ENTRY" not in r["codes_loss"], r["codes_loss"]
    # harfiyen durum: önce −0,8R, sonra +0,5R (MFE), sonra stop (−1R): MFE'den ÖNCE MAE −0,8 → LATE_ENTRY
    vee = bars_from([(100, 100.05, 99.2, 99.3), (99.3, 100.5, 99.25, 100.4), (100.4, 100.45, 99.3, 99.4),
                     (99.4, 99.45, 99.05, 99.1)])
    row = golden(vee, exit_px=99.0, exit_reason="stop")
    assert row["order"] == "MFE_FIRST"                                  # eski kol (MAE_FIRST) bunu kaçırıyordu
    r = AT.attribute(row, AT.AttrInputs(bars=vee))
    assert r["evidence"]["LATE_ENTRY"]["mae_r_before_mfe"] == -0.8 and "WRONG_DIRECTION" not in r["codes_loss"]
    # MFE < 0,3R iken (WRONG_DIRECTION bölgesi) MAE kolu çalışmaz
    small = bars_from([(100, 100.05, 99.2, 99.3), (99.3, 100.2, 99.25, 100.1), (100.1, 100.15, 99.05, 99.1)])
    r = AT.attribute(golden(small, exit_px=99.0, exit_reason="stop"), AT.AttrInputs(bars=small))
    assert "LATE_ENTRY" not in r["codes_loss"] and "WRONG_DIRECTION" in r["codes_loss"]


def test_m2_path_metrics_exclude_the_bar_that_contains_the_exit():
    """Çıkışı içeren bar çıkış SONRASI fiyatları taşır: MFE/MAE'ye girmez; çıkış dolumu son gözlemdir."""
    from tradingbot.research_engine import pathrec as PR
    bars = pd.DataFrame([[60_000, 100.0, 100.4, 99.8, 100.2, 0], [120_000, 100.2, 100.5, 100.0, 100.3, 0],
                         [180_000, 100.3, 103.0, 99.0, 99.5, 0]], columns=["timestamp", "open", "high", "low", "close", "phase"])
    closed = 180_000 + 20_000                       # çıkış üçüncü barın 20. saniyesinde, 100,25'ten
    m = PR.path_metrics(bars, side="LONG", entry=D("100"), rpu=D("1"), opened_ms=30_000, exit_price=D("100.25"),
                        step_ms=M1, closed_ms=closed)
    assert (m["mfe_r"], m["mae_r"]) == (0.5, -0.2), m           # 103 / 99 çıkıştan SONRA olabilir: ölçüye girmez
    assert m["capture_ratio"] == 0.5 and m["n_obs"] == 3 and m["t_mfe"] == 2.5
    # eski pencere (closed_ms yok) hâlâ aynı sonucu verir (geriye uyum)
    old = PR.path_metrics(bars, side="LONG", entry=D("100"), rpu=D("1"), opened_ms=30_000, exit_price=D("100.25"), step_ms=M1)
    assert old["mfe_r"] == 3.0


# ============================================================================ M3: sıralama aynı ölçüyle, seçim etiketli
def _arow(i: int, day: int, cells: dict[str, tuple[float | None, float | None]], *, rank_src: str = "cf_aux_v1",
          book: str = "strategy_paper_box") -> dict:
    from tradingbot.research_engine import cfgrid as CG
    axis = {v[0]: (v[1], v[2]) for v in CG.VARIANTS}
    out = []
    for cid, (rn, rc) in cells.items():
        ax, lab = axis.get(cid, ("size", CG.REFERENCE))
        out.append({"id": cid, "axis": ax, "label": lab, "status": "OK", "r_net": rn, "r_cons": rc, "g": rn})
    return {"trade_key": f"{book}|T{i}|x", "book": book, "closed_at": f"2026-09-{day:02d}T10:00:00+00:00",
            "grid": {"status": "OK", "rank_src": rank_src, "cells": out}}


def _cells(**over):
    base = {"E_ACTUAL": (-0.25, -0.3), "S_ATR150": (0.25, 0.2), "X_T1R": (-0.45, -0.5), "Z_HALF_LEV": (0.9, None),
            "SKIP": (0.0, 0.0)}
    base.update(over)
    return base


def test_m3_book_view_needs_n30_and_10_days_and_never_offers_leverage():
    from tradingbot.research_engine import analysis as AN
    rows = [_arow(i, 1 + i % 12, _cells()) for i in range(40)]
    b = AN.best_ex_ante(rows)
    assert b["status"] == "SELECTED" and b["cell"] == "S_ATR150", b      # Z_HALF_LEV (r_net +0,9; eski kodda en iyi) asla
    assert b["k"] == 3 and abs(b["stats"]["mean_delta_r"] - 0.5) < 1e-9   # S_ATR150, X_T1R, SKIP karşılaştırıldı
    st = AN.variant_stats(rows)
    assert "Z_HALF_LEV" not in st and "Z_ACTUAL_LEV" not in st
    # n < 30 ya da < 10 gün: seçim yok
    assert AN.best_ex_ante(rows[:25])["status"] == "INSUFFICIENT"
    assert AN.best_ex_ante([_arow(i, 1 + i % 5, _cells()) for i in range(40)])["status"] == "INSUFFICIENT"
    # hiçbir hücre iyileştirmiyorsa "en iyi" yazılmaz
    worse = [_arow(i, 1 + i % 12, _cells(E_ACTUAL=(0.5, 0.5))) for i in range(40)]
    assert AN.best_ex_ante(worse)["status"] == "NO_IMPROVING"


def test_m3_like_with_like_trades_without_conservative_r_are_kept_out_of_the_book_view():
    from tradingbot.research_engine import analysis as AN
    rows = [_arow(i, 1 + i % 12, _cells()) for i in range(30)]
    rows += [_arow(100 + i, 1 + i % 12, _cells(S_ATR150=(5.0, None)), rank_src="r_net") for i in range(30)]
    acc = AN.VariantAcc()
    for r in rows:
        acc.add(r)
    assert acc.used == 30 and acc.excluded == 30
    assert abs(acc.stats()["S_ATR150"]["mean_delta_r"] - 0.5) < 1e-9     # r_net'li +5'ler karışmadı


def test_m3_trade_level_ranking_uses_one_measure_and_excludes_the_size_axis():
    from tradingbot.research_engine import cfgrid as CG
    cells = {k: CG.Cell(k, a, lab, status="OK", r_net=rn, r_cons=rc) for k, a, lab, rn, rc in (
        ("E_ACTUAL", "entry", CG.EX_ANTE, -1.0, -1.1), ("S_ATR150", "stop", CG.EX_ANTE, 0.4, 0.35),
        ("X_T1R", "exit", CG.EX_ANTE, -0.2, None), ("Z_HALF_LEV", "size", CG.EX_ANTE, 0.9, None),
        ("SKIP", "skip", CG.EX_ANTE, 0.0, 0.0))}
    assert CG.rank_source(cells) == "r_net"                               # X_T1R'de muhafazakâr R yok → tümü r_net
    hb = CG.hindsight(cells, cells["E_ACTUAL"], CG.rank_source(cells))
    assert hb["cell"] == "S_ATR150" and hb["rank_src"] == "r_net" and hb["r_rank"] == 0.4   # Z_HALF_LEV (0,9) değil
    cells["X_T1R"].r_cons = -0.25
    src = CG.rank_source(cells)
    assert src == "cf_aux_v1"                                             # boyut ekseni ölçü seçimine girmez
    hb = CG.hindsight(cells, cells["E_ACTUAL"], src)
    assert hb["cell"] == "S_ATR150" and hb["r_rank"] == 0.35 and hb["delta_vs_actual"] == 1.45
    assert CG.profitable_cells(cells, src) == ["S_ATR150"]


def test_m3_digest_text_labels_the_selection_and_never_mentions_leverage():
    from tradingbot.research_engine import analysis as AN
    from tradingbot.research_engine import report as RP
    rows = [_arow(i, 1 + i % 12, _cells()) for i in range(40)]
    t = RP.ex_ante_line(rows)
    assert "SEÇİM" in t and "3 sabit hücre" in t and "S_ATR150" in t and "ders değil" in t, t
    assert "kaldıraç" not in t and "Z_HALF_LEV" not in t
    assert "iyileştirmiyor" in RP.ex_ante_line([_arow(i, 1 + i % 12, _cells(E_ACTUAL=(0.5, 0.5))) for i in range(40)])
    assert RP.ex_ante_line(rows[:10]) is None
    assert AN.best_ex_ante(rows)["k"] == 3


# ============================================================================ M4: uzlaştırma S1a okumasına göre
def test_m4_reconcile_uses_the_s1a_read_trades_closed_or_revised_later_are_not_inconsistent(tmp_path):
    import json

    from research_engine_p2_fixtures import standard_world
    w = standard_world(tmp_path / "w")
    J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    assert J.reconcile_journal(w.paths)["status"] == "OK"
    # S1a (2026-10-06 01:40) SONRASI: ana bot yeni bir işlem kapatır ve eski bir kayda geç fonlama yazılır (ledger dosyası)
    led = w.v.futs[""]
    m1 = w.m1("SOL/USDT")
    t = ms(utc(2026, 10, 5, 23, 0, 7))
    e = price_at(m1, t)
    led.open("SOL/USDT", "LONG", D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=round(e * 0.99, 2),
             targets=[round(e * 1.02, 2)], now=dt_of(t))
    led.close_manual("SOL/USDT", D(str(e)), reason="manuel", now=utc(2026, 10, 6, 1, 55))
    w.v.save(utc(2026, 10, 6, 1, 56))
    p = w.v.fut_path("")
    doc = json.loads(p.read_text(encoding="utf-8"))
    h0 = doc["history"][0]
    h0["pnl"] = str(D(h0["pnl"]) - D("0.25"))
    h0["funding"] = str(D(h0.get("funding") or "0") - D("0.25"))
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    rec = J.reconcile_journal(w.paths)
    led_m = rec["books"]["main_fut"]["ledger"]
    assert rec["status"] == "OK", rec["books"]["main_fut"]
    assert led_m["after_s1a"] == 1 and led_m["revised_after_s1a"] == 1 and led_m["as_of"]
    # S1a okumasından ÖNCE kapanmış ama arşivde olmayan kayıt hâlâ tutarsızlıktır
    ghost = dict(doc["history"][1], id="F09999", closed_at="2026-10-01T10:00:00+00:00")
    doc["history"].append(ghost)
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    rec = J.reconcile_journal(w.paths)
    assert rec["status"] == "INCONSISTENT" and rec["books"]["main_fut"]["ledger"]["n_missing"] == 1


# ============================================================================ M5: artımlı kurulum yalnız pencere içeriği değişince
def _append_day(w, *, start, hours: int = 12) -> None:
    """Dünya piyasasının ardına `hours` saatlik yeni barlar (bütün semboller/dilimler) + yeni mühür (günlük ekleme)."""
    from research_engine_p2_fixtures import STEP, StoreFixture, walk
    a = ms(start)
    sf = StoreFixture(w.paths, now=start + timedelta(hours=hours, minutes=30))
    for sym, fr in w.mk.items():
        last = float(fr["1m"]["close"].iloc[-1])
        m1 = walk(f"{sym}:append", a, hours * 60, M1, last, vol=0.001)
        sf.add("futures", sym, "1m", m1)
        for tf in ("5m", "1h", "4h"):
            sf.add("futures", sym, tf, aggregate(m1, STEP[tf]))
    sf.seal()


def test_m5_a_daily_append_after_every_window_rebuilds_nothing_but_a_change_inside_a_window_does(tmp_path):
    from research_engine_p2_fixtures import standard_world

    from tradingbot.research_engine import analysis as A
    w = standard_world(tmp_path / "w")
    J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    A.run_s2(w.paths, now=NOW)
    closed = sorted(str(r["closed_at"]) for r in J.iter_journal(w.paths))
    assert closed[-1] < "2026-10-05T18:00"                    # bütün pencereler (kapanış + 6 sa) 10-06 00:00'dan önce biter
    _append_day(w, start=utc(2026, 10, 6))
    s1 = J.build_journal(w.paths, now=NOW + timedelta(days=1), src=w.source(), rawconfig=w.rc)
    s2 = A.run_s2(w.paths, now=NOW + timedelta(days=1))
    assert s1["built_rows"] == 0 and s2["built_rows"] == 0, (s1["built_rows"], s2["built_rows"])
    # bir işlemin penceresinin İÇİNDEKİ bir bar değişirse yalnız o sembolün o penceredeki satırları yeniden kurulur
    sol = w.mk["SOLUSDT"]["1m"]
    box = [r for r in J.iter_journal(w.paths) if r["book"] == "strategy_paper_box"]
    t0 = ms(dt_of(0)) + int(pd.Timestamp(box[0]["opened_at"]).timestamp() * 1000) // M1 * M1 + 5 * M1
    from research_engine_p2_fixtures import StoreFixture
    sf = StoreFixture(w.paths, now=utc(2026, 10, 6, 13))
    bar = sol[sol["timestamp"] == t0].copy()
    bar["high"] = bar["high"] + 0.01
    sf.add("futures", "SOLUSDT", "1m", bar.reset_index(drop=True))
    sf.seal()
    s1 = J.build_journal(w.paths, now=NOW + timedelta(days=1), src=w.source(), rawconfig=w.rc)
    same_month_sol = [r for r in J.iter_journal(w.paths) if r["symbol"] == "SOL/USDT"]
    assert 1 <= s1["built_rows"] < len(same_month_sol), (s1["built_rows"], len(same_month_sol))


# ============================================================================ M6 ve küçükler
def test_m6_p2_night_unit_memory_and_part_caches():
    from tradingbot.research_engine import analysis as A
    unit = (Path(__file__).resolve().parents[1] / "deploy" / "tradingbot-engine-night.service").read_text(encoding="utf-8")
    assert "\nMemoryHigh=640M\n" in unit and "\nMemoryMax=768M\n" in unit
    assert "\nEnvironment=ENGINE_EXPECTED_MEMORY_MAX=805306368\n" in unit and 768 << 20 == 805306368
    assert J.S1B_MAX_PARTS == A.S2_MAX_PARTS == 16


def test_minor_s2_builds_newest_first_within_a_month_under_backlog(tmp_path):
    from research_engine_p2_fixtures import standard_world

    from tradingbot.research_engine import analysis as A
    w = standard_world(tmp_path / "w")
    J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    r = A.run_s2(w.paths, now=NOW, max_rows=5)
    allc = sorted(str(x["closed_at"]) for x in J.iter_journal(w.paths))
    got = sorted(str(x["closed_at"]) for x in A.iter_attribution(w.paths))
    assert r["pending"] == len(allc) - 5 and got == allc[-5:], (got, allc[-8:])   # dünün işlemleri bekletilmez


def test_minor_legacy_money_fields_are_missing_not_fabricated_zeros(tmp_path):
    import json
    w = World(tmp_path)
    m1 = w.m1("SOL/USDT")
    led = new_ledger()
    w.v.futs[""] = led
    t = ms(utc(2026, 9, 29, 3, 11, 5))
    e = price_at(m1, t)
    led.open("SOL/USDT", "LONG", D(str(e)), SizeSpec(D("300"), AmountType.NOTIONAL, 3), stop=round(e * 0.99, 2),
             targets=[round(e * 1.02, 2)], now=dt_of(t))
    led.close_manual("SOL/USDT", D(str(e)), reason="manuel", now=dt_of(t + 2 * H1))
    w.v.save(utc(2026, 10, 6, 1, 39))
    p = w.v.fut_path("")
    doc = json.loads(p.read_text(encoding="utf-8"))
    for k in ("gross_pnl", "entry_fee", "exit_fee", "slippage_cost", "funding_paid", "funding_received"):
        doc["history"][0].pop(k, None)                     # eski (legacy) kayıt: bu alanlar hiç yazılmamış
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    w.seal(frames={"SOLUSDT": w.mk["SOLUSDT"], "BTCUSDT": w.mk["BTCUSDT"]}, spot=())
    w.v.night(night_of("2026-10-06"), save=False)
    s = J.build_journal(w.paths, now=NOW, src=w.source(), rawconfig=w.rc)
    assert s["fee_identity_violations"] == 0                    # bilinmeyen özdeşlik ihlal sayılmaz
    row = next(iter(J.iter_journal(w.paths)))
    fs = row["field_source"]
    for k in ("gross_pnl", "entry_fee", "exit_fee", "slippage_cost", "funding_paid", "funding_received"):
        assert row[k] is None and fs[k] == "MISSING", (k, row[k], fs[k])
    assert fs["net_pnl"] == "MEASURED" and row["net_pnl"] is not None
    assert row["gross_r"] is None and fs["gross_r"] == "MISSING" and row["fee_identity_ok"] is None
    assert J.owner_fields_ok(row)


def test_minor_journal_is_backed_up_and_interrupted_temp_files_are_cleaned(tmp_path):
    from tradingbot.research_engine import backup as B
    from tradingbot.research_engine.paths import EnginePaths
    assert "journal" in B.INCLUDE and "journal" not in B.EXCLUDE and "attribution" in B.EXCLUDE
    paths = EnginePaths.for_state(tmp_path / "data" / "state")
    root = paths.journal_tj
    root.mkdir(parents=True)
    for n in (".spill-2026-10-0000.jsonl.gz", ".2026-10.jsonl.gz.merge", "..2026-10.jsonl.gz.merge.ab12cd.tmp",
              "._index.json.gz.x1y2.tmp", "2026-10.jsonl.gz"):
        (root / n).write_bytes(b"x")
    J.clean_spills(paths, root)
    assert sorted(p.name for p in root.iterdir()) == ["2026-10.jsonl.gz"]
