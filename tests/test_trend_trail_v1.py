# -*- coding: utf-8 -*-
"""MFE ESIGIYLE SILAHLANAN GEVSEK TRAIL (2026-09-20).

NEDEN
-----
T2/M2'de stop acilista bir kez kurulup BIR DAHA tasinmiyor ve hedef yok
(`decide` -> "targets": []). Olculdu: kural cikisi zirvenin ~%64'unu geri veriyor.

Neden basa-bas ya da kismi kar DEGIL — ikisi de OLCULDU ve DUSTU:
  * basa-bas stop: getiri 3-3 yazi tura, maks dusus 6/6'da KOTULESTI.
  * kismi kar (tp1): iyimser sinirda bile net R %40-53 duser (189,3R -> 89-114R),
    cunku karin tamamini tasiyan islemler tam da hedefi goren islemlerdir.
Ikisi de kuyrugu kesiyordu. Kar bu defterlerde en iyi 3 isleme yogun oldugu icin
kuyrugu kesen her seyin bedeli buyuk.

Bu trail kuyrugu KESMEZ: ancak en yuksek kar `trail_arm_r` esigini astiktan SONRA
silahlanir. Olculdu: A=3R/D=1R 620 islemin yalniz 67'sine dokunur; digerleri
(553 islem) hic 3R MFE gormedigi icin BIT-AYNI kalir.

NEDEN KURALIN ICINDE, DEFTERIN TRAILING'INDE DEGIL
--------------------------------------------------
`futures_ledger` trail'i AYNI tikin `best`inden turetip AYNI tikin `worst`u ile test
eder (bar ici "once yuksek sonra dusuk" varsayimi). Ustelik canli defter 1h bar uclari +
mark ile, replay 4h barla tikler — ayni kural iki motorda FARKLI cikis uretirdi. Trail
KAPANMIS GUNLUK bardan hesaplanirsa iki motor da AYNI satirlari okur.

SOZLESME
--------
1. `trail_arm_r=0, trail_dist_r=0` (varsayilan) -> davranis BIT BIT eskisi gibi.
2. Silahlandiktan sonra zirveden `trail_dist_r` kadar geri verilirse CLOSE/TRAIL_GIVEBACK.
3. MFE esigin ALTINDAYSA trail HIC dokunmaz — kuyruk kesilmez.
4. BAR PROVENANSI: yalnizca pozisyon acildiktan SONRA acilmis barlarin uclari sayilir.
5. Olculemezse (initial_stop / acilis ani yok) trail ATLANIR, kural cikisi normal isler.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tradingbot.ema200_trend import TrendParams, decide, trail_state  # noqa: E402

D1 = 24 * 3_600_000
ENTRY = 100.0
STOP0 = 90.0                      # R = 10 fiyat birimi


def _rows(n=260, close=100.0, ema=95.0, high=None, atr=2.0, start_ts=0):
    out = []
    for i in range(n):
        h = close + 1 if high is None else high
        out.append({"timestamp": start_ts + i * D1, "open": close, "high": h,
                    "low": close - 1, "close": close, "ema200": ema, "atr14": atr})
    return out


def _btc():
    return [{"timestamp": 0, "close": 100.0, "ema200": 90.0}]


def _pos(opened_ts=0, entry=ENTRY, stop0=STOP0):
    return {"entry_avg": entry, "initial_stop": stop0, "opened_ts": opened_ts}


# ------------------------------------------------------------------ sozlesme 1
def test_trail_is_off_by_default_and_changes_nothing():
    rows = _rows(close=110.0, ema=95.0, high=140.0)         # MFE 4R, geri verme 3R
    a = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True)
    b = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True,
               trail_arm_r=0.0, trail_dist_r=0.0, position=_pos())
    assert a is None and b is None, "trail KAPALIYKEN trend saglamken cikis olmamali"
    assert TrendParams().trail_arm_r == 0.0 and TrendParams().trail_dist_r == 0.0


# ------------------------------------------------------------------ sozlesme 2
def test_an_armed_trail_exits_when_the_giveback_is_reached():
    # zirve 140 -> mfe_r = (140-100)/10 = 4R · kapanis 110 -> cur_r = 1R · geri verme 3R
    rows = _rows(close=110.0, ema=95.0, high=140.0)
    act = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True,
                 trail_arm_r=3.0, trail_dist_r=1.0, position=_pos())
    assert act is not None, "silahlanmis trail geri vermede cikmaliydi"
    assert act["action"] == "CLOSE" and act["reason"] == "TRAIL_GIVEBACK"
    assert act["mfe_r"] == pytest.approx(4.0) and act["cur_r"] == pytest.approx(1.0)


# ------------------------------------------------------------------ sozlesme 3 (KUYRUK)
def test_the_trail_never_touches_a_position_that_has_not_armed():
    """ASIL KORUMA: MFE esigin altindayken trail SUSAR — kuyruk kesilmez."""
    # zirve 120 -> mfe_r = 2R (< 3R esik), kapanis 100 -> geri verme 2R
    rows = _rows(close=100.0, ema=95.0, high=120.0)
    act = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True,
                 trail_arm_r=3.0, trail_dist_r=1.0, position=_pos())
    assert act is None, "MFE esigi asilmadan trail cikis uretti — kuyruk kesiliyor"


def test_an_armed_trail_holds_while_price_stays_near_the_peak():
    # zirve 140 (4R), kapanis 135 -> geri verme 0,5R (< 1R)
    rows = _rows(close=135.0, ema=95.0, high=140.0)
    act = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True,
                 trail_arm_r=3.0, trail_dist_r=1.0, position=_pos())
    assert act is None, "geri verme esigin altindayken tutmaliydi"


# ------------------------------------------------------------------ sozlesme 4
def test_bars_opened_before_entry_do_not_count_toward_the_peak():
    """Giris ONCESI barin ucu MFE'ye giremez — bu projede yasanmis bir kusur."""
    rows = _rows(n=10, close=110.0, ema=95.0, high=100.0, start_ts=0)
    rows[0]["high"] = 500.0                                  # giristen ONCEKI dev fitil
    acilis = rows[1]["timestamp"]                            # pozisyon 2. barda acildi
    st = trail_state(rows, entry=ENTRY, initial_stop=STOP0, opened_ms=acilis)
    assert st is not None
    assert st[0] == pytest.approx(0.0), "giris oncesi fitil MFE'ye sizdi (mfe_r=%s)" % st[0]
    act = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True,
                 trail_arm_r=3.0, trail_dist_r=1.0, position=_pos(opened_ts=acilis))
    assert act is None, "giris oncesi fitille silahlanip cikti"


