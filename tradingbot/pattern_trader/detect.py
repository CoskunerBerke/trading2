# -*- coding: utf-8 -*-
"""BULGU — kapalı mumlarda şekil + bağlam; aynı kimlikli kanıt nesnesi; teyit durumu sonraki kapanışlarla güncellenir.

Şekiller `learn.candle_context._shapes` (ikinci formül YOK); önceki trend `detect_trend`; seviyeler `chart_analysis.pivots`
+ `equal_level_clusters` (formasyon dedektörüyle AYNI pivot). Bulgu yön iddiası taşımaz: `side` yalnız şeklin geometrik
tarafıdır (BULL_SIDE_SHAPES/BEAR_SIDE_SHAPES); işlem kararı `strategy.py`de, açık ve sürümlü kural katmanında oluşur.

Durumlar: FOUND (kapalı barda bulundu; en erken teyit anı = sonraki barın kapanışı) → AWAITING_CONFIRMATION → CONFIRMED |
UNCONFIRMED → BROKEN (geçersizlik seviyesi kapanışla aşıldı) | EXPIRED (azami yaş). Kapanmamış bar görüntüsü gözlem
olabilir; bulgu hep KAPALI bardan üretilir. Geçmişte hangi zamanda ne bilindiği: `seen_at_ms` (bar kapanışı) ve
`confirmed_at_ms` alanlarıyla yeniden oynatılabilir.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..chart_analysis import pivots as _pivots
from ..learn.candle_context import (BEAR_SIDE_SHAPES_EXT, BULL_SIDE_SHAPES_EXT, CONFIRMED, NO_PATTERN, UNCONFIRMED,
                                    CandleContextConfig, _shapes, candle_metrics, detect_trend, evaluate_confirmation)
from ..learn.multitimeframe_context import equal_level_clusters
from ..timeframes import tf_ms
from .universe import iso_ms

FINDING_VERSION = "pattern_finding_v1"
ST_FOUND = "FOUND"
ST_AWAITING = "AWAITING_CONFIRMATION"
ST_CONFIRMED = "CONFIRMED"
ST_UNCONFIRMED = "UNCONFIRMED"
ST_BROKEN = "BROKEN"
ST_EXPIRED = "EXPIRED"
MAX_AGE_BARS = 12            # bulgu bu kadar kapalı bar sonra süresi dolar (teyitsiz kalırsa)
NEUTRAL_SHAPES = ("DOJI_LIKE", "SPINNING_TOP_LIKE", "TRI_STAR_LIKE", "MARUBOZU_LIKE")   # tek başına yön vermez


def atr14(bars: list[dict[str, Any]], n: int = 14) -> float | None:
    if len(bars) < n + 1:
        return None
    trs = []
    for i in range(len(bars) - n, len(bars)):
        h, lo, pc = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i - 1]["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    v = sum(trs) / n
    return v if v > 0 else None


def shape_side(shape: str) -> str | None:
    if shape in BULL_SIDE_SHAPES_EXT:
        return "LONG"
    if shape in BEAR_SIDE_SHAPES_EXT:
        return "SHORT"
    return None


def finding_id(symbol: str, tf: str, shape: str, bar_ts: int, policy_version: str) -> str:
    return hashlib.sha256(f"{symbol}|USDM_PERP|{tf}|{shape}|{int(bar_ts)}|{policy_version}".encode("utf-8")).hexdigest()[:16]


def levels_for(bars: list[dict[str, Any]], *, tf: str, atr: float | None, lookback: int = 3, tolerance_atr: float = 0.5) -> dict[str, Any]:
    """Teyitli pivotlar + eşit seviye kümeleri (bölgeler). Dayanak/ATR yoksa bölge YOK (uydurulmaz)."""
    if len(bars) < 2 * lookback + 1:
        return {"pivots": [], "zones": [], "reason": "NOT_ENOUGH_BARS"}
    piv = _pivots(bars, lookback=lookback, tf_ms=tf_ms(tf))
    conf = piv.get("confirmed") or []
    zones = []
    if atr:
        for c in equal_level_clusters(conf, atr=atr, tolerance_atr=tolerance_atr):
            members = [p for p in conf if int(p["timestamp"]) in set(c["member_timestamps"])]
            lv = [float(m["level"]) for m in members]
            zones.append({"level": float(c["level"]), "lower": min(lv), "upper": max(lv), "n": len(members), "side": c["side"],
                          "confirmed_at_ms": max(int(m.get("confirmed_at_ts") or 0) for m in members) or None,
                          "member_ts": [int(m["timestamp"]) for m in members]})
    return {"pivots": [{"timestamp": int(p["timestamp"]), "level": float(p["level"]), "side": p["side"], "confirmed_at_ms": p.get("confirmed_at_ts")} for p in conf],
            "zones": zones, "lookback": lookback, "tolerance_atr": tolerance_atr}


#: Bir taramada geriye dönük en fazla bu kadar YENİ kapanmış bar için şekil aranır (dönen kuyrukta bir sembol birkaç
#: tur atlandığında aradaki barların şekilleri KAYBOLMAZ; daha eskisi zaten `MAX_AGE_BARS` ile süresi dolmuş olurdu).
MAX_BACKFILL_BARS = 16


def detect_findings(symbol: str, tf: str, bars: list[dict[str, Any]], *, as_of_ms: int, cfg: CandleContextConfig | None = None,
                    data_source: dict[str, Any] | None = None, htf_trend: dict[str, Any] | None = None,
                    levels: dict[str, Any] | None = None, since_ts: int | None = None) -> list[dict[str, Any]]:
    """`since_ts`ten SONRA kapanmış her bar için şekiller → bulgu kayıtları (bar başına, şekil başına bir kimlik).

    `since_ts=None` ise yalnız son kapalı bar taranır. Yalnız son bara bakmak, dönen tarama kuyruğunda bir sembol
    birkaç tur atlandığında aradaki şekilleri SESSİZCE kaybettirirdi; bu yüzden geriye dönük (en fazla
    `MAX_BACKFILL_BARS`) tarama yapılır. Her bar KENDİ anındaki bilgiyle değerlendirilir: `rows[:i+1]` — o barın
    kapanışından SONRAKİ hiçbir bar şekle, trende ya da ATR'ye girmez (ileriye bakış YOK)."""
    cfg = cfg or CandleContextConfig()
    rows = [b for b in (bars or []) if isinstance(b, dict) and int(b["timestamp"]) + tf_ms(tf) <= int(as_of_ms)]
    if len(rows) < 1:
        return []
    idxs = [len(rows) - 1]
    if since_ts is not None:
        idxs = [i for i in range(max(0, len(rows) - MAX_BACKFILL_BARS), len(rows)) if int(rows[i]["timestamp"]) > int(since_ts)]
    out: list[dict[str, Any]] = []
    for i in idxs:
        out.extend(_findings_at(symbol, tf, rows[:i + 1], as_of_ms=as_of_ms, cfg=cfg, data_source=data_source,
                                htf_trend=htf_trend, levels=levels))
    return out


