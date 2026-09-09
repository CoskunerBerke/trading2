"""BAR PROVENANSI V1 — pozisyon acilmadan ONCE kapanmis bir bar, pozisyonun MAE/MFE'sine
yazilamaz ve stop/hedef tetikleyemez.

Olculmus uretim kusuru (2026-09-09, dondurulmus defter anlik goruntusu): `engine_v3._marks()`
cerceveden SON KAPANMIS 1h barin uclarini tasiyordu. Pozisyon 16:24'te acildiysa, sonraki turda
son kapanmis bar 15:00-16:00'dir ve uclari pozisyon HENUZ YOKKEN olusmustur. Kapanmis 29 islemin
5'inde kayitli `mfe_pct`, pozisyon omru boyunca hicbir gercek 1m barin ulasmadigi bir degerdi ve
her birinde deger, giristen ONCE kapanmis bir 1h barin ucuyle kurusuna kadar esitti:

    F00015 STX   kayitli 1.57 / gercek -0.04   (giristen 5.1 sa once kapanan bar)
    F00022 SUI   kayitli 2.01 / gercek  0.97   (23:00 bari, giris 00:30)
    F00025 SPCX  kayitli 1.31 / gercek  0.27   (15:00 bari, giris 16:34)
    F00034 NVDA  kayitli 1.13 / gercek  0.73   (14:00 bari, giris 15:00)
    F00020 AAPL  kayitli 0.44 / gercek  0.35   (15:00 bari high 322.48, giris 16:24 @ 321.08)

Canli `breakeven_at_mfe_r` kurali tam bu alani okur; sisirilmis MFE stop'u ERKEN basa-basa tasir.
Ayni uclar `tick()` icinde stop/hedef tetiginde de kullaniliyordu, yani olmamis bir fiyattan cikis
mumkundu.
"""
from __future__ import annotations

import inspect
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_accounting as TA  # noqa: E402

from tradingbot.accounting import AmountType, SizeSpec, TickData  # noqa: E402

D = Decimal
ETH, T0, _f = TA.ETH, TA.T0, TA._f
HOUR = timedelta(hours=1)


def _iso(dt) -> str:
    return dt.isoformat()


def _open_long(led, *, entry=3000, stop=2900, targets=(3300, 3500)):
    return led.open(ETH, "LONG", entry, SizeSpec(48, AmountType.NOTIONAL, 2), stop=stop,
                    targets=list(targets), filters=_f(), now=T0)


# --------------------------------------------------------------------------- 1) MAE/MFE
def test_01_pre_entry_bar_extremes_never_reach_mfe():
    """Giristen ONCE kapanmis barin ucu MFE'ye yazilmaz; yalniz mark degerlendirilir."""
    led = TA._led()
    pos = _open_long(led)
    pre = TickData(last=3000, high=3300, low=2700, bar_open=_iso(T0 - HOUR))   # bar 09:00-10:00
    led.tick({ETH: pre}, now_utc=T0 + timedelta(minutes=10))
    assert pos.mfe_pct == 0 and pos.mae_pct == 0
    assert led.bar_extremes_skipped == 1


def test_02_in_life_bar_extremes_are_used():
    """AYNI uclar, provenansi pozisyonun icindeyse KULLANILIR — guard blanket bir devre disi degil."""
    led = TA._led()
    pos = _open_long(led)
    inlife = TickData(last=3000, high=3150, low=2950, bar_open=_iso(T0 + HOUR))
    led.tick({ETH: inlife}, now_utc=T0 + timedelta(hours=2))
    assert pos.mfe_pct == D(5) and pos.mae_pct < 0
    assert led.bar_extremes_skipped == 0


