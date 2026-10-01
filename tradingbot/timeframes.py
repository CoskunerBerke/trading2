# -*- coding: utf-8 -*-
"""ZAMAN DİLİMLERİ — desteklenen dilimler ve süreleri için TEK kaynak (CHART ANALYSIS V1 onarımı, bulgu #1).

Neden ayrı, bağımlılıksız modül: `candle_confirmation.closed_bars` (mum kapısı + replay), `chart_analysis`
(analiz anı), `dashboard.candles`/`dashboard.app` (panel) ve `engine_v3` (kayıt kancası) aynı tabloyu
okumalı. 2e31926'da mum kapısının tablosu yalnız 4h/1d içeriyordu; panel 15m/1h/1w kabul edip aynı
fonksiyona gidince `KeyError` ile çöküyordu.

Kapanmış bar sözleşmesi (tüm tüketiciler için AYNI): `timestamp` barın AÇILIŞ zamanıdır; bar
`timestamp + tf_ms` anında kapanır ve ancak o anda (eşitlik dahil) kapanmış sayılır — bir milisaniye
önce değil. Bilinmeyen dilim SESSİZCE 4h kabul edilmez: `tf_ms` ValueError verir; panel 400 döner.
"""
from __future__ import annotations

from typing import Any

#: BOX THEORY V15: "5m" eklendi — kural gün içi 5 dakikalık barda tetikleniyor ve tablo TEK kaynak
#: olduğu için mum kapısı, replay, panel ve motor aynı anda öğrenir (ayrı tablo tutulmaz).
TF_MS: dict[str, int] = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}
SUPPORTED_TIMEFRAMES: tuple[str, ...] = tuple(TF_MS)          # kısa → uzun
DAY_MS = TF_MS["1d"]


def is_supported(tf: Any) -> bool:
    return str(tf) in TF_MS


def tf_ms(tf: Any) -> int:
    """Dilim süresi (ms). Bilinmeyen dilim: ValueError (sessiz varsayılan YOK)."""
    try:
        return TF_MS[str(tf)]
    except KeyError:
        raise ValueError("desteklenmeyen zaman dilimi: %r (geçerli: %s)" % (tf, ", ".join(SUPPORTED_TIMEFRAMES))) from None


def bar_close_ms(open_ms: Any, tf: Any) -> int:
    """Barın kapanış anı = bilginin ilk bilinebildiği an (`timestamp` açılıştır)."""
    return int(open_ms) + tf_ms(tf)


def is_closed(open_ms: Any, tf: Any, *, now_ms: Any) -> bool:
    """`now_ms` anında bar kapanmış mı: açılış + süre <= şimdi (eşitlik dahil; 1 ms önce değil)."""
    return bar_close_ms(open_ms, tf) <= int(now_ms)


__all__ = ["DAY_MS", "SUPPORTED_TIMEFRAMES", "TF_MS", "bar_close_ms", "is_closed", "is_supported", "tf_ms"]
