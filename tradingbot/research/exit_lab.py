"""Çıkış politikası laboratuvarı (`research_exit_lab_v1`) — kanonik işlem + GERÇEK bar yolu.

Mevcut `quant/exit_challenger.py` motoru AYNEN kullanılır (champion + en fazla üç önceden
tanımlı challenger, muhafazakâr bar-içi sıra: önce stop, sonra hedef). Bu modül yalnız köprüdür:

    kanonik defter işlemi + `research/bars.BarCache` barları  →  `compare_exit_policies` girdisi

Dürüstlük kuralları:
* Bütün politikalar AYNI barları, AYNI maliyet modelini ve AYNI ufku görür.
* Ufuk kanonik kapanışın ötesine PREREGISTERED bir uzatmayla taşınır (alternatif politika
  kanonik işlemden uzun yaşayabilir); uzatma bütün politikalar için aynıdır.
* Bar içinde stop ve hedefin sırası BİLİNMEZ. Muhafazakâr sıra kullanılır ve belirsiz bar
  sayısı AYRICA raporlanır; belirsizlik yüksekse sonuç `AMBIGUOUS_INTRABAR` işaretlenir.
* Champion simülasyonu kanonik sonucu ne kadar yeniden ürettiği ölçülür (`fidelity`); düşük
  sadakat, karşılaştırmanın zayıf olduğunu AÇIKÇA söyler.
* MFE sonradan ölçülen bir büyüklüktür — hiçbir politika onu önceden bilmez.
"""
from __future__ import annotations

import math
from typing import Any

from ..quant.exit_challenger import (CHAMPION_POLICY, DEFAULT_CHALLENGERS, compare_exit_policies,
                                     simulate_exit)
from .bars import MAPPING_OK, BarCache, verify_mapping

SCHEMA_VERSION = "research_exit_lab_v1"

