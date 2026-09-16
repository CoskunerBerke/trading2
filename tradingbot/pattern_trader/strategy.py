# -*- coding: utf-8 -*-
"""STRATEJİ PROTOKOLÜ V1 — sonuçları görmeden yazılmış, sürümlü, küçük katalog. Bunlar ÖLÇÜLECEK HİPOTEZLERDİR; kârlı
kural ilan edilmez. Her aile: zorunlu girdiler, minimum veri, bağlam, tetik, iptal, stop, hedef, zaman aşımı, maliyet.

Ortak kurallar:
* Karar yalnız KAPALI barlarla; tetik = sonraki kapalı barın kapanışı (kapanış teyidi). Fill teyitten önceki bar
  fiyatından geriye yazılamaz: giriş, tetik barının kapanışından SONRAKİ ilk uygulanabilir doğrulanmış fiyat/zamandır
  (`book.py`), kovalamama sınırı `chase_atr` (tetik seviyesinden bu kadar ATR uzağa gitmişse plan iptal/yeniden değerlendirme).
* Stop = yapının bozulduğu yer ± `stop_buffer_atr` × ATR(giriş dilimi) (sürümlü tampon). Hedef SIRASI (sabit, sonuçlara
  göre değiştirilmez): (1) en yakın karşı teyitli 1h bölge, (2) C'de ölçülü hareket (aralık yüksekliği ×
  `measured_move_mult`), (3) `fallback_rr` × risk. İlk hangisi maliyet sonrası `min_rr_after_cost`u sağlarsa o seçilir;
  hiçbiri sağlamazsa PLAN YOK. Seçilen kaynak `target_source` ile kayda girer (aynı keyfî hedef DEĞİL).
* Plan `expires_after_bars` giriş-dilimi barı içinde tetiklenmezse SÜRESİ DOLAR. Plan, tetiklenmeden işlem açmaz.
* LONG ve SHORT ayrı, simetrik tanımlıdır; bir tarafın dayanağı yoksa o taraf plan üretmez (gerekçe kayda girer).
* Olasılık/beklenti: ÖLÇÜLMEDİ (`p_win: null`, `expected_r: null`); geometrik R/R beklenen kazanç DEĞİLDİR.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..learn.candle_context import TREND_DOWN, TREND_UP
from ..timeframes import tf_ms
from .detect import ST_CONFIRMED, atr14
from .universe import iso_ms

PROTOCOL_VERSION = "pattern_protocol_v1.0.0"
ENTRY_TF = "15m"
STRUCTURE_TF = "1h"
CONTEXT_TF = "4h"

FAMILIES: dict[str, dict[str, Any]] = {
    "A_TREND_PULLBACK": {
        "title_tr": "Trend yönünde geri çekilme + mum teyidi",
        "inputs": {"required": ["15m şekil (BULL/BEAR tarafı) + teyit", "4h önceki trend (detect_trend, ATR14)", "15m ATR14"], "optional": ["1h bölge (hedef için)"]},
        "min_bars": {"15m": 16, "4h": 12}, "context": "4h trend UP → yalnız LONG; DOWN → yalnız SHORT; RANGE/UNKNOWN → plan yok",
        "pullback": "şekil barı kapanışı, önceki 12 15m barın en yüksek (LONG) / en düşük (SHORT) kapanışından >= 1 ATR uzakta",
        "trigger": "sonraki 15m KAPANIŞ > şekil yüksek (LONG) / < şekil düşük (SHORT)", "invalidation": "kapanış < şekil düşük (LONG) / > şekil yüksek (SHORT)",
        "stop": "şekil düşük − buffer×ATR15m (LONG) / şekil yüksek + buffer×ATR15m (SHORT)", "target": "en yakın karşı 1h bölge (>= min_rr) yoksa fallback_rr×risk",
        "expires_after_bars": 8, "chase_atr": 1.0,
    },
    "B_LEVEL_REVERSAL": {
        "title_tr": "Teyitli destek/direnç yakınında dönüş yapısı + mum teyidi",
        "inputs": {"required": ["1h teyitli bölge (>=2 pivot, ATR toleransı)", "15m şekil + teyit", "15m ATR14"], "optional": ["4h trend (bilgi)"]},
        "min_bars": {"15m": 16, "1h": 13}, "context": "şekil düşüğü (LONG) destek bölgesinin <= 0.5 ATR15m yakınında; şekil yükseği (SHORT) direnç bölgesine yakın",
        "trigger": "sonraki 15m KAPANIŞ > şekil yüksek (LONG) / < şekil düşük (SHORT)", "invalidation": "kapanış bölgenin dışına (LONG: < bölge alt − buffer; SHORT: > bölge üst + buffer)",
        "stop": "bölge alt − buffer×ATR15m (LONG) / bölge üst + buffer×ATR15m (SHORT)", "target": "en yakın karşı 1h bölge (>= min_rr) yoksa fallback_rr×risk",
        "expires_after_bars": 8, "chase_atr": 1.0,
    },
    "C_COMPRESSION_BREAKOUT": {
        "title_tr": "Sıkışma / inside-bar aralığından teyitli kırılım (kısa geçmiş kolu)",
        "inputs": {"required": ["15m son 6 kapalı barın aralığı <= 1.0×ATR15m (ya da 2 ardışık inside bar)", "15m ATR14"], "optional": []},
        "min_bars": {"15m": 21}, "context": "4h/1h ZORUNLU DEĞİL — açıkça tanımlı kısa geçmiş kolu; yeni listelenen coinde 15m hazır olunca (>=21 kapalı bar) çalışabilir",
        "trigger": "15m KAPANIŞ > aralık yüksek (LONG) / < aralık düşük (SHORT); ilk tetiklenen taraf diğerini iptal eder",
        "invalidation": "karşı taraf tetiklenirse ya da aralık 8 barda kırılmazsa", "stop": "aralık karşı ucu ∓ buffer×ATR15m",
        "target": "ölçülü hareket (kırılım + measured_move_mult×aralık yüksekliği); min_rr sağlamazsa fallback_rr×risk", "expires_after_bars": 8, "chase_atr": 0.75,
    },
}
DEFAULTS = {"stop_buffer_atr": 0.25, "min_rr_after_cost": 1.5, "fallback_rr": 2.0, "measured_move_mult": 2.0, "zone_near_atr": 0.5,
            "pullback_lookback": 12, "pullback_min_atr": 1.0, "compression_bars": 6, "compression_max_atr": 1.0}

PL_AWAITING = "AWAITING_TRIGGER"
PL_TRIGGERED = "TRIGGERED"
PL_RISK_CHECK = "RISK_CHECK"
PL_OPENED = "OPENED"
PL_MANAGED = "MANAGED"
PL_CLOSED = "CLOSED"
PL_REJECTED = "REJECTED"
PL_BROKEN = "BROKEN"
PL_EXPIRED = "EXPIRED"
PL_CANCELLED = "CANCELLED"
TERMINAL = (PL_CLOSED, PL_REJECTED, PL_BROKEN, PL_EXPIRED, PL_CANCELLED)


def plan_id(symbol: str, family: str, side: str, anchor_ts: int, version: str = PROTOCOL_VERSION) -> str:
    return hashlib.sha256(f"{symbol}|USDM_PERP|{family}|{side}|{int(anchor_ts)}|{version}".encode("utf-8")).hexdigest()[:16]


def cost_fraction(*, taker_fee_pct: float, slippage_bps: float) -> float:
    """Gidiş-dönüş maliyet oranı (notional'a göre): 2×taker + 2×kayma."""
    return 2.0 * float(taker_fee_pct) / 100.0 + 2.0 * float(slippage_bps) / 10_000.0


def rr_after_cost(entry: float, stop: float, target: float, *, cost_frac: float) -> tuple[float, float]:
    """(brüt R/R, maliyet sonrası R/R). Maliyet gidiş-dönüş notional oranı olarak riske eklenir, ödülden düşülür."""
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return 0.0, 0.0
    cost = entry * cost_frac
    gross = reward / risk
    net = max(0.0, reward - cost) / (risk + cost)
    return round(gross, 4), round(net, 4)


def _opposing_zone(zones: list[dict[str, Any]], *, side: str, entry: float) -> dict[str, Any] | None:
    cands = []
    for z in zones or []:
        lo, hi = float(z["lower"]), float(z["upper"])
        if side == "LONG" and lo > entry:
            cands.append((lo - entry, z))
        elif side == "SHORT" and hi < entry:
            cands.append((entry - hi, z))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0])
    return cands[0][1]


