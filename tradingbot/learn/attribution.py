"""Kayıp analizi — hangi koşulda, ne kadar örnekle, ne kadar güvenle zarar edildiğinin YAPILANDIRILMIŞ özeti.

Bu modül ilişki (association) raporlar; nedensellik iddia ETMEZ. Her satırda örnek sayısı ve güven
aralığı bulunur; küçük örnekte `INSUFFICIENT_EVIDENCE` işaretlenir.

Üç katman:
1. `classify_trade`      — tek işlem için kayıp sınıfı adayları + kanıt (snapshot + outcome'dan).
2. `trade_attribution`   — her kayıp işlem için yapılandırılmış kayıt (koşullar, koşullu istatistik, delta).
3. `attribution_report`  — kesitler (taraf/sembol/setup/rejim/volatilite/MA/RSI/funding/spread/pattern/
                           anlaşmazlık/maliyet) + `findings` (politika üretiminin girdisi).

`findings` sözleşmesi `learn/policy.py::candidates_from_attribution` tarafından tüketilir: her bulgu
tek bir gözlemlenebilir koşulu, o koşulun OOS istatistiğini ve baseline'a göre farkını taşır.
"""
from __future__ import annotations

import math

MIN_BUCKET = 8
DISCLAIMER = ("Bu bulgular ilişki (association) düzeyindedir; nedensellik kanıtı DEĞİLDİR. "
              "Örnek sayısı düşük gruplarda tesadüf olasılığı yüksektir.")

LOSS_CLASSES = ("WRONG_DIRECTION", "LATE_ENTRY", "VOLATILITY_MISMATCH", "TREND_MISMATCH",
                "WEAK_MOMENTUM", "LOW_LIQUIDITY", "COST_DRAG", "FUNDING_DRAG", "PATTERN_NEGATIVE",
                "AGENT_DISAGREEMENT", "STOP_TOO_CLOSE_ASSOCIATION", "TIME_EXIT_NO_EDGE",
                "INSUFFICIENT_EVIDENCE")

# Kesit tanımları: (kesit adı, snapshot alanı, eşikler, etiketler). Politika üretimi bu adları kullanır.
VOL_LABELS = ("LOW_VOL", "NORMAL", "HIGH_VOL", "EXTREME")


def _stats(rs: list[float], *, min_bucket: int = MIN_BUCKET) -> dict:
    n = len(rs)
    if not n:
        return {"n": 0, "expectancy_r": None, "ci95_low": None, "win_rate": None,
                "profit_factor": None, "sufficient": False}
    m = sum(rs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in rs) / n) if n > 1 else 0.0
    ci = m - 1.96 * sd / math.sqrt(n) if n > 1 else None
    wins = sum(x for x in rs if x > 0)
    losses = abs(sum(x for x in rs if x < 0))
    return {"n": n, "expectancy_r": round(m, 4), "ci95_low": (round(ci, 4) if ci is not None else None),
            "win_rate": round(sum(1 for x in rs if x > 0) / n, 4),
            "profit_factor": (round(wins / losses, 4) if losses > 0 else None),
            "sufficient": n >= min_bucket}


def _bucket(value: float | None, edges: list[float], labels: list[str]) -> str | None:
    if value is None:
        return None
    for e, lab in zip(edges, labels):
        if value < e:
            return lab
    return labels[-1]


def _vals(row: dict) -> dict:
    return ((row or {}).get("snapshot") or {}).get("values") or {}


def _side(row: dict) -> str:
    out = row.get("outcome") or {}
    v = _vals(row)
    return str(out.get("side") or (row.get("direction") or "") or ("LONG" if v.get("is_long") else "SHORT")).upper()


