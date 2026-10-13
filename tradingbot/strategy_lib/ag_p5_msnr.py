"""S1d — P5 `MSNR` (1h gövde seviyeleri, 5m giriş; SAF). Mühür `LIB_AG_V1`.

* Seviye: karar anında KAPANMIŞ son 168 1h barın gövde tepeleri (max(açılış, kapanış)) ya da — ayrı küme olarak — gövde
  dipleri (min) arasında, birbirine %0,05 içinde olan ≥ 2 bar (iki tür de: aynı renk tepeler ve yükselen kapanış = düşen
  açılış — ikisi de gövde tepesidir; dipler simetrik). Kümeleme: değerler sıralanır, en küçükten başlayarak ilk değerin
  %0,05 üstüne kadarki değerler bir kümedir (≥ 2 üye → seviye = üyelerin ortalaması; oluşma = zamanca İKİNCİ üyenin barı).
* Taze: oluşmadan sonraki EN SON 1h GÖVDE geçişi (açılış < seviye < kapanış → yukarı; tersi aşağı). Yalnız ondan sonraki
  İLK dokunuş (bar aralığı seviyeyi içerir) işlem görür: dokunuş saati kapanmış bir 1h barıysa onun içindeki ilk 5m dokunuş,
  değilse son kapanmış 1h barından sonraki 5m barlarında ilk dokunuş.
* Dokunuştan sonra (dokunuş barı dahil) ≤ 12 5m bar içinde geçiş yönünde 5m MSS (kapanış, bir önceki barda doğrulanmış en son
  5m salınımının ötesinde ve o salınımdan beri ötesine İLK kapanış) → sonraki 5m açılışında giriş; stop = dokunuştan MSS
  barına kadarki uç + tampon. Hedef: `2R` ya da `NEXT_LEVEL` = kâr yönündeki bir sonraki seviye (bütün kümelerden, işlem
  görenin fiyatı hariç; yoksa işlem yok; < 1,5R ise yok — çekirdek, dolumla).
"""
from __future__ import annotations

from typing import Any, Mapping

from .ag_common import LONG, MISS, SHORT, AgVariant, Bars, fnum, index_at_or_after, last_swing, price_target, r_target, \
    stop_beyond

LIVE_MODULES: tuple[str, ...] = ()
PARAM_KEYS = ("common", "window", "target", "level_tol_frac", "level_lookback_1h", "min_bars", "mss_wait_bars",
              "next_level_min_r", "target_r")
H1 = 3_600_000