def _target(side: str, entry: float, stop: float, zones: list[dict[str, Any]], *, cost_frac: float, p: dict[str, Any],
            measured: float | None = None) -> tuple[float | None, str, float, float]:
    """Hedef seçimi: karşı bölge (min R/R sağlıyorsa) → ölçülü hareket (C) → fallback R. Döner: (hedef, kaynak, brüt, net)."""
    risk = abs(entry - stop)
    z = _opposing_zone(zones, side=side, entry=entry)
    if z is not None:
        t = float(z["lower"]) if side == "LONG" else float(z["upper"])
        g, n = rr_after_cost(entry, stop, t, cost_frac=cost_frac)
        if n >= float(p["min_rr_after_cost"]):
            return t, "opposing_1h_zone", g, n
    if measured is not None and measured > 0:
        t = entry + measured if side == "LONG" else entry - measured
        g, n = rr_after_cost(entry, stop, t, cost_frac=cost_frac)
        if n >= float(p["min_rr_after_cost"]):
            return t, "measured_move", g, n
        # ölçülü hareket yetersiz → standart R katı (A/B ile AYNI son çare); kaynak `target_source` ile kayda girer
    t = entry + float(p["fallback_rr"]) * risk if side == "LONG" else entry - float(p["fallback_rr"]) * risk
    g, n = rr_after_cost(entry, stop, t, cost_frac=cost_frac)
    if n >= float(p["min_rr_after_cost"]):
        return t, "fallback_rr", g, n
    return None, "fallback_rr_below_min_rr", g, n


