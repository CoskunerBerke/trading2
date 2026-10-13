"""S1d — P4 `RB_LIMIT` (ret bloğu; 5m; yalnız öldürme bölgesi; SAF). Mühür `LIB_AG_V1`.

* RB = doğrulanmış (j + 1 kapanmış) bir salınım barı j; fitili son 48 bar içindeki ([j − 48, j − 2]) doğrulanmış AYNI yön
  bir önceki salınımın ötesine geçmiş ve kapanışı geri içeride (dip RB: düşük_j < düşük_p ve kapanış_j > düşük_p; tepe
  simetrik). Hem dip hem tepe olan bar (dış bar) iki yönde RB verirse belirsiz → yok. Yalnız EN SON RB geçerlidir.
* Giriş çizgisi = fitile en yakın gövde kenarı (dip: min(açılış, kapanış); tepe: max). Stop = fitil ucu + tampon.
* Çizgiye ilk dönüş (dip: düşük ≤ çizgi) doğrulamadan SONRAKİ barlarda sayılır (j + 2'den; j + 1 doğrulama barıdır, çizgi
  henüz bilinmez) ve 24 bar geçerlidir (j + 2 … j + 25).
* `entry = direct`: çizgide tek-bar limit (çekirdek; ilk dokunuş kurulumu tüketir). `entry = confirm`: ilk dokunuştan
  SONRAKİ 5m bar çizginin ötesinde (dip: kapanış > çizgi) kapanırsa sonraki açılışta piyasa girişi.
* Hedef = karar anında doğrulanmış, o zamandan beri hiç aşılmamış (süpürülmemiş) en yakın karşı 5m salınımı (dip RB'de
  referans fiyatın üstündeki en düşük tepe) referanstan ≥ 2R uzaktaysa o fiyat, değilse 2R (referans = direct'te çizgi,
  confirm'de karar barının kapanışı). Giriş yalnız öldürme bölgesinde (emir/giriş anı).
"""
from __future__ import annotations

from typing import Any, Mapping

from .ag_common import (LONG, MISS, SHORT, AgVariant, Bars, fnum, price_target, r_target, stop_beyond, swing_high,
                        swing_low)

LIVE_MODULES: tuple[str, ...] = ()
PARAM_KEYS = ("common", "window", "entry", "session", "prior_swing_bars", "valid_bars", "min_tp_r")


class RbLimit(AgVariant):
    FAMILY = "AG_P4"
    PARAM_KEYS = PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        if p["entry"] not in ("direct", "confirm") or p["session"] != "KZ" or self.timeframes != ("5m",):
            raise ValueError(f"{self.variant_id}: entry/session/tf")
        self.prior = int(p["prior_swing_bars"])
        self.valid = int(p["valid_bars"])
        self.min_r = fnum(p["min_tp_r"], "min_tp_r")
        if self.lookback["5m"] < self.prior + self.valid + 8:
            raise ValueError(f"{self.variant_id}: pencere kısa")

    def rb_at(self, b: Bars, j: int) -> tuple[str, float, float] | None:
        """j'deki RB: (yön, çizgi, stop) ya da None (j − prior … j + 1 barlarına bağlı; önbellek)."""
        lo = j - self.prior - 1
        if lo < 0 or j + 1 >= b.n:
            return None
        key = ("RB", b.ts[j + 1])
        got = self.memo.get(key, b.rows[j + 1], b.rows[lo])
        if got is not MISS:
            return got
        found = []
        if swing_low(b.l, j) and any(swing_low(b.l, p) and b.l[j] < b.l[p] and b.c[j] > b.l[p]
                                     for p in range(j - 2, j - self.prior - 1, -1)):
            found.append((LONG, min(b.o[j], b.c[j]), stop_beyond(LONG, b.l[j], self.buffer)))
        if swing_high(b.h, j) and any(swing_high(b.h, p) and b.h[j] > b.h[p] and b.c[j] < b.h[p]
                                      for p in range(j - 2, j - self.prior - 1, -1)):
            found.append((SHORT, max(b.o[j], b.c[j]), stop_beyond(SHORT, b.h[j], self.buffer)))
        res = found[0] if len(found) == 1 else None
        return self.memo.put(key, b.rows[j + 1], res, b.ts[0], b.rows[lo])

    def target(self, b: Bars, m: int, side: str, ref: float, stop: float):
        risk = abs(ref - stop)
        best = None
        after_hi, after_lo = b.h[m], b.l[m]                               # j'den sonraki barların (j + 1 … m) uçları
        for j in range(m - 1, 0, -1):                                      # j + 1 ≤ m: doğrulanmış
            if side == LONG and swing_high(b.h, j) and b.h[j] > ref and after_hi < b.h[j]:
                best = b.h[j] if best is None else min(best, b.h[j])
            if side == SHORT and swing_low(b.l, j) and b.l[j] < ref and after_lo > b.l[j]:
                best = b.l[j] if best is None else max(best, b.l[j])
            after_hi, after_lo = max(after_hi, b.h[j]), min(after_lo, b.l[j])
        if best is not None and abs(best - ref) >= self.min_r * risk:
            return price_target(best), best
        return r_target(self.min_r), None

    def decide(self, bars, ctx):
        rows = bars.get("5m") or ()
        now = int(ctx["now_ms"])
        if int(rows[-1]["timestamp"]) + self.step != now or not self.in_kz(now):
            return None
        b = Bars(rows)
        m = b.n - 1
        direct = self.params["entry"] == "direct"
        lo_j = m - self.valid - (0 if direct else 2)
        for j in range(m - 1, max(lo_j, 1) - 1, -1):
            rb = self.rb_at(b, j)
            if rb is None:
                continue
            side, line, stop = rb
            touch = next((t for t in range(j + 2, min(m, j + 1 + self.valid) + 1)
                          if ((b.l[t] <= line) if side == LONG else (b.h[t] >= line))), None)
            if direct:
                if touch is not None or m + 1 > j + 1 + self.valid:
                    return None
                ref_px = line
            else:
                if touch is None or touch != m - 1 or not ((b.c[m] > line) if side == LONG else (b.c[m] < line)):
                    return None
                ref_px = b.c[m]
            tgt, swing_px = self.target(b, m, side, ref_px, stop)
            ref = self.ref(b.c[m], limit=line if direct else None, target_beyond=swing_px is not None)
            return self.intent(signal_ts=b.ts[j], side=side, ref=ref, stop=stop, target=tgt,
                               exit_rule="RB_" + self.params["entry"].upper(),
                               evidence={"rb_ts": b.ts[j], "line": line, "tp_swing": swing_px})
        return None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> RbLimit:
    return RbLimit(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "PARAM_KEYS", "RbLimit", "make"]
