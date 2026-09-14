# -*- coding: utf-8 -*-
"""B2 nakit ve B3 al-tut referanslari + gunluk ozkaynak serisinden Sharpe.

B3 SPOT benzeri, kaldiracsiz, stop'suz bir referanstir; farkli maruziyet sinifidir ve tek
basina ustunluk iddiasi kurmaz. Ayni takvim, ayni baslangic sermayesi, gercekci islem gideri.
"""
from __future__ import annotations

import datetime as _dt
import glob
import gzip
import io
import math
import statistics as st

HIST = r"C:/Users/berke/wt-ten/data/history/futures"
SYMS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]
TAKER = 0.0005                       # %0.05, her yonde


def _ms(s: str) -> int:
    return int(_dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def closes(sym: str, t0: int, t1: int) -> list[tuple[int, float]]:
    """(timestamp_ms, close) — yalniz [t0, t1] araligindaki KAPANMIS 4h barlar."""
    base = sym.replace("/", "_")
    out = []
    for p in sorted(glob.glob("%s/%s/4h/*/*.csv.gz" % (HIST, base))):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            head = f.readline().strip().split(",")
            try:
                i_ts, i_c = head.index("timestamp"), head.index("close")
            except ValueError:
                continue
            for line in f:
                parts = line.rstrip("\n").split(",")
                try:
                    ts = int(float(parts[i_ts]))
                    c = float(parts[i_c])
                except (ValueError, IndexError):
                    continue
                if t0 <= ts <= t1:
                    out.append((ts, c))
    out.sort()
    return out


def buy_and_hold(date_from: str, date_to: str, equity: float = 100.0) -> dict:
    """On coine ESIT agirlikli tek alim + tek satis. Eksik seri varsa ACIKCA raporlanir."""
    t0, t1 = _ms(date_from), _ms(date_to)
    per = equity / len(SYMS)
    total, detail, missing = 0.0, {}, []
    for s in SYMS:
        c = closes(s, t0, t1)
        if len(c) < 2:
            missing.append(s)
            continue
        p0, p1 = c[0][1], c[-1][1]
        qty = (per * (1 - TAKER)) / p0                 # alis ucreti
        val = qty * p1 * (1 - TAKER)                   # satis ucreti
        total += val
        detail[s] = {"ilk": p0, "son": p1, "getiri_pct": round((p1 / p0 - 1) * 100, 2),
                     "deger": round(val, 4)}
    if missing:                                        # eksik coin'in payi NAKITTE kalir
        total += per * len(missing)
    return {"baslangic": equity, "son": round(total, 4),
            "getiri_pct": round((total / equity - 1) * 100, 2),
            "eksik_seri": missing, "coin": detail}


def daily_equity(trades: list[dict], date_from: str, date_to: str, equity: float = 100.0):
    """KAPANMIS islemlerden gunluk GERCEKLESMIS ozkaynak serisi (islemsiz gunler DAHIL).

    Gerceklesmemis kar/zarar YOKTUR; bu yuzden seri gercek gunluk dalgalanmadan daha duzdur.
    Sharpe bu seriden hesaplandiginda YUKARI yanlidir ve oyle etiketlenir.
    """
    d0 = _dt.date.fromisoformat(date_from)
    d1 = _dt.date.fromisoformat(date_to)
    by_day: dict[_dt.date, float] = {}
    for t in trades:
        day = _dt.date.fromisoformat(str(t["closed_at"])[:10])
        by_day[day] = by_day.get(day, 0.0) + float(t["net"])
    out, cur, d = [], equity, d0
    while d <= d1:
        cur += by_day.get(d, 0.0)
        out.append((d, cur))
        d += _dt.timedelta(days=1)
    return out


def sharpe_from_equity(series, periods_per_year: int = 365) -> float | None:
    """Gunluk getirilerden yillik Sharpe (risksiz oran 0). Islemsiz gunler DAHIL."""
    vals = [v for _, v in series]
    rets = [(vals[i] / vals[i - 1] - 1.0) for i in range(1, len(vals)) if vals[i - 1] > 0]
    if len(rets) < 30:
        return None
    sd = st.pstdev(rets)
    if sd <= 0:
        return None
    return round(st.mean(rets) / sd * math.sqrt(periods_per_year), 3)


def max_drawdown_pct(series) -> float:
    peak, dd = None, 0.0
    for _, v in series:
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            dd = max(dd, (peak - v) / peak * 100.0)
    return round(dd, 2)


if __name__ == "__main__":
    import json
    import sys
    a, b = sys.argv[1], sys.argv[2]
    print(json.dumps(buy_and_hold(a, b), ensure_ascii=False, indent=1))
