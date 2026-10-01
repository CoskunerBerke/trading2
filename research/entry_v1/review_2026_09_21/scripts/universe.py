"""Arastirma evreni — TEK KAYNAK: canli `config.yaml` -> `entry_universe.symbols`.

NEDEN BU MODUL VAR
------------------
Alti arastirma betigi (`run_rule`, `box_sweep`, `run_replay`, `benchmarks`,
`benchmarks_v7`, `benchmarks_v8`) evreni ayri ayri, ELLE, on coine sabitlemisti.
Canli olcum 2026-09-17'de kirk coine cikinca backtest ile canli KIYASLANAMAZ hale
geldi ve bu, iki betik guncellenip dorduu unutuldugunda SESSIZCE yanlis sonuc
uretir. Liste artik tek yerden TURETILIR; evren degisince kod degismez.

IKI TASARIM KARARI — ikisi de bu projede pahaliya ogrenildi:

1) ARSIV KOKU IMPORT ANINDA DONDURULMAZ.
   `run_rule` bir zamanlar `TRADINGBOT_CACHE_DIR`i modul basinda sabitliyordu; V14'un
   ilk kosusu yanlis arsivi okudu ve dort coin HIC degerlendirilmedi, kosu yine
   "hatasiz" bitti. Bu yuzden burada modul duzeyinde `SYMS` YOKTUR — her cagri
   arsivi o anda okur. Betikler `resolve()` cagirir, sabit import etmez.

2) SESSIZ KIRPMA YOK.
   Arsivde olmayan sembol sessizce dusurulmez: `resolve()` dusurulenleri hem
   dondurdugu metaya yazar hem de stderr'e basar. `strict=True` ile hata verir.
   "Kirk coinde olctuk" cumlesi, ancak kirkinin da arsivde oldugu kosuda kurulabilir.

KULLANIM
--------
    from universe import resolve
    syms, meta = resolve()                  # config evreni ∩ arsiv, dusurulenler bildirilir
    syms, meta = resolve(strict=True)       # eksik varsa RuntimeError
    syms, meta = resolve(order="reverse")   # sira duyarliligi olcumu icin

SIRA NEDEN ONEMLI
-----------------
Canli defter aday sinyalleri SIRALAMIYOR: `strategy_paper.py:564` tur listesini
gelis sirasinda gezer ve uc risk slotu ilk uyan sembollere gider
(`max_total_open_risk_pct=6` / `risk_per_trade_pct=2` = 3 eszamanli pozisyon).
Olculdu (2026-09-19, VPS): T2 slotlari SOL (evren sirasi 4) ve BNB (9); M2
SOL (4), ONE (8), BNB (9) aldi — hepsi listenin basindan. Yani evreni kirka
cikarmak "daha cok islem" DEGIL, "hangi ucu" sorusunu liste sirasina birakmak
demektir. `order=` bu duyarliligi OLCMEK icindir; kapatmak icin degil.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WT_DEFAULT = Path(r"C:/Users/berke/wt-entry")
DATA_DEFAULT = Path(r"C:/Users/berke/wt-ten/data")


def config_path(explicit: str | Path | None = None) -> Path:
    """Canli config'in yolu — acik arguman > TRADINGBOT_CONFIG > wt-entry/config.yaml."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("TRADINGBOT_CONFIG")
    return Path(env) if env else WT_DEFAULT / "config.yaml"


