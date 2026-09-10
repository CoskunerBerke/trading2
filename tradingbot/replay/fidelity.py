"""Replay sadakat kosumu — defterin KENDI kapanmis islemlerini gercek tarihsel mumlarla yeniden uretir.

Amac tek: strateji degisikliklerini olcmeden ONCE, replay'in botun kendi kararlarini ve nakit
sonucunu belgelenmis tolerans icinde tekrar uretebildigini KANITLAMAK. Temel yoksa sonraki her
karsilastirma degersizdir.

Tasarim kurallari
-----------------
* ARITMETIK YENIDEN YAZILMAZ. Giris/cikis/komisyon/funding/likidasyon hesabi gercek
  `FuturesLedgerV2` uzerinden yurur (`accounting/`), replay yalnizca ona mum besler. Sadakat
  testinin butun anlami budur.
* ILERI BAKIS YOK. Yalnizca `opened_at` dakikasindan SONRA acilan mumlar beslenir; giris mumunun
  giris oncesi bolumu HIC gorulmez.
* AYAR UYDURULMAZ. Komisyon, kayma, tp1 orani, basa-bas esigi ve filtreler defter anlik
  goruntusunden okunur (`replay_config_from_ledger`).

Canli gozlem modeli (sadakatin sinirini burasi belirler)
-------------------------------------------------------
Canli motorun stop/TP tetigi iki farkli akistan gelir: ~60 sn'lik `exit_check` YALNIZ son fiyati
bilir (bar uclari YOK, `tick_kind=last_only`), tur ise olusmakta olan 1h barin uclarini da verir.
Uretim yolunun %79'u `last_only` oldugu icin canli MFE/MAE gercek bar ici uclari SISTEMATIK OLARAK
eksik olcer. Replay 1m mumla gercek yolu gordugunden bu iki alanda replay > canli beklenir; bu bir
hata degil, canli olcumun bilinen kusurudur (raporda ayri bashk).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..accounting.fees import FeeSchedule
from ..accounting.futures_ledger import FuturesLedgerV2
from ..accounting.liquidation import LiquidationParams
from ..accounting.models import AmountType, SizeSpec, TickData
from ..accounting.slippage import SlippageModel
from ..core import D, ZERO, from_iso, iso

log = logging.getLogger(__name__)

#: Desteklenen replay cozunurlukleri → milisaniye.
INTERVAL_MS: dict[str, int] = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}

#: `bars_held` sayaci canli motorda 4h bar ilerlemesinde artar.
BAR_ADVANCE_MS = 14_400_000

#: Hedef geometrisi: T1 = giris + 2R, T2 = giris + 3R (R = |giris − ilk stop|). Uretim kayitlariyla
#: dogrulandi (entry_snapshot, position_path ve gerceklesen TP fill fiyatlari birebir ortusuyor).
TARGET_R_MULTIPLES: tuple[Decimal, ...] = (Decimal("2"), Decimal("3"))


# --------------------------------------------------------------------------- mum kaynagi
@dataclass(frozen=True)
class Bar:
    """Tek mum. `open_ms` acilis, `close_ms` kapanis (dahil) zaman damgasi."""
    open_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    close_ms: int

    @property
    def close_dt(self) -> datetime:
        return datetime.fromtimestamp((self.close_ms + 1) / 1000, tz=timezone.utc)

    @property
    def open_dt(self) -> datetime:
        """Barin ACILIS ani. Defter, bar uclarini yalnizca bar pozisyonun omru icinde acilmissa
        kullanir (bar provenansi); bu deger olmadan uclar SESSIZCE dusurulur ve harness kendi
        29/29 cikis yeniden uretimini kaybeder — olculdu: 26/29'a duser."""
        return datetime.fromtimestamp(self.open_ms / 1000, tz=timezone.utc)


BarFetch = Callable[[str, str, int, int], Sequence[Bar]]


def _rows_to_bars(rows: Iterable[Sequence[Any]]) -> list[Bar]:
    out: list[Bar] = []
    for r in rows:
        r = list(r)
        if len(r) < 7:
            continue
        out.append(Bar(open_ms=int(r[0]), open=D(str(r[1])), high=D(str(r[2])), low=D(str(r[3])),
                       close=D(str(r[4])), close_ms=int(r[6])))
    return out


