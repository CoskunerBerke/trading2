# -*- coding: utf-8 -*-
"""BOX THEORY V15 — ÇIKIŞ SÜPÜRMESİ.

Neden ayrı koşucu: tam `HistoricalReplay` 4h için yazılmış; 5m'de karar sayısı ~48 kat artar ve her
kararda çerçeve dilimlenir. Ölçüldü (probe_box_cost.py), tahmin edilmedi. Bu koşucu AYNI ÜRETİM
KURALINI çağırır (`tradingbot.box_theory.decide` / `targets_for`) ve şu iki yapıyı kullanır:

* **Giriş taraması bir kez.** Giriş/stop/yön çıkış koluna BAĞLI DEĞİLDİR; yalnız giriş ailesine
  (near_frac, trigger, long_stop, allow_outside, taraflar, stride) bağlıdır. Bu yüzden aile başına bir
  tarama yapılır, çıkış kolları bunun üzerinde ucuza koşar.
* **Doluluk kol başına.** Aynı sembolde pozisyon açıkken yeni giriş olmaz; uzun tutan bir çıkış kolu
  sonraki girişleri ENGELLER. Bu etki kol içinde modellenir — havuzlanmış kısayol yoktur.

Maliyetler gerçek: taker komisyonu + kayma iki bacakta, geçilen her funding settlement'ı arşivden
sorulur. Funding BİLİNMİYORSA sıfır sayılmaz; kol "funding_unknown" ile işaretlenir.
Aynı barda hem stop hem hedef değerse STOP kazanır (`ambiguity_policy: worst_case`).
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
WT = Path(r"C:/Users/berke/wt-entry")
sys.path.insert(0, str(WT))

from tradingbot.box_theory import BoxParams, decide, targets_for  # noqa: E402
from tradingbot.timeframes import DAY_MS, tf_ms  # noqa: E402

M5 = tf_ms("5m")
NL = chr(10)
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
CACHE_DEFAULT = r"C:/Users/berke/wt-ten/data"


#: V9'dan beri kullanılan üç pencere — başka pencere uydurulmaz.
WINDOWS = {"P1": ("2020-11-01", "2022-08-31"),
           "P2": ("2022-09-01", "2024-08-31"),
           "P3": ("2024-09-01", "2026-08-31")}

TAKER_PCT = 0.05          # config.fees.futures_taker_pct
SLIP_BPS = 3.0            # config.fees.slippage_bps
COST_LEG = TAKER_PCT / 100.0 + SLIP_BPS / 10_000.0     # tek bacak oransal maliyet

#: ÜRETİM BOYUTLANDIRMA KAPISI (risk/engine.py): notional <= özkaynak * max_position_pct/100 * maks(kaldıraç,1).
#: Boyutlandırma risk tabanlıdır: notional/özkaynak = risk_per_trade_pct / stop_pct. Dar stopta bu oran
#: patlar ve işlem üretimde HİÇ AÇILMAZ. Backtest R'yi ölçer; bu sayaç işlemin GERÇEKLEŞEBİLİR olup
#: olmadığını ölçer. İkisi ayrı sorudur ve ikisi de rapora girer.
RISK_PER_TRADE_PCT = 2.0      # config.risk.risk_per_trade_pct (PAPER_RESEARCH profili)
MAX_POSITION_PCT = 30.0       # config.risk.max_position_pct


def _ms(v: str) -> int:
    import datetime as dt
    return int(dt.datetime.fromisoformat(v).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# ------------------------------------------------------------------ veri
def _store(cache_dir: str):
    os.environ["TRADINGBOT_CACHE_DIR"] = str(cache_dir)
    from tradingbot.config import load_config
    from tradingbot.history import HistoryStore
    cfg = load_config(str(WT / "config.yaml"))
    return cfg, HistoryStore(cfg.cache_path / cfg.v3.history.root_dir)


def load_symbol(store, symbol: str) -> dict | None:
    """5m barları GÜNE göre gruplanmış satırlar + gün başına kutu (önceki günün uçları)."""
    m5 = store.read("futures", symbol, "5m")
    d1 = store.read("futures", symbol, "1d")
    if m5 is None or d1 is None or not len(m5) or not len(d1):
        return None
    daily = {}
    prev = None
    for t, h, lo in zip(d1["timestamp"], d1["high"], d1["low"]):
        t = int(t)
        if prev is not None:
            daily[t] = prev                     # gün t'nin kutusu = ÖNCEKİ günün uçları
        prev = {"timestamp": t, "high": float(h), "low": float(lo),
                "open": float(lo), "close": float(h)}
    days: dict[int, list[dict]] = {}
    for t, o, h, lo, c in zip(m5["timestamp"], m5["open"], m5["high"], m5["low"], m5["close"]):
        t = int(t)
        days.setdefault(t - (t % DAY_MS), []).append(
            {"timestamp": t, "open": float(o), "high": float(h), "low": float(lo), "close": float(c)})
    return {"symbol": symbol, "days": days, "daily": daily,
            "day_keys": sorted(days), "n_bars": int(len(m5))}


# ------------------------------------------------------------------ 1) giriş taraması
def scan_entries(series: dict, p: BoxParams, start_ms: int, end_ms: int, *, stride: int = 1) -> list[dict]:
    """Bir giriş ailesinin BÜTÜN tetikleri (doluluk UYGULANMADAN). Üretim `decide`ını çağırır."""
    out: list[dict] = []
    days, daily = series["days"], series["daily"]
    prev_day_rows: list[dict] = []
    for dk in series["day_keys"]:
        if dk + DAY_MS < start_ms or dk > end_ms:
            prev_day_rows = days[dk][-3:]
            continue
        box_prev = daily.get(dk)
        rows = days[dk]
        if box_prev is None or len(rows) < 3:
            prev_day_rows = rows[-3:]
            continue
        # `read_box` son satırı okur; iki satır MIN_DAILY_BARS içindir.
        drows = [box_prev, box_prev]
        tail = list(prev_day_rows)
        for j in range(len(rows)):
            if stride > 1 and (j % stride):
                continue
            act = decide(daily_rows=drows, m5_rows=tail + rows[: j + 1], position=None, params=p)
            if act and act.get("action") == "OPEN":
                out.append({"ts": int(rows[j]["timestamp"]), "bar": j, "day": dk,
                            "side": act["direction"], "entry": float(act["signal_close"]),
                            "stop": float(act["stop"]), "box_high": float(act["box_high"]),
                            "box_low": float(act["box_low"])})
        prev_day_rows = rows[-3:]
    return out


# ------------------------------------------------------------------ 2) çıkış benzetimi
def _bars_after(series: dict, day: int, bar: int):
    """Girişten SONRAKİ barlar, gün gün. Döner: (gun_key, bar_dict) üreteci."""
    keys = series["day_keys"]
    i = keys.index(day)
    for k in keys[i:]:
        rows = series["days"][k]
        start = bar + 1 if k == day else 0
        for r in rows[start:]:
            yield k, r


def simulate(series: dict, events: list[dict], p: BoxParams, *, funding=None,
             max_hold_days: int = 3) -> tuple[list[dict], dict]:
    """Kolun işlemleri. Doluluk modellenir: pozisyon açıkken gelen tetikler ATLANIR."""
    trades: list[dict] = []
    flags = {"skipped_occupied": 0, "no_target": 0, "funding_unknown": 0, "unclosed": 0, "cap_blocked": 0}
    cap = MAX_POSITION_PCT / 100.0 * max(int(p.leverage), 1)
    busy_until_ts = -1
    for ev in events:
        if ev["ts"] <= busy_until_ts:
            flags["skipped_occupied"] += 1
            continue
        side, entry, stop = ev["side"], ev["entry"], ev["stop"]
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        tg = targets_for(side, entry, stop, ev["box_high"], ev["box_low"], p)
        target = tg[0] if tg else None
        if target is None:
            flags["no_target"] += 1
        exit_px = exit_ts = None
        why = "OPEN"
        for k, bar in _bars_after(series, ev["day"], ev["bar"]):
            if k - ev["day"] > max_hold_days * DAY_MS:
                exit_px, exit_ts, why = float(bar["close"]), int(bar["timestamp"]), "MAX_HOLD"
                break
            hi, lo = bar["high"], bar["low"]
            hit_stop = (lo <= stop) if side == "LONG" else (hi >= stop)
            hit_tgt = target is not None and ((hi >= target) if side == "LONG" else (lo <= target))
            if hit_stop:                       # worst_case: aynı barda ikisi de değerse STOP
                exit_px, exit_ts, why = stop, int(bar["timestamp"]), "STOP"
                break
            if hit_tgt:
                exit_px, exit_ts, why = target, int(bar["timestamp"]), "TARGET"
                break
            if p.eod_close and k > ev["day"]:
                exit_px, exit_ts, why = float(bar["close"]), int(bar["timestamp"]), "EOD"
                break
        if exit_px is None:
            flags["unclosed"] += 1
            continue
        gross = (exit_px - entry) if side == "LONG" else (entry - exit_px)
        cost = (entry + exit_px) * COST_LEG
        fnd, unknown = 0.0, False
        if funding is not None:
            for s in range(((ev["ts"] // 28_800_000) + 1) * 28_800_000, exit_ts + 1, 28_800_000):
                r = funding(series["symbol"], _dt.datetime.fromtimestamp(s / 1000, tz=_dt.timezone.utc))
                if r is None:
                    unknown = True
                else:
                    fnd += float(r) * entry * (1 if side == "SHORT" else -1)
        if unknown:
            flags["funding_unknown"] += 1
        net = gross - cost + fnd
        # Üretimde bu işlem boyutlandırılabilir miydi? (R'yi DEĞİŞTİRMEZ; küme farkını ölçer.)
        notional_over_equity = RISK_PER_TRADE_PCT / max(risk / entry * 100.0, 1e-9)
        cap_ok = notional_over_equity <= cap + 1e-9
        if not cap_ok:
            flags["cap_blocked"] += 1
        trades.append({"cap_ok": bool(cap_ok), "notional_x_equity": round(notional_over_equity, 3),
                       "ts": ev["ts"], "exit_ts": exit_ts, "side": side, "why": why,
                       "entry": entry, "stop": stop, "exit": exit_px, "risk": risk,
                       "stop_pct": round(risk / entry * 100, 4),
                       "r_gross": round(gross / risk, 4), "r": round(net / risk, 4),
                       "cost_r": round(cost / risk, 4), "funding_r": round(fnd / risk, 6),
                       "hold_min": int((exit_ts - ev["ts"]) / 60_000)})
        busy_until_ts = exit_ts
    return trades, flags


# ------------------------------------------------------------------ 3) rapor
def stats(trades: list[dict]) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    rs = sorted(t["r"] for t in trades)
    tot = sum(rs)
    wins = [r for r in rs if r > 0]
    mean = tot / n
    var = sum((r - mean) ** 2 for r in rs) / n if n > 1 else 0.0
    se = (var ** 0.5) / (n ** 0.5) if n > 1 else 0.0
    gs = [t["r_gross"] for t in trades]
    gmean = sum(gs) / n
    gvar = sum((r - gmean) ** 2 for r in gs) / n if n > 1 else 0.0
    gse = (gvar ** 0.5) / (n ** 0.5) if n > 1 else 0.0
    sp = sorted(t["stop_pct"] for t in trades)
    return {"n": n, "sum_r": round(tot, 2), "mean_r": round(mean, 4),
            # BRUT = maliyetsiz. Kural maliyetten mi yoksa sinyalden mi kaybediyor: ayirt edici olcum.
            "mean_r_gross": round(gmean, 4), "sum_r_gross": round(sum(gs), 2),
            "gross_ci95_lo": round(gmean - 1.96 * gse, 4), "gross_ci95_hi": round(gmean + 1.96 * gse, 4),
            "stop_pct_p10": round(sp[n // 10], 4), "stop_pct_med": round(sp[n // 2], 4),
            "stop_pct_p90": round(sp[(9 * n) // 10], 4),
            "ci95_lo": round(mean - 1.96 * se, 4), "ci95_hi": round(mean + 1.96 * se, 4),
            "median_r": round(rs[n // 2], 4), "win_rate": round(len(wins) / n, 4),
            "mean_cost_r": round(sum(t["cost_r"] for t in trades) / n, 4),
            "mean_stop_pct": round(sum(t["stop_pct"] for t in trades) / n, 4),
            "mean_hold_min": round(sum(t["hold_min"] for t in trades) / n, 1),
            # ÜRETİMDE AÇILABİLİR ALT KÜME: tavanı geçen işlemler ve YALNIZ onların beklentisi.
            "cap_ok_n": sum(1 for t in trades if t["cap_ok"]),
            "cap_ok_share": round(sum(1 for t in trades if t["cap_ok"]) / n, 4),
            "cap_ok_mean_r": (round(sum(t["r"] for t in trades if t["cap_ok"])
                                    / max(1, sum(1 for t in trades if t["cap_ok"])), 4)
                              if any(t["cap_ok"] for t in trades) else None),
            "median_notional_x_equity": round(sorted(t["notional_x_equity"] for t in trades)[n // 2], 2),
            "why": {w: sum(1 for t in trades if t["why"] == w) for w in ("STOP", "TARGET", "EOD", "MAX_HOLD")},
            "by_side": {s: {"n": sum(1 for t in trades if t["side"] == s),
                            "mean_r": round(sum(t["r"] for t in trades if t["side"] == s)
                                            / max(1, sum(1 for t in trades if t["side"] == s)), 4)}
                        for s in ("LONG", "SHORT")}}


def entry_key(p: BoxParams, stride: int) -> tuple:
    return (round(p.near_frac, 4), p.trigger, p.long_stop, p.allow_outside,
            p.loc_lookback, p.allow_long, p.allow_short, stride)


def _window_of(ts: int) -> str | None:
    for w, (a, b) in WINDOWS.items():
        if _ms(a) <= ts <= _ms(b) + DAY_MS:
            return w
    return None


def run(entry_arms: list[tuple[BoxParams, int]], exit_arms: list[BoxParams], *,
        windows=("P1", "P2", "P3"), symbols=None, cache_dir: str = CACHE_DEFAULT,
        tag: str = "box_v15") -> dict:
    """Aile başına TEK tarama (tüm tarih), sonra pencereye göre bölme; çıkış kolları bunun üzerinde ucuz.

    Bölme burada bir kısayol DEĞİLDİR: giriş olayları çıkış koluna bağlı olmadığı için pencereye ayırmak
    sonucu değiştirmez. Doluluk her kolda ve her pencerede AYRI kurulur (kol içi, havuzlanmış değil).
    """
    cfg, store = _store(cache_dir)
    from tradingbot.replay.funding_archive import ArchiveFundingRates
    funding = ArchiveFundingRates(store)
    # EVREN: sabit liste YOK — canli config'ten CAGRI ANINDA cozulur (universe.py).
    # Import aninda dondurmak V14'te yanlis arsivin okunmasina yol acmisti.
    from universe import resolve as _resolve_universe
    syms = list(symbols) if symbols else _resolve_universe()[0]
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    loaded = {}
    for s in syms:
        ser = load_symbol(store, s)
        if ser:
            loaded[s] = ser
    meta = {"tag": tag, "history_root": str(store.root), "symbols": sorted(loaded),
            "missing_symbols": [s for s in syms if s not in loaded],
            "windows": {w: WINDOWS[w] for w in windows},
            "cost_per_leg_pct": round(COST_LEG * 100, 5),
            "n_bars": {s: loaded[s]["n_bars"] for s in loaded},
            "load_s": round(time.time() - t0, 1), "results": []}
    lo, hi = _ms(WINDOWS[windows[0]][0]), _ms(WINDOWS[windows[-1]][1]) + DAY_MS
    for ep, stride in entry_arms:
        t1 = time.time()
        ev_by_sym = {s: scan_entries(loaded[s], ep, lo, hi, stride=stride) for s in loaded}
        scan_s = round(time.time() - t1, 1)
        n_all = sum(len(v) for v in ev_by_sym.values())
        print("  tarama %-46s stride=%d olay=%6d %6.1fs" % (ep.label(), stride, n_all, scan_s), flush=True)
        for wname in windows:
            a, b = _ms(WINDOWS[wname][0]), _ms(WINDOWS[wname][1]) + DAY_MS
            win_ev = {s: [e for e in v if a <= e["ts"] <= b] for s, v in ev_by_sym.items()}
            n_ev = sum(len(v) for v in win_ev.values())
            for xp in exit_arms:
                p = BoxParams(**{**asdict(ep), "exit_kind": xp.exit_kind, "exit_r": xp.exit_r,
                                 "eod_close": xp.eod_close}).validate()
                tr, fl = [], {}
                per_sym = {}
                for s, evs in win_ev.items():
                    t_, f_ = simulate(loaded[s], evs, p, funding=funding)
                    tr += t_
                    per_sym[s] = {"n": len(t_), "sum_r": round(sum(x["r"] for x in t_), 2)}
                    for k, v in f_.items():
                        fl[k] = fl.get(k, 0) + v
                meta["results"].append({
                    "window": wname, "entry": ep.label(), "stride": stride,
                    "exit": p.exit_kind if p.exit_kind != "r_multiple" else "r%g" % p.exit_r,
                    "eod": bool(p.eod_close), "min_stop_pct": float(ep.min_stop_pct),
                    "arm": "%s|%s|s%d" % (ep.label(), p.label(), stride),
                    "n_events": n_ev, "scan_s": scan_s, "flags": fl,
                    "by_symbol": per_sym, "stats": stats(tr)})
    meta["funding_missing_series"] = sorted(funding.missing_series)
    meta["funding_unknown_lookups"] = int(funding.unknown)
    meta["elapsed_s"] = round(time.time() - t0, 1)
    io.open(OUT / ("BOX_SWEEP_%s.json" % tag), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=1, default=str))
    write_report(meta, tag)
    return meta


def write_report(meta: dict, tag: str) -> Path:
    """Markdown özet: kol x pencere, BRUT ve NET ayrı. Kazanan ilan edilmez; sayılar konur."""
    L = ["# BOX THEORY V15 — çıkış süpürmesi (%s)" % tag, "",
         "Arşiv: `%s` · semboller: %d · bacak başı maliyet: %%%.3f · funding eksik seri: %s"
         % (meta["history_root"], len(meta["symbols"]), meta["cost_per_leg_pct"],
            meta.get("funding_missing_series") or "yok"), "",
         "`R` = (çıkış−giriş)/|giriş−stop|. BRUT = maliyetsiz; NET = komisyon+kayma+funding sonrası.", "",
         "`cap_ok` = üretimin tek-coin notional tavanını geçebilen işlemlerin oranı "
         "(risk %%%.1f, tavan %%%.0f x kaldıraç). Geçemeyen işlem üretimde HİÇ açılmaz."
         % (RISK_PER_TRADE_PCT, MAX_POSITION_PCT), "",
         "| pencere | min_stop% | stride | çıkış | n | BRUT ortR | BRUT %95 alt | NET ortR | maliyet R | medyan stop% | medyan notional×özk | cap_ok | kazanç | STOP/TGT/EOD |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in meta["results"]:
        st = r["stats"]
        if not st.get("n"):
            L.append("| %s | %g | %d | %s | 0 | — | — | — | — | — | — | — | — | — |"
                     % (r["window"], r["min_stop_pct"], r["stride"], r["exit"]))
            continue
        w = st["why"]
        L.append("| %s | %g | %d | %s | %d | %+.4f | %+.4f | %+.4f | %.3f | %.3f | %.1f | %.3f | %.3f | %d/%d/%d |"
                 % (r["window"], r["min_stop_pct"], r["stride"], r["exit"], st["n"],
                    st["mean_r_gross"], st["gross_ci95_lo"], st["mean_r"], st["mean_cost_r"],
                    st["stop_pct_med"], st.get("median_notional_x_equity", 0), st.get("cap_ok_share", 0),
                    st["win_rate"], w["STOP"], w["TARGET"], w["EOD"]))
    p = OUT / ("BOX_SWEEP_%s.md" % tag)
    io.open(p, "w", encoding="utf-8", newline=NL).write(NL.join(L) + NL)
    return p


__all__ = ["BoxParams", "WINDOWS", "load_symbol", "run", "scan_entries", "simulate", "stats"]
