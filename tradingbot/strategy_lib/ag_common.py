"""S1d — `LIB_AG_V1` ailelerinin ortak tabanı (SAF: G/Ç, saat, ağ, rastgelelik yok; AST testi).

Sahip kararı 2026-10-13: ICT tarzı bir takdir tüccarının (öldürme bölgeleri, öğle süpürmesi, 10:00 "TENM" modeli,
MSS/FVG, ret blokları, MSNR) taktikleri MEKANİK varyantlara çevrilir; ayrı adlı `LIB_AG_V1` mühründe (S1 dokunulmadı),
`LIB_V1` ile aynı kapılardan geçer. Bütün sayılar mühürdendir (motorun S1d mührü `seal_s1d`); bu paket sayı yazmaz,
varyantın `params`'ını (anahtar kümesi tam) ve satırın `spec`'ini (`{family, timeframes, signal_tf, exits}`) alır.

Ortak kurallar (mühürlü `params["common"]`):

* **Saat** New York yereli (`ag_clock`), barın açılış zamanından; oturumlar yarı açık dakika aralıkları.
* **Salınım** 3 barlık fraktal: orta barın yükseği iki komşusununkinden BÜYÜK (düşük için simetrik); yalnız sağ komşu
  KAPANINCA doğrulanmış sayılır (j. bar, j + 1 kapanmışsa). `swing_high(H, j)` 1 ≤ j ≤ n − 2'de tanımlıdır.
* **ATR14** kurulum diliminde Wilder (`indicators.atr`), barın kendisinde biten SABİT 100 barlık pencerede (mühürlü okuma:
  Wilder'ın tohumu pencereye bağlıdır; aynı tanım her koşuda aynı sayıyı verir; 100 barda tohum etkisi (13/14)^99 ≈ 7e-4);
  büyüklük karşılaştırmasında ÖNCEKİ barın ATR'si kullanılır (yer değiştirme barı kendi ATR'sini şişirmez).
* **Tampon** yapısal stopun %0,05 ötesi (fiyatın oranı). **Stop mesafesi** giriş fiyatının %0,08'inden küçük ya da
  %1,2'sinden büyükse işlem yok — çekirdekte DOLUM fiyatıyla (`AgEntryRef.stop_pct_min/max`).
* **Limit emri** çekirdeğin S1d tek-bar limit sözleşmesiyle (`AgEntryRef.limit_price`): emir YALNIZ sonraki bar için
  geçerlidir; o bar seviyeyi ≥ 1 tik İÇİNDEN GEÇERSE (uzun: düşük ≤ seviye − tik; kısa: yüksek ≥ seviye + tik; tik 0 ise
  kesin geçiş) seviyeden dolar (taker ücreti + senaryonun kayması/spread'i — piyasa girişiyle aynı maliyet, daha ihtiyatlı),
  değilse iptal (`LIMIT_NOT_FILLED`). Kurulum geçerli kaldıkça strateji emri her bar yeniden verir (durumsuz; çekirdeğin
  her bar kapanışında "düz" durumu korunur → keşfin artımlı = baştan sözleşmesi bozulmaz). Seviyeye İLK dokunuş (uzun:
  düşük ≤ seviye) kurulumu tüketir: geçmediyse dolum yok sayılır (kuyruğa ulaşılmadı varsayımı; iyimser değil).
* **Günde bir** işlem (varyant × sembol × New York günü; `ctx.history`'nin dolum anları), tek açık pozisyon (çekirdek).
* **İleriye bakma yok:** her başvuru seviyesi yalnız karar anında KAPANMIŞ barlardan (çekirdek yalnız onları verir).

Hız: aileler ucuz zaman/koşul ön-süzgeciyle başlar; bar başına türetilen değerler (ATR, kurulumlar) `Memo`'da bar nesnesinin
KİMLİĞİYLE doğrulanarak saklanır (aynı barlar → aynı değer; farklı seri → kaçırma), `bind_series` sıfırlar. Önbellek
sonucu değiştirmez (test: önbelleksiz yeniden hesapla bayt-aynı).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import ag_clock as C
from . import indicators as ind
from .contract import (LONG, NONE, SHORT, TARGET_PRICE, TARGET_R_FROM_ENTRY, TF_MS, Action, EntryRef, Intent, PosView,
                       Target, Variant, check_params, close)

COMMON_KEYS = ("sessions_min", "kz", "buffer_frac", "stop_pct_min", "stop_pct_max", "atr_len", "atr_window", "atr_at",
               "per_ny_day", "limit_fill", "swing", "clock")
SPEC_KEYS = ("family", "timeframes", "signal_tf", "exits")
EXIT_KEYS = ("stop", "exit_rule", "target", "max_hold_bars")
MISS = object()


@dataclass(frozen=True)
class AgEntryRef(EntryRef):
    """S1d giriş referansı (`contract.EntryRef`'in alt sınıfı; sözleşme dosyası değişmedi → LIB_V1 kimlikleri aynı).

    `limit_price`: tek-bar limit emri (modül belgesi). `stop_pct_min` / `stop_pct_max`: |dolum − stop| / dolum aralığı
    (dışında `STOP_DISTANCE_OUTSIDE_PCT`). `target_beyond_entry`: ilk hedef dolumun kâr yönünde ötesinde olmalı (fiyat
    hedefli kurulumlarda; değilse `TARGET_NOT_BEYOND_ENTRY`). Hepsi çekirdekte dolum fiyatıyla sınanır."""

    limit_price: float | None = None
    stop_pct_min: float | None = None
    stop_pct_max: float | None = None
    target_beyond_entry: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        for k in ("limit_price", "stop_pct_min", "stop_pct_max"):
            v = getattr(self, k)
            if v is not None:
                v = float(v)
                if not math.isfinite(v) or v < 0:
                    raise ValueError(f"entry_ref.{k} sonlu ve negatif olmayan olmalı: {v!r}")
                object.__setattr__(self, k, v)
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("entry_ref.limit_price pozitif olmalı")
        if (self.stop_pct_min is not None and self.stop_pct_max is not None
                and not self.stop_pct_min < self.stop_pct_max):
            raise ValueError("entry_ref.stop_pct_min < stop_pct_max olmalı")
        object.__setattr__(self, "target_beyond_entry", bool(self.target_beyond_entry))


class Memo:
    """Bar başına türetilen değerlerin önbelleği: anahtar (tür, bar açılışı), değer bar NESNESİNİN kimliğiyle doğrulanır
    (güçlü başvuru tutulur → kimlik yeniden kullanılamaz). Kronolojik akışta eski girdiler ara ara budanır."""

    __slots__ = ("d", "n")

    def __init__(self) -> None:
        self.d: dict[tuple, tuple[Any, Any, Any]] = {}
        self.n = 0

    def clear(self) -> None:
        self.d.clear()
        self.n = 0

    def get(self, key: tuple, bar: Any, anchor: Any = None) -> Any:
        e = self.d.get(key)
        if e is None or e[0] is not bar or e[1] is not anchor:
            return MISS
        return e[2]

    def put(self, key: tuple, bar: Any, val: Any, floor_ts: int, anchor: Any = None) -> Any:
        self.d[key] = (bar, anchor, val)
        self.n += 1
        if self.n % 4096 == 0:
            for k in [k for k in self.d if k[1] < floor_ts]:
                del self.d[k]
        return val


def fnum(x: Any, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)):
        raise ValueError(f"{what} sonlu sayı olmalı: {x!r}")
    return float(x)


class Bars:
    """Bir dilimin kapanmış barlarının sütunları (yalnız gerekli kuyruk; girdiler değiştirilmez)."""

    __slots__ = ("rows", "ts", "o", "h", "l", "c", "n")

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self.rows = rows
        self.ts = [int(r["timestamp"]) for r in rows]
        self.o = [float(r["open"]) for r in rows]
        self.h = [float(r["high"]) for r in rows]
        self.l = [float(r["low"]) for r in rows]
        self.c = [float(r["close"]) for r in rows]
        self.n = len(rows)


def swing_high(h: Sequence[float], j: int) -> bool:
    return 1 <= j <= len(h) - 2 and h[j] > h[j - 1] and h[j] > h[j + 1]


def swing_low(lo: Sequence[float], j: int) -> bool:
    return 1 <= j <= len(lo) - 2 and lo[j] < lo[j - 1] and lo[j] < lo[j + 1]


def last_swing(b: Bars, kind: str, confirmed_by: int, *, lo_idx: int = 1) -> int | None:
    """`confirmed_by` barının kapanışında doğrulanmış (j + 1 ≤ confirmed_by) en son salınımın indeksi; yoksa None."""
    j = min(confirmed_by - 1, b.n - 2)
    f = swing_high if kind == "H" else swing_low
    arr = b.h if kind == "H" else b.l
    while j >= max(1, lo_idx):
        if f(arr, j):
            return j
        j -= 1
    return None


def bullish_fvg(b: Bars, i: int) -> tuple[float, float] | None:
    """i. bar ortadaki bar: bar[i−1].high < bar[i+1].low → (alt, üst) = (bar[i−1].high, bar[i+1].low)."""
    if i < 1 or i + 1 >= b.n:
        return None
    lo, hi = b.h[i - 1], b.l[i + 1]
    return (lo, hi) if lo < hi else None


def bearish_fvg(b: Bars, i: int) -> tuple[float, float] | None:
    """bar[i−1].low > bar[i+1].high → (alt, üst) = (bar[i+1].high, bar[i−1].low)."""
    if i < 1 or i + 1 >= b.n:
        return None
    lo, hi = b.h[i + 1], b.l[i - 1]
    return (lo, hi) if lo < hi else None


def stop_beyond(side: str, extreme: float, buffer_frac: float) -> float:
    """Yapısal uç + tampon: LONG uç × (1 − tampon), SHORT uç × (1 + tampon)."""
    return extreme * (1.0 - buffer_frac) if side == LONG else extreme * (1.0 + buffer_frac)


def rows_of(bars: Mapping[str, Sequence[Mapping[str, Any]]], tf: str) -> Sequence[Mapping[str, Any]]:
    return bars.get(tf) or ()


def index_at_or_after(ts: Sequence[int], t: int) -> int:
    lo, hi = 0, len(ts)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    return lo


class AgVariant(Variant):
    """`LIB_AG_V1` ailelerinin tabanı. Alt sınıf `FAMILY`, `PARAM_KEYS` (ortak `common` ve `window` dahil), `TF_KEYS`
    ve `decide(ctx_bars, ctx)` / `rule_exit(pos, bars, ctx)` yazar."""

    FAMILY = ""
    PARAM_KEYS: tuple[str, ...] = ()
    live_modules: tuple[str, ...] = ()

    def __init__(self, variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None):
        p = check_params(variant_id, params, self.PARAM_KEYS)
        if spec is None or set(spec) != set(SPEC_KEYS):
            raise ValueError(f"{variant_id}: spec anahtarları {sorted(SPEC_KEYS)} olmalı")
        if spec["family"] != self.FAMILY:
            raise ValueError(f"{variant_id}: aile {self.FAMILY} olmalı, gelen {spec['family']!r}")
        ex = spec["exits"]
        if set(ex) != set(EXIT_KEYS):
            raise ValueError(f"{variant_id}: exits anahtarları {sorted(EXIT_KEYS)} olmalı")
        mh = ex["max_hold_bars"]
        if isinstance(mh, bool) or not isinstance(mh, int) or mh < 1:
            raise ValueError(f"{variant_id}: max_hold_bars ≥ 1 tam sayı olmalı")
        cm = p["common"]
        if set(cm) != set(COMMON_KEYS):
            raise ValueError(f"{variant_id}: common anahtarları {sorted(COMMON_KEYS)} olmalı, gelen {sorted(cm)}")
        if cm["clock"] != "America/New_York" or cm["swing"] != "FRACTAL_3_RIGHT_NEIGHBOUR_CLOSED" \
                or cm["atr_at"] != "PREVIOUS_BAR" or cm["limit_fill"] != "ONE_BAR_THROUGH_1_TICK_AT_LEVEL_TAKER":
            raise ValueError(f"{variant_id}: common tanımları beklenmiyor")
        self.variant_id = variant_id
        self.family = self.FAMILY
        self.params = dict(p)
        self.timeframes = tuple(str(t) for t in spec["timeframes"])
        self.signal_tf = str(spec["signal_tf"])
        if self.signal_tf not in self.timeframes or any(t not in TF_MS for t in self.timeframes):
            raise ValueError(f"{variant_id}: dilimler {self.timeframes} / {self.signal_tf}")
        win = dict(p["window"])
        if set(win) != set(self.timeframes) or any(int(v) < 3 for v in win.values()):
            raise ValueError(f"{variant_id}: pencere her dilim için olmalı: {win}")
        self.lookback = {str(k): int(v) for k, v in win.items()}
        self.step = int(TF_MS[self.signal_tf])
        self.max_hold = int(mh)
        self.sessions, self.kz = C.parse_sessions(cm["sessions_min"], cm["kz"])
        self.buffer = fnum(cm["buffer_frac"], "buffer_frac")
        self.stop_min = fnum(cm["stop_pct_min"], "stop_pct_min")
        self.stop_max = fnum(cm["stop_pct_max"], "stop_pct_max")
        self.atr_len = int(cm["atr_len"])
        self.atr_window = int(cm["atr_window"])
        self.per_day = int(cm["per_ny_day"])
        if not (0 < self.buffer < 0.01 and 0 < self.stop_min < self.stop_max < 0.1 and self.atr_window > self.atr_len
                and self.per_day >= 1):
            raise ValueError(f"{variant_id}: common değerleri aralık dışı")
        self.memo = Memo()
        self.setup(p)

    # ------------------------------------------------------------------ alt sınıfın yazdıkları
    def setup(self, p: Mapping[str, Any]) -> None:
        """Aileye özgü parametre doğrulaması (kapalı hata)."""

    def decide(self, bars: Mapping[str, Sequence[Mapping[str, Any]]], ctx: Mapping[str, Any]) -> Intent | None:
        raise NotImplementedError

    def rule_exit(self, pos: PosView, bars, ctx) -> str | None:
        return None

    # ------------------------------------------------------------------ çekirdek kancaları
    def bind_series(self, token: Any) -> None:
        self.memo.clear()

    def signals(self, bars, ctx) -> list[Intent]:
        if len(rows_of(bars, self.signal_tf)) < 3:
            return []
        if C.entries_on_day(ctx.get("history"), C.ny_day(int(ctx["now_ms"]))) >= self.per_day:
            return []
        it = self.decide(bars, ctx)
        return [it] if it is not None else []

    def manage(self, pos: PosView, bars, ctx) -> Action:
        why = self.rule_exit(pos, bars, ctx)
        return close(why) if why else NONE

    # ------------------------------------------------------------------ yardımcılar
    def atr_at(self, b: Bars, k: int) -> float:
        """k. barda biten sabit `atr_window` barlık penceredeki Wilder ATR (yetersiz geçmiş → NaN)."""
        s = k - self.atr_window + 1
        if s < 0 or k >= b.n:
            return float("nan")
        bar, anchor = b.rows[k], b.rows[s]
        got = self.memo.get(("ATR", b.ts[k]), bar, anchor)
        if got is MISS:
            v = ind.atr(b.h[s:k + 1], b.l[s:k + 1], b.c[s:k + 1], self.atr_len)[-1]
            got = self.memo.put(("ATR", b.ts[k]), bar, float(v), b.ts[0], anchor)
        return got

    def in_kz(self, t_ms: int) -> bool:
        return C.in_kz(t_ms, self.kz)

    def ref(self, close_px: float, *, limit: float | None = None, min_rr: float | None = None,
            target_beyond: bool = False) -> AgEntryRef:
        return AgEntryRef(price=close_px, limit_price=limit, stop_pct_min=self.stop_min, stop_pct_max=self.stop_max,
                          target_beyond_entry=target_beyond,
                          min_rr_after_cost=min_rr, rr_cost_frac=None if min_rr is None else 0.0)

    def intent(self, *, signal_ts: int, side: str, ref: AgEntryRef, stop: float, target: Target, evidence: Mapping[str, Any],
               exit_rule: str) -> Intent | None:
        if not (stop > 0 and math.isfinite(stop)):
            return None
        return Intent(signal_ts=int(signal_ts), side=side, entry_ref=ref, stop=float(stop), targets=(target,),
                      exit_rule=exit_rule, max_hold_bars=self.max_hold, one_entry_per_signal=True, risk_atr_bounds=None,
                      evidence={"family": self.FAMILY, **evidence})


def price_target(px: float) -> Target:
    return Target(TARGET_PRICE, float(px))


def r_target(r: float) -> Target:
    return Target(TARGET_R_FROM_ENTRY, float(r))


LIVE_MODULES: tuple[str, ...] = ()

__all__ = ["AgEntryRef", "AgVariant", "Bars", "COMMON_KEYS", "EXIT_KEYS", "LIVE_MODULES", "LONG", "MISS", "Memo", "SHORT",
           "SPEC_KEYS", "bearish_fvg", "bullish_fvg", "fnum", "index_at_or_after", "last_swing", "price_target",
           "r_target", "rows_of", "stop_beyond", "swing_high", "swing_low"]
