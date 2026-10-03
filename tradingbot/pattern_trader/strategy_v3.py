# -*- coding: utf-8 -*-
"""FORMASYON BOTU PROTOKOLÜ v3 — 4 SAATLİK MOMENTUM: "üç beyaz asker" + RSI14 > 70 → LONG (tek sinyal).

Kaynak: sinyal laboratuvarı (`tradingbot/signal_lab.py`), GitHub çalıştırması 36123072720 (2026-09-25): 30 coin,
4h 4 yıl (2022 düşüşü dahil), 1.412 kombinasyondan sıkı testi geçen TEK sonuç. Keşif +0,15R (n=1412), doğrulama
+0,26R (n=736), aynı bağlamdaki rastgele girişe göre +0,19/+0,07; maliyet (taker + kayma, gidiş-dönüş %0,16) sonrası.
Bu bir geçmiş test bulgusudur: kâr garantisi DEĞİLDİR; PAPER'da ayrıca ölçülür.

Kural — laboratuvarın test ettiği işlemle BİREBİR (parite testi: `tests/test_pattern_trader_momentum_v3.py`):
* Sinyal: ortak katalogda (`structures.analyze`, 4h) `THREE_WHITE_SOLDIERS` LONG kaydının TEYİDİ, en son kapanmış
  4h barda (taze). Teyit barının kapanışındaki RSI14 (Wilder; laboratuvarla AYNI fonksiyon `signal_lab.indicators`)
  70'in üstünde.
* Giriş: teyit kapanışından sonraki ilk doğrulanmış perp fiyatı (laboratuvar: sonraki barın açılışı). Tetikten
  1 ATR'den uzak fiyattan girilmez (kovalama). Risk (giriş-stop) 0,1–5 ATR dışında ise girilmez (test aralığı).
* Stop: katalog kaydının stop'u (geçersizlik − 0,25×ATR). Hedef: GERÇEK giriş fiyatından 2R (mum kaydının yapısal
  hedefi yoktur). Zaman stopu: 24 × 4h = 96 saat.
* Evren: laboratuvarda test edilen 30 coin (`V3_SYMBOLS`); likidite/spread/derinlik kapıları ve risk motoru aynen.

Yalnız LONG: testte short tarafı (üç kara karga) sıkı testi geçemedi.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd

from ..signal_lab import WIDE_SYMBOLS, indicators
from ..structures import catalog as K
from ..structures.policy import already_used
from ..timeframes import tf_ms
from .strategy import PL_AWAITING, PL_TRIGGERED
from .universe import iso_ms

PROTOCOL_V3 = "pattern_protocol_v3.0.0"
PROTOCOL_NAME = "momentum_4h_v3"
V3_TF = "4h"
FAMILY_V3 = "M3_MOMENTUM_3WS_RSI70"
V3_SIGNAL = {"name": "THREE_WHITE_SOLDIERS", "side": K.LONG, "rsi_min": 70.0, "target_rr": 2.0, "hold_bars": 24,
             "chase_atr": 1.0, "min_risk_atr": 0.1, "max_risk_atr": 5.0, "entry_window_min": 60}
V3_SYMBOLS: tuple[str, ...] = tuple(WIDE_SYMBOLS)
#: Zaman stopu defterde giriş dilimi (15m) biriminde tutulur: 24 × 4h = 384 × 15m.
V3_MAX_HOLD_15M = V3_SIGNAL["hold_bars"] * tf_ms(V3_TF) // tf_ms("15m")
EVIDENCE = {"source": "signal_lab", "github_run": 36123072720, "coins": 30, "history_4h_days": 1460,
            "discovery": {"n": 1412, "mean_r": 0.151, "ci95": [0.02, 0.28]},
            "validation": {"n": 736, "mean_r": 0.263, "ci95": [0.03, 0.48]}, "vs_random": [0.19, 0.07],
            "note_tr": "geçmiş test; kâr garantisi değildir"}


def plan_id_v3(symbol: str, pattern_id: str) -> str:
    return hashlib.sha256(f"{symbol}|USDM_PERP|{FAMILY_V3}|LONG|{pattern_id}|{PROTOCOL_V3}".encode("utf-8")).hexdigest()[:16]


def rsi_at_close(bars: list[dict[str, Any]], idx: int) -> float | None:
    """`idx` barının kapanışındaki RSI14 — yalnız o ana kadarki barlarla, laboratuvarla AYNI hesap."""
    if idx < 14:
        return None
    df = pd.DataFrame(bars[:idx + 1])
    if "volume" not in df:
        df["volume"] = 0.0
    v = float(indicators(df)["rsi"][-1])
    return None if v != v else v


def build_plans_v3(symbol: str, *, as_of_ms: int, analyses: dict[str, dict[str, Any] | None], bars_by_tf: dict[str, list],
                   universe_entry: dict[str, Any] | None = None, data_source: dict[str, Any] | None = None,
                   used_patterns: Any = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """4h katalog kaydı → TETİKLENMİŞ plan (giriş denemesi aynı taramada, `PatternBook._try_open`). Döner: (planlar,
    plana dönüşmeyen kayıtların gerekçeleri)."""
    step = tf_ms(V3_TF)
    used = used_patterns if used_patterns is not None else set()
    skipped: list[dict[str, Any]] = []
    an = analyses.get(V3_TF)
    if not an:
        return [], [{"family": FAMILY_V3, "tf": V3_TF, "reason": "ANALYSIS_UNAVAILABLE"}]
    if str(an.get("market") or "") != "USDM_PERP":
        return [], [{"family": FAMILY_V3, "tf": V3_TF, "reason": "ANALYSIS_MARKET_%s" % (an.get("market") or "UNKNOWN")}]
    bars = [b for b in (bars_by_tf.get(V3_TF) or []) if int(b["timestamp"]) + step <= int(as_of_ms)]
    if not bars:
        return [], [{"family": FAMILY_V3, "tf": V3_TF, "reason": "NO_CLOSED_BARS"}]
    last_close = int(bars[-1]["timestamp"]) + step
    idx_of = {int(b["timestamp"]): i for i, b in enumerate(bars)}
    ue = universe_entry or {}
    plans: list[dict[str, Any]] = []
    for rec in an.get("records") or []:
        if rec.get("name") != V3_SIGNAL["name"] or rec.get("side") != V3_SIGNAL["side"]:
            continue
        c_ms = rec.get("confirmed_at_ms")
        if not c_ms or int(c_ms) != last_close:
            continue                                   # yalnız EN SON kapanan barda teyit (taze); geçmişe giriş yok
        if already_used(rec, used):
            skipped.append({"family": FAMILY_V3, "pattern_id": rec.get("pattern_id"), "reason": "PATTERN_ALREADY_USED"})
            continue
        i = idx_of.get(int(c_ms) - step)
        rsi = rsi_at_close(bars, i) if i is not None else None
        if rsi is None or not rsi > V3_SIGNAL["rsi_min"]:
            skipped.append({"family": FAMILY_V3, "pattern_id": rec.get("pattern_id"), "reason": "RSI_NOT_ABOVE_70",
                            "rsi": None if rsi is None else round(rsi, 2)})
            continue
        trig = (rec.get("trigger") or {}).get("level")
        stop, atr = rec.get("stop"), rec.get("atr")
        if trig is None or stop is None or not atr:
            skipped.append({"family": FAMILY_V3, "pattern_id": rec.get("pattern_id"), "reason": "RECORD_GEOMETRY_INCOMPLETE"})
            continue
        cb = rec.get("confirm_bar") or {}
        ref = float(cb.get("close") or bars[i]["close"])
        stop, trig, atr = float(stop), float(trig), float(atr)
        expires = int(c_ms) + int(V3_SIGNAL["entry_window_min"]) * 60_000
        target_ref = ref + V3_SIGNAL["target_rr"] * (ref - stop)       # bilgi amaçlı; gerçek hedef girişte yeniden
        pl = {"version": PROTOCOL_V3, "protocol": PROTOCOL_NAME, "plan_id": plan_id_v3(symbol, str(rec["pattern_id"])),
              "symbol": symbol, "market": "USDM_PERP", "family": FAMILY_V3,
              "family_title_tr": "4h momentum: üç beyaz asker + RSI>70 (laboratuvar adayı)", "side": K.LONG,
              "entry_tf": V3_TF, "finding_ids": [rec["pattern_id"]], "pattern_id": rec["pattern_id"],
              "anchor_bar_ts": int(rec["anchors"][-1]["ts"]) if rec.get("anchors") else int(c_ms) - step,
              "created_at_ms": int(as_of_ms), "created_at": iso_ms(as_of_ms), "valid_from_ms": int(c_ms),
              "expires_at_ms": expires, "expires_at": iso_ms(expires), "expires_after_bars": 1,
              "trigger": {"level": round(trig, 10), "rule": "close_above", "tf": V3_TF, "sloped": False,
                          "text_tr": "4h boğa kapanışı üstünde %.6g (üç beyaz asker teyidi)" % trig},
              "invalidation": dict(rec.get("invalidation") or {}), "stop": round(stop, 10),
              "stop_rule": "katalog kaydı: geçersizlik − %.2f×ATR" % K.DEFAULT_CONFIG.stop_buffer_atr,
              "stop_buffer_atr": K.DEFAULT_CONFIG.stop_buffer_atr, "atr": round(atr, 10),
              "target": round(target_ref, 10), "target_source": "entry_rr_2.0",
              "target_rule": "gerçek giriş fiyatından %.1fR (laboratuvarla aynı)" % V3_SIGNAL["target_rr"],
              "target_from_entry_rr": float(V3_SIGNAL["target_rr"]),
              "risk_atr_bounds": [V3_SIGNAL["min_risk_atr"], V3_SIGNAL["max_risk_atr"]],
              "rr_gross": None, "rr_after_cost": None, "min_rr_after_cost": 1.0,
              "chase_atr": float(V3_SIGNAL["chase_atr"]), "entry_type": "MARKET_AFTER_TRIGGER_CLOSE", "leverage": 1,
              "max_hold_bars": int(V3_MAX_HOLD_15M), "max_hold_note_tr": "24 × 4h = 96 saat (15m biriminde 384)",
              "p_win": None, "expected_r": None, "edge_note_tr": "laboratuvar adayı; PAPER'da ölçülüyor",
              "cohort": ue.get("cohort"), "age_h_at_plan": ue.get("age_h"), "futures_first_trade_ms": ue.get("futures_first_trade_ms"),
              "status": PL_TRIGGERED,
              "status_history": [{"status": PL_AWAITING, "at_ms": int(as_of_ms), "reason": "PLAN_CREATED_FROM_CATALOG_V3"},
                                 {"status": PL_TRIGGERED, "at_ms": int(c_ms), "reason": "RECORD_CONFIRMED_THREE_WHITE_SOLDIERS"}],
              "last_evaluated_bar_ts": None, "triggered_at_ms": int(c_ms), "trigger_bar_ts": cb.get("ts"),
              "trigger_close": cb.get("close"), "position_id": None, "reasons": [],
              "evidence": {"rsi14_at_confirm": round(rsi, 4), "lab": dict(EVIDENCE),
                           "record": {k: rec.get(k) for k in ("pattern_id", "name", "family", "timeframe", "status",
                                                              "detected_at_ms", "confirmed_at_ms", "analysis_id")}},
              "structure": {"pattern_id": rec["pattern_id"], "name": rec.get("name"), "family": rec.get("family"),
                            "timeframe": V3_TF, "status": rec.get("status"), "analysis_id": rec.get("analysis_id"),
                            "policy_version": K.POLICY_VERSION, "confirmed_at_ms": int(c_ms), "side": K.LONG,
                            "trigger": {"level": round(trig, 10), "rule": "close_above", "tf": V3_TF}},
              "structure_geometry": {"anchors": rec.get("anchors"), "confirm_bar": cb, "detected_at_ms": rec.get("detected_at_ms"),
                                     "atr": rec.get("atr")},
              "record_revisions": 0, "data_source": dict(data_source or {}), "size": None, "risk": None}
        plans.append(pl)
    return plans, skipped


__all__ = ["EVIDENCE", "FAMILY_V3", "PROTOCOL_NAME", "PROTOCOL_V3", "V3_MAX_HOLD_15M", "V3_SIGNAL", "V3_SYMBOLS", "V3_TF",
           "build_plans_v3", "plan_id_v3", "rsi_at_close"]
