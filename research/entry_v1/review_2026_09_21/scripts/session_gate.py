# -*- coding: utf-8 -*-
"""SEANS KAPISI — karar ANININ saati/gunu ile giris izni (V6, 2026-09-12). SAF, tek kaynak.

Kripto vadeli sozlesmeleri hic kapanmaz; bu kapi "borsa kapali" demek DEGILDIR. Sinadigi
hipotez: geleneksel piyasa seanslari (ABD 12-20 UTC) ve hafta sonu, kripto likiditesini ve
dolayisiyla bu sistemin giris kalitesini degistiriyor mu?

Varyantlar:
  s1_us_session   yalniz karar saati 12:00-19:59 UTC iken giris (4h barda 12:00 ve 16:00 kapanislari)
  s2_no_weekend   Cumartesi/Pazar (UTC) giris yok
  s3_both         ikisi birlikte

`decision_ms`: kararin verildigi an (UTC, ms) = kapanmis barin kapanis zamani.
"""
from __future__ import annotations

from datetime import datetime, timezone

VARIANTS = ("s1_us_session", "s2_no_weekend", "s3_both")
US_SESSION_UTC = (12, 20)          # [baslangic, bitis) saat


def _dt(decision_ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(decision_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def in_us_session(decision_ms) -> bool | None:
    d = _dt(decision_ms)
    if d is None:
        return None
    return US_SESSION_UTC[0] <= d.hour < US_SESSION_UTC[1]


def is_weekend(decision_ms) -> bool | None:
    d = _dt(decision_ms)
    if d is None:
        return None
    return d.weekday() >= 5


def evaluate_variant(variant: str, *, decision_ms) -> tuple[bool, str]:
    """Doner: (gecti, gerekce). Zaman okunamazsa FAIL-CLOSED (giris yok, sebep kayitli)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen seans varyanti: %r" % (variant,))
    tag = variant.split("_", 1)[0].upper()
    us, we = in_us_session(decision_ms), is_weekend(decision_ms)
    if us is None or we is None:
        return False, tag + "_NO_TIME"
    if variant in ("s1_us_session", "s3_both") and not us:
        return False, tag + "_OUTSIDE_US_SESSION"
    if variant in ("s2_no_weekend", "s3_both") and we:
        return False, tag + "_WEEKEND"
    return True, ""


__all__ = ["US_SESSION_UTC", "VARIANTS", "evaluate_variant", "in_us_session", "is_weekend"]
