# -*- coding: utf-8 -*-
"""EKONOMİK DEĞERLENDİRME — iki AYRI soru, aynı kural sürümü ve maliyet yöntemiyle:

  (1) SIKLIK: aynı kuralla incelenen uygun KAPALI 15m barı başına kaç şekil / kaç teyit / kaç plan / kaç giriş oluşuyor?
  (2) SONUÇ: bu girişlerden açılan işlemler MALİYET SONRASI ne üretiyor (net R, profit factor, isabet, ort. kazanç/kayıp,
      maks. düşüş, maruziyet, işlem sayısı ve belirsizlik)?

Kohortlar örtüşmez (`universe.COHORTS`). Bu bir GÖZLEMSEL karşılaştırmadır: coin yaşının nedensel etkisi DEĞİLDİR;
veri miktarı, likidite ve piyasa dönemi farkları her satırda görünür. Az gözlemde hüküm `BELİRSİZ`dir; sıklık yüksek
ama net sonuç negatifse "yeni coin avantajı" İLAN EDİLMEZ. Kapanmamış (açık) pozisyonlar sonuç bölümüne GİRMEZ;
ayrı sayılır. Bootstrap aralığı deterministiktir (sabit tohum) ve örneklem küçükse yayımlanır ama hüküm vermez.
"""
from __future__ import annotations

import math
import random
from typing import Any

from ..core import iso, utc_now
from .strategy import PROTOCOL_VERSION
from .universe import COHORTS, COHORT_UNKNOWN

SCHEMA_VERSION = "pattern_report_v1"
#: Hüküm için asgari kapanmış işlem. Altında sonuç YAYIMLANIR ama "avantaj var/yok" denmez.
MIN_TRADES_FOR_VERDICT = 30
VERDICT_UNDECIDED = "BELİRSİZ (örneklem yetersiz)"


