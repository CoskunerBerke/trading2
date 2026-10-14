"""S1d — P3 `MSS_FVG` (piyasa yapısı kırılımı + adil değer boşluğu; 5m ya da 15m; SAF). Mühür `LIB_AG_V1`.

* Yer değiştirme barı i: gövde ≥ 1,5 × ATR14 (önceki barın) ve kapanış, i − 1'de doğrulanmış en son karşı salınımın ötesinde
  (yükseliş: kapanış > son salınım tepesi) ve o salınımın doğrulanmasından beri bu ötesine İLK kapanış = MSS. Salınım
  araması i'den önceki 100 barla sınırlıdır (mühürlü okuma: kurulum yalnız i çevresindeki barlara bağlı → pencere kaymasından
  bağımsız ve önbelleklenebilir).
* FVG'si i − 1 / i + 1 barlarından (yükseliş: bar[i−1].high < bar[i+1].low). Limit FVG'nin fiyata yakın kenarında
  (yükseliş: FVG üstü = bar[i+1].low; düşüş: FVG altı = bar[i+1].high); emir i + 2 … i + 13 barlarında (12 bar) geçerli;
  ilk dokunuş kurulumu tüketir (çekirdeğin tek-bar limit kuralı). Yalnız EN SON kurulum geçerlidir (yenisi eskisinin yerini
  alır).
* Stop = yer değiştirmeden önceki salınım ucu (yükseliş: i'de doğrulanmış en son salınım dibi) + tampon; hedef 2R ya da 3R
  (gerçek girişten). `session = KZ`: emir yalnız öldürme bölgesinde açılan barda yaşar (dokunuş bölge dışında olsa da
  kurulum tükenir); `ALL`: her saat.
"""
from __future__ import annotations

from typing import Any, Mapping

from .ag_common import (LONG, MISS, SHORT, AgVariant, Bars, bearish_fvg, bullish_fvg, fnum, last_swing, r_target,
                        stop_beyond)

LIVE_MODULES: tuple[str, ...] = ()
PARAM_KEYS = ("common", "window", "tf", "target_r", "session", "disp_atr", "valid_bars", "swing_lookback")


class MssFvg(AgVariant):
    FAMILY = "AG_P3"
    PARAM_KEYS = PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        if p["session"] not in ("KZ", "ALL") or p["tf"] not in ("5m", "15m") or self.timeframes != (p["tf"],):
            raise ValueError(f"{self.variant_id}: tf/session")
        self.target_r = fnum(p["target_r"], "target_r")
        self.disp = fnum(p["disp_atr"], "disp_atr")
        self.valid = int(p["valid_bars"])
        self.swing_lb = int(p["swing_lookback"])
        need = self.atr_window + self.valid + 2
        if self.lookback[self.signal_tf] < need or self.swing_lb > self.atr_window:
            raise ValueError(f"{self.variant_id}: pencere ≥ {need} ve swing_lookback ≤ atr_window olmalı")

    def setup_at(self, b: Bars, i: int) -> tuple[str, float, float] | None:
        """i'de biten yer değiştirme + i + 1'de tamamlanan FVG kurulumu: (yön, limit, stop) ya da None. i + 1 barının
        kimliği ve i − swing_lookback barıyla önbelleklenir (yalnız o aralıktaki barlara bağlı)."""
        lo_i = i - self.swing_lb
        if lo_i < 1 or i + 1 >= b.n:
            return None
        key = ("MSS", b.ts[i + 1])
        got = self.memo.get(key, b.rows[i + 1], b.rows[lo_i])
        if got is not MISS:
            return got
        res = None
        a = self.atr_at(b, i - 1)
        body = b.c[i] - b.o[i]
        if a == a and a > 0 and abs(body) >= self.disp * a:
            side = LONG if body > 0 else SHORT
            j = last_swing(b, "H" if side == LONG else "L", i - 1, lo_idx=lo_i)
            if j is not None:
                lvl = b.h[j] if side == LONG else b.l[j]
                fresh = all((b.c[t] <= lvl) if side == LONG else (b.c[t] >= lvl) for t in range(j + 1, i))
                brk = b.c[i] > lvl if side == LONG else b.c[i] < lvl
                g = bullish_fvg(b, i) if side == LONG else bearish_fvg(b, i)
                s = last_swing(b, "L" if side == LONG else "H", i, lo_idx=lo_i)
                if fresh and brk and g is not None and s is not None:
                    limit = g[1] if side == LONG else g[0]
                    stop = stop_beyond(side, b.l[s] if side == LONG else b.h[s], self.buffer)
                    if (stop < limit) if side == LONG else (stop > limit):
                        res = (side, limit, stop)
        return self.memo.put(key, b.rows[i + 1], res, b.ts[0], b.rows[lo_i])

    def decide(self, bars, ctx):
        rows = bars.get(self.signal_tf) or ()
        now = int(ctx["now_ms"])
        if int(rows[-1]["timestamp"]) + self.step != now:
            return None
        if self.params["session"] == "KZ" and not self.in_kz(now):
            return None
        b = Bars(rows)
        m = b.n - 1
        for i in range(m - 1, max(m - self.valid, 0) - 1, -1):
            st = self.setup_at(b, i)
            if st is None:
                continue
            side, limit, stop = st
            for t in range(i + 2, m + 1):
                if (b.l[t] <= limit) if side == LONG else (b.h[t] >= limit):
                    return None                                            # en son kurulum tüketildi
            return self.intent(signal_ts=b.ts[i], side=side, ref=self.ref(b.c[m], limit=limit), stop=stop,
                               target=r_target(self.target_r), exit_rule="MSS_FVG_%gR" % self.target_r,
                               evidence={"disp_ts": b.ts[i], "limit": limit})
        return None


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> MssFvg:
    return MssFvg(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "MssFvg", "PARAM_KEYS", "make"]