class BinanceBars:
    """USDⓈ-M `fapi` mum kaynagi; ham kline satirlarini diske onbellekler (deterministik parcalama).

    Bot USDⓈ-M perpetual isler ve sembollerin cogu (NVDA, SPY, CL, BZ, NATGAS, XPD...) spot'ta YOK,
    bu yuzden kaynak daima `fapi`dir.
    """

    base_url = "https://fapi.binance.com"

    def __init__(self, cache_dir: Path | str, *, limit: int = 1500, http: Any = None):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.limit = int(limit)
        self._http = http
        self.stats = {"cache_hit": 0, "fetch": 0}

    @property
    def http(self) -> Any:
        if self._http is None:
            from ..market.http import HttpClient
            self._http = HttpClient(self.base_url)
        return self._http

    def _chunk(self, raw: str, interval: str, start_ms: int) -> list[list[Any]]:
        path = self.cache_dir / f"{raw}_{interval}_{start_ms}_{self.limit}.json"
        if path.exists():
            self.stats["cache_hit"] += 1
            return json.loads(path.read_text(encoding="utf-8"))
        rows = self.http.get("/fapi/v1/klines", params={"symbol": raw, "interval": interval,
                                                        "limit": self.limit, "startTime": start_ms}, weight=5)
        rows = [list(r) for r in (rows or [])]
        self.stats["fetch"] += 1
        path.write_text(json.dumps(rows), encoding="utf-8")
        return rows

    def bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        """[start_ms, end_ms] araligindaki mumlar (acilis zamanina gore)."""
        step = INTERVAL_MS[interval]
        raw = symbol.split(":")[0].replace("/", "").upper()
        cur = start_ms - (start_ms % step)
        out: list[Bar] = []
        seen: set[int] = set()
        while cur <= end_ms:
            rows = self._chunk(raw, interval, cur)
            if not rows:
                break
            for b in _rows_to_bars(rows):
                if b.open_ms not in seen and start_ms <= b.open_ms <= end_ms:
                    seen.add(b.open_ms)
                    out.append(b)
            last = int(rows[-1][0])
            if last < cur:
                break
            cur = last + step
        out.sort(key=lambda b: b.open_ms)
        return out

    def funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, Decimal]]:
        """Gercek tarihsel funding oranlari [(fundingTime_ms, rate)]. Onbellekli."""
        raw = symbol.split(":")[0].replace("/", "").upper()
        path = self.cache_dir / f"{raw}_funding_{start_ms}_{end_ms}.json"
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))
        else:
            rows = self.http.get("/fapi/v1/fundingRate", params={"symbol": raw, "startTime": start_ms,
                                                                 "endTime": end_ms, "limit": 1000}, weight=1)
            rows = list(rows or [])
            path.write_text(json.dumps(rows), encoding="utf-8")
        return [(int(r["fundingTime"]), D(str(r["fundingRate"]))) for r in rows]


class MemoryBars:
    """Testler icin ag'siz mum kaynagi: {(symbol, interval): [Bar, ...]}."""

    def __init__(self, data: Mapping[tuple[str, str], Sequence[Bar]], funding: Mapping[str, Sequence[tuple[int, Decimal]]] | None = None):
        self._data = {k: list(v) for k, v in data.items()}
        self._funding = {k: list(v) for k, v in (funding or {}).items()}

    def bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        return [b for b in self._data.get((symbol, interval), []) if start_ms <= b.open_ms <= end_ms]

    def funding_rates(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, Decimal]]:
        return [(t, r) for t, r in self._funding.get(symbol, []) if start_ms <= t <= end_ms]


def funding_lookup_from(rates: Sequence[tuple[int, Decimal]], *, tolerance_ms: int = 300_000):
    """Gercek funding gecmisinden `RateLookup` uretir; settlement'a ±`tolerance_ms` icinde oran yoksa None.

    Kayitlar venue'nun `fundingRate` gecmisinden geldigi icin alinti DOGRULANMIS isaretlenir —
    defterin `require_verified` kurali altinda donemi kapatabilmesi icin gereklidir.
    """
    table = sorted(rates)

    def _lookup(_symbol: str, when: datetime):
        target = int(when.timestamp() * 1000)
        best: Decimal | None = None
        best_d = tolerance_ms + 1
        for t, rate in table:
            d = abs(t - target)
            if d <= tolerance_ms and d < best_d:
                best, best_d = rate, d
        return None if best is None else {"rate": best, "source": "venue_history", "verified": True}

    return _lookup


def funding_settlements_from(rates: Sequence[tuple[int, Decimal]]):
    """Venue'nun GERCEK settlement zamanlarindan `settlement_source` uretir (sabit 8 saat YOK)."""
    stamps = sorted(int(t) for t, _ in rates)

    def _source(_symbol: str, start: datetime, end: datetime) -> list[datetime]:
        lo, hi = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        return [datetime.fromtimestamp(t / 1000, tz=timezone.utc) for t in stamps if lo < t <= hi]

    return _source


