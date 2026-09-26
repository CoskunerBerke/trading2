# -*- coding: utf-8 -*-
"""MUM VARYASYONU DİLİ (DSL v1) — kullanıcının gönderdiği mum dizilimlerinin SABİT, sınanabilir tanımı ve TEK dedektörü
(2026-09-26).

Canlı defter (`candle_book`) ile laboratuvar (`candle_lab`) AYNI fonksiyonu AYNI pencereyle çağırır:
`detect_last(window, variation)` son `WINDOW` (=500) kapanmış barı görür ve yalnız son barın (teyit barı `i`) kararını
verir. Laboratuvar her tarihsel bar için o barda biten aynı pencereyi kurar; parite tanım gereği birebirdir. EMA200 de
dahil: "son 500 barın EMA200'ü" bir TANIMDIR, yaklaşıklık değildir (TradingView değeri DEĞİLDİR).

Göstergeler laboratuvarın KENDİ fonksiyonudur (`signal_lab.indicators`, pencere üzerinde); ikinci formül yoktur. Kelime →
eşik çevirisi `LEXICON`dadır; eşik vaka başına ya da sonuç görüldükten sonra SEÇİLMEZ.

Tanım (`definition`) doğrulanır, normalleştirilir ve `definition_sha` ile mühürlenir (DSL_VERSION ve WINDOW dahil). Başlık,
notlar, kaynak, onaylar mühre GİRMEZ. Dedektörün anlamı değişirse DSL_VERSION artırılır; laboratuvar kaydındaki altın
özet (golden_sha) bunu denetler.

Değerlendirme sırası (explain_last'ın "ilk başarısız koşul"u da bu sıradır):
  1. şekil  — renk ve aralığa oranlar (her mum, soldan sağa), ATR terimi olmayan ilişkiler; göstergesiz, ucuz
  2. kırılım — yalnız `break` teyidinde: ilk kırılım mı, geçersizleşti mi, i kapanışı seviyenin ötesinde mi
  3. formasyon göstergeleri — range_atr/body_atr, ATR terimli ilişkiler, mum hacim oranı (A_ref = p0-1 barının ATR14'ü)
  4. önceki hareket — prior_move
  5. bağlam — i barında rsi14, volume_ratio, atr_regime, trend, ema200_side
  6. stop — ATR14(i) geçerli, stop sonlu, > 0 ve koruyucu tarafta

Fonksiyonlar SAFTIR: dosya/ağ yok, girdiler değiştirilmez; pencere dışı okunmaz (geleceğe bakış yapısal olarak yok).
Aralığı sıfır olan barda (h == l) aralığa bölünen oranlar (gövde/fitil/kapanış konumu) NaN'dır; NaN koşulu GEÇEMEZ.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import operator
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterator

import numpy as np

DSL_VERSION = "cv_dsl_1"
#: Canlı 4h çerçeve sınırı 700, panel 600 bar yükler; pencere ikisinin de altında kalır.
WINDOW = 500
LONG, SHORT = "LONG", "SHORT"
TIMEFRAME = "4h"
#: 5m/15m: maliyet R'yi yer (laboratuvar bulgusu). 1h/1d: laboratuvarda yalnız bilgi amaçlı; defter v1'de yalnız 4h.
COST_TRAP_TFS = ("5m", "15m")
NOT_ENABLED_TFS = ("1h", "1d")
ID_PATTERN = re.compile(r"CV\d{3}_[A-Z0-9_]{2,40}")

COLORS = ("bull", "bear", "any")
#: Aralığa (h-l) bölünen oranlar — sınırlar [0, 1].
RATIO_KEYS = ("body_range", "upper_wick_range", "lower_wick_range", "close_pos")
#: A_ref'e bölünen ölçüler — sınırlar >= 0.
ATR_KEYS = ("range_atr", "body_atr")
BAR_KEYS = ("color",) + RATIO_KEYS + ATR_KEYS + ("volume_ratio",)
FIELDS = ("open", "high", "low", "close", "body", "range", "body_hi", "body_lo", "body_mid", "mid", "upper_wick",
          "lower_wick", "volume")
AGGS = ("max_high", "min_low", "max_close", "min_close", "avg_body", "avg_range", "avg_volume")
AGG_MIN, AGG_MAX = -50, -1
OPS = (">=", "<=", ">", "<")
#: Şekil DIŞI koşullar (eşleştirilmiş plasebo da bunları sağlar). Değerlendirme sırası: prior_move, sonra i barı.
CONTEXT_KEYS = ("prior_move", "rsi14", "volume_ratio", "atr_regime", "trend", "ema200_side")
TRENDS = ("with", "against", "mixed")
EMA_SIDES = ("above", "below")
CONFIRM_KINDS = ("pattern_close", "break")
STOP_ANCHORS = ("pattern", "pattern_to_confirm")
#: Laboratuvarın `LabConfig.min_risk_atr` / `max_risk_atr` değerleri.
DEFAULT_RISK_ATR_BOUNDS = (0.1, 5.0)
TOP_KEYS = ("id", "example", "title_tr", "source", "notes_tr", "readback", "approval", "retired", "supersedes", "definition")
DEF_REQUIRED = ("side", "timeframe", "bars", "confirm", "stop", "exit")
DEF_KEYS = DEF_REQUIRED + ("relations", "context", "risk_atr_bounds")
SOURCE_KINDS = ("image", "text", "example")
STAGES = ("shape", "break", "pattern", "prior", "context", "stop")

try:                                                      # içe aktarma ASLA düşmez: paper_rules bu modülü içe aktarır
    from .learn.candle_context import CandleContextConfig
    _CC: Any = CandleContextConfig()
except Exception:  # noqa: BLE001 — sözlük yalnız çeviri/gösterim içindir; dedektör kullanmaz
    _CC = None


def _cc(name: str, default: float) -> Any:
    """Katalog eşiği (`CandleContextConfig.<name>`); alan yoksa DSL v1 değeri. Eşitliği
    `test_lexicon_thresholds_equal_catalog_config` denetler."""
    try:
        return getattr(_CC, name)
    except Exception:  # noqa: BLE001
        return default


#: Kelime → SABİT kısıt. Çeviride eşik seçilmez, buradan okunur. Katalogda karşılığı olan değerler
#: `learn.candle_context.CandleContextConfig`ten gelir (test eşitliği doğrular); diğerleri DSL v1'de burada sabittir.
#: Parça anahtarları tanımdaki yerini söyler: "bar" (mum sözlüğü), "context", "relations", "stop"/"exit".
LEXICON: dict[str, dict[str, Any]] = {
    "doji": {"bar": {"body_range": [None, _cc("doji_body_ratio", 0.10)]}, "source": "CandleContextConfig.doji_body_ratio"},
    "küçük gövde": {"bar": {"body_range": [None, _cc("spinning_top_body_max", 0.35)]},
                    "source": "CandleContextConfig.spinning_top_body_max"},
    "topaç": {"bar": {"body_range": [None, _cc("spinning_top_body_max", 0.35)]}, "source": "CandleContextConfig.spinning_top_body_max"},
    "uzun gövde": {"bar": {"body_range": [_cc("belt_hold_body_min", 0.60), None]}, "source": "CandleContextConfig.belt_hold_body_min"},
    "marubozu": {"bar": {"body_range": [_cc("marubozu_body_ratio", 0.85), None]}, "source": "CandleContextConfig.marubozu_body_ratio"},
    "üst fitilsiz": {"bar": {"upper_wick_range": [None, _cc("belt_hold_wick_max", 0.05)]}, "source": "CandleContextConfig.belt_hold_wick_max"},
    "alt fitilsiz": {"bar": {"lower_wick_range": [None, _cc("belt_hold_wick_max", 0.05)]}, "source": "CandleContextConfig.belt_hold_wick_max"},
    "çekiç": {"bar": {"lower_wick_range": [_cc("hammer_wick_ratio", 0.55), None], "upper_wick_range": [None, _cc("hammer_opposite_wick_max", 0.20)]},
              "source": "CandleContextConfig.hammer_wick_ratio / hammer_opposite_wick_max"},
    "uzun alt fitil": {"bar": {"lower_wick_range": [_cc("hammer_wick_ratio", 0.55), None],
                               "upper_wick_range": [None, _cc("hammer_opposite_wick_max", 0.20)]},
                       "source": "CandleContextConfig.hammer_wick_ratio / hammer_opposite_wick_max"},
    "ters çekiç": {"bar": {"upper_wick_range": [_cc("hammer_wick_ratio", 0.55), None], "lower_wick_range": [None, _cc("hammer_opposite_wick_max", 0.20)]},
                   "source": "CandleContextConfig.hammer_wick_ratio / hammer_opposite_wick_max (ayna)"},
    "uzun üst fitil": {"bar": {"upper_wick_range": [_cc("hammer_wick_ratio", 0.55), None],
                               "lower_wick_range": [None, _cc("hammer_opposite_wick_max", 0.20)]},
                       "source": "CandleContextConfig.hammer_wick_ratio / hammer_opposite_wick_max (ayna)"},
    "büyük mum": {"bar": {"range_atr": [1.3, None]}, "source": "DSL v1 (sabit)"},
    "küçük mum": {"bar": {"range_atr": [None, 0.7]}, "source": "DSL v1 (sabit)"},
    "güçlü kapanış (boğa)": {"bar": {"close_pos": [0.70, None]}, "source": "DSL v1 (sabit)"},
    "güçlü kapanış (ayı)": {"bar": {"close_pos": [None, 0.30]}, "source": "DSL v1 (sabit)"},
    "hacimli": {"bar": {"volume_ratio": [1.5, None]}, "source": "laboratuvar hacim dilimi (>1.5x)"},
    "hacimsiz": {"bar": {"volume_ratio": [None, 0.8]}, "source": "laboratuvar hacim dilimi (<0.8x)"},
    "aşırı satım": {"context": {"rsi14": [None, 30.0]}, "source": "laboratuvar RSI dilimi (<30)"},
    "aşırı alım": {"context": {"rsi14": [70.0, None]}, "source": "laboratuvar RSI dilimi (>70)"},
    "düşüş sonrası": {"context": {"prior_move": {"bars": _cc("trend_lookback_bars", 10), "max": -_cc("trend_min_slope_atr", 0.5)}},
                      "source": "CandleContextConfig.trend_lookback_bars / trend_min_slope_atr (katalog detect_trend)"},
    "yükseliş sonrası": {"context": {"prior_move": {"bars": _cc("trend_lookback_bars", 10), "min": _cc("trend_min_slope_atr", 0.5)}},
                         "source": "CandleContextConfig.trend_lookback_bars / trend_min_slope_atr (katalog detect_trend)"},
    "trendle": {"context": {"trend": ["with"]}, "source": "laboratuvar trend bağlamı (c > ema50 > ema200)"},
    "trende karşı": {"context": {"trend": ["against"]}, "source": "laboratuvar trend bağlamı"},
    "iç mum": {"relations": ["c1.high <= c0.high", "c1.low >= c0.low"], "source": "—"},
    "yutan (gövde)": {"relations": ["c1.body_hi >= c0.body_hi", "c1.body_lo <= c0.body_lo"],
                      "source": "CandleContextConfig.engulf_min_ratio (1.0)"},
    "gövde boşluğu (yukarı)": {"relations": ["c1.body_lo > c0.body_hi"], "source": "— (not: perp'lerde gerçek boşluk nadirdir)"},
    "gövde boşluğu (aşağı)": {"relations": ["c1.body_hi < c0.body_lo"], "source": "— (not: perp'lerde gerçek boşluk nadirdir)"},
    "varsayılan çıkışlar": {"stop": {"anchor": "pattern", "atr_buffer": 0.25}, "exit": {"target_r": 2.0, "max_hold_bars": 24},
                            "source": "signal_lab: extra_events stop tamponu 0.25 ATR, LabConfig.default_rr, LabConfig.max_hold_bars"},
}

_NAN = float("nan")
_CMP = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}
_TREND_OF_LAB = {"trendle": "with", "trende_karşı": "against", "karışık": "mixed"}


# ---------------------------------------------------------------------------- tanım nesneleri
@dataclass(frozen=True)
class Term:
    """`cN.alan` (idx dolu) ya da `toplam(A..B)` (idx None; p0+A .. p0+B barları)."""

    idx: int | None
    name: str
    a: int = 0
    b: int = 0

    def render(self) -> str:
        return "c%d.%s" % (self.idx, self.name) if self.idx is not None else "%s(%d..%d)" % (self.name, self.a, self.b)


@dataclass(frozen=True)
class Relation:
    """`lhs OP k*rhs + atr_k*A_ref`. `atr_k` işaretlidir; 0 → ATR terimi yok (göstergesiz, ucuz aşama)."""

    lhs: Term
    op: str
    k: float
    rhs: Term
    atr_k: float
    text: str


@dataclass(frozen=True)
class BarSpec:
    color: str
    ratios: tuple[tuple[str, float | None, float | None], ...]
    atrs: tuple[tuple[str, float | None, float | None], ...]
    volume: tuple[float | None, float | None] | None


@dataclass(frozen=True)
class Variation:
    """Ayrıştırılmış, doğrulanmış varyasyon. `definition` normalleştirilmiş tanımdır (mühürlenen tek parça)."""

    id: str
    example: bool
    side: str
    timeframe: str
    bars: tuple[BarSpec, ...]
    relations: tuple[Relation, ...]
    context: dict
    prior_move: tuple[int, float | None, float | None] | None
    confirm_kind: str
    within_bars: int
    stop_anchor: str
    atr_buffer: float
    target_r: float | None
    max_hold_bars: int
    risk_atr_bounds: tuple[float, float]
    definition: dict
    definition_sha: str
    notes_tr: tuple[str, ...]
    title_tr: str = ""
    source: dict | None = None
    readback: dict | None = None
    approval: dict | None = None
    retired: dict | None = None
    supersedes: str | None = None

    @property
    def n_bars(self) -> int:
        return len(self.bars)


# ---------------------------------------------------------------------------- doğrulama yardımcıları
def _err(where: str, msg: str) -> ValueError:
    return ValueError("%s: %s" % (where, msg))


def _keys(d: Any, allowed: tuple, required: tuple, where: str) -> None:
    if not isinstance(d, dict):
        raise _err(where, "sözlük olmalı: %r" % (d,))
    extra = sorted(str(k) for k in d if k not in allowed)
    if extra:
        raise _err(where, "bilinmeyen anahtar %s" % extra)
    missing = [k for k in required if k not in d]
    if missing:
        raise _err(where, "eksik anahtar %s" % missing)


def _float(x: Any, where: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise _err(where, "sayı olmalı: %r" % (x,))
    v = float(x)
    if not math.isfinite(v):
        raise _err(where, "sonlu olmalı: %r" % (x,))
    return v


def _int(x: Any, where: str) -> int:
    v = _float(x, where)
    if not v.is_integer():
        raise _err(where, "tam sayı olmalı: %r" % (x,))
    return int(v)


def _range(x: Any, where: str, lo_b: float, hi_b: float) -> list:
    """[min, max] (iki uç dahil, None = açık uç). Boş aralık, min > max ve sınır dışı değer reddedilir."""
    if not isinstance(x, (list, tuple)) or len(x) != 2:
        raise _err(where, "[min, max] olmalı: %r" % (x,))
    lo = None if x[0] is None else _float(x[0], where)
    hi = None if x[1] is None else _float(x[1], where)
    if lo is None and hi is None:
        raise _err(where, "boş aralık [None, None]")
    for v in (lo, hi):
        if v is not None and not (lo_b <= v <= hi_b):
            raise _err(where, "sınır [%g, %g] dışında: %r" % (lo_b, hi_b, v))
    if lo is not None and hi is not None and lo > hi:
        raise _err(where, "min > max: %r" % (list(x),))
    return [lo, hi]


_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _date(x: Any, where: str) -> str:
    if not isinstance(x, str) or not _DATE_RE.fullmatch(x):
        raise _err(where, "YYYY-MM-DD olmalı: %r" % (x,))
    try:
        _dt.date.fromisoformat(x)
    except ValueError:
        raise _err(where, "geçersiz tarih: %r" % (x,)) from None
    return x


def _text(x: Any, where: str) -> str:
    if not isinstance(x, str) or not x.strip():
        raise _err(where, "boş olmayan metin olmalı: %r" % (x,))
    return x


# ---------------------------------------------------------------------------- ilişki dilbilgisi
_NUM = r"(?:\d+(?:\.\d*)?|\.\d+)"


def _term_re(p: str) -> str:
    return (r"(?:c(?P<{p}i>\d+)\.(?P<{p}f>[a-z_]+)|(?P<{p}g>[a-z_]+)\(\s*(?P<{p}a>-?\d+)\s*\.\.\s*(?P<{p}b>-?\d+)\s*\))"
            .format(p=p))


#: REL := TERM OP RHS ; RHS := [K "*"] TERM [("+"|"-") K "atr"] ; OP := > >= < <= ("==" YOK)
_REL_RE = re.compile(r"\s*" + _term_re("l") + r"\s*(?P<op>>=|<=|>|<)\s*(?:(?P<k>" + _NUM + r")\s*\*\s*)?"
                     + _term_re("r") + r"(?:\s*(?P<sg>[+-])\s*(?P<ka>" + _NUM + r")\s*atr)?\s*")


def _num_text(x: float) -> str:
    return repr(float(x))


def _term(m: re.Match, p: str, n: int, where: str) -> Term:
    if m.group(p + "i") is not None:
        idx, f = int(m.group(p + "i")), m.group(p + "f")
        if f not in FIELDS:
            raise _err(where, "bilinmeyen alan %r (geçerli: %s)" % (f, ", ".join(FIELDS)))
        if not 0 <= idx < n:
            raise _err(where, "mum indeksi c0..c%d dışında: c%d" % (n - 1, idx))
        return Term(idx, f)
    g, a, b = m.group(p + "g"), int(m.group(p + "a")), int(m.group(p + "b"))
    if g not in AGGS:
        raise _err(where, "bilinmeyen toplam %r (geçerli: %s)" % (g, ", ".join(AGGS)))
    if not (AGG_MIN <= a <= b <= AGG_MAX):
        raise _err(where, "toplam aralığı %d <= A <= B <= %d olmalı: %d..%d" % (AGG_MIN, AGG_MAX, a, b))
    return Term(None, g, a, b)


def _render(lhs: Term, op: str, k: float, rhs: Term, atr_k: float) -> str:
    r = rhs.render() if k == 1.0 else "%s * %s" % (_num_text(k), rhs.render())
    if atr_k:
        r += " %s %s atr" % ("+" if atr_k > 0 else "-", _num_text(abs(atr_k)))
    return "%s %s %s" % (lhs.render(), op, r)


def parse_relation(s: Any, n: int, where: str = "relation") -> Relation:
    """Katı ayrıştırma: ifade bir demete derlenir, normal metin demetten YENİDEN yazılır (mühürlenen/gösterilen metin)."""
    if not isinstance(s, str):
        raise _err(where, "metin olmalı: %r" % (s,))
    if "==" in s or "!=" in s:
        raise _err(where, "'==' / '!=' yok (yalnız > >= < <=): %r" % (s,))
    m = _REL_RE.fullmatch(s)
    if m is None:
        raise _err(where, "dilbilgisine uymuyor: %r" % (s,))
    lhs, rhs = _term(m, "l", n, where), _term(m, "r", n, where)
    k = float(m.group("k")) if m.group("k") else 1.0
    atr_k = float(m.group("ka")) if m.group("ka") else 0.0
    if m.group("sg") == "-":
        atr_k = -atr_k
    atr_k += 0.0                                          # -0.0 → 0.0
    op = m.group("op")
    return Relation(lhs, op, k, rhs, atr_k, _render(lhs, op, k, rhs, atr_k))


# ---------------------------------------------------------------------------- tanım ayrıştırma
def _timeframe(tf: Any) -> str:
    if tf in COST_TRAP_TFS:
        raise ValueError("COST_TRAP: %s mumunda maliyet R'yi yer; DSL v1 yalnız 4h kabul eder" % (tf,))
    if tf in NOT_ENABLED_TFS:
        raise ValueError("TF_NOT_ENABLED_V1: %s yalnız laboratuvarda bilgi amaçlı koşar; defter v1'de yalnız 4h" % (tf,))
    if tf != TIMEFRAME:
        raise _err("timeframe", "yalnız '4h' kabul edilir: %r" % (tf,))
    return TIMEFRAME


def _bar(b: Any, k: int) -> tuple[dict, BarSpec]:
    where = "bars[%d]" % k
    _keys(b, BAR_KEYS, (), where)
    color = b.get("color", "any")
    if not isinstance(color, str) or color not in COLORS:
        raise _err(where + ".color", "bull | bear | any olmalı (doji rengi yok; doji = body_range [None, 0.10]): %r" % (color,))
    norm: dict[str, Any] = {"color": color}
    ratios, atrs, vol = [], [], None
    for key in RATIO_KEYS:
        if key in b:
            r = norm[key] = _range(b[key], "%s.%s" % (where, key), 0.0, 1.0)
            ratios.append((key, r[0], r[1]))
    for key in ATR_KEYS:
        if key in b:
            r = norm[key] = _range(b[key], "%s.%s" % (where, key), 0.0, math.inf)
            atrs.append((key, r[0], r[1]))
    if "volume_ratio" in b:
        r = norm["volume_ratio"] = _range(b["volume_ratio"], where + ".volume_ratio", 0.0, math.inf)
        vol = (r[0], r[1])
    return norm, BarSpec(color, tuple(ratios), tuple(atrs), vol)


def _context(c: Any) -> dict:
    _keys(c, CONTEXT_KEYS, (), "context")
    out: dict[str, Any] = {}
    if "prior_move" in c:
        pm, w = c["prior_move"], "context.prior_move"
        _keys(pm, ("bars", "min", "max"), ("bars",), w)
        n = _int(pm["bars"], w + ".bars")
        if not 2 <= n <= 50:
            raise _err(w + ".bars", "2..50 olmalı: %r" % (n,))
        lo, hi = _range([pm.get("min"), pm.get("max")], w, -math.inf, math.inf)
        out["prior_move"] = {"bars": n, "min": lo, "max": hi}
    if "rsi14" in c:
        out["rsi14"] = _range(c["rsi14"], "context.rsi14", 0.0, 100.0)
    for key in ("volume_ratio", "atr_regime"):
        if key in c:
            out[key] = _range(c[key], "context." + key, 0.0, math.inf)
    if "trend" in c:
        t = c["trend"]
        if (not isinstance(t, (list, tuple)) or not t or not all(isinstance(x, str) and x in TRENDS for x in t)
                or len(set(t)) != len(t)):
            raise _err("context.trend", "%s alt kümesi olmalı (boş değil, tekrarsız): %r" % (list(TRENDS), t))
        out["trend"] = [x for x in TRENDS if x in t]
    if "ema200_side" in c:
        if not isinstance(c["ema200_side"], str) or c["ema200_side"] not in EMA_SIDES:
            raise _err("context.ema200_side", "above | below olmalı: %r" % (c["ema200_side"],))
        out["ema200_side"] = c["ema200_side"]
    return out


def _confirm(c: Any) -> dict:
    if not isinstance(c, dict) or not isinstance(c.get("kind"), str) or c.get("kind") not in CONFIRM_KINDS:
        raise _err("confirm", "{'kind': 'pattern_close'} ya da {'kind': 'break', 'within_bars': 1..3}: %r" % (c,))
    if c["kind"] == "pattern_close":
        _keys(c, ("kind",), ("kind",), "confirm")
        return {"kind": "pattern_close"}
    _keys(c, ("kind", "within_bars"), ("kind", "within_bars"), "confirm")
    w = _int(c["within_bars"], "confirm.within_bars")
    if not 1 <= w <= 3:
        raise _err("confirm.within_bars", "1..3 olmalı: %r" % (w,))
    return {"kind": "break", "within_bars": w}


def _stop(s: Any) -> dict:
    _keys(s, ("anchor", "atr_buffer"), ("anchor", "atr_buffer"), "stop")
    if not isinstance(s["anchor"], str) or s["anchor"] not in STOP_ANCHORS:
        raise _err("stop.anchor", "pattern | pattern_to_confirm olmalı: %r" % (s["anchor"],))
    buf = _float(s["atr_buffer"], "stop.atr_buffer")
    if not 0.0 <= buf <= 2.0:
        raise _err("stop.atr_buffer", "0..2 olmalı: %r" % (buf,))
    return {"anchor": s["anchor"], "atr_buffer": buf}


def _exit(e: Any) -> dict:
    _keys(e, ("target_r", "max_hold_bars"), ("target_r", "max_hold_bars"), "exit")
    tr = None if e["target_r"] is None else _float(e["target_r"], "exit.target_r")
    if tr is not None and not 0.5 <= tr <= 10.0:
        raise _err("exit.target_r", "0.5..10 ya da None olmalı: %r" % (tr,))
    hold = _int(e["max_hold_bars"], "exit.max_hold_bars")
    if not 1 <= hold <= 300:
        raise _err("exit.max_hold_bars", "1..300 olmalı: %r" % (hold,))
    return {"target_r": tr, "max_hold_bars": hold}


def _risk_bounds(x: Any) -> list:
    if not isinstance(x, (list, tuple)) or len(x) != 2:
        raise _err("risk_atr_bounds", "[lo, hi] olmalı: %r" % (x,))
    lo, hi = _float(x[0], "risk_atr_bounds"), _float(x[1], "risk_atr_bounds")
    if not 0.0 <= lo < hi <= 10.0:
        raise _err("risk_atr_bounds", "0 <= lo < hi <= 10 olmalı: %r" % (list(x),))
    return [lo, hi]


def _normalise(defn: Any) -> tuple[dict, dict]:
    """(normal tanım — mühürlenen, ayrıştırılmış parçalar). Hatalı tanım: ValueError."""
    _keys(defn, DEF_KEYS, DEF_REQUIRED, "definition")
    side = defn["side"]
    if not isinstance(side, str) or side not in (LONG, SHORT):
        raise _err("side", "LONG | SHORT olmalı (otomatik ayna yok): %r" % (side,))
    tf = _timeframe(defn["timeframe"])
    bars = defn["bars"]
    if not isinstance(bars, (list, tuple)) or not 1 <= len(bars) <= 5:
        raise _err("bars", "1..5 mum olmalı: %r" % (bars,))
    parsed_bars = [_bar(b, k) for k, b in enumerate(bars)]
    rels_raw = defn.get("relations", [])
    if not isinstance(rels_raw, (list, tuple)):
        raise _err("relations", "liste olmalı: %r" % (rels_raw,))
    rels = [parse_relation(s, len(bars), "relations[%d]" % j) for j, s in enumerate(rels_raw)]
    ctx = _context(defn.get("context", {}))
    confirm, stop, ex = _confirm(defn["confirm"]), _stop(defn["stop"]), _exit(defn["exit"])
    rb = _risk_bounds(defn.get("risk_atr_bounds", list(DEFAULT_RISK_ATR_BOUNDS)))
    norm = {"side": side, "timeframe": tf, "bars": [nb for nb, _ in parsed_bars], "relations": [r.text for r in rels],
            "context": ctx, "confirm": confirm, "stop": stop, "exit": ex, "risk_atr_bounds": rb}
    parts = {"bars": tuple(spec for _, spec in parsed_bars), "relations": tuple(rels)}
    return norm, parts


def _sha(norm: dict) -> str:
    payload = {"dsl": DSL_VERSION, "window": WINDOW, "definition": norm}
    s = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(s.encode("ascii")).hexdigest()[:16]


def definition_sha(obj: Any) -> str:
    """Tanımın mührü: sha256({dsl, window, normal tanım})[:16]. Girdi: Variation, kayıt sözlüğü ya da yalın tanım.
    Başlık/notlar/kaynak/onaylar mühre girmez; `2` ile `2.0` ve ilişkilerdeki boşluklar aynı mührü verir."""
    if isinstance(obj, Variation):
        return _sha(obj.definition)
    if isinstance(obj, dict) and "definition" in obj:
        obj = obj["definition"]
    return _sha(_normalise(obj)[0])


def _meta(d: dict) -> dict:
    src = d.get("source")
    if src is not None:
        _keys(src, ("kind", "received", "ref"), ("kind", "received", "ref"), "source")
        if src["kind"] not in SOURCE_KINDS:
            raise _err("source.kind", "%s olmalı: %r" % ("|".join(SOURCE_KINDS), src["kind"]))
        src = {"kind": src["kind"], "received": _date(src["received"], "source.received"), "ref": _text(src["ref"], "source.ref")}
    rb = d.get("readback")
    if rb is not None:
        _keys(rb, ("confirmed_by", "date"), ("confirmed_by", "date"), "readback")
        if rb["confirmed_by"] != "user":
            raise _err("readback.confirmed_by", "'user' olmalı: %r" % (rb["confirmed_by"],))
        rb = {"confirmed_by": "user", "date": _date(rb["date"], "readback.date")}
    ap = d.get("approval")
    if ap is not None:
        # run_id: onayın dayandığı laboratuvar çalıştırması (kaydın `run.github_run_id`); kapı eşleşmesini ister —
        # onay, sonucu görülen çalıştırmaya bağlıdır (aynı gün önceden verilen onay geçmez)
        _keys(ap, ("by", "date", "observation", "run_id"), ("by", "date", "observation"), "approval")
        if ap["by"] != "user":
            raise _err("approval.by", "'user' olmalı: %r" % (ap["by"],))
        if not isinstance(ap["observation"], bool):
            raise _err("approval.observation", "bool olmalı: %r" % (ap["observation"],))
        run_id = ap.get("run_id")
        if run_id is not None and (isinstance(run_id, bool) or not isinstance(run_id, (str, int)) or not str(run_id).strip()):
            raise _err("approval.run_id", "laboratuvar çalıştırma kimliği (metin/tam sayı) olmalı: %r" % (run_id,))
        ap = {"by": "user", "date": _date(ap["date"], "approval.date"), "observation": ap["observation"]}
        if run_id is not None:
            ap["run_id"] = str(run_id).strip()
    ret = d.get("retired")
    if ret is not None:
        _keys(ret, ("date", "reason_tr"), ("date", "reason_tr"), "retired")
        ret = {"date": _date(ret["date"], "retired.date"), "reason_tr": _text(ret["reason_tr"], "retired.reason_tr")}
    sup = d.get("supersedes")
    if sup is not None and (not isinstance(sup, str) or not ID_PATTERN.fullmatch(sup) or sup == d.get("id")):
        raise _err("supersedes", "başka bir geçerli id ya da None olmalı: %r" % (sup,))
    notes = d.get("notes_tr", [])
    if not isinstance(notes, (list, tuple)) or not all(isinstance(x, str) for x in notes):
        raise _err("notes_tr", "metin listesi olmalı")
    title = d.get("title_tr", "")
    if not isinstance(title, str):
        raise _err("title_tr", "metin olmalı: %r" % (title,))
    ex = d.get("example", False)
    if not isinstance(ex, bool):
        raise _err("example", "bool olmalı: %r" % (ex,))
    return {"source": src, "readback": rb, "approval": ap, "retired": ret, "supersedes": sup, "notes_tr": tuple(notes),
            "title_tr": title, "example": ex}


def parse_variation(d: Any) -> Variation:
    """Kayıt sözlüğü → dondurulmuş `Variation`. Her seviyede bilinmeyen anahtar, bozuk aralık, `==`, aralık dışı indeks,
    4h dışı dilim (5m/15m COST_TRAP, 1h/1d TF_NOT_ENABLED_V1) ... ValueError verir. Sayılar float'a çevrilir."""
    try:
        _keys(d, TOP_KEYS, ("id", "definition"), "varyasyon")
        vid = d["id"]
        if not isinstance(vid, str) or not ID_PATTERN.fullmatch(vid):
            raise _err("id", "^CV\\d{3}_[A-Z0-9_]{2,40}$ biçiminde olmalı: %r" % (vid,))
        meta = _meta(d)
        norm, parts = _normalise(d["definition"])
    except ValueError:
        raise
    except Exception as exc:                              # tip kazaları da ValueError olarak görünür (tek sözleşme)
        raise ValueError("varyasyon okunamadı: %s: %s" % (type(exc).__name__, exc)) from exc
    pm = norm["context"].get("prior_move")
    return Variation(
        id=vid, example=meta["example"], side=norm["side"], timeframe=norm["timeframe"], bars=parts["bars"],
        relations=parts["relations"], context=norm["context"],
        prior_move=(pm["bars"], pm["min"], pm["max"]) if pm else None,
        confirm_kind=norm["confirm"]["kind"], within_bars=int(norm["confirm"].get("within_bars", 0)),
        stop_anchor=norm["stop"]["anchor"], atr_buffer=norm["stop"]["atr_buffer"], target_r=norm["exit"]["target_r"],
        max_hold_bars=norm["exit"]["max_hold_bars"], risk_atr_bounds=(norm["risk_atr_bounds"][0], norm["risk_atr_bounds"][1]),
        definition=norm, definition_sha=_sha(norm), notes_tr=meta["notes_tr"], title_tr=meta["title_tr"],
        source=meta["source"], readback=meta["readback"], approval=meta["approval"], retired=meta["retired"],
        supersedes=meta["supersedes"])


