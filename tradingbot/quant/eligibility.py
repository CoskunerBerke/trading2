"""Point-in-time sembol uygunluğu (eligibility) artifact sözleşmesi (`quant_eligibility_v1`).

Neden: replay bugünkü exchange bilgisini geçmişin gerçeğiymiş gibi kullanırsa hayatta kalma
(survivorship) ve filtre yanlılığı üretir. Bu modül geçmiş evreni UYDURMAZ; bunun yerine açık bir
artifact sözleşmesi tanımlar ve **o tarihe ait snapshot yoksa bunu dürüstçe raporlar**.

Sözleşme:
* Her snapshot bir `as_of` anına aittir; `lookup` YALNIZ `as_of <= decision_ts` olan en yakın
  snapshot'ı döndürür (gelecekteki metadata karara sızmaz).
* `as_of` öncesi hiç snapshot yoksa sonuç `None` + `MISSING_ELIGIBILITY` bayrağıdır. Bugünkü
  bilgi geçmişe TAŞINMAZ.
* `strict=True` modda eksik snapshot fold'u/backtest'i geçersiz kılar (`valid=False`).
* Seans yönetimi METADATA iledir: kripto `always_open=True` (7/24), seanslı ürünler açık
  `sessions` penceresi taşır. Hiçbir sembol adı kodda özel-durum DEĞİLDİR.
* Artifact ileriye dönük arşivlenebilir: `collect_current_snapshot` bugünkü public metadata'yı
  `source_timestamp` ve `provenance` ile kaydeder; zamanla biriken dosyalar gerçek point-in-time
  geçmişi oluşturur.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core import atomic_write_json, payload_hash, read_json

SCHEMA_VERSION = "quant_eligibility_v1"

TRADING, HALTED, DELISTED, UNKNOWN = "TRADING", "HALTED", "DELISTED", "UNKNOWN"

#: Eksik/eski eligibility bilgisinin rapor bayrakları.
MISSING = "MISSING_ELIGIBILITY"
STALE = "STALE_ELIGIBILITY"
CURRENT_AS_HISTORICAL = "CURRENT_METADATA_NOT_HISTORICAL"


@dataclass
class TradingSession:
    """Seans tanımı. Kripto için `always_open=True`; seanslı ürünler `windows` taşır.

    `windows`: haftanın günü (0=Pazartesi) → [(başlangıç_dk, bitiş_dk)] UTC dakika cinsinden.
    """
    always_open: bool = True
    windows: dict[int, list[list[int]]] = field(default_factory=dict)
    timezone_note: str = "UTC"

    def is_open(self, weekday: int, minute_of_day: int) -> bool | None:
        if self.always_open:
            return True
        wins = self.windows.get(int(weekday))
        if wins is None:
            return False
        for w in wins:
            if len(w) == 2 and w[0] <= minute_of_day < w[1]:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"always_open": self.always_open,
                "windows": {str(k): v for k, v in sorted(self.windows.items())},
                "timezone_note": self.timezone_note}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "TradingSession":
        d = d or {}
        return cls(always_open=bool(d.get("always_open", True)),
                   windows={int(k): [list(x) for x in v] for k, v in (d.get("windows") or {}).items()},
                   timezone_note=str(d.get("timezone_note", "UTC")))


@dataclass
class SymbolEligibility:
    """Tek sembolün `as_of` anındaki uygunluk kaydı. Bilinmeyen alan `None` kalır (0 DEĞİL)."""
    symbol: str
    market_type: str
    as_of_ms: int
    trading_status: str = UNKNOWN
    listing_ms: int | None = None
    delisting_ms: int | None = None
    tick_size: float | None = None
    step_size: float | None = None
    min_qty: float | None = None
    min_notional: float | None = None
    session: TradingSession = field(default_factory=TradingSession)
    source: str = ""
    source_timestamp_ms: int | None = None
    provenance: str = "UNSPECIFIED"

    def availability(self) -> dict[str, bool]:
        return {"tick_size": self.tick_size is not None, "step_size": self.step_size is not None,
                "min_qty": self.min_qty is not None, "min_notional": self.min_notional is not None,
                "listing": self.listing_ms is not None, "delisting": self.delisting_ms is not None,
                "trading_status": self.trading_status != UNKNOWN,
                "session": True}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["session"] = self.session.to_dict()
        d["availability"] = self.availability()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SymbolEligibility":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "session"}
        known["session"] = TradingSession.from_dict(d.get("session"))
        return cls(**known)

    def tradeable_at(self, ts_ms: int) -> tuple[bool, list[str]]:
        """`ts_ms` anında işlem yapılabilir mi + engel kodları (metadata tabanlı, hard-code yok)."""
        reasons: list[str] = []
        if self.trading_status == DELISTED:
            reasons.append("DELISTED")
        elif self.trading_status == HALTED:
            reasons.append("HALTED")
        elif self.trading_status == UNKNOWN:
            reasons.append("STATUS_UNKNOWN")
        if self.listing_ms is not None and ts_ms < self.listing_ms:
            reasons.append("NOT_YET_LISTED")
        if self.delisting_ms is not None and ts_ms >= self.delisting_ms:
            reasons.append("AFTER_DELISTING")
        if not self.session.always_open:
            # UTC epoch → hafta günü / gün-içi dakika (1970-01-01 Perşembe = 3)
            days = ts_ms // 86_400_000
            weekday = int((days + 3) % 7)
            minute = int((ts_ms % 86_400_000) // 60_000)
            if not self.session.is_open(weekday, minute):
                reasons.append("SESSION_CLOSED")
        return (not reasons), reasons


class EligibilityStore:
    """`as_of` sıralı snapshot koleksiyonu. Lookup asla GELECEK metadata döndürmez."""

    def __init__(self, snapshots: Iterable[SymbolEligibility] | None = None):
        self._by_symbol: dict[tuple[str, str], list[SymbolEligibility]] = {}
        for s in snapshots or []:
            self.add(s)

    def add(self, snap: SymbolEligibility) -> None:
        key = (snap.symbol, snap.market_type)
        lst = self._by_symbol.setdefault(key, [])
        lst.append(snap)
        lst.sort(key=lambda s: s.as_of_ms)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_symbol.values())

    @property
    def symbols(self) -> list[str]:
        return sorted({k[0] for k in self._by_symbol})

    def lookup(self, symbol: str, market_type: str, ts_ms: int) -> SymbolEligibility | None:
        """`ts_ms` anında GEÇERLİ olan (as_of <= ts_ms) en yeni snapshot; yoksa None."""
        lst = self._by_symbol.get((symbol, market_type)) or []
        found = None
        for s in lst:
            if s.as_of_ms <= ts_ms:
                found = s
            else:
                break
        return found

    def check(self, symbol: str, market_type: str, ts_ms: int, *, strict: bool = False,
              max_age_ms: int | None = None) -> dict[str, Any]:
        """Karar anı uygunluk kontrolü.

        Snapshot yoksa `eligible=None` (BİLİNMİYOR) + `MISSING_ELIGIBILITY`; `strict=True` ise
        `valid=False` ile fold/backtest geçerliliği düşer. Bugünkü metadata geçmişe taşınmaz.
        """
        snap = self.lookup(symbol, market_type, ts_ms)
        flags: list[str] = []
        if snap is None:
            flags += [MISSING, CURRENT_AS_HISTORICAL]
            return {"symbol": symbol, "market_type": market_type, "ts_ms": ts_ms,
                    "eligible": None, "reasons": ["NO_POINT_IN_TIME_SNAPSHOT"], "flags": flags,
                    "valid": not strict, "snapshot_as_of_ms": None, "provenance": None}
        age = ts_ms - snap.as_of_ms
        if max_age_ms is not None and age > max_age_ms:
            flags.append(STALE)
        ok, reasons = snap.tradeable_at(ts_ms)
        valid = True
        if strict and STALE in flags:
            valid = False
        return {"symbol": symbol, "market_type": market_type, "ts_ms": ts_ms,
                "eligible": ok, "reasons": reasons, "flags": flags, "valid": valid,
                "snapshot_as_of_ms": snap.as_of_ms, "snapshot_age_ms": age,
                "provenance": snap.provenance, "source": snap.source,
                "availability": snap.availability(),
                "filters": {"tick_size": snap.tick_size, "step_size": snap.step_size,
                            "min_qty": snap.min_qty, "min_notional": snap.min_notional}}


def coverage_report(store: EligibilityStore, symbols: Iterable[str], market_type: str,
                    decision_times_ms: Iterable[int], *, strict: bool = False) -> dict[str, Any]:
    """Replay penceresi boyunca eligibility kapsaması — eksikler DÜRÜSTÇE raporlanır.

    `status`: `OK` (tam kapsama) | `PARTIAL` (kısmi) | `UNAVAILABLE` (hiç snapshot yok).
    `backtest_valid`: strict modda eksik kapsama backtest geçerliliğini düşürür.
    """
    syms = sorted(set(symbols))
    times = sorted(set(int(t) for t in decision_times_ms))
    total = len(syms) * len(times)
    covered = 0
    missing_pairs = 0
    for s in syms:
        for t in times:
            if store.lookup(s, market_type, t) is not None:
                covered += 1
            else:
                missing_pairs += 1
    ratio = (covered / total) if total else None
    if total == 0 or covered == 0:
        status = "UNAVAILABLE"
    elif covered == total:
        status = "OK"
    else:
        status = "PARTIAL"
    return {"schema_version": SCHEMA_VERSION, "status": status,
            "n_symbols": len(syms), "n_decision_times": len(times),
            "n_checks": total, "n_covered": covered, "n_missing": missing_pairs,
            "coverage_ratio": round(ratio, 6) if ratio is not None else None,
            "strict": strict,
            "backtest_valid": (status == "OK") if strict else (status != "UNAVAILABLE"),
            "point_in_time": status == "OK",
            "note": ("tam point-in-time kapsama" if status == "OK" else
                     "eksik point-in-time snapshot — bugünkü metadata geçmiş gerçeği sayılmadı"),
            "flags": ([] if status == "OK" else [MISSING, CURRENT_AS_HISTORICAL])}


# ------------------------------------------------------------------ artifact I/O

def build_artifact(snapshots: Iterable[SymbolEligibility], *, as_of_ms: int,
                   source: str, source_timestamp_ms: int | None = None,
                   provenance: str = "public_exchange_info") -> dict[str, Any]:
    """Deterministik artifact (zaman damgası ARGÜMANDIR; aynı girdi aynı SHA'yı verir)."""
    body = {"schema_version": SCHEMA_VERSION, "as_of_ms": int(as_of_ms), "source": source,
            "source_timestamp_ms": source_timestamp_ms, "provenance": provenance,
            "symbols": [s.to_dict() for s in sorted(snapshots, key=lambda x: (x.symbol, x.market_type))]}
    body["artifact_sha"] = payload_hash(body)
    body["forward_archivable"] = True
    return body


def write_artifact(path: Path | str, artifact: dict[str, Any]) -> Path:
    atomic_write_json(path, artifact)
    return Path(path)


def load_artifact(path: Path | str) -> EligibilityStore:
    doc = read_json(path, default=None)
    if not isinstance(doc, dict):
        return EligibilityStore()
    return EligibilityStore(SymbolEligibility.from_dict(d) for d in doc.get("symbols", []))


def load_store(paths: Iterable[Path | str]) -> EligibilityStore:
    """Birden çok artifact'i (farklı `as_of`) tek store'da birleştirir — ileriye dönük arşiv."""
    store = EligibilityStore()
    for p in paths:
        for s in load_artifact(p)._by_symbol.values():
            for snap in s:
                store.add(snap)
    return store


def from_exchange_info(payload: dict[str, Any], *, as_of_ms: int, market_type: str = "USDM_PERP",
                       source: str = "binance_fapi_exchangeInfo",
                       source_timestamp_ms: int | None = None,
                       always_open: bool = True) -> list[SymbolEligibility]:
    """Binance benzeri `exchangeInfo` yükünü eligibility kayıtlarına çevirir.

    ÖNEMLİ: bu BUGÜNÜN metadata'sıdır. `as_of_ms` çağıran tarafından verilir ve kayıt yalnız o
    andan İTİBAREN geçerli sayılır; geriye dönük olarak uygulanmaz (`lookup` as_of <= ts kuralı).
    """
    out: list[SymbolEligibility] = []
    for s in payload.get("symbols", []) or []:
        filt = {f.get("filterType"): f for f in (s.get("filters") or []) if isinstance(f, dict)}

        def _num(d: dict | None, key: str) -> float | None:
            if not d:
                return None
            try:
                v = float(d.get(key))
            except (TypeError, ValueError):
                return None
            return v if math.isfinite(v) else None

        raw_status = str(s.get("status") or "").upper()
        status = TRADING if raw_status == "TRADING" else (
            DELISTED if raw_status in ("BREAK", "SETTLING", "CLOSE", "DELISTED") else
            (HALTED if raw_status in ("HALT", "PAUSED", "AUCTION_MATCH") else UNKNOWN))
        base, quote = s.get("baseAsset"), s.get("quoteAsset")
        symbol = f"{base}/{quote}" if base and quote else str(s.get("symbol") or "")
        onboard = s.get("onboardDate")
        delivery = s.get("deliveryDate")
        out.append(SymbolEligibility(
            symbol=symbol, market_type=market_type, as_of_ms=int(as_of_ms), trading_status=status,
            listing_ms=int(onboard) if isinstance(onboard, (int, float)) and onboard else None,
            delisting_ms=None,           # perpetual'da deliveryDate gerçek delisting DEĞİLDİR
            tick_size=_num(filt.get("PRICE_FILTER"), "tickSize"),
            step_size=_num(filt.get("LOT_SIZE"), "stepSize"),
            min_qty=_num(filt.get("LOT_SIZE"), "minQty"),
            min_notional=_num(filt.get("MIN_NOTIONAL"), "notional") or _num(filt.get("MIN_NOTIONAL"), "minNotional"),
            session=TradingSession(always_open=always_open),
            source=source, source_timestamp_ms=source_timestamp_ms,
            provenance="current_exchange_info_snapshot_valid_from_as_of"))
        if delivery and not always_open:      # yalnız seanslı/vadeli ürünlerde anlamlı
            out[-1].delisting_ms = int(delivery)
    return out
