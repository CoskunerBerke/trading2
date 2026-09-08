"""Yurutme hassasiyeti — sembol kuralinin PROVENANSI ve yurutulebilir teklif izi.

KUSUR (olculdu, 2026-09-08): `ExchangeRules.load()` ccxt `binance` SPOT marketlerini okur ve
markette bulunmayan sembolu SESSIZCE atlar (`if not m: continue`). Bu semboller
`exchange_rules.json`'a hic girmez; asagi akista `default_filters(...)` devreye girer ve
`SymbolFilters.price_tick` VARSAYILAN `0.01` olur. Fiyat verisi ise TradingView'den gelir
(`MarketData(source="tradingview", tv_exchange="BINANCE")`), yani fiyat kaynagi ile kural
kaynagi AYNI degildir.

Olculmus etki (F00037 TRX/USDT, 2026-09-08T13:51:19Z): referans 0.3386, tick 0.01'e AGRESIF
yukari yuvarlanarak 0.34 doldu. Stop ve hedefler tam hassasiyette kaldigi icin plan
geometrisi 2.0000R -> 1.2775R'ye dustu; birim risk %31.72 buyudu. F00038 NATGAS/USDT'de
referans 2.932 -> 2.94 (2.0000R -> 1.7001R). Defterdeki `fills[].slippage` bu tick
yuvarlamasini KAYMA olarak kaydeder, ayri bir kalem olarak GORUNMEZ.

BU MODUL NE YAPMAZ
  * Gorunen ondalik basamaklardan ya da grafikten "gercek borsa tick'i" UYDURMAZ.
  * Eksik metadata'yi bir varsayilana DUSURMEZ.
  * Acik pozisyonlarin tarihsel girisini/miktarini/riskini/stop'unu/hedeflerini DEGISTIRMEZ.
  * Koruyucu cikislari devre disi BIRAKMAZ (cikis yolu `tick()`tir, bu modulden gecmez).
  * Asgari-R kabul esigi EKLEMEZ; 2R/3R ureticinin geometrisidir, kapi degildir.

SENTETIK ENSTRUMANLAR
  NATGAS/USDT, CL/USDT, BZ/USDT, MSFT/USDT, GOOGL/USDT, NVDA/USDT gibi hisse/emtia-benzeri
  semboller bir kripto borsasinin sozlesme spesifikasyonunu DEVRALAMAZ. Bunlar icin
  operatorun yazacagi, surumlenmis ve yururluk tarihli bir SIMULASYON spesifikasyonu gerekir.
  `SYNTHETIC_SPECS` bilerek BOSTUR: bu dosyadan otomatik bir kural TURETILMEZ.

GERCEK BINANCE USD-M SOZLESMESI icin dogru alan `PRICE_FILTER.tickSize`tir; `pricePrecision`
onun yerine KULLANILAMAZ (ayni sekilde `quantityPrecision` != `LOT_SIZE.stepSize`). Bu ancak
sembolun gercekten o sozlesmeye karsilik geldigi DOGRULANDIKTAN sonra kullanilir ve guncel
metadata ileri yonlu bir duzeltmeyi destekler; yururluk tarihi kaniti olmadan GECMIS kurallari
KANITLAMAZ.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .accounting.models import MarketType, SymbolFilters
from .core import D, ZERO
from .core.money import quantize_price, quantize_qty

#: Bu sozlesmenin surumu — teklif kayitlarina damgalanir.
PRECISION_CONTRACT_VERSION = "exec_precision_v1"

#: Kural sinifi.
VERIFIED_VENUE = "VERIFIED_VENUE"      # borsa metadata'sindan DOGRULANMIS
SYNTHETIC_SPEC = "SYNTHETIC_SPEC"      # operatorun yazdigi surumlenmis simulasyon spesifikasyonu
UNRESOLVED = "UNRESOLVED"              # kural YOK -> yeni giris ACILAMAZ

#: Giris reddi kodu (mevcut kapi kodlariyla ayni bicimde).
NO_ENTRY_UNRESOLVED_PRECISION = "UNRESOLVED_PRECISION"

#: Operator tarafindan doldurulur. BOS birakilmasi KASITLIDIR — bu modul kendiliginden
#: sentetik bir tick/step UYDURMAZ. Beklenen bicim:
#:   "NATGAS/USDT": {"spec_version": "...", "effective_from": "2026-..-..T..:..:..Z",
#:                   "price_tick": "0.001", "qty_step": "0.001", "min_notional": "5",
#:                   "contract": "SYNTHETIC_PAPER", "unit": "...", "multiplier": "1",
#:                   "authored_by": "...", "note": "..."}
SYNTHETIC_SPECS: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class RuleProvenance:
    """Kuralin NEREDEN geldigi. `rule_class` UNRESOLVED ise tick/step KULLANILAMAZ."""
    symbol: str
    market_type: str
    rule_class: str
    source: str = ""
    verified_at: str = ""
    contract: str = ""
    instrument_id: str = ""
    multiplier: str = ""
    reason: str = ""
    contract_version: str = PRECISION_CONTRACT_VERSION

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "market_type": self.market_type,
                "rule_class": self.rule_class, "source": self.source,
                "verified_at": self.verified_at, "contract": self.contract,
                "instrument_id": self.instrument_id, "multiplier": self.multiplier,
                "reason": self.reason, "contract_version": self.contract_version}

    @property
    def usable(self) -> bool:
        return self.rule_class in (VERIFIED_VENUE, SYNTHETIC_SPEC)


def _synthetic_filters(symbol: str, market_type: MarketType, spec: dict) -> SymbolFilters:
    return SymbolFilters(symbol=symbol, market_type=market_type,
                         price_tick=D(spec["price_tick"]), qty_step=D(spec["qty_step"]),
                         min_qty=D(spec.get("min_qty", spec["qty_step"])),
                         min_notional=D(spec.get("min_notional", "5")),
                         max_leverage=int(spec.get("max_leverage", 20)),
                         verified_at=str(spec.get("effective_from", "")),
                         source="synthetic:%s" % spec.get("spec_version", "?"))


def resolve_rule(symbol: str, *, cache: Any = None,
                 market_type: MarketType = MarketType.USDM_PERP,
                 synthetic_specs: dict[str, dict] | None = None,
                 ) -> tuple[SymbolFilters | None, RuleProvenance]:
    """Sembolun yurutme kuralini PROVENANSIYLA cozer. Varsayilana DUSMEZ.

    Sirayla: (1) dogrulanmis borsa metadata'si (`FiltersCache` icinde gercekten VAR ve
    `source != "default"`), (2) operatorun surumlenmis sentetik spesifikasyonu,
    (3) UNRESOLVED. Ucuncu durumda `filters` None doner ve cagiran YENI GIRIS ACAMAZ.
    """
    specs = SYNTHETIC_SPECS if synthetic_specs is None else synthetic_specs
    mt = market_type.value if hasattr(market_type, "value") else str(market_type)

    f = None
    if cache is not None and getattr(cache, "has", None) and cache.has(symbol, market_type):
        f = cache.get(symbol, market_type)
    if f is not None and str(getattr(f, "source", "")) not in ("", "default") and f.price_tick > 0:
        return f, RuleProvenance(symbol, mt, VERIFIED_VENUE, source=str(f.source),
                                 verified_at=str(getattr(f, "verified_at", "") or ""),
                                 contract=mt, instrument_id=symbol)

    spec = (specs or {}).get(symbol)
    if spec:
        missing = [k for k in ("spec_version", "effective_from", "price_tick", "qty_step",
                               "contract") if not spec.get(k)]
        if missing:
            return None, RuleProvenance(symbol, mt, UNRESOLVED,
                                        reason="SYNTHETIC_SPEC_INCOMPLETE:%s" % ",".join(missing))
        if D(spec["price_tick"]) <= 0 or D(spec["qty_step"]) <= 0:
            return None, RuleProvenance(symbol, mt, UNRESOLVED,
                                        reason="SYNTHETIC_SPEC_NONPOSITIVE")
        return _synthetic_filters(symbol, market_type, spec), RuleProvenance(
            symbol, mt, SYNTHETIC_SPEC, source="synthetic:%s" % spec["spec_version"],
            verified_at=str(spec["effective_from"]), contract=str(spec["contract"]),
            instrument_id=str(spec.get("instrument_id", symbol)),
            multiplier=str(spec.get("multiplier", "1")))

    why = "NO_VENUE_METADATA" if f is None else "UNVERIFIED_DEFAULT_FILTERS"
    return None, RuleProvenance(symbol, mt, UNRESOLVED, source=str(getattr(f, "source", "") or ""),
                                reason=why)


def affected_universe(symbols: list[str], *, cache: Any = None,
                      market_type: MarketType = MarketType.USDM_PERP,
                      synthetic_specs: dict[str, dict] | None = None) -> dict[str, Any]:
    """Hangi semboller yeni giris ACAMAZ — ve GEREKCESI. Rapor icindir, davranis DEGISTIRMEZ."""
    rows = []
    for s in symbols:
        f, prov = resolve_rule(s, cache=cache, market_type=market_type,
                               synthetic_specs=synthetic_specs)
        rows.append({"symbol": s, "rule_class": prov.rule_class,
                     "price_tick": str(f.price_tick) if f is not None else None,
                     "qty_step": str(f.qty_step) if f is not None else None,
                     "no_entry_reason": (None if prov.usable else NO_ENTRY_UNRESOLVED_PRECISION),
                     "detail": prov.reason, "provenance": prov.to_dict()})
    return {"contract_version": PRECISION_CONTRACT_VERSION,
            "n_symbols": len(rows),
            "n_entry_blocked": sum(1 for r in rows if r["no_entry_reason"]),
            "rows": rows}


@dataclass
class ExecutionProposal:
    """Planlanan geometriden YURUTULEBILIR geometriye tam iz. Defter buna gore doldurur."""
    symbol: str
    side: str
    provenance: RuleProvenance
    ref_price: Decimal
    slipped_price: Decimal
    executable_price: Decimal
    qty: Decimal
    stop: Decimal | None
    targets: list[Decimal] = field(default_factory=list)
    planned_entry: Decimal | None = None
    planned_stop: Decimal | None = None
    planned_targets: list[Decimal] = field(default_factory=list)
    blocked_reason: str = ""

    # ------------------------------------------------------------------ geometri
    @staticmethod
    def _r(entry: Decimal, stop: Decimal, px: Decimal, is_long: bool) -> Decimal | None:
        risk = (entry - stop) if is_long else (stop - entry)
        if risk <= 0:
            return None
        return ((px - entry) if is_long else (entry - px)) / risk

    def geometry(self) -> dict:
        is_long = self.side.upper() in ("LONG", "BUY")
        out: dict[str, Any] = {"is_long": is_long}
        if self.planned_entry is not None and self.planned_stop is not None:
            pr = (self.planned_entry - self.planned_stop) if is_long else (self.planned_stop - self.planned_entry)
            out["planned_unit_risk"] = pr
            out["planned_target_r"] = [self._r(self.planned_entry, self.planned_stop, t, is_long)
                                       for t in self.planned_targets]
        if self.stop is not None:
            er = (self.executable_price - self.stop) if is_long else (self.stop - self.executable_price)
            out["executable_unit_risk"] = er
            out["valid_geometry"] = er > 0
            out["executable_target_r"] = [self._r(self.executable_price, self.stop, t, is_long)
                                          for t in self.targets]
            out["risk_usdt"] = er * self.qty if er > 0 else ZERO
            if out.get("planned_unit_risk") and out["planned_unit_risk"] > 0:
                out["unit_risk_growth"] = er / out["planned_unit_risk"] - 1
        return out

    def to_dict(self) -> dict:
        g = self.geometry()
        return {"contract_version": PRECISION_CONTRACT_VERSION,
                "symbol": self.symbol, "side": self.side,
                "provenance": self.provenance.to_dict(),
                "ref_price": str(self.ref_price), "slipped_price": str(self.slipped_price),
                "executable_price": str(self.executable_price), "qty": str(self.qty),
                "stop": (str(self.stop) if self.stop is not None else None),
                "targets": [str(t) for t in self.targets],
                "planned_entry": (str(self.planned_entry) if self.planned_entry is not None else None),
                "blocked_reason": self.blocked_reason,
                "geometry": {k: (str(v) if isinstance(v, Decimal) else
                                 ([str(x) if isinstance(x, Decimal) else x for x in v]
                                  if isinstance(v, list) else v))
                             for k, v in g.items()}}


def build_proposal(*, symbol: str, side: str, ref_price, qty, stop=None, targets=None,
                   filters: SymbolFilters | None, provenance: RuleProvenance,
                   slippage=None, tick=None,
                   planned_entry=None) -> ExecutionProposal:
    """Referans fiyattan YURUTULEBILIR teklife tek yol: kayma -> tick -> miktar -> geometri.

    Yuvarlama YALNIZ girise ve miktara uygulanir; stop/hedefler tetik fiyatlaridir ve
    burada ikinci kez ALEYHTE kaydirilmaz. Kural cozulmemisse teklif BLOKELIdir.
    """
    is_long = side.upper() in ("LONG", "BUY")
    ref = D(ref_price)
    prop = ExecutionProposal(symbol=symbol, side=side, provenance=provenance, ref_price=ref,
                             slipped_price=ref, executable_price=ref, qty=D(qty),
                             stop=(D(stop) if stop is not None else None),
                             targets=[D(t) for t in (targets or [])],
                             planned_entry=(D(planned_entry) if planned_entry is not None else None),
                             planned_stop=(D(stop) if stop is not None else None),
                             planned_targets=[D(t) for t in (targets or [])])
    if not provenance.usable or filters is None:
        prop.blocked_reason = NO_ENTRY_UNRESOLVED_PRECISION
        return prop
    if ref <= 0:
        prop.blocked_reason = "BAD_PRICE"
        return prop

    open_side = "BUY" if is_long else "SELL"
    slipped = slippage.fill_price(ref, open_side, tick, is_market=True) if slippage else ref
    prop.slipped_price = D(slipped)
    # Yon duyarli, ALEYHTE tek yuvarlama (LONG yukari, SHORT asagi) — mevcut defter davranisi.
    prop.executable_price = quantize_price(prop.slipped_price, filters.price_tick, open_side,
                                           aggressive=True)
    prop.qty = quantize_qty(D(qty), filters.qty_step)
    if prop.qty <= 0:
        prop.blocked_reason = "STEP_ZERO_QTY"
        return prop
    g = prop.geometry()
    if prop.stop is not None and not g.get("valid_geometry", False):
        prop.blocked_reason = "INVALID_GEOMETRY_AFTER_ROUNDING"
    return prop


__all__ = ["PRECISION_CONTRACT_VERSION", "VERIFIED_VENUE", "SYNTHETIC_SPEC", "UNRESOLVED",
           "NO_ENTRY_UNRESOLVED_PRECISION", "SYNTHETIC_SPECS", "RuleProvenance",
           "ExecutionProposal", "resolve_rule", "affected_universe", "build_proposal"]
