# -*- coding: utf-8 -*-
"""EMA200 TREND — tek kurallı strateji, canlı motor ile replay'in ORTAK tek kaynağı (V10).

Kural (DENEY_V9, T1/T2): son KAPANMIŞ günlük bar close > EMA200 → LONG (piyasa); close < EMA200
→ kapat. Felaket stopu: close − `atr_mult` × ATR14(1d). Hedef yok. Kaldıraç 1. T2: ayrıca BTC
rejimi UP (regime_gate ile aynı tanım) olmalı.

Ölçüm (research/entry_v1/out/DENEY_V9.md, botun kendi defterinde, funding dahil):
T2 +107% / +34% / +10% (P1/P2/P3), maksDD %22; şu anki üretim +141% / −28% / −40%. İşlem sayısı az
(21–76 / pencere), aralıklar geniş; bu KÂĞIT İLERİ TEST içindir, gerçek para kararı değildir.

Fonksiyonlar SAFTIR: `self` yok, dosya/ağ yok, girdiler değiştirilmez. Barlar KRONOLOJİK ve KAPANMIŞ
olmalı (çağıran `closed_bars` uygular).
"""
from __future__ import annotations

from dataclasses import dataclass

import math
from typing import Any

from .regime_gate import UP, btc_regime

VARIANTS = ("t1_trend", "t2_trend_regime", "m2_tsmom28")
TSMOM_LOOKBACK_DAYS = 28      # m2: close > close[-28] (Liu-Tsyvinski 1-4 haftalik zaman serisi momentumu)
DEFAULT_ATR_MULT = 3.0
MIN_DAILY_BARS = 210
_COLS = ("timestamp", "open", "high", "low", "close", "ema200", "atr14")


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def daily_rows_from_frame(frame, tail: int = 260) -> list[dict[str, Any]]:
    """Günlük DataFrame'den kural satırları (timestamp, OHLC, varsa ema200/atr14). Sütun seçimi tek yerde."""
    if frame is None:
        return []
    try:
        cols = [c for c in _COLS if c in frame.columns]
        if "close" not in cols or "timestamp" not in cols:
            return []
        return frame.tail(tail)[cols].to_dict("records")
    except (AttributeError, TypeError, ValueError, KeyError):
        return []


def _ema_last(closes: list[float], n: int = 200) -> float | None:
    if len(closes) < n:
        return None
    k = 2.0 / (n + 1.0)
    e = sum(closes[:n]) / n
    for v in closes[n:]:
        e = v * k + e * (1.0 - k)
    return e


def _atr_last(rows: list[dict[str, Any]], n: int = 14) -> float | None:
    if len(rows) < n + 1:
        return None
    trs = []
    for i in range(len(rows) - n, len(rows)):
        h, lo, pc = _f(rows[i].get("high")), _f(rows[i].get("low")), _f(rows[i - 1].get("close"))
        if None in (h, lo, pc):
            return None
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    return sum(trs) / n


def read_daily(rows: list[dict[str, Any]]) -> tuple[float, float, float] | None:
    """(close, ema200, atr14) — sütun varsa oradan, yoksa KAPANIŞLARDAN hesaplanır; ölçülemezse None."""
    if len(rows) < MIN_DAILY_BARS:
        return None
    last = rows[-1]
    close = _f(last.get("close"))
    if close is None:
        return None
    ema = _f(last.get("ema200"))
    if ema is None:
        closes = [_f(r.get("close")) for r in rows]
        if any(c is None for c in closes):
            return None
        ema = _ema_last(closes)
    atr = _f(last.get("atr14"))
    if atr is None:
        atr = _atr_last(rows)
    if ema is None or atr is None or atr <= 0:
        return None
    return close, ema, atr