#: Kanonik kapanıştan sonra alternatiflere tanınan azami ek ufuk (gün).
MAX_EXTENSION_DAYS = 7.0
#: Bir işlemin belirsiz sayılması için gereken bar-içi çakışma oranı.
AMBIGUITY_FLAG_RATIO = 0.10


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def measured_cost_per_fill_r(closed_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ölçülmüş dolum başına maliyet (R) — VARSAYIM değil, kanonik defterden.

    `risk_usdt = |net_pnl / r_multiple|`; dolum başına maliyet = ücret / (risk × dolum sayısı).
    Funding AYRI raporlanır (işaretli, alınabilir de ödenebilir de).
    """
    per_fill, funding_r, used = [], [], 0
    for r in closed_rows:
        net, rm, fees = _f(r.get("net_pnl")), _f(r.get("r_multiple")), _f(r.get("fees"))
        fund, nf = _f(r.get("funding")), int(r.get("n_fills") or 0)
        if None in (net, rm, fees) or rm == 0 or nf <= 0:
            continue
        risk = abs(net / rm)
        if risk <= 0:
            continue
        per_fill.append(abs(fees) / risk / nf)
        if fund is not None:
            funding_r.append(fund / risk)
        used += 1
    mean = (sum(per_fill) / len(per_fill)) if per_fill else None
    return {"cost_per_fill_r": (round(mean, 6) if mean is not None else None),
            "n_trades_used": used, "source": "MEASURED_FROM_CANONICAL_LEDGER",
            "mean_funding_r": (round(sum(funding_r) / len(funding_r), 6) if funding_r else None),
            "note": ("kayma dolum fiyatının içinde olduğu için burada AYRICA sayılmaz — "
                     "çift sayım koruması")}


def intrabar_ambiguity(bars: list[dict[str, float]], *, entry: float, stop: float,
                       targets: list[float], side: str) -> dict[str, Any]:
    """Aynı bar içinde hem stop hem hedef ulaşılabilir mi? Sıra BİLİNMEZ."""
    sign = 1.0 if str(side).upper() == "LONG" else -1.0
    risk = abs(entry - stop)
    if risk <= 0 or not bars:
        return {"n_bars": len(bars), "n_ambiguous": 0, "ratio": 0.0, "measurable": False}
    tgt = targets[0] if targets else None
    n_amb = 0
    for b in bars:
        hit_stop = (b["low"] <= stop) if sign > 0 else (b["high"] >= stop)
        hit_tgt = False
        if tgt is not None:
            hit_tgt = (b["high"] >= tgt) if sign > 0 else (b["low"] <= tgt)
        if hit_stop and hit_tgt:
            n_amb += 1
    ratio = n_amb / max(1, len(bars))
    return {"n_bars": len(bars), "n_ambiguous": n_amb, "ratio": round(ratio, 6),
            "measurable": True,
            "state": ("AMBIGUOUS_INTRABAR" if ratio >= AMBIGUITY_FLAG_RATIO else "OK"),
            "note": ("aynı barda hem stop hem hedef erişilebilir — muhafazakâr sıra "
                     "(önce stop) kullanıldı; gerçek sıra ÖLÇÜLEMEZ")}


def build_exit_trades(closed_rows: list[dict[str, Any]], cache: BarCache, *,
                      interval: str = "15m", cutoff_ms: float | None = None,
                      fetch: bool = True) -> dict[str, Any]:
    """Kanonik kapanışları çıkış motorunun beklediği biçime çevirir (bar yolu GERÇEK)."""
    trades, rejected, mapping = [], [], []
    for r in closed_rows:
        tid = str(r.get("trade_id"))
        entry, stop = _f(r.get("entry")), _f(r.get("initial_stop"))
        o_ms, c_ms = _f(r.get("opened_at_ms")), _f(r.get("closed_at_ms"))
        targets = [t for t in (_f(x) for x in (r.get("targets") or [])) if t is not None]
        if None in (entry, stop, o_ms, c_ms) or abs(entry - stop) <= 0:
            rejected.append({"trade_id": tid, "reason": "MISSING_ENTRY_STOP_OR_TIME"})
            continue
        span = c_ms - o_ms
        ext = min(span, MAX_EXTENSION_DAYS * 86_400_000.0)
        end_ms = c_ms + ext
        if cutoff_ms is not None:
            end_ms = min(end_ms, cutoff_ms)
        rows = cache.bars(r["symbol"], interval, o_ms, end_ms, fetch=fetch)
        vmap = verify_mapping(rows, reference_price=entry, reference_ms=o_ms)
        mapping.append({"trade_id": tid, "symbol": r["symbol"], **vmap})
        if vmap["state"] != MAPPING_OK or not rows:
            rejected.append({"trade_id": tid, "reason": f"MAPPING_{vmap['state']}"})
            continue
        path = [{"high": b["high"], "low": b["low"], "close": b["close"]}
                for b in rows if b["close_ms"] > o_ms]
        if not path:
            rejected.append({"trade_id": tid, "reason": "NO_BARS_AFTER_ENTRY"})
            continue
        trades.append({
            "id": tid, "trade_id": tid, "symbol": r["symbol"], "direction": r["side"],
            "entry_price": entry, "initial_stop": stop, "targets": targets,
            "price_path": path,
            "canonical_r": _f(r.get("r_multiple")), "canonical_exit": r.get("exit_reason"),
            "canonical_bars": len(path),
            "horizon_end_ms": end_ms, "extension_ms": ext,
            "ambiguity": intrabar_ambiguity([b for b in rows if b["close_ms"] > o_ms],
                                            entry=entry, stop=stop, targets=targets,
                                            side=r["side"]),
        })
    return {"trades": trades, "rejected": rejected, "mapping": mapping, "interval": interval}


def champion_fidelity(trades: list[dict[str, Any]], *, cost_per_fill_r: float) -> dict[str, Any]:
    """Champion simülasyonu kanonik gerçeği ne kadar yeniden üretiyor?

    Sadakat düşükse karşılaştırma zayıftır ve rapor bunu SÖYLER. Sonuç asla "champion yanlış"
    diye yorumlanmaz — bar çözünürlüğü ve ufuk uzatması farkı da bu sapmaya girer.
    """
    same_dir, n, diffs, exits = 0, 0, [], {"match": 0, "differ": 0}
    for t in trades:
        sim = simulate_exit(t, CHAMPION_POLICY, cost_per_fill_r=cost_per_fill_r)
        can = _f(t.get("canonical_r"))
        if sim is None or can is None:
            continue
        n += 1
        diffs.append(sim["net_r"] - can)
        if (sim["net_r"] >= 0) == (can >= 0):
            same_dir += 1
        cex = str(t.get("canonical_exit") or "").lower()
        simex = str(sim.get("exit_reason") or "").lower()
        hit = (("stop" in cex and "stop" in simex)
               or ("hedef" in cex and simex == "target")
               or (cex == simex))
        exits["match" if hit else "differ"] += 1
    mean = (sum(diffs) / len(diffs)) if diffs else None
    mae = (sum(abs(d) for d in diffs) / len(diffs)) if diffs else None
    return {"n": n, "sign_agreement": (round(same_dir / n, 4) if n else None),
            "mean_r_error": (round(mean, 4) if mean is not None else None),
            "mean_abs_r_error": (round(mae, 4) if mae is not None else None),
            "exit_reason_agreement": (round(exits["match"] / n, 4) if n else None),
            "note": ("sapma kaynakları: 15dk bar çözünürlüğü, dolum kayması, kısmi çıkış "
                     "kesirleri ve ufuk uzatması — 'champion hatalı' demek DEĞİLDİR")}


class _Lcg:
    """Deterministik LCG — aynı tohum, aynı bootstrap örneği (numpy bağımlılığı yok)."""

    def __init__(self, seed: int) -> None:
        self.s = int(seed) & 0xFFFFFFFF or 1

    def next_int(self, n: int) -> int:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s % max(1, n)


def paired_delta_ci(trades: list[dict[str, Any]], policy, *, cost_per_fill_r: float,
                    iters: int = 2000, seed: int = 7) -> dict[str, Any]:
    """EŞLEŞMİŞ fark (challenger − champion) ve bootstrap %95 GA.

    İki bootstrap birlikte raporlanır:
    * `by_trade`   — işlem düzeyinde (bağımsızlık VARSAYAR),
    * `by_symbol`  — sembol kümesi düzeyinde (aynı sembolün örtüşen işlemleri bağımsız DEĞİL).

    Küme bootstrap'ı daha geniş bir aralık verir; rapor ikisini de gösterir ve karar için
    GENİŞ olanı esas alır.
    """
    pairs: list[tuple[str, float]] = []
    for t in trades:
        base = simulate_exit(t, CHAMPION_POLICY, cost_per_fill_r=cost_per_fill_r)
        alt = simulate_exit(t, policy, cost_per_fill_r=cost_per_fill_r)
        if base is None or alt is None:
            continue
        pairs.append((str(t.get("symbol") or t.get("trade_id")), alt["net_r"] - base["net_r"]))
    if not pairs:
        return {"n": 0, "state": "NO_PAIRS"}
    deltas = [d for _, d in pairs]
    mean = sum(deltas) / len(deltas)

    def _boot(units: list[list[float]]) -> tuple[float | None, float | None]:
        if len(units) < 2:
            return None, None
        rng = _Lcg(seed)
        means = []
        for _ in range(iters):
            vals: list[float] = []
            for _ in range(len(units)):
                vals.extend(units[rng.next_int(len(units))])
            if vals:
                means.append(sum(vals) / len(vals))
        if not means:
            return None, None
        means.sort()
        lo = means[int(0.025 * len(means))]
        hi = means[min(len(means) - 1, int(0.975 * len(means)))]
        return round(lo, 4), round(hi, 4)

    by_trade = _boot([[d] for d in deltas])
    clusters: dict[str, list[float]] = {}
    for sym, d in pairs:
        clusters.setdefault(sym, []).append(d)
    by_symbol = _boot(list(clusters.values()))
    excludes_zero = (by_symbol[0] is not None and by_symbol[0] > 0) or \
                    (by_symbol[1] is not None and by_symbol[1] < 0)
    return {"n": len(deltas), "n_clusters": len(clusters),
            "mean_delta_r": round(mean, 4),
            "ci95_by_trade": list(by_trade), "ci95_by_symbol_cluster": list(by_symbol),
            "cluster_ci_excludes_zero": bool(excludes_zero),
            "note": ("aynı sembolün örtüşen işlemleri bağımsız örnek DEĞİLDİR; karar için "
                     "sembol-kümesi GA'sı esas alınır")}


def run_exit_comparison(closed_rows: list[dict[str, Any]], cache: BarCache, *,
                        interval: str = "15m", cutoff_ms: float | None = None,
                        cost_per_fill_r: float = 0.0,
                        adverse_multiplier: float = 2.0, seed: int = 7,
                        fetch: bool = True) -> dict[str, Any]:
    """Champion + üç challenger, AYNI barlar/maliyet; ayrıca önceden kayıtlı olumsuz maliyet."""
    built = build_exit_trades(closed_rows, cache, interval=interval, cutoff_ms=cutoff_ms,
                              fetch=fetch)
    trades = built["trades"]
    if not trades:
        return {"schema_version": SCHEMA_VERSION, "state": "NO_TRADES",
                "rejected": built["rejected"], "active_policy_changed": False}
    base = compare_exit_policies(trades, cost_per_fill_r=cost_per_fill_r)
    adverse = compare_exit_policies(trades, cost_per_fill_r=cost_per_fill_r * adverse_multiplier)
    amb_flagged = [t["trade_id"] for t in trades
                   if (t["ambiguity"].get("state") == "AMBIGUOUS_INTRABAR")]
    # Kaçırılan büyük kazananlar ve kurtarılan kayıplar AYRI raporlanır (tek yönlü okuma yok).
    return {
        "schema_version": SCHEMA_VERSION, "state": "RUN",
        "interval": interval, "n_trades": len(trades),
        "n_rejected": len(built["rejected"]), "rejected": built["rejected"],
        "mapping_verified": sum(1 for m in built["mapping"] if m.get("state") == MAPPING_OK),
        "extension_policy": {"max_extension_days": MAX_EXTENSION_DAYS,
                             "rule": "ufuk = kapanış + min(tutma süresi, 7 gün), export cutoff ile sınırlı",
                             "applies_to": "champion ve bütün challenger'lar AYNI"},
        "intrabar_ambiguity": {"n_flagged": len(amb_flagged), "trade_ids": amb_flagged,
                               "flag_ratio": AMBIGUITY_FLAG_RATIO,
                               "note": "bar içi stop/hedef sırası ÖLÇÜLEMEZ; muhafazakâr sıra kullanıldı"},
        "champion_fidelity": champion_fidelity(trades, cost_per_fill_r=cost_per_fill_r),
        "paired_deltas": {p.name: paired_delta_ci(trades, p, cost_per_fill_r=cost_per_fill_r,
                                                  seed=seed)
                          for p in DEFAULT_CHALLENGERS},
        "base_scenario": base,
        "adverse_scenario": {"cost_multiplier": adverse_multiplier, **adverse},
        "policies": {"champion": CHAMPION_POLICY.to_dict(),
                     "challengers": [p.to_dict() for p in DEFAULT_CHALLENGERS]},
        "active_policy_changed": False,
        "label": "OFFLINE RESEARCH — aktif çıkış politikası DEĞİŞMEDİ",
    }


__all__ = ["AMBIGUITY_FLAG_RATIO", "MAX_EXTENSION_DAYS", "SCHEMA_VERSION", "build_exit_trades",
           "champion_fidelity", "intrabar_ambiguity", "measured_cost_per_fill_r",
           "run_exit_comparison"]
