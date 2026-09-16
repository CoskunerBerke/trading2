# -*- coding: utf-8 -*-
"""EVREN KEŞFİ — resmi USDⓈ-M exchangeInfo'dan etkin USDT perpetual sözleşmeler; isim listesi DEĞİL.

Sözleşme:
* Yalnız `contractType == PERPETUAL`, `quoteAsset == USDT`, `status == TRADING`. Spot, COIN-M, tarihli kontrat ve
  kripto dışı ürün bu deftere GİRMEZ. Sembol adından piyasa/ürün türü TAHMİN EDİLMEZ (`onboardDate`/`contractType` resmi
  metadata alanlarıdır).
* Yaş = futures sözleşmesinin ilk işlem zamanı (`onboardDate`, ms) — "tokenın doğumu" ya da "spot listeleme" DEĞİL;
  o alanlar bilinmiyorsa `null` kalır (uydurulmaz). `onboardDate` yoksa yaş `None`, kohort `UNKNOWN`, öncelik YOK.
* Kohortlar (tam saat, örtüşmez): 0–24h (0 < yaş <= 24), 1–7d (24 < yaş <= 168), 7–30d (168 < yaş <= 720),
  30–90d (720 < yaş <= 2160), 90d+ (yaş > 2160). Öncelikli tarama grubu: yaş <= 720 saat (30 gün) — ürün tercihi,
  kârlılık eşiği değil; bekleme süresi de değil (30 gün dolmasını beklemez).
* Uygunluk (işlem için): hacim >= eşik, spread <= eşik (bookTicker varsa; yoksa spread BİLİNMİYOR ve giriş anında yeniden
  ölçülür), filtre bilgisi var. Elenenler listede `reason` ile kalır; bir sembol TRADING dışına çıkarsa `delisted` olayı
  yazılır (mevcut pozisyonun yönetimi defterde sürer, yeni plan/giriş durur).
* Genel 60 günlük `min_listing_age_days` ya da `max_symbols=200` bu trader'a MİRAS KALMAZ: burada yaş filtresi yoktur ve
  sembol sayısı sınırsızdır (kapsam/gecikme `scheduler` durum dosyasında görünür).
"""
from __future__ import annotations

from typing import Any

from ..core import iso, utc_now

SCHEMA_VERSION = "pattern_universe_v1"
COHORTS: tuple[tuple[str, float, float | None], ...] = (("0-24h", 0.0, 24.0), ("1-7d", 24.0, 168.0), ("7-30d", 168.0, 720.0),
                                                        ("30-90d", 720.0, 2160.0), ("90d+", 2160.0, None))
COHORT_UNKNOWN = "UNKNOWN"
PRIORITY_MAX_AGE_H = 720.0          # yeni listeleme öncelik grubu: ilk 30 gün (ürün tercihi; kanıt eşiği değil)


def cohort_of(age_h: float | None) -> str:
    """Örtüşmeyen kohort: alt sınır hariç, üst sınır dahil; ilk kohortta 0 dahil. Bilinmeyen yaş → UNKNOWN."""
    if age_h is None:
        return COHORT_UNKNOWN
    a = float(age_h)
    if a < 0:
        return COHORT_UNKNOWN
    for name, lo, hi in COHORTS:
        if (a == 0 and lo == 0) or (a > lo and (hi is None or a <= hi)):
            return name
    return COHORT_UNKNOWN


def _f(x: Any, default: float | None = None) -> float | None:
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _spread(bid: Any, ask: Any) -> float | None:
    b, a = _f(bid), _f(ask)
    if b and a and b > 0 and a >= b:
        return (a - b) / ((a + b) / 2.0) * 100.0
    return None


