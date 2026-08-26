"""Edge ↔ execution ayrımı — "kaybettik" ile "giriş yanlıştı" AYNI ŞEY DEĞİLDİR.

Bir işlem şu nedenlerden herhangi biriyle zarara dönebilir ve her birinin politika sonucu farklıdır:

* girişten sonra neredeyse hiç lehe gitmedi              → sinyal kalitesi (edge) sorusu,
* önce güçlü lehe gitti sonra stop oldu                  → çıkış politikası (execution) sorusu,
* fiyat hareketi lehteydi ama komisyon/funding yedi      → maliyet filtresi sorusu,
* dolum kayması/gap bozdu                                → yürütme kalitesi sorusu,
* rejim girişten sonra değişti                           → rejim filtresi sorusu,
* plana tam uygun normal kayıp                           → HİÇBİR politika değişikliği gerekmez.

Bu modül YALNIZ GÖZLEM üretir. Tek işlemden nedensellik iddia ETMEZ (`causal_claim=False`) ve
tek işlem `OBSERVATION` seviyesini AŞAMAZ. Hipotezler yalnız "araştırılabilir soru"dur; politika
adayı olabilmeleri için `quant` tarafında walk-forward OOS kanıtı gerekir.

R normalizasyonu: MFE/MAE yüzde olarak ölçülür ama karşılaştırılabilir olması için stop mesafesine
bölünür (`stop_distance_pct`). Stop mesafesi bilinmiyorsa değer `None` kalır ve
`DATA_INSUFFICIENT` işaretlenir — EKSİK VERİ İYİMSER SIFIRA DÖNMEZ.

Capture ratio (kısmi kapanış dahil): `realized_r / mfe_r`. `realized_r` kısmi çıkışların
ağırlıklı net sonucudur (defterden gelir), `mfe_r` ise TAM pozisyonun görebildiği en iyi R'dir.
Yani TP1'de yarı kapatılıp kalan başa-baş kapanan bir işlemde capture ratio "mevcut en iyi
hareketin ne kadarını bankaya yazdık" sorusunu ölçer, "TP1 doğru muydu" sorusunu DEĞİL.
`mfe_r <= 0` ise oran tanımsızdır (`None`), sıfır değildir.
"""
from __future__ import annotations

import math
from typing import Any

CLASSIFICATION_VERSION = "edge_execution_v1"

# ------------------------------------------------------------------ gözlem kodları
LOW_MFE_STOP = "LOW_MFE_STOP"
HIGH_MFE_REVERSAL = "HIGH_MFE_REVERSAL"
TARGET_CAPTURED = "TARGET_CAPTURED"
PARTIAL_PROFIT_THEN_BE = "PARTIAL_PROFIT_THEN_BE"
COST_DOMINATED = "COST_DOMINATED"
SLIPPAGE_DOMINATED = "SLIPPAGE_DOMINATED"
GAP_AFFECTED = "GAP_AFFECTED"
NORMAL_PLANNED_LOSS = "NORMAL_PLANNED_LOSS"
REGIME_TRANSITION_OBSERVED = "REGIME_TRANSITION_OBSERVED"
CORRELATED_MOVE_OBSERVED = "CORRELATED_MOVE_OBSERVED"
DATA_INSUFFICIENT = "DATA_INSUFFICIENT"

OBSERVATION_CODES = (LOW_MFE_STOP, HIGH_MFE_REVERSAL, TARGET_CAPTURED, PARTIAL_PROFIT_THEN_BE,
                     COST_DOMINATED, SLIPPAGE_DOMINATED, GAP_AFFECTED, NORMAL_PLANNED_LOSS,
                     REGIME_TRANSITION_OBSERVED, CORRELATED_MOVE_OBSERVED, DATA_INSUFFICIENT)

# ------------------------------------------------------------------ hipotez kodları
ENTRY_QUALITY_CANDIDATE = "ENTRY_QUALITY_CANDIDATE"
EXIT_POLICY_CANDIDATE = "EXIT_POLICY_CANDIDATE"
COST_FILTER_CANDIDATE = "COST_FILTER_CANDIDATE"
REGIME_FILTER_CANDIDATE = "REGIME_FILTER_CANDIDATE"
THEME_RISK_CANDIDATE = "THEME_RISK_CANDIDATE"
NO_POLICY_CHANGE = "NO_POLICY_CHANGE"

