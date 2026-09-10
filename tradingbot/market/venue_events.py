"""VENUE OLAYLARI — borsanin KENDI ucundan dogrulanabilir sozlesme degisiklikleri.

Neden bu kaynak: bir vadeli bot icin en cok para kaybettiren "haber" bir basin bulteni degil,
sozlesmenin kendisinin degismesidir — sembol `TRADING` olmaktan cikar, min-notional yukselir,
adim buyur, funding araligi ya da tavani degisir. Bunlarin tamami `fapi/v1/exchangeInfo` ve
`fapi/v1/fundingInfo` uzerinden ANAHTARSIZ ve KESIN olarak okunur; ikincil bir aktarima
guvenmek gerekmez. Bu yuzden uretilen kayitlar `CONFIRMED`tir.

Yontem: her turda ilgili alanlarin bir goruntusu alinir ve ONCEKI goruntuyle karsilastirilir.
Fark varsa olay uretilir. Ilk calistirmada taban goruntu yazilir ve olay URETILMEZ — "ilk kez
gordum" bir degisiklik degildir.

SINIR (durustluk): `observed_at` bizim GORDUGUMUZ andir, borsanin degisikligi YAPTIGI an degil.
Venue bu iki zamani ayirt edecek bir alan yayimlamadigi icin `published_at` bos birakilir ve
gecikme "olculemedi" olarak raporlanir. Bos alani `observed_at` ile doldurmak, tur araligi
kadar (dakikalar) yanlis bir "aninda ogrendik" iddiasi uretirdi.
"""
from __future__ import annotations

from typing import Any, Iterable

from .news import CONFIRMED, VENUE, NewsItem

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUNDING_INFO_URL = "https://fapi.binance.com/fapi/v1/fundingInfo"

#: Izlenen alanlar. Liste bilincli olarak KISA: her biri emir gecerliligini ya da maliyeti
#: dogrudan degistirir. Kozmetik alanlar (ornegin `pair`) gurultu uretirdi.
TRACKED = ("status", "contractType", "quoteAsset", "tickSize", "stepSize", "minQty",
           "minNotional", "marketStepSize", "marketMaxQty")

_HUMAN = {
    "status": "işlem durumu", "contractType": "sözleşme tipi", "quoteAsset": "kotasyon varlığı",
    "tickSize": "fiyat adımı", "stepSize": "miktar adımı", "minQty": "asgari miktar",
    "minNotional": "asgari emir tutarı", "marketStepSize": "piyasa emri miktar adımı",
    "marketMaxQty": "piyasa emri azami miktarı", "fundingIntervalHours": "funding aralığı (saat)",
    "adjustedFundingRateCap": "funding tavanı", "adjustedFundingRateFloor": "funding tabanı",
}


def _bot_symbol(row: dict) -> str:
    base, quote = str(row.get("baseAsset") or ""), str(row.get("quoteAsset") or "")
    return f"{base}/{quote}" if base and quote else str(row.get("symbol") or "")


def snapshot_contracts(rows: Iterable[dict], symbols: Iterable[str] | None = None) -> dict[str, dict]:
    """`exchangeInfo` satirlarindan izlenen alanlarin goruntusu: `{bot_symbol: {alan: deger}}`."""
    want = {str(s).replace("/", "") for s in symbols} if symbols is not None else None
    out: dict[str, dict] = {}
    for r in rows or ():
        raw = str(r.get("symbol") or "")
        if not raw or (want is not None and raw not in want):
            continue
        f = {str(x.get("filterType")): x for x in (r.get("filters") or []) if isinstance(x, dict)}
        out[_bot_symbol(r)] = {
            "status": str(r.get("status") or ""),
            "contractType": str(r.get("contractType") or ""),
            "quoteAsset": str(r.get("quoteAsset") or ""),
            "tickSize": str(f.get("PRICE_FILTER", {}).get("tickSize") or ""),
            "stepSize": str(f.get("LOT_SIZE", {}).get("stepSize") or ""),
            "minQty": str(f.get("LOT_SIZE", {}).get("minQty") or ""),
            "minNotional": str(f.get("MIN_NOTIONAL", {}).get("notional") or ""),
            "marketStepSize": str(f.get("MARKET_LOT_SIZE", {}).get("stepSize") or ""),
            "marketMaxQty": str(f.get("MARKET_LOT_SIZE", {}).get("maxQty") or ""),
        }
    return out


