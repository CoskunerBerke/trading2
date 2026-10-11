"""P3b — araştırma çekirdeği (docs/ENGINE_BRAIN_V1.md §2.1, §3.3; mühür `WF_PROTOCOL_V1.kernel`, `LIB_V1.sizing`).

Bir `strategy_lib` varyantını tek bir sembolün kapanmış barlarında oynatır; ağsız, saatsiz, belirlenimci (aynı girdi →
bayt-aynı sonuç; test). Kurallar:

* **Karar bar kapanışında**, yalnız o anda KAPANMIŞ barlarla (`timestamp + tf ≤ now_ms`); pozisyon yokken `signals`,
  varken `manage`. Bar başına tek eylem (canlı turdaki gibi). **Dolum sonraki barın açılışında ± kayma.**
* **Giriş kapıları** (canlı `apply_action` / `PatternBook._try_open` sırasıyla, dolumdan önceki açılış fiyatıyla): giriş
  kayması (`EntryRef.max_drift_pct`), stop doğru tarafta (`STRATEGY_BAD_STOP`), kovalama sınırı (`CHASE_LIMIT`), test
  edilmiş risk aralığı (`RISK_OUTSIDE_TESTED_RANGE`), aynı sinyalle tek giriş. R'den hedef GİRİŞTEN ölçülür.
* **Bar içi yol** `learning_cf` kuralıdır (`learning_cf.INTRABAR_PATH`): açılış → ters uç → lehte uç → kapanış; her
  nokta ayrı bir gözlemdir, bir noktanın taşıdığı stop (TP1 başa-baş) yalnız SONRAKİ noktalarla sınanır. Giriş barında
  yol ters uçtan başlar (açılış girişin kendisidir). **Stop seviyede** dolar (+ çıkış kayması); yalnız bar stopun ötesinde
  AÇILIRSA dolum açılıştandır (boşluk). Hedef seviyede dolar (kayma yok, taker ücret; defter `tick` ile aynı); birden çok
  hedefte ilk hedef yarısını kapatır ve stop ücret+kayma dahil başa-başa çekilir (`FuturesLedgerV2.break_even_price`
  formülü). Kural CLOSE'u ve zaman stopu sonraki açılışta piyasa emriyle; aynı açılışta önce açılış gözlemi (boşluk) işlenir.
* **Fonlama** deponun GERÇEK uzlaşma zaman damgalarında (`SeriesData.funding` = `[(ts, rate, mark)]`, depodaki
  `funding_frame` biçimi): (giriş, çıkış] aralığındaki her satır için −yön × açık miktar × mark × oran; açık miktar o andan
  ÖNCEKİ dolumlardan (`accounting.funding.qty_open_at` sözleşmesi). Açılış noktasında kapanan pozisyonun çıkış anı barın
  açılışı, diğer noktalarda barın kapanışıdır (`learning_cf` yolunun zaman damgaları). Seri pencereyi örtmüyorsa ya da
  pencerede serinin kendi tipik aralığının 1,5 katından uzun bir boşluk / okunamaz satır varsa işlem `FUNDING_GAP`
  işaretlenir (kapı istatistiğinden çıkar; sayısı gösterilir, §3.3).
* **Büyüklük** sabit 1 USDT risk (200 USDT × %0,5; `LIB_V1.sizing`), canlı `learning_mode.fit_size` ile: kaldıraç
  büyüklükten türer (gerektiği kadar), likidasyon mesafesi ≥ `min_liq_distance_stop_mult` (2) × stop; likidasyon fiyatı
  defterin **bracket formülü** (`accounting.liquidation_price` + `bracket_for`); gerçek bracket'la mesafe tutmazsa
  kaldıraç düşürülür. Likidasyon açılış boşluğunda (ya da kayma dolumu likidasyonun ötesine iterse) olur; sonuç defterin
  `liquidation_outcome` aritmetiğidir (izole marjla sınırlı kayıp + %0,5 likidasyon ücreti).
* **Üç maliyet senaryosu** (`WF_PROTOCOL_V1.thresholds.costs`: base / ADVERSE / STRESS) AYNI yol üzerinde aritmetik
  yeniden maliyetlendirmeyle: giriş = açılış × (1 + yön × kayma) + yön × spread_tick × tick; piyasa çıkışı = referans ×
  (1 − yön × kayma) − yön × spread_tick × tick; hedef çıkışı = seviye − yön × spread_tick × tick (kayma yok); ücret =
  taker % × her bacağın notional'ı; fonlama her senaryoda aynı (gerçek). R = net / (|giriş dolumu − ilk stop| × miktar)
  (defter `TradeRecord.r_multiple` tanımı). Yol (çıkış olayları, kaldıraç, likidasyon fiyatı) base senaryosundan gelir.

Parite (a) bu çekirdeği `FuturesLedgerV2` + `strategy_paper.apply_action` + `tick`'e karşı sınar (`parity.py`).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from . import registry as RG
from ..accounting import LiquidationParams, bracket_for, liquidation_price
from ..learning_mode import fit_size
from ..strategy_lib import (ACT_CLOSE, ACT_MOVE_STOP, LONG, TARGET_PRICE, TF_MS, BookEntry, Intent, PosView, Variant,
                            catalog as SLC, identity as SLI)

KERNEL_VERSION = "kernel_v1"
SCENARIOS: tuple[str, ...] = ("base", "ADVERSE", "STRESS")
_COSTS = RG.WF_PROTOCOL_V1["thresholds"]["costs"]
SIZING = RG.LIB_V1["sizing"]
MIN_LIQ_MULT = float(RG.LIB_V1["thresholds"]["sizing"]["min_liq_distance_stop_mult"])
#: defterin varsayılan ilk-hedef oranı (`FuturesLedgerV2.tp1_fraction`) ve likidasyon ücreti (`LiquidationParams`)
TP1_FRACTION = 0.5
LIQ_FEE_PCT = float(LiquidationParams().liq_fee_pct)
#: fonlama boşluğu: serinin tipik aralığının bu katından uzun ara
FUNDING_GAP_FACTOR = 1.5

# çıkış türleri
EXIT_STOP, EXIT_TARGET, EXIT_RULE, EXIT_TIME, EXIT_LIQ, EXIT_END = "STOP", "TARGET", "RULE", "TIME", "LIQ", "END"
# işaretler
FLAG_FUNDING_GAP, FLAG_OPEN_AT_END = "FUNDING_GAP", "OPEN_AT_END"
# giriş retleri
REJ_DRIFT, REJ_BAD_STOP, REJ_CHASE, REJ_RISK_RANGE, REJ_SIZE = ("ENTRY_DRIFT", "STRATEGY_BAD_STOP", "CHASE_LIMIT",
                                                                 "RISK_OUTSIDE_TESTED_RANGE", "SIZE_REJECTED")


@dataclass(frozen=True)
class CostScenario:
    name: str
    taker_fee_pct: float
    slippage_bps: float
    spread_ticks: float

    @property
    def fee(self) -> float:
        return self.taker_fee_pct / 100.0

    @property
    def slip(self) -> float:
        return self.slippage_bps / 10_000.0


def scenario(name: str) -> CostScenario:
    c = _COSTS[name]
    if int(c["fee_sides"]) != 2:
        raise ValueError("maliyet senaryosu iki taraflı ücret varsayar")
    return CostScenario(name, float(c["taker_fee_pct"]), float(c["slippage_bps"]), float(c["spread_ticks"]))


@dataclass(frozen=True)
class SeriesData:
    """Bir sembolün girdisi. `bars[tf]`: kapanmış barlar, kronolojik (`timestamp` = açılış ms). `btc_1d`: BTC 1d
    barları (rejim isteyen kopyalar). `funding`: `(uzlaşma ms, oran, mark)` satırları (deponun gerçek damgaları); None =
    fonlama verisi yok (her işlem `FUNDING_GAP`). `price_tick`: serinin fiyat adımı (ADVERSE/STRESS spread'i)."""

    symbol: str
    bars: Mapping[str, Sequence[Mapping[str, Any]]]
    btc_1d: Sequence[Mapping[str, Any]] | None = None
    funding: Sequence[tuple[int, float, float]] | None = None
    price_tick: float = 0.0


@dataclass(frozen=True)
class Leg:
    """Bir çıkış bacağı (yol olayı). `ref`: maliyetten önceki referans fiyat; `frac`: ilk miktarın payı; `market`:
    kayma uygulanır mı (stop/kural/zaman/son); `ts`: fonlama için çıkış anı."""

    kind: str
    ref: float
    frac: float
    market: bool
    ts: int
    point: str
    reason: str = ""


@dataclass
class KernelTrade:
    symbol: str
    variant_id: str
    side: str
    signal_ts: int
    entry_ts: int
    entry_ref: float
    initial_stop: float
    targets: tuple[float, ...]
    leverage: int
    qty: float
    notional: float
    margin: float
    liq_price: float
    risk_usdt: float
    legs: list[Leg] = field(default_factory=list)
    exit_ts: int | None = None
    exit_kind: str | None = None
    exit_reason: str = ""
    bars_held: int = 0
    funding_usdt: float = 0.0
    funding_events: int = 0
    flags: list[str] = field(default_factory=list)
    r: dict[str, float] = field(default_factory=dict)
    pnl_usdt: dict[str, float] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, int, str]:
        return (self.symbol, int(self.signal_ts), self.side)

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "variant_id": self.variant_id, "side": self.side, "signal_ts": self.signal_ts,
                "entry_ts": self.entry_ts, "entry_ref": self.entry_ref, "initial_stop": self.initial_stop,
                "targets": list(self.targets), "leverage": self.leverage, "qty": self.qty, "notional": self.notional,
                "margin": self.margin, "liq_price": self.liq_price, "risk_usdt": self.risk_usdt,
                "legs": [{"kind": lg.kind, "ref": lg.ref, "frac": lg.frac, "market": lg.market, "ts": lg.ts,
                          "point": lg.point, "reason": lg.reason} for lg in self.legs],
                "exit_ts": self.exit_ts, "exit_kind": self.exit_kind, "exit_reason": self.exit_reason,
                "bars_held": self.bars_held, "funding_usdt": self.funding_usdt, "funding_events": self.funding_events,
                "flags": list(self.flags), "r": dict(self.r), "pnl_usdt": dict(self.pnl_usdt)}


