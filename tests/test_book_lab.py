# -*- coding: utf-8 -*-
"""Defter araştırması book_v1 (docs/BOOK_RESEARCH_V1.md; 2026-10-05): ön kayıt mührü ve kaydın belgeyi kapsaması (okunuşlar
belgede), sabit pencereler ve dönem sınırları, eşdeğerlik (D4 = donchian_trend + algo_events; C4 = candle_lab.variation_events
+ candle_book.decide tekilleştirmesi; Formasyon = catalog_events adım 1 + RSI = build_plans_v3; rejim = regime_gate.btc_regime;
temiz pencere = candle_lab.valid_ends), GELECEĞE BAKMAMA (her varyantın sinyali ve işlemi, kesilmiş seri ile geleceği
değiştirilmiş tam seride aynı), simülatörün signal_lab.simulate / simulate_rule paritesi ve DATA_END/DELISTED örnekleri,
maliyet, plasebo (belirlenimci, anahtar, dönem oranı, q havuzu yalnız önceki isabetlerden), C4 tekilleştirme örnekleri,
fonlama (funding_carry + NaN doldurma), aylık ölçü (işlemsiz ay, çıkış ayı, aralık), kapasite (elle hesaplanmış BUMP/NO_BUMP,
slot, aynı sembol, çıkışla boşalan slot, sıra), hedef şartları, Holm, PIT (havuz, sıralama, liste), uçtan uca kuru koşu
(sahte arşiv, ağ YOK) ve komut satırı. Yalnız sentetik veri."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import io
import json
import math
import urllib.request
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import book_lab as B
from tradingbot import signal_lab as L
from tradingbot.timeframes import tf_ms

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000
HOUR = 3_600_000
H4 = 4 * HOUR

#: ön kayıt mührü — bir varyant tanımı, süzgeç, plasebo, istatistik/kapasite/aylık ayarı, veri penceresi, evren, PIT kuralı
#: ya da okunuş değişirse bu test KIRILIR (book_v2 + belge + yeni deneme sayısı; sonuç görüldükten sonra gevşetme YOK)
PINNED_SHA = "81bc20c17ea05d92"


def ms(s: str) -> int:
    return int(pd.Timestamp(s, tz="UTC").timestamp() * 1000)


# ---------------------------------------------------------------------------- sentetik veri
def synth(n, tf, t0, seed=1, vol=0.01, drift=0.0, sub=8, spike=0.08, legs=0, leg_drift=0.006):
    """Mum içi yollu rastgele yürüyüş; `legs` > 0 ise sürüklenme her `legs` barda yön değiştirir (trend bacakları)."""
    rnd = np.random.default_rng(seed)
    step = tf_ms(tf)
    dr = np.full(n, drift, dtype=float)
    if legs:
        dr = dr + leg_drift * np.where((np.arange(n) // legs) % 2 == 0, 1.0, -1.0)
    steps = rnd.normal(0, vol / np.sqrt(sub), (n, sub)) + (dr / sub)[:, None]
    path = 100.0 * np.exp(np.cumsum(steps.ravel())).reshape(n, sub)
    o = np.r_[100.0, path[:-1, -1]]
    ts = t0 + np.arange(n, dtype=np.int64) * step
    v = rnd.uniform(50, 150, n)
    v[rnd.random(n) < spike] *= 3.0
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, path.max(1)), "low": np.minimum(o, path.min(1)),
                         "close": path[:, -1], "volume": v, "close_time": ts + step - 1})


def wave_daily(n, t0, period=70, seed=9):
    """BTC 1d: EMA200'ü sık kesen dalga (iki rejim de görülsün)."""
    rnd = np.random.default_rng(seed)
    k = np.arange(n)
    c = 100.0 * np.exp(0.25 * np.sin(2 * np.pi * k / period) + 0.01 * rnd.normal(size=n).cumsum() * 0.1)
    o = np.r_[c[0], c[:-1]]
    ts = t0 + k.astype(np.int64) * DAY
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c,
                         "volume": rnd.uniform(50, 150, n), "close_time": ts + DAY - 1})


def flat_series(n, tf="4h", t0=None, px=100.0, rng=1.0):
    t0 = B.DECISION_START_MS if t0 is None else t0
    step = tf_ms(tf)
    ts = t0 + np.arange(n, dtype=np.int64) * step
    return pd.DataFrame({"timestamp": ts, "open": px, "high": px + rng, "low": px - rng, "close": px, "volume": 100.0,
                         "close_time": ts + step - 1})


# ---------------------------------------------------------------------------- küçük dünya (zaman sabitleri yamalı)
T0 = B.START_4H_MS                         # 2022-01-01
MINI = {"START_4H_MS": T0, "START_1D_MS": T0 - 400 * DAY, "DECISION_START_MS": ms("2022-04-01"), "IS_END_MS": ms("2022-05-01"),
        "PRICE_END_MS": ms("2022-07-01"), "FUNDING_START_MS": ms("2022-03-01"), "FUNDING_END_MS": ms("2022-07-02"),
        "IS_MONTHS": ["2022-04"], "OOS_MONTHS": ["2022-05", "2022-06"], "PIT_MONTHS": ["2022-04", "2022-05", "2022-06"]}
N4 = (MINI["PRICE_END_MS"] - T0) // H4     # 1086 bar
N1 = (MINI["PRICE_END_MS"] - MINI["START_1D_MS"]) // DAY


def patch_mini(mp) -> None:
    for k, v in MINI.items():
        mp.setattr(B, k, v)


@pytest.fixture
def mini(monkeypatch):
    patch_mini(monkeypatch)
    return MINI


def world_frames(seed=11):
    df4 = synth(N4, "4h", T0, seed=seed, vol=0.012, legs=45, leg_drift=0.004)
    df1 = synth(N1, "1d", MINI["START_1D_MS"], seed=seed + 1, vol=0.03, legs=40, leg_drift=0.01)
    btc1 = wave_daily(N1, MINI["START_1D_MS"])
    return df4, df1, btc1


def tcut(df, t):
    return df[df["timestamp"] + (df["timestamp"].diff().median() if len(df) > 1 else 0) <= t].reset_index(drop=True)


@pytest.fixture(scope="module")
def world():
    """Küçük dünyada bir sembolün bütün defter işlemleri: tam seri (geleceği DEĞİŞTİRİLMİŞ) ve kesim anında kesilmiş seri."""
    with pytest.MonkeyPatch.context() as mp:
        patch_mini(mp)
        df4, df1, btc1 = world_frames()
        cut = 930
        t_cut = int(df4["timestamp"].iloc[cut])
        fut4 = df4.copy()
        fut4.loc[cut:, ["open", "high", "low", "close"]] *= 1.7                    # gelecek tamamen farklı
        fut1, futb = df1.copy(), btc1.copy()
        m1 = fut1["timestamp"] + DAY > t_cut
        fut1.loc[m1, ["open", "high", "low", "close"]] *= 0.6
        futb.loc[futb["timestamp"] + DAY > t_cut, ["open", "high", "low", "close"]] *= 1.9
        full, fmeta = B.process_symbol("SYN/USDT", fut4, fut1, B.regime_daily(futb), None, B.BOOKS)
        trunc, tmeta = B.process_symbol("SYN/USDT", df4.iloc[:cut].reset_index(drop=True), tcut(df1, t_cut),
                                        B.regime_daily(tcut(btc1, t_cut)), None, B.BOOKS)
        base, bmeta = B.process_symbol("SYN/USDT", df4, df1, B.regime_daily(btc1), None, B.BOOKS)
    return {"full": full, "trunc": trunc, "base": base, "t_cut": t_cut, "fmeta": fmeta, "bmeta": bmeta, "df4": df4, "df1": df1,
            "btc1": btc1}


# ---------------------------------------------------------------------------- mühür ve kayıt
def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def test_registry_sha_is_pinned_documented_and_sensitive_to_every_part():
    assert B.BOOK_REGISTRY_SHA == _sha(B.BOOK_REGISTRY) == PINNED_SHA, "kayıt değişti: book_v2 + belge + yeni deneme sayısı"
    doc = (ROOT / "docs" / "BOOK_RESEARCH_V1.md").read_text(encoding="utf-8")
    assert f"Ön kayıt mührü: `BOOK_REGISTRY_SHA = {PINNED_SHA}`" in doc and B.VERSION == "book_v1"
    assert "henüz yok" not in doc.split("## 0.")[0]
    R = B.BOOK_REGISTRY
    for path, val in ((("variants", "D4_05_STOP3ATR", "stop_atr"), 2.5), (("variants", "D4_00_BASE", "risk_atr"), [0.1, 5.0]),
                      (("variants", "C4_02_EXIT_1R", "dsl_change", "exit", "target_r"), 1.5), (("variants", "FM_00_BASE", "chase_atr"), 2.0),
                      (("variants", "FM_08_EXIT_3R", "exit", "max_bars"), 48), (("filters", "COST_STOP"), "x"),
                      (("stats", "min_oos"), 19), (("monthly", "seed"), 1), (("capacity", "books", "FM", "slots"), 3),
                      (("data", "price_end_ms"), 1), (("universes", "primary"), []), (("readings_tr",), []),
                      (("universes", "pit", "top"), 50), (("multiple", "iters"), 1000), (("target", "censor_max"), 0.2),
                      (("c4", "placebo_p"), 0.2), (("placebo", "min_n"), 10)):
        tweak = json.loads(json.dumps(R))
        node = tweak
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = val
        assert _sha(tweak) != PINNED_SHA, path


