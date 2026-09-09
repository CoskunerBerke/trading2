"""Onceden kayit altina alinan rakiplerin (C1/C2/C3) tek ve ORTAK kosum yolu.

`docs/CHALLENGERS_V1.md` bu dosyadan ONCE islendi. Buradaki hicbir sabit sonuca bakilarak
secilmedi; hepsi ya uretim yapilandirmasindan ya da yalniz YORDAYICI dagilimindan gelir.

Temel kural: temel (baseline) ile rakip AYNI fonksiyondan gecer. Tek fark `Policy` nesnesidir.
Boylece "temel farkli bir kod yolundan gectigi icin farkli cikti" hatasi imkansizdir.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Sequence

from ..accounting.models import AmountType, SizeSpec, TickData
from ..core import D, ZERO
from .fidelity import Bar, ReplayConfig, TradePlan, exit_family

BAR_ADVANCE_MS = 14_400_000
MIN_MS = 60_000


@dataclass(frozen=True)
class Policy:
    """Tek bir cikis/hedef politikasi. `name` rapora aynen gecer."""
    name: str
    #: C1 — giristen bu kadar saat sonra, o ana kadarki MFE `time_box_min_mfe_r` altindaysa piyasadan kapat.
    time_box_h: float | None = None
    time_box_min_mfe_r: float = 1.0
    #: C2 — birinci hedefi giristen bu kadar R uzaga tasi (ikinci hedef DOKUNULMAZ).
    tp1_at_r: float | None = None

    def targets_for(self, entry: Decimal, stop: Decimal, side: str,
                    recorded: Sequence[Decimal]) -> tuple[list[Decimal], str]:
        """Politikaya gore hedef listesi + dislama nedeni ('' ise dislama yok)."""
        if self.tp1_at_r is None or not recorded:
            return list(recorded), ""
        risk = abs(entry - stop)
        sign = Decimal("1") if str(side).upper() in ("LONG", "BUY") else Decimal("-1")
        t1 = entry + sign * D(str(self.tp1_at_r)) * risk
        if len(recorded) < 2:
            return list(recorded), "NO_T2"
        t2 = recorded[-1]
        beyond = (t2 > t1) if sign > 0 else (t2 < t1)
        if not beyond:
            # onceden kayit altina alinan dislama: T2, 1R seviyesinin otesinde degilse aday HER IKI
            # koldan da dusurulur (yoksa rakip kendine kolay bir es yaratirdi).
            return list(recorded), "T2_NOT_BEYOND_TP1"
        return [t1, t2], ""


BASELINE = Policy(name="baseline")
C1 = Policy(name="C1_time_box_24h_mfe1R", time_box_h=24.0, time_box_min_mfe_r=1.0)
C2 = Policy(name="C2_tp1_at_1R", tp1_at_r=1.0)
#: C3 bir CIKIS politikasi degil, bir SECIM filtresidir; ayni temel politikayla kosar.
C3_TAU = 0.5931
C3_FIELD = "conservative_net_edge_r"


@dataclass
class RunOutcome:
    closed: bool = False
    exit_reason: str | None = None
    r_multiple: float | None = None
    net_pnl: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    minutes_held: int = 0
    n_bars: int = 0
    ambiguous_bars: int = 0
    time_boxed: bool = False
    excluded: str = ""
    note: str = ""


def run_plan(*, symbol: str, side: str, ref_entry: Decimal, stop: Decimal, targets: Sequence[Decimal],
             notional: Decimal, leverage: int, amount_type: str, opened_at: datetime, bars: Sequence[Bar],
             cfg: ReplayConfig, policy: Policy, funding_lookup: Any = None,
             be_mfe_r: Decimal | None = None, be_mfe_active_from: datetime | None = None,
             horizon_close: bool = True) -> RunOutcome:
    """Bir plani verilen mumlarla ve verilen politikayla kos.

    ILERI BAKIS YOK: `bars` cagiran tarafindan `opened_at`tan SONRAKI ilk mumla baslatilir.
    BAR ICI SIRALAMA: uretim defterinin kendi sirasi (likidasyon -> stop -> hedef) — degistirilmez.
    ZAMAN KUTUSU (C1): MFE yalnizca o ana kadarki mumlardan okunur; kapanis o barin KAPANISINDAN
    piyasa emriyle yapilir.
    """
    out = RunOutcome(n_bars=len(bars))
    if not bars:
        out.note = "NO_BARS"
        return out
    tgts, excl = policy.targets_for(ref_entry, stop, side, [D(str(t)) for t in targets])
    if excl:
        out.excluded = excl
        return out
    led = cfg.new_ledger(breakeven_at_mfe_r=(ZERO if be_mfe_active_from is not None else be_mfe_r))
    size = SizeSpec(amount=notional, amount_type=AmountType(amount_type), leverage=leverage)
    pos = led.open(symbol, side, ref_entry, size, stop=stop, targets=tgts, now=opened_at)
    if pos is None:
        out.note = f"OPEN_REJECTED:{led.last_reject_reason}"
        return out
    risk_pct = abs(pos.entry_avg - stop) / pos.entry_avg * 100 if pos.entry_avg > 0 else ZERO
    box_at = None if policy.time_box_h is None else opened_at + timedelta(hours=policy.time_box_h)
    min_mfe = D(str(policy.time_box_min_mfe_r))
    start_ms = bars[0].open_ms
    prev_slot = int(opened_at.timestamp() * 1000) // BAR_ADVANCE_MS
    rec = None
    for b in bars:
        slot = b.open_ms // BAR_ADVANCE_MS
        advance = slot != prev_slot
        prev_slot = slot
        if be_mfe_active_from is not None:
            led.breakeven_at_mfe_r = (be_mfe_r if be_mfe_r is not None else cfg.breakeven_at_mfe_r) \
                if b.close_dt >= be_mfe_active_from else ZERO
        stop_now = pos.stop
        tgt_now = pos.targets[pos.targets_hit] if pos.targets_hit < len(pos.targets) else None
        if stop_now is not None and tgt_now is not None and b.low <= stop_now <= b.high and b.low <= tgt_now <= b.high:
            out.ambiguous_bars += 1
        td = TickData(last=b.close, mark=b.close, high=b.high, low=b.low, ts=b.close_dt.isoformat(),
                      bar_open=b.open_dt.isoformat())
        recs = led.tick({symbol: td}, now_utc=b.close_dt, funding_rate_lookup=funding_lookup, bar_advance=advance)
        if recs:
            rec = recs[-1]
            out.minutes_held = (b.open_ms - start_ms) // MIN_MS + 1
            break
        # --- C1: zaman kutusu. Defter tick'inden SONRA bakilir; ayni barda stop/hedef tetiklendiyse
        #     onlar kazanir (kutu asla bir kapanisi geri almaz).
        if box_at is not None and b.close_dt >= box_at and risk_pct > 0:
            if D(pos.mfe_pct) / risk_pct < min_mfe:
                rec = led.close_manual(symbol, b.close, reason="TIME_BOX", now=b.close_dt)
                out.time_boxed = True
                out.minutes_held = (b.open_ms - start_ms) // MIN_MS + 1
                break
            box_at = None                       # esik asildi: kutu bir daha calismaz
    if rec is None:
        if not horizon_close:
            out.note = "STILL_OPEN"
            return out
        last = bars[-1]
        rec = led.close_manual(symbol, last.close, reason="HORIZON", now=last.close_dt)
        out.minutes_held = (last.open_ms - start_ms) // MIN_MS + 1
        out.note = "HORIZON_CLOSE"
        if rec is None:
            out.note = "NO_CLOSE"
            return out
    out.closed = True
    out.exit_reason = ("TIME_BOX" if out.time_boxed else
                       "HORIZON" if out.note == "HORIZON_CLOSE" else exit_family(rec.exit_reason))
    out.r_multiple, out.net_pnl = float(rec.r_multiple), float(rec.net_pnl)
    if risk_pct > 0:
        out.mfe_r, out.mae_r = float(D(rec.mfe_pct) / risk_pct), float(D(rec.mae_pct) / risk_pct)
    return out


def plan_bars_window(opened_at: datetime, horizon_h: float) -> tuple[int, int]:
    """(ilk mum acilisi, son mum acilisi) — giris mumunun giris ONCESI kismi disarida kalir."""
    open_ms = int(opened_at.timestamp() * 1000)
    return ((open_ms + MIN_MS - 1) // MIN_MS) * MIN_MS, open_ms + int(horizon_h * 3_600_000)


def run_trade_plan(plan: TradePlan, bars: Sequence[Bar], cfg: ReplayConfig, policy: Policy, **kw) -> RunOutcome:
    """`fidelity.TradePlan` (gercek kapanmis islem) icin ayni kosum yolu — A6 kontrolu bunu kullanir."""
    return run_plan(symbol=plan.symbol, side=plan.side, ref_entry=plan.ref_entry, stop=plan.initial_stop,
                    targets=plan.targets, notional=plan.requested_notional, leverage=plan.leverage,
                    amount_type=plan.amount_type, opened_at=plan.opened_at, bars=bars, cfg=cfg,
                    policy=policy, **kw)


__all__ = ["BASELINE", "C1", "C2", "C3_FIELD", "C3_TAU", "Policy", "RunOutcome",
           "plan_bars_window", "run_plan", "run_trade_plan"]