def _findings_at(symbol: str, tf: str, rows: list[dict[str, Any]], *, as_of_ms: int, cfg: CandleContextConfig,
                 data_source: dict[str, Any] | None, htf_trend: dict[str, Any] | None,
                 levels: dict[str, Any] | None) -> list[dict[str, Any]]:
    """`rows`un SON barındaki şekiller (o barın kapanışında bilinebilen her şey)."""
    m = candle_metrics(rows[-1])
    if m.get("data_quality") != "OK":
        return []
    shapes = [s for s in _shapes(rows, cfg) if s != NO_PATTERN]
    if not shapes:
        return []
    atr = atr14(rows)
    trend = detect_trend(rows[:-1], atr=atr, cfg=cfg)
    last = rows[-1]
    bar_ts = int(last["timestamp"])
    step = tf_ms(tf)
    used = [int(b["timestamp"]) for b in rows[-3:]]
    lv = levels or {}
    near = _nearest_zone(lv.get("zones") or [], float(last["close"]), atr)
    out = []
    for shape in shapes:
        side = shape_side(shape)
        out.append({
            "version": FINDING_VERSION, "policy_version": cfg.policy_version, "finding_id": finding_id(symbol, tf, shape, bar_ts, cfg.policy_version),
            "symbol": symbol, "market": "USDM_PERP", "tf": tf, "shape": shape, "family": _family_of(shape), "side": side, "neutral": shape in NEUTRAL_SHAPES,
            "bar_ts": bar_ts, "bars_used": used, "n_bars_used": min(3, len(rows)),
            "metrics": {k: m.get(k) for k in ("open", "high", "low", "close", "body_to_range_ratio", "upper_wick_to_range_ratio", "lower_wick_to_range_ratio", "close_location_value", "bullish_body", "bearish_body")},
            "atr": atr, "preceding_trend": trend.get("trend"), "trend_detail": {k: trend.get(k) for k in ("slope_atr", "n_bars", "reason")},
            "htf_trend": dict(htf_trend or {}), "nearest_zone": near,
            "pattern_high": float(max(b["high"] for b in rows[-_bars_of(shape):])), "pattern_low": float(min(b["low"] for b in rows[-_bars_of(shape):])),
            "seen_at_ms": bar_ts + step, "seen_at": iso_ms(bar_ts + step), "earliest_confirm_at_ms": bar_ts + 2 * step,
            "confirm_rule": "sonraki %d kapalı %s barının kapanışı şeklin tarafıyla tutarlı (BULL: > şekil kapanışı; BEAR: < şekil kapanışı)" % (cfg.confirm_bars, tf),
            "invalidation_rule": "kapanış şeklin karşı ucunu aşarsa BOZULDU (LONG tarafı: < pattern_low; SHORT tarafı: > pattern_high)",
            "status": ST_FOUND, "confirmation": {"state": "UNKNOWN", "reason": "NO_CLOSED_BAR_AFTER_PATTERN"}, "confirmed_at_ms": None,
            "broken_at_ms": None, "expires_after_bars": MAX_AGE_BARS, "data_source": dict(data_source or {}), "as_of_ms": int(as_of_ms),
        })
    return out


