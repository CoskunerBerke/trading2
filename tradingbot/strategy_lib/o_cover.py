"""P3b-2b — O ailesi (örtüler, bir seferde bir faktör): ortak çerçeve (SAF; docs/ENGINE_BRAIN_V1.md §2.2 satır O).

Mühürlü satır: taban {T2, M2, BOX, D4, C4} × {F_ER, F_BTC, F_VOL, X_TRAIL2, X_CHAN3, X_TIME, X_TPL} = 35 varyant;
`O_<taban>_<faktör>`; faktör parametreleri varyantın `params.factor_params`'ındadır (yani `spec_sha`'da), uygulama
okunuşları mühürlü teknik okumalardır (`LIB_V1.covers.reading`, `covers.factors.<F>.reading`; 2026-10-12, P3b-2b).

* **Taban** canlı kopyanın kendisidir (`k_trend`, `k_box`, `k_d4`; C4'te `k_c4.CandleBook` = C4 defteri, 8 varyasyon
  config sırasıyla, ilk eşleşen) ve mühürlü parametreleriyle kurulur (çekirdek `spec["base_params"]` verir). Taban
  paylaşılan akışla sarılır (`stream.SharedBase`): tabanın `signals`/`manage`'i ailede (taban + 7 örtü) karar anı başına
  bir kez hesaplanır; tek başına kurulan örtünün akışı yalnız kendisine aittir.
* **Tam olarak bir faktör.** Filtre (`F_*`): tabanın niyeti, faktörün koşulu karar anında KAPANMIŞ verilerle (≤ karar
  anı: `bars` ve `ctx`'in BTC 4h barları — çekirdek yalnız `timestamp + dilim ≤ now` barları verir) sağlanırsa aynen
  geçer; sağlanmazsa o bar giriş yok. Açık pozisyonda filtre örtüsü tabanın `manage`'ini aynen döndürür. Çıkış örtüsü
  (`X_*`): giriş tabanınkidir (niyet yalnız örtünün çıkış alanıyla değişir: `X_TIME` zaman stopu, `X_TPL` hedefler);
  açık pozisyonda önce tabanın `manage`'i — CLOSE ise o (tabanın çıkışı önceliklidir) — sonra örtünün eylemi: stop YALNIZ
  çekirdeğin doğrulanmış `MOVE_STOP`'uyla sıkılaştırılır (aday mevcut stopu sıkılaştırmıyorsa eylem yok); aday karar
  fiyatının (karar barının kapanışı) ötesindeyse — iz/chandelier delinmiş; canlı defterde fiyatın ötesine konan stop ilk
  gözlemde dolar — sonraki açılışta piyasa çıkışı (CLOSE). Tabanın bilgi işaretleri (`Action.flags`) korunur.
* Göstergeler `strategy_lib.indicators`'tandır (ATR = Wilder, ER = Kaufman, yüzdelik sıra = `pct_rank`); pencere
  tabanınkidir (canlı çerçeve: 1d 399, 4h 699, 5m 499 kapanmış bar).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .contract import ACT_CLOSE, LONG, NONE, Action, Intent, PosView, Variant, check_params, close, move_stop
from .stream import SharedBase

PARAM_KEYS = ("base", "factor", "factor_params")
SPEC_KEYS = ("family", "timeframes", "signal_tf", "sides", "exits", "base", "base_variants", "base_params", "base_max_hold")
BASES = ("T2", "M2", "BOX", "D4", "C4")
FACTORS = ("F_ER", "F_BTC", "F_VOL", "X_TRAIL2", "X_CHAN3", "X_TIME", "X_TPL")


class Factor:
    """Tek faktör. Alt sınıf `NAME`, `KIND` ("filter" | "exit") ve `PARAM_KEYS`'i (mühürlü `factor_params`'ın `kind`
    dışındaki anahtarları) yazar; `setup` parametreleri doğrular (kapalı hata)."""

    NAME = ""
    KIND = ""
    PARAM_KEYS: tuple[str, ...] = ()
    #: `ctx["btc_4h_bars"]` gerekli mi (F_BTC) ve kaç bar
    needs_btc_4h = False
    btc_4h_lookback = 0

    def __init__(self, cover: "Cover", fp: Mapping[str, Any]):
        self.cover = cover
        got = dict(fp)
        want = set(self.PARAM_KEYS) | {"kind"}
        if set(got) != want:
            raise ValueError(f"{cover.variant_id}: faktör parametreleri {sorted(want)} olmalı, gelen {sorted(got)}")
        if got["kind"] != self.KIND:
            raise ValueError(f"{cover.variant_id}: faktör türü {self.KIND} olmalı, gelen {got['kind']!r}")
        self.fp = got
        self.setup(got)

    def setup(self, fp: Mapping[str, Any]) -> None:
        """Parametre doğrulaması (kapalı hata: ValueError)."""

    def on_intent(self, it: Intent, bars, ctx) -> Intent | None:
        """Filtre: niyet geçerse aynen döner, geçmezse None. Çıkış örtüsü: niyetin çıkış alanı değişmiş hali."""
        return it

    def on_manage(self, base: Action, pos: PosView, bars, ctx) -> Action:
        """Açık pozisyonda (tabanın eylemi CLOSE değilken) örtünün eylemi; filtrede tabanınki."""
        return base


class Cover(Variant):
    """`O_<taban>_<faktör>`: taban kopya + tek faktör (modül belgesi)."""

    family = "O"

    def __init__(self, variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Variant,
                 factor_cls: type[Factor]):
        p = check_params(variant_id, params, PARAM_KEYS)
        if spec is None or set(spec) != set(SPEC_KEYS):
            raise ValueError(f"{variant_id}: spec anahtarları {sorted(SPEC_KEYS)} olmalı")
        if spec["family"] != "O" or p["base"] != spec["base"] or p["base"] not in BASES:
            raise ValueError(f"{variant_id}: O ailesi ve taban {BASES} olmalı")
        if p["factor"] != factor_cls.NAME or variant_id != f"O_{p['base']}_{p['factor']}":
            raise ValueError(f"{variant_id}: faktör {factor_cls.NAME} ve kimlik O_<taban>_<faktör> olmalı")
        self.variant_id = variant_id
        self.params = dict(p)
        self.base_name = str(p["base"])
        self.shared = base if isinstance(base, SharedBase) else SharedBase(base)
        b = self.shared
        if list(spec["timeframes"]) != list(b.timeframes) or spec["signal_tf"] != b.signal_tf:
            raise ValueError(f"{variant_id}: dilimler tabanınki olmalı: {spec['timeframes']} / {spec['signal_tf']}")
        self.signal_tf = b.signal_tf
        self.timeframes = tuple(b.timeframes)
        self.lookback = dict(b.lookback)
        self.needs_btc, self.btc_lookback = b.needs_btc, b.btc_lookback
        self.needs_funding, self.funding_lookback_ms = b.needs_funding, b.funding_lookback_ms
        self.spec = dict(spec)
        self.factor = factor_cls(self, p["factor_params"])
        self.needs_btc_4h = self.factor.needs_btc_4h
        self.btc_4h_lookback = self.factor.btc_4h_lookback

    # ------------------------------------------------------------------ çekirdek kancaları
    def bind_series(self, token: Any) -> None:
        self.shared.bind_series(token)

    @property
    def fast(self) -> bool:
        return self.shared.fast

    @fast.setter
    def fast(self, v: bool) -> None:
        self.shared.fast = v

    # ------------------------------------------------------------------ sözleşme
    def signals(self, bars, ctx) -> list[Intent]:
        out = []
        for it in self.shared.signals(bars, ctx):
            got = self.factor.on_intent(it, bars, ctx)
            if got is not None:
                out.append(got)
        return out

    def manage(self, pos: PosView, bars, ctx) -> Action:
        base = self.shared.manage(pos, bars, ctx)
        if base.kind == ACT_CLOSE or self.factor.KIND != "exit" or base.kind != "NONE":
            return base
        act = self.factor.on_manage(base, pos, bars, ctx)
        if base.flags and act is not base:
            act = Action(act.kind, act.reason, act.price, tuple(dict.fromkeys(tuple(base.flags) + tuple(act.flags))))
        return act


# ============================================================================ ortak yardımcılar
def rows_of(bars: Mapping[str, Sequence[Mapping[str, Any]]], tf: str) -> Sequence[Mapping[str, Any]]:
    return bars.get(tf) or ()


def hlc(rows: Sequence[Mapping[str, Any]]) -> tuple[list[float], list[float], list[float]]:
    return ([float(r["high"]) for r in rows], [float(r["low"]) for r in rows], [float(r["close"]) for r in rows])


def best_since_entry(pos: PosView, rows: Sequence[Mapping[str, Any]]) -> float | None:
    """Girişten bu yana (giriş barı dahil, karar barına kadar) kapanmış barların en iyi ucu: LONG en yüksek, SHORT en
    düşük; pencerede böyle bar yoksa None."""
    t0 = int(pos.entry_bar_ts)
    xs = [float(r["high"] if pos.side == LONG else r["low"]) for r in rows if int(r["timestamp"]) >= t0]
    if not xs:
        return None
    return max(xs) if pos.side == LONG else min(xs)


def stop_overlay(pos: PosView, rows: Sequence[Mapping[str, Any]], cand: float | None, reason: str) -> Action:
    """Aday stop → eylem: ölçülemez → NONE; karar fiyatının (son kapanmış barın kapanışı) ötesinde → CLOSE (sonraki
    açılış); mevcut stopu sıkılaştırıyorsa MOVE_STOP (çekirdek yalnız sıkılaştırır ve fiyatı geçemez); değilse NONE."""
    if cand is None or not rows or not math.isfinite(cand) or cand <= 0:
        return NONE
    px = float(rows[-1]["close"])
    if pos.side == LONG:
        if cand >= px:
            return close(reason + "_BREACHED")
        return move_stop(cand, reason) if cand > float(pos.stop) else NONE
    if cand <= px:
        return close(reason + "_BREACHED")
    return move_stop(cand, reason) if cand < float(pos.stop) else NONE


def make_cover(variant_id: str, params: Mapping[str, Any], spec: Mapping[str, Any] | None, base: Variant,
               factor_cls: type[Factor]) -> Cover:
    return Cover(variant_id, params, spec, base, factor_cls)


__all__ = ["BASES", "Cover", "FACTORS", "Factor", "PARAM_KEYS", "SPEC_KEYS", "best_since_entry", "hlc", "make_cover",
           "rows_of", "stop_overlay"]