def test_registry_covers_the_document_counts_and_readings():
    R = B.BOOK_REGISTRY
    doc = (ROOT / "docs" / "BOOK_RESEARCH_V1.md").read_text(encoding="utf-8")
    hyp = [v for v, d in B.VARIANTS.items() if not d["control"]]
    assert len(hyp) == 32 and [v for v, d in B.VARIANTS.items() if d["control"]] == ["FM_CTRL_NO_RSI"]
    assert all(f"`{v}`" in doc for v in B.VARIANTS)
    assert "FM_06" not in "".join(B.VARIANTS)                                       # çıkarıldı (§6.3)
    by_book = {b: sum(1 for c in B.PRIMARY_CELLS if B.VARIANTS[c.split("|")[0]]["book"] == b) for b in B.BOOKS}
    assert by_book == {"D4": 15, "C4": 27, "FM": 12} and len(B.PRIMARY_CELLS) == 54 == R["trials"]["primary_cells"]
    assert {b: sum(1 for v in hyp if B.VARIANTS[v]["book"] == b) for b in B.BOOKS} == {"D4": 13, "C4": 9, "FM": 10}
    for v, d in B.VARIANTS.items():                                                 # plasebo süzgeci = süzgeç − sinyal süzgeci
        assert d["placebo_filters"] == [f for f in d["filters"] if f not in B.SIGNAL_FILTERS], v
        assert d["cells"] == (["BOTH", "LONG", "SHORT"] if len(d["sides"]) == 2 else ["LONG"]), v
    # okunuşların HEPSİ belgede (harfiyen)
    for r in B.READINGS_TR:
        assert r in doc, r[:60]
    assert R["readings_tr"] == list(B.READINGS_TR)
    for s in ("BTCDOM", "XAU", "TSLA", "USDC"):
        assert s in B.READINGS_TR[-1]


def test_c4_definition_shas_match_doc_and_registry_and_only_changed_definitions_get_new_shas():
    from tradingbot import candle_variations as CV
    doc = (ROOT / "docs" / "BOOK_RESEARCH_V1.md").read_text(encoding="utf-8")
    for cv, sha in B.CV_BASE_SHA.items():
        assert CV.get(cv).definition_sha == sha and f"`{sha}`" in doc
    shas = B.BOOK_REGISTRY["c4"]["definition_sha"]
    for vid, d in B.C4_VARIANTS.items():
        for cv in d["cvs"]:
            same = d["dsl_change"] is None
            assert (shas[vid][cv] == B.CV_BASE_SHA[cv]) is same, (vid, cv)
            var = B.c4_variation(cv, d["dsl_change"])
            assert var.id == cv and var.side == CV.get(cv).side
    v2, v3 = B.c4_variation(B.CV_ORDER[0], B.C4_VARIANTS["C4_02_EXIT_1R"]["dsl_change"]), \
        B.c4_variation(B.CV_ORDER[0], B.C4_VARIANTS["C4_03_EXIT_3R"]["dsl_change"])
    assert (v2.target_r, v2.max_hold_bars, v3.target_r, v3.max_hold_bars) == (1.0, 24, 3.0, 60)
    assert B.c4_variation(B.CV_ORDER[0], {"stop": {"atr_buffer": 1.0}}).atr_buffer == 1.0
    assert B.c4_variation(B.CV_ORDER[0], {"context": {"atr_regime": [None, 1.25]}}).context == {"trend": ["with"], "atr_regime": [None, 1.25]}
    # defter kaydı değişmedi
    assert len(CV.VARIATIONS) == 9 and CV.get(B.CV_ORDER[0]).definition_sha == B.CV_BASE_SHA[B.CV_ORDER[0]]


def test_constants_windows_universes_and_capacity_match_the_bot():
    from tradingbot import learning_mode as LM
    from tradingbot import strategy_paper as SP
    from tradingbot.pattern_trader.strategy_v3 import V3_SYMBOLS
    from tradingbot.risk.profiles import PROFILES
    assert B.START_4H_MS == ms("2022-01-01") and B.START_1D_MS == ms("2020-01-01") and B.PRICE_END_MS == ms("2026-10-01")
    assert B.DECISION_START_MS == ms("2023-01-01") and B.IS_END_MS == ms("2025-01-01")
    assert B.FUNDING_START_MS == ms("2022-12-01") and B.FUNDING_END_MS == ms("2026-10-02")
    assert len(B.PRIMARY_UNIVERSE) == 23 and set(B.PRIMARY_UNIVERSE) == set(V3_SYMBOLS) & set(B.INFO_UNIVERSE)
    assert len(B.INFO_UNIVERSE) == 40 == len(set(B.INFO_UNIVERSE))
    assert B.CAP_COMMON["hard_cap_pct"] == LM.HARD_CAP_PCT and B.CAP_COMMON["mmr"] == SP.LEARNING_MMR
    assert B.CAP_COMMON["max_position_pct"] == PROFILES["PAPER_RESEARCH"].max_position_pct
    cfg = L.LabConfig()
    assert (cfg.fee_pct, cfg.slippage_bps) == (0.05, 3.0) and round(2 * cfg.cost_per_side * 100, 4) == 0.16


def test_split_dates_and_month_lists():
    assert B.period_of(ms("2022-12-31") + 20 * HOUR) is None                       # ısınma
    assert B.period_of(ms("2023-01-01")) == "IS" and B.period_of(ms("2025-01-01") - 1) == "IS"
    assert B.period_of(ms("2025-01-01")) == "OOS" and B.period_of(ms("2026-10-01") - 1) == "OOS"
    assert B.period_of(ms("2026-10-01")) is None
    # 2024-12-31 20:00'de açılan 4h barın kararı 2025-01-01 00:00'dadır → doğrulama
    assert B.period_of(ms("2024-12-31") + 20 * HOUR + H4) == "OOS"
    assert len(B.IS_MONTHS) == 24 and B.IS_MONTHS[0] == "2023-01" and B.IS_MONTHS[-1] == "2024-12"
    assert len(B.OOS_MONTHS) == 21 and B.OOS_MONTHS[0] == "2025-01" and B.OOS_MONTHS[-1] == "2026-09"
    assert B.month_key(ms("2026-10-01") - 1) == "2026-09"


# ---------------------------------------------------------------------------- yeniden yazılan parçaların eşdeğerliği
def test_clean_ends_equals_candle_lab_valid_ends_and_regime_equals_btc_regime():
    from tradingbot import candle_lab as CL
    from tradingbot import regime_gate as RG
    df = synth(1400, "4h", T0, seed=5)
    df.loc[700, "high"] = df.loc[700, "close"] - 0.5                                # bozuk bar
    df.loc[900, "volume"] = -1.0
    df = df.drop(index=[1100, 1101]).reset_index(drop=True)                         # boşluk
    a = CL._arrays(df)
    got = B.clean_ends(a["ts"], a["o"], a["h"], a["l"], a["c"], a["v"], H4, 500)
    assert np.array_equal(got, CL.valid_ends(a, H4)) and got.any() and not got.all()
    closes = list(wave_daily(650, ms("2020-01-01"))["close"])
    closes[300] = float("nan")
    want = [RG.btc_regime([{"close": c} for c in closes[:k + 1]]) for k in range(len(closes))]
    assert B.regime_values(closes) == want and want.count("UP") and want.count("DOWN") and want[:199] == [None] * 199
    # rejim haritası: open_time + 1 gün ≤ t olan son bar
    d = wave_daily(700, ms("2021-01-01"))
    d_ts, reg = B.regime_daily(d)
    k = 350
    t = int(d_ts[k]) + DAY
    assert B.regime_at(np.array([t, t - 1]), d_ts, reg).tolist() == [reg[k], reg[k - 1]]
    assert reg[:199].tolist() == [0] * 199
    # bayat değer kullanılmaz: kullanılan bar t'den önceki son 24 saatte kapanmadıysa bilinmiyor
    assert B.regime_at(np.array([t + DAY - 1, t + DAY + 1]), d_ts[:k + 1], reg[:k + 1]).tolist() == [reg[k], 0]
    d2 = d.drop(index=[300]).reset_index(drop=True)                                   # 1d boşluk → 200 bar temiz değil
    _, reg2 = B.regime_daily(d2)
    assert reg2[300:499].tolist() == [0] * 199 and reg2[499] != 0


def test_channels_and_context_masks_are_the_lab_constructions():
    df = synth(400, "4h", T0, seed=6)
    S = B.Series(df, "4h")
    aux = L.aux_series(df)
    for name in ("hi20", "lo20", "hi10", "lo10"):
        assert np.array_equal(S.chan(name), aux[name], equal_nan=True)
        assert np.array_equal(B.channel(df["high" if name[:2] == "hi" else "low"].to_numpy(), int(name[2:]), name[:2]), aux[name],
                              equal_nan=True)
    h55 = S.chan("hi55")
    assert np.isnan(h55[54]) and h55[55] == df["high"].iloc[:55].max()
    vok, vcf = S.vol_masks()
    for i in (250, 300, 399):
        cx = L.context(S.ind, S.arr, i, B.LONG)
        assert vok[i] == (cx["volatilite"] in ("düşük", "normal")) and vcf[i] == (cx["hacim"] == "yüksek(>1.5x)")


