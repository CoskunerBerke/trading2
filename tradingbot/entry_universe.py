"""SABIT GIRIS EVRENI — yeni futures girisinin TEK kanonik uyelik kurali.

Bu dosya iki soruyu birbirinden ayirir ve ikisini de tek yerde cevaplar:

1. **Hangi sembolleri analiz ediyoruz?** → `tour_symbols()`
2. **Hangi sembolde YENI pozisyon acabiliriz?** → `entry_block_reason()`

Ikisi ayni sey DEGILDIR. Acik bir pozisyonun sembolu evrenden cikarilsa bile o sembol
analiz kapsaminda KALIR (fiyat takibi, stop/TP yonetimi, kapanis surer); yalnizca YENI
giris kapanir. "Liste degisti" hicbir kosulda pozisyon kapatma gerekcesi degildir.

FAIL-CLOSED: `enabled=true` iken bos evren `ConfigError`'dur (bkz. `config_v3.validate_v3`);
bu modul de bos evreni "hersey serbest" saymaz, `UNIVERSE_EMPTY` ile reddeder.
"""
from __future__ import annotations

from collections.abc import Iterable

#: Kapinin sert kodu — `decision_gates.GATES` icinde HARD_SAFETY olarak kayitlidir.
GATE_CODE = "SYMBOL_NOT_IN_ENTRY_UNIVERSE"

#: Evrenin bagladigi piyasa. Talimat vadeli (USDⓈ-M perpetual) giris evrenini sabitler.
GATED_MARKETS = ("USDM_PERP",)


def normalize(symbol: str) -> str:
    """`BTCUSDT`, `btc/usdt`, `BTC-USDT` → `BTC/USDT`. Cozulemeyen giris oldugu gibi doner.

    Bilinen quote'lar sondan eslenir; `USDT` once denenir ki `BTCUSDT` → `BTC/USDT` olsun
    ve `USD` yanlis eslesmesin.
    """
    s = str(symbol or "").strip().upper().replace("-", "/").replace("_", "/")
    if not s:
        return ""
    if "/" in s:
        base, _, quote = s.partition("/")
        return f"{base.strip()}/{quote.strip()}" if base.strip() and quote.strip() else s
    for quote in ("USDT", "USDC", "FDUSD", "TUSD", "BUSD", "BTC", "ETH", "BNB", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[: -len(quote)]}/{quote}"
    return s


def normalize_all(symbols: Iterable[str]) -> list[str]:
    """Normalize + sirasi korunmus tekillestirme. Bos girisler dusurulur."""
    out: list[str] = []
    for s in symbols or ():
        n = normalize(s)
        if n and n not in out:
            out.append(n)
    return out


def tour_symbols(*, universe: Iterable[str], open_positions: Iterable[str],
                 extra: Iterable[str] = (), enabled: bool = True) -> list[str]:
    """Bir turda ANALIZ edilecek semboller: evren ∪ acik pozisyonlar ∪ `extra`.

    Acik pozisyonlar her zaman icerdedir — cikis yonetimi fiyat gerektirir ve evren
    degisikligi bir pozisyonu koru gerekcesi degildir. `enabled=false` iken evren
    baglamaz ve yalnizca birlesim doner (eski davranis cagiran tarafta korunur).
    """
    if not enabled:
        return normalize_all(list(universe) + list(extra) + list(open_positions))
    return normalize_all(list(universe) + list(open_positions) + list(extra))


def entry_block_reason(symbol: str, *, market: str, universe: Iterable[str], direction: str = "",
                       enabled: bool = True, allow_long: bool = True,
                       allow_short: bool = True) -> str | None:
    """YENI giris icin engel gerekcesi; engel yoksa `None`.

    `market` `GATED_MARKETS` disindaysa (ornegin `SPOT`) bu kapi baglamaz: talimat vadeli
    giris evrenini sabitler. Yon izinleri yalnizca baglanan piyasada degerlendirilir.
    """
    if not enabled:
        return None
    if str(market or "").upper() not in GATED_MARKETS:
        return None
    allowed = set(normalize_all(universe))
    if not allowed:
        return "UNIVERSE_EMPTY"
    if normalize(symbol) not in allowed:
        return "NOT_IN_UNIVERSE"
    d = str(direction or "").upper()
    if d == "LONG" and not allow_long:
        return "LONG_DISABLED"
    if d == "SHORT" and not allow_short:
        return "SHORT_DISABLED"
    return None


__all__ = ["GATE_CODE", "GATED_MARKETS", "normalize", "normalize_all", "tour_symbols",
           "entry_block_reason"]
