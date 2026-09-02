"""Haftalık yapı ve yapısal R:R challenger'ları (`entry_challenger_v2`) — SHADOW, `applied=False`.

İki YENİ aile, mevcut beşin yanına eklenir; hiçbiri mevcut aileleri değiştirmez:

* **F — `WEEKLY_SWEEP_RECLAIM_V1`**: önceki tamamlanmış haftanın yüksek/düşüğüyle etkileşim.
* **G — `STRUCTURAL_RISK_REWARD_V1`**: karar anında savunulabilir bir yapısal plan var mıydı.

**Bu bir seçicilik filtresidir, sinyal üreteci DEĞİLDİR.** Yalnız baseline'ın ZATEN aday
gösterdiği işlemler üzerinde çalışır; baseline'ın reddettiği bir işlemi "keşfedemez".

**Mum bağlamı tek başına karar üretemez.** `candle_context` çıktısı yalnız güven düzeyini
değiştirir (`confidence_delta`); bir aileyi ALLOW'dan BLOCK'a ya da tersine ÇEVİREMEZ.

**Eşikler örnekleme uydurulmamıştır.** Birkaç yapılandırma varyantı AYNI ANDA gölgede ölçülür
(`CONFIG_VARIANTS`); "en iyi" olan sonuçlara bakılarak seçilmez, hepsi raporlanır.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any

from ..core import stable_id
from .candle_context import (BEARISH_ENGULFING_LIKE, BULLISH_ENGULFING_LIKE, CONFIRMED,
                             EVENING_STAR_LIKE, HAMMER_LIKE, INVERTED_HAMMER_LIKE,
                             MORNING_STAR_LIKE, THREE_BLACK_CROWS_LIKE,
                             THREE_WHITE_SOLDIERS_LIKE)
from .weekly_structure import (ACCEPTED_BREAKOUT, AMBIGUOUS, BREAKOUT_UNCONFIRMED,
                               DATA_UNAVAILABLE, DQ_OK, HIGH_SWEEP_RECLAIM, LOW_SWEEP_RECLAIM,
                               NO_INTERACTION, TOUCH_ONLY)

SCHEMA_VERSION = "entry_challenger_v2"

# --- kararlar ----------------------------------------------------------------------------
ALLOW = "ALLOW"
BLOCK = "BLOCK"
ABSTAIN = "ABSTAIN"
UNKNOWN = "UNKNOWN"
DECISIONS = (ALLOW, BLOCK, ABSTAIN, UNKNOWN)

# --- aileler -----------------------------------------------------------------------------
FAM_WEEKLY = "F_weekly_sweep_reclaim"
FAM_STRUCT = "G_structural_risk_reward"
FAMILIES_V2 = (FAM_WEEKLY, FAM_STRUCT)

# --- gerekçe kodları ---------------------------------------------------------------------
R_OK = "PASSES_FILTER"
R_NO_WEEKLY_DATA = "WEEKLY_STRUCTURE_UNAVAILABLE"
R_WEEK_PARTIAL = "WEEKLY_DATA_PARTIAL"
R_COUNTER_HIGH_SWEEP = "HIGH_SWEEP_RECLAIM_OPPOSES_LONG"
R_COUNTER_LOW_SWEEP = "LOW_SWEEP_RECLAIM_OPPOSES_SHORT"
R_SUPPORTED_BY_SWEEP = "SWEEP_RECLAIM_SUPPORTS_DIRECTION"
R_ACCEPTED_BREAKOUT = "ACCEPTED_BREAKOUT_NOT_A_SWEEP"
R_AMBIGUOUS = "INTERACTION_AMBIGUOUS"
R_NO_INTERACTION = "NO_WEEKLY_LEVEL_INTERACTION"
R_NO_STRUCTURE = "STRUCTURE_UNAVAILABLE"
R_NO_COST = "COST_INPUTS_UNAVAILABLE"
R_RR_TOO_LOW = "COST_ADJUSTED_RR_BELOW_FLOOR"
R_STOP_UNKNOWN = "STOP_DISTANCE_UNKNOWN"
R_TARGET_UNKNOWN = "TARGET_UNKNOWN"

#: Eksik veri BLOCK gerekçesi DEĞİLDİR (V1 ile aynı ilke). Ölçemediğimiz için engellemek,
#: ölçtüğümüzü iddia etmenin başka biçimidir; aile `ABSTAIN`/`UNKNOWN` döner ve söyler.
MISSING_MEANS_ABSTAIN = True


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _is_long(direction: Any) -> bool | None:
    d = str(direction or "").upper()
    if d.endswith("LONG"):
        return True
    if d.endswith("SHORT"):
        return False
    return None


@dataclass
class WeeklyChallengerConfig:
    """F ve G ailelerinin versiyonlu eşikleri. `config_hash`a girer."""
    policy_version: str = "weekly_ctx_v1.0.0"
    variant: str = "base"

    # --- F: haftalık süpürme / geri alma -------------------------------------------------
    #: Karşı yöndeki teyitli süpürme+geri alma BLOCK üretsin mi.
    block_on_opposing_sweep: bool = True
    #: BLOCK için etkileşimin veri kalitesi en az bu olmalı.
    require_data_quality: str = DQ_OK
    #: Karşı süpürmenin asgari aşım büyüklüğü (ATR katı) — gürültüyle karıştırılmasın.
    min_opposing_sweep_atr: float = 0.10
    #: Kabul edilmiş kırılım ASLA süpürme sayılmaz (sözleşme; kapatılamaz).
    accepted_breakout_never_sweep: bool = True

    # --- G: yapısal risk/ödül --------------------------------------------------------------
    #: Maliyet düzeltilmiş R:R bunun altındaysa BLOCK (yalnız girdiler ÖLÇÜLMÜŞSE).
    min_cost_adjusted_rr: float = 1.0
    #: Stop mesafesi ATR cinsinden bu değerin altındaysa yapı güvenilmez sayılır.
    min_stop_distance_atr: float = 0.25
    #: Hedef için kullanılacak yapısal referans sırası.
    target_preference: tuple[str, ...] = ("active_plan", "weekly_mid", "opposite_week_boundary")

    def validate(self) -> None:
        if self.min_opposing_sweep_atr <= 0:
            raise ValueError("min_opposing_sweep_atr pozitif olmalı")
        if self.min_cost_adjusted_rr <= 0:
            raise ValueError("min_cost_adjusted_rr pozitif olmalı")
        if self.min_stop_distance_atr <= 0:
            raise ValueError("min_stop_distance_atr pozitif olmalı")
        if not self.accepted_breakout_never_sweep:
            raise ValueError("accepted_breakout_never_sweep kapatılamaz (sözleşme)")
        if not self.target_preference:
            raise ValueError("target_preference boş olamaz")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = list(v) if isinstance(v, tuple) else v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "WeeklyChallengerConfig":
        allowed = {f.name for f in fields(cls)}
        kw = {}
        for k, v in dict(d or {}).items():
            if k not in allowed:
                continue
            kw[k] = tuple(v) if k == "target_preference" and isinstance(v, list) else v
        cfg = cls(**kw)
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        return stable_id("wkcfg", self.policy_version, self.variant, self.to_dict())


#: Birkaç varyant AYNI ANDA gölgede ölçülür. Sonuçlara bakıp "en iyisi" seçilmez; hepsi
#: raporlanır ve her biri kendi terfi kapılarından ayrı ayrı geçmek zorundadır.
CONFIG_VARIANTS: tuple[dict[str, Any], ...] = (
    {"variant": "base"},
    {"variant": "strict_sweep", "min_opposing_sweep_atr": 0.25},
    {"variant": "rr_1_5", "min_cost_adjusted_rr": 1.5},
    {"variant": "observe_only", "block_on_opposing_sweep": False},
)


def build_variants(base: dict[str, Any] | None = None) -> list[WeeklyChallengerConfig]:
    base = dict(base or {})
    return [WeeklyChallengerConfig.from_dict(base | v) for v in CONFIG_VARIANTS]


# ------------------------------------------------------------------ mum bağlamı (yalnız güven)

def candle_confidence_delta(candle: dict[str, Any] | None, *, is_long: bool | None
                            ) -> dict[str, Any]:
    """Mum bağlamının GÜVEN katkısı. Karar DEĞİŞTİREMEZ; yalnız `confidence` oynatır.

    Şekil bir yön iddiası taşımaz; burada yapılan tek şey, şeklin geometrisinin adayın
    yönüyle tutarlı olup olmadığını ölçmektir. Teyit edilmemiş şekil katkı vermez.
    """
    out = {"delta": 0.0, "shapes": [], "confirmation": None, "applied": False,
           "reason": "NO_CANDLE_CONTEXT", "directional_claim": "NONE"}
    if not isinstance(candle, dict) or is_long is None:
        return out
    shapes = list(candle.get("confirmed_pattern_shapes") or [])
    conf = str(candle.get("confirmation_state") or "UNKNOWN")
    out.update({"shapes": shapes, "confirmation": conf})
    if conf != CONFIRMED:
        out["reason"] = "SHAPE_NOT_CONFIRMED_NO_WEIGHT"
        return out
    bull = {HAMMER_LIKE, INVERTED_HAMMER_LIKE, BULLISH_ENGULFING_LIKE, MORNING_STAR_LIKE,
            THREE_WHITE_SOLDIERS_LIKE} & set(shapes)
    bear = {BEARISH_ENGULFING_LIKE, EVENING_STAR_LIKE, THREE_BLACK_CROWS_LIKE} & set(shapes)
    if bool(bull) == bool(bear):
        out["reason"] = "SHAPE_HAS_NO_SINGLE_SIDE"
        return out
    aligned = (bool(bull) and is_long) or (bool(bear) and not is_long)
    out.update({"delta": 0.10 if aligned else -0.10, "applied": True,
                "reason": ("SHAPE_ALIGNED_WITH_CANDIDATE" if aligned
                           else "SHAPE_OPPOSES_CANDIDATE")})
    return out


def _verdict(family: str, decision: str, *, snap: dict[str, Any],
             cfg: WeeklyChallengerConfig, reasons: list[str], evidence: dict[str, Any],
             blockers: list[str] | None = None, confidence: float | None = None
             ) -> dict[str, Any]:
    """Ortak sonuç zarfı. `applied` DAİMA False; bu modül hiçbir şey uygulamaz."""
    base_acc = snap.get("baseline_accepted")
    return {
        "schema_version": SCHEMA_VERSION,
        "family": family,
        "policy_version": cfg.policy_version,
        "variant": cfg.variant,
        "config_id": cfg.config_id,
        "candidate_id": snap.get("candidate_id"),
        "decision_id": snap.get("decision_id"),
        "symbol": snap.get("symbol"),
        "direction": snap.get("direction"),
        "ts": snap.get("ts"),
        "decision": decision,
        "reason_codes": list(reasons),
        "evidence": evidence,
        "blockers": list(blockers or []),
        "confidence": confidence,
        "baseline_decision": (None if base_acc is None else ("ACCEPT" if base_acc else "VETO")),
        "counterfactual_decision": decision,
        "changes_baseline": (None if base_acc is None
                             else bool(base_acc and decision == BLOCK)),
        "applied": False,
        "directional_claim_from_candles": "NONE",
        "note_tr": "SHADOW: karşı-olgusal karar; aktif giriş/emir yolunu ETKİLEMEZ.",
    }


# ------------------------------------------------------------------ F: haftalık süpürme

def challenger_f(snap: dict[str, Any], weekly: dict[str, Any] | None,
                 cfg: WeeklyChallengerConfig, *,
                 candle: dict[str, Any] | None = None) -> dict[str, Any]:
    """F — önceki haftanın yüksek/düşüğüyle etkileşim.

    Sözleşme:
      * Teyitli YÜKSEK süpürme+geri alma SHORT bağlamını destekler, LONG'a KARŞIDIR.
      * Teyitli DÜŞÜK süpürme+geri alma LONG bağlamını destekler, SHORT'a KARŞIDIR.
      * **Kabul edilmiş kırılım süpürme sayılmaz** — süreklilik lehine okunur, karşı DEĞİL.
      * Eksik/belirsiz veri `ABSTAIN`/`UNKNOWN` üretir, BLOCK değil.
      * Mum bağlamı yalnız `confidence` oynatır; kararı çeviremez.
    """
    is_long = _is_long(snap.get("direction"))
    hi_i = (weekly or {}).get("high_interaction") or {}
    lo_i = (weekly or {}).get("low_interaction") or {}
    cdelta = candle_confidence_delta(candle, is_long=is_long)
    ev: dict[str, Any] = {
        "week_available": bool((weekly or {}).get("week_available")),
        "previous_week_id": (weekly or {}).get("previous_week_id"),
        "previous_completed_week_high": (weekly or {}).get("previous_completed_week_high"),
        "previous_completed_week_low": (weekly or {}).get("previous_completed_week_low"),
        "weekly_data_quality": (weekly or {}).get("data_quality"),
        "source_session": (weekly or {}).get("source_session"),
        "high_class": hi_i.get("classification"),
        "low_class": lo_i.get("classification"),
        "high_sweep_atr": hi_i.get("sweep_distance_atr"),
        "low_sweep_atr": lo_i.get("sweep_distance_atr"),
        "high_reclaim_confirmed": hi_i.get("reclaim_confirmed"),
        "low_reclaim_confirmed": lo_i.get("reclaim_confirmed"),
        "bars_elapsed_in_current_week": (weekly or {}).get("bars_elapsed_in_current_week"),
        "candle": cdelta,
        "thresholds": {"min_opposing_sweep_atr": cfg.min_opposing_sweep_atr,
                       "require_data_quality": cfg.require_data_quality,
                       "block_on_opposing_sweep": cfg.block_on_opposing_sweep},
    }
    blockers: list[str] = []
    if not weekly or not weekly.get("week_available"):
        blockers.append(R_NO_WEEKLY_DATA)
        return _verdict(FAM_WEEKLY, UNKNOWN, snap=snap, cfg=cfg,
                        reasons=[R_NO_WEEKLY_DATA], evidence=ev, blockers=blockers)
    if is_long is None:
        blockers.append("DIRECTION_UNKNOWN")
        return _verdict(FAM_WEEKLY, UNKNOWN, snap=snap, cfg=cfg,
                        reasons=["DIRECTION_UNKNOWN"], evidence=ev, blockers=blockers)
    if str(weekly.get("data_quality")) != cfg.require_data_quality:
        blockers.append(R_WEEK_PARTIAL)

    hi_c, lo_c = hi_i.get("classification"), lo_i.get("classification")
    if DATA_UNAVAILABLE in (hi_c, lo_c):
        blockers.append(f"{R_NO_WEEKLY_DATA}:interaction")
    if AMBIGUOUS in (hi_c, lo_c):
        blockers.append(R_AMBIGUOUS)

    conf = 0.5
    reasons: list[str] = []
    decision = ABSTAIN

    opposing = None
    supporting = None
    if is_long and hi_c == HIGH_SWEEP_RECLAIM:
        opposing = ("high", hi_i)
    if (not is_long) and lo_c == LOW_SWEEP_RECLAIM:
        opposing = ("low", lo_i)
    if is_long and lo_c == LOW_SWEEP_RECLAIM:
        supporting = ("low", lo_i)
    if (not is_long) and hi_c == HIGH_SWEEP_RECLAIM:
        supporting = ("high", hi_i)

    # Kabul edilmiş kırılım ASLA süpürme sayılmaz.
    accepted_dir = ((is_long and hi_c == ACCEPTED_BREAKOUT)
                    or ((not is_long) and lo_c == ACCEPTED_BREAKOUT))
    if accepted_dir:
        reasons.append(R_ACCEPTED_BREAKOUT)
        conf = 0.6

    if opposing is not None:
        _, inter = opposing
        mag = _f(inter.get("sweep_distance_atr"))
        big_enough = mag is not None and mag >= cfg.min_opposing_sweep_atr
        confirmed = bool(inter.get("reclaim_confirmed"))
        ev["opposing_side"] = opposing[0]
        ev["opposing_magnitude_atr"] = mag
        if confirmed and big_enough and cfg.block_on_opposing_sweep and not blockers:
            decision = BLOCK
            reasons.append(R_COUNTER_HIGH_SWEEP if is_long else R_COUNTER_LOW_SWEEP)
            conf = 0.7
        else:
            decision = ABSTAIN
            reasons.append(R_AMBIGUOUS if not confirmed else R_WEEK_PARTIAL)
    elif supporting is not None:
        decision = ALLOW
        reasons.append(R_SUPPORTED_BY_SWEEP)
        conf = 0.65
    elif hi_c in (NO_INTERACTION, TOUCH_ONLY) and lo_c in (NO_INTERACTION, TOUCH_ONLY):
        decision = ALLOW
        reasons.append(R_NO_INTERACTION)
        conf = 0.55
    elif BREAKOUT_UNCONFIRMED in (hi_c, lo_c) or accepted_dir:
        decision = ALLOW
        reasons.append(reasons[0] if reasons else R_OK)
    else:
        decision = ABSTAIN
        reasons.append(R_AMBIGUOUS)

    if blockers and decision == BLOCK and MISSING_MEANS_ABSTAIN:
        # Veri kalitesi düşükken ENGELLEME: ölçemediğimiz bir gerekçeyle işlem elenmez.
        decision = ABSTAIN
        reasons.append(R_WEEK_PARTIAL)

    conf = max(0.0, min(1.0, conf + (cdelta["delta"] if cdelta["applied"] else 0.0)))
    return _verdict(FAM_WEEKLY, decision, snap=snap, cfg=cfg,
                    reasons=reasons or [R_OK], evidence=ev, blockers=blockers,
                    confidence=round(conf, 4))


# ------------------------------------------------------------------ G: yapısal risk/ödül

def structural_plan(snap: dict[str, Any], weekly: dict[str, Any] | None,
                    cfg: WeeklyChallengerConfig) -> dict[str, Any]:
    """Karar anındaki yapısal geçersizleme ve hedef adaylarını ÇIKARIR (değiştirmez).

    Aktif stop/TP'ye DOKUNULMAZ; burada üretilen her şey yalnız araştırma amaçlıdır.
    Olmayan alan sıfır SAYILMAZ — `None` kalır ve `structure_quality` düşer.
    """
    px = _f(snap.get("entry_price"))
    stop = _f(snap.get("stop_price"))
    atr = _f(snap.get("atr_pct"))
    is_long = _is_long(snap.get("direction"))
    hi = _f((weekly or {}).get("previous_completed_week_high"))
    lo = _f((weekly or {}).get("previous_completed_week_low"))
    mid = _f((weekly or {}).get("previous_completed_week_mid"))
    # ATR fiyat biriminde gerekir; snapshot `atr_pct` taşır.
    atr_abs = (atr / 100.0 * px) if (atr is not None and px) else None

    targets = [t for t in (snap.get("targets") or []) if _f(t) is not None]
    plan_target = _f(targets[0]) if targets else None
    # LONG için karşı sınır önceki hafta YÜKSEĞİ, SHORT için DÜŞÜĞÜ.
    opposite = (hi if is_long else lo) if is_long is not None else None

    candidates = {"active_plan": plan_target, "weekly_mid": mid,
                  "opposite_week_boundary": opposite}
    chosen_src, chosen = None, None
    for src in cfg.target_preference:
        v = candidates.get(src)
        if v is None or px is None or is_long is None:
            continue
        if (is_long and v > px) or ((not is_long) and v < px):
            chosen_src, chosen = src, v
            break

    invalidation = stop
    inv_src = "active_stop" if stop is not None else None
    if invalidation is None and px is not None and atr_abs and is_long is not None:
        # ATR tamponlu geçersizleme — YALNIZ araştırma referansı, aktif stop DEĞİL.
        invalidation = px - atr_abs if is_long else px + atr_abs
        inv_src = "atr_buffered"

    stop_dist = (abs(px - invalidation) if (px is not None and invalidation is not None)
                 else None)
    tgt_dist = abs(chosen - px) if (chosen is not None and px is not None) else None
    stop_atr = (stop_dist / atr_abs) if (stop_dist is not None and atr_abs) else None
    tgt_atr = (tgt_dist / atr_abs) if (tgt_dist is not None and atr_abs) else None
    rr = (tgt_dist / stop_dist) if (tgt_dist is not None and stop_dist) else None

    known = sum(1 for v in (px, invalidation, chosen, atr_abs) if v is not None)
    quality = "OK" if known == 4 else ("PARTIAL" if known >= 2 else "UNAVAILABLE")
    return {
        "entry_price": px, "atr_abs": atr_abs,
        "invalidation": invalidation, "invalidation_source": inv_src,
        "nearest_swing_reference": None,          # ölçülemedi; UYDURULMAZ
        "previous_week_high": hi, "previous_week_low": lo, "previous_week_mid": mid,
        "target_candidates": candidates,
        "structure_source": chosen_src, "structural_target": chosen,
        "proposed_take_profits": [float(t) for t in targets] or None,
        "stop_distance": (round(stop_dist, 10) if stop_dist is not None else None),
        "target_distance": (round(tgt_dist, 10) if tgt_dist is not None else None),
        "stop_distance_atr": (round(stop_atr, 6) if stop_atr is not None else None),
        "target_distance_atr": (round(tgt_atr, 6) if tgt_atr is not None else None),
        "gross_reward_to_risk": (round(rr, 6) if rr is not None else None),
        "structure_quality": quality,
    }


def cost_adjusted_rr(plan: dict[str, Any], snap: dict[str, Any]) -> dict[str, Any]:
    """Maliyet düşülmüş R:R. Maliyet girdisi ölçülemezse `None` — sıfır SAYILMAZ."""
    stop_dist = _f(plan.get("stop_distance"))
    tgt_dist = _f(plan.get("target_distance"))
    px = _f(plan.get("entry_price"))
    cost_pct = _f(snap.get("expected_cost_pct"))
    slip_pct = _f(snap.get("est_slippage_pct"))
    funding = _f(snap.get("funding_rate"))
    out = {"fee_drag_r": None, "slippage_drag_r": None, "funding_drag_r": None,
           "total_drag_r": None, "cost_adjusted_reward_to_risk": None,
           "cost_provenance": {"expected_cost_pct": cost_pct, "est_slippage_pct": slip_pct,
                               "funding_rate": funding},
           "measured": False, "reason": None}
    if stop_dist is None or not stop_dist or px is None:
        out["reason"] = "STOP_DISTANCE_UNKNOWN"
        return out
    risk_abs = stop_dist
    parts: list[float] = []
    if cost_pct is not None:
        out["fee_drag_r"] = round((cost_pct / 100.0 * px) / risk_abs, 6)
        parts.append(out["fee_drag_r"])
    if slip_pct is not None:
        out["slippage_drag_r"] = round((slip_pct / 100.0 * px) / risk_abs, 6)
        parts.append(out["slippage_drag_r"])
    if funding is not None:
        out["funding_drag_r"] = round((abs(funding) * px) / risk_abs, 6)
        parts.append(out["funding_drag_r"])
    if not parts:
        out["reason"] = "NO_COST_INPUT_MEASURED"
        return out
    out["total_drag_r"] = round(sum(parts), 6)
    if tgt_dist is None:
        out["reason"] = "TARGET_UNKNOWN"
        return out
    out["cost_adjusted_reward_to_risk"] = round(tgt_dist / risk_abs - out["total_drag_r"], 6)
    out["measured"] = (cost_pct is not None and tgt_dist is not None)
    return out


def challenger_g(snap: dict[str, Any], weekly: dict[str, Any] | None,
                 cfg: WeeklyChallengerConfig, *,
                 candle: dict[str, Any] | None = None) -> dict[str, Any]:
    """G — karar anında savunulabilir bir yapısal plan var mıydı.

    **BLOCK yalnız** yapı ve maliyet girdileri GERÇEKTEN ölçülmüşse ve yapılandırılmış
    karşı-olgusal koşul sağlanıyorsa üretilir. Aksi hâlde `ABSTAIN`/`UNKNOWN`.
    Gösterilen dolar kârı kanıt sayılmaz; miktar/kaldıraç/hesap büyüklüğü doğrulanmadan
    büyük bir projeksiyon hiçbir şey ifade etmez.
    """
    plan = structural_plan(snap, weekly, cfg)
    cost = cost_adjusted_rr(plan, snap)
    is_long = _is_long(snap.get("direction"))
    cdelta = candle_confidence_delta(candle, is_long=is_long)
    ev = {"structure": plan, "cost": cost, "candle": cdelta,
          "thresholds": {"min_cost_adjusted_rr": cfg.min_cost_adjusted_rr,
                         "min_stop_distance_atr": cfg.min_stop_distance_atr,
                         "target_preference": list(cfg.target_preference)}}
    blockers: list[str] = []
    if plan["structure_quality"] == "UNAVAILABLE":
        blockers.append(R_NO_STRUCTURE)
    if plan.get("stop_distance") is None:
        blockers.append(R_STOP_UNKNOWN)
    if plan.get("structural_target") is None:
        blockers.append(R_TARGET_UNKNOWN)
    if not cost.get("measured"):
        blockers.append(R_NO_COST)

    if blockers:
        return _verdict(FAM_STRUCT, ABSTAIN if plan["structure_quality"] != "UNAVAILABLE"
                        else UNKNOWN, snap=snap, cfg=cfg,
                        reasons=[R_NO_STRUCTURE if R_NO_STRUCTURE in blockers else R_NO_COST],
                        evidence=ev, blockers=blockers, confidence=None)

    reasons: list[str] = []
    conf = 0.6
    decision = ALLOW
    car = _f(cost.get("cost_adjusted_reward_to_risk"))
    s_atr = _f(plan.get("stop_distance_atr"))
    if s_atr is not None and s_atr < cfg.min_stop_distance_atr:
        decision = ABSTAIN
        reasons.append("STOP_TOO_TIGHT_FOR_STRUCTURE")
    elif car is not None and car < cfg.min_cost_adjusted_rr:
        decision = BLOCK
        reasons.append(R_RR_TOO_LOW)
        conf = 0.7
    conf = max(0.0, min(1.0, conf + (cdelta["delta"] if cdelta["applied"] else 0.0)))
    return _verdict(FAM_STRUCT, decision, snap=snap, cfg=cfg, reasons=reasons or [R_OK],
                    evidence=ev, blockers=blockers, confidence=round(conf, 4))


def evaluate_v2(snap: dict[str, Any], weekly: dict[str, Any] | None,
                cfg: WeeklyChallengerConfig, *,
                candle: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """İki ailenin kararı — AYRI AYRI. Birleşik bir süper filtre ÜRETİLMEZ."""
    return {FAM_WEEKLY: challenger_f(snap, weekly, cfg, candle=candle),
            FAM_STRUCT: challenger_g(snap, weekly, cfg, candle=candle)}


def evaluate_all_variants(snap: dict[str, Any], weekly: dict[str, Any] | None, *,
                          candle: dict[str, Any] | None = None,
                          base: dict[str, Any] | None = None
                          ) -> dict[str, dict[str, dict[str, Any]]]:
    """Bütün yapılandırma varyantları gölgede ölçülür; hiçbiri sonuca bakılarak SEÇİLMEZ."""
    return {c.variant: evaluate_v2(snap, weekly, c, candle=candle) for c in build_variants(base)}


__all__ = ["SCHEMA_VERSION", "ALLOW", "BLOCK", "ABSTAIN", "UNKNOWN", "DECISIONS",
           "FAM_WEEKLY", "FAM_STRUCT", "FAMILIES_V2", "MISSING_MEANS_ABSTAIN",
           "CONFIG_VARIANTS", "WeeklyChallengerConfig", "build_variants",
           "candle_confidence_delta", "structural_plan", "cost_adjusted_rr",
           "challenger_f", "challenger_g", "evaluate_v2", "evaluate_all_variants",
           "R_OK", "R_NO_WEEKLY_DATA", "R_WEEK_PARTIAL", "R_COUNTER_HIGH_SWEEP",
           "R_COUNTER_LOW_SWEEP", "R_SUPPORTED_BY_SWEEP", "R_ACCEPTED_BREAKOUT",
           "R_AMBIGUOUS", "R_NO_INTERACTION", "R_NO_STRUCTURE", "R_NO_COST",
           "R_RR_TOO_LOW", "R_STOP_UNKNOWN", "R_TARGET_UNKNOWN"]
