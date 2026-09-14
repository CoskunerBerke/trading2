# -*- coding: utf-8 -*-
"""PROTOCOL_V8 — mum formasyonu GİRİŞ SİNYALİ olarak: tek kurallı referans sistem, üç pencere.

Dedektör ÜRETİMİN kendisi (`candle_context._shapes`, candle_v1.2.0, geniş taraf kümeleri).
Uzman yok, kapı yok. 4h kapanmış bar; sinyal barından SONRAKİ barın açılışında giriş; stop 2,5×ATR14;
hedef 2R; zaman stopu 24 bar; aynı barda stop+hedef → stop (kötümser). Coin kovası = sermaye/10,
işlem riski kovanın %2'si, kaldıraç ≤ 5×. Maliyet: taker %0,05 + kayma %0,05 her yön; FUNDING dahil
(arşivdeki 8 saatlik oranlar; LONG pozitif oranı öder, SHORT alır).
"""
from __future__ import annotations

import bisect
import datetime as _dt
import glob
import gzip
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:/Users/berke/wt-entry")
from tradingbot.learn.candle_context import (BEAR_SIDE_SHAPES_EXT, BULL_SIDE_SHAPES_EXT,  # noqa: E402
                                             CandleContextConfig, _shapes)

HIST = r"C:/Users/berke/wt-ten/data/history/futures"
SYMS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
        "LINK/USDT", "DOGE/USDT", "AVAX/USDT", "LTC/USDT", "AAVE/USDT"]
WINS = [("P1", "2020-11-01", "2022-08-31"), ("P2", "2022-09-01", "2024-08-31"), ("P3", "2024-09-01", "2026-08-31")]
ARMS = ["S-A", "S-B", "S-C", "S-D"]
TAKER, SLIP = 0.0005, 0.0005
ATR_N, ATR_MULT, TARGET_R, TIME_STOP = 14, 2.5, 2.0, 24
RISK_FRAC, MAX_LEV, EQUITY = 0.02, 5.0, 100.0
CFG = CandleContextConfig()
H4 = 4 * 3_600_000
D1 = 24 * 3_600_000