# ---------------------------------------------------------------------------- pencere
@dataclass(frozen=True, eq=False)
class Window:
    """Son WINDOW kapanmış bar (kronolojik; `ts` barın AÇILIŞ zamanı). Diziler numpy; uzunluk tam WINDOW."""

    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    step_ms: int

    def __post_init__(self) -> None:
        n = len(self.ts)
        if n != WINDOW or any(len(x) != n for x in (self.o, self.h, self.l, self.c, self.v)):
            raise ValueError("pencere tam %d bar olmalı" % WINDOW)


def _num(x: Any) -> float:
    if x is None or isinstance(x, bool):
        raise TypeError("sayı değil")
    return float(x)


def window_from_rows(rows: Any, step_ms: int) -> tuple[Window | None, str | None]:
    """Satırlardan pencere; okunamazsa (None, neden). Fail-closed: yeni listelenen coin (500 bar yok) sinyal üretmez.

    NOT_ENOUGH_4H_BARS (uzunluk != WINDOW) · ROWS_NOT_CONTIGUOUS (zaman damgaları adım adım artmıyor) ·
    ROWS_UNREADABLE (OHLC sonlu değil ya da h < max(o,c) / l > min(o,c)) · VOLUME_MISSING (hacim yok/sonlu değil/negatif)."""
    try:
        rows = list(rows or [])
    except Exception:  # noqa: BLE001
        return None, "ROWS_UNREADABLE"
    if len(rows) != WINDOW:
        return None, "NOT_ENOUGH_4H_BARS"
    try:
        step = int(step_ms)
        ts = np.array([int(r["timestamp"]) for r in rows], dtype=np.int64)
    except Exception:  # noqa: BLE001
        return None, "ROWS_UNREADABLE"
    if step <= 0 or not bool(np.all(np.diff(ts) == step)):
        return None, "ROWS_NOT_CONTIGUOUS"
    try:
        ohlc = np.array([[_num(r["open"]), _num(r["high"]), _num(r["low"]), _num(r["close"])] for r in rows], dtype=float)
    except Exception:  # noqa: BLE001
        return None, "ROWS_UNREADABLE"
    o, h, lo, c = (np.ascontiguousarray(ohlc[:, k]) for k in range(4))
    if not bool(np.isfinite(ohlc).all()) or bool((h < np.maximum(o, c)).any()) or bool((lo > np.minimum(o, c)).any()):
        return None, "ROWS_UNREADABLE"
    try:
        v = np.array([_num(r["volume"]) for r in rows], dtype=float)
    except Exception:  # noqa: BLE001
        return None, "VOLUME_MISSING"
    if not bool(np.isfinite(v).all()) or bool((v < 0).any()):
        return None, "VOLUME_MISSING"
    return Window(ts=ts, o=o, h=h, l=lo, c=c, v=v, step_ms=step), None


