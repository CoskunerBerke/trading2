"""Seçicilik challenger'ları — YENİ İNDİKATÖR EKLEMEDEN kötü girişleri elemeyi ölçer.

Amaç "daha az işlem" DEĞİLDİR. İşlem sayısını düşürmek tek başına başarı sayılmaz: çok az
işlemle üretilen yüksek win-rate istatistiksel gürültüdür ve terfi edemez. Ölçülen şey net
beklenti, payoff ve risk-ayarlı sonuçtur; asgari işlem sıklığı/kapsam kapısı zorunludur.

Eşik disiplini (sızıntıya kapalı):

    train      → eşik BURADA fit edilir (tek yer)
    validation → adaylardan BİRİ seçilir
    test       → yalnız SEÇİLEN aday ölçülür (seçimi DEĞİŞTİREMEZ)
    holdout    → hiçbir seçime girmez; en sonda tek kez raporlanır

`select_candidate()` test/holdout satırlarını GÖRMEZ — imzası bunu zorlar. `evaluate_selected()`
seçilmiş adayı alır ve seçim yapamaz. Bu ayrım testlerle sabitlenir.

Challenger'lar (en fazla dört, önceden tanımlı — grid araması YOK):

* `CHAMPION`                — mevcut seçim (hepsini al),
* `CALIBRATED_NET_EDGE`     — kalibre p_win ve maliyetten türeyen net beklenti eşiği,
* `WARNING_DENSITY`         — girişteki uyarı yoğunluğu eşiği,
* `NEGATIVE_SIMILAR`        — benzer geçmiş deneyim beklentisi negatifse çekimser,
* `QUALITY_PERCENTILE`      — train'de belirlenen kalite yüzdelik eşiği.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

SCHEMA_VERSION = "quant_selectivity_v1"

CHAMPION = "CHAMPION_TAKE_ALL"
CALIBRATED_NET_EDGE = "CALIBRATED_NET_EDGE"
WARNING_DENSITY = "WARNING_DENSITY"
NEGATIVE_SIMILAR = "NEGATIVE_SIMILAR"
QUALITY_PERCENTILE = "QUALITY_PERCENTILE"

MAX_CHALLENGERS = 4

#: Kapsam kapıları — bunlar geçilmeden hiçbir seçicilik adayı "daha iyi" ilan edilemez.
MIN_TRADES_ABS = 30
#: Champion'ın işlem sayısının en az bu kadarını korumalı (aşırı seçicilik = ölçülemez).
MIN_TRADE_FRACTION = 0.30
#: Kalan işlemlerin en az bu kadar farklı sembole yayılması gerekir.
MIN_SYMBOLS = 5


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _percentile(vals: Sequence[float], q: float) -> float | None:
    xs = sorted(v for v in vals if v is not None and math.isfinite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * max(0.0, min(1.0, q))
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return xs[lo] if lo == hi else xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


@dataclass(frozen=True)
class SelectivityRule:
    """Eşik + kabul yordamı. Eşik YALNIZ `fit_on_train` ile belirlenir."""
    name: str
    threshold: float | None = None
    fitted_on: str = "train"

    def accepts(self, row: dict[str, Any]) -> bool:
        if self.name == CHAMPION:
            return True
        if self.threshold is None:
            return True                       # eşik fit edilemedi → champion davranışı
        if self.name == CALIBRATED_NET_EDGE:
            v = net_edge_r(row)
            return v is not None and v >= self.threshold
        if self.name == WARNING_DENSITY:
            v = _f(row.get("n_warnings"))
            return v is None or v <= self.threshold
        if self.name == NEGATIVE_SIMILAR:
            v = _f(row.get("similar_expectancy_r"))
            return v is None or v >= self.threshold
        if self.name == QUALITY_PERCENTILE:
            v = quality_score(row)
            return v is not None and v >= self.threshold
        return True


def net_edge_r(row: dict[str, Any]) -> float | None:
    """Kalibre p_win + payoff varsayımından türeyen maliyet SONRASI beklenti (R).

    `p·payoff − (1−p)·1 − maliyet`. Payoff plandaki R/R'dir; maliyet gerçekleşen sürüklemedir.
    Eksik alan → `None` (iyimser sıfır YOK) ve kural o satırı değerlendiremez.
    """
    p = _f(row.get("p_win_calibrated"))
    if p is None:
        p = _f(row.get("p_win"))
    payoff = _f(row.get("rr"))
    if p is None or payoff is None:
        return None
    cost = sum(v for v in (_f(row.get("fee_drag_r")), _f(row.get("funding_drag_r")),
                           _f(row.get("slippage_drag_r"))) if v is not None)
    return round(p * payoff - (1 - p) * 1.0 - cost, 4)


def quality_score(row: dict[str, Any]) -> float | None:
    """Mevcut karar sinyallerinden türeyen kalite skoru — YENİ İNDİKATÖR EKLEMEZ.

    Yalnız zaten kayıtlı alanları birleştirir: kanaat, R/R, uyarı yoğunluğu, benzer deneyim.
    """
    conv, rr = _f(row.get("conviction")), _f(row.get("rr"))
    if conv is None and rr is None:
        return None
    warn = _f(row.get("n_warnings")) or 0.0
    sim = _f(row.get("similar_expectancy_r")) or 0.0
    return round((conv or 0.0) + 0.15 * (rr or 0.0) - 0.05 * warn + 0.25 * sim, 4)


def fit_on_train(train_rows: Iterable[dict[str, Any]], *,
                 quality_q: float = 0.4) -> list[SelectivityRule]:
    """Eşikleri YALNIZ train'den türetir. Validation/test/holdout BURAYA GİREMEZ."""
    rows = [r for r in train_rows if isinstance(r, dict)]
    warns = [v for v in (_f(r.get("n_warnings")) for r in rows) if v is not None]
    quals = [v for v in (quality_score(r) for r in rows) if v is not None]
    return [
        SelectivityRule(CHAMPION),
        # Maliyet sonrası beklenti sıfırın üstünde olmalı — eşik sabit ve açıklanmış.
        SelectivityRule(CALIBRATED_NET_EDGE, threshold=0.0),
        # Uyarı yoğunluğu: train medyanı (yarısını eler, keyfi sabit değil).
        SelectivityRule(WARNING_DENSITY, threshold=_percentile(warns, 0.5)),
        # Benzer deneyim beklentisi negatifse çekimser.
        SelectivityRule(NEGATIVE_SIMILAR, threshold=0.0),
        # Kalite yüzdeliği: train'in alt %40'ı elenir.
        SelectivityRule(QUALITY_PERCENTILE, threshold=_percentile(quals, quality_q)),
    ][:MAX_CHALLENGERS + 1]