# ---------------------------------------------------------------------------- taban varyantların canlı kuralla eşdeğerliği
def test_d4_base_equals_the_live_book_rule_and_the_lab_algorithm():
    from tradingbot import donchian_trend as DT
    df = synth(700, "4h", ms("2024-01-01"), seed=7, legs=40, leg_drift=0.005)
    S = B.Series(df, "4h")
    masks = B._masks(S, None, None)
    raw, elig, unclean = B.d4_raw(S, B.D4_VARIANTS["D4_00_BASE"], masks, B.LONG)
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    live = {}
    for i in range(B.D4_MIN_I, len(df)):
        act = DT.decide(rows=rows[:i + 1])
        if act and act["action"] == "OPEN":
            live[i] = act["stop"]
    assert len(raw) >= 5 and set(raw) == set(live) and unclean == 0
    a = S.ind["atr"]
    for i in raw:
        assert math.isclose(S.c[i] - 2.0 * a[i], live[i], rel_tol=1e-12)
    lab = {e.i: e for e in L.algo_events(df, "X/USDT", "4h", a, S.aux) if e.name == "TREND_DONCHIAN_20_10" and e.side == B.LONG}
    assert set(lab) == set(raw)
    for i, e in lab.items():
        assert e.stop == S.c[i] - 2.0 * a[i] and e.exit == {"kind": "channel", "max_bars": 300}
    assert B.D4_VARIANTS["D4_00_BASE"]["exit"] == {"kind": "channel", "ref": {"LONG": "lo10", "SHORT": "hi10"}, "max_bars": 300}
    assert (DT.ENTRY_N, DT.EXIT_N, DT.STOP_ATR, DT.MAX_BARS, DT.MIN_RISK_ATR, DT.MAX_RISK_ATR) == (20, 10, 2.0, 300, 0.1, 10.0)
    # SHORT (D4_10) = laboratuvarın SHORT kolu
    sraw, _, _ = B.d4_raw(S, {**B.D4_VARIANTS["D4_10_1D_REGIME_BOTH"], "tf": "4h", "filters": []}, masks, B.SHORT)
    lab_s = {e.i for e in L.algo_events(df, "X/USDT", "4h", a, S.aux) if e.name == "TREND_DONCHIAN_20_10" and e.side == B.SHORT}
    assert set(sraw) == lab_s and len(lab_s) >= 3


def test_c4_base_equals_variation_events_and_the_live_book_dedup():
    from tradingbot import candle_book as CB
    from tradingbot import candle_lab as CL
    df = synth(900, "4h", ms("2024-01-01"), seed=21, vol=0.015, legs=30, leg_drift=0.005, spike=0.15)
    S = B.Series(df, "4h")
    det = B.c4_detect(S, "X/USDT", ["base"])
    real = {}
    for cv in B.CV_ORDER:
        evs, atr_sig, _ = det["base"][cv]
        want, _ = CL.variation_events(df, "X/USDT", "4h", B.c4_variation(cv, None))
        assert [(e.name, e.side, e.i, e.stop, e.family) for e in evs] == [(e.name, e.side, e.i, e.stop, e.family) for e in want]
        real[cv] = {e.i: e for e in evs if e.family == CL.FAMILY}
    win, sup = B.c4_dedup(real, B.CV_ORDER, "BOTH")
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    params = CB.CandleParams(variations=tuple(B.CV_ORDER))
    live = {}
    for i in range(499, len(df)):
        act = CB.decide("c4_candle_variations", rows=rows[i - 499:i + 1], params=params)
        if act and act["action"] == "OPEN":
            live[i] = (act["lab_algo"], act["stop"], tuple(act["also_matched"]))
    assert len(win) >= 5 and {i: cv for cv, i in win} == {i: v[0] for i, v in live.items()}
    for cv, i in win:
        assert live[i][1] == real[cv][i].stop
    assert sup == sum(len(v[2]) for v in live.values())
    longs, _ = B.c4_dedup(real, B.CV_ORDER, "LONG")
    assert all(B.c4_variation(cv, None).side == "LONG" for cv, _ in longs)


def test_c4_exit_only_changes_share_the_base_detection():
    """C4_02 / C4_03 tabanın tespitini paylaşır: detect_last ve placebo_context çıkışı OKUMAZ."""
    from tradingbot import candle_lab as CL
    df = synth(700, "4h", ms("2024-01-01"), seed=22, vol=0.015, legs=30, leg_drift=0.005, spike=0.15)
    for vid in ("C4_02_EXIT_1R", "C4_03_EXIT_3R"):
        assert B.C4_VARIANTS[vid]["detection"] == "base" and set(B.C4_VARIANTS[vid]["dsl_change"]) == {"exit"}
        for cv in B.CV_ORDER[:4]:
            a, _ = CL.variation_events(df, "X/USDT", "4h", B.c4_variation(cv, None))
            b, _ = CL.variation_events(df, "X/USDT", "4h", B.c4_variation(cv, B.C4_VARIANTS[vid]["dsl_change"]))
            assert [(e.name, e.i, e.stop, e.family) for e in a] == [(e.name, e.i, e.stop, e.family) for e in b]


def test_formasyon_base_equals_catalog_step1_plus_rsi_and_the_live_v3_plans():
    from tradingbot.pattern_trader.strategy_v3 import build_plans_v3
    from tradingbot.structures.analysis import analyze
    df = synth(560, "4h", ms("2024-01-01"), seed=31, vol=0.01, legs=25, leg_drift=0.012)
    S = B.Series(df, "4h")
    recs = B.fm_records(S, "PAR/USDT")
    hits, _, unclean = B.fm_raw(S, recs, B.FM_VARIANTS["FM_00_BASE"], B._masks(S, None, None), B.LONG)
    cfg1 = dataclasses.replace(L.LabConfig(), stride=1)
    lab = [e for e in L.catalog_events(df, "PAR/USDT", "4h", cfg1) if e.name == "THREE_WHITE_SOLDIERS" and e.side == "LONG"
           and S.ind["rsi"][e.i] > 70]
    assert [(e.i, e.stop, e.trigger) for e in hits] == [(e.i, e.stop, e.trigger) for e in lab] and len(hits) >= 3 and unclean == 0
    rows = df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
    bot = {}
    for end in range(300, len(rows) + 1):
        as_of = int(rows[end - 1]["timestamp"]) + H4
        an = analyze(market="USDM_PERP", symbol="PAR/USDT", timeframe="4h", bars=rows[end - 300:end], as_of_ms=as_of)
        plans, _ = build_plans_v3("PAR/USDT", as_of_ms=as_of, analyses={"4h": an}, bars_by_tf={"4h": rows[:end]})
        for p in plans:
            bot[end - 1] = (p["stop"], p["trigger"]["level"])
    assert {e.i: (round(e.stop, 10), round(e.trigger, 10)) for e in hits} == bot
    sp = B.FM_VARIANTS["FM_00_BASE"]
    assert (sp["exit"], sp["risk_atr"], sp["chase_atr"]) == ({"kind": "target", "rr": 2.0, "max_bars": 24}, [0.1, 5.0], 1.0)


