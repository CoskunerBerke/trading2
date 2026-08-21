"""Loss attribution — hangi bağlamda (rejim/taraf/sembol/koşul) zarar edildiğinin DÜRÜST özeti.

Bu modül ilişki (association) raporlar; nedensellik iddiası ETMEZ. Her satırda örnek sayısı ve güven
aralığı bulunur; küçük örnekte "yetersiz" işaretlenir.
"""
from __future__ import annotations

import math

MIN_BUCKET = 8
DISCLAIMER = ("Bu bulgular ilişki (association) düzeyindedir; nedensellik kanıtı DEĞİLDİR. "
              "Örnek sayısı düşük gruplarda tesadüf olasılığı yüksektir.")


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if not n:
        return {"n": 0, "expectancy_r": None, "ci95_low": None, "win_rate": None, "sufficient": False}
    m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / n) if n > 1 else 0.0
    ci = m - 1.96 * sd / math.sqrt(n) if n > 1 else None
    return {"n": n, "expectancy_r": round(m, 4), "ci95_low": (round(ci, 4) if ci is not None else None),
            "win_rate": round(sum(1 for x in rs if x > 0) / n, 4), "sufficient": n >= MIN_BUCKET}


def _bucket(value: float | None, edges: list[float], labels: list[str]) -> str | None:
    if value is None:
        return None
    for e, lab in zip(edges, labels):
        if value < e:
            return lab
    return labels[-1]


def attribution_report(rows: list[dict], *, min_bucket: int = MIN_BUCKET) -> dict:
    """Kesitler: taraf, sembol, volatilite rejimi, fiyatın MA99'a konumu, funding, taraf×rejim."""
    cuts: dict[str, dict[str, list[float]]] = {k: {} for k in
                                               ("side", "symbol", "vol_regime", "vs_ma99", "funding", "side_x_regime",
                                                "consensus", "leverage")}
    for r in rows:
        out = r.get("outcome") or {}
        snap = r.get("snapshot") or {}
        vals = snap.get("values") or {}
        rr = out.get("r_multiple")
        if rr is None:
            continue
        rr = float(rr)
        side = str(out.get("side") or ("LONG" if vals.get("is_long") else "SHORT"))
        symbol = str(out.get("symbol") or snap.get("symbol") or "?")
        reg = vals.get("vol_regime_code")
        reg_lab = {0: "LOW_VOL", 1: "NORMAL", 2: "HIGH_VOL", 3: "EXTREME"}.get(int(reg)) if reg is not None else None
        ma99 = _bucket(vals.get("px_vs_ma99_pct"), [-2.0, 2.0], ["MA99_ALTINDA", "MA99_YAKIN", "MA99_ÜSTÜNDE"])
        fund = _bucket(vals.get("funding_rate"), [-0.0001, 0.0001], ["FUNDING_NEG", "FUNDING_NÖTR", "FUNDING_POZ"])
        cons = _bucket(abs(vals["consensus_score"]) if vals.get("consensus_score") is not None else None,
                       [0.2, 0.5], ["KONSENSÜS_ZAYIF", "KONSENSÜS_ORTA", "KONSENSÜS_GÜÇLÜ"])
        lev = _bucket(vals.get("leverage"), [1.5, 3.0], ["KALDIRAÇ_1x", "KALDIRAÇ_ORTA", "KALDIRAÇ_YÜKSEK"])
        cuts["side"].setdefault(side, []).append(rr)
        cuts["symbol"].setdefault(symbol, []).append(rr)
        if reg_lab:
            cuts["vol_regime"].setdefault(reg_lab, []).append(rr)
            cuts["side_x_regime"].setdefault(f"{side}|{reg_lab}", []).append(rr)
        if ma99:
            cuts["vs_ma99"].setdefault(ma99, []).append(rr)
        if fund:
            cuts["funding"].setdefault(fund, []).append(rr)
        if cons:
            cuts["consensus"].setdefault(cons, []).append(rr)
        if lev:
            cuts["leverage"].setdefault(lev, []).append(rr)
    report = {cut: {k: _stats(v) for k, v in sorted(groups.items())} for cut, groups in cuts.items()}
    findings: list[str] = []
    for cut, groups in report.items():
        for label, st in groups.items():
            if st["n"] >= min_bucket and st["expectancy_r"] is not None and st["ci95_low"] is not None:
                if st["ci95_low"] < 0 and st["expectancy_r"] < 0:
                    findings.append(f"{cut}={label}: OOS beklenti {st['expectancy_r']:+.3f}R "
                                    f"(n={st['n']}, CI95 alt {st['ci95_low']:+.3f}) — negatif ilişki")
                elif st["ci95_low"] > 0:
                    findings.append(f"{cut}={label}: OOS beklenti {st['expectancy_r']:+.3f}R "
                                    f"(n={st['n']}, CI95 alt {st['ci95_low']:+.3f}) — pozitif ilişki")
    weak = {cut: [k for k, v in g.items() if not v["sufficient"]] for cut, g in report.items()}
    missing_rate = {}
    if rows:
        snaps = [r.get("snapshot") or {} for r in rows if r.get("snapshot")]
        if snaps:
            for name in sorted({m for s in snaps for m in (s.get("missing") or [])}):
                missing_rate[name] = round(sum(1 for s in snaps if name in (s.get("missing") or [])) / len(snaps), 3)
    return {"schema": "loss_attribution_v1", "n_rows": len(rows), "min_bucket": min_bucket,
            "cuts": report, "findings": findings, "insufficient_groups": weak,
            "missing_field_rate": missing_rate, "disclaimer": DISCLAIMER}


__all__ = ["DISCLAIMER", "MIN_BUCKET", "attribution_report"]
