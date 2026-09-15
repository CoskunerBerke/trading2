# -*- coding: utf-8 -*-
"""MUM ONAYI — canli motor ile replay'in ORTAK tek kaynagi (V4, 2026-09-12).

Neden ayri modul: sekil tespiti `learn/candle_context.py` icinde zaten vardi ama hicbir giris
kararina bagli degildi. Operator karariyla giris karar yoluna baglaniyor. Mantik iki motorda
ayri formulle YASAMAZ: `engine_v3` ve `replay/engine` bu dosyadaki AYNI fonksiyonu cagirir.
Arastirma kurallari (`research/entry_v1/candle_rules.py`) de buraya delege eder; boylece
DENEY_V4'te olculen sey ile uretimde calisan sey birebir ayni koddur.

Varyantlar (PROTOCOL_V4 §3):
  c1_4h         son KAPANMIS 4h barda adayin yonuyle ayni tarafli sekil → gir; yoksa/karsiysa girme
  c2_4h_confirm sekil bir onceki 4h barda olusmus VE son kapanmis bar teyit etmis
  c3_4h_veto    yalniz VETO: son 4h barda adayin yonune KARSI sekil varsa girme
  c4_1d         c1'in gunluk bar surumu

Olcum (DENEY_V4): hicbir varyant iki pencerede dogrulanmadi. `ENFORCE` acmak operator
kararidir; bu modul o karari uygular, dogrulanmis bir avantaj iddia etmez.

Fonksiyonlar SAFTIR: `self` yok, dosya/ag yok, girdileri mutasyona ugratmaz.
"""
from __future__ import annotations

from typing import Any

from .learn.candle_context import (BEAR_SIDE_SHAPES, BULL_SIDE_SHAPES, CONFIRMED,
                                   CandleContextConfig, _shapes, evaluate_confirmation)
from .timeframes import TF_MS as _TF_MS, tf_ms

MODES = ("OFF", "SHADOW", "ENFORCE")
VARIANTS = ("c1_4h", "c2_4h_confirm", "c3_4h_veto", "c4_1d")
DEFAULT_VARIANT = "c3_4h_veto"
#: Her varyantin okudugu zaman dilimi ve gerektirdigi KAPANMIS bar sayisi.
_NEED = {"c1_4h": ("4h", 3), "c2_4h_confirm": ("4h", 4), "c3_4h_veto": ("4h", 3),
         "c4_1d": ("1d", 3)}
#: Dilim tablosu TEK kaynaktan (`timeframes.TF_MS`): 2e31926'da burada yalniz 4h/1d vardi ve panelin
#: 15m/1h/1w istekleri ayni fonksiyonda KeyError ile cokuyordu (CHART ANALYSIS V1 onarimi, bulgu #1).


def closed_bars(rows: list[dict[str, Any]], *, now_ms: int, tf: str) -> list[dict[str, Any]]:
    """Yalniz `now_ms` aninda KAPANMIS barlar (timestamp + tf <= now; esitlik dahil, 1 ms once DEGIL).
    Formasyon kapanmamis bardan OKUNMAZ; replay'in `_slice` kurali ile ayni kosul. Bilinmeyen dilim
    sessizce 4h sayilmaz: ValueError."""
    step = tf_ms(tf)
    out = []
    for r in rows or []:
        ts = r.get("timestamp")
        if ts is None:
            continue
        try:
            if float(ts) + step <= float(now_ms):
                out.append(r)
        except (TypeError, ValueError):
            continue
    return out


def shape_side(shapes) -> str | None:
    """Sekil kumesinin TEK tarafli geometrisi: LONG / SHORT / None (yok ya da belirsiz)."""
    s = set(shapes or [])
    bull, bear = bool(BULL_SIDE_SHAPES & s), bool(BEAR_SIDE_SHAPES & s)
    if bull == bear:
        return None
    return "LONG" if bull else "SHORT"


