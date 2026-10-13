"""S1d — P2 `TENM` (10:00 New York açılış modeli; 5m; SAF). Mühür `LIB_AG_V1` (docs/ENGINE_BRAIN_V1.md §8.6 S1d).

Çekirdekte 1m yolu yok (1m yalnız yol kaydı için tembel doldurulur, `lazy1m`) → kurulum 5m'dedir.

* L = açılışı 10:00 New York olan 5m barın AÇILIŞI (bar yoksa o gün yok). 09:30–10:00 yalnız gözlem. Pencere 10:00–12:00:
  giriş anı (karar barının kapanışı = sonraki açılış) < 12:00.
* `wickless`: 10:00 barının açılış yanı fitili (düşen bar: yüksek − açılış; yükselen: açılış − düşük) ≤ barın aralığının
  %10'u (doji ya da sıfır aralık → değil). `wickless_required = yes` → yalnız böyle günler.
* Ölü gün: 10:00 barından karar barına kadarki 5m KAPANIŞLARI L'yi 3 kez kesmişse (kapanışın L'ye göre yanı 3 kez
  değişmişse; L'ye eşit kapanış yanı değiştirmez) o gün yeni giriş yok.
* Kip A (çizgiye dönüş): açılışı ≥ 10:05 olan bir bar, o New York günü daha önce oluşmuş (üç barı da gün içinde) ve henüz
  hiç içine girilmemiş bir 5m FVG'ye İLK kez girer (uzun: L'nin altındaki yükseliş FVG'si, düşük ≤ FVG üstü; kısa
  simetrik) ve sonra FVG'den L'ye doğru dışarı kapanan İLK bar (uzun: kapanış > FVG üstü, < L) karar barıdır; arada FVG'nin
  ötesine kapanış (uzun: < FVG altı) kurulumu bozar → sonraki açılışta L'ye doğru giriş; stop = manipülasyon ucu (10:00
  barından karar barına kadarki en düşük / en yüksek) + tampon; hedef = L; hedef < 1,5R ise yok (çekirdek, dolumla).
* Kip B (kırılım ve yeniden sınama): gövdesi ≥ 1,5 × ATR14 (önceki barın) olan ve L'yi kesen bir 5m kapanış (yükseliş:
  önceki kapanış ≤ L < kapanış; 10:00 barında önceki = L) → kırılım yönünde L'de limit; ilk geri dönüş (uzun: düşük ≤ L)
  kurulumu tüketir (çekirdeğin tek-bar limit kuralıyla dolar ya da dolmaz); en son kırılım geçerlidir; stop = kırılım barının
  karşı ucu + tampon; hedef 3R. Günde bir işlem.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import ag_clock as C
from .ag_common import (LONG, SHORT, AgVariant, Bars, bearish_fvg, bullish_fvg, fnum, index_at_or_after, price_target,
                        r_target, stop_beyond)

LIVE_MODULES: tuple[str, ...] = ()
PARAM_KEYS = ("common", "window", "mode", "wickless_required", "line_bar_min", "window_min", "mode_a_after_min",
              "wickless_max_frac", "dead_after_crosses", "mode_a_min_tp_r", "mode_b_disp_atr", "mode_b_target_r")


class Tenm(AgVariant):
    FAMILY = "AG_P2"
    PARAM_KEYS = PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        if p["mode"] not in ("A", "B") or not isinstance(p["wickless_required"], bool):
            raise ValueError(f"{self.variant_id}: mode/wickless_required")
        if self.timeframes != ("5m",):
            raise ValueError(f"{self.variant_id}: yalnız 5m")
        self.line_min = int(p["line_bar_min"])
        self.win_a, self.win_b = (int(x) for x in p["window_min"])
        self.a_after = int(p["mode_a_after_min"])
        self.wick_max = fnum(p["wickless_max_frac"], "wickless_max_frac")
        self.dead_n = int(p["dead_after_crosses"])
        self.a_min_r = fnum(p["mode_a_min_tp_r"], "mode_a_min_tp_r")
        self.b_atr = fnum(p["mode_b_disp_atr"], "mode_b_disp_atr")
        self.b_r = fnum(p["mode_b_target_r"], "mode_b_target_r")
        if not (self.win_a == self.line_min < self.a_after < self.win_b):
            raise ValueError(f"{self.variant_id}: saat sırası")

    @staticmethod
    def crosses(b: Bars, i10: int, k: int, line: float) -> int:
        side, n = 0, 0
        for j in range(i10, k + 1):
            c = b.c[j]
            s = 1 if c > line else (-1 if c < line else 0)
            if s == 0:
                continue
            if side != 0 and s != side:
                n += 1
            side = s
        return n

    def wickless(self, b: Bars, i10: int) -> bool:
        o, h, lo, c = b.o[i10], b.h[i10], b.l[i10], b.c[i10]
        rng = h - lo
        if rng <= 0 or c == o:
            return False
        wick = (h - o) if c < o else (o - lo)
        return wick <= self.wick_max * rng

    def decide(self, bars, ctx):
        rows = bars.get("5m") or ()
        now = int(ctx["now_ms"])
        t_k = int(rows[-1]["timestamp"])
        if t_k + self.step != now:
            return None
        day = C.ny_day(t_k)
        t10, t12 = C.ny_to_utc(day, self.line_min), C.ny_to_utc(day, self.win_b)
        if t10 is None or t12 is None or not (t10 <= t_k and now < t12):
            return None
        b = Bars(rows)
        k = b.n - 1
        i10 = index_at_or_after(b.ts, t10)
        if i10 > k or b.ts[i10] != t10:
            return None
        line = b.o[i10]
        if self.params["wickless_required"] and not self.wickless(b, i10):
            return None
        if self.crosses(b, i10, k, line) >= self.dead_n:
            return None
        if self.params["mode"] == "A":
            return self._mode_a(b, k, i10, line, day)
        return self._mode_b(b, k, i10, line)

    # ------------------------------------------------------------------ kip A
    def _mode_a(self, b: Bars, k: int, i10: int, line: float, day: int):
        t_after = C.ny_to_utc(day, self.a_after)
        d0 = C.ny_to_utc(day, 0)
        if t_after is None or d0 is None or b.ts[k] < t_after:
            return None
        i0 = index_at_or_after(b.ts, d0)
        best = None
        for i in range(max(i0 + 1, 1), k - 1):
            for side, g in ((LONG, bullish_fvg(b, i)), (SHORT, bearish_fvg(b, i))):
                if g is None:
                    continue
                lo, hi = g
                if (side == LONG and not hi < line) or (side == SHORT and not lo > line):
                    continue
                tap = next((j for j in range(i + 2, k + 1) if (b.l[j] <= hi if side == LONG else b.h[j] >= lo)), None)
                if tap is None or b.ts[tap] < t_after:
                    continue
                conf = None
                for j in range(tap, k + 1):
                    if (b.c[j] > hi) if side == LONG else (b.c[j] < lo):
                        conf = j
                        break
                    if (b.c[j] < lo) if side == LONG else (b.c[j] > hi):
                        break                                              # FVG'nin ötesine kapanış: bozuldu
                if conf != k or not ((b.c[k] < line) if side == LONG else (b.c[k] > line)):
                    continue
                key = (tap, hi if side == LONG else -lo)
                if best is None or key > best[0]:
                    best = (key, side, i, tap, g)
        if best is None:
            return None
        _key, side, i, tap, g = best
        ext = min(b.l[i10:k + 1]) if side == LONG else max(b.h[i10:k + 1])
        stop = stop_beyond(side, ext, self.buffer)
        return self.intent(signal_ts=b.ts[k], side=side, ref=self.ref(b.c[k], min_rr=self.a_min_r, target_beyond=True),
                           stop=stop, target=price_target(line), exit_rule="TENM_A_TO_LINE",
                           evidence={"line": line, "fvg": list(g), "fvg_mid_ts": b.ts[i], "tap_ts": b.ts[tap]})

    # ------------------------------------------------------------------ kip B
    def _mode_b(self, b: Bars, k: int, i10: int, line: float):
        disp = None
        for d in range(k, i10 - 1, -1):
            prev = line if d == i10 else b.c[d - 1]
            up = prev <= line < b.c[d]
            dn = prev >= line > b.c[d]
            if not (up or dn):
                continue
            a = self.atr_at(b, d - 1)
            if a == a and a > 0 and abs(b.c[d] - b.o[d]) >= self.b_atr * a:
                disp = (d, LONG if up else SHORT)
                break
        if disp is None:
            return None
        d, side = disp
        for j in range(d + 1, k + 1):
            if (b.l[j] <= line) if side == LONG else (b.h[j] >= line):
                return None                                                # ilk geri dönüş kullanıldı
        stop = stop_beyond(side, b.l[d] if side == LONG else b.h[d], self.buffer)
        return self.intent(signal_ts=b.ts[d], side=side, ref=self.ref(b.c[k], limit=line), stop=stop,
                           target=r_target(self.b_r), exit_rule="TENM_B_RETEST_3R",
                           evidence={"line": line, "disp_ts": b.ts[d]})


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> Tenm:
    return Tenm(variant_id, params, spec)


__all__ = ["LIVE_MODULES", "PARAM_KEYS", "Tenm", "make"]