HYPOTHESIS_CODES = (ENTRY_QUALITY_CANDIDATE, EXIT_POLICY_CANDIDATE, COST_FILTER_CANDIDATE,
                    REGIME_FILTER_CANDIDATE, THEME_RISK_CANDIDATE, NO_POLICY_CHANGE)

# ------------------------------------------------------------------ kanıt seviyeleri
OBSERVATION = "OBSERVATION"
RESEARCH_HYPOTHESIS = "RESEARCH_HYPOTHESIS"
VALIDATED_POLICY_CANDIDATE = "VALIDATED_POLICY_CANDIDATE"
APPLIED_BOUNDED = "APPLIED_BOUNDED"
REJECTED = "REJECTED"
RETIRED = "RETIRED"

EVIDENCE_LEVELS = (OBSERVATION, RESEARCH_HYPOTHESIS, VALIDATED_POLICY_CANDIDATE, APPLIED_BOUNDED)
TERMINAL_LEVELS = (REJECTED, RETIRED)

#: Bir gözlemin hipotez seviyesine çıkması için gereken asgari benzer örnek.
MIN_SIMILAR_FOR_HYPOTHESIS = 8

# ------------------------------------------------------------------ eşikler (açıklanmış, sihirli sayı değil)
#: MFE bu R'nin altındaysa "fiyat lehimize neredeyse hiç gitmedi" — sinyal sorusu.
LOW_MFE_R = 0.35
#: MFE bu R'yi aşıp yine de stop olduysa "kâr masada kaldı" — çıkış sorusu.
HIGH_MFE_R = 1.0
#: Maliyet sürüklemesi toplamı bu R'yi aşarsa maliyet baskındır.
COST_DOMINATED_R = 0.30
#: Yalnız kayma/spread bu R'yi aşarsa yürütme baskındır.
SLIPPAGE_DOMINATED_R = 0.15
#: Gerçekleşen sonuç plana ne kadar yakınsa "normal kayıp" sayılır (−1R çevresi).
PLANNED_LOSS_BAND = (-1.35, -0.65)
#: Capture ratio ancak ANLAMLI bir lehte hareket varsa okunabilir. Bunun altında oran hesaplanır
#: ama GÜRÜLTÜLÜ işaretlenir: 0.05R'lik bir MFE'ye bölmek −20 gibi anlamsız katsayılar üretir.
MIN_MFE_R_FOR_CAPTURE = 0.25

DATA_OK, DATA_PARTIAL, DATA_MISSING = "OK", "PARTIAL", "MISSING"


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def stop_distance_pct(rec: dict[str, Any]) -> float | None:
    """|giriş − ilk stop| / giriş × 100. Doğrudan alan varsa o kullanılır.

    Bulunamazsa `None` — çağıran bunu 0 SAYMAZ, `DATA_INSUFFICIENT` işaretler.
    """
    direct = _f(rec.get("stop_distance_pct"))
    if direct is not None and direct > 0:
        return direct
    f = rec.get("features") or {}
    entry = _f(rec.get("entry_price")) or _f(rec.get("entry")) or _f(f.get("entry_price"))
    stop = (_f(rec.get("initial_stop")) or _f(rec.get("stop_price"))
            or _f(rec.get("stop")) or _f(f.get("initial_stop")))
    if entry and stop and entry > 0:
        d = abs(entry - stop) / entry * 100.0
        return d if d > 0 else None
    return None


def excursions_r(rec: dict[str, Any]) -> dict[str, Any]:
    """MFE/MAE'yi R cinsine çevirir. Ölçülemeyen değer `None` kalır (iyimser sıfır YOK)."""
    dist = stop_distance_pct(rec)
    mfe_pct, mae_pct = _f(rec.get("mfe_pct")), _f(rec.get("mae_pct"))
    if dist is None or dist <= 0:
        return {"stop_distance_pct": None, "mfe_r": None, "mae_r": None,
                "mfe_pct": mfe_pct, "mae_pct": mae_pct,
                "data_quality": DATA_MISSING if (mfe_pct is None and mae_pct is None) else DATA_PARTIAL}
    quality = DATA_OK if (mfe_pct is not None and mae_pct is not None) else (
        DATA_PARTIAL if (mfe_pct is not None or mae_pct is not None) else DATA_MISSING)
    return {"stop_distance_pct": round(dist, 6),
            "mfe_r": (round(mfe_pct / dist, 4) if mfe_pct is not None else None),
            # MAE aleyhte hareket: işaretten bağımsız BÜYÜKLÜK, negatif R olarak yazılır.
            "mae_r": (round(-abs(mae_pct) / dist, 4) if mae_pct is not None else None),
            "mfe_pct": mfe_pct, "mae_pct": mae_pct, "data_quality": quality}