def _plan(symbol: str, family: str, side: str, *, anchor_ts: int, entry_tf: str, finding_ids: list[str], trigger_level: float, trigger_rule: str,
          invalidation_level: float, stop: float, target: float, target_source: str, rr_gross: float, rr_net: float, atr: float, as_of_ms: int,
          evidence: dict[str, Any], p: dict[str, Any], universe_entry: dict[str, Any] | None, data_source: dict[str, Any]) -> dict[str, Any]:
    step = tf_ms(entry_tf)
    valid_from = int(anchor_ts) + step                       # şekil barının kapanışı: tetik ancak SONRAKİ kapanışla
    fam = FAMILIES[family]
    exp_bars = int(fam["expires_after_bars"])
    ue = universe_entry or {}
    return {"version": PROTOCOL_VERSION, "plan_id": plan_id(symbol, family, side, anchor_ts), "symbol": symbol, "market": "USDM_PERP", "family": family,
            "family_title_tr": fam["title_tr"], "side": side, "entry_tf": entry_tf, "finding_ids": list(finding_ids), "anchor_bar_ts": int(anchor_ts),
            "created_at_ms": int(as_of_ms), "created_at": iso_ms(as_of_ms), "valid_from_ms": valid_from, "expires_at_ms": valid_from + exp_bars * step,
            "expires_at": iso_ms(valid_from + exp_bars * step), "expires_after_bars": exp_bars,
            "trigger": {"level": round(float(trigger_level), 10), "rule": trigger_rule, "tf": entry_tf, "text_tr": "%s %s kapanışı %s %.6g" % (
                entry_tf, "boğa" if side == "LONG" else "ayı", "üstünde" if side == "LONG" else "altında", float(trigger_level))},
            "invalidation": {"level": round(float(invalidation_level), 10), "rule": fam["invalidation"]},
            "stop": round(float(stop), 10), "stop_rule": fam["stop"], "stop_buffer_atr": float(p["stop_buffer_atr"]), "atr": round(float(atr), 10),
            "target": round(float(target), 10), "target_source": target_source, "target_rule": fam["target"],
            "rr_gross": rr_gross, "rr_after_cost": rr_net, "min_rr_after_cost": float(p["min_rr_after_cost"]), "chase_atr": float(fam["chase_atr"]),
            "entry_type": "MARKET_AFTER_TRIGGER_CLOSE", "leverage": 1, "p_win": None, "expected_r": None, "edge_note_tr": "ölçülmedi (kanıt yok)",
            "cohort": ue.get("cohort"), "age_h_at_plan": ue.get("age_h"), "futures_first_trade_ms": ue.get("futures_first_trade_ms"),
            "status": PL_AWAITING, "status_history": [{"status": PL_AWAITING, "at_ms": int(as_of_ms), "reason": "PLAN_CREATED"}],
            "last_evaluated_bar_ts": None, "triggered_at_ms": None, "trigger_bar_ts": None, "position_id": None, "reasons": [], "evidence": evidence,
            "data_source": dict(data_source or {}), "size": None, "risk": None}


