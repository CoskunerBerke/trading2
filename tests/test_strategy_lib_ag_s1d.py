# -*- coding: utf-8 -*-
"""S1d — `LIB_AG_V1` aileleri, örtüleri, New York saati ve çekirdeğin tek-bar limit eki (docs/ENGINE_BRAIN_V1.md §8.6 S1d).

Kabul: saat zoneinfo ile aynı (yaz saati geçişleri dahil); her ailenin altın sentetik yolu (tetik doğru barda ateşler, önce ve
sonra ateşlemez; stop/hedef/çıkış mühürlü sayılarla); yaz saati geçiş günü; ileriye bakmama özellik testi (gelecek barları
silmek ya da bozmak karar anındaki niyeti değiştirmez); önbellek şeffaflığı; örtüler (KZ, NOREACT, CAL); çekirdeğin limit
eki (dokunmak dolum değildir, ≥ 1 tik içinden geçiş seviyeden dolar; stop yüzdesi ve hedef yönü kapıları); LIB_V1 kataloğu
değişmedi; paket motoru import etmez.
"""
from __future__ import annotations

import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradingbot.research_engine import kernel as KN
from tradingbot.research_engine import lib_ag as A
from tradingbot.research_engine import seal_s1d as S1D
from tradingbot.research_engine import synthetic as SY
from tradingbot.strategy_lib import ag_clock as C
from tradingbot.strategy_lib import catalog as SLC
from tradingbot.strategy_lib.ag_common import AgEntryRef
from tradingbot.strategy_lib.ag_overlay import AgOverlay
from tradingbot.strategy_lib.contract import (ACT_CLOSE, LONG, NONE, SHORT, TARGET_PRICE, TARGET_R_FROM_ENTRY, Intent, PosView,
                                              Target, Variant, close)

