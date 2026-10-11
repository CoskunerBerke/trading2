"""P3b — `strategy_lib` sözleşme tipleri (docs/ENGINE_BRAIN_V1.md §2.1). SAF: G/Ç, saat, ağ, rastgelelik yok (AST testi).

* `bars` = `{tf: kapanmış bar dizisi}`; bar = `{"timestamp": açılış ms, "open", "high", "low", "close", "volume"?}`,
  kronolojik, yalnız `ctx["now_ms"]` anında KAPANMIŞ barlar (timestamp + tf ≤ now_ms). Diziler değiştirilmez.
* `ctx` = `{"now_ms", "btc_bars"?, "funding"?}` (§2.1). P3b-1 okunuşu (belge §8.4 P3b-1 notu): canlı kuralın kendi
  defter geçmişine baktığı yerler (ortak yapı katmanının "kullanılmış yapı" kümesi, `one_entry_per_signal`) için çekirdek
  iki SALT-OKUNUR anahtar ekler: `"symbol"` (yapı analizinin kimliği) ve `"history"` (bu varyantın bu sembolde açtığı
  girişler, `BookEntry` demeti). Başka anahtar yoktur.
* `signals(bars, ctx) -> list[Intent]` yalnız pozisyon yokken, `manage(pos, bars, ctx) -> Action` yalnız pozisyon
  varken ve yalnız kapanmış barda çağrılır (§2.1).
* `Intent` alanları §2.1'in dokuzudur; `evidence` yalnız bilgi taşır (canlı eylemin kanıt anlık görüntüsü: yapı kaydı,
  varyasyon kimliği…): çekirdek onu hiçbir karara sokmaz, yalnız pozisyona/geçmişe geri verir.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

LONG, SHORT = "LONG", "SHORT"
SIDES = (LONG, SHORT)

#: dilim → milisaniye (yalnız bu paketin kullandığı dilimler)
TF_MS: Mapping[str, int] = MappingProxyType({"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000,
                                            "1d": 86_400_000})

#: hedef türleri: mutlak fiyat ya da GERÇEK girişten R katı (`apply_action.target_r_from_entry` ile aynı anlam)
TARGET_PRICE, TARGET_R_FROM_ENTRY = "PRICE", "R_FROM_ENTRY"
#: eylem türleri (§2.1)
ACT_NONE, ACT_CLOSE, ACT_MOVE_STOP = "NONE", "CLOSE", "MOVE_STOP"
#: kopyaların çıkış kuralı etiketi: çıkış canlı kural fonksiyonunun `manage` cevabıdır
EXIT_LIVE_RULE = "LIVE_RULE"

_EMPTY: Mapping[str, Any] = MappingProxyType({})


def _finite(x: Any, name: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{name} sayı olmalı: {x!r}") from None
    if not math.isfinite(v):
        raise ValueError(f"{name} sonlu olmalı: {x!r}")
    return v


@dataclass(frozen=True)
class EntryRef:
    """Girişin referansı ve girişte (dolum fiyatı bilinince) uygulanan kabul sınırları.

    `price`: kuralın giriş referansı (sinyal kapanışı). `max_drift_pct`: `apply_action`'ın giriş kayması kapısı
    (|giriş − price| / price × 100 ≤ değer; None = kapalı). `anchor` + `max_anchor_dist`: kovalama sınırı (|giriş − anchor|
    ≤ max_anchor_dist; Formasyon v3'te tetik seviyesi ve 1 × ATR). `min_rr_after_cost` + `rr_cost_frac`: Formasyon
    `PatternBook._try_open`'ın saf geometri kapısı `RR_BELOW_MIN_AT_ENTRY` (ilk hedef GERÇEK girişten çözülür; maliyet
    sonrası R/R canlı `pattern_trader.strategy.rr_after_cost(giriş, stop, hedef, cost_frac)` ile ölçülür; ikisi birlikte
    verilir ya da hiç). Hepsi dolumdan ÖNCEKİ referans fiyata uygulanır."""

    price: float
    max_drift_pct: float | None = None
    anchor: float | None = None
    max_anchor_dist: float | None = None
    min_rr_after_cost: float | None = None
    rr_cost_frac: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _finite(self.price, "entry_ref.price"))
        for k in ("max_drift_pct", "anchor", "max_anchor_dist", "min_rr_after_cost", "rr_cost_frac"):
            v = getattr(self, k)
            if v is not None:
                object.__setattr__(self, k, _finite(v, f"entry_ref.{k}"))
        if (self.min_rr_after_cost is None) != (self.rr_cost_frac is None):
            raise ValueError("entry_ref.min_rr_after_cost ve rr_cost_frac birlikte verilir")
        if self.rr_cost_frac is not None and self.rr_cost_frac < 0:
            raise ValueError("entry_ref.rr_cost_frac negatif olamaz")


@dataclass(frozen=True)
class Target:
    kind: str
    value: float

    def __post_init__(self) -> None:
        if self.kind not in (TARGET_PRICE, TARGET_R_FROM_ENTRY):
            raise ValueError(f"hedef türü {TARGET_PRICE}/{TARGET_R_FROM_ENTRY} olmalı: {self.kind!r}")
        v = _finite(self.value, "target.value")
        if v <= 0:
            raise ValueError("hedef değeri pozitif olmalı")
        object.__setattr__(self, "value", v)


@dataclass(frozen=True)
class RiskAtrBounds:
    """Test edilmiş risk aralığı: lo × atr < |giriş − stop| ≤ hi × atr (`apply_action` ile aynı eşitsizlik)."""

    lo: float
    hi: float
    atr: float

    def __post_init__(self) -> None:
        for k in ("lo", "hi", "atr"):
            object.__setattr__(self, k, _finite(getattr(self, k), f"risk_atr_bounds.{k}"))


@dataclass(frozen=True)
class Intent:
    """§2.1 niyeti: `{signal_ts, side, entry_ref, stop, targets[], exit_rule, max_hold_bars, one_entry_per_signal,
    risk_atr_bounds}`. `signal_ts` = sinyal barının AÇILIŞ ms'i; giriş sonraki barın açılışındadır (çekirdek)."""

    signal_ts: int
    side: str
    entry_ref: EntryRef
    stop: float
    targets: tuple[Target, ...] = ()
    exit_rule: str = EXIT_LIVE_RULE
    max_hold_bars: int | None = None
    one_entry_per_signal: bool = False
    risk_atr_bounds: RiskAtrBounds | None = None
    evidence: Mapping[str, Any] = field(default=_EMPTY, compare=False)

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise ValueError(f"yön {SIDES} olmalı: {self.side!r}")
        if isinstance(self.signal_ts, bool) or not isinstance(self.signal_ts, int):
            object.__setattr__(self, "signal_ts", int(self.signal_ts))
        stop = _finite(self.stop, "stop")
        if stop <= 0:
            raise ValueError("stop pozitif olmalı")
        object.__setattr__(self, "stop", stop)
        object.__setattr__(self, "targets", tuple(self.targets))
        if self.max_hold_bars is not None and int(self.max_hold_bars) < 1:
            raise ValueError("max_hold_bars ≥ 1 olmalı")
        if not isinstance(self.evidence, MappingProxyType):
            object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence or {})))


