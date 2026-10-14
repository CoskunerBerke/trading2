"""S1d — P1 `LUNCH_SWEEP` (öğle süpürmesi; 5m; SAF). Mühür `LIB_AG_V1` (docs/ENGINE_BRAIN_V1.md §8.6 S1d).

* Başvurular: NYAM_H/L = açılışı [09:30, 12:00) olan 5m barların uçları (30 barın hepsi olmalı; eksikse o gün yok) ve
  koşan LUNCH_H/L = açılışı 12:00 ile tetik barının açılışı arasındaki KAPANMIŞ öğle barlarının uçları (tetik barı hariç).
* Tetik penceresi: açılışı [12:00, 13:30) olan barlar (kapanışları 12:05–13:30). KISA: bar yükseği bir başvuru tepesinin
  ÜSTÜNDE ve kapanışı o tepenin ALTINDA → sonraki açılışta kısa; UZUN simetrik. Aynı barda iki yön → yok (belirsiz).
* Stop = süpürme barının ucu + tampon. Hedef = (NYAM ∪ öğle-şimdiye-kadar, tetik barı dahil) aralığının karşı ucu (fiyat);
  hedef < 1,0R ise yok (çekirdek, DOLUM fiyatıyla: `min_rr_after_cost` 1,0, maliyet payı 0; hedef girişin ötesinde olmalı).
  Zaman çıkışı 16:00 New York (karar 16:00 kapanışında, çıkış sonraki açılışta).
* `bias = htf`: yön, son KAPANMIŞ 4h barın kapanışı − 4h EMA50 (300 barlık pencere) işaretiyle aynı olmalı (0 ya da
  ölçülemez → yok). `accum = quiet`: bugünün NYAM aralığı ≤ önceki 20 New York gününün NYAM aralıklarının medyanı; aralıklar
  15m barlarından (her gün 10 bar; eksik gün → ölçülemez → yok; bugün de 15m'den — aynı tanım).
"""
from __future__ import annotations

from typing import Any, Mapping

from . import ag_clock as C
from . import indicators as ind
from .ag_common import LONG, SHORT, AgVariant, Bars, fnum, index_at_or_after, price_target, rows_of, stop_beyond

LIVE_MODULES: tuple[str, ...] = ()
PARAM_KEYS = ("common", "window", "bias", "accum", "ref_window_min", "lunch_start_min", "trigger_open_min", "time_exit_min",
              "min_tp_r", "htf", "quiet")