# ---------------------------------------------------------------------------- simülatör
def _arr(df):
    return {k: df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume")}


def test_simulator_parity_with_lab_simulate_and_simulate_rule():
    df = synth(900, "4h", ms("2024-01-01"), seed=41, vol=0.015)
    S = B.Series(df, "4h")
    arr, atr, aux = _arr(df), S.ind["atr"], S.aux
    cfg = L.LabConfig()
    rnd = np.random.default_rng(3)
    n_fix = n_rule = 0
    for _ in range(600):
        i = int(rnd.integers(20, len(df) - 1))
        side = B.LONG if rnd.random() < 0.5 else B.SHORT
        s = 1 if side == B.LONG else -1
        a = float(atr[i])
        stop = float(S.c[i]) - s * float(rnd.uniform(0.0, 6.0)) * a
        # sabit hedef
        rr, H = float(rnd.choice([1.0, 2.0, 3.0])), int(rnd.choice([12, 24, 60]))
        trig = float(S.c[i]) if rnd.random() < 0.5 else None
        ev = L.Event("X", "4h", "t", "T", side, i, int(S.ts[i]) + H4, stop, trigger=trig)
        vc = dataclasses.replace(cfg, default_rr=rr, max_hold_bars=H)
        lab = L.simulate(ev, arr, atr, vc)
        tr, why = B.simulate_trade(S, i, side, stop, {"kind": "target", "rr": rr, "max_bars": H}, (0.1, 5.0), a, cfg, trigger=trig,
                                   chase_atr=1.0 if trig is not None else None)
        if lab == "":
            n_fix += 1
            assert why == "" and tr["reason"] == ev.exit_reason and tr["hold"] == ev.hold and math.isclose(tr["r"], ev.r, rel_tol=1e-12)
            assert math.isclose(tr["cost_r"], ev.cost_r, rel_tol=1e-12)
        elif lab != "NO_FUTURE_DATA":
            assert why == lab
        else:                                                                         # j + H > n: süre sınırına varılamaz
            assert why != "" or tr["reason"] in ("STOP", "TARGET", "DATA_END", "DELISTED")
        # kanal çıkışı
        ev2 = L.Event("X", "4h", "a", "T", side, i, int(S.ts[i]) + H4, stop, exit={"kind": "channel", "max_bars": H})
        lab2 = L.simulate_rule(ev2, arr, atr, aux, cfg)
        tr2, why2 = B.simulate_trade(S, i, side, stop, {"kind": "channel", "ref": {"LONG": "lo10", "SHORT": "hi10"}, "max_bars": H},
                                     (0.1, 10.0), a, cfg)
        if lab2 == "":
            n_rule += 1
            assert why2 == "" and tr2["reason"] == ev2.exit_reason and tr2["hold"] == ev2.hold
            assert math.isclose(tr2["r"], ev2.r, rel_tol=1e-12)
        elif lab2 != "NO_FUTURE_DATA":
            assert why2 == lab2
    assert n_fix > 150 and n_rule > 150


def test_data_end_delisted_cost_and_exit_times(mini):
    cfg = L.LabConfig()
    df = flat_series(N4, t0=T0)
    S = B.Series(df, "4h")
    n = S.n
    # sabit hedef, son barlarda açılan işlem: veri sonunda DATA_END, son barın kapanışı, maliyet dahil
    tr, why = B.simulate_trade(S, n - 5, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert why == "" and tr["reason"] == "DATA_END" and tr["exit_px"] == 100.0 and tr["xb"] == n - 1
    assert tr["exit_ms"] == B.PRICE_END_MS and tr["hold"] == 4
    cost = 200.0 * cfg.cost_per_side
    assert math.isclose(tr["r"], -cost / 2.0, rel_tol=1e-12) and math.isclose(tr["cost_r"], cost / 2.0)
    # süre sınırı (TIME): son bar kapanışı, maliyet yine dahil
    tr, _ = B.simulate_trade(S, 100, B.SHORT, 102.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert tr["reason"] == "TIME" and tr["xb"] == 124 and tr["exit_ms"] == int(S.ts[124]) + H4 and tr["r"] < 0
    # kanal: son barda kural tetiklenirse de DATA_END
    df2 = df.copy()
    df2.loc[n - 1, ["close", "low"]] = [99.5, 98.9]
    S2 = B.Series(df2, "4h")
    tr, _ = B.simulate_trade(S2, n - 3, B.LONG, 90.0, B.D4_VARIANTS["D4_00_BASE"]["exit"], (0.1, 10.0), 1.0, cfg)
    assert tr["reason"] == "DATA_END" and tr["exit_px"] == 99.5 and tr["xb"] == n - 1
    # kural çıkışı: k+1 açılışı, çıkış anı o açılış
    df3 = df.copy()
    df3.loc[300, ["close", "low"]] = [98.5, 98.4]
    S3 = B.Series(df3, "4h")
    tr, _ = B.simulate_trade(S3, 290, B.LONG, 90.0, B.D4_VARIANTS["D4_00_BASE"]["exit"], (0.1, 10.0), 1.0, cfg)
    assert tr["reason"] == "RULE" and tr["xb"] == 301 and tr["exit_ms"] == int(S3.ts[301]) and tr["exit_bar_ms"] == int(S3.ts[301])
    # STOP: isabet barının kapanışı
    df4 = df.copy()
    df4.loc[305, "low"] = 97.0
    tr, _ = B.simulate_trade(B.Series(df4, "4h"), 300, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0),
                             1.0, cfg)
    assert tr["reason"] == "STOP" and tr["exit_ms"] == int(S.ts[305]) + H4
    # seri erken biterse DELISTED; giriş barı yoksa NO_ENTRY_BAR
    S5 = B.Series(df.iloc[:500].reset_index(drop=True), "4h")
    tr, _ = B.simulate_trade(S5, 495, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert tr["reason"] == "DELISTED" and tr["exit_ms"] == int(S5.ts[499]) + H4
    assert B.simulate_trade(S5, 499, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)[1] == "NO_ENTRY_BAR"
    # COST_STOP: s < %1,6 → ret; eşitlik geçer
    S125 = B.Series(flat_series(N4, t0=T0, px=125.0), "4h")
    assert B.simulate_trade(S125, 100, B.LONG, 123.01, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg,
                            cost_stop=True)[1] == "STOP_TOO_TIGHT_FOR_COST"
    assert B.simulate_trade(S125, 100, B.LONG, 123.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg,
                            cost_stop=True)[1] == ""                                  # 2 / 125 = 0,016
    # bozuk bar geçen işlem BAD_BAR; boşluk geçen işlem işaretlenir
    df6 = df.copy()
    df6.loc[110, "high"] = 50.0
    assert B.simulate_trade(B.Series(df6, "4h"), 100, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0),
                            1.0, cfg)[1] == "BAD_BAR"
    df7 = df.drop(index=[110, 111]).reset_index(drop=True)
    tr, _ = B.simulate_trade(B.Series(df7, "4h"), 100, B.LONG, 98.0, {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), 1.0, cfg)
    assert tr["gap_cross"] and tr["gap_hours"] == 8.0


# ---------------------------------------------------------------------------- geleceğe bakmama (her varyant)
def _sig(r):
    return (r["variant"], r["side"], r["name"], int(r["t_ms"]), round(float(r["stop"]), 10), r["scopes"])


def _out(r):
    rv = r["r"]
    return _sig(r) + (r["reason"], int(r["hold"]), None if rv != rv else round(float(rv), 10), int(r["exit_bar_ms"]),
                      round(float(r["funding_r"]), 10) if r["funding_r"] == r["funding_r"] else None)


def test_no_lookahead_every_variant_signals_and_trades_are_prefix_invariant(world):
    t_cut = world["t_cut"]
    step = {"4h": H4, "1d": DAY}
    full = [r for r in world["full"] if r["kind"] == "real"]
    trunc = [r for r in world["trunc"] if r["kind"] == "real"]
    seen = set()
    for vid, spec in B.VARIANTS.items():
        f = [r for r in full if r["variant"] == vid and int(r["entry_ms"]) + step[r["tf"]] <= t_cut]
        t = [r for r in trunc if r["variant"] == vid and int(r["entry_ms"]) + step[r["tf"]] <= t_cut]
        assert sorted(map(_sig, f)) == sorted(map(_sig, t)), vid
        done = [r for r in f if int(r["exit_bar_ms"]) + step[r["tf"]] <= t_cut]
        assert set(map(_out, done)) <= set(map(_out, t)), vid
        if done:
            seen.add(vid)
    # geleceği değiştirilmiş tam seride kesimden sonraki işlemler gerçekten farklı (test boş değil)
    assert {_out(r) for r in full} != {_out(r) for r in world["base"] if r["kind"] == "real"}
    must = {"D4_00_BASE", "D4_01_BTC_UP", "D4_03_VOL_OK", "D4_05_STOP3ATR", "D4_06_EXIT_LO20", "D4_12_COST_STOP", "C4_00_BASE",
            "C4_01_REGIME", "C4_02_EXIT_1R", "C4_03_EXIT_3R", "C4_04_WIDE_STOP", "C4_05_COST_STOP", "C4_07_TREND_ONLY",
            "C4_08_REGIME_COST", "FM_00_BASE", "FM_07_EXIT_TREND", "FM_08_EXIT_3R", "FM_CTRL_NO_RSI"}
    assert must <= seen, sorted(must - seen)


def test_no_lookahead_raw_signals_for_every_variant_including_1d_and_rare_ones():
    """Ham sinyaller (süzgeçler dahil) karar penceresinden bağımsız: kesilmiş seri = geleceği değiştirilmiş tam seri."""
    df4 = synth(1300, "4h", ms("2024-01-01"), seed=51, vol=0.012, legs=30, leg_drift=0.008, spike=0.2)
    df1 = synth(900, "1d", ms("2022-01-01"), seed=52, vol=0.03, legs=35, leg_drift=0.012, spike=0.2)
    btc = wave_daily(1400, ms("2021-01-01"), period=50)
    cut4, cut1 = 1100, 800
    for tf, df, cut in (("4h", df4, cut4), ("1d", df1, cut1)):
        t_cut = int(df["timestamp"].iloc[cut])
        fut = df.copy()
        fut.loc[cut:, ["open", "high", "low", "close"]] *= 0.5
        futb = btc.copy()
        futb.loc[futb["timestamp"] + DAY > t_cut, ["open", "high", "low", "close"]] *= 2.0
        coin_full = df1.copy()
        coin_full.loc[coin_full["timestamp"] + DAY > t_cut, ["open", "high", "low", "close"]] *= 3.0

        def build(d, b, c1):
            S = B.Series(d, tf)
            cr = B.regime_daily(c1)
            return S, B._masks(S, B.regime_at(S.t, *B.regime_daily(b)), B.regime_at(S.t, *cr))
        Sf, mf = build(fut, futb, coin_full if tf == "4h" else fut)
        St, mt = build(df.iloc[:cut].reset_index(drop=True), tcut(btc, t_cut), tcut(df1, t_cut) if tf == "4h"
                       else df.iloc[:cut].reset_index(drop=True))
        recs_f = B.fm_records(Sf, "X/USDT") if tf == "4h" else []
        recs_t = B.fm_records(St, "X/USDT") if tf == "4h" else []
        fired = []
        for vid, spec in B.VARIANTS.items():
            if spec["tf"] != tf or spec["book"] == "C4":
                continue
            for side in spec["sides"]:
                if spec["book"] == "D4":
                    a, _, _ = B.d4_raw(Sf, spec, mf, side)
                    b, _, _ = B.d4_raw(St, spec, mt, side)
                    a = [int(i) for i in a if i < cut]
                    b = [int(i) for i in b]
                else:
                    a = [(e.i, e.stop, e.trigger) for e in B.fm_raw(Sf, recs_f, spec, mf, side)[0] if e.i < cut]
                    b = [(e.i, e.stop, e.trigger) for e in B.fm_raw(St, recs_t, spec, mt, side)[0]]
                assert a == b, (tf, vid, side)
                if a:
                    fired.append((vid, side))
        if tf == "1d":
            assert {"D4_07_1D", "D4_08_1D_55_20", "D4_09_1D_BTC_UP"} <= {v for v, _ in fired}
            assert ("D4_10_1D_REGIME_BOTH", "LONG") in fired and ("D4_10_1D_REGIME_BOTH", "SHORT") in fired
        else:
            assert {"D4_02_COIN_UP", "D4_04_VOL_CONFIRM", "D4_11_BTC_COIN_UP", "FM_00_BASE", "FM_02_COIN_UP",
                    "FM_03_VOL_OK", "FM_04_VOL_CONFIRM"} <= {v for v, _ in fired}
            assert ("FM_09_REGIME_BOTH", "SHORT") in fired and ("FM_09_REGIME_BOTH", "LONG") in fired
    # C4: tespit + rejim süzgeci + tekilleştirme (C4_06'nın DSL bağlamı dahil)
    t_cut = int(df4["timestamp"].iloc[cut4])
    fut = df4.copy()
    fut.loc[cut4:, ["open", "high", "low", "close"]] *= 0.5
    Sf, St = B.Series(fut.iloc[:cut4 + 150].reset_index(drop=True), "4h"), B.Series(df4.iloc[:cut4].reset_index(drop=True), "4h")
    df_ = {"f": Sf, "t": St}
    out = {}
    for k, S in df_.items():
        bm = B.regime_at(S.t, *B.regime_daily(btc if k == "t" else btc))
        masks = B._masks(S, bm, None)
        det = B.c4_detect(S, "X/USDT", ["base", "stop1", "volok"])
        for vid, spec in B.C4_VARIANTS.items():
            real = {}
            for cv in spec["cvs"]:
                side = B.c4_variation(cv, None).side
                dm = B.filter_mask(spec["filters"], side, masks, S.n)
                real[cv] = {e.i: e.stop for e in det[spec["detection"]][cv][0] if e.family == "candle_var" and dm[e.i] and e.i < cut4}
            for scope in spec["cells"]:
                w, _ = B.c4_dedup(real, spec["cvs"], scope)
                out.setdefault((vid, scope), {})[k] = sorted((cv, i, real[cv][i]) for cv, i in w)
    for key, v in out.items():
        assert v["f"] == v["t"], key
    assert sum(1 for v in out.values() if v["t"]) >= 20


def test_placebo_is_deterministic_keyed_period_rated_and_q_pool_is_prior_only(mini):
    df4, df1, btc1 = world_frames()
    S = B.Series(df4, "4h")
    masks = B._masks(S, B.regime_at(S.t, *B.regime_daily(btc1)), None)
    spec = B.D4_VARIANTS["D4_00_BASE"]
    raw, elig, _ = B.d4_raw(S, spec, masks, B.LONG)
    bars, info = B._placebo_bars(S, "SYN/USDT", "D4_00_BASE", B.LONG, raw, elig)
    bars2, info2 = B._placebo_bars(S, "SYN/USDT", "D4_00_BASE", B.LONG, raw, elig)
    assert bars == bars2 and info == info2 and bars
    per = np.array([B.period_of(int(t)) or "" for t in S.t])
    for p_ in ("IS", "OOS"):
        m = per == p_
        assert info["raw"][p_] == int(np.isin(np.flatnonzero(m), raw).sum())
        assert info["eligible"][p_] == int((elig & m).sum()) and info["p"][p_] == info["raw"][p_] / info["eligible"][p_]
    for j in bars:                                                                   # anahtar: crc32("<sembol>|<dilim>|<kimlik>|<yön>|<ts>")
        u = zlib.crc32(f"SYN/USDT|4h|D4_00_BASE|LONG|{int(S.ts[j])}".encode()) / 2 ** 32
        assert elig[j] and u < info["p"][per[j]] and per[j] in ("IS", "OOS")
    # Formasyon q havuzu: yalnız j'den ÖNCEKİ ham isabetler; havuz boş → NO_REAL_RISK
    recs = [L.Event("SYN/USDT", "4h", "x", "THREE_WHITE_SOLDIERS", "LONG", i, int(S.ts[i]) + H4, float(S.c[i]) - q * float(S.ind["atr"][i]),
                    trigger=float(S.c[i])) for i, q in ((620, 1.5), (700, 2.5), (800, 3.5))]
    vid = "FM_CTRL_NO_RSI"
    fspec = B.FM_VARIANTS[vid]
    rows, meta = B.fm_series_rows("SYN/USDT", S, B._masks(S, None, None), L.LabConfig(), [vid], recs=recs)
    pl = [r for r in rows if r["kind"] == "placebo"]
    hits, elig_f, _ = B.fm_raw(S, recs, fspec, B._masks(S, None, None), B.LONG)
    pbars, pinf = B._placebo_bars(S, "SYN/USDT", vid, B.LONG, [e.i for e in hits], elig_f)
    assert meta[f"{vid}|LONG"]["placebo"]["skipped"].get("NO_REAL_RISK", 0) == sum(1 for j in pbars if j <= 620) > 0
    for r in pl:
        j = r["i"]
        prior = [q for i, q in ((620, 1.5), (700, 2.5), (800, 3.5)) if i < j]
        q = (float(S.c[j]) - r["stop"]) / float(S.ind["atr"][j])
        k = int(zlib.crc32(f"SYN/USDT|4h|{vid}|q|{int(S.ts[j])}".encode()) / 2 ** 32 * len(prior))
        assert math.isclose(q, prior[k], rel_tol=1e-9)
    assert pl


def test_c4_dedup_order_regime_before_dedup_and_cost_stop_after_without_fallback(mini):
    """§6.2 örnekleri: DOWN rejiminde aynı barda CV001 LONG + CV004 SHORT → C4_01'de işlem CV004; C4_00'da CV001; tek yönlü
    hücreler kendi yönünde; COST_STOP elenen seçilmiş varyasyon alttakine DÜŞMEZ."""
    df4, _, _ = world_frames()
    S = B.Series(df4, "4h")
    i = 700
    a = float(S.ind["atr"][i])
    mk = lambda cv, side, stop, fam="candle_var": L.Event("SYN/USDT", "4h", fam, cv, side, i, int(S.ts[i]) + H4, stop)  # noqa: E731
    c = float(S.c[i])
    evs = {cv: ([], np.full(S.n, a), {}) for cv in B.CV_ORDER}
    evs["CV001_BREAKOUT20_TREND_VOL_L"] = ([mk("CV001_BREAKOUT20_TREND_VOL_L", "LONG", c - 0.5 * a)], np.full(S.n, a), {})
    evs["CV003_PULLBACK_ENGULF_L"] = ([mk("CV003_PULLBACK_ENGULF_L", "LONG", c - 3.0 * a)], np.full(S.n, a), {})
    evs["CV004_PULLBACK_ENGULF_S"] = ([mk("CV004_PULLBACK_ENGULF_S", "SHORT", c + 2.0 * a)], np.full(S.n, a), {})
    det = {"base": evs}
    down = B._masks(S, np.full(S.n, -1, dtype=np.int8), None)
    rows, meta = B.c4_series_rows("SYN/USDT", S, down, L.LabConfig(), ["C4_00_BASE", "C4_01_REGIME", "C4_05_COST_STOP"], det=det)
    pick = {(r["variant"], sc): r["name"] for r in rows if r["kind"] == "real" for sc in r["scopes"].split(",") if sc}
    assert pick[("C4_00_BASE", "BOTH")] == "CV001_BREAKOUT20_TREND_VOL_L"
    assert pick[("C4_00_BASE", "LONG")] == "CV001_BREAKOUT20_TREND_VOL_L"
    assert pick[("C4_00_BASE", "SHORT")] == "CV004_PULLBACK_ENGULF_S"
    assert pick[("C4_01_REGIME", "BOTH")] == "CV004_PULLBACK_ENGULF_S" and ("C4_01_REGIME", "LONG") not in pick
    assert meta["C4_00_BASE"]["suppressed"] == {"BOTH": 2, "LONG": 1, "SHORT": 0}
    # CV001'in stop'u dar (s < %1,6) → C4_05'te elenir ve CV003'e DÜŞÜLMEZ
    s = abs(float(S.o[i + 1]) - (c - 0.5 * a)) / float(S.o[i + 1])
    assert s < B.COST_STOP_MIN
    assert ("C4_05_COST_STOP", "LONG") not in pick and pick[("C4_05_COST_STOP", "SHORT")] == "CV004_PULLBACK_ENGULF_S"
    assert meta["C4_05_COST_STOP"]["cvs"]["CV001_BREAKOUT20_TREND_VOL_L"]["real"]["skipped"] == {"STOP_TOO_TIGHT_FOR_COST": 1}


# ---------------------------------------------------------------------------- fonlama
def test_funding_uses_funding_carry_and_fills_nan_conservatively(mini):
    from tradingbot import futures_lab as FL
    df = synth(N4, "4h", T0, seed=61)
    S = B.Series(df, "4h")
    cfg = L.LabConfig()
    items = []
    for i, side in ((600, "LONG"), (650, "SHORT"), (N4 - 3, "LONG")):
        s = 1 if side == "LONG" else -1
        tr, why = B.simulate_trade(S, i, side, float(S.c[i]) - s * 3 * float(S.ind["atr"][i]),
                                   {"kind": "target", "rr": 2.0, "max_bars": 24}, (0.1, 5.0), float(S.ind["atr"][i]), cfg)
        assert why == ""
        items.append(B._row("SYN/USDT", S, "D4", "D4_00_BASE", "real", side, "X", tr, cfg))
    ft = np.arange(MINI["FUNDING_START_MS"], MINI["FUNDING_END_MS"], 8 * HOUR, dtype=np.int64)
    raw = {"f_t": ft, "f_rate": np.full(len(ft), 0.0003)}
    rows = copy.deepcopy(items)
    B.apply_funding(rows, S, raw)
    ref = []
    for r in items:
        ev = L.Event("SYN/USDT", "4h", "book", "T", r["side"], r["i"], r["t_ms"], r["stop"],
                     exit_reason="TIME" if r["reason"] in ("TIME", "DATA_END") else r["reason"], hold=r["hold"])
        ref.append(ev)
    FL.funding_carry(ref, S.arr, S.ts, S.step, raw, names=frozenset({"T"}))
    for r, ev in zip(rows, ref):
        assert r["funding_nan"] is False and r["funding_r"] == ev.funding_r and r["funding_raw"] == ev.funding_r
    assert rows[0]["funding_r"] < 0 < rows[1]["funding_r"]                          # LONG öder, SHORT alır
    assert rows[2]["reason"] == "DATA_END" and rows[2]["funding_nan"] is False      # PRICE_END sonrası uzlaşma var
    rows2 = copy.deepcopy(items)
    B.apply_funding(rows2, S, None)                                                  # fonlama yok → tutucu doldurma
    for r in rows2:
        h = (r["exit_ms"] - r["entry_ms"]) / HOUR
        want = -0.0001 * r["entry"] * math.ceil(h / 8 - 1e-12) / r["risk"]
        assert r["funding_nan"] is True and r["funding_raw"] is None and math.isclose(r["funding_r"], want, rel_tol=1e-12)
        assert r["funding_r"] < 0


# ---------------------------------------------------------------------------- aylık ölçü
def _trades(spec):
    """spec: [(çıkış ayı 'YYYY-MM', R, karar anı ms)] → hücre işlem çerçevesi."""
    rows = []
    for mk, r, t in spec:
        e = B.month_start_ms(mk) + 5 * DAY
        rows.append({"symbol": "X", "t_ms": t, "period": B.period_of(t), "r": r, "rf": r - 0.01, "exit_bar_ms": e, "exit_ms": e + H4,
                     "entry_ms": t, "funding_nan": False, "mtm": {}, "w": 1.0, "reason": "STOP"})
    return pd.DataFrame(rows)


def test_monthly_exit_month_zero_months_and_bootstrap_interval():
    t_is = ms("2024-12-30")
    df = _trades([("2023-03", 2.0, ms("2023-03-01")), ("2025-01", 4.0, t_is), ("2025-01", -1.0, ms("2025-01-02")),
                  ("2026-09", 1.0, ms("2026-09-29"))])
    m = B.monthly(df, "r")
    assert m["IS"]["months"] == 24 and m["OOS"]["months"] == 21
    assert m["IS"]["series"]["2023-03"] == 1.0 and m["IS"]["months_without_trades"] == 23
    # keşifte açılıp doğrulamada kapanan işlem doğrulama ayına yazılır
    assert m["OOS"]["series"]["2025-01"] == 1.5 and m["OOS"]["series"]["2026-09"] == 0.5 and m["OOS"]["months_without_trades"] == 19
    assert math.isclose(m["OOS"]["mean_pct_exact"], 2.0 / 21, rel_tol=1e-12)
    assert m["OOS"]["worst_month"]["pct"] == 0.0 and m["OOS"]["share_ge_target"] == round(1 / 21, 4)
    vals = np.array([m["OOS"]["series"][k] for k in B.OOS_MONTHS])
    idx = np.random.default_rng(20261005).integers(0, 21, size=(10_000, 21))
    means = vals[idx].mean(axis=1)
    assert m["OOS"]["ci95_exact"] == [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]
    w = np.array([1.0, 0.5, 2.0, 1.0])
    mw = B.monthly(df, "r", w)
    assert mw["OOS"]["series"]["2025-01"] == round(0.5 * (0.5 * 4.0 + 2.0 * -1.0), 4)
    assert B.monthly(df, "r", by="t_ms")["IS"]["series"]["2024-12"] == 2.0           # bilgi: karar ayı


# ---------------------------------------------------------------------------- kapasite
def _cands(rows):
    out = []
    for k, (sym, t, exit_t, entry, stop, r) in enumerate(rows):
        out.append({"symbol": sym, "t_ms": t, "exit_ms": exit_t, "exit_bar_ms": exit_t - H4, "entry_ms": t, "entry": entry, "stop": stop,
                    "r": r, "rf": r, "funding_nan": False, "period": B.period_of(t), "reason": "STOP", "mtm": {}})
    return pd.DataFrame(out)


def test_capacity_hand_computed_fit_size_bump_and_no_bump():
    t = ms("2025-03-01")
    table = {"A/USDT": (5.0, 0.001, 0.001), "B/USDT": (100.0, 0.001, 0.001)}
    # s = %2: slot tavanı 0,95 × 200 / 20 × 3 = 28,5 USDT → w = 0,57 (belgedeki örnek)
    res = B.capacity_run(_cands([("A/USDT", t, t + DAY, 100.0, 98.0, 1.0)]), "D4", "D4_00_BASE", table=table, bump=True)
    assert math.isclose(res["accepted"]["w"].iloc[0], 0.57, rel_tol=1e-9)
    # s ≥ %3,51 → tam risk
    res = B.capacity_run(_cands([("A/USDT", t, t + DAY, 100.0, 96.0, 1.0)]), "D4", "D4_00_BASE", table=table, bump=True)
    assert math.isclose(res["accepted"]["w"].iloc[0], 1.0, rel_tol=1e-9)
    # Formasyon (E = 100, K = 30): w = min(1, 19 × s)
    res = B.capacity_run(_cands([("A/USDT", t, t + DAY, 100.0, 98.0, 1.0)]), "FM", "FM_00_BASE", table=table, bump=True)
    assert math.isclose(res["accepted"]["w"].iloc[0], 0.38, rel_tol=1e-9)
    # min-notional 100: BUMP → 100 USDT'ye çıkarılır (risk 2 USDT, w = 2); NO_BUMP → MIN_ORDER_CONFLICT
    c = _cands([("B/USDT", t, t + DAY, 100.0, 98.0, 1.0)])
    rb = B.capacity_run(c, "D4", "D4_00_BASE", table=table, bump=True)
    rn = B.capacity_run(c, "D4", "D4_00_BASE", table=table, bump=False)
    assert math.isclose(rb["accepted"]["w"].iloc[0], 2.0, rel_tol=1e-9) and rb["bumped"] == 1
    assert len(rn["accepted"]) == 0 and rn["rejected"] == {"MIN_ORDER_CONFLICT": 1}
    # PIT: tablo yok → min-notional 0, çıkarma yok
    rp = B.capacity_run(c, "D4", "D4_00_BASE", table=None, bump=False)
    assert math.isclose(rp["accepted"]["w"].iloc[0], 0.57, rel_tol=1e-9)


def test_capacity_slots_same_symbol_exit_frees_slot_and_neutral_order():
    t = ms("2025-03-01")
    table = {f"S{k}/USDT": (5.0, 0.001, 0.001) for k in range(40)}
    rows = [(f"S{k}/USDT", t, t + 10 * H4, 100.0, 96.0, 1.0) for k in range(22)]
    rows += [("S0/USDT", t + H4, t + 5 * H4, 100.0, 96.0, 1.0)]                         # aynı sembol açık
    rows += [("S30/USDT", t + 10 * H4, t + 20 * H4, 100.0, 96.0, 1.0)]                  # çıkışla aynı anda: slot boşalır
    res = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=table, bump=True)
    assert res["rejected"] == {"SLOTS_FULL": 2, "SAME_SYMBOL": 1} and len(res["accepted"]) == 21
    acc = set(res["accepted"]["symbol"])
    first = sorted([f"S{k}/USDT" for k in range(22)], key=lambda s: L._h(s, t, "D4_00_BASE"))
    assert acc == set(first[:20]) | {"S30/USDT"}                                      # aynı t'de _h(sembol, t, kimlik) sırası
    k3 = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=table, bump=True, slots=3)
    assert len(k3["accepted"]) == 4
    cfg_o = B.capacity_run(_cands(rows), "D4", "D4_00_BASE", table=table, bump=True, order="config",
                           order_list=[f"S{k}/USDT" for k in range(40)])
    assert set(cfg_o["accepted"]["symbol"]) == {f"S{k}/USDT" for k in range(20)} | {"S30/USDT"}
    conc = B.concurrency(res["accepted"])
    assert conc["ALL"]["max"] == 20 and conc["OOS"]["mean"] > 0