@dataclass
class KernelResult:
    variant_id: str
    symbol: str
    trades: list[KernelTrade]
    decisions: int
    bars: int
    rejected: dict[str, int]
    signals: int

    def to_dict(self) -> dict[str, Any]:
        return {"kernel": KERNEL_VERSION, "variant_id": self.variant_id, "symbol": self.symbol, "decisions": self.decisions,
                "bars": self.bars, "signals": self.signals, "rejected": dict(sorted(self.rejected.items())),
                "trades": [t.to_dict() for t in self.trades]}


# ============================================================================ kimlik (spec_sha, §2.1)
def lib_params(variant_id: str) -> dict[str, Any]:
    """Varyantın mühürlü parametreleri (alias'lı ızgara üyesi tabanınkini taşır)."""
    vs = RG.variants_by_id()
    v = vs[variant_id]
    if v.get("alias_of"):
        v = vs[v["alias_of"]]
    return RG.thaw(v["params"])


def make_variant(variant_id: str) -> Variant:
    """`strategy_lib` varyantı, mühürlü kayıttaki parametrelerle (tek kaynak)."""
    vs = RG.variants_by_id()
    base = vs[variant_id].get("alias_of") or variant_id
    return SLC.make(base, lib_params(variant_id))


def spec_for(variant_id: str) -> dict[str, Any]:
    """Varyantın §2.1 spec'i: `registry.lib_spec` + `strategy_lib` modül kaynağının sha'sı (alias → tabanınki)."""
    base = RG.variants_by_id()[variant_id].get("alias_of") or variant_id
    return RG.lib_spec(variant_id, SLI.module_src_sha(base))


