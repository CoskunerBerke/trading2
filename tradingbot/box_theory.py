# -*- coding: utf-8 -*-
"""BOX THEORY — önceki günün aralığını 5 dakikalık grafikte soluklayan (fade) kural; canlı motor ile
replay'in ORTAK tek kaynağı (V15).

Kural (kaynak: Instagram "The box theory", 2026-09-17 tarihli video, altyazıdan birebir çıkarıldı):

1. Günlük grafikte ÖNCEKİ GÜNÜN en yükseği ile en düşüğü arasına bir kutu çiz.
2. Aynı kutuyu 5 dakikalık grafiğe taşı.
3. Fiyat kutunun ÜSTÜNDE ya da yakınındaysa SAT; ALTINDA ya da yakınındaysa AL; ORTADAYSA hiçbir şey yapma.
4. Short tetiği: "bir kırmızı mumun önceki mumun altına inmesi"; stop = ÖNCEKİ mumun tepesi.
   Long tetiği: "bir yeşil mumun görünmesi"; stop = "günün en düşüğünün hemen altı".

Videoda OLMAYAN ve bu yüzden PARAMETRE olan üç şey — ölçüm bunların süpürmesiyle yapılır:

* **Çıkış.** Video 93 saniyede kâr alma noktasını bir kez bile söylemez. `exit_kind` bunu açık eder:
  kutunun karşı kenarı / kutu ortası / R katı / yok. Hiçbir varsayılan "videonun kuralı" değildir.
* **"Yakın" ne kadar?** `near_frac`: kutu yüksekliğinin oranı olarak kenar bandı.
* **Long stop asimetrisi.** Videoda short stopu bir mumluk (dar), long stopu "günün en düşüğü" (geniş).
  `long_stop` iki okumayı da koşturur; hangisinin kastedildiği ÖLÇÜLMEDEN varsayılmaz.

Fonksiyonlar SAFTIR: `self` yok, dosya/ağ yok, girdiler değiştirilmez. Barlar KRONOLOJİK ve KAPANMIŞ
olmalı (çağıran `closed_bars` uygular) — giriş öncesi barın uçlarını okumak yasak (bkz. bar provenansı).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from .timeframes import DAY_MS

VARIANTS = ("b1_box_fade",)

#: Kenar bandı: kutu yüksekliğinin oranı. 0.10 → üst %10'a değen fiyat "tepeye yakın" sayılır.
DEFAULT_NEAR_FRAC = 0.10
#: Konum kaç kapanmış 5m mumuna bakılarak ölçülür (tetik mumu + öncesi).
DEFAULT_LOC_LOOKBACK = 2
#: Günlük kutu için gereken en az kapanmış günlük bar (önceki gün + bugünün referansı).
MIN_DAILY_BARS = 2
#: Tetik ve konum için gereken en az kapanmış 5m bar.
MIN_M5_BARS = 3

TRIGGERS = ("break_prev", "color_only")
LONG_STOPS = ("day_low", "prev_candle")
EXIT_KINDS = ("box_opposite", "box_mid", "r_multiple", "none")

_OHLC = ("timestamp", "open", "high", "low", "close")


@dataclass(frozen=True)
class BoxParams:
    """Kuralın TÜM serbest dereceleri. Süpürme bu nesnenin alanlarını gezer; kod dallanmaz."""

    near_frac: float = DEFAULT_NEAR_FRAC
    loc_lookback: int = DEFAULT_LOC_LOOKBACK
    allow_outside: bool = True          # kenarı AŞMIŞ fiyat da "kenarda" sayılır mı
    trigger: str = "break_prev"         # break_prev | color_only
    long_stop: str = "day_low"          # day_low (video birebir) | prev_candle (simetrik okuma)
    stop_buffer_frac: float = 0.0       # "hemen altı/üstü" payı (fiyatın oranı)
    exit_kind: str = "box_opposite"     # box_opposite | box_mid | r_multiple | none
    exit_r: float = 2.0                 # exit_kind == r_multiple için R katı
    eod_close: bool = True              # gün sonunda (UTC) düzleş
    #: BİZİM EKLEDİĞİMİZ EŞİK (videoda YOKTUR): stop mesafesi fiyatın bu yüzdesinden darsa işlem AÇILMAZ.
    #: Neden gerekli: kural stopu bir 5m mumu kadar dar koyuyor (ölçüldü: medyan %0,25) ve gidiş-dönüş
    #: işlem maliyeti (%0,16) riskin ~%80'ini yiyor. 0.0 = eşik yok (videonun birebir okuması).
    min_stop_pct: float = 0.0
    allow_long: bool = True
    allow_short: bool = True
    #: Kaldirac kurala ait bir iddia DEGILDIR (video hic soylemez) ve islem basina riski DEGISTIRMEZ.
    #: Tek islevi: `risk% / stop%` ile buyuyen notional'in tek-coin tavanina sigmasi. Gun ici dar stopta
    #: 1 birakilirsa kural uretimde MAX_POSITION_PCT ile reddedilir ve defter sessizce bos kalir.
    leverage: int = 1

    def validate(self) -> "BoxParams":
        if self.trigger not in TRIGGERS:
            raise ValueError("bilinmeyen tetik: %r (geçerli: %s)" % (self.trigger, ", ".join(TRIGGERS)))
        if self.long_stop not in LONG_STOPS:
            raise ValueError("bilinmeyen long stop: %r (geçerli: %s)" % (self.long_stop, ", ".join(LONG_STOPS)))
        if self.exit_kind not in EXIT_KINDS:
            raise ValueError("bilinmeyen çıkış: %r (geçerli: %s)" % (self.exit_kind, ", ".join(EXIT_KINDS)))
        if not (0.0 < float(self.near_frac) <= 0.5):
            raise ValueError("near_frac 0 ile 0.5 arasında olmalı: %r" % (self.near_frac,))
        if int(self.loc_lookback) < 1:
            raise ValueError("loc_lookback >= 1 olmalı: %r" % (self.loc_lookback,))
        if self.exit_kind == "r_multiple" and not (float(self.exit_r) > 0):
            raise ValueError("exit_r > 0 olmalı: %r" % (self.exit_r,))
        if float(self.stop_buffer_frac) < 0:
            raise ValueError("stop_buffer_frac >= 0 olmalı: %r" % (self.stop_buffer_frac,))
        if not (0.0 <= float(self.min_stop_pct) < 100.0):
            raise ValueError("min_stop_pct 0 ile 100 arasında olmalı: %r" % (self.min_stop_pct,))
        return self

    def label(self) -> str:
        """Süpürme kolunun kısa kimliği — rapor ve run_id burada TEK yerde üretilir."""
        ex = self.exit_kind if self.exit_kind != "r_multiple" else ("r%g" % float(self.exit_r))
        side = "".join(s for s, on in (("L", self.allow_long), ("S", self.allow_short)) if on) or "none"
        ms = "_ms%g" % float(self.min_stop_pct) if float(self.min_stop_pct) > 0 else ""
        return "n%g_%s_%s_%s_%s%s%s" % (float(self.near_frac), self.trigger, self.long_stop, ex, side,
                                        ms, "_eod" if self.eod_close else "")


DEFAULT_PARAMS = BoxParams()


def _f(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _i(x: Any) -> int | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def rows_from_frame(frame, tail: int = 400) -> list[dict[str, Any]]:
    """DataFrame'den kural satırları (timestamp + OHLC). Sütun seçimi tek yerde; eksikse boş liste."""
    if frame is None:
        return []
    try:
        cols = [c for c in _OHLC if c in frame.columns]
        if len(cols) < len(_OHLC):
            return []
        return frame.tail(tail)[cols].to_dict("records")
    except (AttributeError, TypeError, ValueError, KeyError):
        return []


