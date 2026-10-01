"""Vadeli (USDⓈ-M perpetual) long/short bar-bar backtester — izole marj yaklaşımı.

Kurallar
* Sinyal bar KAPANIŞINDA (+1 long / -1 short / 0 düz), dolum bir SONRAKİ barın AÇILIŞINDA (look-ahead yok).
  Ters sinyal → aynı açılışta kapat + ters aç. NaN sinyal 0 sayılır.
* Bar içi kötümser sıra: liq > stop > tp (long: low ≤ liq → liq; low ≤ stop → stop; high ≥ tp → tp; short ayna).
  Gap varsa dolum açılıştan (daha kötü fiyat).
* Likidasyon fiyatı (izole yaklaşık): entry × (1 ∓ (1/lev − mmr)). Likidasyonda kayıp marj + liq ücreti ile SINIRLI.
* Funding: `funding_rates` (df.index'e hizalı) UTC saat {0,8,16} ve dakika 0 olan barlarda uygulanır:
  pnl -= side × notional × rate (long pozitif oranda ÖDER).
* Ücret (taker) + kayma her iki yönde; funding ve ücret işlem kaydında ayrı toplanır.
* Boyut: risk_per_trade_pct × equity / stop mesafesi; marj ≤ max_position_pct × equity; kaldıraç tavanı; min_notional;
  qty_step aşağı yuvarlama.
* TP1/TP2 (R katı) isteğe bağlı: TP1'de yarı kapat + (breakeven_after_tp1) stop → giriş; TP2'de kalan. Sadece TP1
  verilirse tamamı TP1'de kapanır. ATR iz süren stop isteğe bağlı (`atr_trailing=True`).
* MAE/MFE giriş fiyatına göre yüzde (high/low'dan).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import indicators as ind
from .core import FUNDING_HOURS_UTC
from .validation import metrics_extended


@dataclass
class FuturesTrade:
    entry_time: str
    exit_time: str
    side: int                # +1 long, -1 short
    entry: float
    exit: float
    qty: float
    notional: float
    leverage: float
    pnl: float               # net (ücret + funding düşülmüş)
    r: float
    mae_pct: float
    mfe_pct: float
    reason: str              # stop | tp1 | tp2 | liq | signal | reverse | end
    funding: float
    fees: float
    bars_held: int
    margin: float = 0.0
    tp1_hit: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FuturesBacktestResult:
    trades: list[FuturesTrade] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    equity: pd.Series | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"trades": [t.to_dict() for t in self.trades], "metrics": self.metrics,
                "equity": None if self.equity is None else {str(k): float(v) for k, v in self.equity.items()}}


def _is_settlement(ts: Any) -> bool:
    try:
        t = pd.Timestamp(ts)
    except (TypeError, ValueError):
        return False
    if t.tzinfo is not None:
        t = t.tz_convert("UTC")
    return int(t.hour) in FUNDING_HOURS_UTC and int(t.minute) == 0


def run_futures_backtest(
    df: pd.DataFrame,
    side_signals: pd.Series,
    *,
    bars_per_year: float,
    leverage: float = 1.0,
    fee_taker_pct: float = 0.05,
    slippage_pct: float = 0.03,
    funding_rates: pd.Series | None = None,
    mmr: float = 0.004,
    liq_fee_pct: float = 0.5,
    atr_stop_mult: float = 2.5,
    tp1_r: float | None = None,
    tp2_r: float | None = None,
    breakeven_after_tp1: bool = True,
    starting_equity: float = 50.0,
    min_notional: float = 5.0,
    qty_step: float = 0.0,
    risk_per_trade_pct: float = 1.0,
    max_position_pct: float = 30.0,
    atr_length: int = 14,
    atr_trailing: bool = False,
    max_leverage: float = 20.0,
) -> FuturesBacktestResult:
    n = len(df)
    if n < 3:
        return FuturesBacktestResult(metrics={"trades": 0, "bars": n})

    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    lo = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    times = df.index
    atr = ind.atr(df["high"], df["low"], df["close"], atr_length).to_numpy(float)
    sig = pd.Series(side_signals).reindex(df.index).fillna(0.0).to_numpy(float)
    sig = np.sign(sig).astype(int)
    fr = None
    if funding_rates is not None:
        fr = pd.Series(funding_rates).reindex(df.index).fillna(0.0).to_numpy(float)
    settle = np.array([_is_settlement(t) for t in times], dtype=bool) if fr is not None else np.zeros(n, dtype=bool)

    lev = float(min(max(leverage, 1.0), max(1.0, max_leverage)))
    fee = fee_taker_pct / 100.0
    slip = slippage_pct / 100.0
    risk = risk_per_trade_pct / 100.0
    max_pos = max_position_pct / 100.0
    liq_dist = max(0.0, 1.0 / lev - mmr)     # entry'ye göre oransal mesafe

    cash = float(starting_equity)
    equity = np.empty(n)
    trades: list[FuturesTrade] = []
    in_pos_bars = 0
    liq_count = 0
    funding_paid_total = 0.0
    fees_total = 0.0
    lev_used: list[float] = []

    # pozisyon durumu
    side = 0
    qty = 0.0
    qty0 = 0.0
    entry = 0.0
    entry_i = -1
    stop = 0.0
    stop_dist0 = 0.0
    tp1 = tp2 = None
    tp1_hit = False
    margin = 0.0
    liq_px = 0.0
    fees_acc = 0.0
    fund_acc = 0.0
    real_acc = 0.0        # kısmi kapanış gerçekleşmiş pnl
    mae = mfe = 0.0
    pending = 0           # -1/0/+1 hedef pozisyon (bir sonraki açılışta uygulanacak); None = değişiklik yok
    have_pending = False

    def _fill(px: float, direction: int) -> float:
        """direction +1 = alış (long aç / short kapa), -1 = satış. Kayma aleyhte."""
        return px * (1.0 + slip) if direction > 0 else px * (1.0 - slip)

    def _close(i: int, px_raw: float, reason: str, part: float = 1.0) -> None:
        nonlocal cash, side, qty, qty0, entry, entry_i, stop, real_acc, fees_acc, fund_acc, margin, liq_count, fees_total, tp1_hit
        q = qty if part >= 1.0 else qty * part
        if q <= 0:
            return
        exit_px = _fill(px_raw, -side)
        gross = side * (exit_px - entry) * q
        exit_fee = q * exit_px * fee
        if reason == "liq":
            liq_fee = q * entry * (liq_fee_pct / 100.0)
            # kayıp marj (bu parça için) + liq ücreti ile sınırlı
            part_margin = margin * (q / qty) if qty > 0 else margin
            gross = -part_margin
            exit_fee = liq_fee
            liq_count += 1
        fees_acc += exit_fee
        fees_total += exit_fee
        real_acc += gross - exit_fee
        cash += gross - exit_fee
        remaining = qty - q
        if remaining > 1e-15 and part < 1.0:
            # kısmi kapanış: marj oransal düşer
            margin = margin * (remaining / qty)
            qty = remaining
            return
        # tam kapanış → kayıt
        pnl = real_acc - fund_acc
        risk_amt = qty0 * stop_dist0
        trades.append(FuturesTrade(
            entry_time=str(times[entry_i]), exit_time=str(times[i]), side=side, entry=float(entry),
            exit=float(exit_px), qty=float(qty0), notional=float(qty0 * entry), leverage=lev, pnl=float(pnl),
            r=float(pnl / risk_amt) if risk_amt > 0 else 0.0, mae_pct=float(mae), mfe_pct=float(mfe), reason=reason,
            funding=float(fund_acc), fees=float(fees_acc), bars_held=int(i - entry_i), margin=float(qty0 * entry / lev),
            tp1_hit=tp1_hit,
        ))
        side, qty, qty0, entry, entry_i, stop, margin = 0, 0.0, 0.0, 0.0, -1, 0.0, 0.0
        real_acc = fees_acc = fund_acc = 0.0

    def _open(i: int, new_side: int) -> bool:
        nonlocal cash, side, qty, qty0, entry, entry_i, stop, stop_dist0, tp1, tp2, tp1_hit, margin, liq_px
        nonlocal fees_acc, fund_acc, real_acc, mae, mfe, fees_total
        a = atr[i - 1] if i > 0 else np.nan
        if not (np.isfinite(a) and a > 0):
            return False
        px = _fill(o[i], new_side)
        stop_dist = a * atr_stop_mult
        eq_now = cash
        if eq_now <= 0:
            return False
        risk_qty = (eq_now * risk) / stop_dist
        max_margin = eq_now * max_pos
        cap_qty = (max_margin * lev) / (px * (1.0 + fee))
        q = max(0.0, min(risk_qty, cap_qty))
        if min_notional > 0 and q * px < min_notional:
            q = min(cap_qty, min_notional * 1.02 / px) if cap_qty * px >= min_notional else 0.0
        if qty_step > 0 and q > 0:
            q = math.floor(q / qty_step + 1e-12) * qty_step
        if q <= 0 or (min_notional > 0 and q * px < min_notional * 0.999):
            return False
        notional = q * px
        m = notional / lev
        entry_fee = notional * fee
        if m + entry_fee > cash:
            return False
        cash -= entry_fee
        fees_total += entry_fee
        side, qty, qty0, entry, entry_i = new_side, q, q, px, i
        stop_dist0 = stop_dist
        stop = px - new_side * stop_dist
        tp1 = px + new_side * tp1_r * stop_dist if tp1_r else None
        tp2 = px + new_side * tp2_r * stop_dist if tp2_r else None
        tp1_hit = False
        margin = m
        liq_px = px * (1.0 - new_side * liq_dist)
        fees_acc, fund_acc, real_acc = entry_fee, 0.0, -entry_fee
        mae = mfe = 0.0
        lev_used.append(lev)
        return True

    for i in range(n):
        # 1) önceki barın kararını bu açılışta uygula
        if have_pending:
            if side != 0 and pending != side:
                _close(i, o[i], "reverse" if pending != 0 else "signal")
            if pending != 0 and side == 0:
                _open(i, pending)
            have_pending = False

        # 2) bar içi: liq > stop > tp (kötümser)
        if side != 0:
            if side > 0:
                mae = min(mae, (lo[i] - entry) / entry * 100.0)
                mfe = max(mfe, (h[i] - entry) / entry * 100.0)
                if lo[i] <= liq_px:
                    _close(i, min(o[i], liq_px), "liq")
                elif lo[i] <= stop:
                    _close(i, min(o[i], stop), "stop")
                elif tp1 is not None and not tp1_hit and h[i] >= tp1:
                    if tp2 is None:
                        _close(i, max(o[i], tp1), "tp1")
                    else:
                        _close(i, max(o[i], tp1), "tp1", part=0.5)
                        tp1_hit = True
                        if breakeven_after_tp1:
                            stop = max(stop, entry)
                        if side != 0 and h[i] >= tp2:
                            _close(i, max(o[i], tp2), "tp2")
                elif tp2 is not None and tp1_hit and h[i] >= tp2:
                    _close(i, max(o[i], tp2), "tp2")
            else:
                mae = min(mae, (entry - h[i]) / entry * 100.0)
                mfe = max(mfe, (entry - lo[i]) / entry * 100.0)
                if h[i] >= liq_px:
                    _close(i, max(o[i], liq_px), "liq")
                elif h[i] >= stop:
                    _close(i, max(o[i], stop), "stop")
                elif tp1 is not None and not tp1_hit and lo[i] <= tp1:
                    if tp2 is None:
                        _close(i, min(o[i], tp1), "tp1")
                    else:
                        _close(i, min(o[i], tp1), "tp1", part=0.5)
                        tp1_hit = True
                        if breakeven_after_tp1:
                            stop = min(stop, entry)
                        if side != 0 and lo[i] <= tp2:
                            _close(i, min(o[i], tp2), "tp2")
                elif tp2 is not None and tp1_hit and lo[i] <= tp2:
                    _close(i, min(o[i], tp2), "tp2")

        # 3) bar kapanışı: iz süren stop, funding, sinyal
        if side != 0:
            in_pos_bars += 1
            if atr_trailing and np.isfinite(atr[i]):
                if side > 0:
                    stop = max(stop, c[i] - atr[i] * atr_stop_mult)
                else:
                    stop = min(stop, c[i] + atr[i] * atr_stop_mult)
            if fr is not None and settle[i] and fr[i] != 0.0:
                pay = side * qty * c[i] * fr[i]        # long pozitif oranda öder
                fund_acc += pay
                funding_paid_total += pay
                cash -= pay
        s = int(sig[i])
        if s != side:
            pending, have_pending = s, True
        equity[i] = cash + (side * (c[i] - entry) * qty if side != 0 else 0.0)

    # açık pozisyonu son kapanıştan kapat (kayıt "end")
    if side != 0:
        _close(n - 1, c[-1], "end")
        equity[-1] = cash

    eq_series = pd.Series(equity, index=times)
    m = metrics_extended(equity, [t.r for t in trades], bars_per_year)
    m["trades"] = len(trades)
    m["bars"] = n
    m["funding_paid"] = float(funding_paid_total)
    m["fees_paid"] = float(fees_total)
    m["liq_count"] = int(liq_count)
    m["avg_leverage"] = float(np.mean(lev_used)) if lev_used else 0.0
    m["exposure_pct"] = 100.0 * in_pos_bars / n
    m["buy_hold_return_pct"] = float((c[-1] / c[0] - 1.0) * 100.0) if c[0] > 0 else 0.0
    m["net_pnl"] = float(sum(t.pnl for t in trades))
    m["long_trades"] = sum(1 for t in trades if t.side > 0)
    m["short_trades"] = sum(1 for t in trades if t.side < 0)
    m["final_equity"] = float(equity[-1])
    return FuturesBacktestResult(trades=trades, metrics=m, equity=eq_series)


__all__ = ["FuturesTrade", "FuturesBacktestResult", "run_futures_backtest"]