def _apply(rule: SelectivityRule, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if rule.accepts(r)]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from .attribution import group_metrics
    return group_metrics(rows, min_sample=1)


def coverage_gate(taken: list[dict[str, Any]], champion_n: int) -> dict[str, Any]:
    """İşlem sıklığı/kapsam kapısı — az işlemle sahte yüksek win-rate terfi edemez."""
    n = len(taken)
    syms = {str(r.get("symbol")) for r in taken if r.get("symbol")}
    frac = (n / champion_n) if champion_n else 0.0
    checks = {
        "MIN_TRADES_ABS": (n >= MIN_TRADES_ABS, f"n={n} (eşik {MIN_TRADES_ABS})"),
        "MIN_TRADE_FRACTION": (frac >= MIN_TRADE_FRACTION,
                               f"kalan oran={round(frac, 4)} (eşik {MIN_TRADE_FRACTION})"),
        "MIN_SYMBOLS": (len(syms) >= MIN_SYMBOLS, f"sembol={len(syms)} (eşik {MIN_SYMBOLS})"),
    }
    return {"passed": all(v[0] for v in checks.values()),
            "checks": {k: {"passed": v[0], "detail": v[1]} for k, v in checks.items()},
            "n_taken": n, "trade_fraction": round(frac, 4), "n_symbols": len(syms)}


def select_candidate(train_rows: Iterable[dict[str, Any]],
                     validation_rows: Iterable[dict[str, Any]], *,
                     quality_q: float = 0.4,
                     objective: Callable[[dict[str, Any]], float | None] | None = None) -> dict[str, Any]:
    """Eşik train'de fit edilir, aday validation'da seçilir. TEST/HOLDOUT GÖRÜLMEZ.

    İmzada test satırı YOKTUR — sızıntı yapısal olarak imkânsızdır.
    """
    tr = [r for r in train_rows if isinstance(r, dict)]
    va = [r for r in validation_rows if isinstance(r, dict)]
    rules = fit_on_train(tr, quality_q=quality_q)
    obj = objective or (lambda m: _f(m.get("expectancy_r")))
    champion_n = len(va)
    cands = []
    for rule in rules:
        taken = _apply(rule, va)
        gate = coverage_gate(taken, champion_n) if rule.name != CHAMPION else {
            "passed": True, "checks": {}, "n_taken": len(taken),
            "trade_fraction": 1.0, "n_symbols": len({str(r.get("symbol")) for r in taken if r.get("symbol")})}
        m = _metrics(taken) if taken else {}
        cands.append({"rule": rule.name, "threshold": rule.threshold, "fitted_on": "train",
                      "validation_metrics": m, "coverage_gate": gate,
                      "objective": obj(m) if m else None,
                      "eligible": bool(gate["passed"] and m)})
    eligible = [c for c in cands if c["eligible"] and c["objective"] is not None]
    champ = next((c for c in cands if c["rule"] == CHAMPION), None)
    best = max(eligible, key=lambda c: c["objective"]) if eligible else champ
    return {"schema_version": SCHEMA_VERSION,
            "selected": (best or {}).get("rule", CHAMPION),
            "selected_threshold": (best or {}).get("threshold"),
            "candidates": cands,
            "n_train": len(tr), "n_validation": len(va),
            "saw_test_rows": False, "saw_holdout_rows": False,
            "label": "SELECTION USED TRAIN+VALIDATION ONLY"}


def evaluate_selected(selection: dict[str, Any], test_rows: Iterable[dict[str, Any]], *,
                      holdout_rows: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Seçilmiş adayı test'te ölçer. SEÇİMİ DEĞİŞTİREMEZ — yalnız ölçüm döner.

    `holdout` varsa en sonda tek kez raporlanır ve seçime hiçbir biçimde girmez.
    """
    name = str(selection.get("selected") or CHAMPION)
    rule = SelectivityRule(name, threshold=selection.get("selected_threshold"))
    te = [r for r in test_rows if isinstance(r, dict)]
    champ_te = _metrics(te) if te else {}
    taken = _apply(rule, te)
    gate = coverage_gate(taken, len(te))
    out = {"schema_version": SCHEMA_VERSION, "evaluated_rule": name,
           "threshold": rule.threshold,
           "champion_test_metrics": champ_te,
           "selected_test_metrics": _metrics(taken) if taken else {},
           "coverage_gate": gate,
           "selection_unchanged": True,
           "selection_source": "train+validation",
           "label": "TEST MEASURED ONLY — selection was NOT re-run on test"}
    if holdout_rows is not None:
        ho = [r for r in holdout_rows if isinstance(r, dict)]
        out["holdout_metrics"] = _metrics(_apply(rule, ho)) if ho else {}
        out["holdout_champion_metrics"] = _metrics(ho) if ho else {}
        out["holdout_entered_selection"] = False
    return out
