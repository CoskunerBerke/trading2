# -*- coding: utf-8 -*-
"""KURAL CIKISI GIRISIN ON KOSULLARINA BAGLI DEGILDIR (2026-09-19).

OLCULEN KUSUR
-------------
`decide()` hem giris hem cikis icin tek `read_daily()` cagirisini kullaniyordu. `read_daily`
ATR14 olculemez ya da <= 0 ise None doner — ATR14 ise CIKIS formulunde HIC yer almaz (yalnizca
giris stopunu boyutlandirir). Ayrica `read_daily` MIN_DAILY_BARS=210 satir ister; M2'nin cikisi
ise yalnizca 29 satira ihtiyac duyar (close ve close[-28]).

Sonuc: acik bir pozisyonda ATR olculemedigi ya da gunluk satir sayisi 210'un altina dustugu anda
`decide` None donuyordu, `apply_action` bunu "NONE" sayiyordu ve KURAL CIKISI BIR DAHA ASLA
tetiklenemiyordu; pozisyon yalnizca felaket stopu veya likidasyonla kapanabiliyordu. Defter
ozetinde gorunen tek sey {"action": "NONE", "reason": "NO_SIGNAL"} idi — "trend bozulmadi,
tutuyoruz" ile "cikisi olcemiyorum" ayirt EDILEMIYORDU.

Tetikleyici varsayimsal degil: V17 (3b0ae8e) cerceveleri USDS-M perp mumlarina tasidi; genc bir
perp sozlesmesinde kapanmis gunluk satir sayisi 210'un altina duser.

SOZLESME
--------
1. Acik pozisyonda cikis, YALNIZ cikis formulunun okudugu buyukluklerle olculur.
2. Cikis gercekten olculemiyorsa sonuc SESSIZ None degil, ayirt edilebilir EXIT_UNMEASURABLE'dir.
3. Giris yolu DEGISMEDI: giris hala tam `read_daily` (ATR dahil) ister.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.ema200_trend import (  # noqa: E402
    MIN_DAILY_BARS,
    TSMOM_LOOKBACK_DAYS,
    decide,
    exit_measure,
    read_daily,
)

D1 = 24 * 3_600_000


def _rows(n=260, close=100.0, ema=95.0, *, atr=2.0, ref=None, flat=False):
    """Gunluk satirlar. `flat=True`: high==low==close -> ATR 0 (olculemez) ve atr14 sutunu YOK."""
    out = []
    for i in range(n):
        c = close
        if ref is not None and i == n - 1 - TSMOM_LOOKBACK_DAYS:
            c = ref
        row = {"timestamp": i * D1, "open": c, "close": c, "ema200": ema}
        if flat:
            row["high"] = row["low"] = c              # TR = 0 -> ATR = 0 -> read_daily None
        else:
            row["high"], row["low"], row["atr14"] = c + 1, c - 1, atr
        out.append(row)
    return out


def _btc_up():
    return [{"timestamp": 0, "close": 100.0, "ema200": 90.0}]


# --------------------------------------------------------------- on kosul: kusur gercekti
def test_read_daily_still_refuses_when_atr_is_unmeasurable():
    """ON KOSUL: `read_daily` ATR olculemezse hala None doner (giris yolu korunuyor)."""
    assert read_daily(_rows(close=90.0, ema=95.0, flat=True)) is None


# --------------------------------------------------------------- 1) ATR cikisi engelleyemez
def test_t2_exit_fires_even_when_atr_cannot_be_measured():
    """Gunluk kapanis EMA200'un ALTINDA: ATR olculemese bile kural CIKAR.

    Duzeltmeden once bu cagri None donuyordu ve pozisyon sessizce tutuluyordu.
    """
    rows = _rows(close=90.0, ema=95.0, flat=True)          # 90 < 95 -> cikis sarti saglandi
    act = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc_up(), position_open=True)
    assert act is not None, "ATR olculemedi diye cikis bastirilamaz"
    assert act["action"] == "CLOSE" and act["reason"] == "EMA200_CROSS_DOWN"


def test_t2_still_holds_when_trend_is_intact_and_atr_is_unmeasurable():
    """Trend bozulmadiysa cikis YOK — duzeltme cikisi 'her zaman kapat'a cevirmedi."""
    rows = _rows(close=110.0, ema=95.0, flat=True)         # 110 > 95 -> tut
    assert decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc_up(), position_open=True) is None


# --------------------------------------------------------------- 2) M2 210 bar istemez
def test_m2_exit_needs_only_29_bars_not_210():
    """M2 cikisi close ve close[-28] ile olculur; 210 satir sarti bu yola ait DEGILDI."""
    n = TSMOM_LOOKBACK_DAYS + 2                             # 30 satir — 210'un cok altinda
    assert n < MIN_DAILY_BARS
    rows = _rows(n=n, close=90.0, ema=95.0, ref=110.0)      # 90 < 28 gun onceki 110 -> cikis
    act = decide("m2_tsmom28", daily_rows=rows, btc_daily_rows=_btc_up(), position_open=True)
    assert act is not None, "kisa gecmis kural cikisini engelleyemez"
    assert act["action"] == "CLOSE" and act["reason"] == "M2_TSMOM28_CROSS_DOWN"


def test_m2_holds_on_short_history_when_momentum_is_intact():
    rows = _rows(n=TSMOM_LOOKBACK_DAYS + 2, close=120.0, ema=95.0, ref=100.0)
    assert decide("m2_tsmom28", daily_rows=rows, btc_daily_rows=_btc_up(), position_open=True) is None


# --------------------------------------------------------------- 3) olculemeyen cikis GORUNUR
def test_truly_unmeasurable_exit_is_named_not_silent():
    """Cikis gercekten olculemiyorsa sonuc sessiz None DEGIL, adi konmus bir hukumdur."""
    for variant in ("t2_trend_regime", "m2_tsmom28"):
        act = decide(variant, daily_rows=[], btc_daily_rows=_btc_up(), position_open=True)
        assert act is not None, "%s: olculemeyen cikis sessiz gecemez" % variant
        assert act["action"] == "NONE" and act["reason"] == "EXIT_UNMEASURABLE"

    # M2: 28 bardan az -> referans kapanis yok -> olculemez
    short = _rows(n=TSMOM_LOOKBACK_DAYS - 1, close=90.0, ema=95.0)
    act = decide("m2_tsmom28", daily_rows=short, btc_daily_rows=_btc_up(), position_open=True)
    assert act["reason"] == "EXIT_UNMEASURABLE"


def test_exit_measure_reports_the_threshold_each_variant_actually_uses():
    t2 = exit_measure("t2_trend_regime", _rows(close=90.0, ema=95.0, flat=True))
    assert t2 == (90.0, 95.0), "T2 esigi EMA200"
    m2 = exit_measure("m2_tsmom28", _rows(n=40, close=90.0, ema=95.0, ref=110.0))
    assert m2 == (90.0, 110.0), "M2 esigi 28 gun onceki kapanis"


# --------------------------------------------------------------- 4) GIRIS yolu degismedi
def test_entry_path_still_requires_the_full_daily_measure_including_atr():
    """Duzeltme yalnizca CIKISI serbestlestirdi; giris hala ATR ve 210 bar ister."""
    flat = _rows(close=110.0, ema=95.0, flat=True)          # trend yukari ama ATR olculemez
    assert decide("t2_trend_regime", daily_rows=flat, btc_daily_rows=_btc_up(), position_open=False) is None
    short = _rows(n=MIN_DAILY_BARS - 1, close=110.0, ema=95.0)
    assert decide("t2_trend_regime", daily_rows=short, btc_daily_rows=_btc_up(), position_open=False) is None
    ok = _rows(close=110.0, ema=95.0)                       # tam olcu -> giris acilir
    act = decide("t2_trend_regime", daily_rows=ok, btc_daily_rows=_btc_up(), position_open=False)
    assert act is not None and act["action"] == "OPEN" and act["leverage"] == 1