def test_capacity_cell_monthly_weights_nan_share_and_filter_wait(tmp_path):
    t = ms("2025-03-01")
    real = _cands([("A/USDT", t, t + DAY, 100.0, 98.0, 2.0), ("B/USDT", t, t + DAY, 100.0, 96.0, -1.0)])
    real.loc[1, "funding_nan"] = True
    real["rf"] = real["r"] - 0.1
    p = tmp_path / "symbol_filters.json"
    p.write_text(json.dumps({"verified_at": "2026-10-05T00:00:00", "futures": {
        "A/USDT": {"min_notional": "5", "qty_step": "0.001", "min_qty": "0.001"},
        "BUSDT": {"min_notional": "5", "qty_step": "0.001", "min_qty": "0.001"}}}), encoding="utf-8")
    flt = B.load_filters(p, ["A/USDT", "B/USDT"])
    assert flt["ok"] and flt["verified_at"] and flt["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert not B.load_filters(p, ["A/USDT", "C/USDT"])["ok"] and not B.load_filters(None, ["A/USDT"])["ok"]
    cap = B.capacity_cell(real, "D4", "D4_00_BASE", filters=flt, universe_order=["A/USDT", "B/USDT"])
    for mode in B.CAP_MODES:
        m = cap[mode]
        assert m["accepted"] == 2 and m["nan_funding_share_oos"] == 0.5
        assert math.isclose(m["unfunded"]["OOS"]["series"]["2025-03"], round(0.5 * (0.57 * 2.0 + 1.0 * -1.0), 4), abs_tol=1e-4)
    assert cap["info"]["K3"]["accepted"] == 2


def test_target_status_conditions_and_pooled_direction_rule():
    def cell(scope="LONG", strict=(L.V_STRONG, L.V_STRONG), oos=(1.2, 1.1), lo=0.2, is_=0.3, nan=0.0, flags=(), cap=True):
        st = {f: {"verdict_strict": strict[k], "verdict": strict[k]} for k, f in enumerate(("unfunded", "funded"))}
        m = {"unfunded": {"OOS": {"mean_pct_exact": oos[0]}, "IS": {"mean_pct_exact": 0.5}},
             "funded": {"OOS": {"mean_pct_exact": oos[1], "ci95_exact": [lo, 2.0]}, "IS": {"mean_pct_exact": is_}},
             "nan_funding_share_oos": nan}
        return {"variant": "D4_10_1D_REGIME_BOTH", "scope": scope, "stats": st, "flags": list(flags),
                "capacity": {"BUMP": m, "NO_BUMP": copy.deepcopy(m)} if cap else {"status": B.ST_FILTER_WAIT}}
    ok = cell()
    assert B.target_status(ok, {})["meets"] and B.target_status(ok, {})["status"] == B.ST_MEETS
    assert B.target_status(cell(oos=(1.0, 1.0)), {})["meets"]                         # ≥ +1,0 (eşitlik geçer)
    assert "2:AYLIK_BUMP_funded" in B.target_status(cell(oos=(1.2, 0.9999)), {})["failed"]
    assert "3:ARALIK_BUMP" in B.target_status(cell(lo=0.0), {})["failed"]
    assert "4:KESIF_BUMP" in B.target_status(cell(is_=0.0), {})["failed"]
    assert B.target_status(cell(nan=0.051), {})["status"] == B.ST_FUND_MISSING
    assert B.target_status(cell(strict=(L.V_STRONG, L.V_WEAK)), {})["failed"] == ["1:SIKI_FONLAMALI"]
    assert B.target_status(cell(flags=[B.ST_CENSOR]), {})["failed"] == ["7:" + B.ST_CENSOR]
    assert B.target_status(cell(cap=False), {})["status"] == B.ST_FILTER_WAIT
    assert B.target_status(cell(cap=False, strict=(L.V_WEAK, L.V_WEAK)), {})["status"] == B.ST_NOT_MEETS
    nm = cell()
    nm["capacity"]["NO_BUMP"]["unfunded"]["OOS"]["mean_pct_exact"] = 0.5               # kötü olan biçim belirler
    assert B.target_status(nm, {})["failed"] == ["2:AYLIK_NO_BUMP_unfunded"]
    both = cell(scope="BOTH")
    others = {"D4_10_1D_REGIME_BOTH|LONG": cell(), "D4_10_1D_REGIME_BOTH|SHORT": cell(strict=(L.V_LOSS, L.V_STRONG))}
    assert B.target_status(both, others)["failed"] == ["8:YON_SHORT_unfunded"]


def test_holm_and_multiple_test_pvalue():
    p = {"a": 0.001, "b": 0.012, "c": 0.03, "d": 0.04}
    assert B.holm(p) == {"a": True, "b": True, "c": False, "d": False}               # 0,05/4, 0,05/3 geçer; 0,03 > 0,05/2 → durur
    assert B.holm({"a": 0.02, "b": 0.001}) == {"a": True, "b": True}
    assert B.holm({"a": 0.03, "b": 0.03}) == {"a": False, "b": False}
    t0 = ms("2025-02-01")
    real = pd.DataFrame({"period": "OOS", "r": np.linspace(0.5, 1.5, 40), "t_ms": t0 + np.arange(40) * DAY})
    plac = pd.DataFrame({"period": "OOS", "r": np.linspace(-0.5, 0.5, 40), "t_ms": t0 + np.arange(40) * DAY})
    pv = B.mt_pvalue(real, plac, iters=2000)
    assert pv == 1 / 2001
    assert B.mt_pvalue(real.iloc[:4], plac, iters=200) == 1.0                         # gün < 5 → p = 1
    assert B.mt_pvalue(plac, real, iters=2000) > 0.99


# ---------------------------------------------------------------------------- PIT
def test_pit_pool_ranking_listing_and_missing_pairs():
    names = ["BTCUSDT", "BTCUSDT_210326", "ETHBUSD", "AERGOUSDTSETTLED", "TSLAUSDT", "BTCDOMUSDT", "USDCUSDT", "XAUUSDT",
             "1000PEPEUSDT", "PAXGUSDT", "SOLUSDT", "龙虾USDT"]
    pool, excl = B.pit_pool(names)
    assert pool == sorted(["BTCUSDT", "1000PEPEUSDT", "SOLUSDT", "龙虾USDT"])
    assert excl["TSLAUSDT"] == "hisse_etf_halka_arz_oncesi" and excl["XAUUSDT"] == "emtia_doviz" and excl["USDCUSDT"] == "stabil"
    assert excl["BTCDOMUSDT"] == "endeks" and "alt çizgi" in excl["BTCUSDT_210326"] and "USDT" in excl["ETHBUSD"]
    m = B.month_start_ms("2023-03")
    days = lambda n, qv, end=m: pd.DataFrame({"timestamp": end - DAY * np.arange(n, 0, -1), "quote_volume": qv})  # noqa: E731
    frames = {"AUSDT": days(30, 10.0), "BUSDT": days(30, 20.0), "CUSDT": days(29, 1e9), "DUSDT": days(40, 20.0),
              **{f"X{k}USDT": days(30, 1.0) for k in range(45)}}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(B, "PIT_MONTHS", ["2023-03"])
        uni = B.pit_universe(sorted(frames), frames.get)
    top = uni["2023-03"]
    assert len(top) == 40 and top[:3] == ["B/USDT", "D/USDT", "A/USDT"] and "C/USDT" not in top   # en az 30 bar; eşitlikte ad
    assert top[3:] == [f"X{k}/USDT" for k in sorted(range(45), key=lambda k: f"X{k}USDT")][:37]
    pages = ['<ListBucketResult><IsTruncated>true</IsTruncated><NextMarker>data/futures/um/monthly/klines/B/</NextMarker>'
             '<CommonPrefixes><Prefix>data/futures/um/monthly/klines/AUSDT/</Prefix></CommonPrefixes></ListBucketResult>',
             '<ListBucketResult><IsTruncated>false</IsTruncated><CommonPrefixes><Prefix>data/futures/um/monthly/klines/BUSDT/'
             '</Prefix></CommonPrefixes></ListBucketResult>']
    asked = []
    assert B.list_um_symbols(lambda u: (asked.append(u), pages[len(asked) - 1])[1]) == ["AUSDT", "BUSDT"]
    assert "marker=" in asked[1]
    d1 = pd.DataFrame({"timestamp": m + DAY * np.arange(31)})
    d4 = pd.DataFrame({"timestamp": m + H4 * np.arange(31 * 6)})
    mp_ = B.pit_missing_pairs({"2023-03": ["A/USDT", "B/USDT"]}, {"A/USDT": d4, "B/USDT": d4.iloc[:100]}, {"A/USDT": d1, "B/USDT": d1})
    assert mp_["pairs"] == 2 and mp_["missing"] == 1 and mp_["ok"] is False


def test_parse_daily_quote_volume_with_and_without_header_and_microseconds():
    df = pd.DataFrame({"timestamp": [ms("2025-02-01"), ms("2025-02-02")], "open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5],
                       "close": [1.5, 2.5], "volume": [10.0, 20.0]})
    for header, micro in ((True, False), (False, False), (True, True)):
        q = B.parse_daily_qv(kline_zip(df, header=header, micro=micro))
        assert q["timestamp"].tolist() == df["timestamp"].tolist() and q["quote_volume"].tolist() == [15.0, 50.0]


# ---------------------------------------------------------------------------- uçtan uca (sahte arşiv, ağ YOK)
def kline_zip(df, header=True, micro=False):
    buf = io.BytesIO()
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"] \
        if header else []
    for t, o, h, lo, c, v in df[["timestamp", "open", "high", "low", "close", "volume"]].itertuples(index=False):
        tt = int(t) * (1000 if micro else 1)
        lines.append(f"{tt},{o!r},{h!r},{lo!r},{c!r},{v!r},{tt + 1},{v * c!r},1,0,0,0")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("k.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def fake_archive(frames, funding_syms, rate=0.0001):
    """{(sembol, dilim): df} → URL → zip (aylık); fonlama aylık dosyaları + gün dosyası; bilinmeyen dosya None (404)."""
    files = {}
    for (sym, tf), df in frames.items():
        a = B.archive_symbol(sym)
        for mon, g in df.groupby(pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m")):
            files[f"{L.ARCHIVE_BASE}/monthly/klines/{a}/{tf}/{a}-{tf}-{mon}.zip"] = kline_zip(g, header=(mon > "2022-03"))
    for sym in funding_syms:
        a = B.archive_symbol(sym)
        for mon in B._month_range("2022-01", "2022-06"):
            m0 = B.month_start_ms(mon)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(
                    f"{m0 + k * 8 * HOUR},8,{rate}" for k in range(31 * 3) if m0 + k * 8 * HOUR < L._next_month(m0)))
            files[f"{L.ARCHIVE_BASE}/monthly/fundingRate/{a}/{a}-fundingRate-{mon}.zip"] = buf.getvalue()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(
                f"{MINI['PRICE_END_MS'] + k * 8 * HOUR},8,{rate}" for k in range(3)))
        files[f"{L.ARCHIVE_BASE}/daily/fundingRate/{a}/{a}-fundingRate-2022-07-01.zip"] = buf.getvalue()
    return files


def test_end_to_end_dry_run_offline_report_only_no_network_and_cli(tmp_path, monkeypatch, mini):
    def boom(*a, **k):
        raise AssertionError("ağ kullanılmamalı")
    monkeypatch.setattr(L, "_http_get", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    df4, df1, btc1 = world_frames()
    btc4 = synth(N4, "4h", T0, seed=71, vol=0.01, legs=50, leg_drift=0.003)
    frames = {("BTC/USDT", "4h"): btc4, ("BTC/USDT", "1d"): btc1, ("SYN/USDT", "4h"): df4, ("SYN/USDT", "1d"): df1}
    files = fake_archive(frames, ["BTC/USDT", "SYN/USDT"])
    asked = []
    fetch = lambda url: (asked.append(url), files.get(url))[1]  # noqa: E731
    flt = tmp_path / "symbol_filters.json"
    flt.write_text(json.dumps({"verified_at": "2026-10-05T00:00:00", "futures": {
        s: {"min_notional": "5", "qty_step": "0.001", "min_qty": "0.001"} for s in ("BTC/USDT", "SYN/USDT")}}), encoding="utf-8")
    out, cache = tmp_path / "out", tmp_path / "cache"
    now = ms("2022-08-01")
    rep = B.run(cache_dir=cache, out_dir=out, books=B.BOOKS, filters_path=flt, fetch=fetch, now_ms=now, log=lambda s: None,
                symbols_override=["BTC/USDT", "SYN/USDT"])
    assert rep["registry_sha"] == PINNED_SHA and rep["symbols_overridden"] is True and rep["complete"] is True
    assert set(rep["cells"]) == set(B.ALL_CELLS) and len(rep["cells"]) == 55
    c = rep["cells"]["D4_00_BASE|LONG"]
    assert c["counts"]["real"]["IS"] + c["counts"]["real"]["OOS"] > 0 and "BUMP" in c["capacity"]
    assert all(cc["final"] for cc in rep["cells"].values()) and rep["conclusion_tr"]
    assert all(0 <= cc["multiple"]["p"] <= 1 for k, cc in rep["cells"].items() if k in B.PRIMARY_CELLS)
    q = rep["data_quality"]
    assert q["SYN/USDT 4h"]["bars"] == N4 and q["SYN/USDT 4h"]["missing_ratio"] == 0.0 and q["SYN/USDT 4h"]["bad_bars"] == 0
    assert q["SYN/USDT 4h"]["sha256"] == B.series_digest(df4) and q["BTC/USDT 1d"]["first"] == "2020-11-27T00:00:00Z"
    assert rep["funding"]["SYN/USDT"]["rows"] > 0 and rep["funding_files_sha256"]["SYN/USDT"]
    assert rep["coverage"]["OOS"]["coins_full"] == 2
    js = json.loads((out / "book_lab_report.json").read_text(encoding="utf-8"))
    md = (out / "book_lab_report.md").read_text(encoding="utf-8")
    assert js["registry_sha"] == PINNED_SHA and PINNED_SHA in md and "UYARI" in md and "| D4_00_BASE|LONG |" in md
    T = B.read_trades(out / "book_lab_trades.csv.gz")
    assert set(T["book"]) == {"D4", "C4", "FM"} and {"real", "placebo", "labplacebo"} <= set(T["kind"])
    assert (T["period"].isin(["IS", "OOS"])).all() and not list(out.glob("*.part"))
    assert all(u.startswith(L.ARCHIVE_BASE) for u in asked)
    # kayıtlı işlemlerden yalnız rapor: aynı hücre sonuçları (belirlenimci); filtre tablosu yoksa FİLTRE BEKLİYOR
    n_asked = len(asked)
    rep2 = B.run(cache_dir=cache, out_dir=out, filters_path=flt, report_only=True, log=lambda s: None, now_ms=now)
    assert len(asked) == n_asked
    strip = lambda r: {k: {kk: vv for kk, vv in v.items() if kk != "info"} for k, v in r["cells"].items()}  # noqa: E731
    assert strip(rep2) == strip(rep)
    rep3 = B.run(cache_dir=cache, out_dir=out, report_only=True, log=lambda s: None, now_ms=now)
    assert all(cc["capacity"] == {"status": B.ST_FILTER_WAIT} for cc in rep3["cells"].values())
    assert any(cc["final"] == B.ST_FILTER_WAIT or "1:" in " ".join(cc["target"]["failed"]) for k, cc in rep3["cells"].items()
               if k in B.PRIMARY_CELLS)
    # çevrimdışı tekrar: önbellekten, aynı işlemler; yalnız D4 yeniden üretilir, diğer defterler korunur
    rep4 = B.run(cache_dir=cache, out_dir=tmp_path / "out2", books=["D4"], filters_path=flt, offline=True, now_ms=now,
                 log=lambda s: None, symbols_override=["BTC/USDT", "SYN/USDT"])
    assert len(asked) == n_asked and rep4["complete"] is False and set(rep4["books"]) == {"D4"}
    T4 = B.read_trades(tmp_path / "out2" / "book_lab_trades.csv.gz")
    a = T[T["book"] == "D4"].sort_values(["variant", "kind", "symbol", "t_ms", "side"]).reset_index(drop=True)
    b = T4.sort_values(["variant", "kind", "symbol", "t_ms", "side"]).reset_index(drop=True)
    assert a[["variant", "kind", "symbol", "t_ms", "r", "funding_r"]].equals(b[["variant", "kind", "symbol", "t_ms", "r", "funding_r"]])
    # farklı mühürlü önceki koşu ezilmez
    meta = json.loads((out / "book_lab_meta.json").read_text(encoding="utf-8"))
    meta["registry_sha"] = "0" * 16
    (tmp_path / "out3").mkdir()
    (tmp_path / "out3" / "book_lab_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(B.BookDataError, match="mühür"):
        B.run(cache_dir=cache, out_dir=tmp_path / "out3", offline=True, log=lambda s: None, now_ms=now)
    # PIT kipi: sahte liste, yalnız D4 (hız); PIT sonucu birincil rapora yazılır
    lst = '<ListBucketResult><IsTruncated>false</IsTruncated>' + "".join(
        f"<CommonPrefixes><Prefix>data/futures/um/monthly/klines/{s}/</Prefix></CommonPrefixes>"
        for s in ("BTCUSDT", "SYNUSDT", "TSLAUSDT")) + "</ListBucketResult>"
    rp = B.run(cache_dir=cache, out_dir=out, universe="pit", books=["D4"], fetch=fetch, list_text=lambda u: lst, now_ms=now,
               log=lambda s: None)
    assert rp["universe"] == "pit" and rp["pit"]["excluded"] == {"TSLAUSDT": "hisse_etf_halka_arz_oncesi"}
    assert set(rp["pit"]["selected"]) == {"BTC/USDT", "SYN/USDT"} and rp["pit"]["missing_pairs"]["ok"] is True
    assert all("pit_check" in cc and "NO_BUMP" in cc["capacity"] and "BUMP" not in cc["capacity"] for cc in rp["cells"].values())
    assert (out / "book_lab_report_pit.json").exists() and (cache / "pit_listing.json").exists()
    rep5 = B.run(cache_dir=cache, out_dir=out, filters_path=flt, report_only=True, log=lambda s: None, now_ms=now)
    assert "PIT teyidi" in (out / "book_lab_report.md").read_text(encoding="utf-8") and rep5["conclusion_tr"]


def test_apply_pit_promotes_only_waiting_cells():
    rep = {"universe": "primary", "cells": {"D4_01_BTC_UP|LONG": {"final": B.ST_PIT_WAIT}, "D4_02_COIN_UP|LONG": {"final": B.ST_PIT_WAIT},
                                            "D4_00_BASE|LONG": {"final": B.ST_BASE}}}
    pit = {"registry_sha": B.BOOK_REGISTRY_SHA, "cells": {
        "D4_01_BTC_UP|LONG": {"pit_check": {"pass": True, "status": B.ST_PIT_PASS}},
        "D4_02_COIN_UP|LONG": {"pit_check": {"pass": False, "status": B.ST_PIT_FAIL}},
        "D4_00_BASE|LONG": {"pit_check": {"pass": True, "status": B.ST_PIT_PASS}}}}
    B.apply_pit(rep, pit)
    assert [rep["cells"][k]["final"] for k in ("D4_01_BTC_UP|LONG", "D4_02_COIN_UP|LONG", "D4_00_BASE|LONG")] == \
        [B.ST_CANDIDATE, B.ST_PIT_FAIL, B.ST_BASE]
    assert rep["conclusion_tr"].startswith("ÖNERİ ADAYI: D4_01_BTC_UP|LONG")
    assert B.pit_check({"capacity": {}, "stats": {}}, verified=False)["status"] == B.ST_PIT_UNVERIFIED


def test_cli_offline_without_cache_fails_cleanly_and_logs_the_attempt(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("book_lab_cli", ROOT / "scripts" / "book_lab.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    out = tmp_path / "out"
    code = cli.main(["--cache", str(tmp_path / "c"), "--out", str(out), "--offline", "--jobs", "1", "--books", "D4"])
    assert code == 2
    lines = [json.loads(x) for x in (out / "book_lab_attempts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["status"] for x in lines] == ["STARTED", "ERROR"] and lines[0]["attempt"] == lines[1]["attempt"]
    assert lines[1]["registry_sha"] == PINNED_SHA and "çevrimdışı" in lines[1]["error"]
    assert PINNED_SHA in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--universe", "yok"])


def test_import_makes_no_network_call():
    src = (ROOT / "tradingbot" / "book_lab.py").read_text(encoding="utf-8")
    assert "urlopen(" not in src and "requests." not in src