class Msnr(AgVariant):
    FAMILY = "AG_P5"
    PARAM_KEYS = PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        if p["target"] not in ("2R", "NEXT_LEVEL") or self.timeframes != ("5m", "1h") or self.signal_tf != "5m":
            raise ValueError(f"{self.variant_id}: target/dilimler")
        self.tol = fnum(p["level_tol_frac"], "level_tol_frac")
        self.lb1 = int(p["level_lookback_1h"])
        self.min_bars = int(p["min_bars"])
        self.wait = int(p["mss_wait_bars"])
        self.next_min_r = fnum(p["next_level_min_r"], "next_level_min_r")
        self.target_r = fnum(p["target_r"], "target_r")
        if self.lookback["1h"] != self.lb1 or self.min_bars < 2:
            raise ValueError(f"{self.variant_id}: 1h penceresi seviye penceresine eşit olmalı")

    # ------------------------------------------------------------------ 1h seviyeleri (son kapanmış 1h barı başına bir kez)
    def _clusters(self, pts: list[tuple[float, int]]) -> list[tuple[float, int]]:
        pts.sort()
        out = []
        i = 0
        while i < len(pts):
            v0 = pts[i][0]
            j = i
            while j + 1 < len(pts) and pts[j + 1][0] <= v0 * (1.0 + self.tol):
                j += 1
            if j - i + 1 >= self.min_bars:
                mem = pts[i:j + 1]
                out.append((sum(v for v, _ in mem) / len(mem), sorted(ix for _, ix in mem)[1]))
            i = j + 1
        return out

    def levels(self, rows1: Any) -> tuple[tuple, tuple[float, ...]]:
        """((seviye, yön, taze kapanış ms, dokunuş saati ms | None), …), bütün seviye fiyatları."""
        got = self.memo.get(("LV", int(rows1[-1]["timestamp"])), rows1[-1], rows1[0])
        if got is not MISS:
            return got
        b = Bars(rows1)
        tops = [(max(b.o[x], b.c[x]), x) for x in range(b.n)]
        bots = [(min(b.o[x], b.c[x]), x) for x in range(b.n)]
        cl = self._clusters(tops) + self._clusters(bots)
        lv = []
        for px, form in cl:
            f = next((x for x in range(b.n - 1, form, -1) if min(b.o[x], b.c[x]) < px < max(b.o[x], b.c[x])), None)
            if f is None:
                continue
            u = next((x for x in range(f + 1, b.n) if b.l[x] <= px <= b.h[x]), None)
            lv.append((px, LONG if b.c[f] > b.o[f] else SHORT, b.ts[f] + H1, None if u is None else b.ts[u]))
        res = (tuple(lv), tuple(sorted(px for px, _f in cl)))
        return self.memo.put(("LV", int(rows1[-1]["timestamp"])), rows1[-1], res, int(rows1[0]["timestamp"]), rows1[0])

    # ------------------------------------------------------------------ karar
    def _mss(self, b: Bars, m: int) -> list[str]:
        out = []
        for side, kind in ((LONG, "H"), (SHORT, "L")):
            j = last_swing(b, kind, m - 1)
            if j is None:
                continue
            lvl = b.h[j] if side == LONG else b.l[j]
            if ((b.c[m] > lvl) if side == LONG else (b.c[m] < lvl)) and all(
                    (b.c[t] <= lvl) if side == LONG else (b.c[t] >= lvl) for t in range(j + 1, m)):
                out.append(side)
        return out

    def decide(self, bars, ctx):
        rows = bars.get("5m") or ()
        rows1 = bars.get("1h") or ()
        now = int(ctx["now_ms"])
        if int(rows[-1]["timestamp"]) + self.step != now or len(rows1) < self.lb1:
            return None
        b = Bars(rows)
        m = b.n - 1
        sides = self._mss(b, m)
        if not sides:
            return None
        lv, prices = self.levels(rows1)
        last1_close = int(rows1[-1]["timestamp"]) + H1
        cands = []
        for px, side, _fresh, touch_h in lv:
            if side not in sides:
                continue
            if touch_h is not None:
                a, z = index_at_or_after(b.ts, touch_h), index_at_or_after(b.ts, touch_h + H1)
            else:
                a, z = index_at_or_after(b.ts, last1_close), m + 1
            t5 = next((t for t in range(a, min(z, m + 1)) if b.l[t] <= px <= b.h[t]), None)
            if t5 is None or not 0 <= m - t5 <= self.wait:
                continue
            cands.append((t5, -abs(b.c[m] - px), px, side))
        if not cands:
            return None
        t5, _d, px, side = max(cands)
        ext = min(b.l[t5:m + 1]) if side == LONG else max(b.h[t5:m + 1])
        stop = stop_beyond(side, ext, self.buffer)
        if self.params["target"] == "2R":
            tgt, ref = r_target(self.target_r), self.ref(b.c[m])
        else:
            beyond = [p for p in prices if (p > b.c[m] if side == LONG else p < b.c[m]) and p != px]
            if not beyond:
                return None
            nxt = min(beyond) if side == LONG else max(beyond)
            tgt, ref = price_target(nxt), self.ref(b.c[m], min_rr=self.next_min_r, target_beyond=True)
        return self.intent(signal_ts=b.ts[m], side=side, ref=ref, stop=stop, target=tgt, exit_rule="MSNR_" + self.params["target"],
                           evidence={"level": px, "touch_ts": b.ts[t5]})


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> Msnr:
    return Msnr(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "Msnr", "PARAM_KEYS", "make"]
