"""Bağlamsal mum biçimi özellikleri (`candle_context_v1`) — YÖN İDDİASI ÜRETMEZ.

Bu modül mum **şekli** ölçer; alım/satım **etiketi** üretmez. Üretilen her alan
`directional_claim = "NONE"` taşır ve şekil daima bağlamıyla birlikte raporlanır:
önceki trend, konum, haftalık seviye yakınlığı ve teyit durumu.

**Neden bu kadar ısrarlı:**

* *Hammer* ile *Hanging Man* esasen AYNI ŞEKİLDİR. Farkı yaratan tek şey önceki trend ve
  konumdur; şeklin kendisi bir yön söylemez.
* *Inverted Hammer* ile *Shooting Star* için de aynısı geçerlidir.
* *Doji* denge/kararsızlıktır; garanti bir dönüş DEĞİLDİR.
* Doğru ad **Three White Soldiers**'tır; "Three White Crows" kanonik bir boğa formasyonu adı
  değildir (*Three Black Crows* ayı tarafındaki addır).

Bu yüzden çıktı şu biçimdedir:

    pattern_shape=HAMMER_LIKE, trend_context=DOWNTREND,
    level_context=NEAR_PREVIOUS_WEEK_LOW, confirmation=UNCONFIRMED,
    directional_claim=NONE

Formasyon verisi bir challenger kararını GÜÇLENDİREBİLİR ya da ZAYIFLATABİLİR; tek başına
sinyal ÜRETEMEZ (bkz. `entry_challenger_v2`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from typing import Any

from ..core import stable_id

SCHEMA_VERSION = "candle_context_v1"

# --- şekiller (YÖN DEĞİL) ----------------------------------------------------------------
DOJI_LIKE = "DOJI_LIKE"
HAMMER_LIKE = "HAMMER_LIKE"                     # = HANGING_MAN_LIKE şekli; bağlam ayırır
INVERTED_HAMMER_LIKE = "INVERTED_HAMMER_LIKE"   # = SHOOTING_STAR_LIKE şekli; bağlam ayırır
MARUBOZU_LIKE = "MARUBOZU_LIKE"
SPINNING_TOP_LIKE = "SPINNING_TOP_LIKE"
BULLISH_ENGULFING_LIKE = "BULLISH_ENGULFING_LIKE"
BEARISH_ENGULFING_LIKE = "BEARISH_ENGULFING_LIKE"
MORNING_STAR_LIKE = "MORNING_STAR_LIKE"
EVENING_STAR_LIKE = "EVENING_STAR_LIKE"
THREE_WHITE_SOLDIERS_LIKE = "THREE_WHITE_SOLDIERS_LIKE"
THREE_BLACK_CROWS_LIKE = "THREE_BLACK_CROWS_LIKE"
NO_PATTERN = "NO_PATTERN"

#: Aynı şeklin bağlama göre aldığı GELENEKSEL adlar. Ad bir yön iddiası DEĞİLDİR.
CONTEXTUAL_ALIASES = {
    HAMMER_LIKE: {"DOWNTREND": "HAMMER", "UPTREND": "HANGING_MAN"},
    INVERTED_HAMMER_LIKE: {"DOWNTREND": "INVERTED_HAMMER", "UPTREND": "SHOOTING_STAR"},
}

# --- bağlam ------------------------------------------------------------------------------
TREND_UP = "UPTREND"
TREND_DOWN = "DOWNTREND"
TREND_RANGE = "RANGE"
TREND_UNKNOWN = "UNKNOWN"

LOC_NEAR_WEEK_HIGH = "NEAR_PREVIOUS_WEEK_HIGH"
LOC_NEAR_WEEK_LOW = "NEAR_PREVIOUS_WEEK_LOW"
LOC_INSIDE_WEEK_RANGE = "INSIDE_PREVIOUS_WEEK_RANGE"
LOC_ABOVE_WEEK_RANGE = "ABOVE_PREVIOUS_WEEK_RANGE"
LOC_BELOW_WEEK_RANGE = "BELOW_PREVIOUS_WEEK_RANGE"
LOC_UNKNOWN = "UNKNOWN"

CONFIRMED = "CONFIRMED"
UNCONFIRMED = "UNCONFIRMED"
CONFIRMATION_UNKNOWN = "UNKNOWN"

DIRECTIONAL_CLAIM_NONE = "NONE"


@dataclass
class CandleContextConfig:
    """Versiyonlu şekil eşikleri. Tek bir örneğe uydurulmamıştır; oranlar geometriktir."""
    policy_version: str = "candle_v1.0.0"
    #: Gövde/aralık oranı bunun altındaysa doji benzeri (denge).
    doji_body_ratio: float = 0.10
    #: Çekiç benzeri: alt fitil >= bu oran ve üst fitil <= `hammer_opposite_wick_max`.
    hammer_wick_ratio: float = 0.55
    hammer_opposite_wick_max: float = 0.20
    #: Marubozu: gövde/aralık bunun üstünde.
    marubozu_body_ratio: float = 0.85
    #: Topaç: gövde küçük ama doji değil, iki fitil de belirgin.
    spinning_top_body_max: float = 0.35
    spinning_top_min_wick: float = 0.25
    #: Yutan formasyon: gövde önceki gövdeyi bu oranda aşmalı.
    engulf_min_ratio: float = 1.0
    #: Trend tespiti için bakılacak KAPANMIŞ bar sayısı ve asgari eğim (ATR katı).
    trend_lookback_bars: int = 10
    trend_min_slope_atr: float = 0.5
    #: Haftalık seviyeye "yakın" sayılma mesafesi (ATR katı).
    near_level_atr: float = 0.5
    #: Teyit: formasyondan sonra bu kadar KAPANMIŞ bar yönü korumalı.
    confirm_bars: int = 1

    def validate(self) -> None:
        if not (0.0 < self.doji_body_ratio < 0.5):
            raise ValueError("doji_body_ratio (0, 0.5) aralığında olmalı")
        if not (0.0 < self.hammer_wick_ratio < 1.0):
            raise ValueError("hammer_wick_ratio (0, 1) aralığında olmalı")
        if not (0.0 < self.marubozu_body_ratio < 1.0):
            raise ValueError("marubozu_body_ratio (0, 1) aralığında olmalı")
        if self.engulf_min_ratio <= 0 or self.trend_lookback_bars < 2:
            raise ValueError("engulf_min_ratio > 0 ve trend_lookback_bars >= 2 olmalı")
        if self.confirm_bars < 0 or self.near_level_atr <= 0:
            raise ValueError("confirm_bars >= 0 ve near_level_atr > 0 olmalı")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "CandleContextConfig":
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in dict(d or {}).items() if k in allowed})
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        return stable_id("candlecfg", self.policy_version, self.to_dict())


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def candle_metrics(bar: dict[str, Any]) -> dict[str, Any]:
    """Tek mumun geometrisi. Ölçülemeyen alan `None` kalır — sıfıra düşürülmez."""
    o, h, l, c = (_f(bar.get("open")), _f(bar.get("high")),
                  _f(bar.get("low")), _f(bar.get("close")))
    out: dict[str, Any] = {"open": o, "high": h, "low": l, "close": c,
                           "body_size": None, "full_range": None, "body_to_range_ratio": None,
                           "upper_wick_to_range_ratio": None, "lower_wick_to_range_ratio": None,
                           "close_location_value": None, "bullish_body": None,
                           "bearish_body": None, "data_quality": "UNAVAILABLE"}
    if None in (o, h, l, c) or h < l:
        out["reason"] = "MISSING_OR_INCONSISTENT_OHLC"
        return out
    rng = h - l
    body = abs(c - o)
    out.update({"body_size": round(body, 10), "full_range": round(rng, 10),
                "bullish_body": bool(c > o), "bearish_body": bool(c < o),
                "data_quality": "OK"})
    if rng <= 0:
        # Tek fiyatlı bar: oran tanımsızdır. `0` yazmak "gövdesiz" demek olurdu; UNKNOWN kalır.
        out["reason"] = "ZERO_RANGE_BAR"
        return out
    body_hi, body_lo = max(o, c), min(o, c)
    out.update({
        "body_to_range_ratio": round(body / rng, 6),
        "upper_wick_to_range_ratio": round((h - body_hi) / rng, 6),
        "lower_wick_to_range_ratio": round((body_lo - l) / rng, 6),
        "close_location_value": round(((c - l) - (h - c)) / rng, 6),
    })
    return out


def detect_trend(bars: list[dict[str, Any]], *, atr: float | None,
                 cfg: CandleContextConfig) -> dict[str, Any]:
    """Önceki trend — formasyondan ÖNCEKİ kapanmış barlardan. Ölçülemezse `UNKNOWN`."""
    a = _f(atr)
    rows = [b for b in bars if _f(b.get("close")) is not None]
    n = min(len(rows), cfg.trend_lookback_bars)
    if n < 2 or a is None or a <= 0:
        return {"trend": TREND_UNKNOWN, "slope_atr": None, "n_bars": len(rows),
                "reason": "INSUFFICIENT_BARS_OR_ATR"}
    window = rows[-n:]
    first, last = _f(window[0]["close"]), _f(window[-1]["close"])
    slope = (last - first) / a
    if slope >= cfg.trend_min_slope_atr:
        t = TREND_UP
    elif slope <= -cfg.trend_min_slope_atr:
        t = TREND_DOWN
    else:
        t = TREND_RANGE
    return {"trend": t, "slope_atr": round(slope, 6), "n_bars": n,
            "threshold_atr": cfg.trend_min_slope_atr, "reason": None}


def locate_against_week(price: float | None, *, week_high: float | None,
                        week_low: float | None, atr: float | None,
                        cfg: CandleContextConfig) -> dict[str, Any]:
    """Fiyatın önceki hafta aralığına göre konumu. Ölçülemezse `UNKNOWN`."""
    p, hi, lo, a = _f(price), _f(week_high), _f(week_low), _f(atr)
    if p is None or hi is None or lo is None or a is None or a <= 0:
        return {"location": LOC_UNKNOWN, "distance_to_high_atr": None,
                "distance_to_low_atr": None, "reason": "MISSING_LEVEL_PRICE_OR_ATR"}
    d_hi, d_lo = (hi - p) / a, (p - lo) / a
    if p > hi:
        loc = LOC_ABOVE_WEEK_RANGE
    elif p < lo:
        loc = LOC_BELOW_WEEK_RANGE
    elif abs(d_hi) <= cfg.near_level_atr:
        loc = LOC_NEAR_WEEK_HIGH
    elif abs(d_lo) <= cfg.near_level_atr:
        loc = LOC_NEAR_WEEK_LOW
    else:
        loc = LOC_INSIDE_WEEK_RANGE
    return {"location": loc, "distance_to_high_atr": round(d_hi, 6),
            "distance_to_low_atr": round(d_lo, 6),
            "near_level_atr": cfg.near_level_atr, "reason": None}


def _shapes(bars: list[dict[str, Any]], cfg: CandleContextConfig) -> list[str]:
    """Son bar(lar)dan çıkarılan ŞEKİL etiketleri. Yön iddiası TAŞIMAZ."""
    if not bars:
        return []
    m = [candle_metrics(b) for b in bars[-3:]]
    cur = m[-1]
    out: list[str] = []
    br = cur.get("body_to_range_ratio")
    uw = cur.get("upper_wick_to_range_ratio")
    lw = cur.get("lower_wick_to_range_ratio")
    if br is None:
        return out
    if br <= cfg.doji_body_ratio:
        out.append(DOJI_LIKE)
    if br >= cfg.marubozu_body_ratio:
        out.append(MARUBOZU_LIKE)
    if (lw is not None and uw is not None and lw >= cfg.hammer_wick_ratio
            and uw <= cfg.hammer_opposite_wick_max):
        out.append(HAMMER_LIKE)
    if (lw is not None and uw is not None and uw >= cfg.hammer_wick_ratio
            and lw <= cfg.hammer_opposite_wick_max):
        out.append(INVERTED_HAMMER_LIKE)
    if (br <= cfg.spinning_top_body_max and DOJI_LIKE not in out
            and uw is not None and lw is not None
            and uw >= cfg.spinning_top_min_wick and lw >= cfg.spinning_top_min_wick):
        out.append(SPINNING_TOP_LIKE)
    # --- iki barlı: yutan ----------------------------------------------------------------
    if len(m) >= 2 and m[-2].get("body_size") is not None and cur.get("body_size") is not None:
        prev, c = m[-2], cur
        if prev["body_size"] > 0 and c["body_size"] >= cfg.engulf_min_ratio * prev["body_size"]:
            p_hi, p_lo = max(prev["open"], prev["close"]), min(prev["open"], prev["close"])
            c_hi, c_lo = max(c["open"], c["close"]), min(c["open"], c["close"])
            if c["bullish_body"] and prev["bearish_body"] and c_lo <= p_lo and c_hi >= p_hi:
                out.append(BULLISH_ENGULFING_LIKE)
            if c["bearish_body"] and prev["bullish_body"] and c_lo <= p_lo and c_hi >= p_hi:
                out.append(BEARISH_ENGULFING_LIKE)
    # --- üç barlı: yıldız / asker / karga -------------------------------------------------
    if len(m) >= 3 and all(x.get("body_to_range_ratio") is not None for x in m):
        a3, b3, c3 = m
        small_mid = b3["body_to_range_ratio"] <= cfg.spinning_top_body_max
        if a3["bearish_body"] and small_mid and c3["bullish_body"] \
                and c3["close"] > (a3["open"] + a3["close"]) / 2.0:
            out.append(MORNING_STAR_LIKE)
        if a3["bullish_body"] and small_mid and c3["bearish_body"] \
                and c3["close"] < (a3["open"] + a3["close"]) / 2.0:
            out.append(EVENING_STAR_LIKE)
        if all(x["bullish_body"] for x in m) and c3["close"] > b3["close"] > a3["close"]:
            # DOĞRU AD: Three White Soldiers. "Three White Crows" kanonik bir ad DEĞİLDİR.
            out.append(THREE_WHITE_SOLDIERS_LIKE)
        if all(x["bearish_body"] for x in m) and c3["close"] < b3["close"] < a3["close"]:
            out.append(THREE_BLACK_CROWS_LIKE)
    return out


#: Şeklin hangi tarafla GEOMETRİK OLARAK tutarlı olduğunu yalnız TEYİT yönü için tanımlar.
#: Bu bir yön İDDİASI değildir: "sonraki kapanış hangi tarafta olursa şekil kendi geometrisiyle
#: tutarlı olur" sorusunun yanıtıdır; bir alım/satım talimatı DEĞİLDİR.
_BULL_SIDE = frozenset({HAMMER_LIKE, INVERTED_HAMMER_LIKE, BULLISH_ENGULFING_LIKE,
                        MORNING_STAR_LIKE, THREE_WHITE_SOLDIERS_LIKE})
_BEAR_SIDE = frozenset({BEARISH_ENGULFING_LIKE, EVENING_STAR_LIKE, THREE_BLACK_CROWS_LIKE})


def evaluate_confirmation(shapes: list[str], after: list[dict[str, Any]],
                          pattern_close: float | None,
                          cfg: CandleContextConfig) -> dict[str, Any]:
    """Formasyondan SONRA KAPANMIŞ barlarla teyit.

    Sonraki bar yoksa durum `UNKNOWN`'dır — "teyit edilmedi" ile "henüz bakılamadı" AYRI
    şeylerdir; ikisini karıştırmak, ölçülmemiş bir şeyi olumsuz ölçülmüş gibi göstermektir.
    """
    if cfg.confirm_bars <= 0:
        return {"state": CONFIRMATION_UNKNOWN, "reason": "CONFIRMATION_DISABLED",
                "bars_after": len(after)}
    if not shapes or shapes == [NO_PATTERN]:
        return {"state": CONFIRMATION_UNKNOWN, "reason": "NO_SHAPE", "bars_after": len(after)}
    if len(after) < cfg.confirm_bars or pattern_close is None:
        return {"state": CONFIRMATION_UNKNOWN, "reason": "NO_CLOSED_BAR_AFTER_PATTERN",
                "bars_after": len(after)}
    bull = bool(_BULL_SIDE & set(shapes))
    bear = bool(_BEAR_SIDE & set(shapes))
    if bull == bear:
        # Tek taraflı olmayan şekil (ör. yalnız DOJI): teyit YÖNÜ TANIMSIZ, uydurulmaz.
        return {"state": CONFIRMATION_UNKNOWN, "reason": "SHAPE_HAS_NO_SINGLE_SIDE",
                "bars_after": len(after)}
    window = after[:cfg.confirm_bars]
    closes = [_f(b.get("close")) for b in window]
    if any(c is None for c in closes):
        return {"state": CONFIRMATION_UNKNOWN, "reason": "MISSING_CLOSE_AFTER_PATTERN",
                "bars_after": len(after)}
    ok = all(c > pattern_close for c in closes) if bull else all(c < pattern_close
                                                                 for c in closes)
    return {"state": CONFIRMED if ok else UNCONFIRMED,
            "reason": ("CLOSES_CONSISTENT_WITH_SHAPE" if ok
                       else "CLOSES_NOT_CONSISTENT_WITH_SHAPE"),
            "bars_after": len(after), "confirm_bars": cfg.confirm_bars,
            "side": ("BULL_SIDE" if bull else "BEAR_SIDE"),
            "pattern_close": pattern_close}


def build_candle_context(*, bars: list[dict[str, Any]], atr: float | None = None,
                         week_high: float | None = None, week_low: float | None = None,
                         current_price: float | None = None,
                         cfg: CandleContextConfig | None = None) -> dict[str, Any]:
    """Bağlamsal mum kaydı. **Hiçbir alan AL/SAT etiketi değildir.**

    `bars`: kronolojik, YALNIZ KAPANMIŞ barlar. Son bar formasyon barıdır.
    """
    cfg = cfg or CandleContextConfig()
    rows = [b for b in (bars or []) if isinstance(b, dict)]
    metrics = candle_metrics(rows[-1]) if rows else {"data_quality": "UNAVAILABLE",
                                                     "reason": "NO_BARS"}
    trend = detect_trend(rows[:-1] if len(rows) > 1 else [], atr=atr, cfg=cfg)
    px = _f(current_price) if current_price is not None else metrics.get("close")
    loc = locate_against_week(px, week_high=week_high, week_low=week_low, atr=atr, cfg=cfg)
    shapes = _shapes(rows, cfg) if metrics.get("data_quality") == "OK" else []
    # SON bar formasyonu karar anında henüz teyit EDİLEMEZ (sonrasında kapanmış bar yok).
    latest_conf = evaluate_confirmation(shapes, [], metrics.get("close"), cfg)
    # TEYİT EDİLEBİLİR formasyon: `confirm_bars` kadar geride oluşmuş şekil, sonraki KAPANMIŞ
    # barlarla sınanır. Böylece `CONFIRMED/UNCONFIRMED` alanı vacuous olmaz.
    k = max(1, cfg.confirm_bars)
    confirmed_shapes: list[str] = []
    conf = {"state": CONFIRMATION_UNKNOWN, "reason": "NOT_ENOUGH_BARS", "bars_after": 0}
    if len(rows) > k:
        head, tail = rows[:-k], rows[-k:]
        confirmed_shapes = _shapes(head, cfg)
        conf = evaluate_confirmation(confirmed_shapes, tail,
                                     _f(head[-1].get("close")) if head else None, cfg)

    aliases: dict[str, str] = {}
    for shape, table in CONTEXTUAL_ALIASES.items():
        if shape in shapes:
            aliases[shape] = table.get(trend["trend"], "CONTEXT_UNDETERMINED")

    return {
        "schema_version": SCHEMA_VERSION,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "metrics": metrics,
        "pattern_shapes": shapes or [NO_PATTERN],
        "contextual_aliases": aliases,
        "trend_context": trend["trend"],
        "preceding_trend": trend,
        "location_context": loc["location"],
        "weekly_level_context": loc,
        "confirmation_state": conf["state"],
        "confirmation": conf,
        "latest_bar_confirmation": latest_conf,
        "confirmed_pattern_shapes": confirmed_shapes or [NO_PATTERN],
        "fully_closed_bar_count": len(rows),
        "data_quality": metrics.get("data_quality", "UNAVAILABLE"),
        "reason": metrics.get("reason"),
        # SÖZLEŞME: bu kayıt hiçbir koşulda yön iddiası taşımaz.
        "directional_claim": DIRECTIONAL_CLAIM_NONE,
        "note_tr": ("Şekil bağlamsaldır: Hammer ile Hanging Man aynı şekildir, farkı önceki "
                    "trend ve konum yaratır. Doji dengedir, dönüş garantisi değildir. "
                    "Bu kayıt AL/SAT talimatı ÜRETMEZ."),
    }


__all__ = ["SCHEMA_VERSION", "DOJI_LIKE", "HAMMER_LIKE", "INVERTED_HAMMER_LIKE",
           "MARUBOZU_LIKE", "SPINNING_TOP_LIKE", "BULLISH_ENGULFING_LIKE",
           "BEARISH_ENGULFING_LIKE", "MORNING_STAR_LIKE", "EVENING_STAR_LIKE",
           "THREE_WHITE_SOLDIERS_LIKE", "THREE_BLACK_CROWS_LIKE", "NO_PATTERN",
           "CONTEXTUAL_ALIASES", "TREND_UP", "TREND_DOWN", "TREND_RANGE", "TREND_UNKNOWN",
           "LOC_NEAR_WEEK_HIGH", "LOC_NEAR_WEEK_LOW", "LOC_INSIDE_WEEK_RANGE",
           "LOC_ABOVE_WEEK_RANGE", "LOC_BELOW_WEEK_RANGE", "LOC_UNKNOWN",
           "CONFIRMED", "UNCONFIRMED", "CONFIRMATION_UNKNOWN", "DIRECTIONAL_CLAIM_NONE",
           "CandleContextConfig", "candle_metrics", "detect_trend", "locate_against_week",
           "evaluate_confirmation",
           "build_candle_context"]