def spec_sha_for(variant_id: str) -> str:
    """`registry.spec_sha` (mühürlü kanonik fonksiyon; yeniden yazılmaz) — deneme defterinin `seal_entries`'iyle aynı."""
    return RG.spec_sha(spec_for(variant_id))


def module_src_shas() -> dict[str, str]:
    """Uygulanmış varyantların `module_src_sha`'sı (`trials.seal_entries` girdisi; P3b-2'de 106 varyanta tamamlanır)."""
    return {v: SLI.module_src_sha(v) for v in SLC.IMPLEMENTED}


# ============================================================================ yardımcılar
def _beyond(long: bool, level: float, px: float) -> bool:
    return px <= level if long else px >= level


def _tf_index(rows: Sequence[Mapping[str, Any]]) -> list[int]:
    return [int(r["timestamp"]) for r in rows]


def _closed_upto(ts_list: list[int], rows: Sequence[Mapping[str, Any]], step: int, now_ms: int, n: int) -> list:
    j = bisect.bisect_right(ts_list, int(now_ms) - step)
    return list(rows[max(0, j - n):j])


def break_even(side_long: bool, entry_fill: float, fee: float, slip: float) -> float:
    """`FuturesLedgerV2.break_even_price` (giriş ücreti payı = taker, çıkış taker, kayma tamponu), float; tick yuvarlaması
    yok (çekirdek fiyat adımına kuantize etmez)."""
    if side_long:
        return entry_fill * (1.0 + fee) / ((1.0 - slip) * (1.0 - fee))
    return entry_fill * (1.0 - fee) / ((1.0 + slip) * (1.0 + fee))


