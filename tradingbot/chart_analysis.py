# -*- coding: utf-8 -*-
"""CHART ANALYSIS V1 — botun GERÇEK hesaplarını ve karar gerekçelerini mum grafiğinde görünür kılan
ortak analiz çıktısı. SAF: dosya/ağ yok, girdileri değiştirmez, hiçbir karar kapısına dokunmaz.

İlkeler (görev tanımı):
* Karar motorunda zaten üretilen değerler yeniden BAŞKA formülle hesaplanmaz: teyitli pivotlar
  `learn.multitimeframe_context.confirmed_swings` (formasyon dedektörünün kullandığı fonksiyon),
  seviye kümeleri `equal_level_clusters`, formasyonlar `chart_patterns.detect_chart_patterns`
  (dedektörün kendi dayanak pivotları ve çizgileriyle), T2/M2 kural durumu `ema200_trend.rule_state`
  (`decide` ile aynı okuma), BTC rejimi `regime_gate.btc_regime`.
* Her çizim öğesi: tür, fiyat/alt-üst, zaman uçları, dayanak pivotlar, teyit zamanı, geçersizleşme
  kuralı, kısa gerekçe ve KARAR ÜZERİNDEKİ ETKİSİ (`decision_impact`).
* Giriş ANINDAKİ değer (bellekten) ile ŞU ANKİ analiz değeri ayrı alanlardır.
* Son, henüz teyitsiz pivot `UNCONFIRMED` işaretlenir; geçmiş analiz yalnız o anda kapanmış barlarla
  kurulur (`as_of_ms`), sonraki mumlar eski kaydı değiştirmez (analysis_id ile tekilleştirme).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .candle_confirmation import closed_bars
from .chart_patterns import ChartPatternConfig, detect_chart_patterns
from . import paper_rules
from .ema200_trend import TSMOM_LOOKBACK_DAYS, _atr_last
from .learn.multitimeframe_context import MultiTimeframeConfig, confirmed_swings, equal_level_clusters
from .timeframes import DAY_MS, TF_MS  # tek kaynak (bulgu #1): panel, mum kapısı ve motor aynı tabloyu okur

SCHEMA_VERSION = "chart_analysis_v1"
BOOK_MAIN = "main"
EXCHANGE = "binance"
#: Panel piyasa adı ↔ kayıt kimliği (`identity.market_type`). Başka eşleme yok; bilinmeyen değer eşleşmez.
MARKET_TYPES = {"futures": "USDM_PERP", "spot": "SPOT"}
#: Zaman alanlarının sözleşmesi (JSON okuyucular için; bulgu #5): `timestamp`/`t0`/`t1`/`break_at` barın
#: AÇILIŞ zamanıdır (x ekseni); `confirmed_at`/`known_at` bilginin ilk bilinebildiği an = ilgili barın
#: KAPANIŞI (açılış + dilim süresi). Bir bilgi kapanmadan "biliniyor" etiketlenmez.
TIME_CONTRACT = {"timestamp": "bar açılışı (çizim x koordinatı)", "confirmed_at": "teyit barının KAPANIŞI (açılış + dilim süresi) = bilginin ilk bilinebildiği an",
                 "known_at": "kırılış/tanınma barının KAPANIŞI", "as_of": "analiz anı; tüm confirmed_at/known_at <= as_of"}


def market_type_of(market: str) -> str:
    """Panel piyasa adından kayıt kimliği; bilinmeyen ad SPOT'a düşmez (ValueError)."""
    try:
        return MARKET_TYPES[str(market)]
    except KeyError:
        raise ValueError("bilinmeyen piyasa: %r (geçerli: %s)" % (market, ", ".join(MARKET_TYPES))) from None


#: Kayda giren kapanmış işlem kuyruğu (motor ve panel AYNI sayıyı kullanır; parmak izi `history.n` içerir).
HISTORY_TAIL = 50


def plan_for_market(head: dict[str, Any] | None, market_type: str) -> dict[str, Any] | None:
    """Coin head planı PİYASAYA KESİN bağlı ve yalnız GEÇERLİYSE (motor ve panel aynı kural; başka piyasanın planı alınmaz)."""
    h = head or {}
    p = h.get("spot_plan") if str(market_type) == "SPOT" else h.get("futures_plan")
    return p if isinstance(p, dict) and p.get("valid") else None

USED_IN_DECISION = "USED_IN_DECISION"      # bu öğe (ya da kaynağı) gerçek bir kapı/kuralda kullanıldı
OBSERVATION_ONLY = "OBSERVATION_ONLY"      # yalnız gözlem: hiçbir kapı bunu okumaz (ya da mod SHADOW/OFF)
UNCONFIRMED = "UNCONFIRMED"                # henüz teyitsiz (sağ tarafta yeterli kapanmış bar yok)
INVALID = "INVALID"                        # ihlal edilmiş / geçersizleşmiş

LAYER_LEVELS, LAYER_ZONES, LAYER_TREND, LAYER_PATTERNS, LAYER_TRADES, LAYER_INDICATORS = (
    "levels", "zones", "trend", "patterns", "trades", "indicators")

_PATTERN_TR = {"double_bottom": "Çift dip", "double_top": "Çift tepe", "triple_bottom": "Üçlü dip", "triple_top": "Üçlü tepe",
               "inverse_head_and_shoulders": "Ters omuz-baş-omuz", "head_and_shoulders": "Omuz-baş-omuz",
               "descending_triangle": "Alçalan üçgen", "ascending_triangle": "Yükselen üçgen",
               "bull_flag": "Boğa bayrağı", "bear_flag": "Ayı bayrağı"}
_GEOM_TR = {"boyun": "boyun çizgisi", "duz_sinir": "düz sınır", "egik_sinir": "eğik sınır", "direk": "direk",
            "bayrak_siniri": "bayrak sınırı"}
_BLOCK_TR = {"NO_TRIGGER": "tetik gelmedi (fiyat planlanan girişe ulaşmadı)", "NEGATIVE_NET_EDGE": "ekonomik kapı: maliyet sonrası beklenti eksi",
             "RED_TEAM_HARD_VETO": "sert güvenlik vetosu", "CHIEF_BLOCKED": "baş yönetici izin vermedi",
             "TOTAL_OPEN_RISK": "toplam açık risk tavanı", "MAX_POSITION_PCT": "tek pozisyon tavanı",
             "STEP_ZERO_QTY": "borsa adım büyüklüğü: miktar sıfıra yuvarlandı", "MIN_NOTIONAL": "borsa asgari tutar"}