def history_root(explicit: str | Path | None = None) -> Path:
    """Arsiv koku — CAGRI ANINDA cozulur, import aninda DEGIL (V14 dersi).

    Oncelik: acik arguman > TRADINGBOT_HISTORY_ROOT > TRADINGBOT_CACHE_DIR/history
    > wt-ten/data/history.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("TRADINGBOT_HISTORY_ROOT")
    if env:
        return Path(env)
    cache = os.environ.get("TRADINGBOT_CACHE_DIR")
    base = Path(cache) if cache else DATA_DEFAULT
    return base / "history"


def config_universe(explicit: str | Path | None = None) -> list[str]:
    """`entry_universe.symbols` — SIRASI KORUNUR (sira deneysel bir degiskendir).

    `entry_universe` kapaliysa ya da bossa `coins`a duser; ikisi de yoksa hata verir:
    sessizce bos evrenle kosmak, "sifir aday" sonucunu veri gibi gosterirdi.
    """
    import yaml

    p = config_path(explicit)
    if not p.exists():
        raise RuntimeError("config bulunamadi: %s" % p)
    with open(p, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    eu = cfg.get("entry_universe") or {}
    syms = list(eu.get("symbols") or []) if eu.get("enabled", True) else []
    if not syms:
        syms = list(cfg.get("coins") or [])
    if not syms:
        raise RuntimeError("config'te evren YOK (entry_universe.symbols ve coins bos): %s" % p)
    return syms


def _dirname(sym: str) -> str:
    """'SOL/USDT' -> 'SOL_USDT' (arsiv dizin adi)."""
    return sym.replace("/", "_")


def archived(root: str | Path | None = None, *, market: str = "futures",
             timeframes: tuple[str, ...] = ("1d",)) -> set[str]:
    """Arsivde GERCEKTEN verisi olan semboller.

    Bir sembol, istenen her zaman diliminin dizini var VE bos degilse sayilir.
    Dizinin var olmasi yetmez — bos dizin "veri var" demek degildir.
    """
    base = history_root(root) / market
    if not base.exists():
        return set()
    out: set[str] = set()
    for d in base.iterdir():
        if not d.is_dir():
            continue
        if all((d / tf).is_dir() and any((d / tf).iterdir()) for tf in timeframes):
            out.add(d.name)
    return out


def resolve(*, strict: bool = False, order: str = "config",
            config: str | Path | None = None, root: str | Path | None = None,
            market: str = "futures", timeframes: tuple[str, ...] = ("1d",),
            quiet: bool = False) -> tuple[list[str], dict]:
    """Kullanilacak sembol listesi + provenans metasi.

    `strict=True`: config evreninin tamami arsivde degilse RuntimeError.
    `order`: "config" (canliyla ayni sira) | "reverse" | "alpha" — sira duyarliligi olcumu.
    Doner: (semboller, meta). Meta rapora YAZILMALIDIR: pencere basina coin sayisi
    bildirilmeden "kirk coinde olctuk" denemez.
    """
    want = config_universe(config)
    have = archived(root, market=market, timeframes=timeframes)
    present = [s for s in want if _dirname(s) in have]
    missing = [s for s in want if _dirname(s) not in have]

    if missing and strict:
        raise RuntimeError(
            "arsivde %d/%d sembol YOK — 'kirk coinde olctuk' denemez. Eksik: %s"
            % (len(missing), len(want), ", ".join(missing))
        )
    if missing and not quiet:
        print(
            "[universe] UYARI: config evreni %d sembol, arsivde %d var, %d DUSURULDU.\n"
            "[universe] Bu kosu kirk coinlik canli evreni TEMSIL ETMEZ. Eksik: %s"
            % (len(want), len(present), len(missing), ", ".join(missing)),
            file=sys.stderr,
        )

    if order == "reverse":
        present = list(reversed(present))
    elif order == "alpha":
        present = sorted(present)
    elif order != "config":
        raise ValueError("bilinmeyen order: %r (config|reverse|alpha)" % order)

    meta = {
        "config_path": str(config_path(config)),
        "history_root": str(history_root(root)),
        "market": market,
        "timeframes": list(timeframes),
        "universe_configured": len(want),
        "universe_used": len(present),
        "missing_count": len(missing),
        "missing": missing,
        "order": order,
        "symbols": list(present),
        "represents_live_universe": not missing,
    }
    return present, meta