@dataclass(frozen=True)
class TrendParams:
    """Trend defterinin (T1/T2/M2) dogrulanmis parametreleri.

    KALDIRAC (2026-09-20): `decide` kaldiraci KODDA 1'e sabitliyordu. Sonucu sessizdi ama
    agirdi: tek pozisyon tavani `MAX_POSITION_PCT x kaldirac` oldugu icin, stop mesafesi
    ozkaynagin %6,67'sinden dar olan HER sinyal 30 USDT tavanini asip `MAX_POSITION_PCT`
    ile reddediliyordu. Box ayni aritmetigi `leverage: 3` ile cozmustu; trend defterlerinde
    cozulmemisti. Artik defter basina yapilandirilir.

    Kaldirac islem basina RISKI ARTIRMAZ — risk stop mesafesiyle belirlenir ve
    `risk_per_trade_pct` tavani degismez. Degistirdigi sey: ayni riski tasiyabilmek icin
    gereken notional'in tavana sigmasi ve likidasyon mesafesinin kisalmasi.
    """

    atr_mult: float = DEFAULT_ATR_MULT
    leverage: int = 1
    #: IHTIYAC KADAR KALDIRAC TAVANI (2026-09-21). `leverage` TABANDIR; uygulayici, islemin
    #: notional'i tek pozisyon tavanina sigmiyorsa kaldiraci BURAYA KADAR yukseltir. Stopu dar
    #: olan islem ayni hareketten daha cok R uretir ve tam da tavana takilan odur. Gerekmedikce
    #: yukseltilmez — kaldirac riski degil, likidasyon yakinligini artirir.
    leverage_max: int = 0
    #: MFE ESIGIYLE SILAHLANAN GEVSEK TRAIL (2026-09-20) — ikisi de 0 iken KAPALI ve
    #: davranis bit-bit eskisi gibidir. `trail_arm_r`: trail ancak en yuksek kar bu R'ye
    #: ULASINCA silahlanir. `trail_dist_r`: silahlandiktan sonra zirveden bu kadar R geri
    #: verilirse cikilir. Olculdu: kural cikisi zirvenin ~%64'unu geri veriyor; A=3R/D=1R
    #: 620 islemin yalniz 67'sine dokunur (digerleri hic 3R MFE gormuyor).
    #: NEDEN BURADA: defterin kendi `trailing_pct`i AYNI tikin `best`inden turetip AYNI tikin
    #: `worst`u ile test ediyor (bar ici sira varsayimi) ve canli 1h uclariyla, replay 4h barla
    #: tikliyor — ayni kural iki motorda FARKLI cikis uretirdi. Kapanmis GUNLUK bardan
    #: hesaplamak iki motorda da AYNI satirlari okur.
    trail_arm_r: float = 0.0
    trail_dist_r: float = 0.0

    def validate(self) -> "TrendParams":
        if not (self.atr_mult > 0):
            raise ValueError("trend rule_params.atr_mult pozitif olmali")
        if not (1 <= int(self.leverage) <= 125):
            raise ValueError("trend rule_params.leverage 1..125 araliginda olmali")
        if self.leverage_max and not (int(self.leverage) <= int(self.leverage_max) <= 125):
            raise ValueError("trend rule_params.leverage_max, leverage ile 125 arasinda olmali")
        if self.trail_arm_r < 0 or self.trail_dist_r < 0:
            raise ValueError("trend rule_params.trail_* negatif olamaz")
        if bool(self.trail_arm_r > 0) != bool(self.trail_dist_r > 0):
            raise ValueError("trail_arm_r ve trail_dist_r BIRLIKTE verilir (biri 0 ise trail kapalidir)")
        return self