@dataclass(frozen=True)
class Action:
    """§2.1 eylemi: NONE | CLOSE(reason) | MOVE_STOP(price). Yalnız kapanmış barda üretilir; çekirdek CLOSE'u sonraki
    barın açılışında uygular. MOVE_STOP yalnız sıkılaştırır ve kararın anındaki fiyatı geçemez; aksi istek çekirdekte
    uygulanmaz ve işleme `MOVE_STOP_IGNORED` yazılır (`kernel.move_stop_verdict`)."""

    kind: str = ACT_NONE
    reason: str = ""
    price: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in (ACT_NONE, ACT_CLOSE, ACT_MOVE_STOP):
            raise ValueError(f"eylem türü geçersiz: {self.kind!r}")
        if self.kind == ACT_MOVE_STOP:
            p = _finite(self.price, "move_stop.price")
            if p <= 0:
                raise ValueError("MOVE_STOP fiyatı pozitif olmalı")
            object.__setattr__(self, "price", p)


NONE = Action()


def close(reason: str) -> Action:
    return Action(ACT_CLOSE, str(reason or "RULE_EXIT"))


def move_stop(price: float, reason: str = "MOVE_STOP") -> Action:
    return Action(ACT_MOVE_STOP, str(reason), price)


@dataclass(frozen=True)
class PosView:
    """`manage`'a verilen açık pozisyon görünümü (salt-okunur). `opened_ms` = dolum anı (giriş barının açılışı)."""

    side: str
    entry: float
    stop: float
    initial_stop: float
    opened_ms: int
    signal_ts: int
    entry_bar_ts: int
    bars_held: int
    intent: Intent