# ---------------------------------------------------------------------------- koşul değerlendiricileri (TEK yol)
def _indicators(win: Window) -> dict[str, np.ndarray]:
    """Laboratuvarın göstergeleri, YALNIZ pencere üzerinde."""
    import pandas as pd

    from . import signal_lab as L
    return L.indicators(pd.DataFrame({"high": win.h, "low": win.l, "close": win.c, "volume": win.v}))


def _in(v: float, lo: float | None, hi: float | None) -> bool:
    """İki uç dahil; NaN her sınırda GEÇEMEZ (IEEE karşılaştırması)."""
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def _color(win: Window, b: int) -> str:
    o, c = float(win.o[b]), float(win.c[b])
    return "bull" if c > o else ("bear" if c < o else "flat")


def _ratio(win: Window, b: int, key: str) -> float:
    o, h, lo, c = float(win.o[b]), float(win.h[b]), float(win.l[b]), float(win.c[b])
    rng = h - lo
    if not rng > 0:
        return _NAN                                       # h == l: oran tanımsız
    if key == "body_range":
        return abs(c - o) / rng
    if key == "upper_wick_range":
        return (h - max(o, c)) / rng
    if key == "lower_wick_range":
        return (min(o, c) - lo) / rng
    return (c - lo) / rng                                 # close_pos