def _cuts_for(row: dict) -> dict[str, str | None]:
    """Bir kaydın bütün kesit etiketleri — hem rapor hem politika üretimi bu adları kullanır."""
    v, out = _vals(row), (row.get("outcome") or {})
    snap = (row.get("snapshot") or {})
    reg = v.get("vol_regime_code")
    cons = v.get("consensus_score")
    return {
        "side": _side(row),
        "symbol": str(out.get("symbol") or snap.get("symbol") or row.get("symbol") or "?"),
        "setup": str(row.get("setup_type") or "-"),
        "regime": str(row.get("regime") or "-"),
        "vol_regime": (VOL_LABELS[int(reg)] if reg is not None and 0 <= int(reg) < len(VOL_LABELS) else None),
        "vs_ma99": _bucket(v.get("px_vs_ma99_pct"), [-2.0, 2.0], ["MA99_ALTINDA", "MA99_YAKIN", "MA99_ÜSTÜNDE"]),
        "ma_cross": (None if v.get("ma_cross_dir") is None else ("CROSS_UP" if v["ma_cross_dir"] > 0 else "CROSS_DOWN")),
        "rsi": _bucket(v.get("rsi_fast"), [35.0, 65.0], ["RSI_DÜŞÜK", "RSI_ORTA", "RSI_YÜKSEK"]),
        "funding": _bucket(v.get("funding_rate"), [-0.0001, 0.0001], ["FUNDING_NEG", "FUNDING_NÖTR", "FUNDING_POZ"]),
        "liquidity": _bucket(v.get("spread_pct"), [0.05, 0.15], ["SPREAD_DAR", "SPREAD_ORTA", "SPREAD_GENİŞ"]),
        "pattern_conf": _bucket(v.get("pattern_ci_low"), [0.0, 0.10], ["PATTERN_NEGATİF", "PATTERN_ZAYIF", "PATTERN_POZİTİF"]),
        "agent_disagreement": _bucket(v.get("n_dissent"), [1.0, 3.0], ["DISSENT_YOK", "DISSENT_AZ", "DISSENT_ÇOK"]),
        "consensus": _bucket(None if cons is None else abs(cons), [0.2, 0.5],
                             ["KONSENSÜS_ZAYIF", "KONSENSÜS_ORTA", "KONSENSÜS_GÜÇLÜ"]),
        "leverage": _bucket(v.get("leverage"), [1.5, 3.0], ["KALDIRAÇ_1x", "KALDIRAÇ_ORTA", "KALDIRAÇ_YÜKSEK"]),
        "side_x_regime": (f"{_side(row)}|{VOL_LABELS[int(reg)]}" if reg is not None and 0 <= int(reg) < len(VOL_LABELS) else None),
    }