def size_trade(side: str, fill: float, stop: float) -> tuple[bool, str, int, float, float, float]:
    """(ok, neden, kaldıraç, notional, marj, likidasyon fiyatı) — `learning_mode.fit_size` (slot 1, rezerv 0, hedef risk
    %0,5 × 200) + bracket likidasyon formülü; mesafe ≥ MIN_LIQ_MULT × stop tutmazsa kaldıraç düşürülür."""
    E = float(SIZING["base_equity_usdt"])
    risk_pct = float(SIZING["risk_pct"])
    s = abs(fill - stop) / fill
    n_risk = risk_pct / 100.0 * E / s
    br = bracket_for(Decimal(repr(n_risk)))
    fit = fit_size(equity=E, entry=fill, stop=stop, slots=1, leverage_max=int(br.max_leverage), risk_pct=risk_pct,
                   reserve_pct=0.0, liq_buffer_mult=MIN_LIQ_MULT, mmr=float(br.mmr), min_notional=0.0,
                   hard_cap_pct=risk_pct, min_notional_bump=False)
    if not fit.ok:
        return False, str(fit.reason or "FIT_FAILED"), 0, 0.0, 0.0, 0.0
    lev, notional = int(fit.leverage), float(fit.notional)
    while lev >= 1:
        b = bracket_for(Decimal(repr(notional)))
        qty = notional / fill
        margin = notional / lev
        liq = float(liquidation_price(side, Decimal(repr(fill)), Decimal(repr(qty)), Decimal(repr(margin)), b.mmr,
                                      b.maint_amount))
        if abs(fill - liq) >= MIN_LIQ_MULT * abs(fill - stop) * (1.0 - 1e-12):
            return True, "", lev, notional, margin, liq
        lev -= 1
        notional = min(notional, E * lev) if lev >= 1 else notional
    return False, "LIQ_BUFFER_TOO_THIN", 0, 0.0, 0.0, 0.0