def snapshot_funding(rows: Iterable[dict], symbols: Iterable[str] | None = None) -> dict[str, dict]:
    """`fundingInfo` satirlarindan funding araligi/tavan/taban goruntusu.

    Bu uc nokta yalnizca VARSAYILANDAN sapan sembolleri yayimlar; listede olmayan sembol
    "veri yok" degil "varsayilan" demektir ve bu ayrim `_default` bayragiyla korunur.
    """
    want = {str(s).replace("/", "") for s in symbols} if symbols is not None else None
    out: dict[str, dict] = {}
    for r in rows or ():
        raw = str(r.get("symbol") or "")
        if not raw or (want is not None and raw not in want):
            continue
        out[raw] = {"fundingIntervalHours": str(r.get("fundingIntervalHours") or ""),
                    "adjustedFundingRateCap": str(r.get("adjustedFundingRateCap") or ""),
                    "adjustedFundingRateFloor": str(r.get("adjustedFundingRateFloor") or ""),
                    "_default": False}
    return out


def diff_snapshots(prev: dict[str, dict], cur: dict[str, dict], *, url: str, observed_at: str,
                   category: str = VENUE) -> list[NewsItem]:
    """Iki goruntu arasindaki farklari `NewsItem` olarak uret.

    * `prev` BOSSA olay uretilmez — ilk goruntu tabandir, degisiklik degildir.
    * Yeni gorunen sembol `LISTED`, kaybolan sembol `NOT_LISTED` olayidir.
    * Alan degisikliginde eski ve yeni deger kayda GIRER; "degisti" demek yetmez.
    """
    if not prev:
        return []
    items: list[NewsItem] = []
    for sym in sorted(set(prev) | set(cur)):
        before, after = prev.get(sym), cur.get(sym)
        if before is None:
            items.append(NewsItem(source="binance_usdm", category=category, status=CONFIRMED,
                                  url=url, symbols=[sym], ingested_at=observed_at,
                                  title=f"{sym}: sözleşme listeye eklendi",
                                  detail={"change": "LISTED", "after": after}))
            continue
        if after is None:
            items.append(NewsItem(source="binance_usdm", category=category, status=CONFIRMED,
                                  url=url, symbols=[sym], ingested_at=observed_at,
                                  title=f"{sym}: sözleşme listede GÖRÜNMÜYOR",
                                  detail={"change": "NOT_LISTED", "before": before}))
            continue
        for k in sorted(set(before) | set(after)):
            if k.startswith("_"):
                continue
            b, a = before.get(k), after.get(k)
            if b == a:
                continue
            items.append(NewsItem(source="binance_usdm", category=category, status=CONFIRMED,
                                  url=url, symbols=[sym], ingested_at=observed_at,
                                  title=f"{sym}: {_HUMAN.get(k, k)} {b!r} → {a!r}",
                                  detail={"change": "FIELD", "field": k, "before": b, "after": a}))
    return items


def collect(provider: Any, *, symbols: Iterable[str], prev: dict, observed_at: str) -> tuple[list[NewsItem], dict, dict]:
    """Bir turluk toplama. Doner: `(olaylar, yeni_goruntu, durum)`.

    Saglayici hatasi ONCEKI GORUNTUYU BOZMAZ ve olay URETMEZ: erisilemeyen bir uc nokta
    "her sey degisti" anlamina gelmez. Durum sozlugu bunu `ok=False` ile bildirir.
    """
    state = {"ok": True, "errors": {}}
    cur = dict(prev or {})
    items: list[NewsItem] = []
    try:
        rows = provider.exchange_info() or []
        c_now = snapshot_contracts(rows, symbols)
        items += diff_snapshots((prev or {}).get("contracts") or {}, c_now,
                                url=EXCHANGE_INFO_URL, observed_at=observed_at)
        cur["contracts"] = c_now
    except Exception as exc:  # noqa: BLE001 — ag/limit hatasi goruntuyu KORUR
        state["ok"] = False
        state["errors"]["exchange_info"] = f"{type(exc).__name__}: {exc}"
    fn = getattr(provider, "funding_info", None)
    if callable(fn):
        try:
            f_now = snapshot_funding(fn() or [], symbols)
            items += diff_snapshots((prev or {}).get("funding") or {}, f_now,
                                    url=FUNDING_INFO_URL, observed_at=observed_at)
            cur["funding"] = f_now
        except Exception as exc:  # noqa: BLE001
            state["ok"] = False
            state["errors"]["funding_info"] = f"{type(exc).__name__}: {exc}"
    state["events"] = len(items)
    state["baseline_written"] = not bool(prev)
    return items, cur, state


__all__ = ["EXCHANGE_INFO_URL", "FUNDING_INFO_URL", "TRACKED", "collect", "diff_snapshots",
           "snapshot_contracts", "snapshot_funding"]