# --------------------------------------------------------------------------- 1) tek işlem sınıflandırması
def classify_trade(row: dict) -> list[dict]:
    """Bir işlemin kayıp sınıfı adayları + kanıt. Kesin neden DEĞİL; gözlemlenebilir ilişki işaretleri."""
    v, out = _vals(row), (row.get("outcome") or {})
    r = float(out.get("r_multiple", 0) or 0)
    side = _side(row)
    long = side == "LONG"
    mae, mfe = abs(float(out.get("mae_pct", 0) or 0)), abs(float(out.get("mfe_pct", 0) or 0))
    exit_reason = str(out.get("exit_reason") or "").lower()
    sig: list[dict] = []

    def add(code: str, **evidence) -> None:
        sig.append({"code": code, "evidence": {k: (round(x, 6) if isinstance(x, float) else x)
                                               for k, x in evidence.items() if x is not None}})

    if not v:
        add("INSUFFICIENT_EVIDENCE", reason="karar anı snapshot'ı yok")
        return sig
    missing = list((row.get("snapshot") or {}).get("missing") or [])
    if len(missing) > 0.6 * (len(v) + len(missing)):
        add("INSUFFICIENT_EVIDENCE", missing_fields=len(missing))

    if r < 0 and mfe <= 0.25 * max(mae, 1e-9):
        add("WRONG_DIRECTION", mfe_pct=mfe, mae_pct=mae)          # hiç lehe hareket olmadı
    ext = v.get("px_vs_ma25_pct")
    if r < 0 and ext is not None and ((long and ext > 3.0) or (not long and ext < -3.0)):
        add("LATE_ENTRY", px_vs_ma25_pct=ext)                     # MA25'ten uzakta, uzamış giriş
    reg = v.get("vol_regime_code")
    if r < 0 and reg is not None and reg >= 2:
        add("VOLATILITY_MISMATCH", vol_regime=VOL_LABELS[int(reg)], atr_pct=v.get("atr_pct"))
    cross = v.get("ma_cross_dir")
    if r < 0 and cross is not None and ((long and cross < 0) or (not long and cross > 0)):
        add("TREND_MISMATCH", ma_cross_dir=cross, px_vs_ma99_pct=v.get("px_vs_ma99_pct"))
    mom = v.get("momentum_dir")
    if r < 0 and mom is not None and ((long and mom <= 0) or (not long and mom >= 0)):
        add("WEAK_MOMENTUM", momentum_dir=mom, rsi_fast=v.get("rsi_fast"))
    spread, liq = v.get("spread_pct"), v.get("liquidity_ok")
    if r < 0 and ((spread is not None and spread > 0.15) or (liq is not None and liq < 0.5)):
        add("LOW_LIQUIDITY", spread_pct=spread, depth_ratio=v.get("depth_ratio"), liquidity_ok=liq)
    cost = v.get("expected_cost_pct")
    if r < 0 and cost is not None and cost > 0 and abs(r) <= 0.5:
        add("COST_DRAG", expected_cost_pct=cost, r_multiple=r)     # küçük kayıpta maliyet baskın olabilir
    fr = v.get("funding_rate")
    if r < 0 and fr is not None and ((long and fr > 0.0001) or (not long and fr < -0.0001)):
        add("FUNDING_DRAG", funding_rate=fr, funding_z=v.get("funding_z"))
    p_ci, p_n = v.get("pattern_ci_low"), v.get("pattern_n")
    if r < 0 and p_ci is not None and p_ci < 0 and (p_n or 0) >= 10:
        add("PATTERN_NEGATIVE", pattern_ci_low=p_ci, pattern_n=p_n, pattern_expectancy_r=v.get("pattern_expectancy_r"))
    dis, cons = v.get("n_dissent"), v.get("consensus_score")
    if r < 0 and ((dis is not None and dis >= 3) or (cons is not None and abs(cons) < 0.2)):
        add("AGENT_DISAGREEMENT", n_dissent=dis, consensus_score=cons)
    stop_pct = v.get("stop_dist_pct")
    if r < 0 and exit_reason.startswith("stop") and stop_pct is not None and mfe > stop_pct:
        add("STOP_TOO_CLOSE_ASSOCIATION", stop_dist_pct=stop_pct, mfe_pct=mfe)
    if exit_reason in ("horizon", "time", "time_exit") and abs(r) < 0.25:
        add("TIME_EXIT_NO_EDGE", r_multiple=r, bars_held=out.get("bars_held"))
    return sig


def trade_attribution(rows: list[dict], *, min_bucket: int = MIN_BUCKET) -> list[dict]:
    """Her KAYIP işlem için yapılandırılmış kayıt: koşullar, koşullu istatistik, baseline farkı."""
    baseline = [float((r.get("outcome") or {}).get("r_multiple", 0) or 0)
                for r in rows if (r.get("outcome") or {}).get("r_multiple") is not None]
    base_m = (sum(baseline) / len(baseline)) if baseline else 0.0
    # kesit -> etiket -> R listesi (koşullu istatistik için tek geçiş)
    idx: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        rr = (r.get("outcome") or {}).get("r_multiple")
        if rr is None:
            continue
        for cut, lab in _cuts_for(r).items():
            if lab:
                idx.setdefault((cut, lab), []).append(float(rr))
    out: list[dict] = []
    for r in rows:
        rr = (r.get("outcome") or {}).get("r_multiple")
        if rr is None or float(rr) >= 0:
            continue
        o, snap = (r.get("outcome") or {}), (r.get("snapshot") or {})
        conds = []
        for cut, lab in _cuts_for(r).items():
            if not lab:
                continue
            st = _stats(idx.get((cut, lab), []), min_bucket=min_bucket)
            if st["expectancy_r"] is None:
                continue
            conds.append({"cut": cut, "label": lab, **st,
                          "delta_vs_baseline_r": round(st["expectancy_r"] - base_m, 4)})
        adverse = sorted([c for c in conds if c["delta_vs_baseline_r"] < 0 and c["sufficient"]],
                         key=lambda c: c["delta_vs_baseline_r"])[:5]
        classes = classify_trade(r)
        out.append({
            "trade_id": r.get("trade_id"), "symbol": str(o.get("symbol") or snap.get("symbol") or "?"),
            "side": _side(r), "setup": str(r.get("setup_type") or "-"), "regime": str(r.get("regime") or "-"),
            "net_r": round(float(rr), 4), "mae_pct": o.get("mae_pct"), "mfe_pct": o.get("mfe_pct"),
            "exit_reason": o.get("exit_reason"), "snapshot_hash": snap.get("snapshot_hash"),
            "loss_classes": [c["code"] for c in classes], "loss_evidence": classes,
            "strongest_adverse_conditions": adverse,
            "confidence": ("YETERSİZ" if not adverse else ("ORTA" if len(adverse) < 3 else "GÜÇLÜ")),
            "association_not_causation": True})
    return out