def _r_stats(rs: list[float]) -> dict[str, Any]:
    n = len(rs)
    if n == 0:
        return {"n": 0, "mean_r": None, "median_r": None, "sum_r": 0.0, "win_rate": None, "avg_win_r": None, "avg_loss_r": None,
                "profit_factor": None, "max_drawdown_r": None, "std_r": None, "ci95_mean_r": None}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    mean = sum(rs) / n
    var = sum((r - mean) ** 2 for r in rs) / (n - 1) if n > 1 else 0.0
    return {"n": n, "mean_r": round(mean, 4), "median_r": round(sorted(rs)[n // 2], 4), "sum_r": round(sum(rs), 4),
            "win_rate": round(len(wins) / n, 4), "avg_win_r": round(sum(wins) / len(wins), 4) if wins else None,
            "avg_loss_r": round(sum(losses) / len(losses), 4) if losses else None,
            "profit_factor": (round(gross_win / gross_loss, 4) if gross_loss > 0 else ("SONSUZ (kayıp yok)" if gross_win > 0 else None)),
            "max_drawdown_r": round(dd, 4), "std_r": round(math.sqrt(var), 4) if n > 1 else None,
            "ci95_mean_r": _bootstrap_ci(rs)}


def _bootstrap_ci(rs: list[float], *, iters: int = 2000, seed: int = 20260916) -> list[float] | None:
    """Ortalama R için %95 bootstrap aralığı (deterministik tohum). n < 5 ise None: aralık uydurulmaz."""
    n = len(rs)
    if n < 5:
        return None
    rnd = random.Random(seed)
    means = []
    for _ in range(iters):
        means.append(sum(rs[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return [round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters)], 4)]


def _funding_coverage(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """MALİYET KAPSAMASI (2026-09-17): kaç işlemin funding'i GERÇEKTEN mutabık. `complete` olmayan işlemin
    `funding` alanı ölçülmüş bir değer DEĞİLDİR (bilinmeyen dönem sıfır maliyet sayılmaz); `unknown` ise kaydın
    kapsama bilgisi hiç yoktur (onarımdan ÖNCE kapanmış eski işlemler — onlara uydurma maliyet EKLENMEZ)."""
    out = {"trades": len(trades), "complete": 0, "incomplete": 0, "unknown": 0, "settlements_due": 0,
           "settlements_settled": 0, "incomplete_trade_ids": []}
    for t in trades:
        cov = (t.get("features") or {}).get("funding_coverage")
        if not isinstance(cov, dict):
            out["unknown"] += 1
            continue
        if cov.get("due") is not None:
            out["settlements_due"] += int(cov["due"])
            out["settlements_settled"] += int(cov.get("settled") or 0)
        if cov.get("complete"):
            out["complete"] += 1
        else:
            out["incomplete"] += 1
            if t.get("id"):
                out["incomplete_trade_ids"].append(str(t["id"]))
    out["incomplete_trade_ids"] = out["incomplete_trade_ids"][:20]
    return out


def _cost_stats(trades: list[dict[str, Any]]) -> dict[str, Any]:
    fees = sum(float(t.get("fees") or 0) for t in trades)
    funding = sum(float(t.get("funding") or 0) for t in trades)
    gross = sum(float(t.get("gross_pnl") or 0) for t in trades)
    net = sum(float(t.get("net_pnl") or 0) for t in trades)
    cov = _funding_coverage(trades)
    return {"gross_pnl_usdt": round(gross, 6), "net_pnl_usdt": round(net, 6), "fees_usdt": round(fees, 6),
            "funding_usdt": round(funding, 6), "cost_usdt": round(gross - net, 6),
            # `funding_usdt` YALNIZ her işlemin funding'i mutabıksa ÖLÇÜLMÜŞ sayılır; aksi hâlde ALT SINIRDIR.
            "funding_measured": bool(trades) and cov["incomplete"] == 0 and cov["unknown"] == 0,
            "funding_coverage": cov}


def _bucket(trade: dict[str, Any]) -> str:
    f = trade.get("features") or {}
    return str(f.get("cohort") or COHORT_UNKNOWN)


def build_report(summary: dict[str, Any], *, history: list[dict[str, Any]] | None = None, findings: dict[str, Any] | None = None,
                 plans: dict[str, Any] | None = None, scan: dict[str, Any] | None = None, now=None) -> dict[str, Any]:
    """`pattern_trader.json` özeti + defter geçmişi + bulgu/plan kayıtları → ekonomik rapor.

    `history`: `FuturesLedgerV2.history_dicts()` (kapanmış işlemler; `features.cohort/family/plan_id` taşır).
    `scan`: `pattern_scan.json` (kapsama/kohort tarama yükü — sıklık paydası için).
    """
    now = now or utc_now()
    trades = [t for t in (history or []) if isinstance(t, dict)]
    fnd = list((findings or {}).values())
    pls = list((plans or {}).values())
    cohort_scan = dict((summary or {}).get("cohort_stats") or {})
    open_positions = dict((summary or {}).get("positions") or {})

    def _rows(subset: list[dict[str, Any]]) -> list[float]:
        return [float(t.get("r_multiple") or 0.0) for t in subset]

    rows: list[dict[str, Any]] = []
    names = [c[0] for c in COHORTS] + [COHORT_UNKNOWN]
    for name in names:
        tr = [t for t in trades if _bucket(t) == name]
        f_all = [f for f in fnd if str(f.get("cohort") or COHORT_UNKNOWN) == name]
        f_conf = [f for f in f_all if f.get("status") == "CONFIRMED"]
        p_all = [p for p in pls if str(p.get("cohort") or COHORT_UNKNOWN) == name]
        cs = cohort_scan.get(name) or {}
        bars = int(cs.get("bars_15m_scanned") or 0)
        row = {"cohort": name, "scanned_symbols": int(cs.get("scans") or 0), "bars_15m_scanned": bars,
               "findings": len(f_all), "confirmed": len(f_conf), "plans": len(p_all),
               "opened": int(cs.get("opened") or 0), "closed_trades": len(tr),
               "open_positions": sum(1 for p in open_positions.values() if str(p.get("cohort") or COHORT_UNKNOWN) == name),
               "findings_per_1k_bars": round(len(f_all) / bars * 1000, 3) if bars else None,
               "confirmed_per_1k_bars": round(len(f_conf) / bars * 1000, 3) if bars else None,
               "plans_per_1k_bars": round(len(p_all) / bars * 1000, 3) if bars else None,
               "confirm_rate": round(len(f_conf) / len(f_all), 4) if f_all else None,
               "outcome": _r_stats(_rows(tr)), "costs": _cost_stats(tr)}
        row["verdict"] = (VERDICT_UNDECIDED if len(tr) < MIN_TRADES_FOR_VERDICT else
                          ("MALİYET SONRASI POZİTİF" if (row["outcome"]["mean_r"] or 0) > 0 else "MALİYET SONRASI NEGATİF"))
        rows.append(row)
    new_names = {c[0] for c in COHORTS if c[2] is not None and c[2] <= 720.0}
    new_tr = [t for t in trades if _bucket(t) in new_names]
    est_tr = [t for t in trades if _bucket(t) not in new_names and _bucket(t) != COHORT_UNKNOWN]
    new_bars = sum(int((cohort_scan.get(n) or {}).get("bars_15m_scanned") or 0) for n in new_names)
    est_bars = sum(int(v.get("bars_15m_scanned") or 0) for k, v in cohort_scan.items() if k not in new_names and k != COHORT_UNKNOWN)
    new_f = [f for f in fnd if str(f.get("cohort")) in new_names]
    est_f = [f for f in fnd if str(f.get("cohort")) not in new_names and str(f.get("cohort")) != COHORT_UNKNOWN]
    comparison = {
        "question_1_frequency": {
            "note_tr": "Aynı kural sürümü ve aynı 15m dilimiyle incelenen KAPALI bar başına şekil/teyit sayısı.",
            "new_listings": {"bars_15m": new_bars, "findings": len(new_f), "confirmed": sum(1 for f in new_f if f.get("status") == "CONFIRMED"),
                             "findings_per_1k_bars": round(len(new_f) / new_bars * 1000, 3) if new_bars else None},
            "established": {"bars_15m": est_bars, "findings": len(est_f), "confirmed": sum(1 for f in est_f if f.get("status") == "CONFIRMED"),
                            "findings_per_1k_bars": round(len(est_f) / est_bars * 1000, 3) if est_bars else None},
        },
        "question_2_outcome": {
            "note_tr": "Bu girişlerden açılan işlemlerin MALİYET SONRASI sonucu. Sıklık yüksek olup sonuç negatifse avantaj YOKTUR.",
            "new_listings": {"outcome": _r_stats(_rows(new_tr)), "costs": _cost_stats(new_tr)},
            "established": {"outcome": _r_stats(_rows(est_tr)), "costs": _cost_stats(est_tr)},
        },
        "verdict": (VERDICT_UNDECIDED if (len(new_tr) < MIN_TRADES_FOR_VERDICT or len(est_tr) < MIN_TRADES_FOR_VERDICT)
                    else "GÖZLEMSEL FARK: yeni %.4fR vs yerleşik %.4fR (nedensellik İDDİA EDİLMEZ)" % (
                        _r_stats(_rows(new_tr))["mean_r"] or 0.0, _r_stats(_rows(est_tr))["mean_r"] or 0.0)),
        "limitations_tr": ["Gözlemsel karşılaştırma; coin yaşının nedensel etkisi ölçülmedi.",
                           "Veri miktarı, likidite ve piyasa dönemi kohortlar arasında farklıdır (tarama payda sayıları yukarıda).",
                           "İleri testin henüz oluşmamış ayları bu raporda YOKTUR; kapanmamış pozisyonlar sonuç bölümüne girmez.",
                           "Örneklem %d kapanmış işlemin altındaysa hüküm verilmez (şu an: yeni %d, yerleşik %d)." % (MIN_TRADES_FOR_VERDICT, len(new_tr), len(est_tr))],
    }
    by_family: list[dict[str, Any]] = []
    for fam in sorted({str((t.get("features") or {}).get("family") or "?") for t in trades}):
        tr = [t for t in trades if str((t.get("features") or {}).get("family") or "?") == fam]
        by_family.append({"family": fam, "outcome": _r_stats(_rows(tr)), "costs": _cost_stats(tr),
                          "verdict": VERDICT_UNDECIDED if len(tr) < MIN_TRADES_FOR_VERDICT else ("POZİTİF" if (_r_stats(_rows(tr))["mean_r"] or 0) > 0 else "NEGATİF")})
    exits: dict[str, int] = {}
    for t in trades:
        exits[str(t.get("exit_reason") or "?")] = exits.get(str(t.get("exit_reason") or "?"), 0) + 1
    all_stats = _r_stats(_rows(trades))
    return {"schema_version": SCHEMA_VERSION, "generated_at": iso(now), "protocol_version": PROTOCOL_VERSION,
            "status_tr": "ÇALIŞAN ÜRÜN + PAPER DENEYİ — kârlılık kanıtı YOK; aşağıdaki sayılar ileri testin BUGÜNE KADARKİ kısmıdır.",
            "totals": {"closed_trades": len(trades), "open_positions": len(open_positions), "findings": len(fnd), "plans": len(pls),
                       "outcome": all_stats, "costs": _cost_stats(trades),
                       "verdict": VERDICT_UNDECIDED if len(trades) < MIN_TRADES_FOR_VERDICT else ("MALİYET SONRASI POZİTİF" if (all_stats["mean_r"] or 0) > 0 else "MALİYET SONRASI NEGATİF")},
            "by_cohort": rows, "by_family": by_family, "exit_reasons": exits,
            "new_vs_established": comparison,
            "coverage": (scan or {}).get("coverage"), "universe_counts": ((scan or {}).get("universe") or {}).get("counts"),
            "min_trades_for_verdict": MIN_TRADES_FOR_VERDICT}


__all__ = ["SCHEMA_VERSION", "MIN_TRADES_FOR_VERDICT", "VERDICT_UNDECIDED", "build_report"]
