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


def _anchor(bars, pt: dict[str, Any], role: str) -> dict[str, Any]:
    """Dedektorun kullandigi GERCEK pivot (teyit indeksi/zamani ile). Gorsel katman icin."""
    i = int(pt["index"])
    ci = pt.get("confirmed_at_index")
    ci = int(ci) if ci is not None else None
    return {"index": i, "timestamp": bars[i]["timestamp"], "level": round(float(pt["level"]), 10),
            "side": pt.get("side"), "role": role, "confirmed_at_index": ci,
            "confirmed_at_ts": bars[ci]["timestamp"] if ci is not None and ci < len(bars) else None}


def _seg(bars, i0: int, y0: float, i1: int, y1: float, kind: str) -> dict[str, Any]:
    """Cizgi parcasi: (i0,y0)->(i1,y1); zaman uclari bar zaman damgasidir."""
    return {"kind": kind, "i0": int(i0), "t0": bars[int(i0)]["timestamp"], "y0": round(float(y0), 10),
            "i1": int(i1), "t1": bars[int(i1)]["timestamp"], "y1": round(float(y1), 10)}


def _rec(bars, pattern: str, side: str, break_idx: int, last_confirm_idx: int, level: float,
         start_idx: int, end_idx: int, *, anchors: list[dict[str, Any]] | None = None,
         geometry: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    n = len(bars)
    rec = max(break_idx, last_confirm_idx)
    return {"pattern": pattern, "side": side, "state": CONFIRMED,
            "level": round(float(level), 10), "break_index": break_idx,
            "recognition_index": rec, "bars_since": n - 1 - rec,
            "start_ts": bars[start_idx]["timestamp"], "end_ts": bars[end_idx]["timestamp"],
            "recognition_ts": bars[rec]["timestamp"],
            # CHART ANALYSIS V1: dedektorun GERCEK dayanaklari ve cizgileri (karari etkilemez)
            "break_ts": bars[break_idx]["timestamp"], "break_close": round(float(bars[break_idx]["close"]), 10),
            "anchors": list(anchors or []), "geometry": list(geometry or [])}


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


# ---------------------------------------------------------------------------- dedektorler (ADAY GEOMETRI)
# 2026-09-22 (ortak yapi katalogu): her dedektor once KIRILISTAN BAGIMSIZ bir ADAY uretir (dayanak pivotlar, tetik
# sinir fonksiyonu, gecersizlik seviyesi, olculu hareket yuksekligi). Eski API (`detect_chart_patterns`) adaylari
# `_first_break` ile AYNEN eski kurala gore kayda cevirir — cikti bit-bit eskisidir (regresyon testi:
# tests/test_chart_patterns_candidates_v1.py). Ortak katalog (`tradingbot/structures`) ayni adaylari OKUR ve kendi durum
# makinesini (FORMING/CONFIRMED/BROKEN/EXPIRED) uygular: ikinci bir dedektor YOKTUR.
def _double_cands(bars, lows, highs, cfg, *, bottom: bool) -> list[dict[str, Any]]:
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
            else:
                neck = min(m["low"] for m in mid)
                if (1.0 - neck / base) * 100.0 < cfg.min_depth_pct:
                    continue
            role = "dip" if bottom else "tepe"
            spread = abs(p1["level"] - p2["level"]) / max(abs(p1["level"]), abs(p2["level"]), 1e-12) * 100.0
            out.append({"pattern": DOUBLE_BOTTOM if bottom else DOUBLE_TOP, "side": BULL if bottom else BEAR,
                        "above": bottom, "level_at": (lambda _j, _n=neck: _n), "scan_from": p2["index"] + 1,
                        "last_confirm_idx": p2["confirmed_at_index"], "start_idx": p1["index"],
                        "anchors": [_anchor(bars, p1, role + "1"), _anchor(bars, p2, role + "2")],
                        "geometry_at": (lambda j, _i=p1["index"], _n=neck: [_seg(bars, _i, _n, j, _n, "boyun")]),
                        "invalidation": min(p1["level"], p2["level"]) if bottom else max(p1["level"], p2["level"]),
                        "height": abs(neck - base), "quality": max(0.0, 1.0 - spread / cfg.level_tolerance_pct)})
    return out


def _doubles(bars, lows, highs, cfg, *, bottom: bool) -> list[dict[str, Any]]:
    out = []
    for c in _double_cands(bars, lows, highs, cfg, bottom=bottom):
        j = _first_break(bars, c["scan_from"], c["level_at"], above=c["above"])
        if j is None:
            continue
        out.append(_rec(bars, c["pattern"], c["side"], j, c["last_confirm_idx"], c["level_at"](j), c["start_idx"], j,
                        anchors=c["anchors"], geometry=c["geometry_at"](j)))
    return out


def _triple_cands(bars, lows, highs, cfg, *, bottom: bool) -> list[dict[str, Any]]:
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
                else:
                    neck = min(m["low"] for m in mid)
                    if (1.0 - neck / base) * 100.0 < cfg.min_depth_pct:
                        continue
                role = "dip" if bottom else "tepe"
                spread = (max(lv) - min(lv)) / max(max(abs(x) for x in lv), 1e-12) * 100.0
                out.append({"pattern": TRIPLE_BOTTOM if bottom else TRIPLE_TOP, "side": BULL if bottom else BEAR,
                            "above": bottom, "level_at": (lambda _j, _n=neck: _n), "scan_from": p3["index"] + 1,
                            "last_confirm_idx": p3["confirmed_at_index"], "start_idx": p1["index"],
                            "anchors": [_anchor(bars, p1, role + "1"), _anchor(bars, p2, role + "2"), _anchor(bars, p3, role + "3")],
                            "geometry_at": (lambda j, _i=p1["index"], _n=neck: [_seg(bars, _i, _n, j, _n, "boyun")]),
                            "invalidation": min(lv) if bottom else max(lv), "height": abs(neck - base),
                            "quality": max(0.0, 1.0 - spread / cfg.level_tolerance_pct)})
    return out


def _triples(bars, lows, highs, cfg, *, bottom: bool) -> list[dict[str, Any]]:
    out = []
    for c in _triple_cands(bars, lows, highs, cfg, bottom=bottom):
        j = _first_break(bars, c["scan_from"], c["level_at"], above=c["above"])
        if j is None:
            continue
        out.append(_rec(bars, c["pattern"], c["side"], j, c["last_confirm_idx"], c["level_at"](j), c["start_idx"], j,
                        anchors=c["anchors"], geometry=c["geometry_at"](j)))
    return out


def _hs_cands(bars, lows, highs, cfg, *, inverse: bool) -> list[dict[str, Any]]:
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
        troughs = [{"index": t1i, "level": t1, "side": "high" if inverse else "low"},
                   {"index": t2i, "level": t2, "side": "high" if inverse else "low"}]
        sdiff = abs(s1["level"] - s2["level"]) / max(abs(s1["level"]), abs(s2["level"]), 1e-12) * 100.0
        out.append({"pattern": INVERSE_HEAD_AND_SHOULDERS if inverse else HEAD_AND_SHOULDERS, "side": BULL if inverse else BEAR,
                    "above": inverse, "level_at": neck, "scan_from": s2["index"] + 1,
                    "last_confirm_idx": s2["confirmed_at_index"], "start_idx": s1["index"],
                    "anchors": [_anchor(bars, s1, "omuz1"), _anchor(bars, h, "bas"), _anchor(bars, s2, "omuz2"),
                                _anchor(bars, troughs[0], "boyun1"), _anchor(bars, troughs[1], "boyun2")],
                    "geometry_at": (lambda j, _t1i=t1i, _t1=t1, _neck=neck: [_seg(bars, _t1i, _t1, j, _neck(j), "boyun")]),
                    "invalidation": s2["level"], "height": abs(neck(h["index"]) - h["level"]),
                    "quality": max(0.0, 1.0 - sdiff / cfg.shoulder_tolerance_pct)})
    return out


def _head_shoulders(bars, lows, highs, cfg, *, inverse: bool) -> list[dict[str, Any]]:
    out = []
    for c in _hs_cands(bars, lows, highs, cfg, inverse=inverse):
        j = _first_break(bars, c["scan_from"], c["level_at"], above=c["above"])
        if j is None:
            continue
        out.append(_rec(bars, c["pattern"], c["side"], j, c["last_confirm_idx"], c["level_at"](j), c["start_idx"], j,
                        anchors=c["anchors"], geometry=c["geometry_at"](j)))
    return out


def _triangle_cands(bars, lows, highs, cfg, *, descending: bool) -> list[dict[str, Any]]:
    """Alcalan: duz destek (>=2 esit dip) + alcalan tepeler (>=2). Kirilis yonu tarafi belirler (iki tarafli aday)."""
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

            def line_before_apex(j, _line=line, _support=support, _desc=descending):
                # Egimli sinir destege/dirence ulastiktan (tepe noktasi) sonra formasyon GECERSIZ:
                # sonsuza uzatilan cizgi her kapanisi "kirilis" yapardi.
                lv = _line(j)
                if (_desc and lv <= _support) or ((not _desc) and lv >= _support):
                    return None
                return lv
            fspread = abs(f1["level"] - f2["level"]) / max(abs(f1["level"]), abs(f2["level"]), 1e-12) * 100.0
            out.append({"pattern": DESCENDING_TRIANGLE if descending else ASCENDING_TRIANGLE, "descending": descending,
                        "support": support, "line": line, "line_before_apex": line_before_apex,
                        "scan_from": last_idx + 1, "last_confirm_idx": last_conf, "start_idx": f1["index"],
                        "anchors": [_anchor(bars, f1, "duz1"), _anchor(bars, f2, "duz2"),
                                    _anchor(bars, q1, "egik1"), _anchor(bars, q2, "egik2")],
                        "geometry_at": (lambda j, _f1=f1["index"], _s=support, _q1=q1, _line=line: [
                            _seg(bars, _f1, _s, j, _s, "duz_sinir"), _seg(bars, _q1["index"], _q1["level"], j, _line(j), "egik_sinir")]),
                        "height": abs(q1["level"] - support),
                        "quality": max(0.0, 1.0 - fspread / cfg.level_tolerance_pct)})
    return out


def _triangle_cands_pit(bars, lows, highs, cfg, *, descending: bool) -> list[dict[str, Any]]:
    """KATALOG İÇİN "O ANKİ" üçgen adayları (2026-09-23, doğrulayıcı tur-3 #2). Eski üreteç (`_triangle_cands`, eski
    kayıt bit-bit aynı kalsın diye DEĞİŞMEDİ) düz çift başına YALNIZ son iki eğim pivotunu alır; yeni bir eğim pivotu
    teyit olunca önceki üçgen çıktıdan DÜŞER — teyitli-taze olsa bile (ölçüldü: yükselen üçgen teyitlerinin %18'i).
    Burada her ardışık eğim çifti kendi adayıdır ve `superseded_idx` = bir sonraki eğim pivotunun teyit indeksi: aday o
    ana kadar geçerlidir; o anda hâlâ oluşuyorsa SUPERSEDED ile sona erer, önce teyit olduysa kaydı yaşamaya devam eder."""
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
            for m in range(len(sl) - 1):
                q1, q2 = sl[m], sl[m + 1]
                if q2["index"] - q1["index"] < cfg.min_separation_bars:
                    continue
                if descending and not (q2["level"] < q1["level"] * (1 - step)):
                    continue
                if not descending and not (q2["level"] > q1["level"] * (1 + step)):
                    continue
                nxt = sl[m + 2] if m + 2 < len(sl) else None
                support = (f1["level"] + f2["level"]) / 2.0
                slope = (q2["level"] - q1["level"]) / float(max(1, q2["index"] - q1["index"]))

                def line(j, _q1=q1, _slope=slope):
                    return _q1["level"] + _slope * (j - _q1["index"])
                last_idx = max(f2["index"], q2["index"])
                last_conf = max(f2["confirmed_at_index"], q2["confirmed_at_index"])

                def line_before_apex(j, _line=line, _support=support, _desc=descending):
                    lv = _line(j)
                    if (_desc and lv <= _support) or ((not _desc) and lv >= _support):
                        return None
                    return lv
                fspread = abs(f1["level"] - f2["level"]) / max(abs(f1["level"]), abs(f2["level"]), 1e-12) * 100.0
                out.append({"pattern": DESCENDING_TRIANGLE if descending else ASCENDING_TRIANGLE, "descending": descending,
                            "support": support, "line": line, "line_before_apex": line_before_apex,
                            "scan_from": last_idx + 1, "last_confirm_idx": last_conf, "start_idx": f1["index"],
                            "superseded_idx": int(nxt["confirmed_at_index"]) if nxt is not None and nxt.get("confirmed_at_index") is not None else None,
                            "anchors": [_anchor(bars, f1, "duz1"), _anchor(bars, f2, "duz2"),
                                        _anchor(bars, q1, "egik1"), _anchor(bars, q2, "egik2")],
                            "geometry_at": (lambda j, _f1=f1["index"], _s=support, _q1=q1, _line=line: [
                                _seg(bars, _f1, _s, j, _s, "duz_sinir"), _seg(bars, _q1["index"], _q1["level"], j, _line(j), "egik_sinir")]),
                            "height": abs(q1["level"] - support),
                            "quality": max(0.0, 1.0 - fspread / cfg.level_tolerance_pct)})
    return out


def _triangles(bars, lows, highs, cfg, *, descending: bool) -> list[dict[str, Any]]:
    out = []
    for c in _triangle_cands(bars, lows, highs, cfg, descending=descending):
        support, line, lba = c["support"], c["line"], c["line_before_apex"]
        j_flat = _first_break(bars, c["scan_from"], lambda _j, _s=support: _s, above=not descending)
        j_line = _first_break(bars, c["scan_from"], lba, above=descending)
        cands = [(j, "flat") for j in [j_flat] if j is not None] + \
                [(j, "line") for j in [j_line] if j is not None]
        if not cands:
            continue
        j, which = min(cands)
        name = c["pattern"]
        if descending:
            side = BEAR if which == "flat" else BULL
        else:
            side = BULL if which == "flat" else BEAR
        lvl = support if which == "flat" else line(j)
        out.append(_rec(bars, name, side, j, c["last_confirm_idx"], lvl, c["start_idx"], j,
                        anchors=c["anchors"], geometry=c["geometry_at"](j)))
    return out


def _channel_shape(cons: list[dict[str, Any]], *, bull: bool, tol_pct: float) -> str:
    """Konsolidasyon kanalinin SEKLI — bayrak (PARALLEL) ile flama (CONVERGING) AYNI ad altinda birlestirilmez.

    Tepeler ve dipler ayri ayri en kucuk kareler dogrusuna oturtulur; her sinirin konsolidasyon boyunca TOPLAM hareketi
    baslangic genisliginin (w0) orani olarak olculur (fiyat olceginden bagimsiz). Esik eps = 0.10 x w0:
    CONVERGING: ust sinir eps'ten fazla iner VE alt sinir eps'ten fazla yukselir VE genislik en az %30 daralir.
    PARALLEL: iki sinir da direkle ters yone ya da yatay (boga: ikisi de <= +eps; ayi: ikisi de >= -eps) ve genislik
    degisimi %30'dan az. Digeri UNCLASSIFIED (bayrak SAYILMAZ). `tol_pct` imza uyumu icin tutulur."""
    n = len(cons)
    if n < 3:
        return "UNCLASSIFIED"
    xs = list(range(n))
    mx = sum(xs) / n

    def fit(ys):
        my = sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs) or 1.0
        s = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        return s, my - s * mx
    sh, ih = fit([float(b["high"]) for b in cons])
    sl, il = fit([float(b["low"]) for b in cons])
    w0 = ih - il
    w1 = (ih + sh * (n - 1)) - (il + sl * (n - 1))
    if w0 <= 0:
        return "UNCLASSIFIED"
    eps = 0.10 * w0
    mh, ml = sh * (n - 1), sl * (n - 1)
    if mh < -eps and ml > eps and w1 <= 0.7 * w0:
        return "CONVERGING"
    same_dir = (mh <= eps and ml <= eps) if bull else (mh >= -eps and ml >= -eps)
    if same_dir and abs(w1 - w0) <= 0.3 * w0:
        return "PARALLEL"
    return "UNCLASSIFIED"