def _filters(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in raw or []:
        if isinstance(f, dict) and f.get("filterType"):
            out[str(f["filterType"])] = {k: v for k, v in f.items() if k != "filterType"}
    pf, ls = out.get("PRICE_FILTER", {}), out.get("LOT_SIZE", {})
    mn = out.get("MIN_NOTIONAL", {}) or out.get("NOTIONAL", {})
    return {"tick_size": pf.get("tickSize"), "step_size": ls.get("stepSize"), "min_qty": ls.get("minQty"),
            "min_notional": mn.get("notional") or mn.get("minNotional"), "raw": out}


def discover(provider: Any, *, now_ms: int, min_quote_volume_24h: float, max_spread_pct: float,
             previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tek keşif turu (SAF; sağlayıcı duck-typed: `exchange_info()`, `ticker24h()`, isteğe bağlı `book_tickers()`).

    Döner: {schema_version, generated_at, as_of_ms, source, entries: {symbol: entry}, counts, changes, errors}.
    `previous` verilirse `first_seen_at` korunur ve yeni listeleme / delist / durum değişimi olayları üretilir."""
    errors: list[str] = []
    try:
        rows = list(provider.exchange_info() or [])
        src_ok = True
    except Exception as exc:  # noqa: BLE001 — metadata alınamazsa keşif başarısız: eski evren korunur (uydurma yok)
        return {"schema_version": SCHEMA_VERSION, "generated_at": iso(utc_now()), "as_of_ms": int(now_ms), "ok": False,
                "error": f"exchange_info: {type(exc).__name__}: {exc}"[:300], "entries": dict((previous or {}).get("entries") or {}),
                "counts": dict((previous or {}).get("counts") or {}), "changes": {"new_listings": [], "delisted": [], "status_changes": []},
                "source": {"metadata": getattr(provider, "name", type(provider).__name__)}}
    tick: dict[str, dict] = {}
    try:
        tick = {r.get("symbol"): r for r in (provider.ticker24h() or []) if isinstance(r, dict) and r.get("symbol")}
    except Exception as exc:  # noqa: BLE001 — hacim bilinmiyorsa LOW_VOLUME denmez: VOLUME_UNKNOWN (giriş anında yeniden ölçülür)
        errors.append(f"ticker24h: {type(exc).__name__}: {exc}"[:200])
    books: dict[str, dict] = {}
    if hasattr(provider, "book_tickers"):
        try:
            books = {r.get("symbol"): r for r in (provider.book_tickers() or []) if isinstance(r, dict) and r.get("symbol")}
        except Exception as exc:  # noqa: BLE001
            errors.append(f"book_tickers: {type(exc).__name__}: {exc}"[:200])
    prev_entries: dict[str, dict] = dict(((previous or {}).get("entries") or {}))
    entries: dict[str, dict[str, Any]] = {}
    now_iso = iso(utc_now())
    for s in rows:
        raw = str(s.get("symbol") or "")
        if not raw:
            continue
        base, quote = str(s.get("baseAsset") or ""), str(s.get("quoteAsset") or "")
        sym = f"{base}/{quote}" if base and quote else raw
        ct = str(s.get("contractType") or "")
        status = str(s.get("status") or "")
        onboard = s.get("onboardDate")
        try:
            onboard_ms = int(onboard) if onboard is not None else None
        except (TypeError, ValueError):
            onboard_ms = None
        age_h = None
        if onboard_ms is not None and onboard_ms > 0 and onboard_ms <= int(now_ms):
            age_h = (int(now_ms) - onboard_ms) / 3_600_000.0
        t = tick.get(raw) or {}
        b = books.get(raw) or {}
        vol = _f(t.get("quoteVolume")) if t else None
        spread = _spread(b.get("bidPrice") or t.get("bidPrice"), b.get("askPrice") or t.get("askPrice"))
        reason = ""
        if quote != "USDT":
            reason = f"QUOTE_{quote}"
        elif ct != "PERPETUAL":
            reason = f"CONTRACT_{ct or 'UNKNOWN'}"
        elif status != "TRADING":
            reason = f"STATUS_{status or 'UNKNOWN'}"
        elif vol is None:
            reason = "VOLUME_UNKNOWN"
        elif vol < float(min_quote_volume_24h):
            reason = "LOW_VOLUME"
        elif spread is not None and spread > float(max_spread_pct):
            reason = "WIDE_SPREAD"
        prev = prev_entries.get(sym) or {}
        entries[sym] = {"symbol": sym, "raw": raw, "base": base, "quote": quote, "market": "USDM_PERP", "contract_type": ct, "status": status,
                        "futures_first_trade_ms": onboard_ms, "futures_first_trade": iso_ms(onboard_ms),
                        "age_h": round(age_h, 3) if age_h is not None else None, "cohort": cohort_of(age_h),
                        "priority": bool(age_h is not None and age_h <= PRIORITY_MAX_AGE_H),
                        "token_birth_ms": None, "spot_listing_ms": None,        # bilinmiyor: uydurulmaz (ayrı alanlar)
                        "quote_volume_24h": vol, "last_price": _f(t.get("lastPrice")), "spread_pct": round(spread, 5) if spread is not None else None,
                        "spread_known": spread is not None, "filters": _filters(s.get("filters")),
                        "eligible": reason == "", "reason": reason, "first_seen_at": prev.get("first_seen_at") or now_iso, "last_seen_at": now_iso}
    # değişimler: yeni listeleme (önceki evrende yok), delist/durum (önceki TRADING → şimdi değil ya da listede yok)
    changes: dict[str, list] = {"new_listings": [], "delisted": [], "status_changes": []}
    if previous is not None:
        for sym, e in entries.items():
            p = prev_entries.get(sym)
            if p is None:
                changes["new_listings"].append({"symbol": sym, "at": now_iso, "futures_first_trade": e["futures_first_trade"], "age_h": e["age_h"], "cohort": e["cohort"]})
            elif str(p.get("status")) != e["status"]:
                changes["status_changes"].append({"symbol": sym, "at": now_iso, "from": p.get("status"), "to": e["status"]})
        for sym, p in prev_entries.items():
            if sym not in entries and str(p.get("status")) == "TRADING":
                changes["delisted"].append({"symbol": sym, "at": now_iso, "note": "resmi listede yok"})
    by_cohort: dict[str, int] = {}
    elig_by_cohort: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for e in entries.values():
        by_cohort[e["cohort"]] = by_cohort.get(e["cohort"], 0) + 1
        if e["eligible"]:
            elig_by_cohort[e["cohort"]] = elig_by_cohort.get(e["cohort"], 0) + 1
        else:
            reasons[e["reason"]] = reasons.get(e["reason"], 0) + 1
    counts = {"discovered": len(entries), "eligible": sum(1 for e in entries.values() if e["eligible"]),
              "priority": sum(1 for e in entries.values() if e["priority"] and e["eligible"]),
              "by_cohort": by_cohort, "eligible_by_cohort": elig_by_cohort, "excluded_by_reason": reasons,
              "onboard_unknown": sum(1 for e in entries.values() if e["futures_first_trade_ms"] is None)}
    return {"schema_version": SCHEMA_VERSION, "generated_at": now_iso, "as_of_ms": int(now_ms), "ok": src_ok, "errors": errors,
            "source": {"metadata": getattr(provider, "name", type(provider).__name__), "market": "USDM_PERP", "field_age": "onboardDate (futures ilk işlem)",
                       "note_tr": "Yaş = futures sözleşmesinin ilk işlem zamanı; token doğumu/spot listeleme ayrı alanlardır ve bilinmiyorsa null."},
            "policy": {"min_quote_volume_24h": float(min_quote_volume_24h), "max_spread_pct": float(max_spread_pct), "min_listing_age_days": 0,
                       "max_symbols": None, "priority_max_age_h": PRIORITY_MAX_AGE_H, "cohorts": [{"name": n, "gt_h": lo, "le_h": hi} for n, lo, hi in COHORTS]},
            "entries": entries, "counts": counts, "changes": changes}


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


__all__ = ["SCHEMA_VERSION", "COHORTS", "COHORT_UNKNOWN", "PRIORITY_MAX_AGE_H", "cohort_of", "discover", "iso_ms"]