# ----------------------------------------------------------------------------- kimlik
@lru_cache(maxsize=1)
def code_sha() -> str:
    """Çalışan kodun commit'i: önce ortam (`TRADINGBOT_CODE_SHA`), sonra git; bulunamazsa 'unknown'."""
    env = os.environ.get("TRADINGBOT_CODE_SHA")
    if env:
        return str(env).strip()[:40]
    try:
        root = Path(__file__).resolve().parents[1]
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:40]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def config_hash(v3: Any) -> str:
    """Analizi etkileyen config alt kümesinin kararlı özeti (yalnız gösterim/tekilleştirme için)."""
    def _sec(name: str) -> dict:
        s = getattr(v3, name, None)
        if s is None:
            return {}
        return {k: v for k, v in vars(s).items() if not k.startswith("_")}
    payload = {"entry_selectivity": _sec("entry_selectivity"), "strategy_paper": _sec("strategy_paper"),
               "entry_universe": _sec("entry_universe"), "chart_analysis": _sec("chart_analysis")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _f(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def _iso(ms: Any) -> str | None:
    v = _f(ms)
    if v is None:
        return None
    return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _ms(iso_or_ms: Any) -> int | None:
    """ISO metni / epoch ms / datetime → epoch ms (parmak izi ve gösterim için tek normalizasyon)."""
    if iso_or_ms in (None, "") or isinstance(iso_or_ms, bool):
        return None
    if isinstance(iso_or_ms, (int, float)):
        return int(iso_or_ms)
    if hasattr(iso_or_ms, "timestamp") and callable(iso_or_ms.timestamp):
        try:
            return int(iso_or_ms.timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            return None
    try:
        return int(datetime.fromisoformat(str(iso_or_ms).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _daily_close(open_ms: Any) -> int | None:
    """Günlük kural barının KAPANIŞI: kural değeri (EMA200 / 28g referans) ancak günlük bar kapanınca bilinir."""
    v = _ms(open_ms)
    return (int(v) + DAY_MS) if v is not None else None


def bars_from_frame(frame: Any, *, tail: int = 400) -> list[dict[str, Any]]:
    """DataFrame → bar sözlükleri (zaman damgası ms). Gösterge sütunları varsa taşınır (ısınma korunur)."""
    if frame is None or len(frame) == 0:
        return []
    cols = [c for c in ("timestamp", "open", "high", "low", "close", "volume", "ema20", "ema50", "ema200", "atr14") if c in frame.columns]
    out = []
    for r in frame[cols].tail(int(tail)).itertuples(index=False):
        d = dict(zip(cols, r))
        try:
            d["timestamp"] = int(d["timestamp"])
        except (TypeError, ValueError):
            continue
        out.append(d)
    return out


def closed_bars_at(bars: list[dict[str, Any]], *, as_of_ms: int, tf: str) -> list[dict[str, Any]]:
    """`as_of_ms` anında KAPANMIŞ barlar — geçmiş analiz için tek yol (geleceğe bakma yok)."""
    return closed_bars(bars, now_ms=int(as_of_ms), tf=tf)


# ----------------------------------------------------------------------------- pivotlar
def pivots(bars: list[dict[str, Any]], *, lookback: int, tf_ms: int) -> dict[str, Any]:
    """Teyitli fraktal pivotlar (formasyon dedektörüyle AYNI fonksiyon) + son TEYİTSİZ uç.

    `confirmed_at_ts` = teyit barının (i+lookback) KAPANIŞI = açılış + `tf_ms`: pivot ancak o an bilinebilir.
    2e31926 teyit barının açılışını yazıyordu (bir mum erken; bulgu #5). Açılış ayrıca `confirmed_bar_open_ts`."""
    k = max(1, int(lookback))
    step = int(tf_ms)
    sw = confirmed_swings(bars, lookback=k)
    conf = sorted(sw["highs"] + sw["lows"], key=lambda p: p["index"])
    for p in conf:
        ci = int(p["confirmed_at_index"])
        p["confirmed_bar_open_ts"] = int(bars[ci]["timestamp"]) if ci < len(bars) else None
        p["confirmed_at_ts"] = (int(bars[ci]["timestamp"]) + step) if ci < len(bars) else None
    pending = None
    if bars:
        start = (conf[-1]["index"] + 1) if conf else 0
        seg = bars[start:]
        if seg:
            want_high = (conf[-1]["side"] == "low") if conf else True
            if want_high:
                i = max(range(start, len(bars)), key=lambda j: bars[j]["high"])
                pending = {"index": i, "timestamp": bars[i]["timestamp"], "level": float(bars[i]["high"]), "side": "high"}
            else:
                i = min(range(start, len(bars)), key=lambda j: bars[j]["low"])
                pending = {"index": i, "timestamp": bars[i]["timestamp"], "level": float(bars[i]["low"]), "side": "low"}
            pending["bars_needed"] = max(0, k - (len(bars) - 1 - pending["index"]))
    return {"lookback": k, "confirmed": conf, "pending": pending}


def _anchor_of(bars: list[dict[str, Any]], p: dict[str, Any], tf_ms: int) -> dict[str, Any]:
    """Dayanak: `timestamp` pivot barının açılışı (x), `confirmed_at` teyit barının KAPANIŞI (bilinebilir an)."""
    ci = p.get("confirmed_at_index")
    ok = ci is not None and int(ci) < len(bars)
    return {"index": int(p["index"]), "timestamp": int(bars[int(p["index"])]["timestamp"]), "price": round(float(p["level"]), 10),
            "side": p.get("side"), "confirmed_at": (int(bars[int(ci)]["timestamp"]) + int(tf_ms)) if ok else None,
            "confirmed_bar_open": int(bars[int(ci)]["timestamp"]) if ok else None}


# ----------------------------------------------------------------------------- seviyeler / bölgeler
def level_elements(bars: list[dict[str, Any]], piv: dict[str, Any], *, atr: float | None, tolerance_atr: float,
                   timeframe: str, last_close: float | None, tf_ms: int) -> list[dict[str, Any]]:
    """Teyitli pivotlardan küme (≥2 dayanak → BÖLGE, alt/üst = gözlenen üyelerin gerçek min/max) ve
    tekil pivot işaretleri. Dayanak yoksa bölge ÜRETİLMEZ; ATR yoksa küme yok (tolerans uydurulmaz)."""
    out: list[dict[str, Any]] = []
    conf = piv.get("confirmed") or []
    by_ts = {int(p["timestamp"]): p for p in conf}
    clusters = equal_level_clusters(conf, atr=atr, tolerance_atr=tolerance_atr) if atr else []
    used: set[int] = set()
    for n, c in enumerate(clusters):
        members = [by_ts[t] for t in c["member_timestamps"] if t in by_ts]
        if len(members) < 2:
            continue
        for m in members:
            used.add(int(m["timestamp"]))
        lv = [float(m["level"]) for m in members]
        lower, upper = min(lv), max(lv)
        conf_ts = max(int(m.get("confirmed_at_ts") or 0) for m in members) or None   # son üyenin teyit barı KAPANIŞI
        if last_close is not None and upper < last_close:
            kind, lab = "support", "Destek bölgesi"
        elif last_close is not None and lower > last_close:
            kind, lab = "resistance", "Direnç bölgesi"
        else:
            kind, lab = "zone", "Fiyat bölgesi (fiyat içinde)"
        sides = sorted({str(m.get("side")) for m in members})
        out.append({
            "id": "zone:%d" % n, "layer": LAYER_ZONES, "kind": kind, "label_tr": "%s %.6g–%.6g" % (lab, lower, upper),
            "price": round(float(c["level"]), 10), "lower": round(lower, 10), "upper": round(upper, 10),
            "t0": int(min(m["timestamp"] for m in members)), "t1": None,
            "anchors": [_anchor_of(bars, m, tf_ms) for m in members], "confirmed_at": conf_ts,
            "timeframe": timeframe, "n_anchors": len(members),
            "invalidation_tr": "Bölgenin dışında iki ardışık kapanış bölgeyi kırar (gösterim kuralı; kapı değil).",
            "rationale_tr": "%d teyitli %s pivotu %.2f×ATR toleransında kümelendi; alt/üst = gözlenen pivotların gerçek min/max değeri." % (
                len(members), "/".join("tepe" if s == "high" else "dip" for s in sides), float(tolerance_atr)),
            "decision_impact": OBSERVATION_ONLY,
            "source": {"module": "learn.multitimeframe_context", "function": "equal_level_clusters",
                       "params": {"tolerance_atr": float(tolerance_atr), "atr": round(float(atr), 10) if atr else None,
                                  "swing_lookback": piv.get("lookback")}},
        })
    for n, p in enumerate(conf):
        if int(p["timestamp"]) in used:
            continue
        side = "tepe" if p["side"] == "high" else "dip"
        out.append({
            "id": "pivot:%d" % n, "layer": LAYER_LEVELS, "kind": "pivot_%s" % p["side"], "label_tr": "Teyitli %s %.6g" % (side, float(p["level"])),
            "price": round(float(p["level"]), 10), "lower": None, "upper": None, "t0": int(p["timestamp"]), "t1": None,
            "anchors": [_anchor_of(bars, p, tf_ms)], "confirmed_at": p.get("confirmed_at_ts"), "timeframe": timeframe, "n_anchors": 1,
            "invalidation_tr": "Tekil pivot; bölge değildir (ikinci dayanak yok).",
            "rationale_tr": "Her iki yanında %d kapanmış bar ile teyit edilmiş fraktal %s (formasyon dedektörünün kullandığı pivot)." % (int(piv.get("lookback") or 0), side),
            "decision_impact": OBSERVATION_ONLY,
            "source": {"module": "learn.multitimeframe_context", "function": "confirmed_swings", "params": {"lookback": piv.get("lookback")}},
        })
    pend = piv.get("pending")
    if pend:
        side = "tepe" if pend["side"] == "high" else "dip"
        out.append({
            "id": "pivot:pending", "layer": LAYER_LEVELS, "kind": "pivot_pending", "label_tr": "Teyitsiz %s %.6g (%d bar eksik)" % (side, float(pend["level"]), int(pend["bars_needed"])),
            "price": round(float(pend["level"]), 10), "lower": None, "upper": None, "t0": int(pend["timestamp"]), "t1": None,
            "anchors": [{"index": int(pend["index"]), "timestamp": int(pend["timestamp"]), "price": round(float(pend["level"]), 10), "side": pend["side"], "confirmed_at": None}],
            "confirmed_at": None, "timeframe": timeframe, "n_anchors": 1,
            "invalidation_tr": "Sağ tarafta %d kapanmış bar gelmeden pivot sayılmaz; daha uç bir bar gelirse taşınır." % int(piv.get("lookback") or 0),
            "rationale_tr": "Son teyitli pivottan sonraki en uç bar — HENÜZ TEYİTSİZ, hiçbir hesapta kullanılmaz.",
            "decision_impact": UNCONFIRMED,
            "source": {"module": "chart_analysis", "function": "pivots", "params": {"lookback": piv.get("lookback")}},
        })
    return out


# ----------------------------------------------------------------------------- trend çizgileri
def _line_at(a: dict[str, Any], b: dict[str, Any], j: int) -> float:
    slope = (float(b["level"]) - float(a["level"])) / float(max(1, int(b["index"]) - int(a["index"])))
    return float(a["level"]) + slope * (j - int(a["index"]))


def trendline_elements(bars: list[dict[str, Any]], piv: dict[str, Any], *, touch_tolerance_pct: float, timeframe: str,
                       tf_ms: int) -> list[dict[str, Any]]:
    """Geçmişte teyit edilmiş en az iki pivotu bağlayan eğik çizgiler: son iki teyitli dip (destek
    trendi) ve son iki teyitli tepe (direnç trendi). Eğim, ek temaslar ve kapanış ihlalleri hesaplanır.
    Hiçbir kapı trend çizgisi okumaz → yalnız gözlem."""
    out: list[dict[str, Any]] = []
    conf = piv.get("confirmed") or []
    n = len(bars)
    if n < 3:
        return out
    tol = max(0.0, float(touch_tolerance_pct)) / 100.0
    for side, kind, lab in (("low", "trend_support", "Yükselen/alçalan destek trendi"), ("high", "trend_resistance", "Direnç trendi")):
        pts = [p for p in conf if p.get("side") == side]
        if len(pts) < 2:
            continue
        a, b = pts[-2], pts[-1]
        if int(b["index"]) <= int(a["index"]):
            continue
        slope_bar = (float(b["level"]) - float(a["level"])) / float(int(b["index"]) - int(a["index"]))
        per_day = slope_bar * (86_400_000.0 / float(tf_ms)) if tf_ms else None
        touches, breaks = [], []
        for j in range(int(a["index"]), n):
            lv = _line_at(a, b, j)
            if j in (int(a["index"]), int(b["index"])):
                continue
            bar = bars[j]
            # `timestamp` = temas/ihlal barının açılışı (x); `known_at` = o barın KAPANIŞI (kapanış ihlali ancak o an bilinir)
            if side == "low":
                if abs(float(bar["low"]) - lv) <= tol * lv:
                    touches.append({"index": j, "timestamp": int(bar["timestamp"]), "known_at": int(bar["timestamp"]) + int(tf_ms), "price": round(float(bar["low"]), 10)})
                if j > int(b["index"]) and float(bar["close"]) < lv * (1 - tol):
                    breaks.append({"index": j, "timestamp": int(bar["timestamp"]), "known_at": int(bar["timestamp"]) + int(tf_ms), "close": round(float(bar["close"]), 10), "line": round(lv, 10)})
            else:
                if abs(float(bar["high"]) - lv) <= tol * lv:
                    touches.append({"index": j, "timestamp": int(bar["timestamp"]), "known_at": int(bar["timestamp"]) + int(tf_ms), "price": round(float(bar["high"]), 10)})
                if j > int(b["index"]) and float(bar["close"]) > lv * (1 + tol):
                    breaks.append({"index": j, "timestamp": int(bar["timestamp"]), "known_at": int(bar["timestamp"]) + int(tf_ms), "close": round(float(bar["close"]), 10), "line": round(lv, 10)})
        last_j = n - 1
        y1 = _line_at(a, b, last_j)
        status = "BROKEN" if breaks else "INTACT"
        direction = "yükselen" if slope_bar > 0 else ("alçalan" if slope_bar < 0 else "yatay")
        out.append({
            "id": "trend:%s" % side, "layer": LAYER_TREND, "kind": kind,
            "label_tr": "%s (%s) — %s" % (lab, direction, "kırıldı %s" % (_iso(breaks[0]["known_at"]) or "") if breaks else "sağlam"),
            "price": round(y1, 10), "lower": None, "upper": None,
            "t0": int(bars[int(a["index"])]["timestamp"]), "y0": round(float(a["level"]), 10),
            "t1": int(bars[last_j]["timestamp"]), "y1": round(y1, 10),
            "slope_per_bar": round(slope_bar, 10), "slope_per_day": round(per_day, 10) if per_day is not None else None,
            "anchors": [_anchor_of(bars, a, tf_ms), _anchor_of(bars, b, tf_ms)], "confirmed_at": b.get("confirmed_at_ts"),
            "broken_at": breaks[0]["known_at"] if breaks else None,
            "touches": touches, "breaks": breaks, "status": status, "timeframe": timeframe,
            "invalidation_tr": "Çizginin %s tarafında %.2f%% toleransla bir kapanış = ihlal." % ("altında" if side == "low" else "üstünde", touch_tolerance_pct),
            "rationale_tr": "Son iki teyitli %s pivotu (%s → %s) bağlandı; %d ek temas, %d ihlal." % (
                "dip" if side == "low" else "tepe", _iso(bars[int(a["index"])]["timestamp"]), _iso(bars[int(b["index"])]["timestamp"]), len(touches), len(breaks)),
            "decision_impact": INVALID if breaks else OBSERVATION_ONLY,
            "source": {"module": "chart_analysis", "function": "trendline_elements", "params": {"touch_tolerance_pct": touch_tolerance_pct, "anchors": "son 2 teyitli pivot"}},
        })
    return out


# ----------------------------------------------------------------------------- formasyonlar
def pattern_elements(bars: list[dict[str, Any]], det: dict[str, Any], *, chart_mode: str, fresh_within: int | None,
                     timeframe: str, tf_ms: int) -> list[dict[str, Any]]:
    """Dedektörün GERÇEK pivotları/çizgileri: `anchors` + `geometry` kayıttan okunur (yeniden tahmin yok).

    Zaman sözleşmesi (bulgu #5): dedektör kaydındaki `*_ts` alanları bar AÇILIŞ zamanlarıdır (çizim x
    koordinatı; paylaşılan dedektör DEĞİŞTİRİLMEDİ). Bilginin ilk bilinebildiği an burada, tüketicide
    türetilir: `confirmed_at` = tanınma barının KAPANIŞI, `break_known_at` = kırılış barının KAPANIŞI,
    dayanak `confirmed_at` = teyit barının KAPANIŞI."""
    out: list[dict[str, Any]] = []
    mode = str(chart_mode or "OFF").upper()
    step = int(tf_ms)

    def _close(ts: Any) -> int | None:
        v = _ms(ts)
        return (int(v) + step) if v is not None else None

    for n, p in enumerate(det.get("patterns") or []):
        fresh = fresh_within is not None and int(p.get("bars_since", 10**9)) <= int(fresh_within)
        if mode == "ENFORCE" and fresh:
            impact, imp_tr = USED_IN_DECISION, "kararda kullanıldı (ENFORCE, taze)"
        elif mode == "ENFORCE":
            impact, imp_tr = OBSERVATION_ONLY, "ENFORCE ama taze değil → kapı okumadı"
        elif mode == "SHADOW":
            impact, imp_tr = OBSERVATION_ONLY, "yalnız gözlem (SHADOW: hüküm kaydedilir, kararı etkilemez)"
        else:
            impact, imp_tr = OBSERVATION_ONLY, "kapı kapalı (OFF)"
        name_tr = _PATTERN_TR.get(str(p.get("pattern")), str(p.get("pattern")))
        side_tr = "yükseliş" if str(p.get("side")).upper() == "BULL" else "düşüş"
        anchors = [{"index": a.get("index"), "timestamp": a.get("timestamp"), "price": a.get("level"), "side": a.get("side"),
                    "role": a.get("role"), "confirmed_at": _close(a.get("confirmed_at_ts")), "confirmed_bar_open": a.get("confirmed_at_ts")}
                   for a in (p.get("anchors") or [])]
        base = {"pattern": p.get("pattern"), "pattern_tr": name_tr, "side": p.get("side"), "state": p.get("state"),
                "bars_since": p.get("bars_since"), "fresh": fresh,
                "recognition_at": p.get("recognition_ts"), "recognition_known_at": _close(p.get("recognition_ts")),
                "break_at": p.get("break_ts"), "break_known_at": _close(p.get("break_ts")), "break_close": p.get("break_close"), "timeframe": timeframe,
                "anchors": anchors, "confirmed_at": _close(p.get("recognition_ts")), "decision_impact": impact,
                "decision_impact_tr": imp_tr,
                "invalidation_tr": "Kırılış barının kapanışı sınırın gerisine dönerse formasyon gösterimde geçersiz sayılır (kapı bunu izlemez).",
                "source": {"module": "chart_patterns", "function": "detect_chart_patterns",
                           "params": {"policy_version": det.get("policy_version"), "config_id": det.get("config_id"), "chart_mode": mode}}}
        geoms = p.get("geometry") or []
        for g_i, g in enumerate(geoms):
            out.append({**base, "id": "pattern:%d:%d" % (n, g_i), "layer": LAYER_PATTERNS, "kind": "pattern_line",
                        "label_tr": "%s (%s) — %s" % (name_tr, side_tr, _GEOM_TR.get(str(g.get("kind")), str(g.get("kind")))),
                        "price": g.get("y1"), "lower": None, "upper": None,
                        "t0": g.get("t0"), "y0": g.get("y0"), "t1": g.get("t1"), "y1": g.get("y1"),
                        "rationale_tr": "%s: %d dayanak pivot; kırılış barı kapanışı %s (kapanış %.6g); tanınma (bar kapanışı) %s." % (
                            name_tr, len(anchors), _iso(_close(p.get("break_ts"))) or "?", float(p.get("break_close") or 0), _iso(_close(p.get("recognition_ts"))) or "?")})
        if not geoms:
            out.append({**base, "id": "pattern:%d" % n, "layer": LAYER_PATTERNS, "kind": "pattern_level",
                        "label_tr": "%s (%s) seviye %.6g" % (name_tr, side_tr, float(p.get("level") or 0)),
                        "price": p.get("level"), "lower": None, "upper": None, "t0": p.get("start_ts"), "t1": p.get("end_ts"),
                        "rationale_tr": "%s: kırılış seviyesi %.6g." % (name_tr, float(p.get("level") or 0))})
    return out


# ----------------------------------------------------------------------------- işlem katmanı
def mark_element(*, book_id: str, position: dict[str, Any] | None, mark_price: float | None, at_ms: int | None,
                 price_source: dict[str, Any] | None = None, live: bool = False) -> dict[str, Any] | None:
    """Güncel fiyat + açık K/Z öğesi (`kind=mark`). TEK formül: BRÜT K/Z = yön × (fiyat − ortalama giriş) × miktar; ücret ve
    fonlama GİRMEZ (gerçekleşmiş K/Z defterdedir). Motor kaydı (canlı tik) ve panelin canlı katmanı (son mum kapanışı)
    aynı fonksiyonu kullanır; fiyatın GERÇEK kaynağı `price_source` ile etiketlenir — mum kapanışı doğrulanmış borsa
    mark fiyatı DEĞİLDİR ve öyle sunulmaz."""
    if not position or mark_price is None:
        return None
    entry = _f(position.get("entry_avg") if position.get("entry_avg") is not None else position.get("entry"))
    qty = _f(position.get("qty") if position.get("qty") not in (None, "") else (position.get("quantity") if position.get("quantity") not in (None, "") else position.get("units")))
    if entry is None or not qty:
        return None
    side = str(position.get("side") or "").upper()
    sign = 1.0 if side == "LONG" else -1.0
    pnl = sign * (float(mark_price) - entry) * qty
    tid = "%s:%s" % (book_id, position.get("id") or position.get("trade_id") or "open")
    ps = dict(price_source or {"kind": "unknown"})
    kind = str(ps.get("kind") or "unknown")
    if kind == "candle_close":
        what = "Son mum kapanışı %.6g (%s; borsa mark fiyatı DEĞİL)" % (float(mark_price), "kapanmamış bar" if not ps.get("bar_closed") else "kapanmış bar")
        why = "Panelin canlı fiyatı: %s dosyasındaki son mumun kapanışı (%s). Doğrulanmış borsa mark fiyatı değildir; açık K/Z bu fiyatla BRÜT hesaplanır." % (
            ps.get("file") or "mum", _iso(ps.get("bar_open_ms")) or "?")
    elif kind == "ticker_last":
        what = "İşaret fiyatı %.6g (canlı tik)" % float(mark_price)
        why = "Motorun tur anındaki son işlem fiyatı (ticker last); açık K/Z bu fiyatla BRÜT hesaplanır, gerçekleşmiş K/Z'den ayrıdır."
    else:
        what = "İşaret fiyatı %.6g (kaynak: %s)" % (float(mark_price), kind)
        why = "Güncel fiyat; açık K/Z BRÜT (ücret/fonlama hariç), gerçekleşmiş K/Z'den ayrıdır."
    return {"id": "pos_mark:%s" % tid, "layer": LAYER_TRADES, "kind": "mark", "trade_id": tid, "side": side, "t0": _ms(position.get("opened_at")), "t1": None,
            "anchors": [], "confirmed_at": int(at_ms) if at_ms is not None else None, "decision_impact": USED_IN_DECISION, "invalidation_tr": "—",
            "price": float(mark_price), "unrealized_pnl_gross": round(pnl, 10), "price_source": ps, "live": bool(live),
            "label_tr": "%s · açık K/Z %+.4g USDT (brüt)%s" % (what, pnl, " (canlı)" if live else ""), "rationale_tr": why,
            "source": {"module": "chart_analysis", "function": "mark_element", "params": {"trade_id": tid, "price_source": kind}, "live": bool(live),
                       "live_at": _iso(at_ms) if live else None}}


def trade_elements(*, book_id: str, position: dict[str, Any] | None, history: list[dict[str, Any]], plan: dict[str, Any] | None,
                   as_of_ms: int | None, mark_price: float | None, mark_source: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Seçili defterin GERÇEK giriş/çıkış işaretleri, açık pozisyonun giriş/stop/hedefleri ve (ana botta) planı.
    Plan fiyatı, gerçekleşen fiyat ve güncel işaret fiyatı ayrı etiketlenir. TP yoksa 'TP yok' yazılır."""
    out: list[dict[str, Any]] = []
    for h in history or []:
        o_ms, c_ms = _ms(h.get("opened_at")), _ms(h.get("closed_at"))
        if as_of_ms is not None and o_ms is not None and o_ms > as_of_ms:
            continue
        tid = "%s:%s" % (book_id, h.get("id"))
        entry, exitp = _f(h.get("entry")), _f(h.get("exit_price"))
        side = str(h.get("side") or "").upper()
        if entry is not None and o_ms is not None:
            out.append({"id": "trade_entry:%s" % tid, "layer": LAYER_TRADES, "kind": "trade_entry", "trade_id": tid, "side": side,
                        "label_tr": "Gerçek giriş %s %.6g" % (side, entry), "price": entry, "t0": o_ms, "t1": None,
                        "anchors": [], "confirmed_at": o_ms, "decision_impact": USED_IN_DECISION,
                        "rationale_tr": "Defter kaydı: %s açılış (%s)." % (tid, h.get("opened_at")), "invalidation_tr": "—",
                        "source": {"module": "accounting.futures_ledger", "function": "history", "params": {"trade_id": tid}}})
        closed_visible = c_ms is not None and (as_of_ms is None or c_ms <= as_of_ms)
        if closed_visible and exitp is not None:
            out.append({"id": "trade_exit:%s" % tid, "layer": LAYER_TRADES, "kind": "trade_exit", "trade_id": tid, "side": side,
                        "label_tr": "Gerçek çıkış %.6g (%s, %+.4g USDT, %+.2fR)" % (exitp, h.get("exit_reason"), _f(h.get("net_pnl")) or 0.0, _f(h.get("r_multiple")) or 0.0),
                        "price": exitp, "t0": c_ms, "t1": None, "anchors": [], "confirmed_at": c_ms, "decision_impact": USED_IN_DECISION,
                        "net_pnl": _f(h.get("net_pnl")), "r_multiple": _f(h.get("r_multiple")), "exit_reason": h.get("exit_reason"),
                        "rationale_tr": "Defter kaydı: %s kapanış (%s), neden %s." % (tid, h.get("closed_at"), h.get("exit_reason")), "invalidation_tr": "—",
                        "source": {"module": "accounting.futures_ledger", "function": "history", "params": {"trade_id": tid}}})
    if position:
        tid = "%s:%s" % (book_id, position.get("id") or position.get("trade_id") or "open")
        entry, stop = _f(position.get("entry_avg") or position.get("entry")), _f(position.get("stop"))
        side = str(position.get("side") or "").upper()
        o_ms = _ms(position.get("opened_at"))
        tg = [t for t in (position.get("targets") or [position.get("target1"), position.get("target2")]) if _f(t) is not None]
        qty = _f(position.get("qty") or position.get("quantity") or position.get("units"))
        if entry is not None:
            base = {"layer": LAYER_TRADES, "trade_id": tid, "side": side, "t0": o_ms, "t1": None, "anchors": [], "confirmed_at": o_ms,
                    "decision_impact": USED_IN_DECISION, "invalidation_tr": "—",
                    "source": {"module": "accounting.futures_ledger", "function": "positions", "params": {"trade_id": tid}}}
            out.append({**base, "id": "pos_entry:%s" % tid, "kind": "entry", "price": entry,
                        "label_tr": "Açık %s giriş %.6g%s" % (side, entry, (" · %.6g adet" % qty) if qty else ""),
                        "rationale_tr": "Gerçekleşen ortalama giriş fiyatı (%s)." % (position.get("opened_at") or "?")})
            if stop is not None:
                risk_pct = abs(entry - stop) / entry * 100.0 if entry else None
                out.append({**base, "id": "pos_stop:%s" % tid, "kind": "stop", "price": stop,
                            "label_tr": "Mevcut stop %.6g (%.2f%% risk)" % (stop, risk_pct or 0.0),
                            "rationale_tr": "Defterdeki güncel stop; giriş anındaki stop giriş kaydında ayrıca durur."})
            for i, t in enumerate(tg, start=1):
                tv = _f(t)
                rr = (abs(tv - entry) / abs(entry - stop)) if (stop is not None and entry != stop) else None
                out.append({**base, "id": "pos_tp%d:%s" % (i, tid), "kind": "target", "price": tv,
                            "label_tr": "TP%d %.6g%s" % (i, tv, (" (%.2fR)" % rr) if rr else ""), "rationale_tr": "Defterdeki hedef %d." % i})
            if not tg:
                out.append({**base, "id": "pos_notp:%s" % tid, "kind": "no_target", "price": None,
                            "label_tr": "TP yok", "rationale_tr": "Bu defter sabit kâr hedefi kullanmaz; çıkış kural ya da stop ile."})
            liq = _f(position.get("liquidation_price") or position.get("liq_price"))
            if liq is not None:
                out.append({**base, "id": "pos_liq:%s" % tid, "kind": "liq", "price": liq, "label_tr": "Likidasyon %.6g" % liq,
                            "rationale_tr": "İzole marj tasfiye fiyatı (defterden)."})
            me = mark_element(book_id=book_id, position=position, mark_price=mark_price, at_ms=as_of_ms, price_source=mark_source)
            if me is not None:
                out.append(me)
    if plan and _f(plan.get("entry")) is not None and str(plan.get("direction") or "").upper() not in ("", "BEKLE", "NONE"):
        entry, stop = _f(plan.get("entry")), _f(plan.get("stop"))
        tg = [t for t in (plan.get("targets") or [plan.get("tp1"), plan.get("tp2")]) if _f(t) is not None]
        base = {"layer": LAYER_TRADES, "trade_id": None, "side": str(plan.get("direction")).upper(), "t0": None, "t1": None, "anchors": [],
                "confirmed_at": None, "decision_impact": USED_IN_DECISION if plan.get("valid", True) else INVALID,
                "invalidation_tr": str(plan.get("invalidation") or plan.get("invalid_reason") or "plan geçersizleşince kaldırılır"),
                "source": {"module": "coinhead", "function": "plan", "params": {"entry_type": plan.get("entry_type")}}}
        out.append({**base, "id": "plan_entry", "kind": "plan_entry", "price": entry, "label_tr": "Plan girişi %.6g (%s)" % (entry, plan.get("entry_type") or "market"),
                    "rationale_tr": "Ana bot coin-head planı; gerçekleşen giriş değildir."})
        if stop is not None:
            out.append({**base, "id": "plan_stop", "kind": "plan_stop", "price": stop, "label_tr": "Plan stop %.6g" % stop, "rationale_tr": "Plan stop seviyesi."})
        for i, t in enumerate(tg, start=1):
            out.append({**base, "id": "plan_tp%d" % i, "kind": "plan_target", "price": _f(t), "label_tr": "Plan TP%d %.6g" % (i, _f(t)), "rationale_tr": "Plan hedefi %d." % i})
    return out


# ----------------------------------------------------------------------------- açıklama
def _fmt(v: Any, nd: int = 6) -> str:
    x = _f(v)
    return "—" if x is None else ("%%.%dg" % nd) % x


def explain(*, book: dict[str, Any], rs: dict[str, Any] | None, decision: dict[str, Any] | None, gates: dict[str, Any],
            position: dict[str, Any] | None, entry_features: dict[str, Any] | None, patterns: list[dict[str, Any]],
            zones: list[dict[str, Any]], trends: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Yapılandırılmış ölçümlerden Türkçe açıklama satırları. LLM YOK; hesaplanmamış olasılık/hedef YOK."""
    lines: list[dict[str, str]] = []
    name = str(book.get("name") or BOOK_MAIN)
    if name in ("t2_trend_regime", "m2_tsmom28") and rs is not None:
        if not rs.get("ok"):
            lines.append({"k": "kural", "v": "Kural değerlendirilemedi: %s (günlük bar %s / gerekli %s)." % (rs.get("reason"), rs.get("n_daily_bars"), rs.get("min_daily_bars"))})
        else:
            if name == "t2_trend_regime":
                lines.append({"k": "kural", "v": "T2: günlük kapanış %s %s EMA200 %s → koşul %s (bar %s)." % (
                    _fmt(rs["close"]), ">" if rs["above"] else "<", _fmt(rs["ema200"]), "SAĞLANDI" if rs["above"] else "sağlanmadı", _iso(rs.get("signal_ts")) or "?")})
            else:
                lines.append({"k": "kural", "v": "M2: günlük kapanış %s %s 28 gün önceki kapanış %s (%s) → koşul %s (bar %s)." % (
                    _fmt(rs["close"]), ">" if rs["above"] else "<", _fmt(rs["ref_close"]), (_iso(rs.get("ref_ts")) or "?")[:10],
                    "SAĞLANDI" if rs["above"] else "sağlanmadı", _iso(rs.get("signal_ts")) or "?")})
            reg = rs.get("regime")
            lines.append({"k": "rejim", "v": "BTC günlük rejimi %s → %s." % (reg or "bilinmiyor", "yeni giriş izinli" if reg == "UP" else "yeni giriş YOK")})
            if position:
                lines.append({"k": "pozisyon", "v": "Açık %s: giriş %s (%s), mevcut stop %s. TP yok." % (
                    position.get("side"), _fmt(position.get("entry_avg") or position.get("entry")), str(position.get("opened_at") or "?")[:16], _fmt(position.get("stop")))})
                ef = entry_features or {}
                if ef:
                    ref_bit = (" · 28g referans %s (%s)" % (_fmt(ef.get("ref_close")), (_iso(ef.get("ref_ts")) or "?")[:10])) if ef.get("ref_close") is not None else ""
                    lines.append({"k": "giriş anı", "v": "Giriş anındaki değerler: kapanış %s, EMA200 %s, ATR14 %s, stop %s (%s×ATR)%s. Şu anki analiz değerleri yukarıda; ikisi ayrıdır." % (
                        _fmt(ef.get("signal_close")), _fmt(ef.get("ema200")), _fmt(ef.get("atr14")), _fmt(ef.get("stop_at_entry")), _fmt(ef.get("atr_mult"), 3), ref_bit)})
                exit_rule = "günlük kapanış EMA200 altına inince (EMA200_CROSS_DOWN)" if name == "t2_trend_regime" else "günlük kapanış 28 gün önceki kapanışın altına inince (M2_TSMOM28_CROSS_DOWN)"
                lines.append({"k": "çıkış", "v": "Çıkış koşulu: %s ya da stop. BTC rejimi DOWN'a dönünce açık pozisyon OTOMATİK KAPANMAZ; yalnız yeni giriş engellenir." % exit_rule})
            else:
                if rs.get("above") and reg == "UP":
                    lines.append({"k": "durum", "v": "Koşullar sağlandı; pozisyon yoksa neden açılmadığı defter retlerinde (ör. toplam açık risk tavanı, borsa asgari tutar)."})
                else:
                    lines.append({"k": "durum", "v": "Pozisyon yok; giriş koşulu %s." % ("sağlanmıyor" if not rs.get("above") else "rejim nedeniyle kapalı")})
    elif name in ("b1_box_fade",) and rs is not None:
        if not rs.get("ok"):
            lines.append({"k": "kural", "v": "Box kuralı değerlendirilemedi: %s (günlük bar %s / 5m bar %s)."
                          % (rs.get("reason"), rs.get("n_daily_bars"), rs.get("n_m5_bars"))})
        else:
            lines.append({"k": "kutu", "v": "Önceki günün aralığı: dip %s — tepe %s (orta %s). Bant: kutu yüksekliğinin %%%g'i."
                          % (_fmt(rs.get("box_low")), _fmt(rs.get("box_high")), _fmt(rs.get("box_mid")),
                             float(rs.get("near_frac") or 0) * 100)})
            loc_tr = {"TOP": "TEPEDE (satış aranır)", "BOTTOM": "DİPTE (alış aranır)",
                      "MIDDLE": "ORTADA (işlem YOK)", "OUTSIDE": "kutu DIŞINDA (işlem YOK)"}
            lines.append({"k": "konum", "v": "Son 5m kapanış %s → %s." % (_fmt(rs.get("close")), loc_tr.get(str(rs.get("location")), str(rs.get("location"))))})
            if rs.get("side"):
                lines.append({"k": "tetik", "v": "%s tetiği: %s (kural: %s mum ÖNCEKİ mumun ucunu kırmalı)."
                              % (rs.get("side"), "GERÇEKLEŞTİ" if rs.get("triggered") else "yok",
                                 "kırmızı" if rs.get("side") == "SHORT" else "yeşil")})
                if rs.get("stop_if_open") is not None:
                    tg = rs.get("targets_if_open") or []
                    lines.append({"k": "risk", "v": "Açılsaydı stop %s, hedef %s (çıkış kuralı: %s)."
                                  % (_fmt(rs.get("stop_if_open")), _fmt(tg[0]) if tg else "YOK", rs.get("exit_kind"))})
            if position:
                lines.append({"k": "pozisyon", "v": "Açık %s: giriş %s (%s), mevcut stop %s."
                              % (position.get("side"), _fmt(position.get("entry_avg") or position.get("entry")),
                                 str(position.get("opened_at") or "?")[:16], _fmt(position.get("stop")))})
            lines.append({"k": "çıkış", "v": "DİKKAT: çıkış kuralı kaynak videoda YOKTUR. Buradaki '%s' bir SEÇİMDİR; "
                          "araştırma süpürmesiyle ölçülmüştür ve kuralın kendi iddiası değildir." % rs.get("exit_kind")})
    elif name == BOOK_MAIN:
        d = decision or {}
        if d:
            block = d.get("block_code")
            verdict = d.get("verdict")
            if block:
                lines.append({"k": "karar", "v": "Ana bot: %s → giriş AÇILMADI — %s (%s)." % (verdict or "?", _BLOCK_TR.get(str(block).split(":")[0], str(block)), block)})
            else:
                lines.append({"k": "karar", "v": "Ana bot: %s; risk kapısı %s." % (verdict or "?", "izin verdi" if d.get("risk_allowed") else ("reddetti: %s" % ", ".join(d.get("risk_reasons") or [])) if d.get("risk_allowed") is not None else "değerlendirilmedi")})
            cc, ch, rg = d.get("candle_confirmation") or {}, d.get("chart_confirmation") or {}, d.get("regime_gate") or {}
            if cc:
                v = cc.get("verdict") or {}
                lines.append({"k": "mum", "v": "Mum onayı %s (%s): %s%s." % (gates.get("candle_mode"), gates.get("candle_variant"), v.get("reason") or ("geçti" if v.get("ok", True) else "veto"),
                                                                        " → KARARI ETKİLEDİ" if cc.get("blocks") else (" → kararı etkilemedi" if str(gates.get("candle_mode")).upper() != "ENFORCE" else " → geçti"))})
            if ch:
                v = ch.get("verdict") or {}
                lines.append({"k": "formasyon", "v": "Grafik formasyonu kapısı %s (%s): %s → %s." % (gates.get("chart_mode"), gates.get("chart_variant"), v.get("reason") or "hüküm yok",
                                                                                     "KARARI ETKİLEDİ" if ch.get("blocks") else "yalnız gözlem")})
            if rg:
                v = rg.get("verdict") or {}
                lines.append({"k": "rejim", "v": "Rejim kapısı %s (%s): BTC %s → %s." % (gates.get("regime_mode"), gates.get("regime_variant"), rg.get("regime") or "?",
                                                                            "KARARI ETKİLEDİ" if rg.get("blocks") else "geçti")})
        else:
            lines.append({"k": "karar", "v": "Ana bot için bu sembolde kayıtlı karar yok (son turlarda aday olmadı)."})
        if position:
            tg = [t for t in (position.get("targets") or [position.get("target1"), position.get("target2")]) if _f(t) is not None]
            lines.append({"k": "pozisyon", "v": "Açık %s: gerçekleşen giriş %s, stop %s, hedef %s." % (
                position.get("side"), _fmt(position.get("entry_avg") or position.get("entry")), _fmt(position.get("stop")), ", ".join(_fmt(t) for t in tg) if tg else "yok")})
    if patterns:
        seen = {}
        for p in patterns:
            seen.setdefault(p.get("pattern"), p)
        lines.append({"k": "formasyonlar", "v": "; ".join("%s (%s, kırılış %s, %s)" % (p.get("pattern_tr"), "yükseliş" if str(p.get("side")).upper() == "BULL" else "düşüş",
                                                                                    (_iso(p.get("break_at")) or "?")[:16], p.get("decision_impact_tr")) for p in seen.values())})
    else:
        lines.append({"k": "formasyonlar", "v": "Formasyon bulunamadı (kapanmış barlarda teyitli kırılış yok)."})
    if zones:
        lines.append({"k": "bölgeler", "v": "%d fiyat bölgesi (≥2 teyitli pivot): %s." % (len(zones), "; ".join(z["label_tr"] for z in zones[:4]))})
    else:
        lines.append({"k": "bölgeler", "v": "Küme oluşturan pivot yok → bölge üretilmedi."})
    if trends:
        lines.append({"k": "trend", "v": "; ".join(t["label_tr"] for t in trends)})
    return lines


# ----------------------------------------------------------------------------- baglam yardimcilari
def gates_from_v3(v3: Any) -> dict[str, Any]:
    """Ana botun kapi modlari/varyantlari CONFIG'ten (uydurulmaz). Formasyon tazelik penceresi dedektor config'inden."""
    es = getattr(v3, "entry_selectivity", None)
    pol = dict(getattr(es, "chart_policy", None) or {})
    try:
        within = ChartPatternConfig(**{k: v for k, v in pol.items() if k in ChartPatternConfig.__dataclass_fields__}).confirm_within_bars
    except (TypeError, ValueError):
        within = ChartPatternConfig().confirm_within_bars
    return {"candle_mode": getattr(es, "candle_confirmation_mode", None), "candle_variant": getattr(es, "candle_confirmation_variant", None),
            "chart_mode": getattr(es, "chart_confirmation_mode", None), "chart_variant": getattr(es, "chart_confirmation_variant", None),
            "regime_mode": getattr(es, "regime_gate_mode", None), "regime_variant": getattr(es, "regime_gate_variant", None),
            "chart_fresh_within": int(within)}


def position_to_dict(pos: Any) -> dict[str, Any]:
    """Defter pozisyon nesnesi -> sozluk (gosterim). Hedefler bos olabilir (T2/M2: TP yok)."""
    def _d(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None
    side = getattr(pos, "side", None)
    return {"id": getattr(pos, "id", None), "symbol": getattr(pos, "symbol", None), "side": getattr(side, "value", side),
            "qty": _d(getattr(pos, "qty", None)), "entry_avg": _d(getattr(pos, "entry_avg", None)), "stop": _d(getattr(pos, "stop", None)),
            "initial_stop": _d(getattr(pos, "initial_stop", None)), "targets": [_d(t) for t in (getattr(pos, "targets", None) or []) if _d(t)],
            "opened_at": getattr(pos, "opened_at", None), "leverage": getattr(pos, "leverage", None),
            "liquidation_price": _d(getattr(pos, "liquidation_price", None)), "last_price": _d(getattr(pos, "last_price", None))}


def spot_position_to_dict(symbol: str, p: dict[str, Any] | None) -> dict[str, Any] | None:
    """`SpotLedger.positions()` satırı -> gösterim sözlüğü (panelin `spot_positions` şekliyle AYNI; bulgu #2).
    Ana botun SPOT kaydı yalnız spot defterinin pozisyonunu taşır; futures defteri buraya GİRMEZ."""
    if not p:
        return None
    units = _f(p.get("units") if p.get("units") is not None else p.get("qty"))
    if not units or units <= 0:
        return None
    stop = _f(p.get("stop"))
    return {"id": "SPOT:%s" % symbol, "symbol": symbol, "side": "LONG", "qty": units,
            "entry_avg": _f(p.get("entry_price") if p.get("entry_price") is not None else p.get("avg_cost")),
            "stop": stop if stop else None, "initial_stop": None, "targets": [], "opened_at": p.get("entry_time") or p.get("opened_at"),
            "leverage": 1, "liquidation_price": None, "last_price": None, "market_type": "SPOT"}


# ----------------------------------------------------------------------------- karar parmak izi
def decision_fingerprint_payload(*, rs: dict[str, Any] | None, decision: dict[str, Any] | None, plan: dict[str, Any] | None,
                                 position: dict[str, Any] | None, history: list[dict[str, Any]] | None) -> dict[str, Any]:
    """`analysis_id`ye giren karar/defter durumu. Kayda değer her değişim (pozisyon açılış/kapanış, KISMİ kapanış =
    miktar, stop, hedefler, son kapanan işlem ve gerekçesi, plan, karar hükmü/engel, kural koşulu/rejim) yeni bir
    analiz anı üretir; işaret fiyatı ve açık K/Z GİRMEZ (her fiyat güncellemesi için dosya üretilmez).
    2e31926 miktar/hedef/kapanış değişimini içermiyordu (bulgu #3B)."""
    # Sayısal alanlar NORMALİZE edilir: motor defter nesnesinden float, panel defter JSON'undan Decimal-string okur
    # ("2505.56" vs 2505.56); aynı gerçek durum iki yolda AYNI parmak izini vermeli (panel motor kaydını kimlikle bulur).
    def _s(x: Any) -> str | None:
        return None if x in (None, "") else str(x).upper()

    def _pos(p: dict[str, Any] | None) -> dict[str, Any] | None:
        if not p:
            return None
        return {"id": _s(p.get("id")), "side": _s(p.get("side")), "entry": _f(p.get("entry")), "entry_avg": _f(p.get("entry_avg")),
                "stop": _f(p.get("stop")), "opened_at": _ms(p.get("opened_at")),
                "qty": _f(p.get("qty") if p.get("qty") not in (None, "") else (p.get("quantity") if p.get("quantity") not in (None, "") else p.get("units"))),
                "targets": [_f(t) for t in (p.get("targets") or [p.get("target1"), p.get("target2")]) if _f(t) is not None]}

    rows = [h for h in (history or []) if isinstance(h, dict)]
    last_h = rows[-1] if rows else None
    d = decision or {}
    return {"rs": {"above": bool(rs.get("above")) if rs.get("above") is not None else None, "regime": _s(rs.get("regime")),
                   "ok": bool(rs.get("ok")), "signal_ts": _ms(rs.get("signal_ts"))} if rs else None,
            "decision": {"verdict": _s(d.get("verdict")), "block_code": _s(d.get("block_code")),
                         "risk_allowed": (bool(d.get("risk_allowed")) if d.get("risk_allowed") is not None else None)},
            "plan": {"entry": _f(plan.get("entry")), "stop": _f(plan.get("stop")), "direction": _s(plan.get("direction")), "valid": bool(plan.get("valid", True)),
                     "targets": [_f(t) for t in (plan.get("targets") or [plan.get("tp1"), plan.get("tp2")]) if _f(t) is not None]} if plan else None,
            "position": _pos(position),
            "history": {"n": len(rows), "last": ({"id": _s(last_h.get("id")), "closed_at": _ms(last_h.get("closed_at")), "exit_price": _f(last_h.get("exit_price")),
                                                  "exit_reason": _s(last_h.get("exit_reason"))} if last_h else None)}}


# ----------------------------------------------------------------------------- ana kurucu
def build_snapshot(*, symbol: str, market_type: str, timeframe: str, tf_ms: int, book: dict[str, Any],
                   bars: list[dict[str, Any]], as_of_ms: int, daily_rows: list[dict[str, Any]] | None,
                   btc_daily_rows: list[dict[str, Any]] | None, gates: dict[str, Any], decision: dict[str, Any] | None,
                   intraday_rows: list[dict[str, Any]] | None = None,
                   plan: dict[str, Any] | None, position: dict[str, Any] | None, history: list[dict[str, Any]] | None,
                   entry_features: dict[str, Any] | None, mark_price: float | None, cfg: dict[str, Any],
                   code: str | None = None, cfg_hash: str | None = None, source: dict[str, Any] | None = None,
                   mark_source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tek analiz anı. `bars` KAPANMIŞ barlardır (çağıran `closed_bars_at` ile keser). `mark_source` işaret fiyatının
    gerçek kaynağını etiketler (motor: canlı tik; panel: son mum kapanışı) — bkz. `mark_element`."""
    bars = list(bars or [])
    last = bars[-1] if bars else None
    last_close = _f(last.get("close")) if last else None
    lookback = int(cfg.get("swing_lookback", ChartPatternConfig().swing_lookback))
    piv = pivots(bars, lookback=lookback, tf_ms=tf_ms)
    atr = _f(last.get("atr14")) if last else None
    if atr is None:
        atr = _atr_last(bars, 14) if len(bars) >= 15 else None
    zones = level_elements(bars, piv, atr=atr, tolerance_atr=float(cfg.get("cluster_tolerance_atr", MultiTimeframeConfig().equal_level_atr_tolerance)),
                           timeframe=timeframe, last_close=last_close, tf_ms=tf_ms)
    trends = trendline_elements(bars, piv, touch_tolerance_pct=float(cfg.get("trendline_touch_tolerance_pct", 0.3)), timeframe=timeframe, tf_ms=tf_ms)
    det = detect_chart_patterns(bars, ChartPatternConfig()) if bars else {"patterns": []}
    pats = pattern_elements(bars, det, chart_mode=str(gates.get("chart_mode") or "OFF"), fresh_within=gates.get("chart_fresh_within"), timeframe=timeframe, tf_ms=tf_ms)
    name = str(book.get("name") or BOOK_MAIN)
    rs = None
    if name in paper_rules.VARIANTS and name != "t1_trend":
        # Kural durumu KAYIT üzerinden: hangi aile, hangi parametre — panel ikinci bir formül tutmaz.
        try:
            rp = paper_rules.build_params(name, atr_mult=float(book.get("atr_mult") or 3.0),
                                          rule_params=dict(book.get("rule_params") or {}))
            rs = paper_rules.state_from_rows(name, daily=daily_rows or [], intraday=intraday_rows or [],
                                             btc_rows=btc_daily_rows or [], params=rp)
        except (ValueError, TypeError) as exc:      # geçersiz defter ayarı panelde SESSİZ KALMAZ
            rs = {"variant": name, "ok": False, "reason": "RULE_PARAMS_INVALID:%s" % type(exc).__name__}
    trades = trade_elements(book_id=str(book.get("book_id") or BOOK_MAIN), position=position, history=history or [], plan=plan if name == BOOK_MAIN else None,
                            as_of_ms=as_of_ms, mark_price=mark_price, mark_source=mark_source)
    indicators = []
    if last:
        for key, lab in (("ema20", "EMA20 (4h)"), ("ema50", "EMA50 (4h)"), ("ema200", "EMA200 (%s)" % timeframe)):
            v = _f(last.get(key))
            if v is not None:
                indicators.append({"id": "ind:%s" % key, "layer": LAYER_INDICATORS, "kind": "indicator", "label_tr": "%s %.6g" % (lab, v), "price": v,
                                   "t0": None, "t1": None, "anchors": [], "confirmed_at": int(last["timestamp"]) + int(tf_ms), "decision_impact": OBSERVATION_ONLY,
                                   "rationale_tr": "Grafik dilimi göstergesi; T2/M2 kuralı GÜNLÜK EMA200 kullanır (aşağıdaki kural satırı).", "invalidation_tr": "—",
                                   "source": {"module": "indicators", "function": "add_snapshot_indicators", "params": {"tf": timeframe}}})
    if rs and rs.get("ok"):
        indicators.append({"id": "ind:daily_ema200", "layer": LAYER_INDICATORS, "kind": "rule_reference", "label_tr": "Günlük EMA200 %.6g (kural)" % rs["ema200"],
                           "price": rs["ema200"], "t0": None, "t1": None, "anchors": [], "confirmed_at": _daily_close(rs.get("signal_ts")),
                           "decision_impact": USED_IN_DECISION if name == "t2_trend_regime" else OBSERVATION_ONLY,
                           "rationale_tr": "T2 kuralının karşılaştırdığı günlük EMA200 (kapanmış günlük bar %s)." % (_iso(rs.get("signal_ts")) or "?"), "invalidation_tr": "—",
                           "source": {"module": "ema200_trend", "function": "rule_state", "params": {"variant": name}}})
        if rs.get("ref_close") is not None:
            indicators.append({"id": "ind:ref28", "layer": LAYER_INDICATORS, "kind": "rule_reference", "label_tr": "28g referans kapanış %.6g (%s)" % (rs["ref_close"], (_iso(rs.get("ref_ts")) or "?")[:10]),
                               "price": rs["ref_close"], "t0": rs.get("ref_ts"), "t1": rs.get("signal_ts"), "anchors": [], "confirmed_at": _daily_close(rs.get("signal_ts")),
                               "decision_impact": USED_IN_DECISION,
                               "rationale_tr": "M2 kuralının karşılaştırdığı %d gün önceki günlük kapanış." % TSMOM_LOOKBACK_DAYS, "invalidation_tr": "—",
                               "source": {"module": "ema200_trend", "function": "rule_state", "params": {"variant": name, "lookback_days": TSMOM_LOOKBACK_DAYS}}})
    if rs and rs.get("ok") and rs.get("box_high") is not None:
        # BOX THEORY: kutu ÖNCEKİ GÜNÜN uçlarıdır ve konum kapısını O belirler → karara girer.
        band = (float(rs["box_high"]) - float(rs["box_low"])) * float(rs.get("near_frac") or 0.0)
        sig_close = (int(rs["signal_ts"]) + 300_000) if rs.get("signal_ts") is not None else None
        for eid, price, lab, why in (
                ("box_high", rs["box_high"], "Kutu tepesi (önceki gün en yüksek) %.6g",
                 "Fiyat bu seviyeye ya da %g×kutu bandına değerse kural SATIŞ arar." % float(rs.get("near_frac") or 0)),
                ("box_low", rs["box_low"], "Kutu dibi (önceki gün en düşük) %.6g",
                 "Fiyat bu seviyeye ya da %g×kutu bandına değerse kural ALIŞ arar." % float(rs.get("near_frac") or 0)),
                ("box_top_band", float(rs["box_high"]) - band, "Üst bant sınırı %.6g",
                 "Bu çizginin ÜSTÜ 'tepeye yakın' sayılır (near_frac)."),
                ("box_bottom_band", float(rs["box_low"]) + band, "Alt bant sınırı %.6g",
                 "Bu çizginin ALTI 'dibe yakın' sayılır (near_frac)."),
                ("box_mid", rs["box_mid"], "Kutu ortası %.6g",
                 "Yalnız `exit_kind=box_mid` seçildiğinde HEDEFTİR; aksi halde gösterimdir.")):
            v = _f(price)
            if v is None:
                continue
            used = USED_IN_DECISION if eid in ("box_high", "box_low", "box_top_band", "box_bottom_band") else (
                USED_IN_DECISION if rs.get("exit_kind") == "box_mid" else OBSERVATION_ONLY)
            indicators.append({"id": "ind:%s" % eid, "layer": LAYER_INDICATORS, "kind": "rule_reference",
                               "label_tr": lab % v, "price": v, "t0": None, "t1": None, "anchors": [],
                               "confirmed_at": sig_close, "decision_impact": used,
                               "rationale_tr": why, "invalidation_tr": "Yeni gün açılınca kutu YENİLENİR (önceki günün uçlarına taşınır).",
                               "source": {"module": "box_theory", "function": "rule_state",
                                          "params": {"variant": name, "near_frac": rs.get("near_frac")}}})
    elements = zones + trends + pats + trades + indicators
    lines = explain(book=book, rs=rs, decision=decision, gates=gates, position=position, entry_features=entry_features,
                    patterns=pats, zones=[z for z in zones if z["layer"] == LAYER_ZONES], trends=trends)
    code = code or code_sha()
    fp = json.dumps(decision_fingerprint_payload(rs=rs, decision=decision, plan=plan, position=position, history=history),
                    sort_keys=True, default=str)
    ident = {"exchange": EXCHANGE, "market_type": market_type, "symbol": symbol, "timeframe": timeframe,
             "book_id": str(book.get("book_id") or BOOK_MAIN), "book_name": name,
             "as_of": _iso(as_of_ms), "as_of_ms": int(as_of_ms),
             "last_closed_bar": {"timestamp": int(last["timestamp"]) if last else None, "iso": _iso(last["timestamp"]) if last else None, "close": last_close},
             "code_sha": code, "config_hash": cfg_hash or ""}
    aid = hashlib.sha256(json.dumps([ident["symbol"], ident["market_type"], ident["timeframe"], ident["book_id"], ident["last_closed_bar"]["timestamp"],
                                     code, cfg_hash or "", fp], sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return {"schema_version": SCHEMA_VERSION, "analysis_id": aid, "identity": ident, "decision_fingerprint": hashlib.sha256(fp.encode()).hexdigest()[:12],
            "gates": gates, "rule_state": rs, "decision": decision, "plan": plan if name == BOOK_MAIN else None,
            "position": position, "entry_features": entry_features, "n_bars": len(bars),
            "pivots": {"confirmed": [_anchor_of(bars, p, tf_ms) for p in piv["confirmed"]], "pending": piv["pending"], "lookback": lookback},
            "patterns_detected": det.get("patterns") or [], "elements": elements, "explanation": lines,
            "time_contract": TIME_CONTRACT, "source": source or {}, "synthetic": bool(cfg.get("synthetic", False))}


__all__ = ["SCHEMA_VERSION", "TF_MS", "BOOK_MAIN", "MARKET_TYPES", "TIME_CONTRACT", "HISTORY_TAIL", "USED_IN_DECISION", "OBSERVATION_ONLY", "UNCONFIRMED", "INVALID",
           "code_sha", "config_hash", "market_type_of", "plan_for_market", "bars_from_frame", "closed_bars_at", "pivots", "level_elements",
           "trendline_elements", "pattern_elements", "mark_element", "trade_elements", "explain", "decision_fingerprint_payload", "build_snapshot",
           "gates_from_v3", "position_to_dict", "spot_position_to_dict"]