# --------------------------------------------------------------------------- plan cikarimi
@dataclass
class TradePlan:
    """Kapanmis bir defter kaydindan geri cikarilan KARAR ANI plani + kayitli sonuc."""
    trade_id: str
    symbol: str
    side: str
    ref_entry: Decimal            # karar anindaki piyasa fiyati (fill'in referansi)
    initial_stop: Decimal
    targets: list[Decimal]
    target_source: str            # "recorded" | "derived"
    target_max_dev_pct: float     # kayitli hedef varsa turetilenle farki (kalibrasyon kanıtı)
    leverage: int
    requested_notional: Decimal
    amount_type: str
    opened_at: datetime
    closed_at: datetime
    # kayitli sonuc
    rec_exit_reason: str
    rec_exit_price: Decimal | None
    rec_r_multiple: Decimal
    rec_net_pnl: Decimal
    rec_bars_held: int
    rec_mfe_pct: Decimal
    rec_mae_pct: Decimal
    rec_quantity: Decimal
    rec_entry_fill: Decimal
    rec_entry_fee: Decimal
    rec_liquidation_price: Decimal | None
    rec_funding: Decimal
    features: dict = field(default_factory=dict)

    @property
    def hold_hours(self) -> float:
        return (self.closed_at - self.opened_at).total_seconds() / 3600.0


def derive_targets(ref_entry: Decimal, initial_stop: Decimal, side: str,
                   multiples: Sequence[Decimal] = TARGET_R_MULTIPLES) -> list[Decimal]:
    """Plan geometrisi: R = |giris − stop|, hedefler giristen 2R/3R uzakta (yon isaretli)."""
    risk = abs(D(ref_entry) - D(initial_stop))
    sign = Decimal("1") if str(side).upper() in ("LONG", "BUY") else Decimal("-1")
    return [D(ref_entry) + sign * m * risk for m in multiples]


def _targets_from_fills(rec: Mapping[str, Any], initial_stop: Decimal, side: str) -> list[Decimal] | None:
    """TP fill'i olan islemlerde hedef fiyati KAYITLIDIR: defter hedefi tam hedef fiyatindan doldurur.

    Ilk TP fill'inden plan girisi geri cozulur (T1 = E0 + 2R, R = E0 − S → E0 = (T1 + 2S)/3) ve
    kalan hedefler ayni geometriden uretilir. Bu, karar ani verisini OKUMAKTIR; sonuca bakmak degildir.
    """
    tp = next((f for f in rec.get("fills", []) if str(f.get("kind", "")).startswith("hedef")), None)
    if tp is None:
        return None
    t1 = D(tp["price"])
    m1 = TARGET_R_MULTIPLES[0]
    sign = Decimal("1") if str(side).upper() in ("LONG", "BUY") else Decimal("-1")
    # t1 = e0 + sign*m1*(e0 − s)*sign_risk  →  risk pozitif oldugundan: e0 = (t1 + sign*m1*s) / (1 + sign*m1*sign)
    e0 = (t1 + m1 * initial_stop) / (Decimal("1") + m1) if sign > 0 else (t1 - m1 * initial_stop) / (Decimal("1") - m1)
    return derive_targets(e0, initial_stop, side)