def evaluate_variant(variant: str, *, direction: str, bars_4h: list[dict[str, Any]] | None,
                     bars_1d: list[dict[str, Any]] | None,
                     cfg: CandleContextConfig | None = None) -> tuple[bool, str]:
    """Tek varyantin karari. Doner: (gecti, gerekce). Barlar KRONOLOJIK ve KAPANMIS olmali."""
    if variant not in _NEED:
        raise ValueError("bilinmeyen mum onayi varyanti: %r" % (variant,))
    cfg = cfg or CandleContextConfig()
    tag = variant.split("_", 1)[0].upper()
    side = str(direction or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, tag + "_SIDE"
    tf, n = _NEED[variant]
    rows = list((bars_4h if tf == "4h" else bars_1d) or [])[-n:]
    if len(rows) < n:
        # Veto-only varyant veri yokken VETO ETMEZ (kural yalniz eler); digerleri giremez.
        return (True, "") if variant == "c3_4h_veto" else (False, tag + "_MISSING_BARS")
    if variant == "c2_4h_confirm":
        head, tail = rows[:-1], rows[-1:]
        shapes = _shapes(head, cfg)
        ps = shape_side(shapes)
        if ps is None:
            return False, "C2_NO_PATTERN"
        if ps != side:
            return False, "C2_OPPOSITE_PATTERN"
        conf = evaluate_confirmation(shapes, tail, head[-1].get("close"), cfg)
        return (True, "") if conf["state"] == CONFIRMED else (False, "C2_NOT_CONFIRMED")
    ps = shape_side(_shapes(rows, cfg))
    if variant == "c3_4h_veto":
        if ps is not None and ps != side:
            return False, "C3_OPPOSITE_PATTERN"
        return True, ""
    if ps is None:
        return False, tag + "_NO_PATTERN"
    return (True, "") if ps == side else (False, tag + "_OPPOSITE_PATTERN")


def candle_confirmation(*, mode: str, variant: str, direction: str,
                        bars_4h: list[dict[str, Any]] | None, bars_1d: list[dict[str, Any]] | None,
                        cfg: CandleContextConfig | None = None) -> dict[str, Any]:
    """Karar kaydi: secili varyantin hukmu + TUM varyantlarin golge hukmu.

    `blocks` yalniz `mode == ENFORCE` ve secili varyant gecmediginde True olur. `SHADOW`
    hicbir kosulda engellemez; yalniz kaydeder. `OFF` hicbir sey hesaplamaz.
    """
    cfg = cfg or CandleContextConfig()
    m = str(mode or "OFF").upper()
    out: dict[str, Any] = {"schema_version": "candle_confirmation_v1", "mode": m,
                           "variant": variant, "policy_version": cfg.policy_version,
                           "verdict": {"ok": True, "reason": ""}, "blocks": False, "shadow": {}}
    if m == "OFF":
        return out
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen mum onayi varyanti: %r" % (variant,))
    for v in VARIANTS:
        ok, why = evaluate_variant(v, direction=direction, bars_4h=bars_4h, bars_1d=bars_1d, cfg=cfg)
        out["shadow"][v] = {"ok": bool(ok), "reason": why}
    sel = out["shadow"][variant]
    out["verdict"] = {"ok": bool(sel["ok"]), "reason": sel["reason"]}
    out["blocks"] = bool(m == "ENFORCE" and not sel["ok"])
    return out


#: `ENFORCE` gercek parayla CALISMAZ: dogrulanmamis mantik (DENEY_V4) LIVE modda acilamaz.
_REAL_MONEY_MODES = ("LIVE", "LIVE_LIMITED")


def validate_settings(*, mode: str | None, variant: str | None, app_mode: str | None) -> str:
    """Config dogrulamasi (SAF): normalize edilmis modu dondurur; gecersizse ValueError.

    `config_v3.validate_v3` bunu ConfigError'a sarar. Ayri fonksiyon olmasinin nedeni, LIVE
    modlarin bu surumde daha erken bir kapida zaten reddedilmesi: bu kural o kapidan bagimsiz
    olarak SINANABILIR kalmali (savunma derinligi, olu kod degil).
    """
    m = str(mode or "OFF").upper()
    if m not in MODES:
        raise ValueError("candle_confirmation_mode gecersiz: %r (gecerli: %s)" % (mode, ", ".join(MODES)))
    if variant not in VARIANTS:
        raise ValueError("candle_confirmation_variant gecersiz: %r (gecerli: %s)" % (variant, ", ".join(VARIANTS)))
    if m == "ENFORCE" and str(app_mode or "").upper() in _REAL_MONEY_MODES:
        raise ValueError("CANDLE_CONFIRMATION_NOT_VALIDATED_FOR_LIVE: candle_confirmation_mode=ENFORCE "
                         "yalniz PAPER/TESTNET/OBSERVE/SHADOW_LIVE modda acilabilir (DENEY_V4 dogrulamadi)")
    return m


__all__ = ["DEFAULT_VARIANT", "MODES", "VARIANTS", "candle_confirmation", "closed_bars",
           "evaluate_variant", "shape_side", "validate_settings"]