# ------------------------------------------------------------------ sozlesme 5
def test_an_unmeasurable_trail_is_skipped_not_guessed():
    rows = _rows(close=110.0, ema=95.0, high=140.0)
    for eksik in ({"entry_avg": ENTRY, "initial_stop": None, "opened_ts": 0},
                  {"entry_avg": None, "initial_stop": STOP0, "opened_ts": 0},
                  {"entry_avg": ENTRY, "initial_stop": ENTRY, "opened_ts": 0}):   # risk=0
        act = decide("t2_trend_regime", daily_rows=rows, btc_daily_rows=_btc(), position_open=True,
                     trail_arm_r=3.0, trail_dist_r=1.0, position=eksik)
        assert act is None, "olculemeyen trail cikis UYDURDU: %s" % eksik
    # kural cikisi hala calisir (kapanis EMA200 altinda)
    dus = _rows(close=90.0, ema=95.0, high=91.0)
    act = decide("t2_trend_regime", daily_rows=dus, btc_daily_rows=_btc(), position_open=True,
                 trail_arm_r=3.0, trail_dist_r=1.0, position={"entry_avg": ENTRY, "initial_stop": None, "opened_ts": 0})
    assert act is not None and act["reason"] == "EMA200_CROSS_DOWN"


# ------------------------------------------------------------------ dogrulama
def test_the_two_trail_knobs_must_be_given_together():
    TrendParams(trail_arm_r=3.0, trail_dist_r=1.0).validate()
    TrendParams().validate()
    for kotu in ({"trail_arm_r": 3.0}, {"trail_dist_r": 1.0}, {"trail_arm_r": -1.0, "trail_dist_r": 1.0}):
        with pytest.raises(ValueError):
            TrendParams(**kotu).validate()


def test_the_trail_fires_before_the_rule_exit_but_only_when_armed():
    """Sira: trail ONCE sorulur. Ama silahlanmamissa kural cikisi normal isler."""
    dus = _rows(close=90.0, ema=95.0, high=140.0)            # hem 4R zirve hem EMA200 alti
    act = decide("t2_trend_regime", daily_rows=dus, btc_daily_rows=_btc(), position_open=True,
                 trail_arm_r=3.0, trail_dist_r=1.0, position=_pos())
    assert act["reason"] == "TRAIL_GIVEBACK", "silahliyken trail once gelmeli"
    act2 = decide("t2_trend_regime", daily_rows=dus, btc_daily_rows=_btc(), position_open=True,
                  trail_arm_r=99.0, trail_dist_r=1.0, position=_pos())
    assert act2["reason"] == "EMA200_CROSS_DOWN", "silahlanmamisken kural cikisi gelmeli"