def _field(win: Window, b: int, f: str) -> float:
    if f == "volume":
        return float(win.v[b])
    o, h, lo, c = float(win.o[b]), float(win.h[b]), float(win.l[b]), float(win.c[b])
    if f == "open":
        return o
    if f == "high":
        return h
    if f == "low":
        return lo
    if f == "close":
        return c
    if f == "body":
        return abs(c - o)
    if f == "range":
        return h - lo
    if f == "body_hi":
        return max(o, c)
    if f == "body_lo":
        return min(o, c)
    if f == "body_mid":
        return (o + c) / 2.0
    if f == "mid":
        return (h + lo) / 2.0
    if f == "upper_wick":
        return h - max(o, c)
    return min(o, c) - lo                                 # lower_wick


def _agg(win: Window, p0: int, t: Term) -> float:
    s, e = p0 + t.a, p0 + t.b + 1
    if s < 0 or e > len(win.c) or s >= e:
        return _NAN
    if t.name == "max_high":
        return float(win.h[s:e].max())
    if t.name == "min_low":
        return float(win.l[s:e].min())
    if t.name == "max_close":
        return float(win.c[s:e].max())
    if t.name == "min_close":
        return float(win.c[s:e].min())
    if t.name == "avg_body":
        return float(np.abs(win.c[s:e] - win.o[s:e]).mean())
    if t.name == "avg_range":
        return float((win.h[s:e] - win.l[s:e]).mean())
    return float(win.v[s:e].mean())                      # avg_volume