def exit_measure(variant: str, rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    """Acik pozisyonun KURAL CIKISI icin gereken EN KUCUK olcu: (close, esik) | None.

    CIKIS, GIRISIN on kosullarina BAGLANAMAZ. ATR14 yalniz giris stopunu boyutlandirir ve
    cikis formulunde HIC yer almaz; buna ragmen 2026-09-19'a kadar ikisi de tek `read_daily`
    uzerinden okunuyordu. ATR olculemezse (ya da kapanmis gunluk satir sayisi MIN_DAILY_BARS'in
    altina duserse) `decide` sessizce None donuyor, `apply_action` bunu "NONE" sayiyor ve acik
    pozisyon kural cikisini BIR DAHA ASLA alamiyordu; defter ozetinde gorunen tek sey
    {"action": "NONE", "reason": "NO_SIGNAL"} oluyordu — yani "trend bozulmadi, tutuyoruz" ile
    "cikisi olcemiyorum" ayirt EDILEMIYORDU. Tetikleyici varsayimsal degil: V17 (3b0ae8e)
    cerceveleri USDS-M perp mumlarina tasidi ve genc perp sozlesmelerinde gunluk satir sayisi
    210'un altina duser. Veri eksikligi "cik" degil "tut" anlamina gelemez.

    Esik: T2 icin EMA200, M2 icin 28 gun onceki kapanis. EMA200 SUTUNU varsa satir sayisi
    sarti aranmaz (isinma zaten sutunu ureten tarafta yapilmistir); yalnizca EMA'yi BURADA
    hesaplamak gerekiyorsa MIN_DAILY_BARS istenir.
    """
    if not rows:
        return None
    close = _f(rows[-1].get("close"))
    if close is None:
        return None
    if variant == "m2_tsmom28":
        # M2 cikisi yalnizca 29 bar ister; 210 sarti bu yola HIC ait degildi.
        if len(rows) <= TSMOM_LOOKBACK_DAYS:
            return None
        ref = _f(rows[-1 - TSMOM_LOOKBACK_DAYS].get("close"))
        return (close, ref) if ref is not None else None
    ema = _f(rows[-1].get("ema200"))
    if ema is None:
        if len(rows) < MIN_DAILY_BARS:
            return None
        closes = [_f(r.get("close")) for r in rows]
        if any(c is None for c in closes):
            return None
        ema = _ema_last(closes)
    return (close, ema) if ema is not None else None


def trail_state(rows: list[dict[str, Any]], *, entry: float, initial_stop: float,
                opened_ms: int) -> tuple[float, float] | None:
    """(mfe_r, cur_r) — pozisyon acildiktan SONRA acilmis KAPANMIS gunluk barlardan.

    BAR PROVENANSI: yalnizca `timestamp >= opened_ms` olan barlar sayilir. Girisden ONCE
    acilmis bir barin ucu MFE'ye giremez — bu projede tam bu kusur yasandi (giris oncesi
    barin uclari MFE'ye yaziliyordu). Fail-closed: olculemezse None.
    """
    risk = abs(float(entry) - float(initial_stop))
    if not (risk > 0) or not rows:
        return None
    uygun = [r for r in rows if _f(r.get("timestamp")) is not None and int(r["timestamp"]) >= int(opened_ms)]
    if not uygun:
        return None
    yon = 1.0 if float(initial_stop) < float(entry) else -1.0      # LONG: stop asagida
    uclar = [_f(r.get("high") if yon > 0 else r.get("low")) for r in uygun]
    kapanis = _f(uygun[-1].get("close"))
    if kapanis is None or any(x is None for x in uclar):
        return None
    zirve = max(uclar) if yon > 0 else min(uclar)
    return (yon * (zirve - float(entry)) / risk, yon * (kapanis - float(entry)) / risk)


def decide(variant: str, *, daily_rows: list[dict[str, Any]], btc_daily_rows: list[dict[str, Any]] | None,
           position_open: bool, atr_mult: float = DEFAULT_ATR_MULT,
           leverage: int = 1, leverage_max: int = 0, trail_arm_r: float = 0.0,
           trail_dist_r: float = 0.0, position: Any = None) -> dict[str, Any] | None:
    """Tek karar: {"action": "OPEN"|"CLOSE", ...} ya da None. Bilinmeyen veri → None (fail-closed)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    # CIKIS ONCE ve GIRISTEN BAGIMSIZ olculur (2026-09-19): acik pozisyonun kapanmasi, yeni
    # giris icin gereken buyukluklerin (ATR14) olculebilmesine bagli olamaz.
    if position_open:
        # TRAIL (2026-09-20) — KURAL CIKISINDAN ONCE sorulur: kuyrugu kesmemek icin ancak
        # MFE esigi asildiktan SONRA silahlanir. Kapali (0/0) iken bu blok HIC calismaz ve
        # davranis bit-bit eskisi gibidir. Olculemezse (bar provenansi / initial_stop yok)
        # SESSIZ GECMEZ: trail uygulanmaz, kural cikisi normal isler.
        if trail_arm_r > 0 and trail_dist_r > 0 and position is not None:
            _e = _f(getattr(position, "entry_avg", None) if not isinstance(position, dict)
                    else position.get("entry_avg", position.get("entry")))
            _s0 = _f(getattr(position, "initial_stop", None) if not isinstance(position, dict)
                     else position.get("initial_stop"))
            _om = position.get("opened_ts") if isinstance(position, dict) else None
            if _e is not None and _s0 is not None and _om is not None:
                st = trail_state(daily_rows, entry=_e, initial_stop=_s0, opened_ms=int(_om))
                if st is not None and st[0] >= trail_arm_r and (st[0] - st[1]) >= trail_dist_r:
                    return {"action": "CLOSE", "reason": "TRAIL_GIVEBACK", "name": variant,
                            "mfe_r": round(st[0], 4), "cur_r": round(st[1], 4)}
        m = exit_measure(variant, daily_rows)
        if m is None:
            # SESSIZ GECMEZ: "olcemiyorum" ile "sinyal yok" ayri kayitlardir.
            return {"action": "NONE", "reason": "EXIT_UNMEASURABLE", "name": variant}
        x_close, x_thr = m
        if x_close > x_thr:
            return None
        why = "M2_TSMOM28_CROSS_DOWN" if variant == "m2_tsmom28" else "EMA200_CROSS_DOWN"
        return {"action": "CLOSE", "reason": why, "name": variant}
    d = read_daily(daily_rows)
    if d is None:
        return None
    close, ema, atr = d
    if variant == "m2_tsmom28":
        # DENEY_V11: 28 gunluk zaman serisi momentumu; rejim kapisi ve stop T2 ile ayni.
        ref = _f(daily_rows[-1 - TSMOM_LOOKBACK_DAYS].get("close")) if len(daily_rows) > TSMOM_LOOKBACK_DAYS else None
        if ref is None:
            return None
        above = close > ref
    else:
        above = close > ema
    if not above:
        return None
    regime = None
    if variant in ("t2_trend_regime", "m2_tsmom28"):
        regime = btc_regime(btc_daily_rows or [])
        if regime != UP:
            return None
    stop = close - atr_mult * atr
    if stop <= 0:
        return None
    act = {"action": "OPEN", "direction": "LONG", "stop": stop, "targets": [], "leverage": int(leverage), "leverage_max": int(leverage_max),
           "reason": "M2_TSMOM28" if variant == "m2_tsmom28" else "EMA200_TREND", "name": variant,
           "regime": regime, "setup_type": "trend",
           "signal_close": close, "ema200": ema, "atr14": atr,
           # CHART ANALYSIS V1: kararda kullanilan bar ve (M2 icin) referans kapanis KAYDA girer.
           # apply_action bu anahtarlari kullanmaz; karar/boyut/hash DEGISMEZ.
           "signal_ts": daily_rows[-1].get("timestamp")}
    if variant == "m2_tsmom28":
        act["ref_close"] = ref
        act["ref_ts"] = daily_rows[-1 - TSMOM_LOOKBACK_DAYS].get("timestamp")
    return act


def rule_state(variant: str, *, daily_rows: list[dict[str, Any]], btc_daily_rows: list[dict[str, Any]] | None,
               atr_mult: float = DEFAULT_ATR_MULT) -> dict[str, Any]:
    """Kuralin KARSILASTIRDIGI degerler — gosterim icin, `decide` ile AYNI okuma/yol (ikinci formul yok).

    Donus: close, signal_ts, ema200, atr14, ref_close/ref_ts (M2), above (kosul), regime (BTC), stop_if_open,
    reason (neden karar yok). Veri yetersizse `ok=False` ve neden yazilir; hicbir sey uydurulmaz.
    """
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    out: dict[str, Any] = {"variant": variant, "ok": False, "close": None, "signal_ts": None, "ema200": None, "atr14": None,
                           "ref_close": None, "ref_ts": None, "above": None, "regime": None, "stop_if_open": None,
                           "min_daily_bars": MIN_DAILY_BARS, "n_daily_bars": len(daily_rows or []), "atr_mult": float(atr_mult),
                           "lookback_days": TSMOM_LOOKBACK_DAYS if variant == "m2_tsmom28" else None}
    d = read_daily(daily_rows or [])
    if d is None:
        out["reason"] = "NOT_ENOUGH_DAILY_BARS" if len(daily_rows or []) < MIN_DAILY_BARS else "DAILY_ROWS_UNREADABLE"
        return out
    close, ema, atr = d
    out.update({"close": close, "ema200": ema, "atr14": atr, "signal_ts": daily_rows[-1].get("timestamp")})
    if variant == "m2_tsmom28":
        if len(daily_rows) <= TSMOM_LOOKBACK_DAYS:
            out["reason"] = "NOT_ENOUGH_DAILY_BARS"
            return out
        ref = _f(daily_rows[-1 - TSMOM_LOOKBACK_DAYS].get("close"))
        if ref is None:
            out["reason"] = "REF_CLOSE_UNREADABLE"
            return out
        out["ref_close"], out["ref_ts"] = ref, daily_rows[-1 - TSMOM_LOOKBACK_DAYS].get("timestamp")
        out["above"] = close > ref
    else:
        out["above"] = close > ema
    if variant in ("t2_trend_regime", "m2_tsmom28"):
        out["regime"] = btc_regime(btc_daily_rows or [])
    stop = close - float(atr_mult) * atr
    out["stop_if_open"] = stop if stop > 0 else None
    out["ok"] = True
    return out


__all__ = ["DEFAULT_ATR_MULT", "MIN_DAILY_BARS", "TSMOM_LOOKBACK_DAYS", "VARIANTS", "daily_rows_from_frame", "decide", "read_daily", "rule_state"]
