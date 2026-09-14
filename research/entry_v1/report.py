# -*- coding: utf-8 -*-
"""Tek kosunun PROTOCOL_V2 sec. 6'daki tam olcut kumesi."""
from __future__ import annotations
import os, sys, statistics as st
os.chdir(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ".")
import analyze, benchmarks as bm

TOTAL_BARS_PER_DAY = 6          # 4h

def _exposure_day_share(tr, date_from, date_to):
    """En az bir pozisyonun acik oldugu GUN orani (0-1). Ust uste binen pozisyonlar bir sayilir."""
    import datetime as dt
    d0, d1 = dt.date.fromisoformat(date_from), dt.date.fromisoformat(date_to)
    days = set()
    for t in tr:
        a = dt.date.fromisoformat(str(t["opened_at"])[:10])
        b = dt.date.fromisoformat(str(t["closed_at"])[:10])
        d = max(a, d0)
        while d <= min(b, d1):
            days.add(d); d += dt.timedelta(days=1)
    total = (d1 - d0).days + 1
    return round(len(days) / total, 4) if total > 0 else None


def full(run_id, date_from, date_to, equity=100.0):
    tr, led = analyze.load_full(run_id)
    if not tr:
        return {"run_id": run_id, "n": 0, "net_usdt": 0.0, "account_return_pct": 0.0,
                "note": "hic kapanmis islem yok"}
    N = [t["net"] for t in tr]; R = [t["r"] for t in tr]
    w = [x for x in N if x > 0]; l = [-x for x in N if x <= 0]
    eq = bm.daily_equity(tr, date_from, date_to, equity)
    days = max(1, len(eq))
    bars = days * TOTAL_BARS_PER_DAY
    return {"run_id": run_id, "n": len(tr),
            "net_usdt": round(sum(N), 4),
            "account_return_pct": round(100.0 * sum(N) / equity, 2),
            "mean_r": round(st.mean(R), 4), "median_r": round(st.median(R), 4),
            "sum_r": round(sum(R), 3),
            "win_rate": round(sum(1 for x in R if x > 0) / len(R), 4),
            "profit_factor": round(sum(w) / sum(l), 4) if l else None,
            "max_dd_pct": bm.max_drawdown_pct(eq),
            "sharpe_daily_realised": bm.sharpe_from_equity(eq),
            # ORTALAMA ES ZAMANLI POZISYON: pozisyon-bari / takvim-bari. 1'i ASABILIR
            # (ayni anda birden cok pozisyon). "maruziyet orani" DEGILDIR.
            "avg_concurrent_positions": round(sum(t["bars"] for t in tr) / bars, 4),
            # GERCEK MARUZIYET: en az bir pozisyonun ACIK oldugu gun orani.
            "exposure_day_share": _exposure_day_share(tr, date_from, date_to),
            "mean_hold_bars": round(st.mean(t["bars"] for t in tr), 1),
            "symbols": len({t["symbol"] for t in tr}),
            "months": len({t["month"] for t in tr}),
            "fees_total": round(sum(t["fees"] for t in tr), 4),
            "funding_total": round(sum(t["funding"] for t in tr), 4),
            "final_equity": round(equity + sum(N), 4)}

def by_key(run_id, key):
    tr, _ = analyze.load_full(run_id)
    out = {}
    for t in tr:
        out.setdefault(t[key], []).append(t)
    return {k: {"n": len(v), "net": round(sum(x["net"] for x in v), 3),
                "mean_r": round(st.mean(x["r"] for x in v), 3)} for k, v in sorted(out.items(), key=lambda kv: -len(kv[1]))}

if __name__ == "__main__":
    import json
    print(json.dumps(full(sys.argv[1], sys.argv[2], sys.argv[3]), ensure_ascii=False, indent=1))