def _term_value(win: Window, p0: int, t: Term) -> float:
    return _field(win, p0 + t.idx, t.name) if t.idx is not None else _agg(win, p0, t)


def _relation(win: Window, p0: int, rel: Relation, a_ref: float) -> tuple[float, float, bool]:
    lv = _term_value(win, p0, rel.lhs)
    rv = rel.k * _term_value(win, p0, rel.rhs)
    if rel.atr_k:
        rv = rv + rel.atr_k * a_ref
    return lv, rv, bool(_CMP[rel.op](lv, rv))


def _vol_ratio(win: Window, ind: dict, b: int) -> float:
    """`signal_lab.context` ile aynı formül: v[b] / önceki 20 barın hacim ortalaması."""
    va = float(ind["vol_avg"][b])
    return float(win.v[b]) / va if va > 0 else _NAN


def _trend(win: Window, ind: dict, i: int, side: str) -> str:
    """Laboratuvarın trend dilimi (`signal_lab.context`), yöne göre: with | against | mixed | unknown."""
    from . import signal_lab as L
    t = L.context(ind, {"close": win.c, "volume": win.v}, i, side)["trend"]
    return _TREND_OF_LAB.get(t, "unknown")


def _positive(x: float) -> float:
    return x if (math.isfinite(x) and x > 0) else _NAN


