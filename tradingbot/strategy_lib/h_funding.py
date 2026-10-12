"""P3b-2 — H ailesi (fonlama): `FUND_FADE_{24h,72h}`, `FUND_FADE_LOW_{24h,72h}`, `FUND_CARRY` (SAF; docs/ENGINE_BRAIN_V1.md
§2.2). Fonlama `ctx["funding"]`'ten gelir (deponun uzlaşma satırları; çekirdek `funding_lookback_ms` geriye verir).

`f8` ve yüzdeliği fut_v1 laboratuvarının TANIMIDIR ve onun saf fonksiyonu çağrılır (`futures_features.align_funding`;
LIB okunuşu "H 'f8' = fut_v1 tanımı"): karar anı T (sinyal barının kapanışı) için görünür (uzlaşma ≤ T − 60 sn) son
uzlaşmanın 8 saate ölçeklenmiş oranı (oran × 8 / aralık; aralık ardışık farktan, {1, 2, 4, 8} saat); uzlaşma
aralık + 1 saat + 60 sn'den eskiyse yok. Yüzdelik = (T − 20 gün, güncel) penceresindeki DİĞER uzlaşmaların güncelden
KESİN küçük (`f8_hi`) / KESİN büyük (`f8_lo`) olanlarının payı, en az 30 değer (eşitlik uç sayılmaz). Pencere 20 gündür
(mühürlü `pct_window_days` / `pct_window.days` = fut_v1 `WINDOW_DAYS`; değilse kurulum hatası).

* **FUND_FADE** — f8 son 20 günün ≥ %95'liği (`f8_hi ≥ 0,95`) → short; ≤ %5'liği (`f8_lo ≥ 0,95`) → long. Çıkış
  yalnız en çok tutuş (24h = 6, 72h = 18 × 4h bar; sahip kararı B8 — ekin saati 4h bara bölünerek doğrulanır) ya da stop
  3 × ATR14(4h). Pozisyonda f8 karara girmez; bir karar anında f8 ölçülemezse işlem yine `F8_UNMEASURABLE` (bilgi)
  işaretlenir (H'nin bütün işlemlerinde aynı görünürlük).
* **FUND_FADE_LOW** — aynısı, yalnız ER20 < 0,3 (4h kapanışları; B3).
* **FUND_CARRY** — |f8| son 20 günün ≥ %90'lığı iken (|f8| üzerinde aynı fonksiyon: `f8_hi(|f8|) ≥ 0,90`) fonlamayı ALAN
  taraf (f8 > 0 → short, f8 < 0 → long; 0 → yön yok); çıkış |f8| < %50'lik (`f8_hi(|f8|) < 0,50`) YA DA f8'in işareti
  pozisyona TERS (short'ta f8 < 0, long'da f8 > 0: artık fonlamayı alan taraf değil; f8 = 0 ters değildir) → sonraki
  açılış (mühürlü teknik okuma, LIB `exits.exit_rule.sign_against`, 2026-10-12, P3b-2a inceleme bulgu 3); stop
  3 × ATR14(4h); en çok 300 bar (LIB varsayılanı). Kural çıkışının bir parçası ölçülemiyorsa (fonlama yok/bayat, aralık
  kuralı) o parça uygulanmaz, ölçülemeyen kararda pozisyon tutulur ve işlem `F8_UNMEASURABLE` (bilgi) işaretlenir.

**Aralık kuralı** (P3b-2a inceleme bulgu 1, 2026-10-12; muhafazakâr): fut_v1 aralığı ardışık farktan çıkarır; değişim
noktasında ya da eksik bir uzlaşmada (4h/2h/1h dizisinde tek eksik 8h/4h/2h okunur) f8 YARIYA iner ve yanlış bir uç
(FUND_FADE LONG, FUND_CARRY'nin |f8| çıkışı) üretebilirdi (açık başarısızlık). Bu yüzden karar anında GÖRÜNÜR uzlaşmalarda
(≤ T − 60 sn) güncel uzlaşmanın çıkarılan aralığı bir önceki uzlaşmanınkinden FARKLIYSA ya da son iki aralıktan biri
çekirdeğin YEREL aralık kuralına göre boşluksa (`kernel.FundingSeries`: aralık, iki yanındaki komşu aralıkların büyüğünün
ya da iki yandaki üçer aralığın medyanlarının büyüğünün 1,5 katından uzun; karar anında yalnız geçmiş tarafı vardır;
damgalar nominal dakikaya yuvarlanır) f8, `f8_hi` ve `f8_lo` NaN'dır → giriş yok, pozisyonda durum değişmez. Kör nokta
(çekirdeğin m-D'siyle aynı): 8h → 4h geçişinde İLK 4h uzlaşmasının yokluğu 8h'lik bir aralık bırakır ve değişimin bir
uzlaşma sonra olmasından ayırt edilemez → o tek karar penceresinde (bir sonraki uzlaşmaya kadar) f8 yarıdır; arka arkaya
her ikinci uzlaşmanın eksik olduğu sürekli bir desen de gerçek bir 4h → 8h geçişinden ayırt edilemez.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .. import futures_features as FF
from . import indicators as ind
from .contract import LONG, NONE, SHORT, TF_MS, Action, close, hold
from .pure import Cols, PureVariant

FADE_PARAM_KEYS = ("horizon_label", "funding", "pct_window_days", "short_pct_min", "long_pct_max")
FADE_LOW_PARAM_KEYS = FADE_PARAM_KEYS + ("er20_max",)
CARRY_PARAM_KEYS = ("funding", "enter_abs_pct_min", "exit_abs_pct_below", "pct_window")
CARRY_SIDES = "fonlamayı alan taraf"
ER_LEN = 20
#: kimliğe giren canlı modüller: fut_v1 fonlama özellikleri (f8 + 20 günlük yüzdelik) ve onun dilim yardımcısı
LIVE_MODULES: tuple[str, ...] = ("tradingbot.futures_features", "tradingbot.timeframes")
#: açık pozisyonda bir karar anında f8 (FUND_CARRY'de ayrıca |f8|'in sırası) ölçülemedi → durum değişmedi (bilgi
#: işareti; `Action.flags`; kapıdan çıkarmaz)
FLAG_F8_UNMEASURABLE = "F8_UNMEASURABLE"
#: çekirdeğin yerel aralık kuralının sabitleri (`kernel.FUNDING_GAP_FACTOR`, `FUNDING_LOCAL_SPAN`, `FUNDING_SNAP_MS`;
#: eşitlikleri ve kuralın `kernel.FundingSeries.gap_after` ile aynılığı testli — paket motoru import etmez)
GAP_FACTOR = 1.5
LOCAL_SPAN = 3
SNAP_MS = 60_000
_NAN = float("nan")
_NAN3 = (_NAN, _NAN, _NAN)


def _snap(t: int) -> int:
    return (int(t) + SNAP_MS // 2) // SNAP_MS * SNAP_MS


def _median(xs: Sequence[int]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = len(s) // 2
    return float(s[k]) if len(s) % 2 else (s[k - 1] + s[k]) / 2.0


def local_gaps(ts: Sequence[int]) -> list[bool]:
    """Sıralı, tekil (nominal dakikaya yuvarlanmış) damgaların her aralığı için çekirdeğin YEREL aralık kuralı
    (`kernel.FundingSeries._is_gap` ile aynı tanım; testli): i. aralık, iki yanındaki komşu aralıkların büyüğünün ya da iki
    yandaki `LOCAL_SPAN` aralığın medyanlarının büyüğünün `GAP_FACTOR` katını aşarsa boşluktur; tek aralık kıyassızdır."""
    d = [b - a for a, b in zip(ts, ts[1:])]
    out = []
    for i in range(len(d)):
        near = [d[j] for j in (i - 1, i + 1) if 0 <= j < len(d)]
        if not near:
            out.append(False)
            continue
        wide = [x for x in (_median(d[max(0, i - LOCAL_SPAN):i]), _median(d[i + 1:i + 1 + LOCAL_SPAN])) if x is not None]
        out.append(d[i] > GAP_FACTOR * max(near) or d[i] > GAP_FACTOR * max(wide))
    return out


def interval_steady(t_ms: Sequence[int], now_ms: int) -> bool:
    """Aralık kuralı (modül belgesi): karar anında görünür (≤ now − 60 sn) uzlaşmalarda güncel uzlaşmanın fut_v1 aralığı
    (`futures_features.infer_interval_h`) bir öncekininkine eşit VE son iki aralık yerel kurala göre boşluk değil. En az
    üç görünür uzlaşma gerekir (iki aralık); değilse False (f8 doğrulanamaz)."""
    lim = int(now_ms) - FF.FUND_GUARD_MS
    vis = sorted(int(t) for t in t_ms if int(t) <= lim)
    if len(vis) < 3:
        return False
    iv = FF.infer_interval_h(np.asarray(vis[-3:], dtype=np.int64))
    a, b = float(iv[1]), float(iv[2])
    if not (a == a and b == b) or a != b:
        return False
    tail: list[int] = []                                  # son 2 + LOCAL_SPAN + 1 aralığın tekil yuvarlanmış damgaları
    for t in reversed(vis):
        k = _snap(t)
        if not tail or k != tail[-1]:
            tail.append(k)
        if len(tail) >= LOCAL_SPAN + 4:
            break
    tail.reverse()
    if len(tail) < 3:
        return False
    g = local_gaps(tail)
    return not (g[-1] or g[-2])


def f8_state(rows: Sequence[Sequence[Any]] | None, now_ms: int, *, absolute: bool = False) -> tuple[float, float, float]:
    """(f8, f8_hi, f8_lo) karar anı `now_ms`'de — `futures_features.align_funding` (fut_v1) uzlaşma satırlarından
    (`(ms, oran, …)`). `absolute`: oranların mutlak değeriyle (|f8| ve onun yüzdeliği). Yoksa ya da aralık kuralı
    (`interval_steady`) tutmazsa NaN."""
    if not rows:
        return _NAN3
    t = [int(r[0]) for r in rows]
    if not interval_steady(t, now_ms):
        return _NAN3
    f = [abs(_rate(r[1])) if absolute else _rate(r[1]) for r in rows]
    out = FF.align_funding(np.asarray([int(now_ms)], dtype=np.int64), t, f)
    return float(out["f8"][0]), float(out["f8_hi"][0]), float(out["f8_lo"][0])


def _rate(x: Any) -> float:
    """Okunamayan oran NaN (fut_v1: NaN uzlaşma ne güncel ne pencere değeri olur)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return _NAN


