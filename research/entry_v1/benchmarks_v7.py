# -*- coding: utf-8 -*-
"""PROTOCOL_V7 Q2 — iki referans, üç pencere: al-tut ve tek kurallı EMA200 trend temeli.

Trend temeli: her coin için GÜNLÜK kapanış > EMA200 ise pozisyonda, değilse nakit. EMA200
pencereden ÖNCEKİ tüm tarihten hesaplanır (ısınma sorunu yok). Sinyal gün kapanışında, uygulama
ERTESİ günün açılışında (kapanış fiyatı ≈ ertesi açılış varsayımı yerine bir gün gecikme: bakılan
kapanışta değil, SONRAKİ kapanışta işlem). Her giriş/çıkışta taker ücreti + kayma. Kaldıraç yok,
stop yok, funding yok (SPOT benzeri maruziyet). On coin eşit ağırlık, coin bazında ayrı kova.
"""
from __future__ import annotations

import datetime as _dt
import glob
import gzip
import io
import json

HIST = r"C:/Users/berke/wt-ten/data/history/futures"
SYMS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]
WINS = [("P1", "2020-11-01", "2022-08-31"), ("P2", "2022-09-01", "2024-08-31"), ("P3", "2024-09-01", "2026-08-31")]
TAKER = 0.0005          # %0,05 her yönde
SLIP = 0.0005           # %0,05 kayma her yönde
EMA_N = 200


def _ms(s):
    return int(_dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def daily(sym):
    base = sym.replace("/", "_")
    out = []
    for p in sorted(glob.glob("%s/%s/1d/*/*.csv.gz" % (HIST, base))):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            head = f.readline().strip().split(",")
            i_ts, i_c = head.index("timestamp"), head.index("close")
            for line in f:
                parts = line.rstrip("\n").split(",")
                try:
                    out.append((int(float(parts[i_ts])), float(parts[i_c])))
                except (ValueError, IndexError):
                    continue
    out.sort()
    return out


def ema_series(closes, n):
    out, k, e = [], 2.0 / (n + 1), None
    for i, c in enumerate(closes):
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            e = sum(closes[:n]) / n
            out.append(e)
        else:
            e = c * k + e * (1 - k)
            out.append(e)
    return out


def trend_coin(rows, t0, t1, cash):
    """Tek coin: (t, close) günlük seri. Sinyal gün i kapanışında, işlem gün i+1 kapanışında."""
    ts = [r[0] for r in rows]
    cl = [r[1] for r in rows]
    ema = ema_series(cl, EMA_N)
    pos_qty, switches, eq_path = 0.0, 0, []
    in_win = [i for i, t in enumerate(ts) if t0 <= t <= t1]
    if not in_win:
        return None
    first, last = in_win[0], in_win[-1]
    pending = None                      # bir sonraki barda uygulanacak hedef durum
    for i in range(first, last + 1):
        price = cl[i]
        if pending is not None:
            if pending and pos_qty == 0.0:
                pos_qty = cash * (1 - TAKER - SLIP) / price
                cash = 0.0
                switches += 1
            elif (not pending) and pos_qty > 0.0:
                cash = pos_qty * price * (1 - TAKER - SLIP)
                pos_qty = 0.0
                switches += 1
            pending = None
        want = (ema[i] is not None and price > ema[i])
        if want != (pos_qty > 0.0):
            pending = want
        eq_path.append(cash + pos_qty * price)
    final = cash + pos_qty * cl[last] * (1 - TAKER - SLIP if pos_qty > 0 else 1.0)
    peak, dd = 0.0, 0.0
    for v in eq_path:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak if peak > 0 else 0.0)
    return {"final": final, "switches": switches, "max_dd_pct": dd * 100.0,
            "days_in": sum(1 for i in range(first, last + 1) if ema[i] is not None and cl[i] > ema[i]),
            "days": last - first + 1}


def buy_hold_coin(rows, t0, t1, cash):
    w = [r for r in rows if t0 <= r[0] <= t1]
    if len(w) < 2:
        return None
    qty = cash * (1 - TAKER - SLIP) / w[0][1]
    return {"final": qty * w[-1][1] * (1 - TAKER - SLIP)}


def main():
    equity = 100.0
    per = equity / len(SYMS)
    data = {s: daily(s) for s in SYMS}
    report = {}
    for name, f, t in WINS:
        t0, t1 = _ms(f), _ms(t)
        bh_total, tr_total, sw, dd, days_in, days, missing = 0.0, 0.0, 0, [], 0, 0, []
        for s in SYMS:
            bh = buy_hold_coin(data[s], t0, t1, per)
            tr = trend_coin(data[s], t0, t1, per)
            if bh is None or tr is None:
                missing.append(s); bh_total += per; tr_total += per; continue
            bh_total += bh["final"]; tr_total += tr["final"]; sw += tr["switches"]
            dd.append(tr["max_dd_pct"]); days_in += tr["days_in"]; days += tr["days"]
        report[name] = {"pencere": "%s → %s" % (f, t),
                        "al_tut_pct": round((bh_total / equity - 1) * 100, 2),
                        "ema200_trend_pct": round((tr_total / equity - 1) * 100, 2),
                        "trend_switches": sw, "trend_ort_coin_maks_dusus_pct": round(sum(dd) / len(dd), 2) if dd else None,
                        "trend_pozisyonda_gun_payi": round(days_in / days, 3) if days else None,
                        "eksik": missing}
    io.open(r"C:/Users/berke/research/entry_v1/out/benchmarks_v7.json", "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for k, v in report.items():
        print(k, json.dumps(v, ensure_ascii=False))


if __name__ == "__main__":
    main()
