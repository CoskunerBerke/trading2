"""Outcome Learning Loop — geçmiş deneyimin SONRAKİ karara SINIRLI etkisi.

Temel ilke: "yetersiz örnek" öğrenmeyi ENGELLEMEZ; yalnız etkinin BÜYÜKLÜĞÜNÜ küçültür.
İlk kapanmış işlemden itibaren etki hesaplanır ve sıfırdan farklıdır, ama shrinkage nedeniyle
çok küçüktür.

Modlar:
* `OFF`            — hiçbir şey hesaplanmaz.
* `SHADOW`         — ayarlama hesaplanır ve KAYDEDİLİR; baseline karar BİREBİR korunur.
* `PAPER_BOUNDED`  — yalnız PAPER modunda, yalnız yumuşak skor bileşeninde, dar ve
                     yapılandırılabilir bir tavanla uygulanır.

Değişmezler (kod düzeyinde zorunlu):
* Etki asla `max_fraction`ı aşamaz (varsayılan %5).
* Kaldıraç, miktar, stop, TP, risk bütçesi veya emir parametresi DEĞİŞTİRİLEMEZ.
* Hard veto / risk reddi / kill switch GEÇİLEMEZ — bu modül yalnız bir sayı döndürür.
* LIVE/TESTNET'te uygulama YOKTUR (yalnız PAPER).
* Retrieval yalnız karar anından ÖNCE kapanmış işlemleri görür (no-lookahead).
* State eksik/bozuk/stale/NaN ise baseline birebir korunur (`applied=False`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from .retrieval import retrieve_similar

SCHEMA_VERSION = "learning_influence_v1"

OFF, SHADOW, PAPER_BOUNDED = "OFF", "SHADOW", "PAPER_BOUNDED"
MODES = (OFF, SHADOW, PAPER_BOUNDED)

#: Etkinin uygulanabileceği TEK mod ailesi (mode.json `mode` alanı).
APPLY_ONLY_IN_MODE = "PAPER"


@dataclass
class InfluenceConfig:
    """Öğrenme etkisi ayarları — güvenli varsayılanlar."""
    mode: str = SHADOW                 # üretimde varsayılan: yalnız gözlem
    prior_strength: float = 20.0       # shrinkage: w = n / (n + prior_strength); >= 20 zorunlu
    max_fraction: float = 0.05         # etkinin mutlak tavanı (baseline'ın oranı)
    top_k: int = 5
    min_similarity: float = -1.0       # kosinüs benzerliği tabanı (-1 = filtre yok)
    r_scale: float = 1.0               # sinyal normalizasyonu
    shadow_weight: float = 0.25        # gölge sonucun gerçek fill'e göre ağırlığı (<1 zorunlu)
    shadow_fidelity: float = 0.5       # yürütme sadakati çarpanı

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"influence mode gecersiz: {self.mode} (gecerli: {MODES})")
        if self.prior_strength < 20.0:
            raise ValueError("prior_strength >= 20 olmalı (etkinin küçük kalması için)")
        if not (0.0 < self.max_fraction <= 0.20):
            raise ValueError("max_fraction (0, 0.20] aralığında olmalı")
        if self.top_k < 1:
            raise ValueError("top_k >= 1 olmalı")
        if not (0.0 <= self.shadow_weight < 1.0):
            raise ValueError("shadow_weight [0, 1) olmalı — gölge gerçek fill'e eşit sayılamaz")
        if not (0.0 <= self.shadow_fidelity <= 1.0):
            raise ValueError("shadow_fidelity [0, 1] olmalı")

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "prior_strength": self.prior_strength,
                "max_fraction": self.max_fraction, "top_k": self.top_k,
                "min_similarity": self.min_similarity, "r_scale": self.r_scale,
                "shadow_weight": self.shadow_weight, "shadow_fidelity": self.shadow_fidelity}


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _closed_ms(row: dict[str, Any]) -> int | None:
    """Kapanış zamanı (ms). Bilinemiyorsa None — no-lookahead filtresinde DIŞARIDA bırakılır."""
    o = row.get("outcome") or {}
    for src in (o.get("closed_at"), row.get("closed_at")):
        if isinstance(src, str) and len(src) >= 10:
            try:
                from datetime import datetime
                return int(datetime.fromisoformat(src.replace("Z", "+00:00")).timestamp() * 1000)
            except (ValueError, TypeError):
                continue
        if isinstance(src, (int, float)) and math.isfinite(float(src)):
            return int(src)
    return None


class _AsOfMemory:
    """`retrieve_similar` için salt okunur kısıtlı görünüm: yalnız `as_of_ms`'ten ÖNCE kapananlar.

    NO-LOOKAHEAD: karar anından sonra kapanan hiçbir işlem retrieval'a giremez. Kapanış zamanı
    okunamayan satır da DIŞARIDA bırakılır (fail-closed).
    """

    def __init__(self, rows: Iterable[dict[str, Any]], as_of_ms: int | None):
        self._rows = []
        for r in rows:
            if as_of_ms is None:
                self._rows.append(r)
                continue
            t = _closed_ms(r)
            if t is not None and t <= int(as_of_ms):
                self._rows.append(r)

    def trades(self, *, closed_only: bool = True, limit: int | None = None) -> list[dict[str, Any]]:
        return list(self._rows[:limit] if limit else self._rows)


def retrieve_experience(memory: Any, query: dict[str, Any], *, as_of_ms: int | None,
                        cfg: InfluenceConfig | None = None) -> list[dict[str, Any]]:
    """Karar anından ÖNCE kapanmış en benzer deneyimler (top-K, deterministik).

    Mevcut `retrieval.retrieve_similar` yeniden kullanılır; tek fark zaman kesimidir.
    Hafıza okunamazsa boş liste döner — çağıran baseline'da kalır.
    """
    cfg = cfg or InfluenceConfig()
    try:
        rows = memory.trades(closed_only=True)
    except Exception:  # noqa: BLE001 — bozuk hafıza öğrenmeyi durdurur, worker'ı DEĞİL
        return []
    scoped = _AsOfMemory(rows, as_of_ms)
    if not scoped.trades():
        return []
    try:
        hits = retrieve_similar(scoped, query, k=cfg.top_k)
    except Exception:  # noqa: BLE001
        return []
    return [h for h in hits if (_f(h.get("similarity")) or -1.0) >= cfg.min_similarity]


def learning_adjustment(retrieved: list[dict[str, Any]], *, baseline: float | None,
                        cfg: InfluenceConfig | None = None,
                        posterior_mean: float | None = None,
                        posterior_n: float | None = None) -> dict[str, Any]:
    """Deneyimden SINIRLI bir ayarlama üretir. Saf fonksiyon; hiçbir şeyi uygulamaz.

    * `weight = n / (n + prior_strength)` → n=1'de küçük ama SIFIR DEĞİL.
    * `consistency` çelişkili sonuçlarda düşer (2 kazanç / 2 kayıp → ~0 → etki ~0).
    * `|fraction| <= max_fraction` her koşulda garanti.
    """
    cfg = cfg or InfluenceConfig()
    cfg.validate()
    base = _f(baseline)
    rs = [v for h in retrieved if (v := _f(h.get("r_multiple"))) is not None]
    n = len(rs)
    reasons: list[str] = []
    if n == 0:
        return {"schema_version": SCHEMA_VERSION, "n_experience": 0, "weight": 0.0,
                "consistency": None, "signal": None, "fraction": 0.0,
                "baseline": base, "learned": base, "delta": 0.0,
                "reasons": ["NO_PRIOR_EXPERIENCE"], "bounded_by": cfg.max_fraction}
    mean_r = sum(rs) / n
    signal = max(-1.0, min(1.0, mean_r / cfg.r_scale if cfg.r_scale else 0.0))
    agree = sum(1 for r in rs if (r > 0) == (mean_r > 0)) / n if mean_r != 0 else 0.5
    consistency = max(0.0, 2.0 * agree - 1.0)                # 0.5 uyum → 0 (çelişkili)
    n_for_weight = _f(posterior_n)
    n_for_weight = n_for_weight if (n_for_weight is not None and n_for_weight > n) else float(n)
    weight = n_for_weight / (n_for_weight + cfg.prior_strength)
    fraction = weight * consistency * signal * cfg.max_fraction
    fraction = max(-cfg.max_fraction, min(cfg.max_fraction, fraction))
    reasons.append("POSITIVE_PRIOR_EXPERIENCE" if signal > 0 else
                   ("NEGATIVE_PRIOR_EXPERIENCE" if signal < 0 else "NEUTRAL_PRIOR_EXPERIENCE"))
    if consistency <= 0.0:
        reasons.append("CONFLICTING_EXPERIENCE_CONFIDENCE_LOW")
    if n < 5:
        reasons.append("SMALL_SAMPLE_SHRUNK")
    pm = _f(posterior_mean)
    if pm is not None:
        reasons.append("POSTERIOR_AVAILABLE")
    learned = base * (1.0 + fraction) if base is not None else None
    return {"schema_version": SCHEMA_VERSION, "n_experience": n,
            "weight": round(weight, 6), "consistency": round(consistency, 6),
            "signal": round(signal, 6), "mean_r": round(mean_r, 6),
            "fraction": round(fraction, 8),
            "baseline": base, "learned": round(learned, 8) if learned is not None else None,
            "delta": round((learned - base), 8) if (learned is not None and base is not None) else 0.0,
            "posterior_mean": pm, "posterior_n": _f(posterior_n),
            "reasons": reasons, "bounded_by": cfg.max_fraction}


def apply_influence(adjustment: dict[str, Any], *, cfg: InfluenceConfig | None = None,
                    mode_value: str | None = None, live_order_path: bool = False) -> dict[str, Any]:
    """Ayarlamanın UYGULANIP uygulanmayacağına karar verir ve etkin değeri döndürür.

    `applied=True` yalnızca: cfg.mode == PAPER_BOUNDED **ve** çalışma modu PAPER **ve** LIVE emir
    yolu kapalı **ve** baseline/learned sonlu. Diğer her durumda baseline BİREBİR korunur.
    """
    cfg = cfg or InfluenceConfig()
    cfg.validate()
    base = _f(adjustment.get("baseline"))
    learned = _f(adjustment.get("learned"))
    blockers: list[str] = []
    if cfg.mode != PAPER_BOUNDED:
        blockers.append(f"MODE_{cfg.mode}")
    if str(mode_value or "").upper() != APPLY_ONLY_IN_MODE:
        blockers.append(f"RUNTIME_MODE_{str(mode_value or 'UNKNOWN').upper()}")
    if live_order_path:
        blockers.append("LIVE_ORDER_PATH_ENABLED")
    if base is None or learned is None:
        blockers.append("NON_FINITE_STATE")
    applied = not blockers
    effective = learned if applied else base
    # SERT TAVAN: uygulanan değer baseline'dan `max_fraction`dan fazla sapamaz.
    if applied and base:
        lo, hi = base * (1.0 - cfg.max_fraction), base * (1.0 + cfg.max_fraction)
        effective = max(min(effective, max(lo, hi)), min(lo, hi))
    return {"schema_version": SCHEMA_VERSION, "mode": cfg.mode, "applied": applied,
            "blockers": blockers, "baseline": base, "learned": learned,
            "effective": effective if effective is not None else base,
            "max_fraction": cfg.max_fraction,
            "note": "LEARNING CANNOT OVERRIDE RISK GATES"}


# ---------------------------------------------------------------- çift sayım koruması

def weighted_adjustment(experiences: list[Any], *, baseline: float | None,
                        cfg: InfluenceConfig | None = None,
                        prior_leaf_n: float | None = None) -> dict[str, Any]:
    """Ağırlıklı ayarlama — kaynak ağırlığı + ÇİFT SAYIM koruması.

    **Residual yöntemi (seçilen ve gerekçelendirilen çözüm):** hiyerarşik prior aynı kapanışlardan
    zaten `w_prior = n_leaf / (n_leaf + prior_strength)` kadar kanıt çekmiştir. Bu yüzden similarity
    kanalı, prior'da TEMSİL EDİLEN deneyimlere yalnız KALAN payı (`1 - w_prior`) uygular. Böylece
    tek bir `outcome_id` toplamda birden fazla TAM ağırlık alamaz.

    `outcome_id` tekrarları (aynı sonucun iki kez verilmesi, gerçek+gölge kopyası) tekilleştirilir.
    """
    cfg = cfg or InfluenceConfig()
    cfg.validate()
    base = _f(baseline)
    w_prior = 0.0
    if prior_leaf_n is not None:
        n_leaf = max(0.0, _f(prior_leaf_n) or 0.0)
        w_prior = n_leaf / (n_leaf + cfg.prior_strength) if (n_leaf + cfg.prior_strength) else 0.0
    residual = max(0.0, 1.0 - w_prior)

    seen: set[str] = set()
    items: list[tuple[float, float, str]] = []          # (weight, r, source)
    dropped_dupes = 0
    for e in experiences or []:
        get = (lambda k: e.get(k)) if isinstance(e, dict) else (lambda k: getattr(e, k, None))
        oid = str(get("outcome_id") or "")
        r = _f(get("r_multiple"))
        if r is None:
            continue
        if oid and oid in seen:
            dropped_dupes += 1
            continue                                    # AYNI outcome ikinci kez sayılmaz
        if oid:
            seen.add(oid)
        w = _f(get("weight"))
        w = 1.0 if w is None else max(0.0, w)
        # prior'da temsil edilen deneyim yalnız RESIDUAL payı kadar katkı verir
        w_eff = w * residual
        items.append((w_eff, r, str(get("source") or "REAL_PAPER")))

    total_w = sum(w for w, _, _ in items)
    if not items or total_w <= 0.0:
        return {"schema_version": SCHEMA_VERSION, "n_experience": len(items),
                "effective_n": 0.0, "weight": 0.0, "consistency": None, "signal": None,
                "fraction": 0.0, "baseline": base, "learned": base, "delta": 0.0,
                "prior_weight": round(w_prior, 6), "residual_share": round(residual, 6),
                "dropped_duplicates": dropped_dupes,
                "reasons": ["NO_USABLE_EXPERIENCE"], "bounded_by": cfg.max_fraction,
                "counted_outcome_ids": sorted(seen)}

    mean_r = sum(w * r for w, r, _ in items) / total_w
    signal = max(-1.0, min(1.0, mean_r / cfg.r_scale if cfg.r_scale else 0.0))
    agree_w = sum(w for w, r, _ in items if (r > 0) == (mean_r > 0)) / total_w if mean_r != 0 else 0.5
    consistency = max(0.0, 2.0 * agree_w - 1.0)
    weight = total_w / (total_w + cfg.prior_strength)
    fraction = weight * consistency * signal * cfg.max_fraction
    fraction = max(-cfg.max_fraction, min(cfg.max_fraction, fraction))
    learned = base * (1.0 + fraction) if base is not None else None
    reasons = ["POSITIVE_PRIOR_EXPERIENCE" if signal > 0 else
               ("NEGATIVE_PRIOR_EXPERIENCE" if signal < 0 else "NEUTRAL_PRIOR_EXPERIENCE")]
    if consistency <= 0.0:
        reasons.append("CONFLICTING_EXPERIENCE_CONFIDENCE_LOW")
    if total_w < 5:
        reasons.append("SMALL_SAMPLE_SHRUNK")
    if w_prior > 0:
        reasons.append("RESIDUAL_ONLY_PRIOR_ALREADY_COUNTED")
    if dropped_dupes:
        reasons.append(f"DEDUPED_{dropped_dupes}")
    if any(s == "SHADOW" for _, _, s in items):
        reasons.append("INCLUDES_SHADOW_EVIDENCE")
    return {"schema_version": SCHEMA_VERSION, "n_experience": len(items),
            "effective_n": round(total_w, 6), "weight": round(weight, 6),
            "consistency": round(consistency, 6), "signal": round(signal, 6),
            "mean_r": round(mean_r, 6), "fraction": round(fraction, 8),
            "baseline": base, "learned": round(learned, 8) if learned is not None else None,
            "delta": round(learned - base, 8) if (learned is not None and base is not None) else 0.0,
            "prior_weight": round(w_prior, 6), "residual_share": round(residual, 6),
            "dropped_duplicates": dropped_dupes,
            "reasons": reasons, "bounded_by": cfg.max_fraction,
            "counted_outcome_ids": sorted(seen)}


def combine_components(*, raw_model_p: float | None, hierarchical_p: float | None,
                       adjustment: dict[str, Any] | None,
                       cfg: InfluenceConfig | None = None) -> dict[str, Any]:
    """Bileşen katkılarını AYRI raporlar ve nihai sınırlı sonucu verir.

    `raw model` → `hierarchical prior` → `similarity residual` → `final bounded`.
    Nihai sonuç baseline'dan `max_fraction`dan fazla sapamaz.
    """
    cfg = cfg or InfluenceConfig()
    cfg.validate()
    raw = _f(raw_model_p)
    hier = _f(hierarchical_p)
    base = hier if hier is not None else raw
    adj = adjustment or {}
    frac = _f(adj.get("fraction")) or 0.0
    final = base * (1.0 + frac) if base is not None else None
    if final is not None and base:
        lo, hi = base * (1.0 - cfg.max_fraction), base * (1.0 + cfg.max_fraction)
        final = max(min(final, max(lo, hi)), min(lo, hi))
    return {"schema_version": SCHEMA_VERSION,
            "raw_model": raw,
            "hierarchical_prior": hier,
            "hierarchical_contribution": (round(hier - raw, 8)
                                          if (hier is not None and raw is not None) else None),
            "similarity_fraction": round(frac, 8),
            "similarity_contribution": (round(final - base, 8)
                                        if (final is not None and base is not None) else None),
            "final": round(final, 8) if final is not None else None,
            "counted_outcome_ids": adj.get("counted_outcome_ids") or [],
            "prior_weight": adj.get("prior_weight"),
            "residual_share": adj.get("residual_share"),
            "dropped_duplicates": adj.get("dropped_duplicates", 0),
            "bounded_by": cfg.max_fraction,
            "note": "LEARNING CANNOT OVERRIDE RISK GATES"}