class _Eval:
    """Tek aday `p` için koşulların SIRALI değerlendirmesi. `detect_last`, `context_ok`, `placebo_context` ve
    `explain_last` AYNI üreticiyi kullanır; fark yalnız ilk başarısız koşulda durup durmamaktır. Gösterge çağrı başına en
    çok bir kez hesaplanır."""

    __slots__ = ("win", "var", "i", "p", "p0", "_shared", "a_ref", "a_i", "stop", "_ext")

    def __init__(self, win: Window, var: Variation, p: int, shared: dict):
        self.win, self.var, self.p, self._shared = win, var, p, shared
        self.i = len(win.c) - 1
        self.p0 = p - len(var.bars) + 1
        self.a_ref = self.a_i = self.stop = _NAN
        self._ext: tuple[float, float] | None = None

    def ind(self) -> dict[str, np.ndarray]:
        d = self._shared.get("ind")
        if d is None:
            d = self._shared["ind"] = _indicators(self.win)
        return d

    def extremes(self) -> tuple[float, float]:
        """(H, L) = formasyon barlarının (p0..p) en yükseği / en düşüğü."""
        if self._ext is None:
            self._ext = (float(self.win.h[self.p0:self.p + 1].max()), float(self.win.l[self.p0:self.p + 1].min()))
        return self._ext

    def clauses(self, stages: tuple[str, ...] = STAGES) -> Iterator[tuple]:
        """(ad, aşama, değer, geçti mi, açıklama parçası) — sabit sırada."""
        win, var, p0, i = self.win, self.var, self.p0, self.i
        if "shape" in stages:
            for k, bs in enumerate(var.bars):
                b = p0 + k
                if bs.color != "any":
                    col = _color(win, b)
                    yield ("bars[%d].color" % k, "shape", col, col == bs.color, ("eq", bs.color))
                for key, lo, hi in bs.ratios:
                    v = _ratio(win, b, key)
                    yield ("bars[%d].%s" % (k, key), "shape", v, _in(v, lo, hi), ("range", lo, hi))
            for j, rel in enumerate(var.relations):
                if not rel.atr_k:
                    lv, rv, ok = _relation(win, p0, rel, _NAN)
                    yield ("relations[%d]" % j, "shape", lv, ok, ("rel", rel, rv))
        if "break" in stages and var.confirm_kind == "break":
            hi_p, lo_p = self.extremes()
            seg, ci = win.c[self.p + 1:i], float(win.c[i])
            if var.side == LONG:
                early, void, conf, level, op = int((seg > hi_p).sum()), int((seg < lo_p).sum()), ci > hi_p, hi_p, ">"
            else:
                early, void, conf, level, op = int((seg < lo_p).sum()), int((seg > hi_p).sum()), ci < lo_p, lo_p, "<"
            yield ("break.first", "break", early, early == 0, ("count", "erken kırılım"))
            yield ("break.intact", "break", void, void == 0, ("count", "karşı uç ötesinde kapanış"))
            yield ("break.close", "break", ci, conf, ("level", op, level))
        if not any(s in stages for s in ("pattern", "prior", "context", "stop")):
            return
        ind = self.ind()
        self.a_ref = float(ind["atr"][p0 - 1]) if p0 >= 1 else _NAN
        a_ref = _positive(self.a_ref)
        if "pattern" in stages:
            for k, bs in enumerate(var.bars):
                b = p0 + k
                for key, lo, hi in bs.atrs:
                    num = float(win.h[b] - win.l[b]) if key == "range_atr" else abs(float(win.c[b] - win.o[b]))
                    v = num / a_ref
                    yield ("bars[%d].%s" % (k, key), "pattern", v, _in(v, lo, hi), ("range", lo, hi))
            for j, rel in enumerate(var.relations):
                if rel.atr_k:
                    lv, rv, ok = _relation(win, p0, rel, a_ref)
                    yield ("relations[%d]" % j, "pattern", lv, ok, ("rel", rel, rv))
            for k, bs in enumerate(var.bars):
                if bs.volume is not None:
                    v = _vol_ratio(win, ind, p0 + k)
                    yield ("bars[%d].volume_ratio" % k, "pattern", v, _in(v, *bs.volume), ("range",) + bs.volume)
        if "prior" in stages and var.prior_move is not None:
            n, lo, hi = var.prior_move
            v = (float(win.c[p0 - 1]) - float(win.c[p0 - n])) / a_ref if p0 - n >= 0 else _NAN
            yield ("context.prior_move", "prior", v, _in(v, lo, hi), ("range", lo, hi))
        if "context" in stages:
            ctx = var.context
            if "rsi14" in ctx:
                v = float(ind["rsi"][i])
                yield ("context.rsi14", "context", v, _in(v, *ctx["rsi14"]), ("range",) + tuple(ctx["rsi14"]))
            if "volume_ratio" in ctx:
                v = _vol_ratio(win, ind, i)
                yield ("context.volume_ratio", "context", v, _in(v, *ctx["volume_ratio"]), ("range",) + tuple(ctx["volume_ratio"]))
            if "atr_regime" in ctx:
                med = float(ind["atr_med"][i])
                v = float(ind["atr_pct"][i]) / med if med > 0 else _NAN
                yield ("context.atr_regime", "context", v, _in(v, *ctx["atr_regime"]), ("range",) + tuple(ctx["atr_regime"]))
            if "trend" in ctx:
                t = _trend(win, ind, i, var.side)
                yield ("context.trend", "context", t, t in ctx["trend"], ("in", tuple(ctx["trend"])))
            if "ema200_side" in ctx:
                e, ci = float(ind["ema200"][i]), float(win.c[i])
                s = "above" if ci > e else ("below" if ci < e else ("equal" if e == e else "unknown"))
                yield ("context.ema200_side", "context", s, s == ctx["ema200_side"], ("eq", ctx["ema200_side"]))
        if "stop" in stages:
            self.a_i = float(ind["atr"][i])
            a_i = _positive(self.a_i)
            yield ("stop.atr", "stop", self.a_i, a_i == a_i, ("pos",))
            end = self.p if var.stop_anchor == "pattern" else i
            ci = float(win.c[i])
            if var.side == LONG:
                st = float(win.l[p0:end + 1].min()) - var.atr_buffer * a_i
                ok = math.isfinite(st) and 0 < st < ci
            else:
                st = float(win.h[p0:end + 1].max()) + var.atr_buffer * a_i
                ok = math.isfinite(st) and st > ci and st > 0
            self.stop = st
            yield ("stop.side", "stop", st, ok, ("stop", var.side, ci))

    def hit(self) -> "Hit":
        hi_p, lo_p = self.extremes()
        ts_i = int(self.win.ts[self.i])
        return Hit(i=self.i, p=self.p, p0=self.p0, side=self.var.side, signal_ts=ts_i, signal_close_ms=ts_i + int(self.win.step_ms),
                   close=float(self.win.c[self.i]), atr_i=float(self.a_i), atr_ref=float(self.a_ref), stop=float(self.stop),
                   pattern_high=hi_p, pattern_low=lo_p)


