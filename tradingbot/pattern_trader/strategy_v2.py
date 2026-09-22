# -*- coding: utf-8 -*-
"""FORMASYON BOTU PROTOKOLÜ v2 — ORTAK KATALOG KAYDI → KOŞULLU PLAN (structures_v1).

v1 (`strategy.py`, A/B/C) kendi 15m mum bulgusundan plan kuruyordu. v2 planı doğrudan ortak analizin kaydından kurar:
tetik kuralı/seviyesi, geçersizlik, stop ve (varsa) yapısal hedef KAYITTAN gelir; panel ve motor aynı kaydı çizer.
Aile bağlam süzgeçleri (politika matrisi C, "Formasyon" satırı — kodlamadan önce yazıldı):

* A2_TREND_PULLBACK   — 15m taraflı mum kaydı, 4h önceki trend aynı yönde, şekil barı geri çekilmede (v1 kuralı).
* B2_LEVEL_REVERSAL   — 15m taraflı mum kaydı, teyitli 1h bölgeye ≤ 0.5 ATR (v1 kuralı).
* C2_COMPRESSION      — 15m COMPRESSION_BREAKOUT (iki taraf; biri dolunca diğeri OTHER_PLAN_FILLED ile iptal).
* D2_CHART_STRUCTURE  — 15m ya da 1h grafik yapısı (çift/üçlü dip-tepe, OBO, üçgen, bayrak, flama); tetik o yapının
  KENDİ diliminin kapanışıyla değerlendirilir (1h yapı 15m kapanışıyla tetiklenmez).
* E2_SWEEP_RECLAIM    — 15m teyitli taşma-geri dönüş (kayıt teyitliyse plan TETİKLENMİŞ doğar).
* F2_RETEST_HOLD      — 15m teyitli kırılım-geri test-korunma (aynı).

Oluşan (FORMING) kayıt → AWAITING_TRIGGER planı; teyitli (taze) kayıt → TRIGGERED planı (teyit kapanışından sonraki
ilk doğrulanmış fiyatla giriş; kovalama ve R/R `_try_open`da YENİDEN ölçülür). Olasılık/beklenti ÜRETİLMEZ.

PLAN KAYDI İZLER (2026-09-23): bekleyen planın tetik/bozulma/süre olayı ORTAK KAYITTAN gelir; plan kendi seviyesini
dondurup ayrı bir tetik değerlendirmesi YAPMAZ. Oluşan yapı gelişirken (bayrak uzar, eğik boyun çizgisi ilerler)
seviyeler her taramada kayıttan yenilenir (`plan_levels_from_record`); kayıt TEYİT → TETİKLENDİ, BOZULDU → BOZULDU,
SÜRESİ DOLDU → SÜRESİ DOLDU, analizden ÇEKİLDİ → İPTAL (RECORD_WITHDRAWN). Böylece beş bot aynı kaydı aynı okur.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..learn.candle_context import TREND_DOWN, TREND_UP
from ..structures import catalog as K
from ..timeframes import tf_ms
from .strategy import DEFAULTS, PL_AWAITING, PL_TRIGGERED, _pullback_ok, _target, _zone_near, rr_after_cost
from .universe import iso_ms

PROTOCOL_V2 = "pattern_protocol_v2.0.0"
FAMILIES_V2: dict[str, dict[str, Any]] = {
    "A2_TREND_PULLBACK": {"title_tr": "Katalog mumu: trend yönünde geri çekilme sonu (4h trend uyumlu)", "chase_atr": 1.0},
    "B2_LEVEL_REVERSAL": {"title_tr": "Katalog mumu: teyitli 1h bölgede dönüş", "chase_atr": 1.0},
    "C2_COMPRESSION": {"title_tr": "Katalog sıkışması: iki taraflı kırılım planı", "chase_atr": 0.75},
    "D2_CHART_STRUCTURE": {"title_tr": "Katalog grafik yapısı: boyun/sınır kırılımı", "chase_atr": 1.0},
    "E2_SWEEP_RECLAIM": {"title_tr": "Katalog: taşma ve içeride kapanış (başarısız kırılım)", "chase_atr": 1.0},
    "F2_RETEST_HOLD": {"title_tr": "Katalog: kırılan seviyeye geri test ve korunma", "chase_atr": 1.0},
}
PLAN_TIMEFRAMES = ("15m", "1h")


def plan_id_v2(symbol: str, family: str, side: str, pattern_id: str) -> str:
    return hashlib.sha256(f"{symbol}|USDM_PERP|{family}|{side}|{pattern_id}|{PROTOCOL_V2}".encode("utf-8")).hexdigest()[:16]


def _family_for(rec: dict[str, Any], *, trend_4h: str, b15: list[dict[str, Any]], zones: list[dict[str, Any]],
                atr15: float | None, p: dict[str, Any]) -> tuple[str | None, str, dict[str, Any]]:
    """(aile | None, gerekçe, kanıt). Aile yoksa gerekçe üretilir (sessiz ret yok)."""
    tf, fam, name, side, st = rec.get("timeframe"), rec.get("family"), rec.get("name"), rec.get("side"), rec.get("status")
    if side not in (K.LONG, K.SHORT):
        return None, "NO_SIDE", {}
    if st not in (K.ST_FORMING, K.ST_CONFIRMED):
        return None, "STATUS_%s" % st, {}
    if fam == K.FAMILY_CANDLE:
        if tf != "15m":
            return None, "CANDLE_CONTEXT_TF_ONLY", {}
        anchor = int(rec["anchors"][-1]["ts"])
        ok_trend = (trend_4h == TREND_UP and side == K.LONG) or (trend_4h == TREND_DOWN and side == K.SHORT)
        if ok_trend and atr15:
            idx = next((i for i, b in enumerate(b15) if int(b["timestamp"]) == anchor), None)
            ok_pb, detail = _pullback_ok(b15, idx, side=side, atr=atr15, p=p) if idx is not None else (False, {"reason": "ANCHOR_NOT_IN_BARS"})
            if ok_pb:
                return "A2_TREND_PULLBACK", "", {"htf_trend": trend_4h, "pullback": detail}
        if atr15:
            z = _zone_near(zones, side=side, ph=float(rec.get("pattern_high") or 0), pl_=float(rec.get("pattern_low") or 0), atr=atr15, p=p)
            if z is not None:
                return "B2_LEVEL_REVERSAL", "", {"zone": z, "htf_trend": trend_4h}
        return None, "CANDLE_WITHOUT_CONTEXT", {"htf_trend": trend_4h}
    if fam == K.FAMILY_CHART:
        return ("D2_CHART_STRUCTURE", "", {"htf_trend": trend_4h}) if tf in PLAN_TIMEFRAMES else (None, "CHART_TF_NOT_PLANNED", {})
    if tf != "15m":
        return None, "SCENARIO_TF_NOT_PLANNED", {}
    if name == "COMPRESSION_BREAKOUT":
        return ("C2_COMPRESSION", "", {"htf_trend": trend_4h}) if st == K.ST_FORMING else (None, "COMPRESSION_ALREADY_BROKEN_OUT", {})
    if name == "SWEEP_RECLAIM":
        return ("E2_SWEEP_RECLAIM", "", {"htf_trend": trend_4h}) if st == K.ST_CONFIRMED else (None, "SWEEP_NOT_CONFIRMED", {})
    if name == "BREAK_RETEST_HOLD":
        return ("F2_RETEST_HOLD", "", {"htf_trend": trend_4h}) if st == K.ST_CONFIRMED else (None, "RETEST_NOT_CONFIRMED", {})
    return None, "NO_PLAN_FAMILY_%s" % name, {}


def plan_levels_from_record(rec: dict[str, Any], *, side: str, zones: list[dict[str, Any]], cost_frac: float,
                             p: dict[str, Any], atr_fallback: float | None = None) -> tuple[dict[str, Any] | None, str]:
    """Kayıttan plan seviyeleri — TEK tanım (plan kurulurken ve kayıt geliştikçe aynı hesap). Döner: (alanlar | None,
    gerekçe). Hedef: yapısal ölçülü hareket (min R/R sağlıyorsa) → karşı 1h bölge → fallback R."""
    trig = (rec.get("trigger") or {}).get("level")
    inv = (rec.get("invalidation") or {}).get("level")
    stop = rec.get("stop")
    atr = rec.get("atr") or atr_fallback
    if trig is None or inv is None or stop is None or not atr:
        return None, "RECORD_GEOMETRY_INCOMPLETE"
    trig, inv, stop, atr = float(trig), float(inv), float(stop), float(atr)
    tgt, src, g, n = None, "", 0.0, 0.0
    for t0 in list(rec.get("targets") or []):
        g, n = rr_after_cost(trig, stop, float(t0), cost_frac=cost_frac)
        if n >= float(p["min_rr_after_cost"]):
            tgt, src = float(t0), "structure_measured_move"
            break
    if tgt is None:
        tgt, src, g, n = _target(side, trig, stop, zones, cost_frac=cost_frac, p=p)
    if tgt is None:
        return None, "RR_BELOW_MIN"
    etf = str(rec.get("timeframe"))
    rule = str((rec.get("trigger") or {}).get("rule") or ("close_above" if side == K.LONG else "close_below"))
    if rule not in ("close_above", "close_below"):
        rule = "close_above" if side == K.LONG else "close_below"
    return {"trigger": {"level": round(trig, 10), "rule": rule, "tf": etf, "sloped": bool((rec.get("trigger") or {}).get("sloped")),
                        "text_tr": "%s %s kapanışı %s %.6g" % (etf, "boğa" if side == K.LONG else "ayı",
                                                              "üstünde" if side == K.LONG else "altında", trig)},
            "invalidation": {"level": round(inv, 10), "rule": (rec.get("invalidation") or {}).get("rule")},
            "stop": round(stop, 10), "atr": round(atr, 10), "target": round(float(tgt), 10), "target_source": src,
            "rr_gross": g, "rr_after_cost": n,
            "structure_geometry": {"anchors": rec.get("anchors"), "geometry": rec.get("geometry"),
                                   "confirm_bar": rec.get("confirm_bar"), "detected_at_ms": rec.get("detected_at_ms"),
                                   "expires_at_ms": rec.get("expires_at_ms"), "atr": rec.get("atr")}}, ""


def build_plans_v2(symbol: str, *, as_of_ms: int, analyses: dict[str, dict[str, Any] | None], bars_by_tf: dict[str, list],
                   levels_1h: dict[str, Any] | None, trend_4h: dict[str, Any] | None, cost_frac: float,
                   params: dict[str, Any] | None = None, universe_entry: dict[str, Any] | None = None,
                   data_source: dict[str, Any] | None = None, used_patterns: set[str] | None = None
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Katalog kayıtları → koşullu planlar. Döner: (planlar, plana dönüşmeyen kayıtlar için gerekçeler)."""
    from .detect import atr14
    p = {**DEFAULTS, **(params or {})}
    used = set(used_patterns or ())
    b15 = [b for b in (bars_by_tf.get("15m") or []) if int(b["timestamp"]) + tf_ms("15m") <= int(as_of_ms)]
    atr15 = atr14(b15) if len(b15) >= 15 else None
    zones = list((levels_1h or {}).get("zones") or [])
    ht = str((trend_4h or {}).get("trend") or "UNKNOWN")
    ue = universe_entry or {}
    plans: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for tf in PLAN_TIMEFRAMES:
        an = analyses.get(tf)
        if not an:
            skipped.append({"family": "*", "tf": tf, "reason": "ANALYSIS_UNAVAILABLE"})
            continue
        for rec in an.get("records") or []:
            if rec.get("side") not in (K.LONG, K.SHORT) or rec.get("status") not in (K.ST_FORMING, K.ST_CONFIRMED):
                continue
            if rec["pattern_id"] in used:
                skipped.append({"family": "*", "pattern_id": rec["pattern_id"], "reason": "PATTERN_ALREADY_USED"})
                continue
            fam, why, ev = _family_for(rec, trend_4h=ht, b15=b15, zones=zones, atr15=atr15, p=p)
            if fam is None:
                skipped.append({"family": "*", "pattern_id": rec["pattern_id"], "name": rec.get("name"), "tf": tf, "reason": why})
                continue
            side = rec["side"]
            lv, why2 = plan_levels_from_record(rec, side=side, zones=zones, cost_frac=cost_frac, p=p, atr_fallback=atr15)
            if lv is None:
                skipped.append({"family": fam, "pattern_id": rec["pattern_id"], "reason": why2})
                continue
            confirmed = rec.get("status") == K.ST_CONFIRMED
            etf = str(rec.get("timeframe"))
            step = tf_ms(etf)
            valid_from = int(rec.get("detected_at_ms") or as_of_ms)
            expires = int(rec.get("expires_at_ms") or (valid_from + 8 * step))
            pl = {"version": PROTOCOL_V2, "plan_id": plan_id_v2(symbol, fam, side, rec["pattern_id"]), "symbol": symbol,
                  "market": "USDM_PERP", "family": fam, "family_title_tr": FAMILIES_V2[fam]["title_tr"], "side": side,
                  "entry_tf": etf, "finding_ids": [rec["pattern_id"]], "pattern_id": rec["pattern_id"],
                  "anchor_bar_ts": int(rec["anchors"][-1]["ts"]) if rec.get("anchors") else valid_from - step,
                  "created_at_ms": int(as_of_ms), "created_at": iso_ms(as_of_ms), "valid_from_ms": valid_from,
                  "expires_at_ms": expires, "expires_at": iso_ms(expires), "expires_after_bars": max(1, (expires - valid_from) // step),
                  "trigger": lv["trigger"], "invalidation": lv["invalidation"], "stop": lv["stop"],
                  "stop_rule": "katalog kaydı: geçersizlik ∓ %.2f×ATR" % K.DEFAULT_CONFIG.stop_buffer_atr,
                  "stop_buffer_atr": K.DEFAULT_CONFIG.stop_buffer_atr, "atr": lv["atr"], "target": lv["target"],
                  "target_source": lv["target_source"], "target_rule": "yapısal ölçülü hareket → karşı 1h bölge → fallback R",
                  "rr_gross": lv["rr_gross"], "rr_after_cost": lv["rr_after_cost"], "min_rr_after_cost": float(p["min_rr_after_cost"]),
                  "chase_atr": float(FAMILIES_V2[fam]["chase_atr"]), "entry_type": "MARKET_AFTER_TRIGGER_CLOSE", "leverage": 1,
                  "p_win": None, "expected_r": None, "edge_note_tr": "ölçülmedi (kanıt yok)",
                  "cohort": ue.get("cohort"), "age_h_at_plan": ue.get("age_h"), "futures_first_trade_ms": ue.get("futures_first_trade_ms"),
                  "status": PL_TRIGGERED if confirmed else PL_AWAITING,
                  "status_history": [{"status": PL_AWAITING, "at_ms": int(as_of_ms), "reason": "PLAN_CREATED_FROM_CATALOG"}],
                  "last_evaluated_bar_ts": None, "triggered_at_ms": None, "trigger_bar_ts": None, "position_id": None, "reasons": [],
                  "evidence": {**ev, "record": {k: rec.get(k) for k in ("pattern_id", "name", "family", "timeframe", "status",
                                                                        "detected_at_ms", "confirmed_at_ms", "analysis_id", "reference")}},
                  "structure": {"pattern_id": rec["pattern_id"], "name": rec.get("name"), "family": rec.get("family"),
                                "timeframe": etf, "status": rec.get("status"), "analysis_id": rec.get("analysis_id"),
                                "policy_version": K.POLICY_VERSION, "confirmed_at_ms": rec.get("confirmed_at_ms")},
                  # panel motorun ÇİZDİĞİ kaydı çizer: dayanak noktaları ve geometri plana (işlem kaydına DEĞİL) girer
                  "structure_geometry": lv["structure_geometry"], "record_revisions": 0,
                  "data_source": dict(data_source or {}), "size": None, "risk": None}
            if confirmed:
                cb = rec.get("confirm_bar") or {}
                pl["status_history"].append({"status": PL_TRIGGERED, "at_ms": int(rec.get("confirmed_at_ms") or as_of_ms),
                                             "reason": "RECORD_CONFIRMED_%s" % rec.get("name")})
                pl.update(triggered_at_ms=rec.get("confirmed_at_ms"), trigger_bar_ts=cb.get("ts"), trigger_close=cb.get("close"))
            plans.append(pl)
    return plans, skipped


__all__ = ["FAMILIES_V2", "PLAN_TIMEFRAMES", "PROTOCOL_V2", "build_plans_v2", "plan_id_v2", "plan_levels_from_record"]