def _pct(x: Any, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x < 100:
        raise ValueError(f"{what} (0, 100) yüzde olmalı: {x!r}")
    return float(x) / 100.0


class _FundingBase(PureVariant):
    FAMILY = "H"
    live_modules = LIVE_MODULES
    needs_funding = True
    funding_lookback_ms = int(FF.live_requirements("4h")["funding_ms"])

    def _check_common(self, p: Mapping[str, Any], window_days: Any) -> None:
        if p["funding"] != "f8":
            raise ValueError(f"{self.variant_id}: fonlama ölçüsü f8 (fut_v1) olmalı")
        if window_days != FF.WINDOW_DAYS:
            raise ValueError(f"{self.variant_id}: yüzdelik penceresi fut_v1'in {FF.WINDOW_DAYS} günü olmalı: {window_days!r}")
        if self.signal_tf != "4h":
            raise ValueError(f"{self.variant_id}: dilim 4h olmalı")


class FundingFade(_FundingBase):
    PARAM_KEYS = FADE_PARAM_KEYS
    EXIT_RULE = "MAX_HOLD_OR_STOP"
    SIDES_ALLOWED = ("LS",)

    def setup(self, p: Mapping[str, Any]) -> None:
        self._check_common(p, p["pct_window_days"])
        self.short_min = _pct(p["short_pct_min"], f"{self.variant_id}: short_pct_min")
        self.long_min = 1.0 - _pct(p["long_pct_max"], f"{self.variant_id}: long_pct_max")
        label = str(p["horizon_label"])
        hours = int(label[:-1]) if label.endswith("h") and label[:-1].isdigit() else -1
        bars = hours * 3_600_000 / TF_MS[self.signal_tf]
        ex = self.spec_exit_rule
        if not (bars == self.max_hold and isinstance(ex, Mapping) and ex.get("max_hold_bars") == self.max_hold
                and ex.get("decision") == "B8") or self.target_r is not None:
            raise ValueError(f"{self.variant_id}: '{label}' = en çok tutuş {bars} × 4h olmalı (B8); gelen {self.max_hold}")

    def _extra_filter(self, cols: Cols) -> tuple[bool, dict[str, Any]]:
        return True, {}

    def manage(self, pos, bars, ctx) -> Action:
        """Kural çıkışı yok (zaman/stop çekirdekte); f8 bu karar anında ölçülemiyorsa yalnız bilgi işareti."""
        if len(bars.get(self.signal_tf) or ()) < 2:
            return NONE
        f8, _hi, _lo = f8_state(ctx.get("funding"), int(ctx["now_ms"]))
        return NONE if f8 == f8 else hold(FLAG_F8_UNMEASURABLE)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        f8, hi, lo = f8_state(ctx.get("funding"), int(ctx["now_ms"]))
        if f8 != f8 or hi != hi or lo != lo:
            return None
        if hi >= self.short_min:
            side = SHORT
        elif lo >= self.long_min:
            side = LONG
        else:
            return None
        ok, ev = self._extra_filter(cols)
        if not ok:
            return None
        return side, {"f8": f8, "f8_hi": hi, "f8_lo": lo, **ev}


class FundingFadeLowEr(FundingFade):
    PARAM_KEYS = FADE_LOW_PARAM_KEYS

    def setup(self, p: Mapping[str, Any]) -> None:
        super().setup(p)
        x = p["er20_max"]
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x <= 1:
            raise ValueError(f"{self.variant_id}: er20_max (0, 1] olmalı")
        self.er_max = float(x)

    def _extra_filter(self, cols: Cols) -> tuple[bool, dict[str, Any]]:
        e = ind.er(cols.close, ER_LEN, len(cols) - 1)
        return (e is not None and e < self.er_max), {"er20": e}


class FundingCarry(_FundingBase):
    PARAM_KEYS = CARRY_PARAM_KEYS
    EXIT_RULE = "ABS_F8_BELOW_PCTL_OR_SIGN_AGAINST"
    SIDES_ALLOWED = (CARRY_SIDES,)

    def setup(self, p: Mapping[str, Any]) -> None:
        w = p["pct_window"]
        self._check_common(p, w.get("days") if isinstance(w, Mapping) else None)
        if not (isinstance(w, Mapping) and w.get("decision") == "B8"):
            raise ValueError(f"{self.variant_id}: pct_window sahip kararı B8 olmalı")
        self.enter_min = _pct(p["enter_abs_pct_min"], f"{self.variant_id}: enter_abs_pct_min")
        self.exit_below = _pct(p["exit_abs_pct_below"], f"{self.variant_id}: exit_abs_pct_below")
        if not self.exit_below < self.enter_min or self.target_r is not None:
            raise ValueError(f"{self.variant_id}: çıkış yüzdeliği girişinkinden küçük olmalı, hedef yok")
        ex = self.spec_exit_rule
        if not (isinstance(ex, Mapping) and ex.get("sign_against") is True and ex.get("abs_pct_below") == p["exit_abs_pct_below"]
                and "P3b-2a inceleme bulgu 3" in str(ex.get("decision", ""))):
            raise ValueError(f"{self.variant_id}: çıkış kuralı |f8| < %{p['exit_abs_pct_below']}'lik ya da işaret ters olmalı "
                             f"(teknik okuma, bulgu 3): {ex!r}")
        self.allow_short = True                          # yön fonlamanın işaretinden (alan taraf)

    def entry(self, cols: Cols, ctx) -> tuple[str, dict[str, Any]] | None:
        rows, now = ctx.get("funding"), int(ctx["now_ms"])
        af8, ahi, _alo = f8_state(rows, now, absolute=True)
        if af8 != af8 or ahi != ahi or ahi < self.enter_min:
            return None
        f8, _hi, _lo = f8_state(rows, now)
        if f8 != f8 or f8 == 0.0:
            return None
        return (SHORT if f8 > 0 else LONG), {"f8": f8, "abs_f8_hi": ahi}

    def manage(self, pos, bars, ctx) -> Action:
        """Çıkış: |f8| < %50'lik ya da f8'in işareti pozisyona ters → CLOSE (sonraki açılış). Bir parça ölçülemiyorsa o
        parça uygulanmaz; hiçbir çıkış yoksa ve bir parça ölçülemediyse `F8_UNMEASURABLE` işaretiyle tut."""
        rows = bars.get(self.signal_tf) or ()
        if len(rows) < 2:
            return NONE
        fr, now = ctx.get("funding"), int(ctx["now_ms"])
        _af8, ahi, _alo = f8_state(fr, now, absolute=True)
        if ahi == ahi and ahi < self.exit_below:
            return close("ABS_F8_BELOW_P%d" % round(self.exit_below * 100))
        f8, _hi, _lo = f8_state(fr, now)
        if f8 == f8 and ((pos.side == SHORT and f8 < 0.0) or (pos.side == LONG and f8 > 0.0)):
            return close("F8_SIGN_AGAINST_POSITION")
        if f8 != f8 or ahi != ahi:
            return hold(FLAG_F8_UNMEASURABLE)
        return NONE


def make(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None) -> PureVariant:
    if variant_id.startswith("FUND_FADE_LOW_"):
        return FundingFadeLowEr(variant_id, params, spec)
    if variant_id.startswith("FUND_FADE_"):
        return FundingFade(variant_id, params, spec)
    if variant_id == "FUND_CARRY":
        return FundingCarry(variant_id, params, spec)
    raise KeyError(variant_id)


__all__ = ["CARRY_PARAM_KEYS", "FADE_LOW_PARAM_KEYS", "FADE_PARAM_KEYS", "FLAG_F8_UNMEASURABLE", "FundingCarry", "FundingFade",
           "FundingFadeLowEr", "GAP_FACTOR", "LIVE_MODULES", "LOCAL_SPAN", "SNAP_MS", "f8_state", "interval_steady",
           "local_gaps", "make"]
