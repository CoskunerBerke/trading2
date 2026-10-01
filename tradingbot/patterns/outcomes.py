"""Triple-barrier / R tabanlı sonuç: stop, TP1 (kısmi), TP2, zaman çıkışı, maksimum ufuk; aynı barda stop+TP → worst-case (önce stop).
Maliyet sonrası: giriş/çıkış komisyonu, slippage, funding (bar başına, ödenen/alınan yönle) → net R. Spot short YASAK."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np

EXIT_STOP, EXIT_TP1, EXIT_TP2, EXIT_TIME, EXIT_BE = "STOP", "TP1", "TP2", "TIME", "BE_STOP"


@dataclass
class Outcome:
    side: str
    market: str
    entry_idx: int
    exit_idx: int
    entry: float
    exit_price: float
    stop: float
    tp1: float
    tp2: float
    exit_reason: str
    bars_held: int
    gross_pnl_pct: float
    fees_pct: float
    slippage_pct: float
    funding_pct: float
    net_pnl_pct: float
    gross_r: float
    net_r: float
    mae_pct: float
    mfe_pct: float
    tp1_hit: bool

    def to_dict(self) -> dict:
        return asdict(self)


def barriers_from_atr(entry: float, atr_val: float, side: str, *, stop_mult: float = 2.5, tp1_r: float = 1.0, tp2_r: float = 2.0) -> tuple[float, float, float]:
    r = stop_mult * atr_val
    if side == "LONG":
        return entry - r, entry + tp1_r * r, entry + tp2_r * r
    return entry + r, entry - tp1_r * r, entry - tp2_r * r


def triple_barrier(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, signal_idx: int, side: str, *, stop: float, tp1: float, tp2: float,
                   horizon: int, market: str = "futures", fee_pct: float = 0.05, slippage_pct: float = 0.03, funding_pct_per_bar: float = 0.0,
                   tp1_fraction: float = 0.5, breakeven_after_tp1: bool = True) -> Outcome | None:
    """Sinyal bar i kapanışı → giriş i+1 AÇILIŞ. Her barda öncelik: stop(worst-case) > tp1 > tp2; ufuk dolunca kapanışta çık.
    R birimi = |entry - stop|. Kısmi TP1 sonrası kalan için stop başabaşa çekilir (breakeven_after_tp1). Spot short → ValueError."""
    side = side.upper()
    if market == "spot" and side == "SHORT":
        raise ValueError("spot short yasak")
    ei = signal_idx + 1
    if ei >= len(o) or horizon < 1:
        return None
    entry = float(o[ei])
    r_dist = abs(entry - stop)
    if r_dist <= 0:
        return None
    sgn = 1.0 if side == "LONG" else -1.0
    rem = 1.0
    realized = 0.0                       # gerçekleşen brüt %, notional ağırlıklı
    cur_stop = stop
    tp1_hit = False
    mae = mfe = 0.0
    last = ei
    reason = EXIT_TIME
    exit_px = entry
    n_bars = 0
    for i in range(ei, min(len(o), ei + horizon)):
        last = i
        n_bars += 1
        hi, lo, cl = float(h[i]), float(l[i]), float(c[i])
        # MAE/MFE (fiyat yolu, %)
        fav = (hi - entry) / entry * 100 * sgn if side == "LONG" else (entry - lo) / entry * 100
        adv = (lo - entry) / entry * 100 if side == "LONG" else (entry - hi) / entry * 100
        mfe = max(mfe, fav)
        mae = min(mae, adv)
        stop_hit = lo <= cur_stop if side == "LONG" else hi >= cur_stop
        tp1_touch = (hi >= tp1) if side == "LONG" else (lo <= tp1)
        tp2_touch = (hi >= tp2) if side == "LONG" else (lo <= tp2)
        if stop_hit:                                  # worst-case: aynı barda TP de görülse önce stop
            px = min(float(o[i]), cur_stop) if side == "LONG" else max(float(o[i]), cur_stop)
            realized += rem * (px - entry) / entry * 100 * sgn
            rem = 0.0
            reason = EXIT_BE if tp1_hit and abs(cur_stop - entry) < 1e-12 else EXIT_STOP
            exit_px = px
            break
        if not tp1_hit and tp1_touch:
            px = max(float(o[i]), tp1) if side == "LONG" else min(float(o[i]), tp1)
            realized += tp1_fraction * (px - entry) / entry * 100 * sgn
            rem -= tp1_fraction
            tp1_hit = True
            if breakeven_after_tp1:
                cur_stop = entry
            if rem <= 1e-12:
                reason, exit_px = EXIT_TP1, px
                break
        if tp2_touch and rem > 0:
            px = max(float(o[i]), tp2) if side == "LONG" else min(float(o[i]), tp2)
            realized += rem * (px - entry) / entry * 100 * sgn
            rem = 0.0
            reason, exit_px = EXIT_TP2, px
            break
        if i == min(len(o), ei + horizon) - 1 and rem > 0:
            realized += rem * (cl - entry) / entry * 100 * sgn
            rem = 0.0
            reason, exit_px = EXIT_TIME, cl
    if rem > 0:                                       # veri bitti
        realized += rem * (float(c[last]) - entry) / entry * 100 * sgn
        reason, exit_px = EXIT_TIME, float(c[last])
    fees = 2 * fee_pct
    slip = 2 * slippage_pct
    fund = funding_pct_per_bar * n_bars * (1.0 if side == "LONG" else -1.0)      # long öder (pozitif funding), short alır
    net = realized - fees - slip - fund
    r_pct = r_dist / entry * 100
    return Outcome(side=side, market=market, entry_idx=ei, exit_idx=last, entry=entry, exit_price=exit_px, stop=stop, tp1=tp1, tp2=tp2,
                   exit_reason=reason, bars_held=n_bars, gross_pnl_pct=realized, fees_pct=fees, slippage_pct=slip, funding_pct=fund,
                   net_pnl_pct=net, gross_r=realized / r_pct, net_r=net / r_pct, mae_pct=mae, mfe_pct=mfe, tp1_hit=tp1_hit)


__all__ = ["Outcome", "triple_barrier", "barriers_from_atr", "EXIT_STOP", "EXIT_TP1", "EXIT_TP2", "EXIT_TIME", "EXIT_BE"]