@dataclass(frozen=True)
class BookEntry:
    """`ctx["history"]` öğesi: varyantın bu sembolde açtığı bir giriş (açık ya da kapanmış)."""

    symbol: str
    side: str
    signal_ts: int
    opened_ms: int
    evidence: Mapping[str, Any] = field(default=_EMPTY, compare=False)


class Variant:
    """Bir LIB varyantı. Alt sınıflar `signals` ve `manage`'i saf fonksiyon olarak yazar."""

    variant_id: str = ""
    family: str = ""
    signal_tf: str = ""
    timeframes: tuple[str, ...] = ()
    #: dilim → çekirdeğin `bars[tf]` olarak verdiği en çok kapanmış bar sayısı (canlı çerçeve penceresi)
    lookback: Mapping[str, int] = _EMPTY
    #: `ctx["btc_bars"]` (BTC kapanmış 1d barları) gerekli mi ve kaç bar (canlı çerçeve penceresi)
    needs_btc: bool = False
    btc_lookback: int = 0
    #: kimliğe giren BİLDİRİLMİŞ canlı modüller (`identity.module_src_sha`: varyant modülü + paket içi bağımlılıkları + bu
    #: liste; her modülün gerekçesi kopya modülünün `LIVE_MODULES` yorumundadır)
    live_modules: tuple[str, ...] = ()
    params: Mapping[str, Any] = _EMPTY

    def signals(self, bars: Mapping[str, Sequence[Mapping[str, Any]]], ctx: Mapping[str, Any]) -> list[Intent]:
        raise NotImplementedError

    def manage(self, pos: PosView, bars: Mapping[str, Sequence[Mapping[str, Any]]], ctx: Mapping[str, Any]) -> Action:
        raise NotImplementedError


def check_params(variant_id: str, params: Mapping[str, Any], allowed: Sequence[str]) -> dict[str, Any]:
    """LIB parametrelerinin anahtar kümesi TAM olmalı (bilinmeyen/eksik anahtar = ValueError; sessiz varsayılan yok)."""
    got = dict(params or {})
    if set(got) != set(allowed):
        raise ValueError(f"{variant_id}: parametre anahtarları {sorted(allowed)} olmalı, gelen {sorted(got)}")
    return got


__all__ = ["ACT_CLOSE", "ACT_MOVE_STOP", "ACT_NONE", "Action", "BookEntry", "EXIT_LIVE_RULE", "EntryRef", "Intent", "LONG",
           "NONE", "PosView", "RiskAtrBounds", "SHORT", "SIDES", "TARGET_PRICE", "TARGET_R_FROM_ENTRY", "TF_MS", "Target",
           "Variant", "check_params", "close", "move_stop"]