class LunchSweep(AgVariant):
    FAMILY = "AG_P1"
    PARAM_KEYS = PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        if p["bias"] not in ("none", "htf") or p["accum"] not in ("none", "quiet"):
            raise ValueError(f"{self.variant_id}: bias/accum")
        self.ref_a, self.ref_b = (int(x) for x in p["ref_window_min"])
        self.lunch0 = int(p["lunch_start_min"])
        self.trig_a, self.trig_b = (int(x) for x in p["trigger_open_min"])
        self.exit_min = int(p["time_exit_min"])
        self.min_r = fnum(p["min_tp_r"], "min_tp_r")
        h, q = dict(p["htf"]), dict(p["quiet"])
        if set(h) != {"tf", "ema"} or set(q) != {"tf", "days", "stat", "cmp"} or q["stat"] != "MEDIAN" or q["cmp"] != "LE":
            raise ValueError(f"{self.variant_id}: htf/quiet tanımı")
        self.htf_tf, self.htf_ema = str(h["tf"]), int(h["ema"])
        self.q_tf, self.q_days = str(q["tf"]), int(q["days"])
        if self.timeframes != ("5m", self.q_tf, self.htf_tf) or self.signal_tf != "5m":
            raise ValueError(f"{self.variant_id}: dilimler (5m, {self.q_tf}, {self.htf_tf}) olmalı: {self.timeframes}")
        if not (self.ref_a < self.ref_b == self.lunch0 == self.trig_a < self.trig_b < self.exit_min):
            raise ValueError(f"{self.variant_id}: saat sırası")

    # ------------------------------------------------------------------ filtreler
    def _htf_sign(self, bars) -> int:
        rows = rows_of(bars, self.htf_tf)
        if len(rows) < self.htf_ema:
            return 0
        cl = [float(r["close"]) for r in rows]
        e = ind.ema(cl, self.htf_ema)[-1]
        if e != e:
            return 0
        d = cl[-1] - e
        return 1 if d > 0 else (-1 if d < 0 else 0)

    def _nyam_range(self, ts: list[int], rows, day: int, step: int) -> float | None:
        s, e = C.ny_to_utc(day, self.ref_a), C.ny_to_utc(day, self.ref_b)
        if s is None or e is None:
            return None
        a, b = index_at_or_after(ts, s), index_at_or_after(ts, e)
        if b - a != (e - s) // step:
            return None
        return max(float(r["high"]) for r in rows[a:b]) - min(float(r["low"]) for r in rows[a:b])

    def _quiet(self, bars, day: int) -> bool:
        rows = rows_of(bars, self.q_tf)
        ts = [int(r["timestamp"]) for r in rows]
        step = int(C.MIN_MS * {"5m": 5, "15m": 15, "1h": 60}[self.q_tf])
        today = self._nyam_range(ts, rows, day, step)
        prev = [self._nyam_range(ts, rows, day - k, step) for k in range(1, self.q_days + 1)]
        if today is None or any(x is None for x in prev):
            return False
        xs = sorted(prev)
        n = len(xs)
        med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
        return today <= med

    # ------------------------------------------------------------------ karar
    def decide(self, bars, ctx):
        rows = rows_of(bars, "5m")
        now = int(ctx["now_ms"])
        t_k = int(rows[-1]["timestamp"])
        if t_k + self.step != now:
            return None
        mk = C.ny_minute(t_k)
        if not self.trig_a <= mk < self.trig_b:
            return None
        day = C.ny_day(t_k)
        s, e = C.ny_to_utc(day, self.ref_a), C.ny_to_utc(day, self.ref_b)
        if s is None or e is None:
            return None
        b = Bars(rows)
        a, z = index_at_or_after(b.ts, s), index_at_or_after(b.ts, e)
        k = b.n - 1
        if z - a != (e - s) // self.step or z > k:
            return None
        hi_refs = [("NYAM_H", max(b.h[a:z]))]
        lo_refs = [("NYAM_L", min(b.l[a:z]))]
        if k > z:
            hi_refs.append(("LUNCH_H", max(b.h[z:k])))
            lo_refs.append(("LUNCH_L", min(b.l[z:k])))
        sh = [name for name, r in hi_refs if b.h[k] > r and b.c[k] < r]
        lg = [name for name, r in lo_refs if b.l[k] < r and b.c[k] > r]
        if bool(sh) == bool(lg):
            return None
        side = SHORT if sh else LONG
        if self.params["bias"] == "htf":
            sg = self._htf_sign(bars)
            if (side == LONG and sg <= 0) or (side == SHORT and sg >= 0):
                return None
        if self.params["accum"] == "quiet" and not self._quiet(bars, day):
            return None
        tp = min(b.l[a:k + 1]) if side == SHORT else max(b.h[a:k + 1])
        stop = stop_beyond(side, b.h[k] if side == SHORT else b.l[k], self.buffer)
        return self.intent(signal_ts=t_k, side=side, ref=self.ref(b.c[k], min_rr=self.min_r, target_beyond=True), stop=stop,
                           target=price_target(tp), exit_rule="RANGE_OPPOSITE_OR_TIME_EXIT_NY", evidence={"swept": sh or lg, "ny_day": C.iso_day(day)})

    def rule_exit(self, pos, bars, ctx):
        t = C.ny_to_utc(C.ny_day(int(pos.opened_ms)), self.exit_min)
        if t is not None and int(ctx["now_ms"]) >= t:
            return "TIME_EXIT_NY"
        return None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> LunchSweep:
    return LunchSweep(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "LunchSweep", "PARAM_KEYS", "make"]
