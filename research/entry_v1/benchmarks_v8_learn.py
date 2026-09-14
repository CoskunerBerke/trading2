# -*- coding: utf-8 -*-
"""PROTOCOL_V8 §6 — ÖĞRENEN formasyon girişi (S-E1, S-E2).

Küresel kronolojik simülasyon (on coin birlikte, 2019-09'dan itibaren). Her boğa sinyali bir SANAL
işlem açar; kapanınca net R'si sinyal barındaki boğa etiketlerine yazılır. Gerçek işlem yalnız
öğrenilmiş istatistik izin verirse açılır (S-E1: n≥30 ve ort R>0; S-E2: alt %95 sınırı>0).
Gerçek işlemler ve kova muhasebesi yalnız pencere içinde; öğrenme pencereden önce de birikir.
"""
from __future__ import annotations

import bisect
import io
import json
import math
from pathlib import Path

import benchmarks_v8 as B

MIN_N = 30
Z = 1.96


class Stats:
    def __init__(self):
        self.d = {}

    def add(self, label, r):
        n, s, q = self.d.get(label, (0, 0.0, 0.0))
        self.d[label] = (n + 1, s + r, q + r * r)

    def ok(self, labels, strict):
        for lb in labels:
            n, s, q = self.d.get(lb, (0, 0.0, 0.0))
            if n < MIN_N:
                continue
            mean = s / n
            if not strict:
                if mean > 0:
                    return True
            else:
                var = max(0.0, q / n - mean * mean)
                if mean - Z * math.sqrt(var / n) > 0:
                    return True
        return False

    def table(self, k=10):
        rows = [(lb, n, s / n) for lb, (n, s, q) in self.d.items() if n >= MIN_N]
        rows.sort(key=lambda x: -x[1])
        return [{"etiket": lb, "n": n, "ort_R": round(m, 4)} for lb, n, m in rows[:k]]


def bull_labels(rows):
    s = set(B._shapes(rows, B.CFG))
    if B.BEAR_SIDE_SHAPES_EXT & s:
        return set()                          # iki taraflı → sinyal yok
    return s & B.BULL_SIDE_SHAPES_EXT


