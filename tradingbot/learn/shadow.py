"""Gölge / karşı-olgusal işlemler — açılmayan güçlü adaylar ileriye dönük izlenir. Gerçek fill kadar güvenilir DEĞİLDİR
(`is_counterfactual=True`). Etiketleme yalnızca `label_ts` geçtikten sonra ve o ana kadarki kapalı mumlarla yapılır."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from ..core import atomic_write_json, from_iso, iso, new_id, read_json, utc_now

VARIANTS = ("as_planned", "no_tp1", "lev_half", "wider_stop_1_5x", "hold_h")


@dataclass
class ShadowTrade:
    id: str
    plan_id: str
    symbol: str
    market_type: str
    direction: str
    created_at: str
    entry: float
    stop: float
    targets: list[float]
    horizon_bars: int
    variant: str
    reason_not_opened: list[str]
    label_ts: str
    tf_minutes: int = 240
    leverage: float = 1.0
    outcome: dict[str, Any] | None = None
    labeled_at: str | None = None
    is_counterfactual: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def label_with_candles(sh: ShadowTrade, df: pd.DataFrame, *, tp1_fraction: float = 0.5) -> dict[str, Any] | None:
    """Kapalı mumlarla (ts ≤ label_ts) yolu yürüt: her barda ÖNCE stop (low/high) sonra hedef (muhafazakâr).
    df: index/`timestamp` ms ile OHLC. Dönen None = henüz yeterli mum yok."""
    created = from_iso(sh.created_at)
    label_ts = from_iso(sh.label_ts)
    if "timestamp" in df:
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    else:
        ts = pd.to_datetime(df.index, utc=True)
    m = (ts > created) & (ts <= label_ts)
    path = df.loc[m.values]
    if path.empty:
        return None
    long = sh.direction == "LONG"
    stop = sh.stop
    if sh.variant == "wider_stop_1_5x":
        stop = sh.entry - (sh.entry - sh.stop) * 1.5 if long else sh.entry + (sh.stop - sh.entry) * 1.5
    targets = list(sh.targets) if sh.variant != "hold_h" else []
    tp1_done = False
    realized_r = 0.0
    risk = abs(sh.entry - stop)
    if risk <= 0:
        return None
    frac_open = 1.0
    mae = mfe = 0.0
    exit_reason, exit_px, bars = "horizon", float(path["close"].iloc[-1]), 0
    for _, row in path.iterrows():
        bars += 1
        hi, lo = float(row["high"]), float(row["low"])
        move_hi = (hi / sh.entry - 1) * 100 * (1 if long else -1)
        move_lo = (lo / sh.entry - 1) * 100 * (1 if long else -1)
        mfe = max(mfe, move_hi if long else move_lo)
        mae = min(mae, move_lo if long else move_hi)
        hit_stop = lo <= stop if long else hi >= stop
        if hit_stop:
            realized_r += frac_open * ((stop - sh.entry) if long else (sh.entry - stop)) / risk
            exit_reason, exit_px = ("stop" if not tp1_done else "breakeven_stop"), stop
            frac_open = 0.0
            break
        if targets and not tp1_done and sh.variant != "no_tp1" and len(targets) >= 2:
            t1 = targets[0]
            if (hi >= t1 if long else lo <= t1):
                realized_r += tp1_fraction * ((t1 - sh.entry) if long else (sh.entry - t1)) / risk
                frac_open -= tp1_fraction
                tp1_done = True
                stop = sh.entry
        if targets:
            t_last = targets[-1] if (tp1_done or sh.variant == "no_tp1" or len(targets) == 1) else None
            if t_last is not None and (hi >= t_last if long else lo <= t_last):
                realized_r += frac_open * ((t_last - sh.entry) if long else (sh.entry - t_last)) / risk
                exit_reason, exit_px = "target", t_last
                frac_open = 0.0
                break
    if frac_open > 0:
        realized_r += frac_open * ((exit_px - sh.entry) if long else (sh.entry - exit_px)) / risk
    if sh.variant == "lev_half":
        realized_r *= 0.5
    return {"r_multiple": round(realized_r, 4), "exit_reason": exit_reason, "exit_price": exit_px, "bars": bars, "mae_pct": round(mae, 3),
            "mfe_pct": round(mfe, 3), "won": realized_r > 0.25, "veto_was_right": realized_r <= 0, "is_counterfactual": True}


class ShadowBook:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        d = read_json(self.path, default={"trades": []})
        self.trades: list[ShadowTrade] = [ShadowTrade(**{k: v for k, v in t.items() if k in ShadowTrade.__dataclass_fields__}) for t in d.get("trades", [])]

    MAX_TRADES = 5000                       # SINIRLI saklama — dosya sinirsiz buyumez

    def save(self) -> None:
        # Bellek ve dosya AYNI siniri uygular; aksi halde surec omru boyunca liste sinirsiz buyur.
        if len(self.trades) > self.MAX_TRADES:
            self.trades = self.trades[-self.MAX_TRADES:]
        atomic_write_json(self.path, {"trades": [t.to_dict() for t in self.trades]})

    def _event_key(self, plan_id: str, symbol: str, direction: str, variant: str) -> tuple:
        return (str(plan_id), str(symbol), str(direction), str(variant))

    def _open_event_keys(self) -> set[tuple]:
        """ETIKETLENMEMIS (sonucu henuz olcumemis) golge kayitlarin kimlikleri."""
        return {self._event_key(t.plan_id, t.symbol, t.direction, t.variant)
                for t in self.trades if t.outcome is None}

    def add(self, plan: dict[str, Any], reason_not_opened: list[str], *, variants: tuple[str, ...] = ("as_planned",), tf_minutes: int = 240,
            now: datetime | None = None) -> list[ShadowTrade]:
        now = now or utc_now()
        h = int(plan.get("horizon_bars", plan.get("time_horizon", 12)) or 12)
        out = []
        # DUPLICATE OLAY KORUMASI: ayni plan/sembol/yon/varyant icin acik (etiketlenmemis) bir golge
        # kayit varsa IKINCISI YAZILMAZ. Ayni tur icinde iki kapiya birden takilan aday, istatistigi
        # iki kez besleyemez. Etiketlenmis eski kayit yeni bir olayi ENGELLEMEZ.
        seen = self._open_event_keys()
        plan_id = str(plan.get("plan_id", plan.get("id", "")))
        symbol, direction = plan["symbol"], plan.get("direction", "LONG")
        for v in variants:
            if self._event_key(plan_id, symbol, direction, v) in seen:
                continue
            st = ShadowTrade(id=new_id("shadow"), plan_id=str(plan.get("plan_id", plan.get("id", ""))), symbol=plan["symbol"],
                             market_type=str(plan.get("market_type", "futures")), direction=plan.get("direction", "LONG"), created_at=iso(now),
                             entry=float(plan["entry"]), stop=float(plan["stop"]), targets=[float(x) for x in plan.get("targets", [])], horizon_bars=h,
                             variant=v, reason_not_opened=list(reason_not_opened), label_ts=iso(now + timedelta(minutes=tf_minutes * h)),
                             tf_minutes=tf_minutes, leverage=float(plan.get("leverage", 1) or 1))
            self.trades.append(st)
            seen.add(self._event_key(plan_id, symbol, direction, v))
            out.append(st)
        if out:
            self.save()
        return out

    def pending(self, now: datetime | None = None) -> list[ShadowTrade]:
        now = now or utc_now()
        return [t for t in self.trades if t.outcome is None and from_iso(t.label_ts) <= now]

    def label(self, sh: ShadowTrade, df: pd.DataFrame) -> dict | None:
        out = label_with_candles(sh, df)
        if out is not None:
            sh.outcome, sh.labeled_at = out, iso()
            self.save()
        return out

    def stats(self) -> dict:
        done = [t for t in self.trades if t.outcome]
        vr = [t for t in done if t.outcome.get("veto_was_right")]
        return {"total": len(self.trades), "labeled": len(done), "veto_right_rate": round(len(vr) / len(done), 3) if done else None,
                "avg_r": round(sum(t.outcome["r_multiple"] for t in done) / len(done), 3) if done else None, "is_counterfactual": True}