def build_plans(symbol: str, *, as_of_ms: int, bars_by_tf: dict[str, list[dict[str, Any]]], findings: list[dict[str, Any]],
                levels_1h: dict[str, Any] | None, trend_4h: dict[str, Any] | None, cost_frac: float, params: dict[str, Any] | None = None,
                universe_entry: dict[str, Any] | None = None, data_source: dict[str, Any] | None = None,
                enabled_families: tuple[str, ...] | list[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Kapalı 15m/1h/4h barlar + TEYİTLİ bulgular → koşullu planlar. Döner: (planlar, üretilmeyen taraflar için gerekçeler)."""
    p = {**DEFAULTS, **(params or {})}
    fams = tuple(enabled_families or FAMILIES.keys())
    b15 = [b for b in (bars_by_tf.get(ENTRY_TF) or []) if int(b["timestamp"]) + tf_ms(ENTRY_TF) <= int(as_of_ms)]
    plans: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    atr = atr14(b15)
    if atr is None or len(b15) < 16:
        return [], [{"family": "*", "reason": "ENTRY_TF_NOT_READY", "n_15m": len(b15), "need": 16}]
    zones = list((levels_1h or {}).get("zones") or [])
    ds = dict(data_source or {})
    ht = str((trend_4h or {}).get("trend") or "UNKNOWN")
    # --- A ve B: teyitli 15m bulgusundan (yalnız yön taşıyan şekiller) ---
    confirmed = [f for f in findings if f.get("tf") == ENTRY_TF and f.get("status") == ST_CONFIRMED and f.get("side") in ("LONG", "SHORT") and not f.get("neutral")]
    for f in confirmed:
        side = f["side"]
        anchor = int(f["bar_ts"])
        ph, pl_ = float(f["pattern_high"]), float(f["pattern_low"])
        buf = float(p["stop_buffer_atr"]) * atr
        trig = ph if side == "LONG" else pl_
        if "A_TREND_PULLBACK" in fams:
            ok_ctx = (ht == TREND_UP and side == "LONG") or (ht == TREND_DOWN and side == "SHORT")
            if not ok_ctx:
                skipped.append({"family": "A_TREND_PULLBACK", "side": side, "finding_id": f["finding_id"], "reason": "HTF_TREND_MISMATCH", "htf_trend": ht})
            else:
                idx = next((i for i, b in enumerate(b15) if int(b["timestamp"]) == anchor), None)
                ok_pb, pb_detail = _pullback_ok(b15, idx, side=side, atr=atr, p=p) if idx is not None else (False, {"reason": "ANCHOR_NOT_IN_BARS"})
                if not ok_pb:
                    skipped.append({"family": "A_TREND_PULLBACK", "side": side, "finding_id": f["finding_id"], "reason": "NO_PULLBACK", **pb_detail})
                else:
                    stop = pl_ - buf if side == "LONG" else ph + buf
                    entry_est = trig
                    tgt, src, g, n = _target(side, entry_est, stop, zones, cost_frac=cost_frac, p=p)
                    if tgt is None:
                        skipped.append({"family": "A_TREND_PULLBACK", "side": side, "finding_id": f["finding_id"], "reason": "RR_BELOW_MIN", "target_source": src, "rr_after_cost": n})
                    else:
                        plans.append(_plan(symbol, "A_TREND_PULLBACK", side, anchor_ts=anchor, entry_tf=ENTRY_TF, finding_ids=[f["finding_id"]], trigger_level=trig,
                                           trigger_rule="close_above" if side == "LONG" else "close_below", invalidation_level=pl_ if side == "LONG" else ph,
                                           stop=stop, target=tgt, target_source=src, rr_gross=g, rr_net=n, atr=atr, as_of_ms=as_of_ms,
                                           evidence={"shape": f["shape"], "htf_trend": ht, "pullback": pb_detail, "zone_used": src == "opposing_1h_zone"},
                                           p=p, universe_entry=universe_entry, data_source=ds))
        if "B_LEVEL_REVERSAL" in fams:
            zone = _zone_near(zones, side=side, ph=ph, pl_=pl_, atr=atr, p=p)
            if zone is None:
                skipped.append({"family": "B_LEVEL_REVERSAL", "side": side, "finding_id": f["finding_id"], "reason": "NO_CONFIRMED_ZONE_NEAR", "n_zones": len(zones)})
            else:
                stop = float(zone["lower"]) - buf if side == "LONG" else float(zone["upper"]) + buf
                inval = float(zone["lower"]) - buf if side == "LONG" else float(zone["upper"]) + buf
                tgt, src, g, n = _target(side, trig, stop, zones, cost_frac=cost_frac, p=p)
                if tgt is None:
                    skipped.append({"family": "B_LEVEL_REVERSAL", "side": side, "finding_id": f["finding_id"], "reason": "RR_BELOW_MIN", "target_source": src, "rr_after_cost": n})
                else:
                    plans.append(_plan(symbol, "B_LEVEL_REVERSAL", side, anchor_ts=anchor, entry_tf=ENTRY_TF, finding_ids=[f["finding_id"]], trigger_level=trig,
                                       trigger_rule="close_above" if side == "LONG" else "close_below", invalidation_level=inval, stop=stop, target=tgt,
                                       target_source=src, rr_gross=g, rr_net=n, atr=atr, as_of_ms=as_of_ms,
                                       evidence={"shape": f["shape"], "zone": zone, "htf_trend": ht}, p=p, universe_entry=universe_entry, data_source=ds))
    # --- C: sıkışma (bulgu gerekmez; kısa geçmiş kolu) ---
    if "C_COMPRESSION_BREAKOUT" in fams:
        comp = _compression(b15, atr=atr, p=p)
        if comp is None:
            skipped.append({"family": "C_COMPRESSION_BREAKOUT", "side": "*", "reason": "NO_COMPRESSION"})
        else:
            hi, lo, anchor = comp["high"], comp["low"], comp["anchor_ts"]
            buf = float(p["stop_buffer_atr"]) * atr
            height = hi - lo
            for side in ("LONG", "SHORT"):
                trig = hi if side == "LONG" else lo
                stop = lo - buf if side == "LONG" else hi + buf
                tgt, src, g, n = _target(side, trig, stop, zones, cost_frac=cost_frac, p=p, measured=float(p["measured_move_mult"]) * height)
                if tgt is None:
                    skipped.append({"family": "C_COMPRESSION_BREAKOUT", "side": side, "reason": "RR_BELOW_MIN", "target_source": src, "rr_after_cost": n})
                    continue
                plans.append(_plan(symbol, "C_COMPRESSION_BREAKOUT", side, anchor_ts=anchor, entry_tf=ENTRY_TF, finding_ids=[], trigger_level=trig,
                                   trigger_rule="close_above" if side == "LONG" else "close_below", invalidation_level=lo if side == "LONG" else hi,
                                   stop=stop, target=tgt, target_source=src, rr_gross=g, rr_net=n, atr=atr, as_of_ms=as_of_ms,
                                   evidence={"compression": comp, "htf_trend": ht, "paired_side": "SHORT" if side == "LONG" else "LONG"},
                                   p=p, universe_entry=universe_entry, data_source=ds))
    return plans, skipped


def _pullback_ok(b15: list[dict[str, Any]], idx: int, *, side: str, atr: float, p: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    lb = int(p["pullback_lookback"])
    if idx is None or idx < lb:
        return False, {"reason": "NOT_ENOUGH_BARS_FOR_PULLBACK", "need": lb}
    window = b15[idx - lb:idx]
    close = float(b15[idx]["close"])
    if side == "LONG":
        ref = max(float(b["close"]) for b in window)
        dist = (ref - close) / atr
    else:
        ref = min(float(b["close"]) for b in window)
        dist = (close - ref) / atr
    return bool(dist >= float(p["pullback_min_atr"])), {"ref_close": ref, "distance_atr": round(dist, 4), "min_atr": float(p["pullback_min_atr"]), "lookback": lb}


def _zone_near(zones: list[dict[str, Any]], *, side: str, ph: float, pl_: float, atr: float, p: dict[str, Any]) -> dict[str, Any] | None:
    tol = float(p["zone_near_atr"]) * atr
    best = None
    for z in zones:
        lo, hi = float(z["lower"]), float(z["upper"])
        if side == "LONG":
            d = 0.0 if lo - tol <= pl_ <= hi + tol else min(abs(pl_ - lo), abs(pl_ - hi))
            ok = pl_ <= hi + tol and pl_ >= lo - tol
        else:
            d = 0.0 if lo - tol <= ph <= hi + tol else min(abs(ph - lo), abs(ph - hi))
            ok = ph >= lo - tol and ph <= hi + tol
        if ok and (best is None or d < best[0]):
            best = (d, z)
    return dict(best[1]) if best else None


def _compression(b15: list[dict[str, Any]], *, atr: float, p: dict[str, Any]) -> dict[str, Any] | None:
    n = int(p["compression_bars"])
    if len(b15) < n + 15:
        return None
    win = b15[-n:]
    hi, lo = max(float(b["high"]) for b in win), min(float(b["low"]) for b in win)
    rng = hi - lo
    inside2 = len(b15) >= 3 and all(float(b15[-k]["high"]) <= float(b15[-k - 1]["high"]) and float(b15[-k]["low"]) >= float(b15[-k - 1]["low"]) for k in (1, 2))
    if rng <= float(p["compression_max_atr"]) * atr or inside2:
        return {"high": hi, "low": lo, "height": rng, "height_atr": round(rng / atr, 4), "bars": n, "anchor_ts": int(win[-1]["timestamp"]),
                "kind": "inside_bars" if inside2 and not (rng <= float(p["compression_max_atr"]) * atr) else "range_compression"}
    return None


def evaluate_trigger(plan: dict[str, Any], bar: dict[str, Any]) -> str | None:
    """Tek KAPALI barla plan durumu: 'TRIGGERED' | 'BROKEN' | None. Bar planın giriş diliminde ve `valid_from` sonrasında olmalı."""
    ts = int(bar["timestamp"])
    if ts + tf_ms(plan["entry_tf"]) <= int(plan["valid_from_ms"]):
        return None
    c = float(bar["close"])
    side = plan["side"]
    trig, inval = float(plan["trigger"]["level"]), float(plan["invalidation"]["level"])
    if side == "LONG":
        if c > trig:
            return PL_TRIGGERED
        if c < inval:
            return PL_BROKEN
    else:
        if c < trig:
            return PL_TRIGGERED
        if c > inval:
            return PL_BROKEN
    return None


__all__ = ["PROTOCOL_VERSION", "ENTRY_TF", "STRUCTURE_TF", "CONTEXT_TF", "FAMILIES", "DEFAULTS", "PL_AWAITING", "PL_TRIGGERED", "PL_RISK_CHECK",
           "PL_OPENED", "PL_MANAGED", "PL_CLOSED", "PL_REJECTED", "PL_BROKEN", "PL_EXPIRED", "PL_CANCELLED", "TERMINAL", "plan_id", "cost_fraction",
           "rr_after_cost", "build_plans", "evaluate_trigger"]