def plan_from_record(rec: Mapping[str, Any], recorded_targets: Sequence[float] | None = None) -> TradePlan:
    """Defter `history` kaydindan plan cikarir.

    Hedef kaynagi oncelik sirasi: (1) `position_path`/`entry_snapshot` kaydi, (2) gerceklesen TP fill
    fiyati, (3) geometri (giris ± 2R/3R). (3) yalnizca hedefe hic dokunmamis islemlerde kalir.
    """
    feats = dict(rec.get("features") or {})
    entry_fill = next((f for f in rec.get("fills", []) if f.get("kind") == "entry"), None)
    if entry_fill is None:
        raise ValueError(f"{rec.get('id')}: giris fill'i yok")
    ref_entry = D(entry_fill["ref_price"])
    stop_raw = feats.get("initial_stop")
    if stop_raw is None:
        raise ValueError(f"{rec.get('id')}: initial_stop yok")
    initial_stop = D(str(stop_raw))
    derived = derive_targets(ref_entry, initial_stop, rec["side"])
    from_fills = _targets_from_fills(rec, initial_stop, rec["side"])
    if recorded_targets:
        targets, source = [D(str(t)) for t in recorded_targets], "path"
    elif from_fills is not None:
        targets, source = from_fills, "fill"
    else:
        targets, source = derived, "derived"
    dev = max((abs(a - b) / b * 100 for a, b in zip(targets, derived) if b != 0), default=Decimal("0"))
    max_dev = float(dev)
    return TradePlan(
        trade_id=str(rec["id"]), symbol=str(rec["symbol"]), side=str(rec["side"]), ref_entry=ref_entry,
        initial_stop=initial_stop, targets=targets, target_source=source, target_max_dev_pct=max_dev,
        leverage=int(rec.get("leverage") or 1), requested_notional=D(rec["requested_notional"]),
        amount_type=str(rec.get("amount_type") or "NOTIONAL"), opened_at=from_iso(rec["opened_at"]),
        closed_at=from_iso(rec["closed_at"]), rec_exit_reason=str(rec["exit_reason"]),
        rec_exit_price=D(rec["exit_price"]) if rec.get("exit_price") is not None else None,
        rec_r_multiple=D(rec["r_multiple"]), rec_net_pnl=D(rec["net_pnl"]), rec_bars_held=int(rec.get("bars_held") or 0),
        rec_mfe_pct=D(rec.get("mfe_pct", 0)), rec_mae_pct=D(rec.get("mae_pct", 0)), rec_quantity=D(rec["quantity"]),
        rec_entry_fill=D(entry_fill["price"]), rec_entry_fee=D(rec["entry_fee"]),
        rec_liquidation_price=D(rec["liquidation_price"]) if rec.get("liquidation_price") is not None else None,
        rec_funding=D(rec.get("funding", 0)), features=feats)


def path_targets_index(path_lines: Iterable[str]) -> dict[str, list[float]]:
    """`position_path.jsonl` → {trade_id: targets}. Ilk snapshot yeterli (hedefler degismez)."""
    out: dict[str, list[float]] = {}
    for line in path_lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        tid = r.get("trade_id")
        if tid and tid not in out and r.get("targets"):
            out[tid] = [float(t) for t in r["targets"]]
    return out


# --------------------------------------------------------------------------- replay yapilandirmasi
@dataclass
class ReplayConfig:
    """Defter anlik goruntusunden okunan muhasebe ayarlari. Replay bunlari ASLA kendi uydurmaz."""
    fees: FeeSchedule
    slippage: SlippageModel
    liq_params: LiquidationParams
    tp1_fraction: Decimal
    breakeven_at_mfe_r: Decimal
    worst_case: bool
    tp_maker: bool
    starting_equity: Decimal = Decimal("100000")   # tek islem izole edildigi icin yalnizca marj kapisini acar

    def new_ledger(self, *, breakeven_at_mfe_r: Decimal | None = None) -> FuturesLedgerV2:
        return FuturesLedgerV2(self.starting_equity, max_positions=1, enforce_position_cap=False, fees=self.fees,
                               slippage=self.slippage, liq_params=self.liq_params, tp1_fraction=self.tp1_fraction,
                               breakeven_at_mfe_r=self.breakeven_at_mfe_r if breakeven_at_mfe_r is None else breakeven_at_mfe_r,
                               worst_case=self.worst_case, tp_maker=self.tp_maker, allow_shrink=False)


def replay_config_from_ledger(d: Mapping[str, Any]) -> ReplayConfig:
    """`futures_ledger.json` → ReplayConfig (uretimle birebir ayni sayilar)."""
    return ReplayConfig(
        fees=FeeSchedule.from_dict(dict(d.get("fees") or {})),
        slippage=SlippageModel.from_dict(dict(d.get("slippage") or {})),
        liq_params=LiquidationParams(**{k: v for k, v in (d.get("liq_params") or {}).items()
                                        if k in ("liq_fee_pct", "fee_cushion_pct", "use_brackets")}),
        tp1_fraction=D(d.get("tp1_fraction", "0.5")),
        breakeven_at_mfe_r=D(d.get("breakeven_at_mfe_r", "0")),
        worst_case=bool(d.get("worst_case", True)),
        tp_maker=bool(d.get("tp_maker", False)))