def capture_ratio(realized_r: float | None, mfe_r: float | None) -> float | None:
    """Gerçekleşen R / erişilebilir en iyi R. `mfe_r <= 0` ise TANIMSIZ (None) — sıfır değil.

    Kısmi kapanışta `realized_r` ağırlıklı net sonuçtur; oran "mevcut hareketin ne kadarı
    bankaya yazıldı" sorusunu ölçer.
    """
    if realized_r is None or mfe_r is None or mfe_r <= 0:
        return None
    return round(realized_r / mfe_r, 4)


def _capture_state(cap: float | None, mfe_r: float | None) -> str:
    """Oranın OKUNABİLİR olup olmadığını söyler; değer yine de raporlanır."""
    if cap is None:
        return "UNDEFINED_NO_FAVORABLE_EXCURSION"
    if mfe_r is not None and mfe_r < MIN_MFE_R_FOR_CAPTURE:
        return "NOISY_NEGLIGIBLE_EXCURSION"
    return "OK"


def _drags(rec: dict[str, Any], labels: dict[str, Any] | None) -> dict[str, float | None]:
    lab = labels or {}
    return {"fee_drag_r": _f(lab.get("fee_drag_r")) if lab.get("fee_drag_r") is not None else _f(rec.get("fee_drag_r")),
            "funding_drag_r": _f(lab.get("funding_drag_r")) if lab.get("funding_drag_r") is not None else _f(rec.get("funding_drag_r")),
            "slippage_drag_r": _f(lab.get("slippage_drag_r")) if lab.get("slippage_drag_r") is not None else _f(rec.get("slippage_drag_r"))}


def _partial_taken(rec: dict[str, Any]) -> bool:
    if bool(rec.get("tp1_done")):
        return True
    fills = rec.get("partial_fills") or rec.get("partials")
    return bool(isinstance(fills, (list, tuple)) and len(fills) > 0)


