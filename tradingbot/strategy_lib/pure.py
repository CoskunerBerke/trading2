"""P3b-2 — saf sayısal ailelerin (B, C, D, E, F1, H) ortak tabanı (SAF: G/Ç, saat, ağ, rastgelelik yok; AST testi).

Parametreler bu pakette YAZILMAZ: varyant, mühürlü kaydın (`LIB_V1`) kendi satırından kurulur — `params` (anahtar kümesi
tam) ve `spec` = `{family, timeframes, signal_tf, sides, exits}` (motor tarafı `kernel.make_variant` verir; paket motoru
import etmez). `exits.stop` = `{"kind": "ATR", "atr_mult": m, "atr": "ATR14 (…)"}` → ilk stop sinyal kapanışından
m × ATR14 (sinyal dilimi; LIB varsayılanı "ATR = ATR14"); `exits.target_r` → GERÇEK girişten R hedefi (`Target`
`R_FROM_ENTRY`); `exits.max_hold_bars` → niyetin zaman stopu (sinyal barı; çekirdek sonraki açılışta kapatır). Beklenmeyen
yapı ValueError'dır (sessiz varsayılan yok).

Ortak okunuşlar (belge §8.4 P3b-2a notu):
* **Pencere** (`WINDOW`) = canlı kural çerçevesinin bir kurala verdiği kapanmış bar sayısı: gün içi dilimlerde
  `paper_rules.intraday_rows`'un 400 satırı (canlı D4 kuralının göstergelerini hesapladığı pencere), 1d'de perp
  çerçevesinin 399 kapanmış barı (`livebars.LIVE_CLOSED_BARS["1d"]`; canlı günlük göstergelerin hesaplandığı pencere);
  eşitlikler testli. EMA/ATR'nin `adjust=False` tohumu pencerenin başına bağlıdır: aynı pencere → canlı D4 ile bayt-aynı
  ATR; AUTO_v1 worker'ı aynı modülü aynı pencereyle çağırır.
* **LO** = yalnız long (sinyal tersine dönünce pozisyon yok); **LS** = iki yön. Çekirdek tek pozisyon tutar ve `signals`'ı
  yalnız pozisyonsuzken çağırır: ters yöne geçiş, çıkışın uygulandığı barın kapanışındaki `signals` ile olur (bir bar
  gecikme; durum sinyallerinde — TS, MAX, RSI2, fonlama — ters yön o an hâlâ geçerliyse alınır).
* **Kırılım** (DON, KEL, BBSQ) = kapanışın seviyeyi İLK kez geçmesi (önceki kapanış geçmemişti): canlı D4 kuralının
  (`donchian_trend._fresh_breakout`) tanımı; `DON_20_10_4h_LO ≡ K_D4` bunu bağlar.
* Ölçülemeyen gösterge (ısınma, hareketsiz pencere, fonlama yok/bayat) → giriş yok; açık pozisyonda kural çıkışı
  ölçülemiyorsa `NONE` (stop ve zaman stopu çekirdekte sürer; canlı `EXIT_UNMEASURABLE` gibi "tut").
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from . import indicators as ind
from .contract import (LONG, NONE, SHORT, TARGET_R_FROM_ENTRY, Action, EntryRef, Intent, PosView, Target, Variant,
                       check_params, close)

SIDES_LO, SIDES_LS = "LO", "LS"
SPEC_KEYS = ("family", "timeframes", "signal_tf", "sides", "exits")
#: kurala verilen kapanmış bar penceresi (gün içi: `paper_rules.intraday_rows` varsayılan `tail`; 1d:
#: `livebars.LIVE_CLOSED_BARS["1d"]`; eşitlikler testli — burada yazılır ki saf aileler `livebars`/`paper_rules`'ın
#: pandas/canlı modül yüklemesini taşımasın)
WINDOW: Mapping[str, int] = {"1h": 400, "4h": 400, "1d": 399}
ATR_LEN = 14


def _num(x: Any, what: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)):
        raise ValueError(f"{what} sonlu sayı olmalı: {x!r}")
    return float(x)


def parse_spec(variant_id: str, family: str, spec: Mapping[str, Any] | None, *,
               sides_allowed: Sequence[str] = (SIDES_LO, SIDES_LS)) -> dict[str, Any]:
    """Mühürlü varyant satırının çekirdeğe giden alanları; yapı denetimi kapalı hatadır (ValueError)."""
    if spec is None:
        raise ValueError(f"{variant_id}: mühürlü spec (family, timeframes, signal_tf, sides, exits) gerekli")
    if set(spec) != set(SPEC_KEYS):
        raise ValueError(f"{variant_id}: spec anahtarları {sorted(SPEC_KEYS)} olmalı, gelen {sorted(spec)}")
    if spec["family"] != family:
        raise ValueError(f"{variant_id}: aile {family} olmalı, gelen {spec['family']!r}")
    tfs = list(spec["timeframes"])
    stf = str(spec["signal_tf"])
    if tfs != [stf] or stf not in WINDOW:
        raise ValueError(f"{variant_id}: tek sinyal dilimi ({sorted(WINDOW)}) bekleniyor: {tfs} / {stf}")
    sides = spec["sides"]
    if sides not in tuple(sides_allowed):
        raise ValueError(f"{variant_id}: yön {tuple(sides_allowed)} olmalı: {sides!r}")
    ex = spec["exits"]
    if set(ex) != {"stop", "exit_rule", "target_r", "max_hold_bars"}:
        raise ValueError(f"{variant_id}: exits anahtarları beklenmiyor: {sorted(ex)}")
    st = ex["stop"]
    if not isinstance(st, Mapping) or st.get("kind") != "ATR" or not str(st.get("atr", "")).startswith(f"ATR{ATR_LEN} "):
        raise ValueError(f"{variant_id}: stop ATR{ATR_LEN} katı olmalı: {st!r}")
    mult = _num(st.get("atr_mult"), f"{variant_id}: stop atr_mult")
    if mult <= 0:
        raise ValueError(f"{variant_id}: stop atr_mult > 0 olmalı")
    tr = ex["target_r"]
    target_r = None if tr is None else _num(tr, f"{variant_id}: target_r")
    if target_r is not None and target_r <= 0:
        raise ValueError(f"{variant_id}: target_r > 0 olmalı")
    mh = ex["max_hold_bars"]
    if isinstance(mh, bool) or not isinstance(mh, int) or mh < 1:
        raise ValueError(f"{variant_id}: max_hold_bars ≥ 1 tam sayı olmalı: {mh!r}")
    return {"signal_tf": stf, "sides": sides, "stop_atr_mult": mult, "target_r": target_r, "max_hold_bars": int(mh),
            "exit_rule": ex["exit_rule"]}


class Cols:
    """Kapanmış barların sütunları (bir karar anı için; girdiler değiştirilmez)."""

    __slots__ = ("ts", "high", "low", "close")

    def __init__(self, rows: Sequence[Mapping[str, Any]]):
        self.ts = [int(r["timestamp"]) for r in rows]
        self.high = [float(r["high"]) for r in rows]
        self.low = [float(r["low"]) for r in rows]
        self.close = [float(r["close"]) for r in rows]

    def __len__(self) -> int:
        return len(self.close)


class PureVariant(Variant):
    """Saf ailelerin tabanı. Alt sınıf `FAMILY`, `PARAM_KEYS`, `EXIT_RULE`'u ve `entry(cols, ctx)` /
    `exit_reason(pos, cols, ctx)`'i yazar."""

    FAMILY = ""
    PARAM_KEYS: tuple[str, ...] = ()
    EXIT_RULE = ""
    SIDES_ALLOWED: tuple[str, ...] = (SIDES_LO, SIDES_LS)
    live_modules: tuple[str, ...] = ()

    def __init__(self, variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None):
        p = check_params(variant_id, params, self.PARAM_KEYS)
        s = parse_spec(variant_id, self.FAMILY, spec, sides_allowed=self.SIDES_ALLOWED)
        self.variant_id = variant_id
        self.family = self.FAMILY
        self.params = dict(p)
        self.signal_tf = s["signal_tf"]
        self.timeframes = (self.signal_tf,)
        self.sides = s["sides"]
        self.allow_short = self.sides == SIDES_LS
        self.stop_atr_mult = s["stop_atr_mult"]
        self.target_r = s["target_r"]
        self.max_hold = s["max_hold_bars"]
        self.spec_exit_rule = s["exit_rule"]
        self.lookback = {self.signal_tf: WINDOW[self.signal_tf]}
        self.setup(p)

    # ---------------------------------------------------------------- alt sınıfın yazdıkları
    def setup(self, p: Mapping[str, Any]) -> None:
        """Parametre doğrulaması ve türetilmiş sabitler (ValueError: kapalı hata)."""

    def entry(self, cols: Cols, ctx: Mapping[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """Pozisyonsuzken son kapanmış barda giriş yönü ve kanıt (kanıtta `atr14` varsa stop onu kullanır); yoksa None."""
        raise NotImplementedError

    def exit_reason(self, pos: PosView, cols: Cols, ctx: Mapping[str, Any]) -> str | None:
        """Açık pozisyonda kural çıkışının nedeni (sonraki açılışta piyasa); yoksa ya da ölçülemezse None."""
        return None

    # ---------------------------------------------------------------- sözleşme
    def signals(self, bars, ctx) -> list[Intent]:
        rows = bars.get(self.signal_tf) or ()
        if len(rows) < 2:
            return []
        cols = Cols(rows)
        got = self.entry(cols, ctx)
        if got is None:
            return []
        side, evidence = got
        if side == SHORT and not self.allow_short:
            return []
        it = self.make_intent(cols, side, evidence, atr14=evidence.get("atr14"))
        return [it] if it is not None else []

    def manage(self, pos: PosView, bars, ctx) -> Action:
        rows = bars.get(self.signal_tf) or ()
        if len(rows) < 2:
            return NONE
        why = self.exit_reason(pos, Cols(rows), ctx)
        return close(why) if why else NONE

    def make_intent(self, cols: Cols, side: str, evidence: Mapping[str, Any], *, atr14: float | None = None) -> Intent | None:
        """Son kapanmış barın kapanışından m × ATR14 stop; R hedefi GERÇEK girişten; zaman stopu mühürlü. `atr14`: aynı
        pencerede zaten hesaplanmış son ATR14 (yoksa burada hesaplanır)."""
        a = ind.atr(cols.high, cols.low, cols.close, ATR_LEN)[-1] if atr14 is None else float(atr14)
        c = cols.close[-1]
        if not (a == a and a > 0.0 and c > 0.0):
            return None
        stop = c - self.stop_atr_mult * a if side == LONG else c + self.stop_atr_mult * a
        if not stop > 0.0:
            return None
        targets = (Target(TARGET_R_FROM_ENTRY, self.target_r),) if self.target_r else ()
        return Intent(signal_ts=cols.ts[-1], side=side, entry_ref=EntryRef(price=c), stop=stop, targets=targets,
                      exit_rule=self.EXIT_RULE, max_hold_bars=self.max_hold, one_entry_per_signal=False,
                      risk_atr_bounds=None, evidence={"family": self.FAMILY, "atr14": a, **evidence})


def fresh_above(c: Sequence[float], lvl_now: float, lvl_prev: float) -> bool:
    """Kapanış seviyeyi İLK kez yukarı geçti: c[-1] > seviye ve c[-2] ≤ önceki barın seviyesi (NaN → hayır)."""
    if lvl_now != lvl_now or lvl_prev != lvl_prev:
        return False
    return c[-1] > lvl_now and c[-2] <= lvl_prev


def fresh_below(c: Sequence[float], lvl_now: float, lvl_prev: float) -> bool:
    if lvl_now != lvl_now or lvl_prev != lvl_prev:
        return False
    return c[-1] < lvl_now and c[-2] >= lvl_prev


__all__ = ["ATR_LEN", "Cols", "PureVariant", "SIDES_LO", "SIDES_LS", "SPEC_KEYS", "WINDOW", "fresh_above", "fresh_below",
           "parse_spec"]