# --------------------------------------------------------------------------- replay
@dataclass
class ReplayOutcome:
    """Tek islemin replay sonucu + teshis alanlari."""
    trade_id: str
    interval: str
    closed: bool
    mark_mode: str = "close"
    exit_reason: str | None = None
    exit_price: Decimal | None = None
    r_multiple: Decimal | None = None
    net_pnl: Decimal | None = None
    bars_held: int = 0
    mfe_pct: Decimal | None = None
    mae_pct: Decimal | None = None
    closed_at: datetime | None = None
    funding: Decimal = ZERO
    # giris paritesi (mumdan bagimsiz, saf muhasebe)
    entry_fill: Decimal | None = None
    entry_fee: Decimal | None = None
    quantity: Decimal | None = None
    liquidation_price: Decimal | None = None
    # teshis
    n_bars: int = 0
    ambiguous_bars: int = 0            # ayni mumda hem stop hem sonraki hedef menzilde
    ambiguity_decided: bool = False    # kapanisa yol acan mum belirsizdi (worst_case devreye girdi)
    #: MFE basa-bas AYNI mumda hem kuruldu hem vuruldu — bar ici siralamanin gercekten onemli
    #: oldugu tek durum bu cikti; klasik stop+hedef belirsizligi bu ornekte HIC olusmadi.
    be_armed_and_hit_same_bar: bool = False
    reject_reason: str = ""
    note: str = ""

    @property
    def hold_hours(self) -> float | None:
        return None if self.closed_at is None else self.closed_at.timestamp()


def _bar_advance_flags(bars: Sequence[Bar], opened_at: datetime) -> list[bool]:
    """Canli motor `bars_held`i 4h bar ilerlemesinde artirir; ayni siniri replay'de de kullaniyoruz."""
    prev = int(opened_at.timestamp() * 1000) // BAR_ADVANCE_MS
    flags: list[bool] = []
    for b in bars:
        cur = b.open_ms // BAR_ADVANCE_MS
        flags.append(cur != prev)
        prev = cur
    return flags


