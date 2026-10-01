# -*- coding: utf-8 -*-
"""GIRIS TETIGI — canli motor ile replay'in ORTAK tek kaynagi.

Neden ayri modul: `engine_v3._trigger_fired` "planlanan seviyeye gelmeden GIRME" kuralini
uyguluyordu; `replay/engine` bu kontrolu HIC yapmiyor ve her adayi bar kapanisinda hemen
aciyordu. Yani "destege geri cekilmeyi bekle" davranisi uretimde VAR, backtest'te YOK'tu ve
beklemenin kar etkisi hic olculmedi.

Ayni kusur sinifi bu paketle bu oturumda dort kez yasandi (ekonomi kapisi, basa-bas
korumasi, borsa filtreleri, tetik). Bu yuzden mantik KOPYALANMIYOR, TEK yere tasiniyor.

Fonksiyon SAFTIR: `self` yok, dosya/ag yok, girdileri mutasyona ugratmaz.
"""
from __future__ import annotations

#: `pullback` / `market` girisinde fiyatin planlanan girise yaklasmasi gereken tolerans.
NEAR_TOLERANCE = 0.0025
#: `breakout` girisinde kovalama yasagi: fiyat seviyeden bu kadar uzaksa giris REDDEDILIR.
CHASE_LIMIT = 0.015


def trigger_fired(*, direction: str, entry_type: str, entry: float, price: float,
                  last_close: float | None = None, last_bar: str | None = None,
                  already_fired_bar: str | None = None) -> tuple[bool, str]:
    """Planlanan girise GERCEKTEN gelindi mi? Doner: (tetiklendi, gerekce).

    `breakout`: son KAPANMIS bar seviyenin otesinde kapanmali VE fiyat seviyeden
    `CHASE_LIMIT`ten uzak olmamali (kovalama yasagi). Ayni barda ikinci giris engellenir.

    Diger tipler (`pullback` / `market`): fiyat planlanan girise `NEAR_TOLERANCE` kadar
    yaklasmali. ATR plani girisi ANLIK fiyata koydugu icin orada mesafe 0'dir ve tetik
    hemen gecer; seviye tabanli planda fiyatin o seviyeye GELMESI beklenir.
    """
    if not price or not entry:
        return False, "NO_PRICE_OR_ENTRY"
    if entry_type == "breakout":
        if not last_bar or not last_close:
            # 4h cerceve yoksa tetik ASLA gecmez (denetim: last_close=0 kusuru)
            return False, "NO_CLOSED_BAR"
        if already_fired_bar is not None and already_fired_bar == last_bar:
            return False, "ALREADY_FIRED_THIS_BAR"
        lvl = entry / 1.001 if direction == "LONG" else entry / 0.999
        fired = (last_close > lvl) if direction == "LONG" else (last_close < lvl)
        if not fired:
            return False, "NO_BREAKOUT_CLOSE"
        if abs(price / entry - 1) > CHASE_LIMIT:
            return False, "CHASE_FORBIDDEN:%.2f" % (abs(price / entry - 1) * 100)
        return True, ""
    dist = abs(price / entry - 1)
    if dist <= NEAR_TOLERANCE:
        return True, ""
    return False, "NOT_AT_ENTRY:%.2f" % (dist * 100)


__all__ = ["CHASE_LIMIT", "NEAR_TOLERANCE", "trigger_fired"]
