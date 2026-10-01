"""Aile bazlı walk-forward ablation — RESEARCH-ONLY rapor, aktif kararı DEĞİŞTİRMEZ.

Her bilgi ailesinin artımlı OOS katkısını ölçer: aile feature'ları sıfırlanmış modelle tam
model, AYNI ileri-yürüyen bölmelerde karşılaştırılır (train-only fit; test yalnız değerlendirme).
Katkı = OOS log-loss farkı (pozitif → aile bilgi taşıyor). Gereksiz/yedek feature sadeleştirme
kararları bu rapora dayanır; otomatik aktivasyon/deaktivasyon YOKTUR.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from .calibration import log_loss
from .feature_registry import REGISTRY_VERSION, active_families, family_of
from .features import feature_names
from .model import LogisticModel

ABLATION_VERSION = "ablation_v1"


def _fit_eval(Xtr: np.ndarray, ytr: np.ndarray, Xte: np.ndarray, yte: np.ndarray,
              names: list[str], drop_idx: list[int]) -> float:
    """Verilen sütunlar SIFIRLANMIŞ modelle OOS log-loss. Deterministik (GD, sabit tur)."""
    Xtr2, Xte2 = Xtr.copy(), Xte.copy()
    if drop_idx:
        Xtr2[:, drop_idx] = 0.0
        Xte2[:, drop_idx] = 0.0
    m = LogisticModel(feature_names=list(names))
    m.fit(Xtr2, ytr)
    p = np.clip(m.predict_proba(Xte2), 1e-6, 1 - 1e-6)
    return float(log_loss(yte.tolist(), p.tolist()))


def ablation_report(X: np.ndarray, y: np.ndarray, *, names: list[str] | None = None,
                    n_folds: int = 3, min_train: int = 40) -> dict[str, Any]:
    """İleri-yürüyen aile ablation raporu.

    * Bölme İLERİ yürür: fold k, [0..t_k) train / [t_k..t_{k+1}) test (leakage yok).
    * Her ailede: tam model OOS log-loss − aile-sıfırlanmış OOS log-loss.
      `contribution_logloss > 0` → aile OOS bilgi taşıyor; ≤ 0 → yedek/gereksiz adayı.
    * Rapor RESEARCH-ONLY'dir; hiçbir ağırlık/config otomatik değişmez.
    """
    names = list(names or feature_names())
    n = int(len(y))
    fams = active_families()
    fam_idx: dict[str, list[int]] = {f: [] for f in fams}
    for i, nm in enumerate(names):
        f = family_of(nm)
        if f in fam_idx:
            fam_idx[f].append(i)
    if n < max(min_train + 10, 30):
        return {"schema_version": ABLATION_VERSION, "registry_version": REGISTRY_VERSION,
                "n_samples": n, "insufficient_sample": True, "families": {},
                "note": "örnek yetersiz — rapor üretilmedi (uydurma yok)"}

    bounds = np.linspace(min_train, n, n_folds + 1).astype(int)
    per_family: dict[str, list[float]] = {f: [] for f in fams}
    full_losses: list[float] = []
    for k in range(n_folds):
        tr_end, te_end = int(bounds[k]), int(bounds[k + 1])
        if te_end - tr_end < 5 or tr_end < min_train:
            continue
        Xtr, ytr = X[:tr_end], y[:tr_end]
        Xte, yte = X[tr_end:te_end], y[tr_end:te_end]
        if len(set(ytr.tolist())) < 2 or len(yte) == 0:
            continue
        full = _fit_eval(Xtr, ytr, Xte, yte, names, [])
        full_losses.append(full)
        for fam in fams:
            without = _fit_eval(Xtr, ytr, Xte, yte, names, fam_idx[fam])
            per_family[fam].append(without - full)     # >0 → aile çıkınca OOS kötüleşti
    out_f: dict[str, Any] = {}
    for fam in fams:
        vals = per_family[fam]
        out_f[fam] = {"n_folds": len(vals),
                      "contribution_logloss": round(float(np.mean(vals)), 6) if vals else None,
                      "per_fold": [round(v, 6) for v in vals],
                      "n_features": len(fam_idx[fam]),
                      "verdict": ("CARRIES_OOS_SIGNAL" if vals and float(np.mean(vals)) > 0
                                  else ("REDUNDANT_CANDIDATE" if vals else "UNMEASURED"))}
    return {"schema_version": ABLATION_VERSION, "registry_version": REGISTRY_VERSION,
            "n_samples": n, "n_folds_used": len(full_losses),
            "full_model_logloss": round(float(np.mean(full_losses)), 6) if full_losses else None,
            "insufficient_sample": not full_losses,
            "families": out_f,
            "note": "RESEARCH-ONLY: otomatik feature aktivasyonu/deaktivasyonu YOKTUR"}


__all__ = ["ABLATION_VERSION", "ablation_report"]
