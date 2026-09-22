# -*- coding: utf-8 -*-
"""ORTAK ANALİZ — aynı market+symbol+timeframe+as_of için aynı sürümlü sonuç (`structures_v1`).

SAF: dosya/ağ yok, girdiler değiştirilmez. Yalnız `as_of_ms` anında KAPANMIŞ barlar okunur (açılış + dilim ≤ as_of);
kısmi bar görülmez. Sağında `k` barla teyitlenen pivot, teyit barı kapanmadan kullanılmaz; bir yapı son dayanak
pivotunun teyidinden ÖNCE "biliniyor" yazılmaz (`detected_at`), teyit anı da bundan erken olamaz
(`confirmed_at = max(kırılış kapanışı, detected_at)`). Aynı girdi → aynı `analysis_id` ve aynı kayıtlar; yeni bar
geldiğinde eski bir `as_of`un sonucu değişmez (yeniden boyama yok).

Kayıt alanları (her aile): pattern_id, family, name, side, market, symbol, timeframe, policy_version, anchors,
detected_at, confirmed_at, broken_at, expired_at, as_of, status, trigger{rule,level}, invalidation{rule,level}, stop,
targets, expires_at, data_provenance, geometry_quality (olasılık DEĞİL), reason_codes, role, trend_context, geometry,
bars_since_confirm, fresh. Ölçülmemiş p_win/beklenti ÜRETİLMEZ.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from collections import OrderedDict
from typing import Any

from ..chart_patterns import ChartPatternConfig, structure_candidates
from ..learn import candle_context as cc
from ..learn.multitimeframe_context import confirmed_swings, equal_level_clusters
from ..timeframes import tf_ms
from . import catalog as K

_MISSING = object()


# ---------------------------------------------------------------------------- girdi hazırlığı
def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def closed_rows(bars: Any, *, timeframe: str, as_of_ms: int) -> list[dict[str, Any]]:
    """`as_of_ms` anında KAPANMIŞ, sayısal ve tutarlı OHLC satırları (zamana göre sıralı, tekil). DataFrame de kabul
    edilir (timestamp/open/high/low/close sütunları). Kapanmamış bar ve bozuk satır SESSİZCE kullanılmaz; sayısı döner."""
    step = tf_ms(timeframe)
    if hasattr(bars, "to_dict") and hasattr(bars, "columns"):
        try:
            cols = [c for c in ("timestamp", "open", "high", "low", "close") if c in bars.columns]
            bars = bars[cols].to_dict("records") if len(cols) == 5 else []
        except Exception:  # noqa: BLE001
            bars = []
    out: dict[int, dict[str, Any]] = {}
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        ts = _num(b.get("timestamp"))
        o, h, lo, c = (_num(b.get(k)) for k in ("open", "high", "low", "close"))
        if ts is None or None in (o, h, lo, c):
            continue
        ts = int(ts)
        if ts + step > int(as_of_ms):
            continue                                   # kapanmamış (kısmi) bar
        if not (h >= max(o, c) and lo <= min(o, c) and h >= lo and lo > 0):
            continue                                   # tutarsız OHLC karar girdisi olamaz
        out[ts] = {"timestamp": ts, "open": o, "high": h, "low": lo, "close": c}
    return [out[k] for k in sorted(out)]


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(("%d|%.10g|%.10g|%.10g|%.10g;" % (r["timestamp"], r["open"], r["high"], r["low"], r["close"])).encode())
    return h.hexdigest()[:16]


def _atr_series(rows: list[dict[str, Any]], n: int) -> list[float | None]:
    """ATR(i) = son n gerçek aralığın ortalaması, yalnız ≤ i barlarından (ileriye bakış yok)."""
    out: list[float | None] = [None] * len(rows)
    trs: list[float] = []
    for i, r in enumerate(rows):
        if i == 0:
            trs.append(r["high"] - r["low"])
        else:
            pc = rows[i - 1]["close"]
            trs.append(max(r["high"] - r["low"], abs(r["high"] - pc), abs(r["low"] - pc)))
        if i >= n:
            v = sum(trs[i - n + 1:i + 1]) / n
            out[i] = v if v > 0 else None
    return out


def _pid(market: str, symbol: str, tf: str, name: str, side: str | None, anchors: list[int], policy: str) -> str:
    raw = "%s|%s|%s|%s|%s|%s|%s" % (market, symbol, tf, name, side or "-", ",".join(str(int(a)) for a in anchors), policy)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------- ortak durum makinesi
def _run_status(rows: list[dict[str, Any]], *, start: int, detected_idx: int, side: str, trigger_at, invalidation: float,
                window: int, fresh_bars: int) -> dict[str, Any]:
    """Tetik/geçersizlik kapanışlarını `start` indeksinden itibaren kronolojik uygular.

    `trigger_at(k)` k barındaki tetik seviyesi (None → o barda tetik yok, ör. üçgen tepe noktasından sonra). LONG:
    kapanış > tetik → CONFIRMED; kapanış < geçersizlik → BROKEN. SHORT tersi. Teyit anı `detected_idx`ten erken olamaz.
    Teyit yoksa `detected_idx + window` sonrası EXPIRED; teyitten sonra geçersizlik kapanışı BROKEN (başarısız yapı);
    teyit `fresh_bars`tan eskiyse EXPIRED (CONFIRMATION_STALE)."""
    n = len(rows)
    long = side == K.LONG
    res: dict[str, Any] = {"status": K.ST_FORMING, "confirm_idx": None, "broken_idx": None, "expired_idx": None,
                           "reasons": []}
    k = max(0, int(start))
    while k < n:
        c = rows[k]["close"]
        if res["confirm_idx"] is None:
            lvl = trigger_at(k)
            if (c < invalidation) if long else (c > invalidation):
                res.update(status=K.ST_BROKEN, broken_idx=max(k, detected_idx))
                res["reasons"].append("CLOSE_BEYOND_INVALIDATION_BEFORE_TRIGGER")
                return res
            if lvl is not None and ((c > lvl) if long else (c < lvl)):
                res["confirm_idx"] = max(k, detected_idx)
                res["status"] = K.ST_CONFIRMED
                k = res["confirm_idx"] + 1
                continue
            if k - detected_idx >= window and k >= detected_idx:
                res.update(status=K.ST_EXPIRED, expired_idx=k)
                res["reasons"].append("TRIGGER_WINDOW_ELAPSED")
                return res
        else:
            if (c < invalidation) if long else (c > invalidation):
                res.update(status=K.ST_BROKEN, broken_idx=k)
                res["reasons"].append("FAILED_AFTER_CONFIRMATION")
                return res
        k += 1
    if res["confirm_idx"] is None and (n - 1) - detected_idx >= window:
        res.update(status=K.ST_EXPIRED, expired_idx=detected_idx + window)
        res["reasons"].append("TRIGGER_WINDOW_ELAPSED")
    elif res["confirm_idx"] is not None and (n - 1) - res["confirm_idx"] > fresh_bars:
        res.update(status=K.ST_EXPIRED, expired_idx=res["confirm_idx"] + fresh_bars + 1)
        res["reasons"].append("CONFIRMATION_STALE")
    return res


def _close_ms(rows, idx: int | None, step: int) -> int | None:
    if idx is None or idx < 0 or idx >= len(rows):
        return None
    return int(rows[idx]["timestamp"]) + step


def _finish(rec: dict[str, Any], rows, st: dict[str, Any], *, step: int, n: int, window: int, fresh_bars: int,
            detected_idx: int) -> dict[str, Any]:
    rec["status"] = st["status"]
    rec["confirmed_at_ms"] = _close_ms(rows, st["confirm_idx"], step)
    rec["broken_at_ms"] = _close_ms(rows, st["broken_idx"], step)
    rec["expired_at_ms"] = _close_ms(rows, st["expired_idx"], step) if st["expired_idx"] is not None and st["expired_idx"] < n else (
        int(rows[detected_idx]["timestamp"]) + (window + 1) * step if st["expired_idx"] is not None else None)
    rec["bars_since_confirm"] = (n - 1 - st["confirm_idx"]) if st["confirm_idx"] is not None else None
    rec["fresh"] = bool(st["status"] == K.ST_CONFIRMED)
    if st["confirm_idx"] is None:
        rec["expires_at_ms"] = int(rows[detected_idx]["timestamp"]) + (window + 1) * step
    else:
        rec["expires_at_ms"] = int(rows[st["confirm_idx"]]["timestamp"]) + (fresh_bars + 1) * step
    rec["reason_codes"] = list(rec.get("reason_codes") or []) + list(st["reasons"])
    ci = st["confirm_idx"]
    rec["confirm_bar"] = ({"ts": int(rows[ci]["timestamp"]), "open": rows[ci]["open"], "high": rows[ci]["high"],
                           "low": rows[ci]["low"], "close": rows[ci]["close"]} if ci is not None and ci < n else None)
    return rec


# ---------------------------------------------------------------------------- mumlar
_MULTI3 = {cc.MORNING_STAR_LIKE, cc.EVENING_STAR_LIKE, cc.MORNING_DOJI_STAR_LIKE, cc.EVENING_DOJI_STAR_LIKE,
           cc.BULLISH_ABANDONED_BABY_LIKE, cc.BEARISH_ABANDONED_BABY_LIKE, cc.TRI_STAR_LIKE,
           cc.THREE_WHITE_SOLDIERS_LIKE, cc.THREE_BLACK_CROWS_LIKE}
_SINGLE = {cc.DOJI_LIKE, cc.HAMMER_LIKE, cc.INVERTED_HAMMER_LIKE, cc.MARUBOZU_LIKE, cc.SPINNING_TOP_LIKE,
           cc.BULLISH_BELT_HOLD_LIKE, cc.BEARISH_BELT_HOLD_LIKE}


def pattern_bars(shape: str) -> int:
    """Şeklin kaç bardan oluştuğu (1/2/3) — TEK tanım (formasyon botu da bunu kullanır)."""
    if shape in _MULTI3:
        return 3
    if shape in _SINGLE:
        return 1
    return 2


def _candle_quality(shape: str, m: dict[str, Any]) -> float:
    """Geometrik uyum (olasılık DEĞİL): baskın oranın eşiğe göre doluluğu, [0,1]."""
    br, uw, lw = m.get("body_to_range_ratio"), m.get("upper_wick_to_range_ratio"), m.get("lower_wick_to_range_ratio")
    if shape == cc.HAMMER_LIKE and lw is not None:
        return round(min(1.0, lw), 4)
    if shape == cc.INVERTED_HAMMER_LIKE and uw is not None:
        return round(min(1.0, uw), 4)
    if shape in (cc.DOJI_LIKE, cc.TRI_STAR_LIKE) and br is not None:
        return round(max(0.0, 1.0 - br / 0.10), 4)
    return round(min(1.0, br), 4) if br is not None else 0.0


def _candles(rows, atr, *, market, symbol, tf, step, cfg: K.StructuresConfig, prov) -> tuple[list[dict], list[dict]]:
    recs: list[dict] = []
    rej: list[dict] = []
    n = len(rows)
    if n < K.REQUIREMENTS["candle"]:
        return recs, [{"detector": "candle", "reason": "NOT_ENOUGH_BARS", "have": n, "need": K.REQUIREMENTS["candle"]}]
    ccfg = cc.CandleContextConfig()
    for i in range(max(2, n - cfg.candle_lookback_bars), n):
        shapes = [s for s in cc._shapes(rows[:i + 1], ccfg) if s != cc.NO_PATTERN]
        if not shapes:
            continue
        m = cc.candle_metrics(rows[i])
        for shape in shapes:
            nb = pattern_bars(shape)
            if i - nb + 1 < 0:
                continue
            pre = rows[:i - nb + 1]
            trend = cc.detect_trend(pre, atr=atr[i - nb] if i - nb >= 0 else None, cfg=ccfg)
            name, side = K.candle_name(shape, trend.get("trend"))
            pbars = rows[i - nb + 1:i + 1]
            ph, pl = max(b["high"] for b in pbars), min(b["low"] for b in pbars)
            anchors = [int(b["timestamp"]) for b in pbars]
            a = atr[i]
            reasons = []
            if trend.get("trend") in (cc.TREND_UNKNOWN,):
                reasons.append("TREND_CONTEXT_UNKNOWN")
            rec = {"family": K.FAMILY_CANDLE, "name": name, "shape": shape, "side": side, "atr": a,
                   "anchors": [{"ts": int(b["timestamp"]), "price": b["close"], "role": "mum%d" % (j + 1),
                                "confirmed_at_ms": int(b["timestamp"]) + step} for j, b in enumerate(pbars)],
                   "detected_at_ms": int(rows[i]["timestamp"]) + step, "pattern_high": ph, "pattern_low": pl,
                   "trend_context": trend.get("trend"), "role": K.role_of(side, trend.get("trend")),
                   "geometry_quality": _candle_quality(shape, m), "reason_codes": reasons,
                   "geometry": [{"kind": "mum_araligi", "t0": anchors[0], "y0": ph, "t1": anchors[-1], "y1": pl}]}
            if side is None:
                rec.update({"trigger": None, "invalidation": None, "stop": None, "targets": [],
                            "status": K.ST_EXPIRED if (n - 1 - i) >= cfg.candle_trigger_window else K.ST_FORMING,
                            "confirmed_at_ms": None, "broken_at_ms": None, "expired_at_ms": None, "fresh": False,
                            "bars_since_confirm": None, "expires_at_ms": int(rows[i]["timestamp"]) + (cfg.candle_trigger_window + 1) * step})
                rec["reason_codes"].append("NO_SIDE_NEUTRAL_OR_CONTEXTLESS")
            else:
                trig = ph if side == K.LONG else pl
                inv = pl if side == K.LONG else ph
                st = _run_status(rows, start=i + 1, detected_idx=i, side=side, trigger_at=lambda _k, _t=trig: _t,
                                 invalidation=inv, window=cfg.candle_trigger_window, fresh_bars=cfg.fresh_bars)
                buf = cfg.stop_buffer_atr * a if a else 0.0
                rec.update({"trigger": {"rule": "close_above" if side == K.LONG else "close_below", "level": trig},
                            "invalidation": {"rule": "close_below" if side == K.LONG else "close_above", "level": inv},
                            "stop": (inv - buf) if side == K.LONG else (inv + buf), "targets": []})
                if not a:
                    rec["reason_codes"].append("ATR_UNAVAILABLE_STOP_WITHOUT_BUFFER")
                rec["reason_codes"].append("NO_STRUCTURAL_TARGET")
                _finish(rec, rows, st, step=step, n=n, window=cfg.candle_trigger_window, fresh_bars=cfg.fresh_bars,
                        detected_idx=i)
            rec["pattern_id"] = _pid(market, symbol, tf, name, side, anchors, cfg.policy_version)
            recs.append(rec)
    return recs, rej


# ---------------------------------------------------------------------------- grafik yapıları
_CHART_SIDE = {"BULL": K.LONG, "BEAR": K.SHORT}


def _chart(rows, atr, *, market, symbol, tf, step, cfg: K.StructuresConfig) -> tuple[list[dict], list[dict]]:
    ccfg = ChartPatternConfig()
    sc = structure_candidates(rows, ccfg)
    crow = sc["rows"]                        # dedektörün taradığı son `scan_bars` kapanmış bar (indeksler buna göre)
    off = len(rows) - len(crow)
    rej: list[dict] = []
    if sc.get("reason"):
        return [], [{"detector": "chart", "reason": sc["reason"], "have": len(crow), "need": sc.get("need")}]
    n = len(crow)
    out: list[dict] = []
    unclassified = 0

    def build(c, *, name, side, trigger_at, invalidation, start, anchors_idx, height, quality, extra_reasons=()):
        det = int(c["last_confirm_idx"])
        if det >= n:
            return None                                   # son pivot henüz teyitli değil: yapı BİLİNMİYOR
        st = _run_status(crow, start=start, detected_idx=det, side=side, trigger_at=trigger_at, invalidation=invalidation,
                         window=cfg.chart_trigger_window, fresh_bars=cfg.fresh_bars)
        a = atr[det + off] if 0 <= det + off < len(atr) else None
        end_idx = st["confirm_idx"] if st["confirm_idx"] is not None else n - 1
        lvl_end = trigger_at(end_idx)
        if lvl_end is None:
            lvl_end = trigger_at(max(0, det))
        buf = cfg.stop_buffer_atr * a if a else 0.0
        stop = (invalidation - buf) if side == K.LONG else (invalidation + buf)
        tgt = []
        if height and lvl_end is not None:
            t = lvl_end + height if side == K.LONG else lvl_end - height
            if t > 0:
                tgt = [t]
        anchors = [{"ts": int(an["timestamp"]), "price": float(an["level"]), "role": an["role"],
                    "confirmed_at_ms": (int(an["confirmed_at_ts"]) + step) if an.get("confirmed_at_ts") is not None else None}
                   for an in c["anchors"]]
        geo = c["geometry_at"](end_idx)
        rec = {"family": K.FAMILY_CHART, "name": name, "side": side, "anchors": anchors, "atr": a,
               "detected_at_ms": int(crow[det]["timestamp"]) + step,
               "trigger": {"rule": "close_above" if side == K.LONG else "close_below",
                           "level": lvl_end, "sloped": bool(c.get("pattern") in ("HEAD_AND_SHOULDERS", "INVERSE_HEAD_AND_SHOULDERS")) or bool(extra_reasons and "SLOPED" in extra_reasons)},
               "invalidation": {"rule": "close_below" if side == K.LONG else "close_above", "level": invalidation},
               "stop": stop, "targets": tgt, "height": height, "geometry_quality": round(max(0.0, min(1.0, quality)), 4),
               "reason_codes": list(extra_reasons) + ([] if a else ["ATR_UNAVAILABLE_STOP_WITHOUT_BUFFER"]),
               "geometry": [{"kind": g["kind"], "t0": int(g["t0"]), "y0": g["y0"], "t1": int(g["t1"]), "y1": g["y1"]} for g in geo]}
        _finish(rec, crow, st, step=step, n=n, window=cfg.chart_trigger_window, fresh_bars=cfg.fresh_bars, detected_idx=det)
        rec["pattern_id"] = _pid(market, symbol, tf, name, side, [a_["ts"] for a_ in anchors], cfg.policy_version)
        # önceki trend: yapının başlangıcından ÖNCEKİ barlar (bağlam; karar tarafını değiştirmez)
        s0 = int(c["start_idx"]) + off
        tr = cc.detect_trend(rows[:max(0, s0)], atr=atr[s0 - 1] if s0 - 1 >= 0 else None, cfg=cc.CandleContextConfig())
        rec["trend_context"] = tr.get("trend")
        rec["role"] = K.role_of(side, tr.get("trend"))
        return rec

    for c in sc["candidates"]:
        pat = c["pattern"]
        if pat in ("ASCENDING_TRIANGLE", "DESCENDING_TRIANGLE"):
            desc = bool(c["descending"])
            support, lba = c["support"], c["line_before_apex"]
            # iki yönlü aday: düz sınır kırılışı ve eğik sınır kırılışı ayrı kayıt (biri teyit olursa diğeri o bar
            # kapanışıyla geçersizleşir: karşı sınırın ötesinde kapanış = geçersizlik)
            flat_side = K.SHORT if desc else K.LONG
            line_side = K.LONG if desc else K.SHORT
            for side, trig, inv in ((flat_side, (lambda _k, _s=support: _s), None), (line_side, lba, support)):
                if inv is None:
                    # düz sınır tarafı: geçersizlik = eğik sınırın SON pivotu (karşı taraf kırılırsa bu taraf biter)
                    inv = c["anchors"][3]["level"]
                r = build(c, name=pat, side=side, trigger_at=trig, invalidation=float(inv), start=c["scan_from"],
                          anchors_idx=None, height=c["height"], quality=c["quality"],
                          extra_reasons=("PAIRED_TRIANGLE_SIDE",) + (("SLOPED",) if trig is lba else ()))
                if r is not None:
                    out.append(r)
            continue
        side = _CHART_SIDE[c["side"]]
        name = pat
        if pat in ("BULL_FLAG", "BEAR_FLAG"):
            ch = c.get("channel")
            if ch == "CONVERGING":
                name = "BULL_PENNANT" if pat == "BULL_FLAG" else "BEAR_PENNANT"
            elif ch != "PARALLEL":
                unclassified += 1
                continue                                # sınıflanamayan konsolidasyon bayrak SAYILMAZ
        r = build(c, name=name, side=side, trigger_at=c["level_at"], invalidation=float(c["invalidation"]),
                  start=c["scan_from"], anchors_idx=None, height=c.get("height"), quality=c.get("quality", 0.0),
                  extra_reasons=("CHANNEL_" + str(c.get("channel")),) if c.get("channel") else ())
        if r is not None:
            out.append(r)
    if unclassified:
        rej.append({"detector": "chart", "reason": "FLAG_CHANNEL_UNCLASSIFIED", "count": unclassified})
    # Süren (FORMING) bayrak/flama: art arda gelen direk adaylarının her biri aynı konsolidasyonu farklı başlangıçla
    # okur. Taraf başına tek kayıt: direk ucu kapanışı en uç olan (boğa: en yüksek, ayı: en düşük), eşitlikte en yeni.
    keep: dict[str, dict] = {}
    rest = []
    for r in out:
        if r["name"] in ("BULL_FLAG", "BEAR_FLAG", "BULL_PENNANT", "BEAR_PENNANT") and r["status"] == K.ST_FORMING:
            tip = next((a_ for a_ in r["anchors"] if a_["role"] == "direk_ucu"), None)
            key = r["side"]
            cur = keep.get(key)
            better = cur is None or (tip is not None and (
                (tip["price"], tip["ts"]) > next(((a_["price"], a_["ts"]) for a_ in cur["anchors"] if a_["role"] == "direk_ucu"), (-1e30, 0))
                if r["side"] == K.LONG else
                (-tip["price"], tip["ts"]) > next(((-a_["price"], a_["ts"]) for a_ in cur["anchors"] if a_["role"] == "direk_ucu"), (-1e30, 0))))
            if better:
                keep[key] = r
            continue
        rest.append(r)
    out = rest + list(keep.values())
    # aynı kimlik (aynı dayanak pivotlar + ad + taraf) bir kez
    seen: dict[str, dict] = {}
    for r in out:
        seen.setdefault(r["pattern_id"], r)
    return list(seen.values()), rej


# ---------------------------------------------------------------------------- senaryolar
def _compression(rows, atr, *, market, symbol, tf, step, cfg: K.StructuresConfig) -> list[dict]:
    n = len(rows)
    nb = cfg.compression_bars
    out: list[dict] = []
    active = None
    lo_i = max(nb - 1, n - (cfg.scenario_trigger_window + 2 * nb))
    for i in range(lo_i, n):
        c = rows[i]["close"]
        if active is not None and i > active["end"]:
            if c > active["hi"] or c < active["lo"]:
                side = K.LONG if c > active["hi"] else K.SHORT
                out.append(_comp_rec(rows, atr, active, side=side, confirm_idx=i, market=market, symbol=symbol, tf=tf,
                                     step=step, cfg=cfg, n=n))
                out.append(_comp_rec(rows, atr, active, side=K.SHORT if side == K.LONG else K.LONG, confirm_idx=None,
                                     broken_idx=i, market=market, symbol=symbol, tf=tf, step=step, cfg=cfg, n=n))
                active = None
                continue
            if i - active["end"] >= cfg.scenario_trigger_window:
                active = None
        a = atr[i]
        if a is None:
            continue
        win = rows[i - nb + 1:i + 1]
        hi, lo = max(b["high"] for b in win), min(b["low"] for b in win)
        if hi - lo <= cfg.compression_max_atr * a:
            active = {"end": i, "hi": hi, "lo": lo, "atr": a, "start": i - nb + 1}
    if active is not None:
        for side in (K.LONG, K.SHORT):
            out.append(_comp_rec(rows, atr, active, side=side, confirm_idx=None, market=market, symbol=symbol, tf=tf,
                                 step=step, cfg=cfg, n=n))
    return out


def _comp_rec(rows, atr, act, *, side, confirm_idx, market, symbol, tf, step, cfg, n, broken_idx=None) -> dict:
    hi, lo, a, end = act["hi"], act["lo"], act["atr"], act["end"]
    trig = hi if side == K.LONG else lo
    inv = lo if side == K.LONG else hi
    buf = cfg.stop_buffer_atr * a
    h = hi - lo
    rec = {"family": K.FAMILY_SCENARIO, "name": "COMPRESSION_BREAKOUT", "side": side, "atr": a,
           "anchors": [{"ts": int(rows[act["start"]]["timestamp"]), "price": hi, "role": "aralik_ust", "confirmed_at_ms": int(rows[end]["timestamp"]) + step},
                       {"ts": int(rows[end]["timestamp"]), "price": lo, "role": "aralik_alt", "confirmed_at_ms": int(rows[end]["timestamp"]) + step}],
           "detected_at_ms": int(rows[end]["timestamp"]) + step,
           "trigger": {"rule": "close_above" if side == K.LONG else "close_below", "level": trig},
           "invalidation": {"rule": "close_below" if side == K.LONG else "close_above", "level": inv},
           "stop": (inv - buf) if side == K.LONG else (inv + buf), "targets": [trig + h if side == K.LONG else trig - h],
           "height": h, "geometry_quality": round(max(0.0, 1.0 - h / (cfg.compression_max_atr * a)), 4) if a else 0.0,
           "reason_codes": ["PAIRED_BOTH_SIDES"],
           "geometry": [{"kind": "aralik_ust", "t0": int(rows[act["start"]]["timestamp"]), "y0": hi, "t1": int(rows[end]["timestamp"]), "y1": hi},
                        {"kind": "aralik_alt", "t0": int(rows[act["start"]]["timestamp"]), "y0": lo, "t1": int(rows[end]["timestamp"]), "y1": lo}]}
    if broken_idx is not None:
        st = {"status": K.ST_BROKEN, "confirm_idx": None, "broken_idx": broken_idx, "expired_idx": None,
              "reasons": ["OTHER_SIDE_BROKE_OUT"]}
    elif confirm_idx is not None:
        st = {"status": K.ST_CONFIRMED, "confirm_idx": confirm_idx, "broken_idx": None, "expired_idx": None, "reasons": []}
        for k in range(confirm_idx + 1, n):
            c = rows[k]["close"]
            if (c < inv) if side == K.LONG else (c > inv):
                st.update(status=K.ST_BROKEN, broken_idx=k)
                st["reasons"].append("FAILED_AFTER_CONFIRMATION")
                break
        if st["status"] == K.ST_CONFIRMED and (n - 1) - confirm_idx > cfg.fresh_bars:
            st.update(status=K.ST_EXPIRED, expired_idx=confirm_idx + cfg.fresh_bars + 1)
            st["reasons"].append("CONFIRMATION_STALE")
    else:
        st = {"status": K.ST_FORMING, "confirm_idx": None, "broken_idx": None, "expired_idx": None, "reasons": []}
    _finish(rec, rows, st, step=step, n=n, window=cfg.scenario_trigger_window, fresh_bars=cfg.fresh_bars, detected_idx=end)
    tr = cc.detect_trend(rows[:act["start"]], atr=atr[act["start"] - 1] if act["start"] >= 1 else None, cfg=cc.CandleContextConfig())
    rec["trend_context"], rec["role"] = tr.get("trend"), K.role_of(side, tr.get("trend"))
    rec["pattern_id"] = _pid(market, symbol, tf, "COMPRESSION_BREAKOUT", side, [int(rows[act["start"]]["timestamp"]), int(rows[end]["timestamp"])],
                             cfg.policy_version)
    return rec


def _levels_for_scenarios(rows, swings, reference_levels, step) -> list[dict]:
    """Süpürme/kırılım referansları: teyitli son tepe/dipler (teyit indeksinden SONRA kullanılabilir) + dışarıdan
    verilen seviyeler (ör. Box'un önceki gün tepe/dibi; `valid_from_ms` o seviyenin bilindiği an)."""
    out = []
    for p in swings:
        out.append({"name": "SWING_%s" % ("HIGH" if p["side"] == "high" else "LOW"), "level": float(p["level"]),
                    "kind": p["side"], "valid_from_idx": int(p["confirmed_at_index"]) + 1, "anchor_ts": int(p["timestamp"])})
    for r in reference_levels or []:
        try:
            lv = float(r["level"])
        except (KeyError, TypeError, ValueError):
            continue
        vf = int(r.get("valid_from_ms") or 0)
        idx = next((i for i, b in enumerate(rows) if int(b["timestamp"]) >= vf), len(rows))
        out.append({"name": str(r.get("name") or "REF"), "level": lv, "kind": "high" if str(r.get("kind")) == "high" else "low",
                    "valid_from_idx": idx, "anchor_ts": vf})
    return out


def _sweeps_and_breakouts(rows, atr, levels, *, market, symbol, tf, step, cfg) -> list[dict]:
    """SWEEP_RECLAIM (taşma + içeride kapanış; `overshoot_closes` ≥1 ise başarısız kırılım) ve RANGE_BREAKOUT
    (seviyenin ötesinde ardışık `breakout_hold_closes` kapanış). Niyet/bekleyen emir iddiası YOK."""
    n = len(rows)
    out: list[dict] = []
    horizon = max(0, n - 3 * cfg.scenario_trigger_window)
    for L in levels:
        lv, hi_side = L["level"], L["kind"] == "high"
        k = max(L["valid_from_idx"], horizon)
        while k < n:
            b = rows[k]
            over = (b["high"] > lv) if hi_side else (b["low"] < lv)
            inside_before = k > 0 and ((rows[k - 1]["close"] <= lv) if hi_side else (rows[k - 1]["close"] >= lv))
            if not over or not inside_before:
                k += 1
                continue
            # taşma başladı: kaç kapanış dışarıda kaldı?
            closes_out = 0
            ext = b["high"] if hi_side else b["low"]
            j = k
            reclaimed = None
            breakout_idx = None
            while j < n and j - k <= cfg.scenario_trigger_window:
                bj = rows[j]
                ext = max(ext, bj["high"]) if hi_side else min(ext, bj["low"])
                outside = (bj["close"] > lv) if hi_side else (bj["close"] < lv)
                if outside:
                    closes_out += 1
                    if closes_out >= cfg.breakout_hold_closes and breakout_idx is None:
                        breakout_idx = j
                    j += 1
                    continue
                reclaimed = j
                break
            a = atr[k]
            buf = cfg.stop_buffer_atr * a if a else 0.0
            if breakout_idx is not None:
                side = K.LONG if hi_side else K.SHORT
                rec = {"family": K.FAMILY_SCENARIO, "name": "RANGE_BREAKOUT", "side": side, "reference": L["name"], "atr": a,
                       "anchors": [{"ts": int(L["anchor_ts"]), "price": lv, "role": "referans", "confirmed_at_ms": None},
                                   {"ts": int(rows[k]["timestamp"]), "price": rows[k]["close"], "role": "ilk_kapanis_disarida", "confirmed_at_ms": int(rows[k]["timestamp"]) + step}],
                       "detected_at_ms": int(rows[breakout_idx]["timestamp"]) + step,
                       "trigger": {"rule": "closes_beyond_x%d" % cfg.breakout_hold_closes, "level": lv},
                       "invalidation": {"rule": "close_back_inside", "level": lv},
                       "stop": (lv - buf) if side == K.LONG else (lv + buf), "targets": [], "overshoot_closes": closes_out,
                       "geometry_quality": 1.0, "reason_codes": ["NO_STRUCTURAL_TARGET"],
                       "geometry": [{"kind": "referans", "t0": int(L["anchor_ts"]), "y0": lv, "t1": int(rows[breakout_idx]["timestamp"]), "y1": lv}]}
                st = {"status": K.ST_CONFIRMED, "confirm_idx": breakout_idx, "broken_idx": reclaimed, "expired_idx": None,
                      "reasons": []}
                if reclaimed is not None:
                    st["status"] = K.ST_BROKEN
                    st["reasons"].append("CLOSED_BACK_INSIDE")
                elif (n - 1) - breakout_idx > cfg.fresh_bars:
                    st.update(status=K.ST_EXPIRED, expired_idx=breakout_idx + cfg.fresh_bars + 1)
                    st["reasons"].append("CONFIRMATION_STALE")
                _finish(rec, rows, st, step=step, n=n, window=cfg.scenario_trigger_window, fresh_bars=cfg.fresh_bars,
                        detected_idx=breakout_idx)
                rec["pattern_id"] = _pid(market, symbol, tf, "RANGE_BREAKOUT", side, [int(L["anchor_ts"]), int(rows[k]["timestamp"])], cfg.policy_version)
                rec["trend_context"], rec["role"] = None, K.ROLE_UNKNOWN
                out.append(rec)
            if breakout_idx is None and reclaimed is None and closes_out >= 1 and j >= n:
                side = K.LONG if hi_side else K.SHORT
                rec = {"family": K.FAMILY_SCENARIO, "name": "RANGE_BREAKOUT", "side": side, "reference": L["name"], "atr": a,
                       "anchors": [{"ts": int(L["anchor_ts"]), "price": lv, "role": "referans", "confirmed_at_ms": None},
                                   {"ts": int(rows[k]["timestamp"]), "price": rows[k]["close"], "role": "ilk_kapanis_disarida", "confirmed_at_ms": int(rows[k]["timestamp"]) + step}],
                       "detected_at_ms": int(rows[k]["timestamp"]) + step,
                       "trigger": {"rule": "closes_beyond_x%d" % cfg.breakout_hold_closes, "level": lv},
                       "invalidation": {"rule": "close_back_inside", "level": lv},
                       "stop": (lv - buf) if side == K.LONG else (lv + buf), "targets": [], "overshoot_closes": closes_out,
                       "geometry_quality": 1.0, "reason_codes": ["AWAITING_SECOND_CLOSE_OUTSIDE"],
                       "geometry": [{"kind": "referans", "t0": int(L["anchor_ts"]), "y0": lv, "t1": int(rows[n - 1]["timestamp"]), "y1": lv}]}
                st = {"status": K.ST_FORMING, "confirm_idx": None, "broken_idx": None, "expired_idx": None, "reasons": []}
                _finish(rec, rows, st, step=step, n=n, window=cfg.scenario_trigger_window, fresh_bars=cfg.fresh_bars, detected_idx=k)
                rec["pattern_id"] = _pid(market, symbol, tf, "RANGE_BREAKOUT", side, [int(L["anchor_ts"]), int(rows[k]["timestamp"])], cfg.policy_version)
                rec["trend_context"], rec["role"] = None, K.ROLE_UNKNOWN
                out.append(rec)
            if reclaimed is not None:
                side = K.SHORT if hi_side else K.LONG
                inv = ext
                rec = {"family": K.FAMILY_SCENARIO, "name": "SWEEP_RECLAIM", "side": side, "reference": L["name"], "atr": a,
                       "anchors": [{"ts": int(L["anchor_ts"]), "price": lv, "role": "referans", "confirmed_at_ms": None},
                                   {"ts": int(rows[k]["timestamp"]), "price": ext, "role": "tasma_ucu", "confirmed_at_ms": int(rows[k]["timestamp"]) + step},
                                   {"ts": int(rows[reclaimed]["timestamp"]), "price": rows[reclaimed]["close"], "role": "iceride_kapanis",
                                    "confirmed_at_ms": int(rows[reclaimed]["timestamp"]) + step}],
                       "detected_at_ms": int(rows[reclaimed]["timestamp"]) + step,
                       "trigger": {"rule": "close_back_inside", "level": lv},
                       "invalidation": {"rule": "close_above" if hi_side else "close_below", "level": inv},
                       "stop": (inv + buf) if hi_side else (inv - buf), "targets": [], "overshoot_closes": closes_out,
                       "failed_breakout": closes_out >= 1,
                       "geometry_quality": round(min(1.0, abs(ext - lv) / (a or abs(ext - lv) or 1.0)), 4),
                       "reason_codes": ["NO_INTENT_CLAIM", "NO_STRUCTURAL_TARGET"] + (["FAILED_BREAKOUT"] if closes_out >= 1 else []),
                       "geometry": [{"kind": "referans", "t0": int(L["anchor_ts"]), "y0": lv, "t1": int(rows[reclaimed]["timestamp"]), "y1": lv}]}
                st = {"status": K.ST_CONFIRMED, "confirm_idx": reclaimed, "broken_idx": None, "expired_idx": None, "reasons": []}
                for q in range(reclaimed + 1, n):
                    cq = rows[q]["close"]
                    if (cq > inv) if hi_side else (cq < inv):
                        st.update(status=K.ST_BROKEN, broken_idx=q)
                        st["reasons"].append("FAILED_AFTER_CONFIRMATION")
                        break
                if st["status"] == K.ST_CONFIRMED and (n - 1) - reclaimed > cfg.fresh_bars:
                    st.update(status=K.ST_EXPIRED, expired_idx=reclaimed + cfg.fresh_bars + 1)
                    st["reasons"].append("CONFIRMATION_STALE")
                _finish(rec, rows, st, step=step, n=n, window=cfg.scenario_trigger_window, fresh_bars=cfg.fresh_bars,
                        detected_idx=reclaimed)
                rec["pattern_id"] = _pid(market, symbol, tf, "SWEEP_RECLAIM", side,
                                         [int(L["anchor_ts"]), int(rows[k]["timestamp"]), int(rows[reclaimed]["timestamp"])], cfg.policy_version)
                rec["trend_context"], rec["role"] = None, K.ROLE_UNKNOWN
                out.append(rec)
            k = (reclaimed + 1) if reclaimed is not None else (j + 1)
    return out


def _retests(rows, atr, levels, *, market, symbol, tf, step, cfg) -> list[dict]:
    """BREAK_RETEST_HOLD: teyitli seviye kapanışla kırılır, `retest_window` içinde fiyat seviyeye tolerans içinde döner
    ve seviyenin DOĞRU tarafında kapanır → CONFIRMED. Seviyenin ötesine (tolerans dahil) geri kapanış → BROKEN."""
    n = len(rows)
    out: list[dict] = []
    horizon = max(0, n - 3 * cfg.retest_window)
    for L in levels:
        lv, up = L["level"], L["kind"] == "high"             # tepe kırılırsa LONG, dip kırılırsa SHORT
        b0 = None
        for k in range(max(L["valid_from_idx"], horizon, 1), n):
            crossed = (rows[k]["close"] > lv and rows[k - 1]["close"] <= lv) if up else (rows[k]["close"] < lv and rows[k - 1]["close"] >= lv)
            if crossed:
                b0 = k
                break
        if b0 is None:
            continue
        a = atr[b0]
        if not a:
            continue
        tol = cfg.retest_tolerance_atr * a
        side = K.LONG if up else K.SHORT
        st = {"status": K.ST_FORMING, "confirm_idx": None, "broken_idx": None, "expired_idx": None, "reasons": []}
        retest_idx = None
        for r in range(b0 + 1, n):
            br = rows[r]
            if (br["close"] < lv - tol) if up else (br["close"] > lv + tol):
                st.update(status=K.ST_BROKEN, broken_idx=r)
                st["reasons"].append("CLOSED_BACK_THROUGH_LEVEL")
                break
            if st["confirm_idx"] is None:
                touched = (br["low"] <= lv + tol) if up else (br["high"] >= lv - tol)
                held = (br["close"] > lv) if up else (br["close"] < lv)
                if touched and held:
                    st.update(status=K.ST_CONFIRMED, confirm_idx=r)
                    retest_idx = r
                    continue
                if r - b0 >= cfg.retest_window:
                    st.update(status=K.ST_EXPIRED, expired_idx=r)
                    st["reasons"].append("NO_RETEST_IN_WINDOW")
                    break
        if st["status"] == K.ST_CONFIRMED and (n - 1) - st["confirm_idx"] > cfg.fresh_bars:
            st.update(status=K.ST_EXPIRED, expired_idx=st["confirm_idx"] + cfg.fresh_bars + 1)
            st["reasons"].append("CONFIRMATION_STALE")
        inv = (lv - tol) if up else (lv + tol)
        buf = cfg.stop_buffer_atr * a
        anchors = [{"ts": int(L["anchor_ts"]), "price": lv, "role": "seviye", "confirmed_at_ms": None},
                   {"ts": int(rows[b0]["timestamp"]), "price": rows[b0]["close"], "role": "kirilis", "confirmed_at_ms": int(rows[b0]["timestamp"]) + step}]
        if retest_idx is not None:
            anchors.append({"ts": int(rows[retest_idx]["timestamp"]), "price": rows[retest_idx]["low"] if up else rows[retest_idx]["high"],
                            "role": "yeniden_test", "confirmed_at_ms": int(rows[retest_idx]["timestamp"]) + step})
        rec = {"family": K.FAMILY_SCENARIO, "name": "BREAK_RETEST_HOLD", "side": side, "reference": L["name"], "anchors": anchors, "atr": a,
               "detected_at_ms": int(rows[b0]["timestamp"]) + step,
               "trigger": {"rule": "retest_and_hold", "level": lv},
               "invalidation": {"rule": "close_below" if up else "close_above", "level": inv},
               "stop": (inv - buf) if up else (inv + buf), "targets": [],
               "geometry_quality": round(max(0.0, 1.0 - (abs((rows[retest_idx]["low"] if up else rows[retest_idx]["high"]) - lv) / tol)), 4) if retest_idx is not None else 0.0,
               "reason_codes": ["NO_STRUCTURAL_TARGET"],
               "geometry": [{"kind": "seviye", "t0": int(L["anchor_ts"]), "y0": lv, "t1": int(rows[n - 1]["timestamp"]), "y1": lv}]}
        _finish(rec, rows, st, step=step, n=n, window=cfg.retest_window, fresh_bars=cfg.fresh_bars, detected_idx=b0)
        rec["pattern_id"] = _pid(market, symbol, tf, "BREAK_RETEST_HOLD", side, [int(L["anchor_ts"]), int(rows[b0]["timestamp"])], cfg.policy_version)
        rec["trend_context"], rec["role"] = None, K.ROLE_UNKNOWN
        out.append(rec)
    return out


# ---------------------------------------------------------------------------- seviye bağlamı
def _level_context(rows, atr, swings, cfg, step) -> dict[str, Any]:
    n = len(rows)
    a = atr[-1] if atr else None
    conf = [p for p in (swings["highs"] + swings["lows"]) if p["confirmed_at_index"] < n]
    zones = []
    if a:
        for c in equal_level_clusters(sorted(conf, key=lambda p: p["index"]), atr=a, tolerance_atr=cfg.zone_tolerance_atr):
            zones.append({"level": c["level"], "side": c["side"], "n": c["n_members"], "member_ts": c["member_timestamps"],
                          "confirmed_at_ms": int(rows[int(c["confirmed_at_index"])]["timestamp"]) + step})
    hi = max((p for p in swings["highs"]), key=lambda p: p["index"], default=None)
    lo = max((p for p in swings["lows"]), key=lambda p: p["index"], default=None)
    ret = None
    if hi and lo and hi["level"] > lo["level"]:
        up_leg = lo["index"] < hi["index"]
        H, L = hi["level"], lo["level"]
        last = rows[-1]["close"]
        frac = (H - last) / (H - L) if up_leg else (last - L) / (H - L)
        ret = {"leg": "UP" if up_leg else "DOWN", "from": L if up_leg else H, "to": H if up_leg else L,
               "current_ratio": round(frac, 4),
               "levels": {str(r): (H - r * (H - L)) if up_leg else (L + r * (H - L)) for r in cfg.retracement_ratios},
               "decision_effect": "NONE_IN_structures_v1"}
    return {"zones": zones,
            "last_swing_high": ({"ts": int(hi["timestamp"]), "level": hi["level"], "confirmed_at_ms": int(rows[hi["confirmed_at_index"]]["timestamp"]) + step} if hi else None),
            "last_swing_low": ({"ts": int(lo["timestamp"]), "level": lo["level"], "confirmed_at_ms": int(rows[lo["confirmed_at_index"]]["timestamp"]) + step} if lo else None),
            "retracement": ret}


# ---------------------------------------------------------------------------- giriş noktası + önbellek
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 512
_CACHE_LOCK = threading.Lock()


def analyze(*, market: str, symbol: str, timeframe: str, bars: Any, as_of_ms: int,
            data_provenance: dict[str, Any] | None = None, reference_levels: list[dict[str, Any]] | None = None,
            cfg: K.StructuresConfig | None = None) -> dict[str, Any]:
    """Tek dilimin ortak analizi. Aynı (market, symbol, timeframe, kapanmış barlar, referanslar, sürüm) → AYNI sonuç
    (`analysis_id` eşit; süreç içi önbellek aynı nesneyi döndürür — çağıran DEĞİŞTİRMEMELİDİR)."""
    cfg = cfg or K.DEFAULT_CONFIG
    step = tf_ms(timeframe)
    rows = closed_rows(bars, timeframe=timeframe, as_of_ms=int(as_of_ms))
    fp = _fingerprint(rows)
    refkey = json.dumps(sorted([(str(r.get("name")), float(r.get("level") or 0), str(r.get("kind")), int(r.get("valid_from_ms") or 0))
                                for r in (reference_levels or [])]))
    last_ts = int(rows[-1]["timestamp"]) if rows else None
    aid = hashlib.sha256(("%s|%s|%s|%s|%s|%s|%s" % (market, symbol, timeframe, last_ts, cfg.policy_version, fp, refkey)).encode()).hexdigest()[:16]
    key = (aid,)
    with _CACHE_LOCK:
        hit = _CACHE.get(key, _MISSING)
        if hit is not _MISSING:
            _CACHE.move_to_end(key)
            return hit
    n = len(rows)
    prov = dict(data_provenance or {})
    prov.update({"n_closed_bars": n, "first_bar_ts": int(rows[0]["timestamp"]) if rows else None, "last_closed_bar_ts": last_ts,
                 "fingerprint": fp})
    out: dict[str, Any] = {"schema_version": K.SCHEMA_VERSION, "policy_version": cfg.policy_version, "analysis_id": aid,
                           "market": market, "symbol": symbol, "timeframe": timeframe, "as_of_ms": int(as_of_ms),
                           "last_closed_bar_ts": last_ts, "data_provenance": prov, "records": [], "rejects": [],
                           "levels": {}, "config": cfg.to_dict()}
    atr = _atr_series(rows, cfg.atr_len)
    recs: list[dict] = []
    rej: list[dict] = []
    r1, j1 = _candles(rows, atr, market=market, symbol=symbol, tf=timeframe, step=step, cfg=cfg, prov=prov)
    recs += r1
    rej += j1
    if n >= K.REQUIREMENTS["chart"]:
        r2, j2 = _chart(rows, atr, market=market, symbol=symbol, tf=timeframe, step=step, cfg=cfg)
        recs += r2
        rej += j2
    else:
        rej.append({"detector": "chart", "reason": "NOT_ENOUGH_BARS", "have": n, "need": K.REQUIREMENTS["chart"]})
    if n >= K.REQUIREMENTS["compression"]:
        recs += _compression(rows, atr, market=market, symbol=symbol, tf=timeframe, step=step, cfg=cfg)
    else:
        rej.append({"detector": "compression", "reason": "NOT_ENOUGH_BARS", "have": n, "need": K.REQUIREMENTS["compression"]})
    swings = confirmed_swings(rows, lookback=cfg.pivot_lookback) if n >= K.REQUIREMENTS["levels"] else {"highs": [], "lows": []}
    if n >= K.REQUIREMENTS["swing_scenarios"] or reference_levels:
        sel = sorted(swings["highs"], key=lambda p: p["index"])[-cfg.swing_levels:] + \
            sorted(swings["lows"], key=lambda p: p["index"])[-cfg.swing_levels:]
        lv = _levels_for_scenarios(rows, sel, reference_levels, step)
        recs += _sweeps_and_breakouts(rows, atr, lv, market=market, symbol=symbol, tf=timeframe, step=step, cfg=cfg)
        recs += _retests(rows, atr, [x for x in lv if x["name"].startswith("SWING")], market=market, symbol=symbol,
                         tf=timeframe, step=step, cfg=cfg)
    else:
        rej.append({"detector": "swing_scenarios", "reason": "NOT_ENOUGH_BARS", "have": n, "need": K.REQUIREMENTS["swing_scenarios"]})
    if n >= K.REQUIREMENTS["levels"]:
        out["levels"] = _level_context(rows, atr, swings, cfg, step)
    for r in recs:
        r.update({"market": market, "symbol": symbol, "timeframe": timeframe, "policy_version": cfg.policy_version,
                  "as_of_ms": int(as_of_ms), "analysis_id": aid, "data_provenance": {"fingerprint": fp, "last_closed_bar_ts": last_ts,
                                                                                    "source": prov.get("source"), "market": prov.get("market")}})
    # tekil kimlik + kararlı sıra (en yeni tanınan önce)
    uniq: dict[str, dict] = {}
    for r in recs:
        uniq.setdefault(r["pattern_id"], r)
    out["records"] = sorted(uniq.values(), key=lambda r: (-(r.get("detected_at_ms") or 0), r["family"], r["name"], r["pattern_id"]))
    out["rejects"] = rej
    with _CACHE_LOCK:
        _CACHE[key] = out
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return out


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


__all__ = ["analyze", "clear_cache", "closed_rows", "pattern_bars"]