# --------------------------------------------------------------------------- 3) toplu rapor
def attribution_report(rows: list[dict], *, min_bucket: int = MIN_BUCKET) -> dict:
    """Kesitler + kayıp sınıfı dağılımı + politika üretimine girdi olan `findings`."""
    scored = [r for r in rows if (r.get("outcome") or {}).get("r_multiple") is not None]
    all_r = [float((r.get("outcome") or {}).get("r_multiple")) for r in scored]
    base = _stats(all_r, min_bucket=min_bucket)
    base_m = base["expectancy_r"] or 0.0
    cuts: dict[str, dict[str, list[float]]] = {}
    for r in scored:
        rr = float((r.get("outcome") or {}).get("r_multiple"))
        for cut, lab in _cuts_for(r).items():
            if lab:
                cuts.setdefault(cut, {}).setdefault(lab, []).append(rr)
    report = {cut: {lab: _stats(v, min_bucket=min_bucket) for lab, v in sorted(g.items())}
              for cut, g in sorted(cuts.items())}

    findings: list[dict] = []
    for cut, groups in report.items():
        for lab, st in groups.items():
            if not st["sufficient"] or st["expectancy_r"] is None:
                continue
            delta = round(st["expectancy_r"] - base_m, 4)
            direction = ("NEGATIF" if (st["ci95_low"] is not None and st["ci95_low"] < 0 and st["expectancy_r"] < 0)
                         else ("POZITIF" if (st["ci95_low"] is not None and st["ci95_low"] > 0) else "BELIRSIZ"))
            findings.append({"cut": cut, "label": lab, "direction": direction, **st,
                             "delta_vs_baseline_r": delta,
                             "text": (f"{cut}={lab}: beklenti {st['expectancy_r']:+.3f}R "
                                      f"(n={st['n']}, CI95 alt {st['ci95_low']:+.3f}, baseline farkı {delta:+.3f})")})
    findings.sort(key=lambda f: (f["delta_vs_baseline_r"], f["cut"], f["label"]))

    cls_counts: dict[str, int] = {}
    trades = trade_attribution(rows, min_bucket=min_bucket)
    for t in trades:
        for c in t["loss_classes"]:
            cls_counts[c] = cls_counts.get(c, 0) + 1
    snaps = [r.get("snapshot") or {} for r in rows if r.get("snapshot")]
    missing_rate = {}
    if snaps:
        for name in sorted({m for s in snaps for m in (s.get("missing") or [])}):
            missing_rate[name] = round(sum(1 for s in snaps if name in (s.get("missing") or [])) / len(snaps), 3)
    return {"schema": "loss_attribution_v2", "n_rows": len(rows), "n_scored": len(scored),
            "min_bucket": min_bucket, "baseline": base, "cuts": report,
            "findings": findings,
            "negative_findings": [f for f in findings if f["direction"] == "NEGATIF"],
            "loss_class_counts": dict(sorted(cls_counts.items())),
            "loss_classes_known": list(LOSS_CLASSES),
            "trades": trades,
            "insufficient_groups": {cut: [lab for lab, v in g.items() if not v["sufficient"]]
                                    for cut, g in report.items()},
            "missing_field_rate": missing_rate, "disclaimer": DISCLAIMER}


__all__ = ["DISCLAIMER", "LOSS_CLASSES", "MIN_BUCKET", "VOL_LABELS", "attribution_report",
           "classify_trade", "trade_attribution"]