def _ms(s):
    return int(_dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _read(sym, tf, cols):
    base = sym.replace("/", "_")
    out = []
    for p in sorted(glob.glob("%s/%s/%s/*/*.csv.gz" % (HIST, base, tf))):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            head = f.readline().strip().split(",")
            try:
                idx = [head.index(c) for c in cols]
            except ValueError:
                continue
            for line in f:
                parts = line.rstrip("\n").split(",")
                try:
                    out.append(tuple(float(parts[i]) for i in idx))
                except (ValueError, IndexError):
                    continue
    out.sort()
    return out


def bars4h(sym):
    rows = _read(sym, "4h", ["timestamp", "open", "high", "low", "close"])
    return [{"timestamp": int(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in rows]


def funding(sym):
    rows = _read(sym, "funding", ["timestamp", "rate"])
    return [int(r[0]) for r in rows], [r[1] for r in rows]


def atr_series(bars):
    out, prev_c, atr, trs = [], None, None, []
    for b in bars:
        tr = b["high"] - b["low"] if prev_c is None else max(b["high"] - b["low"], abs(b["high"] - prev_c), abs(b["low"] - prev_c))
        if atr is None:
            trs.append(tr)
            atr = sum(trs) / ATR_N if len(trs) == ATR_N else None
        else:
            atr = (atr * (ATR_N - 1) + tr) / ATR_N
        out.append(atr)
        prev_c = b["close"]
    return out


def btc_regime_index():
    rows = _read("BTC/USDT", "1d", ["timestamp", "close"])
    cl = [r[1] for r in rows]
    k, e, regs, ts = 2.0 / 201, None, [], []
    for i, (t, c) in enumerate(rows):
        if i < 199:
            continue
        e = sum(cl[:200]) / 200 if i == 199 else c * k + e * (1 - k)
        ts.append(int(t) + D1)                     # bu günün KAPANIŞ anından itibaren geçerli
        regs.append("UP" if c > e else "DOWN")
    return ts, regs


def regime_at(idx, t):
    ts, regs = idx
    i = bisect.bisect_right(ts, t) - 1
    return regs[i] if i >= 0 else None


def side_of(rows):
    s = set(_shapes(rows, CFG))
    bull, bear = bool(BULL_SIDE_SHAPES_EXT & s), bool(BEAR_SIDE_SHAPES_EXT & s)
    if bull == bear:
        return None
    return "LONG" if bull else "SHORT"


def signal(arm, bars, i, reg_idx):
    """Kapanmış bar i'de sinyal. S-D: formasyon bar i-1'de, bar i teyit."""
    if i < 3:
        return None
    if arm == "S-D":
        side = side_of(bars[i - 3:i])
        if side != "LONG":
            return None
        return "LONG" if bars[i]["close"] > bars[i - 1]["close"] else None
    side = side_of(bars[i - 2:i + 1])
    if side is None:
        return None
    if arm == "S-A":
        return side
    if side != "LONG":
        return None
    if arm == "S-C" and regime_at(reg_idx, bars[i]["timestamp"] + H4) != "UP":
        return None
    return "LONG"


def simulate(arm, sym, bars, atr, fund_ts, fund_rate, reg_idx, t0, t1, bucket):
    n = len(bars)
    idx = [i for i, b in enumerate(bars) if t0 <= b["timestamp"] <= t1]
    if len(idx) < 50:
        return None
    first, last = idx[0], idx[-1]
    cash, pos, trades, eq_path = bucket, None, [], []
    signals = 0
    for i in range(first, last + 1):
        b = bars[i]
        if pos is not None:
            side, entry, stop, target, qty, i_entry, risk = pos
            exit_px, why = None, None
            if side == "LONG":
                if b["low"] <= stop: exit_px, why = stop, "stop"
                elif b["high"] >= target: exit_px, why = target, "target"
            else:
                if b["high"] >= stop: exit_px, why = stop, "stop"
                elif b["low"] <= target: exit_px, why = target, "target"
            if exit_px is None and i - i_entry >= TIME_STOP:
                exit_px, why = b["close"], "time"
            if exit_px is not None:
                gross = (exit_px - entry) * qty if side == "LONG" else (entry - exit_px) * qty
                cost = (entry + exit_px) * qty * (TAKER + SLIP)
                t_in, t_out = bars[i_entry]["timestamp"], b["timestamp"] + H4
                lo, hi = bisect.bisect_right(fund_ts, t_in), bisect.bisect_right(fund_ts, t_out)
                fpay = sum(fund_rate[j] for j in range(lo, hi)) * entry * qty
                fpay = fpay if side == "LONG" else -fpay
                net = gross - cost - fpay
                cash += net
                trades.append({"side": side, "net": net, "r": net / risk if risk > 0 else 0.0, "why": why,
                               "bars": i - i_entry, "fees": cost, "funding": fpay})
                pos = None
        if pos is None and i + 1 <= last and atr[i]:
            sig = signal(arm, bars, i, reg_idx)
            if sig:
                signals += 1
                entry = bars[i + 1]["open"]
                dist = ATR_MULT * atr[i]
                stop = entry - dist if sig == "LONG" else entry + dist
                target = entry + TARGET_R * dist if sig == "LONG" else entry - TARGET_R * dist
                risk = RISK_FRAC * cash
                qty = risk / dist
                qty = min(qty, MAX_LEV * cash / entry)
                risk = qty * dist
                pos = (sig, entry, stop, target, qty, i + 1, risk)
        mtm = cash
        if pos is not None:
            side, entry, stop, target, qty, i_entry, risk = pos
            mtm += (b["close"] - entry) * qty if side == "LONG" else (entry - b["close"]) * qty
        eq_path.append(mtm)
    if pos is not None:                                   # pencere sonunda açık: kapanışta kapat
        side, entry, stop, target, qty, i_entry, risk = pos
        px = bars[last]["close"]
        gross = (px - entry) * qty if side == "LONG" else (entry - px) * qty
        cost = (entry + px) * qty * (TAKER + SLIP)
        cash += gross - cost
        trades.append({"side": side, "net": gross - cost, "r": (gross - cost) / risk if risk else 0.0, "why": "eow", "bars": last - i_entry, "fees": cost, "funding": 0.0})
    peak, dd = 0.0, 0.0
    for v in eq_path:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak if peak > 0 else 0.0)
    return {"final": cash, "trades": trades, "signals": signals, "max_dd": dd}


def main():
    data = {s: bars4h(s) for s in SYMS}
    atrs = {s: atr_series(data[s]) for s in SYMS}
    funds = {s: funding(s) for s in SYMS}
    reg_idx = btc_regime_index()
    per = EQUITY / len(SYMS)
    report = {}
    for arm in ARMS:
        report[arm] = {}
        for name, f, t in WINS:
            t0, t1 = _ms(f), _ms(t)
            total, all_tr, sig, dds, missing = 0.0, [], 0, [], []
            for s in SYMS:
                r = simulate(arm, s, data[s], atrs[s], funds[s][0], funds[s][1], reg_idx, t0, t1, per)
                if r is None:
                    missing.append(s); total += per; continue
                total += r["final"]; all_tr += r["trades"]; sig += r["signals"]; dds.append(r["max_dd"])
            n = len(all_tr)
            wins = [x["net"] for x in all_tr if x["net"] > 0]; losses = [-x["net"] for x in all_tr if x["net"] <= 0]
            report[arm][name] = {
                "getiri_pct": round((total / EQUITY - 1) * 100, 2), "islem": n, "sinyal": sig,
                "ort_R": round(sum(x["r"] for x in all_tr) / n, 4) if n else None,
                "isabet": round(100 * len(wins) / n, 1) if n else None,
                "PF": round(sum(wins) / sum(losses), 3) if losses else None,
                "ucret_usdt": round(sum(x["fees"] for x in all_tr), 2), "funding_usdt": round(sum(x["funding"] for x in all_tr), 2),
                "cikis": {k: sum(1 for x in all_tr if x["why"] == k) for k in ("stop", "target", "time", "eow")},
                "coin_ort_maks_dusus_pct": round(100 * sum(dds) / len(dds), 1) if dds else None, "eksik": missing}
    io.open(Path(r"C:/Users/berke/research/entry_v1/out/benchmarks_v8.json"), "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))
    for arm in ARMS:
        for name, _, _ in WINS:
            q = report[arm][name]
            print(arm, name, "getiri %+.2f%%  islem %d  ortR %s  isabet %s  PF %s  ucret %.1f funding %.1f  cikis %s" % (
                q["getiri_pct"], q["islem"], q["ort_R"], q["isabet"], q["PF"], q["ucret_usdt"], q["funding_usdt"], q["cikis"]))


if __name__ == "__main__":
    main()
