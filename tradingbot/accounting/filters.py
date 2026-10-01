"""Sembol filtreleri (LOT_SIZE / PRICE_FILTER / MIN_NOTIONAL), önbellek, emir kuantizasyonu ve kaldıraç bracket'ları."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from ..core import D, ZERO, atomic_write_json, iso, quantize_price, quantize_qty, read_json
from .models import MarketType, Side, SymbolFilters, ser

DEFAULT_MIN_NOTIONAL = {MarketType.SPOT: Decimal("5"), MarketType.USDM_PERP: Decimal("5")}


def _filters_by_type(sym: dict) -> dict[str, dict]:
    return {f.get("filterType", ""): f for f in sym.get("filters", []) or []}


#: Yeni giriş için kabul edilen USDⓈ-M sözleşme türleri. `TRADIFI_PERPETUAL` Binance'in hisse/emtia
#: perpetual'larıdır (NATGAS, MSFT, CL…): quote USDT, çarpan 1, PRICE_FILTER/LOT_SIZE aynı şemada.
#: Vadeli (CURRENT_QUARTER/NEXT_QUARTER) sözleşmeler perpetual DEĞİLDİR ve buradan geçmez.
USDM_ENTRY_CONTRACT_TYPES = ("PERPETUAL", "TRADIFI_PERPETUAL")


def _parse(sym: dict, market_type: MarketType, *, strict: bool = False) -> SymbolFilters:
    """exchangeInfo `symbols[i]` → SymbolFilters.

    `strict=True`: `PRICE_FILTER.tickSize` ve `LOT_SIZE.stepSize` YOKSA istisna fırlatır — eksik
    metadata sessizce 0.01/0.001 varsayılanına DÜŞMEZ (ölçüldü: varsayılan tick TRX'te plan
    geometrisini 2.00R→1.28R'ye düşürdü). `pricePrecision`/`quantityPrecision` KULLANILMAZ.
    """
    ft = _filters_by_type(sym)
    lot = ft.get("LOT_SIZE", {})
    mlot = ft.get("MARKET_LOT_SIZE", {})
    pf = ft.get("PRICE_FILTER", {})
    mn = ft.get("MIN_NOTIONAL") or ft.get("NOTIONAL") or {}
    if strict and (not pf.get("tickSize") or not lot.get("stepSize")):
        raise ValueError(f"{sym.get('symbol')}: PRICE_FILTER.tickSize / LOT_SIZE.stepSize eksik — kural DOĞRULANAMAZ")
    if strict and (D(pf["tickSize"]) <= 0 or D(lot["stepSize"]) <= 0):
        raise ValueError(f"{sym.get('symbol')}: tickSize/stepSize pozitif değil")
    min_notional = mn.get("minNotional") or mn.get("notional") or DEFAULT_MIN_NOTIONAL[market_type]
    return SymbolFilters(
        symbol=str(sym.get("symbol", "")), market_type=market_type,
        price_tick=D(pf.get("tickSize", "0.01")), qty_step=D(lot.get("stepSize", "0.001")),
        min_qty=D(lot.get("minQty", lot.get("stepSize", "0.001"))), max_qty=D(lot.get("maxQty", "1000000")),
        market_max_qty=D(mlot.get("maxQty", lot.get("maxQty", "1000000"))), min_notional=D(min_notional),
        max_leverage=int(sym.get("maxLeverage", 20 if market_type is MarketType.USDM_PERP else 1)),
        verified_at=iso(), source="binance_api",
        contract_type=str(sym.get("contractType") or ("SPOT" if market_type is MarketType.SPOT else "")))


def from_binance_spot(sym: dict, *, strict: bool = False) -> SymbolFilters:
    """`GET /api/v3/exchangeInfo` → symbols[i] sözlüğünden."""
    return _parse(sym, MarketType.SPOT, strict=strict)


def from_binance_futures(sym: dict, *, strict: bool = False) -> SymbolFilters:
    """`GET /fapi/v1/exchangeInfo` → symbols[i] sözlüğünden."""
    return _parse(sym, MarketType.USDM_PERP, strict=strict)


def refresh_futures_filters(cache: "FiltersCache", provider: Any, symbols: Iterable[str] | None = None,
                            *, save: bool = True) -> dict:
    """Resmi USDⓈ-M exchangeInfo'dan (ağırlık 1) filtreleri STRICT ayrıştırıp önbelleğe yazar.

    Sağlayıcı hatası → önbellek DOKUNULMAZ, `ok=False` + hata metni döner (sessiz varsayılan yok).
    Yalnız `status=TRADING`, quote USDT ve `USDM_ENTRY_CONTRACT_TYPES` sözleşmeleri kabul edilir;
    diğerleri `skipped` içinde gerekçesiyle listelenir. Bot sembolü `BASE/QUOTE` biçimine çevrilir.
    """
    want = {str(s).replace("/", "") for s in symbols} if symbols else None
    try:
        rows = provider.exchange_info() or []
    except Exception as exc:  # noqa: BLE001 — ağ/limit hatası önbelleği BOZMAZ
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "n_ok": 0, "skipped": {}, "errors": {}}
    ok_items: list[SymbolFilters] = []
    skipped: dict[str, str] = {}
    errors: dict[str, str] = {}
    for s in rows:
        raw = str(s.get("symbol") or "")
        if not raw or (want is not None and raw not in want):
            continue
        base, quote = str(s.get("baseAsset") or ""), str(s.get("quoteAsset") or "")
        bot_sym = f"{base}/{quote}" if base and quote else raw
        ct = str(s.get("contractType") or "")
        if quote != "USDT":
            skipped[bot_sym] = f"QUOTE_{quote}"
            continue
        if str(s.get("status")) != "TRADING":
            skipped[bot_sym] = f"STATUS_{s.get('status')}"
            continue
        if ct not in USDM_ENTRY_CONTRACT_TYPES:
            skipped[bot_sym] = f"CONTRACT_{ct or 'UNKNOWN'}"
            continue
        try:
            f = _parse(s, MarketType.USDM_PERP, strict=True)
        except (ValueError, ArithmeticError) as exc:
            errors[bot_sym] = str(exc)
            continue
        f.symbol = bot_sym
        ok_items.append(f)
    if ok_items:
        cache.put(ok_items)
        if save:
            cache.save()
    return {"ok": True, "n_ok": len(ok_items), "skipped": skipped, "errors": errors,
            "verified_at": cache.verified_at}


# sınıf metodları olarak da erişilebilsin
SymbolFilters.from_binance_spot = staticmethod(from_binance_spot)      # type: ignore[attr-defined]
SymbolFilters.from_binance_futures = staticmethod(from_binance_futures)  # type: ignore[attr-defined]


def from_universe_entry(symbol: str, entry: dict | None, market_type: MarketType = MarketType.USDM_PERP,
                        *, verified_at: str | None = None) -> SymbolFilters:
    """Keşif kaydındaki RESMÎ exchangeInfo filtrelerinden (`universe.discover` → `entry["filters"]`) SymbolFilters.

    Aynı `exchangeInfo` yanıtından gelen ham `raw` bloğu varsa STRICT ayrıştırılır (`_parse`), yoksa düzleştirilmiş
    alanlar kullanılır. `PRICE_FILTER.tickSize` / `LOT_SIZE.stepSize` yoksa ya da pozitif değilse `ValueError`:
    eksik metadata 0.01/0.001 VARSAYILANINA DÜŞMEZ (ölçüldü: varsayılan tick plan geometrisini bozuyor).
    """
    f = (entry or {}).get("filters") if isinstance(entry, dict) else None
    if not isinstance(f, dict):
        raise ValueError(f"{symbol}: keşif kaydında filtre yok — resmî emir kuralları DOĞRULANAMAZ")
    raw = f.get("raw")
    if isinstance(raw, dict) and raw.get("PRICE_FILTER") and raw.get("LOT_SIZE"):
        sym = {"symbol": symbol, "filters": [{"filterType": k, **v} for k, v in raw.items() if isinstance(v, dict)],
               "contractType": (entry or {}).get("contract_type") or ""}
        out = _parse(sym, market_type, strict=True)
    else:
        tick, step = f.get("tick_size"), f.get("step_size")
        if not tick or not step or D(tick) <= 0 or D(step) <= 0:
            raise ValueError(f"{symbol}: tickSize/stepSize eksik ya da pozitif değil — kural DOĞRULANAMAZ")
        out = SymbolFilters(symbol=symbol, market_type=market_type, price_tick=D(tick), qty_step=D(step),
                            min_qty=D(f.get("min_qty") or step), max_qty=D("1000000"), market_max_qty=D("1000000"),
                            min_notional=D(f.get("min_notional") or DEFAULT_MIN_NOTIONAL[market_type]),
                            max_leverage=20 if market_type is MarketType.USDM_PERP else 1,
                            contract_type=str((entry or {}).get("contract_type") or ""))
    out.symbol = symbol
    out.source = "binance_api:universe"
    out.verified_at = str(verified_at or (entry or {}).get("last_seen_at") or iso())
    return out


def default_filters(symbol: str, market_type: MarketType = MarketType.USDM_PERP) -> SymbolFilters:
    return SymbolFilters(symbol=symbol, market_type=market_type, min_notional=DEFAULT_MIN_NOTIONAL[market_type],
                         max_leverage=20 if market_type is MarketType.USDM_PERP else 1, source="default")


class FiltersCache:
    """`{ "verified_at": ..., "spot": {sym: filters}, "futures": {sym: filters} }` JSON önbelleği."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.verified_at: str = ""
        self._data: dict[MarketType, dict[str, SymbolFilters]] = {MarketType.SPOT: {}, MarketType.USDM_PERP: {}}
        if self.path.exists():
            raw = read_json(self.path, default={})
            self.verified_at = raw.get("verified_at", "")
            for mt, key in ((MarketType.SPOT, "spot"), (MarketType.USDM_PERP, "futures")):
                for s, d in (raw.get(key) or {}).items():
                    self._data[mt][s] = SymbolFilters.from_dict(d)

    def get(self, symbol: str, market_type: MarketType = MarketType.USDM_PERP) -> SymbolFilters:
        return self._data[market_type].get(symbol) or default_filters(symbol, market_type)

    def age_seconds(self, now=None) -> float | None:
        """Son doğrulamadan bu yana geçen süre; hiç doğrulanmadıysa None."""
        if not self.verified_at:
            return None
        try:
            from ..core import from_iso, utc_now
            return max(0.0, ((now or utc_now()) - from_iso(self.verified_at)).total_seconds())
        except (ValueError, TypeError):
            return None

    def is_stale(self, max_age_seconds: float, now=None) -> bool:
        """Doğrulanmamış ya da `max_age_seconds`'tan eski önbellek bayattır."""
        age = self.age_seconds(now)
        return age is None or age > float(max_age_seconds)

    def has(self, symbol: str, market_type: MarketType) -> bool:
        return symbol in self._data[market_type]

    def put(self, filters: SymbolFilters | Iterable[SymbolFilters]) -> None:
        items = [filters] if isinstance(filters, SymbolFilters) else list(filters)
        for f in items:
            self._data[f.market_type][f.symbol] = f
        self.verified_at = iso()

    def save(self) -> None:
        atomic_write_json(self.path, {"verified_at": self.verified_at,
                                      "spot": ser(self._data[MarketType.SPOT]),
                                      "futures": ser(self._data[MarketType.USDM_PERP])}, indent=1)


def quantize_order(filters: SymbolFilters, qty, price, side: Side | str = Side.BUY, *, market: bool = True
                   ) -> tuple[Decimal, Decimal, bool, str]:
    """Miktarı adıma AŞAĞI, fiyatı tick'e (pasif yön) yuvarla; min/max/min_notional kontrol et.
    Dönen: (qty, price, ok, reason)."""
    side_s = side.value if isinstance(side, Side) else str(side)
    q = quantize_qty(D(qty), filters.qty_step)
    p = quantize_price(D(price), filters.price_tick, side_s) if price is not None else ZERO
    if q <= 0:
        return q, p, False, "ZERO_QTY"
    if q < filters.min_qty:
        return q, p, False, "MIN_QTY"
    cap = filters.market_max_qty if market else filters.max_qty
    if q > cap:
        return q, p, False, "MAX_QTY"
    if p > 0 and q * p < filters.min_notional:
        return q, p, False, "MIN_NOTIONAL"
    return q, p, True, "OK"


# ----------------------------------------------------------------------------- leverage brackets
@dataclass
class LeverageBracket:
    notional_floor: Decimal
    notional_cap: Decimal
    mmr: Decimal            # bakım marjı oranı (0.004 = %0.4)
    maint_amount: Decimal   # kümülatif bakım tutarı (USDT)
    max_leverage: int

    def to_dict(self) -> dict:
        return ser(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LeverageBracket":
        return cls(D(d["notional_floor"]), D(d["notional_cap"]), D(d["mmr"]), D(d["maint_amount"]), int(d["max_leverage"]))


_DEFAULT_TIERS = (  # (cap, mmr, max_lev) — Binance USDⓈ-M büyük coin (ETH benzeri) tipik kademeleri
    ("50000", "0.004", 125), ("600000", "0.005", 100), ("3000000", "0.01", 50), ("12000000", "0.025", 20),
    ("50000000", "0.05", 10), ("65000000", "0.10", 5), ("150000000", "0.125", 4), ("320000000", "0.15", 3),
    ("Infinity", "0.25", 2),
)


def default_brackets() -> list[LeverageBracket]:
    """Binance benzeri kademeler; maint_amount kümülatif olarak türetilir (50k×(0.5%−0.4%)=50 …)."""
    out: list[LeverageBracket] = []
    floor, prev_mmr, maint = ZERO, ZERO, ZERO
    for cap, mmr, lev in _DEFAULT_TIERS:
        cap_d, mmr_d = D(cap), D(mmr)
        if out:
            maint = maint + (mmr_d - prev_mmr) * floor
        out.append(LeverageBracket(floor, cap_d, mmr_d, maint, lev))
        floor, prev_mmr = cap_d, mmr_d
    return out


def bracket_for(notional, brackets: list[LeverageBracket] | None = None) -> LeverageBracket:
    n = D(notional)
    bl = brackets or default_brackets()
    for b in bl:
        if n <= b.notional_cap:
            return b
    return bl[-1]


__all__ = ["from_binance_spot", "from_binance_futures", "from_universe_entry", "refresh_futures_filters", "USDM_ENTRY_CONTRACT_TYPES",
           "default_filters", "FiltersCache", "quantize_order",
           "LeverageBracket", "default_brackets", "bracket_for", "DEFAULT_MIN_NOTIONAL"]
