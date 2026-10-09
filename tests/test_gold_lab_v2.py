# -*- coding: utf-8 -*-
"""Altın laboratuvarı gold_v2 (docs/GOLD_LAB_V2.md; ön kayıt 2026-10-05): ön kayıt mührü ve kaydın belgeyi kapsaması, işlem günü
(17:00 New York) ve 4h kutuları yaz/kış, hafta sonu F/S (saat değişimi hafta sonları dahil), haftanın ilk saati denetimi,
Dukascopy saatlik/günlük bi5 çözümü (ay/yıl başı ofsetleri, kapalı piyasa düz barları, kayıt denetimi), kapsama ve manifest,
segment/boşluk kuralı (ısınma yeniden, BOŞLUK_TUTUŞ, KESİLDİ), GELECEĞE BAKMAMA (her varyant: gelecek fiyatları bozma ve t'de
kesme), çıkış türleri (channel, channel20, sign, month_end, hold) ve fonlama vekili, plasebolar (maruziyet eşli K = 5, aylık p ve
işaret rastgeleleştirmesi, hafta sonu S'ye bağlı), sabit tarihli dönemler ve aggregate ile birebir hüküm, aylık hedef ölçüsü
(78 / 33 ay), "daha yüksek risk" satırı, C, sonuç kuralı, ayna hazır/anlık görüntü, uçtan uca (sentetik ayna + sahte arşiv, ağ
YOK) ve komut satırı. Yalnız sentetik veri; gerçek altın fiyatı YOK."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import lzma
import math
import urllib.request
import zipfile
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradingbot import gold_lab as G
from tradingbot import gold_lab_v2 as V
from tradingbot import signal_lab as L

ROOT = Path(__file__).resolve().parents[1]
HOUR, DAY = 3_600_000, 86_400_000
CFG = L.LabConfig(bootstrap_iters=200)

#: ön kayıt mührü — bir kural sabiti, metin, plasebo tanımı, pencere, eşik ya da okunuş değişirse bu test KIRILIR (yeni sürüm +
#: belge + yeni deneme sayısı; sonuç görüldükten sonra gevşetme YOK)
PINNED_SHA = "72182fc4343f9e3f"


# ---------------------------------------------------------------------------- yardımcılar
def bi5(records) -> bytes:
    a = np.zeros(len(records), dtype=G.DUKA_DTYPE)
    for k, r in enumerate(records):
        a[k] = r
    return lzma.compress(a.tobytes(), format=lzma.FORMAT_ALONE)


def write_mirror(root: Path, hourly: pd.DataFrame, mi0: int, mi1: int, *, skip=(), manifest_404=(), thin=(), day_years=(),
                 log: bool = True) -> Path:
    """Sentetik Dukascopy aynası (gerçek fiyat DEĞİL): her ay BÜTÜN saatler (kapalı saatler düz ve hacim 0), manifest, günlük."""
    root = Path(root)
    s = hourly.set_index("timestamp")
    lines = []
    for mi in range(mi0, mi1 + 1):
        key = V.mi_str(mi)
        if mi in skip:
            lines.append({"key": key, "kind": "hour", "status": 404, "bytes": 0, "tries": 1, "ts": "2026-10-05T06:00:00+00:00"})
            continue
        n = V.days_in_month(mi) * 24
        hours = V.month_start_ms(mi) + np.arange(n, dtype=np.int64) * HOUR
        g = s.reindex(hours)
        present = g["close"].notna().to_numpy().copy()
        if mi in thin:
            present &= np.arange(n) % 5 == 0
        last = g["close"].where(present).ffill().bfill().fillna(1000.0).to_numpy()
        rec = np.zeros(n, dtype=G.DUKA_DTYPE)
        rec["t"] = np.arange(n) * 3600
        for k, col in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close")):
            rec[k] = np.round(np.where(present, g[col].to_numpy(), last) * 1000)
        rec["v"] = np.where(present, g["volume"].fillna(0).to_numpy(), 0.0)
        data = lzma.compress(rec.tobytes(), format=lzma.FORMAT_ALONE, preset=1)
        p = V.duka_hour_path(root, mi)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        bad = mi in manifest_404
        lines.append({"key": key, "kind": "hour", "status": 404 if bad else 200, "bytes": 0 if bad else len(data), "tries": 1,
                      "ts": f"2026-10-05T06:{mi % 60:02d}:00+00:00"})
    for y in day_years:
        days = 366 if (y % 4 == 0) else 365
        a = V.day_ms(f"{y}-01-01")
        rec = np.zeros(days, dtype=G.DUKA_DTYPE)
        rec["t"] = np.arange(days) * 86400
        sub = hourly[(hourly["timestamp"] >= a) & (hourly["timestamp"] < a + days * DAY)]
        agg = sub.assign(d=(sub["timestamp"] - a) // DAY).groupby("d").agg(o=("open", "first"), h=("high", "max"), lo=("low", "min"),
                                                                         c=("close", "last"), v=("volume", "sum"))
        px = 1000.0
        for d in range(days):
            if d in agg.index:
                r = agg.loc[d]
                rec[d] = (d * 86400, round(r["o"] * 1000), round(r["c"] * 1000), round(r["lo"] * 1000), round(r["h"] * 1000), r["v"])
                px = float(r["c"])
            else:
                rec[d] = (d * 86400, round(px * 1000), round(px * 1000), round(px * 1000), round(px * 1000), 0.0)
        data = lzma.compress(rec.tobytes(), format=lzma.FORMAT_ALONE, preset=1)
        p = V.duka_day_path(root, y)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        lines.append({"key": str(y), "kind": "day", "status": 200, "bytes": len(data), "tries": 1, "ts": "2026-10-05T07:00:00+00:00"})
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    lp = root.parent / f"{root.name}.log"
    lp.write_text(("ilerleme...\n" + V.MIRROR_DONE_LINE + "\n") if log else "ilerleme...\n", encoding="utf-8")
    return lp


def a_frames(h: pd.DataFrame):
    D, H4, _ = V.duka_frames(h)
    V.add_indicators(D, ann=V.ANN_DUKA)
    V.add_indicators(H4)
    return D, H4


def a_sigs(name: str, D, H4, usable=None, with_spec: bool = True):
    v = V.VARIANTS[name]
    if v["entry"] in V.MONTHLY:
        sigs, _, _ = V.monthly_signals(name, D, V.month_table(D, usable))
        return [(int(D["cms"][gi]), sd, round(st, 9)) + ((sp["bars"],) if with_spec else ()) for gi, sd, st, sp, _m in sigs]
    F = D if v["bars"] == "1d" else H4
    return [(int(F["cms"][gi]), sd, round(st, 9)) for gi, sd, st, _sp in V.rule_signals(name, F, D)]


def daily_of(h: pd.DataFrame) -> pd.DataFrame:
    d = h.assign(day=h["timestamp"] // DAY).groupby("day").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                               close=("close", "last"), volume=("volume", "sum")).reset_index()
    d["timestamp"] = d["day"].astype(np.int64) * DAY
    return d[["timestamp", "open", "high", "low", "close", "volume"]]


def b_frames(h: pd.DataFrame, d: pd.DataFrame | None = None):
    H = V.binance_frame(h, "1h", symbol=V.PAXG)
    Dd = V.add_indicators(V.binance_frame(daily_of(h) if d is None else d, "1d", symbol=V.PAXG), ann=V.ANN_BINANCE)
    return H, Dd


def b_sigs(name: str, H, Dd, a: str, b: str):
    weeks = V.weekend_table(H, Dd, *V.win_ms(a, b))
    _, sigs, _ = V.weekend_signals(name, H, weeks)
    return [(int(w["S"]), sd, round(st, 9)) for w, sd, st in sigs]


def perturb(h: pd.DataFrame, cut: int, seed: int) -> pd.DataFrame:
    m = h.copy()
    sel = m["timestamp"].to_numpy() >= cut
    f = np.random.default_rng(seed).uniform(0.7, 1.4, int(sel.sum()))
    for col in ("open", "high", "low", "close"):
        m.loc[sel, col] = m.loc[sel, col].to_numpy() * f
    return m


# ---------------------------------------------------------------------------- mühür ve kayıt
def test_registry_sha_is_pinned_documented_and_sensitive_to_every_part():
    again = hashlib.sha256(json.dumps(V.GOLD_V2_REGISTRY, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert V.GOLD_V2_REGISTRY_SHA == again == PINNED_SHA, "kayıt değişti: yeni sürüm + belge + yeni deneme sayısı gerekir"
    doc = (ROOT / "docs" / "GOLD_LAB_V2.md").read_text(encoding="utf-8")
    assert f"Ön kayıt mührü: GOLD_V2_REGISTRY_SHA = {PINNED_SHA}" in doc and V.GOLD2_VERSION == "gold_v2"
    assert G.GOLD_REGISTRY_SHA == "ee32a9db510f41cd"                     # gold_v1'in kaydı değişmez
    R = V.GOLD_V2_REGISTRY
    for path, val in ((("variants", "A_DONCH_20_10", "n"), 21), (("variants", "B_WKND_REV_ALL", "hold_h"), 24),
                      (("variants", "A_TSMOM_12M", "stop_atr"), 4.0), (("placebo", "matched", "k"), 4), (("dukascopy", "gap_h"), 96),
                      (("dukascopy", "coverage", "month_bars"), 0.8), (("dukascopy", "timestamp_check", "summer"), [22]),
                      (("windows", "A", "oos"), ["2014-01-02", "2020-07-31"]), (("monthly", "risk_pct"), 1.0),
                      (("cost", "funding_proxy", "rate_8h"), 0.0002), (("readings_tr",), []), (("main_cells",), []),
                      (("common", "warmup"), 200), (("higher_risk", "profile_cap"), 3.0), (("c_sizing", "target_vol"), 0.15)):
        tweak = json.loads(json.dumps(R))
        node = tweak
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = val
        assert hashlib.sha256(json.dumps(tweak, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16] != PINNED_SHA, path


def test_registry_covers_every_rule_of_the_document():
    R = V.GOLD_V2_REGISTRY
    doc = (ROOT / "docs" / "GOLD_LAB_V2.md").read_text(encoding="utf-8")
    names = ["A_DONCH_20_10", "A_TSMOM_28", "A_DONCH_55_20", "A_TSMOM_1M", "A_TSMOM_3M", "A_TSMOM_12M", "A_SMA10M", "A_4H_DONCH_D200",
             "B_WKND_REV_ALL", "B_WKND_REV_050", "B_WKND_REV_100", "B_WKND_REV_050_LDN"]
    assert list(R["variants"]) == names and all(f"`{n}`" in doc for n in names)
    assert len(R["cells"]) == 24 and R["main_cells"] == ["A_DONCH_20_10 LONG", "A_TSMOM_28 LONG", "B_WKND_REV_ALL İKİ YÖN"]
    assert R["trials"]["cells"] == 24 and R["trials"]["cumulative_gold_cells"] == 56
    v = R["variants"]
    assert (v["A_DONCH_20_10"]["n"], v["A_DONCH_20_10"]["exit"], v["A_DONCH_20_10"]["stop_atr"]) == (20, {"kind": "channel", "n": 10, "max_bars": 300}, 2.0)
    assert (v["A_TSMOM_28"]["exit"], v["A_TSMOM_28"]["stop_atr"]) == ({"kind": "sign", "max_bars": 300}, 3.0)
    assert v["A_DONCH_55_20"]["exit"] == {"kind": "channel20", "n": 20, "max_bars": 300} and v["A_DONCH_55_20"]["n"] == 55
    assert [v[n]["months"] for n in ("A_TSMOM_1M", "A_TSMOM_3M", "A_TSMOM_12M", "A_SMA10M")] == [1, 3, 12, 10]
    assert all(v[n]["stop_atr"] == 5.0 and v[n]["exit"]["kind"] == "month_end" for n in ("A_TSMOM_1M", "A_TSMOM_3M", "A_TSMOM_12M", "A_SMA10M"))
    assert v["A_4H_DONCH_D200"]["exit"] == {"kind": "channel", "n": 10, "max_bars": 180} and v["A_4H_DONCH_D200"]["d200"]
    assert [(v[n]["z_min"], v[n]["hold_h"]) for n in names[8:]] == [(0.0, 23), (0.5, 23), (1.0, 23), (0.5, 9)]
    assert v["B_WKND_REV_ALL"]["z_strict"] and not v["B_WKND_REV_050"]["z_strict"]
    for s in ("2006-01-01 → 2013-12-31", "2014-01-01 → 2020-07-31", "2020-08-28 → 2023-12-31", "2024-01-01 → 2026-09-30",
              "**K = 5**", "120 saat", "40 saatten", "0,85 ×", "8 saatte %0,01", "S + 9 saat", "2014-01 → 2020-06 (78 ay)",
              "2024-01 → 2026-09 (33 ay)", "%0,5 riskte", "≥ 20 geçerli saatlik bar"):
        assert s in doc, s
    assert V.WINDOWS["A"]["is"] == ["2006-01-01", "2013-12-31"] and V.WINDOWS["A"]["oos"] == ["2014-01-01", "2020-07-31"]
    assert V.WINDOWS["B"]["is"] == ["2020-08-28", "2023-12-31"] and V.WINDOWS["B"]["oos"] == ["2024-01-01", "2026-09-30"]
    assert (V.K_MATCH, V.GAP_DUKA_H, V.GAP_BINANCE_H, V.WEEK_GAP_H, V.WARMUP, V.FUND_RATE_8H) == (5, 120, 48, 40, 210, 0.0001)
    assert (V.HW_SUMMER, V.HW_WINTER, V.HW_MIN_SHARE, V.COV_MONTH_SHARE, V.COV_DAY_SHARE) == ((21, 22), (22, 23), 0.9, 0.95, 0.95)
    assert R["cost"]["round_trip_pct"] == 0.16 and R["stats"]["strict_rule_tr"] == L.STRICT_RULE_TR
    assert len(R["readings_tr"]) == len(V.READINGS_TR) >= 30


# ---------------------------------------------------------------------------- işlem günü, 4h, hafta sonu, zaman damgası
def test_trading_day_daily_and_4h_bins_follow_new_york_17_00_in_summer_and_winter():
    ms = V.day_ms
    dn, htd, off = V.ny_parts([ms("2024-07-07") + 21 * HOUR,      # Pazar 17:00 EDT → Pazartesi, işlem gününün ilk saati
                               ms("2024-07-05") + 20 * HOUR,      # Cuma 16:00 EDT → Cuma, son saat
                               ms("2024-07-05") + 21 * HOUR,      # Cuma 17:00 EDT → Cumartesi (hafta sonu; dışarıda)
                               ms("2024-01-05") + 21 * HOUR,      # Cuma 16:00 EST → Cuma
                               ms("2024-01-07") + 23 * HOUR])     # Pazar 18:00 EST → Pazartesi
    assert [str(V.date_of(d)) for d in dn] == ["2024-07-08", "2024-07-05", "2024-07-06", "2024-01-05", "2024-01-08"]
    assert list(htd) == [0.0, 23.0, 0.0, 23.0, 1.0] and list(off) == [-4, -4, -4, -5, -5]
    h = pd.concat([V.synthetic_duka_hourly("2024-01-01", "2024-01-14", 3), V.synthetic_duka_hourly("2024-07-01", "2024-07-14", 4)])
    extra = pd.DataFrame({"timestamp": [ms("2024-07-13") + 12 * HOUR], "open": [1.0e3], "high": [1.001e3], "low": [0.999e3],
                          "close": [1.0e3], "volume": [1.0]})                                    # Cumartesi saati
    h = pd.concat([h, extra]).sort_values("timestamp").reset_index(drop=True)
    D, H4, info = V.duka_frames(h)
    assert info["weekend_hours"] == 1 and info["segments"] == 2                               # Ocak → Temmuz boşluğu > 120 saat
    wd = V.weekday(D["dnum"])
    assert (wd < 5).all() and np.all(D["cms"] - D["ts"] == DAY)
    summer = D["dnum"] >= V.dnum_of(date(2024, 7, 1))
    assert set(V.utc_hour(D["cms"][summer]).tolist()) == {21} and set(V.utc_hour(D["cms"][~summer]).tolist()) == {22}
    loc = pd.to_datetime(H4["ts"], unit="ms", utc=True).tz_convert(V.TZ_NY)
    assert set(loc.hour.tolist()) <= {17, 21, 1, 5, 9, 13} and np.all(H4["cms"] - H4["ts"] == 4 * HOUR)
    full = [k for k in range(D["n"]) if D["hours"][k] == 23]
    assert len(full) == 18                                                    # 2 × 2 hafta; ilk Pazartesiler ve son Pazar akşamı kısmi
    for k in range(D["n"]):                                                                    # her günün kutuları o günün içinde
        m = H4["dnum"] == D["dnum"][k]
        assert H4["ts"][m].min() >= D["ts"][k] and H4["cms"][m].max() <= D["cms"][k]
        assert (m.sum() == 6 and H4["cms"][m].max() == D["cms"][k]) or k not in full
    # günlük bar = o işlem gününün saatleri (açılış ilk, kapanış son, uçlar)
    hts = h["timestamp"].to_numpy()
    k = 3
    sel = (hts >= D["ts"][k]) & (hts < D["cms"][k])
    assert D["open"][k] == h["open"][sel].iloc[0] and D["close"][k] == h["close"][sel].iloc[-1]
    assert D["high"][k] == h["high"][sel].max() and D["low"][k] == h["low"][sel].min() and D["hours"][k] == sel.sum() == 23


def test_weekend_F_and_S_follow_new_york_on_their_own_dates_including_dst_switch_weekends():
    h, d = V.synthetic_paxg_hourly("2024-01-01", "2024-12-31", 5)
    H, Dd = b_frames(h, d)
    weeks = {w["friday"]: w for w in V.weekend_table(H, Dd, *V.win_ms("2024-01-01", "2024-12-31"))}
    ms = V.day_ms
    for fri, f_utc, sun, s_utc, span in (("2024-01-05", 22, "2024-01-07", 23, 49), ("2024-07-05", 21, "2024-07-07", 22, 49),
                                         ("2024-03-08", 22, "2024-03-10", 22, 48), ("2024-11-01", 21, "2024-11-03", 23, 50)):
        w = weeks[fri]
        assert w["F"] == ms(fri) + f_utc * HOUR and w["S"] == ms(sun) + s_utc * HOUR and (w["S"] - w["F"]) // HOUR == span, fri
        assert H["ts"][w["iF"]] == w["F"] - HOUR and H["ts"][w["iS"]] == w["S"] - HOUR
        jd = int(np.searchsorted(Dd["cms"], w["F"], side="right")) - 1
        assert Dd["cms"][jd] <= w["F"] < Dd["cms"][jd + 1] and V.weekday(Dd["dnum"][jd]) == 3      # Perşembe barı
        assert w["atr_d"] == Dd["atr"][jd] or (np.isnan(w["atr_d"]) and np.isnan(Dd["atr"][jd]) and fri == "2024-01-05")
    assert weeks["2024-03-29"]["holiday"] == "Kutsal Cuma" and V.holiday_friday(date(2020, 12, 25)) == "Noel"
    assert V.holiday_friday(date(2021, 1, 1)) == "Yılbaşı" and V.holiday_friday(date(2024, 3, 22)) == ""
    assert V.easter(2024) == date(2024, 3, 31) and V.easter(2019) == date(2019, 4, 21)


def test_week_open_check_accepts_utc_series_and_stops_a_shifted_one():
    h = V.synthetic_duka_hourly("2009-01-01", "2010-12-31", 6)
    per = {"IS": V.win_ms("2009-01-01", "2009-12-31"), "OOS": V.win_ms("2010-01-01", "2010-12-31")}
    ok = V.week_open_check(h["timestamp"].to_numpy(), per)
    assert ok["pass"] and all(p["share"] == 1.0 and p["weeks"] >= 50 for p in ok["periods"].values())
    assert set(ok["summer"]) == {"22"} and set(ok["winter"]) == {"23"}                        # Pazar 18:00 NY
    assert set(ok["friday_last"]["summer"]) == {"20"} and set(ok["friday_last"]["winter"]) == {"21"}
    bad = V.week_open_check(h["timestamp"].to_numpy() + 2 * HOUR, per)                        # yanlış saat dilimi
    assert not bad["pass"] and bad["periods"]["IS"]["share"] < 0.9
    half = V.week_open_check(h["timestamp"].to_numpy(), {"IS": per["IS"], "X": V.win_ms("2015-01-01", "2015-12-31")})
    assert not half["pass"] and half["periods"]["X"]["weeks"] == 0                             # haftası olmayan dönem geçmez


# ---------------------------------------------------------------------------- Dukascopy çözme, temizlik, kapsama
def test_dukascopy_hour_and_day_files_decode_with_month_and_year_offsets_and_clean_closed_flats():
    mi = V.mi_of(2024, 3)                                     # Mart 2024: 31 gün, dosya klasörü 02
    recs = [(0, 2050123, 2050500, 2049900, 2050600, 0.5),               # 03-01 00:00
            (3600, 2050500, 2050500, 2050500, 2050500, 0.0),            # kapalı piyasa (düz + hacim 0) → atılır
            (7200, 2050500, 2050100, 2050000, 2050700, 0.0),            # hacimsiz ama hareketli → tutulur
            (10800, 2050100, 2050100, 2050100, 2050100, 2.0),           # düz ama hacimli → tutulur
            (14400, 2050100, 2052000, 2052100, 2050000, 1.0),           # düşük > yüksek → geçersiz
            (31 * 86400 - 3600, 2051000, 2051500, 2050900, 2051600, 1.0)]   # 03-31 23:00
    df = V.decode_hour_file(bi5(recs), mi)
    assert list(df["timestamp"]) == [V.day_ms("2024-03-01") + t * 1000 for t, *_ in recs]
    r0 = df.iloc[0]
    assert (r0["open"], r0["close"], r0["low"], r0["high"], r0["volume"]) == (2050.123, 2050.5, 2049.9, 2050.6, 0.5)
    kept, fl = V.clean_bars(df)
    assert len(kept) == 4 and [int(fl[k].sum()) for k in ("flat_closed", "vol0_moving", "flat_vol_pos", "invalid")] == [1, 1, 1, 1]
    assert V.duka_hour_path("/m", mi).as_posix() == "/m/XAUUSD/2024/02/BID_candles_hour_1.bi5"
    assert V.duka_day_path("/m", 2010).as_posix() == "/m/XAUUSD/2010/BID_candles_day_1.bi5"
    for bad in ([(1800, 1, 1, 1, 1, 1.0)],                                             # saat başı değil
                [(31 * 86400, 2e6, 2e6, 2e6, 2e6, 1.0)],                               # ay dışında
                [(3600, 2e6, 2e6, 2e6, 2e6, 1.0), (3600, 2e6, 2e6, 2e6, 2e6, 1.0)],     # kesin artan değil
                [(-3600, 2e6, 2e6, 2e6, 2e6, 1.0)]):
        with pytest.raises(ValueError):
            V.decode_hour_file(bi5(bad), mi)
    with pytest.raises(ValueError):                                                     # 24'ün katı değil
        V.decode_hour_file(lzma.compress(b"\x00" * 25, format=lzma.FORMAT_ALONE), mi)
    assert len(V.decode_hour_file(bi5([(29 * 86400 - 3600, 2e6, 2e6, 2e6, 2e6, 1.0)]), V.mi_of(2024, 2))) == 1   # artık Şubat 29 gün
    with pytest.raises(ValueError):
        V.decode_hour_file(bi5([(28 * 86400, 2e6, 2e6, 2e6, 2e6, 1.0)]), V.mi_of(2023, 2))
    day = V.decode_day_file(bi5([(0, 2e6, 2e6, 2e6, 2e6, 1.0), (365 * 86400, 2e6, 2e6, 2e6, 2e6, 1.0)]), 2024)   # 2024-12-31
    assert list(day["timestamp"]) == [V.day_ms("2024-01-01"), V.day_ms("2024-12-31")]
    for bad, y in (([(365 * 86400, 2e6, 2e6, 2e6, 2e6, 1.0)], 2023), ([(3600, 2e6, 2e6, 2e6, 2e6, 1.0)], 2024)):
        with pytest.raises(ValueError):
            V.decode_day_file(bi5(bad), y)
    assert V.decode_hour_file(b"", mi).empty


def test_hourly_loader_coverage_manifest_thin_corrupt_and_invalid_files(tmp_path):
    h = V.synthetic_duka_hourly("2009-01-01", "2010-12-31", 8)
    root = tmp_path / "duka"
    mi0, mi1 = V.mi_of(2009, 1), V.mi_of(2010, 12)
    write_mirror(root, h, mi0, mi1, skip={V.mi_of(2009, 3)}, manifest_404={V.mi_of(2009, 5)}, thin={V.mi_of(2010, 2)})
    V.duka_hour_path(root, V.mi_of(2010, 6)).write_bytes(b"bozuk lzma")                 # çözülemez → kullanılamaz ay
    a, b = V.win_ms("2009-01-01", "2010-12-31")
    kept, info = V.load_duka_hourly(root, mi0, mi1, a, b)
    m = info["months"]
    assert not m["2009-03"]["file"] and not m["2009-03"]["usable"]
    assert m["2009-05"]["file"] and m["2009-05"]["decoded"] and not m["2009-05"]["manifest_ok"] and not m["2009-05"]["usable"]
    assert m["2010-02"]["decoded"] and m["2010-02"]["kept"] < m["2010-02"]["need"] and not m["2010-02"]["usable"]
    assert m["2010-06"]["error"].startswith("çözülemedi") and not m["2010-06"]["usable"]
    jan = h[(h["timestamp"] >= V.day_ms("2009-01-01")) & (h["timestamp"] < V.day_ms("2009-02-01"))]
    assert m["2009-01"]["usable"] and m["2009-01"]["rows"] == 744 and m["2009-01"]["kept"] == len(jan) >= m["2009-01"]["need"]
    assert len(info["usable_months"]) == 20
    # bütün kapalı saatler atıldı: tutulan saatler sentetik açık saatlerin aynısı (bozuk/eksik aylar hariç)
    lost = {V.mi_of(2009, 3), V.mi_of(2010, 6)}
    want = h[[int(x) not in lost for x in V.mi_from_dnum(h["timestamp"] // DAY)]]
    thin_m = V.mi_from_dnum(kept["timestamp"] // DAY) == V.mi_of(2010, 2)
    assert len(kept) - thin_m.sum() == len(want) - (V.mi_from_dnum(want["timestamp"] // DAY) == V.mi_of(2010, 2)).sum()
    cov = V.coverage_check(m, kept["timestamp"].to_numpy(), {"IS": ("2009-01-01", "2009-12-31"), "OOS": ("2010-01-01", "2010-12-31")})
    assert cov["periods"]["IS"]["bad_months"] == ["2009-03", "2009-05"] and cov["periods"]["OOS"]["bad_months"] == ["2010-02", "2010-06"]
    assert not cov["pass"] and cov["periods"]["IS"]["month_share"] == round(10 / 12, 4)
    assert cov["periods"]["IS"]["n_bad_days"] == 22 and cov["periods"]["OOS"]["n_bad_days"] == 20 + 22   # Mart 2009; Şubat + Haziran 2010
    good = V.coverage_check(m, kept["timestamp"].to_numpy(), {"X": ("2009-06-01", "2010-01-31")})
    assert good["pass"] and good["periods"]["X"]["day_share"] == 1.0
    # kayıt denetimi tutmayan dosya koşuyu durdurur (yolu söyler)
    V.duka_hour_path(root, V.mi_of(2010, 7)).write_bytes(bi5([(1800, 2e6, 2e6, 2e6, 2e6, 1.0)]))
    with pytest.raises(V.GoldDataError, match="2010/06/BID_candles_hour_1.bi5"):
        V.load_duka_hourly(root, mi0, mi1, a, b)


# ---------------------------------------------------------------------------- segment, ısınma, BOŞLUK_TUTUŞ, KESİLDİ
def test_gap_over_120h_starts_a_new_segment_restarts_warmup_and_drops_trades_crossing_it():
    h1 = V.synthetic_duka_hourly("2009-01-01", "2010-03-31", 9)
    h2 = V.synthetic_duka_hourly("2010-04-12", "2011-06-30", 10)                      # 2010-04-01..11 eksik (> 120 saat)
    h = pd.concat([h1, h2]).reset_index(drop=True)
    D, H4 = a_frames(h)
    assert len(D["segs"]) == 2 and len(H4["segs"]) == 2
    s1 = D["segs"][1][0]
    assert D["segpos"][s1] == 0 and np.isnan(D["atr"][s1:s1 + 13]).all() and np.isfinite(D["atr"][s1 + 13])
    assert np.isnan(D["hi20"][s1:s1 + 20]).all() and np.isfinite(D["hi20"][s1 + 20]) and np.isnan(D["mom28"][s1 + 27])
    for name in ("A_DONCH_20_10", "A_TSMOM_28", "A_4H_DONCH_D200"):
        F = D if V.VARIANTS[name]["bars"] == "1d" else H4
        assert all(F["segpos"][gi] >= V.WARMUP for gi, *_ in V.rule_signals(name, F, D)), name
    assert V._gap_lookback(D) == V.WARMUP
    # segment sonunu aşan işlem BOŞLUK_TUTUŞ, verinin sonunu aşan KESİLDİ (piyasaya göre R bilgi)
    e0 = D["segs"][0][1]
    gi = e0 - 5
    why, row, mtm = V.simulate_one(D, gi, "LONG", float(D["close"][gi] - 9 * D["atr"][gi]), {"kind": "hold", "bars": 10}, D["atr"][gi],
                                   CFG, family=V.FAMILY, name="x", cell="LONG", period="IS")
    assert why == "BOŞLUK_TUTUŞ" and row is None and mtm is None
    gl = D["n"] - 4
    stop = float(D["close"][gl] - 3 * D["atr"][gl])
    why, row, mtm = V.simulate_one(D, gl, "LONG", stop, {"kind": "hold", "bars": 10}, D["atr"][gl], CFG, family=V.FAMILY, name="x",
                                   cell="LONG", period="OOS")
    entry, last = D["open"][gl + 1], D["close"][-1]
    hit = (D["low"][gl + 1:] <= stop).any()
    assert why == "KESİLDİ" and row is None and (hit or math.isclose(mtm, ((last - entry) - (entry + last) * CFG.cost_per_side) / (entry - stop)))


# ---------------------------------------------------------------------------- geleceğe bakmama
@pytest.mark.parametrize("name", list(V.A_NAMES))
def test_no_lookahead_family_a_perturbing_the_future_and_truncating_at_t(name):
    h = V.synthetic_duka_hourly("2009-01-01", "2011-12-31", 11)
    D, H4 = a_frames(h)
    full = a_sigs(name, D, H4)
    covered = set()
    monthly = V.VARIANTS[name]["entry"] in V.MONTHLY
    for k, cut in enumerate((V.day_ms("2010-09-15") + 13 * HOUR, V.day_ms("2011-04-07") + 2 * HOUR, V.day_ms("2011-11-20"))):
        D2, H42 = a_frames(perturb(h, cut, k))
        got = a_sigs(name, D2, H42)
        assert [x for x in got if x[0] <= cut] == [x for x in full if x[0] <= cut], (name, cut)
        covered |= {x[1] for x in full if x[0] <= cut}
        # t'de kesme: yalnız t'ye kadar kapanmış saatlik barlar
        if monthly:
            mt = V.month_table(D)
            ends = sorted(int(D["cms"][r["last"]]) for r in mt.values() if int(D["cms"][r["last"]]) <= cut)
            t = ends[-1]
        else:
            t = cut
        ht = h[h["timestamp"] + HOUR <= t]
        D3, H43 = a_frames(ht)
        cut_sigs = [x[:3] for x in a_sigs(name, D3, H43) if x[0] <= t]
        assert cut_sigs == [x[:3] for x in full if x[0] <= t], (name, t)
    assert covered == {"LONG", "SHORT"}, name


@pytest.mark.parametrize("name", list(V.B_NAMES))
def test_no_lookahead_family_b_perturbing_the_future_and_truncating_at_t(name):
    h, _ = V.synthetic_paxg_hourly("2023-06-01", "2024-12-31", 12)
    H, Dd = b_frames(h)
    full = b_sigs(name, H, Dd, "2023-06-01", "2024-12-31")
    sides = set()
    for k, cut in enumerate((V.day_ms("2024-03-11") + 5 * HOUR, V.day_ms("2024-09-01") + 22 * HOUR + 30 * 60_000)):
        H2, Dd2 = b_frames(perturb(h, cut, k))
        assert [x for x in b_sigs(name, H2, Dd2, "2023-06-01", "2024-12-31") if x[0] <= cut] == [x for x in full if x[0] <= cut]
        ht = h[h["timestamp"] + HOUR <= cut]
        H3, Dd3 = b_frames(ht)
        assert [x for x in b_sigs(name, H3, Dd3, "2023-06-01", "2024-12-31") if x[0] <= cut] == [x for x in full if x[0] <= cut]
        sides |= {x[1] for x in full if x[0] <= cut}
    assert sides == {"LONG", "SHORT"}


# ---------------------------------------------------------------------------- çıkışlar, fonlama vekili
def _a_rows(seed=13, names=V.A_NAMES, start="2009-01-01", end="2012-12-31"):
    h = V.synthetic_duka_hourly(start, end, seed)
    D, H4 = a_frames(h)
    per = {"IS": V.win_ms(start, "2010-12-31"), "OOS": (V.win_ms("2011-01-01", end))}
    per = {"IS": (per["IS"][0], per["OOS"][0]), "OOS": per["OOS"]}
    counts = V.Counts()
    rows = []
    mt = V.month_table(D)
    for n in names:
        F = D if V.VARIANTS[n]["bars"] == "1d" else H4
        rows += V.a_variant_rows(n, F, D, mt, CFG, per, counts, sig_of=V.daily_sig_lookup(D, F, same_seg=True))
    return h, D, H4, mt, per, counts, rows


def test_rule_exits_month_end_hold_and_funding_proxy_follow_the_document():
    h, D, H4, mt, per, counts, rows = _a_rows()
    seen = set()
    for e in rows:
        if e["family"] != V.FAMILY:
            continue
        v = V.VARIANTS[e["name"]]
        F = D if v["bars"] == "1d" else H4
        s = 1.0 if e["side"] == "LONG" else -1.0
        gi, gx = e["i"], e["i"] + e["hold"]
        assert e["entry_ms"] == F["ts"][gi + 1] and e["entry_px"] == F["open"][gi + 1] and math.isclose(e["risk_px"], s * (e["entry_px"] - e["stop"]))
        assert e["n8"] == e["exit_ms"] // (8 * HOUR) - e["entry_ms"] // (8 * HOUR)
        assert math.isclose(e["r_fon"], e["r"] - s * 1e-4 * e["n8"] * e["entry_px"] / e["risk_px"], rel_tol=1e-12)
        kind = v["exit"]["kind"]

        def trig(k, F=F, s=s, v=v, kind=kind):
            if kind == "sign":
                return F["mom28"][k] <= 0 if s > 0 else F["mom28"][k] >= 0
            n_ = v["exit"]["n"]
            return F["close"][k] < F[f"lo{n_}"][k] if s > 0 else F["close"][k] > F[f"hi{n_}"][k]
        if kind in ("channel", "channel20", "sign") and e["exit_kind"] == "RULE":
            seen.add((e["name"], "RULE"))
            assert e["exit_ms"] == F["ts"][gx]
            assert trig(gx - 1) and not any(trig(k) for k in range(gi + 1, gx - 1)), e
            assert all((F["low"][k] > e["stop"]) if s > 0 else (F["high"][k] < e["stop"]) for k in range(gi + 1, gx))
        if e["exit_kind"] == "STOP":
            seen.add((e["name"], "STOP"))
            assert e["exit_ms"] == F["cms"][gx] and ((F["low"][gx] <= e["stop"]) if s > 0 else (F["high"][gx] >= e["stop"]))
        if e["exit_kind"] == "MONTH_END":
            seen.add((e["name"], "MONTH_END"))
            m = int(D["mi"][gi])
            nxt = mt[m + 1]
            assert e["entry_ms"] == D["ts"][nxt["first"]] and e["exit_ms"] == D["cms"][nxt["last"]] and e["hold"] == nxt["n"] == e["max_bars"]
            assert gi == mt[m]["last"] and e["exit_reason"] == "TIME"
        if e["exit_kind"] == "TIME" and v["exit"]["kind"] != "month_end":
            assert e["hold"] == v["exit"]["max_bars"] and e["exit_ms"] == F["cms"][gx]
    assert {("A_DONCH_20_10", "RULE"), ("A_DONCH_55_20", "RULE"), ("A_TSMOM_28", "RULE"), ("A_TSMOM_1M", "MONTH_END"),
            ("A_4H_DONCH_D200", "RULE"), ("A_DONCH_20_10", "STOP")} <= seen
    # channel20 gerçekten lo20/hi20 kullanır (lo10 değil): aynı olay channel(10) ile daha erken ya da aynı çıkar
    e55 = [e for e in rows if e["family"] == V.FAMILY and e["name"] == "A_DONCH_55_20" and e["exit_kind"] == "RULE"]
    diff = 0
    for e in e55:
        _, r10, _ = V.simulate_one(D, e["i"], e["side"], e["stop"], {"kind": "channel", "n": 10, "max_bars": 300}, D["atr"][e["i"]], CFG,
                                   family=V.FAMILY, name="x", cell="LONG", period="IS")
        if r10 is not None:
            assert r10["exit_ms"] <= e["exit_ms"]
            diff += r10["exit_ms"] < e["exit_ms"]
    assert diff > 0


def test_weekend_trades_enter_at_S_exit_at_S_plus_N_and_pay_three_settlements_overnight():
    h, d = V.synthetic_paxg_hourly("2024-01-01", "2024-12-31", 14)
    H, Dd = b_frames(h, d)
    weeks = V.weekend_table(H, Dd, *V.win_ms("2024-01-05", "2024-12-31"))
    per = {"IS": V.win_ms("2024-01-05", "2024-12-31")}
    for name in V.B_NAMES:
        rows = V.b_variant_rows(name, H, Dd, weeks, CFG, per, V.Counts(), symbol=V.PAXG, info=False)
        N = V.VARIANTS[name]["hold_h"]
        real = [e for e in rows if e["family"] == V.FAMILY]
        assert real and all(e["t_ms"] == e["entry_ms"] and V.weekday(e["t_ms"] // DAY) == 6 for e in real)
        for e in real:
            if e["exit_kind"] == "HOLD":
                assert e["exit_ms"] == e["t_ms"] + N * HOUR
                if N == 23:
                    assert e["n8"] == 3                                     # Pzt 00:00, 08:00, 16:00 UTC
            else:
                assert e["exit_kind"] == "STOP" and e["exit_ms"] <= e["t_ms"] + N * HOUR
            w = next(w for w in weeks if w["S"] == e["t_ms"])
            z = (H["close"][w["iS"]] - H["close"][w["iF"]]) / w["atr_d"]
            assert (e["side"] == "LONG") == (z < 0) and math.isclose(abs(e["stop"] - H["close"][w["iS"]]), w["atr_d"])
            assert abs(z) > 0 if name == "B_WKND_REV_ALL" else abs(z) >= V.VARIANTS[name]["z_min"]


def test_weekend_skips_missing_bars_gaps_volume_zero_and_data_end():
    h, d = V.synthetic_paxg_hourly("2024-01-01", "2024-03-31", 15)
    ms = V.day_ms
    drop = {ms("2024-01-14") + 23 * HOUR + 3 * HOUR}                               # tutuş barı eksik (S + 3 saat)
    gap = (h["timestamp"] >= ms("2024-01-26") + 22 * HOUR) & (h["timestamp"] < ms("2024-01-28") + 22 * HOUR)   # F..S − 1 saat arası
    h2 = h[~h["timestamp"].isin(drop) & ~gap].copy()
    h2.loc[h2["timestamp"] == ms("2024-02-09") + 21 * HOUR, "volume"] = 0.0       # F − 1 saat barı hacimsiz (EST: F = 22:00)
    h2 = h2[h2["timestamp"] < ms("2024-03-24") + 22 * HOUR + 6 * HOUR]            # 03-22 hafta sonunda (EDT: S = 22:00) veri S + 5'te biter
    H, Dd = b_frames(h2, d)
    weeks = V.weekend_table(H, Dd, *V.win_ms("2024-01-01", "2024-03-31"))
    st = {w["friday"]: V.week_status(w, 23, H) for w in weeks}
    assert st["2024-01-12"] == "EKSİK_BAR" and st["2024-01-26"] == "BOŞLUK" and st["2024-02-09"] == "HACİMSİZ"
    assert st["2024-01-05"] == "ATR_YOK" and st["2024-03-22"] == "" and st["2024-03-29"] == "VERİ_SONU"   # ilk hafta: ATR14 yok
    rows = V.b_variant_rows("B_WKND_REV_ALL", H, Dd, weeks, CFG, {"ALL": V.win_ms("2024-01-01", "2024-03-31")}, c := V.Counts(),
                            symbol=V.PAXG, info=False)
    assert c.get(V.FAMILY, "B_WKND_REV_ALL", ["LONG", "SHORT"])["skipped"].get("KESİLDİ") == 1
    s22 = next(w["S"] for w in weeks if w["friday"] == "2024-03-22")
    assert not any(e["t_ms"] == s22 for e in rows if e["family"] == V.FAMILY)
    hw = c.get(V.FAMILY, "B_WKND_REV_ALL", ["HAFTA"])["skipped"]
    assert hw["EKSİK_BAR"] == 1 and hw["BOŞLUK"] == 1 and hw["HACİMSİZ"] == 1 and hw["ATR_YOK"] >= 1


# ---------------------------------------------------------------------------- plasebolar
def test_matched_placebo_takes_five_crc_chosen_bars_of_the_same_period_side_and_holding():
    h, D, H4, mt, per, counts, rows = _a_rows(names=("A_DONCH_20_10", "A_4H_DONCH_D200"))
    for name in ("A_DONCH_20_10", "A_4H_DONCH_D200"):
        F = D if V.VARIANTS[name]["bars"] == "1d" else H4
        real = [e for e in rows if e["family"] == V.FAMILY and e["name"] == name]
        pl = V.matched_placebos(name, real, F, per, V.Counts())
        assert real and len(pl) == 5 * len(real)
        mult = V.VARIANTS[name]["stop_atr"]
        k = 0
        for r in sorted(real, key=lambda x: (x["t_ms"], x["side"])):
            H = V.hold_of(r)
            a, b = per[r["period"]]
            with np.errstate(invalid="ignore"):
                C = np.flatnonzero((F["segpos"] >= 210) & np.isfinite(F["atr"]) & (F["atr"] > 0) & (F["cms"] >= a) & (F["cms"] < b))
            C = C[C + 1 + H <= F["seg_end"][C]]
            for j in range(1, 6):
                c, side, stop, spec, tag = pl[k]
                k += 1
                want = C[zlib.crc32(f"XAUUSD|{F['tf']}|{name}|{r['side']}|{r['ts_i']}|{j}".encode()) % len(C)]
                s = 1.0 if side == "LONG" else -1.0
                assert (c, side, tag, spec) == (want, r["side"], r["side"], {"kind": "hold", "bars": H})
                assert math.isclose(stop, F["close"][c] - s * mult * F["atr"][c])
        prow = [e for e in rows if e["family"] == V.PLACEBO_FAMILY and e["name"] == "PLACEBO_" + name]
        assert prow and all(e["exit_kind"] in ("HOLD", "STOP") for e in prow)
        hold_ok = [e for e in prow if e["exit_kind"] == "HOLD"]
        assert all(e["exit_ms"] == F["ts"][e["i"] + 1 + e["max_bars"]] for e in hold_ok)
    # İKİ YÖN hücresinin plasebosu = iki yönün eşleri; LONG hücresininki yalnız LONG eşleri
    real, pl, inf = V.cell_rows(rows, "A_DONCH_20_10", V.BOTH_CELL)
    assert {e["cell"] for e in pl} == {"LONG", "SHORT"} and {e["side"] for e in real} == {"LONG", "SHORT"} and inf
    real, pl, _ = V.cell_rows(rows, "A_DONCH_20_10", "LONG")
    assert {e["cell"] for e in pl} == {"LONG"} and {e["side"] for e in real} == {"LONG"}


def test_monthly_placebo_rate_and_sign_randomisation_and_weekend_placebo():
    h = V.synthetic_duka_hourly("2009-01-01", "2014-12-31", 16)
    D, H4 = a_frames(h)
    mt = V.month_table(D)
    sigs, cands, why = V.monthly_signals("A_TSMOM_3M", D, mt)
    assert len(cands) >= len(sigs) > 30 and set(why) <= {"ISINMA", "GERİ_BAKIŞ_AYI", "AY_KULLANILAMAZ", "SONRAKİ_AY_KULLANILAMAZ"}
    assert why["AY_KULLANILAMAZ"] == 1 and why["SONRAKİ_AY_KULLANILAMAZ"] == 1          # son saatler 2015-01-01 işlem gününe düşer
    pl = V.monthly_placebos("A_TSMOM_3M", sigs, cands, D)
    for side in ("LONG", "SHORT"):
        p = sum(1 for x in sigs if x[1] == side) / len(cands)
        want = [gi for gi, _sp, _m in cands if zlib.crc32(f"XAUUSD|1M|A_TSMOM_3M|{side}|{int(D['ts'][gi])}".encode()) / 2 ** 32 < p]
        assert [x[0] for x in pl if x[4] == side] == want
    both = [x for x in pl if x[4] == "İKİ"]
    assert [x[0] for x in both] == [x[0] for x in sigs]
    for gi, side, stop, spec, _ in both:
        u = zlib.crc32(f"XAUUSD|1M|A_TSMOM_3M|İKİ|{int(D['ts'][gi])}".encode()) / 2 ** 32
        s = 1.0 if side == "LONG" else -1.0
        assert (side == "LONG") == (u < 0.5) and math.isclose(stop, D["close"][gi] - s * 5 * D["atr"][gi]) and spec["kind"] == "month_end"
    assert {x[1] for x in both} == {"LONG", "SHORT"}
    # aylık: her ay en çok bir karar; i_m ayın son 3 Pzt–Cum günü içinde
    for gi, *_ in sigs:
        m = int(D["mi"][gi])
        assert gi == mt[m]["last"] and D["dnum"][gi] >= V.third_last_weekday(m)
    # hafta sonu: LONG p = LONG sinyal / uygun hafta; İKİ YÖN gerçek sinyal haftalarında işaret
    hp, dp = V.synthetic_paxg_hourly("2023-01-01", "2024-12-31", 17)
    H, Dd = b_frames(hp, dp)
    weeks = V.weekend_table(H, Dd, *V.win_ms("2023-01-01", "2024-12-31"))
    elig, bs, _ = V.weekend_signals("B_WKND_REV_ALL", H, weeks)
    rows = V.b_variant_rows("B_WKND_REV_ALL", H, Dd, weeks, CFG, {"ALL": V.win_ms("2023-01-01", "2024-12-31")}, c := V.Counts(),
                            symbol=V.PAXG, info=False)
    p = sum(1 for x in bs if x[1] == "LONG") / len(elig)
    want = [w["S"] for w in elig if zlib.crc32(f"PAXGUSDT|1h|B_WKND_REV_ALL|LONG|{int(H['ts'][w['iS']])}".encode()) / 2 ** 32 < p]
    got = [e["t_ms"] for e in rows if e["family"] == V.PLACEBO_FAMILY and e["cell"] == "LONG"]
    sk = c.get(V.PLACEBO_FAMILY, "PLACEBO_B_WKND_REV_ALL", ["LONG"])
    assert set(got) <= set(want) and len(got) + sum(sk["skipped"].values()) == len(want) and sk["signals"] == len(want)
    two = {e["t_ms"]: e for e in rows if e["family"] == V.PLACEBO_FAMILY and e["cell"] == "İKİ"}
    for w, _sd, _st in bs:
        if w["S"] in two:
            u = zlib.crc32(f"PAXGUSDT|1h|B_WKND_REV_ALL|İKİ|{int(H['ts'][w['iS']])}".encode()) / 2 ** 32
            assert (two[w["S"]]["side"] == "LONG") == (u < 0.5) and two[w["S"]]["entry_ms"] == w["S"]


# ---------------------------------------------------------------------------- dönemler ve hüküm
def test_fixed_date_periods_and_judge_equals_aggregate_with_the_same_cut():
    per = V._periods_ms(V.WINDOWS["A"])
    oos0 = V.day_ms("2014-01-01")
    assert per["IS"] == (V.day_ms("2006-01-01"), oos0) and per["OOS"] == (oos0, V.day_ms("2020-08-01"))
    assert V.period_of(oos0 - 1, per) == "IS" and V.period_of(oos0, per) == "OOS" and V.period_of(V.day_ms("2020-08-01"), per) is None
    rnd = np.random.default_rng(3)
    verdicts = []
    t = V.day_ms("2020-01-01") + np.sort(rnd.integers(0, 700, 400)) * DAY + 17 * HOUR
    for mu in (0.5, 0.05, -0.3):
        real = [{"symbol": "S", "tf": "1d", "family": V.FAMILY, "name": "X", "side": "LONG", "t_ms": int(x), "r": float(mu + rnd.normal(0, 0.4)),
                 "cost_r": 0.1, "ctx": {"hacim": "bilinmiyor"}} for x in t]
        plac = [{"symbol": "S", "tf": "1d", "family": "placebo", "name": "PLACEBO_X", "side": "LONG", "t_ms": int(x),
                 "r": float(rnd.normal(0, 0.4)), "cost_r": 0.1, "ctx": {"hacim": "bilinmiyor"}} for x in t[::2]]
        agg = L.aggregate(real + plac, CFG)
        g = next(x for x in agg["groups"] if x["name"] == "X" and x["context"] == "HEPSİ")
        cut = agg["cutoff_ms"]["1d"]
        for e in real + plac:
            e["period"] = "IS" if e["t_ms"] <= cut else "OOS"
        j = V.judge(real, plac, CFG)
        assert (j["IS"], j["OOS"], j["vs_placebo"], j["verdict"], j["verdict_strict"], j["replicated"]) == \
               (g["IS"], g["OOS"], g["vs_placebo"], g["verdict"], g["verdict_strict"], g["replicated"]), mu
        verdicts.append((j["verdict"], j["verdict_strict"]))
    assert verdicts[0] == (L.V_STRONG, L.V_STRONG) and verdicts[2][0] == L.V_LOSS


# ---------------------------------------------------------------------------- aylık ölçü, daha yüksek risk, C, sonuç
def _mrows(months, val=1.0):
    out = []
    for k, mk in enumerate(months):
        t = V.day_ms(f"{mk}-15") + 21 * HOUR
        out.append({"t_ms": t, "r_fon": val, "r": val + 0.1, "entry_ms": t + HOUR, "exit_ms": t + 3 * DAY, "period": "OOS",
                    "risk_px": 20.0, "entry_px": 1000.0, "sig_ann": 0.1, "cost_r": 0.1})
    return out


def test_monthly_target_window_is_78_months_for_a_and_33_for_b_and_uses_r_fon():
    for fam, n, first, last, edge in (("A", 78, "2014-01", "2020-06", "2020-07"), ("B", 33, "2024-01", "2026-09", None)):
        w = V.WINDOWS[fam]
        oos0, end = V._periods_ms(w)["OOS"]
        months = G.month_keys(oos0, end - 1)
        rows = _mrows(months, 0.5)
        mon = G.monthly_stats(rows, oos0 - 1, end - 1, iters=200, key="r_fon", open_end_ms=V.day_ms(w["open_end"]))
        assert (mon["months"], mon["first_month"], mon["last_month"]) == (n, first, last), fam
        assert mon["mean_pct_month_exact"] == 0.25 and (edge is None or edge in mon["edge_months"])
        assert not G.meets_target(mon, [L.V_STRONG])
        rows = _mrows(months, 2.0)
        mon = G.monthly_stats(rows, oos0 - 1, end - 1, iters=200, key="r_fon", open_end_ms=V.day_ms(w["open_end"]))
        assert G.meets_target(mon, [L.V_STRONG]) and not G.meets_target(mon, [L.V_WEAK])


def test_higher_risk_line_prints_numbers_only_for_strict_strong_and_flags_caps():
    months = [f"2015-{m:02d}" for m in range(1, 13)]
    rows = _mrows(months, 0.5)
    mon = {"mean_pct_month_exact": 0.25, "ci95_pct_month": [0.1, 0.4], "concurrency": {"max": 4},
           "monthly_r": {k: 0.5 for k in months}}
    assert V.higher_risk(L.V_WEAK, mon, rows, rows, 0.1) == {"text": "kenar yok"}
    assert V.higher_risk(L.V_STRONG, {**mon, "mean_pct_month_exact": -0.1}, rows, rows, 0.1)["text"] == "fonlama sonrası kenar yok"
    hr = V.higher_risk(L.V_STRONG, mon, rows, rows, 0.1)
    assert hr["text"] == "ortalama tahmin 2.00% riskte +%1/ay'a karşılık gelir; %95 aralık [+0.40, +1.60]; ulaşılacağı garanti değildir"
    assert math.isclose(hr["X"], 2.0) and not any("profil" in f for f in hr["flags"])
    assert hr["total_open_risk_pct"]["value"] == 8.0 and hr["total_open_risk_pct"]["flag"]
    assert math.isclose(hr["excess"]["mean_r_month"], 0.4) and math.isclose(hr["excess"]["drift_share"], 0.2)
    assert math.isclose(hr["leverage"]["max"], 1.0) and hr["drawdown_pct"]["all"] == 0.0
    hr3 = V.higher_risk(L.V_STRONG, {**mon, "mean_pct_month_exact": 0.1}, rows, rows, 0.6)
    assert "profil tavanını aşar — uygulanamaz" in hr3["flags"] and hr3["excess"]["text"] == "plasebo üstü fazla yok"
    dd = [{**r, "r_fon": x, "exit_ms": r["exit_ms"] + k} for k, (r, x) in enumerate(zip(rows, [1, -2, -1, 3, -4, 1] * 2))]
    assert V.max_drawdown(dd) == 6.0 and V.max_drawdown([{**dd[0], "r_fon": -1.5}]) == 1.5


def test_c_sizing_caps_total_weight_in_entry_order_and_reports_only_for_strict_strong():
    t0 = V.day_ms("2015-03-02")
    rows = [{"t_ms": t0 + k * HOUR, "entry_ms": t0 + k * HOUR, "exit_ms": t0 + 10 * DAY, "r_fon": 1.0, "risk_px": 10.0, "entry_px": 1000.0,
             "sig_ann": 0.1, "cost_r": 0.1, "period": "OOS"} for k in range(3)] + \
           [{"t_ms": t0 + 20 * DAY, "entry_ms": t0 + 20 * DAY, "exit_ms": t0 + 21 * DAY, "r_fon": 1.0, "risk_px": 10.0, "entry_px": 1000.0,
             "sig_ann": None, "cost_r": 0.1, "period": "OOS"}]
    assert V.c_sizing(L.V_WEAK, rows, CFG, 0, (V.mi_of(2015, 1), V.mi_of(2015, 12)), (V.mi_of(2015, 1), V.mi_of(2015, 12))) == {"text": "kenar yok"}
    c = V.c_sizing(L.V_STRONG, rows, CFG, 0, (V.mi_of(2015, 1), V.mi_of(2015, 12)), (V.mi_of(2015, 1), V.mi_of(2015, 12)))
    assert c["trimmed"] == 1 and c["no_sigma"] == 1 and c["uncapped_max_total_w"] == 3.0 and c["uncapped_flag"]
    assert math.isclose(c["oos_mean_pct_month"], 2 * 1.0 * 0.01 * 100 / 12)                      # Σ f × g × 100, f = 1 + 1 + 0


def _ccell(name, cl, main, strict, meets, fam="A", verdict=None):
    return {"name": name, "cell": cl, "main": main, "verdict": verdict or strict, "verdict_strict": strict, "meets_target": meets,
            "family": fam, "note": "", "monthly": {"mean_pct_month": 1.2, "ci95_pct_month": [0.3, 2.1], "share_months_ge_target": 0.55},
            "higher_risk": {"drawdown_r": {"all": 12.0, "oos": 6.0}}}


def _vrow(name, cl, n=12, net=0.4, gross=0.5, fund=-0.1, series="XAUUSDT vadeli"):
    return {"name": name, "cell": cl, "series": series, "all": {"n": n, "mean_r": net}, "gross": {"mean_r": gross}, "funding_r_mean": fund,
            "note": ""}


def test_conclusion_follows_the_recommendation_rule():
    rep = {"A": {"status": "koşuldu", "cells": [_ccell("A_DONCH_20_10", "LONG", True, L.V_STRONG, True)], "candidate_rate": {"placebo": 0.0}},
           "B": {"status": "koşuldu", "cells": [], "candidate_rate": {"placebo": 0.0}},
           "venue": {"rows": [_vrow("A_DONCH_20_10", "LONG"), _vrow("A_DONCH_20_10", "İKİ YÖN", net=-3.0)], "notes_evaluated": True}}
    c = V.conclusion(rep)
    assert c["recommend"] == ["A_DONCH_20_10 LONG"]
    line = c["lines"][0]
    # öneri satırı belgenin istediği içeriği taşır: %95 aralık, en derin düşüş, mekân fonlama etkisi, kazanan laneti
    assert line.startswith("ÖNERİ (yalnız PAPER, yalnız-kayıt; sahip onayı olmadan hiçbir şey açılmaz): A_DONCH_20_10 LONG — ")
    for part in ("%95 aralık [+0.30, +2.10]", "%0,5 riskte bütün hüküm serisi %6.0, doğrulama %3.0",
                 "XAUUSDT vadeli: n 12, ort.R brüt +0.500 → net +0.400 (gerçek fonlama ort. -0.100 R)",
                 V.GOLD_V2_REGISTRY["higher_risk"]["curse_tr"], "garanti değildir"):
        assert part in line, part
    assert "-3.000" not in line                                                    # yalnız hücrenin kendi satırları
    # (iii) ailenin plasebo aday oranı 0 değil ya da TANIMSIZ → öneri yok, nedeni açık
    rep["A"]["candidate_rate"]["placebo"] = 0.1
    c = V.conclusion(rep)
    assert not c["recommend"] and "öneri şartı tutmadı" in c["lines"][0] and "plasebo aday oranı 0.1" in c["lines"][0]
    rep["A"]["candidate_rate"]["placebo"] = None
    c = V.conclusion(rep)
    assert not c["recommend"] and "hesaplanamadı" in c["lines"][0] and "(iii)" in c["lines"][0]
    rep["A"]["candidate_rate"]["placebo"] = 0.0
    # (iv) mekân satırı yok (venue koşulmadı) → ölçülemedi, öneri yok; 'mekânda tutmadı' notu → öneri yok
    c = V.conclusion({**rep, "venue": None})
    assert not c["recommend"] and "venue bölümü koşulmadı" in c["lines"][0] and "(iv)" in c["lines"][0]
    rep["A"]["cells"][0]["note"] = V.NOTE_VENUE
    c = V.conclusion(rep)
    assert not c["recommend"] and V.NOTE_VENUE in c["lines"][0]
    # ANA hedefin altında; ikincil hücreler: hedefi karşılasa da karşılamasa da yalnız gold_v3, öneri DEĞİL
    rep["A"]["cells"] = [_ccell("A_TSMOM_28", "LONG", True, L.V_STRONG, False), _ccell("A_SMA10M", "LONG", False, L.V_STRONG, True)]
    lines = V.conclusion(rep)["lines"]
    assert any("kenar var, hedefin altında" in x for x in lines) and any("gold_v3" in x and "öneri DEĞİL" in x for x in lines)
    assert any("A_SMA10M LONG (ikincil): sıkı GÜÇLÜ ADAY, hedefi karşılıyor" in x and "zayıf kanıt" in x for x in lines)
    rep["A"]["cells"] = [_ccell("A_DONCH_20_10", "LONG", True, L.V_WEAK, False), _ccell("A_TSMOM_1M", "LONG", False, L.V_STRONG, False),
                         _ccell("A_TSMOM_3M", "LONG", False, L.V_WEAK, False, verdict=L.V_STRONG)]
    rep["B"]["cells"] = [_ccell("B_WKND_REV_ALL", "İKİ YÖN", True, L.V_WEAK, False, fam="B"),
                         _ccell("B_WKND_REV_050", "LONG", False, L.V_STRONG, False, fam="B")]
    lines = V.conclusion(rep)["lines"]
    a1 = next(x for x in lines if x.startswith("A_TSMOM_1M LONG (ikincil)"))
    assert "sıkı GÜÇLÜ ADAY, hedefin altında" in a1 and "ANA hücreleri sıkı GÜÇLÜ ADAY değilken: kanıt değil" in a1 and "gold_v3" in a1
    a3 = next(x for x in lines if x.startswith("A_TSMOM_3M LONG (ikincil)"))
    assert f"standart GÜÇLÜ ADAY (sıkı: {L.V_WEAK})" in a3 and "öneri DEĞİL" in a3
    b1 = next(x for x in lines if x.startswith("B_WKND_REV_050 LONG (ikincil)"))
    assert "tarama yapıntısı, kanıt değil" in b1 and "gold_v3" in b1
    assert lines[-1] == V.NO_TARGET_TR and not any(x.startswith("B_WKND_REV_ALL") or x.startswith("A_DONCH_20_10") for x in lines)
    rep["A"] = {"status": "yapılamadı (kapsama)", "cells": []}
    rep["B"]["cells"] = []
    lines = V.conclusion(rep)["lines"]
    assert lines[0].startswith("Aile A: yapılamadı (kapsama)") and lines[-1] == V.NO_TARGET_TR


def test_venue_note_marks_only_standard_strong_cells_with_n20_and_nonpositive_net():
    cA = {"name": "A_DONCH_20_10", "cell": "LONG", "verdict": L.V_STRONG, "verdict_strict": L.V_WEAK, "note": "eski"}
    cA2 = {"name": "A_TSMOM_28", "cell": "LONG", "verdict": L.V_WEAK, "verdict_strict": L.V_WEAK, "note": ""}
    cB = {"name": "B_WKND_REV_ALL", "cell": "İKİ YÖN", "verdict": L.V_STRONG, "verdict_strict": L.V_STRONG, "note": ""}
    rows = [_vrow("A_DONCH_20_10", "LONG", n=20, net=0.0), _vrow("A_DONCH_20_10", "LONG", n=40, net=0.2, series="PAXGUSDT vadeli"),
            _vrow("A_TSMOM_28", "LONG", n=50, net=-0.5), _vrow("B_WKND_REV_ALL", "İKİ YÖN", n=19, net=-1.0),
            _vrow("B_WKND_REV_ALL", "LONG", n=60, net=-1.0)]
    rep = {"A": {"cells": [cA, cA2]}, "B": {"cells": [cB]}, "venue": {"rows": rows}}
    V.apply_notes(rep)
    # STANDART hüküm GÜÇLÜ ADAY + bir vadeli satırda n ≥ 20 ve net ≤ 0 → not (sıkı hüküm aranmaz); n = 19, ZAYIF hüküm ya da başka
    # hücrenin satırı not düşürmez
    assert (cA["note"], cA2["note"], cB["note"]) == (V.NOTE_VENUE, "", "")
    assert [r["note"] for r in rows] == [V.NOTE_VENUE, "", "", "", ""] and rep["venue"]["notes"] == [rows[0]] and rep["venue"]["notes_evaluated"]
    rep2 = {"A": {"cells": [{**cA, "note": "eski"}]}}
    V.apply_notes(rep2)
    assert rep2["A"]["cells"][0]["note"] == ""                                     # mekân bölümü yoksa not yok (ve eski not silinir)


def _md_cell(name, cl, main, fam="A"):
    months = [f"2015-{m:02d}" for m in range(1, 13)]
    rows = _mrows(months, 0.5)
    mon = {"months": 12, "mean_pct_month": 0.25, "mean_pct_month_exact": 0.25, "ci95_pct_month": [0.1, 0.4], "share_months_ge_target": 0.0,
           "concurrency": {"max": 4, "mean": 0.5}, "monthly_r": {k: 0.5 for k in months}}
    return {"name": name, "cell": cl, "main": main, "family": fam, "IS": {"n": 40, "mean_r": 0.3}, "OOS": {"n": 30, "mean_r": 0.2},
            "verdict": L.V_STRONG, "verdict_strict": L.V_STRONG, "meets_target": False, "monthly": mon,
            "higher_risk": V.higher_risk(L.V_STRONG, mon, rows, rows, 0.1), "concurrency_all": {"max": 5, "mean": 0.75}, "note": ""}


def test_markdown_prints_higher_risk_numbers_concurrency_month_skips_and_the_data_disclosure():
    rep = {"version": "gold_v2", "registry_sha": PINNED_SHA,
           "A": {"status": "koşuldu", "cells": [_md_cell("A_DONCH_20_10", "LONG", True), _md_cell("A_TSMOM_1M", "LONG", False)],
                 "counts": {"gold2|A_TSMOM_1M|AY": {"skipped": {"SONRAKİ_AY_KULLANILAMAZ": 2, "ISINMA": 1}}}},
           "venue": {"rows": []}}
    md = V.render_md(rep)
    for name in ("A_DONCH_20_10", "A_TSMOM_1M"):                                   # ANA ve ikincil sıkı GÜÇLÜ ADAY hücreleri
        line = next(x for x in md.splitlines() if x.startswith(f"- {name} LONG — daha yüksek risk"))
        for part in ("X = %2.00", "toplam açık risk = en çok 4 eşzamanlı işlem × X = %8.00 (PAPER_RESEARCH tavanı %6.0, öğrenme modu %100.0) — İŞARETLİ",
                     "plasebo üstü fazlayla X = 2.50%", "aylık fazla R 0.400", "yön kayması payı 0.20",
                     "en derin düşüş X riskte bütün hüküm serisi %0.0, doğrulama %0.0", "ima edilen kaldıraç medyan 1.00, en çok 1.00",
                     V.GOLD_V2_REGISTRY["higher_risk"]["curse_tr"]):
            assert part in line, (name, part)
    assert "- A_TSMOM_1M aylık karar atlamaları (bilgi; bütün hüküm serisi): SONRAKİ_AY_KULLANILAMAZ 2 · SONRAKİ_AY_YOK 0" in md
    assert "### Eşzamanlı açık işlem (bilgi)" in md and "| A_DONCH_20_10 | LONG | 5 / 0.75 | 4 / 0.50 |" in md
    assert "## Ön kayıttan önce veriden görülenler" in md and all(x in md for x in V.DISCLOSURE_TR) and "2010-01 saatlik" in md
    assert V.VENUE_FUNDING_NOTE_TR in md and "ESŞ_ADAY_YOK" in md and "'EŞ_ADAY_YOK'tur" in md
    # sıkı GÜÇLÜ ADAY olmayan hücrede sayı basılmaz
    weak = {**_md_cell("A_TSMOM_28", "LONG", True), "verdict": L.V_WEAK, "verdict_strict": L.V_WEAK, "higher_risk": {"text": "kenar yok"}}
    md2 = V.render_md({"A": {"status": "koşuldu", "cells": [weak]}})
    assert "daha yüksek risk (bilgi" not in md2 and "kenar yok" in md2


# ---------------------------------------------------------------------------- bilgi satırları ve sayımlar (hükme girmez)
def test_info_placebo_a_draws_every_warm_bar_with_the_gold_v1_key_and_stays_out_of_the_verdict():
    h, D, H4, mt, per, counts, rows = _a_rows(names=("A_DONCH_20_10",))
    sigs = V.rule_signals("A_DONCH_20_10", D, D)
    pl = V.info_placebos("A_DONCH_20_10", sigs, D)
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero((D["segpos"] >= 210) & np.isfinite(D["atr"]) & (D["atr"] > 0))
    for side, s in (("LONG", 1.0), ("SHORT", -1.0)):
        p = sum(1 for x in sigs if x[1] == side) / len(cand)
        want = [int(gi) for gi in cand if zlib.crc32(f"XAUUSD|1d|A_DONCH_20_10|{side}|{int(D['ts'][gi])}".encode()) / 2 ** 32 < p]
        got = [x for x in pl if x[1] == side]
        assert want and [x[0] for x in got] == want
        for gi, _sd, stop, spec, tag in got:
            assert tag == side and spec == V.VARIANTS["A_DONCH_20_10"]["exit"] and math.isclose(stop, D["close"][gi] - s * 2.0 * D["atr"][gi])
    real, plc, inf = V.cell_rows(rows, "A_DONCH_20_10", "LONG")
    assert inf and {e["family"] for e in inf} == {V.INFO_FAMILY} and {e["family"] for e in plc} == {V.PLACEBO_FAMILY}
    ic = counts.get(V.INFO_FAMILY, "INFO_A_DONCH_20_10", ["LONG", "SHORT"])
    assert ic["signals"] == len(pl) and ic["trades"] == sum(1 for e in rows if e["family"] == V.INFO_FAMILY)


def test_info_placebo_b_skips_missing_bars_and_volume_zero_hours():
    h, d = V.synthetic_paxg_hourly("2024-01-01", "2024-06-30", 22)
    rnd = np.random.default_rng(5)
    h = h.drop(index=rnd.choice(np.arange(24 * 20, len(h)), 40, replace=False)).reset_index(drop=True)
    h.loc[rnd.choice(np.arange(len(h)), 300, replace=False), "volume"] = 0.0
    H, Dd = b_frames(h, d)
    name, N = "B_WKND_REV_ALL", 23
    sigs = [(None, "LONG", 0.0)] * 300 + [(None, "SHORT", 0.0)] * 200          # yalnız oran için (p = sinyal / aday)
    c = V.Counts()
    rows = V.b_info_rows(name, H, Dd, sigs, CFG, {"ALL": V.win_ms("2024-01-01", "2024-06-30")}, c, symbol=V.PAXG)
    jd = np.searchsorted(Dd["cms"], H["cms"], side="right") - 1
    atr_d = np.where(jd >= 0, Dd["atr"][np.maximum(jd, 0)], np.nan)
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero(np.isfinite(atr_d) & (atr_d > 0))
    drawn = 0
    for side, k in (("LONG", 300), ("SHORT", 200)):
        drawn += sum(1 for gi in cand if zlib.crc32(f"PAXGUSDT|1h|{name}|{side}|{int(H['ts'][gi])}".encode()) / 2 ** 32 < k / len(cand))
    ic = c.get(V.INFO_FAMILY, "INFO_" + name, ["LONG", "SHORT"])
    sk = ic["skipped"]
    assert sk["EKSİK_BAR"] > 0 and sk["HACİMSİZ"] > 0
    assert ic["signals"] + sk["EKSİK_BAR"] + sk["HACİMSİZ"] == drawn            # her çekilen saat ya olay ya da sayılı atlama
    assert rows and {e["family"] for e in rows} == {V.INFO_FAMILY}
    for e in rows:
        gi = e["i"]
        assert H["volume"][gi] > 0 and H["ts"][gi + 1 + N] - H["ts"][gi] == (N + 1) * HOUR and e["exit_ms"] <= H["ts"][gi + 1 + N]
        assert math.isclose(abs(e["stop"] - H["close"][gi]), atr_d[gi])


def test_kesildi_mark_to_market_info_value():
    o = np.array([100.0, 100.0, 101.0, 102.0, 103.0])
    hi, lo, c = o + 1.0, o - 1.0, o + 0.5
    cps = CFG.cost_per_side
    assert math.isclose(V._mtm(o, hi, lo, c, 1.0, 95.0, 5.0, CFG), (103.5 - 100.0 - (100.0 + 103.5) * cps) / 5.0)   # stop yok → son kapanış
    lo1 = lo.copy()
    lo1[1] = 94.0
    assert math.isclose(V._mtm(o, hi, lo1, c, 1.0, 95.0, 5.0, CFG), (95.0 - 100.0 - 195.0 * cps) / 5.0)             # giriş barında stop
    o3, lo3 = o.copy(), lo.copy()
    o3[3], lo3[3] = 90.0, 89.0
    assert math.isclose(V._mtm(o3, hi, lo3, c, 1.0, 95.0, 5.0, CFG), (90.0 - 100.0 - 190.0 * cps) / 5.0)            # boşlukla açılış
    hi2 = hi.copy()
    hi2[2] = 106.0
    assert math.isclose(V._mtm(o, hi2, lo, c, -1.0, 105.0, 5.0, CFG), (100.0 - 105.0 - 205.0 * cps) / 5.0)          # SHORT stop
    assert V._mtm(o[:1], hi[:1], lo[:1], c[:1], 1.0, 95.0, 5.0, CFG) is None
    assert V._mtm(o, hi, lo, c, 1.0, 99.6, 5.0, CFG) is None and V._mtm(o, hi, lo, c, 1.0, 49.0, 5.0, CFG) is None   # risk ≤ 0,1 / > 10 ATR
    assert V._mtm(o, hi, lo, c, 1.0, 95.0, float("nan"), CFG) is None
    # simulate_one: son segmentte veri biterse KESİLDİ ve piyasaya göre R (_mtm) döner; sayım hücreye yazılır
    h = V.synthetic_duka_hourly("2009-01-01", "2010-06-30", 23)
    D, _ = a_frames(h)
    gi = D["n"] - 5
    stop = float(D["close"][gi] - 9.0 * D["atr"][gi])
    why, row, mtm = V.simulate_one(D, gi, "LONG", stop, {"kind": "hold", "bars": 10}, float(D["atr"][gi]), CFG, family=V.FAMILY, name="x",
                                   cell="LONG", period="IS")
    sl = slice(gi, D["n"])
    assert why == "KESİLDİ" and row is None
    assert math.isclose(mtm, V._mtm(D["open"][sl], D["high"][sl], D["low"][sl], D["close"][sl], 1.0, stop, float(D["atr"][gi]), CFG))
    cn = V.Counts()
    cn.result(V.FAMILY, "x", "LONG", why, mtm)
    cn.result(V.FAMILY, "x", "SHORT", "KESİLDİ", None)
    g = cn.get(V.FAMILY, "x", ["LONG", "SHORT"])
    assert g["skipped"]["KESİLDİ"] == 2 and g["kesildi_mtm"] == {"n": 1, "mean_r": round(mtm, 4)}


def test_matched_placebo_counts_no_candidate_as_es_aday_yok():
    h, D, H4, mt, per, counts, rows = _a_rows(names=("A_DONCH_20_10",))
    real = [{"t_ms": 0, "side": "LONG", "exit_reason": "TIME", "hold": 5, "max_bars": 5, "period": "BOŞ", "ts_i": 1},
            {"t_ms": 0, "side": "LONG", "exit_reason": "TIME", "hold": 10 ** 6, "max_bars": 10 ** 6, "period": "OOS", "ts_i": 2},
            {"t_ms": 0, "side": "SHORT", "exit_reason": "TIME", "hold": 5, "max_bars": 5, "period": "OOS", "ts_i": 3}]
    c = V.Counts()
    pl = V.matched_placebos("A_DONCH_20_10", real, D, {**per, "BOŞ": (0, 1)}, c)
    assert len(pl) == 5 and {x[1] for x in pl} == {"SHORT"}
    assert c.get(V.PLACEBO_FAMILY, "PLACEBO_A_DONCH_20_10", ["LONG"])["skipped"] == {"EŞ_ADAY_YOK": 10}
    assert c.get(V.PLACEBO_FAMILY, "PLACEBO_A_DONCH_20_10", ["SHORT"])["skipped"] == {}


def test_duka_frames_count_short_sessions_and_flag_weekend_hours():
    h = V.synthetic_duka_hourly("2009-03-02", "2009-04-30", 24)
    _, _, info0 = V.duka_frames(h)
    assert info0["short_session_list"] == ["2009-05-01"]                          # pencere 2009-04-30 20:00 NY'de biter: 2 saatlik gün
    dn, _, _ = V.ny_parts(h["timestamp"].to_numpy())
    on_wed = np.flatnonzero(dn == V.dnum_of(date(2009, 3, 11)))
    h1 = h.drop(index=on_wed[5:]).reset_index(drop=True)                          # o işlem gününde 5 saat kalır
    _, _, info = V.duka_frames(h1)
    assert info["short_session_days"] == 2 and info["short_session_list"] == ["2009-03-11", "2009-05-01"]
    assert info["weekend_hours"] == 0 and info["weekend_weeks"] == 0 and not info["weekend_flag"]
    sat = V.ny_to_utc_ms(np.full(5, V.dnum_of(date(2009, 3, 14))), 8) + np.arange(5) * HOUR   # Cumartesi 08–12 NY → D Cumartesi
    extra = pd.DataFrame({"timestamp": sat, "open": 1000.0, "high": 1001.0, "low": 999.0, "close": 1000.5, "volume": 1.0})
    h2 = pd.concat([h1, extra]).sort_values("timestamp").reset_index(drop=True)
    D, _, info = V.duka_frames(h2)
    assert info["weekend_hours"] == 5 and info["weekend_weeks"] == 1 and info["weeks"] >= 9 and info["weekend_flag"]
    assert all(V.weekday(d) < 5 for d in D["dnum"])                               # hafta sonu saatleri günlük seriye girmez


def test_day_file_info_reports_month_end_close_difference_medians(tmp_path):
    y = 2010
    recs, last = [], {}
    for d in range(365):
        day = date(y, 1, 1) + pd.Timedelta(days=d).to_pytimedelta()
        px = 1000.0 + d
        if day.weekday() >= 5:
            recs.append((d * 86400, round(px * 1000), round(px * 1000), round(px * 1000), round(px * 1000), 0.0))   # kapalı: atılır
        else:
            recs.append((d * 86400, round(px * 1000), round((px + 1) * 1000), round((px - 2) * 1000), round((px + 2) * 1000), 1.0))
            last[V.mi_of(y, day.month)] = px + 1
    p = V.duka_day_path(tmp_path, y)
    p.parent.mkdir(parents=True)
    p.write_bytes(bi5(recs))
    deltas = [0.25] * 3 + [0.5] * 4 + [-1.0] * 5
    mclose = {mi: last[mi] - dl for mi, dl in zip(sorted(last), deltas)}
    mclose[V.mi_of(2011, 1)] = 5000.0                                              # günlük dosyası yok → karşılaştırılmaz
    info = V.day_file_info(tmp_path, [2010, 2011], mclose)
    assert info["years_read"] == 1 and info["months_compared"] == 12
    assert info["median_diff"] == 0.25 and info["median_abs_diff"] == 0.5


def test_venue_rows_take_signals_from_spot_and_trade_on_venue_bars():
    h, d = V.synthetic_paxg_hourly("2023-01-01", "2024-12-31", 25)
    Ds = V.add_indicators(V.binance_frame(d, "1d", symbol=V.PAXG), ann=V.ANN_BINANCE)
    F4 = V.add_indicators(V.binance_frame(_bars(h, 4 * HOUR), "4h", symbol=V.PAXG))
    sp = [(int(Ds["ts"][gi]), sd) for gi, sd, _st, _x in V.rule_signals("A_DONCH_20_10", Ds, Ds, same_seg=False)]
    v0 = V.day_ms("2024-03-01")
    in_win = [t for t, _ in sp if t >= v0]
    assert len(in_win) >= 3
    hv = h[h["timestamp"] >= v0].copy()
    hv[["open", "high", "low", "close"]] *= 1.001
    dv = daily_of(hv)
    dv = dv[~dv["timestamp"].isin(in_win[:2])].reset_index(drop=True)            # iki sinyal günü vadelide yok
    Dv = V.binance_frame(dv, "1d", symbol="XAUUSDT")
    V1 = V.venue_frame_a(Ds, Dv)
    pos = {int(t): k for k, t in enumerate(Ds["ts"])}
    for k in range(Dv["n"]):
        j = pos[int(Dv["ts"][k])]
        assert V1["atr"][k] == Ds["atr"][j] or (np.isnan(V1["atr"][k]) and np.isnan(Ds["atr"][j]))
        assert V1["sclose"][k] == Ds["close"][j] and V1["segpos"][k] == Ds["segpos"][j] and V1["close"][k] == Dv["close"][k]
    Dmiss = V.binance_frame(pd.DataFrame({**{c: [1.0] for c in ("open", "high", "low", "close", "volume")},
                                          "timestamp": [V.day_ms("2030-01-01")]}), "1d", symbol="XAUUSDT")
    Vm = V.venue_frame_a(Ds, Dmiss)
    assert np.isnan(Vm["atr"][0]) and Vm["segpos"][0] == -1 and Vm["spot_idx"][0] == -1
    # kural varyantı: sinyal ve stop spot'tan, giriş ve çıkış vadeli barında; vadelide karşılığı olmayan karar MEKÂN_BAR_YOK
    c = V.Counts()
    rows = V.venue_rows_a("A_DONCH_20_10", F4, Ds, V.venue_frame_a(F4, V.binance_frame(_bars(hv, 4 * HOUR), "4h", symbol="XAUUSDT")),
                          V1, CFG, c, None)
    vts = set(Dv["ts"].tolist())
    real = [e for e in rows if e["family"] == V.FAMILY]
    assert real and all(e["symbol"] == "XAUUSDT" and e["period"] == "ALL" for e in real)
    assert c.get(V.FAMILY, "A_DONCH_20_10", ["LONG", "SHORT"])["skipped"]["MEKÂN_BAR_YOK"] == sum(1 for t, _ in sp if t not in vts)
    for e in real:
        j = pos[e["ts_i"]]
        s = 1.0 if e["side"] == "LONG" else -1.0
        assert (e["ts_i"], e["side"]) in sp and math.isclose(e["stop"], Ds["close"][j] - s * 2.0 * Ds["atr"][j])
        assert e["entry_ms"] == Dv["ts"][e["i"] + 1] and e["entry_px"] == Dv["open"][e["i"] + 1]
    assert any(e["family"] == V.PLACEBO_FAMILY for e in rows)
    # aylık varyant: ay yapısı vadelinin; month_end = vadeli m+1 ayının son barının kapanışı
    c2 = V.Counts()
    rows_m = V.venue_rows_a("A_TSMOM_1M", F4, Ds, V1, V1, CFG, c2, None)
    mtv = V.month_table(Dv)
    real_m = [e for e in rows_m if e["family"] == V.FAMILY]
    assert real_m and {e["exit_kind"] for e in real_m} <= {"MONTH_END", "STOP"} and any(e["exit_kind"] == "MONTH_END" for e in real_m)
    for e in real_m:
        m = int(Dv["mi"][e["i"]])
        nxt = mtv[m + 1]
        assert e["i"] == mtv[m]["last"] and e["entry_ms"] == Dv["ts"][nxt["first"]] and e["max_bars"] == nxt["n"]
        assert e["ts_i"] == Dv["ts"][e["i"]] and e["ts_i"] in {int(Ds["ts"][gi]) for gi, *_ in V.monthly_signals("A_TSMOM_1M", Ds, V.month_table(Ds))[0]}
        if e["exit_kind"] == "MONTH_END":
            assert e["exit_ms"] == Dv["cms"][nxt["last"]] and e["hold"] == nxt["n"]
        else:
            assert e["exit_ms"] <= Dv["cms"][nxt["last"]]
    msigs, _, _ = V.monthly_signals("A_TSMOM_1M", Ds, V.month_table(Ds))
    sk = c2.get(V.FAMILY, "A_TSMOM_1M", ["LONG", "SHORT"])
    assert sk["signals"] + sk["skipped"].get("MEKÂN_BAR_YOK", 0) == len(msigs) and sk["skipped"]["MEKÂN_BAR_YOK"] > 0


# ---------------------------------------------------------------------------- ayna hazır, anlık görüntü
def test_mirror_must_be_finished_and_unchanged_during_the_run(tmp_path):
    h = V.synthetic_duka_hourly("2009-01-01", "2009-03-31", 18)
    root = tmp_path / "ayna"
    lp = write_mirror(root, h, V.mi_of(2009, 1), V.mi_of(2009, 3), log=False)
    with pytest.raises(V.GoldDataError, match="hourly mirror pass finished"):
        V.mirror_ready(root, lp)
    with pytest.raises(V.GoldDataError, match="--duka-log"):
        V.mirror_ready(root, None)
    lp.write_text(V.MIRROR_DONE_LINE + "\n", encoding="utf-8")
    assert V.mirror_ready(root, lp)["ready"]
    part = V.duka_hour_path(root, V.mi_of(2009, 4)).with_suffix(".bi5.part")
    part.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(b"x")
    with pytest.raises(V.GoldDataError, match="yarım dosya"):
        V.mirror_ready(root, lp)
    part.unlink()
    files = [str(V.duka_hour_path(root, m)) for m in (V.mi_of(2009, 1), V.mi_of(2009, 2))]
    snap = V.mirror_snapshot(root, files)
    assert snap["n_files"] == 2 and snap["cut_ts"] == "2026-10-05T06:%02d:00+00:00" % (V.mi_of(2009, 3) % 60)
    V.check_snapshot(root, snap)
    p = Path(files[1])
    p.write_bytes(p.read_bytes() + b"\x00")
    with pytest.raises(V.GoldDataError, match="değişti"):
        V.check_snapshot(root, snap)


# ---------------------------------------------------------------------------- uçtan uca (sentetik ayna + sahte arşiv, ağ YOK)
DW = {"A": {"is": ["2009-01-01", "2010-06-30"], "oos": ["2010-07-01", "2011-06-30"], "months": ["2010-07", "2011-05"], "open_end": "2011-06-01"},
      "B": {"is": ["2024-01-05", "2024-12-31"], "oos": ["2025-01-01", "2025-06-30"], "months": ["2025-01", "2025-06"], "open_end": "2025-07-01"},
      "seen": {"paxg": ["2024-01-01", "2025-06-30"], "dukascopy": ["2011-07-01", "2011-12-31"]},
      "venue": {"XAUUSDT": ["2025-03-01", "2025-06-30"], "PAXGUSDT": ["2025-01-01", "2025-06-30"]},
      "calib": {"a": ["2009-01-01", "2011-06-30"], "b": ["2024-01-05", "2025-06-30"]}}


def kline_zip(rows, header=False) -> bytes:
    buf = io.BytesIO()
    lines = (["open_time,open,high,low,close,volume,close_time,qv,n,tb,tq,ignore"] if header else []) + [",".join(str(x) for x in r) for r in rows]
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def _bars(h: pd.DataFrame, step: int) -> pd.DataFrame:
    g = h.assign(b=h["timestamp"] // step * step).groupby("b").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                                                    close=("close", "last"), volume=("volume", "sum")).reset_index()
    return g.rename(columns={"b": "timestamp"})


def fake_archive(windows) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    a, b = windows["seen"]["paxg"]
    h, _ = V.synthetic_paxg_hourly(a, b, 21)
    for tf, step in (("1h", HOUR), ("4h", 4 * HOUR), ("1d", DAY)):
        df = _bars(h, step)
        for mon, g in df.groupby(pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m")):
            files[G.spot_url(V.PAXG, tf, mon)] = kline_zip([[int(t), o, hh, lo, c, v, int(t) + step - 1, 0, 1, 0, 0, 0]
                                                            for t, o, hh, lo, c, v in g[["timestamp", "open", "high", "low", "close", "volume"]].itertuples(index=False)])
    for j, (sym, (a2, b2)) in enumerate(windows["venue"].items()):
        hv, _ = V.synthetic_paxg_hourly(a2, b2, 30 + j)
        hv[["open", "high", "low", "close"]] *= 1.0 + 0.001 * (j + 1)
        for tf, step in (("1h", HOUR), ("4h", 4 * HOUR), ("1d", DAY)):
            df = _bars(hv, step)
            for mon, g in df.groupby(pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.strftime("%Y-%m")):
                files[f"{L.ARCHIVE_BASE}/monthly/klines/{sym}/{tf}/{sym}-{tf}-{mon}.zip"] = kline_zip(
                    [[int(t), o, hh, lo, c, v, int(t) + step - 1, 0, 1, 0, 0, 0]
                     for t, o, hh, lo, c, v in g[["timestamp", "open", "high", "low", "close", "volume"]].itertuples(index=False)], header=True)
        s2, e2 = V.win_ms(a2, b2)
        for mon in sorted({pd.Timestamp(t, unit="ms", tz="UTC").strftime("%Y-%m") for t in range(s2, e2, DAY)}):
            m0 = V.day_ms(mon + "-01")
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr("f.csv", "calc_time,funding_interval_hours,last_funding_rate\n" + "\n".join(
                    f"{m0 + k * 8 * HOUR},8,0.0001" for k in range(31 * 3) if m0 + k * 8 * HOUR < L._next_month(m0)))
            files[f"{L.ARCHIVE_BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{mon}.zip"] = buf.getvalue()
    return files


@pytest.fixture()
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ağ kullanılmamalı")
    monkeypatch.setattr(L, "_http_get", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)


def test_end_to_end_offline_no_network_all_sections_and_cli(tmp_path, no_network):
    h = V.synthetic_duka_hourly("2009-01-01", "2011-12-31", 19)
    root = tmp_path / "ayna"
    lp = write_mirror(root, h, V.mi_of(2009, 1), V.mi_of(2011, 12), day_years=(2009, 2010, 2011))
    files = fake_archive(DW)
    asked = []
    fetch = lambda url: (asked.append(url), files.get(url))[1]  # noqa: E731
    out = tmp_path / "out"
    kw = dict(cache_dir=tmp_path / "cache", out_dir=out, cfg=CFG, fetch=fetch, now_ms=V.day_ms("2026-01-01"), log=lambda s: None,
              data_windows=DW, duka_root=root, duka_log=lp, calib_worlds=1)
    rep = V.run(sections=["all"], **kw)
    assert rep["registry_sha"] == PINNED_SHA and rep["sections"] == ["A", "B", "seen", "venue", "calib"] and rep["windows_overridden"]
    A, B = rep["A"], rep["B"]
    assert A["status"] == "koşuldu" and B["status"] == "koşuldu" and len(A["cells"]) == 16 and len(B["cells"]) == 8
    assert [f"{c['name']} {c['cell']}" for c in A["cells"] + B["cells"] if c["main"]] == V.GOLD_V2_REGISTRY["main_cells"]
    assert A["data"]["timestamp_check"]["pass"] and A["data"]["coverage"]["pass"] and A["data"]["usable_months"] == 30
    assert A["mirror"]["snapshot"]["n_files"] == 33 and A["mirror"]["snapshot_end"] == "aynı"
    assert A["data"]["frames"]["day_file"]["months_compared"] == 30 and A["data"]["frames"]["partial_days"] == 1
    assert sum(c["counts"]["trades"] for c in A["cells"] if c["cell"] == "LONG") > 20 and len(A["short_info"]) == 8
    for c in A["cells"] + B["cells"]:
        assert c["IS"].get("n", 0) + c["OOS"].get("n", 0) == c["counts"]["trades"], c["name"]
        assert c["monthly"]["months"] == (11 if c["family"] == "A" else 6)
        assert (c["higher_risk"]["text"] == "kenar yok") == (c["verdict_strict"] != L.V_STRONG)
    assert len(rep["seen"]["rows"]) == 32 and len(rep["venue"]["rows"]) == 48
    assert any(r["all"].get("n") for r in rep["venue"]["rows"] if r["name"].startswith("B_")) and rep["venue"]["notes_evaluated"]
    vb = [r for r in rep["venue"]["rows"] if r["name"] == "B_WKND_REV_ALL" and r["cell"] == "LONG" and r["all"].get("n")]
    assert vb and all(r["gross"]["mean_r"] > r["all"]["mean_r"] for r in vb)                 # pozitif fonlama LONG'a maliyet
    assert rep["calib"]["worlds"] == 1 and rep["calib"]["totals"]["A"]["cells"] == 16 and rep["calib"]["totals"]["B"]["cells"] == 8
    assert rep["conclusion"]["lines"]
    md = (out / V.REPORT_MD).read_text(encoding="utf-8")
    assert PINNED_SHA in md and "## ANA hücreler" in md and "UYARI" in md and "## İkincil hücreler" in md and "Bilgi eki" in md
    assert rep["disclosure_tr"] == list(V.DISCLOSURE_TR) and "## Ön kayıttan önce veriden görülenler" in md and "2010-01 saatlik" in md
    assert "### Eşzamanlı açık işlem (bilgi)" in md and "- A_TSMOM_12M aylık karar atlamaları" in md and "KISMİ_GÜN 1 (serinin ilk ayının" in md
    assert V.VENUE_FUNDING_NOTE_TR in md
    for sec in rep["sections"]:
        with gzip.open(out / V.events_file(sec), "rt", encoding="utf-8") as fh:
            head = fh.readline().strip().split(",")
            n_rows = sum(1 for _ in fh)
        assert head == V.EVENT_COLS and (n_rows > 0 or sec == "calib"), sec
    assert not list(out.glob("*.part"))
    # ayrı bölüm koşusu aynı klasörde birleşir; önbellekten (çevrimdışı) aynı sonuç
    n_asked = len(asked)
    rep2 = V.run(sections=["B"], **{**kw, "offline": True, "fetch": None})
    assert len(asked) == n_asked and rep2["archive_requests"] == 0 and rep2["sections"] == rep["sections"]
    assert [c["OOS"] for c in rep2["B"]["cells"]] == [c["OOS"] for c in rep["B"]["cells"]] and rep2["A"] == rep["A"]
    with pytest.raises(V.GoldDataError, match="ayar"):
        V.run(sections=["B"], **{**kw, "cfg": L.LabConfig(bootstrap_iters=201)})
    # komut satırı: bilinmeyen bölüm, çevrimdışı boş önbellek, hazır olmayan ayna → çıkış 2
    spec = importlib.util.spec_from_file_location("gold_cli_v2", ROOT / "scripts" / "gold_lab_v2.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["--sections", "nope", "--cache", str(tmp_path / "c2"), "--out", str(tmp_path / "o2")]) == 2
    assert cli.main(["--sections", "B", "--offline", "--cache", str(tmp_path / "c2"), "--out", str(tmp_path / "o3")]) == 2
    assert cli.main(["--sections", "A", "--cache", str(tmp_path / "c2"), "--out", str(tmp_path / "o4"), "--duka-root", str(root)]) == 2


def test_family_a_is_not_run_when_the_timestamp_check_or_coverage_fails(tmp_path, no_network, monkeypatch):
    h = V.synthetic_duka_hourly("2009-01-01", "2011-06-30", 20)
    kw = dict(cache_dir=tmp_path / "cache", cfg=CFG, now_ms=V.day_ms("2026-01-01"), log=lambda s: None, data_windows=DW)
    shifted = h.assign(timestamp=h["timestamp"] + 3 * HOUR)                    # yanlış saat dilimi gibi
    r1 = tmp_path / "kayik"
    lp1 = write_mirror(r1, shifted, V.mi_of(2009, 1), V.mi_of(2011, 6))
    rep = V.run(sections=["A"], out_dir=tmp_path / "o1", duka_root=r1, duka_log=lp1, **kw)
    assert rep["A"]["status"] == "yapılamadı (zaman damgası)" and "cells" not in rep["A"]
    assert any("yapılamadı (zaman damgası)" in x for x in rep["conclusion"]["lines"])
    r2 = tmp_path / "eksik"
    lp2 = write_mirror(r2, h, V.mi_of(2009, 1), V.mi_of(2011, 6), skip={V.mi_of(2010, 9)})
    rep = V.run(sections=["A"], out_dir=tmp_path / "o2", duka_root=r2, duka_log=lp2, **kw)
    assert rep["A"]["status"] == "yapılamadı (kapsama)" and rep["A"]["data"]["coverage"]["periods"]["OOS"]["bad_months"] == ["2010-09"]
    md = (tmp_path / "o2" / V.REPORT_MD).read_text(encoding="utf-8")
    assert "yapılamadı (kapsama)" in md
    # koşu sırasında ayna değişirse koşu geçersiz
    r3 = tmp_path / "degisen"
    lp3 = write_mirror(r3, h, V.mi_of(2009, 1), V.mi_of(2011, 6))
    real = V.run_family_a

    def touching(*a, **k):
        p = V.duka_hour_path(r3, V.mi_of(2010, 1))
        p.write_bytes(p.read_bytes() + b"\x00")
        return real(*a, **k)
    monkeypatch.setattr(V, "run_family_a", touching)
    with pytest.raises(V.GoldDataError, match="değişti"):
        V.run(sections=["A"], out_dir=tmp_path / "o3", duka_root=r3, duka_log=lp3, **kw)


def test_default_output_folder_is_gitignored():
    """Olay dosyaları giriş/stop fiyatı (Dukascopy ve Binance türevi) içerir: varsayılan --out klasörü depoya girmemeli."""
    assert 'default=str(ROOT / "gold_lab_v2_out")' in (ROOT / "scripts" / "gold_lab_v2.py").read_text(encoding="utf-8")
    ign = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "gold_lab_v2_out/" in ign and "gold_lab_data/" in ign


def test_lab_modules_unchanged_by_gold_lab_v2_import():
    for f in ("signal_lab.py", "gold_lab.py"):
        assert "gold_lab_v2" not in (ROOT / "tradingbot" / f).read_text(encoding="utf-8")
    assert L.LabConfig() == L.LabConfig(fee_pct=0.05, slippage_bps=3.0, max_hold_bars=24, default_rr=2.0)