zoneinfo = pytest.importorskip("zoneinfo")
try:
    NY = zoneinfo.ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - tz veritabanı yok
    pytest.skip("America/New_York tz verisi yok", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
M5, M15, H1, H4, D1 = 300_000, 900_000, 3_600_000, 14_400_000, 86_400_000
EPS = 0.01
TICK = 0.001


def ny_ms(day: str, hh: int, mm: int) -> int:
    y, m, d = (int(x) for x in day.split("-"))
    return int(datetime(y, m, d, hh, mm, tzinfo=NY).timestamp() * 1000)


def shift(day: str, k: int) -> str:
    return (datetime.fromisoformat(day) + timedelta(days=k)).date().isoformat()


class Path5:
    """5m barları: varsayılan düz (o = c = fiyat, ± EPS); New York saatine göre bar yazılır."""

    def __init__(self, start_ms: int, end_ms: int, px: float):
        self.t0, self.t1 = start_ms - start_ms % M5, end_ms - end_ms % M5
        self.rows = {t: self._flat(t, px) for t in range(self.t0, self.t1, M5)}

    @staticmethod
    def _flat(t: int, px: float) -> dict:
        return {"timestamp": t, "open": px, "high": px + EPS, "low": px - EPS, "close": px, "volume": 1.0}

    def flat(self, a: int, b: int, px: float) -> "Path5":
        for t in range(a - a % M5, b, M5):
            self.rows[t] = self._flat(t, px)
        return self

    def bar(self, t: int, o: float, h: float, lo: float, c: float) -> "Path5":
        assert lo <= min(o, c) and h >= max(o, c), (t, o, h, lo, c)
        self.rows[t] = {"timestamp": t, "open": o, "high": h, "low": lo, "close": c, "volume": 1.0}
        return self

    def data(self, symbol: str = "SYNAG") -> KN.SeriesData:
        m5 = [self.rows[t] for t in sorted(self.rows)]
        bars = {"5m": m5, "15m": SY.aggregate(m5, M15), "1h": SY.aggregate(m5, H1), "4h": SY.aggregate(m5, H4),
                "1d": SY.aggregate(m5, D1)}
        fr = SY.funding_rows(m5, M5, seed=3)
        return KN.SeriesData(symbol=symbol, bars=bars, funding=fr, price_tick=TICK, markpx_1h=SY.markpx_from_funding(fr))


def sig(vid: str, data: KN.SeriesData, t: int, history=()) -> list[Intent]:
    return KN.signals_at(A.make_variant(vid), data, t, history)


# ============================================================================ saat
def test_ny_clock_equals_zoneinfo_including_dst_transition_days():
    t0, t1 = ny_ms("2019-01-01", 0, 0), ny_ms("2031-01-01", 0, 0)
    for t in range(t0, t1, H1):
        d = datetime.fromtimestamp(t / 1000, NY)
        assert (C.ny_minute(t), C.ny_date_iso(t)) == (d.hour * 60 + d.minute, d.date().isoformat()), t
    for y in range(2019, 2031):
        s, e = C.dst_bounds_utc(y)
        for t in list(range(s - D1, s + D1, M5)) + list(range(e - D1, e + D1, M5)):
            d = datetime.fromtimestamp(t / 1000, NY)
            assert C.ny_minute(t) == d.hour * 60 + d.minute and C.ny_date_iso(t) == d.date().isoformat(), t
    # geçiş anları: 2025-03-09 02:00 EST → 03:00 EDT (07:00 UTC), 2025-11-02 02:00 EDT → 01:00 EST (06:00 UTC)
    assert C.dst_bounds_utc(2025) == (int(datetime(2025, 3, 9, 7, tzinfo=timezone.utc).timestamp() * 1000),
                                      int(datetime(2025, 11, 2, 6, tzinfo=timezone.utc).timestamp() * 1000))
    day = C.day_of_iso("2025-03-09")
    assert C.ny_to_utc(day, 120) is None and C.ny_to_utc(day, 570) == ny_ms("2025-03-09", 9, 30)
    assert C.ny_to_utc(C.day_of_iso("2025-11-02"), 570) == ny_ms("2025-11-02", 9, 30)
    with pytest.raises(ValueError):
        C.ny_offset_ms(int(datetime(2006, 6, 1, tzinfo=timezone.utc).timestamp() * 1000))


def test_sessions_and_kill_zones_follow_new_york_time():
    _s, kz = C.parse_sessions(S1D.SESSIONS_MIN, S1D.KZ)
    for day in ("2025-01-15", "2025-07-15", "2025-03-09", "2025-11-02"):
        assert C.in_kz(ny_ms(day, 9, 30), kz) and C.in_kz(ny_ms(day, 10, 55), kz)
        assert not C.in_kz(ny_ms(day, 11, 0), kz) and not C.in_kz(ny_ms(day, 11, 55), kz)
        assert C.in_kz(ny_ms(day, 12, 0), kz) and not C.in_kz(ny_ms(day, 13, 0), kz) and C.in_kz(ny_ms(day, 13, 30), kz)
        assert not C.in_kz(ny_ms(day, 16, 0), kz) and C.in_kz(ny_ms(day, 4, 55), kz) and not C.in_kz(ny_ms(day, 5, 0), kz)
        assert not C.in_kz(ny_ms(day, 20, 0), kz)                       # ASIA öldürme bölgesi değil
    # 1d barı 00:00 UTC'de kapanır = New York 19:00/20:00 → hiçbir zaman KZ (T2/M2 × KZ yapısal olarak boş)
    for d in range(0, 730):
        assert not C.in_kz(ny_ms("2024-01-01", 0, 0) // D1 * D1 + D1 * d, kz)


# ============================================================================ katalog
def test_catalog_ag_ids_equal_the_seal_and_lib_v1_catalog_is_unchanged():
    assert list(A.variant_ids()) == list(S1D.LIB_AG_V1["variant_order"]) and set(SLC.AG_IMPLEMENTED) == set(A.variant_ids())
    assert len(SLC.IMPLEMENTED) == 93 and not set(SLC.IMPLEMENTED) & set(SLC.AG_IMPLEMENTED)
    for vid in A.variant_ids():
        v = A.make_variant(vid)
        assert v.variant_id == vid and SLC.AG_FAMILY_OF[vid] == A.variant(vid)["family"]
        assert v.signal_tf == A.variant(vid)["signal_tf"] and list(v.timeframes) == list(A.variant(vid)["timeframes"])
    with pytest.raises(ValueError):
        SLC.make_ag("AG_RB_DIRECT", {**dict(A.variant("AG_RB_DIRECT")["params"]), "extra": 1}, A.variant_spec("AG_RB_DIRECT"))


# ============================================================================ P1 LUNCH_SWEEP
def lunch_day(day: str, *, prior_days: int = 2, base: float = 100.0) -> Path5:
    p = Path5(ny_ms(shift(day, -prior_days), 0, 0), ny_ms(shift(day, 1), 0, 0), base)
    p.bar(ny_ms(day, 10, 0), base, base + 0.5, base - EPS, base)                  # NYAM_H 100,5
    p.bar(ny_ms(day, 11, 0), base, base + EPS, base - 0.5, base)                  # NYAM_L 99,5
    p.bar(ny_ms(day, 12, 20), base, base + 0.7, base - EPS, base + 0.3)           # NYAM_H süpürüldü, altında kapandı
    p.flat(ny_ms(day, 12, 25), ny_ms(shift(day, 1), 0, 0), base + 0.3)
    return p


def test_p1_lunch_sweep_fires_on_the_sweep_bar_only_and_exits_at_1600():
    day = "2025-03-12"
    data = lunch_day(day).data()
    assert sig("AG_LS_NONE_NONE", data, ny_ms(day, 12, 20)) == []
    (it,) = sig("AG_LS_NONE_NONE", data, ny_ms(day, 12, 25))
    assert it.side == SHORT and it.signal_ts == ny_ms(day, 12, 20)
    assert it.stop == pytest.approx(100.7 * 1.0005) and it.targets == (Target(TARGET_PRICE, 99.5),)
    assert it.entry_ref.min_rr_after_cost == 1.0 and it.entry_ref.stop_pct_min == 0.0008 and it.entry_ref.stop_pct_max == 0.012
    assert sig("AG_LS_NONE_NONE", data, ny_ms(day, 12, 30)) == [] and sig("AG_LS_NONE_NONE", data, ny_ms(day, 11, 5)) == []
    res, _ = A.run_variants(["AG_LS_NONE_NONE"], data)
    (t,) = [x for x in res["AG_LS_NONE_NONE"].trades if x.signal_ts == ny_ms(day, 12, 20)]
    assert t.entry_ts == ny_ms(day, 12, 25) and t.exit_reason == "TIME_EXIT_NY" and t.exit_ts == ny_ms(day, 16, 0)
    # tetik penceresi: açılışı 13:30 olan bar tetik değildir; 12:00 barı ilk tetiktir
    p = lunch_day(day)
    p.flat(ny_ms(day, 12, 20), ny_ms(day, 12, 25), 100.0).bar(ny_ms(day, 13, 30), 100.0, 100.7, 99.99, 100.3)
    assert all(sig("AG_LS_NONE_NONE", p.data(), ny_ms(day, h, m)) == [] for h, m in ((12, 25), (13, 35)))
    p = lunch_day(day)
    p.flat(ny_ms(day, 12, 20), ny_ms(day, 12, 25), 100.0).bar(ny_ms(day, 12, 0), 100.0, 100.7, 99.99, 100.3)
    assert [i.signal_ts for i in sig("AG_LS_NONE_NONE", p.data(), ny_ms(day, 12, 5))] == [ny_ms(day, 12, 0)]


@pytest.mark.parametrize("day,utc_hour", [("2025-03-07", 17), ("2025-03-09", 16), ("2025-03-10", 16), ("2025-11-01", 16),
                                          ("2025-11-02", 17), ("2025-07-04", 16)])
def test_p1_dst_transition_days_use_new_york_time(day, utc_hour):
    """Aynı New York saatli desen: kış (EST, UTC−5) ve yaz (EDT, UTC−4) günlerinde ve iki geçiş Pazarında tetik 12:20 New
    York'tadır (UTC saati kayar); New York saatine göre bir saat önceki aynı desen (NYAM içinde) tetik değildir."""
    data = lunch_day(day).data()
    (it,) = sig("AG_LS_NONE_NONE", data, ny_ms(day, 12, 25))
    assert datetime.fromtimestamp(it.signal_ts / 1000, timezone.utc).hour == utc_hour
    p = lunch_day(day)
    p.flat(ny_ms(day, 12, 20), ny_ms(day, 12, 25), 100.0).bar(ny_ms(day, 11, 20), 100.0, 100.7, 99.99, 100.3)
    assert sig("AG_LS_NONE_NONE", p.data(), ny_ms(day, 11, 25)) == []


def test_p1_htf_bias_and_quiet_accumulation_filters():
    day = "2025-03-12"
    # yükselen 4h trend (EMA50 kapanışın altında) → kısa süpürme HTF varyantında engellenir
    p = lunch_day(day, prior_days=12)
    t, px = ny_ms(shift(day, -12), 0, 0), 90.0
    while t < ny_ms(day, 0, 0):
        p.flat(t, t + M5, px)
        px += 10.0 / (12 * 288)
        t += M5
    p.flat(ny_ms(day, 0, 0), ny_ms(day, 10, 0), 100.0)
    data = p.data()
    assert len(sig("AG_LS_NONE_NONE", data, ny_ms(day, 12, 25))) == 1 and sig("AG_LS_HTF_NONE", data, ny_ms(day, 12, 25)) == []
    # sakin birikim: önceki 20 günün NYAM aralığı 2,0 > bugünkü 1,0 → QUIET ateşler; 0,5 → ateşlemez
    for prev_range, fires in ((2.0, True), (0.5, False)):
        p = lunch_day(day, prior_days=21)
        for k in range(1, 21):
            d = shift(day, -k)
            p.bar(ny_ms(d, 10, 0), 100.0, 100.0 + prev_range / 2, 99.99, 100.0)
            p.bar(ny_ms(d, 11, 0), 100.0, 100.01, 100.0 - prev_range / 2, 100.0)
        got = sig("AG_LS_NONE_QUIET", p.data(), ny_ms(day, 12, 25))
        assert (len(got) == 1) is fires, prev_range
    p = lunch_day(day, prior_days=21)                                             # 20 günden biri eksik → ölçülemez
    for t in range(ny_ms(shift(day, -5), 9, 30), ny_ms(shift(day, -5), 9, 45), M5):
        del p.rows[t]
    assert sig("AG_LS_NONE_QUIET", p.data(), ny_ms(day, 12, 25)) == []


# ============================================================================ P2 TENM
def tenm_b_day(day: str) -> Path5:
    p = Path5(ny_ms(shift(day, -1), 0, 0), ny_ms(shift(day, 1), 0, 0), 100.0)
    p.bar(ny_ms(day, 10, 0), 100.0, 101.5, 99.85, 101.5)                          # L = 100; gövde 1,5; fitil %9 → fitilsiz
    p.flat(ny_ms(day, 10, 5), ny_ms(day, 10, 30), 101.5)
    p.bar(ny_ms(day, 10, 30), 101.5, 101.51, 99.95, 100.2)                        # ilk geri dönüş, L'yi 1 tikten fazla geçer
    p.flat(ny_ms(day, 10, 35), ny_ms(day, 11, 5), 100.2)
    p.bar(ny_ms(day, 11, 5), 100.2, 100.7, 100.19, 100.65)
    p.flat(ny_ms(day, 11, 10), ny_ms(shift(day, 1), 0, 0), 100.65)
    return p


def test_p2_tenm_mode_b_break_and_retest_limit_fills_at_the_line():
    day = "2025-03-12"
    data = tenm_b_day(day).data()
    assert sig("AG_TENM_B_WL", data, ny_ms(day, 10, 0)) == []                    # 10:00 öncesi yalnız gözlem
    (it,) = sig("AG_TENM_B_WL", data, ny_ms(day, 10, 5))
    assert it.side == LONG and it.entry_ref.limit_price == 100.0 and it.stop == pytest.approx(99.85 * 0.9995)
    assert it.targets == (Target(TARGET_R_FROM_ENTRY, 3.0),) and it.signal_ts == ny_ms(day, 10, 0)
    assert sig("AG_TENM_B_WL", data, ny_ms(day, 10, 35)) == []                   # ilk dönüş kullanıldı
    res, _ = A.run_variants(["AG_TENM_B_WL", "AG_TENM_B_ANY", "AG_TENM_A_ANY"], data)
    (t,) = res["AG_TENM_B_WL"].trades
    assert t.entry_ts == ny_ms(day, 10, 30) and t.entry_ref == 100.0 and t.exit_reason == "TARGET1"
    assert t.legs[-1].ref == pytest.approx(100.0 + 3 * (100.0 - 99.85 * 0.9995)) and t.exit_ts == ny_ms(day, 11, 10)
    assert res["AG_TENM_B_WL"].rejected.get(KN.REJ_LIMIT_NOT_FILLED) == 5        # 10:05–10:25 barları dokunmadı
    assert len(res["AG_TENM_B_ANY"].trades) == 1 and res["AG_TENM_A_ANY"].trades == []
    # fitilli 10:00 barı (açılış yanı fitili %10'dan büyük) → WL varyantı yok, ANY var
    p = tenm_b_day(day).bar(ny_ms(day, 10, 0), 100.0, 101.5, 99.6, 101.5)
    assert sig("AG_TENM_B_WL", p.data(), ny_ms(day, 10, 5)) == [] and len(sig("AG_TENM_B_ANY", p.data(), ny_ms(day, 10, 5))) == 1


def test_p2_tenm_day_is_dead_after_three_line_crosses():
    day = "2025-03-12"
    for closes, alive in (((100.05, 99.95, 100.05, 99.95), False), ((100.05, 99.95, 99.9, 99.9), True)):
        p = Path5(ny_ms(shift(day, -1), 0, 0), ny_ms(shift(day, 1), 0, 0), 100.0)
        prev = 100.0
        for k, c in enumerate(closes):
            p.bar(ny_ms(day, 10, 5 * k), prev, max(prev, c) + EPS, min(prev, c) - EPS, c)
            prev = c
        p.bar(ny_ms(day, 10, 20), prev, 101.5, prev - EPS, 101.5)
        p.flat(ny_ms(day, 10, 25), ny_ms(shift(day, 1), 0, 0), 101.5)
        assert (len(sig("AG_TENM_B_ANY", p.data(), ny_ms(day, 10, 25))) == 1) is alive


def test_p2_tenm_mode_a_reversal_to_line_after_fvg_tap():
    day = "2025-03-12"
    p = Path5(ny_ms(shift(day, -1), 0, 0), ny_ms(shift(day, 1), 0, 0), 99.6)
    p.bar(ny_ms(day, 8, 5), 99.6, 100.0, 99.59, 99.95)                            # FVG (99,61; 99,80) ortadaki bar
    p.bar(ny_ms(day, 8, 10), 99.95, 100.5, 99.8, 100.5)
    p.flat(ny_ms(day, 8, 15), ny_ms(day, 10, 0), 100.5)
    p.bar(ny_ms(day, 10, 0), 100.5, 100.5, 100.4, 100.42)                         # L = 100,5 (düşen, fitilsiz)
    p.bar(ny_ms(day, 10, 5), 100.42, 100.43, 99.95, 100.0)
    p.bar(ny_ms(day, 10, 10), 100.0, 100.01, 99.75, 99.78)                        # FVG'ye ilk dokunuş
    p.bar(ny_ms(day, 10, 15), 99.78, 99.9, 99.77, 99.88)                          # FVG üstüne kapanış → L'ye doğru
    p.flat(ny_ms(day, 10, 20), ny_ms(shift(day, 1), 0, 0), 99.88)
    data = p.data()
    assert sig("AG_TENM_A_WL", data, ny_ms(day, 10, 15)) == []
    (it,) = sig("AG_TENM_A_WL", data, ny_ms(day, 10, 20))
    assert it.side == LONG and it.targets == (Target(TARGET_PRICE, 100.5),) and it.stop == pytest.approx(99.75 * 0.9995)
    assert it.entry_ref.min_rr_after_cost == 1.5 and it.entry_ref.target_beyond_entry
    assert sig("AG_TENM_A_WL", data, ny_ms(day, 10, 25)) == []
    res, _ = A.run_variants(["AG_TENM_A_WL"], data)
    (t,) = res["AG_TENM_A_WL"].trades
    assert t.entry_ts == ny_ms(day, 10, 20) and t.entry_ref == 99.88
    # 10:05'ten önce dokunulmuş FVG (azaltılmış) kurulum değildir
    p.bar(ny_ms(day, 9, 50), 100.5, 100.51, 99.79, 100.5)
    assert sig("AG_TENM_A_WL", p.data(), ny_ms(day, 10, 20)) == []


# ============================================================================ P3 MSS_FVG
def mss_day(day: str, hh: int, mm: int) -> tuple[Path5, int]:
    base = ny_ms(day, hh, mm)
    p = Path5(base - 2 * D1, base + D1, 100.0)
    p.bar(base - 30 * 60_000, 100.0, 100.3, 99.99, 100.0)                         # salınım tepesi
    p.bar(base - 15 * 60_000, 100.0, 100.01, 99.7, 100.0)                         # salınım dibi (stop)
    p.bar(base, 100.0, 100.65, 99.99, 100.6)                                      # yer değiştirme, MSS
    p.bar(base + M5, 100.6, 100.7, 100.4, 100.6)                                  # FVG (100,01; 100,40)
    p.flat(base + 2 * M5, base + 5 * M5, 100.6)
    p.bar(base + 5 * M5, 100.6, 100.61, 100.35, 100.5)                            # limit 100,40 içinden geçilir
    p.flat(base + 6 * M5, base + D1, 100.5)
    return p, base


def test_p3_mss_fvg_limit_at_fvg_edge_valid_until_touched_kz_vs_all():
    day = "2025-03-12"
    p, i = mss_day(day, 10, 0)
    data = p.data()
    assert sig("AG_MSS_5m_2R_KZ", data, i + M5) == []                            # FVG henüz tamamlanmadı
    for t in (i + 2 * M5, i + 5 * M5):
        (it,) = sig("AG_MSS_5m_2R_KZ", data, t)
        assert it.side == LONG and it.entry_ref.limit_price == 100.4 and it.stop == pytest.approx(99.7 * 0.9995)
        assert it.signal_ts == i and it.targets == (Target(TARGET_R_FROM_ENTRY, 2.0),)
    assert sig("AG_MSS_5m_2R_KZ", data, i + 6 * M5) == []                        # dokunuldu: tüketildi
    res, _ = A.run_variants(["AG_MSS_5m_2R_KZ", "AG_MSS_5m_3R_ALL", "AG_MSS_15m_2R_ALL"], data)
    (t,) = res["AG_MSS_5m_2R_KZ"].trades
    assert t.entry_ts == i + 5 * M5 and t.entry_ref == 100.4 and t.targets == (pytest.approx(100.4 + 2 * (100.4 - 99.7 * 0.9995)),)
    assert len(res["AG_MSS_5m_3R_ALL"].trades) == 1 and res["AG_MSS_15m_2R_ALL"].trades == []
    p, i = mss_day(day, 11, 30)                                                   # bölge dışı (NYAM 11:00'de biter)
    data = p.data()
    assert sig("AG_MSS_5m_2R_KZ", data, i + 2 * M5) == [] and len(sig("AG_MSS_5m_2R_ALL", data, i + 2 * M5)) == 1
    p, i = mss_day(day, 10, 0)                                                    # geçilmeyen 12 bar → süre doldu
    p.flat(i + 5 * M5, i + D1, 100.6)
    assert len(sig("AG_MSS_5m_2R_ALL", p.data(), i + 13 * M5)) == 1 and sig("AG_MSS_5m_2R_ALL", p.data(), i + 14 * M5) == []


# ============================================================================ P4 RB_LIMIT
def rb_day(day: str) -> Path5:
    p = Path5(ny_ms(shift(day, -1), 0, 0), ny_ms(shift(day, 1), 0, 0), 100.0)
    p.bar(ny_ms(day, 9, 0), 100.0, 100.01, 99.6, 99.9)                            # önceki salınım dibi
    p.bar(ny_ms(day, 9, 20), 100.0, 100.9, 99.99, 100.0)                          # süpürülmemiş salınım tepesi
    p.bar(ny_ms(day, 9, 40), 100.0, 100.01, 99.4, 99.8)                           # RB: dibi süpürüp içeri kapanır
    p.flat(ny_ms(day, 9, 45), ny_ms(day, 10, 5), 99.9)
    p.bar(ny_ms(day, 10, 5), 99.9, 99.91, 99.75, 99.85)                           # çizgiye (99,80) ilk dönüş
    p.bar(ny_ms(day, 10, 10), 99.85, 99.96, 99.84, 99.95)                         # çizginin üstünde kapanış (confirm)
    p.flat(ny_ms(day, 10, 15), ny_ms(shift(day, 1), 0, 0), 99.95)
    return p


def test_p4_rb_direct_limit_and_confirm_entry_with_swing_or_2r_target():
    day = "2025-03-12"
    data = rb_day(day).data()
    assert sig("AG_RB_DIRECT", data, ny_ms(day, 9, 45)) == []                    # j + 1 doğrulama barı
    (it,) = sig("AG_RB_DIRECT", data, ny_ms(day, 9, 50))
    assert it.side == LONG and it.entry_ref.limit_price == 99.8 and it.stop == pytest.approx(99.4 * 0.9995)
    assert it.targets == (Target(TARGET_PRICE, 100.9),) and it.entry_ref.target_beyond_entry   # 1,1 ≥ 2R = 0,9
    assert sig("AG_RB_DIRECT", data, ny_ms(day, 10, 10)) == []
    res, _ = A.run_variants(["AG_RB_DIRECT", "AG_RB_CONFIRM"], data)
    (t,) = res["AG_RB_DIRECT"].trades
    assert t.entry_ts == ny_ms(day, 10, 5) and t.entry_ref == 99.8
    assert sig("AG_RB_CONFIRM", data, ny_ms(day, 10, 10)) == []
    (ic,) = sig("AG_RB_CONFIRM", data, ny_ms(day, 10, 15))
    assert ic.entry_ref.limit_price is None and ic.targets == (Target(TARGET_R_FROM_ENTRY, 2.0),)  # 0,95 < 2R = 1,2
    (tc,) = res["AG_RB_CONFIRM"].trades
    assert tc.entry_ts == ny_ms(day, 10, 15)
    # yalnız KZ: aynı desen New York 06:40'ta (LONDON 05:00'te bitti, NYAM 09:30'da başlar) → yok
    p = Path5(ny_ms(shift(day, -1), 0, 0), ny_ms(shift(day, 1), 0, 0), 100.0)
    for (h, m), row in zip(((6, 0), (6, 20), (6, 40)), ((100.0, 100.01, 99.6, 99.9), (100.0, 100.9, 99.99, 100.0),
                                                       (100.0, 100.01, 99.4, 99.8))):
        p.bar(ny_ms(day, h, m), *row)
    p.flat(ny_ms(day, 6, 45), ny_ms(shift(day, 1), 0, 0), 99.9)
    assert all(sig("AG_RB_DIRECT", p.data(), ny_ms(day, 6, 50 + k)) == [] for k in (0, 5))


# ============================================================================ P5 MSNR
def _hour(p: Path5, t0: int, o: float, c: float) -> None:
    x = (o + c) / 2
    p.bar(t0, o, max(o, x) + EPS, min(o, x) - EPS, x)
    p.flat(t0 + M5, t0 + 11 * M5, x)
    p.bar(t0 + 11 * M5, x, max(x, c) + EPS, min(x, c) - EPS, c)


def msnr_day(day: str) -> Path5:
    p = Path5(ny_ms(shift(day, -8), 0, 0), ny_ms(shift(day, 1), 0, 0), 99.6)
    _hour(p, ny_ms(day, 1, 0), 99.6, 100.0)                                       # gövde tepesi 100,00 (yükselen kapanış)
    _hour(p, ny_ms(day, 2, 0), 100.0, 99.6)                                       # düşen açılış 100,00
    _hour(p, ny_ms(day, 3, 0), 99.6, 100.02)
    _hour(p, ny_ms(day, 4, 0), 100.02, 99.8)                                      # seviye ≈ 100,01 (4 üye)
    _hour(p, ny_ms(day, 5, 0), 99.8, 100.4)                                       # gövde yukarı geçiş → taze (UZUN)
    p.flat(ny_ms(day, 6, 0), ny_ms(day, 7, 5), 100.4)
    p.bar(ny_ms(day, 7, 5), 100.4, 100.41, 100.19, 100.2)
    p.bar(ny_ms(day, 7, 10), 100.2, 100.21, 100.0, 100.05)                        # ilk dokunuş
    p.bar(ny_ms(day, 7, 15), 100.05, 100.3, 100.04, 100.12)                       # 5m salınım tepesi
    p.bar(ny_ms(day, 7, 20), 100.12, 100.15, 100.06, 100.1)
    p.bar(ny_ms(day, 7, 25), 100.1, 100.36, 100.09, 100.35)                       # MSS: 100,30'un üstüne ilk kapanış
    p.flat(ny_ms(day, 7, 30), ny_ms(shift(day, 1), 0, 0), 100.35)
    return p


def test_p5_msnr_first_touch_then_5m_mss_in_break_direction():
    day = "2025-03-12"
    data = msnr_day(day).data()
    assert sig("AG_MSNR_2R", data, ny_ms(day, 7, 25)) == []
    (it,) = sig("AG_MSNR_2R", data, ny_ms(day, 7, 30))
    assert it.side == LONG and it.stop == pytest.approx(100.0 * 0.9995) and it.targets == (Target(TARGET_R_FROM_ENTRY, 2.0),)
    assert it.evidence["level"] == pytest.approx(100.01)
    (il,) = sig("AG_MSNR_LEVEL", data, ny_ms(day, 7, 30))
    assert il.targets == (Target(TARGET_PRICE, 100.4),) and il.entry_ref.min_rr_after_cost == 1.5   # bir sonraki seviye
    res, _ = A.run_variants(["AG_MSNR_2R", "AG_MSNR_LEVEL"], data)
    assert [t.entry_ts for t in res["AG_MSNR_2R"].trades] == [ny_ms(day, 7, 30)]
    assert res["AG_MSNR_LEVEL"].trades == [] and res["AG_MSNR_LEVEL"].rejected.get(KN.REJ_RR, 0) >= 1    # < 1,5R → yok
    p = msnr_day(day)                                                             # MSS dokunuştan 12 bardan sonra → yok
    p.flat(ny_ms(day, 7, 15), ny_ms(day, 8, 25), 100.1).bar(ny_ms(day, 8, 15), 100.1, 100.3, 100.09, 100.12)
    p.bar(ny_ms(day, 8, 25), 100.1, 100.36, 100.09, 100.35).flat(ny_ms(day, 8, 30), ny_ms(shift(day, 1), 0, 0), 100.35)
    assert sig("AG_MSNR_2R", p.data(), ny_ms(day, 8, 30)) == []


# ============================================================================ örtüler
class _Stub(Variant):
    def __init__(self, tfs=("1d", "5m"), act=NONE):
        self.variant_id, self.family, self.timeframes, self.signal_tf = "STUB", "K", tuple(tfs), tfs[-1]
        self.lookback = {tf: 10 for tf in tfs}
        self.act = act

    def signals(self, bars, ctx):
        return [Intent(signal_ts=int(ctx["now_ms"]) - M5, side=LONG, entry_ref=AgEntryRef(price=100.0), stop=99.0)]

    def manage(self, pos, bars, ctx):
        return self.act


def _overlay(vid: str, base: Variant) -> AgOverlay:
    v = A.variant(vid)
    spec = A.variant_spec(vid)
    return AgOverlay(vid, KN.RG.thaw(v["params"]), spec, base)


def test_kz_and_cal_overlays_filter_entries_by_new_york_time_and_calendar():
    kz = _overlay("AGO_BOX_KZ", _Stub())
    assert len(kz.signals({}, {"now_ms": ny_ms("2025-03-12", 9, 30)})) == 1 and kz.signals({}, {"now_ms": ny_ms("2025-03-12", 11, 0)}) == []
    cal = _overlay("AGO_BOX_CAL", _Stub())
    for day, ok in (("2025-03-19", False), ("2025-03-20", True), ("2025-01-09", False), ("2024-11-07", False),
                    ("2026-07-03", False), ("2022-12-30", False), ("2027-01-04", False), ("2026-12-30", True)):
        assert (len(cal.signals({}, {"now_ms": ny_ms(day, 10, 0)})) == 1) is ok, day
    # FOMC günü New York tarihidir: 2025-03-19 21:00 New York (= 20 Mart 01:00 UTC) hâlâ engelli
    assert cal.signals({}, {"now_ms": ny_ms("2025-03-19", 21, 0)}) == []
    assert A.cal_flag(ny_ms("2023-01-01", 0, 0), ny_ms("2026-12-31", 23, 55)) is None
    assert A.cal_flag(ny_ms("2022-12-31", 23, 55), ny_ms("2024-01-01", 0, 0)) == "CAL_UNCOVERED"
    assert A.cal_flag(ny_ms("2025-01-01", 0, 0), ny_ms("2027-01-01", 0, 0)) == "CAL_UNCOVERED"


def test_noreact_overlay_exits_when_mfe_below_0_3r_after_3_bars():
    def pos(held):
        return PosView(side=LONG, entry=100.0, stop=99.0, initial_stop=99.0, opened_ms=0, signal_ts=0, entry_bar_ts=0,
                       bars_held=held, intent=Intent(signal_ts=0, side=LONG, entry_ref=AgEntryRef(price=100.0), stop=99.0))

    def bars(hi):
        return {"5m": [{"timestamp": k * M5, "open": 100.0, "high": h, "low": 99.9, "close": 100.0} for k, h in enumerate(hi)]}

    nr = _overlay("AGO_BOX_NOREACT", _Stub())
    assert nr.manage(pos(3), bars([100.1, 100.29, 100.2]), {"now_ms": 3 * M5}).kind == ACT_CLOSE
    assert nr.manage(pos(3), bars([100.1, 100.31, 100.2]), {"now_ms": 3 * M5}) == NONE       # MFE ≥ 0,3R
    assert nr.manage(pos(2), bars([100.1, 100.2]), {"now_ms": 2 * M5}) == NONE
    assert nr.manage(pos(4), bars([100.1, 100.2, 100.2, 100.2]), {"now_ms": 4 * M5}) == NONE
    base_close = _overlay("AGO_BOX_NOREACT", _Stub(act=close("BASE_EOD")))
    assert base_close.manage(pos(3), bars([100.1, 100.1, 100.1]), {"now_ms": 3 * M5}).reason == "BASE_EOD"


def test_box_overlays_run_in_the_kernel_and_kz_entries_are_inside_kill_zones():
    data = SY.box_world(3, days=30)                                               # 2020: takvim kapsamı dışı
    res, _sigs = A.run_variants(["AGO_BOX_KZ", "AGO_BOX_NOREACT", "AGO_BOX_CAL"], data)
    _s, kz = C.parse_sessions(S1D.SESSIONS_MIN, S1D.KZ)
    assert res["AGO_BOX_KZ"].trades and all(C.in_kz(t.entry_ts, kz) for t in res["AGO_BOX_KZ"].trades)
    assert res["AGO_BOX_CAL"].trades == [] and KN.run(KN.make_variant("K_BOX"), data).trades
    nr = [t for t in res["AGO_BOX_NOREACT"].trades if t.exit_reason.startswith("NOREACT")]
    assert nr and all(t.bars_held == 4 for t in nr)                               # 3. barın kapanışında karar, sonraki açılışta çıkış


# ============================================================================ çekirdeğin limit eki
class _LimitOnce(Variant):
    variant_id, family, signal_tf, timeframes = "LIM", "X", "5m", ("5m",)
    lookback = {"5m": 5}

    def __init__(self, at_ms, level, stop, *, target=None, **ref):
        self.at, self.level, self.stop, self.target, self.ref = at_ms, level, stop, target, ref

    def signals(self, bars, ctx):
        if int(ctx["now_ms"]) != self.at:
            return []
        tg = (self.target,) if self.target else ()
        return [Intent(signal_ts=self.at - M5, side=LONG, stop=self.stop, targets=tg, one_entry_per_signal=True,
                       entry_ref=AgEntryRef(price=100.0, limit_price=self.level, **self.ref))]

    def manage(self, pos, bars, ctx):
        return NONE


def _lim_data(low_next: float) -> tuple[KN.SeriesData, int]:
    day = "2025-03-12"
    p = Path5(ny_ms(day, 0, 0), ny_ms(day, 6, 0), 100.0)
    t = ny_ms(day, 3, 0)
    p.bar(t, 100.0, 100.01, low_next, 100.0)
    return p.data(), t


def test_kernel_one_bar_limit_fills_only_through_the_level_by_a_tick():
    assert KN.limit_through(True, 99.5, 100.0, 99.499, 0.001) and not KN.limit_through(True, 99.5, 100.0, 99.4995, 0.001)
    assert KN.limit_through(False, 100.5, 100.501, 100.0, 0.001) and not KN.limit_through(False, 100.5, 100.5, 100.0, 0.001)
    assert KN.limit_through(True, 99.5, 100.0, 99.49, 0.0) and not KN.limit_through(True, 99.5, 100.0, 99.5, 0.0)
    for low, filled in ((99.499, True), (99.5, False), (99.4995, False)):
        data, t = _lim_data(low)
        res = KN.run(_LimitOnce(t, 99.5, 99.0), data)
        assert (len(res.trades) == 1) is filled, low
        if filled:
            (tr,) = res.trades
            assert tr.entry_ts == t and tr.entry_ref == 99.5
            base = KN.scenario("base")
            assert tr.risk_usdt == pytest.approx(abs(99.5 * (1 + base.slip) - 99.0) * tr.notional / (99.5 * (1 + base.slip)))
        else:
            assert res.rejected == {KN.REJ_LIMIT_NOT_FILLED: 1}
    data, t = _lim_data(99.0)                                                     # stop yüzdesi ve hedef yönü kapıları
    assert KN.run(_LimitOnce(t, 99.5, 99.45, stop_pct_min=0.0008), data).rejected == {KN.REJ_STOP_PCT: 1}
    assert KN.run(_LimitOnce(t, 99.5, 97.5, stop_pct_max=0.012), data).rejected == {KN.REJ_STOP_PCT: 1}
    assert KN.run(_LimitOnce(t, 99.5, 99.0, target=Target(TARGET_PRICE, 99.4), target_beyond_entry=True),
                  data).rejected == {KN.REJ_TARGET_SIDE: 1}
    ok = KN.run(_LimitOnce(t, 99.5, 99.0, target=Target(TARGET_PRICE, 99.9), target_beyond_entry=True, stop_pct_min=0.0008,
                           stop_pct_max=0.012), data)
    assert len(ok.trades) == 1 and ok.trades[0].exit_kind == KN.EXIT_STOP                     # dolum barında stop (düşük 99,0)


# ============================================================================ ileriye bakmama + önbellek şeffaflığı
class _Rec:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def signals(self, bars, ctx):
        out = self.inner.signals(bars, ctx)
        if out:
            self.calls.append((int(ctx["now_ms"]), tuple(ctx["history"]), tuple(out)))
        return out

    def __getattr__(self, n):
        return getattr(self.inner, n)


def _rand_world(seed: int, days: int = 24) -> KN.SeriesData:
    start = ny_ms("2025-02-25", 0, 0)
    m5 = SY.walk(days * 288, seed=seed, step=M5, start=start, p0=100.0, vol=0.002, trend=0.0004, regime_len=24, gap_p=0.0,
                 gap_vol=0.0, wick=0.9)
    bars = {"5m": m5, "15m": SY.aggregate(m5, M15), "1h": SY.aggregate(m5, H1), "4h": SY.aggregate(m5, H4),
            "1d": SY.aggregate(m5, D1)}
    fr = SY.funding_rows(m5, M5, seed=seed)
    return KN.SeriesData(symbol=f"R{seed}", bars=bars, funding=fr, price_tick=TICK, markpx_1h=SY.markpx_from_funding(fr))


def _cut(data: KN.SeriesData, t: int, perturb: random.Random | None) -> KN.SeriesData:
    out = {}
    for tf, rows in data.bars.items():
        step = KN.TF_MS[tf]
        keep = [r for r in rows if int(r["timestamp"]) + step <= t]
        if perturb is not None:
            keep += [{**r, "high": r["high"] * (1 + perturb.random() * 0.01), "low": r["low"] * (1 - perturb.random() * 0.01)}
                     for r in rows if int(r["timestamp"]) + step > t]
        out[tf] = keep
    return KN.SeriesData(symbol=data.symbol, bars=out, funding=data.funding, price_tick=data.price_tick,
                         markpx_1h=data.markpx_1h)


AG_PURE = [v for v in S1D.LIB_AG_V1["variant_order"] if not v.startswith("AGO_")]


@pytest.mark.parametrize("vid", AG_PURE)
def test_no_look_ahead_and_memo_is_transparent(vid):
    data = _rand_world(17 + AG_PURE.index(vid) % 3)
    rec = _Rec(A.make_variant(vid))
    KN.run(rec, data)
    rnd = random.Random(vid)
    times = [c[0] for c in rec.calls[:6]]
    step = KN.TF_MS[A.variant(vid)["signal_tf"]]
    ts = [int(r["timestamp"]) + step for r in data.bars[A.variant(vid)["signal_tf"]]]
    times += rnd.sample(ts[len(ts) // 2:], 4)
    by_t = {c[0]: c for c in rec.calls}
    for t in times:
        hist = by_t[t][1] if t in by_t else ()
        full = KN.signals_at(A.make_variant(vid), data, t, hist)
        assert full == KN.signals_at(A.make_variant(vid), _cut(data, t, None), t, hist), (vid, t)
        assert full == KN.signals_at(A.make_variant(vid), _cut(data, t, rnd), t, hist), (vid, t)
        if t in by_t:
            assert tuple(full) == by_t[t][2], (vid, t)                            # önbellekli koşu = taze hesap


def test_strategy_lib_ag_modules_are_pure_and_do_not_load_the_engine():
    mods = ", ".join(f"tradingbot.strategy_lib.{m}" for m in ("ag_clock", "ag_common", "ag_p1_lunch", "ag_p2_tenm",
                                                              "ag_p3_mss", "ag_p4_rb", "ag_p5_msnr", "ag_overlay"))
    code = (f"import sys, tradingbot.strategy_lib.catalog, {mods}; "
            "print(sorted(m for m in sys.modules if m.startswith('tradingbot.research_engine') or m in ('zoneinfo',)))")
    cp = subprocess.run([sys.executable, "-s", "-c", code], capture_output=True, text=True, cwd=str(ROOT), timeout=180)
    assert cp.returncode == 0, cp.stderr[-2000:]
    assert cp.stdout.strip().splitlines()[-1] == "[]", cp.stdout


def test_ag_identity_declares_its_modules_and_spec_shas_are_distinct():
    from tradingbot.strategy_lib import identity as SLI
    core = {"tradingbot/strategy_lib/ag_clock.py", "tradingbot/strategy_lib/ag_common.py", "tradingbot/strategy_lib/contract.py",
            "tradingbot/strategy_lib/indicators.py"}
    fam_mod = {"AG_P1": "ag_p1_lunch", "AG_P2": "ag_p2_tenm", "AG_P3": "ag_p3_mss", "AG_P4": "ag_p4_rb", "AG_P5": "ag_p5_msnr"}
    for vid in A.variant_ids():
        files = set(SLI.module_files(vid))
        fam = A.variant(vid)["family"]
        if fam == "AG_O":
            base = A.variant(vid)["cover"]["base"]
            base_vid = "K_C4_CV001" if base == "C4" else f"K_{base}"
            assert files == set(SLI.module_files(base_vid)) | {"tradingbot/strategy_lib/ag_overlay.py",
                                                               "tradingbot/strategy_lib/ag_clock.py"}, vid
        else:
            assert files == core | {f"tradingbot/strategy_lib/{fam_mod[fam]}.py"}, vid
    try:
        shas = A.module_src_shas()
    except SLI.DirtyTreeError as exc:
        pytest.skip(f"çalışma ağacı kirli ya da AG dosyaları henüz işlenmedi (kimlik bilerek hesaplanmaz): {exc}")
    assert len({A.spec_sha_for(v, shas[v]) for v in A.variant_ids()}) == 38
    assert {shas[v] for v in A.variant_ids() if v.startswith("AG_RB_")} == {SLI.module_src_sha("AG_RB_DIRECT")}