def classify_edge_execution(rec: dict[str, Any], *, labels: dict[str, Any] | None = None,
                            regime_at_entry: Any = None, regime_at_exit: Any = None,
                            n_similar: int = 0,
                            correlated_cluster_size: int = 0) -> dict[str, Any]:
    """Tek işlemin GÖZLEM sınıflandırması + araştırılabilir hipotezler.

    Nedensellik iddia edilmez; `evidence_level` tek işlemde daima `OBSERVATION`tır.
    """
    ex = excursions_r(rec)
    realized_r = _f(rec.get("r_multiple"))
    if realized_r is None and labels:
        realized_r = _f(labels.get("r_multiple"))
    drags = _drags(rec, labels)
    reason = str(rec.get("exit_reason", "") or "").lower()
    exit_quality = str((labels or {}).get("exit_quality") or "")
    bars = rec.get("bars_held")
    bars = int(_f(bars) or 0) if bars is not None else None

    obs: list[str] = []
    hyp: list[str] = []

    def add_obs(code: str) -> None:
        if code not in obs:
            obs.append(code)

    def add_hyp(code: str) -> None:
        if code not in hyp:
            hyp.append(code)

    stopped = ("stop" in reason) or exit_quality in ("STOP", "LIQUIDATION")
    hit_target = ("hedef" in reason) or ("target" in reason) or ("tp" in reason) or exit_quality == "TARGET"
    gapped = ("gap" in reason) or exit_quality == "GAP_THROUGH"

    if ex["data_quality"] == DATA_MISSING or ex["mfe_r"] is None:
        add_obs(DATA_INSUFFICIENT)
    else:
        mfe_r = ex["mfe_r"]
        if stopped and mfe_r < LOW_MFE_R:
            add_obs(LOW_MFE_STOP)
            add_hyp(ENTRY_QUALITY_CANDIDATE)
        elif stopped and mfe_r >= HIGH_MFE_R:
            add_obs(HIGH_MFE_REVERSAL)
            add_hyp(EXIT_POLICY_CANDIDATE)
    if hit_target:
        add_obs(TARGET_CAPTURED)
    if _partial_taken(rec) and (exit_quality == "TP1_THEN_BE" or "başa" in reason or "breakeven" in reason):
        add_obs(PARTIAL_PROFIT_THEN_BE)
    if gapped:
        add_obs(GAP_AFFECTED)

    fee, fund, slip = drags["fee_drag_r"], drags["funding_drag_r"], drags["slippage_drag_r"]
    cost_total = sum(v for v in (fee, fund, slip) if v is not None and v > 0)
    if any(v is not None for v in (fee, fund, slip)) and cost_total >= COST_DOMINATED_R:
        add_obs(COST_DOMINATED)
        add_hyp(COST_FILTER_CANDIDATE)
    if slip is not None and slip >= SLIPPAGE_DOMINATED_R:
        add_obs(SLIPPAGE_DOMINATED)
        add_hyp(COST_FILTER_CANDIDATE)

    r_e, r_x = (str(regime_at_entry) if regime_at_entry else None), (str(regime_at_exit) if regime_at_exit else None)
    if r_e and r_x and r_e != r_x:
        add_obs(REGIME_TRANSITION_OBSERVED)
        add_hyp(REGIME_FILTER_CANDIDATE)
    if int(correlated_cluster_size or 0) >= 2:
        add_obs(CORRELATED_MOVE_OBSERVED)
        add_hyp(THEME_RISK_CANDIDATE)

    # Planlı normal kayıp: stop oldu, sonuç −1R bandında, maliyet baskın değil, MFE düşük değil.
    if (stopped and realized_r is not None
            and PLANNED_LOSS_BAND[0] <= realized_r <= PLANNED_LOSS_BAND[1]
            and COST_DOMINATED not in obs and SLIPPAGE_DOMINATED not in obs
            and LOW_MFE_STOP not in obs and HIGH_MFE_REVERSAL not in obs):
        add_obs(NORMAL_PLANNED_LOSS)
    if not hyp:
        hyp.append(NO_POLICY_CHANGE)

    cap = capture_ratio(realized_r, ex["mfe_r"])
    return {"classification_version": CLASSIFICATION_VERSION,
            "trade_id": str(rec.get("id") or rec.get("trade_id") or ""),
            "symbol": rec.get("symbol"),
            "observation_codes": obs, "hypothesis_codes": hyp,
            "evidence_level": OBSERVATION,          # tek işlem BUNU AŞAMAZ
            "n_similar": int(n_similar or 0),
            "mfe_r": ex["mfe_r"], "mae_r": ex["mae_r"],
            "mfe_pct": ex["mfe_pct"], "mae_pct": ex["mae_pct"],
            "stop_distance_pct": ex["stop_distance_pct"],
            "realized_r": realized_r, "capture_ratio": cap,
            "capture_ratio_state": _capture_state(cap, ex["mfe_r"]),
            "bars_held": bars, "exit_reason": rec.get("exit_reason"),
            "fee_drag_r": fee, "funding_drag_r": fund, "slippage_drag_r": slip,
            "cost_drag_total_r": (round(cost_total, 4) if any(v is not None for v in (fee, fund, slip)) else None),
            "regime_at_entry": r_e, "regime_at_exit": r_x,
            "data_quality": ex["data_quality"],
            "causal_claim": False,
            "note_tr": "Bu sınıflandırma GÖZLEMDİR; tek işlem politika değiştiremez."}


def promote_evidence_level(current: str, *, n_supporting: int, n_conflicting: int = 0,
                           oos_validated: bool = False, applied_bounded: bool = False) -> str:
    """Kanıt seviyesi geçişi — fail-closed. Atlamalı terfi YOK.

    `OBSERVATION → RESEARCH_HYPOTHESIS` yalnız yeterli benzer örnek varsa;
    `→ VALIDATED_POLICY_CANDIDATE` yalnız gerçek OOS doğrulaması geçtiyse;
    `→ APPLIED_BOUNDED` yalnız sınırlı uygulama açıkça onaylandıysa.
    """
    if current in TERMINAL_LEVELS:
        return current
    if current not in EVIDENCE_LEVELS:
        return OBSERVATION
    n_sup = int(n_supporting or 0)
    if n_sup <= 1:
        return OBSERVATION            # tek sonuç ASLA hipotezin ötesine geçemez
    level = current
    if level == OBSERVATION and n_sup >= MIN_SIMILAR_FOR_HYPOTHESIS and n_sup > int(n_conflicting or 0):
        level = RESEARCH_HYPOTHESIS
    if level == RESEARCH_HYPOTHESIS and oos_validated:
        level = VALIDATED_POLICY_CANDIDATE
    if level == VALIDATED_POLICY_CANDIDATE and applied_bounded:
        level = APPLIED_BOUNDED
    return level
