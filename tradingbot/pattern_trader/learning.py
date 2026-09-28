# -*- coding: utf-8 -*-
"""ÖĞRENME MODU — FORMASYON DEFTERİ YARDIMCILARI (2026-09-28, öğrenme modu). Yalnız PAPER.

`PatternBook` buradaki fonksiyonları YALNIZ öğrenme aktifken (`learning is not None and learning.on`) çağırır; anahtar
kapalıyken hiçbir yol buraya girmez (bit-aynı). Sözleşme (CONTRACT / SPEC §3, D2/D3/D6/D17):

* Boyut: `learning_mode.fit_size` — eşit slot, %0,5 hedef risk (taban `risk.equity_basis`), liq mesafesi ≥ 2 × stop,
  gerekirse min-notional'a çıkarma (%2 tavan + serbest marj). Boyut GERÇEKLEŞME fiyatıyla (`market_fill_price`)
  hesaplanır: tick yuvarlaması riski büyüttüyse notional kendiliğinden 1/oran küçülür (D6 — ret yerine ölçekleme).
* Derinlik (D17): öğrenmede eşik max(2.000, 20 × notional) USDT.
* Risk: öğrenme RiskEngine'i (`profile_for`: toplam açık risk 100, işlem başı %2 tavan AYNEN) — çağıran verir.
* Defter: `open(..., allow_shrink=True)` yalnız yedek; kalıcı `allow_shrink` özniteliği DEĞİŞMEZ.

Neden `strategy_paper.apply_action` değil: formasyon defterinin iki özel ihtiyacı (D6 yuvarlama oranı, D17 notional'a
bağlı derinlik) boyuttan ÖNCE/SONRA defter içinde ölçülür; ortak uygulayıcının öğrenme dalı bunları taşımaz.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from ..accounting import SizeSpec
from ..accounting.models import AmountType
from ..learning_cf import STALE_GRACE_BARS
from ..learning_mode import RISK_NOTIONAL_ROUND_TOL, SIZE_BUMP, SIZE_SHRUNK, fit_size
from ..timeframes import tf_ms
from .data import BARS_PER_TF

#: D17 — öğrenmede derinlik tabanı ve notional çarpanı (pozisyonlar ~10 USDT; 20.000 sabiti anlamsızlaşır).
LEARNING_MIN_DEPTH_USDT = 2_000.0
LEARNING_DEPTH_NOTIONAL_MULT = 20.0
#: Karşı-olgusal ufuk dilimi seçilirken indirilen pencerenin kapsaması gereken ek bar (etiket + bayatlık payı).
CF_COVER_MARGIN_BARS = STALE_GRACE_BARS + 2
#: Taban defterin (bugünkü) min-notional varsayımı — `apply_action` ve `RiskEngine.evaluate` ile aynı.
BASE_MIN_NOTIONAL = 5.0


def min_depth_for(notional: float) -> float:
    """D17: öğrenmede gereken 0,5% derinlik = max(2.000, 20 × notional)."""
    try:
        n = float(notional)
    except (TypeError, ValueError):
        n = 0.0
    return max(LEARNING_MIN_DEPTH_USDT, LEARNING_DEPTH_NOTIONAL_MULT * (n if math.isfinite(n) and n > 0 else 0.0))


def cf_horizon(max_hold_bars_15m: int) -> tuple[int, int]:
    """Karşı-olgusal etiket dilimi ve ufku (TARGET_STOP_TIME). Zaman stopu defterde 15m biriminde tutulur.

    Defterin ELİNDEKİ barlarla (15m/1h/4h, `BARS_PER_TF` kadar) etiketlenir: ufku + bayatlık payını kapsayan EN KÜÇÜK
    dilim seçilir. v3 (96 saat): 15m penceresi (240 bar = 60 saat) yetmez → 1h (96 bar); klasik (24 saat) → 15m.
    Pencere başı kapsanmazsa etiket erken barları KAÇIRIRDI (sessiz yanlış etiket); bu yüzden kapsama şarttır."""
    hold_ms = max(1, int(max_hold_bars_15m)) * tf_ms("15m")
    for tf in ("15m", "1h", "4h"):
        step = tf_ms(tf)
        if hold_ms % step == 0 and hold_ms // step + CF_COVER_MARGIN_BARS <= int(BARS_PER_TF.get(tf, 240)):
            return step // 60_000, hold_ms // step
    step = tf_ms("4h")
    return step // 60_000, max(1, -(-hold_ms // step))


def _floor_qty(notional: float, price: float, step: Any) -> Decimal:
    s = Decimal(str(step))
    q = Decimal(repr(float(notional))) / Decimal(repr(float(price)))
    if s <= 0:
        return q
    return (q / s).to_integral_value(rounding=ROUND_FLOOR) * s


@dataclass
class LearningOpen:
    """`open_learning` sonucu: `pos` None ise `reason` ret kodudur; `info` plana/pozisyona yazılan kanıttır."""
    pos: Any
    reason: str = ""
    info: dict = field(default_factory=dict)


def open_learning(*, act: dict[str, Any], symbol: str, price: float, fill_price: float, tick: Any, now: datetime,
                  ledger: Any, risk: Any, state: Any, filters: Any, run_id: str, learning: Any, data: Any,
                  max_position_pct: float | None, depth: float | None = None,
                  unlocked: list[str] | None = None) -> LearningOpen:
    """ÖĞRENME GİRİŞİ (D2/D3): `fit_size` → derinlik (D17) → öğrenme RiskEngine'i → defter (allow_shrink yedek).

    `price` referans mark (defter gerçekleşmeyi bundan üretir), `fill_price` = `ledger.market_fill_price` önizlemesi
    (boyut ve risk BU fiyatla). `data` fail-closed veri hükmü (`apply_action` ile aynı sözleşme). Pozisyon kanıtı:
    `pos.meta["learning"]` = {size_rule, slots, risk_pct, risk_fraction_of_budget, learning_unlocked_by, ...}."""
    if data is None or not data.ok or not data.entry_ok:
        return LearningOpen(None, (data.reason if (data is not None and data.reason) else "DATA_VERDICT_MISSING"))
    direction = str(act.get("direction") or "LONG").upper()
    entry, fill, stop = float(price), float(fill_price), float(act.get("stop") or 0.0)
    if entry <= 0 or fill <= 0 or stop <= 0 or (direction == "LONG" and stop >= fill) or (direction == "SHORT" and stop <= fill):
        return LearningOpen(None, "STRATEGY_BAD_STOP")
    E = float(risk.equity_basis(state))
    mn = float(filters.min_notional) or BASE_MIN_NOTIONAL    # RiskEngine ile aynı: 0/None → 5
    lev_max = int(learning.leverage_max)
    for cap in (getattr(getattr(risk, "profile", None), "futures_max_leverage", None), getattr(filters, "max_leverage", None)):
        if cap:                                                # borsa ve profil tavanı: fit ile RiskEngine AYNI kaldıraçta
            lev_max = min(lev_max, int(cap))
    lev_max = max(1, lev_max)
    fit = fit_size(equity=E, entry=fill, stop=stop, slots=int(learning.slots), leverage_max=lev_max,
                   risk_pct=float(learning.risk_pct), reserve_pct=float(learning.reserve_pct),
                   liq_buffer_mult=float(learning.liq_buffer_mult), min_notional=mn, qty_step=float(filters.qty_step),
                   price_for_step=fill, hard_cap_pct=float(learning.hard_cap_pct),
                   min_notional_bump=bool(learning.min_notional_bump), available_margin=float(ledger.available),
                   min_qty=float(filters.min_qty), max_position_pct=max_position_pct)
    info: dict[str, Any] = {"book": str(learning.name), "equity_basis": E, "leverage_max": lev_max, "min_notional": mn,
                            "fit": {"ok": fit.ok, "notional": round(fit.notional, 6), "leverage": fit.leverage,
                                    "margin": round(fit.margin, 6), "risk_usdt": round(fit.risk_usdt, 6),
                                    "size_rule": fit.size_rule, "reason": fit.reason,
                                    "detail": {k: (round(v, 8) if isinstance(v, float) else v) for k, v in fit.detail.items()}}}
    if not fit.ok:
        return LearningOpen(None, fit.reason or "MIN_ORDER_CONFLICT", info)
    if depth is not None:
        need = min_depth_for(fit.notional)
        info["min_depth_0_5pct"] = need
        if float(depth) < need:
            return LearningOpen(None, "THIN_DEPTH", info)
    plan_dict = {"symbol": symbol, "market_type": "USDM_PERP", "direction": direction, "entry": fill, "stop": stop,
                 "targets": list(act.get("targets") or []), "notional": fit.notional, "margin": fit.margin,
                 "leverage": fit.leverage, "amount_type": "NOTIONAL", "expected_r": float(act.get("expected_r") or 0.0),
                 "min_notional": mn}
    rd = risk.evaluate(plan_dict, state, {"now_utc": now})
    if not rd.allowed:
        return LearningOpen(None, (rd.reasons or ["RISK_DENIED"])[0], info)
    lev = int(rd.adjusted_leverage or fit.leverage)
    notional = fit.notional
    adj = float(rd.adjusted_notional or notional)
    # gerçek AŞAĞI ayar (RiskEngine'in 4 hane yuvarlama artığı ≤ 5e-5 değil; göreli tolerans 20 USDT'de 2e-5'ti → çıkarma
    # NOTIONAL'a düşüp bir adım kaybediyordu, 2026-09-28 öğrenme modu)
    if adj < notional - RISK_NOTIONAL_ROUND_TOL:
        notional = adj
    if fit.size_rule == SIZE_BUMP and fit.detail.get("qty") and notional == fit.notional:
        qty = Decimal(str(fit.detail["qty"]))                  # yukarı yuvarlanmış adım: defter aşağı yuvarlayıp kaybetmesin
    else:
        qty = _floor_qty(notional, fill, filters.qty_step)
    if qty <= 0:
        return LearningOpen(None, "STEP_ZERO_QTY", info)
    data_src = {"market": data.market, "source": data.source, "tour_id": data.tour_id, "bars": dict(data.bars), "btc": dict(data.btc)}
    feats = {"regime": act.get("regime"), "market_type": "USDM_PERP", "strategy": act.get("name"),
             "expected_r": float(act.get("expected_r") or 0.0), "p_win": None, "data_source": data_src,
             "structure": dict(act["structure"]) if act.get("structure") else None}
    pos = ledger.open(symbol, direction, entry, SizeSpec(qty, AmountType.QUANTITY, lev),
                      filters=filters, stop=stop, targets=list(act.get("targets") or []),
                      setup_type=str(act.get("setup_type") or "strategy"), trigger_text=str(act.get("reason") or ""),
                      features=feats, tick=tick, now=now,
                      meta={"run_id": run_id, "strategy": str(act.get("name") or ""), "data_source": data_src},
                      allow_shrink=True)
    if pos is None:
        return LearningOpen(None, ledger.last_reject_reason or "LEDGER_REJECT", info)
    rule = SIZE_SHRUNK if pos.meta.get("shrunk_to_margin") else str(fit.size_rule)
    budget = float(fit.detail.get("risk_budget_usdt") or 0.0)
    risk_usdt = float(pos.qty) * abs(float(pos.entry_avg) - stop)
    meta = {"size_rule": rule, "slots": int(learning.slots), "risk_pct": float(learning.risk_pct),
            "risk_fraction_of_budget": round(risk_usdt / budget, 6) if budget > 0 else None,
            "learning_unlocked_by": sorted(set(unlocked or [])), "book": str(learning.name), "leverage": int(pos.leverage),
            "leverage_max": lev_max, "equity_basis": E, "risk_usdt": round(risk_usdt, 6),
            "notional": round(float(pos.qty * pos.entry_avg), 6), "margin": round(float(pos.isolated_margin), 6)}
    if "min_depth_0_5pct" in info:
        meta["min_depth_0_5pct"] = info["min_depth_0_5pct"]
    pos.meta["learning"] = dict(meta)
    pos.features["learning"] = dict(meta)
    info["meta"] = meta
    return LearningOpen(pos, "", info)


def baseline_blockers(*, act: dict[str, Any], symbol: str, mark: float, now: datetime, ledger: Any, risk: Any,
                      profile: Any, state: Any) -> list[str]:
    """`learning_unlocked_by` için UCUZ yeniden ölçüm: bugünkü (baseline) boyutla aynı plan taban RiskEngine'inden ve
    defter marjından geçer miydi? Döner: engelleyecek kodlar (TOTAL_OPEN_RISK, INSUFFICIENT_MARGIN, ...). Durum
    DEĞİŞTİRMEZ; yalnız etiket içindir."""
    stop = float(act.get("stop") or 0.0)
    if mark <= 0 or stop <= 0 or mark == stop:
        return []
    s0 = abs(mark - stop) / mark
    lev = max(1, int(act.get("leverage") or 1))
    nb = float(profile.risk_per_trade_pct) / 100.0 * float(state.equity) / s0
    if act.get("cap_notional_to_position_pct"):
        cap2 = float(risk.equity_basis(state)) * float(getattr(profile, "max_position_pct", 0.0)) / 100.0 * lev
        if cap2 > 0 and nb > cap2:
            nb = cap2 * 0.999
    plan = {"symbol": symbol, "market_type": "USDM_PERP", "direction": str(act.get("direction") or "LONG").upper(),
            "entry": mark, "stop": stop, "targets": list(act.get("targets") or []), "notional": nb, "margin": nb / lev,
            "leverage": lev, "amount_type": "NOTIONAL", "expected_r": 0.0, "min_notional": BASE_MIN_NOTIONAL}
    out: list[str] = []
    try:
        rd = risk.evaluate(plan, state, {"now_utc": now})
        out.extend(str(c) for c in (rd.reasons or []) if not rd.allowed)
    except Exception:  # noqa: BLE001 — etiket hesabı girişi ETKİLEMEZ
        pass
    try:
        taker = float(ledger.fees.rate(False))
        if nb / lev + nb * taker > float(ledger.available):
            out.append("INSUFFICIENT_MARGIN")
    except Exception:  # noqa: BLE001
        pass
    return out


__all__ = ["BASE_MIN_NOTIONAL", "CF_COVER_MARGIN_BARS", "LEARNING_DEPTH_NOTIONAL_MULT", "LEARNING_MIN_DEPTH_USDT",
           "LearningOpen", "baseline_blockers", "cf_horizon", "min_depth_for", "open_learning"]