def read_box(daily_rows: list[dict[str, Any]]) -> tuple[float, float] | None:
    """(box_high, box_low) — SON KAPANMIŞ günlük barın uçları. Ölçülemezse None (fail-closed)."""
    if not daily_rows or len(daily_rows) < MIN_DAILY_BARS:
        return None
    last = daily_rows[-1]
    hi, lo = _f(last.get("high")), _f(last.get("low"))
    if hi is None or lo is None or not (hi > lo > 0):
        return None
    return hi, lo


def _day_start_ms(ts: int) -> int:
    return int(ts) - (int(ts) % DAY_MS)


def read_intraday(m5_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Son kapanmış 5m mumu, öncesi ve BUGÜNÜN (UTC) o ana kadarki uçları. Ölçülemezse None."""
    if not m5_rows or len(m5_rows) < MIN_M5_BARS:
        return None
    cur, prev = m5_rows[-1], m5_rows[-2]
    ts = _i(cur.get("timestamp"))
    if ts is None:
        return None
    vals = {}
    for name, row in (("cur", cur), ("prev", prev)):
        o, h, lo, c = (_f(row.get(k)) for k in ("open", "high", "low", "close"))
        if None in (o, h, lo, c) or not (h >= c >= lo > 0) or not (h >= o >= lo):
            return None
        vals[name] = {"open": o, "high": h, "low": lo, "close": c, "timestamp": _i(row.get("timestamp"))}
    d0 = _day_start_ms(ts)
    day = [r for r in m5_rows if (_i(r.get("timestamp")) or -1) >= d0]
    lows = [_f(r.get("low")) for r in day]
    highs = [_f(r.get("high")) for r in day]
    if not lows or any(v is None for v in lows) or any(v is None for v in highs):
        return None
    return {"cur": vals["cur"], "prev": vals["prev"], "day_start_ms": d0,
            "day_low": min(lows), "day_high": max(highs), "n_day_bars": len(day)}


def location(price: float, box_high: float, box_low: float, *, near_frac: float,
             allow_outside: bool) -> str:
    """Fiyatın kutuya göre yeri: TOP | BOTTOM | MIDDLE | OUTSIDE.

    `allow_outside=False` iken kenarı aşmış fiyat OUTSIDE'tır ve işlem üretmez; True iken aşan fiyat da
    ilgili kenara sayılır (videonun "at or near" ifadesinin geniş okuması). Bu seçim ÖLÇÜLÜR, varsayılmaz.
    """
    h = box_high - box_low
    if not (h > 0):
        return "MIDDLE"
    band = h * float(near_frac)
    if price >= box_high:
        return "TOP" if allow_outside else "OUTSIDE"
    if price <= box_low:
        return "BOTTOM" if allow_outside else "OUTSIDE"
    if price >= box_high - band:
        return "TOP"
    if price <= box_low + band:
        return "BOTTOM"
    return "MIDDLE"


def _edge_location(m5_rows: list[dict[str, Any]], box_high: float, box_low: float,
                   p: BoxParams) -> str:
    """Konum, son `loc_lookback` kapanmış mumun UÇLARINDAN okunur: fiyat kenara DEĞDİ mi.

    Neden uçlar: tetik mumu tanımı gereği kenardan uzaklaşır; yalnız kapanışa bakmak "fiyat tepedeydi"
    olgusunu kaçırır. Yalnız KAPANMIŞ mumlar kullanılır.
    """
    tail = m5_rows[-int(p.loc_lookback):] if p.loc_lookback > 0 else []
    top = bottom = False
    for r in tail:
        hi, lo = _f(r.get("high")), _f(r.get("low"))
        if hi is None or lo is None:
            continue
        if location(hi, box_high, box_low, near_frac=p.near_frac, allow_outside=p.allow_outside) == "TOP":
            top = True
        if location(lo, box_high, box_low, near_frac=p.near_frac, allow_outside=p.allow_outside) == "BOTTOM":
            bottom = True
    if top and bottom:
        return "MIDDLE"          # aynı pencerede iki kenara da değdi: yön yok, işlem yok
    return "TOP" if top else ("BOTTOM" if bottom else "MIDDLE")


def _triggered(side: str, cur: dict[str, float], prev: dict[str, float], p: BoxParams) -> bool:
    if side == "SHORT":
        red = cur["close"] < cur["open"]
        return red and (cur["close"] < prev["low"] if p.trigger == "break_prev" else True)
    green = cur["close"] > cur["open"]
    return green and (cur["close"] > prev["high"] if p.trigger == "break_prev" else True)


def _stop_for(side: str, cur: dict[str, float], prev: dict[str, float], intraday: dict[str, Any],
              p: BoxParams) -> float | None:
    buf = float(p.stop_buffer_frac)
    if side == "SHORT":
        base = prev["high"]
        return base * (1.0 + buf)
    base = intraday["day_low"] if p.long_stop == "day_low" else prev["low"]
    stop = base * (1.0 - buf)
    return stop if stop > 0 else None


def targets_for(side: str, entry: float, stop: float, box_high: float, box_low: float,
                p: BoxParams) -> list[float]:
    """Çıkış hedefi — VİDEODA YOKTUR, süpürmenin konusudur. Ulaşılamaz hedef üretilmez."""
    if p.exit_kind == "none":
        return []
    if p.exit_kind == "box_opposite":
        tgt = box_low if side == "SHORT" else box_high
    elif p.exit_kind == "box_mid":
        tgt = (box_high + box_low) / 2.0
    else:
        r = abs(entry - stop)
        tgt = entry - float(p.exit_r) * r if side == "SHORT" else entry + float(p.exit_r) * r
    if not math.isfinite(tgt) or tgt <= 0:
        return []
    # Hedef girişin YANLIŞ tarafındaysa (kenarı aşmış giriş: kutu karşı kenarı geride kalmış) hedef yok.
    if (side == "SHORT" and tgt >= entry) or (side == "LONG" and tgt <= entry):
        return []
    return [float(tgt)]


def decide(variant: str = "b1_box_fade", *, daily_rows: list[dict[str, Any]],
           m5_rows: list[dict[str, Any]], position: dict[str, Any] | None = None,
           params: BoxParams = DEFAULT_PARAMS) -> dict[str, Any] | None:
    """Tek karar: {"action": "OPEN"|"CLOSE", ...} ya da None. Bilinmeyen veri → None (fail-closed)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    p = params.validate()
    box = read_box(daily_rows)
    intr = read_intraday(m5_rows)
    if box is None or intr is None:
        return None
    box_high, box_low = box
    cur, prev = intr["cur"], intr["prev"]

    if position:
        # Açık pozisyonun stop/hedefi DEFTERDEN yürür; kuralın tek kapatma sebebi gün sonu düzleşmesidir.
        if not p.eod_close:
            return None
        opened = _i(position.get("opened_ts"))
        if opened is None or _day_start_ms(opened) >= intr["day_start_ms"]:
            return None
        return {"action": "CLOSE", "reason": "BOX_EOD_FLAT", "name": variant}

    loc = _edge_location(m5_rows, box_high, box_low, p)
    if loc == "TOP" and p.allow_short:
        side = "SHORT"
    elif loc == "BOTTOM" and p.allow_long:
        side = "LONG"
    else:
        return None
    if not _triggered(side, cur, prev, p):
        return None
    entry = cur["close"]
    stop = _stop_for(side, cur, prev, intr, p)
    if stop is None or not math.isfinite(stop) or stop <= 0:
        return None
    if (side == "SHORT" and stop <= entry) or (side == "LONG" and stop >= entry):
        return None        # stop girişin yanlış tarafında: risk tanımsız, işlem yok
    if float(p.min_stop_pct) > 0 and abs(entry - float(stop)) / entry * 100.0 < float(p.min_stop_pct):
        return None        # stop maliyetin yanında anlamsız kalacak kadar dar (BİZİM eşiğimiz, videonun değil)
    return {"action": "OPEN", "direction": side, "stop": float(stop),
            "targets": targets_for(side, entry, stop, box_high, box_low, p),
            "leverage": int(p.leverage), "reason": "BOX_FADE_%s" % loc, "name": variant,
            "setup_type": "box_fade", "location": loc,
            "box_high": box_high, "box_low": box_low, "box_mid": (box_high + box_low) / 2.0,
            "signal_close": entry, "signal_ts": cur.get("timestamp"),
            "prev_high": prev["high"], "prev_low": prev["low"],
            "day_low": intr["day_low"], "day_high": intr["day_high"],
            "risk_per_unit": abs(entry - float(stop)), "exit_kind": p.exit_kind,
            "params_label": p.label()}


def rule_state(variant: str = "b1_box_fade", *, daily_rows: list[dict[str, Any]],
               m5_rows: list[dict[str, Any]], params: BoxParams = DEFAULT_PARAMS) -> dict[str, Any]:
    """Kuralın KARŞILAŞTIRDIĞI değerler — gösterim için, `decide` ile AYNI okuma/yol (ikinci formül yok)."""
    if variant not in VARIANTS:
        raise ValueError("bilinmeyen strateji varyanti: %r" % (variant,))
    p = params.validate()
    out: dict[str, Any] = {"variant": variant, "ok": False, "box_high": None, "box_low": None, "box_mid": None,
                           "close": None, "signal_ts": None, "location": None, "triggered": None, "side": None,
                           "stop_if_open": None, "targets_if_open": None, "day_low": None, "day_high": None,
                           "n_daily_bars": len(daily_rows or []), "n_m5_bars": len(m5_rows or []),
                           "min_daily_bars": MIN_DAILY_BARS, "min_m5_bars": MIN_M5_BARS,
                           "params_label": p.label(), "near_frac": float(p.near_frac),
                           "exit_kind": p.exit_kind, "reason": None}
    box = read_box(daily_rows or [])
    if box is None:
        out["reason"] = "NOT_ENOUGH_DAILY_BARS" if len(daily_rows or []) < MIN_DAILY_BARS else "DAILY_ROWS_UNREADABLE"
        return out
    box_high, box_low = box
    out.update({"box_high": box_high, "box_low": box_low, "box_mid": (box_high + box_low) / 2.0})
    intr = read_intraday(m5_rows or [])
    if intr is None:
        out["reason"] = "NOT_ENOUGH_M5_BARS" if len(m5_rows or []) < MIN_M5_BARS else "M5_ROWS_UNREADABLE"
        return out
    cur, prev = intr["cur"], intr["prev"]
    out.update({"close": cur["close"], "signal_ts": cur.get("timestamp"),
                "day_low": intr["day_low"], "day_high": intr["day_high"]})
    loc = _edge_location(m5_rows, box_high, box_low, p)
    out["location"] = loc
    side = "SHORT" if (loc == "TOP" and p.allow_short) else ("LONG" if (loc == "BOTTOM" and p.allow_long) else None)
    out["side"] = side
    if side is None:
        out["ok"] = True
        out["reason"] = "MIDDLE" if loc == "MIDDLE" else "SIDE_DISABLED"
        return out
    out["triggered"] = _triggered(side, cur, prev, p)
    stop = _stop_for(side, cur, prev, intr, p)
    if stop is not None and math.isfinite(stop) and stop > 0:
        wrong = (side == "SHORT" and stop <= cur["close"]) or (side == "LONG" and stop >= cur["close"])
        out["stop_if_open"] = None if wrong else float(stop)
        if not wrong:
            out["targets_if_open"] = targets_for(side, cur["close"], float(stop), box_high, box_low, p)
    out["ok"] = True
    return out


def sweep_params(base: BoxParams = DEFAULT_PARAMS, **grid) -> list[BoxParams]:
    """Kartezyen süpürme: `sweep_params(near_frac=[0.05, 0.10], exit_kind=["box_mid"])`.

    Etiketler `BoxParams.label()` ile ÜRETİLİR; iki kol aynı etikete düşerse ValueError — sessizce
    üst üste yazan koşu yok (havuzlanmış kol yanılgısına karşı).
    """
    keys = sorted(grid)
    out: list[BoxParams] = []

    def walk(i: int, acc: dict[str, Any]) -> None:
        if i == len(keys):
            out.append(replace(base, **acc).validate())
            return
        for v in grid[keys[i]]:
            walk(i + 1, {**acc, keys[i]: v})

    walk(0, {})
    labels = [p.label() for p in out]
    dup = {x for x in labels if labels.count(x) > 1}
    if dup:
        raise ValueError("süpürme kolları aynı etikete düşüyor: %s" % ", ".join(sorted(dup)))
    return out


__all__ = ["BoxParams", "DEFAULT_NEAR_FRAC", "DEFAULT_PARAMS", "EXIT_KINDS", "LONG_STOPS", "MIN_DAILY_BARS",
           "MIN_M5_BARS", "TRIGGERS", "VARIANTS", "decide", "location", "read_box", "read_intraday",
           "rows_from_frame", "rule_state", "sweep_params", "targets_for"]