@dataclass(frozen=True)
class Hit:
    i: int
    p: int
    p0: int
    side: str
    signal_ts: int
    signal_close_ms: int
    close: float
    atr_i: float
    atr_ref: float
    stop: float
    pattern_high: float
    pattern_low: float


def _candidates(var: Variation, i: int) -> tuple[int, ...]:
    """Formasyonun son barı p: pattern_close → i; break → i-1 .. i-within_bars (en yeni önce)."""
    if var.confirm_kind == "pattern_close":
        return (i,)
    return tuple(i - d for d in range(1, var.within_bars + 1))


def detect_last(win: Window, var: Variation) -> Hit | None:
    """Teyit barı i = WINDOW-1 için tek karar: ilk geçen aday p'nin `Hit`i ya da None. Pencere dışını OKUMAZ."""
    i = len(win.c) - 1
    shared: dict = {}
    for p in _candidates(var, i):
        ev = _Eval(win, var, p, shared)
        for cl in ev.clauses(STAGES):
            if not cl[3]:
                break
        else:
            return ev.hit()
    return None


def context_ok(win: Window, var: Variation) -> tuple[bool, float | None, float]:
    """Yalnız ŞEKİL DIŞI koşullar (prior_move + bağlam) ve stop kuralı, i = WINDOW-1 sözde formasyon sonu
    (p = i, p0 = i - n + 1) iken. Bilgi/test amaçlı; eşleştirilmiş plasebo `placebo_context` kullanır.
    Döner: (geçti mi, stop | None, ATR14(i))."""
    i = len(win.c) - 1
    ev = _Eval(win, var, i, {})
    ok = all([cl[3] for cl in ev.clauses(("prior", "context", "stop"))])
    return (True, float(ev.stop), float(ev.a_i)) if ok else (False, None, float(ev.a_i))