def _bars_of(shape: str) -> int:
    if shape.startswith("THREE_") or shape in ("MORNING_STAR_LIKE", "EVENING_STAR_LIKE", "MORNING_DOJI_STAR_LIKE", "EVENING_DOJI_STAR_LIKE",
                                              "BULLISH_ABANDONED_BABY_LIKE", "BEARISH_ABANDONED_BABY_LIKE", "TRI_STAR_LIKE"):
        return 3
    if shape in ("DOJI_LIKE", "HAMMER_LIKE", "INVERTED_HAMMER_LIKE", "MARUBOZU_LIKE", "SPINNING_TOP_LIKE", "BULLISH_BELT_HOLD_LIKE", "BEARISH_BELT_HOLD_LIKE"):
        return 1
    return 2


def _family_of(shape: str) -> str:
    s = shape.replace("_LIKE", "")
    for key, fam in (("ENGULFING", "engulfing"), ("HAMMER", "hammer_family"), ("STAR", "stars"), ("SOLDIERS", "soldiers_crows"), ("CROWS", "soldiers_crows"),
                     ("HARAMI", "harami"), ("TWEEZER", "tweezer"), ("PIERCING", "piercing_dark_cloud"), ("DARK_CLOUD", "piercing_dark_cloud"),
                     ("BELT_HOLD", "belt_hold"), ("KICKER", "kicker"), ("MEETING", "meeting_lines"), ("PIGEON", "inside_pairs"), ("HAWK", "inside_pairs"),
                     ("DOJI", "doji"), ("MARUBOZU", "marubozu"), ("SPINNING", "spinning_top")):
        if key in s:
            return fam
    return "other"


def _nearest_zone(zones: list[dict[str, Any]], price: float, atr: float | None) -> dict[str, Any] | None:
    best = None
    for z in zones:
        d = min(abs(price - float(z["lower"])), abs(price - float(z["upper"]))) if not (float(z["lower"]) <= price <= float(z["upper"])) else 0.0
        if best is None or d < best["distance"]:
            best = {"level": z["level"], "lower": z["lower"], "upper": z["upper"], "n": z["n"], "distance": d,
                    "distance_atr": (d / atr) if atr else None, "confirmed_at_ms": z.get("confirmed_at_ms")}
    return best


def update_finding(finding: dict[str, Any], bars: list[dict[str, Any]], *, as_of_ms: int, cfg: CandleContextConfig | None = None) -> dict[str, Any]:
    """Bulgunun durumunu SONRAKİ kapalı barlarla güncelle (idempotent). Kapanmamış bar okunmaz."""
    cfg = cfg or CandleContextConfig()
    f = dict(finding)
    if f.get("status") in (ST_BROKEN, ST_EXPIRED):
        return f
    step = tf_ms(f["tf"])
    after = [b for b in bars if int(b["timestamp"]) > int(f["bar_ts"]) and int(b["timestamp"]) + step <= int(as_of_ms)]
    if not after:
        f["status"] = ST_FOUND
        return f
    side = f.get("side")
    # bozulma: kapanış geçersizlik seviyesini aştı (kapanış esaslı; fitil değil)
    for b in after:
        c = float(b["close"])
        if side == "LONG" and c < float(f["pattern_low"]):
            f.update(status=ST_BROKEN, broken_at_ms=int(b["timestamp"]) + step, broken_reason="CLOSE_BELOW_PATTERN_LOW")
            return f
        if side == "SHORT" and c > float(f["pattern_high"]):
            f.update(status=ST_BROKEN, broken_at_ms=int(b["timestamp"]) + step, broken_reason="CLOSE_ABOVE_PATTERN_HIGH")
            return f
    conf = evaluate_confirmation([f["shape"]], after, float((f.get("metrics") or {}).get("close") or 0) or None, cfg)
    f["confirmation"] = conf
    if conf["state"] == CONFIRMED:
        f["status"] = ST_CONFIRMED
        f["confirmed_at_ms"] = int(after[cfg.confirm_bars - 1]["timestamp"]) + step
    elif conf["state"] == UNCONFIRMED:
        f["status"] = ST_UNCONFIRMED
    else:
        f["status"] = ST_AWAITING if len(after) < cfg.confirm_bars else ST_FOUND
    if len(after) >= int(f.get("expires_after_bars") or MAX_AGE_BARS) and f["status"] not in (ST_CONFIRMED,):
        f["status"] = ST_EXPIRED
        f["expired_at_ms"] = int(after[-1]["timestamp"]) + step
    return f


__all__ = ["FINDING_VERSION", "MAX_BACKFILL_BARS", "ST_FOUND", "ST_AWAITING", "ST_CONFIRMED", "ST_UNCONFIRMED", "ST_BROKEN", "ST_EXPIRED", "MAX_AGE_BARS",
           "NEUTRAL_SHAPES", "atr14", "shape_side", "finding_id", "levels_for", "detect_findings", "update_finding"]