def run_window(strict, data, atrs, funds, t0, t1):
    per = B.EQUITY / len(B.SYMS)
    stats = Stats()
    state = {s: {"cash": per, "pos": None, "virt": [], "trades": [], "eq": [], "signals": 0, "entries": 0} for s in B.SYMS}
    idx = {s: {b["timestamp"]: i for i, b in enumerate(data[s])} for s in B.SYMS}
    all_ts = sorted(set(t for s in B.SYMS for t in idx[s]))
    for ts in all_ts:
        for s in B.SYMS:
            i = idx[s].get(ts)
            if i is None:
                continue
            bars, atr, (fts, frate), st = data[s], atrs[s], funds[s], state[s]
            b = bars[i]
            # --- sanal işlemler: çıkış ve öğrenme
            keep = []
            for v in st["virt"]:
                labels, entry, stop, target, dist, i_entry = v
                if i < i_entry:
                    keep.append(v); continue
                exit_px = None
                if b["low"] <= stop: exit_px = stop
                elif b["high"] >= target: exit_px = target
                elif i - i_entry >= B.TIME_STOP: exit_px = b["close"]
                if exit_px is None:
                    keep.append(v); continue
                lo, hi = bisect.bisect_right(fts, bars[i_entry]["timestamp"]), bisect.bisect_right(fts, b["timestamp"] + B.H4)
                fpay = sum(frate[j] for j in range(lo, hi)) * entry
                r = ((exit_px - entry) - (entry + exit_px) * (B.TAKER + B.SLIP) - fpay) / dist
                for lb in labels:
                    stats.add(lb, r)
            st["virt"] = keep
            # --- gerçek pozisyon: çıkış
            in_win = t0 <= ts <= t1
            if st["pos"] is not None:
                entry, stop, target, qty, i_entry, risk = st["pos"]
                exit_px, why = None, None
                if b["low"] <= stop: exit_px, why = stop, "stop"
                elif b["high"] >= target: exit_px, why = target, "target"
                elif i - i_entry >= B.TIME_STOP: exit_px, why = b["close"], "time"
                if exit_px is None and ts > t1:
                    exit_px, why = b["close"], "eow"
                if exit_px is not None:
                    gross = (exit_px - entry) * qty
                    cost = (entry + exit_px) * qty * (B.TAKER + B.SLIP)
                    lo, hi = bisect.bisect_right(fts, bars[i_entry]["timestamp"]), bisect.bisect_right(fts, b["timestamp"] + B.H4)
                    fpay = sum(frate[j] for j in range(lo, hi)) * entry * qty
                    net = gross - cost - fpay
                    st["cash"] += net
                    st["trades"].append({"net": net, "r": net / risk if risk else 0.0, "why": why, "fees": cost, "funding": fpay})
                    st["pos"] = None
            # --- sinyal
            if i + 1 < len(bars) and atr[i] and i >= 3:
                labels = bull_labels(bars[i - 2:i + 1])
                if labels:
                    entry = bars[i + 1]["open"]
                    dist = B.ATR_MULT * atr[i]
                    st["virt"].append((labels, entry, entry - dist, entry + B.TARGET_R * dist, dist, i + 1))
                    if in_win:
                        st["signals"] += 1
                        if st["pos"] is None and stats.ok(labels, strict):
                            risk = B.RISK_FRAC * st["cash"]
                            qty = min(risk / dist, B.MAX_LEV * st["cash"] / entry)
                            st["pos"] = (entry, entry - dist, entry + B.TARGET_R * dist, qty, i + 1, qty * dist)
                            st["entries"] += 1
            if in_win:
                mtm = st["cash"]
                if st["pos"] is not None:
                    entry, stop, target, qty, i_entry, risk = st["pos"]
                    mtm += (b["close"] - entry) * qty
                st["eq"].append(mtm)
    total, all_tr, sig, ent, dds = 0.0, [], 0, 0, []
    for s in B.SYMS:
        st = state[s]
        if st["pos"] is not None:                                   # pencere sonunda açık kaldıysa
            entry, stop, target, qty, i_entry, risk = st["pos"]
            px = data[s][-1]["close"]
            st["cash"] += (px - entry) * qty - (entry + px) * qty * (B.TAKER + B.SLIP)
        total += st["cash"]; all_tr += st["trades"]; sig += st["signals"]; ent += st["entries"]
        peak, dd = 0.0, 0.0
        for v in st["eq"]:
            peak = max(peak, v); dd = max(dd, (peak - v) / peak if peak > 0 else 0.0)
        dds.append(dd)
    n = len(all_tr)
    wins = [x["net"] for x in all_tr if x["net"] > 0]; losses = [-x["net"] for x in all_tr if x["net"] <= 0]
    return {"getiri_pct": round((total / B.EQUITY - 1) * 100, 2), "islem": n, "sinyal": sig, "giris": ent,
            "ort_R": round(sum(x["r"] for x in all_tr) / n, 4) if n else None,
            "isabet": round(100 * len(wins) / n, 1) if n else None,
            "PF": round(sum(wins) / sum(losses), 3) if losses else None,
            "ucret_usdt": round(sum(x["fees"] for x in all_tr), 2), "funding_usdt": round(sum(x["funding"] for x in all_tr), 2),
            "coin_ort_maks_dusus_pct": round(100 * sum(dds) / len(dds), 1),
            "ogrenilen_tablo_pencere_sonu": stats.table()}


def main():
    data = {s: B.bars4h(s) for s in B.SYMS}
    atrs = {s: B.atr_series(data[s]) for s in B.SYMS}
    funds = {s: B.funding(s) for s in B.SYMS}
    report = {}
    for arm, strict in (("S-E1", False), ("S-E2", True)):
        report[arm] = {}
        for name, f, t in B.WINS:
            r = run_window(strict, data, atrs, funds, B._ms(f), B._ms(t))
            report[arm][name] = r
            print(arm, name, "getiri %+.2f%%  islem %d/%d sinyal  ortR %s  isabet %s  PF %s" % (
                r["getiri_pct"], r["islem"], r["sinyal"], r["ort_R"], r["isabet"], r["PF"]), flush=True)
    io.open(Path(r"C:/Users/berke/research/entry_v1/out/benchmarks_v8_learn.json"), "w", encoding="utf-8").write(
        json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