def placebo_context(win: Window, var: Variation, p: int | None = None) -> tuple[bool, float]:
    """Eşleştirilmiş plasebonun koşulları: YALNIZ şekil dışı olanlar — `prior_move` (sözde formasyon sonu `p`, pencere
    içi dizin; varsayılan i = WINDOW-1) ve i barındaki bağlam — ve ATR14(i) sonlu, > 0. Stop kuralı UYGULANMAZ: mum şekli
    stop mesafesini de belirlediği için plasebonun stop'unu `candle_lab` gerçek isabetlerin risk/ATR dağılımından kurar
    (aynı risk geometrisi). Döner: (geçti mi, ATR14(i))."""
    i = len(win.c) - 1
    ev = _Eval(win, var, i if p is None else int(p), {})
    a_i = float(ev.ind()["atr"][i])
    if not (math.isfinite(a_i) and a_i > 0):
        return False, a_i
    return all(cl[3] for cl in ev.clauses(("prior", "context"))), a_i


# ---------------------------------------------------------------------------- açıklama (panel, "neden sinyal yok")
def _g(x: Any) -> str:
    if isinstance(x, str):
        return x
    if x is None:
        return "-"
    x = float(x)
    return "NaN" if x != x else "%.6g" % x


def _jsonable(x: Any) -> Any:
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(float(x)) else None
    return x


def _clause_text(value: Any, ok: bool, spec: tuple) -> str:
    kind = spec[0]
    if kind == "range":
        lo, hi = spec[1], spec[2]
        v = float(value)
        if v != v:
            return "NaN"
        if ok:
            return "%s in [%s, %s]" % (_g(v), _g(lo), _g(hi))
        return "%s < %s" % (_g(v), _g(lo)) if (lo is not None and not v >= lo) else "%s > %s" % (_g(v), _g(hi))
    if kind == "eq":
        return _g(value) if ok else "%s != %s" % (_g(value), spec[1])
    if kind == "in":
        return "%s %s %s" % (_g(value), "in" if ok else "not in", list(spec[1]))
    if kind == "rel":
        rel, rv = spec[1], spec[2]
        return "%s: %s %s %s" % (rel.text, _g(value), rel.op, _g(rv))
    if kind == "count":
        return "%d %s" % (int(value), spec[1])
    if kind == "level":
        return "kapanış %s %s %s" % (_g(value), spec[1] if ok else "değil " + spec[1], _g(spec[2]))
    if kind == "pos":
        return "ATR14(i) %s" % _g(value)
    if kind == "stop":
        return "stop %s, kapanış %s (%s)" % (_g(value), _g(spec[2]), "altında olmalı" if spec[1] == LONG else "üstünde olmalı")
    return _g(value)


def _row(cl: tuple) -> dict[str, Any]:
    name, stage, value, ok, spec = cl
    return {"clause": name, "stage": stage, "value": _jsonable(value), "ok": bool(ok), "text": _clause_text(value, bool(ok), spec)}


def explain_last(win: Window, var: Variation) -> dict[str, Any]:
    """Her koşul değeri ve sonucuyla; ilk başarısız koşulun adı ve metni ("bars[1].body_range 0.52 > 0.35"). Break
    teyidinde bekleyen kırılımlar da listelenir (formasyon p, seviye, kalan bar). `detect_last` ile AYNI değerlendirici."""
    i = len(win.c) - 1
    shared: dict = {}
    cands, hit, best = [], None, None
    for p in _candidates(var, i):
        ev = _Eval(win, var, p, shared)
        rows = [_row(cl) for cl in ev.clauses(STAGES)]
        k_fail = next((k for k, r in enumerate(rows) if not r["ok"]), None)
        first = rows[k_fail] if k_fail is not None else None
        cand = {"p": p, "p0": ev.p0, "first_fail_clause": first["clause"] if first else None,
                "first_fail": "%s %s" % (first["clause"], first["text"]) if first else None, "clauses": rows}
        cands.append(cand)
        if first is None and hit is None:
            hit = ev.hit()
        prog = len(rows) if k_fail is None else k_fail
        if best is None or prog > best[0]:                 # en çok ilerleyen aday; eşitlikte en yeni p
            best = (prog, cand)
    pending = []
    if var.confirm_kind == "break":
        for d in range(var.within_bars):
            p = i - d
            ev = _Eval(win, var, p, shared)
            if not all([cl[3] for cl in ev.clauses(("shape", "pattern", "prior"))]):
                continue
            hi_p, lo_p = ev.extremes()
            seg = win.c[p + 1:i + 1]
            level, invalid = (hi_p, lo_p) if var.side == LONG else (lo_p, hi_p)
            if (var.side == LONG and ((seg > hi_p).any() or (seg < lo_p).any())) or \
                    (var.side == SHORT and ((seg < lo_p).any() or (seg > hi_p).any())):
                continue
            pending.append({"p": p, "p0": ev.p0, "level": level, "invalid_level": invalid, "bars_left": var.within_bars - d})
    top = None if hit is not None else best[1]
    return {"id": var.id, "definition_sha": var.definition_sha, "dsl_version": DSL_VERSION, "window": WINDOW, "side": var.side,
            "i": i, "signal_ts": int(win.ts[i]), "matched": hit is not None,
            "hit": {k: _jsonable(v) for k, v in asdict(hit).items()} if hit is not None else None,
            "first_fail_clause": top["first_fail_clause"] if top else None, "first_fail": top["first_fail"] if top else None,
            "candidates": cands, "pending_breaks": pending}


__all__ = ["AGGS", "BAR_KEYS", "BarSpec", "COLORS", "CONTEXT_KEYS", "DEFAULT_RISK_ATR_BOUNDS", "DSL_VERSION", "FIELDS", "Hit",
           "ID_PATTERN", "LEXICON", "RATIO_KEYS", "Relation", "STAGES", "Term", "Variation", "WINDOW", "Window", "context_ok",
           "definition_sha", "detect_last", "explain_last", "parse_relation", "parse_variation", "placebo_context", "window_from_rows"]
