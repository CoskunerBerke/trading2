# -*- coding: utf-8 -*-
"""GRAFIK FORMASYONLARI — kapanmis barlardan, gelecege bakmadan, KIRILISLA teyitli (V5).

Kapsam (kullanicinin tablosu): cift dip/tepe, uclu dip/tepe, omuz-bas-omuz ve tersi,
alcalan/yukselen ucgen, boga/ayi bayragi. Wolfe dalgasi BILINCLI OLARAK YOK: tanimi oznel,
parametre yuzeyi buyuk, olculebilir bir kural haline getirilemedi.

Tasarim kurali (Bulkowski'nin olcumu): sekil yon VERMEZ, kirilis verir. Alcalan ucgen bile
zamanin %53'unde yukari kirilir. Bu yuzden hicbir formasyon "olusuyor" diye raporlanmaz;
yalniz boyun cizgisi / sinir KAPANISLA kirildiginda CONFIRMED olur ve yonu kirilisin yonudur.

Gelecege bakmama sozlesmesi: salinimlar `confirmed_swings` ile teyit edilir (her iki yanda
`swing_lookback` kapanmis bar). Bir formasyon en erken `recognition_index`te bilinebilir:
max(kirilis bari, son salinimin teyit bari). `bars_since` bu indeksten olculur.

Fonksiyonlar SAFTIR: `self` yok, dosya/ag yok, girdiler degistirilmez.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .core import stable_id
from .learn.multitimeframe_context import confirmed_swings

SCHEMA_VERSION = "chart_patterns_v1"
DOUBLE_BOTTOM = "DOUBLE_BOTTOM"
DOUBLE_TOP = "DOUBLE_TOP"
TRIPLE_BOTTOM = "TRIPLE_BOTTOM"
TRIPLE_TOP = "TRIPLE_TOP"
INVERSE_HEAD_AND_SHOULDERS = "INVERSE_HEAD_AND_SHOULDERS"
HEAD_AND_SHOULDERS = "HEAD_AND_SHOULDERS"
DESCENDING_TRIANGLE = "DESCENDING_TRIANGLE"
ASCENDING_TRIANGLE = "ASCENDING_TRIANGLE"
BULL_FLAG = "BULL_FLAG"
BEAR_FLAG = "BEAR_FLAG"
BULL = "BULL"
BEAR = "BEAR"
CONFIRMED = "CONFIRMED"
ALL_PATTERNS = (DOUBLE_BOTTOM, DOUBLE_TOP, TRIPLE_BOTTOM, TRIPLE_TOP, INVERSE_HEAD_AND_SHOULDERS,
                HEAD_AND_SHOULDERS, DESCENDING_TRIANGLE, ASCENDING_TRIANGLE, BULL_FLAG, BEAR_FLAG)


@dataclass
class ChartPatternConfig:
    """Geometrik esikler. Tek deger; bu veriye UYDURULMADI (PROTOCOL_V5 §7)."""
    policy_version: str = "chart_v1.0.0"
    scan_bars: int = 120                  # yalniz son N kapanmis bar taranir
    swing_lookback: int = 3               # fraktal k
    level_tolerance_pct: float = 1.5      # esit tepe/dip toleransi (seviyenin yuzdesi)
    min_separation_bars: int = 5          # iki salinim arasi en az bar
    max_pattern_bars: int = 60            # formasyon genisligi ust siniri
    min_depth_pct: float = 3.0            # dip(ler) ile boyun cizgisi arasi en az yuzde
    shoulder_tolerance_pct: float = 3.0   # omuzlar arasi fark
    head_min_excess_pct: float = 2.0      # bas, omuzlari en az bu kadar asmali
    triangle_min_step_pct: float = 0.5    # ucgende ardisik tepeler en az bu kadar alcalmali
    flag_pole_bars: int = 8               # direk uzunlugu (bar)
    flag_pole_min_pct: float = 6.0        # direk buyuklugu (yuzde)
    flag_min_bars: int = 3                # konsolidasyon en az bar
    flag_max_bars: int = 15               # konsolidasyon en fazla bar
    flag_max_retrace: float = 0.5         # direğin en fazla bu orani geri verilir
    confirm_within_bars: int = 3          # "taze" kirilis: son N kapanmis bar icinde

    def validate(self) -> None:
        if self.scan_bars < 30 or self.swing_lookback < 1:
            raise ValueError("scan_bars >= 30 ve swing_lookback >= 1 olmali")
        if not (0 < self.level_tolerance_pct < 10):
            raise ValueError("level_tolerance_pct (0, 10) araliginda olmali")
        if self.min_separation_bars < 2 or self.max_pattern_bars <= self.min_separation_bars:
            raise ValueError("min_separation_bars >= 2 ve max_pattern_bars > min_separation_bars olmali")
        if self.min_depth_pct <= 0 or self.head_min_excess_pct <= 0 or self.triangle_min_step_pct <= 0:
            raise ValueError("derinlik/bas/ucgen esikleri pozitif olmali")
        if self.flag_pole_bars < 2 or self.flag_min_bars < 2 or self.flag_max_bars < self.flag_min_bars:
            raise ValueError("bayrak bar esikleri tutarsiz")
        if not (0 < self.flag_max_retrace < 1) or self.flag_pole_min_pct <= 0:
            raise ValueError("flag_max_retrace (0,1) ve flag_pole_min_pct > 0 olmali")
        if self.confirm_within_bars < 0:
            raise ValueError("confirm_within_bars >= 0 olmali")

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ChartPatternConfig":
        allowed = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in dict(d or {}).items() if k in allowed})
        cfg.validate()
        return cfg

    @property
    def config_id(self) -> str:
        return stable_id("chartcfg", self.policy_version, self.to_dict())


# ---------------------------------------------------------------------------- yardimcilar
def _near(a: float, b: float, tol_pct: float) -> bool:
    return abs(a - b) <= tol_pct / 100.0 * max(abs(a), abs(b), 1e-12)


def _first_break(bars, start: int, level_at, *, above: bool) -> int | None:
    """`start`tan itibaren ilk KAPANIS kirilisi. `level_at(j)` bardaki sinir degeri."""
    for j in range(max(0, start), len(bars)):
        c = bars[j]["close"]
        lv = level_at(j)
        if lv is None:
            continue
        if (c > lv) if above else (c < lv):
            return j
    return None


def _rec(bars, pattern: str, side: str, break_idx: int, last_confirm_idx: int, level: float,
         start_idx: int, end_idx: int) -> dict[str, Any]:
    n = len(bars)
    rec = max(break_idx, last_confirm_idx)
    return {"pattern": pattern, "side": side, "state": CONFIRMED,
            "level": round(float(level), 10), "break_index": break_idx,
            "recognition_index": rec, "bars_since": n - 1 - rec,
            "start_ts": bars[start_idx]["timestamp"], "end_ts": bars[end_idx]["timestamp"],
            "recognition_ts": bars[rec]["timestamp"]}


def _clean_rows(bars) -> list[dict[str, Any]]:
    out = []
    for b in bars or []:
        try:
            r = {"timestamp": b["timestamp"], "open": float(b["open"]), "high": float(b["high"]),
                 "low": float(b["low"]), "close": float(b["close"])}
        except (KeyError, TypeError, ValueError):
            continue
        if r["high"] < r["low"]:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------- dedektorler
def _doubles(bars, lows, highs, cfg, *, bottom: bool) -> list[dict[str, Any]]:
    pts = lows if bottom else highs
    out = []
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            p1, p2 = pts[a], pts[b]
            sep = p2["index"] - p1["index"]
            if sep < cfg.min_separation_bars:
                continue
            if sep > cfg.max_pattern_bars:
                break
            if not _near(p1["level"], p2["level"], cfg.level_tolerance_pct):
                continue
            # arada iki dipten/tepeden daha ileri giden ucuncu bir salinim olmamali
            inner = [q for q in pts[a + 1:b]]
            if bottom and any(q["level"] < min(p1["level"], p2["level"]) for q in inner):
                continue
            if not bottom and any(q["level"] > max(p1["level"], p2["level"]) for q in inner):
                continue
            mid = bars[p1["index"] + 1:p2["index"]]
            if not mid:
                continue
            base = (p1["level"] + p2["level"]) / 2.0
            if bottom:
                neck = max(m["high"] for m in mid)
                if (neck / base - 1.0) * 100.0 < cfg.min_depth_pct:
                    continue
                j = _first_break(bars, p2["index"] + 1, lambda _j: neck, above=True)
            else:
                neck = min(m["low"] for m in mid)
                if (1.0 - neck / base) * 100.0 < cfg.min_depth_pct:
                    continue
                j = _first_break(bars, p2["index"] + 1, lambda _j: neck, above=False)
            if j is None:
                continue
            out.append(_rec(bars, DOUBLE_BOTTOM if bottom else DOUBLE_TOP, BULL if bottom else BEAR,
                            j, p2["confirmed_at_index"], neck, p1["index"], j))
    return out


def _triples(bars, lows, highs, cfg, *, bottom: bool) -> list[dict[str, Any]]:
    pts = lows if bottom else highs
    out = []
    for a in range(len(pts)):
        for b in range(a + 1, len(pts)):
            for c in range(b + 1, len(pts)):
                p1, p2, p3 = pts[a], pts[b], pts[c]
                if p3["index"] - p1["index"] > cfg.max_pattern_bars:
                    break
                if (p2["index"] - p1["index"] < cfg.min_separation_bars
                        or p3["index"] - p2["index"] < cfg.min_separation_bars):
                    continue
                lv = [p1["level"], p2["level"], p3["level"]]
                if not (_near(lv[0], lv[1], cfg.level_tolerance_pct)
                        and _near(lv[1], lv[2], cfg.level_tolerance_pct)
                        and _near(lv[0], lv[2], cfg.level_tolerance_pct)):
                    continue
                inner = [q for q in pts[a + 1:c] if q is not p2]
                if bottom and any(q["level"] < min(lv) for q in inner):
                    continue
                if not bottom and any(q["level"] > max(lv) for q in inner):
                    continue
                mid = bars[p1["index"] + 1:p3["index"]]
                base = sum(lv) / 3.0
                if bottom:
                    neck = max(m["high"] for m in mid)
                    if (neck / base - 1.0) * 100.0 < cfg.min_depth_pct:
                        continue
                    j = _first_break(bars, p3["index"] + 1, lambda _j: neck, above=True)
                else:
                    neck = min(m["low"] for m in mid)
                    if (1.0 - neck / base) * 100.0 < cfg.min_depth_pct:
                        continue
                    j = _first_break(bars, p3["index"] + 1, lambda _j: neck, above=False)
                if j is None:
                    continue
                out.append(_rec(bars, TRIPLE_BOTTOM if bottom else TRIPLE_TOP, BULL if bottom else BEAR,
                                j, p3["confirmed_at_index"], neck, p1["index"], j))
    return out


def _head_shoulders(bars, lows, highs, cfg, *, inverse: bool) -> list[dict[str, Any]]:
    pts = lows if inverse else highs
    out = []
    for a in range(len(pts) - 2):
        s1, h, s2 = pts[a], pts[a + 1], pts[a + 2]
        if s2["index"] - s1["index"] > cfg.max_pattern_bars:
            continue
        if (h["index"] - s1["index"] < cfg.min_separation_bars
                or s2["index"] - h["index"] < cfg.min_separation_bars):
            continue
        ex = cfg.head_min_excess_pct / 100.0
        if inverse:
            if not (h["level"] < s1["level"] * (1 - ex) and h["level"] < s2["level"] * (1 - ex)):
                continue
        else:
            if not (h["level"] > s1["level"] * (1 + ex) and h["level"] > s2["level"] * (1 + ex)):
                continue
        if not _near(s1["level"], s2["level"], cfg.shoulder_tolerance_pct):
            continue
        seg1 = bars[s1["index"] + 1:h["index"]]
        seg2 = bars[h["index"] + 1:s2["index"]]
        if not seg1 or not seg2:
            continue
        if inverse:
            t1i = max(range(s1["index"] + 1, h["index"]), key=lambda i: bars[i]["high"])
            t2i = max(range(h["index"] + 1, s2["index"]), key=lambda i: bars[i]["high"])
            t1, t2 = bars[t1i]["high"], bars[t2i]["high"]
        else:
            t1i = min(range(s1["index"] + 1, h["index"]), key=lambda i: bars[i]["low"])
            t2i = min(range(h["index"] + 1, s2["index"]), key=lambda i: bars[i]["low"])
            t1, t2 = bars[t1i]["low"], bars[t2i]["low"]
        slope = (t2 - t1) / float(max(1, t2i - t1i))

        def neck(j, _t1=t1, _t1i=t1i, _slope=slope):
            return _t1 + _slope * (j - _t1i)
        j = _first_break(bars, s2["index"] + 1, neck, above=inverse)
        if j is None:
            continue
        out.append(_rec(bars, INVERSE_HEAD_AND_SHOULDERS if inverse else HEAD_AND_SHOULDERS,
                        BULL if inverse else BEAR, j, s2["confirmed_at_index"], neck(j),
                        s1["index"], j))
    return out


def _triangles(bars, lows, highs, cfg, *, descending: bool) -> list[dict[str, Any]]:
    """Alcalan: duz destek (>=2 esit dip) + alcalan tepeler (>=2). Kirilis yonu tarafi belirler."""
    flat_pts = lows if descending else highs
    slope_pts = highs if descending else lows
    step = cfg.triangle_min_step_pct / 100.0
    out = []
    for a in range(len(flat_pts)):
        for b in range(a + 1, len(flat_pts)):
            f1, f2 = flat_pts[a], flat_pts[b]
            if f2["index"] - f1["index"] > cfg.max_pattern_bars:
                break
            if f2["index"] - f1["index"] < cfg.min_separation_bars:
                continue
            if not _near(f1["level"], f2["level"], cfg.level_tolerance_pct):
                continue
            sl = [q for q in slope_pts if f1["index"] < q["index"] < f2["index"] + cfg.max_pattern_bars // 2
                  and q["index"] > f1["index"]]
            sl = [q for q in sl if q["index"] <= f2["index"] + cfg.min_separation_bars]
            if len(sl) < 2:
                continue
            q1, q2 = sl[-2], sl[-1]
            if q2["index"] - q1["index"] < cfg.min_separation_bars:
                continue
            if descending and not (q2["level"] < q1["level"] * (1 - step)):
                continue
            if not descending and not (q2["level"] > q1["level"] * (1 + step)):
                continue
            support = (f1["level"] + f2["level"]) / 2.0
            slope = (q2["level"] - q1["level"]) / float(max(1, q2["index"] - q1["index"]))

            def line(j, _q1=q1, _slope=slope):
                return _q1["level"] + _slope * (j - _q1["index"])
            last_idx = max(f2["index"], q2["index"])
            last_conf = max(f2["confirmed_at_index"], q2["confirmed_at_index"])
            j_flat = _first_break(bars, last_idx + 1, lambda _j: support, above=not descending)

            def line_before_apex(j, _line=line, _support=support, _desc=descending):
                # Egimli sinir destege/dirence ulastiktan (tepe noktasi) sonra formasyon GECERSIZ:
                # sonsuza uzatilan cizgi her kapanisi "kirilis" yapardi.
                lv = _line(j)
                if (_desc and lv <= _support) or ((not _desc) and lv >= _support):
                    return None
                return lv
            j_line = _first_break(bars, last_idx + 1, line_before_apex, above=descending)
            cands = [(j, "flat") for j in [j_flat] if j is not None] + \
                    [(j, "line") for j in [j_line] if j is not None]
            if not cands:
                continue
            j, which = min(cands)
            name = DESCENDING_TRIANGLE if descending else ASCENDING_TRIANGLE
            if descending:
                side = BEAR if which == "flat" else BULL
            else:
                side = BULL if which == "flat" else BEAR
            lvl = support if which == "flat" else line(j)
            out.append(_rec(bars, name, side, j, last_conf, lvl, f1["index"], j))
    return out


def _flags(bars, cfg, *, bull: bool) -> list[dict[str, Any]]:
    out = []
    n = len(bars)
    pb = cfg.flag_pole_bars
    seen: set[int] = set()
    for p in range(pb, n - cfg.flag_min_bars - 1):
        c0, cp = bars[p - pb]["close"], bars[p]["close"]
        rise = (cp / c0 - 1.0) if bull else (1.0 - cp / c0)
        if rise * 100.0 < cfg.flag_pole_min_pct:
            continue
        pole_h = abs(cp - c0)
        pole_ext = max(b["high"] for b in bars[p - pb:p + 1]) if bull else min(b["low"] for b in bars[p - pb:p + 1])
        for q in range(p + cfg.flag_min_bars, min(n - 1, p + cfg.flag_max_bars) + 1):
            cons = bars[p + 1:q + 1]
            if bull:
                if max(b["high"] for b in cons) > pole_ext * (1 + cfg.level_tolerance_pct / 100.0):
                    break
                if min(b["low"] for b in cons) < cp - cfg.flag_max_retrace * pole_h:
                    break
                if cons[-1]["high"] > cons[0]["high"] * (1 + cfg.level_tolerance_pct / 100.0):
                    continue                     # kanal yukari egimli: bayrak degil
                boundary = max(b["high"] for b in cons)
                j = q + 1
                if j < n and bars[j]["close"] > boundary:
                    if j in seen:
                        break
                    seen.add(j)
                    out.append(_rec(bars, BULL_FLAG, BULL, j, j, boundary, p - pb, j))
                    break
            else:
                if min(b["low"] for b in cons) < pole_ext * (1 - cfg.level_tolerance_pct / 100.0):
                    break
                if max(b["high"] for b in cons) > cp + cfg.flag_max_retrace * pole_h:
                    break
                if cons[-1]["low"] < cons[0]["low"] * (1 - cfg.level_tolerance_pct / 100.0):
                    continue
                boundary = min(b["low"] for b in cons)
                j = q + 1
                if j < n and bars[j]["close"] < boundary:
                    if j in seen:
                        break
                    seen.add(j)
                    out.append(_rec(bars, BEAR_FLAG, BEAR, j, j, boundary, p - pb, j))
                    break
    return out


# ---------------------------------------------------------------------------- giris noktasi
def detect_chart_patterns(bars: list[dict[str, Any]], cfg: ChartPatternConfig | None = None
                          ) -> dict[str, Any]:
    """Son `scan_bars` KAPANMIS bardaki TEYITLI formasyonlar. Olusmakta olanlar RAPORLANMAZ."""
    cfg = cfg or ChartPatternConfig()
    rows = _clean_rows(bars)[-cfg.scan_bars:]
    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "policy_version": cfg.policy_version,
                           "config_id": cfg.config_id, "n_bars": len(rows), "patterns": []}
    if len(rows) < 2 * cfg.swing_lookback + cfg.min_separation_bars + 2:
        out["reason"] = "NOT_ENOUGH_BARS"
        return out
    sw = confirmed_swings(rows, lookback=cfg.swing_lookback)
    lows, highs = sw["lows"], sw["highs"]
    pats: list[dict[str, Any]] = []
    pats += _doubles(rows, lows, highs, cfg, bottom=True)
    pats += _doubles(rows, lows, highs, cfg, bottom=False)
    pats += _triples(rows, lows, highs, cfg, bottom=True)
    pats += _triples(rows, lows, highs, cfg, bottom=False)
    pats += _head_shoulders(rows, lows, highs, cfg, inverse=True)
    pats += _head_shoulders(rows, lows, highs, cfg, inverse=False)
    pats += _triangles(rows, lows, highs, cfg, descending=True)
    pats += _triangles(rows, lows, highs, cfg, descending=False)
    pats += _flags(rows, cfg, bull=True)
    pats += _flags(rows, cfg, bull=False)
    pats.sort(key=lambda p: (p["bars_since"], p["pattern"]))
    out["patterns"] = pats
    return out


def fresh_patterns(det: dict[str, Any], within: int) -> list[dict[str, Any]]:
    return [p for p in (det.get("patterns") or []) if int(p.get("bars_since", 10**9)) <= within]


def fresh_side(pats: list[dict[str, Any]]) -> str | None:
    """Taze formasyonlarin TEK tarafli yonu: LONG / SHORT / None (yok ya da iki tarafli)."""
    bull = any(p["side"] == BULL for p in pats)
    bear = any(p["side"] == BEAR for p in pats)
    if bull == bear:
        return None
    return "LONG" if bull else "SHORT"


__all__ = ["SCHEMA_VERSION", "ALL_PATTERNS", "BULL", "BEAR", "CONFIRMED", "ChartPatternConfig",
           "detect_chart_patterns", "fresh_patterns", "fresh_side",
           "DOUBLE_BOTTOM", "DOUBLE_TOP", "TRIPLE_BOTTOM", "TRIPLE_TOP",
           "INVERSE_HEAD_AND_SHOULDERS", "HEAD_AND_SHOULDERS", "DESCENDING_TRIANGLE",
           "ASCENDING_TRIANGLE", "BULL_FLAG", "BEAR_FLAG"]