def test_03_production_case_aapl_f00020():
    """Gercek kayit: giris 321.08 @ 16:24; 15:00 barinin high'i 322.48 (+0.44%). MFE +0.44 OLMAMALI.

    Pozisyon omru icinde fiyat yalnizca 322.20'ye (+0.35%) ulasti; kayitli deger 0.44'tu.
    """
    led = TA._led()
    opened = T0 + timedelta(minutes=24)                                     # 10:24
    pos = led.open(ETH, "LONG", "321.08", SizeSpec(48, AmountType.NOTIONAL, 2), stop="315",
                   targets=["340"], filters=_f(), now=opened)
    stale = TickData(last="321.50", high="322.48", low="320.00", bar_open=_iso(T0 - HOUR))
    led.tick({ETH: stale}, now_utc=opened + timedelta(minutes=10))
    assert pos.mfe_pct < D("0.44")
    real = TickData(last="322.00", high="322.20", low="321.00",
                    bar_open=_iso(opened + timedelta(minutes=36)))
    led.tick({ETH: real}, now_utc=opened + timedelta(hours=2))
    assert D("0.34") < pos.mfe_pct < D("0.36")                              # gercek tepe, sisirilmis olan degil


# --------------------------------------------------------------------------- 2) tetikler
def test_04_pre_entry_low_does_not_trigger_stop():
    led = TA._led()
    pos = _open_long(led, stop=2900)
    pre = TickData(last=3000, high=3010, low=2800, bar_open=_iso(T0 - HOUR))  # stop'un ALTINDA bir dip
    closed = led.tick({ETH: pre}, now_utc=T0 + timedelta(minutes=10))
    assert closed == [] and pos.qty > 0 and ETH in led.positions


def test_05_pre_entry_high_does_not_trigger_target():
    led = TA._led()
    pos = _open_long(led, targets=(3300,))
    pre = TickData(last=3000, high=3400, low=2990, bar_open=_iso(T0 - HOUR))
    closed = led.tick({ETH: pre}, now_utc=T0 + timedelta(minutes=10))
    assert closed == [] and pos.targets_hit == 0 and pos.qty > 0


def test_06_same_bar_triggers_once_provenance_is_inside_the_life():
    """Ayni uclar, yalniz `bar_open` degisti -> sonuc DEGISIR. Differential kanit."""
    led = TA._led()
    _open_long(led, targets=(3300,))
    inlife = TickData(last=3000, high=3400, low=2990, bar_open=_iso(T0 + HOUR))
    closed = led.tick({ETH: inlife}, now_utc=T0 + timedelta(hours=2))
    assert closed and closed[0].exit_reason


# --------------------------------------------------------------------------- 3) fail-closed
def test_07_missing_provenance_is_fail_closed():
    """Provenans yoksa uclar KULLANILMAZ. Bilinmeyen bar 'guvenli' sayilmaz."""
    led = TA._led()
    pos = _open_long(led)
    led.tick({ETH: TickData(last=3000, high=3300, low=2700)}, now_utc=T0 + HOUR)
    assert pos.mfe_pct == 0 and pos.mae_pct == 0 and led.bar_extremes_skipped == 1


def test_08_unparseable_provenance_is_fail_closed():
    led = TA._led()
    pos = _open_long(led)
    led.tick({ETH: TickData(last=3000, high=3300, low=2700, bar_open="not-a-timestamp")},
             now_utc=T0 + HOUR)
    assert pos.mfe_pct == 0 and led.bar_extremes_skipped == 1


# --------------------------------------------------------------------------- 4) basa-bas kurali
def test_09_breakeven_cannot_fire_on_a_pre_entry_extreme():
    """`breakeven_at_mfe_r` acikken bile giris oncesi bir uc stop'u basa-basa TASIYAMAZ."""
    led = TA._led(breakeven_at_mfe_r=D("1.0"))
    pos = _open_long(led)                                           # risk %3.333 = 1R
    led.tick({ETH: TickData(last=3000, high=3200, bar_open=_iso(T0 - HOUR))},
             now_utc=T0 + timedelta(minutes=10))
    assert pos.stop == D("2900") and not pos.meta.get("be_by_mfe")
    led.tick({ETH: TickData(last=3090, high=3101, bar_open=_iso(T0 + HOUR))},
             now_utc=T0 + timedelta(hours=2))
    assert pos.meta.get("be_by_mfe") and pos.stop > D("3000")       # gercek 1R'de tetikler