# ============================================================================ fonlama
class FundingSeries:
    """Deponun uzlaşma satırları (sıralı); boşluk ölçüsü serinin kendi tipik (medyan) aralığıdır."""

    def __init__(self, rows: Sequence[tuple[int, float, float]] | None):
        self.rows = sorted((int(t), r, m) for t, r, m in (rows or ()))
        self.ts = [t for t, _r, _m in self.rows]
        diffs = sorted(b - a for a, b in zip(self.ts, self.ts[1:]) if b > a)
        self.typical = diffs[len(diffs) // 2] if diffs else None

    def window(self, a_ms: int, b_ms: int) -> tuple[list[tuple[int, float, float]], bool]:
        """((a, b] aralığındaki satırlar, boşluk var mı). Boşluk: seri pencereyi örtmüyor (ilk satır a'dan, son satır
        b'den tipik aralığın `FUNDING_GAP_FACTOR` katından uzak), pencereyi kuşatan ardışık satırlar arası bu sınırı aşıyor
        ya da penceredeki bir satırın oranı/mark'ı okunamıyor."""
        if not self.rows or self.typical is None:
            return [], True
        a, b = int(a_ms), int(b_ms)
        i = bisect.bisect_right(self.ts, a)
        j = bisect.bisect_right(self.ts, b)
        sel = self.rows[i:j]
        lim = FUNDING_GAP_FACTOR * self.typical
        gap = (i == 0 and self.ts[0] - a > lim) or (j == len(self.ts) and b - self.ts[-1] > lim)
        chain = self.ts[max(0, i - 1):min(len(self.ts), j + 1)]
        if any(y - x > lim for x, y in zip(chain, chain[1:])):
            gap = True
        for _t, r, m in sel:
            if r is None or m is None or not math.isfinite(float(r)) or not math.isfinite(float(m)) or float(m) <= 0:
                gap = True
        return sel, gap


def funding_of(trade: KernelTrade, fs: FundingSeries | None) -> tuple[float, int, bool]:
    """(USDT tutar, olay sayısı, boşluk). Miktar uzlaşma anından ÖNCEKİ dolumlardan; o anda kapanan bacak öder."""
    if fs is None:
        return 0.0, 0, True
    sel, gap = fs.window(trade.entry_ts, int(trade.exit_ts))
    sgn = 1.0 if trade.side == LONG else -1.0
    total, n = 0.0, 0
    for t, r, m in sel:
        if r is None or m is None or not (math.isfinite(float(r)) and math.isfinite(float(m))) or float(m) <= 0:
            continue
        open_frac = 1.0 - sum(lg.frac for lg in trade.legs if lg.ts < t)
        if open_frac <= 1e-12 or float(r) == 0.0:
            continue
        total += -sgn * trade.qty * open_frac * float(m) * float(r)
        n += 1
    return total, n, gap


# ============================================================================ maliyet (aritmetik yeniden maliyetlendirme)
def cost_trade(trade: KernelTrade, sc: CostScenario, tick: float) -> tuple[float, float]:
    """(R, net USDT) — aynı yolun bacakları senaryonun ücret/kayma/spread'iyle; fonlama aynı."""
    s = 1.0 if trade.side == LONG else -1.0
    q = trade.qty
    spread = sc.spread_ticks * float(tick or 0.0)
    e_fill = trade.entry_ref * (1.0 + s * sc.slip) + s * spread
    gross = 0.0
    fees = sc.fee * e_fill * q
    margin_left = trade.margin
    for lg in trade.legs:
        ql = q * lg.frac
        if lg.kind == EXIT_LIQ:
            lp = lg.ref
            m_leg = margin_left
            fee = min(ql * lp * LIQ_FEE_PCT / 100.0, m_leg)
            g = (lp - e_fill) * ql * s
            if -g + fee > m_leg:
                g = -(m_leg - fee)
            gross += g
            fees += fee
            margin_left -= m_leg
            continue
        if lg.market:
            x = lg.ref * (1.0 - s * sc.slip) - s * spread
        else:
            x = lg.ref - s * spread
        gross += (x - e_fill) * ql * s
        fees += sc.fee * x * ql
        margin_left -= trade.margin * lg.frac
    net = gross - fees + trade.funding_usdt
    risk = abs(e_fill - trade.initial_stop) * q
    return (net / risk if risk > 0 else 0.0), net


# ============================================================================ çekirdek döngü
class _Pos:
    __slots__ = ("trade", "intent", "stop", "targets", "tidx", "frac_left", "entry_idx", "tp1_done", "fill")

    def __init__(self, trade: KernelTrade, intent: Intent, entry_idx: int, fill: float):
        self.trade, self.intent, self.entry_idx, self.fill = trade, intent, entry_idx, fill
        self.stop = trade.initial_stop
        self.targets = list(trade.targets)
        self.tidx = 0
        self.frac_left = 1.0
        self.tp1_done = False


def _resolve_targets(intent: Intent, entry: float) -> tuple[float, ...]:
    s = 1.0 if intent.side == LONG else -1.0
    out = []
    for t in intent.targets:
        out.append(float(t.value) if t.kind == TARGET_PRICE else entry + s * float(t.value) * abs(entry - intent.stop))
    return tuple(out)


def _entry_gate(intent: Intent, px: float) -> str | None:
    """Girişte (dolumdan önceki açılış fiyatıyla) kapılar, canlı sırayla: kovalama (Formasyon `_try_open`'ın ilk kapısı),
    stop yanlış tarafta (`apply_action` `STRATEGY_BAD_STOP`), giriş kayması, test edilmiş risk aralığı."""
    long = intent.side == LONG
    ref = intent.entry_ref
    if ref.anchor is not None and ref.max_anchor_dist is not None and abs(px - ref.anchor) > ref.max_anchor_dist:
        return REJ_CHASE
    if px <= 0 or (long and intent.stop >= px) or (not long and intent.stop <= px):
        return REJ_BAD_STOP
    if ref.max_drift_pct and ref.price > 0 and abs(px - ref.price) / ref.price * 100.0 > float(ref.max_drift_pct):
        return REJ_DRIFT
    rb = intent.risk_atr_bounds
    if rb is not None and not (rb.atr > 0 and rb.lo * rb.atr < abs(px - intent.stop) <= rb.hi * rb.atr):
        return REJ_RISK_RANGE
    return None


def run(variant: Variant, data: SeriesData, *, start_ms: int | None = None, end_ms: int | None = None) -> KernelResult:
    """Varyantı `data` üzerinde oynat. `start_ms`/`end_ms`: YENİ GİRİŞ kararlarının anı (bar kapanışı) aralığı
    [start, end]; pencere öncesi barlar yalnız gösterge ısınmasıdır; açık pozisyon pencereden sonra da kendi kuralıyla
    (`manage`, stop, hedef) yönetilir. Belirlenimci."""
    base = scenario("base")
    stf = variant.signal_tf
    step = TF_MS[stf]
    rows = list(data.bars.get(stf) or ())
    views = {tf: (_tf_index(data.bars.get(tf) or ()), list(data.bars.get(tf) or ()), TF_MS[tf],
                  int(variant.lookback.get(tf, 0) or len(data.bars.get(tf) or ()))) for tf in variant.timeframes}
    btc = list(data.btc_1d or ())
    btc_view = (_tf_index(btc), btc, TF_MS["1d"], int(variant.btc_lookback or len(btc))) if variant.needs_btc else None
    fs = FundingSeries(data.funding) if data.funding is not None else None
    trades: list[KernelTrade] = []
    history: list[BookEntry] = []
    used_signals: set[int] = set()
    rejected: dict[str, int] = {}
    pos: _Pos | None = None
    pending: Any = None                    # Intent (aç) | ("CLOSE", neden) | None
    decisions = signals_n = 0

    def ctx_for(now: int) -> dict[str, Any]:
        c: dict[str, Any] = {"now_ms": now, "symbol": data.symbol, "history": tuple(history)}
        if btc_view is not None:
            c["btc_bars"] = _closed_upto(btc_view[0], btc_view[1], btc_view[2], now, btc_view[3])
        return c

    def bars_for(now: int) -> dict[str, list]:
        return {tf: _closed_upto(v[0], v[1], v[2], now, v[3]) for tf, v in views.items()}

    def leg(kind: str, ref: float, frac: float, market: bool, ts: int, point: str, reason: str = "") -> None:
        pos.trade.legs.append(Leg(kind, float(ref), float(frac), market, int(ts), point, reason))
        pos.frac_left -= frac

    def finish(kind: str, reason: str, ts: int, bar_i: int) -> None:
        nonlocal pos
        t = pos.trade
        t.exit_ts, t.exit_kind, t.exit_reason = int(ts), kind, reason
        t.bars_held = bar_i - pos.entry_idx + 1
        trades.append(t)
        pos = None

    def walk_point(point: str, px: float, ts: int, bar_i: int, long: bool) -> bool:
        """Tek gözlem (defterin fiyat-yalnız tiki). Pozisyon kapandıysa True."""
        eff = px
        if point != "open" and _beyond(long, pos.stop, px):
            eff = pos.stop                                   # seviyede stop (boşluk yalnız açılışta)
        t = pos.trade
        if _beyond(long, t.liq_price, eff):
            leg(EXIT_LIQ, t.liq_price, pos.frac_left, False, ts, point, "LIQUIDATION")
            finish(EXIT_LIQ, "LIQUIDATION", ts, bar_i)
            return True
        if _beyond(long, pos.stop, eff):
            fill = eff * (1.0 - (1 if long else -1) * base.slip)
            kind_reason = "BREAKEVEN_STOP" if pos.tp1_done else ("GAP_STOP" if point == "open" else "STOP")
            if _beyond(long, t.liq_price, fill):              # kayma dolumu likidasyonun ötesine itti
                leg(EXIT_LIQ, t.liq_price, pos.frac_left, False, ts, point, "SLIPPAGE_BEYOND_LIQUIDATION")
                finish(EXIT_LIQ, "SLIPPAGE_BEYOND_LIQUIDATION", ts, bar_i)
                return True
            leg(EXIT_STOP, eff, pos.frac_left, True, ts, point, kind_reason)
            finish(EXIT_STOP, kind_reason, ts, bar_i)
            return True
        while pos is not None and pos.tidx < len(pos.targets):
            tgt = pos.targets[pos.tidx]
            if not ((eff >= tgt) if long else (eff <= tgt)):
                break
            last = pos.tidx == len(pos.targets) - 1
            if last:
                leg(EXIT_TARGET, tgt, pos.frac_left, False, ts, point, "TARGET%d" % (pos.tidx + 1))
                pos.tidx += 1
                finish(EXIT_TARGET, "TARGET%d" % pos.tidx, ts, bar_i)
                return True
            leg(EXIT_TARGET, tgt, pos.frac_left * TP1_FRACTION, False, ts, point, "TARGET%d" % (pos.tidx + 1))
            if pos.tidx == 0:
                pos.tp1_done = True
                be = break_even(long, pos.fill, base.fee, base.slip)
                if (long and be > pos.stop) or (not long and be < pos.stop):
                    pos.stop = be
            pos.tidx += 1
        return False

    for i, bar in enumerate(rows):
        o, h, lo, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        t_open = int(bar["timestamp"])
        t_close = t_open + step
        # ---------------------------------------------------------------- açılış: bekleyen eylem
        if pos is not None:
            long = pos.intent.side == LONG
            closed = walk_point("open", o, t_open, i, long)
            if not closed and isinstance(pending, tuple) and pending[0] == ACT_CLOSE:
                leg(pending[2], o, pos.frac_left, True, t_open, "open", pending[1])
                finish(pending[2], pending[1], t_open, i)
            pending = None
        elif isinstance(pending, Intent):
            it = pending
            pending = None
            why = _entry_gate(it, o)
            if why is None:
                long = it.side == LONG
                fill = o * (1.0 + (1 if long else -1) * base.slip)
                ok, why2, lev, notional, margin, liq = size_trade(it.side, fill, it.stop)
                if ok:
                    tr = KernelTrade(symbol=data.symbol, variant_id=variant.variant_id, side=it.side, signal_ts=it.signal_ts,
                                     entry_ts=t_open, entry_ref=o, initial_stop=it.stop, targets=_resolve_targets(it, o),
                                     leverage=lev, qty=notional / fill, notional=notional, margin=margin, liq_price=liq,
                                     risk_usdt=abs(fill - it.stop) * notional / fill, evidence=dict(it.evidence))
                    pos = _Pos(tr, it, i, fill)
                    history.append(BookEntry(data.symbol, it.side, it.signal_ts, t_open, it.evidence))
                    if it.one_entry_per_signal:
                        used_signals.add(int(it.signal_ts))
                else:
                    why = REJ_SIZE + ":" + why2
            if why is not None:
                rejected[why] = rejected.get(why, 0) + 1
        else:
            pending = None
        # ---------------------------------------------------------------- bar içi yol (ters → lehte → kapanış)
        if pos is not None:
            long = pos.intent.side == LONG
            adv, fav = (lo, h) if long else (h, lo)
            for point, px in (("adverse", adv), ("favourable", fav), ("close", c)):
                if walk_point(point, px, t_close, i, long):
                    break
        # ---------------------------------------------------------------- kapanış: karar
        now = t_close
        if pos is not None:
            decisions += 1
            bv = bars_for(now)
            pv = PosView(side=pos.intent.side, entry=pos.fill, stop=pos.stop, initial_stop=pos.trade.initial_stop,
                         opened_ms=pos.trade.entry_ts, signal_ts=pos.intent.signal_ts, entry_bar_ts=pos.trade.entry_ts,
                         bars_held=i - pos.entry_idx + 1, intent=pos.intent)
            act = variant.manage(pv, bv, ctx_for(now))
            if act.kind == ACT_CLOSE:
                pending = (ACT_CLOSE, act.reason, EXIT_RULE)
            elif act.kind == ACT_MOVE_STOP:
                pos.stop = float(act.price)
            mh = pos.intent.max_hold_bars
            if pending is None and mh is not None and (i - pos.entry_idx + 1) >= int(mh):
                pending = (ACT_CLOSE, "TIME_STOP_%d_BARS" % int(mh), EXIT_TIME)
        elif (start_ms is None or now >= int(start_ms)) and (end_ms is None or now <= int(end_ms)):
            decisions += 1
            intents = variant.signals(bars_for(now), ctx_for(now))
            for it in intents:
                if it.one_entry_per_signal and int(it.signal_ts) in used_signals:
                    rejected["SIGNAL_ALREADY_USED"] = rejected.get("SIGNAL_ALREADY_USED", 0) + 1
                    continue
                signals_n += 1
                pending = it
                break
    if pos is not None and rows:
        last = rows[-1]
        t_last = int(last["timestamp"]) + step
        leg(EXIT_END, float(last["close"]), pos.frac_left, True, t_last, "close", "END_OF_DATA")
        pos.trade.flags.append(FLAG_OPEN_AT_END)
        finish(EXIT_END, "END_OF_DATA", t_last, len(rows) - 1)
    for t in trades:
        t.funding_usdt, t.funding_events, gap = funding_of(t, fs)
        if gap:
            t.flags.append(FLAG_FUNDING_GAP)
        for name in SCENARIOS:
            r, net = cost_trade(t, scenario(name), data.price_tick)
            t.r[name], t.pnl_usdt[name] = r, net
    return KernelResult(variant_id=variant.variant_id, symbol=data.symbol, trades=trades, decisions=decisions,
                        bars=len(rows), rejected=rejected, signals=signals_n)


__all__ = ["CostScenario", "EXIT_END", "EXIT_LIQ", "EXIT_RULE", "EXIT_STOP", "EXIT_TARGET", "EXIT_TIME", "FLAG_FUNDING_GAP",
           "FLAG_OPEN_AT_END", "FundingSeries", "KERNEL_VERSION", "KernelResult", "KernelTrade", "Leg", "SCENARIOS",
           "SeriesData", "break_even", "cost_trade", "funding_of", "lib_params", "make_variant", "module_src_shas", "run",
           "scenario", "size_trade", "spec_for", "spec_sha_for"]
