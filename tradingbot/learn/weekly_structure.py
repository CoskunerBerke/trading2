"""Haftalık piyasa yapısı snapshot'ı ve süpürme/geri alma sınıflandırıcısı
(`weekly_market_structure_v1`) — POINT-IN-TIME, SHADOW, karar UYGULAMAZ.

İki iş yapar:

1. **Faz 1 — `build_weekly_structure`**: karar anında görülebilen bilgiyle ÖNCEKİ TAMAMLANMIŞ
   haftanın yüksek/düşük/açılış/kapanış/aralığını çıkarır. Mevcut (tamamlanmamış) hafta,
   kapanmamış mum ve gelecekteki hiçbir bar KULLANILMAZ.
2. **Faz 2 — `classify_level_interaction`**: fiyatın o seviyelerle etkileşimini deterministik
   olarak sınıflandırır. **Seviyenin ötesindeki bir fitil kendiliğinden süpürme SİNYALİ
   DEĞİLDİR**; geri alma ayrı ve açıkça ölçülür.

**Hafta sınırı deterministiktir.** Sürekli işlem gören kripto için kanonik ISO/UTC haftası
(Pazartesi 00:00 UTC → Pazartesi 00:00 UTC) kullanılır. Seans temelli enstrümanlar için
sınır VARSAYILMAZ: haftalık bar sayısı BESLEMEDEN ÖLÇÜLÜR (`infer_session_profile`) ve
güvenilir biçimde yeniden kurulamıyorsa alanlar `UNKNOWN` kalır.

Bu modül bir gösterge kopyası değildir: tek bir sosyal medya örneği evrensel doğru sayılmaz,
eşikler ATR/tick cinsinden yapılandırılabilir ve KAPANMIŞ bar üzerinden ölçülür.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from ..core import stable_id

SCHEMA_VERSION = "weekly_market_structure_v1"

# --- veri kalitesi ---------------------------------------------------------------------
DQ_OK = "OK"
DQ_PARTIAL = "PARTIAL"
DQ_UNAVAILABLE = "UNAVAILABLE"

# --- seans profilleri (ÖLÇÜLÜR, varsayılmaz) --------------------------------------------
SESSION_CRYPTO_CONTINUOUS = "CRYPTO_CONTINUOUS_ISO_UTC"   # 7 gün/hafta
SESSION_WEEKDAY = "SESSION_WEEKDAY_ISO_UTC"               # 5 gün/hafta (hafta içi)
SESSION_UNKNOWN = "UNKNOWN"

# --- etkileşim sınıfları ----------------------------------------------------------------
NO_INTERACTION = "NO_INTERACTION"
TOUCH_ONLY = "TOUCH_ONLY"
BREAKOUT_UNCONFIRMED = "BREAKOUT_UNCONFIRMED"
ACCEPTED_BREAKOUT = "ACCEPTED_BREAKOUT"
HIGH_SWEEP_RECLAIM = "HIGH_SWEEP_RECLAIM"
LOW_SWEEP_RECLAIM = "LOW_SWEEP_RECLAIM"
AMBIGUOUS = "AMBIGUOUS"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"

INTERACTION_CLASSES = (NO_INTERACTION, TOUCH_ONLY, BREAKOUT_UNCONFIRMED, ACCEPTED_BREAKOUT,
                       HIGH_SWEEP_RECLAIM, LOW_SWEEP_RECLAIM, AMBIGUOUS, DATA_UNAVAILABLE)

MS_DAY = 86_400_000


@dataclass
class WeeklyStructureConfig:
    """Versiyonlu eşikler — `config_hash`a girer, koda gömülü DEĞİLDİR."""
    policy_version: str = "weekly_v1.0.0"
    #: Seviyeye "değdi" sayılması için azami uzaklık (ATR katı). Tick tabanlı alt sınır ayrı.
    touch_tolerance_atr: float = 0.05
    #: Aşımın gürültüden ayrılması için asgari mesafe (ATR katı).
    min_sweep_atr: float = 0.10
    #: Aşımın "kabul edilmiş kırılım" sayılması için asgari mesafe (ATR katı).
    accepted_breakout_atr: float = 0.75
    #: Geri almanın onaylanması için gereken KAPANMIŞ bar sayısı.
    reclaim_confirm_bars: int = 1
    #: Geri alma bu kadar bar içinde olmazsa süpürme sayılmaz (kabul edilmiş kırılıma döner).
    max_bars_to_reclaim: int = 6
    #: Reddediş fitili: fitil / toplam aralık asgari oranı.
    min_rejection_wick_ratio: float = 0.35
    #: Haftalık yapı için gereken asgari tamamlanmış hafta sayısı.
    min_complete_weeks: int = 2
    #: Seans profili çıkarımı için bakılacak tamamlanmış hafta sayısı.
    session_probe_weeks: int = 6

    def validate(self) -> None:
        if self.touch_tolerance_atr < 0 or self.min_sweep_atr <= 0:
            raise ValueError("touch_tolerance_atr >= 0 ve min_sweep_atr > 0 olmalı")
        if self.accepted_breakout_atr <= self.min_sweep_atr:
            raise ValueError("accepted_breakout_atr, min_sweep_atr'den büyük olmalı")
        if self.reclaim_confirm_bars < 1 or self.max_bars_to_reclaim < 1:
            raise ValueError("bar sayıları >= 1 olmalı")
        if not (0.0 < self.min_rejection_wick_ratio < 1.0):
            raise ValueError("min_rejection_wick_ratio (0, 1) aralığında olmalı")
        if self.min_complete_weeks < 1 or self.session_probe_weeks < 1:
            raise ValueError("hafta sayıları >= 1 olmalı")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "WeeklyStructureConfig":
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in dict(d or {}).items() if k in allowed})
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        return stable_id("weeklycfg", self.policy_version, self.to_dict())


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _dt(ms: Any) -> datetime | None:
    v = _f(ms)
    if v is None:
        return None
    try:
        return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def iso_week_id(dt: datetime) -> str:
    """`2026-W36` — ISO 8601 hafta kimliği (Pazartesi başlangıçlı, UTC)."""
    y, w, _ = dt.astimezone(timezone.utc).isocalendar()
    return f"{y:04d}-W{w:02d}"


def week_start_utc(dt: datetime) -> datetime:
    """İçinde bulunulan ISO haftasının Pazartesi 00:00:00 UTC başlangıcı."""
    d = dt.astimezone(timezone.utc)
    monday = d - timedelta(days=d.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def rows_from_frame(frame: Any) -> list[dict[str, float]]:
    """DataFrame ya da dict listesini normalize eder. Eksik/bozuk satır ATLANIR, uydurulmaz."""
    if frame is None:
        return []
    recs: Iterable[Any]
    if hasattr(frame, "to_dict"):
        try:
            recs = frame.to_dict("records")
        except (TypeError, ValueError):
            return []
    elif isinstance(frame, Sequence):
        recs = frame
    else:
        return []
    out: list[dict[str, float]] = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        ts = _f(r.get("timestamp"))
        o, h, l, c = (_f(r.get("open")), _f(r.get("high")),
                      _f(r.get("low")), _f(r.get("close")))
        if ts is None or None in (o, h, l, c):
            continue
        if h < l:
            continue                      # tutarsız bar; sessizce düzeltilmez, atlanır
        out.append({"timestamp": ts, "open": o, "high": h, "low": l, "close": c})
    out.sort(key=lambda x: x["timestamp"])
    return out


def infer_session_profile(daily_rows: list[dict[str, float]], *,
                          probe_weeks: int = 6) -> dict[str, Any]:
    """Haftalık bar sayısını BESLEMEDEN ölçer; kripto varsayımı DAYATILMAZ.

    Sürekli piyasa 7 bar/hafta, hafta-içi seans 5 bar/hafta üretir. Ölçüm tutarsızsa profil
    `UNKNOWN` döner ve çağıran haftalık alanları `UNKNOWN` bırakır — yanlış bir sınır
    varsaymaktansa ölçemediğimizi söylemek doğrudur.
    """
    buckets: dict[str, set[int]] = {}
    for r in daily_rows:
        d = _dt(r["timestamp"])
        if d is None:
            continue
        buckets.setdefault(iso_week_id(d), set()).add(d.weekday())
    if not buckets:
        return {"profile": SESSION_UNKNOWN, "expected_bars_per_week": None,
                "observed_weeks": 0, "reason": "NO_DAILY_BARS"}
    weeks = sorted(buckets)[:-1][-max(1, int(probe_weeks)):]     # son (tamamlanmamış) hafta hariç
    counts = [len(buckets[w]) for w in weeks]
    if not counts:
        return {"profile": SESSION_UNKNOWN, "expected_bars_per_week": None,
                "observed_weeks": 0, "reason": "NO_COMPLETE_WEEK"}
    has_weekend = any(any(dw >= 5 for dw in buckets[w]) for w in weeks)
    mode = max(set(counts), key=counts.count)
    consistent = counts.count(mode) >= max(1, len(counts) - 1)
    if mode == 7 and has_weekend and consistent:
        prof, reason = SESSION_CRYPTO_CONTINUOUS, "7_BARS_PER_WEEK_WITH_WEEKEND"
    elif mode == 5 and not has_weekend and consistent:
        prof, reason = SESSION_WEEKDAY, "5_BARS_PER_WEEK_NO_WEEKEND"
    else:
        prof, reason = SESSION_UNKNOWN, f"IRREGULAR_BAR_COUNTS:{sorted(set(counts))}"
    return {"profile": prof, "expected_bars_per_week": (mode if prof != SESSION_UNKNOWN else None),
            "observed_weeks": len(weeks), "bar_counts": counts,
            "has_weekend_bars": has_weekend, "reason": reason}


def previous_completed_week(daily_rows: list[dict[str, float]], now: datetime, *,
                            cfg: WeeklyStructureConfig | None = None) -> dict[str, Any]:
    """ÖNCEKİ TAMAMLANMIŞ haftanın OHLC'si. Mevcut hafta ve gelecek barlar DIŞLANIR.

    Bir bar ancak `timestamp + 1 gün <= şimdi` ise hafta hesabına girer; kapanmamış günlük
    bar KULLANILMAZ. Aynı şekilde `şimdi`den sonraki hiçbir bar kabul edilmez.
    """
    cfg = cfg or WeeklyStructureConfig()
    now = now.astimezone(timezone.utc)
    now_ms = now.timestamp() * 1000.0
    cur_start = week_start_utc(now)
    cur_start_ms = cur_start.timestamp() * 1000.0

    usable: list[dict[str, float]] = []
    future = 0
    unclosed = 0
    for r in daily_rows:
        if r["timestamp"] > now_ms:
            future += 1
            continue
        if r["timestamp"] + MS_DAY > now_ms:
            unclosed += 1          # günlük bar henüz kapanmadı
            continue
        usable.append(r)

    sess = infer_session_profile(usable, probe_weeks=cfg.session_probe_weeks)
    prev_rows = [r for r in usable if r["timestamp"] < cur_start_ms]
    if not prev_rows:
        return {"available": False, "data_quality": DQ_UNAVAILABLE,
                "unavailable_reason": "NO_BARS_BEFORE_CURRENT_WEEK",
                "session": sess, "excluded_future_bars": future,
                "excluded_unclosed_bars": unclosed}

    by_week: dict[str, list[dict[str, float]]] = {}
    for r in prev_rows:
        d = _dt(r["timestamp"])
        if d is None:
            continue
        by_week.setdefault(iso_week_id(d), []).append(r)
    if not by_week:
        return {"available": False, "data_quality": DQ_UNAVAILABLE,
                "unavailable_reason": "NO_WEEK_BUCKETS", "session": sess,
                "excluded_future_bars": future, "excluded_unclosed_bars": unclosed}

    week_id = sorted(by_week)[-1]
    bars = sorted(by_week[week_id], key=lambda x: x["timestamp"])
    expected = sess.get("expected_bars_per_week")
    available = len(bars)
    # Boşluk: haftanın gün kümesindeki kopukluklar (yalnız sürekli piyasada anlamlı).
    days = sorted({(_dt(b["timestamp"]) or now).weekday() for b in bars})
    gaps = 0
    if sess["profile"] == SESSION_CRYPTO_CONTINUOUS:
        gaps = 7 - len(days)
    elif sess["profile"] == SESSION_WEEKDAY:
        gaps = max(0, 5 - len([d for d in days if d < 5]))

    if sess["profile"] == SESSION_UNKNOWN:
        dq, reason = DQ_UNAVAILABLE, f"SESSION_PROFILE_UNKNOWN:{sess.get('reason')}"
    elif expected and available < expected:
        dq, reason = DQ_PARTIAL, f"MISSING_BARS:{available}/{expected}"
    elif len(by_week) < cfg.min_complete_weeks:
        dq, reason = DQ_PARTIAL, f"FEW_COMPLETE_WEEKS:{len(by_week)}/{cfg.min_complete_weeks}"
    else:
        dq, reason = DQ_OK, None

    first, last = bars[0], bars[-1]
    hi = max(b["high"] for b in bars)
    lo = min(b["low"] for b in bars)
    return {
        "available": dq != DQ_UNAVAILABLE,
        "previous_week_id": week_id,
        "previous_completed_week_open": first["open"],
        "previous_completed_week_close": last["close"],
        "previous_completed_week_high": hi,
        "previous_completed_week_low": lo,
        "previous_completed_week_range": round(hi - lo, 10),
        "previous_completed_week_mid": round((hi + lo) / 2.0, 10),
        "source_timezone": "UTC",
        "source_session": sess["profile"],
        "source_first_timestamp_ms": int(first["timestamp"]),
        "source_last_timestamp_ms": int(last["timestamp"]),
        "expected_bars": expected,
        "available_bars": available,
        "gap_count": gaps,
        "data_quality": dq,
        "unavailable_reason": reason,
        "session": sess,
        "n_complete_weeks_seen": len(by_week),
        "excluded_future_bars": future,
        "excluded_unclosed_bars": unclosed,
    }


def _wick_ratios(bar: dict[str, float]) -> dict[str, float | None]:
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return {"upper": None, "lower": None, "body": None}
    body_hi = max(bar["open"], bar["close"])
    body_lo = min(bar["open"], bar["close"])
    return {"upper": (bar["high"] - body_hi) / rng,
            "lower": (body_lo - bar["low"]) / rng,
            "body": abs(bar["close"] - bar["open"]) / rng}


def classify_level_interaction(*, level: float | None, bars: list[dict[str, float]],
                               side: str, atr: float | None,
                               cfg: WeeklyStructureConfig,
                               tick: float | None = None) -> dict[str, Any]:
    """Bir seviye ile KAPANMIŞ barların etkileşimini deterministik sınıflandırır.

    `side="high"` üst seviye (aşım = yukarı), `side="low"` alt seviye (aşım = aşağı).
    Ölçülemeyen her şey `None` kalır ve sınıf `DATA_UNAVAILABLE` olur — sıfır sayılmaz.

    Sözleşme: **fitil tek başına süpürme sinyali değildir.** Aşım ölçülür, geri alma AYRI
    ölçülür; ikisi birlikte ve yapılandırılmış eşikleri geçerse `*_SWEEP_RECLAIM` denir.
    """
    lv = _f(level)
    a = _f(atr)
    up = str(side).lower() == "high"
    base = {
        "side": "high" if up else "low",
        "level": lv,
        "atr": a,
        "touched": None, "swept": None,
        "sweep_distance": None, "sweep_distance_atr": None,
        "close_returned": None, "bars_to_reclaim": None, "reclaim_confirmed": None,
        "rejection_wick_ratio": None, "follow_through_available": None,
        "n_bars_considered": len(bars),
        "classification": DATA_UNAVAILABLE,
        "confidence": None,
        "data_quality": DQ_UNAVAILABLE,
        "reason": None,
        "thresholds": {"touch_tolerance_atr": cfg.touch_tolerance_atr,
                       "min_sweep_atr": cfg.min_sweep_atr,
                       "accepted_breakout_atr": cfg.accepted_breakout_atr,
                       "reclaim_confirm_bars": cfg.reclaim_confirm_bars,
                       "max_bars_to_reclaim": cfg.max_bars_to_reclaim,
                       "min_rejection_wick_ratio": cfg.min_rejection_wick_ratio},
    }
    if lv is None:
        base["reason"] = "LEVEL_UNKNOWN"
        return base
    if not bars:
        base["reason"] = "NO_CLOSED_BARS_IN_WINDOW"
        return base
    if a is None or a <= 0:
        base["reason"] = "ATR_UNKNOWN"
        return base

    tol = cfg.touch_tolerance_atr * a
    tick_tol = abs(_f(tick) or 0.0)
    tol = max(tol, tick_tol)

    # --- aşım / değme (KAPANMIŞ barların uçlarıyla) --------------------------------------
    extremes = [b["high"] for b in bars] if up else [b["low"] for b in bars]
    best = max(extremes) if up else min(extremes)
    excess = (best - lv) if up else (lv - best)
    touched = excess >= -tol
    swept = excess >= cfg.min_sweep_atr * a
    base.update({"touched": bool(touched), "swept": bool(swept),
                 "sweep_distance": round(excess, 10),
                 "sweep_distance_atr": round(excess / a, 6),
                 "data_quality": DQ_OK})

    if not touched:
        base.update({"classification": NO_INTERACTION, "confidence": 1.0,
                     "reason": "PRICE_NEVER_REACHED_LEVEL"})
        return base
    if not swept:
        base.update({"classification": TOUCH_ONLY, "confidence": 0.9,
                     "reason": "TOUCHED_WITHOUT_MEANINGFUL_EXCESS"})
        return base

    # --- geri alma: aşımın OLDUĞU bardan sonraki kapanışlar ------------------------------
    idx = max(range(len(bars)), key=lambda i: (bars[i]["high"] if up else -bars[i]["low"]))
    pierce = bars[idx]
    wr = _wick_ratios(pierce)
    base["rejection_wick_ratio"] = (round(wr["upper"], 6) if up else
                                    (round(wr["lower"], 6) if wr["lower"] is not None else None)) \
        if wr["upper"] is not None else None
    after = bars[idx:]
    base["follow_through_available"] = len(after) - 1

    returned_at = None
    for j, b in enumerate(after):
        back = (b["close"] < lv) if up else (b["close"] > lv)
        if back:
            returned_at = j
            break
    base["close_returned"] = returned_at is not None
    if returned_at is not None:
        base["bars_to_reclaim"] = returned_at
        confirm = sum(1 for b in after[returned_at:returned_at + cfg.reclaim_confirm_bars]
                      if ((b["close"] < lv) if up else (b["close"] > lv)))
        base["reclaim_confirmed"] = bool(confirm >= cfg.reclaim_confirm_bars)

    accepted = excess >= cfg.accepted_breakout_atr * a
    if base["reclaim_confirmed"] and returned_at is not None \
            and returned_at <= cfg.max_bars_to_reclaim:
        # Kabul edilmiş bir kırılım geri alınmışsa bu YİNE süpürmedir; fakat aşım çok
        # büyükse ve geri alma geç geldiyse sınıf AMBIGUOUS olur (iki okuma da mümkündür).
        if accepted and returned_at > cfg.reclaim_confirm_bars:
            base.update({"classification": AMBIGUOUS, "confidence": 0.4,
                         "reason": "LARGE_EXCESS_WITH_LATE_RECLAIM"})
        else:
            base.update({"classification": HIGH_SWEEP_RECLAIM if up else LOW_SWEEP_RECLAIM,
                         "confidence": 0.75,
                         "reason": "SWEPT_AND_RECLAIMED_ON_CLOSED_BARS"})
        return base
    if accepted:
        base.update({"classification": ACCEPTED_BREAKOUT, "confidence": 0.7,
                     "reason": "EXCESS_BEYOND_ACCEPTANCE_WITHOUT_RECLAIM"})
        return base
    base.update({"classification": BREAKOUT_UNCONFIRMED, "confidence": 0.5,
                 "reason": "SWEPT_BUT_NOT_RECLAIMED_AND_NOT_ACCEPTED"})
    return base


def build_weekly_structure(*, symbol: Any, direction: Any, now: datetime,
                           daily_frame: Any = None, intraweek_frame: Any = None,
                           current_price: float | None = None, atr: float | None = None,
                           tick: float | None = None,
                           cfg: WeeklyStructureConfig | None = None) -> dict[str, Any]:
    """Karar anı haftalık yapı snapshot'ı. Saf fonksiyon: hiçbir yere yazmaz, sonucu GÖRMEZ."""
    cfg = cfg or WeeklyStructureConfig()
    now = now.astimezone(timezone.utc)
    daily = rows_from_frame(daily_frame)
    week = previous_completed_week(daily, now, cfg=cfg)
    px = _f(current_price)
    a = _f(atr)

    rec: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": cfg.policy_version,
        "config_id": cfg.config_id,
        "symbol": str(symbol) if symbol is not None else None,
        "direction": str(direction) if direction is not None else None,
        "as_of": now.isoformat(),
        "as_of_ms": int(now.timestamp() * 1000),
        "current_week_id": iso_week_id(now),
        "current_week_start_ms": int(week_start_utc(now).timestamp() * 1000),
        "day_of_week": now.weekday(),
        "current_price": px,
        "current_atr": a,
        "provenance": {"written_at_stage": "RANKING", "sees_outcome": False,
                       "uses_unclosed_bar": False, "uses_future_bar": False,
                       "note_tr": ("Yalnız KAPANMIŞ barlar ve TAMAMLANMIŞ önceki hafta. "
                                   "Mevcut haftanın nihai yüksek/düşüğü GÖRÜLMEZ.")},
    }
    for k in ("previous_week_id", "previous_completed_week_open", "previous_completed_week_close",
              "previous_completed_week_high", "previous_completed_week_low",
              "previous_completed_week_range", "previous_completed_week_mid",
              "source_timezone", "source_session", "source_first_timestamp_ms",
              "source_last_timestamp_ms", "expected_bars", "available_bars", "gap_count",
              "data_quality", "unavailable_reason", "excluded_future_bars",
              "excluded_unclosed_bars"):
        rec[k] = week.get(k)
    rec["session_profile"] = week.get("session")
    rec["week_available"] = bool(week.get("available"))

    hi, lo = rec.get("previous_completed_week_high"), rec.get("previous_completed_week_low")
    rng = rec.get("previous_completed_week_range")
    rec["distance_to_previous_week_high"] = (round(hi - px, 10)
                                             if (hi is not None and px is not None) else None)
    rec["distance_to_previous_week_low"] = (round(px - lo, 10)
                                            if (lo is not None and px is not None) else None)
    rec["distance_to_high_atr"] = (round((hi - px) / a, 6)
                                   if (hi is not None and px is not None and a) else None)
    rec["distance_to_low_atr"] = (round((px - lo) / a, 6)
                                  if (lo is not None and px is not None and a) else None)
    rec["position_inside_previous_week_range"] = (
        bool(lo <= px <= hi) if (hi is not None and lo is not None and px is not None) else None)
    rec["previous_week_range_position"] = (round((px - lo) / rng, 6)
                                           if (rng and px is not None and lo is not None)
                                           else None)

    # --- mevcut hafta içindeki KAPANMIŞ barlar ------------------------------------------
    intra = rows_from_frame(intraweek_frame)
    wk_ms = rec["current_week_start_ms"]
    now_ms = rec["as_of_ms"]
    intra_week = [b for b in intra if wk_ms <= b["timestamp"] <= now_ms]
    rec["bars_elapsed_in_current_week"] = len(intra_week)
    rec["intraweek_first_ts_ms"] = int(intra_week[0]["timestamp"]) if intra_week else None
    rec["intraweek_last_ts_ms"] = int(intra_week[-1]["timestamp"]) if intra_week else None

    rec["high_interaction"] = classify_level_interaction(
        level=hi, bars=intra_week, side="high", atr=a, cfg=cfg, tick=tick)
    rec["low_interaction"] = classify_level_interaction(
        level=lo, bars=intra_week, side="low", atr=a, cfg=cfg, tick=tick)

    missing = [k for k in ("previous_completed_week_high", "previous_completed_week_low",
                           "current_price", "current_atr") if rec.get(k) is None]
    rec["missing_fields"] = missing
    rec["n_missing"] = len(missing)
    return rec


__all__ = ["SCHEMA_VERSION", "DQ_OK", "DQ_PARTIAL", "DQ_UNAVAILABLE",
           "SESSION_CRYPTO_CONTINUOUS", "SESSION_WEEKDAY", "SESSION_UNKNOWN",
           "NO_INTERACTION", "TOUCH_ONLY", "BREAKOUT_UNCONFIRMED", "ACCEPTED_BREAKOUT",
           "HIGH_SWEEP_RECLAIM", "LOW_SWEEP_RECLAIM", "AMBIGUOUS", "DATA_UNAVAILABLE",
           "INTERACTION_CLASSES", "WeeklyStructureConfig", "iso_week_id", "week_start_utc",
           "rows_from_frame", "infer_session_profile", "previous_completed_week",
           "classify_level_interaction", "build_weekly_structure"]