# --------------------------------------------------------------------------- 5) sozlesme
def test_10_every_extreme_producing_site_declares_provenance():
    """Uclarla TickData ureten her URETIM cagrisi `bar_open` vermek ZORUNDA.

    Bu test olmadan yeni bir cagri yeri sessizce provenanssiz uc uretebilir; fail-closed sayesinde
    zarar vermez ama uclar sessizce YOK SAYILIR ve tetikler bozulur. Ikisi de istenmez.
    """
    from tradingbot import engine_v3
    from tradingbot.ops import gap
    from tradingbot.replay import engine as replay_engine
    for mod in (engine_v3, gap, replay_engine):
        src = inspect.getsource(mod)
        for chunk in src.split("TickData(")[1:]:
            head = chunk[:400]
            if "high=" in head or "low=" in head:
                assert "bar_open=" in head, f"{mod.__name__}: uclu TickData provenanssiz uretiliyor"


def test_11_ledger_consults_provenance_before_using_extremes():
    """Guard `tick()` icinde MAE/MFE ve tetiklerden ONCE calisir."""
    from tradingbot.accounting import futures_ledger
    src = inspect.getsource(futures_ledger.FuturesLedgerV2.tick)
    i_guard = src.index("extremes_usable_from")
    assert i_guard < src.index("pos.mae_pct = min"), "provenans kapisi MAE/MFE'den sonra calisiyor"
    assert i_guard < src.index("stop_hit ="), "provenans kapisi stop tetiginden sonra calisiyor"


def test_12_marks_attaches_bar_open_from_frame():
    """`_frame_bar_open` hem `timestamp` sutunundan hem zaman indeksinden okur; cozemezse bos."""
    import pandas as pd

    from tradingbot.engine_v3 import TradingEngineV3
    ms = 1788912000000
    by_col = pd.DataFrame({"timestamp": [ms - 3600000, ms], "high": [1.0, 2.0], "low": [0.5, 1.5]})
    got = TradingEngineV3._frame_bar_open(by_col)
    assert got.startswith("2026-") and got.endswith("+00:00")
    idx = pd.DataFrame({"high": [1.0, 2.0]}, index=pd.to_datetime([ms - 3600000, ms], unit="ms", utc=True))
    assert TradingEngineV3._frame_bar_open(idx).endswith("+00:00")
    assert TradingEngineV3._frame_bar_open(pd.DataFrame({"high": [1.0]})) == ""


def test_13_real_frame_pipeline_yields_provenance():
    """GERCEK cerceve boru hatti (`prepare` -> `drop_unclosed_last_bar` -> gostergeler) provenans verir.

    `_frame_bar_open` bos donerse defter fail-closed davranir ve BUTUN bar uclari sessizce
    yok sayilir — stop/hedef tetikleri yalniz mark ile calisir. Bu, onarimin degil bir
    GERILEMENIN belirtisi olurdu; bu test o yolu kilitler.
    """
    import pandas as pd

    from tradingbot.data import drop_unclosed_last_bar, prepare
    from tradingbot.engine_v3 import TradingEngineV3
    from tradingbot.indicators import add_snapshot_indicators

    base = 1_788_000_000_000
    raw = pd.DataFrame({
        "timestamp": [base + i * 3_600_000 for i in range(300)],
        "open": [100.0 + i * 0.1 for i in range(300)],
        "high": [101.0 + i * 0.1 for i in range(300)],
        "low": [99.0 + i * 0.1 for i in range(300)],
        "close": [100.5 + i * 0.1 for i in range(300)],
        "volume": [10.0] * 300,
    })
    f = prepare(raw)
    f = drop_unclosed_last_bar(f, "1h", now_ms=base + 300 * 3_600_000)
    f = add_snapshot_indicators(f)
    got = TradingEngineV3._frame_bar_open(f)
    assert got, "gercek boru hattinda provenans cozulemedi -> butun uclar sessizce dusurulur"
    assert got == _iso(pd.Timestamp(f.index[-1]).to_pydatetime())