def replay_trade(plan: TradePlan, source: Any, cfg: ReplayConfig, *, interval: str = "1m",
                 horizon_hours: float = 72.0, use_funding: bool = True, mark_mode: str = "close",
                 be_mfe_active_from: datetime | None = None) -> ReplayOutcome:
    """Plani gercek mumlarla yeniden kos. Giris `opened_at`ta, ilk mum `opened_at`i takip eden ilk mumdur.

    Kapanis kayitli kapanistan SONRA da olabilir; bu yuzden ufuk `closed_at + horizon_hours`a kadar
    uzatilir ve kapanmazsa `closed=False` doner (bu da bir bulgudur, gizlenmez).

    `mark_mode` gozlem noktasi varsayimidir; defter bosluktan gecen (gap-through) stop'u MARK'tan
    doldurdugu icin dolum fiyati buna duyarlidir:
      * "close"   — mum kapanisi. Notr, ileri bakissiz varsayilan.
      * "adverse" — mumun aleyhte ucu (LONG icin low, SHORT icin high). Canli botun 60 sn'lik
        yoklamasinin mumun EN KOTU noktasina denk geldigi kotumser sinir.
    Ikisi birlikte kayitli sonucun icinde durdugu araligi verir; hicbiri kayda UYDURULMAZ.

    `be_mfe_active_from` verilirse MFE tabanli basa-bas kurali YALNIZ o andan sonraki mumlarda
    calisir. Bu bir ayar oynamasi DEGIL, dagitim tarihidir: kural 8c99b8f ile geldi ve uretimde ilk
    kez 2026-09-09T11:24:45Z'de tetiklendi; ondan onceki islemler bu kural OLMADAN yasandi.
    """
    step = INTERVAL_MS[interval]
    open_ms = int(plan.opened_at.timestamp() * 1000)
    start_ms = ((open_ms + step - 1) // step) * step          # giris mumunun giris ONCESI kismi HIC gorulmez
    end_ms = int((plan.closed_at + timedelta(hours=horizon_hours)).timestamp() * 1000)
    bars = source.bars(plan.symbol, interval, start_ms, end_ms)
    out = ReplayOutcome(trade_id=plan.trade_id, interval=interval, closed=False, mark_mode=mark_mode, n_bars=len(bars))
    if not bars:
        out.note = "NO_BARS"
        return out

    led = cfg.new_ledger(breakeven_at_mfe_r=ZERO if be_mfe_active_from is not None else None)
    size = SizeSpec(amount=plan.requested_notional, amount_type=AmountType(plan.amount_type), leverage=plan.leverage)
    # `filters` VERILMEZ: uretimde de dogrulanmamis varsayilan filtre kullanildi (price_tick 0.01,
    # qty_step 0.001, source="default"). Burada baska bir filtre secmek sadakati bozar.
    pos = led.open(plan.symbol, plan.side, plan.ref_entry, size, stop=plan.initial_stop, targets=plan.targets,
                   features=dict(plan.features), now=plan.opened_at, setup_type=str(plan.features.get("setup_type") or ""))
    if pos is None:
        out.reject_reason = led.last_reject_reason
        out.note = "OPEN_REJECTED"
        return out
    out.entry_fill, out.entry_fee, out.quantity = pos.entry_avg, pos.entry_fee, pos.qty
    out.liquidation_price = pos.liquidation_price

    lookup = None
    if use_funding and hasattr(source, "funding_rates"):
        rates = source.funding_rates(plan.symbol, open_ms - 8 * 3_600_000, end_ms)
        lookup = funding_lookup_from(rates) if rates else None
        if rates:
            led.funding.settlement_source = funding_settlements_from(rates)

    flags = _bar_advance_flags(bars, plan.opened_at)
    for b, advance in zip(bars, flags):
        if be_mfe_active_from is not None:
            led.breakeven_at_mfe_r = cfg.breakeven_at_mfe_r if b.close_dt >= be_mfe_active_from else ZERO
        stop_now = pos.stop
        tgt_now = pos.targets[pos.targets_hit] if pos.targets_hit < len(pos.targets) else None
        ambiguous = bool(stop_now is not None and tgt_now is not None
                         and b.low <= stop_now <= b.high and b.low <= tgt_now <= b.high)
        out.ambiguous_bars += int(ambiguous)
        had_be = bool(pos.meta.get("be_by_mfe"))
        if mark_mode == "adverse":
            mark = b.low if str(plan.side).upper() in ("LONG", "BUY") else b.high
        else:
            mark = b.close
        td = TickData(last=b.close, mark=mark, high=b.high, low=b.low, ts=iso(b.close_dt),
                      bar_open=iso(b.open_dt))
        recs = led.tick({plan.symbol: td}, now_utc=b.close_dt, funding_rate_lookup=lookup, bar_advance=advance)
        if recs:
            r = recs[-1]
            out.closed = True
            out.ambiguity_decided = ambiguous
            out.be_armed_and_hit_same_bar = (not had_be and bool(pos.meta.get("be_by_mfe"))
                                             and exit_family(r.exit_reason) == "BE_STOP")
            out.exit_reason, out.exit_price = r.exit_reason, r.exit_price
            out.r_multiple, out.net_pnl = r.r_multiple, r.net_pnl
            out.bars_held, out.mfe_pct, out.mae_pct = r.bars_held, r.mfe_pct, r.mae_pct
            out.closed_at, out.funding = b.close_dt, r.funding
            return out
    live = led.positions.get(plan.symbol)
    if live is not None:
        out.bars_held, out.mfe_pct, out.mae_pct = live.bars_held, live.mfe_pct, live.mae_pct
        out.funding = live.funding_received - live.funding_paid
        out.note = "STILL_OPEN"
    return out


# --------------------------------------------------------------------------- karsilastirma
_EXIT_ALIASES = {"stop": "STOP", "başa-baş stop": "BE_STOP", "basa-bas stop": "BE_STOP",
                 "hedef1": "TP1", "hedef2": "TP2", "likidasyon": "LIQ", "manuel": "MANUAL"}


def exit_family(reason: str | None) -> str:
    """Kapanis nedenini ailesine indirger; `stop` ve `başa-baş stop` AYRI ailelerdir (ekonomileri farklidir)."""
    if not reason:
        return "OPEN"
    return _EXIT_ALIASES.get(str(reason).strip().lower(), str(reason).upper())


def _pct_diff(a: Decimal | None, b: Decimal | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return float((a - b) / abs(b) * 100)


def compare(plan: TradePlan, out: ReplayOutcome) -> dict[str, Any]:
    """Replay ↔ kayit farklari. Mutlak ve goreli farklar birlikte verilir; hicbiri gizlenmez."""
    row: dict[str, Any] = {
        "trade_id": plan.trade_id, "symbol": plan.symbol, "side": plan.side, "interval": out.interval,
        "mark_mode": out.mark_mode,
        "opened_at": iso(plan.opened_at), "hold_h": round(plan.hold_hours, 2),
        "target_source": plan.target_source, "target_dev_pct": round(plan.target_max_dev_pct, 6),
        "rec_exit": exit_family(plan.rec_exit_reason), "rep_exit": exit_family(out.exit_reason),
        "exit_match": exit_family(plan.rec_exit_reason) == exit_family(out.exit_reason),
        "rec_r": float(plan.rec_r_multiple), "rep_r": None if out.r_multiple is None else float(out.r_multiple),
        "rec_net": float(plan.rec_net_pnl), "rep_net": None if out.net_pnl is None else float(out.net_pnl),
        "rec_exit_px": None if plan.rec_exit_price is None else float(plan.rec_exit_price),
        "rep_exit_px": None if out.exit_price is None else float(out.exit_price),
        "rec_bars": plan.rec_bars_held, "rep_bars": out.bars_held,
        "rec_mfe": float(plan.rec_mfe_pct), "rep_mfe": None if out.mfe_pct is None else float(out.mfe_pct),
        "rec_mae": float(plan.rec_mae_pct), "rep_mae": None if out.mae_pct is None else float(out.mae_pct),
        "rec_funding": float(plan.rec_funding), "rep_funding": float(out.funding),
        "closed": out.closed, "n_bars": out.n_bars, "ambiguous_bars": out.ambiguous_bars,
        "ambiguity_decided": out.ambiguity_decided, "be_same_bar": out.be_armed_and_hit_same_bar,
        "note": out.note or out.reject_reason,
        # giris paritesi — mumdan BAGIMSIZ, saf muhasebe; burada sapma varsa replay degil defter konusur
        "entry_px_match": out.entry_fill is not None and out.entry_fill == plan.rec_entry_fill,
        "qty_match": out.quantity is not None and out.quantity == plan.rec_quantity,
        "entry_fee_match": out.entry_fee is not None and out.entry_fee == plan.rec_entry_fee,
        "liq_match": (out.liquidation_price is not None and plan.rec_liquidation_price is not None
                      and abs(out.liquidation_price - plan.rec_liquidation_price) <= Decimal("1e-9")),
    }
    row["d_r"] = None if out.r_multiple is None else float(out.r_multiple - plan.rec_r_multiple)
    row["d_net"] = None if out.net_pnl is None else float(out.net_pnl - plan.rec_net_pnl)
    # FUNDING'DEN ARINDIRILMIS FARK: canli funding, o anki tek bir orani butun kacirilan settlement'lara
    # uyguladigi (ve oran hic gelmediyse SIFIR yazdigi) icin gercek tarihsel funding ile birebir tutmaz.
    # Fiyat yolu + komisyon sadakatini ayri gormek icin funding iki taraftan da cikarilir.
    risk = None
    if plan.rec_r_multiple != 0:
        risk = abs(plan.rec_net_pnl / plan.rec_r_multiple)
    row["risk_usdt"] = None if risk is None else float(risk)
    row["d_funding"] = float(out.funding - plan.rec_funding)
    if out.net_pnl is None:
        row["d_net_exfund"] = row["d_r_exfund"] = None
    else:
        dn = (out.net_pnl - out.funding) - (plan.rec_net_pnl - plan.rec_funding)
        row["d_net_exfund"] = float(dn)
        row["d_r_exfund"] = None if not risk else float(dn / risk)
    row["d_exit_px_pct"] = _pct_diff(out.exit_price, plan.rec_exit_price)
    row["d_bars"] = out.bars_held - plan.rec_bars_held
    row["d_mfe"] = None if out.mfe_pct is None else float(out.mfe_pct - plan.rec_mfe_pct)
    row["d_mae"] = None if out.mae_pct is None else float(out.mae_pct - plan.rec_mae_pct)
    row["d_close_h"] = None if out.closed_at is None else round((out.closed_at - plan.closed_at).total_seconds() / 3600, 3)
    return row


def summarise(rows: Sequence[Mapping[str, Any]], *, r_tol: float = 0.05, net_tol: float = 0.02) -> dict[str, Any]:
    """Dagilim ozeti: eslesme sayilari ve mutlak fark yuzdelikleri."""
    n = len(rows)
    exit_ok = sum(1 for r in rows if r["exit_match"])
    r_ok = sum(1 for r in rows if r["d_r"] is not None and abs(r["d_r"]) <= r_tol)
    rx_ok = sum(1 for r in rows if r.get("d_r_exfund") is not None and abs(r["d_r_exfund"]) <= r_tol)
    net_ok = sum(1 for r in rows if r["d_net"] is not None and abs(r["d_net"]) <= net_tol)
    entry_ok = sum(1 for r in rows if r["entry_px_match"] and r["qty_match"] and r["entry_fee_match"])

    def _q(key: str, p: float) -> float | None:
        vals = sorted(abs(r[key]) for r in rows if r.get(key) is not None)
        if not vals:
            return None
        i = min(len(vals) - 1, max(0, int(math.ceil(p * len(vals))) - 1))
        return round(vals[i], 6)

    return {
        "n": n, "exit_match": exit_ok, "r_within": r_ok, "r_exfund_within": rx_ok, "net_within": net_ok,
        "entry_parity": entry_ok,
        "abs_d_r_exfund_p50": _q("d_r_exfund", 0.5), "abs_d_r_exfund_p90": _q("d_r_exfund", 0.9),
        "abs_d_r_exfund_max": _q("d_r_exfund", 1.0),
        "abs_d_funding_max": _q("d_funding", 1.0),
        "closed": sum(1 for r in rows if r["closed"]),
        "ambiguous_trades": sum(1 for r in rows if r["ambiguous_bars"] > 0),
        "ambiguity_decided": sum(1 for r in rows if r["ambiguity_decided"]),
        "be_same_bar": sum(1 for r in rows if r.get("be_same_bar")),
        "abs_d_r_p50": _q("d_r", 0.5), "abs_d_r_p90": _q("d_r", 0.9), "abs_d_r_max": _q("d_r", 1.0),
        "abs_d_net_p50": _q("d_net", 0.5), "abs_d_net_p90": _q("d_net", 0.9), "abs_d_net_max": _q("d_net", 1.0),
        "abs_d_exit_px_pct_p50": _q("d_exit_px_pct", 0.5), "abs_d_exit_px_pct_max": _q("d_exit_px_pct", 1.0),
        "abs_d_mfe_p50": _q("d_mfe", 0.5), "abs_d_mfe_max": _q("d_mfe", 1.0),
        "abs_d_mae_p50": _q("d_mae", 0.5), "abs_d_mae_max": _q("d_mae", 1.0),
        "r_tol": r_tol, "net_tol": net_tol,
    }


def exit_fill_diagnosis(rec: Mapping[str, Any], source: Any, *, window_min: int = 30) -> dict[str, Any]:
    """Kayitli cikis fill'inin referans fiyatini gercek 1m mumlarda ARAR.

    Defter stop'u iki turlu doldurur: (a) `trig = stop` — mark stop'un guvenli tarafinda, fill tam
    stop seviyesinde (`ref_price == stop`); (b) `trig = mark` — bosluktan gecis, fill o anki GOZLEM
    fiyatindan. Yalniz (b) bir piyasa gozlemidir ve zaman damgasiyla tutarli OLMAK ZORUNDADIR.
    Donen `lag_min`, referans fiyatin ilk gorundugu 1m barin kayitli damgaya gore kaymasidir.
    """
    fill = next((f for f in reversed(rec.get("fills", [])) if f.get("kind") != "entry"), None)
    out: dict[str, Any] = {"trade_id": rec.get("id"), "symbol": rec.get("symbol"), "kind": None,
                           "class": "NO_FILL", "lag_min": None, "in_recorded_minute": None}
    if fill is None or fill.get("ref_price") is None or str(fill.get("kind", "")).startswith("hedef"):
        return out
    ref = D(fill["ref_price"])
    stop = D(str((rec.get("features") or {}).get("initial_stop"))) if (rec.get("features") or {}).get("initial_stop") else None
    out["kind"] = fill["kind"]
    at_stop = stop is not None and abs(ref - stop) <= abs(stop) * Decimal("1e-9")
    out["class"] = "AT_STOP" if at_stop else "GAP_THROUGH"
    if at_stop:
        return out                     # seviye fiyati; piyasada gozlenmis olmasi GEREKMEZ
    ts = from_iso(fill["ts"])
    t0 = int(ts.timestamp() * 1000) // 60_000 * 60_000
    bars = source.bars(rec["symbol"], "1m", t0 - 2 * 60_000, t0 + window_min * 60_000)
    hits = [(b.open_ms - t0) // 60_000 for b in bars if b.low <= ref <= b.high]
    out["in_recorded_minute"] = 0 in hits
    out["lag_min"] = min(hits, key=abs) if hits else None
    return out


def load_plans(ledger: Mapping[str, Any], path_targets: Mapping[str, Sequence[float]] | None = None) -> list[TradePlan]:
    """Defter anlik goruntusundeki butun kapanmis islemleri plana cevirir."""
    idx = dict(path_targets or {})
    out: list[TradePlan] = []
    for rec in ledger.get("history", []):
        try:
            out.append(plan_from_record(rec, idx.get(str(rec.get("id")))))
        except (ValueError, KeyError) as exc:
            log.warning("plan cikarilamadi %s: %s", rec.get("id"), exc)
    return out


__all__ = [
    "BAR_ADVANCE_MS", "Bar", "BinanceBars", "MemoryBars", "ReplayConfig", "ReplayOutcome", "TradePlan",
    "TARGET_R_MULTIPLES", "compare", "derive_targets", "exit_family", "exit_fill_diagnosis",
    "funding_lookup_from", "funding_settlements_from", "load_plans",
    "path_targets_index", "plan_from_record", "replay_config_from_ledger", "replay_trade", "summarise",
]