def _flag_scan(bars, cfg, *, bull: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(eski kayitlar, katalog adaylari). Eski kayitlar bit-bit eskisidir; adaylar KIRILMIS (break_index dolu) ve
    SURMEKTE olan (son kapanmis bara kadar kirilmamis) konsolidasyonlari, kanal sekliyle birlikte tasir."""
    out: list[dict[str, Any]] = []
    cands: list[dict[str, Any]] = []
    n = len(bars)
    pb = cfg.flag_pole_bars
    seen: set[int] = set()
    # Üst sınır +1 (2026-09-23): son yinelemede (p = n - min - 1) yalnız q = n - 1 vardır ve kırılış barı henüz YOKTUR —
    # eski kayıt ÜRETEMEZ (çıktı bit-bit aynı), ama OLUŞAN katalog adayı üretir. Bu olmadan en sıkı yorum oluşurken
    # görünmüyor, kırılış barında "yeni" bir kayıt olarak doğuyordu (kimlik süreksizliği).
    for p in range(pb, n - cfg.flag_min_bars):
        c0, cp = bars[p - pb]["close"], bars[p]["close"]
        rise = (cp / c0 - 1.0) if bull else (1.0 - cp / c0)
        if rise * 100.0 < cfg.flag_pole_min_pct:
            continue
        pole_h = abs(cp - c0)
        pole_ext = max(b["high"] for b in bars[p - pb:p + 1]) if bull else min(b["low"] for b in bars[p - pb:p + 1])
        q_last = min(n - 1, p + cfg.flag_max_bars)
        for q in range(p + cfg.flag_min_bars, q_last + 1):
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
                    # Katalog adayı HER direk için üretilir (kanal sınıflaması ve tekilleştirme analizde — oluşan kayıtla
                    # AYNI kuralla). Eski kayıt bit-bit eskisi gibi: kırılış barı taramada ilk direğe (`seen`).
                    cands.append(_flag_cand(bars, cfg, bull=True, p=p, q=q, c0=c0, cp=cp, pole_h=pole_h, cons=cons,
                                            boundary=boundary, break_index=j))
                    if j in seen:
                        break
                    seen.add(j)
                    out.append(_rec(bars, BULL_FLAG, BULL, j, j, boundary, p - pb, j,
                                    anchors=[_anchor(bars, {"index": p - pb, "level": c0, "side": "close"}, "direk_basi"),
                                             _anchor(bars, {"index": p, "level": cp, "side": "close"}, "direk_ucu")],
                                    geometry=[_seg(bars, p - pb, c0, p, cp, "direk"), _seg(bars, p + 1, boundary, j, boundary, "bayrak_siniri")]))
                    break
                if j >= n and q == n - 1:
                    cands.append(_flag_cand(bars, cfg, bull=True, p=p, q=q, c0=c0, cp=cp, pole_h=pole_h, cons=cons,
                                            boundary=boundary, break_index=None))
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
                    cands.append(_flag_cand(bars, cfg, bull=False, p=p, q=q, c0=c0, cp=cp, pole_h=pole_h, cons=cons,
                                            boundary=boundary, break_index=j))
                    if j in seen:
                        break
                    seen.add(j)
                    out.append(_rec(bars, BEAR_FLAG, BEAR, j, j, boundary, p - pb, j,
                                    anchors=[_anchor(bars, {"index": p - pb, "level": c0, "side": "close"}, "direk_basi"),
                                             _anchor(bars, {"index": p, "level": cp, "side": "close"}, "direk_ucu")],
                                    geometry=[_seg(bars, p - pb, c0, p, cp, "direk"), _seg(bars, p + 1, boundary, j, boundary, "bayrak_siniri")]))
                    break
                if j >= n and q == n - 1:
                    cands.append(_flag_cand(bars, cfg, bull=False, p=p, q=q, c0=c0, cp=cp, pole_h=pole_h, cons=cons,
                                            boundary=boundary, break_index=None))
    return out, cands


def _flag_cand(bars, cfg, *, bull: bool, p: int, q: int, c0: float, cp: float, pole_h: float,
               cons: list[dict[str, Any]], boundary: float, break_index: int | None) -> dict[str, Any]:
    shape = _channel_shape(cons, bull=bull, tol_pct=cfg.level_tolerance_pct)
    inv = min(b["low"] for b in cons) if bull else max(b["high"] for b in cons)
    # KİMLİK DAYANAĞI: direğin ucu (boğa: en yüksek tepe, ayı: en düşük dip; eşitlikte ilk). Aynı konsolidasyonun
    # farklı direk başlangıçlı yorumları bu barı PAYLAŞIR; yorum değişse de yapının kimliği değişmez.
    win = range(p - cfg.flag_pole_bars, p + 1)
    ext_i = (max(win, key=lambda i: (bars[i]["high"], -i)) if bull else min(win, key=lambda i: (bars[i]["low"], i)))
    retr = (cp - min(b["low"] for b in cons)) / pole_h if (bull and pole_h > 0) else \
        ((max(b["high"] for b in cons) - cp) / pole_h if pole_h > 0 else 1.0)
    return {"pattern": BULL_FLAG if bull else BEAR_FLAG, "side": BULL if bull else BEAR, "above": bull,
            "level_at": (lambda _j, _b=boundary: _b), "scan_from": q + 1, "last_confirm_idx": q, "start_idx": p - cfg.flag_pole_bars,
            "anchors": [_anchor(bars, {"index": p - cfg.flag_pole_bars, "level": c0, "side": "close"}, "direk_basi"),
                        _anchor(bars, {"index": p, "level": cp, "side": "close"}, "direk_ucu")],
            "geometry_at": (lambda j, _p=p, _c0=c0, _cp=cp, _b=boundary: [
                _seg(bars, _p - cfg.flag_pole_bars, _c0, _p, _cp, "direk"), _seg(bars, _p + 1, _b, j, _b, "bayrak_siniri")]),
            "identity_anchor": _anchor(bars, {"index": ext_i, "level": bars[ext_i]["high"] if bull else bars[ext_i]["low"],
                                              "side": "high" if bull else "low"}, "direk_zirvesi" if bull else "direk_dibi"),
            "invalidation": inv, "height": pole_h, "channel": shape, "consolidation_bars": len(cons),
            "break_index": break_index, "quality": max(0.0, 1.0 - max(0.0, retr) / cfg.flag_max_retrace)}


def _flags(bars, cfg, *, bull: bool) -> list[dict[str, Any]]:
    return _flag_scan(bars, cfg, bull=bull)[0]


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



# ---------------------------------------------------------------------------- ortak katalog girdisi (2026-09-22)
def structure_candidates(bars: list[dict[str, Any]], cfg: ChartPatternConfig | None = None) -> dict[str, Any]:
    """Ortak yapi katalogunun (`tradingbot/structures`) okudugu GEOMETRI: kirilmis ya da kirilmamis her aday.

    Esikler ve pivot teyidi `detect_chart_patterns` ile AYNIDIR (ayni aday uretecleri). Donus: `rows` (taranan kapanmis
    barlar, indeksler buna gore), `candidates` (her biri: pattern, side|None, tetik sinir fonksiyon(lar)i, gecersizlik,
    olculu hareket yuksekligi, dayanak pivotlar, geometri fonksiyonu, kalite) ve yetersiz veride `reason`. Durum
    (FORMING/CONFIRMED/BROKEN/EXPIRED) BURADA verilmez: katalogun durum makinesi kapanislarla uygular."""
    cfg = cfg or ChartPatternConfig()
    rows = _clean_rows(bars)[-cfg.scan_bars:]
    out: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "policy_version": cfg.policy_version,
                           "config_id": cfg.config_id, "rows": rows, "candidates": []}
    need = 2 * cfg.swing_lookback + cfg.min_separation_bars + 2
    if len(rows) < need:
        out["reason"] = "NOT_ENOUGH_BARS"
        out["need"] = need
        return out
    sw = confirmed_swings(rows, lookback=cfg.swing_lookback)
    lows, highs = sw["lows"], sw["highs"]
    c: list[dict[str, Any]] = []
    c += _double_cands(rows, lows, highs, cfg, bottom=True)
    c += _double_cands(rows, lows, highs, cfg, bottom=False)
    c += _triple_cands(rows, lows, highs, cfg, bottom=True)
    c += _triple_cands(rows, lows, highs, cfg, bottom=False)
    c += _hs_cands(rows, lows, highs, cfg, inverse=True)
    c += _hs_cands(rows, lows, highs, cfg, inverse=False)
    c += _triangle_cands_pit(rows, lows, highs, cfg, descending=True)     # "o anki" adaylar (eski kayıt ayrı üreteçte)
    c += _triangle_cands_pit(rows, lows, highs, cfg, descending=False)
    c += _flag_scan(rows, cfg, bull=True)[1]
    c += _flag_scan(rows, cfg, bull=False)[1]
    out["candidates"] = c
    out["swings"] = sw
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
           "detect_chart_patterns", "fresh_patterns", "fresh_side", "structure_candidates",
           "DOUBLE_BOTTOM", "DOUBLE_TOP", "TRIPLE_BOTTOM", "TRIPLE_TOP",
           "INVERSE_HEAD_AND_SHOULDERS", "HEAD_AND_SHOULDERS", "DESCENDING_TRIANGLE",
           "ASCENDING_TRIANGLE", "BULL_FLAG", "BEAR_FLAG"]
