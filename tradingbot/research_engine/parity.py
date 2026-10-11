"""P3b — parite (docs/ENGINE_BRAIN_V1.md §2.1; ana belge §6.2). İki ölçü, eşikleri `LIB_V1.thresholds`'ta mühürlü:

**(a) Çekirdek ↔ defter** (`parity_kernel`: aile başına ≥ 200 sentetik işlem; sinyal kimliği %100; medyan |ΔR| ≤ 0,02;
p95 ≤ 0,05). Aynı sentetik barlar iki yoldan oynatılır:

* çekirdek: `strategy_lib` kopyası → `kernel.run` (base maliyet);
* başvuru: canlı kural fonksiyonu DOĞRUDAN (`paper_rules.decide_with_structures` / `decide_from_rows` — C4'te kapı
  dahil canlı `candle_book.decide`; Formasyonda `build_plans_v3` + `PatternBook._try_open`'ın kovalama/stop kapıları) →
  `strategy_paper.apply_action` → `FuturesLedgerV2.tick`. Bar içi yol çekirdekle aynı sözleşmedir (`learning_cf`
  kuralı: `learning_cf._bar_path` sırası, her nokta ayrı fiyat-yalnız tik, stopun ötesindeki nokta seviyeye kırpılır,
  boşluk yalnız açılışta). Defter base maliyetiyle kurulur (taker %0,05, 3 bps, bracket likidasyon, uzlaşma
  anlarında gerçek fonlama); fiyat adımı serinin kendi `price_tick`'i (defter dolumu adıma kuantize eder, çekirdek
etmez — ΔR'nin bir kaynağı budur), miktar adımı 1e-6. Büyüklük §2.1'indir (canlı defterin
  öğrenme/slot boyutu değil): başvuru eylemine çekirdeğin `size_trade` kaldıracı yazılır, risk ölçeği 50 kat (R ölçekten
  bağımsızdır; min-notional retlerini önler).
* Sinyal kimliği = (sembol, sinyal barı, yön) kümelerinin eşitliği (iki yönde); ΔR = |R_çekirdek − R_defter| eşleşen
  işlemlerde (`TradeRecord.r_multiple`).

**(b) Kopya ↔ canlı defter** (`parity_copy`): yalnız POLICY kohortu girişleri (`learning_mode.classify_unlock_codes`
boş liste → POLICY; öğrenme etiketi olmayan eski kayıt `UNTAGGED` sayılır, POLICY değil), config dönemi başına
(`rehydrate.config_epoch` + mühürlü `CONFIG_EPOCHS_V1`): ≥ 20 girişli her dönemde canlı girişlerin ≥ %95'i kopyada
aynı sinyal barı ve yönle bulunmalı ve eşleşenlerde medyan |ΔR| ≤ 0,05. < 20 girişli dönem raporlanır, kapıya girmez.
Bir kapılı dönem tutmazsa kopya `PARITY_FAIL` (lider tablosuna ve kapıya girmez); hiç kapılı dönem yoksa
`PARITY_UNPROVEN` (koşul gösterilemedi; ana belge §6.2 "ancak bu koşulla girer" → uygun değil). Kopyanın canlıda
olmayan fazla işlemleri ve canlının açıklanmış (slot/teminat) retleri ayrıca sayılır. P3b-1'de iskelet: girdiler
fixture defterlerdir; gece bağlantısı P3g'dedir.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import kernel as KN
from . import registry as RG
from . import rehydrate as RH

PARITY_KERNEL = RG.LIB_V1["thresholds"]["parity_kernel"]
PARITY_COPY = RG.LIB_V1["thresholds"]["parity_copy"]
PASS, PARITY_FAIL, PARITY_UNPROVEN = "PASS", "PARITY_FAIL", "PARITY_UNPROVEN"
#: başvuru defterinin risk ölçeği (R ölçekten bağımsız; min-notional reddini önler)
REF_SCALE = 50.0
REF_EQUITY = 1_000_000.0
#: kopya → canlı defter (state klasörü; `rehydrate.RULE_BOOKS` ile aynı adlar) ve sinyal dilimi
BOOK_DIR = {"K_T2": "strategy_paper", "K_M2": "strategy_paper_m2", "K_BOX": "strategy_paper_box",
            "K_D4": "strategy_paper_trend4h", **{f"K_C4_CV{i:03d}": "strategy_paper_candle4h" for i in range(1, 9)},
            "K_FM": "pattern_trader"}


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc)


def _pct(xs: Sequence[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# ============================================================================ (a) başvuru: canlı kural + defter
class _Lookup:
    """Defterin fonlama kaynağı: uzlaşma anındaki gerçek oran ve mark (deponun satırları)."""

    def __init__(self, rows: Sequence[tuple[int, float, float]] | None):
        self.by = {int(t): (r, m) for t, r, m in (rows or ())}

    def __call__(self, symbol: str, when: datetime):
        v = self.by.get(int(round(when.timestamp() * 1000)))
        return None if v is None else Decimal(repr(float(v[0])))

    def settlement_mark(self, symbol: str, when: datetime):
        v = self.by.get(int(round(when.timestamp() * 1000)))
        return None if v is None else Decimal(repr(float(v[1])))


class _LiveRule:
    """Kopyanın canlı karşılığı: canlı kural fonksiyonunun kendisi, canlı defterin pozisyon nesnesiyle."""

    def __init__(self, variant_id: str):
        from ..strategy_lib import catalog as SLC
        self.variant_id = variant_id
        self.book = SLC.BOOK_OF[variant_id]
        self.params = KN.lib_params(variant_id)
        self.copy = KN.make_variant(variant_id)          # yalnız pencere/dilim bilgisi (karar değil)

    def decide(self, bars: Mapping[str, list], ctx: Mapping[str, Any], ledger: Any, symbol: str) -> dict | None:
        from .. import paper_rules
        from ..strategy_lib import livebars
        from ..structures import bots as SB
        now = int(ctx["now_ms"])
        pos = ledger.positions.get(symbol)
        b, p = self.book, self.params
        if b in ("T2", "M2"):
            name = {"T2": "t2_trend_regime", "M2": "m2_tsmom28"}[b]
            params = paper_rules.build_params(name, atr_mult=float(p["atr_mult"]))
            last = (bars.get("1d") or [None])[-1]
            mode = str(p["structures"]).upper()
            sctx = None if mode == "OFF" else SB.StructureContext(
                mode=mode, symbol=symbol, as_of_ms=now, price=float(last["close"]) if last else None,
                used_patterns=SB.used_patterns_of(ledger), provenance={"market": "USDM_PERP", "source": "parity_ref"})
            act, _d, _a = paper_rules.decide_with_structures(
                name, frames={"1d": livebars.frame(bars.get("1d"), "1d", daily_indicators=True)},
                btc_rows=livebars.btc_daily_rows(ctx.get("btc_bars"), now), now_ms=now, position=pos, params=params,
                ctx=sctx)
            return act
        if b == "BOX":
            from dataclasses import fields

            from .. import box_theory
            names = {f.name for f in fields(box_theory.BoxParams)}
            params = paper_rules.build_params("b1_box_fade", rule_params={k: v for k, v in p.items() if k in names})
            return paper_rules.decide_from_rows("b1_box_fade", daily=livebars.daily_rows(bars.get("1d"), now),
                                                intraday=livebars.intraday_rows(bars.get("5m"), "5m", now), btc_rows=None,
                                                position=pos, params=params, now_ms=now)
        if b == "D4":
            params = paper_rules.build_params("d4_donchian_20_10", rule_params={"entry_window_min": int(p["entry_window_min"])})
            return paper_rules.decide_from_rows("d4_donchian_20_10", daily=[],
                                                intraday=livebars.intraday_rows(bars.get("4h"), "4h", now), btc_rows=None,
                                                position=pos, params=params, now_ms=now)
        if b == "C4":
            params = paper_rules.build_params("c4_candle_variations", rule_params={
                "entry_window_min": int(p["entry_window_min"]), "variations": [str(p["variation"])]})
            return paper_rules.decide_from_rows("c4_candle_variations", daily=[],
                                                intraday=livebars.candle_rows(bars.get("4h"), now), btc_rows=None,
                                                position=pos, params=params, now_ms=now)
        if b == "FM":
            if pos is not None:
                return None
            from ..pattern_trader import strategy_v3 as S3
            from ..structures import analyze
            rows = livebars.fm_rows(bars.get("4h"))
            if not rows:
                return None
            an = analyze(market="USDM_PERP", symbol=symbol, timeframe="4h", bars=rows, as_of_ms=now,
                         data_provenance={"market": "USDM_PERP", "source": "parity_ref"})
            plans, _s = S3.build_plans_v3(symbol, as_of_ms=now, analyses={"4h": an}, bars_by_tf={"4h": rows},
                                          used_patterns=SB.used_patterns_of(ledger))
            if not plans:
                return None
            pl = sorted(plans, key=lambda d: int(d.get("triggered_at_ms") or 0))[0]
            st = dict(pl["structure"], action="ENTER", reason_code="PLAN_" + str(pl.get("family")), side=str(pl["side"]),
                      trigger=dict(pl.get("trigger") or {}), bot="pattern_trader")
            return {"action": "OPEN", "direction": pl["side"], "stop": float(pl["stop"]), "targets": [],
                    "target_r_from_entry": float(pl["target_from_entry_rr"]), "leverage": 1, "leverage_max": 0,
                    "reason": "PLAN_" + str(pl["family"]), "name": "pattern_trader", "setup_type": pl["family"],
                    "signal_ts": int(pl["triggered_at_ms"]) - 14_400_000, "signal_close_ms": int(pl["triggered_at_ms"]),
                    "atr14": float(pl["atr"]), "risk_atr_bounds": list(pl["risk_atr_bounds"]), "one_entry_per_signal": True,
                    "structure": st, "_chase": (float(pl["trigger"]["level"]), float(pl["chase_atr"]) * float(pl["atr"])),
                    "_hold_bars": int(S3.V3_SIGNAL["hold_bars"])}
        raise KeyError(self.variant_id)


@dataclass
class RefTrade:
    symbol: str
    side: str
    signal_ts: int
    r: float
    exit_reason: str
    opened_at: str
    closed_at: str
    funding: float


def _ref_ledger(data: KN.SeriesData) -> tuple[Any, Any, Any, Any, Any]:
    from ..accounting import (FeeSchedule, FundingSchedule, FuturesLedgerV2, LiquidationParams, MarketType,
                              SlippageModel, SymbolFilters)
    from ..risk import PROFILES, KillSwitch, RiskEngine
    base = KN.scenario("base")
    led = FuturesLedgerV2(REF_EQUITY, max_positions=None, enforce_position_cap=False,
                          fees=FeeSchedule(maker_pct=Decimal("0.02"), taker_pct=Decimal(repr(base.taker_fee_pct))),
                          slippage=SlippageModel(fixed_bps=Decimal(repr(base.slippage_bps))),
                          liq_params=LiquidationParams(),
                          funding=FundingSchedule(hours_utc=(0, 8, 16), fallback_to_last_known=False),
                          tp1_fraction=Decimal(repr(KN.TP1_FRACTION)), breakeven_at_mfe_r=Decimal("0"))
    tick = Decimal(repr(float(data.price_tick))) if data.price_tick and data.price_tick > 0 else Decimal("1e-8")
    filt = SymbolFilters(symbol=data.symbol, market_type=MarketType.USDM_PERP, price_tick=tick,
                         qty_step=Decimal("1e-6"), min_qty=Decimal("0"), max_qty=Decimal("1e15"),
                         market_max_qty=Decimal("1e15"), min_notional=Decimal("0"), max_leverage=125, source="parity")
    prof = replace(PROFILES["PAPER_RESEARCH"], name="PARITY_A",
                   risk_per_trade_pct=float(KN.SIZING["risk_usdt"]) * REF_SCALE / REF_EQUITY * 100.0,
                   max_total_open_risk_pct=100.0, futures_max_leverage=125, max_position_pct=1e9,
                   max_spot_allocation_pct=None)
    return led, filt, prof, RiskEngine(prof, KillSwitch()), _Lookup(data.funding)


def reference_run(variant_id: str, data: KN.SeriesData) -> tuple[list[RefTrade], dict[str, int]]:
    """Canlı yol: canlı kural → `apply_action` → `FuturesLedgerV2.tick` (bar içi yol çekirdekle aynı sözleşme)."""
    from ..accounting import TickData
    from ..core import iso
    from ..learning_cf import _bar_path
    from ..paper_rules import signal_already_used
    from ..risk import build_state
    from ..strategy_lib import TF_MS
    from ..strategy_paper import DataVerdict, apply_action
    rule = _LiveRule(variant_id)
    copy = rule.copy
    stf = copy.signal_tf
    step = TF_MS[stf]
    sym = data.symbol
    rows = list(data.bars.get(stf) or ())
    idx = {tf: ([int(r["timestamp"]) for r in (data.bars.get(tf) or ())], list(data.bars.get(tf) or ()), TF_MS[tf],
                int(copy.lookback.get(tf))) for tf in copy.timeframes}
    btc = list(data.btc_1d or ())
    btc_idx = [int(r["timestamp"]) for r in btc]
    led, filt, prof, risk, lookup = _ref_ledger(data)
    rejects: dict[str, int] = {}
    pending: dict | None = None
    entry_bar: dict[str, int] = {}

    def reject(_s: str, why: str) -> None:
        rejects[why] = rejects.get(why, 0) + 1

    def state(now: datetime) -> Any:
        return build_state(equity=float(led.wallet_balance), starting_equity=REF_EQUITY, available=float(led.available),
                           used_margin=float(led.used_margin), positions=[], history=[], now=now)

    def tick(px: float, at: datetime, first: bool) -> None:
        led.tick({sym: TickData(last=Decimal(repr(px)), mark=Decimal(repr(px)), ts=iso(at))}, now_utc=at,
                 funding_rate_lookup=lookup, bar_advance=first)

    for i, bar in enumerate(rows):
        o, h, lo, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        t_open = int(bar["timestamp"])
        odt, cdt = _dt(t_open), _dt(t_open + step)
        long = None
        pos = led.positions.get(sym)
        if pos is not None:
            tick(o, odt, True)                             # açılış gözlemi: boşlukta açılıştan
            if led.positions.get(sym) is not None and pending and str(pending.get("action")).upper() == "CLOSE":
                apply_action(pending, symbol=sym, price=o, tick=None, now=odt, ledger=led, risk=risk, profile=prof,
                             state=state(odt), filters=filt, run_id="parity", reject=reject, on_closed=lambda r: None,
                             data=pending["_verdict"])
            pending = None
        elif pending is not None:
            act, pending = pending, None
            chase = act.pop("_chase", None)
            if chase is not None and abs(o - chase[0]) > chase[1]:
                reject(sym, "CHASE_LIMIT")
            else:
                side = str(act.get("direction") or "LONG").upper()
                fill = o * (1.0 + (1 if side == "LONG" else -1) * KN.scenario("base").slip)
                ok, _why, lev, _n, _m, _l = KN.size_trade(side, fill, float(act["stop"])) \
                    if float(act["stop"]) > 0 and ((side == "LONG") == (float(act["stop"]) < o)) else (False, "", 1, 0, 0, 0)
                act["leverage"], act["leverage_max"] = (int(lev) if ok else 1), 0
                res = apply_action(act, symbol=sym, price=o, tick=None, now=odt, ledger=led, risk=risk, profile=prof,
                                   state=state(odt), filters=filt, run_id="parity", reject=reject,
                                   on_closed=lambda r: None, data=act["_verdict"],
                                   max_entry_drift_pct=float(rule.params.get("max_entry_drift_pct") or 0.0))
                if res == "OPENED":
                    entry_bar[sym] = i
        p = led.positions.get(sym)
        if p is not None:
            long = p.side.value == "LONG"
            for point, px in _bar_path(long, None, h, lo, c):   # giriş/açılış zaten işlendi: ters → lehte → kapanış
                p = led.positions.get(sym)
                if p is None:
                    break
                price = px
                if p.stop is not None and ((price <= float(p.stop)) if long else (price >= float(p.stop))):
                    price = float(p.stop)                  # seviyede stop (boşluk yalnız açılışta)
                tick(price, cdt, False)
        # ---------------------------------------------------------------- kapanış kararı
        now = t_open + step
        bars = {tf: v[1][max(0, _bisect(v[0], now - v[2]) - v[3]):_bisect(v[0], now - v[2])] for tf, v in idx.items()}
        ctx = {"now_ms": now, "symbol": sym}
        if copy.needs_btc:
            j = _bisect(btc_idx, now - TF_MS["1d"])
            ctx["btc_bars"] = btc[max(0, j - int(copy.btc_lookback)):j]
        act = rule.decide(bars, ctx, led, sym)
        verdict = DataVerdict(ok=True, entry_ok=True, market="USDM_PERP", source="parity_ref", tour_id="parity",
                              bars={tf: int(b[-1]["timestamp"]) for tf, b in bars.items() if b}, btc={})
        p = led.positions.get(sym)
        if p is not None:
            if act and str(act.get("action") or "").upper() == "CLOSE":
                pending = dict(act, _verdict=verdict)
            elif rule.book == "FM" and i - entry_bar.get(sym, i) + 1 >= 24:
                pending = {"action": "CLOSE", "reason": "TIME_STOP_24_BARS_4h", "_verdict": verdict}
        elif act and str(act.get("action") or "").upper() == "OPEN":
            if signal_already_used(act, led.history, sym):
                reject(sym, "SIGNAL_ALREADY_USED")
            else:
                pending = dict(act, _verdict=verdict)
    if led.positions.get(sym) is not None and rows:
        last = rows[-1]
        led.close_manual(sym, Decimal(repr(float(last["close"]))), reason="END_OF_DATA",
                         now=_dt(int(last["timestamp"]) + step))
    out = []
    for rec in led.history:
        ds = ((rec.features or {}).get("data_source") or {}).get("bars") or {}
        out.append(RefTrade(symbol=rec.symbol, side=str(rec.side), signal_ts=int(ds.get(stf)), r=float(rec.r_multiple),
                            exit_reason=str(rec.exit_reason), opened_at=str(rec.opened_at), closed_at=str(rec.closed_at),
                            funding=float(rec.funding)))
    return out, rejects


def _bisect(xs: list[int], v: int) -> int:
    import bisect
    return bisect.bisect_right(xs, int(v))


@dataclass
class ParityA:
    variant_ids: list[str]
    n_kernel: int
    n_ref: int
    n_matched: int
    identity_share: float
    median_abs_dr: float
    p95_abs_dr: float
    max_abs_dr: float
    only_kernel: list[tuple] = field(default_factory=list)
    only_ref: list[tuple] = field(default_factory=list)
    passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"variants": self.variant_ids, "n_kernel": self.n_kernel, "n_ref": self.n_ref, "n_matched": self.n_matched,
                "identity_share": self.identity_share, "median_abs_dr": self.median_abs_dr, "p95_abs_dr": self.p95_abs_dr,
                "max_abs_dr": self.max_abs_dr, "only_kernel": self.only_kernel[:10], "only_ref": self.only_ref[:10],
                "passed": self.passed}


def compare_a(pairs: Sequence[tuple[str, KN.KernelResult, list[RefTrade]]], *, min_trades: int | None = None) -> ParityA:
    """(a) ölçüsü: kernel ↔ başvuru, (varyant, sembol, sinyal barı, yön) anahtarıyla."""
    kt: dict[tuple, float] = {}
    rt: dict[tuple, float] = {}
    for vid, kres, refs in pairs:
        for t in kres.trades:
            kt[(vid,) + t.key()] = float(t.r["base"])
        for r in refs:
            rt[(vid, r.symbol, int(r.signal_ts), r.side)] = float(r.r)
    both = sorted(set(kt) & set(rt))
    union = set(kt) | set(rt)
    d = [abs(kt[k] - rt[k]) for k in both]
    share = (len(both) / len(union)) if union else 1.0
    th = PARITY_KERNEL
    need = int(th["min_synthetic_trades_per_family"]) if min_trades is None else int(min_trades)
    med = statistics.median(d) if d else float("nan")
    p95 = _pct(d, 0.95)
    ok = (len(both) >= need and share >= float(th["min_signal_identity_share"]) and med <= float(th["max_median_abs_dr"])
          and p95 <= float(th["max_p95_abs_dr"]))
    return ParityA(variant_ids=sorted({p[0] for p in pairs}), n_kernel=len(kt), n_ref=len(rt), n_matched=len(both),
                   identity_share=share, median_abs_dr=med, p95_abs_dr=p95, max_abs_dr=max(d) if d else float("nan"),
                   only_kernel=sorted(set(kt) - set(rt)), only_ref=sorted(set(rt) - set(kt)), passed=bool(ok))


# ============================================================================ (b) kopya ↔ canlı defter (iskelet)
def _ms_of(x: Any) -> int | None:
    from .ledgers import parse_ts
    t = parse_ts(x)
    return int(t.timestamp() * 1000) if t is not None else None


def live_cohort(rec: Mapping[str, Any]) -> str:
    """Canlı kaydın kohortu (`tj_v1` ile aynı okuma): öğrenme etiketi varsa `classify_unlock_codes` (POLICY / CAPACITY /
    SELECTIVITY), yoksa UNTAGGED."""
    from ..learning_mode import classify_unlock_codes
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    lrn = feats.get("learning") if isinstance(feats.get("learning"), dict) else None
    if lrn is None:
        return "UNTAGGED"
    return classify_unlock_codes([str(c) for c in (lrn.get("learning_unlocked_by") or [])]).upper()


def live_signal_key(rec: Mapping[str, Any], signal_tf: str) -> tuple[str, int, str] | None:
    """(sembol, sinyal barı, yön): kuralın okuduğu son kapanmış bar `features.data_source.bars[signal_tf]`."""
    feats = rec.get("features") if isinstance(rec.get("features"), dict) else {}
    ds = feats.get("data_source") if isinstance(feats.get("data_source"), dict) else {}
    ts = (ds.get("bars") or {}).get(signal_tf) if isinstance(ds.get("bars"), dict) else None
    if ts is None:
        return None
    sym = str(rec.get("symbol") or "").replace("/", "").replace(":USDT", "")
    return (sym, int(ts), str(rec.get("side") or "").upper())


@dataclass
class EpochParity:
    epoch: str | None
    n_policy: int
    n_matched: int
    share: float
    median_abs_dr: float
    gated: bool
    passed: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "n_policy": self.n_policy, "n_matched": self.n_matched, "share": self.share,
                "median_abs_dr": self.median_abs_dr, "gated": self.gated, "passed": self.passed}


@dataclass
class ParityB:
    variant_id: str
    verdict: str
    epochs: list[EpochParity]
    cohorts: dict[str, int]
    copy_extra: int
    explained_rejections: int
    unassigned_epoch: int

    def to_dict(self) -> dict[str, Any]:
        return {"variant_id": self.variant_id, "verdict": self.verdict, "epochs": [e.to_dict() for e in self.epochs],
                "cohorts": dict(sorted(self.cohorts.items())), "copy_extra": self.copy_extra,
                "explained_rejections": self.explained_rejections, "unassigned_epoch": self.unassigned_epoch}


def compare_b(variant_id: str, copy_trades: Sequence[KN.KernelTrade], live_records: Sequence[Mapping[str, Any]], *,
              book: str | None = None, research: Path | None = None,
              explained_rejections: Sequence[Mapping[str, Any]] = ()) -> ParityB:
    """(b): canlı defter kayıtları (`TradeRecord` sözlükleri) ↔ kopyanın çekirdek işlemleri. Dönem `rehydrate.config_epoch`
    ile (varsayılan: mühürlü `CONFIG_EPOCHS_V1`, `registry.PKG_DIR/library/config_epochs.json`)."""
    book = book or BOOK_DIR[variant_id]
    stf = KN.make_variant(variant_id).signal_tf
    research = research if research is not None else RG.PKG_DIR
    copy_by = {(t.symbol.replace("/", "").replace(":USDT", ""), int(t.signal_ts), t.side): t for t in copy_trades}
    cohorts: dict[str, int] = {}
    per: dict[str | None, list[tuple[tuple, float]]] = {}
    unassigned = 0
    live_keys: set[tuple] = set()
    for rec in live_records:
        coh = live_cohort(rec)
        cohorts[coh] = cohorts.get(coh, 0) + 1
        key = live_signal_key(rec, stf)
        if key is not None:
            live_keys.add(key)
        if coh != "POLICY" or key is None:
            continue
        ep, _src = RH.config_epoch(book, rec.get("opened_at"), research=research)
        if ep is None:
            unassigned += 1
            continue
        per.setdefault(ep, []).append((key, float(rec.get("r_multiple") or 0.0)))
    th = PARITY_COPY
    epochs: list[EpochParity] = []
    for ep in sorted(per, key=lambda e: str(e)):
        items = per[ep]
        d = [abs(float(copy_by[k].r["base"]) - r) for k, r in items if k in copy_by]
        n, m = len(items), len(d)
        share = m / n if n else 0.0
        med = statistics.median(d) if d else float("nan")
        gated = n >= int(th["min_policy_entries_per_epoch"])
        passed = (share >= float(th["min_same_bar_side_share"]) and med <= float(th["max_median_abs_dr"])) if gated else None
        epochs.append(EpochParity(ep, n, m, share, med, gated, passed))
    gated = [e for e in epochs if e.gated]
    verdict = PARITY_UNPROVEN if not gated else (PASS if all(e.passed for e in gated) else PARITY_FAIL)
    extra = sum(1 for k in copy_by if k not in live_keys)
    return ParityB(variant_id=variant_id, verdict=verdict, epochs=epochs, cohorts=cohorts, copy_extra=extra,
                   explained_rejections=len(list(explained_rejections)), unassigned_epoch=unassigned)


def mark_parity(rows: Sequence[Mapping[str, Any]], results: Mapping[str, ParityB]) -> list[dict[str, Any]]:
    """Lider tablosu/kapı satırlarına `PARITY_FAIL` işareti: (b)'si PASS olmayan kopyanın (K_*) satırları uygunluk
    dışı (`eligible=False`, `parity` = hüküm). Kopya olmayan satırlar değişmez."""
    out = []
    for r in rows:
        vid = str(r.get("variant_id") or r.get("unit") or "")
        row = dict(r)
        if vid in BOOK_DIR:
            pb = results.get(vid)
            verdict = pb.verdict if pb is not None else PARITY_UNPROVEN
            row["parity"] = verdict
            row["eligible"] = bool(row.get("eligible", True)) and verdict == PASS
            if verdict != PASS:
                row.setdefault("flags", [])
                row["flags"] = list(row["flags"]) + [PARITY_FAIL if verdict == PARITY_FAIL else PARITY_UNPROVEN]
        out.append(row)
    return out


__all__ = ["BOOK_DIR", "PARITY_COPY", "PARITY_FAIL", "PARITY_KERNEL", "PARITY_UNPROVEN", "PASS", "EpochParity", "ParityA",
           "ParityB", "RefTrade", "compare_a", "compare_b", "live_cohort", "live_signal_key", "mark_parity",
           "reference_run"]
